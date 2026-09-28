"""FAST-4A : domaine de sort pour l'état réel, avec inconnu champ par champ et provenance.

``combatbot.models.Spell`` reste le modèle du **simulateur** : tous ses champs sont connus et ``damage``
est une valeur simulée, jamais une donnée DOFUS. ``CombatSpell`` en est la version « état réel » :

- chaque caractéristique peut être ``None`` (inconnue) ;
- la provenance dit d'où vient la valeur ; seul un sort **confirmé par un humain** avec toutes ses
  caractéristiques utiles connues est ``CONFIRMED`` ;
- les paramètres de stratégie (utiliser, cible, moment, priorité) sont séparés des données de jeu ;
- aucun dégât : la valeur simulée ne traverse jamais vers ce modèle ;
- la zone d'effet n'est pas modélisée (non représentable sans supposition dans le dépôt) ;
- version cible : 2.64.5 (serveur privé de test).

Adaptateurs : ``from_simulated`` / ``to_simulated`` (compatibilité simulateur) et ``from_profile_row``
(``profile_spells`` + réglages DofBot2). Une icône ne donne **jamais** de caractéristiques : une ligne au
statut « Reconnu » (valeurs recopiées d'un sort confirmé de même icône) reste non confirmée.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, replace
from enum import Enum
from typing import Any, Mapping

from combatbot.models import Spell

GAME_VERSION = "2.64.5"
CHARACTERISTICS = ("ap_cost", "min_range", "max_range", "modifiable_range", "line_cast", "line_of_sight",
                   "per_turn", "per_target")


class SpellProvenance(str, Enum):
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"            # profile_spells « Confirmé » (validé par l'utilisateur)
    OCR_UNVERIFIED = "OCR_UNVERIFIED"              # lecture d'infobulle « À vérifier »
    ICON_MATCH_UNCONFIRMED = "ICON_MATCH_UNCONFIRMED"  # « Reconnu » : valeurs copiées d'une icône identique
    SIMULATED = "SIMULATED"                        # sort du simulateur, jamais une donnée de jeu
    UNKNOWN = "UNKNOWN"


class SpellStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    UNKNOWN = "UNKNOWN"


class SpellTarget(str, Enum):
    ENEMY = "ENEMY"
    ALLY = "ALLY"
    SELF = "SELF"
    EMPTY_CELL = "EMPTY_CELL"


class SpellTiming(str, Enum):
    ALWAYS = "ALWAYS"
    FIRST_TURN = "FIRST_TURN"
    LOW_HP = "LOW_HP"


# Libellés de l'interface DofBot2 (onglet Sorts) → valeurs métier.
UI_TARGETS = {"Ennemi": SpellTarget.ENEMY, "Allié": SpellTarget.ALLY, "Soi-même": SpellTarget.SELF,
              "Case vide": SpellTarget.EMPTY_CELL}
UI_TIMINGS = {"Toujours": SpellTiming.ALWAYS, "Premier tour": SpellTiming.FIRST_TURN, "PV bas": SpellTiming.LOW_HP}
PROFILE_STATUS = {"Confirmé": SpellProvenance.HUMAN_CONFIRMED, "À vérifier": SpellProvenance.OCR_UNVERIFIED,
                  "Reconnu": SpellProvenance.ICON_MATCH_UNCONFIRMED, "Inconnu": SpellProvenance.UNKNOWN}


@dataclass(frozen=True)
class SpellSlot:
    page: int
    slot: int                      # 1-based, comme le scan de la barre


@dataclass(frozen=True)
class SpellStrategy:
    """Choix de l'utilisateur, pas des données de jeu."""
    use: bool = True
    target: SpellTarget = SpellTarget.ENEMY
    timing: SpellTiming = SpellTiming.ALWAYS
    priority: int = 1              # 1 = lancé en premier


