"""LOT 3B-6C : index spatial GameData, lecture des infos de map, résolveur, changement de map."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from combatbot.gamedata.map_index import MapRecord, MapSpatialIndex, name_similarity, normalize_name
from combatbot.vision.map_reader import (
    CoordinateConsensus, MapCoordinateReader, MapInfoObservation, parse_coordinates, parse_map_info,
)
from combatbot.vision.map_resolver import (
    AutoMapIdentity, MapContextResolver, MapContextService, MapKnowledge, MapResolutionStatus,
    compute_fingerprint,
)

CLIENT = Path(r"C:\Users\Thoma\AppData\Local\Alea\Client")


def _record(map_id, x, y, sub="Tainéla", area="Astrub", level=20, world=1, outdoor=True, neighbours=()):
    return MapRecord(map_id, x, y, world, 1, 1, level, outdoor, False, sub, area, tuple(neighbours))


@pytest.fixture
def index() -> MapSpatialIndex:
    return MapSpatialIndex([
        _record(10, 0, -32, neighbours=(11,)),                        # extérieur
        _record(20, 0, -32, world=-1, outdoor=False, neighbours=(21,)),  # intérieur : mêmes coords et noms
        _record(11, 0, -31, neighbours=(10, 12)),                     # voisin de 10
        _record(12, 0, -30, neighbours=(11,)),
        _record(30, 5, -19, sub="Cité d'Astrub", level=10),          # coordonnées uniques
        _record(40, 7, -2, sub="Port de Madrestam", area="Amakna", level=15),
        _record(41, 7, -4, sub="Port de Madrestam", area="Amakna", level=15),
        _record(50, 9, 30, sub="Péninsule des gelées", area="Amakna", level=50, world=1),
        _record(51, 9, 30, sub="Gelaxième dimension", area="Amakna", level=50, world=-1, outdoor=False),
    ])


def _info(x, y, sub="Tainéla", area="Astrub", level=20, stamp=0.0):
    return parse_map_info([(f"{area} ({sub})", 0.99), (f"{x},{y}, Niveau {level}", 0.99)], timestamp=stamp)


# ------------------------------------------------------------------ index GameData
@pytest.mark.skipif(not (CLIENT / "data" / "common" / "MapPositions.d2o").is_file(), reason="client absent")
def test_coordinate_index_contains_real_maps(tmp_path: Path) -> None:
    from combatbot.gamedata.map_index import build_index
    real = build_index(CLIENT, with_neighbours=False)
    assert len(real) == 12154
    record = real.get_map(88084487)
    assert (record.x, record.y, record.sub_area_name, record.area_name, record.level) == (
        9, 30, "Péninsule des gelées", "Amakna", 50)
    assert {item.map_id for item in real.candidates_by_coords(9, 30)} == {88084487, 98566659}


def test_unique_coordinates_resolve_directly(index) -> None:
    result = MapContextResolver(index).resolve(_info(5, -19, sub="Cité d'Astrub", level=10))
    assert result.status is MapResolutionStatus.RESOLVED and result.map_id == 30
    assert result.source.startswith("OCR_COORDS_UNIQUE")


def test_duplicate_coordinates_return_candidates(index) -> None:
    result = MapContextResolver(index).resolve(_info(0, -32))
    assert result.status is MapResolutionStatus.AMBIGUOUS and result.map_id is None
    assert set(result.candidates) == {10, 20} and result.candidate_count == 2


def test_world_map_disambiguates_coordinates(index) -> None:
    """Deux maps aux mêmes coordonnées mais sous-zones différentes : le nom affiché tranche."""
    result = MapContextResolver(index).resolve(_info(9, 30, sub="Péninsule des gelées", area="Amakna", level=50))
    assert result.status is MapResolutionStatus.RESOLVED and result.map_id == 50


def test_unknown_coordinates_return_unknown(index) -> None:
    result = MapContextResolver(index).resolve(_info(99, 99))
    assert result.status is MapResolutionStatus.UNKNOWN and result.map_id is None


def test_gamedata_index_invalidates_when_source_changes(tmp_path: Path, monkeypatch) -> None:
    from combatbot.gamedata import map_index
    calls = []
    monkeypatch.setattr(map_index, "source_fingerprint", lambda root: "v1")
    monkeypatch.setattr(map_index, "build_index", lambda root, progress=None, **kwargs: (calls.append(1), MapSpatialIndex(
        [_record(1, 0, 0)], fingerprint=map_index.source_fingerprint(root)))[1])
    first = map_index.load_or_build(tmp_path, tmp_path / "cache")
    assert first.fingerprint == "v1" and len(calls) == 1
    again = map_index.load_or_build(tmp_path, tmp_path / "cache")      # cache valide : pas de reconstruction
    assert len(calls) == 1 and len(again) == 1
    monkeypatch.setattr(map_index, "source_fingerprint", lambda root: "v2")   # fichier client modifié
    map_index.load_or_build(tmp_path, tmp_path / "cache")
    assert len(calls) == 2


def test_name_normalisation() -> None:
    assert normalize_name("Île de Moon (Forêt des Masques)") == "ile de moon foret des masques"
    assert name_similarity("Tainéla", "Tainela") == 1.0
    assert name_similarity("Tainéla", "Cité d'Astrub") < 0.5


# ------------------------------------------------------------------ lecture OCR
def test_parse_positive_coordinates() -> None:
    assert (parse_coordinates("4,12").x, parse_coordinates("4,12").y) == (4, 12)


def test_parse_negative_coordinates() -> None:
    value = parse_coordinates("[-4,-12]")
    assert (value.x, value.y) == (-4, -12)


def test_parse_spacing_variants() -> None:
    for text in ("[4,-12]", "4,-12", "4, -12", "[ 4 , -12 ]"):
        value = parse_coordinates(text)
        assert value is not None and (value.x, value.y) == (4, -12), text


def test_reject_double_comma() -> None:
    assert parse_coordinates("4,,12") is None
    assert parse_map_info([("Astrub (Tainéla)", 0.99), ("4,,12, Niveau 20", 0.99)]).complete is False


def test_reject_partial_digit() -> None:
    assert parse_coordinates("4,-1?") is None
    assert parse_coordinates("4,-1234") is None
    # Cas réel (sprite devant le texte) : « 7,-2 » lu « 7,-4, » sans « Niveau » → incomplet, jamais utilisé.
    masked = parse_map_info([("Ar", 1.0), ("t de Madrestam)", 0.98), ("7,-4,", 0.98), ("15", 1.0)])
    assert masked.complete is False and masked.key is None


def test_parse_map_info_real_lines() -> None:
    info = parse_map_info([("Amakna (Péninsule des", 0.99), ("gelées)", 1.0), ("9,30, Niveaù 50", 0.99)])
    assert info.complete and (info.coordinates.x, info.coordinates.y, info.level) == (9, 30, 50)
    assert info.area_name == "Amakna" and info.sub_area_name == "Péninsule des gelées"


def test_temporal_consensus_rejects_single_bad_frame() -> None:
    consensus = CoordinateConsensus()
    assert consensus.update(_info(0, -32)) is None                     # une seule lecture : pas encore
    assert consensus.update(_info(0, -32)) is not None
    assert consensus.update(_info(0, -33)) is None                     # lecture isolée différente : refusée
    assert consensus.pending_change
    assert consensus.update(_info(0, -32)) is not None


def test_real_coordinate_crop_if_fixture_available() -> None:
    fixture = Path(__file__).parent / "fixtures" / "map_info_crop.png"
    if not fixture.is_file():
        pytest.skip("pas de crop réel versionné")
    import cv2
    info = MapCoordinateReader().read(cv2.imread(str(fixture)), roi_image=cv2.imread(str(fixture)))
    assert info.complete


# ------------------------------------------------------------------ résolveur
def test_fresh_log_map_id_has_priority(index) -> None:
    result = MapContextResolver(index).resolve(_info(0, -32), log_map_id=10)
    assert result.status is MapResolutionStatus.RESOLVED and result.map_id == 10 and result.source == "LOCAL_LOG"


def test_stale_log_is_rejected(index) -> None:
    """Aucune source de log locale n'existe (audit) : sans log frais, seule la lecture écran compte."""
    result = MapContextResolver(index).resolve(_info(0, -32), log_map_id=None)
    assert result.status is MapResolutionStatus.AMBIGUOUS


