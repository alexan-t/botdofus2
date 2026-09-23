"""Exécution cadencée du moteur hors du thread principal Qt."""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from combatbot.engine import CombatEngine
from combatbot.models import CombatEvent, CombatSnapshot, CombatState


class SimulationWorker(QObject):
    snapshot_ready = Signal(object)
    event_ready = Signal(object)
    status_ready = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, engine: CombatEngine, tick_ms: int) -> None:
        super().__init__()
        self.engine = engine
        self.tick_ms = tick_ms
        self.timer: QTimer | None = None
        self._finished = False

    @Slot()
    def start(self) -> None:
        try:
            self.timer = QTimer(self)
            self.timer.setInterval(self.tick_ms)
            self.timer.timeout.connect(self._tick)
            self.engine.start()
            self._publish()
            self.status_ready.emit("En cours")
            self.timer.start()
        except Exception as exc:
            self._fail(exc)

    @Slot()
    def pause(self) -> None:
        if self.timer is not None and self.timer.isActive() and not self._finished:
            self.timer.stop()
            self.status_ready.emit("En pause")

    @Slot()
    def resume(self) -> None:
        if self.timer is not None and not self.timer.isActive() and not self._finished:
            self.timer.start()
            self.status_ready.emit("En cours")

    @Slot()
    def stop(self) -> None:
        if self._finished:
            return
        if self.timer is not None:
            self.timer.stop()
        self.engine.stop()
        self._publish()
        self.status_ready.emit("Arrêté")
        self._finish()

    @Slot()
    def _tick(self) -> None:
        try:
            self.engine.step()
            self._publish()
            if self.engine.state == CombatState.FINISHED:
                if self.timer is not None:
                    self.timer.stop()
                self.status_ready.emit("Terminé")
                self._finish()
        except Exception as exc:
            self._fail(exc)

    def _publish(self) -> None:
        for event in self.engine.drain_events():
            self.event_ready.emit(event)
        self.snapshot_ready.emit(self.engine.snapshot())

    def _fail(self, exc: Exception) -> None:
        if self.timer is not None:
            self.timer.stop()
        self.event_ready.emit(CombatEvent("ERROR", "simulation_error", str(exc)))
        self.failed.emit(str(exc))
        self.status_ready.emit("Erreur")
        self._finish()

    def _finish(self) -> None:
        if not self._finished:
            self._finished = True
            self.finished.emit(self.engine.snapshot())


class SimulationController(QObject):
    snapshot_ready = Signal(object)
    event_ready = Signal(object)
    status_ready = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    ready = Signal()
    pause_requested = Signal()
    resume_requested = Signal()
    stop_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.thread: QThread | None = None
        self.worker: SimulationWorker | None = None

    def start(self, engine: CombatEngine, tick_ms: int) -> None:
        if self.thread is not None:
            raise RuntimeError("La simulation précédente se termine encore")
        self.thread = QThread(self)
        self.worker = SimulationWorker(engine, tick_ms)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.start)
        self.pause_requested.connect(self.worker.pause)
        self.resume_requested.connect(self.worker.resume)
        self.stop_requested.connect(self.worker.stop)
        self.worker.snapshot_ready.connect(self.snapshot_ready)
        self.worker.event_ready.connect(self.event_ready)
        self.worker.status_ready.connect(self.status_ready)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._clear_thread)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.start()

    def pause(self) -> None:
        self.pause_requested.emit()

    def resume(self) -> None:
        self.resume_requested.emit()

    def stop(self) -> None:
        self.stop_requested.emit()

    @Slot()
    def _clear_thread(self) -> None:
        self.thread = None
        self.worker = None
        self.ready.emit()

    def shutdown(self) -> None:
        """Request shutdown without blocking the Qt main thread."""
        self.stop()
