"""FAST-4B : déplacement en combat sur la vraie topologie GameData (DofusCellId), fail-closed.

Démontré dans le dépôt (GAME-DATA-REAL-VALIDATION.md §12-16) et donc utilisé :

- voisinage logique à 4 (arêtes partagées) de ``GridTopology.adjacency`` ;
- drapeaux statiques ``walkable`` (bit 0) et ``non_walkable_during_fight`` (bit 1).

Politique (rien n'est deviné) :

- ``walkable is False`` ou ``non_walkable_during_fight is True`` → bloqué ;
- ``walkable is None`` ou ``non_walkable_during_fight is None`` → bloqué (inconnu) ;
- cellule occupée → bloquée ; occupation UNKNOWN → bloquée ;
- départ : la cellule du personnage (il s'y trouve) n'a pas à être libre, mais doit exister ;
- coût : 1 PM par pas d'arête. ``movement_cost`` n'est jamais rempli par le lecteur DLM (toujours
  ``None``) et sa sémantique n'est pas prouvée : il est ignoré ;
- règles de jeu non démontrées (tacle, pièges, glyphes…) : non modélisées. L'appelant peut passer des
  ``stop_cells`` (atteignables mais jamais traversées) pour rester prudent.

Déterminisme : parcours en largeur, voisins visités par DofusCellId croissant ; à coût égal le chemin
retenu est donc toujours le même.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping

from combatbot.gamedata.models import GameMap, GameMapCell, GridTopology
from combatbot.gamedata.topology import CELL_COUNT, adjacency as topology_adjacency

MOVEMENT_COST_POLICY = "ignored: never populated by the DLM reader, semantics not proven"


class CellBlock(str, Enum):
    TRAVERSABLE = "TRAVERSABLE"
    INVALID_CELL = "INVALID_CELL"
    STATIC_NOT_WALKABLE = "STATIC_NOT_WALKABLE"
    STATIC_NOT_WALKABLE_IN_FIGHT = "STATIC_NOT_WALKABLE_IN_FIGHT"
    STATIC_UNKNOWN = "STATIC_UNKNOWN"
    OCCUPIED = "OCCUPIED"
    OCCUPANCY_UNKNOWN = "OCCUPANCY_UNKNOWN"


class PathStatus(str, Enum):
    FOUND = "FOUND"
    ALREADY_THERE = "ALREADY_THERE"
    NO_PATH = "NO_PATH"
    OUT_OF_MP = "OUT_OF_MP"                  # un chemin existe mais coûte plus que les PM disponibles
    TARGET_BLOCKED = "TARGET_BLOCKED"
    INVALID = "INVALID"


def _valid(cell_id: object) -> bool:
    return type(cell_id) is int and 0 <= cell_id < CELL_COUNT


@dataclass(frozen=True)
class CombatMap:
    """Vue statique d'une map pour le combat : voisinage + traversabilité GameData."""
    map_id: int
    neighbors: Mapping[int, tuple[int, ...]]
    static: Mapping[int, CellBlock]
    # Drapeau LOS statique (bit 3) : ``False`` = bloque la vue, ``None`` = inconnu. Aucun algorithme de
    # ligne de vue n'est fourni ici (non démontré) : voir combat/targeting.py.
    line_of_sight: Mapping[int, bool | None] = field(default_factory=dict)

    @classmethod
    def from_cells(cls, map_id: int, cells: Iterable[GameMapCell],
                   adjacency: Mapping[int, tuple[int, ...]] | None = None) -> "CombatMap":
        cells = tuple(cells)
        if len(cells) != CELL_COUNT or sorted(int(cell.cell_id) for cell in cells) != list(range(CELL_COUNT)):
            raise ValueError("Une map de combat exige exactement les 560 DofusCellId")
        static = {int(cell.cell_id): static_block(cell) for cell in cells}
        neighbors = {cell: tuple(sorted(links)) for cell, links in (adjacency or topology_adjacency()).items()}
        return cls(map_id, neighbors, static, {int(cell.cell_id): cell.line_of_sight for cell in cells})

    @classmethod
    def from_topology(cls, topology: GridTopology) -> "CombatMap":
        if topology.adjacency is None or not topology.coordinates_verified:
            raise ValueError("Topologie sans voisinage vérifié : aucun déplacement n'est calculé")
        return cls.from_cells(topology.map_id, topology.cells, topology.adjacency)

    @classmethod
    def from_game_map(cls, game_map: GameMap) -> "CombatMap":
        return cls.from_cells(game_map.map_id, game_map.cells)

    def block(self, cell_id: int, occupied: frozenset[int] = frozenset(),
              unknown: frozenset[int] = frozenset()) -> CellBlock:
        """Pourquoi une cellule n'est pas traversable (ou TRAVERSABLE). L'occupation prime l'inconnu."""
        if not _valid(cell_id):
            return CellBlock.INVALID_CELL
        static = self.static[cell_id]
        if static is not CellBlock.TRAVERSABLE:
            return static
        if cell_id in occupied:
            return CellBlock.OCCUPIED
        if cell_id in unknown:
            return CellBlock.OCCUPANCY_UNKNOWN
        return CellBlock.TRAVERSABLE


