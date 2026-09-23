from pathlib import Path

import cv2
import numpy as np
import pytest

from combatbot.vision.icons import KnownIcon, scan_spell_bar
from combatbot.vision.models import Calibration, CapturedFrame, ClientRect, RelativeRect, ZONE_NAMES
from combatbot.vision.tooltip import parse_tooltip, read_visible_text


def test_relative_calibration_scales_and_invalidates_changed_layout() -> None:
    zones = {name: RelativeRect(0.1, 0.2, 0.3, 0.1) for name in ZONE_NAMES}
    calibration = Calibration(1, 1000, 600, zones)
    calibration.validate()
    assert calibration.compatible(1200, 720)
    assert not calibration.compatible(1200, 600)
    frame = CapturedFrame(5, ClientRect(0, 0, 1200, 720), np.zeros((720, 1200, 3), dtype=np.uint8))
    assert calibration.crop(frame, "spell_bar").shape == (72, 360, 3)


def test_scanner_finds_icons_empties_duplicates_and_known_icon() -> None:
    bar = np.full((80, 120, 3), 70, dtype=np.uint8)  # 2 rangées × 3 colonnes
    icon = np.full((40, 40, 3), (35, 60, 160), dtype=np.uint8)
    cv2.circle(icon, (20, 20), 13, (10, 220, 245), -1)
    cv2.line(icon, (5, 32), (33, 7), (250, 20, 10), 3)
    bar[0:40, 0:40] = icon
    bar[0:40, 40:80] = icon  # doublon
    bar[40:80, 80:120] = cv2.flip(icon, 1)

    first = scan_spell_bar(bar, page=1, columns=3, rows=2)
    assert first.slots_total == 6
    assert first.empty_slots == (3, 4, 5)
    assert [item.slot for item in first.candidates] == [1, 2, 6]
    assert first.candidates[1].duplicate_of == 1
    assert all(item.known_name is None for item in first.candidates)

    saved = first.candidates[0]
    known = [KnownIcon(7, "Nom confirmé manuellement", saved.icon_png, saved.visual_hash)]
    second = scan_spell_bar(bar, page=2, columns=3, rows=2, known_icons=known)
    assert second.candidates[0].status == "Reconnu"
    assert second.candidates[0].known_spell_id == 7
    assert second.candidates[0].recognition_confidence == pytest.approx(1.0, abs=1e-5)


def test_user_supplied_spellbar_static_capture() -> None:
    fixture = Path(__file__).parent / "fixtures" / "user_spellbar.png"
    frame = cv2.imread(str(fixture))
    assert frame is not None and frame.shape[:2] == (121, 618)
    # Le contrôle de pagination à droite n'est pas une case de sort.
    zones = {name: RelativeRect(0.1, 0.1, 0.2, 0.2) for name in ZONE_NAMES}
    zones["spell_bar"] = RelativeRect(0, 0, 575 / 618, 1)
    calibration = Calibration(1, 618, 121, zones)
    captured = CapturedFrame(1, ClientRect(0, 0, 618, 121), frame)
    result = scan_spell_bar(calibration.crop(captured, "spell_bar"), page=1, columns=10, rows=2)
    assert len(result.candidates) == 20
    assert result.empty_slots == ()
    assert all(item.duplicate_of is None for item in result.candidates)
    assert all(item.status == "Inconnu" and item.known_name is None for item in result.candidates)


def test_tooltip_parser_keeps_missing_fields_unknown() -> None:
    recognized = parse_tooltip(
        "Sort personnel\nCoût 3 PA\nPortée 1 - 4\nPortée non modifiable\n"
        "Lancer en ligne\nSans ligne de vue\n2 lancers par tour\n1 par cible\nDégâts : 12",
        0.91,
    )
    assert recognized.fields["name"] == "Sort personnel"
    assert recognized.fields["ap_cost"] == 3
    assert (recognized.fields["min_range"], recognized.fields["max_range"]) == (1, 4)
    assert recognized.fields["modifiable_range"] is False
    assert recognized.fields["line_cast"] is True
    assert recognized.fields["line_of_sight"] is False
    assert recognized.fields["per_turn"] == 2
    assert recognized.fields["per_target"] == 1
    assert recognized.fields["damage"] == 12
    unknown = parse_tooltip("Texte illisible", 0.2)
    assert unknown.fields["ap_cost"] is None
    assert "ap_cost" in unknown.uncertain_fields


def test_rapidocr_reads_synthetic_visible_text() -> None:
    image = np.full((100, 420, 3), 255, dtype=np.uint8)
    cv2.putText(image, "3 PA  Portee 1-4", (8, 55), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    text, confidence = read_visible_text(image)
    assert "PA" in text and "3" in text
    assert confidence > 0.5
