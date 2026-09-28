"""LOT 3B-6C : résolution automatique de la map courante, LECTURE SEULE.

Sources (par ordre) : infos de map lues à l'écran (coordonnées + « Zone (Sous-zone) » + niveau,
consensus temporel) → index GameData → contexte de la map précédente (graphe de voisinage) →
forme de la map à l'écran (cases marchables éclairées, vide noir des intérieurs) → hypothèses
gardées après une ambiguïté → empreinte visuelle des maps déjà résolues ou confirmées. Aucune source réseau, mémoire ou
injection. Principe : mieux vaut UNKNOWN/AMBIGUOUS qu'une mauvaise map. Un changement de map
n'est retenu qu'après consensus ; une lecture isolée ne réinitialise rien.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
import json
from pathlib import Path
import threading
import time

import cv2
import numpy as np

from combatbot.gamedata.map_index import MapRecord, MapSpatialIndex, name_similarity, normalize_name
from combatbot.vision.grid_profile import DeclaredMapId, MapIdOrigin, MapIdSource
from combatbot.vision.map_reader import (
    CoordinateConsensus, MapCoordinateReader, MapInfoObservation, extract_map_info_roi,
)

NAME_THRESHOLD = 0.85
FINGERPRINT_ALGORITHM = "dhash-64x36-v1"
FINGERPRINT_MASK = "top14-bottom12-v1"
FINGERPRINT_ACCEPT = 0.10      # distance max (fraction de bits) pour reconnaître une map
FINGERPRINT_MARGIN = 0.08      # écart minimal avec la 2ᵉ candidate connue
FINGERPRINT_MAX_PER_MAP = 8

# Forme de la map à l'écran : une case marchable est toujours dessinée ; le vide d'un intérieur sans
# image de fond est noir. Mesuré sur 472 frames du corpus : la vraie map n'a jamais plus de 10 % de
# cases marchables noires. Ces tests ne font qu'ÉLIMINER des candidates, jamais en inventer.
DARK_VALUE = 22                  # valeur HSV (0-255) sous laquelle un centre de case est « noir »
SHAPE_WALK_MIN_CELLS = 20
SHAPE_WALK_DARK_REFUTE = 0.25    # ≥ 25 % des cases marchables noires → pas cette map
SHAPE_WALK_DARK_SUPPORT = 0.10   # ≤ 10 % → la forme soutient cette map
SHAPE_VOID_MIN_CELLS = 120
SHAPE_VOID_DARK_MIN = 0.10       # intérieur sans fond dont le vide est éclairé à > 90 % → pas cette map
HYPOTHESIS_CONFIDENCE = 0.96     # < 0.97 : aucune empreinte apprise sur ces résolutions


class MapResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"
    TRANSITION = "TRANSITION"
    STALE = "STALE"
    INCONSISTENT = "INCONSISTENT"


@dataclass(frozen=True)
class MapResolution:
    map_id: int | None
    status: MapResolutionStatus
    coordinates: tuple[int, int] | None = None
    confidence: float = 0.0
    source: str = ""
    candidate_count: int = 0
    candidates: tuple[int, ...] = ()
    reason: str = ""
    changed: bool = False
    previous_map_id: int | None = None
    initial_candidates: int = 0
    contributions: dict[str, object] = field(default_factory=dict)
    game_data_loaded: bool | None = None
    calibration_status: str | None = None
    grid_status: str | None = None
    timestamp: float = 0.0

    @property
    def recordable(self) -> bool:
        return self.status is MapResolutionStatus.RESOLVED and self.map_id is not None

    def to_dict(self) -> dict[str, object]:
        return {"map_id": self.map_id, "status": self.status.value,
                "coordinates": list(self.coordinates) if self.coordinates else None,
                "confidence": round(float(self.confidence), 4), "source": self.source,
                "candidate_count": self.candidate_count, "candidates": list(self.candidates[:12]),
                "initial_candidates": self.initial_candidates, "reason": self.reason, "changed": self.changed,
                "previous_map_id": self.previous_map_id, "contributions": dict(self.contributions),
                "game_data_loaded": self.game_data_loaded, "calibration_status": self.calibration_status,
                "grid_status": self.grid_status}


# ---------------------------------------------------------------------- empreintes visuelles
def compute_fingerprint(combat_image: np.ndarray) -> np.ndarray | None:
    """dHash 64×36 de la zone de jeu, bandeau d'infos (haut) et bas de l'écran masqués.

    Les petits sprites (mode créature) ne pèsent presque rien à cette résolution ; l'empreinte ne
    sert qu'à départager quelques candidates, jamais à chercher parmi les 12 154 maps.
    """
    if combat_image is None or not getattr(combat_image, "size", 0):
        return None
    gray = cv2.cvtColor(combat_image, cv2.COLOR_BGR2GRAY) if combat_image.ndim == 3 else combat_image
    height = gray.shape[0]
    gray = gray[int(height * 0.14):int(height * 0.88)]
    small = cv2.resize(cv2.GaussianBlur(gray, (5, 5), 0), (65, 36), interpolation=cv2.INTER_AREA).astype(np.int16)
    return (small[:, 1:] > small[:, :-1]).flatten()


def fingerprint_distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(a != b)) if a is not None and b is not None and a.shape == b.shape else 1.0


class MapKnowledge:
    """Connaissance locale confirmée : empreintes de maps résolues et confirmations humaines."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self.fingerprints: dict[int, list[dict]] = {}
        self.confirmations: list[dict] = []
        self._lock = threading.Lock()
        if self.path is not None and self.path.is_file():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.fingerprints = {int(key): list(value) for key, value in raw.get("fingerprints", {}).items()}
                self.confirmations = list(raw.get("confirmations", []))
            except (OSError, ValueError, TypeError):
                self.fingerprints, self.confirmations = {}, []

    def fingerprint_bits(self, map_id: int) -> list[np.ndarray]:
        return [np.unpackbits(np.frombuffer(bytes.fromhex(item["bits"]), dtype=np.uint8))[:item["length"]].astype(bool)
                for item in self.fingerprints.get(int(map_id), [])]

    def learn(self, map_id: int, fingerprint: np.ndarray | None, *, source: str, layout: str | None) -> bool:
        """Ajoute une empreinte seulement pour une map résolue avec forte confiance ou confirmée."""
        if fingerprint is None:
            return False
        with self._lock:
            known = self.fingerprint_bits(map_id)
            if any(fingerprint_distance(fingerprint, item) < 0.04 for item in known):
                return False
            entries = self.fingerprints.setdefault(int(map_id), [])
            entries.append({"bits": np.packbits(fingerprint).tobytes().hex(), "length": int(fingerprint.size),
                            "algorithm": FINGERPRINT_ALGORITHM, "mask": FINGERPRINT_MASK, "source": source,
                            "layout_signature": layout,
                            "created_at": datetime.now().astimezone().isoformat(timespec="seconds")})
            del entries[:-FINGERPRINT_MAX_PER_MAP]
            self._save()
            return True

    def confirm(self, key: tuple | None, map_id: int, *, layout: str | None, context: dict | None) -> None:
        with self._lock:
            self.confirmations.append({"key": list(key) if key else None, "map_id": int(map_id),
                                       "layout_signature": layout, "context": context or {},
                                       "source": "human_manual",
                                       "created_at": datetime.now().astimezone().isoformat(timespec="seconds")})
            self._save()

    def confirmed_maps(self, key: tuple | None) -> set[int]:
        if key is None:
            return set()
        return {int(item["map_id"]) for item in self.confirmations if item.get("key") == list(key)}

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "fingerprints": {str(k): v for k, v in
                                                                               self.fingerprints.items()},
                                         "confirmations": self.confirmations}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        temporary.replace(self.path)


