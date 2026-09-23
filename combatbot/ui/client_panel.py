"""Sélection du client, profils, capture diagnostique et calibration."""

from __future__ import annotations

import sqlite3

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QVBoxLayout, QWidget, QInputDialog,
)

from combatbot.storage import Storage
from combatbot.ui.images import bgr_to_pixmap
from combatbot.vision.models import CapturedFrame, ConnectionResult, Profile, RecognizedProfile
from combatbot.vision.window import list_dofus_windows


class ClientPanel(QWidget):
    profile_changed = Signal(int)
    connect_requested = Signal(int)
    capture_requested = Signal()
    calibrate_requested = Signal()
    recognize_requested = Signal()
    disconnect_requested = Signal()
    capture_confirmed = Signal()
    diagnostic = Signal(str)

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage
        self.connected_hwnd: int | None = None
        self.frame: CapturedFrame | None = None
        self.content_confirmed = False
        layout = QVBoxLayout(self)
        heading = QLabel("Client DOFUS — observation seule")
        heading.setObjectName("title")
        layout.addWidget(heading)
        profile_row = QHBoxLayout()
        self.profiles = QComboBox()
        self.profiles.currentIndexChanged.connect(self._load_profile)
        profile_row.addWidget(QLabel("Profil"))
        profile_row.addWidget(self.profiles, 1)
        create = QPushButton("Nouveau profil")
        create.clicked.connect(self._new_profile)
        profile_row.addWidget(create)
        layout.addLayout(profile_row)

        window_row = QHBoxLayout()
        self.windows = QComboBox()
        self.windows.currentIndexChanged.connect(self._window_changed)
        window_row.addWidget(QLabel("Fenêtre"))
        window_row.addWidget(self.windows, 1)
        refresh = QPushButton("Actualiser")
        refresh.clicked.connect(self.refresh_windows)
        window_row.addWidget(refresh)
        connect = QPushButton("Connecter")
        connect.setObjectName("primary")
        connect.clicked.connect(self._connect)
        window_row.addWidget(connect)
        disconnect = QPushButton("Déconnecter")
        disconnect.clicked.connect(self.disconnect_requested)
        window_row.addWidget(disconnect)
        layout.addLayout(window_row)
        self.connection_status = QLabel("Fenêtre non sélectionnée")
        layout.addWidget(self.connection_status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 5)
        self.progress.setFormat("Sélection → Capture → Détection → Vérification → Profil prêt : %v/5")
        layout.addWidget(self.progress)
        diagnostics = QFormLayout()
        self.window_title = QLabel("—")
        self.window_handle = QLabel("—")
        self.client_size = QLabel("—")
        self.capture_status = QLabel("Non testée")
        self.last_error = QLabel("—")
        self.last_success = QLabel("—")
        for caption, widget in (("Titre", self.window_title), ("HWND", self.window_handle),
                                ("Zone cliente", self.client_size), ("Capture", self.capture_status),
                                ("Dernière erreur", self.last_error), ("Dernière vérification", self.last_success)):
            widget.setWordWrap(True)
            diagnostics.addRow(caption, widget)
        layout.addLayout(diagnostics)
        actions = QHBoxLayout()
        self.capture_button = QPushButton("Tester la capture")
        self.capture_button.clicked.connect(self.capture_requested)
        self.confirm_capture_button = QPushButton("Confirmer la capture affichée")
        self.confirm_capture_button.setEnabled(False)
        self.confirm_capture_button.clicked.connect(self._confirm_capture)
        self.calibrate_button = QPushButton("Recalibrer")
        self.calibrate_button.clicked.connect(self.calibrate_requested)
        self.recognize_button = QPushButton("Lire le profil visible")
        self.recognize_button.clicked.connect(self.recognize_requested)
        for button in (self.capture_button, self.confirm_capture_button,
                       self.calibrate_button, self.recognize_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.calibration_status = QLabel("Calibration absente")
        layout.addWidget(self.calibration_status)
        self.preview = QLabel("Aucune capture")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(250)
        self.preview.setStyleSheet("border: 1px solid #34445b; background: #172234;")
        layout.addWidget(self.preview)

        identity = QFormLayout()
        self.label = QLineEdit()
        self.name = QLineEdit()
        self.character_class = QLineEdit()
        self.hp_current = self._unknown_spin(999999)
        self.hp_max = self._unknown_spin(999999)
        self.ap = self._unknown_spin(99)
        self.mp = self._unknown_spin(99)
        self.notes = QLineEdit()
        for caption, widget in (
            ("Libellé du profil", self.label), ("Nom observé / corrigé", self.name),
            ("Classe observée / corrigée", self.character_class),
            ("PV actuels", self.hp_current), ("PV maximum", self.hp_max),
            ("PA affichés", self.ap), ("PM affichés", self.mp), ("Notes", self.notes),
        ):
            identity.addRow(caption, widget)
        layout.addLayout(identity)
        self.recognition_status = QLabel("Les informations non lisibles restent inconnues.")
        self.recognition_status.setWordWrap(True)
        layout.addWidget(self.recognition_status)
        save = QPushButton("Enregistrer le profil")
        save.setObjectName("primary")
        save.clicked.connect(self._save_profile)
        layout.addWidget(save)
        self.refresh_profiles()
        self.refresh_windows()

    @staticmethod
    def _unknown_spin(maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(-1, maximum)
        spin.setSpecialValueText("Inconnu")
        spin.setValue(-1)
        return spin

    @property
    def profile_id(self) -> int | None:
        value = self.profiles.currentData()
        return int(value) if value is not None else None

    def refresh_profiles(self, select_id: int | None = None) -> None:
        current = select_id or self.profile_id
        self.profiles.blockSignals(True)
        self.profiles.clear()
        for profile in self.storage.list_profiles():
            self.profiles.addItem(profile.label, profile.id)
        index = self.profiles.findData(current)
        self.profiles.setCurrentIndex(index if index >= 0 else 0)
        self.profiles.blockSignals(False)
        self._load_profile()

    def refresh_windows(self) -> None:
        previous = self.windows.currentData()
        self.windows.blockSignals(True)
        self.windows.clear()
        try:
            windows = list_dofus_windows()
        except RuntimeError as exc:
            self.windows.blockSignals(False)
            self.show_error(str(exc))
            return
        for info in windows:
            state = " (minimisée)" if info.minimized else ""
            self.windows.addItem(f"{info.title} — HWND {info.hwnd}{state}", info.hwnd)
        index = self.windows.findData(previous)
        if index >= 0:
            self.windows.setCurrentIndex(index)
        self.windows.blockSignals(False)
        self._window_changed()
        if not windows:
            self.show_error("Aucune fenêtre DOFUS visible détectée")

    def _window_changed(self) -> None:
        hwnd = self.windows.currentData()
        if self.connected_hwnd is not None and hwnd != self.connected_hwnd:
            self.disconnect_requested.emit()
        self.window_title.setText(self.windows.currentText().split(" — HWND ")[0] if hwnd else "—")
        self.window_handle.setText(str(hwnd) if hwnd else "—")
        if self.connected_hwnd is None:
            self.connection_status.setText("Fenêtre sélectionnée — cliquez sur Connecter" if hwnd else "Fenêtre non sélectionnée")
            self.progress.setValue(1 if hwnd else 0)

    def _connect(self) -> None:
        hwnd = self.windows.currentData()
        if hwnd is None:
            QMessageBox.warning(self, "Connexion", "Aucune fenêtre DOFUS à sélectionner.")
            return
        self.connect_requested.emit(int(hwnd))

    def _new_profile(self) -> None:
        label, accepted = QInputDialog.getText(self, "Nouveau profil", "Libellé du profil :")
        if not accepted or not label.strip():
            return
        try:
            profile_id = self.storage.save_profile(Profile(None, label.strip()))
        except (ValueError, sqlite3.IntegrityError) as exc:
            QMessageBox.warning(self, "Profil", str(exc))
            return
        self.refresh_profiles(profile_id)

    def _load_profile(self) -> None:
        profile_id = self.profile_id
        if profile_id is None:
            return
        profile = self.storage.get_profile(profile_id)
        self.connected_hwnd = None  # Un handle persistant doit être reconnecté dans cette session.
        self.frame = None
        self.content_confirmed = False
        self.confirm_capture_button.setEnabled(False)
        self.preview.clear()
        self.preview.setText("Aucune capture pour ce profil")
        self.label.setText(profile.label)
        self.name.setText(profile.name or "")
        self.character_class.setText(profile.character_class or "")
        for widget, value in ((self.hp_current, profile.hp_current), (self.hp_max, profile.hp_max),
                              (self.ap, profile.ap), (self.mp, profile.mp)):
            widget.setValue(value if value is not None else -1)
        self.notes.setText(profile.notes)
        self.client_size.setText("—")
        self.capture_status.setText("Non testée")
        self.last_error.setText("—")
        self._window_changed()
        calibration = self.storage.load_calibration(profile_id)
        self.calibration_status.setText(
            f"Calibration enregistrée : {len(calibration.zones)} zone(s), à revalider sur capture"
            if calibration else "Calibration absente"
        )
        self.profile_changed.emit(profile_id)

    def _save_profile(self) -> None:
        profile_id = self.profile_id
        if profile_id is None:
            return
        number = lambda widget: None if widget.value() < 0 else widget.value()
        profile = Profile(
            profile_id, self.label.text().strip(), self.connected_hwnd,
            self.name.text().strip() or None, self.character_class.text().strip() or None,
            number(self.hp_current), number(self.hp_max), number(self.ap), number(self.mp),
            self.notes.text().strip(),
        )
        try:
            self.storage.save_profile(profile)
        except (ValueError, sqlite3.IntegrityError) as exc:
            QMessageBox.warning(self, "Profil", str(exc))
            return
        self.profiles.setItemText(self.profiles.currentIndex(), profile.label)
        self.diagnostic.emit(f"Profil {profile.label} enregistré")

    def set_connection_result(self, result: ConnectionResult) -> None:
        self.connected_hwnd = int(result.details["hwnd"]) if result.success else None
        self.content_confirmed = result.success and result.code == "CONTENT_VERIFIED"
        self.confirm_capture_button.setEnabled(result.success and result.frame is not None)
        self.window_title.setText(str(result.details.get("title", self.window_title.text())))
        self.window_handle.setText(str(result.details.get("hwnd", "—")))
        width, height = result.details.get("width"), result.details.get("height")
        self.client_size.setText(f"{width} × {height}" if width and height else "—")
        self.capture_status.setText(result.message)
        self.connection_status.setText(result.message if result.success else f"Échec : {result.message}")
        self.progress.setValue(2 if result.success else 1)
        if result.success:
            self.last_success.setText(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self.last_error.setText("—")
            if result.frame is not None:
                self.set_frame(result.frame)
        else:
            self.frame = None
            self.last_error.setText(f"{result.code} : {result.message}")
        self.diagnostic.emit(f"[CONNEXION] {result.step} / {result.code} : {result.message} | {result.details}")

    def set_disconnected(self, reason: str = "Déconnecté") -> None:
        self.connected_hwnd = None
        self.frame = None
        self.content_confirmed = False
        self.confirm_capture_button.setEnabled(False)
        self.preview.clear()
        self.preview.setText("Aucune capture active")
        self.capture_status.setText("Non testée")
        self.connection_status.setText(reason)
        self.progress.setValue(1 if self.windows.currentData() is not None else 0)

    def show_error(self, message: str) -> None:
        self.last_error.setText(message)
        self.connection_status.setText(message)
        self.diagnostic.emit(f"[CONNEXION] {message}")

    def _confirm_capture(self) -> None:
        if self.connected_hwnd is None or self.frame is None:
            return
        self.content_confirmed = True
        self.capture_status.setText("Capture confirmée visuellement par l'utilisateur")
        self.connection_status.setText("Fenêtre attachée — capture confirmée visuellement")
        self.diagnostic.emit("Capture confirmée visuellement par l'utilisateur")
        self.capture_confirmed.emit()

    def set_frame(self, frame: CapturedFrame) -> None:
        self.frame = frame
        pixmap = bgr_to_pixmap(frame.image)
        self.preview.setPixmap(pixmap.scaled(600, 300, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))
        self.preview.setToolTip(f"Capture cliente {frame.client.width} × {frame.client.height}")
        if not frame.activation_succeeded:
            self.recognition_status.setText(
                "Windows n'a pas activé la fenêtre choisie. Vérifiez que l'aperçu montre bien le client sans recouvrement."
            )

    def set_recognition(self, recognized: RecognizedProfile) -> None:
        for widget, value in ((self.name, recognized.name), (self.character_class, recognized.character_class)):
            if value is not None and not widget.text().strip():
                widget.setText(value)
        for widget, value in ((self.hp_current, recognized.hp_current), (self.hp_max, recognized.hp_max),
                              (self.ap, recognized.ap), (self.mp, recognized.mp)):
            if value is not None and widget.value() < 0:
                widget.setValue(value)
        uncertain = ", ".join(recognized.uncertain_fields) or "aucun"
        self.recognition_status.setText(
            f"Lecture proposée (confiance OCR {recognized.confidence:.0%}). "
            f"Champs incertains : {uncertain}. Vérifiez puis enregistrez manuellement."
        )
