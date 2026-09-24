"""Profils visuels versionnés : joueur confirmé par l'utilisateur, couleurs d'équipe annotées.

Aucun profil n'est inventé : le profil joueur vient d'une cellule désignée par l'utilisateur sur
une capture PythonBot (et exige un anneau réellement détecté sur cette cellule) ; le profil
d'équipes vient d'annotations humaines. Les profils portent le layout et ne sont pas appliqués à
un layout incompatible.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path

import cv2
import numpy as np

from combatbot.vision.entity_detector import CellEntityDetector, EntityDetectorConfig, measure_cell
from combatbot.vision.entity_models import (
    PLAYER_DISTANCE_TOLERANCE_DEFAULT, PLAYER_MARGIN_DEFAULT, MarkerColorClass, PlayerVisualProfile,
    PlayerVisualProfileV2, TeamMarkerProfile, player_profile_from_dict,
)

# Tolérance de teinte d'une classe d'anneau issue d'un seul échantillon : les anneaux d'une même
# couleur mesurés sur C011/C003 varient de ±2 (rouge 1–5, bleu 118–120) ; marge ×2,5.
SINGLE_SAMPLE_HUE_TOLERANCE = 10.0
MIN_HUE_TOLERANCE = 8.0
# Tolérance Lab (ΔE) du profil joueur : provisoire, mesurée ensuite sur annotations (--entities).
PLAYER_LAB_TOLERANCE = 30.0


# LOT 3B-5B. Une classe d'équipe issue d'anneaux complets exige au moins 3 anneaux mesurés ;
# sinon sa teinte est estimée par enrichissement anneau/extérieur aux cellules annotées.
MIN_FULL_RING_SAMPLES = 3
ENRICHMENT_MIN_PIXELS = 50
ENRICHMENT_WINDOW = 15.0
# L'anneau partiel d'une équipe n'est autorisé que s'il retrouve ≥ 3 vérités TRAIN de cette équipe
# sans jamais se déclencher sur une vérité contraire (autre équipe, EMPTY_CONFIRMED).
PARTIAL_GATE_MIN_POSITIVES = 3


class ProfileError(ValueError):
    """Désignation refusée : pas d'anneau mesurable, cellule hors grille, etc."""


def player_profile_from_cell(image, grid, cell_id: int, *, layout_signature: str | None,
                             detector: CellEntityDetector | None = None) -> PlayerVisualProfile:
    cell = grid.cell_by_id(cell_id) if hasattr(grid, "cell_by_id") else None
    if cell is None:
        raise ProfileError(f"Cellule {cell_id} absente de la grille projetée")
    detector = detector or CellEntityDetector()
    measures = measure_cell(image, cell, detector)
    config: EntityDetectorConfig = detector.config
    if not measures or measures["peak"] < config.peak_presence or measures["hue"] is None:
        raise ProfileError("Aucun anneau au sol mesurable sur cette cellule : choisissez la cellule "
                           "où se trouve le marqueur de votre personnage")
    marker = MarkerColorClass(float(measures["hue"]), SINGLE_SAMPLE_HUE_TOLERANCE, 0.0, 0.0, 1)
    return PlayerVisualProfile(marker, measures["foot_lab"], measures["center_lab"], PLAYER_LAB_TOLERANCE,
                               layout_signature, datetime.now().astimezone().isoformat(timespec="seconds"),
                               1, "human_confirmed", int(cell_id))


def _hue_class(hues: list[float]) -> MarkerColorClass | None:
    if not hues:
        return None
    radians = [math.radians(hue * 2.0) for hue in hues]
    mean = (math.degrees(math.atan2(sum(map(math.sin, radians)), sum(map(math.cos, radians)))) % 360.0) / 2.0
    deltas = [min(abs(hue - mean) % 180.0, 180.0 - abs(hue - mean) % 180.0) for hue in hues]
    spread = math.sqrt(sum(delta ** 2 for delta in deltas) / len(deltas))
    tolerance = max(MIN_HUE_TOLERANCE, 3.0 * spread) if len(hues) > 1 else SINGLE_SAMPLE_HUE_TOLERANCE
    return MarkerColorClass(mean, tolerance, 0.0, 0.0, len(hues))


def frame_pixels(image, maps) -> tuple[np.ndarray, np.ndarray]:
    """HSV (N, 3) et chroma Lab (N,) des pixels de ``maps.box``, à plat."""
    x0, y0, x1, y1 = maps.box
    crop = np.ascontiguousarray(image[y0:y1, x0:x1])
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3).astype(np.float32)
    return hsv, np.hypot(lab[:, 1] - 128.0, lab[:, 2] - 128.0)


def cell_pixels(roi, index: int) -> np.ndarray:
    return roi.pos[roi.cell == index]


