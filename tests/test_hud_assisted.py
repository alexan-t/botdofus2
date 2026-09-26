"""LOT 3B-6A : revue HUD assistée, gabarits locaux TRAIN humains, TEST aveugle."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from test_hud_ground_truth import digit_crop
from combatbot.corpus.hud_collection import HUDCollectionSession
from combatbot.corpus.hud_dataset import inventory
from combatbot.corpus.hud_suggestions import HUDSuggestionProvider, build_local_templates
from combatbot.corpus.models import Annotation
from combatbot.corpus.repository import CorpusRepository
from combatbot.vision.hud_reader import GlyphTemplateLibrary, HUDReader


def _capture(repository: CorpusRepository, session: str, values: list[tuple[str, str]], split: str | None):
    collection = HUDCollectionSession(repository, session)
    frame = np.zeros((120, 200, 3), np.uint8)
    for ap, mp in values:
        collection.capture(frame, digit_crop(ap), digit_crop(mp), {"client_size": [200, 120]}, context="combat")
    for entry in repository.list_entries():
        if entry.session_id != session:
            continue
        path = repository.resolve(entry.paths["observation"])
        document = json.loads(path.read_text(encoding="utf-8"))
        document.setdefault("capture", {})["entity_split_declared"] = split
        path.write_text(json.dumps(document), encoding="utf-8")
    return [e for e in repository.list_entries() if e.session_id == session]


class FakeProvider:
    def __init__(self, data_root: Path, value_ap=15, value_mp=6) -> None:
        self.data_root = data_root
        self.values = {"AP": value_ap, "MP": value_mp}
        self.calls: list[str] = []
        self.template_count = 0

    def suggest(self, image, kind):
        self.calls.append(kind)
        return {"value": self.values[kind], "confidence": 0.74, "source": "RAPIDOCR_UNSURE", "accepted": False}

    def reload(self):
        pass


@pytest.fixture
def repository(tmp_path: Path) -> CorpusRepository:
    return CorpusRepository(tmp_path / "corpus")


def test_local_hud_templates_are_human_confirmed(repository: CorpusRepository, tmp_path: Path) -> None:
    confirmed, unverified = _capture(repository, "train-a", [("12", "3"), ("15", "6")], "train")
    repository.confirm_hud_truth(confirmed.observation_id, ap=12, mp=3)
    repository.save_annotation(Annotation(unverified.observation_id, ap_truth=15, mp_truth=6))   # import non vérifié
    summary = build_local_templates(repository, tmp_path / "data")
    assert summary["installed"] and summary["truth_source"] == "human_confirmed"
    assert set(summary["digits"]["AP"]) == {"1", "2"} and set(summary["digits"]["MP"]) == {"3"}
    library = GlyphTemplateLibrary.load(tmp_path / "data" / "hud_templates")
    assert set(library.digits("AP")) == {1, 2}
    assert json.loads((tmp_path / "data" / "hud_templates" / "provenance.json").read_text(encoding="utf-8"))[
        "training_split"] == "train"


def test_hud_validation_never_trains_templates(repository: CorpusRepository, tmp_path: Path) -> None:
    (train,) = _capture(repository, "train-a", [("12", "3")], "train")
    (validation,) = _capture(repository, "val-a", [("15", "6")], "validation")
    (test,) = _capture(repository, "test-a", [("9", "4")], "test")
    for entry, (ap, mp) in ((train, (12, 3)), (validation, (15, 6)), (test, (9, 4))):
        repository.confirm_hud_truth(entry.observation_id, ap=ap, mp=mp)
    splits = {s.observation_id: s.split for s in inventory(repository)}
    assert splits == {train.observation_id: "train", validation.observation_id: "validation",
                      test.observation_id: "test"}                     # split déclaré, jamais tiré au hasard
    summary = build_local_templates(repository, tmp_path / "data")
    assert set(summary["digits"]["AP"]) == {"1", "2"} and set(summary["digits"]["MP"]) == {"3"}


def test_hud_unknown_when_confidence_insufficient(tmp_path: Path) -> None:
    provider = HUDSuggestionProvider(tmp_path / "data", rapidocr=False)
    provider.reader = HUDReader(GlyphTemplateLibrary(), rapidocr_reader=lambda image, lo, hi: (15, 0.74),
                                rapidocr_interval=0.0)
    crop = digit_crop("15")
    assert provider.reader.read(crop, "AP").value is None               # seuil 0,94 inchangé : lecture refusée
    suggestion = provider.suggest(crop, "AP")
    assert suggestion["value"] == 15 and suggestion["accepted"] is False
    assert suggestion["source"] == "RAPIDOCR_UNSURE"
    assert provider.suggest(None, "AP")["value"] is None


def _dialog(repository, provider):
    from combatbot.ui.hud_review_dialog import HUDReviewDialog
    QApplication.instance() or QApplication([])
    return HUDReviewDialog(repository, suggestion_provider=provider)


def test_hud_review_prefills_train_suggestion_and_records_it(repository: CorpusRepository, tmp_path: Path) -> None:
    (entry,) = _capture(repository, "train-a", [("15", "6")], "train")
    provider = FakeProvider(tmp_path / "data")
    dialog = _dialog(repository, provider)
    assert dialog.ap.value.value() == 15 and dialog.mp.value.value() == 6
    assert "Suggestion" in dialog.ap.recorded.text()
    dialog._confirm()                                                   # « Tout est correct »
    saved = repository.read_annotation(entry.observation_id)
    assert saved.human_confirmed and (saved.ap_truth, saved.mp_truth) == (15, 6)
    assert saved.hud_suggestion["outcome"] == {"ap": "accepted", "mp": "accepted"}
    assert saved.hud_suggestion["ap"]["value"] == 15 and saved.hud_suggestion["ap"]["accepted"] is False
    dialog.close()


def test_hud_corrected_suggestion_keeps_original(repository: CorpusRepository, tmp_path: Path) -> None:
    (entry,) = _capture(repository, "train-a", [("12", "6")], "train")
    dialog = _dialog(repository, FakeProvider(tmp_path / "data"))
    dialog.ap.value.setValue(12)
    dialog._confirm()
    saved = repository.read_annotation(entry.observation_id)
    assert saved.ap_truth == 12 and saved.hud_suggestion["ap"]["value"] == 15
    assert saved.hud_suggestion["outcome"] == {"ap": "corrected", "mp": "accepted"}
    dialog.close()


def test_hud_temporal_consensus_does_not_create_truth(repository: CorpusRepository, tmp_path: Path) -> None:
    """Une suggestion affichée (même stable d'une frame à l'autre) n'est jamais une vérité sans clic."""
    first, _second = _capture(repository, "train-a", [("15", "6"), ("12", "6")], "train")
    dialog = _dialog(repository, FakeProvider(tmp_path / "data"))
    dialog._move(1)
    dialog._move(-1)
    dialog.close()
    annotation = repository.read_annotation(first.observation_id)
    assert annotation is None or not annotation.human_confirmed


def test_hud_test_split_is_blind(repository: CorpusRepository, tmp_path: Path) -> None:
    _capture(repository, "test-a", [("15", "6")], "test")
    provider = FakeProvider(tmp_path / "data")
    dialog = _dialog(repository, provider)
    assert dialog.split_filter.currentData() is None                    # aucun TRAIN à traiter
    assert provider.calls == []
    assert dialog.ap.value.value() == -1 and "Suggestion" not in dialog.ap.recorded.text()
    assert "aveugle" in dialog.header.text()
    dialog.close()
