"""Pourquoi ``player_cell_id`` est UNKNOWN — ``python -m combatbot.benchmark --player-cell-diagnostics``.

Chaque frame sans cellule joueur reçoit **une** raison, dans l'ordre de la chaîne de perception, à partir
de ce qui a été journalisé (sessions 3B-7 ``frames.jsonl`` et observations enregistrées du corpus) :

1. hors combat, phase connue (la cellule du joueur n'est pas attendue) ; une phase inconnue ne cache
   jamais la cause technique : la frame suit la chaîne et est comptée à part ;
2. map inconnue ; grille non GameData ; grille non visible ; grille non alignée ;
3. profil joueur absent / d'un autre layout ;
4. aucun candidat de l'équipe du joueur (anneau masqué, mode créature, animation : **indiscernables**
   sans vérité humaine, jamais attribués au hasard) ;
5. candidat trop éloigné du profil, ambigu, en conflit avec la position précédente ;
6. joueur choisi par le détecteur mais non retenu par le suivi (EntityTracker) ;
7. non explicable : le journal ne contient pas le diagnostic (enregistrement antérieur à ce lot).

Lecture seule : aucun seuil n'est modifié, aucune vérité TEST n'est lue ni utilisée pour régler.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

REASONS = {
    "NOT_IN_FIGHT": "hors combat : cellule joueur non attendue",
    "MAP_UNKNOWN": "map inconnue",
    "GRID_NOT_GAMEDATA": "grille non GameData (pipeline historique)",
    "GRID_NOT_VISIBLE": "grille non visible",
    "GRID_NOT_ALIGNED": "projection non alignée",
    "LEGACY_PIPELINE": "pipeline historique sans détecteur par cellule (aucun diagnostic)",
    "PLAYER_PROFILE_ABSENT": "profil joueur absent",
    "PLAYER_PROFILE_OTHER_LAYOUT": "profil joueur d'un autre layout",
    "NO_PLAYER_CANDIDATE": "aucun anneau de l'équipe du joueur (masqué / mode créature / animation, indiscernables)",
    "CANDIDATE_TOO_FAR": "candidat trop éloigné du profil joueur",
    "CANDIDATE_AMBIGUOUS": "plusieurs candidats proches du profil (marge insuffisante)",
    "CONFLICT_WITH_PRIOR": "candidat en conflit avec la position précédente",
    "CANDIDATE_NOT_CONFIRMED": "candidat de l'équipe du joueur non confirmé (trop loin ou ambigu, non journalisé)",
    "TRACKER_DROPPED": "joueur choisi par le détecteur mais non retenu par le suivi",
    "UNEXPLAINED": "diagnostic non journalisé (enregistrement antérieur)",
}
FIGHT_PHASES = ("FIGHTING", "PLACEMENT")


def record_from_document(document: dict) -> dict:
    """Observation enregistrée du corpus → même forme qu'une ligne de session."""
    prediction = document.get("prediction") if isinstance(document.get("prediction"), dict) else {}
    capture = document.get("capture") if isinstance(document.get("capture"), dict) else {}
    semantic = capture.get("semantic_combat_state") or {}
    resolution = capture.get("map_resolution") or {}
    grid = prediction.get("grid") if isinstance(prediction.get("grid"), dict) else {}
    entities = capture.get("entities") or {}
    diag = {key: entities[key] for key in ("pipeline", "player_profile", "team_profile", "player_decision",
                                           "player_candidates", "player_track_state") if key in entities}
    evidence = prediction.get("entity_evidence") or ()
    unconfirmed = sum(1 for item in evidence if isinstance(item, dict)
                      and any("joueur non confirmé" in str(reason) for reason in item.get("reasons", ())))
    return {"session": document.get("session_id") or capture.get("session_id"),
            "frame": document.get("frame_index") or capture.get("frame_index"),
            "phase": semantic.get("phase") or capture.get("phase"),
            "map_status": resolution.get("status"), "map_id": resolution.get("map_id") or grid.get("map_id_declared"),
            "grid_source": capture.get("grid_source") or grid.get("grid_source"),
            "grid_visibility": capture.get("grid_visibility_state"), "alignment": capture.get("alignment_status"),
            "player_cell_id": prediction.get("player_cell_id"),
            "player_confidence": prediction.get("player_confidence"),
            "player_diag": diag, "unconfirmed_player_candidates": unconfirmed}


