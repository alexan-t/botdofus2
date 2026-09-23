"""Conversion d'images OpenCV en éléments Qt, uniquement dans le thread UI."""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtGui import QImage, QPixmap


def bgr_to_pixmap(image: np.ndarray) -> QPixmap:
    """Crée un pixmap DPR=1 : une unité de scène correspond à un pixel capturé."""
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    qt_image = QImage(rgb.data, width, height, 3 * width, QImage.Format.Format_RGB888).copy()
    pixmap = QPixmap.fromImage(qt_image)
    pixmap.setDevicePixelRatio(1.0)
    return pixmap
