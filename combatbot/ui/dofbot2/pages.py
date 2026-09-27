"""Onglets de l'application DofBot2 : Accueil, Donjons, Zones, Sorts, Alertes, Réglages."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget,
)

from combatbot.ui.dofbot2 import theme as t
from combatbot.ui.dofbot2.controls import (
    Accordion, HeroHeader, Switch, button, card, choice_row, info_row, input_row, label, step_row, switch_row,
)
from combatbot.ui.dofbot2.settings import (
    ALERT_KINDS, DETECTED_AT_KEY, DUNGEONS, SPELL_DEFAULTS, SPELL_TARGETS, SPELL_TIMINGS, ZONES, Place,
    SpellBinding, clamp_index, current_plan, load_dungeon_places, monsters_key, plan_summary,
    recent_dungeon_ids,
)
from combatbot.ui.dofbot2.system import webhook_problem
from combatbot.ui.dofbot2.widgets import HoverButton, rounded_pixmap

if TYPE_CHECKING:
    from combatbot.ui.dofbot2.app import AppView


HEROES = {
    "home": ("Bonjour, {name}", "Programmez un donjon ou une zone, réglez vos sorts, puis démarrez. DofBot2 vous "
             "prévient de ce qui se passe pendant que vous faites autre chose.", "illustration · personnage"),
    "donjon": ("Farm de donjon", "Choisissez un donjon. Les options sont repliées et facultatives : ouvrez "
               "seulement celles que vous voulez personnaliser.", "illustration · entrée de donjon"),
    "zone": ("Farm de zone", "Choisissez une zone, les monstres à attaquer et le trajet. DofBot2 enchaîne les "
             "combats map après map.", "illustration · carte de zone"),
    "sorts": ("Sorts", "DofBot2 lit votre barre de sorts. Touchez un sort pour régler son coût, sa portée et "
              "quand l'utiliser.", "illustration · barre de sorts"),
    "notifs": ("Alertes", "Restez informé sans surveiller le jeu : boss vaincu, inventaire plein, message "
               "privé…", "illustration · notification"),
    "params": ("Réglages", "Fenêtre connectée, profil, raccourcis clavier.", "illustration · réglages"),
}
JOURNAL_SIZE = 7
LOG_COLORS = {"green": t.GREEN, "light": t.GREEN_LIGHT, "muted": t.TEXT_2, "text": t.TEXT, "alert": t.ALERT}


def relative_time(when: datetime, now: datetime | None = None) -> str:
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - when).total_seconds()))
    if seconds < 60:
        return "à l'instant"
    if seconds < 3600:
        return f"il y a {seconds // 60} min"
    if seconds < 86400:
        return f"il y a {seconds // 3600} h"
    days = seconds // 86400
    return f"il y a {days} jour{'s' if days > 1 else ''}"


class Page(QScrollArea):
    """Colonne centrée max 800px, padding 32/24/140 (place du dock), gap 24."""

    def __init__(self, app: "AppView", key: str) -> None:
        super().__init__()
        self.app, self.key = app, key
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        outer = QWidget()
        outer.setObjectName("d2Screen")
        row = QHBoxLayout(outer)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        self.column = QWidget()
        self.column.setObjectName("d2Screen")
        self.column.setMaximumWidth(800)
        self.column.setMinimumWidth(640)
        self.layout_ = QVBoxLayout(self.column)
        self.layout_.setContentsMargins(24, 32, 24, 140)
        self.layout_.setSpacing(24)
        row.addWidget(self.column, 100)
        row.addStretch(1)
        self.setWidget(outer)
        title, text, image = HEROES[key]
        self.hero = HeroHeader(title, text, image)
        self.layout_.addWidget(self.hero)
        self.content = QVBoxLayout()
        self.content.setSpacing(16)
        self.layout_.addLayout(self.content)
        self.accordions_box = QVBoxLayout()
        self.accordions_box.setSpacing(10)
        self.layout_.addLayout(self.accordions_box)
        self.footer = QVBoxLayout()
        self.layout_.addLayout(self.footer)
        self.layout_.addStretch(1)
        self.accordions: dict[str, Accordion] = {}
        self._open_state: dict[str, bool] = {}

    def set_accordions(self, specs: list[tuple[str, str, str, list[QWidget], bool, bool]]) -> None:
        """(clé, titre, sous-titre, lignes, ouvert par défaut, facultatif) ; l'état ouvert est conservé."""
        for accordion in self.accordions.values():
            self.accordions_box.removeWidget(accordion)
            accordion.hide()
            accordion.deleteLater()
        self.accordions = {}
        for key, title, subtitle, rows, opened, optional in specs:
            accordion = Accordion(title, subtitle, rows, self._open_state.get(key, opened), optional)
            accordion.toggled.connect(lambda state, key=key: self._open_state.__setitem__(key, state))
            self.accordions_box.addWidget(accordion)
            self.accordions[key] = accordion

    def refresh(self) -> None:
        """Relit les données (appelé à chaque affichage de l'onglet)."""


