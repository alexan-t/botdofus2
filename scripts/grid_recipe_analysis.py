"""Analyse hors ligne d'une session de recette (LOT 3B-2R), sans nouvelle capture.

- stale map simulé : chaque frame placement/combat de la map k est réévaluée avec la
  topologie de la map précédente de la session (ce que le résolveur aurait calculé si
  l'utilisateur n'avait pas redéclaré la map) ;
- stabilité entre frames d'une même map : dérive des réseaux ajustés (alignés sur T) ;
- ambiguïté ±1 cellule : scores du transform appliqué et de ses décalages ±U/±V.
Les mesures 0.4.0 ne sont pas modifiées ; seuils inchangés.
"""
import argparse
from itertools import combinations
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

import cv2  # noqa: E402

from combatbot.vision.gamedata_grid import GameDataTopologySource, projected_observation  # noqa: E402
from combatbot.vision.grid_fit import MIN_TOPOLOGY_CONSISTENCY, candidate_union, fit_grid_from_candidates  # noqa: E402
from combatbot.vision.grid_projection import GridProjector, GridScreenTransform  # noqa: E402
from combatbot.vision.grid_recipe import RealGridValidationSession, _stats, transform_drift  # noqa: E402
from combatbot.vision.coordinates import CombatPoint  # noqa: E402

DATA = root / "data" / "validation" / "grid-real"


def aligned_fit(image, topology, applied: GridScreenTransform):
    fit = fit_grid_from_candidates(candidate_union(image), (image.shape[1], image.shape[0]), topology=topology)
    if fit.transform is None:
        return None
    t = fit.transform
    fx, fy = applied.combat_to_fractional_grid(t.origin)
    ix, iy = round(fx), round(fy)
    origin = CombatPoint(t.origin.x - ix * t.basis_x.x - iy * t.basis_y.x, t.origin.y - ix * t.basis_x.y - iy * t.basis_y.y)
    return GridScreenTransform(origin, t.basis_x, t.basis_y, t.reference_combat_size)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True)
    parser.add_argument("--client", default=r"C:\Users\Thoma\AppData\Local\Alea\Client")
    args = parser.parse_args()
    session = RealGridValidationSession.load(DATA, args.session)
    source = GameDataTopologySource.for_client(args.client, root / "data" / "gamedata" / "cache")
    order = [m["map_id"] for m in session.maps]
    gridded = [c for c in session.captures
               if not c["stale_map"] and c.get("context", {}).get("mode") in ("placement", "combat")]
    images = {c["capture_id"]: cv2.imread(str(session.directory / c["files"]["combat"])) for c in gridded}

    # 1. Stale map simulated on real gridded frames (previous map of the session).
    stale = []
    for capture in gridded:
        index = order.index(capture["map_id"])
        applied = session.transform(capture["transform_id"])
        image = images[capture["capture_id"]]
        correct = projected_observation(GridProjector(applied).project(source.topology(capture["map_id"])), image)
        others = [m for m in order if m != capture["map_id"]]
        previous = order[index - 1] if index > 0 else others[0]
        rows = []
        for other in others:
            grid = projected_observation(GridProjector(applied).project(source.topology(other)), image)
            rows.append({"declared_map": other, "topology_consistency": grid.topology_consistency,
                         "is_previous": other == previous})
        stale.append({"capture_id": capture["capture_id"], "map_id": capture["map_id"], "mode": capture["context"]["mode"],
                      "correct_consistency": correct.topology_consistency, "previous_map": previous,
                      "previous_consistency": next(r["topology_consistency"] for r in rows if r["is_previous"]),
                      "all_other_maps": rows})
    correct_values = [s["correct_consistency"] for s in stale]
    previous_values = [s["previous_consistency"] for s in stale]
    all_wrong = [r["topology_consistency"] for s in stale for r in s["all_other_maps"]]
    flagged = lambda values: sum(v is not None and v < MIN_TOPOLOGY_CONSISTENCY for v in values)  # noqa: E731
    stale_summary = {
        "frames": len(stale), "threshold_0_4_0": MIN_TOPOLOGY_CONSISTENCY,
        "correct_map": _stats(correct_values), "previous_map": _stats(previous_values),
        "any_other_map": _stats(all_wrong),
        "correct_map_flagged_suspect": flagged(correct_values),
        "previous_map_flagged_suspect": flagged(previous_values),
        "other_map_pairs_flagged_suspect": f"{flagged(all_wrong)}/{len(all_wrong)}",
        "per_frame_drop_previous": [s["correct_consistency"] - s["previous_consistency"] for s in stale],
    }

    # 2. Frame-to-frame stability within a map (fitted lattices aligned on the applied transform).
    stability = []
    for map_id in order:
        frames = [c for c in gridded if c["map_id"] == map_id]
        if len(frames) < 2:
            continue
        topology = source.topology(map_id)
        fitted = {c["capture_id"]: aligned_fit(images[c["capture_id"]], topology, session.transform(c["transform_id"]))
                  for c in frames}
        for a, b in combinations(frames, 2):
            ta, tb = fitted[a["capture_id"]], fitted[b["capture_id"]]
            if ta is None or tb is None:
                continue
            stability.append({"map_id": map_id, "pair": [a["capture_id"], b["capture_id"]], **transform_drift(ta, tb)})

    # 3. One-cell shift ambiguity per gridded frame.
    shifts = [{"capture_id": c["capture_id"], "map_id": c["map_id"], "mode": c["context"]["mode"],
               "applied_score": (c["metrics"]["applied_score"] or {}).get("score"),
               "best_shifted_u": c["metrics"]["best_shifted_u_score"], "best_shifted_v": c["metrics"]["best_shifted_v_score"],
               "margin_vs_shift": c["metrics"]["margin_vs_shift"], "fit_status": c["metrics"]["fit_status"],
               "fit_margin": c["metrics"]["score_margin"], "integer_offset": c["metrics"]["integer_offset"]}
              for c in gridded]
    result = {
        "session_id": session.session_id, "gridded_frames": len(gridded),
        "stale_simulated": stale_summary, "stale_simulated_frames": stale,
        "frame_stability_pairs": stability,
        "frame_stability": {key: _stats([s[key] for s in stability])
                            for key in ("origin_px", "basis_x_px", "basis_y_px", "center_mean_px", "center_max_px")},
        "shift_ambiguity": shifts,
        "shift_ambiguity_summary": {"margin_vs_shift": _stats([s["margin_vs_shift"] for s in shifts]),
                                    "applied_best_or_tied": sum((s["margin_vs_shift"] or 0) >= 0 for s in shifts),
                                    "frames": len(shifts)},
        "residual_median_gridded": _stats([c["metrics"]["residual_median_px"] for c in gridded]),
        "residual_mean_gridded": _stats([c["metrics"]["residual_mean_px"] for c in gridded]),
        "residual_max_gridded": _stats([c["metrics"]["residual_max_px"] for c in gridded]),
        "lattice_vs_applied_center_max": _stats([c["metrics"]["lattice_drift"]["center_max_px"]
                                                 for c in gridded if c["metrics"]["lattice_drift"]]),
        "cell_size_fitted": {"width": _stats([c["metrics"]["fitted_cell_width_px"] for c in gridded]),
                             "height": _stats([c["metrics"]["fitted_cell_height_px"] for c in gridded])},
    }
    path = session.directory / "analysis.json"
    path.write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("stale_simulated_frames", "shift_ambiguity",
                                                                   "frame_stability_pairs")},
                     indent=1, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
