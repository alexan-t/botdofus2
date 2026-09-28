"""Capture d'écran bornée au rectangle client courant."""

from __future__ import annotations

import cv2
import numpy as np
import time
from PIL import ImageGrab

from combatbot.vision.models import CapturedFrame
from combatbot.vision.window import activate_window, client_geometry, window_bounds

UNIFORM_STD = 2.0
# Écart type d'un sous-échantillon 1/64 au-dessus duquel l'image n'est pas uniforme sans calcul complet.
# En dessous, l'écart type complet tranche : une capture n'est rejetée que si l'image ENTIÈRE est uniforme.
UNIFORM_SCREEN_MARGIN = 8.0


def is_uniform(pixels: np.ndarray) -> bool:
    """Même critère qu'avant (écart type complet < 2), sans parcourir 10 Mpx à chaque frame normale."""
    if float(pixels[::8, ::8].std()) >= UNIFORM_SCREEN_MARGIN:
        return False
    return float(pixels.std()) < UNIFORM_STD


def capture_client(hwnd: int, activate: bool = True) -> CapturedFrame:
    """Capture le client en pixels physiques, sans appliquer une seconde échelle DPI."""
    timings: dict[str, float] = {}
    lap = [time.perf_counter()]

    def mark(name: str) -> None:
        now = time.perf_counter()
        timings[name] = round((now - lap[0]) * 1000, 2)
        lap[0] = now

    activated = activate_window(hwnd) if activate else False
    if activate:
        time.sleep(0.12)
    mark("activate")
    before = client_geometry(hwnd)
    mark("geometry")
    screenshot = None
    try:
        window_image = ImageGrab.grab(window=hwnd)
        if window_image.size == (before.width, before.height):
            screenshot = window_image
        else:
            bounds = window_bounds(hwnd)
            if window_image.size == (bounds.width, bounds.height):
                x, y = before.left - bounds.left, before.top - bounds.top
                if 0 <= x and 0 <= y and x + before.width <= bounds.width and y + before.height <= bounds.height:
                    screenshot = window_image.crop((x, y, x + before.width, y + before.height))
    except OSError:
        pass
    used_window_capture = screenshot is not None
    mark("grab_window")
    if screenshot is None:
        import pyautogui  # Repli sur la portion visible du bureau.
        screenshot = pyautogui.screenshot(region=(before.left, before.top, before.width, before.height))
        mark("grab_desktop")
    rgb = np.asarray(screenshot.convert("RGB"))          # une seule conversion par capture
    mark("to_rgb")
    if used_window_capture and is_uniform(rgb):
        try:
            import pyautogui
            screenshot = pyautogui.screenshot(region=(before.left, before.top, before.width, before.height))
            used_window_capture = False
        except OSError as exc:
            raise RuntimeError("Capture de fenêtre uniforme et capture visible indisponible") from exc
        rgb = np.asarray(screenshot.convert("RGB"))
        mark("grab_desktop_after_uniform")
    after = client_geometry(hwnd)
    if before != after:
        raise RuntimeError("Le client a bougé ou changé de taille durant la capture ; réessayez")
    image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    mark("to_bgr")
    if is_uniform(image):
        raise RuntimeError("La capture cliente est uniforme ; vérifiez la fenêtre et son rendu")
    if image.shape[:2] != (before.height, before.width):
        raise RuntimeError("La capture ne correspond pas à la zone cliente")
    mark("checks")
    return CapturedFrame(hwnd, before, image, activated, "window" if used_window_capture else "desktop", timings)