# ---------------------------------------------------------------------- forme de la map
@dataclass(frozen=True)
class MapShape:
    """Faits GameData statiques : cases marchables, cases sans aucun élément graphique."""

    walkable: frozenset[int]
    void: frozenset[int]
    background_fixtures: int
    outdoor: bool


def map_shape(game_map, outdoor: bool) -> MapShape:
    drawn: set[int] = set()
    for cells in (game_map.metadata.get("layer_cells") or {}).values():
        drawn.update(int(cell) for cell in cells)
    identifiers = [int(cell.cell_id) for cell in game_map.cells]
    return MapShape(frozenset(int(cell.cell_id) for cell in game_map.cells if cell.walkable),
                    frozenset(cell for cell in identifiers if cell not in drawn),
                    int(game_map.metadata.get("background_fixture_count") or 0), bool(outdoor))


class GameDataShapeSource:
    """map_id → MapShape depuis les DLM locaux (quelques candidates seulement, en cache)."""

    def __init__(self, provider, index: MapSpatialIndex, max_cache: int = 64) -> None:
        self.provider, self.index, self.max_cache = provider, index, max_cache
        self._cache: dict[int, MapShape | None] = {}

    def __call__(self, map_id: int) -> MapShape | None:
        if map_id not in self._cache:
            record = self.index.get_map(map_id)
            try:
                shape = map_shape(self.provider.get_map(int(map_id), track=False), record.outdoor) \
                    if record is not None else None
            except (OSError, ValueError):
                shape = None
            self._cache[map_id] = shape
            while len(self._cache) > self.max_cache:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[map_id]


