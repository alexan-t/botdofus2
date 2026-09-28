"""FAST-5A1 : traduction ``PlanStep`` → actions écran, **sans jamais les exécuter**.

Prépare le futur ``MouseActionExecutor`` : chaque étape du plan devient une ou plusieurs
``ScreenAction`` (un clic gauche simple sur un point client/écran). Rien n'est envoyé ici.

Aucune coordonnée n'est écrite en dur. Toutes les coordonnées viennent des référentiels existants :

- cellule : ``DofusCellId`` → ``GridCoordinate`` (topologie GameData) → ``GridScreenTransform``
  (profil de projection confirmé, déjà mis à l'échelle par ``CombatGridProfileV2.status_for``) →
  ``CombatPoint`` → ``LayoutTransform`` → ``ClientPoint`` → ``ScreenPoint`` ;
- sort : zone calibrée ``spell_bar`` (normalisée) découpée en ``columns × rows`` exactement comme
  le scan de la barre (``vision.icons.scan_spell_bar``) : case ``slot`` = ``row * columns + column + 1`` ;
- fin de tour : centre de la zone calibrée ``end_turn``.

Tout élément inconnu donne ``REFUSED`` avec sa raison : géométrie absente ou incompatible, zone non
confirmée, case hors zone, sort sans case, page de sorts visible inconnue ou différente.
Module pur : ni Qt, ni OpenCV, ni numpy, ni Win32.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from combatbot.combat.planner import PlanStep, StepKind
from combatbot.combat.spells import CombatSpell
from combatbot.gamedata.topology import CELL_COUNT
from combatbot.vision.coordinates import (
    ClientBox, ClientPoint, ClientSize, LayoutTransform, NormalizedRect, ScreenPoint,
)
from combatbot.vision.grid_projection import GridOrientation, GridScreenTransform, cell_to_client

CONFIRMED = "confirmée"          # même statut que ``Calibration.crop`` / ``ZoneEvidence``
MIN_TARGET_SIZE_PX = 4.0         # une zone plus petite n'offre pas de point central sûr


class ScreenActionKind(str, Enum):
    # Un seul type utile aujourd'hui : amener le pointeur puis UN clic gauche simple.
    CLICK = "CLICK"


class TranslationStatus(str, Enum):
    READY = "READY"
    REFUSED = "REFUSED"


@dataclass(frozen=True)
class ScreenAction:
    kind: ScreenActionKind
    client_x: float
    client_y: float
    screen_x: float
    screen_y: float
    semantic_target: str                # « cell:312 », « spell_slot:1/3 », « end_turn »
    source: str                         # référentiel d'où vient le point
    confidence: float | None = None     # confiance du référentiel (None = non mesurée)
    prerequisites: tuple[str, ...] = ()
    button: str = "left"
    clicks: int = 1                     # jamais de double clic

    @property
    def screen_point(self) -> tuple[int, int]:
        return round(self.screen_x), round(self.screen_y)

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind.value, "client": [self.client_x, self.client_y],
                "screen": [self.screen_x, self.screen_y], "semantic_target": self.semantic_target,
                "source": self.source, "confidence": self.confidence,
                "prerequisites": list(self.prerequisites), "button": self.button, "clicks": self.clicks}


@dataclass(frozen=True)
class SpellBarLayout:
    """Découpage de la barre calibrée ; ``visible_page`` None = page affichée inconnue."""
    columns: int
    rows: int
    visible_page: int | None

    def __post_init__(self) -> None:
        if not (1 <= self.columns <= 30 and 1 <= self.rows <= 5):   # mêmes bornes que scan_spell_bar
            raise ValueError("Colonnes ou rangées de barre de sorts invalides")


@dataclass(frozen=True)
class ScreenGeometry:
    """Instantané géométrique de l'écran au moment de traduire ; comparé avant tout envoi."""
    hwnd: int
    layout: LayoutTransform
    layout_digest: str
    zones: Mapping[str, NormalizedRect] = field(default_factory=dict)   # zones CONFIRMÉES seulement
    zone_confidence: Mapping[str, float] = field(default_factory=dict)
    grid_transform: GridScreenTransform | None = None
    grid_confidence: float | None = None
    spell_bar: SpellBarLayout | None = None
    map_id: int | None = None

    @property
    def client_size(self) -> ClientSize:
        return self.layout.client_size

    @classmethod
    def from_calibration(cls, calibration, *, hwnd: int, client_origin: ScreenPoint, client_size: ClientSize,
                         grid_profile=None, grid_confidence: float | None = None,
                         spell_bar: SpellBarLayout | None = None, map_id: int | None = None
                         ) -> tuple["ScreenGeometry | None", tuple[str, ...]]:
        """Adaptateur depuis ``vision.models.Calibration`` et ``CombatGridProfileV2`` (typage canard)."""
        compatibility = calibration.layout_compatibility(client_size.width, client_size.height)
        if not compatibility.compatible or compatibility.requires_revalidation:
            return None, (f"calibration incompatible avec le client actuel ({compatibility.reason.value})",)
        if "combat" not in calibration.zones:
            return None, ("zone de combat non calibrée",)
        meta = getattr(calibration, "zone_meta", {}) or {}
        zones, confidence = {}, {}
        for name, rect in calibration.zones.items():
            evidence = meta.get(name)
            if evidence is not None and evidence.status == CONFIRMED:
                zones[name] = rect.to_normalized_rect()
                confidence[name] = float(evidence.confidence)
        layout = LayoutTransform(client_origin, client_size, calibration.zones["combat"].to_normalized_rect())
        transform = None
        if grid_profile is not None:
            status = grid_profile.status_for(client_size, {name: rect.to_normalized_rect()
                                                           for name, rect in calibration.zones.items()})
            transform = status.transform if status.applicable else None
        digest = str(compatibility.details.get("current_digest") or "")
        if not digest:
            return None, ("signature de layout absente",)
        return cls(hwnd, layout, digest, zones, confidence, transform, grid_confidence, spell_bar,
                   map_id), ()


