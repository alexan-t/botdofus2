"""LOT 3B-6B : état sémantique du combat (phase + tour), OBSERVATION SEULE.

Aucun de ces modèles ne déclenche d'action : ils décrivent ce que l'écran montre. UNKNOWN est
toujours une réponse valide ; un détecteur qui hésite doit s'abstenir plutôt que deviner.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class CombatPhase(str, Enum):
    OUT_OF_COMBAT = "OUT_OF_COMBAT"   # exploration, aucun combat affiché
    PLACEMENT = "PLACEMENT"           # préparation : cases de placement, bouton « Prêt »
    FIGHTING = "FIGHTING"             # combat lancé (tour de quelqu'un)
    RESULTS = "RESULTS"               # fenêtre de fin de combat
    UNKNOWN = "UNKNOWN"


class TurnOwner(str, Enum):
    PLAYER = "PLAYER"                 # mon tour
    OTHER = "OTHER"                   # tour d'un autre combattant (ennemi, allié, invocation)
    UNKNOWN = "UNKNOWN"


PHASE_LABELS = {
    CombatPhase.OUT_OF_COMBAT: "Hors combat",
    CombatPhase.PLACEMENT: "Placement",
    CombatPhase.FIGHTING: "Combat",
    CombatPhase.RESULTS: "Résultats",
    CombatPhase.UNKNOWN: "Inconnu",
}
TURN_LABELS = {
    TurnOwner.PLAYER: "Mon tour",
    TurnOwner.OTHER: "Tour d'un autre",
    TurnOwner.UNKNOWN: "Tour inconnu",
}


def validate_truth(phase: str | None, turn: str | None) -> None:
    """Le tour n'a de sens qu'en phase FIGHTING ; il y est obligatoire (UNKNOWN permis)."""
    if phase is None:
        if turn is not None:
            raise ValueError("Un tour annoté exige une phase")
        return
    if phase not in CombatPhase.__members__:
        raise ValueError(f"Phase de combat inconnue : {phase}")
    if phase == CombatPhase.FIGHTING.value:
        if turn not in TurnOwner.__members__:
            raise ValueError("Phase COMBAT : indiquez à qui est le tour (ou « inconnu »)")
    elif turn is not None:
        raise ValueError("Le tour ne s'annote qu'en phase COMBAT")


@dataclass(frozen=True)
class SemanticCombatState:
    phase: CombatPhase = CombatPhase.UNKNOWN
    turn_owner: TurnOwner = TurnOwner.UNKNOWN
    confidence: float = 0.0
    reasons: tuple[str, ...] = ()
    evidence: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {"phase": self.phase.value, "turn_owner": self.turn_owner.value,
                "confidence": round(float(self.confidence), 4), "reasons": list(self.reasons),
                "evidence": dict(self.evidence)}

    @property
    def label(self) -> str:
        if self.phase is CombatPhase.FIGHTING:
            return f"{PHASE_LABELS[self.phase]} · {TURN_LABELS[self.turn_owner]}"
        return PHASE_LABELS[self.phase]


__all__ = [name for name in dir() if not name.startswith("_")]
