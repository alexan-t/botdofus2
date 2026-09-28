"""FAST-4B : pathfinding GameData fail-closed sur la topologie réelle 560 cellules."""
from __future__ import annotations

import time

import pytest

from combatbot.combat.pathfinding import (
    CellBlock, CombatMap, PathStatus, reachable, shortest_path, static_block,
)
from combatbot.gamedata.models import DofusCellId, GameMap, GameMapCell, GridCoordinate
from combatbot.gamedata.topology import CELL_COUNT, build_topology, cell_to_grid, grid_to_cell, neighbors


def make_map(*, blocked=(), fight_blocked=(), unknown=(), fight_unknown=()) -> CombatMap:
    cells = []
    for cell_id in range(CELL_COUNT):
        walkable = None if cell_id in unknown else cell_id not in blocked
        fight = None if cell_id in fight_unknown else cell_id in fight_blocked
        cells.append(GameMapCell(DofusCellId(cell_id), walkable=walkable, non_walkable_during_fight=fight))
    return CombatMap.from_game_map(GameMap(123, tuple(cells), "test", 11))


def step(cell: int, dx: int, dy: int) -> int:
    coordinate = cell_to_grid(cell)
    found = grid_to_cell(GridCoordinate(coordinate.x + dx, coordinate.y + dy))
    assert found is not None
    return int(found)


CENTER = 300


def test_real_topology_degrees_and_symmetry() -> None:
    combat_map = make_map()
    degrees = sorted(len(links) for links in combat_map.neighbors.values())
    assert (degrees.count(4), degrees.count(2), degrees.count(1)) == (494, 64, 2)   # GAME-DATA-REAL-VALIDATION §14
    assert all(cell in combat_map.neighbors[other] for cell, links in combat_map.neighbors.items() for other in links)
    game_map = GameMap(7, tuple(GameMapCell(DofusCellId(i), walkable=True, non_walkable_during_fight=False)
                                for i in range(CELL_COUNT)), "test", 11)
    assert CombatMap.from_topology(build_topology(game_map, coordinates_verified=True)).neighbors[CENTER] \
        == tuple(sorted(int(n) for n in neighbors(CENTER)))
    with pytest.raises(ValueError):
        CombatMap.from_topology(build_topology(game_map, coordinates_verified=False))
    with pytest.raises(ValueError):
        CombatMap.from_cells(1, game_map.cells[:-1])


def test_simple_path_costs_one_mp_per_edge() -> None:
    target = step(step(step(CENTER, 1, 0), 1, 0), 1, 0)
    result = shortest_path(make_map(), CENTER, target, max_mp=3)
    assert result.status is PathStatus.FOUND and result.cost == 3 and len(result.path) == 3
    assert result.path[-1] == target
    cells = (CENTER, *result.path)
    assert all(b in neighbors(a) for a, b in zip(cells, cells[1:]))


def test_static_blocking_and_unknown_walkability_are_closed() -> None:
    east = step(CENTER, 1, 0)
    for combat_map, reason in ((make_map(blocked={east}), CellBlock.STATIC_NOT_WALKABLE),
                               (make_map(fight_blocked={east}), CellBlock.STATIC_NOT_WALKABLE_IN_FIGHT),
                               (make_map(unknown={east}), CellBlock.STATIC_UNKNOWN),
                               (make_map(fight_unknown={east}), CellBlock.STATIC_UNKNOWN)):
        assert combat_map.block(east) is reason
        result = shortest_path(combat_map, CENTER, east)
        assert result.status is PathStatus.TARGET_BLOCKED and result.reason == reason.value
        assert east not in reachable(combat_map, CENTER, 3)
    assert static_block(GameMapCell(DofusCellId(0))) is CellBlock.STATIC_UNKNOWN


def test_dynamic_occupancy_and_unknown_occupancy_block() -> None:
    combat_map = make_map()
    east = step(CENTER, 1, 0)
    far = step(east, 1, 0)
    straight = shortest_path(combat_map, CENTER, far)
    assert straight.cost == 2
    around = shortest_path(combat_map, CENTER, far, occupied={east})
    assert around.found and east not in around.path and around.cost == 4
    assert shortest_path(combat_map, CENTER, far, unknown={east}).cost == 4
    assert shortest_path(combat_map, CENTER, east, occupied={east}).reason == CellBlock.OCCUPIED.value
    assert shortest_path(combat_map, CENTER, east, unknown={east}).reason == CellBlock.OCCUPANCY_UNKNOWN.value