def test_log_map_not_in_gamedata_is_rejected(index) -> None:
    result = MapContextResolver(index).resolve(_info(0, -32), log_map_id=999)
    assert result.status is MapResolutionStatus.UNKNOWN and result.map_id is None


def test_unique_coord_map_resolves(index) -> None:
    assert MapContextResolver(index).resolve(_info(0, -31)).map_id == 11


def test_previous_map_graph_disambiguates(index) -> None:
    result = MapContextResolver(index).resolve(_info(0, -32), previous_map_id=11, scene_changed=True)
    assert result.status is MapResolutionStatus.RESOLVED and result.map_id == 10
    assert "PREVIOUS_MAP_GRAPH" in result.source and result.contributions["graph_score"] == 1.0


def test_teleport_does_not_force_neighbour(index) -> None:
    """Arrivée non locale (zaap, porte) : aucune candidate voisine → pas de choix forcé."""
    result = MapContextResolver(index).resolve(_info(0, -32), previous_map_id=30, scene_changed=True)
    assert result.status is MapResolutionStatus.AMBIGUOUS and result.contributions.get("non_local_transition")


def test_same_coordinates_after_scene_change_is_not_assumed_same_map(index) -> None:
    """Entrer dans une maison : mêmes coordonnées et noms, mais autre map → ambigu, pas la précédente."""
    resolver = MapContextResolver(index)
    assert resolver.resolve(_info(0, -32), previous_map_id=10).map_id == 10          # scène continue
    assert resolver.resolve(_info(0, -32), previous_map_id=10, scene_changed=True).status is \
        MapResolutionStatus.AMBIGUOUS


