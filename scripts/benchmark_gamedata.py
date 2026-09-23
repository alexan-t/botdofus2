import json
from pathlib import Path
import runpy
import sys
import time
import tracemalloc
from uuid import uuid4

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
helpers = runpy.run_path(str(root / "tests" / "test_gamedata.py"))
from combatbot.gamedata import LocalGameDataProvider

output = root / "data" / "gamedata" / ("synthetic-" + uuid4().hex[:8])
client = output / "client"
client.mkdir(parents=True)
payload = helpers["make_d2p"]([(f"{i}.dlm", helpers["make_dlm"](i)) for i in range(1000)])
(client / "synthetic.d2p").write_bytes(payload)
provider = LocalGameDataProvider(client, cache_dir=output / "cache")
initial = provider.scan_client()
second = LocalGameDataProvider(client, cache_dir=output / "cache")
cached = second.scan_client()
tracemalloc.start()
cold_start = time.perf_counter()
game_map = second.get_map(123)
cold_seconds = time.perf_counter() - cold_start
map_peak = tracemalloc.get_traced_memory()[1]
tracemalloc.stop()
hot_start = time.perf_counter()
second.get_map(123)
hot_seconds = time.perf_counter() - hot_start
result = {
    "synthetic_only": True, "compatibility_2645_verified": False,
    "python": sys.version, "candidate_maps": len(second.list_maps()),
    "parsed_maps": second.report.readable_maps, "cells_per_parsed_map": len(game_map.cells),
    "archive_bytes": len(payload), "scan_initial_seconds": initial.scan_seconds,
    "build_index_seconds": initial.index_seconds, "scan_with_cached_index_seconds": cached.scan_seconds,
    "read_cached_index_seconds": cached.index_seconds,
    "map_cold_seconds": cold_seconds, "map_hot_seconds": hot_seconds,
    "scan_python_peak_bytes": initial.python_peak_bytes,
    "cached_scan_python_peak_bytes": cached.python_peak_bytes,
    "map_python_peak_bytes": map_peak,
}
destination = root / "data" / "gamedata" / "benchmark-synthetic.json"
destination.write_text(json.dumps(result, indent=2), encoding="utf-8")
second.export_report(output / "probe-report.json")
print(json.dumps(result, indent=2))
