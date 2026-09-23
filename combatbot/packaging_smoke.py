"""Diagnostic automatisé exécuté depuis le binaire ONEDIR construit."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import traceback

import cv2
import numpy as np

from combatbot.runtime import app_data_root, executable_path, is_frozen
from combatbot.vision.connection import connect_window
from combatbot.vision.tooltip import read_visible_text
from combatbot.vision.window import list_dofus_windows


def _gamedata_grid_check() -> dict[str, object]:
    """LOT 3B-2 : modules GameData présents et projection des 560 cellules dans le binaire."""
    import importlib
    from combatbot.gamedata.models import DofusCellId, GameMapCell, GridTopology
    from combatbot.vision.grid_projection import GridProjector, GridScreenTransform
    modules = ["combatbot.gamedata.formats.archives", "combatbot.gamedata.formats.maps",
               "combatbot.gamedata.formats.d2o", "combatbot.gamedata.topology", "combatbot.gamedata.validation",
               "combatbot.vision.grid_fit", "combatbot.vision.grid_profile", "combatbot.vision.gamedata_grid"]
    loaded = [name for name in modules if importlib.import_module(name)]
    topology = GridTopology(0, tuple(GameMapCell(DofusCellId(i)) for i in range(560)))
    grid = GridProjector(GridScreenTransform.from_cell_size((43, 21.5), 86, 43)).project(topology)
    return {"modules": loaded, "projected_cells": len(grid.cells),
            "lookup_287": grid.pixel_to_cell(grid.cell(287).center)}


def _grid_validation_check() -> dict[str, object]:
    """LOT 3B-3 : validateur embarqué, visibilité sur image vide / grille dessinée, aucune action."""
    from combatbot import ports
    from combatbot.gamedata.models import DofusCellId, GameMapCell, GridTopology
    from combatbot.vision import combat_observer
    from combatbot.vision.gamedata_grid import projected_observation
    from combatbot.vision.grid_fit import candidate_union
    from combatbot.vision.grid_projection import GridProjector, GridScreenTransform
    from combatbot.vision.grid_validation import GridAlignmentValidator, GridVisibilityDetector
    topology = GridTopology(0, tuple(GameMapCell(DofusCellId(i), walkable=40 <= i < 520,
                                                 non_walkable_during_fight=False) for i in range(560)))
    transform = GridScreenTransform.from_cell_size((30, 16), 54, 27)
    projected = GridProjector(transform).project(topology)
    size = (820, 580)
    results = {}
    for name, draw in (("blank", False), ("drawn", True)):
        image = np.full((size[1], size[0], 3), 32, np.uint8)
        if draw:
            for cell in projected.cells:
                if cell.static_traversable_in_fight:
                    points = np.array([p.rounded() for p in cell.polygon], np.int32)
                    cv2.fillPoly(image, [points], (60, 92, 70))
                    cv2.polylines(image, [points], True, (175, 175, 175), 1, cv2.LINE_AA)
        grid = projected_observation(projected, image)
        visibility, inliers, outliers = GridVisibilityDetector().observe(
            projected, candidate_union(image), size, grid.topology_consistency)
        alignment = GridAlignmentValidator().validate(projected, visibility, inliers, outliers)
        results[f"{name}_visibility"] = visibility.state.value
        results[f"{name}_alignment"] = alignment.status.value
    # The observation pipeline holds no reference to the (unconnected) action port.
    invoked = any(getattr(value, "__name__", "") == "ActionExecutor" for value in vars(combat_observer).values())
    return {"projected_cells": len(projected.cells), **results,
            "action_executor_invoked": invoked, "action_port_is_protocol": hasattr(ports, "ActionExecutor")}


def run_packaging_smoke(app, storage, window) -> int:
    report_path = Path(os.environ.get(
        "PYTHONBOT_SMOKE_REPORT", str(app_data_root() / "logs" / "packaging-smoke.json")
    )).resolve()
    results: dict[str, object] = {
        "date": datetime.now().astimezone().isoformat(),
        "frozen": is_frozen(),
        "executable": str(executable_path()),
        "database": str(storage.path),
        "checks": {},
    }
    checks: dict[str, object] = results["checks"]  # type: ignore[assignment]
    try:
        window.show()
        app.processEvents()
        checks["interface_visible"] = window.isVisible()
        for index in range(window.stack.count()):
            window._navigate(index)
            app.processEvents()
        checks["navigation_pages"] = window.stack.count()
        checks["corpus_entries"] = len(window.corpus.repository.list_entries())
        checks["sqlite_profiles"] = len(storage.list_profiles())
        checks["sqlite_statistics"] = storage.statistics()

        sample = np.zeros((64, 128, 3), dtype=np.uint8)
        cv2.rectangle(sample, (5, 5), (120, 58), (255, 255, 255), 2)
        checks["opencv"] = int(cv2.Canny(sample, 50, 120).sum()) > 0

        checks["gamedata_grid"] = _gamedata_grid_check()
        checks["grid_validation"] = _grid_validation_check()

        ocr_image = np.full((100, 420, 3), 255, dtype=np.uint8)
        cv2.putText(ocr_image, "3 PA  Portee 1-4", (8, 55), cv2.FONT_HERSHEY_SIMPLEX, 1,
                    (0, 0, 0), 2)
        text, confidence = read_visible_text(ocr_image)
        checks["rapidocr"] = {"loaded": True, "text": text, "confidence": confidence}

        # PYTHONBOT_SMOKE_SKIP_DOFUS=1 : ne pas activer la fenêtre d'une partie en cours.
        windows = [] if os.environ.get("PYTHONBOT_SMOKE_SKIP_DOFUS") == "1" else list_dofus_windows()
        checks["dofus_windows"] = [
            {"hwnd": item.hwnd, "title": item.title, "minimized": item.minimized} for item in windows
        ]
        if windows:
            connection = connect_window(windows[0].hwnd, storage.known_icons(window.client_panel.profile_id or 0))
            checks["dofus_capture"] = {
                "success": connection.success, "code": connection.code,
                "message": connection.message, "details": connection.details,
            }
        else:
            checks["dofus_capture"] = {"tested": False, "reason": "Aucune fenêtre DOFUS détectée"}
        results["success"] = all((
            checks["interface_visible"], checks["navigation_pages"] == 8,
            checks["sqlite_profiles"] >= 1, checks["opencv"], checks["rapidocr"]["loaded"],
            checks["gamedata_grid"]["projected_cells"] == 560,
            checks["grid_validation"]["blank_visibility"] == "NOT_VISIBLE",
            checks["grid_validation"]["drawn_visibility"] == "VISIBLE",
            not checks["grid_validation"]["action_executor_invoked"],
        ))
    except Exception as exc:
        results["success"] = False
        results["error"] = f"{type(exc).__name__}: {exc}"
        results["traceback"] = traceback.format_exc()
    finally:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        window.close()
        app.processEvents()
    return 0 if results.get("success") else 2
