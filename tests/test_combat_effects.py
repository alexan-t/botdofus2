"""FAST-5B : invariants d'effet propres à MOVE / CAST / END_TURN."""
from __future__ import annotations

from dataclasses import replace

from combatbot.combat.effects import Effect, check_effect
from combatbot.combat.planner import ExpectedState, PlanStep, StepKind
from tests.test_combat_planner import PLAYER, state

MOVE = PlanStep(StepKind.MOVE, "t", (), 0, 2, ExpectedState(PLAYER + 1, 6, 1), path=(PLAYER + 1,),
                origin_cell_id=PLAYER)
CAST = PlanStep(StepKind.CAST, "t", (), 3, 0, ExpectedState(PLAYER, 3, 3), spell_key="s", target_id="E1",
                target_cell_id=PLAYER + 2)
END = PlanStep(StepKind.END_TURN, "t", (), 0, 0, ExpectedState(PLAYER, 3, 3))


def test_move_requires_the_cell_and_checks_mp_only_when_readable() -> None:
    assert check_effect(MOVE, state(player_cell_id=PLAYER + 1, mp=1)).effect is Effect.CONFIRMED
    assert check_effect(MOVE, state(player_cell_id=PLAYER + 1, mp=None)).effect is Effect.CONFIRMED
    assert check_effect(MOVE, state(player_cell_id=PLAYER + 1, mp=3)).effect is Effect.CONTRADICTED
    assert check_effect(MOVE, state(player_cell_id=PLAYER)).effect is Effect.PENDING
    wrong = check_effect(MOVE, state(player_cell_id=PLAYER + 5))
    assert wrong.effect is Effect.CONTRADICTED and "player_cell_id" in wrong.mismatches
    assert check_effect(MOVE, state(player_cell_id=None)).effect is Effect.UNKNOWN
    assert check_effect(MOVE, state(player_cell_id=PLAYER + 1, mp=1, ap=0)).effect is Effect.CONFIRMED   # PA non exigés


def test_cast_requires_the_exact_ap_drop_but_never_the_target_death() -> None:
    assert check_effect(CAST, state(ap=3)).effect is Effect.CONFIRMED
    assert check_effect(CAST, state(ap=3, enemies=())).effect is Effect.CONFIRMED
    assert check_effect(CAST, state(ap=6)).effect is Effect.PENDING
    assert check_effect(CAST, state(ap=2)).effect is Effect.CONTRADICTED
    assert check_effect(CAST, state(ap=None)).effect is Effect.UNKNOWN
    assert check_effect(CAST, state(ap=3, player_cell_id=PLAYER + 1)).effect is Effect.CONTRADICTED
    assert check_effect(replace(CAST, ap_cost=0), state(ap=3)).effect is Effect.UNKNOWN


def test_end_turn_only_requires_leaving_the_player_turn() -> None:
    assert check_effect(END, state(turn="OTHER", ap=0, mp=0)).effect is Effect.CONFIRMED
    assert check_effect(END, state(turn="PLAYER")).effect is Effect.PENDING
    assert check_effect(END, state(turn=None)).effect is Effect.PENDING
    assert check_effect(END, state(phase="RESULTS", turn=None)).effect is Effect.CONFIRMED
