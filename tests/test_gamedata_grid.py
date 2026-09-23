"""LOT 3B-2 : topologie GameData projetée à l'écran. Fixtures générées par code."""
from dataclasses import replace
import json
import math
import random

import cv2
import numpy as np
import pytest

from combatbot.corpus.benchmark import run_benchmark
from combatbot.corpus.models import CorpusEntry, CorpusManifest
from combatbot.corpus.repository import CorpusRepository
from combatbot.gamedata.errors import GameDataError
from combatbot.gamedata.models import DofusCellId, GameMapCell, GridCoordinate, GridTopology
from combatbot.gamedata.topology import CELL_COUNT, cell_to_grid
from combatbot.models import Cell
from combatbot.vision.combat_grid import classify_cell_occupancy, detect_diamond_candidates
from combatbot.vision.combat_models import (
    GRID_SOURCE_GAMEDATA, GRID_SOURCE_LEGACY, GRID_SOURCE_VISION, CellVisualState, GridCalibration,
)
from combatbot.vision.combat_observer import OverlayOptions, RealCombatObserver, draw_diagnostic_overlay, save_debug_observation
from combatbot.vision.coordinates import ClientSize, CombatPoint, LayoutSignature, LayoutTransform, NormalizedRect, ScreenPoint
from combatbot.vision.gamedata_grid import (
    GRID_SOURCE_NONE, GameDataGridResolver, GameDataTopologySource, projected_observation,
)
from combatbot.vision.grid_fit import FitStatus, fit_from_anchors, fit_grid_from_candidates
from combatbot.vision.grid_profile import (
    CombatGridProfileV2, DeclaredMapId, ManualMapIdentity, MapIdOrigin, MapIdSource, ProjectionStatusCode,
)
from combatbot.vision.grid_projection import (
    GridOrientation, GridProjector, GridScreenTransform, Vector2, cell_to_client, cell_to_combat, cell_to_screen,
)
from combatbot.vision.models import Calibration, CapturedFrame, ClientRect, RelativeRect, ZoneEvidence

W, H = 54.0, 27.0
CROP = (820, 580)
TRANSFORM = GridScreenTransform.from_cell_size((W / 2 + 6, H / 2 + 5), W, H, reference_combat_size=(820.0, 580.0))


def make_topology(map_id=1, seed=1, walk_ratio=0.55, red=(), blue=(), los_blocked=(), fight_blocked=()):
    rng = random.Random(seed)
    cells = tuple(GameMapCell(
        DofusCellId(i), walkable=rng.random() < walk_ratio, line_of_sight=i not in los_blocked,
        non_walkable_during_fight=i in fight_blocked, red_hint=i in red, blue_hint=i in blue,
    ) for i in range(CELL_COUNT))
    return GridTopology(map_id, cells)


def render(topology, transform=TRANSFORM, size=CROP, *, draw=True):
    image = np.full((size[1], size[0], 3), 32, np.uint8)
    if draw:
        for cell in GridProjector(transform).project(topology).cells:
            if cell.static_traversable_in_fight:
                pts = np.array([p.rounded() for p in cell.polygon], np.int32)
                cv2.fillPoly(image, [pts], (60, 92, 70))
                cv2.polylines(image, [pts], True, (175, 175, 175), 1, cv2.LINE_AA)
    return image


class FakeProvider:
    def __init__(self, *topologies):
        self.maps = {t.map_id: t for t in topologies}
        self.calls = 0

    def get_map_topology(self, map_id):
        self.calls += 1
        if map_id not in self.maps:
            raise GameDataError("MAP_NOT_FOUND", f"Map ID {map_id} absent de l'index")
        return self.maps[map_id]


ZONES = {"combat": NormalizedRect(0.05, 0.05, 0.82, 0.725)}
CLIENT = ClientSize(1000, 800)


def profile(transform=TRANSFORM, *, confirmed=True, map_id=None):
    signature = LayoutSignature.create(CLIENT, ZONES).to_json()
    return CombatGridProfileV2(transform, signature, "manual", (), {}, map_id, confirmed_by_user=confirmed)


