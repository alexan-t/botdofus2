"""Contrôles de l'application DofBot2 : accordéon, lignes de réglage, dock de bulles, toasts."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import (
    Property, QEasingCurve, QEvent, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
    QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from combatbot.ui.dofbot2 import theme as t
from combatbot.ui.dofbot2.icons import icon_pixmap
from combatbot.ui.dofbot2.settings import SettingsBinding
from combatbot.ui.dofbot2.widgets import FlowLayout, HoverButton, LogoBadge, Placeholder


ANIMATION_MS = 200


def mix(a: QColor, b: QColor, amount: float) -> QColor:
    return QColor.fromRgbF(*(x + (y - x) * amount for x, y in zip(a.getRgbF(), b.getRgbF())))


def label(text: str, name: str, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def button(text: str, name: str) -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName(name)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def card(name: str = "d2Card") -> QWidget:
    """Carte r18 fond #121813 (styles dans la feuille QSS)."""
    widget = QWidget()
    widget.setObjectName(name)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    return widget


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class Switch(QAbstractButton):
    """Interrupteur 40×22 : on #8fd14f + bouton #0b1408, off #2a332c + bouton #8e9c8f."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(40, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._position = 1.0 if checked else 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._moved)
        self.toggled.connect(self._animate)

    def set_state(self, checked: bool) -> None:
        """Change l'état sans animation ni signal (sélection d'un autre élément)."""
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self._animation.stop()
        self._moved(1.0 if checked else 0.0)

    def _animate(self, checked: bool) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(1.0 if checked else 0.0)
        self._animation.start()

    def _moved(self, value: float) -> None:
        self._position = float(value)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        return QSize(40, 22)

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(mix(QColor(t.SWITCH_OFF), QColor(t.GREEN), self._position))
        painter.drawRoundedRect(QRectF(0, 0, 40, 22), 11, 11)
        painter.setBrush(mix(QColor(t.TEXT_2), QColor(t.ON_GREEN), self._position))
        painter.drawEllipse(QRectF(3 + 18 * self._position, 3, 16, 16))


class Stepper(QWidget):
    """« − valeur + » h34 r17, valeur mono 600 13px sur 54px minimum."""

    value_changed = Signal(int)

    def __init__(self, value: int | None, minimum: int, maximum: int, step: int = 1,
                 formatter: Callable[[int | None], str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("d2Stepper")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.minimum, self.maximum, self.step = minimum, maximum, step
        self.formatter = formatter or (lambda value: "—" if value is None else str(value))
        self._value = value
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.minus = button("−", "d2StepButton")
        self.plus = button("+", "d2StepButton")
        self.minus.setToolTip("Diminuer")
        self.plus.setToolTip("Augmenter")
        self.display = label("", "d2StepValue")
        self.display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display.setMinimumWidth(54)
        layout.addWidget(self.minus)
        layout.addWidget(self.display)
        layout.addWidget(self.plus)
        self.minus.clicked.connect(lambda: self._shift(-1))
        self.plus.clicked.connect(lambda: self._shift(1))
        self.setFixedHeight(34)
        self._refresh()

    def value(self) -> int | None:
        return self._value

    def setValue(self, value: int | None) -> None:  # noqa: N802 - convention Qt
        self._value = None if value is None else max(self.minimum, min(self.maximum, int(value)))
        self._refresh()

    def _shift(self, direction: int) -> None:
        base = self._value if self._value is not None else (self.minimum if direction > 0 else self.maximum)
        new = max(self.minimum, min(self.maximum, base + direction * self.step)) if self._value is not None else base
        if new != self._value:
            self._value = new
            self._refresh()
            self.value_changed.emit(new)

    def _refresh(self) -> None:
        self.display.setText(self.formatter(self._value))
        self.minus.setEnabled(self._value is None or self._value > self.minimum)
        self.plus.setEnabled(self._value is None or self._value < self.maximum)


class Choices(QWidget):
    """Puces h30 r15, choix unique (valeur str) ou multiple (liste)."""

    changed = Signal(object)

    def __init__(self, options: Sequence[str], value: object, multi: bool = False,
                 disabled: Sequence[str] = (), parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.multi = multi
        self.setMaximumWidth(380)
        flow = FlowLayout(self, spacing=6)
        self.buttons: dict[str, QPushButton] = {}
        for option in options:
            chip = button(option, "d2Choice")
            chip.setCheckable(True)
            chip.setEnabled(option not in disabled)
            chip.clicked.connect(lambda _checked=False, option=option: self._clicked(option))
            flow.addWidget(chip)
            self.buttons[option] = chip
        self.setValue(value)

    def value(self) -> object:
        chosen = [option for option, chip in self.buttons.items() if chip.isChecked()]
        return chosen if self.multi else (chosen[0] if chosen else None)

    def setValue(self, value: object) -> None:  # noqa: N802 - convention Qt
        selected = set(value) if self.multi and isinstance(value, (list, tuple)) else {value}
        for option, chip in self.buttons.items():
            chip.setChecked(option in selected)

    def _clicked(self, option: str) -> None:
        if not self.multi:
            for other, chip in self.buttons.items():
                chip.setChecked(other == option)
        self.changed.emit(self.value())

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        width = sum(chip.sizeHint().width() for chip in self.buttons.values()) + 6 * max(0, len(self.buttons) - 1)
        width = min(width, 380)
        return QSize(width, self.layout().heightForWidth(width))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - API Qt
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - API Qt
        return self.layout().heightForWidth(width)


class SettingRow(QWidget):
    """Ligne d'accordéon : libellé 600 14px + description 12px à gauche, contrôle à droite."""

    def __init__(self, title: str, description: str, control: QWidget | None) -> None:
        super().__init__()
        self.setObjectName("d2Row")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 13, 0, 13)
        layout.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        texts.addWidget(label(title, "d2RowTitle", wrap=True))
        if description:
            texts.addWidget(label(description, "d2RowDesc", wrap=True))
        layout.addLayout(texts, 1)
        self.control = control
        if control is not None:
            layout.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)


