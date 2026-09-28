"""LOT-4C-LIVE-PROOF : échantillons de vérité client et comparaison de règles candidates (aucune adoptée)."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from combatbot.combat.pathfinding import CombatMap
from combatbot.combat.targeting import CONSERVATIVE_RULES, RangeMetric
from combatbot.combat.targeting_proof import (
    CANDIDATES, REQUIRED_CASES, ProofSample, TargetingProofStore, compare, manhattan, segment_cells,
)
from combatbot.gamedata.models import DofusCellId, GameMap, GameMapCell, GridCoordinate
from combatbot.gamedata.topology import CELL_COUNT, grid_to_cell


def at(x: int, y: int) -> int:
    return int(grid_to_cell(GridCoordinate(x, y)))


WALL = at(7, 1)


def game_map(map_id: int) -> CombatMap:
    cells = tuple(GameMapCell(DofusCellId(i), walkable=True, non_walkable_during_fight=False,
                              line_of_sight=i != WALL) for i in range(CELL_COUNT))
    return CombatMap.from_game_map(GameMap(map_id, cells, "test", 11))


MAPS = {1: game_map(1), 2: game_map(2)}
CASTER = at(5, 0)


def truth_sample(sample_id: str, map_id: int, **changes) -> ProofSample:
    """Vérité synthétique produite par « manhattan + LOS stricte » : le candidat doit concorder."""
    base = ProofSample(sample_id, None, map_id, CASTER, "profile:1", "Flèche", 1, 3, True, False, True, 1)
    base = replace(base, **changes)
    rule = CANDIDATES["manhattan + LOS stricte (coins bloquants)"]
    cells = [at(x, y) for x in range(2, 10) for y in range(-3, 4) if grid_to_cell(GridCoordinate(x, y)) is not None
             and at(x, y) != CASTER and manhattan(CASTER, at(x, y)) <= base.max_range + 3]
    targetable = frozenset(cell for cell in cells if rule(MAPS[map_id], base, cell))
    return replace(base, targetable=targetable, not_targetable=frozenset(cells) - targetable)


def test_sample_validation_store_and_frozen_split(tmp_path: Path) -> None:
    sample = truth_sample("s1", 1)
    store = TargetingProofStore(tmp_path / "targeting_proof")
    store.save(sample)
    assert store.load() == [sample] and sample.split == ProofSample.from_dict(sample.to_dict()).split
    with pytest.raises(ValueError):
        replace(sample, not_targetable=sample.targetable).validate()
    with pytest.raises(ValueError):
        replace(sample, targetable=frozenset(), not_targetable=frozenset()).validate()
    (tmp_path / "targeting_proof" / "broken.json").write_text("{", encoding="utf-8")
    assert len(store.load()) == 1


def test_segment_is_exact_through_lattice_corners() -> None:
    crossed, corners = segment_cells(at(5, 0), at(8, 1))
    assert crossed == {at(6, 0), at(7, 1)} and corners == {at(6, 1), at(7, 0)}
    assert segment_cells(at(5, 0), at(8, 0)) == (frozenset({at(6, 0), at(7, 0)}), frozenset())


def test_consistent_candidate_needs_full_coverage_and_two_maps() -> None:
    one = [truth_sample("a", 1)]
    report = compare(one, MAPS.get)
    strict = report["candidates"]["manhattan + LOS stricte (coins bloquants)"]
    assert strict["disagree"] == 0 and strict["verdict"] == "COHÉRENT, PREUVE INSUFFISANTE"
    assert report["candidates"]["chebyshev + LOS stricte"]["verdict"] == "REJETÉ"
    assert report["candidates"]["manhattan sans LOS"]["verdict"] == "REJETÉ"          # le mur bloque la vue
    assert report["rule_status"] == "UNVERIFIED"
    both = compare(one + [truth_sample("b", 2)], MAPS.get)
    assert set(REQUIRED_CASES) <= set(both["coverage"]) and both["maps"] == [1, 2]
    assert both["candidates"]["manhattan + LOS stricte (coins bloquants)"]["verdict"] == "COHÉRENT"
    assert both["rule_status"] == "UNVERIFIED"                  # jamais adoptée automatiquement
    assert CONSERVATIVE_RULES.range_metric is RangeMetric.UNVERIFIED


def test_unknown_bonus_or_static_los_stays_unknown() -> None:
    sample = truth_sample("c", 1)
    report = compare([replace(sample, range_bonus=None)], MAPS.get)
    assert all(bucket["unknown"] > 0 for bucket in report["candidates"].values())
    unknown_map = CombatMap(1, MAPS[1].neighbors, MAPS[1].static, {})
    report = compare([sample], {1: unknown_map}.get)
    assert report["candidates"]["manhattan + LOS stricte (coins bloquants)"]["unknown"] > 0
    assert compare([sample], lambda _m: None)["skipped"][0]["reason"] == "topologie de map indisponible"


def test_cli_report_never_adopts_a_rule(tmp_path: Path, monkeypatch, capsys) -> None:
    from combatbot.benchmark import main
    monkeypatch.setenv("PYTHONBOT_DATA_DIR", str(tmp_path))
    TargetingProofStore(tmp_path / "data" / "targeting_proof").save(truth_sample("a", 1))
    assert main(["--targeting-proof-report", "--client", str(tmp_path / "absent"),
                 "--output-dir", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "UNVERIFIED" in out and (tmp_path / "out" / "4c-proof.json").exists()
