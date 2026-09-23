"""Référentiels géométriques explicites de PythonBot.

Toutes les valeurs sont exprimées en pixels physiques de la capture, sauf les
types ``Normalized*``. Qt peut afficher l'image à une autre échelle : le point
de scène doit être reconverti vers les dimensions de l'image avant d'entrer ici.

Conventions :

* ``ScreenPoint(0, 0)`` est l'origine du bureau virtuel Windows ;
* ``ClientPoint(0, 0)`` est le coin supérieur gauche du contenu client, hors
  bordure et barre de titre ;
* ``NormalizedPoint(0, 0)`` et ``(1, 1)`` désignent respectivement le coin
  supérieur gauche et le bord inférieur droit théorique du client ;
* ``CombatPoint(0, 0)`` est le coin supérieur gauche de la zone combat calibrée ;
* ``Cell`` reste un identifiant logique et n'apparaît pas dans ce module.

Les transformations conservent des flottants. La conversion finale vers un
indice pixel utilise explicitement ``round()``, donc l'arrondi Python au pair le
plus proche en cas de demi-entier. Aucune correction DPI n'est appliquée : les
coordonnées provenant de ``capture_client`` sont déjà physiques.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping


ROUNDTRIP_TOLERANCE = 1e-9
LAYOUT_SIGNATURE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class _Point:
    x: float
    y: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("Les coordonnées doivent être finies")

    def __iter__(self):
        yield self.x
        yield self.y

    def __getitem__(self, index: int) -> float:
        return (self.x, self.y)[index]

    def rounded(self) -> tuple[int, int]:
        return round(self.x), round(self.y)

    def to_dict(self) -> dict[str, float]:
        return {"x": float(self.x), "y": float(self.y)}


@dataclass(frozen=True)
class ScreenPoint(_Point):
    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ScreenPoint":
        return cls(float(raw["x"]), float(raw["y"]))


@dataclass(frozen=True)
class ClientPoint(_Point):
    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ClientPoint":
        return cls(float(raw["x"]), float(raw["y"]))


@dataclass(frozen=True)
class CombatPoint(_Point):
    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "CombatPoint":
        return cls(float(raw["x"]), float(raw["y"]))


@dataclass(frozen=True)
class NormalizedPoint(_Point):
    def __post_init__(self) -> None:
        super().__post_init__()
        if not (0.0 <= self.x <= 1.0 and 0.0 <= self.y <= 1.0):
            raise ValueError("Un point normalisé doit rester dans [0, 1]")

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "NormalizedPoint":
        return cls(float(raw["x"]), float(raw["y"]))


@dataclass(frozen=True)
class ClientSize:
    width: int
    height: int

    def __post_init__(self) -> None:
        if isinstance(self.width, bool) or isinstance(self.height, bool) or self.width <= 0 or self.height <= 0:
            raise ValueError("La taille cliente doit être strictement positive")

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height

    def to_dict(self) -> dict[str, int]:
        return {"width": self.width, "height": self.height}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ClientSize":
        return cls(int(raw["width"]), int(raw["height"]))


@dataclass(frozen=True)
class ScreenRect:
    origin: ScreenPoint
    size: ClientSize

    def to_dict(self) -> dict[str, object]:
        return {"origin": self.origin.to_dict(), "size": self.size.to_dict()}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ScreenRect":
        origin, size = raw.get("origin"), raw.get("size")
        if not isinstance(origin, Mapping) or not isinstance(size, Mapping):
            raise ValueError("Rectangle écran invalide")
        return cls(ScreenPoint.from_dict(origin), ClientSize.from_dict(size))


@dataclass(frozen=True)
class ClientBox:
    """Rectangle en pixels locaux du client, bords droit/bas exclus pour un crop."""

    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.x, self.y, self.width, self.height)):
            raise ValueError("Rectangle client non fini")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Rectangle client vide")

    @property
    def origin(self) -> ClientPoint:
        return ClientPoint(self.x, self.y)

    def rounded(self) -> tuple[int, int, int, int]:
        left, top = round(self.x), round(self.y)
        right, bottom = round(self.x + self.width), round(self.y + self.height)
        return left, top, max(1, right - left), max(1, bottom - top)

    def to_dict(self) -> dict[str, float]:
        return {"x": float(self.x), "y": float(self.y),
                "width": float(self.width), "height": float(self.height)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ClientBox":
        return cls(float(raw["x"]), float(raw["y"]),
                   float(raw["width"]), float(raw["height"]))


@dataclass(frozen=True)
class NormalizedRect:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(value) for value in (self.x, self.y, self.width, self.height)):
            raise ValueError("Rectangle normalisé non fini")
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise ValueError("Rectangle normalisé invalide")
        # Tolérance historique de RelativeRect conservée pour charger les anciennes calibrations.
        if self.x + self.width > 1.001 or self.y + self.height > 1.001:
            raise ValueError("Rectangle normalisé hors du client")

    def to_dict(self) -> dict[str, float]:
        return {"x": float(self.x), "y": float(self.y),
                "width": float(self.width), "height": float(self.height)}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "NormalizedRect":
        return cls(float(raw["x"]), float(raw["y"]),
                   float(raw["width"]), float(raw["height"]))


@dataclass(frozen=True)
class LayoutTransform:
    """Autorité de conversion entre écran, client, normalisé et zone combat."""

    client_screen_origin: ScreenPoint
    client_size: ClientSize
    combat_zone: NormalizedRect

    @property
    def combat_client_box(self) -> ClientBox:
        return self.normalized_rect_to_client(self.combat_zone)

    def screen_to_client(self, point: ScreenPoint) -> ClientPoint:
        return ClientPoint(point.x - self.client_screen_origin.x,
                           point.y - self.client_screen_origin.y)

    def client_to_screen(self, point: ClientPoint) -> ScreenPoint:
        return ScreenPoint(point.x + self.client_screen_origin.x,
                           point.y + self.client_screen_origin.y)

    def client_to_normalized(self, point: ClientPoint) -> NormalizedPoint:
        return NormalizedPoint(point.x / self.client_size.width,
                               point.y / self.client_size.height)

    def normalized_to_client(self, point: NormalizedPoint) -> ClientPoint:
        return ClientPoint(point.x * self.client_size.width,
                           point.y * self.client_size.height)

    def client_to_combat(self, point: ClientPoint) -> CombatPoint:
        origin = self.combat_client_box.origin
        return CombatPoint(point.x - origin.x, point.y - origin.y)

    def combat_to_client(self, point: CombatPoint) -> ClientPoint:
        origin = self.combat_client_box.origin
        return ClientPoint(point.x + origin.x, point.y + origin.y)

    def normalized_to_combat(self, point: NormalizedPoint) -> CombatPoint:
        return self.client_to_combat(self.normalized_to_client(point))

    def combat_to_normalized(self, point: CombatPoint) -> NormalizedPoint:
        return self.client_to_normalized(self.combat_to_client(point))

    def normalized_rect_to_client(self, rect: NormalizedRect) -> ClientBox:
        return ClientBox(rect.x * self.client_size.width, rect.y * self.client_size.height,
                         rect.width * self.client_size.width, rect.height * self.client_size.height)

    def client_box_to_normalized(self, rect: ClientBox) -> NormalizedRect:
        return NormalizedRect(rect.x / self.client_size.width, rect.y / self.client_size.height,
                              rect.width / self.client_size.width, rect.height / self.client_size.height)

    def to_dict(self) -> dict[str, object]:
        return {
            "client_screen_origin": self.client_screen_origin.to_dict(),
            "client_size": self.client_size.to_dict(),
            "combat_zone": self.combat_zone.to_dict(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "LayoutTransform":
        origin, size, zone = raw.get("client_screen_origin"), raw.get("client_size"), raw.get("combat_zone")
        if not isinstance(origin, Mapping) or not isinstance(size, Mapping) or not isinstance(zone, Mapping):
            raise ValueError("LayoutTransform sérialisé invalide")
        return cls(ScreenPoint.from_dict(origin), ClientSize.from_dict(size), NormalizedRect.from_dict(zone))


@dataclass(frozen=True)
class LayoutSignature:
    """Identité d'une disposition, indépendante du HWND et de la position écran."""

    client_size: ClientSize
    zones: tuple[tuple[str, NormalizedRect], ...]
    visual_anchors: tuple[tuple[str, str], ...] = ()
    schema_version: int = LAYOUT_SIGNATURE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != LAYOUT_SIGNATURE_SCHEMA_VERSION:
            raise ValueError(f"Version de signature non prise en charge : {self.schema_version}")
        if tuple(sorted(self.zones, key=lambda item: item[0])) != self.zones:
            raise ValueError("Les zones de signature doivent être triées")
        if tuple(sorted(self.visual_anchors, key=lambda item: item[0])) != self.visual_anchors:
            raise ValueError("Les ancres visuelles doivent être triées")

    @classmethod
    def create(cls, client_size: ClientSize, zones: Mapping[str, NormalizedRect],
               visual_anchors: Mapping[str, str] | None = None) -> "LayoutSignature":
        return cls(client_size, tuple(sorted(zones.items())),
                   tuple(sorted((visual_anchors or {}).items())))

    @property
    def digest(self) -> str:
        canonical = json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "client_size": self.client_size.to_dict(),
            "aspect_ratio": self.client_size.aspect_ratio,
            "zones": {name: rect.to_dict() for name, rect in self.zones},
            "visual_anchors": dict(self.visual_anchors),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "LayoutSignature":
        if int(raw.get("schema_version", -1)) != LAYOUT_SIGNATURE_SCHEMA_VERSION:
            raise ValueError("Signature legacy ou version inconnue")
        size, zones = raw.get("client_size"), raw.get("zones")
        anchors = raw.get("visual_anchors", {})
        if not isinstance(size, Mapping) or not isinstance(zones, Mapping) or not isinstance(anchors, Mapping):
            raise ValueError("Signature de layout invalide")
        parsed_zones: dict[str, NormalizedRect] = {}
        for name, value in zones.items():
            if not isinstance(value, Mapping):
                raise ValueError(f"Zone de signature invalide : {name}")
            parsed_zones[str(name)] = NormalizedRect.from_dict(value)
        return cls.create(
            ClientSize.from_dict(size), parsed_zones,
            {str(name): str(value) for name, value in anchors.items()},
        )

    @classmethod
    def from_json(cls, value: str) -> "LayoutSignature":
        try:
            raw = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Signature legacy ou illisible") from exc
        if not isinstance(raw, Mapping):
            raise ValueError("Signature legacy ou illisible")
        return cls.from_dict(raw)


