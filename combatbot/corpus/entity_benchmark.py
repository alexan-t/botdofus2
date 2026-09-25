"""Banc LOT 3B-5 : entités par cellule et suivi global, sur vérité humaine uniquement.

``python -m combatbot.benchmark --entities`` :

- inventaire des observations à grille GameData projetée portant une vérité entités
  ``human_confirmed`` (jamais une ancienne prédiction) ;
- split par combat (session + map déclarée), registre ``entity_split_registry.json``, TEST gelé ;
- profils d'équipe dérivés de TRAIN seulement ;
- BEFORE (``classify_cell_occupancy``) vs AFTER (``CellEntityDetector`` + ``EntityTracker``) ;
- UNKNOWN rapporté à part : une réponse prudente n'est pas une fausse détection.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
from statistics import mean, median
import time
from typing import Any

import cv2
import numpy as np

from combatbot.corpus.entity_split import cover_layouts, layout_digest
from combatbot.corpus.hud_dataset import _assign_splits
from combatbot.corpus.repository import CorpusRepository
from combatbot.gamedata.models import GridCoordinate
from combatbot.models import Cell
from combatbot.vision.background_model import CellBackgroundModel
from combatbot.vision.combat_grid import classify_cell_occupancy
from combatbot.vision.combat_models import GRID_SOURCE_GAMEDATA, CombatGridObservation, ObservedCell
from combatbot.vision.entity_detector import (
    CellEntityDetector, DetectionContext, _circular_hue, partial_ring_features,
)
from combatbot.vision.entity_geometry import build_roi_maps
from combatbot.vision.entity_models import (
    EntityKind, MarkerColorClass, PlayerVisualProfileV2, TeamMarkerProfile, TrackState, VisualProfiles,
)
from combatbot.vision.entity_profiles import (
    MIN_FULL_RING_SAMPLES, ProfileError, _hue_class, cell_pixels, circular_hue_mean, enrichment_hue,
    frame_pixels, gate_partial, hue_deviation, player_profile_v2, save_team_profile, save_train_player_profile,
    stroke_hues, team_class_from_train,
)
from combatbot.vision.entity_tracker import EntityTracker, greedy_assign, grid_distance, hungarian, INFINITE

SPLIT_REGISTRY = "entity_split_registry.json"


def grid_from_document(document: dict) -> CombatGridObservation | None:
    snapshot = document.get("grid_snapshot") or {}
    if snapshot.get("grid_source") != GRID_SOURCE_GAMEDATA:
        return None
    cells = []
    for raw in snapshot.get("cells", ()):
        if raw.get("cell_id") is None or not raw.get("polygon"):
            continue
        logical = raw.get("logical")
        cell = Cell(int(logical["x"]), int(logical["y"])) if isinstance(logical, dict) else Cell(*logical)
        coordinate = raw.get("grid_coordinate")
        cells.append(ObservedCell(
            cell, tuple(raw["center"]), tuple(tuple(point) for point in raw["polygon"]),
            cell_id=int(raw["cell_id"]),
            grid_coordinate=GridCoordinate(coordinate["x"], coordinate["y"]) if isinstance(coordinate, dict) else None,
            walkable_static=raw.get("walkable_static"),
            non_walkable_during_fight_static=raw.get("non_walkable_during_fight_static"),
            los_static=raw.get("los_static")))
    capture = document.get("capture") or {}
    prediction_grid = (document.get("prediction") or {}).get("grid") or {}
    return CombatGridObservation(
        tuple(cells), prediction_grid.get("cell_width"), prediction_grid.get("cell_height"), 0.0,
        float(snapshot.get("confidence") or 0.0), GRID_SOURCE_GAMEDATA, snapshot.get("map_id_declared"),
        grid_visibility={"state": capture.get("grid_visibility_state")} if capture.get("grid_visibility_state") else None,
        alignment={"status": capture.get("alignment_status")} if capture.get("alignment_status") else None)


@dataclass(frozen=True)
class EntitySample:
    observation_id: str
    session_id: str
    frame_index: int
    map_id: int | None
    group_id: str
    split: str
    frame_path: Path
    player_cell: int | None
    player_visible: bool
    enemies: tuple[tuple[int, str | None], ...]
    occluded_tracks: tuple[str, ...]
    empty_cells: tuple[int, ...]
    frame_phase: str | None
    layout_signature: str | None
    analysis_ms: float | None
    document: dict
    tracking_identity_confirmed: bool = False
    tracking_identity_source: str | None = None
    tracking_confirmed_at: str | None = None
    tracking_sequence_id: str | None = None
    sampled_cells: tuple[tuple[int, str], ...] = ()
    split_declared: bool = False


DECLARABLE_SPLITS = ("train", "validation", "test")


def declared_split(document: dict) -> str | None:
    """LOT 3B-5D : split choisi par l'utilisateur au démarrage de la capture du combat."""
    value = (document.get("capture") or {}).get("entity_split_declared")
    return value if value in DECLARABLE_SPLITS else None


