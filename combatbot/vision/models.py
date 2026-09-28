"""Données observées et reconnues, séparées des modèles du simulateur."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np

from combatbot.vision.coordinates import (
    ClientBox, ClientSize, LayoutCompatibility, LayoutCompatibilityReason,
    LayoutSignature, LayoutTransform, NormalizedRect, ScreenPoint, ScreenRect,
    compatibility,
)


ZONE_NAMES = ("combat", "spell_bar", "hp", "ap", "mp", "end_turn")
ZONE_LABELS = {
    "combat": "Zone de combat",
    "spell_bar": "Barre de sorts",
    "hp": "Points de vie",
    "ap": "Compteur PA",
    "mp": "Compteur PM",
    "end_turn": "Fin de tour",
    "identity": "Nom et classe (facultatif)",
}


@dataclass(frozen=True)
class ClientRect:
    """Géométrie du client en pixels physiques de l'écran virtuel Windows."""

    left: int
    top: int
    width: int
    height: int

    @property
    def screen_origin(self) -> ScreenPoint:
        return ScreenPoint(self.left, self.top)

    @property
    def size(self) -> ClientSize:
        return ClientSize(self.width, self.height)

    def to_screen_rect(self) -> ScreenRect:
        return ScreenRect(self.screen_origin, self.size)

    def to_dict(self) -> dict[str, int]:
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ClientRect":
        return cls(int(raw["left"]), int(raw["top"]), int(raw["width"]), int(raw["height"]))


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    minimized: bool


@dataclass(frozen=True)
class CapturedFrame:
    hwnd: int
    client: ClientRect
    image: np.ndarray  # BGR, lecture seule par convention
    activation_succeeded: bool = True
    source: str = "window"  # window ou desktop
    # Sous-étapes de la capture (ms) : grab, contrôles, conversion. Diagnostic de latence uniquement.
    timings_ms: dict[str, float] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class CaptureAssessment:
    usable: bool
    content_verified: bool
    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectionResult:
    step: str
    success: bool
    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)
    frame: CapturedFrame | None = None


@dataclass(frozen=True)
class RelativeRect:
    """Rectangle normalisé relativement à la zone cliente complète."""

    x: float
    y: float
    width: float
    height: float

    def validate(self) -> None:
        if not (0 <= self.x < 1 and 0 <= self.y < 1 and self.width > 0 and self.height > 0):
            raise ValueError("Rectangle de calibration invalide")
        if self.x + self.width > 1.001 or self.y + self.height > 1.001:
            raise ValueError("Rectangle de calibration hors de la zone cliente")

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        box = self.to_client_box(ClientSize(width, height))
        x, y, box_width, box_height = box.rounded()
        x = max(0, min(width - 1, x))
        y = max(0, min(height - 1, y))
        right = max(x + 1, min(width, x + box_width))
        bottom = max(y + 1, min(height, y + box_height))
        return x, y, right - x, bottom - y

    @classmethod
    def from_pixels(cls, x: int, y: int, width: int, height: int, frame_width: int, frame_height: int) -> "RelativeRect":
        return cls.from_client_box(ClientBox(x, y, width, height), ClientSize(frame_width, frame_height))

    def to_normalized_rect(self) -> NormalizedRect:
        self.validate()
        return NormalizedRect(self.x, self.y, self.width, self.height)

    def to_client_box(self, size: ClientSize) -> ClientBox:
        transform = LayoutTransform(ScreenPoint(0, 0), size, self.to_normalized_rect())
        return transform.normalized_rect_to_client(self.to_normalized_rect())

    def to_dict(self) -> dict[str, float]:
        return self.to_normalized_rect().to_dict()

    @classmethod
    def from_normalized_rect(cls, rect: NormalizedRect) -> "RelativeRect":
        return cls(rect.x, rect.y, rect.width, rect.height)

    @classmethod
    def from_client_box(cls, box: ClientBox, size: ClientSize) -> "RelativeRect":
        transform = LayoutTransform(ScreenPoint(0, 0), size, NormalizedRect(0, 0, 1, 1))
        return cls.from_normalized_rect(transform.client_box_to_normalized(box))

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "RelativeRect":
        return cls.from_normalized_rect(NormalizedRect.from_dict(raw))


