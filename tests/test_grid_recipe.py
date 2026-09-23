"""LOT 3B-2R : outillage de recette réelle (fixtures synthétiques générées ici)."""
import json
import os

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from combatbot.gamedata.models import DofusCellId, GameMapCell, GridTopology
from combatbot.gamedata.topology import CELL_COUNT, cell_to_grid
from combatbot.vision.grid_fit import candidate_union, fit_grid_from_candidates
from combatbot.vision.grid_projection import GridProjector
from combatbot.vision.grid_recipe import (
    RealGridValidationSession, compare_sets, measure_capture, observed_placement_cells, score_transform, shifted,
    transform_drift,
)
from test_gamedata_grid import CROP, TRANSFORM, make_topology, render

EDGES_OK = {k: True for k in ("edge_top_ok", "edge_bottom_ok", "edge_left_ok", "edge_right_ok", "center_ok")}


def blob(map_id=1, red=(), blue=()):
    return GridTopology(map_id, tuple(GameMapCell(
        DofusCellId(i), walkable=abs(cell_to_grid(i).x - 17) + abs(cell_to_grid(i).y + 3) <= 11,
        non_walkable_during_fight=False, line_of_sight=True, red_hint=i in red, blue_hint=i in blue)
        for i in range(CELL_COUNT)))


def test_score_transform_reproduces_fit_score():
    topology = make_topology(seed=9)
    image = render(topology)
    fit = fit_grid_from_candidates(candidate_union(image), CROP, topology=topology)
    points = np.array(fit.inlier_points)
    assert score_transform(points, CROP, topology, fit.transform)["score"] == pytest.approx(fit.score, abs=1e-12)


def test_shift_scores_and_measures_on_confirmed_transform():
    topology = make_topology(seed=9)
    metrics, grid, candidates = measure_capture(render(topology), topology, TRANSFORM)
    assert metrics.integer_offset == [0, 0] and metrics.fit_status == "ACCEPTED"
    assert metrics.applied_score["score"] == pytest.approx(metrics.best_score)
    assert metrics.margin_vs_shift > 0 and set(metrics.shift_scores) == {"+U", "-U", "+V", "-V"}
    assert metrics.residual_median_px < 2 and metrics.automatic_flags["orientation_normal"]
    assert len(grid.cells) == 560 and candidates
    moved = shifted(TRANSFORM, 1, 0)
    assert transform_drift(moved, TRANSFORM)["origin_px"] == pytest.approx(TRANSFORM.basis_x.length)
    assert transform_drift(TRANSFORM, TRANSFORM)["center_max_px"] == 0


def test_measures_do_not_depend_on_user_verdict(tmp_path):
    topology = blob()
    session = RealGridValidationSession.create(tmp_path, {"client_size": {"width": 1, "height": 1}}, "s1")
    tid = session.add_transform(TRANSFORM, "test")
    image = render(topology)
    first = session.add_capture(map_id=1, map_id_source="user_verified_mapid", kind="D", transform_id=tid,
                                frame=image, combat_image=image, topology=topology, context={"mode": "placement"})
    session.set_verdict(1, alignment="incorrect", edges=EDGES_OK)
    second = session.add_capture(map_id=1, map_id_source="user_verified_mapid", kind="E", transform_id=tid,
                                 frame=image, combat_image=image, topology=topology, context={"mode": "combat"})
    drop = ("elapsed_ms",)
    assert {k: v for k, v in first["metrics"].items() if k not in drop} == \
        {k: v for k, v in second["metrics"].items() if k not in drop}


def test_session_roundtrip_and_map_status(tmp_path):
    topology = blob()
    session = RealGridValidationSession.create(tmp_path, {"client_size": {"width": 1, "height": 1}}, "s2")
    tid = session.add_transform(TRANSFORM, "test")
    image = render(topology)
    for kind, mode in (("A", "exploration"), ("D", "placement")):
        capture = session.add_capture(map_id=1, map_id_source="user_verified_mapid", kind=kind, transform_id=tid,
                                      frame=image, combat_image=image, topology=topology, context={"mode": mode})
        for name in ("frame", "combat", "overlay", "review"):
            assert (session.directory / capture["files"][name]).is_file()
    assert session.map_status(session.map_record(1))[0] == "NOT_JUDGED"
    session.set_verdict(1, alignment="correct", edges=EDGES_OK, transform_id=tid)
    loaded = RealGridValidationSession.load(tmp_path, "s2")
    assert loaded.map_status(loaded.map_record(1)) == ("PASS", [])
    bad_edges = dict(EDGES_OK, edge_left_ok=False)
    loaded.set_verdict(1, alignment="decale", edges=bad_edges, shift_direction="+U")
    status, reasons = loaded.map_status(loaded.map_record(1))
    assert status == "FAIL" and any("edge_left_ok" in r for r in reasons)
    loaded.set_verdict(1, alignment="ambigu", edges=EDGES_OK)
    assert loaded.map_status(loaded.map_record(1))[0] == "AMBIGUOUS"
    with pytest.raises(ValueError):
        loaded.set_verdict(1, alignment="bof", edges=EDGES_OK)
    with pytest.raises(ValueError):
        loaded.declare_map(1, "detected")