def _declared_groups(rows) -> dict[str, str | None]:
    """Un combat entier appartient à un seul split : toute frame divergente est une erreur."""
    values: dict[str, set] = defaultdict(set)
    for entry, _annotation, document in rows:
        values[_group(entry, document)].add(declared_split(document))
    result = {}
    for group, splits in values.items():
        if len(splits) > 1:
            raise ValueError(f"Split déclaré incohérent dans le combat {group} : {sorted(map(str, splits))}")
        result[group] = next(iter(splits))
    return result


def _group(entry, document) -> str:
    snapshot = document.get("grid_snapshot") or {}
    return f"{entry.session_id}|map{snapshot.get('map_id_declared')}"


def _registry(repository: CorpusRepository) -> dict[str, Any]:
    from combatbot.entity_runtime import active_profile_directory
    runtime_registry = active_profile_directory(repository.root.parent) / "entity_split_registry.v2.json"
    path = runtime_registry if runtime_registry.is_file() else repository.manifests / SPLIT_REGISTRY
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schema_version": 1, "groups": {}}


def entity_inventory(repository: CorpusRepository, *, freeze: bool = True,
                     layout_coverage: bool = True, registry_override: dict | None = None,
                     declared_only: bool = False) -> list[EntitySample]:
    """Observations annotées (human_confirmed) ; split par combat, TEST gelé une fois attribué.

    ``layout_coverage`` (LOT 3B-5B) : chaque disposition annotée reçoit si possible un groupe TRAIN,
    jamais pris dans TEST. ``False`` sert uniquement à reproduire la baseline historique.
    LOT 3B-5D : un split déclaré à la capture s'impose (jamais déplacé par la couverture de layout) ;
    un désaccord avec le registre est une erreur. ``declared_only`` écarte les combats non déclarés.
    """
    rows = []
    for entry in repository.list_entries():
        annotation = repository.read_annotation(entry)
        if annotation is None or not annotation.entities_confirmed:
            continue
        document = repository.read_observation(entry)
        if grid_from_document(document) is None:
            continue
        rows.append((entry, annotation, document))
    declared = _declared_groups(rows)
    if declared_only:
        rows = [row for row in rows if declared[_group(row[0], row[2])]]
    registry = registry_override if registry_override is not None else _registry(repository)
    groups = {_group(entry, document) for entry, _annotation, document in rows}
    for group in groups:
        known = registry.get("groups", {}).get(group)
        if declared[group] and known and known != declared[group]:
            raise ValueError(f"Combat {group} : split déclaré {declared[group]} ≠ registre {known}")
    layouts = defaultdict(set)
    for entry, _annotation, document in rows:
        if declared[_group(entry, document)]:
            continue
        signature = ((document.get("capture") or {}).get("calibration") or {}).get("layout_signature")
        layouts[layout_digest(signature)].add(_group(entry, document))
    if layout_coverage:
        registry = cover_layouts(registry, layouts)
    fixed = {group: split for group, split in registry["groups"].items() if group in groups}
    fixed.update({group: declared[group] for group in groups if declared[group]})
    splits = _assign_splits({group: set() for group in groups}, fixed) if groups else {}
    if freeze and groups:
        # Une fois attribué, un groupe TEST reste TEST ; les autres groupes gardent leur split.
        registry["groups"] = {**registry["groups"], **splits}
        repository.manifests.mkdir(parents=True, exist_ok=True)
        (repository.manifests / SPLIT_REGISTRY).write_text(json.dumps(registry, indent=2), encoding="utf-8")
    samples = []
    for entry, annotation, document in rows:
        capture = document.get("capture") or {}
        calibration = capture.get("calibration") or {}
        samples.append(EntitySample(
            entry.observation_id, entry.session_id, entry.frame_index,
            (document.get("grid_snapshot") or {}).get("map_id_declared"), _group(entry, document),
            splits[_group(entry, document)], repository.resolve(entry.paths["frame"]),
            annotation.player_cell_id_truth, annotation.player_visibility == "VISIBLE",
            tuple((int(item["cell_id"]), item.get("track_id")) for item in annotation.enemy_cells_truth),
            annotation.enemy_occluded_tracks, annotation.empty_confirmed_cells, annotation.frame_phase,
            calibration.get("layout_signature"), capture.get("analysis_ms"), document,
            bool(annotation.tracking_identity_confirmed and annotation.tracking_identity_source),
            annotation.tracking_identity_source, annotation.tracking_confirmed_at,
            annotation.tracking_sequence_id,
            tuple((int(item["cell_id"]), str(item["label"])) for item in annotation.sampled_cells_truth),
            declared[_group(entry, document)] is not None))
    return sorted(samples, key=lambda item: (item.session_id, item.frame_index))


# ---------------------------------------------------------------------------- profils (TRAIN)
def _grid_trusted(grid: CombatGridObservation) -> bool:
    return grid.grid_visibility_state == "VISIBLE" and (grid.alignment or {}).get("status") == "ALIGNED"


