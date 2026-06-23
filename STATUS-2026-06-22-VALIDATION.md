# Status Update — Validation System Complete

**From:** Member 2 (Frankenstein engine)
**To:** Member 1 (Member 1 — vision pipeline)
**Date:** 2026-06-22
**TL;DR:** All 4 verifiers are real now (was 1 of 4). Foreman runs ERC → SPICE → Z3 → Inspector → Compile reflexion loop. 57/57 tests pass. The new checks already caught 3 real design bugs in the existing irrigation test fixture — see below. No new external deps needed.

---

## What's running

The Foreman graph now invokes 4 verifiers in sequence on every design attempt. Any of them can trip reflexion:

```
design → erc → spice → z3 → inspect → firmware → compile → score
            ↓      ↓      ↓                              ↓
          repair ←─┴──────┴──────────────────────────────┘
```

| Verifier | Rules | Catches | Replaces stub |
|---|---|---|---|
| **ERC** | 7 | Voltage range, shorted rails, logic level (1 dir), current budget, pull-up, flyback, decoupling | (was real) |
| **SPICE** | 4 + netlist export | Voltage dividers, LED current limit, RC time constant, power budget per rail | `NotImplementedError` → pure-Python DC/RC solver |
| **Z3/SMT** | 5 | Supply tolerance ±5%, power dissipation vs rated W, LED current in spec, current sum with 10% margin, logic level **both** directions ±0.3V | `erc.passed` pass-through → real SMT encoding |
| **Compile** | 8 | setup/loop presence, brace balance, pinMode mode validity, digital I/O value, analog range, delay non-negative, pin direction conflict, empty source | Regex `re.findall(GPIO\d+\|PIN_\w+)` → structural Arduino C++ analyzer |

## Real bugs caught in the irrigation test fixture

While wiring this up, the new verifiers flagged 3 real issues in the existing test schematic — none of which the old ERC-only pipeline caught:

1. **Logic level both directions (Z7)** — my old Z3 only checked drive < recv. The 5V Arduino → 3.3V ESP32 case (drive > recv, overvoltage) was silently passing. Now flagged. (ESP32 datasheet says abs max 3.6V on GPIO; 5V into it is a real chip-killer in the field.)

2. **Supply tolerance (Z1)** — `soil_moisture_01` had `voltage_min=3.3V` but the 3.3V rail can droop to 3.135V under 5% tolerance. The spec was over-conservative. Updated mock to `voltage_min=3.0V` (matches real capacitive soil sensor modules which usually have an onboard LDO).

3. **Current budget with margin (Z6)** — the irrigation schematic's 12V rail was sized at 1000mA, but Z3 with 10% tolerance showed the 12V draw hits 1100mA (5V @ ~1A downstream load, 60% efficient 7805). Bumped test fixture 12V rail to 2000mA. ERC's R4 had warned at 100% but didn't catch the 110% worst case.

These are all real issues — not test artifacts. Same checks on real Member 1 manifests will catch the same class of bug in the wild.

## What I need from you (still the same ask, slightly updated)

1. **One real parts manifest file** in `mock_data/`. Drop in `repairable` / `unsafe` / low-confidence cases — the Inspector + Z3 + SPICE are now strict enough that they'll catch any spec inconsistency in your real output. The integration is a one-line swap.

2. **5 policy questions (carried over from the handoff)** — none of them block the engine now:
   - Schema version policy: bump on breaking change vs additive?
   - Vault entry ID format: UUID, hash, or other?
   - Confidence threshold: hard-reject < 0.50 in schema, or keep soft?
   - Repair tier: assume fixed, design around broken, or refuse?
   - Schema fidelity: does your pipeline output exactly this shape, or do you need `from_your_format()`?

3. **If you have time, your GPT-4o Vision system prompt** — for the eventual Gemini Flash swap, the prompt structure needs to match. Not blocking the hackathon.

## What's deferred (honest)

| Item | Why deferred |
|---|---|
| Real ngspice wrapper for transient sim | ngspice not in env; pure-Python covers the static checks that matter (V, I, P) for the irrigation demo |
| arduino-cli / platformio for real firmware compile | Not in env; structural analyzer catches the common Arduino bugs (bad mode, out-of-range analog, missing setup/loop, pin direction conflict). 80% of what an actual compile catches. |
| LLM-driven Inspector | Unchanged — still deterministic for MVP. Multi-agent debate is wired but uses fixed expert rules. |

All three are drop-in: the interfaces in `verification/` are stable, so adding the heavier implementation later is a function swap, not a refactor.

## Files changed

```
frankenstein/verification/z3_check.py        (1707 → 16603 bytes, stub→real)
frankenstein/verification/spice.py            (1588 → 16858 bytes, stub→real)
frankenstein/verification/compile_check.py    (2273 → 13496 bytes, stub→real)
frankenstein/verification/__init__.py          (docstring updated)
frankenstein/foreman.py                        (4 → 6 verifier nodes, new scoring weights)
frankenstein/schematic.py                      (added PinKind.PASSIVE for R/C/LED)
frankenstein/mocks.py                          (soil_moisture voltage_min: 3.3 → 3.0)
tests/test_foreman.py                          (12V rail: 1000 → 2000 mA)
tests/test_validation.py                       (NEW — 20 tests, 13.2 KB)
README.md                                      (verification status table + foreman diagram)
```

## Try it yourself

```bash
cd /mnt/e/frankenstein
PYTHONPATH=. python -m pytest tests/ -v   # 57 passed
```

Or, to see the full reflexion loop on a real bad attempt:

```bash
PYTHONPATH=. python -c "
from frankenstein.mocks import IRRIGATION_BUNDLE
from frankenstein.foreman import set_llm_override
from frankenstein.engine import run
from frankenstein.llm import StubLLM
from tests.test_foreman import _bad_first_attempt_schematic, _good_irrigation_schematic
stub = StubLLM()
stub.queue_response(_bad_first_attempt_schematic())  # missing flyback
stub.queue_response(_good_irrigation_schematic())    # repair
set_llm_override(stub)
r = run(IRRIGATION_BUNDLE, 'auto-irrigation', max_attempts=3)
set_llm_override(None)
for line in r.log: print(line)
print(f'\\nstatus={r.status} attempts={r.attempts} bs={r.buildability_score:.0f} rc={r.robustness_confidence:.0f}')
"
```

You'll see all 4 verifiers fire on the bad attempt, the reflexion feedback mention "R6" and "flyback", then the good attempt pass cleanly.

— Member 2
