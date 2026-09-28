"""Application DofBot2 : onglets, accordéons, session, alertes, sorts et réglages."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QBuffer, QDeadlineTimer, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from combatbot.combat.safety import GLOBAL_EMERGENCY_STOP
from combatbot.storage import Storage
from combatbot.ui.dofbot2 import system, theme
from combatbot.ui.dofbot2.controls import Accordion, Choices, Stepper, Switch
from combatbot.ui.dofbot2.pages import relative_time
from combatbot.ui.dofbot2.profiles import create_profile
from combatbot.ui.dofbot2.settings import (
    DUNGEONS, ZONES, JsonSettings, SpellBinding, SpellConfig, current_plan, plan_summary, profile_settings,
)
from combatbot.ui.dofbot2.shell import DofBot2Window
from combatbot.vision.models import IconCandidate


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    theme.load_fonts()
    return application


@pytest.fixture
def storage(tmp_path):
    store = Storage(tmp_path / "dofbot2-app.sqlite3")
    yield store
    store.close()


def _wait_for(condition, timeout_ms: int = 2000) -> bool:
    deadline = QDeadlineTimer(timeout_ms)
    while not condition() and not deadline.hasExpired():
        QTest.qWait(20)
    return condition()


def _icon() -> bytes:
    image = QImage(52, 52, QImage.Format.Format_RGB32)
    image.fill(QColor("#b27ae0"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def _add_spells(storage, profile_id: int, count: int = 3) -> list[int]:
    ids = []
    for slot in range(1, count + 1):
        spell_id = storage.save_scan_candidate(profile_id, IconCandidate(1, slot, _icon(), f"hash{slot}", .9, .9, "Inconnu"))
        storage.save_profile_spell_fields(spell_id, {"ap_cost": slot + 1, "min_range": 1, "max_range": 5})
        ids.append(spell_id)
    return ids


@pytest.fixture
def window(app, storage, monkeypatch):
    monkeypatch.setattr(DofBot2Window, "_connect_legacy", lambda self, hwnd: None)
    monkeypatch.setattr(system, "play_alert_sound", lambda: None)
    monkeypatch.setattr("combatbot.ui.dofbot2.app.play_alert_sound", lambda: None)
    kira = create_profile(storage, "Kira", 0, "Cra")
    win = DofBot2Window(storage, start_screen="profile")
    win.show()
    win.enter_app(kira.id)
    yield win
    win.close()


def test_settings_are_per_profile_and_plan_summary(storage) -> None:
    kira = create_profile(storage, "Kira", 0)
    iop = create_profile(storage, "Iop", 1)
    kira_settings, iop_settings = profile_settings(storage, kira.id), profile_settings(storage, iop.id)
    kira_settings.set("runs", 0)
    kira_settings.set("bossFirst", False)
    assert iop_settings.get("runs", 10) == 10
    assert plan_summary(kira_settings, 20) == "Donjon · ∞ runs · 20 sorts actifs"
    assert plan_summary(iop_settings, None) == "Donjon · 10 runs · boss en priorité · aucun sort détecté"
    iop_settings.set("plan_mode", "zone")
    iop_settings.set("zn", 2)
    iop_settings.set("path", "Aléatoire")
    assert current_plan(iop_settings).place == ZONES[2]
    assert plan_summary(iop_settings, 1) == "Zone · aléatoire · 1 sort actif"
    iop_settings.set("zn", 99)   # valeur corrompue : on retombe sur la première zone
    assert current_plan(iop_settings).place == ZONES[0]
    app_wide = JsonSettings(storage, "dofbot2_app")
    app_wide.set("tray", False)
    assert storage.get_setting("dofbot2_app") == {"tray": False}


def test_spell_binding_keeps_confirmed_status_and_range_order(storage) -> None:
    kira = create_profile(storage, "Kira", 0)
    spell_id = _add_spells(storage, kira.id, 1)[0]
    storage.save_profile_spell_fields(spell_id, {
        "name": "Flèche", "modifiable_range": 1, "line_cast": 0, "line_of_sight": 1, "per_turn": 2, "per_target": 1,
    }, confirm=True)
    binding = SpellBinding(storage, SpellConfig(storage, kira.id), spell_id, slot_priority=4)
    binding.set("min_range", 8)   # au-delà de la portée max : la max suit
    row = storage.get_profile_spell(spell_id)
    assert (row["min_range"], row["max_range"], row["status"]) == (8, 8, "Confirmé")
    binding.set("line_cast", True)
    assert binding.get("line_cast") is True and storage.get_profile_spell(spell_id)["line_cast"] == 1
    assert binding.get("priority") == 4
    binding.set("target", "Allié")
    assert SpellConfig(storage, kira.id).get(spell_id, "target") == "Allié"


def test_controls(app) -> None:
    switch = Switch(False)
    switch.click()
    assert switch.isChecked()
    switch.set_state(False)
    assert not switch.isChecked()
    values = []
    stepper = Stepper(95, 50, 100, 5, lambda value: f"{value} %")
    stepper.value_changed.connect(values.append)
    stepper.plus.click()
    stepper.plus.click()   # borné à 100
    assert values == [100] and stepper.display.text() == "100 %" and not stepper.plus.isEnabled()
    unknown = Stepper(None, 1, 12)
    assert unknown.display.text() == "—"
    unknown.plus.click()
    assert unknown.value() == 1
    single = Choices(("Boucle", "Aléatoire"), "Boucle")
    single.buttons["Aléatoire"].click()
    assert single.value() == "Aléatoire" and not single.buttons["Boucle"].isChecked()
    multi = Choices(("Paysan", "Mineur"), ["Paysan"], multi=True)
    multi.buttons["Mineur"].click()
    multi.buttons["Paysan"].click()
    assert multi.value() == ["Mineur"]
    accordion = Accordion("Sécurité", "Pauses", [], opened=False)
    accordion.set_open(True, animate=False)
    assert accordion.is_open and accordion.property("open") is True


def test_relative_time() -> None:
    now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
    assert relative_time(now - timedelta(seconds=20), now) == "à l'instant"
    assert relative_time(now - timedelta(minutes=2), now) == "il y a 2 min"
    assert relative_time(now - timedelta(hours=3), now) == "il y a 3 h"
    assert relative_time(now - timedelta(days=2), now) == "il y a 2 jours"


def test_dock_navigation_and_crumb(window) -> None:
    view = window.app_view
    assert view.current_tab == "home" and window.title_bar.crumb.text() == "· Accueil"
    view.dock.bubbles["sorts"].click()
    assert view.current_tab == "sorts" and window.title_bar.crumb.text() == "· Sorts"
    assert view.stack.currentWidget() is view.pages["sorts"]
    assert _wait_for(lambda: view.dock.bubbles["sorts"].width() == view.dock.bubbles["sorts"].full_width
                     and view.dock.bubbles["home"].width() == 48)


def test_schedule_a_zone_and_session(window, storage) -> None:
    view = window.app_view
    view.go_tab("zone")
    zones = view.pages["zone"]
    zones.cards[1].click()
    monsters = zones.accordions["znMob"].rows()[0].control
    assert list(monsters.buttons) == list(ZONES[1].monsters)   # les monstres suivent la zone choisie
    zones.schedule_button.click()
    assert zones.schedule_button.text() == "Programmé ✓"
    view.go_tab("home")
    home = view.pages["home"]
    assert home.plan_name.text() == ZONES[1].name and home.plan_sub.text().startswith("Zone · boucle")
    home.run_button.click()
    assert view.running and view.status_text.text() == "En cours" and view.run_button.text() == "Arrêter"
    view.emergency_stop()
    assert not view.running and view.status_text.text() == "En pause"
    assert GLOBAL_EMERGENCY_STOP.triggered          # F9 verrouille toute future action
    home.run_button.click()                          # seul un démarrage explicite réarme
    assert view.running and not GLOBAL_EMERGENCY_STOP.triggered
    view.emergency_stop()
    journal = [message for _time, message, _color in view.journal(7)]
    assert journal[0] == "Arrêt d'urgence (F9)"
    assert f"Session démarrée · {ZONES[1].name}" in journal
    assert f"Activité programmée · {ZONES[1].name}" in journal
    # Le journal est propre au profil.
    other = create_profile(storage, "Brakmar", 3)
    window.enter_app(other.id)
    assert view.journal(7) == []


def test_dungeon_options_change_the_home_summary(window) -> None:
    view = window.app_view
    view.go_tab("donjon")
    dungeons = view.pages["donjon"]
    runs = dungeons.accordions["djRuns"].rows()[0]
    runs.control.setValue(1)
    runs.control.minus.click()
    assert view.settings.get("runs") == 0
    # Le panneau principal ne montre que l'historique ; la sélection complète vient du catalogue.
    dungeons.select(3)
    view.go_tab("home")
    assert view.pages["home"].plan_name.text() == DUNGEONS[3].name
    assert "∞ runs" in view.pages["home"].plan_sub.text()


def test_started_dungeon_is_added_to_recent_list(window) -> None:
    view = window.app_view
    dungeons = view.pages["donjon"]
    assert dungeons.cards == []
    dungeons.select(2)
    assert dungeons.cards == []  # choisir ne compte pas encore comme un farm
    dungeons.schedule()
    view.start()
    assert [card.place.place_id for card in dungeons.cards] == [DUNGEONS[2].place_id]


def test_alert_filters_badge_and_discord(window, monkeypatch) -> None:
    view = window.app_view
    sent = []
    monkeypatch.setattr("combatbot.ui.dofbot2.app.send_discord", lambda url, title, body: sent.append((url, title)))
    view.go_tab("notifs")
    view.settings.set("nfEnd", False)
    assert not view.notify("Session arrêtée", "…", kind="nfEnd")
    assert view.notify("Boss vaincu", "Bouftou Royal", kind="nfBoss")
    assert view.dock.bubbles["home"].badge
    view.settings.set("nfDiscord", True)
    view.settings.set("webhook", "https://discord.com/api/webhooks/1/abc")
    assert view.notify("Run terminé", "1/10", kind="nfRun")
    assert _wait_for(lambda: sent == [("https://discord.com/api/webhooks/1/abc", "Run terminé")])
    view.go_tab("home")
    assert not view.dock.bubbles["home"].badge


def test_webhook_validation() -> None:
    assert system.webhook_problem("https://discord.com/api/webhooks/1/abc") is None
    assert system.webhook_problem("http://discord.com/api/webhooks/1/abc")
    assert system.webhook_problem("https://evil.example/api/webhooks/1")
    with pytest.raises(ValueError):
        system.send_discord("https://evil.example/api/webhooks/1", "t", "b")


def test_toast_stack_keeps_three(app) -> None:
    from combatbot.ui.dofbot2.controls import ToastStack
    stack = ToastStack()
    for index in range(5):
        stack.push(f"Alerte {index}", "…", duration_ms=0)
    assert [toast.title.text() for toast in stack.toasts] == ["Alerte 2", "Alerte 3", "Alerte 4"]
    stack.toasts[0].dismiss()
    assert len(stack.toasts) == 2


def test_spells_tab_edits_detected_spells(window, storage) -> None:
    view = window.app_view
    spell_ids = _add_spells(storage, view.profile_id, 3)
    view.go_tab("sorts")
    page = view.pages["sorts"]
    assert len(page.tiles) == 3 and page.selected_id == spell_ids[0]
    assert "3 sorts détectés" in page.banner.text()
    page.tiles[1].click()
    assert page.selected_id == spell_ids[1] and page.spell_name.text() == "Sort 2"
    cost = page.accordions["spCost"].rows()[0].control
    cost.plus.click()
    assert storage.get_profile_spell(spell_ids[1])["ap_cost"] == 4 and page.tiles[1].ap_cost == 4
    page.use_switch.click()
    assert not view.spell_config.in_use(spell_ids[1]) and "désactivé" in page.spell_meta.text()
    assert view.active_spell_count() == 2
    view.spells_detected(3)
    assert "à l'instant" in page.banner.text()


def test_redetect_requires_a_connected_window(window, monkeypatch) -> None:
    view = window.app_view
    shown = []
    monkeypatch.setattr(view, "notify", lambda title, body, *args, **kwargs: shown.append(title))
    view.redetect_spells()
    assert shown == ["Fenêtre non connectée"]
    calls = []
    window.legacy.client_panel.connected_hwnd = 42
    monkeypatch.setattr(window.legacy.scan_panel, "request_scan", lambda: calls.append(True))
    view.redetect_spells()
    assert calls == [True]
    window.legacy.client_panel.connected_hwnd = None


def test_settings_tab_links(window) -> None:
    view = window.app_view
    view.go_tab("params")
    connection = view.pages["params"].accordions["stConn"].rows()
    change_window = connection[0].control.layout().itemAt(1).widget()
    change_window.click()
    assert window.current_screen == "connect"
    window.show_screen("app")
    view.go_tab("params")
    view.pages["params"].accordions["stAdvanced"].rows()[0].control.layout().itemAt(1).widget().click()
    assert window.current_screen == "advanced"
    assert window.title_bar.crumb.text().startswith("· Outils avancés")
    window.show_screen("app")
    assert window.current_screen == "app"


def test_autostart_failure_resets_the_switch(window, monkeypatch) -> None:
    view = window.app_view
    def refuse(_enabled):
        raise RuntimeError("Le lancement avec Windows n'est disponible que sous Windows")
    monkeypatch.setattr("combatbot.ui.dofbot2.app.set_autostart", refuse)
    view.go_tab("params")
    view.set_autostart(True)
    assert view.app_settings.get("boot") is False


def test_choosing_another_window_keeps_the_profile(window) -> None:
    from combatbot.ui.dofbot2.game_windows import GameWindow
    profile_id = window.profile_id
    window._window_chosen(GameWindow(7, "Dofus · Kira", "Kira", "1280×720", "Kira - Cra"))
    assert window.current_screen == "app" and window.profile_id == profile_id
    assert window.app_view.window_label() == "Dofus · Kira"