def screen_darkness(combat_image: np.ndarray | None,
                    centers: dict[int, tuple[float, float]] | None) -> dict[int, bool] | None:
    """Case → centre noir à l'écran (patch 7×7), pour les cases dont le centre est dans l'image."""
    if combat_image is None or not getattr(combat_image, "size", 0) or not centers:
        return None
    value = cv2.cvtColor(combat_image, cv2.COLOR_BGR2HSV)[..., 2] if combat_image.ndim == 3 else combat_image
    height, width = value.shape[:2]
    dark = {}
    for cell_id, (x, y) in centers.items():
        x, y = int(round(x)), int(round(y))
        if 4 <= x < width - 4 and 4 <= y < height - 4:
            dark[int(cell_id)] = float(value[y - 3:y + 4, x - 3:x + 4].mean()) < DARK_VALUE
    return dark or None


# ---------------------------------------------------------------------- résolveur
class MapContextResolver:
    """Décision explicable : chaque étape réduit les candidates ou s'abstient."""

    def __init__(self, index: MapSpatialIndex, knowledge: MapKnowledge | None = None,
                 shapes=None) -> None:
        self.index = index
        self.knowledge = knowledge or MapKnowledge()
        self.shapes = shapes        # map_id → MapShape | None ; None = test de forme indisponible

    def _shape_verdict(self, record: MapRecord, dark: dict[int, bool]) -> dict[str, object]:
        """``refuted`` : la forme GameData contredit l'écran ; ``supported`` : marchables éclairées."""
        shape = self.shapes(record.map_id) if self.shapes is not None else None
        if shape is None:
            return {"available": False}
        verdict: dict[str, object] = {"available": True, "refuted": None, "supported": False}
        walk = [dark[cell] for cell in shape.walkable if cell in dark]
        if len(walk) >= SHAPE_WALK_MIN_CELLS:
            ratio = float(np.mean(walk))
            verdict["walk_dark"] = round(ratio, 3)
            verdict["supported"] = ratio <= SHAPE_WALK_DARK_SUPPORT
            if ratio >= SHAPE_WALK_DARK_REFUTE:
                verdict["refuted"] = "WALKABLE_CELLS_DARK"
                return verdict
        if not shape.outdoor and shape.background_fixtures == 0:
            void = [dark[cell] for cell in shape.void if cell in dark]
            if len(void) >= SHAPE_VOID_MIN_CELLS:
                ratio = float(np.mean(void))
                verdict["void_dark"] = round(ratio, 3)
                if ratio < SHAPE_VOID_DARK_MIN:
                    verdict["refuted"] = "INDOOR_VOID_LIT"
        return verdict

    def _names_match(self, record: MapRecord, observation: MapInfoObservation) -> bool:
        if observation.map_name is not None:
            # Nom propre de salle : égalité exacte après normalisation (« Première » ≠ « Dernière »).
            return bool(record.map_name) and normalize_name(record.map_name) == normalize_name(observation.map_name)
        return (name_similarity(record.sub_area_name, observation.sub_area_name) >= NAME_THRESHOLD
                and name_similarity(record.area_name, observation.area_name) >= NAME_THRESHOLD
                and (record.level is None or observation.level is None or record.level == observation.level))

    def _adjacent(self, record: MapRecord, previous: int) -> bool:
        return previous in record.neighbours or record.map_id in self.index.neighbours(previous)

    def resolve(self, observation: MapInfoObservation | None, *, previous_map_id: int | None = None,
                scene_changed: bool = False, fingerprint: np.ndarray | None = None,
                log_map_id: int | None = None, now: float | None = None,
                screen_dark: dict[int, bool] | None = None,
                previous_candidates: tuple[int, ...] = ()) -> MapResolution:
        stamp = time.time() if now is None else now
        base = {"previous_map_id": previous_map_id, "timestamp": stamp}
        if log_map_id is not None:
            # Aucune source de log locale n'existe pour ce client (audit 3B-6C) ; gardée pour l'API.
            record = self.index.get_map(log_map_id)
            if record is None:
                return MapResolution(None, MapResolutionStatus.UNKNOWN, reason="LOG_MAP_NOT_IN_GAMEDATA", **base)
            if observation is not None and observation.complete and observation.coordinates is not None and \
                    (record.x, record.y) != (observation.coordinates.x, observation.coordinates.y):
                return MapResolution(None, MapResolutionStatus.INCONSISTENT, (record.x, record.y),
                                     reason="LOG_CONTRADICTS_SCREEN_COORDINATES", **base)
            return MapResolution(record.map_id, MapResolutionStatus.RESOLVED, (record.x, record.y), 0.999,
                                 "LOCAL_LOG", 1, (record.map_id,), "LOG_MAP_ID",
                                 changed=record.map_id != previous_map_id, contributions={"log_score": 1.0},
                                 **{k: v for k, v in base.items() if k != "previous_map_id"},
                                 previous_map_id=previous_map_id)
        if observation is None or not observation.complete or observation.coordinates is None:
            return MapResolution(None, MapResolutionStatus.UNKNOWN,
                                 reason=observation.reason if observation else "NO_READING", **base)
        coords = (observation.coordinates.x, observation.coordinates.y)
        initial = self.index.candidates_by_coords(*coords)
        contributions: dict[str, object] = {"coord_score": round(observation.coordinates.confidence, 3),
                                            "initial_candidates": len(initial)}
        if not initial:
            return MapResolution(None, MapResolutionStatus.UNKNOWN, coords, reason="COORDINATES_NOT_IN_GAMEDATA",
                                 contributions=contributions, **base)
        named = [record for record in initial if self._names_match(record, observation)]
        contributions["area_candidates"] = len(named)
        if not named:
            return MapResolution(None, MapResolutionStatus.INCONSISTENT, coords, candidates=tuple(r.map_id for r in initial),
                                 candidate_count=len(initial), initial_candidates=len(initial),
                                 reason="NAMES_OR_LEVEL_CONTRADICT_GAMEDATA", contributions=contributions, **base)
        sources = ["OCR_COORDS", "MAP_NAME" if observation.map_name is not None else "AREA_NAMES"]

        def resolved(record: MapRecord, confidence: float, reason: str) -> MapResolution:
            return MapResolution(record.map_id, MapResolutionStatus.RESOLVED, coords, confidence, " + ".join(sources),
                                 len(named), (record.map_id,), reason, record.map_id != previous_map_id,
                                 previous_map_id, len(initial), contributions, timestamp=stamp)

        if len(named) == 1:
            if len(initial) == 1:
                sources[0] = "OCR_COORDS_UNIQUE"
            return resolved(named[0], 0.99, "UNIQUE_AFTER_NAMES")
        # Plusieurs maps partagent coordonnées + noms (extérieur / intérieur, mines, donjons…).
        verdicts: dict[int, dict] = {}
        if screen_dark and self.shapes is not None:
            verdicts = {record.map_id: self._shape_verdict(record, screen_dark) for record in named}
            contributions["shape"] = {str(k): {key: value for key, value in v.items() if key != "available"}
                                      for k, v in verdicts.items() if v.get("available")}
            survivors = [record for record in named if not verdicts[record.map_id].get("refuted")]
            if not survivors:
                contributions["shape_refutes_all"] = True          # écran incohérent : rien n'est éliminé
            elif len(survivors) < len(named):
                contributions["shape_refuted"] = [r.map_id for r in named if r not in survivors]
                sources.append("SCREEN_SHAPE")
                named = survivors
                if len(named) == 1:
                    return resolved(named[0], HYPOTHESIS_CONFIDENCE, "UNIQUE_AFTER_SCREEN_SHAPE")
        if previous_candidates:
            # Map précédente ambiguë : ses candidates restent des hypothèses (plus récentes que la
            # dernière map résolue). Un pas vers une voisine peut lever l'ambiguïté.
            hypotheses = {int(item) for item in previous_candidates}
            same = [record for record in named if record.map_id in hypotheses]
            contributions["hypotheses"] = len(hypotheses)
            if same and not scene_changed:
                if len(same) == 1:
                    sources.append("PREVIOUS_CANDIDATES")
                    return resolved(same[0], HYPOTHESIS_CONFIDENCE, "UNIQUE_REMAINING_HYPOTHESIS")
                named = same
            else:
                adjacent = [record for record in named
                            if any(self._adjacent(record, hypothesis) for hypothesis in hypotheses)]
                contributions["hypothesis_graph_candidates"] = len(adjacent)
                indoor_hypothesis = any(not getattr(self.index.get_map(h), "outdoor", False) for h in hypotheses)
                if len(adjacent) == 1:
                    # Depuis un intérieur possible, une porte peut aussi changer de coordonnées : on exige
                    # alors que l'écran SOUTIENNE la voisine (ses cases marchables sont éclairées).
                    if not indoor_hypothesis or verdicts.get(adjacent[0].map_id, {}).get("supported"):
                        sources.append("PREVIOUS_CANDIDATES_GRAPH")
                        return resolved(adjacent[0], HYPOTHESIS_CONFIDENCE,
                                        "UNIQUE_NEIGHBOUR_OF_PREVIOUS_CANDIDATES")
                    contributions["hypothesis_graph_unsupported"] = adjacent[0].map_id
                elif adjacent:
                    named = adjacent
        elif previous_map_id is not None:
            same = next((record for record in named if record.map_id == previous_map_id), None)
            if same is not None and not scene_changed:
                contributions["previous_map_score"] = 1.0
                sources.append("PREVIOUS_MAP")
                return resolved(same, 0.98, "SAME_MAP_CONTINUOUS_SCENE")
            adjacent = [record for record in named if self._adjacent(record, previous_map_id)]
            contributions["graph_candidates"] = len(adjacent)
            if len(adjacent) == 1 and adjacent[0].map_id != previous_map_id:
                contributions["graph_score"] = 1.0
                sources.append("PREVIOUS_MAP_GRAPH")
                return resolved(adjacent[0], 0.97, "UNIQUE_NEIGHBOUR_OF_PREVIOUS_MAP")
            if not adjacent:
                contributions["non_local_transition"] = True
        # Empreinte visuelle : repli, seulement parmi ces quelques candidates.
        if fingerprint is not None:
            distances = {}
            for record in named:
                known = self.knowledge.fingerprint_bits(record.map_id)
                if known:
                    distances[record.map_id] = min(fingerprint_distance(fingerprint, item) for item in known)
            contributions["fingerprint_candidates"] = {str(k): round(v, 3) for k, v in distances.items()}
            if distances:
                best_id = min(distances, key=distances.get)
                others = [value for key, value in distances.items() if key != best_id]
                if distances[best_id] <= FINGERPRINT_ACCEPT and (not others or min(others) - distances[best_id]
                                                                 >= FINGERPRINT_MARGIN):
                    contributions["fingerprint_score"] = round(1 - distances[best_id], 3)
                    sources.append("FINGERPRINT")
                    record = self.index.get_map(best_id)
                    return resolved(record, 0.95, "KNOWN_FINGERPRINT")
        return MapResolution(None, MapResolutionStatus.AMBIGUOUS, coords, 0.0, " + ".join(sources), len(named),
                             tuple(record.map_id for record in named), f"{len(named)} maps candidates", False,
                             previous_map_id, len(initial), contributions, timestamp=stamp)


