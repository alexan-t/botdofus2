"""Géométrie logique des DofusCellId, pure et sans pixels.

Disposition historique documentée pour les maps 2.x : 560 cellules en
40 demi-rangées de 14, une demi-rangée sur deux décalée d'une demi-cellule.
Coordonnées logiques (x, y) : x - y = demi-rangée, x + y = 2 × colonne + parité.

Cette couche ne touche ni la vision, ni GridCalibration/GridTransform :
elle ne dit rien de la position des cellules à l'écran. Les preuves
réelles de cette formule sont dans GAME-DATA-REAL-VALIDATION.md.
La walkability ne modifie jamais la topologie.
"""
from __future__ import annotations

from .models import DofusCellId, GameMap, GridCoordinate, GridTopology

# Logical coordinates/neighbourhood demonstrated on the private 2.64.5 client
# (12 153 maps, criteria in validation.topology_decision). Screen projection
# is NOT covered: pixels remain the job of GridCalibration/GridTransform.
COORDINATES_VERIFIED = True

MAP_WIDTH = 14
MAP_HEIGHT = 20
CELL_COUNT = 2 * MAP_WIDTH * MAP_HEIGHT  # 560
ROWS = 2 * MAP_HEIGHT  # 40 half-rows

# Edge-sharing neighbours in logical coordinates (4-connexity).
EDGE_STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))
# Corner-sharing neighbours (same half-row ±1 column, or ±2 half-rows).
CORNER_STEPS = ((1, 1), (-1, -1), (1, -1), (-1, 1))


def _check(cell_id: int) -> int:
    if type(cell_id) is not int or not 0 <= cell_id < CELL_COUNT:
        raise ValueError(f"DofusCellId hors bornes : {cell_id!r}")
    return cell_id


def row_col(cell_id: int) -> tuple[int, int]:
    """Half-row (0..39) and column (0..13) in the stream layout."""
    return divmod(_check(cell_id), MAP_WIDTH)


def cell_to_grid(cell_id: int) -> GridCoordinate:
    row, col = row_col(cell_id)
    total = 2 * col + (row & 1)  # x + y
    return GridCoordinate((total + row) // 2, (total - row) // 2)


def grid_to_cell(coordinate: GridCoordinate) -> DofusCellId | None:
    """Inverse mapping; None for coordinates outside the 560-cell map."""
    row, total = coordinate.x - coordinate.y, coordinate.x + coordinate.y
    if not 0 <= row < ROWS or (total - row) & 1:
        return None
    col = (total - (row & 1)) // 2
    if not 0 <= col < MAP_WIDTH:
        return None
    return DofusCellId(row * MAP_WIDTH + col)


def neighbors(cell_id: int, *, corners: bool = False) -> tuple[DofusCellId, ...]:
    origin = cell_to_grid(cell_id)
    steps = EDGE_STEPS + (CORNER_STEPS if corners else ())
    found = (grid_to_cell(GridCoordinate(origin.x + dx, origin.y + dy)) for dx, dy in steps)
    return tuple(cell for cell in found if cell is not None)


def adjacency(*, corners: bool = False) -> dict[int, tuple[int, ...]]:
    return {cell: tuple(int(n) for n in neighbors(cell, corners=corners)) for cell in range(CELL_COUNT)}


def build_topology(game_map: GameMap, *, coordinates_verified: bool) -> GridTopology:
    """Geometry is independent of walkability; flags stay on the cells."""
    if len(game_map.cells) != CELL_COUNT:
        raise ValueError("Topologie définie uniquement pour 560 cellules")
    return GridTopology(game_map.map_id, game_map.cells, adjacency=adjacency(),
                        coordinates_verified=coordinates_verified,
                        coordinates={int(c.cell_id): cell_to_grid(int(c.cell_id)) for c in game_map.cells})
