"""Schémas JSON versionnés du corpus et de la vérité terrain humaine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from combatbot.vision.coordinates import CombatPoint


SCHEMA_VERSION = 1
USAGES = {"train", "validation", "test", "diagnostic"}
HUD_CROP_QUALITIES = {
    "VALID", "CUT_LEFT", "CUT_RIGHT", "CUT_TOP", "CUT_BOTTOM",
    "WRONG_ROI", "HUD_OCCLUDED", "EMPTY", "OTHER",
}
# Provenance des vérités PA/PM. Une annotation sans provenance est un import non vérifié.
TRUTH_SOURCES = {"human_confirmed", "unverified_import"}
# Décision humaine par compteur lors de la revue HUD.
HUD_REVIEW_DECISIONS = {"confirmed", "corrected", "entered", "unreadable"}
# LOT 3B-5 : phase de la frame annotée (entités).
ENTITY_FRAME_PHASES = {"placement", "debut_combat", "mon_tour", "tour_ennemi", "animation_sort",
                       "changement_tour", "exploration", "autre"}
ENTITY_FIELDS = ("entity_annotation_source", "entity_confirmed_at", "player_cell_id_truth", "player_visibility",
                 "enemy_cells_truth", "enemy_occluded_tracks", "empty_confirmed_cells", "frame_phase",
                 "occlusion", "tactical_mode", "tracking_identity_confirmed", "tracking_identity_source")


def _optional_bool(value: object, field_name: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise ValueError(f"{field_name} doit être vrai, faux ou absent")


def _optional_counter(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 99:
        raise ValueError(f"{field_name} doit être un entier entre 0 et 99")
    return value


@dataclass(frozen=True)
class PixelAnnotation:
    """Centre humain dans le crop combat ; ``logical`` reste facultatif."""

    center: CombatPoint | tuple[int, int]
    logical: tuple[int, int] | None = None
    coordinate_space: Literal["combat"] = "combat"

    def __post_init__(self) -> None:
        if not isinstance(self.center, CombatPoint):
            object.__setattr__(self, "center", CombatPoint(*self.center))

    def validate(self) -> None:
        if self.coordinate_space != "combat":
            raise ValueError("Les annotations du corpus doivent utiliser le référentiel combat")
        if any(not float(v).is_integer() or v < 0 for v in self.center):
            raise ValueError("Le centre pixel doit contenir deux entiers positifs")
        if self.logical is not None and (
            len(self.logical) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) for v in self.logical)
        ):
            raise ValueError("La cellule logique doit contenir deux entiers")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        result: dict[str, object] = {
            "center": list(self.center.rounded()),
            "coordinate_space": self.coordinate_space,
        }
        if self.logical is not None:
            result["logical"] = list(self.logical)
        return result

    @classmethod
    def from_dict(cls, raw: object) -> "PixelAnnotation":
        if not isinstance(raw, dict):
            raise ValueError("Annotation pixel invalide")
        center = raw.get("center")
        logical = raw.get("logical")
        if not isinstance(center, (list, tuple)) or len(center) != 2:
            raise ValueError("Centre pixel absent")
        item = cls(
            (int(center[0]), int(center[1])),
            (int(logical[0]), int(logical[1]))
            if isinstance(logical, (list, tuple)) and len(logical) == 2 else None,
            str(raw.get("coordinate_space", "combat")),  # type: ignore[arg-type]
        )
        item.validate()
        return item


@dataclass(frozen=True)
class Annotation:
    """Vérité terrain indépendante ; chaque champ peut rester absent."""

    observation_id: str
    combat_truth: bool | None = None
    player_turn_truth: bool | None = None
    ap_truth: int | None = None
    mp_truth: int | None = None
    player: PixelAnnotation | None = None
    enemies: tuple[PixelAnnotation, ...] = ()
    reference_cells: tuple[PixelAnnotation, ...] = ()
    reference_cells_complete: bool | None = None
    grid_anchors: tuple[PixelAnnotation, ...] = ()
    comments: str | None = None
    digit_issue: str | None = None
    ap_crop_quality: str | None = None
    mp_crop_quality: str | None = None
    hud_burst_id: str | None = None
    truth_source: str | None = None
    confirmed_at: str | None = None
    confirmed_by: str | None = None
    session_id: str | None = None
    hud_review: dict[str, str] | None = None
    truth_history: tuple[dict[str, object], ...] = ()
    # LOT 3B-5 : vérité entités par DofusCellId (jamais une ancienne prédiction).
    entity_annotation_source: str | None = None
    entity_confirmed_at: str | None = None
    player_cell_id_truth: int | None = None
    player_visibility: str | None = None                 # VISIBLE | NOT_VISIBLE
    enemy_cells_truth: tuple[dict[str, object], ...] = ()  # {"cell_id": int, "track_id": "E1" | None}
    enemy_occluded_tracks: tuple[str, ...] = ()           # ennemis connus mais non localisables
    empty_confirmed_cells: tuple[int, ...] = ()
    frame_phase: str | None = None
    occlusion: bool | None = None
    tracking_identity_confirmed: bool = False
    tracking_identity_source: str | None = None
    tactical_mode: bool | None = None
    schema_version: int = SCHEMA_VERSION

    @property
    def entities_confirmed(self) -> bool:
        return self.entity_annotation_source == "human_confirmed" and bool(self.entity_confirmed_at)

    def validate_entities(self) -> None:
        _optional_bool(self.tracking_identity_confirmed, "tracking_identity_confirmed")
        if self.tracking_identity_confirmed and not self.tracking_identity_source:
            raise ValueError("Une identité suivie exige sa provenance explicite")
        if self.entity_annotation_source not in (None, "human_confirmed"):
            raise ValueError("entity_annotation_source doit valoir human_confirmed")
        if self.entity_annotation_source == "human_confirmed" and not self.entity_confirmed_at:
            raise ValueError("Une vérité entités human_confirmed exige entity_confirmed_at")
        if self.player_visibility not in (None, "VISIBLE", "NOT_VISIBLE"):
            raise ValueError("player_visibility invalide")
        if self.player_visibility == "NOT_VISIBLE" and self.player_cell_id_truth is not None:
            raise ValueError("Joueur non visible : aucune cellule joueur ne doit être inscrite")
        if self.frame_phase is not None and self.frame_phase not in ENTITY_FRAME_PHASES:
            raise ValueError(f"frame_phase non reconnu : {self.frame_phase}")
        cells: list[int] = []
        tracks: list[str] = []
        for item in self.enemy_cells_truth:
            cell_id = item.get("cell_id")
            if isinstance(cell_id, bool) or not isinstance(cell_id, int) or not 0 <= cell_id < 560:
                raise ValueError("Cellule ennemie invalide")
            cells.append(cell_id)
            if item.get("track_id"):
                tracks.append(str(item["track_id"]))
        tracks.extend(self.enemy_occluded_tracks)
        if len(cells) != len(set(cells)):
            raise ValueError("Une cellule ne peut porter qu'un ennemi annoté")
        if len(tracks) != len(set(tracks)):
            raise ValueError("Un identifiant d'ennemi ne peut apparaître qu'une fois par frame")
        if self.player_cell_id_truth is not None:
            if (isinstance(self.player_cell_id_truth, bool) or not isinstance(self.player_cell_id_truth, int)
                    or not 0 <= self.player_cell_id_truth < 560):
                raise ValueError("Cellule joueur invalide")
            if self.player_cell_id_truth in cells:
                raise ValueError("Une cellule ne peut pas être à la fois PLAYER et ENEMY")
        occupied = set(cells) | ({self.player_cell_id_truth} if self.player_cell_id_truth is not None else set())
        if occupied & set(self.empty_confirmed_cells):
            raise ValueError("Une cellule vide confirmée ne peut pas porter d'entité")
        if any(isinstance(cell, bool) or not isinstance(cell, int) or not 0 <= cell < 560
               for cell in self.empty_confirmed_cells):
            raise ValueError("Cellule vide confirmée invalide")
        if self.player_visibility == "VISIBLE" and self.player_cell_id_truth is None:
            raise ValueError("Joueur VISIBLE : exactement une cellule joueur est requise")
        if self.entities_confirmed and self.player_visibility is None:
            raise ValueError("Une vérité entités exige player_visibility")

    @property
    def human_confirmed(self) -> bool:
        """Seule une confirmation humaine traçable fait des PA/PM une vérité finale."""
        return self.truth_source == "human_confirmed" and bool(self.confirmed_at)

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Version d'annotation non prise en charge : {self.schema_version}")
        if not self.observation_id.strip():
            raise ValueError("Identifiant d'observation requis")
        _optional_bool(self.combat_truth, "combat_truth")
        _optional_bool(self.player_turn_truth, "player_turn_truth")
        _optional_counter(self.ap_truth, "ap_truth")
        _optional_counter(self.mp_truth, "mp_truth")
        _optional_bool(self.reference_cells_complete, "reference_cells_complete")
        if self.player is not None:
            self.player.validate()
        for collection in (self.enemies, self.reference_cells, self.grid_anchors):
            for item in collection:
                item.validate()
        if self.digit_issue not in (None, "1_vs_7"):
            raise ValueError("digit_issue non reconnu")
        for value, field_name in (
            (self.ap_crop_quality, "ap_crop_quality"),
            (self.mp_crop_quality, "mp_crop_quality"),
        ):
            if value is not None and value not in HUD_CROP_QUALITIES:
                raise ValueError(f"{field_name} non reconnu : {value}")
        if self.hud_burst_id is not None and not self.hud_burst_id.strip():
            raise ValueError("hud_burst_id ne peut pas être vide")
        if self.truth_source is not None and self.truth_source not in TRUTH_SOURCES:
            raise ValueError(f"truth_source non reconnu : {self.truth_source}")
        if self.truth_source == "human_confirmed" and not self.confirmed_at:
            raise ValueError("Une vérité human_confirmed exige confirmed_at")
        for kind, decision in (self.hud_review or {}).items():
            if kind not in ("ap", "mp") or decision not in HUD_REVIEW_DECISIONS:
                raise ValueError(f"Décision de revue HUD invalide : {kind}={decision}")
        for kind in ("ap", "mp"):
            truth = self.ap_truth if kind == "ap" else self.mp_truth
            if (self.hud_review or {}).get(kind) == "unreadable" and truth is not None:
                raise ValueError(f"{kind.upper()} marqué illisible ne peut pas porter de vérité")
        self.validate_entities()

    def to_dict(self) -> dict[str, object]:
        self.validate()
        result: dict[str, object] = {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
        }
        scalar_values = {
            "combat_truth": self.combat_truth,
            "player_turn_truth": self.player_turn_truth,
            "ap_truth": self.ap_truth,
            "mp_truth": self.mp_truth,
            "comments": self.comments.strip() if self.comments else None,
            "digit_issue": self.digit_issue,
            "ap_crop_quality": self.ap_crop_quality,
            "mp_crop_quality": self.mp_crop_quality,
            "hud_burst_id": self.hud_burst_id.strip() if self.hud_burst_id else None,
            "reference_cells_complete": self.reference_cells_complete,
            "truth_source": self.truth_source,
            "confirmed_at": self.confirmed_at,
            "confirmed_by": self.confirmed_by,
            "session_id": self.session_id,
            "hud_review": dict(self.hud_review) if self.hud_review else None,
            "entity_annotation_source": self.entity_annotation_source,
            "entity_confirmed_at": self.entity_confirmed_at,
            "player_cell_id_truth": self.player_cell_id_truth,
            "player_visibility": self.player_visibility,
            "frame_phase": self.frame_phase,
            "occlusion": self.occlusion,
            "tactical_mode": self.tactical_mode,
            "tracking_identity_confirmed": self.tracking_identity_confirmed,
            "tracking_identity_source": self.tracking_identity_source,
        }
        result.update({key: value for key, value in scalar_values.items() if value is not None})
        if self.truth_history:
            result["truth_history"] = [dict(item) for item in self.truth_history]
        if self.enemy_cells_truth:
            result["enemy_cells_truth"] = [dict(item) for item in self.enemy_cells_truth]
        if self.enemy_occluded_tracks:
            result["enemy_occluded_tracks"] = list(self.enemy_occluded_tracks)
        if self.empty_confirmed_cells:
            result["empty_confirmed_cells"] = list(self.empty_confirmed_cells)
        if self.player is not None:
            result["player"] = self.player.to_dict()
        for key, values in (
            ("enemies", self.enemies),
            ("reference_cells", self.reference_cells),
            ("grid_anchors", self.grid_anchors),
        ):
            if values:
                result[key] = [item.to_dict() for item in values]
        return result

    @classmethod
    def from_dict(cls, raw: object) -> "Annotation":
        if not isinstance(raw, dict):
            raise ValueError("Annotation JSON invalide")

        def many(name: str) -> tuple[PixelAnnotation, ...]:
            value = raw.get(name, ())
            if not isinstance(value, (list, tuple)):
                raise ValueError(f"{name} doit être une liste")
            return tuple(PixelAnnotation.from_dict(item) for item in value)

        player_raw = raw.get("player")
        item = cls(
            observation_id=str(raw.get("observation_id", "")),
            combat_truth=_optional_bool(raw.get("combat_truth"), "combat_truth"),
            player_turn_truth=_optional_bool(raw.get("player_turn_truth"), "player_turn_truth"),
            ap_truth=_optional_counter(raw.get("ap_truth"), "ap_truth"),
            mp_truth=_optional_counter(raw.get("mp_truth"), "mp_truth"),
            player=PixelAnnotation.from_dict(player_raw) if player_raw is not None else None,
            enemies=many("enemies"),
            reference_cells=many("reference_cells"),
            reference_cells_complete=_optional_bool(
                raw.get("reference_cells_complete"), "reference_cells_complete"
            ),
            grid_anchors=many("grid_anchors"),
            comments=str(raw["comments"]) if raw.get("comments") is not None else None,
            digit_issue=str(raw["digit_issue"]) if raw.get("digit_issue") is not None else None,
            ap_crop_quality=(str(raw["ap_crop_quality"])
                             if raw.get("ap_crop_quality") is not None else None),
            mp_crop_quality=(str(raw["mp_crop_quality"])
                             if raw.get("mp_crop_quality") is not None else None),
            hud_burst_id=str(raw["hud_burst_id"]) if raw.get("hud_burst_id") is not None else None,
            truth_source=str(raw["truth_source"]) if raw.get("truth_source") is not None else None,
            confirmed_at=str(raw["confirmed_at"]) if raw.get("confirmed_at") is not None else None,
            confirmed_by=str(raw["confirmed_by"]) if raw.get("confirmed_by") is not None else None,
            session_id=str(raw["session_id"]) if raw.get("session_id") is not None else None,
            hud_review=({str(key): str(value) for key, value in raw["hud_review"].items()}
                        if isinstance(raw.get("hud_review"), dict) else None),
            truth_history=tuple(dict(item) for item in raw.get("truth_history", ())
                                if isinstance(item, dict)),
            entity_annotation_source=(str(raw["entity_annotation_source"])
                                      if raw.get("entity_annotation_source") is not None else None),
            entity_confirmed_at=(str(raw["entity_confirmed_at"])
                                 if raw.get("entity_confirmed_at") is not None else None),
            player_cell_id_truth=(int(raw["player_cell_id_truth"])
                                  if raw.get("player_cell_id_truth") is not None else None),
            player_visibility=str(raw["player_visibility"]) if raw.get("player_visibility") is not None else None,
            enemy_cells_truth=tuple({"cell_id": int(item["cell_id"]),
                                     "track_id": str(item["track_id"]) if item.get("track_id") else None}
                                    for item in raw.get("enemy_cells_truth", ()) if isinstance(item, dict)),
            enemy_occluded_tracks=tuple(str(item) for item in raw.get("enemy_occluded_tracks", ())),
            empty_confirmed_cells=tuple(int(item) for item in raw.get("empty_confirmed_cells", ())),
            frame_phase=str(raw["frame_phase"]) if raw.get("frame_phase") is not None else None,
            occlusion=_optional_bool(raw.get("occlusion"), "occlusion"),
            tactical_mode=_optional_bool(raw.get("tactical_mode"), "tactical_mode"),
            tracking_identity_confirmed=_optional_bool(raw.get("tracking_identity_confirmed", False), "tracking_identity_confirmed"),
            tracking_identity_source=raw.get("tracking_identity_source"),
            schema_version=int(raw.get("schema_version", SCHEMA_VERSION)),
        )
        item.validate()
        return item


@dataclass(frozen=True)
class CorpusEntry:
    observation_id: str
    session_id: str
    frame_index: int
    paths: dict[str, str]
    annotation_available: bool = False
    tags: tuple[str, ...] = ()
    map_name: str | None = None
    usage: Literal["train", "validation", "test", "diagnostic"] = "diagnostic"
    ap_truth: int | None = None
    mp_truth: int | None = None
    known_errors: tuple[str, ...] = ()
    # LOT 3B-5 : copie de la vérité entités humaine (jamais une prédiction).
    player_cell_id_truth: int | None = None
    enemy_cells_truth: tuple[int, ...] = ()
    enemy_track_truth: dict[str, int] | None = None
    entity_annotation_source: str | None = None

    def validate(self) -> None:
        if not self.observation_id.strip() or not self.session_id.strip():
            raise ValueError("Identifiants corpus et session requis")
        if isinstance(self.frame_index, bool) or self.frame_index < 0:
            raise ValueError("frame_index doit être positif")
        if self.usage not in USAGES:
            raise ValueError(f"Usage invalide : {self.usage}")
        for key, value in self.paths.items():
            if not key or not value or value.startswith(("/", "\\")) or ":" in value:
                raise ValueError("Les chemins du manifeste doivent être relatifs")
            if ".." in value.replace("\\", "/").split("/"):
                raise ValueError("Un chemin du manifeste sort du corpus")
        _optional_counter(self.ap_truth, "ap_truth")
        _optional_counter(self.mp_truth, "mp_truth")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        result: dict[str, object] = {
            "observation_id": self.observation_id,
            "session_id": self.session_id,
            "frame_index": self.frame_index,
            "paths": dict(self.paths),
            "annotation_available": self.annotation_available,
            "tags": list(self.tags),
            "usage": self.usage,
            "known_errors": list(self.known_errors),
        }
        if self.map_name:
            result["map"] = self.map_name
        if self.ap_truth is not None:
            result["ap_truth"] = self.ap_truth
        if self.mp_truth is not None:
            result["mp_truth"] = self.mp_truth
        if self.entity_annotation_source is not None:
            result["entity_annotation_source"] = self.entity_annotation_source
            result["player_cell_id_truth"] = self.player_cell_id_truth
            result["enemy_cells_truth"] = list(self.enemy_cells_truth)
            result["enemy_track_truth"] = dict(self.enemy_track_truth or {})
        return result

    @classmethod
    def from_dict(cls, raw: object) -> "CorpusEntry":
        if not isinstance(raw, dict) or not isinstance(raw.get("paths"), dict):
            raise ValueError("Entrée de manifeste invalide")
        item = cls(
            observation_id=str(raw.get("observation_id", "")),
            session_id=str(raw.get("session_id", "")),
            frame_index=int(raw.get("frame_index", -1)),
            paths={str(k): str(v) for k, v in raw["paths"].items()},
            annotation_available=bool(raw.get("annotation_available", False)),
            tags=tuple(str(value) for value in raw.get("tags", ())),
            map_name=str(raw["map"]) if raw.get("map") else None,
            usage=str(raw.get("usage", "diagnostic")),  # type: ignore[arg-type]
            ap_truth=_optional_counter(raw.get("ap_truth"), "ap_truth"),
            mp_truth=_optional_counter(raw.get("mp_truth"), "mp_truth"),
            known_errors=tuple(str(value) for value in raw.get("known_errors", ())),
            player_cell_id_truth=(int(raw["player_cell_id_truth"])
                                  if raw.get("player_cell_id_truth") is not None else None),
            enemy_cells_truth=tuple(int(value) for value in raw.get("enemy_cells_truth", ())),
            enemy_track_truth=({str(key): int(value) for key, value in raw["enemy_track_truth"].items()}
                               if isinstance(raw.get("enemy_track_truth"), dict) else None),
            entity_annotation_source=(str(raw["entity_annotation_source"])
                                      if raw.get("entity_annotation_source") is not None else None),
        )
        item.validate()
        return item


@dataclass(frozen=True)
class CorpusManifest:
    entries: tuple[CorpusEntry, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Version de manifeste non prise en charge : {self.schema_version}")
        identifiers: set[str] = set()
        frames: set[tuple[str, int]] = set()
        for entry in self.entries:
            entry.validate()
            if entry.observation_id in identifiers:
                raise ValueError(f"Observation dupliquée : {entry.observation_id}")
            if (entry.session_id, entry.frame_index) in frames:
                raise ValueError(f"Frame dupliquée : {entry.session_id}/{entry.frame_index}")
            identifiers.add(entry.observation_id)
            frames.add((entry.session_id, entry.frame_index))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {"schema_version": self.schema_version, "entries": [entry.to_dict() for entry in self.entries]}

    @classmethod
    def from_dict(cls, raw: object) -> "CorpusManifest":
        if not isinstance(raw, dict) or not isinstance(raw.get("entries", ()), list):
            raise ValueError("Manifeste JSON invalide")
        result = cls(
            tuple(CorpusEntry.from_dict(item) for item in raw.get("entries", ())),
            int(raw.get("schema_version", SCHEMA_VERSION)),
        )
        result.validate()
        return result
