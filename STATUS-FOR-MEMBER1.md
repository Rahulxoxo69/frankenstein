# Frankenstein E-Waste — Status for Member 1 (Member 1)

**From:** Member 2 (Frankenstein engine / circuit design)
**To:** Member 1 (Member 1 — vision pipeline, DeepPCB-YOLOv8, RAG vault)
**Date:** 2026-06-21
**TL;DR:** Member 2's half is mostly built. 37 tests passing, mock bundle proves the engine works on the irrigation use case (the one in our submission). Three real gaps + the LLM-cost swap.

---

## What the submission says vs what I've built

I'm mapping each line in our Gen Z Challenge submission to the actual code in `/mnt/e/frankenstein/`. So you can see what I'm responsible for and where I need your input.

### What I own (Member 2 — Frankenstein engine)

| Submission line | What it means | Built? | File |
|---|---|---|---|
| **Frankenstein Engine — multi-agent LLM (ReAct) for circuit design** | Use-case invention + schematic design | 🟡 partial — schematic only, use-case invention is a stub | `frankenstein/engine.py` |
| **Chain-of-thought pin-level verification** | ReAct + multi-step reasoning on every pin | ✅ done | `agents/circuit_design.py` |
| **Constraint-aware (user tools + skill level)** | Beginner vs expert output | ❌ not in model | needs to be added |
| **Wiring diagram output** | Schematic with pin connections | ✅ done | `frankenstein/schematic.py` |
| **Arduino C++/MicroPython code** | Firmware output | ❌ not done | needs `agents/firmware.py` |
| **Electrically validated schematics** | ERC + SPICE + Z3 | 🟡 1/4 — ERC only, SPICE/Z3 are stubs | `verification/erc.py` + 3 stubs |
| **Safety guardrails** (hazardous parts flagged with disposal) | UNSAFE parts filtered + disposal report | 🟡 partial — filter works, no explicit disposal report yet | `schema.py` + `engine.py` |

### What you own (Member 1 — vision pipeline)

| Submission line | Status | Notes from my side |
|---|---|---|
| **YOLOv8 fine-tuned on DeepPCB** | Your code | The contract I emit assumes `part_id` + name match your detection labels — see below |
| **GPT-4o Vision for condition analysis** | Your code | My schema accepts `status: functional \| repairable \| unsafe` and a `repair_note` field for fix instructions |
| **Damage inference LLM** | Your code | You emit `confidence: 0.0–1.0`; my engine treats < 0.65 as low-confidence but doesn't hard-reject (your call) |
| **Draft Vault (pgvector)** | Your code | The schema is the contract — `frankenstein/schema.py`. Just emit `ManifestBundle` and I'll consume it |
| **RAG pipeline over component datasheets** | Your code | My engine doesn't need RAG — I trust the `specs` block you emit (Nexar, datasheet, or measured) |
| **Frontend (Next.js) — user-facing photo intake** | Your code | No action from me |

### What we share (the contract)

**`frankenstein/schema.py` — locked at version 0.1.0.** This is the only file we both touch. It defines exactly the JSON shape I expect from your pipeline. You can see the full schema there, but the key fields you emit:

- `status: "functional" | "repairable" | "unsafe"`
- `confidence: 0.0–1.0` (your detection + status confidence)
- `source: "photo" | "vault" | "mock"` (mock = my testing only)
- `specs.voltage`, `specs.current_ma`, `specs.io_voltage`, `specs.pinout` (electrical/physical data)
- `repair_note: str | None` (required when status == repairable, describes the fix)
- `disposal_reason: str | None` (required when status == unsafe)

If your output matches this shape, my engine runs. If it doesn't, my code throws a `ValidationError` and tells you exactly which field is wrong.

---

## What's working (you can verify)

✅ **37 tests passing.** Run `pytest tests/` in `/mnt/e/frankenstein/` to see.
✅ **End-to-end smoke test passes.** The engine runs on the irrigation mock bundle and produces a real Schematic + Inspector report. The Inspector caught the 5V→3.3V relay mismatch (the kind of bug the submission says we catch).
✅ **UNSAFE parts are filtered.** PUMP_12V01 in the irrigation bundle is marked unsafe → filtered before the design phase. The safety guardrail the submission promises.
✅ **Mock bundle matches our submission example.** The irrigation mock I built has moisture sensor → ESP32 GPIO 34 → relay GPIO 5 → pump, same as the doc. So when you have real data, the demo flow is already proven.

## What's pending (gaps to fill before the hackathon)

| Gap | What it needs | Effort | ETA |
|---|---|---|---|
| **Use-case invention agent** | Add an Ideation agent that runs BEFORE circuit design. Takes the parts + user context, invents "smart irrigation / weather station / plant monitor" etc. Currently I assume target_use is given. | 10-15 min | today |
| **Firmware generation agent** | Add a Firmware agent that emits Arduino C++/MicroPython. Skill-level-aware (beginner: breadboard comments; expert: OTA-capable). | 15-20 min | today |
| **Skill-level + available-tools input** | Add to engine input and propagate to every agent. The doc says "constraint-aware" — this is the mechanism. | 5-10 min | today |
| **LLM swap to Gemini Flash** | Currently `frankenstein/llm.py` is OpenAI. Need to swap to Gemini Flash free tier (15 req/min, 1500/day, multimodal). $0 cost. | 10-15 min | today |
| **SPICE / Z3 verification stubs → real** | Three of the four verification checks are stubs. ERC is real; SPICE/Z3/compile-check return None. | 1-2 hours | tomorrow |
| **Explicit DisposalReport in EngineResult** | When UNSAFE parts are in the bundle, emit a `DisposalReport` with India E-Waste Rules 2022 references. The submission says "disposal instructions generated" — currently we just filter. | 5-10 min | today |

**Total to "demo-ready at $0 cost":** ~60-90 minutes of work, mostly mechanical.

---

## What I need from you (Member 1)

1. **One real parts manifest file.** Drop it in `/mnt/e/frankenstein/mock_data/` as JSON matching the schema. I'll wire it into a new end-to-end test the same way I wired the irrigation bundle. The integration is a one-line swap. (~5 min on my end once you have the file.)

2. **5 policy questions (in the handoff I sent):**
   - Schema version policy: bump on breaking change vs additive?
   - Vault entry ID format: UUID, hash, or other?
   - Confidence threshold: hard-reject < 0.50 in the schema, or keep soft?
   - Repair tier handling: assume fixed, design around broken, or refuse?
   - Schema fidelity: does your pipeline output exactly this shape, or do you need a translator layer?

3. **Your GPT-4o Vision system prompt** — I'd like to see the prompt you're using for the damage inference. If we swap to Gemini Flash for the demo, I'll need a compatible prompt structure.

4. **One CircuitLM paper skim** (arXiv:2601.04505) — the doc references it as the inspiration for the multi-agent design. If you have time, a 10-min read of their evaluation criteria would help me tune the Inspector to match. Not blocking.

---

## What's NOT in my plan (deferred)

- LangSmith tracing in the Foreman (10 lines, low priority)
- Full provenance tracking (which photo produced which part → which iteration fixed it)
- UI / frontend (your side entirely)
- YOLOv8 fine-tuning (your side entirely)
- The "vault across teardowns" accumulation (your side; my engine just consumes whatever vault entries you send it)

---

## Files you'll want to look at

```
/mnt/e/frankenstein/
├── frankenstein/
│   ├── schema.py              # THE CONTRACT — read this first
│   ├── mocks.py               # 15 fake parts including the irrigation bundle
│   ├── engine.py              # top-level entry: run(bundle, target_use) → EngineResult
│   ├── foreman.py             # LangGraph routing with reflexion loop
│   ├── agents/
│   │   ├── circuit_design.py  # the ReAct multi-step design agent
│   │   └── inspector.py       # the multi-agent debate review
│   └── verification/erc.py    # electrical rule check (real, 357 lines)
├── tests/
│   └── test_end_to_end.py     # smoke test on irrigation bundle, run this
├── examples/
│   └── irrigation_manifest.json   # the JSON shape you need to emit
└── MEMBER1-HANDOFF.md         # the simpler handoff I sent earlier
```

**Quickest way to see it work:**
```bash
cd /mnt/e/frankenstein
source .venv/bin/activate
pytest tests/ -v
```

Should take 25 seconds. 37 tests pass.

---

## Honest state assessment

The submission claims "AI-powered upcycling pipeline that outputs schematics + code." My half currently outputs schematics + safety scores, but NOT code. I can fix that in 30 minutes. Once I do, Member 2's half matches the submission spec.

The submission claims "electrically validated schematics." I have 1 of 4 validators working. For the hackathon demo, ERC is enough — it catches the high-value issues (5V→3.3V, overcurrent, etc.). For production, the other 3 are needed.

The submission claims "zero hardware cost." I can hit that with the Gemini Flash swap. Without it, the GPT-4o cost per demo run is small but non-zero.

**Bottom line:** the architecture in the diagram is the right shape, my code matches it, and the gaps are mechanical (not architectural). A few hours of focused work closes them.

Let me know when you have a real manifest and I'll wire it in.

— Member 2

P.S. The handoff in `MEMBER1-HANDOFF.md` is a 5-minute read if you want the absolute minimum on what my side does. This file is the "what's the actual state right now" version.
