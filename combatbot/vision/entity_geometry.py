"""Sous-régions d'une cellule projetée, relatives au losange (LOT 3B-5).

Chaque pixel est exprimé dans la base du losange : ``p = centre + s·u + t·v`` avec ``u`` vers le
sommet droit et ``v`` vers le sommet bas. Le losange est ``|s| + |t| ≤ 1`` et le rayon normalisé
``r = √(s² + t²)``. Aucune dimension pixel n'est codée : tout dérive du polygone projeté.

Mesures réelles (client 2.64.5, frames C011 et C003) :

- l'anneau d'équipe au sol a un rayon normalisé médian 0,52 (p10 0,46, p90 0,58), 65–73 % de ses
  pixels dans la moitié basse (le sprite masque le haut) ;
- dans la moitié basse, la chroma Lab de la bande [0,44 ; 0,60] dépasse celle de la bande
  extérieure [0,62 ; 0,76] de 26 à 65 sur les anneaux, de 0 à 3 sur le décor et les cellules de
  placement pleines. Le sprite reste à l'intérieur (r < 0,40) : on compare donc à l'extérieur.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RING_BAND = (0.44, 0.60)          # GROUND_RING_ROI : trait de l'anneau
OUTER_BAND = (0.62, 0.76)         # référence de fond immédiate, hors anneau et hors sprite
INNER_BAND = (0.28, 0.42)         # intérieur de l'anneau (pieds possibles) : case pleine vs trait
CENTER_MAX = 0.25                 # CENTER_ROI : signal complémentaire seulement
FOOT = (0.40, 0.0, 0.40)          # FOOT_ROI : r ≤ 0,40 et 0 ≤ t ≤ 0,40
BACKGROUND = (0.62, 0.95)         # BACKGROUND_ROI : r ≥ 0,62 et |s|+|t| ≤ 0,95 (modèle de fond)
SECTORS = 8
LOWER_SECTORS = (4, 5, 6, 7)      # t > 0


def cell_basis(polygon, center=None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Centre et demi-axes du losange depuis ses sommets (droite = x max, bas = y max)."""
    points = np.asarray(polygon, dtype=np.float64)
    middle = points.mean(axis=0) if center is None else np.asarray(center, dtype=np.float64)
    right = points[int(np.argmax(points[:, 0]))] - middle
    bottom = points[int(np.argmax(points[:, 1]))] - middle
    return middle, right, bottom


@dataclass
class RoiIndex:
    """Pixels d'une sous-région : index plats dans l'image et position (0..n-1) de la cellule."""

    flat: np.ndarray
    cell: np.ndarray
    counts: np.ndarray
    sector: np.ndarray | None = None
    pos: np.ndarray | None = None   # index plat relatif à ``CellRoiMaps.box``


@dataclass
class CellRoiMaps:
    cell_ids: np.ndarray
    centers: np.ndarray
    ring: RoiIndex          # moitié basse seulement, avec secteurs
    outer: RoiIndex         # moitié basse seulement
    inner: RoiIndex         # moitié basse seulement
    center: RoiIndex
    foot: RoiIndex
    background: RoiIndex
    sector_counts: np.ndarray
    visibility: np.ndarray
    traversable: np.ndarray  # 1 traversable, 0 non traversable, -1 inconnu (GameData statique)
    box: tuple[int, int, int, int]  # (x0, y0, x1, y1) : seule zone convertie en couleur
    shape: tuple[int, int]
    partial_ring: RoiIndex
    partial_outer: RoiIndex
    partial_inner: RoiIndex

    @property
    def size(self) -> int:
        return len(self.cell_ids)

    def index_of(self, cell_id: int) -> int | None:
        found = np.nonzero(self.cell_ids == cell_id)[0]
        return int(found[0]) if len(found) else None


ROI_NAMES = ("ring", "outer", "inner", "center", "foot", "background", "partial_ring", "partial_outer", "partial_inner")


def _roi_masks(s: np.ndarray, t: np.ndarray) -> dict[str, np.ndarray]:
    radius = np.hypot(s, t)
    lower = t > 0
    return {
        "partial_ring": (radius >= RING_BAND[0]) & (radius <= RING_BAND[1]),
        "partial_outer": (radius >= OUTER_BAND[0]) & (radius <= OUTER_BAND[1]),
        "partial_inner": (radius >= INNER_BAND[0]) & (radius <= INNER_BAND[1]),
        "ring": lower & (radius >= RING_BAND[0]) & (radius <= RING_BAND[1]),
        "outer": lower & (radius >= OUTER_BAND[0]) & (radius <= OUTER_BAND[1]),
        "inner": lower & (radius >= INNER_BAND[0]) & (radius <= INNER_BAND[1]),
        "center": radius < CENTER_MAX,
        "foot": (radius <= FOOT[0]) & (t >= FOOT[1]) & (t <= FOOT[2]),
        "background": (radius >= BACKGROUND[0]) & ((np.abs(s) + np.abs(t)) <= BACKGROUND[1]),
        "diamond": (np.abs(s) + np.abs(t)) <= 1.0,
    }


