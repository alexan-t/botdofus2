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
    first, second, third = _combat(repository, "s1", 3)
    dialog = _dialog(repository)
    press = lambda key: dialog.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))
    press(Qt.Key.Key_2)                                  # phase hors combat : enregistrée + frame suivante
    assert dialog.index == 1 and repository.read_annotation(first.observation_id).combat_phase_truth == "PLACEMENT"
    press(Qt.Key.Key_3)                                  # Combat : attend le tour, rien n'est enregistré
    assert dialog.index == 1 and repository.read_annotation(second.observation_id) is None
    press(Qt.Key.Key_E)
    saved = repository.read_annotation(second.observation_id)
    assert dialog.index == 2 and (saved.combat_phase_truth, saved.turn_owner_truth) == ("FIGHTING", "OTHER")
    press(Qt.Key.Key_Return)                             # Entrée : même réponse que la frame précédente
    assert repository.read_annotation(third.observation_id).combat_state_mode == "carried_previous"
    press(Qt.Key.Key_Left)
    assert dialog.index == 1
    dialog.close()


def test_dialog_click_saves_directly(repository: CorpusRepository) -> None:
    """Retour utilisateur : cliquer sur une phase doit valider sans passer par Entrée."""
    first, second = _combat(repository, "s1", 2)
    dialog = _dialog(repository)
    dialog.phase_buttons[CombatPhase.OUT_OF_COMBAT].click()
    assert repository.read_annotation(first.observation_id).combat_phase_truth == "OUT_OF_COMBAT"
    assert dialog.index == 1
    dialog.phase_buttons[CombatPhase.FIGHTING].click()
    assert repository.read_annotation(second.observation_id) is None and dialog.turn_buttons[TurnOwner.PLAYER].isEnabled()
    dialog.turn_buttons[TurnOwner.PLAYER].click()
    saved = repository.read_annotation(second.observation_id)
    assert (saved.combat_phase_truth, saved.turn_owner_truth) == ("FIGHTING", "PLAYER")
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


# ------------------------------------------------------------------ détecteur (3B-6B)
from combatbot.vision.combat_state_detector import (
    CombatStateModel, SemanticCombatStateTracker, button_colours, extract_features, fit_model, turn_from_button,
)
from combatbot.vision.combat_state import SemanticCombatState


def _button(kind: str, two_lines: bool = True) -> np.ndarray:
    image = np.full((60, 160, 3), 30, np.uint8)
    if kind == "bright":
        image[8:52, 10:150] = (0, 235, 235)
    elif kind == "dim":
        image[8:52, 10:150] = (0, 120, 120)
    elif kind == "menu":
        image[:] = (40, 90, 160)
        return image
    rows = ((18, 26), (34, 42)) if two_lines else ((26, 34),)
    for top, bottom in rows:
        image[top:bottom, 40:120] = (20, 20, 20)
    return image


def _client(value: int) -> np.ndarray:
    image = np.full((90, 160, 3), value, np.uint8)
    image[10:30, 10:60] = 255 - value
    return image


def test_turn_comes_only_from_button_colour() -> None:
    assert turn_from_button(*button_colours(_button("bright"))) is TurnOwner.PLAYER
    assert turn_from_button(*button_colours(_button("dim"))) is TurnOwner.OTHER
    assert turn_from_button(*button_colours(_button("menu"))) is TurnOwner.UNKNOWN      # bouton masqué


def _model():
    samples = []
    for index in range(4):
        samples.append((extract_features(_button("bright", two_lines=False), _client(80)), "PLACEMENT", "a"))
        samples.append((extract_features(_button("bright"), _client(120)), "FIGHTING:PLAYER", "a"))
        samples.append((extract_features(_button("dim"), _client(120)), "FIGHTING:OTHER", "b"))
        samples.append((extract_features(_button("menu"), _client(200)), "OUT_OF_COMBAT", "b"))
    return fit_model(samples, provenance={"truth_source": "human_confirmed", "training_split": "train"})


