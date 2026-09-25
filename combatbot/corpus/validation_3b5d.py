"""LOT 3B-5D : critères fixés AVANT le TEST, verdicts et garde « TEST mesuré une seule fois ».

Les seuils ci-dessous sont ceux de la consigne 3B-5D ; ils ne doivent pas être ajustés après
lecture d'un résultat TEST. Le TEST exige un SHA de gel : aucun fichier ``combatbot/`` ne doit
différer de ce commit, et une seconde mesure des mêmes combats TEST est refusée (sauf mesure
explicitement « diagnostic », qui ne vaut plus TEST indépendant).
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess

from combatbot.corpus.repository import CorpusRepository

CRITERIA = {
    "player_accepted_precision": 1.0, "player_coverage": 0.90,
    "enemy_precision": 0.98, "enemy_recall": 0.90,
    "tracking_switch_rate": 0.05, "tracking_false_reassociation_rate": 0.05,
    "free_precision": 0.98, "false_free_on_entities": 0, "min_empty_truth": 50,
}
TEST_LEDGER = "entity_test_runs.json"


def player_verdict(values: dict) -> str:
    accepted = values["correct"] + values["wrong"] + values["false_positive"]
    if not values["frames_visible"]:
        return "NOT_EVALUABLE"
    if values["wrong"] or values["false_positive"]:
        return "FAIL"
    coverage = values["detection_recall"] or 0.0
    if accepted and coverage >= CRITERIA["player_coverage"]:
        return "PASS"
    return "PARTIAL"


def enemy_verdict(values: dict) -> str:
    if not values["truth"]:
        return "NOT_EVALUABLE"
    precision, recall = values["precision"], values["recall"] or 0.0
    if precision is None or precision < CRITERIA["enemy_precision"]:
        return "FAIL"
    return "PASS" if recall >= CRITERIA["enemy_recall"] else "PARTIAL"


def tracking_verdict(values: dict) -> str:
    if values.get("status") != "MEASURED":
        return "NOT_EVALUABLE"
    if (values["switch_rate"] <= CRITERIA["tracking_switch_rate"]
            and values["false_reassociation_rate"] <= CRITERIA["tracking_false_reassociation_rate"]):
        return "PASS"
    return "PARTIAL"


def occupancy_verdict(values: dict) -> str:
    if values["false_free_on_player_or_enemy"] > CRITERIA["false_free_on_entities"]:
        return "FAIL"
    if values["empty_truth"] < CRITERIA["min_empty_truth"] or values["free_precision"] is None:
        return "PARTIAL"
    return "PASS" if values["free_precision"] >= CRITERIA["free_precision"] else "FAIL"


def verdicts(report: dict, split: str) -> dict:
    after = report["after"].get(split)
    tracking = report["tracking_verified"].get(split, {}).get("global", {})
    if after is None:
        return {"split": split, "status": "NO_DATA"}
    return {
        "split": split, "criteria": CRITERIA,
        "PLAYER DETECTION": player_verdict(after["player"]),
        "ENEMY DETECTION": enemy_verdict(after["enemies"]),
        "GLOBAL TRACKING": tracking_verdict(tracking),
        "CELL OCCUPANCY": occupancy_verdict(after["occupancy_truth"]),
        "OCCLUSION HANDLING": ("NOT OBSERVED" if not tracking.get("explicit_occlusion_annotations")
                               else "MEASURED"),
    }


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def check_freeze(freeze_sha: str, project_root: Path) -> str:
    """SHA complet si le code ``combatbot/`` courant est exactement celui du gel ; sinon erreur."""
    resolved = _git("rev-parse", "--verify", f"{freeze_sha}^{{commit}}", cwd=project_root)
    if resolved.returncode:
        raise ValueError(f"SHA de gel inconnu : {freeze_sha}")
    full = resolved.stdout.strip()
    if _git("merge-base", "--is-ancestor", full, "HEAD", cwd=project_root).returncode:
        raise ValueError("Le SHA de gel n'est pas un ancêtre de HEAD")
    if _git("diff", "--quiet", full, "--", "combatbot", cwd=project_root).returncode:
        raise ValueError("Le code combatbot/ diffère du gel : TEST refusé (aucun réglage après gel)")
    return full


def guard_test_run(repository: CorpusRepository, test_groups: list[str], freeze_sha: str,
                   *, diagnostic_rerun: bool = False) -> dict:
    """Enregistre la mesure TEST ; refuse une seconde mesure des mêmes combats (sauf diagnostic)."""
    path = repository.manifests / TEST_LEDGER
    ledger = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"schema_version": 1, "runs": []}
    measured = {group for run in ledger["runs"] if run["kind"] == "independent_test" for group in run["groups"]}
    reused = sorted(set(test_groups) & measured)
    if reused and not diagnostic_rerun:
        raise ValueError(f"Combats TEST déjà mesurés une fois : {reused}. Collecter de nouveaux combats TEST "
                         "(ou --diagnostic-rerun, qui ne vaut plus TEST indépendant).")
    run = {"run_at": datetime.now().astimezone().isoformat(timespec="seconds"), "freeze_sha": freeze_sha,
           "groups": sorted(test_groups), "kind": "diagnostic" if reused else "independent_test"}
    ledger["runs"].append(run)
    repository.manifests.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return run


def markdown_3b5d(report: dict, results: list[dict], run: dict | None) -> str:
    lines = ["# Validation entités 3B-5D", "",
             f"- Frames : {report['frames']} ; splits (frames) `{report['splits']}` ; combats `{report['groups']}`",
             f"- Mesure TEST : `{run}`" if run else "- Mesure hors TEST", ""]
    for result in results:
        split = result["split"]
        if result.get("status") == "NO_DATA":
            lines += [f"## {split.upper()} : aucune donnée", ""]
            continue
        after = report["after"][split]
        tracking = report["tracking_verified"][split]["global"]
        lines += [f"## {split.upper()}", "", "```text",
                  *(f"{key}: {value}" for key, value in result.items() if key not in ("split", "criteria")),
                  "```", "", f"- Joueur : `{after['player']}`", f"- Ennemis : `{after['enemies']}`",
                  f"- Suivi (identités confirmées) : `{ {k: v for k, v in tracking.items() if k != 'definitions'} }`",
                  f"- Occupation (vérités humaines) : `{after['occupancy_truth']}`", ""]
    lines += ["## Performance (ms)", "", f"`{report['performance_ms']}`", ""]
    return "\n".join(lines)
