"""Tests for the new validation modules: Z3, SPICE, compile_check.

These tests exercise the real implementations (not stubs) on known-good
and known-bad schematics. They run without a real LLM, ngspice, or
arduino-cli — all checks are pure-Python.
"""

from __future__ import annotations

import pytest

from frankenstein.mocks import IRRIGATION_BUNDLE
from frankenstein.schematic import ComponentRef, Pin, PinKind, PowerRail, Schematic
from frankenstein.verification.compile_check import check_compile
from frankenstein.verification.erc import ERCReport
from frankenstein.verification.spice import check_spice
from frankenstein.verification.z3_check import check_z3


# ---------- shared fixtures ----------

@pytest.fixture
def led_circuit() -> Schematic:
    """Valid LED + resistor on 5V rail. Should pass all checks."""
    return Schematic(
        title="LED test",
        target_use="test",
        rails=[
            PowerRail(name="5V", voltage=5.0, current_ma=500.0),
            PowerRail(name="GND", voltage=0.0, current_ma=0.0),
        ],
        components=[
            ComponentRef(part_id="r_led", refdes="R1", kind="resistor", value="330R", pins=[
                Pin(name="1", kind=PinKind.PASSIVE, net="5V"),
                Pin(name="2", kind=PinKind.PASSIVE, net="led_n"),
            ]),
            ComponentRef(part_id="led_1", refdes="D1", kind="led", pins=[
                Pin(name="A", kind=PinKind.PASSIVE, net="led_n"),
                Pin(name="K", kind=PinKind.PASSIVE, net="GND"),
            ]),
        ],
    )


@pytest.fixture
def divider_circuit() -> Schematic:
    """Valid 5V→2.5V divider using two equal resistors."""
    return Schematic(
        title="Divider test",
        target_use="test",
        rails=[
            PowerRail(name="5V", voltage=5.0, current_ma=100.0),
            PowerRail(name="GND", voltage=0.0, current_ma=0.0),
        ],
        components=[
            ComponentRef(part_id="r_top", refdes="R1", kind="resistor", value="10k", pins=[
                Pin(name="1", kind=PinKind.PASSIVE, net="5V"),
                Pin(name="2", kind=PinKind.PASSIVE, net="mid"),
            ]),
            ComponentRef(part_id="r_bot", refdes="R2", kind="resistor", value="10k", pins=[
                Pin(name="1", kind=PinKind.PASSIVE, net="mid"),
                Pin(name="2", kind=PinKind.PASSIVE, net="GND"),
            ]),
        ],
    )


# ============================================================
# Z3 tests
# ============================================================

def test_z3_returns_synthetic_when_solve_disabled(led_circuit):
    r = check_z3(led_circuit, IRRIGATION_BUNDLE, solve=False)
    assert r.passed is True
    assert r.constraints_checked == 0
    assert "stub mode" in r.summary.lower()


def test_z3_real_solve_on_valid_led_circuit(led_circuit):
    r = check_z3(led_circuit, IRRIGATION_BUNDLE, solve=True)
    # LED circuit: no ICs (so Z1 skipped), no ICs on rails (so Z6 skips),
    # but Z2 power dissipation and Z7 logic level may still check
    assert r.constraints_checked >= 0  # doesn't crash
    # No unsat errors expected (no ICs to violate supply, no digital out→in)
    assert r.passed is True


def test_z3_detects_undersized_resistor():
    """A 0.1W resistor carrying 1A at 12V dissipates 120W → must fail Z2."""
    sch = Schematic(
        title="Hot resistor",
        target_use="test",
        rails=[PowerRail(name="12V", voltage=12.0, current_ma=1000.0)],
        components=[
            ComponentRef(part_id="r_burn", refdes="R1", kind="resistor", value="1R", pins=[
                Pin(name="1", kind=PinKind.PASSIVE, net="12V"),
                Pin(name="2", kind=PinKind.PASSIVE, net="load"),
            ]),
        ],
    )
    r = check_z3(sch, IRRIGATION_BUNDLE, solve=True)
    # P = 12^2 / 1 = 144W; rated 0.125W → unsat
    unsat = [c for c in r.constraints if c.result == "unsat"]
    assert any(c.rule == "Z2_power_dissipation" for c in unsat)


