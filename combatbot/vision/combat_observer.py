"""Observateur visuel réel en lecture seule du client DOFUS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from uuid import uuid4

import cv2
import numpy as np

from combatbot.runtime import app_data_root
from combatbot import __version__
from combatbot.vision.capture import capture_client
from combatbot.vision.combat_grid import classify_cell_occupancy, infer_combat_grid
from combatbot.vision.combat_models import (
    GRID_SOURCE_GAMEDATA, CombatObservation, EnemyObservation, GridCalibration, ObservationPacket,
)
from combatbot.vision.combat_ocr import NumberReader, read_small_number
from combatbot.vision.combat_tracker import CombatObservationTracker
from combatbot.vision.coordinates import CombatPoint, LayoutTransform
from combatbot.vision.gamedata_grid import GameDataGridResolver
from combatbot.vision.models import Calibration, CapturedFrame


FrameProvider = Callable[[], CapturedFrame]


def _zone(frame: CapturedFrame, calibration: Calibration, transform: LayoutTransform,
          name: str) -> np.ndarray | None:
    rect = calibration.zones.get(name)
    if rect is None:
        return None
    x, y, width, height = transform.normalized_rect_to_client(
        rect.to_normalized_rect()
    ).rounded()
    return frame.image[y:y + height, x:x + width].copy()


def _visual_activity(image: np.ndarray | None) -> float:
    if image is None or image.size == 0:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    texture = min(1.0, float(gray.std()) / 35)
    edges = min(1.0, float((cv2.Canny(gray, 45, 120) > 0).mean()) / 0.12)
    return 0.55 * texture + 0.45 * edges


class RealCombatObserver:
    """Produit des observations; ne dépend ni de Qt ni d'un exécuteur d'actions."""

    def __init__(self, hwnd: int, calibration: Calibration, *,
                 frame_provider: FrameProvider | None = None,
                 number_reader: NumberReader = read_small_number,
                 grid_calibration: GridCalibration | None = None,
                 tracker: CombatObservationTracker | None = None,
                 capture_context: dict[str, object] | None = None,
                 grid_resolver: GameDataGridResolver | None = None,
                 overlay_options: "OverlayOptions | None" = None) -> None:
        self.hwnd = hwnd
        self.calibration = calibration
        self.frame_provider = frame_provider or (lambda: capture_client(hwnd, activate=False))
        self.number_reader = number_reader
        self.grid_calibration = grid_calibration
        self.tracker = tracker or CombatObservationTracker()
        # Without resolver the historical pipeline is used unchanged.
        self.grid_resolver = grid_resolver
        if grid_resolver is not None and grid_resolver.legacy_calibration is None:
            grid_resolver.legacy_calibration = grid_calibration
        self.overlay_options = overlay_options or OverlayOptions()
        self.capture_context = dict(capture_context or {})
        self.session_id = str(self.capture_context.get("session_id") or f"session_{uuid4().hex[:12]}")
        self._frame_index = 0
        self._last_numbers: tuple[float, tuple[int | None, float], tuple[int | None, float]] | None = None

    def observe(self) -> ObservationPacket:
        started = time.perf_counter()
        frame = self.frame_provider()
        if frame.hwnd != self.hwnd:
            raise RuntimeError("La capture ne correspond plus à la fenêtre observée")
        if not self.calibration.compatible(frame.client.width, frame.client.height):
            raise RuntimeError("Calibration incompatible avec la taille actuelle du client")
        combat_rect = self.calibration.zones.get("combat")
        if combat_rect is None:
            raise RuntimeError("La zone de combat doit être calibrée")
        transform = self.calibration.layout_transform(frame)
        combat_image = _zone(frame, self.calibration, transform, "combat")
        assert combat_image is not None
        resolution = None
        if self.grid_resolver is not None:
            zones = {name: rect.to_normalized_rect() for name, rect in self.calibration.zones.items()}
            resolution = self.grid_resolver.resolve(combat_image, frame.client.size, zones)
            grid = resolution.grid
        else:
            grid = infer_combat_grid(combat_image, self.grid_calibration)
        player_reference = self.grid_calibration.player_reference_hsv if self.grid_calibration else None
        grid, player_cell, player_confidence, raw_enemies = classify_cell_occupancy(
            combat_image, grid, player_reference,
        )
        # With GAMEDATA_PROJECTED, occupancy is attached to the canonical DofusCellId.
        cell_ids = {cell.logical: cell.cell_id for cell in grid.cells} if grid.grid_source == GRID_SOURCE_GAMEDATA else {}
        enemies = tuple(EnemyObservation("", cell, center, confidence, cell_ids.get(cell))
                        for cell, center, confidence in raw_enemies)

        now = time.monotonic()
        ap_image = _zone(frame, self.calibration, transform, "ap")
        mp_image = _zone(frame, self.calibration, transform, "mp")
        if self._last_numbers is None or now - self._last_numbers[0] >= 0.55:
            ap = self.number_reader(ap_image) if ap_image is not None else (None, 0.0)
            mp = self.number_reader(mp_image) if mp_image is not None else (None, 0.0)
            self._last_numbers = (now, ap, mp)
        _, (ap, confidence_ap), (mp, confidence_mp) = self._last_numbers

        counter_signal = (_visual_activity(_zone(frame, self.calibration, transform, "ap")) +
                          _visual_activity(_zone(frame, self.calibration, transform, "mp"))) / 2
        end_signal = _visual_activity(_zone(frame, self.calibration, transform, "end_turn"))
        spell_signal = _visual_activity(_zone(frame, self.calibration, transform, "spell_bar"))
        signals = {"grid": grid.confidence, "counters": counter_signal,
                   "end_turn": end_signal, "spell_bar": spell_signal}
        combat_confidence = 0.55 * grid.confidence + 0.15 * counter_signal + 0.15 * end_signal + 0.15 * spell_signal
        combat_detected = len(grid.cells) >= 4 and combat_confidence >= 0.45

        # Le bouton et les compteurs doivent tous deux être lisibles avant de conclure au tour.
        turn_score = 0.6 * end_signal + 0.4 * max(confidence_ap, confidence_mp)
        if not combat_detected or end_signal < 0.2:
            player_turn = None
        elif turn_score >= 0.52 and (ap is not None or mp is not None):
            player_turn = True
        elif turn_score <= 0.24:
            player_turn = False
        else:
            player_turn = None
        essential = [grid.confidence, combat_confidence]
        essential.extend(score for value, score in ((ap, confidence_ap), (mp, confidence_mp)) if value is not None)
        if player_cell is not None:
            essential.append(player_confidence)
        observation_confidence = float(sum(essential) / len(essential)) if essential else 0.0
        raw = CombatObservation(
            combat_detected, combat_confidence, player_turn, turn_score,
            player_cell, player_confidence, enemies, grid, ap, mp,
            confidence_ap, confidence_mp, observation_confidence,
            signals=signals, timestamp=time.time(),
            player_cell_id=cell_ids.get(player_cell) if player_cell is not None else None,
        )
        observation = self.tracker.update(raw)
        annotated = draw_diagnostic_overlay(combat_image, observation, self.overlay_options)
        elapsed_ms = (time.perf_counter() - started) * 1000
        phase = "inconnue"
        if observation.result is not None:
            phase = "fin de combat"
        elif observation.combat_detected and observation.player_turn is True:
            phase = "mon tour"
        elif observation.combat_detected and observation.player_turn is False:
            phase = "tour ennemi"
        elif observation.combat_detected:
            phase = "combat"
        elif observation.combat_confidence < 0.2:
            phase = "hors combat"
        metadata = {
            "pythonbot_version": __version__,
            "profile": self.capture_context.get("profile"),
            "client_size": [frame.client.width, frame.client.height],
            "dpi": self.capture_context.get("dpi"),
            "window_id": self.capture_context.get("window_id", f"dofus-{self.hwnd:x}"),
            "calibration": {
                "profile_id": self.calibration.profile_id,
                "client_size": [self.calibration.client_width, self.calibration.client_height],
                "layout_signature": self.calibration.layout_signature,
                "zones": {name: rect.to_dict() for name, rect in self.calibration.zones.items()},
            },
            "layout_transform": transform.to_dict(),
            "coordinate_spaces": {
                "capture": "client", "frame": "combat", "grid": "combat", "hud": "client",
            },
            "tactical_mode": self.capture_context.get("tactical_mode", "inconnu"),
            "grid_source": grid.grid_source,
            "grid_source_reason": resolution.reason if resolution else "LEGACY_PIPELINE",
            "map_id_declared": grid.map_id_declared,
            "map_id_origin": "DECLARED_MANUALLY" if grid.map_id_declared is not None else None,
            "grid_profile_version": grid.grid_profile_version,
            "projection_confidence": grid.projection_confidence,
            "projection_status": grid.projection_status,
            "topology_consistency": grid.topology_consistency,
            "declared_map_suspect": grid.declared_map_suspect,
            "requires_recalibration": bool(resolution and resolution.requires_recalibration),
            "phase": phase,
            "analysis_ms": elapsed_ms,
            "global_confidence": observation.observation_confidence,
            "capture_source": frame.source,
            "session_id": self.session_id,
            "frame_index": self._frame_index,
        }
        self._frame_index += 1
        hud_crops = {name: value for name, value in (("ap", ap_image), ("mp", mp_image))
                     if value is not None and value.size > 0}
        return ObservationPacket(observation, combat_image, annotated, elapsed_ms, metadata, hud_crops)

    def set_player_reference(self, pixel: CombatPoint | tuple[int, int], packet: ObservationPacket) -> bool:
        """Mémorise la couleur au pixel explicitement désigné par l'utilisateur."""
        image = np.asarray(packet.original)
        point = pixel if isinstance(pixel, CombatPoint) else CombatPoint(*pixel)
        x, y = point.rounded()
        if not (0 <= x < image.shape[1] and 0 <= y < image.shape[0]):
            return False
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        y0, y1 = max(0, y - 5), min(hsv.shape[0], y + 6)
        x0, x1 = max(0, x - 5), min(hsv.shape[1], x + 6)
        pixels = hsv[y0:y1, x0:x1].reshape(-1, 3)
        saturated = pixels[pixels[:, 1] >= 65]
        if len(saturated) < 5:
            return False
        reference = tuple(float(value) for value in np.median(saturated, axis=0))
        current = self.grid_calibration
        grid = packet.observation.grid
        if current is None and grid.grid_source == GRID_SOURCE_GAMEDATA:
            # Reference holder only: no logical cells, so it never defines a legacy grid.
            current = GridCalibration((0.0, 0.0), grid.cell_width or 1.0, grid.cell_height or 1.0, ())
        if current is None:
            if not grid.cells or grid.cell_width is None or grid.cell_height is None:
                return False
            anchor = min(grid.cells, key=lambda item: (item.logical.y, item.logical.x))
            current = GridCalibration(anchor.center, grid.cell_width, grid.cell_height,
                                      tuple(item.logical for item in grid.cells))
        self.grid_calibration = GridCalibration(current.origin, current.cell_width, current.cell_height,
                                                current.logical_cells, reference)
        if self.grid_resolver is not None:
            self.grid_resolver.legacy_calibration = self.grid_calibration
        return True


