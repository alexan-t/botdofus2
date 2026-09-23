"""Banc LOT 3B-3 sur le corpus réel 3B-2R (tag ``lot3b2r``), sans nouvelle capture.

Vérité terrain : annotations humaines existantes uniquement.
* ``combat_truth`` de l'annotation (issue du mode déclaré par l'utilisateur pendant la recette) ;
* grille visible ⇔ placement/combat (tags ``mode_*`` de la recette) : DOFUS ne dessine la grille
  qu'en combat, fait observé sur les 26 captures.
Les anciennes prédictions (0.4.0) servent seulement de point de comparaison « avant ».

Absence de corpus, de tag ou de dossier client : ``available = False``, sans erreur.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable

import cv2

from combatbot.corpus.repository import CorpusRepository
from combatbot.vision.gamedata_grid import projected_observation
from combatbot.vision.grid_fit import candidate_union
from combatbot.vision.grid_projection import GridProjector, GridScreenTransform
from combatbot.vision.grid_validation import (
    CombatStateDetector, GridAlignmentValidator, GridVisibilityDetector, GridVisibilityState, MapConsistencyTracker,
)

TAG = "lot3b2r"


def _confusion(rows: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = defaultdict(dict)
    for truth, predicted in rows:
        matrix[truth][predicted] = matrix[truth].get(predicted, 0) + 1
    return {k: dict(v) for k, v in matrix.items()}


def _rate(rows: list[tuple[str, str]], truth: str, predicted: str) -> float | None:
    subset = [p for t, p in rows if t == truth]
    return sum(p == predicted for p in subset) / len(subset) if subset else None


def run_grid_validation_benchmark(repository: CorpusRepository,
                                  topology_for: Callable[[int], Any] | None) -> dict[str, Any]:
    if topology_for is None:
        return {"available": False, "reason": "dossier client GameData non configuré"}
    try:
        entries = [e for e in repository.list_entries() if TAG in e.tags]
    except (OSError, ValueError) as exc:
        return {"available": False, "reason": f"corpus illisible : {exc}"}
    if not entries:
        return {"available": False, "reason": f"aucune observation taguée {TAG}"}
    visibility_detector, validator, combat = GridVisibilityDetector(), GridAlignmentValidator(), CombatStateDetector()
    frames, issues = [], []
    for entry in sorted(entries, key=lambda e: (e.session_id, e.frame_index)):
        try:
            document = repository.read_observation(entry)
            annotation = repository.read_annotation(entry)
            prediction = document["prediction"]
            grid_raw = prediction["grid"]
            image = cv2.imread(str(repository.resolve(entry.paths["frame"])))
            if image is None or not grid_raw.get("transform") or grid_raw.get("map_id_declared") is None:
                raise ValueError("frame, transform ou map ID absent")
        except (OSError, ValueError, KeyError) as exc:
            issues.append({"observation_id": entry.observation_id, "issue": str(exc)})
            continue
        mode = next((t[5:] for t in entry.tags if t.startswith("mode_")), None)
        frames.append({"entry": entry, "annotation": annotation, "prediction": prediction, "image": image,
                       "mode": mode, "map_id": int(grid_raw["map_id_declared"]),
                       "source": grid_raw.get("map_id_source"), "stale": "projection_stale_map" in entry.tags,
                       "transform": GridScreenTransform.from_dict(grid_raw["transform"])})

    def evaluate(frame: dict, map_id: int):
        topology = topology_for(map_id)
        projected = GridProjector(frame["transform"]).project(topology)
        image = frame["image"]
        grid = projected_observation(projected, image)
        candidates = frame.setdefault("candidates", candidate_union(image))
        visibility, inliers, outliers = visibility_detector.observe(
            projected, candidates, (image.shape[1], image.shape[0]), grid.topology_consistency)
        return grid, visibility, validator.validate(projected, visibility, inliers, outliers)

    visibility_rows, combat_before, combat_after, per_frame = [], [], [], []
    alignment_rows = []
    for frame in frames:
        grid, visibility, alignment = evaluate(frame, frame["map_id"])
        frame["visibility"], frame["consistency"] = visibility, grid.topology_consistency
        truth_visible = "VISIBLE" if frame["mode"] in ("placement", "combat") else "NOT_VISIBLE"
        visibility_rows.append((truth_visible, visibility.state.value))
        annotation = frame["annotation"]
        truth_combat = None if annotation is None or annotation.combat_truth is None else \
            ("COMBAT" if annotation.combat_truth else "EXPLORATION")
        before = frame["prediction"].get("combat_detected")
        signals = frame["prediction"].get("signals", {})
        after = combat.detect(grid_source="GAMEDATA_PROJECTED", visibility=visibility,
                              grid_confidence=float(frame["prediction"]["grid"].get("confidence") or 0.0),
                              hud={k: float(signals.get(k, 0.0)) for k in ("counters", "end_turn", "spell_bar")})
        if truth_combat is not None:
            combat_before.append((truth_combat, "COMBAT" if before else "EXPLORATION"))
            combat_after.append((truth_combat, after.state.value))
        if truth_visible == "VISIBLE" and not frame["stale"]:
            alignment_rows.append({
                "observation_id": frame["entry"].observation_id, "map_id": frame["map_id"],
                "status": alignment.status.value, "coverage": alignment.coverage,
                "inliers": alignment.inlier_count, "residual_median_px": alignment.residual["median"],
                "residual_max_px": alignment.residual["max"],
                "suggested_correction": alignment.suggested_correction.to_dict() if alignment.suggested_correction else None})
        per_frame.append({"observation_id": frame["entry"].observation_id, "mode": frame["mode"],
                          "map_id": frame["map_id"], "stale_tag": frame["stale"],
                          "visibility": visibility.state.value, "inliers": visibility.inlier_count,
                          "regions": f"{len(visibility.supported_regions)}/{len(visibility.expected_regions)}",
                          "consistency": grid.topology_consistency, "combat_before": before,
                          "combat_after": after.state.value, "alignment": alignment.status.value})

    # Stale map, simulated on real gridded frames: map A frames (correct), then map B frames
    # evaluated with A's topology, as if the user had not redeclared the map.
    gridded = [f for f in frames if f["mode"] in ("placement", "combat") and not f["stale"]]
    order = list(dict.fromkeys(f["map_id"] for f in gridded))
    stale_rows, correct_rows, sequences = [], [], []
    for previous, current in zip(order, order[1:]):
        tracker = MapConsistencyTracker()
        sequence = {"declared_map": previous, "screen_map": current, "correct_states": [], "stale_states": []}
        for frame in [f for f in gridded if f["map_id"] == previous]:
            state = tracker.update(previous, frame["source"], frame["visibility"].state, frame["consistency"])
            sequence["correct_states"].append(state.state.value)
            correct_rows.append(state.state.value)
        for frame in [f for f in gridded if f["map_id"] == current]:
            grid, visibility, _ = evaluate(frame, previous)
            state = tracker.update(previous, frame["source"], visibility.state, grid.topology_consistency)
            sequence["stale_states"].append({"state": state.state.value, "delta": state.delta_from_baseline,
                                             "consistency": grid.topology_consistency})
            stale_rows.append(state.state.value)
        sequences.append(sequence)
    exploration_map_states = []
    for frame in [f for f in frames if f["mode"] == "exploration"]:
        tracker = MapConsistencyTracker()
        exploration_map_states.append(tracker.update(frame["map_id"], frame["source"], frame["visibility"].state,
                                                     frame["consistency"]).state.value)

    def fp_rate(rows):
        return _rate(rows, "EXPLORATION", "COMBAT")

    residuals = [r["residual_median_px"] for r in alignment_rows if r["residual_median_px"] is not None]
    coverages = [r["coverage"] for r in alignment_rows if r["coverage"] is not None]
    return {
        "available": True, "frames": len(frames), "issues": issues,
        "modes": dict(Counter(f["mode"] for f in frames)),
        "grid_visibility_confusion_matrix": _confusion(visibility_rows),
        "combat_confusion_matrix_before_0_4_0": _confusion(combat_before),
        "combat_confusion_matrix_after": _confusion(combat_after),
        "combat_false_positive_rate_before": fp_rate(combat_before),
        "combat_false_positive_rate_after": fp_rate(combat_after),
        "combat_recall_after": _rate(combat_after, "COMBAT", "COMBAT"),
        "combat_recall_before": _rate(combat_before, "COMBAT", "COMBAT"),
        "alignment": {"frames": len(alignment_rows), "status": dict(Counter(r["status"] for r in alignment_rows)),
                      "residual_median_px": {"median": median(residuals) if residuals else None,
                                             "max": max(residuals) if residuals else None},
                      "coverage": {"mean": mean(coverages) if coverages else None,
                                   "min": min(coverages) if coverages else None},
                      "runtime_adjustment_suggested": sum(r["suggested_correction"] is not None for r in alignment_rows),
                      "rows": alignment_rows},
        "stale_map": {"correct_states": dict(Counter(correct_rows)), "simulated_stale_states": dict(Counter(stale_rows)),
                      "exploration_states": dict(Counter(exploration_map_states)), "sequences": sequences},
        "per_frame": per_frame,
    }


def markdown_summary(report: dict[str, Any]) -> str:
    if not report.get("available"):
        return f"# Banc LOT 3B-3\n\nNon disponible : {report.get('reason')}\n"
    lines = ["# Banc LOT 3B-3 (corpus 3B-2R)", "",
             f"- Frames : {report['frames']} {report['modes']}",
             f"- Visibilité de grille : `{report['grid_visibility_confusion_matrix']}`",
             f"- Combat avant (0.4.0) : `{report['combat_confusion_matrix_before_0_4_0']}` "
             f"FP={report['combat_false_positive_rate_before']}",
             f"- Combat après : `{report['combat_confusion_matrix_after']}` FP={report['combat_false_positive_rate_after']}",
             f"- Alignement : {report['alignment']['status']} ; résidu médian {report['alignment']['residual_median_px']}",
             f"- Map périmée : correct {report['stale_map']['correct_states']} ; simulée "
             f"{report['stale_map']['simulated_stale_states']} ; exploration {report['stale_map']['exploration_states']}", ""]
    return "\n".join(lines)


def topology_source_for(client_dir: str | Path | None, cache_dir: Path) -> Callable[[int], Any] | None:
    if not client_dir or not Path(client_dir).is_dir():
        return None
    from combatbot.vision.gamedata_grid import GameDataTopologySource
    try:
        source = GameDataTopologySource.for_client(client_dir, cache_dir)
    except Exception:  # noqa: BLE001 - any GameData failure means "not available"
        return None
    return source.topology
