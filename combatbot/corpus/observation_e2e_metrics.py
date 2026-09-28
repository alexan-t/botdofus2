"""FAST-3B7 : mesure image par image de l'observation complète contre les vérités humaines.

Logique pure (ni Qt, ni OpenCV) : elle compare la prédiction **enregistrée** avec chaque frame du
corpus à la vérité humaine confirmée de cette frame. Aucun détecteur n'est relancé ni modifié.

Chaque domaine classe chaque frame dans exactement une issue :

- ``CORRECT`` / ``WRONG`` : une vérité existe et la prédiction l'affirme (juste ou fausse) ;
- ``ABSTAINED`` : une vérité existe mais la prédiction est UNKNOWN / None (refus volontaire) ;
- ``NOT_RECORDED`` : une vérité existe mais l'observation enregistrée ne porte pas ce champ
  (ancien pipeline) : ce n'est ni une réussite ni une abstention ;
- ``NO_TRUTH`` : aucune vérité humaine utilisable. **Jamais comptée comme un succès.**

Règles de vérité (celles des lots d'origine, rien de plus) :

- map : ``grid_snapshot.map_id_declared`` seulement si ``map_id_source == "user_verified_mapid"`` ;
- combat : ``Annotation.combat_truth`` (annotation 3B-0) ;
- PA / PM : ``Annotation.human_confirmed`` (3B-4R2) ;
- joueur, ennemis, occupation : ``Annotation.entities_confirmed`` (3B-5) ;
- phase / tour : ``Annotation.combat_state_confirmed`` (3B-6B) ; « mon tour » affirmé à tort = erreur
  dangereuse, comptée à part ;
- visibilité/alignement de grille, source de grille, résultat de combat : aucune vérité par frame dans le
  corpus → distribution seulement (INFO) ou NOT_EVALUABLE.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

CORRECT, WRONG, ABSTAINED, NOT_RECORDED, NO_TRUTH = "CORRECT", "WRONG", "ABSTAINED", "NOT_RECORDED", "NO_TRUTH"
OUTCOMES = (CORRECT, WRONG, ABSTAINED, NOT_RECORDED, NO_TRUTH)
PASS, PARTIAL, FAIL, NOT_EVALUABLE, INFO = "PASS", "PARTIAL", "FAIL", "NOT_EVALUABLE", "INFO"
INCOMPLETE = "INCOMPLET"
UNKNOWN = "UNKNOWN"
LATENCY_STAGES = ("capture", "map", "grid", "entities", "hud", "combat_state", "overlay", "total")

# Domaines mesurés contre une vérité, dans l'ordre du rapport.
DOMAINS = ("map", "combat", "player", "enemies", "occupancy", "ap", "mp", "phase", "turn")
# Domaines sans vérité par frame dans le corpus : distribution uniquement.
INFO_DOMAINS = ("grid_source", "grid_visibility", "alignment")
NOT_EVALUABLE_DOMAINS = {
    "result": "aucune vérité victoire/défaite dans le corpus (la phase RESULTS est mesurée par « phase »)",
    "grid_visibility_truth": "pas de vérité humaine par frame dans ce corpus : voir --grid-validation (corpus lot3b2r)",
}


@dataclass(frozen=True)
class FrameTruth:
    """Vérités humaines d'une frame ; ``None`` = pas de vérité pour ce domaine."""
    map_id: int | None = None
    map_declared_unverified: bool = False
    combat: bool | None = None
    player_cell: int | None = None
    player_visible: bool | None = None       # NOT_VISIBLE : la vérité est « aucun joueur »
    entities_confirmed: bool = False
    enemy_cells: frozenset[int] | None = None
    empty_cells: frozenset[int] = frozenset()
    ap: int | None = None
    mp: int | None = None
    phase: str | None = None
    turn: str | None = None


