"""LOT 3B-5B : apprentissage layout-aware, profil joueur multi-exemples, anneau partiel, horodatages."""

from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from entity_fixtures import BLUE, RED, draw_ring, draw_ring_arc, draw_sprite, ground, region, synthetic_grid
from combatbot.corpus.entity_benchmark import (
    EntitySample, _tracking_metrics, build_profiles, entity_inventory, replay_timestamps, run_entity_benchmark,
)
from combatbot.corpus.entity_split import cover_layouts
from combatbot.vision.entity_detector import CellEntityDetector, DetectionContext
from combatbot.vision.entity_models import (
    EntityKind, MarkerColorClass, PlayerVisualProfileV2, TeamMarkerProfile, VisualProfiles, player_profile_from_dict,
)
from combatbot.vision.entity_profiles import (
    gate_partial, load_team_profile, load_train_player_profile, player_profile_v2, save_team_profile,
    save_train_player_profile,
)
from combatbot.vision.entity_tracker import EntityTracker

CENTER = 287
LAYOUT = "layout-a"
TRUSTED = DetectionContext(grid_visible=True, grid_aligned=True, map_id=1, layout_signature=LAYOUT)
RED_HUE = float(cv2.cvtColor(np.uint8([[RED]]), cv2.COLOR_BGR2HSV)[0, 0, 0])
BLUE_HUE = float(cv2.cvtColor(np.uint8([[BLUE]]), cv2.COLOR_BGR2HSV)[0, 0, 0])
PLAYER_TEAM = MarkerColorClass(RED_HUE, 10, 0, 0, 1)


def enemy_team(partial_allowed: bool = True) -> MarkerColorClass:
    return MarkerColorClass(BLUE_HUE, 10, 0, 0, 1, 4.0, partial_allowed, "test")


def teams(partial_allowed: bool = True) -> TeamMarkerProfile:
    return TeamMarkerProfile(PLAYER_TEAM, enemy_team(partial_allowed), layout_signature=LAYOUT)


# ---------------------------------------------------------------- couverture TRAIN par layout
def test_new_layout_gets_train_coverage_without_moving_test() -> None:
    registry = {"schema_version": 1, "groups": {"old|a": "train", "old|b": "test",
                                                 "new|val": "validation", "new|test": "test"}}
    covered = cover_layouts(registry, {"L_old": {"old|a", "old|b"}, "L_new": {"new|val", "new|test"}})
    assert covered["schema_version"] == 2
    assert covered["groups"]["new|val"] == "train"
    assert covered["groups"]["new|test"] == "test" and covered["groups"]["old|b"] == "test"
    assert covered["migrations"] == [{"reason": "layout_train_coverage", "group_id": "new|val",
                                      "previous_split": "validation", "new_split": "train",
                                      "layout_signature": "L_new"}]
    assert registry["groups"]["new|val"] == "validation"  # l'original n'est pas modifié
    # Rejouer la migration ne déplace plus rien et conserve l'historique.
    again = cover_layouts(covered, {"L_old": {"old|a", "old|b"}, "L_new": {"new|val", "new|test"}})
    assert again["groups"] == covered["groups"] and again["migrations"] == covered["migrations"]


def test_layout_train_selection_is_deterministic() -> None:
    groups = {f"new|g{index}": None for index in range(6)}
    first = cover_layouts({"groups": dict(groups)}, {"L": set(groups)})
    second = cover_layouts({"groups": dict(reversed(list(groups.items())))}, {"L": set(reversed(list(groups)))})
    assert first["migrations"][0]["group_id"] == second["migrations"][0]["group_id"]
    # Priorité : un groupe VALIDATION passe avant un groupe non attribué.
    mixed = cover_layouts({"groups": {"new|a": None, "new|z": "validation"}}, {"L": {"new|a", "new|z"}})
    assert mixed["migrations"][0]["group_id"] == "new|z"


