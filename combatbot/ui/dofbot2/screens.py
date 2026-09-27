"""Écrans 1 à 4 du handoff DofBot2 : démarrage, connexion, choix et création de profil."""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QElapsedTimer, QIODevice, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage, QKeyEvent
from PySide6.QtWidgets import (
    QButtonGroup, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

from combatbot.storage import Storage
from combatbot.ui.dofbot2 import theme as t
from combatbot.ui.dofbot2.game_windows import GameWindow, discover_windows, window_thumbnail
from combatbot.ui.dofbot2.profiles import (
    DEFAULT_PROFILE_NAME, MAX_NAME_LENGTH, ProfileEntry, create_profile, list_profile_entries,
)
from combatbot.ui.dofbot2.widgets import (
    Avatar, AvatarSwatch, FlowLayout, LogoBadge, NewProfileCard, Placeholder, ProfileCard, ThinProgress,
    UploadTile, WindowCard,
)
from combatbot.ui.images import bgr_to_pixmap
from combatbot.ui.jobs import JobRunner


SPLASH_DURATION_MS = 1800
WINDOW_REFRESH_MS = 3000
AVATAR_IMAGE_SIZE = 256


def _label(text: str, name: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _discard(widget: QWidget) -> None:
    """Retire un widget tout de suite : deleteLater seul le laisse affiché jusqu'au prochain tour de boucle."""
    parent = widget.parentWidget()
    if parent is not None and parent.layout() is not None:
        parent.layout().removeWidget(widget)
    widget.hide()
    widget.deleteLater()


def _button(text: str, name: str) -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(name)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


class SplashScreen(QWidget):
    """Logo, barre de progression ~1,8 s, puis ``finished`` ; un clic passe l'écran."""

    finished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("d2Screen")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Cliquer pour passer")
        layout = QVBoxLayout(self)
        layout.setSpacing(22)
        layout.addStretch(1)
        # Le halo déborde de 60px autour du logo : il est posé par-dessus un emplacement de 96px
        # pour garder l'écart de 22px de la maquette.
        self.logo_slot = QWidget()
        self.logo_slot.setFixedSize(96, 96)
        layout.addWidget(self.logo_slot, 0, Qt.AlignmentFlag.AlignHCenter)
        self.logo = LogoBadge(96, 28, 34, halo=True, parent=self)
        self.logo.lower()
        titles = QVBoxLayout()
        titles.setSpacing(6)
        title = _label("DofBot2", "d2H1")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        subtitle = _label("Votre assistant de farm, donjons et zones", "d2Lead")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        layout.addLayout(titles)
        layout.addSpacing(14)
        self.progress = ThinProgress()
        layout.addWidget(self.progress, 0, Qt.AlignmentFlag.AlignHCenter)
        self.message = _label("", "d2Mono")
        self.message.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.message)
        layout.addStretch(1)
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._done = False
        self._set_progress(0)

    @staticmethod
    def status_text(progress: int) -> str:
        if progress < 40:
            return "Chargement des données…"
        if progress < 80:
            return "Recherche des fenêtres du jeu…"
        return "Prêt"

    def start(self) -> None:
        self._done = False
        self._set_progress(0)
        self._clock.start()
        self._timer.start()

    def skip(self) -> None:
        self._timer.stop()
        self._finish()

    def _set_progress(self, value: int) -> None:
        self.progress.setValue(value)
        self.message.setText(self.status_text(value))

    def _tick(self) -> None:
        value = min(100, round(self._clock.elapsed() * 100 / SPLASH_DURATION_MS))
        self._set_progress(value)
        if value >= 100:
            self._timer.stop()
            QTimer.singleShot(300, self._finish)

    def _finish(self) -> None:
        if not self._done:
            self._done = True
            self.finished.emit()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self._place_logo()

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self._place_logo()

    def _place_logo(self) -> None:
        self.layout().activate()
        center = self.logo_slot.geometry().center()
        self.logo.move(center.x() - self.logo.width() // 2 + 1, center.y() - self.logo.height() // 2 + 1)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.button() == Qt.MouseButton.LeftButton:
            self.skip()
        super().mousePressEvent(event)


class ConnectScreen(QWidget):
    """Liste des fenêtres du jeu ouvertes ; « Se connecter » passe au choix du profil."""

    window_chosen = Signal(object)   # GameWindow, ou None pour continuer sans le jeu

    def __init__(self, jobs: JobRunner, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("d2Screen")
        self.jobs = jobs
        self.windows: list[GameWindow] = []
        self.preferred_hwnd: int | None = None
        self._thumbnails: dict[int, QImage | None] = {}
        self._pending_thumbnails: set[int] = set()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(72, 40, 72, 56)
        layout.setSpacing(56)

        left = QVBoxLayout()
        left.setSpacing(16)
        left.addStretch(1)
        illustration = Placeholder("illustration\nDofBot2 se relie à la fenêtre du jeu", 18)
        illustration.setFixedHeight(320)
        left.addWidget(illustration)
        hints = QGridLayout()
        hints.setHorizontalSpacing(12)
        for column, (title, text) in enumerate((
            ("1. Lancez le jeu", "Connectez votre personnage normalement."),
            ("2. Choisissez", "Sélectionnez sa fenêtre dans la liste."),
            ("3. C'est prêt", "DofBot2 lit l'écran de cette fenêtre."),
        )):
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(_label(title, "d2HintTitle"))
            box.addWidget(_label(text, "d2Hint", wrap=True))
            box.addStretch(1)
            hints.addLayout(box, 0, column)
            hints.setColumnStretch(column, 1)
        left.addLayout(hints)
        left.addStretch(1)
        layout.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(22)
        right.addStretch(1)
        heading = QVBoxLayout()
        heading.setSpacing(10)
        heading.addWidget(_label("Connectez-vous à la fenêtre du jeu", "d2H2", wrap=True))
        heading.addWidget(_label("DofBot2 a trouvé ces fenêtres ouvertes. Choisissez celle du personnage à utiliser.",
                                 "d2Lead", wrap=True))
        right.addLayout(heading)
        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(10)
        right.addLayout(self.list_layout)
        self.empty = _label("", "d2Empty", wrap=True)
        right.addWidget(self.empty)
        actions = QHBoxLayout()
        actions.setSpacing(16)
        self.connect_button = _button("Se connecter", "d2Primary")
        self.connect_button.clicked.connect(self._connect)
        self.refresh_button = _button("Actualiser la liste", "d2Link")
        self.refresh_button.clicked.connect(self.refresh)
        self.skip_button = _button("Continuer sans le jeu", "d2Link")
        self.skip_button.setToolTip("Configurer les profils maintenant et relier la fenêtre plus tard")
        self.skip_button.clicked.connect(lambda: self.window_chosen.emit(None))
        actions.addWidget(self.connect_button)
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.skip_button)
        actions.addStretch(1)
        right.addLayout(actions)
        right.addStretch(1)
        layout.addLayout(right, 1)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.group.buttonToggled.connect(lambda *_: self._update_actions())
        self.cards: list[WindowCard] = []
        self._timer = QTimer(self)
        self._timer.setInterval(WINDOW_REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._update_actions()

    # Rafraîchissement automatique tant que l'écran est visible : le jeu peut être lancé après DofBot2.
    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - API Qt
        self._timer.stop()
        super().hideEvent(event)

    @property
    def selected(self) -> GameWindow | None:
        card = self.group.checkedButton()
        if card is None:
            return None
        return next((window for window in self.windows if window.hwnd == card.hwnd), None)

    @staticmethod
    def _screens() -> list[tuple[int, int, int, int]]:
        # Qt garde l'origine native de chaque écran ; seule la taille est en pixels logiques.
        result = []
        for screen in QGuiApplication.screens():
            geometry, ratio = screen.geometry(), screen.devicePixelRatio()
            result.append((geometry.x(), geometry.y(), round(geometry.width() * ratio), round(geometry.height() * ratio)))
        return result

    def refresh(self) -> None:
        try:
            windows = discover_windows(self._screens())
            error = None
        except RuntimeError as exc:
            windows, error = [], str(exc)
        self.set_windows(windows, error)

    def set_windows(self, windows: list[GameWindow], error: str | None = None) -> None:
        previous = self.selected.hwnd if self.selected else self.preferred_hwnd
        if [(w.hwnd, w.title, w.meta) for w in windows] == [(w.hwnd, w.title, w.meta) for w in self.windows] \
                and self.cards:
            return  # rien de nouveau : on ne reconstruit pas la liste (pas de clignotement)
        self.windows = list(windows)
        for card in self.cards:
            self.group.removeButton(card)
            self.list_layout.removeWidget(card)
            _discard(card)
        self.cards = []
        for window in self.windows:
            card = WindowCard(window.hwnd, window.title, window.meta)
            card.setToolTip(window.raw_title)
            self.group.addButton(card)
            self.list_layout.addWidget(card)
            self.cards.append(card)
            self._apply_thumbnail(card)
        target = next((card for card in self.cards if card.hwnd == previous), self.cards[0] if self.cards else None)
        if target is not None:
            target.setChecked(True)
        if error:
            self.empty.setText(f"Impossible de lister les fenêtres : {error}")
        else:
            self.empty.setText("Aucune fenêtre du jeu n'est ouverte. Lancez DOFUS et connectez votre "
                               "personnage : la liste se met à jour toute seule.")
        self.empty.setVisible(not self.cards)
        self._update_actions()

    def _apply_thumbnail(self, card: WindowCard) -> None:
        if card.hwnd in self._thumbnails:
            card.set_thumbnail(self._thumbnails[card.hwnd])
            return
        if card.hwnd in self._pending_thumbnails:
            return
        self._pending_thumbnails.add(card.hwnd)
        hwnd = card.hwnd

        def success(image) -> None:
            self._pending_thumbnails.discard(hwnd)
            self._thumbnails[hwnd] = bgr_to_pixmap(image).toImage() if image is not None else None
            for current in self.cards:
                if current.hwnd == hwnd:
                    current.set_thumbnail(self._thumbnails[hwnd])

        def failure(_message: str) -> None:
            self._pending_thumbnails.discard(hwnd)
            self._thumbnails[hwnd] = None

        self.jobs.submit(lambda: window_thumbnail(hwnd), success, failure)

    def _update_actions(self) -> None:
        self.connect_button.setEnabled(self.selected is not None)
        self.skip_button.setVisible(not self.cards)

    def _connect(self) -> None:
        window = self.selected
        if window is not None:
            self.preferred_hwnd = window.hwnd
            self.window_chosen.emit(window)


class ProfileScreen(QWidget):
    """« Qui joue aujourd'hui ? » : une carte par profil, plus « Nouveau profil »."""

    profile_chosen = Signal(int)
    create_requested = Signal()

    def __init__(self, storage: Storage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("d2Screen")
        self.storage = storage
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        content = QWidget()
        content.setObjectName("d2Screen")
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 24, 40, 64)
        layout.setSpacing(36)
        layout.addStretch(1)
        heading = QVBoxLayout()
        heading.setSpacing(8)
        title = _label("Qui joue aujourd'hui ?", "d2H2")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lead = _label("Chaque profil garde ses sorts, ses donjons et ses réglages.", "d2Lead")
        lead.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        heading.addWidget(title)
        heading.addWidget(lead)
        layout.addLayout(heading)
        self.cards_host = QWidget()
        self.cards_host.setObjectName("d2Screen")
        self.flow = FlowLayout(self.cards_host, spacing=18, centered=True)
        layout.addWidget(self.cards_host)
        layout.addStretch(1)
        self.cards: list[ProfileCard] = []
        self.new_card = NewProfileCard()
        self.new_card.clicked.connect(self.create_requested)
        self.refresh()

    def refresh(self) -> None:
        while self.flow.count():
            item = self.flow.takeAt(0)
            if item.widget() is not None and item.widget() is not self.new_card:
                _discard(item.widget())
        self.cards = []
        for entry in list_profile_entries(self.storage):
            card = ProfileCard(entry.id, entry.name, entry.meta, entry.color, entry.initial, entry.image_png)
            card.clicked.connect(lambda _checked=False, profile_id=entry.id: self.profile_chosen.emit(profile_id))
            self.flow.addWidget(card)
            self.cards.append(card)
        self.flow.addWidget(self.new_card)
        self.cards_host.updateGeometry()


class CreateProfileScreen(QWidget):
    """Aperçu en direct à gauche, formulaire (nom, avatar, classe) à droite."""

    created = Signal(int)
    back_requested = Signal()

    def __init__(self, storage: Storage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("d2Screen")
        self.storage = storage
        self.image_png: bytes | None = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(88, 40, 88, 56)
        layout.setSpacing(56)

        preview = QWidget()
        preview.setObjectName("d2Preview")
        preview.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        preview.setFixedWidth(340)
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(24, 40, 24, 40)
        # Gap 16 de la maquette, moins les 8px d'anneau de l'avatar et le « margin-top:-8 » des textes.
        preview_layout.setSpacing(8)
        caps = _label("APERÇU", "d2Caps")
        caps.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        preview_layout.addWidget(caps)
        self.preview_avatar = Avatar(156)   # 140px + anneau de 8px
        self.preview_avatar.halo = True
        preview_layout.addWidget(self.preview_avatar, 0, Qt.AlignmentFlag.AlignHCenter)
        self.preview_name = _label(DEFAULT_PROFILE_NAME, "d2PreviewName")
        self.preview_name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        preview_layout.addWidget(self.preview_name)
        self.preview_class = _label("", "d2PreviewClass")
        self.preview_class.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        preview_layout.addWidget(self.preview_class)
        layout.addWidget(preview, 0, Qt.AlignmentFlag.AlignVCenter)

        form = QVBoxLayout()
        form.setSpacing(26)
        form.addStretch(1)
        form.addWidget(_label("Créer un profil", "d2H2"))

        name_box = QVBoxLayout()
        name_box.setSpacing(8)
        name_box.addWidget(_label("Nom du profil", "d2FieldLabel"))
        self.name = QLineEdit()
        self.name.setObjectName("d2Input")
        self.name.setPlaceholderText("ex. Kira")
        self.name.setMaxLength(MAX_NAME_LENGTH)
        self.name.setMaximumWidth(420)
        self.name.textChanged.connect(self._update_preview)
        self.name.returnPressed.connect(self._create)
        name_box.addWidget(self.name)
        form.addLayout(name_box)

        avatar_box = QVBoxLayout()
        avatar_box.setSpacing(5)  # 10px de la maquette, dont 5px déjà réservés par l'anneau des pastilles
        avatar_box.addWidget(_label("Avatar", "d2FieldLabel"))
        swatches = QHBoxLayout()
        swatches.setSpacing(0)  # pastilles de 54px (44 + anneau) : l'écart visible reste de 10px
        self.avatar_group = QButtonGroup(self)
        self.avatar_group.setExclusive(True)
        self.swatches: list[AvatarSwatch] = []
        for index, color in enumerate(t.AVATAR_COLORS):
            swatch = AvatarSwatch(color)
            self.avatar_group.addButton(swatch, index)
            swatches.addWidget(swatch)
            self.swatches.append(swatch)
        self.upload = UploadTile()
        self.avatar_group.addButton(self.upload, len(t.AVATAR_COLORS))
        self.upload.clicked.connect(self._import_image)
        swatches.addWidget(self.upload)
        swatches.addStretch(1)
        self.avatar_group.idToggled.connect(lambda *_: self._update_preview())
        avatar_box.addLayout(swatches)
        form.addLayout(avatar_box)

        class_box = QVBoxLayout()
        class_box.setSpacing(10)
        class_label = QLabel(f"Classe <span style='font-weight:500; color:{t.TEXT_MUTED}'>· facultatif</span>")
        class_label.setObjectName("d2FieldLabel")
        class_box.addWidget(class_label)
        chips_host = QWidget()
        chips_host.setObjectName("d2Screen")
        chips_host.setMaximumWidth(560)
        chips = FlowLayout(chips_host, spacing=8)
        self.class_group = QButtonGroup(self)
        self.class_group.setExclusive(False)  # facultatif : un second clic désélectionne
        self.class_chips: list[QPushButton] = []
        for name in t.CLASSES:
            chip = _button(name, "d2Chip")
            chip.setCheckable(True)
            chip.toggled.connect(lambda checked, chip=chip: self._class_toggled(chip, checked))
            self.class_group.addButton(chip)
            chips.addWidget(chip)
            self.class_chips.append(chip)
        class_box.addWidget(chips_host)
        form.addLayout(class_box)

        actions = QHBoxLayout()
        actions.setSpacing(14)
        self.create_button = _button("Créer le profil", "d2Primary")
        self.create_button.clicked.connect(self._create)
        back = _button("Retour", "d2Link")
        back.clicked.connect(self.back_requested)
        actions.addWidget(self.create_button)
        actions.addWidget(back)
        actions.addStretch(1)
        form.addSpacing(6)
        form.addLayout(actions)
        form.addStretch(1)
        layout.addLayout(form, 1)
        self.reset()

    def reset(self) -> None:
        self.name.clear()
        self.image_png = None
        self.upload.set_image(None)
        self.swatches[1].setChecked(True)
        for chip in self.class_chips:
            chip.setChecked(False)
        self._update_preview()

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self.name.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - API Qt
        if event.key() == Qt.Key.Key_Escape:
            self.back_requested.emit()
            return
        super().keyPressEvent(event)

    @property
    def character_class(self) -> str | None:
        return next((chip.text() for chip in self.class_chips if chip.isChecked()), None)

    @property
    def color_index(self) -> int:
        checked = self.avatar_group.checkedId()
        return checked if 0 <= checked < len(t.AVATAR_COLORS) else 1

    @property
    def uses_image(self) -> bool:
        return self.upload.isChecked() and self.image_png is not None

    def _class_toggled(self, chip: QPushButton, checked: bool) -> None:
        if checked:
            for other in self.class_chips:
                if other is not chip and other.isChecked():
                    other.setChecked(False)
        self._update_preview()

    def _update_preview(self) -> None:
        name = self.name.text().strip()
        self.preview_name.setText(name or DEFAULT_PROFILE_NAME)
        self.preview_class.setText(self.character_class or "Classe au choix")
        self.preview_avatar.set_avatar(t.AVATAR_COLORS[self.color_index], (name[:1] or "?").upper(),
                                       self.image_png if self.uses_image else None)

    def _import_image(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Importer une image d'avatar", "", "Images (*.png *.jpg *.jpeg *.bmp *.webp)")
        image = QImage(path) if path else QImage()
        if image.isNull():
            if path:
                QMessageBox.warning(self, "Avatar", "Cette image n'a pas pu être lue.")
            if self.image_png is None:   # rien d'importé : on revient à la couleur choisie avant
                self.swatches[self.color_index].setChecked(True)
            self._update_preview()
            return
        self.image_png = square_png(image, AVATAR_IMAGE_SIZE)
        self.upload.set_image(QImage.fromData(self.image_png))
        self.upload.setChecked(True)
        self._update_preview()

    def _create(self) -> None:
        try:
            entry: ProfileEntry = create_profile(
                self.storage, self.name.text(), self.color_index, self.character_class,
                self.image_png if self.uses_image else None,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Profil", str(exc))
            return
        self.created.emit(entry.id)


def square_png(image: QImage, size: int) -> bytes:
    """Recadrage carré centré puis PNG ``size``×``size`` : l'avatar reste léger en base."""
    side = min(image.width(), image.height())
    square = image.copy(QRect((image.width() - side) // 2, (image.height() - side) // 2, side, side))
    square = square.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    square.save(buffer, "PNG")
    return bytes(buffer.data())
