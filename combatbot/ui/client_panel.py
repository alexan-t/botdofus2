"""Sélection du client, profils, capture diagnostique et calibration."""

from __future__ import annotations

import sqlite3

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget, QInputDialog,
)

from combatbot.storage import Storage
from combatbot.ui.images import bgr_to_pixmap
from combatbot.vision.models import CapturedFrame, ConnectionResult, Profile, RecognizedProfile
from combatbot.vision.window import list_dofus_windows


STEP_CAPTIONS = ("Fenêtre", "Capture", "Calibration", "Prêt")
PANEL_STYLE = """
QLabel#step { background: #172234; border: 1px solid #273449; border-radius: 14px; padding: 6px 10px;
              color: #8b98ab; font-weight: 600; }
QLabel#step[state="done"] { background: #134e45; border-color: #1f7a6b; color: #7ee8cf; }
QLabel#step[state="current"] { background: #173b43; border-color: #40c8aa; color: #f1f5f9; }
QLabel#nextStep { background: #173b43; border: 1px solid #2f8f7e; border-radius: 10px; padding: 10px 14px;
                  color: #d9fbf2; font-weight: 600; font-size: 14px; }
QLabel#preview { border: 1px solid #273449; border-radius: 12px; background: #0b1220; color: #8b98ab; }
QToolButton#section { border: none; color: #9ca3af; font-weight: 600; padding: 4px 0; background: transparent; }
QToolButton#section:hover { color: #e5e7eb; }
"""