@dataclass(frozen=True)
class FramePrediction:
    """Prédiction enregistrée ; ``NOT_RECORDED`` quand le champ n'existe pas dans le document."""
    map_status: str | None = None             # RESOLVED, AMBIGUOUS, UNKNOWN… ; None = non enregistré
    map_id: int | None = None
    combat_state: str | None = None           # COMBAT / EXPLORATION / UNKNOWN
    player_cell: int | None = None
    player_recorded: bool = False
    enemy_cells: frozenset[int] | None = None  # None = pipeline sans identité de cellule
    occupancy: dict[int, str] | None = None
    ap: int | None = None
    mp: int | None = None
    hud_recorded: bool = False
    phase: str | None = None                  # None = non enregistré
    turn: str | None = None
    grid_source: str | None = None
    grid_visibility: str | None = None
    alignment: str | None = None
    stage_ms: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Frame:
    observation_id: str
    session_id: str
    frame_index: int
    split: str
    truth: FrameTruth
    prediction: FramePrediction


# ------------------------------------------------------------------ extraction depuis le corpus
def _int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def prediction_from_document(document: dict[str, Any]) -> FramePrediction:
    capture = document.get("capture") if isinstance(document.get("capture"), dict) else {}
    prediction = document.get("prediction") if isinstance(document.get("prediction"), dict) else {}
    grid = prediction.get("grid") if isinstance(prediction.get("grid"), dict) else {}
    resolution = capture.get("map_resolution")
    map_status = map_id = None
    if isinstance(resolution, dict):
        map_status = str(resolution.get("status") or UNKNOWN)
        map_id = _int(resolution.get("map_id")) if map_status == "RESOLVED" else None
    enemies = prediction.get("enemies")
    enemy_cells = None
    if isinstance(enemies, list) and (prediction.get("entities") is not None
                                      or all(isinstance(item, dict) and "cell_id" in item for item in enemies)):
        enemy_cells = frozenset(
            cell for item in enemies if isinstance(item, dict) and item.get("observed_this_frame", True)
            for cell in [_int(item.get("cell_id"))] if cell is not None)
    cells = grid.get("cells") if isinstance(grid.get("cells"), list) else []
    occupancy = {cell_id: str(item.get("state") or UNKNOWN) for item in cells if isinstance(item, dict)
                 for cell_id in [_int(item.get("cell_id"))] if cell_id is not None} or None
    semantic = capture.get("semantic_combat_state")
    phase = turn = None
    if isinstance(semantic, dict):
        phase = str(semantic.get("phase") or UNKNOWN)
        turn = str(semantic.get("turn_owner") or UNKNOWN)
    stage_ms = capture.get("stage_ms") if isinstance(capture.get("stage_ms"), dict) else {}
    timings = {name: float(value) for name, value in stage_ms.items() if isinstance(value, (int, float))}
    if "total" not in timings and isinstance(capture.get("analysis_ms"), (int, float)):
        timings["total"] = float(capture["analysis_ms"])
    return FramePrediction(
        map_status=map_status, map_id=map_id,
        combat_state=str(prediction["combat_state"]) if prediction.get("combat_state") else None,
        player_cell=_int(prediction.get("player_cell_id")), player_recorded="player_cell_id" in prediction,
        enemy_cells=enemy_cells, occupancy=occupancy,
        ap=_int(prediction.get("ap")), mp=_int(prediction.get("mp")),
        hud_recorded="ap" in prediction or "mp" in prediction,
        phase=phase, turn=turn,
        grid_source=str(grid["grid_source"]) if grid.get("grid_source") else None,
        grid_visibility=(grid.get("grid_visibility") or {}).get("state") if isinstance(grid.get("grid_visibility"), dict)
        else capture.get("grid_visibility_state"),
        alignment=(grid.get("alignment") or {}).get("status") if isinstance(grid.get("alignment"), dict)
        else capture.get("alignment_status"),
        stage_ms=timings,
    )


