"""Dossier de recette live fast-track — ``python -m combatbot.benchmark --live-fasttrack-report``.

À lancer **après** une session réelle observée dans DofBot2 (voir ``RECETTE-LIVE-FASTTRACK.md``). Tout
est lu : journaux de session 3B-7, corpus, base locale. Rien n'est écrit ailleurs que dans le dossier du
rapport ; aucune action n'est envoyée au jeu. Chaque domaine sans preuve reste NOT_EVALUABLE.

Fichiers : summary.md, telemetry.json, map.json, grid.json, entities.json, hud.json, combat-state.json,
4c-proof.json, readiness.json.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable

from combatbot.live.player_cell_diagnostics import FIGHT_PHASES, diagnose, load_records
from combatbot.live.runtime_profile import load_frames, runtime_profile, session_journals

NOT_EVALUABLE = "NOT_EVALUABLE"


def latest_session(data_dirs: list[Path]) -> str | None:
    journals = session_journals(data_dirs)
    return max(journals, key=lambda name: journals[name].stat().st_mtime) if journals else None


def _ratio(count: int, total: int) -> float | None:
    return round(count / total, 3) if total else None


def map_report(frames: list[dict]) -> dict[str, object]:
    statuses = Counter(str(frame.get("map_status")) for frame in frames)
    changes, previous = [], None
    for index, frame in enumerate(frames):
        if frame.get("map_status") == "RESOLVED" and frame.get("map_id") is not None:
            if previous is not None and frame["map_id"] != previous:
                changes.append({"frame_index": index, "from": previous, "to": frame["map_id"]})
            previous = frame["map_id"]
    verdict = NOT_EVALUABLE if not frames else ("PARTIAL" if statuses.get("RESOLVED") else "FAIL")
    return {"frames": len(frames), "status": dict(statuses), "resolved_rate": _ratio(statuses.get("RESOLVED", 0), len(frames)),
            "distinct_maps": sorted({frame["map_id"] for frame in frames
                                     if frame.get("map_status") == "RESOLVED" and frame.get("map_id") is not None}),
            "map_changes": changes, "verdict": verdict,
            "note": "Le type de trajet (extérieur, intérieur, donjon, salle, zaap) est déclaré dans summary.md par "
                    "l'utilisateur : il n'est pas déduit ici."}


def grid_report(frames: list[dict], changes: list[dict]) -> dict[str, object]:
    after_change = []
    for change in changes:
        start = change["frame_index"]
        aligned_at = next((index - start for index in range(start, len(frames))
                           if frames[index].get("alignment") == "ALIGNED"), None)
        after_change.append({**change, "frames_until_aligned": aligned_at})
    fight = [frame for frame in frames if frame.get("phase") in FIGHT_PHASES]
    aligned = sum(frame.get("alignment") == "ALIGNED" for frame in fight)
    return {"frames": len(frames),
            "grid_source": dict(Counter(str(frame.get("grid_source")) for frame in frames)),
            "visibility": dict(Counter(str(frame.get("grid_visibility")) for frame in frames)),
            "alignment": dict(Counter(str(frame.get("alignment")) for frame in frames)),
            "fight_frames": len(fight), "fight_aligned_rate": _ratio(aligned, len(fight)),
            "alignment_after_map_change": after_change,
            "verdict": NOT_EVALUABLE if not fight else ("PASS" if aligned == len(fight) else "PARTIAL")}


def hud_report(frames: list[dict]) -> dict[str, object]:
    fight = [frame for frame in frames if frame.get("phase") == "FIGHTING"]
    unknown_ap = sum(frame.get("ap") is None for frame in fight)
    unknown_mp = sum(frame.get("mp") is None for frame in fight)
    return {"fight_frames": len(fight), "ap_unknown_rate": _ratio(unknown_ap, len(fight)),
            "mp_unknown_rate": _ratio(unknown_mp, len(fight)),
            "note": "Taux d'inconnus seulement : l'exactitude PA/PM exige une vérité humaine (revue HUD).",
            "verdict": NOT_EVALUABLE}


def combat_state_report(frames: list[dict]) -> dict[str, object]:
    without_model = [frame for frame in frames if frame.get("combat_state_model") is False]
    claimed_without_model = sum(frame.get("turn_owner") == "PLAYER" for frame in without_model)
    known = [frame for frame in frames if "combat_state_model" in frame]
    return {"frames": len(frames), "phase": dict(Counter(str(frame.get("phase")) for frame in frames)),
            "turn_owner": dict(Counter(str(frame.get("turn_owner")) for frame in frames)),
            "model_present_frames": sum(frame.get("combat_state_model") is True for frame in known),
            "model_absent_frames": len(without_model),
            "my_turn_claimed_without_model": claimed_without_model,
            "fail_closed": claimed_without_model == 0,
            "note": "Faux « mon tour » mesurable seulement avec une vérité humaine (annotation phase/tour).",
            "verdict": "FAIL" if claimed_without_model else (NOT_EVALUABLE if not known else "PARTIAL")}


def build_bundle(data_dirs: list[Path], *, session: str | None, repository=None, readiness: dict | None = None,
                 targeting_proof: dict | None = None) -> dict[str, dict]:
    sessions = {session} if session else None
    frames = [record for _name, record in load_frames(data_dirs, sessions)]
    maps = map_report(frames)
    reports = {
        "telemetry.json": runtime_profile(data_dirs, sessions),
        "map.json": maps,
        "grid.json": grid_report(frames, maps["map_changes"]),
        "entities.json": diagnose(load_records(data_dirs, None, sessions)),
        "hud.json": hud_report(frames),
        "combat-state.json": combat_state_report(frames),
        "4c-proof.json": targeting_proof or {"verdict": NOT_EVALUABLE, "rule_status": "UNVERIFIED"},
        "readiness.json": readiness or {},
    }
    for report in reports.values():
        report.setdefault("session", session)
    return reports


def summary_markdown(reports: dict[str, dict], *, session: str | None) -> str:
    telemetry, entities = reports["telemetry.json"], reports["entities.json"]
    readiness, proof = reports["readiness.json"], reports["4c-proof.json"]
    rows = [
        ("Latence (p95 ≤ 400 ms)", telemetry.get("verdict"), f"p95 total {telemetry['stages_ms']['total']['p95']} ms"),
        ("Map", reports["map.json"]["verdict"], f"résolue {reports['map.json']['resolved_rate']}, "
                                                f"{len(reports['map.json']['map_changes'])} changement(s)"),
        ("Grille en combat", reports["grid.json"]["verdict"], f"alignée {reports['grid.json']['fight_aligned_rate']}"),
        ("Cellule joueur en combat", NOT_EVALUABLE if not entities["fight_frames"] else "PARTIAL",
         f"inconnue {entities['fight_player_unknown_rate']} · raisons {entities['reasons']}"),
        ("PA/PM en combat", reports["hud.json"]["verdict"], f"PA inconnus {reports['hud.json']['ap_unknown_rate']}, "
                                                          f"PM inconnus {reports['hud.json']['mp_unknown_rate']}"),
        ("Phase/tour (fail-closed)", reports["combat-state.json"]["verdict"],
         f"« mon tour » sans modèle : {reports['combat-state.json']['my_turn_claimed_without_model']}"),
        ("Preuve portée/LOS (4C)", proof.get("rule_status", "UNVERIFIED"),
         f"{proof.get('samples', 0)} échantillon(s), cas manquants {proof.get('missing_cases', 'tous')}"),
        ("Porte d'entrée réelle", readiness.get("real_input_gate", "?"), "aucune entrée envoyée"),
    ]
    lines = [f"# Recette live fast-track — session {session or 'aucune'}", "",
             "**ACTIONS AUTOMATIQUES DANS DOFUS : AUCUNE.**", "",
             "| Domaine | Verdict | Mesure |", "|---|---|---|"]
    lines += [f"| {name} | **{verdict}** | {detail} |" for name, verdict, detail in rows]
    lines += ["", "## À compléter par l'utilisateur", "",
              "- Trajets effectués (extérieur / intérieur / donjon / salle → salle / zaap) : …",
              "- Combats joués manuellement (nombre, maps) : …",
              "- Sorts sélectionnés pour la preuve 4C : …",
              "", "Chaque domaine sans preuve reste NOT_EVALUABLE ; aucun PASS n'est déduit d'une absence."]
    return "\n".join(lines) + "\n"


def write_bundle(reports: dict[str, dict], output: Path, *, session: str | None) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    for name, report in reports.items():
        (output / name).write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (output / "summary.md").write_text(summary_markdown(reports, session=session), encoding="utf-8")
    return output


def readiness_report(storage_factory: Callable | None, profile_id: int | None) -> dict[str, object]:
    """Profil, sorts, page confirmée, porte d'entrée réelle — lecture seule."""
    from combatbot.combat.safety import REAL_INPUT_GATE
    from combatbot.combat.spell_page import load_confirmed_page
    result: dict[str, object] = {"real_input_gate": "CLOSED" if REAL_INPUT_GATE.refusal() else "OPEN",
                                 "generated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    if storage_factory is None:
        return result
    from combatbot.live.profiles import resolve_profile
    from combatbot.live.spell_readiness import spell_readiness
    storage = storage_factory()
    try:
        resolution = resolve_profile(storage, profile_id)
        result["profile"] = resolution.to_dict()
        if resolution.profile_id is not None:
            spells = spell_readiness(storage, resolution.profile_id)
            result["spells"] = {key: spells[key] for key in ("total", "decision_ready", "by_scan_status")}
            page = load_confirmed_page(storage, resolution.profile_id)
            result["confirmed_spell_page"] = page.page if page is not None else None
    finally:
        storage.close()
    return result
