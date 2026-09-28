"""Rapport ``profile-spells-readiness`` : état de chaque sort du profil pour une décision réelle.

Lecture seule : aucune valeur n'est modifiée. Chaque refus est expliqué (statut de scan, caractéristiques
inconnues, ``decision_ready`` absent, case absente, sort désactivé par la stratégie).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from combatbot.combat.spells import from_profile_row

FIELDS = ("ap_cost", "min_range", "max_range", "modifiable_range", "line_cast", "line_of_sight",
          "per_turn", "per_target")


def spell_readiness(storage, profile_id: int) -> dict[str, object]:
    config = storage.get_profile_setting(profile_id, "dofbot2_spells", None)
    config = config if isinstance(config, dict) else {}
    rows = []
    for index, row in enumerate(storage.list_profile_spells(profile_id)):
        spell = from_profile_row(row, config.get(str(row["id"])), default_priority=index + 1)
        keys = row.keys()
        reasons = []
        if row["status"] != "Confirmé":
            reasons.append(f"statut de scan « {row['status']} » (confirmation humaine requise)")
        elif "decision_ready" in keys and not row["decision_ready"]:
            reasons.append("confirmé mais decision_ready absent")
        if spell.unknown_fields:
            reasons.append("inconnu : " + ", ".join(spell.unknown_fields))
        if spell.slot is None:
            reasons.append("case ou page inconnue")
        if not spell.strategy.use:
            reasons.append("désactivé dans la stratégie")
        rows.append({"id": int(row["id"]), "page": row["page"], "slot": row["slot"], "name": row["name"],
                     "scan_status": row["status"], "provenance": spell.provenance.value,
                     **{name: getattr(spell, name) for name in FIELDS},
                     "decision_ready": spell.usable_for_real_decision, "refusal_reasons": reasons})
    by_status: dict[str, int] = {}
    for item in rows:
        by_status[item["scan_status"]] = by_status.get(item["scan_status"], 0) + 1
    return {"schema_version": 1, "report": "profile-spells-readiness", "profile_id": profile_id,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "total": len(rows), "decision_ready": sum(item["decision_ready"] for item in rows),
            "by_scan_status": by_status, "spells": rows, "values_modified": "NONE"}


def _cell(value: object) -> str:
    return "?" if value is None else ("oui" if value is True else "non" if value is False else str(value))


def markdown_report(report: dict) -> str:
    lines = ["# Sorts du profil — préparation à la décision réelle", "",
             f"Profil {report['profile_id']} · {report['total']} sort(s) · prêts pour une décision réelle : "
             f"**{report['decision_ready']}** · statuts : {json.dumps(report['by_scan_status'], ensure_ascii=False)}", "",
             "| Page | Case | Nom | PA | Portée | Modif. | Ligne | LOS | /tour | /cible | Statut | Prêt | Raisons |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for item in report["spells"]:
        span = f"{_cell(item['min_range'])}–{_cell(item['max_range'])}"
        lines.append(f"| {_cell(item['page'])} | {_cell(item['slot'])} | {item['name'] or '?'} | {_cell(item['ap_cost'])} "
                     f"| {span} | {_cell(item['modifiable_range'])} | {_cell(item['line_cast'])} "
                     f"| {_cell(item['line_of_sight'])} | {_cell(item['per_turn'])} | {_cell(item['per_target'])} "
                     f"| {item['scan_status']} | {'oui' if item['decision_ready'] else 'non'} "
                     f"| {'; '.join(item['refusal_reasons']) or '—'} |")
    lines += ["", "Aucune valeur n'a été modifiée : toute correction passe par la confirmation dans l'onglet Sorts."]
    return "\n".join(lines) + "\n"


def write_report(report: dict, output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path, md_path = output / "profile-spells-readiness.json", output / "profile-spells-readiness.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, md_path
