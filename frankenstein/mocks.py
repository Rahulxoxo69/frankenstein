"""Mock manifests — 5-10 hand-written PartsManifests covering every case
Member 2's engine needs to handle.

These let Member 2 build and test the Frankenstein engine without
Member 1's vision pipeline existing yet. Once Member 1's real output
matches the schema, swap these for the real file (one-line change).

Cases covered:
  1. clean functional (ESP32 from vault)
  2. clean functional (DHT22 from photo)
  3. low confidence → triggers RAG fallback (7805 regulator)
  4. repairable with concrete repair_note (bent relay pin)
  5. unsafe with disposal_reason (cracked pump housing — audit only)
  6. photo-source functional (LED indicator)
  7. vault-source functional (resistor pack)
  8. repairable with complex note (corroded battery contact)
"""

from __future__ import annotations

from frankenstein.schema import (
    ManifestBundle,
    PartSource,
    PartSpecs,
    PartsManifest,
    PartStatus,
    SpecSource,
)


# --- individual parts ---

ESP32 = PartsManifest(
    part_id="esp32_01",
    name="ESP32-WROOM-32",
    status=PartStatus.FUNCTIONAL,
    confidence=0.97,
    source=PartSource.VAULT,
    vault_entry_id="vault_esp32_wroom_001",
    specs=PartSpecs(
        voltage="3.3V",
        voltage_min=3.0,
        voltage_max=3.6,
        current_ma=80.0,  # avg; peak 500mA during Wi-Fi TX
        io_voltage="3.3V",
        package="SMD-module",
        pinout={
            "3V3": "module supply (3.3V)",
            "GPIO4": "DHT22 data",
            "GPIO34": "soil moisture ADC",
            "GPIO5": "relay IN1",
            "GPIO2": "status LED",
            "VIN": "5V input to onboard LDO",
            "GND": "common ground",
        },
        datasheet_url="https://www.espressif.com/sites/default/files/documentation/esp32-wroom-32_datasheet_en.pdf",
        alternate_names=["ESP32", "WROOM-32", "ESP32-WROOM"],
        source=SpecSource.NEXAR,
        confidence=0.95,
    ),
)

DHT22 = PartsManifest(
    part_id="dht22_01",
    name="DHT22",
    status=PartStatus.FUNCTIONAL,
    confidence=0.91,
    source=PartSource.PHOTO,
    photo_id="photo_intake_2026_06_20_01",
    specs=PartSpecs(
        voltage="5V",  # recommended; range 3.3-5.5V
        voltage_min=3.0,
        voltage_max=5.5,
        current_ma=2.5,
        io_voltage="3.3V",
        package="through-hole-4pin",
        pinout={"1": "VCC", "2": "DATA", "3": "NC", "4": "GND"},
        alternate_names=["AM2302", "RHT03"],
        source=SpecSource.DATASHEET,
        confidence=0.92,
    ),
)

SOIL_MOISTURE = PartsManifest(
    part_id="soil_moisture_01",
    name="Capacitive Soil Moisture Sensor v1.2",
    status=PartStatus.FUNCTIONAL,
    confidence=0.84,
    source=PartSource.PHOTO,
    photo_id="photo_intake_2026_06_20_01",
    specs=PartSpecs(
        voltage="3.3V",
        voltage_min=3.0,  # realistic — most 3.3V sensor modules work down to 3.0V
        voltage_max=5.5,
        current_ma=5.0,
        io_voltage="3.3V",
        package="PCB-probe",
        pinout={"VCC": "3.3V", "GND": "ground", "AOUT": "analog output 0-3V"},
        alternate_names=["capacitive moisture sensor", "soil hygrometer"],
        source=SpecSource.DATASHEET,
        confidence=0.80,
    ),
)

RELAY_5V = PartsManifest(
    part_id="relay_5v_01",
    name="5V Single-Channel Relay Module",
    status=PartStatus.REPAIRABLE,
    confidence=0.88,
    source=PartSource.PHOTO,
    photo_id="photo_intake_2026_06_20_01",
    repair_note=(
        "Pin 3 (IN) is bent at ~30 degrees from normal. Straighten with "
        "needle-nose pliers before insertion; verify continuity with a "
        "multimeter. Optocoupler side appears undamaged. Once straightened, "
        "the part is fully functional — design can assume post-repair state."
    ),
    specs=PartSpecs(
        voltage="5V",
        voltage_min=4.75,
        voltage_max=5.25,
        current_ma=70.0,
        io_voltage="5V",  # NOTE: this is the trap — 5V logic into a 3.3V GPIO needs level shifter
        package="PCB-module",
        pinout={"VCC": "5V", "GND": "ground", "IN": "logic input (active LOW)"},
        datasheet_url="https://www.handsontec.com/dataspecs/relay/5V-relay-module.pdf",
        source=SpecSource.INFERRED,
        confidence=0.70,  # low because spec wasn't datasheet-confirmed
    ),
)

