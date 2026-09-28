"""FAST-6B : placement — domaine et décision offline, **aucun clic** (ni case, ni « Prêt »).

Seules des cellules de départ **observées comme actives** à l'écran peuvent servir. Les indices
rouge/bleu GameData (``red_hint`` / ``blue_hint``) décrivent la map, pas les cases ouvertes à ce
combat : ils ne sont jamais acceptés comme vérité (``CellSource.GAMEDATA_HINT`` → BLOCKED). Aucun
détecteur ne produit encore ``OBSERVED_ACTIVE`` : le moteur est prêt à les recevoir.

Décision déterministe : distance de marche (BFS 4-voisins sur la topologie GameData, statiquement
praticable, ennemis bloquants) entre chaque case candidate et l'ennemi le plus proche ; la politique
choisit la plus proche ou la plus éloignée ; égalité → plus petit ``DofusCellId``. Rester sur place si
la case actuelle est déjà la meilleure. Toute donnée manquante → BLOCKED avec la raison.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from combatbot.combat.pathfinding import CombatMap, PathStatus, shortest_path


class CellSource(str, Enum):
    OBSERVED_ACTIVE = "OBSERVED_ACTIVE"      # cases de placement vues actives sur cette frame
    GAMEDATA_HINT = "GAMEDATA_HINT"          # red/blue hints statiques : jamais une vérité
    UNKNOWN = "UNKNOWN"


class PlacementGoal(str, Enum):
    CLOSE_TO_ENEMIES = "CLOSE_TO_ENEMIES"
    FAR_FROM_ENEMIES = "FAR_FROM_ENEMIES"


class PlacementStatus(str, Enum):
    MOVE = "MOVE"                            # changer de case (non exécuté ici)
    STAY = "STAY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class PlacementState:
    map_id: int | None
    phase: str | None
    available_start_cells: frozenset[int] | None     # None : non observable avec certitude
    source: CellSource
    player_cell_id: int | None
    enemy_start_cells: frozenset[int] | None

    @classmethod
    def from_gamedata_hints(cls, map_id: int | None, phase: str | None, hint_cells, player_cell_id: int | None,
                            enemy_start_cells) -> "PlacementState":
        """Explicite : des hints GameData restent des hints (la décision sera BLOCKED)."""
        return cls(map_id, phase, frozenset(hint_cells), CellSource.GAMEDATA_HINT, player_cell_id,
                   frozenset(enemy_start_cells) if enemy_start_cells is not None else None)


@dataclass(frozen=True)
class PlacementPolicy:
    goal: PlacementGoal = PlacementGoal.CLOSE_TO_ENEMIES


@dataclass(frozen=True)
class PlacementDecision:
    status: PlacementStatus
    target_cell_id: int | None = None
    reasons: tuple[str, ...] = ()
    scores: dict[int, int] = field(default_factory=dict)     # case → distance à l'ennemi le plus proche

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status.value, "target_cell_id": self.target_cell_id, "reasons": list(self.reasons),
                "scores": {str(cell): score for cell, score in sorted(self.scores.items())}, "actions_sent": "NONE"}


def _blocked(*reasons: str) -> PlacementDecision:
    return PlacementDecision(PlacementStatus.BLOCKED, None, tuple(reasons))


def _walking_distance(combat_map: CombatMap, start: int, enemies: frozenset[int]) -> int | None:
    best = None
    for enemy in sorted(enemies):
        # L'ennemi est la cible (atteinte sans être traversée) ; les autres ennemis bloquent.
        result = shortest_path(combat_map, start, enemy, occupied=enemies - {enemy}, stop_cells=frozenset({enemy}))
        if result.status in (PathStatus.FOUND, PathStatus.ALREADY_THERE):
            best = result.cost if best is None else min(best, result.cost)
    return best


def decide_placement(state: PlacementState, combat_map: CombatMap | None,
                     policy: PlacementPolicy = PlacementPolicy()) -> PlacementDecision:
    reasons = []
    if state.phase != "PLACEMENT":
        reasons.append(f"phase {state.phase or 'UNKNOWN'} : pas une phase de placement")
    if state.source is CellSource.GAMEDATA_HINT:
        reasons.append("indices rouge/bleu GameData non démontrés comme cases de placement actives")
    elif state.source is not CellSource.OBSERVED_ACTIVE or not state.available_start_cells:
        reasons.append("cases de placement disponibles non observables avec certitude")
    if state.player_cell_id is None:
        reasons.append("case du joueur inconnue")
    if state.enemy_start_cells is None:
        reasons.append("cases de départ ennemies inconnues")
    elif not state.enemy_start_cells:
        reasons.append("aucun ennemi observé")
    if combat_map is None or state.map_id is None or combat_map.map_id != state.map_id:
        reasons.append("topologie de la map indisponible")
    if reasons:
        return _blocked(*reasons)
    candidates = set(state.available_start_cells) - set(state.enemy_start_cells)
    candidates.add(state.player_cell_id)            # rester sur place est toujours une option
    scores: dict[int, int] = {}
    for cell in sorted(candidates):
        distance = _walking_distance(combat_map, cell, state.enemy_start_cells)
        if distance is not None:
            scores[cell] = distance
    if not scores:
        return _blocked("aucune case candidate ne mène à un ennemi : distance inconnue")
    far = policy.goal is PlacementGoal.FAR_FROM_ENEMIES
    target = min(scores, key=lambda cell: (-scores[cell] if far else scores[cell], cell))
    if state.player_cell_id in scores and scores[state.player_cell_id] == scores[target]:
        return PlacementDecision(PlacementStatus.STAY, state.player_cell_id, ("case actuelle déjà optimale",), scores)
    return PlacementDecision(PlacementStatus.MOVE, target,
                             (f"distance à l'ennemi le plus proche : {scores[target]}",), scores)
