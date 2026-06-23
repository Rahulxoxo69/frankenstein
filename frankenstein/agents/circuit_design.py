"""Circuit Design Agent — Member 2's primary LLM-driven agent.

CoT prompting: think step-by-step about the target use, then map manifests
to a schematic. Outputs a Schematic Pydantic model.

Injectable LLM client: default = real OpenAI client, swap StubLLM for tests.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Protocol

from frankenstein.llm import StubLLM, complete_json
from frankenstein.schematic import Schematic
from frankenstein.schema import ManifestBundle, PartStatus


class LLMClient(Protocol):
    def complete_json(self, system_prompt: str, user_prompt: str, response_model: type, **kwargs): ...


SYSTEM_PROMPT = """You are a senior electrical engineer designing a Frankenstein circuit
from a set of validated e-waste parts.

Your job:
1. Read the parts manifest (each part has specs, status, and a repair_note if applicable).
2. Decide what to build with the USABLE parts (status=functional or status=repairable
   that you judge worth designing around or post-repair).
3. Drop UNSAFE parts from your design — they go to disposal.
4. For REPAIRABLE parts, decide explicitly in your notes:
     - ASSUME_REPAIR: design as if the fix happens before assembly (simpler, common).
     - DESIGN_AROUND: design tolerates the current damaged state (e.g. larger hold-down).
5. Output a JSON object matching the Schematic schema.

Constraints:
- Respect voltage/current specs. Never connect a 3.3V GPIO to a 5V input without a
  level shifter (call it out in notes, the ERC will catch it).
- Every inductive load (relay/motor) needs a flyback diode.
- Every IC needs at least one decoupling capacitor in the BOM (100nF typical).
- Sum of part current_ma must not exceed the chosen PSU rail current.
- Pin nets must be explicit — no implicit connections.

Think step-by-step, then output JSON."""


def _format_manifests(bundle: ManifestBundle) -> str:
    lines = [f"Bundle: {bundle.bundle_id}"]
    lines.append(f"Detected: {bundle.detected_at.isoformat()}")
    lines.append("")
    for p in bundle.parts:
        marker = " " if p.status == PartStatus.FUNCTIONAL else f"[{p.status.value.upper()}]"
        lines.append(f"{marker} {p.part_id}: {p.name} (confidence {p.confidence:.2f}, source={p.source.value})")
        if p.specs.voltage:
            lines.append(f"    V={p.specs.voltage}  Vio={p.specs.io_voltage or p.specs.voltage}  I={p.specs.current_ma}mA")
        if p.specs.pinout:
            lines.append(f"    Pinout: {p.specs.pinout}")
        if p.repair_note:
            lines.append(f"    REPAIR: {p.repair_note}")
        if p.disposal_reason:
            lines.append(f"    DISPOSAL: {p.disposal_reason}")
    return "\n".join(lines)


def design_circuit(
    bundle: ManifestBundle,
    target_use: str,
    *,
    llm: Optional[LLMClient] = None,
    extra_constraints: str = "",
) -> Schematic:
    """Run the Circuit Design Agent on a bundle.

    llm=None  → use real OpenAI client (default).
    llm=StubLLM() → deterministic for tests.
    """
    user_prompt = (
        f"Target use: {target_use}\n\n"
        f"Parts available:\n{_format_manifests(bundle)}\n\n"
        + (f"Additional constraints:\n{extra_constraints}\n\n" if extra_constraints else "")
        + "Design the Frankenstein circuit and return JSON matching the Schematic schema."
    )

    if llm is None:
        return complete_json(SYSTEM_PROMPT, user_prompt, Schematic)
    return llm.complete_json(SYSTEM_PROMPT, user_prompt, Schematic)