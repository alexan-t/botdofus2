"""Modèles métier indépendants de Qt et du jeu."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True, order=True)
class Cell:
    x: int
    y: int

    def distance(self, other: "Cell") -> int:
        return abs(self.x - other.x) + abs(self.y - other.y)


class CombatState(str, Enum):
    IDLE = "IDLE"
    COMBAT_DETECTED = "COMBAT_DETECTED"
    WAIT_TURN = "WAIT_TURN"
    ANALYZE = "ANALYZE"
    EXECUTE = "EXECUTE"
    END_TURN = "END_TURN"
    FINISHED = "FINISHED"


class StrategyMode(str, Enum):
    DISTANCE = "distance"
    MELEE = "corps-à-corps"
    SURVIVAL = "survie"


class TargetPriority(str, Enum):
    NEAREST = "plus proche"
    LOWEST_HP = "moins de PV"


@dataclass(frozen=True)
class Spell:
    name: str
    ap_cost: int
    min_range: int
    max_range: int
    modifiable_range: bool
    line_cast: bool
    line_of_sight: bool
    per_turn: int
    per_target: int
    priority: int
    damage: int = 1  # Valeur purement simulée, jamais présentée comme une donnée DOFUS.
    id: int | None = None

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("Le nom du sort est requis")
        if self.ap_cost < 1 or self.min_range < 0 or self.max_range < self.min_range:
            raise ValueError("Coût en PA ou portée invalide")
        if self.per_turn < 1 or self.per_target < 1 or self.damage < 0:
            raise ValueError("Limites de lancer ou dégâts simulés invalides")


@dataclass(frozen=True)
class Strategy:
    mode: StrategyMode = StrategyMode.DISTANCE
    hp_threshold: int = 35
    target_priority: TargetPriority = TargetPriority.NEAREST
    reserve_ap: int = 0

    def validate(self) -> None:
        if not 0 <= self.hp_threshold <= 100 or self.reserve_ap < 0:
            raise ValueError("Paramètres de stratégie invalides")


@dataclass
class Actor:
    name: str
    cell: Cell
    hp: int
    max_hp: int

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass(frozen=True)
class Action:
    kind: str  # cast, move, end_turn
    target: str | None = None
    spell_id: int | None = None
    destination: Cell | None = None
    cost: int = 0


@dataclass(frozen=True)
class CombatSnapshot:
    state: CombatState
    turn: int
    player: Actor
    enemies: tuple[Actor, ...]
    obstacles: frozenset[Cell]
    width: int
    height: int
    ap: int
    mp: int
    selected_target: str | None
    selected_spell: str | None
    last_action: str
    history: tuple[str, ...] = field(default_factory=tuple)
    outcome: str | None = None
    xp: int = 0
    kamas: int = 0


@dataclass(frozen=True)
class CombatEvent:
    level: str
    event: str
    message: str
    context: dict[str, object] = field(default_factory=dict)
