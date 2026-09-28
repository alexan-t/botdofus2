"""Threads ONNX Runtime de RapidOCR — ``python -m combatbot.benchmark --ocr-threads-benchmark``.

Mesure, pour chaque réglage (défaut du moteur, puis 1, 2, 4 threads), le temps d'une lecture OCR sur
une image de texte synthétique et le CPU du processus pendant les lectures (% d'un cœur). Le choix
éventuel se fait ensuite par ``DOFBOT_OCR_THREADS`` : rien n'est changé automatiquement. RapidOCR
absent → NOT_AVAILABLE (jamais une valeur inventée).
"""
from __future__ import annotations

import os
import time

import numpy as np

from combatbot.performance.metrics import cpu_percent, process_snapshot
from combatbot.performance.monitor import percentile


def sample_image() -> np.ndarray:
    import cv2
    image = np.full((80, 520, 3), 30, np.uint8)
    cv2.putText(image, "Temple du Grand Ougah 4,-12", (10, 52), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (235, 235, 235), 2)
    return image


def run_ocr_threads_benchmark(options=(None, 1, 2, 4), reads: int = 8, factory=None) -> dict[str, object]:
    if factory is None:
        import importlib.util
        if importlib.util.find_spec("rapidocr") is None:
            return {"report": "ocr-threads", "verdict": "NOT_AVAILABLE", "reason": "module rapidocr absent",
                    "rows": []}
        from combatbot.vision.tooltip import create_ocr_engine
        factory = create_ocr_engine
    image = sample_image()
    rows = []
    for threads in options:
        started = time.perf_counter()
        engine = factory(threads)
        load_ms = (time.perf_counter() - started) * 1000
        engine(image)                                        # échauffement
        before = process_snapshot()
        timings = []
        for _ in range(reads):
            started = time.perf_counter()
            engine(image)
            timings.append((time.perf_counter() - started) * 1000)
        after = process_snapshot()
        rows.append({"threads": threads if threads is not None else "défaut", "load_ms": round(load_ms, 1),
                     "read_median_ms": percentile(timings, 0.5), "read_p95_ms": percentile(timings, 0.95),
                     "cpu_percent_one_core": cpu_percent(before, after)})
    return {"report": "ocr-threads", "verdict": "MEASURED", "cpu_count": os.cpu_count(), "reads": reads,
            "rows": rows, "note": "Choisir un réglage : DOFBOT_OCR_THREADS=N ; rien n'est appliqué automatiquement."}


def markdown_report(report: dict) -> str:
    lines = ["# RapidOCR : threads ONNX Runtime", ""]
    if report["verdict"] != "MEASURED":
        return "\n".join(lines + [f"Non mesuré : {report.get('reason')}"]) + "\n"
    lines += [f"{report['cpu_count']} cœurs logiques · {report['reads']} lectures par réglage", "",
              "| Threads | Chargement ms | Lecture médiane ms | p95 ms | CPU (% d'un cœur) |", "|---|---|---|---|---|"]
    lines += [f"| {row['threads']} | {row['load_ms']} | {row['read_median_ms']} | {row['read_p95_ms']} "
              f"| {row['cpu_percent_one_core']} |" for row in report["rows"]]
    lines += ["", report["note"]]
    return "\n".join(lines) + "\n"
