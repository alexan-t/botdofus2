"""LOT 3B-3 : visibilité de grille, alignement, dérive, cohérence de map, état de combat."""
import math

import cv2
import numpy as np
import pytest

from combatbot.gamedata.models import DofusCellId, GameMapCell, GridTopology
from combatbot.gamedata.topology import CELL_COUNT, cell_to_grid
from combatbot.vision.combat_observer import RealCombatObserver
from combatbot.vision.gamedata_grid import projected_observation
from combatbot.vision.grid_fit import candidate_union
from combatbot.vision.grid_projection import GridProjector
from combatbot.vision.grid_recipe import shifted
from combatbot.vision.grid_validation import (
    AlignmentConfig, AlignmentStatus, CombatState, CombatStateDetector, DriftState, DriftTracker,
    GridAlignmentObservation, GridAlignmentValidator, GridVisibilityDetector, GridVisibilityState,
    MapConsistencyTracker, MapDeclarationState, RuntimeAdjustment,
)
from test_gamedata_grid import CROP, TRANSFORM, _calibration, _frame, render, resolver

CELL_H = TRANSFORM.cell_height


def blob(map_id=1, cx=17, cy=-3, radius=11):
    return GridTopology(map_id, tuple(GameMapCell(
        DofusCellId(i), walkable=abs(cell_to_grid(i).x - cx) + abs(cell_to_grid(i).y - cy) <= radius,
        non_walkable_during_fight=False, line_of_sight=True) for i in range(CELL_COUNT)))


def evaluate(image, topology=None, transform=TRANSFORM, candidates=None):
    topology = topology or blob()
    projected = GridProjector(transform).project(topology)
    grid = projected_observation(projected, image)
    candidates = candidate_union(image) if candidates is None else candidates
    visibility, inliers, outliers = GridVisibilityDetector().observe(projected, candidates, CROP, grid.topology_consistency)
    return projected, visibility, GridAlignmentValidator().validate(projected, visibility, inliers, outliers)


