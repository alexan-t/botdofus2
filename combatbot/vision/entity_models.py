"""Modèles purs des entités de combat (LOT 3B-5), sans Qt ni OpenCV.

L'identité géométrique d'une entité est toujours un ``DofusCellId`` de la grille GameData
projetée, jamais un contour ni un pixel brut.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class EntityKind(str, Enum):
    PLAYER = "PLAYER"
    ENEMY = "ENEMY"
    UNKNOWN = "UNKNOWN"


class TrackState(str, Enum):
    OBSERVED = "OBSERVED"
    # LOT 3B-5D : marqueur invisible mais un sprite occupe toujours la dernière cellule observée
    # (animation de sort, portée bleue, bulle) : la position est maintenue, non observée.
    HELD = "HELD"
    OCCLUDED = "OCCLUDED"
    LOST = "LOST"


def _float_map(raw: object) -> dict[str, float]:
    return {str(key): float(value) for key, value in raw.items()} if isinstance(raw, Mapping) else {}


@dataclass(frozen=True)
class EntityEvidence:
    """Preuves brutes d'une présence sur une cellule ; la confiance reste explicable."""

    cell_id: int
    kind: EntityKind
    confidence: float
    marker_score: float = 0.0
    color_score: float = 0.0
    shape_score: float = 0.0
    background_score: float = 0.0
    visibility_score: float = 0.0
    profile_score: float = 0.0
    marker_hue: float | None = None
    reasons: tuple[str, ...] = ()
    center: tuple[int, int] | None = None
    marker_state: str = "FULL_RING"  # FULL_RING | PARTIAL_RING (LOT 3B-5B)

    def to_dict(self) -> dict[str, object]:
        return {
            "cell_id": self.cell_id, "kind": self.kind.value, "confidence": self.confidence,
            "marker_score": self.marker_score, "color_score": self.color_score,
            "shape_score": self.shape_score, "background_score": self.background_score,
            "visibility_score": self.visibility_score, "profile_score": self.profile_score,
            "marker_hue": self.marker_hue, "reasons": list(self.reasons),
            "center": list(self.center) if self.center is not None else None,
            "marker_state": self.marker_state,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "EntityEvidence":
        center = raw.get("center")
        return cls(
            int(raw["cell_id"]), EntityKind(str(raw.get("kind", "UNKNOWN"))), float(raw.get("confidence", 0.0)),
            float(raw.get("marker_score", 0.0)), float(raw.get("color_score", 0.0)),
            float(raw.get("shape_score", 0.0)), float(raw.get("background_score", 0.0)),
            float(raw.get("visibility_score", 0.0)), float(raw.get("profile_score", 0.0)),
            float(raw["marker_hue"]) if raw.get("marker_hue") is not None else None,
            tuple(str(item) for item in raw.get("reasons", ())),
            (int(center[0]), int(center[1])) if isinstance(center, (list, tuple)) and len(center) == 2 else None,
            str(raw.get("marker_state", "FULL_RING")),
        )


@dataclass(frozen=True)
class EntityDetectionResult:
    """Sortie du détecteur par cellule : aucune identité temporelle ici."""

    entities: tuple[EntityEvidence, ...] = ()
    per_cell: dict[int, dict[str, float]] = field(default_factory=dict)
    occupancy: dict[int, str] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)
    diagnostics: dict[str, object] = field(default_factory=dict)

    @property
    def player(self) -> EntityEvidence | None:
        return next((item for item in self.entities if item.kind is EntityKind.PLAYER), None)

    @property
    def enemies(self) -> tuple[EntityEvidence, ...]:
        return tuple(item for item in self.entities if item.kind is EntityKind.ENEMY)

    def occupancy_summary(self) -> dict[str, int]:
        summary = {"OCCUPIED": 0, "FREE": 0, "UNKNOWN": 0, "BLOCKED": 0}
        for state in self.occupancy.values():
            summary[state] = summary.get(state, 0) + 1
        return summary


