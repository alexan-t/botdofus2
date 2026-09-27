"""Composants DofBot2 dessinés à la main pour respecter la maquette au pixel près."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFontMetrics, QImage, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QLayout, QSizePolicy, QWidget

from combatbot.ui.dofbot2 import theme as t


def _antialiased(widget: QWidget) -> QPainter:
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    return painter


def draw_check(painter: QPainter, center: QPointF, size: float, color: QColor, width: float = 1.8) -> None:
    """Coche vectorielle : le glyphe ✓ n'existe pas dans Manrope."""
    path = QPainterPath(QPointF(center.x() - size * 0.5, center.y()))
    path.lineTo(center.x() - size * 0.15, center.y() + size * 0.35)
    path.lineTo(center.x() + size * 0.5, center.y() - size * 0.35)
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)


def rounded_pixmap(image: QImage, size: QSize, radius: float) -> QPixmap:
    """Image recadrée au centre (cover) puis découpée avec des coins arrondis."""
    ratio = 2.0
    target = QPixmap(round(size.width() * ratio), round(size.height() * ratio))
    target.fill(Qt.GlobalColor.transparent)
    scaled = image.scaled(target.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                          Qt.TransformationMode.SmoothTransformation)
    x, y = (scaled.width() - target.width()) // 2, (scaled.height() - target.height()) // 2
    painter = QPainter(target)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(target.rect()), radius * ratio, radius * ratio)
    painter.setClipPath(clip)
    painter.drawImage(0, 0, scaled, x, y, target.width(), target.height())
    painter.end()
    target.setDevicePixelRatio(ratio)
    return target


class LogoBadge(QWidget):
    """Pastille « D2 » ; ``halo`` ajoute l'anneau et la lueur de l'écran de démarrage."""

    def __init__(self, size: int, radius: int, text_size: int, halo: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.box, self.radius, self.text_size, self.halo = size, radius, text_size, halo
        self.margin = 60 if halo else 0
        self.setFixedSize(size + 2 * self.margin, size + 2 * self.margin)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = _antialiased(self)
        rect = QRectF(self.margin, self.margin, self.box, self.box)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.halo:
            glow = QRadialGradient(rect.center(), self.box / 2 + self.margin)
            glow.setColorAt(0.0, t.green(0.25))
            glow.setColorAt(0.55, t.green(0.10))
            glow.setColorAt(1.0, t.green(0.0))
            painter.setBrush(glow)
            painter.drawEllipse(rect.center(), self.box / 2 + self.margin, self.box / 2 + self.margin)
            painter.setBrush(t.green(0.08))
            painter.drawRoundedRect(rect.adjusted(-10, -10, 10, 10), self.radius + 10, self.radius + 10)
        painter.setBrush(QColor(t.GREEN))
        painter.drawRoundedRect(rect, self.radius, self.radius)
        painter.setPen(QColor(t.ON_GREEN))
        painter.setFont(t.font(self.text_size, 800, spacing=-0.04))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "D2")


class Avatar(QWidget):
    """Rond de couleur avec initiale, ou image importée découpée en cercle."""

    def __init__(self, size: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.color = QColor(t.GREEN)
        self.initial = "?"
        self.image: QImage | None = None
        self.halo = False

    def set_avatar(self, color: str, initial: str, image_png: bytes | None = None) -> None:
        self.color = QColor(color)
        self.initial = initial
        self.image = None
        if image_png:
            image = QImage.fromData(image_png)
            self.image = None if image.isNull() else image
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = _antialiased(self)
        side = min(self.width(), self.height())
        rect = QRectF(0, 0, side, side)
        if self.halo:  # anneau très discret de l'aperçu (box-shadow 0 0 0 8px rgba(255,255,255,.03))
            rect = rect.adjusted(8, 8, -8, -8)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(t.white(0.03))
            painter.drawEllipse(QRectF(0, 0, side, side))
        if self.image is not None:
            painter.drawPixmap(rect.toRect(), rounded_pixmap(self.image, rect.size().toSize(), rect.width() / 2))
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.color)
        painter.drawEllipse(rect)
        painter.setPen(QColor(t.ON_GREEN))
        painter.setFont(t.font(round(rect.width() * 0.4), 800))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.initial)


