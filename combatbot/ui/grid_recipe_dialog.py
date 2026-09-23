"""Dialogue de recette réelle de la grille (LOT 3B-2R), sans action dans le jeu.

Affiche la capture figée avec l'overlay (walkability, IDs, candidats), les mesures
automatiques et recueille le jugement de l'utilisateur. Les mesures sont déjà
enregistrées avant l'ouverture : le jugement ne les recalcule jamais.
"""

from __future__ import annotations

import cv2
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from combatbot.gamedata.models import GridTopology
from combatbot.ui.images import bgr_to_pixmap
from combatbot.vision.grid_projection import GridProjector
from combatbot.vision.grid_recipe import ALIGNMENTS, EDGE_KEYS, SHIFT_DIRECTIONS, RealGridValidationSession

EDGE_LABELS = {"edge_top_ok": "Bord haut", "edge_bottom_ok": "Bord bas", "edge_left_ok": "Bord gauche",
               "edge_right_ok": "Bord droit", "center_ok": "Centre"}
MATCHES = ("", "exact", "partiel", "non", "no_gamedata_hints", "not_confirmed")


class _HoverView(QGraphicsView):
    hovered = Signal(float, float)

    def __init__(self) -> None:
        super().__init__()
        self.setMouseTracking(True)
        self.scene_ = QGraphicsScene(self)
        self.item = QGraphicsPixmapItem()
        self.scene_.addItem(self.item)
        self.setScene(self.scene_)
        self.setMinimumSize(720, 420)

    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        point = self.mapToScene(event.position().toPoint())
        self.hovered.emit(point.x(), point.y())
        super().mouseMoveEvent(event)


class GridRecipeDialog(QDialog):
    def __init__(self, session: RealGridValidationSession, capture_id: str, topology: GridTopology,
                 parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.capture = next(c for c in session.captures if c["capture_id"] == capture_id)
        self.grid = GridProjector(session.transform(self.capture["transform_id"])).project(topology)
        self.setWindowTitle(f"Recette de grille — map {self.capture['map_id']} ({self.capture['capture_id']})")
        layout = QVBoxLayout(self)
        source = self.capture["map_id_source"]
        layout.addWidget(QLabel(
            f"Map {self.capture['map_id']} — source : {source}"
            + ("" if source == "user_verified_mapid" else " — ATTENTION : non vérifiée par /mapid")
            + ". Capture figée, aucune action envoyée au jeu."))
        content = QHBoxLayout()
        self.view = _HoverView()
        self.overlay = cv2.imread(str(session.directory / self.capture["files"]["overlay"]))
        if self.overlay is not None:
            self.view.item.setPixmap(bgr_to_pixmap(self.overlay))
        self.view.hovered.connect(self._hovered)
        content.addWidget(self.view, 3)
        side = QVBoxLayout()
        metrics = self.capture["metrics"]
        consistency = metrics.get("topology_consistency")
        self.metrics_label = QLabel(
            f"Mode : {self.capture.get('context', {}).get('mode')}\n"
            f"Candidats OpenCV : {metrics['candidate_count']}\n"
            f"Ajustement : {metrics['fit_status']} (marge {metrics['score_margin']})\n"
            f"Décalage entier vs transform : {metrics['integer_offset']}\n"
            f"Résidu médian : {metrics['residual_median_px']}\n"
            f"topology_consistency : {consistency if consistency is None else round(consistency, 3)}\n"
            f"Marge vs décalage ±1 : {metrics['margin_vs_shift']}")
        self.metrics_label.setWordWrap(True)
        side.addWidget(self.metrics_label)
        self.hover = QLabel("Survolez l'image pour lire le cell ID.")
        self.hover.setWordWrap(True)
        side.addWidget(self.hover)
        form = QFormLayout()
        self.alignment = QComboBox()
        self.alignment.addItems(ALIGNMENTS)
        self.shift = QComboBox()
        self.shift.addItems(SHIFT_DIRECTIONS)
        form.addRow("Alignement", self.alignment)
        form.addRow("Direction du décalage", self.shift)
        side.addLayout(form)
        self.edges = {key: QCheckBox(EDGE_LABELS[key]) for key in EDGE_KEYS}
        for box in self.edges.values():
            side.addWidget(box)
        self.anomaly = QCheckBox("Anomalie visuelle majeure")
        side.addWidget(self.anomaly)
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Notes")
        side.addWidget(self.notes)
        self.has_red_blue = "red_blue_auto" in self.capture
        self.red_match, self.blue_match = QComboBox(), QComboBox()
        self.real_red, self.real_blue = QLineEdit(), QLineEdit()
        if self.has_red_blue:
            auto = self.capture["red_blue_auto"]
            side.addWidget(QLabel(f"Rouge/bleu (indices GameData, jamais « placement » validé) — vus rouges : "
                                  f"{auto['red']['observed']}, vus bleus : {auto['blue']['observed']}"))
            for combo in (self.red_match, self.blue_match):
                combo.addItems(MATCHES)
            self.real_red.setPlaceholderText("Cell IDs rouges réels, séparés par des virgules")
            self.real_blue.setPlaceholderText("Cell IDs bleus réels, séparés par des virgules")
            rb = QFormLayout()
            rb.addRow("red_match", self.red_match)
            rb.addRow("blue_match", self.blue_match)
            rb.addRow("Rouges réels", self.real_red)
            rb.addRow("Bleus réels", self.real_blue)
            side.addLayout(rb)
        side.addStretch()
        content.addLayout(side, 1)
        layout.addLayout(content, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(self.reject)
        self.save_button = QPushButton("Enregistrer le jugement")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        buttons.addWidget(cancel)
        buttons.addWidget(self.save_button)
        layout.addLayout(buttons)

    def _hovered(self, x: float, y: float) -> None:
        cell_id = self.grid.pixel_to_cell((x, y))
        if cell_id is None:
            self.hover.setText(f"({x:.0f}, {y:.0f}) : hors des 560 cellules")
            return
        cell = self.grid.cell(cell_id)
        self.hover.setText(f"Cellule {cell_id} ({cell.grid_coordinate.x}, {cell.grid_coordinate.y}) — "
                           f"traversable : {cell.static_traversable_in_fight} — rouge/bleu : "
                           f"{cell.red_hint}/{cell.blue_hint}")

    @staticmethod
    def _ids(text: str) -> list[int] | None:
        values = [part.strip() for part in text.split(",") if part.strip()]
        return [int(v) for v in values] if values else None

    def save(self) -> None:
        self.session.set_verdict(
            self.capture["map_id"], alignment=self.alignment.currentText(),
            edges={key: box.isChecked() for key, box in self.edges.items()},
            shift_direction=self.shift.currentText(), notes=self.notes.text(),
            major_anomaly=self.anomaly.isChecked(), transform_id=self.capture["transform_id"])
        if self.has_red_blue and (self.red_match.currentText() or self.blue_match.currentText()):
            self.session.add_red_blue_annotation(
                map_id=self.capture["map_id"], capture_id=self.capture["capture_id"],
                red_match=self.red_match.currentText(), blue_match=self.blue_match.currentText(),
                real_red=self._ids(self.real_red.text()), real_blue=self._ids(self.real_blue.text()))
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Échap ferme sans enregistrer
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
