"""« Preuve portée / LOS (4C) » : annoter la vérité du client sur une frame enregistrée.

Dans DOFUS, l'utilisateur sélectionne **lui-même** un sort (sans le lancer) : le client colore les
cellules ciblables. Il enregistre l'observation depuis Vision réelle (aucune action de PythonBot), puis
ouvre cette fenêtre : il choisit le sort, vérifie la cellule du lanceur, indique le bonus de portée s'il
le connaît, et clique chaque cellule pour la marquer ciblable → non ciblable → obstacle → effacée.

L'échantillon est enregistré sous ``<données>/targeting_proof/`` : le corpus et ses vérités ne sont
jamais modifiés. Aucune règle n'est adoptée ici (voir ``combat/targeting_proof.py``).
"""
from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
)

from combatbot.combat.targeting_proof import ProofSample, TargetingProofStore
from combatbot.corpus.dry_run_replay import frame_map_id
from combatbot.corpus.repository import CorpusRepository
from combatbot.ui.entity_annotation_dialog import _CellCanvas, entity_entries, projected_cells
from combatbot.ui.images import bgr_to_pixmap

CYCLE = (None, "TARGETABLE", "NOT_TARGETABLE", "OBSTACLE")
COLORS = {"TARGETABLE": (80, 235, 120), "NOT_TARGETABLE": (60, 60, 230), "OBSTACLE": (40, 200, 240),
          "CASTER": (255, 255, 255)}