# --- Lignes liées à un SettingsBinding ------------------------------------------------------------
def switch_row(binding: SettingsBinding, key: str, title: str, description: str = "", default: bool = True,
               on_change: Callable[[bool], None] | None = None) -> SettingRow:
    control = Switch(bool(binding.get(key, default)))

    def changed(checked: bool) -> None:
        binding.set(key, checked)
        if on_change:
            on_change(checked)

    control.toggled.connect(changed)
    return SettingRow(title, description, control)


def step_row(binding: SettingsBinding, key: str, title: str, description: str, default: int | None,
             minimum: int, maximum: int, step: int = 1, unit: str = "",
             on_change: Callable[[int], None] | None = None) -> SettingRow:
    def formatter(value: int | None) -> str:
        if value is None:
            return "—"
        if unit == "runs":
            return "∞" if value == 0 else str(value)
        return f"{value} {unit}" if unit else str(value)

    value = binding.get(key, default)
    control = Stepper(value if isinstance(value, int) else default, minimum, maximum, step, formatter)

    def changed(value: int) -> None:
        binding.set(key, value)
        if on_change:
            on_change(value)

    control.value_changed.connect(changed)
    return SettingRow(title, description, control)


def choice_row(binding: SettingsBinding, key: str, title: str, description: str, options: Sequence[str],
               default: object, multi: bool = False, disabled: Sequence[str] = (),
               on_change: Callable[[object], None] | None = None) -> SettingRow:
    value = binding.get(key, default)
    if multi and not isinstance(value, list):
        value = list(default)
    if not multi and value not in options:
        value = default
    control = Choices(options, value, multi, disabled)

    def changed(value: object) -> None:
        binding.set(key, value)
        if on_change:
            on_change(value)

    control.changed.connect(changed)
    return SettingRow(title, description, control)


