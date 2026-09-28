"""FAST-4D : planificateur dry-run sur l'état réel (aucune action envoyée)."""
from __future__ import annotations

from dataclasses import replace

from combatbot.combat.planner import PlannerPolicy, PlanStatus, StepKind, plan_turn
from combatbot.combat.spells import CombatSpell, SpellProvenance, SpellStrategy, SpellTarget, SpellTiming
from combatbot.combat.state import EnemyState, RealCombatState
from combatbot.combat.targeting import RangeMetric, TargetingRules
from combatbot.gamedata.topology import neighbors
from tests.test_combat_pathfinding import make_map
from tests.test_combat_targeting import offset

MAP = make_map()
HYPOTHESIS = TargetingRules(range_metric=RangeMetric.LOGICAL_MANHATTAN)
PLAYER = 300


def spell(key: str = "profile:1", **changes) -> CombatSpell:
    values = dict(key=key, name="Flèche", ap_cost=3, min_range=1, max_range=2, modifiable_range=False,
                  line_cast=False, line_of_sight=False, per_turn=2, per_target=2,
                  provenance=SpellProvenance.HUMAN_CONFIRMED)
    values.update(changes)
    return CombatSpell(**values)


def state(**changes) -> RealCombatState:
    values = dict(map_id=123, player_cell_id=PLAYER, ap=6, mp=3,
                  enemies=(EnemyState("E1", offset(PLAYER, 2, 0)),), phase="FIGHTING", turn="PLAYER",
                  safe_for_decision=True, turn_number=2)
    values.update(changes)
    return RealCombatState(**values)


def test_enemy_already_in_range_casts_until_limits_then_ends_turn() -> None:
    plan = plan_turn(state(), MAP, [spell()], rules=HYPOTHESIS)
    assert plan.status is PlanStatus.READY
    assert plan.describe() == ["CAST profile:1 ON E1", "CAST profile:1 ON E1", "END_TURN"]
    first, second, end = plan.steps
    assert (first.ap_cost, first.expected.ap, second.expected.ap) == (3, 3, 0)
    assert any("PA ≥ 3" in item for item in first.preconditions)
    assert plan.assumptions and "non prouvée" in plan.assumptions[0]
    assert plan.to_dict()["actions_sent"] == "NONE"


def test_move_needed_before_casting() -> None:
    far = offset(PLAYER, 4, 0)
    plan = plan_turn(state(enemies=(EnemyState("E1", far),)), MAP, [spell(per_turn=1)], rules=HYPOTHESIS)
    assert [step.kind for step in plan.steps] == [StepKind.MOVE, StepKind.CAST, StepKind.END_TURN]
    move = plan.steps[0]
    assert move.origin_cell_id == PLAYER and move.mp_cost == 2 and move.expected.mp == 1
    assert plan.describe()[0] == f"MOVE {PLAYER} -> {move.expected.player_cell_id}"
    cells = (PLAYER, *move.path)
    assert all(b in neighbors(a) for a, b in zip(cells, cells[1:]))


def test_no_path_or_not_enough_ap_ends_turn() -> None:
    far = offset(PLAYER, 6, 0)
    walled = make_map(blocked=set(neighbors(PLAYER)))
    plan = plan_turn(state(enemies=(EnemyState("E1", far),)), walled, [spell()], rules=HYPOTHESIS)
    assert plan.describe() == ["END_TURN"]
    poor = plan_turn(state(ap=2), MAP, [spell()], rules=HYPOTHESIS)
    assert poor.describe() == ["END_TURN"]


