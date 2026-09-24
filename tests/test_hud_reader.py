from __future__ import annotations

import cv2
import numpy as np

from combatbot.vision.combat_ocr import read_small_number
from combatbot.vision.hud_reader import (
    GlyphTemplateLibrary, HUDReader, NumberReadReason, NumberReadResult,
    NumberReadSource, NumberTemporalTracker, TemporalState, segment_glyphs,
)


def digit_crop(text: str) -> np.ndarray:
    image = np.zeros((58, 28 * len(text) + 10, 3), np.uint8)
    for index, digit in enumerate(text):
        cv2.putText(image, digit, (5 + 28 * index, 46), cv2.FONT_HERSHEY_SIMPLEX,
                    1.35, (255, 255, 255), 2, cv2.LINE_AA)
    return image


def library(*digits: int) -> GlyphTemplateLibrary:
    result = GlyphTemplateLibrary()
    for digit in digits:
        segments = segment_glyphs(digit_crop(str(digit)))
        assert len(segments) == 1
        result.add("SHARED", digit, segments[0].image)
    return result


def accepted(value: int, confidence: float = 0.8) -> NumberReadResult:
    return NumberReadResult(value, confidence, NumberReadSource.GLYPH_TEMPLATE,
                            best_score=.9, second_score=.5, margin=.4,
                            reason=NumberReadReason.ACCEPTED)


def unknown() -> NumberReadResult:
    return NumberReadResult(None, 0.0, NumberReadSource.UNKNOWN,
                            reason=NumberReadReason.NO_GLYPH)


def test_blank_crop_returns_unknown() -> None:
    assert HUDReader(library(1)).read(np.zeros((40, 40, 3), np.uint8), "AP").value is None


def test_single_digit_segmentation() -> None:
    assert len(segment_glyphs(digit_crop("7"))) == 1


def test_two_digit_segmentation() -> None:
    assert len(segment_glyphs(digit_crop("12"))) == 2


def test_template_reader_one() -> None:
    result = HUDReader(library(1, 7)).read(digit_crop("1"), "AP")
    assert result.value == 1 and result.source is NumberReadSource.GLYPH_TEMPLATE


def test_template_reader_seven() -> None:
    result = HUDReader(library(1, 7)).read(digit_crop("7"), "MP")
    assert result.value == 7 and result.source is NumberReadSource.GLYPH_TEMPLATE


def test_one_seven_ambiguous_returns_unknown() -> None:
    templates = GlyphTemplateLibrary()
    glyph = segment_glyphs(digit_crop("1"))[0].image
    templates.add("SHARED", 1, glyph)
    templates.add("SHARED", 7, glyph)
    result = HUDReader(templates).read(digit_crop("1"), "AP")
    assert result.value is None and result.reason is NumberReadReason.AMBIGUOUS_1_7


def test_clear_one_not_read_as_seven() -> None:
    assert HUDReader(library(1, 7)).read(digit_crop("1"), "AP").value == 1


def test_clear_seven_not_read_as_one() -> None:
    assert HUDReader(library(1, 7)).read(digit_crop("7"), "AP").value == 7


def test_low_margin_returns_unknown() -> None:
    templates = GlyphTemplateLibrary()
    glyph = segment_glyphs(digit_crop("3"))[0].image
    templates.add("SHARED", 3, glyph)
    templates.add("SHARED", 8, glyph)
    result = HUDReader(templates).read(digit_crop("3"), "AP")
    assert result.value is None and result.reason is NumberReadReason.LOW_MARGIN


def test_out_of_range_rejected_not_corrected() -> None:
    result = HUDReader(library(1, 5), rapidocr_reader=lambda *_: (5, 1.0)).read(
        digit_crop("15"), "AP", maximum=10)
    assert result.value is None and result.reason is NumberReadReason.OUT_OF_RANGE


def test_rapidocr_disagreement_returns_unknown() -> None:
    reader = HUDReader(library(3, 8), rapidocr_reader=lambda *_: (8, .99),
                       skip_rapidocr_confidence=1.1)
    result = reader.read(digit_crop("3"), "AP")
    assert result.value is None and result.reason is NumberReadReason.OCR_DISAGREEMENT


def test_rapidocr_agreement_can_support_result() -> None:
    reader = HUDReader(library(3, 8), rapidocr_reader=lambda *_: (3, .99),
                       skip_rapidocr_confidence=1.1)
    result = reader.read(digit_crop("3"), "AP")
    assert result.value == 3 and result.source is NumberReadSource.CONSENSUS


def test_high_confidence_specialized_does_not_require_rapidocr() -> None:
    calls = []
    reader = HUDReader(library(2, 3), rapidocr_reader=lambda *_: (calls.append(True) or (2, 1.0)))
    assert reader.read(digit_crop("2"), "AP").value == 2
    assert calls == []


def test_temporal_stability() -> None:
    tracker = NumberTemporalTracker(high_confidence=.9)
    assert tracker.update(accepted(7)).value == 7
    changing = tracker.update(accepted(6))
    assert changing.value is None and changing.temporal_state is TemporalState.CHANGING
    assert tracker.update(accepted(6)).value == 6


def test_high_confidence_change_can_be_accepted() -> None:
    tracker = NumberTemporalTracker(high_confidence=.9)
    tracker.update(accepted(7))
    assert tracker.update(accepted(4, .95)).value == 4


def test_old_value_expires() -> None:
    tracker = NumberTemporalTracker(hold_frames=1)
    tracker.update(accepted(7))
    assert tracker.update(unknown()).value == 7
    assert tracker.update(unknown()).value is None


def test_ap_and_mp_use_same_reader() -> None:
    reader = HUDReader(library(3))
    assert reader.read(digit_crop("3"), "AP").value == 3
    assert reader.read(digit_crop("3"), "MP").value == 3


def test_reader_serialization(tmp_path) -> None:
    result = HUDReader(library(1, 7)).read(digit_crop("7"), "AP")
    assert NumberReadResult.from_dict(result.to_dict()) == result
    templates = library(7)
    templates.save(tmp_path)
    assert HUDReader(GlyphTemplateLibrary.load(tmp_path)).read(digit_crop("7"), "AP").value == 7


def test_old_read_small_number_compatibility(monkeypatch) -> None:
    class OCRResult:
        txts = ("7",)
        scores = (.98,)

    monkeypatch.setattr("combatbot.vision.tooltip._ocr_engine", lambda: lambda _image: OCRResult())
    assert read_small_number(digit_crop("7"))[0] == 7
