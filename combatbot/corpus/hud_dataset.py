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
import numpy as np

from combatbot.corpus.repository import CorpusRepository
from combatbot.vision.hud_reader import (
    GlyphTemplateLibrary, HUDReader, distinguish_one_seven, segment_glyphs,
)


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
    group_id: str
    crop_quality: str | None
    split: HUDSplit

    def to_dict(self) -> dict[str, object]:
        return {
            "observation_id": self.observation_id, "session_id": self.session_id,
            "map_id": self.map_id, "type": self.kind, "truth": self.truth,
            "crop_path": self.crop_path,
            "client_size": list(self.client_size) if self.client_size else None,
            "layout_signature": self.layout_signature, "split": self.split.upper(),
            "group_id": self.group_id, "crop_quality": self.crop_quality,
        }


def _session_split(session_id: str) -> HUDSplit:
    bucket = int(hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "train" if bucket < 70 else ("validation" if bucket < 85 else "test")


def _group_splits(entries, group_by_observation: dict[str, str]) -> dict[str, HUDSplit]:
    """Répartit les groupes sans consulter les vérités et sans fuite temporelle.

    Le simple bucket SHA peut laisser VALIDATION ou TEST vide sur un petit corpus.
    On classe donc les groupes par hash, puis on applique une proportion 70/15/15
    avec au moins un groupe de validation et de test dès trois groupes disponibles.
    Les usages explicites restent prioritaires.
    """
    group_ids = sorted(set(group_by_observation.values()),
                       key=lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest())
    explicit_by_group: dict[str, HUDSplit] = {}
    for group_id in group_ids:
        explicit = {entry.usage for entry in entries
                    if group_by_observation[entry.observation_id] == group_id
                    and entry.usage in ("train", "validation", "test")}
        if len(explicit) > 1:
            raise ValueError(f"Fuite temporelle : le groupe {group_id} traverse plusieurs splits")
        if explicit:
            explicit_by_group[group_id] = next(iter(explicit))  # type: ignore[assignment]

    count = len(group_ids)
    if count < 3:
        targets = {"train": max(1, count - 1), "validation": 0,
                   "test": 1 if count == 2 else 0}
    else:
        validation = max(1, round(count * 0.15))
        test = max(1, round(count * 0.15))
        train = max(1, count - validation - test)
        targets = {"train": train, "validation": validation, "test": test}

    result = dict(explicit_by_group)
    current = Counter(result.values())
    for group_id in group_ids:
        if group_id in result:
            continue
        deficits = {name: targets[name] - current[name]
                    for name in ("train", "validation", "test")}
        split = max(deficits, key=lambda name: (deficits[name],
                                                 {"train": 2, "validation": 1, "test": 0}[name]))
        result[group_id] = split  # type: ignore[assignment]
        current[split] += 1
    return result


def _merge_continuous_counters(entries, annotations, group_by_observation: dict[str, str]) -> dict[str, str]:
    """Fusionne les groupes consécutifs d'une session qui partagent un compteur inchangé.

    Deux captures voisines où le PM reste à 3 montrent le même compteur immobile, même
    si le PA a changé : ce ne sont pas deux preuves indépendantes du PM, et elles ne
    doivent pas se retrouver l'une en TRAIN, l'autre en TEST.
    """
    parent = {group: group for group in group_by_observation.values()}

    def find(group: str) -> str:
        while parent[group] != group:
            parent[group] = parent[parent[group]]
            group = parent[group]
        return group

    by_session: dict[str, list] = defaultdict(list)
    for entry in entries:
        by_session[entry.session_id].append(entry)
    for session_entries in by_session.values():
        ordered = sorted(session_entries, key=lambda item: (item.frame_index, item.observation_id))
        for previous, current in zip(ordered, ordered[1:]):
            before, after = annotations[previous.observation_id], annotations[current.observation_id]
            if before is None or after is None:
                continue
            shared = any(first is not None and first == second for first, second in (
                (before.ap_truth, after.ap_truth), (before.mp_truth, after.mp_truth)))
            if shared:
                first, second = (find(group_by_observation[previous.observation_id]),
                                 find(group_by_observation[current.observation_id]))
                if first != second:
                    parent[max(first, second)] = min(first, second)
    members: dict[str, set[str]] = defaultdict(set)
    for group in parent:
        members[find(group)].add(group)
    names = {root: "+".join(sorted(groups)) for root, groups in members.items()}
    return {observation: names[find(group)] for observation, group in group_by_observation.items()}


def inventory(repository: CorpusRepository) -> tuple[HUDSample, ...]:
    samples: list[HUDSample] = []
    entries = repository.list_entries()
    annotations = {entry.observation_id: repository.read_annotation(entry) for entry in entries}
    group_by_observation = {
        entry.observation_id: (
            annotations[entry.observation_id].hud_burst_id
            if annotations[entry.observation_id] is not None
            and annotations[entry.observation_id].hud_burst_id
            else entry.session_id
        )
        for entry in entries
    }
    group_by_observation = _merge_continuous_counters(entries, annotations, group_by_observation)
    labelled_groups = {
        group_by_observation[entry.observation_id]
        for entry in entries
        if annotations[entry.observation_id] is not None
        and (annotations[entry.observation_id].ap_truth is not None
             or annotations[entry.observation_id].mp_truth is not None)
    }
    split_by_group: dict[str, HUDSplit] = {}
    for selected_groups in (
        labelled_groups,
        set(group_by_observation.values()) - labelled_groups,
    ):
        if not selected_groups:
            continue
        selected_entries = [entry for entry in entries
                            if group_by_observation[entry.observation_id] in selected_groups]
        selected_mapping = {entry.observation_id: group_by_observation[entry.observation_id]
                            for entry in selected_entries}
        split_by_group.update(_group_splits(selected_entries, selected_mapping))
    for entry in entries:
        try:
            document = repository.read_observation(entry)
            annotation = annotations[entry.observation_id]
        except (OSError, ValueError, KeyError):
            continue
        capture = document.get("capture", {})
        capture = capture if isinstance(capture, dict) else {}
        calibration = capture.get("calibration", {})
        calibration = calibration if isinstance(calibration, dict) else {}
        size = capture.get("client_size")
        client_size = ((int(size[0]), int(size[1]))
                       if isinstance(size, (list, tuple)) and len(size) == 2 else None)
        group_id = group_by_observation[entry.observation_id]
        split = split_by_group[group_id]
        for kind, key, truth, quality in (
            ("AP", "ap_crop", annotation.ap_truth if annotation else None,
             annotation.ap_crop_quality if annotation else None),
            ("MP", "mp_crop", annotation.mp_truth if annotation else None,
             annotation.mp_crop_quality if annotation else None),
        ):
            path = entry.paths.get(key)
            if not path:
                continue
            samples.append(HUDSample(
                entry.observation_id, entry.session_id,
                int(capture["map_id_declared"]) if capture.get("map_id_declared") is not None else None,
                kind, truth, path, client_size,
                str(calibration["layout_signature"]) if calibration.get("layout_signature") else None,
                group_id, quality,
                split,
            ))
    return tuple(samples)


def write_manifest(repository: CorpusRepository, samples: tuple[HUDSample, ...]) -> Path:
    repository.manifests.mkdir(parents=True, exist_ok=True)
    path = repository.manifests / "hud_manifest.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "split_policy": "grouped by annotated burst, else session; explicit usage wins; hash-ranked 70/15/15 with non-empty validation/test from 3 groups",
        "samples": [sample.to_dict() for sample in samples],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_templates(repository: CorpusRepository, samples: tuple[HUDSample, ...]) -> tuple[GlyphTemplateLibrary, list[str]]:
    library, issues = GlyphTemplateLibrary(), []
    seen: set[tuple[str, int, bytes]] = set()
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
            signature = (sample.kind, digit, hashlib.sha256(glyph.image.tobytes()).digest())
            if signature in seen:
                continue
            seen.add(signature)
            library.add(sample.kind, digit, glyph.image,
                        source=f"{sample.observation_id}/{sample.kind}")
    return library, issues


def _classification_metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    accepted = [row for row in rows if row["predicted"] is not None]
    correct = sum(row["truth"] == row["predicted"] for row in rows)
    accepted_correct = sum(row["truth"] == row["predicted"] for row in accepted)
    confusion: dict[str, dict[str, int]] = defaultdict(dict)
    digit_confusion: dict[str, dict[str, int]] = {str(digit): {} for digit in range(10)}
    digit_pairs: list[tuple[str, str]] = []
    for row in rows:
        truth, predicted = str(row["truth"]), row["predicted"]
        label = "UNKNOWN" if predicted is None else str(predicted)
        confusion[truth][label] = confusion[truth].get(label, 0) + 1
        if predicted is not None and len(truth) == len(str(predicted)):
            pairs = list(zip(truth, str(predicted)))
            digit_pairs.extend(pairs)
        else:
            # Un nombre inconnu (ou de longueur différente) ne prouve aucun chiffre.
            pairs = [(digit, "UNKNOWN" if predicted is None else "LENGTH_MISMATCH") for digit in truth]
        for digit, predicted_digit in pairs:
            cell = digit_confusion[digit]
            cell[predicted_digit] = cell.get(predicted_digit, 0) + 1

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
        "digit_confusion": {digit: cells for digit, cells in digit_confusion.items() if cells},
        "errors_1_to_7": sum(t == "1" and p == "7" for t, p in digit_pairs),
        "errors_7_to_1": sum(t == "7" and p == "1" for t, p in digit_pairs),
        "one_to_unknown": digit_confusion["1"].get("UNKNOWN", 0),
        "seven_to_unknown": digit_confusion["7"].get("UNKNOWN", 0),
        "digit_1": precision_recall("1"), "digit_7": precision_recall("7"),
        "margins": [row["margin"] for row in rows],
    }


