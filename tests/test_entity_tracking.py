"""LOT 3B-5 : suivi global des entités (affectation hongroise, occlusion, identités)."""

from __future__ import annotations

from collections import deque
import itertools
import random

from combatbot.gamedata.models import GridCoordinate
from combatbot.gamedata.topology import CELL_COUNT, grid_to_cell, neighbors
from combatbot.models import Cell
from combatbot.vision.combat_models import CombatGridObservation, CombatObservation, EnemyObservation
from combatbot.vision.entity_models import EntityEvidence, EntityKind, TrackState
from combatbot.vision.entity_tracker import (
    INFINITE, EntityTracker, TrackerConfig, greedy_assign, grid_distance, hungarian,
)


def line(x: int, y: int = 0) -> int:
    """Cellule sur une ligne logique (x croissant) : distance topologique = écart de x."""
    cell = grid_to_cell(GridCoordinate(10 + x, y))
    assert cell is not None
    return int(cell)


def enemy(cell_id: int, confidence: float = 0.9, hue: float = 118.0) -> EntityEvidence:
    return EntityEvidence(cell_id, EntityKind.ENEMY, confidence, marker_score=confidence, marker_hue=hue)


def player(cell_id: int, confidence: float = 0.9) -> EntityEvidence:
    return EntityEvidence(cell_id, EntityKind.PLAYER, confidence, marker_score=confidence, marker_hue=3.0)


def by_id(tracks):
    return {item.track_id: item for item in tracks}


def test_grid_distance_equals_shortest_path_on_topology() -> None:
    source = line(0)
    distances, queue = {source: 0}, deque([source])
    while queue:
        current = queue.popleft()
        for neighbour in neighbors(current):
            if neighbour not in distances:
                distances[neighbour] = distances[current] + 1
                queue.append(neighbour)
    assert len(distances) == CELL_COUNT
    assert all(grid_distance(source, cell) == distance for cell, distance in distances.items())


def test_hungarian_matches_brute_force() -> None:
    rng = random.Random(7)
    for _ in range(150):
        rows, columns = rng.randint(1, 4), rng.randint(1, 4)
        cost = [[rng.uniform(0, 9) for _ in range(columns)] for _ in range(rows)]
        best = min(sum(cost[r][c] for r, c in zip(rs, cs))
                   for k in [min(rows, columns)]
                   for rs in itertools.combinations(range(rows), k)
                   for cs in itertools.permutations(range(columns), k))
        pairs = hungarian(cost)
        assert len(pairs) == min(rows, columns)
        assert abs(sum(cost[r][c] for r, c in pairs) - best) < 1e-9


def test_hungarian_never_returns_forbidden_pairs() -> None:
    assert hungarian([[INFINITE, 1.0], [INFINITE, INFINITE]]) == [(0, 1)]


def test_track_id_stable_when_enemy_moves() -> None:
    tracker = EntityTracker()
    first = by_id(tracker.update([enemy(line(0))], 0.0))
    second = by_id(tracker.update([enemy(line(2))], 0.5))
    assert list(first) == list(second) == ["enemy_1"]
    assert second["enemy_1"].cell_id == line(2) and second["enemy_1"].state is TrackState.OBSERVED


def test_two_nearby_enemies_keep_ids() -> None:
    tracker = EntityTracker()
    tracker.update([enemy(line(0)), enemy(line(1))], 0.0)
    tracks = by_id(tracker.update([enemy(line(1)), enemy(line(0))], 0.5))  # ordre inversé, cellules voisines
    assert tracks["enemy_1"].cell_id == line(0) and tracks["enemy_2"].cell_id == line(1)


def test_global_assignment_beats_greedy_crossing_case() -> None:
    # A en 0, B en 3 ; vérité : A → 2, B → 4. Détections dans l'ordre [2, 4].
    greedy = greedy_assign([("A", line(0)), ("B", line(3))], [line(2), line(4)])
    assert greedy == {0: "B"}  # l'ancien algorithme vole B pour la détection 2 et perd A
    tracker = EntityTracker()
    tracker.update([enemy(line(0)), enemy(line(3))], 0.0)
    tracks = by_id(tracker.update([enemy(line(2)), enemy(line(4))], 0.5))
    assert tracks["enemy_1"].cell_id == line(2) and tracks["enemy_2"].cell_id == line(4)
    assert set(tracks) == {"enemy_1", "enemy_2"}  # aucune identité fragmentée


def test_short_occlusion_preserves_track_id() -> None:
    tracker = EntityTracker()
    tracker.update([enemy(line(0))], 0.0)
    tracker.update([], 0.4)
    tracks = by_id(tracker.update([enemy(line(1))], 0.8))
    assert list(tracks) == ["enemy_1"] and tracks["enemy_1"].state is TrackState.OBSERVED


