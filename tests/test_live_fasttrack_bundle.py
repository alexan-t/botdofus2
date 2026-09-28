"""Dossier de recette live fast-track : tout est lu, rien n'est déduit d'une absence, aucune action."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from combatbot.benchmark import main
from combatbot.live.fasttrack_bundle import build_bundle, latest_session, summary_markdown, write_bundle
from tests.test_runtime_profile import write_session

FILES = {"summary.md", "telemetry.json", "map.json", "grid.json", "entities.json", "hud.json",
         "combat-state.json", "4c-proof.json", "readiness.json"}


def frame(**changes) -> dict:
    values = {"stage_ms": {"total": 300.0, "capture": 100.0}, "phase": "FIGHTING", "turn_owner": "OTHER",
              "map_status": "RESOLVED", "map_id": 1, "grid_source": "GAMEDATA_PROJECTED",
              "grid_visibility": "VISIBLE", "alignment": "ALIGNED", "ap": 6, "mp": 3, "player_cell_id": 10,
              "combat_state_model": True}
    values.update(changes)
    return values


def test_bundle_measures_the_selected_session_only(tmp_path: Path) -> None:
    write_session(tmp_path, "old", [frame(map_id=9)])
    time.sleep(0.01)
    write_session(tmp_path, "new", [frame(), frame(map_id=2, alignment="MISALIGNED"), frame(map_id=2),
                                    frame(map_id=2, ap=None, player_cell_id=None, phase="EXPLORATION")])
    assert latest_session([tmp_path]) == "new"
    reports = build_bundle([tmp_path], session="new")
    assert reports["map.json"]["map_changes"] == [{"frame_index": 1, "from": 1, "to": 2}]
    assert reports["map.json"]["distinct_maps"] == [1, 2] and reports["map.json"]["verdict"] == "PARTIAL"
    assert reports["grid.json"]["alignment_after_map_change"][0]["frames_until_aligned"] == 1
    assert reports["grid.json"]["verdict"] == "PARTIAL"
    assert reports["hud.json"]["ap_unknown_rate"] == 0.0 and reports["hud.json"]["verdict"] == "NOT_EVALUABLE"
    assert reports["entities.json"]["reasons"] == {"NOT_IN_FIGHT": 1}
    assert reports["combat-state.json"]["fail_closed"] and reports["telemetry.json"]["frames"] == 4
    assert reports["4c-proof.json"]["rule_status"] == "UNVERIFIED"
    output = write_bundle(reports, tmp_path / "out", session="new")
    assert {path.name for path in output.iterdir()} == FILES


def test_my_turn_without_model_is_a_failure_and_no_session_is_not_evaluable(tmp_path: Path) -> None:
    write_session(tmp_path, "s", [frame(combat_state_model=False, turn_owner="PLAYER")])
    state = build_bundle([tmp_path], session="s")["combat-state.json"]
    assert state["my_turn_claimed_without_model"] == 1 and state["verdict"] == "FAIL"
    empty = build_bundle([tmp_path / "none"], session=None)
    assert empty["map.json"]["verdict"] == "NOT_EVALUABLE" and empty["telemetry.json"]["verdict"] == "NOT_EVALUABLE"
    assert "NOT_EVALUABLE" in summary_markdown(empty, session=None)


def test_cli_writes_the_folder_with_a_closed_gate(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PYTHONBOT_DATA_DIR", str(tmp_path))
    (tmp_path / "data").mkdir()
    write_session(tmp_path / "data", "s", [frame()])
    output = tmp_path / "bundle"
    assert main(["--live-fasttrack-report", "--data-dir", str(tmp_path / "data"), "--client",
                 str(tmp_path / "absent"), "--output-dir", str(output)]) == 0
    readiness = json.loads((output / "readiness.json").read_text(encoding="utf-8"))
    assert readiness["real_input_gate"] == "CLOSED" and readiness["profile"]["profile_id"] is not None
    assert {path.name for path in output.iterdir()} == FILES
    assert "AUCUNE" in capsys.readouterr().out and not os.environ.get("DOFBOT_ALLOW_REAL_INPUT")
