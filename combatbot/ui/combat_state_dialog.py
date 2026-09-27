"""« Annoter phase et tour » (LOT 3B-6B) : vérité humaine de la phase de combat et du tour.

Chaque frame capturée en Vision réelle garde l'image entière du client, le bouton fin de tour et
la barre de sorts. L'humain choisit la phase (1 Hors combat, 2 Placement, 3 Combat, 4 Résultats,
0 Inconnu) et, en combat, le tour (M mon tour, E tour d'un autre, I inconnu). Entrée enregistre et
passe à la frame suivante, préremplie avec la vérité HUMAINE de la frame précédente (jamais une
prédiction). Aucune prédiction logicielle n'est affichée : TEST reste aveugle. Rien n'est cliqué
dans DOFUS.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSizePolicy,
    QVBoxLayout,
)

from combatbot.corpus.models import CorpusEntry
from combatbot.corpus.repository import CorpusRepository
from combatbot.ui.images import bgr_to_pixmap
from combatbot.vision.combat_state import CombatPhase, PHASE_LABELS, TURN_LABELS, TurnOwner

PHASE_KEYS = {Qt.Key.Key_1: CombatPhase.OUT_OF_COMBAT, Qt.Key.Key_2: CombatPhase.PLACEMENT,
              Qt.Key.Key_3: CombatPhase.FIGHTING, Qt.Key.Key_4: CombatPhase.RESULTS,
              Qt.Key.Key_0: CombatPhase.UNKNOWN}
TURN_KEYS = {Qt.Key.Key_M: TurnOwner.PLAYER, Qt.Key.Key_E: TurnOwner.OTHER, Qt.Key.Key_I: TurnOwner.UNKNOWN}
PHASE_SHORTCUTS = {CombatPhase.OUT_OF_COMBAT: "1", CombatPhase.PLACEMENT: "2", CombatPhase.FIGHTING: "3",
                   CombatPhase.RESULTS: "4", CombatPhase.UNKNOWN: "0"}
TURN_SHORTCUTS = {TurnOwner.PLAYER: "M", TurnOwner.OTHER: "E", TurnOwner.UNKNOWN: "I"}
# Couleurs de la frise (RGB) : une case par frame du combat.
TIMELINE_COLORS = {
    None: (70, 70, 78), "OUT_OF_COMBAT": (150, 150, 150), "PLACEMENT": (70, 130, 230),
    "FIGHTING:PLAYER": (70, 200, 110), "FIGHTING:OTHER": (230, 80, 80), "FIGHTING:UNKNOWN": (230, 170, 60),
    "RESULTS": (240, 210, 60), "UNKNOWN": (170, 90, 200),
}


def state_entries(repository: CorpusRepository) -> list[CorpusEntry]:
    """Frames capturées avec l'image entière du client (LOT 3B-6B et suivants)."""
    return sorted((entry for entry in repository.list_entries() if "client_frame" in entry.paths),
                  key=lambda entry: (entry.session_id, entry.frame_index))


def _read_image(path: Path) -> np.ndarray | None:
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def _truth_key(phase: str | None, turn: str | None) -> str | None:
    if phase is None:
        return None
    return f"{phase}:{turn}" if phase == "FIGHTING" else phase


class _Timeline(QLabel):
    """Frise cliquable : une case colorée par frame du combat, la frame courante encadrée."""

    clicked = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.keys: list[str | None] = []
        self.current = 0

    def set_state(self, keys: list[str | None], current: int) -> None:
        self.keys, self.current = keys, current
        self._paint()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self._paint()

    def _paint(self) -> None:
        width, height = max(1, self.width()), 22
        pixmap = QPixmap(width, height)
        pixmap.fill(QColor(30, 30, 34))
        count = len(self.keys)
        if count:
            painter = QPainter(pixmap)
            step = width / count
            for index, key in enumerate(self.keys):
                painter.fillRect(int(index * step), 3, max(1, int((index + 1) * step) - int(index * step) - 1),
                                 height - 6, QColor(*TIMELINE_COLORS.get(key, (70, 70, 78))))
            painter.setPen(QColor(255, 255, 255))
            painter.drawRect(int(self.current * step), 0, max(2, int(step)), height - 1)
            painter.end()
        self.setPixmap(pixmap)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self.keys:
            index = int(event.position().x() / max(1, self.width()) * len(self.keys))
            self.clicked.emit(min(len(self.keys) - 1, max(0, index)))


