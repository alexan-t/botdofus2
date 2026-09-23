"""Rectangles déplaçables et redimensionnables sur une capture réelle."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QBrush, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QDialog, QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from combatbot.ui.images import bgr_to_pixmap
from combatbot.vision.models import Calibration, CapturedFrame, RelativeRect, ZoneEvidence, ZONE_LABELS, ZONE_NAMES
from combatbot.vision.autocalibration import ZoneSuggestion, zones_needing_review
from combatbot.vision.coordinates import ClientBox, ClientSize, LayoutSignature


COLORS = ("#5dd6bd", "#f0bc65", "#e77783", "#8eaaf0", "#b59cf2", "#62c0e2", "#ffffff")


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
        self._handle = 13

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setPen(QPen(self.color, 3 if self.isSelected() else 2))
        fill = QColor(self.color)
        fill.setAlpha(35)
        painter.setBrush(QBrush(fill))
        painter.drawRect(self.rect())
        painter.setBrush(self.color)
        painter.drawRect(QRectF(self.rect().right() - self._handle, self.rect().bottom() - self._handle,
                                self._handle, self._handle))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(self.rect().adjusted(5, 4, -4, -4), self.label)

    def mousePressEvent(self, event) -> None:
        handle_rect = QRectF(self.rect().right() - self._handle, self.rect().bottom() - self._handle,
                             self._handle, self._handle)
        self._resizing = handle_rect.contains(event.pos())
        if self._resizing:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resizing:
            scene = self.scene().sceneRect() if self.scene() else QRectF(0, 0, 10000, 10000)
            width = max(16, min(event.pos().x(), scene.right() - self.pos().x()))
            height = max(16, min(event.pos().y(), scene.bottom() - self.pos().y()))
            self.setRect(0, 0, width, height)
            if self.on_edit:
                self.on_edit()
            event.accept()
        else:
            super().mouseMoveEvent(event)
            if self.on_edit:
                self.on_edit()

    def mouseReleaseEvent(self, event) -> None:
        self._resizing = False
        if self.on_edit:
            self.on_edit()
        super().mouseReleaseEvent(event)

    def itemChange(self, change, value):
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
        self.resize(1100, 760)
        self.scene = QGraphicsScene(self)
        pixmap = bgr_to_pixmap(frame.image)
        self.scene.addItem(QGraphicsPixmapItem(pixmap))
        self.scene.setSceneRect(0, 0, pixmap.width(), pixmap.height())
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setMinimumSize(700, 500)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_image)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "view"):
            QTimer.singleShot(0, self._fit_image)

    def _fit_image(self) -> None:
        self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)


class CalibrationDialog(_ImageDialog):
    def __init__(self, frame: CapturedFrame, profile_id: int, existing: Calibration | None = None,
                 parent=None, suggestions: dict[str, ZoneSuggestion] | None = None) -> None:
        super().__init__(frame, "Calibrer les zones du client", parent)
        self.profile_id = profile_id
        self.items: dict[str, ResizableRectItem] = {}
        self.evidence: dict[str, ZoneEvidence] = {}
        self.zone_labels: dict[str, QLabel] = {}
        review = zones_needing_review(existing, frame, suggestions or {}) if existing else set()
        layout = QHBoxLayout(self)
        layout.addWidget(self.view, 4)
        side = QVBoxLayout()
        instruction = QLabel("Vérifiez les zones sur l'image. Glissez les rectangles et tirez leur carré pour les ajuster. "
                             "Ajoutez individuellement une zone inconnue.")
        instruction.setWordWrap(True)
        side.addWidget(instruction)
        for index, zone in enumerate((*ZONE_NAMES, "identity")):
            if existing and existing.layout_compatibility(
                frame.client.width, frame.client.height
            ).compatible and zone in existing.zones:
                rect = QRectF(*existing.zones[zone].pixels(frame.client.width, frame.client.height))
                evidence = existing.zone_meta.get(zone, ZoneEvidence(1.0, "calibration existante", "confirmée"))
                if zone in review:
                    evidence = ZoneEvidence(evidence.confidence, evidence.method + " ; disposition à revalider", "proposée")
            elif suggestions and zone in suggestions:
                rect = QRectF(*suggestions[zone].rect)
                evidence = suggestions[zone].evidence
            else:
                rect = None
                evidence = ZoneEvidence(0.0, "aucune preuve", "inconnue")
            self.evidence[zone] = evidence
            row = QHBoxLayout()
            label = QLabel()
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {COLORS[index]};")
            self.zone_labels[zone] = label
            row.addWidget(label, 1)
            button = QPushButton("Ajouter" if rect is None else "Confirmer")
            button.clicked.connect(lambda checked=False, zone=zone: self._confirm_or_add(zone))
            row.addWidget(button)
            side.addLayout(row)
            if rect is not None:
                self._add_item(zone, COLORS[index], rect)
            self._update_zone_label(zone)
        side.addStretch()
        self.progress = QLabel()
        side.addWidget(self.progress)
        confirm_all = QPushButton("Confirmer les zones détectées")
        confirm_all.clicked.connect(self._confirm_all)
        side.addWidget(confirm_all)
        self.fullscreen_button = QPushButton("Mode fenêtre  ·  F11")
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)
        side.addWidget(self.fullscreen_button)
        self.save_button = QPushButton("Enregistrer la calibration")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.accept)
        side.addWidget(self.save_button)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        side.addWidget(cancel)
        layout.addLayout(side, 1)
        self._update_progress()
        self.fullscreen_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        self.fullscreen_shortcut.activated.connect(self._toggle_fullscreen)
        self.setWindowState(self.windowState() | Qt.WindowState.WindowFullScreen)

    def _add_item(self, zone: str, color: str, rect: QRectF) -> None:
        item = ResizableRectItem(ZONE_LABELS[zone], color, rect,
                                 on_edit=lambda zone=zone: self._edited(zone))
        self.scene.addItem(item)
        self.items[zone] = item

    def _update_zone_label(self, zone: str) -> None:
        evidence = self.evidence[zone]
        self.zone_labels[zone].setText(
            f"{ZONE_LABELS[zone]} : {evidence.status} ({evidence.confidence:.0%}, {evidence.method})"
        )

    def _confirm_or_add(self, zone: str) -> None:
        if zone not in self.items:
            index = (*ZONE_NAMES, "identity").index(zone)
            x = min(20 + index * 28, max(0, self.frame.client.width - 180))
            y = min(20 + index * 28, max(0, self.frame.client.height - 65))
            self._add_item(zone, COLORS[index], QRectF(x, y, min(180, self.frame.client.width - x),
                                                       min(55, self.frame.client.height - y)))
            self.evidence[zone] = ZoneEvidence(0.0, "placement manuel à ajuster", "proposée")
        else:
            prior = self.evidence[zone]
            self.evidence[zone] = ZoneEvidence(prior.confidence, prior.method + " ; vérification humaine", "confirmée")
        self._update_zone_label(zone)
        self._update_progress()

    def _confirm_all(self) -> None:
        for zone in self.items:
            prior = self.evidence[zone]
            self.evidence[zone] = ZoneEvidence(prior.confidence, prior.method + " ; vérification humaine", "confirmée")
            self._update_zone_label(zone)
        self._update_progress()

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "fullscreen_button"):
            self.fullscreen_button.setText(
                "Mode fenêtre  ·  F11" if self.isFullScreen() else "Plein écran  ·  F11"
            )

    def _edited(self, zone: str) -> None:
        prior = self.evidence[zone]
        self.evidence[zone] = ZoneEvidence(prior.confidence, prior.method + " ; ajustée manuellement", "confirmée")
        self._update_zone_label(zone)
        self._update_progress()

    def _update_progress(self) -> None:
        count = sum(self.evidence[zone].status == "confirmée" for zone in self.items)
        self.progress.setText(f"Zones confirmées : {count}/{len(self.items)} · autres zones facultatives")
        self.save_button.setEnabled(bool(self.items))

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
