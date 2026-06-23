# Frankenstein

AI-powered e-waste component recovery system. Upload photos of damaged electronics PCBs, identify salvageable components, assess their condition, and maintain a searchable inventory of reusable parts.

## Architecture

```
                   ┌─────────────────────────────────────────────┐
                   │              Web UI (Frontend)              │
                   │     Upload Images / View Results / Vault    │
                   └──────────────────┬──────────────────────────┘
                                      │ HTTP (FastAPI)
                   ┌──────────────────▼──────────────────────────┐
                   │            Vision API (main.py)             │
                   │     Receives images, returns manifest       │
                   └──────────────────┬──────────────────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
   ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
   │  YOLOv8n (x2)    │   │  CLIP Assessment │   │   Board Damage   │
   │ Component Detect │   │  Condition Check │   │   Detection      │
   │ 20+ classes      │   │  Functional/Unsafe│   │   Shorts, Burns  │
   └────────┬─────────┘   └────────┬─────────┘   └────────┬─────────┘
            │                      │                      │
            └──────────────────────┼──────────────────────┘
                                   ▼
                   ┌──────────────────────────────────────────┐
                   │         Grounding Cascade                │
                   │  OCR -> Nexar API -> RAG (Semantic)      │
                   │  Maps parts to accurate specifications   │
                   └──────────────────┬───────────────────────┘
                                      ▼
                   ┌──────────────────────────────────────────┐
                   │              SQLite Vault                │
                   │  Stores all teardowns + parts            │
                   │  Semantic search across inventory        │
                   └──────────────────────────────────────────┘
                                      ▼
                   ┌──────────────────────────────────────────┐
                   │     Circuit Design Agent (LangGraph)     │
                   │  Designs reuse circuits from vaulted     │
                   │  components with ERC/SPICE verification  │
                   └──────────────────────────────────────────┘
```

## System Workflow

1. **Upload** &mdash; User uploads PCB photos through the web UI or API
2. **Detection** &mdash; Two YOLOv8n models run simultaneously:
   - Component detection (20 classes: resistors, capacitors, ICs, sensors, etc.)
   - Board damage detection (6 classes: shorts, burns, corrosion, etc.)
3. **Verification** &mdash; CLIP zero-shot classifier filters false positives by verifying each detected crop actually contains the expected component
4. **Condition Assessment** &mdash; CLIP classifies each component as functional, repairable, or unsafe based on visual condition (no external API needed)
5. **Spec Grounding** &mdash; A cascade of OCR -> Nexar part lookup -> RAG semantic search identifies the component and retrieves specifications
6. **Vault Storage** &mdash; Results are stored in a SQLite database with embedding-based semantic search for future retrieval
7. **Export** &mdash; Users can export repair guides as HTML reports

## Dependencies

Install from `pyproject.toml`:

```bash
pip install -e .
```

Core dependencies:
- Python 3.9+
- ultralytics (YOLOv8n)
- transformers (CLIP zero-shot classification)
- sentence-transformers (RAG embeddings)
- fastapi + uvicorn (web server)
- pydantic (data validation)
- opencv-python (image processing)
- Pillow (image handling)
- sqlalchemy (vault database)
- langgraph (circuit design agent)
- numpy

## Project Structure

