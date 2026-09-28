"""Capture : même décision d'uniformité qu'avant, sans deux écarts types plein cadre par frame."""
from __future__ import annotations

import time

import numpy as np
from PIL import Image

from combatbot.vision import capture
from combatbot.vision.models import ClientRect


def legacy_uniform(pixels: np.ndarray) -> bool:
    return float(pixels.std()) < capture.UNIFORM_STD


def test_uniformity_decision_matches_the_full_frame_criterion() -> None:
    rng = np.random.default_rng(3)
    cases = [np.zeros((400, 600, 3), np.uint8), np.full((400, 600, 3), 128, np.uint8),
             rng.integers(0, 255, (400, 600, 3), dtype=np.uint8),
             np.full((400, 600, 3), (255, 0, 0), np.uint8)]
    near = np.full((400, 600, 3), 90, np.uint8)
    near[::97, ::89] = 255                                    # détails épars : le sous-échantillon les rate
    cases.append(near)
    faint = np.full((400, 600, 3), 90, np.uint8) + rng.integers(0, 3, (400, 600, 3), dtype=np.uint8)
    cases.append(faint)
    for pixels in cases:
        assert capture.is_uniform(pixels) == legacy_uniform(pixels)


def test_capture_reports_sub_step_timings(monkeypatch) -> None:
    client = ClientRect(10, 20, 64, 48)
    image = Image.fromarray(np.random.default_rng(1).integers(0, 255, (48, 64, 3), dtype=np.uint8))
    monkeypatch.setattr(capture, "client_geometry", lambda hwnd: client)
    monkeypatch.setattr(capture.ImageGrab, "grab", lambda **kwargs: image)
    frame = capture.capture_client(7, activate=False)
    assert frame.source == "window" and frame.image.shape == (48, 64, 3)
    assert {"activate", "geometry", "grab_window", "to_rgb", "to_bgr", "checks"} <= set(frame.timings_ms)
    assert frame.image[0, 0].tolist() == np.asarray(image)[0, 0][::-1].tolist()      # RGB → BGR inchangé


def test_post_grab_processing_is_faster_than_the_previous_double_pass(monkeypatch) -> None:
    height, width = 1377, 2560                                  # taille client mesurée en 3B-7
    image = Image.fromarray(np.random.default_rng(2).integers(0, 255, (height, width, 3), dtype=np.uint8))
    client = ClientRect(0, 0, width, height)
    monkeypatch.setattr(capture, "client_geometry", lambda hwnd: client)
    monkeypatch.setattr(capture.ImageGrab, "grab", lambda **kwargs: image)

    def previous() -> None:                                    # ancienne chaîne, recopiée pour mesure
        float(np.asarray(image.convert("RGB")).std())
        converted = np.asarray(image.convert("RGB"))[:, :, ::-1].copy()
        float(converted.std())

    def timed(function) -> float:
        best = float("inf")
        for _ in range(3):
            started = time.perf_counter()
            function()
            best = min(best, time.perf_counter() - started)
        return best

    assert timed(lambda: capture.capture_client(1, activate=False)) < timed(previous)
