# Frankenstein Vision — Setup & Run Guide
## For Member 2 (Frankenstein Engine Integration)

---

## 📋 Prerequisites
- Python 3.10+ (tested on 3.14)
- pip

---

## 🚀 Quick Start (3 steps)

### Step 1: Install dependencies
```bash
pip install -r requirements.txt
```

### Step 2: Set environment variable (optional — works without)
```bash
# Optional: Set Google Gemini API key for real vision assessment
# Without it, a mock assessor runs (marks all parts as functional)
set GOOGLE_API_KEY=your_gemini_api_key_here     # Windows
export GOOGLE_API_KEY=your_gemini_api_key_here   # Linux/Mac
```

### Step 3: Start the server
```bash
cd frankenstein-vision
set PYTHONPATH=src          # Windows
export PYTHONPATH=src       # Linux/Mac
python src/backend/api/main.py
```

Server starts at: **http://localhost:8000**

---

## 🖥️ Using the App

### Demo Mode (No images needed!)
1. Open http://localhost:8000
2. Click any **demo card**: 📱 Phone, ☕ Coffee Machine, 💻 Laptop, 🚁 Drone
3. Watch pipeline animate → auto-switches to Results tab
4. Check **Vault** tab for stored parts across all sessions

### Upload Mode (Real images)
1. Drop/browse a PCB or circuit board photo
2. Fill in device context
3. Click **Analyze Components**
4. Pipeline runs: YOLO → Gemini → Damage Rules → Grounding → Vault

---

## 📁 Project Structure
```
frankenstein-vision/
├── src/
│   ├── backend/
│   │   ├── api/
│   │   │   ├── main.py            ← FastAPI server (all endpoints)
│   │   │   └── demo_data.py       ← 4 demo scenarios
│   │   ├── vision/
│   │   │   ├── detector.py        ← Dual YOLOv8 inference
│   │   │   ├── condition_assessor.py ← Gemini 2.0 Flash Vision
│   │   │   ├── damage_inference.py   ← Deterministic rules engine
│   │   │   └── triage_pipeline.py    ← Full pipeline orchestrator
│   │   ├── grounding/
│   │   │   ├── ocr_reader.py      ← Tesseract/PaddleOCR
│   │   │   ├── nexar_client.py    ← Component database lookup
│   │   │   ├── rag_fallback.py    ← Semantic search (25 components)
│   │   │   └── cascade.py         ← OCR→Nexar→RAG waterfall
│   │   └── vault/
│   │       ├── models.py          ← SQLAlchemy tables (SQLite)
│   │       ├── embedder.py        ← Sentence-transformer embeddings
│   │       └── repository.py      ← CRUD + semantic search
│   ├── frontend/
│   │   ├── index.html             ← Main UI (3 tabs)
│   │   ├── style.css              ← Premium dark theme
│   │   └── app.js                 ← Frontend logic
│   ├── shared/
│   │   └── schema/
│   │       └── parts_manifest.py  ← Pydantic schema (THE CONTRACT)
│   └── models/
│       └── mock_manifests/        ← 7 example manifests
├── weights/
│   ├── component_id_best.pt       ← YOLOv8n trained (20 classes)
│   └── board_damage_best.pt       ← YOLOv8n trained (6 defect types)
├── requirements.txt
└── SETUP.md                       ← This file
```

---

## 🔌 API Endpoints (for Member 2 integration)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/teardown` | Upload images → full triage → returns TeardownManifest |
| `POST` | `/api/demo/run/{id}` | Run demo scenario (phone/coffee/laptop/drone) |
| `GET` | `/api/demo/scenarios` | List available demo scenarios |
| `GET` | `/api/vault/search?q=...` | Semantic search over stored parts |
| `GET` | `/api/vault/stats` | Vault statistics |
| `GET` | `/api/vault/parts` | List all vaulted parts |
| `POST` | `/api/vault/mark` | Mark part as used/available |
| `GET` | `/api/health` | Health check |

### Key Data Contract: `TeardownManifest`
The main output schema that Member 2 consumes. See `src/shared/schema/parts_manifest.py`.

Each part has:
- `part_id`, `name`, `category`
- `status`: functional / repairable / unsafe
- `confidence`: 0.0-1.0
- `specs`: { source, voltage, current_rating, package, part_number, raw }
- `repair_note` / `disposal_note`
- `detection`: { yolo_class, yolo_confidence, bbox }

---

## 🧠 Architecture

```
Photo Upload
    ↓
Dual YOLOv8 Detection
  ├── Component-ID (20 classes: capacitors, ICs, sensors, etc.)
  └── Board-Damage (6 types: short, open_circuit, spur, mouse_bite, etc.)
    ↓
Gemini 2.0 Flash Vision (condition: functional/repairable/unsafe)
    ↓
Damage Inference Rules (defect overlap → status override)
    ↓
Grounding Cascade (OCR → Nexar → RAG — never LLM memory)
    ↓
Draft Vault (SQLite + sentence-transformer embeddings)
    ↓
TeardownManifest JSON → Member 2's Frankenstein Engine
```

---

## ⚠️ Notes
- **No GPU required** — all models run on CPU (~6MB each)
- **No database server** — uses SQLite (auto-created as vault.db)
- **No API keys required for demo** — Gemini runs in mock mode, Nexar uses built-in DB
- **OCR is optional** — if Tesseract isn't installed, it's skipped gracefully
- The `vault.db` file is auto-created on first run — don't include it in version control
