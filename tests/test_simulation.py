from PySide6.QtCore import QCoreApplication, QTimer

from combatbot.engine import CombatEngine
from combatbot.models import CombatState, Spell, Strategy
from combatbot.simulation import SimulationController


def test_worker_pause_resume_stop() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    spell = Spell("Test", 3, 1, 4, False, False, True, 2, 2, 10, 6)
    engine = CombatEngine([spell], Strategy())
    controller = SimulationController()
    statuses = []
    snapshots = []
    controller.status_ready.connect(statuses.append)
    controller.snapshot_ready.connect(snapshots.append)

    counts = {}
    controller.start(engine, 70)
    QTimer.singleShot(200, controller.pause)
    QTimer.singleShot(300, lambda: counts.update(paused=len(snapshots)))
    QTimer.singleShot(480, lambda: counts.update(still_paused=len(snapshots)))
    QTimer.singleShot(500, controller.resume)
    QTimer.singleShot(660, lambda: counts.update(resumed=len(snapshots)))
    QTimer.singleShot(700, controller.stop)
    QTimer.singleShot(900, app.quit)
    app.exec()

    assert "En cours" in statuses
    assert snapshots
    assert "En pause" in statuses
    assert counts["paused"] == counts["still_paused"]
    assert counts["resumed"] > counts["still_paused"]
    assert statuses[-1] == "Arrêté"
    assert snapshots[-1].state == CombatState.IDLE
    controller.shutdown()