def test_z3_detects_logic_level_mismatch():
    """5V Arduino output driving 3.3V ESP32 input — must fail Z7."""
    from frankenstein.schema import ManifestBundle, PartSource, PartSpecs, PartsManifest, PartStatus, SpecSource

    def _make_manifest(pid, v, io, cur_ma):
        return PartsManifest(
            part_id=pid, name=pid, status=PartStatus.FUNCTIONAL,
            confidence=0.9, source=PartSource.MOCK,
            specs=PartSpecs(
                voltage=v, current_ma=cur_ma, io_voltage=io,
                source=SpecSource.INFERRED, confidence=0.9,
            ),
        )

    sch = Schematic(
        title="Logic level",
        target_use="test",
        rails=[PowerRail(name="5V", voltage=5.0, current_ma=500.0),
               PowerRail(name="3V3", voltage=3.3, current_ma=500.0)],
        components=[
            ComponentRef(part_id="arduino", refdes="U1", kind="ic", pins=[
                Pin(name="VCC", kind=PinKind.POWER_IN, net="5V"),
                Pin(name="D5", kind=PinKind.DIGITAL_OUT, net="sig"),
            ]),
            ComponentRef(part_id="esp32", refdes="U2", kind="ic", pins=[
                Pin(name="3V3", kind=PinKind.POWER_IN, net="3V3"),
                Pin(name="IO5", kind=PinKind.DIGITAL_IN, net="sig"),
            ]),
        ],
    )
    bundle = ManifestBundle(
        schema_version="0.1.0",
        bundle_id="t",
        detected_at="2026-06-22T00:00:00Z",
        parts=[
            _make_manifest("arduino", "5V", "5V", 200.0),
            _make_manifest("esp32", "3.3V", "3.3V", 240.0),
        ],
    )
    r = check_z3(sch, bundle, solve=True)
    unsat = [c for c in r.constraints if c.result == "unsat"]
    assert any(c.rule == "Z7_logic_level_compat" for c in unsat), \
        f"Expected Z7 unsat for 5V→3.3V, got: {r.summary}"


# ============================================================
# SPICE tests
# ============================================================

def test_spice_synthetic_when_no_bundle(led_circuit):
    erc = ERCReport(passed=True, violations=[], summary="OK")
    r = check_spice(led_circuit, erc, simulate=False, bundle=None)
    # Without bundle, all S1-S4 are skipped but netlist still generated
    assert r.netlist is not None
    assert r.passed is True


def test_spice_netlist_exported(led_circuit):
    erc = ERCReport(passed=True, violations=[], summary="OK")
    r = check_spice(led_circuit, erc, simulate=False, bundle=IRRIGATION_BUNDLE)
    assert r.netlist is not None
    assert "* Frankenstein netlist export" in r.netlist
    assert ".end" in r.netlist
    assert "R1" in r.netlist  # resistor
    assert "D1" in r.netlist  # LED


def test_spice_detects_voltage_divider(divider_circuit):
    erc = ERCReport(passed=True, violations=[], summary="OK")
    r = check_spice(divider_circuit, erc, simulate=False, bundle=IRRIGATION_BUNDLE)
    div_checks = [c for c in r.checks if c.rule == "S1_voltage_divider"]
    assert len(div_checks) >= 1
    # Mid should be ~2.5V
    assert any(2.0 < c.value < 3.0 for c in div_checks)


def test_spice_led_current_in_spec(led_circuit):
    erc = ERCReport(passed=True, violations=[], summary="OK")
    r = check_spice(led_circuit, erc, simulate=False, bundle=IRRIGATION_BUNDLE)
    led_checks = [c for c in r.checks if c.rule == "S2_led_current"]
    assert len(led_checks) == 1
    c = led_checks[0]
    # I = (5-2)/330 ≈ 9.1mA
    assert 0.005 < c.value < 0.020
    assert c.result == "pass"


def test_spice_led_overcurrent_fails():
    """Too-small resistor → LED overcurrent → must FAIL (not warn)."""
    sch = Schematic(
        title="Bad LED",
        target_use="test",
        rails=[PowerRail(name="5V", voltage=5.0, current_ma=500.0)],
        components=[
            ComponentRef(part_id="r", refdes="R1", kind="resistor", value="10R", pins=[
                Pin(name="1", kind=PinKind.PASSIVE, net="5V"),
                Pin(name="2", kind=PinKind.PASSIVE, net="led_n"),
            ]),
            ComponentRef(part_id="led", refdes="D1", kind="led", pins=[
                Pin(name="A", kind=PinKind.PASSIVE, net="led_n"),
                Pin(name="K", kind=PinKind.PASSIVE, net=None),  # to ground by default
            ]),
        ],
    )
    # Wire K to GND rail
    sch.rails.append(PowerRail(name="GND", voltage=0.0, current_ma=0.0))
    sch.components[1].pins[1].net = "GND"
    erc = ERCReport(passed=True, violations=[], summary="OK")
    r = check_spice(sch, erc, simulate=False, bundle=IRRIGATION_BUNDLE)
    led = [c for c in r.checks if c.rule == "S2_led_current"][0]
    # I = (5-2)/10 = 0.3A = 300mA, way above 20mA spec
    assert led.value > 0.020
    assert led.result == "fail"
    assert r.passed is False


