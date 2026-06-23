"""Schematic model — what the Circuit Design Agent produces.

This is the second contract in the system (the first being PartsManifest).
ERC / SPICE / Z3 / compile_check all consume a Schematic.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class PinKind(str, Enum):
    POWER_IN = "power_in"
    POWER_OUT = "power_out"
    GROUND = "ground"
    DIGITAL_IN = "digital_in"
    DIGITAL_OUT = "digital_out"
    ANALOG_IN = "analog_in"
    ANALOG_OUT = "analog_out"
    BIDIRECTIONAL = "bidirectional"
    PASSIVE = "passive"  # for R, C, L, LED, diode, motor, etc. — terminal that's neither in nor out
    NC = "nc"


class Pin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    net: Optional[str] = Field(default=None, description="Net this pin is connected to, null = unconnected")
    kind: PinKind
    drive_voltage: Optional[str] = Field(default=None, description="Logic level this pin drives/expects, e.g. '3.3V'")


class ComponentRef(BaseModel):
    """A part from the manifest, placed on the schematic."""

    model_config = ConfigDict(extra="forbid")

    part_id: str = Field(description="References PartsManifest.part_id")
    refdes: str = Field(description="Reference designator, e.g. 'U1', 'R1', 'C1'")
    kind: Literal["ic", "resistor", "capacitor", "diode", "inductor", "relay", "motor", "sensor", "led", "psu", "other"]
    value: Optional[str] = Field(default=None, description="Component value, e.g. '10kΩ', '100nF'")
    pins: list[Pin] = Field(default_factory=list)


class PowerRail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    voltage: float
    current_ma: float = Field(description="Maximum current this rail can supply")


class Schematic(BaseModel):
    """The Circuit Design Agent's output. ERC / SPICE / Z3 consume this."""

    model_config = ConfigDict(extra="forbid")

    title: str
    target_use: str = Field(description="What we're building, e.g. 'auto-irrigation-controller'")
    rails: list[PowerRail] = Field(min_length=1)
    components: list[ComponentRef] = Field(min_length=1)
    notes: list[str] = Field(default_factory=list, description="Design notes, assumptions, TODO from the LLM")

    def component_by_id(self, part_id: str) -> Optional[ComponentRef]:
        for c in self.components:
            if c.part_id == part_id:
                return c
        return None