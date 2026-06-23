"""Top-level engine entrypoint.

Members 2's deliverable: run_foreman(bundle, target_use) → EngineResult.

This is what Member 2 imports from. Everything below this is plumbing.
"""

from frankenstein.foreman import EngineResult, build_graph, run

__all__ = ["run", "EngineResult", "build_graph"]