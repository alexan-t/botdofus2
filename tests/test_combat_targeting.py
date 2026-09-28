"""FAST-4C : ciblage conservateur (TARGETABLE / NOT_TARGETABLE / UNKNOWN)."""
from __future__ import annotations

from combatbot.combat.spells import CombatSpell, SpellProvenance
from combatbot.combat.targeting import (
    CONSERVATIVE_RULES, RangeMetric, Reason, Targetability, TargetingRules, evaluate_target, in_line,
    logical_distance,
)
from combatbot.gamedata.models import GridCoordinate
from combatbot.gamedata.topology import cell_to_grid, grid_to_cell
from tests.test_combat_pathfinding import make_map

MAP = make_map()
HYPOTHESIS = TargetingRules(range_metric=RangeMetric.LOGICAL_MANHATTAN)


def spell(**changes) -> CombatSpell:
    values = dict(key="profile:1", name="Flèche", ap_cost=3, min_range=1, max_range=4, modifiable_range=False,
                  line_cast=False, line_of_sight=False, per_turn=2, per_target=1,
                  provenance=SpellProvenance.HUMAN_CONFIRMED)
    values.update(changes)
    return CombatSpell(**values)


def offset(cell: int, dx: int, dy: int) -> int:
    origin = cell_to_grid(cell)
    found = grid_to_cell(GridCoordinate(origin.x + dx, origin.y + dy))
    assert found is not None
    return int(found)


CASTER = 300


def test_default_rules_never_decide_range() -> None:
    result = evaluate_target(MAP, spell(), CASTER, offset(CASTER, 2, 0), available_ap=6)
    assert result.status is Targetability.UNKNOWN and Reason.RANGE_RULE_UNVERIFIED in result.reasons
    assert result.distance is None and result.assumptions == ()


def test_proven_rules_decide_even_without_range_metric() -> None:
    target = offset(CASTER, 2, 0)
    assert evaluate_target(MAP, spell(), CASTER, target, available_ap=2).status is Targetability.NOT_TARGETABLE
    limited = evaluate_target(MAP, spell(), CASTER, target, available_ap=6, casts_this_turn=2)
    assert limited.status is Targetability.NOT_TARGETABLE and Reason.PER_TURN_LIMIT_REACHED in limited.reasons
    per_target = evaluate_target(MAP, spell(), CASTER, target, available_ap=6, casts_on_target=1)
    assert Reason.PER_TARGET_LIMIT_REACHED in per_target.reasons
    diagonal = evaluate_target(MAP, spell(line_cast=True), CASTER, offset(CASTER, 1, 1), available_ap=6)
    assert diagonal.status is Targetability.NOT_TARGETABLE and Reason.NOT_IN_LINE in diagonal.reasons
    assert evaluate_target(MAP, spell(), CASTER, 999, available_ap=6).reasons == (Reason.INVALID_CELL,)


def test_hypothesis_range_is_explicit_and_traced() -> None:
    near = evaluate_target(MAP, spell(), CASTER, offset(CASTER, 2, 1), available_ap=6, rules=HYPOTHESIS)
    assert near.status is Targetability.TARGETABLE and near.distance == 3
    assert near.assumptions and "non prouvée" in near.assumptions[0]
    far = evaluate_target(MAP, spell(), CASTER, offset(CASTER, 3, 2), available_ap=6, rules=HYPOTHESIS)
    assert far.status is Targetability.NOT_TARGETABLE and Reason.OUT_OF_RANGE in far.reasons
    too_close = evaluate_target(MAP, spell(min_range=2), CASTER, offset(CASTER, 1, 0), available_ap=6,
                                rules=HYPOTHESIS)
    assert Reason.OUT_OF_RANGE in too_close.reasons


