"""Les jobs terminés sont libérés avec tout ce que leurs rappels capturent (fuite mesurée avant correctif)."""
from __future__ import annotations

import gc
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from combatbot.ui.jobs import JobRunner, _Job


class Captured:
    def __init__(self) -> None:
        self.data = bytearray(1_000_000)


def run(runner: JobRunner, app, work, on_success, on_error) -> None:
    runner.submit(work, on_success, on_error)
    deadline = time.monotonic() + 5
    while runner.active and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    app.processEvents()


def test_finished_jobs_and_their_captures_are_released() -> None:
    app = QApplication.instance() or QApplication([])
    runner = JobRunner()
    results, errors = [], []
    for index in range(60):
        owner = Captured()
        if index % 10 == 9:
            run(runner, app, lambda: 1 / 0, lambda value: None, errors.append)
        else:
            run(runner, app, lambda owner=owner, index=index: index,
                lambda value, owner=owner: results.append(value), lambda message: None)
    gc.collect()
    jobs = sum(isinstance(item, _Job) for item in gc.get_objects())
    owners = sum(isinstance(item, Captured) for item in gc.get_objects())
    assert len(results) == 54 and len(errors) == 6 and "ZeroDivisionError" in errors[0]
    assert jobs <= 1                                # libérés (avant correctif : 60)
    assert owners <= 1                              # captures libérées (avant correctif : 60)