# ---------------------------------------------------------------------- détection de transition
@dataclass
class MapTransitionDetector:
    """« La map est probablement en train de changer » ; ne donne jamais de map ID."""

    change_threshold: float = 0.55
    dark_threshold: float = 18.0
    quiet_threshold: float = 0.08
    _previous: np.ndarray | None = None
    in_transition: bool = False
    changed_since_read: bool = False

    def update(self, combat_image: np.ndarray) -> bool:
        small = cv2.resize(combat_image, (96, 54), interpolation=cv2.INTER_AREA).astype(np.int16)
        dark = float(small.mean()) < self.dark_threshold
        change = 0.0 if self._previous is None else float(np.mean(np.abs(small - self._previous).max(axis=2) > 24))
        self._previous = small
        if dark or change >= self.change_threshold:
            self.in_transition = True
            self.changed_since_read = True
        elif self.in_transition and change <= self.quiet_threshold:
            self.in_transition = False
        return self.in_transition


# ---------------------------------------------------------------------- service temps réel
class AutoMapIdentity:
    """MapIdentityProvider : map résolue automatiquement, ou déclaration manuelle de secours."""

    def __init__(self) -> None:
        self._value: DeclaredMapId | None = None

    def set_detected(self, map_id: int | None) -> None:
        self._value = DeclaredMapId(map_id, MapIdOrigin.DETECTED, MapIdSource.AUTO_DETECTED) if map_id is not None \
            else None

    def declare(self, map_id: int | None, source: MapIdSource = MapIdSource.MANUAL_GUESS) -> DeclaredMapId | None:
        self._value = DeclaredMapId(map_id, source=MapIdSource(source)) if map_id is not None else None
        return self._value

    def current_map(self) -> DeclaredMapId | None:
        return self._value