# --- Accueil ---------------------------------------------------------------------------------------
class HomePage(Page):
    def __init__(self, app: "AppView") -> None:
        super().__init__(app, "home")
        activity = card()
        row = QHBoxLayout(activity)
        row.setContentsMargins(24, 22, 24, 22)
        row.setSpacing(20)
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addWidget(label("ACTIVITÉ PROGRAMMÉE", "d2Caps"))
        texts.addSpacing(6)
        self.plan_name = label("", "d2PlanName", wrap=True)
        texts.addWidget(self.plan_name)
        texts.addSpacing(4)
        self.plan_sub = label("", "d2PlanSub", wrap=True)
        texts.addWidget(self.plan_sub)
        row.addLayout(texts, 1)
        edit = button("Modifier", "d2Secondary")
        edit.clicked.connect(lambda: self.app.go_tab(current_plan(self.app.settings).mode))
        row.addWidget(edit)
        self.run_button = button("Démarrer", "d2Run")
        self.run_button.clicked.connect(self.app.toggle_run)
        row.addWidget(self.run_button)
        self.content.addWidget(activity)

        journal = card()
        self.journal_layout = QVBoxLayout(journal)
        self.journal_layout.setContentsMargins(24, 8, 24, 8)
        self.journal_layout.setSpacing(0)
        caps = label("JOURNAL", "d2Caps")
        caps.setContentsMargins(0, 14, 0, 6)
        self.journal_layout.addWidget(caps)
        self.journal_rows: list[QWidget] = []
        self.content.addWidget(journal)

    def refresh(self) -> None:
        self.hero.title.setText(HEROES["home"][0].format(name=self.app.profile_name))
        plan = current_plan(self.app.settings)
        self.plan_name.setText(plan.place.name)
        self.plan_sub.setText(plan_summary(self.app.settings, self.app.active_spell_count()))
        self.set_running(self.app.running)
        self.refresh_journal()

    def set_running(self, running: bool) -> None:
        self.run_button.setText("Arrêter" if running else "Démarrer")
        self.run_button.setProperty("running", running)
        self.run_button.style().unpolish(self.run_button)
        self.run_button.style().polish(self.run_button)

    def refresh_journal(self) -> None:
        for row in self.journal_rows:
            self.journal_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.journal_rows = []
        entries = self.app.journal(JOURNAL_SIZE)
        if not entries:
            entries = [("—", "Aucune activité pour le moment. Démarrez une session pour voir le journal.", t.TEXT_MUTED)]
        for time_text, message, color in entries:
            row = QWidget()
            row.setObjectName("d2LogRow")
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 12, 0, 12)
            layout.setSpacing(16)
            stamp = label(time_text, "d2LogTime")
            stamp.setFixedWidth(44)
            text = label(message, "d2LogText", wrap=True)
            text.setStyleSheet(f"color: {color};")
            layout.addWidget(stamp, 0, Qt.AlignmentFlag.AlignTop)
            layout.addWidget(text, 1)
            self.journal_layout.addWidget(row)
            self.journal_rows.append(row)


