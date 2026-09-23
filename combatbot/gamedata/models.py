"""Modèles métier purs. None signifie inconnu, jamais False par défaut."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import NewType

DofusCellId = NewType("DofusCellId", int)


@dataclass(frozen=True)
class GridCoordinate:
    x: int
    y: int


@dataclass(frozen=True)
class GameMapCell:
    cell_id: DofusCellId
    logical_position: GridCoordinate | None = None
    walkable: bool | None = None
    line_of_sight: bool | None = None
    movement_cost: float | None = None
    floor: int | None = None
    fight_start_allowed: bool | None = None
    non_walkable_during_fight: bool | None = None
    non_walkable_during_roleplay: bool | None = None
    raw_flags: int | None = None
    # Historique DLM seulement : aucune équivalence avec un placement actif.
    blue_hint: bool | None = None
    red_hint: bool | None = None
    # Octets bruts du schéma v11, exposés sans interprétation de jeu.
    speed: int | None = None
    map_change_data: int | None = None
    move_zone: int | None = None
    linked_zone: int | None = None


@dataclass(frozen=True)
class GameMap:
    map_id: int
    cells: tuple[GameMapCell, ...]
    source: str
    format_version: int
    width: int | None = None
    height: int | None = None
    compatibility_verified: bool = False
    warnings: tuple[str, ...] = ()
    # En-tête DLM lu (sous-zone, voisins, tacticalModeTemplateId…), sans sémantique ajoutée.
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> dict:
        return {
            "map_id": self.map_id, "cells": len(self.cells),
            "walkable": sum(c.walkable is True for c in self.cells),
            "non_walkable": sum(c.walkable is False for c in self.cells),
            "walkability_unknown": sum(c.walkable is None for c in self.cells),
            "los": sum(c.line_of_sight is True for c in self.cells),
            "los_unknown": sum(c.line_of_sight is None for c in self.cells),
            "fight_cells": None if any(c.fight_start_allowed is None for c in self.cells)
                           else [int(c.cell_id) for c in self.cells if c.fight_start_allowed],
            "blue_hints": sum(c.blue_hint is True for c in self.cells),
            "red_hints": sum(c.red_hint is True for c in self.cells),
            "non_walkable_during_fight": sum(c.non_walkable_during_fight is True for c in self.cells),
            "tactical_mode_template_id": self.metadata.get("tactical_mode_template_id"),
            "sub_area_id": self.metadata.get("sub_area_id"),
            "map_parsed": True, "topology_extracted": False,
            "compatibility_verified": self.compatibility_verified,
        }


@dataclass(frozen=True)
class GridTopology:
    map_id: int
    cells: tuple[GameMapCell, ...]
    # Logical neighbourhood only (topology.py); never a screen projection.
    adjacency: dict[int, tuple[int, ...]] | None = None
    coordinates_verified: bool = False
    coordinates: dict[int, GridCoordinate] | None = None


@dataclass(frozen=True)
class InventoryFile:
    relative_path: str
    extension: str
    size: int
    mtime_ns: int
    modified_utc: str
    header_hex: str
    classification: str
    sha256: str | None = None


@dataclass
class ScanReport:
    client_path: str | None = None
    status: str = "NOT_CONFIGURED"
    message: str = "Dossier du client DOFUS non configuré"
    detected_version: str | None = None
    version_evidence: list[str] = field(default_factory=list)
    files: list[InventoryFile] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    formats: dict[str, int] = field(default_factory=dict)
    format_details: dict[str, dict] = field(default_factory=dict)
    tested_maps: dict[int, dict] = field(default_factory=dict)
    indexed_maps: int = 0
    readable_maps: int | None = None
    cells_readable: bool | None = None
    fight_cells: bool | None = None
    map_parsed: bool = False
    topology_extracted: bool = False
    compatibility_verified: bool = False
    verdict: str = "NO"
    verdict_reason: str = "Aucune topologie exploitable démontrée."
    scan_seconds: float = 0
    index_seconds: float = 0
    index_cache_hit: bool = False
    last_map_seconds: float | None = None
    python_peak_bytes: int | None = None

    def to_dict(self) -> dict:
        return asdict(self)
