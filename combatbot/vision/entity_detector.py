"""Détection d'entités par cellule projetée (LOT 3B-5), sans suivi temporel.

Cell ID connu → projection connue → sous-régions connues → preuves locales. Le signal principal
est l'anneau d'équipe au sol : dans la moitié basse, un pic de chroma Lab sur la bande de l'anneau
par rapport à la bande extérieure, réparti sur plusieurs secteurs. Le sprite n'est qu'une preuve
secondaire. Aucune couleur n'est supposée : l'équipe d'un anneau n'est décidée que par des
profils dérivés d'annotations humaines (``TeamMarkerProfile``) ou confirmés par l'utilisateur
(``PlayerVisualProfile``). Sans profil, une présence reste ``EntityKind.UNKNOWN``.

FREE reste très conservateur : il exige un modèle de fond prêt et cohérent (``background_model``).
Sans preuve suffisante, une cellule reste UNKNOWN.

LOT 3B-5B — anneau partiel (``PARTIAL_RING``) : quand le sprite masque une partie de l'anneau, la
chroma moyenne ne suffit plus. Par secteur (cercle complet), on mesure la fraction de pixels de la
teinte d'une équipe (tolérance pixel mesurée sur TRAIN) dans la bande de l'anneau, comparée aux
bandes intérieure et extérieure. Plusieurs secteurs cohérents et étalés sont exigés, et deux
contradictions l'interdisent (secteur rempli = aplat de couleur, teinte présente hors de l'anneau =
décor ou sprite coloré). Ce
chemin n'existe que pour une classe d'équipe dont ``partial_allowed`` a été accordé sur TRAIN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

import cv2
import numpy as np

from combatbot.vision.entity_geometry import LOWER_SECTORS, SECTORS, CellRoiMaps, build_roi_maps, grid_signature
from combatbot.vision.entity_models import (
    EntityDetectionResult, EntityEvidence, EntityKind, MarkerColorClass, VisualProfiles,
)


@dataclass(frozen=True)
class EntityDetectorConfig:
    """Seuils de chroma (unités Lab OpenCV), provisoires jusqu'à ``--entities`` sur VALIDATION.

    Mesure de conception (9 cellules, frames C011/C003) : pic anneau 26–65, décor/placement 0–3.
    """

    peak_presence: float = 15.0
    peak_weak: float = 8.0
    peak_full: float = 40.0
    sector_peak: float = 10.0
    min_lower_sectors: int = 2
    stroke_delta: float = 18.0
    # Case de placement pleine : creux intérieur 0 et centre uniforme (écart-type Lab 0,0) ;
    # anneaux mesurés : creux 10–54, écart-type 32–47. Une des deux preuves est exigée.
    inner_drop_min: float = 8.0
    center_std_min: float = 8.0
    player_profile_min: float = 0.55
    player_margin: float = 0.10
    # Anneau partiel (LOT 3B-5B), seuils mesurés sur TRAIN uniquement (deux layouts) :
    # S/V = p5 des pixels de trait ennemis ; secteur cohérent = fraction anneau ≥ 0,05 et
    # dépassant intérieur/extérieur de 0,03 ; ≥ 2 secteurs dont deux séparés d'au moins 2 pas.
    partial_min_saturation: float = 135.0
    partial_min_value: float = 105.0
    partial_sector_fraction: float = 0.05
    partial_sector_contrast: float = 0.03
    partial_min_sectors: int = 2
    partial_sector_fill_max: float = 0.77   # au-delà : aplat de couleur, pas un trait
    # Structure d'anneau (TRAIN, vrais ennemis partiels) : rapport anneau / max(intérieur,
    # extérieur) par secteur cohérent ≥ 7,1 (p5) ; teinte d'équipe hors anneau ≤ 0,023 (max).
    # Seuils à ~×2 de marge : un décor parsemé de la couleur d'équipe est une contradiction.
    partial_sector_ratio: float = 3.0
    partial_outside_max: float = 0.05


@dataclass
class DetectionContext:
    """Contexte de frame : état de grille 3B-3 et identité de projection."""

    grid_visible: bool | None = None
    grid_aligned: bool | None = None
    map_id: int | None = None
    layout_signature: str | None = None
    timestamp: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def grid_trusted(self) -> bool:
        return bool(self.grid_visible) and bool(self.grid_aligned)


def _circular_hue(sum_cos: float, sum_sin: float) -> float | None:
    if abs(sum_cos) < 1e-9 and abs(sum_sin) < 1e-9:
        return None
    return float((np.degrees(np.arctan2(sum_sin, sum_cos)) % 360.0) / 2.0)


def _mean(values: np.ndarray, roi, n: int) -> np.ndarray:
    return np.bincount(roi.cell, weights=values[roi.pos], minlength=n) / np.maximum(roi.counts, 1)


def _sector_fraction(member: np.ndarray, roi, n: int) -> np.ndarray:
    """``member`` : un booléen par pixel de ``roi`` (même ordre que ``roi.pos``)."""
    key = roi.cell * SECTORS + roi.sector
    hits = np.bincount(key, weights=member.astype(np.float64), minlength=n * SECTORS)
    count = np.bincount(key, minlength=n * SECTORS)
    return (hits / np.maximum(count, 1)).reshape(n, SECTORS)


def spread_sectors(mask: np.ndarray) -> np.ndarray:
    """Vrai si deux secteurs cohérents sont séparés d'au moins deux pas (pas un arc étroit)."""
    return np.any([np.any(mask & np.roll(mask, shift, axis=1), axis=1) for shift in (2, 3, 4)], axis=0)


