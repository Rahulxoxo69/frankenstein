# Frankenstein Pipeline Accuracy Audit

## Baseline (2026-06-23)
- Full test suite: **57 passed, 0 failed** in ~61s
- Good schematic end-to-end: `buildability=100.0 robustness=95.0 attempts=1`
- Bad→good reflexion end-to-end: `buildability=95.0 robustness=92.0 attempts=2`
- Reflexion feedback length: **153 chars** (just "ERC FAIL — 1 error(s), 1 warning(s)")

## Accuracy Bugs Found

### 1. Z3 Z1 — Only checks LOWER voltage bound
**File:** `frankenstein/verification/z3_check.py` `_rule_z1_supply_tolerance`
**Issue:** The solver sets `v_actual == v_high` where `v_high = rail.voltage - v_drop` (the LOWEST possible voltage). It never tests the UPPER bound (`rail.voltage + v_drop`).
**Impact:** Overvoltage cases are missed. Example: 3.3V rail ±5% → up to 3.465V. A part with `voltage_max=3.4V` would pass Z1 but experience 3.465V in worst case.
**Fix:** Run two solver checks per IC — one at `Vrail - v_drop` and one at `Vrail + v_drop`.

### 2. SPICE S3 — Always returns "pass"\**File:** `frankenstein/verification/spice.py` `_check_s3_rc_time_constant`
**Issue:** Every R-C pair gets `result="pass"` regardless of τ value. The function computes tau but never evaluates it against thresholds.
**Impact:** Slow decoupling (τ > 100 µs) and ineffective filters (τ < 1 µs) are silently accepted.
**Fix:** Add thresholds based on context:
- If cap is on a power rail (shares net with IC power_in) → decoupling; warn if τ > 100 µs
- If cap is on a signal/digitial net → filter; warn if τ < 1 µs or τ > 10 ms

### 3. Compile C7 — Documented but not implemented
**File:** `frankenstein/verification/compile_check.py`
**Issue:** Docstring lists C7 `undefined_identifier` (Serial/SPI/Wire without setup, delay before pinMode). No `_rule_c7_*` function exists.
**Impact:** Arduino logic-ordering bugs are missed.
**Fix:** Implement `_rule_c7_logic_order` that:
- Flags `Serial.begin` after `Serial.print`
- Flags `delay()` before any `pinMode()` (setup ordering bug)
- Flags `SPI.begin/Wire.begin` missing before SPI/Wire transfers

### 4. Reflexion feedback too terse
**File:** `frankenstein/foreman.py` `_build_reflexion_feedback`
**Issue:** Feedback is just a concatenation of error messages. No actionable component-level suggestions. Example: "ERC ERROR (R6_flyback_diode) on K1: back-EMF will kill the driver." → but doesn't say WHAT to add.
**Impact:** LLM has to guess the fix, increasing reflexion attempts.
**Fix:** Append suggested fixes per rule type:
- R6 flyback → "Add 1N4007 or equivalent flyback diode antiparallel to inductive load coil."
- R7 decoupling → "Add 100nF ceramic capacitor between Vcc and GND close to IC."
- R3 logic level → "Add level shifter (e.g. BSS138 MOSFET pair) for 5V→3.3V translation."

### 5. Circuit design prompt lacks anti-patterns
**File:** `frankenstein/agents/circuit_design.py` `SYSTEM_PROMPT`
**Issue:** Prompt lists rules but doesn't give concrete anti-pattern examples the LLM should avoid.
**Impact:** LLM still makes first-attempt ERC errors (flyback missing, decoupling missing).
**Fix:** Add explicit "DO NOT" section with examples:
- "DO NOT place a 5V digital_out pin directly on a 3.3V digital_in net without a level shifter component in the BOM."
- "DO NOT omit a flyback diode when you include a relay or motor."
- "DO NOT leave a digital_in pin unconnected — always add a pull-up resistor or driver."

### 6. Vision detector confidence threshold low
**File:** `frankenstein/vision/detector.py` `DualYOLODetector.__init__`
**Issue:** `component_conf=0.25` and `damage_conf=0.25` are quite permissive.
**Impact:** False positive components and defects increase downstream processing.
**Fix:** Consider raising to 0.35 for components, 0.30 for damage, or adding post-NMS filtering. (Deferred — outside immediate Member-2 scope.)

## Files to Modify
1. `frankenstein/verification/z3_check.py` — fix Z1 double bound
2. `frankenstein/verification/spice.py` — fix S3 thresholds
3. `frankenstein/verification/compile_check.py` — add C7
4. `frankenstein/foreman.py` — richer reflexion feedback
5. `frankenstein/agents/circuit_design.py` — stronger SYSTEM_PROMPT
6. `tests/test_validation.py` — new tests for Z1 upper bound, S3 bad tau, C7 logic order
