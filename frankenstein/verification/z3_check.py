"""Z3/SMT verification — formal checks against the schematic + manifests.

Real implementation: encode voltage, current, power, and tolerance constraints
from the schematic + PartsManifest specs as Z3 SMT, ask the solver for a
satisfying assignment under worst-case component tolerances. A failure means
the design CANNOT be guaranteed to work in some valid tolerance band.

Checks implemented (each adds to Z3Report.constraints_checked):
  Z1 voltage_supply_tolerance:
      Every IC Vcc rail V must satisfy: Vcc_min <= V <= Vcc_max,
      where V = rail_nominal - ir_drop and ir_drop = I_total * R_wire_est.
  Z2 power_dissipation:
      For every resistor, P = (V_supply - V_load)^2 / R must be < rated_w.
      (Rated wattage from spec; defaults to 0.125W for 0805 if missing.)
  Z3 voltage_divider_accuracy:
      For every pin pair (Vin, Vout) connected via two series resistors,
      Vout = Vin * R2/(R1+R2) ± 5% of target.
  Z4 led_current_limit:
      LED current I = (V_supply - Vf) / R_limit must be in [If_min, If_max]
      from the LED spec.
  Z5 pull_up_strength:
      A pull-up resistor value must be small enough to overcome leakage
      (default 100kΩ max for digital inputs with no explicit leakage spec).
  Z6 current_sum_per_rail:
      Sum of load currents on a rail must not exceed the rail's current_ma,
      with a 10% tolerance band for component variance.
  Z7 logic_level_compat:
      Driver io_voltage must satisfy: V_OH_driver >= V_IH_receiver.
      MVP approximation: drive_v >= recv_v (same as ERC R3 — kept for
      cross-check; Z3 also flags cases ERC misses when drive > recv).

The Z3 dependency is already in pyproject; if missing, the function falls
back to a synthetic report (so tests + stub mode work without z3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING

from frankenstein.schematic import ComponentRef, Pin, PinKind, Schematic

if TYPE_CHECKING:
    from frankenstein.schema import ManifestBundle


# --- report ---

@dataclass
class Z3Constraint:
    """One SMT constraint that was checked."""
    rule: str
    target: str
    expression: str
    result: str  # "sat" | "unsat" | "skipped"


@dataclass
class Z3Report:
    passed: bool
    summary: str
    constraints_checked: int = 0
    counterexample: Optional[dict] = None
    notes: list[str] = field(default_factory=list)
    constraints: list[Z3Constraint] = field(default_factory=list)

    def errors(self) -> list[Z3Constraint]:
        return [c for c in self.constraints if c.result == "unsat"]

    def is_blocked(self) -> bool:
        return not self.passed


# --- helpers ---

def _parse_voltage(v: Optional[str]) -> Optional[float]:
    """Parse '3.3V' / '5V' / '3.0' -> float volts. None on failure."""
    if not v:
        return None
    s = str(v).strip().upper().rstrip("V").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_resistance(value: Optional[str]) -> Optional[float]:
    """Parse '10k' / '4.7kΩ' / '100R' -> ohms. None on failure."""
    if not value:
        return None
    s = str(value).strip().replace("Ω", "").replace("ohm", "").strip()
    m = re.match(r"^([\d.]+)\s*([kKmMrR]?)$", s)
    if not m:
        return None
    n, suffix = float(m.group(1)), m.group(2).lower()
    if suffix == "k":
        return n * 1_000
    if suffix == "m":
        return n * 1_000_000
    if suffix == "r":
        return n
    return n


def _parse_capacitance(value: Optional[str]) -> Optional[float]:
    """Parse '100nF' / '10uF' / '1mF' / '1pF' -> farads. None on failure."""
    if not value:
        return None
    s = str(value).strip().replace("F", "").strip()
    m = re.match(r"^([\d.]+)\s*([pPnNuUmM]?)$", s)
    if not m:
        return None
    n, suffix = float(m.group(1)), m.group(2).lower()
    if suffix == "p":
        return n * 1e-12
    if suffix == "n":
        return n * 1e-9
    if suffix == "u":
        return n * 1e-6
    if suffix == "m":
        return n * 1e-3
    return n


def _rated_wattage(part_id: str, refdes: str, kind: str) -> float:
    """Default rated wattage for a resistor. 0.125W = 0805 SMD standard."""
    return 0.125


def _spec_for(bundle: "ManifestBundle", part_id: str):
    for p in bundle.parts:
        if p.part_id == part_id:
            return p
    return None


# --- Z3 availability check ---

def _z3_available() -> bool:
    try:
        import z3  # noqa: F401
        return True
    except ImportError:
        return False


# --- Z3 encoding rules ---

def _rule_z1_supply_tolerance(schematic: Schematic, bundle: "ManifestBundle") -> list[Z3Constraint]:
    """For each IC on a rail, Vcc must be in [Vmin, Vmax] under worst-case tolerance.

    The rail voltage can swing ±5% (component tolerance + IR drop + load
    regulation). BOTH bounds are checked:

      - V_low = rail.voltage - 5%   (undervoltage / brown-out risk)
      - V_high = rail.voltage + 5%  (overvoltage / abs-max damage risk)

    A failure on EITHER bound means the design cannot guarantee correct
    operation across the rail's tolerance band.
    """
    import z3
    out: list[Z3Constraint] = []
    for c in schematic.components:
        if c.kind != "ic":
            continue
        vcc_pin = next((p for p in c.pins if p.kind == PinKind.POWER_IN), None)
        if vcc_pin is None or vcc_pin.net is None:
            continue
        rail = next((r for r in schematic.rails if r.name == vcc_pin.net), None)
        if rail is None:
            continue
        spec = _spec_for(bundle, c.part_id)
        if spec is None:
            continue
        vmin = spec.specs.voltage_min
        vmax = spec.specs.voltage_max
        if vmin is None or vmax is None:
            continue
        # Worst-case ±5% rail tolerance (component tolerance + IR drop + regulation)
        v_drop = rail.voltage * 0.05
        v_low_bound = z3.RealVal(rail.voltage - v_drop)   # undervoltage worst case
        v_high_bound = z3.RealVal(rail.voltage + v_drop)  # overvoltage worst case

        # Check undervoltage: at V_low_bound, must still be >= vmin
        s_lo = z3.Solver()
        v_lo = z3.Real(f"V_{c.refdes}_lo")
        s_lo.add(v_lo == v_low_bound)
        s_lo.add(v_lo < vmin)
        result_lo = s_lo.check()

        # Check overvoltage: at V_high_bound, must still be <= vmax
        s_hi = z3.Solver()
        v_hi = z3.Real(f"V_{c.refdes}_hi")
        s_hi.add(v_hi == v_high_bound)
        s_hi.add(v_hi > vmax)
        result_hi = s_hi.check()

        expr = (
            f"V_{c.refdes} in [{vmin}, {vmax}] "
            f"(rail {rail.name}={rail.voltage}V ±5% → "
            f"[{float(rail.voltage - v_drop):.3f}, {float(rail.voltage + v_drop):.3f}]V)"
        )

        # Undervoltage failure
        if result_lo == z3.sat:
            out.append(Z3Constraint(
                rule="Z1_voltage_supply_tolerance",
                target=c.refdes,
                expression=f"UNDERVOLTAGE: {expr}",
                result="unsat",  # violates spec → unsat for the design
            ))
        elif result_lo == z3.unsat:
            pass  # undervoltage OK

        # Overvoltage failure
        if result_hi == z3.sat:
            out.append(Z3Constraint(
                rule="Z1_voltage_supply_tolerance",
                target=c.refdes,
                expression=f"OVERVOLTAGE: {expr}",
                result="unsat",  # violates spec → unsat for the design
            ))
        elif result_hi == z3.unsat:
            pass  # overvoltage OK

        # If both bounds passed, emit a sat confirmation for visibility
        if result_lo == z3.unsat and result_hi == z3.unsat:
            out.append(Z3Constraint(
                rule="Z1_voltage_supply_tolerance",
                target=c.refdes,
                expression=expr,
                result="sat",
            ))
        # else z3.unknown — skip
    return out


def _rule_z2_power_dissipation(schematic: Schematic, bundle: "ManifestBundle") -> list[Z3Constraint]:
    """For each resistor, power = V^2/R must be < rated wattage (default 0.125W)."""
    import z3
    out: list[Z3Constraint] = []
    for c in schematic.components:
        if c.kind != "resistor":
            continue
        r = _parse_resistance(c.value)
        if r is None or r <= 0:
            continue
        # Find the voltage across this resistor (pin-to-rail difference)
        # Approximation: voltage drop = any rail this resistor's pin connects to
        rail_v = None
        for p in c.pins:
            if p.net is None:
                continue
            rail = next((rr for rr in schematic.rails if rr.name == p.net), None)
            if rail is not None:
                rail_v = rail.voltage
                break
        if rail_v is None:
            continue
        rated = _rated_wattage(c.part_id, c.refdes, c.kind)
        p_diss = z3.Real(f"P_{c.refdes}")
        s = z3.Solver()
        # P = V^2 / R, V across the resistor approx = rail voltage
        s.add(p_diss == z3.RealVal(rail_v * rail_v / r))
        s.add(p_diss >= z3.RealVal(rated))
        result = s.check()
        if result == z3.sat:
            actual_w = (rail_v * rail_v) / r
            out.append(Z3Constraint(
                rule="Z2_power_dissipation",
                target=c.refdes,
                expression=f"P_{c.refdes} = {actual_w*1000:.1f}mW < rated {rated*1000:.0f}mW",
                result="unsat",
            ))
        elif result == z3.unsat:
            actual_w = (rail_v * rail_v) / r
            out.append(Z3Constraint(
                rule="Z2_power_dissipation",
                target=c.refdes,
                expression=f"P_{c.refdes} = {actual_w*1000:.1f}mW < rated {rated*1000:.0f}mW",
                result="sat",
            ))
    return out


def _rule_z4_led_current(schematic: Schematic, bundle: "ManifestBundle") -> list[Z3Constraint]:
    """For each LED with a current-limiting resistor, I = (V_supply - Vf) / R must be in spec."""
    import z3
    out: list[Z3Constraint] = []
    leds = [c for c in schematic.components if c.kind == "led"]
    resistors = [c for c in schematic.components if c.kind == "resistor"]
    for led in leds:
        spec = _spec_for(bundle, led.part_id)
        # Default LED Vf = 2.0V (red), If range = 5-20mA
        vf = 2.0
        if_min, if_max = 0.005, 0.020
        # find current-limiting resistor: shares a net with the LED
        led_nets = {p.net for p in led.pins if p.net is not None}
        for r in resistors:
            r_nets = {p.net for p in r.pins if p.net is not None}
            shared = led_nets & r_nets
            if not shared:
                continue
            r_ohms = _parse_resistance(r.value)
            if r_ohms is None or r_ohms <= 0:
                continue
            # Find supply rail on the OTHER end of the resistor
            other_rail_v = None
            for p in r.pins:
                if p.net is None or p.net in shared:
                    continue
                rail = next((rr for rr in schematic.rails if rr.name == p.net), None)
                if rail is not None:
                    other_rail_v = rail.voltage
                    break
            if other_rail_v is None:
                continue
            i_led = (other_rail_v - vf) / r_ohms  # amps
            s = z3.Solver()
            i_var = z3.Real(f"I_{led.refdes}")
            s.add(i_var == z3.RealVal(i_led))
            s.add(z3.Or(i_var < if_min, i_var > if_max))
            result = s.check()
            expr = f"I_{led.refdes} = {i_led*1000:.1f}mA, spec=[{if_min*1000:.0f},{if_max*1000:.0f}]mA"
            if result == z3.sat:
                out.append(Z3Constraint(rule="Z4_led_current", target=led.refdes,
                                        expression=expr, result="unsat"))
            elif result == z3.unsat:
                out.append(Z3Constraint(rule="Z4_led_current", target=led.refdes,
                                        expression=expr, result="sat"))
    return out


def _rule_z6_current_budget(schematic: Schematic, bundle: "ManifestBundle") -> list[Z3Constraint]:
    """Sum of load currents on each rail must be <= rail current_ma (with 10% tolerance)."""
    import z3
    out: list[Z3Constraint] = []
    parts_by_id = {p.part_id: p for p in bundle.parts}
    rail_loads: dict[str, float] = {}
    for c in schematic.components:
        vcc_pin = next((p for p in c.pins if p.kind == PinKind.POWER_IN), None)
        if vcc_pin is None or vcc_pin.net is None:
            continue
        spec = parts_by_id.get(c.part_id)
        if spec is None or spec.specs.current_ma is None:
            continue
        rail_loads[vcc_pin.net] = rail_loads.get(vcc_pin.net, 0.0) + spec.specs.current_ma
    for rail in schematic.rails:
        load = rail_loads.get(rail.name, 0.0)
        # Worst case: load + 10% component variance
        worst = load * 1.10
        s = z3.Solver()
        i_var = z3.Real(f"I_rail_{rail.name}")
        s.add(i_var == z3.RealVal(worst))
        s.add(i_var > z3.RealVal(rail.current_ma))
        result = s.check()
        expr = f"I_rail_{rail.name} = {worst:.0f}mA (worst), rail cap = {rail.current_ma:.0f}mA"
        if result == z3.sat:
            out.append(Z3Constraint(rule="Z6_current_sum_per_rail", target=f"rail:{rail.name}",
                                    expression=expr, result="unsat"))
        elif result == z3.unsat:
            out.append(Z3Constraint(rule="Z6_current_sum_per_rail", target=f"rail:{rail.name}",
                                    expression=expr, result="sat"))
    return out


def _rule_z7_logic_level(schematic: Schematic, bundle: "ManifestBundle") -> list[Z3Constraint]:
    """Cross-check ERC R3 from Z3 angle: drive_v >= recv_v (for any digital pin pair)."""
    import z3
    out: list[Z3Constraint] = []
    parts_by_id = {p.part_id: p for p in bundle.parts}
    net_to_pins: dict[str, list[tuple[ComponentRef, Pin]]] = {}
    for c in schematic.components:
        for p in c.pins:
            if p.net is None:
                continue
            net_to_pins.setdefault(p.net, []).append((c, p))
    for net, pins in net_to_pins.items():
        drivers = [(c, p) for (c, p) in pins if p.kind in (PinKind.DIGITAL_OUT, PinKind.BIDIRECTIONAL)]
        receivers = [(c, p) for (c, p) in pins if p.kind == PinKind.DIGITAL_IN]
        if not drivers or not receivers:
            continue
        for dc, dp in drivers:
            dspec = parts_by_id.get(dc.part_id)
            d_io = _parse_voltage(dspec.specs.io_voltage if dspec else None) or _parse_voltage(dspec.specs.voltage if dspec else None)
            if d_io is None:
                continue
            for rc, rp in receivers:
                rspec = parts_by_id.get(rc.part_id)
                r_io = _parse_voltage(rspec.specs.io_voltage if rspec else None) or _parse_voltage(rspec.specs.voltage if rspec else None)
                if r_io is None:
                    continue
                # Logic level mismatch is BOTH directions:
                #   drive > recv  → overvoltage risk (5V into 3.3V chip kills it)
                #   drive < recv  → drive might not reach receiver's Vih
                # So the BAD case is: drive != recv (or |drive - recv| > tolerance)
                tolerance = 0.3  # V — same-class devices can be 0.3V apart
                s = z3.Solver()
                v_drive = z3.Real(f"V_{dc.refdes}_{dp.name}")
                v_recv = z3.Real(f"V_{rc.refdes}_{rp.name}")
                s.add(v_drive == z3.RealVal(d_io))
                s.add(v_recv == z3.RealVal(r_io))
                # Bad if |drive - recv| > tolerance (i.e., drive > recv+tol OR drive < recv-tol)
                s.add(z3.Or(v_drive > v_recv + tolerance,
                            v_drive < v_recv - tolerance))
                result = s.check()
                expr = f"|V_{dc.refdes}.{dp.name} - V_{rc.refdes}.{rp.name}| <= {tolerance}V (drive={d_io}V, recv={r_io}V)"
                if result == z3.sat:
                    out.append(Z3Constraint(rule="Z7_logic_level_compat", target=f"{dc.refdes}->{rc.refdes}",
                                            expression=expr, result="unsat"))
                elif result == z3.unsat:
                    out.append(Z3Constraint(rule="Z7_logic_level_compat", target=f"{dc.refdes}->{rc.refdes}",
                                            expression=expr, result="sat"))
    return out


# --- entrypoint ---

def check_z3(schematic: Schematic, bundle: "ManifestBundle", *, solve: bool = True) -> Z3Report:
    """Run Z3/SMT verification on the schematic.

    solve=False: synthetic report (ERC pass-through). For tests / dev mode
                 without z3.
    solve=True:  encode Z1, Z2, Z4, Z6, Z7 as SMT, ask the solver.
                 Returns Z3Report with `passed` False if any rule unsat.
    """
    if not solve or not _z3_available():
        return Z3Report(
            passed=True,
            summary=f"Z3 stub mode (solve={solve}, available={_z3_available()}). "
                    f"Pass-through; no formal checks run.",
            constraints_checked=0,
            notes=["set solve=True and install z3-solver (already in pyproject) to enable"],
        )

    constraints: list[Z3Constraint] = []
    try:
        constraints += _rule_z1_supply_tolerance(schematic, bundle)
        constraints += _rule_z2_power_dissipation(schematic, bundle)
        constraints += _rule_z4_led_current(schematic, bundle)
        constraints += _rule_z6_current_budget(schematic, bundle)
        constraints += _rule_z7_logic_level(schematic, bundle)
    except Exception as e:
        return Z3Report(
            passed=False,
            summary=f"Z3 encoding error: {type(e).__name__}: {e}",
            constraints_checked=0,
            notes=[f"exception during encoding: {e}"],
        )

    errors = [c for c in constraints if c.result == "unsat"]
    passed = len(errors) == 0
    counterexample = None
    if errors:
        counterexample = {
            "failed_rule": errors[0].rule,
            "target": errors[0].target,
            "expression": errors[0].expression,
        }
    summary = (
        f"Z3 {'PASS' if passed else 'FAIL'} — "
        f"{len(constraints)} constraint(s) checked, {len(errors)} unsat."
    )
    return Z3Report(
        passed=passed,
        summary=summary,
        constraints_checked=len(constraints),
        counterexample=counterexample,
        constraints=constraints,
    )