def _section(title: str, content: QWidget) -> QWidget:
    """Bloc repliable, fermé par défaut : les détails n'encombrent pas le parcours principal."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    toggle = QToolButton()
    toggle.setObjectName("section")
    toggle.setText(f"▸  {title}")
    toggle.setCheckable(True)
    toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    content.setVisible(False)

    def switch(shown: bool) -> None:
        content.setVisible(shown)
        toggle.setText(f"{'▾' if shown else '▸'}  {title}")

    toggle.toggled.connect(switch)
    layout.addWidget(toggle)
    layout.addWidget(content)
    return box


class ClientPanel(QWidget):
    profile_changed = Signal(int)
    connect_requested = Signal(int)
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
        self.calibration_state = "absent"   # absent | review | ok (fixé par la fenêtre principale)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        self.setStyleSheet(PANEL_STYLE)
        subtitle = QLabel("Lecture seule : DofBot2 regarde l'écran, il ne clique jamais dans DOFUS.")
        subtitle.setObjectName("subtitle")
        layout.addWidget(subtitle)
        # Étapes visibles d'un coup d'œil, puis une seule consigne : la prochaine action.
        steps = QHBoxLayout()
        steps.setSpacing(8)
        self.step_chips: list[QLabel] = []
        for index, caption in enumerate(STEP_CAPTIONS, start=1):
            chip = QLabel(f"{index}  {caption}")
            chip.setObjectName("step")
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            steps.addWidget(chip, 1)
            self.step_chips.append(chip)
        layout.addLayout(steps)
        self.next_step = QLabel()
        self.next_step.setObjectName("nextStep")
        self.next_step.setWordWrap(True)
        layout.addWidget(self.next_step)

        connection = QFrame()
        connection.setObjectName("card")
        grid = QGridLayout(connection)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(10)
        self.profiles = QComboBox()
        self.profiles.currentIndexChanged.connect(self._load_profile)
        create = QPushButton("Nouveau")
        create.setToolTip("Créer un profil (un par personnage / disposition d'écran)")
        create.clicked.connect(self._new_profile)
        grid.addWidget(QLabel("Profil"), 0, 0)
        grid.addWidget(self.profiles, 0, 1)
        grid.addWidget(create, 0, 2)
        self.windows = QComboBox()
        self.windows.currentIndexChanged.connect(self._window_changed)
        refresh = QPushButton("↻")
        refresh.setToolTip("Rechercher de nouveau les fenêtres DOFUS")
        refresh.setFixedWidth(40)
        refresh.clicked.connect(self.refresh_windows)
        self.connect_button = QPushButton("Connecter")
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button = QPushButton("Déconnecter")
        self.disconnect_button.clicked.connect(self.disconnect_requested)
        grid.addWidget(QLabel("Fenêtre"), 1, 0)
        grid.addWidget(self.windows, 1, 1)
        grid.addWidget(refresh, 1, 2)
        buttons = QHBoxLayout()
        buttons.addWidget(self.connect_button)
        buttons.addWidget(self.disconnect_button)
        grid.addLayout(buttons, 1, 3)
        grid.setColumnStretch(1, 1)
        self.connection_status = QLabel("Fenêtre non sélectionnée")
        self.connection_status.setObjectName("subtitle")
        grid.addWidget(self.connection_status, 2, 1, 1, 3)
        layout.addWidget(connection)

        self.preview = QLabel("Aucune capture")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(300)
        self.preview.setObjectName("preview")
        layout.addWidget(self.preview, 1)
        actions = QHBoxLayout()
        self.confirm_capture_button = QPushButton("Oui, l'aperçu montre DOFUS")
        self.confirm_capture_button.setEnabled(False)
        self.confirm_capture_button.clicked.connect(lambda: self._confirm_capture())
        self.calibrate_button = QPushButton("Calibrer les zones")
        self.calibrate_button.clicked.connect(self.calibrate_requested)
        self.recognize_button = QPushButton("Lire le profil visible")
        self.recognize_button.clicked.connect(self.recognize_requested)
        for button in (self.confirm_capture_button, self.calibrate_button, self.recognize_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.calibration_status = QLabel("Calibration absente")
        self.calibration_status.setObjectName("subtitle")
        layout.addWidget(self.calibration_status)

        # Progression historique conservée pour la fenêtre principale, remplacée à l'écran par les étapes.
        self.progress = QProgressBar()
        self.progress.setRange(0, 5)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        diagnostics_box = QWidget()
        diagnostics = QFormLayout(diagnostics_box)
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
        layout.addWidget(_section("Détails techniques", diagnostics_box))

        identity_box = QWidget()
        identity_layout = QVBoxLayout(identity_box)
        identity_layout.setContentsMargins(0, 0, 0, 0)
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
        identity_layout.addLayout(identity)
        self.recognition_status = QLabel("Les informations non lisibles restent inconnues.")
        self.recognition_status.setWordWrap(True)
        identity_layout.addWidget(self.recognition_status)
        save = QPushButton("Enregistrer le profil")
        save.setObjectName("primary")
        save.clicked.connect(self._save_profile)
        identity_layout.addWidget(save)
        layout.addWidget(_section("Personnage (facultatif)", identity_box))
        self.refresh_profiles()
        self.refresh_windows()
        self.update_state()

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        if self.connected_hwnd is None:
            self.refresh_windows()   # DOFUS lancé après DofBot2 : pas besoin de cliquer Actualiser

    def set_calibration_state(self, state: str, text: str) -> None:
        self.calibration_state = state
        self.calibration_status.setText(text)
        self.update_state()

    def update_state(self) -> None:
        """Active seulement les boutons utiles et affiche la prochaine étape."""
        if not hasattr(self, "next_step"):
            return
        connected = self.connected_hwnd is not None
        has_window = self.windows.currentData() is not None
        calibration = self.storage.load_calibration(self.profile_id) if self.profile_id is not None else None
        readable = calibration is not None and {"hp", "ap", "mp"} <= set(calibration.zones)
        self.connect_button.setEnabled(has_window)
        self.connect_button.setText("Reconnecter" if connected else "Connecter")
        self.disconnect_button.setEnabled(connected)
        self.confirm_capture_button.setEnabled(connected and self.frame is not None and not self.content_confirmed)
        self.calibrate_button.setEnabled(connected)
        self.calibrate_button.setText("Recalibrer" if calibration is not None else "Calibrer les zones")
        self.recognize_button.setEnabled(connected and readable)
        if self.profile_id is None:
            step = "Créez un profil avec « Nouveau profil »."
        elif not has_window:
            step = "Lancez DOFUS : la fenêtre apparaîtra ici (ou cliquez ↻)."
        elif not connected:
            step = "Cliquez « Connecter ». DofBot2 se masque une seconde le temps de capturer DOFUS."
        elif not self.content_confirmed:
            step = "Regardez l'aperçu ci-dessous. Si c'est bien votre jeu, cliquez « Oui, l'aperçu montre DOFUS »."
        elif calibration is None or self.calibration_state == "absent":
            step = "Cliquez « Calibrer les zones » et posez chaque cadre sur le jeu."
        elif self.calibration_state == "review":
            step = "La fenêtre a changé : cliquez « Recalibrer » et vérifiez les zones « À vérifier »."
        else:
            step = "✓ Prêt. Allez dans Combat → Vision réelle pour observer."
        self.next_step.setText(step)
        reached = (0 if not connected else 1 if not self.content_confirmed
                   else 2 if calibration is None or self.calibration_state != "ok" else 4)
        for index, chip in enumerate(self.step_chips):
            chip.setProperty("state", "done" if index < reached else "current" if index == reached else "todo")
            chip.style().unpolish(chip)
            chip.style().polish(chip)

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
        self.update_state()

    def connect_to(self, hwnd: int) -> bool:
        """Sélectionne la fenêtre choisie à l'écran « Connexion » de DofBot2 puis la vérifie.
        Retourne False si elle n'est plus listée ; rien n'est relancé si elle est déjà connectée."""
        if self.connected_hwnd == hwnd:
            return True
        self.refresh_windows()
        index = self.windows.findData(hwnd)
        if index < 0:
            return False
        self.windows.setCurrentIndex(index)
        self._connect()
        return True

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
        self.calibration_state = "ok" if calibration else "absent"
        self.calibration_status.setText(
            f"Calibration enregistrée : {len(calibration.zones)} zone(s)" if calibration else "Calibration absente"
        )
        self.update_state()
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
        self.update_state()
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
        self.update_state()

    def show_error(self, message: str) -> None:
        self.last_error.setText(message)
        self.connection_status.setText(message)
        self.diagnostic.emit(f"[CONNEXION] {message}")

    def _confirm_capture(self, automatic: bool = False) -> None:
        if self.connected_hwnd is None or self.frame is None or self.content_confirmed:
            return
        self.content_confirmed = True
        text = ("Capture reconnue : même fenêtre et même taille que la dernière confirmation"
                if automatic else "Capture confirmée visuellement par l'utilisateur")
        self.capture_status.setText(text)
        self.connection_status.setText("Fenêtre attachée — " + text[0].lower() + text[1:])
        self.diagnostic.emit(text)
        self.update_state()
        self.capture_confirmed.emit()

    def set_frame(self, frame: CapturedFrame) -> None:
        self.frame = frame
        pixmap = bgr_to_pixmap(frame.image)
        self.preview.setPixmap(pixmap.scaled(max(600, self.preview.width() - 4), max(300, self.preview.height() - 4),
                                             Qt.AspectRatioMode.KeepAspectRatio,
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