def _grid_unrecorded(grid: CombatGridObservation) -> bool:
    """Corpus antérieur à 3B-3 : l'état de grille n'a jamais été enregistré (inconnu, pas refusé)."""
    return grid.grid_visibility_state is None and not (grid.alignment or {}).get("status")


def _team_cells(sample: EntitySample) -> dict[str, list[int]]:
    return {"player": [sample.player_cell] if sample.player_cell is not None else [],
            "enemy": [cell_id for cell_id, _track in sample.enemies]}


def build_profiles(samples: list[EntitySample], detector: CellEntityDetector) -> tuple[VisualProfiles, dict]:
    """Profils d'un layout, depuis ses vérités TRAIN human_confirmed uniquement (LOT 3B-5B).

    1. Classes d'équipe : anneaux complets (≥ 3) sinon enrichissement anneau/extérieur ;
       tolérance pixel = p95 des écarts + 1.
    2. Anneau partiel autorisé par équipe seulement s'il retrouve ≥ 3 vérités de l'équipe sans
       aucun déclenchement sur une vérité contraire.
    3. Profil joueur multi-exemples ; chaque exemple refusé l'est avec sa raison.
    """
    train = [sample for sample in samples if sample.split == "train" and sample.layout_signature]
    layouts = {sample.layout_signature for sample in train}
    if len(layouts) > 1:
        raise ValueError("Construire les profils séparément pour chaque layout")
    layout_signature = next(iter(layouts), None)
    config = detector.config
    frames = []
    for sample in train:
        image = cv2.imread(str(sample.frame_path), cv2.IMREAD_COLOR)
        grid = grid_from_document(sample.document)
        if image is None or grid is None:
            continue
        maps = build_roi_maps(grid.cells, image.shape[:2])
        features = detector.cell_features(image, maps)
        _hsv, chroma = frame_pixels(image, maps)
        frames.append((sample, grid, maps, features, chroma))

    full = {"player": [], "enemy": []}
    strokes = {"player": [], "enemy": []}
    enrich = {team: {"ring": [], "outer": [], "inner": [], "ring_total": 0, "outer_total": 0, "inner_total": 0,
                     "cells": 0} for team in full}
    for sample, _grid, maps, features, chroma in frames:
        hsv = features["hsv"]
        saturated = (hsv[:, 1] >= config.partial_min_saturation) & (hsv[:, 2] >= config.partial_min_value)
        for team, cells in _team_cells(sample).items():
            for cell_id in cells:
                index = maps.index_of(cell_id)
                if index is None:
                    continue
                hue = _circular_hue(float(features["sum_cos"][index]), float(features["sum_sin"][index]))
                if hue is not None and features["peak"][index] >= config.peak_presence:
                    full[team].append(hue)
                    strokes[team].append(stroke_hues(hsv, chroma, maps, index, config.stroke_delta))
                for band in ("ring", "outer", "inner"):
                    pixels = cell_pixels(getattr(maps, f"partial_{band}"), index)
                    enrich[team][band].append(hsv[pixels[saturated[pixels]], 0])
                    enrich[team][f"{band}_total"] += len(pixels)
                enrich[team]["cells"] += 1
    classes: dict[str, MarkerColorClass | None] = {}
    team_diagnostics: dict[str, dict] = {}
    for team in ("player", "enemy"):
        data = enrich[team]
        enrichment = None
        if len(full[team]) < MIN_FULL_RING_SAMPLES and data["cells"]:
            enrichment = enrichment_hue(np.concatenate(data["ring"]), data["ring_total"],
                                        [(np.concatenate(data[band]), data[f"{band}_total"])
                                         for band in ("outer", "inner")])
        pixels = np.concatenate(strokes[team]) if strokes[team] else np.zeros(0)
        classes[team] = team_class_from_train(full[team], pixels, enrichment, data["cells"])
        spread = None
        if full[team]:
            mean_hue = circular_hue_mean(full[team])
            spread = float(np.sqrt(np.mean(hue_deviation(full[team], mean_hue) ** 2)))
        team_diagnostics[team] = {"annotated_cells": data["cells"], "full_ring_samples": len(full[team]),
                                  "full_ring_hue_spread": spread, "stroke_pixels": int(len(pixels)),
                                  "enrichment": enrichment}
    if classes["player"] and classes["enemy"] and classes["player"].hue_distance(classes["enemy"].hue) <= max(
            classes["player"].hue_tolerance, classes["enemy"].hue_tolerance):
        team_diagnostics["error"] = "Couleurs joueur et ennemi indiscernables : profil d'équipe refusé"
        classes = {"player": None, "enemy": None}

    # Autorisation de l'anneau partiel : décidée sur les vérités TRAIN de ce layout.
    for team, other in (("player", "enemy"), ("enemy", "player")):
        marker = classes[team]
        if marker is None or marker.pixel_tolerance is None:
            continue
        positives = contrary = 0
        for sample, grid, maps, features, _chroma in frames:
            if not _grid_trusted(grid):
                continue
            values = partial_ring_features(features["hsv"], maps, marker, config)
            fires = values["fires"] & (maps.visibility > 0.5) & (features["center_std"] >= config.center_std_min)
            cells = _team_cells(sample)
            for cell_id in cells[team]:
                index = maps.index_of(cell_id)
                positives += int(index is not None and fires[index])
            for cell_id in [*cells[other], *sample.empty_cells]:
                index = maps.index_of(cell_id)
                contrary += int(index is not None and fires[index])
        classes[team] = gate_partial(marker, positives, contrary)
    teams = None
    if classes["player"] or classes["enemy"]:
        teams = TeamMarkerProfile(classes["player"], classes["enemy"], layout_signature, "human_confirmed",
                                  datetime.now().astimezone().isoformat(timespec="seconds"),
                                  "corpus TRAIN human_confirmed (LOT 3B-5B)")

    # Profil joueur multi-exemples.
    examples: list[tuple[str, tuple[float, ...]]] = []
    rejected: Counter = Counter()
    accepted_hues: list[float] = []
    grid_unrecorded = 0
    player_class = classes["player"]
    for sample, grid, maps, features, _chroma in frames:
        if sample.player_cell is None:
            continue
        index = maps.index_of(sample.player_cell)
        hue = (_circular_hue(float(features["sum_cos"][index]), float(features["sum_sin"][index]))
               if index is not None else None)
        structured = index is not None and (features["inner_drop"][index] >= config.inner_drop_min
                                            or features["center_std"][index] >= config.center_std_min)
        unrecorded = _grid_unrecorded(grid)
        if not sample.player_visible:
            rejected["player_not_visible"] += 1
        elif not _grid_trusted(grid) and not unrecorded:
            rejected["grid_untrusted"] += 1
        elif index is None or maps.visibility[index] <= 0.5:
            rejected["roi_abnormal"] += 1
        elif hue is None or features["peak"][index] < config.peak_presence:
            rejected["marker_unreadable"] += 1
        elif features["lower_sectors"][index] < config.min_lower_sectors or not structured:
            rejected["weak_evidence"] += 1
        elif player_class is not None and not player_class.matches(hue):
            rejected["marker_team_mismatch"] += 1
        else:
            examples.append((sample.observation_id, tuple(float(v) for v in np.concatenate(
                [features["foot_lab"][index], features["center_lab"][index]]))))
            grid_unrecorded += int(unrecorded)
            accepted_hues.append(hue)
    player_profile = None
    player_error = None
    marker = player_class or (_hue_class(accepted_hues) if accepted_hues else None)
    if marker is not None:
        try:
            player_profile = player_profile_v2(examples, rejected, marker, layout_signature=layout_signature)
        except ProfileError as exc:
            player_error = str(exc)
    diagnostics: dict[str, object] = {
        "player_hues": len(full["player"]), "enemy_hues": len(full["enemy"]),
        "player_source": examples[0][0] if examples else None,
        "player_sources": [source for source, _vector in examples],
        "teams": teams.to_dict() if teams else None, "team_measures": team_diagnostics,
        "player_profile": {"accepted": len(examples), "rejected": dict(player_profile.rejected if player_profile
                                                                        else rejected),
                           "prototypes": len(player_profile.prototypes) if player_profile else 0,
                           "dispersion": player_profile.dispersion if player_profile else None,
                           "distance_tolerance": player_profile.distance_tolerance if player_profile else None,
                           "margin": player_profile.margin if player_profile else None,
                           "accepted_with_unrecorded_grid_state": grid_unrecorded,
                           "error": player_error},
    }
    return VisualProfiles(player_profile, teams), diagnostics


