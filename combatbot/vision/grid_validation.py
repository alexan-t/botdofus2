"""Validation d'alignement, dérive, visibilité de grille et état de combat (LOT 3B-3).

Principes :

* GameData reste l'autorité : ce module ne crée, ne supprime ni ne renumérote aucune
  cellule. Il compare des **candidats visuels** aux centres **déjà projetés**.
* La visibilité de la grille (« DOFUS dessine-t-il la grille de combat ? ») est
  distincte de l'existence de la topologie (toujours 560 cellules).
* Toutes les limites sont exprimées en fraction de la hauteur de cellule projetée
  (``cell_h``) et regroupées dans des configurations testables. Les valeurs par
  défaut viennent d'une seule session réelle (7 maps, un layout) : elles sont
  prudentes, documentées, et laissent UNKNOWN quand la preuve manque.

Rejet des aberrants (documenté) : un candidat est **compatible** s'il tombe dans un
losange projeté, à moins de ``compatible_radius`` du centre, avec une taille proche de
la cellule. Parmi les compatibles, on garde les résidus ``r <= médiane + k·1,4826·MAD``
(plancher ``min_outlier_cut``). Les statistiques utilisent la médiane ; le maximum
reste diagnostique.

Micro-ajustement : translation = médiane par axe des vecteurs résiduels des inliers,
échelle uniforme = moindres carrés bornés des résidus restants autour du centre des
inliers. Aucune recherche de ±1 cellule, de permutation d'axes, de miroir ni de
nouvelle origine : ces hypothèses relèvent de la calibration initiale humaine.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
import math
from typing import Any, Iterable, Sequence

import numpy as np

from combatbot.vision.coordinates import CombatPoint
from combatbot.vision.grid_projection import GridScreenTransform, ProjectedGrid, Vector2, lattice_pixel_to_cell_id

REGION_ROWS = REGION_COLS = 3


# --- Configurations ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MatchConfig:
    compatible_radius: float = 0.3      # × cell_h ; real median residuals ≤ 4.5 px of 59.6 (0.076), room for drift
    size_min: float = 0.55              # candidate w/h relative to projected cell (inner contours ≈ 0.82)
    size_max: float = 1.35
    mad_k: float = 3.0
    min_outlier_cut: float = 0.03       # × cell_h : never cut below this radius


@dataclass(frozen=True)
class VisibilityConfig:
    min_inliers: int = 10
    min_inlier_fraction: float = 0.08   # of statically traversable cells whose centre is in the crop
    min_regions: int = 3
    min_coverage: float = 0.5           # supported / expected regions
    max_median_residual: float = 0.1    # × cell_h, after removing the global translation (a drifted grid is still visible)
    min_consistency: float = 0.1        # only rejects very weak cases; real exploration ≤ 0.13, combat ≥ 0.60
    not_visible_max_inliers: int = 3
    not_visible_max_fraction: float = 0.03
    min_region_inliers: int = 2
    min_expected_region_cells: int = 3


@dataclass(frozen=True)
class AlignmentConfig:
    aligned_translation: float = 0.05   # × cell_h : within this, no correction is needed
    local_window: float = 0.15          # × cell_h : largest correction ever proposed (≪ one cell)
    max_scale: float = 0.01             # |uniform scale| proposed at most
    min_scale: float = 0.002            # below this, scale is reported as 0
    max_median_residual: float = 0.15   # × cell_h : beyond this after correction → MISALIGNED
    region_agreement: float = 0.05      # × cell_h : region translations must agree with the global one
    min_agreeing_regions: int = 3


@dataclass(frozen=True)
class DriftConfig:
    window: int = 6
    min_agreeing_frames: int = 3
    agreement: float = 0.03             # × cell_h between successive corrections
    reset_after_insufficient: int = 4


@dataclass(frozen=True)
class MapConsistencyConfig:
    baseline_size: int = 5
    min_baseline_samples: int = 1       # 2 let a stale first frame enter the baseline (real corpus, 1-frame maps)
    consistent_drop: float = 0.08       # delta below this keeps the map CONSISTENT
    suspect_drop: float = 0.12          # same-frame drops 0.19–0.39 ; but a correct map varied by up to 0.14
    stale_drop: float = 0.15
    frames_for_stale_guess: int = 2
    frames_for_stale_verified: int = 3  # user_verified_mapid needs a longer consensus
    absolute_suspect_without_baseline: float = 0.35


# --- Matching -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CandidateMatch:
    cell_id: int
    candidate: tuple[float, float]
    center: tuple[float, float]
    residual: tuple[float, float]
    norm: float
    region: int | None
    traversable: bool | None


def _cell_h(transform: GridScreenTransform) -> float:
    return max(1.0, transform.cell_height)


def _regions(grid: ProjectedGrid, image_size: tuple[int, int]):
    """3×3 regions over the bounding box of in-crop (traversable) cell centres."""
    width, height = image_size
    inside = [c for c in grid.cells if 0 <= c.center.x < width and 0 <= c.center.y < height]
    known = [c for c in inside if c.static_traversable_in_fight is not None]
    expected = [c for c in inside if c.static_traversable_in_fight] if known else inside
    if not expected:
        return None, {}, []
    xs = np.array([c.center.x for c in expected]); ys = np.array([c.center.y for c in expected])
    box = (float(xs.min()), float(ys.min()), float(xs.max()) + 1e-6, float(ys.max()) + 1e-6)

    def region_of(x: float, y: float) -> int | None:
        if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
            return None
        col = min(REGION_COLS - 1, int((x - box[0]) / (box[2] - box[0] + 1e-9) * REGION_COLS))
        row = min(REGION_ROWS - 1, int((y - box[1]) / (box[3] - box[1] + 1e-9) * REGION_ROWS))
        return row * REGION_COLS + col

    counts: dict[int, int] = {}
    for c in expected:
        region = region_of(c.center.x, c.center.y)
        if region is not None:
            counts[region] = counts.get(region, 0) + 1
    return region_of, counts, expected


def match_candidates(candidates: Iterable[Sequence[float]], grid: ProjectedGrid, image_size: tuple[int, int],
                     config: MatchConfig = MatchConfig()) -> tuple[list[CandidateMatch], dict[str, Any]]:
    """Compatible candidates only; the grid itself is never modified."""
    transform = grid.transform
    cell_h, cell_w = _cell_h(transform), max(1.0, transform.cell_width)
    region_of, region_cells, expected = _regions(grid, image_size)
    valid = grid.cell_ids
    raw, total = [], 0
    for candidate in candidates:
        total += 1
        cx, cy = float(candidate[0]), float(candidate[1])
        if len(candidate) >= 4:
            w_ratio, h_ratio = float(candidate[2]) / cell_w, float(candidate[3]) / cell_h
            if not (config.size_min <= w_ratio <= config.size_max and config.size_min <= h_ratio <= config.size_max):
                continue
        cell_id = lattice_pixel_to_cell_id(transform, (cx, cy), valid)
        if cell_id is not None:
            raw.append((cx, cy, grid.cell(cell_id)))
    # Two passes: a uniformly drifted grid is still a drawn grid. The global translation
    # (median residual vector, robust to decor) is removed before the compatibility radius.
    if raw:
        vectors = np.array([(cx - c.center.x, cy - c.center.y) for cx, cy, c in raw])
        drift = np.median(vectors, axis=0)
    else:
        drift = np.zeros(2)
    matches = []
    for cx, cy, cell in raw:
        dx, dy = cx - cell.center.x, cy - cell.center.y
        if math.hypot(dx - drift[0], dy - drift[1]) > config.compatible_radius * cell_h:
            continue
        matches.append(CandidateMatch(int(cell.cell_id), (cx, cy), (cell.center.x, cell.center.y), (dx, dy),
                                      math.hypot(dx, dy),
                                      region_of(cell.center.x, cell.center.y) if region_of else None,
                                      cell.static_traversable_in_fight))
    return matches, {"candidate_count": total, "expected_cells": len(expected), "region_cells": region_cells}


def reject_outliers(matches: list[CandidateMatch], cell_h: float,
                    config: MatchConfig = MatchConfig()) -> tuple[list[CandidateMatch], list[CandidateMatch], float | None]:
    """Median + MAD on residual norms (documented in the module docstring)."""
    if not matches:
        return [], [], None
    vectors = np.array([m.residual for m in matches])
    norms = np.linalg.norm(vectors - np.median(vectors, axis=0), axis=1)   # detrended
    median = float(np.median(norms))
    mad = float(np.median(np.abs(norms - median)))
    cut = max(median + config.mad_k * 1.4826 * mad, config.min_outlier_cut * cell_h)
    inliers = [m for m, n in zip(matches, norms) if n <= cut]
    outliers = [m for m, n in zip(matches, norms) if n > cut]
    return inliers, outliers, cut


def _supported_regions(inliers: list[CandidateMatch], region_cells: dict[int, int],
                       config: VisibilityConfig) -> tuple[list[int], list[int]]:
    expected = sorted(r for r, n in region_cells.items() if n >= config.min_expected_region_cells)
    counts: dict[int, int] = {}
    for m in inliers:
        if m.region is not None:
            counts[m.region] = counts.get(m.region, 0) + 1
    supported = sorted(r for r in expected if counts.get(r, 0) >= config.min_region_inliers)
    return expected, supported


def _stats(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "p95": None, "max": None}
    array = np.asarray(values, float)
    return {"median": float(np.median(array)), "p95": float(np.percentile(array, 95)), "max": float(array.max())}


# --- Grid visibility ---------------------------------------------------------------------------------

class GridVisibilityState(str, Enum):
    VISIBLE = "VISIBLE"
    NOT_VISIBLE = "NOT_VISIBLE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class GridVisibilityObservation:
    """Is DOFUS drawing its combat grid right now? (Not: does the topology exist.)"""
    state: GridVisibilityState
    confidence: float
    candidate_count: int
    compatible_count: int
    inlier_count: int
    expected_cells: int
    coverage: float | None
    supported_regions: tuple[int, ...]
    expected_regions: tuple[int, ...]
    topology_consistency: float | None
    residual_median: float | None
    evidence: dict[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value


class GridVisibilityDetector:
    def __init__(self, match: MatchConfig = MatchConfig(), config: VisibilityConfig = VisibilityConfig()) -> None:
        self.match_config, self.config = match, config

    def observe(self, grid: ProjectedGrid, candidates, image_size: tuple[int, int],
                consistency: float | None = None
                ) -> tuple[GridVisibilityObservation, list[CandidateMatch], list[CandidateMatch]]:
        """``consistency`` = topology_consistency already measured on the same projection."""
        cfg = self.config
        cell_h = _cell_h(grid.transform)
        matches, info = match_candidates(candidates, grid, image_size, self.match_config)
        # Only candidates on cells DOFUS would outline count as grid evidence when walkability is known.
        on_grid = [m for m in matches if m.traversable is not False]
        inliers, outliers, cut = reject_outliers(on_grid, cell_h, self.match_config)
        expected, supported = _supported_regions(inliers, info["region_cells"], cfg)
        expected_cells = info["expected_cells"]
        coverage = len(supported) / len(expected) if expected else None
        # Visibility asks "is a grid drawn?", not "is it aligned?": detrend the global translation.
        if inliers:
            vectors = np.array([m.residual for m in inliers])
            residual = float(np.median(np.linalg.norm(vectors - np.median(vectors, axis=0), axis=1)))
        else:
            residual = None
        fraction = len(inliers) / expected_cells if expected_cells else 0.0
        reasons = [f"inliers={len(inliers)}/{expected_cells} cellules attendues ({fraction:.2f})",
                   f"régions={len(supported)}/{len(expected)}",
                   f"residu_median_detrend={'n/a' if residual is None else f'{residual:.2f}px'}",
                   f"topology_consistency={'n/a' if consistency is None else f'{consistency:.3f}'}"]
        strong = (len(inliers) >= max(cfg.min_inliers, cfg.min_inlier_fraction * expected_cells)
                  and len(supported) >= cfg.min_regions and (coverage or 0) >= cfg.min_coverage
                  and residual is not None and residual <= cfg.max_median_residual * cell_h
                  and (consistency is None or consistency >= cfg.min_consistency))
        absent = (len(inliers) <= max(cfg.not_visible_max_inliers, cfg.not_visible_max_fraction * expected_cells)
                  or ((coverage or 0) <= 0.25 and consistency is not None and consistency < cfg.min_consistency))
        if strong:
            state = GridVisibilityState.VISIBLE
        elif absent:
            state = GridVisibilityState.NOT_VISIBLE
        else:
            state = GridVisibilityState.UNKNOWN
            reasons.append("preuves contradictoires ou insuffisantes")
        confidence = float(min(1.0, fraction / 0.3) * (coverage or 0.0)) if state is not GridVisibilityState.NOT_VISIBLE \
            else float(1.0 - min(1.0, fraction / max(cfg.min_inlier_fraction, 1e-6)))
        observation = GridVisibilityObservation(
            state, round(confidence, 4), info["candidate_count"], len(matches), len(inliers), expected_cells,
            coverage, tuple(supported), tuple(expected), consistency, residual,
            {"outlier_cut_px": cut, "outliers": len(outliers), "off_grid_compatible": len(matches) - len(on_grid)},
            tuple(reasons))
        return observation, inliers, outliers


# --- Alignment -------------------------------------------------------------------------------------

class AlignmentStatus(str, Enum):
    ALIGNED = "ALIGNED"
    DEGRADED = "DEGRADED"
    MISALIGNED = "MISALIGNED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class RuntimeAdjustment:
    """Bounded runtime correction around a confirmed transform; never persisted."""
    dx: float = 0.0
    dy: float = 0.0
    scale: float = 0.0          # relative uniform scale around (cx, cy)
    cx: float = 0.0
    cy: float = 0.0

    @property
    def is_zero(self) -> bool:
        return self.dx == 0 and self.dy == 0 and self.scale == 0

    def magnitude(self) -> float:
        return math.hypot(self.dx, self.dy)

    def apply(self, base: GridScreenTransform) -> GridScreenTransform:
        if self.is_zero:
            return base
        k = 1.0 + self.scale
        origin = CombatPoint(self.cx + (base.origin.x - self.cx) * k + self.dx,
                             self.cy + (base.origin.y - self.cy) * k + self.dy)
        return GridScreenTransform(origin, Vector2(base.basis_x.x * k, base.basis_x.y * k),
                                   Vector2(base.basis_y.x * k, base.basis_y.y * k), base.reference_combat_size)

    def combined(self, other: "RuntimeAdjustment") -> "RuntimeAdjustment":
        return RuntimeAdjustment(self.dx + other.dx, self.dy + other.dy, self.scale + other.scale,
                                 other.cx if not other.is_zero else self.cx,
                                 other.cy if not other.is_zero else self.cy)

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class GridAlignmentObservation:
    status: AlignmentStatus
    confidence: float
    coverage: float | None
    candidate_count: int
    inlier_count: int
    residual: dict[str, float | None]
    topology_consistency: float | None
    suggested_correction: RuntimeAdjustment | None
    supported_regions: tuple[int, ...]
    reasons: tuple[str, ...]
    outlier_count: int = 0
    inlier_points: tuple[tuple[float, float], ...] = field(default=(), repr=False)
    outlier_points: tuple[tuple[float, float], ...] = field(default=(), repr=False)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value.pop("inlier_points"), value.pop("outlier_points")
        return value


class GridAlignmentValidator:
    """Validates a confirmed projection against local visual evidence; micro-corrections only."""

    def __init__(self, config: AlignmentConfig = AlignmentConfig(), match: MatchConfig = MatchConfig(),
                 visibility: VisibilityConfig = VisibilityConfig()) -> None:
        self.config, self.match_config, self.visibility_config = config, match, visibility

    def validate(self, grid: ProjectedGrid, visibility: GridVisibilityObservation,
                 inliers: list[CandidateMatch], outliers: Sequence[CandidateMatch] = ()) -> GridAlignmentObservation:
        cfg = self.config
        cell_h = _cell_h(grid.transform)
        # Median on inliers (decision); p95/max over every compatible match (diagnostic only).
        everything = [m.norm for m in list(inliers) + list(outliers)]
        residual = {"median": _stats([m.norm for m in inliers])["median"],
                    "p95": _stats(everything)["p95"], "max": _stats(everything)["max"]}
        common = dict(coverage=visibility.coverage, candidate_count=visibility.candidate_count,
                      inlier_count=len(inliers), residual=residual,
                      topology_consistency=visibility.topology_consistency,
                      supported_regions=visibility.supported_regions, outlier_count=len(outliers),
                      inlier_points=tuple(m.candidate for m in inliers),
                      outlier_points=tuple(m.candidate for m in outliers))
        if visibility.state is not GridVisibilityState.VISIBLE:
            return GridAlignmentObservation(AlignmentStatus.INSUFFICIENT_EVIDENCE, 0.0, suggested_correction=None,
                                            reasons=(f"grille {visibility.state.value}",), **common)
        vectors = np.array([m.residual for m in inliers])
        tx, ty = float(np.median(vectors[:, 0])), float(np.median(vectors[:, 1]))
        # Spatial consensus: each supported region's own median translation must agree.
        agreeing, region_reasons = 0, []
        for region in visibility.supported_regions:
            own = np.array([m.residual for m in inliers if m.region == region])
            if len(own) == 0:
                continue
            rx, ry = float(np.median(own[:, 0])), float(np.median(own[:, 1]))
            if math.hypot(rx - tx, ry - ty) <= cfg.region_agreement * cell_h:
                agreeing += 1
        points = np.array([m.center for m in inliers])
        centre = points.mean(axis=0)
        spread = points - centre
        remaining = vectors - np.array([tx, ty])
        denominator = float((spread ** 2).sum())
        scale = float((spread * remaining).sum() / denominator) if denominator > 0 else 0.0
        scale = max(-cfg.max_scale, min(cfg.max_scale, scale)) if abs(scale) >= cfg.min_scale else 0.0
        translation = math.hypot(tx, ty)
        after = np.linalg.norm(remaining - scale * spread, axis=1)
        reasons = [f"translation=({tx:.2f},{ty:.2f})px ({translation / cell_h:.3f}×cell_h)",
                   f"scale={scale:.4f}", f"regions_en_accord={agreeing}/{len(visibility.supported_regions)}",
                   f"residu_median={residual['median']:.2f}px", f"residu_max={residual['max']:.2f}px (diagnostic)"]
        correction = RuntimeAdjustment(tx, ty, scale, float(centre[0]), float(centre[1]))
        coverage = visibility.coverage or 0.0
        confidence = round(float(coverage * min(1.0, len(inliers) / 40)), 4)
        if agreeing < cfg.min_agreeing_regions:
            return GridAlignmentObservation(AlignmentStatus.INSUFFICIENT_EVIDENCE, confidence * 0.5,
                                            suggested_correction=None,
                                            reasons=tuple(reasons + ["consensus spatial insuffisant"]), **common)
        if translation > cfg.local_window * cell_h or float(np.median(after)) > cfg.max_median_residual * cell_h:
            return GridAlignmentObservation(AlignmentStatus.MISALIGNED, confidence, suggested_correction=None,
                                            reasons=tuple(reasons + ["correction hors fenêtre locale : RECALIBRATION_REQUIRED"]),
                                            **common)
        if translation <= cfg.aligned_translation * cell_h and scale == 0.0:
            return GridAlignmentObservation(AlignmentStatus.ALIGNED, confidence, suggested_correction=None,
                                            reasons=tuple(reasons), **common)
        return GridAlignmentObservation(AlignmentStatus.DEGRADED, confidence, suggested_correction=correction,
                                        reasons=tuple(reasons + ["micro-correction proposée (non appliquée)"]), **common)


# --- Temporal drift consensus -------------------------------------------------------------------------

class DriftState(str, Enum):
    ALIGNED = "ALIGNED"
    DRIFT_SUSPECTED = "DRIFT_SUSPECTED"
    DRIFT_CONFIRMED = "DRIFT_CONFIRMED"
    RECOVERED = "RECOVERED"
    RECALIBRATION_REQUIRED = "RECALIBRATION_REQUIRED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class DriftTracker:
    """base_transform (immutable, persisted) + runtime_adjustment (in memory, bounded)."""

    def __init__(self, base_transform: GridScreenTransform, config: DriftConfig = DriftConfig(),
                 alignment: AlignmentConfig = AlignmentConfig()) -> None:
        self.base_transform = base_transform
        self.config, self.alignment = config, alignment
        self.adjustment = RuntimeAdjustment()
        self.state = DriftState.INSUFFICIENT_EVIDENCE
        self._corrections: deque[RuntimeAdjustment] = deque(maxlen=config.window)
        self._misaligned = 0
        self._insufficient = 0

    @property
    def effective_transform(self) -> GridScreenTransform:
        return self.adjustment.apply(self.base_transform)

    def rebase(self, base_transform: GridScreenTransform) -> None:
        if base_transform != self.base_transform:
            self.base_transform = base_transform
            self.reset()

    def reset(self) -> None:
        self.adjustment = RuntimeAdjustment()
        self._corrections.clear()
        self._misaligned = self._insufficient = 0
        self.state = DriftState.INSUFFICIENT_EVIDENCE

    def update(self, observation: GridAlignmentObservation) -> DriftState:
        cell_h = _cell_h(self.base_transform)
        status = observation.status
        if status is AlignmentStatus.INSUFFICIENT_EVIDENCE:
            self._insufficient += 1
            self._corrections.clear()
            if self._insufficient >= self.config.reset_after_insufficient and not self.adjustment.is_zero:
                self.adjustment = RuntimeAdjustment()
                self.state = DriftState.RECOVERED
            elif self.state not in (DriftState.RECOVERED, DriftState.RECALIBRATION_REQUIRED):
                self.state = DriftState.INSUFFICIENT_EVIDENCE
            return self.state
        self._insufficient = 0
        if status is AlignmentStatus.MISALIGNED:
            self._misaligned += 1
            self._corrections.clear()
            if self._misaligned >= self.config.min_agreeing_frames:
                self.adjustment = RuntimeAdjustment()  # never apply an out-of-window correction
                self.state = DriftState.RECALIBRATION_REQUIRED
            else:
                self.state = DriftState.DRIFT_SUSPECTED
            return self.state
        self._misaligned = 0
        if status is AlignmentStatus.ALIGNED:
            self._corrections.clear()
            # Aligned again after a compensated drift or a recalibration alert: recovered.
            self.state = (DriftState.RECOVERED if self.state in (DriftState.DRIFT_CONFIRMED,
                                                                 DriftState.RECALIBRATION_REQUIRED)
                          else DriftState.ALIGNED)
            return self.state
        # DEGRADED: accumulate; apply only with temporal consensus.
        correction = observation.suggested_correction
        if correction is None:
            self.state = DriftState.DRIFT_SUSPECTED
            return self.state
        self._corrections.append(correction)
        recent = list(self._corrections)[-self.config.min_agreeing_frames:]
        if len(recent) >= self.config.min_agreeing_frames:
            xs = np.array([c.dx for c in recent]); ys = np.array([c.dy for c in recent])
            ss = np.array([c.scale for c in recent])
            mx, my, ms = float(np.median(xs)), float(np.median(ys)), float(np.median(ss))
            spread = max(math.hypot(x - mx, y - my) for x, y in zip(xs, ys))
            if spread <= self.config.agreement * cell_h:
                step = RuntimeAdjustment(mx, my, ms, recent[-1].cx, recent[-1].cy)
                total = self.adjustment.combined(step)
                if total.magnitude() > self.alignment.local_window * cell_h or abs(total.scale) > self.alignment.max_scale:
                    self.adjustment = RuntimeAdjustment()
                    self.state = DriftState.RECALIBRATION_REQUIRED
                else:
                    self.adjustment = total
                    self.state = DriftState.DRIFT_CONFIRMED
                self._corrections.clear()
                return self.state
        self.state = DriftState.DRIFT_SUSPECTED
        return self.state

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state.value, "runtime_adjustment": self.adjustment.to_dict(),
                "base_transform": self.base_transform.to_dict(),
                "effective_transform": self.effective_transform.to_dict()}


# --- Relative map consistency --------------------------------------------------------------------------

class MapDeclarationState(str, Enum):
    CONSISTENT = "CONSISTENT"
    SUSPECT = "SUSPECT"
    STALE_LIKELY = "STALE_LIKELY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MapConsistencyObservation:
    state: MapDeclarationState
    map_id: int | None
    map_id_source: str | None
    absolute_consistency: float | None
    baseline_consistency: float | None
    delta_from_baseline: float | None
    low_streak: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        return value

    @property
    def message(self) -> str | None:
        if self.state in (MapDeclarationState.SUSPECT, MapDeclarationState.STALE_LIKELY):
            return "Le map ID déclaré semble ne plus correspondre à la grille observée."
        return None


class MapConsistencyTracker:
    """Warning only: never detects a map, never overrides a user-verified map ID on one frame."""

    def __init__(self, config: MapConsistencyConfig = MapConsistencyConfig()) -> None:
        self.config = config
        self._baselines: dict[int, deque[float]] = {}
        self._low_streak = 0
        self._map: int | None = None

    def update(self, map_id: int | None, map_id_source: str | None, visibility: GridVisibilityState,
               consistency: float | None) -> MapConsistencyObservation:
        cfg = self.config
        if map_id != self._map:
            self._map, self._low_streak = map_id, 0
        baseline_values = self._baselines.get(map_id) if map_id is not None else None
        baseline = float(np.median(baseline_values)) if baseline_values and len(baseline_values) >= cfg.min_baseline_samples else None

        def result(state, delta=None, reasons=()):
            return MapConsistencyObservation(state, map_id, map_id_source, consistency, baseline, delta,
                                             self._low_streak, tuple(reasons))

        if map_id is None:
            return result(MapDeclarationState.UNKNOWN, reasons=("aucun map ID déclaré",))
        if visibility is not GridVisibilityState.VISIBLE or consistency is None:
            # Out of combat DOFUS draws no grid: consistency is not informative there.
            return result(MapDeclarationState.UNKNOWN, reasons=(f"grille {visibility.value} : cohérence non évaluable",))
        if baseline is None:
            self._record(map_id, consistency)
            if consistency < cfg.absolute_suspect_without_baseline:
                return result(MapDeclarationState.SUSPECT,
                              reasons=(f"pas encore de baseline ; cohérence absolue très basse ({consistency:.3f})",))
            return result(MapDeclarationState.CONSISTENT, reasons=("baseline en construction",))
        delta = baseline - consistency
        if delta < cfg.consistent_drop:
            self._low_streak = 0
            self._record(map_id, consistency)
            return result(MapDeclarationState.CONSISTENT, delta, (f"delta={delta:.3f} < {cfg.consistent_drop}",))
        if delta < cfg.suspect_drop:
            return result(MapDeclarationState.CONSISTENT, delta, (f"baisse modérée delta={delta:.3f} (baseline gelée)",))
        self._low_streak += 1
        needed = cfg.frames_for_stale_verified if map_id_source == "user_verified_mapid" else cfg.frames_for_stale_guess
        reasons = [f"delta={delta:.3f} ≥ {cfg.suspect_drop}", f"frames basses consécutives={self._low_streak}/{needed}"]
        if delta >= cfg.stale_drop and self._low_streak >= needed:
            return result(MapDeclarationState.STALE_LIKELY, delta, reasons)
        return result(MapDeclarationState.SUSPECT, delta, reasons)

    def _record(self, map_id: int, value: float) -> None:
        values = self._baselines.setdefault(map_id, deque(maxlen=self.config.baseline_size))
        values.append(value)


# --- Combat state -------------------------------------------------------------------------------------

class CombatState(str, Enum):
    COMBAT = "COMBAT"
    EXPLORATION = "EXPLORATION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CombatStateObservation:
    state: CombatState
    confidence: float
    reasons: tuple[str, ...]

    @property
    def combat_detected(self) -> bool:
        """Historical boolean: True only on positive evidence, never from a weak score."""
        return self.state is CombatState.COMBAT

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state.value, "confidence": self.confidence, "reasons": list(self.reasons)}


class CombatStateDetector:
    """GameData topology exists ≠ combat grid visible ≠ combat active.

    With a projected GameData grid, the 560 cells prove nothing: the decision rests on
    grid visibility, HUD signals only adjust confidence (they are also active in
    exploration on the real client). Legacy sources use the visual grid confidence,
    never the number of cells.
    """

    def __init__(self, hud_weight: float = 0.3, legacy_min_grid: float = 0.4,
                 legacy_max_absent: float = 0.2, legacy_min_combined: float = 0.45) -> None:
        self.hud_weight = hud_weight
        self.legacy_min_grid, self.legacy_max_absent = legacy_min_grid, legacy_max_absent
        self.legacy_min_combined = legacy_min_combined

    def detect(self, *, grid_source: str, visibility: GridVisibilityObservation | None,
               grid_confidence: float, hud: dict[str, float]) -> CombatStateObservation:
        hud_score = float(np.mean([hud.get(k, 0.0) for k in ("counters", "end_turn", "spell_bar")]))
        if grid_source == "GAMEDATA_PROJECTED":
            if visibility is None:
                return CombatStateObservation(CombatState.UNKNOWN, 0.0, ("visibilité de grille non mesurée",))
            reasons = (f"grille {visibility.state.value} ({visibility.confidence:.2f})", f"hud={hud_score:.2f}",
                       "560 cellules GameData ignorées comme preuve")
            if visibility.state is GridVisibilityState.VISIBLE:
                confidence = (1 - self.hud_weight) * max(0.5, visibility.confidence) + self.hud_weight * hud_score
                return CombatStateObservation(CombatState.COMBAT, round(confidence, 4), reasons)
            if visibility.state is GridVisibilityState.NOT_VISIBLE:
                return CombatStateObservation(CombatState.EXPLORATION, round(visibility.confidence, 4), reasons)
            return CombatStateObservation(CombatState.UNKNOWN, round(visibility.confidence, 4), reasons)
        if grid_source == "NONE":
            return CombatStateObservation(CombatState.UNKNOWN, 0.0, ("aucune grille exploitable",))
        combined = 0.55 * grid_confidence + 0.45 * hud_score
        reasons = (f"grille {grid_source} confiance={grid_confidence:.2f}", f"hud={hud_score:.2f}",
                   f"combiné={combined:.2f}")
        if grid_confidence >= self.legacy_min_grid and combined >= self.legacy_min_combined:
            return CombatStateObservation(CombatState.COMBAT, round(combined, 4), reasons)
        if grid_confidence < self.legacy_max_absent:
            return CombatStateObservation(CombatState.EXPLORATION, round(1 - combined, 4), reasons)
        return CombatStateObservation(CombatState.UNKNOWN, round(combined, 4), reasons)


__all__ = [name for name in dir() if not name.startswith("_")]