def stroke_hues(hsv: np.ndarray, chroma: np.ndarray, maps, index: int, stroke_delta: float) -> np.ndarray:
    """Teintes des pixels du trait (chroma de l'anneau nettement au-dessus de l'extérieur)."""
    ring, outer = cell_pixels(maps.partial_ring, index), cell_pixels(maps.partial_outer, index)
    if not len(ring) or not len(outer):
        return np.zeros(0)
    return hsv[ring[chroma[ring] >= chroma[outer].mean() + stroke_delta], 0]


def hue_deviation(hues, centre: float) -> np.ndarray:
    delta = np.abs(np.asarray(hues, dtype=np.float64) - centre) % 180.0
    return np.minimum(delta, 180.0 - delta)


def circular_hue_mean(hues, weights=None) -> float:
    radians = np.radians(np.asarray(hues, dtype=np.float64) * 2.0)
    w = np.ones(len(radians)) if weights is None else np.asarray(weights, dtype=np.float64)
    return float((np.degrees(np.arctan2((w * np.sin(radians)).sum(), (w * np.cos(radians)).sum())) % 360.0) / 2.0)


def pixel_tolerance(deviations, weights=None) -> float | None:
    """p95 (pondéré) de l'écart de teinte des pixels + 1 (quantification de la teinte OpenCV)."""
    values = np.asarray(deviations, dtype=np.float64)
    if not len(values):
        return None
    w = np.ones(len(values)) if weights is None else np.asarray(weights, dtype=np.float64)
    order = np.argsort(values)
    cumulative = np.cumsum(w[order]) / max(float(w.sum()), 1e-9)
    return float(values[order][min(len(values) - 1, int(np.searchsorted(cumulative, 0.95)))] + 1.0)