def test_unknown_essentials_block_the_plan() -> None:
    cases = {
        "PA inconnus": state(ap=None), "PM inconnus": state(mp=None),
        "cellule du joueur inconnue": state(player_cell_id=None),
        "ennemi E1 non localisé": state(enemies=(EnemyState("E1", None),)),
        "pas mon tour": state(turn="OTHER"), "safe_for_decision": state(safe_for_decision=None),
        "map inconnue": state(map_id=None),
    }
    for expected, case in cases.items():
        plan = plan_turn(case, MAP, [spell()], rules=HYPOTHESIS)
        assert plan.status is PlanStatus.BLOCKED and any(expected in reason for reason in plan.blocked_reasons), expected
        assert plan.steps == ()
    occluded = plan_turn(state(enemies=(EnemyState("E1", 310, observed=False),)), MAP, [spell()], rules=HYPOTHESIS)
    assert occluded.status is PlanStatus.BLOCKED
    assert plan_turn(state(map_id=999), MAP, [spell()], rules=HYPOTHESIS).status is PlanStatus.BLOCKED
    assert plan_turn(state(), None, [spell()], rules=HYPOTHESIS).status is PlanStatus.BLOCKED


def test_unknown_cell_on_the_only_useful_path_blocks() -> None:
    far = offset(PLAYER, 4, 0)
    east = offset(PLAYER, 1, 0)
    ring = set(neighbors(PLAYER)) - {east}
    combat_map = make_map(blocked=ring)
    plan = plan_turn(state(enemies=(EnemyState("E1", far),), unknown_cells=frozenset({east})), combat_map,
                     [spell()], rules=HYPOTHESIS)
    assert plan.status is PlanStatus.BLOCKED and "occupation inconnue" in plan.blocked_reasons[0]
    # Même situation sans cellule inconnue : chemin libre, le plan se déplace.
    assert plan_turn(state(enemies=(EnemyState("E1", far),)), combat_map, [spell()], rules=HYPOTHESIS).steps[0].kind \
        is StepKind.MOVE


def test_unknown_line_of_sight_or_range_blocks() -> None:
    los = plan_turn(state(), MAP, [spell(line_of_sight=True)], rules=HYPOTHESIS)
    assert los.status is PlanStatus.BLOCKED and "LOS_ALGORITHM_UNVERIFIED" in los.blocked_reasons[0]
    default_rules = plan_turn(state(), MAP, [spell()])
    assert default_rules.status is PlanStatus.BLOCKED and "RANGE_RULE_UNVERIFIED" in default_rules.blocked_reasons[0]
    bonus = plan_turn(state(), MAP, [spell(modifiable_range=True)], rules=HYPOTHESIS)
    assert bonus.status is PlanStatus.BLOCKED and "RANGE_BONUS_UNKNOWN" in bonus.blocked_reasons[0]
    known_bonus = plan_turn(state(range_bonus=0), MAP, [spell(modifiable_range=True)], rules=HYPOTHESIS)
    assert known_bonus.status is PlanStatus.READY


def test_several_enemies_and_spell_priorities() -> None:
    enemies = (EnemyState("E2", offset(PLAYER, 0, 2)), EnemyState("E1", offset(PLAYER, 2, 0)))
    strong = spell("profile:2", ap_cost=4, per_turn=1, strategy=SpellStrategy(priority=1))
    weak = spell("profile:1", ap_cost=2, per_turn=1, per_target=1, strategy=SpellStrategy(priority=2))
    plan = plan_turn(state(enemies=enemies), MAP, [weak, strong], rules=HYPOTHESIS)
    assert plan.describe() == ["CAST profile:2 ON E1", "CAST profile:1 ON E1", "END_TURN"]


def test_per_turn_and_per_target_limits() -> None:
    enemies = (EnemyState("E1", offset(PLAYER, 2, 0)), EnemyState("E2", offset(PLAYER, 0, 2)))
    per_target = plan_turn(state(ap=9, enemies=enemies), MAP, [spell(per_turn=3, per_target=1)], rules=HYPOTHESIS)
    assert per_target.describe() == ["CAST profile:1 ON E1", "CAST profile:1 ON E2", "END_TURN"]
    already = plan_turn(state(casts_this_turn={"profile:1": 2}), MAP, [spell()], rules=HYPOTHESIS)
    assert already.describe() == ["END_TURN"]
    targeted = plan_turn(state(ap=9, casts_on_target={("profile:1", "E1"): 2}), MAP, [spell(per_turn=5)],
                         rules=HYPOTHESIS)
    assert targeted.describe() == ["END_TURN"]


