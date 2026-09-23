from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from combatbot.corpus.benchmark import pixel_error, run_benchmark, write_reports
from combatbot.corpus.models import Annotation, CorpusEntry, CorpusManifest, PixelAnnotation
from combatbot.corpus.repository import CorpusRepository


def prediction(*, ap: int | None = 7, mp: int | None = 1,
               player: tuple[int, int] | None = (0, 0)) -> dict[str, object]:
    cell = {
        "logical": {"x": 0, "y": 0}, "center": [20, 20],
        "polygon": [[20, 10], [30, 20], [20, 30], [10, 20]],
        "state": "OCCUPIED", "confidence": 0.8,
    }
    return {
        "combat_detected": True, "combat_confidence": 0.9,
        "player_turn": True, "turn_confidence": 0.8,
        "player_cell": {"x": player[0], "y": player[1]} if player else None,
        "player_confidence": 0.8, "enemies": [],
        "grid": {"cells": [cell], "confidence": 0.75},
        "ap": ap, "mp": mp, "confidence_ap": 0.7, "confidence_mp": 0.6,
        "observation_confidence": 0.75,
    }


def make_debug(root: Path, name: str = "sample", *, ap: int | None = 7,
               mp: int | None = 1, session: str | None = None,
               frame_index: int | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    image = np.full((40, 50, 3), 32, np.uint8)
    cv2.imwrite(str(root / f"{name}-original.png"), image)
    cv2.imwrite(str(root / f"{name}-annotated.png"), image)
    hud = root / f"{name}-hud"
    hud.mkdir()
    cv2.imwrite(str(hud / "ap_original.png"), image[:15, :20])
    cv2.imwrite(str(hud / "mp_original.png"), image[:15, :20])
    payload = {
        "schema_version": 1, "observation_id": f"obs_{name}",
        "session_id": session, "frame_index": frame_index,
        "capture": {"client_size": [800, 600], "phase": "mon tour"},
        "prediction": prediction(ap=ap, mp=mp),
        "files": {
            "frame": f"{name}-original.png", "overlay": f"{name}-annotated.png",
            "ap": f"{name}-hud/ap_original.png", "mp": f"{name}-hud/mp_original.png",
        },
    }
    path = root / f"{name}-observation.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_manifest_round_trip_and_relative_paths(tmp_path: Path) -> None:
    repository = CorpusRepository(tmp_path / "corpus")
    assert repository.load_manifest() == CorpusManifest()
    entry = repository.import_debug(make_debug(tmp_path / "debug"), session_id="s1", frame_index=0)
    loaded = repository.load_manifest()
    assert loaded.entries == (entry,)
    assert all(not Path(value).is_absolute() for value in entry.paths.values())
    with pytest.raises(ValueError, match="relatifs"):
        CorpusEntry("obs", "s", 0, {"frame": "C:/outside.png"}).validate()


def test_annotation_valid_and_partial_round_trip(tmp_path: Path) -> None:
    repository = CorpusRepository(tmp_path / "corpus")
    entry = repository.import_debug(make_debug(tmp_path / "debug"))
    partial = Annotation(entry.observation_id, ap_truth=7)
    repository.save_annotation(partial)
    assert repository.read_annotation(entry.observation_id) == partial
    complete = Annotation(
        entry.observation_id, combat_truth=True, player_turn_truth=False,
        ap_truth=7, mp_truth=1, player=PixelAnnotation((20, 20), (0, 0)),
        enemies=(PixelAnnotation((35, 25), (1, 0)),),
        reference_cells=(PixelAnnotation((20, 20), (0, 0)),),
        reference_cells_complete=True, grid_anchors=(PixelAnnotation((10, 10)),),
        digit_issue="1_vs_7", comments="cas réel",
    )
    updated = repository.save_annotation(complete)
    assert updated.annotation_available and updated.ap_truth == 7
    assert repository.read_annotation(updated) == complete


def test_corpus_coordinate_serialization() -> None:
    annotation = PixelAnnotation((12, 34), (2, 3))
    payload = annotation.to_dict()
    assert payload["coordinate_space"] == "combat"
    assert payload["center"] == [12, 34]
    assert PixelAnnotation.from_dict(payload) == annotation
    legacy = PixelAnnotation.from_dict({"center": [12, 34], "logical": [2, 3]})
    assert legacy.coordinate_space == "combat"
    assert legacy == annotation


def test_observation_without_ground_truth_keeps_metrics_unknown(tmp_path: Path) -> None:
    repository = CorpusRepository(tmp_path / "corpus")
    repository.import_debug(make_debug(tmp_path / "debug"))
    report = run_benchmark(repository)
    assert report["corpus"]["annotated_observations"] == 0
    assert report["ap"]["accuracy"] is None
    assert report["states"]["combat"]["accuracy"] is None


def test_session_frames_and_stability_are_grouped(tmp_path: Path) -> None:
    repository = CorpusRepository(tmp_path / "corpus")
    first = repository.import_debug(make_debug(tmp_path / "d1", "one"),
                                    session_id="fight_a", frame_index=2)
    second = repository.import_debug(make_debug(tmp_path / "d2", "two"),
                                     session_id="fight_a", frame_index=3)
    entries = repository.list_entries()
    assert [(item.session_id, item.frame_index) for item in entries] == [("fight_a", 2), ("fight_a", 3)]
    report = run_benchmark(repository)
    assert report["sessions"]["successive_pairs"] == 1
    assert report["sessions"]["mean_identifier_jaccard"] == 1.0
    assert first.observation_id != second.observation_id


def test_import_debug_copies_useful_files_without_touching_source(tmp_path: Path) -> None:
    source = make_debug(tmp_path / "debug")
    before = source.read_bytes()
    repository = CorpusRepository(tmp_path / "corpus")
    entry = repository.import_debug(source)
    assert source.read_bytes() == before
    assert repository.resolve(entry.paths["frame"]).is_file()
    assert repository.resolve(entry.paths["overlay"]).is_file()
    assert repository.resolve(entry.paths["ap_crop"]).is_file()
    assert repository.resolve(entry.paths["mp_crop"]).is_file()


def test_promotion_requires_annotation_and_is_explicit(tmp_path: Path) -> None:
    repository = CorpusRepository(tmp_path / "corpus")
    entry = repository.import_debug(make_debug(tmp_path / "debug"))
    fixture_root = tmp_path / "fixtures"
    with pytest.raises(ValueError, match="annotée"):
        repository.promote_fixture(entry.observation_id, "pa_7_basic", fixture_root)
    repository.save_annotation(Annotation(entry.observation_id, ap_truth=7))
    destination = repository.promote_fixture(entry.observation_id, "pa_7_basic", fixture_root)
    assert (destination / "annotation.json").is_file()
    assert json.loads((destination / "fixture.json").read_text(encoding="utf-8"))["usage"] == "test"
    with pytest.raises(ValueError, match="existe déjà"):
        repository.promote_fixture(entry.observation_id, "pa_7_basic", fixture_root)


def test_empty_benchmark_writes_human_and_json_reports(tmp_path: Path) -> None:
    repository = CorpusRepository(tmp_path / "corpus")
    report = run_benchmark(repository)
    json_path, markdown_path = write_reports(report, tmp_path / "reports")
    assert json.loads(json_path.read_text(encoding="utf-8"))["corpus"]["manifest_entries"] == 0
    assert "N/A" in markdown_path.read_text(encoding="utf-8")


def test_ap_mp_confusion_matrix_and_confidence(tmp_path: Path) -> None:
    repository = CorpusRepository(tmp_path / "corpus")
    first = repository.import_debug(make_debug(tmp_path / "d1", "one", ap=7, mp=1))
    second = repository.import_debug(make_debug(tmp_path / "d2", "two", ap=1, mp=7))
    repository.save_annotation(Annotation(first.observation_id, ap_truth=1, mp_truth=7,
                                          digit_issue="1_vs_7"))
    repository.save_annotation(Annotation(second.observation_id, ap_truth=7, mp_truth=1,
                                          digit_issue="1_vs_7"))
    report = run_benchmark(repository)
    assert report["ap"]["errors_1_to_7"] == 1
    assert report["ap"]["errors_7_to_1"] == 1
    assert report["mp"]["errors_1_to_7"] == 1
    assert report["mp"]["errors_7_to_1"] == 1
    assert report["ap"]["accuracy"] == 0
    assert report["ap"]["mean_confidence_incorrect"] == pytest.approx(0.7)


def test_pixel_error_has_no_hidden_threshold() -> None:
    assert pixel_error((0, 0), (3, 4)) == 5.0


def test_incomplete_corpus_and_missing_image_are_reported(tmp_path: Path) -> None:
    repository = CorpusRepository(tmp_path / "corpus")
    entry = repository.import_debug(make_debug(tmp_path / "debug"))
    repository.resolve(entry.paths["frame"]).unlink()
    report = run_benchmark(repository)
    assert any("frame absent" in item["issue"] for item in report["issues"])
    assert report["corpus"]["readable_observations"] == 1


def test_corrupted_json_is_rejected_or_reported(tmp_path: Path) -> None:
    repository = CorpusRepository(tmp_path / "corpus")
    repository.ensure_layout()
    repository.manifest_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON illisible"):
        repository.load_manifest()

    source = tmp_path / "broken-observation.json"
    source.write_text("not-json", encoding="utf-8")
    clean = CorpusRepository(tmp_path / "clean")
    with pytest.raises(ValueError, match="JSON illisible"):
        clean.import_debug(source)