@dataclass(frozen=True)
class CombatSpell:
    key: str                                   # identifiant stable dans un plan (ex. « profile:42 »)
    name: str | None = None
    game_spell_id: int | None = None           # identifiant DOFUS : aucune source dans le dépôt pour l'instant
    slot: SpellSlot | None = None
    ap_cost: int | None = None
    min_range: int | None = None
    max_range: int | None = None
    modifiable_range: bool | None = None
    line_cast: bool | None = None
    line_of_sight: bool | None = None
    per_turn: int | None = None
    per_target: int | None = None
    provenance: SpellProvenance = SpellProvenance.UNKNOWN
    game_version: str = GAME_VERSION
    strategy: SpellStrategy = field(default_factory=SpellStrategy)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.key.strip():
            raise ValueError("Un sort exige une clé stable")
        for name in ("ap_cost", "min_range", "max_range", "per_turn", "per_target", "game_spell_id"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} invalide : {value!r}")
        for name in ("modifiable_range", "line_cast", "line_of_sight"):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise ValueError(f"{name} doit être booléen ou inconnu : {value!r}")
        if self.ap_cost is not None and self.ap_cost < 1:
            raise ValueError("Un sort coûte au moins 1 PA")
        if self.min_range is not None and self.max_range is not None and self.min_range > self.max_range:
            raise ValueError("Portée minimale supérieure à la portée maximale")
        for name in ("per_turn", "per_target"):
            value = getattr(self, name)
            if value is not None and value < 1:
                raise ValueError(f"{name} doit valoir au moins 1")
        if self.slot is not None and (self.slot.page < 1 or self.slot.slot < 1):
            raise ValueError("Emplacement invalide")
        if self.strategy.priority < 1:
            raise ValueError("Priorité invalide")

    # ------------------------------------------------------------------ état de connaissance
    @property
    def unknown_fields(self) -> tuple[str, ...]:
        return tuple(name for name in CHARACTERISTICS if getattr(self, name) is None)

    @property
    def status(self) -> SpellStatus:
        if self.provenance is SpellProvenance.HUMAN_CONFIRMED and not self.unknown_fields:
            return SpellStatus.CONFIRMED
        return SpellStatus.UNKNOWN

    @property
    def usable_for_real_decision(self) -> bool:
        """Un moteur réel ne s'appuie que sur un sort confirmé que l'utilisateur veut utiliser."""
        return self.status is SpellStatus.CONFIRMED and self.strategy.use

    # ------------------------------------------------------------------ sérialisation
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["provenance"] = self.provenance.value
        data["strategy"] = {**data["strategy"], "target": self.strategy.target.value,
                            "timing": self.strategy.timing.value}
        data["status"] = self.status.value
        data["unknown_fields"] = list(self.unknown_fields)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CombatSpell":
        known = {item.name for item in fields(cls)}
        values = {key: value for key, value in data.items() if key in known}
        if isinstance(values.get("slot"), Mapping):
            values["slot"] = SpellSlot(int(values["slot"]["page"]), int(values["slot"]["slot"]))
        values["provenance"] = SpellProvenance(values.get("provenance", SpellProvenance.UNKNOWN))
        strategy = values.get("strategy")
        if isinstance(strategy, Mapping):
            values["strategy"] = SpellStrategy(bool(strategy.get("use", True)),
                                               SpellTarget(strategy.get("target", SpellTarget.ENEMY)),
                                               SpellTiming(strategy.get("timing", SpellTiming.ALWAYS)),
                                               int(strategy.get("priority", 1)))
        return cls(**values)

    # ------------------------------------------------------------------ simulateur
    @classmethod
    def from_simulated(cls, spell: Spell, index: int = 0) -> "CombatSpell":
        """Sort du simulateur : provenance SIMULATED, donc jamais CONFIRMED ; les dégâts ne passent pas."""
        return cls(key=f"sim:{spell.id if spell.id is not None else index}", name=spell.name,
                   ap_cost=spell.ap_cost, min_range=spell.min_range, max_range=spell.max_range,
                   modifiable_range=spell.modifiable_range, line_cast=spell.line_cast,
                   line_of_sight=spell.line_of_sight, per_turn=spell.per_turn, per_target=spell.per_target,
                   provenance=SpellProvenance.SIMULATED, strategy=SpellStrategy(priority=max(1, spell.priority)))

    def to_simulated(self, damage: int = 1, priority: int = 10) -> Spell:
        """Pour rejouer un sort dans le simulateur ; exige toutes les caractéristiques."""
        if self.unknown_fields:
            raise ValueError(f"Caractéristiques inconnues : {', '.join(self.unknown_fields)}")
        spell = Spell(self.name or self.key, self.ap_cost, self.min_range, self.max_range, self.modifiable_range,
                      self.line_cast, self.line_of_sight, self.per_turn, self.per_target, priority, damage)
        spell.validate()
        return spell

    def with_strategy(self, strategy: SpellStrategy) -> "CombatSpell":
        return replace(self, strategy=strategy)