PUMP_12V = PartsManifest(
    part_id="pump_12v_01",
    name="12V DC Submersible Water Pump",
    status=PartStatus.UNSAFE,
    confidence=0.93,
    source=PartSource.PHOTO,
    photo_id="photo_intake_2026_06_20_01",
    disposal_reason=(
        "Housing has visible crack near the cable gland. Even if motor "
        "functions, water ingress is likely. Do not reuse for irrigation — "
        "route to e-waste per India E-Waste Rules 2022 Schedule I cat. 4."
    ),
    specs=PartSpecs(
        voltage="12V",
        current_ma=400.0,
        source=SpecSource.INFERRED,
        confidence=0.60,
    ),
)

REGULATOR_7805 = PartsManifest(
    part_id="reg_7805_01",
    name="7805 Voltage Regulator (suspected)",
    status=PartStatus.FUNCTIONAL,
    confidence=0.52,  # BELOW 0.65 threshold → RAG fallback triggered in Member 1
    source=PartSource.PHOTO,
    photo_id="photo_intake_2026_06_20_01",
    specs=PartSpecs(
        voltage="5V",  # output voltage; voltage_min/max below refer to INPUT range
        voltage_min=7.0,
        voltage_max=35.0,
        current_ma=1000.0,
        package="TO-220",
        pinout={"1": "INPUT", "2": "GND", "3": "OUTPUT"},
        alternate_names=["L7805", "LM7805"],
        source=SpecSource.INFERRED,
        confidence=0.45,
    ),
)

LED_INDICATOR = PartsManifest(
    part_id="led_indicator_01",
    name="3mm Green LED",
    status=PartStatus.FUNCTIONAL,
    confidence=0.99,
    source=PartSource.VAULT,
    vault_entry_id="vault_led_3mm_green_042",
    specs=PartSpecs(
        voltage="2.1V Vf",
        current_ma=20.0,
        package="through-hole-3mm",
        pinout={"A": "anode", "K": "cathode"},
        alternate_names=["green LED", "L-7113GD"],
        source=SpecSource.MEASURED,
        confidence=0.98,
    ),
)

RESISTOR_PACK = PartsManifest(
    part_id="resistor_pack_01",
    name="1/4W Carbon Film Resistor Assortment",
    status=PartStatus.FUNCTIONAL,
    confidence=1.0,
    source=PartSource.VAULT,
    vault_entry_id="vault_resistors_1_4w_1206",
    specs=PartSpecs(
        package="through-hole-axial",
        dimensions_mm={"length": 6.3, "diameter": 2.3},
        source=SpecSource.MEASURED,
        confidence=1.0,
    ),
)

BATTERY_HOLDER = PartsManifest(
    part_id="battery_holder_01",
    name="18650 Single-Cell Battery Holder",
    status=PartStatus.REPAIRABLE,
    confidence=0.79,
    source=PartSource.PHOTO,
    photo_id="photo_intake_2026_06_20_02",
    repair_note=(
        "Negative contact spring is corroded (visible green-white crust). "
        "Clean with isopropyl alcohol + brass brush, then verify spring "
        "tension. If spring is fatigued (cell rattles loose), replace the "
        "holder. Until cleaned, the holder may give intermittent contact — "
        "Member 2 should assume the contact is unreliable and either design "
        "with a larger hold-down cap OR route power through the regulator "
        "side only after the repair."
    ),
    specs=PartSpecs(
        voltage="3.7V nominal",
        package="through-hole",
        pinout={"+": "positive", "-": "negative"},
        source=SpecSource.DATASHEET,
        confidence=0.85,
    ),
)


# --- supporting parts (level shifter, flyback diode, resistors, caps) ---
# These are what the Frankenstein engine actually designs WITH. Without them
# in the bundle, any realistic irrigation schematic gets flagged by Inspector.

