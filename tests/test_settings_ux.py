"""Paramètres : calibration lisible, parcours guidé, fenêtres DOFUS réelles uniquement."""
from __future__ import annotations

import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from combatbot.storage import Storage
from combatbot.ui import client_panel as client_panel_module
from combatbot.ui.calibration_dialog import CalibrationDialog, with_note
from combatbot.vision import window
from combatbot.vision.autocalibration import suggest_zones
from combatbot.vision.models import (
    Calibration, CapturedFrame, ClientRect, ConnectionResult, RelativeRect, ZoneEvidence,
)


def _app():
    return QApplication.instance() or QApplication([])


def _frame(width=640, height=360):
    return CapturedFrame(1, ClientRect(0, 0, width, height), np.full((height, width, 3), 60, np.uint8))


def test_provenance_never_grows_with_edits() -> None:
    bloated = "zone de recherche centrale" + " ; ajustée manuellement" * 50
    assert with_note(bloated, "ajustée manuellement") == "zone de recherche centrale ; ajustée manuellement"
    assert with_note("OCR du libellé visible ; vérification humaine", "ajustée manuellement") == \
        "OCR du libellé visible ; ajustée manuellement"


def test_calibration_dialog_states_and_repeated_edits(monkeypatch) -> None:
    _app()
    monkeypatch.setattr("combatbot.vision.tooltip._ocr_engine", lambda: lambda image: object())
    frame = _frame()
    dialog = CalibrationDialog(frame, 1, None, None, suggest_zones(frame))
    assert set(dialog.items) == {"combat"}
    assert dialog._state("combat")[1] == "À vérifier" and dialog._state("hp")[1] == "À placer"
    for _ in range(30):
        dialog._edited("combat")
    assert dialog.evidence["combat"].method.count("ajustée manuellement") == 1
    assert dialog._state("combat")[1] == "Validée"
    dialog._row_action("hp")                        # placer puis valider
    assert "hp" in dialog.items and dialog._state("hp")[1] == "À vérifier"
    dialog._row_action("hp")
    assert dialog._state("hp")[1] == "Validée"
    assert "2 / 5" in dialog.progress.text()
    calibration = dialog.calibration()
    assert set(calibration.zones) == {"combat", "hp"}
    dialog.close()


def test_existing_bloated_calibration_is_cleaned_on_open() -> None:
    _app()
    frame = _frame()
    meta = {"combat": ZoneEvidence(0.2, "centrale" + " ; ajustée manuellement" * 40, "confirmée")}
    existing = Calibration(1, 640, 360, {"combat": RelativeRect(0, 0, 1, 0.8)}, meta, None)
    dialog = CalibrationDialog(frame, 1, existing, None, {})
    assert len(dialog.evidence["combat"].method) < 80
    dialog.close()


def test_combat_suggestion_spans_width_above_hud(monkeypatch) -> None:
    monkeypatch.setattr("combatbot.vision.tooltip._ocr_engine", lambda: lambda image: object())
    suggestion = suggest_zones(_frame(2560, 1377))["combat"]
    assert suggestion.rect == (0, 0, 2560, round(1377 * 0.838))


def test_client_panel_guides_next_step(tmp_path, monkeypatch) -> None:
    _app()
    monkeypatch.setattr(client_panel_module, "list_dofus_windows", lambda: [])
    storage = Storage(tmp_path / "ui.sqlite3")
    panel = client_panel_module.ClientPanel(storage)
    assert "Lancez DOFUS" in panel.next_step.text()
    assert not panel.connect_button.isEnabled() and not panel.recognize_button.isEnabled()
    panel.windows.addItem("Perso - Dofus 2.64", 42)
    assert "Connecter" in panel.next_step.text() and panel.connect_button.isEnabled()
    result = ConnectionResult("capture", True, "CONTENT_UNCERTAIN", "à vérifier",
                              {"hwnd": 42, "width": 640, "height": 360}, _frame())
    panel.set_connection_result(result)
    assert panel.confirm_capture_button.isEnabled() and "aperçu" in panel.next_step.text()
    panel._confirm_capture()
    assert not panel.confirm_capture_button.isEnabled()      # plus de double confirmation
    assert "Calibrer" in panel.next_step.text() and panel.calibrate_button.isEnabled()
    storage.save_calibration(Calibration(panel.profile_id, 640, 360,
                                         {z: RelativeRect(0.1, 0.1, 0.1, 0.1) for z in ("hp", "ap", "mp")},
                                         {z: ZoneEvidence(1, "test", "confirmée") for z in ("hp", "ap", "mp")}))
    panel.set_calibration_state("ok", "Calibration compatible")
    assert panel.next_step.text().startswith("✓ Prêt") and panel.recognize_button.isEnabled()
    assert [chip.property("state") for chip in panel.step_chips] == ["done", "done", "done", "done"]
    panel.close()
    storage.close()


def test_browser_titled_dofus_is_not_a_game_window(monkeypatch) -> None:
    class User32:
        def EnumWindows(self, callback, lparam):
            for hwnd in (1, 2, 3):
                callback(hwnd, 0)
            return True

        def IsWindowVisible(self, hwnd):
            return True

        def IsIconic(self, hwnd):
            return False

    monkeypatch.setattr(window, "_user32", lambda: User32())
    monkeypatch.setattr(window, "_title", lambda hwnd: {1: "Perso - Dofus 2.64.5.0",
                                                        2: "Guide Dofus - Google Chrome", 3: "DOFUS"}[hwnd])
    monkeypatch.setattr(window, "_process_name", lambda hwnd: {1: "dofus.exe", 2: "chrome.exe", 3: None}[hwnd])
    assert [item.hwnd for item in window.list_dofus_windows()] == [1, 3]


def test_stacked_heart_hp_is_read_top_current_bottom_max() -> None:
    from combatbot.vision.character import parse_hp
    assert parse_hp("3516\n3585") == (3516, 3585)        # cœur du HUD : actuels en haut, max en bas
    assert parse_hp("3516/3585") == (3516, 3585)
    assert parse_hp("3585\n3516") is None                 # incohérent : jamais deviné
    assert parse_hp("3516") is None and parse_hp("") is None


def test_resize_handle_stays_small_on_small_zones() -> None:
    from PySide6.QtCore import QRectF
    from combatbot.ui.calibration_dialog import ResizableRectItem
    _app()
    item = ResizableRectItem("PA", "#8eaaf0", QRectF(0, 0, 60, 45))
    handle = item._handle_rect()
    assert handle.width() <= 15 and handle.center() == item.rect().bottomRight()
