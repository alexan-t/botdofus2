"""Profil de projection de grille GameData, versionné et lié au layout.

Décision (mesurée) : les 12 153 maps lisibles du client ont toutes
``zoom_scale = 100`` et ``zoom_offset = (0, 0)``. La géométrie des 560 cellules
est donc commune à toutes les maps d'un même layout : le profil est
*layout-scoped*. Le map ID ne sélectionne que la topologie (walkability, LOS,
indices) ; il est mémorisé uniquement comme dernière valeur **déclarée**.

On persiste la transformation, jamais les 560 positions pixel recalculables.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Mapping, Protocol

from combatbot.vision.coordinates import (
    ClientSize, LayoutCompatibilityReason, LayoutSignature, compatibility,
)
from combatbot.vision.grid_projection import GridOrientation, GridScreenTransform

PROFILE_SCHEMA_VERSION = 2
PROFILE_SETTING_KEY = "combat_grid_v2"
GAMEDATA_TOPOLOGY_VERSION = "dlm-v11/560-cells/topology-v1"


class MapIdOrigin(str, Enum):
    DECLARED_MANUALLY = "DECLARED_MANUALLY"
    # Réservé à un futur MapIdentityProvider ; jamais produit dans ce lot.
    DETECTED = "DETECTED"


class MapIdSource(str, Enum):
    """Provenance of a manually declared map ID (LOT 3B-2R)."""
    USER_VERIFIED_MAPID = "user_verified_mapid"  # typed by the user from /mapid in the client
    MANUAL_GUESS = "manual_guess"                # anything else (memory, coordinates, candidates)


@dataclass(frozen=True)
class DeclaredMapId:
    map_id: int
    origin: MapIdOrigin = MapIdOrigin.DECLARED_MANUALLY
    source: MapIdSource = MapIdSource.MANUAL_GUESS

    def __post_init__(self) -> None:
        if isinstance(self.map_id, bool) or not isinstance(self.map_id, int) or self.map_id < 0:
            raise ValueError("Map ID : entier positif ou nul attendu")

    @property
    def label(self) -> str:
        if self.origin is MapIdOrigin.DECLARED_MANUALLY:
            verified = " (vérifié par /mapid)" if self.source is MapIdSource.USER_VERIFIED_MAPID else " (non vérifié)"
            return f"Map ID déclaré manuellement : {self.map_id}{verified}"
        return f"Map ID détecté : {self.map_id}"


class MapIdentityProvider(Protocol):
    """Source de la map active. Remplaçable sans toucher à GridProjector."""

    def current_map(self) -> DeclaredMapId | None: ...


class ManualMapIdentity:
    """Seule implémentation de ce lot : l'utilisateur déclare le map ID."""

    def __init__(self, map_id: int | None = None) -> None:
        self._value = DeclaredMapId(map_id) if map_id is not None else None

    def declare(self, map_id: int | None,
                source: MapIdSource = MapIdSource.MANUAL_GUESS) -> DeclaredMapId | None:
        self._value = DeclaredMapId(map_id, source=MapIdSource(source)) if map_id is not None else None
        return self._value

    def current_map(self) -> DeclaredMapId | None:
        return self._value


class ProjectionStatusCode(str, Enum):
    READY = "READY"
    SCALED = "SCALED"
    GRID_CALIBRATION_INCOMPATIBLE = "GRID_CALIBRATION_INCOMPATIBLE"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    ORIENTATION_REJECTED = "ORIENTATION_REJECTED"


