"""Fenêtres du jeu proposées à l'écran « Connexion » (lecture seule, aucune activation)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from combatbot.vision.models import ClientRect, WindowInfo
from combatbot.vision.window import client_geometry, list_dofus_windows


@dataclass(frozen=True)
class GameWindow:
    hwnd: int
    title: str          # « Dofus · Kira »
    character: str | None
    meta: str           # « 1920×1080 · écran 1 »
    raw_title: str


def character_from_title(title: str) -> str | None:
    """Le client titre sa fenêtre « Perso - Classe - version - Release » ; l'écran de connexion
    n'affiche que « Dofus ». On ne garde que le nom du personnage, sans rien deviner d'autre."""
    first = title.split(" - ")[0].strip()
    if not first or "dofus" in first.casefold():
        return None
    return first


def screen_index(rect: ClientRect, screens: list[tuple[int, int, int, int]]) -> int | None:
    """Numéro (1…n) de l'écran qui contient le centre de la zone cliente."""
    cx, cy = rect.left + rect.width // 2, rect.top + rect.height // 2
    for number, (x, y, width, height) in enumerate(screens, start=1):
        if x <= cx < x + width and y <= cy < y + height:
            return number
    return None


def describe_window(info: WindowInfo, screens: list[tuple[int, int, int, int]],
                    geometry: Callable[[int], ClientRect] = client_geometry) -> GameWindow:
    character = character_from_title(info.title)
    title = f"Dofus · {character}" if character else "Dofus"
    if info.minimized:
        meta = "fenêtre réduite"
    else:
        try:
            rect = geometry(info.hwnd)
        except RuntimeError:
            meta = "taille inconnue"
        else:
            number = screen_index(rect, screens)
            meta = f"{rect.width}×{rect.height}" + (f" · écran {number}" if number else "")
    return GameWindow(info.hwnd, title, character, meta, info.title)


def discover_windows(screens: list[tuple[int, int, int, int]]) -> list[GameWindow]:
    """Peut lever RuntimeError hors Windows ; l'écran affiche alors le message."""
    return [describe_window(info, screens) for info in list_dofus_windows()]


def window_thumbnail(hwnd: int) -> np.ndarray | None:
    """Miniature BGR via la capture de fenêtre (sans activer le jeu ni lire le bureau, qui
    pourrait montrer DofBot2 par-dessus). ``None`` si Windows ne fournit pas l'image."""
    try:
        from PIL import ImageGrab
        image = ImageGrab.grab(window=hwnd)
    except (OSError, ValueError, TypeError):
        return None
    array = np.asarray(image.convert("RGB"))
    if array.size == 0 or float(array.std()) < 2:
        return None
    return np.ascontiguousarray(array[:, :, ::-1])
