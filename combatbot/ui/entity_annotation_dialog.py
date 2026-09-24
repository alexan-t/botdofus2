"""« Annoter les entités » (LOT 3B-5) : vérité humaine par DofusCellId, sans prédiction affichée.

La frame d'origine et la grille GameData projetée enregistrée avec l'observation sont affichées ;
le survol donne le cell ID, un zoom local aide à voir le marqueur au sol. Un clic sur une cellule
choisit JOUEUR, ENNEMI (E1…E8), VIDE confirmé ou INCONNU. Tout se passe dans PythonBot : rien
n'est cliqué dans DOFUS. La frame précédente n'est jamais recopiée dans la vérité.
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
    QVBoxLayout,
)

from combatbot.corpus.models import ENTITY_FRAME_PHASES, CorpusEntry
from combatbot.corpus.repository import CorpusRepository
from combatbot.ui.images import bgr_to_pixmap

ENEMY_LABELS = tuple(f"E{index}" for index in range(1, 9))
PHASE_LABELS = {"placement": "Placement", "debut_combat": "Début de combat", "mon_tour": "Mon tour",
                "tour_ennemi": "Tour ennemi", "animation_sort": "Animation de sort",
                "changement_tour": "Changement de tour", "exploration": "Exploration", "autre": "Autre"}


def projected_cells(document: dict) -> list[dict]:
    """Cellules projetées annotables : traversables en combat selon GameData (ou inconnues)."""
    snapshot = document.get("grid_snapshot") or {}
    if snapshot.get("grid_source") != "GAMEDATA_PROJECTED":
        return []
    cells = []
    for cell in snapshot.get("cells", ()):
        if cell.get("cell_id") is None or not cell.get("polygon"):
            continue
        if cell.get("walkable_static") is False or cell.get("non_walkable_during_fight_static") is True:
            continue  # une entité ne peut pas se tenir sur une cellule non traversable
        cells.append(cell)
    return cells


def entity_entries(repository: CorpusRepository) -> list[CorpusEntry]:
    result = []
    for entry in repository.list_entries():
        try:
            if projected_cells(repository.read_observation(entry)):
                result.append(entry)
        except (OSError, ValueError):
            continue
    return result


class _CellCanvas(QLabel):
    hovered = Signal(object)
    clicked = Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(900, 480)
        self.setStyleSheet("background:#0b1220; border:1px solid #34445b;")
        self.image_size: tuple[int, int] | None = None

    def _image_point(self, position) -> tuple[float, float] | None:
        pixmap = self.pixmap()
        if pixmap is None or self.image_size is None or pixmap.isNull():
            return None
        left = (self.width() - pixmap.width()) / 2
        top = (self.height() - pixmap.height()) / 2
        x, y = position.x() - left, position.y() - top
        if not (0 <= x < pixmap.width() and 0 <= y < pixmap.height()):
            return None
        return x * self.image_size[0] / pixmap.width(), y * self.image_size[1] / pixmap.height()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.hovered.emit(self._image_point(event.position()))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        point = self._image_point(event.position())
        if point is not None:
            self.clicked.emit(point, event.globalPosition().toPoint())


class EntityAnnotationDialog(QDialog):
    def __init__(self, repository: CorpusRepository, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.entries = entity_entries(repository)
        self.index = 0
        self.labels: dict[int, str] = {}   # cell_id -> PLAYER | E1… | ENEMY | EMPTY
        self.cells: list[dict] = []
        self.image: np.ndarray | None = None
        self.offset = (0, 0)               # recadrage sur l'arène (coordonnées de la frame)
        self.setWindowTitle("Annoter les entités — vérité humaine par cell ID")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1500, 950)
        root = QVBoxLayout(self)
        self.header = QLabel()
        self.header.setWordWrap(True)
        root.addWidget(self.header)
        body = QHBoxLayout()
        self.canvas = _CellCanvas()
        body.addWidget(self.canvas, 4)
        side = QVBoxLayout()
        self.zoom = QLabel("Zoom local")
        self.zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom.setFixedSize(360, 240)
        self.zoom.setStyleSheet("background:#0b1220; border:1px solid #34445b;")
        side.addWidget(self.zoom)
        self.hover = QLabel("Survolez une cellule projetée.")
        side.addWidget(self.hover)
        self.player_hidden = QCheckBox("Joueur non visible (UNKNOWN)")
        self.phase = QComboBox()
        self.phase.addItem("Phase non précisée", None)
        for key in sorted(ENTITY_FRAME_PHASES):
            self.phase.addItem(PHASE_LABELS.get(key, key), key)
        self.occlusion = QCheckBox("Occlusion / sprite masquant un marqueur")
        self.tactical = QCheckBox("Mode tactique activé manuellement")
        self.tactical.setTristate(True)
        self.tactical.setToolTip("Coché : oui ; décoché : non ; tiret : inconnu.")
        self.occluded_tracks = QLineEdit()
        self.occluded_tracks.setPlaceholderText("Ennemis présents mais non localisables : E2, E3")
        for widget in (self.player_hidden, QLabel("Phase de la frame :"), self.phase, self.occlusion,
                       self.tactical, QLabel("Ennemis occultés :"), self.occluded_tracks):
            side.addWidget(widget)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        side.addWidget(self.summary)
        side.addStretch()
        body.addLayout(side, 1)
        root.addLayout(body, 1)
        self.status = QLabel("Clic sur une cellule : JOUEUR, ENNEMI, VIDE confirmé ou INCONNU. "
                             "Aucune prédiction n'est affichée.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        previous, following = QPushButton("◀ Précédente"), QPushButton("Suivante ▶")
        clear = QPushButton("Tout effacer")
        save = QPushButton("Confirmer cette frame")
        save.setObjectName("primary")
        close = QPushButton("Fermer")
        for button in (previous, following, clear):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(save)
        buttons.addWidget(close)
        root.addLayout(buttons)
        previous.clicked.connect(lambda: self._move(-1))
        following.clicked.connect(lambda: self._move(1))
        clear.clicked.connect(self._clear)
        save.clicked.connect(self._save)
        close.clicked.connect(self.accept)
        self.canvas.hovered.connect(self._hover)
        self.canvas.clicked.connect(self._menu)
        self.player_hidden.toggled.connect(self._player_visibility_changed)
        self._show()

    # ------------------------------------------------------------------ données
    def _frame_point(self, point) -> tuple[float, float] | None:
        """Point du canevas (arène recadrée) → coordonnées de la frame d'origine."""
        return None if point is None else (point[0] + self.offset[0], point[1] + self.offset[1])

    def _cell_at(self, point) -> dict | None:
        if point is None:
            return None
        best = None
        for cell in self.cells:
            polygon = np.asarray(cell["polygon"], np.float32)
            distance = cv2.pointPolygonTest(polygon, (float(point[0]), float(point[1])), True)
            if distance >= 0 and (best is None or distance > best[0]):
                best = (distance, cell)
        return best[1] if best else None

    def _show(self) -> None:
        if not self.entries:
            self.header.setText("Aucune observation avec grille GameData projetée. Enregistrez des observations "
                                "en « Vision réelle » avec une map déclarée et une projection calibrée.")
            return
        entry = self.entries[self.index]
        document = self.repository.read_observation(entry)
        self.cells = projected_cells(document)
        self.image = cv2.imread(str(self.repository.resolve(entry.paths["frame"])), cv2.IMREAD_COLOR)
        annotation = self.repository.read_annotation(entry)
        self.labels = {}
        if annotation is not None and annotation.entities_confirmed:
            if annotation.player_cell_id_truth is not None:
                self.labels[annotation.player_cell_id_truth] = "PLAYER"
            for item in annotation.enemy_cells_truth:
                self.labels[int(item["cell_id"])] = str(item.get("track_id") or "ENEMY")
            for cell_id in annotation.empty_confirmed_cells:
                self.labels[cell_id] = "EMPTY"
        confirmed = annotation is not None and annotation.entities_confirmed
        self.player_hidden.setChecked(bool(annotation and annotation.player_visibility == "NOT_VISIBLE"))
        self.phase.setCurrentIndex(max(0, self.phase.findData(annotation.frame_phase if annotation else None)))
        self.occlusion.setChecked(bool(annotation and annotation.occlusion))
        tactical = annotation.tactical_mode if annotation else None
        self.tactical.setCheckState(Qt.CheckState.PartiallyChecked if tactical is None else
                                   Qt.CheckState.Checked if tactical else Qt.CheckState.Unchecked)
        self.occluded_tracks.setText(", ".join(annotation.enemy_occluded_tracks) if annotation else "")
        grid = document.get("grid_snapshot") or {}
        self.header.setText(
            f"Frame {self.index + 1}/{len(self.entries)} · <b>{entry.session_id}</b> · frame {entry.frame_index} · "
            f"map {grid.get('map_id_declared')} · "
            + ("<b>vérité entités confirmée</b>" if confirmed else "non annotée"))
        self._render()

    def _render(self) -> None:
        if self.image is None:
            return
        output = self.image.copy()
        if self.cells:
            points = np.concatenate([np.asarray(cell["polygon"]) for cell in self.cells])
            margin = 60
            x0, y0 = np.maximum(points.min(axis=0).astype(int) - margin, 0)
            x1, y1 = points.max(axis=0).astype(int) + margin
            self.offset = (int(x0), int(y0))
        else:
            x0 = y0 = 0
            y1, x1 = output.shape[:2]
            self.offset = (0, 0)
        cv2.polylines(output, [np.asarray(c["polygon"], np.int32) for c in self.cells], True, (90, 150, 190), 1,
                      cv2.LINE_AA)
        colors = {"PLAYER": (80, 235, 120), "EMPTY": (160, 160, 160)}
        for cell in self.cells:
            label = self.labels.get(int(cell["cell_id"]))
            if label is None:
                continue
            color = colors.get(label, (60, 70, 245))
            cv2.polylines(output, [np.asarray(cell["polygon"], np.int32)], True, color, 3, cv2.LINE_AA)
            text = "P" if label == "PLAYER" else ("vide" if label == "EMPTY" else label)
            center = tuple(int(v) for v in cell["center"])
            cv2.putText(output, f"{text} {cell['cell_id']}", (center[0] - 22, center[1] + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)
        output = np.ascontiguousarray(output[y0:y1, x0:x1])
        self.canvas.image_size = (output.shape[1], output.shape[0])
        self.canvas.setPixmap(bgr_to_pixmap(output).scaled(
            self.canvas.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        players = [cell for cell, label in self.labels.items() if label == "PLAYER"]
        enemies = sorted((label, cell) for cell, label in self.labels.items() if label not in ("PLAYER", "EMPTY"))
        self.summary.setText(
            f"Joueur : {players[0] if players else ('non visible' if self.player_hidden.isChecked() else '—')}\n"
            f"Ennemis : {', '.join(f'{label}@{cell}' for label, cell in enemies) or '—'}\n"
            f"Vides confirmées : {sum(label == 'EMPTY' for label in self.labels.values())}")

    def _hover(self, point) -> None:
        point = self._frame_point(point)
        cell = self._cell_at(point)
        if point is None or self.image is None:
            return
        self.hover.setText(f"Cell ID {cell['cell_id']}" if cell else "Hors grille projetée")
        x, y = int(point[0]), int(point[1])
        crop = self.image[max(0, y - 60):y + 60, max(0, x - 90):x + 90]
        if crop.size:
            self.zoom.setPixmap(bgr_to_pixmap(cv2.resize(crop, None, fx=2, fy=2,
                                                         interpolation=cv2.INTER_NEAREST)))

    def _menu(self, point, global_position: QPoint) -> None:
        cell = self._cell_at(self._frame_point(point))
        if cell is None:
            return
        cell_id = int(cell["cell_id"])
        menu = QMenu(self)
        actions = {menu.addAction("JOUEUR"): "PLAYER"}
        enemy_menu = menu.addMenu("ENNEMI")
        for label in ENEMY_LABELS:
            actions[enemy_menu.addAction(label)] = label
        actions[enemy_menu.addAction("Ennemi sans identifiant")] = "ENEMY"
        actions[menu.addAction("VIDE confirmé")] = "EMPTY"
        actions[menu.addAction("INCONNU (effacer)")] = None
        chosen = menu.exec(global_position)
        if chosen is None or chosen not in actions:
            return
        self._assign(cell_id, actions[chosen])

    def _assign(self, cell_id: int, label: str | None) -> None:
        if label is None:
            self.labels.pop(cell_id, None)
        else:
            if label == "PLAYER":
                # Exactement 0 ou 1 joueur : l'ancienne cellule joueur est libérée.
                self.labels = {cell: value for cell, value in self.labels.items() if value != "PLAYER"}
                self.player_hidden.setChecked(False)
            if label in ENEMY_LABELS:
                self.labels = {cell: value for cell, value in self.labels.items() if value != label}
            self.labels[cell_id] = label
        self._render()

    def _player_visibility_changed(self, hidden: bool) -> None:
        if hidden:
            self.labels = {cell: value for cell, value in self.labels.items() if value != "PLAYER"}
        self._render()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        if hasattr(self, "canvas"):
            self._render()

    def _clear(self) -> None:
        self.labels = {}
        self._render()

    def _move(self, step: int) -> None:
        if 0 <= self.index + step < len(self.entries):
            self.index += step
            self._show()

    def _save(self) -> None:
        if not self.entries:
            return
        players = [cell for cell, label in self.labels.items() if label == "PLAYER"]
        if not players and not self.player_hidden.isChecked():
            QMessageBox.warning(self, "Annotation incomplète",
                                "Désignez la cellule du joueur ou cochez « Joueur non visible ».")
            return
        enemies = [(cell, None if label == "ENEMY" else label) for cell, label in self.labels.items()
                   if label not in ("PLAYER", "EMPTY")]
        occluded = [value.strip().upper() for value in self.occluded_tracks.text().split(",") if value.strip()]
        entry = self.entries[self.index]
        try:
            self.repository.confirm_entities(
                entry.observation_id, player_cell_id=players[0] if players else None,
                player_visible=bool(players), enemies=enemies, occluded_tracks=occluded,
                empty_cells=[cell for cell, label in self.labels.items() if label == "EMPTY"],
                frame_phase=self.phase.currentData(), occlusion=self.occlusion.isChecked() or None,
                tactical_mode=(None if self.tactical.checkState() == Qt.CheckState.PartiallyChecked
                               else self.tactical.checkState() == Qt.CheckState.Checked))
        except ValueError as exc:
            QMessageBox.warning(self, "Annotation invalide", str(exc))
            return
        self.status.setText(f"{entry.observation_id} : vérité entités confirmée.")
        self._move(1)
