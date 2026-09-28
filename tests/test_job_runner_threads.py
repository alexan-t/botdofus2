"""JobRunner : work() dans un worker ; rappels, _done et all_done dans le thread GUI ; libération complète."""
from __future__ import annotations

import gc
import os
import threading
import time
import tracemalloc
import weakref

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QThread
from PySide6.QtWidgets import QApplication, QLabel

from combatbot.performance.metrics import process_snapshot
from combatbot.ui.jobs import JobRunner, _Job

APP = QApplication.instance() or QApplication([])
MAIN_IDENT = threading.get_ident()


def on_gui() -> bool:
    """Deux preuves indépendantes : thread Qt courant == thread de l'application, et identifiant Python."""
    return QThread.currentThread() == APP.thread() and threading.get_ident() == MAIN_IDENT


def flush_deletes() -> None:
    """``deleteLater`` n'est exécuté qu'en traitant explicitement les suppressions différées."""
    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    APP.processEvents()


def wait(runner: JobRunner, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while runner.active and time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(0.002)
    APP.processEvents()
    assert not runner.active


class Owner:
    def __init__(self) -> None:
        self.data = bytearray(256_000)


def test_work_in_worker_and_every_callback_on_the_gui_thread() -> None:
    runner = JobRunner()
    seen: dict[str, list[bool]] = {"work": [], "success": [], "error": [], "done": [], "all_done": []}
    original_done = runner._done

    def spy_done(job) -> None:
        seen["done"].append(on_gui())
        original_done(job)

    runner._done = spy_done
    runner.all_done.connect(lambda: seen["all_done"].append(on_gui()))

    def work() -> int:
        seen["work"].append(on_gui())
        return 1

    def failing() -> int:
        seen["work"].append(on_gui())
        raise ValueError("échec")

    runner.submit(work, lambda value: seen["success"].append(on_gui()), lambda m: seen["error"].append(on_gui()))
    wait(runner)
    runner.submit(failing, lambda value: seen["success"].append(on_gui()), lambda m: seen["error"].append(on_gui()))
    wait(runner)
    assert seen["work"] == [False, False]                       # jamais dans le thread GUI
    assert seen["success"] == [True] and seen["error"] == [True]
    assert seen["done"] == [True, True] and seen["all_done"] == [True, True]


def test_concurrent_jobs_each_callback_once_and_all_done_once() -> None:
    runner = JobRunner()
    results, threads, all_done = [], [], []
    runner.all_done.connect(lambda: all_done.append(on_gui()))
    for index in range(8):                                      # pool de 2 : exécutions concurrentes
        runner.submit(lambda index=index: (threads.append(threading.get_ident()), time.sleep(0.02), index)[2],
                      lambda value: results.append((value, on_gui())), lambda message: results.append(message))
    wait(runner)
    assert sorted(value for value, _ in results) == list(range(8)) and all(gui for _, gui in results)
    assert all_done == [True]                                   # une seule fois, au dernier job
    assert MAIN_IDENT not in threads and len(set(threads)) <= 2


def test_callback_capturing_a_widget_updates_it_on_the_gui_thread() -> None:
    runner = JobRunner()
    label = QLabel("avant")
    checks = []

    def update(value) -> None:
        checks.append(on_gui())
        label.setText(str(value))

    runner.submit(lambda: "après", update, lambda message: None)
    wait(runner)
    assert label.text() == "après" and checks == [True]


def test_destroyed_widget_or_runner_during_a_job_never_crashes_nor_calls_back() -> None:
    runner = JobRunner()
    label = QLabel("x")
    runner.submit(lambda: (time.sleep(0.1), 1)[1], lambda value, label=label: label.setText("fini"),
                  lambda message: None)
    label.deleteLater()
    flush_deletes()
    wait(runner)                                                # aucun crash
    closing, calls = JobRunner(), []
    closing.submit(lambda: (time.sleep(0.1), 1)[1], calls.append, calls.append)
    closing.deleteLater()                                       # fenêtre / dialogue fermé pendant le job
    flush_deletes()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        APP.processEvents()
        time.sleep(0.01)
    assert calls == []                                          # avant correctif : [1]
    stopped = JobRunner()
    stopped.shutdown()
    with pytest.raises(RuntimeError, match="arrêté"):
        stopped.submit(lambda: 1, calls.append, calls.append)


def test_submit_from_a_worker_thread_is_refused() -> None:
    runner = JobRunner()
    errors = []

    def from_thread() -> None:
        try:
            runner.submit(lambda: 1, lambda value: None, lambda message: None)
        except RuntimeError as exc:
            errors.append(str(exc))

    thread = threading.Thread(target=from_thread)
    thread.start()
    thread.join()
    assert errors and "thread GUI" in errors[0] and not runner.active


def test_everything_is_released_after_done() -> None:
    runner = JobRunner()
    owner = Owner()
    references = {}

    def success(value, owner=owner) -> None:
        references["called"] = references.get("called", 0) + 1

    def failure(message, owner=owner) -> None:
        references["failed"] = references.get("failed", 0) + 1

    def work(owner=owner) -> int:
        return 1

    references["success"], references["failure"] = weakref.ref(success), weakref.ref(failure)
    references["work"], references["owner"] = weakref.ref(work), weakref.ref(owner)
    runner.submit(work, success, failure)
    job = next(iter(runner._jobs))
    references["job"] = weakref.ref(job)
    del success, failure, work, owner, job
    wait(runner)
    gc.collect()
    assert references["called"] == 1 and "failed" not in references        # aucun double rappel
    assert all(references[name]() is None for name in ("success", "failure", "work", "owner", "job"))
    assert runner._jobs == set()


def test_stress_hundreds_of_jobs_release_everything() -> None:
    class StressOwner(Owner):                                   # type propre au test : comptage indépendant
        pass

    runner = JobRunner()
    all_done = []
    runner.all_done.connect(lambda: all_done.append(1))
    jobs_before = sum(isinstance(item, _Job) for item in gc.get_objects())
    tracemalloc.start()
    before_threads = process_snapshot().threads
    heap = []
    for batch in range(6):
        for _ in range(50):
            owner = StressOwner()
            runner.submit(lambda owner=owner: 1, lambda value, owner=owner: None, lambda message: None)
        wait(runner, 20)
        del owner
        gc.collect()
        heap.append(tracemalloc.get_traced_memory()[0])
    tracemalloc.stop()
    alive_jobs = sum(isinstance(item, _Job) for item in gc.get_objects()) - jobs_before
    alive_owners = sum(isinstance(item, StressOwner) for item in gc.get_objects())
    assert alive_jobs == 0 and alive_owners == 0 and runner._jobs == set()
    assert len(all_done) >= 1
    assert heap[-1] - heap[1] < 2_000_000                       # 300 jobs de 256 Ko capturés : pas de croissance
    after_threads = process_snapshot().threads
    if before_threads is not None and after_threads is not None:
        assert after_threads <= before_threads + 2              # au plus les 2 threads du pool