@dataclass(frozen=True)
class StepTranslation:
    status: TranslationStatus
    step: PlanStep
    actions: tuple[ScreenAction, ...] = ()
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status.value, "step": self.step.describe(),
                "actions": [action.to_dict() for action in self.actions], "reasons": list(self.reasons)}


def _refused(step: PlanStep, *reasons: str) -> StepTranslation:
    return StepTranslation(TranslationStatus.REFUSED, step, (), tuple(reasons))


def _inside(box: ClientBox, point: ClientPoint) -> bool:
    return box.x <= point.x < box.x + box.width and box.y <= point.y < box.y + box.height


def _action(geometry: ScreenGeometry, point: ClientPoint, target: str, source: str, confidence: float | None,
            prerequisites: tuple[str, ...]) -> ScreenAction:
    screen = geometry.layout.client_to_screen(point)
    return ScreenAction(ScreenActionKind.CLICK, point.x, point.y, screen.x, screen.y, target, source,
                        confidence, prerequisites)


def _client_box(geometry: ScreenGeometry) -> ClientBox:
    size = geometry.client_size
    return ClientBox(0, 0, size.width, size.height)


def cell_action(geometry: ScreenGeometry, cell_id: int | None, prerequisites: tuple[str, ...] = ()
                ) -> tuple[ScreenAction | None, str | None]:
    """Centre client/écran d'une cellule, ou la raison du refus."""
    if cell_id is None:
        return None, "cellule cible inconnue"
    if not 0 <= int(cell_id) < CELL_COUNT:
        return None, f"cellule {cell_id} hors topologie"
    transform = geometry.grid_transform
    if transform is None:
        return None, "projection GameData absente ou non applicable"
    if transform.orientation is not GridOrientation.NORMAL:
        return None, "orientation de projection non démontrée"
    point = cell_to_client(int(cell_id), transform, geometry.layout)
    if not _inside(geometry.layout.combat_client_box, point) or not _inside(_client_box(geometry), point):
        return None, f"centre de la cellule {cell_id} hors de la zone de combat"
    return _action(geometry, point, f"cell:{int(cell_id)}", "gamedata_projection", geometry.grid_confidence,
                   prerequisites), None


