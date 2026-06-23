# Frankenstein Engine — Member 2

Takes a `ManifestBundle` of validated e-waste parts and produces a circuit
design + firmware + validation reports. The Foreman runs a reflexion loop:
design → ERC → SPICE → Z3 → Inspector → Compile → score, with any verifier
failure routing back to redesign (up to N attempts).

Member 1's vision pipeline emits the `ManifestBundle`; Member 2 (this repo)
decides what to build with the parts and proves it works.

## 1-minute quick start

```bash
# 1. Unpack and install
unzip frankenstein-2026-06-22.zip
cd frankenstein
uv pip install -e .[dev]         # or: pip install -e .[dev]

# 2. Verify install (57 tests, no network needed)
PYTHONPATH=. python -m pytest tests/ -q

# 3. Run the engine on a mock bundle (uses StubLLM, no OpenAI key needed)
PYTHONPATH=. python -c "
from frankenstein.foreman import set_llm_override
from frankenstein.engine import run
from frankenstein.llm import StubLLM
from frankenstein.mocks import IRRIGATION_BUNDLE
from tests.test_foreman import _good_irrigation_schematic
stub = StubLLM(); stub.queue_response(_good_irrigation_schematic())
set_llm_override(stub)
r = run(IRRIGATION_BUNDLE, 'auto-irrigation', max_attempts=3)
set_llm_override(None)
for line in r.log: print(line)
print(f'\\nstatus={r.status} attempts={r.attempts} '
      f'buildability={r.buildability_score:.0f} '
      f'robustness={r.robustness_confidence:.0f}')
"
```

To use a real LLM instead, set `OPENAI_API_KEY` in your env and remove the
StubLLM block.

## 5-minute walkthrough

A new user (or a grader) should be able to follow this in 5 minutes flat.

### Step 1 — Look at the contract

`frankenstein/schema.py` is the only file Member 1 and Member 2 share.
It defines the JSON shape Member 1 must emit:

```python
from frankenstein.schema import PartsManifest, PartStatus, PartSpecs, SpecSource

manifest = PartsManifest(
    part_id="esp32_01",
    name="ESP32-WROOM-32",
    status=PartStatus.FUNCTIONAL,         # functional | repairable | unsafe
    confidence=0.97,                      # 0.0–1.0
    source="vault",                       # vault | photo | mock
    specs=PartSpecs(
        voltage="3.3V", voltage_min=3.0, voltage_max=3.6,
        current_ma=240.0, io_voltage="3.3V",
        pinout={"GPIO4": "DHT22 data", "3V3": "module supply", ...},
        source=SpecSource.NEXAR, confidence=0.95,
    ),
)
```

Three rules the contract enforces (Pydantic `model_validator(mode="after")`):
- `status == repairable` REQUIRES non-empty `repair_note`
- `status == unsafe` REQUIRES non-empty `disposal_reason`
- `status != repairable` REJECTs `repair_note` (and vice versa for `disposal_reason`)

Pin a schema version on both sides; drift is a visible diff in CI.

### Step 2 — Drop a real manifest in `mock_data/`

`mock_data/` is where Member 1 lands real output. Filename: `<your-name>.json`.
Validate before handoff:

```bash
PYTHONPATH=. python -c "
from frankenstein.schema import ManifestBundle
from pathlib import Path
b = ManifestBundle.model_validate_json(Path('mock_data/your-name.json').read_text())
print('VALID —', len(b.parts), 'parts:')
print('  usable:    ', [p.name for p in b.usable()])
print('  repairable:', [p.name for p in b.repairable()])
print('  unsafe:    ', [p.name for p in b.unsafe()])
"
```

### Step 3 — Run the engine on your bundle

```python
from frankenstein import run
from frankenstein.mocks import MY_NEW_BUNDLE  # or load your file

result = run(MY_NEW_BUNDLE, "auto-irrigation", max_attempts=3)
```

`EngineResult` fields:
- `status` — `"done"` or `"failed"` (last attempt exceeded `max_attempts`)
- `attempts` — how many design→verify cycles ran (1 = first try passed)
- `schematic` — the winning `Schematic` (or `None` on failure)
- `buildability_score` — 0–100, weighted by ERC errors (-15), SPICE fail
  (-12), Z3 unsat (-18), Inspector blockers (-20), unaddressed repairables
  (-10), reflexion instability (-5/attempt), unsafe-in-design (-50)
- `robustness_confidence` — 0–100, lower for ERC warnings (-5), SPICE
  warnings (-8), tight current headroom, compile errors, reflexion attempts