class CombatStateDialog(QDialog):
    def __init__(self, repository: CorpusRepository, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Annoter phase et tour — lecture seule")
        self.repository = repository
        self.entries = state_entries(repository)
        if not self.entries:
            raise ValueError("Aucune frame avec image du client : capturez d'abord un combat en Vision réelle "
                             "(« Enregistrer la séquence » coché) avec cette version de PythonBot.")
        self.sessions: dict[str, list[CorpusEntry]] = {}
        for entry in self.entries:
            self.sessions.setdefault(entry.session_id, []).append(entry)
        self.session_id: str | None = None
        self.index = 0
        self.phase: CombatPhase | None = None
        self.turn: TurnOwner | None = None
        self._carried: tuple[str | None, str | None] | None = None

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.sequence = QComboBox()
        top.addWidget(QLabel("Combat :"))
        top.addWidget(self.sequence, 1)
        root.addLayout(top)
        self.header = QLabel()
        self.header.setWordWrap(True)
        root.addWidget(self.header)
        self.timeline = _Timeline()
        self.timeline.setToolTip("Gris : hors combat · bleu : placement · vert : mon tour · rouge : tour d'un autre "
                                 "· orange : tour inconnu · jaune : résultats · violet : inconnu · sombre : à annoter")
        root.addWidget(self.timeline)

        body = QHBoxLayout()
        self.client = QLabel()
        self.client.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.client.setMinimumSize(640, 360)
        self.client.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        body.addWidget(self.client, 3)
        side = QVBoxLayout()
        side.addWidget(QLabel("<b>Bouton fin de tour</b>"))
        self.button_crop = QLabel()
        self.button_crop.setMinimumHeight(120)
        side.addWidget(self.button_crop)
        side.addWidget(QLabel("<b>Barre de sorts</b>"))
        self.spell_crop = QLabel()
        side.addWidget(self.spell_crop)
        side.addSpacing(10)
        side.addWidget(QLabel("<b>Phase</b>"))
        self.phase_group = QButtonGroup(self)
        self.phase_buttons: dict[CombatPhase, QPushButton] = {}
        for phase in (CombatPhase.OUT_OF_COMBAT, CombatPhase.PLACEMENT, CombatPhase.FIGHTING,
                      CombatPhase.RESULTS, CombatPhase.UNKNOWN):
            button = QPushButton(f"{PHASE_SHORTCUTS[phase]} · {PHASE_LABELS[phase]}")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, value=phase: self._set_phase(value))
            self.phase_group.addButton(button)
            self.phase_buttons[phase] = button
            side.addWidget(button)
        side.addWidget(QLabel("<b>Tour (en combat)</b>"))
        self.turn_group = QButtonGroup(self)
        self.turn_buttons: dict[TurnOwner, QPushButton] = {}
        for turn in (TurnOwner.PLAYER, TurnOwner.OTHER, TurnOwner.UNKNOWN):
            button = QPushButton(f"{TURN_SHORTCUTS[turn]} · {TURN_LABELS[turn]}")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, value=turn: self._set_turn(value))
            self.turn_group.addButton(button)
            self.turn_buttons[turn] = button
            side.addWidget(button)
        side.addStretch()
        self.origin = QLabel()
        self.origin.setWordWrap(True)
        side.addWidget(self.origin)
        body.addLayout(side, 1)
        root.addLayout(body, 1)

        self.status = QLabel("Entrée : enregistrer et suivante · ←/→ : naviguer · 1-4/0 : phase · M/E/I : tour")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        actions = QHBoxLayout()
        self.previous_button = QPushButton("← Précédente")
        self.next_button = QPushButton("Suivante →")
        self.confirm_button = QPushButton("✓ Enregistrer (Entrée)")
        self.confirm_button.setObjectName("primary")
        close = QPushButton("Fermer")
        for button in (self.previous_button, self.next_button):
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(self.confirm_button)
        actions.addWidget(close)
        root.addLayout(actions)
        for button in (*self.phase_buttons.values(), *self.turn_buttons.values(), self.previous_button,
                       self.next_button, self.confirm_button, close):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.previous_button.clicked.connect(lambda: self._move(-1))
        self.next_button.clicked.connect(lambda: self._move(1))
        self.confirm_button.clicked.connect(self._confirm)
        close.clicked.connect(self.accept)
        self.timeline.clicked.connect(self._jump)
        self.sequence.currentIndexChanged.connect(self._select_session)
        self._fill_sessions()
        self._select_session(self.sequence.currentIndex())

    # ------------------------------------------------------------------ combats
    def _annotation(self, entry: CorpusEntry):
        try:
            return self.repository.read_annotation(entry.observation_id)
        except (OSError, ValueError):
            return None

    def _split(self, entry: CorpusEntry) -> str | None:
        try:
            value = (self.repository.read_observation(entry).get("capture") or {}).get("entity_split_declared")
        except (OSError, ValueError):
            return None
        return value if value in ("train", "validation", "test") else None

    def _done(self, entry: CorpusEntry) -> bool:
        annotation = self._annotation(entry)
        return bool(annotation and annotation.combat_state_confirmed)

    def _fill_sessions(self) -> None:
        rows = []
        for session_id, entries in self.sessions.items():
            done = sum(self._done(entry) for entry in entries)
            try:
                created = str(self.repository.read_observation(entries[0]).get("created_at") or "")
            except (OSError, ValueError):
                created = ""
            rows.append((done >= len(entries), created, session_id, done, len(entries), self._split(entries[0])))
        # Combats à terminer d'abord, les plus récents en tête.
        rows.sort(key=lambda row: (row[0], [-ord(char) for char in row[1]]))
        current = self.sequence.currentData()
        self.sequence.blockSignals(True)
        self.sequence.clear()
        for finished, created, session_id, done, total, split in rows:
            when = f"{created[8:10]}/{created[5:7]} {created[11:16]}" if len(created) >= 16 else "?"
            state = f"✓ {total} frames annotées" if finished else f"⏳ {done}/{total} annotées"
            self.sequence.addItem(f"{state} · {when} · {(split or 'non déclaré').upper()} · {session_id}", session_id)
        if current is not None and self.sequence.findData(current) >= 0:
            self.sequence.setCurrentIndex(self.sequence.findData(current))
        self.sequence.blockSignals(False)

    def _select_session(self, _index: int) -> None:
        self.session_id = self.sequence.currentData()
        entries = self.sessions.get(self.session_id, [])
        pending = next((index for index, entry in enumerate(entries) if not self._done(entry)), None)
        self.index = pending if pending is not None else 0
        self._show()

    @property
    def current_entries(self) -> list[CorpusEntry]:
        return self.sessions.get(self.session_id, [])

    # ------------------------------------------------------------------ affichage
    def _show(self) -> None:
        entries = self.current_entries
        if not entries:
            return
        entry = entries[self.index]
        split = self._split(entry)
        annotation = self._annotation(entry)
        self._carried = None
        if annotation and annotation.combat_state_confirmed:
            phase, turn = annotation.combat_phase_truth, annotation.turn_owner_truth
            self.origin.setText(f"<span style='color:#7fd67f'>✓ Vérité enregistrée "
                                f"({annotation.combat_state_mode or 'manuel'})</span>")
        else:
            previous = self._annotation(entries[self.index - 1]) if self.index > 0 else None
            if previous and previous.combat_state_confirmed:
                phase, turn = previous.combat_phase_truth, previous.turn_owner_truth
                self._carried = (phase, turn)
                self.origin.setText("Prérempli avec <b>votre</b> réponse de la frame précédente : "
                                    "Entrée si c'est toujours vrai, sinon changez.")
            else:
                phase = turn = None
                self.origin.setText("À annoter : choisissez la phase (1-4, 0).")
        self.phase = CombatPhase(phase) if phase else None
        self.turn = TurnOwner(turn) if turn else None
        self._sync_buttons()
        done = sum(self._done(item) for item in entries)
        blind = " · <b>TEST aveugle</b> (aucune prédiction affichée)" if split == "test" else ""
        self.header.setText(f"Frame <b>{self.index + 1}/{len(entries)}</b> · {done} annotées · split "
                            f"<b>{(split or 'non déclaré').upper()}</b>{blind} · {entry.session_id}")
        self._render(entry)
        self._update_timeline()
        self.previous_button.setEnabled(self.index > 0)
        self.next_button.setEnabled(self.index < len(entries) - 1)

    def _update_timeline(self) -> None:
        keys = []
        for item in self.current_entries:
            annotation = self._annotation(item)
            keys.append(_truth_key(annotation.combat_phase_truth, annotation.turn_owner_truth)
                        if annotation and annotation.combat_state_confirmed else None)
        self.timeline.set_state(keys, self.index)

    def _render(self, entry: CorpusEntry) -> None:
        def show(label: QLabel, key: str, width: int, height: int, zoom_small: bool = False) -> None:
            image = _read_image(self.repository.resolve(entry.paths[key])) if key in entry.paths else None
            if image is None:
                label.setText("absent")
                return
            if zoom_small:
                image = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
            pixmap = bgr_to_pixmap(image)
            label.setPixmap(pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        show(self.client, "client_frame", max(640, self.client.width()), max(360, self.client.height()))
        show(self.button_crop, "end_turn_crop", 300, 160, zoom_small=True)
        show(self.spell_crop, "spell_bar_crop", 300, 90)

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        if self.current_entries:
            self._render(self.current_entries[self.index])

    def _sync_buttons(self) -> None:
        for phase, button in self.phase_buttons.items():
            button.setChecked(phase is self.phase)
        fighting = self.phase is CombatPhase.FIGHTING
        self.turn_group.setExclusive(False)
        for turn, button in self.turn_buttons.items():
            button.setEnabled(fighting)
            button.setChecked(fighting and turn is self.turn)
        self.turn_group.setExclusive(True)

    # ------------------------------------------------------------------ saisie
    def _set_phase(self, phase: CombatPhase) -> None:
        self.phase = phase
        if phase is not CombatPhase.FIGHTING:
            self.turn = None
        self._sync_buttons()

    def _set_turn(self, turn: TurnOwner) -> None:
        if self.phase is not CombatPhase.FIGHTING:
            self.phase = CombatPhase.FIGHTING
        self.turn = turn
        self._sync_buttons()

    def _confirm(self) -> None:
        entries = self.current_entries
        if not entries:
            return
        if self.phase is None:
            QMessageBox.information(self, "Phase et tour", "Choisissez d'abord la phase (1-4, ou 0 si inconnue).")
            return
        if self.phase is CombatPhase.FIGHTING and self.turn is None:
            QMessageBox.information(self, "Phase et tour", "En combat, indiquez le tour : M, E ou I (inconnu).")
            return
        entry = entries[self.index]
        value = (self.phase.value, self.turn.value if self.turn else None)
        existing = self._annotation(entry)
        if existing and existing.combat_state_confirmed and \
                (existing.combat_phase_truth, existing.turn_owner_truth) == value:
            mode = existing.combat_state_mode or "manual"
        elif self._carried == value:
            mode = "carried_previous"
        else:
            mode = "manual_blind" if self._split(entry) == "test" else "manual"
        try:
            self.repository.confirm_combat_state(entry.observation_id, phase=value[0], turn_owner=value[1], mode=mode)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Phase et tour", f"Enregistrement impossible : {exc}")
            return
        self.status.setText(f"Frame {entry.frame_index} : {value[0]}{' · ' + value[1] if value[1] else ''} enregistrée.")
        if self.index < len(entries) - 1:
            self.index += 1
            self._show()
        else:
            self._fill_sessions()
            self._show()
            QMessageBox.information(self, "Phase et tour", "Dernière frame de ce combat annotée.")

    def _move(self, step: int) -> None:
        target = self.index + step
        if 0 <= target < len(self.current_entries):
            self.index = target
            self._show()

    def _jump(self, index: int) -> None:
        self.index = index
        self._show()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - API Qt
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._confirm()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self._move(1)
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self._move(-1)
        elif key in PHASE_KEYS:
            self._set_phase(PHASE_KEYS[key])
        elif key in TURN_KEYS:
            self._set_turn(TURN_KEYS[key])
        else:
            super().keyPressEvent(event)
