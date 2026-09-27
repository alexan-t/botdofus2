"""Fenêtre DofBot2 : cadre custom, démarrage → connexion → profil → application."""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QMenu, QSizeGrip, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from combatbot.storage import Storage
from combatbot.ui.dofbot2 import theme as t
from combatbot.ui.dofbot2.advanced import ADVANCED_LABELS, AdvancedView
from combatbot.ui.dofbot2.app import TAB_LABELS, AppView
from combatbot.ui.dofbot2.game_windows import GameWindow
from combatbot.ui.dofbot2.profiles import last_profile_id, remember_profile
from combatbot.ui.dofbot2.screens import ConnectScreen, CreateProfileScreen, ProfileScreen, SplashScreen
from combatbot.ui.dofbot2.system import hotkey_from_message, register_hotkeys, unregister_hotkeys
from combatbot.ui.dofbot2.tool_frame import global_area, present_in
from combatbot.ui.dofbot2.widgets import LogoBadge, StepIndicator, TitleBar
from combatbot.ui.jobs import JobRunner
from combatbot.ui.theme import STYLE as LEGACY_STYLE
from combatbot.ui.tool_host import set_presenter


RESIZE_BORDER = 6
SCREENS = ("splash", "connect", "profile", "create", "app")


def logo_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    LogoBadge(64, 18, 26).render(pixmap)
    return QIcon(pixmap)


