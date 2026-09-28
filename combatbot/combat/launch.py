"""FAST-6C : lancement de combat — **machine à états seulement**, aucun clic, aucune détection.

EXPLORATION → TARGET_CONFIRMED → LAUNCH_REQUESTED → WAIT_PLACEMENT → PLACEMENT → WAIT_FIGHT → FIGHTING,
et FAILED (terminal jusqu'à ``reset`` humain).

Les entrées sont des événements fournis de l'extérieur :

- ``TargetConfirmed`` : produit par un futur détecteur de groupe (inexistant aujourd'hui) ;
- ``LaunchSent`` / ``PlacementDone`` : produits par un futur exécuteur (5A2/6B), jamais ici ;
- ``PhaseObserved`` : phase 3B-6B (EXPLORATION, PLACEMENT, FIGHTING, RESULTS ou None) ;
- ``TargetLost``, ``MapChanged``, ``Stop`` ; ``tick(now)`` applique les délais.

Toute transition non prévue est refusée (état inchangé, raison journalisée) ; une phase incohérente
pendant l'attente (RESULTS avant le combat…), un délai dépassé ou l'arrêt d'urgence → FAILED.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class LaunchState(str, Enum):
    EXPLORATION = "EXPLORATION"
    TARGET_CONFIRMED = "TARGET_CONFIRMED"
    LAUNCH_REQUESTED = "LAUNCH_REQUESTED"
    WAIT_PLACEMENT = "WAIT_PLACEMENT"
    PLACEMENT = "PLACEMENT"
    WAIT_FIGHT = "WAIT_FIGHT"
    FIGHTING = "FIGHTING"
    FAILED = "FAILED"


@dataclass(frozen=True)
class TargetConfirmed:
    at: float
    target_id: str
    map_id: int | None
    confidence: float


@dataclass(frozen=True)
class LaunchRequested:
    at: float


@dataclass(frozen=True)
class LaunchSent:
    at: float


@dataclass(frozen=True)
class PhaseObserved:
    at: float
    phase: str | None


@dataclass(frozen=True)
class PlacementDone:
    at: float


@dataclass(frozen=True)
class TargetLost:
    at: float


@dataclass(frozen=True)
class MapChanged:
    at: float
    map_id: int | None


@dataclass(frozen=True)
class Stop:
    at: float
    reason: str = "arrêt d'urgence"


@dataclass(frozen=True)
class LaunchConfig:
    min_target_confidence: float = 0.9
    target_ttl_s: float = 5.0             # une cible confirmée trop ancienne n'est plus lancée
    wait_placement_s: float = 10.0
    wait_fight_s: float = 60.0


@dataclass
class LaunchStateMachine:
    config: LaunchConfig = LaunchConfig()
    state: LaunchState = LaunchState.EXPLORATION
    target: TargetConfirmed | None = None
    entered_at: float = 0.0
    history: list[tuple[float, str, str, str]] = field(default_factory=list)   # (t, avant, après, raison)
    refusals: list[tuple[float, str, str]] = field(default_factory=list)

    def _go(self, at: float, state: LaunchState, reason: str) -> LaunchState:
        self.history.append((at, self.state.value, state.value, reason))
        self.state, self.entered_at = state, at
        return state

    def _refuse(self, at: float, event, reason: str) -> LaunchState:
        self.refusals.append((at, type(event).__name__, reason))
        return self.state

    def reset(self, at: float) -> LaunchState:
        """Retour humain explicite en exploration (seule sortie de FAILED)."""
        self.target = None
        return self._go(at, LaunchState.EXPLORATION, "réinitialisation")

    def tick(self, now: float) -> LaunchState:
        deadlines = {LaunchState.TARGET_CONFIRMED: self.config.target_ttl_s,
                     LaunchState.WAIT_PLACEMENT: self.config.wait_placement_s,
                     LaunchState.WAIT_FIGHT: self.config.wait_fight_s}
        limit = deadlines.get(self.state)
        if limit is not None and now - self.entered_at > limit:
            if self.state is LaunchState.TARGET_CONFIRMED:
                self.target = None
                return self._go(now, LaunchState.EXPLORATION, "cible confirmée expirée")
            return self._go(now, LaunchState.FAILED, f"délai dépassé en {self.state.value}")
        return self.state

    def handle(self, event) -> LaunchState:
        at = event.at
        if isinstance(event, Stop):
            return self.state if self.state is LaunchState.FAILED else self._go(at, LaunchState.FAILED, event.reason)
        if self.state is LaunchState.FAILED:
            return self._refuse(at, event, "machine en échec : réinitialisation humaine requise")
        state = self.state
        if isinstance(event, TargetConfirmed):
            if state is not LaunchState.EXPLORATION:
                return self._refuse(at, event, f"cible reçue en {state.value}")
            if event.map_id is None or event.confidence < self.config.min_target_confidence:
                return self._refuse(at, event, "cible non prouvée (map inconnue ou confiance insuffisante)")
            self.target = event
            return self._go(at, LaunchState.TARGET_CONFIRMED, f"cible {event.target_id}")
        if isinstance(event, (TargetLost, MapChanged)):
            if state in (LaunchState.TARGET_CONFIRMED, LaunchState.LAUNCH_REQUESTED):
                self.target = None
                return self._go(at, LaunchState.EXPLORATION, "cible perdue ou map changée avant le lancement")
            if state in (LaunchState.WAIT_PLACEMENT, LaunchState.PLACEMENT, LaunchState.WAIT_FIGHT) \
                    and isinstance(event, MapChanged):
                return self._go(at, LaunchState.FAILED, "map changée pendant le lancement")
            return self._refuse(at, event, f"ignoré en {state.value}")
        if isinstance(event, LaunchRequested):
            if state is not LaunchState.TARGET_CONFIRMED or self.target is None:
                return self._refuse(at, event, "lancement sans cible confirmée")
            if at - self.target.at > self.config.target_ttl_s:
                self.target = None
                return self._go(at, LaunchState.EXPLORATION, "cible confirmée expirée")
            return self._go(at, LaunchState.LAUNCH_REQUESTED, "lancement demandé (aucun clic ici)")
        if isinstance(event, LaunchSent):
            if state is not LaunchState.LAUNCH_REQUESTED:
                return self._refuse(at, event, "envoi non demandé")
            return self._go(at, LaunchState.WAIT_PLACEMENT, "attente de la phase de placement")
        if isinstance(event, PlacementDone):
            if state is not LaunchState.PLACEMENT:
                return self._refuse(at, event, "placement terminé hors phase de placement")
            return self._go(at, LaunchState.WAIT_FIGHT, "attente du début du combat")
        if isinstance(event, PhaseObserved):
            return self._on_phase(at, event)
        return self._refuse(at, event, "événement inconnu")

    def _on_phase(self, at: float, event: PhaseObserved) -> LaunchState:
        state, phase = self.state, event.phase
        if phase is None:
            return state                                 # UNKNOWN : on attend, les délais tranchent
        if state is LaunchState.WAIT_PLACEMENT:
            if phase == "PLACEMENT":
                return self._go(at, LaunchState.PLACEMENT, "phase de placement observée")
            if phase == "FIGHTING":
                return self._go(at, LaunchState.FIGHTING, "combat commencé sans placement observé")
            if phase == "RESULTS":
                return self._go(at, LaunchState.FAILED, "résultats observés avant le combat")
            return state                                 # encore en exploration : le délai tranchera
        if state in (LaunchState.PLACEMENT, LaunchState.WAIT_FIGHT):
            if phase == "FIGHTING":
                return self._go(at, LaunchState.FIGHTING, "combat commencé")
            if phase in ("EXPLORATION", "RESULTS"):
                return self._go(at, LaunchState.FAILED, f"phase {phase} pendant le placement")
            return state
        return state
