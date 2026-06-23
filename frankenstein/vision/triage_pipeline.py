"""
Vision Triage Pipeline
======================
End-to-end pipeline: Image → YOLO → Gemini → Damage Rules → Grounding Cascade → PartsManifest

This is the core of Member 1's deliverable.
Input:  A photo of e-waste / PCB
Output: List of PartsManifest objects ready for Member 2's Frankenstein Engine
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from frankenstein.schema import (
    BoundingBox,
    BoardDefect,
    DetectionInfo,
    BoardDamage,
    PartsManifest,
    PartSource,
    PartSpecs,
    PartStatus,
    SpecSource,
    TeardownContext,
    TeardownManifest,
)

from frankenstein.vision.detector import DualYOLODetector, DetectionResult
from frankenstein.vision.condition_assessor import (
    GeminiConditionAssessor,
    MockConditionAssessor,
    ConditionAssessment,
    create_assessor,
)
from frankenstein.vision.damage_inference import DamageInferenceEngine, TriageResult
from frankenstein.vision.grounding.cascade import GroundingCascade


class VisionTriagePipeline:
    """
    Full vision triage pipeline.

    Usage:
        pipeline = VisionTriagePipeline(
            component_weights="weights/component_id_best.pt",
            damage_weights="weights/board_damage_best.pt",
            gemini_api_key="your_key",  # or set GOOGLE_API_KEY env var
        )

        manifest = pipeline.process_teardown(
            image_paths=["photo1.jpg", "photo2.jpg"],
            context=TeardownContext(
                device_model="Samsung Galaxy S10",
                failure_cause="Screen cracked",
                available_tools=["soldering_iron", "multimeter"],
                skill_level=3,
            ),
        )

        # manifest is a TeardownManifest — ready for Member 2
        print(manifest.model_dump_json(indent=2))
    """

    def __init__(
        self,
        component_weights: str | Path,
        damage_weights: str | Path,
        gemini_api_key: Optional[str] = None,
        device: Optional[str] = None,
        component_conf: float = 0.40,
        damage_conf: float = 0.35,
        vault: Optional[object] = None,
        store_useful_only: bool = True,
    ):
        if sys.platform == "win32":
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except AttributeError:
                pass

        print("=" * 60)
        print("Initializing Vision Triage Pipeline")
        print("=" * 60)

        # 1. Dual YOLO Detector
        self.detector = DualYOLODetector(
            component_weights=component_weights,
            damage_weights=damage_weights,
            device=device,
            component_conf=component_conf,
            damage_conf=damage_conf,
        )

        # 2. Gemini Condition Assessor
        self.assessor = create_assessor(gemini_api_key)

        # 3. Damage Inference Engine
        self.damage_engine = DamageInferenceEngine()

        # 4. Grounding Cascade (OCR → Nexar → RAG)
        self.grounding = GroundingCascade()
        from frankenstein.vision.local_verifier import LocalHardwareVerifier
        self.local_verifier = LocalHardwareVerifier()

        # 5. Recycle Vault (optional). When set, process_teardown() stores
        #    useful parts (functional + repairable) automatically so they
        #    can be retrieved later for new builds.
        self.vault = vault
        self.store_useful_only = store_useful_only

        print("=" * 60)
        print("[OK] Pipeline ready")
        print("=" * 60)

    def process_image(self, image_path: str | Path) -> tuple[DetectionResult, list[TriageResult]]:
        """
        Process a single image through the full pipeline.

        Returns:
            (DetectionResult, list[TriageResult])
        """
        print(f"\n--- Processing: {image_path} ---")

        # Step 0: Image relevance check
        print("  [0/3] Verifying hardware presence (Local AI)...")
        import cv2
        img_bgr = cv2.imread(str(image_path))
        if img_bgr is not None:
            is_hw = self.local_verifier.verify_hardware(img_bgr)
            if not is_hw:
                raise ValueError("The uploaded image does not appear to contain internal electronic hardware or a bare PCB.")

        # Step 1: YOLO Detection
        print("  [1/3] Running YOLO detection...")
        detection = self.detector.detect(str(image_path), extract_crops=True)
        
        # Step 1.5: Filter YOLO false positives using Local AI
        print("  [1.5/3] Filtering YOLO false positives using Local AI...")
        valid_components = []
        for comp in detection.components:
            if comp.crop is not None:
                if self.local_verifier.verify_crop(comp.crop, comp.class_name):
                    valid_components.append(comp)
            else:
                valid_components.append(comp)
        
        detection.components = valid_components
        
        print(f"    Valid Components: {len(detection.components)}")
        print(f"    Defects:    {len(detection.defects)}")

        # Step 2: Gemini Vision Assessment (for each component with a crop)
        print("  [2/3] Running Gemini condition assessment...")
        gemini_results = {}
        for idx, comp in enumerate(detection.components):
            if comp.crop is not None:
                assessment = self.assessor.assess(
                    image=comp.crop,
                    component_name=comp.class_name,
                    component_class=comp.class_name,
                    yolo_confidence=comp.confidence,
                )
                gemini_results[str(idx)] = assessment
                print(f"    {comp.class_name}: {assessment.status} ({assessment.confidence:.0%})")

        # Step 3: Damage Inference Rules
        print("  [3/3] Applying damage inference rules...")
        triage_results = self.damage_engine.run(detection, gemini_results)
        for tr in triage_results:
            print(f"    {tr.component.class_name}: {tr.final_status} "
                  f"(rule: {tr.rule_applied})")

        return detection, triage_results

    def process_teardown(
        self,
        image_paths: list[str | Path],
        context: TeardownContext,
        teardown_id: Optional[str] = None,
    ) -> TeardownManifest:
        """
        Process a full teardown session (multiple images) and produce
        the TeardownManifest that Member 2 consumes.

        Args:
            image_paths: List of photo paths from the capture interface
            context: User-provided device context
            teardown_id: Optional custom ID (auto-generated if not provided)

        Returns:
            TeardownManifest ready for the Frankenstein Engine
        """
        if teardown_id is None:
            teardown_id = f"tdn_{uuid.uuid4().hex[:8]}"

        print(f"\n{'='*60}")
        print(f"Teardown Session: {teardown_id}")
        print(f"Device: {context.device_model}")
        print(f"Images: {len(image_paths)}")
        print(f"{'='*60}")

        all_parts: list[PartsManifest] = []
        all_damages: list[BoardDamage] = []
        part_counter = 0

        for img_path in image_paths:
            detection, triage_results = self.process_image(img_path)

            # Convert triage results to PartsManifest
            for tr in triage_results:
                part_counter += 1
                comp = tr.component
                part_id = f"{comp.class_name.lower().replace('-', '_')}_{part_counter:02d}"

                # Build detection info
                det_info = DetectionInfo(
                    yolo_class=comp.class_name,
                    yolo_confidence=comp.confidence,
                    bbox=BoundingBox(
                        x_center=comp.bbox_norm[0],
                        y_center=comp.bbox_norm[1],
                        width=comp.bbox_norm[2],
                        height=comp.bbox_norm[3],
                    ),
                    vision_llm_assessment=(
                        tr.gemini_assessment.reasoning
                        if tr.gemini_assessment else None
                    ),
                )

                # Ground specs via cascade (OCR → Nexar → RAG)
                specs = None
                if tr.final_status != "unsafe":
                    grounding_result = self.grounding.ground(
                        crop_image=comp.crop,
                        yolo_class=comp.class_name,
                        yolo_confidence=comp.confidence,
                        gemini_description=(
                            tr.gemini_assessment.reasoning
                            if tr.gemini_assessment else ""
                        ),
                    )
                    specs = grounding_result.final_specs

                part = PartsManifest(
                    part_id=part_id,
                    name=comp.class_name,
                    category=comp.class_name.lower(),
                    status=PartStatus(tr.final_status),
                    confidence=tr.final_confidence,
                    source=PartSource.PHOTO,
                    specs=specs,
                    repair_note=tr.repair_note,
                    disposal_reason=tr.disposal_reason,
                    detection=det_info,
                    detected_at=datetime.now(timezone.utc),
                    description=grounding_result.rag_result.description if grounding_result and hasattr(grounding_result, 'rag_result') and grounding_result.rag_result and grounding_result.rag_result.found else None,
                    reuse_suggestion=grounding_result.rag_result.reuse_suggestion if grounding_result and hasattr(grounding_result, 'rag_result') and grounding_result.rag_result and grounding_result.rag_result.found else None,
                )
                all_parts.append(part)

            # Convert defects to BoardDamage
            for defect in detection.defects:
                # Find which part this defect affects (if any)
                affected_part = None
                for part in all_parts:
                    if part.detection and _boxes_overlap(
                        defect.bbox_norm, 
                        (part.detection.bbox.x_center, part.detection.bbox.y_center,
                         part.detection.bbox.width, part.detection.bbox.height)
                    ):
                        affected_part = part.part_id
                        break

                all_damages.append(BoardDefect(
                    defect_type=defect.defect_type,
                    confidence=defect.confidence,
                    bbox=BoundingBox(
                        x_center=defect.bbox_norm[0],
                        y_center=defect.bbox_norm[1],
                        width=defect.bbox_norm[2],
                        height=defect.bbox_norm[3],
                    ),
                    affects_part=affected_part,
                ))

        # Build final manifest (TeardownManifest only accepts: teardown_id, context, parts, schema_version)
        manifest = TeardownManifest(
            teardown_id=teardown_id,
            context=context,
            parts=all_parts,
            board_damages=all_damages,
            image_paths=[str(p) for p in image_paths],
        )

        # Summary
        print(f"\n{'='*60}")
        print(f"Teardown Complete: {teardown_id}")
        print(f"{'='*60}")
        print(f"  Total parts:     {len(all_parts)}")
        print(f"  Functional:      {sum(1 for p in all_parts if p.status == PartStatus.FUNCTIONAL)}")
        print(f"  Repairable:      {sum(1 for p in all_parts if p.status == PartStatus.REPAIRABLE)}")
        print(f"  Unsafe:          {sum(1 for p in all_parts if p.status == PartStatus.UNSAFE)}")
        print(f"  Board defects:   {len(all_damages)}")

        # ── Recycle Vault storage ──
        # If a vault was wired in at __init__ time, automatically store the
        # teardown so its useful parts (functional + repairable) can be
        # retrieved later via semantic search.
        if self.vault is not None and all_parts:
            parts_to_store = all_parts
            if self.store_useful_only:
                parts_to_store = [
                    p for p in all_parts
                    if p.status in (PartStatus.FUNCTIONAL, PartStatus.REPAIRABLE)
                ]
            vault_manifest = TeardownManifest(
                teardown_id=teardown_id,
                context=context,
                parts=parts_to_store,
                board_damages=all_damages,
                image_paths=[str(p) for p in image_paths],
            )
            try:
                stored = self.vault.store_teardown(vault_manifest)
                skipped_unsafe = len(all_parts) - len(parts_to_store)
                print(f"\n{'='*60}")
                print(f"  Recycle Vault:    stored {stored} useful part(s)")
                if self.store_useful_only and skipped_unsafe:
                    print(f"  Skipped (unsafe): {skipped_unsafe} part(s) not vaulted")
                print(f"{'='*60}")
            except Exception as e:
                print(f"  [WARN] Vault store failed: {e}")

        return manifest


def _boxes_overlap(b1: tuple, b2: tuple, threshold: float = 0.15) -> bool:
    """Check if two normalized (x_c, y_c, w, h) boxes overlap significantly."""
    # Convert to xyxy
    x1_1 = b1[0] - b1[2] / 2
    y1_1 = b1[1] - b1[3] / 2
    x2_1 = b1[0] + b1[2] / 2
    y2_1 = b1[1] + b1[3] / 2

    x1_2 = b2[0] - b2[2] / 2
    y1_2 = b2[1] - b2[3] / 2
    x2_2 = b2[0] + b2[2] / 2
    y2_2 = b2[1] + b2[3] / 2

    # IoU
    xi1 = max(x1_1, x1_2)
    yi1 = max(y1_1, y1_2)
    xi2 = min(x2_1, x2_2)
    yi2 = min(y2_1, y2_2)

    inter = max(0, xi2 - xi1) * max(0, yi2 - yi1)
    a1 = (x2_1 - x1_1) * (y2_1 - y1_1)
    a2 = (x2_2 - x1_2) * (y2_2 - y1_2)
    union = a1 + a2 - inter

    return (inter / union if union > 0 else 0) > threshold


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    PROJECT_ROOT = Path(__file__).resolve().parents[3]

    pipeline = VisionTriagePipeline(
        component_weights=PROJECT_ROOT / "weights" / "component_id_best.pt",
        damage_weights=PROJECT_ROOT / "weights" / "board_damage_best.pt",
    )

    if len(sys.argv) > 1:
        image_paths = sys.argv[1:]
        manifest = pipeline.process_teardown(
            image_paths=image_paths,
            context=TeardownContext(
                device_model="CLI Test",
                failure_cause="Testing",
                available_tools=["multimeter"],
                skill_level=3,
            ),
        )

        # Save output
        output_path = PROJECT_ROOT / "output_manifest.json"
        with open(output_path, "w") as f:
            f.write(manifest.model_dump_json(indent=2))
        print(f"\nManifest saved to: {output_path}")
    else:
        print("\nPipeline ready. Usage:")
        print("  python triage_pipeline.py image1.jpg [image2.jpg ...]")