def partial_ring_features(hsv: np.ndarray, maps: CellRoiMaps, team: MarkerColorClass,
                          config: "EntityDetectorConfig") -> dict[str, np.ndarray]:
    """Preuves d'anneau partiel pour une classe d'équipe, vectorisées sur toutes les cellules.

    ``hsv`` : pixels (N, 3) de ``maps.box`` à plat. La couleur seule ne suffit jamais : il faut
    des secteurs où la teinte est plus présente sur l'anneau que dedans et dehors, étalés.
    Seuls les pixels des trois bandes sont lus ; la teinte (entière, 0..179) passe par une table.
    """
    n = maps.size
    tolerance = team.pixel_tolerance if team.pixel_tolerance is not None else team.hue_tolerance
    delta = np.abs(np.arange(180, dtype=np.float64) - team.hue) % 180.0
    table = np.zeros(256, dtype=bool)
    table[:180] = np.minimum(delta, 180.0 - delta) <= tolerance
    s_min = max(config.partial_min_saturation, team.min_saturation)
    v_min = max(config.partial_min_value, team.min_value)

    def member(roi) -> np.ndarray:
        pixels = hsv[roi.pos]
        return table[pixels[:, 0].astype(np.intp)] & (pixels[:, 1] >= s_min) & (pixels[:, 2] >= v_min)

    ring_member, outer_member, inner_member = (member(roi) for roi in (
        maps.partial_ring, maps.partial_outer, maps.partial_inner))
    ring = _sector_fraction(ring_member, maps.partial_ring, n)
    outer = _sector_fraction(outer_member, maps.partial_outer, n)
    inner = _sector_fraction(inner_member, maps.partial_inner, n)
    neighbour = np.maximum(outer, inner)
    coherent = ((ring >= config.partial_sector_fraction)
                & (ring - neighbour >= config.partial_sector_contrast)
                & (ring >= config.partial_sector_ratio * neighbour))
    count = coherent.sum(axis=1)
    spread = spread_sectors(coherent)
    fill = ring.max(axis=1) if n else np.zeros(0)
    outside = np.maximum(
        np.bincount(maps.partial_outer.cell, weights=outer_member.astype(np.float64), minlength=n)
        / np.maximum(maps.partial_outer.counts, 1),
        np.bincount(maps.partial_inner.cell, weights=inner_member.astype(np.float64), minlength=n)
        / np.maximum(maps.partial_inner.counts, 1))
    contradiction = (fill > config.partial_sector_fill_max) | (outside >= config.partial_outside_max)
    fires = (count >= config.partial_min_sectors) & spread & ~contradiction
    contrast = np.where(coherent, ring - neighbour, 0.0).sum(axis=1) / np.maximum(count, 1)
    return {"coherent": coherent, "count": count, "spread": spread, "fill": fill, "outside": outside,
            "contradiction": contradiction, "fires": fires, "contrast": contrast}


