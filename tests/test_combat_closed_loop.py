"""FAST-5B : coordinateur en boucle fermée (ScriptedObserver + FakeSender, aucune entrée réelle)."""
from __future__ import annotations

from dataclasses import replace

from combatbot.combat.closed_loop import ClosedLoopCoordinator, LoopConfig, LoopState, Observation
from combatbot.combat.planner import PlanStatus, StepKind, plan_turn
from combatbot.combat.safety import EmergencyStop, SafetyGuard, SafetyLimits
from combatbot.combat.scripted import FakeClock, FakeSender, ScriptedFrame, ScriptedObserver
from combatbot.combat.state import EnemyState
from tests.test_combat_planner import HYPOTHESIS, MAP, PLAYER, state
from tests.test_combat_screen_actions import SPELL, SPELLS, geometry
from tests.test_combat_targeting import offset

GEO = geometry()
ENEMY = offset(PLAYER, 2, 0)


def rig(script=(), *, sender=None, config=LoopConfig(), limits=SafetyLimits()):
    clock = FakeClock()
    stop = EmergencyStop()
    guard = SafetyGuard(limits, stop, clock)
    guard.begin_turn()
    observer = ScriptedObserver(script, clock=clock, hwnd=GEO.hwnd, layout_digest=GEO.layout_digest)
    sender = sender or FakeSender()
    loop = ClosedLoopCoordinator(observer, sender, guard=guard, spells=SPELLS, config=config, clock=clock,
                                 sleep=clock.sleep)
    return loop, observer, sender, clock


def before(clock: FakeClock, **changes) -> Observation:
    return Observation(state(**changes), clock(), 0, GEO.hwnd, GEO.layout_digest)


def plan(**changes):
    result = plan_turn(state(**changes), MAP, [SPELL], rules=HYPOTHESIS)
    assert result.status is PlanStatus.READY
    return result


def run(loop, clock, current=None, **changes):
    return loop.run_plan(plan(**changes), GEO, current or before(clock, **changes), clock())


def test_full_plan_finishes_only_with_proof_of_each_step() -> None:
    loop, _observer, sender, clock = rig([state(ap=3), state(ap=0), state(turn="OTHER", ap=6)])
    record = run(loop, clock)
    assert record.state is LoopState.FINISHED
    assert [step.state for step in record.steps] == [LoopState.SUCCEEDED] * 3
    assert [target for _cid, target in sender.sent] == ["spell_slot:1/3", f"cell:{ENEMY}", "spell_slot:1/3",
                                                        f"cell:{ENEMY}", "end_turn"]
    assert [step.correlation_id for step in record.steps] == [f"{record.plan_id}:{i}" for i in range(3)]
    states = [state for state, _at, _detail in record.steps[0].transitions]
    assert states == ["PRECHECK", "READY_TO_SEND", "WAITING_EFFECT", "SUCCEEDED"]
    assert loop.journal.unmatched() == []


def test_pending_effect_waits_for_a_new_frame() -> None:
    loop, observer, sender, clock = rig([None, state(ap=6), None, state(ap=3)])
    step = plan().steps[0]
    record = loop.run_step(step, plan_id="p", index=0, geometry=GEO, before=before(clock), plan_created_at=clock())
    assert record.state is LoopState.SUCCEEDED and observer.calls == 4 and len(sender.sent) == 2


def test_contradiction_fails_and_never_sends_the_next_step() -> None:
    loop, _observer, sender, clock = rig([state(ap=2)])
    record = run(loop, clock)
    assert record.state is LoopState.FAILED and len(record.steps) == 1 and len(sender.sent) == 2
    assert "PA différents" in record.reason


def test_no_valid_observation_times_out() -> None:
    loop, _observer, sender, clock = rig([], config=LoopConfig(effect_timeout_s=2.0))
    record = run(loop, clock)
    assert record.state is LoopState.TIMED_OUT and len(sender.sent) == 2
    assert record.steps[-1].transitions[-1][1] - record.steps[-1].transitions[-2][1] > 2.0


def test_unknown_aborts_immediately_or_after_a_bounded_grace() -> None:
    loop, *_rest, clock = rig([state(ap=None)])
    assert run(loop, clock).steps[0].state is LoopState.ABORTED
    loop, *_rest, clock = rig([state(safe_for_decision=False, ap=3)])
    record = run(loop, clock)
    assert record.state is LoopState.ABORTED and "observation non sûre" in record.reason
    loop, *_rest, clock = rig([state(ap=None), state(ap=3)], config=LoopConfig(unknown_grace_s=1.0))
    step = plan().steps[0]
    assert loop.run_step(step, plan_id="p", index=0, geometry=GEO, before=before(clock),
                         plan_created_at=clock()).state is LoopState.SUCCEEDED


