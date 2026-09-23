"""Calibration de la projection de grille GameData sur une capture figée.

L'utilisateur n'a pas besoin de connaître les cell IDs : il fait coïncider la
grille théorique des 560 cellules avec l'écran (proposition automatique,
déplacement, largeur, hauteur, légère inclinaison, nudges ±1 px). Les ancres
explicites (cell ID → pixel) restent un mode avancé facultatif.
Aucun clic n'est envoyé au client DOFUS : l'image est une capture figée.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QGraphicsPixmapItem, QGraphicsScene,
    QGraphicsView, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout,
)

from combatbot.gamedata.models import DofusCellId, GameMapCell, GridTopology
from combatbot.gamedata.topology import CELL_COUNT
from combatbot.ui.images import bgr_to_pixmap
from combatbot.vision.grid_fit import candidate_union
from combatbot.vision.combat_models import CombatObservation
from combatbot.vision.combat_observer import OverlayOptions, draw_diagnostic_overlay
from combatbot.vision.gamedata_grid import projected_observation
from combatbot.vision.grid_fit import FitStatus, GridFitResult, fit_from_anchors, fit_grid_from_candidates
from combatbot.vision.grid_profile import CombatGridProfileV2
from combatbot.vision.grid_projection import GridProjector, GridScreenTransform


def generic_topology() -> GridTopology:
    """560 cells without static facts: geometry only, when no map is declared."""
    return GridTopology(-1, tuple(GameMapCell(DofusCellId(i)) for i in range(CELL_COUNT)))


def default_transform(width: int, height: int) -> GridScreenTransform:
    """Initial guess: the 560-cell footprint (14.5 × 20.5 cells) fitted to the crop."""
    cell_width = min(width / 14.5, 2 * height / 20.5)
    cell_height = cell_width / 2
    return GridScreenTransform.from_cell_size((cell_width / 2, cell_height / 2), cell_width, cell_height,
                                              reference_combat_size=(float(width), float(height)))


class _ImageView(QGraphicsView):
    clicked = Signal(float, float)

    def __init__(self) -> None:
        super().__init__()
        self.scene_ = QGraphicsScene(self)
        self.item = QGraphicsPixmapItem()
        self.scene_.addItem(self.item)
        self.setScene(self.scene_)
        self.setMinimumSize(640, 420)

    def set_image(self, pixmap: QPixmap) -> None:
        self.item.setPixmap(pixmap)
        self.scene_.setSceneRect(self.item.boundingRect())

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API ; zoom contrôlé
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        point = self.mapToScene(event.position().toPoint())
        self.clicked.emit(point.x(), point.y())
        super().mousePressEvent(event)


class GridProjectionDialog(QDialog):
    def __init__(self, combat_image: np.ndarray, *, layout_signature: str,
                 topology: GridTopology | None = None, map_id: int | None = None,
                 current: CombatGridProfileV2 | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calibrer la projection de grille")
        self.image = combat_image
        self.layout_signature = layout_signature
        self.topology = topology if topology is not None else generic_topology()
        self.has_topology = topology is not None
        self.map_id = map_id
        self.size_hint = (float(combat_image.shape[1]), float(combat_image.shape[0]))
        self.transform = current.transform if current else default_transform(combat_image.shape[1],
                                                                              combat_image.shape[0])
        self.method = current.calibration_method if current else "manual"
        self.anchors: list[tuple[int, tuple[float, float]]] = []
        self.fit: GridFitResult | None = None
        self.candidates: list = []
        self.result_profile: CombatGridProfileV2 | None = None
        self.alignment = 0.0
        self._updating = False
        self._alternative = 0

        layout = QVBoxLayout(self)
        notice = QLabel(
            "Capture figée, lecture seule. Les 560 cell IDs viennent de GameData ; ajustez uniquement la "
            "position de la grille. Molette : zoom. Aucune action n'est envoyée au jeu."
            + ("" if self.has_topology else "\nAucune map déclarée : proposition automatique limitée, ajustement manuel conseillé.")
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        content = QHBoxLayout()
        self.view = _ImageView()
        self.view.clicked.connect(self._image_clicked)
        content.addWidget(self.view, 3)
        side = QVBoxLayout()
        self.auto_button = QPushButton("Proposition automatique")
        self.auto_button.clicked.connect(self.propose_auto)
        side.addWidget(self.auto_button)
        self.next_button = QPushButton("Hypothèse suivante")
        self.next_button.setToolTip("Placements proches classés par le fit ; choisissez celui qui coïncide")
        self.next_button.clicked.connect(self.next_hypothesis)
        self.next_button.setEnabled(False)
        side.addWidget(self.next_button)
        form = QFormLayout()
        self.origin_x, self.origin_y = QDoubleSpinBox(), QDoubleSpinBox()
        self.cell_w, self.cell_h, self.shear = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
        for spin, low, high in ((self.origin_x, -5000, 5000), (self.origin_y, -5000, 5000),
                                (self.cell_w, 4, 1000), (self.cell_h, 2, 500), (self.shear, -50, 50)):
            spin.setRange(low, high)
            spin.setDecimals(2)
            spin.setSingleStep(0.5)
            spin.valueChanged.connect(self._manual_changed)
        form.addRow("Centre cellule 0 — X", self.origin_x)
        form.addRow("Centre cellule 0 — Y", self.origin_y)
        form.addRow("Largeur cellule", self.cell_w)
        form.addRow("Hauteur cellule", self.cell_h)
        form.addRow("Inclinaison", self.shear)
        side.addLayout(form)
        nudges = QGridLayout()
        for label, row, col, delta in (("↑", 0, 1, (0, -1)), ("←", 1, 0, (-1, 0)), ("→", 1, 2, (1, 0)),
                                       ("↓", 2, 1, (0, 1))):
            button = QPushButton(label)
            button.clicked.connect(lambda _c=False, d=delta: self.nudge(*d))
            nudges.addWidget(button, row, col)
        for label, row, col, dw, dh in (("L−", 3, 0, -1, 0), ("L+", 3, 2, 1, 0), ("H−", 4, 0, 0, -1), ("H+", 4, 2, 0, 1)):
            button = QPushButton(label)
            button.clicked.connect(lambda _c=False, a=dw, b=dh: self.resize_cells(a, b))
            nudges.addWidget(button, row, col)
        side.addLayout(nudges)
        self.show_ids = QCheckBox("Afficher cell IDs (1 sur 7)")
        self.show_candidates = QCheckBox("Afficher candidats OpenCV")
        self.show_walk = QCheckBox("Afficher walkability GameData")
        self.show_hints = QCheckBox("Afficher indices rouge/bleu GameData")
        for box in (self.show_ids, self.show_candidates, self.show_walk, self.show_hints):
            box.toggled.connect(self.render)
            side.addWidget(box)
        anchor_row = QHBoxLayout()
        self.anchor_mode = QCheckBox("Ancre : cell ID")
        self.anchor_cell = QSpinBox()
        self.anchor_cell.setRange(0, CELL_COUNT - 1)
        anchor_row.addWidget(self.anchor_mode)
        anchor_row.addWidget(self.anchor_cell)
        side.addLayout(anchor_row)
        self.anchor_fit = QPushButton("Résoudre depuis les ancres (≥ 3)")
        self.anchor_fit.clicked.connect(self.fit_anchors)
        self.anchor_clear = QPushButton("Effacer les ancres")
        self.anchor_clear.clicked.connect(self._clear_anchors)
        side.addWidget(self.anchor_fit)
        side.addWidget(self.anchor_clear)
        self.metrics = QLabel("")
        self.metrics.setWordWrap(True)
        side.addWidget(self.metrics, 1)
        content.addLayout(side, 1)
        layout.addLayout(content, 1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button = QPushButton("Je confirme l'alignement")
        self.confirm_button.setObjectName("primary")
        self.confirm_button.clicked.connect(self.confirm)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)
        self._sync_spins()
        self.render()

    # --- State changes ------------------------------------------------------------
    def _sync_spins(self) -> None:
        self._updating = True
        t = self.transform
        self.origin_x.setValue(t.origin.x)
        self.origin_y.setValue(t.origin.y)
        self.cell_w.setValue(t.cell_width)
        self.cell_h.setValue(t.cell_height)
        self.shear.setValue((t.basis_x.y + t.basis_y.y) / 2)
        self._updating = False

    def _manual_changed(self) -> None:
        if self._updating:
            return
        self.set_transform(GridScreenTransform.from_cell_size(
            (self.origin_x.value(), self.origin_y.value()), self.cell_w.value(), self.cell_h.value(),
            shear=self.shear.value(), reference_combat_size=self.size_hint), "manual", sync=False)

    def set_transform(self, transform: GridScreenTransform, method: str, *, sync: bool = True) -> None:
        self.transform = replace(transform, reference_combat_size=self.size_hint)
        self.method = method
        if sync:
            self._sync_spins()
        self.render()

    def nudge(self, dx: float, dy: float) -> None:
        self.set_transform(self.transform.translated(dx, dy), "manual")

    def resize_cells(self, dw: float, dh: float) -> None:
        t = self.transform
        self.set_transform(GridScreenTransform.from_cell_size(
            t.origin, max(4.0, t.cell_width + dw), max(2.0, t.cell_height + dh),
            shear=(t.basis_x.y + t.basis_y.y) / 2), "manual")

    def propose_auto(self) -> GridFitResult:
        # Union of legacy and sensitive thresholds; duplicates only add weight to the same lattice points.
        self.candidates = candidate_union(self.image)
        self.fit = fit_grid_from_candidates(self.candidates, (self.image.shape[1], self.image.shape[0]),
                                            topology=self.topology if self.has_topology else None)
        self._alternative = 0
        self.next_button.setEnabled(len(self.fit.alternatives) > 1)
        if self.fit.transform is not None:
            # Weak/ambiguous proposals are shown for manual refinement, never auto-confirmed.
            self.set_transform(self.fit.transform, "auto")
        else:
            self.render()
        return self.fit

    def next_hypothesis(self) -> None:
        if self.fit is None or not self.fit.alternatives:
            return
        self._alternative = (self._alternative + 1) % len(self.fit.alternatives)
        self.set_transform(self.fit.alternatives[self._alternative], "auto+human")

    def _image_clicked(self, x: float, y: float) -> None:
        if not self.anchor_mode.isChecked():
            return
        cell = int(self.anchor_cell.value())
        self.anchors = [item for item in self.anchors if item[0] != cell] + [(cell, (x, y))]
        self.render()

    def _clear_anchors(self) -> None:
        self.anchors.clear()
        self.render()

    def fit_anchors(self) -> GridFitResult:
        self.fit = fit_from_anchors(self.anchors, reference_combat_size=self.size_hint)
        if self.fit.transform is not None and self.fit.status is FitStatus.ACCEPTED:
            self.set_transform(self.fit.transform, "anchors")
        else:
            self.render()
        return self.fit

    # --- Rendering ------------------------------------------------------------------
    def render(self) -> None:
        projected = GridProjector(self.transform).project(self.topology)
        grid = projected_observation(projected, self.image)
        self.alignment = grid.projection_confidence or 0.0
        observation = CombatObservation(False, 0.0, None, 0.0, None, 0.0, (), grid, None, None, 0.0, 0.0, 0.0)
        options = OverlayOptions(grid=True, cell_ids=self.show_ids.isChecked(),
                                 walkability=self.show_walk.isChecked(), red_blue=self.show_hints.isChecked(),
                                 candidates=self.show_candidates.isChecked(), anchors=bool(self.anchors))
        image = draw_diagnostic_overlay(self.image, observation, options, candidates=self.candidates,
                                        anchors=self.anchors)
        self.view.set_image(bgr_to_pixmap(image))
        lines = [f"Méthode : {self.method}", f"Orientation : {self.transform.orientation.value}",
                 f"Cellule : {self.transform.cell_width:.1f} × {self.transform.cell_height:.1f} px",
                 f"Alignement visuel : {self.alignment:.0%}", f"Ancres : {len(self.anchors)}"]
        if self.fit is not None:
            lines.append(f"Dernier ajustement : {self.fit.status.value} — {self.fit.message}")
            if self.fit.method == "auto":
                lines.append(f"Score {self.fit.score:.2f}, marge {self.fit.margin:.2f}, "
                             f"inliers {self.fit.inliers}/{self.fit.candidates}")
                if self.fit.alternatives:
                    lines.append(f"Hypothèse affichée : {self._alternative + 1}/{len(self.fit.alternatives)}")
        self.metrics.setText("\n".join(lines))

    def confirm(self) -> None:
        metrics = {"alignment_confidence": self.alignment, "orientation": self.transform.orientation.value,
                   "fit": self.fit.metrics() if self.fit else None}
        self.result_profile = CombatGridProfileV2(
            self.transform, self.layout_signature, self.method, tuple(self.anchors), metrics,
            self.map_id, confirmed_by_user=True,
        )
        self.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - flèches = nudge ±1 px
        moves = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if event.key() in moves:
            self.nudge(*moves[event.key()])
            return
        super().keyPressEvent(event)
