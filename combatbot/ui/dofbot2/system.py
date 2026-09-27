"""Intégrations système de DofBot2 : démarrage avec Windows, raccourcis globaux, alertes."""

from __future__ import annotations

import json
import sys
from urllib.parse import urlparse
import urllib.request

from combatbot.runtime import PROJECT_ROOT, executable_path, is_frozen


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "DofBot2"
WM_HOTKEY = 0x0312
HOTKEYS = {1: ("start_pause", 0x77), 2: ("emergency_stop", 0x78)}  # F8, F9
WEBHOOK_HOSTS = ("discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com")


def launch_command() -> str:
    if is_frozen():
        return f'"{executable_path()}"'
    interpreter = executable_path().with_name("pythonw.exe") if sys.platform == "win32" else executable_path()
    if not interpreter.exists():
        interpreter = executable_path()
    return f'"{interpreter}" "{PROJECT_ROOT / "main.py"}"'


def set_autostart(enabled: bool) -> None:
    """Entrée « Exécuter » de l'utilisateur courant (HKCU), sans droits administrateur."""
    if sys.platform != "win32":
        raise RuntimeError("Le lancement avec Windows n'est disponible que sous Windows")
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def register_hotkeys(hwnd: int) -> list[str]:
    """Enregistre F8/F9 pour toute la session Windows ; retourne ceux déjà pris par une autre application."""
    if sys.platform != "win32":
        return []
    import ctypes
    user32 = ctypes.windll.user32
    refused = []
    for identifier, (name, virtual_key) in HOTKEYS.items():
        if not user32.RegisterHotKey(hwnd, identifier, 0x4000, virtual_key):  # MOD_NOREPEAT
            refused.append(name)
    return refused


def unregister_hotkeys(hwnd: int) -> None:
    if sys.platform != "win32":
        return
    import ctypes
    for identifier in HOTKEYS:
        ctypes.windll.user32.UnregisterHotKey(hwnd, identifier)


def hotkey_from_message(message_address: int) -> str | None:
    if sys.platform != "win32":
        return None
    from ctypes import wintypes
    msg = wintypes.MSG.from_address(message_address)
    if msg.message != WM_HOTKEY:
        return None
    entry = HOTKEYS.get(int(msg.wParam))
    return entry[0] if entry else None


def foreground_window() -> int | None:
    if sys.platform != "win32":
        return None
    import ctypes
    return int(ctypes.windll.user32.GetForegroundWindow() or 0) or None


def play_alert_sound() -> None:
    if sys.platform == "win32":
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    else:
        from PySide6.QtWidgets import QApplication
        QApplication.beep()


def webhook_problem(url: str) -> str | None:
    """Message d'erreur si l'adresse n'est pas un webhook Discord en HTTPS, sinon None."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in WEBHOOK_HOSTS \
            or not parsed.path.startswith("/api/webhooks/"):
        return "Adresse attendue : https://discord.com/api/webhooks/…"
    return None


def send_discord(url: str, title: str, body: str, timeout: float = 8.0) -> None:
    """Envoie l'alerte au webhook (appelé hors du thread Qt)."""
    problem = webhook_problem(url)
    if problem:
        raise ValueError(problem)
    payload = json.dumps({"username": "DofBot2", "content": f"**{title}** — {body}"}).encode("utf-8")
    request = urllib.request.Request(url.strip(), data=payload, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "DofBot2"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - hôte vérifié
        if response.status >= 300:
            raise RuntimeError(f"Discord a répondu {response.status}")
