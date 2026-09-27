import sqlite3

from combatbot import runtime
from combatbot.storage import Storage


def test_packaged_storage_migrates_legacy_database_once(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    executable = project / "dist" / "DofBot2" / "DofBot2.exe"
    executable.parent.mkdir(parents=True)
    source = project / "data" / "pythonbot.sqlite3"
    source.parent.mkdir(parents=True)
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
    connection.execute("INSERT INTO settings VALUES ('player_name', '\"Profil conservé\"')")
    connection.commit()
    connection.close()

    destination_root = tmp_path / "LocalAppData" / "PythonBot"
    monkeypatch.setenv("PYTHONBOT_DATA_DIR", str(destination_root))
    monkeypatch.setattr(runtime, "is_frozen", lambda: True)
    monkeypatch.setattr(runtime, "executable_path", lambda: executable)

    store = Storage()
    assert store.path == destination_root / "data" / "pythonbot.sqlite3"
    assert store.get_setting("player_name") == "Profil conservé"
    assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert store.backup_path is not None and store.backup_path.exists()
    store.close()

    source.unlink()
    reopened = Storage()
    assert reopened.get_setting("player_name") == "Profil conservé"
    assert reopened.backup_path is None
    reopened.close()


def test_development_paths_stay_in_project(monkeypatch) -> None:
    monkeypatch.delenv("PYTHONBOT_DATA_DIR", raising=False)
    monkeypatch.setattr(runtime, "is_frozen", lambda: False)
    assert runtime.database_path() == runtime.PROJECT_ROOT / "data" / "pythonbot.sqlite3"
    assert runtime.log_directory() == runtime.PROJECT_ROOT / "logs"
