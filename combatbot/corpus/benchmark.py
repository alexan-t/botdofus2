"""Mesure reproductible de la baseline existante, sans modifier les détecteurs."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from combatbot.corpus.models import Annotation, CorpusEntry, SCHEMA_VERSION
from combatbot.corpus.repository import CorpusRepository


def _prediction(document: dict[str, Any]) -> dict[str, Any]:
    value = document.get("prediction")
    return value if isinstance(value, dict) else document


def _logical(value: object) -> tuple[int, int] | None:
    if isinstance(value, dict) and "x" in value and "y" in value:
        try:
            return int(value["x"]), int(value["y"])
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _cells(prediction: dict[str, Any]) -> list[dict[str, Any]]:
    grid = prediction.get("grid")
    values = grid.get("cells") if isinstance(grid, dict) else None
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def pixel_error(expected: tuple[int, int], actual: tuple[int, int]) -> float:
    """Distance euclidienne sans seuil arbitraire."""
    return ((expected[0] - actual[0]) ** 2 + (expected[1] - actual[1]) ** 2) ** 0.5


def _cell_center(prediction: dict[str, Any], logical: tuple[int, int] | None) -> tuple[int, int] | None:
    if logical is None:
        return None
    for cell in _cells(prediction):
        if _logical(cell.get("logical")) == logical:
            center = cell.get("center")
            if isinstance(center, (list, tuple)) and len(center) == 2:
                return int(center[0]), int(center[1])
    return None


def _counter_metrics(samples: Iterable[tuple[int, int | None, float | None]]) -> dict[str, object]:
    values = list(samples)
    correct = [(truth, predicted, confidence) for truth, predicted, confidence in values if truth == predicted]
    incorrect = [(truth, predicted, confidence) for truth, predicted, confidence in values
                 if predicted is not None and truth != predicted]
    unknown = sum(predicted is None for _truth, predicted, _confidence in values)
    confusion: dict[str, dict[str, int]] = {}
    for truth, predicted, _confidence in values:
        row = confusion.setdefault(str(truth), {})
        key = "UNKNOWN" if predicted is None else str(predicted)
        row[key] = row.get(key, 0) + 1
    correct_confidences = [float(confidence) for _t, _p, confidence in correct if confidence is not None]
    incorrect_confidences = [float(confidence) for _t, _p, confidence in incorrect if confidence is not None]
    return {
        "annotated": len(values),
        "correct": len(correct),
        "incorrect": len(incorrect),
        "unknown": unknown,
        "accuracy": len(correct) / len(values) if values else None,
        "unknown_rate": unknown / len(values) if values else None,
        "confusion_matrix": confusion,
        "errors_1_to_7": sum(truth == 1 and predicted == 7 for truth, predicted, _c in values),
        "errors_7_to_1": sum(truth == 7 and predicted == 1 for truth, predicted, _c in values),
        "mean_confidence_correct": mean(correct_confidences) if correct_confidences else None,
        "mean_confidence_incorrect": mean(incorrect_confidences) if incorrect_confidences else None,
    }


def _binary_metrics(samples: Iterable[tuple[bool, bool | None]]) -> dict[str, object]:
    values = list(samples)
    tp = sum(truth and predicted is True for truth, predicted in values)
    tn = sum(not truth and predicted is False for truth, predicted in values)
    fp = sum(not truth and predicted is True for truth, predicted in values)
    fn = sum(truth and predicted is False for truth, predicted in values)
    unknown = sum(predicted is None for _truth, predicted in values)
    return {
        "annotated": len(values), "true_positive": tp, "true_negative": tn,
        "false_positive": fp, "false_negative": fn, "unknown": unknown,
        "accuracy": (tp + tn) / len(values) if values else None,
        "unknown_rate": unknown / len(values) if values else None,
    }


def _session_metrics(records: list[tuple[CorpusEntry, dict[str, Any], Annotation | None]]) -> dict[str, object]:
    grouped: dict[str, list[tuple[CorpusEntry, dict[str, Any], Annotation | None]]] = defaultdict(list)
    for record in records:
        grouped[record[0].session_id].append(record)
    pairs = 0
    jaccards: list[float] = []
    disappeared = appeared = renumbered = origin_changes = player_moves = enemy_changes = 0
    for session_records in grouped.values():
        ordered = sorted(session_records, key=lambda item: item[0].frame_index)
        for previous, current in zip(ordered, ordered[1:]):
            if current[0].frame_index <= previous[0].frame_index:
                continue
            pairs += 1
            before, after = _prediction(previous[1]), _prediction(current[1])
            before_cells, after_cells = _cells(before), _cells(after)
            before_ids = {_logical(item.get("logical")) for item in before_cells} - {None}
            after_ids = {_logical(item.get("logical")) for item in after_cells} - {None}
            union = before_ids | after_ids
            jaccards.append(len(before_ids & after_ids) / len(union) if union else 1.0)
            disappeared += len(before_ids - after_ids)
            appeared += len(after_ids - before_ids)
            before_by_center = {tuple(item["center"]): _logical(item.get("logical")) for item in before_cells
                                if isinstance(item.get("center"), list) and len(item["center"]) == 2}
            after_by_center = {tuple(item["center"]): _logical(item.get("logical")) for item in after_cells
                               if isinstance(item.get("center"), list) and len(item["center"]) == 2}
            renumbered += sum(before_by_center[key] != after_by_center[key]
                              for key in before_by_center.keys() & after_by_center.keys())
            before_origin, after_origin = _cell_center(before, (0, 0)), _cell_center(after, (0, 0))
            if before_origin is not None and after_origin is not None and before_origin != after_origin:
                origin_changes += 1
            if _logical(before.get("player_cell")) != _logical(after.get("player_cell")):
                player_moves += 1
            before_enemy = sorted(filter(None, (_logical(item.get("cell")) for item in before.get("enemies", ())
                                                 if isinstance(item, dict))))
            after_enemy = sorted(filter(None, (_logical(item.get("cell")) for item in after.get("enemies", ())
                                                if isinstance(item, dict))))
            if before_enemy != after_enemy:
                enemy_changes += 1
    return {
        "sessions": len(grouped), "successive_pairs": pairs,
        "mean_identifier_jaccard": mean(jaccards) if jaccards else None,
        "cells_disappeared": disappeared, "cells_appeared": appeared,
        "same_center_renumbered": renumbered, "logical_origin_pixel_changes": origin_changes,
        "predicted_player_moves": player_moves, "predicted_enemy_set_changes": enemy_changes,
        "renumbering_scope": "centres pixel strictement identiques seulement",
    }


def run_benchmark(repository: CorpusRepository) -> dict[str, object]:
    manifest = repository.load_manifest()
    issues: list[dict[str, str]] = []
    records: list[tuple[CorpusEntry, dict[str, Any], Annotation | None]] = []
    ap_samples: list[tuple[int, int | None, float | None]] = []
    mp_samples: list[tuple[int, int | None, float | None]] = []
    combat_samples: list[tuple[bool, bool | None]] = []
    turn_samples: list[tuple[bool, bool | None]] = []
    cell_counts: list[int] = []
    grid_confidences: list[float] = []
    player_result = {"correct": 0, "incorrect": 0, "unknown": 0, "annotated": 0}
    player_pixel_errors: list[float] = []
    enemy_result = {"correct": 0, "missed": 0, "false_positive": 0, "annotated_frames": 0}
    reference_result = {"expected": 0, "missed": 0, "false_positive": 0, "complete_frames": 0}

    for entry in manifest.entries:
        for issue in repository.validate_files(entry):
            issues.append({"observation_id": entry.observation_id, "issue": issue})
        try:
            document = repository.read_observation(entry)
        except ValueError as exc:
            issues.append({"observation_id": entry.observation_id, "issue": str(exc)})
            continue
        try:
            annotation = repository.read_annotation(entry)
        except ValueError as exc:
            issues.append({"observation_id": entry.observation_id, "issue": str(exc)})
            annotation = None
        prediction = _prediction(document)
        records.append((entry, document, annotation))
        cells = _cells(prediction)
        cell_counts.append(len(cells))
        grid = prediction.get("grid")
        if isinstance(grid, dict) and isinstance(grid.get("confidence"), (int, float)):
            grid_confidences.append(float(grid["confidence"]))
        if annotation is None:
            continue
        if annotation.ap_truth is not None:
            ap_samples.append((annotation.ap_truth,
                               int(prediction["ap"]) if isinstance(prediction.get("ap"), int) else None,
                               float(prediction["confidence_ap"]) if isinstance(prediction.get("confidence_ap"), (int, float)) else None))
        if annotation.mp_truth is not None:
            mp_samples.append((annotation.mp_truth,
                               int(prediction["mp"]) if isinstance(prediction.get("mp"), int) else None,
                               float(prediction["confidence_mp"]) if isinstance(prediction.get("confidence_mp"), (int, float)) else None))
        if annotation.combat_truth is not None:
            predicted_combat = prediction.get("combat_detected")
            combat_samples.append((annotation.combat_truth,
                                   predicted_combat if isinstance(predicted_combat, bool) else None))
        if annotation.player_turn_truth is not None:
            predicted_turn = prediction.get("player_turn")
            turn_samples.append((annotation.player_turn_truth,
                                 predicted_turn if isinstance(predicted_turn, bool) else None))
        if annotation.player is not None:
            player_result["annotated"] += 1
            predicted_player = _logical(prediction.get("player_cell"))
            if predicted_player is None:
                player_result["unknown"] += 1
            elif annotation.player.logical is not None:
                player_result["correct" if predicted_player == annotation.player.logical else "incorrect"] += 1
            center = _cell_center(prediction, predicted_player)
            if center is not None:
                player_pixel_errors.append(pixel_error(annotation.player.center, center))
        if annotation.enemies:
            enemy_result["annotated_frames"] += 1
            expected = {item.logical for item in annotation.enemies if item.logical is not None}
            actual = {_logical(item.get("cell")) for item in prediction.get("enemies", ())
                      if isinstance(item, dict)} - {None}
            if expected:
                enemy_result["correct"] += len(expected & actual)
                enemy_result["missed"] += len(expected - actual)
                enemy_result["false_positive"] += len(actual - expected)
        reference_logical = {item.logical for item in annotation.reference_cells if item.logical is not None}
        if reference_logical:
            actual_cells = {_logical(item.get("logical")) for item in cells} - {None}
            reference_result["expected"] += len(reference_logical)
            reference_result["missed"] += len(reference_logical - actual_cells)
            if annotation.reference_cells_complete:
                reference_result["complete_frames"] += 1
                reference_result["false_positive"] += len(actual_cells - reference_logical)

    annotated_count = sum(annotation is not None for _entry, _doc, annotation in records)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "corpus": {
            "manifest_entries": len(manifest.entries), "readable_observations": len(records),
            "annotated_observations": annotated_count,
            "usage_counts": {usage: sum(entry.usage == usage for entry in manifest.entries)
                             for usage in ("train", "validation", "test", "diagnostic")},
        },
        "ap": _counter_metrics(ap_samples),
        "mp": _counter_metrics(mp_samples),
        "states": {"combat": _binary_metrics(combat_samples), "player_turn": _binary_metrics(turn_samples)},
        "grid": {
            "observations": len(cell_counts),
            "mean_detected_cells": mean(cell_counts) if cell_counts else None,
            "min_detected_cells": min(cell_counts) if cell_counts else None,
            "max_detected_cells": max(cell_counts) if cell_counts else None,
            "mean_confidence": mean(grid_confidences) if grid_confidences else None,
            "player_cell": player_result,
            "player_center_error_px_mean": mean(player_pixel_errors) if player_pixel_errors else None,
            "player_center_error_px_max": max(player_pixel_errors) if player_pixel_errors else None,
            "enemies_logical": enemy_result,
            "reference_cells": reference_result,
            "false_positive_note": "Calculé uniquement lorsque reference_cells_complete=true.",
        },
        "sessions": _session_metrics(records),
        "issues": issues,
    }


def _percent(value: object) -> str:
    return "N/A" if value is None else f"{float(value):.1%}"


def _number(value: object, digits: int = 2) -> str:
    return "N/A" if value is None else f"{float(value):.{digits}f}"


def markdown_report(report: dict[str, Any]) -> str:
    corpus, ap, mp = report["corpus"], report["ap"], report["mp"]
    grid, sessions, states = report["grid"], report["sessions"], report["states"]
    lines = [
        "# Baseline du corpus PythonBot", "",
        f"Généré le `{report['generated_at']}`.", "",
        "## Corpus", "",
        f"- Entrées du manifeste : **{corpus['manifest_entries']}**",
        f"- Observations lisibles : **{corpus['readable_observations']}**",
        f"- Observations annotées : **{corpus['annotated_observations']}**", "",
        "## PA", "",
        f"- Vérités disponibles : **{ap['annotated']}**",
        f"- Accuracy exacte : **{_percent(ap['accuracy'])}**",
        f"- UNKNOWN : **{ap['unknown']}** ({_percent(ap['unknown_rate'])})",
        f"- 1 → 7 : **{ap['errors_1_to_7']}**",
        f"- 7 → 1 : **{ap['errors_7_to_1']}**",
        f"- Confiance moyenne correcte : **{_percent(ap['mean_confidence_correct'])}**",
        f"- Confiance moyenne incorrecte : **{_percent(ap['mean_confidence_incorrect'])}**", "",
        "## PM", "",
        f"- Vérités disponibles : **{mp['annotated']}**",
        f"- Accuracy exacte : **{_percent(mp['accuracy'])}**",
        f"- UNKNOWN : **{mp['unknown']}** ({_percent(mp['unknown_rate'])})",
        f"- 1 → 7 : **{mp['errors_1_to_7']}**",
        f"- 7 → 1 : **{mp['errors_7_to_1']}**",
        f"- Confiance moyenne correcte : **{_percent(mp['mean_confidence_correct'])}**",
        f"- Confiance moyenne incorrecte : **{_percent(mp['mean_confidence_incorrect'])}**", "",
        "## Grille", "",
        f"- Cellules détectées, moyenne : **{_number(grid['mean_detected_cells'])}**",
        f"- Cellules détectées, min/max : **{grid['min_detected_cells'] if grid['min_detected_cells'] is not None else 'N/A'} / {grid['max_detected_cells'] if grid['max_detected_cells'] is not None else 'N/A'}**",
        f"- Confiance moyenne : **{_percent(grid['mean_confidence'])}**",
        f"- Cellule joueur correcte / incorrecte / inconnue : **{grid['player_cell']['correct']} / {grid['player_cell']['incorrect']} / {grid['player_cell']['unknown']}**",
        f"- Erreur moyenne du centre joueur : **{_number(grid['player_center_error_px_mean'])} px**",
        f"- Ennemis corrects / manqués / faux positifs : **{grid['enemies_logical']['correct']} / {grid['enemies_logical']['missed']} / {grid['enemies_logical']['false_positive']}**", "",
        "## Stabilité entre frames", "",
        f"- Sessions : **{sessions['sessions']}** ; paires successives : **{sessions['successive_pairs']}**",
        f"- Jaccard moyen des identifiants : **{_percent(sessions['mean_identifier_jaccard'])}**",
        f"- Cellules disparues / apparues : **{sessions['cells_disappeared']} / {sessions['cells_appeared']}**",
        f"- Centres identiques renumérotés : **{sessions['same_center_renumbered']}**",
        f"- Changements pixel de l'origine logique : **{sessions['logical_origin_pixel_changes']}**", "",
        "## États", "",
        f"- Combat : accuracy **{_percent(states['combat']['accuracy'])}**, FP **{states['combat']['false_positive']}**, FN **{states['combat']['false_negative']}**, UNKNOWN **{states['combat']['unknown']}**",
        f"- Tour joueur : accuracy **{_percent(states['player_turn']['accuracy'])}**, FP **{states['player_turn']['false_positive']}**, FN **{states['player_turn']['false_negative']}**, UNKNOWN **{states['player_turn']['unknown']}**", "",
        "## Matrices de confusion", "",
        "```json", json.dumps({"ap": ap["confusion_matrix"], "mp": mp["confusion_matrix"]}, ensure_ascii=False, indent=2), "```", "",
        "## Problèmes de corpus", "",
    ]
    if report["issues"]:
        lines.extend(f"- `{item['observation_id']}` : {item['issue']}" for item in report["issues"])
    else:
        lines.append("Aucun problème de fichier détecté.")
    lines.extend(["", "Les valeurs `N/A` correspondent à une vérité terrain absente ; aucune métrique n'a été inventée.", ""])
    return "\n".join(lines)


def write_reports(report: dict[str, object], output_directory: Path) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "latest.json"
    markdown_path = output_directory / "latest.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, markdown_path