def exploration_image(seed=3):
    rng = np.random.default_rng(seed)
    image = np.full((CROP[1], CROP[0], 3), 60, np.uint8)
    for _ in range(80):  # decor: rectangles, blobs, a few diamond-ish shapes in one corner
        x, y = int(rng.integers(0, CROP[0])), int(rng.integers(0, CROP[1]))
        cv2.rectangle(image, (x, y), (x + int(rng.integers(10, 80)), y + int(rng.integers(10, 60))),
                      tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
    for x in range(40, 200, 30):
        cv2.polylines(image, [np.array([(x, 40), (x + 26, 53), (x, 66), (x - 26, 53)], np.int32)], True, (230,) * 3, 1)
    return image


def alignment(status, correction=None):
    return GridAlignmentObservation(status, 0.9, 1.0, 100, 80, {"median": 1.0, "p95": 2.0, "max": 3.0}, 0.7,
                                    correction, (0, 1, 2, 3), ())


# --- Visibility --------------------------------------------------------------------------------------

def test_projected_topology_does_not_imply_grid_visible():
    projected, visibility, align = evaluate(np.full((CROP[1], CROP[0], 3), 90, np.uint8))
    assert len(projected.cells) == 560
    assert visibility.state is GridVisibilityState.NOT_VISIBLE
    assert align.status is AlignmentStatus.INSUFFICIENT_EVIDENCE


def test_exploration_grid_not_visible():
    _, visibility, _ = evaluate(exploration_image())
    assert visibility.state is GridVisibilityState.NOT_VISIBLE
    assert visibility.inlier_count <= 3


def test_combat_grid_visible():
    topology = blob()
    _, visibility, align = evaluate(render(topology), topology)
    assert visibility.state is GridVisibilityState.VISIBLE
    assert len(visibility.supported_regions) >= 6 and visibility.coverage >= 0.8
    assert align.status is AlignmentStatus.ALIGNED
    assert any(reason.startswith("régions=") for reason in visibility.reasons)


def test_grid_visibility_unknown_on_weak_evidence():
    topology = blob()
    image = render(topology)
    mask = np.zeros(image.shape[:2], bool)
    mask[:, CROP[0] // 2 - 60:CROP[0] // 2 + 60] = True   # one vertical strip of the arena only
    image[~mask] = 32
    _, visibility, align = evaluate(image, topology)
    assert visibility.state is GridVisibilityState.UNKNOWN, visibility.reasons
    assert align.status is AlignmentStatus.INSUFFICIENT_EVIDENCE


# --- Alignment ---------------------------------------------------------------------------------------

def test_alignment_uses_spatial_coverage():
    topology = blob()
    image = render(topology)
    image[:, CROP[0] // 3:] = 32          # evidence only in the left third: dense but local
    _, visibility, align = evaluate(image, topology)
    assert visibility.state is not GridVisibilityState.VISIBLE
    assert align.status is AlignmentStatus.INSUFFICIENT_EVIDENCE


def test_alignment_uses_median_residual():
    topology = blob()
    image = render(topology)
    candidates = list(candidate_union(image))
    projected = GridProjector(TRANSFORM).project(topology)
    for cell in list(projected.cells)[200:215]:  # spurious detections 0.25 cell away from their centre
        candidates.append((cell.center.x + 0.25 * CELL_H, cell.center.y, TRANSFORM.cell_width * 0.8, CELL_H * 0.8, 1.0))
    _, visibility, align = evaluate(image, topology, candidates=candidates)
    assert align.status is AlignmentStatus.ALIGNED
    assert align.residual["median"] < 2 and align.residual["max"] >= 0.2 * CELL_H


def test_outlier_does_not_move_transform():
    topology = blob()
    image = render(topology)
    candidates = list(candidate_union(image))
    projected = GridProjector(TRANSFORM).project(topology)
    traversable = [c for c in projected.cells if c.static_traversable_in_fight]
    for cell in traversable[:12]:  # a local cluster of shifted false contours
        candidates.append((cell.center.x + 9, cell.center.y + 4, TRANSFORM.cell_width * 0.8, CELL_H * 0.8, 1.0))
    _, _, align = evaluate(image, topology, candidates=candidates)
    assert align.status is AlignmentStatus.ALIGNED and align.suggested_correction is None


def test_small_runtime_translation_can_be_suggested():
    topology = blob()
    image = render(topology)
    dx, dy = 0.08 * CELL_H, -0.04 * CELL_H
    _, visibility, align = evaluate(image, topology, transform=TRANSFORM.translated(dx, dy))
    assert visibility.state is GridVisibilityState.VISIBLE
    assert align.status is AlignmentStatus.DEGRADED
    correction = align.suggested_correction
    assert correction.dx == pytest.approx(-dx, abs=1.0) and correction.dy == pytest.approx(-dy, abs=1.0)
    assert correction.magnitude() <= AlignmentConfig().local_window * CELL_H


def test_translation_beyond_local_window_is_misaligned():
    topology = blob()
    _, visibility, align = evaluate(render(topology), topology, transform=TRANSFORM.translated(0.3 * CELL_H, 0))
    assert visibility.state is GridVisibilityState.VISIBLE   # a drifted grid is still a drawn grid
    assert align.status is AlignmentStatus.MISALIGNED and align.suggested_correction is None
    assert any("RECALIBRATION_REQUIRED" in r for r in align.reasons)


def test_full_cell_shift_is_never_auto_applied():
    topology = blob()
    image = render(topology)
    one_cell = shifted(TRANSFORM, 1, 0)
    _, _, align = evaluate(image, topology, transform=one_cell)
    # Locally indistinguishable (the ±1 ambiguity): it may look aligned, but nothing is ever applied.
    assert align.suggested_correction is None or align.suggested_correction.magnitude() < CELL_H * 0.15
    tracker = DriftTracker(TRANSFORM)
    step = one_cell.basis_x
    for _ in range(6):
        tracker.update(alignment(AlignmentStatus.DEGRADED, RuntimeAdjustment(step.x, step.y)))
    assert tracker.adjustment.is_zero and tracker.state is DriftState.RECALIBRATION_REQUIRED
    for _ in range(6):   # many small agreeing steps can never add up to a cell either
        tracker.update(alignment(AlignmentStatus.DEGRADED, RuntimeAdjustment(2.0, 0.0)))
    assert tracker.adjustment.magnitude() <= AlignmentConfig().local_window * CELL_H + 1e-9


def test_runtime_adjustment_does_not_mutate_profile():
    base_before = TRANSFORM.to_dict()
    tracker = DriftTracker(TRANSFORM)
    for _ in range(3):
        tracker.update(alignment(AlignmentStatus.DEGRADED, RuntimeAdjustment(1.5, -1.0)))
    assert tracker.state is DriftState.DRIFT_CONFIRMED
    assert tracker.base_transform.to_dict() == base_before == TRANSFORM.to_dict()
    assert tracker.effective_transform.origin.x == pytest.approx(TRANSFORM.origin.x + 1.5)


def test_temporal_consensus_required_for_drift():
    tracker = DriftTracker(TRANSFORM)
    assert tracker.update(alignment(AlignmentStatus.DEGRADED, RuntimeAdjustment(2, 0))) is DriftState.DRIFT_SUSPECTED
    assert tracker.update(alignment(AlignmentStatus.DEGRADED, RuntimeAdjustment(2.2, 0))) is DriftState.DRIFT_SUSPECTED
    assert tracker.adjustment.is_zero
    tracker2 = DriftTracker(TRANSFORM)
    for dx in (2.0, -2.0, 2.0):  # disagreeing corrections: no consensus
        tracker2.update(alignment(AlignmentStatus.DEGRADED, RuntimeAdjustment(dx, 0)))
    assert tracker2.adjustment.is_zero and tracker2.state is DriftState.DRIFT_SUSPECTED
    assert tracker.update(alignment(AlignmentStatus.DEGRADED, RuntimeAdjustment(2.1, 0))) is DriftState.DRIFT_CONFIRMED
    assert tracker.adjustment.dx == pytest.approx(2.1)


def test_runtime_adjustment_recovers_to_zero():
    tracker = DriftTracker(TRANSFORM)
    for _ in range(3):
        tracker.update(alignment(AlignmentStatus.DEGRADED, RuntimeAdjustment(2, 1)))
    assert not tracker.adjustment.is_zero
    for _ in range(3):
        tracker.update(alignment(AlignmentStatus.INSUFFICIENT_EVIDENCE))
    assert not tracker.adjustment.is_zero
    assert tracker.update(alignment(AlignmentStatus.INSUFFICIENT_EVIDENCE)) is DriftState.RECOVERED
    assert tracker.adjustment.is_zero and tracker.effective_transform == TRANSFORM


# --- Map consistency ---------------------------------------------------------------------------------------

def test_stale_map_not_decided_when_grid_not_visible():
    tracker = MapConsistencyTracker()
    for value in (0.75, 0.74, 0.76):
        tracker.update(1, "manual_guess", GridVisibilityState.VISIBLE, value)
    for _ in range(5):
        result = tracker.update(1, "manual_guess", GridVisibilityState.NOT_VISIBLE, -0.2)
        assert result.state is MapDeclarationState.UNKNOWN
    assert tracker.update(1, "manual_guess", GridVisibilityState.UNKNOWN, 0.1).state is MapDeclarationState.UNKNOWN


def test_single_low_consistency_does_not_mark_verified_map_stale():
    tracker = MapConsistencyTracker()
    for value in (0.75, 0.74, 0.76):
        tracker.update(1, "user_verified_mapid", GridVisibilityState.VISIBLE, value)
    assert tracker.update(1, "user_verified_mapid", GridVisibilityState.VISIBLE, 0.40).state is MapDeclarationState.SUSPECT
    assert tracker.update(1, "user_verified_mapid", GridVisibilityState.VISIBLE, 0.42).state is MapDeclarationState.SUSPECT
    third = tracker.update(1, "user_verified_mapid", GridVisibilityState.VISIBLE, 0.41)
    assert third.state is MapDeclarationState.STALE_LIKELY
    assert third.message == "Le map ID déclaré semble ne plus correspondre à la grille observée."


def test_relative_consistency_drop_marks_suspect():
    tracker = MapConsistencyTracker()
    for value in (0.70, 0.68):
        tracker.update(7, "manual_guess", GridVisibilityState.VISIBLE, value)
    first = tracker.update(7, "manual_guess", GridVisibilityState.VISIBLE, 0.52)
    assert first.state is MapDeclarationState.SUSPECT and first.delta_from_baseline == pytest.approx(0.17)
    assert tracker.update(7, "manual_guess", GridVisibilityState.VISIBLE, 0.50).state is MapDeclarationState.STALE_LIKELY


def test_correct_map_low_absolute_consistency_can_remain_valid():
    # Real map 191106050: 0.601 and 0.620 with the correct map (below the old 0.65 threshold).
    tracker = MapConsistencyTracker()
    states = [tracker.update(191106050, "user_verified_mapid", GridVisibilityState.VISIBLE, value).state
              for value in (0.601, 0.620, 0.61, 0.605)]
    assert set(states) == {MapDeclarationState.CONSISTENT}


# --- Combat state ----------------------------------------------------------------------------------------

def test_combat_detection_does_not_use_cell_count():
    detector = CombatStateDetector()
    projected, visibility, _ = evaluate(np.full((CROP[1], CROP[0], 3), 90, np.uint8))
    result = detector.detect(grid_source="GAMEDATA_PROJECTED", visibility=visibility, grid_confidence=1.0,
                             hud={"counters": 1.0, "end_turn": 1.0, "spell_bar": 1.0})
    assert len(projected.cells) == 560 and result.state is CombatState.EXPLORATION and not result.combat_detected
    legacy = detector.detect(grid_source="LEGACY_CALIBRATION", visibility=None, grid_confidence=0.05,
                             hud={"counters": 1.0, "end_turn": 1.0, "spell_bar": 1.0})
    assert legacy.state is CombatState.EXPLORATION
    unknown = detector.detect(grid_source="GAMEDATA_PROJECTED", visibility=None, grid_confidence=1.0, hud={})
    assert unknown.state is CombatState.UNKNOWN and not unknown.combat_detected


def _observer(topology, frames):
    frames = iter(frames)
    return RealCombatObserver(7, _calibration(), frame_provider=lambda: next(frames),
                              number_reader=lambda image: (None, 0.0), grid_resolver=resolver(topology))


def test_exploration_not_detected_as_combat():
    topology = blob()
    observer = _observer(topology, [_frame(exploration_image(seed)) for seed in range(4)])
    for _ in range(4):
        packet = observer.observe()
        observation = packet.observation
        assert observation.combat_state == "EXPLORATION" and not observation.combat_detected
        assert len(observation.grid.cells) == 560
        assert observation.grid.map_declaration_state == "UNKNOWN"


def test_existing_combat_still_detected():
    topology = blob()
    observer = _observer(topology, [_frame(render(topology)) for _ in range(3)])
    for _ in range(3):
        packet = observer.observe()
    observation = packet.observation
    assert observation.combat_state == "COMBAT" and observation.combat_detected
    assert observation.grid.grid_visibility_state == "VISIBLE"
    assert observation.grid.alignment["status"] == "ALIGNED"
    assert packet.metadata["combat_state"] == "COMBAT"


def test_observer_confirms_small_drift_then_realigns_without_touching_profile():
    topology = blob()
    drifted = render(topology, TRANSFORM.translated(0.1 * CELL_H, 0))
    r = resolver(topology)
    profile_before = r.profile.to_dict()
    frames = iter([_frame(drifted)] * 6)
    observer = RealCombatObserver(7, _calibration(), frame_provider=lambda: next(frames),
                                  number_reader=lambda image: (None, 0.0), grid_resolver=r)
    states = [observer.observe().observation.grid.drift["state"] for _ in range(5)]
    assert states[:2] == ["DRIFT_SUSPECTED", "DRIFT_SUSPECTED"] and "DRIFT_CONFIRMED" in states
    assert observer.drift.adjustment.dx == pytest.approx(0.1 * CELL_H, abs=0.8)
    last = observer.observe().observation.grid
    assert last.alignment["status"] == "ALIGNED"
    assert {c.cell_id for c in last.cells} == set(range(560))
    assert r.profile.to_dict() == profile_before
    assert last.drift["base_transform"] == TRANSFORM.to_dict()


def test_no_free_state_from_alignment():
    topology = blob()
    observer = _observer(topology, [_frame(render(topology))])
    grid = observer.observe().observation.grid
    assert all(cell.state.value != "FREE" for cell in grid.cells)


# --- Corpus benchmark ---------------------------------------------------------------------------------

def test_grid_validation_benchmark_is_optional(tmp_path):
    from combatbot.corpus.grid_validation_benchmark import run_grid_validation_benchmark
    from combatbot.corpus.repository import CorpusRepository
    repository = CorpusRepository(tmp_path / "corpus")
    assert run_grid_validation_benchmark(repository, None)["available"] is False
    assert run_grid_validation_benchmark(repository, lambda _map: blob())["available"] is False


def test_grid_validation_benchmark_on_small_corpus(tmp_path):
    from combatbot.corpus.grid_validation_benchmark import run_grid_validation_benchmark
    from combatbot.corpus.models import Annotation
    from combatbot.corpus.repository import CorpusRepository
    from combatbot.vision.combat_observer import save_debug_observation
    topology = blob()
    repository = CorpusRepository(tmp_path / "corpus")
    repository.ensure_layout()
    frames = [("combat", render(topology)), ("exploration", exploration_image(1)), ("combat", render(topology))]
    for index, (mode, image) in enumerate(frames):
        packet = _observer(topology, [_frame(image)]).observe()
        target = tmp_path / f"debug{index}"
        target.mkdir()
        save_debug_observation(packet, target)
        entry = repository.import_debug(target, session_id="s", frame_index=index, tags=("lot3b2r", f"mode_{mode}"))
        repository.save_annotation(Annotation(entry.observation_id, combat_truth=mode == "combat"))
    report = run_grid_validation_benchmark(repository, lambda _map: topology)
    assert report["available"] and report["frames"] == 3
    assert report["grid_visibility_confusion_matrix"] == {"VISIBLE": {"VISIBLE": 2}, "NOT_VISIBLE": {"NOT_VISIBLE": 1}}
    assert report["combat_confusion_matrix_after"] == {"COMBAT": {"COMBAT": 2}, "EXPLORATION": {"EXPLORATION": 1}}
    assert report["combat_false_positive_rate_after"] == 0.0
    assert report["alignment"]["status"] == {"ALIGNED": 2}
