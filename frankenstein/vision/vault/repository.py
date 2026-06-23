"""
Draft Vault Repository
======================
CRUD + semantic search over the vaulted parts database.

This is the interface between the pipeline and the vault:
  - store_teardown()   → saves a TeardownManifest's parts to the vault
  - search()           → semantic search ("find me a 5V microcontroller")
  - get_available()    → list available parts by category/status
  - mark_used()        → mark a part as used in a build
  - get_part()         → retrieve a specific part by ID

The vault is what makes cross-teardown builds possible:
  "I need an ESP32" → searches ALL past teardowns → finds one from 3 weeks ago.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from frankenstein.schema import (
    PartsManifest, PartSource, PartSpecs, PartStatus,
    TeardownContext, TeardownManifest,
)

from .models import (
    Base, TeardownSessionModel, VaultedPartModel,
    create_vault_engine, create_vault_session,
)
from .embedder import VaultEmbedder


# ── Search Result ────────────────────────────────────────────────────────────

class VaultSearchResult:
    """A part found via semantic search."""
    def __init__(self, part: VaultedPartModel, similarity: float):
        self.part = part
        self.similarity = similarity

    def __repr__(self) -> str:
        return (f"<VaultResult {self.part.part_id}: {self.part.name} "
                f"({self.similarity:.2f} similarity)>")


# ── Repository ───────────────────────────────────────────────────────────────

class VaultRepository:
    """
    CRUD + semantic search for the Draft Vault.

    Usage:
        vault = VaultRepository("vault.db")

        # Store a teardown
        vault.store_teardown(manifest)

        # Search for parts
        results = vault.search("5V microcontroller with WiFi")
        for r in results:
            print(f"{r.part.name}: {r.similarity:.2f}")

        # Get available parts
        parts = vault.get_available(category="microcontroller")
    """

    def __init__(self, db_path: str = "vault.db"):
        self.db_path = db_path
        self.engine = create_vault_engine(db_path)
        self.embedder = VaultEmbedder()
        print(f"[Vault] Database: {db_path}")

    def _session(self):
        """Create a new database session."""
        return create_vault_session(self.engine)

    # ── Store ────────────────────────────────────────────────────────────

    def store_teardown(self, manifest: TeardownManifest) -> int:
        """
        Store all parts from a teardown manifest into the vault.

        Args:
            manifest: TeardownManifest from the triage pipeline

        Returns:
            Number of parts stored
        """
        session = self._session()
        try:
            # Create session record
            teardown = TeardownSessionModel(
                id=manifest.teardown_id,
                device_model=manifest.context.device_model,
                failure_cause=manifest.context.failure_cause,
                skill_level=manifest.context.skill_level,
                available_tools=json.dumps(manifest.context.available_tools),
                image_paths=json.dumps(manifest.image_paths),
                created_at=manifest.created_at,
            )
            session.add(teardown)

            # Store each part
            stored = 0
            for part in manifest.parts:
                # Generate embedding
                specs_dict = part.specs.model_dump() if part.specs else None
                embedding = self.embedder.embed_part(
                    name=part.name,
                    category=part.category or "",
                    specs=specs_dict,
                    status=part.status.value,
                )

                vaulted = VaultedPartModel(
                    part_id=f"{manifest.teardown_id}_{part.part_id}",
                    name=part.name,
                    category=part.category,
                    status=part.status.value,
                    confidence=part.confidence,
                    specs_json=part.specs.model_dump_json() if part.specs else None,
                    repair_note=part.repair_note,
                    disposal_reason=part.disposal_reason,
                    detection_json=part.detection.model_dump_json() if part.detection else None,
                    embedding=self.embedder.to_blob(embedding) if embedding is not None else None,
                    session_id=manifest.teardown_id,
                    detected_at=part.detected_at,
                    is_available=part.status != PartStatus.UNSAFE,
                )
                session.add(vaulted)
                stored += 1

            session.commit()
            print(f"[Vault] Stored {stored} parts from teardown {manifest.teardown_id}")
            return stored

        except Exception as e:
            session.rollback()
            print(f"[Vault] Error storing teardown: {e}")
            raise
        finally:
            session.close()

    # ── Search ───────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 10,
        min_similarity: float = 0.3,
        status_filter: Optional[str] = None,
        available_only: bool = True,
    ) -> list[VaultSearchResult]:
        """
        Semantic search over vaulted parts.

        Args:
            query: Natural language query (e.g., "5V motor driver")
            top_k: Maximum results to return
            min_similarity: Minimum cosine similarity threshold
            status_filter: Filter by status ("functional", "repairable")
            available_only: Only return parts not yet used in a build

        Returns:
            List of VaultSearchResult sorted by similarity (descending)
        """
        # Embed query
        query_embedding = self.embedder.embed_query(query)
        if query_embedding is None:
            print("[Vault] No embedding model — falling back to text search")
            return self._text_search(query, top_k, status_filter, available_only)

        session = self._session()
        try:
            # Fetch all candidate parts
            q = session.query(VaultedPartModel).filter(
                VaultedPartModel.embedding.isnot(None)
            )
            if available_only:
                q = q.filter(VaultedPartModel.is_available == True)
            if status_filter:
                q = q.filter(VaultedPartModel.status == status_filter)

            candidates = q.all()

            if not candidates:
                return []

            # Compute similarities
            results = []
            for part in candidates:
                part_embedding = self.embedder.from_blob(part.embedding, self.embedder.dim)
                similarity = self.embedder.cosine_similarity(query_embedding, part_embedding)
                if similarity >= min_similarity:
                    results.append(VaultSearchResult(part=part, similarity=similarity))

            # Sort by similarity descending
            results.sort(key=lambda r: r.similarity, reverse=True)
            return results[:top_k]

        finally:
            session.close()

    def _text_search(
        self,
        query: str,
        top_k: int = 10,
        status_filter: Optional[str] = None,
        available_only: bool = True,
    ) -> list[VaultSearchResult]:
        """Fallback text-based search when embeddings aren't available."""
        session = self._session()
        try:
            q = session.query(VaultedPartModel)
            if available_only:
                q = q.filter(VaultedPartModel.is_available == True)
            if status_filter:
                q = q.filter(VaultedPartModel.status == status_filter)

            # Simple LIKE search
            query_lower = f"%{query.lower()}%"
            q = q.filter(
                (VaultedPartModel.name.ilike(query_lower)) |
                (VaultedPartModel.category.ilike(query_lower)) |
                (VaultedPartModel.specs_json.ilike(query_lower))
            )

            parts = q.limit(top_k).all()
            return [VaultSearchResult(part=p, similarity=0.5) for p in parts]
        finally:
            session.close()

    # ── Query Helpers ────────────────────────────────────────────────────

    def get_available(
        self,
        category: Optional[str] = None,
        status: str = "functional",
    ) -> list[VaultedPartModel]:
        """Get all available parts, optionally filtered by category."""
        session = self._session()
        try:
            q = session.query(VaultedPartModel).filter(
                VaultedPartModel.is_available == True,
                VaultedPartModel.status == status,
            )
            if category:
                q = q.filter(VaultedPartModel.category.ilike(f"%{category}%"))
            return q.all()
        finally:
            session.close()

    def get_part(self, part_id: str) -> Optional[VaultedPartModel]:
        """Get a specific part by its vault ID."""
        session = self._session()
        try:
            return session.query(VaultedPartModel).filter_by(part_id=part_id).first()
        finally:
            session.close()

    def get_all_parts(self, available_only: bool = False) -> list[VaultedPartModel]:
        """Get all parts in the vault."""
        session = self._session()
        try:
            q = session.query(VaultedPartModel)
            if available_only:
                q = q.filter(VaultedPartModel.is_available == True)
            return q.all()
        finally:
            session.close()

    def mark_used(self, part_id: str, build_id: str) -> bool:
        """Mark a part as used in a build (no longer available)."""
        session = self._session()
        try:
            part = session.query(VaultedPartModel).filter_by(part_id=part_id).first()
            if part:
                part.is_available = False
                part.used_in_build = build_id
                session.commit()
                return True
            return False
        finally:
            session.close()

    def delete_part(self, part_id: str) -> bool:
        """Delete a part from the vault."""
        session = self._session()
        try:
            part = session.query(VaultedPartModel).filter_by(part_id=part_id).first()
            if part:
                session.delete(part)
                session.commit()
                return True
            return False
        except Exception as e:
            session.rollback()
            print(f"[Vault] Error deleting part: {e}")
            return False
        finally:
            session.close()

    def mark_available(self, part_id: str) -> bool:
        """Mark a part as available again (e.g., build was cancelled)."""
        session = self._session()
        try:
            part = session.query(VaultedPartModel).filter_by(part_id=part_id).first()
            if part:
                part.is_available = True
                part.used_in_build = None
                session.commit()
                return True
            return False
        finally:
            session.close()

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Get vault statistics."""
        session = self._session()
        try:
            total = session.query(VaultedPartModel).count()
            available = session.query(VaultedPartModel).filter_by(is_available=True).count()
            functional = session.query(VaultedPartModel).filter_by(status="functional", is_available=True).count()
            repairable = session.query(VaultedPartModel).filter_by(status="repairable", is_available=True).count()
            unsafe = session.query(VaultedPartModel).filter_by(status="unsafe").count()
            sessions = session.query(TeardownSessionModel).count()

            # Category breakdown
            categories = {}
            for part in session.query(VaultedPartModel).filter_by(is_available=True).all():
                cat = part.category or "unknown"
                categories[cat] = categories.get(cat, 0) + 1

            return {
                "total_parts": total,
                "available": available,
                "functional": functional,
                "repairable": repairable,
                "unsafe": unsafe,
                "teardown_sessions": sessions,
                "categories": categories,
            }
        finally:
            session.close()

    def to_manifest_part(self, part: VaultedPartModel) -> PartsManifest:
        """Convert a vaulted part back to a PartsManifest (for Member 2)."""
        specs = None
        if part.specs_json:
            specs = PartSpecs.model_validate_json(part.specs_json)

        return PartsManifest(
            part_id=part.part_id,
            name=part.name,
            category=part.category,
            status=PartStatus(part.status),
            confidence=part.confidence,
            source=PartSource.VAULT,
            specs=specs,
            repair_note=part.repair_note,
            disposal_reason=part.disposal_reason,
            detected_at=part.detected_at,
        )