def truth_from_annotation(annotation, document: dict[str, Any]) -> FrameTruth:
    """``annotation`` : ``corpus.models.Annotation`` ou None."""
    snapshot = document.get("grid_snapshot") if isinstance(document.get("grid_snapshot"), dict) else {}
    declared = _int(snapshot.get("map_id_declared"))
    verified = declared is not None and snapshot.get("map_id_source") == "user_verified_mapid"
    values: dict[str, Any] = {"map_id": declared if verified else None,
                              "map_declared_unverified": declared is not None and not verified}
    if annotation is None:
        return FrameTruth(**values)
    values["combat"] = annotation.combat_truth
    if annotation.human_confirmed:
        values["ap"], values["mp"] = annotation.ap_truth, annotation.mp_truth
    if annotation.entities_confirmed:
        values["entities_confirmed"] = True
        values["player_visible"] = (annotation.player_visibility == "VISIBLE"
                                    if annotation.player_visibility in ("VISIBLE", "NOT_VISIBLE") else None)
        values["player_cell"] = annotation.player_cell_id_truth
        values["enemy_cells"] = frozenset(int(item["cell_id"]) for item in annotation.enemy_cells_truth
                                          if isinstance(item, dict) and _int(item.get("cell_id")) is not None)
        empty = set(annotation.empty_confirmed_cells)
        empty |= {int(item["cell_id"]) for item in annotation.sampled_cells_truth
                  if isinstance(item, dict) and item.get("label") == "EMPTY" and _int(item.get("cell_id")) is not None}
        values["empty_cells"] = frozenset(empty)
    if annotation.combat_state_confirmed:
        values["phase"] = annotation.combat_phase_truth
        values["turn"] = annotation.turn_owner_truth
    return FrameTruth(**values)


# ------------------------------------------------------------------ issues par domaine
def _scalar(truth: object, predicted: object, recorded: bool) -> str:
    if truth is None:
        return NO_TRUTH
    if not recorded:
        return NOT_RECORDED
    if predicted is None or predicted == UNKNOWN:
        return ABSTAINED
    return CORRECT if predicted == truth else WRONG


def frame_outcomes(frame: Frame) -> dict[str, dict[str, object]]:
    """Issue de chaque domaine pour une frame, avec les détails utiles au rapport."""
    truth, prediction = frame.truth, frame.prediction
    result: dict[str, dict[str, object]] = {}
    # Map : seule une map RESOLVED est une affirmation ; AMBIGUOUS/UNKNOWN/TRANSITION sont des refus.
    result["map"] = {"outcome": _scalar(truth.map_id, prediction.map_id if prediction.map_status == "RESOLVED"
                                        else None, prediction.map_status is not None),
                     "declared_unverified": truth.map_declared_unverified}
    combat = {"COMBAT": True, "EXPLORATION": False}.get(prediction.combat_state or "")
    result["combat"] = {"outcome": _scalar(truth.combat, combat, prediction.combat_state is not None)}
    # Joueur : vérité VISIBLE → sa cellule ; NOT_VISIBLE → aucune cellule ne doit être affirmée.
    if not truth.entities_confirmed or truth.player_visible is None:
        player = NO_TRUTH
    elif not prediction.player_recorded:
        player = NOT_RECORDED
    elif truth.player_visible:
        player = _scalar(truth.player_cell, prediction.player_cell, True) if truth.player_cell is not None else NO_TRUTH
    else:
        player = CORRECT if prediction.player_cell is None else WRONG
    result["player"] = {"outcome": player}
    # Ennemis : ensemble exact de cellules ; précision/rappel à part.
    enemies: dict[str, object] = {"outcome": NO_TRUTH, "tp": 0, "fp": 0, "fn": 0}
    if truth.entities_confirmed and truth.enemy_cells is not None:
        if prediction.enemy_cells is None:
            enemies["outcome"] = NOT_RECORDED
        else:
            tp = len(truth.enemy_cells & prediction.enemy_cells)
            enemies.update(tp=tp, fp=len(prediction.enemy_cells - truth.enemy_cells),
                           fn=len(truth.enemy_cells - prediction.enemy_cells))
            enemies["outcome"] = CORRECT if prediction.enemy_cells == truth.enemy_cells else WRONG
    result["enemies"] = enemies
    # Occupation : chaque cellule étiquetée (vide confirmée ou entité) comparée à l'état prédit.
    occupancy: dict[str, object] = {"outcome": NO_TRUTH, "cells": Counter()}
    if truth.entities_confirmed:
        labelled = {cell: "FREE" for cell in truth.empty_cells}
        labelled.update({cell: "OCCUPIED" for cell in (truth.enemy_cells or ())})
        if truth.player_visible and truth.player_cell is not None:
            labelled[truth.player_cell] = "OCCUPIED"
        if labelled:
            if prediction.occupancy is None:
                occupancy["outcome"] = NOT_RECORDED
            else:
                cells: Counter = Counter()
                for cell, expected in labelled.items():
                    state = prediction.occupancy.get(cell, UNKNOWN)
                    cells[ABSTAINED if state == UNKNOWN else CORRECT if state == expected else WRONG] += 1
                occupancy["cells"] = cells
                occupancy["outcome"] = WRONG if cells[WRONG] else ABSTAINED if cells[ABSTAINED] else CORRECT
    result["occupancy"] = occupancy
    result["ap"] = {"outcome": _scalar(truth.ap, prediction.ap, prediction.hud_recorded)}
    result["mp"] = {"outcome": _scalar(truth.mp, prediction.mp, prediction.hud_recorded)}
    phase_truth = None if truth.phase in (None, UNKNOWN) else truth.phase
    result["phase"] = {"outcome": _scalar(phase_truth, prediction.phase, prediction.phase is not None)}
    # Tour : jugé seulement quand la vérité est FIGHTING avec un propriétaire connu (sémantique 3B-6B).
    turn: dict[str, object] = {"outcome": NO_TRUTH, "dangerous": None}
    if truth.phase == "FIGHTING" and truth.turn not in (None, UNKNOWN):
        shown = prediction.turn if prediction.phase == "FIGHTING" else UNKNOWN
        turn["outcome"] = _scalar(truth.turn, shown, prediction.phase is not None)
        if turn["outcome"] == WRONG:
            turn["dangerous"] = "claimed_my_turn" if shown == "PLAYER" else "missed_my_turn"
    result["turn"] = turn
    return result


