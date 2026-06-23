# Frankenstein

AI-powered e-waste component recovery system. Upload photos of damaged electronics PCBs, identify salvageable components, assess their condition, and maintain a searchable inventory of reusable parts.

## Architecture

```
                   +---------------------------------------------+
                   |              Web UI (Frontend)              |
                   |     Upload Images / View Results / Vault    |
                   +------------------+--------------------------+
                                      | HTTP (FastAPI)
                   +------------------+--------------------------+
                   |            Vision API (main.py)             |
                   |     Receives images, returns manifest       |
                   +------------------+--------------------------+
                                      |
              +-----------------------+-----------------------+-------+
              v                       v                       v
   +------------------+   +------------------+   +------------------+
   |  YOLOv8n (x2)    |   |  CLIP Assessment |   |   Board Damage   |
   | Component Detect |   |  Condition Check |   |   Detection      |
   | 20+ classes      |   |  Functional/Unsafe|   |   Shorts, Burns  |
   +--------+---------+   +--------+---------+   +--------+---------+
            |                      |                      |
            +----------------------+----------------------+
                                   v
                   +------------------------------------------+
                   |         Grounding Cascade                |
                   |  OCR -> Nexar API -> RAG (Semantic)      |
                   +------------------+-----------------------+
                                      v
                   +------------------------------------------+
                   |              SQLite Vault                |
                   |  Stores all teardowns + parts            |
                   |  Semantic search across inventory        |
                   +------------------------------------------+
                                      v
                   +------------------------------------------+
                   |     Circuit Design Agent (LangGraph)     |
                   |  Designs reuse circuits from vaulted     |
                   |  components with ERC/SPICE verification  |
                   +------------------------------------------+
```

## System Workflow

1. **Upload** - User uploads PCB photos through the web UI or API
2. **Detection** - Two YOLOv8n models run simultaneously (component + damage detection)
3. **Verification** - CLIP zero-shot classifier filters false positives
4. **Condition Assessment** - CLIP classifies as functional/repairable/unsafe (no API key needed)
5. **Spec Grounding** - OCR -> Nexar -> RAG semantic search retrieves specifications
6. **Vault Storage** - Results stored in SQLite with embedding search
7. **Export** - Export repair guides as HTML reports

## Setup

```bash
# Clone
git clone https://github.com/Rahulxoxo69/frankenstein.git
cd frankenstein

# Install
pip install -e .

# Run
cd frankenstein
PYTHONPATH=. python vision/api/main.py
```

Open http://localhost:8000

## Dependencies

- Python 3.9+
- ultralytics (YOLOv8n)
- transformers (CLIP)
- sentence-transformers (RAG)
- fastapi + uvicorn
- opencv-python, Pillow
- sqlalchemy
- langgraph

## Usage

1. Open http://localhost:8000
2. Upload PCB photos (drag and drop or click to browse)
3. Click Analyze
4. View detected components with status, specs, and reuse suggestions
5. Check Vault tab for inventory
6. Export repair guides as HTML

### Demo Scenarios

| Scenario | Device | Contents |
|---|---|---|
| Broken Phone | Galaxy S22 | 10 parts, 2 defects |
| Coffee Machine | Breville BES870 | 10 parts, 2 defects |
| Laptop Board | Dell XPS 13 | 9 parts, 2 defects |
| FPV Drone | 5" Racing Quad | 8 parts, 1 defect |

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | /api/health | Health check |
| POST | /api/teardown | Upload images for analysis |
| GET | /api/vault/parts | List vaulted parts |
| GET | /api/vault/stats | Vault statistics |
| DELETE | /api/vault/parts/{id} | Delete a part |
| GET | /api/vault/search?q= | Semantic search |
| POST | /api/demo/run/{scenario} | Run demo |
| GET | /api/report/{teardown_id} | Repair report |

## Testing

```bash
PYTHONPATH=. python -m pytest tests/ -v
```

57 tests covering schema validation, ERC rules, SPICE checks, Z3 constraints, foreman workflow, and end-to-end pipeline.

## Notes

- No API keys required - all AI runs locally (YOLOv8n, CLIP, sentence-transformers)
- Demo works out of the box
- Vault persists between sessions (SQLite)
- Model weights not included in git - place in weights/ directory
