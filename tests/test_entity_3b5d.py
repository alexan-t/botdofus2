"""LOT 3B-5D : échantillon EMPTY aveugle, splits déclarés par combat, verdicts et garde TEST."""
from __future__ import annotations

from dataclasses import replace
import json
import subprocess

import pytest
from PySide6.QtWidgets import QApplication

from entity_fixtures import decide_samples
from test_entity_corpus import at, build_corpus
from combatbot.corpus.empty_sampling import SAMPLE_VERSION, ZONE_NAMES, sample_cells
from combatbot.corpus.entity_benchmark import (
    _frame_metrics, _new_counts, _summaries, entity_inventory, run_entity_benchmark,
)
from combatbot.corpus.models import Annotation
from combatbot.corpus import validation_3b5d as v


def _declare(repository, splits: dict[str, str | None]) -> None:
    for entry in repository.list_entries():
        path = repository.resolve(entry.paths["observation"])
        document = json.loads(path.read_text(encoding="utf-8"))
        document["capture"]["entity_split_declared"] = splits.get(entry.session_id)
        path.write_text(json.dumps(document), encoding="utf-8")


def _cells(repository):
    from combatbot.ui.entity_annotation_dialog import projected_cells
    entry = repository.list_entries()[0]
    return entry, projected_cells(repository.read_observation(entry))


# ---------------------------------------------------------------------------- échantillon
def test_sample_is_deterministic_and_ignores_predictions(tmp_path) -> None:
    repository = build_corpus(tmp_path, ("combat-a",))
    entry, cells = _cells(repository)
    first = sample_cells(cells, entry.observation_id)
    assert first == sample_cells([dict(c, prediction="OCCUPIED") for c in cells], entry.observation_id)
    assert [item["zone"] for item in first] == list(ZONE_NAMES)   # une cellule par zone 3 × 3
    assert len({item["cell_id"] for item in first}) == 9
    assert first != sample_cells(cells, "autre-observation")
    assert len(sample_cells(cells, entry.observation_id, per_zone=2)) == 18


