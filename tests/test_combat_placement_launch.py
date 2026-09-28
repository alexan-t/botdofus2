"""FAST-6B / FAST-6C : placement fail-closed et machine de lancement (événements synthétiques, aucun clic)."""
from __future__ import annotations

from combatbot.combat.launch import (
    LaunchConfig, LaunchRequested, LaunchSent, LaunchState, LaunchStateMachine, MapChanged, PhaseObserved,
    PlacementDone, Stop, TargetConfirmed, TargetLost,
)
from combatbot.combat.placement import (
    CellSource, PlacementGoal, PlacementPolicy, PlacementState, PlacementStatus, decide_placement,
)
from tests.test_combat_pathfinding import make_map
from tests.test_combat_targeting import offset

MAP = make_map()
PLAYER = 300
ENEMY = offset(PLAYER, 6, 0)
NEAR, FAR = offset(PLAYER, 3, 0), offset(PLAYER, -3, 0)


def placement(**changes) -> PlacementState:
    values = dict(map_id=123, phase="PLACEMENT", available_start_cells=frozenset({PLAYER, NEAR, FAR}),
                  source=CellSource.OBSERVED_ACTIVE, player_cell_id=PLAYER, enemy_start_cells=frozenset({ENEMY}))
    values.update(changes)
    return PlacementState(**values)


def test_deterministic_choice_close_or_far() -> None:
    close = decide_placement(placement(), MAP)
    assert close.status is PlacementStatus.MOVE and close.target_cell_id == NEAR
    assert close.scores == {PLAYER: 6, NEAR: 3, FAR: 9}
    far = decide_placement(placement(), MAP, PlacementPolicy(PlacementGoal.FAR_FROM_ENEMIES))
    assert far.target_cell_id == FAR
    assert decide_placement(placement(), MAP) == close
    assert close.to_dict()["actions_sent"] == "NONE"


def test_stays_when_the_current_cell_is_already_best_and_breaks_ties_by_cell_id() -> None:
    stay = decide_placement(placement(player_cell_id=NEAR, available_start_cells=frozenset({NEAR, FAR})), MAP)
    assert stay.status is PlacementStatus.STAY and stay.target_cell_id == NEAR
    twins = frozenset({offset(ENEMY, 0, 2), offset(ENEMY, 0, -2)})
    tie = decide_placement(placement(available_start_cells=twins), MAP)
    assert tie.target_cell_id == min(twins)


def test_gamedata_hints_are_never_accepted_as_active_cells() -> None:
    hinted = PlacementState.from_gamedata_hints(123, "PLACEMENT", {NEAR, FAR}, PLAYER, {ENEMY})
    decision = decide_placement(hinted, MAP)
    assert decision.status is PlacementStatus.BLOCKED and decision.target_cell_id is None
    assert "non démontrés" in decision.reasons[0]


def test_unknown_inputs_block() -> None:
    cases = {"pas une phase de placement": dict(phase="FIGHTING"),
             "non observables avec certitude": dict(available_start_cells=None),
             "case du joueur inconnue": dict(player_cell_id=None),
             "cases de départ ennemies inconnues": dict(enemy_start_cells=None),
             "aucun ennemi observé": dict(enemy_start_cells=frozenset()),
             "topologie de la map indisponible": dict(map_id=999)}
    for reason, change in cases.items():
        decision = decide_placement(placement(**change), MAP)
        assert decision.status is PlacementStatus.BLOCKED and any(reason in item for item in decision.reasons)
    assert decide_placement(placement(), None).status is PlacementStatus.BLOCKED
    assert decide_placement(placement(source=CellSource.UNKNOWN), MAP).status is PlacementStatus.BLOCKED


def test_unreachable_enemy_gives_no_score() -> None:
    walled = make_map(blocked={ENEMY})
    decision = decide_placement(placement(), walled)
    assert decision.status is PlacementStatus.BLOCKED and "distance inconnue" in decision.reasons[0]


def to_fighting(machine: LaunchStateMachine) -> None:
    machine.handle(TargetConfirmed(0, "G1", 123, 0.95))
    machine.handle(LaunchRequested(1))
    machine.handle(LaunchSent(1.5))
    machine.handle(PhaseObserved(2, "PLACEMENT"))
    machine.handle(PlacementDone(10))
    machine.handle(PhaseObserved(12, "FIGHTING"))