def test_determinism() -> None:
    enemies = (EnemyState("E3", offset(PLAYER, -4, 0)), EnemyState("E1", offset(PLAYER, 4, 0)))
    plans = {tuple(plan_turn(state(enemies=enemies), MAP, [spell(per_turn=1)], rules=HYPOTHESIS).describe())
             for _ in range(10)}
    assert len(plans) == 1


def test_end_turn_when_no_safe_action_and_ignored_spells_are_noted() -> None:
    ally = spell("profile:5", strategy=SpellStrategy(target=SpellTarget.ALLY))
    low_hp = spell("profile:6", strategy=SpellStrategy(timing=SpellTiming.LOW_HP))
    unconfirmed = spell("profile:7", provenance=SpellProvenance.OCR_UNVERIFIED)
    plan = plan_turn(state(), MAP, [ally, low_hp, unconfirmed], rules=HYPOTHESIS)
    assert plan.describe() == ["END_TURN"] and len(plan.notes) == 3
    assert plan_turn(state(enemies=()), MAP, [spell()], rules=HYPOTHESIS).describe() == ["END_TURN"]


def test_tackle_policy_never_moves_away_from_contact() -> None:
    adjacent = offset(PLAYER, 1, 0)
    far_enemy = offset(PLAYER, -4, 0)   # atteignable au contact avec 3 PM
    enemies = (EnemyState("E1", adjacent), EnemyState("E2", far_enemy))
    melee_only_far = spell(min_range=1, max_range=1, per_target=1, per_turn=2)
    plan = plan_turn(state(enemies=enemies), MAP, [melee_only_far], rules=HYPOTHESIS)
    assert StepKind.MOVE not in [step.kind for step in plan.steps]
    relaxed = plan_turn(state(enemies=enemies), MAP, [melee_only_far], rules=HYPOTHESIS,
                        policy=PlannerPolicy(avoid_tackle=False))
    assert StepKind.MOVE in [step.kind for step in relaxed.steps]


def test_first_turn_spell_and_state_from_observation() -> None:
    opener = spell(strategy=SpellStrategy(timing=SpellTiming.FIRST_TURN))
    assert plan_turn(state(turn_number=1), MAP, [opener], rules=HYPOTHESIS).steps[0].kind is StepKind.CAST
    assert plan_turn(state(turn_number=3), MAP, [opener], rules=HYPOTHESIS).describe() == ["END_TURN"]

    from combatbot.models import Cell
    from combatbot.vision.combat_models import (
        CellVisualState, CombatGridObservation, CombatObservation, EnemyObservation, ObservedCell,
    )
    cells = (ObservedCell(Cell(0, 0), (0, 0), (), CellVisualState.OCCUPIED, 0.9, cell_id=310),
             ObservedCell(Cell(1, 0), (0, 0), (), CellVisualState.UNKNOWN, 0.1, cell_id=311),
             ObservedCell(Cell(2, 0), (0, 0), (), CellVisualState.OCCUPIED, 0.9, cell_id=PLAYER))
    observation = CombatObservation(True, 0.9, True, 0.9, Cell(0, 0), 0.9,
                                    (EnemyObservation("E1", Cell(0, 0), (0, 0), 0.9, cell_id=310),),
                                    CombatGridObservation(cells=cells, confidence=0.9), 6, 3, 0.9, 0.9, 0.9,
                                    player_cell_id=PLAYER)
    real = RealCombatState.from_observation(observation, map_id=123, phase="FIGHTING", turn="PLAYER")
    assert real.occupied_cells == frozenset({310}) and real.unknown_cells == frozenset({311})
    assert real.enemies == (EnemyState("E1", 310, True),) and real.safe_for_decision is False
    assert plan_turn(real, MAP, [spell()], rules=HYPOTHESIS).status is PlanStatus.BLOCKED
    assert replace(real, safe_for_decision=True).blocking_unknowns() == ()
