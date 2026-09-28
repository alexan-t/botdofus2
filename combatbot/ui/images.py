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


def preview_pixmap(image: np.ndarray, width: int, height: int) -> QPixmap:
    """Aperçu d'UI à la taille utile, pour l'affichage seulement (jamais pour la vision).

    La réduction se fait une seule fois, sur le tableau source (``INTER_AREA`` : qualité d'un lissage,
    sans créer d'abord un pixmap pleine résolution) ; seule l'image réduite est convertie pour Qt.
    Jamais agrandie : une source plus petite que la cible est affichée à sa taille.
    """
    source_height, source_width = image.shape[:2]
    scale = min(width / source_width, height / source_height, 1.0) if width > 0 and height > 0 else 1.0
    if scale < 1.0:
        size = (max(1, round(source_width * scale)), max(1, round(source_height * scale)))
        # Décimation entière d'abord (vue sans copie), en gardant au moins 2×2 pixels sources par pixel
        # affiché : même rendu à ~1 niveau de gris près, jusqu'à 200× plus rapide pour une vignette.
        step = int(1 / (scale * 2))
        if step >= 2:
            image = image[::step, ::step]
        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    return bgr_to_pixmap(np.ascontiguousarray(image))
