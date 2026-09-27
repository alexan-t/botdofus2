"""Écran A7 : un outil avancé ouvert en plein écran dans la fenêtre DofBot2.

Les outils restent les dialogues existants (même logique, mêmes vérités humaines) ; ils reçoivent
l'en-tête DofBot2 (titre, sous-titre, ✕ / Échap) et recouvrent la zone de contenu de la fenêtre."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QAbstractButton, QDialog, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from combatbot.ui.dofbot2 import theme as t
from combatbot.ui.dofbot2.controls import label
from combatbot.ui.theme import STYLE as LEGACY_STYLE
from combatbot.ui.tool_host import TOOLS


HEADER_HEIGHT = 60


class CloseButton(QAbstractButton):
    """Rond 36px fond #121813, croix dessinée (le glyphe ✕ manque dans Manrope)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Fermer (Échap)")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def event(self, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self.update()
        return super().event(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered = self.underMouse()
        painter.setPen(QPen(t.white(0.2 if hovered else 0.08), 1))
        painter.setBrush(QColor(t.SURFACE))
        painter.drawEllipse(QRectF(0.5, 0.5, 35, 35))
        pen = QPen(QColor(t.TEXT if hovered else t.TEXT_2), 1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(13.5, 13.5), QPointF(22.5, 22.5))
        painter.drawLine(QPointF(22.5, 13.5), QPointF(13.5, 22.5))


class ToolHeader(QWidget):
    close_clicked = Signal()

    def __init__(self, title: str, subtitle: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("d2ToolHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(t.STYLE)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 20, 0)
        layout.setSpacing(14)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addStretch(1)
        texts.addWidget(label(title, "d2ToolTitle"))
        texts.addWidget(label(subtitle, "d2ToolSub"))
        texts.addStretch(1)
        layout.addLayout(texts, 1)
        close = CloseButton()
        close.clicked.connect(self.close_clicked)
        layout.addWidget(close)


class _HeaderSync(QObject):
    """Garde l'en-tête en haut du dialogue et arrondit les coins bas comme la fenêtre."""

    def __init__(self, dialog: QDialog, header: ToolHeader, rounded: Callable[[], bool]) -> None:
        super().__init__(dialog)
        self.dialog, self.header, self.rounded = dialog, header, rounded

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if watched is self.dialog and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.header.setGeometry(0, 0, self.dialog.width(), HEADER_HEIGHT)
            self.header.raise_()
            self.apply_mask()
        return False

    def apply_mask(self) -> None:
        rect = self.dialog.rect()
        if not self.rounded():
            self.dialog.clearMask()
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(rect), 13, 13)
        region = QRegion(path.toFillPolygon().toPolygon()).united(QRegion(0, 0, rect.width(), 13))
        self.dialog.setMask(region)


def present_in(host, dialog: QDialog, key: str) -> bool:
    """Présentateur DofBot2 : ``host`` est la fenêtre DofBot2 (``tool_area()`` et fil d'Ariane)."""
    title, subtitle = TOOLS[key]
    present(host, dialog, title, subtitle)
    return True


def present(host, dialog: QDialog, title: str, subtitle: str) -> None:
    dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
    dialog.setWindowState(Qt.WindowState.WindowNoState)
    header = ToolHeader(title, subtitle, dialog)
    header.close_clicked.connect(dialog.reject)
    layout = dialog.layout()
    if layout is not None:
        margins = layout.contentsMargins()
        layout.setContentsMargins(margins.left(), margins.top() + HEADER_HEIGHT, margins.right(), margins.bottom())
    sync = _HeaderSync(dialog, header, lambda: not host.is_expanded())
    dialog.installEventFilter(sync)
    area = host.tool_area()
    dialog.setGeometry(area)
    header.setGeometry(0, 0, area.width(), HEADER_HEIGHT)
    host.tool_opened(title)
    dialog.finished.connect(lambda _result: host.tool_closed())


def show_legacy_page(host, page: QWidget, title: str, subtitle: str) -> None:
    """Affiche une page de l'interface historique comme un outil, puis la lui rend à la fermeture."""
    parent = page.parentWidget()
    stack = parent if isinstance(parent, QStackedWidget) else None
    index = stack.indexOf(page) if stack is not None else -1
    dialog = QDialog(host)
    dialog.setStyleSheet(LEGACY_STYLE)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 16, 24, 20)
    layout.addWidget(page)
    page.show()
    present(host, dialog, title, subtitle)
    try:
        dialog.exec()
    finally:
        layout.removeWidget(page)
        if stack is not None:
            stack.insertWidget(index, page)
        else:
            page.setParent(parent)
        dialog.deleteLater()


def global_area(widget: QWidget, top: int) -> QRect:
    """Zone de contenu d'une fenêtre (sous la barre de titre) en coordonnées écran."""
    origin = widget.mapToGlobal(QPoint(1, top))
    return QRect(origin.x(), origin.y(), widget.width() - 2, widget.height() - top - 1)
