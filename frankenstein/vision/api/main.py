"""
Frankenstein Vision API
=======================
FastAPI backend that ties the full pipeline together:
  POST /api/teardown       → upload photos → run triage → return manifest
  GET  /api/vault/search   → semantic search over stored parts
  GET  /api/vault/stats    → vault dashboard data
  GET  /api/vault/parts    → list vaulted parts
  POST /api/vault/mark     → mark part used/available
"""

from __future__ import annotations

import io
import json
import os
import sys
import uuid
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# Add src to path
PROJECT_ROOT = Path(__file__).resolve().parents[3]

from frankenstein.schema import TeardownContext, TeardownManifest
from frankenstein.vision.triage_pipeline import VisionTriagePipeline
from frankenstein.vision.vault.repository import VaultRepository
from frankenstein.vision.api.demo_data import DEMO_SCENARIOS


# ── App Setup ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Frankenstein Vision API",
    description="E-Waste Component Triage & Draft Vault",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Lazy-init globals ────────────────────────────────────────────────────────

_pipeline: Optional[VisionTriagePipeline] = None
_vault: Optional[VaultRepository] = None

UPLOAD_DIR = PROJECT_ROOT / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

VAULT_DB = str(PROJECT_ROOT / "vault.db")


def get_pipeline() -> VisionTriagePipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = VisionTriagePipeline(
            component_weights=str(PROJECT_ROOT / "weights" / "component_id_best.pt"),
            damage_weights=str(PROJECT_ROOT / "weights" / "board_damage_best.pt"),
        )
    return _pipeline


def get_vault() -> VaultRepository:
    global _vault
    if _vault is None:
        _vault = VaultRepository(VAULT_DB)
    return _vault


# ── Serve Frontend ───────────────────────────────────────────────────────────

FRONTEND_DIR = PROJECT_ROOT / "frontend"

@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")


# Mount static files AFTER route definitions
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ── Sample Images for Demo ───────────────────────────────────────────────────

SAMPLE_DIR = PROJECT_ROOT / "datasets" / "electrocom61" / "test" / "images"

@app.get("/api/sample-images")
async def list_sample_images():
    """List available sample images for demo."""
    if not SAMPLE_DIR.exists():
        return {"images": []}
    imgs = [f.name for f in sorted(SAMPLE_DIR.glob("*.jpg"))[:8]]
    return {"images": imgs, "count": len(imgs)}


@app.get("/api/sample-images/{filename}")
async def get_sample_image(filename: str):
    """Serve a sample image."""
    path = SAMPLE_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Sample image not found")
    return FileResponse(path, media_type="image/jpeg")


# ── Teardown Endpoint ────────────────────────────────────────────────────────

