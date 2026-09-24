import math
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from combatbot.gamedata.topology import cell_to_grid
from combatbot.ui.grid_projection_dialog import GridProjectionDialog, default_transform
from combatbot.ui.pages import CombatPage
from combatbot.vision.combat_models import ObservationPacket
from combatbot.vision.grid_fit import FitStatus
from combatbot.vision.grid_projection import cell_to_combat
from test_gamedata_grid import (
    CLIENT, CROP, TRANSFORM, ZONES, _observation, make_topology, render, resolver,
)
from combatbot.vision.coordinates import LayoutSignature


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def dialog(topology=None, image=None):
    image = image if image is not None else render(topology or make_topology(seed=9))
    return GridProjectionDialog(image, layout_signature=LayoutSignature.create(CLIENT, ZONES).to_json(),
                                topology=topology, map_id=getattr(topology, "map_id", None))


def test_dialog_auto_proposal_then_user_confirmation(qapp):
    topology = make_topology(seed=9)
    window = dialog(topology)
    fit = window.propose_auto()
    assert fit.status is FitStatus.ACCEPTED and window.method == "auto"
    assert "ACCEPTED" in window.metrics.text() and window.alignment > 0.5
    window.confirm()
    profile = window.result_profile
    assert profile is not None and profile.confirmed_by_user and profile.map_id == topology.map_id
    assert profile.calibration_method == "auto" and profile.calibration_metrics["fit"]["status"] == "ACCEPTED"
    a, b = cell_to_combat(287, profile.transform), cell_to_combat(287, TRANSFORM)
    assert math.hypot(a.x - b.x, a.y - b.y) < 1.5
    assert profile.status_for(CLIENT, ZONES).applicable
    window.close()


def test_dialog_manual_adjustment_without_ids(qapp):
    window = dialog(None, render(make_topology(seed=9)))
    assert not window.has_topology and window.transform == default_transform(CROP[0], CROP[1])
    before = window.transform.origin
    window.nudge(1, 0)
    window.nudge(0, -1)
    assert (window.transform.origin.x, window.transform.origin.y) == (before.x + 1, before.y - 1)
    width = window.transform.cell_width
    window.resize_cells(2, 0)
    assert window.transform.cell_width == pytest.approx(width + 2) and window.method == "manual"
    window.origin_x.setValue(TRANSFORM.origin.x)
    window.origin_y.setValue(TRANSFORM.origin.y)
    window.cell_w.setValue(TRANSFORM.cell_width)
    window.cell_h.setValue(TRANSFORM.cell_height)
    window.shear.setValue(0.0)
    assert window.transform.grid_to_combat(cell_to_grid(287)) == cell_to_combat(287, TRANSFORM)
    window.close()


def test_dialog_optional_anchors(qapp):
    window = dialog(make_topology(seed=9))
    window.anchor_mode.setChecked(True)
    for cell in (0, 13, 300, 546):
        window.anchor_cell.setValue(cell)
        point = cell_to_combat(cell, TRANSFORM)
        window._image_clicked(point.x, point.y)
    fit = window.fit_anchors()
    assert fit.status is FitStatus.ACCEPTED and window.method == "anchors" and len(window.anchors) == 4
    window.close()


def test_real_vision_page_declared_map_and_hover(qapp):
    page = CombatPage()
    assert "non détectée" in page.map_id_input.placeholderText()
    options = page.overlay_options()
    assert options.grid and not options.red_blue
    page.overlay_boxes["red_blue"].setChecked(True)
    assert page.overlay_options().red_blue
    page.set_declared_map("Map ID déclaré manuellement : 1")
    assert "déclaré manuellement" in page.map_status.text()
    topology = make_topology()
    grid = resolver(topology).resolve(render(topology), CLIENT, ZONES).grid
    image = render(topology)
    packet = ObservationPacket(_observation(grid), image, image, 5.0,
                               {"grid_source_reason": "READY", "requires_recalibration": False})
    page.set_observation(packet)
    assert "GAMEDATA_PROJECTED" in page.real_values["grid_source"].text()
    assert "déclarée manuellement" in page.real_values["grid_source"].text()
    center = cell_to_combat(287, TRANSFORM).rounded()
    page._preview_hovered(center)
    assert page.hover_info.text().startswith("Cellule 287") and "non validés" in page.hover_info.text()
    page._preview_hovered((-50, -50))
    assert "hors des 560" in page.hover_info.text()
    page.close()


def test_dialog_cycles_ambiguous_hypotheses_for_human_choice(qapp):
    window = dialog(None, render(make_topology(seed=9)))  # no map: ambiguous by construction
    fit = window.propose_auto()
    assert not fit.accepted and len(fit.alternatives) > 1
    first = window.transform
    window.next_hypothesis()
    assert window.transform != first and window.method == "auto+human"
    assert all(t.orientation.value == "NORMAL" for t in fit.alternatives)
    window.close()


def test_main_window_map_declaration_wiring(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from combatbot.storage import Storage
    from combatbot.ui import client_panel
    from combatbot.ui.main_window import MainWindow
    monkeypatch.setattr(client_panel, "list_dofus_windows", lambda: [])
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args[2]))
    storage = Storage(tmp_path / "window.sqlite3")
    window = MainWindow(storage)
    window._load_declared_map("abc")
    assert "entier positif" in warnings[-1] and window._map_identity.current_map() is None
    window._load_declared_map("123")
    assert "dossier du client" in warnings[-1]
    window.combat.legacy_fallback.setChecked(True)
    assert window._allow_legacy_fallback is True
    window.jobs.pool.waitForDone(3000)
    window.observation_timer.stop()
    window.controller.thread = None
    window.close()
    storage.close()


def test_opening_dialog_computes_automatically_in_worker(qapp, monkeypatch):
    import threading
    from PySide6.QtCore import QElapsedTimer
    from PySide6.QtTest import QTest
    from combatbot.vision.grid_fit import GridFitResult
    main_thread = threading.get_ident()
    worker_threads = []
    window = dialog(make_topology(seed=9))

    def calculate():
        worker_threads.append(threading.get_ident())
        return [], GridFitResult(FitStatus.ACCEPTED, "auto", TRANSFORM, score=0.9)

    monkeypatch.setattr(window, "_calculate_auto", calculate)
    window.show()
    timer = QElapsedTimer()
    timer.start()
    while (not worker_threads or window.jobs.active) and timer.elapsed() < 5000:
        QTest.qWait(10)
    assert worker_threads and worker_threads[0] != main_thread
    assert not window.jobs.active and window.method == "auto"
    assert window.result_profile is None  # no silent confirmation of the proposal
    assert window.confirm_button.isEnabled()
    window.close()


def test_dialog_can_close_during_automatic_calculation(qapp, monkeypatch):
    import threading
    from PySide6.QtCore import QElapsedTimer
    from PySide6.QtTest import QTest
    window = dialog(make_topology(seed=9))
    release = threading.Event()

    def calculate():
        release.wait(2)
        raise ValueError("fixture illisible")

    monkeypatch.setattr(window, "_calculate_auto", calculate)
    window.show()
    qapp.processEvents()
    assert window.jobs.active
    window.reject()
    assert window._close_pending
    release.set()
    timer = QElapsedTimer()
    timer.start()
    while window.jobs.active and timer.elapsed() < 5000:
        QTest.qWait(10)
    assert not window.jobs.active and not window.isVisible()
    assert window.result_profile is None
