"""
Gemini Vision Condition Assessor
================================
Takes each detected component's cropped bounding box and sends it to
Gemini 2.0 Flash (free via Google AI Studio) for condition assessment.

This is the judgment call a detector architecturally can't make:
  - Is this capacitor swollen?
  - Is this trace burnt?
  - Is there corrosion on the pins?

YOLO classifies *what* it is; Gemini reasons about *condition*.

Output: structured JSON with status (functional/repairable/unsafe),
confidence, repair_note, and disposal_reason.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from google import genai
from google.genai import types


# ── Assessment Result ────────────────────────────────────────────────────────

@dataclass
class ConditionAssessment:
    """Result of Gemini Vision's condition analysis on a single component."""
    status: str          # "functional", "repairable", "unsafe"
    confidence: float    # 0.0 - 1.0
    reasoning: str       # Gemini's explanation
    repair_note: Optional[str] = None   # What needs fixing (repairable only)
    disposal_reason: Optional[str] = None # Regulation citation (unsafe only)
    raw_response: Optional[str] = None  # Full Gemini response for debugging


# ── System Prompt ────────────────────────────────────────────────────────────

CONDITION_SYSTEM_PROMPT = """You are an expert electronics technician inspecting salvaged electronic components from e-waste teardowns. You are examining a cropped image of a single detected component.

Your job: assess the PHYSICAL CONDITION of this component and classify it into exactly one category.

## Classification Rules

**FUNCTIONAL**: The component appears to be in working condition.
- No visible damage, corrosion, swelling, burn marks, or cracks
- Pins/leads appear intact and properly shaped
- Package/housing is undamaged

**REPAIRABLE**: The component can be made functional with basic tools (soldering iron, multimeter, pliers) in under 15 minutes.
- Minor lead damage (bent, frayed) that can be resoldered
- Light surface oxidation that can be cleaned
- Minor cosmetic damage that doesn't affect function
- Loose but reattachable connectors

**UNSAFE**: The component is damaged beyond practical repair OR poses a safety risk.
- Swollen/bulging battery cells or capacitors
- Burn marks, charring, or melting
- Cracked IC packages exposing die
- Severe corrosion affecting structural integrity
- Leaking electrolyte from capacitors
- Any condition posing fire, chemical, or electrical hazard

## Response Format

You MUST respond with ONLY a valid JSON object (no markdown, no extra text):

{
  "status": "functional" | "repairable" | "unsafe",
  "confidence": 0.0-1.0,
  "reasoning": "One sentence explaining your assessment",
  "repair_note": "What needs fixing (ONLY if status is repairable, else null)",
  "disposal_reason": "Safety concern and regulation (ONLY if status is unsafe, else null)"
}

For disposal_reason, cite the relevant Indian regulation:
- For batteries: "Battery Waste Management Rules 2022, Schedule II"
- For other e-waste: "E-Waste (Management) Rules 2022, Schedule I"
"""


# ── Assessor Class ───────────────────────────────────────────────────────────