# ---------------------------------------------------------------------------- métriques
def _match(predicted: list[int], truth: list[int], radius: int = 1) -> list[tuple[int, int]]:
    if not predicted or not truth:
        return []
    cost = [[float(grid_distance(p, t)) if grid_distance(p, t) <= radius else INFINITE for t in truth]
            for p in predicted]
    return [(predicted[i], truth[j]) for i, j in hungarian(cost)]


class _Counts:
    def __init__(self) -> None:
        self.values: Counter[str] = Counter()
        self.errors: list[float] = []

    def add(self, key: str, amount: int = 1) -> None:
        self.values[key] += amount


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _frame_metrics(sample: EntitySample, player: int | None, enemies: list[int], unknown: list[int],
                   occupancy: dict[int, str], counts: dict[str, _Counts]) -> dict[str, object]:
    p, e, c = counts["player"], counts["enemy"], counts["cells"]
    if sample.player_visible:
        p.add("visible")
        if player is None:
            p.add("unknown")
        elif player == sample.player_cell:
            p.add("correct")
        else:
            p.add("wrong")
    elif player is not None:
        p.add("false_positive")
    else:
        p.add("absent_ok")
    truth = [cell for cell, _track in sample.enemies]
    pairs = _match(enemies, truth)
    exact = sum(pred == true for pred, true in pairs)
    matched_pred = {pred for pred, _true in pairs}
    e.add("truth", len(truth))
    e.add("predicted", len(enemies))
    e.add("matched", len(pairs))
    e.add("exact", exact)
    e.add("false_positive", len([cell for cell in enemies if cell not in matched_pred]))
    e.add("predicted_as_player_cell", sum(cell == sample.player_cell for cell in enemies))
    e.add("unknown_on_truth", sum(cell in truth for cell in unknown))
    e.add("unknown_elsewhere", sum(cell not in truth and cell != sample.player_cell for cell in unknown))
    e.errors.append(abs(len(enemies) - len(truth)))
    entity_cells = set(truth) | ({sample.player_cell} if sample.player_cell is not None else set())
    empty_cells = set(sample.empty_cells)
    c.add("empty_truth", len(empty_cells))
    for cell_id, state in occupancy.items():
        c.add(f"state_{state}")
        if state == "OCCUPIED":
            c.add("occupied_correct" if cell_id in entity_cells else
                  "occupied_wrong" if cell_id in empty_cells else "occupied_unlabelled")
        elif state == "FREE":
            c.add("free_wrong" if cell_id in entity_cells else
                  "free_correct" if cell_id in empty_cells else "free_unlabelled")
    # LOT 3B-5D : FREE mesuré seulement sur des vérités humaines (échantillon aveugle + entités).
    sampled_empty = {cell for cell, label in sample.sampled_cells if label == "EMPTY"}
    sampled_occupied = {cell for cell, label in sample.sampled_cells if label == "OCCUPIED"}
    occupied_truth = entity_cells | sampled_occupied
    c.add("sample_cells", len(sample.sampled_cells))
    c.add("sample_empty_truth", len(sampled_empty))
    c.add("sample_occupied_truth", len(sampled_occupied))
    c.add("sample_unknown_truth", sum(label == "UNKNOWN" for _cell, label in sample.sampled_cells))
    c.add("truth_occupied_cells", len(occupied_truth))
    for cell_id in sampled_empty:
        c.add(f"sample_empty_pred_{occupancy.get(cell_id, 'UNSCORED')}")
    for cell_id in occupied_truth:
        state = occupancy.get(cell_id, "UNSCORED")
        c.add(f"occupied_truth_pred_{state}")
        if state == "FREE":
            c.add("false_free_entity" if cell_id in entity_cells else "false_free_sampled")
    return {"observation_id": sample.observation_id, "split": sample.split, "player_truth": sample.player_cell,
            "player_pred": player, "enemies_truth": truth, "enemies_pred": enemies, "unknown": unknown}


