"""Segmentation des cases et reconnaissance d'icônes par empreinte visuelle."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from combatbot.vision.models import IconCandidate, ScanResult


@dataclass(frozen=True)
class KnownIcon:
    spell_id: int
    name: str
    icon_png: bytes
    visual_hash: str


def visual_hash(image: np.ndarray) -> str:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in bits.flat:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _decode_icon(png: bytes) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(png, dtype=np.uint8), cv2.IMREAD_COLOR)


def _similarity(image: np.ndarray, candidate_hash: str, known: KnownIcon) -> float:
    saved = _decode_icon(known.icon_png)
    if saved is None:
        return 0.0
    hash_score = 1 - (int(candidate_hash, 16) ^ int(known.visual_hash, 16)).bit_count() / 64
    gray_a = cv2.cvtColor(cv2.resize(image, (32, 32)), cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(cv2.resize(saved, (32, 32)), cv2.COLOR_BGR2GRAY)
    if float(gray_a.std()) < 1 or float(gray_b.std()) < 1:
        correlation = 0.0
    else:
        correlation = float(cv2.matchTemplate(gray_a, gray_b, cv2.TM_CCOEFF_NORMED)[0, 0])
    return max(0.0, min(1.0, 0.65 * hash_score + 0.35 * max(0.0, correlation)))


def _presence(image: np.ndarray) -> float:
    if image.size == 0:
        return 0.0
    height, width = image.shape[:2]
    inset_y, inset_x = max(1, height // 8), max(1, width // 8)
    center = image[inset_y:height - inset_y, inset_x:width - inset_x]
    if center.size == 0:
        center = image
    gray = cv2.cvtColor(center, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(center, cv2.COLOR_BGR2HSV)
    edges = cv2.Canny(gray, 50, 130)
    texture = min(1.0, float(gray.std()) / 45)
    edge_density = min(1.0, float(np.mean(edges > 0)) / 0.15)
    saturation = min(1.0, float(np.mean(hsv[:, :, 1])) / 90)
    return max(0.0, min(1.0, 0.48 * texture + 0.34 * edge_density + 0.18 * saturation))


def infer_grid_shape(bar: np.ndarray) -> tuple[int, int, float] | None:
    """Propose a regular near-square icon grid; caller must show it for review."""
    if bar.ndim != 3 or min(bar.shape[:2]) < 24:
        return None
    height, width = bar.shape[:2]
    gray = cv2.cvtColor(bar, cv2.COLOR_BGR2GRAY)
    gradient_x = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0)).mean(axis=0)
    gradient_y = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1)).mean(axis=1)
    baseline_x = float(np.mean(gradient_x)) + 1
    baseline_y = float(np.mean(gradient_y)) + 1
    best: tuple[float, int, int] | None = None
    for rows in range(1, 6):
        cell_h = height / rows
        if not 22 <= cell_h <= 100:
            continue
        for columns in range(1, 31):
            cell_w = width / columns
            if not 22 <= cell_w <= 100 or not 0.75 <= cell_w / cell_h <= 1.3:
                continue
            xs = [round(index * cell_w) for index in range(1, columns)]
            ys = [round(index * cell_h) for index in range(1, rows)]
            if not xs or not ys:
                continue
            x_score = float(np.mean([gradient_x[max(0, x - 2):min(width, x + 3)].max() for x in xs])) / baseline_x
            y_score = float(np.mean([gradient_y[max(0, y - 2):min(height, y + 3)].max() for y in ys])) / baseline_y
            score = min(x_score, y_score) + 0.03 * min(columns, 15)
            if best is None or score > best[0]:
                best = (score, columns, rows)
    if best is None or best[0] < 1.15:
        return None
    return best[1], best[2], min(0.85, max(0.3, (best[0] - 1) / 2))


def slot_icons(bar: np.ndarray, columns: int, rows: int):
    """Découpage unique de la barre calibrée : (case 1-based, icône sans bordure), ligne par ligne."""
    height, width = bar.shape[:2]
    for row in range(rows):
        for column in range(columns):
            x0, x1 = round(column * width / columns), round((column + 1) * width / columns)
            y0, y1 = round(row * height / rows), round((row + 1) * height / rows)
            inset = max(2, min(x1 - x0, y1 - y0) // 12)
            yield row * columns + column + 1, bar[y0 + inset:y1 - inset, x0 + inset:x1 - inset].copy()


def bar_signature(bar: np.ndarray, columns: int, rows: int, presence_threshold: float = 0.26
                  ) -> dict[int, str | None]:
    """Hash visuel de chaque case (None = case vide) : sert à invalider une page de sorts confirmée."""
    if bar.ndim != 3 or bar.shape[2] != 3 or bar.dtype != np.uint8:
        raise ValueError("La barre doit être une image BGR uint8")
    if columns < 1 or rows < 1 or columns > 30 or rows > 5:
        raise ValueError("Colonnes ou rangées invalides")
    return {slot: (visual_hash(icon) if _presence(icon) >= presence_threshold else None)
            for slot, icon in slot_icons(bar, columns, rows)}


def scan_spell_bar(
    bar: np.ndarray,
    *,
    page: int,
    columns: int,
    rows: int,
    known_icons: list[KnownIcon] | None = None,
    match_threshold: float = 0.88,
    presence_threshold: float = 0.26,
) -> ScanResult:
    if bar.ndim != 3 or bar.shape[2] != 3 or bar.dtype != np.uint8:
        raise ValueError("La barre doit être une image BGR uint8")
    if page < 1 or columns < 1 or rows < 1 or columns > 30 or rows > 5:
        raise ValueError("Page, colonnes ou rangées invalides")
    if not 0 <= match_threshold <= 1 or not 0 <= presence_threshold <= 1:
        raise ValueError("Seuil de confiance invalide")
    height, width = bar.shape[:2]
    if width // columns < 16 or height // rows < 16:
        raise ValueError("La barre calibrée est trop petite pour ce nombre de cases")
    known_icons = known_icons or []
    detections: list[IconCandidate] = []
    empty: list[int] = []
    seen: list[tuple[int, str]] = []
    for slot, icon in slot_icons(bar, columns, rows):
        presence = _presence(icon)
        if presence < presence_threshold:
            empty.append(slot)
            continue
        fingerprint = visual_hash(icon)
        success, encoded = cv2.imencode(".png", icon)
        if not success:
            raise RuntimeError("Impossible d'encoder une icône détectée")
        duplicate = next((index for index, prior in seen if (int(prior, 16) ^ int(fingerprint, 16)).bit_count() <= 2), None)
        seen.append((slot, fingerprint))
        best = max((( _similarity(icon, fingerprint, item), item) for item in known_icons),
                   key=lambda pair: pair[0], default=(0.0, None))
        confidence, known = best
        if duplicate is not None:
            status = "À vérifier"
        elif known is not None and confidence >= match_threshold:
            status = "Reconnu"
        elif known is not None and confidence >= max(0.55, match_threshold - 0.12):
            status = "À vérifier"
        else:
            status = "Inconnu"
        detections.append(IconCandidate(
            page=page, slot=slot, icon_png=encoded.tobytes(), visual_hash=fingerprint,
            presence_confidence=presence, recognition_confidence=confidence,
            status=status, known_spell_id=known.spell_id if known and confidence >= max(0.55, match_threshold - 0.12) else None,
            known_name=known.name if known and confidence >= max(0.55, match_threshold - 0.12) else None,
            duplicate_of=duplicate,
        ))
    return ScanResult(page, rows * columns, tuple(empty), tuple(detections))
