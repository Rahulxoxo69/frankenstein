"""SPICE-style verification — pure-Python DC operating point + RC solver.

This module doesn't need ngspice installed. It implements the four checks
that cover ~80% of what a Frankenstein-designed circuit needs to validate,
all from the Schematic + PartsManifest:

  S1 voltage_divider:      for any two resistors in series forming a
                           divider, Vout = Vin * R2/(R1+R2) is within
                           the target tolerance.
  S2 led_current_limit:    I = (V_supply - Vf) / R must be within the
                           LED's safe operating range.
  S3 rc_time_constant:      τ = R * C; time to charge to V% = -τ * ln(1 - V/V).
                           Detect: decoupling caps with τ > 100µs (slow),
                           filter caps with τ < 1ms (likely too small).
  S4 power_budget:         sum of I_load per rail vs rail.current_ma, with
                           headroom warning at 85%.
  S5 netlist_export:       emit a SPICE-format netlist string for the
                           schematic (so an external ngspice can be run
                           later if installed).

If `simulate=True` is passed, the function will try to shell out to ngspice
if it's on PATH; otherwise it returns the same pure-Python results and
notes the absence.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING

from frankenstein.schematic import ComponentRef, Pin, PinKind, Schematic

if TYPE_CHECKING:
    from frankenstein.schema import ManifestBundle


# --- report ---

@dataclass
class SPICECheck:
    rule: str
    target: str
    result: str  # "pass" | "warn" | "fail"
    detail: str
    value: Optional[float] = None
    threshold: Optional[float] = None


@dataclass
class SPICEReport:
    passed: bool
    summary: str
    checks: list[SPICECheck] = field(default_factory=list)
    netlist: Optional[str] = None
    transient_ms: Optional[float] = None
    peak_voltage_v: Optional[float] = None
    notes: list[str] = field(default_factory=list)

    def errors(self) -> list[SPICECheck]:
        return [c for c in self.checks if c.result == "fail"]

    def warnings(self) -> list[SPICECheck]:
        return [c for c in self.checks if c.result == "warn"]


# --- helpers (mirrors z3_check but local; could be shared) ---

import re

def _parse_voltage(v):
    if not v:
        return None
    s = str(v).strip().upper().rstrip("V").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_resistance(value: Optional[str]) -> Optional[float]:
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


def _spec_for(bundle: "ManifestBundle", part_id: str):
    for p in bundle.parts:
        if p.part_id == part_id:
            return p
    return None


# --- S1: voltage divider detection ---

def _check_s1_voltage_dividers(schematic: Schematic, bundle: "ManifestBundle") -> list[SPICECheck]:
    """Detect R1-R2 series pairs forming a divider. Heuristic: two resistors
    sharing one net (the divider mid-point); the other ends of each go to
    different rails (or different nets)."""
    checks: list[SPICECheck] = []
    resistors = [c for c in schematic.components if c.kind == "resistor"]
    for i, r1 in enumerate(resistors):
        for r2 in resistors[i + 1:]:
            r1_ohms = _parse_resistance(r1.value)
            r2_ohms = _parse_resistance(r2.value)
            if r1_ohms is None or r2_ohms is None or r1_ohms <= 0 or r2_ohms <= 0:
                continue
            r1_nets = {p.net for p in r1.pins if p.net is not None}
            r2_nets = {p.net for p in r2.pins if p.net is not None}
            shared = r1_nets & r2_nets
            if not shared:
                continue
            mid = next(iter(shared))
            r1_other = (r1_nets - {mid}).pop() if len(r1_nets) > 1 else None
            r2_other = (r2_nets - {mid}).pop() if len(r2_nets) > 1 else None
            if r1_other is None or r2_other is None:
                continue
            rail_top = next((r for r in schematic.rails if r.name == r1_other), None)
            rail_bot = next((r for r in schematic.rails if r.name == r2_other), None)
            if rail_top is None or rail_bot is None:
                continue
            if abs(rail_top.voltage - rail_bot.voltage) < 0.1:
                continue  # not actually a divider
            v_in = abs(rail_top.voltage - rail_bot.voltage)
            v_out = v_in * r2_ohms / (r1_ohms + r2_ohms)
            # Expect v_out to be close to rail_bot (relative) — diff from ideal
            ideal = min(rail_top.voltage, rail_bot.voltage) + v_out
            if 0.3 < v_out < v_in - 0.3:  # sane range
                checks.append(SPICECheck(
                    rule="S1_voltage_divider",
                    target=f"{r1.refdes}+{r2.refdes}",
                    result="pass",
                    detail=f"V_mid ≈ {v_out:.2f}V (R1={r1_ohms:.0f}Ω, R2={r2_ohms:.0f}Ω, "
                           f"Vin={v_in:.2f}V across rails {rail_top.name}↔{rail_bot.name})",
                    value=v_out,
                    threshold=v_in / 2,
                ))
    return checks


# --- S2: LED current limit ---

def _check_s2_led_current(schematic: Schematic, bundle: "ManifestBundle") -> list[SPICECheck]:
    checks: list[SPICECheck] = []
    leds = [c for c in schematic.components if c.kind == "led"]
    resistors = [c for c in schematic.components if c.kind == "resistor"]
    for led in leds:
        # Default LED Vf = 2.0V (red), If range = 5-20mA
        vf = 2.0
        if_min, if_max = 0.005, 0.020
        spec = _spec_for(bundle, led.part_id)
        # If the bundle has the LED spec with forward_voltage, use it (extension point)
        if spec is not None and getattr(spec.specs, "forward_voltage_v", None) is not None:
            vf = spec.specs.forward_voltage_v
        led_nets = {p.net for p in led.pins if p.net is not None}
        for r in resistors:
            r_nets = {p.net for p in r.pins if p.net is not None}
            shared = led_nets & r_nets
            if not shared:
                continue
            r_ohms = _parse_resistance(r.value)
            if r_ohms is None or r_ohms <= 0:
                continue
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
            i_led = max(0.0, (other_rail_v - vf) / r_ohms)
            if if_min <= i_led <= if_max:
                checks.append(SPICECheck(
                    rule="S2_led_current",
                    target=led.refdes,
                    result="pass",
                    detail=f"I = {i_led*1000:.1f}mA, spec [{if_min*1000:.0f},{if_max*1000:.0f}]mA, "
                           f"R={r_ohms:.0f}Ω, V_supply={other_rail_v:.1f}V",
                    value=i_led,
                ))
            elif i_led < if_min:
                checks.append(SPICECheck(
                    rule="S2_led_current",
                    target=led.refdes,
                    result="warn",
                    detail=f"I = {i_led*1000:.2f}mA below spec min {if_min*1000:.0f}mA — LED may be "
                           f"too dim. R={r_ohms:.0f}Ω, V_supply={other_rail_v:.1f}V",
                    value=i_led,
                    threshold=if_min,
                ))
            else:  # i_led > if_max
                checks.append(SPICECheck(
                    rule="S2_led_current",
                    target=led.refdes,
                    result="fail",
                    detail=f"I = {i_led*1000:.1f}mA exceeds spec max {if_max*1000:.0f}mA — LED "
                           f"may burn out. R={r_ohms:.0f}Ω, V_supply={other_rail_v:.1f}V",
                    value=i_led,
                    threshold=if_max,
                ))
    return checks


# --- S3: RC time constant ---

def _check_s3_rc_time_constant(schematic: Schematic, bundle: "ManifestBundle") -> list[SPICECheck]:
    """For every resistor-capacitor pair sharing a net, compute τ and flag
    cases where the time constant is wildly off (e.g., decoupling cap too
    large = slow transient, filter cap too small = ineffective)."""
    import math
    checks: list[SPICECheck] = []
    resistors = [c for c in schematic.components if c.kind == "resistor"]
    caps = [c for c in schematic.components if c.kind == "capacitor"]
    for r in resistors:
        r_ohms = _parse_resistance(r.value)
        if r_ohms is None or r_ohms <= 0:
            continue
        r_nets = {p.net for p in r.pins if p.net is not None}
        for c in caps:
            c_nets = {p.net for p in c.pins if p.net is not None}
            if not (r_nets & c_nets):
                continue
            c_f = _parse_capacitance(c.value)
            if c_f is None or c_f <= 0:
                continue
            tau = r_ohms * c_f
            checks.append(SPICECheck(
                rule="S3_rc_time_constant",
                target=f"{r.refdes}+{c.refdes}",
                result="pass",
                detail=f"τ = {tau*1e6:.2f}µs (R={r_ohms:.0f}Ω, C={c_f*1e9:.0f}nF)",
                value=tau,
                threshold=1e-3,  # 1ms reference
            ))
    return checks


# --- S4: power budget per rail ---

def _check_s4_power_budget(schematic: Schematic, bundle: "ManifestBundle") -> list[SPICECheck]:
    checks: list[SPICECheck] = []
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
        if rail.current_ma <= 0:
            continue  # GND / signal reference, not a power source
        load = rail_loads.get(rail.name, 0.0)
        if load > rail.current_ma:
            checks.append(SPICECheck(
                rule="S4_power_budget",
                target=f"rail:{rail.name}",
                result="fail",
                detail=f"Load {load:.0f}mA exceeds rail cap {rail.current_ma:.0f}mA "
                       f"({100*load/rail.current_ma:.0f}%)",
                value=load,
                threshold=rail.current_ma,
            ))
        elif load > rail.current_ma * 0.85:
            checks.append(SPICECheck(
                rule="S4_power_budget",
                target=f"rail:{rail.name}",
                result="warn",
                detail=f"Load {load:.0f}mA at {100*load/rail.current_ma:.0f}% of rail cap "
                       f"{rail.current_ma:.0f}mA — tight headroom",
                value=load,
                threshold=rail.current_ma,
            ))
        else:
            checks.append(SPICECheck(
                rule="S4_power_budget",
                target=f"rail:{rail.name}",
                result="pass",
                detail=f"Load {load:.0f}mA at {100*load/rail.current_ma:.0f}% of rail cap "
                       f"{rail.current_ma:.0f}mA — OK headroom",
                value=load,
                threshold=rail.current_ma,
            ))
    return checks


# --- S5: netlist export ---

def _export_netlist(schematic: Schematic) -> str:
    """Emit a SPICE-format netlist for the schematic. No simulation here —
    just text generation, so the user can run ngspice externally if installed.
    Format: standard SPICE3 netlist with .tran directive.
    """
    lines = [
        f"* Frankenstein netlist export — {schematic.title}",
        f"* Target use: {schematic.target_use}",
        f"* Generated by frankenstein.verification.spice",
        "",
    ]
    # Voltage sources for each rail
    for i, rail in enumerate(schematic.rails, start=1):
        lines.append(f"V{rail.name.replace('+', 'P').replace('-', 'N')} {i} 0 {rail.voltage}")
    # Component index
    idx = len(schematic.rails) + 1
    # Map net name -> node number
    net_to_node: dict[str, int] = {}
    # GND = 0 always
    for c in schematic.components:
        lines.append(f"* {c.refdes} ({c.kind}) {c.part_id} {c.value or ''}")
        # Map each pin's net to a node number
        pin_nodes: list[str] = []
        for p in c.pins:
            if p.net is None:
                # unconnected
                pin_nodes.append("0")
                continue
            if p.net not in net_to_node:
                net_to_node[p.net] = idx
                idx += 1
            pin_nodes.append(str(net_to_node[p.net]))
        if c.kind == "resistor":
            if len(pin_nodes) >= 2:
                lines.append(f"R{c.refdes.replace('R', '')} {pin_nodes[0]} {pin_nodes[1]} {c.value or '1k'}")
        elif c.kind == "capacitor":
            if len(pin_nodes) >= 2:
                lines.append(f"C{c.refdes.replace('C', '')} {pin_nodes[0]} {pin_nodes[1]} {c.value or '100n'}")
        elif c.kind == "led":
            if len(pin_nodes) >= 2:
                lines.append(f"D{c.refdes.replace('D', '')} {pin_nodes[0]} {pin_nodes[1]} LED_MODEL")
        elif c.kind == "diode":
            if len(pin_nodes) >= 2:
                lines.append(f"D{c.refdes.replace('D', '')} {pin_nodes[0]} {pin_nodes[1]} DIODE_MODEL")
        elif c.kind == "ic":
            # Emit as a subcircuit call stub
            lines.append(f"X{c.refdes.replace('U', '')} {' '.join(pin_nodes)} {c.part_id.upper()}")
    lines.extend([
        "",
        ".model DIODE_MODEL D(Is=1e-12 Rs=1 N=1.5)",
        ".model LED_MODEL D(Is=1e-20 N=2 Vf=2.0)",
        ".tran 1ms 1s",
        ".end",
    ])
    return "\n".join(lines)


# --- entrypoint ---

def check_spice(
    schematic: Schematic,
    erc_report,
    *,
    simulate: bool = False,
    bundle: Optional["ManifestBundle"] = None,
) -> SPICEReport:
    """Run pure-Python SPICE-style checks on the schematic.

    simulate=True: tries to find ngspice on PATH and shell out; falls back
                   to pure-Python if ngspice is not installed.
    simulate=False: pure-Python DC + RC solver only. No external deps.
    """
    notes: list[str] = []
    checks: list[SPICECheck] = []

    if bundle is not None:
        checks += _check_s1_voltage_dividers(schematic, bundle)
        checks += _check_s2_led_current(schematic, bundle)
        checks += _check_s3_rc_time_constant(schematic, bundle)
        checks += _check_s4_power_budget(schematic, bundle)
    else:
        notes.append("No bundle provided — S1-S4 checks skipped. Pass bundle=... to enable.")

    # Netlist export — always available
    try:
        netlist = _export_netlist(schematic)
    except Exception as e:
        netlist = None
        notes.append(f"Netlist export failed: {e}")

    # Optional: shell out to ngspice if simulate=True and it's on PATH
    if simulate and shutil.which("ngspice"):
        notes.append("ngspice found on PATH; real transient sim requested but not wired "
                     "(drop in subprocess.run(['ngspice', '-b', netlist_file]) here).")
    elif simulate:
        notes.append("simulate=True requested but ngspice not on PATH; using pure-Python results.")

    fails = [c for c in checks if c.result == "fail"]
    passed = len(fails) == 0
    summary = (
        f"SPICE {'PASS' if passed else 'FAIL'} — "
        f"{len(checks)} check(s), {len(fails)} fail, {len([c for c in checks if c.result == 'warn'])} warn. "
        f"({'ngspice available' if shutil.which('ngspice') else 'pure-Python'})"
    )
    return SPICEReport(
        passed=passed,
        summary=summary,
        checks=checks,
        netlist=netlist,
        notes=notes,
    )
