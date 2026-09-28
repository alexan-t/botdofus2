"""FAST-4C : validation de cible conservatrice — TARGETABLE / NOT_TARGETABLE / UNKNOWN avec raisons.

Ce qui est démontré dans le dépôt, donc décidé ici :

- validité des DofusCellId (0..559) et coordonnées logiques (``topology.cell_to_grid``) ;
- coût en PA, limites par tour et par cible : règles portées par le sort **confirmé** lui-même ;
- « lancer en ligne » : même x ou même y logique. Dérivé du voisinage à 4 démontré (chaque pas d'arête
  change x ou y de 1) : les lignes droites de la grille sont exactement x constant ou y constant.

Ce qui n'est PAS démontré, donc jamais deviné (résultat UNKNOWN tant que la règle n'est pas fournie) :

- **métrique de portée** : aucune preuve dans le dépôt que la portée DOFUS 2.64.5 est la distance logique
  |dx| + |dy|. ``TargetingRules.range_metric`` vaut ``UNVERIFIED`` par défaut ; ``LOGICAL_MANHATTAN``
  peut être activé explicitement pour un dry-run et apparaît alors dans ``assumptions`` ;
- **bonus de portée** du personnage : non lu à l'écran ; un sort à portée modifiable avec bonus inconnu
  donne UNKNOWN (un malus peut aussi réduire la portée) ;
- **ligne de vue** : le drapeau statique existe (bit 3) mais l'algorithme de tracé DOFUS n'est pas prouvé.
  Un sort avec ligne de vue donne UNKNOWN, sauf ``los_oracle`` injecté ; un oracle non marqué vérifié est
  lui aussi listé dans ``assumptions``.

Point à valider plus tard (en jeu, lecture seule) : captures de zones de portée affichées par le client
pour quelques sorts et positions, afin de prouver la métrique, l'effet du bonus et le tracé de LOS.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable

from combatbot.combat.pathfinding import CombatMap
from combatbot.combat.spells import CombatSpell, SpellStatus
from combatbot.gamedata.topology import CELL_COUNT, cell_to_grid


class Targetability(str, Enum):
    TARGETABLE = "TARGETABLE"
    NOT_TARGETABLE = "NOT_TARGETABLE"
    UNKNOWN = "UNKNOWN"


class RangeMetric(str, Enum):
    UNVERIFIED = "UNVERIFIED"                   # défaut : aucune portée n'est calculée
    LOGICAL_MANHATTAN = "LOGICAL_MANHATTAN"     # hypothèse explicite, non prouvée


class Reason(str, Enum):
    INVALID_CELL = "INVALID_CELL"
    SPELL_NOT_CONFIRMED = "SPELL_NOT_CONFIRMED"
    AP_UNKNOWN = "AP_UNKNOWN"
    NOT_ENOUGH_AP = "NOT_ENOUGH_AP"
    PER_TURN_LIMIT_REACHED = "PER_TURN_LIMIT_REACHED"
    PER_TARGET_LIMIT_REACHED = "PER_TARGET_LIMIT_REACHED"
    NOT_IN_LINE = "NOT_IN_LINE"
    RANGE_RULE_UNVERIFIED = "RANGE_RULE_UNVERIFIED"
    RANGE_BONUS_UNKNOWN = "RANGE_BONUS_UNKNOWN"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    LOS_ALGORITHM_UNVERIFIED = "LOS_ALGORITHM_UNVERIFIED"
    LOS_UNKNOWN = "LOS_UNKNOWN"
    LOS_BLOCKED = "LOS_BLOCKED"


# Raisons qui prouvent l'impossibilité ; toutes les autres laissent la question ouverte.
BLOCKING = frozenset({Reason.INVALID_CELL, Reason.NOT_ENOUGH_AP, Reason.PER_TURN_LIMIT_REACHED,
                      Reason.PER_TARGET_LIMIT_REACHED, Reason.NOT_IN_LINE, Reason.OUT_OF_RANGE, Reason.LOS_BLOCKED})

# (carte, lanceur, cible, cellules occupées) → True (vue dégagée), False (bloquée) ou None (inconnu).
LosOracle = Callable[[CombatMap, int, int, frozenset[int]], "bool | None"]


@dataclass(frozen=True)
class TargetingRules:
    range_metric: RangeMetric = RangeMetric.UNVERIFIED
    los_oracle: LosOracle | None = None
    los_oracle_verified: bool = False

    @property
    def assumptions(self) -> tuple[str, ...]:
        found = []
        if self.range_metric is RangeMetric.LOGICAL_MANHATTAN:
            found.append("portée = distance logique |dx|+|dy| (non prouvée pour 2.64.5)")
        if self.los_oracle is not None and not self.los_oracle_verified:
            found.append("ligne de vue calculée par un oracle non vérifié")
        return tuple(found)


CONSERVATIVE_RULES = TargetingRules()


@dataclass(frozen=True)
class TargetingResult:
    status: Targetability
    reasons: tuple[Reason, ...] = ()
    distance: int | None = None
    assumptions: tuple[str, ...] = ()
    details: dict[str, object] = field(default_factory=dict)

    @property
    def targetable(self) -> bool:
        return self.status is Targetability.TARGETABLE

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status.value, "reasons": [reason.value for reason in self.reasons],
                "distance": self.distance, "assumptions": list(self.assumptions), "details": dict(self.details)}


def _valid(cell_id: object) -> bool:
    return type(cell_id) is int and 0 <= cell_id < CELL_COUNT


def logical_distance(origin: int, target: int) -> int:
    a, b = cell_to_grid(origin), cell_to_grid(target)
    return abs(a.x - b.x) + abs(a.y - b.y)


def in_line(origin: int, target: int) -> bool:
    a, b = cell_to_grid(origin), cell_to_grid(target)
    return a.x == b.x or a.y == b.y


def evaluate_target(combat_map: CombatMap, spell: CombatSpell, caster_cell: int, target_cell: int, *,
                    available_ap: int | None, casts_this_turn: int = 0, casts_on_target: int = 0,
                    occupied: Iterable[int] = (), range_bonus: int | None = None,
                    rules: TargetingRules = CONSERVATIVE_RULES) -> TargetingResult:
    """Peut-on lancer ``spell`` depuis ``caster_cell`` sur ``target_cell`` ? Ne devine aucune règle."""
    if not _valid(caster_cell) or not _valid(target_cell):
        return TargetingResult(Targetability.NOT_TARGETABLE, (Reason.INVALID_CELL,))
    if spell.status is not SpellStatus.CONFIRMED:
        return TargetingResult(Targetability.UNKNOWN, (Reason.SPELL_NOT_CONFIRMED,),
                               details={"unknown_fields": list(spell.unknown_fields),
                                        "provenance": spell.provenance.value})
    reasons: list[Reason] = []
    assumptions: list[str] = []
    details: dict[str, object] = {}
    if available_ap is None:
        reasons.append(Reason.AP_UNKNOWN)
    elif available_ap < spell.ap_cost:
        reasons.append(Reason.NOT_ENOUGH_AP)
    if casts_this_turn >= spell.per_turn:
        reasons.append(Reason.PER_TURN_LIMIT_REACHED)
    if casts_on_target >= spell.per_target:
        reasons.append(Reason.PER_TARGET_LIMIT_REACHED)
    if spell.line_cast and not in_line(caster_cell, target_cell):
        reasons.append(Reason.NOT_IN_LINE)
    distance: int | None = None
    if rules.range_metric is RangeMetric.UNVERIFIED:
        reasons.append(Reason.RANGE_RULE_UNVERIFIED)
    else:
        distance = logical_distance(caster_cell, target_cell)
        assumptions.append(rules.assumptions[0])
        if spell.modifiable_range and range_bonus is None:
            # Même la borne de base n'est pas sûre : un malus de portée peut la réduire.
            reasons.append(Reason.RANGE_BONUS_UNKNOWN)
        else:
            bonus = (range_bonus or 0) if spell.modifiable_range else 0   # portée fixe : bonus sans effet
            maximum = max(spell.min_range, spell.max_range + bonus)
            details["effective_max_range"] = maximum
            if not spell.min_range <= distance <= maximum:
                reasons.append(Reason.OUT_OF_RANGE)
    if spell.line_of_sight:
        if rules.los_oracle is None:
            reasons.append(Reason.LOS_ALGORITHM_UNVERIFIED)
        else:
            if not rules.los_oracle_verified:
                assumptions.append("ligne de vue calculée par un oracle non vérifié")
            clear = rules.los_oracle(combat_map, caster_cell, target_cell, frozenset(occupied))
            if clear is None:
                reasons.append(Reason.LOS_UNKNOWN)
            elif not clear:
                reasons.append(Reason.LOS_BLOCKED)
    if any(reason in BLOCKING for reason in reasons):
        status = Targetability.NOT_TARGETABLE
    elif reasons:
        status = Targetability.UNKNOWN
    else:
        status = Targetability.TARGETABLE
    return TargetingResult(status, tuple(reasons), distance, tuple(assumptions), details)
