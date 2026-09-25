"""Diagnostic de données locales, indépendant de la capture et de la grille."""
from collections import Counter
import json
from pathlib import Path
import sqlite3

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QVBoxLayout, QWidget,
)

from combatbot.gamedata import LocalGameDataProvider
from combatbot.gamedata.validation import MapValidator, geometry_selfcheck, topology_decision
from combatbot.runtime import app_data_root
from combatbot.ui.jobs import JobRunner


EMPTY_SUMMARY = ("Dossier : —\nClient : non déterminé\nD2P : —\nD2O : —\nMaps candidates : —\n"
                 "Maps lisibles : non validées\nVersions DLM : —\nMaps chiffrées : —\nCellules : —\n"
                 "Topologie logique vérifiée : non\nFight cells vérifiées : non")


class GameDataPanel(QWidget):
    def __init__(self, storage, jobs: JobRunner | None = None):
        super().__init__()
        self.storage = storage
        self.jobs = jobs or JobRunner()
        self.provider: LocalGameDataProvider | None = None
        self.loaded_map: int | None = None
        self.validator: MapValidator | None = None
        self.validation: dict | None = None
        self.busy = False
        self.output_directory = app_data_root() / "data" / "gamedata"
        layout = QVBoxLayout(self)
        notice = QLabel("Analyse locale en lecture seule. La grille de combat actuelle reste indépendante.")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        row = QHBoxLayout()
        row.addWidget(QLabel("Dossier client :"))
        self.folder = QLineEdit(str(storage.get_setting("dofus_client_directory") or ""))
        self.folder.editingFinished.connect(self._save_folder)
        self.folder.textChanged.connect(self._folder_changed)
        self.browse_button = QPushButton("Parcourir…")
        self.browse_button.clicked.connect(self._browse)
        self.analyze_button = QPushButton("Analyser")
        self.analyze_button.clicked.connect(self._analyze)
        for widget in (self.folder, self.browse_button, self.analyze_button):
            row.addWidget(widget)
        layout.addLayout(row)
        self.status = QLabel("Dossier du client DOFUS non configuré" if not self.folder.text() else "Dossier configuré — analyse à lancer")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.summary = QLabel(EMPTY_SUMMARY)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        validate_row = QHBoxLayout()
        self.validate_button = QPushButton("Valider toutes les maps")
        self.validate_button.setToolTip("Lecture seule de chaque map indexée, en tâche de fond")
        self.validate_button.clicked.connect(self._validate)
        self.details_button = QPushButton("Détails")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(lambda shown: self.details.setVisible(shown))
        self.validation_status = QLabel("")
        for widget in (self.validate_button, self.details_button):
            validate_row.addWidget(widget)
        validate_row.addWidget(self.validation_status, 1)
        layout.addLayout(validate_row)
        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(250)
        self.progress_timer.timeout.connect(self._show_progress)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(140)
        self.details.setVisible(False)
        layout.addWidget(self.details, 1)
        map_row = QHBoxLayout()
        map_row.addWidget(QLabel("Map ID :"))
        self.map_id = QLineEdit()
        self.map_id.setPlaceholderText("Identifiant à inspecter manuellement")
        self.load_button = QPushButton("Charger")
        self.load_button.clicked.connect(self._load_map)
        self.export_button = QPushButton("Exporter JSON")
        self.export_button.clicked.connect(self._export_map)
        for widget in (self.map_id, self.load_button, self.export_button):
            map_row.addWidget(widget)
        layout.addLayout(map_row)
        self.map_summary = QLabel("Aucune map chargée")
        self.map_summary.setWordWrap(True)
        layout.addWidget(self.map_summary)
        self._set_busy(False)

    def showEvent(self, event):  # noqa: N802 - API Qt
        super().showEvent(event)
        if self.provider is None and not self.busy and self.folder.text().strip():
            self._analyze()

    def _folder_changed(self):
        if self.validator is not None:
            self.validator.cancelled = True
            self.validator = None
            self.progress_timer.stop()
            self.validation_status.setText("")
        self.provider = None
        self.loaded_map = None
        self.validation = None
        self.details.clear()
        self.summary.setText(EMPTY_SUMMARY)
        self.map_summary.setText("Aucune map chargée")
        self.status.setText("Dossier du client DOFUS non configuré" if not self.folder.text().strip() else "Dossier modifié — relancez Analyser")
        self._set_busy(False)

    def _save_folder(self) -> bool:
        try:
            self.storage.set_setting("dofus_client_directory", self.folder.text().strip())
            return True
        except (sqlite3.Error, OSError, ValueError) as exc:
            self.status.setText(f"Configuration non enregistrée : {exc}")
            return False

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Dossier du client DOFUS", self.folder.text())
        if folder:
            self.folder.setText(folder)
            self._analyze()

    def _set_busy(self, busy: bool):
        self.busy = busy
        for widget in (self.folder, self.browse_button, self.analyze_button):
            widget.setEnabled(not busy)
        self.map_id.setEnabled(not busy)
        has_maps = self.provider is not None and bool(self.provider.list_maps())
        validating = self.validator is not None
        self.validate_button.setText("Arrêter la validation" if validating else "Valider toutes les maps")
        self.validate_button.setEnabled(validating or (not busy and has_maps))
        self.load_button.setEnabled(not busy and has_maps)
        self.export_button.setEnabled(not busy and self.loaded_map is not None)

    def _failed(self, message):
        self.status.setText(f"Analyse interrompue : {message}")
        self.validator = None
        self.progress_timer.stop()
        self._set_busy(False)

    def _analyze(self):
        if self.busy or not self._save_folder():
            return
        self.loaded_map = None
        self.validation = None
        self.map_summary.setText("Aucune map chargée")
        self.provider = LocalGameDataProvider(self.folder.text().strip(), cache_dir=self.output_directory / "cache")
        provider = self.provider
        self._set_busy(True)
        self.status.setText("Inventaire et indexation en cours…")

        def work():
            report = provider.scan_client()
            try:
                provider.export_report(self.output_directory / "probe-report.json")
            except (ValueError, OSError) as exc:
                report.errors.append(f"Rapport non enregistré : {exc}")
            return report

        self.jobs.submit(work, self._scan_done, self._failed)

    def _scan_done(self, report):
        self.status.setText(report.message)
        self._show_summary()
        ids = self.provider.list_maps()
        self.details.setPlainText(
            f"Formats/index reconnus : {report.formats}\n"
            f"IDs candidats (100 premiers) : {', '.join(map(str, ids[:100])) or 'aucun'}\n"
            f"Scan : {report.scan_seconds:.3f} s ; index : {report.index_seconds:.3f} s ; "
            f"cache : {report.index_cache_hit}\n"
            f"Pic allocations Python : {report.python_peak_bytes} octets\n"
            f"Version : {' ; '.join(report.version_evidence) or 'aucune preuve'}\n"
            f"{report.verdict} : {report.verdict_reason}\n"
            + "\n".join(report.errors[:100])
        )
        self._set_busy(False)

    def _show_summary(self):
        report = self.provider.report
        count = lambda extension: sum(entry.extension == extension for entry in report.files)
        version = f"DOFUS {report.detected_version} (version déclarée)" if report.detected_version else "non déterminé"
        d2p = [d for d in report.format_details.values() if d.get("format") == "D2P 2.1"]
        layouts = Counter(d.get("layout") for d in d2p)
        map_archives = sum(bool(d.get("dlm_entries")) for d in d2p)
        layout_text = ", ".join(f"{name} {n}" for name, n in sorted(layouts.items())) or "—"
        result = self.validation
        if result is None:
            readable = (f"{report.readable_maps} testées manuellement" if report.readable_maps else "non validées") + \
                " — lancez « Valider toutes les maps »"
            versions = encrypted = cells = "non mesuré"
            topology = "non démontrée sur ce dossier"
        else:
            maps = result["maps"]
            partial = " (validation interrompue)" if maps["stages"].get("entry_indexed", 0) != maps["maps_total"] else ""
            readable = f"{maps['stages'].get('cells_parsed', 0)} / {maps['maps_total']}{partial}"
            versions = ", ".join(f"v{v} : {n}" for v, n in maps["versions"].items()) or "aucune"
            encrypted = str(sum(e["maps"] for e in maps["envelopes"] if e["encrypted"]))
            cells = ", ".join(f"{c} cellules : {n} maps" for c, n in maps["cell_counts"].items()) or "—"
            topology = ("oui — cellId ↔ (x, y) logiques et voisinage ; projection écran non couverte"
                        if result["topology"]["logical_topology_verified"] else "non")
        self.summary.setText(
            f"Dossier : {report.client_path or '—'}\nClient : {version}\n"
            f"D2P : {count('.d2p')} fichiers ({layout_text})\nD2O : {count('.d2o')} fichiers\n"
            f"Maps candidates : {report.indexed_maps} IDs indexés ; {map_archives} archives de maps\n"
            f"Maps lisibles : {readable}\nVersions DLM : {versions}\nMaps chiffrées : {encrypted}\n"
            f"Cellules : {cells}\nTopologie logique vérifiée : {topology}\n"
            "Fight cells vérifiées : non — indices rouge/bleu uniquement"
        )

    def _validate(self):
        if self.validator is not None:
            self.validator.cancelled = True
            self.validation_status.setText("Arrêt demandé…")
            return
        if self.busy or self.provider is None:
            return
        provider = self.provider
        validator = self.validator = MapValidator(provider)
        destination = self.output_directory / "validation-summary.json"
        self._set_busy(True)
        self.progress_timer.start()

        def work():
            maps = validator.run()
            result = {"maps": maps, "topology": topology_decision(geometry_selfcheck(), maps)}
            destination.parent.mkdir(parents=True, exist_ok=True)
            summary = {k: v for k, v in maps.items() if k not in ("failures",)}
            summary["failures"] = maps["failures"][:50]
            provider.export_json(destination, {"maps": summary, "topology": result["topology"]})
            return result

        self.jobs.submit(work, self._validation_done, self._failed)

    def _show_progress(self):
        if self.validator is not None:
            self.validation_status.setText(f"Validation : {self.validator.progress} / {self.validator.total} maps")

    def _validation_done(self, result):
        if self.provider is None or self.validator is None:
            return
        self.progress_timer.stop()
        self.validator = None
        self.validation = result
        maps = result["maps"]
        self.validation_status.setText(f"Validation terminée en {maps['seconds']:.1f} s")
        failures = "\n".join(f"  {f['map_id']} [{f['code']}] {f['error'][:300]}" for f in maps["failures"][:20])
        controls = maps["topology_controls"]
        self.details.appendPlainText(
            f"\n— Validation des maps —\nNiveaux : {json.dumps(maps['stages'], ensure_ascii=False)}\n"
            f"Échecs ({len(maps['failures'])}) :\n{failures or '  aucun'}\n"
            f"Désaccord walkability sur arêtes — formule : {controls['formula_4']['walkability_disagreement']:.3f} ; "
            f"décalage inverse : {controls['mirrored_stagger_4']['walkability_disagreement']:.3f} ; "
            f"paires aléatoires : {controls['random_pairs']['walkability_disagreement']:.3f}\n"
            f"Critères topologie : {json.dumps(result['topology']['criteria'], ensure_ascii=False)}\n"
            f"Rouge/bleu : {maps['red_blue'].get('maps_with_both', 0)} maps avec les deux couleurs (indices, non démontrés)"
        )
        self._show_summary()
        self._set_busy(False)

    def _load_map(self):
        if self.busy or self.provider is None:
            return
        try:
            map_id = int(self.map_id.text())
            if map_id < 0:
                raise ValueError()
        except ValueError:
            self.status.setText("Map ID : entier positif ou nul attendu")
            return
        self.loaded_map = None
        self.map_summary.setText("Chargement de la map…")
        self._set_busy(True)
        provider = self.provider
        def work():
            game_map = provider.get_map(map_id)
            try:
                provider.export_report(self.output_directory / "probe-report.json")
            except (ValueError, OSError) as exc:
                provider.report.errors.append(f"Rapport non enregistré : {exc}")
            return game_map
        self.jobs.submit(work, self._map_done, self._map_failed)

    def _map_failed(self, message):
        self.map_summary.setText("Aucune map chargée")
        self._failed(message)

    def _map_done(self, game_map):
        self.loaded_map = game_map.map_id
        values = game_map.summary()
        self.map_summary.setText(
            f"Map {game_map.map_id} — {values['cells']} cellules\n"
            f"Marchables : {values['walkable']} ; non marchables : {values['non_walkable']} ; inconnues : {values['walkability_unknown']}\n"
            f"LOS : {values['los']} ; LOS inconnue : {values['los_unknown']}\n"
            f"Indices bleu/rouge : {values['blue_hints']}/{values['red_hints']} ; fight cells : inconnu\n"
            f"Sous-zone : {values['sub_area_id']} ; tacticalModeTemplateId : {values['tactical_mode_template_id']}\n"
            f"Source : {game_map.source}\n" + "\n".join(game_map.warnings)
        )
        self.status.setText(f"Map décodée en {self.provider.report.last_map_seconds:.4f} s — placements d'équipe non démontrés")
        self.details.appendPlainText(f"Après chargement : {self.provider.report.verdict} — {self.provider.report.verdict_reason}")
        self._show_summary()
        self._set_busy(False)

    def _export_map(self):
        if self.busy or self.loaded_map is None:
            return
        map_id = self.loaded_map
        destination = self.output_directory / "debug" / f"map_{map_id}.json"
        self._set_busy(True)
        self.jobs.submit(lambda: self.provider.export_map(map_id, destination), self._export_done, self._failed)

    def _export_done(self, path: Path):
        self.status.setText(f"Export enregistré : {path}")
        self._set_busy(False)