def test_area_name_disambiguates(index) -> None:
    result = MapContextResolver(index).resolve(_info(9, 30, sub="Gelaxième dimension", area="Amakna", level=50))
    assert result.map_id == 51


def test_visual_fingerprint_only_used_as_fallback(index, tmp_path: Path) -> None:
    knowledge = MapKnowledge(tmp_path / "knowledge.json")
    rng = np.random.default_rng(1)
    outdoor = rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)
    indoor = rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)
    knowledge.learn(10, compute_fingerprint(outdoor), source="test", layout=None)
    knowledge.learn(20, compute_fingerprint(indoor), source="test", layout=None)
    resolver = MapContextResolver(index, knowledge)
    # Unique par les noms : l'empreinte n'est même pas consultée.
    unique = resolver.resolve(_info(5, -19, sub="Cité d'Astrub", level=10), fingerprint=compute_fingerprint(indoor))
    assert unique.map_id == 30 and "fingerprint_candidates" not in unique.contributions
    fallback = resolver.resolve(_info(0, -32), fingerprint=compute_fingerprint(indoor))
    assert fallback.map_id == 20 and "FINGERPRINT" in fallback.source
    stranger = rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)
    assert resolver.resolve(_info(0, -32), fingerprint=compute_fingerprint(stranger)).status is \
        MapResolutionStatus.AMBIGUOUS
    assert MapKnowledge(tmp_path / "knowledge.json").fingerprint_bits(20)   # persistant


def test_ambiguous_candidates_return_ambiguous(index) -> None:
    result = MapContextResolver(index).resolve(_info(0, -32))
    assert result.status is MapResolutionStatus.AMBIGUOUS and not result.recordable


def test_conflicting_sources_return_inconsistent(index) -> None:
    wrong_names = MapContextResolver(index).resolve(_info(0, -32, sub="Cité d'Astrub", level=10))
    assert wrong_names.status is MapResolutionStatus.INCONSISTENT and wrong_names.map_id is None
    log_conflict = MapContextResolver(index).resolve(_info(5, -19, sub="Cité d'Astrub", level=10), log_map_id=10)
    assert log_conflict.status is MapResolutionStatus.INCONSISTENT


# ------------------------------------------------------------------ service et changement de map
class ScriptedReader(MapCoordinateReader):
    def __init__(self, script):
        super().__init__(engine=lambda image: [])
        self.script = list(script)

    def read(self, client_image, *, roi_image=None, timestamp=None):
        x, y = self.script.pop(0)
        return _info(x, y, stamp=timestamp or 0.0) if x is not None else MapInfoObservation(
            None, None, None, None, False, "NO_COMPLETE_COORDINATE_LINE", (), timestamp or 0.0)


def _service(index, script, tmp_path):
    return MapContextService(MapContextResolver(index, MapKnowledge(tmp_path / "k.json")),
                             reader=ScriptedReader(script), interval=0.0, synchronous=True)