def test_no_path_when_enclosed() -> None:
    walls = set(neighbors(CENTER))
    result = shortest_path(make_map(blocked=walls), CENTER, 10)
    assert result.status is PathStatus.NO_PATH and not result.found
    assert reachable(make_map(blocked=walls), CENTER, 6) == {CENTER: 0}
    everything_unknown = make_map(unknown=set(range(CELL_COUNT)) - {CENTER})
    assert shortest_path(everything_unknown, CENTER, 10).status is PathStatus.TARGET_BLOCKED


def test_several_equal_paths_are_deterministic() -> None:
    combat_map = make_map()
    target = step(step(CENTER, 1, 0), 0, 1)       # deux chemins de 2 PM
    results = {shortest_path(combat_map, CENTER, target).path for _ in range(20)}
    assert len(results) == 1
    shuffled = CombatMap(combat_map.map_id, {cell: tuple(reversed(links)) for cell, links in combat_map.neighbors.items()},
                         combat_map.static)
    # Le constructeur trie les voisins ; même une table inversée injectée à la main reste stable à l'appel suivant.
    assert shortest_path(shuffled, CENTER, target).cost == 2


def test_mp_budget() -> None:
    combat_map = make_map()
    target = step(step(step(step(CENTER, 1, 0), 1, 0), 1, 0), 1, 0)
    result = shortest_path(combat_map, CENTER, target, max_mp=3)
    assert result.status is PathStatus.OUT_OF_MP and result.cost == 4
    cells = reachable(combat_map, CENTER, 2)
    assert max(cells.values()) == 2 and len(cells) == 13   # losange de rayon 2 en 4-connexité
    assert reachable(combat_map, CENTER, 0) == {CENTER: 0}
    with pytest.raises(ValueError):
        reachable(combat_map, CENTER, -1)


def test_start_equals_target_and_start_may_be_occupied_by_the_mover() -> None:
    combat_map = make_map()
    assert shortest_path(combat_map, CENTER, CENTER).status is PathStatus.ALREADY_THERE
    assert shortest_path(combat_map, CENTER, step(CENTER, 1, 0), occupied={CENTER}).found


def test_map_edges_and_corners() -> None:
    combat_map = make_map()
    assert combat_map.neighbors[0] == tuple(sorted(int(n) for n in neighbors(0))) and len(combat_map.neighbors[0]) == 1
    result = shortest_path(combat_map, 0, 559)
    assert result.found and result.cost == sum(abs(a - b) for a, b in zip(
        (cell_to_grid(0).x, cell_to_grid(0).y), (cell_to_grid(559).x, cell_to_grid(559).y)))


def test_invalid_cell_ids() -> None:
    combat_map = make_map()
    for bad in (-1, 560, 3.0, True, None):
        assert shortest_path(combat_map, bad, 10).status is PathStatus.INVALID
        assert shortest_path(combat_map, 10, bad).status is PathStatus.INVALID
        assert combat_map.block(bad) is CellBlock.INVALID_CELL
    with pytest.raises(ValueError):
        reachable(combat_map, 999, 3)
    assert shortest_path(combat_map, 10, 20, max_mp=-2).status is PathStatus.INVALID


def test_stop_cells_are_reachable_but_never_crossed() -> None:
    combat_map = make_map()
    east = step(CENTER, 1, 0)
    far = step(east, 1, 0)
    assert shortest_path(combat_map, CENTER, east, stop_cells={east}).found
    detour = shortest_path(combat_map, CENTER, far, stop_cells={east})
    assert east not in detour.path and detour.cost == 4
    # Départ dans une stop_cell : on peut en partir (c'est à l'appelant d'interdire ce cas).
    assert shortest_path(combat_map, CENTER, east, stop_cells={CENTER}).found


def test_performance_full_map() -> None:
    combat_map = make_map(blocked={cell for cell in range(CELL_COUNT) if cell % 7 == 3})
    started = time.perf_counter()
    for start in range(0, CELL_COUNT, 11):
        shortest_path(combat_map, start, CELL_COUNT - 1 - start)
        reachable(combat_map, start, 6)
    elapsed = (time.perf_counter() - started) / (2 * len(range(0, CELL_COUNT, 11)))
    assert elapsed < 0.05   # très large : quelques centaines de µs attendues
