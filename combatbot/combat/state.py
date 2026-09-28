"""FAST-4D : état de combat métier immuable, alimenté par l'observation (lecture seule)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EnemyState:
    id: str
    cell_id: int | None
    observed: bool = True            # une piste occultée n'a pas de position sûre


@dataclass(frozen=True)
class RealCombatState:
    map_id: int | None
    player_cell_id: int | None
    ap: int | None
    mp: int | None
    enemies: tuple[EnemyState, ...] = ()
    occupied_cells: frozenset[int] = frozenset()      # occupants observés (hors joueur)
    unknown_cells: frozenset[int] = frozenset()       # occupation non déterminée
    phase: str | None = None                          # CombatPhase (3B-6B)
    turn: str | None = None                           # TurnOwner (3B-6B)
    safe_for_decision: bool | None = None
    confidence: float | None = None
    turn_number: int | None = None
    range_bonus: int | None = None                    # bonus de portée : non lu à l'écran aujourd'hui
    casts_this_turn: Mapping[str, int] = field(default_factory=dict)
    casts_on_target: Mapping[tuple[str, str], int] = field(default_factory=dict)

    def blocking_unknowns(self) -> tuple[str, ...]:
        """Données indispensables inconnues ou défavorables : chacune suffit à refuser un plan."""
        reasons = []
        if self.phase != "FIGHTING":
            reasons.append(f"phase {self.phase or UNKNOWN} : pas un combat en cours")
        elif self.turn != "PLAYER":
            reasons.append(f"tour {self.turn or UNKNOWN} : pas mon tour")
        if self.safe_for_decision is not True:
            reasons.append("observation non sûre pour décider (safe_for_decision)")
        if self.map_id is None:
            reasons.append("map inconnue")
        if self.player_cell_id is None:
            reasons.append("cellule du joueur inconnue")
        if self.ap is None:
            reasons.append("PA inconnus")
        if self.mp is None:
            reasons.append("PM inconnus")
        for enemy in self.enemies:
            if enemy.cell_id is None or not enemy.observed:
                reasons.append(f"ennemi {enemy.id} non localisé")
        return tuple(reasons)

    @classmethod
    def from_observation(cls, observation, *, map_id: int | None, phase: str | None, turn: str | None,
                         turn_number: int | None = None, range_bonus: int | None = None) -> "RealCombatState":
        """Adaptateur depuis ``vision.combat_models.CombatObservation`` (aucune dépendance importée)."""
        enemies = tuple(EnemyState(str(enemy.id), enemy.cell_id, bool(enemy.observed_this_frame))
                        for enemy in observation.enemies)
        occupied, unknown = set(), set()
        for cell in observation.grid.cells:
            if cell.cell_id is None:
                continue
            state = getattr(cell.state, "value", cell.state)
            if state == "OCCUPIED" and cell.cell_id != observation.player_cell_id:
                occupied.add(int(cell.cell_id))
            elif state == "UNKNOWN":
                unknown.add(int(cell.cell_id))
        return cls(map_id=map_id, player_cell_id=observation.player_cell_id, ap=observation.ap, mp=observation.mp,
                   enemies=enemies, occupied_cells=frozenset(occupied), unknown_cells=frozenset(unknown),
                   phase=phase, turn=turn, safe_for_decision=bool(observation.safe_for_decision),
                   confidence=float(observation.observation_confidence), turn_number=turn_number,
                   range_bonus=range_bonus)