class CellEntityDetector:
    def __init__(self, config: EntityDetectorConfig | None = None) -> None:
        self.config = config or EntityDetectorConfig()
        self._maps: CellRoiMaps | None = None
        self._signature: tuple | None = None

    def roi_maps(self, cells, shape: tuple[int, int]) -> CellRoiMaps:
        signature = grid_signature(cells, shape)
        if self._maps is None or signature != self._signature:
            self._maps = build_roi_maps(cells, shape)
            self._signature = signature
        return self._maps

    # ------------------------------------------------------------------ features
    def cell_features(self, image: np.ndarray, maps: CellRoiMaps) -> dict[str, np.ndarray]:
        """Statistiques vectorisées par cellule. Seuls les pixels des sous-régions sont convertis."""
        n = maps.size
        if not n:
            empty = np.zeros(0)
            return {key: empty for key in ("ring_chroma", "outer_chroma", "peak", "lower_sectors",
                                           "sum_cos", "sum_sin")} | {
                "foot_lab": np.zeros((0, 3)), "center_lab": np.zeros((0, 3)), "ground_lab": np.zeros((0, 3)),
                "hsv": np.zeros((0, 3))}
        x0, y0, x1, y1 = maps.box
        crop = np.ascontiguousarray(image[y0:y1, x0:x1])
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).reshape(-1, 3)  # uint8, lu seulement aux ROI
        hue = hsv[:, 0].astype(np.float32)
        chroma = np.hypot(lab[:, 1] - 128.0, lab[:, 2] - 128.0)

        ring, outer = maps.ring, maps.outer
        ring_chroma = _mean(chroma, ring, n)
        outer_chroma = _mean(chroma, outer, n)
        inner_chroma = _mean(chroma, maps.inner, n)
        peak = ring_chroma - outer_chroma
        # Texture du centre (écart-type Lab) : un sprite est hétérogène, une case pleine uniforme.
        center_sq = np.stack([_mean(lab[:, k] ** 2, maps.center, n) for k in range(3)], axis=1)
        center_mean = np.stack([_mean(lab[:, k], maps.center, n) for k in range(3)], axis=1)
        center_std = np.sqrt(np.clip(center_sq - center_mean ** 2, 0.0, None).sum(axis=1))
        sector_sum = np.bincount(ring.cell * SECTORS + ring.sector, weights=chroma[ring.pos],
                                 minlength=n * SECTORS).reshape(n, SECTORS)
        sector_mean = sector_sum / np.maximum(maps.sector_counts, 1)
        sector_peak = sector_mean - outer_chroma[:, None]
        covered = (sector_peak >= self.config.sector_peak) & (maps.sector_counts > 0)
        lower_sectors = covered[:, list(LOWER_SECTORS)].sum(axis=1)
        # Pixels du trait : chroma nettement au-dessus du fond extérieur de la même cellule.
        stroke = chroma[ring.pos] >= outer_chroma[ring.cell] + self.config.stroke_delta
        radians = np.radians(hue[ring.pos][stroke] * 2.0)
        sum_cos = np.bincount(ring.cell[stroke], weights=np.cos(radians), minlength=n)
        sum_sin = np.bincount(ring.cell[stroke], weights=np.sin(radians), minlength=n)

        def lab_mean(roi) -> np.ndarray:
            return np.stack([_mean(lab[:, k], roi, n) for k in range(3)], axis=1)

        return {"ring_chroma": ring_chroma, "outer_chroma": outer_chroma, "inner_chroma": inner_chroma,
                "inner_drop": ring_chroma - inner_chroma, "center_std": center_std, "peak": peak,
                "lower_sectors": lower_sectors, "sum_cos": sum_cos, "sum_sin": sum_sin,
                "foot_lab": lab_mean(maps.foot), "center_lab": center_mean,
                "ground_lab": lab_mean(maps.background), "hsv": hsv}

    # ------------------------------------------------------------------ decision
    def detect(self, image: np.ndarray, projected_grid, visual_profiles: VisualProfiles | None = None,
               context: DetectionContext | None = None, background=None) -> EntityDetectionResult:
        started = time.perf_counter()
        profiles = visual_profiles or VisualProfiles()
        context = context or DetectionContext()
        config = self.config
        maps = self.roi_maps(projected_grid.cells, image.shape[:2])
        built = time.perf_counter()
        features = self.cell_features(image, maps)
        measured = time.perf_counter()

        peak = features["peak"]
        coverage = np.minimum(features["lower_sectors"] / len(LOWER_SECTORS), 1.0)
        marker = np.clip(peak / config.peak_full, 0.0, 1.0) * (0.5 + 0.5 * coverage)
        visibility = maps.visibility * (1.0 if context.grid_trusted else 0.6)

        player_profile = profiles.player_v2() if profiles.player and profiles.player.compatible(
            context.layout_signature) else None
        teams = profiles.teams if profiles.teams and profiles.teams.compatible(context.layout_signature) else None
        player_team: MarkerColorClass | None = (teams.player_team if teams and teams.player_team
                                                else player_profile.marker if player_profile else None)
        enemy_team = teams.enemy_team if teams else None
        diagnostics: dict[str, object] = {
            "player_profile": "applied" if player_profile else (
                "incompatible_layout" if profiles.player else "absent"),
            "team_profile": "applied" if teams else ("incompatible_layout" if profiles.teams else "absent"),
            "cells_analysed": maps.size,
        }
        # Anneau partiel : seulement grille fiable + profil d'équipe du layout + autorisation TRAIN.
        partial: dict[str, dict[str, np.ndarray]] = {}
        if teams is not None and context.grid_trusted:
            for name, team in (("enemy", enemy_team), ("player", teams.player_team)):
                if team is not None and team.partial_allowed:
                    partial[name] = partial_ring_features(features["hsv"], maps, team, config)
        diagnostics["partial_ring"] = sorted(partial) if partial else (
            "grid_untrusted" if teams is not None and not context.grid_trusted else "not_allowed")

        per_cell: dict[int, dict[str, float]] = {}
        candidates: list[dict[str, object]] = []
        occupancy: dict[int, str] = {}
        if background is not None:
            background.use_context((context.map_id, self._signature))
        cells_by_id = {cell.cell_id: cell for cell in projected_grid.cells if cell.cell_id is not None}
        for index, cell_id in enumerate(maps.cell_ids.tolist()):
            cell_peak = float(peak[index])
            hue = _circular_hue(float(features["sum_cos"][index]), float(features["sum_sin"][index]))
            stats = {"marker_score": float(marker[index]), "peak": cell_peak,
                     "ring_chroma": float(features["ring_chroma"][index]),
                     "outer_chroma": float(features["outer_chroma"][index]),
                     "inner_drop": float(features["inner_drop"][index]),
                     "center_std": float(features["center_std"][index]),
                     "lower_sectors": float(features["lower_sectors"][index]),
                     "visibility": float(visibility[index])}
            if hue is not None:
                stats["marker_hue"] = hue
            state = "UNKNOWN"
            if cell_peak >= config.peak_weak:
                per_cell[cell_id] = stats
            structured = (features["inner_drop"][index] >= config.inner_drop_min
                          or features["center_std"][index] >= config.center_std_min)
            present = (cell_peak >= config.peak_presence
                       and features["lower_sectors"][index] >= config.min_lower_sectors
                       and structured and visibility[index] > 0.5)
            partial_teams = [name for name, values in partial.items() if values["fires"][index]]
            if present:
                candidates.append({"index": index, "cell_id": cell_id, "score": float(marker[index]),
                                   "hue": hue, "stats": stats, "state": "FULL_RING"})
            elif (len(partial_teams) == 1 and visibility[index] > 0.5
                  and features["center_std"][index] >= config.center_std_min):
                # Un seul camp compatible (deux teintes d'équipe = contradiction) et un centre
                # hétérogène (sprite) : la couleur seule ne fait jamais une entité.
                name = partial_teams[0]
                values = partial[name]
                team = enemy_team if name == "enemy" else teams.player_team  # type: ignore[union-attr]
                count = int(values["count"][index])
                stats = {**stats, "partial_sectors": float(count),
                         "partial_contrast": float(values["contrast"][index]),
                         "partial_fill": float(values["fill"][index]),
                         "partial_outside": float(values["outside"][index])}
                per_cell[cell_id] = stats
                candidates.append({"index": index, "cell_id": cell_id, "state": "PARTIAL_RING",
                                   # Secteurs cohérents rapportés aux 4 secteurs bas qu'un anneau non masqué montre.
                                   "score": float(min(1.0, count / len(LOWER_SECTORS))),
                                   "hue": team.hue, "stats": stats, "partial_team": name})  # type: ignore[union-attr]
            elif (cell_peak < config.peak_weak and not partial_teams and background is not None
                  # 3B-5E : un sprite (centre hétérogène) sans anneau visible reste UNKNOWN, jamais FREE
                  # (TRAIN : 98–100 % des vérités joueur/ennemi ont center_std ≥ 8, les cases vides ~0).
                  and features["center_std"][index] < config.center_std_min):
                if background.is_free(cell_id, features["ground_lab"][index], cells_by_id.get(cell_id), context):
                    state = "FREE"
            occupancy[cell_id] = state

        entities: list[EntityEvidence] = []
        player_candidates: list[tuple[dict, float]] = []
        for candidate in candidates:
            hue = candidate["hue"]
            stats = candidate["stats"]
            if candidate["state"] == "PARTIAL_RING":
                reasons = [f"anneau partiel : {int(stats['partial_sectors'])} secteurs cohérents et étalés",
                           f"contraste de teinte {stats['partial_contrast']:.2f}"]
                if candidate["partial_team"] == "enemy":
                    entities.append(self._evidence(candidate, EntityKind.ENEMY, 1.0, 0.0,
                                                   reasons + ["teinte d'équipe ennemie (profil TRAIN)"], maps))
                else:
                    player_candidates.append((candidate, 1.0))
                continue
            reasons = [f"anneau pic chroma {stats['peak']:.0f}", f"secteurs bas {int(stats['lower_sectors'])}/4"]
            if enemy_team is not None and enemy_team.matches(hue):  # type: ignore[arg-type]
                color = 1.0 - enemy_team.hue_distance(hue) / max(enemy_team.hue_tolerance, 1e-6)  # type: ignore[arg-type]
                entities.append(self._evidence(candidate, EntityKind.ENEMY, color, 0.0,
                                               reasons + ["couleur d'équipe ennemie (profil humain)"], maps))
            elif player_team is not None and player_team.matches(hue):  # type: ignore[arg-type]
                color = 1.0 - player_team.hue_distance(hue) / max(player_team.hue_tolerance, 1e-6)  # type: ignore[arg-type]
                player_candidates.append((candidate, color))
            else:
                entities.append(self._evidence(candidate, EntityKind.UNKNOWN, 0.0, 0.0,
                                               reasons + ["équipe inconnue : aucun profil ne correspond"], maps))

        # Joueur : marqueur de son équipe + proche d'un prototype confirmé + marge sur les autres
        # candidats de la même équipe (un allié proche du profil rend la décision UNKNOWN).
        player_choice = None
        if player_candidates and player_profile is not None:
            scored = []
            for candidate, color in player_candidates:
                index = int(candidate["index"])  # type: ignore[arg-type]
                vector = np.concatenate([features["foot_lab"][index], features["center_lab"][index]])
                scored.append((player_profile.distance(vector), candidate, color, vector))
            scored.sort(key=lambda item: item[0])
            best = scored[0][0]
            second = scored[1][0] if len(scored) > 1 else float("inf")
            if best <= player_profile.distance_tolerance and second - best >= player_profile.margin:
                player_choice = (player_profile.similarity(scored[0][3]), scored[0][1])
            diagnostics["player_candidates"] = [(int(c["cell_id"]), round(d, 2)) for d, c, _, _ in scored]
            diagnostics["player_decision"] = ("selected" if player_choice else
                                              "too_far" if best > player_profile.distance_tolerance else "ambiguous")
        for candidate, color in player_candidates:
            stats = candidate["stats"]
            reasons = ([f"anneau partiel {int(stats['partial_sectors'])} secteurs"]
                       if candidate["state"] == "PARTIAL_RING" else [f"anneau pic chroma {stats['peak']:.0f}"])
            reasons.append("couleur d'équipe du joueur")
            if player_choice is not None and candidate is player_choice[1]:
                entities.append(self._evidence(candidate, EntityKind.PLAYER, color, player_choice[0],
                                               reasons + [f"profil joueur confirmé {player_choice[0]:.2f}"], maps))
            else:
                # Allié, ou joueur sans profil suffisant : présence réelle, identité inconnue.
                entities.append(self._evidence(candidate, EntityKind.UNKNOWN, color, 0.0,
                                               reasons + ["joueur non confirmé (profil absent ou ambigu)"], maps))
        for entity in entities:
            occupancy[entity.cell_id] = "OCCUPIED"
        # LOT 3B-5D : cellules où un sprite est présent (centre hétérogène), quelle que soit la
        # visibilité de l'anneau ; le suivi s'en sert pour maintenir une entité masquée.
        diagnostics["sprite_cells"] = [
            int(cell_id) for index, cell_id in enumerate(maps.cell_ids.tolist())
            if features["center_std"][index] >= config.center_std_min and visibility[index] > 0.5]
        if background is not None:
            # Après décision : une frame ne valide jamais son propre FREE. Toute preuve, même
            # faible (pic ≥ seuil faible), interdit d'apprendre le fond de cette cellule.
            key = (context.map_id, self._signature)
            for index, cell_id in enumerate(maps.cell_ids.tolist()):
                background.learn(key, cell_id, features["ground_lab"][index], cells_by_id.get(cell_id), context,
                                 entity_evidence=bool(peak[index] >= config.peak_weak
                                                      or features["center_std"][index] >= config.center_std_min
                                                      or occupancy.get(cell_id) == "OCCUPIED"
                                                      or any(v["count"][index] > 0 for v in partial.values())))
        finished = time.perf_counter()
        return EntityDetectionResult(
            tuple(sorted(entities, key=lambda item: item.cell_id)), per_cell, occupancy,
            {"roi_maps": (built - started) * 1000, "features": (measured - built) * 1000,
             "decision": (finished - measured) * 1000, "total": (finished - started) * 1000},
            diagnostics,
        )

    def _evidence(self, candidate: dict, kind: EntityKind, color_score: float, profile_score: float,
                  reasons: list[str], maps: CellRoiMaps) -> EntityEvidence:
        stats = candidate["stats"]
        index = int(candidate["index"])
        marker = float(candidate["score"])
        partial = candidate.get("state") == "PARTIAL_RING"
        shape = (float(stats["partial_sectors"]) / SECTORS if partial
                 else float(stats["lower_sectors"]) / len(LOWER_SECTORS))
        # Séparation du fond : contraste de teinte anneau − voisinage, 1,0 au double du seuil minimal.
        background = float(min(1.0, stats["partial_contrast"] / (2.0 * self.config.partial_sector_contrast)) if partial
                           else min(1.0, max(0.0, stats["peak"]) / max(self.config.peak_full, 1e-6)))
        visibility = float(stats["visibility"])
        # Confiance explicable : combinaison pondérée des scores bruts conservés dans la preuve.
        confidence = 0.45 * marker + 0.20 * background + 0.15 * shape + 0.10 * visibility
        confidence += 0.10 * (profile_score if kind is EntityKind.PLAYER else
                              max(0.0, color_score) if kind is EntityKind.ENEMY else 0.0)
        center = maps.centers[index]
        return EntityEvidence(int(candidate["cell_id"]), kind, float(min(1.0, confidence)), marker,
                              float(max(0.0, color_score)), shape, background, visibility, float(profile_score),
                              candidate["hue"], tuple(reasons), (int(center[0]), int(center[1])),
                              "PARTIAL_RING" if partial else "FULL_RING")


def measure_cell(image: np.ndarray, cell, detector: CellEntityDetector | None = None) -> dict[str, object]:
    """Mesures d'une seule cellule (profils joueur/équipe à partir d'une désignation humaine)."""
    detector = detector or CellEntityDetector()
    maps = build_roi_maps([cell], image.shape[:2], traversable_only=False)
    if not maps.size:
        return {}
    features = detector.cell_features(image, maps)
    return {"peak": float(features["peak"][0]), "lower_sectors": int(features["lower_sectors"][0]),
            "hue": _circular_hue(float(features["sum_cos"][0]), float(features["sum_sin"][0])),
            "foot_lab": tuple(float(v) for v in features["foot_lab"][0]),
            "center_lab": tuple(float(v) for v in features["center_lab"][0]),
            "ground_lab": tuple(float(v) for v in features["ground_lab"][0])}
