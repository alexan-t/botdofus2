"""Aperçus UI : réduits une fois à la taille utile, jamais utilisés par la vision, source intacte."""
from __future__ import annotations

import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from combatbot.ui.images import preview_pixmap

APP = QApplication.instance() or QApplication([])


def frame(width: int = 2100, height: int = 1000, value: int = 0) -> np.ndarray:
    image = np.random.default_rng(value).integers(0, 255, (height, width, 3), dtype=np.uint8)
    image[:, :, 0] = value
    return image


def test_preview_fits_the_target_and_never_upscales() -> None:
    source = frame()
    before = source.copy()
    pixmap = preview_pixmap(source, 900, 500)
    assert (pixmap.width(), pixmap.height()) == (900, 429)
    assert np.array_equal(source, before)                          # la vision garde sa frame intacte
    small = preview_pixmap(frame(200, 100), 900, 500)
    assert (small.width(), small.height()) == (200, 100)
    thumb = preview_pixmap(frame(2560, 1377), 44, 30)
    assert thumb.width() <= 44 and thumb.height() <= 30


def test_observation_preview_keeps_source_coordinates_for_clicks() -> None:
    from combatbot.ui.pages import ObservationPreview
    preview = ObservationPreview()
    preview.resize(900, 500)
    preview.show()                                                 # Qt ne livre resizeEvent qu'une fois affiché
    APP.processEvents()
    preview.set_image(frame())
    assert preview.pixmap().width() == 900
    clicked = []
    preview.image_clicked.connect(clicked.append)
    top = (preview.height() - preview.pixmap().height()) / 2
    QTest.mouseClick(preview, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(450, round(top + 214)))
    assert clicked and abs(clicked[0][0] - 1050) <= 3 and abs(clicked[0][1] - 499) <= 3
    first = preview.pixmap().cacheKey()
    preview.resize(900, 500)
    assert preview.pixmap().cacheKey() == first                    # même frame, même taille : aucun travail
    preview.resize(600, 400)
    APP.processEvents()
    assert preview.pixmap().width() == 600                         # recalculé depuis la source, pas agrandi


def test_frame_preview_renders_each_new_frame_at_widget_size() -> None:
    from combatbot.ui.dofbot2.advanced import FramePreview
    widget = FramePreview("x")
    widget.resize(520, 300)
    widget.show()
    APP.processEvents()
    first, second = frame(2560, 1377, 10), frame(2560, 1377, 200)
    widget.set_frame(first)
    assert widget.image.width() <= 520 and widget.image.height() <= 300
    blue_first = widget.image.pixelColor(10, 10).blue()
    widget.set_frame(second)                                       # même taille : nouvelle image quand même
    assert widget.image.pixelColor(10, 10).blue() != blue_first
    kept = widget.image
    widget.set_frame(second)
    assert widget.image is kept
    widget.set_frame(None)
    assert widget.image is None


def test_hidden_previews_do_no_work_until_shown() -> None:
    from combatbot.ui.dofbot2.advanced import FramePreview
    from combatbot.ui.pages import ObservationPreview
    preview = ObservationPreview()
    preview.resize(900, 500)
    preview.set_image(frame())                                     # jamais affiché : rien de calculé
    assert preview._rendered is None
    preview.show()
    APP.processEvents()
    assert preview._rendered is not None and preview.pixmap().width() == 900
    preview.hide()
    latest = frame(value=7)
    preview.set_image(latest)
    assert preview._rendered is None                               # caché : la frame attend l'affichage
    preview.show()
    APP.processEvents()
    assert preview._rendered[0] == id(latest)
    widget = FramePreview("x")
    widget.resize(520, 300)
    widget.set_frame(frame(2560, 1377))
    assert widget.image is None
    widget.show()
    APP.processEvents()
    assert widget.image is not None and widget.image.width() <= 520