class ThinProgress(QWidget):
    """Barre 260×4 r2, piste #1b231e, remplissage vert."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(260, 4)
        self._value = 0

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:  # noqa: N802 - même nom que QProgressBar
        self._value = max(0, min(100, int(value)))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = _antialiased(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.SURFACE_2))
        painter.drawRoundedRect(QRectF(self.rect()), 2, 2)
        if self._value:
            painter.setBrush(QColor(t.GREEN))
            painter.drawRoundedRect(QRectF(0, 0, self.width() * self._value / 100, self.height()), 2, 2)


class Placeholder(QWidget):
    """Emplacement d'illustration hachuré, à remplacer par un vrai visuel."""

    def __init__(self, text: str, radius: int = 18, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.text, self.radius = text, radius
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = _antialiased(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)
        painter.fillPath(path, QColor(t.PLACEHOLDER))
        painter.save()
        painter.setClipPath(path)
        painter.setPen(QPen(t.green(0.05), 7.07))  # bandes de 10px à 135°, espacées de 20px
        span = self.width() + self.height()
        for offset in range(-self.height(), span, 20):
            painter.drawLine(QPointF(offset, self.height()), QPointF(offset + self.height(), 0))
        painter.restore()
        painter.setPen(QPen(t.green(0.15), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.setPen(QColor(t.TEXT_3))
        painter.setFont(t.font(12, 500, mono=True))
        painter.drawText(rect.adjusted(24, 24, -24, -24), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                         self.text)


class StepIndicator(QWidget):
    """« Connexion · Profil · C'est parti » : fait, en cours, à venir."""

    STEPS = ("Connexion", "Profil", "C'est parti")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.current = 0
        self.setFixedHeight(24)
        metrics = QFontMetrics(t.font(13, 600))
        self._widths = [24 + 10 + metrics.horizontalAdvance(label) for label in self.STEPS]
        self.setFixedWidth(sum(self._widths) + 28 * (len(self.STEPS) - 1) + 2)

    def set_current(self, index: int) -> None:
        self.current = index
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = _antialiased(self)
        x = 1.0
        for index, (label, width) in enumerate(zip(self.STEPS, self._widths)):
            circle = QRectF(x, 0.5, 23, 23)
            done, current = index < self.current, index == self.current
            if done:
                painter.setPen(QPen(QColor(t.GREEN), 1))
                painter.setBrush(QColor(t.GREEN))
            elif current:
                painter.setPen(QPen(QColor(t.GREEN), 1))
                painter.setBrush(t.green(0.14))
            else:
                painter.setPen(QPen(t.white(0.12), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(circle)
            if done:
                draw_check(painter, circle.center(), 9, QColor(t.ON_GREEN), 1.8)
            else:
                painter.setPen(QColor(t.GREEN if current else t.TEXT_MUTED))
                painter.setFont(t.font(11, 700))
                painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(index + 1))
            painter.setPen(QColor(t.TEXT if index <= self.current else t.TEXT_MUTED))
            painter.setFont(t.font(13, 600))
            painter.drawText(QRectF(x + 34, 0, width - 34, 24), Qt.AlignmentFlag.AlignVCenter, label)
            x += width + 28


class HoverButton(QAbstractButton):
    """Bouton peint : suit le survol et affiche la main."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._keyboard_focus = False

    def event(self, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self.update()
        return super().event(event)

    @property
    def hovered(self) -> bool:
        return self.underMouse()

    def focusInEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(event)

    def _focus_ring(self, painter: QPainter, rect: QRectF, radius: float) -> None:
        """Anneau visible seulement pour la navigation au clavier (Tab / Espace)."""
        if self.hasFocus() and self._keyboard_focus:
            painter.setPen(QPen(t.green(0.8), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)


class WindowCard(HoverButton):
    """Fenêtre détectée : miniature, titre, méta mono, bouton radio."""

    def __init__(self, hwnd: int, title: str, meta: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.hwnd, self.title, self.meta = hwnd, title, meta
        self.thumbnail: QPixmap | None = None
        self.setCheckable(True)
        self.setFixedHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(title)

    def set_thumbnail(self, image: QImage | None) -> None:
        self.thumbnail = None if image is None or image.isNull() else rounded_pixmap(image, QSize(44, 30), 6)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        return QSize(360, 60)

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = _antialiased(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        selected = self.isChecked()
        painter.setPen(QPen(t.green(0.6) if selected else (t.white(0.12) if self.hovered else t.white(0.06)), 1))
        painter.setBrush(t.green(0.07) if selected else QColor(t.SURFACE))
        painter.drawRoundedRect(rect, 14, 14)
        thumb = QRectF(16, (self.height() - 30) / 2, 44, 30)
        if self.thumbnail is not None:
            painter.drawPixmap(thumb.toRect(), self.thumbnail)
        else:
            painter.setPen(QPen(t.white(0.08), 1))
            painter.setBrush(QColor(t.SURFACE_2))
            painter.drawRoundedRect(thumb.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        text_left, text_right = 16 + 44 + 14, self.width() - 16 - 18 - 14
        title_font = t.font(14, 700)
        painter.setFont(title_font)
        painter.setPen(QColor(t.TEXT))
        title = QFontMetrics(title_font).elidedText(self.title, Qt.TextElideMode.ElideRight, text_right - text_left)
        painter.drawText(QRectF(text_left, 11, text_right - text_left, 20), Qt.AlignmentFlag.AlignVCenter, title)
        painter.setFont(t.font(11, 500, mono=True))
        painter.setPen(QColor(t.TEXT_3))
        painter.drawText(QRectF(text_left, 32, text_right - text_left, 16), Qt.AlignmentFlag.AlignVCenter, self.meta)
        radio = QRectF(self.width() - 16 - 18, (self.height() - 18) / 2, 18, 18)
        painter.setPen(QPen(QColor(t.GREEN if selected else "#3a453c"), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(radio.adjusted(1, 1, -1, -1))
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.GREEN))
            painter.drawEllipse(radio.center(), 4, 4)
        self._focus_ring(painter, rect.adjusted(-0.5, -0.5, 0.5, 0.5), 14)


class ProfileCard(HoverButton):
    """Carte 176px : avatar 80px, nom, « Classe · niv. n »."""

    def __init__(self, profile_id: int, name: str, meta: str, color: str, initial: str,
                 image_png: bytes | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.profile_id, self.name, self.meta = profile_id, name, meta
        self.setFixedSize(176, 26 + 80 + 12 + 22 + 6 + 16 + 20)
        self.avatar = Avatar(80, self)
        self.avatar.move((176 - 80) // 2, 26)
        self.avatar.set_avatar(color, initial, image_png)
        self.setToolTip(name)

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = _antialiased(self)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(t.green(0.5) if self.hovered else t.white(0.06), 1))
        painter.setBrush(QColor(t.SURFACE_HOVER if self.hovered else t.SURFACE))
        painter.drawRoundedRect(rect, 20, 20)
        name_font = t.font(16, 700)
        painter.setFont(name_font)
        painter.setPen(QColor(t.TEXT))
        name = QFontMetrics(name_font).elidedText(self.name, Qt.TextElideMode.ElideRight, self.width() - 32)
        painter.drawText(QRectF(16, 118, self.width() - 32, 22), Qt.AlignmentFlag.AlignCenter, name)
        painter.setFont(t.font(12))
        painter.setPen(QColor(t.TEXT_2))
        painter.drawText(QRectF(16, 140, self.width() - 32, 16), Qt.AlignmentFlag.AlignCenter, self.meta)
        self._focus_ring(painter, rect, 20)


class NewProfileCard(HoverButton):
    """Carte « Nouveau profil » en pointillés."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(176, 176)

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = _antialiased(self)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        pen = QPen(t.green(0.35), 1.5)
        pen.setDashPattern([4, 3])
        painter.setPen(pen)
        painter.setBrush(t.green(0.05) if self.hovered else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 20, 20)
        circle = QRectF((self.width() - 80) / 2, 26, 80, 80)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(t.green(0.1))
        painter.drawEllipse(circle)
        plus = QPen(QColor(t.GREEN), 2)
        plus.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(plus)
        center = circle.center()
        painter.drawLine(QPointF(center.x() - 11, center.y()), QPointF(center.x() + 11, center.y()))
        painter.drawLine(QPointF(center.x(), center.y() - 11), QPointF(center.x(), center.y() + 11))
        painter.setFont(t.font(16, 700))
        painter.setPen(QColor(t.GREEN))
        painter.drawText(QRectF(8, 118, self.width() - 16, 22), Qt.AlignmentFlag.AlignCenter, "Nouveau profil")
        painter.setFont(t.font(12))
        painter.setPen(QColor(t.TEXT_2))
        painter.drawText(QRectF(8, 140, self.width() - 16, 16), Qt.AlignmentFlag.AlignCenter, "Avatar et nom au choix")
        self._focus_ring(painter, rect, 20)


class AvatarSwatch(HoverButton):
    """Rond de couleur 44px ; sélection = anneau 0 0 0 3px fond, 0 0 0 5px couleur."""

    def __init__(self, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.color = QColor(color)
        self.setCheckable(True)
        self.setFixedSize(54, 54)  # 44px + 5px d'anneau de chaque côté
        self.setToolTip("Couleur d'avatar")

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = _antialiased(self)
        painter.setPen(Qt.PenStyle.NoPen)
        if self.isChecked():
            painter.setBrush(self.color)
            painter.drawEllipse(QRectF(0, 0, 54, 54))
            painter.setBrush(QColor(t.WINDOW))
            painter.drawEllipse(QRectF(2, 2, 50, 50))
        painter.setBrush(self.color.lighter(108) if self.hovered and not self.isChecked() else self.color)
        painter.drawEllipse(QRectF(5, 5, 44, 44))


class UploadTile(HoverButton):
    """Tuile « ↑ » pour importer une image ; affiche l'image importée une fois choisie."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(54, 54)
        self.image: QImage | None = None
        self.setToolTip("Importer une image")

    def set_image(self, image: QImage | None) -> None:
        self.image = image
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = _antialiased(self)
        inner = QRectF(5, 5, 44, 44)
        if self.isChecked() and self.image is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.GREEN))
            painter.drawEllipse(QRectF(0, 0, 54, 54))
            painter.setBrush(QColor(t.WINDOW))
            painter.drawEllipse(QRectF(2, 2, 50, 50))
        if self.image is not None:
            painter.drawPixmap(inner.toRect(), rounded_pixmap(self.image, QSize(44, 44), 22))
            return
        pen = QPen(QColor(t.GREEN) if self.hovered else t.white(0.2), 1.5)
        pen.setDashPattern([3, 2.5])
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(inner.adjusted(0.75, 0.75, -0.75, -0.75))
        painter.setPen(QColor(t.TEXT if self.hovered else t.TEXT_2))
        painter.setFont(t.font(18))
        painter.drawText(inner, Qt.AlignmentFlag.AlignCenter, "↑")


class FlowLayout(QLayout):
    """Disposition « flex-wrap » : éléments en lignes, lignes éventuellement centrées."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8, row_spacing: int | None = None,
                 centered: bool = False) -> None:
        super().__init__(parent)
        self._items = []
        self._spacing, self._row_spacing, self.centered = spacing, spacing if row_spacing is None else row_spacing, centered
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:  # noqa: N802 - API Qt
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - API Qt
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802 - API Qt
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - API Qt
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - API Qt
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - API Qt
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - API Qt
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - API Qt
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _arrange(self, rect: QRect, apply: bool) -> int:
        rows: list[list] = [[]]
        line_width = 0
        for item in self._items:
            if item.isEmpty():
                continue
            width = item.sizeHint().width()
            if rows[-1] and line_width + self._spacing + width > rect.width():
                rows.append([])
                line_width = 0
            line_width += (self._spacing if rows[-1] else 0) + width
            rows[-1].append(item)
        y = rect.y()
        for row in rows:
            if not row:
                continue
            total = sum(item.sizeHint().width() for item in row) + self._spacing * (len(row) - 1)
            height = max(item.sizeHint().height() for item in row)
            x = rect.x() + ((rect.width() - total) // 2 if self.centered else 0)
            for item in row:
                hint = item.sizeHint()
                if apply:
                    item.setGeometry(QRect(QPoint(x, y + (height - hint.height()) // 2), hint))
                x += hint.width() + self._spacing
            y += height + self._row_spacing
        return max(0, y - rect.y() - self._row_spacing)


class WindowButton(QAbstractButton):
    """Boutons 46px de la barre de titre, dessinés (les glyphes ▢/✕ manquent dans Manrope)."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.maximized = False
        self.setFixedSize(46, 40)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip({"min": "Réduire", "max": "Agrandir", "close": "Fermer"}[kind])

    def event(self, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self.update()
        return super().event(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = _antialiased(self)
        hovered = self.underMouse()
        if hovered:
            painter.fillRect(self.rect(), QColor(t.CLOSE_HOVER) if self.kind == "close" else t.white(0.06))
        color = QColor("#ffffff") if hovered and self.kind == "close" else QColor(t.TEXT if hovered else t.TEXT_2)
        pen = QPen(color, 1.1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.kind == "min":
            painter.drawLine(QPointF(c.x() - 5, c.y()), QPointF(c.x() + 5, c.y()))
        elif self.kind == "max":
            if self.maximized:
                painter.drawRect(QRectF(c.x() - 5, c.y() - 3, 8, 8))
                painter.drawPolyline([QPointF(c.x() - 3, c.y() - 3), QPointF(c.x() - 3, c.y() - 5),
                                      QPointF(c.x() + 5, c.y() - 5), QPointF(c.x() + 5, c.y() + 3),
                                      QPointF(c.x() + 3, c.y() + 3)])
            else:
                painter.drawRoundedRect(QRectF(c.x() - 5, c.y() - 5, 10, 10), 1.5, 1.5)
        else:
            painter.drawLine(QPointF(c.x() - 5, c.y() - 5), QPointF(c.x() + 5, c.y() + 5))
            painter.drawLine(QPointF(c.x() + 5, c.y() - 5), QPointF(c.x() - 5, c.y() + 5))


class TitleBar(QWidget):
    """Barre de titre custom 40px : logo, nom, fil d'Ariane, boutons de fenêtre."""

    minimize_requested = Signal()
    maximize_requested = Signal()
    close_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("d2TitleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedHeight(40)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(LogoBadge(20, 6, 10))
        name = QLabel("DofBot2")
        name.setObjectName("d2TitleName")
        layout.addWidget(name)
        self.crumb = QLabel("")
        self.crumb.setObjectName("d2Crumb")
        layout.addWidget(self.crumb)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.setSpacing(0)
        self.min_button = WindowButton("min")
        self.max_button = WindowButton("max")
        self.close_button = WindowButton("close")
        self.min_button.clicked.connect(self.minimize_requested)
        self.max_button.clicked.connect(self.maximize_requested)
        self.close_button.clicked.connect(self.close_requested)
        for button in (self.min_button, self.max_button, self.close_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)

    def set_crumb(self, text: str) -> None:
        self.crumb.setText(f"· {text}" if text else "")

    def set_maximized(self, maximized: bool) -> None:
        self.max_button.maximized = maximized
        self.max_button.setToolTip("Restaurer" if maximized else "Agrandir")
        self.max_button.update()
        self.setProperty("maximized", maximized)
        self.style().unpolish(self)
        self.style().polish(self)

    # Le déplacement ne démarre qu'après quelques pixels : un double-clic reste possible (agrandir).
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - API Qt
        self._press = event.position().toPoint() if event.button() == Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - API Qt
        press = getattr(self, "_press", None)
        handle = self.window().windowHandle()
        if press is not None and handle is not None and event.buttons() & Qt.MouseButton.LeftButton \
                and (event.position().toPoint() - press).manhattanLength() > 3:
            self._press = None
            handle.startSystemMove()
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - API Qt
        if event.button() == Qt.MouseButton.LeftButton:
            self.maximize_requested.emit()
        super().mouseDoubleClickEvent(event)