# ------------------------------------------------------------------ agrégation
def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def domain_verdict(counts: Counter) -> tuple[str, list[str]]:
    """Règle stricte par frame : aucune vérité ou aucune affirmation → NOT_EVALUABLE ; une erreur → FAIL ;
    uniquement des bonnes réponses → PASS ; bonnes réponses + abstentions/non enregistrées → PARTIAL."""
    with_truth = sum(counts[outcome] for outcome in (CORRECT, WRONG, ABSTAINED, NOT_RECORDED))
    if not with_truth:
        return NOT_EVALUABLE, ["aucune vérité humaine utilisable : rien n'est mesuré"]
    if counts[WRONG]:
        return FAIL, [f"{counts[WRONG]} affirmation(s) fausse(s)"]
    if not counts[CORRECT]:
        return NOT_EVALUABLE, ["vérités présentes mais aucune affirmation : mesure vide, pas un succès"]
    if counts[ABSTAINED] or counts[NOT_RECORDED]:
        return PARTIAL, [f"{counts[ABSTAINED]} abstention(s), {counts[NOT_RECORDED]} champ(s) non enregistré(s)"]
    return PASS, []


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"frames": 0, "mean": None, "median": None, "p95": None, "max": None}
    ordered = sorted(values)

    def percentile(share: float) -> float:
        return round(ordered[min(len(ordered) - 1, max(0, round(share * len(ordered)) - 1))], 2)

    return {"frames": len(ordered), "mean": round(sum(ordered) / len(ordered), 2), "median": percentile(0.5),
            "p95": percentile(0.95), "max": round(ordered[-1], 2)}