def test_existing_test_group_is_never_promoted() -> None:
    covered = cover_layouts({"groups": {"new|t1": "test", "new|t2": "test"}}, {"L": {"new|t1", "new|t2"}})
    assert covered["groups"] == {"new|t1": "test", "new|t2": "test"}
    assert covered["layouts_without_train"] == ["L"] and covered["migrations"] == []


# ---------------------------------------------------------------- profil joueur V2
@pytest.fixture
def small():
    grid, shape = synthetic_grid(region(CENTER, 4))
    return grid, shape


def _character(image, grid, cell_id: int, body, *, seed: int, ring=RED) -> None:
    cell = grid.cell_by_id(cell_id)
    draw_sprite(image, cell, color=body, seed=seed)
    draw_ring(image, cell, ring)


def _vector(image, grid, cell_id: int) -> tuple[float, ...]:
    detector = CellEntityDetector()
    maps = detector.roi_maps(grid.cells, image.shape[:2])
    features = detector.cell_features(image, maps)
    index = maps.index_of(cell_id)
    return tuple(float(v) for v in np.concatenate([features["foot_lab"][index], features["center_lab"][index]]))


def test_player_profile_uses_multiple_human_train_examples(small) -> None:
    grid, shape = small
    # Deux apparences réelles du même personnage (orientation/animation) : A puis B.
    look_a, look_b = (40, 60, 90), (150, 90, 170)
    frame_a, frame_b = ground(shape, seed=1), ground(shape, seed=2)
    _character(frame_a, grid, CENTER, look_a, seed=7)
    _character(frame_b, grid, CENTER, look_b, seed=7)
    single = player_profile_v2([("obs-a", _vector(frame_a, grid, CENTER))], {}, PLAYER_TEAM, layout_signature=LAYOUT)
    both = player_profile_v2([("obs-a", _vector(frame_a, grid, CENTER)), ("obs-b", _vector(frame_b, grid, CENTER))],
                             {"marker_unreadable": 1}, PLAYER_TEAM, layout_signature=LAYOUT)
    assert len(both.prototypes) == 2 and both.accepted == 2 and both.rejected == {"marker_unreadable": 1}
    assert both.dispersion["pairwise_max"] > 0
    probe = ground(shape, seed=3)
    _character(probe, grid, CENTER, look_b, seed=7)
    team = TeamMarkerProfile(PLAYER_TEAM, None, layout_signature=LAYOUT)
    assert CellEntityDetector().detect(probe, grid, VisualProfiles(single, team), TRUSTED).player is None
    result = CellEntityDetector().detect(probe, grid, VisualProfiles(both, team), TRUSTED)
    assert result.player is not None and result.player.cell_id == CENTER
    assert player_profile_from_dict(both.to_dict()) == both


def test_player_profile_rejects_ambiguous_same_team_candidate(small) -> None:
    grid, shape = small
    ally = CENTER + 2
    look = (40, 60, 90)
    reference = ground(shape, seed=1)
    _character(reference, grid, CENTER, look, seed=7)
    profile = player_profile_v2([("obs", _vector(reference, grid, CENTER))], {}, PLAYER_TEAM, layout_signature=LAYOUT)
    team = TeamMarkerProfile(PLAYER_TEAM, None, layout_signature=LAYOUT)
    # Allié de la même équipe, même apparence : la marge manque → aucun PLAYER, deux présences UNKNOWN.
    twin = ground(shape, seed=1)
    _character(twin, grid, CENTER, look, seed=7)
    _character(twin, grid, ally, look, seed=7)
    result = CellEntityDetector().detect(twin, grid, VisualProfiles(profile, team), TRUSTED)
    assert result.player is None and result.diagnostics["player_decision"] == "ambiguous"
    assert {item.cell_id: item.kind for item in result.entities} == {CENTER: EntityKind.UNKNOWN,
                                                                     ally: EntityKind.UNKNOWN}
    # Allié d'apparence différente : le joueur reste identifié, l'allié reste UNKNOWN.
    distinct = ground(shape, seed=1)
    _character(distinct, grid, CENTER, look, seed=7)
    _character(distinct, grid, ally, (200, 200, 60), seed=9)
    result = CellEntityDetector().detect(distinct, grid, VisualProfiles(profile, team), TRUSTED)
    assert result.player is not None and result.player.cell_id == CENTER
    assert {item.cell_id: item.kind for item in result.entities}[ally] is EntityKind.UNKNOWN


