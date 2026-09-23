"""Recette réelle multi-map de la grille GameData (LOT 3B-2R).

Mesure 0.4.0 **tel quel** : ce module n'altère ni seuils, ni scoring, ni
GridScreenTransform, ni GridProjector, ni topology_consistency, ni détecteur.
Il enregistre des mesures automatiques identiques quel que soit le jugement de
l'utilisateur, puis ce jugement, séparément. Données sous
``data/validation/grid-real/<session>/`` (jamais versionnées).

Aucune action dans le jeu : les images viennent de captures en lecture seule ;
le map ID vient de l'utilisateur (``/mapid``), jamais d'une détection.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any, Sequence

import cv2
import numpy as np

from combatbot.gamedata.models import GridTopology
from combatbot.gamedata.topology import CELL_COUNT, MAP_WIDTH, ROWS, cell_to_grid
from combatbot.vision.combat_models import CombatObservation
from combatbot.vision.combat_observer import OverlayOptions, draw_diagnostic_overlay
from combatbot.vision.coordinates import CombatPoint
from combatbot.vision.gamedata_grid import projected_observation
from combatbot.vision.grid_fit import (
    MAX_MEDIAN_RESIDUAL_RATIO, MIN_TOPOLOGY_CONSISTENCY, candidate_union, fit_grid_from_candidates,
)
from combatbot.vision.grid_projection import GridProjector, GridScreenTransform, lattice_pixel_to_cell_id

RECIPE_SCHEMA_VERSION = 1
CAPTURE_KINDS = {
    "A": "juste après chargement de la map", "B": "personnage immobile", "C": "animation / entité",
    "D": "phase de placement", "E": "mon tour", "F": "tour ennemi",
}
ALIGNMENTS = ("correct", "decale", "incorrect", "ambigu")
SHIFT_DIRECTIONS = ("", "+U", "-U", "+V", "-V", "translation_px", "autre")
EDGE_KEYS = ("edge_top_ok", "edge_bottom_ok", "edge_left_ok", "edge_right_ok", "center_ok")
_GRID_X = np.array([cell_to_grid(c).x for c in range(CELL_COUNT)], dtype=float)
_GRID_Y = np.array([cell_to_grid(c).y for c in range(CELL_COUNT)], dtype=float)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Scoring of a given transform (same formula as fit_grid_from_candidates) ---------------

def score_transform(points: np.ndarray, image_size: tuple[int, int], topology: GridTopology | None,
                    transform: GridScreenTransform) -> dict[str, float]:
    """Score of one placement, computed exactly like each hypothesis of the automatic fit.

    Used only to *measure* how close the ±1-cell shifts of a confirmed transform are;
    tests assert equality with the fit's own best score.
    """
    if len(points) == 0:
        return {"score": 0.0, "valid_ratio": 0.0, "precision": None, "recall": None, "inside_ratio": 0.0}
    grid = np.round(np.array([transform.combat_to_fractional_grid(tuple(p)) for p in points]))
    rows, sums = grid[:, 0] - grid[:, 1], grid[:, 0] + grid[:, 1]
    odd = rows.astype(int) & 1
    valid = (rows >= 0) & (rows < ROWS) & (sums - odd >= 0) & (sums - odd <= 2 * (MAP_WIDTH - 1))
    valid_ratio = float(valid.mean())
    image_w, image_h = image_size
    margin_x, margin_y = transform.cell_width / 2, transform.cell_height / 2
    b1, b2, origin = transform.basis_x, transform.basis_y, transform.origin
    centers_x = origin.x + _GRID_X * b1.x + _GRID_Y * b2.x
    centers_y = origin.y + _GRID_X * b1.y + _GRID_Y * b2.y
    inside = ((centers_x >= -margin_x) & (centers_x <= image_w + margin_x) &
              (centers_y >= -margin_y) & (centers_y <= image_h + margin_y))
    ids = (rows[valid] * MAP_WIDTH + (sums[valid] - odd[valid]) // 2).astype(int)
    if topology is not None and len(topology.cells) == CELL_COUNT:
        traversable = np.array([bool(c.walkable) and not bool(c.non_walkable_during_fight) for c in topology.cells])
        precision = float(traversable[ids].mean()) if len(ids) else 0.0
        expected = traversable & inside
        recall = (len(set(ids.tolist()) & set(np.flatnonzero(expected).tolist())) / max(1, int(expected.sum())))
        agreement = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        score = valid_ratio * agreement
    else:
        precision = recall = None
        score = valid_ratio * float(inside.mean())
    return {"score": score, "valid_ratio": valid_ratio, "precision": precision, "recall": recall,
            "inside_ratio": float(inside.mean())}


def shifted(transform: GridScreenTransform, du: int, dv: int) -> GridScreenTransform:
    """Same lattice, identities moved by (du, dv) cells: cell (x, y) drawn where (x+du, y+dv) was."""
    bx, by = transform.basis_x, transform.basis_y
    origin = CombatPoint(transform.origin.x + du * bx.x + dv * by.x, transform.origin.y + du * bx.y + dv * by.y)
    return GridScreenTransform(origin, bx, by, transform.reference_combat_size)


def _percentile(values: Sequence[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None


def _stats(values: Sequence[float]) -> dict[str, float | None]:
    values = [float(v) for v in values if v is not None]
    return {"count": len(values), "min": min(values) if values else None, "mean": mean(values) if values else None,
            "median": median(values) if values else None, "p95": _percentile(values, 95),
            "max": max(values) if values else None}


def transform_drift(a: GridScreenTransform, b: GridScreenTransform) -> dict[str, float]:
    """Differences between two transforms, and between their 560 projected centres."""
    ax = np.column_stack([a.origin.x + _GRID_X * a.basis_x.x + _GRID_Y * a.basis_y.x,
                          a.origin.y + _GRID_X * a.basis_x.y + _GRID_Y * a.basis_y.y])
    bx = np.column_stack([b.origin.x + _GRID_X * b.basis_x.x + _GRID_Y * b.basis_y.x,
                          b.origin.y + _GRID_X * b.basis_x.y + _GRID_Y * b.basis_y.y])
    centers = np.linalg.norm(ax - bx, axis=1)
    return {"origin_px": math.hypot(a.origin.x - b.origin.x, a.origin.y - b.origin.y),
            "basis_x_px": math.hypot(a.basis_x.x - b.basis_x.x, a.basis_x.y - b.basis_x.y),
            "basis_y_px": math.hypot(a.basis_y.x - b.basis_y.x, a.basis_y.y - b.basis_y.y),
            "center_mean_px": float(centers.mean()), "center_max_px": float(centers.max())}


def _lattice_alignment(fit_transform: GridScreenTransform, applied: GridScreenTransform) -> dict[str, Any]:
    """Integer offset (cells) and sub-cell phase (px) of the fitted lattice relative to the applied one."""
    fx, fy = applied.combat_to_fractional_grid(fit_transform.origin)
    ix, iy = round(fx), round(fy)
    nearest = CombatPoint(fit_transform.origin.x - ix * fit_transform.basis_x.x - iy * fit_transform.basis_y.x,
                          fit_transform.origin.y - ix * fit_transform.basis_x.y - iy * fit_transform.basis_y.y)
    aligned = GridScreenTransform(nearest, fit_transform.basis_x, fit_transform.basis_y,
                                  fit_transform.reference_combat_size)
    return {"integer_offset": [ix, iy], "lattice_drift": transform_drift(aligned, applied)}


# --- Capture measures -----------------------------------------------------------------------

@dataclass
class CaptureMetrics:
    candidate_count: int
    inlier_count: int | None
    fit_status: str
    fit_message: str
    orientation: str | None
    best_score: float | None
    second_score: float | None
    score_margin: float | None
    fit_residual_median_px: float | None
    fit_residual_max_px: float | None
    integer_offset: list[int] | None
    lattice_drift: dict[str, float] | None
    residual_mean_px: float | None
    residual_median_px: float | None
    residual_p95_px: float | None
    residual_max_px: float | None
    residual_samples: int
    cell_width_px: float
    cell_height_px: float
    fitted_cell_width_px: float | None
    fitted_cell_height_px: float | None
    projection_confidence: float | None
    topology_consistency: float | None
    applied_score: dict[str, Any] | None
    shift_scores: dict[str, dict[str, Any]]
    best_shifted_u_score: float | None
    best_shifted_v_score: float | None
    margin_vs_shift: float | None
    automatic_flags: dict[str, bool | None]
    elapsed_ms: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def measure_capture(combat_image: np.ndarray, topology: GridTopology,
                    applied: GridScreenTransform) -> tuple[CaptureMetrics, Any, list]:
    """Automatic measures of one frame under the applied (confirmed or reused) transform."""
    import time
    started = time.perf_counter()
    size = (combat_image.shape[1], combat_image.shape[0])
    candidates = candidate_union(combat_image)
    fit = fit_grid_from_candidates(candidates, size, topology=topology)
    projected = GridProjector(applied).project(topology)
    grid = projected_observation(projected, combat_image)
    valid_ids = set(range(CELL_COUNT))
    residuals = []
    for cx, cy, *_ in candidates:
        cell = lattice_pixel_to_cell_id(applied, (cx, cy), valid_ids)
        if cell is not None:
            center = applied.grid_to_combat(cell_to_grid(int(cell)))
            residuals.append(math.hypot(cx - center.x, cy - center.y))
    points = np.array(fit.inlier_points) if fit.inlier_points else np.array([(c[0], c[1]) for c in candidates])
    applied_score = score_transform(points, size, topology, applied) if len(points) else None
    shift_scores = {name: score_transform(points, size, topology, shifted(applied, du, dv))
                    for name, (du, dv) in {"+U": (1, 0), "-U": (-1, 0), "+V": (0, 1), "-V": (0, -1)}.items()
                    } if len(points) else {}
    best_u = max((shift_scores[k]["score"] for k in ("+U", "-U") if k in shift_scores), default=None)
    best_v = max((shift_scores[k]["score"] for k in ("+V", "-V") if k in shift_scores), default=None)
    margin_vs_shift = (applied_score["score"] - max(best_u, best_v)
                       if applied_score and best_u is not None and best_v is not None else None)
    alignment = _lattice_alignment(fit.transform, applied) if fit.transform is not None else None
    median_residual = median(residuals) if residuals else None
    flags = {
        # Existing 0.4.0 thresholds only: measurement, not new criteria.
        "orientation_normal": applied.orientation.value == "NORMAL",
        "residual_acceptable": (median_residual <= MAX_MEDIAN_RESIDUAL_RATIO * applied.cell_width
                                if median_residual is not None else None),
        "topology_consistent": (grid.topology_consistency >= MIN_TOPOLOGY_CONSISTENCY
                                if grid.topology_consistency is not None else None),
        "fit_agrees_with_applied": (alignment["integer_offset"] == [0, 0] if alignment else None),
    }
    metrics = CaptureMetrics(
        len(candidates), fit.inliers if fit.transform is not None else None, fit.status.value, fit.message,
        fit.orientation, fit.score if fit.transform is not None else None,
        (fit.score - fit.margin) if fit.transform is not None else None,
        fit.margin if fit.transform is not None else None, fit.residual_median_px, fit.residual_max_px,
        alignment["integer_offset"] if alignment else None, alignment["lattice_drift"] if alignment else None,
        mean(residuals) if residuals else None, median_residual, _percentile(residuals, 95),
        max(residuals) if residuals else None, len(residuals), applied.cell_width, applied.cell_height,
        fit.transform.cell_width if fit.transform else None, fit.transform.cell_height if fit.transform else None,
        grid.projection_confidence, grid.topology_consistency, applied_score, shift_scores, best_u, best_v,
        margin_vs_shift, flags, (time.perf_counter() - started) * 1000,
    )
    return metrics, grid, candidates


# --- Visual material for the human check -----------------------------------------------------

def overlay_image(combat_image: np.ndarray, grid, candidates, *, red_blue: bool = False,
                  label_every: int = 5) -> np.ndarray:
    """Human-check overlay: walkability tint, outlines of traversable cells only, sparse IDs.

    Drawing all 560 outlines hides the floor; only GameData-traversable cells are outlined
    so their border can be compared with the visible floor edge.
    """
    observation = CombatObservation(True, 1.0, None, 0.0, None, 0.0, (), grid, None, None, 0.0, 0.0, 0.0)
    options = OverlayOptions(grid=False, cell_ids=True, walkability=True, red_blue=red_blue,
                             candidates=True, label_every=label_every)
    output = draw_diagnostic_overlay(combat_image, observation, options, candidates=candidates)
    outlines = [np.asarray(c.polygon, np.int32) for c in grid.cells if c.static_traversable]
    cv2.polylines(output, outlines, True, (60, 230, 255), 1, cv2.LINE_AA)
    return output


def rerender(session: "RealGridValidationSession", capture_id: str, topology: GridTopology) -> None:
    """Redraw overlay/review of a stored capture; stored metrics are left untouched."""
    capture = next(c for c in session.captures if c["capture_id"] == capture_id)
    image = cv2.imread(str(session.directory / capture["files"]["combat"]))
    grid = projected_observation(GridProjector(session.transform(capture["transform_id"])).project(topology), image)
    overlay = overlay_image(image, grid, candidate_union(image), red_blue="red_blue_auto" in capture)
    cv2.imwrite(str(session.directory / capture["files"]["overlay"]), overlay)
    cv2.imwrite(str(session.directory / capture["files"]["review"]), review_sheet(overlay, capture["edge_regions"]))


def edge_regions(grid, image_shape) -> dict[str, tuple[int, int, int, int]]:
    """Crops around the extreme traversable cells visible in the frame, plus the centre."""
    height, width = image_shape[:2]
    cells = [c for c in grid.cells if c.static_traversable and 0 <= c.center[0] < width and 0 <= c.center[1] < height]
    if not cells:
        cells = [c for c in grid.cells if 0 <= c.center[0] < width and 0 <= c.center[1] < height]
    if not cells:
        return {}
    cw, ch = int(grid.cell_width or 80), int(grid.cell_height or 40)
    box_w, box_h = cw * 5, ch * 5
    xs = np.array([c.center[0] for c in cells]); ys = np.array([c.center[1] for c in cells])
    picks = {"top": cells[int(ys.argmin())], "bottom": cells[int(ys.argmax())],
             "left": cells[int(xs.argmin())], "right": cells[int(xs.argmax())]}
    center = (int(xs.mean()), int(ys.mean()))
    regions = {}
    for name, (cx, cy) in {**{k: v.center for k, v in picks.items()}, "center": center}.items():
        x0 = int(min(max(0, cx - box_w // 2), max(0, width - box_w)))
        y0 = int(min(max(0, cy - box_h // 2), max(0, height - box_h)))
        regions[name] = (x0, y0, min(box_w, width), min(box_h, height))
    return regions


def review_sheet(overlay: np.ndarray, regions: dict[str, tuple[int, int, int, int]]) -> np.ndarray:
    tiles = []
    for name in ("top", "bottom", "left", "right", "center"):
        if name not in regions:
            continue
        x, y, w, h = regions[name]
        tile = cv2.resize(overlay[y:y + h, x:x + w], (360, max(1, int(360 * h / max(1, w)))))
        cv2.rectangle(tile, (0, 0), (150, 24), (0, 0, 0), -1)
        cv2.putText(tile, name.upper(), (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append(tile)
    if not tiles:
        return overlay
    height = max(t.shape[0] for t in tiles)
    padded = [np.vstack([t, np.zeros((height - t.shape[0], t.shape[1], 3), np.uint8)]) for t in tiles]
    rows = [np.hstack(padded[i:i + 3]) for i in range(0, len(padded), 3)]
    width = max(r.shape[1] for r in rows)
    rows = [np.hstack([r, np.zeros((r.shape[0], width - r.shape[1], 3), np.uint8)]) for r in rows]
    return np.vstack(rows)


# --- Red / blue placement measurement --------------------------------------------------------

def observed_placement_cells(combat_image: np.ndarray, grid) -> dict[str, list[int]]:
    """Cells whose inner diamond is dominantly red / blue (DOFUS placement tint). Estimate only."""
    hsv = cv2.cvtColor(combat_image, cv2.COLOR_BGR2HSV)
    height, width = hsv.shape[:2]
    red, blue = [], []
    for cell in grid.cells:
        cx, cy = cell.center
        if not (0 <= cx < width and 0 <= cy < height):
            continue
        polygon = np.array([(cx + (px - cx) * 0.55, cy + (py - cy) * 0.55) for px, py in cell.polygon], np.int32)
        mask = np.zeros((height, width), np.uint8)
        cv2.fillPoly(mask, [polygon], 255)
        pixels = hsv[mask > 0]
        if len(pixels) < 10:
            continue
        saturated = pixels[pixels[:, 1] >= 90]
        if len(saturated) < 0.35 * len(pixels):
            continue
        hue = saturated[:, 0]
        if ((hue <= 8) | (hue >= 170)).mean() >= 0.6:
            red.append(int(cell.cell_id))
        elif ((hue >= 95) & (hue <= 130)).mean() >= 0.6:
            blue.append(int(cell.cell_id))
    return {"red": red, "blue": blue}


def compare_sets(expected: set[int], observed: set[int]) -> dict[str, Any]:
    tp = len(expected & observed)
    return {"expected": sorted(expected), "observed": sorted(observed), "true_positive": tp,
            "false_positive": sorted(observed - expected), "false_negative": sorted(expected - observed),
            "precision": tp / len(observed) if observed else None,
            "recall": tp / len(expected) if expected else None}


# --- Session ---------------------------------------------------------------------------------------

@dataclass
class RealGridValidationSession:
    session_id: str
    root: Path
    environment: dict[str, Any] = field(default_factory=dict)
    transforms: list[dict[str, Any]] = field(default_factory=list)
    maps: list[dict[str, Any]] = field(default_factory=list)
    captures: list[dict[str, Any]] = field(default_factory=list)
    stale_tests: list[dict[str, Any]] = field(default_factory=list)
    red_blue: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    schema_version: int = RECIPE_SCHEMA_VERSION

    @property
    def directory(self) -> Path:
        return self.root / self.session_id

    @classmethod
    def create(cls, root: Path, environment: dict[str, Any], session_id: str | None = None
               ) -> "RealGridValidationSession":
        session = cls(session_id or f"grid-real-{datetime.now().strftime('%Y%m%d-%H%M%S')}", Path(root),
                      dict(environment))
        session.directory.mkdir(parents=True, exist_ok=False)
        session.save()
        return session

    @classmethod
    def load(cls, root: Path, session_id: str) -> "RealGridValidationSession":
        raw = json.loads((Path(root) / session_id / "session.json").read_text(encoding="utf-8"))
        if raw.get("schema_version") != RECIPE_SCHEMA_VERSION:
            raise ValueError("Version de session de recette non prise en charge")
        return cls(raw["session_id"], Path(root), raw.get("environment", {}), raw.get("transforms", []),
                   raw.get("maps", []), raw.get("captures", []), raw.get("stale_tests", []),
                   raw.get("red_blue", []), raw.get("created_at", ""), raw["schema_version"])

    def save(self) -> Path:
        path = self.directory / "session.json"
        payload = {"schema_version": self.schema_version, "session_id": self.session_id,
                   "created_at": self.created_at, "environment": self.environment,
                   "transforms": self.transforms, "maps": self.maps, "captures": self.captures,
                   "stale_tests": self.stale_tests, "red_blue": self.red_blue}
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        temporary.replace(path)
        return path

    # transforms
    def add_transform(self, transform: GridScreenTransform, source: str, *, map_id: int | None = None,
                      method: str = "", notes: str = "") -> str:
        transform_id = f"T{len(self.transforms) + 1}"
        self.transforms.append({"transform_id": transform_id, "transform": transform.to_dict(), "source": source,
                                "map_id": map_id, "method": method, "notes": notes, "created_at": _now()})
        self.save()
        return transform_id

    def transform(self, transform_id: str) -> GridScreenTransform:
        record = next(t for t in self.transforms if t["transform_id"] == transform_id)
        return GridScreenTransform.from_dict(record["transform"])

    # maps
    def map_record(self, map_id: int) -> dict[str, Any]:
        record = next((m for m in self.maps if m["map_id"] == map_id), None)
        if record is None:
            record = {"map_id": map_id, "map_id_source": None, "label": "", "first_transform_id": None,
                      "final_transform_id": None, "calibration_method": None, "reuse": None,
                      "verdict": None, "captures": []}
            self.maps.append(record)
        return record

    def declare_map(self, map_id: int, map_id_source: str, label: str = "") -> dict[str, Any]:
        if map_id_source not in ("user_verified_mapid", "manual_guess"):
            raise ValueError("map_id_source inconnu")
        record = self.map_record(map_id)
        record["map_id_source"], record["label"] = map_id_source, label or record["label"]
        self.save()
        return record

    def add_capture(self, *, map_id: int, map_id_source: str, kind: str, transform_id: str,
                    frame: np.ndarray, combat_image: np.ndarray, topology: GridTopology,
                    stale: bool = False, notes: str = "", red_blue: bool = False,
                    context: dict[str, Any] | None = None) -> dict[str, Any]:
        if kind not in CAPTURE_KINDS:
            raise ValueError(f"Type de capture inconnu : {kind}")
        applied = self.transform(transform_id)
        metrics, grid, candidates = measure_capture(combat_image, topology, applied)
        capture_id = f"C{len(self.captures) + 1:03d}_{map_id}_{kind}"
        folder = self.directory / "captures" / capture_id
        folder.mkdir(parents=True, exist_ok=True)
        overlay = overlay_image(combat_image, grid, candidates, red_blue=red_blue)
        cv2.imwrite(str(folder / "frame.png"), frame)
        cv2.imwrite(str(folder / "combat.png"), combat_image)
        cv2.imwrite(str(folder / "overlay.png"), overlay)
        regions = edge_regions(grid, combat_image.shape)
        cv2.imwrite(str(folder / "review.png"), review_sheet(overlay, regions))
        record = {"capture_id": capture_id, "map_id": map_id, "map_id_source": map_id_source, "kind": kind,
                  "kind_label": CAPTURE_KINDS[kind], "timestamp": _now(), "transform_id": transform_id,
                  "stale_map": stale, "notes": notes, "files": {
                      "frame": f"captures/{capture_id}/frame.png", "combat": f"captures/{capture_id}/combat.png",
                      "overlay": f"captures/{capture_id}/overlay.png", "review": f"captures/{capture_id}/review.png"},
                  "edge_regions": regions, "metrics": metrics.to_dict(), "context": dict(context or {})}
        if red_blue:
            observed = observed_placement_cells(combat_image, grid)
            hints_red = {int(c.cell_id) for c in grid.cells if c.red_hint}
            hints_blue = {int(c.cell_id) for c in grid.cells if c.blue_hint}
            record["red_blue_auto"] = {"red": compare_sets(hints_red, set(observed["red"])),
                                       "blue": compare_sets(hints_blue, set(observed["blue"]))}
        self.captures.append(record)
        if not stale:
            record_map = self.map_record(map_id)
            record_map["captures"].append(capture_id)
            if record_map["map_id_source"] is None:
                record_map["map_id_source"] = map_id_source
        self.save()
        return record

    def set_verdict(self, map_id: int, *, alignment: str, edges: dict[str, bool], shift_direction: str = "",
                    notes: str = "", major_anomaly: bool = False, transform_id: str | None = None) -> dict[str, Any]:
        if alignment not in ALIGNMENTS or shift_direction not in SHIFT_DIRECTIONS:
            raise ValueError("Jugement d'alignement inconnu")
        if set(edges) != set(EDGE_KEYS):
            raise ValueError(f"Points de contrôle attendus : {EDGE_KEYS}")
        record = self.map_record(map_id)
        record["verdict"] = {"alignment": alignment, "shift_direction": shift_direction,
                             **{k: bool(v) for k, v in edges.items()}, "major_anomaly": bool(major_anomaly),
                             "notes": notes, "transform_id": transform_id, "timestamp": _now()}
        self.save()
        return record

    def record_reuse(self, map_id: int, transform_id: str, *, reused_exactly: bool,
                     recalibrated_transform_id: str | None = None, notes: str = "") -> None:
        record = self.map_record(map_id)
        drift = None
        if recalibrated_transform_id:
            drift = transform_drift(self.transform(recalibrated_transform_id), self.transform(transform_id))
        record["first_transform_id"] = record["first_transform_id"] or transform_id
        record["final_transform_id"] = recalibrated_transform_id or transform_id
        record["reuse"] = {"applied_transform_id": transform_id, "reused_exactly": reused_exactly,
                           "recalibrated_transform_id": recalibrated_transform_id, "drift": drift, "notes": notes}
        self.save()

    def add_stale_test(self, *, old_map_id: int, new_map_id: int, before_capture: str,
                       after_capture: str | None, notes: str = "") -> None:
        self.stale_tests.append({"old_map_id": old_map_id, "new_map_id": new_map_id,
                                 "before_capture": before_capture, "after_capture": after_capture,
                                 "notes": notes, "timestamp": _now()})
        self.save()

    def add_red_blue_annotation(self, *, map_id: int, capture_id: str, red_match: str, blue_match: str,
                                real_red: list[int] | None = None, real_blue: list[int] | None = None,
                                notes: str = "") -> None:
        capture = next(c for c in self.captures if c["capture_id"] == capture_id)
        auto = capture.get("red_blue_auto", {})
        entry = {"map_id": map_id, "capture_id": capture_id, "red_match": red_match, "blue_match": blue_match,
                 "notes": notes, "timestamp": _now()}
        for colour, real in (("red", real_red), ("blue", real_blue)):
            if real is not None and colour in auto:
                entry[f"{colour}_vs_user"] = compare_sets(set(auto[colour]["expected"]), set(real))
        self.red_blue.append(entry)
        self.save()

    # --- evaluation -------------------------------------------------------------------------
    def map_status(self, record: dict[str, Any]) -> tuple[str, list[str]]:
        """PASS only if every criterion of the recipe holds; reasons listed otherwise."""
        reasons = []
        verdict = record.get("verdict")
        captures = [c for c in self.captures if c["capture_id"] in record.get("captures", [])]
        if record.get("map_id_source") != "user_verified_mapid":
            reasons.append("map ID non vérifié par /mapid")
        if verdict is None:
            return "NOT_JUDGED", reasons + ["pas de jugement utilisateur"]
        if verdict["alignment"] == "ambigu":
            return "AMBIGUOUS", reasons + ["jugé ambigu"]
        if verdict["alignment"] != "correct":
            reasons.append(f"alignement jugé {verdict['alignment']}")
        reasons += [f"{key} = non" for key in EDGE_KEYS if not verdict.get(key)]
        if verdict.get("major_anomaly"):
            reasons.append("anomalie visuelle majeure")
        final = [c for c in captures if c["transform_id"] == (verdict.get("transform_id") or record.get("final_transform_id"))]
        # Outside combat DOFUS draws no grid: candidates, residual and consistency are not
        # applicable there, so automatic criteria come from placement/combat frames only.
        gridded = [c for c in (final or captures) if c.get("context", {}).get("mode") in ("placement", "combat")]
        if not gridded:
            return "VISUAL_ONLY", reasons + ["aucune capture placement/combat : critères automatiques non mesurables"]
        for capture in gridded:
            flags = capture["metrics"]["automatic_flags"]
            if flags.get("orientation_normal") is False:
                reasons.append(f"{capture['capture_id']} : orientation non normale")
            if flags.get("residual_acceptable") is False:
                reasons.append(f"{capture['capture_id']} : résidu médian au-delà du seuil 0.4.0")
            if flags.get("topology_consistent") is False:
                reasons.append(f"{capture['capture_id']} : topology_consistency sous le seuil 0.4.0")
        return ("PASS" if not reasons else "FAIL"), reasons

    def baseline(self) -> dict[str, Any]:
        statuses = {}
        for record in self.maps:
            status, reasons = self.map_status(record)
            statuses[str(record["map_id"])] = {"status": status, "reasons": reasons, "label": record.get("label")}
        counts: dict[str, int] = {}
        for value in statuses.values():
            counts[value["status"]] = counts.get(value["status"], 0) + 1
        all_live = [c for c in self.captures if not c["stale_map"]]
        # Distributions only from frames where DOFUS draws the grid (placement/combat).
        live = [c for c in all_live if c.get("context", {}).get("mode") in ("placement", "combat")]
        pass_maps = {int(k) for k, v in statuses.items() if v["status"] == "PASS"}
        ambiguous_maps = {int(k) for k, v in statuses.items() if v["status"] == "AMBIGUOUS"}
        consistency_correct = [c["metrics"]["topology_consistency"] for c in live if c["map_id"] in pass_maps]
        consistency_stale = [c["metrics"]["topology_consistency"] for c in self.captures if c["stale_map"]]
        margins_correct = [c["metrics"]["score_margin"] for c in live if c["map_id"] in pass_maps]
        margins_ambiguous = [c["metrics"]["score_margin"] for c in live if c["map_id"] in ambiguous_maps]
        shift_margin = [c["metrics"]["margin_vs_shift"] for c in live]
        frame_drift = []
        for record in self.maps:
            chosen = [c for c in live if c["map_id"] == record["map_id"] and c["metrics"]["lattice_drift"]]
            frame_drift += [c["metrics"]["lattice_drift"]["center_max_px"] for c in chosen]
        reuse = [m["reuse"] for m in self.maps if m.get("reuse")]
        reuse_drift = [r["drift"]["center_max_px"] for r in reuse if r.get("drift")]
        verified = sorted({m["map_id"] for m in self.maps if m.get("map_id_source") == "user_verified_mapid"})
        return {
            "session_id": self.session_id, "verified_map_ids": verified, "maps_total": len(self.maps),
            "captures": len(self.captures), "live_captures": len(all_live),
            "gridded_live_captures": len(live), "exploration_captures_not_applicable": len(all_live) - len(live),
            "stale_captures": len(self.captures) - len(all_live),
            "map_status_counts": counts, "map_status": statuses,
            "residual_median_px_per_capture": _stats([c["metrics"]["residual_median_px"] for c in live]),
            "residual_max_px_per_capture": _stats([c["metrics"]["residual_max_px"] for c in live]),
            "residual_mean_px_per_capture": _stats([c["metrics"]["residual_mean_px"] for c in live]),
            "topology_consistency_pass": _stats(consistency_correct),
            "topology_consistency_stale": _stats(consistency_stale),
            "score_margin_pass": _stats(margins_correct), "score_margin_ambiguous": _stats(margins_ambiguous),
            "margin_vs_one_cell_shift": _stats(shift_margin),
            "lattice_drift_vs_applied_center_max_px": _stats(frame_drift),
            "transform_reuse": {"maps": len(reuse), "reused_exactly": sum(bool(r["reused_exactly"]) for r in reuse),
                                "recalibrated": sum(bool(r["recalibrated_transform_id"]) for r in reuse),
                                "recalibration_center_drift_px": _stats(reuse_drift)},
            "fit_status_counts": _count(c["metrics"]["fit_status"] for c in live),
            "integer_offsets": _count(str(c["metrics"]["integer_offset"]) for c in live),
            "stale_tests": self.stale_tests, "red_blue": self.red_blue,
            "thresholds_0_4_0": {"MAX_MEDIAN_RESIDUAL_RATIO": MAX_MEDIAN_RESIDUAL_RATIO,
                                 "MIN_TOPOLOGY_CONSISTENCY": MIN_TOPOLOGY_CONSISTENCY},
        }


def _count(values) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[str(value)] = result.get(str(value), 0) + 1
    return result


__all__ = ["RealGridValidationSession", "measure_capture", "score_transform", "shifted", "transform_drift",
           "observed_placement_cells", "compare_sets", "CAPTURE_KINDS", "ALIGNMENTS", "EDGE_KEYS",
           "SHIFT_DIRECTIONS"]