class MapContextService:
    """Lecture OCR en arrière-plan (cadence bornée), consensus, résolution, identité de map.

    Un vieux job OCR ne remplace jamais un résultat plus récent (numéro de génération) ; une lecture
    faite avant une transition de scène est ignorée.
    """

    def __init__(self, resolver: MapContextResolver, *, reader: MapCoordinateReader | None = None,
                 identity: AutoMapIdentity | None = None, interval: float = 2.0, stale_seconds: float = 90.0,
                 layout_signature: str | None = None, journal: Path | None = None, synchronous: bool = False,
                 track_hypotheses: bool = True) -> None:
        self.resolver = resolver
        self.reader = reader or MapCoordinateReader()
        self.identity = identity or AutoMapIdentity()
        self.interval, self.stale_seconds = interval, stale_seconds
        self.layout_signature = layout_signature
        self.journal = journal
        self.consensus = CoordinateConsensus()
        self.transitions = MapTransitionDetector()
        self.synchronous = synchronous
        self._executor = None if synchronous else ThreadPoolExecutor(max_workers=1, thread_name_prefix="map-ocr")
        self._pending: tuple[int, float, Future] | None = None
        self._generation = 0
        self._applied_generation = 0
        self._last_submit = -1e9
        self._last_scene_change = -1e9
        self._last_good_read = -1e9
        self._last_observation: MapInfoObservation | None = None
        self._accepted: MapInfoObservation | None = None
        self.resolution = MapResolution(None, MapResolutionStatus.UNKNOWN, reason="DETECTING")
        self.map_id: int | None = None
        # Candidates de la dernière lecture ambiguë ; vidées dès qu'une map est résolue.
        self.hypotheses: tuple[int, ...] = ()
        self.track_hypotheses = track_hypotheses
        self.manual = False
        self.timings: dict[str, float] = {}

    # --------------------------------------------------------------- OCR asynchrone
    def _submit(self, client_image: np.ndarray, now: float) -> None:
        self._generation += 1
        roi = extract_map_info_roi(client_image, self.reader.roi)
        if self.synchronous:
            started = time.perf_counter()
            observation = self.reader.read(client_image, roi_image=roi, timestamp=now)
            self.timings["coordinate_reader_ms"] = (time.perf_counter() - started) * 1000
            self._consume(self._generation, now, observation)
            return
        generation = self._generation

        def work():
            started = time.perf_counter()
            result = self.reader.read(client_image, roi_image=roi, timestamp=now)
            self.timings["coordinate_reader_ms"] = (time.perf_counter() - started) * 1000
            return result

        self._pending = (generation, now, self._executor.submit(work))

    def _collect(self) -> None:
        if self._pending is None or not self._pending[2].done():
            return
        generation, captured_at, future = self._pending
        self._pending = None
        try:
            observation = future.result()
        except Exception:  # noqa: BLE001 - lecture perdue, jamais inventée
            return
        self._consume(generation, captured_at, observation)

    def _consume(self, generation: int, captured_at: float, observation: MapInfoObservation) -> None:
        if generation <= self._applied_generation or captured_at < self._last_scene_change:
            return   # plus ancien qu'un résultat appliqué ou qu'une transition : ignoré
        self._applied_generation = generation
        self._last_observation = observation
        accepted = self.consensus.update(observation)
        if accepted is not None:
            self._last_good_read = captured_at
            self._accepted = accepted

    # --------------------------------------------------------------- mise à jour par frame
    def update(self, client_image: np.ndarray, combat_image: np.ndarray, now: float | None = None,
               cell_centers: dict[int, tuple[float, float]] | None = None) -> MapResolution:
        """``cell_centers`` : centres des 560 cases dans ``combat_image`` (grille calibrée), sinon None."""
        stamp = time.monotonic() if now is None else now
        started = time.perf_counter()
        in_transition = self.transitions.update(combat_image)
        if in_transition:
            self._last_scene_change = stamp
        self._collect()
        if not in_transition and self._pending is None and (
                stamp - self._last_submit >= self.interval or self.transitions.changed_since_read):
            self._last_submit = stamp
            self._submit(client_image, stamp)
        if self.manual:
            self.resolution = replace(self.resolution, timestamp=stamp)
            return self.resolution
        accepted = self._accepted
        previous = self.map_id
        if in_transition:
            resolution = MapResolution(previous, MapResolutionStatus.TRANSITION, reason="SCENE_CHANGING",
                                       previous_map_id=previous, timestamp=stamp)
        elif accepted is None:
            reason = self._last_observation.reason if self._last_observation else "DETECTING"
            resolution = MapResolution(None, MapResolutionStatus.UNKNOWN, reason=reason, timestamp=stamp)
        elif self.consensus.pending_change or accepted.timestamp < self._last_scene_change:
            resolution = MapResolution(previous, MapResolutionStatus.TRANSITION, reason="AWAITING_CONSENSUS",
                                       previous_map_id=previous, timestamp=stamp)
        elif stamp - self._last_good_read > self.stale_seconds:
            resolution = MapResolution(previous, MapResolutionStatus.STALE, reason="NO_RECENT_READING",
                                       previous_map_id=previous, timestamp=stamp)
        else:
            resolver_started = time.perf_counter()
            fingerprint = compute_fingerprint(combat_image)
            resolution = self.resolver.resolve(accepted, previous_map_id=previous,
                                               scene_changed=self.transitions.changed_since_read,
                                               fingerprint=fingerprint, now=stamp,
                                               screen_dark=screen_darkness(combat_image, cell_centers),
                                               previous_candidates=self.hypotheses)
            self.timings["resolver_ms"] = (time.perf_counter() - resolver_started) * 1000
            if resolution.status is MapResolutionStatus.RESOLVED:
                self.hypotheses = ()
                self.transitions.changed_since_read = False
                if resolution.changed:
                    self._log("MAP_CHANGED", resolution, accepted)
                self.map_id = resolution.map_id
                self.identity.set_detected(resolution.map_id)
                if resolution.confidence >= 0.97:
                    self.resolver.knowledge.learn(resolution.map_id, fingerprint, source=resolution.source,
                                                  layout=self.layout_signature)
            elif self.track_hypotheses and resolution.status is MapResolutionStatus.AMBIGUOUS                     and resolution.candidates != self.hypotheses:
                self.hypotheses = resolution.candidates
                self.transitions.changed_since_read = False    # hypothèses fixées sur cette scène
                self._log(resolution.status.value, resolution, accepted)
            elif resolution.status is not self.resolution.status or resolution.candidates != self.resolution.candidates:
                # Ambigu/incohérent : la map précédente reste l'identité du suivi (aucun reset),
                # mais rien n'est enregistré tant que la map n'est pas résolue. Journalisé une fois.
                self._log(resolution.status.value, resolution, accepted)
        self.timings["update_ms"] = (time.perf_counter() - started) * 1000
        self.resolution = resolution
        return resolution

    def declare_manual(self, map_id: int | None, combat_image: np.ndarray | None = None) -> None:
        """Fallback « Utiliser ce mapId manuellement » ; mémorise la confirmation si cohérente."""
        if map_id is None:
            self.manual = False
            return
        self.manual = True
        self.map_id = map_id
        self.hypotheses = ()
        self.identity.declare(map_id, MapIdSource.MANUAL_GUESS)
        record = self.resolver.index.get_map(map_id)
        accepted = self._accepted
        if record is not None and accepted is not None and accepted.coordinates is not None and \
                (accepted.coordinates.x, accepted.coordinates.y) == (record.x, record.y):
            self.resolver.knowledge.confirm(accepted.key, map_id, layout=self.layout_signature,
                                            context=self.resolver.index.world_context(map_id))
            self.resolver.knowledge.learn(map_id, compute_fingerprint(combat_image) if combat_image is not None
                                          else None, source="human_manual", layout=self.layout_signature)
        self.resolution = MapResolution(map_id, MapResolutionStatus.RESOLVED,
                                        (record.x, record.y) if record else None, 1.0, "MANUAL", 1, (map_id,),
                                        "USER_DECLARED", map_id != self.resolution.map_id)

    def resume_automatic(self) -> None:
        self.manual = False
        self.identity.set_detected(self.map_id if self.resolution.status is MapResolutionStatus.RESOLVED else None)

    def _log(self, event: str, resolution: MapResolution, observation: MapInfoObservation | None) -> None:
        if self.journal is None:
            return
        try:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event": event, "at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                         "reading": observation.to_dict() if observation else None,
                                         **resolution.to_dict()}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