LEVEL_SHIFTER = PartsManifest(
    part_id="lvl_shift_01",
    name="TXS0108E 8-bit Bidirectional Level Shifter",
    status=PartStatus.FUNCTIONAL,
    confidence=0.93,
    source=PartSource.VAULT,
    vault_entry_id="vault_txs0108e_001",
    specs=PartSpecs(
        voltage="1.4-5.5V",
        voltage_min=1.4,  # VCCA min per TI datasheet
        voltage_max=5.5,
        current_ma=0.008,  # 8µA quiescent
        io_voltage="1.4-5.5V",
        package="TSSOP-20",  # SOIC-20 variant does not exist
        pinout={
            "LV": "low-voltage reference",
            "HV": "high-voltage reference",
            "GND": "ground",
            "LV_IN": "low-voltage side input",
            "LV_OUT": "low-voltage side output",
            "HV_IN": "high-voltage side input",
            "HV_OUT": "high-voltage side output",
        },
        source=SpecSource.NEXAR,
        confidence=0.95,
    ),
)

FLYBACK_DIODE = PartsManifest(
    part_id="flyback_d1",
    name="1N4007 Rectifier Diode",
    status=PartStatus.FUNCTIONAL,
    confidence=0.99,
    source=PartSource.VAULT,
    vault_entry_id="vault_1n4007_001",
    specs=PartSpecs(
        voltage="1000V reverse",
        current_ma=1000.0,
        package="DO-41",
        pinout={"A": "anode", "K": "cathode"},
        alternate_names=["1N4007"],
        source=SpecSource.MEASURED,
        confidence=0.99,
    ),
)

RESISTOR_330 = PartsManifest(
    part_id="r_led_01",
    name="330Ω 1/4W Resistor",
    status=PartStatus.FUNCTIONAL,
    confidence=1.0,
    source=PartSource.VAULT,
    vault_entry_id="vault_r330_001",
    specs=PartSpecs(
        value="330Ω",
        package="through-hole-axial",
        source=SpecSource.MEASURED,
        confidence=1.0,
    ),
)

RESISTOR_10K = PartsManifest(
    part_id="r_pull_dht",
    name="10kΩ 1/4W Resistor",
    status=PartStatus.FUNCTIONAL,
    confidence=1.0,
    source=PartSource.VAULT,
    vault_entry_id="vault_r10k_001",
    specs=PartSpecs(
        value="10kΩ",
        package="through-hole-axial",
        source=SpecSource.MEASURED,
        confidence=1.0,
    ),
)

CAP_100NF = PartsManifest(
    part_id="cap_01",
    name="100nF Ceramic Capacitor",
    status=PartStatus.FUNCTIONAL,
    confidence=1.0,
    source=PartSource.VAULT,
    vault_entry_id="vault_cap100n_001",
    specs=PartSpecs(
        value="100nF",
        package="through-hole",
        source=SpecSource.MEASURED,
        confidence=1.0,
    ),
)

CAP_100NF_2 = PartsManifest(
    part_id="cap_02",
    name="100nF Ceramic Capacitor (spare)",
    status=PartStatus.FUNCTIONAL,
    confidence=1.0,
    source=PartSource.VAULT,
    vault_entry_id="vault_cap100n_002",
    specs=PartSpecs(
        value="100nF",
        package="through-hole",
        source=SpecSource.MEASURED,
        confidence=1.0,
    ),
)


# --- bundles ---

IRRIGATION_BUNDLE = ManifestBundle(
    bundle_id="bundle_irrigation_demo_2026_06_20",
    parts=[
        ESP32, DHT22, SOIL_MOISTURE, RELAY_5V, PUMP_12V, REGULATOR_7805,
        LED_INDICATOR, RESISTOR_PACK,
        LEVEL_SHIFTER, FLYBACK_DIODE, RESISTOR_330, RESISTOR_10K, CAP_100NF, CAP_100NF_2,
    ],
)

BATTERY_BUNDLE = ManifestBundle(
    bundle_id="bundle_battery_repair_test_01",
    parts=[ESP32, BATTERY_HOLDER, LED_INDICATOR, RESISTOR_PACK],
)


def all_mocks() -> list[PartsManifest]:
    """Flat list of every mock part, for tests that want to iterate everything."""
    return [
        ESP32,
        DHT22,
        SOIL_MOISTURE,
        RELAY_5V,
        PUMP_12V,
        REGULATOR_7805,
        LED_INDICATOR,
        RESISTOR_PACK,
        BATTERY_HOLDER,
        LEVEL_SHIFTER,
        FLYBACK_DIODE,
        RESISTOR_330,
        RESISTOR_10K,
        CAP_100NF,
        CAP_100NF_2,
    ]


def all_bundles() -> list[ManifestBundle]:
    return [IRRIGATION_BUNDLE, BATTERY_BUNDLE]