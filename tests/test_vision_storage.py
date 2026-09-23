import json
import sqlite3

import pytest

from combatbot.storage import Storage
from combatbot.vision.models import Calibration, IconCandidate, Profile, RecognizedText, RelativeRect, ZONE_NAMES


def test_migrates_legacy_database_after_backup_without_losing_simulation(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
        CREATE TABLE spells (id INTEGER PRIMARY KEY, name TEXT UNIQUE, ap_cost INTEGER,
            min_range INTEGER, max_range INTEGER, modifiable_range INTEGER,
            line_cast INTEGER, line_of_sight INTEGER, per_turn INTEGER,
            per_target INTEGER, priority INTEGER, damage INTEGER);
        CREATE TABLE combats (id INTEGER PRIMARY KEY, ended_at TEXT, outcome TEXT,
            turns INTEGER, xp INTEGER, kamas INTEGER);
        CREATE TABLE events (id INTEGER PRIMARY KEY, created_at TEXT, level TEXT,
            event TEXT, message TEXT, context_json TEXT);
        INSERT INTO settings VALUES ('player_name', '"Ancien profil"');
        INSERT INTO settings VALUES ('demo_seeded', 'true');
        INSERT INTO spells VALUES (4, 'Sort conservé', 2, 1, 3, 0, 0, 1, 2, 1, 8, 5);
    """)
    connection.close()

    store = Storage(path)
    assert store.backup_path is not None and store.backup_path.exists()
    assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert store.get_setting("player_name") == "Ancien profil"
    assert store.list_spells()[0].name == "Sort conservé"
    assert len(store.list_profiles()) == 1
    with sqlite3.connect(store.backup_path) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 0
        assert backup.execute("SELECT name FROM spells WHERE id=4").fetchone()[0] == "Sort conservé"
    store.close()
    reopened = Storage(path)
    assert reopened.backup_path is None
    reopened.close()


def test_profile_calibration_scanned_spell_confirmation_is_isolated(tmp_path) -> None:
    store = Storage(tmp_path / "profiles.sqlite3")
    profile_id = store.save_profile(Profile(None, "Personnage B", name="Nom saisi"))
    zones = {key: RelativeRect(0.1, 0.1, 0.2, 0.1) for key in ZONE_NAMES}
    store.save_calibration(Calibration(profile_id, 1000, 600, zones))
    assert store.load_calibration(profile_id).zones["spell_bar"] == zones["spell_bar"]

    candidate = IconCandidate(2, 4, b"fake_png", "a" * 16, 0.9, 0.0, "Inconnu")
    spell_id = store.save_scan_candidate(profile_id, candidate)
    assert store.get_profile_spell(spell_id)["name"] is None
    assert store.get_profile_spell(spell_id)["status"] == "Inconnu"
    assert store.list_spells()[0].name == "Sort simulé A"  # Isolation du moteur simulé.
    store.save_profile_spell_fields(spell_id, {"name": "Nom vérifié"}, confirm=True)
    assert store.get_profile_spell(spell_id)["decision_ready"] == 0
    store.save_profile_spell_fields(spell_id, {}, confirm=False)

    recognized = RecognizedText("Nom vérifié\n3 PA", 0.8, {"name": "Nom vérifié", "ap_cost": 3})
    store.save_recognized_characteristics(spell_id, recognized)
    row = store.get_profile_spell(spell_id)
    assert row["status"] == "À vérifier"
    assert row["max_range"] is None
    assert row["ocr_confidence"] == 0.8
    assert row["recognition_confidence"] == 0.0
    fields = {
        "min_range": 1, "max_range": 4, "modifiable_range": False,
        "line_cast": False, "line_of_sight": True, "per_turn": 2, "per_target": 1,
    }
    store.save_profile_spell_fields(spell_id, fields, confirm=True)
    assert store.get_profile_spell(spell_id)["status"] == "Confirmé"
    assert store.get_profile_spell(spell_id)["decision_ready"] == 1
    assert store.known_icons(profile_id)[0].name == "Nom vérifié"
    assert store.list_spells()[0].name == "Sort simulé A"
    with pytest.raises(ValueError, match="a changé"):
        store.save_scan_candidate(profile_id, IconCandidate(2, 4, b"new_png", "b" * 16, 0.9, 0.0, "Inconnu"))
    store.close()
