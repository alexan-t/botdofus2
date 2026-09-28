"""LOT 3B-7 : recette réelle d'observation — rapport chiffré consolidé.

Ne relance aucun banc (certains durent des heures ou écrivent des gabarits) : lit les rapports
produits par chaque banc et les journaux de session réelle, puis rend un verdict par domaine.

- Un rapport plus ancien que la dernière modification (git) du code qu'il mesure est PÉRIMÉ : il
  n'est jamais compté comme PASS.
- Un domaine sans rapport est NON ÉVALUABLE, avec la commande qui le produit.
- Les critères viennent des lots d'origine ; ceux ajoutés en 3B-7 sont marqués « proposé 3B-7 ».
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from combatbot.runtime import PROJECT_ROOT

PASS, PARTIAL, FAIL, NOT_EVALUABLE, STALE, INFO = "PASS", "PARTIAL", "FAIL", "NOT_EVALUABLE", "STALE", "INFO"
INCOMPLETE = "INCOMPLET"          # verdict global : au moins un domaine non mesuré ou périmé
LATENCY_P95_MAX_MS = 400.0        # proposé 3B-7 : une analyse tient dans un tick du minuteur (400 ms)
COMBAT_PHASE_PRECISION_MIN = 0.97  # proposé 3B-7 : niveau mesuré en TEST 3B-6B (0,974)
COMBAT_TURN_PRECISION_MIN = 0.98   # proposé 3B-7 : niveau mesuré en TRAIN/VALIDATION/TEST 3B-6B


@dataclass
class Verdict:
    status: str
    metrics: dict[str, object] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Domain:
    key: str
    title: str
    criterion: str
    patterns: tuple[str, ...]          # relatifs à <data>/ ; le plus récent l'emporte
    sources: tuple[str, ...]           # code mesuré (fraîcheur)
    command: str
    evaluate: Callable[[dict], Verdict]


# ------------------------------------------------------------------------------------ verdicts
def evaluate_grid(report: dict) -> Verdict:
    if not report.get("available") or not report.get("frames"):
        return Verdict(NOT_EVALUABLE, notes=["corpus lot3b2r absent"])
    fp, recall = report.get("combat_false_positive_rate_after"), report.get("combat_recall_after")
    alignment = (report.get("alignment") or {}).get("status") or {}
    aligned = sum(alignment.values()) and alignment.get("ALIGNED", 0) / sum(alignment.values())
    metrics = {"frames": report["frames"], "combat_false_positive_rate": fp, "combat_recall": recall,
               "aligned_rate": aligned,
               "residual_median_px": ((report.get("alignment") or {}).get("residual_median_px") or {}).get("median")}
    if fp is None or recall is None:
        return Verdict(NOT_EVALUABLE, metrics)
    if fp > 0:
        return Verdict(FAIL, metrics, ["faux combat détecté en exploration"])
    return Verdict(PASS if recall >= 0.95 and (aligned or 0) >= 0.95 else PARTIAL, metrics)


def evaluate_hud(report: dict) -> Verdict:
    one_seven = report.get("one_seven") or {}
    test = (report.get("splits") or {}).get("test") or {}
    metrics = {"status_3b4r2": report.get("status"), "one_seven": one_seven.get("verdict"),
               "test_examples": test.get("examples"), "test_accepted_accuracy": test.get("accepted_accuracy"),
               "test_coverage": test.get("coverage"), "test_confusions_1_7": one_seven.get("test_confusions_1_7")}
    if (one_seven.get("test_confusions_1_7") or 0) > 0 or (test.get("accepted_accuracy") not in (None, 1.0)):
        return Verdict(FAIL, metrics, ["erreur acceptée en TEST"])
    status = {"PASS": PASS, "PARTIAL": PARTIAL}.get(str(report.get("status")), NOT_EVALUABLE)
    return Verdict(status, metrics, [str(item) for item in one_seven.get("missing") or []])


def evaluate_entities(report: dict) -> Verdict:
    if "results" in report:        # fichier verdicts-<splits>.json : {"run": …, "results": [par split]}
        results = {item.get("split"): item for item in report.get("results") or []}
        chosen = next((results[split] for split in ("test", "validation", "train") if split in results), None)
        if chosen is None:
            return Verdict(NOT_EVALUABLE, notes=["aucun split mesuré"])
        verdict = evaluate_entities(chosen)
        if chosen.get("split") != "test":
            verdict.notes.append(f"split {chosen.get('split')} : pas une mesure TEST indépendante")
            if verdict.status == PASS:
                verdict.status = PARTIAL
        return verdict
    keys = ("PLAYER DETECTION", "ENEMY DETECTION", "GLOBAL TRACKING", "CELL OCCUPANCY")
    values = {key: report.get(key) for key in keys}
    metrics = {"split": report.get("split"), **values, "OCCLUSION HANDLING": report.get("OCCLUSION HANDLING")}
    if report.get("status") == "NO_DATA" or all(value in (None, NOT_EVALUABLE) for value in values.values()):
        return Verdict(NOT_EVALUABLE, metrics)
    if FAIL in values.values():
        return Verdict(FAIL, metrics)
    return Verdict(PASS if all(value == PASS for value in values.values()) else PARTIAL, metrics)


def _combat_block(report: dict) -> tuple[str, dict] | None:
    if "phase" in report and "turn" in report:
        return "rapport", report
    # Rapport TEST figé (3B-6B, « combat-state-TEST-<sha>.json ») : mesure sous « result ».
    if isinstance(report.get("result"), dict) and "phase" in report["result"] and report.get("test_frames"):
        return "test", report["result"]
    for scope in ("test", "validation", "train_loco_with_tracker"):
        block = report.get(scope)
        if isinstance(block, dict) and "phase" in block:
            return scope, block
    return None


def evaluate_combat_state(report: dict) -> Verdict:
    found = _combat_block(report)
    if found is None:
        return Verdict(NOT_EVALUABLE, notes=["aucune vérité phase/tour mesurée"])
    scope, block = found
    phase, turn = block["phase"], block["turn"]
    metrics = {"scope": scope, "phase_precision": phase.get("precision"), "phase_coverage": phase.get("coverage"),
               "turn_precision": turn.get("precision"), "turn_coverage": turn.get("coverage"),
               "claimed_my_turn_wrongly": turn.get("dangerous_claimed_my_turn"),
               "missed_my_turn": turn.get("dangerous_missed_my_turn")}
    if (turn.get("dangerous_claimed_my_turn") or 0) > 0:
        return Verdict(FAIL, metrics, ["« mon tour » affirmé à tort"])
    ok = (phase.get("precision") or 0) >= COMBAT_PHASE_PRECISION_MIN and \
        (turn.get("precision") or 0) >= COMBAT_TURN_PRECISION_MIN
    notes = [] if scope in ("test", "rapport") else [f"mesure {scope} : pas une mesure indépendante finale"]
    return Verdict(PASS if ok and not notes else PARTIAL, metrics, notes)


def evaluate_map(report: dict) -> Verdict:
    metrics = {"frames_with_truth": report.get("frames_with_truth"), "wrong_maps": report.get("wrong_maps"),
               "accepted_precision": report.get("accepted_precision"), "coverage": report.get("coverage"),
               "coordinate_reader_ms_median": report.get("coordinate_reader_ms_median")}
    metrics["ocr_inputs"] = report.get("ocr_inputs")
    if not report.get("frames_with_truth"):
        return Verdict(NOT_EVALUABLE, metrics)
    if report.get("wrong_maps"):
        return Verdict(FAIL, metrics, ["mauvaise map acceptée"])
    if not (report.get("outcomes") or {}).get("correct"):
        # 0 erreur parce que 0 réponse : mesure vide, pas un succès.
        return Verdict(NOT_EVALUABLE, metrics, ["aucune map résolue : rien n'a été mesuré (lectures refusées)"])
    return Verdict(PASS, metrics, ["couverture informative : AMBIGUOUS/UNKNOWN sont des refus volontaires"])


DOMAINS: tuple[Domain, ...] = (
    Domain("grid", "Grille : visibilité, combat, alignement (3B-3)",
           "0 faux combat en exploration ; rappel combat ≥ 0,95 ; ≥ 95 % des frames visibles ALIGNED",
           ("benchmarks/grid-validation.json",),
           ("combatbot/vision/grid_validation.py", "combatbot/vision/gamedata_grid.py",
            "combatbot/vision/grid_projection.py", "combatbot/corpus/grid_validation_benchmark.py"),
           "python -m combatbot.benchmark --grid-validation", evaluate_grid),
    Domain("hud", "PA/PM (3B-4R2)",
           "0 erreur acceptée en TEST, 0 confusion 1↔7 ; statut 3B-4R2 PASS (1 et 7 dans ≥ 2 groupes TRAIN et TEST)",
           ("benchmarks/hud-reader.json",),
           ("combatbot/vision/hud_reader.py", "combatbot/corpus/hud_dataset.py"),
           "python -m combatbot.benchmark --hud-reader --hud-rapidocr", evaluate_hud),
    Domain("entities", "Entités et suivi (3B-5D)",
           "joueur précision 1,0 couverture 0,90 ; ennemis précision 0,98 rappel 0,90 ; suivi ≤ 5 % ; FREE 0,98",
           ("validation/lot3b5d/benchmarks/verdicts-*test*.json", "validation/lot3b5d/benchmarks/verdicts-*.json"),
           ("combatbot/vision/entity_detector.py", "combatbot/vision/entity_tracker.py",
            "combatbot/vision/entity_profiles.py", "combatbot/corpus/entity_benchmark.py"),
           "python -m combatbot.benchmark --entities-3b5d [--entity-splits test --freeze-sha SHA]", evaluate_entities),
    Domain("combat_state", "Phase et tour (3B-6B)",
           "0 « mon tour » affirmé à tort ; précision phase ≥ 0,97 et tour ≥ 0,98 (proposé 3B-7) sur TEST",
           ("benchmarks/combat-state-TEST-*.json", "benchmarks/combat-state.json"),
           ("combatbot/vision/combat_state_detector.py", "combatbot/corpus/combat_state_metrics.py"),
           "python -m combatbot.benchmark --combat-state", evaluate_combat_state),
    Domain("map", "Résolution de map (3B-6C)", "0 mauvaise map acceptée (couverture informative)",
           ("benchmarks/map-resolution.json",),
           ("combatbot/vision/map_resolver.py", "combatbot/vision/map_reader.py", "combatbot/gamedata/map_index.py"),
           "python -m combatbot.benchmark --map-resolution", evaluate_map),
)


# ------------------------------------------------------------------------------------ fraîcheur
def last_source_change(paths: tuple[str, ...], repo: Path = PROJECT_ROOT) -> float | None:
    """Horodatage du dernier commit touchant ces fichiers, ou mtime s'ils ont des modifications locales."""
    try:
        result = subprocess.run(["git", "log", "-1", "--format=%ct", "--", *paths], cwd=repo,
                                capture_output=True, text=True, timeout=20)
        committed = float(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else None
        dirty = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=repo,
                               capture_output=True, text=True, timeout=20)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if dirty.returncode == 0 and dirty.stdout.strip():
        mtimes = [(repo / path).stat().st_mtime for path in paths if (repo / path).exists()]
        return max([committed or 0.0, *mtimes])
    return committed