def classify(record: dict) -> str | None:
    """Raison de l'absence de cellule joueur, ou None si elle est connue."""
    if record.get("player_cell_id") is not None:
        return None
    phase = record.get("phase")
    if phase is not None and phase not in FIGHT_PHASES:    # phase connue et hors combat
        return "NOT_IN_FIGHT"
    if record.get("map_id") is None and record.get("map_status") not in ("RESOLVED", None):
        return "MAP_UNKNOWN"
    if record.get("grid_source") not in (None, "GAMEDATA_PROJECTED"):
        return "GRID_NOT_GAMEDATA"
    if record.get("grid_visibility") not in (None, "VISIBLE"):
        return "GRID_NOT_VISIBLE"
    if record.get("alignment") not in (None, "ALIGNED"):
        return "GRID_NOT_ALIGNED"
    diag = record.get("player_diag") or {}
    if diag.get("pipeline") == "LEGACY_CLASSIFY":
        return "LEGACY_PIPELINE"
    profile = diag.get("player_profile")
    if profile == "absent":
        return "PLAYER_PROFILE_ABSENT"
    if profile == "incompatible_layout":
        return "PLAYER_PROFILE_OTHER_LAYOUT"
    if "player_decision" not in diag:                        # journal antérieur à ce diagnostic
        return "CANDIDATE_NOT_CONFIRMED" if record.get("unconfirmed_player_candidates", 0) else "UNEXPLAINED"
    decision = diag["player_decision"]
    if decision is None:                                     # le détecteur n'a vu aucun candidat
        return "NO_PLAYER_CANDIDATE"
    return {"too_far": "CANDIDATE_TOO_FAR", "ambiguous": "CANDIDATE_AMBIGUOUS",
            "conflict_with_prior": "CONFLICT_WITH_PRIOR"}.get(decision, "TRACKER_DROPPED")


def load_records(data_dirs: list[Path], repository=None) -> list[dict]:
    from combatbot.live.runtime_profile import load_frames
    records = []
    for session, frame in load_frames(data_dirs):
        records.append({**frame, "session": session, "source": "session"})
    if repository is not None:
        for entry in repository.list_entries():
            try:
                document = repository.read_observation(entry)
            except (OSError, ValueError):
                continue
            records.append({**record_from_document(document), "source": "corpus",
                            "observation_id": entry.observation_id})
    return records


def diagnose(records: list[dict], *, case_limit: int = 500) -> dict[str, object]:
    reasons: Counter = Counter()
    by_source: dict[str, Counter] = {}
    cases = []
    in_fight = in_fight_known = phase_unknown = 0
    for record in records:
        reason = classify(record)
        phase_unknown += record.get("phase") is None
        if record.get("phase") in FIGHT_PHASES:
            in_fight += 1
            in_fight_known += reason is None
        if reason is None:
            continue
        reasons[reason] += 1
        by_source.setdefault(str(record.get("source")), Counter())[reason] += 1
        if reason != "NOT_IN_FIGHT" and len(cases) < case_limit:
            diag = record.get("player_diag") or {}
            cases.append({"source": record.get("source"), "session": record.get("session"),
                          "frame": record.get("frame"), "observation_id": record.get("observation_id"),
                          "map_id": record.get("map_id"), "grid_source": record.get("grid_source"),
                          "alignment": record.get("alignment"), "confidence": record.get("player_confidence"),
                          "candidates": diag.get("player_candidates"), "reason": reason})
    unknown = sum(reasons.values())
    unknown_in_fight = unknown - reasons.get("NOT_IN_FIGHT", 0)
    return {"schema_version": 1, "report": "player-cell-diagnostics",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "frames": len(records), "player_unknown": unknown,
            "player_unknown_rate": round(unknown / len(records), 3) if records else None,
            "fight_frames": in_fight, "fight_player_known": in_fight_known,
            "fight_player_unknown_rate": round(1 - in_fight_known / in_fight, 3) if in_fight else None,
            "reasons": dict(reasons.most_common()), "labels": REASONS,
            "by_source": {source: dict(counter.most_common()) for source, counter in by_source.items()},
            "unknown_in_fight": unknown_in_fight, "phase_unknown_frames": phase_unknown, "cases": cases, "thresholds_modified": "NONE"}


def markdown_report(report: dict) -> str:
    lines = ["# Cellule joueur inconnue — raisons", "",
             f"{report['frames']} frame(s) · cellule joueur inconnue : {report['player_unknown']} "
             f"({report['player_unknown_rate']}) · en combat : {report['fight_frames']} frame(s), "
             f"inconnue à {report['fight_player_unknown_rate']}", "",
             "| Raison | Frames |", "|---|---|"]
    lines += [f"| {report['labels'][reason]} | {count} |" for reason, count in report["reasons"].items()] \
        or ["| — | 0 |"]
    lines += ["", f"Frames à phase inconnue (classées selon la chaîne technique) : {report['phase_unknown_frames']}."]
    lines += ["", f"Cas en combat listés dans le JSON : {len(report['cases'])} (limite 500).",
              "Aucun seuil modifié. « Anneau masqué », « mode créature » et « animation » ne sont pas séparables "
              "sans vérité humaine : ils restent regroupés."]
    return "\n".join(lines) + "\n"


def write_report(report: dict, output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path, md_path = output / "player-cell-diagnostics.json", output / "player-cell-diagnostics.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, md_path
