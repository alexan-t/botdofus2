"""Pages métier historiques de l'interface, sans règles de combat embarquées."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
    QTabWidget, QScrollArea,
)

from combatbot.models import CombatSnapshot, Spell, Strategy, StrategyMode, TargetPriority
from combatbot.storage import Storage
from combatbot.ui.grid_widget import GridWidget
from combatbot.ui.images import bgr_to_pixmap
from combatbot.ui.client_panel import ClientPanel
from combatbot.ui.scan_panel import ScanPanel
from combatbot.ui.gamedata_panel import GameDataPanel
from combatbot.ui.jobs import JobRunner
from combatbot.vision.combat_models import GRID_SOURCE_GAMEDATA, ObservationPacket
from combatbot.vision.combat_observer import OverlayOptions


def title(text: str, subtitle: str) -> QVBoxLayout:
    layout = QVBoxLayout()
    heading = QLabel(text)
    heading.setObjectName("title")
    description = QLabel(subtitle)
    description.setObjectName("subtitle")
    layout.addWidget(heading)
    layout.addWidget(description)
    layout.addSpacing(12)
    return layout


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)
    return frame, layout


GRID_REASONS = {
    "NO_GAMEDATA_PROFILE": "Grille non calibrée : cliquez « Calibrer projection de grille » (une fois par écran).",
    "NO_MAP_ID_DECLARED": "Map non déclarée : tapez /mapid dans DOFUS, saisissez l'ID puis « Charger ».",
    "NO_TOPOLOGY_SOURCE": "Données du client non chargées : Paramètres → Données du client.",
}


def _hud_reading(evidence: dict | None) -> tuple[int | None, float | None, str]:
    evidence = evidence or {}
    raw = evidence.get("raw_candidates") or {}
    return raw.get("rapidocr_value"), raw.get("rapidocr_confidence"), str(evidence.get("reason", ""))


def collection_checklist(metadata: dict, observation, recording: tuple[str, str] | None = None) -> list[tuple[str, str]]:
    """Liste lisible (état, texte) : ok | warn | todo. Aucune valeur n'est inventée ni validée ici."""
    items: list[tuple[str, str]] = []
    if metadata.get("grid_source") == "GAMEDATA_PROJECTED":
        items.append(("ok", f"Grille GameData projetée — map {metadata.get('map_id_declared')}"))
    else:
        reason = str(metadata.get("grid_source_reason") or "")
        items.append(("todo", GRID_REASONS.get(reason, f"Grille GameData indisponible ({reason or 'inconnu'}).")))
    hud = []
    for label, value, evidence in (("PA", observation.ap, observation.ap_read), ("PM", observation.mp, observation.mp_read)):
        if value is not None:
            continue
        ocr, confidence, reason = _hud_reading(evidence)
        if reason == "NO_TEMPLATE":
            hint = f"l'OCR propose {ocr} ({confidence:.0%}), non validé" if ocr is not None else "illisible"
            hud.append(f"{label} : {hint}")
        else:
            hud.append(f"{label} : {reason or 'illisible'}")
    if hud:
        items.append(("warn", "PA/PM pas encore appris sur ce PC (" + " · ".join(hud) + "). "
                              "Normal : ils s'apprennent avec la revue HUD."))
    else:
        items.append(("ok", f"PA {observation.ap} · PM {observation.mp}"))
    pipeline = (metadata.get("entities") or {}).get("pipeline")
    if pipeline != "CELL_ENTITY_DETECTOR":
        items.append(("todo", "Joueur/ennemis : il faut d'abord la grille GameData."))
    elif observation.player_cell_id is None:
        items.append(("warn", "Joueur/ennemis pas encore appris pour cet écran : normal pendant la collecte TRAIN "
                              "(vous les annoterez ensuite)."))
    else:
        items.append(("ok", f"Joueur en cellule {observation.player_cell_id}"))
    if recording is not None:
        items.append(recording)
    return items