@dataclass(frozen=True)
class TrackedEntity:
    """Piste temporelle. ``cell_id`` est la cellule observée ; en OCCLUDED elle vaut None
    et ``last_known_cell_id`` garde la dernière position observée (jamais « occupée »).
    En HELD, ``cell_id`` est la position maintenue (sprite présent) avec ``observed_this_frame``
    faux : c'est une affirmation de position, de confiance réduite, pas une observation."""

    track_id: str
    kind: EntityKind
    cell_id: int | None
    confidence: float
    state: TrackState
    age: int
    missed_frames: int
    last_seen: float
    last_known_cell_id: int | None
    observed_this_frame: bool
    evidence: EntityEvidence | None = None

    @property
    def claimed_cell(self) -> int | None:
        """Cellule affirmée cette frame : observée, ou maintenue (HELD)."""
        return self.cell_id if self.state in (TrackState.OBSERVED, TrackState.HELD) else None

    def to_dict(self) -> dict[str, object]:
        return {
            "track_id": self.track_id, "kind": self.kind.value, "cell_id": self.cell_id,
            "confidence": self.confidence, "state": self.state.value, "age": self.age,
            "missed_frames": self.missed_frames, "last_seen": self.last_seen,
            "last_known_cell_id": self.last_known_cell_id, "observed_this_frame": self.observed_this_frame,
            "evidence": self.evidence.to_dict() if self.evidence is not None else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TrackedEntity":
        evidence = raw.get("evidence")
        return cls(
            str(raw["track_id"]), EntityKind(str(raw.get("kind", "UNKNOWN"))),
            int(raw["cell_id"]) if raw.get("cell_id") is not None else None,
            float(raw.get("confidence", 0.0)), TrackState(str(raw.get("state", "OBSERVED"))),
            int(raw.get("age", 0)), int(raw.get("missed_frames", 0)), float(raw.get("last_seen", 0.0)),
            int(raw["last_known_cell_id"]) if raw.get("last_known_cell_id") is not None else None,
            bool(raw.get("observed_this_frame", False)),
            EntityEvidence.from_dict(evidence) if isinstance(evidence, Mapping) else None,
        )


@dataclass(frozen=True)
class MarkerColorClass:
    """Classe de couleur d'anneau mesurée (teinte OpenCV 0..180, circulaire)."""

    hue: float
    hue_tolerance: float
    min_saturation: float
    min_value: float
    samples: int
    # LOT 3B-5B : tolérance mesurée au niveau pixel (p95 des traits TRAIN) et autorisation
    # de l'anneau partiel, accordée seulement si ce chemin n'a touché aucune vérité contraire en TRAIN.
    pixel_tolerance: float | None = None
    partial_allowed: bool = False
    method: str = "full_ring"
    partial_gate: dict[str, int] | None = None

    def hue_distance(self, hue: float) -> float:
        delta = abs(float(hue) - self.hue) % 180.0
        return min(delta, 180.0 - delta)

    def matches(self, hue: float | None) -> bool:
        return hue is not None and self.hue_distance(hue) <= self.hue_tolerance

    def to_dict(self) -> dict[str, object]:
        return {"hue": self.hue, "hue_tolerance": self.hue_tolerance, "min_saturation": self.min_saturation,
                "min_value": self.min_value, "samples": self.samples, "pixel_tolerance": self.pixel_tolerance,
                "partial_allowed": self.partial_allowed, "method": self.method,
                "partial_gate": dict(self.partial_gate) if self.partial_gate else None}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MarkerColorClass":
        gate = raw.get("partial_gate")
        return cls(float(raw["hue"]), float(raw["hue_tolerance"]), float(raw.get("min_saturation", 0.0)),
                   float(raw.get("min_value", 0.0)), int(raw.get("samples", 0)),
                   float(raw["pixel_tolerance"]) if raw.get("pixel_tolerance") is not None else None,
                   bool(raw.get("partial_allowed", False)), str(raw.get("method", "full_ring")),
                   {str(k): int(v) for k, v in gate.items()} if isinstance(gate, Mapping) else None)


@dataclass(frozen=True)
class TeamMarkerProfile:
    """Couleurs d'équipe dérivées d'annotations humaines (jamais une constante DOFUS).

    ``player_team`` : anneau de l'équipe du joueur ; ``enemy_team`` : anneau des ennemis.
    """

    player_team: MarkerColorClass | None
    enemy_team: MarkerColorClass | None
    layout_signature: str | None = None
    provenance: str = "human_confirmed"
    created_at: str | None = None
    source: str | None = None
    schema_version: int = 1

    def compatible(self, layout_signature: str | None) -> bool:
        return (self.provenance == "human_confirmed" and bool(self.layout_signature)
                and self.layout_signature == layout_signature)

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "provenance": self.provenance,
                "layout_signature": self.layout_signature, "created_at": self.created_at, "source": self.source,
                "player_team": self.player_team.to_dict() if self.player_team else None,
                "enemy_team": self.enemy_team.to_dict() if self.enemy_team else None}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TeamMarkerProfile":
        if int(raw.get("schema_version", 1)) != 1:
            raise ValueError("Version de TeamMarkerProfile non prise en charge")
        if raw.get("provenance") != "human_confirmed":
            raise ValueError("Un profil d'équipe doit être confirmé par l'utilisateur")
        player, enemy = raw.get("player_team"), raw.get("enemy_team")
        return cls(MarkerColorClass.from_dict(player) if isinstance(player, Mapping) else None,
                   MarkerColorClass.from_dict(enemy) if isinstance(enemy, Mapping) else None,
                   raw.get("layout_signature"), str(raw.get("provenance", "human_confirmed")),
                   raw.get("created_at"), raw.get("source"))


