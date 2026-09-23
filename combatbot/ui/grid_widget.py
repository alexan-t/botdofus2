"""Visualisation de la grille de combat simulée."""

from __future__ import annotations

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from combatbot.models import CombatSnapshot


class GridWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.snapshot: CombatSnapshot | None = None
        self.setMinimumSize(540, 420)

    def set_snapshot(self, snapshot: CombatSnapshot) -> None:
        self.snapshot = snapshot
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#121c2a"))
        snap = self.snapshot
        if snap is None:
            painter.end()
            return
        margin = 30
        cell = min((self.width() - 2 * margin) / snap.width, (self.height() - 2 * margin) / snap.height)
        start_x = (self.width() - snap.width * cell) / 2
        start_y = (self.height() - snap.height * cell) / 2
        font = QFont("Segoe UI", 10)
        font.setBold(True)
        painter.setFont(font)
        for y in range(snap.height):
            for x in range(snap.width):
                rect = QRectF(start_x + x * cell + 2, start_y + y * cell + 2, cell - 4, cell - 4)
                from combatbot.models import Cell
                position = Cell(x, y)
                color = QColor("#23344b") if (x + y) % 2 == 0 else QColor("#1b2d42")
                if position in snap.obstacles:
                    color = QColor("#46556b")
                painter.setPen(QPen(QColor("#344861"), 1))
                painter.setBrush(color)
                painter.drawRoundedRect(rect, 5, 5)
                if position in snap.obstacles:
                    painter.setPen(QColor("#aab7c8"))
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "▦")
        def actor_at(actor, color: str, label: str, selected: bool = False) -> None:
            rect = QRectF(start_x + actor.cell.x * cell + 7, start_y + actor.cell.y * cell + 7, cell - 14, cell - 14)
            painter.setPen(QPen(QColor("#fbbf24") if selected else QColor("#dce8f5"), 3 if selected else 1))
            painter.setBrush(QColor(color))
            painter.drawEllipse(rect)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
        actor_at(snap.player, "#14937d", "P")
        for index, enemy in enumerate(snap.enemies, 1):
            if enemy.alive:
                actor_at(enemy, "#b54f5e", str(index), enemy.name == snap.selected_target)
        painter.end()
