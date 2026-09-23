"""Modèles immuables produits par l'observation visuelle d'un combat réel."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from combatbot.models import Cell
from combatbot.vision.coordinates import CombatPoint


class CellVisualState(str, Enum):
    FREE = "FREE"
    OCCUPIED = "OCCUPIED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ObservedCell:
    """Cellule observée ; centre et polygone sont locaux au crop combat."""
    logical: Cell
    center: tuple[int, int]
    polygon: tuple[tuple[int, int], ...]
    state: CellVisualState = CellVisualState.UNKNOWN
    confidence: float = 0.0


@dataclass(frozen=True)
class CombatGridObservation:
    cells: tuple[ObservedCell, ...] = ()
    cell_width: float | None = None
    cell_height: float | None = None
    orientation_degrees: float | None = None
    confidence: float = 0.0

    def cell_at(self, logical: Cell) -> ObservedCell | None:
        return next((item for item in self.cells if item.logical == logical), None)

    def pixel_to_cell(self, point: CombatPoint | tuple[int, int]) -> Cell | None:
        """Convertit un pixel si celui-ci appartient au losange observé."""
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