@dataclass(frozen=True)
class Calibration:
    profile_id: int
    client_width: int
    client_height: int
    zones: dict[str, RelativeRect]
    zone_meta: dict[str, "ZoneEvidence"] = field(default_factory=dict)
    layout_signature: str = ""

    def validate(self) -> None:
        if self.client_width <= 0 or self.client_height <= 0:
            raise ValueError("Dimensions de calibration invalides")
        if not self.zones or set(self.zones) - set(ZONE_LABELS):
            raise ValueError("Sélectionnez au moins une zone de calibration")
        for rect in self.zones.values():
            rect.validate()

    def layout_compatibility(self, width: int, height: int,
                             zones: Mapping[str, RelativeRect] | None = None) -> LayoutCompatibility:
        """Retourne un diagnostic structuré, y compris pour une signature historique."""
        if width <= 0 or height <= 0:
            return LayoutCompatibility(False, LayoutCompatibilityReason.UNKNOWN,
                                       {"invalid_client_size": [width, height]}, True)
        width_scale = width / self.client_width
        height_scale = height / self.client_height
        aspect_drift = abs(width_scale / height_scale - 1)
        legacy_details: dict[str, object] = {
            "width_scale": width_scale, "height_scale": height_scale, "aspect_drift": aspect_drift,
        }
        try:
            saved = LayoutSignature.from_json(self.layout_signature)
        except ValueError:
            if aspect_drift > 0.04:
                return LayoutCompatibility(False, LayoutCompatibilityReason.ASPECT_RATIO_CHANGED,
                                           legacy_details, True)
            size_ok = 0.65 <= width_scale <= 1.5 and 0.65 <= height_scale <= 1.5
            return LayoutCompatibility(size_ok, LayoutCompatibilityReason.LEGACY_SIGNATURE,
                                       legacy_details, True)
        current_zones = zones or self.zones
        current = LayoutSignature.create(
            ClientSize(width, height),
            {name: rect.to_normalized_rect() for name, rect in current_zones.items()},
        )
        return compatibility(current, saved)

    def compatible(self, width: int, height: int) -> bool:
        return self.layout_compatibility(width, height).compatible

    def layout_transform(self, frame: CapturedFrame) -> LayoutTransform:
        if "combat" not in self.zones:
            raise ValueError("Zone de combat non calibrée")
        return LayoutTransform(frame.client.screen_origin, frame.client.size,
                               self.zones["combat"].to_normalized_rect())

    def crop(self, frame: CapturedFrame, zone: str) -> np.ndarray:
        if not self.compatible(frame.client.width, frame.client.height):
            raise ValueError("Calibration incompatible avec la taille actuelle : recalibrez")
        if zone not in self.zones:
            raise ValueError(f"Zone non calibrée : {zone}")
        evidence = self.zone_meta.get(zone)
        if evidence is not None and evidence.status != "confirmée":
            raise ValueError(f"Zone {zone} à confirmer avant utilisation")
        x, y, width, height = self.zones[zone].pixels(frame.client.width, frame.client.height)
        return frame.image[y:y + height, x:x + width].copy()


@dataclass(frozen=True)
class ZoneEvidence:
    confidence: float
    method: str
    status: str  # détectée, proposée, inconnue, confirmée

    def to_dict(self) -> dict[str, object]:
        return {"confidence": self.confidence, "method": self.method, "status": self.status}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ZoneEvidence":
        return cls(float(raw["confidence"]), str(raw["method"]), str(raw["status"]))


@dataclass(frozen=True)
class Profile:
    id: int | None
    label: str
    window_hwnd: int | None = None
    name: str | None = None
    character_class: str | None = None
    hp_current: int | None = None
    hp_max: int | None = None
    ap: int | None = None
    mp: int | None = None
    notes: str = ""


@dataclass(frozen=True)
class IconCandidate:
    page: int
    slot: int
    icon_png: bytes
    visual_hash: str
    presence_confidence: float
    recognition_confidence: float
    status: str  # Reconnu, À vérifier, Inconnu, Confirmé
    known_spell_id: int | None = None
    known_name: str | None = None
    duplicate_of: int | None = None


@dataclass(frozen=True)
class ScanResult:
    page: int
    slots_total: int
    empty_slots: tuple[int, ...]
    candidates: tuple[IconCandidate, ...]


@dataclass(frozen=True)
class RecognizedText:
    raw_text: str
    confidence: float
    fields: dict[str, str | int | bool | None] = field(default_factory=dict)
    uncertain_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecognizedProfile:
    name: str | None
    character_class: str | None
    hp_current: int | None
    hp_max: int | None
    ap: int | None
    mp: int | None
    confidence: float
    raw_text: str
    uncertain_fields: tuple[str, ...]