def _zone_box(geometry: ScreenGeometry, zone: str) -> tuple[ClientBox | None, str | None]:
    rect = geometry.zones.get(zone)
    if rect is None:
        return None, f"zone {zone} absente ou non confirmée"
    box = geometry.layout.normalized_rect_to_client(rect)
    if box.width < MIN_TARGET_SIZE_PX or box.height < MIN_TARGET_SIZE_PX:
        return None, f"zone {zone} trop petite pour un point sûr"
    return box, None


def spell_slot_action(geometry: ScreenGeometry, spell: CombatSpell, prerequisites: tuple[str, ...] = ()
                      ) -> tuple[ScreenAction | None, str | None]:
    if spell.slot is None:
        return None, f"sort {spell.key} sans case dans la barre"
    bar = geometry.spell_bar
    if bar is None:
        return None, "découpage de la barre de sorts inconnu"
    if bar.visible_page is None:
        return None, "page de sorts affichée inconnue"
    if spell.slot.page != bar.visible_page:
        return None, f"sort {spell.key} sur la page {spell.slot.page}, page affichée {bar.visible_page}"
    index = spell.slot.slot - 1
    column, row = index % bar.columns, index // bar.columns
    if index < 0 or row >= bar.rows:
        return None, f"case {spell.slot.slot} hors de la barre ({bar.columns}×{bar.rows})"
    box, reason = _zone_box(geometry, "spell_bar")
    if box is None:
        return None, reason
    point = ClientPoint(box.x + (column + 0.5) * box.width / bar.columns,
                        box.y + (row + 0.5) * box.height / bar.rows)
    return _action(geometry, point, f"spell_slot:{spell.slot.page}/{spell.slot.slot}", "calibration.spell_bar",
                   geometry.zone_confidence.get("spell_bar"), prerequisites), None


def end_turn_action(geometry: ScreenGeometry, prerequisites: tuple[str, ...] = ()
                    ) -> tuple[ScreenAction | None, str | None]:
    box, reason = _zone_box(geometry, "end_turn")
    if box is None:
        return None, reason
    point = ClientPoint(box.x + box.width / 2, box.y + box.height / 2)
    return _action(geometry, point, "end_turn", "calibration.end_turn",
                   geometry.zone_confidence.get("end_turn"), prerequisites), None


def translate_step(step: PlanStep, geometry: ScreenGeometry | None, spells: Mapping[str, CombatSpell]
                   ) -> StepTranslation:
    """Une étape → actions écran ordonnées ; la moindre inconnue refuse l'étape entière."""
    if geometry is None:
        return _refused(step, "géométrie écran inconnue")
    if step.kind is StepKind.MOVE:
        action, reason = cell_action(geometry, step.expected.player_cell_id,
                                     ("tour du joueur", f"cellule {step.expected.player_cell_id} atteignable"))
        return _refused(step, reason) if action is None else \
            StepTranslation(TranslationStatus.READY, step, (action,))
    if step.kind is StepKind.CAST:
        spell = spells.get(step.spell_key or "")
        if spell is None:
            return _refused(step, f"sort {step.spell_key} inconnu du traducteur")
        slot, slot_reason = spell_slot_action(geometry, spell, ("tour du joueur", f"PA ≥ {step.ap_cost}"))
        target, target_reason = cell_action(geometry, step.target_cell_id, ("sort sélectionné",))
        reasons = tuple(reason for reason in (slot_reason, target_reason) if reason)
        if reasons:
            return _refused(step, *reasons)
        return StepTranslation(TranslationStatus.READY, step, (slot, target))
    if step.kind is StepKind.END_TURN:
        action, reason = end_turn_action(geometry, ("tour du joueur",))
        return _refused(step, reason) if action is None else \
            StepTranslation(TranslationStatus.READY, step, (action,))
    return _refused(step, f"type d'étape non géré : {step.kind}")
