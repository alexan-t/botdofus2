"""Outils avancés DofBot2 : six onglets pilotant l'interface historique, outils en écran A7."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QVBoxLayout

from combatbot.models import CombatEvent, StrategyMode, TargetPriority
from combatbot.storage import Storage
from combatbot.ui import tool_host
from combatbot.ui.dofbot2 import theme
from combatbot.ui.dofbot2.advanced import StrategyBinding, WidgetBinding, value_color
from combatbot.ui.dofbot2.profiles import create_profile
from combatbot.ui.dofbot2.shell import DofBot2Window
from combatbot.vision.models import CapturedFrame, ClientRect, IconCandidate
from tests.test_corpus import make_debug


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    theme.load_fonts()
    return application


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONBOT_DATA_DIR", str(tmp_path / "appdata"))   # corpus isolé
    store = Storage(tmp_path / "advanced.sqlite3")
    yield store
    store.close()


@pytest.fixture
def window(app, storage, monkeypatch):
    monkeypatch.setattr(DofBot2Window, "_connect_legacy", lambda self, hwnd: None)
    kira = create_profile(storage, "Kira", 0, "Cra")
    win = DofBot2Window(storage, start_screen="profile")
    win.show()
    win.enter_app(kira.id)
    win.open_advanced()
    win.advanced_view.set_recheck(False)   # pas de vraie fenêtre DOFUS sous test
    yield win
    win.close()


def _icon(color: str) -> bytes:
    image = QImage(52, 52, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def _fake_connection(window, monkeypatch, confirmed: bool = False) -> None:
    """Fenêtre simulée : la revérification (après confirmation) ne doit pas la détacher sous Linux."""
    from combatbot.vision.models import WindowInfo
    monkeypatch.setattr("combatbot.ui.main_window.inspect_dofus_window",
                        lambda hwnd: (WindowInfo(hwnd, "Kira - Dofus 3.2", False), ClientRect(0, 0, 800, 600)))
    panel = window.legacy.client_panel
    panel.connected_hwnd = 0x4A2
    panel.frame = CapturedFrame(0x4A2, ClientRect(0, 0, 800, 600), np.full((600, 800, 3), 40, np.uint8))
    panel.window_title.setText("Kira - Dofus 3.2")
    panel.content_confirmed = confirmed


def test_tool_host_without_presenter_keeps_classic_windows(app) -> None:
    tool_host.set_presenter(None)
    assert tool_host.present_tool(QDialog(), "hud") is False
    with pytest.raises(KeyError):
        tool_host.present_tool(QDialog(), "inconnu")


def test_advanced_navigation_and_back(window) -> None:
    view = window.advanced_view
    assert window.current_screen == "advanced"
    assert window.title_bar.crumb.text() == "· Outils avancés · Connexion"
    assert window.legacy.isHidden()   # l'interface historique n'est plus affichée, elle pilote
    view.dock.bubbles["journal"].click()
    assert view.current_tab == "journal" and window.title_bar.crumb.text() == "· Outils avancés · Journal"
    view.back_requested.emit()
    assert window.current_screen == "app"
    window.open_advanced()
    assert window.current_screen == "advanced" and view.current_tab == "journal"


def test_tool_opens_in_the_window_with_header(window) -> None:
    dialog = QDialog(window.legacy)
    QVBoxLayout(dialog).addWidget(QLabel("outil"))
    assert tool_host.present_tool(dialog, "phase")
    dialog.show()
    QTest.qWait(50)
    assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert dialog.geometry() == window.tool_area()
    assert dialog.layout().contentsMargins().top() >= 60
    assert window.title_bar.crumb.text() == "· Outils avancés · Phase et tour"
    header = dialog.findChild(QLabel, "d2ToolTitle")
    assert header is not None and header.text() == "Phase et tour"
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    assert window.title_bar.crumb.text() == "· Outils avancés · Connexion"


def test_connection_steps_drive_the_legacy_panel(window, monkeypatch) -> None:
    view = window.advanced_view
    page = view.pages["connexion"]
    page.live_update()
    assert page.primary.text() == "Connecter la fenêtre" and page.capture_value.text() == "non connectée"
    requested = []
    view.window_change_requested.connect(lambda: requested.append(True))
    page.secondary.click()
    assert requested == [True]
    _fake_connection(window, monkeypatch)
    view.live_update()
    assert page.question.text() == "L'aperçu montre-t-il bien le jeu ?"
    assert page.window_title.text() == "Kira - Dofus 3.2" and "hwnd 0x0004A2" in page.window_meta.text()
    assert page.preview.image is not None and view.status_text.text() == "Capture à confirmer"
    page.primary.click()   # « Oui, c'est DOFUS »
    assert window.legacy.client_panel.content_confirmed
    page.live_update()
    assert page.primary.text() == "Calibrer les zones" and page.capture_value.text() == "validée"
    calibrations = []
    monkeypatch.setattr(window.legacy, "_calibrate", lambda: calibrations.append(True))
    page.primary.click()
    assert calibrations == [True]


def test_auto_confirm_switch_is_honoured(window) -> None:
    legacy = window.legacy
    profile_id = legacy.client_panel.profile_id
    details = {"title": "Kira - Dofus", "width": 800, "height": 600}
    window.storage.set_profile_setting(profile_id, "confirmed_window", details)
    assert legacy._window_already_confirmed(profile_id, details)
    window.storage.set_profile_setting(profile_id, "dofbot2_advanced", {"auto_confirm_window": False})
    assert not legacy._window_already_confirmed(profile_id, details)


def test_observation_page_mirrors_the_combat_page(window) -> None:
    view = window.advanced_view
    combat = window.legacy.combat
    view.go_tab("observation")
    page = view.pages["observation"]
    assert combat.mode.currentText() == "Vision réelle"
    assert combat.observation_preview.parentWidget() is page.canvas
    page.overlays.buttons["Cell IDs"].click()
    assert combat.overlay_boxes["cell_ids"].isChecked()
    combat.real_values["ap"].setText("6")
    combat.real_values["combat"].setText("Oui")
    page.live_update()
    assert page.signal_values["ap"].text() == "6" and page.signal_values["combat"].text() == "Oui"
    verified = page.accordions["map"].rows()[3].control
    verified.click()
    assert combat.map_id_verified.isChecked()
    assert page.toggle.text() == "Démarrer l'observation" and not page.save.isEnabled()


def test_spell_scan_page(window, storage) -> None:
    view = window.advanced_view
    profile_id = window.legacy.client_panel.profile_id
    first = storage.save_scan_candidate(profile_id, IconCandidate(1, 1, _icon("#b27ae0"), "a", .9, .9, "Reconnu"))
    storage.save_scan_candidate(profile_id, IconCandidate(1, 2, _icon("#5cc4d6"), "b", .9, .4, "Inconnu"))
    view.go_tab("sorts")
    page = view.pages["sorts"]
    scan = window.legacy.scan_panel
    assert len(page.tiles) == scan.rows.value() * scan.columns.value()
    assert "1 sort reconnu · 1 à vérifier" in page.summary.text()
    assert page.tiles[1].pending and page.tiles[2].spell_id is None
    page.columns.plus.click()
    assert scan.columns.value() == 11
    page.live_update()
    assert len(page.tiles) == scan.rows.value() * 11
    page.tiles[0].click()
    assert scan.current_spell_id == first
    page.live_update()
    assert "selected" in page.accordions and page.tiles[0].isChecked()


def test_simulation_page(window, storage) -> None:
    view = window.advanced_view
    view.go_tab("simulation")
    page = view.pages["simulation"]
    strategy = StrategyBinding(storage)
    strategy.set("mode", "Survie")
    strategy.set("target", "Moins de PV")
    strategy.set("reserve_ap", 2)
    saved = storage.load_strategy()
    assert (saved.mode, saved.target_priority, saved.reserve_ap) == (StrategyMode.SURVIVAL, TargetPriority.LOWEST_HP, 2)
    assert strategy.get("mode") == "Survie"
    window.legacy.dashboard.set_status("En pause")
    page.live_update()
    assert page.toggle.text() == "Arrêter" and page.pause.text() == "Reprendre" and page.pause.isVisibleTo(page)
    window.legacy.controller.event_ready.emit(CombatEvent("INFO", "combat", "Tour 1 · Flèche Magique"))
    assert page.lines[0][1] == "Tour 1 · Flèche Magique"
    storage.record_combat("victoire", 3, 1240, 310)
    page.live_update()
    assert page.stats["victories"].text() == "1" and page.stats["xp"].text() == "1 240"


def test_corpus_page_selects_and_opens_tools(window, tmp_path, monkeypatch) -> None:
    view = window.advanced_view
    corpus = window.legacy.corpus
    entry = corpus.repository.import_debug(make_debug(tmp_path / "debug"), session_id="s1", frame_index=0)
    view.go_tab("corpus")
    page = view.pages["corpus"]
    assert page.selected_id == entry.observation_id
    assert page.selected_caps.text() == f"ANNOTER · {entry.observation_id}"
    assert corpus.list.currentItem().data(Qt.ItemDataRole.UserRole) == entry.observation_id
    assert page.tool_counts["annot"].text() == "1 à annoter"
    opened = []
    monkeypatch.setattr(corpus, "_hud_review", lambda: opened.append("hud"))
    page.open_tool("hud")
    assert opened == ["hud"]


def test_journal_filters_levels(window, storage) -> None:
    storage.record_event(CombatEvent("ERROR", "vision.grid", "Projection incertaine"))
    storage.record_event(CombatEvent("WARNING", "vision.hud", "PA illisible"))
    view = window.advanced_view
    view.go_tab("journal")
    page = view.pages["journal"]
    page.filters.buttons["Erreurs"].click()
    texts = [label.text() for row in page.rows for label in row.findChildren(QLabel)]
    assert "Projection incertaine" in texts and "PA illisible" not in texts


def test_bindings_and_colors() -> None:
    values = {"x": 1}
    binding = WidgetBinding().add("x", lambda: values["x"], lambda value: values.__setitem__("x", value))
    binding.set("x", 5)
    assert binding.get("x") == 5 and binding.get("missing", 3) == 3
    assert value_color("Oui") == theme.GREEN_LIGHT
    assert value_color("Inconnu") == theme.TEXT_2
    assert value_color("153880322") == theme.TEXT


def test_notifications_follow_the_visible_screen(window, monkeypatch) -> None:
    monkeypatch.setattr(window, "isActiveWindow", lambda: True)
    window.app_view.spells_detected(12)   # la détection se lance depuis les Outils avancés
    assert [toast.title.text() for toast in window.advanced_view.toasts.toasts] == ["Détection terminée"]
    assert window.app_view.toasts.toasts == []


def test_legacy_features_stay_reachable(window, monkeypatch) -> None:
    """Masquer l'interface historique ne doit rendre aucune de ses fonctions inaccessible."""
    view = window.advanced_view
    shown = []
    monkeypatch.setattr("combatbot.ui.dofbot2.tool_frame.show_legacy_page",
                        lambda host, page, title, subtitle: shown.append(
                            (window.legacy.combat.mode.currentText(), title)))
    view.go_tab("observation")
    details = view.pages["observation"].accordions["details"].rows()
    captions = [row.findChild(QLabel, "d2RowTitle").text() for row in details[:-1]]
    assert "Qualité observation" in captions and "Sûre pour décision" in captions
    window.legacy.combat.real_values["quality"].setText("Bonne")
    view.pages["observation"].live_update()
    assert view.pages["observation"].detail_values["quality"].text() == "Bonne"
    view.open_scan_editor()
    view.open_statistics()
    view.open_simulated_combat()
    view.open_gamedata_tools()
    assert [title for _page, title in shown] == [
        "Éditeur des sorts scannés", "Historique des combats", "Combat simulé", "Données du client DOFUS"]
    assert shown[2][0] == "Simulation"   # la vue simulée s'ouvre en mode simulation
    view.go_tab("observation")
    assert window.legacy.combat.mode.currentText() == "Vision réelle"


def test_resume_automatic_map_row_appears_in_manual_mode(window) -> None:
    """Le contrôleur historique reste masqué : l'état du bouton se lit avec isHidden(), pas isVisible()."""
    view = window.advanced_view
    combat = window.legacy.combat
    view.go_tab("observation")
    page = view.pages["observation"]

    def titles() -> list[str]:
        return [row.findChild(QLabel).text() for row in page.accordions["map"].rows() if row.findChild(QLabel)]

    page.live_update()
    assert "Revenir à la détection automatique" not in titles()
    combat.map_auto.setVisible(True)                   # mapId manuel déclaré
    page.live_update()
    assert "Revenir à la détection automatique" in titles()
    requested = []
    combat.map_auto_requested.connect(lambda: requested.append(True))
    page.accordions["map"].rows()[-1].control.findChild(QPushButton).click()
    assert requested