def test_occluded_track_not_reported_as_observed() -> None:
    tracker = EntityTracker()
    tracker.update([enemy(line(0))], 0.0)
    occluded = by_id(tracker.update([], 0.4))["enemy_1"]
    assert occluded.state is TrackState.OCCLUDED and not occluded.observed_this_frame
    assert occluded.cell_id is None and occluded.last_known_cell_id == line(0)
    assert occluded.confidence == 0.0 and occluded.evidence is None


def test_occluded_last_cell_not_forced_occupied() -> None:
    occluded = EnemyObservation("enemy_1", Cell(1, 1), (10, 10), 0.0, line(0), "OCCLUDED", False)
    visible = EnemyObservation("enemy_2", Cell(2, 2), (20, 20), 0.9, line(3))
    observation = CombatObservation(True, 0.9, None, 0.0, None, 0.0, (occluded, visible),
                                    CombatGridObservation(), None, None, 0.0, 0.0, 0.0, entities=())
    assert observation.enemy_cells == (Cell(2, 2),)
    assert not observation.safe_for_decision


def test_long_disappearance_becomes_lost() -> None:
    tracker = EntityTracker(TrackerConfig(occlusion_frames=2, occlusion_seconds=10.0))
    tracker.update([enemy(line(0))], 0.0)
    states = [by_id(tracker.update([], 0.1 * step))["enemy_1"].state for step in range(1, 4)]
    assert states == [TrackState.OCCLUDED, TrackState.OCCLUDED, TrackState.LOST]
    assert "enemy_1" not in by_id(tracker.update([], 0.5))


def test_lost_by_time_even_with_few_frames() -> None:
    tracker = EntityTracker(TrackerConfig(occlusion_frames=50, occlusion_seconds=1.0))
    tracker.update([enemy(line(0))], 0.0)
    assert by_id(tracker.update([], 1.5))["enemy_1"].state is TrackState.LOST


def test_reappearance_recovers_track() -> None:
    tracker = EntityTracker(TrackerConfig(occlusion_frames=1, occlusion_seconds=1.0))
    tracker.update([enemy(line(0))], 0.0)
    tracker.update([], 0.3)
    tracker.update([], 0.6)  # LOST
    tracks = by_id(tracker.update([enemy(line(1))], 1.2))
    assert list(tracks) == ["enemy_1"] and tracks["enemy_1"].state is TrackState.OBSERVED


def test_impossible_jump_rejected_or_penalized() -> None:
    tracker = EntityTracker(TrackerConfig(max_jump_cells=3, jump_cells_per_second=0.0))
    tracker.update([enemy(line(0))], 0.0)
    tracks = by_id(tracker.update([enemy(line(9))], 0.3))
    assert tracks["enemy_1"].state is TrackState.OCCLUDED  # l'ancienne piste n'a pas sauté
    assert tracks["enemy_2"].cell_id == line(9)            # nouvelle identité plutôt qu'une fusion


def test_player_track_never_swaps_with_enemy() -> None:
    tracker = EntityTracker()
    tracker.update([player(line(0)), enemy(line(1))], 0.0)
    tracks = by_id(tracker.update([player(line(1)), enemy(line(0))], 0.5))
    assert tracks["player"].kind is EntityKind.PLAYER and tracks["player"].cell_id == line(1)
    assert tracks["enemy_1"].kind is EntityKind.ENEMY and tracks["enemy_1"].cell_id == line(0)


def test_unknown_detection_never_feeds_enemy_track() -> None:
    tracker = EntityTracker()
    tracker.update([enemy(line(0))], 0.0)
    unknown = EntityEvidence(line(0), EntityKind.UNKNOWN, 0.95, marker_score=0.95)
    assert by_id(tracker.update([unknown], 0.5))["enemy_1"].state is TrackState.OCCLUDED


def test_low_confidence_detection_does_not_move_track() -> None:
    tracker = EntityTracker(TrackerConfig(min_move_confidence=0.6, new_track_confidence=0.7))
    tracker.update([enemy(line(0))], 0.0)
    tracks = by_id(tracker.update([enemy(line(1), confidence=0.4)], 0.5))
    assert tracks["enemy_1"].state is TrackState.OCCLUDED and tracks["enemy_1"].last_known_cell_id == line(0)
    assert set(tracks) == {"enemy_1"}
    # Même cellule : une preuve faible peut confirmer la piste sans la déplacer.
    assert by_id(tracker.update([enemy(line(0), confidence=0.4)], 0.9))["enemy_1"].cell_id == line(0)


def test_player_jump_confirmed_only_when_repeated() -> None:
    tracker = EntityTracker(TrackerConfig(max_jump_cells=2, jump_cells_per_second=0.0, player_confirm_frames=2))
    tracker.update([player(line(0))], 0.0)
    first = by_id(tracker.update([player(line(8))], 0.3))["player"]
    assert first.state is TrackState.OCCLUDED
    assert by_id(tracker.update([player(line(8))], 0.6))["player"].cell_id == line(8)


