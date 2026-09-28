"""Santé des ressources en session longue — ``python -m combatbot.benchmark --runtime-health``.

Sans DOFUS : la **vraie** fenêtre DofBot2 (Qt, pages avancées, boucle ``_observe_once``, aperçus,
journal) est pilotée avec un observateur synthétique qui rend des paquets de taille réelle (zone de
combat 2100×1000 pour un client 2560×1377). Le travail de vision est simulé par une attente sans CPU :
le CPU mesuré est celui de l'UI, des copies d'images et de l'infrastructure, pas celui de la vision
(mesurée à part par ``--runtime-profile`` sur de vraies sessions). Aucune action n'est envoyée.

Phases : IDLE, OBSERVATION (autre onglet), ADVANCED_OBSERVATION (onglet d'observation visible),
MINIMIZED, puis N cycles START/STOP. Chaque phase : CPU moyen/p95, RSS/privé début/fin/max, threads,
handles, GDI/USER, retard UI p95/max, observation p50/p95, frames traitées/sautées, reconstructions
d'accordéons, tendance mémoire (``classify_growth``).
"""
from __future__ import annotations

import gc
import os
import tempfile
import time
from pathlib import Path

import numpy as np

from combatbot.performance.monitor import ResourceSampler, UiLagProbe, classify_growth, percentile

CLIENT = (2560, 1377)
COMBAT = (2100, 1000)


class _Counters:
    """Remplace le journal de session : compte frames traitées / sautées, latences."""

    def __init__(self) -> None:
        self.frames = self.skipped = self.errors = 0
        self.latencies_ms: list[float] = []

    def record(self, metadata: dict, _observation) -> None:
        self.frames += 1
        if metadata.get("analysis_ms") is not None:
            self.latencies_ms.append(float(metadata["analysis_ms"]))

    def skip(self) -> None:
        self.skipped += 1

    def error(self, _message: str) -> None:
        self.errors += 1

    def close(self):
        return None


class SyntheticObserver:
    """Rend un ObservationPacket de taille réelle ; le « calcul » est une attente (aucun CPU)."""

    def __init__(self, work_s: float = 0.25) -> None:
        from combatbot.vision.combat_models import CombatGridObservation, CombatObservation
        self.work_s = work_s
        self.map_context = None
        self.session_id = "runtime-health"
        rng = np.random.default_rng(0)
        self.base = rng.integers(0, 255, (COMBAT[1], COMBAT[0], 3), dtype=np.uint8)
        self.observation = CombatObservation(True, 0.9, None, 0.0, None, 0.0, (), CombatGridObservation(),
                                             6, 3, 0.9, 0.9, 0.5)
        self.index = 0

    def observe(self):
        from combatbot.vision.combat_models import ObservationPacket
        started = time.perf_counter()
        time.sleep(self.work_s)
        self.index += 1
        original = self.base.copy()                        # comme le pipeline : une frame neuve par tick
        annotated = original.copy()
        annotated[(self.index * 13) % 900:(self.index * 13) % 900 + 60, 100:400] = 255
        elapsed = (time.perf_counter() - started) * 1000
        # Résolution de map qui varie comme en session réelle (transition, ambiguïté, map résolue) :
        # le texte « Map » de l'UI change toutes les 3 frames.
        step = (self.index // 3) % 3
        resolution = ({"status": "TRANSITION", "map_id": None} if step == 0 else
                      {"status": "AMBIGUOUS", "candidate_count": 2 + self.index % 5, "candidates": [1, 2, 3],
                       "coordinates": (self.index % 7, -3)} if step == 1 else
                      {"status": "RESOLVED", "map_id": 153880322 + self.index % 4, "coordinates": (4, -3),
                       "source": "AUTO", "confidence": 0.9})
        return ObservationPacket(self.observation, original, annotated, elapsed,
                                 {"analysis_ms": elapsed, "frame_index": self.index, "stage_ms": {"total": elapsed},
                                  "map_resolution": resolution})


def _pump(app, seconds: float) -> None:
    """Comme la boucle Qt réelle, y compris les suppressions ``deleteLater`` (que ``processEvents`` seul
    n'exécute jamais : sans cela, le harnais conserverait des widgets que l'application libère)."""
    from PySide6.QtCore import QEvent
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.005)