def find_report(data_dirs: list[Path], patterns: tuple[str, ...]) -> Path | None:
    for pattern in patterns:                     # l'ordre des motifs exprime la préférence (TEST d'abord)
        found = [path for root in data_dirs for path in Path(root).glob(pattern) if path.is_file()]
        if found:
            return max(found, key=lambda path: path.stat().st_mtime)
    return None


def _iso(timestamp: float | None) -> str | None:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds") if timestamp else None


def evaluate_domain(domain: Domain, data_dirs: list[Path], *, check_freshness: bool = True) -> dict[str, object]:
    path = find_report(data_dirs, domain.patterns)
    base = {"key": domain.key, "title": domain.title, "criterion": domain.criterion, "command": domain.command}
    if path is None:
        return {**base, "status": NOT_EVALUABLE, "report": None, "metrics": {},
                "notes": ["aucun rapport : lancer la commande indiquée"]}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {**base, "status": NOT_EVALUABLE, "report": str(path), "metrics": {}, "notes": [f"illisible : {exc}"]}
    verdict = domain.evaluate(report)
    produced = path.stat().st_mtime
    changed = last_source_change(domain.sources) if check_freshness else None
    status, notes = verdict.status, list(verdict.notes)
    if changed is not None and produced < changed:
        notes.insert(0, f"rapport du {_iso(produced)} antérieur au code mesuré ({_iso(changed)}) : "
                        f"verdict brut {verdict.status}, à re-mesurer")
        status = STALE
    elif changed is None and check_freshness:
        notes.append("fraîcheur non vérifiable (git indisponible)")
    return {**base, "status": status, "raw_status": verdict.status, "report": str(path),
            "report_date": _iso(produced), "code_date": _iso(changed), "metrics": verdict.metrics, "notes": notes}


