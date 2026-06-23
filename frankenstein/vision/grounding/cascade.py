"""
Grounding Cascade Orchestrator
==============================
Runs the full spec-grounding waterfall for each detected component:

  OCR (read part number) → Nexar (look up specs) → RAG (semantic fallback)

Each step only runs if the previous one didn't produce a result.
This ensures specs always come from trusted sources — never LLM memory.

The cascade produces a PartSpecs object that gets attached to the
PartsManifest, with a `source` field tracking provenance.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from frankenstein.schema import PartSpecs, SpecSource

from .ocr_reader import OCRReader, OCRResult
from .nexar_client import NexarClient, MockNexarClient, NexarPartInfo, create_nexar_client
from .rag_fallback import RAGFallback, RAGResult


# ── Cascade Result ───────────────────────────────────────────────────────────

@dataclass
class GroundingResult:
    """Result of the full grounding cascade for one component."""
    component_class: str         # YOLO class name
    ocr_result: Optional[OCRResult] = None
    nexar_result: Optional[NexarPartInfo] = None
    rag_result: Optional[RAGResult] = None
    final_specs: Optional[PartSpecs] = None
    grounding_source: str = "unknown"   # Which step succeeded
    part_number: Optional[str] = None   # Best part number found


# ── Cascade Orchestrator ─────────────────────────────────────────────────────

class GroundingCascade:
    """
    Orchestrates OCR → Nexar → RAG to ground component specs.

    Usage:
        cascade = GroundingCascade()
        result = cascade.ground(
            crop_image=component_crop,
            yolo_class="IC-Chip",
        )
        print(result.final_specs)
        print(result.grounding_source)  # "nexar", "rag", "ocr_manual", etc.
    """

    def __init__(
        self,
        ocr_reader: Optional[OCRReader] = None,
        nexar_client: Optional[NexarClient | MockNexarClient] = None,
        rag_fallback: Optional[RAGFallback] = None,
        enable_ocr: bool = True,
        enable_nexar: bool = True,
        enable_rag: bool = True,
    ):
        print("[Cascade] Initializing Grounding Cascade...")

        # Step 1: OCR
        if enable_ocr:
            self.ocr = ocr_reader or OCRReader()
        else:
            self.ocr = None
            print("  [SKIP] OCR disabled")

        # Step 2: Nexar
        if enable_nexar:
            self.nexar = nexar_client or create_nexar_client()
        else:
            self.nexar = None
            print("  [SKIP] Nexar disabled")

        # Step 3: RAG
        if enable_rag:
            self.rag = rag_fallback or RAGFallback()
        else:
            self.rag = None
            print("  [SKIP] RAG disabled")

        print("[Cascade] Ready")

    def ground(
        self,
        crop_image: Optional[np.ndarray] = None,
        yolo_class: str = "unknown",
        yolo_confidence: float = 0.0,
        gemini_description: str = "",
    ) -> GroundingResult:
        """
        Run the grounding cascade for one component.

        Args:
            crop_image: Cropped component image (for OCR)
            yolo_class: YOLO-detected class name
            yolo_confidence: YOLO detection confidence
            gemini_description: Gemini Vision's text assessment

        Returns:
            GroundingResult with final specs and provenance
        """
        result = GroundingResult(component_class=yolo_class)

        # ── Step 1: OCR ──────────────────────────────────────────────────
        if self.ocr is not None and crop_image is not None:
            ocr_result = self.ocr.read(crop_image)
            result.ocr_result = ocr_result

            if ocr_result.part_numbers:
                result.part_number = ocr_result.part_numbers[0]
                print(f"    [OCR] Found part number: {result.part_number}")

                # ── Step 2: Nexar lookup with OCR part number ────────────
                if self.nexar is not None:
                    nexar_result = self.nexar.lookup(result.part_number)
                    result.nexar_result = nexar_result

                    if nexar_result.found:
                        print(f"    [Nexar] Match: {nexar_result.part_number} "
                              f"({nexar_result.manufacturer})")
                        result.final_specs = self._nexar_to_specs(nexar_result)
                        result.grounding_source = "nexar"
                        return result
                    else:
                        print(f"    [Nexar] No match for '{result.part_number}'")

                # Nexar failed but we have a part number from OCR
                result.final_specs = PartSpecs(
                    part_number=result.part_number,
                    source=SpecSource.OCR_MANUAL,
                    raw={"ocr_text": ocr_result.raw_text},
                )
                result.grounding_source = "ocr_manual"
                # Don't return yet — try RAG for more specs

        # ── Step 3: RAG Fallback ─────────────────────────────────────────
        if self.rag is not None:
            # Build a rich query from all available info
            query_parts = [yolo_class]
            if result.ocr_result and result.ocr_result.raw_text:
                query_parts.append(result.ocr_result.raw_text)
            if gemini_description:
                query_parts.append(gemini_description)
            query = " ".join(query_parts)

            rag_result = self.rag.search(query)
            result.rag_result = rag_result

            if rag_result.found:
                print(f"    [RAG] Match: {rag_result.matched_name} "
                      f"(similarity: {rag_result.similarity:.2f})")

                # If we already have OCR specs, merge RAG into them
                if result.final_specs is not None:
                    # Merge RAG specs into existing OCR specs
                    if result.final_specs.raw is None:
                        result.final_specs.raw = {}
                    result.final_specs.raw.update(rag_result.specs)
                    # Keep OCR source since we have a part number
                else:
                    result.final_specs = self._rag_to_specs(rag_result, result.part_number)
                    result.grounding_source = "rag"

                return result
            else:
                print(f"    [RAG] No good match (best similarity: {rag_result.similarity:.2f})")

        # ── Fallback: no grounding succeeded ─────────────────────────────
        if result.final_specs is None:
            result.final_specs = PartSpecs(
                source=SpecSource.UNKNOWN,
                raw={"note": "No grounding source matched. Manual identification needed."},
            )
            result.grounding_source = "unknown"
            print(f"    [Cascade] No grounding found for {yolo_class}")

        return result

    def ground_batch(
        self,
        components: list[dict],
    ) -> list[GroundingResult]:
        """
        Ground multiple components.

        Args:
            components: List of dicts with keys:
                crop_image, yolo_class, yolo_confidence, gemini_description

        Returns:
            List of GroundingResult
        """
        results = []
        for i, comp in enumerate(components):
            print(f"  [{i+1}/{len(components)}] Grounding {comp.get('yolo_class', '?')}...")
            result = self.ground(
                crop_image=comp.get("crop_image"),
                yolo_class=comp.get("yolo_class", "unknown"),
                yolo_confidence=comp.get("yolo_confidence", 0.0),
                gemini_description=comp.get("gemini_description", ""),
            )
            results.append(result)
        return results

    # ── Conversion helpers ───────────────────────────────────────────────

    def _nexar_to_specs(self, nexar: NexarPartInfo) -> PartSpecs:
        """Convert Nexar result to PartSpecs."""
        # Extract voltage from specs
        voltage = (
            nexar.specs.get("Operating Voltage")
            or nexar.specs.get("Supply Voltage")
            or nexar.specs.get("Output Voltage")
            or nexar.specs.get("Voltage - Supply")
        )

        # Extract current
        current = (
            nexar.specs.get("Output Current")
            or nexar.specs.get("Current Rating")
            or nexar.specs.get("Current - Output")
        )

        return PartSpecs(
            part_number=nexar.part_number,
            voltage=voltage,
            current_rating=current,
            package=nexar.package,
            datasheet_url=nexar.datasheet_url,
            source=SpecSource.NEXAR,
            raw=nexar.specs if nexar.specs else None,
        )

    def _rag_to_specs(self, rag: RAGResult, part_number: Optional[str] = None) -> PartSpecs:
        """Convert RAG result to PartSpecs."""
        specs = rag.specs or {}

        voltage = (
            specs.get("voltage")
            or specs.get("Operating Voltage")
            or specs.get("typical_voltage")
            or specs.get("forward_voltage")
        )

        current = (
            specs.get("current")
            or specs.get("current_rating")
            or specs.get("Output Current")
        )

        package = specs.get("package")

        return PartSpecs(
            part_number=part_number,
            voltage=voltage,
            current_rating=current,
            package=package,
            source=SpecSource.RAG,
            raw=specs if specs else None,
        )


# ── Factory ──────────────────────────────────────────────────────────────────

def create_grounding_cascade(**kwargs) -> GroundingCascade:
    """Create a GroundingCascade with default settings."""
    return GroundingCascade(**kwargs)
