"""ERC — Electrical Rules Check.

Pure-Python rules against a Schematic + the originating parts manifests.
Returns an ERCReport with a list of violations + a pass/fail boolean.

Rules implemented (MVP):
  R1 voltage_range: every IC's Vcc pin connects to a rail within its
                   [voltage_min, voltage_max] spec.
  R2 no_shorted_rails: no two rails of different voltages share a net.
  R3 logic_level:     when a digital output drives a digital input on a
                     different component, the driver's io_voltage must be
                     >= the receiver's min high input voltage (proxy: same
                     voltage class for MVP).
  R4 current_budget:  sum of all part current_ma must not exceed the
                     weakest rail's current_ma.
  R5 pull_required:   every digital_in pin must be driven (have a net
                     connected to some output) OR have a pull resistor
                     in the BOM. Floating inputs are an ERC failure.
  R6 flyback_diode:   every relay/motor/solenoid must have a diode
                     antiparallel to its coil/terminals.
  R7 decoupling:      every IC should have at least one capacitor in the
                     BOM (real layout check is out of scope; this is the
                     BOM proxy).

All thresholds in volts are derived from the specs' voltage_min/voltage_max
where present, with sensible defaults for parts missing the spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, TYPE_CHECKING

from frankenstein.schematic import ComponentRef, Pin, PinKind, Schematic

if TYPE_CHECKING:
    from frankenstein.schema import ManifestBundle


@dataclass
class Violation:
    rule: str
    severity: str  # "error" or "warning"
    refdes: str
    message: str


@dataclass
class ERCReport:
    passed: bool
    violations: list[Violation]
    summary: str

    def errors(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "error"]

    def warnings(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "warning"]


# --- rule helpers ---

def _part_specs_lookup(bundle: "ManifestBundle") -> dict[str, "object"]:
    """Index PartsManifest by part_id for spec lookups during ERC."""
    return {p.part_id: p for p in bundle.parts}


def _drive_voltage(spec_voltage: Optional[str], spec_io: Optional[str]) -> Optional[float]:
    """Parse voltage strings like '3.3V' / '5V' into a float.

    Prefers io_voltage for signal pins, voltage for power.
    """
    for raw in (spec_io, spec_voltage):
        if not raw:
            continue
        s = raw.strip().upper().rstrip("V").strip()
        try:
            return float(s)
        except ValueError:
            continue
    return None


def _rail_for_net(schematic: Schematic, net: Optional[str]) -> Optional[tuple[str, float, float]]:
    """If a net is a power rail, return (name, voltage, current_ma)."""
    if net is None:
        return None
    for r in schematic.rails:
        if r.name == net:
            return (r.name, r.voltage, r.current_ma)
    return None


# --- individual rules ---

def _rule_voltage_range(schematic: Schematic, bundle: "ManifestBundle") -> list[Violation]:
    out: list[Violation] = []
    parts_by_id = _part_specs_lookup(bundle)

    for c in schematic.components:
        if c.kind != "ic":
            continue
        # Find Vcc / power_in pin
        vcc_pin = next((p for p in c.pins if p.kind == PinKind.POWER_IN), None)
        if vcc_pin is None:
            out.append(Violation(
                "R1_voltage_range", "warning", c.refdes,
                f"IC {c.refdes} ({c.part_id}) has no power_in pin declared; cannot check supply."
            ))
            continue
        rail = _rail_for_net(schematic, vcc_pin.net)
        if rail is None:
            out.append(Violation(
                "R1_voltage_range", "error", c.refdes,
                f"IC {c.refdes} ({c.part_id}) Vcc pin '{vcc_pin.name}' is not connected to any power rail."
            ))
            continue
        rail_name, rail_v, _ = rail
        spec = parts_by_id.get(c.part_id)
        if spec is None:
            continue
        vmin = spec.specs.voltage_min
        vmax = spec.specs.voltage_max
        if vmin is not None and rail_v < vmin:
            out.append(Violation(
                "R1_voltage_range", "error", c.refdes,
                f"IC {c.refdes} ({c.part_id}) Vcc={rail_v}V is below spec min {vmin}V (rail '{rail_name}')."
            ))
        if vmax is not None and rail_v > vmax:
            out.append(Violation(
                "R1_voltage_range", "error", c.refdes,
                f"IC {c.refdes} ({c.part_id}) Vcc={rail_v}V exceeds spec max {vmax}V (rail '{rail_name}')."
            ))
    return out


def _rule_no_shorted_rails(schematic: Schematic, _bundle: "ManifestBundle") -> list[Violation]:
    """Two rails of different voltages on the same net = short circuit.

    Catches both:
      a) A pin sitting on a net that two different rails share (different voltages).
      b) Two rails declared with the same name but different voltages (data error).
    """
    out: list[Violation] = []

    # (a) duplicate-name rails with conflicting voltages
    by_name: dict[str, list[PowerRail]] = {}
    for r in schematic.rails:
        by_name.setdefault(r.name, []).append(r)
    for name, group in by_name.items():
        voltages = {r.voltage for r in group}
        if len(voltages) > 1:
            out.append(Violation(
                "R2_no_shorted_rails", "error", f"rail:{name}",
                f"Rail '{name}' declared with conflicting voltages {sorted(voltages)} — short circuit."
            ))

    # (b) pin net name matches more than one rail by name (covers legacy/dup nets)
    for c in schematic.components:
        for p in c.pins:
            if p.kind != PinKind.POWER_IN:
                continue
            if p.net is None:
                continue
            matching = [r for r in schematic.rails if r.name == p.net]
            if len(matching) > 1:
                voltages = {r.voltage for r in matching}
                if len(voltages) > 1:
                    out.append(Violation(
                        "R2_no_shorted_rails", "error", c.refdes,
                        f"Pin '{p.name}' on {c.refdes} sits on net '{p.net}' which carries "
                        f"{sorted(voltages)}V from multiple rail declarations — short circuit."
                    ))
    return out


def _rule_logic_level(schematic: Schematic, bundle: "ManifestBundle") -> list[Violation]:
    """Cross-component digital out -> digital in must respect logic levels."""
    out: list[Violation] = []
    parts_by_id = _part_specs_lookup(bundle)

    # Build: net -> [pin refs on that net]
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
            spec = parts_by_id.get(dc.part_id)
            drive_v = _drive_voltage(spec.specs.voltage if spec else None, spec.specs.io_voltage if spec else None)
            if drive_v is None:
                continue
            for rc, rp in receivers:
                rspec = parts_by_id.get(rc.part_id)
                recv_v = _drive_voltage(rspec.specs.voltage if rspec else None, rspec.specs.io_voltage if rspec else None)
                if recv_v is None:
                    continue
                if drive_v + 0.3 < recv_v:
                    out.append(Violation(
                        "R3_logic_level", "error", rc.refdes,
                        f"{rc.refdes}.{rp.name} expects {recv_v}V logic but is driven by "
                        f"{dc.refdes}.{dp.name} at {drive_v}V — level shifter required."
                    ))
                # Overvoltage: driver voltage exceeds receiver's max input
                if rspec and rspec.specs.voltage_max is not None:
                    if drive_v > rspec.specs.voltage_max + 0.3:
                        out.append(Violation(
                            "R3_logic_level", "error", rc.refdes,
                            f"{dc.refdes}.{dp.name} drives {drive_v}V but "
                            f"{rc.refdes}.{rp.name} max input is {rspec.specs.voltage_max}V — "
                            f"overvoltage will damage the receiver. Level shifter required."
                        ))
                elif recv_v is not None and drive_v > recv_v + 0.5:
                    out.append(Violation(
                        "R3_logic_level", "warning", rc.refdes,
                        f"{dc.refdes}.{dp.name} at {drive_v}V may overdrive "
                        f"{rc.refdes}.{rp.name} at {recv_v}V — verify logic level compatibility."
                    ))
    return out


def _rule_current_budget(schematic: Schematic, bundle: "ManifestBundle") -> list[Violation]:
    out: list[Violation] = []
    parts_by_id = _part_specs_lookup(bundle)

    # Group loads by source rail (via Vcc pin net)
    rail_load_ma: dict[str, float] = {}
    for c in schematic.components:
        vcc_pin = next((p for p in c.pins if p.kind == PinKind.POWER_IN), None)
        if vcc_pin is None or vcc_pin.net is None:
            continue
        spec = parts_by_id.get(c.part_id)
        if spec is None or spec.specs.current_ma is None:
            continue
        rail_load_ma[vcc_pin.net] = rail_load_ma.get(vcc_pin.net, 0.0) + spec.specs.current_ma

    for rail in schematic.rails:
        load = rail_load_ma.get(rail.name, 0.0)
        if load > rail.current_ma:
            out.append(Violation(
                "R4_current_budget", "error", f"rail:{rail.name}",
                f"Rail '{rail.name}' ({rail.voltage}V) supplies {rail.current_ma}mA but "
                f"loads sum to {load:.0f}mA — undersized."
            ))
        elif load > rail.current_ma * 0.85:
            out.append(Violation(
                "R4_current_budget", "warning", f"rail:{rail.name}",
                f"Rail '{rail.name}' at {load:.0f}mA / {rail.current_ma:.0f}mA "
                f"({100*load/rail.current_ma:.0f}%) — tight headroom."
            ))
    return out


def _rule_pull_required(schematic: Schematic, _bundle: "ManifestBundle") -> list[Violation]:
    """Every digital_in must be driven OR have a pull resistor.

    Driven = some output on the same net.
    Pull = a resistor component whose OTHER pin connects to a rail.
    """
    out: list[Violation] = []
    net_to_pins: dict[str, list[tuple[ComponentRef, Pin]]] = {}
    for c in schematic.components:
        for p in c.pins:
            if p.net is None:
                continue
            net_to_pins.setdefault(p.net, []).append((c, p))

    for c in schematic.components:
        for p in c.pins:
            if p.kind != PinKind.DIGITAL_IN:
                continue
            if p.net is None:
                out.append(Violation(
                    "R5_pull_required", "warning", c.refdes,
                    f"Pin {c.refdes}.{p.name} is unconnected (floating) — pull resistor or driver needed."
                ))
                continue
            pins_on_net = net_to_pins.get(p.net, [])
            has_driver = any(other_p.kind in (PinKind.DIGITAL_OUT, PinKind.BIDIRECTIONAL) for _, other_p in pins_on_net)
            has_pull = False
            for other_c, other_p in pins_on_net:
                if other_c.kind != "resistor":
                    continue
                # Check the OTHER pin(s) of this resistor — any of them hitting a rail counts as pull
                for other_other_p in other_c.pins:
                    if other_other_p.name == other_p.name:
                        continue
                    if _rail_for_net(schematic, other_other_p.net) is not None:
                        has_pull = True
                        break
                if has_pull:
                    break
            if not has_driver and not has_pull:
                out.append(Violation(
                    "R5_pull_required", "error", c.refdes,
                    f"Pin {c.refdes}.{p.name} on net '{p.net}' has no driver and no pull resistor — floating input."
                ))
    return out


def _rule_flyback_diode(schematic: Schematic, _bundle: "ManifestBundle") -> list[Violation]:
    """Inductive loads (relay/motor) must have a diode antiparallel."""
    out: list[Violation] = []
    inductive_kinds = {"relay", "motor"}

    for c in schematic.components:
        if c.kind not in inductive_kinds:
            continue
        # Look for a diode whose pins share both nets of the inductive load
        load_nets = {p.net for p in c.pins if p.net is not None}
        if len(load_nets) < 2:
            out.append(Violation(
                "R6_flyback_diode", "warning", c.refdes,
                f"Inductive load {c.refdes} ({c.kind}) has fewer than 2 nets; cannot check flyback."
            ))
            continue
        load_nets_list = list(load_nets)
        a, b = load_nets_list[0], load_nets_list[1]
        has_diode = False
        for other in schematic.components:
            if other.kind != "diode":
                continue
            diode_nets = {p.net for p in other.pins if p.net is not None}
            if a in diode_nets and b in diode_nets:
                has_diode = True
                break
        if not has_diode:
            out.append(Violation(
                "R6_flyback_diode", "error", c.refdes,
                f"Inductive load {c.refdes} ({c.kind}) between nets '{a}' and '{b}' "
                f"has no flyback diode — back-EMF will kill the driver."
            ))
    return out


def _rule_decoupling(schematic: Schematic, _bundle: "ManifestBundle") -> list[Violation]:
    """Every IC should have at least one decoupling capacitor on its power net."""
    out: list[Violation] = []
    ic_components = [c for c in schematic.components if c.kind == "ic"]
    cap_nets = set()
    for c in schematic.components:
        if c.kind == "capacitor":
            for p in c.pins:
                if p.net is not None:
                    cap_nets.add(p.net)

    for ic in ic_components:
        vcc_pin = next((p for p in ic.pins if p.kind == PinKind.POWER_IN), None)
        if vcc_pin is None or vcc_pin.net is None:
            continue
        if vcc_pin.net not in cap_nets:
            out.append(Violation(
                "R7_decoupling", "warning", ic.refdes,
                f"IC {ic.refdes} ({ic.part_id}) has no decoupling capacitor on "
                f"power net '{vcc_pin.net}'. Add 100nF ceramic cap close to Vcc/GND."
            ))

    if not ic_components:
        has_any_cap = any(c.kind == "capacitor" for c in schematic.components)
        if not has_any_cap and len(schematic.components) > 2:
            out.append(Violation(
                "R7_decoupling", "warning", "BOM",
                "No capacitors in BOM — consider adding decoupling caps."
            ))
    return out


# --- entrypoint ---

def check_erc(schematic: Schematic, bundle: "ManifestBundle") -> ERCReport:
    violations: list[Violation] = []
    violations += _rule_voltage_range(schematic, bundle)
    violations += _rule_no_shorted_rails(schematic, bundle)
    violations += _rule_logic_level(schematic, bundle)
    violations += _rule_current_budget(schematic, bundle)
    violations += _rule_pull_required(schematic, bundle)
    violations += _rule_flyback_diode(schematic, bundle)
    violations += _rule_decoupling(schematic, bundle)

    errors = [v for v in violations if v.severity == "error"]
    passed = len(errors) == 0
    summary = (
        f"ERC {'PASS' if passed else 'FAIL'} — "
        f"{len(errors)} error(s), {len(violations) - len(errors)} warning(s)."
    )
    return ERCReport(passed=passed, violations=violations, summary=summary)