def test_launch_happy_path_with_synthetic_events() -> None:
    machine = LaunchStateMachine()
    to_fighting(machine)
    assert machine.state is LaunchState.FIGHTING
    assert [after for _t, _before, after, _reason in machine.history] == [
        "TARGET_CONFIRMED", "LAUNCH_REQUESTED", "WAIT_PLACEMENT", "PLACEMENT", "WAIT_FIGHT", "FIGHTING"]
    assert machine.refusals == []


def test_unproven_target_and_out_of_order_events_are_refused() -> None:
    machine = LaunchStateMachine()
    assert machine.handle(TargetConfirmed(0, "G1", None, 0.99)) is LaunchState.EXPLORATION
    assert machine.handle(TargetConfirmed(0, "G1", 123, 0.5)) is LaunchState.EXPLORATION
    assert machine.handle(LaunchRequested(1)) is LaunchState.EXPLORATION
    assert machine.handle(LaunchSent(1)) is LaunchState.EXPLORATION
    assert machine.handle(PlacementDone(1)) is LaunchState.EXPLORATION
    assert len(machine.refusals) == 5 and machine.history == []


def test_target_lost_expired_or_map_changed_return_to_exploration() -> None:
    machine = LaunchStateMachine()
    machine.handle(TargetConfirmed(0, "G1", 123, 0.95))
    assert machine.handle(TargetLost(1)) is LaunchState.EXPLORATION and machine.target is None
    machine.handle(TargetConfirmed(2, "G1", 123, 0.95))
    assert machine.handle(LaunchRequested(20)) is LaunchState.EXPLORATION          # cible trop ancienne
    machine.handle(TargetConfirmed(30, "G1", 123, 0.95))
    assert machine.tick(40) is LaunchState.EXPLORATION
    machine.handle(TargetConfirmed(50, "G1", 123, 0.95))
    machine.handle(LaunchRequested(51))
    assert machine.handle(MapChanged(52, 7)) is LaunchState.EXPLORATION


def test_timeouts_incoherent_phases_and_stop_fail() -> None:
    machine = LaunchStateMachine(LaunchConfig(wait_placement_s=10))
    machine.handle(TargetConfirmed(0, "G1", 123, 0.95))
    machine.handle(LaunchRequested(1))
    machine.handle(LaunchSent(2))
    assert machine.handle(PhaseObserved(3, None)) is LaunchState.WAIT_PLACEMENT      # UNKNOWN : attendre
    assert machine.handle(PhaseObserved(4, "EXPLORATION")) is LaunchState.WAIT_PLACEMENT
    assert machine.tick(13) is LaunchState.FAILED
    assert machine.handle(TargetConfirmed(14, "G2", 123, 0.99)) is LaunchState.FAILED   # terminal
    assert machine.reset(15) is LaunchState.EXPLORATION

    machine = LaunchStateMachine()
    machine.handle(TargetConfirmed(0, "G1", 123, 0.95))
    machine.handle(LaunchRequested(1))
    machine.handle(LaunchSent(2))
    assert machine.handle(PhaseObserved(3, "RESULTS")) is LaunchState.FAILED

    machine = LaunchStateMachine()
    machine.handle(TargetConfirmed(0, "G1", 123, 0.95))
    machine.handle(LaunchRequested(1))
    machine.handle(LaunchSent(2))
    machine.handle(PhaseObserved(3, "PLACEMENT"))
    assert machine.handle(MapChanged(4, 8)) is LaunchState.FAILED

    machine = LaunchStateMachine()
    machine.handle(TargetConfirmed(0, "G1", 123, 0.95))
    assert machine.handle(Stop(1, "F9")) is LaunchState.FAILED and machine.history[-1][3] == "F9"


def test_fight_can_start_without_an_observed_placement() -> None:
    machine = LaunchStateMachine()
    machine.handle(TargetConfirmed(0, "G1", 123, 0.95))
    machine.handle(LaunchRequested(1))
    machine.handle(LaunchSent(2))
    assert machine.handle(PhaseObserved(3, "FIGHTING")) is LaunchState.FIGHTING
