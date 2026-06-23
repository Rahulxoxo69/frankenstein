"""
RAG Fallback (Semantic Search over Component Knowledge)
======================================================
Step 3 of the Grounding Cascade: OCR → Nexar → **RAG Fallback**

When OCR finds nothing and Nexar has no match, we fall back to
semantic similarity search over a local knowledge base using
sentence-transformers embeddings.

For the hackathon, the "knowledge base" is a curated JSON of common
component specs indexed by category/description. In production, this
would be backed by pgvector over parsed datasheets.

Uses: all-MiniLM-L6-v2 (384-dim embeddings, MIT license, ~90MB)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
    SBERT_AVAILABLE = True
except ImportError:
    SBERT_AVAILABLE = False


# ── RAG Result ───────────────────────────────────────────────────────────────

@dataclass
class RAGResult:
    """Result of semantic search fallback."""
    query: str
    matched_name: Optional[str] = None
    matched_category: Optional[str] = None
    similarity: float = 0.0
    specs: dict[str, str] = field(default_factory=dict)
    found: bool = False
    source: str = "rag"
    description: str = ""
    reuse_suggestion: str = ""


# ── Component Knowledge Base ────────────────────────────────────────────────

# Curated knowledge base of common electronic components
# In production: replaced by pgvector over parsed datasheets
COMPONENT_KNOWLEDGE = [
    {
        "name": "Electrolytic Capacitor",
        "category": "capacitor",
        "description": "Polarized capacitor used for power supply filtering and decoupling",
        "specs": {"type": "electrolytic", "polarity": "polarized", "typical_voltage": "6.3V-450V", "typical_capacitance": "1uF-10000uF"},
        "keywords": "electrolytic capacitor cylindrical aluminum can stripe negative polarity",
        "reuse_suggestion": "Excellent for power supply recapping, audio amplifier filters, or smoothing capacitors in DC-DC converter builds.",
    },
    {
        "name": "Ceramic Capacitor",
        "category": "capacitor",
        "description": "Non-polarized capacitor for high-frequency bypass and coupling",
        "specs": {"type": "ceramic", "polarity": "non-polarized", "typical_voltage": "6.3V-100V", "typical_capacitance": "1pF-10uF"},
        "keywords": "ceramic disc capacitor small flat non-polarized MLCC",
        "reuse_suggestion": "Perfect for decoupling IC power pins, RF bypass circuits, or RC timing networks in microcontroller projects.",
    },
    {
        "name": "Film Capacitor",
        "category": "capacitor",
        "description": "Non-polarized capacitor with plastic film dielectric for precision applications",
        "specs": {"type": "film", "polarity": "non-polarized", "typical_voltage": "50V-1000V", "typical_capacitance": "1nF-10uF"},
        "keywords": "film capacitor box shaped rectangular polyester polypropylene",
    },
    {
        "name": "Tantalum Capacitor",
        "category": "capacitor",
        "description": "Polarized solid-state capacitor with tantalum anode, compact and reliable",
        "specs": {"type": "tantalum", "polarity": "polarized", "typical_voltage": "4V-50V", "typical_capacitance": "0.1uF-1000uF"},
        "keywords": "tantalum capacitor small drop shaped bead solid compact SMD",
    },
    {
        "name": "Carbon Film Resistor",
        "category": "resistor",
        "description": "Common through-hole resistor with color-coded bands",
        "specs": {"type": "carbon_film", "power_rating": "0.25W typical", "tolerance": "5%"},
        "keywords": "resistor color bands axial through-hole carbon film",
        "reuse_suggestion": "Universal for LED current limiting, pull-up/pull-down networks, voltage dividers, and sensor biasing.",
    },
    {
        "name": "SMD Resistor",
        "category": "resistor",
        "description": "Surface mount chip resistor with numeric code marking",
        "specs": {"type": "chip_smd", "power_rating": "0.1W typical", "tolerance": "1-5%"},
        "keywords": "SMD chip resistor surface mount 0402 0603 0805 1206 small rectangular",
    },
    {
        "name": "LED (Light Emitting Diode)",
        "category": "led",
        "description": "Semiconductor light source for indication or illumination",
        "specs": {"forward_voltage": "1.8-3.3V depending on color", "current": "20mA typical"},
        "keywords": "LED light emitting diode round clear lens color red green blue yellow",
        "reuse_suggestion": "Status indicators, lighting projects, throwie LEDs, or replace damaged LEDs in existing electronics.",
    },
    {
        "name": "Signal Diode (1N4148)",
        "category": "diode",
        "description": "Small signal diode for switching and signal routing",
        "specs": {"type": "signal", "forward_voltage": "0.7V", "reverse_voltage": "100V", "current": "300mA"},
        "keywords": "diode small signal glass body band stripe 1N4148",
    },
    {
        "name": "Rectifier Diode (1N4007)",
        "category": "diode",
        "description": "General-purpose rectifier diode for AC-DC conversion",
        "specs": {"type": "rectifier", "forward_voltage": "1.1V", "reverse_voltage": "1000V", "current": "1A"},
        "keywords": "rectifier diode black body band stripe 1N4007 power supply",
    },
    {
        "name": "NPN Transistor (2N2222)",
        "category": "transistor",
        "description": "General-purpose NPN bipolar junction transistor",
        "specs": {"type": "NPN BJT", "collector_current": "600mA", "voltage": "40V", "package": "TO-92"},
        "keywords": "transistor NPN BJT three pins TO-92 small plastic 2N2222",
        "reuse_suggestion": "Switch for relays and solenoids, LED drivers, signal amplification in audio circuits, or logic level conversion.",
    },
    {
        "name": "Buzzer",
        "category": "buzzer",
        "description": "Piezoelectric or electromagnetic audio buzzer for alerts",
        "specs": {"voltage": "3-12V", "frequency": "2-4kHz", "type": "active or passive"},
        "keywords": "buzzer round cylindrical audio sound alert piezo beeper",
    },
    {
        "name": "Push Switch / Tactile Button",
        "category": "switch",
        "description": "Momentary tactile push button switch for user input",
        "specs": {"voltage_rating": "12V", "current_rating": "50mA", "type": "momentary SPST"},
        "keywords": "push button switch tactile small square click momentary SPST",
    },
    {
        "name": "IC Chip (Generic DIP)",
        "category": "ic",
        "description": "Integrated circuit in dual in-line package",
        "specs": {"package": "DIP", "note": "Identify by reading silkscreen part number"},
        "keywords": "IC chip DIP dual inline package black rectangular pins legs integrated circuit",
    },
    {
        "name": "Inductor / Coil",
        "category": "inductor",
        "description": "Wire-wound inductor for energy storage, filtering, and impedance matching",
        "specs": {"typical_inductance": "1uH-10mH", "typical_current": "0.1-10A"},
        "keywords": "inductor coil wire wound toroid axial radial ferrite choke",
    },
    {
        "name": "Heat Sink",
        "category": "heat_sink",
        "description": "Aluminum or copper heat dissipation element for power components",
        "specs": {"material": "aluminum or copper", "purpose": "thermal management"},
        "keywords": "heat sink aluminum fins black anodized thermal cooling dissipation",
    },
    {
        "name": "Fuse",
        "category": "fuse",
        "description": "Overcurrent protection device that melts to break the circuit",
        "specs": {"type": "glass or ceramic cartridge", "voltage": "250V typical"},
        "keywords": "fuse glass tube cartridge ceramic overcurrent protection transparent",
    },
    {
        "name": "9V Battery",
        "category": "battery",
        "description": "9-Volt alkaline or rechargeable battery for portable projects",
        "specs": {"voltage": "9V", "type": "alkaline or NiMH", "connector": "snap connector"},
        "keywords": "9V nine volt battery rectangular snap connector alkaline",
    },
    {
        "name": "16x2 Character LCD (HD44780)",
        "category": "display",
        "description": "Industry-standard alphanumeric LCD module with Hitachi HD44780 controller, 16-pin parallel interface, 5V operation",
        "specs": {
            "type": "character LCD",
            "interface": "4-bit or 8-bit parallel (HD44780)",
            "voltage": "5V DC (4.5V-5.5V)",
            "current": "1-2mA logic, ~20-30mA with LED backlight",
            "display_format": "16 characters x 2 lines",
            "character_size": "5x8 dot matrix",
            "controller": "HD44780 or compatible (KS0070, SPLC780D)",
            "pinout": "VSS,VDD,V0,RS,RW,E,D0,D1,D2,D3,D4,D5,D6,D7,A,K",
            "backlight": "LED, blue/green/white, ~3-4V via 10Ω resistor",
            "contrast": "Adjustable via V0 (10k pot)",
            "response_time": "1-5ms",
            "operating_temp": "0°C to 50°C"
        },
        "keywords": "LCD display 1602 16x2 character HD44780 alphanumeric parallel backlight 16-pin salvage e-waste",
        "reuse_suggestion": "Perfect for Arduino/ESP32 weather stations, digital clocks, thermostat displays, or I2C retrofitting for PC status monitors.",
    },
    {
        "name": "20x4 Character LCD (HD44780)",
        "category": "display",
        "description": "Wide-format alphanumeric LCD module with HD44780-compatible controller, 20 chars x 4 lines",
        "specs": {
            "type": "character LCD",
            "interface": "4-bit or 8-bit parallel (HD44780)",
            "voltage": "5V DC (4.5V-5.5V)",
            "current": "2-3mA logic, ~30-50mA with LED backlight",
            "display_format": "20 characters x 4 lines",
            "character_size": "5x8 dot matrix",
            "controller": "HD44780 compatible",
            "pinout": "VSS,VDD,V0,RS,RW,E,D0,D1,D2,D3,D4,D5,D6,D7,A,K",
            "backlight": "LED"
        },
        "keywords": "LCD display 2004 20x4 character HD44780 alphanumeric wide parallel backlight salvage",
        "reuse_suggestion": "Ideal for CNC controller panels, multi-sensor monitoring displays, or as a universal status display for home automation projects.",
    },
    {
        "name": "128x64 Graphic LCD (ST7920/KS0108)",
        "category": "display",
        "description": "Dot-matrix graphic LCD module capable of displaying text and bitmaps, common in printers and test equipment",
        "specs": {
            "type": "graphic LCD",
            "interface": "serial (SPI) or 8-bit parallel",
            "voltage": "5V DC (some 3.3V variants exist)",
            "current": "5-10mA typical",
            "resolution": "128x64 pixels",
            "controller": "ST7920 or KS0108",
            "backlight": "LED backlight (white/blue/green)",
            "pinout": "varies by controller"
        },
        "keywords": "graphic LCD 128x64 dot matrix ST7920 KS0108 SPI parallel bitmap pixel salvage display",
        "reuse_suggestion": "Great for oscilloscope front-ends, game emulator screens, or animated dashboard displays in Raspberry Pi projects.",
    },
    {
        "name": "Bluetooth Module (HC-05/06)",
        "category": "communication",
        "description": "Serial Bluetooth communication module",
        "specs": {"voltage": "3.3V (5V tolerant)", "protocol": "Bluetooth 2.0 SPP", "baud_rate": "9600 default"},
        "keywords": "bluetooth module HC-05 HC-06 wireless serial communication blue board",
        "reuse_suggestion": "Wireless UART for Arduino/ESP projects, smartphone-controlled relays, or retrofitting serial cables with Bluetooth.",
    },
    {
        "name": "RFID Reader Module (RC522)",
        "category": "communication",
        "description": "13.56 MHz RFID reader/writer module",
        "specs": {"voltage": "3.3V", "frequency": "13.56 MHz", "interface": "SPI", "protocol": "MIFARE"},
        "keywords": "RFID reader RC522 card tag reader NFC 13.56MHz SPI blue board",
    },
    {
        "name": "Gas Sensor (MQ Series)",
        "category": "sensor",
        "description": "Semiconductor gas sensor for detecting various gases",
        "specs": {"voltage": "5V", "heater_voltage": "5V", "detection": "varies by model (CO, LPG, alcohol, etc.)"},
        "keywords": "gas sensor MQ metal can cylinder heater analog detection air quality",
    },
    {
        "name": "LDR (Light Dependent Resistor)",
        "category": "sensor",
        "description": "Photoresistor that changes resistance based on ambient light",
        "specs": {"dark_resistance": "1MΩ+", "light_resistance": "1-10kΩ", "voltage": "up to 150V"},
        "keywords": "LDR photoresistor light sensor CdS cell serpentine pattern",
    },
    {
        "name": "Servo Motor (SG90 / MG996R)",
        "category": "actuator",
        "description": "Hobby servo motor for precise angular control",
        "specs": {"voltage": "4.8-6V", "signal": "PWM 50Hz", "rotation": "180°"},
        "keywords": "servo motor blue plastic horn gear PWM angular rotation SG90 MG996R",
        "reuse_suggestion": "Robot arm joints, camera gimbal stabilization, RC vehicle steering, or automated window blind openers.",
    },
    {
        "name": "Motor Driver Module (L298N / L293D)",
        "category": "motor_driver",
        "description": "H-Bridge motor driver module for DC motors and stepper motors",
        "specs": {"voltage": "5-35V", "current": "2A per channel", "channels": "2"},
        "keywords": "motor driver H-bridge L298N L293D dual channel red board heatsink",
        "reuse_suggestion": "Drive DC motors in robots, control stepper motors for 3D printer builds, or automate window blinds with gear motors.",
    },
    {
        "name": "Sonar / Ultrasonic Sensor (HC-SR04)",
        "category": "sensor",
        "description": "Ultrasonic distance measurement sensor module",
        "specs": {"voltage": "5V", "range": "2-400cm", "accuracy": "3mm", "trigger": "10μs pulse"},
        "keywords": "ultrasonic sonar sensor HC-SR04 two cylinders transducer blue board distance",
        "reuse_suggestion": "Water tank level monitoring, automated parking sensors, obstacle avoidance robots, or presence detection for smart lighting.",
    },
]


# ── RAG Engine ───────────────────────────────────────────────────────────────

class RAGFallback:
    """
    Semantic search over component knowledge base.

    Embeds component descriptions + keywords and finds the closest match
    to the query (YOLO class name + any OCR text).

    Usage:
        rag = RAGFallback()
        result = rag.search("small blue rectangular module with two metal cylinders")
        print(result.matched_name)  # "Sonar / Ultrasonic Sensor (HC-SR04)"
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        if not SBERT_AVAILABLE:
            print("[RAG] WARNING: sentence-transformers not installed")
            self.model = None
            self.embeddings = None
            return

        print(f"[RAG] Loading embedding model: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.knowledge = COMPONENT_KNOWLEDGE

        # Pre-compute embeddings for all knowledge entries
        texts = [
            f"{k['name']} {k['category']} {k['description']} {k.get('keywords', '')}"
            for k in self.knowledge
        ]
        self.embeddings = self.model.encode(texts, normalize_embeddings=True)
        print(f"[RAG] Indexed {len(self.knowledge)} components ({self.embeddings.shape})")

    def search(
        self,
        query: str,
        top_k: int = 1,
        min_similarity: float = 0.3,
    ) -> RAGResult:
        """
        Search for the best matching component.

        Args:
            query: Free-text description (e.g., YOLO class + OCR text)
            top_k: Number of results to consider
            min_similarity: Minimum cosine similarity to accept a match

        Returns:
            RAGResult with matched component specs
        """
        if self.model is None or self.embeddings is None:
            return RAGResult(query=query, found=False)

        # Encode query
        query_emb = self.model.encode([query], normalize_embeddings=True)

        # Cosine similarity (embeddings are already normalized)
        similarities = np.dot(self.embeddings, query_emb.T).flatten()

        # Get best match
        best_idx = int(np.argmax(similarities))
        best_sim = float(similarities[best_idx])

        if best_sim < min_similarity:
            return RAGResult(query=query, similarity=best_sim, found=False)

        matched = self.knowledge[best_idx]
        return RAGResult(
            query=query,
            matched_name=matched["name"],
            matched_category=matched["category"],
            similarity=best_sim,
            specs=matched.get("specs", {}),
            found=True,
            source="rag",
            description=matched.get("description", ""),
            reuse_suggestion=matched.get("reuse_suggestion", ""),
        )

    def search_batch(self, queries: list[str]) -> list[RAGResult]:
        """Search for multiple queries."""
        return [self.search(q) for q in queries]
