"""Détection prudente des losanges et conversion pixel/cellule logique."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
import math

import cv2
import numpy as np

from combatbot.models import Cell
from combatbot.vision.combat_models import (
    GRID_SOURCE_LEGACY, GRID_SOURCE_VISION, CellVisualState, CombatGridObservation, GridCalibration,
    ObservedCell,
)


def _deduplicate(candidates: list[tuple[float, float, float, float, float]]) -> list[tuple[float, float, float, float, float]]:
    selected: list[tuple[float, float, float, float, float]] = []
    for candidate in sorted(candidates, key=lambda value: value[4], reverse=True):
        if all(math.hypot(candidate[0] - prior[0], candidate[1] - prior[1]) > min(candidate[2], prior[2]) * 0.35
               for prior in selected):
            selected.append(candidate)
    return selected


def _logical_coordinates(centers: list[tuple[float, float, float, float, float]], width: float,
                         height: float) -> list[tuple[Cell, tuple[float, float, float, float, float]]]:
    anchor = min(centers, key=lambda value: (value[1], value[0]))
    found: dict[Cell, tuple[float, float, float, float, float]] = {}
    for item in centers:
        dx = item[0] - anchor[0]
        dy = item[1] - anchor[1]
        # Bases isométriques : (+w/2,+h/2) et (-w/2,+h/2).
        logical = Cell(round(dx / width + dy / height), round(-dx / width + dy / height))
        previous = found.get(logical)
        if previous is None or item[4] > previous[4]:
            found[logical] = item
    return sorted(found.items(), key=lambda pair: (pair[0].y, pair[0].x))


# Seuils Canny : historiques (grille détectée) et sensibles (calibration de projection).
LEGACY_CANNY = (35, 110)
CALIBRATION_CANNY = (10, 30)  # mesuré : dallage en damier peu contrasté du client réel


def detect_diamond_candidates(image: np.ndarray, canny: tuple[int, int] = LEGACY_CANNY
                              ) -> list[tuple[float, float, float, float, float]]:
    """Candidats de losanges (cx, cy, largeur, hauteur, score) dans le crop combat.

    Générateur de candidats uniquement : n'attribue ni identité, ni voisinage,
    ni nombre de cellules (LOT 3B-2 : ces faits viennent de GameData).
    """
    if image.size == 0:
        return []
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, *canny)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    image_area = image.shape[0] * image.shape[1]
    candidates: list[tuple[float, float, float, float, float]] = []
    for contour in contours:
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.055 * perimeter, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        x, y, width, height = cv2.boundingRect(approx)
        area = abs(cv2.contourArea(approx))
        if not (image_area * 0.00008 <= area <= image_area * 0.025):
            continue
        if width < 12 or height < 6 or not 1.25 <= width / height <= 4.8:
            continue
        points = approx[:, 0, :]
        # Un losange a un sommet près de chaque milieu du rectangle englobant.
        extrema = [points[np.argmin(points[:, 0])], points[np.argmax(points[:, 0])],
                   points[np.argmin(points[:, 1])], points[np.argmax(points[:, 1])]]
        cx, cy = x + width / 2, y + height / 2
        symmetry = np.mean([abs(float(p[1]) - cy) / height for p in extrema[:2]] +
                           [abs(float(p[0]) - cx) / width for p in extrema[2:]])
        score = max(0.0, 1.0 - symmetry * 5)
        if score >= 0.35:
            candidates.append((cx, cy, float(width), float(height), score))
    candidates = _deduplicate(candidates)
    return candidates


def infer_combat_grid(image: np.ndarray, manual: GridCalibration | None = None) -> CombatGridObservation:
    if image.size == 0:
        return CombatGridObservation()
    if manual is not None and manual.logical_cells:
        cells = []
        ox, oy = manual.origin
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 35, 110)
        supports: list[float] = []
        for logical in manual.logical_cells:
            cx = ox + (logical.x - logical.y) * manual.cell_width / 2
            cy = oy + (logical.x + logical.y) * manual.cell_height / 2
            polygon = _diamond(cx, cy, manual.cell_width, manual.cell_height)
            mask = np.zeros(edges.shape, np.uint8)
            cv2.polylines(mask, [np.asarray(polygon, np.int32)], True, 255, 3)
            support = float((edges[mask > 0] > 0).mean()) if np.any(mask) else 0.0
            confidence = min(1.0, support / 0.22)
            supports.append(confidence)
            cells.append(ObservedCell(logical, (round(cx), round(cy)), polygon,
                                      CellVisualState.UNKNOWN, confidence))
        grid_confidence = float(np.median(supports)) if supports else 0.0
        return CombatGridObservation(tuple(cells), manual.cell_width, manual.cell_height, 0.0,
                                     grid_confidence, GRID_SOURCE_LEGACY)

    candidates = detect_diamond_candidates(image)
    if len(candidates) < 4:
        return CombatGridObservation()

    widths = np.asarray([item[2] for item in candidates])
    heights = np.asarray([item[3] for item in candidates])
    median_width, median_height = float(np.median(widths)), float(np.median(heights))
    consistent = [item for item in candidates
                  if 0.7 <= item[2] / median_width <= 1.3 and 0.7 <= item[3] / median_height <= 1.3]
    if len(consistent) < 4:
        return CombatGridObservation()
    logical = _logical_coordinates(consistent, median_width, median_height)
    cells = tuple(
        ObservedCell(cell, (round(item[0]), round(item[1])),
                     _diamond(item[0], item[1], item[2], item[3]),
                     CellVisualState.UNKNOWN, float(item[4]))
        for cell, item in logical
    )
    count_score = min(1.0, len(cells) / 12)
    consistency = max(0.0, 1.0 - float(np.std(widths) / max(median_width, 1)))
    confidence = max(0.0, min(1.0, 0.55 * count_score + 0.45 * consistency))
    return CombatGridObservation(cells, median_width, median_height, 0.0, confidence, GRID_SOURCE_VISION)


def _diamond(cx: float, cy: float, width: float, height: float) -> tuple[tuple[int, int], ...]:
    return ((round(cx), round(cy - height / 2)), (round(cx + width / 2), round(cy)),
            (round(cx), round(cy + height / 2)), (round(cx - width / 2), round(cy)))


def classify_cell_occupancy(image: np.ndarray, grid: CombatGridObservation,
                            player_reference_hsv: tuple[float, float, float] | None = None
                            ) -> tuple[CombatGridObservation, Cell | None, float, tuple[tuple[Cell, tuple[int, int], float], ...]]:
    """Cherche des marqueurs colorés; le joueur exige une signature confirmée."""
    if not grid.cells:
        return grid, None, 0.0, ()
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    player: tuple[Cell, float] | None = None
    enemies: list[tuple[Cell, tuple[int, int], float]] = []
    updated = []
    for observed in grid.cells:
        cx, cy = observed.center
        radius_x = max(3, round((grid.cell_width or 20) * 0.23))
        radius_y = max(3, round((grid.cell_height or 10) * 0.42))
        x0, x1 = max(0, cx - radius_x), min(hsv.shape[1], cx + radius_x + 1)
        y0, y1 = max(0, cy - radius_y), min(hsv.shape[0], cy + radius_y + 1)
        patch = hsv[y0:y1, x0:x1]
        if patch.size == 0:
            updated.append(observed)
            continue
        saturation = patch[:, :, 1]
        red = (((patch[:, :, 0] <= 10) | (patch[:, :, 0] >= 170)) & (saturation >= 110))
        red_ratio = float(red.mean())
        player_score = 0.0
        if player_reference_hsv is not None:
            reference = np.asarray(player_reference_hsv, dtype=float)
            pixels = patch.reshape(-1, 3).astype(float)
            hue_delta = np.minimum(abs(pixels[:, 0] - reference[0]), 180 - abs(pixels[:, 0] - reference[0]))
            similarity = (hue_delta <= 9) & (abs(pixels[:, 1] - reference[1]) <= 65) & (pixels[:, 1] >= 65)
            player_score = min(1.0, float(similarity.mean()) * 7)
        state = CellVisualState.UNKNOWN
        confidence = observed.confidence
        if player_score >= 0.45 and (player is None or player_score > player[1]):
            player = (observed.logical, player_score)
            state = CellVisualState.OCCUPIED
            confidence = player_score
        elif red_ratio >= 0.055:
            score = min(1.0, red_ratio * 6)
            enemies.append((observed.logical, observed.center, score))
            state = CellVisualState.OCCUPIED
            confidence = score
        # replace() keeps cell_id and GameData static fields untouched.
        updated.append(replace(observed, state=state, confidence=confidence))
    return replace(grid, cells=tuple(updated)), \
        (player[0] if player else None), (player[1] if player else 0.0), tuple(enemies)
