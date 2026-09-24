from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from combatbot.corpus.hud_dataset import (
    HUDSample, _classification_metrics, _group_splits, build_templates, inventory,
    one_seven_verdict, run_hud_benchmark, write_manifest,
)
from combatbot.corpus.models import Annotation, CorpusEntry, CorpusManifest
from combatbot.corpus.repository import CorpusRepository


def confirmed(observation_id: str, **values) -> Annotation:
    """Vérité PA/PM confirmée par un humain : seule forme comptée par le banc HUD."""
    return Annotation(observation_id, truth_source="human_confirmed",
                      confirmed_at="2026-09-24T12:00:00+02:00", confirmed_by="user", **values)


def crop(text: str) -> np.ndarray:
    image = np.zeros((58, 28 * len(text) + 10, 3), np.uint8)
    for index, digit in enumerate(text):
        cv2.putText(image, digit, (5 + 28 * index, 46), cv2.FONT_HERSHEY_SIMPLEX,
                    1.35, (255, 255, 255), 2, cv2.LINE_AA)
    return image


def repository_with_entry(tmp_path: Path, *, usage: str = "diagnostic") -> tuple[CorpusRepository, CorpusEntry]:
    repository = CorpusRepository(tmp_path / "corpus")
    base = repository.root / "sessions" / "burst-a" / "obs-a"
    (base / "hud").mkdir(parents=True)
    cv2.imwrite(str(base / "hud" / "ap_original.png"), crop("12"))
    cv2.imwrite(str(base / "hud" / "mp_original.png"), crop("3"))
    document = {
        "observation_id": "obs-a",
        "capture": {"client_size": [800, 600], "map_id_declared": 42,
                    "calibration": {"layout_signature": "layout-a"}},
        # Une prédiction ne doit jamais devenir une vérité terrain.
        "prediction": {"ap": 12, "mp": 3},
    }
    (base / "observation.json").write_text(json.dumps(document), encoding="utf-8")
    entry = CorpusEntry(
        "obs-a", "burst-a", 0,
        {"observation": "sessions/burst-a/obs-a/observation.json",
         "ap_crop": "sessions/burst-a/obs-a/hud/ap_original.png",
         "mp_crop": "sessions/burst-a/obs-a/hud/mp_original.png"},
        usage=usage,  # type: ignore[arg-type]
    )
    repository.save_manifest(CorpusManifest((entry,)))
    return repository, entry


def test_hud_inventory_never_uses_prediction_as_truth(tmp_path: Path) -> None:
    repository, _entry = repository_with_entry(tmp_path)
    samples = inventory(repository)
    assert len(samples) == 2
    assert all(sample.truth is None for sample in samples)
    assert {sample.kind for sample in samples} == {"AP", "MP"}


def test_hud_inventory_keeps_metadata_and_session_split(tmp_path: Path) -> None:
    repository, entry = repository_with_entry(tmp_path)
    repository.save_annotation(confirmed(entry.observation_id, ap_truth=12, mp_truth=3))
    samples = inventory(repository)
    assert len({sample.split for sample in samples}) == 1
    assert samples[0].map_id == 42 and samples[0].client_size == (800, 600)
    assert samples[0].layout_signature == "layout-a"
    manifest = write_manifest(repository, samples)
    assert json.loads(manifest.read_text(encoding="utf-8"))["samples"][0]["split"].isupper()


def test_templates_are_built_from_human_train_truth(tmp_path: Path) -> None:
    repository, entry = repository_with_entry(tmp_path, usage="train")
    repository.save_annotation(confirmed(entry.observation_id, ap_truth=12, mp_truth=3))
    templates, issues = build_templates(repository, inventory(repository))
    assert issues == []
    assert set(templates.digits("AP")) == {1, 2}
    assert set(templates.digits("MP")) == {3}


