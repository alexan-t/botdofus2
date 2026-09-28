"""FAST-4D/5A0 : rejeu dry-run du corpus — ``python -m combatbot.benchmark --dry-run-plans``.

Pour chaque frame enregistrée : prédiction sauvée → ``RealCombatState`` → plan → exécuteur dry-run.
Mesure combien de frames seraient décidables et **pourquoi** les autres sont bloquées, sans DOFUS, sans
relancer la vision et sans rien envoyer. Rien n'est écrit dans le corpus.

Map de chaque frame : map RESOLVED par 3B-6C, sinon mapId vérifié par /mapid ; sinon inconnue (BLOCKED).
Sorts : ceux du profil (``profile_spells`` confirmés) ; règles de ciblage : prudentes par défaut.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from combatbot.combat.executor import DryRunActionExecutor
from combatbot.combat.pathfinding import CombatMap
from combatbot.combat.planner import PlanStatus, plan_turn
from combatbot.combat.spells import CombatSpell
from combatbot.combat.state import EnemyState, RealCombatState
from combatbot.combat.targeting import CONSERVATIVE_RULES, TargetingRules
from combatbot.corpus.repository import CorpusRepository

SAFE_GRID_CONFIDENCE = 0.65     # mêmes seuils que CombatObservation.safe_for_decision
SAFE_OBSERVATION_CONFIDENCE = 0.7


def safe_for_decision(prediction: dict[str, Any]) -> bool:
    """Recalcul de ``CombatObservation.safe_for_decision`` (propriété non sérialisée) depuis le dict."""
    grid = prediction.get("grid") if isinstance(prediction.get("grid"), dict) else {}
    cells = grid.get("cells") if isinstance(grid.get("cells"), list) else []
    enemies = prediction.get("enemies") if isinstance(prediction.get("enemies"), list) else []
    entities = prediction.get("entities") or ()
    return bool(
        prediction.get("combat_detected")
        and prediction.get("player_turn") is not None
        and prediction.get("player_cell") is not None
        and enemies
        and prediction.get("ap") is not None
        and prediction.get("mp") is not None
        and float(grid.get("confidence") or 0.0) >= SAFE_GRID_CONFIDENCE
        and cells
        and all(isinstance(cell, dict) and cell.get("state") != "UNKNOWN" for cell in cells)
        and float(prediction.get("observation_confidence") or 0.0) >= SAFE_OBSERVATION_CONFIDENCE
        and not [item for item in entities if isinstance(item, dict) and item.get("kind") == "UNKNOWN"]
        and all(isinstance(enemy, dict) and enemy.get("observed_this_frame", True) for enemy in enemies)
    )


def frame_map_id(document: dict[str, Any]) -> int | None:
    resolution = (document.get("capture") or {}).get("map_resolution")
    if isinstance(resolution, dict) and resolution.get("status") == "RESOLVED" and resolution.get("map_id") is not None:
        return int(resolution["map_id"])
    snapshot = document.get("grid_snapshot") or {}
    if snapshot.get("map_id_source") == "user_verified_mapid" and snapshot.get("map_id_declared") is not None:
        return int(snapshot["map_id_declared"])
    return None


def state_from_document(document: dict[str, Any]) -> RealCombatState:
    prediction = document.get("prediction") if isinstance(document.get("prediction"), dict) else {}
    capture = document.get("capture") if isinstance(document.get("capture"), dict) else {}
    semantic = capture.get("semantic_combat_state") if isinstance(capture.get("semantic_combat_state"), dict) else {}
    grid = prediction.get("grid") if isinstance(prediction.get("grid"), dict) else {}
    player = prediction.get("player_cell_id")
    occupied, unknown = set(), set()
    for cell in grid.get("cells") or ():
        if not isinstance(cell, dict) or not isinstance(cell.get("cell_id"), int):
            continue
        if cell.get("state") == "OCCUPIED" and cell["cell_id"] != player:
            occupied.add(cell["cell_id"])
        elif cell.get("state") == "UNKNOWN":
            unknown.add(cell["cell_id"])
    enemies = tuple(EnemyState(str(item.get("id")), item.get("cell_id") if isinstance(item.get("cell_id"), int) else None,
                               bool(item.get("observed_this_frame", True)))
                    for item in prediction.get("enemies") or () if isinstance(item, dict))
    return RealCombatState(
        map_id=frame_map_id(document), player_cell_id=player if isinstance(player, int) else None,
        ap=prediction.get("ap") if isinstance(prediction.get("ap"), int) else None,
        mp=prediction.get("mp") if isinstance(prediction.get("mp"), int) else None,
        enemies=enemies, occupied_cells=frozenset(occupied), unknown_cells=frozenset(unknown),
        phase=semantic.get("phase"), turn=semantic.get("turn_owner"),
        safe_for_decision=safe_for_decision(prediction),
        confidence=prediction.get("observation_confidence"))


def _reason_family(reason: str) -> str:
    """Regroupe « ennemi E3 non localisé » et « ennemi E1 non localisé » dans une même famille."""
    for prefix in ("ennemi ", "phase ", "tour ", "topologie de la map", "sort possible mais non prouvé",
                   "le seul placement utile"):
        if reason.startswith(prefix):
            return prefix.strip()
    return reason


def run_dry_run_replay(repository: CorpusRepository, map_provider: Callable[[int], CombatMap | None],
                       spells: Iterable[CombatSpell], *, rules: TargetingRules = CONSERVATIVE_RULES,
                       limit: int | None = None) -> dict[str, object]:
    spells = tuple(spells)
    statuses: Counter = Counter()
    reasons: Counter = Counter()
    first_steps: Counter = Counter()
    assumptions: Counter = Counter()
    examples: list[dict[str, object]] = []
    maps: dict[int, CombatMap | None] = {}
    executor = DryRunActionExecutor()
    frames = 0
    for entry in repository.list_entries():
        if limit is not None and frames >= limit:
            break
        try:
            document = repository.read_observation(entry)
        except (OSError, ValueError):
            statuses["UNREADABLE"] += 1
            continue
        frames += 1
        state = state_from_document(document)
        combat_map = None
        if state.map_id is not None:
            if state.map_id not in maps:
                try:
                    maps[state.map_id] = map_provider(state.map_id)
                except (OSError, ValueError, KeyError):
                    maps[state.map_id] = None
            combat_map = maps[state.map_id]
        plan = plan_turn(state, combat_map, spells, rules=rules)
        report = executor.run_plan(plan)
        statuses[plan.status.value] += 1
        assumptions.update(plan.assumptions)
        if plan.status is PlanStatus.BLOCKED:
            reasons.update(_reason_family(reason) for reason in plan.blocked_reasons)
        else:
            first_steps[plan.steps[0].kind.value] += 1
            if len(examples) < 10:
                examples.append({"observation_id": entry.observation_id, "plan": plan.describe(),
                                 "plan_id": report.plan_id})
    return {"schema_version": 1, "lot": "FAST-4D/5A0", "benchmark": "dry-run-plans",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "frames": frames, "statuses": dict(statuses), "blocked_reasons": dict(reasons.most_common()),
            "first_step": dict(first_steps), "assumptions": dict(assumptions), "examples": examples,
            "spells": {"total": len(spells), "usable": sum(spell.usable_for_real_decision for spell in spells)},
            "maps_loaded": sum(value is not None for value in maps.values()), "maps_requested": len(maps),
            "rules": {"range_metric": rules.range_metric.value, "los_oracle": rules.los_oracle is not None},
            "actions": "NONE"}


def markdown_report(report: dict) -> str:
    lines = ["# Rejeu dry-run des décisions (FAST-4D / 5A0)", "",
             f"{report['frames']} frame(s) · ACTIONS : NONE · règles : portée {report['rules']['range_metric']}, "
             f"oracle LOS {'oui' if report['rules']['los_oracle'] else 'non'}", "",
             f"- Plans : {json.dumps(report['statuses'], ensure_ascii=False)}",
             f"- Sorts utilisables (confirmés) : {report['spells']['usable']}/{report['spells']['total']}",
             f"- Maps chargées : {report['maps_loaded']}/{report['maps_requested']}", "", "## Raisons de refus", ""]
    lines += [f"- {reason} : {count}" for reason, count in report["blocked_reasons"].items()] or ["- aucune"]
    lines += ["", "## Plans prêts (exemples)", ""]
    lines += [f"- `{item['observation_id']}` : {' · '.join(item['plan'])}" for item in report["examples"]] or ["- aucun"]
    if report["assumptions"]:
        lines += ["", "## Hypothèses utilisées", ""] + [f"- {item} ({count})" for item, count in report["assumptions"].items()]
    return "\n".join(lines) + "\n"


def write_report(report: dict, output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path, md_path = output / "dry-run-plans.json", output / "dry-run-plans.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, md_path