def _qt_census(window, legacy) -> dict[str, int]:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    timers = window.findChildren(QTimer) + (legacy.findChildren(QTimer) if legacy is not None else [])
    return {"qt_timers": len(timers), "qt_timers_active": sum(timer.isActive() for timer in timers),
            "qt_widgets": len(QApplication.allWidgets()),
            "workers_active": legacy.jobs.pool.activeThreadCount() if legacy is not None else 0}


def _packets_alive() -> int:
    """Diagnostic uniquement (gc) : paquets d'observation encore référencés."""
    from combatbot.vision.combat_models import ObservationPacket
    return sum(isinstance(item, ObservationPacket) for item in gc.get_objects())


def _summarize(name: str, rows: list[dict], counters: _Counters, rebuilds: int, warmup_s: float) -> dict[str, object]:
    cpu = [row["cpu_percent"] for row in rows if row.get("cpu_percent") is not None]
    times = [row["t"] for row in rows]

    def edge(key: str) -> dict[str, object]:
        values = [row.get(key) for row in rows if row.get(key) is not None]
        return {"start": values[0], "end": values[-1], "max": max(values)} if values else {"start": None}

    lags = [row["ui_lag_p95_ms"] for row in rows if row.get("ui_lag_p95_ms") is not None]
    lag_max = [row["ui_lag_max_ms"] for row in rows if row.get("ui_lag_max_ms") is not None]
    return {"phase": name, "samples": len(rows),
            "cpu_mean": round(sum(cpu) / len(cpu), 1) if cpu else None, "cpu_p95": percentile(cpu, 0.95),
            "rss_mb": edge("rss_mb"), "private_mb": edge("private_mb"), "threads": edge("threads"),
            "handles": edge("handles"), "gdi_objects": edge("gdi_objects"), "user_objects": edge("user_objects"),
            "python_heap_mb": edge("python_heap_mb"),
            "ui_lag_p95_ms": max(lags) if lags else None, "ui_lag_max_ms": max(lag_max) if lag_max else None,
            "observation_p50_ms": percentile(counters.latencies_ms, 0.5),
            "observation_p95_ms": percentile(counters.latencies_ms, 0.95),
            "frames": counters.frames, "skipped_ticks": counters.skipped, "accordion_rebuilds": rebuilds,
            "rss_growth": classify_growth(times, [row.get("rss_mb") for row in rows], warmup_s),
            "private_growth": classify_growth(times, [row.get("private_mb") for row in rows], warmup_s),
            "heap_growth": classify_growth(times, [row.get("python_heap_mb") for row in rows], warmup_s)}


def _cycle_growth(rows: list[dict], field: str) -> tuple[int, dict[str, object]]:
    """Évalue les cycles après leur première moitié d'échauffement."""
    if not rows:
        return 0, {"verdict": "NOT_MEASURED"}
    times = [row["t"] for row in rows]
    warmup_cycles = len(rows) // 2
    warmup_s = times[warmup_cycles] - times[0] if len(times) >= 2 else 0
    return warmup_cycles, classify_growth(times, [row.get(field) for row in rows], warmup_s)


