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


def _profile_load_row(calibration, directory: Path) -> dict:
    """Charge via le constructeur réel de RealCombatObserver (donc ``_load_profiles``)."""
    from combatbot.corpus.entity_split import layout_digest
    from combatbot.vision.combat_observer import RealCombatObserver
    observer = RealCombatObserver(calibration.profile_id, calibration, number_reader=lambda *_: (None, 0.0),
                                  frame_provider=lambda: (_ for _ in ()).throw(AssertionError("Aucune capture")))
    profiles = observer.entity_profiles
    digest = layout_digest(calibration.layout_signature)
    player, teams = profiles.player, profiles.teams
    player_file = directory / f"player_train_{digest}.json"
    team_file = directory / f"team_markers_{digest}.json"
    loaded = player is not None and teams is not None
    # Aucun mauvais profil : tout profil chargé porte exactement le layout demandé.
    no_foreign = all(item is None or layout_digest(item.layout_signature) == digest for item in (player, teams))
    return {"layout": digest, "player_profile": player_file.name if player_file.is_file() else None,
            "player_version": getattr(player, "schema_version", None),
            "player_samples": getattr(player, "accepted", None),
            "player_prototypes": len(getattr(player, "prototypes", ()) or ()),
            "team_profile": team_file.name if team_file.is_file() else None,
            "team_version": getattr(teams, "schema_version", None),
            "enemy_partial_allowed": bool(teams and teams.enemy_team and teams.enemy_team.partial_allowed),
            "profile_root": str(directory), "no_foreign_profile": no_foreign,
            "compatible": loaded and player.compatible(calibration.layout_signature)
                          and teams.compatible(calibration.layout_signature),
            "detector": type(observer.entity_detector).__name__,
            "tracker": type(observer.entity_tracker).__name__,
            "load_status": "PASS" if loaded and no_foreign else ("NO_PROFILE" if no_foreign else "FAIL")}


def runtime_profiles_check(data: Path, current=None) -> dict:
    """Charge le vrai stockage choisi, sans capture et sans écriture de données utilisateur.

    ``current`` : calibration réelle du profil utilisateur, rapportée à part.
    """
    from dataclasses import replace
    from combatbot.entity_runtime import active_profile_directory, read_json
    from combatbot.vision.models import Calibration
    previous = os.environ.get("PYTHONBOT_DATA_DIR")
    os.environ["PYTHONBOT_DATA_DIR"] = str(data.parent)
    rows = []
    try:
        directory = active_profile_directory(data)
        for path in sorted(directory.glob("player_train_*.json")):
            raw = read_json(path)
            base = current or Calibration(0, 2560, 1377, {})
            rows.append(_profile_load_row(replace(base, layout_signature=raw["layout_signature"]), directory))
        unknown = _profile_load_row(Calibration(0, 2560, 1377, {}, layout_signature="missing-incompatible-layout"),
                                    directory)
        missing_unknown = unknown["player_version"] is None and unknown["team_version"] is None
        result = {"data": str(data.resolve()), "generation": str(directory),
                  "active_generation": read_json(data / "entity_profiles" / "active.json").get("generation")
                  if (data / "entity_profiles" / "active.json").is_file() else None,
                  "layouts": rows, "unknown_layout": unknown, "missing_unknown": missing_unknown,
                  "actions": "NONE"}
        if current is not None:
            result["current_calibration"] = _profile_load_row(current, directory)
        result["success"] = (bool(rows) and all(r["load_status"] == "PASS" and r["compatible"] for r in rows)
                             and missing_unknown and unknown["no_foreign_profile"]
                             and (current is None or result["current_calibration"]["no_foreign_profile"]))
        return result
    finally:
        if previous is None:
            os.environ.pop("PYTHONBOT_DATA_DIR", None)
        else:
            os.environ["PYTHONBOT_DATA_DIR"] = previous


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


def _hud_reader_check() -> dict[str, object]:
    """LOT 3B-4 : classification spécialisée et fallback, sans action ni donnée client."""
    from combatbot.vision.hud_reader import GlyphTemplateLibrary, HUDReader, segment_glyphs
    image = np.zeros((58, 38, 3), np.uint8)
    cv2.putText(image, "7", (5, 46), cv2.FONT_HERSHEY_SIMPLEX, 1.35, (255, 255, 255), 2, cv2.LINE_AA)
    segments = segment_glyphs(image)
    templates = GlyphTemplateLibrary()
    if segments:
        templates.add("SHARED", 7, segments[0].image)
    specialized = HUDReader(templates).read(image, "AP")
    fallback = HUDReader(GlyphTemplateLibrary(), rapidocr_reader=lambda *_: (3, .99)).read(image, "MP")
    return {
        "initialized": True, "fixture_value": specialized.value,
        "fixture_source": specialized.source.value,
        "fallback_available": fallback.value == 3 and fallback.source.value == "RAPIDOCR",
        "action_executed": False,
    }


