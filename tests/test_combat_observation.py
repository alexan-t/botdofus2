from dataclasses import replace

import cv2
import numpy as np

from combatbot.models import Cell
from combatbot.vision.combat_grid import classify_cell_occupancy, infer_combat_grid
from combatbot.vision.combat_models import (
    CellVisualState, CombatGridObservation, CombatObservation, EnemyObservation,
    GridCalibration, ObservedCell,
)
from combatbot.vision.combat_observer import RealCombatObserver, save_debug_observation
from combatbot.vision.combat_ocr import preprocess_number_variants
from combatbot.vision.combat_tracker import CombatObservationTracker
from combatbot.vision.models import Calibration, CapturedFrame, ClientRect, RelativeRect, ZoneEvidence
from combatbot.vision.hud_reader import NumberReadReason, NumberReadResult, NumberReadSource


WIDTH, HEIGHT = 800, 600
COMBAT_RECT = (80, 60, 560, 360)
GRID = GridCalibration((280.0, 55.0), 76.0, 38.0,
                       tuple(Cell(x, y) for y in range(4) for x in range(4)),
                       (60.0, 255.0, 255.0))


def _diamond(cell: Cell) -> np.ndarray:
    cx = GRID.origin[0] + (cell.x - cell.y) * GRID.cell_width / 2
    cy = GRID.origin[1] + (cell.x + cell.y) * GRID.cell_height / 2
    return np.asarray(((cx, cy - GRID.cell_height / 2), (cx + GRID.cell_width / 2, cy),
                       (cx, cy + GRID.cell_height / 2), (cx - GRID.cell_width / 2, cy)), np.int32)