def resolver(*topologies, map_id=1, prof=None, allow=False, legacy=None):
    identity = ManualMapIdentity(map_id)
    source = GameDataTopologySource(FakeProvider(*topologies)) if topologies else None
    return GameDataGridResolver(profile=prof if prof is not None else profile(), topology_source=source,
                                map_identity=identity, legacy_calibration=legacy, allow_legacy_fallback=allow)


# --- Projection core -----------------------------------------------------------------

def test_project_560_gamedata_cells():
    grid = GridProjector(TRANSFORM).project(make_topology())
    assert len(grid.cells) == 560 and grid.cell_ids == frozenset(range(560))
    for cell in grid.cells:
        assert cell.grid_coordinate == cell_to_grid(int(cell.cell_id))
        assert cell.center == TRANSFORM.grid_to_combat(cell.grid_coordinate)
        assert len(cell.polygon) == 4
    # Theoretical footprint from the projection vectors, not from any contour.
    top, right, bottom, left = grid.cell(0).polygon
    assert (right.x - left.x, bottom.y - top.y) == (W, H)


def test_cell_id_identity_is_stable():
    topology = make_topology()
    first = GridProjector(TRANSFORM).project(topology)
    second = GridProjector(TRANSFORM).project(topology)
    assert [(c.cell_id, c.center) for c in first.cells] == [(c.cell_id, c.center) for c in second.cells]
    assert first.cell(14).grid_coordinate == GridCoordinate(1, 0)
    assert first.cell(0).center == CombatPoint(W / 2 + 6, H / 2 + 5)


def test_projection_without_any_contours():
    topology = make_topology()
    blank = np.full((CROP[1], CROP[0], 3), 90, np.uint8)
    assert detect_diamond_candidates(blank) == []
    grid = projected_observation(GridProjector(TRANSFORM).project(topology), blank)
    assert len(grid.cells) == 560 and {c.cell_id for c in grid.cells} == set(range(560))
    assert grid.grid_source == GRID_SOURCE_GAMEDATA
    assert grid.confidence == 0.0  # the evidence is weak, the topology is not gone


def _ids_and_centers(image, topology):
    grid = projected_observation(GridProjector(TRANSFORM).project(topology), image)
    return {c.cell_id for c in grid.cells}, {c.cell_id: c.center for c in grid.cells}, grid.confidence


