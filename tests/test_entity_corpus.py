"""LOT 3B-5 : vérité entités du corpus, banc --entities, séquence de suivi (fixtures synthétiques)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from entity_fixtures import BLUE, RED, decide_samples, draw_entity, ground, region, synthetic_grid
from combatbot.corpus.entity_benchmark import entity_inventory, grid_from_document, run_entity_benchmark
from combatbot.corpus.models import Annotation, CorpusEntry, CorpusManifest
from combatbot.corpus.repository import CorpusRepository
from combatbot.gamedata.models import GridCoordinate
from combatbot.gamedata.topology import grid_to_cell

CENTER = 287


def at(dx: int, dy: int = 0) -> int:
    from combatbot.gamedata.topology import cell_to_grid
    origin = cell_to_grid(CENTER)
    cell = grid_to_cell(GridCoordinate(origin.x + dx, origin.y + dy))
    assert cell is not None
    return int(cell)


# Séquence §50 : P + E1 + E2 ; E1 bouge ; E1/E2 voisins ; E2 occulté ; E2 réapparaît.
SEQUENCE = [
    {"player": at(-2), "enemies": [(at(2), "E1"), (at(0, 2), "E2")], "occluded": []},
    {"player": at(-2), "enemies": [(at(1), "E1"), (at(0, 2), "E2")], "occluded": []},
    {"player": at(-2), "enemies": [(at(0, 1), "E1"), (at(0, 2), "E2")], "occluded": []},
    {"player": at(-2), "enemies": [(at(0, 1), "E1")], "occluded": ["E2"]},
    {"player": at(-2), "enemies": [(at(0, 1), "E1"), (at(1, 2), "E2")], "occluded": []},
]


def snapshot(grid) -> dict:
    return {"grid_source": "GAMEDATA_PROJECTED", "map_id_declared": 42, "confidence": 1.0,
            "cells": [{"cell_id": c.cell_id, "center": list(c.center), "polygon": [list(p) for p in c.polygon],
                       "logical": {"x": c.logical.x, "y": c.logical.y},
                       "grid_coordinate": {"x": c.grid_coordinate.x, "y": c.grid_coordinate.y},
                       "walkable_static": True, "non_walkable_during_fight_static": False}
                      for c in grid.cells]}


def build_corpus(tmp_path: Path, sessions=("combat-a", "combat-b", "combat-c")) -> CorpusRepository:
    repository = CorpusRepository(tmp_path / "corpus")
    repository.ensure_layout()
    grid, shape = synthetic_grid(region(CENTER, 4))
    entries = []
    for session in sessions:
        for index, frame in enumerate(SEQUENCE):
            image = ground(shape, seed=index)
            draw_entity(image, grid, frame["player"], RED)
            for cell_id, _track in frame["enemies"]:
                draw_entity(image, grid, cell_id, BLUE)
            base = repository.sessions / session / f"obs-{session}-{index}"
            base.mkdir(parents=True)
            cv2.imwrite(str(base / "frame.png"), image)
            cv2.imwrite(str(base / "overlay.png"), image)
            document = {"schema_version": 1, "observation_id": base.name, "session_id": session,
                        "frame_index": index, "capture": {"grid_visibility_state": "VISIBLE",
                                                          "alignment_status": "ALIGNED",
                                                          "calibration": {"layout_signature": "layout-a"}},
                        "prediction": {"enemies": [{"cell": [0, 0]}] * 9}, "grid_snapshot": snapshot(grid)}
            (base / "observation.json").write_text(json.dumps(document), encoding="utf-8")
            relative = f"sessions/{session}/{base.name}"
            entries.append(CorpusEntry(base.name, session, index,
                                       {"frame": f"{relative}/frame.png", "overlay": f"{relative}/overlay.png",
                                        "observation": f"{relative}/observation.json"}))
    repository.save_manifest(CorpusManifest(tuple(entries)))
    for entry in entries:
        frame = SEQUENCE[entry.frame_index]
        repository.confirm_entities(entry.observation_id, player_cell_id=frame["player"], player_visible=True,
                                    enemies=frame["enemies"], occluded_tracks=frame["occluded"],
                                    frame_phase="tour_ennemi",
                                    # Fixture : identités construites comme suivies ; provenance explicite (§29).
                                    tracking_identity_source="synthetic_fixture_followed_identities")
    return repository


def test_entity_truth_round_trip_and_manifest(tmp_path) -> None:
    repository = build_corpus(tmp_path, ("combat-a",))
    entry = repository.list_entries()[0]
    annotation = repository.read_annotation(entry)
    assert annotation is not None and annotation.entities_confirmed
    assert annotation.player_cell_id_truth == SEQUENCE[0]["player"]
    assert entry.entity_annotation_source == "human_confirmed"
    assert entry.enemy_track_truth == {"E1": at(2), "E2": at(0, 2)}
    assert Annotation.from_dict(annotation.to_dict()) == annotation


def test_old_annotation_without_entities_still_loads() -> None:
    old = Annotation.from_dict({"schema_version": 1, "observation_id": "obs", "ap_truth": 12})
    assert old.entity_annotation_source is None and old.enemy_cells_truth == () and not old.entities_confirmed


@pytest.mark.parametrize("fields, message", [
    ({"player_cell_id_truth": 10, "enemy_cells_truth": ({"cell_id": 10, "track_id": "E1"},)}, "PLAYER et ENEMY"),
    ({"player_cell_id_truth": 10, "player_visibility": "NOT_VISIBLE"}, "non visible"),
    ({"enemy_cells_truth": ({"cell_id": 3, "track_id": "E1"}, {"cell_id": 4, "track_id": "E1"})}, "identifiant"),
    ({"enemy_cells_truth": ({"cell_id": 3, "track_id": None},), "empty_confirmed_cells": (3,)}, "vide confirmée"),
])
def test_invalid_entity_annotations_rejected(fields, message) -> None:
    with pytest.raises(ValueError, match=message):
        Annotation("obs", entity_annotation_source="human_confirmed", entity_confirmed_at="x", **fields).validate()


def test_entity_benchmark_before_after_and_tracking(tmp_path) -> None:
    repository = build_corpus(tmp_path)
    report = run_entity_benchmark(repository, save_profiles=False)
    assert report["frames"] == 15 and set(report["splits"]) == {"train", "validation", "test"}
    assert report["profiles"]["player_hues"] > 0 and report["profiles"]["enemy_hues"] > 0
    after = report["after"]["all"]
    assert after["player"]["cell_accuracy"] == 1.0 and after["player"]["false_positive"] == 0
    assert after["enemies"]["precision"] == 1.0 and after["enemies"]["recall"] == 1.0
    assert after["enemies"]["cell_accuracy"] == 1.0
    tracking = report["tracking_global"]
    assert tracking["id_switches"] == 0 and tracking["false_reassociations"] == 0
    assert tracking["occlusions_observed"] == 3 and tracking["occlusion_recovered"] == 3
    assert all("tracked_entities" in frame and "greedy_tracks" in frame for frame in report["frames_detail"])
    # BEFORE est mesuré sur les mêmes frames (le banc réel compare ; ici on vérifie sa présence).
    assert set(report["before"]["all"]) == {"player", "enemies", "cells", "occupancy_truth"}
    assert report["before"]["all"]["player"]["correct"] == 0  # aucune référence joueur dans le corpus


def test_test_split_never_builds_profiles(tmp_path) -> None:
    repository = build_corpus(tmp_path)
    samples = entity_inventory(repository)
    from combatbot.corpus.entity_benchmark import build_profiles
    from combatbot.vision.entity_detector import CellEntityDetector
    _profiles, diagnostics = build_profiles(samples, CellEntityDetector())
    train_frames = [sample for sample in samples if sample.split == "train"]
    assert diagnostics["player_source"] in {sample.observation_id for sample in train_frames}
    assert set(diagnostics["player_sources"]) <= {sample.observation_id for sample in train_frames}
    assert diagnostics["player_hues"] == len(train_frames)


def test_entity_split_is_frozen(tmp_path) -> None:
    repository = build_corpus(tmp_path)
    first = {sample.group_id: sample.split for sample in entity_inventory(repository)}
    again = {sample.group_id: sample.split for sample in entity_inventory(repository)}
    assert first == again
    registry = json.loads((repository.manifests / "entity_split_registry.json").read_text(encoding="utf-8"))
    assert registry["groups"] == first


def test_grid_from_document_keeps_static_fields(tmp_path) -> None:
    repository = build_corpus(tmp_path, ("combat-a",))
    grid = grid_from_document(repository.read_observation(repository.list_entries()[0]))
    assert grid is not None and all(cell.static_traversable for cell in grid.cells)


def test_annotation_dialog_saves_human_truth_without_prediction(tmp_path) -> None:
    from combatbot.ui.entity_annotation_dialog import EntityAnnotationDialog
    QApplication.instance() or QApplication([])
    repository = build_corpus(tmp_path, ("combat-a",))
    dialog = EntityAnnotationDialog(repository)
    assert "9" not in dialog.summary.text().split("Ennemis")[0]  # aucune prédiction reprise
    dialog._clear()
    dialog._assign(at(-1), "PLAYER")
    dialog._assign(at(3), "E1")
    dialog._assign(at(3, 1), "EMPTY")
    decide_samples(dialog)
    dialog._save()
    annotation = repository.read_annotation(repository.list_entries()[0].observation_id)
    assert annotation.player_cell_id_truth == at(-1)
    assert annotation.enemy_cells_truth == ({"cell_id": at(3), "track_id": "E1"},)
    assert annotation.empty_confirmed_cells == (at(3, 1),)
    dialog.close()


def test_general_annotation_keeps_entity_truth(tmp_path) -> None:
    from combatbot.ui.corpus_page import _carry_hud_provenance
    repository = build_corpus(tmp_path, ("combat-a",))
    entry = repository.list_entries()[0]
    previous = repository.read_annotation(entry)
    rewritten = _carry_hud_provenance(previous, Annotation(entry.observation_id, comments="note"))
    assert rewritten.enemy_cells_truth == previous.enemy_cells_truth and rewritten.entities_confirmed


def test_annotation_hidden_player_and_multiple_anonymous_enemies(tmp_path) -> None:
    from PySide6.QtCore import Qt
    from combatbot.ui.entity_annotation_dialog import EntityAnnotationDialog
    app = QApplication.instance() or QApplication([])
    repository = build_corpus(tmp_path, ("combat-a",))
    dialog = EntityAnnotationDialog(repository)
    dialog._clear()
    dialog._assign(at(0), "PLAYER")
    dialog.player_hidden.setChecked(True)
    dialog._assign(at(2), "ENEMY")
    dialog._assign(at(3), "ENEMY")
    dialog.tactical.setCheckState(Qt.CheckState.Unchecked)
    decide_samples(dialog)
    dialog._save()
    saved = repository.read_annotation(repository.list_entries()[0])
    assert saved.player_visibility == "NOT_VISIBLE" and saved.player_cell_id_truth is None
    assert {item["cell_id"] for item in saved.enemy_cells_truth} == {at(2), at(3)}
    assert all(item["track_id"] is None for item in saved.enemy_cells_truth)
    assert saved.tactical_mode is False
    dialog.close()


def test_visible_player_requires_cell_truth() -> None:
    with pytest.raises(ValueError, match="exactement une cellule"):
        Annotation("obs", entity_annotation_source="human_confirmed", entity_confirmed_at="x",
                   player_visibility="VISIBLE").validate()


def test_unlabelled_cells_never_count_as_confirmed_free(tmp_path) -> None:
    from dataclasses import replace
    from combatbot.corpus.entity_benchmark import _frame_metrics, _new_counts, _summaries
    repository = build_corpus(tmp_path, ("combat-a",))
    sample = replace(entity_inventory(repository)[0], empty_cells=(at(-1),))
    counts = _new_counts()
    _frame_metrics(sample, sample.player_cell, [sample.player_cell], [],
                   {at(-1): "FREE", at(4): "FREE", at(5): "OCCUPIED", sample.player_cell: "FREE"}, counts)
    metrics = _summaries(counts)
    assert metrics["cells"]["free_precision"] == 0.5
    assert metrics["cells"]["free_coverage"] == 1.0
    assert metrics["cells"]["free_unlabelled"] == 1
    assert metrics["cells"]["occupied_precision"] is None
    assert metrics["cells"]["occupied_unlabelled"] == 1
    assert metrics["enemies"]["false_positive"] == 1


def test_benchmark_profiles_do_not_mix_layouts(tmp_path) -> None:
    from dataclasses import replace
    from combatbot.corpus.entity_benchmark import build_profiles
    from combatbot.vision.entity_detector import CellEntityDetector
    repository = build_corpus(tmp_path, ("combat-a",))
    samples = [replace(sample, split="train") for sample in entity_inventory(repository)]
    with pytest.raises(ValueError, match="chaque layout"):
        build_profiles([samples[0], replace(samples[1], layout_signature="layout-b")], CellEntityDetector())
    profiles, _ = build_profiles(samples, CellEntityDetector())
    assert profiles.teams.layout_signature == "layout-a"
    assert not profiles.teams.compatible("layout-b")


def test_identity_review_ui_preserves_source_and_invalidates_changed_labels(tmp_path) -> None:
    import time
    from combatbot.ui.entity_annotation_dialog import EntityAnnotationDialog
    app = QApplication.instance() or QApplication([])
    repository = build_corpus(tmp_path, ("combat-a",))
    dialog = EntityAnnotationDialog(repository)
    entry = dialog.entries[0]
    sequence_id = repository.tracking_sequence_id(entry)
    assert not dialog.tracking_confirmed.isChecked()  # source fixture != revue UI de séquence
    dialog.tracking_confirmed.setChecked(True)
    dialog._save_sequence()
    deadline = time.monotonic() + 5
    while dialog._sequence_jobs.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert repository.tracking_sequence_confirmed(sequence_id)
    assert all(repository.read_annotation(e).tracking_identity_source == "human_ui_review"
               for e in dialog.entries)
    dialog._assign(at(3), "E1")
    assert not dialog.tracking_confirmed.isChecked()
    decide_samples(dialog)
    dialog._save()
    assert not repository.tracking_sequence_confirmed(sequence_id)
    assert all(not repository.read_annotation(e).tracking_identity_confirmed for e in dialog.entries)
    dialog.close()


def test_identity_review_checkbox_survives_navigation_and_warns_when_unchecked(tmp_path, monkeypatch) -> None:
    import time
    from combatbot.ui import entity_annotation_dialog as module
    app = QApplication.instance() or QApplication([])
    warnings = []
    monkeypatch.setattr(module.QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    repository = build_corpus(tmp_path, ("combat-a",))
    dialog = module.EntityAnnotationDialog(repository)
    sequence_id = repository.tracking_sequence_id(dialog.entries[0])
    # Case non cochée : le bouton prévient et n'écrit rien.
    dialog._save_sequence()
    assert warnings and "Cochez" in warnings[0] and dialog._sequence_jobs is None
    assert not repository.tracking_sequence_confirmed(sequence_id)
    # Cochée puis navigation dans la séquence : la case reste cochée jusqu'à l'enregistrement.
    dialog.tracking_confirmed.setChecked(True)
    dialog._move(1)
    dialog._move(1)
    assert dialog.tracking_confirmed.isChecked()
    dialog._save_sequence()
    deadline = time.monotonic() + 5
    while dialog._sequence_jobs.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert repository.tracking_sequence_confirmed(sequence_id)
    assert dialog.tracking_confirmed.isChecked() and sequence_id not in dialog._pending_tracking
    dialog.close()
