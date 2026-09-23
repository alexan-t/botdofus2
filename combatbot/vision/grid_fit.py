"""Calibration de GridScreenTransform à partir de candidats visuels ou d'ancres.

La vision ne propose que des **centres candidats**. Le réseau des 560 cellules,
leurs identités et leur voisinage viennent de GameData. Le fit est global :

1. pas et orientation dominants estimés sur tous les candidats (médianes) ;
2. phase du réseau par moyenne circulaire, puis moindres carrés affines
   itératifs avec rejet des aberrants ;
3. le réseau étant périodique et symétrique, les candidats seuls ne fixent ni
   la cellule 0 ni l'orientation : chaque hypothèse (orientation × décalage
   entier) est notée par la couverture de la zone et, si la topologie de la map
   est fournie, par l'accord entre candidats et cellules traversables en combat
   (DOFUS ne dessine la grille de combat que sur ces cellules) ;
4. une solution faible, ambiguë ou d'orientation non normale n'est jamais
   acceptée automatiquement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Iterable, Sequence

import cv2
import numpy as np

from combatbot.gamedata.models import GridTopology
from combatbot.gamedata.topology import CELL_COUNT, MAP_WIDTH, ROWS, cell_to_grid
from combatbot.vision.coordinates import CombatPoint
from combatbot.vision.grid_projection import GridOrientation, GridScreenTransform, ProjectedGrid, Vector2

MIN_CANDIDATES = 6
MIN_SCORE_WITH_TOPOLOGY = 0.75
MIN_SCORE_WITHOUT_TOPOLOGY = 0.92
MIN_MARGIN = 0.08
MAX_MEDIAN_RESIDUAL_RATIO = 0.12  # of cell width
MAX_ANISOTROPY = 0.2              # |bx| vs |by| relative difference
ALIGNMENT_SAMPLES_PER_EDGE = 6
# Mesuré sur 2 captures réelles (même layout) : 0,78 avec la bonne map, 0,51 après
# changement de salle. Seuil indicatif, à recalibrer sur le corpus ; jamais bloquant.
MIN_TOPOLOGY_CONSISTENCY = 0.65

_GRID_X = np.array([cell_to_grid(c).x for c in range(CELL_COUNT)], dtype=float)
_GRID_Y = np.array([cell_to_grid(c).y for c in range(CELL_COUNT)], dtype=float)


class FitStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    WEAK = "WEAK"
    AMBIGUOUS = "AMBIGUOUS"
    ORIENTATION_CONFLICT = "ORIENTATION_CONFLICT"
    INSUFFICIENT_CANDIDATES = "INSUFFICIENT_CANDIDATES"
    INVALID_ANCHORS = "INVALID_ANCHORS"


@dataclass(frozen=True)
class DiamondCandidate:
    x: float
    y: float
    width: float
    height: float
    score: float = 1.0

    @classmethod
    def from_tuple(cls, value: Sequence[float]) -> "DiamondCandidate":
        return cls(*(float(item) for item in value[:5]))


@dataclass(frozen=True)
class GridFitResult:
    status: FitStatus
    method: str
    transform: GridScreenTransform | None = None
    score: float = 0.0
    margin: float = 0.0
    orientation: str | None = None
    candidates: int = 0
    inliers: int = 0
    residual_median_px: float | None = None
    residual_max_px: float | None = None
    hypotheses: tuple[dict, ...] = ()
    message: str = ""
    inlier_points: tuple[tuple[float, float], ...] = field(default=(), repr=False)
    # Best NORMAL-orientation placements, for human disambiguation (never auto-applied).
    alternatives: tuple[GridScreenTransform, ...] = field(default=(), repr=False)

    @property
    def accepted(self) -> bool:
        return self.status is FitStatus.ACCEPTED

    def metrics(self) -> dict[str, object]:
        return {"status": self.status.value, "method": self.method, "score": self.score,
                "margin": self.margin, "orientation": self.orientation, "candidates": self.candidates,
                "inliers": self.inliers, "residual_median_px": self.residual_median_px,
                "residual_max_px": self.residual_max_px, "hypotheses": list(self.hypotheses),
                "message": self.message}


# --- Anchors ------------------------------------------------------------------------

def fit_from_anchors(anchors: Iterable[tuple[int, CombatPoint | tuple[float, float]]], *,
                     reference_combat_size: tuple[float, float] | None = None) -> GridFitResult:
    """Least squares affine from explicit (DofusCellId, pixel) pairs; >= 3 non-collinear."""
    pairs = [(int(cell_id), (float(point[0]), float(point[1]))) for cell_id, point in anchors]
    if len({cell for cell, _ in pairs}) != len(pairs):
        return GridFitResult(FitStatus.INVALID_ANCHORS, "anchors", message="Ancre dupliquée")
    try:
        grid = [cell_to_grid(cell) for cell, _ in pairs]
    except ValueError as exc:
        return GridFitResult(FitStatus.INVALID_ANCHORS, "anchors", message=str(exc))
    design = np.array([[1.0, g.x, g.y] for g in grid])
    if len(pairs) < 3 or np.linalg.matrix_rank(design) < 3:
        return GridFitResult(FitStatus.INVALID_ANCHORS, "anchors",
                             message="Au moins 3 ancres non colinéaires sont requises")
    points = np.array([point for _, point in pairs])
    transform, residuals = _solve_affine(design, points, reference_combat_size)
    if transform is None:
        return GridFitResult(FitStatus.INVALID_ANCHORS, "anchors", message="Transformation dégénérée")
    return GridFitResult(
        FitStatus.ACCEPTED if transform.orientation is GridOrientation.NORMAL else FitStatus.ORIENTATION_CONFLICT,
        "anchors", transform, 1.0, 0.0, transform.orientation.value, len(pairs), len(pairs),
        float(np.median(residuals)), float(np.max(residuals)),
        message="Ancres explicites" if transform.orientation is GridOrientation.NORMAL
        else "Les ancres produisent une orientation non normale : IDs probablement inversés",
    )


def _solve_affine(design: np.ndarray, points: np.ndarray, reference_combat_size=None
                  ) -> tuple[GridScreenTransform | None, np.ndarray]:
    solution, *_ = np.linalg.lstsq(design, points, rcond=None)
    (ox, oy), (bxx, bxy), (byx, byy) = solution
    try:
        transform = GridScreenTransform(CombatPoint(float(ox), float(oy)), Vector2(float(bxx), float(bxy)),
                                        Vector2(float(byx), float(byy)), reference_combat_size)
    except ValueError:
        return None, np.array([])
    residuals = np.linalg.norm(design @ solution - points, axis=1)
    return transform, residuals


# --- Automatic fit from candidates -------------------------------------------------

def _circular_phase(values: np.ndarray) -> float:
    angles = 2 * math.pi * values
    return (math.atan2(float(np.sin(angles).mean()), float(np.cos(angles).mean())) / (2 * math.pi)) % 1.0


def _estimate_bases(points: np.ndarray, width: float, height: float) -> tuple[Vector2, Vector2] | None:
    """Dominant lattice vectors from centre-to-centre displacements.

    Contour sizes are biased (inner contours are smaller than the cell), centres
    are not: diagonal neighbours give basis_x (down-right) and basis_y (up-right).
    """
    if len(points) < 2:
        return None
    radius = max(width, 2 * height)
    delta = points[None, :, :] - points[:, None, :]
    dx, dy = delta[..., 0].ravel(), delta[..., 1].ravel()
    length = np.hypot(dx, dy)
    keep = (dx > 0) & (length <= radius) & (np.abs(dy) >= 0.25 * dx) & (dx >= 0.25 * np.abs(dy))
    down, up = keep & (dy > 0), keep & (dy < 0)
    if down.sum() < 3 or up.sum() < 3:
        return None
    return (Vector2(float(np.median(dx[down])), float(np.median(dy[down]))),
            Vector2(float(np.median(dx[up])), float(np.median(dy[up]))))


def _fit_lattice(points: np.ndarray, width: float, height: float):
    """Normal-orientation lattice through the points; returns (transform, indices, inliers, residuals)."""
    bases = _estimate_bases(points, width, height)
    if bases is None:
        bases = (Vector2(width / 2, height / 2), Vector2(width / 2, -height / 2))
    try:
        transform = GridScreenTransform(CombatPoint(0.0, 0.0), *bases)
    except ValueError:
        return None, np.zeros((len(points), 2)), np.zeros(len(points), bool), np.zeros(len(points))
    width, height = transform.cell_width, transform.cell_height
    fractional = np.array([transform.combat_to_fractional_grid(tuple(p)) for p in points])
    phase = (_circular_phase(fractional[:, 0]), _circular_phase(fractional[:, 1]))
    bx, by = transform.basis_x, transform.basis_y
    transform = transform.translated(phase[0] * bx.x + phase[1] * by.x, phase[0] * bx.y + phase[1] * by.y)
    inliers = np.ones(len(points), bool)
    tolerance = max(3.0, 0.25 * min(width, height))
    indices = np.zeros((len(points), 2))
    residuals = np.zeros(len(points))
    for _ in range(4):
        fractional = np.array([transform.combat_to_fractional_grid(tuple(p)) for p in points])
        indices = np.round(fractional)
        design = np.column_stack([np.ones(len(points)), indices])
        predicted = design @ np.array([[transform.origin.x, transform.origin.y],
                                       [transform.basis_x.x, transform.basis_x.y],
                                       [transform.basis_y.x, transform.basis_y.y]])
        residuals = np.linalg.norm(predicted - points, axis=1)
        inliers = residuals <= tolerance
        if inliers.sum() < MIN_CANDIDATES or np.linalg.matrix_rank(design[inliers]) < 3:
            return None, indices, inliers, residuals
        refined, _ = _solve_affine(design[inliers], points[inliers])
        if refined is None:
            return None, indices, inliers, residuals
        transform = refined
    fractional = np.array([transform.combat_to_fractional_grid(tuple(p)) for p in points])
    indices = np.round(fractional)
    design = np.column_stack([np.ones(len(points)), indices])
    predicted = design @ np.array([[transform.origin.x, transform.origin.y],
                                   [transform.basis_x.x, transform.basis_x.y],
                                   [transform.basis_y.x, transform.basis_y.y]])
    residuals = np.linalg.norm(predicted - points, axis=1)
    return transform, indices, residuals <= tolerance, residuals


def _orientation_bases(transform: GridScreenTransform) -> dict[GridOrientation, tuple[Vector2, Vector2]]:
    bx, by = transform.basis_x, transform.basis_y
    neg = lambda v: Vector2(-v.x, -v.y)  # noqa: E731
    return {GridOrientation.NORMAL: (bx, by), GridOrientation.SWAPPED: (by, bx),
            GridOrientation.MIRRORED: (neg(by), neg(bx)), GridOrientation.ROTATED_180: (neg(bx), neg(by))}


def fit_grid_from_candidates(candidates: Iterable[DiamondCandidate | Sequence[float]],
                             image_size: tuple[int, int], *, topology: GridTopology | None = None
                             ) -> GridFitResult:
    """Global fit. ``image_size`` = (width, height) of the combat crop."""
    items = [c if isinstance(c, DiamondCandidate) else DiamondCandidate.from_tuple(c) for c in candidates]
    if len(items) < MIN_CANDIDATES:
        return GridFitResult(FitStatus.INSUFFICIENT_CANDIDATES, "auto", candidates=len(items),
                             message=f"{len(items)} candidats ; au moins {MIN_CANDIDATES} requis")
    widths = np.array([c.width for c in items])
    heights = np.array([c.height for c in items])
    width, height = float(np.median(widths)), float(np.median(heights))
    consistent = [c for c in items if 0.7 <= c.width / width <= 1.3 and 0.7 <= c.height / height <= 1.3]
    if len(consistent) < MIN_CANDIDATES:
        return GridFitResult(FitStatus.INSUFFICIENT_CANDIDATES, "auto", candidates=len(items),
                             message="Pas de taille de losange dominante")
    points = np.array([(c.x, c.y) for c in consistent])
    lattice, _indices, inliers, residuals = _fit_lattice(points, width, height)
    if lattice is None:
        return GridFitResult(FitStatus.INSUFFICIENT_CANDIDATES, "auto", candidates=len(items),
                             message="Réseau incohérent : trop peu de candidats alignés")
    lengths = (lattice.basis_x.length, lattice.basis_y.length)
    if abs(lengths[0] - lengths[1]) / max(lengths) > MAX_ANISOTROPY:
        return GridFitResult(FitStatus.WEAK, "auto", lattice, candidates=len(items), inliers=int(inliers.sum()),
                             message="Anisotropie excessive entre les deux bases")
    inlier_points = points[inliers]
    image_w, image_h = image_size
    traversable = None
    if topology is not None and len(topology.cells) == CELL_COUNT:
        traversable = np.array([bool(c.walkable) and not bool(c.non_walkable_during_fight)
                                for c in topology.cells])
    margin_x, margin_y = lattice.cell_width / 2, lattice.cell_height / 2
    hypotheses: list[tuple[float, dict, GridScreenTransform]] = []
    for orientation, (b1, b2) in _orientation_bases(lattice).items():
        base = GridScreenTransform(lattice.origin, b1, b2, (float(image_w), float(image_h)))
        grid = np.round(np.array([base.combat_to_fractional_grid(tuple(p)) for p in inlier_points]))
        rows0, sums0 = grid[:, 0] - grid[:, 1], grid[:, 0] + grid[:, 1]
        for d in range(int(-rows0.max()) - 2, int(ROWS - rows0.min()) + 2):
            rows = rows0 + d
            for e in range(int(-sums0.max()) - 2, int(2 * MAP_WIDTH - sums0.min()) + 2):
                if (d + e) % 2:
                    continue
                sums = sums0 + e
                odd = rows.astype(int) & 1
                valid = (rows >= 0) & (rows < ROWS) & (sums - odd >= 0) & (sums - odd <= 2 * (MAP_WIDTH - 1))
                valid_ratio = float(valid.mean())
                if valid_ratio < 0.5:
                    continue
                dx, dy = (d + e) // 2, (e - d) // 2
                origin = CombatPoint(lattice.origin.x - dx * b1.x - dy * b2.x,
                                     lattice.origin.y - dx * b1.y - dy * b2.y)
                transform = GridScreenTransform(origin, b1, b2, (float(image_w), float(image_h)))
                centers_x = origin.x + _GRID_X * b1.x + _GRID_Y * b2.x
                centers_y = origin.y + _GRID_X * b1.y + _GRID_Y * b2.y
                inside = ((centers_x >= -margin_x) & (centers_x <= image_w + margin_x) &
                          (centers_y >= -margin_y) & (centers_y <= image_h + margin_y))
                ids = (rows[valid] * MAP_WIDTH + (sums[valid] - odd[valid]) // 2).astype(int)
                if traversable is not None:
                    precision = float(traversable[ids].mean()) if len(ids) else 0.0
                    expected = traversable & inside
                    recall = (len(set(ids.tolist()) & set(np.flatnonzero(expected).tolist())) /
                              max(1, int(expected.sum())))
                    agreement = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
                    score = valid_ratio * agreement
                else:
                    precision = recall = None
                    score = valid_ratio * float(inside.mean())
                hypotheses.append((score, {
                    "orientation": orientation.value, "shift": [dx, dy], "score": round(score, 4),
                    "valid_ratio": round(valid_ratio, 4), "inside_ratio": round(float(inside.mean()), 4),
                    "precision": None if precision is None else round(precision, 4),
                    "recall": None if recall is None else round(recall, 4),
                }, transform))
    if not hypotheses:
        return GridFitResult(FitStatus.WEAK, "auto", lattice, candidates=len(items), inliers=int(inliers.sum()),
                             message="Aucun placement des 560 cellules compatible avec les candidats")
    hypotheses.sort(key=lambda item: item[0], reverse=True)
    best_score, best_info, best_transform = hypotheses[0]
    second = hypotheses[1][0] if len(hypotheses) > 1 else 0.0
    normal = next((h for h in hypotheses if h[1]["orientation"] == GridOrientation.NORMAL.value), None)
    median_residual = float(np.median(residuals[inliers]))
    max_residual = float(np.max(residuals[inliers]))
    top = tuple(info for _score, info, _t in hypotheses[:6])
    common = dict(candidates=len(items), inliers=int(inliers.sum()), residual_median_px=median_residual,
                  residual_max_px=max_residual, hypotheses=top,
                  inlier_points=tuple((float(x), float(y)) for x, y in inlier_points),
                  alternatives=tuple(t for _s, info, t in hypotheses
                                     if info["orientation"] == GridOrientation.NORMAL.value)[:6])
    minimum = MIN_SCORE_WITH_TOPOLOGY if traversable is not None else MIN_SCORE_WITHOUT_TOPOLOGY
    if best_info["orientation"] != GridOrientation.NORMAL.value:
        proposal = normal[2] if normal else None
        return GridFitResult(FitStatus.ORIENTATION_CONFLICT, "auto", proposal, best_score, best_score - second,
                             best_info["orientation"], message=(
                                 f"Meilleure hypothèse en orientation {best_info['orientation']} : "
                                 "identités potentiellement inversées, validation humaine requise"), **common)
    status, message = FitStatus.ACCEPTED, "Alignement global accepté"
    if median_residual > MAX_MEDIAN_RESIDUAL_RATIO * best_transform.cell_width:
        status, message = FitStatus.WEAK, "Résidu médian trop élevé"
    elif best_score < minimum:
        status, message = FitStatus.WEAK, f"Score {best_score:.2f} < {minimum:.2f}"
    elif best_score - second < MIN_MARGIN:
        status, message = FitStatus.AMBIGUOUS, (
            f"Deux placements trop proches ({best_score:.2f} / {second:.2f}) : "
            "choisissez la map ou ajustez manuellement")
    return GridFitResult(status, "auto", best_transform, best_score, best_score - second,
                         GridOrientation.NORMAL.value, message=message, **common)


def candidate_union(image: np.ndarray) -> list[tuple[float, float, float, float, float]]:
    """Candidates from legacy and calibration thresholds, de-duplicated by centre."""
    from combatbot.vision.combat_grid import CALIBRATION_CANNY, LEGACY_CANNY, _deduplicate, detect_diamond_candidates
    merged = detect_diamond_candidates(image, LEGACY_CANNY) + detect_diamond_candidates(image, CALIBRATION_CANNY)
    return _deduplicate(merged)


# --- Visual alignment evidence (never changes identity) ------------------------------

def topology_consistency(grid: ProjectedGrid, per_cell: dict[int, float]) -> float | None:
    """Mean edge support on traversable cells minus non-traversable ones.

    DOFUS outlines only combat-traversable cells, so a stale declared map (or a
    shifted projection) raises the support on cells GameData says are blocked.
    """
    traversable, blocked = [], []
    for cell in grid.cells:
        value = per_cell.get(int(cell.cell_id))
        if value is None or cell.static_traversable_in_fight is None:
            continue
        (traversable if cell.static_traversable_in_fight else blocked).append(value)
    if not traversable or not blocked:
        return None
    return float(np.mean(traversable) - np.mean(blocked))


def projection_alignment(image: np.ndarray, grid: ProjectedGrid) -> tuple[float, dict[int, float]]:
    """Edge support along each theoretical diamond, in [0, 1].

    Returns (grid confidence, per-cell confidence). Only cells whose centre lies in
    the crop are scored; when static walkability is known, the grid confidence is
    the median over cells traversable in combat (the ones DOFUS outlines).
    """
    if image.size == 0 or not grid.cells:
        return 0.0, {}
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    edges = cv2.dilate(cv2.Canny(gray, 35, 110), np.ones((3, 3), np.uint8)) > 0
    height, width = edges.shape
    steps = (np.arange(ALIGNMENT_SAMPLES_PER_EDGE) + 0.5) / ALIGNMENT_SAMPLES_PER_EDGE
    ids = np.array([int(c.cell_id) for c in grid.cells])
    centers = np.array([(c.center.x, c.center.y) for c in grid.cells])
    vertices = np.array([[(p.x, p.y) for p in c.polygon] for c in grid.cells])  # (n, 4, 2)
    inside = (centers[:, 0] >= 0) & (centers[:, 0] < width) & (centers[:, 1] >= 0) & (centers[:, 1] < height)
    starts, ends = vertices, np.roll(vertices, -1, axis=1)
    samples = starts[:, :, None, :] + (ends - starts)[:, :, None, :] * steps[None, None, :, None]
    samples = samples.reshape(len(ids), -1, 2)
    xs, ys = np.round(samples[..., 0]).astype(int), np.round(samples[..., 1]).astype(int)
    visible = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
    hits = np.zeros(xs.shape, bool)
    hits[visible] = edges[ys[visible], xs[visible]]
    counts = visible.sum(axis=1)
    usable = inside & (counts > 0)
    support = np.where(counts > 0, hits.sum(axis=1) / np.maximum(counts, 1), 0.0)
    scored = {int(cell_id): float(min(1.0, value / 0.6))
              for cell_id, value, ok in zip(ids, support, usable) if ok}
    if not scored:
        return 0.0, {}
    expected = [scored[int(c.cell_id)] for c in grid.cells
                if int(c.cell_id) in scored and c.static_traversable_in_fight]
    values = expected if expected else list(scored.values())
    return float(np.median(values)), scored
