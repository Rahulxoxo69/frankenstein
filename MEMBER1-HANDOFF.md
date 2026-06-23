# Frankenstein E-Waste — Quick Handoff

**Hi — this is what I've been doing on the Frankenstein engine side. Read sections 1–3, skim the rest. Five-minute read.**

---

## 1. What this project is (in plain English)

We take photos of broken e-waste circuit boards, an AI looks at the photo and figures out what parts are on it (ESP32, resistors, sensors, etc.), and decides which parts are clean / fixable / unsafe. Then another AI takes the clean parts and designs a brand-new working circuit from them.

You (Member 1) own the first half — looking at photos and outputting a list of parts. I (Member 2) own the second half — taking that list and designing a circuit from it.

---

## 2. What I built (the part you need to know about)

I built a program that takes your parts list (as a JSON file) and designs a circuit. It checks if the parts will work together (e.g. doesn't blow up a 3.3V chip by connecting it to 5V), it runs a design review with a separate AI "inspector" that flags problems, and it loops back to redesign if needed.

It has 37 tests passing. I have a mock bundle (fake parts) I used to test the whole thing end-to-end. When you have real data, you drop your real parts list in the same JSON format and it just works.

The repo is at `/mnt/e/frankenstein/`. Run `pytest tests/` to see all tests pass.

---

## 3. What I need from you (the actual ask)

**Just one thing: give me one real parts list (a JSON file) from your pipeline.**

The format I need is fixed. I wrote a Pydantic model in `frankenstein/schema.py` that defines it exactly, but for you all that matters is the JSON shape. Here's a single example part (the real shape — just one item, not the full list):

```json
{
  "schema_version": "0.1.0",
  "part_id": "esp32_01",
  "name": "ESP32-WROOM-32",
  "status": "functional",
  "confidence": 0.97,
  "source": "vault",
  "specs": {
    "voltage": "3.3V",
    "current_ma": 240,
    "io_voltage": "3.3V",
    "pinout": {"GPIO4": "DHT22 data"},
    "source": "nexar"
  },
  "repair_note": null,
  "disposal_reason": null,
  "detected_at": "2026-06-20T19:00:00Z"
}
```

Three statuses you'll be outputting:
- `"functional"` — clean, drop in the design
- `"repairable"` — fixable, include `"repair_note": "what's wrong + how to fix"`
- `"unsafe"` — physically dangerous, include `"disposal_reason": "why it can't be reused"` (these never reach my engine, they go to e-waste disposal)

The other fields are straightforward:
- `confidence`: 0.0–1.0, how sure your detection is
- `source`: `"photo"` (from today's intake), `"vault"` (pulled from a known-good database), or `"mock"` (only me, ignore)
- `specs.voltage`, `current_ma`, `io_voltage`, `pinout`: electrical and physical info from Nexar / datasheet / your measurement

If you can output JSON that looks like the above, my engine eats it. If a field is wrong, my code will throw a `ValidationError` and tell you exactly which field is wrong.

**Drop one file in `mock_data/` when you have a real output. I'll wire it into a test in 5 minutes.**

---

## 4. Five questions I need you to answer (in your own time)

1. **Schema version policy** — when you change a field in your output, do you bump `schema_version` (my engine rejects old data, clean break) or just add new fields (forgiving, but drift is invisible)? Default: bump on breaking change.

2. **Vault dedup** — if you emit `source: "vault"`, what's the `vault_entry_id`? Stable UUID? Hash of the part? Just need it to be the same across multiple intake photos that detect the same part.

3. **Confidence threshold** — currently parts with `confidence < 0.65` still reach my engine. Want a hard cutoff in the schema (reject below 0.50) or keep it soft?

4. **Repairable tier** — when you emit a part with `repair_note: "resolder pin 3"`, should my engine design around the post-fix state (assume the fix happens) or the current broken state? I currently assume post-fix. Tell me if you want it explicit.

5. **Schema fidelity** — when you start emitting real data, is your current pipeline output *exactly* this shape, or do you need a translator layer? (If the latter, I'll add a `from_your_format()` adapter.)

Just reply to this in any form — even one-liners are fine. I'll lock in the policy.

---

## 5. The full picture (only if you want the gory details)

The repo has ~3000 lines of code. The main pieces are:

- `frankenstein/schema.py` — the contract (Pydantic model, this is the only file we share)
- `frankenstein/mocks.py` — 15 fake parts + 2 fake bundles for testing
- `frankenstein/verification/erc.py` — electrical rule checks (voltage, current, logic levels)
- `frankenstein/agents/inspector.py` — AI "design review" that flags problems
- `frankenstein/foreman.py` — the controller that loops design → check → review → redesign
- `frankenstein/engine.py` — the entry point: `run(parts_list, target_use) → circuit_design + scores`

There are 37 tests, all passing. End-to-end smoke test runs the engine on a fake irrigation-controller parts list and produces a real circuit + flags a real bug (5V relay into 3.3V chip — caught by the inspector).

If you want to see it work, run:
```
cd /mnt/e/frankenstein
source .venv/bin/activate
pytest tests/ -v
```

If you want to read the full handoff I originally wrote (with the architecture diagrams, schema reference, and file map), it's in `MEMBER1-HANDOFF-detailed.md` — but you don't need to.

---

## TL;DR

1. I built a circuit-design engine. It works. 37 tests pass.
2. I need ONE real parts-list JSON file from your pipeline.
3. The JSON shape is in section 3. If yours matches, we're integrated.
4. Five policy questions in section 4 — answer any way you want.

— Member 2
