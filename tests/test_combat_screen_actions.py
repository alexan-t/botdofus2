"""FAST-5A1 : traduction plan → actions écran (rien n'est exécuté)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from combatbot.combat.planner import ExpectedState, PlanStatus, PlanStep, StepKind, plan_turn
from combatbot.combat.screen_actions import (
    ScreenActionKind, ScreenGeometry, SpellBarLayout, TranslationStatus, translate_step,
)
from combatbot.combat.spells import SpellSlot
from combatbot.combat.state import EnemyState
from combatbot.vision.coordinates import ClientSize, LayoutSignature, NormalizedRect, ScreenPoint
from combatbot.vision.grid_profile import CombatGridProfileV2
from combatbot.vision.grid_projection import GridScreenTransform, Vector2, cell_to_screen
from combatbot.vision.models import Calibration, RelativeRect, ZoneEvidence
from tests.test_combat_planner import HYPOTHESIS, MAP, PLAYER, spell, state
from tests.test_combat_targeting import offset

CLIENT = ClientSize(1000, 800)
ORIGIN = ScreenPoint(200, 100)
ZONES = {"combat": NormalizedRect(0.05, 0.05, 0.82, 0.725),
         "spell_bar": NormalizedRect(0.5, 0.9, 0.4, 0.08),
         "end_turn": NormalizedRect(0.9, 0.9, 0.08, 0.06)}
TRANSFORM = GridScreenTransform.from_cell_size((33.0, 18.5), 54.0, 27.0, reference_combat_size=(820.0, 580.0))
SPELL = spell(slot=SpellSlot(1, 3))
SPELLS = {SPELL.key: SPELL}


def calibration(zones=ZONES, *, status="confirmée", size=CLIENT) -> Calibration:
    signature = LayoutSignature.create(size, zones).to_json()
    return Calibration(1, size.width, size.height, {name: RelativeRect.from_normalized_rect(rect)
                                                    for name, rect in zones.items()},
                       {name: ZoneEvidence(0.9, "manual", status) for name in zones}, signature)


def grid_profile(zones=ZONES, size=CLIENT, transform=TRANSFORM) -> CombatGridProfileV2:
    return CombatGridProfileV2(transform, LayoutSignature.create(size, zones).to_json(), confirmed_by_user=True)


def geometry(*, client=CLIENT, origin=ORIGIN, calib=None, profile=..., bar=SpellBarLayout(10, 2, 1)):
    result, reasons = ScreenGeometry.from_calibration(
        calib or calibration(), hwnd=42, client_origin=origin, client_size=client,
        grid_profile=grid_profile() if profile is ... else profile, grid_confidence=0.95, spell_bar=bar, map_id=123)
    assert result is not None, reasons
    return result


def move(cell: int) -> PlanStep:
    return PlanStep(StepKind.MOVE, "test", (), 0, 1, ExpectedState(cell, 6, 2), path=(cell,), origin_cell_id=PLAYER)


def cast(target_cell: int | None, key: str = SPELL.key) -> PlanStep:
    return PlanStep(StepKind.CAST, "test", (), 3, 0, ExpectedState(PLAYER, 3, 3), spell_key=key, target_id="E1",
                    target_cell_id=target_cell)


END = PlanStep(StepKind.END_TURN, "test", (), 0, 0, ExpectedState(PLAYER, 6, 3))


def test_known_cell_goes_through_the_gamedata_projection() -> None:
    geo = geometry()
    result = translate_step(move(PLAYER), geo, SPELLS)
    assert result.status is TranslationStatus.READY and len(result.actions) == 1
    action = result.actions[0]
    expected = cell_to_screen(PLAYER, TRANSFORM, geo.layout)
    assert (action.screen_x, action.screen_y) == (expected.x, expected.y)
    assert (action.client_x + ORIGIN.x, action.client_y + ORIGIN.y) == (action.screen_x, action.screen_y)
    assert action.kind is ScreenActionKind.CLICK and action.clicks == 1 and action.button == "left"
    assert action.semantic_target == f"cell:{PLAYER}" and action.source == "gamedata_projection"
    assert action.confidence == 0.95


def test_cast_clicks_the_calibrated_slot_then_the_target_cell() -> None:
    geo = geometry()
    target = offset(PLAYER, 2, 0)
    result = translate_step(cast(target), geo, SPELLS)
    assert result.status is TranslationStatus.READY
    slot, cell = result.actions
    bar = geo.layout.normalized_rect_to_client(ZONES["spell_bar"])
    assert slot.client_x == pytest.approx(bar.x + 2.5 * bar.width / 10)        # case 3 → colonne 2
    assert slot.client_y == pytest.approx(bar.y + 0.5 * bar.height / 2)        # rangée 1
    assert slot.semantic_target == "spell_slot:1/3" and cell.semantic_target == f"cell:{target}"
    second_row = translate_step(cast(target), geo, {SPELL.key: replace(SPELL, slot=SpellSlot(1, 12))})
    assert second_row.actions[0].client_y == pytest.approx(bar.y + 1.5 * bar.height / 2)


def test_end_turn_uses_the_center_of_the_calibrated_zone() -> None:
    geo = geometry()
    action = translate_step(END, geo, SPELLS).actions[0]
    box = geo.layout.normalized_rect_to_client(ZONES["end_turn"])
    assert (action.client_x, action.client_y) == (box.x + box.width / 2, box.y + box.height / 2)
    assert action.source == "calibration.end_turn"


def test_missing_projection_refuses() -> None:
    result = translate_step(move(PLAYER), geometry(profile=None), SPELLS)
    assert result.status is TranslationStatus.REFUSED and result.actions == ()
    assert "projection GameData absente" in result.reasons[0]
    unconfirmed = CombatGridProfileV2(TRANSFORM, LayoutSignature.create(CLIENT, ZONES).to_json())
    assert translate_step(move(PLAYER), geometry(profile=unconfirmed), SPELLS).status is TranslationStatus.REFUSED
    assert translate_step(move(PLAYER), None, SPELLS).reasons == ("géométrie écran inconnue",)


def test_spell_without_slot_or_outside_the_visible_page_refuses() -> None:
    target = offset(PLAYER, 2, 0)
    no_slot = translate_step(cast(target), geometry(), {SPELL.key: replace(SPELL, slot=None)})
    assert no_slot.status is TranslationStatus.REFUSED and "sans case" in no_slot.reasons[0]
    other_page = translate_step(cast(target), geometry(), {SPELL.key: replace(SPELL, slot=SpellSlot(2, 3))})
    assert "page 2, page affichée 1" in other_page.reasons[0]
    unknown_page = translate_step(cast(target), geometry(bar=SpellBarLayout(10, 2, None)), SPELLS)
    assert unknown_page.reasons == ("page de sorts affichée inconnue",)
    beyond = translate_step(cast(target), geometry(), {SPELL.key: replace(SPELL, slot=SpellSlot(1, 21))})
    assert "hors de la barre" in beyond.reasons[0]
    assert translate_step(cast(target), geometry(bar=None), SPELLS).reasons == ("découpage de la barre de sorts inconnu",)
    assert "inconnu du traducteur" in translate_step(cast(target, key="profile:9"), geometry(), SPELLS).reasons[0]


def test_incompatible_calibration_gives_no_geometry() -> None:
    result, reasons = ScreenGeometry.from_calibration(calibration(), hwnd=1, client_origin=ORIGIN,
                                                      client_size=ClientSize(1600, 700))
    assert result is None and "ASPECT_RATIO_CHANGED" in reasons[0]


def test_missing_or_unconfirmed_end_turn_zone_refuses() -> None:
    without = {name: rect for name, rect in ZONES.items() if name != "end_turn"}
    geo = geometry(calib=calibration(without), profile=grid_profile(without))
    assert translate_step(END, geo, SPELLS).reasons == ("zone end_turn absente ou non confirmée",)
    proposed = geometry(calib=calibration(status="proposée"))
    assert translate_step(END, proposed, SPELLS).status is TranslationStatus.REFUSED
    assert translate_step(cast(PLAYER), proposed, SPELLS).status is TranslationStatus.REFUSED


def test_resized_client_scales_every_point() -> None:
    small, big = geometry(), geometry(client=ClientSize(1250, 1000))
    for step in (move(PLAYER), cast(offset(PLAYER, 2, 0)), END):
        for a, b in zip(translate_step(step, small, SPELLS).actions, translate_step(step, big, SPELLS).actions):
            assert b.client_x == pytest.approx(a.client_x * 1.25) and b.client_y == pytest.approx(a.client_y * 1.25)
    assert small.layout_digest != big.layout_digest


def test_client_to_screen_follows_the_window_origin() -> None:
    moved = geometry(origin=ScreenPoint(-1500, 40))            # écran secondaire à gauche
    here, there = translate_step(END, geometry(), SPELLS).actions[0], translate_step(END, moved, SPELLS).actions[0]
    assert (here.client_x, here.client_y) == (there.client_x, there.client_y)
    assert there.screen_x == there.client_x - 1500 and there.screen_y == there.client_y + 40


def test_unknown_values_are_refused() -> None:
    geo = geometry()
    assert translate_step(move(10_000), geo, SPELLS).reasons == ("cellule 10000 hors topologie",)
    assert translate_step(cast(None), geo, SPELLS).reasons == ("cellule cible inconnue",)
    off = GridScreenTransform(TRANSFORM.origin, Vector2(-27, 13.5), Vector2(-27, -13.5))
    assert "orientation" in translate_step(move(PLAYER), replace(geo, grid_transform=off), SPELLS).reasons[0]
    shifted = GridScreenTransform.from_cell_size((900.0, 18.5), 54.0, 27.0)
    assert "hors de la zone de combat" in translate_step(move(PLAYER), replace(geo, grid_transform=shifted),
                                                         SPELLS).reasons[0]


def test_every_step_of_a_real_plan_translates() -> None:
    plan = plan_turn(state(enemies=(EnemyState("E1", offset(PLAYER, 4, 0)),)), MAP, [SPELL], rules=HYPOTHESIS)
    assert plan.status is PlanStatus.READY
    translated = [translate_step(step, geometry(), SPELLS) for step in plan.steps]
    assert all(item.status is TranslationStatus.READY for item in translated)
    assert [len(item.actions) for item in translated] == [1, 2, 2, 1]   # MOVE, CAST, CAST, END_TURN
    assert translated[0].to_dict()["actions"][0]["clicks"] == 1
