"""Contrats du moteur de combat, distincts de l'observation visuelle du LOT 2.

Le port d'exécution n'est connecté ni au simulateur ni au client DOFUS.
"""

from __future__ import annotations

from typing import Protocol

from combatbot.models import Action
from combatbot.vision.combat_models import CombatObservation


class CombatObserver(Protocol):
    def observe(self) -> CombatObservation: ...


class ActionExecutor(Protocol):
    def execute(self, action: Action) -> None: ...
