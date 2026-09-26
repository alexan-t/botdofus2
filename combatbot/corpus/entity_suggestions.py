"""LOT 3B-5E : suggestions du logiciel pour l'annotation assistée des entités.

Une suggestion n'est **jamais** une vérité : elle préremplit l'interface sur TRAIN/VALIDATION,
et l'utilisateur la confirme, la corrige ou la rejette. Sur TEST (ou split non déclaré), aucune
suggestion n'est calculée ni affichée avant que la vérité humaine de la frame soit enregistrée.

Les suggestions sont recalculées avec le code et les profils runtime actuels (détecteur par cellule
+ suivi global rejoué sur la séquence dans l'ordre des frames) ; elles ne réutilisent pas la
prédiction enregistrée à la capture, qui peut dater d'avant l'apprentissage des profils.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from combatbot import __version__
from combatbot.corpus.entity_benchmark import declared_split, grid_from_document
from combatbot.corpus.repository import CorpusRepository
from combatbot.vision.entity_models import EntityKind, TrackState, VisualProfiles

ASSISTED_SPLITS = ("train", "validation")
ENEMY_TRACK_LABELS = tuple(f"E{index}" for index in range(1, 9))
from combatbot.corpus.models import ANNOTATION_MODES  # noqa: E402,F401 - réexport


@dataclass(frozen=True)
class EntitySuggestion:
    """Sortie logicielle d'une frame, exprimée comme l'interface d'annotation (cell ID + E1…)."""

    player_cell: int | None
    enemies: tuple[tuple[int, str | None], ...]          # (cell_id, "E1" | None)
    free_cells: frozenset[int] = frozenset()
    confidences: dict[int, float] = field(default_factory=dict)
    meta: dict[str, object] = field(default_factory=dict)

    def snapshot(self, sampled: list[int] | None = None) -> dict[str, object]:
        """Instantané figé AVANT correction humaine (enregistré avec la vérité)."""
        occupancies = None
        if sampled is not None:
            entity_cells = {cell for cell, _ in self.enemies} | ({self.player_cell} if self.player_cell is not None else set())
            occupancies = {str(cell): "OCCUPIED" if cell in entity_cells else "FREE" if cell in self.free_cells
                           else "UNKNOWN" for cell in sampled}
        return {"player_cell": self.player_cell,
                "enemies": [{"cell_id": cell, "track_id": track} for cell, track in self.enemies],
                "occupancies": occupancies,
                "confidence": {str(cell): round(value, 3) for cell, value in sorted(self.confidences.items())},
                **self.meta}


def frame_split(document: dict) -> str | None:
    return declared_split(document)


def assistance_allowed(document: dict) -> bool:
    """Préremplissage autorisé : TRAIN et VALIDATION seulement. TEST / non déclaré : aveugle."""
    return frame_split(document) in ASSISTED_SPLITS


def comparison_allowed(document: dict, truth_confirmed: bool) -> bool:
    """La prédiction peut être montrée si l'assistance est permise, ou après la vérité gelée."""
    return assistance_allowed(document) or truth_confirmed


