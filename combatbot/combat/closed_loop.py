"""FAST-5B : coordinateur en boucle fermée — une étape n'est suivie d'une autre qu'après preuve.

Pour chaque ``PlanStep`` :

1. PRECHECK : arrêt d'urgence, observation sûre, même fenêtre / layout / map que la géométrie,
   préconditions de l'étape (cellule de départ, PA/PM, cible toujours sur sa cellule) ;
2. traduction 5A1 en ``ScreenAction`` (REFUSED → ABORTED) ;
3. READY_TO_SEND : ``SafetyGuard`` puis envoi par l'exécuteur injecté, action par action ;
4. WAITING_EFFECT : attente d'une observation **nouvelle** (postérieure à l'envoi) ;
5. comparaison selon le type d'étape (``effects.check_effect``) :
   CONFIRMED → SUCCEEDED, CONTRADICTED → FAILED, aucune observation valable → TIMED_OUT,
   UNKNOWN / observation non sûre → ABORTED (après ``unknown_grace_s``, 0 par défaut),
   fenêtre / calibration / map changée, tour perdu, fin de combat → ABORTED.

États : IDLE, PRECHECK, READY_TO_SEND, WAITING_EFFECT, SUCCEEDED, FAILED, TIMED_OUT, ABORTED,
FINISHED (plan entier réussi). Chaque étape porte ``correlation_id`` = ``plan_id:step_index``.
Module pur : observateur, exécuteur, horloge et attente sont injectés (testable sans DOFUS).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Mapping, Protocol

from combatbot.combat.effects import Effect, check_effect
from combatbot.combat.executor import plan_id as compute_plan_id
from combatbot.combat.planner import Plan, PlanStatus, PlanStep, StepKind
from combatbot.combat.safety import ActionContext, ActionJournal, SafetyGuard
from combatbot.combat.screen_actions import ScreenAction, ScreenGeometry, TranslationStatus, translate_step
from combatbot.combat.spells import CombatSpell
from combatbot.combat.state import RealCombatState


class LoopState(str, Enum):
    IDLE = "IDLE"
    PRECHECK = "PRECHECK"
    READY_TO_SEND = "READY_TO_SEND"
    WAITING_EFFECT = "WAITING_EFFECT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    ABORTED = "ABORTED"
    FINISHED = "FINISHED"


TERMINAL = frozenset({LoopState.SUCCEEDED, LoopState.FAILED, LoopState.TIMED_OUT, LoopState.ABORTED,
                      LoopState.FINISHED})


@dataclass(frozen=True)
class Observation:
    """Une frame observée : état combat + identité de la fenêtre et du layout qui l'ont produite."""
    state: RealCombatState
    at: float
    frame_id: int
    hwnd: int | None
    layout_digest: str | None


class Observer(Protocol):
    def observe(self) -> Observation | None: ...          # None : aucune frame exploitable


class ActionSender(Protocol):
    def send(self, action: ScreenAction, geometry: ScreenGeometry, *, correlation_id: str): ...


@dataclass(frozen=True)
class LoopConfig:
    poll_interval_s: float = 0.1
    effect_timeout_s: float = 3.0
    unknown_grace_s: float = 0.0          # UNKNOWN pendant une attente → ABORTED immédiatement


@dataclass
class StepRecord:
    correlation_id: str
    plan_id: str
    step_index: int
    step: PlanStep
    state: LoopState = LoopState.IDLE
    transitions: list[tuple[str, float, str]] = field(default_factory=list)
    actions_sent: int = 0
    last_observation: Observation | None = None

    @property
    def reason(self) -> str:
        return self.transitions[-1][2] if self.transitions else ""

    def to_dict(self) -> dict[str, object]:
        return {"correlation_id": self.correlation_id, "plan_id": self.plan_id, "step_index": self.step_index,
                "step": self.step.describe(), "state": self.state.value, "actions_sent": self.actions_sent,
                "transitions": [{"state": state, "at": at, "detail": detail}
                                for state, at, detail in self.transitions]}


@dataclass
class PlanRecord:
    plan_id: str
    state: LoopState
    steps: list[StepRecord]
    reason: str = ""

    @property
    def last_observation(self) -> Observation | None:
        return self.steps[-1].last_observation if self.steps else None


