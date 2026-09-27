"""« Annoter les entités » (LOT 3B-5) : vérité humaine par DofusCellId, sans prédiction affichée.

La frame d'origine et la grille GameData projetée enregistrée avec l'observation sont affichées ;
le survol donne le cell ID, un zoom local aide à voir le marqueur au sol. Un clic sur une cellule
choisit JOUEUR, ENNEMI (E1…E8), VIDE confirmé ou INCONNU. Tout se passe dans DofBot2 : rien
n'est cliqué dans DOFUS. La frame précédente n'est jamais recopiée dans la vérité.

LOT 3B-5D : quelques cellules (contour jaune « ? ») sont tirées par ``empty_sampling`` sans
regarder aucune prédiction ; chacune reçoit VIDE, OCCUPÉE ou INCONNU avant la confirmation.

LOT 3B-5E — annotation assistée : sur TRAIN/VALIDATION, les suggestions du logiciel préremplissent
la frame (contour violet, « ? »). Elles ne deviennent une vérité qu'au clic humain (« Tout est
correct » ou « Confirmer ») ; la suggestion d'origine est enregistrée avec la vérité. Sur TEST (et
split non déclaré), l'annotation reste aveugle : aucune prédiction n'est calculée ni montrée avant
que la vérité de la frame soit enregistrée ; ensuite seulement « Comparer avec la prédiction ».
"""

from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
    QPushButton, QVBoxLayout,
)

from combatbot.corpus.entity_suggestions import (
    DetectorSuggestionProvider, assistance_allowed, frame_split, review, review_statistics,
)

from combatbot.corpus.empty_sampling import SAMPLE_VERSION, sample_cells
from combatbot.corpus.models import ENTITY_FRAME_PHASES, CorpusEntry
from combatbot.corpus.repository import CorpusRepository
from combatbot.ui.images import bgr_to_pixmap

ENEMY_LABELS = tuple(f"E{index}" for index in range(1, 9))
SAMPLE_CAPTIONS = {"EMPTY": "vide", "OCCUPIED": "occupée", "UNKNOWN": "inconnu"}
UNSAVED_TRACKING = "Vous avez une modification de confirmation non enregistrée."
TRACKING_INVALIDATED = "Confirmation de suivi invalidée après modification des identités."
SUGGESTED_COLOR = (255, 110, 200)        # BGR : violet, jamais confondu avec une vérité
STATUS_TEXT = {"confirmed": "✓ confirmé", "corrected": "✎ corrigé", "rejected": "✗ rejeté",
               "added": "＋ ajouté (manqué par le logiciel)"}
PHASE_LABELS = {"placement": "Placement", "debut_combat": "Début de combat", "mon_tour": "Mon tour",
                "tour_ennemi": "Tour ennemi", "animation_sort": "Animation de sort",
                "changement_tour": "Changement de tour", "exploration": "Exploration", "autre": "Autre"}


def projected_cells(document: dict) -> list[dict]:
    """Cellules projetées annotables : traversables en combat selon GameData (ou inconnues)."""
    snapshot = document.get("grid_snapshot") or {}
    if snapshot.get("grid_source") != "GAMEDATA_PROJECTED":
        return []
    cells = []
    for cell in snapshot.get("cells", ()):
        if cell.get("cell_id") is None or not cell.get("polygon"):
            continue
        if cell.get("walkable_static") is False or cell.get("non_walkable_during_fight_static") is True:
            continue  # une entité ne peut pas se tenir sur une cellule non traversable
        cells.append(cell)
    return cells


def entity_entries(repository: CorpusRepository) -> list[CorpusEntry]:
    result = []
    for entry in repository.list_entries():
        if "grid-not-aligned" in entry.tags:
            continue      # 3B-6C : frame gardée pour la phase/tour, grille non alignée
        try:
            if projected_cells(repository.read_observation(entry)):
                result.append(entry)
        except (OSError, ValueError):
            continue
    return result


