"""Validation offline du client réel : exports sous data/gamedata/real-validation/.

Usage : python scripts/validate_gamedata_real.py --client DOSSIER_CLIENT
Lecture seule du client ; aucune copie de D2P/DLM/D2O ni d'asset graphique.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time
import tracemalloc

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from combatbot import __version__
from combatbot.gamedata import LocalGameDataProvider
from combatbot.gamedata.topology import cell_to_grid, neighbors
from combatbot.gamedata.validation import MapValidator, cross_check_d2o, geometry_selfcheck, topology_decision


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=root / "data" / "gamedata" / "real-validation")
    parser.add_argument("--samples", type=int, nargs="*", default=None, help="map IDs exportés en détail")
    args = parser.parse_args()
    output, client = args.output.resolve(), args.client.resolve()
    if output.is_relative_to(client):
        raise SystemExit("Sortie interdite dans le dossier client")
    cache = output / "cache"
    for stale in cache.glob("index_*.json"):
        stale.unlink()  # cold measurement: removes our own index cache only
    perf = {}
    cold = LocalGameDataProvider(client, cache_dir=cache)
    report = cold.scan_client()
    perf["scan_initial_seconds"], perf["index_build_seconds"] = report.scan_seconds, report.index_seconds
    perf["scan_initial_python_peak_bytes"] = report.python_peak_bytes
    provider = LocalGameDataProvider(client, cache_dir=cache)
    cached = provider.scan_client()
    perf["scan_cached_seconds"], perf["index_cached_seconds"] = cached.scan_seconds, cached.index_seconds
    perf["scan_cached_python_peak_bytes"] = cached.python_peak_bytes
    perf["index_cache_hit"] = cached.index_cache_hit

    files = cached.files
    write(output / "inventory.json", {
        "client_path": cached.client_path, "files": len(files),
        "by_extension": dict(Counter(f.extension or "(none)" for f in files).most_common()),
        "d2p": [{"path": f.relative_path, "size": f.size} for f in files if f.extension == ".d2p"],
        "d2o": [{"path": f.relative_path, "size": f.size} for f in files if f.extension == ".d2o"],
        "declared_version": cached.detected_version, "version_evidence": cached.version_evidence,
        "errors": cached.errors,
    })
    layouts = []
    for path, detail in sorted(cached.format_details.items()):
        if detail.get("format") != "D2P 2.1":
            continue
        regions = detail["regions"]
        layouts.append({"path": path, "size": regions["file_size"], "variant": detail["layout"],
                        "footer": detail["footer"], "entries": detail["entry_count"],
                        "dlm_entries": detail["dlm_entries"], "properties": detail["properties"],
                        "data_region": regions["data_region"], "index_region": regions["index_region"],
                        "property_region": regions["property_region"],
                        "data_unreferenced_bytes": detail["data_unreferenced_bytes"]})
    d2p_errors = [e for e in cached.errors if ".d2p" in e]
    write(output / "d2p-layouts.json", {"archives": layouts, "errors": d2p_errors,
                                        "variants": dict(Counter(a["variant"] for a in layouts))})

    ids = provider.list_maps()
    sample = next(i for i in ids if i)
    tracemalloc.start()
    started = time.perf_counter()
    provider.get_map(sample)
    perf["map_cold_seconds"] = time.perf_counter() - started
    perf["map_cold_python_peak_bytes"] = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    started = time.perf_counter()
    provider.get_map(sample)
    perf["map_cached_seconds"] = time.perf_counter() - started

    # tracemalloc slows Python ~10x: memory on a subset, timing on the full run.
    tracemalloc.start()
    subset = MapValidator(provider).run(ids[:500])
    perf["validate_500_python_peak_bytes"] = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    perf["validate_500_seconds_under_tracemalloc"] = subset["seconds"]
    validator = MapValidator(provider)
    maps = validator.run()
    perf["validate_all_seconds"] = maps["seconds"]
    selfcheck = geometry_selfcheck()
    decision = topology_decision(selfcheck, maps)
    cross = cross_check_d2o(client, validator.per_map)
    write(output / "map-validation.json", {"maps": maps, "geometry_selfcheck": selfcheck,
                                          "topology_decision": decision, "d2o_cross_check": cross})

    per_map = validator.per_map
    chosen = list(args.samples or [])
    if not chosen:
        with_hints = sorted((m for m, r in per_map.items() if r["red"] and r["blue"]), key=lambda m: (m % 97, m))
        without = sorted((m for m, r in per_map.items() if not r["red"] and not r["blue"]), key=lambda m: (m % 89, m))
        chosen = with_hints[:7] + without[:3]
    for map_id in chosen:
        value = provider.get_map(map_id).to_dict()
        for cell in value["cells"]:
            coordinate = cell_to_grid(cell["cell_id"])
            cell["logical_xy"] = [coordinate.x, coordinate.y]
            cell["neighbours_4"] = [int(n) for n in neighbors(cell["cell_id"])]
        value["topology_note"] = "logical_xy/neighbours_4 : formule logique ; aucune projection écran."
        write(output / f"map_{map_id}.json", value)

    stages = maps["stages"]
    summary = {
        "pythonbot_version": __version__, "client_path": str(client),
        "d2p_files": sum(f.extension == ".d2p" for f in files),
        "d2o_files": sum(f.extension == ".d2o" for f in files),
        "d2p_layouts": dict(Counter(a["variant"] for a in layouts)),
        "d2p_errors": len(d2p_errors),
        "map_archives": [a["path"] for a in layouts if a["dlm_entries"]],
        "levels": {
            "ARCHIVE_RECONNUE": stages.get("archive_recognised"),
            "ENTRY_INDEXEE": stages.get("entry_indexed"),
            "DLM_EXTRAIT": stages.get("dlm_extracted"),
            "DLM_PARSE": stages.get("dlm_parsed"),
            "CELLULES_PARSEES": stages.get("cells_parsed"),
            "TOPOLOGIE_LOGIQUE_DEMONTREE": decision["logical_topology_verified"],
            "PROJECTION_ECRAN_DEMONTREE": False,
            "FIGHT_CELLS_DEMONTREES": False,
        },
        "dlm_versions": maps["versions"],
        "encrypted_maps": sum(e["maps"] for e in maps["envelopes"] if e["encrypted"]),
        "cell_counts": maps["cell_counts"],
        "failures": [{k: f[k] for k in ("map_id", "source", "code")} for f in maps["failures"]],
        "tactical_mode_template_ids": maps["tactical_mode_template_ids"],
        "d2o_cross_check": {k: {kk: vv for kk, vv in v.items() if kk != "fields"} for k, v in cross.items()},
        "topology": decision, "performance_real_client": perf, "exported_maps": chosen,
    }
    write(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