def test_detector_phase_and_turn() -> None:
    model = _model()
    fighting = model.predict(extract_features(_button("bright"), _client(120)))
    assert (fighting.phase, fighting.turn_owner) == (CombatPhase.FIGHTING, TurnOwner.PLAYER)
    placement = model.predict(extract_features(_button("bright", two_lines=False), _client(80)))
    assert placement.phase is CombatPhase.PLACEMENT and placement.turn_owner is TurnOwner.UNKNOWN
    other = model.predict(extract_features(_button("dim"), _client(120)))
    assert other.turn_owner is TurnOwner.OTHER
    hidden = model.predict(extract_features(_button("menu"), _client(120)))
    assert hidden.turn_owner is not TurnOwner.PLAYER                      # jamais « mon tour » sans bouton


def test_detector_abstains_on_unseen_scene() -> None:
    model = _model()
    rng = np.random.default_rng(3)
    strange = model.predict(extract_features(rng.integers(0, 255, (60, 160, 3), dtype=np.uint8),
                                             rng.integers(0, 255, (90, 160, 3), dtype=np.uint8)))
    assert strange.phase is CombatPhase.UNKNOWN


def test_tracker_holds_phase_but_never_the_turn() -> None:
    tracker = SemanticCombatStateTracker(ttl=2.0)
    sure = SemanticCombatState(CombatPhase.FIGHTING, TurnOwner.PLAYER, 1.0)
    assert tracker.update(sure, 0.0).turn_owner is TurnOwner.PLAYER
    held = tracker.update(SemanticCombatState(), 1.0)
    assert held.phase is CombatPhase.FIGHTING and held.turn_owner is TurnOwner.UNKNOWN
    assert tracker.update(SemanticCombatState(), 5.0).phase is CombatPhase.UNKNOWN      # TTL dépassé


def test_tracker_needs_confirmation_for_low_confidence_phase_change() -> None:
    tracker = SemanticCombatStateTracker(confirm_frames=2, immediate_confidence=0.9)
    tracker.update(SemanticCombatState(CombatPhase.FIGHTING, TurnOwner.OTHER, 1.0), 0.0)
    once = tracker.update(SemanticCombatState(CombatPhase.RESULTS, TurnOwner.UNKNOWN, 0.5), 0.5)
    assert once.phase is CombatPhase.FIGHTING
    assert tracker.update(SemanticCombatState(CombatPhase.RESULTS, TurnOwner.UNKNOWN, 0.5), 1.0).phase is \
        CombatPhase.RESULTS


def test_model_load_requires_human_train_provenance(tmp_path: Path) -> None:
    model = _model()
    model.save(tmp_path / "ok")
    assert CombatStateModel.load(tmp_path / "ok") is not None
    bad = fit_model([(extract_features(_button("dim"), _client(1)), "FIGHTING:OTHER", "a")],
                    provenance={"truth_source": "human_confirmed", "training_split": "validation"})
    bad.save(tmp_path / "bad")
    assert CombatStateModel.load(tmp_path / "bad") is None


def test_runtime_model_learns_from_train_only(repository: CorpusRepository, tmp_path: Path) -> None:
    from combatbot.corpus.combat_state_benchmark import build_runtime_model
    (train,) = _combat(repository, "t", 1, "train")
    (validation,) = _combat(repository, "v", 1, "validation")
    repository.confirm_combat_state(train.observation_id, phase="FIGHTING", turn_owner="PLAYER")
    repository.confirm_combat_state(validation.observation_id, phase="RESULTS", turn_owner=None)
    summary = build_runtime_model(repository, tmp_path / "data")
    model = CombatStateModel.load(tmp_path / "data" / "combat_state_model")
    assert summary["train_frames"] == 1 and model.labels == ("FIGHTING:PLAYER",)


def test_leave_one_combat_out_never_sees_the_held_out_combat() -> None:
    from combatbot.corpus.combat_state_benchmark import leave_one_combat_out
    frames = []
    for session, label, button in (("a", "RESULTS", "menu"), ("b", "FIGHTING:OTHER", "dim")):
        phase, _, turn = label.partition(":")
        for index in range(3):
            frame = StateFrame(f"{session}{index}", session, index, "train", phase, turn or None)
            frames.append({"frame": frame, "label": label, "feature": extract_features(_button(button), _client(90)),
                           "time": float(index)})
    result = leave_one_combat_out(frames)
    # « RESULTS » n'existe que dans le combat a : laissé de côté, il ne peut jamais être prédit juste.
    assert result["phase_confusion"].get("RESULTS->RESULTS", 0) == 0