def test_train_profiles_saved_per_layout(tmp_path: Path, small) -> None:
    grid, shape = small
    image = ground(shape)
    _character(image, grid, CENTER, (40, 60, 90), seed=7)
    for layout in ("layout-a", "layout-b"):
        save_team_profile(tmp_path, replace(teams(), layout_signature=layout))
        save_train_player_profile(tmp_path, player_profile_v2([("obs", _vector(image, grid, CENTER))], {},
                                                              PLAYER_TEAM, layout_signature=layout))
    assert load_team_profile(tmp_path, "layout-b").layout_signature == "layout-b"
    assert load_team_profile(tmp_path, "layout-a").enemy_team.partial_allowed
    assert load_train_player_profile(tmp_path, "layout-a").layout_signature == "layout-a"
    assert load_train_player_profile(tmp_path, "layout-c") is None


# ---------------------------------------------------------------- anneau partiel
@pytest.fixture
def large():
    # Cellules de 128×64 : bande d'anneau de plusieurs pixels, comme sur le client réel.
    grid, shape = synthetic_grid(region(CENTER, 3), cell_width=128, cell_height=64)
    return grid, shape


def _masked_enemy(grid, shape, **arc):
    image = ground(shape)
    cell = grid.cell_by_id(CENTER)
    draw_sprite(image, cell, seed=3)
    # Le sprite masque les secteurs bas 5–7 : seule la moitié haute et le secteur 4 restent visibles.
    draw_ring_arc(image, cell, BLUE, arc.get("start", -math.pi), arc.get("end", math.pi / 4))
    return image


def test_partial_ring_detects_enemy_when_sprite_masks_one_sector(large) -> None:
    grid, shape = large
    image = _masked_enemy(grid, shape)
    without = CellEntityDetector().detect(image, grid, VisualProfiles(None, teams(False)), TRUSTED)
    assert without.enemies == ()  # l'anneau complet ne suffit plus : 1 secteur bas sur 4
    result = CellEntityDetector().detect(image, grid, VisualProfiles(None, teams(True)), TRUSTED)
    assert [(item.cell_id, item.kind, item.marker_state) for item in result.entities] == [
        (CENTER, EntityKind.ENEMY, "PARTIAL_RING")]
    assert result.occupancy[CENTER] == "OCCUPIED"
    assert result.entities[0].to_dict()["marker_state"] == "PARTIAL_RING"
    # Grille non fiable : chemin partiel coupé.
    untrusted = DetectionContext(grid_visible=True, grid_aligned=False, layout_signature=LAYOUT)
    assert CellEntityDetector().detect(image, grid, VisualProfiles(None, teams(True)), untrusted).enemies == ()


def test_partial_ring_requires_multiple_consistent_sectors(large) -> None:
    grid, shape = large
    one_sector = _masked_enemy(grid, shape, start=-math.pi + 0.1, end=-math.pi + 0.6)
    adjacent = _masked_enemy(grid, shape, start=-math.pi + 0.1, end=-math.pi / 2 - 0.1)
    for image in (one_sector, adjacent):
        result = CellEntityDetector().detect(image, grid, VisualProfiles(None, teams(True)), TRUSTED)
        assert result.entities == ()
        assert result.occupancy[CENTER] == "UNKNOWN"


