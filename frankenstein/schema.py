"""Parts manifest schema — THE contract between Member 1 and Member 2.

This file is the single source of truth for what crosses the boundary.
Member 1's vision pipeline emits lists of PartsManifest; Member 2's
Frankenstein engine consumes them. Any change here is a visible diff.

Versioned via SCHEMA_VERSION so Member 1 and Member 2 can pin compatibility
in CI: a manifest with schema_version > ENGINE_MAX_SCHEMA fails fast
instead of crashing deeper in the pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "0.1.0"


# --- enums ---

class PartStatus(str, Enum):
    """Three-tier disposition decided by Member 1's vision pipeline.

    functional:  clean, drop into the design as-is.
    repairable:  damaged but fixable; needs repair_note describing the fix,
                 Member 2's Circuit Design Agent must decide whether to
                 design around the current state or assume the fix.
    unsafe:      physically unsafe (leaking cap, cracked PCB near mains,
                 leaded solder on food-contact device, etc.) — never
                 reaches Member 2, gets routed to disposal.
    """

    FUNCTIONAL = "functional"
    REPAIRABLE = "repairable"
    UNSAFE = "unsafe"


class PartSource(str, Enum):
    """Where this manifest came from.

    photo:  today's intake photo, fresh detection.
    vault:  pulled from a previously-validated parts vault (Member 1 has
            pgvector embeddings + Nexar specs already indexed).
    mock:   hand-written for development / testing — Member 2 only.
    """

    PHOTO = "photo"
    VAULT = "vault"
    MOCK = "mock"


class SpecSource(str, Enum):
    """Where the spec data came from within a part's specs block.

    nexar:      Nexar API datasheet lookup.
    datasheet:  PDF/HTML datasheet OCR'd by Member 1's pipeline.
    measured:   physical measurement (multimeter, calipers) — highest trust.
    inferred:   LLM/VLM guess from appearance alone — lowest trust.
    """

    NEXAR = "nexar"
    DATASHEET = "datasheet"
    MEASURED = "measured"
    INFERRED = "inferred"
    RAG = "rag"
    OCR_MANUAL = "ocr_manual"
    UNKNOWN = "unknown"


# --- core models ---

class PartSpecs(BaseModel):
    """Electrical / physical specs known about a part.

    All fields optional except source — Member 1 may not have every spec
    for every part (e.g. vault entries from older intake sessions).
    """

    model_config = ConfigDict(extra="allow")  # forward-compat for new specs

    # electrical
    voltage: Optional[str] = Field(default=None, description="Operating voltage, e.g. '3.3V', '5V', '12-24V'")
    voltage_min: Optional[float] = Field(default=None, description="Min supply voltage in volts")
    voltage_max: Optional[float] = Field(default=None, description="Max supply voltage in volts")
    current_ma: Optional[float] = Field(default=None, description="Typical operating current in mA")
    io_voltage: Optional[str] = Field(default=None, description="I/O logic level, e.g. '3.3V', '5V'")
    pinout: Optional[dict[str, Any]] = Field(default=None, description="Pin name -> function map")

    # physical
    package: Optional[str] = Field(default=None, description="Package code, e.g. 'SOT-23', 'DIP-28', '0805'")
    dimensions_mm: Optional[dict[str, float]] = Field(default=None, description="l/w/h in mm")

    # procurement / metadata
    datasheet_url: Optional[str] = None
    current_rating: Optional[str] = Field(default=None, description="Current rating e.g. 500mA")
    part_number: Optional[str] = Field(default=None, description="OCR-read part number from silkscreen")
    raw: Optional[Dict[str, Any]] = Field(default=None, description="Raw OCR / Nexar response")
    alternate_names: list[str] = Field(default_factory=list)

    # trust trail
    source: SpecSource = Field(description="Where these specs came from")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Spec-level confidence")


class PartsManifest(BaseModel):
    """A single part, validated by Member 1, ready for Member 2.

    Member 1 emits this. Member 2 only ever reads instances of this.
    """

    model_config = ConfigDict(extra="forbid")  # contract is strict — no surprises

    schema_version: Literal["0.1.0"] = SCHEMA_VERSION

    part_id: str = Field(description="Stable ID, e.g. 'esp32_01' or vault UUID")
    name: str = Field(description="Human-readable name, e.g. 'ESP32-WROOM-32'")
    category: Optional[str] = Field(default=None, description="Component category e.g. microcontroller, passive")
    status: PartStatus

    confidence: float = Field(ge=0.0, le=1.0, description="Detection + status confidence")
    detection: Optional[DetectionInfo] = Field(default=None, description="YOLO detection metadata from vision pipeline")
    source: PartSource

    specs: PartSpecs

    repair_note: Optional[str] = Field(
        default=None,
        description=(
            "Required when status == REPAIRABLE. Free-text describing what's "
            "wrong and the fix (e.g. 'frayed lead on pin 3, resolder before use'). "
            "Member 2's Circuit Design Agent reads this to decide whether to "
            "design around the damage or assume the fix happens first."
        ),
    )

    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    photo_id: Optional[str] = None
    vault_entry_id: Optional[str] = None

    disposal_reason: Optional[str] = Field(
        default=None,
        description="Required when status == UNSAFE. Why this can't be reused.",
    )
    description: Optional[str] = Field(default=None, description="Human-readable component description")
    reuse_suggestion: Optional[str] = Field(default=None, description="Suggested reuse project or application")

    @model_validator(mode="after")
    def _cross_field_validation(self) -> "PartsManifest":
        if self.status == PartStatus.REPAIRABLE and not self.repair_note:
            raise ValueError("repair_note is required when status == repairable")
        if self.status != PartStatus.REPAIRABLE and self.repair_note:
            raise ValueError("repair_note is only allowed when status == repairable")
        if self.status == PartStatus.UNSAFE and not self.disposal_reason:
            raise ValueError("disposal_reason is required when status == unsafe")
        return self


class ManifestBundle(BaseModel):
    """A batch of parts — typically one intake photo's worth of detections.

    Member 2's engine operates on bundles, not single parts: the Circuit
    Design Agent needs to know what else is on the table to pick a design.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["0.1.0"] = SCHEMA_VERSION
    bundle_id: str
    detected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parts: list[PartsManifest] = Field(min_length=1)

    def usable(self) -> list[PartsManifest]:
        """Filter to parts Member 2 is allowed to design with."""
        return [p for p in self.parts if p.status != PartStatus.UNSAFE]

    def repairable(self) -> list[PartsManifest]:
        return [p for p in self.parts if p.status == PartStatus.REPAIRABLE]

    def unsafe(self) -> list[PartsManifest]:
        return [p for p in self.parts if p.status == PartStatus.UNSAFE]


