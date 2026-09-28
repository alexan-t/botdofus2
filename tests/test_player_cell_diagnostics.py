"""Raisons de chaque cellule joueur inconnue (lecture seule, aucun seuil modifié)."""
from __future__ import annotations

import json
from pathlib import Path

from combatbot.benchmark import main
from combatbot.live.player_cell_diagnostics import classify, diagnose, load_records, record_from_document
from combatbot.vision.session_telemetry import frame_record
from tests.test_runtime_profile import write_session

FIGHT = dict(phase="FIGHTING", map_status="RESOLVED", map_id=1, grid_source="GAMEDATA_PROJECTED",
             grid_visibility="VISIBLE", alignment="ALIGNED", player_cell_id=None)


def rec(diag=None, **changes) -> dict:
    return {**FIGHT, "player_diag": {"pipeline": "CELL_ENTITY_DETECTOR", "player_profile": "applied",
                                      **(diag or {})}, **changes}


def test_each_unknown_gets_the_first_failing_link_of_the_chain() -> None:
    assert classify(rec(player_cell_id=5)) is None
    assert classify(rec(phase="EXPLORATION")) == "NOT_IN_FIGHT"
    assert classify(rec(map_id=None, map_status="AMBIGUOUS")) == "MAP_UNKNOWN"
    assert classify(rec(grid_source="LEGACY_CALIBRATION")) == "GRID_NOT_GAMEDATA"
    assert classify(rec(grid_visibility="HIDDEN")) == "GRID_NOT_VISIBLE"
    assert classify(rec(alignment="MISALIGNED")) == "GRID_NOT_ALIGNED"
    assert classify(rec({"pipeline": "LEGACY_CLASSIFY"})) == "LEGACY_PIPELINE"
    assert classify(rec({"player_profile": "absent"})) == "PLAYER_PROFILE_ABSENT"
    assert classify(rec({"player_profile": "incompatible_layout"})) == "PLAYER_PROFILE_OTHER_LAYOUT"
    assert classify(rec()) == "UNEXPLAINED"                                  # diagnostic non journalisé
    assert classify(rec(unconfirmed_player_candidates=1)) == "CANDIDATE_NOT_CONFIRMED"
    assert classify(rec({"player_decision": None})) == "NO_PLAYER_CANDIDATE"
    assert classify(rec({"player_decision": "too_far"})) == "CANDIDATE_TOO_FAR"
    assert classify(rec({"player_decision": "ambiguous"})) == "CANDIDATE_AMBIGUOUS"
    assert classify(rec({"player_decision": "conflict_with_prior"})) == "CONFLICT_WITH_PRIOR"
    assert classify(rec({"player_decision": "selected"})) == "TRACKER_DROPPED"
    assert classify(rec({"player_decision": None}, phase=None)) == "NO_PLAYER_CANDIDATE"   # phase inconnue


def test_report_separates_fight_and_non_fight_frames() -> None:
    records = [rec(player_cell_id=3), rec({"player_decision": "too_far"}), rec(phase="EXPLORATION"),
               rec({"player_decision": None}, phase=None)]
    report = diagnose(records)
    assert report["player_unknown"] == 3 and report["fight_frames"] == 2 and report["fight_player_unknown_rate"] == 0.5
    assert report["reasons"] == {"CANDIDATE_TOO_FAR": 1, "NOT_IN_FIGHT": 1, "NO_PLAYER_CANDIDATE": 1}
    assert report["phase_unknown_frames"] == 1 and len(report["cases"]) == 2
    assert report["thresholds_modified"] == "NONE"


def test_corpus_document_and_session_journal_share_the_same_shape(tmp_path: Path) -> None:
    document = {"session_id": "s", "frame_index": 4,
                "prediction": {"player_cell_id": None, "player_confidence": 0.0, "grid": {"map_id_declared": 1},
                               "entity_evidence": [{"reasons": ["couleur d'équipe du joueur",
                                                                "joueur non confirmé (profil absent ou ambigu)"]}]},
                "capture": {"semantic_combat_state": {"phase": "FIGHTING"}, "map_resolution": {"status": "RESOLVED",
                            "map_id": 1}, "grid_source": "GAMEDATA_PROJECTED", "grid_visibility_state": "VISIBLE",
                            "alignment_status": "ALIGNED",
                            "entities": {"pipeline": "CELL_ENTITY_DETECTOR", "player_profile": "applied"}}}
    assert classify(record_from_document(document)) == "CANDIDATE_NOT_CONFIRMED"
    metadata = {"stage_ms": {"total": 1}, "semantic_combat_state": {"phase": "FIGHTING"},
                "map_resolution": {"status": "RESOLVED", "map_id": 1}, "grid_source": "GAMEDATA_PROJECTED",
                "grid_visibility_state": "VISIBLE", "alignment_status": "ALIGNED",
                "entities": {"pipeline": "CELL_ENTITY_DETECTOR", "player_profile": "applied", "player_decision": None,
                             "player_candidates": None, "player_track_state": None, "detector_ms": 3.0}}
    line = frame_record(metadata, None, wall_time=0.0)
    assert "detector_ms" not in line["player_diag"] and classify(line) == "NO_PLAYER_CANDIDATE"
    write_session(tmp_path, "s1", [line])
    records = load_records([tmp_path])
    assert records[0]["source"] == "session" and diagnose(records)["reasons"] == {"NO_PLAYER_CANDIDATE": 1}


def test_cli_writes_the_report(tmp_path: Path, capsys) -> None:
    write_session(tmp_path, "s1", [{**FIGHT, "stage_ms": {"total": 1}}])
    assert main(["--player-cell-diagnostics", "--data-dir", str(tmp_path), "--corpus-root",
                 str(tmp_path / "corpus"), "--output-dir", str(tmp_path / "out")]) == 0
    report = json.loads((tmp_path / "out" / "player-cell-diagnostics.json").read_text(encoding="utf-8"))
    assert report["reasons"] == {"UNEXPLAINED": 1} and "Aucun seuil modifié" in capsys.readouterr().out