def test_identical_train_glyphs_are_not_duplicated(tmp_path: Path) -> None:
    repository, entry = repository_with_entry(tmp_path, usage="train")
    duplicate = CorpusEntry(
        "obs-b", "burst-b", 0,
        {"observation": entry.paths["observation"],
         "ap_crop": entry.paths["ap_crop"], "mp_crop": entry.paths["mp_crop"]},
        usage="train",
    )
    repository.save_manifest(CorpusManifest((entry, duplicate)))
    # Les deux observations pointent volontairement sur les mêmes pixels.
    repository.save_annotation(confirmed(entry.observation_id, ap_truth=12, mp_truth=3))
    templates, _issues = build_templates(repository, inventory(repository))
    assert len(templates.digits("AP")[1]) == 1
    assert len(templates.digits("AP")[2]) == 1
    assert len(templates.digits("MP")[3]) == 1


def test_hud_benchmark_reports_insufficient_unlabelled_corpus(tmp_path: Path) -> None:
    repository, _entry = repository_with_entry(tmp_path)
    report = run_hud_benchmark(repository)
    assert report["status"] == "INSUFFICIENT"
    assert report["inventory_examples"] == 2 and report["labelled_examples"] == 0


def test_same_session_cannot_cross_splits(tmp_path: Path) -> None:
    repository, entry = repository_with_entry(tmp_path, usage="train")
    duplicate = CorpusEntry("obs-b", entry.session_id, 1, dict(entry.paths), usage="test")
    repository.save_manifest(CorpusManifest((entry, duplicate)))
    with pytest.raises(ValueError, match="Fuite temporelle"):
        inventory(repository)


def test_duplicate_burst_does_not_cross_splits(tmp_path: Path) -> None:
    repository, entry = repository_with_entry(tmp_path, usage="train")
    duplicate = CorpusEntry("obs-b", "another-session", 0, dict(entry.paths), usage="test")
    repository.save_manifest(CorpusManifest((entry, duplicate)))
    repository.save_annotation(confirmed(entry.observation_id, ap_truth=12,
                                          hud_burst_id="same-visible-counter"))
    repository.save_annotation(confirmed(duplicate.observation_id, ap_truth=12,
                                          hud_burst_id="same-visible-counter"))
    with pytest.raises(ValueError, match="Fuite temporelle"):
        inventory(repository)


def test_inventory_exports_crop_quality_and_group(tmp_path: Path) -> None:
    repository, entry = repository_with_entry(tmp_path)
    repository.save_annotation(confirmed(
        entry.observation_id, ap_truth=12, mp_truth=None,
        ap_crop_quality="VALID", mp_crop_quality="CUT_LEFT",
        hud_burst_id="burst-verified-01",
    ))
    samples = inventory(repository)
    by_kind = {sample.kind: sample for sample in samples}
    assert by_kind["AP"].crop_quality == "VALID"
    assert by_kind["MP"].crop_quality == "CUT_LEFT"
    assert {sample.group_id for sample in samples} == {"burst-verified-01"}


def test_small_grouped_corpus_keeps_validation_and_test_non_empty(tmp_path: Path) -> None:
    entries = [CorpusEntry(f"obs-{index}", f"session-{index}", 0,
                           {"observation": f"session-{index}/observation.json"})
               for index in range(7)]
    groups = {entry.observation_id: f"independent-burst-{index}"
              for index, entry in enumerate(entries)}
    split_by_group = _group_splits(entries, groups)
    assert len(split_by_group) == 7
    assert set(split_by_group.values()) == {"train", "validation", "test"}


def two_consecutive_entries(tmp_path: Path, usages: tuple[str, str]) -> CorpusRepository:
    repository, entry = repository_with_entry(tmp_path)
    first = CorpusEntry("obs-47", "session-x", 47, dict(entry.paths), usage=usages[0])  # type: ignore[arg-type]
    second = CorpusEntry("obs-48", "session-x", 48, dict(entry.paths), usage=usages[1])  # type: ignore[arg-type]
    repository.save_manifest(CorpusManifest((first, second)))
    # Séquence réelle : PA 11 → 7, PM 3 inchangé entre deux captures voisines.
    repository.save_annotation(confirmed("obs-47", ap_truth=11, mp_truth=3, hud_burst_id="f47"))
    repository.save_annotation(confirmed("obs-48", ap_truth=7, mp_truth=3, hud_burst_id="f48"))
    return repository


