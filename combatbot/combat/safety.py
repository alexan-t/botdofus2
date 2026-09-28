"""FAST-SAFETY : garde-fous vérifiés avant toute action écran, réelle ou simulée.

- ``EmergencyStop`` : verrou (F9). Une fois déclenché il le reste jusqu'au réarmement explicite ;
  il est vérifié avant chaque action et entre le déplacement du pointeur et le clic.
- ``RealInputGate`` : interrupteur global de l'entrée réelle. Fermé par défaut ; ne s'ouvre qu'avec
  une variable d'environnement explicite ET un appel ``authorize`` ; jamais sous pytest ; ``kill``
  le ferme définitivement pour le processus.
- ``SafetyGuard`` : limites (actions par tour, clics par seconde, durée du tour) et invariants
  (fenêtre, layout, map, fraîcheur de l'observation et du plan, ``safe_for_decision``).
- ``ActionJournal`` : une entrée AVANT et une entrée APRÈS chaque action.

Module pur : ni Qt, ni OpenCV, ni Win32.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

REAL_INPUT_ENV = "DOFBOT_ALLOW_REAL_INPUT"
REAL_INPUT_ENV_VALUE = "I_UNDERSTAND_THIS_CLICKS_IN_DOFUS"


class EmergencyStop:
    """Verrou thread-safe : la touche F9 (thread UI) le déclenche, la boucle d'action le lit."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._reason: str | None = None
        self._at: float | None = None

    def trigger(self, reason: str = "arrêt d'urgence") -> None:
        with self._lock:
            if self._reason is None:
                self._reason, self._at = reason, self._clock()

    @property
    def triggered(self) -> bool:
        with self._lock:
            return self._reason is not None

    @property
    def reason(self) -> str | None:
        with self._lock:
            return self._reason

    def rearm(self) -> None:
        """Action humaine explicite uniquement (bouton Démarrer), jamais automatique."""
        with self._lock:
            self._reason, self._at = None, None


GLOBAL_EMERGENCY_STOP = EmergencyStop()


class RealInputBlocked(RuntimeError):
    """Levée par tout backend réel appelé alors que l'entrée réelle n'est pas autorisée."""


class RealInputGate:
    def __init__(self, environ: dict[str, str] | None = None) -> None:
        self._environ = environ if environ is not None else os.environ
        self._authorized = False
        self._killed = False
        self._lock = threading.Lock()

    @property
    def under_test(self) -> bool:
        return "PYTEST_CURRENT_TEST" in self._environ

    def refusal(self) -> str | None:
        """None si l'entrée réelle est permise, sinon la raison du refus."""
        with self._lock:
            if self._killed:
                return "entrée réelle coupée (kill switch)"
            if self.under_test:
                return "entrée réelle interdite pendant les tests"
            if self._environ.get(REAL_INPUT_ENV) != REAL_INPUT_ENV_VALUE:
                return f"entrée réelle non autorisée ({REAL_INPUT_ENV} absent)"
            if not self._authorized:
                return "entrée réelle non armée pour cette session"
            return None

    def authorize(self) -> str | None:
        """Arme l'entrée réelle si l'environnement l'autorise ; retourne la raison d'un refus."""
        with self._lock:
            self._authorized = True
        reason = self.refusal()
        if reason is not None:
            with self._lock:
                self._authorized = False
        return reason

    def kill(self) -> None:
        with self._lock:
            self._killed, self._authorized = True, False

    def require(self) -> None:
        reason = self.refusal()
        if reason is not None:
            raise RealInputBlocked(reason)


REAL_INPUT_GATE = RealInputGate()


@dataclass(frozen=True)
class SafetyLimits:
    max_actions_per_turn: int = 12        # clics, sort + cible comptent pour deux
    max_clicks_per_second: float = 2.0
    max_turn_duration_s: float = 45.0
    plan_ttl_s: float = 5.0
    observation_max_age_s: float = 1.0


