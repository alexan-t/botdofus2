"""LOT 3B-4R2 : provenance des vérités PA/PM, revue humaine, split gelé et collecte en lecture seule."""

from __future__ import annotations

import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from combatbot.corpus.hud_collection import HUDCollectionSession
from combatbot.corpus.hud_dataset import (
    SPLIT_REGISTRY, _assign_splits, build_split_registry, ensure_split_registry, inventory,
)
from combatbot.corpus.models import Annotation, CorpusEntry, CorpusManifest
from combatbot.corpus.repository import CorpusRepository


def digit_crop(text: str) -> np.ndarray:
    image = np.zeros((58, 28 * len(text) + 10, 3), np.uint8)
    for index, digit in enumerate(text):
        cv2.putText(image, digit, (5 + 28 * index, 46), cv2.FONT_HERSHEY_SIMPLEX,
                    1.35, (255, 255, 255), 2, cv2.LINE_AA)
    return image


def collected(repository: CorpusRepository, values: list[tuple[str, str]]) -> HUDCollectionSession:
    session = HUDCollectionSession(repository, "hud-collect-test")
    frame = np.zeros((120, 200, 3), np.uint8)
    for ap, mp in values:
        session.capture(frame, digit_crop(ap), digit_crop(mp), {"client_size": [200, 120]}, context="combat")
    return session


@pytest.fixture
def repository(tmp_path: Path) -> CorpusRepository:
    return CorpusRepository(tmp_path / "corpus")


def test_unverified_import_is_not_a_final_truth(repository: CorpusRepository) -> None:
    session = collected(repository, [("12", "3")])
    observation_id = repository.list_entries()[0].observation_id
    # Mention historique non traçable : la valeur reste un import non vérifié.
    repository.save_annotation(Annotation(observation_id, ap_truth=12, mp_truth=3,
                                          comments="Vérité PA/PM confirmée par l'utilisateur"))
    assert session.saved == 1
    assert all(sample.truth is None for sample in inventory(repository))
    assert {sample.truth for sample in inventory(repository, require_human=False)} == {12, 3}
    assert {sample.truth_source for sample in inventory(repository)} == {"unverified_import"}


def test_confirmation_keeps_history_and_decisions(repository: CorpusRepository) -> None:
    collected(repository, [("12", "3")])
    observation_id = repository.list_entries()[0].observation_id
    repository.save_annotation(Annotation(observation_id, ap_truth=12, mp_truth=3))
    first = repository.confirm_hud_truth(observation_id, ap=12, mp=3, confirmed_at="2026-09-24T13:00:00+02:00")
    assert first.human_confirmed and first.hud_review == {"ap": "confirmed", "mp": "confirmed"}
    assert first.session_id == "hud-collect-test" and first.confirmed_by == "user"
    assert first.truth_history[-1]["truth_source"] == "unverified_import"
    corrected = repository.confirm_hud_truth(observation_id, ap=11, mp=3)
    assert corrected.ap_truth == 11 and corrected.hud_review == {"ap": "corrected", "mp": "confirmed"}
    assert corrected.truth_history[-1]["ap_truth"] == 12
    assert repository.read_annotation(observation_id) == corrected
    assert {sample.truth for sample in inventory(repository) if sample.kind == "AP"} == {11}


def test_unreadable_counter_has_no_truth(repository: CorpusRepository) -> None:
    collected(repository, [("12", "3")])
    observation_id = repository.list_entries()[0].observation_id
    annotation = repository.confirm_hud_truth(observation_id, ap=None, mp=3, ap_unreadable=True,
                                              ap_crop_quality="HUD_OCCLUDED")
    assert annotation.ap_truth is None and annotation.hud_review == {"ap": "unreadable", "mp": "entered"}
    with pytest.raises(ValueError, match="illisible"):
        Annotation(observation_id, ap_truth=4, truth_source="human_confirmed", confirmed_at="x",
                   hud_review={"ap": "unreadable"}).validate()


def test_human_confirmed_requires_timestamp() -> None:
    with pytest.raises(ValueError, match="confirmed_at"):
        Annotation("obs", ap_truth=4, truth_source="human_confirmed").validate()


def test_review_summary_counts_untreated(repository: CorpusRepository) -> None:
    collected(repository, [("12", "3"), ("7", "3"), ("5", "2")])
    first, second, _third = repository.list_entries()
    repository.confirm_hud_truth(first.observation_id, ap=12, mp=3)
    repository.confirm_hud_truth(second.observation_id, ap=None, mp=3, ap_unreadable=True)
    summary = repository.hud_review_summary()
    assert summary["observations"] == {"human_confirmed": 2, "untreated": 1}
    assert summary["counters"]["ap"] == {"confirmed": 0, "corrected": 0, "entered": 1,
                                         "unreadable": 1, "untreated": 1}