def test_unchanged_counter_merges_consecutive_bursts(tmp_path: Path) -> None:
    samples = inventory(two_consecutive_entries(tmp_path, ("diagnostic", "diagnostic")))
    assert len({sample.group_id for sample in samples}) == 1
    assert len({sample.split for sample in samples}) == 1


def test_unchanged_counter_cannot_cross_train_and_test(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Fuite temporelle"):
        inventory(two_consecutive_entries(tmp_path, ("train", "test")))


def test_test_split_never_used_for_templates(tmp_path: Path) -> None:
    repository, entry = repository_with_entry(tmp_path, usage="test")
    repository.save_annotation(confirmed(entry.observation_id, ap_truth=12, mp_truth=3))
    templates, _issues = build_templates(repository, inventory(repository))
    assert templates.empty


def test_unknown_truth_excluded_from_accuracy(tmp_path: Path) -> None:
    repository, entry = repository_with_entry(tmp_path, usage="test")
    # PA illisible pour l'humain : aucune vérité, donc aucune ligne de mesure.
    repository.save_annotation(confirmed(entry.observation_id, ap_truth=None, mp_truth=3,
                                          ap_crop_quality="HUD_OCCLUDED"))
    report = run_hud_benchmark(repository)
    assert report["labelled_examples"] == 1
    assert report["modes"]["specialized"]["global"]["examples"] == 1


def test_coverage_and_accepted_accuracy() -> None:
    rows = [{"truth": 7, "predicted": 7, "margin": .3}, {"truth": 1, "predicted": None, "margin": 0},
            {"truth": 11, "predicted": 11, "margin": .3}, {"truth": 7, "predicted": None, "margin": 0}]
    metrics = _classification_metrics(rows)
    assert metrics["coverage"] == .5 and metrics["accepted_accuracy"] == 1.0
    assert metrics["accuracy"] == .5
    assert metrics["one_to_unknown"] == 1 and metrics["seven_to_unknown"] == 1
    assert metrics["digit_confusion"]["1"] == {"UNKNOWN": 1, "1": 2}


def test_one_seven_not_validated_without_independent_train_and_test_sevens() -> None:
    def sample(group: str, split: str, truth: int) -> HUDSample:
        return HUDSample("obs-" + group, "session", None, "AP", truth, "crop.png", None, None,
                         group, "VALID", split)  # type: ignore[arg-type]

    labelled = [sample("a", "train", 11), sample("b", "test", 13), sample("c", "test", 7)]
    verdict = one_seven_verdict(labelled, {"errors_1_to_7": 0, "errors_7_to_1": 0})
    assert verdict["verdict"] == "NOT VALIDATED"
    assert "aucun 7 en TRAIN pour construire un template" in verdict["missing"]
    enough = labelled + [sample("d", "train", 7), sample("e", "train", 1)]
    assert one_seven_verdict(enough, {"errors_1_to_7": 0, "errors_7_to_1": 0})["verdict"] == "VALIDATED"
    # Le « 1 » de « 12 » UNKNOWN à cause du « 2 » ne déclasse pas 1/7 ; une ambiguïté 1/7 oui.
    other_digit = {"errors_1_to_7": 0, "errors_7_to_1": 0, "one_to_unknown": 1}
    assert one_seven_verdict(enough, other_digit, [{"reason": "LOW_MARGIN"}])["verdict"] == "VALIDATED"
    assert one_seven_verdict(enough, other_digit, [{"reason": "AMBIGUOUS_1_7"}])["verdict"] == "IMPROVED"
    assert one_seven_verdict(enough, {"errors_1_to_7": 1, "errors_7_to_1": 0})["verdict"] == "NOT VALIDATED"