# ------------------------------------------------------------------------------------ sessions réelles
def _percentile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, round(share * len(ordered)) - 1))], 2)


def load_sessions(data_dirs: list[Path]) -> list[dict]:
    sessions, seen = [], set()
    for root in data_dirs:
        for summary in sorted(Path(root).glob("logs/sessions/*/summary.json")):
            if summary.parent.name in seen:
                continue
            try:
                report = json.loads(summary.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            seen.add(summary.parent.name)
            totals = []
            frames = summary.parent / "frames.jsonl"
            try:
                for line in frames.read_text(encoding="utf-8").splitlines():
                    total = (json.loads(line).get("stage_ms") or {}).get("total")
                    if total is not None:
                        totals.append(float(total))
            except (OSError, ValueError):
                pass
            sessions.append({"summary": report, "totals": totals, "path": str(summary.parent)})
    return sessions


def evaluate_latency(sessions: list[dict]) -> dict[str, object]:
    base = {"key": "latency", "title": "Latence en session réelle (3B-0, 3B-7)",
            "criterion": f"p95 de l'analyse par frame ≤ {LATENCY_P95_MAX_MS:.0f} ms sur l'exécutable Windows "
                         "(proposé 3B-7 : une analyse par tick du minuteur)",
            "command": "Vision réelle dans DofBot2.exe, puis « Arrêter l'observation » (journal data/logs/sessions)"}
    if not sessions:
        return {**base, "status": NOT_EVALUABLE, "metrics": {}, "notes": ["aucune session réelle journalisée"]}
    by_runtime: dict[str, list[float]] = {}
    for session in sessions:
        runtime = session["summary"]["context"].get("runtime", "?")
        by_runtime.setdefault(runtime, []).extend(session["totals"])
    metrics = {runtime: {"frames": len(values), "median_ms": _percentile(values, 0.5),
                         "p95_ms": _percentile(values, 0.95), "max_ms": max(values) if values else None}
               for runtime, values in by_runtime.items()}
    stages: dict[str, list[float]] = {}
    for session in sessions:
        for stage, stats in (session["summary"].get("latency_ms") or {}).items():
            if stats.get("median") is not None:
                stages.setdefault(stage, []).append(float(stats["median"]))
    metrics["stage_median_ms_by_session"] = {stage: values for stage, values in stages.items()}
    exe = metrics.get("exécutable")
    if not exe or not exe["frames"]:
        return {**base, "status": PARTIAL, "metrics": metrics,
                "notes": ["mesuré depuis les sources seulement : refaire avec DofBot2.exe"]}
    status = PASS if exe["p95_ms"] is not None and exe["p95_ms"] <= LATENCY_P95_MAX_MS else FAIL
    return {**base, "status": status, "metrics": metrics, "notes": []}


def configuration_coverage(sessions: list[dict]) -> dict[str, object]:
    sizes, dpis, layouts, runtimes, maps = set(), set(), set(), set(), set()
    frames = 0
    for session in sessions:
        summary = session["summary"]
        context = summary.get("context") or {}
        sizes.update(summary.get("client_sizes") or {})
        dpis.add(str(context.get("dpi")))
        layouts.add(str(context.get("layout_digest")))
        runtimes.add(str(context.get("runtime")))
        maps.update(summary.get("distinct_maps") or [])
        frames += int(summary.get("frames") or 0)
    return {"key": "configurations", "title": "Configurations observées", "status": INFO,
            "metrics": {"sessions": len(sessions), "frames": frames, "client_sizes": sorted(sizes),
                        "dpi": sorted(dpis), "layouts": sorted(layouts), "runtimes": sorted(runtimes),
                        "distinct_maps": len(maps)},
            "notes": ["mode tactique : non détecté par la vision (toujours « inconnu »)",
                      "thème DOFUS : non détecté ni enregistré",
                      "aucune session réelle journalisée" if not sizes else
                      "une seule taille de client observée : résultats valables pour ce layout uniquement"
                      if len(sizes) == 1 else f"{len(sizes)} tailles de client observées"]}


# ------------------------------------------------------------------------------------ rapport
def default_data_dirs() -> list[Path]:
    from combatbot.runtime import app_data_root
    candidates = [app_data_root() / "data", PROJECT_ROOT / "data"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "PythonBot" / "data")
    unique: list[Path] = []
    for path in candidates:
        if path.exists() and path.resolve() not in {item.resolve() for item in unique}:
            unique.append(path)
    return unique


def run_acceptance(data_dirs: list[Path] | None = None, *, check_freshness: bool = True) -> dict[str, object]:
    dirs = list(data_dirs) if data_dirs else default_data_dirs()
    domains = [evaluate_domain(domain, dirs, check_freshness=check_freshness) for domain in DOMAINS]
    sessions = load_sessions(dirs)
    domains.append(evaluate_latency(sessions))
    domains.append(configuration_coverage(sessions))
    judged = [item["status"] for item in domains if item["status"] != INFO]
    if FAIL in judged:
        overall = FAIL
    elif any(status in (NOT_EVALUABLE, STALE) for status in judged):
        overall = INCOMPLETE
    elif judged and all(status == PASS for status in judged):
        overall = PASS
    else:
        overall = PARTIAL
    return {"schema_version": 1, "lot": "3B-7", "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "data_dirs": [str(path) for path in dirs], "overall": overall, "domains": domains,
            "counts": {status: judged.count(status) for status in sorted(set(judged))}, "actions": "NONE"}


def markdown_report(report: dict) -> str:
    lines = ["# Recette réelle d'observation (LOT 3B-7)", "",
             f"Généré le {report['generated_at']} · verdict global **{report['overall']}** · ACTIONS : NONE", "",
             "| Domaine | Verdict | Critère |", "|---|---|---|"]
    for item in report["domains"]:
        lines.append(f"| {item['title']} | **{item['status']}** | {item.get('criterion', '—')} |")
    for item in report["domains"]:
        lines += ["", f"## {item['title']} — {item['status']}"]
        if item.get("report"):
            lines.append(f"- Rapport : `{item['report']}` ({item.get('report_date')})")
        for key, value in (item.get("metrics") or {}).items():
            lines.append(f"- {key} : {json.dumps(value, ensure_ascii=False)}")
        for note in item.get("notes") or []:
            lines.append(f"- ⚠ {note}")
        if item["status"] in (NOT_EVALUABLE, STALE) and item.get("command"):
            lines.append(f"- Pour mesurer : `{item['command']}`")
    return "\n".join(lines) + "\n"


def write_acceptance(report: dict, output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path, md_path = output / "acceptance-3b7.json", output / "acceptance-3b7.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, md_path