def test_duplicate_capture_is_ignored(repository: CorpusRepository) -> None:
    session = collected(repository, [("12", "3"), ("12", "3"), ("12", "3"), ("9", "3")])
    assert session.saved == 2 and session.duplicates == 2
    entries = repository.list_entries()
    assert [entry.frame_index for entry in entries] == [0, 1]
    document = repository.read_observation(entries[0])
    assert document["prediction"] == {} and document["capture"]["actions_sent"] is False
    assert document["capture"]["context"] == "combat"


def test_reopened_collection_keeps_temporal_continuity(repository: CorpusRepository) -> None:
    # Cas réel : la fenêtre de collecte rouverte crée une nouvelle session quelques secondes
    # plus tard ; le PM inchangé reste le même compteur, donc un seul groupe.
    frame = np.zeros((120, 200, 3), np.uint8)
    for session_id, ap in (("hud-collect-a", "7"), ("hud-collect-b", "5")):
        HUDCollectionSession(repository, session_id).capture(
            frame, digit_crop(ap), digit_crop("1"), {"client_size": [200, 120]}, context="combat")
    for entry, ap in zip(repository.list_entries(), (7, 5)):
        repository.confirm_hud_truth(entry.observation_id, ap=ap, mp=1)
    samples = inventory(repository)
    assert len({sample.group_id for sample in samples}) == 1
    assert len({sample.split for sample in samples}) == 1


def test_collection_turns_without_shared_counter_are_separate_groups(repository: CorpusRepository) -> None:
    # 7/6 puis 4/1 : les deux compteurs ont changé, puis 15/6 au tour suivant.
    collected(repository, [("7", "6"), ("4", "1"), ("15", "6")])
    for entry, (ap, mp) in zip(repository.list_entries(), ((7, 6), (4, 1), (15, 6))):
        repository.confirm_hud_truth(entry.observation_id, ap=ap, mp=mp)
    assert len({sample.group_id for sample in inventory(repository)}) == 3


def test_small_digit_change_is_not_a_duplicate(repository: CorpusRepository) -> None:
    # Cas réel : PA inchangé, PM 6 → 0 sur une grande icône ; écart moyen mesuré 1,94.
    frame = np.zeros((120, 200, 3), np.uint8)
    ap = np.full((63, 66, 3), 90, np.uint8)
    before = np.full((65, 66, 3), 90, np.uint8)
    after = before.copy()
    after[20:32, 30:38] = 255  # 96 pixels × 165 niveaux / 4290 pixels ≈ 3,7
    after[20:26, 30:38] = 90   # ramené à 48 pixels ≈ 1,85 d'écart moyen
    session = HUDCollectionSession(repository, "hud-collect-test")
    assert session.capture(frame, ap, before, {}, context="combat").saved
    outcome = session.capture(frame, ap, after, {}, context="combat")
    assert outcome.saved and 1.5 < (outcome.difference or 0) < 2.0


def test_truth_conflict_detects_identical_crops_with_different_values(repository: CorpusRepository) -> None:
    from combatbot.corpus.hud_dataset import truth_conflicts

    frame = np.zeros((120, 200, 3), np.uint8)
    for session_id in ("hud-collect-a", "hud-collect-b"):
        HUDCollectionSession(repository, session_id).capture(
            frame, digit_crop("7"), digit_crop("1"), {}, context="combat")
    first, second = repository.list_entries()
    repository.confirm_hud_truth(first.observation_id, ap=7, mp=1)
    repository.confirm_hud_truth(second.observation_id, ap=7, mp=6)  # saisie erronée
    conflicts = truth_conflicts(repository, inventory(repository))
    assert len(conflicts) == 1 and conflicts[0]["kind"] == "MP"


def test_frozen_test_member_survives_group_merge_after_correction(repository: CorpusRepository) -> None:
    # Cas réel : PM 6 corrigé en 1 relie 7/1 et 4/1 ; la capture 4/1 était gelée en TEST.
    collected(repository, [("7", "1"), ("4", "1")])
    first, second = repository.list_entries()
    repository.confirm_hud_truth(first.observation_id, ap=7, mp=6)
    repository.confirm_hud_truth(second.observation_id, ap=4, mp=1)
    registry = {"schema_version": 1, "frozen_test": [second.observation_id],
                "groups": {first.observation_id: "train", second.observation_id: "test"}}
    repository.manifests.mkdir(parents=True, exist_ok=True)
    (repository.manifests / SPLIT_REGISTRY).write_text(json.dumps(registry), encoding="utf-8")
    repository.confirm_hud_truth(first.observation_id, ap=7, mp=1)
    assert {sample.split for sample in inventory(repository)} == {"test"}
    rebuilt = build_split_registry(repository)
    assert set(rebuilt["groups"].values()) == {"test"} and rebuilt["missing_frozen_test"] == []


def test_digit_seen_in_single_group_is_learned_in_train() -> None:
    # Cas réel : un premier 8 observé ; il doit alimenter un template plutôt que TEST.
    groups = {"first-eight": {"8"}, **{f"g{i}": {"5"} for i in range(12)}}
    for fixed in ({}, {"g0": "test", "g1": "test"}):
        assert _assign_splits(groups, fixed)["first-eight"] == "train"


