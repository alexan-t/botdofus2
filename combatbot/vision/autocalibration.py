"""Propositions prudentes de zones sur une capture ; aucune position présumée n'est confirmée."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Literal
from typing import Mapping

import cv2
import numpy as np

from combatbot.vision.models import Calibration, CapturedFrame, ZoneEvidence
from combatbot.vision.coordinates import ClientBox


@dataclass(frozen=True)
class ZoneSuggestion:
    """Rectangle proposé dans le référentiel client, en pixels physiques."""

    rect: tuple[int, int, int, int]
    evidence: ZoneEvidence
    coordinate_space: Literal["client"] = "client"

    @property
    def client_box(self) -> ClientBox:
        return ClientBox(*self.rect)

    def to_dict(self) -> dict[str, object]:
        return {
            "rect": self.client_box.to_dict(),
            "coordinate_space": self.coordinate_space,
            "evidence": self.evidence.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ZoneSuggestion":
        if raw.get("coordinate_space", "client") != "client":
            raise ValueError("Référentiel de suggestion inconnu")
        rect_raw, evidence_raw = raw.get("rect"), raw.get("evidence")
        if not isinstance(rect_raw, Mapping) or not isinstance(evidence_raw, Mapping):
            raise ValueError("Suggestion sérialisée invalide")
        return cls(ClientBox.from_dict(rect_raw).rounded(), ZoneEvidence.from_dict(evidence_raw))


def _icon_rectangles(image: np.ndarray) -> list[tuple[int, int, int, int]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    rectangles = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        if 20 <= width <= 110 and 20 <= height <= 110 and 0.75 <= width / height <= 1.3:
            rectangles.append((x, y, width, height))
    return rectangles


def find_spell_bar(image: np.ndarray) -> ZoneSuggestion | None:
    height, width = image.shape[:2]
    reference = cv2.imread(str(Path(__file__).resolve().parents[2] / "assets" / "user_spellbar_reference.png"))
    if reference is not None:
        for scale in (1.0, 0.8, 1.2):
            template = cv2.resize(reference, None, fx=scale, fy=scale)
            if template.shape[0] > height or template.shape[1] > width:
                continue
            score_map = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, point = cv2.minMaxLoc(score_map)
            if score >= 0.93:
                bar_width = round(575 / 618 * template.shape[1])
                return ZoneSuggestion((point[0], point[1], bar_width, template.shape[0]),
                                      ZoneEvidence(float(score), "template utilisateur validé", "détectée"))
    boxes = [box for box in _icon_rectangles(image) if box[1] > height * 0.35]
    best: list[tuple[int, int, int, int]] = []
    for anchor in boxes:
        ax, ay, aw, ah = anchor
        row = [box for box in boxes if abs(box[1] - ay) <= max(5, ah * 0.18)
               and 0.78 <= box[2] / aw <= 1.22 and 0.78 <= box[3] / ah <= 1.22]
        row.sort(key=lambda box: box[0])
        contiguous: list[tuple[int, int, int, int]] = []
        for box in row:
            if not contiguous or 0.7 * aw <= box[0] - contiguous[-1][0] <= 1.35 * aw:
                contiguous.append(box)
            else:
                if len(contiguous) > len(best):
                    best = contiguous
                contiguous = [box]
        if len(contiguous) > len(best):
            best = contiguous
    if len(best) < 6:
        return None
    left = max(0, min(box[0] for box in best) - 4)
    right = min(width, max(box[0] + box[2] for box in best) + 4)
    top = max(0, min(box[1] for box in best) - 4)
    bottom = min(height, max(box[1] + box[3] for box in best) + 4)
    # The second row is included only when matching tile columns are observable.
    below = [box for box in boxes if top + best[0][3] * 0.8 <= box[1] <= top + best[0][3] * 1.4
             and any(abs(box[0] - prior[0]) <= 0.2 * best[0][2] for prior in best)]
    if len(below) >= max(4, len(best) // 2):
        bottom = min(height, max(box[1] + box[3] for box in below) + 4)
    confidence = min(0.75, 0.35 + 0.025 * len(best) + (0.1 if below else 0))
    return ZoneSuggestion((left, top, right - left, bottom - top),
                          ZoneEvidence(confidence, "contours de cases répétées", "proposée"))


def _ocr_zone_suggestions(image: np.ndarray) -> dict[str, ZoneSuggestion]:
    try:
        from combatbot.vision.tooltip import _ocr_engine
        result = _ocr_engine()(image)
        raw_texts = getattr(result, "txts", None)
        raw_boxes = getattr(result, "boxes", None)
        raw_scores = getattr(result, "scores", None)
        texts = tuple(raw_texts) if raw_texts is not None else ()
        boxes = tuple(raw_boxes) if raw_boxes is not None else ()
        scores = tuple(raw_scores) if raw_scores is not None else ()
    except Exception:
        return {}
    found: dict[str, ZoneSuggestion] = {}
    patterns = {
        "hp": r"\b(?:PV|HP|points? de vie)\b",
        "ap": r"\bPA\b",
        "mp": r"\bPM\b",
        "end_turn": r"\b(?:fin|terminer)\s+(?:du\s+)?tour\b",
    }
    for index, (text, box) in enumerate(zip(texts, boxes)):
        points = np.asarray(box, dtype=float)
        if points.shape != (4, 2):
            continue
        x0, y0 = np.floor(points.min(axis=0)).astype(int)
        x1, y1 = np.ceil(points.max(axis=0)).astype(int)
        x0, y0 = max(0, x0 - 10), max(0, y0 - 8)
        x1, y1 = min(image.shape[1], x1 + 10), min(image.shape[0], y1 + 8)
        if x1 <= x0 or y1 <= y0:
            continue
        for zone, pattern in patterns.items():
            if zone not in found and re.search(pattern, str(text), re.I):
                confidence = float(scores[index]) if index < len(scores) else 0.5
                found[zone] = ZoneSuggestion((x0, y0, x1 - x0, y1 - y0),
                                             ZoneEvidence(confidence, "OCR du libellé visible", "proposée"))
    return found


def suggest_zones(frame: CapturedFrame) -> dict[str, ZoneSuggestion]:
    image = frame.image
    height, width = image.shape[:2]
    if height < 100 or width < 100:
        return {}
    suggestions: dict[str, ZoneSuggestion] = {}
    spell_bar = find_spell_bar(image)
    if spell_bar is not None:
        suggestions["spell_bar"] = spell_bar
    suggestions.update(_ocr_zone_suggestions(image))
    # Zone de combat : pleine largeur, du haut jusqu'au-dessus du HUD s'il est repéré ; sinon la
    # proportion mesurée sur le client réel (1151 / 1377 ≈ 0,838). Toujours à vérifier.
    hud_tops = [suggestions[zone].rect[1] for zone in ("spell_bar", "hp", "ap", "mp")
                if zone in suggestions and suggestions[zone].rect[1] > height * 0.5]
    bottom = min(hud_tops) - round(height * 0.01) if hud_tops else round(height * 0.838)
    suggestions["combat"] = ZoneSuggestion(
        (0, 0, width, bottom),
        ZoneEvidence(0.35, "pleine largeur au-dessus du HUD", "proposée"),
    )
    return suggestions


def zones_needing_review(calibration: Calibration, frame: CapturedFrame,
                         suggestions: dict[str, ZoneSuggestion]) -> set[str]:
    """Flag geometry changes and conflicting visual anchors, without discarding saved data."""
    layout_status = calibration.layout_compatibility(frame.client.width, frame.client.height)
    if not layout_status.compatible or layout_status.requires_revalidation:
        return set(calibration.zones)
    width_change = abs(frame.client.width / calibration.client_width - 1)
    height_change = abs(frame.client.height / calibration.client_height - 1)
    review = set(calibration.zones) if max(width_change, height_change) > 0.01 else set()
    for zone, proposal in suggestions.items():
        if zone not in calibration.zones or zone == "combat" or proposal.evidence.confidence < 0.65:
            continue
        saved = calibration.zones[zone].pixels(frame.client.width, frame.client.height)
        current = proposal.client_box.rounded()
        sx, sy, sw, sh = saved
        px, py, pw, ph = current
        overlap = max(0, min(sx + sw, px + pw) - max(sx, px)) * max(0, min(sy + sh, py + ph) - max(sy, py))
        union = sw * sh + pw * ph - overlap
        if union and overlap / union < 0.3:
            review.add(zone)
    return review
