"""Foreman / engine tests — end-to-end reflexion loop with StubLLM."""

import pytest

from frankenstein.foreman import build_graph, set_llm_override, get_llm_override
from frankenstein.llm import StubLLM
from frankenstein.mocks import IRRIGATION_BUNDLE
from frankenstein.schematic import ComponentRef, Pin, PinKind, PowerRail, Schematic


def _good_irrigation_schematic() -> Schematic:
    """Schematic that should pass ERC."""
    def _ic(part_id, refdes, pin_map):
        pins = [Pin(name=p, net=net, kind=kind,
                    drive_voltage="3.3V" if "3V3" in (net or "") else ("5V" if net == "5V" else None))
                for p, (net, kind) in pin_map.items()]
        return ComponentRef(part_id=part_id, refdes=refdes, kind="ic", value=None, pins=pins)

    return Schematic(
        title="Irrigation controller",
        target_use="auto-irrigation",
        rails=[
            PowerRail(name="12V", voltage=12.0, current_ma=2000.0),  # sized for 5V@1A load (60% eff)
            PowerRail(name="5V", voltage=5.0, current_ma=2000.0),
            PowerRail(name="3V3", voltage=3.3, current_ma=1000.0),
            PowerRail(name="GND", voltage=0.0, current_ma=5000.0),
        ],
        components=[
            _ic("esp32_01", "U1", {
                "3V3": ("3V3", PinKind.POWER_IN),
                "GPIO4": ("net_dht", PinKind.DIGITAL_IN),
                "GPIO34": ("net_soil", PinKind.ANALOG_IN),
                "GPIO5": ("net_shifter_in", PinKind.DIGITAL_OUT),
                "GPIO2": ("net_led", PinKind.DIGITAL_OUT),
                "VIN": ("5V", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),
            _ic("dht22_01", "U2", {
                "DATA": ("net_dht", PinKind.BIDIRECTIONAL),
                "VCC": ("3V3", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),
            _ic("soil_moisture_01", "U3", {
                "AOUT": ("net_soil", PinKind.ANALOG_OUT),
                "VCC": ("3V3", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),
            _ic("reg_7805_01", "U4", {
                "INPUT": ("12V", PinKind.POWER_IN),
                "OUTPUT": ("5V", PinKind.POWER_OUT),
                "GND": ("GND", PinKind.GROUND),
            }),
            _ic("lvl_shift_01", "U5", {
                "LV_IN": ("net_shifter_in", PinKind.DIGITAL_IN),
                "LV_OUT": ("net_shifter_lv", PinKind.DIGITAL_OUT),
                "HV_IN": ("net_shifter_hv", PinKind.DIGITAL_OUT),
                "HV_OUT": ("net_relay_in", PinKind.DIGITAL_OUT),
                "LV": ("3V3", PinKind.POWER_IN),
                "HV": ("5V", PinKind.POWER_IN),
                "GND": ("GND", PinKind.GROUND),
            }),
            ComponentRef(part_id="relay_5v_01", refdes="K1", kind="relay", value="5V coil",
                         pins=[Pin(name="coil_a", net="net_relay_in", kind=PinKind.DIGITAL_IN),
                               Pin(name="coil_b", net="GND", kind=PinKind.GROUND)]),
            ComponentRef(part_id="flyback_d1", refdes="D1", kind="diode", value="1N4007",
                         pins=[Pin(name="A", net="GND", kind=PinKind.POWER_IN),
                               Pin(name="K", net="net_relay_in", kind=PinKind.POWER_OUT)]),
            ComponentRef(part_id="led_indicator_01", refdes="D2", kind="led", value="green",
                         pins=[Pin(name="A", net="net_led_res", kind=PinKind.POWER_IN),
                               Pin(name="K", net="GND", kind=PinKind.GROUND)]),
            ComponentRef(part_id="r_led_01", refdes="R1", kind="resistor", value="330Ω",
                         pins=[Pin(name="1", net="net_led", kind=PinKind.DIGITAL_IN),
                               Pin(name="2", net="net_led_res", kind=PinKind.POWER_OUT)]),
            ComponentRef(part_id="r_pull_dht", refdes="R2", kind="resistor", value="10kΩ",
                         pins=[Pin(name="1", net="net_dht", kind=PinKind.DIGITAL_IN),
                               Pin(name="2", net="3V3", kind=PinKind.POWER_OUT)]),
            ComponentRef(part_id="cap_01", refdes="C1", kind="capacitor", value="100nF",
                         pins=[Pin(name="1", net="3V3", kind=PinKind.POWER_IN),
                               Pin(name="2", net="GND", kind=PinKind.GROUND)]),
            ComponentRef(part_id="cap_02", refdes="C2", kind="capacitor", value="100nF",
                         pins=[Pin(name="1", net="5V", kind=PinKind.POWER_IN),
                               Pin(name="2", net="GND", kind=PinKind.GROUND)]),
        ],
        notes=[
            "esp32_01: ASSUME_REPAIR — vault-sourced, fully functional.",
            "relay_5v_01: ASSUME_REPAIR — bent pin straightened before assembly.",
        ],
    )


def _bad_first_attempt_schematic() -> Schematic:
    """Missing flyback diode — should fail ERC R6."""
    sch = _good_irrigation_schematic()
    sch.components = [c for c in sch.components if not (c.kind == "diode" and c.refdes == "D1")]
    sch.notes = ["first attempt: forgot the flyback, will add on retry"]
    return sch


def test_foreman_graph_compiles():
    """LangGraph actually assembles (slow but real)."""
    from frankenstein.foreman import build_graph
    g = build_graph()
    assert g is not None


def test_foreman_clean_pass_first_try():
    """Stub returns good schematic immediately → status=done, 1 attempt."""
    from frankenstein.engine import run
    stub = StubLLM()
    stub.queue_response(_good_irrigation_schematic())
    set_llm_override(stub)
    try:
        result = run(IRRIGATION_BUNDLE, "auto-irrigation", max_attempts=3)
    finally:
        set_llm_override(None)

    assert result.status == "done", f"log:\n" + "\n".join(result.log)
    assert result.attempts == 1
    assert result.buildability_score > 70
    assert result.robustness_confidence > 0


def test_foreman_reflexion_loop_after_erc_fail():
    """Bad (no flyback) on attempt 1, good on attempt 2 → reflexion loop works."""
    from frankenstein.engine import run
    stub = StubLLM()
    stub.queue_response(_bad_first_attempt_schematic())
    stub.queue_response(_good_irrigation_schematic())
    set_llm_override(stub)
    try:
        result = run(IRRIGATION_BUNDLE, "auto-irrigation", max_attempts=3)
    finally:
        set_llm_override(None)

    assert result.status == "done", f"expected done; got {result.status}; log:\n" + "\n".join(result.log)
    assert result.attempts == 2, f"expected 2 attempts; got {result.attempts}"
    assert len(stub.calls) == 2, f"expected 2 LLM calls; got {len(stub.calls)}"

    # Second call must include the reflexion feedback
    second_user_prompt = stub.calls[1][1]
    assert "R6" in second_user_prompt or "flyback" in second_user_prompt.lower(), \
        f"second attempt should have received flyback feedback; got: {second_user_prompt[:200]}"


def test_foreman_fails_after_max_attempts():
    """If every attempt produces a failing ERC, status=failed."""
    from frankenstein.engine import run
    stub = StubLLM()
    for _ in range(5):
        stub.queue_response(_bad_first_attempt_schematic())
    set_llm_override(stub)
    try:
        result = run(IRRIGATION_BUNDLE, "auto-irrigation", max_attempts=2)
    finally:
        set_llm_override(None)

    assert result.status == "failed", f"expected failed; got {result.status}"
    assert result.attempts == 2
    erc_logs = [l for l in result.log if "ERC:" in l]
    assert len(erc_logs) == 2


def test_engine_result_has_required_fields():
    """EngineResult contract: status, attempts, schematic, scores, log."""
    from frankenstein.engine import run
    stub = StubLLM()
    stub.queue_response(_good_irrigation_schematic())
    set_llm_override(stub)
    try:
        result = run(IRRIGATION_BUNDLE, "auto-irrigation", max_attempts=3)
    finally:
        set_llm_override(None)

    assert hasattr(result, "status")
    assert hasattr(result, "attempts")
    assert hasattr(result, "schematic")
    assert hasattr(result, "buildability_score")
    assert hasattr(result, "robustness_confidence")
    assert hasattr(result, "log")
    assert isinstance(result.log, list)


def test_foreman_audit_log_records_each_step():
    """The log is the audit trail — proves what the graph actually did."""
    from frankenstein.engine import run
    stub = StubLLM()
    stub.queue_response(_good_irrigation_schematic())
    set_llm_override(stub)
    try:
        result = run(IRRIGATION_BUNDLE, "auto-irrigation", max_attempts=3)
    finally:
        set_llm_override(None)

    log_text = "\n".join(result.log)
    assert "design_circuit" in log_text
    assert "ERC:" in log_text
    assert "Inspector:" in log_text
    assert "firmware generated" in log_text
    assert "compile:" in log_text
    assert "FINAL:" in log_text


@pytest.fixture(autouse=True)
def _reset_llm_override():
    """Make sure no test leaks LLM override state to the next test."""
    yield
    set_llm_override(None)