def test_spice_rail_overload_fails():
    """Sum of IC currents > rail cap → must FAIL."""
    from frankenstein.schema import ManifestBundle, PartSource, PartSpecs, PartsManifest, PartStatus, SpecSource

    sch = Schematic(
        title="Overload",
        target_use="test",
        rails=[PowerRail(name="3V3", voltage=3.3, current_ma=100.0)],  # tiny rail
        components=[
            ComponentRef(part_id="ic_big", refdes="U1", kind="ic", pins=[
                Pin(name="VCC", kind=PinKind.POWER_IN, net="3V3"),
            ]),
        ],
    )
    big_ic = PartsManifest(
        part_id="ic_big", name="ic_big", status=PartStatus.FUNCTIONAL,
        confidence=0.9, source=PartSource.MOCK,
        specs=PartSpecs(
            voltage="3.3V", current_ma=500.0, io_voltage="3.3V",
            source=SpecSource.INFERRED, confidence=0.9,
        ),
    )
    bundle = ManifestBundle(
        schema_version="0.1.0",
        bundle_id="t",
        detected_at="2026-06-22T00:00:00Z",
        parts=[big_ic],
    )
    erc = ERCReport(passed=True, violations=[], summary="OK")
    r = check_spice(sch, erc, simulate=False, bundle=bundle)
    budget = [c for c in r.checks if c.rule == "S4_power_budget"]
    assert any(c.result == "fail" for c in budget)


# ============================================================
# Compile check tests
# ============================================================

def test_compile_valid_firmware_passes(led_circuit):
    """A simple, correct Arduino sketch should pass."""
    fw = """
void setup() {
  pinMode(2, OUTPUT);
  Serial.begin(9600);
}
void loop() {
  digitalWrite(2, HIGH);
  delay(1000);
  digitalWrite(2, LOW);
  delay(1000);
}
"""
    r = check_compile(fw, led_circuit)
    # Note: pin 2 is not in the LED circuit's schematic, so it's allowed
    # because it starts with a digit (not checked against schematic)
    assert r.passed is True


def test_compile_detects_missing_setup_loop(led_circuit):
    fw = """
int main() {
  return 0;
}
"""
    r = check_compile(fw, led_circuit)
    assert r.passed is False
    assert any(e.rule == "C1_function_signature" for e in r.errors)


def test_compile_detects_unbalanced_braces(led_circuit):
    fw = """
void setup() {
  pinMode(2, OUTPUT);
  // missing closing brace
void loop() {
  digitalWrite(2, HIGH);
}
"""
    r = check_compile(fw, led_circuit)
    assert r.passed is False
    assert any(e.rule == "C2_brace_balance" for e in r.errors)


def test_compile_detects_bad_pinmode_mode(led_circuit):
    fw = """
void setup() {
  pinMode(2, OUTPT);  // typo
}
void loop() {
  delay(100);
}
"""
    r = check_compile(fw, led_circuit)
    assert r.passed is False
    assert any(e.rule == "C3_pinmode_args" and "OUTPT" in e.message for e in r.errors)


def test_compile_detects_out_of_range_analog(led_circuit):
    fw = """
void setup() { pinMode(3, OUTPUT); }
void loop() {
  analogWrite(3, 999);  // max is 255
}
"""
    r = check_compile(fw, led_circuit)
    assert r.passed is False
    assert any(e.rule == "C5_analog_io_args" and "255" in e.message for e in r.errors)


def test_compile_detects_negative_delay(led_circuit):
    fw = """
void setup() { pinMode(3, OUTPUT); }
void loop() { delay(-100); }
"""
    r = check_compile(fw, led_circuit)
    assert r.passed is False
    assert any(e.rule == "C6_delay_args" and "negative" in e.message for e in r.errors)


def test_compile_warns_on_pin_direction_conflict(led_circuit):
    fw = """
void setup() {
  pinMode(2, OUTPUT);
}
void loop() {
  digitalRead(2);  // reading an output pin — likely a bug
}
"""
    r = check_compile(fw, led_circuit)
    assert any(e.rule == "C8_pin_direction_match" for e in r.warnings_only())


def test_compile_strips_comments_before_check(led_circuit):
    """Comments containing 'fake' pinMode shouldn't trigger C3."""
    fw = """
void setup() {
  // pinMode(99, BOGUS);  // this is in a comment, should be ignored
  pinMode(2, OUTPUT);
}
void loop() { delay(100); }
"""
    r = check_compile(fw, led_circuit)
    assert r.passed is True


def test_compile_empty_source_fails(led_circuit):
    r = check_compile("", led_circuit)
    assert r.passed is False
    assert any(e.rule == "C0_empty_source" for e in r.errors)


def test_compile_detects_unknown_pin_for_schematic(led_circuit):
    """A pin that's not in the schematic AND doesn't start with A/GPIO/PIN
    should fail C3 (it must be a named pin from the schematic)."""
    fw = """
void setup() {
  pinMode(MY_CUSTOM_PIN, OUTPUT);  // not in schematic, not a prefix
}
void loop() { delay(100); }
"""
    r = check_compile(fw, led_circuit)
    # MY_CUSTOM_PIN doesn't start with A/GPIO/PIN_ → should fail
    errs = r.errors
    assert any(e.rule == "C3_pinmode_args" and "MY_CUSTOM_PIN" in e.message for e in errs), \
        f"Expected C3 error for unknown pin, got: {r.summary}"