def input_row(binding: SettingsBinding, key: str, title: str, placeholder: str,
              validate: Callable[[str], str | None] | None = None) -> SettingRow:
    """Champ 300×34 mono ; enregistré à la sortie du champ (Entrée ou perte de focus)."""
    field = QLineEdit(str(binding.get(key, "") or ""))
    field.setObjectName("d2Field")
    field.setPlaceholderText(placeholder)
    field.setFixedSize(300, 34)
    row = SettingRow(title, "", field)
    error = label("", "d2RowError", wrap=True)
    error.hide()
    row.layout().itemAt(0).layout().addWidget(error)

    def save() -> None:
        text = field.text().strip()
        problem = validate(text) if validate and text else None
        error.setText(problem or "")
        error.setVisible(bool(problem))
        if not problem:
            binding.set(key, text)

    field.editingFinished.connect(save)
    return row


def info_row(title: str, value: str, action: Callable[[], None] | None = None,
             action_text: str = "Changer") -> SettingRow:
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    layout.addWidget(label(value, "d2InfoValue"))
    if action is not None:
        link = button(action_text, "d2InfoAction")
        link.clicked.connect(action)
        layout.addWidget(link)
    return SettingRow(title, "", box)


class _AccordionHeader(HoverButton):
    """En-tête cliquable : titre 700 15px, badge « facultatif », sous-titre, chevron qui pivote."""

    def __init__(self, title: str, subtitle: str, optional: bool) -> None:
        super().__init__()
        self.title, self.subtitle, self.optional = title, subtitle, optional
        self._rotation = 0.0
        self.setFixedHeight(16 + 20 + 3 + 16 + 16)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def get_rotation(self) -> float:
        return self._rotation

    def set_rotation(self, value: float) -> None:
        self._rotation = value
        self.update()

    rotation = Property(float, get_rotation, set_rotation)

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        title_font = t.font(15, 700)
        painter.setFont(title_font)
        painter.setPen(QColor(t.TEXT))
        painter.drawText(QRectF(20, 16, self.width() - 80, 21), Qt.AlignmentFlag.AlignVCenter, self.title)
        if self.optional:
            x = 20 + QFontMetrics(title_font).horizontalAdvance(self.title) + 10
            badge_font = t.font(10, 600, mono=True)
            width = QFontMetrics(badge_font).horizontalAdvance("facultatif") + 14
            badge = QRectF(x, 18, width, 17)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.SURFACE_2))
            painter.drawRoundedRect(badge, 6, 6)
            painter.setFont(badge_font)
            painter.setPen(QColor(t.TEXT_3))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "facultatif")
        painter.setFont(t.font(12))
        painter.setPen(QColor(t.TEXT_2))
        painter.drawText(QRectF(20, 40, self.width() - 80, 16), Qt.AlignmentFlag.AlignVCenter, self.subtitle)
        center = QPointF(self.width() - 20 - 9, self.height() / 2)
        painter.translate(center)
        painter.rotate(self._rotation)
        painter.drawPixmap(-9, -9, icon_pixmap("chevron", t.TEXT_2, 18, 2.0))
        painter.resetTransform()
        self._focus_ring(painter, QRectF(self.rect()).adjusted(2, 2, -2, -2), 14)