# --- compatibility check ---

ENGINE_MAX_SCHEMA = "0.1.0"


def _semver_tuple(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def is_compatible(manifest_schema_version: str) -> bool:
    return _semver_tuple(manifest_schema_version) <= _semver_tuple(ENGINE_MAX_SCHEMA)


# === Vision pipeline types (Member 1) ===

class BoundingBox(BaseModel):
    x_center: float = Field(..., ge=0.0, le=1.0)
    y_center: float = Field(..., ge=0.0, le=1.0)
    width: float = Field(..., ge=0.0, le=1.0)
    height: float = Field(..., ge=0.0, le=1.0)


class DetectionInfo(BaseModel):
    yolo_class: str = Field(..., description="Class label from YOLO model")
    yolo_confidence: float = Field(..., ge=0.0, le=1.0)
    bbox: BoundingBox
    vision_llm_assessment: Optional[str] = Field(default=None, description="Free-text condition assessment from Gemini")


class BoardDamage(BaseModel):
    """Context-level summary of overall board damage assessment."""

    damaged: bool = Field(default=False)
    categories: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    description: Optional[str] = None


class BoardDefect(BaseModel):
    """A single board-level defect detected by the YOLO damage model."""

    defect_type: str = Field(..., description="Defect class: open, short, mousebite, spur, pinhole, spurious_copper")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Detection confidence")
    bbox: Optional[BoundingBox] = Field(default=None, description="Normalised bounding box of the defect")
    affects_part: Optional[str] = Field(default=None, description="part_id of overlapping component, if any")


class TeardownContext(BaseModel):
    device_model: Optional[str] = None
    image_path: Optional[str] = None
    notes: Optional[str] = None
    damage: Optional[BoardDamage] = None
    failure_cause: Optional[str] = None
    available_tools: List[str] = Field(default_factory=list)
    skill_level: int = 3


class TeardownManifest(BaseModel):
    teardown_id: str = Field(..., description="Unique identifier for this teardown session")
    context: TeardownContext
    parts: List[PartsManifest]
    schema_version: str = Field(default=SCHEMA_VERSION)
    board_damages: List[BoardDefect] = Field(default_factory=list, description="Board-level defects detected by YOLO damage model")
    image_paths: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_manifest_bundle(self) -> ManifestBundle:
        return ManifestBundle(
            bundle_id=self.teardown_id,
            parts=self.parts,
            schema_version=self.schema_version,
        )