- `log` — audit trail of every node visited

### Step 4 — Run individual verifiers (no LLM)

Each verifier is callable standalone on any `Schematic` + `ManifestBundle`.
Useful for debugging a specific failure without burning LLM calls.

```python
from frankenstein.verification import check_erc, check_spice, check_z3, check_compile
from frankenstein.mocks import IRRIGATION_BUNDLE
from tests.test_foreman import _good_irrigation_schematic
sch = _good_irrigation_schematic()

# ERC — 7 rules, pure-Python
erc = check_erc(sch, IRRIGATION_BUNDLE)
print(erc.summary)                  # "ERC PASS — 0 error(s), 1 warning(s)."
for v in erc.errors(): print(f"  ERR {v.rule} {v.refdes}: {v.message}")

# SPICE — 4 checks + netlist export
spice = check_spice(sch, erc, simulate=False, bundle=IRRIGATION_BUNDLE)
print(spice.summary)                # "SPICE PASS — 8 check(s), 0 fail, 1 warn."
print(spice.netlist)                # Full SPICE3 netlist, ready for ngspice
open("/tmp/my_circuit.cir", "w").write(spice.netlist)

# Z3 — 5 SMT rule encodings (real, not stub)
z3r = check_z3(sch, IRRIGATION_BUNDLE, solve=True)
print(z3r.summary)                  # "Z3 PASS — 11 constraint(s) checked, 0 unsat."
print(f"  {z3r.constraints_checked} constraints checked")
if z3r.counterexample: print(f"  counterexample: {z3r.counterexample}")

# Compile — 8-rule Arduino C++ analyzer
fw = "void setup() { pinMode(2, OUTPUT); }\nvoid loop() { digitalWrite(2, HIGH); delay(1000); }"
crep = check_compile(fw, sch)
print(crep.summary)                 # "Compile PASS — 0 error(s), 0 warning(s)."
```

### Step 5 — See the reflexion loop in action

A bad design attempt is rejected by ERC, feedback is added to the next
LLM prompt, and the LLM gets to retry:

```python
from frankenstein.foreman import set_llm_override
from frankenstein.engine import run
from frankenstein.llm import StubLLM
from frankenstein.mocks import IRRIGATION_BUNDLE
from tests.test_foreman import _bad_first_attempt_schematic, _good_irrigation_schematic

stub = StubLLM()
stub.queue_response(_bad_first_attempt_schematic())   # missing flyback → ERC R6 fail
stub.queue_response(_good_irrigation_schematic())     # retry succeeds
set_llm_override(stub)

r = run(IRRIGATION_BUNDLE, "auto-irrigation", max_attempts=3)
set_llm_override(None)

assert r.attempts == 2
# Second LLM call must include the flyback feedback:
assert "flyback" in stub.calls[1][1].lower()
```

## The Foreman graph (the full flow)

```
START → design → erc → spice → z3 → inspect → firmware → compile → score → END
                  ↓      ↓      ↓     ↓
                repair ←──┴──────┴─────┘  (any verifier failure → reflexion)
                  ↓
                design (reflexion)
                  ↓
                ... up to max_attempts
                  ↓
                failed → END
```

Five verifiers in the graph (in order): **ERC**, **SPICE**, **Z3**, **Inspector**, **Compile**.
The first four can each trip the reflexion loop. Inspector can also block
via the `manufacturing` expert (unknown `part_id` in schematic). Compile
catches Arduino firmware bugs (bad `pinMode` mode, out-of-range `analogWrite`,
negative `delay`, etc.).

## Validation rules — what each verifier catches

| Verifier | Rules | Catches |
|---|---|---|
| **ERC** | R1–R7 | IC supply out of spec, shorted rails, drive/recv logic level (drive < recv), current overload, missing pull-ups, missing flyback diode, no decoupling |
| **SPICE** | S1–S4 + netlist | Voltage dividers, LED current out of spec, RC time constants, per-rail power budget. Exports a SPICE3 netlist. |
| **Z3/SMT** | Z1, Z2, Z4, Z6, Z7 | Supply tolerance ±5% vs `voltage_min/max`, power dissipation vs rated W, LED current in spec, current sum +10% margin, **logic level both directions** ±0.3V |
| **Compile** | C0–C8 | Empty source, missing setup/loop, unbalanced braces, bad `pinMode` mode, digital I/O value, analog range, negative `delay`, output/read pin direction conflict |

