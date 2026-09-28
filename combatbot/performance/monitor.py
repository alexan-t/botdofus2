"""Surveillance légère et désactivable : échantillons de ressources, retard de la boucle UI, tendance.

``classify_growth`` ne conclut jamais « fuite » sur une simple hausse : une mémoire qui monte pendant
l'échauffement puis se stabilise est un PLATEAU. GROWING exige, **après** l'échauffement, une hausse
nette supérieure au bruit mesuré (2 écarts types des résidus et 1 % de la moyenne) ET une montée
majoritairement monotone. Aucun seuil absolu de RAM n'est utilisé.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

from combatbot.performance.metrics import ProcessSnapshot, cpu_percent, process_snapshot


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, round(fraction * len(ordered)) - 1))], 2)


def classify_growth(times: list[float], values: list[float | None], warmup_s: float) -> dict[str, object]:
    points = [(t, v) for t, v in zip(times, values) if v is not None]
    if not points:
        return {"verdict": "NOT_MEASURED"}
    start = points[0][0]
    post = [(t, v) for t, v in points if t - start >= warmup_s]
    result: dict[str, object] = {"start": points[0][1], "end": points[-1][1],
                                 "max": max(v for _t, v in points), "samples_after_warmup": len(post)}
    if len(post) < 6:
        return {**result, "verdict": "INSUFFICIENT"}
    xs, ys = [t for t, _v in post], [v for _t, v in post]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    variance = sum((x - mean_x) ** 2 for x in xs) or 1e-9
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / variance
    residuals = [y - (mean_y + slope * (x - mean_x)) for x, y in zip(xs, ys)]
    noise = statistics.pstdev(residuals)
    quarter = max(1, len(ys) // 4)
    delta = statistics.fmean(ys[-quarter:]) - statistics.fmean(ys[:quarter])
    rising = sum(b > a for a, b in zip(ys, ys[1:])) / (len(ys) - 1)
    floor = max(2 * noise, 0.01 * abs(mean_y))
    median = statistics.median(ys)
    mad = statistics.median(abs(y - median) for y in ys) or 1e-9
    result.update({"slope_per_min": round(slope * 60, 4), "delta_after_warmup": round(delta, 3),
                   "noise": round(noise, 4), "rising_fraction": round(rising, 2),
                   "spikes": sum(abs(y - median) > 6 * mad and abs(y - median) > floor for y in ys)})
    if delta <= floor:
        verdict = "STABLE" if abs(post[0][1] - points[0][1]) <= floor else "PLATEAU_AFTER_WARMUP"
    elif rising >= 0.6 and slope > 0:
        verdict = "GROWING"
    else:
        verdict = "DRIFT_INCONCLUSIVE"
    return {**result, "verdict": verdict}


@dataclass
class ResourceSampler:
    """Échantillonne le processus ; ``extra`` porte les mesures applicatives de la fenêtre écoulée."""
    samples: list[dict[str, object]] = field(default_factory=list)
    _previous: ProcessSnapshot | None = None
    _origin: float = field(default_factory=time.monotonic)

    def sample(self, **extra: object) -> dict[str, object]:
        snapshot = process_snapshot()
        row = {"t": round(snapshot.wall - self._origin, 2), **snapshot.to_dict(),
               "cpu_percent": cpu_percent(self._previous, snapshot) if self._previous else None, **extra}
        row.pop("wall", None)
        self._previous = snapshot
        self.samples.append(row)
        return row


class UiLagProbe:
    """Retard du thread UI : un QTimer de 50 ms qui mesure de combien chaque tick arrive en retard."""

    INTERVAL_MS = 50

    def __init__(self) -> None:
        from PySide6.QtCore import QTimer
        self.timer = QTimer()
        self.timer.setInterval(self.INTERVAL_MS)
        self.timer.timeout.connect(self._tick)
        self._last: float | None = None
        self.lags_ms: list[float] = []

    def start(self) -> None:
        self._last = time.perf_counter()
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def _tick(self) -> None:
        now = time.perf_counter()
        if self._last is not None:
            self.lags_ms.append(max(0.0, (now - self._last) * 1000 - self.INTERVAL_MS))
        self._last = now

    def drain(self) -> dict[str, float | None]:
        lags, self.lags_ms = self.lags_ms, []
        return {"ui_lag_p95_ms": percentile(lags, 0.95), "ui_lag_max_ms": round(max(lags), 2) if lags else None}