def _summaries(counts: dict[str, _Counts]) -> dict[str, object]:
    p, e, c = counts["player"].values, counts["enemy"].values, counts["cells"].values
    occupied = c["occupied_correct"] + c["occupied_wrong"]
    free = c["free_correct"] + c["free_wrong"]
    cells = sum(value for key, value in c.items() if key.startswith("state_"))
    return {
        "player": {"frames_visible": p["visible"], "correct": p["correct"], "wrong": p["wrong"],
                   "unknown": p["unknown"], "false_positive": p["false_positive"],
                   "cell_accuracy": _ratio(p["correct"], p["visible"]),
                   "accepted_accuracy": _ratio(p["correct"], p["correct"] + p["wrong"] + p["false_positive"]),
                   "detection_recall": _ratio(p["correct"] + p["wrong"], p["visible"]),
                   "unknown_rate": _ratio(p["unknown"], p["visible"]),
                   "false_positive_rate": _ratio(p["false_positive"], p["false_positive"] + p["absent_ok"])},
        "enemies": {"truth": e["truth"], "predicted": e["predicted"], "matched_within_1": e["matched"],
                    "exact_cell": e["exact"], "false_positive": e["false_positive"],
                    "precision": _ratio(e["matched"], e["matched"] + e["false_positive"]),
                    "recall": _ratio(e["matched"], e["truth"]),
                    "cell_accuracy": _ratio(e["exact"], e["matched"]),
                    "count_mae": mean(counts["enemy"].errors) if counts["enemy"].errors else None,
                    "unknown_on_truth": e["unknown_on_truth"], "unknown_elsewhere": e["unknown_elsewhere"],
                    "unknown_rate": _ratio(e["unknown_on_truth"], e["truth"]),
                    "predicted_on_player_cell": e["predicted_as_player_cell"]},
        "cells": {"occupied_precision": _ratio(c["occupied_correct"], occupied),
                  "free_precision": _ratio(c["free_correct"], free), "free_cells": free,
                  "false_free": c["free_wrong"], "empty_truth": c["empty_truth"],
                  "free_coverage": _ratio(c["free_correct"], c["empty_truth"]),
                  "free_unlabelled": c["free_unlabelled"],
                  "occupied_unlabelled": c["occupied_unlabelled"],
                  "unknown_rate": _ratio(c["state_UNKNOWN"], cells), "cells_scored": cells},
        "occupancy_truth": _occupancy_truth(c),
    }


