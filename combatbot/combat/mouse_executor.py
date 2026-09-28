"""FAST-5A2 : ``MouseActionExecutor`` préparé mais **désactivé**.

Envoie une ``ScreenAction`` (5A1) à un backend d'entrée injecté : amener le pointeur, revérifier,
puis UN clic gauche. Rien ne part si l'une de ces conditions manque :

1. l'exécuteur a été construit avec ``enabled=True`` (défaut : False) ;
2. le backend est un backend de test, ou la porte globale ``RealInputGate`` est ouverte ;
3. l'arrêt d'urgence (F9) n'est pas déclenché — vérifié avant le déplacement ET avant le clic ;
4. la fenêtre au premier plan est exactement celle attendue, à la même position et à la même taille
   que la géométrie ayant servi à traduire l'action — vérifié avant le déplacement ET avant le clic ;
5. le point est dans le client et l'action est un clic gauche simple (jamais de double clic).

Le backend Windows réel vit dans ``combatbot.input.win32_mouse`` ; il n'est jamais construit ici
par défaut et il refuse lui-même d'agir sous pytest. Le backend par défaut enregistre seulement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from combatbot.combat.safety import (
    GLOBAL_EMERGENCY_STOP, REAL_INPUT_GATE, ActionJournal, EmergencyStop, RealInputGate,
)
from combatbot.combat.screen_actions import ScreenAction, ScreenActionKind, ScreenGeometry


class InputBackend(Protocol):
    real: bool                                   # True seulement pour un backend qui agit sur l'OS

    def move_to(self, x: int, y: int) -> None: ...
    def left_click(self) -> None: ...           # un seul clic à la position courante


@dataclass(frozen=True)
class WindowState:
    hwnd: int | None
    foreground_hwnd: int | None
    client_left: int | None
    client_top: int | None
    client_width: int | None
    client_height: int | None


class WindowProbe(Protocol):
    def current(self, hwnd: int) -> WindowState: ...


@dataclass
class RecordingBackend:
    """Backend par défaut et de test : enregistre les appels, n'agit jamais."""
    real: bool = False
    calls: list[tuple] = field(default_factory=list)

    def move_to(self, x: int, y: int) -> None:
        self.calls.append(("move_to", x, y))

    def left_click(self) -> None:
        self.calls.append(("left_click",))


@dataclass(frozen=True)
class SendResult:
    sent: bool
    reasons: tuple[str, ...] = ()
    clicked_at: tuple[int, int] | None = None


def window_mismatch(state: WindowState, geometry: ScreenGeometry) -> str | None:
    if state.hwnd != geometry.hwnd:
        return "fenêtre DOFUS introuvable ou différente"
    if state.foreground_hwnd != geometry.hwnd:
        return "la fenêtre DOFUS n'est pas au premier plan"
    origin, size = geometry.layout.client_screen_origin, geometry.layout.client_size
    if (state.client_left, state.client_top) != (round(origin.x), round(origin.y)):
        return "fenêtre DOFUS déplacée"
    if (state.client_width, state.client_height) != (size.width, size.height):
        return "fenêtre DOFUS redimensionnée"
    return None


class MouseActionExecutor:
    def __init__(self, backend: InputBackend | None = None, *, window_probe: WindowProbe,
                 enabled: bool = False, gate: RealInputGate = REAL_INPUT_GATE,
                 stop: EmergencyStop = GLOBAL_EMERGENCY_STOP, journal: ActionJournal | None = None) -> None:
        self.backend = backend if backend is not None else RecordingBackend()
        self.window_probe = window_probe
        self.enabled = enabled
        self.gate = gate
        self.stop = stop
        self.journal = journal if journal is not None else ActionJournal()

    def _blocking(self, action: ScreenAction, geometry: ScreenGeometry) -> str | None:
        if not self.enabled:
            return "exécuteur souris désactivé"
        if getattr(self.backend, "real", True):
            refusal = self.gate.refusal()
            if refusal is not None:
                return refusal
        if self.stop.triggered:
            return f"arrêt d'urgence : {self.stop.reason}"
        if action.kind is not ScreenActionKind.CLICK or action.clicks != 1 or action.button != "left":
            return "seul un clic gauche simple est permis"
        size = geometry.layout.client_size
        if not (0 <= action.client_x < size.width and 0 <= action.client_y < size.height):
            return "point hors du client"
        return window_mismatch(self.window_probe.current(geometry.hwnd), geometry)

    def send(self, action: ScreenAction, geometry: ScreenGeometry, *, correlation_id: str) -> SendResult:
        point = action.screen_point
        self.journal.before(correlation_id, action=action.to_dict(), backend_real=getattr(self.backend, "real", True))
        reason = self._blocking(action, geometry)
        if reason is not None:
            self.journal.after(correlation_id, sent=False, reason=reason)
            return SendResult(False, (reason,))
        self.backend.move_to(*point)
        reason = self._blocking(action, geometry)          # F9 / fenêtre entre le déplacement et le clic
        if reason is not None:
            self.journal.after(correlation_id, sent=False, reason=f"clic annulé après déplacement : {reason}")
            return SendResult(False, (f"clic annulé après déplacement : {reason}",))
        self.backend.left_click()
        self.journal.after(correlation_id, sent=True, point=list(point))
        return SendResult(True, (), point)
