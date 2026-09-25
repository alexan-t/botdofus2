"""LOT 3B-5C-FIX : la confirmation de suivi appartient à la séquence, jamais perdue en silence."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from entity_fixtures import decide_samples
from test_entity_corpus import at, build_corpus
from combatbot.corpus import repository as repository_module
from combatbot.corpus.tracking_metrics import tracking_metrics
from combatbot.ui import entity_annotation_dialog as dialog_module


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(app, tmp_path, monkeypatch):
    monkeypatch.setattr(dialog_module.QMessageBox, "warning", lambda *args: None)
    repository = build_corpus(tmp_path, ("combat-a", "combat-b"))
    widget = dialog_module.EntityAnnotationDialog(repository)
    widget.questions = []

    def answer(choice):
        def ask():
            widget.questions.append(widget._current_sequence)
            return choice
        monkeypatch.setattr(widget, "_ask_unsaved_tracking", ask)
    widget.answer = answer
    answer("cancel")
    yield widget
    widget.close()


def _sequences(dialog) -> list[str]:
    return list(dict.fromkeys(dialog._sequence_ids.values()))


def _wait(dialog, app) -> None:
    deadline = time.monotonic() + 5
    while dialog._sequence_jobs is not None and dialog._sequence_jobs.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_tracking_checkbox_survives_frame_navigation_before_save(dialog) -> None:
    first = dialog._current_sequence
    assert not dialog.tracking_confirmed.isChecked()
    dialog.tracking_confirmed.setChecked(True)
    for step in (1, 1, -1, 1, -1, -1):
        dialog._move(step)
        assert dialog._current_sequence == first
        assert dialog.tracking_confirmed.isChecked()
        assert dialog.tracking_confirmation_dirty()
    assert "non enregistrée" in dialog.tracking_state.text()
    assert not dialog.repository.tracking_sequence_confirmed(first)
    assert dialog.questions == []  # navigation interne à la séquence : aucune question


def test_tracking_confirmation_is_sequence_scoped(dialog) -> None:
    first, second = _sequences(dialog)
    dialog.tracking_confirmed.setChecked(True)
    dialog.answer("save")
    dialog.sequence.setCurrentIndex(dialog.sequence.findData(second))
    assert dialog._current_sequence == second
    # La coche de la séquence A ne déborde pas sur B ; A seule est confirmée.
    assert not dialog.tracking_confirmed.isChecked()
    assert dialog.repository.tracking_sequence_confirmed(first)
    assert not dialog.repository.tracking_sequence_confirmed(second)
    entries = dialog.repository.tracking_sequence_entries(first)
    assert all(dialog.repository.read_annotation(e).tracking_sequence_id == first for e in entries)


def test_switch_sequence_warns_when_tracking_confirmation_dirty(dialog) -> None:
    first, second = _sequences(dialog)
    dialog.tracking_confirmed.setChecked(True)
    dialog.answer("cancel")
    dialog.sequence.setCurrentIndex(dialog.sequence.findData(second))
    assert dialog.questions == [first]
    assert dialog._current_sequence == first and dialog.sequence.currentData() == first
    assert dialog.tracking_confirmed.isChecked() and dialog.tracking_confirmation_dirty()
    # Passer d'une frame à l'autre à travers la frontière de séquence pose aussi la question.
    last = max(i for i, e in enumerate(dialog.entries) if dialog._sequence_ids[e.observation_id] == first)
    dialog.index = last
    dialog._move(1)
    assert dialog.questions == [first, first] and dialog._current_sequence == first
    dialog.answer("discard")
    dialog.sequence.setCurrentIndex(dialog.sequence.findData(second))
    assert dialog._current_sequence == second
    assert not dialog.repository.tracking_sequence_confirmed(first)
    assert first not in dialog._pending_tracking


def test_save_tracking_confirmation_clears_dirty_state(dialog, app) -> None:
    sequence = dialog._current_sequence
    dialog.tracking_confirmed.setChecked(True)
    dialog._move(1)
    assert dialog.tracking_confirmation_dirty()
    dialog._save_sequence()
    _wait(dialog, app)
    assert dialog.repository.tracking_sequence_confirmed(sequence)
    assert not dialog.tracking_confirmation_dirty() and sequence not in dialog._pending_tracking
    assert dialog.tracking_confirmed.isChecked()
    assert dialog.tracking_state.text() == "Confirmation de suivi enregistrée pour toute la séquence."
    dialog.answer("cancel")
    dialog.sequence.setCurrentIndex(dialog.sequence.findData(_sequences(dialog)[1]))
    assert dialog.questions == []  # rien de non enregistré : aucune question


def test_editing_enemy_id_invalidates_sequence_confirmation(dialog, app) -> None:
    sequence = dialog._current_sequence
    dialog.tracking_confirmed.setChecked(True)
    dialog._save_sequence()
    _wait(dialog, app)
    assert dialog.repository.tracking_sequence_confirmed(sequence)
    dialog._assign(at(3), "E1")
    assert not dialog.tracking_confirmed.isChecked()
    assert dialog_module.TRACKING_INVALIDATED in dialog.tracking_state.text()
    assert dialog.status.text() == dialog_module.TRACKING_INVALIDATED
    decide_samples(dialog)
    dialog._save()
    assert not dialog.repository.tracking_sequence_confirmed(sequence)
    assert all(not dialog.repository.read_annotation(e).tracking_identity_confirmed
               for e in dialog.repository.tracking_sequence_entries(sequence))


def test_failed_sequence_confirmation_rolls_back_all_annotations(tmp_path, monkeypatch) -> None:
    repository = build_corpus(tmp_path, ("combat-a",))
    sequence = repository.tracking_sequence_id(repository.list_entries()[0])
    entries = repository.tracking_sequence_entries(sequence)
    before = {e.observation_id: repository.resolve(e.paths["annotation"]).read_bytes() for e in entries}
    original = repository_module._write_json_atomic
    calls = []

    def failing(path, value):
        calls.append(path)
        if len(calls) == 3:
            raise OSError("disque plein simulé")
        original(path, value)

    monkeypatch.setattr(repository_module, "_write_json_atomic", failing)
    with pytest.raises(OSError):
        repository.confirm_tracking_sequence(sequence, confirmed=True)
    monkeypatch.setattr(repository_module, "_write_json_atomic", original)
    assert len(calls) == 3
    after = {e.observation_id: repository.resolve(e.paths["annotation"]).read_bytes() for e in entries}
    assert after == before  # aucune demi-séquence confirmée
    assert not repository.tracking_sequence_confirmed(sequence)


def test_old_unverified_sequence_remains_unverified(dialog, app) -> None:
    first, second = _sequences(dialog)
    dialog.tracking_confirmed.setChecked(True)
    dialog._save_sequence()
    _wait(dialog, app)
    repository = dialog.repository
    assert repository.tracking_sequence_confirmed(first)
    old = [repository.read_annotation(e) for e in repository.tracking_sequence_entries(second)]
    # Provenance ancienne conservée telle quelle, jamais promue en revue humaine de séquence.
    assert not repository.tracking_sequence_confirmed(second)
    assert all(a.tracking_identity_source != "human_ui_review" and a.tracking_sequence_id is None for a in old)
    frames = [{"group_id": sequence, "tracking_identity_confirmed": a.tracking_identity_confirmed,
               "tracking_identity_source": a.tracking_identity_source,
               "tracking_confirmed_at": a.tracking_confirmed_at, "tracking_sequence_id": a.tracking_sequence_id,
               "timestamp": float(index), "frame_index": index, "truth_identities": [], "tracked_entities": []}
              for sequence, annotations in ((first, [repository.read_annotation(e) for e in
                                                     repository.tracking_sequence_entries(first)]), (second, old))
              for index, a in enumerate(annotations)]
    metrics = tracking_metrics(frames)
    assert metrics["verified_sequences"] == 1 and metrics["excluded_frames"] == len(old)