def evaluate_frames(frames: Iterable[Frame]) -> dict[str, object]:
    frames = list(frames)
    counts: dict[str, Counter] = {domain: Counter() for domain in DOMAINS}
    by_split: dict[str, dict[str, Counter]] = {}
    enemy_counts = Counter()
    occupancy_cells = Counter()
    dangerous = Counter()
    map_unverified = 0
    wrong_examples: dict[str, list[dict[str, object]]] = {domain: [] for domain in DOMAINS}
    for frame in frames:
        outcomes = frame_outcomes(frame)
        split_counts = by_split.setdefault(frame.split, {domain: Counter() for domain in DOMAINS})
        for domain in DOMAINS:
            outcome = str(outcomes[domain]["outcome"])
            counts[domain][outcome] += 1
            split_counts[domain][outcome] += 1
            if outcome == WRONG and len(wrong_examples[domain]) < 10:
                wrong_examples[domain].append({"observation_id": frame.observation_id,
                                               "session_id": frame.session_id, "frame_index": frame.frame_index})
        enemy_counts.update({key: int(outcomes["enemies"][key]) for key in ("tp", "fp", "fn")})
        occupancy_cells.update(outcomes["occupancy"]["cells"])
        if outcomes["turn"]["dangerous"]:
            dangerous[str(outcomes["turn"]["dangerous"])] += 1
        map_unverified += bool(outcomes["map"]["declared_unverified"])
    domains: dict[str, dict[str, object]] = {}
    for domain in DOMAINS:
        c = counts[domain]
        with_truth = sum(c[outcome] for outcome in (CORRECT, WRONG, ABSTAINED, NOT_RECORDED))
        asserted = c[CORRECT] + c[WRONG]
        status, notes = domain_verdict(c)
        metrics: dict[str, object] = {
            "frames_with_truth": with_truth, "frames_without_truth": c[NO_TRUTH],
            **{outcome.lower(): c[outcome] for outcome in (CORRECT, WRONG, ABSTAINED, NOT_RECORDED)},
            "precision": _rate(c[CORRECT], asserted), "coverage": _rate(asserted, with_truth),
            "unknown_rate": _rate(c[ABSTAINED], with_truth),
        }
        domains[domain] = {"status": status, "metrics": metrics, "notes": notes,
                           "wrong_examples": wrong_examples[domain]}
    enemies = domains["enemies"]["metrics"]
    enemies["cell_precision"] = _rate(enemy_counts["tp"], enemy_counts["tp"] + enemy_counts["fp"])
    enemies["cell_recall"] = _rate(enemy_counts["tp"], enemy_counts["tp"] + enemy_counts["fn"])
    labelled = sum(occupancy_cells.values())
    domains["occupancy"]["metrics"].update({
        "labelled_cells": labelled, "cell_correct": occupancy_cells[CORRECT], "cell_wrong": occupancy_cells[WRONG],
        "cell_unknown": occupancy_cells[ABSTAINED], "cell_unknown_rate": _rate(occupancy_cells[ABSTAINED], labelled)})
    domains["turn"]["metrics"]["dangerous_claimed_my_turn"] = dangerous["claimed_my_turn"]
    domains["turn"]["metrics"]["dangerous_missed_my_turn"] = dangerous["missed_my_turn"]
    if dangerous["claimed_my_turn"]:
        domains["turn"]["notes"].insert(0, "« mon tour » affirmé à tort : erreur dangereuse")
    if map_unverified:
        domains["map"]["notes"].append(f"{map_unverified} frame(s) avec mapId déclaré mais non vérifié par /mapid : "
                                       "non comptées comme vérité")
    for domain in INFO_DOMAINS:
        attribute = {"grid_source": "grid_source", "grid_visibility": "grid_visibility", "alignment": "alignment"}[domain]
        distribution = Counter(getattr(frame.prediction, attribute) or "NON_ENREGISTRÉ" for frame in frames)
        domains[domain] = {"status": INFO, "metrics": {"distribution": dict(sorted(distribution.items()))},
                           "notes": ["distribution seulement : aucune vérité par frame dans ce corpus"]}
    for domain, reason in NOT_EVALUABLE_DOMAINS.items():
        domains[domain] = {"status": NOT_EVALUABLE, "metrics": {}, "notes": [reason]}
    latency: dict[str, object] = {}
    for stage in LATENCY_STAGES:
        latency[stage] = _stats([frame.prediction.stage_ms[stage] for frame in frames
                                 if stage in frame.prediction.stage_ms])
    judged = [item["status"] for item in domains.values() if item["status"] != INFO]
    if FAIL in judged:
        overall = FAIL
    elif NOT_EVALUABLE in judged:
        overall = INCOMPLETE
    elif judged and all(status == PASS for status in judged):
        overall = PASS
    else:
        overall = PARTIAL
    return {
        "frames": len(frames),
        "splits": {split: {domain: dict(counter) for domain, counter in values.items()}
                   for split, values in sorted(by_split.items())},
        "domains": domains,
        "latency_ms": latency,
        "end_to_end_ms": latency["total"],
        "overall": overall,
    }