```
E:/
├── frankenstein/                 # Main Python package
│   ├── schema.py                 # Pydantic data models (PartsManifest, etc.)
│   ├── engine.py                 # Member 2: Circuit design engine
│   ├── foreman.py                # LangGraph workflow orchestration
│   ├── llm.py                    # LLM client + StubLLM for testing
│   ├── mocks.py                  # Mock data for testing
│   ├── agents/
│   │   ├── circuit_design.py     # Circuit design agent
│   │   └── inspector.py          # Design inspection agent
│   ├── vision/
│   │   ├── api/main.py           # FastAPI server (entry point)
│   │   ├── api/demo_data.py      # Demo scenario definitions
│   │   ├── triage_pipeline.py    # Full analysis pipeline
│   │   ├── detector.py           # YOLOv8n model wrapper
│   │   ├── condition_assessor.py # CLIP-based + Gemini condition assessment
│   │   ├── local_verifier.py     # CLIP-based false positive filter
│   │   ├── damage_inference.py   # Board damage rules
│   │   ├── grounding/
│   │   │   ├── cascade.py        # OCR -> Nexar -> RAG cascade
│   │   │   ├── ocr_reader.py     # OCR text extraction
│   │   │   ├── nexar_client.py   # Nexar part database client
│   │   │   └── rag_fallback.py   # Semantic search knowledge base
│   │   └── vault/
│   │       ├── repository.py     # CRUD + semantic search
│   │       ├── models.py         # SQLAlchemy models
│   │       └── embedder.py       # Embedding generation
│   ├── verification/             # Circuit verification modules
│   │   ├── erc.py                # Electrical Rules Check
│   │   ├── spice.py              # SPICE simulation
│   │   ├── z3_check.py           # Z3 constraint checking
│   │   └── compile_check.py      # Arduino firmware analysis
│   └── __init__.py
├── frontend/
│   ├── index.html                # Web UI
│   ├── app.js                    # Frontend logic
│   └── style.css                 # Styling
├── tests/                        # Test suite (57 tests)
├── weights/                      # YOLO model weights (not in git)
├── pyproject.toml                # Python dependencies
└── README.md
```

## Setup

### Prerequisites
- Python 3.9 or higher
- Git

### Installation

```bash
# Clone the repository
git clone https://github.com/Rahulxoxo69/frankenstein.git
cd frankenstein

# Install dependencies
pip install -e .

# Download YOLO weights (place in weights/ directory)
# - component_id_best.pt (20-class component detection)
# - board_damage_best.pt (6-class board damage detection)
```

### Running the Server

```bash
# Windows
cd frankenstein
set PYTHONPATH=.
python vision/api/main.py

# Linux/Mac
cd frankenstein
PYTHONPATH=. python vision/api/main.py
```

Open http://localhost:8000 in your browser.

## Usage

### Web UI
1. Open http://localhost:8000
2. Upload PCB photos (drag & drop or click to browse)
3. Click "Analyze" to run the full pipeline
4. View detected components with status, specs, and reuse suggestions
5. Check the Vault tab for accumulated inventory
6. Export repair guides as HTML

### Demo Scenarios
The app includes 4 pre-built demos for testing without uploading images:

| Scenario | Device | Contents |
|---|---|---|
| Broken Phone | Galaxy S22 | 10 components, 2 board defects |
| Coffee Machine | Breville BES870 | 10 components, 2 board defects |
| Laptop Board | Dell XPS 13 | 9 components, 2 board defects |
| FPV Drone | 5" Racing Quad | 8 components, 1 board defect |

Click any demo card on the homepage to see the full pipeline in action.

### API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | /api/health | Server health check |
| POST | /api/teardown | Upload images for analysis |
| GET | /api/vault/parts | List all vaulted parts |
| GET | /api/vault/stats | Vault statistics |
| POST | /api/vault/mark | Mark part as used/unused |
| DELETE | /api/vault/parts/{id} | Delete a part from vault |
| GET | /api/vault/search?q=... | Semantic search over vault |
| POST | /api/demo/run/{scenario} | Run demo scenario |
| GET | /api/report/{teardown_id} | Get repair report |
| GET | /api/sample-images | List sample images |

## Testing

```bash
cd frankenstein
PYTHONPATH=. python -m pytest tests/ -v
```

57 tests covering: schema validation, ERC rules, SPICE checks, Z3 constraints, foreman workflow, end-to-end pipeline, mock data validation.

## Key Notes

- **No API keys required** &mdash; All AI models run locally (YOLOv8n, CLIP, sentence-transformers)
- **Condition assessment** uses CLIP zero-shot classification as fallback when no Gemini API key is configured
- **Demo works out of the box** &mdash; No setup beyond installing dependencies
- **The vault persists between sessions** &mdash; Uses SQLite database in the project directory
- **Model weights are not included in git** &mdash; Place them in the weights/ directory
