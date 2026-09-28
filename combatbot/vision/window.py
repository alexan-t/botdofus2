"""Fenêtres Win32 par handle et géométrie de la zone cliente physique."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from functools import lru_cache
import sys

from combatbot.vision.models import ClientRect, WindowInfo


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Connexion au client disponible uniquement sous Windows")
    user32 = _user32()
    # Per-monitor v2; Windows peut avoir déjà fixé le mode DPI via Qt.
    user32.SetProcessDpiAwarenessContext(wintypes.HANDLE(-4))


@lru_cache(maxsize=1)
def _user32():
    if sys.platform != "win32":
        raise RuntimeError("Connexion au client disponible uniquement sous Windows")
    dll = ctypes.WinDLL("user32", use_last_error=True)
    dll.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    dll.EnumWindows.restype = wintypes.BOOL
    dll.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    dll.GetWindowTextLengthW.restype = ctypes.c_int
    dll.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    dll.GetWindowTextW.restype = ctypes.c_int
    for name in ("IsWindow", "IsWindowVisible", "IsIconic"):
        function = getattr(dll, name)
        function.argtypes = [wintypes.HWND]
        function.restype = wintypes.BOOL
    dll.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    dll.GetClientRect.restype = wintypes.BOOL
    dll.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    dll.GetWindowRect.restype = wintypes.BOOL
    dll.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    dll.ClientToScreen.restype = wintypes.BOOL
    dll.SetForegroundWindow.argtypes = [wintypes.HWND]
    dll.SetForegroundWindow.restype = wintypes.BOOL
    dll.GetForegroundWindow.restype = wintypes.HWND
    if hasattr(dll, "GetDpiForWindow"):
        dll.GetDpiForWindow.argtypes = [wintypes.HWND]
        dll.GetDpiForWindow.restype = wintypes.UINT
    dll.SetProcessDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    dll.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
    return dll


def window_dpi(hwnd: int) -> int | None:
    """Retourne le DPI effectif lorsque l'API Windows le fournit."""
    user32 = _user32()
    function = getattr(user32, "GetDpiForWindow", None)
    if function is None or not user32.IsWindow(hwnd):
        return None
    value = int(function(hwnd))
    return value or None


def _title(hwnd: int) -> str:
    user32 = _user32()
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


GAME_EXECUTABLE = "dofus.exe"


def _process_name(hwnd: int) -> str | None:
    """Nom de l'exécutable propriétaire (lecture seule) ; ``None`` si Windows ne le fournit pas."""
    try:
        user32 = _user32()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value.replace("/", "\\").rsplit("\\", 1)[-1].casefold()
        finally:
            kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        return None


def list_dofus_windows(fragment: str = "DOFUS") -> list[WindowInfo]:
    """Fenêtres du jeu. Un navigateur ou le launcher dont le titre contient « Dofus » est écarté
    dès que Windows identifie un autre exécutable que Dofus.exe."""
    user32 = _user32()
    windows: list[WindowInfo] = []
    # WINFUNCTYPE n'existe que sous Windows (même type qu'avant) ; le repli CFUNCTYPE ne sert qu'à
    # exécuter la logique de filtrage ailleurs, avec un user32 simulé.
    callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd: int, _lparam: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            title = _title(hwnd)
            if fragment.casefold() in title.casefold() and _process_name(hwnd) in (None, GAME_EXECUTABLE):
                windows.append(WindowInfo(int(hwnd), title, bool(user32.IsIconic(hwnd))))
        return True

    callback = callback_type(visit)
    if not user32.EnumWindows(callback, 0) and ctypes.get_last_error():
        raise RuntimeError("Impossible d'énumérer les fenêtres Windows")
    return windows


def client_geometry(hwnd: int) -> ClientRect:
    user32 = _user32()
    if not user32.IsWindow(hwnd):
        raise RuntimeError("Le client DOFUS a été fermé")
    if user32.IsIconic(hwnd):
        raise RuntimeError("Le client DOFUS est minimisé")
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("Impossible de lire la zone cliente")
    origin = wintypes.POINT(rect.left, rect.top)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise RuntimeError("Impossible de localiser la zone cliente")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError("La zone cliente est vide")
    return ClientRect(origin.x, origin.y, width, height)


def inspect_dofus_window(hwnd: int) -> tuple[WindowInfo, ClientRect]:
    """Revalidate a selected handle; Windows may reuse a closed window's handle."""
    user32 = _user32()
    if not user32.IsWindow(hwnd):
        raise RuntimeError("Fenêtre fermée ou HWND invalide")
    title = _title(hwnd)
    if not user32.IsWindowVisible(hwnd) or "dofus" not in title.casefold():
        raise RuntimeError("Le HWND ne désigne plus une fenêtre DOFUS visible")
    if user32.IsIconic(hwnd):
        raise RuntimeError("Le client DOFUS est minimisé")
    return WindowInfo(hwnd, title, False), client_geometry(hwnd)


def window_bounds(hwnd: int) -> ClientRect:
    user32 = _user32()
    if not user32.IsWindow(hwnd):
        raise RuntimeError("Le client DOFUS a été fermé")
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("Impossible de lire le rectangle de la fenêtre")
    return ClientRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def activate_window(hwnd: int) -> bool:
    client_geometry(hwnd)
    if _user32().GetForegroundWindow() == hwnd:
        return True
    if not _user32().SetForegroundWindow(hwnd):
        return False
    return _user32().GetForegroundWindow() == hwnd
