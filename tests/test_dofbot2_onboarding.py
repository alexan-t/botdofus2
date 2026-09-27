"""Écrans DofBot2 : démarrage, connexion à la fenêtre, choix et création de profil."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDeadlineTimer, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from combatbot.storage import Storage
from combatbot.ui.dofbot2 import game_windows, profiles, theme
from combatbot.ui.dofbot2.game_windows import GameWindow, character_from_title, describe_window, screen_index
from combatbot.ui.dofbot2.profiles import create_profile, list_profile_entries, unique_label
from combatbot.ui.dofbot2.screens import ConnectScreen, CreateProfileScreen, SplashScreen, square_png
from combatbot.ui.dofbot2.shell import DofBot2Window
from combatbot.ui.jobs import JobRunner
from combatbot.vision.models import ClientRect, WindowInfo


def _wait_for(condition, timeout_ms: int = 3000) -> bool:
    deadline = QDeadlineTimer(timeout_ms)
    while not condition() and not deadline.hasExpired():
        QTest.qWait(20)
    return condition()


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    theme.load_fonts()
    return application


@pytest.fixture
def storage(tmp_path):
    store = Storage(tmp_path / "dofbot2.sqlite3")
    yield store
    store.close()


def _windows():
    return [GameWindow(101, "Dofus · Kira", "Kira", "1920×1080 · écran 1", "Kira - Cra - 3.0 - Release"),
            GameWindow(202, "Dofus · Brakmar", "Brakmar", "1600×900 · écran 2", "Brakmar - Iop - 3.0")]


def test_profile_appearance_is_stored_without_schema_change(storage) -> None:
    kira = create_profile(storage, "  Kira  ", 3, "Cra", image_png=b"\x89PNG fake")
    storage.set_profile_setting(kira.id, profiles.LEVEL_KEY, 200)
    entries = {entry.name: entry for entry in list_profile_entries(storage)}
    # Le profil par défaut de la base garde une couleur stable dérivée de son id.
    assert entries["Profil 1"].color == theme.AVATAR_COLORS[0]
    assert entries["Profil 1"].meta == "Classe non définie"
    assert entries["Kira"].color == theme.AVATAR_COLORS[3]
    assert entries["Kira"].image_png == b"\x89PNG fake"
    assert entries["Kira"].meta == "Cra · niv. 200"
    assert entries["Kira"].initial == "K"
    assert storage.connection.execute("PRAGMA user_version").fetchone()[0] == 4


def test_profile_names_stay_unique_and_default(storage) -> None:
    create_profile(storage, "Kira", 0)
    assert unique_label(storage, "kira") == "kira 2"
    assert create_profile(storage, "Kira", 1).name == "Kira 2"
    assert create_profile(storage, "   ", 1).name == profiles.DEFAULT_PROFILE_NAME
    with pytest.raises(ValueError):
        create_profile(storage, "Iop", 99)


def test_window_titles_and_screens() -> None:
    assert character_from_title("Kira - Cra - 3.1.2.5 - Release") == "Kira"
    assert character_from_title("Dofus") is None
    assert character_from_title("Dofus 3.1 - Release") is None
    screens = [(0, 0, 1920, 1080), (1920, 0, 1600, 900)]
    assert screen_index(ClientRect(1920 + 100, 50, 800, 600), screens) == 2
    assert screen_index(ClientRect(-5000, 0, 10, 10), screens) is None
    window = describe_window(WindowInfo(7, "Kira - Cra - 3.0", False), screens,
                             geometry=lambda hwnd: ClientRect(0, 0, 1920, 1080))
    assert (window.title, window.meta, window.character) == ("Dofus · Kira", "1920×1080 · écran 1", "Kira")
    assert describe_window(WindowInfo(8, "Dofus", True), screens).meta == "fenêtre réduite"

    def closed(_hwnd):
        raise RuntimeError("Le client DOFUS a été fermé")

    assert describe_window(WindowInfo(9, "Dofus", False), screens, geometry=closed).meta == "taille inconnue"


def test_splash_messages_and_skip(app) -> None:
    assert SplashScreen.status_text(10) == "Chargement des données…"
    assert SplashScreen.status_text(40) == "Recherche des fenêtres du jeu…"
    assert SplashScreen.status_text(80) == "Prêt"
    splash = SplashScreen()
    finished = []
    splash.finished.connect(lambda: finished.append(True))
    splash.start()
    QTest.mouseClick(splash, Qt.MouseButton.LeftButton)
    splash.skip()
    assert finished == [True]   # un seul passage, même avec plusieurs clics


def test_splash_runs_to_the_connect_screen(app, storage, monkeypatch) -> None:
    monkeypatch.setattr("combatbot.ui.dofbot2.screens.SPLASH_DURATION_MS", 60)
    monkeypatch.setattr(ConnectScreen, "refresh", lambda self: self.set_windows([]))
    window = DofBot2Window(storage)
    assert window.current_screen == "splash"
    assert _wait_for(lambda: window.current_screen == "connect")
    assert window.title_bar.crumb.text() == ""
    window.close()


def test_connect_screen_keeps_selection_and_empty_state(app, monkeypatch) -> None:
    monkeypatch.setattr(game_windows, "window_thumbnail", lambda hwnd: None)
    screen = ConnectScreen(JobRunner())
    chosen = []
    screen.window_chosen.connect(chosen.append)
    screen.set_windows(_windows())
    assert screen.selected.hwnd == 101 and screen.connect_button.isEnabled()
    screen.cards[1].click()
    screen.set_windows(list(reversed(_windows())))   # l'ordre change, la sélection reste
    assert screen.selected.hwnd == 202
    screen.connect_button.click()
    assert [window.hwnd for window in chosen] == [202]

    screen.set_windows([])
    assert screen.selected is None and not screen.connect_button.isEnabled()
    assert screen.empty.isVisibleTo(screen) and screen.skip_button.isVisibleTo(screen)
    screen.skip_button.click()
    assert chosen[-1] is None
    screen.set_windows([], "Connexion au client disponible uniquement sous Windows")
    assert "uniquement sous Windows" in screen.empty.text()


def test_create_profile_screen(app, storage) -> None:
    screen = CreateProfileScreen(storage)
    created = []
    screen.created.connect(created.append)
    assert screen.preview_name.text() == "Nouveau profil"
    QTest.keyClicks(screen.name, "soin")
    assert screen.preview_name.text() == "soin" and screen.preview_avatar.initial == "S"
    screen.swatches[4].click()
    iop = next(chip for chip in screen.class_chips if chip.text() == "Iop")
    cra = next(chip for chip in screen.class_chips if chip.text() == "Cra")
    iop.click()
    cra.click()
    assert screen.character_class == "Cra" and not iop.isChecked()
    cra.click()   # la classe est facultative : un second clic la retire
    assert screen.character_class is None
    iop.click()
    screen.create_button.click()
    entry = next(entry for entry in list_profile_entries(storage) if entry.id == created[0])
    assert (entry.name, entry.character_class, entry.color) == ("soin", "Iop", theme.AVATAR_COLORS[4])
    screen.reset()
    assert screen.name.text() == "" and screen.color_index == 1 and screen.character_class is None


def test_avatar_image_is_cropped_square(app) -> None:
    image = QImage(300, 120, QImage.Format.Format_RGB32)
    image.fill(QColor("#e0677e"))
    data = square_png(image, 64)
    result = QImage.fromData(data)
    assert (result.width(), result.height()) == (64, 64)


def test_onboarding_flow_reaches_the_application(app, storage, monkeypatch) -> None:
    monkeypatch.setattr(ConnectScreen, "refresh", lambda self: self.set_windows(_windows()))
    monkeypatch.setattr(game_windows, "window_thumbnail", lambda hwnd: None)
    connected = []
    monkeypatch.setattr("combatbot.ui.client_panel.ClientPanel.connect_to",
                        lambda self, hwnd: connected.append(hwnd) or True)
    window = DofBot2Window(storage, start_screen="connect")
    window.show()
    assert window.current_screen == "connect" and window.steps.current == 0
    window.connect_screen.connect_button.click()
    assert window.current_screen == "profile" and window.steps.current == 1
    assert window.game_window.hwnd == 101
    window.profile_screen.new_card.click()
    assert window.current_screen == "create"
    QTest.keyClicks(window.create_screen.name, "Kira")
    window.create_screen.create_button.click()
    assert window.current_screen == "app"
    kira = next(entry for entry in list_profile_entries(storage) if entry.name == "Kira")
    assert window.legacy.client_panel.profile_id == kira.id
    assert profiles.last_profile_id(storage) == kira.id
    assert window.app_view.profile_chip.name == "Kira" and "Dofus · Kira" in window.app_view.profile_chip.meta
    assert window.title_bar.crumb.text() == "· Accueil"
    assert _wait_for(lambda: connected == [101])
    # La puce profil ramène au choix du profil ; les cartes reflètent le nouveau profil.
    window.app_view.profile_chip.click()
    assert window.current_screen == "profile"
    assert "Kira" in [card.name for card in window.profile_screen.cards]
    window.close()
    assert not window.isVisible()


def test_start_screen_app_reopens_the_last_profile(app, storage) -> None:
    with pytest.raises(ValueError):
        DofBot2Window(storage, start_screen="accueil")
    kira = create_profile(storage, "Kira", 0, "Cra")
    profiles.remember_profile(storage, kira.id)
    window = DofBot2Window(storage, start_screen="app")
    assert window.current_screen == "app" and window.legacy.client_panel.profile_id == kira.id
    window.close()
