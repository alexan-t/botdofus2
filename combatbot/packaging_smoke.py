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

        ocr_image = np.full((100, 420, 3), 255, dtype=np.uint8)
        cv2.putText(ocr_image, "3 PA  Portee 1-4", (8, 55), cv2.FONT_HERSHEY_SIMPLEX, 1,
                    (0, 0, 0), 2)
        text, confidence = read_visible_text(ocr_image)
        checks["rapidocr"] = {"loaded": True, "text": text, "confidence": confidence}

        windows = list_dofus_windows()
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
