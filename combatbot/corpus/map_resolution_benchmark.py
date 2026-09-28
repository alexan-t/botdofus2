"""LOT 3B-6C : benchmark offline de la résolution automatique de map, sans aucune action DOFUS.

Les frames du corpus sont rejouées dans l'ordre de chaque session, comme en direct (le service
démarre sans map connue à chaque session). La map déclarée historiquement (/mapid) sert
UNIQUEMENT au score : elle n'est jamais transmise au résolveur. Critère principal : 0 mauvaise map.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
import statistics
import time

import cv2

from combatbot.corpus.repository import CorpusRepository
from combatbot.gamedata.map_index import MapSpatialIndex
from combatbot.vision.map_reader import MapCoordinateReader
from combatbot.vision.map_resolver import MapContextResolver, MapContextService, MapKnowledge

# Les frames du corpus sont le crop combat (haut du client) : même coin haut-gauche, hauteur réduite.
CORPUS_ROI = (0.0, 0.0, 0.30, 0.16)


def run_map_resolution_benchmark(repository: CorpusRepository, index: MapSpatialIndex, *,
                                 reader: MapCoordinateReader | None = None, limit: int | None = None,
                                 simulate_human_confirmation: bool = False, shapes=None,
                                 track_hypotheses: bool = True) -> dict:
    """``simulate_human_confirmation`` : scénario séparé où l'humain résout UNE fois chaque lieu
    ambigu (la vérité ne sert qu'à simuler cette réponse) ; on mesure ensuite si les frames
    suivantes se résolvent seules par l'empreinte mémorisée, sans jamais de mauvaise map."""
    reader = reader or MapCoordinateReader(roi=CORPUS_ROI)
    knowledge = MapKnowledge()          # connaissance vierge : aucune fuite depuis le runtime
    sessions: dict[str, list] = defaultdict(list)
    for entry in repository.list_entries():
        sessions[entry.session_id].append(entry)
    counts = Counter()
    by_map: dict[str, Counter] = defaultdict(Counter)
    wrong: list[dict] = []
    frames_to_resolve: list[int] = []
    reader_ms: list[float] = []
    resolver_ms: list[float] = []
    sources = Counter()
    processed = 0
    for session_id in sorted(sessions):
        service = MapContextService(MapContextResolver(index, knowledge, shapes=shapes), reader=reader,
                                    interval=0.0, synchronous=True, track_hypotheses=track_hypotheses)
        first_resolved = None
        for position, entry in enumerate(sorted(sessions[session_id], key=lambda item: item.frame_index)):
            if limit is not None and processed >= limit:
                break
            processed += 1
            document = repository.read_observation(entry)
            truth = (document.get("grid_snapshot") or {}).get("map_id_declared")
            image = cv2.imread(str(repository.resolve(entry.paths["frame"])))
            if image is None:
                counts["unreadable_frame"] += 1
                continue
            started = time.perf_counter()
            resolution = service.update(image, image, now=float(position) * 2.5,
                                        cell_centers=_cell_centers(document) if shapes is not None else None)
            reader_ms.append(service.timings.get("coordinate_reader_ms", 0.0))
            resolver_ms.append(service.timings.get("resolver_ms", 0.0))
            status = resolution.status.value
            if simulate_human_confirmation and status == "AMBIGUOUS" and truth in resolution.candidates:
                key = service._accepted.key if service._accepted is not None else None
                if key is not None and truth not in knowledge.confirmed_maps(key):
                    from combatbot.vision.map_resolver import compute_fingerprint
                    knowledge.confirm(key, truth, layout=None, context=None)
                    knowledge.learn(truth, compute_fingerprint(image), source="simulated_human", layout=None)
                    counts["simulated_human_confirmations"] += 1
            label = "no_truth" if truth is None else str(truth)
            if resolution.status.value == "RESOLVED":
                sources[resolution.source] += 1
                if first_resolved is None:
                    first_resolved = position
                if truth is None:
                    outcome = "resolved_without_truth"
                elif resolution.map_id == truth:
                    outcome = "correct"
                else:
                    outcome = "WRONG"
                    wrong.append({"session": session_id, "frame": entry.frame_index, "truth": truth,
                                  "resolved": resolution.map_id, "source": resolution.source,
                                  "reason": resolution.reason, "coordinates": resolution.coordinates})
            else:
                outcome = status.lower()
            counts[outcome] += 1
            by_map[label][outcome] += 1
            counts["latency_total_ms"] += 0
            _ = started
        if first_resolved is not None:
            frames_to_resolve.append(first_resolved + 1)
        service.close()
    counts.pop("latency_total_ms", None)
    with_truth = sum(value for key, value in counts.items() if key not in ("resolved_without_truth", "no_truth"))
    truth_frames = sum(sum(counter.values()) for label, counter in by_map.items() if label != "no_truth")
    accepted = counts["correct"] + counts["WRONG"]
    return {
        "scenario": "one_simulated_human_confirmation_per_ambiguous_place" if simulate_human_confirmation
        else "fully_automatic",
        "screen_shape": shapes is not None, "hypotheses": track_hypotheses,
        "frames": processed,
        "frames_with_truth": truth_frames,
        "outcomes": dict(counts),
        "wrong_maps": counts["WRONG"],
        "wrong_details": wrong,
        "accepted_precision": (counts["correct"] / accepted) if accepted else None,
        "coverage": (counts["correct"] / truth_frames) if truth_frames else None,
        "by_map": {key: dict(value) for key, value in sorted(by_map.items())},
        "sources": dict(sources),
        "sessions": len(sessions),
        "frames_to_first_resolution": frames_to_resolve,
        "coordinate_reader_ms_median": statistics.median(reader_ms) if reader_ms else None,
        "resolver_ms_median": statistics.median(resolver_ms) if resolver_ms else None,
        "_unused": with_truth,
    }


def _cell_centers(document: dict) -> dict[int, tuple[float, float]] | None:
    """Centres des cases projetés au moment de la capture (grille GameData calibrée), sinon None."""
    cells = (document.get("grid_snapshot") or {}).get("cells")
    if isinstance(cells, str):
        try:
            cells = ast.literal_eval(cells)
        except (ValueError, SyntaxError):
            return None
    if not isinstance(cells, list):
        return None
    centers = {int(cell["cell_id"]): (float(cell["center"][0]), float(cell["center"][1]))
               for cell in cells if cell.get("cell_id") is not None and cell.get("center")}
    return centers or None


def markdown_summary(report: dict) -> str:
    outcomes = report["outcomes"]
    lines = ["# Benchmark résolution de map (3B-6C)", "", f"Scénario : {report.get('scenario')}",
             f"Forme à l'écran : {report.get('screen_shape')} · hypothèses : {report.get('hypotheses')}", "",
             f"- Frames : {report['frames']} (avec vérité : {report['frames_with_truth']})",
             f"- Correctes : {outcomes.get('correct', 0)} · **Mauvaises : {report['wrong_maps']}** · "
             f"Ambiguës : {outcomes.get('ambiguous', 0)} · Inconnues : {outcomes.get('unknown', 0)} · "
             f"Transition : {outcomes.get('transition', 0)} · Incohérentes : {outcomes.get('inconsistent', 0)}",
             f"- Précision acceptée : {report['accepted_precision']}",
             f"- Couverture : {report['coverage']}",
             f"- Frames avant 1ʳᵉ résolution par session : {report['frames_to_first_resolution']}",
             f"- Lecteur (médiane) : {report['coordinate_reader_ms_median']} ms · résolveur : "
             f"{report['resolver_ms_median']} ms", "", "| Map (vérité) | Issues |", "|---|---|"]
    lines += [f"| {key} | {value} |" for key, value in report["by_map"].items()]
    return "\n".join(lines) + "\n"
