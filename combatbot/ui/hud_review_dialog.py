"""Revue humaine des vérités PA/PM et collecte HUD réelle en lecture seule (LOT 3B-4R2).

LOT 3B-6A — revue assistée : sur les frames TRAIN/VALIDATION, la valeur proposée par le lecteur
(gabarits locaux, sinon RapidOCR même sous son seuil de sécurité) préremplit PA/PM ; « ✓ Tout est
correct » (Entrée) l'enregistre comme vérité HUMAINE, avec la suggestion d'origine. Sur TEST (ou
split non déclaré), la revue reste aveugle : aucune prédiction n'est calculée ni affichée.
« Construire les gabarits (TRAIN) » apprend les chiffres depuis les seules vérités humaines TRAIN.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import cv2
import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QSpinBox, QVBoxLayout,
)

from combatbot.corpus.hud_collection import CONTEXTS, HUDCollectionSession
from combatbot.corpus.hud_suggestions import (
    HUDSuggestionProvider, build_local_templates, frame_split, hud_assistance_allowed,
)
from combatbot.corpus.models import Annotation, CorpusEntry, HUD_CROP_QUALITIES
from combatbot.corpus.repository import CorpusRepository
from combatbot.ui.images import bgr_to_pixmap

ZOOM = 5
DECISION_LABELS = {"confirmed": "confirmée sans changement", "corrected": "corrigée",
                   "entered": "saisie", "unreadable": "illisible"}


def _zoom(image: np.ndarray, factor: int = ZOOM) -> np.ndarray:
    return cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_NEAREST)


def hud_entries(repository: CorpusRepository) -> list[CorpusEntry]:
    return [entry for entry in repository.list_entries() if {"ap_crop", "mp_crop"} & set(entry.paths)]


class _CounterPanel(QGroupBox):
    def __init__(self, title: str) -> None:
        super().__init__(title)
        layout = QGridLayout(self)
        self.original = QLabel()
        self.original.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom = QLabel()
        self.zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom.setMinimumSize(300, 260)
        self.zoom.setStyleSheet("background:#0b1220; border:1px solid #34445b;")
        self.recorded = QLabel()
        self.recorded.setWordWrap(True)
        self.value = QSpinBox()
        self.value.setRange(-1, 99)
        self.value.setSpecialValueText("À saisir")
        self.value.setMinimumHeight(36)
        self.unreadable = QCheckBox("Illisible")
        self.quality = QComboBox()
        self.quality.addItem("Qualité non classée", None)
        for quality in sorted(HUD_CROP_QUALITIES):
            self.quality.addItem(quality, quality)
        layout.addWidget(QLabel("Taille réelle"), 0, 0)
        layout.addWidget(self.original, 1, 0)
        layout.addWidget(QLabel(f"Zoom ×{ZOOM} nearest-neighbor"), 0, 1)
        layout.addWidget(self.zoom, 1, 1)
        layout.addWidget(self.recorded, 2, 0, 1, 2)
        row = QHBoxLayout()
        row.addWidget(QLabel("Valeur visible :"))
        row.addWidget(self.value, 1)
        row.addWidget(self.unreadable)
        layout.addLayout(row, 3, 0, 1, 2)
        layout.addWidget(self.quality, 4, 0, 1, 2)
        self.unreadable.toggled.connect(lambda checked: self.value.setEnabled(not checked and self._editable))
        self._editable = False

    def show_crop(self, image: np.ndarray | None) -> None:
        if image is None:
            self.original.setText("Crop absent")
            self.zoom.setText("—")
            return
        self.original.setPixmap(bgr_to_pixmap(image))
        self.zoom.setPixmap(bgr_to_pixmap(_zoom(image)))

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self.value.setEnabled(editable and not self.unreadable.isChecked())


class HUDReviewDialog(QDialog):
    """Confirmer / Corriger / Illisible / Précédent / Suivant, une observation à la fois."""

    def __init__(self, repository: CorpusRepository, parent=None, *, only_untreated: bool = True,
                 suggestion_provider=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.entries = hud_entries(repository)
        self.index = 0
        self._provider = suggestion_provider
        self._suggestions: dict[str, dict] = {}
        self._shown_suggestion: dict | None = None
        self._splits = {}
        for entry in self.entries:
            try:
                self._splits[entry.observation_id] = frame_split(self.repository.read_observation(entry))
            except (OSError, ValueError):
                self._splits[entry.observation_id] = None
        self.setWindowTitle("Revue HUD PA/PM — vérité humaine")
        self.resize(1320, 900)
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.progress = QLabel()
        self.only_untreated = QCheckBox("Seulement les observations non confirmées")
        self.only_untreated.setChecked(only_untreated)
        self.split_filter = QComboBox()
        for caption, value in (("TRAIN (sert aux gabarits)", "train"), ("VALIDATION", "validation"),
                               ("TEST (aveugle)", "test"), ("Tous les splits", None)):
            self.split_filter.addItem(caption, value)
        self.build_button = QPushButton("Construire les gabarits (TRAIN)")
        self.build_button.setToolTip("Apprend les chiffres depuis les vérités humaines TRAIN seulement, puis les "
                                     "installe pour la Vision réelle (ancien jeu sauvegardé).")
        top.addWidget(self.progress, 1)
        top.addWidget(QLabel("Split :"))
        top.addWidget(self.split_filter)
        top.addWidget(self.only_untreated)
        top.addWidget(self.build_button)
        root.addLayout(top)
        self.header = QLabel()
        self.header.setWordWrap(True)
        root.addWidget(self.header)
        self.frame = QLabel()
        self.frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame.setMinimumHeight(300)
        self.frame.setStyleSheet("background:#0b1220; border:1px solid #34445b;")
        root.addWidget(self.frame, 1)
        panels = QHBoxLayout()
        self.ap = _CounterPanel("PA")
        self.mp = _CounterPanel("PM")
        panels.addWidget(self.ap)
        panels.addWidget(self.mp)
        root.addLayout(panels)
        self.review_stats = QLabel()
        self.review_stats.setWordWrap(True)
        root.addWidget(self.review_stats)
        self.status = QLabel("TRAIN/VALIDATION : la suggestion est préremplie, corrigez seulement ce qui est faux. "
                             "TEST : aveugle.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        self.previous_button = QPushButton("◀ Précédent")
        self.next_button = QPushButton("Suivant ▶")
        self.correct_button = QPushButton("Corriger")
        self.confirm_button = QPushButton("✓ Tout est correct (Entrée)")
        self.confirm_button.setObjectName("primary")
        self.confirm_button.setDefault(True)
        close = QPushButton("Fermer")
        for button in (self.previous_button, self.next_button):
            buttons.addWidget(button)
        buttons.addStretch()
        for button in (self.correct_button, self.confirm_button, close):
            buttons.addWidget(button)
        root.addLayout(buttons)
        self.previous_button.clicked.connect(lambda: self._move(-1))
        self.next_button.clicked.connect(lambda: self._move(1))
        self.correct_button.clicked.connect(self._start_correction)
        self.confirm_button.clicked.connect(self._confirm)
        close.clicked.connect(self.accept)
        self.only_untreated.toggled.connect(lambda _checked: self._refilter())
        self.split_filter.currentIndexChanged.connect(lambda _index: self._refilter())
        self.build_button.clicked.connect(self._build_templates)
        # TRAIN d'abord (gabarits) s'il reste du travail, sinon tous les splits.
        if not any(self._splits.get(e.observation_id) == "train" and self._matches(e) for e in self.entries):
            self.split_filter.setCurrentIndex(self.split_filter.findData(None))
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=lambda: self._move(1))
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=lambda: self._move(-1))
        if self.only_untreated.isChecked():
            self.index = self._first_matching(0, 1) or 0
        self._show()

    def _annotation(self, entry: CorpusEntry) -> Annotation | None:
        try:
            return self.repository.read_annotation(entry)
        except ValueError:
            return None

    def _matches(self, entry: CorpusEntry) -> bool:
        wanted = self.split_filter.currentData() if hasattr(self, "split_filter") else None
        if wanted is not None and self._splits.get(entry.observation_id) != wanted:
            return False
        if not self.only_untreated.isChecked():
            return True
        annotation = self._annotation(entry)
        return annotation is None or not annotation.human_confirmed

    def _first_matching(self, start: int, step: int) -> int | None:
        index = start
        while 0 <= index < len(self.entries):
            if self._matches(self.entries[index]):
                return index
            index += step
        return None

    def _refilter(self) -> None:
        target = self._first_matching(0, 1)
        self.index = target if target is not None else 0
        self._show()

    # ------------------------------------------------------------------ assistance 3B-6A
    def _provider_or_default(self):
        if self._provider is None:
            self._provider = HUDSuggestionProvider()
        return self._provider

    def _suggestion_for(self, entry: CorpusEntry) -> dict | None:
        if entry.observation_id not in self._suggestions:
            provider = self._provider_or_default()
            self._suggestions[entry.observation_id] = {
                "ap": provider.suggest(self._read(entry, "ap_crop"), "AP"),
                "mp": provider.suggest(self._read(entry, "mp_crop"), "MP"),
                "templates": getattr(provider, "template_count", None)}
        return self._suggestions[entry.observation_id]

    def _build_templates(self) -> None:
        provider = self._provider_or_default()
        try:
            summary = build_local_templates(self.repository, provider.data_root)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Gabarits HUD", f"Construction impossible : {exc}")
            return
        if not summary.get("installed"):
            QMessageBox.information(self, "Gabarits HUD", str(summary.get("reason")))
            return
        provider.reload()
        self._suggestions.clear()
        digits = "  ".join(f"{kind} : " + ", ".join(f"{digit}×{count}" for digit, count in values.items())
                           for kind, values in summary["digits"].items())
        QMessageBox.information(
            self, "Gabarits HUD installés",
            f"{summary['train_samples']} lectures TRAIN confirmées → gabarits installés.\n{digits}\n\n"
            "Redémarrez l'observation en Vision réelle pour les utiliser.")
        self._show()

    def _update_review_stats(self) -> None:
        counts = {"ap": {"accepted": 0, "corrected": 0}, "mp": {"accepted": 0, "corrected": 0}}
        for entry in self.entries:
            annotation = self._annotation(entry)
            outcome = (annotation.hud_suggestion or {}).get("outcome") if annotation else None
            for kind in ("ap", "mp"):
                if outcome and outcome.get(kind) in counts[kind]:
                    counts[kind][outcome[kind]] += 1
        self.review_stats.setText(
            f"Revue assistée (aide, pas un benchmark) — PA : suggestion juste {counts['ap']['accepted']}, "
            f"corrigée {counts['ap']['corrected']} · PM : juste {counts['mp']['accepted']}, "
            f"corrigée {counts['mp']['corrected']}")

    def _move(self, step: int) -> None:
        target = self._first_matching(self.index + step, step)
        if target is None:
            self.status.setText("Aucune autre observation dans ce sens avec ce filtre.")
            return
        self.index = target
        self._show()

    def _read(self, entry: CorpusEntry, key: str) -> np.ndarray | None:
        relative = entry.paths.get(key)
        if not relative:
            return None
        return cv2.imread(str(self.repository.resolve(relative)), cv2.IMREAD_COLOR)

    def _update_progress(self) -> None:
        summary = self.repository.hud_review_summary()
        observations = summary["observations"]
        total = observations["human_confirmed"] + observations["untreated"]  # type: ignore[index]
        self.progress.setText(
            f"Observation {self.index + 1}/{len(self.entries)} · confirmées par vous : "
            f"{observations['human_confirmed']}/{total}"  # type: ignore[index]
        )

    def _show(self) -> None:
        self._update_progress()
        if not self.entries:
            self.header.setText("Aucune observation avec crops PA/PM dans le corpus.")
            return
        entry = self.entries[self.index]
        annotation = self._annotation(entry)
        try:
            capture = self.repository.read_observation(entry).get("capture", {})
        except ValueError:
            capture = {}
        context = capture.get("context", "—") if isinstance(capture, dict) else "—"
        state = "non confirmée"
        if annotation is not None and annotation.human_confirmed:
            decisions = ", ".join(f"{kind.upper()} {DECISION_LABELS.get(value, value)}"
                                  for kind, value in (annotation.hud_review or {}).items())
            state = f"confirmée le {annotation.confirmed_at} ({decisions})"
        self.header.setText(
            f"<b>{entry.session_id}</b> · frame {entry.frame_index} · {entry.observation_id} · "
            f"contexte : {context} · <b>{state}</b>"
        )
        frame = self._read(entry, "frame")
        if frame is not None:
            self.frame.setPixmap(bgr_to_pixmap(frame).scaled(
                1250, 420, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.frame.setText("Frame d'origine absente")
        source = "vérité humaine confirmée" if annotation is not None and annotation.human_confirmed \
            else "import non vérifié"
        split = self._splits.get(entry.observation_id)
        confirmed = annotation is not None and annotation.human_confirmed
        assisted = split in ("train", "validation") and not confirmed
        suggestion = self._suggestion_for(entry) if assisted else None
        self._shown_suggestion = suggestion
        self.header.setText(self.header.text() + f" · split <b>{(split or 'non déclaré').upper()}</b>"
                            + ("" if split in ("train", "validation") else " · <b>aveugle</b>"))
        for panel, key, truth, quality, kind in (
            (self.ap, "ap_crop", annotation.ap_truth if annotation else None,
             annotation.ap_crop_quality if annotation else None, "ap"),
            (self.mp, "mp_crop", annotation.mp_truth if annotation else None,
             annotation.mp_crop_quality if annotation else None, "mp"),
        ):
            panel.show_crop(self._read(entry, key))
            unreadable = bool(annotation and (annotation.hud_review or {}).get(kind) == "unreadable")
            panel.recorded.setText(
                f"Valeur actuellement inscrite : <b>{truth if truth is not None else '—'}</b> ({source})"
                + (" · marquée illisible" if unreadable else ""))
            panel.unreadable.setChecked(unreadable)
            panel.value.setValue(truth if truth is not None else -1)
            panel.quality.setCurrentIndex(max(0, panel.quality.findData(quality)))
            # Une valeur existante n'est modifiable qu'après « Corriger » ; une capture neuve se saisit.
            panel.set_editable(truth is None and not unreadable)
            proposal = (suggestion or {}).get(kind)
            if proposal is not None:
                if truth is None and proposal.get("value") is not None:
                    panel.value.setValue(int(proposal["value"]))     # préremplissage, pas une vérité
                state = ("acceptée par le lecteur" if proposal.get("accepted")
                         else "non validée par le lecteur : vérifiez")
                panel.recorded.setText(
                    panel.recorded.text() + f"<br><span style='color:#c9a2ff'>Suggestion : "
                    f"<b>{proposal.get('value') if proposal.get('value') is not None else '—'}</b> · "
                    f"{proposal.get('source')} {float(proposal.get('confidence') or 0):.0%} · {state}</span>")
            elif confirmed and annotation.hud_suggestion and annotation.hud_suggestion.get(kind):
                before = annotation.hud_suggestion[kind]
                panel.recorded.setText(panel.recorded.text() + f"<br>Suggestion d'origine : {before.get('value')} "
                                       f"({(annotation.hud_suggestion.get('outcome') or {}).get(kind, '—')})")
        self._update_review_stats()
        self.previous_button.setEnabled(self._first_matching(self.index - 1, -1) is not None)
        self.next_button.setEnabled(self._first_matching(self.index + 1, 1) is not None)
        self.confirm_button.setFocus()

    def _start_correction(self) -> None:
        for panel in (self.ap, self.mp):
            panel.set_editable(True)
        self.ap.value.setFocus()
        self.ap.value.selectAll()
        self.status.setText("Correction : saisissez la valeur réellement visible, puis Confirmer.")

    def _confirm(self) -> None:
        if not self.entries:
            return
        entry = self.entries[self.index]
        values = {}
        for kind, panel in (("ap", self.ap), ("mp", self.mp)):
            if panel.unreadable.isChecked():
                values[kind] = None
            elif panel.value.value() < 0:
                QMessageBox.warning(self, "Valeur manquante",
                                    f"{kind.upper()} : saisissez la valeur visible ou cochez Illisible.")
                return
            else:
                values[kind] = panel.value.value()
        snapshot = None
        if self._shown_suggestion is not None:
            outcome = {kind: ("accepted" if values[kind] is not None
                              and values[kind] == (self._shown_suggestion.get(kind) or {}).get("value") else "corrected")
                       for kind in ("ap", "mp")}
            snapshot = {**self._shown_suggestion, "outcome": outcome}
        try:
            annotation = self.repository.confirm_hud_truth(
                entry.observation_id, ap=values["ap"], mp=values["mp"],
                ap_unreadable=self.ap.unreadable.isChecked(), mp_unreadable=self.mp.unreadable.isChecked(),
                ap_crop_quality=self.ap.quality.currentData(), mp_crop_quality=self.mp.quality.currentData(),
                suggestion=snapshot,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Confirmation impossible", str(exc))
            return
        decisions = ", ".join(f"{kind.upper()} {DECISION_LABELS[value]}"
                              for kind, value in (annotation.hud_review or {}).items())
        target = self._first_matching(self.index + 1, 1)
        if target is not None:
            self.index = target
        self._show()
        self.status.setText(f"{entry.observation_id} : {decisions}."
                            + ("" if target is not None else " Fin de la liste pour ce filtre."))


GrabCallable = Callable[[], tuple[np.ndarray, np.ndarray | None, np.ndarray | None, dict[str, Any]]]


class HUDCollectionDialog(QDialog):
    """HUD Real Collection : l'utilisateur joue, DofBot2 capture seulement PA/PM."""

    collection_changed = Signal()

    def __init__(self, repository: CorpusRepository, grab: GrabCallable, parent=None) -> None:
        super().__init__(parent)
        self.repository = repository
        self.grab = grab
        self.session = HUDCollectionSession(repository)
        self.setWindowTitle("HUD Real Collection — lecture seule")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(520, 520)
        root = QVBoxLayout(self)
        notice = QLabel(
            "<b>Lecture seule.</b> Jouez normalement : DofBot2 capture uniquement la fenêtre et "
            "découpe PA/PM. Aucun clic, sort, déplacement, fin de tour ni lancement de combat n'est "
            "envoyé. Aucune lecture n'est affichée : les valeurs seront saisies dans la revue HUD."
        )
        notice.setWordWrap(True)
        root.addWidget(notice)
        self.session_label = QLabel(f"Session : {self.session.session_id}")
        root.addWidget(self.session_label)
        context_row = QHBoxLayout()
        context_row.addWidget(QLabel("Contexte actuel :"))
        self.context = QComboBox()
        for context in CONTEXTS:
            self.context.addItem(context, context)
        self.context.setCurrentIndex(self.context.findData("combat"))
        context_row.addWidget(self.context, 1)
        root.addLayout(context_row)
        self.capture_button = QPushButton("Capturer PA/PM maintenant")
        self.capture_button.setObjectName("primary")
        self.capture_button.setMinimumHeight(44)
        root.addWidget(self.capture_button)
        continuous_row = QHBoxLayout()
        self.continuous = QPushButton("Capture continue")
        self.continuous.setCheckable(True)
        self.interval = QSpinBox()
        self.interval.setRange(1, 10)
        self.interval.setValue(2)
        self.interval.setSuffix(" s")
        continuous_row.addWidget(self.continuous, 1)
        continuous_row.addWidget(QLabel("toutes les"))
        continuous_row.addWidget(self.interval)
        root.addLayout(continuous_row)
        crops = QHBoxLayout()
        self.last_ap, self.last_mp = QLabel("PA —"), QLabel("PM —")
        for label in (self.last_ap, self.last_mp):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumSize(200, 180)
            label.setStyleSheet("background:#0b1220; border:1px solid #34445b;")
            crops.addWidget(label)
        root.addLayout(crops)
        self.counters = QLabel()
        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.counters)
        root.addWidget(self.status)
        close = QPushButton("Terminer la collecte")
        root.addWidget(close)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self._capture(manual=False))
        self.capture_button.clicked.connect(lambda: self._capture(manual=True))
        self.continuous.toggled.connect(self._toggle_continuous)
        self.interval.valueChanged.connect(lambda value: self.timer.setInterval(value * 1000))
        close.clicked.connect(self.accept)
        self._update_counters()

    def _toggle_continuous(self, enabled: bool) -> None:
        if enabled:
            self.timer.start(self.interval.value() * 1000)
            self.continuous.setText("Arrêter la capture continue")
        else:
            self.timer.stop()
            self.continuous.setText("Capture continue")

    def _update_counters(self) -> None:
        self.counters.setText(f"Captures enregistrées : {self.session.saved} · "
                              f"doublons ignorés : {self.session.duplicates}")

    def _capture(self, *, manual: bool) -> None:
        try:
            frame, ap, mp, metadata = self.grab()
            outcome = self.session.capture(frame, ap, mp, metadata,
                                           context=self.context.currentData(), manual=manual)
        except Exception as exc:  # noqa: BLE001 - la collecte s'arrête proprement sur toute erreur
            self.continuous.setChecked(False)
            self.status.setText(f"Capture impossible : {exc}")
            return
        if outcome.saved:
            for label, image in ((self.last_ap, ap), (self.last_mp, mp)):
                if image is not None:
                    label.setPixmap(bgr_to_pixmap(_zoom(image, 3)))
            self.collection_changed.emit()
        self.status.setText(outcome.reason)
        self._update_counters()

    def done(self, result: int) -> None:  # noqa: D401 - API Qt
        self.timer.stop()
        super().done(result)
