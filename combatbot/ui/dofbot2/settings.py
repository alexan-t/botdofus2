"""Réglages DofBot2 persistés en SQLite : par profil (activité, alertes, sorts) ou globaux."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import unicodedata

from combatbot.gamedata.errors import GameDataError
from combatbot.runtime import PROJECT_ROOT, executable_path, is_frozen
from combatbot.storage import Storage


PROFILE_SETTINGS_KEY = "dofbot2"
APP_SETTINGS_KEY = "dofbot2_app"
SPELL_CONFIG_KEY = "dofbot2_spells"
DETECTED_AT_KEY = "dofbot2_spells_detected_at"


@dataclass(frozen=True)
class Place:
    name: str
    meta: str
    boss: str | None = None
    monsters: tuple[str, ...] = ()
    place_id: int | None = None
    level: int | None = None
    map_ids: tuple[int, ...] = ()
    room_names: tuple[str, ...] = ()
    entrance_map_id: int | None = None
    exit_map_id: int | None = None
    image_path: str | None = None


# Catalogue initial de la maquette ; les images restent des emplacements à fournir.
DUNGEONS = (
    Place("Cour du Bouftou Royal", "Niv. 30 · 5 cartes", boss="Bouftou Royal", place_id=1),
    Place("Château Ensablé", "Niv. 20 · 6 cartes", boss="Mob l'Éponge", place_id=19),
    Place("Antre du Dragon Cochon", "Niv. 100 · 13 cartes", boss="Dragon Cochon", place_id=6),
    Place("Donjon des Larves", "Niv. 50 · 15 cartes", boss="Shin Larve", place_id=33),
)
ZONES = (
    Place("Champs d'Astrub", "Niv. 1–30 · 12 maps", monsters=("Pissenlit", "Tofu", "Moskito", "Larve Bleue")),
    Place("Plaine des Porkass", "Niv. 30–60 · 9 maps", monsters=("Porkass", "Sanglier", "Prespic")),
    Place("Forêt des Abraknydes", "Niv. 60–90 · 14 maps", monsters=("Abraknyde", "Tronknyde", "Arakne")),
    Place("Cimetière d'Amakna", "Niv. 40–70 · 8 maps", monsters=("Chafer", "Chafer Archer", "Fantôme")),
)

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".jfif", ".webp")
KNOWN_DUNGEON_BOSSES = {
    "cour_du_bouftou_royal": ("Bouftou Royal", 147, "bouftou_royal"),
    "antre_du_dragon_cochon": ("Dragon Cochon", 113, "dragon_cochon"),
    "chateau_ensable": ("Mob l'Éponge", 928, "mob_l_eponge"),
    "donjon_des_larves": ("Shin Larve", 457, "shin_larve"),
}


def _slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.replace("’", "'"))
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char)).lower()
    return "_".join("".join(char if char.isalnum() else " " for char in ascii_text).split())


def _illustration_directories() -> tuple[Path, ...]:
    directories = [PROJECT_ROOT / "assets" / "illustration" / "boss_donjon"]
    if is_frozen():
        # Permet d'ajouter une illustration au projet sans reconstruire l'exécutable.
        directories.insert(0, executable_path().parent.parent.parent / "assets" / "illustration" / "boss_donjon")
    return tuple(dict.fromkeys(path.resolve() for path in directories))


def _manual_illustration(*stems: str) -> Path | None:
    wanted = {stem.casefold() for stem in stems if stem}
    for directory in _illustration_directories():
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and path.stem.casefold() in wanted:
                return path
    return None


def load_dungeon_places(storage: Storage) -> tuple[Place, ...]:
    """Adapte le catalogue réel à l'UI ; revient au catalogue de démonstration sans client configuré."""
    client_value = str(storage.get_setting("dofus_client_directory") or "").strip()
    client = Path(client_value) if client_value else None
    if client is None or not client.is_dir():
        return DUNGEONS
    try:
        from combatbot.gamedata.dungeons import load_dungeons
        records = load_dungeons(client)
    except (GameDataError, KeyError, OSError, ValueError):
        return DUNGEONS

    generic = client / "content" / "gfx" / "guideBook" / "donjon-200x600.png"
    places = []
    for record in records:
        slug = _slug(record.name)
        boss_name = None
        boss_id = None
        candidates = (f"donjon_{record.dungeon_id}", slug)
        known = KNOWN_DUNGEON_BOSSES.get(slug)
        if known:
            boss_name, boss_id, boss_slug = known
            candidates += (boss_slug,)
        manual = _manual_illustration(*candidates)
        boss_icon = (client / "content" / "themes" / "darkStone" / "texture" / "songes" /
                     f"boss_{boss_id}.png") if boss_id is not None else None
        image = manual or (boss_icon if boss_icon and boss_icon.is_file() else None) or \
            (generic if generic.is_file() else None)
        names = tuple(room.name or f"Map {room.map_id}" for room in record.rooms)
        places.append(Place(
            record.name,
            f"Niv. {record.optimal_level} · {len(record.rooms)} carte{'s' if len(record.rooms) != 1 else ''}",
            boss=boss_name,
            place_id=record.dungeon_id,
            level=record.optimal_level,
            map_ids=tuple(room.map_id for room in record.rooms),
            room_names=names,
            entrance_map_id=record.entrance_map_id,
            exit_map_id=record.exit_map_id,
            image_path=str(image) if image else None,
        ))
    return tuple(places) or DUNGEONS

