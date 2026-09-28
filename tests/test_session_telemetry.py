"""LOT 3B-7 : journal de session réelle (latence par étape, états, configuration)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from combatbot.vision.combat_observer import RealCombatObserver
from combatbot.vision.session_telemetry import STAGES, SessionTelemetry, _stats, frame_record, summarize
from tests.test_combat_observation import GRID, calibration, combat_frame


def _metadata(index: int, total: float, *, map_status="RESOLVED", map_id=104072452, phase="FIGHTING") -> dict:
    return {"frame_index": index, "client_size": [2560, 1377], "analysis_ms": total,
            "stage_ms": {"capture": 10.0, "map": 1.0, "total": total},
            "map_resolution": {"status": map_status, "map_id": map_id},
            "grid_source": "GAMEDATA_PROJECTED", "grid_visibility_state": "VISIBLE", "alignment_status": "ALIGNED",
            "semantic_combat_state": {"phase": phase, "turn_owner": "PLAYER"}}


def _observation(ap=6, mp=3, cell=120, enemies=2):
    return SimpleNamespace(ap=ap, mp=mp, player_cell_id=cell, enemies=tuple(range(enemies)))


def test_observer_reports_every_stage_duration() -> None:
    frame = combat_frame(combat=False)
    observer = RealCombatObserver(7, calibration(), frame_provider=lambda: frame,
                                  number_reader=lambda _image: (None, 0), grid_calibration=GRID)
    stages = observer.observe().metadata["stage_ms"]
    assert set(STAGES) <= set(stages)
    assert all(value >= 0 for value in stages.values())
    assert abs(sum(value for key, value in stages.items() if key != "total") - stages["total"]) < 1.0


def test_stats_percentiles() -> None:
    stats = _stats([float(value) for value in range(1, 101)])
    assert (stats["n"], stats["median"], stats["p95"], stats["max"]) == (100, 50.5, 95.0, 100.0)
    assert _stats([])["mean"] is None


def test_frame_record_has_no_pixels_and_keeps_states() -> None:
    record = frame_record(_metadata(3, 120.0), _observation(), wall_time=1.23456)
    assert record["t"] == 1.235 and record["frame"] == 3 and record["map_status"] == "RESOLVED"
    assert record["phase"] == "FIGHTING" and record["turn_owner"] == "PLAYER" and record["enemies"] == 2
    assert all(not hasattr(value, "shape") for value in record.values())


def test_summary_rates_cadence_and_undetected_context() -> None:
    records = [frame_record(_metadata(i, 100.0 + i), _observation(ap=None if i == 0 else 6), wall_time=i * 0.5)
               for i in range(4)]
    records[1]["map_status"] = "AMBIGUOUS"
    report = summarize(records, context={"tactical_mode": "non détecté"}, skipped_busy=2)
    assert report["frames"] == 4 and report["skipped_busy_ticks"] == 2
    assert report["cadence"]["fps"] == 2.0
    assert report["unknown_rates"]["ap"] == 0.25 and report["unknown_rates"]["map"] == 0.25
    assert report["latency_ms"]["total"]["max"] == 103.0 and report["latency_ms"]["capture"]["n"] == 4
    assert report["latency_ms"]["hud"]["n"] == 0
    assert report["client_sizes"] == {"2560x1377": 4} and report["distinct_maps"] == [104072452]


def test_session_writes_frames_and_summary(tmp_path: Path) -> None:
    telemetry = SessionTelemetry(tmp_path, "session_test", {"dpi": 96, "layout_digest": "abc"})
    for index in range(3):
        telemetry.record(_metadata(index, 90.0), _observation())
    telemetry.skip()
    telemetry.error("capture perdue")
    report = telemetry.close()
    folder = tmp_path / "session_test"
    lines = (folder / "frames.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["frame"] == 0
    saved = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    assert saved["frames"] == 3 and saved["context"]["dpi"] == 96 and saved["errors"] == ["capture perdue"]
    assert saved["context"]["tactical_mode"] == "non détecté" and saved["context"]["runtime"] == "sources"
    assert "| total | 3 |" in (folder / "summary.md").read_text(encoding="utf-8")
    assert report["skipped_busy_ticks"] == 1


def test_empty_session_writes_no_summary(tmp_path: Path) -> None:
    telemetry = SessionTelemetry(tmp_path, "vide", {})
    assert telemetry.close() is None
    assert not (tmp_path / "vide" / "summary.json").exists()
