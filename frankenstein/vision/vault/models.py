"""
Draft Vault — SQLAlchemy Models
================================
Stores cataloged parts from all teardown sessions so they can be
retrieved later for new builds. This is the "memory" of the system.

Uses SQLite (zero-config, no server) with embeddings stored as BLOBs
for semantic search via numpy cosine similarity.

Tables:
  - vaulted_parts:    Stored components with specs + embeddings
  - teardown_sessions: Metadata for each teardown session
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column, String, Float, Integer, Text, DateTime, LargeBinary,
    ForeignKey, Boolean, Index, create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship,
    Session, sessionmaker,
)


# ── Base ─────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Teardown Session ─────────────────────────────────────────────────────────

class TeardownSessionModel(Base):
    """Record of a teardown session."""
    __tablename__ = "teardown_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    device_model: Mapped[str] = mapped_column(String(256), nullable=False)
    failure_cause: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    skill_level: Mapped[int] = mapped_column(Integer, default=3)
    available_tools: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list
    image_paths: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON list
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    parts: Mapped[list["VaultedPartModel"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<TeardownSession {self.id}: {self.device_model}>"


# ── Vaulted Part ─────────────────────────────────────────────────────────────

class VaultedPartModel(Base):
    """
    A single component stored in the Draft Vault.

    This is the persistent version of PartsManifest — kept across sessions
    so parts can be retrieved for new builds via semantic search.
    """
    __tablename__ = "vaulted_parts"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Identity
    part_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # Triage result
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # functional/repairable/unsafe
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # Specs (JSON blob)
    specs_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Notes
    repair_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    disposal_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Detection metadata (JSON blob)
    detection_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Embedding for semantic search (binary blob of float32 array)
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)

    # Provenance
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("teardown_sessions.id"), nullable=False
    )
    source_image: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    # Availability tracking
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    used_in_build: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    # Relationship
    session: Mapped["TeardownSessionModel"] = relationship(back_populates="parts")

    # Indexes for common queries
    __table_args__ = (
        Index("idx_status", "status"),
        Index("idx_category", "category"),
        Index("idx_available", "is_available"),
        Index("idx_session", "session_id"),
    )

    def __repr__(self) -> str:
        return f"<VaultedPart {self.part_id}: {self.name} ({self.status})>"

    @property
    def specs(self) -> Optional[dict]:
        """Deserialize specs JSON."""
        if self.specs_json:
            return json.loads(self.specs_json)
        return None

    @specs.setter
    def specs(self, value: Optional[dict]):
        """Serialize specs to JSON."""
        self.specs_json = json.dumps(value) if value else None

    @property
    def detection(self) -> Optional[dict]:
        """Deserialize detection JSON."""
        if self.detection_json:
            return json.loads(self.detection_json)
        return None

    @detection.setter
    def detection(self, value: Optional[dict]):
        """Serialize detection to JSON."""
        self.detection_json = json.dumps(value) if value else None


# ── Database Setup ───────────────────────────────────────────────────────────

def create_vault_engine(db_path: str = "vault.db", echo: bool = False):
    """Create SQLite engine and tables."""
    engine = create_engine(f"sqlite:///{db_path}", echo=echo)
    Base.metadata.create_all(engine)
    return engine


def create_vault_session(engine) -> Session:
    """Create a new database session."""
    SessionFactory = sessionmaker(bind=engine)
    return SessionFactory()
