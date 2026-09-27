"""Outils avancés DofBot2 : Connexion, Observation, Sorts, Simulation, Corpus, Journal.

Les pages pilotent l'application historique (``MainWindow``), qui reste le contrôleur : mêmes
vérifications, mêmes captures, mêmes vérités humaines. Rien n'est jamais envoyé au jeu."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QButtonGroup, QGridLayout, QHBoxLayout, QPushButton, QScrollArea, QStackedWidget,
    QVBoxLayout, QWidget,
)

from combatbot.models import Strategy, StrategyMode, TargetPriority
from combatbot.storage import Storage
from combatbot.ui.dofbot2 import theme as t
from combatbot.ui.dofbot2.app import StatusDot
from combatbot.ui.dofbot2.controls import (
    Choices, Dock, SettingRow, Stepper, ToastStack, button, card, choice_row, info_row, input_row, label, repolish,
    step_row, switch_row,
)
from combatbot.ui.dofbot2.icons import ICONS, icon_pixmap
from combatbot.ui.dofbot2.pages import Page
from combatbot.ui.dofbot2.settings import JsonSettings, SettingsBinding, app_settings
from combatbot.ui.dofbot2.widgets import HoverButton, rounded_pixmap
from combatbot.ui.images import bgr_to_pixmap
from combatbot.vision.models import ZONE_LABELS

if TYPE_CHECKING:
    from combatbot.ui.main_window import MainWindow


ICONS.update({
    "connexion": "M9 2v6M15 2v6M6 8h12v3a6 6 0 0 1-12 0zM12 17v5",
    "observation": "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
    "simulation": "M7 4l13 8-13 8z",
    "corpus": "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5M3 17l9 5 9-5",
    "journal": "M5 6h14M5 12h14M5 18h9",
    "back": "M15 18l-6-6 6-6",
})
ADVANCED_TABS = (("connexion", "Connexion"), ("observation", "Observation"), ("sorts", "Sorts"),
                 ("simulation", "Simulation"), ("corpus", "Corpus"), ("journal", "Journal"))
ADVANCED_LABELS = dict(ADVANCED_TABS)
HEROES = {
    "connexion": ("Fenêtre et calibration", "Vérifiez que DofBot2 voit bien le jeu, puis placez les zones de "
                  "l'interface. Le bot regarde l'écran, il ne clique jamais dans DOFUS."),
    "observation": ("Observation en combat", "Le bot lit la grille, les entités et le HUD sans agir. Utile pour "
                    "vérifier ce qu'il comprend avant de le laisser jouer."),
    "sorts": ("Scan de la barre de sorts", "Vérifiez la grille proposée, puis lancez le scan. Les sorts reconnus "
              "sont repris dans l'onglet Sorts de DofBot2."),
    "simulation": ("Simulation", "Rejoue un combat fictif avec vos sorts et votre stratégie, sans toucher au jeu."),
    "corpus": ("Corpus et annotation", "Choisissez une observation enregistrée, puis ouvrez l'outil d'annotation "
               "voulu. Chaque outil s'ouvre en plein écran dans cette fenêtre."),
    "journal": ("Journal", "Tous les événements du bot, de la connexion aux annotations."),
}
LIVE_MS = 800
POSITIVE = ("oui", "vrai", "visible", "ok", "true")


def value_color(text: str) -> str:
    lowered = text.strip().casefold()
    if not lowered or lowered.startswith(("inconnu", "—", "non disponible")):
        return t.TEXT_2
    return t.GREEN_LIGHT if lowered.startswith(POSITIVE) else t.TEXT


class WidgetBinding(SettingsBinding):
    """Relie une ligne d'accordéon à un contrôle de l'interface historique (case, compteur, liste)."""

    def __init__(self) -> None:
        self.fields: dict[str, tuple[Callable[[], object], Callable[[object], None]]] = {}

    def add(self, key: str, getter: Callable[[], object], setter: Callable[[object], None]) -> "WidgetBinding":
        self.fields[key] = (getter, setter)
        return self

    def checkbox(self, key: str, box) -> "WidgetBinding":
        return self.add(key, box.isChecked, lambda value: box.setChecked(bool(value)))

    def spin(self, key: str, spin, scale: float = 1.0) -> "WidgetBinding":
        return self.add(key, lambda: round(spin.value() * scale),
                        lambda value: spin.setValue(value / scale if scale != 1 else int(value)))

    def get(self, key: str, default: object = None) -> object:
        return self.fields[key][0]() if key in self.fields else default

    def set(self, key: str, value: object) -> None:
        self.fields[key][1](value)


class StorageSettings(SettingsBinding):
    """Réglages globaux historiques (table ``settings``)."""

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def get(self, key: str, default: object = None) -> object:
        value = self.storage.get_setting(key)
        return default if value is None else value

    def set(self, key: str, value: object) -> None:
        self.storage.set_setting(key, value)


class StrategyBinding(SettingsBinding):
    """Stratégie de la simulation : enregistrée à chaque changement."""

    MODES = {"Distance": StrategyMode.DISTANCE, "Corps-à-corps": StrategyMode.MELEE, "Survie": StrategyMode.SURVIVAL}
    TARGETS = {"Plus proche": TargetPriority.NEAREST, "Moins de PV": TargetPriority.LOWEST_HP}

    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def get(self, key: str, default: object = None) -> object:
        strategy = self.storage.load_strategy()
        if key == "mode":
            return next(name for name, mode in self.MODES.items() if mode == strategy.mode)
        if key == "target":
            return next(name for name, target in self.TARGETS.items() if target == strategy.target_priority)
        return getattr(strategy, key, default)

    def set(self, key: str, value: object) -> None:
        strategy = self.storage.load_strategy()
        values = {"mode": strategy.mode, "hp_threshold": strategy.hp_threshold,
                  "target_priority": strategy.target_priority, "reserve_ap": strategy.reserve_ap}
        if key == "mode":
            values["mode"] = self.MODES[str(value)]
        elif key == "target":
            values["target_priority"] = self.TARGETS[str(value)]
        else:
            values[key] = int(value)
        self.storage.save_strategy(Strategy(**values))


class FramePreview(QWidget):
    """Capture BGR affichée en entier dans un cadre arrondi ; emplacement hachuré sinon."""

    def __init__(self, placeholder: str, radius: int = 14, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.placeholder, self.radius = placeholder, radius
        self.image: QImage | None = None
        self._source_id: int | None = None

    def set_frame(self, image: np.ndarray | None) -> None:
        if image is None:
            if self.image is not None:
                self.image, self._source_id = None, None
                self.update()
            return
        if id(image) == self._source_id:
            return
        self._source_id = id(image)
        self.image = bgr_to_pixmap(image).toImage()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)
        painter.fillPath(path, QColor("#0f1510"))
        painter.save()
        painter.setClipPath(path)
        if self.image is not None:
            scaled = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF((self.width() - scaled.width()) / 2, (self.height() - scaled.height()) / 2,
                            scaled.width(), scaled.height())
            painter.drawImage(target, self.image)
        else:
            painter.setPen(QPen(t.green(0.05), 5.66))
            for offset in range(-self.height(), self.width() + self.height(), 16):
                painter.drawLine(QPointF(offset, self.height()), QPointF(offset + self.height(), 0))
            if self.width() > 80:
                painter.setPen(QColor(t.TEXT_3))
                painter.setFont(t.font(11, 500, mono=True))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.placeholder)
        painter.restore()
        painter.setPen(QPen(t.green(0.14) if self.width() > 80 else t.white(0.08), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)


class AdvHero(QWidget):
    """En-tête des pages avancées : titre 800 26px et texte 14px (max 620px)."""

    def __init__(self, key: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        title, text = HEROES[key]
        self.title = label(title, "d2HeroTitle", wrap=True)
        self.text = label(text, "d2PageText", wrap=True)
        layout.addWidget(self.title)
        layout.addWidget(self.text)

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        # Un QLabel à retour à la ligne limité en largeur sous-estime sa hauteur : on la fixe nous-mêmes.
        width = min(620, max(200, self.width()))
        self.text.setFixedSize(width, self.text.heightForWidth(width))
        super().resizeEvent(event)


class AdvancedPage(Page):
    def __init__(self, view: "AdvancedView", key: str) -> None:
        super().__init__(view, key, hero=AdvHero(key), max_width=920, margins=(24, 28, 24, 140), spacing=20)
        self.view = view
        self.legacy = view.legacy
        self._signature: object = None

    def live_update(self) -> None:
        """Rafraîchissement léger toutes les 800 ms tant que la page est visible."""
        signature = self.accordion_signature()
        if signature != self._signature:
            self._signature = signature
            self.build_accordions()

    def refresh(self) -> None:
        self._signature = None
        self.live_update()

    def accordion_signature(self) -> object:
        return None

    def build_accordions(self) -> None:
        """Construit les accordéons ; rappelé quand ``accordion_signature`` change."""


def _row(text: str, value: QWidget) -> QWidget:
    """Ligne 13px : libellé discret à gauche, valeur mono à droite, séparateur en haut."""
    row = QWidget()
    row.setObjectName("d2Row")
    row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 11, 0, 11)
    layout.setSpacing(12)
    layout.addWidget(label(text, "d2Banner"), 1)
    layout.addWidget(value)
    return row


def _set_value(widget, text: str, color: str | None = None) -> None:
    widget.setText(text)
    widget.setStyleSheet(f"color: {color or value_color(text)};")


# --- A1 Connexion ------------------------------------------------------------------------------------
class ConnexionPage(AdvancedPage):
    def __init__(self, view: "AdvancedView") -> None:
        super().__init__(view, "connexion")
        panel = self.legacy.client_panel
        box = card()
        grid = QHBoxLayout(box)
        grid.setContentsMargins(20, 20, 20, 20)
        grid.setSpacing(24)
        self.preview = FramePreview("aperçu de la capture")
        self.preview.setMinimumHeight(250)
        grid.addWidget(self.preview, 115)
        right = QVBoxLayout()
        right.setSpacing(14)
        window_row = QHBoxLayout()
        window_row.setSpacing(12)
        self.thumbnail = FramePreview("", 6)
        self.thumbnail.setFixedSize(44, 30)
        window_row.addWidget(self.thumbnail)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.window_title = label("Aucune fenêtre", "d2WindowTitle")
        self.window_meta = label("—", "d2MetaMono")
        texts.addWidget(self.window_title)
        texts.addWidget(self.window_meta)
        window_row.addLayout(texts, 1)
        change = button("Changer", "d2InfoAction")
        change.clicked.connect(self.view.window_change_requested)
        window_row.addWidget(change)
        right.addLayout(window_row)
        rows = QVBoxLayout()
        rows.setSpacing(0)
        self.capture_value = label("", "d2MonoValue")
        self.zones_value = label("", "d2MonoValue")
        self.profile_value = label("", "d2MonoValue")
        for caption, widget in (("Capture", self.capture_value), ("Zones calibrées", self.zones_value),
                                ("Profil lu", self.profile_value)):
            rows.addWidget(_row(caption, widget))
        right.addLayout(rows)
        right.addStretch(1)
        self.question = label("", "d2Question", wrap=True)
        right.addWidget(self.question)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.primary = button("", "d2Action")
        self.primary.clicked.connect(self._primary)
        self.secondary = button("", "d2ActionOutline")
        self.secondary.clicked.connect(self._secondary)
        actions.addWidget(self.primary)
        actions.addWidget(self.secondary)
        actions.addStretch(1)
        right.addLayout(actions)
        grid.addLayout(right, 100)
        self.content.addWidget(box)
        self.state: str | None = None   # forcé au premier affichage pour libeller les boutons
        panel.capture_confirmed.connect(self.live_update)

    def _connection_state(self) -> str:
        panel = self.legacy.client_panel
        if panel.connected_hwnd is None:
            return "none"
        return "confirmed" if panel.content_confirmed else "unconfirmed"

    def live_update(self) -> None:
        panel = self.legacy.client_panel
        frame = panel.frame
        self.preview.set_frame(frame.image if frame is not None else None)
        self.thumbnail.set_frame(frame.image if frame is not None else None)
        hwnd = panel.connected_hwnd
        title = panel.window_title.text()
        self.window_title.setText(title if hwnd is not None and title not in ("", "—") else "Aucune fenêtre connectée")
        meta = []
        if hwnd is not None:
            meta.append(f"hwnd 0x{hwnd:06X}")
            if frame is not None:
                meta.append(f"{frame.client.width}×{frame.client.height}")
            dpi = self.view.window_dpi(hwnd)
            if dpi:
                meta.append(f"DPI {dpi}")
        self.window_meta.setText(" · ".join(meta) or "Choisissez la fenêtre du jeu")
        state = self._connection_state()
        _set_value(self.capture_value, {"none": "non connectée", "unconfirmed": "à confirmer",
                                        "confirmed": "validée"}[state],
                   {"none": t.TEXT_2, "unconfirmed": t.ALERT, "confirmed": t.GREEN_LIGHT}[state])
        confirmed, total = self.view.zone_counts()
        _set_value(self.zones_value, f"{confirmed} / {total}", t.GREEN_LIGHT if confirmed == total else t.ALERT)
        name = panel.name.text().strip() or "nom inconnu"
        klass = panel.character_class.text().strip() or "classe inconnue"
        _set_value(self.profile_value, f"{name} · {klass}", t.TEXT)
        if state != self.state:
            self.state = state
            self._sync_actions()
        self.primary.setEnabled(state != "confirmed" or panel.calibrate_button.isEnabled())
        self.secondary.setEnabled(state != "confirmed" or panel.recognize_button.isEnabled())
        super().live_update()

    def _sync_actions(self) -> None:
        texts = {
            "none": ("Aucune fenêtre n'est connectée à cet outil.", "Connecter la fenêtre", "Choisir une fenêtre"),
            "unconfirmed": ("L'aperçu montre-t-il bien le jeu ?", "Oui, c'est DOFUS", "Non, autre fenêtre"),
            "confirmed": ("", "Calibrer les zones", "Lire le profil visible"),
        }[self.state]
        self.question.setText(texts[0])
        self.question.setVisible(bool(texts[0]))
        self.primary.setText(texts[1])
        self.secondary.setText(texts[2])

    def _primary(self) -> None:
        panel = self.legacy.client_panel
        if self.state == "none":
            window = self.view.game_window()
            if window is None or not panel.connect_to(window.hwnd):
                self.view.window_change_requested.emit()
        elif self.state == "unconfirmed":
            panel._confirm_capture()
        else:
            self.legacy._calibrate()

    def _secondary(self) -> None:
        if self.state == "confirmed":
            self.legacy._recognize_character()
        else:
            self.view.window_change_requested.emit()

    def accordion_signature(self) -> object:
        panel, data = self.legacy.client_panel, self.legacy.settings.gamedata_panel
        return (panel.name.text(), panel.character_class.text(), panel.hp_current.value(), panel.hp_max.value(),
                panel.ap.value(), panel.mp.value(), self._connection_state(), data.folder.text(), data.status.text(),
                self.legacy.connection_timer.isActive())

    def build_accordions(self) -> None:
        panel, data = self.legacy.client_panel, self.legacy.settings.gamedata_panel
        number = lambda spin: "inconnu" if spin.value() < 0 else str(spin.value())
        hp = "inconnu" if panel.hp_current.value() < 0 else f"{panel.hp_current.value()} / {number(panel.hp_max)}"
        diagnostics = WidgetBinding()
        diagnostics.add("recheck", self.legacy.connection_timer.isActive, self.view.set_recheck)
        profile_id = panel.profile_id
        auto = JsonSettings(self.view.storage, "dofbot2_advanced", profile_id) if profile_id is not None else None
        rows = [
            switch_row(diagnostics, "recheck", "Revérifier la fenêtre toutes les 2 s",
                       "Détache la fenêtre si elle change de taille ou se ferme"),
        ]
        if auto is not None:
            rows.append(switch_row(auto, "auto_confirm_window", "Reconnaître la fenêtre déjà confirmée",
                                   "Même titre et même taille que la dernière fois"))
        rows.append(info_row("Détacher la fenêtre", "", self.legacy._disconnect_client, "Déconnecter",
                             "Une vérification sera demandée avant de reconnecter", outlined=True))
        folder = data.folder.text().strip()
        self.set_accordions([
            ("prof", "Profil lu à l'écran", "Les informations non lisibles restent inconnues", [
                info_row("Nom", panel.name.text().strip() or "inconnu"),
                info_row("Classe", panel.character_class.text().strip() or "inconnue"),
                info_row("Points de vie", hp),
                info_row("PA / PM", f"{number(panel.ap)} / {number(panel.mp)}"),
                info_row("Enregistrer le profil", "", panel._save_profile, "Enregistrer",
                         "Met à jour le profil DofBot2 actif", outlined=True),
            ], False, True),
            ("diag", "Diagnostic", "Surveillance de la fenêtre", rows, False, True),
            ("data", "Données du client DOFUS", data.status.text(), [
                info_row("Dossier du client", folder[-38:] if folder else "non configuré", data._browse, "Choisir…",
                         outlined=True),
                info_row("Analyser les fichiers", "", data._analyze, "Analyser", "Lecture seule, rien n'est modifié",
                         outlined=True),
                info_row("Outils GameData", "", self.view.open_gamedata_tools, "Ouvrir",
                         "Valider toutes les maps, inspecter ou exporter une map", outlined=True),
            ], False, True),
        ])


# --- A2 Observation ----------------------------------------------------------------------------------
GRID_OVERLAYS = (("grid", "Grille"), ("cell_ids", "Cell IDs"), ("coordinates", "Coordonnées"),
                 ("walkability", "Walkability"), ("los", "LOS"), ("red_blue", "Rouge / bleu"),
                 ("alignment_debug", "Alignement"))
ENTITY_OVERLAYS = (("entity_rois", "ROIs"), ("player_evidence", "Preuves joueur"), ("enemy_evidence", "Preuves ennemis"),
                   ("track_ids", "IDs de piste"), ("occluded_tracks", "Pistes occultées"),
                   ("background_delta", "Fond (FREE)"), ("occupancy_states", "Occupation"))
SIGNALS = (("combat", "Combat"), ("turn", "Mon tour"), ("ap", "PA"), ("mp", "PM"), ("map", "Map"))


def overlay_choices(combat, overlays) -> Choices:
    names = dict(overlays)
    selected = [names[key] for key, _name in overlays if combat.overlay_boxes[key].isChecked()]
    choices = Choices([name for _key, name in overlays], selected, multi=True)
    choices.setMaximumWidth(760)

    def changed(values) -> None:
        for key, name in overlays:
            combat.overlay_boxes[key].setChecked(name in values)

    choices.changed.connect(changed)
    return choices


class ObservationPage(AdvancedPage):
    def __init__(self, view: "AdvancedView") -> None:
        super().__init__(view, "observation")
        combat = self.legacy.combat
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(14)
        overlay_row = QHBoxLayout()
        overlay_row.setSpacing(10)
        caps = label("OVERLAY", "d2Caps")
        overlay_row.addWidget(caps, 0, Qt.AlignmentFlag.AlignTop)
        overlay_row.addSpacing(4)
        self.overlays = overlay_choices(combat, GRID_OVERLAYS)
        overlay_row.addWidget(self.overlays, 1)
        layout.addLayout(overlay_row)
        # L'aperçu historique est repris tel quel : clics (joueur) et survol (cellule) restent gérés par lui.
        self.canvas = QWidget()
        self.canvas.setObjectName("d2Canvas")
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.canvas.setMinimumHeight(340)
        canvas_layout = QVBoxLayout(self.canvas)
        canvas_layout.setContentsMargins(1, 1, 1, 1)
        preview = combat.observation_preview
        preview.setParent(self.canvas)
        preview.setStyleSheet("background: transparent; border: none; color: #6f7f71;")
        canvas_layout.addWidget(preview)
        preview.show()
        self.hover = label("", "d2HoverPill")
        self.hover.setParent(self.canvas)
        layout.addWidget(self.canvas)
        tiles = QGridLayout()
        tiles.setSpacing(8)
        self.signal_values: dict[str, object] = {}
        for index, (key, caption) in enumerate(SIGNALS):
            tile = QWidget()
            tile.setObjectName("d2Tile")
            tile.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(12, 10, 12, 10)
            tile_layout.setSpacing(4)
            tile_layout.addWidget(label(caption, "d2TileLabel"))
            value = label("inconnu", "d2TileValue")
            tile_layout.addWidget(value)
            tiles.addWidget(tile, 0, index)
            tiles.setColumnStretch(index, 1)
            self.signal_values[key] = value
        layout.addLayout(tiles)
        checklist = combat.checklist
        visible = checklist.isVisibleTo(combat)
        checklist.setParent(box)
        checklist.setObjectName("d2Checklist")
        checklist.setStyleSheet("")
        layout.addWidget(checklist)
        checklist.setVisible(visible)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.toggle = button("Démarrer l'observation", "d2Action")
        self.toggle.clicked.connect(self._toggle)
        self.player = button("Voici mon personnage", "d2ActionOutline")
        self.player.setCheckable(True)
        self.player.setToolTip("Activez puis cliquez sur le marqueur de votre personnage dans l'aperçu")
        self.player.clicked.connect(lambda: combat.select_player.click())
        self.save = button("Enregistrer cette observation", "d2Link")
        self.save.clicked.connect(combat.observation_save.click)
        actions.addWidget(self.toggle)
        actions.addWidget(self.player)
        actions.addStretch(1)
        actions.addWidget(self.save)
        layout.addLayout(actions)
        self.content.addWidget(box)

    def refresh(self) -> None:
        combat = self.legacy.combat
        if combat.mode.currentIndex() != 1:
            combat.mode.setCurrentIndex(1)   # vision réelle
        super().refresh()

    @property
    def observing(self) -> bool:
        return self.legacy.combat.observation_stop.isEnabled()

    def _toggle(self) -> None:
        combat = self.legacy.combat
        (combat.observation_stop if self.observing else combat.observation_start).click()
        self.live_update()

    def live_update(self) -> None:
        combat = self.legacy.combat
        for key, value in self.signal_values.items():
            text = combat.real_values[key].text().split("\n")[0]
            _set_value(value, text if text else "inconnu")
            value.setToolTip(combat.real_values[key].text())
        observing = self.observing
        self.toggle.setText("Arrêter l'observation" if observing else "Démarrer l'observation")
        self.toggle.setProperty("running", observing)
        repolish(self.toggle)
        self.player.setEnabled(combat.select_player.isEnabled())
        self.player.setChecked(combat.select_player.isChecked())
        self.save.setEnabled(combat.observation_save.isEnabled())
        hover = combat.hover_info.text()
        self.hover.setText(hover if len(hover) < 90 else hover[:87] + "…")
        self.hover.adjustSize()
        self.hover.move(12, self.canvas.height() - self.hover.height() - 12)
        self.hover.raise_()
        super().live_update()
        self._update_details()

    def _detail_rows(self) -> list[QWidget]:
        combat = self.legacy.combat
        self.detail_values = {}
        rows = []
        for key, value in combat.real_values.items():
            row = info_row(_form_caption(value) or key, value.text() or "Inconnu")
            self.detail_values[key] = row.control.layout().itemAt(0).widget()
            rows.append(row)
        self.signals_label = label(combat.real_signals.toPlainText() or "Aucun signal visuel pour l'instant.",
                                   "d2RowDesc", wrap=True)
        self.signals_label.setContentsMargins(0, 10, 0, 10)
        rows.append(self.signals_label)
        return rows

    def _update_details(self) -> None:
        combat = self.legacy.combat
        for key, pill in getattr(self, "detail_values", {}).items():
            text = combat.real_values[key].text() or "Inconnu"
            pill.setText(text if len(text) < 60 else text[:57] + "…")
            pill.setToolTip(text)
            pill.setStyleSheet(f"color: {value_color(text)};")
            pill.setVisible(True)
        if hasattr(self, "signals_label"):
            self.signals_label.setText(combat.real_signals.toPlainText() or "Aucun signal visuel pour l'instant.")

    def accordion_signature(self) -> object:
        combat = self.legacy.combat
        return (combat.real_values["map"].text(), combat.real_values["map_coords"].text(),
                combat.map_auto.isVisible(), combat.sequence_split.isEnabled(), self.observing)

    def build_accordions(self) -> None:
        combat = self.legacy.combat
        values = combat.real_values
        switches = (WidgetBinding().checkbox("verified", combat.map_id_verified)
                    .checkbox("legacy", combat.legacy_fallback).checkbox("sequence", combat.sequence_capture))
        splits = [combat.sequence_split.itemText(index).replace("Split : ", "")
                  for index in range(combat.sequence_split.count())]
        switches.add("split", lambda: combat.sequence_split.currentText().replace("Split : ", ""),
                     lambda value: combat.sequence_split.setCurrentIndex(splits.index(str(value))))
        manual = WidgetBinding().add("map_id", combat.map_id_input.text, combat.map_id_input.setText)
        map_rows = [
            info_row("Map actuelle", values["map"].text() or "inconnue"),
            input_row(manual, "map_id", "mapId manuel (secours)", "ex. 153880322"),
            info_row("Utiliser ce mapId", "", combat.map_load.click, "Utiliser", "En secours si la détection échoue",
                     outlined=True),
            switch_row(switches, "verified", "Vérifié par /mapid", "Cochez si vous avez tapé /mapid dans le chat", False),
        ]
        if combat.map_auto.isVisible():
            map_rows.append(info_row("Revenir à la détection automatique", "", combat.map_auto.click, "Revenir",
                                     outlined=True))
        split_row = choice_row(switches, "split", "Split du combat", "Fixé au démarrage de l'observation", splits,
                               splits[0])
        split_row.control.setEnabled(combat.sequence_split.isEnabled())
        coordinates = values["map_coords"].text()
        self.set_accordions([
            ("map", "Map", f"Détectée automatiquement pendant l'observation · {coordinates}"
             if coordinates and coordinates != "Inconnu" else "Détectée automatiquement pendant l'observation",
             map_rows, False, True),
            ("grid", "Grille", "Projection et vérification de la grille", [
                info_row("Projection de grille", "", combat.projection_calibrate.click, "Ouvrir",
                         "Aligne la grille isométrique sur l'écran", outlined=True),
                info_row("Recette de grille", "", combat.grid_recipe.click, "Ouvrir",
                         "Juge la grille GameData sur une capture réelle", outlined=True),
                switch_row(switches, "legacy", "Autoriser la grille historique en secours",
                           "Utilisée uniquement si GameData est indisponible", False),
            ], False, True),
            ("details", "Détails de l'observation", "Toutes les valeurs lues à chaque image", self._detail_rows(),
             False, True),
            ("ent", "Entités", "Suivi des personnages sur la grille", [
                _choices_row("Afficher", overlay_choices(combat, ENTITY_OVERLAYS)),
                switch_row(switches, "sequence", "Enregistrer la séquence dans le corpus",
                           "Entités en lecture seule, pour annotation plus tard", False),
                split_row,
            ], False, True),
        ])


def _form_caption(widget: QWidget) -> str | None:
    """Libellé d'un champ de formulaire historique (QFormLayout imbriqué dans la carte)."""
    from PySide6.QtWidgets import QFormLayout
    parent = widget.parentWidget()
    pending = [parent.layout()] if parent is not None and parent.layout() is not None else []
    while pending:
        layout = pending.pop()
        if isinstance(layout, QFormLayout):
            caption = layout.labelForField(widget)
            if caption is not None:
                return caption.text()
        for index in range(layout.count()):
            child = layout.itemAt(index).layout()
            if child is not None:
                pending.append(child)
    return None