# Types d'alertes filtrés par leur interrupteur dans l'onglet Alertes.
ALERT_KINDS = {
    "nfBoss": "Boss vaincu",
    "nfRun": "Run terminé",
    "nfPods": "Inventaire presque plein",
    "nfMp": "Message privé reçu",
    "nfDeath": "Personnage mort",
    "nfEnd": "Session arrêtée",
}
TOAST_DURATIONS = {"4 s": 4000, "8 s": 8000, "Jusqu'au clic": 0}
SPELL_TARGETS = ("Ennemi", "Allié", "Soi-même", "Case vide")
SPELL_TIMINGS = ("Toujours", "Premier tour", "PV bas")


class SettingsBinding:
    """Interface commune aux contrôles des accordéons : lire et écrire une valeur par clé."""

    def get(self, key: str, default: object = None) -> object:
        raise NotImplementedError

    def set(self, key: str, value: object) -> None:
        raise NotImplementedError


class JsonSettings(SettingsBinding):
    """Dictionnaire JSON unique, relu depuis SQLite à chaque accès (pas de cache désynchronisé)."""

    def __init__(self, storage: Storage, key: str, profile_id: int | None = None) -> None:
        self.storage, self.key, self.profile_id = storage, key, profile_id

    def _load(self) -> dict:
        if self.profile_id is None:
            raw = self.storage.get_setting(self.key)
        else:
            raw = self.storage.get_profile_setting(self.profile_id, self.key, None)
        return dict(raw) if isinstance(raw, dict) else {}

    def get(self, key: str, default: object = None) -> object:
        return self._load().get(key, default)

    def set(self, key: str, value: object) -> None:
        data = self._load()
        data[key] = value
        if self.profile_id is None:
            self.storage.set_setting(self.key, data)
        else:
            self.storage.set_profile_setting(self.profile_id, self.key, data)


def profile_settings(storage: Storage, profile_id: int) -> JsonSettings:
    return JsonSettings(storage, PROFILE_SETTINGS_KEY, profile_id)


def app_settings(storage: Storage) -> JsonSettings:
    return JsonSettings(storage, APP_SETTINGS_KEY)


def monsters_key(zone_index: int) -> str:
    return f"mobs{zone_index}"


def recent_dungeon_ids(settings: SettingsBinding) -> tuple[int, ...]:
    raw = settings.get("recent_dungeon_ids", [])
    if not isinstance(raw, list):
        return ()
    result = []
    for value in raw:
        if isinstance(value, int) and value not in result:
            result.append(value)
    return tuple(result[:8])


def remember_recent_dungeon(settings: SettingsBinding, place: Place) -> None:
    """Mémorise un donjon quand sa session démarre, le plus récent en premier."""
    if place.place_id is None:
        return
    values = [place.place_id, *(item for item in recent_dungeon_ids(settings) if item != place.place_id)]
    settings.set("recent_dungeon_ids", values[:8])


def clamp_index(value: object, size: int) -> int:
    return value if isinstance(value, int) and 0 <= value < size else 0


@dataclass(frozen=True)
class Plan:
    mode: str           # donjon | zone
    place: Place
    planned: bool


