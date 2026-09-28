"""FAST-5B : preuve de l'effet d'une étape, avec des invariants propres à chaque type d'étape.

``ExpectedState`` (planner) reste la prédiction complète après l'étape ; ce module décide ce qu'une
observation **prouve** de cette prédiction, sans exiger ce qui n'est pas lu ou pas pertinent :

- MOVE : la cellule du joueur doit devenir ``expected.player_cell_id`` ; les PM sont comparés
  seulement s'ils sont lisibles ; toujours sur la cellule de départ = en attente.
- CAST : les PA doivent baisser exactement du coût prévu (s'ils sont illisibles : UNKNOWN) ; le joueur
  ne doit pas avoir bougé. La mort ou les PV de la cible ne sont **pas** exigés : ils ne sont pas lus.
- END_TURN : le tour doit quitter PLAYER (ou le combat se terminer) ; PA/PM ne sont pas comparés.

Verdicts : CONFIRMED, PENDING (effet pas encore visible), CONTRADICTED, UNKNOWN (observation
insuffisante pour trancher). Module pur ; l'observation est lue par attributs (typage canard).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from combatbot.combat.planner import PlanStep, StepKind


class Effect(str, Enum):
    CONFIRMED = "CONFIRMED"
    PENDING = "PENDING"
    CONTRADICTED = "CONTRADICTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EffectCheck:
    effect: Effect
    detail: str
    mismatches: dict[str, dict[str, object]] = field(default_factory=dict)


def _mismatch(name: str, expected: object, observed: object) -> dict[str, dict[str, object]]:
    return {name: {"expected": expected, "observed": observed}}


def check_effect(step: PlanStep, observed) -> EffectCheck:
    """``observed`` : tout objet exposant player_cell_id / ap / mp (et turn / phase pour END_TURN)."""
    cell, ap, mp = (getattr(observed, name, None) for name in ("player_cell_id", "ap", "mp"))
    expected = step.expected
    if step.kind is StepKind.MOVE:
        if cell is None:
            return EffectCheck(Effect.UNKNOWN, "cellule du joueur illisible")
        if cell == expected.player_cell_id:
            if mp is not None and mp != expected.mp:
                return EffectCheck(Effect.CONTRADICTED, "PM incohérents après le déplacement",
                                   _mismatch("mp", expected.mp, mp))
            return EffectCheck(Effect.CONFIRMED, "joueur sur la cellule attendue")
        if cell == step.origin_cell_id:
            return EffectCheck(Effect.PENDING, "joueur encore sur la cellule de départ")
        return EffectCheck(Effect.CONTRADICTED, "joueur sur une cellule inattendue",
                           _mismatch("player_cell_id", expected.player_cell_id, cell))
    if step.kind is StepKind.CAST:
        if step.ap_cost <= 0:
            return EffectCheck(Effect.UNKNOWN, "sort sans coût PA : aucun effet observable")
        if cell is not None and cell != expected.player_cell_id:
            return EffectCheck(Effect.CONTRADICTED, "le joueur a bougé pendant un lancer",
                               _mismatch("player_cell_id", expected.player_cell_id, cell))
        if ap is None:
            return EffectCheck(Effect.UNKNOWN, "PA illisibles")
        if ap == expected.ap:
            return EffectCheck(Effect.CONFIRMED, f"PA passés à {ap}")
        if ap == expected.ap + step.ap_cost:
            return EffectCheck(Effect.PENDING, "PA inchangés")
        return EffectCheck(Effect.CONTRADICTED, "PA différents du coût prévu", _mismatch("ap", expected.ap, ap))
    if step.kind is StepKind.END_TURN:
        phase, turn = getattr(observed, "phase", None), getattr(observed, "turn", None)
        if phase is not None and phase != "FIGHTING":
            return EffectCheck(Effect.CONFIRMED, f"combat terminé ou changé de phase ({phase})")
        if turn is None:
            return EffectCheck(Effect.PENDING, "tour illisible")
        if turn == "PLAYER":
            return EffectCheck(Effect.PENDING, "toujours mon tour")
        return EffectCheck(Effect.CONFIRMED, f"tour passé à {turn}")
    return EffectCheck(Effect.UNKNOWN, f"type d'étape non géré : {step.kind}")