def critical_change(observation: Observation, geometry: ScreenGeometry) -> str | None:
    if observation.hwnd != geometry.hwnd:
        return "fenêtre DOFUS changée"
    if observation.layout_digest != geometry.layout_digest:
        return "layout / calibration changé"
    if observation.state.map_id != geometry.map_id:
        return "map changée ou inconnue"
    return None


def precondition_failures(step: PlanStep, observation: Observation, geometry: ScreenGeometry) -> tuple[str, ...]:
    state = observation.state
    reasons = list(state.blocking_unknowns())
    change = critical_change(observation, geometry)
    if change:
        reasons.append(change)
    if step.kind is StepKind.MOVE:
        if state.player_cell_id != step.origin_cell_id:
            reasons.append("joueur absent de la cellule de départ du déplacement")
        if state.mp is not None and state.mp < step.mp_cost:
            reasons.append(f"PM insuffisants ({state.mp} < {step.mp_cost})")
    elif step.kind is StepKind.CAST:
        if state.player_cell_id != step.expected.player_cell_id:
            reasons.append("joueur absent de la cellule de lancer")
        if state.ap is not None and state.ap < step.ap_cost:
            reasons.append(f"PA insuffisants ({state.ap} < {step.ap_cost})")
        target = next((enemy for enemy in state.enemies if enemy.id == step.target_id), None)
        if target is None or not target.observed or target.cell_id != step.target_cell_id:
            reasons.append(f"cible {step.target_id} absente ou déplacée")
    return tuple(reasons)