def _choices_row(title: str, control: QWidget) -> QWidget:
    return SettingRow(title, "", control)


# --- A3 Scan des sorts -------------------------------------------------------------------------------
class ScanTile(HoverButton):
    """Icône scannée : 1:1 r12 ; non confirmée = opacité .35 et anneau orange ; sélection = anneau vert."""

    def __init__(self, spell_id: int | None, icon_png: bytes | None, status: str | None, size: int) -> None:
        super().__init__()
        self.spell_id, self.status = spell_id, status
        self.setCheckable(spell_id is not None)
        self.setEnabled(spell_id is not None)
        self.setFixedSize(size, size)
        image = QImage.fromData(icon_png) if icon_png else QImage()
        self.pixmap = None if image.isNull() else rounded_pixmap(image, QSize(size - 4, size - 4), 11)
        self.setToolTip(status or "Case vide")

    @property
    def pending(self) -> bool:
        return self.status in ("À vérifier", "Inconnu")

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        icon = QRectF(2, 2, self.width() - 4, self.height() - 4)
        painter.setOpacity(0.35 if self.pending else 1.0)
        if self.pixmap is not None:
            painter.drawPixmap(icon.toRect(), self.pixmap)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.SURFACE_2))
            painter.drawRoundedRect(icon, 12, 12)
        painter.setOpacity(1.0)
        ring = QColor(t.GREEN) if self.isChecked() else QColor(t.ALERT) if self.pending else None
        if ring is not None:
            painter.setPen(QPen(ring, 2 if self.isChecked() else 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(icon.adjusted(-1, -1, 1, 1), 13, 13)
        elif self.hovered and self.spell_id is not None:
            painter.setPen(QPen(t.white(0.25), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(icon, 12, 12)


class SpellScanPage(AdvancedPage):
    GRID_WIDTH = 832

    def __init__(self, view: "AdvancedView") -> None:
        super().__init__(view, "sorts")
        scan = self.legacy.scan_panel
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(16)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.summary = label("", "d2Banner", wrap=True)
        top.addWidget(self.summary, 1)
        top.addWidget(label("Lignes", "d2UseLabel"))
        self.rows = Stepper(scan.rows.value(), scan.rows.minimum(), scan.rows.maximum())
        self.rows.value_changed.connect(scan.rows.setValue)
        top.addWidget(self.rows)
        top.addWidget(label("Colonnes", "d2UseLabel"))
        self.columns = Stepper(scan.columns.value(), 4, 14)
        self.columns.value_changed.connect(scan.columns.setValue)
        top.addWidget(self.columns)
        layout.addLayout(top)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.grid_host)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        scan_button = button("Scanner mes sorts", "d2Action")
        scan_button.clicked.connect(scan.request_scan)
        self.tooltip = button("Analyser l'infobulle", "d2ActionOutline")
        self.tooltip.clicked.connect(self._tooltip)
        actions.addWidget(scan_button)
        actions.addWidget(self.tooltip)
        actions.addWidget(label("Sélectionnez un sort, puis survolez-le dans le jeu pendant le décompte.", "d2Note"))
        actions.addStretch(1)
        layout.addLayout(actions)
        self.content.addWidget(box)
        self.tiles: list[ScanTile] = []
        self._grid_signature: object = None
        self._countdown = 0
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._tick)
        scan.scan_finished.connect(lambda _count: self.refresh())

    def _spells(self) -> list:
        scan = self.legacy.scan_panel
        if scan.profile_id is None:
            return []
        return self.view.storage.list_profile_spells(scan.profile_id, page=scan.page.value())

    def live_update(self) -> None:
        scan = self.legacy.scan_panel
        self.rows.setValue(scan.rows.value())
        self.columns.setValue(scan.columns.value())
        spells = self._spells()
        signature = (scan.rows.value(), scan.columns.value(), scan.page.value(), scan.current_spell_id,
                     tuple((row["id"], row["slot"], row["status"], row["visual_hash"]) for row in spells))
        if signature != self._grid_signature:
            self._grid_signature = signature
            self._build_grid(spells)
        recognized = sum(row["status"] in ("Confirmé", "Reconnu") for row in spells)
        pending = len(spells) - recognized
        grid = f"grille {scan.rows.value()} × {scan.columns.value()}"
        self.summary.setText(f"{recognized} sort{'s' if recognized > 1 else ''} reconnu{'s' if recognized > 1 else ''}"
                             f" · {pending} à vérifier · {grid}" if spells else f"Grille à vérifier avant le scan · {grid}")
        super().live_update()

    def _build_grid(self, spells) -> None:
        scan = self.legacy.scan_panel
        for tile in self.tiles:
            self.grid.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        self.tiles = []
        columns, rows = scan.columns.value(), scan.rows.value()
        size = max(36, min(64, (self.GRID_WIDTH - 8 * (columns - 1)) // columns))
        by_slot = {int(row["slot"]): row for row in spells}
        for index in range(rows * columns):
            row = by_slot.get(index + 1)
            tile = ScanTile(int(row["id"]) if row else None, row["icon_png"] if row else None,
                            row["status"] if row else None, size)
            tile.setChecked(row is not None and int(row["id"]) == scan.current_spell_id)
            if row is not None:
                tile.clicked.connect(lambda _checked=False, spell_id=int(row["id"]): self.select(spell_id))
            self.grid.addWidget(tile, index // columns, index % columns)
            self.tiles.append(tile)

    def select(self, spell_id: int) -> None:
        self.legacy.scan_panel.reload(select_id=spell_id)
        self.live_update()

    def _tooltip(self) -> None:
        scan = self.legacy.scan_panel
        if scan.current_spell_id is None:
            self.view.notify("Choisissez un sort", "Touchez son icône dans la grille, puis relancez l'analyse.", t.ALERT)
            return
        if self._countdown:
            return
        scan._request_tooltip()
        self._countdown = 3
        self._tick(first=True)
        self._countdown_timer.start()

    def _tick(self, first: bool = False) -> None:
        if not first:
            self._countdown -= 1
        if self._countdown <= 0:
            self._countdown_timer.stop()
            self.tooltip.setText("Analyser l'infobulle")
            return
        self.tooltip.setText(f"Survolez un sort… {self._countdown} s")

    def accordion_signature(self) -> object:
        scan = self.legacy.scan_panel
        spells = tuple((spell.id, spell.name, spell.ap_cost, spell.min_range, spell.max_range)
                       for spell in self.view.storage.list_spells())
        current = self.view.storage.get_profile_spell(scan.current_spell_id) if scan.current_spell_id else None
        return (spells, tuple(current) if current is not None else None, scan.page.value())

    def build_accordions(self) -> None:
        scan = self.legacy.scan_panel
        options = WidgetBinding().spin("page", scan.page).spin("threshold", scan.threshold, 100)
        selected = []
        if scan.current_spell_id is not None:
            row = self.view.storage.get_profile_spell(scan.current_spell_id)
            cost = "?" if row["ap_cost"] is None else row["ap_cost"]
            reach = "?" if row["min_range"] is None else f"{row['min_range']}–{row['max_range']}"
            selected = [
                info_row(row["name"] or f"Emplacement {row['slot']}", f"{cost} PA · portée {reach}"),
                info_row("Statut", str(row["status"])),
                info_row("Valider ce sort", "", self._confirm, "Valider", "Les valeurs lues deviennent la référence",
                         outlined=True),
                info_row("Ignorer cet emplacement", "", self._ignore, "Ignorer", "Supprime la lecture automatique",
                         outlined=True),
            ]
        simulated = [info_row(spell.name, f"{spell.ap_cost} PA · portée {spell.min_range}–{spell.max_range}"
                              + (" · ligne de vue" if spell.line_of_sight else "")
                              + f" · {spell.per_turn} par tour", self.view.open_simulated_spells, "Modifier",
                              outlined=True)
                     for spell in self.view.storage.list_spells()]
        simulated.append(info_row("Nouveau sort simulé", "", self.view.open_simulated_spells, "Ajouter",
                                  outlined=True))
        editor = info_row("Éditeur complet", "", self.view.open_scan_editor, "Ouvrir",
                          "Nom, effets, dégâts, texte OCR, validation de plusieurs sorts", outlined=True)
        specs = []
        selected.append(editor)
        specs.append(("selected", "Sort sélectionné" if scan.current_spell_id is not None else "Sorts scannés",
                      "Vérifiez la lecture avant de la valider", selected, scan.current_spell_id is not None, False))
        specs += [
            ("simSpells", "Sorts simulés", "Utilisés par la simulation, indépendants du scan", simulated, False, True),
            ("scanOpt", "Options du scan", "", [
                step_row(options, "page", "Page de la barre", "", 1, 1, 30),
                step_row(options, "threshold", "Seuil de reconnaissance", "Ressemblance minimale avec un sort connu",
                         88, 50, 100, 1, "%"),
            ], False, True),
        ]
        self.set_accordions(specs)

    def _confirm(self) -> None:
        self.legacy.scan_panel._confirm_current()
        self.refresh()

    def _ignore(self) -> None:
        self.legacy.scan_panel._ignore_current()
        self.refresh()


# --- A4 Simulation -----------------------------------------------------------------------------------
class SimulationPage(AdvancedPage):
    def __init__(self, view: "AdvancedView") -> None:
        super().__init__(view, "simulation")
        status_card = card()
        row = QHBoxLayout(status_card)
        row.setContentsMargins(24, 20, 24, 20)
        row.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(6)
        texts.addWidget(label("COMBAT SIMULÉ", "d2Caps"))
        self.status = label("Arrêté", "d2PlanName")
        texts.addWidget(self.status)
        row.addLayout(texts, 1)
        self.pause = button("Pause", "d2ActionOutline")
        self.pause.clicked.connect(lambda: self.legacy.dashboard.pause_button.click())
        self.toggle = button("Démarrer la simulation", "d2Action")
        self.toggle.clicked.connect(self._toggle)
        row.addWidget(self.pause)
        row.addWidget(self.toggle)
        self.content.addWidget(status_card)
        stats = QGridLayout()
        stats.setSpacing(10)
        self.stats: dict[str, object] = {}
        for index, (key, caption) in enumerate((("combats", "Combats"), ("victories", "Victoires"), ("xp", "XP"),
                                                ("kamas", "Kamas"))):
            tile = QWidget()
            tile.setObjectName("d2StatTile")
            tile.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(16, 14, 16, 14)
            tile_layout.setSpacing(4)
            tile_layout.addWidget(label(caption, "d2StatLabel"))
            value = label("0", "d2StatValue")
            tile_layout.addWidget(value)
            stats.addWidget(tile, 0, index)
            stats.setColumnStretch(index, 1)
            self.stats[key] = value
        self.content.addLayout(stats)
        console = card()
        self.console_layout = QVBoxLayout(console)
        self.console_layout.setContentsMargins(24, 8, 24, 8)
        self.console_layout.setSpacing(0)
        caps = label("CONSOLE", "d2Caps")
        caps.setContentsMargins(0, 14, 0, 6)
        self.console_layout.addWidget(caps)
        self.console_rows: list[QWidget] = []
        self.content.addWidget(console)
        self.lines: list[tuple[str, str, str]] = []
        self.legacy.controller.event_ready.connect(self._event)
        self.legacy.controller.status_ready.connect(lambda status: self._line(f"État : {status}", t.TEXT_2))
        self._render_console()

    @property
    def running(self) -> bool:
        return self.status.text() in ("En cours", "En pause")

    def _toggle(self) -> None:
        dashboard = self.legacy.dashboard
        (dashboard.stop_button if self.running else dashboard.start_button).click()
        self.live_update()

    def _event(self, event) -> None:
        color = t.ALERT if event.level == "WARNING" else "#e0677e" if event.level == "ERROR" else t.TEXT
        self._line(event.message, color)

    def _line(self, text: str, color: str) -> None:
        self.lines.insert(0, (datetime.now().strftime("%H:%M:%S"), text, color))
        del self.lines[7:]
        self._render_console()

    def _render_console(self) -> None:
        for row in self.console_rows:
            self.console_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.console_rows = []
        for time_text, text, color in self.lines or [("—", "Aucun combat simulé pour l'instant", t.TEXT_2)]:
            row = QWidget()
            row.setObjectName("d2LogRow")
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 10, 0, 10)
            layout.setSpacing(16)
            stamp = label(time_text, "d2LogTime")
            stamp.setFixedWidth(62)
            message = label(text, "d2LogText", wrap=True)
            message.setStyleSheet(f"color: {color};")
            layout.addWidget(stamp, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(message, 1)
            self.console_layout.addWidget(row)
            self.console_rows.append(row)

    def live_update(self) -> None:
        status = self.legacy.dashboard.values["status"].text()
        self.status.setText(status)
        self.status.setStyleSheet(f"color: {t.GREEN if status == 'En cours' else t.ALERT if status == 'En pause' else t.TEXT};")
        running = self.running
        self.pause.setVisible(running)
        self.pause.setText("Reprendre" if status == "En pause" else "Pause")
        self.toggle.setText("Arrêter" if running else "Démarrer la simulation")
        self.toggle.setProperty("running", running)
        repolish(self.toggle)
        statistics = self.view.storage.statistics()
        for key, value in self.stats.items():
            value.setText(f"{statistics[key]:,}".replace(",", " "))
        super().live_update()

    def build_accordions(self) -> None:
        strategy, settings = StrategyBinding(self.view.storage), StorageSettings(self.view.storage)

        def player_changed(_value=None) -> None:
            self.legacy.dashboard.values["character"].setText(str(settings.get("player_name", "")))

        player = input_row(settings, "player_name", "Personnage", "Personnage test",
                           lambda text: None if text.strip() else "Nom requis")
        player.control.editingFinished.connect(player_changed)
        self.set_accordions([
            ("strat", "Stratégie", "Choix des cibles et style de combat", [
                choice_row(strategy, "mode", "Approche", "", tuple(StrategyBinding.MODES), "Distance"),
                choice_row(strategy, "target", "Cible", "", tuple(StrategyBinding.TARGETS), "Plus proche"),
                step_row(strategy, "hp_threshold", "Seuil de PV (survie)", "", 35, 0, 100, 5, "%"),
                step_row(strategy, "reserve_ap", "PA à réserver", "", 0, 0, 20),
            ], True, True),
            ("simSet", "Paramètres", "Pris en compte au prochain combat simulé", [
                player,
                step_row(settings, "tick_ms", "Délai entre étapes", "", 350, 50, 2000, 50, "ms"),
            ], False, True),
            ("simMore", "Détails", "Historique et déroulé du combat simulé", [
                info_row("Historique des combats", "", self.view.open_statistics, "Ouvrir",
                         "Date, résultat, tours, XP et kamas", outlined=True),
                info_row("Combat simulé", "", self.view.open_simulated_combat, "Ouvrir",
                         "Grille, acteurs et historique des actions", outlined=True),
            ], False, True),
        ])

    def accordion_signature(self) -> object:
        return "static"


# --- A5 Corpus ---------------------------------------------------------------------------------------
class RadioDot(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.checked = False

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(t.GREEN if self.checked else "#3a453c"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(1, 1, 14, 14))
        if self.checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.GREEN))
            painter.drawEllipse(QRectF(5, 5, 6, 6))


def entry_tag(repository, entry) -> tuple[str, str]:
    if entry.annotation_available:
        try:
            annotation = repository.read_annotation(entry)
        except ValueError:
            annotation = None
        if annotation is not None and getattr(annotation, "human_confirmed", False):
            return "COMPLET", t.GREEN_LIGHT
        return "ANNOTÉE", "#5cc4d6"
    return "À ANNOTER", t.ALERT


CORPUS_TOOLS = (
    ("annot", "Annotation", "Combat, tour, PA/PM et points de référence"),
    ("hud", "Revue HUD PA/PM", "Confirmez ou corrigez la lecture du bot"),
    ("entites", "Entités", "Placez joueur et ennemis par cell ID"),
    ("phase", "Phase et tour", "Placement, combat, fin et numéro de tour"),
    ("collecte", "Collecte HUD réelle", "Capture PA/PM en direct pendant vos combats"),
    ("recette", "Recette de grille", "Jugez la grille GameData sur une capture"),
)


class CorpusToolsPage(AdvancedPage):
    LIMIT = 200

    def __init__(self, view: "AdvancedView") -> None:
        super().__init__(view, "corpus")
        box = card()
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(8, 8, 8, 8)
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.list_host = QWidget()
        self.list_host.setObjectName("d2Screen")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(2)
        self.list_scroll.setWidget(self.list_host)
        box_layout.addWidget(self.list_scroll)
        self.content.addWidget(box)
        self.corpus_summary = label("", "d2Note", wrap=True)
        self.content.addWidget(self.corpus_summary)
        self.selected_caps = label("ANNOTER", "d2Caps")
        self.selected_caps.setContentsMargins(4, 4, 4, 0)
        self.content.addWidget(self.selected_caps)
        tools = QGridLayout()
        tools.setSpacing(10)
        self.tool_counts: dict[str, object] = {}
        for index, (key, title, desc) in enumerate(CORPUS_TOOLS):
            tool = button("", "d2ToolCard")
            tool.setMinimumHeight(88)
            tool_layout = QVBoxLayout(tool)
            tool_layout.setContentsMargins(18, 16, 18, 16)
            tool_layout.setSpacing(4)
            head = QHBoxLayout()
            head.addWidget(label(title, "d2CardTitle"), 1)
            count = label("", "d2CardCount")
            head.addWidget(count)
            tool_layout.addLayout(head)
            tool_layout.addWidget(label(desc, "d2RowDesc", wrap=True))
            tool_layout.addStretch(1)
            for child in tool.findChildren(QWidget):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            tool.clicked.connect(lambda _checked=False, key=key: self.open_tool(key))
            tools.addWidget(tool, index // 3, index % 3)
            tools.setColumnStretch(index % 3, 1)
            self.tool_counts[key] = count
        self.content.addLayout(tools)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.items: dict[str, tuple[QPushButton, RadioDot]] = {}
        self.selected_id: str | None = None

    def refresh(self) -> None:
        corpus = self.legacy.corpus
        corpus.refresh()
        for item, _dot in self.items.values():
            self.group.removeButton(item)
            self.list_layout.removeWidget(item)
            item.hide()
            item.deleteLater()
        self.items = {}
        try:
            entries = list(corpus.repository.list_entries())
        except ValueError as exc:
            entries = []
            self.corpus_summary.setText(f"Manifeste illisible : {exc}")
        else:
            self.corpus_summary.setText(corpus.summary.text())
        entries = entries[::-1][:self.LIMIT]   # plus récentes d'abord
        for entry in entries:
            self._add_item(entry)
        if not entries:
            empty = label("Aucune observation enregistrée. Utilisez « Enregistrer cette observation » dans "
                          "l'onglet Observation, ou importez un debug.", "d2Banner", wrap=True)
            empty.setContentsMargins(14, 12, 14, 12)
            button_like = QPushButton()
            button_like.setObjectName("d2ListItem")
            button_like.setEnabled(False)
            layout = QVBoxLayout(button_like)
            layout.addWidget(empty)
            button_like.setMinimumHeight(60)
            self.list_layout.addWidget(button_like)
            self.items["__empty__"] = (button_like, RadioDot())
        rows = max(1, len(entries))
        self.list_scroll.setFixedHeight(min(300, rows * 46 + (rows - 1) * 2 + (14 if not entries else 0)))
        pending = sum(not entry.annotation_available for entry in entries)
        self.tool_counts["annot"].setText(f"{pending} à annoter" if pending else "")
        ids = [entry.observation_id for entry in entries]
        if self.selected_id not in ids:
            self.selected_id = ids[0] if ids else None
        if self.selected_id is not None:
            self.select(self.selected_id)
        else:
            self.selected_caps.setText("ANNOTER")
        super().refresh()

    def _add_item(self, entry) -> None:
        item = QPushButton()
        item.setObjectName("d2ListItem")
        item.setCheckable(True)
        item.setCursor(Qt.CursorShape.PointingHandCursor)
        item.setMinimumHeight(44)
        layout = QHBoxLayout(item)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(16)
        dot = RadioDot()
        layout.addWidget(dot)
        identifier = label(entry.observation_id, "d2MonoValue")
        identifier.setFixedWidth(210)
        layout.addWidget(identifier)
        meta = [f"session {entry.session_id}", f"frame {entry.frame_index}"]
        if entry.map_name:
            meta.append(entry.map_name)
        meta.append(entry.usage)
        layout.addWidget(label(" · ".join(meta), "d2Banner"), 1)
        tag, color = entry_tag(self.legacy.corpus.repository, entry)
        tag_label = label(tag, "d2Tag")
        tag_label.setStyleSheet(f"color: {color};")
        layout.addWidget(tag_label)
        for child in item.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        item.clicked.connect(lambda _checked=False, identifier=entry.observation_id: self.select(identifier))
        self.group.addButton(item)
        self.list_layout.addWidget(item)
        self.items[entry.observation_id] = (item, dot)

    def select(self, observation_id: str) -> None:
        self.selected_id = observation_id
        for identifier, (item, dot) in self.items.items():
            item.setChecked(identifier == observation_id)
            dot.checked = identifier == observation_id
            dot.update()
        corpus = self.legacy.corpus
        for index in range(corpus.list.count()):
            if corpus.list.item(index).data(Qt.ItemDataRole.UserRole) == observation_id:
                corpus.list.setCurrentRow(index)
                break
        self.selected_caps.setText(f"ANNOTER · {observation_id}")

    def open_tool(self, key: str) -> None:
        corpus = self.legacy.corpus
        actions = {
            "annot": corpus._annotate, "hud": corpus._hud_review, "entites": corpus._annotate_entities,
            "phase": corpus._annotate_combat_state, "collecte": corpus.hud_collection_requested.emit,
            "recette": self.legacy.combat.grid_recipe.click,
        }
        actions[key]()
        if key not in ("collecte", "recette"):
            self.refresh()

    def accordion_signature(self) -> object:
        return self.selected_id

    def build_accordions(self) -> None:
        corpus = self.legacy.corpus

        def after(action) -> Callable[[], None]:
            def run() -> None:
                action()
                self.refresh()
            return run

        self.set_accordions([
            ("base", "Baseline et fixtures", "Import, mesures et tests", [
                info_row("Importer un debug", "", after(corpus._import), "Importer",
                         "Ajoute un dossier d'observation au corpus", outlined=True),
                info_row("Calculer la baseline", "", after(corpus._benchmark), "Calculer",
                         "Compare le bot à la vérité humaine", outlined=True),
                info_row("Promouvoir en fixture de test", "", after(corpus._promote), "Promouvoir",
                         f"Observation sélectionnée : {self.selected_id or 'aucune'}", outlined=True),
            ], False, True),
        ])


# --- A6 Journal --------------------------------------------------------------------------------------
LEVEL_FILTERS = {"Tout": None, "Info": ("INFO",), "Alertes": ("WARNING", "WARN"), "Erreurs": ("ERROR", "CRITICAL")}
LEVEL_COLORS = {"INFO": t.TEXT, "WARNING": t.ALERT, "WARN": t.ALERT, "ERROR": "#e0677e", "CRITICAL": "#e0677e"}


class JournalPage(AdvancedPage):
    LIMIT = 120

    def __init__(self, view: "AdvancedView") -> None:
        super().__init__(view, "journal")
        box = card()
        self.box_layout = QVBoxLayout(box)
        self.box_layout.setContentsMargins(24, 8, 24, 8)
        self.box_layout.setSpacing(0)
        self.filters = Choices(tuple(LEVEL_FILTERS), "Tout")
        self.filters.setContentsMargins(0, 12, 0, 10)
        self.filters.changed.connect(lambda _value: self._render(force=True))
        self.box_layout.addWidget(self.filters)
        self.rows: list[QWidget] = []
        self.content.addWidget(box)
        self._last_id: int | None = None

    def live_update(self) -> None:
        self._render()

    def refresh(self) -> None:
        self._render(force=True)

    def _render(self, force: bool = False) -> None:
        events = self.view.storage.recent_events(self.LIMIT)
        last = int(events[-1]["id"]) if events else None
        if not force and last == self._last_id:
            return
        self._last_id = last
        levels = LEVEL_FILTERS.get(str(self.filters.value()))
        for row in self.rows:
            self.box_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        shown = [event for event in reversed(events) if levels is None or event["level"] in levels]
        for event in shown[:80]:
            try:
                stamp = datetime.fromisoformat(event["created_at"]).astimezone().strftime("%H:%M:%S")
            except ValueError:
                stamp = "—"
            row = QWidget()
            row.setObjectName("d2LogRow")
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 11, 0, 11)
            layout.setSpacing(14)
            time_label = label(stamp, "d2LogTime")
            time_label.setFixedWidth(62)
            level = label(str(event["level"]), "d2Level")
            level.setFixedWidth(52)
            level.setStyleSheet(f"color: {LEVEL_COLORS.get(event['level'], t.TEXT)};")
            name = label(str(event["event"]), "d2Event")
            name.setFixedWidth(150)
            name.setToolTip(str(event["event"]))
            message = label(str(event["message"]), "d2LogText", wrap=True)
            for widget, stretch in ((time_label, 0), (level, 0), (name, 0), (message, 1)):
                layout.addWidget(widget, stretch, Qt.AlignmentFlag.AlignTop)
            self.box_layout.addWidget(row)
            self.rows.append(row)
        if not shown:
            empty = label("Aucun événement pour ce filtre.", "d2Banner")
            empty.setContentsMargins(0, 12, 0, 14)
            self.box_layout.addWidget(empty)
            self.rows.append(empty)


# --- Vue -------------------------------------------------------------------------------------------
class AdvancedView(QWidget):
    back_requested = Signal()
    window_change_requested = Signal()
    tab_changed = Signal(str)

    def __init__(self, storage: Storage, legacy: "MainWindow", game_window: Callable[[], object],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("d2Screen")
        self.storage, self.legacy, self.game_window = storage, legacy, game_window
        self.settings = app_settings(storage)
        self.current_tab = "connexion"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("d2AppBar")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        bar.setFixedHeight(64)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(28, 0, 28, 0)
        bar_layout.setSpacing(14)
        back = button("DofBot2", "d2BackPill")
        back.setIcon(QIcon(icon_pixmap("back", t.TEXT, 16, 2.0)))
        back.setIconSize(QSize(16, 16))
        back.setToolTip("Revenir à l'application")
        back.clicked.connect(self.back_requested)
        bar_layout.addWidget(back)
        bar_layout.addWidget(label("Outils avancés", "d2AdvTitle"))
        badge = label("LECTURE SEULE", "d2Badge")
        badge.setToolTip("Aucun clic ni aucune touche n'est envoyé au jeu")
        bar_layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignVCenter)
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
        layout.addWidget(bar)
        self.body = QWidget()
        self.body.setObjectName("d2Screen")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.stack.setObjectName("d2Screens")
        body_layout.addWidget(self.stack)
        layout.addWidget(self.body, 1)
        self.pages: dict[str, AdvancedPage] = {
            "connexion": ConnexionPage(self), "observation": ObservationPage(self), "sorts": SpellScanPage(self),
            "simulation": SimulationPage(self), "corpus": CorpusToolsPage(self), "journal": JournalPage(self),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        self.dock = Dock(ADVANCED_TABS, self.body)
        self.dock.tab_selected.connect(self.go_tab)
        self.dock.resized.connect(self._place_overlays)
        self.toasts = ToastStack(self.body)
        self.toasts.changed.connect(self._place_overlays)
        self.toasts.hide()
        self.timer = QTimer(self)
        self.timer.setInterval(LIVE_MS)
        self.timer.timeout.connect(self.live_update)
        self.set_recheck(bool(self.settings.get("adv_recheck", True)), persist=False)
        self.go_tab("connexion", animate=False)

    # --- Navigation -----------------------------------------------------------------------------
    def go_tab(self, key: str, animate: bool = True) -> None:
        if key not in self.pages:
            return
        self.current_tab = key
        page = self.pages[key]
        self.stack.setCurrentWidget(page)
        page.refresh()
        self.dock.set_current(key, animate)
        self._place_overlays()
        self.live_update()
        self.tab_changed.emit(key)

    def refresh_current(self) -> None:
        self.pages[self.current_tab].refresh()
        self.live_update()

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self.timer.start()
        self.refresh_current()
        self._place_overlays()

    def hideEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self._place_overlays()

    def _place_overlays(self) -> None:
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

    def live_update(self) -> None:
        panel = self.legacy.client_panel
        if self.legacy.combat.observation_stop.isEnabled():
            text, color = "Observation en cours", t.GREEN
        elif panel.connected_hwnd is not None and panel.content_confirmed:
            text, color = "Fenêtre connectée", t.GREEN_LIGHT
        elif panel.connected_hwnd is not None:
            text, color = "Capture à confirmer", t.ALERT
        else:
            text, color = "Aucune fenêtre", t.TEXT_2
        self.status_dot.set_color(color)
        self.status_text.setText(text)
        self.status_text.setStyleSheet(f"color: {color};")
        self.pages[self.current_tab].live_update()

    # --- Services pour les pages --------------------------------------------------------------------
    def notify(self, title: str, body: str = "", color: str = t.TEXT, duration_ms: int = 3800) -> None:
        self.toasts.push(title, body, color, duration_ms)
        self._place_overlays()

    def set_recheck(self, enabled: bool, persist: bool = True) -> None:
        timer = self.legacy.connection_timer
        timer.start() if enabled else timer.stop()
        if persist:
            self.settings.set("adv_recheck", bool(enabled))

    def zone_counts(self) -> tuple[int, int]:
        profile_id = self.legacy.client_panel.profile_id
        calibration = self.storage.load_calibration(profile_id) if profile_id is not None else None
        if calibration is None:
            return 0, len(ZONE_LABELS)
        confirmed = sum(1 for name in calibration.zones
                        if name not in calibration.zone_meta or calibration.zone_meta[name].status == "confirmée")
        return confirmed, len(ZONE_LABELS)

    @staticmethod
    def window_dpi(hwnd: int) -> int | None:
        from combatbot.vision.window import window_dpi
        try:
            return window_dpi(hwnd)
        except (RuntimeError, OSError, AttributeError):
            return None

    def open_legacy(self, page: QWidget, title: str, subtitle: str, tab: int | None = None) -> None:
        """Page de l'interface historique présentée comme un outil (écran A7), puis rendue."""
        from PySide6.QtWidgets import QTabWidget
        from combatbot.ui.dofbot2.tool_frame import show_legacy_page
        tabs = page.findChild(QTabWidget)
        if tab is not None and tabs is not None:
            tabs.setCurrentIndex(tab)
        show_legacy_page(self.window(), page, title, subtitle)
        self.refresh_current()

    def open_simulated_spells(self) -> None:
        self.open_legacy(self.legacy.spells, "Sorts simulés", "Sorts utilisés par la simulation, indépendants du scan", 0)

    def open_scan_editor(self) -> None:
        self.open_legacy(self.legacy.spells, "Éditeur des sorts scannés",
                         "Nom, caractéristiques, effets et texte OCR ; validation d'un ou plusieurs sorts", 1)

    def open_statistics(self) -> None:
        self.legacy.statistics.refresh()
        self.open_legacy(self.legacy.statistics, "Historique des combats", "Combats simulés enregistrés")

    def open_simulated_combat(self) -> None:
        combat = self.legacy.combat
        if combat.observation_stop.isEnabled():
            self.notify("Observation en cours", "Arrêtez l'observation avant d'afficher le combat simulé.", t.ALERT)
            return
        combat.mode.setCurrentIndex(0)   # vue simulation : grille, acteurs et historique des actions
        self.open_legacy(combat, "Combat simulé", "Grille, acteurs et historique des actions du dernier combat")

    def open_gamedata_tools(self) -> None:
        self.open_legacy(self.legacy.settings.gamedata_panel, "Données du client DOFUS",
                         "Analyse, validation de toutes les maps, inspection et export d'une map")