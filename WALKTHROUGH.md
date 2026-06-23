# Frankenstein E-Waste Upcycling Pipeline - Walkthrough

> **Two-member integration:** Member 1 (Vision) detects and classifies PCB components from
> photos. Member 2 (Engine) takes the validated parts list and designs a working circuit.
> This project merges both pipelines into a single codebase with a unified schema contract.

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Quick Start](#3-quick-start)
4. [The Schema Contract](#4-the-schema-contract)
5. [Running the Vision Pipeline (Member 1)](#5-running-the-vision-pipeline-member-1)
6. [Running the Engine (Member 2)](#6-running-the-engine-member-2)
7. [End-to-End Integration](#7-end-to-end-integration)
8. [Testing](#8-testing)
9. [Project Structure](#9-project-structure)
10. [FAQ](#10-faq)

---

## 1. Project Overview

Frankenstein turns e-waste into working circuits. The pipeline:

1. **Take a photo** of a discarded circuit board
2. **Vision pipeline** detects components, assesses physical condition, reads part numbers
3. **Spec grounding** enriches each part with specs from Nexar, datasheets, or a RAG vault
4. **Engine** takes validated parts and designs a circuit, verifying with ERC/SPICE/Z3/Compile
5. **Output** is a schematic + firmware + buildability score

## 2. Architecture

The integration revolves around a **unified schema** (`frankenstein/schema.py`).
This single file defines every type that crosses the Member 1 / Member 2 boundary.

```
Member 1 (Vision)             Member 2 (Engine)
  YOLOv8 Detector               Foreman (LangGraph)
       |                              |
  Condition Assessor              Circuit Design
       |                              |
  Grounding (Nexar/OCR/Vault)     Verifiers (ERC/SPICE/Z3/Compile)
       |                              |
  TeardownManifest ----[adapter]--> ManifestBundle
```

The **TeardownManifest** (Vision output) is converted to a **ManifestBundle**
(Engine input) via `.to_manifest_bundle()` on the schema.

## 3. Quick Start

### Setup
```bash
cd frankenstein
pip install -e .[dev]
python -m pytest tests/ -v        # Verify 48+ tests pass
```

### Smoke Test (no API keys)
```bash
PYTHONPATH=. python -c "
from frankenstein.schema import PartsManifest, PartSpecs, PartStatus
from frankenstein.schema import SpecSource, TeardownContext, TeardownManifest

spec = PartSpecs(voltage='3.3V', source=SpecSource.NEXAR)
part = PartsManifest(part_id='cap_01', name='Capacitor',
    status=PartStatus.FUNCTIONAL, confidence=0.95, source='photo', specs=spec)
ctx = TeardownContext(device_model='Test')
tdn = TeardownManifest(teardown_id='demo', context=ctx, parts=[part])
bundle = tdn.to_manifest_bundle()
print(f'Bundle: {bundle.bundle_id}, {len(bundle.usable())} usable')
"
```

## 4. The Schema Contract

Key types in `frankenstein/schema.py`:

| Type | Purpose | Produced By | Consumed By |
|---|---|---|---|
| PartsManifest | A single detected part | Vision | Both |
| PartSpecs | Electrical specs | Grounding | Engine |
| ManifestBundle | Parts for engine input | Adapter | Engine |
| TeardownManifest | Full vision output | Vision | Adapter |
| BoundingBox | YOLO coordinates | Detector | DetectionInfo |
| DetectionInfo | YOLO metadata | Detector | PartsManifest |
| BoardDamage | PCB damage | Assessor | Context |

### Part Status
- **functional** - usable as-is
- **repairable** - needs repair_note, assumed post-fix
- **unsafe** - needs disposal_reason, never reaches engine

## 5. Running the Vision Pipeline

### Start API Server
```bash
python -m frankenstein.vision.api.main
# -> http://localhost:8000
```

### API Endpoints
| Endpoint | Method | Description |
|---|---|---|
| / | GET | Frontend UI |
| /teardown | POST | Upload board photo |
| /vault/search | POST | Search parts vault |
| /vault/stats | GET | Vault statistics |
| /health | GET | Health check |

### Use the Detector Directly
```python
from frankenstein.vision.detector import DualYOLODetector
detector = DualYOLODetector(weights_dir='weights/')
result = detector.detect('board.jpg')
print(f'{len(result.components)} components found')
```

### Full Triage Pipeline
```python
from frankenstein.vision.triage_pipeline import VisionTriagePipeline
pipeline = VisionTriagePipeline()
manifest = pipeline.process_image('board.jpg', device_model='MyDevice')
bundle = manifest.to_manifest_bundle()  # Convert for engine
```

## 6. Running the Engine

### With Mock Bundle (no API keys)
```python
from frankenstein.engine import run
from frankenstein.mocks import IRRIGATION_BUNDLE
from frankenstein.llm import StubLLM
from frankenstein.foreman import set_llm_override
from tests.test_foreman import _good_irrigation_schematic

stub = StubLLM()
stub.queue_response(_good_irrigation_schematic())
set_llm_override(stub)
result = run(IRRIGATION_BUNDLE, 'irrigation controller', max_attempts=3)
set_llm_override(None)
print(f'Status: {result.status}, Buildability: {result.buildability_score:.0f}/100')
```

### Individual Verifiers
```python
from frankenstein.verification import check_erc, check_spice, check_z3
erc = check_erc(schematic, bundle)
print(erc.summary)  # 'ERC PASS - 0 error(s), 1 warning(s).'
spice = check_spice(schematic, erc, bundle=bundle)
print(spice.netlist)  # Full SPICE3 netlist
z3r = check_z3(schematic, bundle, solve=True)
print(z3r.summary)
```

## 7. End-to-End Integration

The integration bridge is `TeardownManifest.to_manifest_bundle()`.

### Full Pipeline
```python
from frankenstein.vision.triage_pipeline import VisionTriagePipeline
from frankenstein.engine import run

pipeline = VisionTriagePipeline()
teardown = pipeline.process_image('board.jpg', device_model='MyDevice')
bundle = teardown.to_manifest_bundle()
result = run(bundle, target_use='custom gadget', max_attempts=3)
print(f'Design: {result.status}')
```

### Load Saved Manifest
```python
from frankenstein.schema import TeardownManifest
with open('result.json') as f:
    tdn = TeardownManifest.model_validate_json(f.read())
bundle = tdn.to_manifest_bundle()
result = run(bundle, 'my project')
```

## 8. Testing

```bash
python -m pytest tests/ -v                    # All tests
python -m pytest tests/test_schema.py         # Schema (8)
python -m pytest tests/test_erc.py            # ERC rules (10)
python -m pytest tests/test_validation.py     # Z3/SPICE/Compile (20)
python -m pytest tests/test_end_to_end.py     # End-to-end (2)
```

Tests requiring langgraph or z3 solver (9 total) will be skipped if not installed.
48 tests pass with no external dependencies.

## 9. Project Structure

```
frankenstein/
  frankenstein/
    schema.py          # UNIFIED CONTRACT - both Member types
    engine.py          # Public entry: run(bundle, target_use)
    foreman.py         # LangGraph reflexion loop
    llm.py             # OpenAI client + StubLLM
    mocks.py           # 15 mock parts, 2 bundles
    schematic.py       # Circuit data model
    agents/            # Circuit design + inspector
    verification/      # ERC, SPICE, Z3, Compile checks
    vision/            # Detector, triage, grounding, vault, API
  frontend/            # Vision API frontend (HTML/JS/CSS)
  tests/               # 57 tests
  examples/            # Sample manifests
  weights/             # YOLO model weights
```

## 10. FAQ

**Q: Do I need an OpenAI API key?**
A: No - StubLLM provides canned responses. The vision pipeline has demo mode too.

**Q: Do I need a GPU?**
A: YOLOv8 works on CPU (slower). Engine/verifiers are CPU-only.

**Q: What's the difference between TeardownManifest and ManifestBundle?**
A: TeardownManifest includes teardown context (device model, damage, image paths).
ManifestBundle is the engine's input format. The adapter converts between them.

**Q: Why do some tests fail?**
A: 7 need langgraph, 2 need z3 C++ solver. All 48 others pass.

---

*Last updated: June 23, 2026*
