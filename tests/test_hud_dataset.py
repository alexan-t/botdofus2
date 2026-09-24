from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from combatbot.corpus.hud_dataset import build_templates, inventory, run_hud_benchmark, write_manifest
from combatbot.corpus.models import Annotation, CorpusEntry, CorpusManifest
from combatbot.corpus.repository import CorpusRepository


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
    repository.save_annotation(Annotation(entry.observation_id, ap_truth=12, mp_truth=3))
    samples = inventory(repository)
    assert len({sample.split for sample in samples}) == 1
    assert samples[0].map_id == 42 and samples[0].client_size == (800, 600)
    assert samples[0].layout_signature == "layout-a"
    manifest = write_manifest(repository, samples)
    assert json.loads(manifest.read_text(encoding="utf-8"))["samples"][0]["split"].isupper()


def test_templates_are_built_from_human_train_truth(tmp_path: Path) -> None:
    repository, entry = repository_with_entry(tmp_path, usage="train")
    repository.save_annotation(Annotation(entry.observation_id, ap_truth=12, mp_truth=3))
    templates, issues = build_templates(repository, inventory(repository))
    assert issues == []
    assert set(templates.digits("AP")) == {1, 2}
    assert set(templates.digits("MP")) == {3}


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
