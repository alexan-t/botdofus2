"""OCR ciblé des petits compteurs PA/PM, sans valeur de repli inventée."""

from __future__ import annotations

from collections.abc import Callable
import re

import cv2
import numpy as np


NumberReader = Callable[[np.ndarray], tuple[int | None, float]]


def preprocess_number_variants(image: np.ndarray) -> tuple[np.ndarray, ...]:
    if image.size == 0:
        return ()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    scale = max(2.0, min(5.0, 120 / max(1, gray.shape[0])))
    enlarged = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    normalized = cv2.normalize(enlarged, None, 0, 255, cv2.NORM_MINMAX)
    _, otsu = cv2.threshold(normalized, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(normalized, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 31, 7)
    return normalized, otsu, cv2.bitwise_not(otsu), adaptive


def read_small_number(image: np.ndarray, minimum: int = 0, maximum: int = 99) -> tuple[int | None, float]:
    """Vote entre plusieurs prétraitements RapidOCR; l'échec reste ``None``."""
    try:
        from combatbot.vision.tooltip import _ocr_engine
        engine = _ocr_engine()
    except Exception:
        return None, 0.0
    candidates: dict[int, list[float]] = {}
    for variant in preprocess_number_variants(image):
        try:
            result = engine(variant)
            texts = tuple(getattr(result, "txts", None) or ())
            scores = tuple(getattr(result, "scores", None) or ())
        except Exception:
            continue
        for index, text in enumerate(texts):
            match = re.search(r"(?<!\d)(\d{1,2})(?!\d)", str(text))
            if not match:
                continue
            value = int(match.group(1))
            if minimum <= value <= maximum:
                candidates.setdefault(value, []).append(float(scores[index]) if index < len(scores) else 0.5)
    if not candidates:
        return None, 0.0
    ranked = sorted(candidates.items(), key=lambda item: (len(item[1]), sum(item[1]) / len(item[1])), reverse=True)
    value, scores = ranked[0]
    agreement = len(scores) / max(1, len(preprocess_number_variants(image)))
    confidence = min(1.0, (sum(scores) / len(scores)) * (0.65 + 0.35 * agreement))
    return value, confidence

