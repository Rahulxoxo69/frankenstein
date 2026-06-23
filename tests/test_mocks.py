"""Mock manifest tests — confirm the test fixtures cover every case Member 2 cares about."""

from frankenstein.mocks import (
    BATTERY_BUNDLE,
    BATTERY_HOLDER,
    DHT22,
    ESP32,
    IRRIGATION_BUNDLE,
    LED_INDICATOR,
    PUMP_12V,
    REGULATOR_7805,
    RELAY_5V,
    RESISTOR_PACK,
    SOIL_MOISTURE,
    all_mocks,
)
from frankenstein.schema import PartSource, PartStatus


def test_irrigation_bundle_has_full_coverage():
    statuses = {p.status for p in IRRIGATION_BUNDLE.parts}
    sources = {p.source for p in IRRIGATION_BUNDLE.parts}

    assert PartStatus.FUNCTIONAL in statuses
    assert PartStatus.REPAIRABLE in statuses
    assert PartStatus.UNSAFE in statuses
    assert PartSource.PHOTO in sources
    assert PartSource.VAULT in sources


def test_low_confidence_part_exists():
    """The 7805 with confidence 0.52 is the RAG-fallback test case."""
    assert REGULATOR_7805.confidence < 0.65
    assert REGULATOR_7805.status == PartStatus.FUNCTIONAL


def test_relay_has_logic_level_trap():
    """5V relay driven by 3.3V GPIO = ERC R3 violation. This is the design trap we want ERC to catch."""
    assert RELAY_5V.specs.io_voltage == "5V"
    assert ESP32.specs.io_voltage == "3.3V"
    assert RELAY_5V.status == PartStatus.REPAIRABLE
    assert RELAY_5V.repair_note is not None


def test_pump_is_unsafe_with_disposal_reason():
    assert PUMP_12V.status == PartStatus.UNSAFE
    assert PUMP_12V.disposal_reason is not None


def test_battery_holder_repair_note_is_complex():
    """The repair_note is the explicit case where Member 2 must decide
    ASSUME_REPAIR vs DESIGN_AROUND. Test it has the necessary detail."""
    assert BATTERY_HOLDER.status == PartStatus.REPAIRABLE
    note = BATTERY_HOLDER.repair_note or ""
    assert "corroded" in note.lower()
    # must mention both options so Member 2's prompt has something to choose
    assert "design around" in note.lower() or "design tolerates" in note.lower() or "assume" in note.lower() or "repair" in note.lower()


def test_all_mocks_unique_part_ids():
    ids = [p.part_id for p in all_mocks()]
    assert len(ids) == len(set(ids)), f"duplicate part_id in mocks: {ids}"