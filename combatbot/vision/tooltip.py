"""OCR local d'infobulles visibles et extraction prudente des champs lisibles."""

from __future__ import annotations

from functools import lru_cache
import re

import cv2
import numpy as np

from combatbot.vision.models import RecognizedText


def locate_new_tooltip(before: np.ndarray, after: np.ndarray) -> tuple[int, int, int, int] | None:
    """Locate a newly visible panel; return None when motion/animation is ambiguous."""
    if before.shape != after.shape or before.ndim != 3:
        return None
    difference = cv2.cvtColor(cv2.absdiff(before, after), cv2.COLOR_BGR2GRAY)
    mask = np.uint8(difference > 25) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 17), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    height, width = mask.shape
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if w < 95 or h < 45 or area < 6000 or area > width * height * 0.35:
            continue
        if not 0.7 <= w / h <= 8:
            continue
        changed = float(np.mean(mask[y:y + h, x:x + w] > 0))
        if changed < 0.12:
            continue
        candidates.append((area * changed, x, y, w, h))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    if len(candidates) > 1 and candidates[1][0] > 0.7 * candidates[0][0]:
        return None
    _, x, y, w, h = candidates[0]
    padding = 8
    x0, y0 = max(0, x - padding), max(0, y - padding)
    x1, y1 = min(width, x + w + padding), min(height, y + h + padding)
    return x0, y0, x1 - x0, y1 - y0


FIELD_NAMES = (
    "name", "ap_cost", "min_range", "max_range", "modifiable_range",
    "line_cast", "line_of_sight", "per_turn", "per_target", "effects", "damage",
)


OCR_THREADS_ENV = "DOFBOT_OCR_THREADS"


def ocr_threads_setting() -> int | None:
    """Threads ONNX Runtime demandés (``DOFBOT_OCR_THREADS``) ; None = réglage par défaut du moteur.

    Aucune valeur n'est imposée : ``python -m combatbot.benchmark --ocr-threads-benchmark`` mesure sur
    le PC le temps OCR et le CPU pour chaque valeur avant d'en choisir une.
    """
    import os
    raw = os.environ.get(OCR_THREADS_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 1 else None


def create_ocr_engine(threads: int | None = None):
    """Nouveau moteur RapidOCR ; ``threads`` limite intra/inter-op d'ONNX Runtime si le moteur l'accepte."""
    from rapidocr import RapidOCR
    if threads is None:
        return RapidOCR()
    params = {"EngineConfig.onnxruntime.intra_op_num_threads": threads,
              "EngineConfig.onnxruntime.inter_op_num_threads": 1}
    try:
        return RapidOCR(params=params)
    except (TypeError, KeyError, ValueError):
        return RapidOCR()                     # version sans ce réglage : comportement par défaut


@lru_cache(maxsize=1)
def _ocr_engine():
    """Moteur unique pour tout le processus (chargé une seule fois)."""
    return create_ocr_engine(ocr_threads_setting())


def read_visible_text(image: np.ndarray) -> tuple[str, float]:
    if image.size == 0:
        raise ValueError("La zone OCR est vide")
    result = _ocr_engine()(image)
    raw_texts = getattr(result, "txts", None)
    raw_scores = getattr(result, "scores", None)
    raw_boxes = getattr(result, "boxes", None)
    texts = tuple(raw_texts) if raw_texts is not None else ()
    scores = tuple(raw_scores) if raw_scores is not None else ()
    boxes = tuple(raw_boxes) if raw_boxes is not None else ()
    if not texts:
        return "", 0.0
    if boxes and len(boxes) == len(texts):
        fragments = []
        for text, box in zip(texts, boxes):
            points = np.asarray(box)
            fragments.append((float(points[:, 1].mean()), float(points[:, 0].mean()), str(text)))
        fragments.sort(key=lambda item: (round(item[0] / 12), item[1]))
        lines: list[list[str]] = []
        last_y: float | None = None
        for y, _x, text in fragments:
            if last_y is None or abs(y - last_y) > 12:
                lines.append([])
            lines[-1].append(text)
            last_y = y
        raw = "\n".join(" ".join(line) for line in lines)
    else:
        raw = "\n".join(map(str, texts))
    return raw, float(sum(scores) / len(scores)) if scores else 0.0


def parse_tooltip(raw_text: str, confidence: float = 0.0) -> RecognizedText:
    """Only explicit visible words/numbers produce values. Missing fields stay None."""
    text = raw_text.replace("\u00a0", " ")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    fields: dict[str, str | int | bool | None] = {name: None for name in FIELD_NAMES}

    for line in lines:
        if not re.search(r"\d|\b(?:PA|PO|PM|port[ée]e|lancer|ligne|effet|d[ée]g[âa]t|dommage|cible|tour)\b", line, re.I):
            fields["name"] = line
            break

    pa = re.search(r"\b(?:co[uû]t\s*(?:en)?\s*)?(\d{1,2})\s*PA\b|\bPA\s*[:=]\s*(\d{1,2})\b", text, re.I)
    if pa:
        fields["ap_cost"] = int(pa.group(1) or pa.group(2))
    reach = re.search(r"\b(?:port[ée]e|PO)\s*[:=]?\s*(\d{1,2})\s*(?:-|à|a|–)\s*(\d{1,2})", text, re.I)
    if reach:
        minimum, maximum = int(reach.group(1)), int(reach.group(2))
        if minimum <= maximum:
            fields["min_range"], fields["max_range"] = minimum, maximum
    else:
        single = re.search(r"\b(?:port[ée]e|PO)\s*[:=]?\s*(\d{1,2})\b", text, re.I)
        if single:
            fields["max_range"] = int(single.group(1))

    for key, positive, negative in (
        ("modifiable_range", r"port[ée]e\s+modifiable", r"port[ée]e\s+non\s+modifiable"),
        ("line_cast", r"lancer\s+en\s+ligne", r"(?:pas|sans)\s+de?\s+lancer\s+en\s+ligne"),
        ("line_of_sight", r"ligne\s+de\s+vue\s+(?:requise|n[ée]cessaire)", r"sans\s+ligne\s+de\s+vue"),
    ):
        if re.search(negative, text, re.I):
            fields[key] = False
        elif re.search(positive, text, re.I):
            fields[key] = True

    for key, noun in (("per_turn", "tour"), ("per_target", "cible")):
        match = re.search(rf"\b(\d{{1,2}})\s*(?:lancers?\s*)?(?:par|/)\s*{noun}\b", text, re.I)
        if match:
            fields[key] = int(match.group(1))
    effects = [line for line in lines if re.search(r"effet|dommage|d[ée]g[âa]t", line, re.I)]
    if effects:
        fields["effects"] = " ; ".join(effects)
        damage = re.search(r"(?:dommages?|d[ée]g[âa]ts?)\s*[:=]?\s*(\d{1,3})", fields["effects"], re.I)
        if damage:
            fields["damage"] = int(damage.group(1))
    uncertain = tuple(key for key, value in fields.items() if value is None)
    return RecognizedText(text, confidence, fields, uncertain)


def recognize_tooltip(image: np.ndarray) -> RecognizedText:
    raw, confidence = read_visible_text(image)
    if not raw:
        raise ValueError("Aucun texte lisible dans l'infobulle sélectionnée")
    return parse_tooltip(raw, confidence)
