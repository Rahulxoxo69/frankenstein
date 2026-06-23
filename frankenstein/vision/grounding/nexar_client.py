"""
Nexar API Client (Free Tier)
=============================
Looks up part numbers against Nexar's 70M+ component database to retrieve
grounded technical specifications (pinout, voltage, package, datasheet URL).

Step 2 of the Grounding Cascade: OCR → **Nexar** → RAG Fallback

Nexar provides a free tier (1000 queries/month) which is sufficient for
hackathon/demo use. If unavailable, falls through to RAG.

API Docs: https://nexar.com/api
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

import requests


# ── Nexar Result ─────────────────────────────────────────────────────────────

@dataclass
class NexarPartInfo:
    """Structured specs returned from Nexar lookup."""
    part_number: str
    manufacturer: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    package: Optional[str] = None
    datasheet_url: Optional[str] = None
    specs: dict[str, str] = field(default_factory=dict)  # key-value specs
    found: bool = False
    source: str = "nexar"


# ── GraphQL Query ────────────────────────────────────────────────────────────

NEXAR_SEARCH_QUERY = """
query SearchParts($q: String!) {
  supSearchMpn(q: $q, limit: 3) {
    results {
      part {
        mpn
        manufacturer {
          name
        }
        shortDescription
        category {
          name
        }
        bestDatasheet {
          url
        }
        specs {
          attribute {
            name
          }
          displayValue
        }
      }
    }
  }
}
"""


# ── Client ───────────────────────────────────────────────────────────────────

class NexarClient:
    """
    Look up component specs from Nexar's database.

    Usage:
        client = NexarClient(client_id="...", client_secret="...")
        info = client.lookup("NE555")
        print(info.specs)  # {'Operating Voltage': '4.5V to 16V', ...}
    """

    TOKEN_URL = "https://identity.nexar.com/connect/token"
    API_URL = "https://api.nexar.com/graphql"

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
    ):
        """
        Args:
            client_id: Nexar application client ID (or NEXAR_CLIENT_ID env var)
            client_secret: Nexar application client secret (or NEXAR_CLIENT_SECRET env var)
        """
        self.client_id = client_id or os.environ.get("NEXAR_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("NEXAR_CLIENT_SECRET", "")
        self._token: Optional[str] = None
        self.available = bool(self.client_id and self.client_secret)

        if self.available:
            print("[Nexar] Client initialized (credentials provided)")
        else:
            print("[Nexar] No credentials — Nexar lookup will be skipped")

    def _get_token(self) -> str:
        """Obtain OAuth2 token from Nexar identity server."""
        if self._token:
            return self._token

        resp = requests.post(
            self.TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    def lookup(self, part_number: str) -> NexarPartInfo:
        """
        Look up a part number in Nexar's database.

        Args:
            part_number: MPN to search for (e.g., "NE555", "ESP32-WROOM-32")

        Returns:
            NexarPartInfo with specs if found, or empty result if not
        """
        if not self.available:
            return NexarPartInfo(part_number=part_number, found=False)

        try:
            token = self._get_token()

            resp = requests.post(
                self.API_URL,
                json={
                    "query": NEXAR_SEARCH_QUERY,
                    "variables": {"q": part_number},
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            results = (
                data.get("data", {})
                .get("supSearchMpn", {})
                .get("results", [])
            )

            if not results:
                return NexarPartInfo(part_number=part_number, found=False)

            # Take the first (best) result
            part = results[0].get("part", {})

            # Extract specs into a flat dict
            specs = {}
            for spec in part.get("specs", []):
                attr_name = spec.get("attribute", {}).get("name", "")
                value = spec.get("displayValue", "")
                if attr_name and value:
                    specs[attr_name] = value

            return NexarPartInfo(
                part_number=part.get("mpn", part_number),
                manufacturer=part.get("manufacturer", {}).get("name"),
                description=part.get("shortDescription"),
                category=part.get("category", {}).get("name"),
                datasheet_url=(part.get("bestDatasheet") or {}).get("url"),
                package=specs.get("Package / Case", specs.get("Package", None)),
                specs=specs,
                found=True,
                source="nexar",
            )

        except requests.exceptions.RequestException as e:
            print(f"  [Nexar] API error for '{part_number}': {e}")
            return NexarPartInfo(part_number=part_number, found=False)

        except Exception as e:
            print(f"  [Nexar] Unexpected error for '{part_number}': {e}")
            return NexarPartInfo(part_number=part_number, found=False)

    def lookup_batch(self, part_numbers: list[str]) -> list[NexarPartInfo]:
        """Look up multiple part numbers."""
        return [self.lookup(pn) for pn in part_numbers]


# ── Mock Client (offline / no credentials) ───────────────────────────────────

# Common component specs for demo/testing without API access
KNOWN_PARTS_DB: dict[str, dict[str, Any]] = {
    "ESP32-WROOM-32": {
        "manufacturer": "Espressif",
        "description": "Wi-Fi & Bluetooth SoC Module",
        "category": "Microcontroller",
        "package": "Module",
        "specs": {
            "Operating Voltage": "3.0V to 3.6V",
            "Flash Memory": "4 MB",
            "WiFi": "802.11 b/g/n",
            "Bluetooth": "v4.2 BR/EDR + BLE",
            "GPIO Pins": "34",
            "ADC Channels": "18",
        },
        "datasheet_url": "https://www.espressif.com/sites/default/files/documentation/esp32-wroom-32_datasheet_en.pdf",
    },
    "ATMEGA328P": {
        "manufacturer": "Microchip",
        "description": "8-bit AVR Microcontroller",
        "category": "Microcontroller",
        "package": "DIP-28",
        "specs": {
            "Operating Voltage": "1.8V to 5.5V",
            "Flash Memory": "32 KB",
            "SRAM": "2 KB",
            "Clock Speed": "20 MHz max",
            "Digital I/O Pins": "23",
            "ADC Channels": "6",
        },
        "datasheet_url": "https://ww1.microchip.com/downloads/en/DeviceDoc/ATmega328P-datasheet.pdf",
    },
    "NE555": {
        "manufacturer": "Texas Instruments",
        "description": "Precision Timer IC",
        "category": "Timer IC",
        "package": "DIP-8",
        "specs": {
            "Operating Voltage": "4.5V to 16V",
            "Output Current": "200mA",
            "Timing Range": "μs to hours",
        },
        "datasheet_url": "https://www.ti.com/lit/ds/symlink/ne555.pdf",
    },
    "LM7805": {
        "manufacturer": "Texas Instruments",
        "description": "5V Linear Voltage Regulator",
        "category": "Voltage Regulator",
        "package": "TO-220",
        "specs": {
            "Output Voltage": "5V",
            "Input Voltage": "7V to 35V",
            "Output Current": "1.5A max",
            "Dropout Voltage": "2V",
        },
        "datasheet_url": "https://www.ti.com/lit/ds/symlink/lm7805.pdf",
    },
    "L298N": {
        "manufacturer": "STMicroelectronics",
        "description": "Dual H-Bridge Motor Driver",
        "category": "Motor Driver",
        "package": "Multiwatt-15",
        "specs": {
            "Operating Voltage": "5V to 46V",
            "Output Current": "2A per channel",
            "Peak Current": "3A per channel",
            "Logic Voltage": "5V",
        },
        "datasheet_url": "https://www.st.com/resource/en/datasheet/l298.pdf",
    },
    "SG90": {
        "manufacturer": "TowerPro",
        "description": "Micro Servo Motor",
        "category": "Servo Motor",
        "package": "Module",
        "specs": {
            "Operating Voltage": "4.8V to 6V",
            "Stall Torque": "1.8 kg·cm (4.8V)",
            "Speed": "0.1s/60° (4.8V)",
            "Rotation Range": "180°",
        },
    },
    "HC-SR04": {
        "manufacturer": "Generic",
        "description": "Ultrasonic Distance Sensor",
        "category": "Sensor",
        "package": "Module",
        "specs": {
            "Operating Voltage": "5V",
            "Range": "2cm to 400cm",
            "Accuracy": "3mm",
            "Trigger": "10μs pulse",
        },
    },
    "DHT11": {
        "manufacturer": "Aosong",
        "description": "Temperature & Humidity Sensor",
        "category": "Sensor",
        "package": "Module",
        "specs": {
            "Operating Voltage": "3.3V to 5V",
            "Temperature Range": "0°C to 50°C",
            "Humidity Range": "20% to 90% RH",
            "Accuracy": "±2°C, ±5% RH",
        },
    },
}


class MockNexarClient:
    """
    Offline mock Nexar client using a built-in database of common components.
    Used when no Nexar API credentials are available.
    """

    def __init__(self):
        print("[Nexar] MOCK mode — using built-in component database")
        self.available = True  # Always "available" for cascade logic

    def lookup(self, part_number: str) -> NexarPartInfo:
        """Look up from built-in database."""
        # Try exact match first, then case-insensitive
        pn_upper = part_number.upper().replace("-", "").replace(" ", "")
        for known_pn, info in KNOWN_PARTS_DB.items():
            if known_pn.upper().replace("-", "").replace(" ", "") == pn_upper:
                return NexarPartInfo(
                    part_number=known_pn,
                    manufacturer=info.get("manufacturer"),
                    description=info.get("description"),
                    category=info.get("category"),
                    package=info.get("package"),
                    datasheet_url=info.get("datasheet_url"),
                    specs=info.get("specs", {}),
                    found=True,
                    source="mock_db",
                )

        # Try partial match
        for known_pn, info in KNOWN_PARTS_DB.items():
            if pn_upper in known_pn.upper().replace("-", "") or known_pn.upper().replace("-", "") in pn_upper:
                return NexarPartInfo(
                    part_number=known_pn,
                    manufacturer=info.get("manufacturer"),
                    description=info.get("description"),
                    category=info.get("category"),
                    package=info.get("package"),
                    datasheet_url=info.get("datasheet_url"),
                    specs=info.get("specs", {}),
                    found=True,
                    source="mock_db_partial",
                )

        return NexarPartInfo(part_number=part_number, found=False)

    def lookup_batch(self, part_numbers: list[str]) -> list[NexarPartInfo]:
        return [self.lookup(pn) for pn in part_numbers]


def create_nexar_client(
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> NexarClient | MockNexarClient:
    """Factory: create real client if credentials exist, mock otherwise."""
    cid = client_id or os.environ.get("NEXAR_CLIENT_ID", "")
    csec = client_secret or os.environ.get("NEXAR_CLIENT_SECRET", "")
    if cid and csec:
        return NexarClient(client_id=cid, client_secret=csec)
    else:
        return MockNexarClient()