def test_modifiable_range_needs_known_bonus() -> None:
    target = offset(CASTER, 5, 0)
    unknown = evaluate_target(MAP, spell(modifiable_range=True), CASTER, target, available_ap=6, rules=HYPOTHESIS)
    assert unknown.status is Targetability.UNKNOWN and Reason.RANGE_BONUS_UNKNOWN in unknown.reasons
    boosted = evaluate_target(MAP, spell(modifiable_range=True), CASTER, target, available_ap=6, range_bonus=1,
                              rules=HYPOTHESIS)
    assert boosted.targetable and boosted.details["effective_max_range"] == 5
    fixed = evaluate_target(MAP, spell(), CASTER, target, available_ap=6, range_bonus=3, rules=HYPOTHESIS)
    assert fixed.status is Targetability.NOT_TARGETABLE   # portée fixe : le bonus ne s'applique pas
    malus = evaluate_target(MAP, spell(modifiable_range=True, min_range=2), CASTER, offset(CASTER, 2, 0),
                            available_ap=6, range_bonus=-5, rules=HYPOTHESIS)
    assert malus.details["effective_max_range"] == 2 and malus.targetable


def test_line_of_sight_is_unknown_without_a_verified_algorithm() -> None:
    result = evaluate_target(MAP, spell(line_of_sight=True), CASTER, offset(CASTER, 2, 0), available_ap=6,
                             rules=HYPOTHESIS)
    assert result.status is Targetability.UNKNOWN and Reason.LOS_ALGORITHM_UNVERIFIED in result.reasons

    def clear(_map, _a, _b, _occupied):
        return True

    def blocked(_map, _a, _b, _occupied):
        return False

    def unsure(_map, _a, _b, _occupied):
        return None

    oracle = TargetingRules(RangeMetric.LOGICAL_MANHATTAN, clear, los_oracle_verified=False)
    with_oracle = evaluate_target(MAP, spell(line_of_sight=True), CASTER, offset(CASTER, 2, 0), available_ap=6,
                                  rules=oracle)
    assert with_oracle.targetable and any("oracle non vérifié" in item for item in with_oracle.assumptions)
    assert evaluate_target(MAP, spell(line_of_sight=True), CASTER, offset(CASTER, 2, 0), available_ap=6,
                           rules=TargetingRules(RangeMetric.LOGICAL_MANHATTAN, blocked)).status \
        is Targetability.NOT_TARGETABLE
    assert Reason.LOS_UNKNOWN in evaluate_target(MAP, spell(line_of_sight=True), CASTER, offset(CASTER, 2, 0),
                                                 available_ap=6, rules=TargetingRules(RangeMetric.LOGICAL_MANHATTAN,
                                                                                      unsure)).reasons


def test_unconfirmed_spell_or_unknown_ap() -> None:
    ocr = spell(provenance=SpellProvenance.OCR_UNVERIFIED)
    result = evaluate_target(MAP, ocr, CASTER, offset(CASTER, 1, 0), available_ap=6, rules=HYPOTHESIS)
    assert result.status is Targetability.UNKNOWN and result.reasons == (Reason.SPELL_NOT_CONFIRMED,)
    assert result.details["provenance"] == "OCR_UNVERIFIED"
    no_ap = evaluate_target(MAP, spell(), CASTER, offset(CASTER, 1, 0), available_ap=None, rules=HYPOTHESIS)
    assert no_ap.status is Targetability.UNKNOWN and no_ap.reasons == (Reason.AP_UNKNOWN,)
    assert CONSERVATIVE_RULES.assumptions == ()


def test_geometry_helpers() -> None:
    assert logical_distance(CASTER, offset(CASTER, -2, 3)) == 5
    assert in_line(CASTER, offset(CASTER, 0, 4)) and in_line(CASTER, offset(CASTER, -3, 0))
    assert not in_line(CASTER, offset(CASTER, 2, -1))
    assert evaluate_target(MAP, spell(), CASTER, CASTER, available_ap=6, rules=HYPOTHESIS).status \
        is Targetability.NOT_TARGETABLE   # distance 0 < portée minimale 1
