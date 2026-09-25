"""Installation réversible et revue de séquence, sans client ni données utilisateur."""
from dataclasses import replace
import json

import pytest

from test_entity_corpus import build_corpus
from combatbot.entity_runtime import (
    active_profile_directory, hashes, install_prepared, prepare_installation, read_json,
)
from combatbot.corpus.entity_benchmark import entity_inventory, run_entity_benchmark


@pytest.fixture
def prepared(tmp_path):
    data = tmp_path / "data"
    repository = build_corpus(data)
    samples = entity_inventory(repository)
    seed = {"schema_version": 2, "groups": {s.group_id: s.split for s in samples}, "migrations": []}
    return repository, prepare_installation(data, seed)


def test_runtime_installation_preserves_corpus_and_loads_observer(prepared, monkeypatch):
    from combatbot.packaging_smoke import runtime_profiles_check
    repository, plan = prepared
    before = hashes(repository.root)
    data = repository.root.parent
    result = install_prepared(data, plan)
    assert result["installed"] and hashes(repository.root) == before
    assert result["corpus_files_unchanged"] == len(before)
    assert read_json(active_profile_directory(data) / "entity_split_registry.v2.json")["schema_version"] == 2
    check = runtime_profiles_check(data)
    assert check["success"] and check["missing_unknown"]
    assert check["layouts"][0]["player_version"] == 2
    assert check["layouts"][0]["player_samples"] > 1


def test_installation_readback_failure_rolls_back_entire_generation(prepared, monkeypatch):
    import combatbot.entity_runtime as module
    repository, plan = prepared
    data = repository.root.parent
    install_prepared(data, plan)
    old = active_profile_directory(data)
    old_hashes = hashes(old)
    old_pointer = (data / "entity_profiles/active.json").read_bytes()
    original = module.read_json
    def broken_read(path):
        if path.name.startswith("player_train_") and path.parent.parent.name == "generations" and path.parent != old:
            raise OSError("simulated readback failure")
        return original(path)
    monkeypatch.setattr(module, "read_json", broken_read)
    with pytest.raises(OSError, match="readback failure"):
        install_prepared(data, plan)
    assert (data / "entity_profiles/active.json").read_bytes() == old_pointer
    assert hashes(old) == old_hashes and hashes(repository.root) == plan["corpus_sha256"]
    assert not (data / "entity_profiles/install.lock").exists()


def test_dry_run_and_normal_benchmark_never_write_runtime(prepared):
    repository, _plan = prepared
    before = hashes(repository.root.parent)
    run_entity_benchmark(repository)
    assert hashes(repository.root.parent) == before


def test_changed_corpus_rejects_stale_installation(prepared):
    repository, plan = prepared
    entry = repository.list_entries()[0]
    path = repository.resolve(entry.paths["observation"])
    path.write_text(path.read_text() + "\n")
    with pytest.raises(RuntimeError, match="Corpus modifié"):
        install_prepared(repository.root.parent, plan)
    assert not (repository.root.parent / "entity_profiles/active.json").exists()


def test_explicit_restore_returns_to_previous_profiles(prepared):
    from pathlib import Path
    from combatbot.entity_runtime import restore_backup
    repository, plan = prepared
    data = repository.root.parent
    first = install_prepared(data, plan)
    second = install_prepared(data, plan)
    assert active_profile_directory(data) != Path(first["generation"])
    restore_backup(data, Path(second["backup"]))
    assert active_profile_directory(data) == Path(first["generation"])
    assert hashes(repository.root) == plan["corpus_sha256"]


