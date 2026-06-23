"""ERC tests — the safety net of Member 2's half."""

import pytest

from frankenstein.mocks import IRRIGATION_BUNDLE
from frankenstein.schematic import ComponentRef, Pin, PinKind, PowerRail, Schematic
from frankenstein.verification.erc import check_erc


# --- helpers ---

def _pin(name, net, kind, drive=None):
    return Pin(name=name, net=net, kind=kind, drive_voltage=drive)


def _ic(part_id, refdes, vcc_net, vcc_voltage, io_voltage, pin_map):
    pins = [Pin(name=p, net=net, kind=kind, drive_voltage=io_voltage if kind != PinKind.POWER_IN else str(vcc_voltage) + "V")
            for p, (net, kind) in pin_map.items()]
    return ComponentRef(part_id=part_id, refdes=refdes, kind="ic", value=None, pins=pins)


def _rail(name, v, ma):
    return PowerRail(name=name, voltage=v, current_ma=ma)


# --- "known good" irrigation schematic — should pass ERC ---

def _good_irrigation_schematic() -> Schematic:
    return Schematic(
        title="Irrigation controller (good)",
        target_use="auto-irrigation",
        rails=[
            _rail("12V", 12.0, 1000.0),
            _rail("5V", 5.0, 2000.0),
            _rail("3V3", 3.3, 1000.0),
            _rail("GND", 0.0, 5000.0),
        ],
        components=[
            # ESP32: 3V3 pin is module supply; VIN feeds onboard LDO (no ERC check on VIN net voltage)
            _ic("esp32_01", "U1", "3V3", 3.3, "3.3V", {
                "3V3":   ("3V3", PinKind.POWER_IN),
                "GPIO4": ("net_dht", PinKind.DIGITAL_IN),
                "GPIO34": ("net_soil", PinKind.ANALOG_IN),
                "GPIO5":  ("net_shifter_in", PinKind.DIGITAL_OUT),
                "GPIO2":  ("net_led", PinKind.DIGITAL_OUT),
                "VIN":    ("5V", PinKind.POWER_IN),
                "GND":    ("GND", PinKind.GROUND),
            }),
            # DHT22 on 3V3, data pin pulled up to 3V3
            _ic("dht22_01", "U2", "3V3", 3.3, "3.3V", {
                "DATA": ("net_dht", PinKind.BIDIRECTIONAL),
                "VCC":  ("3V3", PinKind.POWER_IN),
                "GND":  ("GND", PinKind.GROUND),
            }),
            # Soil moisture analog output to GPIO34 (no level issue, both 3.3V)
            _ic("soil_moisture_01", "U3", "3V3", 3.3, "3.3V", {
                "AOUT": ("net_soil", PinKind.ANALOG_OUT),
                "VCC":  ("3V3", PinKind.POWER_IN),
                "GND":  ("GND", PinKind.GROUND),
            }),
            # 7805 regulator: 12V in, 5V out — 7805 spec min 7V so 12V is fine
            _ic("reg_7805_01", "U4", "5V", 5.0, "5V", {
                "INPUT":  ("12V", PinKind.POWER_IN),
                "OUTPUT": ("5V", PinKind.POWER_OUT),
                "GND":    ("GND", PinKind.GROUND),
            }),
            # Level shifter: 3V3 <-> 5V
            _ic("lvl_shift_01", "U5", "5V", 5.0, "5V", {
                "LV_IN":  ("net_shifter_in", PinKind.DIGITAL_IN),
                "LV_OUT": ("net_shifter_lv", PinKind.DIGITAL_OUT),
                "HV_IN":  ("net_shifter_hv", PinKind.DIGITAL_OUT),
                "HV_OUT": ("net_relay_in", PinKind.DIGITAL_OUT),
                "LV":     ("3V3", PinKind.POWER_IN),
                "HV":     ("5V", PinKind.POWER_IN),
                "GND":    ("GND", PinKind.GROUND),
            }),
            # 5V relay with flyback diode (D1) across coil
            ComponentRef(part_id="relay_5v_01", refdes="K1", kind="relay", value="5V coil",
                         pins=[
                             Pin(name="coil_a", net="net_relay_in", kind=PinKind.DIGITAL_IN, drive_voltage="5V"),
                             Pin(name="coil_b", net="GND", kind=PinKind.GROUND),
                         ]),
            ComponentRef(part_id="flyback_d1", refdes="D1", kind="diode", value="1N4007",
                         pins=[
                             Pin(name="A", net="GND", kind=PinKind.POWER_IN, drive_voltage=None),
                             Pin(name="K", net="net_relay_in", kind=PinKind.POWER_OUT, drive_voltage=None),
                         ]),
            # LED with current-limit resistor (LED is 'led' kind, not 'ic' — no power_in check)
            ComponentRef(part_id="led_indicator_01", refdes="D2", kind="led", value="green 3mm",
                         pins=[
                             Pin(name="A", net="net_led_res", kind=PinKind.POWER_IN, drive_voltage=None),
                             Pin(name="K", net="GND", kind=PinKind.GROUND),
                         ]),
            ComponentRef(part_id="r_led_01", refdes="R1", kind="resistor", value="330Ω",
                         pins=[
                             Pin(name="1", net="net_led", kind=PinKind.DIGITAL_IN, drive_voltage=None),
                             Pin(name="2", net="net_led_res", kind=PinKind.POWER_OUT, drive_voltage=None),
                         ]),
            # Pull-up resistor on DHT22 data line
            ComponentRef(part_id="r_pull_dht", refdes="R2", kind="resistor", value="10kΩ",
                         pins=[
                             Pin(name="1", net="net_dht", kind=PinKind.DIGITAL_IN, drive_voltage=None),
                             Pin(name="2", net="3V3", kind=PinKind.POWER_OUT, drive_voltage=None),
                         ]),
            # Decoupling caps
            ComponentRef(part_id="cap_01", refdes="C1", kind="capacitor", value="100nF",
                         pins=[
                             Pin(name="1", net="3V3", kind=PinKind.POWER_IN, drive_voltage=None),
                             Pin(name="2", net="GND", kind=PinKind.GROUND),
                         ]),
            ComponentRef(part_id="cap_02", refdes="C2", kind="capacitor", value="100nF",
                         pins=[
                             Pin(name="1", net="5V", kind=PinKind.POWER_IN, drive_voltage=None),
                             Pin(name="2", net="GND", kind=PinKind.GROUND),
                         ]),
        ],
        notes=[
            "esp32_01: ASSUME_REPAIR — vault-sourced, fully functional.",
            "relay_5v_01: ASSUME_REPAIR — bent pin straightened before assembly.",
        ],
    )