class DetectorSuggestionProvider:
    """Rejoue détecteur + suivi sur une séquence ; résultat mis en cache par séquence."""

    def __init__(self, data_root: Path | None = None) -> None:
        from combatbot.runtime import app_data_root
        self.data_root = data_root or app_data_root() / "data"
        self._cache: dict[str, dict[str, EntitySuggestion]] = {}
        self._profiles: dict[str | None, VisualProfiles] = {}

    def _generation(self) -> str | None:
        from combatbot.entity_runtime import read_json
        pointer = self.data_root / "entity_profiles" / "active.json"
        try:
            return str(read_json(pointer).get("generation")) if pointer.is_file() else None
        except (OSError, ValueError):
            return None

    def _profiles_for(self, layout_signature: str | None) -> VisualProfiles:
        if layout_signature not in self._profiles:
            from combatbot.entity_runtime import active_profile_directory
            from combatbot.vision.entity_profiles import load_team_profile, load_train_player_profile
            try:
                directory = active_profile_directory(self.data_root)
                player = load_train_player_profile(self.data_root, layout_signature, directory=directory)
                teams = load_team_profile(self.data_root, layout_signature, directory=directory)
            except (OSError, ValueError, KeyError):
                player = teams = None
            self._profiles[layout_signature] = VisualProfiles(
                player if player and player.compatible(layout_signature) else None,
                teams if teams and teams.compatible(layout_signature) else None)
        return self._profiles[layout_signature]

    def for_sequence(self, repository: CorpusRepository, sequence_id: str) -> dict[str, EntitySuggestion]:
        if sequence_id in self._cache:
            return self._cache[sequence_id]
        import cv2
        from combatbot.vision.background_model import CellBackgroundModel
        from combatbot.vision.entity_detector import CellEntityDetector, DetectionContext
        from combatbot.vision.entity_tracker import EntityTracker
        entries = sorted(repository.tracking_sequence_entries(sequence_id), key=lambda e: e.frame_index)
        # Même chaîne qu'en observation réelle : fond appris frame après frame (FREE prudent).
        detector, tracker, background = CellEntityDetector(), EntityTracker(), CellBackgroundModel()
        generation = self._generation()
        result: dict[str, EntitySuggestion] = {}
        for position, entry in enumerate(entries):
            document = repository.read_observation(entry)
            grid = grid_from_document(document)
            image = cv2.imread(str(repository.resolve(entry.paths["frame"])), cv2.IMREAD_COLOR)
            if grid is None or image is None:
                continue
            capture = document.get("capture") or {}
            layout = (capture.get("calibration") or {}).get("layout_signature")
            raw_time = capture.get("timestamp")
            timestamp = float(raw_time) if isinstance(raw_time, (int, float)) else position * 0.4
            context = DetectionContext(grid_visible=capture.get("grid_visibility_state") == "VISIBLE",
                                       grid_aligned=capture.get("alignment_status") == "ALIGNED",
                                       map_id=(document.get("grid_snapshot") or {}).get("map_id_declared"),
                                       layout_signature=layout, timestamp=timestamp,
                                       player_prior_cell=tracker.player_prior())
            detection = detector.detect(image, grid, self._profiles_for(layout), context, background)
            tracked = tracker.update(detection, timestamp)
            player = next((t.claimed_cell for t in tracked if t.kind is EntityKind.PLAYER
                           and t.claimed_cell is not None), None)
            enemies = []
            for track in tracked:
                if track.kind is EntityKind.ENEMY and track.claimed_cell is not None:
                    if track.state is TrackState.AMBIGUOUS:
                        enemies.append((track.claimed_cell, None))   # identité non tranchée : pas de E1/E2
                        continue
                    number = int(track.track_id.rsplit("_", 1)[-1])
                    enemies.append((track.claimed_cell, f"E{number}" if number <= len(ENEMY_TRACK_LABELS) else None))
            confidences = {t.claimed_cell: float(t.confidence) for t in tracked if t.claimed_cell is not None}
            result[entry.observation_id] = EntitySuggestion(
                player, tuple(sorted(enemies)),
                frozenset(cell for cell, state in detection.occupancy.items() if state == "FREE"), confidences,
                {"detector_version": f"pythonbot {__version__} CellEntityDetector+EntityTracker",
                 "profile_generation": generation,
                 "profiles": {"player": bool(self._profiles_for(layout).player),
                              "teams": bool(self._profiles_for(layout).teams)},
                 "computed_at": datetime.now().astimezone().isoformat(timespec="seconds")})
        self._cache[sequence_id] = result
        return result


# ---------------------------------------------------------------------------- revue
def review(snapshot: dict | None, player_cell: int | None, enemies: list[tuple[int, str | None]],
           distance: Callable[[int, int], int] | None = None) -> dict[str, object]:
    """Compare la suggestion d'origine et la vérité finale, élément par élément.

    Statuts : confirmed (inchangé), corrected (cellule voisine ou identité changée),
    rejected (suggestion supprimée), added (élément manqué par le logiciel).
    """
    if snapshot is None:
        return {"mode": "manual", "player": None, "enemies": []}
    if distance is None:
        from combatbot.vision.entity_tracker import grid_distance as distance
    suggested_player = snapshot.get("player_cell")
    if suggested_player is None and player_cell is None:
        player_status = None
    elif suggested_player is None:
        player_status = "added"
    elif player_cell is None:
        player_status = "rejected"
    else:
        player_status = "confirmed" if suggested_player == player_cell else "corrected"
    suggested = [(int(item["cell_id"]), item.get("track_id")) for item in snapshot.get("enemies", ())]
    final = list(enemies)
    rows: list[dict[str, object]] = []
    for matcher in (lambda s, f: s[0] == f[0], lambda s, f: distance(s[0], f[0]) <= 1):
        for s_item in list(suggested):
            f_item = next((f for f in final if matcher(s_item, f)), None)
            if f_item is None:
                continue
            same = s_item == f_item
            rows.append({"suggested": list(s_item), "final": list(f_item),
                         "status": "confirmed" if same else "corrected"})
            suggested.remove(s_item)
            final.remove(f_item)
    rows += [{"suggested": list(s), "final": None, "status": "rejected"} for s in suggested]
    rows += [{"suggested": None, "final": list(f), "status": "added"} for f in final]
    statuses = [row["status"] for row in rows] + ([player_status] if player_status else [])
    mode = "assisted_confirmed" if all(status == "confirmed" for status in statuses) else "assisted_corrected"
    return {"mode": mode, "player": player_status, "enemies": rows}


def review_statistics(annotations) -> dict[str, object]:
    """Aide de revue (TRAIN/VALIDATION) : ne remplace jamais le benchmark indépendant."""
    stats = {"frames_reviewed": 0, "frames_all_correct": 0, "frames_corrected": 0,
             "player": {"confirmed": 0, "corrected": 0, "rejected": 0, "added": 0},
             "enemy": {"confirmed": 0, "corrected": 0, "rejected": 0, "added": 0}}
    for annotation in annotations:
        if annotation is None or not annotation.suggestion_review:
            continue
        outcome = annotation.suggestion_review
        stats["frames_reviewed"] += 1
        stats["frames_all_correct" if outcome.get("mode") == "assisted_confirmed" else "frames_corrected"] += 1
        if outcome.get("player"):
            stats["player"][outcome["player"]] += 1
        for row in outcome.get("enemies", ()):
            stats["enemy"][row["status"]] += 1
    return stats