def enrichment_hue(ring_hues: np.ndarray, ring_total: int, neighbours: list[tuple[np.ndarray, int]]) -> dict | None:
    """Teinte d'équipe là où l'anneau est masqué : la teinte la plus enrichie dans l'anneau.

    Densités de pixels saturés par teinte dans la bande de l'anneau et dans les bandes voisines
    (intérieur, extérieur) des cellules annotées. Excès = densité anneau − max(densités voisines) :
    un trait d'anneau est propre à sa bande, alors qu'un corps de sprite coloré déborde vers
    l'intérieur. Le meilleur bac (2 unités, ≥ 50 pixels) donne le centre ; teinte et tolérance
    pixel sont mesurées sur l'excès dans une fenêtre de ±15.
    """
    if not len(ring_hues) or not ring_total or not neighbours:
        return None
    units = np.arange(180, dtype=np.float64)
    ring_density = np.bincount(ring_hues.astype(int), minlength=180)[:180] / ring_total
    other = np.max([np.bincount(hues.astype(int), minlength=180)[:180] / max(total, 1)
                    for hues, total in neighbours], axis=0)
    excess = np.clip(ring_density - other, 0.0, None)
    ring_bins = np.bincount((ring_hues // 2).astype(int), minlength=90)[:90]
    bin_excess = excess.reshape(90, 2).sum(axis=1) * (ring_bins >= ENRICHMENT_MIN_PIXELS)
    best = int(np.argmax(bin_excess))
    if bin_excess[best] <= 0:
        return None
    window = hue_deviation(units, best * 2 + 1) <= ENRICHMENT_WINDOW
    centre = circular_hue_mean(units[window], excess[window])
    tolerance = pixel_tolerance(hue_deviation(units[window], centre), excess[window])
    return {"hue": centre, "pixel_tolerance": tolerance, "best_bin": best * 2 + 1,
            "excess_density": float(bin_excess[best]), "ring_pixels": int(ring_bins[best])}


def team_class_from_train(full_hues: list[float], stroke_pixels: np.ndarray, enrichment: dict | None,
                          cells: int) -> MarkerColorClass | None:
    """Classe d'équipe TRAIN : anneaux complets si assez nombreux, sinon enrichissement."""
    if len(full_hues) >= MIN_FULL_RING_SAMPLES or (full_hues and enrichment is None):
        base = _hue_class(full_hues)
        tolerance = pixel_tolerance(hue_deviation(stroke_pixels, base.hue)) if len(stroke_pixels) else None
        return MarkerColorClass(base.hue, base.hue_tolerance, 0.0, 0.0, base.samples, tolerance, False, "full_ring")
    if enrichment is not None and enrichment["pixel_tolerance"] is not None:
        tolerance = float(enrichment["pixel_tolerance"])
        return MarkerColorClass(float(enrichment["hue"]), max(MIN_HUE_TOLERANCE, tolerance), 0.0, 0.0, cells,
                                tolerance, False, "ring_enrichment")
    return None


def gate_partial(team: MarkerColorClass | None, positives: int, contrary: int) -> MarkerColorClass | None:
    """Autorise l'anneau partiel d'une équipe d'après TRAIN seulement (vérités, jamais TEST)."""
    if team is None or team.pixel_tolerance is None:
        return team
    allowed = positives >= PARTIAL_GATE_MIN_POSITIVES and contrary == 0
    return MarkerColorClass(team.hue, team.hue_tolerance, team.min_saturation, team.min_value, team.samples,
                            team.pixel_tolerance, allowed, team.method,
                            {"train_positives": int(positives), "train_contrary": int(contrary)})


def player_profile_v2(examples: list[tuple[str, tuple[float, ...]]], rejected, marker: MarkerColorClass,
                      *, layout_signature: str | None) -> PlayerVisualProfileV2:
    """Profil multi-exemples depuis des vérités PLAYER human_confirmed de TRAIN (jamais TEST).

    Prototypes = vecteurs (Lab pieds, Lab centre) des exemples acceptés, dédoublonnés (frames
    identiques) ; la dispersion est rapportée pour juger de la variabilité réelle du personnage.
    """
    unique: dict[str, tuple[float, ...]] = {}
    duplicates = 0
    for _source, vector in examples:
        key = hashlib.sha1(np.round(np.asarray(vector, dtype=np.float64), 1).tobytes()).hexdigest()
        if key in unique:
            duplicates += 1
        unique.setdefault(key, tuple(float(v) for v in vector))
    if not unique:
        raise ProfileError("Aucune vérité PLAYER exploitable dans TRAIN pour ce layout")
    prototypes = tuple(unique.values())
    matrix = np.asarray(prototypes)
    pairwise = [0.5 * (float(np.linalg.norm(a[:3] - b[:3])) + float(np.linalg.norm(a[3:] - b[3:])))
                for i, a in enumerate(matrix) for b in matrix[i + 1:]]
    dispersion = {"foot_std": float(np.linalg.norm(matrix[:, :3].std(axis=0))),
                  "center_std": float(np.linalg.norm(matrix[:, 3:].std(axis=0))),
                  "pairwise_median": float(np.median(pairwise)) if pairwise else 0.0,
                  "pairwise_max": float(max(pairwise)) if pairwise else 0.0}
    rejected = Counter(rejected)
    if duplicates:
        rejected["duplicate_prototype"] += duplicates
    return PlayerVisualProfileV2(marker, prototypes, PLAYER_DISTANCE_TOLERANCE_DEFAULT, PLAYER_MARGIN_DEFAULT,
                                 layout_signature, datetime.now().astimezone().isoformat(timespec="seconds"),
                                 len(examples), dict(rejected), "human_confirmed", dispersion)


def team_profile_from_hues(player_hues: list[float], enemy_hues: list[float], *,
                           layout_signature: str | None = None, source: str | None = None) -> TeamMarkerProfile:
    """Couleurs d'équipe à partir de teintes d'anneaux mesurées sur cellules annotées (TRAIN)."""
    player, enemy = _hue_class(player_hues), _hue_class(enemy_hues)
    if player and enemy and (player.hue_distance(enemy.hue) <= max(player.hue_tolerance, enemy.hue_tolerance)):
        raise ProfileError("Couleurs joueur et ennemi indiscernables : profil d'équipe refusé")
    return TeamMarkerProfile(player, enemy, layout_signature, "human_confirmed",
                             datetime.now().astimezone().isoformat(timespec="seconds"), source)


def _write(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return path


def profile_directory(root: Path) -> Path:
    return root / "entity_profiles"


def _digest(layout_signature: str | None) -> str:
    from combatbot.corpus.entity_split import layout_digest
    return layout_digest(layout_signature)


def save_player_profile(root: Path, profile_id: int | str, profile: PlayerVisualProfile) -> Path:
    return _write(profile_directory(root) / f"player_{profile_id}.json", profile.to_dict())


def load_player_profile(root: Path, profile_id: int | str):
    path = profile_directory(root) / f"player_{profile_id}.json"
    if not path.is_file():
        return None
    return player_profile_from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_train_player_profile(root: Path, profile: PlayerVisualProfileV2) -> Path:
    """Profil V2 appris sur TRAIN, un fichier par layout (jamais écrasé par un autre layout)."""
    return _write(profile_directory(root) / f"player_train_{_digest(profile.layout_signature)}.json",
                  profile.to_dict())


def load_train_player_profile(root: Path, layout_signature: str | None) -> PlayerVisualProfileV2 | None:
    if not layout_signature:
        return None
    path = profile_directory(root) / f"player_train_{_digest(layout_signature)}.json"
    if not path.is_file():
        return None
    return PlayerVisualProfileV2.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_team_profile(root: Path, profile: TeamMarkerProfile) -> Path:
    """Un fichier par layout : plusieurs dispositions coexistent sans se remplacer."""
    return _write(profile_directory(root) / f"team_markers_{_digest(profile.layout_signature)}.json",
                  profile.to_dict())


def load_team_profile(root: Path, layout_signature: str | None = None) -> TeamMarkerProfile | None:
    """Profil du layout demandé ; à défaut, ancien fichier unique ``team_markers.json`` (3B-5)."""
    candidates = ([profile_directory(root) / f"team_markers_{_digest(layout_signature)}.json"]
                  if layout_signature else [])
    candidates.append(profile_directory(root) / "team_markers.json")
    for path in candidates:
        if path.is_file():
            return TeamMarkerProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))
    return None
