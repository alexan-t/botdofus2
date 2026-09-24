"""Lecteur spécialisé des petits compteurs PA/PM, prudent et indépendant de Qt.

Les templates proviennent uniquement de crops du corpus accompagnés d'une
vérité humaine. RapidOCR reste un signal secondaire et n'est pas consulté
lorsqu'une classification spécialisée est déjà très sûre.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
import json
from pathlib import Path
import time
import zlib

import cv2
import numpy as np


CANONICAL_SIZE = (24, 36)  # largeur, hauteur


class NumberReadSource(str, Enum):
    GLYPH_TEMPLATE = "GLYPH_TEMPLATE"
    RAPIDOCR = "RAPIDOCR"
    CONSENSUS = "CONSENSUS"
    UNKNOWN = "UNKNOWN"


class NumberReadReason(str, Enum):
    ACCEPTED = "ACCEPTED"
    AMBIGUOUS_1_7 = "AMBIGUOUS_1_7"
    LOW_MARGIN = "LOW_MARGIN"
    NO_GLYPH = "NO_GLYPH"
    SEGMENTATION_FAILED = "SEGMENTATION_FAILED"
    CLIPPED_GLYPH = "CLIPPED_GLYPH"
    NO_TEMPLATE = "NO_TEMPLATE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    TEMPORAL_CONFLICT = "TEMPORAL_CONFLICT"
    TEMPORAL_HOLD = "TEMPORAL_HOLD"
    OCR_DISAGREEMENT = "OCR_DISAGREEMENT"
    OCR_FALLBACK = "OCR_FALLBACK"


class TemporalState(str, Enum):
    STABLE = "STABLE"
    CHANGING = "CHANGING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class GlyphRead:
    bbox: tuple[int, int, int, int]
    best_digit: int | None
    best_score: float
    second_digit: int | None
    second_score: float
    margin: float
    segmentation_quality: float
    candidates: tuple[tuple[int, float], ...] = ()
    one_seven_features: dict[str, float | int | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "bbox": list(self.bbox), "best_digit": self.best_digit,
            "best_score": self.best_score, "second_digit": self.second_digit,
            "second_score": self.second_score, "margin": self.margin,
            "segmentation_quality": self.segmentation_quality,
            "candidates": [[digit, score] for digit, score in self.candidates],
            "one_seven_features": dict(self.one_seven_features),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "GlyphRead":
        bbox = raw.get("bbox")
        candidates = raw.get("candidates", ())
        features = raw.get("one_seven_features", {})
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise ValueError("bbox de glyphe invalide")
        if not isinstance(candidates, (list, tuple)) or not isinstance(features, Mapping):
            raise ValueError("preuves de glyphe invalides")
        return cls(
            tuple(int(value) for value in bbox),  # type: ignore[arg-type]
            int(raw["best_digit"]) if raw.get("best_digit") is not None else None,
            float(raw.get("best_score", 0.0)),
            int(raw["second_digit"]) if raw.get("second_digit") is not None else None,
            float(raw.get("second_score", 0.0)), float(raw.get("margin", 0.0)),
            float(raw.get("segmentation_quality", 0.0)),
            tuple((int(item[0]), float(item[1])) for item in candidates),  # type: ignore[index]
            {str(key): value for key, value in features.items()},
        )


@dataclass(frozen=True)
class NumberReadResult:
    value: int | None
    confidence: float
    source: NumberReadSource
    glyphs: tuple[GlyphRead, ...] = ()
    best_score: float = 0.0
    second_score: float = 0.0
    margin: float = 0.0
    reason: NumberReadReason = NumberReadReason.NO_GLYPH
    raw_candidates: dict[str, object] = field(default_factory=dict)
    temporal_state: TemporalState = TemporalState.UNKNOWN
    timings_ms: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1, "value": self.value, "confidence": self.confidence,
            "source": self.source.value, "glyphs": [glyph.to_dict() for glyph in self.glyphs],
            "best_score": self.best_score, "second_score": self.second_score,
            "margin": self.margin, "reason": self.reason.value,
            "raw_candidates": dict(self.raw_candidates),
            "temporal_state": self.temporal_state.value,
            "timings_ms": dict(self.timings_ms),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "NumberReadResult":
        if int(raw.get("schema_version", 1)) != 1:
            raise ValueError("Version de NumberReadResult non prise en charge")
        glyphs = raw.get("glyphs", ())
        candidates, timings = raw.get("raw_candidates", {}), raw.get("timings_ms", {})
        if not isinstance(glyphs, (list, tuple)) or not isinstance(candidates, Mapping) \
                or not isinstance(timings, Mapping):
            raise ValueError("NumberReadResult invalide")
        return cls(
            int(raw["value"]) if raw.get("value") is not None else None,
            float(raw.get("confidence", 0.0)), NumberReadSource(str(raw.get("source", "UNKNOWN"))),
            tuple(GlyphRead.from_dict(item) for item in glyphs if isinstance(item, Mapping)),
            float(raw.get("best_score", 0.0)), float(raw.get("second_score", 0.0)),
            float(raw.get("margin", 0.0)), NumberReadReason(str(raw.get("reason", "NO_GLYPH"))),
            dict(candidates), TemporalState(str(raw.get("temporal_state", "UNKNOWN"))),
            {str(key): float(value) for key, value in timings.items()},
        )


@dataclass(frozen=True)
class SegmentedGlyph:
    bbox: tuple[int, int, int, int]
    image: np.ndarray = field(compare=False, repr=False)
    quality: float = 0.0


def _canonical(mask: np.ndarray) -> np.ndarray:
    """Centre un masque sans étirer son ratio dans un canevas 24×36."""
    points = cv2.findNonZero(mask)
    if points is None:
        return np.zeros((CANONICAL_SIZE[1], CANONICAL_SIZE[0]), np.uint8)
    x, y, width, height = cv2.boundingRect(points)
    crop = mask[y:y + height, x:x + width]
    target_w, target_h = CANONICAL_SIZE
    scale = min((target_w - 4) / max(1, width), (target_h - 4) / max(1, height))
    resized = cv2.resize(crop, (max(1, round(width * scale)), max(1, round(height * scale))),
                         interpolation=cv2.INTER_NEAREST)
    canvas = np.zeros((target_h, target_w), np.uint8)
    left, top = (target_w - resized.shape[1]) // 2, (target_h - resized.shape[0]) // 2
    canvas[top:top + resized.shape[0], left:left + resized.shape[1]] = resized
    return canvas


def _candidate_masks(image: np.ndarray) -> tuple[np.ndarray, ...]:
    if image.size == 0:
        return ()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(gray)
    if image.ndim == 3:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        white = np.where((hsv[:, :, 1] <= 105) & (hsv[:, :, 2] >= 145), 255, 0).astype(np.uint8)
    else:
        white = np.where(gray >= 160, 255, 0).astype(np.uint8)
    _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    _, bright = cv2.threshold(gray, 185, 255, cv2.THRESH_BINARY)
    return white, otsu, bright


def _segments_for_mask(mask: np.ndarray) -> tuple[SegmentedGlyph, ...]:
    height, width = mask.shape
    clean = mask.copy()
    border = max(1, round(min(width, height) * 0.04))
    clean[:border] = clean[-border:] = 0
    clean[:, :border] = clean[:, -border:] = 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats(clean, 8)
    components: list[tuple[int, int, int, int, int]] = []
    min_area = max(5, round(width * height * 0.008))
    for index in range(1, count):
        x, y, w, h, area = (int(value) for value in stats[index])
        if area < min_area or h < height * 0.30 or w > width * 0.65:
            continue
        if x <= border or y <= border or x + w >= width - border or y + h >= height - border:
            continue
        components.append((x, y, w, h, area))
    components.sort()
    if not 1 <= len(components) <= 2:
        return ()
    output = []
    for x, y, w, h, area in components:
        isolated = np.where(labels[y:y + h, x:x + w] > 0, 255, 0).astype(np.uint8)
        fill = min(1.0, area / max(1, w * h))
        height_score = min(1.0, h / max(1, height * 0.55))
        quality = 0.55 * height_score + 0.45 * min(1.0, fill / 0.22)
        output.append(SegmentedGlyph((x, y, w, h), _canonical(isolated), quality))
    return tuple(output)


def _clipped_by_side(mask: np.ndarray) -> bool:
    """Un trait blanc de hauteur chiffre qui touche le bord gauche/droit est un glyphe coupé.

    Sur le corpus réel, une ROI trop étroite coupait le « 1 » de « 15 » : le
    segmenteur écartait ce reste comme composante de bord et lisait « 5 ».
    """
    height, width = mask.shape
    clean = mask.copy()
    border = max(1, round(min(width, height) * 0.04))
    clean[:border] = clean[-border:] = 0
    clean[:, :border] = clean[:, -border:] = 0
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(clean, 8)
    min_area = max(5, round(width * height * 0.008))
    for index in range(1, count):
        x, _y, w, h, area = (int(value) for value in stats[index])
        if area >= min_area and h >= height * 0.30 and w <= width * 0.65 \
                and (x <= border or x + w >= width - border):
            return True
    return False


def glyph_clipped(image: np.ndarray) -> bool:
    """Vrai si le masque blanc HUD montre un chiffre tronqué par la ROI."""
    masks = _candidate_masks(image)
    return bool(masks) and _clipped_by_side(masks[0])


def segment_glyphs(image: np.ndarray) -> tuple[SegmentedGlyph, ...]:
    """Priorise le masque blanc HUD, puis utilise les seuils gris en repli.

    Le masque HSV est spécifique aux glyphes blancs. Un masque lumineux plus
    large peut absorber l'icône PA/PM et faire disparaître un chiffre pourtant
    correctement séparé par le masque blanc. Un chiffre coupé par la ROI ne
    produit aucun glyphe : lire seulement la partie visible serait une erreur.
    """
    masks = _candidate_masks(image)
    if masks and _clipped_by_side(masks[0]):
        return ()
    for mask in masks:
        segments = _segments_for_mask(mask)
        if segments:
            return segments
    return ()


def distinguish_one_seven(glyph: np.ndarray) -> tuple[int | None, dict[str, float | int | None]]:
    """Indice interprétable fondé sur la largeur du sommet et la hampe basse.

    Diagnostic uniquement : réfuté sur le corpus réel 3B-4R (préfère 7 pour chaque « 1 »).
    """
    mask = glyph > 0
    rows, columns = np.where(mask)
    if not len(columns):
        return None, {"preferred": None}
    x0, x1, y0, y1 = columns.min(), columns.max(), rows.min(), rows.max()
    width, height = max(1, x1 - x0 + 1), max(1, y1 - y0 + 1)
    top = mask[y0:y0 + max(2, round(height * 0.28)), x0:x1 + 1]
    top_columns = np.where(top.any(axis=0))[0]
    top_span = (top_columns.max() - top_columns.min() + 1) / width if len(top_columns) else 0.0
    lower = mask[y0 + round(height * 0.45):y1 + 1, x0:x1 + 1]
    lower_columns = np.where(lower)[1]
    lower_center = float(lower_columns.mean() / width) if len(lower_columns) else 0.5
    preferred = 7 if top_span >= 0.78 and lower_center >= 0.45 else (1 if top_span <= 0.62 else None)
    return preferred, {"top_span": float(top_span), "lower_center": lower_center, "preferred": preferred}


class GlyphTemplateLibrary:
    """Templates binaires, séparés par compteur avec repli sur ``shared``."""

    def __init__(self) -> None:
        self._templates: dict[str, dict[int, list[np.ndarray]]] = defaultdict(lambda: defaultdict(list))
        self._sources: dict[str, dict[int, list[str | None]]] = defaultdict(lambda: defaultdict(list))

    @property
    def empty(self) -> bool:
        return not any(values for groups in self._templates.values() for values in groups.values())

    def add(self, kind: str, digit: int, glyph: np.ndarray, *, source: str | None = None) -> None:
        if not 0 <= digit <= 9:
            raise ValueError("Un template doit représenter un chiffre 0..9")
        canonical = glyph if glyph.shape == (CANONICAL_SIZE[1], CANONICAL_SIZE[0]) else _canonical(glyph)
        self._templates[kind.upper()][digit].append(np.where(canonical > 0, 255, 0).astype(np.uint8))
        self._sources[kind.upper()][digit].append(source)

    def digits(self, kind: str) -> dict[int, list[np.ndarray]]:
        merged: dict[int, list[np.ndarray]] = defaultdict(list)
        for source in (self._templates.get("SHARED", {}), self._templates.get(kind.upper(), {})):
            for digit, values in source.items():
                merged[digit].extend(values)
        return dict(merged)

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        rows = []
        for kind, digits in sorted(self._templates.items()):
            for digit, templates in sorted(digits.items()):
                for index, template in enumerate(templates):
                    filename = f"{kind.lower()}-{digit}-{index:03d}.png"
                    if not cv2.imwrite(str(directory / filename), template):
                        raise OSError(f"Impossible d'écrire {filename}")
                    rows.append({"kind": kind, "digit": digit, "file": filename,
                                 "source": self._sources[kind][digit][index]})
        manifest = directory / "manifest.json"
        manifest.write_text(json.dumps({"schema_version": 1, "templates": rows}, indent=2), encoding="utf-8")
        return manifest

    @classmethod
    def load(cls, directory: Path) -> "GlyphTemplateLibrary":
        result = cls()
        manifest = directory / "manifest.json"
        if not manifest.is_file():
            return result
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        if int(raw.get("schema_version", 0)) != 1:
            raise ValueError("Version de templates HUD inconnue")
        for row in raw.get("templates", ()):
            image = cv2.imread(str(directory / row["file"]), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise ValueError(f"Template HUD absent : {row['file']}")
            result.add(str(row["kind"]), int(row["digit"]), image,
                       source=str(row["source"]) if row.get("source") else None)
        return result


def _similarity(first: np.ndarray, second: np.ndarray) -> float:
    a, b = first > 0, second > 0
    best = 0.0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            shifted = np.roll(np.roll(b, dy, axis=0), dx, axis=1)
            intersection = float(np.logical_and(a, shifted).sum())
            dice = 2 * intersection / max(1.0, float(a.sum() + shifted.sum()))
            agreement = 1.0 - float(np.logical_xor(a, shifted).mean())
            best = max(best, 0.72 * dice + 0.28 * agreement)
    return best


RapidReader = Callable[[np.ndarray, int, int], tuple[int | None, float]]


class HUDReader:
    def __init__(self, templates: GlyphTemplateLibrary | None = None, *,
                 rapidocr_reader: RapidReader | None = None, minimum_score: float = 0.70,
                 minimum_margin: float = 0.055, skip_rapidocr_confidence: float = 0.74,
                 fallback_confidence: float = 0.94, rapidocr_interval: float = 0.55) -> None:
        self.templates = templates or GlyphTemplateLibrary()
        self.rapidocr_reader = rapidocr_reader
        self.minimum_score = minimum_score
        self.minimum_margin = minimum_margin
        self.skip_rapidocr_confidence = skip_rapidocr_confidence
        self.fallback_confidence = fallback_confidence
        self.rapidocr_interval = max(0.0, rapidocr_interval)
        self._rapid_cache: dict[str, tuple[float, int, int | None, float]] = {}

    def _glyph_read(self, segment: SegmentedGlyph, kind: str) -> GlyphRead:
        scores = []
        for digit, templates in self.templates.digits(kind).items():
            scores.append((digit, max(_similarity(segment.image, template) for template in templates)))
        scores.sort(key=lambda item: item[1], reverse=True)
        best = scores[0] if scores else (None, 0.0)
        second = scores[1] if len(scores) > 1 else (None, 0.0)
        features = {}
        if {best[0], second[0]} == {1, 7}:
            _preferred, features = distinguish_one_seven(segment.image)
        return GlyphRead(segment.bbox, best[0], best[1], second[0], second[1],
                         best[1] - second[1], segment.quality, tuple(scores[:5]), features)

    def _specialized(self, image: np.ndarray, kind: str, minimum: int, maximum: int) -> NumberReadResult:
        started = time.perf_counter()
        if image is None or image.size == 0 or float(np.asarray(image).std()) < 1.0:
            return NumberReadResult(None, 0.0, NumberReadSource.UNKNOWN,
                                    reason=NumberReadReason.NO_GLYPH,
                                    timings_ms={"specialized": (time.perf_counter() - started) * 1000})
        if glyph_clipped(image):
            return NumberReadResult(None, 0.0, NumberReadSource.UNKNOWN,
                                    reason=NumberReadReason.CLIPPED_GLYPH,
                                    timings_ms={"specialized": (time.perf_counter() - started) * 1000})
        segments = segment_glyphs(image)
        if not segments:
            return NumberReadResult(None, 0.0, NumberReadSource.UNKNOWN,
                                    reason=NumberReadReason.SEGMENTATION_FAILED,
                                    timings_ms={"specialized": (time.perf_counter() - started) * 1000})
        glyphs = tuple(self._glyph_read(segment, kind) for segment in segments)
        if any(glyph.best_digit is None for glyph in glyphs):
            return NumberReadResult(None, 0.0, NumberReadSource.UNKNOWN, glyphs,
                                    reason=NumberReadReason.NO_TEMPLATE,
                                    timings_ms={"specialized": (time.perf_counter() - started) * 1000})
        best_score = min(glyph.best_score for glyph in glyphs)
        second_score = max(glyph.second_score for glyph in glyphs)
        margin = min(glyph.margin for glyph in glyphs)
        value = int("".join(str(glyph.best_digit) for glyph in glyphs))
        # 3B-4R : sur les 39 « 1 » réels annotés, distinguish_one_seven préfère 7 (la hampe
        # étroite donne une largeur de sommet de 1,0). Ses mesures restent exportées pour le
        # diagnostic, mais seule la marge des templates décide de l'ambiguïté 1/7.
        ambiguous_17 = any(
            {glyph.best_digit, glyph.second_digit} == {1, 7} and glyph.margin < self.minimum_margin
            for glyph in glyphs
        )
        if ambiguous_17:
            reason = NumberReadReason.AMBIGUOUS_1_7
        elif best_score < self.minimum_score:
            reason = NumberReadReason.LOW_MARGIN
        elif margin < self.minimum_margin:
            reason = NumberReadReason.LOW_MARGIN
        elif not minimum <= value <= maximum:
            reason = NumberReadReason.OUT_OF_RANGE
        else:
            reason = NumberReadReason.ACCEPTED
        quality = sum(glyph.segmentation_quality for glyph in glyphs) / len(glyphs)
        confidence = min(1.0, 0.45 * best_score + 0.30 * min(1.0, margin / 0.18) + 0.25 * quality)
        accepted = reason is NumberReadReason.ACCEPTED
        return NumberReadResult(
            value if accepted else None, confidence if accepted else min(confidence, 0.49),
            NumberReadSource.GLYPH_TEMPLATE if accepted else NumberReadSource.UNKNOWN,
            glyphs, best_score, second_score, margin, reason,
            {"specialized_value": value}, timings_ms={"specialized": (time.perf_counter() - started) * 1000},
        )

    def read(self, image: np.ndarray, kind: str, minimum: int = 0, maximum: int = 99) -> NumberReadResult:
        specialized = self._specialized(image, kind, minimum, maximum)
        if specialized.value is not None and specialized.confidence >= self.skip_rapidocr_confidence:
            return specialized
        # Un crop tronqué reste UNKNOWN : RapidOCR lirait lui aussi la seule partie visible.
        if specialized.reason in (NumberReadReason.OUT_OF_RANGE, NumberReadReason.CLIPPED_GLYPH) \
                or self.rapidocr_reader is None:
            return specialized
        started = time.perf_counter()
        signature = zlib.crc32(np.ascontiguousarray(image).tobytes())
        cached = self._rapid_cache.get(kind.upper())
        # Une ROI strictement identique réutilise toujours le résultat. Pour une ROI modifiée,
        # l'intervalle est compté après la fin de l'inférence ONNX, pas avant son coût.
        cache_hit = bool(cached and cached[1] == signature)
        if cache_hit:
            assert cached is not None
            ocr_value, ocr_confidence = cached[2], cached[3]
        elif cached and started - cached[0] < self.rapidocr_interval:
            ocr_value, ocr_confidence = None, 0.0
        else:
            try:
                ocr_value, ocr_confidence = self.rapidocr_reader(image, minimum, maximum)
            except Exception:
                ocr_value, ocr_confidence = None, 0.0
            self._rapid_cache[kind.upper()] = (time.perf_counter(), signature, ocr_value, ocr_confidence)
        timing = (time.perf_counter() - started) * 1000
        timings = {**specialized.timings_ms, "rapidocr": timing}
        raw = {**specialized.raw_candidates, "rapidocr_value": ocr_value,
               "rapidocr_confidence": ocr_confidence, "rapidocr_cached": cache_hit}
        if ocr_value is not None and not minimum <= ocr_value <= maximum:
            return replace(specialized, value=None, source=NumberReadSource.UNKNOWN,
                           reason=NumberReadReason.OUT_OF_RANGE, raw_candidates=raw, timings_ms=timings)
        if specialized.value is not None:
            if ocr_value is None:
                return replace(specialized, raw_candidates=raw, timings_ms=timings)
            if ocr_value != specialized.value:
                return replace(specialized, value=None, confidence=0.0, source=NumberReadSource.UNKNOWN,
                               reason=NumberReadReason.OCR_DISAGREEMENT, raw_candidates=raw,
                               timings_ms=timings)
            confidence = min(1.0, specialized.confidence + 0.08 * ocr_confidence)
            return replace(specialized, confidence=confidence, source=NumberReadSource.CONSENSUS,
                           raw_candidates=raw, timings_ms=timings)
        proposed = specialized.raw_candidates.get("specialized_value")
        if specialized.reason is NumberReadReason.AMBIGUOUS_1_7:
            return replace(specialized, raw_candidates=raw, timings_ms=timings)
        if proposed is not None and ocr_value == proposed and ocr_confidence >= 0.85 \
                and specialized.best_score >= self.minimum_score:
            return replace(specialized, value=int(proposed), confidence=min(0.87, 0.65 + 0.2 * ocr_confidence),
                           source=NumberReadSource.CONSENSUS, reason=NumberReadReason.ACCEPTED,
                           raw_candidates=raw, timings_ms=timings)
        if specialized.reason in (NumberReadReason.NO_GLYPH, NumberReadReason.SEGMENTATION_FAILED,
                                  NumberReadReason.NO_TEMPLATE) and ocr_value is not None \
                and ocr_confidence >= self.fallback_confidence:
            return NumberReadResult(ocr_value, 0.72 * ocr_confidence, NumberReadSource.RAPIDOCR,
                                    specialized.glyphs, reason=NumberReadReason.OCR_FALLBACK,
                                    raw_candidates=raw, timings_ms=timings)
        return replace(specialized, raw_candidates=raw, timings_ms=timings)


class NumberTemporalTracker:
    """Accepte vite une preuve forte, confirme deux fois une transition faible."""

    def __init__(self, high_confidence: float = 0.88, hold_frames: int = 1) -> None:
        self.high_confidence = high_confidence
        self.hold_frames = max(0, hold_frames)
        self._stable: NumberReadResult | None = None
        self._pending_value: int | None = None
        self._pending_count = 0
        self._misses = 0

    @staticmethod
    def _shows_change(result: NumberReadResult, stable: NumberReadResult) -> bool:
        """Une frame illisible mais segmentée qui contredit la valeur stable prouve un changement.

        Sur une vraie séquence, 11 → 7 illisible gardait 11 affiché : un nombre de glyphes
        différent ou une proposition spécialisée différente interdit ce maintien.
        """
        if not result.glyphs or stable.value is None:
            return False
        proposed = result.raw_candidates.get("specialized_value")
        return len(result.glyphs) != len(str(stable.value)) \
            or (proposed is not None and proposed != stable.value)

    def update(self, result: NumberReadResult) -> NumberReadResult:
        if result.value is None:
            self._misses += 1
            self._pending_value, self._pending_count = None, 0
            if self._stable is not None and self._misses <= self.hold_frames \
                    and not self._shows_change(result, self._stable):
                return replace(self._stable, confidence=self._stable.confidence * 0.72,
                               reason=NumberReadReason.TEMPORAL_HOLD,
                               temporal_state=TemporalState.STABLE,
                               raw_candidates={**result.raw_candidates, "held_value": self._stable.value})
            self._stable = None
            return replace(result, temporal_state=TemporalState.UNKNOWN)
        self._misses = 0
        if self._stable is None or result.value == self._stable.value or result.confidence >= self.high_confidence:
            self._stable = replace(result, temporal_state=TemporalState.STABLE)
            self._pending_value, self._pending_count = None, 0
            return self._stable
        if result.value == self._pending_value:
            self._pending_count += 1
        else:
            self._pending_value, self._pending_count = result.value, 1
        if self._pending_count >= 2:
            self._stable = replace(result, temporal_state=TemporalState.STABLE)
            self._pending_value, self._pending_count = None, 0
            return self._stable
        return replace(result, value=None, confidence=min(result.confidence, 0.49),
                       reason=NumberReadReason.TEMPORAL_CONFLICT,
                       temporal_state=TemporalState.CHANGING)