def _sectors(s: np.ndarray, t: np.ndarray) -> np.ndarray:
    return ((np.arctan2(t, s) + np.pi) / (2 * np.pi) * SECTORS).astype(np.int64) % SECTORS


def _eligible(cells, traversable_only: bool):
    for cell in cells:
        if cell.cell_id is None or not cell.polygon:
            continue
        static = getattr(cell, "static_traversable", None)
        if traversable_only and static is False:
            continue
        middle, u, v = cell_basis(cell.polygon, cell.center)
        if abs(float(np.linalg.det(np.column_stack([u, v])))) < 1e-6:
            continue
        yield cell, static, middle, u, v


def _finish(parts, ids, centers, visibility, traversable, shape) -> CellRoiMaps:
    count = len(ids)

    def assemble(name: str) -> RoiIndex:
        chunks = parts[name]
        if not chunks:
            return RoiIndex(np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(count))
        flat = np.concatenate([chunk[0] for chunk in chunks]).astype(np.int64)
        cell = np.concatenate([chunk[1] for chunk in chunks]).astype(np.int64)
        roi = RoiIndex(flat, cell, np.bincount(cell, minlength=count).astype(np.float64))
        if name in ("ring", "outer", "inner", "partial_ring", "partial_outer", "partial_inner"):
            roi.sector = np.concatenate([chunk[2] for chunk in chunks]).astype(np.int64)
        return roi

    rois = {name: assemble(name) for name in ROI_NAMES}
    ring = rois["ring"]
    sector_counts = (np.bincount(ring.cell * SECTORS + ring.sector, minlength=count * SECTORS)
                     .reshape(count, SECTORS).astype(np.float64)
                     if ring.sector is not None and len(ring.cell) else np.zeros((count, SECTORS)))
    # Rectangle englobant de toutes les sous-régions : seule cette zone est convertie en Lab/HSV.
    height, width = shape
    box = (0, 0, 0, 0)
    flats = [roi.flat for roi in rois.values() if len(roi.flat)]
    if flats:
        ys = np.concatenate([flat // width for flat in flats])
        xs = np.concatenate([flat % width for flat in flats])
        box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    box_width = box[2] - box[0]
    for roi in rois.values():
        roi.pos = (roi.flat // width - box[1]) * box_width + (roi.flat % width - box[0])
    return CellRoiMaps(np.asarray(ids, np.int64), np.asarray(centers, np.int64).reshape(-1, 2),
                       ring, rois["outer"], rois["inner"], rois["center"], rois["foot"], rois["background"],
                       sector_counts, np.asarray(visibility, np.float64), np.asarray(traversable, np.int64),
                       box, shape, rois["partial_ring"], rois["partial_outer"], rois["partial_inner"])


def build_roi_maps(cells, shape: tuple[int, int], *, traversable_only: bool = True) -> CellRoiMaps:
    """Précalcule les sous-régions (une fois par projection).

    ``traversable_only`` : les cellules non traversables en combat selon GameData ne peuvent pas
    porter d'entité ; elles sont exclues (leur état statique reste celui de GameData).

    Projection affine (GAMEDATA_PROJECTED) : toutes les cellules ont la même base à une translation
    près ; un gabarit unique est translaté de façon vectorisée. Sinon, calcul cellule par cellule.
    """
    eligible = list(_eligible(cells, traversable_only))
    if not eligible:
        return _finish({name: [] for name in ROI_NAMES}, [], [], [], [], shape)
    bases = np.asarray([np.concatenate([u, v]) for _cell, _static, _middle, u, v in eligible])
    reference = np.median(bases, axis=0)
    # Polygones arrondis au pixel : les bases d'une projection affine varient de ~1 px.
    if len(eligible) > 1 and float(np.abs(bases - reference).max()) <= 1.5:
        return _build_from_template(eligible, reference, shape)
    return _build_per_cell(eligible, shape)


def _build_from_template(eligible, reference: np.ndarray, shape: tuple[int, int]) -> CellRoiMaps:
    height, width = shape
    u, v = reference[:2], reference[2:]
    inverse = np.linalg.inv(np.column_stack([u, v]))
    reach_x = int(np.ceil(abs(u[0]) + abs(v[0]))) + 1
    reach_y = int(np.ceil(abs(u[1]) + abs(v[1]))) + 1
    dy, dx = np.mgrid[-reach_y:reach_y + 1, -reach_x:reach_x + 1]
    dx, dy = dx.ravel(), dy.ravel()
    s, t = inverse @ np.stack([dx, dy]).astype(np.float64)
    masks = _roi_masks(s, t)
    sector_template = _sectors(s, t)
    centers = np.asarray([np.round(middle) for _cell, _static, middle, _u, _v in eligible], np.int64)
    ids = [int(cell.cell_id) for cell, *_ in eligible]
    traversable = [1 if static is True else (0 if static is False else -1) for _cell, static, *_ in eligible]
    slots = np.arange(len(eligible))
    parts: dict[str, list[tuple[np.ndarray, ...]]] = {name: [] for name in ROI_NAMES}
    diamond_total = max(1, int(masks["diamond"].sum()))
    visibility = np.zeros(len(eligible))
    for name in ROI_NAMES + ("diamond",):
        keep = masks[name]
        xs = centers[:, 0:1] + dx[keep][None, :]
        ys = centers[:, 1:2] + dy[keep][None, :]
        inside = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
        if name == "diamond":
            visibility = inside.sum(axis=1) / diamond_total
            continue
        cell = np.broadcast_to(slots[:, None], xs.shape)[inside]
        flat = (ys * width + xs)[inside]
        if name in ("ring", "outer", "inner", "partial_ring", "partial_outer", "partial_inner"):
            sector = np.broadcast_to(sector_template[keep][None, :], xs.shape)[inside]
            parts[name].append((flat, cell, sector))
        else:
            parts[name].append((flat, cell))
    visible = visibility > 0
    if not visible.all():
        # Cellules entièrement hors image : retirées, index de cellule renumérotés.
        remap = np.cumsum(visible) - 1
        for name in ROI_NAMES:
            chunk = parts[name][0]
            keep = visible[chunk[1]]
            parts[name] = [tuple(array[keep] if i != 1 else remap[array[keep]] for i, array in enumerate(chunk))]
        ids = [value for value, flag in zip(ids, visible) if flag]
        traversable = [value for value, flag in zip(traversable, visible) if flag]
        centers, visibility = centers[visible], visibility[visible]
    return _finish(parts, ids, [tuple(center) for center in centers.tolist()], visibility.tolist(),
                   traversable, shape)


def _build_per_cell(eligible, shape: tuple[int, int]) -> CellRoiMaps:
    height, width = shape
    parts: dict[str, list[tuple[np.ndarray, ...]]] = {name: [] for name in ROI_NAMES}
    ids, centers, visibility, traversable = [], [], [], []
    for cell, static, middle, u, v in eligible:
        inverse = np.linalg.inv(np.column_stack([u, v]))
        points = np.asarray(cell.polygon, dtype=np.float64)
        x0, y0 = np.floor(points.min(axis=0)).astype(int)
        x1, y1 = np.ceil(points.max(axis=0)).astype(int)
        full_area = max(1, (x1 - x0 + 1) * (y1 - y0 + 1))
        cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(width - 1, x1), min(height - 1, y1)
        if cx1 < cx0 or cy1 < cy0:
            continue
        slot = len(ids)
        ids.append(int(cell.cell_id))
        centers.append((int(round(middle[0])), int(round(middle[1]))))
        visibility.append(((cx1 - cx0 + 1) * (cy1 - cy0 + 1)) / full_area)
        traversable.append(1 if static is True else (0 if static is False else -1))
        ys, xs = np.mgrid[cy0:cy1 + 1, cx0:cx1 + 1]
        s, t = inverse @ np.stack([xs.ravel() - middle[0], ys.ravel() - middle[1]])
        flat = (ys.ravel() * width + xs.ravel()).astype(np.int64)
        masks = _roi_masks(s, t)
        for name in ROI_NAMES:
            keep = masks[name]
            chunk = (flat[keep], np.full(int(keep.sum()), slot))
            parts[name].append(chunk + ((_sectors(s[keep], t[keep]),) if name in ("ring", "outer", "inner", "partial_ring", "partial_outer", "partial_inner") else ()))
    return _finish(parts, ids, centers, visibility, traversable, (height, width))


def grid_signature(cells, shape: tuple[int, int]) -> tuple:
    """Clé de cache : une nouvelle projection (dérive corrigée, map) reconstruit les index."""
    sample = tuple((cell.cell_id, tuple(cell.center), getattr(cell, "static_traversable", None))
                   for cell in cells[:: max(1, len(cells) // 16)])
    return (len(cells), shape, sample)
