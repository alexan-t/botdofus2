"""Composition MVC : vues Qt, contrôleur de simulation et dépôt SQLite."""

from __future__ import annotations

from datetime import datetime
from dataclasses import replace
import json
import time

from PySide6.QtCore import QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from combatbot.engine import CombatEngine
from combatbot.models import CombatEvent, CombatSnapshot
from combatbot.simulation import SimulationController
from combatbot.storage import Storage
from combatbot.ui.pages import (
    CombatPage, DashboardPage, LogsPage, SettingsPage,
    SpellsPage, StatisticsPage, StrategiesPage,
)
from combatbot.ui.jobs import JobRunner
from combatbot.ui.calibration_dialog import CalibrationDialog, ImageCropDialog
from combatbot.ui.corpus_page import CorpusPage
from combatbot.vision.capture import capture_client
from combatbot.vision.window import inspect_dofus_window
from combatbot.vision.window import window_dpi
from combatbot.vision.connection import connect_window
from combatbot.vision.autocalibration import ZoneSuggestion, suggest_zones, zones_needing_review
from combatbot.vision.icons import infer_grid_shape, scan_spell_bar
from combatbot.vision.tooltip import locate_new_tooltip, recognize_tooltip
from combatbot.vision.character import recognize_profile
from combatbot.vision.models import CapturedFrame, ConnectionResult, ZoneEvidence
from combatbot.vision.combat_models import GridCalibration, ObservationPacket
from combatbot.vision.coordinates import CombatPoint
from combatbot.vision.combat_observer import RealCombatObserver, save_debug_observation
from combatbot.runtime import app_data_root
from combatbot.ui.grid_projection_dialog import GridProjectionDialog
from combatbot.ui.grid_recipe_dialog import GridRecipeDialog
from combatbot.vision.grid_recipe import CAPTURE_KINDS, RealGridValidationSession
from combatbot.vision.coordinates import LayoutSignature
from combatbot.vision.gamedata_grid import GameDataGridResolver, GameDataTopologySource
from combatbot.vision.grid_profile import PROFILE_SETTING_KEY, CombatGridProfileV2, ManualMapIdentity, MapIdSource

DECLARED_MAP_SETTING = "declared_map_id"


