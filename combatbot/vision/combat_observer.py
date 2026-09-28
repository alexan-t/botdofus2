"""Observateur visuel réel en lecture seule du client DOFUS."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from uuid import uuid4

import cv2
import numpy as np

from combatbot.runtime import app_data_root
from combatbot import __version__
from combatbot.vision.capture import capture_client
from combatbot.vision.combat_grid import classify_cell_occupancy, infer_combat_grid
from combatbot.vision.background_model import CellBackgroundModel
from combatbot.vision.combat_models import (
    GRID_SOURCE_GAMEDATA, CellVisualState, CombatObservation, EnemyObservation, GridCalibration,
    ObservationPacket,
)
from combatbot.vision.entity_detector import CellEntityDetector, DetectionContext
from combatbot.vision.entity_models import EntityKind, TrackState, VisualProfiles
from combatbot.vision.entity_tracker import EntityTracker
from combatbot.vision.combat_ocr import NumberReader, read_small_number
from combatbot.vision.combat_tracker import CombatObservationTracker
from combatbot.vision.coordinates import CombatPoint, LayoutTransform
from combatbot.vision.gamedata_grid import GameDataGridResolver
from combatbot.vision.grid_fit import candidate_union
from combatbot.vision.grid_validation import (
    CombatStateDetector, DriftTracker, GridAlignmentValidator, GridVisibilityDetector, MapConsistencyTracker,
)
from combatbot.vision.models import Calibration, CapturedFrame
from combatbot.vision.hud_reader import (
    GlyphTemplateLibrary, HUDReader, NumberReadReason, NumberReadResult,
    NumberReadSource, NumberTemporalTracker,
)


FrameProvider = Callable[[], CapturedFrame]


def _load_profiles(calibration: Calibration) -> VisualProfiles:
    """Profils persistés (joueur par profil de calibration, équipes issues d'annotations)."""
    from combatbot.vision.entity_profiles import (
        load_player_profile, load_team_profile, load_train_player_profile,
    )
    root = app_data_root() / "data"
    from combatbot.entity_runtime import active_profile_directory
    try:
        directory = active_profile_directory(root)
    except (OSError, ValueError, KeyError):
        return VisualProfiles()
    try:
        # Profil multi-exemples TRAIN du layout courant d'abord ; sinon désignation unique.
        player = load_train_player_profile(root, calibration.layout_signature, directory=directory)
        if player is None and calibration.profile_id is not None:
            player = load_player_profile(root, calibration.profile_id)
    except (OSError, ValueError, KeyError):
        player = None
    try:
        teams = load_team_profile(root, calibration.layout_signature, directory=directory)
    except (OSError, ValueError, KeyError):
        teams = None
    return VisualProfiles(player if player and player.compatible(calibration.layout_signature) else None,
                          teams if teams and teams.compatible(calibration.layout_signature) else None)


def _zone(frame: CapturedFrame, calibration: Calibration, transform: LayoutTransform,
          name: str) -> np.ndarray | None:
    rect = calibration.zones.get(name)
    if rect is None:
        return None
    x, y, width, height = transform.normalized_rect_to_client(
        rect.to_normalized_rect()
    ).rounded()
    return frame.image[y:y + height, x:x + width].copy()


def _visual_activity(image: np.ndarray | None) -> float:
    if image is None or image.size == 0:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    texture = min(1.0, float(gray.std()) / 35)
    edges = min(1.0, float((cv2.Canny(gray, 45, 120) > 0).mean()) / 0.12)
    return 0.55 * texture + 0.45 * edges


class RealCombatObserver:
    """Produit des observations; ne dépend ni de Qt ni d'un exécuteur d'actions."""

    def __init__(self, hwnd: int, calibration: Calibration, *,
                 frame_provider: FrameProvider | None = None,
                 number_reader: NumberReader | None = None,
                 hud_reader: HUDReader | None = None,
                 grid_calibration: GridCalibration | None = None,
                 tracker: CombatObservationTracker | None = None,
                 capture_context: dict[str, object] | None = None,
                 grid_resolver: GameDataGridResolver | None = None,
                 overlay_options: "OverlayOptions | None" = None,
                 entity_detector: CellEntityDetector | None = None,
                 entity_tracker: EntityTracker | None = None,
                 entity_profiles: VisualProfiles | None = None,
                 background_model: CellBackgroundModel | None = None,
                 map_context=None, combat_state_model=None) -> None:
        self.hwnd = hwnd
        self.calibration = calibration
        self.frame_provider = frame_provider or (lambda: capture_client(hwnd, activate=False))
        self.number_reader = number_reader
        if hud_reader is not None:
            self.hud_reader = hud_reader
        elif number_reader is None:
            templates = GlyphTemplateLibrary.load(app_data_root() / "data" / "hud_templates")
            self.hud_reader = HUDReader(
                templates,
                rapidocr_reader=lambda image, minimum, maximum: read_small_number(image, minimum, maximum),
            )
        else:
            # Injection historique conservée pour les intégrations et tests existants.
            self.hud_reader = None
        self.ap_temporal = NumberTemporalTracker()
        self.mp_temporal = NumberTemporalTracker()
        self.grid_calibration = grid_calibration
        self.tracker = tracker or CombatObservationTracker()
        # Without resolver the historical pipeline is used unchanged.
        self.grid_resolver = grid_resolver
        if grid_resolver is not None and grid_resolver.legacy_calibration is None:
            grid_resolver.legacy_calibration = grid_calibration
        self.overlay_options = overlay_options or OverlayOptions()
        # LOT 3B-3 : preuves de grille, dérive, cohérence de map et état de combat.
        self.visibility_detector = GridVisibilityDetector()
        self.alignment_validator = GridAlignmentValidator()
        self.combat_state_detector = CombatStateDetector()
        self.map_consistency = MapConsistencyTracker()
        self.drift: DriftTracker | None = None
        self._validation_debug: dict[str, object] = {}
        self._last_visibility = None
        # LOT 3B-5 : entités par cellule projetée et suivi global (grille GameData uniquement).
        self.entity_detector = entity_detector or CellEntityDetector()
        self.entity_tracker = entity_tracker or EntityTracker()
        self.background_model = background_model or CellBackgroundModel()
        self.entity_profiles = entity_profiles if entity_profiles is not None else _load_profiles(calibration)
        self._entity_map: int | None = None
        self.capture_context = dict(capture_context or {})
        # LOT 3B-6C : MapContextService (détection automatique de map) ; None = map déclarée à la main.
        self.map_context = map_context
        # LOT 3B-6B : phase/tour appris sur TRAIN humain ; absent → ancienne heuristique de tour.
        from combatbot.vision.combat_state_detector import CombatStateModel, SemanticCombatStateTracker
        self.combat_state_model = combat_state_model if combat_state_model is not None else \
            CombatStateModel.load(app_data_root() / "data" / "combat_state_model")
        self.combat_state_tracker = SemanticCombatStateTracker()
        self.session_id = str(self.capture_context.get("session_id") or f"session_{uuid4().hex[:12]}")
        self._frame_index = 0
        self._last_numbers: tuple[float, tuple[int | None, float], tuple[int | None, float]] | None = None
        self._last_number_results: tuple[NumberReadResult, NumberReadResult] | None = None

    def observe(self) -> ObservationPacket:
        started = time.perf_counter()
        frame = self.frame_provider()
        if frame.hwnd != self.hwnd:
            raise RuntimeError("La capture ne correspond plus à la fenêtre observée")
        if not self.calibration.compatible(frame.client.width, frame.client.height):
            raise RuntimeError("Calibration incompatible avec la taille actuelle du client")
        combat_rect = self.calibration.zones.get("combat")
        if combat_rect is None:
            raise RuntimeError("La zone de combat doit être calibrée")
        transform = self.calibration.layout_transform(frame)
        combat_image = _zone(frame, self.calibration, transform, "combat")
        assert combat_image is not None
        resolution = None
        map_resolution = None
        zones = {name: rect.to_normalized_rect() for name, rect in self.calibration.zones.items()}
        if self.map_context is not None:
            # Met à jour l'identité de map AVANT la projection : la grille suit la map détectée.
            centers = self.grid_resolver.cell_centers(frame.client.size, zones) \
                if self.grid_resolver is not None else None
            map_resolution = self.map_context.update(frame.image, combat_image, cell_centers=centers)
        if self.grid_resolver is not None:
            resolution = self.grid_resolver.resolve(combat_image, frame.client.size, zones)
            grid = self._validate_grid(resolution, combat_image)
        else:
            grid = infer_combat_grid(combat_image, self.grid_calibration)
        now = time.monotonic()
        entity_fields: dict[str, object] = {}
        entity_timings: dict[str, float] = {}
        if grid.grid_source == GRID_SOURCE_GAMEDATA and self.entity_detector is not None:
            grid, player_cell, player_cell_id, player_confidence, enemies, entity_fields, entity_timings = \
                self._observe_entities(combat_image, grid, now)
        else:
            # Ancien pipeline (grille historique) conservé tant que le remplacement n'est pas mesuré.
            player_reference = self.grid_calibration.player_reference_hsv if self.grid_calibration else None
            grid, player_cell, player_confidence, raw_enemies = classify_cell_occupancy(
                combat_image, grid, player_reference,
            )
            cell_ids = {cell.logical: cell.cell_id for cell in grid.cells} \
                if grid.grid_source == GRID_SOURCE_GAMEDATA else {}
            enemies = tuple(EnemyObservation("", cell, center, confidence, cell_ids.get(cell))
                            for cell, center, confidence in raw_enemies)
            player_cell_id = cell_ids.get(player_cell) if player_cell is not None else None

        ap_image = _zone(frame, self.calibration, transform, "ap")
        mp_image = _zone(frame, self.calibration, transform, "mp")
        if self.hud_reader is not None:
            ap_raw = self.hud_reader.read(ap_image, "AP") if ap_image is not None else NumberReadResult(
                None, 0.0, NumberReadSource.UNKNOWN, reason=NumberReadReason.NO_GLYPH)
            mp_raw = self.hud_reader.read(mp_image, "MP") if mp_image is not None else NumberReadResult(
                None, 0.0, NumberReadSource.UNKNOWN, reason=NumberReadReason.NO_GLYPH)
            ap_read = self.ap_temporal.update(ap_raw)
            mp_read = self.mp_temporal.update(mp_raw)
            ap, confidence_ap = ap_read.value, ap_read.confidence
            mp, confidence_mp = mp_read.value, mp_read.confidence
        else:
            assert self.number_reader is not None
            if self._last_numbers is None or now - self._last_numbers[0] >= 0.55:
                ap_pair = self.number_reader(ap_image) if ap_image is not None else (None, 0.0)
                mp_pair = self.number_reader(mp_image) if mp_image is not None else (None, 0.0)
                self._last_numbers = (now, ap_pair, mp_pair)
                self._last_number_results = tuple(
                    NumberReadResult(value, confidence, NumberReadSource.RAPIDOCR if value is not None
                                     else NumberReadSource.UNKNOWN,
                                     reason=NumberReadReason.OCR_FALLBACK if value is not None
                                     else NumberReadReason.NO_GLYPH)
                    for value, confidence in (ap_pair, mp_pair)
                )  # type: ignore[assignment]
            _, (ap, confidence_ap), (mp, confidence_mp) = self._last_numbers
            assert self._last_number_results is not None
            ap_read, mp_read = self._last_number_results

        counter_signal = (_visual_activity(ap_image) + _visual_activity(mp_image)) / 2
        end_turn_image = _zone(frame, self.calibration, transform, "end_turn")
        spell_image = _zone(frame, self.calibration, transform, "spell_bar")
        end_signal = _visual_activity(end_turn_image)
        spell_signal = _visual_activity(spell_image)
        signals = {"grid": grid.confidence, "counters": counter_signal,
                   "end_turn": end_signal, "spell_bar": spell_signal}
        # LOT 3B-3 : les 560 cellules GameData ne prouvent rien ; plus aucun len(grid.cells).
        visibility = self._last_visibility if grid.grid_visibility is not None else None
        state = self.combat_state_detector.detect(
            grid_source=grid.grid_source, visibility=visibility, grid_confidence=grid.confidence,
            hud={"counters": counter_signal, "end_turn": end_signal, "spell_bar": spell_signal})
        combat_confidence = state.confidence
        combat_detected = state.combat_detected

        # Le bouton et les compteurs doivent tous deux être lisibles avant de conclure au tour.
        turn_score = 0.6 * end_signal + 0.4 * max(confidence_ap, confidence_mp)
        if not combat_detected or end_signal < 0.2:
            player_turn = None
        elif turn_score >= 0.52 and (ap is not None or mp is not None):
            player_turn = True
        elif turn_score <= 0.24:
            player_turn = False
        else:
            player_turn = None
        semantic = None
        if self.combat_state_model is not None:
            # LOT 3B-6B : le tour vient uniquement de la couleur du bouton fin de tour ; jamais de mémoire.
            from combatbot.vision.combat_state_detector import extract_features
            semantic = self.combat_state_tracker.update(
                self.combat_state_model.predict(extract_features(end_turn_image, frame.image)), now)
            fighting = semantic.phase.value == "FIGHTING"
            player_turn = {"PLAYER": True, "OTHER": False}.get(semantic.turn_owner.value) if fighting else None
            turn_score = semantic.confidence
        essential = [grid.confidence, combat_confidence]
        essential.extend(score for value, score in ((ap, confidence_ap), (mp, confidence_mp)) if value is not None)
        if player_cell is not None:
            essential.append(player_confidence)
        observation_confidence = float(sum(essential) / len(essential)) if essential else 0.0
        raw = CombatObservation(
            combat_detected, combat_confidence, player_turn, turn_score,
            player_cell, player_confidence, enemies, grid, ap, mp,
            confidence_ap, confidence_mp, observation_confidence,
            signals=signals, timestamp=time.time(),
            player_cell_id=player_cell_id,
            combat_state=state.state.value,
            ap_read=ap_read.to_dict(), mp_read=mp_read.to_dict(),
            **entity_fields,  # type: ignore[arg-type]
        )
        observation = self.tracker.update(raw)
        annotated = draw_diagnostic_overlay(combat_image, observation, self.overlay_options,
                                            validation=self._validation_debug)
        elapsed_ms = (time.perf_counter() - started) * 1000
        phase = "inconnue"
        if semantic is not None:
            phase = semantic.label
        elif observation.result is not None:
            phase = "fin de combat"
        elif observation.combat_detected and observation.player_turn is True:
            phase = "mon tour"
        elif observation.combat_detected and observation.player_turn is False:
            phase = "tour ennemi"
        elif observation.combat_detected:
            phase = "combat"
        elif observation.combat_confidence < 0.2:
            phase = "hors combat"
        metadata = {
            "pythonbot_version": __version__,
            "profile": self.capture_context.get("profile"),
            "client_size": [frame.client.width, frame.client.height],
            "dpi": self.capture_context.get("dpi"),
            "window_id": self.capture_context.get("window_id", f"dofus-{self.hwnd:x}"),
            "calibration": {
                "profile_id": self.calibration.profile_id,
                "client_size": [self.calibration.client_width, self.calibration.client_height],
                "layout_signature": self.calibration.layout_signature,
                "zones": {name: rect.to_dict() for name, rect in self.calibration.zones.items()},
            },
            "layout_transform": transform.to_dict(),
            "coordinate_spaces": {
                "capture": "client", "frame": "combat", "grid": "combat", "hud": "client",
            },
            "tactical_mode": self.capture_context.get("tactical_mode", "inconnu"),
            "entity_split_declared": self.capture_context.get("entity_split_declared"),
            "grid_source": grid.grid_source,
            "grid_source_reason": resolution.reason if resolution else "LEGACY_PIPELINE",
            "map_id_declared": grid.map_id_declared,
            "map_id_origin": "DECLARED_MANUALLY" if grid.map_id_declared is not None else None,
            "map_id_source": grid.map_id_source,
            "grid_profile_version": grid.grid_profile_version,
            "projection_confidence": grid.projection_confidence,
            "projection_status": grid.projection_status,
            "topology_consistency": grid.topology_consistency,
            "declared_map_suspect": grid.declared_map_suspect,
            "grid_visibility_state": grid.grid_visibility_state,
            "alignment_status": (grid.alignment or {}).get("status"),
            "drift_state": (grid.drift or {}).get("state"),
            "runtime_adjustment": (grid.drift or {}).get("runtime_adjustment"),
            "map_declaration_state": grid.map_declaration_state,
            "combat_state": observation.combat_state,
            "requires_recalibration": bool(resolution and resolution.requires_recalibration),
            "phase": phase,
            "semantic_combat_state": semantic.to_dict() if semantic is not None else None,
            "analysis_ms": elapsed_ms,
            "global_confidence": observation.observation_confidence,
            "entities": {"pipeline": "CELL_ENTITY_DETECTOR" if entity_timings else "LEGACY_CLASSIFY",
                         **entity_timings},
            "hud_reader": {
                "ap": observation.ap_read, "mp": observation.mp_read,
                "specialized_ap_ms": ap_read.timings_ms.get("specialized"),
                "specialized_mp_ms": mp_read.timings_ms.get("specialized"),
                "rapidocr_ap_ms": ap_read.timings_ms.get("rapidocr"),
                "rapidocr_mp_ms": mp_read.timings_ms.get("rapidocr"),
            },
            "map_resolution": ({**map_resolution.to_dict(),
                                "game_data_loaded": grid.grid_source == GRID_SOURCE_GAMEDATA
                                and grid.map_id_declared == map_resolution.map_id,
                                "calibration_status": ("REUSED" if resolution and resolution.status
                                                       and resolution.status.applicable else
                                                       (resolution.reason if resolution else None)),
                                "grid_status": (grid.alignment or {}).get("status"),
                                "timings_ms": dict(self.map_context.timings)}
                               if map_resolution is not None else None),
            "capture_source": frame.source,
            "session_id": self.session_id,
            "frame_index": self._frame_index,
        }
        self._frame_index += 1
        # LOT 3B-6B : bouton fin de tour, barre de sorts et client entier gardés pour la phase/tour.
        hud_crops = {name: value for name, value in (("ap", ap_image), ("mp", mp_image),
                                                     ("end_turn", end_turn_image), ("spell_bar", spell_image),
                                                     ("client", frame.image))
                     if value is not None and value.size > 0}
        return ObservationPacket(observation, combat_image, annotated, elapsed_ms, metadata, hud_crops)

    def _observe_entities(self, combat_image: np.ndarray, grid, now: float):
        """Détecteur par cellule + suivi global ; l'identité est toujours le DofusCellId."""
        from dataclasses import replace as _replace
        if grid.map_id_declared != self._entity_map:
            # Nouvelle map : anciennes pistes et ancien fond n'ont plus de sens.
            self._entity_map = grid.map_id_declared
            self.entity_tracker = EntityTracker(self.entity_tracker.config)
            self.background_model.reset()
        context = DetectionContext(
            grid_visible=grid.grid_visibility_state == "VISIBLE" if grid.grid_visibility_state else None,
            grid_aligned=(grid.alignment or {}).get("status") == "ALIGNED" if grid.alignment else None,
            map_id=grid.map_id_declared, layout_signature=self.calibration.layout_signature, timestamp=now,
            player_prior_cell=self.entity_tracker.player_prior())
        detection = self.entity_detector.detect(combat_image, grid, self.entity_profiles, context,
                                                self.background_model)
        started = time.perf_counter()
        tracked = self.entity_tracker.update(detection, now)
        tracker_ms = (time.perf_counter() - started) * 1000
        by_id = {cell.cell_id: cell for cell in grid.cells if cell.cell_id is not None}
        confidence_by_cell = {item.cell_id: item.confidence for item in detection.entities}
        # Une entité maintenue (HELD) occupe sa cellule : jamais FREE.
        occupancy = {**detection.occupancy, **{item.claimed_cell: "OCCUPIED" for item in tracked
                                              if item.state is TrackState.HELD}}
        cells = tuple(
            _replace(cell, state=CellVisualState(occupancy.get(cell.cell_id, "UNKNOWN")),
                     confidence=confidence_by_cell.get(cell.cell_id, 0.0))
            for cell in grid.cells)
        grid = _replace(grid, cells=cells)
        player_cell = player_cell_id = None
        player_confidence = 0.0
        player_track = next((item for item in tracked if item.kind is EntityKind.PLAYER), None)
        if player_track is not None and player_track.claimed_cell in by_id:
            player_cell_id = player_track.claimed_cell
            player_cell = by_id[player_cell_id].logical
            player_confidence = player_track.confidence
        enemies = []
        for item in tracked:
            if item.kind is not EntityKind.ENEMY or item.state is TrackState.LOST:
                continue
            cell = by_id.get(item.cell_id if item.observed_this_frame else item.last_known_cell_id)
            if cell is None:
                continue
            evidence = item.evidence
            enemies.append(EnemyObservation(
                item.track_id, cell.logical,
                evidence.center if evidence and evidence.center else cell.center,
                item.confidence, cell.cell_id, item.state.value, item.observed_this_frame,
                evidence.marker_score if evidence else None,
                {"marker_hue": evidence.marker_hue} if evidence else None,
                evidence.to_dict() if evidence else None))
        unknown = tuple({"track_id": None, "kind": "UNKNOWN", "cell_id": item.cell_id,
                         "confidence": item.confidence, "state": "OBSERVED", "observed_this_frame": True}
                        for item in detection.entities if item.kind is EntityKind.UNKNOWN)
        summary = detection.occupancy_summary()
        summary["NOT_ANALYSED"] = len(grid.cells) - len(detection.occupancy)
        fields = {
            "entities": tuple(item.to_dict() for item in tracked if item.state is not TrackState.LOST) + unknown,
            "player_track": player_track.to_dict() if player_track else None,
            "entity_evidence": tuple(item.to_dict() for item in detection.entities),
            "occupancy_summary": summary,
        }
        timings = {"detector_ms": detection.timings_ms.get("total", 0.0), "tracker_ms": tracker_ms,
                   "roi_maps_ms": detection.timings_ms.get("roi_maps", 0.0),
                   "player_profile": detection.diagnostics.get("player_profile"),
                   "team_profile": detection.diagnostics.get("team_profile")}
        return grid, player_cell, player_cell_id, player_confidence, tuple(enemies), fields, timings

    def designate_player_cell(self, pixel: CombatPoint | tuple[int, int], packet: ObservationPacket):
        """« Cette cellule est mon personnage » : crée un PlayerVisualProfile confirmé.

        La cellule doit porter un anneau mesurable ; aucun clic n'est envoyé au client DOFUS.
        """
        from combatbot.vision.entity_profiles import ProfileError, player_profile_from_cell
        grid = packet.observation.grid
        if grid.grid_source != GRID_SOURCE_GAMEDATA:
            raise ProfileError("Grille GameData projetée requise pour désigner une cellule")
        cell_id = grid.pixel_to_cell_id(pixel)
        if cell_id is None:
            raise ProfileError("Aucune cellule projetée à cet endroit")
        profile = player_profile_from_cell(np.asarray(packet.original), grid, cell_id,
                                           layout_signature=self.calibration.layout_signature,
                                           detector=self.entity_detector)
        self.entity_profiles = VisualProfiles(profile, self.entity_profiles.teams if self.entity_profiles else None)
        return profile

    def _validate_grid(self, resolution, combat_image: np.ndarray):
        """Visibility → alignment → temporal drift → map consistency (GAMEDATA_PROJECTED only)."""
        from dataclasses import replace as _replace
        grid = resolution.grid
        self._validation_debug = {}
        self._last_visibility = None
        if resolution.projected is None or resolution.status is None or resolution.status.transform is None:
            return grid
        base = resolution.status.transform
        if self.drift is None:
            self.drift = DriftTracker(base)
        else:
            self.drift.rebase(base)
        size = (combat_image.shape[1], combat_image.shape[0])
        candidates = candidate_union(combat_image)
        visibility, inliers, outliers = self.visibility_detector.observe(
            resolution.projected, candidates, size, grid.topology_consistency)
        self._last_visibility = visibility
        alignment = self.alignment_validator.validate(resolution.projected, visibility, inliers, outliers)
        self.drift.update(alignment)
        # The new adjustment applies from the next frame on; the profile is never touched.
        self.grid_resolver.runtime_adjustment = self.drift.adjustment
        map_state = self.map_consistency.update(grid.map_id_declared, grid.map_id_source, visibility.state,
                                                grid.topology_consistency)
        self._validation_debug = {"base_transform": base, "effective_transform": resolution.projected.transform,
                                  "inliers": alignment.inlier_points or tuple(m.candidate for m in inliers),
                                  "outliers": alignment.outlier_points or tuple(m.candidate for m in outliers),
                                  "correction": alignment.suggested_correction,
                                  "supported_regions": visibility.supported_regions,
                                  "applied": self.drift.adjustment}
        return _replace(grid, grid_visibility=visibility.to_dict(), alignment=alignment.to_dict(),
                        drift=self.drift.to_dict(), map_declaration=map_state.to_dict())

    def set_player_reference(self, pixel: CombatPoint | tuple[int, int], packet: ObservationPacket) -> bool:
        """Mémorise la couleur au pixel explicitement désigné par l'utilisateur."""
        image = np.asarray(packet.original)
        point = pixel if isinstance(pixel, CombatPoint) else CombatPoint(*pixel)
        x, y = point.rounded()
        if not (0 <= x < image.shape[1] and 0 <= y < image.shape[0]):
            return False
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        y0, y1 = max(0, y - 5), min(hsv.shape[0], y + 6)
        x0, x1 = max(0, x - 5), min(hsv.shape[1], x + 6)
        pixels = hsv[y0:y1, x0:x1].reshape(-1, 3)
        saturated = pixels[pixels[:, 1] >= 65]
        if len(saturated) < 5:
            return False
        reference = tuple(float(value) for value in np.median(saturated, axis=0))
        current = self.grid_calibration
        grid = packet.observation.grid
        if current is None and grid.grid_source == GRID_SOURCE_GAMEDATA:
            # Reference holder only: no logical cells, so it never defines a legacy grid.
            current = GridCalibration((0.0, 0.0), grid.cell_width or 1.0, grid.cell_height or 1.0, ())
        if current is None:
            if not grid.cells or grid.cell_width is None or grid.cell_height is None:
                return False
            anchor = min(grid.cells, key=lambda item: (item.logical.y, item.logical.x))
            current = GridCalibration(anchor.center, grid.cell_width, grid.cell_height,
                                      tuple(item.logical for item in grid.cells))
        self.grid_calibration = GridCalibration(current.origin, current.cell_width, current.cell_height,
                                                current.logical_cells, reference)
        if self.grid_resolver is not None:
            self.grid_resolver.legacy_calibration = self.grid_calibration
        return True


@dataclass(frozen=True)
class OverlayOptions:
    """Couches de l'overlay de diagnostic ; les IDs sont sous-échantillonnés."""

    grid: bool = True
    cell_ids: bool = False
    coordinates: bool = False
    walkability: bool = False
    los: bool = False
    red_blue: bool = False
    candidates: bool = False
    anchors: bool = False
    alignment_debug: bool = False   # base vs effective, inliers/outliers, régions, correction
    # LOT 3B-5 : diagnostic des entités (aucune injection dans DOFUS).
    entity_rois: bool = False
    player_evidence: bool = False
    enemy_evidence: bool = False
    track_ids: bool = True
    occluded_tracks: bool = True
    background_delta: bool = False
    occupancy_states: bool = False
    label_every: int = 7


def _blend_cells(output: np.ndarray, polygons: list[np.ndarray], color: tuple[int, int, int], alpha: float) -> None:
    if not polygons:
        return
    layer = output.copy()
    cv2.fillPoly(layer, polygons, color)
    cv2.addWeighted(layer, alpha, output, 1 - alpha, 0, dst=output)


def draw_diagnostic_overlay(image: np.ndarray, observation: CombatObservation,
                            options: OverlayOptions | None = None, *,
                            candidates=(), anchors=(), validation: dict | None = None) -> np.ndarray:
    options = options or OverlayOptions()
    output = image.copy()
    grid = observation.grid
    projected = grid.grid_source == GRID_SOURCE_GAMEDATA
    polygon = lambda item: np.asarray(item.polygon, np.int32)  # noqa: E731
    if projected:
        if options.walkability:
            _blend_cells(output, [polygon(c) for c in grid.cells if c.static_traversable], (70, 160, 70), 0.22)
            _blend_cells(output, [polygon(c) for c in grid.cells if c.static_traversable is False], (40, 40, 40), 0.35)
        if options.los:
            _blend_cells(output, [polygon(c) for c in grid.cells if c.los_static is False], (160, 60, 160), 0.35)
        if options.red_blue:
            # GameData hints only: never presented as validated placement cells.
            _blend_cells(output, [polygon(c) for c in grid.cells if c.red_hint], (40, 40, 220), 0.4)
            _blend_cells(output, [polygon(c) for c in grid.cells if c.blue_hint], (220, 110, 40), 0.4)
        if options.grid:
            cv2.polylines(output, [polygon(c) for c in grid.cells], True, (80, 170, 230), 1, cv2.LINE_AA)
            occupied = [polygon(c) for c in grid.cells if c.state.value == "OCCUPIED"]
            cv2.polylines(output, occupied, True, (40, 190, 245), 2, cv2.LINE_AA)
        step = max(1, options.label_every)
        for item in grid.cells:
            if item.cell_id is None or item.cell_id % step:
                continue
            text = []
            if options.cell_ids:
                text.append(str(item.cell_id))
            if options.coordinates and item.grid_coordinate is not None:
                text.append(f"{item.grid_coordinate.x},{item.grid_coordinate.y}")
            if text:
                cv2.putText(output, " ".join(text), (item.center[0] - 12, item.center[1] + 3),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.3, (240, 240, 240), 1, cv2.LINE_AA)
    else:
        for item in grid.cells:
            color = (80, 170, 230)
            if item.state.value == "OCCUPIED":
                color = (40, 190, 245)
            cv2.polylines(output, [np.asarray(item.polygon, np.int32)], True, color, 1, cv2.LINE_AA)
            cv2.putText(output, f"{item.logical.x},{item.logical.y}",
                        (item.center[0] - 13, item.center[1] + 3), cv2.FONT_HERSHEY_SIMPLEX,
                        0.3, color, 1, cv2.LINE_AA)
    if options.alignment_debug and validation:
        _draw_alignment_debug(output, grid, validation)
    if options.candidates:
        for candidate in candidates:
            cv2.circle(output, (round(candidate[0]), round(candidate[1])), 3, (0, 220, 255), -1)
    if options.anchors:
        for cell_id, point in anchors:
            cv2.drawMarker(output, (round(point[0]), round(point[1])), (255, 0, 255), cv2.MARKER_CROSS, 12, 2)
            cv2.putText(output, str(cell_id), (round(point[0]) + 6, round(point[1]) - 6),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 0, 255), 1, cv2.LINE_AA)
    if observation.entities is not None:
        _draw_entities(output, observation, options)
        return output
    if observation.player_cell is not None:
        item = grid.cell_at(observation.player_cell)
        if item:
            cv2.circle(output, item.center, 10, (80, 235, 120), 3)
            cv2.putText(output, "JOUEUR", (item.center[0] + 8, item.center[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (80, 235, 120), 1, cv2.LINE_AA)
    for enemy in observation.enemies:
        cv2.circle(output, enemy.center, 9, (60, 70, 245), 3)
        cv2.putText(output, enemy.id, (enemy.center[0] + 7, enemy.center[1] + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (60, 70, 245), 1, cv2.LINE_AA)
    return output


def _short_track(track_id: str | None) -> str:
    if not track_id:
        return "?"
    return "P" if track_id == "player" else "E" + track_id.rsplit("_", 1)[-1]


def _draw_entities(output: np.ndarray, observation: CombatObservation, options: "OverlayOptions") -> None:
    """P @ 287 0.94 · E1 @ 301 0.88 · E2 OCCLUDED last=315 ; UNKNOWN reste distinct."""
    from combatbot.vision.entity_geometry import RING_BAND, cell_basis
    grid = observation.grid
    by_id = {cell.cell_id: cell for cell in grid.cells if cell.cell_id is not None}
    font = cv2.FONT_HERSHEY_SIMPLEX
    # FREE n'existe que si le modèle de fond l'a prouvé ; « Background delta » montre ces cellules.
    shown = ({"OCCUPIED": (40, 160, 245)} if options.occupancy_states else {}) | (
        {"FREE": (70, 190, 70)} if options.occupancy_states or options.background_delta else {})
    for state, color in shown.items():
        polygons = [np.asarray(c.polygon, np.int32) for c in grid.cells if c.state.value == state]
        _blend_cells(output, polygons, color, 0.30)
    if options.entity_rois:
        for evidence in observation.entity_evidence or ():
            cell = by_id.get(evidence.get("cell_id"))
            if cell is None:
                continue
            middle, u, v = cell_basis(cell.polygon, cell.center)
            for radius in RING_BAND:
                axes = (max(1, int(np.hypot(*u) * radius)), max(1, int(np.hypot(*v) * radius)))
                cv2.ellipse(output, (int(middle[0]), int(middle[1])), axes, 0, 0, 180, (255, 255, 0), 1, cv2.LINE_AA)
    for entity in observation.entities or ():
        kind, state = entity.get("kind"), entity.get("state")
        observed = bool(entity.get("observed_this_frame"))
        cell = by_id.get(entity.get("cell_id") if observed else entity.get("last_known_cell_id"))
        if cell is None:
            continue
        color = {"PLAYER": (80, 235, 120), "ENEMY": (60, 70, 245)}.get(str(kind), (200, 200, 200))
        if not observed:
            if not options.occluded_tracks:
                continue
            cv2.circle(output, cell.center, 11, (150, 150, 150), 1, cv2.LINE_AA)
            cv2.putText(output, f"{_short_track(entity.get('track_id'))} {state} last={cell.cell_id}",
                        (cell.center[0] + 10, cell.center[1] + 16), font, 0.38, (170, 170, 170), 1, cv2.LINE_AA)
            continue
        cv2.circle(output, cell.center, 10, color, 3 if kind != "UNKNOWN" else 1, cv2.LINE_AA)
        if options.track_ids:
            label = _short_track(entity.get("track_id")) if kind != "UNKNOWN" else "?"
            cv2.putText(output, f"{label} @ {cell.cell_id} {float(entity.get('confidence', 0.0)):.2f}",
                        (cell.center[0] + 10, cell.center[1] - 10), font, 0.42, color, 1, cv2.LINE_AA)
    wanted = {"PLAYER": options.player_evidence, "ENEMY": options.enemy_evidence}
    for evidence in observation.entity_evidence or ():
        if not wanted.get(str(evidence.get("kind"))):
            continue
        cell = by_id.get(evidence.get("cell_id"))
        if cell is None:
            continue
        text = (f"m{float(evidence.get('marker_score', 0)):.2f} c{float(evidence.get('color_score', 0)):.2f} "
                f"s{float(evidence.get('shape_score', 0)):.2f} p{float(evidence.get('profile_score', 0)):.2f}")
        cv2.putText(output, text, (cell.center[0] - 40, cell.center[1] + 28), font, 0.34, (255, 255, 255), 1,
                    cv2.LINE_AA)


def _draw_alignment_debug(output: np.ndarray, grid, validation: dict) -> None:
    """Grey +: base transform centres; green: inliers; red x: outliers; arrow: correction."""
    from combatbot.vision.grid_projection import GridProjector
    base = validation.get("base_transform")
    effective = validation.get("effective_transform")
    if base is not None and effective is not None and base != effective:
        height, width = output.shape[:2]
        for cell in GridProjector(base)._geometry.values():
            x, y = cell[1].rounded()
            if 0 <= x < width and 0 <= y < height:
                cv2.drawMarker(output, (x, y), (170, 170, 170), cv2.MARKER_CROSS, 6, 1)
    for x, y in validation.get("inliers", ()):
        cv2.circle(output, (round(x), round(y)), 3, (60, 220, 60), -1)
    for x, y in validation.get("outliers", ()):
        cv2.drawMarker(output, (round(x), round(y)), (40, 40, 230), cv2.MARKER_TILTED_CROSS, 8, 2)
    lines = [f"regions: {list(validation.get('supported_regions', ()))}"]
    correction, applied = validation.get("correction"), validation.get("applied")
    if correction is not None:
        centre = (output.shape[1] // 2, output.shape[0] // 2)
        tip = (round(centre[0] + correction.dx * 8), round(centre[1] + correction.dy * 8))
        cv2.arrowedLine(output, centre, tip, (0, 200, 255), 2, tipLength=0.25)
        lines.append(f"proposee: dx={correction.dx:.1f} dy={correction.dy:.1f} s={correction.scale:.4f} (x8)")
    if applied is not None and not applied.is_zero:
        lines.append(f"appliquee: dx={applied.dx:.1f} dy={applied.dy:.1f} s={applied.scale:.4f}")
    for index, text in enumerate(lines):
        cv2.putText(output, text, (10, 22 + 20 * index), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 1, cv2.LINE_AA)


# Crops HUD enregistrés avec une observation ; « client » est l'image entière du client (JPEG).
STATE_CROP_FILES = (("ap", "ap_original.png"), ("mp", "mp_original.png"), ("end_turn", "end_turn.png"),
                    ("spell_bar", "spell_bar.png"), ("client", "client.jpg"))


def save_debug_observation(packet: ObservationPacket, directory: Path | None = None) -> Path:
    root = directory or (app_data_root() / "data" / "debug")
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time_ns() % 1_000_000):06d}"
    original_path = root / f"{stamp}-original.png"
    annotated_path = root / f"{stamp}-annotated.png"
    json_path = root / f"{stamp}-observation.json"
    if not cv2.imwrite(str(original_path), np.asarray(packet.original)):
        raise OSError("Impossible d'enregistrer la capture originale")
    if not cv2.imwrite(str(annotated_path), np.asarray(packet.annotated)):
        raise OSError("Impossible d'enregistrer la capture annotée")
    hud_files: dict[str, str] = {}
    if packet.hud_crops:
        hud_directory = root / f"{stamp}-hud"
        hud_directory.mkdir(parents=True, exist_ok=True)
        for name, filename in STATE_CROP_FILES:
            crop = packet.hud_crops.get(name)
            if crop is not None and np.asarray(crop).size:
                path = hud_directory / filename
                parameters = [cv2.IMWRITE_JPEG_QUALITY, 90] if filename.endswith(".jpg") else []
                if not cv2.imwrite(str(path), np.asarray(crop), parameters):
                    raise OSError(f"Impossible d'enregistrer le crop {name.upper()}")
                hud_files[name] = f"{hud_directory.name}/{filename}"
    prediction = asdict(packet.observation)
    grid = prediction.get("grid", {})
    cells = grid.get("cells", [])
    payload = {
        "schema_version": 1,
        "observation_id": f"obs_{uuid4().hex[:16]}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "session_id": packet.metadata.get("session_id"),
        "frame_index": packet.metadata.get("frame_index"),
        "capture": dict(packet.metadata),
        "prediction": prediction,
        "grid_snapshot": {
            "cell_count": len(cells),
            "confidence": grid.get("confidence"),
            # LOT 3B-2 : identité canonique DofusCellId + source de grille explicite.
            "grid_source": grid.get("grid_source"),
            "map_id_declared": grid.get("map_id_declared"),
            "map_id_origin": "DECLARED_MANUALLY" if grid.get("map_id_declared") is not None else None,
            "map_id_source": grid.get("map_id_source"),
            "grid_profile_version": grid.get("grid_profile_version"),
            "projection_confidence": grid.get("projection_confidence"),
            "cells": cells,
            "player_cell": prediction.get("player_cell"),
            "player_confidence": prediction.get("player_confidence"),
            "enemies": prediction.get("enemies", []),
        },
        "files": {
            "frame": original_path.name,
            "overlay": annotated_path.name,
            **hud_files,
        },
    }
    json_path.write_text(json.dumps(
        payload, ensure_ascii=False, indent=2,
        default=lambda value: value.value if hasattr(value, "value") else str(value),
    ), encoding="utf-8")
    return json_path
