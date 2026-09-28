"""FAST-5C : orchestrateur d'UN tour — sans entrée réelle par défaut (exécuteur injecté).

WAIT_PLAYER_TURN → OBSERVE → PLAN → EXECUTE_STEP → VERIFY → (OBSERVE → PLAN …) → END_TURN →
VERIFY_OTHER_TURN → COMPLETE.

- Le plan est **recalculé après chaque étape prouvée**, depuis l'observation qui a prouvé l'effet
  (les lancers déjà faits ce tour sont reportés dans l'état). Seule la première étape d'un plan est
  exécutée : jamais deux actions sans preuve de la précédente.
- Un ennemi présent au début du tour qui disparaît de l'observation arrête le tour : sa mort n'est
  pas prouvée (PV non lus).
- Fin : COMPLETE (fin de tour prouvée), COMBAT_ENDED, BLOCKED (plan refusé), FAILED, TIMED_OUT,
  ABORTED (UNKNOWN, arrêt d'urgence, fenêtre/map/layout, limite d'étapes…).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Iterable

from combatbot.combat.closed_loop import ClosedLoopCoordinator, LoopState, Observation, StepRecord, critical_change
from combatbot.combat.executor import plan_id as compute_plan_id
from combatbot.combat.pathfinding import CombatMap
from combatbot.combat.planner import Plan, PlanStatus, StepKind, plan_turn
from combatbot.combat.screen_actions import ScreenGeometry
from combatbot.combat.spells import CombatSpell
from combatbot.combat.state import RealCombatState
from combatbot.combat.targeting import CONSERVATIVE_RULES, TargetingRules


class TurnPhase(str, Enum):
    WAIT_PLAYER_TURN = "WAIT_PLAYER_TURN"
    OBSERVE = "OBSERVE"
    PLAN = "PLAN"
    EXECUTE_STEP = "EXECUTE_STEP"
    VERIFY = "VERIFY"
    END_TURN = "END_TURN"
    VERIFY_OTHER_TURN = "VERIFY_OTHER_TURN"
    COMPLETE = "COMPLETE"


class TurnOutcome(str, Enum):
    COMPLETE = "COMPLETE"
    COMBAT_ENDED = "COMBAT_ENDED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    ABORTED = "ABORTED"


@dataclass(frozen=True)
class TurnConfig:
    wait_player_turn_s: float = 30.0
    poll_interval_s: float = 0.2
    max_steps: int = 12


@dataclass
class TurnReport:
    outcome: TurnOutcome
    reason: str
    phases: list[tuple[str, float, str]] = field(default_factory=list)
    steps: list[StepRecord] = field(default_factory=list)
    plans: list[list[str]] = field(default_factory=list)
    last_observation: Observation | None = None

    @property
    def actions_sent(self) -> int:
        return sum(step.actions_sent for step in self.steps)

    def to_dict(self) -> dict[str, object]:
        return {"outcome": self.outcome.value, "reason": self.reason, "actions_sent": self.actions_sent,
                "phases": [{"phase": phase, "at": at, "detail": detail} for phase, at, detail in self.phases],
                "steps": [step.to_dict() for step in self.steps], "plans": self.plans}


Planner = Callable[[RealCombatState], Plan]


def make_planner(map_provider: Callable[[int], CombatMap | None], spells: Iterable[CombatSpell], *,
                 rules: TargetingRules = CONSERVATIVE_RULES) -> Planner:
    spells = tuple(spells)

    def planner(state: RealCombatState) -> Plan:
        combat_map = map_provider(state.map_id) if state.map_id is not None else None
        return plan_turn(state, combat_map, spells, rules=rules)
    return planner


_OUTCOME = {LoopState.FAILED: TurnOutcome.FAILED, LoopState.TIMED_OUT: TurnOutcome.TIMED_OUT,
            LoopState.ABORTED: TurnOutcome.ABORTED}


class SingleTurnRunner:
    def __init__(self, observer, planner: Planner, coordinator: ClosedLoopCoordinator, *,
                 config: TurnConfig = TurnConfig(), clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.observer = observer
        self.planner = planner
        self.coordinator = coordinator
        self.config = config
        self.clock = clock
        self.sleep = sleep

    @property
    def stop(self):
        return self.coordinator.guard.stop

    def _phase(self, report: TurnReport, phase: TurnPhase, detail: str = "") -> None:
        report.phases.append((phase.value, self.clock(), detail))
        self.coordinator.journal.record("TURN", phase.value, detail=detail)

    def _finish(self, report: TurnReport, outcome: TurnOutcome, reason: str) -> TurnReport:
        report.outcome, report.reason = outcome, reason
        self.coordinator.journal.record("TURN", "END", outcome=outcome.value, reason=reason)
        return report

    def wait_player_turn(self, report: TurnReport, geometry: ScreenGeometry) -> Observation | None:
        """Attend une observation sûre de mon tour ; UNKNOWN toléré jusqu'au délai (rien n'est envoyé)."""
        self._phase(report, TurnPhase.WAIT_PLAYER_TURN)
        started = self.clock()
        while not self.stop.triggered and self.clock() - started <= self.config.wait_player_turn_s:
            observation = self.observer.observe()
            if observation is not None:
                report.last_observation = observation
                state = observation.state
                if state.phase is not None and state.phase not in ("FIGHTING", "PLACEMENT"):
                    self._finish(report, TurnOutcome.COMBAT_ENDED, f"phase {state.phase}")
                    return None
                if observation.hwnd != geometry.hwnd or observation.layout_digest != geometry.layout_digest:
                    self._finish(report, TurnOutcome.ABORTED, "fenêtre ou layout changé")
                    return None
                if state.phase == "FIGHTING" and state.turn == "PLAYER" and state.safe_for_decision is True:
                    return observation
            self.sleep(self.config.poll_interval_s)
        if self.stop.triggered:
            self._finish(report, TurnOutcome.ABORTED, f"arrêt d'urgence : {self.stop.reason}")
        else:
            self._finish(report, TurnOutcome.TIMED_OUT, "mon tour n'a pas été observé à temps")
        return None

    def run(self, geometry: ScreenGeometry, start: Observation | None = None) -> TurnReport:
        """``start`` : observation sûre de mon tour déjà obtenue (boucle multi-tours), sinon on l'attend."""
        report = TurnReport(TurnOutcome.ABORTED, "")
        if start is not None and start.state.turn == "PLAYER" and start.state.safe_for_decision is True:
            current = start
            report.last_observation = start
        else:
            current = self.wait_player_turn(report, geometry)
        if current is None:
            return report
        self.coordinator.guard.begin_turn()
        initial_enemies = {enemy.id for enemy in current.state.enemies}
        casts: dict[str, int] = {}
        casts_on_target: dict[tuple[str, str], int] = {}
        for index in range(self.config.max_steps):
            self._phase(report, TurnPhase.OBSERVE, f"frame {current.frame_id}")
            if self.stop.triggered:
                return self._finish(report, TurnOutcome.ABORTED, f"arrêt d'urgence : {self.stop.reason}")
            change = critical_change(current, geometry)
            if change:
                return self._finish(report, TurnOutcome.ABORTED, change)
            vanished = sorted(initial_enemies - {enemy.id for enemy in current.state.enemies})
            if vanished:
                return self._finish(report, TurnOutcome.ABORTED,
                                    f"ennemi(s) {', '.join(vanished)} disparu(s) : mort non prouvée")
            state = replace(current.state, casts_this_turn=dict(casts), casts_on_target=dict(casts_on_target))
            self._phase(report, TurnPhase.PLAN)
            plan = self.planner(state)
            report.plans.append(plan.describe())
            if plan.status is not PlanStatus.READY:
                return self._finish(report, TurnOutcome.BLOCKED, "plan refusé : " + " ; ".join(plan.blocked_reasons))
            step = plan.steps[0]
            is_end = step.kind is StepKind.END_TURN
            self._phase(report, TurnPhase.END_TURN if is_end else TurnPhase.EXECUTE_STEP, step.describe())
            record = self.coordinator.run_step(step, plan_id=compute_plan_id(plan), index=index, geometry=geometry,
                                               before=current, plan_created_at=self.clock())
            report.steps.append(record)
            self._phase(report, TurnPhase.VERIFY_OTHER_TURN if is_end else TurnPhase.VERIFY, record.state.value)
            report.last_observation = record.last_observation or current
            if record.state is not LoopState.SUCCEEDED:
                last = record.last_observation
                if last is not None and last.state.phase == "RESULTS":
                    return self._finish(report, TurnOutcome.COMBAT_ENDED, record.reason)
                return self._finish(report, _OUTCOME.get(record.state, TurnOutcome.ABORTED), record.reason)
            if is_end:
                self._phase(report, TurnPhase.COMPLETE, record.reason)
                ended = record.last_observation is not None and record.last_observation.state.phase == "RESULTS"
                return self._finish(report, TurnOutcome.COMBAT_ENDED if ended else TurnOutcome.COMPLETE,
                                    record.reason)
            if step.kind is StepKind.CAST and step.spell_key and step.target_id:
                casts[step.spell_key] = casts.get(step.spell_key, 0) + 1
                key = (step.spell_key, step.target_id)
                casts_on_target[key] = casts_on_target.get(key, 0) + 1
            current = record.last_observation or current
        return self._finish(report, TurnOutcome.ABORTED, f"limite de {self.config.max_steps} étapes atteinte")