class MainWindow(QMainWindow):
    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage
        self.controller = SimulationController()
        self.jobs = JobRunner()
        self._pending_capture = False
        self._recorded = False
        self._closing = False
        self._status = "Arrêté"
        self._connection_suggestions: dict[str, ZoneSuggestion] = {}
        self._observation_pending = False
        self._observer: RealCombatObserver | None = None
        self._last_observation: ObservationPacket | None = None
        # LOT 3B-2 : map déclarée manuellement, jamais détectée.
        self._map_identity = ManualMapIdentity()
        self._topology_source: GameDataTopologySource | None = None
        self._topology_folder: str | None = None
        self._declared_topology = None
        self._allow_legacy_fallback = False
        self._fullscreen_restore_maximized = False
        self._fullscreen_restore_geometry: QRect | None = None
        self.setWindowTitle("PythonBot • Simulation et observation")
        self.resize(1270, 780)
        self.setMinimumSize(1050, 650)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(205)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(12, 20, 12, 16)
        brand = QLabel("PYTHONBOT")
        brand.setStyleSheet("font-size: 17px; font-weight: 800; color: #65d6b5; padding: 8px;")
        nav.addWidget(brand)
        caption = QLabel("COMBAT • SIMULATION")
        caption.setObjectName("subtitle")
        nav.addWidget(caption)
        nav.addSpacing(22)

        self.stack = QStackedWidget()
        self.dashboard = DashboardPage()
        self.combat = CombatPage()
        self.spells = SpellsPage(storage)
        self.strategies = StrategiesPage(storage)
        self.statistics = StatisticsPage(storage)
        self.logs = LogsPage(storage)
        self.settings = SettingsPage(storage, self.jobs)
        self.corpus = CorpusPage()
        self.pages = [
            self.dashboard, self.combat, self.spells, self.strategies,
            self.statistics, self.logs, self.settings, self.corpus,
        ]
        labels = ["Tableau de bord", "Combat", "Sorts", "Stratégies", "Statistiques", "Logs", "Paramètres",
                  "Corpus / Annotation"]
        self.nav_buttons: list[QPushButton] = []
        for index, (name, page) in enumerate(zip(labels, self.pages)):
            self.stack.addWidget(page)
            button = QPushButton(name)
            button.setObjectName("nav")
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, index=index: self._navigate(index))
            nav.addWidget(button)
            self.nav_buttons.append(button)
        nav.addStretch()
        self.fullscreen_button = QPushButton("Plein écran  ·  F11")
        self.fullscreen_button.setObjectName("nav")
        self.fullscreen_button.clicked.connect(self._toggle_fullscreen)
        nav.addWidget(self.fullscreen_button)
        footer = QLabel("LOT 3B-2  ·  Grille GameData, observation sans action")
        footer.setWordWrap(True)
        footer.setObjectName("subtitle")
        nav.addWidget(footer)
        layout.addWidget(sidebar)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(22, 18, 22, 20)
        content_layout.addWidget(self.stack)
        layout.addWidget(content, 1)
        self.setCentralWidget(root)
        self.fullscreen_shortcut = QShortcut(QKeySequence(Qt.Key.Key_F11), self)
        self.fullscreen_shortcut.activated.connect(self._toggle_fullscreen)
        self.exit_fullscreen_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.exit_fullscreen_shortcut.activated.connect(self._leave_fullscreen)

        self.dashboard.start_clicked.connect(self._start)
        self.dashboard.pause_clicked.connect(self._toggle_pause)
        self.dashboard.stop_clicked.connect(self.controller.stop)
        self.controller.snapshot_ready.connect(self._on_snapshot)
        self.controller.event_ready.connect(self._on_event)
        self.controller.status_ready.connect(self._on_status)
        self.controller.finished.connect(self._on_finished)
        self.controller.failed.connect(self._on_failed)
        self.controller.ready.connect(self._on_ready)
        self.jobs.all_done.connect(self._on_ready)
        self.client_panel = self.settings.client_panel
        self.scan_panel = self.spells.scan_panel
        self.client_panel.profile_changed.connect(self.scan_panel.set_profile)
        self.client_panel.profile_changed.connect(lambda _profile_id: self._connection_suggestions.clear())
        self.client_panel.profile_changed.connect(lambda _profile_id: self._stop_observation())
        self.client_panel.connect_requested.connect(self._connect_client)
        self.client_panel.disconnect_requested.connect(self._disconnect_client)
        self.client_panel.capture_confirmed.connect(self._check_connection)
        self.client_panel.diagnostic.connect(
            lambda message: self._on_event(CombatEvent("INFO", "vision.connection", message))
        )
        self.client_panel.capture_requested.connect(self._capture_preview)
        self.client_panel.calibrate_requested.connect(self._calibrate)
        self.client_panel.recognize_requested.connect(self._recognize_character)
        self.combat.observation_start_requested.connect(self._start_observation)
        self.combat.observation_stop_requested.connect(self._stop_observation)
        self.combat.observation_save_requested.connect(self._save_observation)
        self.combat.player_reference_requested.connect(self._set_player_reference)
        self.combat.map_load_requested.connect(self._load_declared_map)
        self.combat.projection_calibration_requested.connect(self._calibrate_projection)
        self.combat.overlay_options_changed.connect(self._set_overlay_options)
        self.combat.legacy_fallback_changed.connect(self._set_legacy_fallback)
        self.corpus.hud_collection_requested.connect(self._open_hud_collection)
        self._hud_collection_dialog = None
        self.combat.grid_recipe_requested.connect(self._grid_recipe)
        self.client_panel.profile_changed.connect(self._restore_declared_map_input)
        self.combat.mode.currentTextChanged.connect(
            lambda mode: self._stop_observation() if mode == "Simulation" else None
        )
        self.scan_panel.scan_requested.connect(self._scan_spells)
        self.scan_panel.tooltip_requested.connect(self._scan_tooltip)
        if self.client_panel.profile_id is not None:
            self.scan_panel.set_profile(self.client_panel.profile_id)
        self.connection_timer = QTimer(self)
        self.connection_timer.setInterval(2000)
        self.connection_timer.timeout.connect(self._check_connection)
        self.connection_timer.start()
        self.observation_timer = QTimer(self)
        self.observation_timer.setInterval(400)
        self.observation_timer.timeout.connect(self._observe_once)
        self._navigate(0)
        self._refresh_statistics()
        self.dashboard.values["character"].setText(str(storage.get_setting("player_name")))
        self.combat.set_snapshot(CombatEngine(storage.list_spells(), storage.load_strategy()).snapshot())

    def _toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self._leave_fullscreen()
            return
        self._fullscreen_restore_maximized = self.isMaximized()
        self._fullscreen_restore_geometry = self.normalGeometry()
        self.showFullScreen()

    def _leave_fullscreen(self) -> None:
        if not self.isFullScreen():
            return
        if self._fullscreen_restore_maximized:
            self.showMaximized()
        else:
            self.showNormal()
            if self._fullscreen_restore_geometry is not None:
                self.setGeometry(self._fullscreen_restore_geometry)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "fullscreen_button"):
            self.fullscreen_button.setText(
                "Quitter le plein écran  ·  F11" if self.isFullScreen() else "Plein écran  ·  F11"
            )

    def _navigate(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for position, button in enumerate(self.nav_buttons):
            button.setChecked(position == index)
        if index == 4:
            self.statistics.refresh()
        elif index == 5:
            self.logs.refresh()

    def _start(self) -> None:
        try:
            spells = self.storage.list_spells()
            if not spells:
                raise ValueError("Configurez au moins un sort simulé dans l'écran Sorts")
            strategy = self.storage.load_strategy()
            name = str(self.storage.get_setting("player_name") or "Personnage test")
            tick_ms = int(self.storage.get_setting("tick_ms") or 350)
            engine = CombatEngine(spells, strategy, name)
            self.controller.start(engine, tick_ms)
            self._recorded = False
            self._on_status("Démarrage")
            self.dashboard.values["character"].setText(name)
            self.combat.set_snapshot(engine.snapshot())
        except Exception as exc:
            QMessageBox.warning(self, "Démarrage impossible", str(exc))

    def _toggle_pause(self) -> None:
        if self._status == "En pause":
            self.controller.resume()
        elif self._status == "En cours":
            self.controller.pause()

    def _on_status(self, status: str) -> None:
        self._status = status
        self.dashboard.set_status(status)

    def _on_snapshot(self, snapshot: CombatSnapshot) -> None:
        self.combat.set_snapshot(snapshot)

    def _on_event(self, event: CombatEvent) -> None:
        try:
            self.storage.record_event(event)
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.dashboard.append_log(f"{timestamp}  [{event.level}] {event.message}")
            self.logs.append_event(timestamp, event.level, event.event, event.message,
                                   json.dumps(event.context, ensure_ascii=False))
        except Exception as exc:
            self.dashboard.append_log(f"Erreur de journalisation : {exc}")

    def _on_finished(self, snapshot: CombatSnapshot) -> None:
        if snapshot.outcome is not None and not self._recorded:
            try:
                self.storage.record_combat(snapshot.outcome, snapshot.turn, snapshot.xp, snapshot.kamas)
                self._recorded = True
                self._refresh_statistics()
            except Exception as exc:
                self.dashboard.append_log(f"Erreur de statistiques : {exc}")
        if self._status != "Erreur":
            self._on_status("Terminé" if snapshot.outcome else "Arrêté")
        self.dashboard.start_button.setEnabled(False)

    def _on_failed(self, message: str) -> None:
        self.dashboard.append_log(f"Erreur de simulation : {message}")
        self._on_status("Erreur")

    def _refresh_statistics(self) -> None:
        self.dashboard.set_statistics(self.storage.statistics())
        self.statistics.refresh()

    def _on_ready(self) -> None:
        if self._closing and self.controller.thread is None and not self.jobs.active and not self._pending_capture:
            self.close()
        elif not self._closing:
            self.dashboard.set_status(self._status)

    def _connect_client(self, hwnd: int) -> None:
        profile_id = self.client_panel.profile_id
        if profile_id is None:
            return
        if self._pending_capture or self.jobs.active:
            self.client_panel.show_error("Une opération de capture est déjà en cours")
            return
        self._pending_capture = True
        self.client_panel.connection_status.setText("Vérification de la fenêtre et de la capture…")
        known = self.storage.known_icons(profile_id)
        self.hide()

        def success(result: ConnectionResult) -> None:
            self._pending_capture = False
            self.show()
            self.raise_()
            if self.client_panel.profile_id != profile_id or self.client_panel.windows.currentData() != hwnd:
                self.client_panel.show_error("Le profil ou la fenêtre a changé pendant la vérification")
                return
            self.client_panel.set_connection_result(result)
            if result.success:
                rectangles = result.details.get("zone_rects", {})
                confidences = result.details.get("zone_confidence", {})
                self._connection_suggestions = {
                    name: ZoneSuggestion(tuple(rect), ZoneEvidence(float(confidences.get(name, 0)),
                                                                   "vérification de capture", "proposée"))
                    for name, rect in rectangles.items()
                }
                try:
                    profile = self.storage.get_profile(profile_id)
                    self.storage.save_profile(replace(profile, window_hwnd=hwnd))
                    self._check_connection()
                except (RuntimeError, ValueError) as exc:
                    self.client_panel.show_error(f"Capture validée, profil non enregistré : {exc}")
            else:
                profile = self.storage.get_profile(profile_id)
                if profile.window_hwnd is not None:
                    self.storage.save_profile(replace(profile, window_hwnd=None))

        def failure(message: str) -> None:
            self._pending_capture = False
            self.show()
            self.raise_()
            self._disconnect_client()
            self.client_panel.set_connection_result(
                ConnectionResult("capture", False, "UNEXPECTED_ERROR", message, {"hwnd": hwnd})
            )

        QTimer.singleShot(150, lambda: self.jobs.submit(lambda: connect_window(hwnd, known), success, failure))

    def _disconnect_client(self) -> None:
        self._stop_observation()
        profile_id = self.client_panel.profile_id
        self._connection_suggestions.clear()
        self.client_panel.set_disconnected()
        if profile_id is not None:
            try:
                profile = self.storage.get_profile(profile_id)
                if profile.window_hwnd is not None:
                    self.storage.save_profile(replace(profile, window_hwnd=None))
            except (RuntimeError, ValueError) as exc:
                self.client_panel.show_error(f"Déconnexion non enregistrée : {exc}")
        self.client_panel.diagnostic.emit("Fenêtre détachée ; vérification requise avant reconnexion")

    def _check_connection(self) -> None:
        hwnd = self.client_panel.connected_hwnd
        profile_id = self.client_panel.profile_id
        if hwnd is None or profile_id is None:
            return
        try:
            info, geometry = inspect_dofus_window(hwnd)
        except RuntimeError as exc:
            self._disconnect_client()
            self.client_panel.show_error(f"Connexion perdue : {exc}")
            return
        self.client_panel.window_title.setText(info.title)
        self.client_panel.client_size.setText(f"{geometry.width} × {geometry.height}")
        calibration = self.storage.load_calibration(profile_id)
        detected = len(self._connection_suggestions) > 1
        if calibration is None:
            self.client_panel.calibration_status.setText("Calibration absente")
            self.client_panel.progress.setValue(3 if detected else 2)
        else:
            compatibility = calibration.layout_compatibility(geometry.width, geometry.height)
        if calibration is not None and compatibility.compatible:
            frame = self.client_panel.frame
            review = zones_needing_review(calibration, frame, self._connection_suggestions) if frame else set()
            if max(abs(geometry.width / calibration.client_width - 1),
                   abs(geometry.height / calibration.client_height - 1)) > 0.01:
                review.update(calibration.zones)
            confirmed_zones = {name for name in calibration.zones
                               if calibration.zone_meta.get(name, ZoneEvidence(1, "ancienne calibration", "confirmée")).status
                               == "confirmée"}
            if review:
                self.client_panel.calibration_status.setText(
                    "Zones à revalider : " + ", ".join(sorted(review))
                )
            else:
                self.client_panel.calibration_status.setText(
                    f"Calibration compatible : {len(confirmed_zones)}/{len(calibration.zones)} zone(s) confirmée(s)"
                )
            if not self.client_panel.content_confirmed or review or not confirmed_zones:
                self.client_panel.progress.setValue(3 if detected else 2)
            elif {"combat", "spell_bar", "hp", "ap", "mp"}.issubset(confirmed_zones):
                self.client_panel.progress.setValue(5)
            else:
                self.client_panel.progress.setValue(4)
        elif calibration is not None:
            self.client_panel.calibration_status.setText(
                "Calibration invalidée : " + compatibility.reason.value + " ; recalibrez"
            )
            self.client_panel.progress.setValue(2)

    def _run_capture(self, on_success, process=None, delay_ms: int = 450) -> None:
        if self._pending_capture or self.jobs.active:
            self._vision_error("Une autre opération de capture ou de reconnaissance est déjà en cours.")
            return
        hwnd = self.client_panel.connected_hwnd
        profile_id = self.client_panel.profile_id
        if hwnd is None:
            self._vision_error("Connectez d'abord une fenêtre DOFUS dans Paramètres.")
            return
        self._pending_capture = True
        self.hide()  # Évite que la fenêtre PythonBot recouvre la zone capturée.

        def work():
            frame = capture_client(hwnd)
            return frame, process(frame) if process is not None else None

        def success(value) -> None:
            self._pending_capture = False
            frame, result = value
            self.show()
            self.raise_()
            if self.client_panel.profile_id != profile_id or self.client_panel.connected_hwnd != hwnd:
                self.client_panel.diagnostic.emit("Résultat de capture ignoré : profil ou fenêtre modifié")
                return
            try:
                self.client_panel.set_frame(frame)
                on_success(frame, result)
            except Exception as exc:
                self._vision_error(str(exc))

        def failure(message: str) -> None:
            self._pending_capture = False
            self.show()
            self.raise_()
            self._vision_error(message)

        QTimer.singleShot(delay_ms, lambda: self.jobs.submit(work, success, failure))

    def _vision_error(self, message: str) -> None:
        self.client_panel.diagnostic.emit(f"[VISION] {message}")
        QMessageBox.warning(self, "Observation DOFUS", message)

    def _capture_preview(self) -> None:
        hwnd = self.client_panel.windows.currentData()
        if hwnd is None:
            self.client_panel.show_error("Sélectionnez une fenêtre avant de tester la capture")
            return
        self._connect_client(int(hwnd))

    def _calibrate(self) -> None:
        profile_id = self.client_panel.profile_id
        if profile_id is None:
            self._vision_error("Sélectionnez un profil avant de calibrer.")
            return

        def show_dialog(frame: CapturedFrame, suggestions) -> None:
            existing = self.storage.load_calibration(profile_id)
            dialog = CalibrationDialog(frame, profile_id, existing, self, suggestions)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                try:
                    calibration = dialog.calibration()
                    self.storage.save_calibration(calibration)
                    confirmed = sum(item.status == "confirmée" for item in calibration.zone_meta.values())
                    if "spell_bar" in calibration.zones and calibration.zone_meta["spell_bar"].status == "confirmée":
                        grid = infer_grid_shape(calibration.crop(frame, "spell_bar"))
                        if grid is not None:
                            self.scan_panel.set_grid_suggestion(*grid)
                    self.client_panel.diagnostic.emit(
                        f"Calibration enregistrée : {confirmed} zone(s) confirmée(s), "
                        f"{len(calibration.zones) - confirmed} à vérifier"
                    )
                    self._check_connection()
                except ValueError as exc:
                    self._vision_error(str(exc))

        self._run_capture(show_dialog, suggest_zones)

    def _scan_spells(self, page: int, columns: int, rows: int, threshold: float) -> None:
        profile_id = self.client_panel.profile_id
        if profile_id is None:
            self._vision_error("Sélectionnez un profil avant de scanner.")
            return
        calibration = self.storage.load_calibration(profile_id)
        if calibration is None:
            self._vision_error("Calibrez la barre de sorts dans Paramètres avant le scan.")
            return
        known = self.storage.known_icons(profile_id)

        def process(frame: CapturedFrame):
            bar = calibration.crop(frame, "spell_bar")
            return scan_spell_bar(bar, page=page, columns=columns, rows=rows,
                                  known_icons=known, match_threshold=threshold)

        def deliver(_frame: CapturedFrame, result) -> None:
            if self.client_panel.profile_id != profile_id:
                raise ValueError("Le profil a changé pendant le scan ; résultat non enregistré")
            self.scan_panel.accept_scan(result)

        self._run_capture(deliver, process)

    def _scan_tooltip(self, spell_id: int) -> None:
        hwnd = self.client_panel.connected_hwnd
        profile_id = self.client_panel.profile_id
        if hwnd is None or profile_id is None:
            self._vision_error("Connectez et vérifiez d'abord la capture du client.")
            return
        if self._pending_capture or self.jobs.active:
            self._vision_error("Une autre opération visuelle est en cours.")
            return
        self.scan_panel.summary.setText("Survolez le sort dans DOFUS : capture avant/après dans 3 secondes…")
        self._pending_capture = True
        self.hide()

        def work():
            before = capture_client(hwnd)
            time.sleep(3)
            after = capture_client(hwnd, activate=False)
            rect = locate_new_tooltip(before.image, after.image)
            if rect is None:
                return after, None, None, "Panneau non localisé automatiquement"
            x, y, width, height = rect
            try:
                recognized = recognize_tooltip(after.image[y:y + height, x:x + width].copy())
            except ValueError as exc:
                return after, rect, None, f"OCR incertain : {exc}"
            return after, rect, recognized, "Infobulle localisée automatiquement"

        def success(value) -> None:
            self._pending_capture = False
            self.show()
            self.raise_()
            if self.client_panel.profile_id != profile_id or self.client_panel.connected_hwnd != hwnd:
                self.scan_panel.summary.setText("Profil ou fenêtre changé pendant la lecture ; résultat ignoré")
                return
            frame, rect, recognized, message = value
            self.client_panel.set_frame(frame)
            self.scan_panel.summary.setText(message)
            if recognized is not None:
                self.scan_panel.accept_ocr(spell_id, recognized)
                return
            dialog = ImageCropDialog(frame, self)
            if rect is not None:
                dialog.item.setPos(rect[0], rect[1])
                dialog.item.setRect(0, 0, rect[2], rect[3])
            if dialog.exec() == QDialog.DialogCode.Accepted:
                crop = dialog.crop()
                self.jobs.submit(lambda: recognize_tooltip(crop),
                                 lambda found: self.scan_panel.accept_ocr(spell_id, found),
                                 self._vision_error)

        def failure(message: str) -> None:
            self._pending_capture = False
            self.show()
            self.raise_()
            self.scan_panel.summary.setText(f"Échec de lecture : {message}")
            self.client_panel.diagnostic.emit(f"[INFOBULLE] {message}")

        QTimer.singleShot(150, lambda: self.jobs.submit(work, success, failure))

    def _recognize_character(self) -> None:
        profile_id = self.client_panel.profile_id
        if profile_id is None:
            self._vision_error("Sélectionnez un profil.")
            return
        calibration = self.storage.load_calibration(profile_id)
        if calibration is None:
            self._vision_error("Calibrez les zones PV, PA et PM avant la lecture du profil.")
            return
        self._run_capture(
            lambda _frame, result: self.client_panel.set_recognition(result),
            lambda frame: recognize_profile(frame, calibration),
        )

    def _load_grid_calibration(self, profile_id: int) -> GridCalibration | None:
        raw = self.storage.get_profile_setting(profile_id, "combat_grid", None)
        if not isinstance(raw, dict):
            return None
        try:
            return GridCalibration.from_dict(raw)
        except (KeyError, TypeError, ValueError, IndexError):
            self._on_event(CombatEvent("WARNING", "vision.grid", "Calibration de grille ignorée : données invalides"))
            return None

    def _load_grid_profile(self, profile_id: int) -> CombatGridProfileV2 | None:
        raw = self.storage.get_profile_setting(profile_id, PROFILE_SETTING_KEY, None)
        if not isinstance(raw, dict):
            return None
        try:
            return CombatGridProfileV2.from_dict(raw)
        except (KeyError, TypeError, ValueError, IndexError):
            self._on_event(CombatEvent("WARNING", "vision.grid", "Profil de projection ignoré : données invalides"))
            return None

    def _restore_declared_map_input(self, profile_id) -> None:
        if profile_id is None:
            return
        value = self.storage.get_profile_setting(profile_id, DECLARED_MAP_SETTING, None)
        # Pré-remplissage seulement : la map n'est déclarée qu'après « Charger ».
        if isinstance(value, int) and not self.combat.map_id_input.text():
            self.combat.map_id_input.setText(str(value))

    def _load_declared_map(self, text: str) -> None:
        try:
            map_id = int(text.strip())
            if map_id < 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Map ID", "Map ID : entier positif ou nul attendu")
            return
        folder = str(self.storage.get_setting("dofus_client_directory") or "").strip()
        if not folder:
            QMessageBox.warning(self, "Map ID", "Configurez d'abord le dossier du client dans "
                                "Paramètres → Données du client.")
            return
        if self.jobs.active:
            self.combat.set_declared_map("Une autre opération est en cours ; réessayez dans un instant.")
            return
        source = self._topology_source if self._topology_folder == folder else None
        self.combat.map_load.setEnabled(False)
        self.combat.set_declared_map(f"Chargement de la topologie GameData de la map {map_id}…")

        def work():
            active = source or GameDataTopologySource.for_client(folder, app_data_root() / "data" / "gamedata" / "cache")
            return active, active.topology(map_id)

        def success(value) -> None:
            self.combat.map_load.setEnabled(True)
            active, topology = value
            self._topology_source, self._topology_folder = active, folder
            self._declared_topology = topology
            source = (MapIdSource.USER_VERIFIED_MAPID if self.combat.map_id_verified.isChecked()
                      else MapIdSource.MANUAL_GUESS)
            declared = self._map_identity.declare(map_id, source)
            cells = topology.cells
            traversable = sum(bool(c.walkable) and not c.non_walkable_during_fight for c in cells)
            blocked_los = sum(c.line_of_sight is False for c in cells)
            red, blue = sum(bool(c.red_hint) for c in cells), sum(bool(c.blue_hint) for c in cells)
            self.combat.set_declared_map(
                f"{declared.label} — {len(cells)} cellules GameData, {traversable} traversables en combat, "
                f"{blocked_los} bloquant la LOS, indices rouge/bleu {red}/{blue} (non validés). "
                "PythonBot ne sait pas si la map change dans le jeu : redéclarez-la."
            )
            profile_id = self.client_panel.profile_id
            if profile_id is not None:
                self.storage.set_profile_setting(profile_id, DECLARED_MAP_SETTING, map_id)
                profile = self._load_grid_profile(profile_id)
                if profile is not None:
                    self.storage.set_profile_setting(profile_id, PROFILE_SETTING_KEY, profile.with_map(map_id).to_dict())
            observer = self._observer
            if observer is not None and observer.grid_resolver is not None:
                observer.grid_resolver.topology_source = active
            self._on_event(CombatEvent("INFO", "vision.map", f"Map {map_id} déclarée manuellement"))

        def failure(message: str) -> None:
            self.combat.map_load.setEnabled(True)
            self._map_identity.declare(None)
            self._declared_topology = None
            self.combat.set_declared_map(f"Map {map_id} non chargée : {message}")
            QMessageBox.warning(self, "Map ID", f"Map {map_id} inconnue ou illisible : {message}")

        self.jobs.submit(work, success, failure)

    def _calibrate_projection(self) -> None:
        profile_id = self.client_panel.profile_id
        if profile_id is None or self.client_panel.connected_hwnd is None:
            self._vision_error("Connectez d'abord une fenêtre DOFUS dans Paramètres.")
            return
        calibration = self.storage.load_calibration(profile_id)
        if calibration is None or "combat" not in calibration.zones:
            self._vision_error("Calibrez et confirmez la zone de combat dans Paramètres.")
            return

        def show_dialog(frame: CapturedFrame, _result) -> None:
            combat_image = calibration.crop(frame, "combat")
            signature = LayoutSignature.create(
                frame.client.size, {name: rect.to_normalized_rect() for name, rect in calibration.zones.items()},
            ).to_json()
            declared = self._map_identity.current_map()
            dialog = GridProjectionDialog(
                combat_image, layout_signature=signature, topology=self._declared_topology,
                map_id=declared.map_id if declared else None, current=self._load_grid_profile(profile_id), parent=self,
            )
            if dialog.exec() and dialog.result_profile is not None:
                self.storage.set_profile_setting(profile_id, PROFILE_SETTING_KEY, dialog.result_profile.to_dict())
                observer = self._observer
                if observer is not None and observer.grid_resolver is not None:
                    observer.grid_resolver.profile = dialog.result_profile
                self._on_event(CombatEvent("INFO", "vision.grid", "Projection de grille GameData confirmée"))

        self._run_capture(show_dialog)

    def _recipe_session(self, frame: CapturedFrame, calibration) -> RealGridValidationSession:
        """One session per day under data/validation/grid-real (never versioned)."""
        root = app_data_root() / "data" / "validation" / "grid-real"
        session_id = "grid-real-" + datetime.now().strftime("%Y%m%d")
        if (root / session_id / "session.json").exists():
            return RealGridValidationSession.load(root, session_id)
        signature = LayoutSignature.create(
            frame.client.size, {name: rect.to_normalized_rect() for name, rect in calibration.zones.items()})
        return RealGridValidationSession.create(root, {
            "client_size": frame.client.size.to_dict(), "dpi": window_dpi(frame.hwnd),
            "layout_signature": signature.to_dict(), "layout_digest": signature.digest,
            "profile_id": self.client_panel.profile_id}, session_id)

    def _grid_recipe(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        profile_id = self.client_panel.profile_id
        declared = self._map_identity.current_map()
        if profile_id is None or self.client_panel.connected_hwnd is None:
            self._vision_error("Connectez d'abord une fenêtre DOFUS dans Paramètres.")
            return
        profile = self._load_grid_profile(profile_id)
        calibration = self.storage.load_calibration(profile_id)
        if declared is None or self._declared_topology is None or profile is None or calibration is None:
            self._vision_error("Recette : chargez un map ID (/mapid) et confirmez la projection de grille d'abord.")
            return
        items = [f"{key} — {label}" for key, label in CAPTURE_KINDS.items()]
        choice, ok = QInputDialog.getItem(self, "Recette de grille", "Type de capture", items, 0, False)
        if not ok:
            return
        mode, ok = QInputDialog.getItem(self, "Recette de grille", "Mode affiché",
                                        ["exploration", "placement", "combat"], 0, False)
        if not ok:
            return
        kind = choice.split(" ")[0]
        topology = self._declared_topology

        def show(frame: CapturedFrame, _result) -> None:
            session = self._recipe_session(frame, calibration)
            transform = profile.transform.to_dict()
            transform_id = next((t["transform_id"] for t in session.transforms if t["transform"] == transform), None)
            if transform_id is None:
                transform_id = session.add_transform(profile.transform, "profil combat_grid_v2",
                                                     method=profile.calibration_method)
            session.declare_map(declared.map_id, declared.source.value)
            record = session.add_capture(
                map_id=declared.map_id, map_id_source=declared.source.value, kind=kind,
                transform_id=transform_id, frame=frame.image, combat_image=calibration.crop(frame, "combat"),
                topology=topology, red_blue=kind == "D", context={"mode": mode, "client": frame.client.to_dict()})
            GridRecipeDialog(session, record["capture_id"], topology, self).exec()
            status, reasons = session.map_status(session.map_record(declared.map_id))
            self._on_event(CombatEvent("INFO", "vision.recipe",
                                       f"Recette {record['capture_id']} : map {declared.map_id} {status} {reasons}"))

        self._run_capture(show, delay_ms=0)

    def _set_overlay_options(self, options) -> None:
        if self._observer is not None:
            self._observer.overlay_options = options

    def _set_legacy_fallback(self, allowed: bool) -> None:
        self._allow_legacy_fallback = bool(allowed)
        if self._observer is not None and self._observer.grid_resolver is not None:
            self._observer.grid_resolver.allow_legacy_fallback = bool(allowed)

    def _open_hud_collection(self) -> None:
        """HUD Real Collection : capture PA/PM en lecture seule, aucune action n'est envoyée."""
        from combatbot.corpus.hud_collection import capture_metadata, extract_hud_crops
        from combatbot.ui.hud_review_dialog import HUDCollectionDialog

        hwnd = self.client_panel.connected_hwnd
        profile_id = self.client_panel.profile_id
        if hwnd is None or profile_id is None or not self.client_panel.content_confirmed:
            QMessageBox.warning(self, "Collecte HUD", "Connectez et confirmez d'abord une fenêtre DOFUS dans Paramètres.")
            return
        calibration = self.storage.load_calibration(profile_id)
        if calibration is None or not {"combat", "ap", "mp"} <= set(calibration.zones):
            QMessageBox.warning(self, "Collecte HUD", "Calibrez les zones combat, PA et PM dans Paramètres.")
            return
        if self._hud_collection_dialog is not None and self._hud_collection_dialog.isVisible():
            self._hud_collection_dialog.raise_()
            return

        def grab():
            frame = capture_client(hwnd, activate=False)
            if frame.hwnd != hwnd:
                raise RuntimeError("La capture ne correspond plus à la fenêtre connectée")
            if not calibration.compatible(frame.client.width, frame.client.height):
                raise RuntimeError("Calibration incompatible avec la taille actuelle du client")
            ap, mp, transform = extract_hud_crops(frame, calibration)
            return frame.image, ap, mp, capture_metadata(frame, calibration, transform)

        dialog = HUDCollectionDialog(self.corpus.repository, grab, self)
        dialog.collection_changed.connect(self.corpus.refresh)
        dialog.finished.connect(lambda _result: self.corpus.refresh())
        self._hud_collection_dialog = dialog
        self._on_event(CombatEvent("INFO", "vision.hud_collection",
                                   f"Collecte HUD en lecture seule : {dialog.session.session_id}"))
        dialog.show()

    def _start_observation(self) -> None:
        hwnd = self.client_panel.connected_hwnd
        profile_id = self.client_panel.profile_id
        if hwnd is None or profile_id is None:
            QMessageBox.warning(self, "Vision réelle", "Connectez d'abord une fenêtre DOFUS dans Paramètres.")
            return
        if not self.client_panel.content_confirmed:
            QMessageBox.warning(self, "Vision réelle", "Confirmez d'abord que l'aperçu montre bien le client DOFUS.")
            return
        calibration = self.storage.load_calibration(profile_id)
        if calibration is None or "combat" not in calibration.zones:
            QMessageBox.warning(self, "Vision réelle", "Calibrez et confirmez la zone de combat dans Paramètres.")
            return
        layout_status = calibration.layout_compatibility(
            self.client_panel.frame.client.width, self.client_panel.frame.client.height
        ) if self.client_panel.frame is not None else None
        if layout_status is None or layout_status.requires_revalidation:
            QMessageBox.warning(
                self, "Vision réelle",
                "Cette calibration utilise une ancienne signature ou une disposition modifiée. "
                "Ouvrez la calibration et confirmez de nouveau les zones.",
            )
            return
        evidence = calibration.zone_meta.get("combat")
        if evidence is not None and evidence.status != "confirmée":
            QMessageBox.warning(self, "Vision réelle", "La zone de combat doit être confirmée dans la calibration.")
            return
        legacy_grid = self._load_grid_calibration(profile_id)
        resolver = GameDataGridResolver(
            profile=self._load_grid_profile(profile_id), topology_source=self._topology_source,
            map_identity=self._map_identity, legacy_calibration=legacy_grid,
            allow_legacy_fallback=self._allow_legacy_fallback,
        )
        self._observer = RealCombatObserver(
            hwnd, calibration, grid_calibration=legacy_grid, grid_resolver=resolver,
            overlay_options=self.combat.overlay_options(),
            capture_context={
                "profile": self.storage.get_profile(profile_id).label,
                "dpi": window_dpi(hwnd),
                "window_id": f"dofus-{hwnd:x}",
                "tactical_mode": "inconnu",
            },
        )
        self._last_observation = None
        self.combat.mode.setCurrentText("Vision réelle")
        self.combat.set_observing(True)
        self.observation_timer.start()
        self._on_event(CombatEvent("INFO", "vision.observation", "Session d'observation réelle démarrée sans action"))
        self._observe_once()

    def _stop_observation(self) -> None:
        was_active = self.observation_timer.isActive() or self._observer is not None
        self.observation_timer.stop()
        self._observer = None
        self.combat.set_observing(False)
        if was_active:
            self._on_event(CombatEvent("INFO", "vision.observation", "Session d'observation arrêtée"))

    def _observe_once(self) -> None:
        observer = self._observer
        if observer is None or self._observation_pending or self.jobs.active:
            return
        self._observation_pending = True

        def success(value: object) -> None:
            self._observation_pending = False
            if observer is not self._observer or not isinstance(value, ObservationPacket):
                return
            self._last_observation = value
            self.combat.set_observation(value)

        def failure(message: str) -> None:
            self._observation_pending = False
            if observer is not self._observer:
                return
            self._stop_observation()
            self._on_event(CombatEvent("ERROR", "vision.observation", f"Observation interrompue : {message}"))
            QMessageBox.warning(self, "Vision réelle", f"Observation interrompue : {message}")

        self.jobs.submit(observer.observe, success, failure)

    def _save_observation(self) -> None:
        packet = self._last_observation
        if packet is None:
            return

        def saved(value: object) -> None:
            self._on_event(CombatEvent("INFO", "vision.debug", f"Observation enregistrée : {value}"))
            self.combat.observation_help.setText(f"Observation enregistrée : {value}")

        self.jobs.submit(lambda: save_debug_observation(packet), saved,
                         lambda message: QMessageBox.warning(self, "Enregistrement", message))

    def _set_player_reference(self, point: tuple[int, int]) -> None:
        observer, packet = self._observer, self._last_observation
        profile_id = self.client_panel.profile_id
        if observer is None or packet is None or profile_id is None:
            return
        if self._observation_pending:
            self.combat.observation_help.setText("Une analyse est en cours ; recliquez sur le personnage après sa mise à jour.")
            return
        if not observer.set_player_reference(CombatPoint(*point), packet):
            self.combat.observation_help.setText("Signature insuffisante à cet endroit ; cliquez sur un marqueur coloré du personnage.")
            return
        calibration = observer.grid_calibration
        assert calibration is not None
        self.storage.set_profile_setting(profile_id, "combat_grid", calibration.to_dict())
        self.combat.observation_help.setText("Signature du joueur enregistrée pour ce profil.")
        self._on_event(CombatEvent("INFO", "vision.player", "Signature visuelle du joueur confirmée manuellement"))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.observation_timer.stop()
        self._observer = None
        if self.controller.thread is not None or self.jobs.active or self._pending_capture:
            self._closing = True
            if self.controller.thread is not None:
                self.controller.stop()
            event.ignore()
            return
        self.storage.close()
        super().closeEvent(event)