def test_window_layout_or_map_change_aborts() -> None:
    for frame, reason in ((ScriptedFrame(state(ap=3), hwnd=7), "fenêtre"),
                          (ScriptedFrame(state(ap=3), layout_digest="x"), "layout"),
                          (state(ap=3, map_id=124), "map")):
        loop, *_rest, clock = rig([frame])
        record = run(loop, clock)
        assert record.state is LoopState.ABORTED and reason in record.reason


def test_turn_lost_or_combat_over_during_a_step_aborts() -> None:
    loop, *_rest, clock = rig([state(turn="OTHER", ap=3)])
    assert "tour perdu" in run(loop, clock).reason
    loop, *_rest, clock = rig([state(phase="RESULTS", turn=None)])
    assert "phase RESULTS" in run(loop, clock).reason


def test_preconditions_are_checked_before_any_send() -> None:
    step = plan().steps[0]
    cases = {"cible E1 absente ou déplacée": dict(enemies=(EnemyState("E1", offset(PLAYER, 3, 0)),)),
             "PA insuffisants": dict(ap=2),
             "pas mon tour": dict(turn="OTHER"),
             "observation non sûre": dict(safe_for_decision=False),
             "map changée": dict(map_id=999)}
    for reason, change in cases.items():
        loop, _observer, sender, clock = rig()
        record = loop.run_step(step, plan_id="p", index=0, geometry=GEO, before=before(clock, **change),
                               plan_created_at=clock())
        assert record.state is LoopState.ABORTED and reason in record.reason and sender.attempts == 0
    loop, _observer, sender, clock = rig()
    loop.guard.stop.trigger("F9")
    record = loop.run_step(step, plan_id="p", index=0, geometry=GEO, before=before(clock), plan_created_at=clock())
    assert record.state is LoopState.ABORTED and "F9" in record.reason and sender.attempts == 0


def test_executor_failure_or_exception_fails_the_step() -> None:
    loop, _observer, sender, clock = rig(sender=FakeSender(fail_on=frozenset({0})))
    record = run(loop, clock)
    assert record.state is LoopState.FAILED and "échec simulé" in record.reason and sender.sent == []

    def explode(_index, _action):
        raise OSError("SendInput")

    loop, _observer, sender, clock = rig(sender=FakeSender(on_send=explode))
    record = run(loop, clock)
    assert record.state is LoopState.FAILED and "exécuteur en erreur" in record.reason
    assert loop.journal.unmatched() == []


def test_f9_between_the_spell_click_and_the_target_click() -> None:
    holder = {}
    sender = FakeSender(on_send=lambda index, _action: holder["stop"].trigger("F9") if index == 0 else None)
    loop, _observer, sender, clock = rig(sender=sender)
    holder["stop"] = loop.guard.stop
    record = run(loop, clock)
    assert record.state is LoopState.ABORTED and "arrêt d'urgence" in record.reason
    assert [target for _cid, target in sender.sent] == ["spell_slot:1/3"]


def test_move_and_end_turn_use_their_own_invariants() -> None:
    far = offset(PLAYER, 4, 0)
    moved = plan(enemies=(EnemyState("E1", far),))
    move = moved.steps[0]
    assert move.kind is StepKind.MOVE
    loop, *_rest, clock = rig([state(player_cell_id=move.expected.player_cell_id, mp=None,
                                     enemies=(EnemyState("E1", far),))])
    record = loop.run_step(move, plan_id="p", index=0, geometry=GEO,
                           before=before(clock, enemies=(EnemyState("E1", far),)), plan_created_at=clock())
    assert record.state is LoopState.SUCCEEDED
    end = replace(plan().steps[-1])
    assert end.kind is StepKind.END_TURN
    loop, *_rest, clock = rig([state(turn=None, ap=None, safe_for_decision=False), state(turn="OTHER", ap=None)])
    record = loop.run_step(end, plan_id="p", index=2, geometry=GEO, before=before(clock), plan_created_at=clock())
    assert record.state is LoopState.SUCCEEDED and "OTHER" in record.reason


def test_expired_plan_or_action_limit_is_refused_by_the_guard() -> None:
    loop, _observer, sender, clock = rig()
    step = plan().steps[0]
    record = loop.run_step(step, plan_id="p", index=0, geometry=GEO, before=before(clock),
                           plan_created_at=clock() - 10)
    assert record.state is LoopState.ABORTED and "plan expiré" in record.reason and sender.attempts == 0
    loop, _observer, sender, clock = rig(limits=SafetyLimits(max_actions_per_turn=1))
    record = loop.run_step(step, plan_id="p", index=0, geometry=GEO, before=before(clock), plan_created_at=clock())
    assert record.state is LoopState.ABORTED and "nombre maximal" in record.reason and len(sender.sent) == 1


def test_blocked_plan_is_never_started() -> None:
    loop, _observer, sender, clock = rig()
    blocked = plan_turn(state(ap=None), MAP, [SPELL], rules=HYPOTHESIS)
    record = loop.run_plan(blocked, GEO, before(clock), clock())
    assert record.state is LoopState.ABORTED and "PA inconnus" in record.reason and sender.attempts == 0
