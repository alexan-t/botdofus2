"""FAST-5A0 : exécuteur dry-run (journalise, n'envoie jamais rien) et pureté du cœur combat."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from combatbot.combat import executor as executor_module
from combatbot.combat.executor import ActionEventKind, ActionRequest, DryRunActionExecutor, plan_id
from combatbot.combat.planner import ExpectedState, PlanStatus, plan_turn
from combatbot.models import Action
from combatbot.ports import ActionExecutor
from tests.test_combat_planner import HYPOTHESIS, MAP, spell, state


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def ready_plan():
    plan = plan_turn(state(), MAP, [spell()], rules=HYPOTHESIS)
    assert plan.status is PlanStatus.READY
    return plan


def test_run_plan_logs_every_step_without_executing() -> None:
    sink = []
    executor = DryRunActionExecutor(clock=Clock(), sink=sink.append)
    plan = ready_plan()
    report = executor.run_plan(plan)
    assert report.status == "READY" and report.to_dict()["steps"] == plan.describe()
    assert [request.correlation_id for request in report.requests] == [f"{plan_id(plan)}:{i}" for i in range(3)]
    kinds = [event.kind for event in report.events]
    assert kinds == [ActionEventKind.STARTED, ActionEventKind.DRY_RUN_NOT_EXECUTED] * 3
    assert all(event.context["actions_sent"] == "NONE" for event in sink)
    assert sink[0].event == "dry_run.started" and "CAST profile:1 ON E1" in sink[0].message
    assert json.loads(json.dumps(report.to_dict()))["actions_sent"] == "NONE"


def test_blocked_plan_is_refused() -> None:
    executor = DryRunActionExecutor(clock=Clock())
    report = executor.run_plan(plan_turn(state(ap=None), MAP, [spell()], rules=HYPOTHESIS))
    assert report.status == "BLOCKED" and report.requests == []
    assert report.events[0].kind is ActionEventKind.REFUSED and "PA inconnus" in report.events[0].data["reasons"][0]


def test_closed_loop_contract_success_failure_timeout() -> None:
    clock = Clock()
    executor = DryRunActionExecutor(clock=clock)
    plan = ready_plan()
    step = plan.steps[0]
    request = ActionRequest("c1", plan_id(plan), 0, step, step.expected, timeout_s=2.0)
    executor.start(request)
    assert executor.verify("c1", ExpectedState(step.expected.player_cell_id, step.expected.ap, step.expected.mp)).kind \
        is ActionEventKind.SUCCEEDED
    executor.start(ActionRequest("c2", "p", 0, step, step.expected))
    failed = executor.verify("c2", ExpectedState(step.expected.player_cell_id, step.expected.ap + 3, step.expected.mp))
    assert failed.kind is ActionEventKind.FAILED and "ap" in failed.data["mismatches"]
    executor.start(ActionRequest("c3", "p", 0, step, step.expected, timeout_s=2.0))
    assert executor.verify("c3", None, now=clock.now + 1).kind is ActionEventKind.STARTED   # encore en attente
    assert executor.verify("c3", None, now=clock.now + 2.5).kind is ActionEventKind.TIMED_OUT
    assert executor.verify("c3", None).kind is ActionEventKind.REFUSED    # déjà close


def test_legacy_action_executor_protocol_is_honoured_without_acting() -> None:
    executor: ActionExecutor = DryRunActionExecutor(clock=Clock())
    executor.execute(Action("cast", "E1", 1, cost=3))
    assert executor.legacy_actions[0].kind == "cast"
    assert executor.events[-1].kind is ActionEventKind.DRY_RUN_NOT_EXECUTED


def test_plan_id_is_stable() -> None:
    assert plan_id(ready_plan()) == plan_id(ready_plan())


def test_combat_core_is_pure_and_sends_no_input() -> None:
    assert executor_module.SENDS_INPUT is False
    code = ("import sys; import combatbot.combat.pathfinding, combatbot.combat.spells, combatbot.combat.targeting, "
            "combatbot.combat.state, combatbot.combat.planner, combatbot.combat.executor; "
            "bad = [m for m in ('cv2', 'PySide6', 'rapidocr', 'pyautogui', 'onnxruntime') if m in sys.modules]; "
            "print(','.join(bad))")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""
    forbidden = ("pyautogui", "SendInput", "mouse_event", "keybd_event", "windll", "PostMessage", "SetCursorPos")
    for path in (root / "combatbot" / "combat").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not [token for token in forbidden if token in text], path.name
