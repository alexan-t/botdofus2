"""Travaux de vision lancés hors du thread principal Qt."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThreadPool, Signal


class _Signals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    done = Signal()


class _Job:
    """Un travail en cours : ses signaux (thread du pool → thread UI) et sa fonction."""

    def __init__(self, work: Callable[[], object]) -> None:
        self.work: Callable[[], object] | None = work
        self.signals = _Signals()

    def run(self) -> None:
        work, signals = self.work, self.signals
        try:
            signals.succeeded.emit(work())
        except Exception as exc:
            signals.failed.emit(f"{type(exc).__name__} : {exc}")
        finally:
            signals.done.emit()


class JobRunner(QObject):
    """Jobs hors thread UI.

    Le pool exécute une simple fonction Python (runnable interne supprimé par Qt après exécution). Un
    ``QRunnable`` Python avec ``setAutoDelete(False)`` était au contraire conservé par le pool pour
    toujours, avec ses rappels et tout ce qu'ils capturaient (observateur, frames, calibration) : fuite
    mesurée par ``--runtime-health`` (un observateur complet conservé par cycle démarrer/arrêter).
    À la fin, les rappels sont déconnectés et le job n'est plus référencé.
    """

    all_done = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self._jobs: set[_Job] = set()
        # État hors de ``self`` : lisible même après destruction de l'objet C++ (fenêtre ou dialogue fermé).
        state = self._state = {"closed": False}
        self.destroyed.connect(lambda *_: state.__setitem__("closed", True))

    def shutdown(self) -> None:
        """Abandonne les rappels des jobs encore en cours (fermeture) ; aucun nouveau job accepté."""
        self._state["closed"] = True

    @property
    def active(self) -> bool:
        return bool(self._jobs)

    def submit(
        self,
        work: Callable[[], object],
        on_success: Callable[[object], None],
        on_error: Callable[[str], None],
    ) -> None:
        # Les rappels touchent des widgets : ils doivent s'exécuter dans le thread GUI. Les signaux du job
        # sont créés ici, donc dans le thread appelant ; émis depuis le worker, Qt les met en file vers ce
        # thread. Un appel depuis un autre thread rendrait cette garantie fausse : refusé.
        from PySide6.QtCore import QCoreApplication, QThread
        app = QCoreApplication.instance()
        if app is not None and QThread.currentThread() != app.thread():
            raise RuntimeError("JobRunner.submit doit être appelé depuis le thread GUI")
        if self._state["closed"]:
            raise RuntimeError("JobRunner arrêté : aucun nouveau job")
        state = self._state
        job = _Job(work)
        self._jobs.add(job)
        job.signals.succeeded.connect(lambda value: None if state["closed"] else on_success(value))
        job.signals.failed.connect(lambda message: None if state["closed"] else on_error(message))
        job.signals.done.connect(lambda: self._done(job))
        self.pool.start(job.run)

    def _done(self, job: _Job) -> None:
        self._jobs.discard(job)
        # La connexion « done → lambda(job) » forme un cycle via Qt, invisible pour le GC Python : la
        # rompre libère le job et tout ce que ses rappels capturent.
        for signal in (job.signals.succeeded, job.signals.failed, job.signals.done):
            try:
                signal.disconnect()
            except (RuntimeError, TypeError):
                pass
        job.work = None
        if not self._jobs and not self._state["closed"]:
            self.all_done.emit()
