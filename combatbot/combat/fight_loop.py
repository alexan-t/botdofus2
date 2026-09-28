"""FAST-6A : orchestrateur multi-tours offline (aucune entrée réelle connectée).

WAIT_PLAYER_TURN → RUN_SINGLE_TURN → WAIT_OTHER_TURN → WAIT_PLAYER_TURN → … → RESULT / ABORT.

- Ne joue jamais pendant OTHER : un tour ne démarre que sur une observation sûre de mon tour, et
  seulement après avoir vu le tour quitter PLAYER (fin de tour prouvée par 5C).
- UNKNOWN (tour/phase illisible, frame absente, observation non sûre) est toléré pendant l'attente
  jusqu'à ``unknown_grace_s`` d'affilée ; au-delà : ABORT.
- RESULTS observé → RESULT. Fenêtre / layout changés, map différente de celle du combat → ABORT.
- Arrêt d'urgence : vérifié à chaque itération ; le verrou reste actif (aucune reprise automatique).
- ``max_turns`` : plafond de sécurité. Journal : celui du coordinateur (tours + actions).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from combatbot.combat.closed_loop import Observation
from combatbot.combat.screen_actions import ScreenGeometry
from combatbot.combat.turn_runner import SingleTurnRunner, TurnOutcome, TurnReport


class FightPhase(str, Enum):
    WAIT_PLAYER_TURN = "WAIT_PLAYER_TURN"
    RUN_SINGLE_TURN = "RUN_SINGLE_TURN"
    WAIT_OTHER_TURN = "WAIT_OTHER_TURN"
    RESULT = "RESULT"
    ABORT = "ABORT"


class FightOutcome(str, Enum):
    RESULT = "RESULT"
    ABORTED = "ABORTED"


@dataclass(frozen=True)
class FightConfig:
    max_turns: int = 40
    wait_turn_s: float = 180.0            # attente maximale de mon tour (tours adverses compris)
    unknown_grace_s: float = 10.0         # UNKNOWN continu toléré pendant l'attente
    poll_interval_s: float = 0.25


@dataclass
class FightReport:
    outcome: FightOutcome
    reason: str
    turns: list[TurnReport] = field(default_factory=list)
    phases: list[tuple[str, float, str]] = field(default_factory=list)

    @property
    def actions_sent(self) -> int:
        return sum(turn.actions_sent for turn in self.turns)

    def to_dict(self) -> dict[str, object]:
        return {"outcome": self.outcome.value, "reason": self.reason, "actions_sent": self.actions_sent,
                "turns": [turn.to_dict() for turn in self.turns],
                "phases": [{"phase": phase, "at": at, "detail": detail} for phase, at, detail in self.phases]}


class FightLoop:
    def __init__(self, observer, turn_runner: SingleTurnRunner,
                 geometry_provider: Callable[[], ScreenGeometry | None], *, config: FightConfig = FightConfig(),
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.observer = observer
        self.turn_runner = turn_runner
        self.geometry_provider = geometry_provider
        self.config = config
        self.clock = clock
        self.sleep = sleep

    @property
    def stop(self):
        return self.turn_runner.stop

    def _phase(self, report: FightReport, phase: FightPhase, detail: str = "") -> None:
        report.phases.append((phase.value, self.clock(), detail))
        self.turn_runner.coordinator.journal.record("FIGHT", phase.value, detail=detail)

    def _end(self, report: FightReport, outcome: FightOutcome, reason: str) -> FightReport:
        self._phase(report, FightPhase.RESULT if outcome is FightOutcome.RESULT else FightPhase.ABORT, reason)
        report.outcome, report.reason = outcome, reason
        return report

    def _wait_player_turn(self, report: FightReport, geometry: ScreenGeometry
                          ) -> tuple[Observation | None, str | None, bool]:
        """(observation de mon tour, raison d'arrêt, fin de combat)."""
        started = self.clock()
        unknown_since: float | None = None
        while True:
            if self.stop.triggered:
                return None, f"arrêt d'urgence : {self.stop.reason}", False
            now = self.clock()
            if now - started > self.config.wait_turn_s:
                return None, "mon tour n'est pas revenu à temps", False
            observation = self.observer.observe()
            known = False
            if observation is not None:
                state = observation.state
                if observation.hwnd != geometry.hwnd or observation.layout_digest != geometry.layout_digest:
                    return None, "fenêtre ou layout changé", False
                if state.phase == "RESULTS":
                    return None, "fin de combat observée", True
                if state.map_id is not None and state.map_id != geometry.map_id:
                    return None, "map différente de celle du combat", False
                if state.phase == "FIGHTING" and state.turn == "PLAYER" and state.safe_for_decision is True:
                    return observation, None, False
                known = state.phase == "FIGHTING" and state.turn == "OTHER"
            if known:
                unknown_since = None
            else:
                unknown_since = now if unknown_since is None else unknown_since
                if now - unknown_since > self.config.unknown_grace_s:
                    return None, f"état inconnu depuis plus de {self.config.unknown_grace_s:.0f} s", False
            self.sleep(self.config.poll_interval_s)

    def run(self) -> FightReport:
        report = FightReport(FightOutcome.ABORTED, "")
        for number in range(1, self.config.max_turns + 1):
            geometry = self.geometry_provider()
            if geometry is None:
                return self._end(report, FightOutcome.ABORTED, "géométrie écran inconnue")
            self._phase(report, FightPhase.WAIT_PLAYER_TURN, f"tour {number}")
            start, reason, ended = self._wait_player_turn(report, geometry)
            if ended:
                return self._end(report, FightOutcome.RESULT, reason or "")
            if start is None:
                return self._end(report, FightOutcome.ABORTED, reason or "")
            self._phase(report, FightPhase.RUN_SINGLE_TURN, f"tour {number}")
            turn = self.turn_runner.run(geometry, start)
            report.turns.append(turn)
            if turn.outcome is TurnOutcome.COMBAT_ENDED:
                return self._end(report, FightOutcome.RESULT, turn.reason)
            if turn.outcome is not TurnOutcome.COMPLETE:
                return self._end(report, FightOutcome.ABORTED, f"tour {number} : {turn.outcome.value} — {turn.reason}")
            self._phase(report, FightPhase.WAIT_OTHER_TURN, turn.reason)
        return self._end(report, FightOutcome.ABORTED, f"limite de sécurité de {self.config.max_turns} tours atteinte")