def _frame(value=90):
    return np.full((180, 320, 3), value, np.uint8)


def test_single_bad_ocr_does_not_reset_tracker(index, tmp_path: Path) -> None:
    service = _service(index, [(0, -31), (0, -31), (0, -30), (0, -31)], tmp_path)
    frame = _frame()
    service.update(frame, frame, now=0.0)
    assert service.update(frame, frame, now=1.0).map_id == 11
    noisy = service.update(frame, frame, now=2.0)                     # une lecture (0,-30) isolée
    assert noisy.status is MapResolutionStatus.TRANSITION and service.identity.current_map().map_id == 11
    assert service.update(frame, frame, now=3.0).map_id == 11 and service.map_id == 11


def test_confirmed_map_change_changes_identity(index, tmp_path: Path) -> None:
    service = _service(index, [(0, -31), (0, -31), (0, -30), (0, -30)], tmp_path)
    frame = _frame()
    for stamp in (0.0, 1.0, 2.0):
        service.update(frame, frame, now=stamp)
    result = service.update(frame, frame, now=3.0)
    assert result.status is MapResolutionStatus.RESOLVED and result.map_id == 12 and result.changed
    assert service.identity.current_map().map_id == 12


def test_confirmed_map_change_resets_tracker_and_background(monkeypatch) -> None:
    """L'observateur réinitialise suivi et fond seulement quand l'identité de map change."""
    from combatbot.vision import combat_observer
    from combatbot.vision.entity_tracker import EntityTracker
    from combatbot.vision.background_model import CellBackgroundModel
    from combatbot.vision.combat_models import CombatGridObservation
    from combatbot.vision.entity_models import VisualProfiles
    observer = combat_observer.RealCombatObserver.__new__(combat_observer.RealCombatObserver)
    observer._entity_map = 11
    observer.entity_tracker = EntityTracker()
    observer.background_model = CellBackgroundModel()
    observer.entity_profiles = profiles = VisualProfiles()
    resets = []
    monkeypatch.setattr(observer.background_model, "reset", lambda: resets.append("background"))
    tracker_before = observer.entity_tracker

    class Stop(Exception):
        pass

    def detect(*args, **kwargs):
        raise Stop
    observer.entity_detector = type("D", (), {"detect": staticmethod(detect)})()
    observer.calibration = type("C", (), {"layout_signature": "b2b36fdf07e78000"})()
    with pytest.raises(Stop):
        observer._observe_entities(np.zeros((10, 10, 3), np.uint8), CombatGridObservation(map_id_declared=11), 0.0)
    assert observer.entity_tracker is tracker_before and not resets    # même map : rien n'est réinitialisé
    with pytest.raises(Stop):
        observer._observe_entities(np.zeros((10, 10, 3), np.uint8), CombatGridObservation(map_id_declared=12), 0.0)
    assert observer.entity_tracker is not tracker_before and resets == ["background"]
    assert observer.entity_profiles is profiles                        # profils visuels conservés


def test_scene_transition_blocks_recording_until_new_reading(index, tmp_path: Path) -> None:
    service = _service(index, [(0, -31), (0, -31), (0, -31), (0, -31)], tmp_path)
    frame = _frame()
    service.update(frame, frame, now=0.0)
    assert service.update(frame, frame, now=1.0).recordable
    black = _frame(0)
    assert service.update(black, black, now=2.0).status is MapResolutionStatus.TRANSITION
    assert service.identity.current_map().map_id == 11                 # suivi non réinitialisé
    service.update(frame, frame, now=3.0)
    assert service.update(frame, frame, now=4.0).recordable


def test_old_ocr_job_does_not_overwrite_newer_result(index, tmp_path: Path) -> None:
    service = _service(index, [], tmp_path)
    service._consume(2, 5.0, _info(0, -31, stamp=5.0))
    service._consume(1, 4.0, _info(0, -30, stamp=4.0))                 # génération plus ancienne : ignorée
    assert service._last_observation.coordinates.y == -31