def combat_frame(*, combat: bool = True, player: Cell | None = Cell(1, 1),
                 enemies: tuple[Cell, ...] = (Cell(3, 1),)) -> CapturedFrame:
    image = np.full((HEIGHT, WIDTH, 3), 24, np.uint8)
    x, y, width, height = COMBAT_RECT
    arena = image[y:y + height, x:x + width]
    if combat:
        for cell in GRID.logical_cells:
            cv2.polylines(arena, [_diamond(cell)], True, (170, 170, 170), 2, cv2.LINE_AA)
        if player is not None:
            center = tuple(np.mean(_diamond(player), axis=0).astype(int))
            cv2.circle(arena, center, 9, (0, 255, 0), -1)
        for enemy in enemies:
            center = tuple(np.mean(_diamond(enemy), axis=0).astype(int))
            cv2.circle(arena, center, 9, (0, 0, 255), -1)
        cv2.rectangle(image, (600, 520), (775, 570), (35, 110, 190), -1)
        cv2.putText(image, "TOUR", (640, 555), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        for index in range(7):
            cv2.rectangle(image, (240 + index * 45, 520), (278 + index * 45, 560), (80, 95, 120), 2)
        cv2.putText(image, "6", (45, 552), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        cv2.putText(image, "3", (125, 552), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
    return CapturedFrame(7, ClientRect(0, 0, WIDTH, HEIGHT), image)


def calibration() -> Calibration:
    zones = {
        "combat": RelativeRect(0.1, 0.1, 0.7, 0.6),
        "ap": RelativeRect(0.04, 0.84, 0.08, 0.11),
        "mp": RelativeRect(0.14, 0.84, 0.08, 0.11),
        "spell_bar": RelativeRect(0.29, 0.84, 0.42, 0.11),
        "end_turn": RelativeRect(0.74, 0.84, 0.24, 0.11),
    }
    meta = {name: ZoneEvidence(1.0, "test", "confirmée") for name in zones}
    return Calibration(1, WIDTH, HEIGHT, zones, meta)


def number_reader(image: np.ndarray) -> tuple[int | None, float]:
    # La position PA contient le glyphe 6, la position PM le glyphe 3.
    return (6, 0.92) if image.shape[1] <= 66 and float(image[:, : image.shape[1] // 2].mean()) > 23 else (3, 0.9)


def raw_observation(*, combat=True, turn=True, enemies=(), player=Cell(0, 0), ap=6, mp=3):
    cells = (ObservedCell(Cell(0, 0), (20, 20), ((20, 10), (30, 20), (20, 30), (10, 20))),)
    grid = CombatGridObservation(cells, 20, 20, 0, 0.9)
    observed = tuple(EnemyObservation("", cell, (40 + i * 20, 40), 0.9)
                     for i, cell in enumerate(enemies))
    return CombatObservation(combat, 0.9, turn, 0.9, player, 0.9 if player else 0,
                             observed, grid, ap, mp, 0.9 if ap is not None else 0,
                             0.9 if mp is not None else 0, 0.9)


def test_combat_absent_remains_absent_and_unknown() -> None:
    frame = combat_frame(combat=False)
    observer = RealCombatObserver(7, calibration(), frame_provider=lambda: frame,
                                  number_reader=lambda _image: (None, 0), grid_calibration=GRID)
    packet = observer.observe()
    assert not packet.observation.combat_detected
    assert packet.observation.player_turn is None
    assert packet.observation.ap is None and packet.observation.mp is None


def test_combat_present_reads_grid_player_enemies_ap_pm() -> None:
    frame = combat_frame()
    calls = iter(((6, 0.92), (3, 0.9)))
    observer = RealCombatObserver(7, calibration(), frame_provider=lambda: frame,
                                  number_reader=lambda _image: next(calls), grid_calibration=GRID)
    observer.observe()  # première frame de stabilisation
    packet = observer.observe()
    observation = packet.observation
    assert observation.combat_detected
    assert observation.ap == 6 and observation.mp == 3
    assert observation.player_cell == Cell(1, 1)
    assert Cell(3, 1) in observation.enemy_cells
    assert len(observation.grid.cells) == 16
    assert observation.player_turn is True
    assert packet.elapsed_ms >= 0


def test_observer_exposes_rich_hud_evidence() -> None:
    class Reader:
        def read(self, _image, kind):
            value = 6 if kind == "AP" else 3
            return NumberReadResult(value, .93, NumberReadSource.GLYPH_TEMPLATE,
                                    best_score=.95, second_score=.5, margin=.45,
                                    reason=NumberReadReason.ACCEPTED)

    frame = combat_frame()
    observer = RealCombatObserver(7, calibration(), frame_provider=lambda: frame,
                                  hud_reader=Reader(), grid_calibration=GRID)
    packet = observer.observe()
    assert packet.observation.ap == 6 and packet.observation.mp == 3
    assert packet.observation.ap_read["source"] == "GLYPH_TEMPLATE"
    assert packet.metadata["hud_reader"]["ap"]["margin"] == .45
    assert packet.metadata["coordinate_spaces"]["frame"] == "combat"
    assert packet.metadata["layout_transform"]["client_screen_origin"] == {"x": 0.0, "y": 0.0}


def test_automatic_grid_and_pixel_conversion() -> None:
    arena = combat_frame().image[60:420, 80:640]
    grid = infer_combat_grid(arena)
    assert len(grid.cells) >= 12 and grid.confidence >= 0.65
    target = grid.cells[5]
    assert grid.pixel_to_cell(target.center) == target.logical
    assert grid.pixel_to_cell((0, 350)) is None


def test_grid_calibration_legacy_origin_stays_combat_local() -> None:
    legacy = {
        "origin": [280.0, 55.0], "cell_width": 76.0, "cell_height": 38.0,
        "logical_cells": [{"x": 0, "y": 0}], "player_reference_hsv": [60, 255, 255],
    }
    calibration = GridCalibration.from_dict(legacy)
    assert calibration.coordinate_space == "combat"
    assert calibration.origin.rounded() == (280, 55)
    assert GridCalibration.from_dict(calibration.to_dict()) == calibration


def test_unknown_is_never_free() -> None:
    arena = combat_frame(player=None, enemies=()).image[60:420, 80:640]
    grid = infer_combat_grid(arena, replace(GRID, player_reference_hsv=None))
    classified, player, _score, enemies = classify_cell_occupancy(arena, grid)
    assert player is None and not enemies
    assert classified.cells
    assert all(cell.state is CellVisualState.UNKNOWN for cell in classified.cells)
    assert all(cell.state is not CellVisualState.FREE for cell in classified.cells)


def test_enemy_identifier_survives_move_and_temporary_disappearance() -> None:
    tracker = CombatObservationTracker(disappearance_tolerance=2)
    first = tracker.update(raw_observation(enemies=(Cell(1, 1),)))
    identifier = first.enemies[0].id
    moved = tracker.update(raw_observation(enemies=(Cell(2, 1),)))
    assert moved.enemies[0].id == identifier
    missing = tracker.update(raw_observation(enemies=()))
    assert missing.enemies[0].id == identifier and missing.enemies[0].confidence < 0.9
    returned = tracker.update(raw_observation(enemies=(Cell(2, 2),)))
    assert returned.enemies[0].id == identifier


def test_turn_transition_requires_multiple_frames() -> None:
    tracker = CombatObservationTracker()
    tracker.update(raw_observation(turn=True))
    assert tracker.update(raw_observation(turn=True)).player_turn is True
    assert tracker.update(raw_observation(turn=False)).player_turn is True
    assert tracker.update(raw_observation(turn=False)).player_turn is False


def test_combat_end_is_stable_and_result_stays_unknown() -> None:
    tracker = CombatObservationTracker()
    tracker.update(raw_observation(combat=True))
    tracker.update(raw_observation(combat=True))
    assert tracker.update(raw_observation(combat=False, turn=None)).combat_detected
    ended = tracker.update(raw_observation(combat=False, turn=None))
    assert not ended.combat_detected
    assert ended.result == "unknown"


def test_incomplete_observation_is_not_safe_for_decision() -> None:
    incomplete = raw_observation(player=None, ap=None, mp=None, turn=None)
    assert not incomplete.safe_for_decision
    assert not raw_observation(enemies=(Cell(1, 1),)).safe_for_decision  # grille encore UNKNOWN


def test_number_preprocessing_enlarges_and_thresholds() -> None:
    image = np.full((18, 28, 3), 20, np.uint8)
    cv2.putText(image, "6", (6, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (240, 240, 240), 1)
    variants = preprocess_number_variants(image)
    assert len(variants) == 4
    assert all(item.shape[0] > image.shape[0] for item in variants)
    assert set(np.unique(variants[1])).issubset({0, 255})


def test_debug_capture_is_only_saved_on_explicit_call(tmp_path) -> None:
    frame = combat_frame()
    calls = iter(((6, 0.9), (3, 0.9)))
    observer = RealCombatObserver(7, calibration(), frame_provider=lambda: frame,
                                  number_reader=lambda _image: next(calls), grid_calibration=GRID)
    packet = observer.observe()
    assert list(tmp_path.iterdir()) == []
    json_path = save_debug_observation(packet, tmp_path)
    assert json_path.exists()
    assert len(list(tmp_path.glob("*.png"))) == 2
    assert '"safe_for_decision"' not in json_path.read_text(encoding="utf-8")
