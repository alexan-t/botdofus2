"""Profil de latence lu depuis les journaux de sessions réelles (aucune capture rejouée)."""
from __future__ import annotations

import json
from pathlib import Path

from combatbot.benchmark import main
from combatbot.live.runtime_profile import markdown_report, runtime_profile
from combatbot.vision.session_telemetry import frame_record


def write_session(root: Path, name: str, frames: list[dict]) -> None:
    folder = root / "logs" / "sessions" / name
    folder.mkdir(parents=True)
    (folder / "frames.jsonl").write_text("\n".join(json.dumps(frame) for frame in frames) + "\nnot json\n",
                                         encoding="utf-8")


def frame(total: float, capture: float = 236.0, **extra) -> dict:
    return {"stage_ms": {"capture": capture, "map": 20.0, "grid": 120.0, "entities": 124.0, "hud": 60.0,
                         "combat_state": 10.0, "overlay": 30.0, "total": total}, **extra}


def test_profile_ranks_the_real_costs_and_judges_p95(tmp_path: Path) -> None:
    write_session(tmp_path, "s1", [frame(734), frame(979, 300.0), frame(700)])
    write_session(tmp_path, "s2", [frame(650, capture_ms={"grab_window": 150.0, "to_rgb": 10.0},
                                         capture_source="window")])
    report = runtime_profile([tmp_path])
    assert report["frames"] == 4 and report["verdict"] == "FAIL"
    assert [item["stage"] for item in report["top3"]] == ["capture", "entities", "grid"]
    assert report["capture_substeps_ms"]["grab_window"]["median"] == 150.0
    assert report["capture_sources"] == {"non journalisée": 3, "window": 1}
    assert set(report["per_session_total_ms"]) == {"s1", "s2"}
    assert "Top 3" in markdown_report(report)


def test_no_session_is_not_evaluable(tmp_path: Path, capsys) -> None:
    assert runtime_profile([tmp_path])["verdict"] == "NOT_EVALUABLE"
    assert main(["--runtime-profile", "--data-dir", str(tmp_path), "--output-dir", str(tmp_path / "out")]) == 0
    assert json.loads((tmp_path / "out" / "runtime-profile.json").read_text(encoding="utf-8"))["frames"] == 0
    assert "non journalisées" in capsys.readouterr().out


def test_frame_record_keeps_capture_substeps() -> None:
    record = frame_record({"stage_ms": {"total": 5}, "capture_ms": {"grab_window": 3.0}, "capture_source": "desktop"},
                          None, wall_time=1.0)
    assert record["capture_ms"] == {"grab_window": 3.0} and record["capture_source"] == "desktop"