class DashboardPage(QWidget):
    start_clicked = Signal()
    pause_clicked = Signal()
    stop_clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        outer = title("Tableau de bord", "Simulation de combat ; observation du client dans Paramètres et Sorts")
        metrics = QGridLayout()
        self.values: dict[str, QLabel] = {}
        for index, (key, label) in enumerate((
            ("status", "État du bot"), ("character", "Personnage"),
            ("combats", "Combats"), ("victories", "Victoires"),
            ("defeats", "Défaites"), ("xp", "XP simulée"),
            ("kamas", "Kamas simulés"),
        )):
            frame, box = card()
            caption = QLabel(label)
            caption.setObjectName("subtitle")
            value = QLabel("—")
            value.setObjectName("metric")
            value.setWordWrap(True)
            box.addWidget(caption)
            box.addWidget(value)
            metrics.addWidget(frame, index // 4, index % 4)
            self.values[key] = value
        outer.addLayout(metrics)
        buttons = QHBoxLayout()
        self.start_button = QPushButton("Démarrer la simulation")
        self.start_button.setObjectName("primary")
        self.pause_button = QPushButton("Pause")
        self.stop_button = QPushButton("Arrêter")
        self.stop_button.setObjectName("danger")
        self.start_button.clicked.connect(self.start_clicked)
        self.pause_button.clicked.connect(self.pause_clicked)
        self.stop_button.clicked.connect(self.stop_clicked)
        for button in (self.start_button, self.pause_button, self.stop_button):
            buttons.addWidget(button)
        buttons.addStretch()
        outer.addLayout(buttons)
        frame, box = card()
        box.addWidget(QLabel("Console des événements"))
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.document().setMaximumBlockCount(100)
        box.addWidget(self.console)
        outer.addWidget(frame, 1)
        self.setLayout(outer)
        self.set_status("Arrêté")

    def set_status(self, status: str) -> None:
        self.values["status"].setText(status)
        self.start_button.setEnabled(status in ("Arrêté", "Terminé", "Erreur"))
        self.pause_button.setEnabled(status in ("En cours", "En pause"))
        self.pause_button.setText("Reprendre" if status == "En pause" else "Pause")
        self.stop_button.setEnabled(status in ("En cours", "En pause"))

    def set_statistics(self, statistics: dict[str, int]) -> None:
        for key in ("combats", "victories", "defeats", "xp", "kamas"):
            self.values[key].setText(str(statistics[key]))

    def append_log(self, message: str) -> None:
        self.console.append(message)


class ObservationPreview(QLabel):
    image_clicked = Signal(object)
    image_hovered = Signal(object)

    def __init__(self) -> None:
        super().__init__("Aucune observation")
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(560, 390)
        self.setStyleSheet("border: 1px solid rgba(143,209,79,36); background: #0f1510;")
        self._image_size: tuple[int, int] | None = None

    def set_image(self, image) -> None:
        self._image_size = (image.shape[1], image.shape[0])
        pixmap = bgr_to_pixmap(image)
        self.setPixmap(pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.pixmap() is not None and self._image_size:
            # L'image suivante rétablira la résolution source; éviter d'agrandir un aperçu déjà réduit.
            self.setPixmap(self.pixmap().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation))
        super().resizeEvent(event)

    def _image_point(self, event) -> tuple[int, int] | None:
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull() or self._image_size is None:
            return None
        left = (self.width() - pixmap.width()) / 2
        top = (self.height() - pixmap.height()) / 2
        px, py = event.position().x() - left, event.position().y() - top
        if 0 <= px < pixmap.width() and 0 <= py < pixmap.height():
            width, height = self._image_size
            return round(px * width / pixmap.width()), round(py * height / pixmap.height())
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        point = self._image_point(event)
        if point is not None:
            self.image_clicked.emit(point)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API ; survol = inspection seule
        point = self._image_point(event)
        if point is not None:
            self.image_hovered.emit(point)


class CombatPage(QWidget):
    observation_start_requested = Signal()
    observation_stop_requested = Signal()
    observation_save_requested = Signal()
    player_reference_requested = Signal(object)
    map_load_requested = Signal(str)
    map_auto_requested = Signal()
    projection_calibration_requested = Signal()
    overlay_options_changed = Signal(object)
    legacy_fallback_changed = Signal(bool)
    grid_recipe_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        outer = title("Combat", "Simulation ou observation visuelle en lecture seule")
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode"))
        self.mode = QComboBox()
        self.mode.addItems(("Simulation", "Vision réelle"))
        mode_row.addWidget(self.mode)
        mode_row.addStretch()
        outer.addLayout(mode_row)
        self.views = QStackedWidget()

        simulation = QWidget()
        content = QHBoxLayout()
        frame, grid_box = card()
        self.grid = GridWidget()
        grid_box.addWidget(self.grid)
        content.addWidget(frame, 3)
        details, details_box = card()
        self.state = QLabel("IDLE")
        self.turn = QLabel("0")
        self.player = QLabel("—")
        self.enemies = QLabel("—")
        self.ap = QLabel("0")
        self.mp = QLabel("0")
        self.target = QLabel("—")
        self.spell = QLabel("—")
        form = QFormLayout()
        for label, widget in (
            ("État", self.state), ("Tour", self.turn), ("Joueur", self.player),
            ("Ennemis", self.enemies), ("PA", self.ap), ("PM", self.mp),
            ("Cible", self.target), ("Sort", self.spell),
        ):
            widget.setWordWrap(True)
            form.addRow(label, widget)
        details_box.addLayout(form)
        details_box.addWidget(QLabel("Historique des actions"))
        self.history = QTextEdit()
        self.history.setReadOnly(True)
        details_box.addWidget(self.history, 1)
        content.addWidget(details, 2)
        simulation.setLayout(content)
        self.views.addWidget(simulation)

        real = QWidget()
        real_layout = QVBoxLayout(real)
        controls = QHBoxLayout()
        self.observation_start = QPushButton("Démarrer l'observation")
        self.observation_start.setObjectName("primary")
        self.observation_stop = QPushButton("Arrêter l'observation")
        self.observation_stop.setObjectName("danger")
        self.observation_stop.setEnabled(False)
        self.observation_save = QPushButton("Enregistrer cette observation")
        self.observation_save.setEnabled(False)
        self.select_player = QPushButton("Voici mon personnage")
        self.select_player.setCheckable(True)
        self.select_player.setEnabled(False)
        for button in (self.observation_start, self.observation_stop,
                       self.observation_save, self.select_player):
            controls.addWidget(button)
        controls.addStretch()
        real_layout.addLayout(controls)
        map_row = QHBoxLayout()
        map_row.addWidget(QLabel("Map : détection automatique · secours :"))
        self.map_id_input = QLineEdit()
        self.map_id_input.setPlaceholderText("mapId manuel (facultatif)")
        self.map_id_input.setMaximumWidth(170)
        self.map_load = QPushButton("Utiliser ce mapId manuellement")
        self.map_auto = QPushButton("Revenir à la détection automatique")
        self.map_auto.setVisible(False)
        self.map_auto.clicked.connect(self.map_auto_requested)
        self.map_load.clicked.connect(lambda: self.map_load_requested.emit(self.map_id_input.text()))
        self.map_id_input.returnPressed.connect(self.map_load.click)
        self.map_id_verified = QCheckBox("vérifié par /mapid")
        self.map_id_verified.setToolTip("Cochez seulement si l'ID vient de la commande /mapid tapée dans le client")
        self.map_status = QLabel("La map est détectée automatiquement pendant l'observation (coordonnées affichées "
                                 "en haut à gauche du jeu + GameData). Le mapId manuel ne sert qu'en secours.")
        self.map_status.setWordWrap(True)
        self.projection_calibrate = QPushButton("Calibrer projection de grille")
        self.projection_calibrate.clicked.connect(self.projection_calibration_requested)
        self.grid_recipe = QPushButton("Recette de grille…")
        self.grid_recipe.setToolTip("Capture figée + mesures + jugement d'alignement (lecture seule)")
        self.grid_recipe.clicked.connect(self.grid_recipe_requested)
        self.legacy_fallback = QCheckBox("Autoriser la grille historique en secours")
        self.legacy_fallback.toggled.connect(self.legacy_fallback_changed)
        for widget in (self.map_id_input, self.map_id_verified, self.map_load, self.map_auto, self.projection_calibrate,
                       self.grid_recipe, self.legacy_fallback):
            map_row.addWidget(widget)
        map_row.addStretch()
        real_layout.addLayout(map_row)
        real_layout.addWidget(self.map_status)
        overlay_row = QHBoxLayout()
        overlay_row.addWidget(QLabel("Overlay :"))
        self.overlay_boxes: dict[str, QCheckBox] = {}
        for key, caption, checked in (
            ("grid", "grille projetée", True), ("cell_ids", "cell IDs", False),
            ("coordinates", "coordonnées logiques", False), ("walkability", "walkability", False),
            ("los", "LOS", False), ("red_blue", "indices rouge/bleu GameData", False),
            ("alignment_debug", "diagnostic alignement", False),
        ):
            box = QCheckBox(caption)
            box.setChecked(checked)
            box.toggled.connect(self._overlay_changed)
            overlay_row.addWidget(box)
            self.overlay_boxes[key] = box
        overlay_row.addStretch()
        real_layout.addLayout(overlay_row)
        entity_row = QHBoxLayout()
        entity_row.addWidget(QLabel("Entités :"))
        for key, caption, checked in (
            ("entity_rois", "ROIs entités", False), ("player_evidence", "preuves joueur", False),
            ("enemy_evidence", "preuves ennemis", False), ("track_ids", "IDs de piste", True),
            ("occluded_tracks", "pistes occultées", True), ("background_delta", "fond (FREE)", False),
            ("occupancy_states", "états d'occupation", False),
        ):
            box = QCheckBox(caption)
            box.setChecked(checked)
            box.toggled.connect(self._overlay_changed)
            entity_row.addWidget(box)
            self.overlay_boxes[key] = box
        self.sequence_capture = QCheckBox("Enregistrer la séquence dans le corpus (entités, lecture seule)")
        entity_row.addWidget(self.sequence_capture)
        # LOT 3B-5D : le split appartient au combat entier, déclaré avant la capture.
        self.sequence_split = QComboBox()
        for caption, value in (("Split : non déclaré", None), ("Split : TRAIN", "train"),
                               ("Split : VALIDATION", "validation"), ("Split : TEST", "test")):
            self.sequence_split.addItem(caption, value)
        self.sequence_split.setToolTip("Combat entier. Fixé au démarrage de l'observation, non modifiable ensuite.")
        entity_row.addWidget(self.sequence_split)
        entity_row.addStretch()
        real_layout.addLayout(entity_row)
        self.hover_info = QLabel("Survolez l'aperçu pour inspecter une cellule projetée.")
        self.hover_info.setWordWrap(True)
        real_layout.addWidget(self.hover_info)
        self.observation_help = QLabel(
            "Lecture seule. Pour identifier le joueur, activez « Voici mon personnage » puis cliquez "
            "sur son marqueur dans l'aperçu DofBot2."
        )
        self.observation_help.setWordWrap(True)
        real_layout.addWidget(self.observation_help)
        # Pourquoi « Inconnu » ? Une ligne par condition, avec l'action à faire.
        self.recording_status: tuple[str, str] | None = None
        self.checklist = QLabel()
        self.checklist.setWordWrap(True)
        self.checklist.setTextFormat(Qt.TextFormat.RichText)
        self.checklist.setStyleSheet("background:#121813; border:1px solid rgba(255,255,255,20); border-radius:10px; padding:8px 12px;")
        self.checklist.setVisible(False)
        real_layout.addWidget(self.checklist)
        real_content = QHBoxLayout()
        preview_frame, preview_box = card()
        self.observation_preview = ObservationPreview()
        preview_box.addWidget(self.observation_preview)
        real_content.addWidget(preview_frame, 4)
        status_frame, status_box = card()
        self.real_values: dict[str, QLabel] = {}
        real_form = QFormLayout()
        for key, caption in (
            ("map", "Map"), ("map_coords", "Coordonnées"), ("map_source", "Détection"),
            ("combat", "Combat"), ("turn", "Mon tour"), ("ap", "PA"), ("mp", "PM"),
            ("player", "Ma cellule"), ("enemies", "Ennemis détectés"), ("occupancy", "Occupation"),
            ("grid", "Cellules de grille"), ("grid_source", "Source de grille"),
            ("grid_visible", "Grille"), ("alignment", "Alignement"), ("declared_map", "Map déclarée"),
            ("map_consistency", "Cohérence map"), ("runtime_adjustment", "Correction runtime"),
            ("quality", "Qualité observation"),
            ("safe", "Sûre pour décision"), ("performance", "Durée analyse"),
        ):
            value = QLabel("Inconnu")
            value.setWordWrap(True)
            real_form.addRow(caption, value)
            self.real_values[key] = value
        status_box.addLayout(real_form)
        status_box.addWidget(QLabel("Signaux visuels"))
        self.real_signals = QTextEdit()
        self.real_signals.setReadOnly(True)
        self.real_signals.setMaximumHeight(140)
        status_box.addWidget(self.real_signals)
        real_content.addWidget(status_frame, 2)
        real_layout.addLayout(real_content, 1)
        self.views.addWidget(real)
        outer.addWidget(self.views, 1)
        self.setLayout(outer)
        self.mode.currentIndexChanged.connect(self.views.setCurrentIndex)
        self.observation_start.clicked.connect(self.observation_start_requested)
        self.observation_stop.clicked.connect(self.observation_stop_requested)
        self.observation_save.clicked.connect(self.observation_save_requested)
        self.observation_preview.image_clicked.connect(self._preview_clicked)
        self.observation_preview.image_hovered.connect(self._preview_hovered)
        self._last_packet: ObservationPacket | None = None

    def overlay_options(self) -> OverlayOptions:
        return OverlayOptions(**{key: box.isChecked() for key, box in self.overlay_boxes.items()})

    def _overlay_changed(self) -> None:
        self.overlay_options_changed.emit(self.overlay_options())

    def _show_validation(self, grid) -> None:
        """LOT 3B-3 : résumé lisible ; les chiffres restent dans « Signaux visuels »."""
        visibility = {"VISIBLE": "Visible", "NOT_VISIBLE": "Non visible", "UNKNOWN": "Incertaine"}
        alignment = {"ALIGNED": "OK", "DEGRADED": "Dégradé", "MISALIGNED": "Recalibration nécessaire",
                     "INSUFFICIENT_EVIDENCE": "Preuves insuffisantes"}
        consistency = {"CONSISTENT": "OK", "SUSPECT": "Suspecte", "STALE_LIKELY": "Suspecte (persistante)",
                       "UNKNOWN": "Inconnue"}
        drift = grid.drift or {}
        status = (grid.alignment or {}).get("status")
        if drift.get("state") == "RECALIBRATION_REQUIRED":
            status = "MISALIGNED"
        self.real_values["grid_visible"].setText(visibility.get(grid.grid_visibility_state, "—"))
        self.real_values["alignment"].setText(alignment.get(status, "—"))
        if grid.map_id_declared is None:
            declared = "—"
        else:
            verified = "vérifiée par /mapid" if grid.map_id_source == "user_verified_mapid" else "non vérifiée"
            declared = f"{grid.map_id_declared} — {verified}"
        self.real_values["declared_map"].setText(declared)
        state = grid.map_declaration_state
        text = consistency.get(state, "—")
        if state in ("SUSPECT", "STALE_LIKELY"):
            text += " — Le map ID déclaré semble ne plus correspondre à la grille observée."
        self.real_values["map_consistency"].setText(text)
        adjustment = drift.get("runtime_adjustment")
        if adjustment and any(adjustment.get(k) for k in ("dx", "dy", "scale")):
            self.real_values["runtime_adjustment"].setText(
                f"dx={adjustment['dx']:.1f} px, dy={adjustment['dy']:.1f} px, échelle={adjustment['scale']:+.3%} "
                f"(runtime, profil inchangé)")
        else:
            self.real_values["runtime_adjustment"].setText("aucune")
        details = []
        for label, payload in (("grille", grid.grid_visibility), ("alignement", grid.alignment),
                               ("map", grid.map_declaration)):
            if payload:
                details.append(f"{label} : " + " ; ".join(payload.get("reasons", ())))
        if drift:
            details.append(f"dérive : {drift.get('state')}")
        self._validation_details = "\n".join(details)

    def set_declared_map(self, text: str) -> None:
        self.map_status.setText(text)

    def _show_map_resolution(self, resolution: dict | None, grid) -> None:
        """LOT 3B-6C : bloc Map (ID, coordonnées, source, confiance, GameData, projection)."""
        if not resolution:
            self.real_values["map"].setText("Détection indisponible (index GameData non chargé)")
            self.real_values["map_coords"].setText("—")
            self.real_values["map_source"].setText("—")
            return
        status = resolution.get("status")
        coords = resolution.get("coordinates")
        self.real_values["map_coords"].setText(f"[{coords[0]},{coords[1]}]" if coords else "—")
        labels = {"UNKNOWN": "Détection de la map…", "TRANSITION": "Changement de map en cours…",
                  "STALE": "Lecture des coordonnées trop ancienne", "INCONSISTENT": "Lecture incohérente avec GameData"}
        if status == "RESOLVED":
            aligned = (grid.alignment or {}).get("status") == "ALIGNED"
            loaded = grid.grid_source == GRID_SOURCE_GAMEDATA and grid.map_id_declared == resolution.get("map_id")
            text = (f"{resolution.get('map_id')} — GameData {'OK' if loaded else 'en chargement'} — "
                    f"projection {'ALIGNÉE' if aligned else 'à vérifier'}")
            if loaded and not aligned and grid.grid_visibility_state == "VISIBLE":
                text += " (Map détectée automatiquement, mais projection de grille à vérifier.)"
        elif status == "AMBIGUOUS":
            candidates = resolution.get("candidates") or []
            text = (f"AMBIGUË : {resolution.get('candidate_count')} candidates ({', '.join(map(str, candidates[:4]))}"
                    f"{'…' if len(candidates) > 4 else ''}) — saisissez le mapId en secours si besoin")
        else:
            text = labels.get(status, str(status))
            if resolution.get("map_id") is not None:
                text += f" (suivi conservé sur {resolution.get('map_id')})"
        self.real_values["map"].setText(text)
        manual = resolution.get("source") == "MANUAL"
        self.map_auto.setVisible(manual)
        source = "Manuelle (secours)" if manual else ("Automatique — " + (resolution.get("source") or "—"))
        self.real_values["map_source"].setText(f"{source} · confiance {float(resolution.get('confidence') or 0):.1%}"
                                               f" · {resolution.get('reason') or ''}")

    def _preview_hovered(self, point: tuple[int, int]) -> None:
        packet = self._last_packet
        if packet is None:
            return
        grid = packet.observation.grid
        if grid.grid_source != GRID_SOURCE_GAMEDATA:
            self.hover_info.setText(f"Pixel {point} — grille {grid.grid_source} : pas de cell ID GameData")
            return
        cell_id = grid.pixel_to_cell_id(point)
        cell = grid.cell_by_id(cell_id) if cell_id is not None else None
        if cell is None:
            self.hover_info.setText(f"Pixel {point} — hors des 560 cellules projetées")
            return
        flag = lambda value: "inconnu" if value is None else ("oui" if value else "non")  # noqa: E731
        coordinate = cell.grid_coordinate
        self.hover_info.setText(
            f"Cellule {cell.cell_id} — logique ({coordinate.x}, {coordinate.y}) — "
            f"traversable statique : {flag(cell.static_traversable)} — LOS statique : {flag(cell.los_static)} — "
            f"état visuel : {cell.state.value} ({cell.confidence:.0%}) — "
            f"indices rouge/bleu : {flag(cell.red_hint)}/{flag(cell.blue_hint)} (non validés)"
        )

    def _preview_clicked(self, point: tuple[int, int]) -> None:
        if self.select_player.isChecked():
            self.player_reference_requested.emit(point)
            self.select_player.setChecked(False)

    def set_observing(self, active: bool) -> None:
        self.observation_start.setEnabled(not active)
        self.observation_stop.setEnabled(active)
        self.sequence_split.setEnabled(not active)
        self.select_player.setEnabled(active and self.observation_save.isEnabled())
        if not active:
            self.observation_help.setText("Observation arrêtée. Aucun clic n'a été envoyé au client DOFUS.")

    def set_observation(self, packet: ObservationPacket) -> None:
        observation = packet.observation
        self._last_packet = packet
        self.observation_preview.set_image(packet.annotated)
        unknown = lambda value: "Inconnu" if value is None else str(value)
        self.real_values["combat"].setText(
            f"{'Oui' if observation.combat_detected else 'Non'} ({observation.combat_confidence:.0%})"
        )
        turn = "Inconnu" if observation.player_turn is None else ("Oui" if observation.player_turn else "Non")
        if packet.metadata.get("combat_state_model") is False:
            turn = "Inconnu — modèle phase/tour absent (à installer)"
        self.real_values["turn"].setText(f"{turn} ({observation.turn_confidence:.0%})")
        def hud_text(value, confidence, evidence) -> str:
            if not evidence:
                return f"{unknown(value)} ({confidence:.0%})"
            source = {"GLYPH_TEMPLATE": "Glyphes", "RAPIDOCR": "RapidOCR",
                      "CONSENSUS": "Consensus", "UNKNOWN": "Inconnu"}.get(
                          str(evidence.get("source")), str(evidence.get("source", "Inconnu")))
            reason = str(evidence.get("reason", ""))
            margin = float(evidence.get("margin", 0.0))
            ocr, ocr_confidence, _reason = _hud_reading(evidence)
            if value is None and reason == "NO_TEMPLATE" and ocr is not None:
                return f"Inconnu — l'OCR propose {ocr} ({ocr_confidence:.0%}), non validé"
            return f"{unknown(value)} ({confidence:.0%}) · {source} · marge {margin:.3f} · {reason}"

        self.real_values["ap"].setText(hud_text(observation.ap, observation.confidence_ap,
                                                observation.ap_read))
        self.real_values["mp"].setText(hud_text(observation.mp, observation.confidence_mp,
                                                observation.mp_read))
        player = observation.player_cell
        if observation.entities is not None:
            # LOT 3B-5 : identités par DofusCellId ; une piste occultée n'est jamais « observée ».
            track = observation.player_track or {}
            if observation.player_cell_id is not None and track.get("state") == "HELD":
                player_text = f"Cell {observation.player_cell_id} — maintenu (masqué) — {observation.player_confidence:.2f}"
            elif observation.player_cell_id is not None:
                player_text = f"Cell {observation.player_cell_id} — confirmé — {observation.player_confidence:.2f}"
            elif track.get("state") == "OCCLUDED":
                player_text = f"Occulté (dernière cellule {track.get('last_known_cell_id')})"
            else:
                player_text = "Inconnu (désignez votre personnage si aucun profil n'existe)"
            self.real_values["player"].setText(player_text)
            lines = []
            for enemy in observation.enemies:
                label = "E?" if enemy.track_state == "AMBIGUOUS" else "E" + enemy.id.rsplit("_", 1)[-1]
                lines.append(f"{label} : Cell {enemy.cell_id} — observé — {enemy.confidence:.2f}"
                             if enemy.observed_this_frame else
                             f"{label} : Cell {enemy.cell_id} — maintenu (masqué) — {enemy.confidence:.2f}"
                             if enemy.track_state == "HELD" else
                             f"{label} : occulté (dernière cellule {enemy.cell_id})")
            unknown = observation.unknown_entities
            if unknown:
                lines.append(f"Équipe inconnue : {', '.join(str(item.get('cell_id')) for item in unknown)}")
            self.real_values["enemies"].setText("\n".join(lines) or "Aucun détecté")
            summary = observation.occupancy_summary or {}
            self.real_values["occupancy"].setText(
                f"occupées {summary.get('OCCUPIED', 0)} · libres prouvées {summary.get('FREE', 0)} · "
                f"inconnues {summary.get('UNKNOWN', 0) + summary.get('NOT_ANALYSED', 0)}")
        else:
            self.real_values["player"].setText(
                f"({player.x}, {player.y}) ({observation.player_confidence:.0%})" if player else "Inconnue"
            )
            self.real_values["enemies"].setText("\n".join(
                f"{enemy.id}: ({enemy.cell.x}, {enemy.cell.y}) — {enemy.confidence:.0%}"
                for enemy in observation.enemies
            ) or "Aucun détecté")
            self.real_values["occupancy"].setText("Ancien pipeline (grille historique)")
        self.real_values["grid"].setText(
            f"{len(observation.grid.cells)} ({observation.grid.confidence:.0%})"
        )
        grid = observation.grid
        source = grid.grid_source
        if source == GRID_SOURCE_GAMEDATA:
            origin = "détectée automatiquement" if grid.map_id_source == "auto_detected" else "déclarée manuellement"
            source += f" — map {grid.map_id_declared} ({origin}), {grid.projection_status}"
        else:
            source += f" — {packet.metadata.get('grid_source_reason', '')}"
        if packet.metadata.get("requires_recalibration"):
            source += " — recalibration de projection requise"
        self.real_values["grid_source"].setText(source)
        self._show_validation(grid)
        self._show_map_resolution(packet.metadata.get("map_resolution"), grid)
        self.real_values["quality"].setText(f"{observation.observation_confidence:.0%}")
        self.real_values["safe"].setText("Oui" if observation.safe_for_decision else "Non")
        self.real_values["performance"].setText(f"{packet.elapsed_ms:.0f} ms")
        self.real_signals.setPlainText("\n".join(
            f"{name}: {score:.0%}" for name, score in observation.signals.items()
        ) + (f"\nÉtat combat : {observation.combat_state}" if observation.combat_state else "")
          + (f"\nHUD PA : {observation.ap_read}" if observation.ap_read else "")
          + (f"\nHUD PM : {observation.mp_read}" if observation.mp_read else "")
          + ("\n" + getattr(self, "_validation_details", "") if getattr(self, "_validation_details", "") else ""))
        self.observation_save.setEnabled(True)
        self.select_player.setEnabled(self.observation_stop.isEnabled())
        self.observation_help.setText(
            "Observation active en lecture seule. Les données insuffisantes restent inconnues."
        )
        self.show_checklist(collection_checklist(packet.metadata, observation, self.recording_status))

    def show_checklist(self, items: list[tuple[str, str]]) -> None:
        icons = {"ok": ("✓", "#b6e68a"), "warn": ("!", "#e5a13a"), "todo": ("✗", "#f59aa5")}
        rows = [f"<span style='color:{icons[state][1]}; font-weight:700'>{icons[state][0]}</span>&nbsp; {text}"
                for state, text in items]
        self.checklist.setText("<br>".join(rows))
        self.checklist.setVisible(bool(rows))

    def set_snapshot(self, snap: CombatSnapshot) -> None:
        self.grid.set_snapshot(snap)
        self.state.setText(snap.state.value)
        self.turn.setText(str(snap.turn))
        self.player.setText(f"{snap.player.name} : ({snap.player.cell.x}, {snap.player.cell.y}), {snap.player.hp}/{snap.player.max_hp} PV")
        self.enemies.setText("\n".join(
            f"{e.name} : ({e.cell.x}, {e.cell.y}), {e.hp} PV" for e in snap.enemies
        ))
        self.ap.setText(str(snap.ap))
        self.mp.setText(str(snap.mp))
        self.target.setText(snap.selected_target or "—")
        self.spell.setText(snap.selected_spell or "—")
        self.history.setPlainText("\n".join(snap.history[-30:]))
        self.history.moveCursor(self.history.textCursor().MoveOperation.End)


class SpellsPage(QWidget):
    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage
        self.selected_id: int | None = None
        outer = title("Sorts", "Valeurs configurables pour la simulation ; aucun sort DOFUS n'est prédéfini")
        content = QHBoxLayout()
        left, left_box = card()
        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self._on_selection)
        left_box.addWidget(self.list_widget)
        self.new_button = QPushButton("Nouveau sort")
        self.new_button.clicked.connect(self._new)
        left_box.addWidget(self.new_button)
        content.addWidget(left, 1)
        right, right_box = card()
        form = QFormLayout()
        self.name = QLineEdit()
        self.ap_cost = self._spin(1, 20, 3)
        self.min_range = self._spin(0, 30, 1)
        self.max_range = self._spin(0, 30, 4)
        self.modifiable_range = QCheckBox()
        self.line_cast = QCheckBox()
        self.line_of_sight = QCheckBox()
        self.line_of_sight.setChecked(True)
        self.per_turn = self._spin(1, 20, 2)
        self.per_target = self._spin(1, 20, 2)
        self.priority = self._spin(-100, 100, 10)
        self.damage = self._spin(0, 100, 6)
        for label, widget in (
            ("Nom", self.name), ("Coût PA", self.ap_cost),
            ("Portée min.", self.min_range), ("Portée max.", self.max_range),
            ("Portée modifiable", self.modifiable_range), ("Lancer en ligne", self.line_cast),
            ("Ligne de vue", self.line_of_sight), ("Lancers par tour", self.per_turn),
            ("Lancers par cible", self.per_target), ("Priorité", self.priority),
            ("Dégâts simulés", self.damage),
        ):
            form.addRow(label, widget)
        right_box.addLayout(form)
        actions = QHBoxLayout()
        save = QPushButton("Enregistrer")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        delete = QPushButton("Supprimer")
        delete.setObjectName("danger")
        delete.clicked.connect(self._delete)
        actions.addWidget(save)
        actions.addWidget(delete)
        right_box.addLayout(actions)
        content.addWidget(right, 2)
        tabs = QTabWidget()
        manual = QWidget()
        manual.setLayout(content)
        self.scan_panel = ScanPanel(storage)
        tabs.addTab(manual, "Sorts simulés")
        tabs.addTab(self.scan_panel, "Scanner le client")
        outer.addWidget(tabs, 1)
        self.setLayout(outer)
        self.reload()

    @staticmethod
    def _spin(minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def reload(self, select_id: int | None = None) -> None:
        self.list_widget.clear()
        spells = self.storage.list_spells()
        for spell in spells:
            item = QListWidgetItem(f"{spell.name}  •  {spell.ap_cost} PA  •  priorité {spell.priority}")
            item.setData(Qt.ItemDataRole.UserRole, spell.id)
            self.list_widget.addItem(item)
        for row in range(self.list_widget.count()):
            if self.list_widget.item(row).data(Qt.ItemDataRole.UserRole) == select_id:
                self.list_widget.setCurrentRow(row)
                return
        if spells:
            self.list_widget.setCurrentRow(0)
        else:
            self._new()

    def _on_selection(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        spell_id = current.data(Qt.ItemDataRole.UserRole)
        spell = next((s for s in self.storage.list_spells() if s.id == spell_id), None)
        if spell is None:
            return
        self.selected_id = spell.id
        self.name.setText(spell.name)
        for widget, value in (
            (self.ap_cost, spell.ap_cost), (self.min_range, spell.min_range),
            (self.max_range, spell.max_range), (self.per_turn, spell.per_turn),
            (self.per_target, spell.per_target), (self.priority, spell.priority),
            (self.damage, spell.damage),
        ):
            widget.setValue(value)
        self.modifiable_range.setChecked(spell.modifiable_range)
        self.line_cast.setChecked(spell.line_cast)
        self.line_of_sight.setChecked(spell.line_of_sight)

    def _new(self) -> None:
        self.list_widget.clearSelection()
        self.selected_id = None
        self.name.clear()
        self.ap_cost.setValue(3)
        self.min_range.setValue(1)
        self.max_range.setValue(4)
        self.modifiable_range.setChecked(False)
        self.line_cast.setChecked(False)
        self.line_of_sight.setChecked(True)
        self.per_turn.setValue(2)
        self.per_target.setValue(2)
        self.priority.setValue(10)
        self.damage.setValue(6)
        self.name.setFocus()

    def _save(self) -> None:
        spell = Spell(
            id=self.selected_id, name=self.name.text().strip(), ap_cost=self.ap_cost.value(),
            min_range=self.min_range.value(), max_range=self.max_range.value(),
            modifiable_range=self.modifiable_range.isChecked(), line_cast=self.line_cast.isChecked(),
            line_of_sight=self.line_of_sight.isChecked(), per_turn=self.per_turn.value(),
            per_target=self.per_target.value(), priority=self.priority.value(), damage=self.damage.value(),
        )
        try:
            spell_id = self.storage.save_spell(spell)
        except (ValueError, sqlite3.IntegrityError) as exc:
            QMessageBox.warning(self, "Sort invalide", str(exc))
            return
        self.reload(spell_id)

    def _delete(self) -> None:
        if self.selected_id is not None:
            self.storage.delete_spell(self.selected_id)
            self.reload()


class StrategiesPage(QWidget):
    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage
        outer = title("Stratégies", "Paramètres utilisés au prochain démarrage de la simulation")
        frame, box = card()
        form = QFormLayout()
        self.mode = QComboBox()
        for mode in StrategyMode:
            self.mode.addItem(mode.value.capitalize(), mode.value)
        self.threshold = QSpinBox()
        self.threshold.setRange(0, 100)
        self.threshold.setSuffix(" %")
        self.priority = QComboBox()
        for priority in TargetPriority:
            self.priority.addItem(priority.value.capitalize(), priority.value)
        self.reserve_ap = QSpinBox()
        self.reserve_ap.setRange(0, 20)
        for label, widget in (
            ("Approche", self.mode), ("Seuil de PV (survie)", self.threshold),
            ("Priorité des cibles", self.priority), ("PA à réserver", self.reserve_ap),
        ):
            form.addRow(label, widget)
        box.addLayout(form)
        save = QPushButton("Enregistrer la stratégie")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        box.addWidget(save)
        outer.addWidget(frame)
        outer.addStretch()
        self.setLayout(outer)
        strategy = storage.load_strategy()
        self.mode.setCurrentIndex(self.mode.findData(strategy.mode.value))
        self.threshold.setValue(strategy.hp_threshold)
        self.priority.setCurrentIndex(self.priority.findData(strategy.target_priority.value))
        self.reserve_ap.setValue(strategy.reserve_ap)

    def _save(self) -> None:
        try:
            self.storage.save_strategy(Strategy(
                mode=StrategyMode(self.mode.currentData()), hp_threshold=self.threshold.value(),
                target_priority=TargetPriority(self.priority.currentData()),
                reserve_ap=self.reserve_ap.value(),
            ))
        except ValueError as exc:
            QMessageBox.warning(self, "Stratégie invalide", str(exc))
            return
        QMessageBox.information(self, "Stratégie", "Stratégie enregistrée pour le prochain combat.")


class StatisticsPage(QWidget):
    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage
        outer = title("Statistiques", "Résultats des combats simulés enregistrés dans SQLite")
        self.summary = QLabel()
        self.summary.setObjectName("metric")
        outer.addWidget(self.summary)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Date UTC", "Résultat", "Tours", "XP", "Kamas"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        outer.addWidget(self.table, 1)
        self.setLayout(outer)
        self.refresh()

    def refresh(self) -> None:
        stats = self.storage.statistics()
        self.summary.setText(
            f"{stats['combats']} combats  ·  {stats['victories']} victoires  ·  "
            f"{stats['defeats']} défaites  ·  {stats['xp']} XP  ·  {stats['kamas']} kamas"
        )
        rows = self.storage.recent_combats()
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, key in enumerate(("ended_at", "outcome", "turns", "xp", "kamas")):
                self.table.setItem(row_index, column, QTableWidgetItem(str(row[key])))
        self.table.resizeColumnsToContents()


class LogsPage(QWidget):
    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage
        outer = title("Logs", "Événements structurés conservés dans SQLite")
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        outer.addWidget(self.text, 1)
        self.setLayout(outer)
        self.refresh()

    def refresh(self) -> None:
        lines = [
            f"{row['created_at']}  [{row['level']}] {row['event']}  {row['message']}  {row['context_json']}"
            for row in self.storage.recent_events()
        ]
        self.text.setPlainText("\n".join(lines))
        self.text.moveCursor(self.text.textCursor().MoveOperation.End)

    def append_event(self, timestamp: str, level: str, event: str, message: str, context: str) -> None:
        self.text.append(f"{timestamp}  [{level}] {event}  {message}  {context}")


class SettingsPage(QWidget):
    def __init__(self, storage: Storage, jobs: JobRunner | None = None) -> None:
        super().__init__()
        self.storage = storage
        outer = title("Paramètres", "Connexion au jeu, données du client et simulation")
        frame, box = card()
        form = QFormLayout()
        self.player_name = QLineEdit(str(storage.get_setting("player_name") or "Personnage test"))
        self.tick_ms = QSpinBox()
        self.tick_ms.setRange(50, 5000)
        self.tick_ms.setSuffix(" ms")
        self.tick_ms.setValue(int(storage.get_setting("tick_ms") or 350))
        form.addRow("Personnage", self.player_name)
        form.addRow("Délai entre étapes", self.tick_ms)
        box.addLayout(form)
        save = QPushButton("Enregistrer les paramètres")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        box.addWidget(save)
        simulation = QWidget()
        simulation_layout = QVBoxLayout(simulation)
        simulation_layout.addWidget(frame)
        simulation_layout.addStretch()
        self.client_panel = ClientPanel(storage)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.client_panel)
        self.gamedata_panel = GameDataPanel(storage, jobs)
        self.client_tabs = QTabWidget()
        self.client_tabs.addTab(scroll, "Connexion au jeu")
        self.client_tabs.addTab(self.gamedata_panel, "Données du client")
        self.client_tabs.addTab(simulation, "Simulation")
        outer.addWidget(self.client_tabs, 1)
        self.setLayout(outer)

    def _save(self) -> None:
        name = self.player_name.text().strip()
        if not name:
            QMessageBox.warning(self, "Paramètre invalide", "Le nom du personnage est requis.")
            return
        self.storage.set_setting("player_name", name)
        self.storage.set_setting("tick_ms", self.tick_ms.value())
        QMessageBox.information(self, "Paramètres", "Paramètres enregistrés pour le prochain combat.")
