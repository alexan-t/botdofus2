"""Inventaire et banc du corpus PA/PM, fondés uniquement sur les annotations humaines."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from statistics import mean
import time
from typing import Any, Literal

import cv2

from combatbot.corpus.repository import CorpusRepository
from combatbot.vision.hud_reader import GlyphTemplateLibrary, HUDReader, segment_glyphs


HUDSplit = Literal["train", "validation", "test"]


@dataclass(frozen=True)
class HUDSample:
    observation_id: str
    session_id: str
    map_id: int | None
    kind: Literal["AP", "MP"]
    truth: int | None
    crop_path: str
    client_size: tuple[int, int] | None
    layout_signature: str | None
    split: HUDSplit

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id, "session_id": self.session_id,
            "map_id": self.map_id, "type": self.kind, "truth": self.truth,
            "crop_path": self.crop_path,
            "client_size": list(self.client_size) if self.client_size else None,
            "layout_signature": self.layout_signature, "split": self.split.upper(),
        }


def _session_split(session_id: str) -> HUDSplit:
    bucket = int(hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "train" if bucket < 70 else ("validation" if bucket < 85 else "test")


def inventory(repository: CorpusRepository) -> tuple[HUDSample, ...]:
    samples: list[HUDSample] = []
    entries = repository.list_entries()
    split_by_session: dict[str, HUDSplit] = {}
    for session_id in {entry.session_id for entry in entries}:
        explicit = {entry.usage for entry in entries if entry.session_id == session_id
                    and entry.usage in ("train", "validation", "test")}
        if len(explicit) > 1:
            raise ValueError(f"Fuite temporelle : la session {session_id} traverse plusieurs splits")
        split_by_session[session_id] = (next(iter(explicit)) if explicit else _session_split(session_id))  # type: ignore[assignment]
    for entry in entries:
        try:
            document = repository.read_observation(entry)
            annotation = repository.read_annotation(entry)
        except (OSError, ValueError, KeyError):
            continue
        capture = document.get("capture", {})
        capture = capture if isinstance(capture, dict) else {}
        calibration = capture.get("calibration", {})
        calibration = calibration if isinstance(calibration, dict) else {}
        size = capture.get("client_size")
        client_size = ((int(size[0]), int(size[1]))
                       if isinstance(size, (list, tuple)) and len(size) == 2 else None)
        split = split_by_session[entry.session_id]
        for kind, key, truth in (
            ("AP", "ap_crop", annotation.ap_truth if annotation else None),
            ("MP", "mp_crop", annotation.mp_truth if annotation else None),
        ):
            path = entry.paths.get(key)
            if not path:
                continue
            samples.append(HUDSample(
                entry.observation_id, entry.session_id,
                int(capture["map_id_declared"]) if capture.get("map_id_declared") is not None else None,
                kind, truth, path, client_size,
                str(calibration["layout_signature"]) if calibration.get("layout_signature") else None,
                split,
            ))
    return tuple(samples)


def write_manifest(repository: CorpusRepository, samples: tuple[HUDSample, ...]) -> Path:
    repository.manifests.mkdir(parents=True, exist_ok=True)
    path = repository.manifests / "hud_manifest.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "split_policy": "grouped by session; explicit corpus usage wins, otherwise SHA-256 70/15/15",
        "samples": [sample.to_dict() for sample in samples],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_templates(repository: CorpusRepository, samples: tuple[HUDSample, ...]) -> tuple[GlyphTemplateLibrary, list[str]]:
    library, issues = GlyphTemplateLibrary(), []
    for sample in samples:
        if sample.split != "train" or sample.truth is None:
            continue
        image = cv2.imread(str(repository.resolve(sample.crop_path)), cv2.IMREAD_COLOR)
        if image is None:
            issues.append(f"{sample.observation_id}/{sample.kind}: crop illisible")
            continue
        glyphs = segment_glyphs(image)
        digits = [int(value) for value in str(sample.truth)]
        if len(glyphs) != len(digits):
            issues.append(
                f"{sample.observation_id}/{sample.kind}: {len(glyphs)} glyphe(s), vérité {sample.truth}"
            )
            continue
        for digit, glyph in zip(digits, glyphs):
            library.add(sample.kind, digit, glyph.image,
                        source=f"{sample.observation_id}/{sample.kind}")
    return library, issues


def _classification_metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    accepted = [row for row in rows if row["predicted"] is not None]
    correct = sum(row["truth"] == row["predicted"] for row in rows)
    accepted_correct = sum(row["truth"] == row["predicted"] for row in accepted)
    confusion: dict[str, dict[str, int]] = defaultdict(dict)
    digit_pairs: list[tuple[str, str]] = []
    for row in rows:
        truth, predicted = str(row["truth"]), row["predicted"]
        label = "UNKNOWN" if predicted is None else str(predicted)
        confusion[truth][label] = confusion[truth].get(label, 0) + 1
        if predicted is not None and len(truth) == len(str(predicted)):
            digit_pairs.extend(zip(truth, str(predicted)))

    def precision_recall(digit: str) -> dict[str, float | None]:
        tp = sum(t == digit and p == digit for t, p in digit_pairs)
        fp = sum(t != digit and p == digit for t, p in digit_pairs)
        fn = sum(t == digit and p != digit for t, p in digit_pairs)
        return {
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
        }

    return {
        "examples": len(rows), "accuracy": correct / len(rows) if rows else None,
        "accepted_accuracy": accepted_correct / len(accepted) if accepted else None,
        "coverage": len(accepted) / len(rows) if rows else None,
        "unknown": len(rows) - len(accepted),
        "unknown_rate": (len(rows) - len(accepted)) / len(rows) if rows else None,
        "confusion_matrix": dict(confusion),
        "errors_1_to_7": sum(t == "1" and p == "7" for t, p in digit_pairs),
        "errors_7_to_1": sum(t == "7" and p == "1" for t, p in digit_pairs),
        "digit_1": precision_recall("1"), "digit_7": precision_recall("7"),
        "margins": [row["margin"] for row in rows],
    }


def run_hud_benchmark(repository: CorpusRepository, *, rapidocr: bool = False) -> dict[str, Any]:
    samples = inventory(repository)
    manifest_path = write_manifest(repository, samples)
    labelled = tuple(sample for sample in samples if sample.truth is not None)
    library, issues = build_templates(repository, labelled)
    if not library.empty:
        library.save(repository.root.parent / "hud_templates")
    rapid_reader = None
    if rapidocr:
        from combatbot.vision.combat_ocr import read_small_number
        rapid_reader = lambda image, minimum, maximum: read_small_number(image, minimum, maximum)
    reader = HUDReader(library, rapidocr_reader=rapid_reader, rapidocr_interval=0.0)
    rows_by_split: dict[str, list[dict[str, object]]] = {name: [] for name in ("train", "validation", "test")}
    timings: dict[str, list[float]] = defaultdict(list)
    segmentation: dict[str, Counter[int]] = {"AP": Counter(), "MP": Counter()}
    for sample in samples:
        image = cv2.imread(str(repository.resolve(sample.crop_path)), cv2.IMREAD_COLOR)
        if image is None:
            issues.append(f"{sample.observation_id}/{sample.kind}: crop illisible")
            continue
        started = time.perf_counter()
        result = reader.read(image, sample.kind)
        segmentation[sample.kind][len(result.glyphs)] += 1
        timings[f"specialized_{sample.kind.lower()}"] += [result.timings_ms.get("specialized", 0.0)]
        if "rapidocr" in result.timings_ms:
            timings[f"rapidocr_{sample.kind.lower()}"] += [result.timings_ms["rapidocr"]]
        if sample.truth is not None:
            rows_by_split[sample.split].append({
                "observation_id": sample.observation_id, "kind": sample.kind,
                "truth": sample.truth, "predicted": result.value, "confidence": result.confidence,
                "margin": result.margin, "reason": result.reason.value,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
            })
    truth_numbers = Counter(str(sample.truth) for sample in labelled)
    truth_digits = Counter(digit for sample in labelled for digit in str(sample.truth))
    return {
        "schema_version": 1, "manifest": str(manifest_path),
        "inventory_examples": len(samples), "labelled_examples": len(labelled),
        "unlabelled_examples": len(samples) - len(labelled),
        "distribution": {
            "digits": {str(digit): truth_digits.get(str(digit), 0) for digit in range(10)},
            "numbers": dict(sorted(truth_numbers.items(), key=lambda item: int(item[0]))),
            "types": dict(Counter(sample.kind for sample in labelled)),
            "splits": dict(Counter(sample.split for sample in labelled)),
        },
        "splits": {name: _classification_metrics(rows) for name, rows in rows_by_split.items()},
        "performance_ms_mean": {name: mean(values) if values else None for name, values in timings.items()},
        "segmentation_glyph_counts": {
            kind: {str(count): occurrences for count, occurrences in sorted(values.items())}
            for kind, values in segmentation.items()
        },
        "template_digits": {
            kind: {str(digit): len(values) for digit, values in library.digits(kind).items()}
            for kind in ("AP", "MP")
        },
        "issues": issues,
        "status": "PASS" if labelled and rows_by_split["test"] else "INSUFFICIENT",
        "note": "Seules les annotations humaines PA/PM sont utilisées.",
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Benchmark HUD PA/PM", "",
        f"- Statut : **{report['status']}**",
        f"- Crops inventoriés : **{report['inventory_examples']}**",
        f"- Vérités humaines : **{report['labelled_examples']}**",
        f"- Sans vérité : **{report['unlabelled_examples']}**", "",
        "## Distribution", "",
        f"- Chiffres 0..9 : `{report['distribution']['digits']}`",
        f"- Nombres : `{report['distribution']['numbers']}`",
        f"- Splits : `{report['distribution']['splits']}`", "",
    ]
    for split in ("train", "validation", "test"):
        value = report["splits"][split]
        lines += [f"## {split.upper()}", "",
                  f"- Exemples : {value['examples']}", f"- Accuracy : {value['accuracy']}",
                  f"- Accepted accuracy : {value['accepted_accuracy']}",
                  f"- Coverage : {value['coverage']}", f"- UNKNOWN : {value['unknown']}",
                  f"- 1→7 / 7→1 : {value['errors_1_to_7']} / {value['errors_7_to_1']}", ""]
    lines += ["## Limite", "", report["note"]]
    if report["status"] == "INSUFFICIENT":
        lines.append("Le corpus ne permet pas encore de fixer ou valider statistiquement les seuils.")
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / "hud-reader.json", directory / "hud-reader.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, md_path
