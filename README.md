# Frankenstein

AI-powered e-waste component recovery system. Upload photos of damaged electronics, identify salvageable components, assess their condition, and maintain a searchable inventory for reuse.

## Features

- **Component Detection** &mdash; YOLOv8n identifies 20+ electronic components from PCB photos
- **Damage Detection** &mdash; Separate YOLOv8n model detects board defects (shorts, burns, corrosion)
- **Condition Assessment** &mdash; CLIP zero-shot classification assesses part condition locally
- **Spec Grounding** &mdash; RAG-based semantic search grounds detected parts with accurate specs
- **Smart Vault** &mdash; SQLite-backed inventory with semantic search across all teardowns
- **Circuit Designer** &mdash; LangGraph agent designs reuse circuits from salvaged components
- **Web UI** &mdash; FastAPI frontend with upload, analysis pipeline, and vault management

## Quick Start

```bash
pip install -e .
cd frankenstein
PYTHONPATH=. python vision/api/main.py
```

Open http://localhost:8000

## Demo Scenarios

| Scenario | Device | Description |
|---|---|---|
| Broken Phone | Galaxy S22 | 10 parts, 2 board defects |
| Coffee Machine | Breville BES870 | 10 parts, 2 board defects |
| Laptop Board | Dell XPS 13 | 9 parts, 2 board defects |
| FPV Drone | 5" Racing Quad | 8 parts, 1 board defect |

## Architecture

```
Uploads -> YOLOv8n (detection) -> CLIP (assessment) -> RAG (grounding) -> Vault
                                  |
                        Board Damage YOLO -> Defect Mapping
```

## Tech Stack

- YOLOv8n (Ultralytics)
- CLIP (OpenAI)
- Sentence-Transformers
- FastAPI + Uvicorn
- SQLite
- LangGraph

## Notes

- No API keys required &mdash; all AI runs locally
- Works out of the box with demo scenarios
- Designed for e-waste recycling, repair shops, and maker spaces
