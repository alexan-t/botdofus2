"""LOT 3B-5E : annotation assistée — suggestion ≠ vérité, TEST aveugle, provenance."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from entity_fixtures import decide_samples
from test_entity_corpus import SEQUENCE, at, build_corpus
from combatbot.corpus.entity_suggestions import EntitySuggestion, review
from combatbot.corpus.models import Annotation
from combatbot.ui import entity_annotation_dialog as module


class FakeProvider:
    """Suggestion = vérité de la fixture, sauf erreurs injectées ; compte les appels (TEST aveugle)."""

    def __init__(self, repository, errors=None):
        self.repository = repository
        self.errors = errors or {}
        self.calls: list[str] = []

    def for_sequence(self, repository, sequence_id):
        self.calls.append(sequence_id)
        result = {}
        for entry in repository.tracking_sequence_entries(sequence_id):
            frame = SEQUENCE[entry.frame_index]
            enemies = tuple(self.errors.get(entry.frame_index, frame["enemies"]))
            result[entry.observation_id] = EntitySuggestion(
                frame["player"], enemies, frozenset(), {frame["player"]: 0.9},
                {"detector_version": "fake", "profile_generation": "g1"})
        return result


def _corpus(tmp_path, split: str | None, *, annotated: bool = False):
    repository = build_corpus(tmp_path, ("combat-a",))
    for entry in repository.list_entries():
        path = repository.resolve(entry.paths["observation"])
        document = json.loads(path.read_text(encoding="utf-8"))
        document["capture"]["entity_split_declared"] = split
        path.write_text(json.dumps(document), encoding="utf-8")
        if not annotated:
            # Frames jamais annotées : on retire la vérité posée par la fixture.
            (path.parent / "annotation.json").unlink()
    if not annotated:
        manifest = repository.load_manifest()
        from dataclasses import replace
        from combatbot.corpus.models import CorpusManifest
        repository.save_manifest(CorpusManifest(tuple(
            replace(e, paths={k: v for k, v in e.paths.items() if k != "annotation"}, annotation_available=False,
                    player_cell_id_truth=None, enemy_cells_truth=(), enemy_track_truth=None,
                    entity_annotation_source=None) for e in manifest.entries)))
    return repository


def _dialog(repository, provider, monkeypatch=None):
    QApplication.instance() or QApplication([])
    if monkeypatch is not None:
        monkeypatch.setattr(module.QMessageBox, "warning", lambda *args: None)
    return module.EntityAnnotationDialog(repository, suggestion_provider=provider)


@pytest.mark.parametrize("split", ["train", "validation"])
def test_assisted_annotation_prefills_prediction(tmp_path, split) -> None:
    repository = _corpus(tmp_path, split)
    provider = FakeProvider(repository)
    dialog = _dialog(repository, provider)
    frame = SEQUENCE[0]
    assert dialog.assist.isEnabled() and dialog.assist.isChecked()
    assert dialog.labels[frame["player"]] == "PLAYER"
    assert {dialog.labels[cell] for cell, _track in frame["enemies"]} == {"E1", "E2"}
    assert all(dialog.origin[cell] == "suggested" for cell in dialog.labels)
    assert dialog.accept_all_button.isEnabled()
    assert "LOGICIEL" in dialog.compare.text() and "non confirmée" in dialog.compare.text()
    dialog.close()


def test_assisted_annotation_prefills_train_prediction(tmp_path) -> None:
    test_assisted_annotation_prefills_prediction(tmp_path, "train")


def test_assisted_annotation_prefills_validation_prediction(tmp_path) -> None:
    test_assisted_annotation_prefills_prediction(tmp_path, "validation")


def test_test_split_never_reveals_prediction_before_confirmation(tmp_path, monkeypatch) -> None:
    repository = _corpus(tmp_path, "test")
    provider = FakeProvider(repository)
    dialog = _dialog(repository, provider, monkeypatch)
    assert provider.calls == []                          # aucune prédiction même calculée
    assert dialog.labels == {} and dialog.suggestion is None
    assert not dialog.assist.isEnabled() and not dialog.assist.isChecked()
    dialog.assist.setChecked(True)                       # impossible de contourner par la case
    assert provider.calls == [] and dialog.labels == {}
    assert not dialog.accept_all_button.isEnabled()
    assert "masquée" in dialog.compare.text() and dialog.compare_button.isHidden()
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Right, Qt.Key.Key_Left):
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent
        dialog.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
    assert provider.calls == []
    dialog.close()


def test_undeclared_split_is_treated_as_blind(tmp_path) -> None:
    repository = _corpus(tmp_path, None)
    provider = FakeProvider(repository)
    dialog = _dialog(repository, provider)
    assert provider.calls == [] and not dialog.assist.isEnabled()
    dialog.close()


def test_test_prediction_visible_after_truth_confirmation(tmp_path, monkeypatch) -> None:
    repository = _corpus(tmp_path, "test")
    provider = FakeProvider(repository, errors={0: [(at(2), "E1")]})   # E2 manqué par le logiciel
    dialog = _dialog(repository, provider, monkeypatch)
    entry = dialog.entries[0]
    frame = SEQUENCE[0]
    dialog._assign(frame["player"], "PLAYER")
    for cell, track in frame["enemies"]:
        dialog._assign(cell, track)
    decide_samples(dialog)
    dialog._save()
    assert provider.calls == []
    saved = repository.read_annotation(entry.observation_id)
    assert saved.annotation_mode == "manual_blind" and saved.suggestion_snapshot is None
    dialog._move(-1)
    assert not dialog.compare_button.isHidden()
    dialog._reveal_prediction()
    assert provider.calls and "LOGICIEL" in dialog.compare.text() and "ajouté" in dialog.compare.text()
    assert repository.read_annotation(entry.observation_id) == saved      # la vérité TEST n'est pas modifiée
    dialog.close()


def test_suggestion_does_not_become_truth_without_confirmation(tmp_path) -> None:
    repository = _corpus(tmp_path, "train")
    dialog = _dialog(repository, FakeProvider(repository))
    entry = dialog.entries[0]
    dialog._move(1)
    dialog._move(-1)
    dialog.close()
    assert repository.read_annotation(entry.observation_id) is None


def test_accept_all_marks_human_confirmed(tmp_path, monkeypatch) -> None:
    repository = _corpus(tmp_path, "train")
    dialog = _dialog(repository, FakeProvider(repository), monkeypatch)
    monkeypatch.setattr(dialog, "_ask_remaining_empty", lambda count: True)
    entry = dialog.entries[0]
    dialog._accept_all()
    saved = repository.read_annotation(entry.observation_id)
    assert saved.entities_confirmed and saved.entity_confirmed_by == "user"
    assert saved.annotation_mode == "assisted_confirmed"
    assert saved.player_cell_id_truth == SEQUENCE[0]["player"]
    assert saved.suggestion_snapshot["player_cell"] == SEQUENCE[0]["player"]
    assert saved.suggestion_snapshot["detector_version"] == "fake"
    assert saved.suggestion_review["player"] == "confirmed"
    assert all(item["label"] in ("EMPTY", "OCCUPIED") for item in saved.sampled_cells_truth)
    assert dialog.index == 1                              # passage automatique à la suivante
    dialog.close()


def test_corrected_suggestion_keeps_original_snapshot(tmp_path, monkeypatch) -> None:
    repository = _corpus(tmp_path, "train")
    wrong = [(at(3), "E1"), (at(0, 2), "E2")]              # E1 suggéré une case trop loin
    dialog = _dialog(repository, FakeProvider(repository, errors={0: wrong}), monkeypatch)
    entry = dialog.entries[0]
    dialog._assign(at(3), None)
    dialog._assign(at(2), "E1")
    decide_samples(dialog)
    dialog._save()
    saved = repository.read_annotation(entry.observation_id)
    assert saved.annotation_mode == "assisted_corrected"
    assert {"cell_id": at(3), "track_id": "E1"} in saved.suggestion_snapshot["enemies"]   # original conservé
    assert {"cell_id": at(2), "track_id": "E1"} in saved.enemy_cells_truth
    statuses = {row["status"] for row in saved.suggestion_review["enemies"]}
    assert statuses == {"corrected", "confirmed"}
    dialog.close()


def test_rejected_enemy_suggestion_is_recorded(tmp_path, monkeypatch) -> None:
    repository = _corpus(tmp_path, "train")
    ghost = [(at(2), "E1"), (at(0, 2), "E2"), (at(-4), "E3")]   # E3 fantôme
    dialog = _dialog(repository, FakeProvider(repository, errors={0: ghost}), monkeypatch)
    entry = dialog.entries[0]
    dialog.selected_cell = at(-4)
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    dialog.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
    assert at(-4) not in dialog.labels and "rejeté" in dialog.compare.text()
    decide_samples(dialog)
    dialog._save()
    rows = repository.read_annotation(entry.observation_id).suggestion_review["enemies"]
    assert {"suggested": [at(-4), "E3"], "final": None, "status": "rejected"} in rows
    dialog.close()


def test_assisted_navigation_preserves_edits(tmp_path) -> None:
    repository = _corpus(tmp_path, "train")
    dialog = _dialog(repository, FakeProvider(repository))
    dialog._assign(at(4), "E3")                           # ajout non confirmé
    dialog._move(1)
    dialog._move(-1)
    assert dialog.labels.get(at(4)) == "E3" and dialog.origin[at(4)] == "human"
    assert dialog.origin[SEQUENCE[0]["player"]] == "suggested"
    assert repository.read_annotation(dialog.entries[0].observation_id) is None   # brouillon, pas vérité
    dialog.close()


def test_tracking_ids_can_be_corrected_before_confirmation(tmp_path, monkeypatch) -> None:
    repository = _corpus(tmp_path, "train")
    swapped = [(at(2), "E2"), (at(0, 2), "E1")]            # identités inversées par le logiciel
    dialog = _dialog(repository, FakeProvider(repository, errors={0: swapped}), monkeypatch)
    entry = dialog.entries[0]
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    for cell, key in ((at(2), Qt.Key.Key_1), (at(0, 2), Qt.Key.Key_2)):
        dialog.selected_cell = cell
        dialog.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
    assert dialog.labels[at(2)] == "E1" and dialog.labels[at(0, 2)] == "E2"
    decide_samples(dialog)
    dialog._save()
    saved = repository.read_annotation(entry.observation_id)
    assert {(item["cell_id"], item["track_id"]) for item in saved.enemy_cells_truth} == {(at(2), "E1"), (at(0, 2), "E2")}
    assert saved.annotation_mode == "assisted_corrected"
    dialog.close()


def test_review_statuses_and_manual_mode() -> None:
    snapshot = {"player_cell": 10, "enemies": [{"cell_id": 20, "track_id": "E1"}]}
    assert review(snapshot, 10, [(20, "E1")])["mode"] == "assisted_confirmed"
    assert review(snapshot, None, [(20, "E1")])["player"] == "rejected"
    assert review({"player_cell": None, "enemies": []}, 10, [])["player"] == "added"
    assert review(None, 10, [])["mode"] == "manual"


def test_assisted_annotation_requires_snapshot() -> None:
    base = Annotation("obs", entity_annotation_source="human_confirmed", entity_confirmed_at="t",
                      player_cell_id_truth=10, player_visibility="VISIBLE", annotation_mode="assisted_confirmed")
    with pytest.raises(ValueError, match="instantané"):
        base.validate()
    with pytest.raises(ValueError, match="annotation_mode"):
        Annotation("obs", annotation_mode="auto").validate()
