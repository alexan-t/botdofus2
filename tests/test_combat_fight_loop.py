"""FAST-6A : boucle multi-tours offline (ScriptedObserver + FakeSender). ACTIONS RÉELLES : NONE."""
from __future__ import annotations

from combatbot.combat.fight_loop import FightConfig, FightLoop, FightOutcome
from combatbot.combat.scripted import FakeSender, ScriptedFrame
from tests.test_combat_planner import state
from tests.test_combat_turn_runner import GEO, runner

OTHER = state(turn="OTHER")
MY_TURN_LOW_AP = state(ap=2)          # le plan est un simple END_TURN


def fight(script, *, config=FightConfig(wait_turn_s=60, unknown_grace_s=3), sender=None, geometry=GEO):
    turn, sender = runner(script, sender=sender)
    loop = FightLoop(turn.observer, turn, lambda: geometry, config=config, clock=turn.clock, sleep=turn.sleep)
    return loop, sender


def test_several_turns_until_results() -> None:
    loop, sender = fight([OTHER, OTHER, MY_TURN_LOW_AP, OTHER, OTHER, OTHER,
                          state(), state(ap=3), state(ap=0), OTHER,
                          OTHER, state(phase="RESULTS", turn=None)])
    report = loop.run()
    assert report.outcome is FightOutcome.RESULT and len(report.turns) == 2
    assert [target for _cid, target in sender.sent].count("end_turn") == 2 and report.actions_sent == 6
    phases = [phase for phase, _at, _detail in report.phases]
    assert phases == ["WAIT_PLAYER_TURN", "RUN_SINGLE_TURN", "WAIT_OTHER_TURN",
                      "WAIT_PLAYER_TURN", "RUN_SINGLE_TURN", "WAIT_OTHER_TURN", "WAIT_PLAYER_TURN", "RESULT"]


def test_never_plays_during_the_other_turn() -> None:
    loop, sender = fight([OTHER] * 30)
    loop.config = FightConfig(wait_turn_s=5, unknown_grace_s=3)
    report = loop.run()
    assert report.outcome is FightOutcome.ABORTED and "pas revenu" in report.reason and sender.attempts == 0


def test_unknown_is_tolerated_for_a_bounded_time() -> None:
    unknown = state(turn=None, safe_for_decision=False)
    loop, sender = fight([None, unknown, None, MY_TURN_LOW_AP, OTHER, state(phase="RESULTS", turn=None)])
    assert loop.run().outcome is FightOutcome.RESULT and sender.attempts == 1
    loop, sender = fight([unknown] * 200)
    report = loop.run()
    assert report.outcome is FightOutcome.ABORTED and "état inconnu" in report.reason and sender.attempts == 0


def test_critical_map_or_window_loss_aborts() -> None:
    for frame, reason in ((state(turn="OTHER", map_id=999), "map"),
                          (ScriptedFrame(OTHER, hwnd=5), "fenêtre")):
        loop, sender = fight([OTHER, frame, MY_TURN_LOW_AP])
        report = loop.run()
        assert report.outcome is FightOutcome.ABORTED and reason in report.reason and sender.attempts == 0


def test_emergency_stop_is_permanent() -> None:
    loop, sender = fight([OTHER, MY_TURN_LOW_AP, OTHER, MY_TURN_LOW_AP, OTHER])
    loop.stop.trigger("F9")
    report = loop.run()
    assert report.outcome is FightOutcome.ABORTED and "F9" in report.reason and sender.attempts == 0
    assert loop.run().outcome is FightOutcome.ABORTED and sender.attempts == 0      # aucune reprise seule


def test_turn_failure_and_turn_cap_abort() -> None:
    loop, sender = fight([MY_TURN_LOW_AP], sender=FakeSender(fail_on=frozenset({0})))
    report = loop.run()
    assert report.outcome is FightOutcome.ABORTED and "FAILED" in report.reason
    loop, sender = fight([MY_TURN_LOW_AP, OTHER, MY_TURN_LOW_AP, OTHER],
                         config=FightConfig(max_turns=1, wait_turn_s=60, unknown_grace_s=3))
    report = loop.run()
    assert report.outcome is FightOutcome.ABORTED and "limite de sécurité" in report.reason and len(report.turns) == 1


def test_missing_geometry_aborts_and_journal_is_complete() -> None:
    loop, sender = fight([MY_TURN_LOW_AP], geometry=None)
    assert loop.run().reason == "géométrie écran inconnue" and sender.attempts == 0
    loop, _sender = fight([MY_TURN_LOW_AP, OTHER, state(phase="RESULTS", turn=None)])
    report = loop.run()
    journal = loop.turn_runner.coordinator.journal
    assert journal.unmatched() == [] and report.to_dict()["actions_sent"] == 1
    assert [entry["correlation_id"] for entry in journal.entries if entry["phase"] == "FIGHT"][-1] == "RESULT"
