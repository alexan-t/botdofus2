"""FAST-5A2 : backend souris physique Windows — présent, **jamais appelé** dans les tests ni par défaut.

Chaque méthode exige d'abord ``RealInputGate.require()`` : sous pytest, sans la variable
d'environnement explicite ou sans armement de session, elle lève ``RealInputBlocked`` **avant** tout
appel Win32. La construction n'appelle rien. ``Win32WindowProbe`` ne fait que lire la fenêtre
(réutilise ``vision.window``).

Coordonnées : pixels physiques du bureau virtuel (le processus est DPI-aware per-monitor v2, comme
la capture). Un clic = LEFTDOWN puis LEFTUP, jamais de double clic.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from combatbot.combat.mouse_executor import WindowState
from combatbot.combat.safety import REAL_INPUT_GATE, RealInputGate

INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _InputUnion)]


class Win32MouseBackend:
    real = True

    def __init__(self, gate: RealInputGate = REAL_INPUT_GATE) -> None:
        self.gate = gate          # aucune API Windows appelée ici

    def _user32(self):
        self.gate.require()
        if sys.platform != "win32":
            raise RuntimeError("Souris physique disponible uniquement sous Windows")
        return ctypes.WinDLL("user32", use_last_error=True)

    def move_to(self, x: int, y: int) -> None:
        user32 = self._user32()
        if not user32.SetCursorPos(int(x), int(y)):
            raise OSError(ctypes.get_last_error(), "SetCursorPos a échoué")

    def left_click(self) -> None:
        user32 = self._user32()
        events = (_Input * 2)(_Input(INPUT_MOUSE, _InputUnion(_MouseInput(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, 0))),
                              _Input(INPUT_MOUSE, _InputUnion(_MouseInput(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, 0))))
        if user32.SendInput(2, events, ctypes.sizeof(_Input)) != 2:
            raise OSError(ctypes.get_last_error(), "SendInput n'a pas envoyé le clic complet")


class Win32WindowProbe:
    """Lecture seule : HWND, fenêtre au premier plan et zone cliente physique."""

    def current(self, hwnd: int) -> WindowState:
        from combatbot.vision.window import _user32, client_geometry
        try:
            client = client_geometry(hwnd)
        except RuntimeError:
            return WindowState(None, None, None, None, None, None)
        foreground = _user32().GetForegroundWindow()
        return WindowState(hwnd, int(foreground) if foreground else None, client.left, client.top,
                           client.width, client.height)