class GeminiConditionAssessor:
    """
    Assesses component condition using Gemini 2.0 Flash Vision.

    Usage:
        assessor = GeminiConditionAssessor(api_key="your_key")
        result = assessor.assess(crop_image, component_name="ESP32")
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_name: str = "gemini-2.0-flash",
    ):
        """
        Args:
            api_key: Google AI Studio API key. Falls back to GOOGLE_API_KEY env var.
            model_name: Gemini model to use (default: gemini-2.0-flash, free tier)
        """
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "No API key provided. Set GOOGLE_API_KEY environment variable "
                "or pass api_key parameter. Get a free key at https://aistudio.google.com"
            )

        self.client = genai.Client(api_key=self.api_key)
        self.model_name = model_name
        print(f"[Assessor] Gemini Vision ready (model: {model_name})")

    def assess(
        self,
        image: np.ndarray,
        component_name: str = "Unknown component",
        component_class: str = "unknown",
        yolo_confidence: float = 0.0,
    ) -> ConditionAssessment:
        """
        Assess the condition of a single component from its cropped image.

        Args:
            image: Cropped BGR image (numpy array) of the component
            component_name: YOLO-detected class name
            component_class: Category of the component
            yolo_confidence: YOLO detection confidence

        Returns:
            ConditionAssessment with status, confidence, and notes
        """
        # Encode image to base64 PNG
        _, buffer = cv2.imencode(".png", image)
        image_bytes = buffer.tobytes()

        # Build the prompt
        user_prompt = (
            f"This is a cropped image of a detected '{component_name}' "
            f"(detection confidence: {yolo_confidence:.0%}). "
            f"Assess its physical condition."
        )

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=CONDITION_SYSTEM_PROMPT),
                            types.Part.from_bytes(
                                data=image_bytes,
                                mime_type="image/png",
                            ),
                            types.Part.from_text(text=user_prompt),
                        ],
                    ),
                ],
                config=types.GenerateContentConfig(
                    temperature=0.1,  # Low temp for consistent structured output
                    max_output_tokens=500,
                ),
            )

            raw_text = response.text.strip()

            # Parse JSON from response (handle markdown code blocks)
            json_str = raw_text
            if "```" in json_str:
                # Extract JSON from code block
                match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", json_str, re.DOTALL)
                if match:
                    json_str = match.group(1).strip()

            parsed = json.loads(json_str)

            return ConditionAssessment(
                status=parsed.get("status", "functional"),
                confidence=float(parsed.get("confidence", 0.5)),
                reasoning=parsed.get("reasoning", "No reasoning provided"),
                repair_note=parsed.get("repair_note"),
                disposal_reason=parsed.get("disposal_reason"),
                raw_response=raw_text,
            )

        except json.JSONDecodeError as e:
            # Gemini returned non-JSON — fall back to functional with low confidence
            return ConditionAssessment(
                status="functional",
                confidence=0.3,
                reasoning=f"Could not parse Gemini response as JSON: {e}",
                raw_response=raw_text if 'raw_text' in dir() else str(e),
            )

        except Exception as e:
            # API error — fail gracefully
            return ConditionAssessment(
                status="functional",
                confidence=0.1,
                reasoning=f"Gemini API error: {e}",
                raw_response=str(e),
            )

    def assess_batch(
        self,
        components: list[dict],
    ) -> list[ConditionAssessment]:
        """
        Assess multiple components sequentially.

        Args:
            components: List of dicts with keys: image, name, class_name, confidence

        Returns:
            List of ConditionAssessment results
        """
        results = []
        for i, comp in enumerate(components):
            print(f"  [{i+1}/{len(components)}] Assessing {comp.get('name', 'unknown')}...")
            result = self.assess(
                image=comp["image"],
                component_name=comp.get("name", "Unknown"),
                component_class=comp.get("class_name", "unknown"),
                yolo_confidence=comp.get("confidence", 0.0),
            )
            print(f"    -> {result.status} ({result.confidence:.0%}): {result.reasoning}")
            results.append(result)
        return results

    def verify_hardware(self, image: np.ndarray) -> bool:
        """Verify if the image contains electronic hardware."""
        import cv2
        from PIL import Image
        from google.genai import types

        img_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[
                    "Does this image show the INTERNAL components of an electronic device, such as a bare printed circuit board (PCB), exposed microchips, or discrete electronic components? If the image shows only the OUTSIDE of a fully assembled consumer device (like a closed laptop, a phone case, or a monitor) or something entirely non-electronic, reply 'NO'. Reply with strictly 'YES' or 'NO'.",
                    pil_img
                ],
                config=types.GenerateContentConfig(temperature=0.0)
            )
            text = response.text.strip().upper()
            return "YES" in text
        except Exception as e:
            print(f"[Assessor] Hardware verification failed: {e}")
            return True # fallback to assuming it is hardware on error


# ── Offline / Mock Assessor (for testing without API key) ────────────────────

class MockConditionAssessor:
    """
    Offline condition assessor using CLIP zero-shot classification.
    Uses the same LocalHardwareVerifier CLIP model to assess component condition
    (damaged vs clean) without requiring a Gemini API key.
    """

    def __init__(self):
        self.verifier = None
        self._init_clip()
        print("[Assessor] OFFLINE mode — using CLIP for condition assessment")

    def _init_clip(self):
        """Lazy-init CLIP pipeline (only loads when first used)."""
        if self.verifier is not None:
            return
        try:
            from frankenstein.vision.local_verifier import LocalHardwareVerifier
            self.verifier = LocalHardwareVerifier()
            print("[Assessor] CLIP condition model ready")
        except ImportError:
            print("[Assessor] WARNING: LocalHardwareVerifier not available — using rule-based fallback")
            self.verifier = None

    def assess(
        self,
        image: np.ndarray,
        component_name: str = "Unknown",
        component_class: str = "unknown",
        yolo_confidence: float = 0.0,
    ) -> ConditionAssessment:
        if self.verifier is None:
            self._init_clip()

        # Use CLIP for zero-shot condition assessment
        damaged_keywords = [
            "burnt", "burned", "damaged", "corroded", "cracked",
            "broken", "swollen", "melted", "charred", "rusted",
            "frayed", "bent", "scratched", "worn", "dirty"
        ]
        
        if self.verifier and hasattr(self.verifier, 'classifier') and image is not None and image.size > 0:
            try:
                import cv2
                from PIL import Image as PILImage
                img_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
                pil_img = PILImage.fromarray(img_rgb)

                candidate_labels = [
                    f"a clean functional {component_name} component in good condition",
                    f"a damaged burnt or corroded {component_name} component",
                    "a broken crack or scratch on circuit board",
                ]
                
                results = self.verifier.classifier(pil_img, candidate_labels=candidate_labels)
                best_label = results[0]["label"]
                best_score = results[0]["score"]

                # Check if CLIP thinks it's damaged
                is_damaged = "damaged" in best_label.lower() or "broken" in best_label.lower()

                if is_damaged:
                    return ConditionAssessment(
                        status="repairable" if best_score < 0.75 else "unsafe",
                        confidence=best_score,
                        reasoning=f"CLIP detected damage: '{best_label}' ({best_score:.0%} confidence). Component shows visible wear or damage.",
                        repair_note="Recommend cleaning pins and testing with multimeter before reuse." if best_score < 0.75 else "Component appears significantly damaged — verify with multimeter.",
                        disposal_reason=None if best_score < 0.75 else "E-Waste (Management) Rules 2022, Schedule I — damaged component unsuitable for reuse",
                    )

                # Looks clean — functional
                return ConditionAssessment(
                    status="functional",
                    confidence=max(best_score, yolo_confidence * 0.7),
                    reasoning=f"CLIP assessment: '{best_label}' ({best_score:.0%} confidence). Component appears clean and functional.",
                )

            except Exception as e:
                print(f"[Assessor] CLIP assessment failed: {e}")
                # Fall through to rule-based fallback

        # Rule-based fallback: check image properties
        if image is not None and image.size > 0:
            try:
                import cv2
                # Check brightness (burnt = dark patches)
                gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
                mean_brightness = gray.mean()
                # Check for very dark regions (possible burn)
                dark_pixels = (gray < 30).mean()
                # Check for very bright regions (possible corrosion reflection)
                bright_pixels = (gray > 225).mean()

                if dark_pixels > 0.15:
                    return ConditionAssessment(
                        status="repairable",
                        confidence=0.55,
                        reasoning=f"Image analysis: {dark_pixels:.0%} very dark pixels detected (possible burn marks). Recommend visual inspection.",
                        repair_note="Check for burn marks and test continuity with multimeter.",
                    )
                if bright_pixels > 0.20:
                    return ConditionAssessment(
                        status="functional",
                        confidence=0.65,
                        reasoning=f"Image analysis: typical component appearance. No significant damage indicators detected.",
                    )
            except Exception:
                pass

        # Ultimate fallback
        return ConditionAssessment(
            status="functional",
            confidence=yolo_confidence * 0.6,
            reasoning=f"Component detected (YOLO: {yolo_confidence:.0%}). No damage indicators visible in image.",
        )

    def assess_batch(self, components: list[dict]) -> list[ConditionAssessment]:
        return [
            self.assess(
                c["image"], c.get("name", ""), c.get("class_name", ""), c.get("confidence", 0)
            )
            for c in components
        ]

    def verify_hardware(self, image: np.ndarray) -> bool:
        return True


def create_assessor(api_key: Optional[str] = None) -> GeminiConditionAssessor | MockConditionAssessor:
    """Factory: create real assessor if API key available, mock otherwise."""
    key = api_key or os.environ.get("GOOGLE_API_KEY", "")
    if key:
        return GeminiConditionAssessor(api_key=key)
    else:
        print("[WARN] No GOOGLE_API_KEY set. Using mock assessor.")
        return MockConditionAssessor()