# ------------------------------------------------------------------ profile_spells (DofBot2)
def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object) -> bool | None:
    return None if value is None else bool(int(value)) if isinstance(value, (int, bool)) else None


def strategy_from_config(config: Mapping[str, Any] | None, default_priority: int) -> SpellStrategy:
    """Réglages de l'onglet Sorts de DofBot2 (clé ``dofbot2_spells``) ; libellés inconnus → défauts."""
    config = config or {}
    priority = _optional_int(config.get("priority"))
    return SpellStrategy(use=bool(config.get("use", True)),
                         target=UI_TARGETS.get(str(config.get("target")), SpellTarget.ENEMY),
                         timing=UI_TIMINGS.get(str(config.get("when")), SpellTiming.ALWAYS),
                         priority=priority if priority and priority >= 1 else max(1, default_priority))


def from_profile_row(row: Mapping[str, Any], config: Mapping[str, Any] | None = None,
                     default_priority: int = 1) -> CombatSpell:
    """Ligne ``profile_spells`` → sort. « Confirmé » + ``decision_ready`` = HUMAN_CONFIRMED ; sinon la
    provenance reflète le doute et le sort reste UNKNOWN, même si des valeurs sont présentes."""
    status = str(row["status"])
    provenance = PROFILE_STATUS.get(status, SpellProvenance.UNKNOWN)
    keys = row.keys() if hasattr(row, "keys") else ()
    if provenance is SpellProvenance.HUMAN_CONFIRMED and "decision_ready" in keys and not row["decision_ready"]:
        provenance = SpellProvenance.OCR_UNVERIFIED
    page, slot = _optional_int(row["page"]), _optional_int(row["slot"])
    return CombatSpell(
        key=f"profile:{row['id']}", name=row["name"] or None,
        slot=SpellSlot(page, slot) if page and slot else None,
        ap_cost=_optional_int(row["ap_cost"]), min_range=_optional_int(row["min_range"]),
        max_range=_optional_int(row["max_range"]), modifiable_range=_optional_bool(row["modifiable_range"]),
        line_cast=_optional_bool(row["line_cast"]), line_of_sight=_optional_bool(row["line_of_sight"]),
        per_turn=_optional_int(row["per_turn"]), per_target=_optional_int(row["per_target"]),
        provenance=provenance, strategy=strategy_from_config(config, default_priority),
    )


def load_profile_spells(storage, profile_id: int, config_key: str = "dofbot2_spells") -> tuple[CombatSpell, ...]:
    """``storage`` : tout objet exposant ``list_profile_spells`` et ``get_profile_setting`` (Storage)."""
    config = storage.get_profile_setting(profile_id, config_key, None)
    config = config if isinstance(config, Mapping) else {}
    spells = []
    for index, row in enumerate(storage.list_profile_spells(profile_id)):
        spells.append(from_profile_row(row, config.get(str(row["id"])), default_priority=index + 1))
    return tuple(spells)
