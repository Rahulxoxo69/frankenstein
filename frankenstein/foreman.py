"""Foreman — LangGraph routing layer for the Frankenstein engine.

Routes:
  START → design → ERC → (Inspector → firmware → compile) → score → END

Conditional edges:
  ERC fail       → repair → design  (reflexion)
  Inspector block→ repair → design  (reflexion)
  Compile fail   → repair → design  (reflexion)
  attempts > max → failed  → END

The 'repair' node doesn't design anything itself — it appends the previous
attempt's violations to the user prompt and routes back to 'design', which
the LLM uses as feedback (reflexion pattern).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, TypedDict

from frankenstein.agents.circuit_design import SYSTEM_PROMPT, design_circuit
from frankenstein.agents.inspector import inspect
from frankenstein.schematic import Schematic
from frankenstein.schema import ManifestBundle
from frankenstein.verification.compile_check import check_compile
from frankenstein.verification.erc import check_erc
from frankenstein.verification.spice import check_spice
from frankenstein.verification.z3_check import check_z3


# Module-level LLM override for tests + alternate providers. If set, _node_design
# uses this instead of the real OpenAI client. Production leaves it None.
_llm_override: Optional[object] = None


def set_llm_override(llm: Optional[object]) -> None:
    """Globally swap the LLM used by the Foreman (tests + alt providers)."""
    global _llm_override
    _llm_override = llm


def get_llm_override() -> Optional[object]:
    return _llm_override


# --- state ---

class ForemanState(TypedDict, total=False):
    bundle: ManifestBundle
    target_use: str
    max_attempts: int

    attempt: int
    schematic: Optional[Schematic]
    erc_report: Optional[object]
    spice_report: Optional[object]
    z3_report: Optional[object]
    inspector_report: Optional[object]
    firmware: Optional[str]
    compile_report: Optional[object]
    buildability_score: float
    robustness_confidence: float

    reflexion_feedback: str
    log: list[str]
    status: Literal["designing", "verifying", "simulating", "formally_verifying",
                    "inspecting", "writing_firmware", "compiling", "done", "failed"]

    # Test/alt-provider injection: if set, _node_design routes through this LLM client
    # instead of the real OpenAI client. Anything implementing complete_json(system, user, model).
    llm: Optional[object]


# --- firmware generation (simple MVP) ---

def _generate_firmware_for(schematic: Schematic) -> str:
    """MVP firmware stub — emits a sketch that drives every GPIO output we know about.

    Real impl: LLM-driven firmware spec generation, toolchain compile.
    """
    lines = [
        f"// Auto-generated for {schematic.title}",
        f"// Target: {schematic.target_use}",
        "",
        "void setup() {",
        "  Serial.begin(115200);",
    ]
    for c in schematic.components:
        for p in c.pins:
            if p.kind.value == "digital_out":
                lines.append(f"  pinMode({p.name}, OUTPUT);")
            elif p.kind.value == "digital_in":
                lines.append(f"  pinMode({p.name}, INPUT_PULLUP);")
    lines += [
        "}",
        "",
        "void loop() {",
        "  // TODO: replace with design-specific logic",
        "  delay(1000);",
        "}",
    ]
    return "\n".join(lines) + "\n"


# --- scoring ---

def _buildability_score(state: ForemanState) -> float:
    """0-100. Lower for missing components / ERC errors / unused repairables."""
    score = 100.0
    bundle = state["bundle"]
    usable = bundle.usable()
    schematic = state.get("schematic")
    notes_text = "\n".join(schematic.notes).lower() if schematic else ""

    # ERC errors cost 15 each
    if state.get("erc_report") is not None:
        score -= 15.0 * len(state["erc_report"].errors())

    # SPICE failures cost 12 each (real circuit math errors are serious)
    if state.get("spice_report") is not None:
        score -= 12.0 * len(state["spice_report"].errors())

    # Z3 unsat constraints cost 18 each (formal proof the design is broken)
    if state.get("z3_report") is not None:
        score -= 18.0 * len(state["z3_report"].errors())

    # Inspector blockers cost 20 each
    if state.get("inspector_report") is not None:
        score -= 20.0 * state["inspector_report"].blockers

    # Repairable parts not addressed cost 10 each
    for p in usable:
        if p.status.value == "repairable" and p.part_id.lower() not in notes_text:
            score -= 10.0

    # Each reflexion attempt (beyond first) costs 5 — design instability
    score -= 5.0 * max(0, state.get("attempt", 1) - 1)

    # Unsafe parts still in bundle (shouldn't happen — filter upstream) cost 50
    if schematic is not None:
        unsafe_in_design = sum(
            1 for c in schematic.components
            for p in bundle.parts
            if p.part_id == c.part_id and p.status.value == "unsafe"
        )
        score -= 50.0 * unsafe_in_design

    return max(0.0, min(100.0, score))


def _robustness_confidence(state: ForemanState) -> float:
    """0-100. Higher for tighter ERC pass, margins, and clean compile."""
    conf = 100.0

    if state.get("erc_report") is not None:
        conf -= 5.0 * len(state["erc_report"].warnings())

    if state.get("spice_report") is not None:
        conf -= 8.0 * len(state["spice_report"].warnings())  # warnings reduce confidence

    if state.get("compile_report") is not None and not state["compile_report"].passed:
        conf -= 30.0 * len(state["compile_report"].errors)

    # Tight current headroom = less robust
    erc = state.get("erc_report")
    if erc is not None:
        for v in erc.violations:
            if v.rule == "R4_current_budget" and v.severity == "warning":
                conf -= 10.0

    # Each reflexion attempt = less confident in design stability
    conf -= 3.0 * max(0, state.get("attempt", 1) - 1)

    return max(0.0, min(100.0, conf))


# --- node functions ---

def _node_design(state: ForemanState) -> dict:
    attempt = state.get("attempt", 0) + 1
    log = list(state.get("log", []))
    log.append(f"[attempt {attempt}] design_circuit → target={state['target_use']}")

    reflexion = state.get("reflexion_feedback", "")
    extra = ""
    if reflexion:
        extra = (
            "Your previous attempt failed. Here is the feedback — address EVERY issue:\n\n"
            + reflexion
        )
        log.append(f"[attempt {attempt}] reflexion feedback attached ({len(reflexion)} chars)")

    # Allow injected LLM (tests, alt providers); default = real OpenAI client
    llm = state.get("llm") or get_llm_override()
    if llm is not None:
        schematic = llm.complete_json(SYSTEM_PROMPT, _design_user_prompt(state, extra), Schematic)
    else:
        schematic = design_circuit(
            state["bundle"],
            state["target_use"],
            extra_constraints=extra,
        )
    log.append(f"[attempt {attempt}] schematic produced with {len(schematic.components)} components on {len(schematic.rails)} rail(s)")
    return {
        "attempt": attempt,
        "schematic": schematic,
        "status": "designing",
        "log": log,
    }


def _design_user_prompt(state: ForemanState, extra: str) -> str:
    """Build the same user prompt design_circuit would, for stub-mode calls."""
    from frankenstein.agents.circuit_design import _format_manifests
    return (
        f"Target use: {state['target_use']}\n\n"
        f"Parts available:\n{_format_manifests(state['bundle'])}\n\n"
        + (f"Additional constraints:\n{extra}\n\n" if extra else "")
        + "Design the Frankenstein circuit and return JSON matching the Schematic schema."
    )


def _node_erc(state: ForemanState) -> dict:
    schematic = state["schematic"]
    if schematic is None:
        return {"status": "failed"}
    erc = check_erc(schematic, state["bundle"])
    log = list(state.get("log", []))
    log.append(f"[attempt {state['attempt']}] ERC: {erc.summary}")
    return {"status": "verifying", "erc_report": erc, "log": log}


def _node_spice(state: ForemanState) -> dict:
    schematic = state["schematic"]
    erc = state.get("erc_report")
    if schematic is None or erc is None:
        return {"status": "failed"}
    spice = check_spice(schematic, erc, simulate=False, bundle=state["bundle"])
    log = list(state.get("log", []))
    log.append(f"[attempt {state['attempt']}] SPICE: {spice.summary}")
    return {"status": "simulating", "spice_report": spice, "log": log}


def _node_z3(state: ForemanState) -> dict:
    schematic = state["schematic"]
    if schematic is None:
        return {"status": "failed"}
    z3r = check_z3(schematic, state["bundle"], solve=True)
    log = list(state.get("log", []))
    log.append(f"[attempt {state['attempt']}] Z3: {z3r.summary}")
    return {"status": "formally_verifying", "z3_report": z3r, "log": log}


def _node_inspector(state: ForemanState) -> dict:
    schematic = state["schematic"]
    erc = state["erc_report"]
    if schematic is None or erc is None:
        return {"status": "failed"}
    inspector = inspect(schematic, state["bundle"], erc.violations)
    log = list(state.get("log", []))
    log.append(f"[attempt {state['attempt']}] Inspector: {inspector.summary}")
    return {"status": "inspecting", "inspector_report": inspector, "log": log}


def _node_firmware(state: ForemanState) -> dict:
    schematic = state["schematic"]
    if schematic is None:
        return {"status": "failed"}
    fw = _generate_firmware_for(schematic)
    log = list(state.get("log", []))
    log.append(f"[attempt {state['attempt']}] firmware generated ({len(fw)} chars)")
    return {"status": "writing_firmware", "firmware": fw, "log": log}


def _node_compile(state: ForemanState) -> dict:
    if state.get("firmware") is None or state.get("schematic") is None:
        return {"status": "failed"}
    rep = check_compile(state["firmware"], state["schematic"])
    log = list(state.get("log", []))
    log.append(f"[attempt {state['attempt']}] compile: {rep.summary}")
    return {"status": "compiling", "compile_report": rep, "log": log}


def _node_score(state: ForemanState) -> dict:
    bs = _buildability_score(state)
    rc = _robustness_confidence(state)
    log = list(state.get("log", []))
    log.append(f"FINAL: buildability={bs:.1f} robustness={rc:.1f}")
    return {
        "buildability_score": bs,
        "robustness_confidence": rc,
        "status": "done",
        "log": log,
    }


def _node_failed(state: ForemanState) -> dict:
    log = list(state.get("log", []))
    log.append(f"FAILED after {state.get('attempt', 0)} attempt(s)")
    return {"status": "failed", "log": log}


def _node_repair(state: ForemanState) -> dict:
    return {"reflexion_feedback": _build_reflexion_feedback(state)}


# --- conditional edges ---

def _after_erc(state: ForemanState) -> str:
    if state["erc_report"].passed:
        return "spice"
    if state["attempt"] >= state["max_attempts"]:
        return "failed"
    return "repair"


def _after_spice(state: ForemanState) -> str:
    # SPICE failures can be tolerated if they're warning-only (LED slightly off-spec, etc.)
    spice = state.get("spice_report")
    if spice is None:
        return "z3"
    spice_fails = [c for c in spice.checks if c.result == "fail"]
    if not spice_fails:
        return "z3"
    if state["attempt"] >= state["max_attempts"]:
        return "failed"
    return "repair"


def _after_z3(state: ForemanState) -> str:
    z3r = state.get("z3_report")
    if z3r is None or z3r.passed:
        return "inspect"
    if state["attempt"] >= state["max_attempts"]:
        return "failed"
    return "repair"


def _after_inspector(state: ForemanState) -> str:
    if not state["inspector_report"].is_blocked():
        return "firmware"
    if state["attempt"] >= state["max_attempts"]:
        return "failed"
    return "repair"


def _after_compile(state: ForemanState) -> str:
    if state["compile_report"].passed:
        return "score"
    if state["attempt"] >= state["max_attempts"]:
        return "failed"
    return "repair"


def _build_reflexion_feedback(state: ForemanState) -> str:
    """Aggregate the latest failure into a single feedback string for the LLM."""
    pieces: list[str] = []
    if state.get("erc_report") is not None and not state["erc_report"].passed:
        for v in state["erc_report"].errors():
            pieces.append(f"ERC ERROR ({v.rule}) on {v.refdes}: {v.message}")
    if state.get("spice_report") is not None:
        for c in state["spice_report"].errors():
            pieces.append(f"SPICE FAIL ({c.rule}) on {c.target}: {c.detail}")
    if state.get("z3_report") is not None and not state["z3_report"].passed:
        for c in state["z3_report"].errors()[:5]:
            pieces.append(f"Z3 UNSAT ({c.rule}) on {c.target}: {c.expression}")
    if state.get("inspector_report") is not None and state["inspector_report"].is_blocked():
        for c in state["inspector_report"].concerns:
            if c.severity == "blocker":
                pieces.append(f"INSPECTOR BLOCKER ({c.expert}) on {c.target}: {c.message}")
    if state.get("compile_report") is not None and not state["compile_report"].passed:
        for e in state["compile_report"].errors:
            pieces.append(f"COMPILE ERROR: {e}")
    return "\n".join(pieces)


# --- graph assembly ---

def build_graph():
    """Construct the Foreman LangGraph. Returns a compiled graph.

    The graph uses simple Python functions for nodes + LangGraph for routing.
    This keeps it readable while still being a real LangGraph cycle.
    """
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as e:  # pragma: no cover
        raise ImportError("langgraph not installed; run `uv pip install -e .[dev]`") from e

    g = StateGraph(ForemanState)

    g.add_node("design", _node_design)
    g.add_node("erc", _node_erc)
    g.add_node("spice", _node_spice)
    g.add_node("z3", _node_z3)
    g.add_node("inspect", _node_inspector)
    g.add_node("firmware", _node_firmware)
    g.add_node("compile", _node_compile)
    g.add_node("score", _node_score)
    g.add_node("repair", _node_repair)
    g.add_node("failed", _node_failed)

    g.set_entry_point("design")
    g.add_edge("design", "erc")
    g.add_conditional_edges("erc", _after_erc, {
        "spice": "spice",
        "repair": "repair",
        "failed": "failed",
    })
    g.add_conditional_edges("spice", _after_spice, {
        "z3": "z3",
        "repair": "repair",
        "failed": "failed",
    })
    g.add_conditional_edges("z3", _after_z3, {
        "inspect": "inspect",
        "repair": "repair",
        "failed": "failed",
    })
    g.add_conditional_edges("inspect", _after_inspector, {
        "firmware": "firmware",
        "repair": "repair",
        "failed": "failed",
    })
    g.add_edge("firmware", "compile")
    g.add_conditional_edges("compile", _after_compile, {
        "score": "score",
        "repair": "repair",
        "failed": "failed",
    })
    g.add_edge("repair", "design")
    g.add_edge("score", END)
    g.add_edge("failed", END)

    return g.compile()


# --- public API ---

@dataclass
class EngineResult:
    status: str
    attempts: int
    schematic: Optional[Schematic]
    buildability_score: float
    robustness_confidence: float
    log: list[str] = field(default_factory=list)


def run(
    bundle: ManifestBundle,
    target_use: str,
    *,
    max_attempts: int = 3,
    graph=None,
    llm=None,
) -> EngineResult:
    """Run the Foreman on a bundle. Returns an EngineResult.

    graph=None  → builds a fresh graph (default).
    graph=...   → uses a pre-built graph (useful for tests + persistent state).
    llm=None    → uses the real OpenAI client (default).
    llm=...     → injected client (StubLLM for tests, alt provider for prod).
    """
    if graph is None:
        graph = build_graph()

    initial: ForemanState = {
        "bundle": bundle,
        "target_use": target_use,
        "max_attempts": max_attempts,
        "attempt": 0,
        "schematic": None,
        "erc_report": None,
        "spice_report": None,
        "z3_report": None,
        "inspector_report": None,
        "firmware": None,
        "compile_report": None,
        "buildability_score": 0.0,
        "robustness_confidence": 0.0,
        "reflexion_feedback": "",
        "log": [],
        "status": "designing",
        "llm": llm,
    }

    final = graph.invoke(initial)

    return EngineResult(
        status=final.get("status", "unknown"),
        attempts=final.get("attempt", 0),
        schematic=final.get("schematic"),
        buildability_score=final.get("buildability_score", 0.0),
        robustness_confidence=final.get("robustness_confidence", 0.0),
        log=list(final.get("log", [])),
    )


# --- convenience for CLI / debugging ---

def _enable_debug_logging() -> None:
    if os.environ.get("FRANKENSTEIN_DEBUG"):
        import logging
        logging.basicConfig(level=logging.DEBUG)