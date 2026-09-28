"""FAST-5A0 : exécution **dry-run** d'un plan — journalise, ne touche jamais au client DOFUS.

Contrat préparé pour la boucle fermée du LOT 5B (un futur ``MouseActionExecutor`` l'implémentera) :

1. ``start(request)`` : une étape part avec un ``correlation_id`` et son effet attendu ;
2. ``verify(correlation_id, observed)`` : l'observation suivante confirme l'effet (SUCCEEDED), le
   contredit (FAILED) ou n'arrive pas à temps (TIMED_OUT).

En dry-run, aucune étape n'est exécutée : ``start`` renvoie ``DRY_RUN_NOT_EXECUTED`` et ``verify`` ne peut
comparer que des observations fournies par l'appelant (tests, rejeu). Un plan BLOCKED est refusé
(``REFUSED``). Aucune dépendance à la souris, au clavier, à Win32, à Qt ou à OpenCV.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol

from combatbot.combat.effects import Effect, check_effect
from combatbot.combat.planner import ExpectedState, Plan, PlanStatus, PlanStep
from combatbot.models import Action, CombatEvent

SENDS_INPUT = False   # vérifié par les tests : ce module n'envoie jamais d'entrée au jeu


class ActionEventKind(str, Enum):
    STARTED = "STARTED"
    DRY_RUN_NOT_EXECUTED = "DRY_RUN_NOT_EXECUTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    REFUSED = "REFUSED"


@dataclass(frozen=True)
class ActionRequest:
    correlation_id: str
    plan_id: str
    step_index: int
    step: PlanStep
    expected: ExpectedState
    timeout_s: float = 3.0


@dataclass(frozen=True)
class ActionEvent:
    kind: ActionEventKind
    correlation_id: str | None
    at: float
    detail: str = ""
    data: dict[str, object] = field(default_factory=dict)

    def to_combat_event(self) -> CombatEvent:
        level = "WARNING" if self.kind in (ActionEventKind.FAILED, ActionEventKind.TIMED_OUT,
                                           ActionEventKind.REFUSED) else "INFO"
        return CombatEvent(level, f"dry_run.{self.kind.value.lower()}", self.detail,
                           {"correlation_id": self.correlation_id, **self.data, "actions_sent": "NONE"})


class ClosedLoopExecutor(Protocol):
    """Contrat des exécuteurs de plan (dry-run aujourd'hui ; souris seulement dans un lot futur)."""
    def start(self, request: ActionRequest) -> ActionEvent: ...
    def verify(self, correlation_id: str, observed, *, now: float | None = None) -> ActionEvent: ...


def plan_id(plan: Plan) -> str:
    """Identifiant stable : le même plan donne toujours le même identifiant."""
    digest = hashlib.sha256(json.dumps(plan.to_dict(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return digest[:12]


@dataclass
class DryRunReport:
    plan_id: str
    status: str
    requests: list[ActionRequest]
    events: list[ActionEvent]

    def to_dict(self) -> dict[str, object]:
        return {"plan_id": self.plan_id, "status": self.status, "actions_sent": "NONE",
                "steps": [request.step.describe() for request in self.requests],
                "events": [{"kind": event.kind.value, "correlation_id": event.correlation_id,
                            "detail": event.detail} for event in self.events]}


class DryRunActionExecutor:
    """Implémente ``ports.ActionExecutor`` (compatibilité) et ``ClosedLoopExecutor`` sans jamais agir."""

    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 sink: Callable[[CombatEvent], None] | None = None) -> None:
        self.clock = clock
        self.sink = sink
        self.events: list[ActionEvent] = []
        self.legacy_actions: list[Action] = []
        self._pending: dict[str, tuple[ActionRequest, float]] = {}

    def _emit(self, event: ActionEvent) -> ActionEvent:
        self.events.append(event)
        if self.sink is not None:
            self.sink(event.to_combat_event())
        return event

    # -------------------------------------------------------------- ports.ActionExecutor (historique)
    def execute(self, action: Action) -> None:
        """Compatibilité avec ``ports.ActionExecutor`` : l'action est journalisée, jamais exécutée."""
        self.legacy_actions.append(action)
        self._emit(ActionEvent(ActionEventKind.DRY_RUN_NOT_EXECUTED, None, self.clock(),
                               f"action historique {action.kind} non exécutée (dry-run)",
                               {"kind": action.kind, "target": action.target}))

    # -------------------------------------------------------------- ClosedLoopExecutor
    def start(self, request: ActionRequest) -> ActionEvent:
        now = self.clock()
        self._pending[request.correlation_id] = (request, now)
        self._emit(ActionEvent(ActionEventKind.STARTED, request.correlation_id, now, request.step.describe(),
                               {"plan_id": request.plan_id, "step_index": request.step_index,
                                "expected": {"player_cell_id": request.expected.player_cell_id,
                                             "ap": request.expected.ap, "mp": request.expected.mp}}))
        return self._emit(ActionEvent(ActionEventKind.DRY_RUN_NOT_EXECUTED, request.correlation_id, now,
                                      f"{request.step.describe()} : aucune entrée envoyée au jeu"))

    def verify(self, correlation_id: str, observed, *, now: float | None = None) -> ActionEvent:
        """Juge l'effet de l'étape selon son type (``effects.check_effect``) : sert au rejeu aujourd'hui.

        ``observed`` : objet exposant player_cell_id / ap / mp (et turn / phase), ou None si aucune
        observation. PENDING, UNKNOWN ou aucune observation : en attente jusqu'au délai, puis TIMED_OUT ;
        jamais un succès.
        """
        if correlation_id not in self._pending:
            return self._emit(ActionEvent(ActionEventKind.REFUSED, correlation_id, self.clock(),
                                          "corrélation inconnue"))
        request, started = self._pending[correlation_id]
        now = self.clock() if now is None else now
        check = check_effect(request.step, observed) if observed is not None else None
        if check is not None and check.effect is Effect.CONTRADICTED:
            del self._pending[correlation_id]
            return self._emit(ActionEvent(ActionEventKind.FAILED, correlation_id, now,
                                          f"effet observé différent de l'effet attendu : {check.detail}",
                                          {"mismatches": check.mismatches}))
        if check is not None and check.effect is Effect.CONFIRMED:
            del self._pending[correlation_id]
            return self._emit(ActionEvent(ActionEventKind.SUCCEEDED, correlation_id, now,
                                          f"effet attendu observé : {check.detail}"))
        if now - started >= request.timeout_s:
            del self._pending[correlation_id]
            detail = check.detail if check is not None else "aucune observation"
            return self._emit(ActionEvent(ActionEventKind.TIMED_OUT, correlation_id, now,
                                          f"{detail} après {request.timeout_s:.1f} s"))
        return ActionEvent(ActionEventKind.STARTED, correlation_id, now,
                           "en attente d'observation" if check is None else check.detail)

    # -------------------------------------------------------------- plan complet
    def run_plan(self, plan: Plan, timeout_s: float = 3.0) -> DryRunReport:
        identifier = plan_id(plan)
        if plan.status is not PlanStatus.READY:
            event = self._emit(ActionEvent(ActionEventKind.REFUSED, None, self.clock(),
                                           "plan BLOCKED : rien n'est lancé", {"plan_id": identifier,
                                                                              "reasons": list(plan.blocked_reasons)}))
            return DryRunReport(identifier, PlanStatus.BLOCKED.value, [], [event])
        first = len(self.events)
        requests = []
        for index, step in enumerate(plan.steps):
            request = ActionRequest(f"{identifier}:{index}", identifier, index, step, step.expected, timeout_s)
            requests.append(request)
            self.start(request)
        return DryRunReport(identifier, PlanStatus.READY.value, requests, self.events[first:])
