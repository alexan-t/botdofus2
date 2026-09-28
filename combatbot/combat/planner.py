"""FAST-4D : premier planificateur pour l'état réel — dry-run, aucune action envoyée.

Produit un plan déterministe ``MOVE`` / ``CAST`` / ``END_TURN``. Chaque étape porte sa raison, ses
préconditions, son coût attendu et l'état attendu après exécution.

Refus (``BLOCKED``) :

- donnée indispensable inconnue dans l'état (voir ``RealCombatState.blocking_unknowns``) ;
- aucune action prouvée possible alors qu'au moins une reste UNKNOWN (portée, LOS, bonus de portée,
  chemin passant par une cellule d'occupation inconnue) : on ne peut ni agir ni prouver qu'il faut finir
  le tour.

``END_TURN`` seul : quand toute action est prouvée impossible (PA, limites, portée démontrée…).

Règles non démontrées, jamais simulées : PV des ennemis (inconnus : un ennemi n'est jamais supposé
mort), tacle (aucun déplacement ne part d'une case voisine d'un ennemi ni n'en traverse une), sorts
ciblant un allié / soi / une case vide et moments « PV bas » (non gérés : ignorés et signalés).

Le ``CombatEngine`` du simulateur n'est pas remplacé : il reste le moteur de la grille synthétique.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from combatbot.combat.pathfinding import CombatMap, reachable, shortest_path
from combatbot.combat.spells import CombatSpell, SpellTarget, SpellTiming
from combatbot.combat.state import RealCombatState
from combatbot.combat.targeting import (
    CONSERVATIVE_RULES, Targetability, TargetingResult, TargetingRules, evaluate_target,
)

MAX_STEPS = 32


class StepKind(str, Enum):
    MOVE = "MOVE"
    CAST = "CAST"
    END_TURN = "END_TURN"


class PlanStatus(str, Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class ExpectedState:
    player_cell_id: int
    ap: int
    mp: int


@dataclass(frozen=True)
class PlanStep:
    kind: StepKind
    reason: str
    preconditions: tuple[str, ...]
    ap_cost: int
    mp_cost: int
    expected: ExpectedState
    spell_key: str | None = None
    target_id: str | None = None
    target_cell_id: int | None = None
    path: tuple[int, ...] = ()
    origin_cell_id: int | None = None

    def describe(self) -> str:
        if self.kind is StepKind.MOVE:
            return f"MOVE {self.origin_cell_id} -> {self.expected.player_cell_id}"
        if self.kind is StepKind.CAST:
            return f"CAST {self.spell_key} ON {self.target_id}"
        return "END_TURN"

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind.value, "describe": self.describe(), "reason": self.reason,
                "preconditions": list(self.preconditions), "ap_cost": self.ap_cost, "mp_cost": self.mp_cost,
                "expected": {"player_cell_id": self.expected.player_cell_id, "ap": self.expected.ap,
                             "mp": self.expected.mp},
                "spell_key": self.spell_key, "target_id": self.target_id, "target_cell_id": self.target_cell_id,
                "path": list(self.path), "origin_cell_id": self.origin_cell_id}


@dataclass(frozen=True)
class Plan:
    status: PlanStatus
    steps: tuple[PlanStep, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def describe(self) -> list[str]:
        return [step.describe() for step in self.steps] if self.status is PlanStatus.READY \
            else [f"BLOCKED: {reason}" for reason in self.blocked_reasons]

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status.value, "steps": [step.to_dict() for step in self.steps],
                "blocked_reasons": list(self.blocked_reasons), "assumptions": list(self.assumptions),
                "notes": list(self.notes), "actions_sent": "NONE"}


@dataclass(frozen=True)
class PlannerPolicy:
    # Tacle non démontré : prudence par défaut, jamais de déplacement depuis/à travers le contact ennemi.
    avoid_tackle: bool = True


@dataclass
class _Cursor:
    cell: int
    ap: int
    mp: int
    casts_this_turn: dict[str, int]
    casts_on_target: dict[tuple[str, str], int]


def _candidate_spells(spells: Iterable[CombatSpell], state: RealCombatState) -> tuple[list[CombatSpell], list[str]]:
    usable, notes = [], []
    for spell in sorted(spells, key=lambda item: (item.strategy.priority, item.key)):
        if not spell.strategy.use:
            continue
        if not spell.usable_for_real_decision:
            notes.append(f"{spell.key} ignoré : sort non confirmé ({', '.join(spell.unknown_fields) or spell.provenance.value})")
            continue
        if spell.strategy.target is not SpellTarget.ENEMY:
            notes.append(f"{spell.key} ignoré : cible {spell.strategy.target.value} non gérée par ce planificateur")
            continue
        if spell.strategy.timing is SpellTiming.LOW_HP:
            notes.append(f"{spell.key} ignoré : moment « PV bas » mais PV non observés")
            continue
        if spell.strategy.timing is SpellTiming.FIRST_TURN and state.turn_number != 1:
            notes.append(f"{spell.key} ignoré : réservé au premier tour (tour {state.turn_number or 'inconnu'})")
            continue
        usable.append(spell)
    return usable, notes


def _neighbors_of(combat_map: CombatMap, cells: Iterable[int]) -> frozenset[int]:
    return frozenset(neighbor for cell in cells for neighbor in combat_map.neighbors.get(cell, ()))


def _best_cast(combat_map: CombatMap, state: RealCombatState, spells: list[CombatSpell], cursor: _Cursor,
               rules: TargetingRules, occupied: frozenset[int]
               ) -> tuple[tuple[CombatSpell, object, TargetingResult] | None, list[TargetingResult]]:
    unknowns = []
    for spell in spells:
        for enemy in sorted(state.enemies, key=lambda item: item.id):
            result = evaluate_target(
                combat_map, spell, cursor.cell, enemy.cell_id, available_ap=cursor.ap,
                casts_this_turn=cursor.casts_this_turn.get(spell.key, 0),
                casts_on_target=cursor.casts_on_target.get((spell.key, enemy.id), 0),
                occupied=occupied, range_bonus=state.range_bonus, rules=rules)
            if result.status is Targetability.TARGETABLE:
                return (spell, enemy, result), unknowns
            if result.status is Targetability.UNKNOWN:
                unknowns.append(result)
    return None, unknowns


def plan_turn(state: RealCombatState, combat_map: CombatMap | None, spells: Iterable[CombatSpell], *,
              rules: TargetingRules = CONSERVATIVE_RULES, policy: PlannerPolicy = PlannerPolicy()) -> Plan:
    blocking = list(state.blocking_unknowns())
    if combat_map is None:
        blocking.append("topologie de map indisponible")
    elif state.map_id is not None and combat_map.map_id != state.map_id:
        blocking.append(f"topologie de la map {combat_map.map_id} pour un état sur la map {state.map_id}")
    if blocking:
        return Plan(PlanStatus.BLOCKED, blocked_reasons=tuple(blocking))
    usable, notes = _candidate_spells(spells, state)
    enemy_cells = frozenset(enemy.cell_id for enemy in state.enemies)
    occupied = frozenset(state.occupied_cells | enemy_cells) - {state.player_cell_id}
    stop_cells = _neighbors_of(combat_map, enemy_cells) if policy.avoid_tackle else frozenset()
    cursor = _Cursor(state.player_cell_id, state.ap, state.mp, dict(state.casts_this_turn),
                     dict(state.casts_on_target))
    steps: list[PlanStep] = []
    assumptions: set[str] = set()
    if not state.enemies:
        notes.append("aucun ennemi observé")
    while len(steps) < MAX_STEPS and usable and state.enemies:
        choice, unknowns = _best_cast(combat_map, state, usable, cursor, rules, occupied)
        if choice is not None:
            spell, enemy, result = choice
            assumptions.update(result.assumptions)
            before = (cursor.cell, cursor.ap, cursor.mp)
            cursor.ap -= spell.ap_cost
            cursor.casts_this_turn[spell.key] = cursor.casts_this_turn.get(spell.key, 0) + 1
            key = (spell.key, enemy.id)
            cursor.casts_on_target[key] = cursor.casts_on_target.get(key, 0) + 1
            steps.append(PlanStep(
                StepKind.CAST, f"{spell.name or spell.key} sur {enemy.id} (priorité {spell.strategy.priority})",
                (f"joueur sur {before[0]}", f"PA ≥ {spell.ap_cost} (actuel {before[1]})",
                 f"{enemy.id} sur {enemy.cell_id}",
                 f"lancers ce tour {cursor.casts_this_turn[spell.key] - 1}/{spell.per_turn}",
                 f"lancers sur la cible {cursor.casts_on_target[key] - 1}/{spell.per_target}",
                 *(f"hypothèse : {item}" for item in result.assumptions)),
                spell.ap_cost, 0, ExpectedState(cursor.cell, cursor.ap, cursor.mp),
                spell_key=spell.key, target_id=enemy.id, target_cell_id=enemy.cell_id))
            continue
        if unknowns:
            reasons = sorted({reason.value for result in unknowns for reason in result.reasons})
            return Plan(PlanStatus.BLOCKED, tuple(steps),
                        (f"sort possible mais non prouvé depuis {cursor.cell} : {', '.join(reasons)}",),
                        tuple(sorted(assumptions)), tuple(notes))
        move = _best_move(combat_map, state, usable, cursor, rules, occupied, stop_cells)
        if isinstance(move, str):
            return Plan(PlanStatus.BLOCKED, tuple(steps), (move,), tuple(sorted(assumptions)), tuple(notes))
        if move is None:
            break
        path, cost, result = move
        assumptions.update(result.assumptions)
        origin = cursor.cell
        cursor.cell, cursor.mp = path[-1], cursor.mp - cost
        steps.append(PlanStep(
            StepKind.MOVE, f"se placer pour lancer un sort ({cost} PM)",
            (f"joueur sur {origin}", f"PM ≥ {cost} (actuel {cursor.mp + cost})",
             f"chemin libre : {' -> '.join(str(cell) for cell in (origin, *path))}"),
            0, cost, ExpectedState(cursor.cell, cursor.ap, cursor.mp), path=path, origin_cell_id=origin))
    steps.append(PlanStep(StepKind.END_TURN, "aucune autre action sûre" if steps else "aucune action sûre",
                          (f"joueur sur {cursor.cell}",), 0, 0, ExpectedState(cursor.cell, cursor.ap, cursor.mp)))
    return Plan(PlanStatus.READY, tuple(steps), (), tuple(sorted(assumptions)), tuple(notes))


def _best_move(combat_map: CombatMap, state: RealCombatState, spells: list[CombatSpell], cursor: _Cursor,
               rules: TargetingRules, occupied: frozenset[int], stop_cells: frozenset[int]):
    """Déplacement le moins coûteux qui rend un sort lançable ; str = raison de refus ; None = aucun."""
    if cursor.mp <= 0:
        return None
    if cursor.cell in stop_cells:
        return None     # au contact d'un ennemi : quitter la case exposerait au tacle, non modélisé
    costs = reachable(combat_map, cursor.cell, cursor.mp, occupied=occupied, unknown=state.unknown_cells,
                      stop_cells=stop_cells)
    unknown_here = False
    for cell, cost in sorted(costs.items(), key=lambda item: (item[1], item[0])):
        if cost == 0:
            continue
        probe = _Cursor(cell, cursor.ap, cursor.mp - cost, cursor.casts_this_turn, cursor.casts_on_target)
        choice, unknowns = _best_cast(combat_map, state, spells, probe, rules, occupied)
        if choice is not None:
            path = shortest_path(combat_map, cursor.cell, cell, occupied=occupied, unknown=state.unknown_cells,
                                 stop_cells=stop_cells, max_mp=cursor.mp)
            return path.path, path.cost, choice[2]
        unknown_here = unknown_here or bool(unknowns)
    if unknown_here:
        return "déplacement possible mais aucun placement n'est prouvé (portée ou ligne de vue non démontrée)"
    if state.unknown_cells:
        optimistic = reachable(combat_map, cursor.cell, cursor.mp, occupied=occupied, stop_cells=stop_cells)
        for cell, cost in sorted(optimistic.items(), key=lambda item: (item[1], item[0])):
            if cell in costs or cost == 0:
                continue
            probe = _Cursor(cell, cursor.ap, cursor.mp - cost, cursor.casts_this_turn, cursor.casts_on_target)
            choice, _unknowns = _best_cast(combat_map, state, spells, probe, rules, occupied)
            if choice is not None:
                return f"le seul placement utile ({cell}) exige de traverser une cellule d'occupation inconnue"
    return None