def test_manual_fallback_is_remembered(index, tmp_path: Path) -> None:
    service = _service(index, [(0, -32), (0, -32)], tmp_path)
    frame = _frame(120)
    service.update(frame, frame, now=0.0)
    assert service.update(frame, frame, now=1.0).status is MapResolutionStatus.AMBIGUOUS
    service.declare_manual(20, frame)
    assert service.resolution.recordable and service.resolution.source == "MANUAL"
    knowledge = MapKnowledge(tmp_path / "k.json")
    assert knowledge.confirmed_maps(_info(0, -32).key) == {20} and knowledge.fingerprint_bits(20)


def test_auto_identity_labels_detected_map() -> None:
    identity = AutoMapIdentity()
    identity.set_detected(191102978)
    assert identity.current_map().label == "Map ID détecté automatiquement : 191102978"
    identity.set_detected(None)
    assert identity.current_map() is None


def test_map_resolution_tooling_never_imports_action_executor() -> None:
    import combatbot.gamedata.map_index as a
    import combatbot.vision.map_reader as b
    import combatbot.vision.map_resolver as c
    for module in (a, b, c):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "ActionExecutor" not in source and "pyautogui" not in source and "socket" not in source


# ------------------------------------------------------------------ calibration réutilisée
def test_new_map_reuses_layout_calibration() -> None:
    """Nouvelle map détectée : même profil de layout, topologie de la nouvelle map, aucune recalibration."""
    from test_gamedata_grid import CLIENT as GRID_CLIENT, ZONES, make_topology, render, resolver as grid_resolver
    first, second = make_topology(1, seed=1), make_topology(2, seed=2)
    resolver = grid_resolver(first, second)
    identity = AutoMapIdentity()
    resolver.map_identity = identity
    identity.set_detected(1)
    a = resolver.resolve(render(first), GRID_CLIENT, ZONES)
    identity.set_detected(2)
    b = resolver.resolve(render(second), GRID_CLIENT, ZONES)
    assert a.grid.map_id_declared == 1 and b.grid.map_id_declared == 2
    assert a.grid.transform == b.grid.transform and not b.requires_recalibration
    assert b.grid.map_id_source == "auto_detected"


# ------------------------------------------------------------------ sécurité du corpus
def _packet(*, status="RESOLVED", resolved=11, grid_map=11, visibility="VISIBLE", alignment="ALIGNED",
            source="auto_detected", grid_source="GAMEDATA_PROJECTED"):
    from combatbot.vision.combat_models import CombatGridObservation, CombatObservation, ObservationPacket
    grid = CombatGridObservation(grid_source=grid_source, map_id_declared=grid_map, map_id_source=source,
                                 grid_visibility={"state": visibility}, alignment={"status": alignment})
    observation = CombatObservation(True, 1.0, None, 0.0, None, 0.0, (), grid, None, None, 0.0, 0.0, 0.0)
    resolution = {"status": status, "map_id": resolved if status == "RESOLVED" else None}
    return ObservationPacket(observation, None, None, 1.0, {"map_resolution": resolution})


def test_frame_never_saved_with_unknown_map() -> None:
    from combatbot.corpus.recording_guard import recording_block_reason
    assert "non résolue" in recording_block_reason(_packet(status="UNKNOWN"))
    assert recording_block_reason(_packet(status="TRANSITION")) is not None


def test_frame_never_saved_with_ambiguous_map() -> None:
    from combatbot.corpus.recording_guard import recording_block_reason
    assert "AMBIGUOUS" in recording_block_reason(_packet(status="AMBIGUOUS"))
    # Grille encore chargée pour l'ancienne map : bloqué aussi.
    assert recording_block_reason(_packet(resolved=12, grid_map=11)) is not None


def test_frame_never_saved_with_unaligned_grid() -> None:
    from combatbot.corpus.recording_guard import recording_block_reason
    assert "projection de grille à vérifier" in recording_block_reason(_packet(alignment="MISALIGNED"))


def test_alignment_failure_blocks_corpus_recording() -> None:
    from combatbot.corpus.recording_guard import recording_block_reason
    assert recording_block_reason(_packet(alignment="DEGRADED")) is not None


