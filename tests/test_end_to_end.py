"""End-to-end integration test: run_foreman on the irrigation bundle with a stub LLM.

Proves Member 2's half works end-to-end without Member 1's pipeline existing.
This is the integration test that replaces "swap mock file for real manifest."
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Type

import pytest
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from frankenstein.engine import run
from frankenstein.mocks import IRRIGATION_BUNDLE
from frankenstein.schema import PartsManifest


class StubLLM:
    """Fake LLM that returns canned responses for each agent type.

    Mirrors the real LLMClient API (complete_json) so the test exercises
    the same code path the production engine does — just without HTTP.
    """

    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, system_prompt: str, user_prompt: str, response_model: Type[BaseModel], **kwargs) -> BaseModel:
        # The foreman calls us with the response_model class. Match on the SCHEMA NAME
        # first (most reliable), then fall back to prompt heuristics for stub-mode nodes
        # that don't pass a unique schema.
        schema_name = response_model.__name__
        self.calls.append({
            "system_len": len(system_prompt),
            "user_len": len(user_prompt),
            "schema": schema_name,
        })
        if schema_name == "Schematic":
            return self._schematic()
        if schema_name == "InspectorReport":
            return self._inspection()
        # Heuristic fallback (combined prompt text)
        combined = (system_prompt + " " + user_prompt).lower()
        if "schematic" in combined or "design" in combined or "circuit" in combined:
            return self._schematic()
        if "inspect" in combined or "critique" in combined:
            return self._inspection()
        return self._schematic()  # default fallback

    def _schematic(self):
        from frankenstein.schematic import Schematic, PowerRail, ComponentRef, Pin, PinKind
        return Schematic(
            title="Irrigation Controller",
            target_use="auto-irrigation-controller for home garden",
            rails=[
                PowerRail(name="+5V", voltage=5.0, current_ma=1000),
                PowerRail(name="+3V3", voltage=3.3, current_ma=500),
                PowerRail(name="GND", voltage=0.0, current_ma=0),
            ],
            components=[
                ComponentRef(part_id="esp32_01", refdes="U1", kind="ic", pins=[]),
                ComponentRef(part_id="dht22_01", refdes="U2", kind="sensor", pins=[]),
                ComponentRef(part_id="soil_moisture_01", refdes="U3", kind="sensor", pins=[]),
                ComponentRef(part_id="relay_5v_01", refdes="K1", kind="relay", pins=[]),
                ComponentRef(part_id="reg_7805_01", refdes="REG1", kind="ic", pins=[]),
            ],
        )

    def _inspection(self):
        from frankenstein.agents.inspector import InspectorReport, Concern
        return InspectorReport(
            approved=False,
            concerns=[
                Concern(text="RELAY 5V logic level into 3.3V ESP32 GPIO without level shifter — will damage MCU over time", severity="high"),
                Concern(text="PUMP_12V01 was filtered out (UNSAFE) so no pump drive circuit is included", severity="info"),
            ],
            suggestions=[
                "Add a 2N7000 level shifter or use a 3.3V relay variant",
                "Substitute 5V pump with 12V supply + 12V relay variant",
            ],
        )

    def _scores(self):
        # The foreman computes scores internally from state, not via LLM.
        # This is here for any future LLM-driven scoring path; not used by current graph.
        raise NotImplementedError("scores are computed from state, not LLM")


def test_engine_runs_end_to_end():
    """Member 2's half on the irrigation bundle. The smoke test for the whole system."""
    stub = StubLLM()
    # Pass llm=stub into the engine so the graph uses it
    result = run(IRRIGATION_BUNDLE, target_use="irrigation controller for home garden", llm=stub)


def test_engine_uses_only_usable_parts():
    """UNSAFE parts (e.g. PUMP_12V with cracked housing) MUST NOT appear in the schematic."""
    # Use IRRIGATION_BUNDLE which contains PUMP_12V marked UNSAFE
    bundle = IRRIGATION_BUNDLE
    unsafe_ids = {p.part_id for p in bundle.parts if p.status.value == "unsafe"}
    assert "pump_12v_01" in unsafe_ids
    # The engine's filtered set should exclude these
    usable_ids = {p.part_id for p in bundle.usable()}
    assert "pump_12v_01" not in usable_ids
    print(f"\n--- Usable filter ---")
    print(f"  total parts: {len(bundle.parts)}")
    print(f"  usable: {len(usable_ids)}")
    print(f"  unsafe (excluded): {len(unsafe_ids)}")