class ClosedLoopCoordinator:
    def __init__(self, observer: Observer, sender: ActionSender, *, guard: SafetyGuard,
                 spells: Mapping[str, CombatSpell], config: LoopConfig = LoopConfig(),
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 journal: ActionJournal | None = None) -> None:
        self.observer = observer
        self.sender = sender
        self.guard = guard
        self.spells = spells
        self.config = config
        self.clock = clock
        self.sleep = sleep
        self.journal = journal if journal is not None else ActionJournal()

    def _move(self, record: StepRecord, state: LoopState, detail: str = "") -> StepRecord:
        record.state = state
        record.transitions.append((state.value, self.clock(), detail))
        self.journal.record("STEP", record.correlation_id, state=state.value, detail=detail,
                            step=record.step.describe())
        return record

    def _send(self, record: StepRecord, actions: tuple[ScreenAction, ...], geometry: ScreenGeometry,
              before: Observation, plan_created_at: float) -> bool:
        for index, action in enumerate(actions):
            wait = self.guard.seconds_until_click_allowed()        # la cadence espace les clics, sans abandon
            if wait > 0:
                self.sleep(wait)
            context = ActionContext(before.hwnd, before.layout_digest, before.state.map_id, before.at,
                                    before.state.safe_for_decision)
            verdict = self.guard.check(action, expected_hwnd=geometry.hwnd, expected_layout=geometry.layout_digest,
                                       expected_map=geometry.map_id, context=context,
                                       plan_created_at=plan_created_at)
            if not verdict.allowed:
                self._move(record, LoopState.ABORTED, "garde-fou : " + " ; ".join(verdict.reasons))
                return False
            action_id = f"{record.correlation_id}#{index}"
            self.journal.before(action_id, action=action.to_dict())
            try:
                result = self.sender.send(action, geometry, correlation_id=action_id)
            except Exception as exc:   # un exécuteur qui lève ne doit jamais laisser le journal ouvert
                self.journal.after(action_id, sent=False, reason=f"exception : {exc}")
                self._move(record, LoopState.FAILED, f"exécuteur en erreur : {exc}")
                return False
            self.journal.after(action_id, sent=bool(result.sent), reasons=list(result.reasons))
            if not result.sent:
                self._move(record, LoopState.FAILED, "exécuteur : " + " ; ".join(result.reasons))
                return False
            self.guard.record_action()
            record.actions_sent += 1
        return True

    def _wait_effect(self, record: StepRecord, geometry: ScreenGeometry, before: Observation) -> StepRecord:
        step, config = record.step, self.config
        sent_at, last_frame = self.clock(), before.frame_id
        unknown_since: float | None = None
        while True:
            if self.guard.stop.triggered:
                return self._move(record, LoopState.ABORTED, f"arrêt d'urgence : {self.guard.stop.reason}")
            now = self.clock()
            if now - sent_at > config.effect_timeout_s:
                return self._move(record, LoopState.TIMED_OUT,
                                  f"aucun effet prouvé après {config.effect_timeout_s:.1f} s")
            observation = self.observer.observe()
            if observation is None or observation.frame_id <= last_frame or observation.at <= sent_at:
                self.sleep(config.poll_interval_s)
                continue
            last_frame = observation.frame_id
            record.last_observation = observation
            change = critical_change(observation, geometry)
            if change:
                return self._move(record, LoopState.ABORTED, change)
            state = observation.state
            if step.kind is not StepKind.END_TURN:
                if state.phase is not None and state.phase != "FIGHTING":
                    return self._move(record, LoopState.ABORTED, f"phase {state.phase} pendant l'étape")
                if state.turn is not None and state.turn != "PLAYER":
                    return self._move(record, LoopState.ABORTED, "tour perdu pendant l'étape")
            check = check_effect(step, state)
            # Pendant MOVE / CAST, une observation non sûre (grille UNKNOWN, joueur perdu…) ne prouve rien.
            unsafe = step.kind is not StepKind.END_TURN and state.safe_for_decision is not True
            if check.effect is Effect.UNKNOWN or unsafe:
                unknown_since = now if unknown_since is None else unknown_since
                if now - unknown_since >= config.unknown_grace_s:
                    detail = "observation non sûre" if unsafe else check.detail
                    return self._move(record, LoopState.ABORTED, f"UNKNOWN : {detail}")
                self.sleep(config.poll_interval_s)
                continue
            if check.effect is Effect.CONTRADICTED:
                return self._move(record, LoopState.FAILED, check.detail)
            if check.effect is Effect.CONFIRMED:
                return self._move(record, LoopState.SUCCEEDED, check.detail)
            unknown_since = None
            self.sleep(config.poll_interval_s)

    def run_step(self, step: PlanStep, *, plan_id: str, index: int, geometry: ScreenGeometry,
                 before: Observation, plan_created_at: float) -> StepRecord:
        record = StepRecord(f"{plan_id}:{index}", plan_id, index, step, last_observation=before)
        self._move(record, LoopState.PRECHECK, step.describe())
        if self.guard.stop.triggered:
            return self._move(record, LoopState.ABORTED, f"arrêt d'urgence : {self.guard.stop.reason}")
        failures = precondition_failures(step, before, geometry)
        if failures:
            return self._move(record, LoopState.ABORTED, "préconditions : " + " ; ".join(failures))
        translation = translate_step(step, geometry, self.spells)
        if translation.status is not TranslationStatus.READY:
            return self._move(record, LoopState.ABORTED, "traduction refusée : " + " ; ".join(translation.reasons))
        self._move(record, LoopState.READY_TO_SEND, f"{len(translation.actions)} action(s)")
        if not self._send(record, translation.actions, geometry, before, plan_created_at):
            return record
        self._move(record, LoopState.WAITING_EFFECT)
        return self._wait_effect(record, geometry, before)

    def run_plan(self, plan: Plan, geometry: ScreenGeometry, before: Observation,
                 plan_created_at: float) -> PlanRecord:
        """Exécute les étapes dans l'ordre ; s'arrête à la première qui n'est pas SUCCEEDED."""
        identifier = compute_plan_id(plan)
        if plan.status is not PlanStatus.READY:
            return PlanRecord(identifier, LoopState.ABORTED, [], "plan BLOCKED : " + " ; ".join(plan.blocked_reasons))
        records: list[StepRecord] = []
        current = before
        for index, step in enumerate(plan.steps):
            record = self.run_step(step, plan_id=identifier, index=index, geometry=geometry, before=current,
                                   plan_created_at=plan_created_at)
            records.append(record)
            if record.state is not LoopState.SUCCEEDED:
                return PlanRecord(identifier, record.state, records, record.reason)
            current = record.last_observation or current
        return PlanRecord(identifier, LoopState.FINISHED, records)