class TargetingProofDialog(QDialog):
    def __init__(self, repository: CorpusRepository, spells, store: TargetingProofStore, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preuve portée / LOS (4C) — vérité du client, aucune action")
        self.repository = repository
        self.store = store
        # Seuls les sorts dont la portée min/max est renseignée peuvent servir de référence.
        self.spells = [spell for spell in spells if spell.min_range is not None and spell.max_range is not None]
        self.entries = entity_entries(repository)
        self.index = 0
        self.labels: dict[int, str] = {}
        self.caster: int | None = None
        self.choosing_caster = False
        self.cells: list[dict] = []
        self.image = None
        self.document: dict = {}
        layout = QVBoxLayout(self)
        self.header = QLabel()
        self.header.setWordWrap(True)
        layout.addWidget(self.header)
        controls = QHBoxLayout()
        self.spell = QComboBox()
        for spell in self.spells:
            caption = f"{spell.name or spell.key} ({spell.min_range}–{spell.max_range}" \
                      f"{', modifiable' if spell.modifiable_range else ''})"
            self.spell.addItem(caption, spell.key)
        self.bonus = QSpinBox()
        self.bonus.setRange(-1, 12)
        self.bonus.setSpecialValueText("inconnu")
        self.bonus.setValue(-1)
        caster = QPushButton("Choisir le lanceur")
        caster.clicked.connect(self._choose_caster)
        for label, widget in (("Sort", self.spell), ("Bonus de portée", self.bonus)):
            controls.addWidget(QLabel(label))
            controls.addWidget(widget)
        controls.addWidget(caster)
        layout.addLayout(controls)
        self.canvas = _CellCanvas()
        self.canvas.clicked.connect(self._click)
        layout.addWidget(self.canvas, 1)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        buttons = QHBoxLayout()
        for caption, callback in (("◀ Précédente", lambda: self._move(-1)), ("Suivante ▶", lambda: self._move(1)),
                                  ("Effacer", self._clear), ("Enregistrer l'échantillon", self._save)):
            button = QPushButton(caption)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._show()

    # ------------------------------------------------------------------ données
    def _show(self) -> None:
        if not self.entries:
            self.header.setText("Aucune observation avec grille GameData projetée. Dans DOFUS, sélectionnez un sort "
                                "(sans le lancer), puis « Enregistrer cette observation » dans l'observation réelle.")
            return
        entry = self.entries[self.index]
        self.document = self.repository.read_observation(entry)
        self.cells = projected_cells(self.document)
        self.image = cv2.imread(str(self.repository.resolve(entry.paths["frame"])), cv2.IMREAD_COLOR)
        prediction = self.document.get("prediction") or {}
        self.caster = prediction.get("player_cell_id") if isinstance(prediction.get("player_cell_id"), int) else None
        self.labels = {}
        map_id = frame_map_id(self.document)
        self.header.setText(f"Frame {self.index + 1}/{len(self.entries)} · {entry.observation_id} · map "
                            f"{map_id if map_id is not None else 'NON PROUVÉE (échantillon refusé)'} · "
                            "clic : ciblable → non ciblable → obstacle → effacé")
        self._render()

    def _cell_at(self, point) -> int | None:
        if point is None:
            return None
        for cell in self.cells:
            if cv2.pointPolygonTest(np.asarray(cell["polygon"], np.float32), (float(point[0]), float(point[1])),
                                    False) >= 0:
                return int(cell["cell_id"])
        return None

    def _render(self) -> None:
        if self.image is None:
            return
        output = self.image.copy()
        for cell in self.cells:
            cell_id = int(cell["cell_id"])
            label = "CASTER" if cell_id == self.caster else self.labels.get(cell_id)
            color = COLORS.get(label, (90, 150, 190))
            cv2.polylines(output, [np.asarray(cell["polygon"], np.int32)], True, color, 3 if label else 1, cv2.LINE_AA)
        self.canvas.image_size = (output.shape[1], output.shape[0])
        self.canvas.setPixmap(bgr_to_pixmap(output).scaled(
            self.canvas.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        counts = {name: sum(1 for value in self.labels.values() if value == name) for name in CYCLE[1:]}
        self.summary.setText(f"Lanceur : {self.caster if self.caster is not None else 'inconnu'} · "
                             f"ciblables {counts['TARGETABLE']} · non ciblables {counts['NOT_TARGETABLE']} · "
                             f"obstacles {counts['OBSTACLE']} · échantillons enregistrés {len(self.store.load())}")

    # ------------------------------------------------------------------ interactions
    def _choose_caster(self) -> None:
        self.choosing_caster = True
        self.summary.setText("Cliquez la cellule du lanceur.")

    def _click(self, point, _global=None) -> None:
        cell_id = self._cell_at(point)
        if cell_id is None:
            return
        self.cycle(cell_id)

    def cycle(self, cell_id: int) -> None:
        if self.choosing_caster:
            self.caster, self.choosing_caster = cell_id, False
            self.labels.pop(cell_id, None)
        elif cell_id != self.caster:
            following = CYCLE[(CYCLE.index(self.labels.get(cell_id)) + 1) % len(CYCLE)]
            if following is None:
                self.labels.pop(cell_id, None)
            else:
                self.labels[cell_id] = following
        self._render()

    def _clear(self) -> None:
        self.labels = {}
        self._render()

    def _move(self, step: int) -> None:
        if self.entries:
            self.index = (self.index + step) % len(self.entries)
            self._show()

    def build_sample(self) -> ProofSample:
        if not self.entries:
            raise ValueError("Aucune frame")
        map_id = frame_map_id(self.document)
        if map_id is None:
            raise ValueError("Map non prouvée (ni résolue automatiquement, ni mapId vérifié) : échantillon refusé")
        if self.caster is None:
            raise ValueError("Cellule du lanceur inconnue : cliquez « Choisir le lanceur »")
        key = self.spell.currentData()
        spell = next((item for item in self.spells if item.key == key), None)
        if spell is None:
            raise ValueError("Aucun sort avec portée min/max renseignée")
        entry = self.entries[self.index]
        sample = ProofSample(
            f"{entry.observation_id}-{spell.key.replace(':', '_')}", entry.observation_id, map_id, self.caster,
            spell.key, spell.name, spell.min_range, spell.max_range, spell.modifiable_range, spell.line_cast,
            spell.line_of_sight, None if self.bonus.value() < 0 else self.bonus.value(),
            frozenset(cell for cell, label in self.labels.items() if label == "TARGETABLE"),
            frozenset(cell for cell, label in self.labels.items() if label == "NOT_TARGETABLE"),
            frozenset(cell for cell, label in self.labels.items() if label == "OBSTACLE"),
            notes=f"provenance du sort : {spell.provenance.value}")
        sample.validate()
        return sample

    def _save(self) -> None:
        try:
            path = self.store.save(self.build_sample())
        except ValueError as exc:
            QMessageBox.warning(self, "Preuve portée / LOS", str(exc))
            return
        self.summary.setText(f"Échantillon enregistré : {path.name}")