def _occupancy_truth(c: Counter) -> dict[str, object]:
    """FREE sur vérités humaines : EMPTY échantillonnées vs cellules PLAYER/ENEMY/OCCUPIED."""
    free_correct = c["sample_empty_pred_FREE"]
    false_free = c["false_free_entity"] + c["false_free_sampled"]
    return {"sampled_cells": c["sample_cells"], "empty_truth": c["sample_empty_truth"],
            "occupied_truth_sampled": c["sample_occupied_truth"], "unknown_truth": c["sample_unknown_truth"],
            "occupied_truth_total": c["truth_occupied_cells"],
            "free_correct": free_correct, "false_free": false_free,
            "false_free_on_player_or_enemy": c["false_free_entity"],
            "empty_predicted_occupied": c["sample_empty_pred_OCCUPIED"],
            "empty_predicted_unknown": c["sample_empty_pred_UNKNOWN"] + c["sample_empty_pred_UNSCORED"],
            "occupied_predicted_unknown": c["occupied_truth_pred_UNKNOWN"] + c["occupied_truth_pred_UNSCORED"],
            "free_precision": _ratio(free_correct, free_correct + false_free),
            "free_coverage": _ratio(free_correct, c["sample_empty_truth"])}


def _new_counts() -> dict[str, _Counts]:
    return {"player": _Counts(), "enemy": _Counts(), "cells": _Counts()}


def _tracking_metrics(sequences: dict[str, list[tuple[EntitySample, dict[str, int]]]]) -> dict[str, object]:
    """ID switches, fragmentation, récupération après occlusion, réassociations fausses."""
    switches = fragments = recovered = occlusions = false_reassociations = frames = 0
    truth_count = matched = excluded = 0
    for rows in sequences.values():
        last_pred: dict[str, str] = {}
        seen_pred: dict[str, set[str]] = defaultdict(set)
        owners: dict[str, set[str]] = defaultdict(set)
        absent: set[str] = set()
        for sample, predicted_tracks in rows:
            if not (sample.tracking_identity_confirmed and sample.tracking_identity_source):
                excluded += 1
                # Ne pas comparer deux identités de part et d'autre d'une frame non vérifiée.
                last_pred.clear()
                absent.clear()
                continue
            frames += 1
            absent.update(sample.occluded_tracks)
            truth = {track: cell for cell, track in sample.enemies if track}
            truth_count += len(truth)
            cell_to_pred = {cell: track for track, cell in predicted_tracks.items()}
            for track, cell in truth.items():
                predicted = cell_to_pred.get(cell)
                if predicted is None:
                    continue
                matched += 1
                if track in absent:
                    occlusions += 1
                    recovered += int(last_pred.get(track) == predicted)
                    absent.discard(track)
                if track in last_pred and last_pred[track] != predicted:
                    switches += 1
                last_pred[track] = predicted
                seen_pred[track].add(predicted)
                owners[predicted].add(track)
        fragments += sum(max(0, len(values) - 1) for values in seen_pred.values())
        false_reassociations += sum(max(0, len(values) - 1) for values in owners.values())
    return {"frames": frames, "excluded_unverified_frames": excluded,
            "truth_observations": truth_count, "matched_observations": matched,
            "status": "MEASURED" if matched else "NOT_EVALUABLE",
            "id_switches": switches if matched else None, "fragmentation": fragments if matched else None,
            "occlusions_observed": occlusions, "occlusion_recovered": recovered,
            "false_reassociations": false_reassociations if matched else None}


def replay_timestamps(samples: list[EntitySample]) -> dict[str, tuple[float, str]]:
    """Real capture time; missing values interpolate between real anchors, else 0.4 s/frame.

    Frame order is authoritative. Reversed/non-finite timestamps are rejected, never silently clamped.
    """
    groups = defaultdict(list)
    for sample in samples:
        groups[sample.group_id].append(sample)
    result = {}
    for rows in groups.values():
        rows.sort(key=lambda s: s.frame_index)
        anchors = []
        for index, sample in enumerate(rows):
            raw = (sample.document.get("capture") or {}).get("timestamp")
            if raw is None:
                raw = (sample.document.get("prediction") or {}).get("timestamp")
            if raw is not None:
                timestamp = float(raw)
                if not math.isfinite(timestamp) or (anchors and timestamp <= anchors[-1][1]):
                    raise ValueError(f"Timestamp non croissant/invalide : {sample.observation_id}")
                anchors.append((index, timestamp))
        for index, sample in enumerate(rows):
            known = next((t for i, t in anchors if i == index), None)
            source = "capture.timestamp" if (sample.document.get("capture") or {}).get("timestamp") is not None else "prediction.timestamp"
            if known is None:
                left = [(i, t) for i, t in anchors if i < index]
                right = [(i, t) for i, t in anchors if i > index]
                if left and right:
                    a, ta = left[-1]; b, tb = right[0]
                    known = ta + (tb-ta) * (sample.frame_index-rows[a].frame_index) / (rows[b].frame_index-rows[a].frame_index)
                    source = "fallback_interpolated_real_anchors"
                elif anchors:
                    a, ta = left[-1] if left else right[0]
                    known = ta + (sample.frame_index-rows[a].frame_index) * 0.4
                    source = "fallback_0.4s_anchored"
                else:
                    known = sample.frame_index * 0.4
                    source = "fallback_0.4s_no_timestamp"
            result[sample.observation_id] = (known, source)
    return result