def test_sample_keeps_only_fully_visible_cells(tmp_path) -> None:
    repository = build_corpus(tmp_path, ("combat-a",))
    entry, cells = _cells(repository)
    width = int(max(max(p[0] for p in c["polygon"]) for c in cells) // 2)
    height = int(max(max(p[1] for p in c["polygon"]) for c in cells)) + 10
    chosen = {item["cell_id"] for item in sample_cells(cells, entry.observation_id, frame_size=(width, height))}
    visible = {c["cell_id"] for c in cells if all(p[0] < width for p in c["polygon"])}
    assert chosen and chosen <= visible


def test_sampled_truth_round_trip_and_validation() -> None:
    base = Annotation("obs", entity_annotation_source="human_confirmed", entity_confirmed_at="t",
                      player_cell_id_truth=10, player_visibility="VISIBLE")
    item = replace(base, sampled_cells_truth=({"cell_id": 11, "label": "EMPTY"},
                                              {"cell_id": 10, "label": "OCCUPIED"}),
                   sampled_cells_version=SAMPLE_VERSION)
    assert Annotation.from_dict(item.to_dict()) == item
    with pytest.raises(ValueError, match="VIDE ne peut pas"):
        replace(item, sampled_cells_truth=({"cell_id": 10, "label": "EMPTY"},)).validate()
    with pytest.raises(ValueError, match="invalide"):
        replace(item, sampled_cells_truth=({"cell_id": 11, "label": "FREE"},)).validate()
    with pytest.raises(ValueError, match="version"):
        replace(item, sampled_cells_version=None).validate()


def test_dialog_requires_a_decision_for_each_sampled_cell(tmp_path, monkeypatch) -> None:
    from combatbot.ui import entity_annotation_dialog as module
    QApplication.instance() or QApplication([])
    warnings = []
    monkeypatch.setattr(module.QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    repository = build_corpus(tmp_path, ("combat-a",))
    dialog = module.EntityAnnotationDialog(repository)
    entry = dialog.entries[0]
    assert len(dialog.sample) == 9
    dialog._clear()
    dialog._assign(at(-2), "PLAYER")
    dialog._save()
    assert warnings and "cellule(s) jaunes" in warnings[-1]
    assert not repository.read_annotation(entry).sampled_cells_truth    # rien d'enregistré
    sample = list(dialog.sample)
    first, second, *others = [cell for cell in sample if cell != at(-2)]
    dialog._assign(first, "SAMPLE_EMPTY")
    dialog._assign(second, "SAMPLE_OCCUPIED")
    decide_samples(dialog)
    dialog._save()                                  # enregistre puis passe à la frame suivante
    saved = repository.read_annotation(entry)
    labels = {item["cell_id"]: item["label"] for item in saved.sampled_cells_truth}
    assert saved.sampled_cells_version == SAMPLE_VERSION and set(labels) == set(sample)
    assert labels[first] == "EMPTY" and first in saved.empty_confirmed_cells
    assert labels[second] == "OCCUPIED" and all(labels[c] == "UNKNOWN" for c in others)
    if at(-2) in sample:
        assert labels[at(-2)] == "OCCUPIED"            # la cellule joueur annotée vaut OCCUPÉE
    dialog.close()


# ---------------------------------------------------------------------------- splits déclarés
def test_declared_split_is_authoritative_per_combat(tmp_path) -> None:
    repository = build_corpus(tmp_path)
    _declare(repository, {"combat-a": "validation", "combat-b": "test", "combat-c": None})
    samples = entity_inventory(repository, freeze=False, declared_only=True)
    splits = {s.session_id: s.split for s in samples}
    # La couverture de layout ne transforme jamais un combat VALIDATION déclaré en TRAIN.
    assert splits == {"combat-a": "validation", "combat-b": "test"}
    assert all(s.split_declared for s in samples)
    everything = {s.session_id for s in entity_inventory(repository, freeze=False)}
    assert everything == {"combat-a", "combat-b", "combat-c"}


def test_mixed_declaration_inside_one_combat_is_rejected(tmp_path) -> None:
    repository = build_corpus(tmp_path, ("combat-a",))
    _declare(repository, {"combat-a": "train"})
    entry = repository.list_entries()[-1]
    path = repository.resolve(entry.paths["observation"])
    document = json.loads(path.read_text(encoding="utf-8"))
    document["capture"]["entity_split_declared"] = "test"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="incohérent"):
        entity_inventory(repository, freeze=False)


def test_declared_split_conflicting_with_registry_is_rejected(tmp_path) -> None:
    repository = build_corpus(tmp_path, ("combat-a",))
    _declare(repository, {"combat-a": "train"})
    registry = {"schema_version": 2, "groups": {"combat-a|map42": "test"}}
    with pytest.raises(ValueError, match="registre"):
        entity_inventory(repository, freeze=False, registry_override=registry)


# ---------------------------------------------------------------------------- occupation
def test_occupancy_truth_counts_false_free_on_entities(tmp_path) -> None:
    repository = build_corpus(tmp_path, ("combat-a",))
    sample = entity_inventory(repository, freeze=False)[0]
    player, enemy = sample.player_cell, sample.enemies[0][0]
    sample = replace(sample, sampled_cells=((at(-4), "EMPTY"), (at(-3), "EMPTY"), (at(4), "OCCUPIED"),
                                            (at(3), "UNKNOWN")))
    counts = _new_counts()
    _frame_metrics(sample, player, [enemy], [], {at(-4): "FREE", at(-3): "UNKNOWN", at(4): "FREE",
                                                 player: "FREE", enemy: "OCCUPIED"}, counts)
    truth = _summaries(counts)["occupancy_truth"]
    assert truth["empty_truth"] == 2 and truth["free_correct"] == 1
    assert truth["false_free"] == 2 and truth["false_free_on_player_or_enemy"] == 1
    assert truth["free_precision"] == pytest.approx(1 / 3) and truth["free_coverage"] == 0.5
    assert truth["empty_predicted_unknown"] == 1 and truth["unknown_truth"] == 1
    assert v.occupancy_verdict(truth) == "FAIL"


# ---------------------------------------------------------------------------- verdicts
def test_verdicts_follow_the_pre_test_criteria() -> None:
    player = {"frames_visible": 10, "correct": 9, "wrong": 0, "false_positive": 0, "detection_recall": 0.9}
    assert v.player_verdict(player) == "PASS"
    assert v.player_verdict({**player, "correct": 8, "detection_recall": 0.8}) == "PARTIAL"
    assert v.player_verdict({**player, "wrong": 1}) == "FAIL"
    assert v.enemy_verdict({"truth": 50, "precision": 1.0, "recall": 0.92}) == "PASS"
    assert v.enemy_verdict({"truth": 50, "precision": 1.0, "recall": 0.8}) == "PARTIAL"
    assert v.enemy_verdict({"truth": 50, "precision": 0.95, "recall": 1.0}) == "FAIL"
    assert v.tracking_verdict({"status": "MEASURED", "switch_rate": 0.05, "false_reassociation_rate": 0.0}) == "PASS"
    assert v.tracking_verdict({"status": "MEASURED", "switch_rate": 0.1, "false_reassociation_rate": 0.0}) == "PARTIAL"
    small = {"false_free_on_player_or_enemy": 0, "empty_truth": 10, "free_precision": 1.0}
    assert v.occupancy_verdict(small) == "PARTIAL"
    assert v.occupancy_verdict({**small, "empty_truth": 60}) == "PASS"


def test_benchmark_reports_validation_scope_and_p95(tmp_path) -> None:
    repository = build_corpus(tmp_path)
    _declare(repository, {"combat-a": "train", "combat-b": "validation", "combat-c": "test"})
    report = run_entity_benchmark(repository, splits=("validation",), declared_only=True)
    assert set(report["after"]) == {"validation", "all"}
    assert "validation" in report["tracking_verified"]
    assert "p95" in report["performance_ms"]["detector_ms"]
    result = v.verdicts(report, "validation")
    assert result["OCCLUSION HANDLING"] == "NOT OBSERVED"   # identités de la fixture non revues en UI


# ---------------------------------------------------------------------------- garde TEST
def test_test_split_is_measured_only_once(tmp_path) -> None:
    repository = build_corpus(tmp_path, ("combat-a",))
    run = v.guard_test_run(repository, ["combat-a|map42"], "abc")
    assert run["kind"] == "independent_test"
    with pytest.raises(ValueError, match="déjà mesurés"):
        v.guard_test_run(repository, ["combat-a|map42"], "abc")
    assert v.guard_test_run(repository, ["combat-a|map42"], "abc", diagnostic_rerun=True)["kind"] == "diagnostic"
    assert v.guard_test_run(repository, ["combat-z|map1"], "abc")["kind"] == "independent_test"


def test_freeze_check_refuses_code_changed_after_freeze(tmp_path) -> None:
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    git("init", "-q")
    (tmp_path / "combatbot").mkdir()
    (tmp_path / "combatbot" / "a.py").write_text("x = 1\n", encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "gel")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True).stdout.strip()
    assert v.check_freeze(sha[:8], tmp_path) == sha
    (tmp_path / "combatbot" / "a.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="diffère du gel"):
        v.check_freeze(sha, tmp_path)
    with pytest.raises(ValueError, match="inconnu"):
        v.check_freeze("0" * 40, tmp_path)


def test_previous_frame_shows_what_was_just_saved(tmp_path) -> None:
    from combatbot.ui.entity_annotation_dialog import EntityAnnotationDialog
    QApplication.instance() or QApplication([])
    repository = build_corpus(tmp_path, ("combat-a",))
    dialog = EntityAnnotationDialog(repository)
    # Comme sur le PC de l'utilisateur : la fenêtre a été ouverte avant toute annotation.
    dialog.entries = [replace(e, paths={k: v for k, v in e.paths.items() if k != "annotation"},
                              annotation_available=False) for e in dialog.entries]
    dialog._clear()
    dialog._assign(at(-1), "PLAYER")
    dialog._assign(at(3), "E1")
    decide_samples(dialog)
    dialog._save()                      # enregistre puis passe à la frame suivante
    assert dialog.index == 1
    dialog._move(-1)                    # « Précédente »
    assert dialog.labels.get(at(-1)) == "PLAYER" and dialog.labels.get(at(3)) == "E1"
    assert all(dialog._sample_decision(cell) for cell in dialog.sample)
    dialog.close()


def test_sequence_capture_keeps_a_single_monster_move() -> None:
    """3B-5E : un seul monstre qui bouge doit déclencher l'enregistrement ; une frame identique non."""
    import numpy as np
    from combatbot.vision.combat_models import sequence_change
    frame = np.full((800, 1600, 3), 90, np.uint8)
    moved = frame.copy()
    moved[300:380, 700:760] = (40, 40, 200)            # petit sprite déplacé (0,4 % de l'écran)
    assert sequence_change(frame, frame.copy()) == 0.0
    assert sequence_change(frame, moved) >= 0.002
    assert float(np.abs(moved.astype(int) - frame.astype(int)).mean()) < 1.0   # l'ancienne règle l'ignorait
    hud_only = frame.copy()
    hud_only[750:800, :] = 255                          # HUD sous la zone de combat
    assert sequence_change(frame, hud_only, (0.0, 0.0, 1.0, 0.9)) == 0.0
