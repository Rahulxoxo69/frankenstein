"""
OCR Reader
==========
Reads part numbers, markings, and text from component crops using Tesseract OCR
(via pytesseract) with optional PaddleOCR fallback for difficult cases.

This is Step 1 of the Grounding Cascade:
  OCR → Nexar Lookup → RAG Fallback

The part number read here is the KEY that unlocks grounded specs
from Nexar or datasheet search.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

try:
    from paddleocr import PaddleOCR
    PADDLE_AVAILABLE = True
except ImportError:
    PADDLE_AVAILABLE = False


# ── OCR Result ───────────────────────────────────────────────────────────────

@dataclass
class OCRResult:
    """Result of OCR on a component crop."""
    raw_text: str                           # Everything detected
    part_numbers: list[str] = field(default_factory=list)  # Extracted part numbers
    all_lines: list[str] = field(default_factory=list)     # All text lines
    confidence: float = 0.0                 # Average confidence (0-1)
    method: str = "none"                    # "tesseract", "paddle", "none"


# ── Part Number Patterns ─────────────────────────────────────────────────────

# Common part number patterns for electronic components
PART_NUMBER_PATTERNS = [
    # Microcontrollers: ESP32, ATmega, STM32, PIC, etc.
    re.compile(r'\b(ESP32[\w-]*)\b', re.IGNORECASE),
    re.compile(r'\b(ATmega\d+[\w-]*)\b', re.IGNORECASE),
    re.compile(r'\b(STM32[\w-]+)\b', re.IGNORECASE),
    re.compile(r'\b(PIC\d+[\w-]+)\b', re.IGNORECASE),
    re.compile(r'\b(SAMD\d+[\w-]*)\b', re.IGNORECASE),

    # Common ICs: NE555, LM7805, LM317, 74HC series
    re.compile(r'\b(NE\d{3,4}[\w]*)\b', re.IGNORECASE),
    re.compile(r'\b(LM\d{3,5}[\w]*)\b', re.IGNORECASE),
    re.compile(r'\b(74[HCLS]{0,3}\d{2,4}[\w]*)\b', re.IGNORECASE),
    re.compile(r'\b(TL\d{3,4}[\w]*)\b', re.IGNORECASE),
    re.compile(r'\b(LM[23]\d{2,3}[\w]*)\b', re.IGNORECASE),

    # Sensors
    re.compile(r'\b(DHT\d{2})\b', re.IGNORECASE),
    re.compile(r'\b(BME?\d{3})\b', re.IGNORECASE),
    re.compile(r'\b(MQ[\-]?\d+)\b', re.IGNORECASE),
    re.compile(r'\b(HC[\-]?SR\d+)\b', re.IGNORECASE),

    # Motor drivers
    re.compile(r'\b(L298N?)\b', re.IGNORECASE),
    re.compile(r'\b(A4988)\b', re.IGNORECASE),
    re.compile(r'\b(DRV\d{4}[\w]*)\b', re.IGNORECASE),

    # Voltage regulators
    re.compile(r'\b(AMS1117[\w-]*)\b', re.IGNORECASE),
    re.compile(r'\b(7805|7812|7815|78L05)\b'),

    # Generic alphanumeric part numbers (at least 4 chars, mix of letters+digits)
    re.compile(r'\b([A-Z]{1,4}\d{3,6}[A-Z]?(?:[\-][A-Z0-9]+)?)\b'),
    re.compile(r'\b(\d{2,3}[A-Z]{1,3}\d{2,4})\b'),
]

# Resistance/Capacitance value patterns
VALUE_PATTERNS = [
    re.compile(r'\b(\d+(?:\.\d+)?\s*[kKmMuUnNpP]?\s*[ΩΩF])\b'),      # 100kΩ, 10uF
    re.compile(r'\b(\d+(?:\.\d+)?\s*(?:ohm|Ohm|OHM)s?)\b'),          # 100 ohms
    re.compile(r'\b(\d+(?:\.\d+)?\s*(?:uF|mF|nF|pF))\b', re.IGNORECASE),  # Capacitance
    re.compile(r'\b(\d+(?:\.\d+)?\s*(?:uH|mH|nH))\b', re.IGNORECASE),     # Inductance
    re.compile(r'\b(\d+(?:\.\d+)?\s*[Vv])\b'),                        # Voltage rating
]


# ── OCR Reader ───────────────────────────────────────────────────────────────

class OCRReader:
    """
    Read text/part numbers from component crop images.

    Usage:
        reader = OCRReader()
        result = reader.read(crop_image)
        print(result.part_numbers)  # ['NE555', 'DIP-8']
    """

    def __init__(self, prefer_paddle: bool = False):
        """
        Args:
            prefer_paddle: Use PaddleOCR as primary engine (better for
                           small text on ICs but heavier dependency)
        """
        self.paddle_ocr = None

        if prefer_paddle and PADDLE_AVAILABLE:
            self.primary = "paddle"
            self.paddle_ocr = PaddleOCR(
                use_angle_cls=True,
                lang='en',
                show_log=False,
            )
            print("[OCR] Primary engine: PaddleOCR")
        elif TESSERACT_AVAILABLE:
            # Verify tesseract binary is actually installed (not just the Python wrapper)
            try:
                pytesseract.get_tesseract_version()
                self.primary = "tesseract"
                print("[OCR] Primary engine: Tesseract")
            except Exception:
                if PADDLE_AVAILABLE:
                    self.primary = "paddle"
                    self.paddle_ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
                    print("[OCR] Tesseract binary not found, using PaddleOCR")
                else:
                    self.primary = "none"
                    print("[OCR] WARNING: Tesseract binary not installed. OCR disabled.")
                    print("       Install: https://github.com/UB-Mannheim/tesseract/wiki")
        elif PADDLE_AVAILABLE:
            self.primary = "paddle"
            self.paddle_ocr = PaddleOCR(
                use_angle_cls=True,
                lang='en',
                show_log=False,
            )
            print("[OCR] Primary engine: PaddleOCR (Tesseract not available)")
        else:
            self.primary = "none"
            print("[OCR] WARNING: No OCR engine available. Install pytesseract or paddleocr.")

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        """Preprocess image for better OCR accuracy on small component text."""
        # Convert to grayscale
        if len(image.shape) == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image.copy()

        # Upscale small images (IC markings are tiny)
        h, w = gray.shape[:2]
        if max(h, w) < 200:
            scale = 200 / max(h, w)
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        # Sharpen
        kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])
        gray = cv2.filter2D(gray, -1, kernel)

        # Adaptive threshold for text on various backgrounds
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        # Also try inverted (white text on dark IC packages)
        inverted = cv2.bitwise_not(binary)

        return binary, inverted

    def _read_tesseract(self, image: np.ndarray) -> tuple[str, float]:
        """Read text using Tesseract OCR."""
        try:
            binary, inverted = self._preprocess(image)
        except Exception:
            return "", 0.0

        try:
            # Try both normal and inverted
            results = []
            for img in [binary, inverted]:
                data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            texts = []
            confs = []
            for i, text in enumerate(data['text']):
                text = text.strip()
                conf = int(data['conf'][i])
                if text and conf > 30:  # Filter low-confidence noise
                    texts.append(text)
                    confs.append(conf / 100.0)
            if texts:
                results.append((' '.join(texts), sum(confs) / len(confs)))

            if not results:
                return "", 0.0

            # Return the result with more text (likely more successful)
            results.sort(key=lambda x: len(x[0]), reverse=True)
            return results[0]
        except Exception as e:
            return "", 0.0

    def _read_paddle(self, image: np.ndarray) -> tuple[str, float]:
        """Read text using PaddleOCR."""
        result = self.paddle_ocr.ocr(image, cls=True)

        if not result or not result[0]:
            return "", 0.0

        texts = []
        confs = []
        for line in result[0]:
            text = line[1][0]
            conf = line[1][1]
            if text.strip():
                texts.append(text.strip())
                confs.append(conf)

        if not texts:
            return "", 0.0

        return ' '.join(texts), sum(confs) / len(confs)

    def _extract_part_numbers(self, text: str) -> list[str]:
        """Extract part numbers from raw OCR text using known patterns."""
        found = []
        for pattern in PART_NUMBER_PATTERNS:
            matches = pattern.findall(text)
            found.extend(matches)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for pn in found:
            pn_upper = pn.upper().strip()
            if pn_upper not in seen and len(pn_upper) >= 3:
                seen.add(pn_upper)
                unique.append(pn_upper)

        return unique

    def read(self, image: np.ndarray) -> OCRResult:
        """
        Read text from a component crop image.

        Args:
            image: BGR numpy array of the cropped component

        Returns:
            OCRResult with raw text, extracted part numbers, and confidence
        """
        if self.primary == "none":
            return OCRResult(raw_text="", method="none", confidence=0.0)

        # Run primary OCR
        if self.primary == "tesseract":
            raw_text, confidence = self._read_tesseract(image)
            method = "tesseract"
        else:
            raw_text, confidence = self._read_paddle(image)
            method = "paddle"

        # If primary returned nothing, try fallback
        if not raw_text.strip() and self.primary == "tesseract" and PADDLE_AVAILABLE:
            if self.paddle_ocr is None:
                self.paddle_ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
            raw_text, confidence = self._read_paddle(image)
            method = "paddle_fallback"

        # Extract part numbers
        part_numbers = self._extract_part_numbers(raw_text)

        # Extract all text lines
        all_lines = [line.strip() for line in raw_text.split('\n') if line.strip()]

        return OCRResult(
            raw_text=raw_text,
            part_numbers=part_numbers,
            all_lines=all_lines,
            confidence=confidence,
            method=method,
        )

    def read_batch(self, images: list[np.ndarray]) -> list[OCRResult]:
        """Read text from multiple component crops."""
        return [self.read(img) for img in images]
