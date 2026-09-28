"""Application principale DofBot2 : barre haute, onglets, dock de bulles, toasts, session."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from combatbot.combat.safety import GLOBAL_EMERGENCY_STOP
from combatbot.models import CombatEvent
from combatbot.storage import Storage
from combatbot.ui.dofbot2 import theme as t
from combatbot.ui.dofbot2.controls import Dock, ToastStack, button, label
from combatbot.ui.dofbot2.game_windows import GameWindow
from combatbot.ui.dofbot2.pages import (
    AlertsPage, HomePage, Page, PlacesPage, SettingsPage, SpellsPage, journal_entry,
)
from combatbot.ui.dofbot2.profiles import ProfileEntry, profile_entry
from combatbot.ui.dofbot2.widgets import Avatar, HoverButton
from combatbot.ui.dofbot2.settings import (
    TOAST_DURATIONS, SpellConfig, app_settings, current_plan, profile_settings, remember_recent_dungeon,
)
from combatbot.ui.dofbot2.system import (
    foreground_window, play_alert_sound, send_discord, set_autostart, webhook_problem,
)
from combatbot.ui.jobs import JobRunner


TABS = (("home", "Accueil"), ("donjon", "Donjons"), ("zone", "Zones"), ("sorts", "Sorts"),
        ("notifs", "Alertes"), ("params", "Réglages"))
TAB_LABELS = dict(TABS)
EVENT_PREFIX = "dofbot2."


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


class StatusDot(QWidget):
    """Point 7px lumineux de la pastille d'état."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(15, 15)
        self.color = QColor(t.TEXT_2)

    def set_color(self, color: str) -> None:
        if QColor(color) == self.color:
            return
        self.color = QColor(color)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        for radius, alpha in ((7.5, 0.10), (5.5, 0.22)):   # halo « box-shadow 0 0 8px »
            glow = QColor(self.color)
            glow.setAlphaF(alpha)
            painter.setBrush(glow)
            painter.drawEllipse(self.rect().center() + QPoint(1, 1), radius, radius)
        painter.setBrush(self.color)
        painter.drawEllipse(self.rect().center() + QPoint(1, 1), 3.5, 3.5)


