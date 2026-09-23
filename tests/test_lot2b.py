from pathlib import Path

import cv2
import numpy as np
import pytest

from combatbot.storage import Storage
from combatbot.vision import connection
from combatbot.vision.autocalibration import ZoneSuggestion, suggest_zones, zones_needing_review
from combatbot.vision.icons import KnownIcon, infer_grid_shape, visual_hash
from combatbot.vision.models import (
    Calibration, CapturedFrame, ClientRect, ConnectionResult, Profile, RelativeRect,
    WindowInfo, ZoneEvidence,
)
from combatbot.vision.tooltip import locate_new_tooltip
from combatbot.vision.coordinates import ClientSize, LayoutSignature


def textured_frame(*, source="window", activated=True, left=0) -> CapturedFrame:
    rng = np.random.default_rng(45)
    image = rng.integers(30, 220, (240, 360, 3), dtype=np.uint8)
    return CapturedFrame(101, ClientRect(left, 0, 360, 240), image, activated, source)


def test_connection_requires_real_capture_and_reports_uncertainty(monkeypatch) -> None:
    frame = textured_frame()
    monkeypatch.setattr(connection, "inspect_dofus_window",
                        lambda hwnd: (WindowInfo(hwnd, "DOFUS test", False), frame.client))
    monkeypatch.setattr(connection, "capture_client", lambda hwnd: frame)
    monkeypatch.setattr(connection, "suggest_zones", lambda frame: {})
    result = connection.connect_window(101)
    assert result.success and result.frame is frame
    assert result.code == "CONTENT_UNCERTAIN"
    assert "contenu à vérifier" in result.message
    assert result.details["width"] == 360

    black = CapturedFrame(101, frame.client, np.zeros_like(frame.image))
    monkeypatch.setattr(connection, "capture_client", lambda hwnd: black)
    result = connection.connect_window(101)
    assert not result.success and result.code == "CAPTURE_BLACK"


@pytest.mark.parametrize("message,code", [
    ("Fenêtre fermée ou HWND invalide", "WINDOW_CLOSED"),
    ("Le client DOFUS est minimisé", "WINDOW_MINIMIZED"),
    ("Le HWND ne désigne plus une fenêtre DOFUS visible", "WINDOW_WRONG_TARGET"),
])
def test_connection_window_failures(monkeypatch, message, code) -> None:
    def failed(_hwnd):
        raise RuntimeError(message)
    monkeypatch.setattr(connection, "inspect_dofus_window", failed)
    result = connection.connect_window(404)
    assert not result.success and result.code == code and result.step == "sélection"


def test_desktop_capture_after_activation_failure_is_not_declared_verified() -> None:
    assessment = connection.assess_capture(textured_frame(source="desktop", activated=False))
    assert assessment.usable and not assessment.content_verified
    assert "recouvrement possible" in assessment.message


def test_confirmed_visual_icons_can_verify_content() -> None:
    frame = textured_frame()
    icon_a = frame.image[20:55, 20:55].copy()
    icon_b = frame.image[100:135, 160:195].copy()
    known = []
    for number, icon in enumerate((icon_a, icon_b), 1):
        ok, png = cv2.imencode(".png", icon)
        assert ok
        known.append(KnownIcon(number, f"Sort {number}", png.tobytes(), visual_hash(icon)))
    assessment = connection.assess_capture(frame, known)
    assert assessment.usable and assessment.content_verified
    assert assessment.details["confirmed_icon_matches"] == 2


def test_partial_calibration_persists_and_requires_confirmation_for_scan(tmp_path) -> None:
    store = Storage(tmp_path / "partial.sqlite3")
    profile_id = store.save_profile(Profile(None, "Profil partiel"))
    calibration = Calibration(profile_id, 360, 240,
                              {"combat": RelativeRect(0.1, 0.1, 0.7, 0.6),
                               "spell_bar": RelativeRect(0.1, 0.8, 0.7, 0.1)},
                              {"combat": ZoneEvidence(0.3, "contours", "confirmée"),
                               "spell_bar": ZoneEvidence(0.5, "contours", "proposée")}, "layout-1")
    store.save_calibration(calibration)
    loaded = store.load_calibration(profile_id)
    assert loaded is not None and loaded.layout_signature == "layout-1"
    assert len(loaded.zones) == 2
    frame = textured_frame()
    with pytest.raises(ValueError, match="à confirmer"):
        loaded.crop(frame, "spell_bar")
    assert loaded.crop(frame, "combat").size > 0
    with pytest.raises(ValueError, match="non calibrée"):
        loaded.crop(frame, "hp")
    store.close()


def test_reuse_and_revalidate_when_layout_moves_or_size_changes() -> None:
    frame = textured_frame(left=300)
    zones = {"spell_bar": RelativeRect(0.1, 0.7, 0.5, 0.2)}
    signature = LayoutSignature.create(
        ClientSize(360, 240), {name: rect.to_normalized_rect() for name, rect in zones.items()},
    ).to_json()
    calibration = Calibration(1, 360, 240, zones, layout_signature=signature)
    original = ZoneSuggestion((36, 168, 180, 48), ZoneEvidence(0.9, "template", "détectée"))
    moved = ZoneSuggestion((160, 100, 180, 48), ZoneEvidence(0.9, "template", "détectée"))
    assert zones_needing_review(calibration, frame, {"spell_bar": original}) == set()
    assert zones_needing_review(calibration, frame, {"spell_bar": moved}) == {"spell_bar"}
    resized = CapturedFrame(101, ClientRect(300, 0, 400, 240), np.zeros((240, 400, 3), np.uint8))
    assert zones_needing_review(calibration, resized, {}) == {"spell_bar"}


def test_legacy_layout_requires_revalidation_when_needed() -> None:
    frame = textured_frame()
    calibration = Calibration(1, 360, 240, {"combat": RelativeRect(.1, .1, .8, .7)},
                              layout_signature="historical-hash")
    status = calibration.layout_compatibility(360, 240)
    assert status.compatible and status.requires_revalidation
    assert zones_needing_review(calibration, frame, {}) == {"combat"}
    assert "combat" in calibration.zones


def test_user_spellbar_template_and_grid_shape(monkeypatch) -> None:
    image = cv2.imread(str(Path(__file__).parent / "fixtures" / "user_spellbar.png"))
    assert image is not None
    monkeypatch.setattr("combatbot.vision.tooltip._ocr_engine", lambda: lambda image: object())
    frame = CapturedFrame(1, ClientRect(0, 0, 618, 121), image)
    suggestions = suggest_zones(frame)
    assert suggestions["spell_bar"].rect == (0, 0, 575, 121)
    assert suggestions["spell_bar"].evidence.status == "détectée"
    assert infer_grid_shape(image[:, :575])[:2] == (10, 2)
    assert "hp" not in suggestions


def test_tooltip_diff_finds_new_panel_and_rejects_no_change() -> None:
    before = np.full((300, 420, 3), 45, np.uint8)
    after = before.copy()
    cv2.rectangle(after, (180, 55), (345, 180), (220, 225, 220), -1)
    cv2.putText(after, "3 PA", (195, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (10, 10, 10), 2)
    rect = locate_new_tooltip(before, after)
    assert rect is not None
    x, y, width, height = rect
    assert x <= 180 and y <= 55 and x + width >= 345 and y + height >= 180
    assert locate_new_tooltip(before, before) is None
