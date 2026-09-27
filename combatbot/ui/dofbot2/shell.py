"""Fenêtre DofBot2 : cadre custom, démarrage → connexion → profil → application."""

from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontMetrics, QKeySequence, QPainter, QPainterPath, QPen, QRegion, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout, QSizeGrip, QStackedWidget, QVBoxLayout, QWidget,
)

from combatbot.storage import Storage
from combatbot.ui.dofbot2 import theme as t
from combatbot.ui.dofbot2.game_windows import GameWindow
from combatbot.ui.dofbot2.profiles import ProfileEntry, last_profile_id, profile_entry, remember_profile
from combatbot.ui.dofbot2.screens import ConnectScreen, CreateProfileScreen, ProfileScreen, SplashScreen
from combatbot.ui.dofbot2.widgets import Avatar, HoverButton, StepIndicator, TitleBar
from combatbot.ui.jobs import JobRunner
from combatbot.ui.theme import STYLE as LEGACY_STYLE


RESIZE_BORDER = 6
SCREENS = ("splash", "connect", "profile", "create", "app")


class ProfileChip(HoverButton):
    """Puce profil de la barre haute (avatar 32px, nom, méta) ; renvoie au choix du profil."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(44)
        self.avatar = Avatar(32, self)
        self.avatar.move(6, 6)
        self.name = ""
        self.meta = ""
        self.setToolTip("Changer de profil")

    def set_profile(self, entry: ProfileEntry, window: GameWindow | None) -> None:
        self.name = entry.name
        parts = [entry.character_class] if entry.character_class else []
        parts.append(window.title if window else "Aucune fenêtre")
        self.meta = " · ".join(parts)
        self.avatar.set_avatar(entry.color, entry.initial, entry.image_png)
        width = max(QFontMetrics(t.font(13, 700)).horizontalAdvance(self.name),
                    QFontMetrics(t.font(11)).horizontalAdvance(self.meta))
        self.setFixedWidth(6 + 32 + 10 + width + 12)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if self.hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.SURFACE))
            painter.drawRoundedRect(rect, 22, 22)
        painter.setPen(QColor(t.TEXT))
        painter.setFont(t.font(13, 700))
        painter.drawText(QRectF(48, 5, rect.width() - 48, 18), Qt.AlignmentFlag.AlignVCenter, self.name)
        painter.setPen(QColor(t.TEXT_2))
        painter.setFont(t.font(11))
        painter.drawText(QRectF(48, 23, rect.width() - 48, 15), Qt.AlignmentFlag.AlignVCenter, self.meta)
        self._focus_ring(painter, rect.adjusted(0.5, 0.5, -0.5, -0.5), 22)


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
    """Fenêtre principale. L'application historique est intégrée dans l'écran « app » en attendant
    la migration des onglets (Accueil, Donjons, Zones, Sorts, Alertes, Réglages)."""

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

        self.app_page = QWidget()
        self.app_page.setObjectName("d2Screen")
        app_layout = QVBoxLayout(self.app_page)
        app_layout.setContentsMargins(0, 0, 0, 0)
        app_layout.setSpacing(0)
        app_bar = QWidget()
        app_bar.setObjectName("d2AppBar")
        app_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        app_bar.setFixedHeight(64)
        bar_layout = QHBoxLayout(app_bar)
        bar_layout.setContentsMargins(22, 0, 28, 0)
        bar_layout.setSpacing(14)
        self.profile_chip = ProfileChip()
        self.profile_chip.clicked.connect(lambda: self.show_screen("profile"))
        bar_layout.addWidget(self.profile_chip)
        bar_layout.addStretch(1)
        app_layout.addWidget(app_bar)
        self.legacy_host = QVBoxLayout()
        self.legacy_host.setContentsMargins(0, 0, 0, 0)
        app_layout.addLayout(self.legacy_host, 1)
        self.screens.addWidget(self.app_page)

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
        if page is self.app_page:
            return "app"
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
                self.screens.setCurrentWidget(self.app_page)
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
        self.show_screen("profile")

    def enter_app(self, profile_id: int) -> None:
        self.profile_id = profile_id
        remember_profile(self.storage, profile_id)
        entry = profile_entry(self.storage, self.storage.get_profile(profile_id))
        self.profile_chip.set_profile(entry, self.game_window)
        legacy = self._ensure_legacy()
        panel = legacy.client_panel
        if panel.profile_id != profile_id or panel.profiles.findData(profile_id) < 0:
            panel.refresh_profiles(profile_id)
        self.screens.setCurrentWidget(self.app_page)
        self._update_crumb()
        if self.game_window is not None:
            hwnd = self.game_window.hwnd
            # Laisse l'écran s'afficher avant la capture de vérification (qui masque la fenêtre).
            QTimer.singleShot(250, lambda: self._connect_legacy(hwnd))

    def _connect_legacy(self, hwnd: int) -> None:
        if self.legacy is None or self._closing:
            return
        if not self.legacy.client_panel.connect_to(hwnd):
            self.legacy.client_panel.show_error("La fenêtre choisie n'est plus ouverte : reconnectez-la dans Paramètres")

    def _ensure_legacy(self):
        if self.legacy is None:
            from combatbot.ui.main_window import MainWindow  # import tardif : démarrage plus rapide
            legacy = MainWindow(self.storage)
            legacy.setWindowFlags(Qt.WindowType.Widget)
            legacy.setStyleSheet(LEGACY_STYLE)   # plus proche dans la cascade : garde son apparence
            legacy.stack.currentChanged.connect(lambda _index: self._update_crumb())
            legacy.fullscreen_shortcut.setEnabled(False)   # un seul F11 par fenêtre, sinon Qt l'ignore
            self.legacy_host.addWidget(legacy)
            legacy.installEventFilter(self)
            legacy.show()
            self.legacy = legacy
        return self.legacy

    def _update_crumb(self) -> None:
        crumb = ""
        if self.screens.currentWidget() is self.app_page and self.legacy is not None:
            index = self.legacy.stack.currentIndex()
            crumb = self.legacy.nav_buttons[index].text() if 0 <= index < len(self.legacy.nav_buttons) else ""
        self.title_bar.set_crumb(crumb)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if watched is self.legacy and event.type() == QEvent.Type.Resize:
            self._mask_legacy()
        return super().eventFilter(watched, event)

    def _mask_legacy(self) -> None:
        """L'interface historique peint des coins carrés : on suit l'arrondi bas de la fenêtre."""
        if self.legacy is None:
            return
        rect = self.legacy.rect()
        if self.frame.maximized:
            self.legacy.clearMask()
            return
        radius = 13
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), radius, radius)
        path.addRect(QRectF(0, 0, rect.width(), radius))
        self.legacy.setMask(QRegion(path.simplified().toFillPolygon().toPolygon()))

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
            full = self.isMaximized() or self.isFullScreen()
            self.frame.maximized = full
            self.frame.update()
            self._mask_legacy()
            self.title_bar.set_maximized(full)

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        if self.size_grip is not None:
            self.size_grip.move(self.width() - self.size_grip.width() - 2, self.height() - self.size_grip.height() - 2)
            self.size_grip.raise_()

    def nativeEvent(self, event_type, message):  # noqa: N802 - API Qt
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
        if self.jobs.active:   # miniature encore en cours de capture
            if not self._close_when_idle:
                self._close_when_idle = True
                self.jobs.all_done.connect(self.close)
            event.ignore()
            return
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
