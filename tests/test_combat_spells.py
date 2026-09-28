"""FAST-4A : domaine de sort réel (inconnus champ par champ, provenance, adaptateurs)."""
from __future__ import annotations

import json

import pytest

from combatbot.combat.spells import (
    GAME_VERSION, CombatSpell, SpellProvenance, SpellSlot, SpellStatus, SpellStrategy, SpellTarget, SpellTiming,
    from_profile_row, load_profile_spells,
)
from combatbot.engine import CombatEngine
from combatbot.models import Spell, Strategy
from combatbot.storage import Storage
from combatbot.vision.models import IconCandidate, Profile

FULL = dict(ap_cost=3, min_range=1, max_range=6, modifiable_range=True, line_cast=False, line_of_sight=True,
            per_turn=2, per_target=1)


def test_status_requires_human_confirmation_and_every_characteristic() -> None:
    confirmed = CombatSpell("profile:1", "Flèche", provenance=SpellProvenance.HUMAN_CONFIRMED, **FULL)
    assert confirmed.status is SpellStatus.CONFIRMED and confirmed.usable_for_real_decision
    assert confirmed.game_version == GAME_VERSION == "2.64.5"
    missing_los = CombatSpell("profile:2", provenance=SpellProvenance.HUMAN_CONFIRMED, **{**FULL, "line_of_sight": None})
    assert missing_los.status is SpellStatus.UNKNOWN and missing_los.unknown_fields == ("line_of_sight",)
    for provenance in (SpellProvenance.OCR_UNVERIFIED, SpellProvenance.ICON_MATCH_UNCONFIRMED,
                       SpellProvenance.SIMULATED, SpellProvenance.UNKNOWN):
        assert CombatSpell("x", provenance=provenance, **FULL).status is SpellStatus.UNKNOWN
    unused = confirmed.with_strategy(SpellStrategy(use=False))
    assert unused.status is SpellStatus.CONFIRMED and not unused.usable_for_real_decision


def test_validation() -> None:
    for bad in ({"ap_cost": 0}, {"ap_cost": -1}, {"min_range": 5, "max_range": 2}, {"per_turn": 0},
                {"line_cast": 1}, {"ap_cost": 2.5}):
        with pytest.raises(ValueError):
            CombatSpell("x", **{**FULL, **bad})
    with pytest.raises(ValueError):
        CombatSpell(" ")
    with pytest.raises(ValueError):
        CombatSpell("x", slot=SpellSlot(0, 1))
    assert CombatSpell("x").unknown_fields == tuple(FULL)   # tout inconnu est permis


def test_serialization_round_trip() -> None:
    spell = CombatSpell("profile:3", "Flèche", slot=SpellSlot(1, 4), provenance=SpellProvenance.HUMAN_CONFIRMED,
                        strategy=SpellStrategy(True, SpellTarget.ALLY, SpellTiming.FIRST_TURN, 2), **FULL)
    data = json.loads(json.dumps(spell.to_dict()))
    assert data["status"] == "CONFIRMED" and data["unknown_fields"] == [] and "damage" not in data
    assert CombatSpell.from_dict(data) == spell


def test_simulator_compatibility_and_simulated_damage_never_crosses() -> None:
    simulated = Spell("Sort simulé A", 3, 1, 4, False, False, True, 2, 2, 10, damage=6, id=5)
    spell = CombatSpell.from_simulated(simulated)
    assert spell.provenance is SpellProvenance.SIMULATED and spell.status is SpellStatus.UNKNOWN
    assert "damage" not in spell.to_dict() and spell.key == "sim:5"
    back = spell.to_simulated(damage=6, priority=10)
    assert (back.ap_cost, back.max_range, back.per_target) == (3, 4, 2)
    engine = CombatEngine([back], Strategy())
    engine.start()
    for _ in range(40):
        engine.step()
    assert engine.turn >= 1
    with pytest.raises(ValueError):
        CombatSpell("x", ap_cost=3).to_simulated()


def _storage(tmp_path) -> tuple[Storage, int]:
    storage = Storage(tmp_path / "spells.sqlite3")
    return storage, storage.save_profile(Profile(None, "Kira"))


def test_profile_rows_adapter(tmp_path) -> None:
    storage, profile_id = _storage(tmp_path)
    confirmed = storage.save_scan_candidate(profile_id, IconCandidate(1, 1, b"png", "a", .9, .9, "Inconnu"))
    storage.save_profile_spell_fields(confirmed, {"name": "Flèche", **{k: int(v) if isinstance(v, bool) else v
                                                                         for k, v in FULL.items()}}, confirm=True)
    pending = storage.save_scan_candidate(profile_id, IconCandidate(1, 2, b"png", "b", .9, .9, "Inconnu"))
    storage.save_profile_spell_fields(pending, {"ap_cost": 4})
    # « Reconnu » : même icône qu'un sort confirmé → valeurs recopiées, mais jamais confirmées.
    storage.save_scan_candidate(profile_id, IconCandidate(2, 1, b"png", "a", .9, .95, "Reconnu",
                                                          known_spell_id=confirmed, known_name="Flèche"))
    storage.set_profile_setting(profile_id, "dofbot2_spells",
                                {str(confirmed): {"use": False, "target": "Allié", "when": "PV bas", "priority": 3}})
    spells = {spell.key: spell for spell in load_profile_spells(storage, profile_id)}
    first = spells[f"profile:{confirmed}"]
    assert first.status is SpellStatus.CONFIRMED and first.slot == SpellSlot(1, 1)
    assert first.line_of_sight is True and first.line_cast is False
    assert first.strategy == SpellStrategy(False, SpellTarget.ALLY, SpellTiming.LOW_HP, 3)
    assert not first.usable_for_real_decision
    second = spells[f"profile:{pending}"]
    assert second.provenance is SpellProvenance.OCR_UNVERIFIED and second.ap_cost == 4
    assert second.status is SpellStatus.UNKNOWN and "line_of_sight" in second.unknown_fields
    copied = next(spell for spell in spells.values() if spell.slot == SpellSlot(2, 1))
    assert copied.ap_cost == 3 and copied.provenance is SpellProvenance.ICON_MATCH_UNCONFIRMED
    assert copied.status is SpellStatus.UNKNOWN
    storage.close()


def test_confirmed_row_without_decision_ready_stays_unverified() -> None:
    row = {"id": 9, "status": "Confirmé", "decision_ready": 0, "page": 1, "slot": 1, "name": "X",
           **{key: int(value) if isinstance(value, bool) else value for key, value in FULL.items()}}
    assert from_profile_row(row).provenance is SpellProvenance.OCR_UNVERIFIED
    assert from_profile_row({**row, "decision_ready": 1}).status is SpellStatus.CONFIRMED
    assert from_profile_row({**row, "status": "Bizarre"}).provenance is SpellProvenance.UNKNOWN