def run_health(duration_s: float = 60.0, *, sample_every_s: float = 5.0, cycles: int = 20,
               frames_per_cycle: int = 3, work_s: float = 0.25, trace_python: bool = False,
               phases: tuple[str, ...] = ("IDLE", "OBSERVATION", "ADVANCED_OBSERVATION", "MINIMIZED")
               ) -> dict[str, object]:
    import tracemalloc
    from PySide6.QtWidgets import QApplication
    if os.environ.get("QT_QPA_PLATFORM") is None and os.name != "nt" and not os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    workdir = Path(tempfile.mkdtemp(prefix="dofbot2-health-"))
    os.environ["PYTHONBOT_DATA_DIR"] = str(workdir)          # stockage et corpus isolés : rien de réel touché
    from combatbot.storage import Storage
    from combatbot.ui.dofbot2 import theme
    from combatbot.ui.dofbot2.profiles import create_profile
    from combatbot.ui.dofbot2.shell import DofBot2Window
    app = QApplication.instance() or QApplication([])
    theme.load_fonts()
    if trace_python:
        tracemalloc.start()
    storage = Storage(workdir / "health.sqlite3")
    DofBot2Window._connect_legacy = lambda self, hwnd: None   # aucune fenêtre DOFUS
    profile = create_profile(storage, "Santé", 0, "Cra")
    window = DofBot2Window(storage, start_screen="profile")
    window.show()
    window.enter_app(profile.id)
    window.open_advanced()
    view = window.advanced_view
    view.set_recheck(False)
    legacy = window.legacy
    probe = UiLagProbe()
    probe.start()
    sampler = ResourceSampler()
    report: dict[str, object] = {"schema_version": 1, "report": "runtime-health", "duration_s": duration_s,
                                 "sample_every_s": sample_every_s, "client": CLIENT, "combat_crop": COMBAT,
                                 "synthetic_work_s": work_s, "python_tracing": trace_python, "phases": [],
                                 "actions": "NONE"}

    def start_observation() -> _Counters:
        counters = _Counters()
        legacy._telemetry = counters
        legacy._observer = SyntheticObserver(work_s)
        legacy.combat.set_observing(True)
        legacy.observation_timer.start()
        return counters

    def stop_observation() -> None:
        legacy._stop_observation()
        deadline = time.monotonic() + 5
        while legacy.jobs.active and time.monotonic() < deadline:     # le job en vol se termine
            app.processEvents()
            time.sleep(0.01)
        app.processEvents()

    def run_phase(name: str) -> None:
        counters = _Counters()
        if name == "IDLE":
            view.go_tab("connexion", animate=False)
        else:
            counters = start_observation()
            if name == "ADVANCED_OBSERVATION":
                view.go_tab("observation", animate=False)
            else:
                view.go_tab("journal", animate=False)
            if name == "MINIMIZED":
                window.showMinimized()
        pages = list(view.pages.values())
        rebuilds = sum(page.accordion_rebuild_count for page in pages)
        _pump(app, 1.0)
        probe.drain()
        rows = [sampler.sample(**probe.drain(), phase=name)]
        started = time.monotonic()
        while time.monotonic() - started < duration_s:
            _pump(app, min(sample_every_s, duration_s - (time.monotonic() - started)))
            rows.append(sampler.sample(**probe.drain(), phase=name, frames=counters.frames,
                                       skipped=counters.skipped))
        rebuilds = sum(page.accordion_rebuild_count for page in pages) - rebuilds
        if name != "IDLE":
            stop_observation()
        if name == "MINIMIZED":
            window.showNormal()
        report["phases"].append(_summarize(name, rows, counters, rebuilds, warmup_s=min(30.0, duration_s / 4)))

    for name in phases:
        run_phase(name)

    cycle_rows = []
    view.go_tab("observation", animate=False)
    for index in range(cycles):
        counters = start_observation()
        deadline = time.monotonic() + 30
        while counters.frames < frames_per_cycle and time.monotonic() < deadline:
            _pump(app, 0.05)
        stop_observation()
        cycle_rows.append({**sampler.sample(phase="START_STOP"), "cycle": index + 1, "frames": counters.frames,
                           "observer_alive": legacy._observer is not None, "jobs_active": legacy.jobs.active,
                           "packets_alive": _packets_alive(), **_qt_census(window, legacy)})
    # Les premières créations de workers, caches Qt et buffers d'image sont un
    # échauffement normal. Une fuite continue reste visible sur la seconde moitié
    # des cycles ; inclure le warm-up produisait un faux GROWING sous Windows.
    def cycle_growth(field: str) -> dict[str, object]:
        return _cycle_growth(cycle_rows, field)[1]

    report["start_stop"] = {
        "cycles": cycles, "rows": cycle_rows,
        "warmup_cycles": _cycle_growth(cycle_rows, "rss_mb")[0],
        "rss_growth": cycle_growth("rss_mb"),
        "private_growth": cycle_growth("private_mb"),
        "thread_growth": cycle_growth("threads"),
        "handle_growth": cycle_growth("handles"),
        "gdi_growth": cycle_growth("gdi_objects"),
        "user_growth": cycle_growth("user_objects"),
        "threads": [row.get("threads") for row in cycle_rows],
        "qt_timers": [row["qt_timers"] for row in cycle_rows],
        "qt_timers_active": [row["qt_timers_active"] for row in cycle_rows],
        "packets_alive": [row["packets_alive"] for row in cycle_rows],
        "observer_alive_after_stop": any(row["observer_alive"] for row in cycle_rows),
        "jobs_active_after_stop": any(row["jobs_active"] for row in cycle_rows)}
    probe.stop()
    window.close()
    storage.close()
    if trace_python:
        tracemalloc.stop()
    return report


