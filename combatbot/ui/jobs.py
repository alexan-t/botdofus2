"""Travaux de vision lancés hors du thread principal Qt."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    done = Signal()


class _Job(QRunnable):
    def __init__(self, work: Callable[[], object]) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.work = work
        self.signals = _Signals()

    def run(self) -> None:
        try:
            self.signals.succeeded.emit(self.work())
        except Exception as exc:
            self.signals.failed.emit(f"{type(exc).__name__} : {exc}")
        finally:
            self.signals.done.emit()


class JobRunner(QObject):
    all_done = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._jobs: set[_Job] = set()

    @property
    def active(self) -> bool:
        return bool(self._jobs)

    def submit(
        self,
        work: Callable[[], object],
        on_success: Callable[[object], None],
        on_error: Callable[[str], None],
    ) -> None:
        job = _Job(work)
        self._jobs.add(job)
        job.signals.succeeded.connect(on_success)
        job.signals.failed.connect(on_error)
        job.signals.done.connect(lambda: self._done(job))
        self.pool.start(job)

    def _done(self, job: _Job) -> None:
        self._jobs.discard(job)
        if not self._jobs:
            self.all_done.emit()