def test_sequence_confirmation_only_changes_tracking_fields(tmp_path):
    repository = build_corpus(tmp_path, ("new", "old"))
    entry = next(e for e in repository.list_entries() if e.session_id == "new")
    sequence = repository.tracking_sequence_id(entry)
    before = {e.observation_id: repository.read_annotation(e).to_dict() for e in repository.list_entries()}
    old_hashes = hashes(repository.root / "sessions/old")
    manifest = repository.manifest_path.read_bytes()
    repository.confirm_tracking_sequence(sequence, confirmed=True)
    assert repository.tracking_sequence_confirmed(sequence)
    assert repository.manifest_path.read_bytes() == manifest
    assert hashes(repository.root / "sessions/old") == old_hashes
    keys = {"tracking_identity_confirmed", "tracking_identity_source", "tracking_confirmed_at", "tracking_sequence_id"}
    for e in repository.list_entries():
        after = repository.read_annotation(e).to_dict()
        assert {k: v for k, v in after.items() if k not in keys} == {
            k: v for k, v in before[e.observation_id].items() if k not in keys}
    a = repository.read_annotation(entry)
    repository.save_annotation(replace(a, enemy_occluded_tracks=("E8",)))
    assert all(not repository.read_annotation(e).tracking_identity_confirmed
               for e in repository.tracking_sequence_entries(sequence))


def test_tracking_metrics_from_saved_frames_separates_splits():
    from combatbot.corpus.tracking_metrics import tracking_scopes
    frames = []
    for i, track in enumerate(("t1", None, "t2")):
        frames.append({"group_id": "s|map1", "frame_index": i, "timestamp": 10+i, "split": "test",
                       "tracking_identity_confirmed": True, "tracking_identity_source": "human_ui_review",
                       "tracking_sequence_id": "s|map1", "tracking_confirmed_at": "now",
                       "truth_identities": [{"track_id": "E1", "cell_id": 20}],
                       "tracked_entities": [{"kind": "ENEMY", "track_id": track, "cell_id": 20,
                                             "observed_this_frame": True, "state": "OBSERVED"}] if track else [],
                       "greedy_tracks": {track: 20} if track else {}})
    result = tracking_scopes(frames)
    assert result["train"]["global"]["status"] == "NOT_EVALUABLE"
    measured = result["test"]["global"]
    assert measured["exact_track_associations"] == 2 and measured["identity_observations"] == 3
    assert measured["id_switches"] == 1 and measured["fragmentations"] == 1
    assert measured["tracking_coverage"] == pytest.approx(2/3)
    assert measured["occlusion_status"] == "NOT_OBSERVED"


def test_runtime_pointer_failures_are_prudent(prepared, monkeypatch):
    """§18 : pointeur cassé ou génération incomplète → aucun profil, jamais un mélange."""
    from combatbot.vision.combat_observer import _load_profiles
    from combatbot.vision.models import Calibration
    repository, plan = prepared
    data = repository.root.parent
    install_prepared(data, plan)
    monkeypatch.setenv("PYTHONBOT_DATA_DIR", str(data.parent))
    base = data / "entity_profiles"
    pointer = base / "active.json"
    good = pointer.read_bytes()
    generation = active_profile_directory(data)
    calibration = Calibration(1, 2560, 1377, {}, layout_signature="layout-a")
    loaded = _load_profiles(calibration)
    assert loaded.player is not None and loaded.teams is not None
    for broken in (b'{"schema_version": 1, "generation": "inexistante"}', b"{pas du json",
                   b'{"schema_version": 1, "generation": "../generations"}', b"[]"):
        pointer.write_bytes(broken)
        empty = _load_profiles(calibration)
        assert empty.player is None and empty.teams is None, broken
    pointer.write_bytes(good)
    # Profil joueur corrompu : pas de joueur, rien d'une autre génération.
    player_file = next(generation.glob("player_train_*.json"))
    saved_player = player_file.read_bytes()
    player_file.write_text("{corrompu", encoding="utf-8")
    assert _load_profiles(calibration).player is None
    player_file.write_bytes(saved_player)
    # Profil d'équipe absent de la génération + ancien fichier racine : pas de mélange de générations.
    team_file = next(generation.glob("team_markers_*.json"))
    (base / "team_markers.json").write_bytes(team_file.read_bytes())
    team_file.unlink()
    partial = _load_profiles(calibration)
    assert partial.teams is None and partial.player is not None