@dataclass(frozen=True)
class PlayerVisualProfile:
    """Profil du personnage confirmé par l'utilisateur sur une capture PythonBot.

    Mesures par sous-région (anneau, pieds, centre) en Lab, pas une moyenne HSV du sprite.
    Versionné et lié à un layout : il n'est jamais appliqué à un layout incompatible.
    """

    marker: MarkerColorClass
    foot_lab: tuple[float, float, float]
    center_lab: tuple[float, float, float]
    lab_tolerance: float
    layout_signature: str | None
    created_at: str
    samples: int
    provenance: str = "human_confirmed"
    source_cell_id: int | None = None
    schema_version: int = 1

    def compatible(self, layout_signature: str | None) -> bool:
        return (self.provenance == "human_confirmed" and bool(self.layout_signature)
                and self.layout_signature == layout_signature)

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "provenance": self.provenance,
                "layout_signature": self.layout_signature, "created_at": self.created_at,
                "samples": self.samples, "source_cell_id": self.source_cell_id,
                "marker": self.marker.to_dict(), "foot_lab": list(self.foot_lab),
                "center_lab": list(self.center_lab), "lab_tolerance": self.lab_tolerance}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PlayerVisualProfile":
        if int(raw.get("schema_version", 1)) != 1:
            raise ValueError("Version de PlayerVisualProfile non prise en charge")
        if raw.get("provenance") != "human_confirmed":
            raise ValueError("Un profil joueur doit être confirmé par l'utilisateur")
        return cls(MarkerColorClass.from_dict(raw["marker"]),
                   tuple(float(v) for v in raw["foot_lab"]),  # type: ignore[arg-type]
                   tuple(float(v) for v in raw["center_lab"]),  # type: ignore[arg-type]
                   float(raw["lab_tolerance"]), raw.get("layout_signature"), str(raw["created_at"]),
                   int(raw.get("samples", 1)), "human_confirmed",
                   int(raw["source_cell_id"]) if raw.get("source_cell_id") is not None else None)


    def as_v2(self) -> "PlayerVisualProfileV2":
        """Désignation unique (« cette cellule est mon personnage ») = profil V2 à un prototype."""
        return PlayerVisualProfileV2(
            self.marker, ((*self.foot_lab, *self.center_lab),), PLAYER_DISTANCE_TOLERANCE_DEFAULT, PLAYER_MARGIN_DEFAULT,
            self.layout_signature, self.created_at, 1, {}, self.provenance)


