"""Journaux UI bornés ; l'historique complet reste dans SQLite."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from combatbot.models import CombatEvent
from combatbot.storage import Storage
from combatbot.ui.pages import LogsPage


def test_logs_page_keeps_a_bounded_window_and_sqlite_keeps_everything(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    storage = Storage(tmp_path / "logs.sqlite3")
    assert storage.last_event_id() is None
    for index in range(300):
        storage.record_event(CombatEvent("INFO", "test", f"événement {index}"))
    page = LogsPage(storage)
    for index in range(5000):
        page.append_event("t", "INFO", "test", f"ligne {index}", "{}")
    assert page.text.document().blockCount() <= LogsPage.LIMIT
    assert page.text.toPlainText().splitlines()[-1].endswith("ligne 4999  {}")
    assert storage.last_event_id() == 300 and len(storage.recent_events(1000)) == 300
    storage.close()
