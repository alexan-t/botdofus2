"""Fixtures synthétiques minimales pour le LOT 3B-5 (aucune capture réelle du client)."""

from __future__ import annotations

import cv2
import numpy as np

from combatbot.gamedata.models import GridCoordinate
from combatbot.gamedata.topology import CELL_COUNT, cell_to_grid
from combatbot.models import Cell
from combatbot.vision.combat_models import GRID_SOURCE_GAMEDATA, CombatGridObservation, ObservedCell
from combatbot.vision.entity_geometry import cell_basis

RED = (0, 0, 235)      # BGR
BLUE = (235, 40, 20)
GROUND = (120, 150, 125)


def synthetic_grid(cell_ids=None, *, cell_width: float = 64.0, cell_height: float = 32.0,
                   non_traversable=()) -> tuple[CombatGridObservation, tuple[int, int]]:
    """Grille projetée synthétique : écran = (x − y, x + y) × demi-cellule, topologie réelle."""
    ids = list(cell_ids) if cell_ids is not None else list(range(CELL_COUNT))
    coordinates = {cell_id: cell_to_grid(cell_id) for cell_id in ids}
    xs = [c.x - c.y for c in coordinates.values()]
    ys = [c.x + c.y for c in coordinates.values()]
    half_w, half_h = cell_width / 2, cell_height / 2
    origin = (-min(xs) * half_w + cell_width, -min(ys) * half_h + cell_height)
    cells = []
    for cell_id, coordinate in coordinates.items():
        cx = origin[0] + (coordinate.x - coordinate.y) * half_w
        cy = origin[1] + (coordinate.x + coordinate.y) * half_h
        polygon = ((round(cx), round(cy - half_h)), (round(cx + half_w), round(cy)),
                   (round(cx), round(cy + half_h)), (round(cx - half_w), round(cy)))
        cells.append(ObservedCell(Cell(coordinate.x, coordinate.y), (round(cx), round(cy)), polygon,
                                  cell_id=cell_id, grid_coordinate=GridCoordinate(coordinate.x, coordinate.y),
                                  walkable_static=cell_id not in non_traversable,
                                  non_walkable_during_fight_static=False))
    width = int((max(xs) - min(xs) + 2) * half_w + 2 * cell_width)
    height = int((max(ys) - min(ys) + 2) * half_h + 2 * cell_height)
    grid = CombatGridObservation(tuple(cells), cell_width, cell_height, 0.0, 1.0, GRID_SOURCE_GAMEDATA,
                                 grid_visibility={"state": "VISIBLE"}, alignment={"status": "ALIGNED"})
    return grid, (height, width)


def ground(shape: tuple[int, int], color=GROUND, *, noise: int = 3, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = np.empty((shape[0], shape[1], 3), np.uint8)
    image[:] = color
    if noise:
        image = np.clip(image.astype(int) + rng.integers(-noise, noise + 1, image.shape), 0, 255).astype(np.uint8)
    return image


def draw_ring(image: np.ndarray, cell, color, *, radius: float = 0.52, thickness: int = 3,
              lower_only: bool = False) -> None:
    """Anneau au sol dans la base du losange (rayon normalisé mesuré sur le client réel)."""
    middle, u, v = cell_basis(cell.polygon, cell.center)
    angles = np.linspace(0, np.pi if lower_only else 2 * np.pi, 90)
    points = np.stack([middle[0] + radius * (np.cos(angles) * u[0] + np.sin(angles) * v[0]),
                       middle[1] + radius * (np.cos(angles) * u[1] + np.sin(angles) * v[1])], axis=1)
    cv2.polylines(image, [np.round(points).astype(np.int32)], not lower_only, color, thickness, cv2.LINE_AA)


def draw_sprite(image: np.ndarray, cell, color=(40, 60, 90), *, seed: int = 1) -> None:
    """Sprite texturé au-dessus du centre : preuve secondaire seulement."""
    middle, u, v = cell_basis(cell.polygon, cell.center)
    center = (int(middle[0]), int(middle[1] - 0.45 * abs(v[1])))
    axes = (max(2, int(abs(u[0]) * 0.28)), max(3, int(abs(v[1]) * 0.9)))
    cv2.ellipse(image, center, axes, 0, 0, 360, color, -1, cv2.LINE_AA)
    rng = np.random.default_rng(seed)
    for _ in range(25):
        x = center[0] + int(rng.integers(-axes[0], axes[0] + 1))
        y = center[1] + int(rng.integers(-axes[1], axes[1] + 1))
        cv2.circle(image, (x, y), 1, tuple(int(c) for c in rng.integers(0, 255, 3)), -1)


def draw_entity(image: np.ndarray, grid, cell_id: int, ring_color, *, sprite: bool = True, **ring) -> None:
    cell = grid.cell_by_id(cell_id)
    if sprite:
        draw_sprite(image, cell, seed=cell_id)
    draw_ring(image, cell, ring_color, **ring)


def region(center_cell: int, radius: int = 3) -> list[int]:
    """Cellules à distance topologique ≤ radius d'une cellule (petite image de test)."""
    origin = cell_to_grid(center_cell)
    return [cell_id for cell_id in range(CELL_COUNT)
            if abs(cell_to_grid(cell_id).x - origin.x) + abs(cell_to_grid(cell_id).y - origin.y) <= radius]


def draw_ring_arc(image: np.ndarray, cell, color, start: float, end: float, *, radius: float = 0.52,
                  thickness: int = 2) -> None:
    """Arc d'anneau entre deux angles de la base du losange (0 = sommet droit, π/2 = sommet bas).

    Secteur k (entity_geometry) = angles [−π + kπ/4 ; −π + (k+1)π/4[ ; secteurs bas 4–7 = ]0 ; π[.
    """
    middle, u, v = cell_basis(cell.polygon, cell.center)
    angles = np.linspace(start, end, 120)
    points = np.stack([middle[0] + radius * (np.cos(angles) * u[0] + np.sin(angles) * v[0]),
                       middle[1] + radius * (np.cos(angles) * u[1] + np.sin(angles) * v[1])], axis=1)
    cv2.polylines(image, [np.round(points).astype(np.int32)], False, color, thickness, cv2.LINE_AA)