def _mode_metrics(rows_by_split: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    rows = [row for split_rows in rows_by_split.values() for row in split_rows]
    return {
        "splits": {name: _classification_metrics(split_rows)
                   for name, split_rows in rows_by_split.items()},
        "global": _classification_metrics(rows),
        "by_kind": {
            kind: _classification_metrics([row for row in rows if row["kind"] == kind])
            for kind in ("AP", "MP")
        },
    }


def one_seven_verdict(labelled: tuple[HUDSample, ...] | list[HUDSample],
                      test_metrics: dict[str, object]) -> dict[str, object]:
    """Applique le minimum 3B-4R : groupes indépendants de 1 et de 7, en TRAIN et en TEST."""
    groups: dict[str, dict[str, set[str]]] = {
        digit: defaultdict(set) for digit in ("1", "7")
    }
    for sample in labelled:
        for digit in ("1", "7"):
            if digit in str(sample.truth):
                groups[digit][sample.split].add(sample.group_id)
    counts = {digit: {split: len(groups[digit].get(split, ())) for split in ("train", "validation", "test")}
              for digit in ("1", "7")}
    missing = []
    for digit in ("1", "7"):
        total = len(set().union(*groups[digit].values())) if groups[digit] else 0
        if total < 2:
            missing.append(f"moins de deux groupes indépendants contenant {digit}")
        if not counts[digit]["train"]:
            missing.append(f"aucun {digit} en TRAIN pour construire un template")
        if not counts[digit]["test"]:
            missing.append(f"aucun {digit} en TEST")
    digit_confusion = test_metrics.get("digit_confusion", {})
    assert isinstance(digit_confusion, dict)
    confusions = int(test_metrics.get("errors_1_to_7", 0)) + int(test_metrics.get("errors_7_to_1", 0))
    unknown = int(test_metrics.get("one_to_unknown", 0)) + int(test_metrics.get("seven_to_unknown", 0))
    if missing or confusions:
        verdict = "NOT VALIDATED"
    else:
        verdict = "VALIDATED" if not unknown else "IMPROVED"
    return {"verdict": verdict, "independent_groups": counts, "missing": missing,
            "test_confusions_1_7": confusions, "test_unknown_1_7": unknown,
            "test_digit_1": digit_confusion.get("1", {}), "test_digit_7": digit_confusion.get("7", {})}


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
    specialized_reader = HUDReader(library)
    rapid_cache: dict[tuple[tuple[int, ...], bytes], tuple[int | None, float]] = {}
    combined_reader = HUDReader(
        library,
        rapidocr_reader=lambda image, minimum, maximum: rapid_cache[
            (tuple(image.shape), hashlib.sha256(image.tobytes()).digest())
        ],
        rapidocr_interval=0.0,
    ) if rapid_reader is not None else None
    mode_rows: dict[str, dict[str, list[dict[str, object]]]] = {
        mode: {name: [] for name in ("train", "validation", "test")}
        for mode in (("specialized", "rapidocr", "combined") if rapidocr else ("specialized",))
    }
    timings: dict[str, list[float]] = defaultdict(list)
    segmentation: dict[str, Counter[int]] = {"AP": Counter(), "MP": Counter()}
    for sample in samples:
        image = cv2.imread(str(repository.resolve(sample.crop_path)), cv2.IMREAD_COLOR)
        if image is None:
            issues.append(f"{sample.observation_id}/{sample.kind}: crop illisible")
            continue
        started = time.perf_counter()
        result = specialized_reader.read(image, sample.kind)
        segmentation[sample.kind][len(result.glyphs)] += 1
        timings[f"specialized_{sample.kind.lower()}"] += [result.timings_ms.get("specialized", 0.0)]
        if sample.truth is not None:
            common = {
                "observation_id": sample.observation_id, "kind": sample.kind,
                "truth": sample.truth,
            }
            mode_rows["specialized"][sample.split].append({**common,
                "predicted": result.value, "confidence": result.confidence,
                "margin": result.margin, "reason": result.reason.value,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
            })
        if rapid_reader is not None:
            signature = (tuple(image.shape), hashlib.sha256(image.tobytes()).digest())
            if signature not in rapid_cache:
                rapid_started = time.perf_counter()
                rapid_cache[signature] = rapid_reader(image, 0, 99)
                timings[f"rapidocr_{sample.kind.lower()}"] += [
                    (time.perf_counter() - rapid_started) * 1000
                ]
            rapid_value, rapid_confidence = rapid_cache[signature]
            combined_started = time.perf_counter()
            assert combined_reader is not None
            combined = combined_reader.read(image, sample.kind)
            timings[f"combined_{sample.kind.lower()}"] += [
                (time.perf_counter() - combined_started) * 1000
            ]
            if sample.truth is not None:
                common = {"observation_id": sample.observation_id,
                          "kind": sample.kind, "truth": sample.truth}
                mode_rows["rapidocr"][sample.split].append({**common,
                    "predicted": rapid_value, "confidence": rapid_confidence,
                    "margin": 0.0, "reason": "RAPIDOCR_RAW",
                })
                mode_rows["combined"][sample.split].append({**common,
                    "predicted": combined.value, "confidence": combined.confidence,
                    "margin": combined.margin, "reason": combined.reason.value,
                })
    truth_numbers = Counter(str(sample.truth) for sample in labelled)
    truth_digits = Counter(digit for sample in labelled for digit in str(sample.truth))
    modes = {name: _mode_metrics(rows) for name, rows in mode_rows.items()}
    one_seven = one_seven_verdict(labelled, modes["specialized"]["splits"]["test"])
    missing_digits = [str(digit) for digit in range(10) if not truth_digits.get(str(digit))]
    if not labelled or not mode_rows["specialized"]["test"]:
        status = "INSUFFICIENT"
    elif one_seven["verdict"] != "VALIDATED" or missing_digits:
        status = "PARTIAL"
    else:
        status = "PASS"
    return {
        "schema_version": 1, "manifest": str(manifest_path),
        "inventory_examples": len(samples), "labelled_examples": len(labelled),
        "unlabelled_examples": len(samples) - len(labelled),
        "distribution": {
            "digits": {str(digit): truth_digits.get(str(digit), 0) for digit in range(10)},
            "numbers": dict(sorted(truth_numbers.items(), key=lambda item: int(item[0]))),
            "types": dict(Counter(sample.kind for sample in labelled)),
            "splits": dict(Counter(sample.split for sample in labelled)),
            "groups": len({sample.group_id for sample in labelled}),
            "crop_quality": dict(Counter(sample.crop_quality or "UNCLASSIFIED" for sample in samples)),
        },
        "splits": modes["specialized"]["splits"],
        "modes": modes,
        "one_seven": one_seven,
        "missing_digits": missing_digits,
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
        "status": status,
        "note": "Seules les annotations humaines PA/PM sont utilisées.",
    }


def export_one_seven(repository: CorpusRepository, directory: Path) -> tuple[Path, Path | None]:
    """Exporte les preuves de chaque glyphe annoté 1 ou 7 et une planche comparative.

    Les templates viennent seulement de TRAIN ; seuls les crops annotés sont lus.
    """
    samples = inventory(repository)
    labelled = tuple(sample for sample in samples if sample.truth is not None)
    library, _issues = build_templates(repository, labelled)
    reader = HUDReader(library)
    rows: list[dict[str, object]] = []
    tiles: list[np.ndarray] = []
    tile_digits: list[int] = []
    for sample in labelled:
        truth = str(sample.truth)
        if "1" not in truth and "7" not in truth:
            continue
        image = cv2.imread(str(repository.resolve(sample.crop_path)), cv2.IMREAD_COLOR)
        if image is None:
            continue
        result = reader.read(image, sample.kind)
        segments = segment_glyphs(image)
        if len(segments) != len(truth):
            rows.append({"observation_id": sample.observation_id, "kind": sample.kind,
                         "split": sample.split, "truth": sample.truth, "glyph_index": None,
                         "reason": result.reason.value, "segmented_glyphs": len(segments)})
            continue
        for index, (digit, segment) in enumerate(zip(truth, segments)):
            if digit not in ("1", "7"):
                continue
            glyph = next((item for item in result.glyphs if item.bbox == segment.bbox), None)
            mask = segment.image > 0
            preferred, features = distinguish_one_seven(segment.image)
            band = max(2, round(mask.shape[0] * 0.28))
            rows.append({
                "observation_id": sample.observation_id, "kind": sample.kind, "split": sample.split,
                "group_id": sample.group_id, "truth": sample.truth, "glyph_index": index,
                "truth_digit": int(digit),
                "horizontal_projection": [int(value) for value in mask.sum(axis=1)],
                "top_band_occupancy": float(mask[:band].mean()),
                "top_width": features.get("top_span"), "lower_center": features.get("lower_center"),
                "distinguish_one_seven": preferred,
                "best_digit": glyph.best_digit if glyph else None,
                "best_score": glyph.best_score if glyph else None,
                "second_digit": glyph.second_digit if glyph else None,
                "second_score": glyph.second_score if glyph else None,
                "margin": glyph.margin if glyph else None,
                "final_value": result.value, "reason": result.reason.value,
            })
            tile = cv2.cvtColor(cv2.resize(segment.image, None, fx=4, fy=4,
                                           interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
            label = np.full((34, tile.shape[1], 3), 40, np.uint8)
            predicted = "?" if glyph is None or glyph.best_digit is None else str(glyph.best_digit)
            color = (80, 220, 80) if predicted == digit else (60, 60, 230)
            cv2.putText(label, f"T{digit} P{predicted}", (3, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1)
            cv2.putText(label, f"{sample.split[:3]} {result.value}", (3, 29),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (220, 220, 220), 1)
            tiles.append(np.vstack([label, tile]))
            tile_digits.append(int(digit))
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "one-seven.json"
    json_path.write_text(json.dumps({"schema_version": 1, "glyphs": rows}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    if not tiles:
        return json_path, None
    # Les 7 d'abord : ils sont rares et doivent rester visibles sur la planche.
    tiles = [tile for _digit, tile in sorted(zip(tile_digits, tiles), key=lambda item: -item[0])]
    per_row = 12
    blank = np.zeros_like(tiles[0])
    while len(tiles) % per_row:
        tiles.append(blank)
    plate = np.vstack([np.hstack(tiles[i:i + per_row]) for i in range(0, len(tiles), per_row)])
    plate_path = directory / "one-seven.png"
    cv2.imwrite(str(plate_path), plate)
    return json_path, plate_path


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
    one_seven = report.get("one_seven", {})
    lines += ["## 1 / 7", "",
              f"- Verdict : **{one_seven.get('verdict')}**",
              f"- Groupes indépendants : `{one_seven.get('independent_groups')}`",
              f"- Manques : {', '.join(one_seven.get('missing', ())) or 'aucun'}",
              f"- Chiffres jamais observés : {', '.join(report.get('missing_digits', ())) or 'aucun'}", ""]
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