def test_observer_map_change_resets_tracks_and_background_but_keeps_profile() -> None:
    from dataclasses import replace
    from types import SimpleNamespace
    from entity_fixtures import BLUE, draw_entity, ground, region, synthetic_grid
    from combatbot.vision.background_model import CellBackgroundModel
    from combatbot.vision.combat_observer import RealCombatObserver
    from combatbot.vision.entity_detector import CellEntityDetector
    from combatbot.vision.entity_models import MarkerColorClass, TeamMarkerProfile, VisualProfiles
    grid, shape = synthetic_grid(region(287, 3))
    grid = replace(grid, map_id_declared=1, grid_visibility={"state": "VISIBLE"},
                   alignment={"status": "ALIGNED"})
    observer = RealCombatObserver.__new__(RealCombatObserver)
    observer._entity_map = None
    observer.calibration = SimpleNamespace(layout_signature="test-map-reset")
    observer.entity_detector = CellEntityDetector()
    observer.entity_tracker = EntityTracker()
    observer.background_model = CellBackgroundModel()
    profile = VisualProfiles(None, TeamMarkerProfile(None, MarkerColorClass(118, 12, 0, 0, 1),
                                                     layout_signature="test-map-reset"))
    observer.entity_profiles = profile
    image = ground(shape)
    draw_entity(image, grid, 287, BLUE)
    first = observer._observe_entities(image, grid, 0.0)
    assert first[4] and first[4][0].cell_id == 287
    blank = ground(shape)
    for index in range(1, 7):
        observer._observe_entities(blank, grid, index * 0.1)
    assert observer.background_model.ready(287)
    changed = observer._observe_entities(blank, replace(grid, map_id_declared=2), 0.8)
    assert changed[4] == () and changed[5]["entities"] == ()
    assert not observer.background_model.ready(287)
    assert "FREE" not in [cell.state.value for cell in changed[0].cells]
    assert observer.entity_profiles is profile


# ---------------------------------------------------------------------------- LOT 3B-5D : maintien
HOLD = TrackerConfig(hold_with_sprite=True)   # désactivé par défaut depuis l'échec TEST 3B-5D


def test_hold_is_disabled_by_default() -> None:
    tracker = EntityTracker()
    tracker.update(_frame([enemy(line(0))]), 0.0)
    assert by_id(tracker.update(_frame([], sprites=[line(0)]), 0.5))["enemy_1"].state is TrackState.OCCLUDED


def _frame(entities=(), sprites=()):
    from combatbot.vision.entity_models import EntityDetectionResult
    return EntityDetectionResult(tuple(entities), diagnostics={"sprite_cells": list(sprites)})


def test_hidden_enemy_is_held_while_a_sprite_stays_on_its_cell() -> None:
    tracker = EntityTracker(HOLD)
    tracker.update(_frame([enemy(line(0))]), 0.0)
    held = by_id(tracker.update(_frame([], sprites=[line(0)]), 0.5))["enemy_1"]
    assert held.state is TrackState.HELD and held.cell_id == line(0) and held.claimed_cell == line(0)
    assert not held.observed_this_frame and 0.0 < held.confidence < 0.9
    assert held.evidence is None                      # une position maintenue n'est pas une observation


def test_enemy_leaving_an_empty_cell_is_not_held() -> None:
    tracker = EntityTracker(HOLD)
    tracker.update(_frame([enemy(line(0))]), 0.0)
    gone = by_id(tracker.update(_frame([], sprites=[]), 0.5))["enemy_1"]   # ennemi tué : case vide
    assert gone.state is TrackState.OCCLUDED and gone.cell_id is None and gone.claimed_cell is None


def test_hold_needs_detector_sprite_evidence_and_expires() -> None:
    tracker = EntityTracker(TrackerConfig(occlusion_frames=2, hold_with_sprite=True))
    tracker.update([enemy(line(0))], 0.0)
    assert by_id(tracker.update([], 0.1))["enemy_1"].state is TrackState.OCCLUDED   # liste simple : pas de maintien
    states = [by_id(tracker.update(_frame([], sprites=[line(0)]), 0.2 + 0.1 * i))["enemy_1"].state for i in range(2)]
    assert states == [TrackState.HELD, TrackState.LOST]
    disabled = EntityTracker(TrackerConfig(hold_with_sprite=False))
    disabled.update(_frame([enemy(line(0))]), 0.0)
    assert by_id(disabled.update(_frame([], sprites=[line(0)]), 0.5))["enemy_1"].state is TrackState.OCCLUDED


def test_held_track_keeps_its_identity_when_seen_again() -> None:
    tracker = EntityTracker(HOLD)
    tracker.update(_frame([enemy(line(0)), enemy(line(4))]), 0.0)
    tracker.update(_frame([enemy(line(4))], sprites=[line(0)]), 0.5)
    back = by_id(tracker.update(_frame([enemy(line(0)), enemy(line(4))]), 1.0))
    assert back["enemy_1"].cell_id == line(0) and back["enemy_1"].state is TrackState.OBSERVED
    assert set(back) == {"enemy_1", "enemy_2"}