def markdown_report(report: dict) -> str:
    lines = ["# Santé des ressources (runtime-health)", "",
             f"Durée par phase {report['duration_s']} s · échantillon toutes les {report['sample_every_s']} s · "
             f"vision simulée ({report['synthetic_work_s']} s d'attente, sans CPU) · ACTIONS : NONE", "",
             "| Phase | CPU moy/p95 % | RSS début→fin (max) Mo | Privé fin Mo | Threads | Handles | GDI/USER "
             "| UI lag p95/max ms | Obs p50/p95 ms | Frames/sautés | Rebuilds | Tendance RSS |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for phase in report["phases"]:
        rss, threads, handles = phase["rss_mb"], phase["threads"], phase["handles"]
        lines.append(
            f"| {phase['phase']} | {phase['cpu_mean']}/{phase['cpu_p95']} | {rss.get('start')}→{rss.get('end')} "
            f"({rss.get('max')}) | {phase['private_mb'].get('end')} | {threads.get('start')}→{threads.get('end')} "
            f"| {handles.get('start')}→{handles.get('end')} | {phase['gdi_objects'].get('end')}/"
            f"{phase['user_objects'].get('end')} | {phase['ui_lag_p95_ms']}/{phase['ui_lag_max_ms']} "
            f"| {phase['observation_p50_ms']}/{phase['observation_p95_ms']} | {phase['frames']}/{phase['skipped_ticks']} "
            f"| {phase['accordion_rebuilds']} | {phase['rss_growth']['verdict']} |")
    cycles = report["start_stop"]
    lines += ["", f"## {cycles['cycles']} cycles START/STOP", "",
              f"- Warm-up exclu du verdict : {cycles.get('warmup_cycles', 0)} premier(s) cycle(s)",
              f"- RSS : {cycles['rss_growth']['verdict']} (début {cycles['rss_growth'].get('start')} Mo, fin "
              f"{cycles['rss_growth'].get('end')} Mo)",
              f"- Private Bytes : {cycles['private_growth']['verdict']} "
              f"({cycles['private_growth'].get('start')} → {cycles['private_growth'].get('end')} Mo)",
              f"- Handles / GDI / USER : {cycles['handle_growth']['verdict']} / "
              f"{cycles['gdi_growth']['verdict']} / {cycles['user_growth']['verdict']}",
              f"- Threads : {cycles['threads'][0] if cycles['threads'] else None} → "
              f"{cycles['threads'][-1] if cycles['threads'] else None}",
              f"- QTimers (actifs) : {cycles['qt_timers_active'][0] if cycles['qt_timers_active'] else None} → "
              f"{cycles['qt_timers_active'][-1] if cycles['qt_timers_active'] else None}",
              f"- Paquets d'observation vivants après arrêt : {cycles['packets_alive'][-1] if cycles['packets_alive'] else None}",
              f"- Observateur ou job encore vivant après un arrêt : "
              f"{'OUI' if cycles['observer_alive_after_stop'] or cycles['jobs_active_after_stop'] else 'non'}"]
    return "\n".join(lines) + "\n"


def write_report(report: dict, output: Path) -> tuple[Path, Path]:
    import json
    output.mkdir(parents=True, exist_ok=True)
    json_path, md_path = output / "runtime-health.json", output / "runtime-health.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, md_path


def run_ui_stress(rounds: int = 5) -> dict[str, object]:
    """GDI / USER / handles : blocs d'opérations UI répétés, mesure après chaque bloc (aucune action jeu).

    Par bloc : 1000 changements texte/style, reconstruction des accordéons de chaque page, 50 nouveaux
    aperçus, 5 réductions/restaurations, 20 jobs. Une hausse au premier bloc puis un plafond est normal ;
    une croissance à chaque bloc est signalée (``classify_growth`` sur les blocs 2..N).
    """
    from PySide6.QtWidgets import QApplication
    if os.environ.get("QT_QPA_PLATFORM") is None and os.name != "nt" and not os.environ.get("DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    workdir = Path(tempfile.mkdtemp(prefix="dofbot2-uistress-"))
    os.environ["PYTHONBOT_DATA_DIR"] = str(workdir)
    from combatbot.storage import Storage
    from combatbot.ui.dofbot2 import theme
    from combatbot.ui.dofbot2.controls import set_style, set_text
    from combatbot.ui.dofbot2.profiles import create_profile
    from combatbot.ui.dofbot2.shell import DofBot2Window
    app = QApplication.instance() or QApplication([])
    theme.load_fonts()
    storage = Storage(workdir / "stress.sqlite3")
    DofBot2Window._connect_legacy = lambda self, hwnd: None
    profile = create_profile(storage, "Stress", 0, "Cra")
    window = DofBot2Window(storage, start_screen="profile")
    window.show()
    window.enter_app(profile.id)
    window.open_advanced()
    view = window.advanced_view
    view.set_recheck(False)
    legacy = window.legacy
    observer = SyntheticObserver(0.0)
    sampler = ResourceSampler()
    rows = [sampler.sample(block=0)]
    for block in range(1, rounds + 1):
        page = view.pages["observation"]
        view.go_tab("observation", animate=False)
        for index in range(1000):
            set_text(view.status_text, f"état {index}")
            set_style(view.status_text, f"color: #{(index * 97) % 0xFFFFFF:06x};")
        for key in view.pages:
            view.go_tab(key, animate=False)
            view.pages[key].refresh()                             # reconstruction volontaire des accordéons
            app.processEvents()
        view.go_tab("observation", animate=False)
        for _ in range(50):
            legacy.combat.set_observation(observer.observe())
            app.processEvents()
        for _ in range(5):
            window.showMinimized()
            app.processEvents()
            window.showNormal()
            app.processEvents()
        for _ in range(20):
            legacy.jobs.submit(lambda: 1, lambda value: None, lambda message: None)
        deadline = time.monotonic() + 10
        while legacy.jobs.active and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        _pump(app, 0.5)
        rows.append(sampler.sample(block=block, accordion_rebuilds=page.accordion_rebuild_count))
    window.close()
    storage.close()
    later = rows[1:]
    times = [float(row["block"]) * 60 for row in later]         # un bloc = une « minute » pour la tendance
    return {"schema_version": 1, "report": "ui-stress", "rounds": rounds, "rows": rows,
            "gdi_growth": classify_growth(times, [row.get("gdi_objects") for row in later], 0),
            "user_growth": classify_growth(times, [row.get("user_objects") for row in later], 0),
            "handles_growth": classify_growth(times, [row.get("handles") for row in later], 0),
            "rss_growth": classify_growth(times, [row.get("rss_mb") for row in later], 0),
            "threads": [row.get("threads") for row in rows], "actions": "NONE"}
