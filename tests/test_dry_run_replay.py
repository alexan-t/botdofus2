"""Rejeu dry-run du corpus : prédiction enregistrée → état → plan → exécuteur (aucune action)."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from combatbot.combat.targeting import RangeMetric, TargetingRules
from combatbot.corpus.dry_run_replay import (
    frame_map_id, markdown_report, run_dry_run_replay, safe_for_decision, state_from_document, write_report,
)
from combatbot.corpus.repository import CorpusRepository
from combatbot.models import Cell
from combatbot.vision.combat_models import (
    CellVisualState, CombatGridObservation, CombatObservation, EnemyObservation, ObservedCell,
)
from tests.test_combat_pathfinding import make_map
from tests.test_combat_planner import spell
from tests.test_combat_targeting import offset
from tests.test_corpus import make_debug

PLAYER = 300
ENEMY = offset(PLAYER, 2, 0)


def observation(**changes) -> CombatObservation:
    cells = (ObservedCell(Cell(0, 0), (0, 0), (), CellVisualState.OCCUPIED, 0.9, cell_id=ENEMY),
             ObservedCell(Cell(1, 0), (0, 0), (), CellVisualState.FREE, 0.9, cell_id=301),
             ObservedCell(Cell(2, 0), (0, 0), (), CellVisualState.OCCUPIED, 0.9, cell_id=PLAYER))
    values = dict(combat_detected=True, combat_confidence=0.9, player_turn=True, turn_confidence=0.9,
                  player_cell=Cell(2, 0), player_confidence=0.9,
                  enemies=(EnemyObservation("E1", Cell(0, 0), (0, 0), 0.9, cell_id=ENEMY),),
                  grid=CombatGridObservation(cells=cells, confidence=0.9), ap=6, mp=3, confidence_ap=0.9,
                  confidence_mp=0.9, observation_confidence=0.9, player_cell_id=PLAYER)
    values.update(changes)
    return CombatObservation(**values)


def recorded(obs: CombatObservation) -> dict:
    """Même sérialisation que save_debug_observation (Enum → valeur)."""
    return json.loads(json.dumps(asdict(obs), default=lambda value: value.value if hasattr(value, "value") else str(value)))


def test_safe_for_decision_matches_the_observation_property() -> None:
    cases = [observation(), observation(ap=None), observation(player_turn=None),
             observation(grid=CombatGridObservation(cells=(), confidence=0.9)),
             observation(observation_confidence=0.5),
             observation(enemies=(EnemyObservation("E1", Cell(0, 0), (0, 0), 0.9, cell_id=ENEMY,
                                                   observed_this_frame=False),)),
             observation(entities=({"kind": "UNKNOWN", "cell_id": 5},)),
             observation(grid=CombatGridObservation(cells=(ObservedCell(Cell(0, 0), (0, 0), (),
                                                                         CellVisualState.UNKNOWN, 0.1, cell_id=9),),
                                                    confidence=0.9))]
    for case in cases:
        assert safe_for_decision(recorded(case)) == case.safe_for_decision


def document(obs: CombatObservation, *, map_status="RESOLVED", map_id=123, phase="FIGHTING", turn="PLAYER") -> dict:
    return {"prediction": recorded(obs),
            "capture": {"map_resolution": {"status": map_status, "map_id": map_id},
                        "semantic_combat_state": {"phase": phase, "turn_owner": turn}},
            "grid_snapshot": {}}


def test_state_and_map_from_document() -> None:
    state = state_from_document(document(observation()))
    assert (state.map_id, state.player_cell_id, state.ap, state.mp) == (123, PLAYER, 6, 3)
    assert state.occupied_cells == frozenset({ENEMY}) and state.safe_for_decision
    assert state.blocking_unknowns() == ()
    assert frame_map_id({"capture": {"map_resolution": {"status": "AMBIGUOUS", "map_id": 5}}}) is None
    assert frame_map_id({"grid_snapshot": {"map_id_declared": 7, "map_id_source": "user_verified_mapid"}}) == 7
    assert frame_map_id({"grid_snapshot": {"map_id_declared": 7, "map_id_source": "manual_guess"}}) is None


def test_replay_counts_ready_and_blocked_frames(tmp_path: Path) -> None:
    repository = CorpusRepository(tmp_path / "corpus")
    ready = repository.import_debug(make_debug(tmp_path / "a", "a"), session_id="s", frame_index=0)
    blocked = repository.import_debug(make_debug(tmp_path / "b", "b"), session_id="s", frame_index=1)
    for entry, doc in ((ready, document(observation())), (blocked, document(observation(), turn="OTHER"))):
        path = repository.resolve(entry.paths["observation"]) if "observation" in entry.paths else None
        if path is None:
            path = next(repository.root.rglob(f"*{entry.observation_id}*.json"))
        original = json.loads(path.read_text(encoding="utf-8"))
        original.update(doc)
        path.write_text(json.dumps(original), encoding="utf-8")
    snapshot = {item: item.read_bytes() for item in repository.root.rglob("*") if item.is_file()}
    combat_map = make_map()
    hypothesis = TargetingRules(range_metric=RangeMetric.LOGICAL_MANHATTAN)
    report = run_dry_run_replay(repository, lambda map_id: combat_map if map_id == 123 else None, [spell()],
                                rules=hypothesis)
    assert report["frames"] == 2 and report["statuses"] == {"READY": 1, "BLOCKED": 1}
    assert report["blocked_reasons"] == {"tour": 1} and report["first_step"] == {"CAST": 1}
    assert report["examples"][0]["plan"][0] == "CAST profile:1 ON E1" and report["actions"] == "NONE"
    assert report["assumptions"]
    conservative = run_dry_run_replay(repository, lambda map_id: combat_map, [spell()])
    assert conservative["statuses"] == {"BLOCKED": 2}
    assert "sort possible mais non prouvé" in conservative["blocked_reasons"]
    no_map = run_dry_run_replay(repository, lambda map_id: None, [spell()], rules=hypothesis)
    assert no_map["blocked_reasons"].get("topologie de map indisponible") == 2   # les deux frames
    assert {item: item.read_bytes() for item in repository.root.rglob("*") if item.is_file()} == snapshot
    json_path, md_path = write_report(report, tmp_path / "out")
    assert json.loads(json_path.read_text(encoding="utf-8"))["frames"] == 2
    assert "Raisons de refus" in markdown_report(report) and md_path.exists()