def run_entity_benchmark(repository: CorpusRepository, *, save_profiles: bool = False,
                         splits: tuple[str, ...] | None = None, layout: str | None = None,
                         layout_coverage: bool = True, freeze: bool = False,
                         declared_only: bool = False) -> dict[str, Any]:
    samples = entity_inventory(repository, freeze=freeze, layout_coverage=layout_coverage,
                               declared_only=declared_only)
    detector = CellEntityDetector()
    profiles_by_layout, diagnostics_by_layout = {}, {}
    # Nom distinct du paramètre ``layout`` (filtre) : la boucle ne doit pas l'écraser.
    for signature in sorted({sample.layout_signature for sample in samples}, key=lambda value: value or ""):
        profiles, diagnostics = build_profiles([sample for sample in samples if sample.layout_signature == signature],
                                               detector)
        profiles_by_layout[signature] = profiles
        diagnostics_by_layout[layout_digest(signature)] = diagnostics
    profile_diagnostics = {"by_layout": diagnostics_by_layout,
                           "player_hues": sum(d["player_hues"] for d in diagnostics_by_layout.values()),
                           "enemy_hues": sum(d["enemy_hues"] for d in diagnostics_by_layout.values())}
    # Un fichier par layout (LOT 3B-5B) : aucun layout n'écrase le profil d'un autre.
    if save_profiles:
        for profiles in profiles_by_layout.values():
            if profiles.teams is not None:
                save_team_profile(repository.root.parent, profiles.teams)
            if isinstance(profiles.player, PlayerVisualProfileV2):
                save_train_player_profile(repository.root.parent, profiles.player)
    samples = [s for s in samples if (splits is None or s.split in splits)
               and (layout is None or layout_digest(s.layout_signature) == layout)]
    replay_times = replay_timestamps(samples)
    before: dict[str, dict[str, _Counts]] = defaultdict(_new_counts)
    after: dict[str, dict[str, _Counts]] = defaultdict(_new_counts)
    timings: dict[str, list[float]] = defaultdict(list)
    frames_after, frames_before = [], []
    sequences: dict[str, list] = defaultdict(list)
    greedy_sequences: dict[str, list] = defaultdict(list)
    trackers: dict[str, EntityTracker] = {}
    greedy_state: dict[str, dict[str, int]] = {}
    backgrounds: dict[str, CellBackgroundModel] = {}
    for sample in samples:
        image = cv2.imread(str(sample.frame_path), cv2.IMREAD_COLOR)
        grid = grid_from_document(sample.document)
        if image is None or grid is None:
            continue
        # BEFORE : ancien détecteur tel quel (référence HSV joueur absente du corpus).
        started = time.perf_counter()
        legacy_grid, legacy_player, _score, legacy_enemies = classify_cell_occupancy(image, grid, None)
        timings["legacy_ms"].append((time.perf_counter() - started) * 1000)
        ids = {cell.logical: cell.cell_id for cell in legacy_grid.cells}
        legacy_occupancy = {cell.cell_id: cell.state.value for cell in legacy_grid.cells if cell.cell_id is not None}
        for scope in (sample.split, "all"):
            frames_before.append(_frame_metrics(
                sample, ids.get(legacy_player) if legacy_player else None,
                [ids[cell] for cell, _center, _score in legacy_enemies if ids.get(cell) is not None], [],
                legacy_occupancy, before[scope]))
        # AFTER : détecteur par cellule + suivi global, séquence par session.
        context = DetectionContext(grid_visible=grid.grid_visibility_state == "VISIBLE",
                                   grid_aligned=(grid.alignment or {}).get("status") == "ALIGNED",
                                   map_id=sample.map_id, layout_signature=sample.layout_signature,
                                   timestamp=replay_times[sample.observation_id][0])
        background = backgrounds.setdefault(sample.group_id, CellBackgroundModel())
        result = detector.detect(image, grid, profiles_by_layout[sample.layout_signature], context, background)
        timings["detector_ms"].append(result.timings_ms.get("total", 0.0))
        tracker = trackers.setdefault(sample.group_id, EntityTracker())
        started = time.perf_counter()
        tracked = tracker.update(result, context.timestamp)
        timings["tracker_ms"].append((time.perf_counter() - started) * 1000)
        if sample.analysis_ms:
            timings["observer_ms"].append(float(sample.analysis_ms))
        player = next((item.cell_id for item in tracked if item.kind is EntityKind.PLAYER
                       and item.observed_this_frame), None)
        enemies = [item.cell_id for item in tracked if item.kind is EntityKind.ENEMY and item.observed_this_frame]
        unknown = [item.cell_id for item in result.entities if item.kind is EntityKind.UNKNOWN]
        for scope in (sample.split, "all"):
            detail = _frame_metrics(sample, player, enemies, unknown, result.occupancy, after[scope])
            if scope == "all":
                detail.update({"layout": layout_digest(sample.layout_signature), "timestamp": context.timestamp,
                               "group_id": sample.group_id, "frame_index": sample.frame_index,
                               "truth_identities": [{"cell_id": c, "track_id": t} for c, t in sample.enemies],
                               "occluded_truth": list(sample.occluded_tracks),
                               "tracking_identity_confirmed": sample.tracking_identity_confirmed,
                               "tracking_identity_source": sample.tracking_identity_source,
                               "tracking_confirmed_at": sample.tracking_confirmed_at,
                               "tracking_sequence_id": sample.tracking_sequence_id,
                               "global_assignment": dict(tracker.last_diagnostics),
                               "detections": [e.to_dict() for e in result.entities],
                               "timestamp_source": replay_times[sample.observation_id][1],
                               "detector_player": result.player.cell_id if result.player else None,
                               "tracked_entities": [item.to_dict() for item in tracked],
                               "diagnostics": result.diagnostics})
                frames_after.append(detail)
        sequences[sample.group_id].append((sample, {item.track_id: item.cell_id for item in tracked
                                                    if item.kind is EntityKind.ENEMY and item.observed_this_frame
                                                    and item.state is TrackState.OBSERVED}))
        # Référence gloutonne sur les MÊMES détections, pour mesurer le gain de l'affectation globale.
        state = greedy_state.setdefault(sample.group_id, {})
        detections = [item.cell_id for item in result.entities if item.kind is EntityKind.ENEMY]
        assignment = greedy_assign(list(state.items()), detections)
        next_state: dict[str, int] = {}
        for index, cell_id in enumerate(detections):
            track = assignment.get(index) or f"g{len(state) + len(next_state) + 1}_{sample.frame_index}"
            next_state[track] = cell_id
        greedy_state[sample.group_id] = next_state
        detail["greedy_tracks"] = dict(next_state)
        greedy_sequences[sample.group_id].append((sample, dict(next_state)))
    from combatbot.corpus.tracking_metrics import tracking_scopes
    split_counts = Counter(sample.split for sample in samples)
    group_counts = {split: len({s.group_id for s in samples if s.split == split}) for split in ("train", "validation", "test")}
    tracked_truth = sum(1 for sample in samples if sample.tracking_identity_confirmed and any(track for _cell, track in sample.enemies))
    return {
        "schema_version": 1,
        "truth_policy": "entity_annotation_source == human_confirmed ; anciennes prédictions jamais utilisées",
        "frames": len(samples), "splits": dict(split_counts), "groups": group_counts,
        "frames_with_track_truth": tracked_truth,
        "phases": dict(Counter(sample.frame_phase or "—" for sample in samples)),
        "profiles": profile_diagnostics,
        "before": {scope: _summaries(values) for scope, values in before.items()},
        "after": {scope: _summaries(values) for scope, values in after.items()},
        "tracking_verified": tracking_scopes(frames_after),
        "tracking_global": _tracking_metrics(sequences),
        "tracking_greedy_same_detections": _tracking_metrics(greedy_sequences),
        "performance_ms": {name: {"mean": mean(values), "median": median(values),
                                  "p95": float(np.percentile(values, 95)), "max": max(values), "count": len(values)}
                           for name, values in timings.items() if values},
        "declared_splits": declared_only,
        "status": "INSUFFICIENT" if not split_counts.get("test") else "MEASURED",
        "frames_detail": frames_after,
    }


