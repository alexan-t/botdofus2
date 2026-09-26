"""LOT 3B-5E : validation croisée « un combat TRAIN laissé de côté » (développement seulement).

Chaque combat TRAIN est mesuré avec des profils appris sur les AUTRES combats TRAIN : estimation
honnête pendant le développement, sans toucher VALIDATION ni TEST. Ce n'est jamais une preuve
finale : seule la mesure TEST gelée l'est.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Callable

import cv2

from combatbot.corpus.entity_benchmark import (
    EntitySample, _frame_metrics, _new_counts, _summaries, build_profiles, entity_inventory, grid_from_document,
    replay_timestamps,
)
from combatbot.corpus.repository import CorpusRepository
from combatbot.corpus.tracking_metrics import tracking_metrics
from combatbot.vision.background_model import CellBackgroundModel
from combatbot.vision.entity_detector import CellEntityDetector, DetectionContext
from combatbot.vision.entity_models import EntityKind, TrackState
from combatbot.vision.entity_tracker import EntityTracker


def replay_group(samples: list[EntitySample], profiles, detector: CellEntityDetector | None = None,
                 tracker_factory: Callable[[], EntityTracker] = EntityTracker) -> list[dict]:
    """Rejoue détecteur + suivi sur un combat ; une ligne par frame (vérité, prédiction, pistes)."""
    detector = detector or CellEntityDetector()
    tracker, background = tracker_factory(), CellBackgroundModel()
    times = replay_timestamps(samples)
    rows = []
    for sample in sorted(samples, key=lambda item: item.frame_index):
        image = cv2.imread(str(sample.frame_path), cv2.IMREAD_COLOR)
        grid = grid_from_document(sample.document)
        if image is None or grid is None:
            continue
        context = DetectionContext(grid_visible=grid.grid_visibility_state == "VISIBLE",
                                   grid_aligned=(grid.alignment or {}).get("status") == "ALIGNED",
                                   map_id=sample.map_id, layout_signature=sample.layout_signature,
                                   timestamp=times[sample.observation_id][0])
        result = detector.detect(image, grid, profiles, context, background)
        tracked = tracker.update(result, context.timestamp)
        player = next((t.claimed_cell for t in tracked if t.kind is EntityKind.PLAYER and t.claimed_cell is not None), None)
        enemies = [t.claimed_cell for t in tracked if t.kind is EntityKind.ENEMY and t.claimed_cell is not None]
        rows.append({"sample": sample, "result": result, "player": player, "enemies": enemies,
                     "unknown": [e.cell_id for e in result.entities if e.kind is EntityKind.UNKNOWN],
                     "occupancy": dict(result.occupancy), "tracked": tracked,
                     "timestamp": context.timestamp, "diagnostics": result.diagnostics})
    return rows


def _tracking_frames(rows: list[dict]) -> list[dict]:
    frames = []
    for row in rows:
        sample = row["sample"]
        frames.append({"group_id": sample.group_id, "timestamp": row["timestamp"], "frame_index": sample.frame_index,
                       "tracking_identity_confirmed": sample.tracking_identity_confirmed,
                       "tracking_identity_source": sample.tracking_identity_source,
                       "tracking_confirmed_at": sample.tracking_confirmed_at,
                       "tracking_sequence_id": sample.tracking_sequence_id,
                       "truth_identities": [{"cell_id": c, "track_id": t} for c, t in sample.enemies],
                       "occluded_truth": list(sample.occluded_tracks),
                       "tracked_entities": [t.to_dict() for t in row["tracked"]]})
    return frames


def leave_one_combat_out(repository: CorpusRepository, *, detector_factory=CellEntityDetector,
                         tracker_factory=EntityTracker) -> dict[str, object]:
    """Profils appris sans le combat mesuré ; agrégat + détail par combat (TRAIN déclaré seulement)."""
    samples = [s for s in entity_inventory(repository, freeze=False, declared_only=True) if s.split == "train"]
    groups = defaultdict(list)
    for sample in samples:
        groups[sample.group_id].append(sample)
    total = _new_counts()
    per_group: dict[str, object] = {}
    all_frames: list[dict] = []
    details: list[dict] = []
    for group, held_out in sorted(groups.items()):
        detector = detector_factory()
        others = [replace(s, split="train") for s in samples if s.group_id != group]
        profiles, _diagnostics = build_profiles(others, detector)
        rows = replay_group(held_out, profiles, detector, tracker_factory)
        counts = _new_counts()
        for row in rows:
            _frame_metrics(row["sample"], row["player"], row["enemies"], row["unknown"], row["occupancy"], counts)
            _frame_metrics(row["sample"], row["player"], row["enemies"], row["unknown"], row["occupancy"], total)
        frames = _tracking_frames(rows)
        all_frames += frames
        details += rows
        per_group[group] = {**_summaries(counts), "tracking": tracking_metrics(frames)}
    return {"groups": per_group, "all": {**_summaries(total), "tracking": tracking_metrics(all_frames)},
            "rows": details}


def summary_line(values: dict) -> str:
    p, e, t, o = values["player"], values["enemies"], values["tracking"], values["occupancy_truth"]
    fmt = lambda v: "—" if v is None else f"{v:.3f}"
    return (f"joueur {p['correct']}/{p['frames_visible']} faux {p['wrong']} (couv {fmt(p['detection_recall'])}) | "
            f"ennemis {e['exact_cell']}/{e['truth']} ±1 {e['matched_within_1'] - e['exact_cell']} faux {e['false_positive']} "
            f"(préc {fmt(e['precision'])}, rappel {fmt(e['recall'])}) | suivi {t['exact_track_associations']}/"
            f"{t['identity_observations']} switch {fmt(t['switch_rate'])} réassoc {fmt(t['false_reassociation_rate'])} | "
            f"FREE faux {o['false_free']} sur entité {o['false_free_on_player_or_enemy']} préc {fmt(o['free_precision'])}")
