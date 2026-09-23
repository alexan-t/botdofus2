"""Diagnostic de connexion fondé sur une capture, sans calibration préalable."""

from __future__ import annotations

import cv2
import numpy as np

from combatbot.vision.capture import capture_client
from combatbot.vision.autocalibration import suggest_zones
from combatbot.vision.icons import KnownIcon
from combatbot.vision.models import CaptureAssessment, CapturedFrame, ConnectionResult
from combatbot.vision.window import inspect_dofus_window


def assess_capture(frame: CapturedFrame, known_icons: list[KnownIcon] | None = None) -> CaptureAssessment:
    image = frame.image
    if image.ndim != 3 or image.shape[:2] != (frame.client.height, frame.client.width):
        return CaptureAssessment(False, False, "CAPTURE_SIZE", "Capture de dimensions incohérentes")
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark_fraction = float(np.mean(gray < 8))
    edges = cv2.Canny(gray, 60, 140)
    edge_density = float(np.mean(edges > 0))
    tiles = [gray[y:y + max(1, gray.shape[0] // 4), x:x + max(1, gray.shape[1] // 4)]
             for y in range(0, gray.shape[0], max(1, gray.shape[0] // 4))
             for x in range(0, gray.shape[1], max(1, gray.shape[1] // 4))]
    textured_tiles = sum(float(tile.std()) >= 8 for tile in tiles if tile.size)
    details: dict[str, object] = {
        "source": frame.source, "activation": frame.activation_succeeded,
        "dark_fraction": round(dark_fraction, 3), "edge_density": round(edge_density, 4),
        "textured_tiles": textured_tiles,
    }
    if dark_fraction > 0.96 or float(gray.std()) < 4:
        return CaptureAssessment(False, False, "CAPTURE_BLACK", "Capture noire ou presque uniforme", details)
    if edge_density < 0.002 or textured_tiles < 3:
        return CaptureAssessment(False, False, "CAPTURE_EMPTY", "Capture sans détails visuels suffisants", details)

    matches = 0
    for known in (known_icons or [])[:12]:
        icon = cv2.imdecode(np.frombuffer(known.icon_png, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if icon is None or min(icon.shape) < 16 or float(icon.std()) < 8:
            continue
        if icon.shape[0] > gray.shape[0] or icon.shape[1] > gray.shape[1]:
            continue
        score = float(cv2.matchTemplate(gray, icon, cv2.TM_CCOEFF_NORMED).max())
        if score >= 0.94:
            matches += 1
    details["confirmed_icon_matches"] = matches
    if matches >= 2 and frame.source == "window":
        return CaptureAssessment(True, True, "CONTENT_VERIFIED", "Contenu visuel vérifié par des icônes confirmées", details)
    reason = "Capture disponible — contenu à vérifier"
    if frame.source == "desktop" and not frame.activation_succeeded:
        reason += " (activation Windows refusée ; recouvrement possible)"
    return CaptureAssessment(True, False, "CONTENT_UNCERTAIN", reason, details)


def connect_window(hwnd: int, known_icons: list[KnownIcon] | None = None) -> ConnectionResult:
    """Run in a worker; never describe a geometry-only probe as connected."""
    try:
        info, geometry = inspect_dofus_window(hwnd)
    except RuntimeError as exc:
        message = str(exc)
        code = ("WINDOW_MINIMIZED" if "minimis" in message else
                "WINDOW_CLOSED" if "fermée" in message or "HWND invalide" in message else
                "WINDOW_WRONG_TARGET" if "ne désigne plus" in message else "WINDOW_INVALID")
        return ConnectionResult("sélection", False, code, message, {"hwnd": hwnd})
    try:
        frame = capture_client(hwnd)
        assessment = assess_capture(frame, known_icons)
    except (RuntimeError, OSError, ValueError) as exc:
        message = str(exc)
        code = ("CAPTURE_BLACK" if "uniforme" in message or "noire" in message else
                "CAPTURE_MOVED" if "bougé" in message or "taille" in message else "CAPTURE_FAILED")
        return ConnectionResult("capture", False, code, message, {
            "hwnd": hwnd, "title": info.title, "width": geometry.width, "height": geometry.height,
        })
    details = {"hwnd": hwnd, "title": info.title, "width": geometry.width, "height": geometry.height,
               **assessment.details}
    if assessment.usable:
        suggestions = suggest_zones(frame)
        details["zone_rects"] = {name: suggestion.rect for name, suggestion in suggestions.items()}
        details["zone_confidence"] = {name: suggestion.evidence.confidence for name, suggestion in suggestions.items()}
    return ConnectionResult("capture", assessment.usable, assessment.code,
                            assessment.message, details, frame if assessment.usable else None)
