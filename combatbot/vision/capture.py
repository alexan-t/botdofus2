"""Capture d'écran bornée au rectangle client courant."""

from __future__ import annotations

import cv2
import numpy as np
import time
from PIL import ImageGrab

from combatbot.vision.models import CapturedFrame
from combatbot.vision.window import activate_window, client_geometry, window_bounds


def capture_client(hwnd: int, activate: bool = True) -> CapturedFrame:
    """Capture le client en pixels physiques, sans appliquer une seconde échelle DPI."""
    activated = activate_window(hwnd) if activate else False
    if activate:
        time.sleep(0.12)
    before = client_geometry(hwnd)
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
    if screenshot is None:
        import pyautogui  # Repli sur la portion visible du bureau.
        screenshot = pyautogui.screenshot(region=(before.left, before.top, before.width, before.height))
    if used_window_capture and float(np.asarray(screenshot.convert("RGB")).std()) < 2:
        try:
            import pyautogui
            screenshot = pyautogui.screenshot(region=(before.left, before.top, before.width, before.height))
            used_window_capture = False
        except OSError as exc:
            raise RuntimeError("Capture de fenêtre uniforme et capture visible indisponible") from exc
    after = client_geometry(hwnd)
    if before != after:
        raise RuntimeError("Le client a bougé ou changé de taille durant la capture ; réessayez")
    image = cv2.cvtColor(np.asarray(screenshot.convert("RGB")), cv2.COLOR_RGB2BGR)
    if float(image.std()) < 2:
        raise RuntimeError("La capture cliente est uniforme ; vérifiez la fenêtre et son rendu")
    if image.shape[:2] != (before.height, before.width):
        raise RuntimeError("La capture ne correspond pas à la zone cliente")
    return CapturedFrame(hwnd, before, image, activated, "window" if used_window_capture else "desktop")
