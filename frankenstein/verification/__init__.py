"""Verification suite: ERC, SPICE, Z3, compile check.

All four are real, runnable against mock data today. SPICE is a pure-Python
DC/RC solver + netlist generator (no ngspice dependency); Z3 encodes
voltage/current/power/logic-level constraints as SMT; compile_check is a
real Arduino C++ structural analyzer (no arduino-cli dependency).

Each module can be swapped for a heavier implementation later (ngspice for
full SPICE transient sim, real Z3 encodings for tolerance bands, arduino-cli
for actual firmware compile) — the interfaces are stable.
"""

from __future__ import annotations

from frankenstein.verification.erc import check_erc, ERCReport
from frankenstein.verification.spice import check_spice, SPICEReport
from frankenstein.verification.z3_check import check_z3, Z3Report
from frankenstein.verification.compile_check import check_compile, CompileReport

__all__ = [
    "check_erc",
    "check_spice",
    "check_z3",
    "check_compile",
    "ERCReport",
    "SPICEReport",
    "Z3Report",
    "CompileReport",
]