def _entity_check() -> dict[str, object]:
    """LOT 3B-5 : détecteur par cellule + suivi global sur une fixture synthétique, sans action."""
    from combatbot.gamedata.models import GridCoordinate
    from combatbot.gamedata.topology import CELL_COUNT, cell_to_grid
    from combatbot.models import Cell
    from combatbot.vision.combat_models import GRID_SOURCE_GAMEDATA, CombatGridObservation, ObservedCell
    from combatbot.vision.entity_detector import CellEntityDetector, DetectionContext
    from combatbot.vision.entity_models import MarkerColorClass, TeamMarkerProfile, VisualProfiles
    from combatbot.vision.entity_profiles import player_profile_from_cell
    from combatbot.vision.entity_tracker import EntityTracker

    origin = cell_to_grid(287)
    ids = [c for c in range(CELL_COUNT)
           if abs(cell_to_grid(c).x - origin.x) + abs(cell_to_grid(c).y - origin.y) <= 3]
    half_w, half_h = 32, 16
    coords = {c: cell_to_grid(c) for c in ids}
    min_x = min(k.x - k.y for k in coords.values())
    min_y = min(k.x + k.y for k in coords.values())
    cells = []
    for cell_id, k in coords.items():
        cx, cy = (k.x - k.y - min_x) * half_w + 64, (k.x + k.y - min_y) * half_h + 48
        cells.append(ObservedCell(Cell(k.x, k.y), (cx, cy),
                                  ((cx, cy - half_h), (cx + half_w, cy), (cx, cy + half_h), (cx - half_w, cy)),
                                  cell_id=cell_id, grid_coordinate=GridCoordinate(k.x, k.y),
                                  walkable_static=True, non_walkable_during_fight_static=False))
    grid = CombatGridObservation(tuple(cells), 64, 32, 0.0, 1.0, GRID_SOURCE_GAMEDATA)
    width = max(c.center[0] for c in cells) + 96
    height = max(c.center[1] for c in cells) + 64
    image = np.full((height, width, 3), (120, 150, 125), np.uint8)
    player_cell, enemy_cell = ids[0], ids[-1]
    for cell_id, color in ((player_cell, (0, 0, 235)), (enemy_cell, (235, 40, 20))):
        cell = grid.cell_by_id(cell_id)
        cv2.ellipse(image, (cell.center[0], cell.center[1] - 10), (7, 14), 0, 0, 360, (40, 60, 90), -1)
        cv2.circle(image, (cell.center[0] - 3, cell.center[1] - 12), 2, (230, 230, 230), -1)
        cv2.ellipse(image, cell.center, (int(half_w * 0.52), int(half_h * 0.52)), 0, 0, 360, color, 3)
    profile = player_profile_from_cell(image, grid, player_cell, layout_signature="smoke-layout")
    teams = TeamMarkerProfile(None, MarkerColorClass(120.0, 12.0, 0.0, 0.0, 1), layout_signature="smoke-layout")
    result = CellEntityDetector().detect(image, grid, VisualProfiles(profile, teams),
                                         DetectionContext(grid_visible=True, grid_aligned=True,
                                                          layout_signature="smoke-layout"))
    tracker = EntityTracker()
    tracked = tracker.update(result, 0.0)
    hidden = tracker.update([], 0.4)
    recovered = tracker.update(result, 0.8)
    return {"detector_imported": True, "tracker_initialized": True,
            "player_detected": result.player is not None and result.player.cell_id == player_cell,
            "enemy_detected": [item.cell_id for item in result.enemies] == [enemy_cell],
            "track_ids": [item.track_id for item in tracked],
            "occlusion_distinct": all(not item.observed_this_frame and item.cell_id is None for item in hidden),
            "recovered_same_ids": [item.track_id for item in tracked] == [item.track_id for item in recovered],
            "action_executed": False}


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
        from combatbot.ui.entity_annotation_dialog import EntityAnnotationDialog
        annotation = EntityAnnotationDialog(window.corpus.repository, window)
        annotation.showMaximized()
        app.processEvents()
        checks["entity_annotation"] = {"opened": annotation.isVisible(),
                                       "projected_frames": len(annotation.entries),
                                       "image_loaded": annotation.image is not None,
                                       "human_labels": len(annotation.labels)}
        checks["entity_annotation"]["sequence_review"] = hasattr(annotation, "sequence") and hasattr(annotation, "save_sequence")
        annotation.close()
        checks["sqlite_profiles"] = len(storage.list_profiles())
        checks["sqlite_statistics"] = storage.statistics()

        sample = np.zeros((64, 128, 3), dtype=np.uint8)
        cv2.rectangle(sample, (5, 5), (120, 58), (255, 255, 255), 2)
        checks["opencv"] = int(cv2.Canny(sample, 50, 120).sum()) > 0

        checks["gamedata_grid"] = _gamedata_grid_check()
        checks["grid_validation"] = _grid_validation_check()
        checks["hud_reader"] = _hud_reader_check()
        checks["entities"] = _entity_check()
        profile_data = os.environ.get("PYTHONBOT_SMOKE_PROFILE_DATA")
        if profile_data:
            current = storage.load_calibration(window.client_panel.profile_id or 1)
            checks["runtime_profiles"] = runtime_profiles_check(Path(profile_data), current)
        from combatbot.ui import entity_annotation_dialog as tracking_ui
        checks["tracking_ui"] = {"importable": True,
                                 "sequence_scoped": hasattr(tracking_ui.EntityAnnotationDialog,
                                                            "tracking_confirmation_dirty"),
                                 "unsaved_warning": tracking_ui.UNSAVED_TRACKING}

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
            checks["hud_reader"]["fixture_value"] == 7,
            checks["hud_reader"]["fallback_available"],
            not checks["hud_reader"]["action_executed"],
            checks["entities"]["player_detected"], checks["entities"]["enemy_detected"],
            checks["entities"]["track_ids"] == ["player", "enemy_1"],
            checks["entities"]["occlusion_distinct"], checks["entities"]["recovered_same_ids"],
            checks["entity_annotation"]["opened"],
            checks["entity_annotation"]["sequence_review"],
            checks["tracking_ui"]["sequence_scoped"],
            checks.get("runtime_profiles", {}).get("success", not bool(profile_data)),
            not checks["entities"]["action_executed"],
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
