"""
Damage Inference Rules Engine
=============================
Deterministic rules table combining YOLOv8 damage detections + Gemini Vision
condition judgments to produce final triage status for each component.

This is NOT an LLM — it's hard-coded logic that overrides or supplements
the Gemini assessment based on objective defect detections.

Rules:
  1. Component overlapping a board defect → status downgraded
  2. Specific defect types → automatic unsafe (e.g. short circuit)
  3. Low YOLO confidence → flag for manual review
  4. Gemini and YOLO disagree → use stricter assessment
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .detector import DetectedComponent, DetectedDefect, DetectionResult, _bbox_iou
from .condition_assessor import ConditionAssessment


# ── Triage Result ────────────────────────────────────────────────────────────

@dataclass
class TriageResult:
    """Final triage decision for a single component."""
    component: DetectedComponent
    final_status: str           # functional / repairable / unsafe
    final_confidence: float
    gemini_assessment: Optional[ConditionAssessment]
    overlapping_defects: list[DetectedDefect]
    reasoning: str
    repair_note: Optional[str] = None
    disposal_reason: Optional[str] = None
    rule_applied: Optional[str] = None  # Which rule determined the outcome


# ── Rules ────────────────────────────────────────────────────────────────────

# Defects that automatically make any overlapping component UNSAFE
CRITICAL_DEFECTS = {"short", "open"}

# Defects that downgrade a component to REPAIRABLE (if currently functional)
DEGRADING_DEFECTS = {"mousebite", "spur", "pinhole", "spurious_copper"}

# Minimum YOLO confidence to trust a detection
MIN_YOLO_CONFIDENCE = 0.40

# IoU threshold for considering a component "affected" by a defect
DEFECT_OVERLAP_IOU = 0.15


# ── Rules Engine ─────────────────────────────────────────────────────────────

class DamageInferenceEngine:
    """
    Applies deterministic rules to combine YOLO detections with Gemini assessments.

    Pipeline:
      1. For each detected component, find overlapping defects
      2. Apply defect-severity rules (critical → unsafe, degrading → repairable)
      3. Compare with Gemini's assessment — use the STRICTER of the two
      4. Handle edge cases (low confidence, conflicting signals)
    """

    STATUS_SEVERITY = {"functional": 0, "repairable": 1, "unsafe": 2}

    def __init__(
        self,
        defect_overlap_iou: float = DEFECT_OVERLAP_IOU,
        min_confidence: float = MIN_YOLO_CONFIDENCE,
    ):
        self.defect_overlap_iou = defect_overlap_iou
        self.min_confidence = min_confidence

    def _stricter(self, s1: str, s2: str) -> str:
        """Return the stricter of two statuses."""
        return s1 if self.STATUS_SEVERITY.get(s1, 0) >= self.STATUS_SEVERITY.get(s2, 0) else s2

    def run(
        self,
        detection_result: DetectionResult,
        gemini_assessments: dict[str, ConditionAssessment],
    ) -> list[TriageResult]:
        """
        Apply rules to produce final triage for every detected component.

        Args:
            detection_result: Output of DualYOLODetector.detect()
            gemini_assessments: Dict mapping component index/id to its Gemini assessment

        Returns:
            List of TriageResult, one per component
        """
        results = []

        for idx, comp in enumerate(detection_result.components):
            # Get Gemini assessment (if available)
            gemini = gemini_assessments.get(str(idx))

            # Find overlapping defects
            overlapping = [
                d for d in detection_result.defects
                if _bbox_iou(comp.bbox_xyxy, d.bbox_xyxy) > self.defect_overlap_iou
            ]

            # Start with Gemini's assessment or default to functional
            if gemini:
                status = gemini.status
                confidence = gemini.confidence
                repair_note = gemini.repair_note
                disposal_reason = gemini.disposal_reason
                reasoning = gemini.reasoning
                rule = "gemini_assessment"
            else:
                status = "functional"
                confidence = comp.confidence * 0.8  # Lower confidence without Gemini
                repair_note = None
                disposal_reason = None
                reasoning = "No Gemini assessment available, defaulting to functional"
                rule = "default_functional"

            # ── Rule 1: Critical defect overlap → UNSAFE ──
            critical_overlaps = [d for d in overlapping if d.defect_type in CRITICAL_DEFECTS]
            if critical_overlaps:
                defect_names = ", ".join(d.defect_type for d in critical_overlaps)
                status = "unsafe"
                confidence = max(confidence, max(d.confidence for d in critical_overlaps))
                disposal_reason = (
                    f"Board defect ({defect_names}) detected overlapping this component. "
                    f"Component integrity compromised. "
                    f"Dispose per E-Waste (Management) Rules 2022, Schedule I."
                )
                repair_note = None
                reasoning = f"Critical board defect ({defect_names}) overlaps component region"
                rule = "critical_defect_overlap"

            # ── Rule 2: Degrading defect overlap → downgrade to REPAIRABLE ──
            elif overlapping and status == "functional":
                degrading = [d for d in overlapping if d.defect_type in DEGRADING_DEFECTS]
                if degrading:
                    defect_names = ", ".join(d.defect_type for d in degrading)
                    status = "repairable"
                    repair_note = (
                        f"Minor board defect ({defect_names}) near component. "
                        f"Inspect solder joints and traces before use. "
                        f"Clean affected area and verify continuity with multimeter."
                    )
                    reasoning = f"Degrading defect ({defect_names}) near component, downgraded to repairable"
                    rule = "degrading_defect_downgrade"

            # ── Rule 3: Gemini says safe but defect detected → use stricter ──
            if overlapping and gemini and gemini.status == "functional":
                if status == "functional":
                    # Defect didn't trigger rules above but exists — flag it
                    status = "repairable"
                    repair_note = (
                        f"Gemini assessed as functional, but board defect detected nearby. "
                        f"Recommend visual inspection before use."
                    )
                    rule = "gemini_defect_disagreement"

            # ── Rule 4: Low YOLO confidence → reduce confidence ──
            if comp.confidence < self.min_confidence:
                confidence *= 0.5
                reasoning += f" [LOW YOLO CONFIDENCE: {comp.confidence:.0%}]"
                rule = f"{rule}+low_confidence"

            # ── Rule 5: Ensure status consistency ──
            if status == "functional":
                repair_note = None
                disposal_reason = None
            elif status == "repairable":
                disposal_reason = None
                if not repair_note:
                    repair_note = "Visual inspection recommended before use."
            elif status == "unsafe":
                repair_note = None
                if not disposal_reason:
                    disposal_reason = "Dispose per E-Waste (Management) Rules 2022, Schedule I."

            results.append(TriageResult(
                component=comp,
                final_status=status,
                final_confidence=min(confidence, 1.0),
                gemini_assessment=gemini,
                overlapping_defects=overlapping,
                reasoning=reasoning,
                repair_note=repair_note,
                disposal_reason=disposal_reason,
                rule_applied=rule,
            ))

        return results


# ── Convenience function ─────────────────────────────────────────────────────

def apply_damage_rules(
    detection_result: DetectionResult,
    gemini_assessments: dict[str, ConditionAssessment],
) -> list[TriageResult]:
    """Shorthand for running the damage inference engine."""
    engine = DamageInferenceEngine()
    return engine.run(detection_result, gemini_assessments)
