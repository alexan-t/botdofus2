"""FAST-5C : un tour complet en simulation d'observations. ACTIONS RÉELLES : NONE."""
from __future__ import annotations

from dataclasses import replace

from combatbot.combat.closed_loop import ClosedLoopCoordinator, LoopConfig
from combatbot.combat.safety import EmergencyStop, SafetyGuard, SafetyLimits
from combatbot.combat.scripted import FakeClock, FakeSender, ScriptedObserver
from combatbot.combat.state import EnemyState
from combatbot.combat.targeting import CONSERVATIVE_RULES
from combatbot.combat.turn_runner import SingleTurnRunner, TurnConfig, TurnOutcome, make_planner
from tests.test_combat_planner import HYPOTHESIS, MAP, PLAYER, state
from tests.test_combat_screen_actions import SPELL, geometry
from tests.test_combat_targeting import offset

GEO = geometry()
FAR = offset(PLAYER, 4, 0)


def runner(script, *, spell=SPELL, sender=None, rules=HYPOTHESIS, config=TurnConfig(wait_player_turn_s=5)):
    clock = FakeClock()
    guard = SafetyGuard(SafetyLimits(), EmergencyStop(), clock)
    observer = ScriptedObserver(script, clock=clock, hwnd=GEO.hwnd, layout_digest=GEO.layout_digest)
    sender = sender or FakeSender()
    loop = ClosedLoopCoordinator(observer, sender, guard=guard, spells={spell.key: spell},
                                 config=LoopConfig(effect_timeout_s=2.0), clock=clock, sleep=clock.sleep)
    planner = make_planner(lambda map_id: MAP if map_id == 123 else None, [spell], rules=rules)
    return SingleTurnRunner(observer, planner, loop, config=config, clock=clock, sleep=clock.sleep), sender


def targets(sender: FakeSender) -> list[str]:
    return [target for _cid, target in sender.sent]


def test_spell_already_in_range_two_casts_then_end_turn() -> None:
    turn, sender = runner([state(), state(ap=3), state(ap=0), state(turn="OTHER", ap=6)])
    report = turn.run(GEO)
    assert report.outcome is TurnOutcome.COMPLETE and report.actions_sent == 5
    assert report.plans == [["CAST profile:1 ON E1", "CAST profile:1 ON E1", "END_TURN"],
                            ["CAST profile:1 ON E1", "END_TURN"], ["END_TURN"]]
    assert targets(sender)[-1] == "end_turn"
    phases = [phase for phase, _at, _detail in report.phases]
    assert phases[0] == "WAIT_PLAYER_TURN" and phases[-2:] == ["VERIFY_OTHER_TURN", "COMPLETE"]


def test_move_then_cast_then_end_turn() -> None:
    spell = replace(SPELL, per_turn=1)
    enemies = (EnemyState("E1", FAR),)
    first = state(enemies=enemies)
    turn, sender = runner([first], spell=spell)
    plan = turn.planner(first)
    destination = plan.steps[0].expected.player_cell_id
    turn.observer.push(state(enemies=enemies, player_cell_id=destination, mp=1),
                       state(enemies=enemies, player_cell_id=destination, mp=1, ap=3),
                       state(enemies=enemies, player_cell_id=destination, turn="OTHER"))
    report = turn.run(GEO)
    assert report.outcome is TurnOutcome.COMPLETE
    assert targets(sender) == [f"cell:{destination}", "spell_slot:1/3", f"cell:{FAR}", "end_turn"]


def test_not_enough_ap_only_ends_the_turn() -> None:
    turn, sender = runner([state(ap=2), state(turn="OTHER")])
    report = turn.run(GEO)
    assert report.outcome is TurnOutcome.COMPLETE and targets(sender) == ["end_turn"]


def test_enemy_vanishing_between_two_steps_aborts() -> None:
    turn, sender = runner([state(), state(ap=3, enemies=())])
    report = turn.run(GEO)
    assert report.outcome is TurnOutcome.ABORTED and "mort non prouvée" in report.reason
    assert report.actions_sent == 2


def test_map_change_grid_unknown_player_lost_or_turn_lost_abort() -> None:
    for frame, reason in ((state(ap=3, map_id=124), "map"),
                          (state(ap=3, safe_for_decision=False), "UNKNOWN"),
                          (state(ap=3, player_cell_id=None, safe_for_decision=False), "UNKNOWN"),
                          (state(ap=3, turn="OTHER"), "tour perdu")):
        turn, sender = runner([state(), frame])
        report = turn.run(GEO)
        assert report.outcome is TurnOutcome.ABORTED and reason in report.reason, (frame, report.reason)
        assert report.actions_sent == 2


def test_timeout_and_executor_failure() -> None:
    turn, _sender = runner([state()])
    report = turn.run(GEO)
    assert report.outcome is TurnOutcome.TIMED_OUT and report.actions_sent == 2
    turn, sender = runner([state()], sender=FakeSender(fail_on=frozenset({1})))
    report = turn.run(GEO)
    assert report.outcome is TurnOutcome.FAILED and targets(sender) == ["spell_slot:1/3"]


def test_blocked_plan_sends_nothing() -> None:
    turn, sender = runner([state()], rules=CONSERVATIVE_RULES)
    report = turn.run(GEO)
    assert report.outcome is TurnOutcome.BLOCKED and "non prouvé" in report.reason and sender.attempts == 0


def test_emergency_stop_between_two_actions() -> None:
    holder = {}
    sender = FakeSender(on_send=lambda index, _a: holder["turn"].stop.trigger("F9") if index == 1 else None)
    turn, sender = runner([state(), state(ap=3), state(ap=0)], sender=sender)
    holder["turn"] = turn
    report = turn.run(GEO)
    assert report.outcome is TurnOutcome.ABORTED and "F9" in report.reason and report.actions_sent == 2


def test_waiting_for_the_player_turn() -> None:
    turn, sender = runner([None, state(turn="OTHER"), state(turn=None, safe_for_decision=False), state(ap=2),
                           state(turn="OTHER")])
    assert turn.run(GEO).outcome is TurnOutcome.COMPLETE and targets(sender) == ["end_turn"]
    turn, sender = runner([state(turn="OTHER")] * 100, config=TurnConfig(wait_player_turn_s=2))
    report = turn.run(GEO)
    assert report.outcome is TurnOutcome.TIMED_OUT and sender.attempts == 0
    turn, sender = runner([state(phase="RESULTS", turn=None)])
    assert turn.run(GEO).outcome is TurnOutcome.COMBAT_ENDED and sender.attempts == 0


def test_journal_is_complete_and_balanced() -> None:
    turn, _sender = runner([state(), state(ap=3), state(ap=0), state(turn="OTHER")])
    report = turn.run(GEO)
    journal = turn.coordinator.journal
    assert journal.unmatched() == [] and journal.entries[-1]["outcome"] == "COMPLETE"
    assert sum(entry["phase"] == "BEFORE" for entry in journal.entries) == report.actions_sent
    assert report.to_dict()["actions_sent"] == 5