def static_block(cell: GameMapCell) -> CellBlock:
    if cell.walkable is False:
        return CellBlock.STATIC_NOT_WALKABLE
    if cell.non_walkable_during_fight is True:
        return CellBlock.STATIC_NOT_WALKABLE_IN_FIGHT
    if cell.walkable is None or cell.non_walkable_during_fight is None:
        return CellBlock.STATIC_UNKNOWN
    return CellBlock.TRAVERSABLE


@dataclass(frozen=True)
class PathResult:
    status: PathStatus
    start: int
    target: int
    path: tuple[int, ...] = ()      # cellules traversées après le départ, arrivée comprise
    cost: int | None = None         # PM
    reason: str = ""

    @property
    def found(self) -> bool:
        return self.status in (PathStatus.FOUND, PathStatus.ALREADY_THERE)


def _search(combat_map: CombatMap, start: int, occupied: frozenset[int], unknown: frozenset[int],
            stop_cells: frozenset[int], max_steps: int | None) -> tuple[dict[int, int], dict[int, int]]:
    """Largeur d'abord ; retourne coûts et parents. Les stop_cells sont atteintes, jamais traversées."""
    costs, parents = {start: 0}, {}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if max_steps is not None and costs[current] >= max_steps:
            continue
        if current != start and current in stop_cells:
            continue
        for neighbor in combat_map.neighbors.get(current, ()):
            if neighbor in costs:
                continue
            if combat_map.block(neighbor, occupied, unknown) is not CellBlock.TRAVERSABLE:
                continue
            costs[neighbor] = costs[current] + 1
            parents[neighbor] = current
            queue.append(neighbor)
    return costs, parents


def reachable(combat_map: CombatMap, start: int, max_mp: int, *, occupied: Iterable[int] = (),
              unknown: Iterable[int] = (), stop_cells: Iterable[int] = ()) -> dict[int, int]:
    """Cellules atteignables avec au plus ``max_mp`` PM → coût ; le départ vaut 0."""
    if not _valid(start):
        raise ValueError(f"Cellule de départ invalide : {start!r}")
    if type(max_mp) is not int or max_mp < 0:
        raise ValueError(f"PM invalides : {max_mp!r}")
    costs, _ = _search(combat_map, start, frozenset(occupied), frozenset(unknown), frozenset(stop_cells), max_mp)
    return dict(sorted(costs.items()))


def shortest_path(combat_map: CombatMap, start: int, target: int, *, occupied: Iterable[int] = (),
                  unknown: Iterable[int] = (), stop_cells: Iterable[int] = (),
                  max_mp: int | None = None) -> PathResult:
    if not _valid(start) or not _valid(target):
        return PathResult(PathStatus.INVALID, start, target, reason="DofusCellId hors 0..559")
    if max_mp is not None and (type(max_mp) is not int or max_mp < 0):
        return PathResult(PathStatus.INVALID, start, target, reason=f"PM invalides : {max_mp!r}")
    if start == target:
        return PathResult(PathStatus.ALREADY_THERE, start, target, (), 0, "déjà sur la cellule")
    occupied, unknown = frozenset(occupied), frozenset(unknown)
    block = combat_map.block(target, occupied, unknown)
    if block is not CellBlock.TRAVERSABLE:
        return PathResult(PathStatus.TARGET_BLOCKED, start, target, reason=block.value)
    costs, parents = _search(combat_map, start, occupied, unknown, frozenset(stop_cells), None)
    if target not in costs:
        return PathResult(PathStatus.NO_PATH, start, target, reason="aucun chemin traversable")
    path = [target]
    while path[-1] in parents and parents[path[-1]] != start:
        path.append(parents[path[-1]])
    path.reverse()
    cost = costs[target]
    if max_mp is not None and cost > max_mp:
        return PathResult(PathStatus.OUT_OF_MP, start, target, tuple(path), cost,
                          f"chemin de {cost} PM pour {max_mp} PM disponibles")
    return PathResult(PathStatus.FOUND, start, target, tuple(path), cost)