@app.post("/api/teardown")
async def create_teardown(
    images: list[UploadFile] = File(...),
    device_model: str = Form("Unknown Device"),
    failure_cause: str = Form(""),
    skill_level: int = Form(3),
    available_tools: str = Form("[]"),
):
    """
    Upload images → run full triage pipeline → return manifest.
    Also stores results in the vault automatically.
    """
    if not images:
        raise HTTPException(400, "No images uploaded")

    # Save uploaded files
    saved_paths = []
    teardown_id = f"tdn_{uuid.uuid4().hex[:8]}"
    teardown_dir = UPLOAD_DIR / teardown_id
    teardown_dir.mkdir(exist_ok=True)

    for img_file in images:
        file_path = teardown_dir / img_file.filename
        content = await img_file.read()
        with open(file_path, "wb") as f:
            f.write(content)
        saved_paths.append(str(file_path))

    # Parse tools
    try:
        tools_list = json.loads(available_tools)
    except json.JSONDecodeError:
        tools_list = [t.strip() for t in available_tools.split(",") if t.strip()]

    # Build context
    context = TeardownContext(
        device_model=device_model,
        failure_cause=failure_cause or "Not specified",
        available_tools=tools_list or ["multimeter"],
        skill_level=skill_level,
    )

    # Run pipeline
    pipeline = get_pipeline()
    try:
        manifest = pipeline.process_teardown(
            image_paths=saved_paths,
            context=context,
            teardown_id=teardown_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Store in vault
    vault = get_vault()
    vault.store_teardown(manifest)

    # Return JSON
    return JSONResponse(content=json.loads(manifest.model_dump_json()))


# ── Vault Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/vault/search")
async def vault_search(
    q: str = Query(..., description="Search query"),
    top_k: int = Query(10, ge=1, le=50),
    status: Optional[str] = Query(None),
    available_only: bool = Query(True),
):
    """Semantic search over vaulted parts."""
    vault = get_vault()
    results = vault.search(
        query=q,
        top_k=top_k,
        status_filter=status,
        available_only=available_only,
    )

    return {
        "query": q,
        "count": len(results),
        "results": [
            {
                "part_id": r.part.part_id,
                "name": r.part.name,
                "category": r.part.category,
                "status": r.part.status,
                "confidence": r.part.confidence,
                "similarity": round(r.similarity, 4),
                "specs": r.part.specs,
                "repair_note": r.part.repair_note,
                "is_available": r.part.is_available,
            }
            for r in results
        ],
    }


@app.get("/api/vault/stats")
async def vault_stats():
    """Get vault statistics."""
    vault = get_vault()
    return vault.stats()


@app.get("/api/vault/parts")
async def vault_parts(
    available_only: bool = Query(False),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """List all vaulted parts."""
    vault = get_vault()

    if category or status:
        parts = vault.get_available(category=category, status=status or "functional")
    else:
        parts = vault.get_all_parts(available_only=available_only)

    return {
        "count": len(parts),
        "parts": [
            {
                "part_id": p.part_id,
                "name": p.name,
                "category": p.category,
                "status": p.status,
                "confidence": p.confidence,
                "specs": p.specs,
                "repair_note": p.repair_note,
                "disposal_reason": p.disposal_reason,
                "is_available": p.is_available,
                "used_in_build": p.used_in_build,
                "session_id": p.session_id,
            }
            for p in parts
        ],
    }


@app.delete("/api/vault/parts/{part_id}")
async def vault_delete_part(part_id: str):
    """Delete a part from the vault."""
    vault = get_vault()
    ok = vault.delete_part(part_id)
    if not ok:
        raise HTTPException(404, f"Part not found: {part_id}")
    return {"status": "ok", "part_id": part_id, "action": "deleted"}


@app.post("/api/vault/mark")
async def vault_mark(
    part_id: str = Form(...),
    action: str = Form(...),  # "used" or "available"
    build_id: str = Form(""),
):
    """Mark a part as used or available."""
    vault = get_vault()

    if action == "used":
        if not build_id:
            build_id = f"build_{uuid.uuid4().hex[:6]}"
        ok = vault.mark_used(part_id, build_id)
    elif action == "available":
        ok = vault.mark_available(part_id)
    else:
        raise HTTPException(400, f"Unknown action: {action}")

    if not ok:
        raise HTTPException(404, f"Part not found: {part_id}")

    return {"status": "ok", "part_id": part_id, "action": action}


@app.get("/api/report/{teardown_id}")
async def get_repair_report(teardown_id: str):
    """Generate a structured repair guide from a teardown."""
    vault = get_vault()
    # Get all parts for this session
    parts = [p for p in vault.get_all_parts() if p.session_id == teardown_id]
    
    if not parts:
        raise HTTPException(404, f"No parts found for teardown {teardown_id}")
        
    functional = [p for p in parts if p.status == "functional"]
    repairable = [p for p in parts if p.status == "repairable"]
    unsafe = [p for p in parts if p.status == "unsafe"]
    
    # Estimate salvage value ($0.50 per functional part, $0.10 per repairable)
    salvage_value = (len(functional) * 0.50) + (len(repairable) * 0.10)
    
    return {
        "teardown_id": teardown_id,
        "summary": {
            "total_components": len(parts),
            "functional_count": len(functional),
            "repairable_count": len(repairable),
            "unsafe_count": len(unsafe),
            "estimated_salvage_value_usd": round(salvage_value, 2),
            "difficulty_rating": "High" if len(repairable) > 2 else "Medium" if repairable else "Low"
        },
        "repair_instructions": [
            {
                "part": p.name,
                "note": p.repair_note or "Inspect thoroughly before reuse.",
            } for p in repairable
        ],
        "safety_warnings": [
            {
                "part": p.name,
                "reason": p.disposal_reason or "Unsafe for reuse. Dispose according to e-waste regulations.",
            } for p in unsafe
        ]
    }


# ── Demo Endpoints ───────────────────────────────────────────────────────────

@app.get("/api/demo/scenarios")
async def list_demo_scenarios():
    """List available demo scenarios."""
    return {
        "scenarios": [
            {
                "id": sid,
                "name": info["name"],
                "icon": info["icon"],
                "desc": info["desc"],
            }
            for sid, info in DEMO_SCENARIOS.items()
        ]
    }


@app.post("/api/demo/run/{scenario_id}")
async def run_demo_scenario(scenario_id: str):
    """Run a demo scenario — returns a realistic manifest without needing images."""
    if scenario_id not in DEMO_SCENARIOS:
        raise HTTPException(404, f"Unknown scenario: {scenario_id}. Available: {list(DEMO_SCENARIOS.keys())}")

    manifest = DEMO_SCENARIOS[scenario_id]["fn"]()

    # Also store in vault
    try:
        vault = get_vault()
        vault.store_teardown(manifest)
    except Exception as e:
        print(f"[Demo] Vault store warning: {e}")

    return JSONResponse(content=json.loads(manifest.model_dump_json()))


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "version": "2.0.0",
        "models_loaded": _pipeline is not None,
        "vault_ready": _vault is not None,
    }


# ── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print(f"Starting Frankenstein Vision API on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
