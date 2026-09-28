"""Profil de latence réel — ``python -m combatbot.benchmark --runtime-profile``.

Lit les journaux de sessions réelles (``<données>/logs/sessions/*/frames.jsonl``, écrits par 3B-7) et
produit, sans rien rejouer : statistiques par étape (capture, map, grid, entities, hud, combat_state,
overlay, total), top 3 des coûts médians, sous-étapes de capture quand elles sont journalisées, source
de capture (fenêtre / bureau) et verdict p95 ≤ 400 ms. Aucune session → NOT_EVALUABLE, jamais PASS.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from combatbot.vision.session_telemetry import STAGES, _stats

P95_CRITERION_MS = 400.0


def session_journals(data_dirs: list[Path]) -> dict[str, Path]:
    """session → journal (premier dossier de données qui la contient)."""
    journals: dict[str, Path] = {}
    for root in data_dirs:
        for journal in sorted(Path(root).glob("logs/sessions/*/frames.jsonl")):
            journals.setdefault(journal.parent.name, journal)
    return journals


def load_frames(data_dirs: list[Path], sessions: set[str] | None = None) -> list[tuple[str, dict]]:
    frames, seen = [], set()
    for root in data_dirs:
        for journal in sorted(Path(root).glob("logs/sessions/*/frames.jsonl")):
            session = journal.parent.name
            if session in seen or (sessions is not None and session not in sessions):
                continue
            seen.add(session)
            try:
                lines = journal.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    frames.append((session, record))
    return frames


def _stage_values(frames: list[tuple[str, dict]], key: str, stage: str) -> list[float]:
    values = []
    for _session, record in frames:
        value = (record.get(key) or {}).get(stage)
        if isinstance(value, (int, float)):
            values.append(float(value))
    return values


def runtime_profile(data_dirs: list[Path], sessions: set[str] | None = None) -> dict[str, object]:
    frames = load_frames(data_dirs, sessions)
    stages = {stage: _stats(_stage_values(frames, "stage_ms", stage)) for stage in STAGES}
    def substeps(key: str) -> dict[str, dict]:
        names = sorted({name for _s, record in frames for name in (record.get(key) or {})})
        return {name: _stats(_stage_values(frames, key, name)) for name in names}

    capture = substeps("capture_ms")
    ranked = sorted((stage for stage in STAGES if stage != "total" and stages[stage]["median"] is not None),
                    key=lambda stage: stages[stage]["median"], reverse=True)
    total = stages["total"]
    if total["n"] == 0:
        verdict = "NOT_EVALUABLE"
    else:
        verdict = "PASS" if total["p95"] <= P95_CRITERION_MS else "FAIL"
    sessions = Counter(session for session, _record in frames)
    per_session = {session: _stats([float((record.get("stage_ms") or {}).get("total"))
                                    for name, record in frames if name == session
                                    and isinstance((record.get("stage_ms") or {}).get("total"), (int, float))])
                   for session in sessions}
    return {"schema_version": 1, "report": "runtime-profile",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "data_dirs": [str(path) for path in data_dirs], "frames": len(frames), "sessions": dict(sessions),
            "stages_ms": stages, "top3": [{"stage": stage, "median_ms": stages[stage]["median"],
                                           "p95_ms": stages[stage]["p95"]} for stage in ranked[:3]],
            "capture_substeps_ms": capture, "grid_substeps_ms": substeps("grid_ms"),
            "entities_substeps_ms": substeps("entities_ms"),
            "capture_sources": dict(Counter(record.get("capture_source") or "non journalisée" for _s, record in frames)),
            "per_session_total_ms": per_session, "criterion": {"p95_total_ms": P95_CRITERION_MS},
            "verdict": verdict, "actions": "NONE"}


def markdown_report(report: dict) -> str:
    lines = ["# Profil de latence réel", "",
             f"{report['frames']} frame(s), {len(report['sessions'])} session(s) · critère p95 total ≤ "
             f"{report['criterion']['p95_total_ms']:.0f} ms · verdict **{report['verdict']}**", "",
             "| Étape | n | médiane | moyenne | p95 | max |", "|---|---|---|---|---|---|"]
    for stage, stats in report["stages_ms"].items():
        lines.append(f"| {stage} | {stats['n']} | {stats['median']} | {stats['mean']} | {stats['p95']} | {stats['max']} |")
    lines += ["", "## Top 3 des coûts (médiane)", ""]
    lines += [f"{index}. {item['stage']} — médiane {item['median_ms']} ms, p95 {item['p95_ms']} ms"
              for index, item in enumerate(report["top3"], 1)] or ["- aucune mesure"]
    lines += ["", "## Capture : sous-étapes", ""]
    if report["capture_substeps_ms"]:
        lines += ["| Sous-étape | n | médiane | p95 |", "|---|---|---|---|"]
        lines += [f"| {name} | {stats['n']} | {stats['median']} | {stats['p95']} |"
                  for name, stats in report["capture_substeps_ms"].items()]
    else:
        lines.append("- non journalisées (sessions antérieures à ce lot) : refaire une session pour les mesurer")
    for key, title in (("grid_substeps_ms", "Grille"), ("entities_substeps_ms", "Entités")):
        if report.get(key):
            lines += ["", f"## {title} : sous-étapes", "", "| Sous-étape | n | médiane | p95 |", "|---|---|---|---|"]
            lines += [f"| {name} | {stats['n']} | {stats['median']} | {stats['p95']} |"
                      for name, stats in report[key].items()]
    lines += ["", f"Sources de capture : {json.dumps(report['capture_sources'], ensure_ascii=False)}"]
    return "\n".join(lines) + "\n"


def write_report(report: dict, output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path, md_path = output / "runtime-profile.json", output / "runtime-profile.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, md_path
