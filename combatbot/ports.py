"""Contrats du moteur de combat, distincts de l'observation visuelle du LOT 2.

Le port d'exécution n'est connecté ni au simulateur ni au client DOFUS. FAST-5A0 : l'implémentation
disponible est ``combatbot.combat.executor.DryRunActionExecutor`` (journalise, n'agit jamais) ; le
contrat corrélé des plans (début, effet attendu, succès/échec/timeout) est ``ClosedLoopExecutor``.
"""

from __future__ import annotations

from typing import Protocol

from combatbot.models import Action
from combatbot.vision.combat_models import CombatObservation


class CombatObserver(Protocol):
    def observe(self) -> CombatObservation: ...


class ActionExecutor(Protocol):
    def execute(self, action: Action) -> None: ...