class AppView(QWidget):
    """Écran « app » : une instance par fenêtre, rechargée à chaque changement de profil."""

    plan_changed = Signal()
    running_changed = Signal(bool)       # point d'accroche du moteur d'automatisation
    request_screen = Signal(str)         # connect | profile
    open_advanced = Signal()
    tab_changed = Signal(str)

    def __init__(self, storage: Storage, jobs: JobRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("d2Screen")
        self.storage, self.jobs = storage, jobs
        self.profile_id: int | None = None
        self.profile: ProfileEntry | None = None
        self.game_window: GameWindow | None = None
        self.running = False
        self.current_tab = "home"
        self.redetect_handler: Callable[[], None] | None = None
        self.desktop_toasts: ToastStack | None = None
        # Écran qui affiche les toasts quand cette vue est masquée (fourni par la fenêtre DofBot2).
        self.toast_sink: Callable[[str, str, str, int], None] | None = None
        self.app_settings = app_settings(storage)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("d2AppBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        bar.setFixedHeight(64)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(22, 0, 28, 0)
        bar_layout.setSpacing(14)
        self.profile_chip = ProfileChip()
        self.profile_chip.clicked.connect(lambda: self.request_screen.emit("profile"))
        bar_layout.addWidget(self.profile_chip)
        bar_layout.addStretch(1)
        pill = QWidget()
        pill.setObjectName("d2StatusPill")
        pill.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        pill.setFixedHeight(32)
        pill_layout = QHBoxLayout(pill)
        pill_layout.setContentsMargins(10, 0, 14, 0)
        pill_layout.setSpacing(4)
        self.status_dot = StatusDot()
        self.status_text = label("", "d2StatusText")
        pill_layout.addWidget(self.status_dot)
        pill_layout.addWidget(self.status_text)
        bar_layout.addWidget(pill)
        self.run_button = button("Démarrer", "d2Run")
        self.run_button.setToolTip("Démarrer / pause : F8 · arrêt d'urgence : F9")
        self.run_button.clicked.connect(self.toggle_run)
        bar_layout.addWidget(self.run_button)
        layout.addWidget(bar)

        self.body = QWidget()
        self.body.setObjectName("d2Screen")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.stack.setObjectName("d2Screens")
        body_layout.addWidget(self.stack)
        layout.addWidget(self.body, 1)
        self.pages: dict[str, Page] = {}
        self.dock = Dock(TABS, self.body)
        self.dock.tab_selected.connect(self.go_tab)
        self.dock.resized.connect(self._place_overlays)
        self.toasts = ToastStack(self.body)
        self.toasts.changed.connect(self._place_overlays)
        self.toasts.hide()
        self.plan_changed.connect(self._plan_changed)
        self._set_running_ui()

    # --- Profil ---------------------------------------------------------------------------------
    def set_profile(self, profile_id: int, game_window: GameWindow | None) -> None:
        if profile_id != self.profile_id and self.running:
            self.stop()
        self.profile_id = profile_id
        self.profile = profile_entry(self.storage, self.storage.get_profile(profile_id))
        self.game_window = game_window
        self.settings = profile_settings(self.storage, profile_id)
        self.spell_config = SpellConfig(self.storage, profile_id)
        self.profile_chip.set_profile(self.profile, game_window)
        for page in self.pages.values():   # les pages sont liées au profil : on les recrée
            self.stack.removeWidget(page)
            page.hide()
            page.deleteLater()
        self.pages = {
            "home": HomePage(self), "donjon": PlacesPage(self, "donjon"), "zone": PlacesPage(self, "zone"),
            "sorts": SpellsPage(self), "notifs": AlertsPage(self), "params": SettingsPage(self),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        self.dock.set_badge("home", False)
        self.go_tab("home", animate=False)

    @property
    def profile_name(self) -> str:
        return self.profile.name if self.profile else ""

    def window_label(self) -> str:
        return self.game_window.title if self.game_window else "Aucune fenêtre"

    # --- Navigation -----------------------------------------------------------------------------
    def go_tab(self, key: str, animate: bool = True) -> None:
        if key not in self.pages:
            return
        self.current_tab = key
        page = self.pages[key]
        page.refresh()
        self.stack.setCurrentWidget(page)
        self.dock.set_current(key, animate)
        if key == "home":
            self.dock.set_badge("home", False)
        self._place_overlays()
        self.tab_changed.emit(key)

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self._place_overlays()

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self._place_overlays()

    def _place_overlays(self) -> None:
        """Dock centré à 22px du bas ; toasts à 20px du bord droit et 96px du bas."""
        area = self.body.rect()
        hint = self.dock.sizeHint()
        self.dock.setGeometry(QRect((area.width() - hint.width()) // 2, area.height() - 22 - hint.height(),
                                    hint.width(), hint.height()))
        self.dock.raise_()
        if self.toasts.toasts:
            size = self.toasts.sizeHint()
            self.toasts.setGeometry(area.width() - 20 - size.width(), area.height() - 96 - size.height(),
                                    size.width(), size.height())
            self.toasts.raise_()

    # --- Session --------------------------------------------------------------------------------
    def toggle_run(self) -> None:
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        if self.running or self.profile_id is None:
            return
        plan = current_plan(self.settings)
        if plan.mode == "donjon":
            remember_recent_dungeon(self.settings, plan.place)
            dungeon_page = self.pages.get("donjon")
            if isinstance(dungeon_page, PlacesPage):
                dungeon_page.refresh_recent()
        GLOBAL_EMERGENCY_STOP.rearm()   # seul un démarrage humain explicite réarme après F9
        self.running = True
        self._set_running_ui()
        self.log(f"Session démarrée · {plan.place.name}", "green")
        self.log("Lecture seule : DofBot2 observe le jeu, aucune action ne lui est envoyée", "muted")
        self.notify("Session démarrée", plan.place.name, t.GREEN)
        self.running_changed.emit(True)

    def stop(self, reason: str = "Session arrêtée") -> None:
        if not self.running:
            return
        self.running = False
        self._set_running_ui()
        self.log(reason, "muted")
        self.notify("Session arrêtée", "Le personnage est à l'arrêt.", kind="nfEnd")
        self.running_changed.emit(False)

    def emergency_stop(self) -> None:
        # Verrou global lu par toute future boucle d'action, même si la session n'est pas « en cours ».
        GLOBAL_EMERGENCY_STOP.trigger("Arrêt d'urgence (F9)")
        self.stop("Arrêt d'urgence (F9)")

    def _set_running_ui(self) -> None:
        color = t.GREEN if self.running else t.TEXT_2
        self.status_dot.set_color(color)
        self.status_text.setText("En cours" if self.running else "En pause")
        self.status_text.setStyleSheet(f"color: {color};")
        self.run_button.setText("Arrêter" if self.running else "Démarrer")
        self.run_button.setProperty("running", self.running)
        self.run_button.style().unpolish(self.run_button)
        self.run_button.style().polish(self.run_button)
        home = self.pages.get("home")
        if isinstance(home, HomePage):
            home.set_running(self.running)

    def _plan_changed(self) -> None:
        home = self.pages.get("home")
        if home is not None and self.current_tab == "home":
            home.refresh()

    # --- Journal et alertes ---------------------------------------------------------------------
    def log(self, message: str, tone: str = "text") -> None:
        if self.profile_id is None:
            return
        self.storage.record_event(CombatEvent("INFO", EVENT_PREFIX + "journal", message,
                                              {"profile_id": self.profile_id, "tone": tone}))
        home = self.pages.get("home")
        if isinstance(home, HomePage) and self.current_tab == "home":
            home.refresh_journal()

    def journal(self, limit: int) -> list[tuple[str, str, str]]:
        if self.profile_id is None:
            return []
        return [journal_entry(row) for row in self.storage.recent_profile_events(EVENT_PREFIX, self.profile_id, limit)]

    def notify(self, title: str, body: str, color: str = t.TEXT, kind: str | None = None,
               force: bool = False) -> bool:
        """Affiche un toast (dans la fenêtre, ou sur le bureau si DofBot2 n'est pas au premier plan).
        ``kind`` est une clé de ``ALERT_KINDS`` filtrée par son interrupteur. Retourne True si affiché."""
        settings = getattr(self, "settings", None)
        if settings is None:
            return False
        if kind is not None and not force and not settings.get(kind, True):
            return False
        duration = TOAST_DURATIONS.get(str(settings.get("nfDur", "4 s")), 4000)
        window = self.window()
        in_front = window.isVisible() and window.isActiveWindow() and not window.isMinimized()
        if in_front and not self.isVisible() and self.toast_sink is not None:
            self.toast_sink(title, body, color, duration)   # autre écran affiché (Outils avancés)
        elif in_front:
            self.toasts.push(title, body, color, duration)
            self._place_overlays()
        else:
            game_in_front = self.game_window is not None and foreground_window() == self.game_window.hwnd
            if settings.get("nfFocus", False) and game_in_front and not force:
                return False
            self._desktop_stack().push(title, body, color, duration)
            self._place_desktop()
            if settings.get("nfSound", True):
                play_alert_sound()
        if self.current_tab != "home":
            self.dock.set_badge("home", True)
        if (kind is not None or force) and settings.get("nfDiscord", False):
            self._send_discord(title, body)
        return True

    def _send_discord(self, title: str, body: str) -> None:
        url = str(self.settings.get("webhook", "") or "")
        if webhook_problem(url):
            return
        self.jobs.submit(lambda: send_discord(url, title, body), lambda _result: None,
                         lambda message: self.log(f"Discord injoignable : {message}", "alert"))

    def _desktop_stack(self) -> ToastStack:
        """Fenêtre toast autonome, toujours au premier plan, sans voler le focus du jeu."""
        if self.desktop_toasts is None:
            stack = ToastStack()
            stack.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                                 | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
            stack.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            stack.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            stack.changed.connect(self._place_desktop)
            self.desktop_toasts = stack
        return self.desktop_toasts

    def _place_desktop(self) -> None:
        stack = self.desktop_toasts
        if stack is None or not stack.toasts:
            return
        screen = self.window().screen() or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        size = stack.sizeHint()
        stack.setGeometry(area.right() - 20 - size.width(), area.bottom() - 20 - size.height(),
                          size.width(), size.height())
        stack.show()

    def close_desktop_toasts(self) -> None:
        if self.desktop_toasts is not None:
            self.desktop_toasts.close()
            self.desktop_toasts.deleteLater()
            self.desktop_toasts = None

    # --- Sorts ----------------------------------------------------------------------------------
    def detected_spells(self) -> list:
        return self.storage.list_profile_spells(self.profile_id) if self.profile_id is not None else []

    def active_spell_count(self) -> int | None:
        spells = self.detected_spells()
        if not spells:
            return None
        return sum(1 for row in spells if self.spell_config.in_use(int(row["id"])))

    def redetect_spells(self) -> None:
        if self.redetect_handler is None:
            self.notify("Détection indisponible", "Ouvrez l'interface avancée pour scanner la barre de sorts.")
            return
        self.redetect_handler()

    def spells_detected(self, count: int) -> None:
        if self.profile_id is None:
            return
        self.storage.set_profile_setting(self.profile_id, "dofbot2_spells_detected_at",
                                         datetime.now(timezone.utc).isoformat())
        self.log(f"Détection terminée · {count} sort{'s' if count > 1 else ''}", "light")
        self.notify("Détection terminée", f"{count} sort{'s' if count > 1 else ''} trouvé{'s' if count > 1 else ''} "
                    "dans la barre.", t.GREEN)
        if self.current_tab == "sorts":
            self.pages["sorts"].refresh()
        self.plan_changed.emit()

    # --- Réglages d'application -----------------------------------------------------------------
    def set_autostart(self, enabled: bool) -> None:
        try:
            set_autostart(enabled)
        except (OSError, RuntimeError) as exc:
            self.app_settings.set("boot", False)
            self.notify("Lancement avec Windows", str(exc), t.ALERT)
            self.pages["params"].refresh()


