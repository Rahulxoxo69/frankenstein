"""
Dual YOLOv8 Detector
====================
Runs both trained YOLO models on an input image:
  1. Component-ID model  → detects & classifies electronic components
  2. Board-Damage model  → detects PCB defects (6 classes)

Returns structured detection results with bounding boxes, class labels,
and confidence scores — ready for the Gemini Vision condition pass.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import torch
from ultralytics import YOLO


# ── Detection Result Data Classes ────────────────────────────────────────────

@dataclass
class DetectedComponent:
    """A single component detected by the Component-ID model."""
    class_name: str
    class_id: int
    confidence: float
    bbox_xyxy: tuple[float, float, float, float]   # absolute pixel coords
    bbox_norm: tuple[float, float, float, float]    # normalized (x_c, y_c, w, h)
    crop: Optional[np.ndarray] = field(default=None, repr=False)  # cropped image region


@dataclass
class DetectedDefect:
    """A single defect detected by the Board-Damage model."""
    defect_type: str     # open, short, mousebite, spur, pinhole, spurious_copper
    class_id: int
    confidence: float
    bbox_xyxy: tuple[float, float, float, float]
    bbox_norm: tuple[float, float, float, float]


@dataclass
class DetectionResult:
    """Combined output of both YOLO models for a single image."""
    image_path: str
    image_shape: tuple[int, int]  # (height, width)
    components: list[DetectedComponent] = field(default_factory=list)
    defects: list[DetectedDefect] = field(default_factory=list)
    device: str = "cpu"

    @property
    def functional_candidates(self) -> list[DetectedComponent]:
        """Components not overlapping with any defect region."""
        safe = []
        for comp in self.components:
            overlaps_defect = any(
                _bbox_iou(comp.bbox_xyxy, d.bbox_xyxy) > 0.3
                for d in self.defects
            )
            if not overlaps_defect:
                safe.append(comp)
        return safe

    @property
    def damaged_components(self) -> list[tuple[DetectedComponent, DetectedDefect]]:
        """Components overlapping with defect regions."""
        damaged = []
        for comp in self.components:
            for defect in self.defects:
                if _bbox_iou(comp.bbox_xyxy, defect.bbox_xyxy) > 0.3:
                    damaged.append((comp, defect))
                    break
        return damaged


def _bbox_iou(box1: tuple, box2: tuple) -> float:
    """Compute IoU between two (x1,y1,x2,y2) bounding boxes."""
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])

    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
    area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
    union = area1 + area2 - inter

    return inter / union if union > 0 else 0.0


# ── Detector Class ───────────────────────────────────────────────────────────

class DualYOLODetector:
    """
    Loads and runs both YOLOv8 models for the triage pipeline.

    Usage:
        detector = DualYOLODetector(
            component_weights="weights/component_id_best.pt",
            damage_weights="weights/board_damage_best.pt",
        )
        result = detector.detect("path/to/pcb_photo.jpg")
        print(result.components)   # detected components
        print(result.defects)      # detected defects
    """

    # Board-damage class names (must match training order)
    DAMAGE_CLASSES = ["open", "short", "mousebite", "spur", "pinhole", "spurious_copper"]

    def __init__(
        self,
        component_weights: str | Path,
        damage_weights: str | Path,
        device: Optional[str] = None,
        component_conf: float = 0.40,
        damage_conf: float = 0.35,
        iou_threshold: float = 0.5,
    ):
        """
        Args:
            component_weights: Path to Component-ID model weights
            damage_weights: Path to Board-Damage model weights
            device: 'cuda', 'cpu', or None (auto-detect)
            component_conf: Confidence threshold for component detection
                             (default 0.40 — tuned to reduce false positives
                             vs the YOLOv8 baseline of 0.25)
            damage_conf: Confidence threshold for defect detection
                          (default 0.35)
            iou_threshold: IoU threshold for NMS suppression (default 0.5)
        """
        # Auto-detect device
        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.component_conf = component_conf
        self.damage_conf = damage_conf
        self.iou_threshold = iou_threshold

        # Load models
        print(f"[Detector] Loading models on {self.device}...")

        self.component_model = YOLO(str(component_weights))
        self.component_model.to(self.device)
        self.component_classes = self.component_model.names
        print(f"  [OK] Component-ID: {len(self.component_classes)} classes")

        self.damage_model = YOLO(str(damage_weights))
        self.damage_model.to(self.device)
        self.damage_classes = self.damage_model.names
        print(f"  [OK] Board-Damage: {len(self.damage_classes)} classes")
        print(f"  [OK] Device: {self.device}")

    def detect(
        self,
        image: str | Path | np.ndarray,
        extract_crops: bool = True,
    ) -> DetectionResult:
        """
        Run both models on an image.

        Args:
            image: Path to image or numpy array (BGR)
            extract_crops: Whether to extract cropped regions for each component

        Returns:
            DetectionResult with components and defects
        """
        # Load image if path
        if isinstance(image, (str, Path)):
            img = cv2.imread(str(image))
            image_path = str(image)
        else:
            img = image
            image_path = "<array>"

        if img is None:
            raise ValueError(f"Could not load image: {image_path}")

        h, w = img.shape[:2]

        # Run Component-ID model
        comp_results = self.component_model.predict(
            img,
            conf=self.component_conf,
            iou=self.iou_threshold,
            device=self.device,
            verbose=False,
        )

        components = []
        for result in comp_results:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                cls_id = int(box.cls[0].cpu().numpy())
                conf = float(box.conf[0].cpu().numpy())

                # Normalized bbox (x_center, y_center, width, height)
                x_c = (x1 + x2) / 2.0 / w
                y_c = (y1 + y2) / 2.0 / h
                bw = (x2 - x1) / w
                bh = (y2 - y1) / h

                # Extract crop for Gemini Vision
                crop = None
                if extract_crops:
                    pad = 10  # pixels padding
                    cx1 = max(0, int(x1) - pad)
                    cy1 = max(0, int(y1) - pad)
                    cx2 = min(w, int(x2) + pad)
                    cy2 = min(h, int(y2) + pad)
                    crop = img[cy1:cy2, cx1:cx2].copy()

                components.append(DetectedComponent(
                    class_name=self.component_classes.get(cls_id, f"class_{cls_id}"),
                    class_id=cls_id,
                    confidence=conf,
                    bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)),
                    bbox_norm=(float(x_c), float(y_c), float(bw), float(bh)),
                    crop=crop,
                ))

        # Run Board-Damage model
        dmg_results = self.damage_model.predict(
            img,
            conf=self.damage_conf,
            iou=self.iou_threshold,
            device=self.device,
            verbose=False,
        )

        defects = []
        for result in dmg_results:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                cls_id = int(box.cls[0].cpu().numpy())
                conf = float(box.conf[0].cpu().numpy())

                x_c = (x1 + x2) / 2.0 / w
                y_c = (y1 + y2) / 2.0 / h
                bw = (x2 - x1) / w
                bh = (y2 - y1) / h

                defects.append(DetectedDefect(
                    defect_type=self.damage_classes.get(cls_id, f"defect_{cls_id}"),
                    class_id=cls_id,
                    confidence=conf,
                    bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)),
                    bbox_norm=(float(x_c), float(y_c), float(bw), float(bh)),
                ))

        return DetectionResult(
            image_path=image_path,
            image_shape=(h, w),
            components=components,
            defects=defects,
            device=self.device,
        )

    def detect_batch(
        self,
        images: list[str | Path],
        extract_crops: bool = True,
    ) -> list[DetectionResult]:
        """Run detection on multiple images."""
        return [self.detect(img, extract_crops) for img in images]


# ── CLI test ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    PROJECT_ROOT = Path(__file__).resolve().parents[3]

    detector = DualYOLODetector(
        component_weights=PROJECT_ROOT / "weights" / "component_id_best.pt",
        damage_weights=PROJECT_ROOT / "weights" / "board_damage_best.pt",
    )

    # Test with a sample image if provided
    if len(sys.argv) > 1:
        result = detector.detect(sys.argv[1])
        print(f"\nImage: {result.image_path} ({result.image_shape})")
        print(f"Components: {len(result.components)}")
        for c in result.components:
            print(f"  {c.class_name}: {c.confidence:.2f} @ {c.bbox_xyxy}")
        print(f"Defects: {len(result.defects)}")
        for d in result.defects:
            print(f"  {d.defect_type}: {d.confidence:.2f} @ {d.bbox_xyxy}")
        print(f"Safe components: {len(result.functional_candidates)}")
        print(f"Damaged components: {len(result.damaged_components)}")
    else:
        print("\nDetector loaded successfully. Pass an image path to test:")
        print("  python detector.py path/to/pcb_image.jpg")
