"""Projection écran d'une topologie GameData, sans OpenCV.

Deux domaines restent séparés :

* topologie GameData : ``DofusCellId`` → ``GridCoordinate`` (combatbot.gamedata.topology),
  voisinage, walkability et LOS **statiques** ;
* géométrie visuelle : ``CombatPoint`` → ``ClientPoint`` → ``ScreenPoint`` (LayoutTransform).

``GridScreenTransform`` est l'unique pont : ``p = origin + x · basis_x + y · basis_y``,
en pixels du crop combat. Une ``GridCoordinate`` n'est jamais un pixel.

Orientation normale (convention d'affichage DOFUS, cellule 0 en haut à gauche) :
``basis_x`` pointe vers le bas-droite et ``basis_y`` vers le haut-droite. Pour une
cellule de largeur W et de hauteur H : ``basis_x = (W/2, H/2)``, ``basis_y = (W/2, -H/2)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Mapping

from combatbot.gamedata.models import DofusCellId, GameMapCell, GridCoordinate, GridTopology
from combatbot.gamedata.topology import CELL_COUNT, cell_to_grid, grid_to_cell
from combatbot.vision.coordinates import ClientPoint, CombatPoint, LayoutTransform, ScreenPoint

TRANSFORM_SCHEMA_VERSION = 1
# A pixel belongs to a cell when its normalised diamond distance is <= 1 (+ tolerance).
DEFAULT_DIAMOND_TOLERANCE = 0.05


class GridSource(str, Enum):
    GAMEDATA_PROJECTED = "GAMEDATA_PROJECTED"
    LEGACY_CALIBRATION = "LEGACY_CALIBRATION"
    VISION_DETECTED = "VISION_DETECTED"


class GridOrientation(str, Enum):
    NORMAL = "NORMAL"            # basis_x down-right, basis_y up-right
    SWAPPED = "SWAPPED"          # roles of x and y exchanged (vertical mirror of IDs)
    MIRRORED = "MIRRORED"        # horizontal mirror
    ROTATED_180 = "ROTATED_180"  # both signs flipped
    OTHER = "OTHER"


@dataclass(frozen=True)
class Vector2:
    x: float
    y: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("Vecteur non fini")

    def __iter__(self):
        yield self.x
        yield self.y

    @property
    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def to_list(self) -> list[float]:
        return [float(self.x), float(self.y)]


@dataclass(frozen=True)
class GridScreenTransform:
    """Affine GridCoordinate → CombatPoint ; immuable et sérialisable."""

    origin: CombatPoint
    basis_x: Vector2
    basis_y: Vector2
    # Size of the combat crop this transform was measured on (pixels).
    reference_combat_size: tuple[float, float] | None = None

    def __post_init__(self) -> None:
        if abs(self.determinant) < 1e-6:
            raise ValueError("Transformation de grille dégénérée (bases colinéaires)")

    @classmethod
    def from_cell_size(cls, origin: CombatPoint | tuple[float, float], width: float, height: float,
                       *, shear: float = 0.0, reference_combat_size: tuple[float, float] | None = None
                       ) -> "GridScreenTransform":
        """Normal orientation from a cell footprint; ``shear`` tilts rows (pixels per half-width)."""
        if width <= 0 or height <= 0:
            raise ValueError("Largeur et hauteur de cellule strictement positives requises")
        point = origin if isinstance(origin, CombatPoint) else CombatPoint(*origin)
        return cls(point, Vector2(width / 2, height / 2 + shear), Vector2(width / 2, -height / 2 + shear),
                   reference_combat_size)

    @property
    def determinant(self) -> float:
        return self.basis_x.x * self.basis_y.y - self.basis_x.y * self.basis_y.x

    @property
    def cell_width(self) -> float:
        """Horizontal diamond extent: |basis_x + basis_y| along x."""
        return abs(self.basis_x.x + self.basis_y.x)

    @property
    def cell_height(self) -> float:
        return abs(self.basis_x.y - self.basis_y.y)

    @property
    def orientation(self) -> GridOrientation:
        bx, by = self.basis_x, self.basis_y
        signs = (bx.x > 0, bx.y > 0, by.x > 0, by.y > 0)
        return {
            (True, True, True, False): GridOrientation.NORMAL,
            (True, False, True, True): GridOrientation.SWAPPED,
            (False, True, False, False): GridOrientation.MIRRORED,
            (False, False, False, True): GridOrientation.ROTATED_180,
        }.get(signs, GridOrientation.OTHER)

    def grid_to_combat(self, coordinate: GridCoordinate) -> CombatPoint:
        return CombatPoint(self.origin.x + coordinate.x * self.basis_x.x + coordinate.y * self.basis_y.x,
                           self.origin.y + coordinate.x * self.basis_x.y + coordinate.y * self.basis_y.y)

    def combat_to_fractional_grid(self, point: CombatPoint | tuple[float, float]) -> tuple[float, float]:
        """Inverse affine; the result is continuous, never an identity by itself."""
        px, py = point
        dx, dy = px - self.origin.x, py - self.origin.y
        det = self.determinant
        return ((dx * self.basis_y.y - dy * self.basis_y.x) / det,
                (self.basis_x.x * dy - self.basis_x.y * dx) / det)

    def scaled(self, sx: float, sy: float) -> "GridScreenTransform":
        size = self.reference_combat_size
        return GridScreenTransform(
            CombatPoint(self.origin.x * sx, self.origin.y * sy),
            Vector2(self.basis_x.x * sx, self.basis_x.y * sy), Vector2(self.basis_y.x * sx, self.basis_y.y * sy),
            (size[0] * sx, size[1] * sy) if size else None,
        )

    def translated(self, dx: float, dy: float) -> "GridScreenTransform":
        return GridScreenTransform(CombatPoint(self.origin.x + dx, self.origin.y + dy),
                                   self.basis_x, self.basis_y, self.reference_combat_size)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": TRANSFORM_SCHEMA_VERSION, "model": "affine",
            "origin": self.origin.to_dict(), "basis_x": self.basis_x.to_list(), "basis_y": self.basis_y.to_list(),
            "reference_combat_size": list(self.reference_combat_size) if self.reference_combat_size else None,
            "orientation": self.orientation.value,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "GridScreenTransform":
        if int(raw.get("schema_version", -1)) != TRANSFORM_SCHEMA_VERSION or raw.get("model") != "affine":
            raise ValueError("GridScreenTransform : version ou modèle non pris en charge")
        origin, bx, by = raw.get("origin"), raw.get("basis_x"), raw.get("basis_y")
        if not isinstance(origin, Mapping) or not isinstance(bx, (list, tuple)) or not isinstance(by, (list, tuple)):
            raise ValueError("GridScreenTransform sérialisé invalide")
        size = raw.get("reference_combat_size")
        return cls(CombatPoint.from_dict(origin), Vector2(float(bx[0]), float(bx[1])),
                   Vector2(float(by[0]), float(by[1])),
                   (float(size[0]), float(size[1])) if isinstance(size, (list, tuple)) and len(size) == 2 else None)


# --- Composition DofusCellId → CombatPoint → ClientPoint → ScreenPoint -------------

def cell_to_combat(cell_id: int, transform: GridScreenTransform) -> CombatPoint:
    return transform.grid_to_combat(cell_to_grid(int(cell_id)))


def cell_to_client(cell_id: int, transform: GridScreenTransform, layout: LayoutTransform) -> ClientPoint:
    return layout.combat_to_client(cell_to_combat(cell_id, transform))


def cell_to_screen(cell_id: int, transform: GridScreenTransform, layout: LayoutTransform) -> ScreenPoint:
    return layout.client_to_screen(cell_to_client(cell_id, transform, layout))


def cell_polygon(center: CombatPoint, transform: GridScreenTransform) -> tuple[CombatPoint, ...]:
    """Theoretical diamond from the projection vectors (top, right, bottom, left in normal orientation).

    Vertices sit halfway between the centre and its edge neighbours' shared corners:
    centre ± (basis_x + basis_y) / 2 and centre ± (basis_x - basis_y) / 2.
    """
    bx, by = transform.basis_x, transform.basis_y
    horizontal = ((bx.x + by.x) / 2, (bx.y + by.y) / 2)
    vertical = ((bx.x - by.x) / 2, (bx.y - by.y) / 2)
    return (CombatPoint(center.x - vertical[0], center.y - vertical[1]),
            CombatPoint(center.x + horizontal[0], center.y + horizontal[1]),
            CombatPoint(center.x + vertical[0], center.y + vertical[1]),
            CombatPoint(center.x - horizontal[0], center.y - horizontal[1]))


# --- Projected grid -----------------------------------------------------------------

@dataclass(frozen=True)
class ProjectedCell:
    cell_id: DofusCellId
    grid_coordinate: GridCoordinate
    center: CombatPoint
    polygon: tuple[CombatPoint, ...]
    # Static GameData facts only; dynamic occupancy lives in the observation.
    walkable_static: bool | None = None
    non_walkable_during_fight_static: bool | None = None
    los_static: bool | None = None
    red_hint: bool | None = None
    blue_hint: bool | None = None

    @property
    def static_traversable_in_fight(self) -> bool | None:
        """Statically traversable in combat; says nothing about current occupancy."""
        if self.walkable_static is None or self.non_walkable_during_fight_static is None:
            return None
        return self.walkable_static and not self.non_walkable_during_fight_static


@dataclass(frozen=True)
class ProjectedGrid:
    map_id: int | None
    transform: GridScreenTransform
    cells: tuple[ProjectedCell, ...]
    source: GridSource = GridSource.GAMEDATA_PROJECTED
    _by_id: dict[int, ProjectedCell] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {int(cell.cell_id): cell for cell in self.cells})

    @property
    def cell_ids(self) -> frozenset[int]:
        return frozenset(self._by_id)

    def cell(self, cell_id: int) -> ProjectedCell | None:
        return self._by_id.get(int(cell_id))

    def pixel_to_cell(self, point: CombatPoint | tuple[float, float], *,
                      max_distance: float | None = None,
                      tolerance: float = DEFAULT_DIAMOND_TOLERANCE) -> DofusCellId | None:
        """Nearest projected cell; None outside its theoretical diamond (no contour needed)."""
        return lattice_pixel_to_cell_id(self.transform, point, self._by_id.keys(),
                                        max_distance=max_distance, tolerance=tolerance)


def lattice_pixel_to_cell_id(transform: GridScreenTransform, point: CombatPoint | tuple[float, float],
                             valid_ids, *, max_distance: float | None = None,
                             tolerance: float = DEFAULT_DIAMOND_TOLERANCE) -> DofusCellId | None:
    """O(1) lookup using the lattice itself as spatial index.

    The affine inverse gives fractional (x, y); the containing diamond is one of the
    four surrounding lattice points. In basis coordinates each diamond is the square
    max(|u|, |v|) <= 1/2, so ``|u + v| + |u - v| <= 1`` means "inside".
    ``max_distance`` optionally bounds the Euclidean distance to the centre (pixels).
    """
    fx, fy = transform.combat_to_fractional_grid(point)
    best: tuple[float, int, GridCoordinate] | None = None
    for gx in (math.floor(fx), math.ceil(fx)):
        for gy in (math.floor(fy), math.ceil(fy)):
            coordinate = GridCoordinate(gx, gy)
            cell_id = grid_to_cell(coordinate)
            if cell_id is None or int(cell_id) not in valid_ids:
                continue
            u, v = fx - gx, fy - gy
            norm = abs(u + v) + abs(u - v)
            if best is None or norm < best[0]:
                best = (norm, int(cell_id), coordinate)
    if best is None or best[0] > 1.0 + tolerance:
        return None
    if max_distance is not None:
        center = transform.grid_to_combat(best[2])
        px, py = point
        if math.hypot(px - center.x, py - center.y) > max_distance:
            return None
    return DofusCellId(best[1])


def legacy_cell(coordinate: GridCoordinate):
    """Explicit adapter GridCoordinate → historical ``Cell`` (tracker/overlay/corpus).

    Manhattan distance on these coordinates equals the 4-neighbour step count,
    so the historical tracker radius keeps a geometric meaning. The canonical
    identity stays ``DofusCellId``.
    """
    from combatbot.models import Cell
    return Cell(coordinate.x, coordinate.y)


class GridProjector:
    """Pure: GridTopology + GridScreenTransform → ProjectedGrid (560 cells, always)."""

    def __init__(self, transform: GridScreenTransform) -> None:
        self.transform = transform
        # Geometry depends on the transform only; computed once and shared by all maps.
        self._geometry: dict[int, tuple[GridCoordinate, CombatPoint, tuple[CombatPoint, ...]]] = {}
        for cell_id in range(CELL_COUNT):
            coordinate = cell_to_grid(cell_id)
            center = transform.grid_to_combat(coordinate)
            self._geometry[cell_id] = (coordinate, center, cell_polygon(center, transform))

    def project(self, topology: GridTopology) -> ProjectedGrid:
        if len(topology.cells) != CELL_COUNT:
            raise ValueError("Topologie GameData de 560 cellules requise")
        cells = []
        for cell in topology.cells:
            coordinate, center, polygon = self._geometry[int(cell.cell_id)]
            cells.append(_projected(cell, coordinate, center, polygon))
        return ProjectedGrid(topology.map_id, self.transform, tuple(cells))


def _projected(cell: GameMapCell, coordinate: GridCoordinate, center: CombatPoint,
               polygon: tuple[CombatPoint, ...]) -> ProjectedCell:
    return ProjectedCell(cell.cell_id, coordinate, center, polygon, cell.walkable,
                         cell.non_walkable_during_fight, cell.line_of_sight, cell.red_hint, cell.blue_hint)