class LayoutCompatibilityReason(str, Enum):
    COMPATIBLE = "COMPATIBLE"
    CLIENT_SIZE_SCALED = "CLIENT_SIZE_SCALED"
    ASPECT_RATIO_CHANGED = "ASPECT_RATIO_CHANGED"
    COMBAT_ZONE_CHANGED = "COMBAT_ZONE_CHANGED"
    LAYOUT_SIGNATURE_MISMATCH = "LAYOUT_SIGNATURE_MISMATCH"
    LEGACY_SIGNATURE = "LEGACY_SIGNATURE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LayoutCompatibility:
    compatible: bool
    reason: LayoutCompatibilityReason
    details: dict[str, object]
    requires_revalidation: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "compatible": self.compatible,
            "reason": self.reason.value,
            "details": dict(self.details),
            "requires_revalidation": self.requires_revalidation,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "LayoutCompatibility":
        details = raw.get("details", {})
        if not isinstance(details, Mapping):
            raise ValueError("Détails de compatibilité invalides")
        return cls(
            bool(raw["compatible"]), LayoutCompatibilityReason(str(raw["reason"])), dict(details),
            bool(raw.get("requires_revalidation", False)),
        )


def compatibility(current: LayoutSignature, saved: LayoutSignature) -> LayoutCompatibility:
    """Compare deux dispositions sans tenir compte de leur position sur le bureau."""
    width_scale = current.client_size.width / saved.client_size.width
    height_scale = current.client_size.height / saved.client_size.height
    aspect_drift = abs(width_scale / height_scale - 1.0)
    details: dict[str, object] = {
        "width_scale": width_scale, "height_scale": height_scale,
        "aspect_drift": aspect_drift, "saved_digest": saved.digest, "current_digest": current.digest,
    }
    if aspect_drift > 0.04:
        return LayoutCompatibility(False, LayoutCompatibilityReason.ASPECT_RATIO_CHANGED, details, True)
    if not (0.65 <= width_scale <= 1.5 and 0.65 <= height_scale <= 1.5):
        return LayoutCompatibility(False, LayoutCompatibilityReason.UNKNOWN,
                                   {**details, "size_out_of_supported_range": True}, True)
    current_zones, saved_zones = dict(current.zones), dict(saved.zones)
    if current_zones.get("combat") != saved_zones.get("combat"):
        return LayoutCompatibility(False, LayoutCompatibilityReason.COMBAT_ZONE_CHANGED, details, True)
    if current_zones != saved_zones or current.visual_anchors != saved.visual_anchors:
        return LayoutCompatibility(False, LayoutCompatibilityReason.LAYOUT_SIGNATURE_MISMATCH, details, True)
    if current.client_size != saved.client_size:
        return LayoutCompatibility(True, LayoutCompatibilityReason.CLIENT_SIZE_SCALED, details)
    return LayoutCompatibility(True, LayoutCompatibilityReason.COMPATIBLE, details)