# Distance ΔE (moyenne pieds/centre) au prototype le plus proche, et marge sur le candidat suivant
# de la même équipe. Choisies en leave-one-frame-out sur TRAIN (LOT 3B-5B) : 0 mauvais joueur
# de 15 à 30 / marge 3 à 8 ; 15 perd un joueur correct ; 20 / 5 est dans le plateau.
PLAYER_DISTANCE_TOLERANCE_DEFAULT = 20.0
PLAYER_MARGIN_DEFAULT = 5.0


@dataclass(frozen=True)
class PlayerVisualProfileV2:
    """Profil joueur multi-exemples : plusieurs apparences réelles, jamais une moyenne aveugle.

    Chaque prototype = (L,a,b pieds, L,a,b centre) d'une vérité PLAYER human_confirmed de TRAIN.
    La distance d'un candidat est la plus petite distance à un prototype ; la décision exige une
    tolérance et une marge devant les autres candidats de la même équipe (sinon UNKNOWN).
    """

    marker: MarkerColorClass
    prototypes: tuple[tuple[float, ...], ...]
    distance_tolerance: float
    margin: float
    layout_signature: str | None
    created_at: str
    accepted: int
    rejected: dict[str, int]
    provenance: str = "human_confirmed"
    dispersion: dict[str, float] | None = None
    schema_version: int = 2

    def compatible(self, layout_signature: str | None) -> bool:
        return (self.provenance == "human_confirmed" and bool(self.layout_signature)
                and self.layout_signature == layout_signature)

    def distance(self, vector) -> float:
        best = float("inf")
        for prototype in self.prototypes:
            foot = sum((float(vector[k]) - prototype[k]) ** 2 for k in range(3)) ** 0.5
            centre = sum((float(vector[k]) - prototype[k]) ** 2 for k in range(3, 6)) ** 0.5
            best = min(best, 0.5 * (foot + centre))
        return best

    def similarity(self, vector) -> float:
        return max(0.0, 1.0 - self.distance(vector) / max(self.distance_tolerance, 1e-6))

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "provenance": self.provenance,
                "layout_signature": self.layout_signature, "created_at": self.created_at,
                "marker": self.marker.to_dict(), "prototypes": [list(p) for p in self.prototypes],
                "distance_tolerance": self.distance_tolerance, "margin": self.margin,
                "accepted": self.accepted, "rejected": dict(self.rejected),
                "dispersion": dict(self.dispersion) if self.dispersion else None}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PlayerVisualProfileV2":
        if int(raw.get("schema_version", 2)) != 2:
            raise ValueError("Version de PlayerVisualProfileV2 non prise en charge")
        if raw.get("provenance") != "human_confirmed":
            raise ValueError("Un profil joueur doit être confirmé par l'utilisateur")
        return cls(MarkerColorClass.from_dict(raw["marker"]),
                   tuple(tuple(float(v) for v in p) for p in raw["prototypes"]),
                   float(raw["distance_tolerance"]), float(raw.get("margin", PLAYER_MARGIN_DEFAULT)),
                   raw.get("layout_signature"), str(raw["created_at"]), int(raw.get("accepted", 0)),
                   {str(k): int(v) for k, v in (raw.get("rejected") or {}).items()}, "human_confirmed",
                   {str(k): float(v) for k, v in raw["dispersion"].items()} if raw.get("dispersion") else None)


def player_profile_from_dict(raw: Mapping[str, Any]):
    """V1 (désignation unique) ou V2 (multi-exemples) selon ``schema_version``."""
    return PlayerVisualProfileV2.from_dict(raw) if int(raw.get("schema_version", 1)) == 2 \
        else PlayerVisualProfile.from_dict(raw)


@dataclass(frozen=True)
class VisualProfiles:
    player: PlayerVisualProfile | PlayerVisualProfileV2 | None = None
    teams: TeamMarkerProfile | None = None

    def player_v2(self) -> PlayerVisualProfileV2 | None:
        if self.player is None:
            return None
        return self.player if isinstance(self.player, PlayerVisualProfileV2) else self.player.as_v2()