@dataclass(frozen=True)
class ProjectionStatus:
    code: ProjectionStatusCode
    transform: GridScreenTransform | None
    requires_recalibration: bool
    details: dict[str, object] = field(default_factory=dict)

    @property
    def applicable(self) -> bool:
        return self.transform is not None and not self.requires_recalibration


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CombatGridProfileV2:
    transform: GridScreenTransform
    client_layout_signature: str
    calibration_method: str = "manual"
    calibration_anchors: tuple[tuple[int, tuple[float, float]], ...] = ()
    calibration_metrics: dict = field(default_factory=dict)
    map_id: int | None = None
    map_id_origin: MapIdOrigin = MapIdOrigin.DECLARED_MANUALLY
    topology_source: str = "gamedata"
    gamedata_version: str = GAMEDATA_TOPOLOGY_VERSION
    scope: str = "layout"
    confirmed_by_user: bool = False
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    schema_version: int = PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROFILE_SCHEMA_VERSION:
            raise ValueError(f"CombatGridProfile : version {self.schema_version} non prise en charge")
        if self.topology_source != "gamedata":
            raise ValueError("CombatGridProfileV2 exige topology_source = gamedata")
        if self.scope != "layout":
            raise ValueError("Portée de profil inconnue")
        if self.map_id_origin is not MapIdOrigin.DECLARED_MANUALLY:
            raise ValueError("Ce lot ne connaît que des map IDs déclarés manuellement")

    def with_map(self, map_id: int | None) -> "CombatGridProfileV2":
        return replace(self, map_id=map_id, updated_at=_now())

    def status_for(self, client_size: ClientSize, zones: Mapping) -> ProjectionStatus:
        """Applies the transform only if the current layout is compatible with the saved one."""
        if not self.confirmed_by_user:
            return ProjectionStatus(ProjectionStatusCode.NOT_CONFIRMED, None, True,
                                    {"message": "Calibration de projection non confirmée"})
        if self.transform.orientation is not GridOrientation.NORMAL:
            return ProjectionStatus(ProjectionStatusCode.ORIENTATION_REJECTED, None, True,
                                    {"orientation": self.transform.orientation.value})
        try:
            saved = LayoutSignature.from_json(self.client_layout_signature)
            current = LayoutSignature.create(client_size, dict(zones))
        except (ValueError, TypeError) as exc:
            return ProjectionStatus(ProjectionStatusCode.GRID_CALIBRATION_INCOMPATIBLE, None, True,
                                    {"reason": "LEGACY_SIGNATURE", "error": str(exc)})
        result = compatibility(current, saved)
        details = {"layout": result.to_dict()}
        if not result.compatible:
            return ProjectionStatus(ProjectionStatusCode.GRID_CALIBRATION_INCOMPATIBLE, None, True, details)
        if result.reason is LayoutCompatibilityReason.CLIENT_SIZE_SCALED:
            # Same normalised combat zone, uniformly scaled client: the scene scales with it.
            scaled = self.transform.scaled(float(result.details["width_scale"]),
                                           float(result.details["height_scale"]))
            return ProjectionStatus(ProjectionStatusCode.SCALED, scaled, False, details)
        return ProjectionStatus(ProjectionStatusCode.READY, self.transform, False, details)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "topology_source": self.topology_source,
            "scope": self.scope, "gamedata_version": self.gamedata_version,
            "client_layout_signature": self.client_layout_signature,
            "transform": self.transform.to_dict(), "calibration_method": self.calibration_method,
            "calibration_anchors": [[cell, list(point)] for cell, point in self.calibration_anchors],
            "calibration_metrics": dict(self.calibration_metrics),
            "map_id": self.map_id, "map_id_origin": self.map_id_origin.value,
            "confirmed_by_user": self.confirmed_by_user,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "CombatGridProfileV2":
        transform = raw.get("transform")
        if not isinstance(transform, Mapping):
            raise ValueError("Profil de grille sans transformation")
        anchors = tuple((int(cell), (float(point[0]), float(point[1])))
                        for cell, point in raw.get("calibration_anchors", ()))  # type: ignore[union-attr]
        map_id = raw.get("map_id")
        metrics = raw.get("calibration_metrics", {})
        return cls(
            GridScreenTransform.from_dict(transform), str(raw.get("client_layout_signature", "")),
            str(raw.get("calibration_method", "manual")), anchors,
            dict(metrics) if isinstance(metrics, Mapping) else {},
            int(map_id) if isinstance(map_id, int) and not isinstance(map_id, bool) else None,
            MapIdOrigin(str(raw.get("map_id_origin", MapIdOrigin.DECLARED_MANUALLY.value))),
            str(raw.get("topology_source", "")), str(raw.get("gamedata_version", GAMEDATA_TOPOLOGY_VERSION)),
            str(raw.get("scope", "")), bool(raw.get("confirmed_by_user", False)),
            str(raw.get("created_at", "")), str(raw.get("updated_at", "")),
            int(raw.get("schema_version", -1)),
        )