def current_plan(settings: SettingsBinding) -> Plan:
    mode = settings.get("plan_mode", "donjon")
    mode = mode if mode in ("donjon", "zone") else "donjon"
    if mode == "donjon":
        saved = settings.get("dj_place")
        if isinstance(saved, dict) and isinstance(saved.get("name"), str):
            place = Place(str(saved["name"]), str(saved.get("meta") or ""),
                          boss=str(saved["boss"]) if saved.get("boss") else None,
                          place_id=int(saved["place_id"]) if saved.get("place_id") is not None else None)
            return Plan(mode, place, bool(settings.get("planned", False)))
    places = DUNGEONS if mode == "donjon" else ZONES
    index = clamp_index(settings.get("dj" if mode == "donjon" else "zn", 0), len(places))
    return Plan(mode, places[index], bool(settings.get("planned", False)))


def plan_summary(settings: SettingsBinding, active_spells: int | None) -> str:
    """« Donjon · 10 runs · boss en priorité · 20 sorts actifs »."""
    plan = current_plan(settings)
    if plan.mode == "donjon":
        runs = settings.get("runs", 10)
        parts = ["Donjon", f"{runs or '∞'} runs"]
        if settings.get("bossFirst", True):
            parts.append("boss en priorité")
    else:
        parts = ["Zone", str(settings.get("path", "Boucle")).lower()]
    if active_spells is None:
        parts.append("aucun sort détecté")
    else:
        parts.append(f"{active_spells} sort{'s' if active_spells > 1 else ''} actif{'s' if active_spells > 1 else ''}")
    return " · ".join(parts)


class SpellConfig:
    """Réglages propres à DofBot2 par sort détecté : utilisation, cible, moment, priorité.
    Les caractéristiques lues (PA, portée…) restent dans ``profile_spells``."""

    def __init__(self, storage: Storage, profile_id: int) -> None:
        self.storage, self.profile_id = storage, profile_id

    def _load(self) -> dict:
        raw = self.storage.get_profile_setting(self.profile_id, SPELL_CONFIG_KEY, None)
        return dict(raw) if isinstance(raw, dict) else {}

    def get(self, spell_id: int, key: str, default: object = None) -> object:
        entry = self._load().get(str(spell_id), {})
        return entry.get(key, default) if isinstance(entry, dict) else default

    def set(self, spell_id: int, key: str, value: object) -> None:
        data = self._load()
        entry = data.get(str(spell_id))
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry[key] = value
        data[str(spell_id)] = entry
        self.storage.set_profile_setting(self.profile_id, SPELL_CONFIG_KEY, data)

    def in_use(self, spell_id: int) -> bool:
        return bool(self.get(spell_id, "use", True))


# Colonnes de profile_spells exposées dans « Coût & portée » et « Conditions de lancer ».
SPELL_COLUMNS = {
    "ap_cost": int, "min_range": int, "max_range": int, "modifiable_range": bool,
    "line_of_sight": bool, "line_cast": bool, "per_turn": int, "per_target": int,
}
SPELL_DEFAULTS = {
    "ap_cost": 3, "min_range": 1, "max_range": 6, "modifiable_range": True,
    "line_of_sight": True, "line_cast": False, "per_turn": 2, "per_target": 1,
}


class SpellBinding(SettingsBinding):
    """Lie les contrôles d'un sort : colonnes lues par le scan dans ``profile_spells``, le reste
    dans ``SpellConfig``. Un sort confirmé reste confirmé quand on corrige une valeur ici."""

    def __init__(self, storage: Storage, config: SpellConfig, spell_id: int, slot_priority: int) -> None:
        self.storage, self.config, self.spell_id = storage, config, spell_id
        self.slot_priority = slot_priority

    def get(self, key: str, default: object = None) -> object:
        if key in SPELL_COLUMNS:
            value = self.storage.get_profile_spell(self.spell_id)[key]
            if value is None:
                return default
            return SPELL_COLUMNS[key](value)
        if key == "priority":
            return self.config.get(self.spell_id, key, self.slot_priority)
        return self.config.get(self.spell_id, key, default)

    def set(self, key: str, value: object) -> None:
        if key not in SPELL_COLUMNS:
            self.config.set(self.spell_id, key, value)
            return
        row = self.storage.get_profile_spell(self.spell_id)
        fields = {key: int(value) if isinstance(value, bool) else value}
        # Garder min ≤ max : on déplace l'autre borne plutôt que de refuser le changement.
        if key == "min_range" and row["max_range"] is not None and value > row["max_range"]:
            fields["max_range"] = value
        if key == "max_range" and row["min_range"] is not None and value < row["min_range"]:
            fields["min_range"] = value
        self.storage.save_profile_spell_fields(self.spell_id, fields, confirm=row["status"] == "Confirmé")
