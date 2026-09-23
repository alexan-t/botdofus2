"""Intégration GameData → vision : choix de la source de grille par observation.

Priorité :

1. ``GAMEDATA_PROJECTED`` : map ID déclaré, topologie disponible, profil de
   projection confirmé et layout compatible. Les 560 cellules existent parce que
   GameData les définit ; la vision n'estime que l'alignement et l'occupation.
2. ``LEGACY_CALIBRATION`` / ``VISION_DETECTED`` : pipeline historique, utilisé
   tel quel sans profil GameData, ou en secours seulement si explicitement autorisé.
3. ``NONE`` : profil GameData présent mais inapplicable et secours refusé ;
   la raison est exposée (par exemple GRID_CALIBRATION_INCOMPATIBLE).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np

from combatbot.gamedata.errors import GameDataError
from combatbot.gamedata.models import GridTopology
from combatbot.vision.combat_grid import infer_combat_grid
from combatbot.vision.combat_models import (
    GRID_SOURCE_GAMEDATA, CellVisualState,
    CombatGridObservation, GridCalibration, ObservedCell,
)
from combatbot.vision.coordinates import ClientSize
from combatbot.vision.grid_fit import projection_alignment, topology_consistency
from combatbot.vision.grid_profile import (
    PROFILE_SCHEMA_VERSION, CombatGridProfileV2, MapIdentityProvider, ProjectionStatus,
)
from combatbot.vision.grid_projection import GridProjector, GridScreenTransform, ProjectedGrid, legacy_cell

GRID_SOURCE_NONE = "NONE"
TOPOLOGY_CACHE_SIZE = 8


class GameDataTopologySource:
    """Charge des GridTopology par map ID, avec cache LRU ; aucun accès mémoire/réseau."""

    def __init__(self, provider) -> None:
        self.provider = provider
        self._cache: OrderedDict[int, GridTopology] = OrderedDict()
        self.last_load_seconds: float | None = None

    @classmethod
    def for_client(cls, client_folder: str | Path, cache_dir: Path | None) -> "GameDataTopologySource":
        """Scans only ``content/maps`` when present (1.9 s cold / 0.2 s cached on the real client)."""
        from combatbot.gamedata import LocalGameDataProvider
        root = Path(client_folder)
        maps = root / "content" / "maps"
        provider = LocalGameDataProvider(maps if maps.is_dir() else root, cache_dir=cache_dir)
        report = provider.scan_client()
        if report.status not in ("SCANNED", "EMPTY") or not provider.list_maps():
            raise GameDataError("NOT_CONFIGURED", f"Aucune map GameData disponible : {report.message}")
        return cls(provider)

    def topology(self, map_id: int) -> GridTopology:
        if map_id in self._cache:
            self._cache.move_to_end(map_id)
            self.last_load_seconds = 0.0
            return self._cache[map_id]
        started = time.perf_counter()
        topology = self.provider.get_map_topology(map_id)
        self.last_load_seconds = time.perf_counter() - started
        self._cache[map_id] = topology
        while len(self._cache) > TOPOLOGY_CACHE_SIZE:
            self._cache.popitem(last=False)
        return topology


@dataclass(frozen=True)
class GridResolution:
    grid: CombatGridObservation
    source: str
    reason: str
    status: ProjectionStatus | None = None
    projected: ProjectedGrid | None = None
    requires_recalibration: bool = False


def observed_cells(projected: ProjectedGrid, confidences: dict[int, float] | None = None
                   ) -> tuple[ObservedCell, ...]:
    confidences = confidences or {}
    return tuple(ObservedCell(
        legacy_cell(cell.grid_coordinate), cell.center.rounded(), tuple(p.rounded() for p in cell.polygon),
        CellVisualState.UNKNOWN, confidences.get(int(cell.cell_id), 0.0), int(cell.cell_id),
        cell.grid_coordinate, cell.walkable_static, cell.non_walkable_during_fight_static,
        cell.los_static, cell.red_hint, cell.blue_hint,
    ) for cell in projected.cells)


def projected_observation(projected: ProjectedGrid, image: np.ndarray | None, *,
                          status: ProjectionStatus | None = None,
                          profile_version: int | None = PROFILE_SCHEMA_VERSION) -> CombatGridObservation:
    """560 cells whatever the image shows; the image only drives confidences."""
    if image is not None and image.size:
        confidence, per_cell = projection_alignment(image, projected)
    else:
        confidence, per_cell = 0.0, {}
    transform = projected.transform
    return CombatGridObservation(
        observed_cells(projected, per_cell), transform.cell_width, transform.cell_height, 0.0, confidence,
        GRID_SOURCE_GAMEDATA, projected.map_id, confidence, profile_version,
        status.code.value if status else None, transform.to_dict(),
        topology_consistency(projected, per_cell) if per_cell else None,
    )


class GameDataGridResolver:
    def __init__(self, *, profile: CombatGridProfileV2 | None, topology_source: GameDataTopologySource | None,
                 map_identity: MapIdentityProvider | None, legacy_calibration: GridCalibration | None = None,
                 allow_legacy_fallback: bool = False) -> None:
        self.profile = profile
        self.topology_source = topology_source
        self.map_identity = map_identity
        self.legacy_calibration = legacy_calibration
        self.allow_legacy_fallback = allow_legacy_fallback
        self._projector: tuple[GridScreenTransform, GridProjector] | None = None
        self._projected: dict[tuple[int, GridScreenTransform], ProjectedGrid] = {}

    def projector(self, transform: GridScreenTransform) -> GridProjector:
        if self._projector is None or self._projector[0] != transform:
            self._projector = (transform, GridProjector(transform))
            self._projected.clear()
        return self._projector[1]

    def _legacy(self, image: np.ndarray, reason: str, *, forced: bool = False,
                status: ProjectionStatus | None = None) -> GridResolution:
        if self.profile is not None and not (self.allow_legacy_fallback or forced):
            return GridResolution(
                CombatGridObservation(grid_source=GRID_SOURCE_NONE,
                                      projection_status=status.code.value if status else reason),
                GRID_SOURCE_NONE, reason, status, None, bool(status and status.requires_recalibration))
        grid = infer_combat_grid(image, self.legacy_calibration)
        return GridResolution(grid, grid.grid_source, reason, status, None,
                              bool(status and status.requires_recalibration))

    def resolve(self, image: np.ndarray, client_size: ClientSize, zones) -> GridResolution:
        if self.profile is None:
            return self._legacy(image, "NO_GAMEDATA_PROFILE", forced=True)
        declared = self.map_identity.current_map() if self.map_identity is not None else None
        if declared is None:
            return self._legacy(image, "NO_MAP_ID_DECLARED")
        if self.topology_source is None:
            return self._legacy(image, "NO_TOPOLOGY_SOURCE")
        status = self.profile.status_for(client_size, zones)
        if not status.applicable:
            return self._legacy(image, status.code.value, status=status)
        try:
            topology = self.topology_source.topology(declared.map_id)
        except (GameDataError, OSError, ValueError) as exc:
            return self._legacy(image, f"MAP_UNAVAILABLE: {exc}", status=status)
        key = (declared.map_id, status.transform)
        projected = self._projected.get(key)
        if projected is None:
            projected = self.projector(status.transform).project(topology)
            self._projected[key] = projected
            while len(self._projected) > TOPOLOGY_CACHE_SIZE:
                self._projected.pop(next(iter(self._projected)))
        grid = projected_observation(projected, image, status=status)
        return GridResolution(grid, GRID_SOURCE_GAMEDATA, status.code.value, status, projected, False)