class RoundedFrame(QWidget):
    """Fond de fenêtre #0c100d, rayon 14, bordure rgba(255,255,255,.07) (carré une fois agrandie)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("d2Frame")
        self.maximized = False

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        radius = 0 if self.maximized else 14
        painter.setPen(QPen(t.white(0.07), 1) if not self.maximized else Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.WINDOW))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)


class DofBot2Window(QWidget):
    """Fenêtre principale. L'interface historique (calibration, scan, observation, corpus) reste
    disponible dans « Outils avancés » et sert à vérifier la fenêtre et à scanner les sorts."""

    def __init__(self, storage: Storage, start_screen: str = "splash") -> None:
        super().__init__()
        if start_screen not in SCREENS:
            raise ValueError(f"Écran inconnu : {start_screen}")
        self.storage = storage
        self.jobs = JobRunner()
        self.legacy = None          # MainWindow historique, créée à la première entrée dans l'application
        self.game_window: GameWindow | None = None
        self.profile_id: int | None = None
        self._closing = False
        self._close_when_idle = False

        self.setWindowTitle("DofBot2")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(t.STYLE)
        self.resize(1200, 760)
        self.setMinimumSize(1060, 680)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.frame = RoundedFrame()
        outer.addWidget(self.frame)
        frame_layout = QVBoxLayout(self.frame)
        frame_layout.setContentsMargins(1, 1, 1, 1)
        frame_layout.setSpacing(0)
        self.title_bar = TitleBar()
        self.title_bar.minimize_requested.connect(self.showMinimized)
        self.title_bar.maximize_requested.connect(self._toggle_maximized)
        self.title_bar.close_requested.connect(self.close)
        frame_layout.addWidget(self.title_bar)

        self.screens = QStackedWidget()
        self.screens.setObjectName("d2Screens")
        frame_layout.addWidget(self.screens, 1)

        self.splash = SplashScreen()
        self.splash.finished.connect(lambda: self.show_screen("connect"))
        self.screens.addWidget(self.splash)

        onboarding = QWidget()
        onboarding.setObjectName("d2Screen")
        onboarding_layout = QVBoxLayout(onboarding)
        onboarding_layout.setContentsMargins(0, 22, 0, 0)
        onboarding_layout.setSpacing(0)
        self.steps = StepIndicator()
        onboarding_layout.addWidget(self.steps, 0, Qt.AlignmentFlag.AlignHCenter)
        self.onboarding_stack = QStackedWidget()
        self.onboarding_stack.setObjectName("d2Screens")
        onboarding_layout.addWidget(self.onboarding_stack, 1)
        self.connect_screen = ConnectScreen(self.jobs)
        self.profile_screen = ProfileScreen(storage)
        self.create_screen = CreateProfileScreen(storage)
        for screen in (self.connect_screen, self.profile_screen, self.create_screen):
            self.onboarding_stack.addWidget(screen)
        self.screens.addWidget(onboarding)
        self.onboarding = onboarding

        self.app_view = AppView(storage, self.jobs)
        self.app_view.request_screen.connect(self.show_screen)
        self.app_view.open_advanced.connect(self.open_advanced)
        self.app_view.tab_changed.connect(lambda _key: self._update_crumb())
        self.app_view.redetect_handler = self._redetect_spells
        self.screens.addWidget(self.app_view)
        self.app_page = self.app_view

        self.advanced_view: AdvancedView | None = None   # créée à la première ouverture
        set_presenter(lambda dialog, key: present_in(self, dialog, key))
        self._tool_title: str | None = None

        self.connect_screen.window_chosen.connect(self._window_chosen)
        self.profile_screen.profile_chosen.connect(self.enter_app)
        self.profile_screen.create_requested.connect(lambda: self.show_screen("create"))
        self.create_screen.created.connect(self.enter_app)
        self.create_screen.back_requested.connect(lambda: self.show_screen("profile"))

        if sys.platform != "win32":  # Windows redimensionne par les bords (WM_NCHITTEST)
            self.size_grip = QSizeGrip(self)
            self.size_grip.resize(14, 14)
        else:
            self.size_grip = None
        self.fullscreen_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        self.fullscreen_shortcut.activated.connect(self.toggle_fullscreen)
        # F8 / F9 : raccourcis globaux sous Windows (RegisterHotKey), raccourcis de fenêtre sinon.
        self._hotkeys_registered = False
        self.local_shortcuts = []
        if sys.platform != "win32":
            for key, action in ((Qt.Key.Key_F8, self._hotkey_start_pause), (Qt.Key.Key_F9, self._hotkey_stop)):
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.activated.connect(action)
                self.local_shortcuts.append(shortcut)
        self.tray: QSystemTrayIcon | None = None
        self.setWindowIcon(logo_icon())
        last = last_profile_id(storage)
        if start_screen == "app" and last is not None and any(p.id == last for p in storage.list_profiles()):
            self.enter_app(last)
        else:
            self.show_screen(start_screen)

    # --- Navigation -------------------------------------------------------------------------
    @property
    def current_screen(self) -> str:
        page = self.screens.currentWidget()
        if page is self.splash:
            return "splash"
        if page is self.app_view:
            return "app"
        if page is not None and page is self.advanced_view:
            return "advanced"
        return {self.connect_screen: "connect", self.profile_screen: "profile",
                self.create_screen: "create"}[self.onboarding_stack.currentWidget()]

    def show_screen(self, name: str) -> None:
        if name == "splash":
            self.screens.setCurrentWidget(self.splash)
            self.splash.start()
        elif name == "app":
            if self.profile_id is None:
                name = "profile"
            else:
                self.screens.setCurrentWidget(self.app_view)
                self.app_view.go_tab(self.app_view.current_tab, animate=False)
        if name in ("connect", "profile", "create"):
            screen = {"connect": self.connect_screen, "profile": self.profile_screen,
                      "create": self.create_screen}[name]
            if name == "profile":
                self.profile_screen.refresh()
            elif name == "create":
                self.create_screen.reset()
            self.steps.set_current(0 if name == "connect" else 1)
            self.onboarding_stack.setCurrentWidget(screen)
            self.screens.setCurrentWidget(self.onboarding)
        self._update_crumb()

    def _window_chosen(self, window: GameWindow | None) -> None:
        self.game_window = window
        if self.profile_id is not None:   # « Changer » depuis Réglages : on garde le profil en cours
            self.enter_app(self.profile_id)
        else:
            self.show_screen("profile")

    def enter_app(self, profile_id: int) -> None:
        self.profile_id = profile_id
        remember_profile(self.storage, profile_id)
        legacy = self._ensure_legacy()
        panel = legacy.client_panel
        if panel.profile_id != profile_id or panel.profiles.findData(profile_id) < 0:
            panel.refresh_profiles(profile_id)
        self.app_view.set_profile(profile_id, self.game_window)
        self.screens.setCurrentWidget(self.app_view)
        self._update_crumb()
        if self.game_window is not None:
            hwnd = self.game_window.hwnd
            # Laisse l'écran s'afficher avant la capture de vérification (qui masque la fenêtre).
            QTimer.singleShot(250, lambda: self._connect_legacy(hwnd))

    def open_advanced(self) -> None:
        legacy = self._ensure_legacy()
        if self.advanced_view is None:
            view = AdvancedView(self.storage, legacy, lambda: self.game_window)
            view.back_requested.connect(lambda: self.show_screen("app"))
            view.window_change_requested.connect(lambda: self.show_screen("connect"))
            view.tab_changed.connect(lambda _key: self._update_crumb())
            self.screens.addWidget(view)
            self.advanced_view = view
        self.screens.setCurrentWidget(self.advanced_view)
        self._update_crumb()

    def _connect_legacy(self, hwnd: int) -> None:
        if self.legacy is None or self._closing:
            return
        if not self.legacy.client_panel.connect_to(hwnd):
            self.app_view.log("La fenêtre choisie n'est plus ouverte", "alert")
            self.app_view.notify("Fenêtre introuvable", "Choisissez de nouveau la fenêtre du jeu dans Réglages.",
                                 t.ALERT)

    def _redetect_spells(self) -> None:
        legacy = self._ensure_legacy()
        if legacy.client_panel.connected_hwnd is None:
            self.app_view.notify("Fenêtre non connectée",
                                 "Connectez la fenêtre du jeu (Réglages → Connexion), puis relancez la détection.",
                                 t.ALERT)
            return
        legacy.scan_panel.request_scan()

    def _ensure_legacy(self):
        """Interface historique : contrôleur masqué (connexion, captures, scan, observation, corpus).
        Ses écrans sont remplacés par la vue « Outils avancés » ; ses outils s'ouvrent en écran A7."""
        if self.legacy is None:
            from combatbot.ui.main_window import MainWindow  # import tardif : démarrage plus rapide
            legacy = MainWindow(self.storage)
            legacy.setWindowFlags(Qt.WindowType.Widget)
            legacy.setParent(self)
            legacy.setStyleSheet(LEGACY_STYLE)   # dialogues enfants : palette historique alignée sur DofBot2
            legacy.fullscreen_shortcut.setEnabled(False)   # un seul F11 par fenêtre, sinon Qt l'ignore
            legacy.exit_fullscreen_shortcut.setEnabled(False)
            legacy.scan_panel.scan_finished.connect(self.app_view.spells_detected)
            legacy.hide()
            self.legacy = legacy
        return self.legacy

    def _update_crumb(self) -> None:
        crumb = ""
        page = self.screens.currentWidget()
        if page is self.app_view:
            crumb = TAB_LABELS.get(self.app_view.current_tab, "")
        elif page is not None and page is self.advanced_view:
            crumb = f"Outils avancés · {ADVANCED_LABELS[self.advanced_view.current_tab]}"
        if self._tool_title:
            crumb = f"Outils avancés · {self._tool_title}"
        self.title_bar.set_crumb(crumb)

    # --- Outils plein écran (A7) ----------------------------------------------------------------
    def tool_area(self) -> QRect:
        return global_area(self, self.title_bar.height() + 1)

    def is_expanded(self) -> bool:
        return self.isMaximized() or self.isFullScreen()

    def tool_opened(self, title: str) -> None:
        self._tool_title = title
        self._update_crumb()

    def tool_closed(self) -> None:
        self._tool_title = None
        self._update_crumb()
        if self.advanced_view is not None and self.screens.currentWidget() is self.advanced_view:
            self.advanced_view.refresh_current()

    # --- Raccourcis F8 / F9 ---------------------------------------------------------------------
    def _hotkey_start_pause(self) -> None:
        if self.profile_id is not None:
            self.app_view.toggle_run()

    def _hotkey_stop(self) -> None:
        if self.profile_id is not None:
            self.app_view.emergency_stop()

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        if sys.platform == "win32" and not self._hotkeys_registered:
            self._hotkeys_registered = True
            refused = register_hotkeys(int(self.winId()))
            if refused:   # touche déjà prise ailleurs : raccourci limité à la fenêtre DofBot2
                actions = {"start_pause": (Qt.Key.Key_F8, self._hotkey_start_pause),
                           "emergency_stop": (Qt.Key.Key_F9, self._hotkey_stop)}
                for name in refused:
                    key, action = actions[name]
                    shortcut = QShortcut(QKeySequence(key), self)
                    shortcut.activated.connect(action)
                    self.local_shortcuts.append(shortcut)

    # --- Barre des tâches ------------------------------------------------------------------------
    def _minimize_to_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self.tray is None:
            self.tray = QSystemTrayIcon(logo_icon(), self)
            self.tray.setToolTip("DofBot2")
            menu = QMenu(self)
            menu.addAction("Ouvrir DofBot2", self._restore_from_tray)
            menu.addAction("Quitter", self.close)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(
                lambda reason: self._restore_from_tray()
                if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick)
                else None)
        self.tray.show()
        self.hide()

    def _restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if self.tray is not None:
            self.tray.hide()

    # --- Fenêtre sans cadre --------------------------------------------------------------------
    def _toggle_maximized(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        elif self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def toggle_fullscreen(self) -> None:
        if self.legacy is not None:
            self.legacy._toggle_fullscreen()   # garde la géométrie à restaurer et le libellé du bouton
        elif self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - API Qt
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized() and self.app_view.app_settings.get("tray", True):
                QTimer.singleShot(0, self._minimize_to_tray)
            full = self.isMaximized() or self.isFullScreen()
            self.frame.maximized = full
            self.frame.update()
            self.title_bar.set_maximized(full)

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        if self.size_grip is not None:
            self.size_grip.move(self.width() - self.size_grip.width() - 2, self.height() - self.size_grip.height() - 2)
            self.size_grip.raise_()

    def nativeEvent(self, event_type, message):  # noqa: N802 - API Qt
        if sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG":
            hotkey = hotkey_from_message(int(message))
            if hotkey is not None:
                (self._hotkey_start_pause if hotkey == "start_pause" else self._hotkey_stop)()
                return True, 0
        if sys.platform == "win32" and bytes(event_type) == b"windows_generic_MSG" \
                and not (self.isMaximized() or self.isFullScreen()):
            hit = _hit_test(int(message), round(RESIZE_BORDER * self.devicePixelRatioF()))
            if hit:
                return True, hit
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._closing = True
        if self.legacy is not None and not self.legacy.close():
            event.ignore()   # capture ou simulation en cours : l'application historique rappellera close()
            return
        set_presenter(None)
        if self.jobs.active:   # miniature encore en cours de capture
            if not self._close_when_idle:
                self._close_when_idle = True
                self.jobs.all_done.connect(self.close)
            event.ignore()
            return
        if self._hotkeys_registered:
            unregister_hotkeys(int(self.winId()))
        if self.tray is not None:
            self.tray.hide()
        self.app_view.close_desktop_toasts()
        self.storage.close()
        super().closeEvent(event)


def _hit_test(message_address: int, border: int) -> int:
    """Redimensionnement par les bords d'une fenêtre sans cadre (WM_NCHITTEST)."""
    from ctypes import wintypes
    msg = wintypes.MSG.from_address(message_address)
    if msg.message != 0x0084:  # WM_NCHITTEST
        return 0
    x = ctypes.c_short(msg.lParam & 0xFFFF).value
    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(msg.hWnd, ctypes.byref(rect)):
        return 0
    left, right = x < rect.left + border, x >= rect.right - border
    top, bottom = y < rect.top + border, y >= rect.bottom - border
    return {
        (True, False, True, False): 13, (False, True, True, False): 14,     # HTTOPLEFT, HTTOPRIGHT
        (True, False, False, True): 16, (False, True, False, True): 17,     # HTBOTTOMLEFT, HTBOTTOMRIGHT
        (True, False, False, False): 10, (False, True, False, False): 11,   # HTLEFT, HTRIGHT
        (False, False, True, False): 12, (False, False, False, True): 15,   # HTTOP, HTBOTTOM
    }.get((left, right, top, bottom), 0)
