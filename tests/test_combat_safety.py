"""FAST-SAFETY / FAST-5A2 : garde-fous et exécuteur souris désactivé (aucune entrée réelle)."""
from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

import pytest

from combatbot.combat.mouse_executor import MouseActionExecutor, RecordingBackend, WindowState
from combatbot.combat.safety import (
    REAL_INPUT_ENV, REAL_INPUT_ENV_VALUE, REAL_INPUT_GATE, ActionContext, ActionJournal, EmergencyStop,
    RealInputBlocked, RealInputGate, SafetyGuard, SafetyLimits,
)
from combatbot.combat.screen_actions import translate_step
from combatbot.input.win32_mouse import Win32MouseBackend
from tests.test_combat_screen_actions import END, SPELLS, geometry


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Probe:
    def __init__(self, geo, **changes) -> None:
        origin, size = geo.layout.client_screen_origin, geo.layout.client_size
        self.state = replace(WindowState(geo.hwnd, geo.hwnd, round(origin.x), round(origin.y), size.width,
                                         size.height), **changes)
        self.calls = 0

    def current(self, hwnd: int) -> WindowState:
        self.calls += 1
        return self.state


GEO = geometry()
ACTION = translate_step(END, GEO, SPELLS).actions[0]


def test_emergency_stop_latches_until_explicit_rearm() -> None:
    stop = EmergencyStop()
    threads = [threading.Thread(target=stop.trigger, args=(f"F9 #{i}",)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert stop.triggered and stop.reason.startswith("F9 #")
    first = stop.reason
    stop.trigger("autre")
    assert stop.reason == first
    stop.rearm()
    assert not stop.triggered


def test_real_input_gate_is_closed_by_default_and_under_pytest() -> None:
    assert REAL_INPUT_GATE.refusal() is not None
    assert "tests" in REAL_INPUT_GATE.refusal()
    assert RealInputGate({}).refusal().startswith("entrée réelle non autorisée")
    gate = RealInputGate({REAL_INPUT_ENV: REAL_INPUT_ENV_VALUE})
    assert gate.refusal() == "entrée réelle non armée pour cette session"
    assert gate.authorize() is None and gate.refusal() is None
    gate.kill()
    assert gate.refusal() == "entrée réelle coupée (kill switch)" and gate.authorize() is not None
    tested = RealInputGate({REAL_INPUT_ENV: REAL_INPUT_ENV_VALUE, "PYTEST_CURRENT_TEST": "x"})
    assert tested.authorize() == "entrée réelle interdite pendant les tests"
    with pytest.raises(RealInputBlocked):
        tested.require()


def test_win32_backend_refuses_before_any_windows_call() -> None:
    backend = Win32MouseBackend()                       # la construction n'appelle rien
    with pytest.raises(RealInputBlocked):
        backend.move_to(10, 10)
    with pytest.raises(RealInputBlocked):
        backend.left_click()


def context(clock: Clock, **changes) -> ActionContext:
    values = dict(hwnd=GEO.hwnd, layout_digest=GEO.layout_digest, map_id=123, observation_at=clock.now,
                  safe_for_decision=True)
    values.update(changes)
    return ActionContext(**values)


def check(guard: SafetyGuard, clock: Clock, *, plan_at=None, **changes):
    return guard.check(ACTION, expected_hwnd=GEO.hwnd, expected_layout=GEO.layout_digest, expected_map=123,
                       context=context(clock, **changes), plan_created_at=clock.now if plan_at is None else plan_at)


def test_guard_invariants() -> None:
    clock = Clock()
    guard = SafetyGuard(SafetyLimits(), EmergencyStop(), clock)
    assert "tour non commencé" in check(guard, clock).reasons
    guard.begin_turn()
    assert check(guard, clock).allowed
    cases = {"fenêtre DOFUS différente de celle attendue": dict(hwnd=7),
             "layout changé depuis la traduction": dict(layout_digest="other"),
             "map changée ou inconnue": dict(map_id=124),
             "observation non sûre (safe_for_decision)": dict(safe_for_decision=None),
             "observation trop ancienne": dict(observation_at=clock.now - 5)}
    for reason, change in cases.items():
        verdict = check(guard, clock, **change)
        assert not verdict.allowed and reason in verdict.reasons
    assert "plan expiré" in check(guard, clock, plan_at=clock.now - 6).reasons
    double = guard.check(replace(ACTION, clicks=2), expected_hwnd=GEO.hwnd, expected_layout=GEO.layout_digest,
                         expected_map=123, context=context(clock), plan_created_at=clock.now)
    assert "seul un clic gauche simple est permis" in double.reasons
    guard.stop.trigger("F9")
    assert "arrêt d'urgence : F9" in check(guard, clock).reasons


def test_guard_limits_rate_actions_and_turn_duration() -> None:
    clock = Clock()
    guard = SafetyGuard(SafetyLimits(max_actions_per_turn=3, max_clicks_per_second=2), EmergencyStop(), clock)
    guard.begin_turn()
    guard.record_action()
    guard.record_action()
    assert "cadence maximale de clics atteinte" in check(guard, clock).reasons
    clock.now += 1.0
    assert check(guard, clock).allowed
    guard.record_action()
    clock.now += 1.0
    assert "nombre maximal d'actions du tour atteint" in check(guard, clock).reasons
    guard.begin_turn()
    clock.now += 46
    assert "durée maximale du tour dépassée" in check(guard, clock).reasons


def test_disabled_executor_never_touches_the_backend() -> None:
    backend, probe, journal = RecordingBackend(), Probe(GEO), ActionJournal()
    executor = MouseActionExecutor(backend, window_probe=probe, stop=EmergencyStop(), journal=journal)
    result = executor.send(ACTION, GEO, correlation_id="c1")
    assert not result.sent and result.reasons == ("exécuteur souris désactivé",)
    assert backend.calls == [] and journal.unmatched() == []
    assert [entry["phase"] for entry in journal.entries] == ["BEFORE", "AFTER"]


def test_enabled_executor_with_recording_backend_moves_then_clicks_once() -> None:
    backend = RecordingBackend()
    executor = MouseActionExecutor(backend, window_probe=Probe(GEO), enabled=True, stop=EmergencyStop())
    result = executor.send(ACTION, GEO, correlation_id="c1")
    assert result.sent and backend.calls == [("move_to", *ACTION.screen_point), ("left_click",)]
    assert executor.journal.entries[-1]["sent"] is True


def test_real_backend_is_refused_by_the_gate_even_when_enabled() -> None:
    backend = RecordingBackend(real=True)             # se déclare réel : la porte globale s'applique
    executor = MouseActionExecutor(backend, window_probe=Probe(GEO), enabled=True, stop=EmergencyStop())
    result = executor.send(ACTION, GEO, correlation_id="c1")
    assert not result.sent and "tests" in result.reasons[0] and backend.calls == []


@pytest.mark.parametrize("changes, reason", [
    (dict(hwnd=None), "introuvable"), (dict(foreground_hwnd=99), "premier plan"),
    (dict(client_left=0), "déplacée"), (dict(client_width=640), "redimensionnée"),
])
def test_window_changes_block_the_action(changes, reason) -> None:
    backend = RecordingBackend()
    executor = MouseActionExecutor(backend, window_probe=Probe(GEO, **changes), enabled=True, stop=EmergencyStop())
    result = executor.send(ACTION, GEO, correlation_id="c1")
    assert not result.sent and reason in result.reasons[0] and backend.calls == []


def test_f9_between_move_and_click_cancels_the_click() -> None:
    stop = EmergencyStop()

    class StopOnMove(RecordingBackend):
        def move_to(self, x: int, y: int) -> None:
            super().move_to(x, y)
            stop.trigger("F9")

    backend = StopOnMove()
    executor = MouseActionExecutor(backend, window_probe=Probe(GEO), enabled=True, stop=stop)
    result = executor.send(ACTION, GEO, correlation_id="c1")
    assert not result.sent and "clic annulé après déplacement" in result.reasons[0]
    assert backend.calls == [("move_to", *ACTION.screen_point)]
    assert executor.send(ACTION, GEO, correlation_id="c2").reasons == ("arrêt d'urgence : F9",)
    assert executor.journal.unmatched() == []


def test_point_outside_the_client_and_double_click_are_refused() -> None:
    executor = MouseActionExecutor(RecordingBackend(), window_probe=Probe(GEO), enabled=True, stop=EmergencyStop())
    assert executor.send(replace(ACTION, client_x=5000), GEO, correlation_id="c").reasons == ("point hors du client",)
    assert "clic gauche simple" in executor.send(replace(ACTION, clicks=2), GEO, correlation_id="d").reasons[0]
    assert executor.backend.calls == []


def test_only_the_win32_module_holds_real_input_calls() -> None:
    root = Path(__file__).resolve().parents[1] / "combatbot"
    tokens = ("SendInput", "SetCursorPos", "mouse_event", "keybd_event", "pyautogui.click", "pyautogui.move",
              "pyautogui.press", "pyautogui.drag", "pyautogui.key", "pyautogui.mouse")   # capture : screenshot seul
    holders = sorted(str(path.relative_to(root)) for path in root.rglob("*.py")
                     if any(token in path.read_text(encoding="utf-8") for token in tokens))
    assert holders == ["input/win32_mouse.py"]


def test_rate_limit_reports_the_wait_instead_of_forcing_an_abort() -> None:
    clock = Clock()
    guard = SafetyGuard(SafetyLimits(max_clicks_per_second=2), EmergencyStop(), clock)
    guard.begin_turn()
    assert guard.seconds_until_click_allowed() == 0.0
    guard.record_action()
    clock.now += 0.25
    guard.record_action()
    wait = guard.seconds_until_click_allowed()
    assert 0.75 < wait < 0.76
    clock.now += wait
    assert guard.seconds_until_click_allowed() == 0.0 and check(guard, clock).allowed