def test_partial_ring_colour_alone_is_not_entity(large) -> None:
    grid, shape = large
    cell = grid.cell_by_id(CENTER)
    patch = ground(shape)
    cv2.ellipse(patch, cell.center, (40, 20), 0, 0, 360, BLUE, -1)  # aplat de couleur d'équipe
    bare_arc = ground(shape)
    draw_ring_arc(bare_arc, cell, BLUE, -math.pi, math.pi / 4)      # arc sans sprite (centre uniforme)
    blue_body = ground(shape)
    draw_sprite(blue_body, cell, color=BLUE, seed=3)                 # sprite de la couleur, sans anneau
    for image in (patch, bare_arc, blue_body):
        result = CellEntityDetector().detect(image, grid, VisualProfiles(None, teams(True)), TRUSTED)
        assert result.enemies == ()


def test_partial_ring_does_not_create_false_enemy_on_background(large) -> None:
    grid, shape = large
    cell = grid.cell_by_id(CENTER)
    for density in (0.03, 0.10, 0.35):
        for seed in range(3):
            image = ground(shape)
            draw_sprite(image, cell, seed=3)
            image[np.random.default_rng(seed).random(shape) < density] = BLUE
            result = CellEntityDetector().detect(image, grid, VisualProfiles(None, teams(True)), TRUSTED)
            assert result.enemies == (), (density, seed)
    striped = ground(shape)
    for x in range(0, shape[1], 9):
        cv2.line(striped, (x, 0), (x - shape[0], shape[0]), BLUE, 1)
    assert CellEntityDetector().detect(striped, grid, VisualProfiles(None, teams(True)), TRUSTED).enemies == ()


def test_partial_gate_needs_train_positives_and_no_contrary_truth() -> None:
    base = MarkerColorClass(BLUE_HUE, 8, 0, 0, 5, 4.0)
    assert gate_partial(base, 18, 0).partial_allowed
    assert not gate_partial(base, 3, 15).partial_allowed     # cas réel : équipe joueur sur étoiles
    assert not gate_partial(base, 2, 0).partial_allowed
    assert gate_partial(base, 18, 0).partial_gate == {"train_positives": 18, "train_contrary": 0}
    assert MarkerColorClass.from_dict(gate_partial(base, 18, 0).to_dict()) == gate_partial(base, 18, 0)


# ---------------------------------------------------------------- horodatages et identités
def _sample(index: int, timestamp=None, *, group="g", confirmed=True, enemies=((10, "E1"),)) -> EntitySample:
    document = {"prediction": {"timestamp": timestamp} if timestamp is not None else {}}
    return EntitySample(f"obs-{group}-{index}", "s", index, 1, group, "train", Path("x.png"), None, False,
                        tuple(enemies), (), (), None, LAYOUT, None, document,
                        tracking_identity_confirmed=confirmed,
                        tracking_identity_source="human_ui_review" if confirmed else None)


def test_entity_benchmark_uses_real_timestamps() -> None:
    samples = [_sample(0, 1000.0), _sample(1, 1001.0), _sample(2, 1005.0)]  # écarts réels 1 s puis 4 s
    times = replay_timestamps(samples)
    assert [times[s.observation_id] for s in samples] == [(1000.0, "prediction.timestamp"),
                                                          (1001.0, "prediction.timestamp"),
                                                          (1005.0, "prediction.timestamp")]
    # Écart réel de 1 s : saut de 8 cellules accepté (6 + 2/s) ; au pas fictif de 0,4 s il serait refusé.
    tracker = EntityTracker()
    tracker.update([_evidence(CENTER)], 1000.0)
    moved = tracker.update([_evidence(CENTER + 8 * 14)], 1001.0)
    tracker_fake = EntityTracker()
    tracker_fake.update([_evidence(CENTER)], 0.0)
    fake = tracker_fake.update([_evidence(CENTER + 8 * 14)], 0.4)
    from combatbot.vision.entity_tracker import grid_distance
    assert grid_distance(CENTER, CENTER + 8 * 14) == 8
    assert [item.track_id for item in moved if item.observed_this_frame] == ["enemy_1"]
    assert sorted(item.track_id for item in fake if item.observed_this_frame) == ["enemy_2"]
    # Écart réel de 4 s sans détection intermédiaire : la piste dépasse l'occlusion (4 s) → perdue.
    lost = EntityTracker()
    lost.update([_evidence(CENTER)], 1001.0)
    assert lost.update([], 1005.5)[0].state.value == "LOST"


