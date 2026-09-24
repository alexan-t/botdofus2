"""LOT 3B-5 : détecteur d'entités par cellule projetée (fixtures synthétiques)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from entity_fixtures import BLUE, RED, draw_entity, draw_ring, draw_sprite, ground, region, synthetic_grid
from combatbot.gamedata.topology import neighbors
from combatbot.vision.background_model import BackgroundConfig, CellBackgroundModel
from combatbot.vision.entity_detector import CellEntityDetector, DetectionContext
from combatbot.vision.entity_models import (
    EntityKind, MarkerColorClass, PlayerVisualProfile, TeamMarkerProfile, VisualProfiles,
)
from combatbot.vision.entity_profiles import ProfileError, player_profile_from_cell

CENTER = 287
TRUSTED = DetectionContext(grid_visible=True, grid_aligned=True, map_id=1, layout_signature="layout-a")
RED_HUE = float(cv2.cvtColor(np.uint8([[RED]]), cv2.COLOR_BGR2HSV)[0, 0, 0])
BLUE_HUE = float(cv2.cvtColor(np.uint8([[BLUE]]), cv2.COLOR_BGR2HSV)[0, 0, 0])
TEAMS = TeamMarkerProfile(MarkerColorClass(RED_HUE, 10, 0, 0, 1), MarkerColorClass(BLUE_HUE, 10, 0, 0, 1),
                          layout_signature="layout-a")


@pytest.fixture
def scene():
    grid, shape = synthetic_grid(region(CENTER, 4))
    return grid, ground(shape)


def detect(image, grid, profiles=None, background=None, context=TRUSTED):
    return CellEntityDetector().detect(image, grid, profiles or VisualProfiles(None, TEAMS), context, background)


def test_empty_cell_is_not_enemy(scene) -> None:
    grid, image = scene
    result = detect(image, grid)
    assert result.entities == ()


def test_unknown_is_not_free(scene) -> None:
    grid, image = scene
    result = detect(image, grid)
    # Sans modèle de fond, rien ne prouve qu'une cellule est libre.
    assert set(result.occupancy.values()) == {"UNKNOWN"}


def test_enemy_marker_detected_on_projected_cell(scene) -> None:
    grid, image = scene
    draw_entity(image, grid, CENTER, BLUE)
    result = detect(image, grid)
    assert [(item.cell_id, item.kind) for item in result.entities] == [(CENTER, EntityKind.ENEMY)]
    assert result.occupancy[CENTER] == "OCCUPIED"
    evidence = result.entities[0]
    assert evidence.marker_score > 0.5 and evidence.reasons and 0 < evidence.confidence <= 1


def test_player_marker_detected_on_projected_cell(scene) -> None:
    grid, image = scene
    draw_entity(image, grid, CENTER, RED)
    profile = player_profile_from_cell(image, grid, CENTER, layout_signature="layout-a")
    assert profile.provenance == "human_confirmed" and profile.source_cell_id == CENTER
    result = detect(image, grid, VisualProfiles(profile, TEAMS))
    assert result.player is not None and result.player.cell_id == CENTER
    assert result.player.profile_score > 0


def test_ring_without_team_profile_stays_unknown(scene) -> None:
    grid, image = scene
    draw_entity(image, grid, CENTER, BLUE)
    result = CellEntityDetector().detect(image, grid, VisualProfiles(), TRUSTED)
    # Aucune couleur n'est supposée : sans profil humain, une présence n'a pas d'équipe.
    assert [(item.cell_id, item.kind) for item in result.entities] == [(CENTER, EntityKind.UNKNOWN)]


def test_sprite_color_without_ground_marker_not_enough(scene) -> None:
    grid, image = scene
    cell = grid.cell_by_id(CENTER)
    draw_sprite(image, cell, color=BLUE)
    cv2.circle(image, cell.center, 6, RED, -1)  # tache colorée au centre, sans anneau
    assert detect(image, grid).entities == ()


def test_placement_fill_is_not_an_entity(scene) -> None:
    grid, image = scene
    # Cas réel C003 : case de placement pleine, saturée, sans personnage.
    cell = grid.cell_by_id(CENTER)
    inset = np.asarray(cell.polygon, np.float64)
    inset = cell.center + (inset - cell.center) * 0.82
    cv2.fillPoly(image, [np.round(inset).astype(np.int32)], RED)
    assert detect(image, grid).entities == ()


def test_marker_on_neighbor_does_not_shift_cell_id(scene) -> None:
    grid, image = scene
    draw_entity(image, grid, CENTER, BLUE)
    result = detect(image, grid)
    assert {item.cell_id for item in result.entities} == {CENTER}
    for neighbour in neighbors(CENTER, corners=True):
        assert result.occupancy.get(int(neighbour)) != "OCCUPIED"


def test_occluded_marker_returns_unknown_not_wrong_cell(scene) -> None:
    grid, image = scene
    cell = grid.cell_by_id(CENTER)
    draw_sprite(image, cell)
    # Seul un petit arc de l'anneau reste visible : preuve insuffisante.
    cell_basis_points = np.asarray(cell.polygon, np.float64)
    right = cell_basis_points[1] - np.asarray(cell.center, np.float64)
    bottom = cell_basis_points[2] - np.asarray(cell.center, np.float64)
    angles = np.linspace(0.05, 0.35, 12)
    arc = np.stack([cell.center[0] + 0.52 * (np.cos(angles) * right[0] + np.sin(angles) * bottom[0]),
                    cell.center[1] + 0.52 * (np.cos(angles) * right[1] + np.sin(angles) * bottom[1])], axis=1)
    cv2.polylines(image, [np.round(arc).astype(np.int32)], False, BLUE, 3)
    result = detect(image, grid)
    assert result.entities == ()
    assert result.occupancy[CENTER] == "UNKNOWN"


def _trained_background(grid, image, frames: int, config=None):
    detector, model = CellEntityDetector(), CellBackgroundModel(config or BackgroundConfig())
    results = [detector.detect(image, grid, VisualProfiles(None, TEAMS), TRUSTED, model) for _ in range(frames)]
    return detector, model, results


def test_background_model_requires_multiple_frames(scene) -> None:
    grid, image = scene
    config = BackgroundConfig(min_frames=3, consecutive=2)
    _detector, _model, results = _trained_background(grid, image, 1, config)
    assert "FREE" not in results[-1].occupancy.values()
    _detector, _model, results = _trained_background(grid, image, 3 + 2, config)
    assert "FREE" not in results[2].occupancy.values()      # fond prêt, pas encore confirmé
    assert "FREE" in results[-1].occupancy.values()


def test_background_with_entity_not_learned(scene) -> None:
    grid, image = scene
    draw_entity(image, grid, CENTER, BLUE)
    _detector, model, results = _trained_background(grid, image, 6)
    assert not model.ready(CENTER)
    assert results[-1].occupancy[CENTER] == "OCCUPIED"
    assert any(state == "FREE" for cell, state in results[-1].occupancy.items() if cell != CENTER)


def test_background_needs_trusted_grid(scene) -> None:
    grid, image = scene
    detector, model = CellEntityDetector(), CellBackgroundModel()
    untrusted = DetectionContext(grid_visible=True, grid_aligned=False, map_id=1)
    for _ in range(6):
        result = detector.detect(image, grid, VisualProfiles(), untrusted, model)
    assert "FREE" not in result.occupancy.values()


def test_non_traversable_static_cell_not_marked_free() -> None:
    blocked = 287
    grid, shape = synthetic_grid(region(CENTER, 3), non_traversable={blocked})
    image = ground(shape)
    _detector, _model, results = _trained_background(grid, image, 6)
    assert blocked not in results[-1].occupancy  # jamais analysée : état statique GameData seulement
    assert grid.cell_by_id(blocked).static_traversable is False


def test_player_profile_requires_a_ground_ring(scene) -> None:
    grid, image = scene
    with pytest.raises(ProfileError):
        player_profile_from_cell(image, grid, CENTER, layout_signature="layout-a")


def test_player_profile_not_applied_to_incompatible_layout(scene) -> None:
    grid, image = scene
    draw_entity(image, grid, CENTER, RED)
    profile = player_profile_from_cell(image, grid, CENTER, layout_signature="layout-a")
    other = DetectionContext(grid_visible=True, grid_aligned=True, map_id=1, layout_signature="layout-b")
    result = CellEntityDetector().detect(image, grid, VisualProfiles(profile, None), other)
    assert result.player is None and result.diagnostics["player_profile"] == "incompatible_layout"


def test_player_profile_round_trip(scene, tmp_path) -> None:
    from combatbot.vision.entity_profiles import load_player_profile, save_player_profile
    grid, image = scene
    draw_entity(image, grid, CENTER, RED)
    profile = player_profile_from_cell(image, grid, CENTER, layout_signature="layout-a")
    save_player_profile(tmp_path, 3, profile)
    assert load_player_profile(tmp_path, 3) == profile
    with pytest.raises(ValueError):
        PlayerVisualProfile.from_dict({**profile.to_dict(), "provenance": "script"})


def test_detector_uses_small_rois_and_reports_timings(scene) -> None:
    grid, image = scene
    result = detect(image, grid)
    assert {"roi_maps", "features", "decision", "total"} <= set(result.timings_ms)
    assert result.diagnostics["cells_analysed"] == len(grid.cells)


@pytest.mark.parametrize("layout", [None, "layout-b"])
def test_team_and_player_profiles_require_matching_layout(scene, layout) -> None:
    grid, image = scene
    draw_entity(image, grid, CENTER, BLUE)
    profile = player_profile_from_cell(image, grid, CENTER, layout_signature="layout-a")
    context = DetectionContext(grid_visible=True, grid_aligned=True, layout_signature=layout)
    result = detect(image, grid, VisualProfiles(profile, TEAMS), context=context)
    assert result.player is None and not result.enemies
    assert result.entities[0].kind is EntityKind.UNKNOWN
    assert result.diagnostics["team_profile"] == "incompatible_layout"


def test_team_profile_rejects_unconfirmed_provenance() -> None:
    with pytest.raises(ValueError, match="confirmé"):
        TeamMarkerProfile.from_dict({**TEAMS.to_dict(), "provenance": "prediction"})