def test_projection_with_missing_contours():
    topology = make_topology()
    image = render(topology)
    image[:, : CROP[0] // 2] = 32  # half of the outlines erased
    ids, centers, _ = _ids_and_centers(image, topology)
    reference_ids, reference_centers, _ = _ids_and_centers(render(topology), topology)
    assert ids == reference_ids == set(range(560)) and centers == reference_centers


def test_projection_with_false_contours():
    topology = make_topology()
    clean = render(topology)
    fake = clean.copy()
    for x, y in ((100, 90), (400, 300), (650, 470)):
        cv2.polylines(fake, [np.array([(x, y - 11), (x + 22, y), (x, y + 11), (x - 22, y)], np.int32)],
                      True, (220, 220, 220), 1)
    ids_a, centers_a, _ = _ids_and_centers(clean, topology)
    ids_c, centers_c, _ = _ids_and_centers(fake, topology)
    assert ids_a == ids_c == set(range(560)) and centers_a == centers_c


def test_projection_with_sprite_occlusion():
    topology = make_topology()
    occluded = render(topology)
    for x, y in ((200, 200), (500, 350)):
        cv2.ellipse(occluded, (x, y), (40, 70), 0, 0, 360, (20, 60, 200), -1)  # opaque "sprites"
    ids_a, centers_a, conf_a = _ids_and_centers(render(topology), topology)
    ids_b, centers_b, conf_b = _ids_and_centers(occluded, topology)
    assert ids_a == ids_b == set(range(560)) and centers_a == centers_b
    assert conf_b <= conf_a


def test_cell_to_combat_roundtrip_nearest():
    grid = GridProjector(TRANSFORM).project(make_topology())
    rng = random.Random(3)
    for cell_id in range(CELL_COUNT):
        center = cell_to_combat(cell_id, TRANSFORM)
        assert grid.pixel_to_cell(center) == cell_id
        jitter = (center.x + rng.uniform(-W / 5, W / 5), center.y + rng.uniform(-H / 5, H / 5))
        assert grid.pixel_to_cell(jitter) == cell_id
    assert grid.pixel_to_cell((-500, -500)) is None
    assert grid.pixel_to_cell((5000, 5000)) is None
    center = cell_to_combat(287, TRANSFORM)
    assert grid.pixel_to_cell((center.x + W * 0.45, center.y), max_distance=W * 0.25) is None


def test_cell_to_client_and_screen_composition():
    layout = LayoutTransform(ScreenPoint(100, 50), CLIENT, ZONES["combat"])
    combat = cell_to_combat(287, TRANSFORM)
    client = cell_to_client(287, TRANSFORM, layout)
    screen = cell_to_screen(287, TRANSFORM, layout)
    assert client == layout.combat_to_client(combat)
    assert screen == ScreenPoint(client.x + 100, client.y + 50)


# --- Map switch ---------------------------------------------------------------------

def test_map_switch_preserves_geometry():
    projector = GridProjector(TRANSFORM)
    a = projector.project(make_topology(1, seed=1))
    b = projector.project(make_topology(2, seed=2))
    assert [(c.cell_id, c.center, c.polygon) for c in a.cells] == [(c.cell_id, c.center, c.polygon) for c in b.cells]


def test_map_switch_changes_static_properties():
    a_topology = make_topology(1, seed=1, red=(10,), los_blocked=(5,))
    b_topology = make_topology(2, seed=2, blue=(10,))
    r = resolver(a_topology, b_topology, map_id=1)
    first = r.resolve(render(a_topology), CLIENT, ZONES)
    r.map_identity.declare(2)
    second = r.resolve(render(b_topology), CLIENT, ZONES)
    assert first.grid.map_id_declared == 1 and second.grid.map_id_declared == 2
    assert [c.center for c in first.grid.cells] == [c.center for c in second.grid.cells]
    assert [c.walkable_static for c in first.grid.cells] != [c.walkable_static for c in second.grid.cells]
    assert first.grid.cell_by_id(10).red_hint and not second.grid.cell_by_id(10).red_hint
    assert second.grid.cell_by_id(10).blue_hint and first.grid.cell_by_id(5).los_static is False


def test_unknown_map_rejected():
    r = resolver(make_topology(1), map_id=999)
    result = r.resolve(render(make_topology(1)), CLIENT, ZONES)
    assert result.source == GRID_SOURCE_NONE and not result.grid.cells
    assert "MAP_UNAVAILABLE" in result.reason and "999" in result.reason
    with pytest.raises(GameDataError) as caught:
        r.topology_source.topology(999)
    assert caught.value.code == "MAP_NOT_FOUND"


def test_manual_map_id_is_marked_declared():
    identity = ManualMapIdentity()
    assert identity.current_map() is None
    declared = identity.declare(123)
    assert declared.origin is MapIdOrigin.DECLARED_MANUALLY
    assert declared.label == "Map ID déclaré manuellement : 123 (non vérifié)"
    assert declared.source is MapIdSource.MANUAL_GUESS
    verified = identity.declare(124, MapIdSource.USER_VERIFIED_MAPID)
    assert verified.label.endswith("(vérifié par /mapid)") and verified.source.value == "user_verified_mapid"
    topology = make_topology(124)
    r = resolver(topology, map_id=124)
    r.map_identity.declare(124, MapIdSource.USER_VERIFIED_MAPID)
    assert r.resolve(render(topology), CLIENT, ZONES).grid.map_id_source == "user_verified_mapid"
    with pytest.raises(ValueError):
        DeclaredMapId(-1)
    with pytest.raises(ValueError):
        replace(profile(), map_id_origin=MapIdOrigin.DETECTED)


# --- Layout / profile ---------------------------------------------------------------

def test_incompatible_layout_invalidates_projection():
    prof = profile()
    ok = prof.status_for(CLIENT, ZONES)
    assert ok.code is ProjectionStatusCode.READY and ok.applicable and ok.transform == TRANSFORM
    ratio = prof.status_for(ClientSize(1200, 800), ZONES)
    assert ratio.code is ProjectionStatusCode.GRID_CALIBRATION_INCOMPATIBLE
    assert ratio.requires_recalibration and ratio.transform is None
    moved = prof.status_for(CLIENT, {"combat": NormalizedRect(0.1, 0.05, 0.8, 0.725)})
    assert moved.requires_recalibration
    topology = make_topology()
    result = resolver(topology).resolve(render(topology), ClientSize(1200, 800), ZONES)
    assert result.source == GRID_SOURCE_NONE and result.requires_recalibration
    assert result.grid.projection_status == "GRID_CALIBRATION_INCOMPATIBLE"


def test_uniform_client_scaling_scales_projection_explicitly():
    status = profile().status_for(ClientSize(1250, 1000), ZONES)
    assert status.code is ProjectionStatusCode.SCALED and status.applicable
    assert status.transform.cell_width == pytest.approx(W * 1.25)
    assert status.transform.origin.x == pytest.approx(TRANSFORM.origin.x * 1.25)


def test_unconfirmed_or_mirrored_profile_is_not_applied():
    assert profile(confirmed=False).status_for(CLIENT, ZONES).code is ProjectionStatusCode.NOT_CONFIRMED
    mirrored = GridScreenTransform(TRANSFORM.origin, Vector2(-W / 2, H / 2), Vector2(-W / 2, -H / 2))
    assert profile(mirrored).status_for(CLIENT, ZONES).code is ProjectionStatusCode.ORIENTATION_REJECTED


def test_profile_roundtrip_persists_transform_not_pixels():
    raw = profile(map_id=77).to_dict()
    assert "cells" not in raw and "centers" not in raw and len(json.dumps(raw)) < 2000
    loaded = CombatGridProfileV2.from_dict(json.loads(json.dumps(raw)))
    assert loaded.transform == TRANSFORM and loaded.map_id == 77 and loaded.confirmed_by_user
    assert loaded.topology_source == "gamedata" and loaded.scope == "layout" and loaded.schema_version == 2
    with pytest.raises(ValueError):
        CombatGridProfileV2.from_dict({**raw, "schema_version": 1})


# --- Static vs dynamic ----------------------------------------------------------------

def test_red_blue_remain_hints():
    topology = make_topology(red=(100, 101), blue=(300,))
    grid = projected_observation(GridProjector(TRANSFORM).project(topology), render(topology))
    assert grid.cell_by_id(100).red_hint and grid.cell_by_id(300).blue_hint
    assert not hasattr(grid.cell_by_id(100), "fight_start_allowed")
    assert all(c.state is CellVisualState.UNKNOWN for c in grid.cells)
    image = draw_diagnostic_overlay(render(topology), _observation(grid), OverlayOptions(red_blue=True))
    assert image.shape == (CROP[1], CROP[0], 3)


def test_walkable_does_not_mean_free():
    topology = make_topology(walk_ratio=1.0, fight_blocked=(3,))
    grid = projected_observation(GridProjector(TRANSFORM).project(topology), render(topology))
    cell = grid.cell_by_id(50)
    assert cell.walkable_static is True and cell.static_traversable is True
    assert cell.state is CellVisualState.UNKNOWN  # never FREE from static data
    assert grid.cell_by_id(3).static_traversable is False
    image = render(topology)
    center = cell.center
    cv2.circle(image, center, 7, (0, 0, 255), -1)  # an enemy marker on a walkable cell
    occupied, _player, _conf, enemies = classify_cell_occupancy(image, grid)
    after = occupied.cell_by_id(50)
    assert after.state is CellVisualState.OCCUPIED and after.walkable_static is True
    assert after.cell_id == 50 and any(e[0] == after.logical for e in enemies)


def test_static_los_separate_from_dynamic():
    topology = make_topology(walk_ratio=1.0, los_blocked=(7,))
    grid = projected_observation(GridProjector(TRANSFORM).project(topology), render(topology))
    assert grid.cell_by_id(7).los_static is False and grid.cell_by_id(8).los_static is True
    image = render(topology)
    cv2.circle(image, grid.cell_by_id(8).center, 7, (0, 0, 255), -1)
    occupied, *_ = classify_cell_occupancy(image, grid)
    # An entity makes the cell occupied; the static LOS stays the GameData value.
    assert occupied.cell_by_id(8).state is CellVisualState.OCCUPIED and occupied.cell_by_id(8).los_static is True


# --- Source priority --------------------------------------------------------------------

LEGACY = GridCalibration((280.0, 55.0), 76.0, 38.0, tuple(Cell(x, y) for y in range(4) for x in range(4)))


def test_legacy_grid_fallback():
    topology = make_topology()
    image = render(topology)
    no_profile = GameDataGridResolver(profile=None, topology_source=None, map_identity=None, legacy_calibration=LEGACY)
    assert no_profile.resolve(image, CLIENT, ZONES).source == GRID_SOURCE_LEGACY
    detected = GameDataGridResolver(profile=None, topology_source=None, map_identity=None)
    assert detected.resolve(image, CLIENT, ZONES).source == GRID_SOURCE_VISION
    refused = resolver(topology, map_id=None, legacy=LEGACY)
    assert refused.resolve(image, CLIENT, ZONES).source == GRID_SOURCE_NONE
    allowed = resolver(topology, map_id=None, legacy=LEGACY, allow=True)
    result = allowed.resolve(image, CLIENT, ZONES)
    assert result.source == GRID_SOURCE_LEGACY and result.reason == "NO_MAP_ID_DECLARED"


def test_gamedata_grid_has_priority():
    topology = make_topology()
    result = resolver(topology, legacy=LEGACY, allow=True).resolve(render(topology), CLIENT, ZONES)
    assert result.source == GRID_SOURCE_GAMEDATA and len(result.grid.cells) == 560
    assert result.grid.projection_confidence is not None and result.grid.projection_confidence > 0.5
    assert result.grid.transform == TRANSFORM.to_dict()


def test_contours_never_define_identity_with_gamedata():
    topology = make_topology()
    image = render(topology)
    cv2.rectangle(image, (0, 0), (300, 580), (32, 32, 32), -1)
    for x in range(40, 800, 90):  # dense false diamonds everywhere
        cv2.polylines(image, [np.array([(x, 540), (x + 20, 550), (x, 560), (x - 20, 550)], np.int32)], True, (255,) * 3, 1)
    grid = resolver(topology).resolve(image, CLIENT, ZONES).grid
    assert {c.cell_id for c in grid.cells} == set(range(560))
    assert grid.cell_by_id(0).center == TRANSFORM.grid_to_combat(GridCoordinate(0, 0)).rounded()


# --- Calibration ----------------------------------------------------------------------------

def test_auto_fit_recovers_transform_with_topology():
    topology = make_topology(seed=9)
    image = render(topology)
    fit = fit_grid_from_candidates(detect_diamond_candidates(image), CROP, topology=topology)
    assert fit.status is FitStatus.ACCEPTED and fit.orientation == "NORMAL" and fit.margin >= 0.08
    for cell_id in (0, 287, 559):
        a, b = cell_to_combat(cell_id, fit.transform), cell_to_combat(cell_id, TRANSFORM)
        assert math.hypot(a.x - b.x, a.y - b.y) < 1.5


def test_auto_fit_never_accepts_weak_or_ambiguous():
    topology = make_topology(seed=9)
    candidates = detect_diamond_candidates(render(topology))
    assert not fit_grid_from_candidates(candidates, CROP).accepted  # no map topology: symmetric lattice
    assert fit_grid_from_candidates(candidates[:4], CROP, topology=topology).status is FitStatus.INSUFFICIENT_CANDIDATES
    wrong_map = make_topology(seed=10)
    assert not fit_grid_from_candidates(candidates, CROP, topology=wrong_map).accepted


def test_auto_fit_flags_mirrored_scene():
    topology = make_topology(seed=12)
    mirrored = render(topology)[:, ::-1].copy()
    fit = fit_grid_from_candidates(detect_diamond_candidates(mirrored), CROP, topology=topology)
    assert fit.status is FitStatus.ORIENTATION_CONFLICT and not fit.accepted
    assert fit.orientation != GridOrientation.NORMAL.value


def test_anchor_fit_least_squares():
    anchors = [(cell, cell_to_combat(cell, TRANSFORM)) for cell in (0, 13, 300, 546)]
    fit = fit_from_anchors(anchors)
    assert fit.status is FitStatus.ACCEPTED and fit.residual_max_px < 1e-6
    assert tuple(fit.transform.grid_to_combat(GridCoordinate(5, 2))) == pytest.approx(
        tuple(TRANSFORM.grid_to_combat(GridCoordinate(5, 2))))
    collinear = [(cell, cell_to_combat(cell, TRANSFORM)) for cell in (0, 1, 2)]
    assert fit_from_anchors(collinear).status is FitStatus.INVALID_ANCHORS
    assert fit_from_anchors(anchors[:2]).status is FitStatus.INVALID_ANCHORS
    flipped = [(cell, (CROP[0] - p.x, p.y)) for cell, p in anchors]
    assert fit_from_anchors(flipped).status is FitStatus.ORIENTATION_CONFLICT


# --- Observer end-to-end, corpus, benchmark -----------------------------------------------------

def _observation(grid):
    from combatbot.vision.combat_models import CombatObservation
    return CombatObservation(True, 0.9, None, 0.0, None, 0.0, (), grid, None, None, 0.0, 0.0, 0.0)


def _frame(image_crop):
    client = np.full((CLIENT.height, CLIENT.width, 3), 24, np.uint8)
    x, y = round(0.05 * CLIENT.width), round(0.05 * CLIENT.height)
    client[y:y + image_crop.shape[0], x:x + image_crop.shape[1]] = image_crop
    return CapturedFrame(7, ClientRect(0, 0, CLIENT.width, CLIENT.height), client)


def _calibration():
    zones = {"combat": RelativeRect(0.05, 0.05, 0.82, 0.725)}
    return Calibration(1, CLIENT.width, CLIENT.height, zones, {"combat": ZoneEvidence(1.0, "test", "confirmée")})


def test_observer_uses_projected_grid_and_records_corpus_fields(tmp_path):
    topology = make_topology(walk_ratio=1.0)
    crop = render(topology)
    enemy = GridProjector(TRANSFORM).project(topology).cell(287).center.rounded()
    cv2.circle(crop, enemy, 7, (0, 0, 255), -1)
    frame = _frame(crop)
    observer = RealCombatObserver(7, _calibration(), frame_provider=lambda: frame,
                                  number_reader=lambda image: (None, 0.0), grid_resolver=resolver(topology))
    packet = observer.observe()
    grid = packet.observation.grid
    assert grid.grid_source == GRID_SOURCE_GAMEDATA and len(grid.cells) == 560
    assert any(e.cell_id == 287 for e in packet.observation.enemies)
    assert packet.metadata["grid_source"] == GRID_SOURCE_GAMEDATA and packet.metadata["map_id_declared"] == 1
    assert packet.metadata["map_id_origin"] == "DECLARED_MANUALLY"
    saved = json.loads(save_debug_observation(packet, tmp_path).read_text(encoding="utf-8"))
    snapshot = saved["grid_snapshot"]
    assert snapshot["grid_source"] == GRID_SOURCE_GAMEDATA and snapshot["cell_count"] == 560
    assert snapshot["map_id_declared"] == 1 and snapshot["grid_profile_version"] == 2
    assert snapshot["cells"][14]["cell_id"] == 14 and snapshot["cells"][14]["grid_coordinate"] == {"x": 1, "y": 0}

    repository = CorpusRepository(tmp_path / "corpus")
    repository.ensure_layout()
    first = repository.import_debug(tmp_path, session_id="s1", frame_index=0)
    second_packet = observer.observe()
    other = tmp_path / "second"
    other.mkdir()
    save_debug_observation(second_packet, other)
    repository.import_debug(other, session_id="s1", frame_index=1)
    report = run_benchmark(repository)
    assert report["grid"]["grid_source"] == {GRID_SOURCE_GAMEDATA: 2}
    assert report["grid"]["projected_cell_count_min"] == 560 == report["grid"]["projected_cell_count_max"]
    assert report["sessions"]["cell_identity_stability"] == 1.0
    assert report["sessions"]["projection_center_drift_px_max"] == 0.0
    assert first.observation_id


def test_old_corpus_still_loads(tmp_path):
    repository = CorpusRepository(tmp_path / "corpus")
    repository.ensure_layout()
    base = repository.sessions / "old" / "obs_old"
    base.mkdir(parents=True)
    cv2.imwrite(str(base / "frame.png"), np.zeros((10, 10, 3), np.uint8))
    cv2.imwrite(str(base / "overlay.png"), np.zeros((10, 10, 3), np.uint8))
    # A pre-3B-2 observation: no grid_source, no cell_id, legacy "logical" only.
    legacy = {"schema_version": 1, "observation_id": "obs_old", "prediction": {
        "grid": {"cells": [{"logical": {"x": 0, "y": 0}, "center": [5, 5]}], "confidence": 0.4},
        "player_cell": None, "enemies": []}}
    (base / "observation.json").write_text(json.dumps(legacy), encoding="utf-8")
    paths = {"frame": "sessions/old/obs_old/frame.png", "overlay": "sessions/old/obs_old/overlay.png",
             "observation": "sessions/old/obs_old/observation.json"}
    repository.save_manifest(CorpusManifest((CorpusEntry("obs_old", "old", 0, paths),)))
    report = run_benchmark(repository)
    assert report["corpus"]["readable_observations"] == 1 and not report["issues"]
    assert report["grid"]["grid_source"] == {"LEGACY_UNSPECIFIED": 1}
    assert report["grid"]["projected_cell_count_mean"] is None
    assert report["sessions"]["cell_identity_stability"] is None


def blob_topology(map_id, cx, cy, radius):
    """Contiguous walkable region, like real maps (random speckle would be unrealistic)."""
    return GridTopology(map_id, tuple(GameMapCell(
        DofusCellId(i), walkable=abs(cell_to_grid(i).x - cx) + abs(cell_to_grid(i).y - cy) <= radius,
        non_walkable_during_fight=False, line_of_sight=True) for i in range(CELL_COUNT)))


def test_stale_declared_map_is_flagged_not_redetected():
    # Real captures: 0.78 with the declared room, 0.51 after the player changed room.
    screen_topology = blob_topology(1, 17, -3, 11)
    image = render(screen_topology)
    right = resolver(screen_topology, map_id=1).resolve(image, CLIENT, ZONES).grid
    stale = resolver(blob_topology(1, 15, 0, 9), map_id=1).resolve(image, CLIENT, ZONES).grid
    assert right.topology_consistency > stale.topology_consistency
    assert not right.declared_map_suspect and stale.declared_map_suspect
    # The warning never changes the declared map nor the 560 identities.
    assert stale.map_id_declared == 1 and {c.cell_id for c in stale.cells} == set(range(560))
