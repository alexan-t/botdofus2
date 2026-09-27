"""Profils vus par DofBot2 : identité SQLite existante + apparence (avatar, niveau)."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import sqlite3

from combatbot.storage import Storage
from combatbot.ui.dofbot2.theme import AVATAR_COLORS
from combatbot.vision.models import Profile


# L'apparence vit dans profile_settings : aucune migration du schéma n'est nécessaire.
AVATAR_COLOR_KEY = "avatar_color"
AVATAR_IMAGE_KEY = "avatar_image_png"
LEVEL_KEY = "level"
LAST_PROFILE_SETTING = "dofbot2_last_profile"
DEFAULT_PROFILE_NAME = "Nouveau profil"
MAX_NAME_LENGTH = 32


@dataclass(frozen=True)
class ProfileEntry:
    id: int
    name: str
    character_class: str | None
    level: int | None
    color: str
    image_png: bytes | None = None
    window_hwnd: int | None = None

    @property
    def initial(self) -> str:
        return (self.name.strip()[:1] or "?").upper()

    @property
    def meta(self) -> str:
        parts = [self.character_class] if self.character_class else []
        if self.level:
            parts.append(f"niv. {self.level}")
        return " · ".join(parts) or "Classe non définie"


def _color(storage: Storage, profile: Profile) -> str:
    index = storage.get_profile_setting(profile.id, AVATAR_COLOR_KEY, None)
    if not isinstance(index, int) or not 0 <= index < len(AVATAR_COLORS):
        index = (profile.id - 1) % len(AVATAR_COLORS)
    return AVATAR_COLORS[index]


def _image(storage: Storage, profile_id: int) -> bytes | None:
    raw = storage.get_profile_setting(profile_id, AVATAR_IMAGE_KEY, None)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return base64.b64decode(raw, validate=True)
    except ValueError:
        return None


def profile_entry(storage: Storage, profile: Profile) -> ProfileEntry:
    level = storage.get_profile_setting(profile.id, LEVEL_KEY, None)
    return ProfileEntry(
        id=int(profile.id), name=profile.label, character_class=profile.character_class,
        level=level if isinstance(level, int) and level > 0 else None,
        color=_color(storage, profile), image_png=_image(storage, profile.id),
        window_hwnd=profile.window_hwnd,
    )


def list_profile_entries(storage: Storage) -> list[ProfileEntry]:
    return [profile_entry(storage, profile) for profile in storage.list_profiles()]


def unique_label(storage: Storage, wanted: str) -> str:
    """Le libellé est UNIQUE en base : « Kira » devient « Kira 2 » plutôt que d'échouer."""
    base = " ".join(wanted.split())[:MAX_NAME_LENGTH] or DEFAULT_PROFILE_NAME
    taken = {profile.label.casefold() for profile in storage.list_profiles()}
    if base.casefold() not in taken:
        return base
    number = 2
    while f"{base} {number}".casefold() in taken:
        number += 1
    return f"{base} {number}"


def create_profile(storage: Storage, name: str, color_index: int, character_class: str | None = None,
                   image_png: bytes | None = None) -> ProfileEntry:
    if not 0 <= color_index < len(AVATAR_COLORS):
        raise ValueError("Couleur d'avatar inconnue")
    label = unique_label(storage, name)
    try:
        profile_id = storage.save_profile(Profile(None, label, character_class=character_class or None))
    except sqlite3.IntegrityError as exc:  # course improbable entre deux créations
        raise ValueError(f"Le profil « {label} » existe déjà") from exc
    storage.set_profile_setting(profile_id, AVATAR_COLOR_KEY, color_index)
    if image_png:
        storage.set_profile_setting(profile_id, AVATAR_IMAGE_KEY, base64.b64encode(image_png).decode("ascii"))
    return profile_entry(storage, storage.get_profile(profile_id))


def last_profile_id(storage: Storage) -> int | None:
    value = storage.get_setting(LAST_PROFILE_SETTING)
    return value if isinstance(value, int) else None


def remember_profile(storage: Storage, profile_id: int) -> None:
    storage.set_setting(LAST_PROFILE_SETTING, profile_id)
