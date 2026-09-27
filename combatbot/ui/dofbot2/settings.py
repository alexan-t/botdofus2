"""Réglages DofBot2 persistés en SQLite : par profil (activité, alertes, sorts) ou globaux."""

from __future__ import annotations

from dataclasses import dataclass

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


# Catalogue initial de la maquette ; les images restent des emplacements à fournir.
DUNGEONS = (
    Place("Donjon des Bouftous", "Niv. 20 · 5 salles", boss="Bouftou Royal"),
    Place("Château Ensablé", "Niv. 40 · 4 salles", boss="Mob l'Éponge"),
    Place("Antre du Dragon Cochon", "Niv. 60 · 6 salles", boss="Dragon Cochon"),
    Place("Donjon des Larves", "Niv. 30 · 5 salles", boss="Shin Larve"),
)
ZONES = (
    Place("Champs d'Astrub", "Niv. 1–30 · 12 maps", monsters=("Pissenlit", "Tofu", "Moskito", "Larve Bleue")),
    Place("Plaine des Porkass", "Niv. 30–60 · 9 maps", monsters=("Porkass", "Sanglier", "Prespic")),
    Place("Forêt des Abraknydes", "Niv. 60–90 · 14 maps", monsters=("Abraknyde", "Tronknyde", "Arakne")),
    Place("Cimetière d'Amakna", "Niv. 40–70 · 8 maps", monsters=("Chafer", "Chafer Archer", "Fantôme")),
)

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