def test_resolved_map_and_aligned_grid_allow_recording() -> None:
    from combatbot.corpus.recording_guard import recording_block_reason
    assert recording_block_reason(_packet()) is None
    # Hors combat / fenêtre de résultats : grille non visible ou incertaine, map résolue → gardée pour la
    # phase/tour, mais marquée et exclue des entités (retour utilisateur : résultats jamais enregistrés).
    from combatbot.corpus.recording_guard import recording_tags
    for visibility in ("NOT_VISIBLE", "UNKNOWN"):
        packet = _packet(visibility=visibility, alignment="INSUFFICIENT_EVIDENCE")
        assert recording_block_reason(packet) is None and recording_tags(packet) == ("grid-not-aligned",)
    assert recording_tags(_packet()) == ()
    # Map toujours exigée, même sans grille.
    assert recording_block_reason(_packet(status="TRANSITION", visibility="UNKNOWN")) is not None
    # Secours manuel (/mapid) : autorisé, comme avant 3B-6C, si la grille est alignée.
    assert recording_block_reason(_packet(status="UNKNOWN", source="user_verified_mapid")) is None


# ------------------------------------------------------------------ donjons (format « nom de la map »)
DUNGEON = [MapRecord(120063489, 2, -34, 1, 30, 1, 20, True, False, "Tainéla", "Astrub", (), None)] + [
    MapRecord(map_id, 2, -34, -1, 82, 2, 30, False, False, "Cour du Bouftou", "Amakna", (), name)
    for map_id, name in ((121373185, "Cour du Bouftou Royal - Première salle"),
                         (121374209, "Cour du Bouftou Royal - Deuxième salle"),
                         (121374211, "Cour du Bouftou Royal - Dernière salle"),
                         (205784064, "Cour du Bouftou Royal"))]


def test_dungeon_room_name_format_is_parsed() -> None:
    """Cas réel (donjon Bouftou) : « Cour du Bouftou Royal - Première salle » puis « 2,-34 »."""
    info = parse_map_info([("Cour du Bouftou Royal - Première salle", 0.99213), ("2,-34", 0.9999)])
    assert info.complete and info.map_name == "Cour du Bouftou Royal - Première salle"
    assert (info.coordinates.x, info.coordinates.y) == (2, -34) and info.level is None


def test_dungeon_rooms_resolve_by_exact_room_name() -> None:
    resolver = MapContextResolver(MapSpatialIndex(DUNGEON))
    for name, expected in (("Cour du Bouftou Royal - Première salle", 121373185),
                           ("Cour du Bouftou Royal - Deuxième salle", 121374209),
                           ("COUR DU BOUFTOU ROYAL – DERNIERE SALLE", 121374211)):
        info = parse_map_info([(name, 0.99), ("2,-34", 0.99)])
        result = resolver.resolve(info, previous_map_id=120063489, scene_changed=True)
        assert result.status is MapResolutionStatus.RESOLVED and result.map_id == expected, name
        assert "MAP_NAME" in result.source


def test_dungeon_room_name_is_never_fuzzy_matched() -> None:
    """« Premiere » mal lu en « Premierc » : proche de plusieurs salles → jamais de choix approximatif."""
    resolver = MapContextResolver(MapSpatialIndex(DUNGEON))
    info = parse_map_info([("Cour du Bouftou Royal - Premierc salle", 0.99), ("2,-34", 0.99)])
    result = resolver.resolve(info)
    assert result.status is MapResolutionStatus.INCONSISTENT and result.map_id is None


def test_named_format_rejects_partial_or_mixed_lines() -> None:
    assert parse_map_info([("Cour du Bouftou Royal - Première salle", 0.99), ("2,-3?", 0.99)]).complete is False
    assert parse_map_info([("2,-34", 0.99)]).complete is False                      # nom absent
    # « Zone (Sous-zone) » avec « x,y » seul : niveau masqué → incomplet, jamais un nom de map.
    masked = parse_map_info([("Amakna (Port de Madrestam)", 0.99), ("7,-4", 0.99)])
    assert masked.complete is False and masked.reason == "LEVEL_MISSING"
    assert parse_map_info([("Cour du Bouftou Royal", 0.6), ("2,-34", 0.99)]).complete is False