def test_replay_timestamps_order_and_missing() -> None:
    with pytest.raises(ValueError, match="non croissant"):
        replay_timestamps([_sample(0, 10.0), _sample(1, 9.0)])
    with pytest.raises(ValueError):
        replay_timestamps([_sample(0, 10.0), _sample(1, float("nan"))])
    times = replay_timestamps([_sample(0, 10.0), _sample(1), _sample(2, 14.0), _sample(3)])
    assert times["obs-g-1"] == (12.0, "fallback_interpolated_real_anchors")
    assert times["obs-g-3"] == (pytest.approx(14.4), "fallback_0.4s_anchored")
    none = replay_timestamps([_sample(0, group="h"), _sample(1, group="h")])
    assert none["obs-h-1"] == (pytest.approx(0.4), "fallback_0.4s_no_timestamp")


def _evidence(cell_id: int):
    from combatbot.vision.entity_models import EntityEvidence
    return EntityEvidence(cell_id, EntityKind.ENEMY, 0.9, 0.9, 0.9, 1.0, 1.0, 1.0, 0.0, BLUE_HUE, ("test",), None)


def test_unverified_track_labels_not_scored_as_tracking_truth() -> None:
    # Anciennes annotations : E1/E2 renumérotés image par image → identité non vérifiée.
    old = [_sample(i, float(i), group="old", confirmed=False, enemies=((10 + i, "E1"), (40, "E2"))) for i in range(3)]
    swapped = [(sample, {"t1" if i % 2 == 0 else "t2": 10 + i, "t3": 40}) for i, sample in enumerate(old)]
    metrics = _tracking_metrics({"old": swapped})
    assert metrics["status"] == "NOT_EVALUABLE" and metrics["excluded_unverified_frames"] == 3
    assert metrics["id_switches"] is None and metrics["false_reassociations"] is None
    # Mélange : seules les frames à identité confirmée comptent ; les anciennes n'ajoutent aucun switch.
    new = [_sample(i, float(i), group="new", enemies=((10, "E1"),)) for i in range(3)]
    mixed = _tracking_metrics({"old": swapped, "new": [(sample, {"t9": 10}) for sample in new]})
    assert mixed["status"] == "MEASURED" and mixed["frames"] == 3 and mixed["excluded_unverified_frames"] == 3
    assert mixed["id_switches"] == 0 and mixed["truth_observations"] == 3
    forged = replace(new[0], tracking_identity_source=None)
    assert _tracking_metrics({"x": [(forged, {"t1": 10})]})["status"] == "NOT_EVALUABLE"


def test_build_profiles_multi_example_on_corpus(tmp_path) -> None:
    from test_entity_corpus import build_corpus
    repository = build_corpus(tmp_path)
    samples = entity_inventory(repository)
    profiles, diagnostics = build_profiles(samples, CellEntityDetector())
    train = [sample for sample in samples if sample.split == "train"]
    assert isinstance(profiles.player, PlayerVisualProfileV2)
    assert diagnostics["player_profile"]["accepted"] == len(train)
    assert set(diagnostics["player_sources"]) <= {sample.observation_id for sample in train}
    assert json.dumps(diagnostics, default=str)
    report = run_entity_benchmark(repository, save_profiles=False, freeze=False)
    assert report["after"]["all"]["player"]["wrong"] == 0