def test_frozen_test_group_never_moves() -> None:
    groups = {f"g{index}": {"5"} for index in range(8)}
    result = _assign_splits(groups, {"g0": "test", "g1": "test"})
    assert result["g0"] == result["g1"] == "test"


def test_rare_seven_is_stratified_into_train_when_test_is_frozen() -> None:
    groups = {"old-seven": {"7"}, "new-seven": {"7", "1"}, **{f"g{i}": {"5"} for i in range(6)}}
    result = _assign_splits(groups, {"old-seven": "test"})
    assert result["old-seven"] == "test" and result["new-seven"] == "train"


def test_registry_freezes_previous_test_groups(repository: CorpusRepository) -> None:
    values = [(12, 3), (7, 2), (5, 6)]
    collected(repository, [(str(ap), str(mp)) for ap, mp in values])
    frozen = repository.list_entries()[0].observation_id
    repository.manifests.mkdir(parents=True, exist_ok=True)
    (repository.manifests / "hud_manifest.json").write_text(json.dumps({"samples": [
        {"group_id": frozen, "split": "TEST"}]}), encoding="utf-8")
    registry = ensure_split_registry(repository)
    assert registry["frozen_test"] == [frozen]
    for entry, (ap, mp) in zip(repository.list_entries(), values):
        repository.confirm_hud_truth(entry.observation_id, ap=ap, mp=mp)
    rebuilt = build_split_registry(repository)
    assert rebuilt["groups"][frozen] == "test"
    assert (repository.manifests / SPLIT_REGISTRY).is_file()
    assert {sample.split for sample in inventory(repository) if sample.group_id == frozen} == {"test"}


def test_confirmation_does_not_recompute_registered_split(repository: CorpusRepository) -> None:
    collected(repository, [("12", "3")])
    entry = repository.list_entries()[0]
    registry = {"schema_version": 1, "frozen_test": [], "groups": {entry.observation_id: "validation"}}
    repository.manifests.mkdir(parents=True, exist_ok=True)
    (repository.manifests / SPLIT_REGISTRY).write_text(json.dumps(registry), encoding="utf-8")
    repository.confirm_hud_truth(entry.observation_id, ap=7, mp=3)
    assert {sample.split for sample in inventory(repository)} == {"validation"}
    assert all(sample.split_registered for sample in inventory(repository))


def test_review_dialog_never_shows_reader_prediction(repository: CorpusRepository) -> None:
    from combatbot.ui.hud_review_dialog import HUDReviewDialog

    QApplication.instance() or QApplication([])
    collected(repository, [("12", "3")])
    entry = repository.list_entries()[0]
    path = repository.resolve(entry.paths["observation"])
    document = json.loads(path.read_text(encoding="utf-8"))
    document["prediction"] = {"ap": 99, "mp": 98}
    path.write_text(json.dumps(document), encoding="utf-8")
    dialog = HUDReviewDialog(repository)
    # Les identifiants aléatoires peuvent contenir « 99 » : on vérifie les zones de valeur.
    for panel in (dialog.ap, dialog.mp):
        assert "99" not in panel.recorded.text() and "98" not in panel.recorded.text()
        assert panel.value.value() not in (99, 98)
    # Nouvelle capture : rien d'inscrit, la valeur doit être saisie par l'humain.
    assert dialog.ap.value.value() == -1 and dialog.ap.value.isEnabled()
    dialog.ap.value.setValue(12)
    dialog.mp.value.setValue(3)
    dialog._confirm()
    annotation = repository.read_annotation(entry.observation_id)
    assert annotation is not None and annotation.human_confirmed
    assert annotation.hud_review == {"ap": "entered", "mp": "entered"}
    dialog.close()


def test_existing_value_needs_correct_before_editing(repository: CorpusRepository) -> None:
    from combatbot.ui.hud_review_dialog import HUDReviewDialog

    QApplication.instance() or QApplication([])
    collected(repository, [("15", "6")])
    entry = repository.list_entries()[0]
    repository.save_annotation(Annotation(entry.observation_id, ap_truth=15, mp_truth=6))
    dialog = HUDReviewDialog(repository)
    assert not dialog.ap.value.isEnabled() and dialog.ap.value.value() == 15
    dialog._start_correction()
    dialog.ap.value.setValue(13)
    dialog._confirm()
    annotation = repository.read_annotation(entry.observation_id)
    assert annotation is not None and annotation.ap_truth == 13
    assert annotation.hud_review == {"ap": "corrected", "mp": "confirmed"}
    assert annotation.truth_history[-1]["ap_truth"] == 15
    dialog.close()


def test_collection_entry_stays_in_manifest_order(repository: CorpusRepository) -> None:
    collected(repository, [("12", "3")])
    entry = repository.list_entries()[0]
    assert isinstance(entry, CorpusEntry)
    assert CorpusManifest.from_dict(json.loads(repository.manifest_path.read_text(encoding="utf-8")))
