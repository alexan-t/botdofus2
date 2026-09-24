"""Écran d'import, annotation visuelle et promotion volontaire du corpus."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSpinBox, QTextEdit, QVBoxLayout, QWidget,
)

from combatbot.corpus.benchmark import run_benchmark, write_reports
from combatbot.corpus.models import Annotation, CorpusEntry, PixelAnnotation
from combatbot.corpus.repository import CorpusRepository
from combatbot.runtime import app_data_root
from combatbot.ui.images import bgr_to_pixmap


class AnnotationCanvas(QLabel):
    image_clicked = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(600, 430)
        self.setStyleSheet("background:#0b1220; border:1px solid #34445b;")
        self._image: np.ndarray | None = None
        self._image_size: tuple[int, int] | None = None

    def set_image(self, image: np.ndarray) -> None:
        self._image = image.copy()
        self._image_size = (image.shape[1], image.shape[0])
        self._render()

    def _render(self) -> None:
        if self._image is None:
            self.clear()
            return
        pixmap = bgr_to_pixmap(self._image)
        self.setPixmap(pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self._render()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        pixmap = self.pixmap()
        if pixmap is None or self._image_size is None:
            return
        left = (self.width() - pixmap.width()) / 2
        top = (self.height() - pixmap.height()) / 2
        px, py = event.position().x() - left, event.position().y() - top
        if 0 <= px < pixmap.width() and 0 <= py < pixmap.height():
            width, height = self._image_size
            self.image_clicked.emit((round(px * width / pixmap.width()),
                                     round(py * height / pixmap.height())))


class AnnotationDialog(QDialog):
    def __init__(self, repository: CorpusRepository, entry: CorpusEntry, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.entry = entry
        self.document = repository.read_observation(entry)
        self.original = self._read_image("frame")
        self.overlay = self._read_image("overlay")
        self.player: PixelAnnotation | None = None
        self.enemies: list[PixelAnnotation] = []
        self.references: list[PixelAnnotation] = []
        self.anchors: list[PixelAnnotation] = []
        self.setWindowTitle(f"Corpus / Annotation — {entry.observation_id}")
        self.resize(1380, 850)

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.show_overlay = QCheckBox("Afficher l'overlay du bot")
        self.show_overlay.setChecked(False)
        self.mode = QComboBox()
        for label, value in (
            ("Navigation", "none"), ("Cellule réelle du joueur", "player"),
            ("Cellule réelle d'un ennemi", "enemy"),
            ("Cellule de référence visible", "reference"), ("Ancre de grille", "anchor"),
        ):
            self.mode.addItem(label, value)
        top.addWidget(self.show_overlay)
        top.addWidget(QLabel("Clic dans l'image :"))
        top.addWidget(self.mode)
        top.addStretch()
        root.addLayout(top)

        body = QHBoxLayout()
        self.canvas = AnnotationCanvas()
        body.addWidget(self.canvas, 4)
        panel = QWidget()
        form = QFormLayout(panel)
        self.combat = self._truth_combo()
        self.turn = self._truth_combo()
        self.ap = self._counter()
        self.mp = self._counter()
        self.hud_status = QLabel("Les valeurs restent non annotées tant qu'elles ne sont pas validées.")
        self.hud_status.setWordWrap(True)
        self.digit_issue = QCheckBox("Confusion 1 ↔ 7 pertinente")
        self.reference_complete = QCheckBox("Liste des cellules de référence exhaustive")
        self.points = QLabel("Aucun point humain")
        self.points.setWordWrap(True)
        self.last_click = QLabel("Cliquez dans l'image après avoir choisi un type.")
        self.last_click.setWordWrap(True)
        self.comments = QTextEdit()
        self.comments.setPlaceholderText("Commentaires factuels sur cette frame")
        self.comments.setMaximumHeight(110)
        form.addRow("Combat réel", self.combat)
        form.addRow("Mon tour", self.turn)
        form.addRow("PA réels", self.ap)
        form.addRow("PM réels", self.mp)
        hud_editors = QHBoxLayout()
        hud_editors.addWidget(self._hud_editor("PA", "ap_crop", self.ap, self.mp))
        hud_editors.addWidget(self._hud_editor("PM", "mp_crop", self.mp, self.ap))
        hud_widget = QWidget()
        hud_widget.setLayout(hud_editors)
        form.addRow("Inspection HUD", hud_widget)
        form.addRow("Validation HUD", self.hud_status)
        form.addRow("Cas chiffres", self.digit_issue)
        form.addRow("Références", self.reference_complete)
        form.addRow("Dernier clic", self.last_click)
        form.addRow("Points", self.points)
        form.addRow("Commentaires", self.comments)
        clear = QPushButton("Effacer les points humains")
        clear.clicked.connect(self._clear_points)
        form.addRow(clear)
        body.addWidget(panel, 2)
        root.addLayout(body, 1)

        actions = QHBoxLayout()
        cancel = QPushButton("Fermer sans enregistrer")
        save = QPushButton("Enregistrer l'annotation")
        save.setObjectName("primary")
        cancel.clicked.connect(self.reject)
        save.clicked.connect(self._save)
        actions.addStretch()
        actions.addWidget(cancel)
        actions.addWidget(save)
        root.addLayout(actions)

        self.show_overlay.toggled.connect(self._refresh_image)
        self.canvas.image_clicked.connect(self._image_clicked)
        self._load_existing()
        self._refresh_image()

    @staticmethod
    def _truth_combo() -> QComboBox:
        combo = QComboBox()
        combo.addItem("Inconnu / non annoté", None)
        combo.addItem("Oui", True)
        combo.addItem("Non", False)
        return combo

    @staticmethod
    def _counter() -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(-1, 99)
        spin.setSpecialValueText("Non annoté")
        spin.setValue(-1)
        return spin

    def _hud_editor(self, kind: str, path_key: str, spin: QSpinBox, next_spin: QSpinBox) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        title = QLabel(f"{kind} — original + zoom nearest-neighbor")
        layout.addWidget(title)
        images = QHBoxLayout()
        original, zoom = QLabel(), QLabel()
        original.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom.setMinimumSize(150, 120)
        relative = self.entry.paths.get(path_key)
        image = cv2.imread(str(self.repository.resolve(relative)), cv2.IMREAD_COLOR) if relative else None
        if image is None:
            original.setText("Crop absent")
            zoom.setText("—")
        else:
            pixmap = bgr_to_pixmap(image)
            original.setPixmap(pixmap)
            zoom.setPixmap(pixmap.scaled(180, 150, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.FastTransformation))
        images.addWidget(original)
        images.addWidget(zoom)
        layout.addLayout(images)
        buttons = QHBoxLayout()
        validate = QPushButton("Valider")
        unknown = QPushButton("Inconnu")
        following = QPushButton("Suivant")
        validate.clicked.connect(
            lambda: self.hud_status.setText(
                f"{kind} validé manuellement : {spin.value()}" if spin.value() >= 0
                else f"{kind} reste inconnu : saisissez une valeur avant validation."
            )
        )
        unknown.clicked.connect(lambda: (spin.setValue(-1), self.hud_status.setText(f"{kind} marqué inconnu")))
        following.clicked.connect(next_spin.setFocus)
        for button in (validate, unknown, following):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        return widget

    def _read_image(self, key: str) -> np.ndarray:
        path = self.repository.resolve(self.entry.paths[key])
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Image illisible : {path}")
        return image

    def _prediction(self) -> dict[str, object]:
        value = self.document.get("prediction")
        return value if isinstance(value, dict) else self.document

    def _suggest_logical(self, point: tuple[int, int]) -> tuple[int, int] | None:
        grid = self._prediction().get("grid")
        cells = grid.get("cells", ()) if isinstance(grid, dict) else ()
        best: tuple[float, tuple[int, int]] | None = None
        for cell in cells if isinstance(cells, list) else ():
            if not isinstance(cell, dict):
                continue
            polygon, logical = cell.get("polygon"), cell.get("logical")
            if not isinstance(polygon, list) or not isinstance(logical, dict):
                continue
            contour = np.asarray(polygon, np.float32)
            distance = cv2.pointPolygonTest(contour, point, True)
            if distance >= -2 and (best is None or distance > best[0]):
                try:
                    best = distance, (int(logical["x"]), int(logical["y"]))
                except (KeyError, TypeError, ValueError):
                    continue
        return best[1] if best else None

    def _image_clicked(self, point: tuple[int, int]) -> None:
        mode = self.mode.currentData()
        if mode == "none":
            return
        logical = self._suggest_logical(point)
        annotation = PixelAnnotation(point, logical)
        if mode == "player":
            self.player = annotation
        elif mode == "enemy":
            self.enemies.append(annotation)
        elif mode == "reference":
            self.references.append(annotation)
        elif mode == "anchor":
            self.anchors.append(annotation)
        suggestion = f" ; logique proposée {logical}" if logical is not None else " ; aucune logique proposée"
        self.last_click.setText(f"Centre pixel {point}{suggestion}")
        self._update_points()
        self._refresh_image()

    def _draw_point(self, image: np.ndarray, item: PixelAnnotation, color: tuple[int, int, int], label: str) -> None:
        center = item.center.rounded()
        cv2.circle(image, center, 8, color, 2, cv2.LINE_AA)
        cv2.putText(image, label, (center[0] + 9, center[1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    def _refresh_image(self) -> None:
        image = (self.overlay if self.show_overlay.isChecked() else self.original).copy()
        if self.player:
            self._draw_point(image, self.player, (70, 230, 90), "J")
        for index, item in enumerate(self.enemies, 1):
            self._draw_point(image, item, (60, 70, 245), f"E{index}")
        for index, item in enumerate(self.references, 1):
            self._draw_point(image, item, (230, 170, 60), f"R{index}")
        for index, item in enumerate(self.anchors, 1):
            self._draw_point(image, item, (230, 70, 210), f"A{index}")
        self.canvas.set_image(image)

    def _update_points(self) -> None:
        player = "oui" if self.player else "non"
        self.points.setText(
            f"Joueur : {player} · Ennemis : {len(self.enemies)} · "
            f"Références : {len(self.references)} · Ancres : {len(self.anchors)}"
        )

    def _clear_points(self) -> None:
        self.player = None
        self.enemies.clear()
        self.references.clear()
        self.anchors.clear()
        self._update_points()
        self._refresh_image()

    def _load_existing(self) -> None:
        annotation = self.repository.read_annotation(self.entry)
        if annotation is None:
            self._update_points()
            return
        for combo, value in ((self.combat, annotation.combat_truth),
                             (self.turn, annotation.player_turn_truth)):
            combo.setCurrentIndex(combo.findData(value))
        self.ap.setValue(annotation.ap_truth if annotation.ap_truth is not None else -1)
        self.mp.setValue(annotation.mp_truth if annotation.mp_truth is not None else -1)
        self.digit_issue.setChecked(annotation.digit_issue == "1_vs_7")
        self.reference_complete.setChecked(annotation.reference_cells_complete is True)
        self.player = annotation.player
        self.enemies = list(annotation.enemies)
        self.references = list(annotation.reference_cells)
        self.anchors = list(annotation.grid_anchors)
        self.comments.setPlainText(annotation.comments or "")
        self._update_points()

    def _save(self) -> None:
        try:
            annotation = Annotation(
                observation_id=self.entry.observation_id,
                combat_truth=self.combat.currentData(),
                player_turn_truth=self.turn.currentData(),
                ap_truth=self.ap.value() if self.ap.value() >= 0 else None,
                mp_truth=self.mp.value() if self.mp.value() >= 0 else None,
                player=self.player,
                enemies=tuple(self.enemies), reference_cells=tuple(self.references),
                reference_cells_complete=True if self.reference_complete.isChecked() else None,
                grid_anchors=tuple(self.anchors),
                comments=self.comments.toPlainText().strip() or None,
                digit_issue="1_vs_7" if self.digit_issue.isChecked() else None,
            )
            self.repository.save_annotation(annotation)
        except ValueError as exc:
            QMessageBox.warning(self, "Annotation invalide", str(exc))
            return
        self.accept()


class CorpusPage(QWidget):
    def __init__(self, repository: CorpusRepository | None = None) -> None:
        super().__init__()
        self.repository = repository or CorpusRepository()
        outer = QVBoxLayout(self)
        heading = QLabel("Corpus / Annotation")
        heading.setObjectName("title")
        subtitle = QLabel("Import volontaire des observations, vérité terrain humaine et baseline LOT 3B-0")
        subtitle.setObjectName("subtitle")
        outer.addWidget(heading)
        outer.addWidget(subtitle)
        buttons = QHBoxLayout()
        self.import_button = QPushButton("Importer un debug")
        self.annotate_button = QPushButton("Ouvrir l'annotation")
        self.annotate_button.setObjectName("primary")
        self.promote_button = QPushButton("Promouvoir en fixture de test")
        self.benchmark_button = QPushButton("Calculer la baseline")
        refresh = QPushButton("Actualiser")
        for button in (self.import_button, self.annotate_button, self.promote_button,
                       self.benchmark_button, refresh):
            buttons.addWidget(button)
        buttons.addStretch()
        outer.addLayout(buttons)
        self.list = QListWidget()
        outer.addWidget(self.list, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        self.import_button.clicked.connect(self._import)
        self.annotate_button.clicked.connect(self._annotate)
        self.promote_button.clicked.connect(self._promote)
        self.benchmark_button.clicked.connect(self._benchmark)
        refresh.clicked.connect(self.refresh)
        self.list.itemDoubleClicked.connect(lambda _item: self._annotate())
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        try:
            entries = self.repository.list_entries()
        except ValueError as exc:
            self.summary.setText(f"Manifeste illisible : {exc}")
            return
        for entry in entries:
            marker = "annotée" if entry.annotation_available else "sans annotation"
            item = QListWidgetItem(
                f"{entry.session_id} · frame {entry.frame_index} · {entry.observation_id} · {marker} · {entry.usage}"
            )
            item.setData(Qt.ItemDataRole.UserRole, entry.observation_id)
            self.list.addItem(item)
        self.summary.setText(
            f"{len(entries)} observation(s), {sum(item.annotation_available for item in entries)} annotée(s). "
            "Aucune observation n'est promue automatiquement dans les tests."
        )

    def _selected(self) -> CorpusEntry | None:
        item = self.list.currentItem()
        if item is None:
            QMessageBox.information(self, "Corpus", "Sélectionnez une observation.")
            return None
        return self.repository.get_entry(str(item.data(Qt.ItemDataRole.UserRole)))

    def _import(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Choisir l'observation JSON créée par le debug", str(app_data_root() / "data" / "debug"),
            "Observation JSON (*-observation.json observation.json);;JSON (*.json)",
        )
        if not path:
            return
        try:
            entry = self.repository.import_debug(Path(path))
        except ValueError as exc:
            QMessageBox.warning(self, "Import impossible", str(exc))
            return
        self.refresh()
        matches = self.list.findItems(entry.observation_id, Qt.MatchFlag.MatchContains)
        if matches:
            self.list.setCurrentItem(matches[0])
        self.summary.setText(f"Observation importée sans modifier le debug : {entry.observation_id}")

    def _annotate(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        try:
            dialog = AnnotationDialog(self.repository, entry, self)
            dialog.showFullScreen()
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.refresh()
        except ValueError as exc:
            QMessageBox.warning(self, "Observation illisible", str(exc))

    def _promote(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Promouvoir en fixture",
            "Nom stable (ex. pa_7_basic ou grid_sprite_occlusion_01) :",
        )
        if not accepted:
            return
        answer = QMessageBox.question(
            self, "Confirmer la promotion",
            f"Copier explicitement {entry.observation_id} vers tests/fixtures/combat_real/{name} ?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            destination = self.repository.promote_fixture(entry.observation_id, name.strip())
        except ValueError as exc:
            QMessageBox.warning(self, "Promotion impossible", str(exc))
            return
        self.summary.setText(f"Fixture créée : {destination}")

    def _benchmark(self) -> None:
        try:
            report = run_benchmark(self.repository)
            json_path, markdown_path = write_reports(report, app_data_root() / "data" / "benchmarks")
        except ValueError as exc:
            QMessageBox.warning(self, "Benchmark impossible", str(exc))
            return
        self.summary.setText(
            f"Baseline calculée sur {report['corpus']['annotated_observations']} observation(s) annotée(s). "
            f"Rapports : {json_path} et {markdown_path}"
        )
