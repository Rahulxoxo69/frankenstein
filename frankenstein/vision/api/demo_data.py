"""
Demo Data — Pre-built Realistic Teardown Manifests
===================================================
Provides ready-to-go demo scenarios for hackathon presentations.
No images needed — just select a device and get instant results.
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from frankenstein.schema import (
    BoundingBox, BoardDefect, BoardDamage, DetectionInfo, PartsManifest,
    PartSource, PartSpecs, PartStatus, SpecSource,
    TeardownContext, TeardownManifest,
)


def _make_part(
    name, category, status, confidence, voltage=None, current=None,
    package=None, part_number=None, source=SpecSource.RAG,
    repair_note=None, disposal_reason=None, raw=None,
    yolo_class=None, yolo_conf=0.85,
):
    specs = PartSpecs(
        source=source,
        voltage=voltage,
        current_rating=current,
        package=package,
        part_number=part_number,
        raw=raw or {},
    ) 

    return PartsManifest(
        part_id=f"{category}_{uuid.uuid4().hex[:4]}",
        name=name,
        category=category,
        status=PartStatus(status),
        confidence=confidence,
        source=PartSource.PHOTO,
        specs=specs,
        repair_note=repair_note,
        disposal_reason=disposal_reason,
        detection=DetectionInfo(
            yolo_class=yolo_class or name,
            yolo_confidence=yolo_conf,
            bbox=BoundingBox(x_center=0.5, y_center=0.5, width=0.1, height=0.1),
        ),
        detected_at=datetime.now(timezone.utc),
    )


# ═══════════════════════════════════════════════════════════════
# SCENARIO 1: Broken Smartphone
# ═══════════════════════════════════════════════════════════════

def broken_smartphone() -> TeardownManifest:
    parts = [
        _make_part(
            "Qualcomm Snapdragon 778G", "processor", "functional", 0.94,
            voltage="0.75-1.05V", package="PoP BGA",
            part_number="SM7325", source=SpecSource.NEXAR,
            raw={"cores": "8 (Kryo 670)", "process": "6nm", "gpu": "Adreno 642L"},
            yolo_class="IC-Chip", yolo_conf=0.92,
        ),
        _make_part(
            "Samsung K3LK4K40BM-BGCP", "memory", "functional", 0.91,
            voltage="1.1V (LPDDR5)", package="PoP",
            part_number="K3LK4K40BM", source=SpecSource.NEXAR,
            raw={"capacity": "6GB LPDDR5", "bandwidth": "6400 Mbps"},
            yolo_class="IC-Chip", yolo_conf=0.89,
        ),
        _make_part(
            "AMOLED Display Assembly", "display", "unsafe", 0.97,
            disposal_reason="Screen shattered. LCD fluid may leak. Dispose as hazardous e-waste.",
            yolo_class="Display-Module", yolo_conf=0.96,
        ),
        _make_part(
            "Li-Po Battery 4500mAh", "battery", "repairable", 0.88,
            voltage="3.85V nominal", current="4500mAh",
            repair_note="Battery swollen (15% above normal thickness). Replace before reuse. Do NOT puncture.",
            raw={"chemistry": "Li-Polymer", "wh": "17.3Wh", "cycles": "~800"},
            yolo_class="Battery", yolo_conf=0.94,
        ),
        _make_part(
            "Qualcomm QTM535 mmWave Antenna", "antenna", "functional", 0.82,
            voltage="3.3V", package="Module",
            raw={"bands": "n257, n258, n260, n261", "type": "mmWave 5G"},
            yolo_class="Antenna-Module", yolo_conf=0.78,
        ),
        _make_part(
            "Sony IMX766 Camera Sensor", "camera", "functional", 0.93,
            voltage="2.8V", package="CSP",
            part_number="IMX766", source=SpecSource.NEXAR,
            raw={"resolution": "50MP", "sensor_size": "1/1.56\"", "pixel": "1.0μm"},
            yolo_class="Camera-Module", yolo_conf=0.91,
        ),
        _make_part(
            "Cirrus Logic CS35L45 Amplifier", "audio-ic", "functional", 0.86,
            voltage="3.6-5.5V", package="WLCSP",
            part_number="CS35L45", source=SpecSource.NEXAR,
            raw={"output_power": "6.2W", "thd": "0.01%", "class": "Class D Boosted"},
            yolo_class="IC-Chip", yolo_conf=0.83,
        ),
        _make_part(
            "USB-C Port (Type C 3.1)", "connector", "unsafe", 0.90,
            disposal_reason="Charging port bent and corroded. Water damage visible on pins.",
            yolo_class="Connector", yolo_conf=0.88,
        ),
        _make_part(
            "Murata 22μF MLCC Capacitor", "capacitor", "functional", 0.79,
            voltage="6.3V", package="0402 (1005M)",
            part_number="GRM155R60J226ME15", source=SpecSource.NEXAR,
            raw={"capacitance": "22μF", "tolerance": "±20%", "dielectric": "X5R"},
            yolo_class="Capacitor", yolo_conf=0.76,
        ),
        _make_part(
            "Vibration Motor (LRA)", "motor", "functional", 0.84,
            voltage="1.8V", current="75mA",
            raw={"type": "Linear Resonant Actuator", "frequency": "175Hz"},
            yolo_class="Motor", yolo_conf=0.81,
        ),
    ]

    damages = [
        BoardDefect(defect_type="crack", confidence=0.95,
                    bbox=BoundingBox(x_center=0.45, y_center=0.3, width=0.4, height=0.2),
                    affects_part="display"),
        BoardDefect(defect_type="corrosion", confidence=0.87,
                    bbox=BoundingBox(x_center=0.7, y_center=0.8, width=0.1, height=0.05),
                    affects_part="connector"),
    ]

    return TeardownManifest(
        teardown_id=f"demo_phone_{uuid.uuid4().hex[:6]}",
        parts=parts,
        board_damages=damages,
        context=TeardownContext(
            device_model="Samsung Galaxy S22 (SM-S901B)",
            failure_cause="Dropped — cracked screen, charging port water damage",
            available_tools=["soldering_iron", "heat_gun", "multimeter", "spudger"],
            skill_level=4,
        ),
        image_paths=["demo_phone_front.jpg", "demo_phone_pcb.jpg"],
        created_at=datetime.now(timezone.utc),
    )


# ═══════════════════════════════════════════════════════════════
# SCENARIO 2: Coffee Machine
# ═══════════════════════════════════════════════════════════════

def coffee_machine() -> TeardownManifest:
    parts = [
        _make_part(
            "STM32F103C8T6 MCU", "microcontroller", "functional", 0.92,
            voltage="3.3V", package="LQFP-48",
            part_number="STM32F103C8T6", source=SpecSource.NEXAR,
            raw={"core": "ARM Cortex-M3", "flash": "64KB", "ram": "20KB", "clock": "72MHz"},
            yolo_class="IC-Chip", yolo_conf=0.90,
        ),
        _make_part(
            "Heating Element NTC Thermistor", "sensor", "functional", 0.88,
            voltage="5V (sense)", raw={"type": "NTC 10K", "beta": "3950K", "range": "-40 to 125°C"},
            yolo_class="Thermistor", yolo_conf=0.85,
        ),
        _make_part(
            "Solenoid Valve (Water Flow)", "valve", "repairable", 0.83,
            voltage="12V DC", current="350mA",
            repair_note="Calcium buildup on valve seat. Soak in vinegar solution 2h, then test flow rate.",
            raw={"type": "Normally Closed", "pressure": "0-0.8MPa", "orifice": "2.5mm"},
            yolo_class="Solenoid", yolo_conf=0.80,
        ),
        _make_part(
            "Water Pump (Ulka EP5)", "pump", "functional", 0.91,
            voltage="230V AC", current="1.1A",
            part_number="EP5", source=SpecSource.NEXAR,
            raw={"pressure": "15 bar", "flow": "280ml/min", "type": "Vibratory"},
            yolo_class="Motor", yolo_conf=0.88,
        ),
        _make_part(
            "Nichrome Heating Coil", "heater", "repairable", 0.76,
            voltage="230V AC", current="5.2A",
            repair_note="One coil segment shows increased resistance (+15%). Functional but may heat unevenly. Monitor temperature with thermal probe.",
            raw={"power": "1200W", "resistance": "44Ω", "material": "Ni80Cr20"},
            yolo_class="Resistor", yolo_conf=0.72,
        ),
        _make_part(
            "OLED Display 128x64 (SSD1306)", "display", "functional", 0.89,
            voltage="3.3V", package="Module",
            part_number="SSD1306", source=SpecSource.NEXAR,
            raw={"resolution": "128x64", "interface": "I2C", "size": "0.96 inch"},
            yolo_class="Display-Module", yolo_conf=0.86,
        ),
        _make_part(
            "Relay Module (SRD-05VDC)", "relay", "functional", 0.87,
            voltage="5V coil / 250V AC switch", current="10A",
            part_number="SRD-05VDC-SL-C", source=SpecSource.NEXAR,
            raw={"type": "SPDT", "coil_power": "0.36W", "contact_rating": "10A 250VAC"},
            yolo_class="Relay", yolo_conf=0.84,
        ),
        _make_part(
            "Electrolytic Capacitor 1000μF", "capacitor", "unsafe", 0.93,
            voltage="25V", disposal_reason="Capacitor bulging — electrolyte leaking. HAZARDOUS. Discharge fully before handling.",
            raw={"capacitance": "1000μF", "esr": "degraded"},
            yolo_class="Capacitor", yolo_conf=0.91,
        ),
        _make_part(
            "5V 2A Switching Regulator", "power-supply", "functional", 0.85,
            voltage="5V out / 230V in", current="2A",
            raw={"topology": "Flyback", "efficiency": "85%", "isolation": "3kV"},
            yolo_class="Voltage-Regulator", yolo_conf=0.82,
        ),
        _make_part(
            "Rotary Encoder (Menu Control)", "encoder", "functional", 0.81,
            voltage="3.3V",
            raw={"type": "Incremental", "pulses": "20 per revolution", "switch": "built-in push"},
            yolo_class="Potentiometer", yolo_conf=0.77,
        ),
    ]

    damages = [
        BoardDefect(defect_type="corrosion", confidence=0.89,
                    bbox=BoundingBox(x_center=0.6, y_center=0.5, width=0.15, height=0.08),
                    affects_part="capacitor"),
        BoardDefect(defect_type="spur", confidence=0.72,
                    bbox=BoundingBox(x_center=0.3, y_center=0.7, width=0.05, height=0.03)),
    ]

    return TeardownManifest(
        teardown_id=f"demo_coffee_{uuid.uuid4().hex[:6]}",
        parts=parts,
        board_damages=damages,
        context=TeardownContext(
            device_model="Breville Barista Express (BES870)",
            failure_cause="Intermittent heating, display flickering, descale warning won't clear",
            available_tools=["multimeter", "soldering_iron", "oscilloscope"],
            skill_level=3,
        ),
        image_paths=["demo_coffee_board.jpg", "demo_coffee_internals.jpg"],
        created_at=datetime.now(timezone.utc),
    )


# ═══════════════════════════════════════════════════════════════
# SCENARIO 3: Laptop Motherboard
# ═══════════════════════════════════════════════════════════════

def laptop_motherboard() -> TeardownManifest:
    parts = [
        _make_part(
            "Intel Core i7-1165G7", "processor", "functional", 0.96,
            voltage="0.6-1.4V", package="BGA-1449",
            part_number="SRK01", source=SpecSource.NEXAR,
            raw={"cores": "4C/8T", "tdp": "12-28W", "process": "10nm SuperFin", "boost": "4.7GHz"},
            yolo_class="IC-Chip", yolo_conf=0.94,
        ),
        _make_part(
            "SK Hynix H9HCNNNCPUMLHR 8GB", "memory", "functional", 0.90,
            voltage="1.1V (LPDDR4X)", package="PoP BGA",
            part_number="H9HCNNNCPUML", source=SpecSource.NEXAR,
            raw={"capacity": "8GB LPDDR4X", "speed": "4267MHz", "channels": "dual"},
            yolo_class="IC-Chip", yolo_conf=0.87,
        ),
        _make_part(
            "Intel AX201 WiFi 6 Module", "wireless", "functional", 0.88,
            voltage="3.3V", package="M.2 2230",
            part_number="AX201NGW", source=SpecSource.NEXAR,
            raw={"wifi": "802.11ax (Wi-Fi 6)", "bluetooth": "5.0", "speed": "2.4 Gbps"},
            yolo_class="Wireless-Module", yolo_conf=0.85,
        ),
        _make_part(
            "Realtek ALC295 Audio Codec", "audio-ic", "functional", 0.84,
            voltage="3.3V", package="QFN-48",
            part_number="ALC295", source=SpecSource.NEXAR,
            raw={"dac": "24-bit/192kHz", "channels": "2.1", "snr": "97dB"},
            yolo_class="IC-Chip", yolo_conf=0.81,
        ),
        _make_part(
            "TPS51397A Voltage Regulator", "power-ic", "unsafe", 0.92,
            voltage="5-25V in / 0.6-1.5V out", package="QFN-20",
            disposal_reason="VRM burnt. Scorch marks visible. Likely short-circuited. May have damaged nearby components.",
            yolo_class="Voltage-Regulator", yolo_conf=0.90,
        ),
        _make_part(
            "Thunderbolt 4 Controller (JHL8540)", "controller", "functional", 0.87,
            voltage="1.0V / 3.3V", package="BGA",
            part_number="JHL8540", source=SpecSource.NEXAR,
            raw={"bandwidth": "40 Gbps", "dp": "DisplayPort 1.4", "usb": "USB4"},
            yolo_class="IC-Chip", yolo_conf=0.84,
        ),
        _make_part(
            "CMOS Battery CR2032", "battery", "repairable", 0.79,
            voltage="3.0V", current="225mAh",
            repair_note="Battery depleted (reads 2.1V). Replace with fresh CR2032 to restore BIOS settings.",
            raw={"chemistry": "Lithium MnO2", "shelf_life": "10 years"},
            yolo_class="Battery", yolo_conf=0.75,
        ),
        _make_part(
            "DC Power Jack", "connector", "repairable", 0.85,
            voltage="19.5V", current="3.25A",
            repair_note="Loose solder joints on barrel jack. Reflow with leaded solder at 350°C. Test with load.",
            raw={"barrel_size": "4.5mm x 3.0mm", "power": "65W"},
            yolo_class="Connector", yolo_conf=0.82,
        ),
        _make_part(
            "Tantalum Capacitor 100μF", "capacitor", "functional", 0.83,
            voltage="16V", package="Case D (7343)",
            raw={"capacitance": "100μF", "esr": "0.1Ω", "type": "Tantalum Polymer"},
            yolo_class="Tantalum-Capacitor", yolo_conf=0.80,
        ),
    ]

    damages = [
        BoardDefect(defect_type="short", confidence=0.93,
                    bbox=BoundingBox(x_center=0.4, y_center=0.35, width=0.08, height=0.06),
                    affects_part="power-ic"),
        BoardDefect(defect_type="missing_hole", confidence=0.78,
                    bbox=BoundingBox(x_center=0.8, y_center=0.9, width=0.04, height=0.04)),
    ]

    return TeardownManifest(
        teardown_id=f"demo_laptop_{uuid.uuid4().hex[:6]}",
        parts=parts,
        board_damages=damages,
        context=TeardownContext(
            device_model="Dell XPS 13 9310 Motherboard",
            failure_cause="No power on — burnt smell near VRM area, CMOS battery dead",
            available_tools=["multimeter", "soldering_iron", "heat_gun", "oscilloscope", "thermal_camera"],
            skill_level=5,
        ),
        image_paths=["demo_laptop_top.jpg", "demo_laptop_bottom.jpg"],
        created_at=datetime.now(timezone.utc),
    )


# ═══════════════════════════════════════════════════════════════
# SCENARIO 4: Drone Controller
# ═══════════════════════════════════════════════════════════════

def drone_controller() -> TeardownManifest:
    parts = [
        _make_part(
            "STM32F405RGT6 Flight Controller", "microcontroller", "functional", 0.93,
            voltage="3.3V", package="LQFP-64",
            part_number="STM32F405RGT6", source=SpecSource.NEXAR,
            raw={"core": "Cortex-M4F @ 168MHz", "flash": "1MB", "ram": "192KB", "fpu": "yes"},
            yolo_class="IC-Chip", yolo_conf=0.91,
        ),
        _make_part(
            "InvenSense ICM-42688-P IMU", "sensor", "functional", 0.90,
            voltage="1.8V", package="LGA-14",
            part_number="ICM-42688-P", source=SpecSource.NEXAR,
            raw={"gyro": "±2000°/s", "accel": "±16g", "odr": "32kHz", "noise": "ultra-low"},
            yolo_class="IC-Chip", yolo_conf=0.88,
        ),
        _make_part(
            "BLHeli_32 ESC (35A)", "esc", "repairable", 0.82,
            voltage="3-6S LiPo (11.1-25.2V)", current="35A continuous / 45A burst",
            repair_note="One MOSFET (Q3) shows elevated temperature. Replace Q3 and test under load. Check for desoldered motor pad.",
            raw={"protocol": "DShot1200", "mosfets": "4x IRFH5015"},
            yolo_class="Motor-Driver", yolo_conf=0.79,
        ),
        _make_part(
            "XT60 Power Connector", "connector", "functional", 0.89,
            voltage="60V max", current="60A continuous",
            raw={"type": "XT60 Male", "contact_resistance": "<1mΩ"},
            yolo_class="Connector", yolo_conf=0.87,
        ),
        _make_part(
            "TBS Crossfire Nano RX", "receiver", "functional", 0.91,
            voltage="5V", current="100mA",
            raw={"frequency": "868/915MHz", "range": "40km+", "protocol": "CRSF", "antenna": "T-type"},
            yolo_class="Wireless-Module", yolo_conf=0.88,
        ),
        _make_part(
            "6S 1300mAh LiPo Battery", "battery", "unsafe", 0.95,
            disposal_reason="LiPo puffy — 3mm swelling on cell 4. FIRE HAZARD. Discharge to 0V via resistor, dispose in LiPo-safe bag at certified facility.",
            yolo_class="Battery", yolo_conf=0.93,
        ),
        _make_part(
            "BMP280 Barometric Sensor", "sensor", "functional", 0.85,
            voltage="1.8-3.6V", package="LGA-8",
            part_number="BMP280", source=SpecSource.NEXAR,
            raw={"pressure_range": "300-1100 hPa", "accuracy": "±1 hPa", "altitude_resolution": "0.1m"},
            yolo_class="IC-Chip", yolo_conf=0.82,
        ),
        _make_part(
            "Caddx Nebula Nano Camera", "camera", "repairable", 0.78,
            voltage="3.3-5.5V",
            repair_note="FPV ribbon cable torn. Replace 20-pin FFC cable (0.5mm pitch, 30mm length). Lens intact.",
            raw={"resolution": "720p60", "fov": "150°", "sensor": "1/3\" CMOS", "latency": "4ms"},
            yolo_class="Camera-Module", yolo_conf=0.75,
        ),
    ]

    damages = [
        BoardDefect(defect_type="spur", confidence=0.81,
                    bbox=BoundingBox(x_center=0.55, y_center=0.4, width=0.06, height=0.04),
                    affects_part="esc"),
    ]

    return TeardownManifest(
        teardown_id=f"demo_drone_{uuid.uuid4().hex[:6]}",
        parts=parts,
        board_damages=damages,
        context=TeardownContext(
            device_model="Custom 5\" FPV Racing Drone (F405 Stack)",
            failure_cause="Crash landing — ESC smoking, battery puffed, camera cable torn",
            available_tools=["soldering_iron", "multimeter", "heat_gun", "smoke_stopper"],
            skill_level=4,
        ),
        image_paths=["demo_drone_stack.jpg", "demo_drone_damage.jpg"],
        created_at=datetime.now(timezone.utc),
    )


# ── Registry ─────────────────────────────────────────────────

DEMO_SCENARIOS = {
    "phone": {
        "name": "Broken Smartphone",
        "icon": "📱",
        "desc": "Samsung Galaxy S22 — cracked screen, water-damaged USB-C port",
        "fn": broken_smartphone,
    },
    "coffee": {
        "name": "Coffee Machine",
        "icon": "☕",
        "desc": "Breville Barista Express — intermittent heating, leaking capacitor",
        "fn": coffee_machine,
    },
    "laptop": {
        "name": "Laptop Motherboard",
        "icon": "💻",
        "desc": "Dell XPS 13 — burnt VRM, dead CMOS battery, loose power jack",
        "fn": laptop_motherboard,
    },
    "drone": {
        "name": "FPV Racing Drone",
        "icon": "🚁",
        "desc": "5\" FPV quad — smoking ESC, puffy LiPo, torn camera cable",
        "fn": drone_controller,
    },
}