def markdown_entity_report(report: dict[str, Any]) -> str:
    lines = ["# Benchmark entités (LOT 3B-5)", "",
             f"- Statut : **{report['status']}**", f"- Frames annotées : **{report['frames']}**",
             f"- Splits (frames) : `{report['splits']}` ; groupes (combats) : `{report['groups']}`",
             f"- Frames avec identités ennemies annotées : {report['frames_with_track_truth']}",
             f"- Profils (TRAIN) : `{ {k: v for k, v in report['profiles'].items() if k != 'teams'} }`", ""]
    for label, key in (("BEFORE — classify_cell_occupancy", "before"), ("AFTER — détecteur + tracker", "after")):
        lines += [f"## {label}", ""]
        for scope, values in sorted(report[key].items()):
            lines.append(f"- **{scope.upper()}** joueur `{values['player']}`")
            lines.append(f"  ennemis `{values['enemies']}`")
            lines.append(f"  cellules `{values['cells']}`")
        lines.append("")
    lines += ["## Suivi", "", f"- Global : `{report['tracking_global']}`",
              f"- Glouton (mêmes détections) : `{report['tracking_greedy_same_detections']}`", "",
              "## Performance (ms)", "", f"`{report['performance_ms']}`", ""]
    return "\n".join(lines)


def write_entity_report(report: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path, md_path = directory / "entities.json", directory / "entities.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(markdown_entity_report(report), encoding="utf-8")
    return json_path, md_path
