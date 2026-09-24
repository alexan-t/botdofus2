from __future__ import annotations

import cv2
import numpy as np

import combatbot.vision.hud_reader as hud_reader
from combatbot.vision.combat_ocr import read_small_number
from combatbot.vision.hud_reader import (
    GlyphRead, GlyphTemplateLibrary, HUDReader, NumberReadReason, NumberReadResult,
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


def test_white_hud_mask_keeps_two_glyphs_before_bright_fallback(monkeypatch) -> None:
    two = np.zeros((50, 70), np.uint8)
    cv2.rectangle(two, (14, 10), (20, 39), 255, -1)
    cv2.rectangle(two, (35, 10), (45, 39), 255, -1)
    one = np.zeros_like(two)
    cv2.rectangle(one, (35, 9), (46, 40), 255, -1)
    monkeypatch.setattr(hud_reader, "_candidate_masks", lambda _image: (two, one))
    assert len(segment_glyphs(np.zeros((50, 70, 3), np.uint8))) == 2


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


def test_validation_threshold_skips_rapidocr_for_accepted_template() -> None:
    calls = []
    reader = HUDReader(library(2, 3), rapidocr_reader=lambda *_: (calls.append(True) or (3, 1.0)))
    result = reader.read(digit_crop("2"), "AP")
    assert result.value == 2
    assert result.confidence >= reader.skip_rapidocr_confidence
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


def test_real_transition_high_confidence() -> None:
    # Transition réelle observée : 13 PA puis 11 PA, lecture spécialisée à 0,97.
    tracker = NumberTemporalTracker()
    tracker.update(accepted(13, .93))
    result = tracker.update(accepted(11, .97))
    assert result.value == 11 and result.temporal_state is TemporalState.STABLE


def test_low_confidence_transition_stays_unknown() -> None:
    # 6 PM → 3 PM lu à 0,82 : une seule frame ne remplace pas la valeur stable.
    tracker = NumberTemporalTracker()
    tracker.update(accepted(6, .94))
    first = tracker.update(accepted(3, .82))
    assert first.value is None and first.reason is NumberReadReason.TEMPORAL_CONFLICT
    assert tracker.update(accepted(3, .82)).value == 3


def test_unreadable_changed_counter_is_not_held() -> None:
    # Séquence réelle 11 → 7 : le 7 illisible ne doit pas laisser 11 affiché.
    tracker = NumberTemporalTracker()
    tracker.update(accepted(11, .97))
    glyph = GlyphRead((20, 10, 15, 30), 3, .69, 2, .68, .015, .9)
    changed = NumberReadResult(None, .49, NumberReadSource.UNKNOWN, (glyph,),
                               reason=NumberReadReason.LOW_MARGIN,
                               raw_candidates={"specialized_value": 3})
    result = tracker.update(changed)
    assert result.value is None and result.temporal_state is TemporalState.UNKNOWN


def test_unreadable_glitch_without_glyph_is_still_held_once() -> None:
    tracker = NumberTemporalTracker(hold_frames=1)
    tracker.update(accepted(11, .97))
    held = tracker.update(unknown())
    assert held.value == 11 and held.reason is NumberReadReason.TEMPORAL_HOLD


def clipped_fifteen() -> np.ndarray:
    full = digit_crop("15")
    one = segment_glyphs(full)[0]
    x, _y, width, _height = one.bbox
    # La ROI démarre au milieu du « 1 » : seul le « 5 » reste entier.
    return np.ascontiguousarray(full[:, x + width // 2:])


def test_real_pm_crop_not_clipped() -> None:
    reader = HUDReader(library(1, 5))
    result = reader.read(clipped_fifteen(), "AP")
    assert result.value is None
    assert result.reason is NumberReadReason.CLIPPED_GLYPH
    assert segment_glyphs(clipped_fifteen()) == ()


def test_pm_roi_keeps_full_glyph() -> None:
    reader = HUDReader(library(1, 5))
    assert not hud_reader.glyph_clipped(digit_crop("15"))
    assert reader.read(digit_crop("15"), "AP").value == 15


def test_rapidocr_not_called_on_clipped_crop() -> None:
    calls = []
    reader = HUDReader(library(1, 5), rapidocr_reader=lambda *_: (calls.append(True) or (5, 1.0)))
    assert reader.read(clipped_fifteen(), "AP").value is None
    assert calls == []


def hud_glyph_crop(digit: str) -> np.ndarray:
    """Forme proche de la police HUD réelle : 1 = hampe + petit drapeau, 7 = barre + diagonale."""
    image = np.zeros((65, 66, 3), np.uint8)
    if digit == "1":
        cv2.rectangle(image, (30, 16), (35, 47), (255, 255, 255), -1)
        cv2.rectangle(image, (26, 16), (29, 20), (255, 255, 255), -1)
    else:
        cv2.rectangle(image, (24, 16), (40, 21), (255, 255, 255), -1)
        cv2.fillConvexPoly(image, np.array([[35, 22], [40, 22], [31, 47], [26, 47]], np.int32),
                           (255, 255, 255))
    return image


def real_like_library() -> GlyphTemplateLibrary:
    result = GlyphTemplateLibrary()
    for digit in ("1", "7"):
        result.add("AP", int(digit), segment_glyphs(hud_glyph_crop(digit))[0].image)
    return result


def test_one_real_like_glyph() -> None:
    result = HUDReader(real_like_library()).read(hud_glyph_crop("1"), "AP")
    assert result.value == 1 and result.reason is NumberReadReason.ACCEPTED


def test_seven_real_like_glyph() -> None:
    result = HUDReader(real_like_library()).read(hud_glyph_crop("7"), "AP")
    assert result.value == 7 and result.reason is NumberReadReason.ACCEPTED


def test_one_seven_heuristic_is_not_trusted_on_narrow_real_one() -> None:
    # Mesure réelle 3B-4R : la largeur du sommet vaut 1,0 sur les 39 « 1 » annotés.
    # L'heuristique n'est donc qu'un diagnostic ; elle ne doit pas transformer 1 en 7.
    glyph = segment_glyphs(hud_glyph_crop("1"))[0].image
    _preferred, features = hud_reader.distinguish_one_seven(glyph)
    assert features["top_span"] == 1.0
    assert HUDReader(real_like_library()).read(hud_glyph_crop("1"), "AP").value == 1


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