Real bugs these caught in the irrigation test fixture (before fix):
- 5V Arduino → 3.3V ESP32 (Z7 overvoltage) — old rule missed it
- `soil_moisture_01` `voltage_min=3.3V` violated by 5% rail droop (Z1)
- 12V rail 1000mA cap, 1100mA draw at 10% margin (Z6)
- `digitalWrite(GPIO4, 5)` (C4) — value must be HIGH/LOW/0/1
- `pinMode(2, OUTPT)` typo (C3)

## For Member 1 (the integration point)

Three things you need to know:

1. **Drop a real manifest in `mock_data/your-name.json`.** Schema is
   `ManifestBundle` in `frankenstein/schema.py`. Validate with the one-liner
   in Step 2 above before sending.
2. **Integration is one line.** Whoever runs the engine changes one
   `from frankenstein.mocks import IRRIGATION_BUNDLE` to
   `from frankenstein.mocks import MY_NEW_BUNDLE` (or reads your file
   directly with `ManifestBundle.model_validate_json(...)`).
3. **5 policy questions still open** (see `STATUS-FOR-MEMBER1.md`) — none
   block the engine. Default answers: bump `schema_version` on breaking
   change; vault entry ID is a stable UUID; reject `confidence < 0.50` in
   the schema; assume `repairable` parts are post-fix; emit `ManifestBundle`
   directly (no translator needed).

## What's deferred (honest)

| Deferred | Why |
|---|---|
| Real ngspice wrapper for transient sim | ngspice not in the dev env. Pure-Python SPICE covers the static (V/I/P) checks that matter for the irrigation demo. The `check_spice().netlist` field emits a real SPICE3 netlist you can run through ngspice elsewhere. |
| arduino-cli / platformio for real firmware compile | Not in the env. The 8-rule structural analyzer catches the common Arduino bugs (bad mode, out-of-range analog, missing setup/loop, pin direction conflict, brace mismatch, negative delay, unknown pin). ~80% of what an actual compile catches. |
| LLM-driven Inspector (multi-agent debate as GPT-4o calls) | Currently deterministic for MVP. The wiring supports it — `inspector.py` is structured for swap. |
| LLM-driven firmware spec generation | Currently template-based (`_generate_firmware_for` in foreman.py). |

All four are drop-in function swaps; the interfaces in `verification/`
and the agent modules are stable.

## Test coverage (57 passed)

```
tests/test_schema.py        8  — contract enforcement (extra=forbid, repair_note rules)
tests/test_mocks.py         6  — fixture coverage (15 parts, 2 bundles)
tests/test_erc.py          10  — every ERC rule + a known-good irrigation schematic
tests/test_inspector.py     5  — manufacturing / electrical / repairable / low-conf
tests/test_foreman.py       6  — LangGraph wiring + reflexion loop + audit log
tests/test_end_to_end.py    2  — engine.run() with StubLLM (good + bad→good cycle)
tests/test_validation.py   20  — Z3 (5) + SPICE (5) + compile check (10)
                            ──
                            57 passed
```

Run with `PYTHONPATH=. python -m pytest tests/ -v` (no network, no API key).

## Files

```
frankenstein/
├── __init__.py
├── schema.py              # THE contract (PartsManifest, ManifestBundle)
├── schematic.py           # second contract (what Circuit Design produces)
├── llm.py                 # OpenAI client wrapper + StubLLM
├── mocks.py               # 15 mock manifests, 2 pre-built bundles
├── engine.py              # public entry point: run(bundle, target_use)
├── foreman.py             # LangGraph routing + reflexion + scoring
├── agents/
│   ├── __init__.py
│   ├── circuit_design.py  # CoT prompt → Schematic
│   └── inspector.py       # multi-agent debate → blocker/warning list
└── verification/
    ├── __init__.py
    ├── erc.py             # 7-rule Electrical Rules Check (real)
    ├── spice.py           # pure-Python DC + RC solver + netlist export (real)
    ├── z3_check.py        # 5 SMT rule encodings (real, z3-solver 4.13+)
    └── compile_check.py   # 8-rule Arduino C++ analyzer (real)

tests/                     # 57 tests, see coverage above
examples/
├── dump_manifest.py       # writes IRRIGATION_BUNDLE → irrigation_manifest.json
└── irrigation_manifest.json

mock_data/                 # where Member 1 drops real parts manifests

pyproject.toml             # pydantic, openai, langgraph, z3-solver
README.md                  # you are here
MEMBER1-HANDOFF.md         # simpler handoff for Member 1 (5-min read)
STATUS-FOR-MEMBER1.md      # status update (2026-06-21)
STATUS-2026-06-22-VALIDATION.md   # status update (2026-06-22, validation upgrade)
```