@dataclass(frozen=True)
class ActionContext:
    """Ce qui est vrai au moment d'agir (dernière observation + fenêtre)."""
    hwnd: int | None
    layout_digest: str | None
    map_id: int | None
    observation_at: float | None
    safe_for_decision: bool | None


@dataclass(frozen=True)
class SafetyVerdict:
    allowed: bool
    reasons: tuple[str, ...] = ()


class SafetyGuard:
    def __init__(self, limits: SafetyLimits = SafetyLimits(), stop: EmergencyStop = GLOBAL_EMERGENCY_STOP,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limits = limits
        self.stop = stop
        self.clock = clock
        self.turn_started: float | None = None
        self.actions_this_turn = 0
        self._clicks: deque[float] = deque()

    def begin_turn(self) -> None:
        self.turn_started, self.actions_this_turn = self.clock(), 0

    def check(self, action, *, expected_hwnd: int, expected_layout: str, expected_map: int | None,
              context: ActionContext, plan_created_at: float) -> SafetyVerdict:
        now = self.clock()
        limits = self.limits
        reasons = []
        if self.stop.triggered:
            reasons.append(f"arrêt d'urgence : {self.stop.reason}")
        if getattr(action, "clicks", None) != 1 or getattr(action, "button", None) != "left":
            reasons.append("seul un clic gauche simple est permis")
        if context.safe_for_decision is not True:
            reasons.append("observation non sûre (safe_for_decision)")
        if context.observation_at is None or now - context.observation_at > limits.observation_max_age_s:
            reasons.append("observation trop ancienne")
        if now - plan_created_at > limits.plan_ttl_s:
            reasons.append("plan expiré")
        if context.hwnd is None or context.hwnd != expected_hwnd:
            reasons.append("fenêtre DOFUS différente de celle attendue")
        if context.layout_digest is None or context.layout_digest != expected_layout:
            reasons.append("layout changé depuis la traduction")
        if expected_map is None or context.map_id != expected_map:
            reasons.append("map changée ou inconnue")
        if self.turn_started is None:
            reasons.append("tour non commencé")
        elif now - self.turn_started > limits.max_turn_duration_s:
            reasons.append("durée maximale du tour dépassée")
        if self.actions_this_turn >= limits.max_actions_per_turn:
            reasons.append("nombre maximal d'actions du tour atteint")
        while self._clicks and now - self._clicks[0] >= 1.0:
            self._clicks.popleft()
        if len(self._clicks) >= limits.max_clicks_per_second:
            reasons.append("cadence maximale de clics atteinte")
        return SafetyVerdict(not reasons, tuple(reasons))

    def record_action(self) -> None:
        self.actions_this_turn += 1
        self._clicks.append(self.clock())


@dataclass
class ActionJournal:
    """Journal append-only : BEFORE puis AFTER pour chaque action (aussi en dry-run)."""
    sink: Callable[[dict[str, object]], None] | None = None
    entries: list[dict[str, object]] = field(default_factory=list)

    def record(self, phase: str, correlation_id: str, **data: object) -> None:
        entry = {"phase": phase, "correlation_id": correlation_id, **data}
        self.entries.append(entry)
        if self.sink is not None:
            self.sink(entry)

    def before(self, correlation_id: str, **data: object) -> None:
        self.record("BEFORE", correlation_id, **data)

    def after(self, correlation_id: str, **data: object) -> None:
        self.record("AFTER", correlation_id, **data)

    def unmatched(self) -> list[str]:
        """Actions journalisées AVANT sans entrée APRÈS (doit rester vide)."""
        open_ids: list[str] = []
        for entry in self.entries:
            if entry["phase"] == "BEFORE":
                open_ids.append(str(entry["correlation_id"]))
            elif entry["phase"] == "AFTER" and str(entry["correlation_id"]) in open_ids:
                open_ids.remove(str(entry["correlation_id"]))
        return open_ids