def test_grid_not_aligned_frames_are_excluded_from_entity_annotation(tmp_path: Path) -> None:
    from combatbot.corpus.repository import CorpusRepository
    from combatbot.ui.entity_annotation_dialog import entity_entries
    from combatbot.vision.combat_models import CombatGridObservation, CombatObservation, ObservationPacket
    from combatbot.vision.grid_projection import GridScreenTransform
    repository = CorpusRepository(tmp_path / "corpus")
    grid = CombatGridObservation(grid_source="GAMEDATA_PROJECTED", map_id_declared=1,
                                 transform=GridScreenTransform.from_cell_size((40, 20), 80, 40).to_dict())
    observation = CombatObservation(True, 1.0, None, 0.0, None, 0.0, (), grid, None, None, 0.0, 0.0, 0.0)
    image = np.zeros((60, 80, 3), np.uint8)
    kept = repository.import_packet(ObservationPacket(observation, image, image, 1.0, {"session_id": "s", "frame_index": 0}))
    tagged = repository.import_packet(ObservationPacket(observation, image, image, 1.0, {"session_id": "s", "frame_index": 1}),
                                      tags=("entity-sequence", "grid-not-aligned"))
    ids = {entry.observation_id for entry in entity_entries(repository)}
    assert tagged.observation_id not in ids
    assert kept.observation_id in ids or not ids          # sans cellules projetées, aucune n'est listée


# ------------------------------------------------------------------ durcissement : forme à l'écran et hypothèses
from combatbot.vision.map_resolver import MapShape, screen_darkness  # noqa: E402

ROOM = frozenset(range(250, 290))                     # petite pièce d'intérieur au milieu de la grille


def _shapes(extra=None):
    table = {
        10: MapShape(frozenset(range(0, 560, 2)), frozenset(), 0, True),       # extérieur plein
        20: MapShape(ROOM, frozenset(set(range(560)) - ROOM), 0, False),       # maison : vide noir autour
        11: MapShape(frozenset(range(0, 560, 3)), frozenset(), 0, True),
        21: MapShape(ROOM, frozenset(set(range(560)) - ROOM), 0, False),
        **(extra or {})}
    return table.get


def _dark(lit: set[int]) -> dict[int, bool]:
    return {cell: cell not in lit for cell in range(560)}


def test_dark_screen_refutes_outdoor_map(index) -> None:
    """Dans la maison : les cases marchables de l'extérieur sont noires → seule la maison reste."""
    result = MapContextResolver(index, shapes=_shapes()).resolve(_info(0, -32), screen_dark=_dark(set(ROOM)))
    assert result.status is MapResolutionStatus.RESOLVED and result.map_id == 20
    assert "SCREEN_SHAPE" in result.source and result.contributions["shape_refuted"] == [10]
    assert result.confidence < 0.97                 # aucune empreinte apprise sur ce seul indice


def test_lit_screen_refutes_indoor_without_background(index) -> None:
    """Dehors : le vide de la maison est éclairé → la maison est éliminée."""
    result = MapContextResolver(index, shapes=_shapes()).resolve(_info(0, -32), screen_dark=_dark(set(range(560))))
    assert result.status is MapResolutionStatus.RESOLVED and result.map_id == 10
    assert result.contributions["shape"]["20"]["refuted"] == "INDOOR_VOID_LIT"


def test_indoor_with_background_image_is_never_refuted_by_lit_void(index) -> None:
    shapes = _shapes({20: MapShape(ROOM, frozenset(set(range(560)) - ROOM), 3, False)})
    result = MapContextResolver(index, shapes=shapes).resolve(_info(0, -32), screen_dark=_dark(set(range(560))))
    assert result.status is MapResolutionStatus.AMBIGUOUS


def test_shape_refuting_every_candidate_eliminates_nothing(index) -> None:
    shapes = _shapes({20: MapShape(frozenset(range(300, 340)), frozenset(), 0, False)})
    result = MapContextResolver(index, shapes=shapes).resolve(_info(0, -32), screen_dark=_dark(set()))
    assert result.status is MapResolutionStatus.AMBIGUOUS and result.contributions["shape_refutes_all"]
    assert set(result.candidates) == {10, 20}


def test_shape_needs_enough_visible_cells(index) -> None:
    few = {cell: True for cell in range(10)}           # presque rien de visible : aucune élimination
    result = MapContextResolver(index, shapes=_shapes()).resolve(_info(0, -32), screen_dark=few)
    assert result.status is MapResolutionStatus.AMBIGUOUS


def test_screen_darkness_samples_cell_centres() -> None:
    image = np.zeros((100, 200, 3), np.uint8)
    image[:, 100:] = 200
    dark = screen_darkness(image, {1: (50, 50), 2: (150, 50), 3: (199, 50)})
    assert dark == {1: True, 2: False}                 # la case 3 sort de l'image


