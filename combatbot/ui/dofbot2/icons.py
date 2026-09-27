"""Icônes du dock : tracés SVG 24×24 du prototype (ICONS), rendus en trait de 1,8."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


ICONS = {
    "home": "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
    "donjon": "M4 21V9l3 1.5V6l2.5 1.5V4h5v3.5L17 6v4.5L20 9v12zM10 21v-4a2 2 0 0 1 4 0v4",
    "zone": "M9 4l6 2 6-2v16l-6 2-6-2-6 2V6zM9 4v16M15 6v16",
    "sorts": "M12 3l2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4z",
    "notifs": "M6 16v-5a6 6 0 0 1 12 0v5l2 2H4zM10 21a2 2 0 0 0 4 0",
    "params": "M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4",
    "chevron": "M6 9l6 6 6-6",
}


def svg_source(path: str, color: str, stroke: float = 1.8) -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{path}"/></svg>'
    ).encode()


@lru_cache(maxsize=64)
def icon_pixmap(name: str, color: str, size: int = 20, stroke: float = 1.8, ratio: float = 2.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg_source(ICONS[name], QColor(color).name(), stroke)))
    pixmap = QPixmap(round(size * ratio), round(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, pixmap.width(), pixmap.height()))
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap
