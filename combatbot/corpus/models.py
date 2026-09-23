"""Schémas JSON versionnés du corpus et de la vérité terrain humaine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from combatbot.vision.coordinates import CombatPoint


SCHEMA_VERSION = 1
USAGES = {"train", "validation", "test", "diagnostic"}


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
    schema_version: int = SCHEMA_VERSION

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
            "reference_cells_complete": self.reference_cells_complete,
        }
        result.update({key: value for key, value in scalar_values.items() if value is not None})
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
