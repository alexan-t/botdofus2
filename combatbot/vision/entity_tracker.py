"""Suivi global des entités par DofusCellId (LOT 3B-5).

Remplace l'association gloutonne « chaque détection prend la piste la plus proche » par une
affectation globale à coût minimal (algorithme hongrois exact, O(n³), sans dépendance externe :
scipy n'est pas installé et quelques entités seulement sont suivies).

- Le joueur a une piste dédiée ``player`` ; les ennemis ``enemy_1``, ``enemy_2``… Les catégories
  ne se mélangent jamais : une détection faible ne transforme pas un ENEMY en PLAYER.
- Une détection d'équipe inconnue (``EntityKind.UNKNOWN``) n'alimente aucune piste.
- OCCLUDED : absente brièvement ; ``cell_id`` vaut None, ``last_known_cell_id`` est conservé et
  la cellule n'est jamais déclarée occupée pour autant. LOST au-delà de la tolérance
  (temps ET frames, configurables).
- HELD (LOT 3B-5D, désactivé par défaut) : absente de la détection mais un sprite occupe toujours sa dernière cellule
  (``sprite_cells`` du détecteur) : la position est maintenue avec une confiance réduite. Un
  ennemi tué laisse une case vide : il n'est pas maintenu. Réglé sur TRAIN/VALIDATION seulement.
- Distance = distance topologique GameData (Manhattan en coordonnées logiques, équivalence au
  plus court chemin 4-connexe démontrée par test sur les 560 cellules), jamais en pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from combatbot.gamedata.topology import cell_to_grid
from combatbot.vision.entity_models import EntityEvidence, EntityKind, TrackedEntity, TrackState

INFINITE = float("inf")


def grid_distance(first: int, second: int) -> int:
    a, b = cell_to_grid(int(first)), cell_to_grid(int(second))
    return abs(a.x - b.x) + abs(a.y - b.y)


def hungarian(cost: list[list[float]]) -> list[tuple[int, int]]:
    """Affectation de coût minimal (lignes ↔ colonnes), exacte, rectangulaire.

    Les coûts infinis sont interdits : les paires correspondantes ne sont jamais renvoyées.
    """
    rows = len(cost)
    columns = len(cost[0]) if rows else 0
    if not rows or not columns:
        return []
    transpose = rows > columns
    matrix = [list(column) for column in zip(*cost)] if transpose else [list(row) for row in cost]
    n, m = len(matrix), len(matrix[0])
    finite = [value for row in matrix for value in row if value != INFINITE]
    big = (max(finite) if finite else 0.0) * (n + m + 1) + 1.0
    work = [[value if value != INFINITE else big for value in row] for row in matrix]
    # Potentiels (e-maxx), indices 1..n / 1..m.
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    match, way = [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        match[0], column = i, 0
        minimum, used = [math.inf] * (m + 1), [False] * (m + 1)
        while True:
            used[column] = True
            row, delta, next_column = match[column], math.inf, 0
            for j in range(1, m + 1):
                if not used[j]:
                    current = work[row - 1][j - 1] - u[row] - v[j]
                    if current < minimum[j]:
                        minimum[j], way[j] = current, column
                    if minimum[j] < delta:
                        delta, next_column = minimum[j], j
            for j in range(m + 1):
                if used[j]:
                    u[match[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            column = next_column
            if match[column] == 0:
                break
        while column:
            previous = way[column]
            match[column] = match[previous]
            column = previous
    pairs = []
    for j in range(1, m + 1):
        if match[j] and matrix[match[j] - 1][j - 1] != INFINITE:
            pairs.append((j - 1, match[j] - 1) if transpose else (match[j] - 1, j - 1))
    return sorted(pairs)


def greedy_assign(tracks: list[tuple[str, int]], detections: list[int], max_distance: int = 2) -> dict[int, str]:
    """Ancien algorithme (référence de comparaison) : détection par détection, piste la plus proche."""
    unused = dict(tracks)
    result: dict[int, str] = {}
    for index, cell_id in enumerate(detections):
        best, best_distance = None, INFINITE
        for track_id, track_cell in unused.items():
            distance = grid_distance(track_cell, cell_id)
            if distance < best_distance and distance <= max_distance:
                best, best_distance = track_id, distance
        if best is not None:
            result[index] = best
            del unused[best]
    return result


@dataclass(frozen=True)
class TrackerConfig:
    """Poids et tolérances configurables ; valeurs provisoires à mesurer sur séquences réelles."""

    w_distance: float = 1.0
    w_visual: float = 0.4
    w_confidence: float = 0.3
    w_time: float = 0.2
    max_jump_cells: int = 6            # saut maximal accepté entre deux frames rapprochées
    jump_cells_per_second: float = 2.0  # tolérance supplémentaire quand du temps s'est écoulé
    max_hue_difference: float = 30.0   # teinte d'anneau (OpenCV, 0..180)
    min_move_confidence: float = 0.6   # en dessous, une détection ne peut pas déplacer une piste
    new_track_confidence: float = 0.5
    occlusion_frames: int = 6
    occlusion_seconds: float = 4.0
    player_confirm_frames: int = 2     # saut du joueur hors gabarit : confirmé à la même cellule
    # 3B-5D : maintien si un sprite reste sur la dernière cellule. DÉSACTIVÉ : sur le TEST gelé
    # 6b72202, 3 maintiens sur 4 étaient faux (ennemi déplacé, ancienne case texturée).
    hold_with_sprite: bool = False
    hold_confidence_factor: float = 0.5


@dataclass
class _Track:
    track_id: str
    kind: EntityKind
    cell_id: int
    confidence: float
    age: int
    missed: int
    last_seen: float
    evidence: EntityEvidence | None
    hue: float | None


class EntityTracker:
    def __init__(self, config: TrackerConfig | None = None) -> None:
        self.config = config or TrackerConfig()
        self._tracks: dict[str, _Track] = {}
        self._lost: dict[str, _Track] = {}
        self._next_enemy = 1
        self._pending_player: tuple[int, int] | None = None
        self.last_diagnostics: dict[str, object] = {}

    # --------------------------------------------------------------- coûts
    def _gate(self, track: _Track, timestamp: float) -> float:
        elapsed = max(0.0, timestamp - track.last_seen)
        return self.config.max_jump_cells + self.config.jump_cells_per_second * elapsed

    def cost(self, track: _Track, detection: EntityEvidence, timestamp: float) -> float:
        config = self.config
        distance = grid_distance(track.cell_id, detection.cell_id)
        gate = self._gate(track, timestamp)
        if distance > gate:
            return INFINITE  # déplacement impossible dans le temps écoulé : rejeté
        if distance > 0 and detection.confidence < config.min_move_confidence:
            return INFINITE  # preuve trop faible pour déplacer une piste
        visual = 0.0
        if track.hue is not None and detection.marker_hue is not None:
            delta = abs(track.hue - detection.marker_hue) % 180.0
            visual = min(1.0, min(delta, 180.0 - delta) / config.max_hue_difference)
        elapsed = max(0.0, timestamp - track.last_seen)
        return (config.w_distance * distance / max(gate, 1e-6)
                + config.w_visual * visual
                + config.w_confidence * (1.0 - detection.confidence)
                + config.w_time * min(1.0, elapsed / max(config.occlusion_seconds, 1e-6)))

    # --------------------------------------------------------------- mise à jour
    def update(self, detections, timestamp: float) -> tuple[TrackedEntity, ...]:
        """``detections`` : EntityDetectionResult ou itérable d'EntityEvidence."""
        entities = tuple(getattr(detections, "entities", detections))
        # Sans ce diagnostic (appel historique avec une simple liste), aucun maintien.
        sprite_cells = set((getattr(detections, "diagnostics", None) or {}).get("sprite_cells", ()))
        observed: set[str] = set()
        diagnostics: dict[str, object] = {"assignments": [], "rejected": [], "new": []}
        player_detections = [item for item in entities if item.kind is EntityKind.PLAYER]
        enemy_detections = [item for item in entities if item.kind is EntityKind.ENEMY]
        self._update_player(player_detections, timestamp, observed, diagnostics)
        self._update_enemies(enemy_detections, timestamp, observed, diagnostics)
        output = []
        for track_id in list(self._tracks):
            track = self._tracks[track_id]
            if track_id in observed:
                output.append(self._public(track, TrackState.OBSERVED, True))
                continue
            track.missed += 1
            elapsed = timestamp - track.last_seen
            if track.missed > self.config.occlusion_frames or elapsed > self.config.occlusion_seconds:
                self._lost[track_id] = self._tracks.pop(track_id)
                output.append(self._public(track, TrackState.LOST, False))
            elif self.config.hold_with_sprite and track.cell_id in sprite_cells:
                output.append(self._public(track, TrackState.HELD, False, self.config.hold_confidence_factor))
            else:
                output.append(self._public(track, TrackState.OCCLUDED, False))
        self.last_diagnostics = diagnostics
        order = {EntityKind.PLAYER: 0, EntityKind.ENEMY: 1, EntityKind.UNKNOWN: 2}
        return tuple(sorted(output, key=lambda item: (order[item.kind], _numeric(item.track_id))))

    def _update_player(self, detections, timestamp, observed, diagnostics) -> None:
        if not detections:
            self._pending_player = None
            return
        detection = max(detections, key=lambda item: item.confidence)
        track = self._tracks.get("player") or self._lost.pop("player", None)
        if track is None:
            self._tracks["player"] = self._new_track("player", EntityKind.PLAYER, detection, timestamp)
            observed.add("player")
            diagnostics["new"].append("player")
            return
        if self.cost(track, detection, timestamp) == INFINITE and track.cell_id != detection.cell_id:
            # Saut hors gabarit : jamais accepté sur une seule frame ; confirmé s'il se répète.
            count = (self._pending_player[1] + 1 if self._pending_player
                     and self._pending_player[0] == detection.cell_id else 1)
            self._pending_player = (detection.cell_id, count)
            if count < self.config.player_confirm_frames:
                self._tracks["player"] = track
                diagnostics["rejected"].append(("player", detection.cell_id))
                return
        self._pending_player = None
        self._tracks["player"] = track
        self._apply(track, detection, timestamp)
        observed.add("player")

    def _update_enemies(self, detections, timestamp, observed, diagnostics) -> None:
        candidates = [track for track in self._tracks.values() if track.kind is EntityKind.ENEMY]
        # Une piste perdue récemment peut réapparaître : elle participe aussi à l'affectation.
        recent_lost = [track for track in self._lost.values() if track.kind is EntityKind.ENEMY
                       and timestamp - track.last_seen <= 2 * self.config.occlusion_seconds]
        pool = candidates + recent_lost
        matrix = [[self.cost(track, detection, timestamp) for detection in detections] for track in pool]
        assigned: set[int] = set()
        for row, column in hungarian(matrix) if pool and detections else ():
            track, detection = pool[row], detections[column]
            if track.track_id in self._lost:
                self._tracks[track.track_id] = self._lost.pop(track.track_id)
            self._apply(track, detection, timestamp)
            observed.add(track.track_id)
            assigned.add(column)
            diagnostics["assignments"].append((track.track_id, detection.cell_id, round(matrix[row][column], 3)))
        for column, detection in enumerate(detections):
            if column in assigned:
                continue
            if detection.confidence < self.config.new_track_confidence:
                diagnostics["rejected"].append(("enemy", detection.cell_id))
                continue
            track_id = f"enemy_{self._next_enemy}"
            self._next_enemy += 1
            self._tracks[track_id] = self._new_track(track_id, EntityKind.ENEMY, detection, timestamp)
            observed.add(track_id)
            diagnostics["new"].append(track_id)

    # --------------------------------------------------------------- outils
    @staticmethod
    def _new_track(track_id: str, kind: EntityKind, detection: EntityEvidence, timestamp: float) -> _Track:
        return _Track(track_id, kind, detection.cell_id, detection.confidence, 1, 0, timestamp,
                      detection, detection.marker_hue)

    @staticmethod
    def _apply(track: _Track, detection: EntityEvidence, timestamp: float) -> None:
        track.cell_id = detection.cell_id
        track.confidence = detection.confidence
        track.age += 1
        track.missed = 0
        track.last_seen = timestamp
        track.evidence = detection
        track.hue = detection.marker_hue if detection.marker_hue is not None else track.hue

    @staticmethod
    def _public(track: _Track, state: TrackState, observed: bool, held_factor: float = 0.0) -> TrackedEntity:
        held = state is TrackState.HELD
        return TrackedEntity(track.track_id, track.kind, track.cell_id if observed or held else None,
                             track.confidence if observed else track.confidence * held_factor if held else 0.0,
                             state, track.age, track.missed, track.last_seen, track.cell_id, observed,
                             track.evidence if observed else None)

    def tracks(self) -> dict[str, _Track]:
        return dict(self._tracks)


def _numeric(track_id: str) -> tuple[int, str]:
    suffix = track_id.rsplit("_", 1)[-1]
    return (int(suffix), track_id) if suffix.isdigit() else (-1, track_id)


__all__ = ["EntityTracker", "TrackerConfig", "grid_distance", "greedy_assign", "hungarian"]
