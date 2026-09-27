"""LOT 3B-6B : vérité phase/tour, capture des crops d'état, outil d'annotation, mesures."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from combatbot.corpus.combat_state_metrics import StateFrame, distribution, evaluate, truth_frames
from combatbot.corpus.models import Annotation
from combatbot.corpus.repository import CorpusRepository
from combatbot.vision.combat_models import CombatGridObservation, CombatObservation, ObservationPacket
from combatbot.vision.combat_state import CombatPhase, SemanticCombatState, TurnOwner, validate_truth


def _packet(session: str, index: int, split: str | None) -> ObservationPacket:
    observation = CombatObservation(False, 0.0, None, 0.0, None, 0.0, (), CombatGridObservation(),
                                    None, None, 0.0, 0.0, 0.0)
    combat = np.full((60, 80, 3), 40, np.uint8)
    client = np.full((120, 200, 3), 90, np.uint8)
    crops = {"ap": np.zeros((10, 10, 3), np.uint8), "mp": np.zeros((10, 10, 3), np.uint8),
             "end_turn": np.full((12, 30, 3), 200, np.uint8), "spell_bar": np.full((10, 60, 3), 120, np.uint8),
             "client": client}
    return ObservationPacket(observation, combat, combat, 1.0,
                             {"session_id": session, "frame_index": index, "entity_split_declared": split}, crops)


@pytest.fixture(autouse=True)
def _no_modal(monkeypatch):
    """Une QMessageBox modale bloquerait la suite de tests."""
    from combatbot.ui import combat_state_dialog
    for name in ("information", "warning"):
        monkeypatch.setattr(combat_state_dialog.QMessageBox, name, staticmethod(lambda *args, **kwargs: None))


@pytest.fixture
def repository(tmp_path: Path) -> CorpusRepository:
    return CorpusRepository(tmp_path / "corpus")


def _combat(repository: CorpusRepository, session: str, count: int, split: str | None = "train"):
    return [repository.import_packet(_packet(session, index, split)) for index in range(count)]


def test_truth_rules() -> None:
    validate_truth("FIGHTING", "PLAYER")
    validate_truth("PLACEMENT", None)
    validate_truth(None, None)
    with pytest.raises(ValueError):
        validate_truth("FIGHTING", None)                 # en combat, le tour est obligatoire (UNKNOWN permis)
    with pytest.raises(ValueError):
        validate_truth("PLACEMENT", "PLAYER")            # pas de tour hors combat
    with pytest.raises(ValueError):
        validate_truth("BOSS", None)
    assert SemanticCombatState(CombatPhase.FIGHTING, TurnOwner.OTHER).label == "Combat · Tour d'un autre"


def test_annotation_round_trip_and_validation() -> None:
    annotation = Annotation("obs_1", combat_phase_truth="FIGHTING", turn_owner_truth="OTHER",
                            combat_state_source="human_confirmed", combat_state_confirmed_at="2026-09-27T10:00:00+02:00",
                            combat_state_mode="carried_previous")
    assert Annotation.from_dict(annotation.to_dict()) == annotation
    with pytest.raises(ValueError):
        Annotation("obs_1", combat_phase_truth="RESULTS", turn_owner_truth="PLAYER").validate()
    with pytest.raises(ValueError):          # une annotation assistée garde la suggestion d'origine
        Annotation("obs_1", combat_phase_truth="RESULTS", combat_state_mode="assisted_confirmed").validate()


def test_capture_keeps_end_turn_spell_bar_and_client(repository: CorpusRepository) -> None:
    (entry,) = _combat(repository, "s1", 1)
    for key in ("ap_crop", "mp_crop", "end_turn_crop", "spell_bar_crop", "client_frame"):
        assert repository.resolve(entry.paths[key]).is_file(), key
    assert entry.paths["client_frame"].endswith(".jpg")
    document = repository.read_observation(entry)
    assert document["capture"]["entity_split_declared"] == "train"


def test_confirm_combat_state_keeps_hud_truth(repository: CorpusRepository) -> None:
    (entry,) = _combat(repository, "s1", 1)
    repository.confirm_hud_truth(entry.observation_id, ap=11, mp=3)
    saved = repository.confirm_combat_state(entry.observation_id, phase="FIGHTING", turn_owner="PLAYER")
    again = repository.read_annotation(entry.observation_id)
    assert again == saved and again.combat_state_confirmed
    assert (again.ap_truth, again.mp_truth) == (11, 3) and again.human_confirmed


def test_test_split_refuses_software_suggestion(repository: CorpusRepository) -> None:
    (entry,) = _combat(repository, "t1", 1, "test")
    with pytest.raises(ValueError):
        repository.confirm_combat_state(entry.observation_id, phase="RESULTS", turn_owner=None,
                                        mode="assisted_confirmed", suggestion={"phase": "RESULTS"})
    repository.confirm_combat_state(entry.observation_id, phase="RESULTS", turn_owner=None, mode="manual_blind")


def _dialog(repository):
    from combatbot.ui.combat_state_dialog import CombatStateDialog
    QApplication.instance() or QApplication([])
    return CombatStateDialog(repository)


def test_dialog_carries_previous_human_truth_only(repository: CorpusRepository) -> None:
    first, second, third = _combat(repository, "s1", 3)
    dialog = _dialog(repository)
    assert dialog.index == 0 and dialog.phase is None                  # rien de prérempli sans vérité humaine
    dialog._set_turn(TurnOwner.PLAYER)                                  # M : implique la phase COMBAT
    assert dialog.phase is CombatPhase.FIGHTING
    dialog._confirm()
    assert dialog.index == 1 and dialog.phase is CombatPhase.FIGHTING and dialog.turn is TurnOwner.PLAYER
    assert repository.read_annotation(second.observation_id) is None    # le préremplissage n'est pas une vérité
    dialog._confirm()                                                   # Entrée : toujours vrai
    saved = repository.read_annotation(second.observation_id)
    assert saved.combat_state_mode == "carried_previous" and saved.turn_owner_truth == "PLAYER"
    dialog._set_phase(CombatPhase.RESULTS)
    assert dialog.turn is None and not dialog.turn_buttons[TurnOwner.PLAYER].isEnabled()
    dialog._confirm()
    third_saved = repository.read_annotation(third.observation_id)
    assert (third_saved.combat_phase_truth, third_saved.turn_owner_truth, third_saved.combat_state_mode) == (
        "RESULTS", None, "manual")
    assert repository.read_annotation(first.observation_id).combat_state_mode == "manual"
    dialog.close()


def test_dialog_requires_turn_in_combat_and_is_blind_on_test(repository: CorpusRepository, monkeypatch) -> None:
    from combatbot.ui import combat_state_dialog
    messages = []
    monkeypatch.setattr(combat_state_dialog.QMessageBox, "information", lambda *args: messages.append(args[2]))
    (entry,) = _combat(repository, "t1", 1, "test")
    dialog = _dialog(repository)
    assert "TEST aveugle" in dialog.header.text()
    dialog._set_phase(CombatPhase.FIGHTING)
    dialog._confirm()
    assert messages and repository.read_annotation(entry.observation_id) is None
    dialog._set_turn(TurnOwner.OTHER)
    dialog._confirm()
    assert repository.read_annotation(entry.observation_id).combat_state_mode == "manual_blind"
    dialog.close()


def test_dialog_keyboard_shortcuts(repository: CorpusRepository) -> None:
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    _combat(repository, "s1", 2)
    dialog = _dialog(repository)
    press = lambda key: dialog.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
    press(Qt.Key.Key_2)
    assert dialog.phase is CombatPhase.PLACEMENT
    press(Qt.Key.Key_E)
    assert dialog.phase is CombatPhase.FIGHTING and dialog.turn is TurnOwner.OTHER
    press(Qt.Key.Key_Return)
    assert dialog.index == 1
    press(Qt.Key.Key_Left)
    assert dialog.index == 0
    dialog.close()


def test_dialog_without_new_captures_explains(repository: CorpusRepository) -> None:
    repository.ensure_layout()
    with pytest.raises(ValueError, match="Vision réelle"):
        _dialog(repository)


def _frames(values):
    return [StateFrame(f"o{index}", "s", index, "validation", phase, turn)
            for index, (phase, turn) in enumerate(values)]


def test_metrics_abstention_is_not_correct_and_dangerous_errors_counted() -> None:
    truth = _frames([("PLACEMENT", None), ("FIGHTING", "PLAYER"), ("FIGHTING", "PLAYER"),
                     ("FIGHTING", "OTHER"), ("RESULTS", None)])
    predictions = {"o0": ("PLACEMENT", None), "o1": ("FIGHTING", "PLAYER"), "o2": ("UNKNOWN", None),
                   "o3": ("FIGHTING", "PLAYER")}                    # o4 absent = UNKNOWN
    result = evaluate(truth, predictions)
    assert result["phase"]["correct"] == 3 and result["phase"]["abstained"] == 2 and result["phase"]["wrong"] == 0
    assert result["phase"]["precision"] == 1.0 and result["phase"]["coverage"] == pytest.approx(0.6)
    assert result["turn"]["dangerous_claimed_my_turn"] == 1 and result["turn"]["abstained"] == 1
    assert result["turn_confusion"]["OTHER->PLAYER"] == 1
    # Transitions vraies : PLACEMENT→PLAYER, PLAYER→OTHER, OTHER→RESULTS ; seule la 1ʳᵉ est retrouvée.
    assert result["transitions"]["truth"] == 3 and result["transitions"]["found"] == 1


def test_truth_frames_and_distribution(repository: CorpusRepository) -> None:
    train = _combat(repository, "s1", 2)
    (validation,) = _combat(repository, "v1", 1, "validation")
    repository.confirm_combat_state(train[0].observation_id, phase="PLACEMENT", turn_owner=None)
    repository.confirm_combat_state(validation.observation_id, phase="FIGHTING", turn_owner="OTHER")
    assert [f.observation_id for f in truth_frames(repository, ("validation",))] == [validation.observation_id]
    assert distribution(truth_frames(repository)) == {"train": {"PLACEMENT": 1},
                                                      "validation": {"FIGHTING:OTHER": 1}}


def test_combat_state_tooling_never_imports_action_executor() -> None:
    import combatbot.corpus.combat_state_metrics as metrics
    import combatbot.ui.combat_state_dialog as dialog
    import combatbot.vision.combat_state as state
    for module in (metrics, dialog, state):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "ActionExecutor" not in source and "pyautogui" not in source and ".execute(" not in source
