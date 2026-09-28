"""FAST-3B7 : banc de bout en bout de l'observation, ``python -m combatbot.benchmark --observation-e2e``.

Rejoue uniquement les données enregistrées (prédiction sauvée avec chaque frame du corpus + vérités
humaines) : aucun détecteur n'est relancé, DOFUS n'est jamais nécessaire, rien n'est écrit dans le
corpus. Les frames TEST sont mesurées en lecture seule ; ce rapport ne doit servir à aucun réglage.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from combatbot.corpus.observation_e2e_metrics import (
    DOMAINS, INFO_DOMAINS, NOT_EVALUABLE_DOMAINS, Frame, evaluate_frames, prediction_from_document,
    truth_from_annotation,
)
from combatbot.corpus.repository import CorpusRepository

REPORT_NAME = "observation-e2e"
TITLES = {
    "map": "Résolution de map", "combat": "Combat détecté", "player": "Joueur (cellule)",
    "enemies": "Ennemis (cellules)", "occupancy": "Occupation des cellules", "ap": "PA", "mp": "PM",
    "phase": "Phase", "turn": "Tour", "grid_source": "Source de grille", "grid_visibility": "Visibilité de grille",
    "alignment": "Alignement de grille", "result": "Résultat de combat",
    "grid_visibility_truth": "Visibilité de grille (vérité)",
}


def _split(entry, document: dict) -> str:
    declared = (document.get("capture") or {}).get("entity_split_declared")
    return str(declared or entry.usage or "diagnostic")


def collect_frames(repository: CorpusRepository, splits: tuple[str, ...] | None = None) -> tuple[list[Frame], dict]:
    frames: list[Frame] = []
    skipped = {"unreadable": 0, "filtered_split": 0}
    for entry in repository.list_entries():
        try:
            document = repository.read_observation(entry)
            annotation = repository.read_annotation(entry)
        except (OSError, ValueError):
            skipped["unreadable"] += 1
            continue
        split = _split(entry, document)
        if splits is not None and split not in splits:
            skipped["filtered_split"] += 1
            continue
        frames.append(Frame(entry.observation_id, entry.session_id, entry.frame_index, split,
                            truth_from_annotation(annotation, document), prediction_from_document(document)))
    frames.sort(key=lambda frame: (frame.session_id, frame.frame_index))
    return frames, skipped


def run_observation_e2e(repository: CorpusRepository, splits: tuple[str, ...] | None = None) -> dict[str, object]:
    frames, skipped = collect_frames(repository, splits)
    report = evaluate_frames(frames)
    report.update({
        "schema_version": 1, "lot": "FAST-3B7", "benchmark": REPORT_NAME,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "corpus_root": str(repository.root), "splits_filter": list(splits) if splits else None,
        "skipped": skipped, "actions": "NONE",
        "rules": {"missing_truth": "NO_TRUTH, jamais compté comme PASS",
                  "abstention": "UNKNOWN / None = abstention, jamais une bonne réponse",
                  "domain_verdict": "FAIL si une affirmation fausse ; PASS seulement si toutes les frames à vérité "
                                    "sont justes ; NOT_EVALUABLE si aucune vérité ou aucune affirmation"},
    })
    if report["splits"].get("test"):
        report["notes"] = ["frames TEST mesurées en lecture seule : ne pas régler un modèle sur ce rapport"]
    return report


def markdown_report(report: dict) -> str:
    lines = ["# Observation de bout en bout (FAST-3B7)", "",
             f"Généré le {report['generated_at']} · {report['frames']} frame(s) · verdict **{report['overall']}** "
             "· ACTIONS : NONE", "",
             "Mesure image par image de la prédiction **enregistrée** contre la vérité humaine confirmée. "
             "Une frame sans vérité n'est jamais un succès ; une abstention n'est jamais une bonne réponse.", "",
             "| Domaine | Verdict | Vérités | Justes | Fausses | Abstentions | Non enregistré | Précision | UNKNOWN |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|"]

    def fmt(value) -> str:
        return "—" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)

    for domain in DOMAINS:
        item = report["domains"][domain]
        m = item["metrics"]
        lines.append(f"| {TITLES[domain]} | **{item['status']}** | {m['frames_with_truth']} | {m['correct']} | "
                     f"{m['wrong']} | {m['abstained']} | {m['not_recorded']} | {fmt(m['precision'])} | "
                     f"{fmt(m['unknown_rate'])} |")
    lines += ["", "## Détails"]
    enemies, occupancy, turn = (report["domains"][key]["metrics"] for key in ("enemies", "occupancy", "turn"))
    lines += [f"- Ennemis : précision par cellule {fmt(enemies.get('cell_precision'))}, "
              f"rappel {fmt(enemies.get('cell_recall'))}",
              f"- Occupation : {occupancy.get('labelled_cells', 0)} cellule(s) étiquetée(s), "
              f"{occupancy.get('cell_wrong', 0)} fausse(s), UNKNOWN {fmt(occupancy.get('cell_unknown_rate'))}",
              f"- Tour : « mon tour » affirmé à tort **{turn.get('dangerous_claimed_my_turn', 0)}**, "
              f"manqué {turn.get('dangerous_missed_my_turn', 0)}"]
    for domain in DOMAINS:
        for note in report["domains"][domain]["notes"]:
            lines.append(f"- {TITLES[domain]} : {note}")
        for example in report["domains"][domain]["wrong_examples"]:
            lines.append(f"  - erreur : `{example['observation_id']}` (session {example['session_id']}, "
                         f"frame {example['frame_index']})")
    lines += ["", "## Sans vérité par frame"]
    for domain in INFO_DOMAINS:
        lines.append(f"- {TITLES[domain]} (INFO) : {json.dumps(report['domains'][domain]['metrics']['distribution'], ensure_ascii=False)}")
    for domain in NOT_EVALUABLE_DOMAINS:
        lines.append(f"- {TITLES[domain]} : NOT_EVALUABLE — {report['domains'][domain]['notes'][0]}")
    lines += ["", "## Latence enregistrée (ms)", "", "| Étape | Frames | Moyenne | Médiane | p95 | Max |",
              "|---|---:|---:|---:|---:|---:|"]
    for stage, stats in report["latency_ms"].items():
        lines.append(f"| {'bout en bout' if stage == 'total' else stage} | {stats['frames']} | {fmt(stats['mean'])} | "
                     f"{fmt(stats['median'])} | {fmt(stats['p95'])} | {fmt(stats['max'])} |")
    lines += ["", "## Par split", ""]
    for split, domains in report["splits"].items():
        summary = ", ".join(f"{TITLES[domain]} {counts.get('CORRECT', 0)}✓/{counts.get('WRONG', 0)}✗"
                            for domain, counts in domains.items()
                            if sum(counts.get(key, 0) for key in ("CORRECT", "WRONG", "ABSTAINED", "NOT_RECORDED")))
        lines.append(f"- {split} : {summary or 'aucune vérité'}")
    for note in report.get("notes") or []:
        lines.append(f"- ⚠ {note}")
    if report["skipped"]["unreadable"]:
        lines.append(f"- ⚠ {report['skipped']['unreadable']} observation(s) illisible(s) ignorée(s)")
    return "\n".join(lines) + "\n"


def write_report(report: dict, output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path, md_path = output / f"{REPORT_NAME}.json", output / f"{REPORT_NAME}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, md_path