def test_good_irrigation_schematic_passes_erc():
    rep = check_erc(_good_irrigation_schematic(), IRRIGATION_BUNDLE)
    assert rep.passed, f"expected PASS but got: {rep.summary}\n" + "\n".join(
        f"  [{v.severity}] {v.rule} {v.refdes}: {v.message}" for v in rep.violations
    )


# --- "known bad" cases for each rule ---

def test_r1_voltage_undervoltage_caught():
    """ESP32 needs 3.0-3.6V; connect to 2.5V rail → fail R1."""
    s = Schematic(
        title="undervoltage",
        target_use="test",
        rails=[_rail("2V5", 2.5, 500.0), _rail("GND", 0.0, 1000.0)],
        components=[
            _ic("esp32_01", "U1", "2V5", 2.5, "3.3V", {
                "VIN": ("2V5", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),
        ],
    )
    rep = check_erc(s, IRRIGATION_BUNDLE)
    assert not rep.passed
    assert any(v.rule == "R1_voltage_range" and "below spec min" in v.message for v in rep.errors())


def test_r1_vcc_unconnected_caught():
    """IC has power_in pin but net doesn't match any rail."""
    s = Schematic(
        title="floating vcc",
        target_use="test",
        rails=[_rail("3V3", 3.3, 500.0), _rail("GND", 0.0, 1000.0)],
        components=[
            _ic("esp32_01", "U1", "FLOAT", 3.3, "3.3V", {
                "VIN": ("FLOAT", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),
        ],
    )
    rep = check_erc(s, IRRIGATION_BUNDLE)
    assert any(v.rule == "R1_voltage_range" and "not connected to any power rail" in v.message for v in rep.errors())


def test_r2_shorted_rails_caught():
    """Two rails with same name but different voltages = short circuit (data integrity)."""
    s = Schematic(
        title="short",
        target_use="test",
        rails=[
            _rail("5V", 5.0, 500.0),
            _rail("3V3", 3.3, 500.0),
            _rail("GND", 0.0, 1000.0),
            # Two rails both named "shared" with conflicting voltages — data error
            _rail("shared", 3.3, 500.0),
            _rail("shared", 5.0, 500.0),
        ],
        components=[
            _ic("esp32_01", "U1", "shared", 3.3, "3.3V", {
                "VIN": ("shared", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),
        ],
    )
    rep = check_erc(s, IRRIGATION_BUNDLE)
    assert any(v.rule == "R2_no_shorted_rails" for v in rep.errors()), \
        f"expected R2 error; got: {[v.message for v in rep.errors()]}"


def test_r3_logic_level_mismatch_caught():
    """3.3V GPIO directly drives 5V input — should require level shifter."""
    s = Schematic(
        title="level mismatch",
        target_use="test",
        rails=[_rail("3V3", 3.3, 500.0), _rail("5V", 5.0, 500.0), _rail("GND", 0.0, 1000.0)],
        components=[
            _ic("esp32_01", "U1", "3V3", 3.3, "3.3V", {
                "GPIO5": ("net_relay", PinKind.DIGITAL_OUT),
                "VIN": ("3V3", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),
            # Direct connection, NO level shifter
            _ic("relay_5v_01", "K1", "5V", 5.0, "5V", {
                "IN": ("net_relay", PinKind.DIGITAL_IN),
                "VCC": ("5V", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),
        ],
    )
    rep = check_erc(s, IRRIGATION_BUNDLE)
    assert any(v.rule == "R3_logic_level" for v in rep.errors())


def test_r4_current_overload_caught():
    """Sum of part currents exceeds PSU."""
    s = Schematic(
        title="overload",
        target_use="test",
        rails=[_rail("3V3", 3.3, 50.0), _rail("GND", 0.0, 1000.0)],  # only 50mA
        components=[
            _ic("esp32_01", "U1", "3V3", 3.3, "3.3V", {
                "VIN": ("3V3", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),  # ESP32 alone draws 80mA > 50mA
        ],
    )
    rep = check_erc(s, IRRIGATION_BUNDLE)
    assert any(v.rule == "R4_current_budget" and "undersized" in v.message for v in rep.errors())


def test_r5_floating_input_caught():
    """Input pin on a net with no driver and no pull resistor."""
    s = Schematic(
        title="floating",
        target_use="test",
        rails=[_rail("3V3", 3.3, 500.0), _rail("GND", 0.0, 1000.0)],
        components=[
            _ic("esp32_01", "U1", "3V3", 3.3, "3.3V", {
                "GPIO4": ("net_orphan", PinKind.DIGITAL_IN),  # nothing else on this net
                "VIN": ("3V3", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),
        ],
    )
    rep = check_erc(s, IRRIGATION_BUNDLE)
    assert any(v.rule == "R5_pull_required" for v in rep.errors())


def test_r5_pull_resistor_satisfies():
    """A resistor pulling to a rail satisfies the pull requirement."""
    s = Schematic(
        title="pulled",
        target_use="test",
        rails=[_rail("3V3", 3.3, 500.0), _rail("GND", 0.0, 1000.0)],
        components=[
            _ic("esp32_01", "U1", "3V3", 3.3, "3.3V", {
                "GPIO4": ("net_pulled", PinKind.DIGITAL_IN),
                "VIN": ("3V3", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),
            ComponentRef(part_id="r_pull", refdes="R1", kind="resistor", value="10k",
                         pins=[
                             Pin(name="1", net="net_pulled", kind=PinKind.DIGITAL_IN, drive_voltage=None),
                             Pin(name="2", net="3V3", kind=PinKind.POWER_OUT, drive_voltage=None),
                         ]),
        ],
    )
    rep = check_erc(s, IRRIGATION_BUNDLE)
    assert not any(v.rule == "R5_pull_required" and v.severity == "error" for v in rep.violations)


def test_r6_missing_flyback_caught():
    """Relay without antiparallel diode."""
    s = Schematic(
        title="no flyback",
        target_use="test",
        rails=[_rail("5V", 5.0, 500.0), _rail("GND", 0.0, 1000.0)],
        components=[
            ComponentRef(part_id="relay_5v_01", refdes="K1", kind="relay", value="5V",
                         pins=[
                             Pin(name="a", net="net_coil", kind=PinKind.DIGITAL_IN, drive_voltage="5V"),
                             Pin(name="b", net="GND", kind=PinKind.GROUND),
                         ]),
        ],
    )
    rep = check_erc(s, IRRIGATION_BUNDLE)
    assert any(v.rule == "R6_flyback_diode" for v in rep.errors())


def test_r7_missing_decoupling_caught():
    """No capacitors anywhere → warning."""
    s = Schematic(
        title="no caps",
        target_use="test",
        rails=[_rail("3V3", 3.3, 500.0), _rail("GND", 0.0, 1000.0)],
        components=[
            _ic("esp32_01", "U1", "3V3", 3.3, "3.3V", {
                "VIN": ("3V3", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),
        ],
    )
    rep = check_erc(s, IRRIGATION_BUNDLE)
    assert any(v.rule == "R7_decoupling" for v in rep.warnings())