class Accordion(QWidget):
    """Carte r16 repliable (bordure verte quand elle est ouverte)."""

    toggled = Signal(bool)

    def __init__(self, title: str, subtitle: str, rows: Sequence[QWidget], opened: bool = False,
                 optional: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("d2Acc")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.header = _AccordionHeader(title, subtitle, optional)
        self.header.clicked.connect(lambda: self.set_open(not self.is_open))
        layout.addWidget(self.header)
        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(20, 0, 20, 8)
        body_layout.setSpacing(0)
        for row in rows:
            body_layout.addWidget(row)
        layout.addWidget(self.body)
        self._rotation = QPropertyAnimation(self.header, b"rotation", self)
        self._rotation.setDuration(ANIMATION_MS)
        self._height = QPropertyAnimation(self.body, b"maximumHeight", self)
        self._height.setDuration(ANIMATION_MS)
        self._height.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._height.finished.connect(self._settled)
        self.is_open = opened
        self.body.setVisible(opened)
        self.header.set_rotation(180.0 if opened else 0.0)
        self._sync_style()

    def rows(self) -> list[QWidget]:
        layout = self.body.layout()
        return [layout.itemAt(index).widget() for index in range(layout.count())]

    def set_open(self, opened: bool, animate: bool = True) -> None:
        if opened == self.is_open:
            return
        self.is_open = opened
        self._sync_style()
        self._rotation.stop()
        self._rotation.setStartValue(self.header.get_rotation())
        self._rotation.setEndValue(180.0 if opened else 0.0)
        self._height.stop()
        target = self.body.sizeHint().height()
        if opened:
            self.body.setMaximumHeight(0)
            self.body.show()
        if animate and self.isVisible():
            self._rotation.start()
            self._height.setStartValue(self.body.height() if not opened else 0)
            self._height.setEndValue(target if opened else 0)
            self._height.start()
        else:
            self.header.set_rotation(180.0 if opened else 0.0)
            self._settled()
        self.toggled.emit(opened)

    def _settled(self) -> None:
        self.body.setMaximumHeight(16777215)
        self.body.setVisible(self.is_open)

    def _sync_style(self) -> None:
        self.setProperty("open", self.is_open)
        repolish(self)


class HeroHeader(QWidget):
    """En-tête de page : illustration 200×120 r16, titre 800 28px, texte 14px (max 480px)."""

    def __init__(self, title: str, text: str, illustration: str) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(24)
        image = Placeholder(illustration, 16)
        image.set_font_size(11)
        image.setFixedSize(200, 120)
        layout.addWidget(image)
        texts = QVBoxLayout()
        texts.setSpacing(8)
        texts.addStretch(1)
        self.title = label(title, "d2PageTitle", wrap=True)
        self.text = label(text, "d2PageText", wrap=True)
        self.text.setMaximumWidth(480)
        texts.addWidget(self.title)
        texts.addWidget(self.text)
        texts.addStretch(1)
        layout.addLayout(texts, 1)


class _Bubble(QAbstractButton):
    """Bulle du dock : 48×48 ronde, pilule verte avec libellé quand elle est active."""

    resized = Signal()

    def __init__(self, key: str, text: str) -> None:
        super().__init__()
        self.key, self.text_label = key, text
        self.setToolTip(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.badge = False
        self._amount = 0.0
        self._label_width = QFontMetrics(t.font(13, 800)).horizontalAdvance(text)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(ANIMATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._resize)
        self.setFixedSize(48, 48)

    @property
    def full_width(self) -> int:
        return 14 + 20 + 8 + self._label_width + 18

    def set_active(self, active: bool, animate: bool = True) -> None:
        target = 1.0 if active else 0.0
        self._animation.stop()
        if animate and self.isVisible():
            self._animation.setStartValue(self._amount)
            self._animation.setEndValue(target)
            self._animation.start()
        else:
            self._resize(target)

    def _resize(self, value: float) -> None:
        self._amount = float(value)
        self.setFixedWidth(round(48 + (self.full_width - 48) * self._amount))
        self.update()
        self.resized.emit()

    def event(self, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.HoverEnter, QEvent.Type.HoverLeave):
            self.update()
        return super().event(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        amount = self._amount
        if amount > 0:
            fill = QColor(t.GREEN)
            fill.setAlphaF(amount)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(self.rect()), 24, 24)
        idle = QColor(t.TEXT if self.underMouse() else t.TEXT_2)
        color = mix(idle, QColor(t.ON_GREEN), amount)
        # L'icône glisse du centre (bulle) vers la gauche (pilule : padding 14px).
        icon_x = (48 - 20) / 2 + (14 - (48 - 20) / 2) * amount
        painter.drawPixmap(round(icon_x), 14, icon_pixmap(self.key, color.name(), 20))
        if amount > 0.05:
            painter.setOpacity(amount)
            painter.setFont(t.font(13, 800))
            painter.setPen(color)
            painter.setClipRect(QRectF(icon_x + 28, 0, self.width() - icon_x - 28, 48))
            painter.drawText(QRectF(icon_x + 28, 0, self._label_width + 2, 48), Qt.AlignmentFlag.AlignVCenter,
                             self.text_label)
            painter.setOpacity(1)
            painter.setClipping(False)
        if self.badge:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(t.ALERT))
            painter.drawEllipse(QRectF(icon_x + 20 - 2, 12, 7, 7))
        if self.hasFocus():
            painter.setPen(QPen(t.green(0.8), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 23, 23)


class Dock(QWidget):
    """Dock flottant : conteneur r32 padding 7 gap 6, fond rgba(18,24,19,.86), ombre portée."""

    tab_selected = Signal(str)
    resized = Signal()   # pendant l'animation des bulles : le propriétaire recentre le dock

    def __init__(self, tabs: Sequence[tuple[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(6)
        self.bubbles: dict[str, _Bubble] = {}
        for key, text in tabs:
            bubble = _Bubble(key, text)
            bubble.clicked.connect(lambda _checked=False, key=key: self.tab_selected.emit(key))
            bubble.resized.connect(self.resized)
            layout.addWidget(bubble)
            self.bubbles[key] = bubble
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 16)
        shadow.setColor(QColor(0, 0, 0, 128))
        self.setGraphicsEffect(shadow)
        self.current: str | None = None

    def set_current(self, key: str, animate: bool = True) -> None:
        self.current = key
        for name, bubble in self.bubbles.items():
            bubble.set_active(name == key, animate)

    def set_badge(self, key: str, shown: bool) -> None:
        self.bubbles[key].badge = shown
        self.bubbles[key].update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(t.white(0.08), 1))
        painter.setBrush(QColor(18, 24, 19, round(0.86 * 255)))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 31.5, 31.5)


class Toast(QWidget):
    """Carte r14 fond #161e17 : pastille « D2 » 32px r10, titre 700 13px, texte 12px ; un clic la ferme."""

    closed = Signal(object)

    def __init__(self, title: str, body: str, color: str = t.TEXT, duration_ms: int = 4500,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("d2Toast")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedWidth(320)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Cliquer pour fermer")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)
        layout.addWidget(LogoBadge(32, 10, 12), 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = label(title, "d2ToastTitle", wrap=True)
        self.title.setStyleSheet(f"color: {QColor(color).name()};")
        self.body = label(body, "d2ToastBody", wrap=True)
        texts.addWidget(self.title)
        texts.addWidget(self.body)
        layout.addLayout(texts, 1)
        self._dismissed = False
        self._timer = QTimer(self)   # détruit avec le toast : pas d'appel sur un widget supprimé
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        if duration_ms > 0:
            self._timer.start(duration_ms)

    def dismiss(self) -> None:
        if not self._dismissed:
            self._dismissed = True
            self.closed.emit(self)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - API Qt
        self.dismiss()
        super().mousePressEvent(event)


class ToastStack(QWidget):
    """Pile de 3 toasts au plus, gap 10 ; ancrée en bas à droite par son propriétaire."""

    MAX = 3
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if parent is None:   # fenêtre toast autonome : elle ne reçoit pas la feuille de DofBot2
            self.setStyleSheet(t.STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.toasts: list[Toast] = []

    def push(self, title: str, body: str, color: str = t.TEXT, duration_ms: int = 4500) -> Toast:
        toast = Toast(title, body, color, duration_ms)
        toast.closed.connect(self._remove)
        self.layout().addWidget(toast)
        self.toasts.append(toast)
        while len(self.toasts) > self.MAX:
            self._remove(self.toasts[0])
        self._fit()
        return toast

    def _remove(self, toast: Toast) -> None:
        if toast in self.toasts:
            self.toasts.remove(toast)
            self.layout().removeWidget(toast)
            toast.hide()
            toast.deleteLater()
            self._fit()

    def _fit(self) -> None:
        self.setVisible(bool(self.toasts))
        self.adjustSize()
        self.changed.emit()
