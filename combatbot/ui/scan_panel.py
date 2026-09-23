"""Validation humaine des icônes et caractéristiques observées."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget,
)

from combatbot.storage import Storage
from combatbot.vision.models import RecognizedText, ScanResult


class ScanPanel(QWidget):
    scan_requested = Signal(int, int, int, float)
    tooltip_requested = Signal(int)

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage
        self.profile_id: int | None = None
        self.current_spell_id: int | None = None
        outer = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.profile_label = QLabel("Profil : —")
        controls.addWidget(self.profile_label)
        self.page = self._spin(1, 30, 1)
        self.columns = self._spin(1, 30, 10)
        self.rows = self._spin(1, 5, 2)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0.50, 1.00)
        self.threshold.setSingleStep(0.01)
        self.threshold.setValue(0.88)
        for label, widget in (("Page", self.page), ("Colonnes", self.columns),
                              ("Rangées", self.rows), ("Seuil", self.threshold)):
            controls.addWidget(QLabel(label))
            controls.addWidget(widget)
        outer.addLayout(controls)
        self.grid_status = QLabel("Grille : nombre de lignes et colonnes à vérifier avant le scan.")
        outer.addWidget(self.grid_status)
        buttons = QHBoxLayout()
        scan = QPushButton("Scanner mes sorts")
        scan.setObjectName("primary")
        scan.clicked.connect(self._request_scan)
        buttons.addWidget(scan)
        tooltip = QPushButton("Analyser l'infobulle (3 s)")
        tooltip.clicked.connect(self._request_tooltip)
        buttons.addWidget(tooltip)
        buttons.addStretch()
        outer.addLayout(buttons)
        self.summary = QLabel("Connectez une fenêtre, confirmez la barre de sorts, puis vérifiez la grille proposée.")
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Icône", "Page", "Case", "Nom", "PA", "Portée", "Confiance / statut"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.currentCellChanged.connect(self._select_row)
        self.table.horizontalHeader().setStretchLastSection(True)
        outer.addWidget(self.table, 2)

        self.name = QLineEdit()
        self.ap_cost = self._spin(-1, 99, -1)
        self.min_range = self._spin(-1, 99, -1)
        self.max_range = self._spin(-1, 99, -1)
        self.modifiable_range = self._bool_combo()
        self.line_cast = self._bool_combo()
        self.line_of_sight = self._bool_combo()
        self.per_turn = self._spin(-1, 99, -1)
        self.per_target = self._spin(-1, 99, -1)
        self.damage = self._spin(-1, 9999, -1)
        self.effects = QLineEdit()
        self.raw_ocr = QTextEdit()
        self.raw_ocr.setReadOnly(True)
        self.raw_ocr.setMaximumHeight(75)
        fields = (
            ("Nom", self.name), ("PA", self.ap_cost), ("Portée minimale", self.min_range),
            ("Portée maximale", self.max_range), ("Portée modifiable", self.modifiable_range),
            ("Lancer en ligne", self.line_cast), ("Ligne de vue", self.line_of_sight),
            ("Par tour", self.per_turn), ("Par cible", self.per_target),
            ("Dégâts observés", self.damage), ("Effets lisibles", self.effects),
            ("Texte OCR brut", self.raw_ocr),
        )
        # Deux colonnes pour garder l'éditeur utilisable à la hauteur de la fenêtre.
        left = QFormLayout()
        right = QFormLayout()
        for index, (label, widget) in enumerate(fields):
            (left if index < 6 else right).addRow(label, widget)
        forms = QHBoxLayout()
        forms.addLayout(left, 1)
        forms.addLayout(right, 1)
        outer.addLayout(forms)
        actions = QHBoxLayout()
        for label, callback in (
            ("Enregistrer configuration", self._save_current),
            ("Valider ce sort", self._confirm_current),
            ("Valider les sélectionnés", self._confirm_selected),
            ("Ignorer l'emplacement", self._ignore_current),
        ):
            button = QPushButton(label)
            if label.startswith("Valider"):
                button.setObjectName("primary")
            button.clicked.connect(callback)
            actions.addWidget(button)
        outer.addLayout(actions)

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        if minimum < 0:
            widget.setSpecialValueText("Inconnu")
        widget.setValue(value)
        return widget

    @staticmethod
    def _bool_combo() -> QComboBox:
        widget = QComboBox()
        widget.addItem("Inconnu", None)
        widget.addItem("Oui", True)
        widget.addItem("Non", False)
        return widget

    def set_profile(self, profile_id: int) -> None:
        self.profile_id = profile_id
        profile = self.storage.get_profile(profile_id)
        self.profile_label.setText(f"Profil : {profile.label}")
        self.columns.setValue(int(self.storage.get_profile_setting(profile_id, "spell_columns", 10)))
        self.rows.setValue(int(self.storage.get_profile_setting(profile_id, "spell_rows", 2)))
        self.threshold.setValue(float(self.storage.get_profile_setting(profile_id, "match_threshold", 0.88)))
        self.reload()

    def set_grid_suggestion(self, columns: int, rows: int, confidence: float) -> None:
        self.columns.setValue(columns)
        self.rows.setValue(rows)
        self.grid_status.setText(
            f"Grille proposée : {columns} colonnes × {rows} rangées (confiance {confidence:.0%}). "
            "Vérifiez ces valeurs avant le scan."
        )

    def _request_scan(self) -> None:
        if self.profile_id is None:
            QMessageBox.warning(self, "Scan", "Sélectionnez un profil.")
            return
        for key, value in (("spell_columns", self.columns.value()), ("spell_rows", self.rows.value()),
                           ("match_threshold", self.threshold.value())):
            self.storage.set_profile_setting(self.profile_id, key, value)
        self.scan_requested.emit(self.page.value(), self.columns.value(), self.rows.value(), self.threshold.value())

    def _request_tooltip(self) -> None:
        if self.current_spell_id is None:
            QMessageBox.warning(self, "Infobulle", "Sélectionnez d'abord une icône scannée.")
            return
        self.tooltip_requested.emit(self.current_spell_id)

    def accept_scan(self, result: ScanResult) -> None:
        if self.profile_id is None:
            return
        errors = []
        duplicates = 0
        for candidate in result.candidates:
            if candidate.duplicate_of is not None:
                duplicates += 1
                if not self.storage.clear_unconfirmed_slot(self.profile_id, result.page, candidate.slot):
                    errors.append(f"Case {candidate.slot} : résultat confirmé à vérifier")
                continue
            try:
                self.storage.save_scan_candidate(self.profile_id, candidate)
            except ValueError as exc:
                errors.append(str(exc))
        for slot in result.empty_slots:
            if not self.storage.clear_unconfirmed_slot(self.profile_id, result.page, slot):
                errors.append(f"Case {slot} : sort confirmé absent de la nouvelle capture")
        self.page.setValue(result.page)
        self.reload()
        self.summary.setText(
            f"Page {result.page} : {len(result.candidates) - duplicates} icônes, "
            f"{len(result.empty_slots)} cases vides, {duplicates} doublons ignorés. "
            f"{len(errors)} emplacement(s) à vérifier."
        )
        if errors:
            QMessageBox.warning(self, "Scan partiel", "\n".join(errors))

    def accept_ocr(self, spell_id: int, recognized: RecognizedText) -> None:
        try:
            self.storage.save_recognized_characteristics(spell_id, recognized)
        except ValueError as exc:
            QMessageBox.warning(self, "Infobulle", str(exc))
            return
        self.reload(select_id=spell_id)
        self.summary.setText(
            f"OCR : confiance {recognized.confidence:.0%}. "
            f"Champs encore inconnus : {', '.join(recognized.uncertain_fields) or 'aucun'}. "
            "Vérifiez les valeurs avant validation."
        )

    def reload(self, select_id: int | None = None) -> None:
        self.current_spell_id = None
        rows = self.storage.list_profile_spells(self.profile_id) if self.profile_id else []
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        target_row = 0
        for index, row in enumerate(rows):
            pixmap = QPixmap()
            pixmap.loadFromData(row["icon_png"])
            icon_item = QTableWidgetItem()
            icon_item.setIcon(QIcon(pixmap))
            icon_item.setData(Qt.ItemDataRole.UserRole, row["id"])
            self.table.setItem(index, 0, icon_item)
            reach = "?" if row["min_range"] is None or row["max_range"] is None else f"{row['min_range']}–{row['max_range']}"
            confidence = row["recognition_confidence"] or row["presence_confidence"]
            score_label = f"icône {confidence:.0%}"
            if row["ocr_confidence"] is not None:
                score_label += f", OCR {row['ocr_confidence']:.0%}"
            readiness = " · prêt pour décision" if row["decision_ready"] else " · incomplet"
            values = (row["page"], row["slot"], row["name"] or "?", row["ap_cost"] if row["ap_cost"] is not None else "?",
                      reach, f"{score_label} • {row['status']}{readiness}")
            for column, value in enumerate(values, 1):
                self.table.setItem(index, column, QTableWidgetItem(str(value)))
            self.table.setRowHeight(index, 45)
            if row["id"] == select_id:
                target_row = index
        self.table.blockSignals(False)
        if rows:
            self.table.setCurrentCell(target_row, 0)
            self._select_row(target_row, 0, -1, -1)
        else:
            self._clear_form()

    def _select_row(self, row: int, column: int, previous_row: int, previous_column: int) -> None:
        if row < 0 or self.table.item(row, 0) is None:
            return
        spell_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        spell = self.storage.get_profile_spell(int(spell_id))
        self.current_spell_id = int(spell_id)
        self.name.setText(spell["name"] or "")
        for widget, key in ((self.ap_cost, "ap_cost"), (self.min_range, "min_range"),
                            (self.max_range, "max_range"), (self.per_turn, "per_turn"),
                            (self.per_target, "per_target"), (self.damage, "damage")):
            widget.setValue(spell[key] if spell[key] is not None else -1)
        for widget, key in ((self.modifiable_range, "modifiable_range"), (self.line_cast, "line_cast"),
                            (self.line_of_sight, "line_of_sight")):
            widget.setCurrentIndex(0 if spell[key] is None else 1 if spell[key] else 2)
        self.effects.setText(spell["effects"] or "")
        self.raw_ocr.setPlainText(spell["raw_ocr"] or "")

    def _clear_form(self) -> None:
        self.name.clear()
        self.effects.clear()
        self.raw_ocr.clear()
        for widget in (self.ap_cost, self.min_range, self.max_range, self.per_turn, self.per_target, self.damage):
            widget.setValue(-1)
        for widget in (self.modifiable_range, self.line_cast, self.line_of_sight):
            widget.setCurrentIndex(0)

    def _fields(self) -> dict[str, object]:
        value = lambda widget: None if widget.value() < 0 else widget.value()
        return {
            "name": self.name.text().strip() or None,
            "ap_cost": value(self.ap_cost), "min_range": value(self.min_range),
            "max_range": value(self.max_range),
            "modifiable_range": self.modifiable_range.currentData(),
            "line_cast": self.line_cast.currentData(), "line_of_sight": self.line_of_sight.currentData(),
            "per_turn": value(self.per_turn), "per_target": value(self.per_target),
            "damage": value(self.damage), "effects": self.effects.text().strip() or None,
        }

    def _save_current(self) -> bool:
        if self.current_spell_id is None:
            return False
        try:
            self.storage.save_profile_spell_fields(self.current_spell_id, self._fields())
        except ValueError as exc:
            QMessageBox.warning(self, "Configuration", str(exc))
            return False
        self.reload(self.current_spell_id)
        return True

    def _confirm_current(self) -> None:
        if self.current_spell_id is None:
            return
        try:
            self.storage.save_profile_spell_fields(self.current_spell_id, self._fields(), confirm=True)
        except ValueError as exc:
            QMessageBox.warning(self, "Validation", str(exc))
            return
        self.reload(self.current_spell_id)
        self.summary.setText("Sort vérifié et enregistré. Les champs inconnus restent vides ; un sort incomplet n'est pas utilisable par le moteur.")

    def _confirm_selected(self) -> None:
        ids = {
            self.table.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
            for index in self.table.selectedIndexes() if self.table.item(index.row(), 0)
        }
        if not ids:
            return
        if self.current_spell_id in ids:
            if not self._save_current():
                return
        errors = []
        confirmed = 0
        for spell_id in ids:
            try:
                self.storage.save_profile_spell_fields(int(spell_id), {}, confirm=True)
                confirmed += 1
            except ValueError as exc:
                errors.append(f"Sort {spell_id} : {exc}")
        self.reload()
        self.summary.setText(f"{confirmed} sort(s) confirmés ; {len(errors)} à corriger.")
        if errors:
            QMessageBox.warning(self, "Validation partielle", "\n".join(errors))

    def _ignore_current(self) -> None:
        if self.current_spell_id is not None:
            self.storage.ignore_profile_spell(self.current_spell_id)
            self.reload()