# --- Donjons / Zones ---------------------------------------------------------------------------------
class PlaceCard(HoverButton):
    """Carte lieu : image 78px (emplacement), nom 700 13px, méta 11px ; sélection bordure 1.5px verte."""

    def __init__(self, place: Place) -> None:
        super().__init__()
        self.place = place
        self.setCheckable(True)
        self.setFixedHeight(78 + 10 + 34 + 12)
        self.setMinimumWidth(120)
        self.setToolTip(place.name)
        self.image = QImage(place.image_path) if place.image_path else QImage()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        painter.fillPath(path, QColor(t.SURFACE))
        painter.save()
        painter.setClipPath(path)
        image = QRectF(0, 0, self.width(), 78)
        if self.image.isNull():
            painter.fillRect(image, QColor("#0f1510"))
            painter.setPen(QPen(t.green(0.05), 5.66))
            for offset in range(-78, self.width() + 78, 16):
                painter.drawLine(QPointF(offset, 78), QPointF(offset + 78, 0))
            painter.setPen(QColor(t.TEXT_MUTED))
            painter.setFont(t.font(10, 500, mono=True))
            painter.drawText(image, Qt.AlignmentFlag.AlignCenter, "image")
        else:
            scaled = self.image.scaled(QSize(self.width(), 78), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                       Qt.TransformationMode.SmoothTransformation)
            source = QRectF((scaled.width() - self.width()) / 2, (scaled.height() - 78) / 2,
                            self.width(), 78)
            painter.drawImage(image, scaled, source)
            painter.fillRect(image, QColor(0, 0, 0, 38))
        painter.restore()
        name_font = t.font(13, 700)
        painter.setFont(name_font)
        painter.setPen(QColor(t.TEXT))
        painter.drawText(QRectF(12, 88, self.width() - 24, 36), Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
                         self.place.name)
        lines = 2 if QFontMetrics(name_font).horizontalAdvance(self.place.name) > self.width() - 24 else 1
        painter.setFont(t.font(11))
        painter.setPen(QColor(t.TEXT_2))
        painter.drawText(QRectF(12, 88 + 17 * lines + 3, self.width() - 24, 15), Qt.AlignmentFlag.AlignLeft,
                         self.place.meta)
        selected = self.isChecked()
        painter.setPen(QPen(QColor(t.GREEN) if selected else (t.white(0.14) if self.hovered else t.white(0.06)), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        self._focus_ring(painter, rect.adjusted(2, 2, -2, -2), 14)


class DungeonCatalogDialog(QDialog):
    """Fenêtre dédiée au catalogue complet, avec recherche instantanée."""

    def __init__(self, places: tuple[Place, ...], selected_index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.places = places
        self.selected_index: int | None = None
        self.setWindowTitle("DofBot2 · Tous les donjons")
        self.setObjectName("d2Screen")
        self.setStyleSheet(t.STYLE)
        self.resize(1080, 760)
        self.setMinimumSize(820, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        titles.addWidget(label("Tous les donjons", "d2H2"))
        self.result_count = label("", "d2Note")
        titles.addWidget(self.result_count)
        heading.addLayout(titles, 1)
        close = button("Fermer", "d2Secondary")
        close.clicked.connect(self.reject)
        heading.addWidget(close)
        layout.addLayout(heading)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher un donjon, un niveau ou un boss…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        host = QWidget()
        host.setObjectName("d2Screen")
        self.grid = QGridLayout(host)
        self.grid.setContentsMargins(0, 4, 0, 8)
        self.grid.setSpacing(12)
        self.cards: list[PlaceCard] = []
        for index, place in enumerate(places):
            place_card = PlaceCard(place)
            place_card.setChecked(index == selected_index)
            place_card.clicked.connect(lambda _checked=False, index=index: self._choose(index))
            self.cards.append(place_card)
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        self.search.textChanged.connect(self._filter)
        self._filter("")
        self.search.setFocus()

    def _choose(self, index: int) -> None:
        self.selected_index = index
        self.accept()

    def _filter(self, query: str) -> None:
        words = query.casefold().split()
        visible = []
        for place, place_card in zip(self.places, self.cards):
            haystack = f"{place.name} {place.meta} {place.boss or ''}".casefold()
            match = all(word in haystack for word in words)
            place_card.setVisible(match)
            if match:
                visible.append(place_card)
        for place_card in self.cards:
            self.grid.removeWidget(place_card)
        for position, place_card in enumerate(visible):
            self.grid.addWidget(place_card, position // 4, position % 4)
        self.result_count.setText(f"{len(visible)} donjon{'s' if len(visible) != 1 else ''}")


class PlacesPage(Page):
    """Donjons récents + catalogue séparé, ou grille des zones."""

    def __init__(self, app: "AppView", mode: str) -> None:
        super().__init__(app, mode)
        self.mode = mode
        self.places = load_dungeon_places(app.storage) if mode == "donjon" else ZONES
        self.index_key = "dj" if mode == "donjon" else "zn"
        if mode == "donjon":
            recent_header = QHBoxLayout()
            recent_header.addWidget(label("DONJONS FARMÉS RÉCEMMENT", "d2Caps"))
            recent_header.addStretch(1)
            show_all = button("Afficher tous les donjons", "d2Secondary")
            show_all.clicked.connect(self.show_all_dungeons)
            recent_header.addWidget(show_all)
            self.content.addLayout(recent_header)
        grid_host = QWidget()
        self.places_grid = QGridLayout(grid_host)
        self.places_grid.setContentsMargins(0, 0, 0, 0)
        self.places_grid.setSpacing(12)
        self.cards: list[PlaceCard] = []
        self.card_indices: list[int] = []
        self.empty_recent = label(
            "Aucun donjon farmé récemment. Ouvrez le catalogue pour en choisir un.", "d2Note", wrap=True)
        if mode == "zone":
            for index, place in enumerate(self.places):
                self._add_card(index, place)
        self.content.addWidget(grid_host)
        if mode == "donjon":
            self.content.addWidget(self.empty_recent)
            self.refresh_recent()
        self.place_details = label("", "d2Note", wrap=True)
        self.content.addWidget(self.place_details)
        if self.mode == "donjon":
            self._save_selected_place(self.selected_index)
        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.addStretch(1)
        actions.addWidget(label("Tout est facultatif, les valeurs par défaut suffisent pour démarrer.", "d2Note"))
        self.schedule_button = button("", "d2Schedule")
        self.schedule_button.clicked.connect(self.schedule)
        actions.addWidget(self.schedule_button)
        self.footer.addLayout(actions)

    @property
    def selected_index(self) -> int:
        if self.mode == "donjon":
            selected_id = self.app.settings.get("dj_id")
            if isinstance(selected_id, int):
                for index, place in enumerate(self.places):
                    if place.place_id == selected_id:
                        return index
        return clamp_index(self.app.settings.get(self.index_key, 0), len(self.places))

    def select(self, index: int) -> None:
        previous = self.selected_index
        self.app.settings.set(self.index_key, index)
        if self.mode == "donjon":
            self._save_selected_place(index)
        if self.app.settings.get("plan_mode", "donjon") == self.mode:
            self.app.settings.set("planned", False)   # le lieu programmé a changé : à reprogrammer
        self._sync_cards()
        self._sync_details()
        if self.mode == "zone" and previous != index:
            self._build_accordions()   # les monstres dépendent de la zone
        self._sync_schedule()
        self.app.plan_changed.emit()

    def schedule(self) -> None:
        place = self.places[self.selected_index]
        self.app.settings.set("plan_mode", self.mode)
        self.app.settings.set("planned", True)
        self._sync_schedule()
        self.app.plan_changed.emit()
        self.app.log(f"Activité programmée · {place.name}", "green")
        self.app.notify("Activité programmée", place.name, t.GREEN)

    def refresh(self) -> None:
        if self.mode == "donjon":
            self.refresh_recent()
        self._sync_cards()
        self._sync_details()
        self._sync_schedule()
        if not self.accordions:
            self._build_accordions()

    def _sync_cards(self) -> None:
        for index, place_card in zip(self.card_indices, self.cards):
            place_card.setChecked(index == self.selected_index)

    def _add_card(self, index: int, place: Place) -> None:
        place_card = PlaceCard(place)
        place_card.clicked.connect(lambda _checked=False, index=index: self.select(index))
        position = len(self.cards)
        self.places_grid.addWidget(place_card, position // 4, position % 4)
        self.places_grid.setColumnStretch(position % 4, 1)
        self.card_indices.append(index)
        self.cards.append(place_card)

    def refresh_recent(self) -> None:
        if self.mode != "donjon":
            return
        for place_card in self.cards:
            self.places_grid.removeWidget(place_card)
            place_card.hide()
            place_card.deleteLater()
        self.cards = []
        self.card_indices = []
        by_id = {place.place_id: index for index, place in enumerate(self.places)}
        indices = [by_id[item] for item in recent_dungeon_ids(self.app.settings) if item in by_id]
        if not indices:
            indices = self._legacy_recent_indices()
        for index in indices:
            self._add_card(index, self.places[index])
        self.empty_recent.setVisible(not self.cards)
        self._sync_cards()

    def _legacy_recent_indices(self) -> list[int]:
        """Reprend les sessions antérieures au stockage structuré des donjons récents."""
        if self.app.profile_id is None:
            return []
        by_name = {place.name: index for index, place in enumerate(self.places)}
        result = []
        for row in self.app.storage.recent_profile_events("dofbot2.", self.app.profile_id, 100):
            message = str(row["message"])
            prefix = "Session démarrée · "
            if message.startswith(prefix):
                index = by_name.get(message[len(prefix):])
                if index is not None and index not in result:
                    result.append(index)
            if len(result) >= 8:
                break
        return result

    def show_all_dungeons(self) -> None:
        dialog = DungeonCatalogDialog(self.places, self.selected_index, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_index is not None:
            self.select(dialog.selected_index)

    def _save_selected_place(self, index: int) -> None:
        place = self.places[index]
        self.app.settings.set("dj_id", place.place_id)
        self.app.settings.set("dj_place", {"place_id": place.place_id, "name": place.name,
                                           "meta": place.meta, "boss": place.boss})

    def _sync_details(self) -> None:
        place = self.places[self.selected_index]
        if self.mode != "donjon" or not place.map_ids:
            self.place_details.setText("")
            return
        rooms = " → ".join(f"{name} [{map_id}]" for name, map_id in zip(place.room_names, place.map_ids))
        self.place_details.setText(
            f"Données locales du client · niveau conseillé {place.level} · "
            f"entrée {place.entrance_map_id} · sortie {place.exit_map_id}\n"
            f"Cartes déclarées : {rooms}"
        )

    def _sync_schedule(self) -> None:
        plan = current_plan(self.app.settings)
        done = plan.mode == self.mode and plan.planned
        self.schedule_button.setText("Programmé ✓" if done else
                                     "Programmer " + ("ce donjon" if self.mode == "donjon" else "cette zone"))

    def _build_accordions(self) -> None:
        s = self.app.settings
        changed = self.app.plan_changed.emit
        if self.mode == "donjon":
            specs = [
                ("djStrat", "Stratégie de combat", "Ordre des cibles et style de jeu", [
                    switch_row(s, "bossFirst", "Prioriser le boss", "Attaque le boss en premier dès qu'il est à portée",
                               True, lambda _v: changed()),
                    choice_row(s, "target", "Ensuite, cibler", "", ("Plus proche", "Moins de PV", "Plus dangereux"),
                               "Plus proche"),
                    choice_row(s, "style", "Style", "", ("Distance", "Corps-à-corps", "Survie"), "Distance"),
                    step_row(s, "reserve", "Garder des PA en réserve", "Utile pour un soin ou une fuite en fin de tour",
                             0, 0, 12),
                ], True, True),
                ("djRuns", "Déroulement", "Nombre de runs et pauses", [
                    step_row(s, "runs", "Nombre de runs", "0 = sans limite", 10, 0, 999, 1, "runs",
                             lambda _v: changed()),
                    step_row(s, "pause", "Pause entre deux runs", "", 30, 0, 600, 10, "s"),
                    switch_row(s, "retry", "Recommencer après une défaite", "", False),
                ], False, True),
                ("djKeys", "Clés & inventaire", "Achat de clé, banque, recyclage", [
                    switch_row(s, "buyKey", "Acheter la clé automatiquement", "Au prix moyen de l'hôtel de vente", False),
                    step_row(s, "pods", "Retour banque au-delà de", "Poids de l'inventaire", 90, 50, 100, 5, "%"),
                    switch_row(s, "recycle", "Recycler les objets sans valeur", "", False),
                ], False, True),
                self._safety(),
            ]
        else:
            zone_index = self.selected_index
            zone = self.places[zone_index]
            specs = [
                ("znMob", "Monstres ciblés", "Quels groupes attaquer", [
                    choice_row(s, monsters_key(zone_index), "Monstres", "Laissez vide pour tout attaquer",
                               zone.monsters, list(zone.monsters[:2]), multi=True),
                    step_row(s, "grp", "Taille max d'un groupe", "", 4, 1, 8),
                    step_row(s, "grpLvl", "Niveau max du groupe", "", 120, 10, 1000, 10),
                ], True, True),
                ("znPath", "Trajet", "Parcours des maps de la zone", [
                    choice_row(s, "path", "Parcours", "", ("Boucle", "Aléatoire"), "Boucle", on_change=lambda _v: changed()),
                    switch_row(s, "avoid", "Éviter les maps avec d'autres joueurs", "", False),
                ], False, True),
                ("znHarv", "Récolte", "Ramasser des ressources en chemin", [
                    switch_row(s, "harvest", "Récolter sur le trajet", "", False),
                    choice_row(s, "jobs", "Métiers", "", ("Paysan", "Bûcheron", "Alchimiste", "Mineur", "Pêcheur"),
                               ["Paysan"], multi=True),
                ], False, True),
                self._safety(),
            ]
        self.set_accordions(specs)

    def _safety(self):
        s = self.app.settings
        return ("safe", "Sécurité", "Pauses et arrêts automatiques", [
            step_row(s, "hp", "Se soigner sous", "Le personnage se repose avant le prochain combat", 35, 0, 100, 5, "%"),
            switch_row(s, "mpPause", "Pause si un joueur vous écrit", "Reprend quand vous cliquez sur Démarrer"),
            switch_row(s, "deathStop", "Arrêt si le personnage meurt", ""),
        ], False, True)


# --- Sorts -------------------------------------------------------------------------------------------
class SpellTile(HoverButton):
    """Icône détectée 64px r12, pastille PA en haut à droite, anneau vert quand sélectionnée."""

    def __init__(self, spell_id: int, icon_png: bytes, ap_cost: int | None) -> None:
        super().__init__()
        self.spell_id = spell_id
        self.ap_cost = ap_cost
        self.in_use = True
        self.setCheckable(True)
        self.setFixedSize(72, 72)
        image = QImage.fromData(icon_png) if icon_png else QImage()
        self.pixmap = None if image.isNull() else rounded_pixmap(image, QSize(64, 64), 12)

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        icon = QRectF(4, 4, 64, 64)
        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.GREEN))
            painter.drawRoundedRect(icon.adjusted(-4, -4, 4, 4), 16, 16)
            painter.setBrush(QColor(t.WINDOW))
            painter.drawRoundedRect(icon.adjusted(-2, -2, 2, 2), 14, 14)
        painter.setOpacity(1.0 if self.in_use else 0.35)
        if self.pixmap is not None:
            painter.drawPixmap(icon.toRect(), self.pixmap)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.SURFACE_2))
            painter.drawRoundedRect(icon, 12, 12)
        if self.hovered and not self.isChecked():
            painter.setPen(QPen(t.white(0.25), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(icon.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)
        text = "?" if self.ap_cost is None else str(self.ap_cost)
        font = t.font(10, 600, mono=True)
        width = max(18, QFontMetrics(font).horizontalAdvance(text) + 8)
        badge = QRectF(72 - width, 0, width, 18)
        painter.setPen(QPen(t.white(0.1), 1))
        painter.setBrush(QColor(t.WINDOW))
        painter.drawRoundedRect(badge.adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
        painter.setFont(font)
        painter.setPen(QColor(t.GREEN_LIGHT))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, text)
        painter.setOpacity(1.0)
        self._focus_ring(painter, icon.adjusted(-3, -3, 3, 3), 15)


class SpellIcon(QWidget):
    """Icône 56px r14 du sort sélectionné."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(56, 56)
        self.pixmap = None

    def set_icon(self, icon_png: bytes | None) -> None:
        image = QImage.fromData(icon_png) if icon_png else QImage()
        self.pixmap = None if image.isNull() else rounded_pixmap(image, QSize(56, 56), 14)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.pixmap is not None:
            painter.drawPixmap(0, 0, self.pixmap)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.SURFACE_2))
            painter.drawRoundedRect(QRectF(0, 0, 56, 56), 14, 14)


class SpellsPage(Page):
    COLUMNS = 10

    def __init__(self, app: "AppView") -> None:
        super().__init__(app, "sorts")
        detection = card()
        detection_layout = QVBoxLayout(detection)
        detection_layout.setContentsMargins(16, 18, 16, 18)
        detection_layout.setSpacing(14)
        top = QHBoxLayout()
        top.setContentsMargins(4, 0, 4, 0)
        top.setSpacing(12)
        self.banner = label("", "d2Banner", wrap=True)
        self.banner.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(self.banner, 1)
        self.redetect_button = button("Relancer la détection", "d2SmallOutline")
        self.redetect_button.clicked.connect(self.app.redetect_spells)
        top.addWidget(self.redetect_button)
        detection_layout.addLayout(top)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(0)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        detection_layout.addWidget(self.grid_host)
        self.content.addWidget(detection)

        self.selected_box = QWidget()
        selected = QHBoxLayout(self.selected_box)
        selected.setContentsMargins(4, 4, 4, 0)
        selected.setSpacing(16)
        self.selected_icon = SpellIcon()
        selected.addWidget(self.selected_icon)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        self.spell_name = label("", "d2SpellName")
        self.spell_meta = label("", "d2RowDesc")
        texts.addWidget(self.spell_name)
        texts.addWidget(self.spell_meta)
        selected.addLayout(texts, 1)
        selected.addWidget(label("Utiliser en combat", "d2UseLabel"))
        self.use_switch = Switch(True)
        self.use_switch.toggled.connect(self._toggle_use)
        selected.addWidget(self.use_switch)
        self.content.addWidget(self.selected_box)
        self.tiles: list[SpellTile] = []
        self.rows: dict[int, object] = {}
        self.selected_id: int | None = None

    def refresh(self) -> None:
        rows = self.app.detected_spells()
        self.rows = {int(row["id"]): row for row in rows}
        for tile in self.tiles:
            self.grid.removeWidget(tile)
            tile.hide()
            tile.deleteLater()
        self.tiles = []
        for index, row in enumerate(rows):
            tile = SpellTile(int(row["id"]), row["icon_png"], row["ap_cost"])
            tile.in_use = self.app.spell_config.in_use(tile.spell_id)
            tile.clicked.connect(lambda _checked=False, spell_id=tile.spell_id: self.select(spell_id))
            self.grid.addWidget(tile, index // self.COLUMNS, index % self.COLUMNS)
            self.tiles.append(tile)
        count = len(rows)
        detected_at = self.app.storage.get_profile_setting(self.app.profile_id, DETECTED_AT_KEY, None)
        when = ""
        if isinstance(detected_at, str):
            try:
                when = " · " + relative_time(datetime.fromisoformat(detected_at))
            except ValueError:
                when = ""
        if count:
            self.banner.setText(f"<span style='color:{t.GREEN}; font-weight:700'>{count} sort{'s' if count > 1 else ''} "
                                f"détecté{'s' if count > 1 else ''}</span> dans la barre de sorts{when}")
            self.redetect_button.setText("Relancer la détection")
        else:
            self.banner.setText("Aucun sort détecté pour ce profil. Connectez la fenêtre du jeu puis lancez la "
                                "détection : DofBot2 lit la barre de sorts calibrée.")
            self.redetect_button.setText("Lancer la détection")
        self.grid_host.setVisible(bool(count))
        if self.selected_id not in self.rows:
            self.selected_id = next(iter(self.rows), None)
        self.select(self.selected_id)

    def select(self, spell_id: int | None) -> None:
        self.selected_id = spell_id
        for tile in self.tiles:
            tile.setChecked(tile.spell_id == spell_id)
        self.selected_box.setVisible(spell_id is not None)
        if spell_id is None:
            self.set_accordions([])
            return
        row = self.app.storage.get_profile_spell(spell_id)
        self.rows[spell_id] = row
        index = list(self.rows).index(spell_id)
        self.selected_icon.set_icon(row["icon_png"])
        self.spell_name.setText(row["name"] or f"Sort {index + 1}")
        self.use_switch.set_state(self.app.spell_config.in_use(spell_id))
        self._update_meta(row)
        self._build_accordions(spell_id, index)

    def _update_meta(self, row) -> None:
        columns = int(self.app.storage.get_profile_setting(self.app.profile_id, "spell_columns", 10) or 10)
        slot = int(row["slot"])
        line, box = (slot - 1) // columns + 1, (slot - 1) % columns + 1
        page = f"page {row['page']}, " if int(row["page"]) > 1 else ""
        status = "" if row["status"] == "Confirmé" else f" · {str(row['status']).lower()}"
        disabled = "" if self.app.spell_config.in_use(int(row["id"])) else " · désactivé"
        self.spell_meta.setText(f"Emplacement {page}ligne {line}, case {box}{status}{disabled}")

    def _toggle_use(self, checked: bool) -> None:
        if self.selected_id is None:
            return
        self.app.spell_config.set(self.selected_id, "use", checked)
        for tile in self.tiles:
            if tile.spell_id == self.selected_id:
                tile.in_use = checked
                tile.update()
        self._update_meta(self.app.storage.get_profile_spell(self.selected_id))
        self.app.plan_changed.emit()

    def _build_accordions(self, spell_id: int, index: int) -> None:
        binding = SpellBinding(self.app.storage, self.app.spell_config, spell_id, index + 1)

        def refresh_values(_value=None) -> None:
            row = self.app.storage.get_profile_spell(spell_id)
            for tile in self.tiles:
                if tile.spell_id == spell_id:
                    tile.ap_cost = row["ap_cost"]
                    tile.update()
            self._sync_range_steppers(row)

        d = SPELL_DEFAULTS
        self._min_row = step_row(binding, "min_range", "Portée minimale", "", d["min_range"], 0, 20,
                                 on_change=refresh_values)
        self._max_row = step_row(binding, "max_range", "Portée maximale", "", d["max_range"], 0, 20,
                                 on_change=refresh_values)
        self.set_accordions([
            ("spCost", "Coût & portée", "Lus automatiquement, modifiables", [
                step_row(binding, "ap_cost", "Coût en PA", "", d["ap_cost"], 1, 12, on_change=refresh_values),
                self._min_row, self._max_row,
                switch_row(binding, "modifiable_range", "Portée modifiable", "Les bonus de portée s'appliquent",
                           d["modifiable_range"]),
            ], True, False),
            ("spCond", "Conditions de lancer", "Ligne de vue, en ligne, limites", [
                switch_row(binding, "line_of_sight", "Ligne de vue requise", "", d["line_of_sight"]),
                switch_row(binding, "line_cast", "Lancer en ligne uniquement", "", d["line_cast"]),
                step_row(binding, "per_turn", "Lancers par tour", "", d["per_turn"], 1, 10),
                step_row(binding, "per_target", "Lancers par cible", "", d["per_target"], 1, 10),
            ], False, False),
            ("spTgt", "Ciblage", "Sur qui et quand l'utiliser", [
                choice_row(binding, "target", "Cible", "", SPELL_TARGETS, "Ennemi"),
                choice_row(binding, "when", "Quand", "", SPELL_TIMINGS, "Toujours"),
                step_row(binding, "priority", "Priorité", "1 = lancé en premier", index + 1, 1, 20),
            ], False, True),
        ])

    def _sync_range_steppers(self, row) -> None:
        """Déplacer une borne de portée peut pousser l'autre : on réaffiche les deux."""
        for widget, key in ((self._min_row, "min_range"), (self._max_row, "max_range")):
            if row[key] is not None and widget.control.value() != row[key]:
                widget.control.setValue(int(row[key]))


# --- Alertes -----------------------------------------------------------------------------------------
class AlertsPage(Page):
    def __init__(self, app: "AppView") -> None:
        super().__init__(app, "notifs")
        banner = card()
        row = QHBoxLayout(banner)
        row.setContentsMargins(20, 16, 20, 16)
        row.setSpacing(14)
        row.addWidget(label("Les alertes apparaissent en bas à droite de l'écran, même quand vous êtes sur une autre "
                            "application.", "d2Banner", wrap=True), 1)
        test = button("Envoyer un test", "d2GreenOutline")
        test.clicked.connect(lambda: self.app.notify("Notification test",
                                                     "Voici à quoi ressemblent les alertes de DofBot2.", t.GREEN,
                                                     force=True))
        row.addWidget(test)
        self.content.addWidget(banner)
        s = self.app.settings
        self.set_accordions([
            ("nfDesk", "Notifications bureau", "Choisissez ce qui vous prévient",
             [switch_row(s, key, title, "", True) for key, title in ALERT_KINDS.items()], True, False),
            ("nfSide", "Quand je fais autre chose", "Comportement en arrière-plan", [
                switch_row(s, "nfFocus", "Seulement si le jeu n'est pas au premier plan", "", False),
                switch_row(s, "nfSound", "Jouer un son", ""),
                choice_row(s, "nfDur", "Durée d'affichage", "", ("4 s", "8 s", "Jusqu'au clic"), "4 s"),
            ], False, True),
            ("nfPhone", "Sur le téléphone", "Via un webhook Discord", [
                switch_row(s, "nfDiscord", "Envoyer aussi sur Discord", "", False),
                input_row(s, "webhook", "Webhook", "https://discord.com/api/webhooks/…", webhook_problem),
            ], False, True),
        ])


# --- Réglages ----------------------------------------------------------------------------------------
class SettingsPage(Page):
    def __init__(self, app: "AppView") -> None:
        super().__init__(app, "params")

    def refresh(self) -> None:
        a = self.app.app_settings
        self.set_accordions([
            ("stConn", "Connexion", "Fenêtre et profil actifs", [
                info_row("Fenêtre du jeu", self.app.window_label(), lambda: self.app.request_screen.emit("connect")),
                info_row("Profil", self.app.profile_name, lambda: self.app.request_screen.emit("profile")),
            ], True, False),
            ("stKeys", "Raccourcis", "Contrôler DofBot2 sans quitter le jeu", [
                info_row("Démarrer / pause", "F8"),
                info_row("Arrêt d'urgence", "F9"),
            ], False, False),
            ("stApp", "Application", "Démarrage et langue", [
                switch_row(a, "boot", "Lancer DofBot2 avec Windows", "", False, self.app.set_autostart),
                switch_row(a, "tray", "Réduire dans la barre des tâches",
                           "La réduction masque la fenêtre ; l'icône près de l'horloge la rouvre"),
                choice_row(a, "lang", "Langue", "L'anglais arrivera avec la traduction de l'interface",
                           ("Français", "English"), "Français", disabled=("English",)),
            ], False, False),
            ("stAdvanced", "Outils avancés", "Calibration, scan des sorts, observation et corpus", [
                info_row("Interface avancée", "DofBot2", self.app.open_advanced.emit, "Ouvrir"),
            ], False, False),
        ])


def journal_entry(row) -> tuple[str, str, str]:
    """Ligne SQLite ``events`` → (heure locale HH:MM, message, couleur)."""
    try:
        stamp = datetime.fromisoformat(row["created_at"]).astimezone().strftime("%H:%M")
    except ValueError:
        stamp = "—"
    try:
        tone = json.loads(row["context_json"]).get("tone", "text")
    except (ValueError, AttributeError):
        tone = "text"
    return stamp, row["message"], LOG_COLORS.get(tone, t.TEXT)