def test_exploration_only_map_is_visual_only_and_guess_is_not_pass(tmp_path):
    topology = blob()
    session = RealGridValidationSession.create(tmp_path, {}, "s3")
    tid = session.add_transform(TRANSFORM, "test")
    image = render(topology)
    session.declare_map(1, "user_verified_mapid")
    session.add_capture(map_id=1, map_id_source="user_verified_mapid", kind="A", transform_id=tid,
                        frame=image, combat_image=image, topology=topology, context={"mode": "exploration"})
    session.set_verdict(1, alignment="correct", edges=EDGES_OK)
    assert session.map_status(session.map_record(1))[0] == "VISUAL_ONLY"
    session.declare_map(2, "manual_guess")
    session.add_capture(map_id=2, map_id_source="manual_guess", kind="D", transform_id=tid,
                        frame=image, combat_image=image, topology=topology, context={"mode": "placement"})
    session.set_verdict(2, alignment="correct", edges=EDGES_OK)
    status, reasons = session.map_status(session.map_record(2))
    assert status == "FAIL" and "map ID non vérifié par /mapid" in reasons


def test_stale_reuse_and_baseline(tmp_path):
    a, b = blob(1), make_topology(2, seed=5)
    session = RealGridValidationSession.create(tmp_path, {}, "s4")
    tid = session.add_transform(TRANSFORM, "test")
    image = render(a)
    good = session.add_capture(map_id=1, map_id_source="user_verified_mapid", kind="D", transform_id=tid,
                               frame=image, combat_image=image, topology=a, context={"mode": "placement"})
    stale = session.add_capture(map_id=2, map_id_source="user_verified_mapid", kind="D", transform_id=tid,
                                frame=image, combat_image=image, topology=b, stale=True, context={"mode": "placement"})
    session.add_stale_test(old_map_id=2, new_map_id=1, before_capture=stale["capture_id"], after_capture=good["capture_id"])
    t2 = session.add_transform(shifted(TRANSFORM, 0, 0).translated(2, 0), "recalibrated")
    session.record_reuse(1, tid, reused_exactly=False, recalibrated_transform_id=t2)
    session.set_verdict(1, alignment="correct", edges=EDGES_OK, transform_id=tid)
    baseline = session.baseline()
    assert baseline["stale_captures"] == 1 and baseline["live_captures"] == 1
    assert baseline["topology_consistency_stale"]["max"] < baseline["topology_consistency_pass"]["min"]
    assert baseline["transform_reuse"]["recalibrated"] == 1
    assert baseline["transform_reuse"]["recalibration_center_drift_px"]["max"] == pytest.approx(2.0)
    json.dumps(baseline, default=str)


def test_red_blue_observed_cells_and_comparison():
    topology = blob(red=(300, 301), blue=(400,))
    grid_cells = GridProjector(TRANSFORM).project(topology)
    image = render(topology)
    for cell_id, colour in ((300, (0, 0, 230)), (301, (0, 0, 230)), (400, (230, 60, 0))):
        cell = grid_cells.cell(cell_id)
        cv2.fillPoly(image, [np.array([p.rounded() for p in cell.polygon], np.int32)], colour)
    from combatbot.vision.gamedata_grid import projected_observation
    observed = observed_placement_cells(image, projected_observation(grid_cells, image))
    assert observed == {"red": [300, 301], "blue": [400]}
    comparison = compare_sets({300, 301, 302}, {300, 301, 999})
    assert comparison["precision"] == pytest.approx(2 / 3) and comparison["false_negative"] == [302]
    assert comparison["false_positive"] == [999]


def test_recipe_dialog_records_verdict_and_red_blue(tmp_path):
    from PySide6.QtWidgets import QApplication
    from combatbot.ui.grid_recipe_dialog import GridRecipeDialog
    app = QApplication.instance() or QApplication([])
    topology = blob(red=(300,), blue=(400,))
    session = RealGridValidationSession.create(tmp_path, {}, "s5")
    tid = session.add_transform(TRANSFORM, "test")
    image = render(topology)
    capture = session.add_capture(map_id=1, map_id_source="user_verified_mapid", kind="D", transform_id=tid,
                                  frame=image, combat_image=image, topology=topology, red_blue=True,
                                  context={"mode": "placement"})
    dialog = GridRecipeDialog(session, capture["capture_id"], topology)
    center = GridProjector(TRANSFORM).project(topology).cell(287).center
    dialog._hovered(center.x, center.y)
    assert dialog.hover.text().startswith("Cellule 287")
    dialog.alignment.setCurrentText("correct")
    for box in dialog.edges.values():
        box.setChecked(True)
    dialog.red_match.setCurrentText("exact")
    dialog.blue_match.setCurrentText("exact")
    dialog.real_red.setText("300")
    dialog.real_blue.setText("400")
    dialog.save()
    loaded = RealGridValidationSession.load(tmp_path, "s5")
    assert loaded.map_status(loaded.map_record(1))[0] == "PASS"
    assert loaded.red_blue[0]["red_vs_user"]["precision"] is not None
    dialog.close()
    app.processEvents()