class _CellCanvas(QLabel):
    hovered = Signal(object)
    clicked = Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.setMouseTracking(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(900, 480)
        self.setStyleSheet("background:#0f1510; border:1px solid rgba(143,209,79,36);")
        self.image_size: tuple[int, int] | None = None

    def _image_point(self, position) -> tuple[float, float] | None:
        pixmap = self.pixmap()
        if pixmap is None or self.image_size is None or pixmap.isNull():
            return None
        left = (self.width() - pixmap.width()) / 2
        top = (self.height() - pixmap.height()) / 2
        x, y = position.x() - left, position.y() - top
        if not (0 <= x < pixmap.width() and 0 <= y < pixmap.height()):
            return None
        return x * self.image_size[0] / pixmap.width(), y * self.image_size[1] / pixmap.height()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.hovered.emit(self._image_point(event.position()))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.setFocus()
        point = self._image_point(event.position())
        if point is not None:
            self.clicked.emit(point, event.globalPosition().toPoint())


class EntityAnnotationDialog(QDialog):
    def __init__(self, repository: CorpusRepository, parent=None, suggestion_provider=None) -> None:
        super().__init__(parent)
        self.repository = repository
        # 3B-5E : suggestions calculées à la demande (jamais pour TEST avant vérité).
        self._provider = suggestion_provider
        self._assist_preference = True
        self.suggestion = None            # EntitySuggestion affichée pour la frame courante
        self._snapshot: dict | None = None  # suggestion d'origine figée, enregistrée avec la vérité
        self.origin: dict[int, str] = {}  # cell_id -> "suggested" | "human"
        self.selected_cell: int | None = None
        self._drafts: dict[str, dict] = {}
        self._dirty = False
        self._compare_revealed: set[str] = set()
        self._annotation = None
        self._document: dict = {}
        self.entries = entity_entries(repository)
        self.index = 0
        self.labels: dict[int, str] = {}   # cell_id -> PLAYER | E1… | ENEMY | EMPTY
        self.cells: list[dict] = []
        self.sample: list[int] = []                # cellules tirées (indépendantes des prédictions)
        self.sample_labels: dict[int, str] = {}    # cell_id -> EMPTY | OCCUPIED | UNKNOWN
        self.image: np.ndarray | None = None
        self.offset = (0, 0)               # recadrage sur l'arène (coordonnées de la frame)
        self.setWindowTitle("Annoter les entités — vérité humaine par cell ID")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1500, 950)
        root = QVBoxLayout(self)
        self.header = QLabel()
        self.header.setWordWrap(True)
        root.addWidget(self.header)
        self.sequence = QComboBox()
        self.sequence.setToolTip("⏳ = frames encore à annoter ; les combats à terminer sont en tête, "
                                 "du plus récent au plus ancien.")
        self._sequence_ids = {e.observation_id: repository.tracking_sequence_id(e) for e in self.entries}
        self._fill_sequences()
        # Ouvrir directement sur le travail à faire : 1ʳᵉ frame non annotée du combat à terminer le
        # plus récent ; si tout est annoté, comportement historique (première frame).
        pending = self._pending_frame()
        if pending is not None:
            self.index = pending
        root.addWidget(self.sequence)
        assist_row = QHBoxLayout()
        self.assist = QCheckBox("Assistance activée (suggestions du logiciel)")
        self.assist.setChecked(True)
        self.assist.toggled.connect(self._assist_toggled)
        self.assist_state = QLabel()
        self.assist_state.setWordWrap(True)
        assist_row.addWidget(self.assist)
        assist_row.addWidget(self.assist_state, 1)
        root.addLayout(assist_row)
        body = QHBoxLayout()
        self.canvas = _CellCanvas()
        body.addWidget(self.canvas, 4)
        side = QVBoxLayout()
        self.zoom = QLabel("Zoom local")
        self.zoom.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom.setFixedSize(360, 240)
        self.zoom.setStyleSheet("background:#0f1510; border:1px solid rgba(143,209,79,36);")
        side.addWidget(self.zoom)
        self.hover = QLabel("Survolez une cellule projetée.")
        side.addWidget(self.hover)
        self.player_hidden = QCheckBox("Joueur non visible (UNKNOWN)")
        self.phase = QComboBox()
        self.phase.addItem("Phase non précisée", None)
        for key in sorted(ENTITY_FRAME_PHASES):
            self.phase.addItem(PHASE_LABELS.get(key, key), key)
        self.occlusion = QCheckBox("Occlusion / sprite masquant un marqueur")
        self.tactical = QCheckBox("Mode tactique activé manuellement")
        self.tactical.setTristate(True)
        self.tactical.setToolTip("Coché : oui ; décoché : non ; tiret : inconnu.")
        self.occluded_tracks = QLineEdit()
        self.occluded_tracks.setPlaceholderText("Ennemis présents mais non localisables : E2, E3")
        self.tracking_confirmed = QCheckBox("Mêmes E1/E2… pour les mêmes ennemis dans ce combat")
        self.tracking_confirmed.setToolTip(
            "Cochez après avoir comparé cette frame aux autres frames du combat. "
            "Laissez décoché si les numéros ont été attribués à nouveau sur chaque image.")
        self._tracking_source: str | None = None
        # La confirmation de suivi appartient à la SÉQUENCE (tracking_sequence_id), pas à une frame.
        # Valeur locale non enregistrée par séquence : conservée pendant la navigation entre ses
        # frames ; « dirty » tant qu'elle diffère de la valeur enregistrée dans le corpus.
        self._pending_tracking: dict[str, bool] = {}
        self._invalidated: set[str] = set()
        self._current_sequence: str | None = None
        self.tracking_confirmed.toggled.connect(self._tracking_toggled)
        self.tracking_state = QLabel()
        self.tracking_state.setWordWrap(True)
        self.save_sequence = QPushButton("Enregistrer la confirmation de la séquence")
        self.save_sequence.clicked.connect(self._save_sequence)
        self._sequence_jobs = None
        self._sequence_saving: str | None = None

        for widget in (self.player_hidden, QLabel("Phase de la frame :"), self.phase, self.occlusion,
                       self.tactical, QLabel("Ennemis occultés :"), self.occluded_tracks,
                       self.tracking_confirmed, self.tracking_state, self.save_sequence):
            side.addWidget(widget)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        side.addWidget(self.summary)
        self.compare = QLabel()
        self.compare.setWordWrap(True)
        self.compare.setTextFormat(Qt.TextFormat.RichText)
        self.compare.setStyleSheet("background:#121813; border:1px solid rgba(255,255,255,20); border-radius:8px; padding:6px;")
        side.addWidget(self.compare)
        self.compare_button = QPushButton("Comparer avec la prédiction")
        self.compare_button.clicked.connect(self._reveal_prediction)
        side.addWidget(self.compare_button)
        self.review_stats = QLabel()
        self.review_stats.setWordWrap(True)
        side.addWidget(self.review_stats)
        side.addStretch()
        body.addLayout(side, 1)
        root.addLayout(body, 1)
        self.status = QLabel("Clic sur une cellule : JOUEUR, ENNEMI, VIDE confirmé ou INCONNU. "
                             "Raccourcis : Entrée = tout est correct · ←/→ = frame · Suppr = effacer la "
                             "cellule choisie · 1…8 = E1…E8 · P = joueur · V = cases jaunes restantes vides.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        buttons = QHBoxLayout()
        previous, following = QPushButton("◀ Précédente"), QPushButton("Suivante ▶")
        clear = QPushButton("Tout effacer")
        save = QPushButton("Confirmer cette frame")
        self.accept_all_button = QPushButton("✓ Tout est correct")
        self.accept_all_button.setObjectName("primary")
        self.accept_all_button.setToolTip("Accepte les suggestions affichées comme vérité humaine (Entrée)")
        close = QPushButton("Fermer")
        for button in (previous, following, clear, save, self.accept_all_button, close):
            button.setAutoDefault(False)
            button.setDefault(False)
        for button in (previous, following, clear):
            buttons.addWidget(button)
        buttons.addStretch()
        buttons.addWidget(save)
        buttons.addWidget(self.accept_all_button)
        buttons.addWidget(close)
        root.addLayout(buttons)
        previous.clicked.connect(lambda: self._move(-1))
        following.clicked.connect(lambda: self._move(1))
        clear.clicked.connect(self._clear)
        save.clicked.connect(self._save)
        self.accept_all_button.clicked.connect(self._accept_all)
        close.clicked.connect(self.accept)
        self.canvas.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.canvas.hovered.connect(self._hover)
        self.canvas.clicked.connect(self._menu)
        self.player_hidden.toggled.connect(self._player_visibility_changed)
        self.occluded_tracks.textEdited.connect(lambda _text: self._invalidate_tracking())
        self.sequence.currentIndexChanged.connect(self._select_sequence)
        self._show()

    # ------------------------------------------------------------------ liste des combats
    def _sequence_info(self) -> dict[str, dict]:
        """Par séquence : date, split, map, frames, frames annotées (lecture des fichiers du corpus)."""
        info: dict[str, dict] = {}
        for entry in self.entries:
            sequence_id = self._sequence_ids[entry.observation_id]
            row = info.setdefault(sequence_id, {"created": "", "split": None, "map": None, "frames": 0, "annotated": 0})
            try:
                document = self.repository.read_observation(entry)
                annotation = self.repository.read_annotation(entry.observation_id)
            except (OSError, ValueError):
                continue
            row["frames"] += 1
            row["annotated"] += int(bool(annotation and annotation.entities_confirmed))
            created = str(document.get("created_at") or "")
            if created and (not row["created"] or created < row["created"]):
                row["created"] = created
            row["split"] = frame_split(document) or row["split"]
            row["map"] = (document.get("grid_snapshot") or {}).get("map_id_declared")
        return info

    def _fill_sequences(self) -> None:
        info = self._sequence_info()
        order = sorted(info, key=lambda sid: (info[sid]["annotated"] >= info[sid]["frames"], info[sid]["created"]),
                       reverse=False)
        todo = sorted((sid for sid in order if info[sid]["annotated"] < info[sid]["frames"]),
                      key=lambda sid: info[sid]["created"], reverse=True)
        done = sorted((sid for sid in order if info[sid]["annotated"] >= info[sid]["frames"]),
                      key=lambda sid: info[sid]["created"], reverse=True)
        current = self.sequence.currentData()
        self.sequence.blockSignals(True)
        self.sequence.clear()
        for sequence_id in [*todo, *done]:
            row = info[sequence_id]
            when = row["created"][8:10] + "/" + row["created"][5:7] + " " + row["created"][11:16] if row["created"] else "?"
            state = (f"⏳ {row['annotated']}/{row['frames']} annotées" if row["annotated"] < row["frames"]
                     else f"✓ {row['frames']} frames annotées")
            split = (row["split"] or "non déclaré").upper()
            self.sequence.addItem(f"{state} · {when} · {split} · map {row['map']} · {sequence_id.split('|')[0]}",
                                  sequence_id)
        if current is not None:
            self.sequence.setCurrentIndex(max(0, self.sequence.findData(current)))
        self.sequence.blockSignals(False)
        self._sequence_rows = info

    def _pending_frame(self) -> int | None:
        """Index de la 1ʳᵉ frame non annotée du combat à terminer le plus récent."""
        info = getattr(self, "_sequence_rows", {})
        todo = [sid for sid, row in info.items() if row["annotated"] < row["frames"]]
        if not todo:
            return None
        newest = max(todo, key=lambda sid: info[sid]["created"])
        candidates = [(entry.frame_index, index) for index, entry in enumerate(self.entries)
                      if self._sequence_ids[entry.observation_id] == newest]
        for _frame, index in sorted(candidates):
            annotation = self.repository.read_annotation(self.entries[index].observation_id)
            if not (annotation and annotation.entities_confirmed):
                return index
        return None

    # ------------------------------------------------------------------ données
    def _frame_point(self, point) -> tuple[float, float] | None:
        """Point du canevas (arène recadrée) → coordonnées de la frame d'origine."""
        return None if point is None else (point[0] + self.offset[0], point[1] + self.offset[1])

    def _cell_at(self, point) -> dict | None:
        if point is None:
            return None
        best = None
        for cell in self.cells:
            polygon = np.asarray(cell["polygon"], np.float32)
            distance = cv2.pointPolygonTest(polygon, (float(point[0]), float(point[1])), True)
            if distance >= 0 and (best is None or distance > best[0]):
                best = (distance, cell)
        return best[1] if best else None

    def _show(self) -> None:
        if not self.entries:
            self.header.setText("Aucune observation avec grille GameData projetée. Enregistrez des observations "
                                "en « Vision réelle » avec une map déclarée et une projection calibrée.")
            return
        # Relire l'entrée dans le manifeste : une frame enregistrée depuis l'ouverture de la fenêtre
        # porte maintenant son chemin d'annotation (sinon « Précédente » l'afficherait vide).
        entry = self.entries[self.index] = self.repository.get_entry(self.entries[self.index].observation_id)
        document = self.repository.read_observation(entry)
        self.cells = projected_cells(document)
        self.image = cv2.imread(str(self.repository.resolve(entry.paths["frame"])), cv2.IMREAD_COLOR)
        annotation = self.repository.read_annotation(entry)
        size = None if self.image is None else (self.image.shape[1], self.image.shape[0])
        self.sample = [item["cell_id"] for item in sample_cells(self.cells, entry.observation_id, frame_size=size)]
        self.sample_labels = {}
        if annotation is not None and annotation.sampled_cells_version == SAMPLE_VERSION:
            self.sample_labels = {int(item["cell_id"]): str(item["label"]) for item in annotation.sampled_cells_truth
                                  if int(item["cell_id"]) in self.sample}
        self.labels = {}
        self.origin = {}
        self.suggestion = None
        self._snapshot = None
        self.selected_cell = None
        self._dirty = False
        self._document = document
        if annotation is not None and annotation.entities_confirmed:
            if annotation.player_cell_id_truth is not None:
                self.labels[annotation.player_cell_id_truth] = "PLAYER"
            for item in annotation.enemy_cells_truth:
                self.labels[int(item["cell_id"])] = str(item.get("track_id") or "ENEMY")
            for cell_id in annotation.empty_confirmed_cells:
                self.labels[cell_id] = "EMPTY"
            self.origin = {cell: "human" for cell in self.labels}
        confirmed = annotation is not None and annotation.entities_confirmed
        self._annotation = annotation
        self.player_hidden.blockSignals(True)
        self.player_hidden.setChecked(bool(annotation and annotation.player_visibility == "NOT_VISIBLE"))
        self.player_hidden.blockSignals(False)
        allowed = assistance_allowed(document)
        self.assist.blockSignals(True)
        self.assist.setEnabled(allowed)
        self.assist.setChecked(allowed and self._assist_preference)
        self.assist.blockSignals(False)
        draft = self._drafts.get(entry.observation_id)
        if draft is not None:
            self.labels, self.origin = dict(draft["labels"]), dict(draft["origin"])
            self.sample_labels, self._snapshot = dict(draft["sample_labels"]), draft["snapshot"]
            self.suggestion = draft["suggestion"]
            self.player_hidden.blockSignals(True)
            self.player_hidden.setChecked(draft["player_hidden"])
            self.player_hidden.blockSignals(False)
            self._dirty = True
        elif not confirmed and allowed and self.assist.isChecked():
            self._prefill(entry)
        self.phase.setCurrentIndex(max(0, self.phase.findData(annotation.frame_phase if annotation else None)))
        self.occlusion.setChecked(bool(annotation and annotation.occlusion))
        tactical = annotation.tactical_mode if annotation else None
        self.tactical.setCheckState(Qt.CheckState.PartiallyChecked if tactical is None else
                                   Qt.CheckState.Checked if tactical else Qt.CheckState.Unchecked)
        self.occluded_tracks.setText(", ".join(annotation.enemy_occluded_tracks) if annotation else "")
        self._tracking_source = annotation.tracking_identity_source if annotation else None
        sequence_id = self._sequence_ids[entry.observation_id]
        self.sequence.blockSignals(True)
        self.sequence.setCurrentIndex(self.sequence.findData(sequence_id))
        self.sequence.blockSignals(False)
        self._current_sequence = sequence_id
        saved = self.repository.tracking_sequence_confirmed(sequence_id)
        if self._pending_tracking.get(sequence_id) == saved:
            self._pending_tracking.pop(sequence_id, None)   # plus rien à enregistrer
            self._invalidated.discard(sequence_id)
        pending = self._pending_tracking.get(sequence_id)
        self.tracking_confirmed.blockSignals(True)
        self.tracking_confirmed.setChecked(pending if pending is not None else saved)
        self.tracking_confirmed.blockSignals(False)
        self._update_tracking_state()
        grid = document.get("grid_snapshot") or {}
        self.header.setText(
            f"Frame {self.index + 1}/{len(self.entries)} · <b>{entry.session_id}</b> · frame {entry.frame_index} · "
            f"map {grid.get('map_id_declared')} · split <b>{(frame_split(document) or 'non déclaré').upper()}</b> · "
            + ("<b>vérité entités confirmée</b>" if confirmed else
               "suggestions à vérifier" if self.suggestion is not None else "non annotée"))
        if allowed:
            self.assist_state.setText("Suggestions affichées en violet « ? » : elles ne comptent qu'après votre "
                                      "confirmation." if self.assist.isChecked() else "Assistance coupée : saisie manuelle.")
        else:
            self.assist_state.setText("<b>Split TEST ou non déclaré : annotation aveugle.</b> Aucune prédiction "
                                      "n'est montrée avant d'avoir confirmé la vérité de la frame.")
        self._render()
        self._update_compare()
        self._update_stats()

    # ------------------------------------------------------------------ assistance 3B-5E
    def _suggestions(self):
        if self._provider is None:
            self._provider = DetectorSuggestionProvider()
        return self._provider

    def _suggestion_for(self, entry):
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                return self._suggestions().for_sequence(self.repository, self._sequence_ids[entry.observation_id]).get(
                    entry.observation_id)
            finally:
                QApplication.restoreOverrideCursor()
        except (OSError, ValueError) as exc:
            self.status.setText(f"Suggestions indisponibles : {exc}")
            return None

    def _prefill(self, entry) -> None:
        """Préremplit depuis la suggestion (TRAIN/VALIDATION seulement) ; rien n'est encore une vérité."""
        if not assistance_allowed(self._document):  # garde-fou : jamais sur TEST
            return
        suggestion = self._suggestion_for(entry)
        if suggestion is None:
            return
        self.suggestion = suggestion
        self._snapshot = suggestion.snapshot(sampled=list(self.sample))
        if suggestion.player_cell is not None:
            self.labels[suggestion.player_cell] = "PLAYER"
        for cell, track in suggestion.enemies:
            self.labels.setdefault(cell, track or "ENEMY")
        for cell in self.sample:
            if cell in suggestion.free_cells and cell not in self.labels:
                self.labels[cell] = "EMPTY"
        self.origin = {cell: "suggested" for cell in self.labels}

    def _assist_toggled(self, checked: bool) -> None:
        self._assist_preference = checked
        if not self.entries:
            return
        entry = self.entries[self.index]
        if self._annotation is not None and self._annotation.entities_confirmed:
            self._update_compare()
            return
        if checked and not self._dirty:
            self.labels, self.origin = {}, {}
            self._prefill(entry)
        elif not checked:
            self.labels = {cell: label for cell, label in self.labels.items() if self.origin.get(cell) == "human"}
            self.origin = {cell: "human" for cell in self.labels}
            self.suggestion, self._snapshot = None, None
        self._render()
        self._update_compare()

    def _current_truth(self) -> tuple[int | None, list[tuple[int, str | None]]]:
        players = [cell for cell, label in self.labels.items() if label == "PLAYER"]
        enemies = [(cell, None if label == "ENEMY" else label) for cell, label in self.labels.items()
                   if label not in ("PLAYER", "EMPTY")]
        return (players[0] if players else None), sorted(enemies)

    def _reveal_prediction(self) -> None:
        """TEST : prédiction montrée seulement après la vérité enregistrée."""
        if not self.entries or self._annotation is None or not self._annotation.entities_confirmed:
            return
        entry = self.entries[self.index]
        suggestion = self._suggestion_for(entry)
        if suggestion is not None:
            self.suggestion = suggestion
            self._compare_revealed.add(entry.observation_id)
        self._update_compare()

    @staticmethod
    def _describe(player, enemies) -> str:
        items = [f"Joueur {player if player is not None else '—'}"]
        items += [f"{track or 'ennemi'} {cell}" for cell, track in sorted(enemies, key=lambda item: (item[1] or "~", item[0]))]
        return " · ".join(items)

    def _update_compare(self) -> None:
        if not self.entries:
            return
        entry = self.entries[self.index]
        confirmed = self._annotation is not None and self._annotation.entities_confirmed
        allowed = assistance_allowed(self._document)
        blind_locked = not allowed and not confirmed
        self.compare_button.setVisible(not allowed and confirmed and entry.observation_id not in self._compare_revealed)
        self.accept_all_button.setEnabled(self.suggestion is not None and not confirmed)
        if blind_locked:
            self.compare.setText("<b>Prédiction masquée</b> (annotation aveugle). Confirmez la vérité de la "
                                 "frame pour pouvoir la comparer au logiciel.")
            return
        snapshot = (self._annotation.suggestion_snapshot if confirmed and self._annotation.suggestion_snapshot
                    else self.suggestion.snapshot() if self.suggestion is not None else None)
        if snapshot is None:
            self.compare.setText("Aucune suggestion pour cette frame." if allowed else
                                 "Vérité enregistrée. « Comparer avec la prédiction » pour voir le logiciel.")
            return
        software = self._describe(snapshot.get("player_cell"),
                                  [(item["cell_id"], item.get("track_id")) for item in snapshot.get("enemies", ())])
        player, enemies = self._current_truth()
        outcome = review(snapshot, player, enemies)
        lines = []
        if outcome["player"] and outcome["player"] != "confirmed":
            lines.append(f"Joueur : {snapshot.get('player_cell')} → {player} ({STATUS_TEXT[outcome['player']]})")
        for row in outcome["enemies"]:
            if row["status"] == "confirmed":
                continue
            before = f"{row['suggested'][1] or 'ennemi'} {row['suggested'][0]}" if row["suggested"] else "—"
            after = f"{row['final'][1] or 'ennemi'} {row['final'][0]}" if row["final"] else "—"
            lines.append(f"{before} → {after} ({STATUS_TEXT[row['status']]})")
        state = ("vérité humaine enregistrée" if confirmed else "non confirmée : rien n'est encore une vérité")
        self.compare.setText(f"<b>LOGICIEL</b> : {software}<br><b>HUMAIN</b> ({state}) : "
                             f"{self._describe(player, enemies)}<br><b>DIFF</b> : "
                             + ("<br>".join(lines) if lines else "aucune — tout concorde ✓"))

    def _update_stats(self) -> None:
        annotations = []
        for item in self.entries:
            try:
                annotations.append(self.repository.read_annotation(item))
            except (OSError, ValueError):
                continue
        stats = review_statistics(annotations)
        assisted = sum(1 for item in self.entries if assistance_allowed(self.repository.read_observation(item)))
        p, e = stats["player"], stats["enemy"]
        self.review_stats.setText(
            f"<b>Revue assistée</b> (aide, pas un benchmark) — frames revues {stats['frames_reviewed']} / {assisted}"
            f" · entièrement correctes {stats['frames_all_correct']} · corrigées {stats['frames_corrected']}<br>"
            f"Joueur : correct direct {p['confirmed']} · corrigé {p['corrected'] + p['rejected'] + p['added']}<br>"
            f"Ennemis : correct direct {e['confirmed']} · corrigé {e['corrected']} · manqué {e['added']} · faux {e['rejected']}")

    def _render(self) -> None:
        if self.image is None:
            return
        output = self.image.copy()
        if self.cells:
            points = np.concatenate([np.asarray(cell["polygon"]) for cell in self.cells])
            margin = 60
            x0, y0 = np.maximum(points.min(axis=0).astype(int) - margin, 0)
            x1, y1 = points.max(axis=0).astype(int) + margin
            self.offset = (int(x0), int(y0))
        else:
            x0 = y0 = 0
            y1, x1 = output.shape[:2]
            self.offset = (0, 0)
        cv2.polylines(output, [np.asarray(c["polygon"], np.int32) for c in self.cells], True, (90, 150, 190), 1,
                      cv2.LINE_AA)
        colors = {"PLAYER": (80, 235, 120), "EMPTY": (160, 160, 160)}
        for cell in self.cells:
            cell_id = int(cell["cell_id"])
            label = self.labels.get(cell_id)
            if label is None:
                continue
            suggested = self.origin.get(cell_id) == "suggested"
            color = SUGGESTED_COLOR if suggested else colors.get(label, (60, 70, 245))
            cv2.polylines(output, [np.asarray(cell["polygon"], np.int32)], True, color, 2 if suggested else 3,
                          cv2.LINE_AA)
            text = "P" if label == "PLAYER" else ("vide" if label == "EMPTY" else label)
            center = tuple(int(v) for v in cell["center"])
            cv2.putText(output, f"{'?' if suggested else ''}{text} {cell_id}", (center[0] - 22, center[1] + 5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)
        if self.selected_cell is not None:
            chosen = next((c for c in self.cells if int(c["cell_id"]) == self.selected_cell), None)
            if chosen is not None:
                cv2.polylines(output, [np.asarray(chosen["polygon"], np.int32)], True, (255, 255, 255), 1, cv2.LINE_AA)
        by_id = {int(cell["cell_id"]): cell for cell in self.cells}
        for cell_id in self.sample:
            decision = self._sample_decision(cell_id)
            polygon = np.asarray(by_id[cell_id]["polygon"], np.int32)
            cv2.polylines(output, [polygon], True, (0, 215, 255), 2 if decision else 3, cv2.LINE_AA)
            if cell_id not in self.labels:
                center = tuple(int(v) for v in by_id[cell_id]["center"])
                cv2.putText(output, SAMPLE_CAPTIONS.get(decision, "?"), (center[0] - 18, center[1] + 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 215, 255), 2, cv2.LINE_AA)
        output = np.ascontiguousarray(output[y0:y1, x0:x1])
        self.canvas.image_size = (output.shape[1], output.shape[0])
        self.canvas.setPixmap(bgr_to_pixmap(output).scaled(
            self.canvas.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        players = [cell for cell, label in self.labels.items() if label == "PLAYER"]
        enemies = sorted((label, cell) for cell, label in self.labels.items() if label not in ("PLAYER", "EMPTY"))
        pending = sum(origin == "suggested" for origin in self.origin.values())
        self.summary.setText(
            (f"{pending} suggestion(s) non confirmée(s) (violet « ? »)\n" if pending else "")
            + f"Joueur : {players[0] if players else ('non visible' if self.player_hidden.isChecked() else '—')}\n"
            f"Ennemis : {', '.join(f'{label}@{cell}' for label, cell in enemies) or '—'}\n"
            f"Vides confirmées : {sum(label == 'EMPTY' for label in self.labels.values())}\n"
            f"Échantillon (jaune) : {sum(bool(self._sample_decision(c)) for c in self.sample)}/{len(self.sample)} décidées")

    def _sample_decision(self, cell_id: int) -> str | None:
        """Décision humaine sur une cellule tirée ; une entité annotée y vaut OCCUPÉE."""
        label = self.labels.get(cell_id)
        if label == "EMPTY":
            return "EMPTY"
        if label is not None:
            return "OCCUPIED"
        return self.sample_labels.get(cell_id)

    def _hover(self, point) -> None:
        point = self._frame_point(point)
        cell = self._cell_at(point)
        if point is None or self.image is None:
            return
        self.hover.setText(f"Cell ID {cell['cell_id']}" if cell else "Hors grille projetée")
        x, y = int(point[0]), int(point[1])
        crop = self.image[max(0, y - 60):y + 60, max(0, x - 90):x + 90]
        if crop.size:
            self.zoom.setPixmap(bgr_to_pixmap(cv2.resize(crop, None, fx=2, fy=2,
                                                         interpolation=cv2.INTER_NEAREST)))

    def _menu(self, point, global_position: QPoint) -> None:
        cell = self._cell_at(self._frame_point(point))
        if cell is None:
            return
        cell_id = int(cell["cell_id"])
        self.selected_cell = cell_id
        menu = QMenu(self)
        actions = {}
        if cell_id in self.sample:
            for caption, decision in (("Échantillon : VIDE", "SAMPLE_EMPTY"),
                                      ("Échantillon : OCCUPÉE (sans entité à nommer)", "SAMPLE_OCCUPIED"),
                                      ("Échantillon : INCONNU", "SAMPLE_UNKNOWN")):
                actions[menu.addAction(caption)] = decision
            menu.addSeparator()
        actions[menu.addAction("JOUEUR")] = "PLAYER"
        enemy_menu = menu.addMenu("ENNEMI")
        for label in ENEMY_LABELS:
            actions[enemy_menu.addAction(label)] = label
        actions[enemy_menu.addAction("Ennemi sans identifiant")] = "ENEMY"
        actions[menu.addAction("VIDE confirmé")] = "EMPTY"
        actions[menu.addAction("INCONNU (effacer)")] = None
        chosen = menu.exec(global_position)
        if chosen is None or chosen not in actions:
            return
        self._assign(cell_id, actions[chosen])

    def _assign(self, cell_id: int, label: str | None) -> None:
        self._dirty = True
        self.origin[cell_id] = "human"
        if label is not None and label.startswith("SAMPLE_"):
            decision = label.removeprefix("SAMPLE_")
            if decision == "EMPTY":
                self._assign(cell_id, "EMPTY")
                return
            if self.labels.get(cell_id) == "EMPTY":
                self.labels.pop(cell_id)
            self.sample_labels[cell_id] = decision
            self._render()
            return
        if label is None:
            self.sample_labels.pop(cell_id, None)
        if label != self.labels.get(cell_id) and (
                label in ENEMY_LABELS or self.labels.get(cell_id) in ENEMY_LABELS):
            self._invalidate_tracking()
        if label is None:
            self.labels.pop(cell_id, None)
            self.origin.pop(cell_id, None)
        else:
            if label == "PLAYER":
                # Exactement 0 ou 1 joueur : l'ancienne cellule joueur est libérée.
                self.labels = {cell: value for cell, value in self.labels.items() if value != "PLAYER"}
                self.player_hidden.setChecked(False)
            if label in ENEMY_LABELS:
                self.labels = {cell: value for cell, value in self.labels.items() if value != label}
            self.labels[cell_id] = label
        self._render()
        if hasattr(self, "compare"):
            self._update_compare()

    def _player_visibility_changed(self, hidden: bool) -> None:
        self._dirty = True
        if hidden:
            self.labels = {cell: value for cell, value in self.labels.items() if value != "PLAYER"}
        self._render()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        if hasattr(self, "canvas"):
            self._render()

    def _clear(self) -> None:
        if any(label in ENEMY_LABELS for label in self.labels.values()):
            self._invalidate_tracking()
        self.labels = {}
        self.origin = {}
        self.sample_labels = {}
        self._dirty = True
        self._render()
        self._update_compare()

    def _keep_draft(self) -> None:
        """Modifications non confirmées conservées pendant la navigation (jamais une vérité)."""
        if not self.entries or not self._dirty:
            return
        self._drafts[self.entries[self.index].observation_id] = {
            "labels": dict(self.labels), "origin": dict(self.origin), "sample_labels": dict(self.sample_labels),
            "snapshot": self._snapshot, "suggestion": self.suggestion,
            "player_hidden": self.player_hidden.isChecked()}

    def _move(self, step: int) -> None:
        if 0 <= self.index + step < len(self.entries):
            target = self._sequence_ids[self.entries[self.index + step].observation_id]
            if target != self._current_sequence and not self._leave_sequence_ok():
                return
            self._keep_draft()
            self.index += step
            self._show()

    def _select_sequence(self, _index: int) -> None:
        selected = self.sequence.currentData()
        if selected != self._current_sequence and not self._leave_sequence_ok():
            self.sequence.blockSignals(True)
            self.sequence.setCurrentIndex(self.sequence.findData(self._current_sequence))
            self.sequence.blockSignals(False)
            return
        self._keep_draft()
        self.index = next((i for i, e in enumerate(self.entries)
                           if self._sequence_ids[e.observation_id] == selected), self.index)
        self._show()

    # ------------------------------------------------------------------ confirmation de suivi
    def tracking_confirmation_dirty(self, sequence_id: str | None = None) -> bool:
        """Vrai si la valeur locale de la séquence diffère de la valeur enregistrée."""
        sequence_id = sequence_id or self._current_sequence
        if sequence_id is None or sequence_id not in self._pending_tracking:
            return False
        return self._pending_tracking[sequence_id] != self.repository.tracking_sequence_confirmed(sequence_id)

    def _tracking_toggled(self, checked: bool) -> None:
        if self._current_sequence is None:
            return
        self._pending_tracking[self._current_sequence] = checked
        if checked:
            self._invalidated.discard(self._current_sequence)
        self._update_tracking_state()

    def _invalidate_tracking(self) -> None:
        """Une identité E1/E2/E3 change : la confirmation de toute la séquence tombe, visiblement."""
        sequence_id = self._current_sequence
        if sequence_id is None:
            return
        was_confirmed = self.tracking_confirmed.isChecked() or self.repository.tracking_sequence_confirmed(sequence_id)
        self.tracking_confirmed.blockSignals(True)
        self.tracking_confirmed.setChecked(False)
        self.tracking_confirmed.blockSignals(False)
        self._pending_tracking[sequence_id] = False
        self._tracking_source = None
        if was_confirmed:
            self._invalidated.add(sequence_id)
            self.status.setText(TRACKING_INVALIDATED)
        self._update_tracking_state()

    def _update_tracking_state(self) -> None:
        sequence_id = self._current_sequence
        if sequence_id is None:
            self.tracking_state.setText("")
            return
        saved = self.repository.tracking_sequence_confirmed(sequence_id)
        if self.tracking_confirmation_dirty(sequence_id):
            if sequence_id in self._invalidated:
                text = TRACKING_INVALIDATED + " Enregistrez la frame modifiée, puis reconfirmez la séquence."
            elif self._pending_tracking[sequence_id]:
                text = "Confirmation cochée, non enregistrée pour cette séquence."
            else:
                text = "Retrait de la confirmation non enregistré pour cette séquence."
        else:
            text = ("Confirmation de suivi enregistrée pour toute la séquence." if saved
                    else "Séquence non confirmée pour le suivi.")
        self.tracking_state.setText(text)

    def _ask_unsaved_tracking(self) -> str:
        """« save », « discard » ou « cancel » (isolé pour les tests)."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Confirmation de suivi")
        box.setText(UNSAVED_TRACKING)
        save = box.addButton("Enregistrer", QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton("Ignorer", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Annuler", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        return "save" if clicked is save else "discard" if clicked is discard else "cancel"

    def _leave_sequence_ok(self) -> bool:
        sequence_id = self._current_sequence
        if sequence_id is None or not self.tracking_confirmation_dirty(sequence_id):
            return True
        choice = self._ask_unsaved_tracking()
        if choice == "cancel":
            return False
        if choice == "discard":
            self._pending_tracking.pop(sequence_id, None)
            self._invalidated.discard(sequence_id)
            return True
        return self._save_sequence_now(sequence_id)

    def _sequence_save_blocker(self, sequence_id: str, confirmed: bool) -> str | None:
        if not confirmed and not self.repository.tracking_sequence_confirmed(sequence_id):
            return ("Cochez « Mêmes E1/E2… pour les mêmes ennemis dans ce combat » avant d'enregistrer : "
                    "aucune confirmation n'a été enregistrée.")
        current = self.repository.read_annotation(self.entries[self.index])
        visible = {(cell, None if label == "ENEMY" else label) for cell, label in self.labels.items()
                   if label not in ("PLAYER", "EMPTY")}
        saved = {(int(e["cell_id"]), e.get("track_id")) for e in current.enemy_cells_truth} if current else set()
        occluded = tuple(v.strip().upper() for v in self.occluded_tracks.text().split(",") if v.strip())
        if visible != saved or (current and occluded != current.enemy_occluded_tracks):
            return "Enregistrez les modifications de cette frame avant de confirmer la séquence."
        return None

    def _save_sequence_now(self, sequence_id: str) -> bool:
        """Enregistrement synchrone (sortie de séquence) ; atomique côté dépôt."""
        confirmed = self._pending_tracking.get(sequence_id, self.tracking_confirmed.isChecked())
        blocker = self._sequence_save_blocker(sequence_id, confirmed)
        if blocker:
            self.status.setText(blocker)
            return False
        try:
            count = self.repository.confirm_tracking_sequence(sequence_id, confirmed=confirmed)
        except (OSError, ValueError) as exc:
            self.status.setText(f"Confirmation non enregistrée : {exc}")
            return False
        self._pending_tracking.pop(sequence_id, None)
        self._invalidated.discard(sequence_id)
        self.status.setText(f"{sequence_id} : confirmation {'enregistrée' if confirmed else 'retirée'} "
                            f"pour la séquence ({count} annotations).")
        self._update_tracking_state()
        return True

    def _save_sequence(self) -> None:
        if not self.entries:
            return
        sequence_id = self._current_sequence
        blocker = self._sequence_save_blocker(sequence_id, self.tracking_confirmed.isChecked())
        if blocker:
            self.status.setText(blocker)
            if blocker.startswith("Cochez"):
                QMessageBox.warning(self, "Confirmation non cochée", blocker)
            return
        from combatbot.ui.jobs import JobRunner
        if self._sequence_jobs is None:
            self._sequence_jobs = JobRunner()
            self._sequence_jobs.all_done.connect(self._sequence_done)
        confirmed = self.tracking_confirmed.isChecked()
        self._sequence_saving = sequence_id
        self.setEnabled(False)
        self._sequence_jobs.submit(
            lambda: self.repository.confirm_tracking_sequence(sequence_id, confirmed=confirmed),
            lambda count: self.status.setText(f"{sequence_id} : confirmation {'enregistrée' if confirmed else 'retirée'} "
                                             f"pour la séquence ({count} annotations)."),
            lambda error: self.status.setText(f"Confirmation non enregistrée : {error}"))

    def _sequence_done(self) -> None:
        self.setEnabled(True)
        if self._sequence_saving is not None and self.repository.tracking_sequence_confirmed(
                self._sequence_saving) == self._pending_tracking.get(self._sequence_saving):
            # Enregistré : l'état affiché redevient celui du corpus. Un échec laisse l'état « dirty ».
            self._pending_tracking.pop(self._sequence_saving, None)
            self._invalidated.discard(self._sequence_saving)
        self._sequence_saving = None
        self._show()

    def reject(self) -> None:
        if self._sequence_jobs and self._sequence_jobs.active:
            self.status.setText("Enregistrement de la séquence en cours…")
            return
        if not self._leave_sequence_ok():
            return
        super().reject()

    def accept(self) -> None:
        if self._sequence_jobs and self._sequence_jobs.active:
            return
        if not self._leave_sequence_ok():
            return
        super().accept()

    def _save(self) -> None:
        if not self.entries:
            return
        players = [cell for cell, label in self.labels.items() if label == "PLAYER"]
        if not players and not self.player_hidden.isChecked():
            QMessageBox.warning(self, "Annotation incomplète",
                                "Désignez la cellule du joueur ou cochez « Joueur non visible ».")
            return
        undecided = [cell for cell in self.sample if not self._sample_decision(cell)]
        if undecided:
            QMessageBox.warning(self, "Échantillon incomplet",
                                f"Choisissez VIDE, OCCUPÉE ou INCONNU pour les {len(undecided)} cellule(s) jaunes "
                                f"restantes : {', '.join(map(str, undecided))}.")
            return
        enemies = [(cell, None if label == "ENEMY" else label) for cell, label in self.labels.items()
                   if label not in ("PLAYER", "EMPTY")]
        occluded = [value.strip().upper() for value in self.occluded_tracks.text().split(",") if value.strip()]
        entry = self.entries[self.index]
        # Provenance : suggestion d'origine figée (celle affichée, ou celle d'une revue antérieure).
        previous = self._annotation.suggestion_snapshot if self._annotation is not None else None
        snapshot = self._snapshot or previous
        if snapshot is not None:
            outcome = review(snapshot, players[0] if players else None, sorted(enemies))
            mode = outcome["mode"]
        else:
            outcome = None
            mode = "manual" if assistance_allowed(self._document) else "manual_blind"
        try:
            self.repository.confirm_entities(
                entry.observation_id, player_cell_id=players[0] if players else None,
                player_visible=bool(players), enemies=enemies, occluded_tracks=occluded,
                empty_cells=[cell for cell, label in self.labels.items() if label == "EMPTY"],
                frame_phase=self.phase.currentData(), occlusion=self.occlusion.isChecked() or None,
                tactical_mode=(None if self.tactical.checkState() == Qt.CheckState.PartiallyChecked
                               else self.tactical.checkState() == Qt.CheckState.Checked),
                sampled_cells=[(cell, self._sample_decision(cell)) for cell in self.sample],
                sampled_cells_version=SAMPLE_VERSION, annotation_mode=mode, suggestion_snapshot=snapshot,
                suggestion_review=outcome, confirmed_by="user")
        except ValueError as exc:
            QMessageBox.warning(self, "Annotation invalide", str(exc))
            return
        self._drafts.pop(entry.observation_id, None)
        self._dirty = False
        self._fill_sequences()
        verdict = {"assisted_confirmed": "suggestions acceptées telles quelles ✓",
                   "assisted_corrected": "suggestions corrigées ✎"}.get(mode, "saisie manuelle")
        self.status.setText(f"{entry.observation_id} : vérité entités confirmée ({verdict}).")
        if self.index + 1 < len(self.entries):
            self._move(1)
        else:
            self._show()

    def _ask_remaining_empty(self, count: int) -> bool:
        """Isolé pour les tests : confirmation humaine que les cases jaunes restantes sont vides."""
        answer = QMessageBox.question(
            self, "Cases jaunes", f"Les {count} case(s) jaunes non décidées sont-elles toutes VIDES ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes)
        return answer == QMessageBox.StandardButton.Yes

    def _mark_remaining_empty(self) -> None:
        for cell in self.sample:
            if not self._sample_decision(cell):
                self._assign(cell, "EMPTY")

    def _accept_all(self) -> None:
        """« Tout est correct » : les suggestions affichées deviennent la vérité humaine de la frame."""
        if not self.entries or self.suggestion is None or (self._annotation is not None
                                                           and self._annotation.entities_confirmed):
            return
        undecided = [cell for cell in self.sample if not self._sample_decision(cell)]
        if undecided:
            if not self._ask_remaining_empty(len(undecided)):
                self.status.setText("Décidez les cases jaunes restantes, puis validez.")
                return
            self._mark_remaining_empty()
        self._save()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - API Qt
        key = event.key()
        if isinstance(self.focusWidget(), QLineEdit) and key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return super().keyPressEvent(event)
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            (self._accept_all if self.accept_all_button.isEnabled() else self._save)()
        elif key == Qt.Key.Key_Left:
            self._move(-1)
        elif key == Qt.Key.Key_Right:
            self._move(1)
        elif key == Qt.Key.Key_Delete and self.selected_cell is not None:
            self._assign(self.selected_cell, None)
        elif Qt.Key.Key_1 <= key <= Qt.Key.Key_8 and self.selected_cell is not None:
            self._assign(self.selected_cell, f"E{key - Qt.Key.Key_0}")
        elif key == Qt.Key.Key_P and self.selected_cell is not None:
            self._assign(self.selected_cell, "PLAYER")
        elif key == Qt.Key.Key_V:
            self._mark_remaining_empty()
        else:
            super().keyPressEvent(event)
            return
        self._update_compare()
