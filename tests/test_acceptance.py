"""LOT 3B-7 : recette consolidée (verdicts par domaine, fraîcheur, sessions réelles)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from combatbot.corpus import acceptance
from combatbot.corpus.acceptance import (
    FAIL, INCOMPLETE, NOT_EVALUABLE, PARTIAL, PASS, STALE, evaluate_combat_state, evaluate_entities, evaluate_grid,
    evaluate_hud, evaluate_latency, evaluate_map, run_acceptance,
)
from combatbot.vision.session_telemetry import SessionTelemetry


def _turn(claimed=0, precision=1.0):
    return {"precision": precision, "coverage": 0.9, "dangerous_claimed_my_turn": claimed, "dangerous_missed_my_turn": 0}


def test_grid_verdicts() -> None:
    good = {"available": True, "frames": 26, "combat_false_positive_rate_after": 0.0, "combat_recall_after": 1.0,
            "alignment": {"status": {"ALIGNED": 12}, "residual_median_px": {"median": 1.2}}}
    assert evaluate_grid(good).status == PASS
    assert evaluate_grid({**good, "combat_false_positive_rate_after": 0.1}).status == FAIL
    assert evaluate_grid({**good, "alignment": {"status": {"ALIGNED": 5, "DEGRADED": 5}}}).status == PARTIAL
    assert evaluate_grid({"available": False}).status == NOT_EVALUABLE


def test_hud_verdicts() -> None:
    report = {"status": "PASS", "one_seven": {"verdict": "VALIDATED", "test_confusions_1_7": 0},
              "splits": {"test": {"examples": 26, "accepted_accuracy": 1.0, "coverage": 0.92}}}
    assert evaluate_hud(report).status == PASS
    assert evaluate_hud({**report, "one_seven": {"test_confusions_1_7": 1}}).status == FAIL
    assert evaluate_hud({**report, "splits": {"test": {"accepted_accuracy": 0.96}}}).status == FAIL
    assert evaluate_hud({**report, "status": "INSUFFICIENT"}).status == NOT_EVALUABLE


def test_entity_verdicts_prefer_test_and_downgrade_validation() -> None:
    passing = {"PLAYER DETECTION": PASS, "ENEMY DETECTION": PASS, "GLOBAL TRACKING": PASS, "CELL OCCUPANCY": PASS}
    assert evaluate_entities({"results": [{"split": "validation", **passing},
                                          {"split": "test", **passing}]}).status == PASS
    only_validation = evaluate_entities({"results": [{"split": "validation", **passing}]})
    assert only_validation.status == PARTIAL and "pas une mesure TEST" in only_validation.notes[-1]
    assert evaluate_entities({"results": [{"split": "test", **passing, "ENEMY DETECTION": FAIL}]}).status == FAIL
    assert evaluate_entities({"results": [{"split": "test", "status": "NO_DATA"}]}).status == NOT_EVALUABLE


def test_combat_state_verdicts() -> None:
    test = {"phase": {"precision": 0.974, "coverage": 0.9}, "turn": _turn()}
    assert evaluate_combat_state(test).status == PASS
    assert evaluate_combat_state({**test, "turn": _turn(claimed=1)}).status == FAIL
    assert evaluate_combat_state({"validation": test}).status == PARTIAL      # pas une mesure finale
    assert evaluate_combat_state({"train_frames": 0}).status == NOT_EVALUABLE


def test_map_verdicts() -> None:
    assert evaluate_map({"frames_with_truth": 472, "wrong_maps": 0, "coverage": 0.75,
                         "outcomes": {"correct": 356}}).status == PASS
    empty = evaluate_map({"frames_with_truth": 57, "wrong_maps": 0, "coverage": 0.0, "outcomes": {"unknown": 75}})
    assert empty.status == NOT_EVALUABLE                     # 0 erreur parce que 0 réponse
    assert evaluate_map({"frames_with_truth": 472, "wrong_maps": 1}).status == FAIL
    assert evaluate_map({"frames_with_truth": 0}).status == NOT_EVALUABLE


def _session(root: Path, name: str, runtime: str, totals: list[float]) -> None:
    telemetry = SessionTelemetry(root / "logs" / "sessions", name, {"dpi": 96, "layout_digest": "b2b3"})
    telemetry.context["runtime"] = runtime
    for index, total in enumerate(totals):
        telemetry.record({"frame_index": index, "client_size": [2560, 1377], "stage_ms": {"total": total},
                          "map_resolution": {"status": "RESOLVED", "map_id": 1}}, None)
    telemetry.close()


def test_latency_needs_the_packaged_executable(tmp_path: Path) -> None:
    _session(tmp_path, "s1", "sources", [100.0] * 20)
    sources_only = evaluate_latency(acceptance.load_sessions([tmp_path]))
    assert sources_only["status"] == PARTIAL
    _session(tmp_path, "s2", "exécutable", [120.0] * 19 + [900.0])
    exe = evaluate_latency(acceptance.load_sessions([tmp_path]))
    assert exe["status"] == PASS and exe["metrics"]["exécutable"]["frames"] == 20
    _session(tmp_path, "s3", "exécutable", [600.0] * 20)
    assert evaluate_latency(acceptance.load_sessions([tmp_path]))["status"] == FAIL
    assert evaluate_latency([])["status"] == NOT_EVALUABLE


def test_stale_report_is_never_a_pass(tmp_path: Path, monkeypatch) -> None:
    report = tmp_path / "benchmarks" / "map-resolution.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"frames_with_truth": 10, "wrong_maps": 0, "outcomes": {"correct": 10}}),
                      encoding="utf-8")
    os.utime(report, (1_000_000, 1_000_000))
    monkeypatch.setattr(acceptance, "last_source_change", lambda paths, repo=None: 2_000_000.0)
    domain = next(item for item in acceptance.DOMAINS if item.key == "map")
    result = acceptance.evaluate_domain(domain, [tmp_path])
    assert result["status"] == STALE and result["raw_status"] == PASS
    monkeypatch.setattr(acceptance, "last_source_change", lambda paths, repo=None: 500_000.0)
    assert acceptance.evaluate_domain(domain, [tmp_path])["status"] == PASS


def test_run_acceptance_writes_report_and_overall(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(acceptance, "last_source_change", lambda paths, repo=None: None)
    _session(tmp_path, "s1", "exécutable", [150.0] * 10)
    report = run_acceptance([tmp_path], check_freshness=False)
    statuses = {item["key"]: item["status"] for item in report["domains"]}
    assert statuses["latency"] == PASS and statuses["map"] == NOT_EVALUABLE
    assert report["overall"] == INCOMPLETE and report["actions"] == "NONE"
    json_path, md_path = acceptance.write_acceptance(report, tmp_path / "out")
    assert json.loads(json_path.read_text(encoding="utf-8"))["lot"] == "3B-7"
    text = md_path.read_text(encoding="utf-8")
    assert "Pour mesurer" in text and "mode tactique : non détecté" in text
