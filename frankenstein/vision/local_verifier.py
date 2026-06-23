import os
from PIL import Image
import numpy as np

class LocalHardwareVerifier:
    """
    Uses a local CLIP model to verify images and filter false positives.
    """
    def __init__(self):
        print("[Verifier] Initializing local zero-shot image classifier (CLIP)...")
        from transformers import pipeline
        import torch
        device = 0 if torch.cuda.is_available() else -1
        self.classifier = pipeline(
            "zero-shot-image-classification", 
            model="openai/clip-vit-base-patch32",
            device=device
        )
        self.hardware_labels = [
            "a printed circuit board or electronic component",
            "an electronic device like a laptop, phone, or appliance",
            "a person, animal, or outdoor scenery",
            "a random non-electronic household object"
        ]

    def verify_hardware(self, image: np.ndarray) -> bool:
        """
        Returns True if the image is likely electronic hardware or a device.
        Accepts assembled devices since the goal is to tear them down!
        """
        import cv2
        img_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        
        try:
            results = self.classifier(pil_img, candidate_labels=self.hardware_labels)
            best_match = results[0]["label"]
            score = results[0]["score"]
            print(f"  [Verifier] Image classification: '{best_match}' (score: {score:.2f})")
            
            # Reject only if it's completely irrelevant (animals, scenery, etc)
            if "person, animal" in best_match or "non-electronic" in best_match:
                return False
            return True
        except Exception as e:
            print(f"  [Verifier] Error during image verification: {e}")
            return True # fallback

    def verify_crop(self, crop: np.ndarray, class_name: str) -> bool:
        """
        Verifies if a YOLO-cropped image actually looks like the detected component 
        rather than a false positive (like a crack, wire, or empty plastic).
        """
        if crop is None or crop.size == 0:
            return False

        import cv2
        img_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        
        candidate_labels = [
            f"an electronic {class_name} component",
            "a broken crack, scratch, or abstract pattern",
            "a piece of plastic casing or empty space"
        ]
        
        try:
            results = self.classifier(pil_img, candidate_labels=candidate_labels)
            best_match = results[0]["label"]
            best_score = results[0]["score"]

            # Reject if CLIP is moderately confident (>50%) it's NOT a component
            # (was 0.6 — too lenient, let through cracks/wires/text as components)
            if best_match != candidate_labels[0] and best_score > 0.5:
                print(f"  [Verifier] Rejected false positive for '{class_name}'. CLIP: '{best_match}' ({best_score:.0%})")
                return False
            # Otherwise keep it — CLIP may be unsure or YOLO crop is small
            print(f"  [Verifier] Kept '{class_name}'. CLIP: '{best_match}' ({best_score:.0%})")
            return True
        except Exception as e:
            print(f"  [Verifier] Error during crop verification: {e}")
            return True # fallback
