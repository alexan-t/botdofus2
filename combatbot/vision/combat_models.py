"""Modèles immuables produits par l'observation visuelle d'un combat réel."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from combatbot.gamedata.models import GridCoordinate
from combatbot.models import Cell
from combatbot.vision.coordinates import CombatPoint

# Valeurs de CombatGridObservation.grid_source (voir grid_projection.GridSource).
GRID_SOURCE_GAMEDATA = "GAMEDATA_PROJECTED"
GRID_SOURCE_LEGACY = "LEGACY_CALIBRATION"
GRID_SOURCE_VISION = "VISION_DETECTED"


class CellVisualState(str, Enum):
    FREE = "FREE"
    OCCUPIED = "OCCUPIED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ObservedCell:
    """Cellule observée ; centre et polygone sont locaux au crop combat.

    Avec une grille GAMEDATA_PROJECTED, ``cell_id`` (DofusCellId) est l'identité
    canonique et ``logical`` n'est qu'un adaptateur explicite
    ``Cell(grid_coordinate.x, grid_coordinate.y)`` pour le code historique.
    ``state``/``confidence`` sont visuels et dynamiques ; les champs ``*_static``
    viennent de GameData et ne disent rien de l'occupation courante.
    """
    logical: Cell
    center: tuple[int, int]
    polygon: tuple[tuple[int, int], ...]
    state: CellVisualState = CellVisualState.UNKNOWN
    confidence: float = 0.0
    cell_id: int | None = None
    grid_coordinate: GridCoordinate | None = None
    walkable_static: bool | None = None
    non_walkable_during_fight_static: bool | None = None
    los_static: bool | None = None
    red_hint: bool | None = None
    blue_hint: bool | None = None

    @property
    def visual_state(self) -> CellVisualState:
        return self.state

    @property
    def visual_confidence(self) -> float:
        return self.confidence

    @property
    def static_traversable(self) -> bool | None:
        """Traversable en combat selon GameData ; jamais synonyme de FREE."""
        if self.walkable_static is None or self.non_walkable_during_fight_static is None:
            return None
        return self.walkable_static and not self.non_walkable_during_fight_static


@dataclass(frozen=True)
class CombatGridObservation:
    cells: tuple[ObservedCell, ...] = ()
    cell_width: float | None = None
    cell_height: float | None = None
    orientation_degrees: float | None = None
    confidence: float = 0.0
    grid_source: str = GRID_SOURCE_VISION
    map_id_declared: int | None = None
    projection_confidence: float | None = None
    grid_profile_version: int | None = None
    projection_status: str | None = None
    # Sérialisation de GridScreenTransform (GAMEDATA_PROJECTED uniquement).
    transform: dict | None = None
    # Support visuel moyen (traversables − non traversables) : cohérence écran / map déclarée.
    topology_consistency: float | None = None
    # user_verified_mapid | manual_guess ; None pour les observations antérieures au LOT 3B-2R.
    map_id_source: str | None = None

    @property
    def declared_map_suspect(self) -> bool:
        """Indice seulement : la map déclarée semble ne plus correspondre à l'écran."""
        from combatbot.vision.grid_fit import MIN_TOPOLOGY_CONSISTENCY
        return self.topology_consistency is not None and self.topology_consistency < MIN_TOPOLOGY_CONSISTENCY

    def cell_at(self, logical: Cell) -> ObservedCell | None:
        return next((item for item in self.cells if item.logical == logical), None)

    def cell_by_id(self, cell_id: int) -> ObservedCell | None:
        return next((item for item in self.cells if item.cell_id == cell_id), None)

    def pixel_to_cell_id(self, point: CombatPoint | tuple[float, float], *,
                         max_distance: float | None = None) -> int | None:
        """Cellule projetée la plus proche (GAMEDATA_PROJECTED), sans contour."""
        if self.grid_source != GRID_SOURCE_GAMEDATA or self.transform is None:
            return None
        from combatbot.vision.grid_projection import GridScreenTransform, lattice_pixel_to_cell_id
        valid = {item.cell_id for item in self.cells if item.cell_id is not None}
        return lattice_pixel_to_cell_id(GridScreenTransform.from_dict(self.transform), point, valid,
                                        max_distance=max_distance)

    def pixel_to_cell(self, point: CombatPoint | tuple[int, int]) -> Cell | None:
        """Convertit un pixel si celui-ci appartient au losange observé."""
        if self.grid_source == GRID_SOURCE_GAMEDATA and self.transform is not None:
            cell_id = self.pixel_to_cell_id(point)
            cell = self.cell_by_id(cell_id) if cell_id is not None else None
            return cell.logical if cell is not None else None
        px, py = point
        best: tuple[float, Cell] | None = None
        for item in self.cells:
            if not item.polygon:
                continue
            xs = [value[0] for value in item.polygon]
            ys = [value[1] for value in item.polygon]
            half_width = max(1.0, (max(xs) - min(xs)) / 2)
            half_height = max(1.0, (max(ys) - min(ys)) / 2)
            distance = abs(px - item.center[0]) / half_width + abs(py - item.center[1]) / half_height
            if distance <= 1.08 and (best is None or distance < best[0]):
                best = (distance, item.logical)
        return best[1] if best else None


