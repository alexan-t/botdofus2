"""Mesures LOT 3B-2 : topologie GameData → projection → lookup, sans action.

Usage : python scripts/benchmark_gamedata_grid.py [--client DOSSIER] [--map-id ID]
Sans client, seule la partie synthétique est mesurée. Résultats :
data/benchmarks/gamedata-grid.json (hors dépôt).
"""
import argparse
import json
from pathlib import Path
import random
import statistics
import sys
import tempfile
import time

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

import numpy as np  # noqa: E402

from combatbot import __version__  # noqa: E402
from combatbot.gamedata.models import DofusCellId, GameMapCell, GridTopology  # noqa: E402
from combatbot.gamedata.topology import CELL_COUNT  # noqa: E402
from combatbot.vision.combat_grid import classify_cell_occupancy  # noqa: E402
from combatbot.vision.coordinates import ClientSize, LayoutSignature, NormalizedRect  # noqa: E402
from combatbot.vision.gamedata_grid import (  # noqa: E402
    GameDataGridResolver, GameDataTopologySource, projected_observation,
)
from combatbot.vision.grid_fit import candidate_union, fit_grid_from_candidates  # noqa: E402
from combatbot.vision.grid_profile import CombatGridProfileV2, ManualMapIdentity  # noqa: E402
from combatbot.vision.grid_projection import GridProjector, GridScreenTransform  # noqa: E402


def timed(function, repeat=20):
    values = []
    result = None
    for _ in range(repeat):
        started = time.perf_counter()
        result = function()
        values.append((time.perf_counter() - started) * 1000)
    return result, {"median_ms": statistics.median(values), "max_ms": max(values), "runs": repeat}


def synthetic_topology(seed=1):
    rng = random.Random(seed)
    return GridTopology(1, tuple(GameMapCell(DofusCellId(i), walkable=rng.random() < 0.55,
                                             non_walkable_during_fight=False, line_of_sight=True)
                                 for i in range(CELL_COUNT)))


def render(topology, transform, size):
    import cv2
    image = np.full((size[1], size[0], 3), 32, np.uint8)
    for cell in GridProjector(transform).project(topology).cells:
        if cell.static_traversable_in_fight:
            points = np.array([p.rounded() for p in cell.polygon], np.int32)
            cv2.fillPoly(image, [points], (60, 92, 70))
            cv2.polylines(image, [points], True, (175, 175, 175), 1, cv2.LINE_AA)
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path)
    parser.add_argument("--map-id", type=int, default=87294471)
    args = parser.parse_args()
    # Real-client scale measured on a 2560×1377 capture: cells of ~119 × 60 px.
    size = (2555, 1151)
    transform = GridScreenTransform.from_cell_size((470.0, -20.0), 119.2, 59.6, reference_combat_size=(2555.0, 1151.0))
    result = {"pythonbot_version": __version__, "scale": {"combat_crop": size, "cell_px": [119.2, 59.6]}}
    topology = synthetic_topology()
    if args.client:
        cache = Path(tempfile.mkdtemp(prefix="pythonbot-grid-bench-"))
        started = time.perf_counter()
        source = GameDataTopologySource.for_client(args.client, cache)
        result["topology_source_cold_scan_ms"] = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        source = GameDataTopologySource.for_client(args.client, cache)
        result["topology_source_cached_scan_ms"] = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        topology = source.topology(args.map_id)
        result["topology_load_cold_ms"] = (time.perf_counter() - started) * 1000
        _, result["topology_load_cached"] = timed(lambda: source.topology(args.map_id), 200)
        result["real_map_id"] = args.map_id
    projector, result["projector_init_560"] = timed(lambda: GridProjector(transform), 20)
    projected, result["project_560_cells"] = timed(lambda: projector.project(topology), 50)
    rng = random.Random(2)
    points = [(rng.uniform(0, size[0]), rng.uniform(0, size[1])) for _ in range(20000)]
    started = time.perf_counter()
    hits = sum(projected.pixel_to_cell(p) is not None for p in points)
    elapsed = time.perf_counter() - started
    result["nearest_lookup"] = {"points": len(points), "hits": hits, "us_per_lookup": elapsed / len(points) * 1e6}
    image = render(topology, transform, size)
    grid, result["projected_observation_with_alignment"] = timed(lambda: projected_observation(projected, image), 10)
    _, result["occupancy_classification_560"] = timed(lambda: classify_cell_occupancy(image, grid), 10)
    zones = {"combat": NormalizedRect(0.0, 0.0, 1.0, 0.8)}
    client = ClientSize(2560, 1439)
    profile = CombatGridProfileV2(transform, LayoutSignature.create(client, zones).to_json(), confirmed_by_user=True)

    class Provider:
        def get_map_topology(self, _map_id):
            return topology
    resolver = GameDataGridResolver(profile=profile, topology_source=GameDataTopologySource(Provider()),
                                    map_identity=ManualMapIdentity(1))
    resolution, result["resolver_per_frame"] = timed(lambda: resolver.resolve(image, client, zones), 10)
    result["resolver_cells"] = len(resolution.grid.cells)
    candidates, result["candidate_detection"] = timed(lambda: candidate_union(image), 3)
    fit, result["auto_fit_with_topology"] = timed(
        lambda: fit_grid_from_candidates(candidates, size, topology=topology), 3)
    result["auto_fit_status"] = fit.status.value
    result["auto_fit_candidates"] = len(candidates)
    destination = root / "data" / "benchmarks" / "gamedata-grid.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