@dataclass(frozen=True)
class OverlayOptions:
    """Couches de l'overlay de diagnostic ; les IDs sont sous-échantillonnés."""

    grid: bool = True
    cell_ids: bool = False
    coordinates: bool = False
    walkability: bool = False
    los: bool = False
    red_blue: bool = False
    candidates: bool = False
    anchors: bool = False
    label_every: int = 7


def _blend_cells(output: np.ndarray, polygons: list[np.ndarray], color: tuple[int, int, int], alpha: float) -> None:
    if not polygons:
        return
    layer = output.copy()
    cv2.fillPoly(layer, polygons, color)
    cv2.addWeighted(layer, alpha, output, 1 - alpha, 0, dst=output)


def draw_diagnostic_overlay(image: np.ndarray, observation: CombatObservation,
                            options: OverlayOptions | None = None, *,
                            candidates=(), anchors=()) -> np.ndarray:
    options = options or OverlayOptions()
    output = image.copy()
    grid = observation.grid
    projected = grid.grid_source == GRID_SOURCE_GAMEDATA
    polygon = lambda item: np.asarray(item.polygon, np.int32)  # noqa: E731
    if projected:
        if options.walkability:
            _blend_cells(output, [polygon(c) for c in grid.cells if c.static_traversable], (70, 160, 70), 0.22)
            _blend_cells(output, [polygon(c) for c in grid.cells if c.static_traversable is False], (40, 40, 40), 0.35)
        if options.los:
            _blend_cells(output, [polygon(c) for c in grid.cells if c.los_static is False], (160, 60, 160), 0.35)
        if options.red_blue:
            # GameData hints only: never presented as validated placement cells.
            _blend_cells(output, [polygon(c) for c in grid.cells if c.red_hint], (40, 40, 220), 0.4)
            _blend_cells(output, [polygon(c) for c in grid.cells if c.blue_hint], (220, 110, 40), 0.4)
        if options.grid:
            cv2.polylines(output, [polygon(c) for c in grid.cells], True, (80, 170, 230), 1, cv2.LINE_AA)
            occupied = [polygon(c) for c in grid.cells if c.state.value == "OCCUPIED"]
            cv2.polylines(output, occupied, True, (40, 190, 245), 2, cv2.LINE_AA)
        step = max(1, options.label_every)
        for item in grid.cells:
            if item.cell_id is None or item.cell_id % step:
                continue
            text = []
            if options.cell_ids:
                text.append(str(item.cell_id))
            if options.coordinates and item.grid_coordinate is not None:
                text.append(f"{item.grid_coordinate.x},{item.grid_coordinate.y}")
            if text:
                cv2.putText(output, " ".join(text), (item.center[0] - 12, item.center[1] + 3),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.3, (240, 240, 240), 1, cv2.LINE_AA)
    else:
        for item in grid.cells:
            color = (80, 170, 230)
            if item.state.value == "OCCUPIED":
                color = (40, 190, 245)
            cv2.polylines(output, [np.asarray(item.polygon, np.int32)], True, color, 1, cv2.LINE_AA)
            cv2.putText(output, f"{item.logical.x},{item.logical.y}",
                        (item.center[0] - 13, item.center[1] + 3), cv2.FONT_HERSHEY_SIMPLEX,
                        0.3, color, 1, cv2.LINE_AA)
    if options.candidates:
        for candidate in candidates:
            cv2.circle(output, (round(candidate[0]), round(candidate[1])), 3, (0, 220, 255), -1)
    if options.anchors:
        for cell_id, point in anchors:
            cv2.drawMarker(output, (round(point[0]), round(point[1])), (255, 0, 255), cv2.MARKER_CROSS, 12, 2)
            cv2.putText(output, str(cell_id), (round(point[0]) + 6, round(point[1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 255), 1, cv2.LINE_AA)
    if observation.player_cell is not None:
        item = grid.cell_at(observation.player_cell)
        if item:
            cv2.circle(output, item.center, 10, (80, 235, 120), 3)
            cv2.putText(output, "JOUEUR", (item.center[0] + 8, item.center[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (80, 235, 120), 1, cv2.LINE_AA)
    for enemy in observation.enemies:
        cv2.circle(output, enemy.center, 9, (60, 70, 245), 3)
        cv2.putText(output, enemy.id, (enemy.center[0] + 7, enemy.center[1] + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (60, 70, 245), 1, cv2.LINE_AA)
    return output


def save_debug_observation(packet: ObservationPacket, directory: Path | None = None) -> Path:
    root = directory or (app_data_root() / "data" / "debug")
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time_ns() % 1_000_000):06d}"
    original_path = root / f"{stamp}-original.png"
    annotated_path = root / f"{stamp}-annotated.png"
    json_path = root / f"{stamp}-observation.json"
    if not cv2.imwrite(str(original_path), np.asarray(packet.original)):
        raise OSError("Impossible d'enregistrer la capture originale")
    if not cv2.imwrite(str(annotated_path), np.asarray(packet.annotated)):
        raise OSError("Impossible d'enregistrer la capture annotée")
    hud_files: dict[str, str] = {}
    if packet.hud_crops:
        hud_directory = root / f"{stamp}-hud"
        hud_directory.mkdir(parents=True, exist_ok=True)
        for name, filename in (("ap", "ap_original.png"), ("mp", "mp_original.png")):
            crop = packet.hud_crops.get(name)
            if crop is not None and np.asarray(crop).size:
                path = hud_directory / filename
                if not cv2.imwrite(str(path), np.asarray(crop)):
                    raise OSError(f"Impossible d'enregistrer le crop {name.upper()}")
                hud_files[name] = f"{hud_directory.name}/{filename}"
    prediction = asdict(packet.observation)
    grid = prediction.get("grid", {})
    cells = grid.get("cells", [])
    payload = {
        "schema_version": 1,
        "observation_id": f"obs_{uuid4().hex[:16]}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session_id": packet.metadata.get("session_id"),
        "frame_index": packet.metadata.get("frame_index"),
        "capture": dict(packet.metadata),
        "prediction": prediction,
        "grid_snapshot": {
            "cell_count": len(cells),
            "confidence": grid.get("confidence"),
            # LOT 3B-2 : identité canonique DofusCellId + source de grille explicite.
            "grid_source": grid.get("grid_source"),
            "map_id_declared": grid.get("map_id_declared"),
            "map_id_origin": "DECLARED_MANUALLY" if grid.get("map_id_declared") is not None else None,
            "grid_profile_version": grid.get("grid_profile_version"),
            "projection_confidence": grid.get("projection_confidence"),
            "cells": cells,
            "player_cell": prediction.get("player_cell"),
            "player_confidence": prediction.get("player_confidence"),
            "enemies": prediction.get("enemies", []),
        },
        "files": {
            "frame": original_path.name,
            "overlay": annotated_path.name,
            **hud_files,
        },
    }
    json_path.write_text(json.dumps(
        payload, ensure_ascii=False, indent=2,
        default=lambda value: value.value if hasattr(value, "value") else str(value),
    ), encoding="utf-8")
    return json_path