@dataclass(frozen=True)
class EnemyObservation:
    """Ennemi dont le centre est exprimé dans le référentiel combat."""
    id: str
    cell: Cell
    center: tuple[int, int]
    confidence: float
    cell_id: int | None = None


@dataclass(frozen=True)
class CombatObservation:
    combat_detected: bool
    combat_confidence: float
    player_turn: bool | None
    turn_confidence: float
    player_cell: Cell | None
    player_confidence: float
    enemies: tuple[EnemyObservation, ...]
    grid: CombatGridObservation
    ap: int | None
    mp: int | None
    confidence_ap: float
    confidence_mp: float
    observation_confidence: float
    result: Literal["victory", "defeat", "unknown"] | None = None
    signals: dict[str, float] = field(default_factory=dict)
    timestamp: float = 0.0
    player_cell_id: int | None = None

    @property
    def enemy_cells(self) -> tuple[Cell, ...]:
        return tuple(enemy.cell for enemy in self.enemies)

    @property
    def safe_for_decision(self) -> bool:
        """Information préparatoire : le LOT 3 ne déclenche aucune décision."""
        return bool(
            self.combat_detected
            and self.player_turn is not None
            and self.player_cell is not None
            and bool(self.enemies)
            and self.ap is not None
            and self.mp is not None
            and self.grid.confidence >= 0.65
            and bool(self.grid.cells)
            and all(cell.state is not CellVisualState.UNKNOWN for cell in self.grid.cells)
            and self.observation_confidence >= 0.7
        )


@dataclass(frozen=True)
class GridCalibration:
    """Paramètres d'une grille ; ``origin`` est toujours locale au crop combat."""

    origin: CombatPoint | tuple[float, float]
    cell_width: float
    cell_height: float
    logical_cells: tuple[Cell, ...] = ()
    player_reference_hsv: tuple[float, float, float] | None = None
    coordinate_space: Literal["combat"] = "combat"

    def __post_init__(self) -> None:
        if not isinstance(self.origin, CombatPoint):
            object.__setattr__(self, "origin", CombatPoint(float(self.origin[0]), float(self.origin[1])))
        if self.coordinate_space != "combat":
            raise ValueError("GridCalibration doit utiliser le référentiel combat")

    def to_dict(self) -> dict[str, object]:
        assert isinstance(self.origin, CombatPoint)
        return {
            "schema_version": 1,
            "origin": self.origin.to_dict(),
            "coordinate_space": self.coordinate_space,
            "cell_width": self.cell_width,
            "cell_height": self.cell_height,
            "logical_cells": [[cell.x, cell.y] for cell in self.logical_cells],
            "player_reference_hsv": list(self.player_reference_hsv or ()),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "GridCalibration":
        if int(raw.get("schema_version", 1)) != 1:
            raise ValueError("Version de GridCalibration non prise en charge")
        origin_raw = raw.get("origin")
        if isinstance(origin_raw, dict):
            origin = CombatPoint.from_dict(origin_raw)
        elif isinstance(origin_raw, (list, tuple)) and len(origin_raw) == 2:
            origin = CombatPoint(float(origin_raw[0]), float(origin_raw[1]))
        else:
            raise ValueError("Origine de grille invalide")
        logical_raw = raw.get("logical_cells", ())
        if not isinstance(logical_raw, (list, tuple)):
            raise ValueError("Cellules logiques invalides")
        logical = tuple(
            Cell(int(item["x"]), int(item["y"])) if isinstance(item, dict)
            else Cell(int(item[0]), int(item[1]))
            for item in logical_raw
        )
        reference_raw = raw.get("player_reference_hsv")
        reference = (tuple(float(value) for value in reference_raw)
                     if isinstance(reference_raw, (list, tuple)) and reference_raw else None)
        if reference is not None and len(reference) != 3:
            raise ValueError("Référence HSV invalide")
        return cls(origin, float(raw["cell_width"]), float(raw["cell_height"]), logical,
                   reference, str(raw.get("coordinate_space", "combat")))  # type: ignore[arg-type]


@dataclass(frozen=True)
class ObservationPacket:
    observation: CombatObservation
    original: object  # ndarray BGR, gardé hors du modèle sérialisé
    annotated: object
    elapsed_ms: float
    metadata: dict[str, object] = field(default_factory=dict)
    hud_crops: dict[str, object] = field(default_factory=dict)
