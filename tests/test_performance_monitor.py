"""Mesures de ressources et tendance mémoire : une hausse suivie d'un plateau n'est pas une fuite."""
from __future__ import annotations

from combatbot.performance.metrics import cpu_percent, process_snapshot
from combatbot.performance.monitor import ResourceSampler, classify_growth, percentile


def test_process_snapshot_reports_measurable_values_only() -> None:
    snapshot = process_snapshot()
    assert snapshot.rss_mb and snapshot.rss_mb > 0 and snapshot.threads and snapshot.threads >= 1
    assert snapshot.python_heap_mb is None                 # tracemalloc non démarré : pas d'estimation
    later = process_snapshot()
    assert cpu_percent(snapshot, later) is None or cpu_percent(snapshot, later) >= 0


def test_growth_classification() -> None:
    times = [float(t) for t in range(0, 120, 5)]
    assert classify_growth(times, [100.0] * len(times), 30)["verdict"] == "STABLE"
    warm = [100 + min(t, 30) for t in times]                # monte pendant l'échauffement puis se stabilise
    assert classify_growth(times, warm, 30)["verdict"] == "PLATEAU_AFTER_WARMUP"
    leak = [100 + 2 * t for t in times]
    grown = classify_growth(times, leak, 30)
    assert grown["verdict"] == "GROWING" and grown["slope_per_min"] > 0
    assert classify_growth(times[:3], leak[:3], 30)["verdict"] == "INSUFFICIENT"
    assert classify_growth(times, [None] * len(times), 30)["verdict"] == "NOT_MEASURED"
    assert percentile([1, 2, 3, 4], 0.5) == 2 and percentile([], 0.5) is None


def test_sampler_adds_cpu_after_the_first_sample() -> None:
    sampler = ResourceSampler()
    first, second = sampler.sample(phase="x"), sampler.sample(phase="x")
    assert first["cpu_percent"] is None and "rss_mb" in second and second["phase"] == "x"
