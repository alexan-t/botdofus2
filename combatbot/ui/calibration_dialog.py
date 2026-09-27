"""Calibration des zones : grande capture à gauche, liste compacte des zones à droite.

Chaque zone est un cadre déplaçable/redimensionnable sur la capture réelle. La barre latérale ne
montre qu'un nom, un badge d'état (À placer / À vérifier / Validée) et une action ; la provenance
détaillée reste en infobulle et n'est jamais concaténée à chaque mouvement de souris.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QBrush, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QDialog, QFrame, QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsView, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from combatbot.ui.images import bgr_to_pixmap
from combatbot.vision.models import Calibration, CapturedFrame, RelativeRect, ZoneEvidence, ZONE_LABELS, ZONE_NAMES
from combatbot.vision.autocalibration import ZoneSuggestion, zones_needing_review
from combatbot.vision.coordinates import ClientBox, ClientSize, LayoutSignature


COLORS = ("#5dd6bd", "#f0bc65", "#e77783", "#8eaaf0", "#b59cf2", "#62c0e2", "#ffffff")
ALL_ZONES = (*ZONE_NAMES, "identity")
REQUIRED_ZONES = ("combat", "spell_bar", "hp", "ap", "mp")
ZONE_HINTS = {
    "combat": "Toute la carte de combat, au-dessus du HUD.",
    "spell_bar": "Les cases de sorts en bas de l'écran.",
    "hp": "Le cœur des points de vie.",
    "ap": "Le chiffre des PA (étoile bleue).",
    "mp": "Le chiffre des PM (vert).",
    "end_turn": "Le bouton « Terminer le tour ».",
    "identity": "Nom et classe du personnage.",
}
MANUAL = "ajustée manuellement"
HUMAN = "vérification humaine"
HANDLE_SCREEN_PX = 10   # poignée de redimensionnement : taille fixe à l'écran, quel que soit le zoom
GRAB_SCREEN_PX = 18     # zone cliquable autour de la poignée (invisible, plus confortable)
TAG_SCREEN_PX = 12
SHORT_TAGS = {"combat": "Combat", "spell_bar": "Sorts", "hp": "PV", "ap": "PA", "mp": "PM",
              "end_turn": "Fin de tour", "identity": "Nom"}

SIDEBAR_STYLE = """
QFrame#calibrationSidebar { background: #0a0d0b; border-left: 1px solid rgba(255,255,255,20); }
QFrame#zoneRow { background: #121813; border: 1px solid rgba(255,255,255,15); border-radius: 10px; }
QFrame#zoneRow[selected="true"] { border: 1px solid #8fd14f; background: rgba(143,209,79,26); }
QLabel#zoneName { font-weight: 600; color: #e6ede4; }
QLabel#zoneHint { color: #8e9c8f; font-size: 11px; }
QLabel#chip { border-radius: 9px; padding: 2px 8px; font-size: 11px; font-weight: 600; }
QLabel#chip[state="ok"] { background: rgba(143,209,79,41); color: #b6e68a; }
QLabel#chip[state="check"] { background: rgba(229,161,58,41); color: #e5a13a; }
QLabel#chip[state="missing"] { background: rgba(255,255,255,20); color: #8e9c8f; }
QPushButton#rowAction { padding: 4px 10px; border-radius: 7px; }
QProgressBar { background: #121813; border: none; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background: #8fd14f; border-radius: 4px; }
"""


def with_note(method: str, note: str) -> str:
    """Provenance courte : origine + une seule note, jamais une répétition à chaque mouvement."""
    origin = next((part for part in method.split(" ; ") if part and part not in (MANUAL, HUMAN)
                   and "à revalider" not in part), "manuel")
    return f"{origin} ; {note}"


class ResizableRectItem(QGraphicsRectItem):
    def __init__(self, label: str, color: str, rect: QRectF, on_edit=None) -> None:
        super().__init__(QRectF(0, 0, rect.width(), rect.height()))
        self.label = label
        self.color = QColor(color)
        self.on_edit = on_edit
        self.setPos(rect.topLeft())
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self._resizing = False
        self._moved = False

    def _scale(self) -> float:
        """Pixels écran par pixel de capture (la capture 2560 px est réduite dans la fenêtre)."""
        views = self.scene().views() if self.scene() else []
        scale = views[0].transform().m11() if views else 1.0
        return scale if scale > 0 else 1.0

    def _corner_square(self, screen_px: float, cap: bool) -> QRectF:
        size = screen_px / self._scale()
        if cap:   # jamais plus d'un tiers d'une petite zone (compteur PA/PM)
            size = max(2.0, min(size, self.rect().width() / 3, self.rect().height() / 3))
        corner = self.rect().bottomRight()
        return QRectF(corner.x() - size / 2, corner.y() - size / 2, size, size)

    def _handle_rect(self) -> QRectF:
        """Petite poignée centrée sur le coin : elle déborde du cadre au lieu de masquer son contenu."""
        return self._corner_square(HANDLE_SCREEN_PX, cap=True)

    def _grab_rect(self) -> QRectF:
        return self._corner_square(GRAB_SCREEN_PX, cap=False)

    def refresh_geometry(self) -> None:
        """À appeler quand le zoom de la vue change (tailles exprimées en pixels écran)."""
        self.prepareGeometryChange()
        self.update()

    def _font(self) -> QFont:
        font = QFont("Segoe UI")
        font.setPixelSize(max(4, round(TAG_SCREEN_PX / self._scale())))
        font.setBold(True)
        return font

    def _tag_rect(self) -> QRectF:
        """Étiquette au-dessus du cadre (dedans s'il touche le haut de l'image), jamais tronquée."""
        from PySide6.QtGui import QFontMetricsF
        font = self._font()
        pad = 6 / self._scale()
        width = QFontMetricsF(font).horizontalAdvance(self.label) + 2 * pad
        height = font.pixelSize() + pad
        top = -height if self.pos().y() >= height else 0
        return QRectF(0, top, width, height)

    def boundingRect(self) -> QRectF:  # noqa: N802 - API Qt
        return super().boundingRect().united(self._tag_rect()).united(self._grab_rect()).adjusted(-2, -2, 2, 2)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        pen = QPen(self.color, 3 if self.isSelected() else 2)
        pen.setCosmetic(True)   # épaisseur lisible quelle que soit la réduction de la capture
        painter.setPen(pen)
        fill = QColor(self.color)
        fill.setAlpha(55 if self.isSelected() else 28)
        painter.setBrush(QBrush(fill))
        painter.drawRect(self.rect())
        painter.setBrush(QColor("#ffffff"))
        painter.drawRect(self._handle_rect())
        painter.setFont(self._font())
        tag = self._tag_rect()
        painter.setBrush(self.color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(tag)
        painter.setPen(QColor("#0f1510"))
        painter.drawText(tag, Qt.AlignmentFlag.AlignCenter, self.label)

    def hoverMoveEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if self._grab_rect().contains(event.pos())
                       else Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        self._resizing = self._grab_rect().contains(event.pos())
        self._moved = False
        if self._resizing:
            self.setSelected(True)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        self._moved = True
        if self._resizing:
            scene = self.scene().sceneRect() if self.scene() else QRectF(0, 0, 10000, 10000)
            minimum = 6 / self._scale()
            width = max(minimum, min(event.pos().x(), scene.right() - self.pos().x()))
            height = max(minimum, min(event.pos().y(), scene.bottom() - self.pos().y()))
            self.prepareGeometryChange()
            self.setRect(0, 0, width, height)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._resizing = False
        super().mouseReleaseEvent(event)
        if self._moved and self.on_edit:
            self.on_edit()   # une seule notification par geste, pas à chaque pixel
        self._moved = False

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            self.prepareGeometryChange()   # l'étiquette peut passer dedans/dehors
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            scene = self.scene().sceneRect()
            pos = value
            return QPointF(
                max(scene.left(), min(pos.x(), scene.right() - self.rect().width())),
                max(scene.top(), min(pos.y(), scene.bottom() - self.rect().height())),
            )
        return super().itemChange(change, value)

    def absolute_rect(self) -> QRectF:
        return QRectF(self.pos().x(), self.pos().y(), self.rect().width(), self.rect().height())


class _ImageDialog(QDialog):
    def __init__(self, frame: CapturedFrame, heading: str, parent=None) -> None:
        super().__init__(parent)
        self.frame = frame
        self.setWindowTitle(heading)
        self.resize(1280, 800)
        self.scene = QGraphicsScene(self)
        pixmap = bgr_to_pixmap(frame.image)
        self.scene.addItem(QGraphicsPixmapItem(pixmap))
        self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setStyleSheet("background: #070908;")
        self.view.setMinimumSize(600, 400)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_image)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "view"):
            QTimer.singleShot(0, self._fit_image)

    def _fit_image(self) -> None:
        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        for item in self.scene.items():
            if isinstance(item, ResizableRectItem):
                item.refresh_geometry()


class _ZoneRow(QFrame):
    """Une ligne : pastille, nom, consigne courte, badge d'état, action."""

    def __init__(self, zone: str, color: str, on_select, on_action) -> None:
        super().__init__()
        self.setObjectName("zoneRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._on_select = on_select
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)
        dot = QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(f"background: {color}; border-radius: 6px;")
        layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(1)
        name = ZONE_LABELS[zone] if zone != "identity" else "Nom et classe"
        self.name = QLabel(name + ("" if zone in REQUIRED_ZONES else "  ·  facultatif"))
        self.name.setObjectName("zoneName")
        hint = QLabel(ZONE_HINTS[zone])
        hint.setObjectName("zoneHint")
        hint.setWordWrap(True)
        text.addWidget(self.name)
        text.addWidget(hint)
        layout.addLayout(text, 1)
        right = QVBoxLayout()
        right.setSpacing(4)
        self.chip = QLabel()
        self.chip.setObjectName("chip")
        self.chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.action = QPushButton()
        self.action.setObjectName("rowAction")
        self.action.clicked.connect(on_action)
        right.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.action, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(right)

    def mousePressEvent(self, event) -> None:
        self._on_select()
        super().mousePressEvent(event)

    def set_state(self, state: str, caption: str, action: str | None, selected: bool, tooltip: str) -> None:
        self.chip.setText(caption)
        self.chip.setProperty("state", state)
        self.action.setText(action or "")
        self.action.setVisible(action is not None)
        self.setProperty("selected", "true" if selected else "false")
        self.setToolTip(tooltip)
        for widget in (self, self.chip):
            widget.style().unpolish(widget)
            widget.style().polish(widget)


class CalibrationDialog(_ImageDialog):
    def __init__(self, frame: CapturedFrame, profile_id: int, existing: Calibration | None = None,
                 parent=None, suggestions: dict[str, ZoneSuggestion] | None = None) -> None:
        super().__init__(frame, "Calibration des zones", parent)
        self.profile_id = profile_id
        self.items: dict[str, ResizableRectItem] = {}
        self.evidence: dict[str, ZoneEvidence] = {}
        self.rows: dict[str, _ZoneRow] = {}
        self.selected: str | None = None
        review = zones_needing_review(existing, frame, suggestions or {}) if existing else set()
        compatible = bool(existing and existing.layout_compatibility(frame.client.width, frame.client.height).compatible)
        initial: dict[str, QRectF] = {}
        for zone in ALL_ZONES:
            if compatible and zone in existing.zones:
                initial[zone] = QRectF(*existing.zones[zone].pixels(frame.client.width, frame.client.height))
                evidence = existing.zone_meta.get(zone, ZoneEvidence(1.0, "calibration existante", "confirmée"))
                method = (with_note(evidence.method, HUMAN) if evidence.status == "confirmée"
                          else with_note(evidence.method, "").removesuffix(" ; "))
                evidence = ZoneEvidence(evidence.confidence, method, evidence.status)
                if zone in review:
                    evidence = ZoneEvidence(evidence.confidence, with_note(evidence.method, "disposition à revalider"),
                                            "proposée")
            elif suggestions and zone in suggestions:
                initial[zone] = QRectF(*suggestions[zone].rect)
                evidence = suggestions[zone].evidence
            else:
                evidence = ZoneEvidence(0.0, "aucune preuve", "inconnue")
            self.evidence[zone] = evidence

        self.setStyleSheet(SIDEBAR_STYLE)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.view, 1)
        sidebar = QFrame()
        sidebar.setObjectName("calibrationSidebar")
        sidebar.setFixedWidth(340)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 18, 18, 18)
        side.setSpacing(10)
        heading = QLabel("Calibration")
        heading.setObjectName("title")
        side.addWidget(heading)
        intro = QLabel("Posez chaque cadre sur l'élément du jeu.\nGlisser : déplacer · coin plein : redimensionner.")
        intro.setObjectName("subtitle")
        intro.setWordWrap(True)
        side.addWidget(intro)
        rows = QWidget()
        rows_layout = QVBoxLayout(rows)
        rows_layout.setContentsMargins(0, 4, 0, 4)
        rows_layout.setSpacing(6)
        for index, zone in enumerate(ALL_ZONES):
            row = _ZoneRow(zone, COLORS[index], lambda zone=zone: self._select(zone),
                           lambda checked=False, zone=zone: self._row_action(zone))
            self.rows[zone] = row
            rows_layout.addWidget(row)
            if zone in initial:
                self._add_item(zone, initial[zone])
        side.addWidget(rows)
        side.addStretch()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        self.progress = QLabel()
        side.addWidget(self.progress)
        side.addWidget(self.progress_bar)
        self.confirm_all_button = QPushButton("Tout valider")
        self.confirm_all_button.clicked.connect(self._confirm_all)
        side.addWidget(self.confirm_all_button)
        footer = QHBoxLayout()
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        self.save_button = QPushButton("Enregistrer")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.accept)
        footer.addWidget(cancel)
        footer.addWidget(self.save_button, 1)
        side.addLayout(footer)
        root.addWidget(sidebar)
        self.scene.selectionChanged.connect(self._scene_selection)
        self.fullscreen_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        self.fullscreen_shortcut.activated.connect(
            lambda: self.showNormal() if self.isFullScreen() else self.showFullScreen())
        self._refresh()
        self.setWindowState(self.windowState() | Qt.WindowState.WindowMaximized)

    # ------------------------------------------------------------------ zones
    def _add_item(self, zone: str, rect: QRectF) -> None:
        index = ALL_ZONES.index(zone)
        item = ResizableRectItem(SHORT_TAGS[zone], COLORS[index], rect, on_edit=lambda zone=zone: self._edited(zone))
        self.scene.addItem(item)
        self.items[zone] = item

    def _state(self, zone: str) -> tuple[str, str, str | None]:
        if zone not in self.items:
            return "missing", "À placer", "Placer"
        if self.evidence[zone].status == "confirmée":
            return "ok", "Validée", None
        return "check", "À vérifier", "Valider"

    def _refresh(self) -> None:
        for zone, row in self.rows.items():
            state, caption, action = self._state(zone)
            evidence = self.evidence[zone]
            row.set_state(state, caption, action, zone == self.selected,
                          f"Origine : {evidence.method} (confiance {evidence.confidence:.0%})")
        required = [zone for zone in REQUIRED_ZONES]
        done = sum(self._state(zone)[0] == "ok" for zone in required)
        self.progress.setText(f"{done} / {len(required)} zones essentielles validées")
        self.progress_bar.setRange(0, len(required))
        self.progress_bar.setValue(done)
        pending = any(self._state(zone)[0] == "check" for zone in self.items)
        self.confirm_all_button.setEnabled(pending)
        self.save_button.setEnabled(bool(self.items))

    def _select(self, zone: str) -> None:
        self.selected = zone
        item = self.items.get(zone)
        if item is not None:
            self.scene.blockSignals(True)
            self.scene.clearSelection()
            item.setSelected(True)
            self.scene.blockSignals(False)
        self._refresh()

    def _scene_selection(self) -> None:
        chosen = next((zone for zone, item in self.items.items() if item.isSelected()), None)
        if chosen is not None and chosen != self.selected:
            self.selected = chosen
            self._refresh()

    def _row_action(self, zone: str) -> None:
        if zone not in self.items:
            self._place(zone)
        else:
            self._confirm(zone)
        self._select(zone)

    def _place(self, zone: str) -> None:
        """Cadre posé au centre de la capture, à ajuster puis valider."""
        width, height = self.frame.client.width, self.frame.client.height
        box_w, box_h = max(80, width * 0.12), max(40, height * 0.07)
        self._add_item(zone, QRectF((width - box_w) / 2, (height - box_h) / 2, box_w, box_h))
        self.evidence[zone] = ZoneEvidence(0.0, "placement manuel", "proposée")

    def _confirm(self, zone: str) -> None:
        prior = self.evidence[zone]
        self.evidence[zone] = ZoneEvidence(prior.confidence, with_note(prior.method, HUMAN), "confirmée")
        self._refresh()

    # Compatibilité des appels existants.
    def _confirm_or_add(self, zone: str) -> None:
        self._row_action(zone)

    def _confirm_all(self) -> None:
        for zone in self.items:
            if self.evidence[zone].status != "confirmée":
                self._confirm(zone)
        self._refresh()

    def _edited(self, zone: str) -> None:
        prior = self.evidence[zone]
        self.evidence[zone] = ZoneEvidence(prior.confidence, with_note(prior.method, MANUAL), "confirmée")
        self.selected = zone
        self._refresh()

    def calibration(self) -> Calibration:
        zones: dict[str, RelativeRect] = {}
        client_size = ClientSize(self.frame.client.width, self.frame.client.height)
        for zone, item in self.items.items():
            # QGraphicsScene utilise exactement les pixels de l'image cliente.
            rect = item.absolute_rect()
            zones[zone] = RelativeRect.from_client_box(
                ClientBox(rect.x(), rect.y(), rect.width(), rect.height()), client_size,
            )
        signature = LayoutSignature.create(
            client_size, {zone: rect.to_normalized_rect() for zone, rect in zones.items()},
        ).to_json()
        result = Calibration(self.profile_id, self.frame.client.width, self.frame.client.height, zones,
                             {zone: self.evidence[zone] for zone in zones}, signature)
        result.validate()
        return result


class ImageCropDialog(_ImageDialog):
    """Sélection manuelle de la zone d'infobulle visible."""

    def __init__(self, frame: CapturedFrame, parent=None) -> None:
        super().__init__(frame, "Délimiter l'infobulle visible", parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Déplacez et redimensionnez le rectangle autour du texte de l'infobulle."))
        layout.addWidget(self.view, 1)
        width, height = frame.client.width, frame.client.height
        self.item = ResizableRectItem("Infobulle", "#f0bc65",
                                      QRectF(width * 0.25, height * 0.18, width * 0.5, height * 0.5))
        self.scene.addItem(self.item)
        buttons = QHBoxLayout()
        accept = QPushButton("Analyser cette zone")
        accept.setObjectName("primary")
        accept.clicked.connect(self.accept)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(accept)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def crop(self):
        rect = self.item.absolute_rect()
        # La scène représente le client complet ; ce crop reste donc client-local.
        x, y, width, height = ClientBox(
            rect.x(), rect.y(), rect.width(), rect.height()
        ).rounded()
        return self.frame.image[y:y + height, x:x + width].copy()
