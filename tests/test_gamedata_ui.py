import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from combatbot.storage import Storage
from combatbot.ui.gamedata_panel import GameDataPanel
from test_gamedata import make_dlm


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def panel(qapp, tmp_path):
    storage = Storage(tmp_path / "ui.sqlite3")
    widget = GameDataPanel(storage)
    widget.output_directory = tmp_path / "output"
    yield widget
    widget.jobs.pool.waitForDone(5000)
    qapp.processEvents()
    widget.close()
    storage.close()


def wait_done(panel, qapp):
    until = time.monotonic() + 5
    while (panel.busy or panel.jobs.active) and time.monotonic() < until:
        qapp.processEvents()
        QTest.qWait(10)
    assert not panel.busy and not panel.jobs.active


def test_missing_folder_ui_is_explicit_and_non_crashing(panel, qapp):
    panel._analyze()
    wait_done(panel, qapp)
    assert panel.status.text() == "Dossier du client DOFUS non configuré"
    assert not panel.load_button.isEnabled()
    assert not panel.export_button.isEnabled()
    assert panel.analyze_button.isEnabled()


def test_folder_persisted_without_scanning_on_edit(panel, tmp_path):
    folder = tmp_path / "client"
    folder.mkdir()
    panel.folder.setText(str(folder))
    panel._save_folder()
    assert panel.storage.get_setting("dofus_client_directory") == str(folder)
    assert panel.provider is None
    second = GameDataPanel(panel.storage)
    assert second.folder.text() == str(folder)
    second.close()


def test_analyze_load_export_and_failure_clear_stale_map(panel, qapp, tmp_path):
    folder = tmp_path / "client"
    folder.mkdir()
    (folder / "sample.dlm").write_bytes(make_dlm())
    panel.folder.setText(str(folder))
    panel._analyze()
    assert not panel.analyze_button.isEnabled()
    wait_done(panel, qapp)
    assert panel.load_button.isEnabled()
    assert "1 IDs indexés" in panel.summary.text()
    assert not panel.export_button.isEnabled()
    panel.map_id.setText("123")
    panel._load_map()
    wait_done(panel, qapp)
    assert panel.loaded_map == 123
    assert "560 cellules" in panel.map_summary.text()
    assert "fight cells : inconnu" in panel.map_summary.text()
    assert panel.export_button.isEnabled()
    panel._export_map()
    wait_done(panel, qapp)
    exported = panel.output_directory / "debug" / "map_123.json"
    assert json.loads(exported.read_text(encoding="utf-8"))["map_id"] == 123
    report = json.loads((panel.output_directory / "probe-report.json").read_text(encoding="utf-8"))
    assert report["map_parsed"] and not report["topology_extracted"]
    panel.map_id.setText("999")
    panel._load_map()
    wait_done(panel, qapp)
    assert panel.loaded_map is None and not panel.export_button.isEnabled()
    assert "absent" in panel.status.text()
    assert panel.map_summary.text() == "Aucune map chargée"


def test_bad_map_id_is_handled(panel, qapp, tmp_path):
    folder = tmp_path / "client"
    folder.mkdir()
    (folder / "sample.dlm").write_bytes(make_dlm())
    panel.folder.setText(str(folder))
    panel._analyze()
    wait_done(panel, qapp)
    panel.map_id.setText("not an id")
    panel._load_map()
    assert "entier" in panel.status.text()
    assert not panel.busy


def test_settings_tab_and_shared_worker(qapp, tmp_path, monkeypatch):
    from combatbot.ui import client_panel
    from combatbot.ui.pages import SettingsPage
    from combatbot.ui.jobs import JobRunner
    monkeypatch.setattr(client_panel, "list_dofus_windows", lambda: [])
    storage = Storage(tmp_path / "settings.sqlite3")
    jobs = JobRunner()
    page = SettingsPage(storage, jobs)
    assert page.client_tabs.tabText(1) == "Données du client"
    assert page.gamedata_panel.jobs is jobs
    page.close()
    storage.close()


def test_full_validation_runs_in_background_and_updates_summary(panel, qapp, tmp_path):
    from test_gamedata_real_formats import make_d2p_index_first, make_real_dlm
    folder = tmp_path / "client"
    (folder / "content" / "maps").mkdir(parents=True)
    (folder / "content" / "maps" / "maps0.d2p").write_bytes(make_d2p_index_first(
        [("1/101.dlm", make_real_dlm(101)), ("2/102.dlm", make_real_dlm(102))]))
    panel.folder.setText(str(folder))
    assert not panel.validate_button.isEnabled()
    panel._analyze()
    wait_done(panel, qapp)
    assert "INDEX_BEFORE_DATA 1" in panel.summary.text()
    assert "Maps lisibles : non validées" in panel.summary.text()
    assert panel.validate_button.isEnabled()
    panel._validate()
    assert panel.validate_button.text() == "Arrêter la validation"
    wait_done(panel, qapp)
    text = panel.summary.text()
    assert "Maps lisibles : 2 / 2" in text and "v11 : 2" in text
    assert "Maps chiffrées : 0" in text and "560 cellules : 2 maps" in text
    assert "Fight cells vérifiées : non" in text
    assert panel.validate_button.text() == "Valider toutes les maps"
    exported = json.loads((panel.output_directory / "validation-summary.json").read_text(encoding="utf-8"))
    assert exported["maps"]["stages"]["cells_parsed"] == 2
    assert not exported["topology"]["fight_cells_verified"]
