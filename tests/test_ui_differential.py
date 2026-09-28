"""UI avancée : mises à jour en place, sans reconstruction ni réécriture quand rien ne change."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel

from combatbot.performance.health import SyntheticObserver
from combatbot.storage import Storage
from combatbot.ui.dofbot2 import theme
from combatbot.ui.dofbot2.controls import set_prop, set_style, set_text
from combatbot.ui.dofbot2.profiles import create_profile
from combatbot.ui.dofbot2.shell import DofBot2Window


@pytest.fixture
def advanced(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    theme.load_fonts()
    monkeypatch.setenv("PYTHONBOT_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(DofBot2Window, "_connect_legacy", lambda self, hwnd: None)
    storage = Storage(tmp_path / "ui.sqlite3")
    profile = create_profile(storage, "Kira", 0, "Cra")
    window = DofBot2Window(storage, start_screen="profile")
    window.show()
    window.enter_app(profile.id)
    window.open_advanced()
    window.advanced_view.set_recheck(False)
    window.advanced_view.go_tab("observation", animate=False)
    yield app, window
    window.close()
    storage.close()


def test_map_changes_update_in_place_without_rebuilding(advanced) -> None:
    app, window = advanced
    view, legacy = window.advanced_view, window.legacy
    page = view.pages["observation"]
    observer = SyntheticObserver(0.0)
    packets = [observer.observe() for _ in range(9)]            # transition, ambiguë, résolue…
    legacy.combat.set_observation(packets[0])
    view.live_update()
    built = page.accordion_rebuild_count
    for packet in packets[1:]:
        legacy.combat.set_observation(packet)
        view.live_update()
        app.processEvents()
        assert page.map_value.text() == (legacy.combat.real_values["map"].text() or "inconnue")
        coords = legacy.combat.real_values["map_coords"].text()
        assert page.accordions["map"].header.subtitle.endswith(coords) or coords in ("", "—", "Inconnu")
    assert page.accordion_rebuild_count == built                  # avant : une reconstruction par changement


def test_unchanged_values_are_not_rewritten(advanced, monkeypatch) -> None:
    app, window = advanced
    view = window.advanced_view
    view.live_update()
    calls = {"text": 0, "style": 0}
    original_text, original_style = QLabel.setText, QLabel.setStyleSheet

    def counting_text(self, text):
        calls["text"] += 1
        original_text(self, text)

    def counting_style(self, style):
        calls["style"] += 1
        original_style(self, style)

    monkeypatch.setattr(QLabel, "setText", counting_text)
    monkeypatch.setattr(QLabel, "setStyleSheet", counting_style)
    for _ in range(5):
        view.live_update()
    assert calls == {"text": 0, "style": 0}


def test_differential_setters() -> None:
    QApplication.instance() or QApplication([])
    label = QLabel("a")
    assert not set_text(label, "a") and set_text(label, "b") and label.text() == "b"
    assert set_style(label, "color: red;") and not set_style(label, "color: red;")
    assert set_prop(label, "running", True) and not set_prop(label, "running", True)
