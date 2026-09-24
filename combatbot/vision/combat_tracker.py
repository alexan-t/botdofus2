"""Stabilisation temporelle des observations et suivi prudent des ennemis."""

from __future__ import annotations

from collections import deque
from dataclasses import replace

from combatbot.vision.combat_models import CombatObservation, EnemyObservation


class CombatObservationTracker:
    def __init__(self, history_size: int = 5, disappearance_tolerance: int = 2) -> None:
        self.history: deque[CombatObservation] = deque(maxlen=max(3, history_size))
        self.disappearance_tolerance = max(1, disappearance_tolerance)
        self._combat: bool | None = None
        self._turn: bool | None = None
        self._had_combat = False
        self._next_enemy_id = 1
        self._tracked: dict[str, tuple[EnemyObservation, int]] = {}

    @staticmethod
    def _stable(values: list[bool | None], previous: bool | None, required: int = 2) -> bool | None:
        recent = values[-3:]
        true_count = sum(value is True for value in recent)
        false_count = sum(value is False for value in recent)
        if true_count >= required:
            return True
        if false_count >= required:
            return False
        return previous

    def update(self, raw: CombatObservation) -> CombatObservation:
        self.history.append(raw)
        prior_combat = self._combat
        # UNKNOWN (LOT 3B-3) never counts as a vote for combat or for exploration.
        votes = [None if item.combat_state == "UNKNOWN" else item.combat_detected for item in self.history]
        self._combat = self._stable(votes, self._combat)
        turn_values = [item.player_turn for item in self.history if item.combat_detected]
        self._turn = self._stable(turn_values, self._turn)
        # LOT 3B-5 : avec la grille GameData, EntityTracker a déjà attribué des identités globales ;
        # l'association gloutonne historique ne reste active que pour l'ancien pipeline.
        enemies = raw.enemies if raw.entities is not None else self._track_enemies(raw.enemies)
        result = raw.result
        if self._combat is True:
            self._had_combat = True
            result = None
        elif prior_combat is True and self._combat is False and self._had_combat:
            # La disparition stable de l'interface prouve la fin, mais pas son issue.
            result = raw.result or "unknown"
        return replace(
            raw,
            combat_detected=bool(self._combat) if self._combat is not None else raw.combat_detected,
            player_turn=self._turn if self._combat else None,
            enemies=enemies,
            result=result,
        )

    def _track_enemies(self, detected: tuple[EnemyObservation, ...]) -> tuple[EnemyObservation, ...]:
        unused = set(self._tracked)
        output: list[EnemyObservation] = []
        for enemy in detected:
            match = None
            best_distance = float("inf")
            for identifier in unused:
                prior, _missing = self._tracked[identifier]
                distance = prior.cell.distance(enemy.cell)
                if distance < best_distance and distance <= 2:
                    best_distance, match = distance, identifier
            identifier = match or f"enemy_{self._next_enemy_id}"
            if match is None:
                self._next_enemy_id += 1
            else:
                unused.discard(match)
            tracked = replace(enemy, id=identifier)
            self._tracked[identifier] = (tracked, 0)
            output.append(tracked)
        for identifier in unused:
            prior, missing = self._tracked[identifier]
            missing += 1
            if missing <= self.disappearance_tolerance:
                self._tracked[identifier] = (prior, missing)
                # Une disparition brève ne supprime pas l'identité, mais réduit sa confiance.
                output.append(replace(prior, confidence=prior.confidence * (0.45 ** missing)))
            else:
                del self._tracked[identifier]
        return tuple(sorted(output, key=lambda item: item.id))