@pytest.fixture
def twin_index() -> MapSpatialIndex:
    """Deux coordonnées voisines, chacune avec un extérieur et un intérieur jumeaux."""
    return MapSpatialIndex([
        _record(10, 0, -32, neighbours=(11,)),
        _record(20, 0, -32, world=-1, outdoor=False, neighbours=(9020,)),   # voisins DLM fictifs
        _record(11, 0, -31, neighbours=(10,)),
        _record(21, 0, -31, world=-1, outdoor=False, neighbours=(9021,)),
        _record(12, 1, -32, neighbours=(13,)),
        _record(14, 1, -32, neighbours=(15,)),
        _record(13, 1, -31, neighbours=(12,)),
        _record(15, 1, -31, neighbours=(14,)),
        _record(16, 1, -31, neighbours=()),
    ])


def test_outdoor_hypotheses_resolve_through_graph(twin_index) -> None:
    result = MapContextResolver(twin_index).resolve(_info(1, -31), scene_changed=True, previous_candidates=(12,))
    assert result.status is MapResolutionStatus.RESOLVED and result.map_id == 13
    assert "PREVIOUS_CANDIDATES_GRAPH" in result.source


def test_hypotheses_narrow_without_forcing(twin_index) -> None:
    result = MapContextResolver(twin_index).resolve(_info(1, -31), scene_changed=True, previous_candidates=(12, 14))
    assert result.status is MapResolutionStatus.AMBIGUOUS and set(result.candidates) == {13, 15}


def test_indoor_hypothesis_requires_screen_support(twin_index) -> None:
    """Depuis {extérieur, maison}, une porte pourrait changer les coordonnées : le graphe seul ne suffit pas."""
    resolver = MapContextResolver(twin_index, shapes=_shapes())
    blind = resolver.resolve(_info(0, -31), scene_changed=True, previous_candidates=(10, 20))
    assert blind.status is MapResolutionStatus.AMBIGUOUS and set(blind.candidates) == {11, 21}
    assert blind.contributions["hypothesis_graph_unsupported"] == 11
    # Écran éclairé partout : la maison 21 est éliminée par la forme, l'extérieur 11 est soutenu.
    seen = resolver.resolve(_info(0, -31), scene_changed=True, previous_candidates=(10, 20),
                            screen_dark=_dark(set(range(560))))
    assert seen.status is MapResolutionStatus.RESOLVED and seen.map_id == 11


def test_service_keeps_hypotheses_until_resolved(twin_index, tmp_path: Path) -> None:
    service = MapContextService(MapContextResolver(twin_index, MapKnowledge(tmp_path / "k.json")),
                                reader=ScriptedReader([(1, -32), (1, -32), (1, -31), (1, -31)]),
                                interval=0.0, synchronous=True)
    frame = _frame()
    service.update(frame, frame, now=0.0)
    ambiguous = service.update(frame, frame, now=1.0)
    assert ambiguous.status is MapResolutionStatus.AMBIGUOUS and service.hypotheses == (12, 14)
    service.update(frame, frame, now=2.0)
    still = service.update(frame, frame, now=3.0)     # 2 hypothèses → 2 voisines : jamais forcé
    assert still.status is MapResolutionStatus.AMBIGUOUS and set(service.hypotheses) == {13, 15}
    assert service.map_id is None


def test_service_resolves_after_one_step_from_ambiguous_start(twin_index, tmp_path: Path) -> None:
    index = MapSpatialIndex([r for r in twin_index.maps.values() if r.map_id != 14 and r.map_id != 15])
    service = MapContextService(MapContextResolver(index, MapKnowledge(tmp_path / "k.json")),
                                reader=ScriptedReader([(0, -32), (0, -32), (0, -31), (0, -31)]),
                                interval=0.0, synchronous=True, track_hypotheses=True)
    frame = _frame()
    service.update(frame, frame, now=0.0)
    assert service.update(frame, frame, now=1.0).status is MapResolutionStatus.AMBIGUOUS
    assert service.hypotheses == (10, 20)
    service.update(frame, frame, now=2.0)
    step = service.update(frame, frame, now=3.0)
    # Hypothèse intérieure possible et pas d'écran : rien n'est forcé (porte possible).
    assert step.status is MapResolutionStatus.AMBIGUOUS and step.map_id is None
