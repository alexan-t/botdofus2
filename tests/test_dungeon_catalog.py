from pathlib import Path

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from combatbot.gamedata import dungeons
from combatbot.gamedata.dungeons import DungeonRecord, DungeonRoom
from combatbot.ui.dofbot2 import settings
from combatbot.ui.dofbot2.pages import DungeonCatalogDialog


def test_load_dungeons_reads_level_maps_and_room_names(tmp_path, monkeypatch) -> None:
    class FakeD2O:
        def __init__(self, path: Path):
            self.name = path.name

        def objects(self):
            assert self.name == "Dungeons.d2o"
            yield 7, {"id": 7, "nameId": 70, "optimalPlayerLevel": 120,
                      "mapIds": [300.0, 301.0], "entranceMapId": 299.0, "exitMapId": 302.0}
            yield 2, {"id": 2, "nameId": 20, "optimalPlayerLevel": 20,
                      "mapIds": [100.0], "entranceMapId": 99.0, "exitMapId": 101.0}

        def get(self, key: int):
            assert self.name == "MapPositions.d2o"
            return {"nameId": {100: 1000, 300: 3000, 301: 0}[key]}

    class FakeD2I:
        def __init__(self, _path: Path):
            pass

        def text(self, key: int):
            return {20: "Petit donjon", 70: "Grand donjon", 1000: "Salle unique",
                    3000: "Première salle"}.get(key)

    monkeypatch.setattr(dungeons, "D2OFile", FakeD2O)
    monkeypatch.setattr(dungeons, "D2IFile", FakeD2I)
    result = dungeons.load_dungeons(tmp_path)

    assert [item.dungeon_id for item in result] == [2, 7]
    assert result[0].optimal_level == 20
    assert result[0].rooms == (DungeonRoom(100, "Salle unique"),)
    assert result[1].rooms == (DungeonRoom(300, "Première salle"), DungeonRoom(301, None))
    assert (result[1].entrance_map_id, result[1].exit_map_id) == (299, 302)


def test_ui_catalog_prefers_manual_boss_illustration(tmp_path, monkeypatch) -> None:
    client = tmp_path / "client"
    client.mkdir()
    illustrations = tmp_path / "illustrations"
    illustrations.mkdir()
    manual = illustrations / "bouftou_royal.jpg"
    manual.write_bytes(b"fixture")
    record = DungeonRecord(1, "Cour du Bouftou Royal", 30,
                           (DungeonRoom(10, "Première salle"), DungeonRoom(11, None)), 9, 12)

    class FakeStorage:
        def get_setting(self, key: str):
            return str(client) if key == "dofus_client_directory" else None

    monkeypatch.setattr(dungeons, "load_dungeons", lambda _client: (record,))
    monkeypatch.setattr(settings, "_illustration_directories", lambda: (illustrations,))
    places = settings.load_dungeon_places(FakeStorage())

    assert len(places) == 1
    assert places[0].meta == "Niv. 30 · 2 cartes"
    assert places[0].boss == "Bouftou Royal"
    assert places[0].image_path == str(manual)
    assert places[0].map_ids == (10, 11)
    assert places[0].room_names == ("Première salle", "Map 11")


def test_current_plan_uses_saved_real_dungeon() -> None:
    class Values(settings.SettingsBinding):
        def get(self, key: str, default=None):
            return {"plan_mode": "donjon", "planned": True,
                    "dj_place": {"place_id": 33, "name": "Donjon des Larves",
                                 "meta": "Niv. 50 · 15 cartes", "boss": "Shin Larve"}}.get(key, default)

        def set(self, key: str, value) -> None:
            raise AssertionError((key, value))

    plan = settings.current_plan(Values())
    assert plan.planned is True
    assert plan.place.place_id == 33
    assert plan.place.name == "Donjon des Larves"
    assert plan.place.meta == "Niv. 50 · 15 cartes"


def test_recent_dungeons_are_unique_ordered_and_limited() -> None:
    class Values(settings.SettingsBinding):
        def __init__(self):
            self.values = {"recent_dungeon_ids": list(range(1, 9))}

        def get(self, key: str, default=None):
            return self.values.get(key, default)

        def set(self, key: str, value) -> None:
            self.values[key] = value

    values = Values()
    settings.remember_recent_dungeon(values, settings.Place("Donjon", "", place_id=4))
    assert settings.recent_dungeon_ids(values) == (4, 1, 2, 3, 5, 6, 7, 8)


def test_dungeon_catalog_searches_name_level_and_boss() -> None:
    application = QApplication.instance() or QApplication([])
    places = (
        settings.Place("Cour du Bouftou Royal", "Niv. 30 · 5 cartes", "Bouftou Royal", place_id=1),
        settings.Place("Antre du Dragon Cochon", "Niv. 100 · 13 cartes", "Dragon Cochon", place_id=6),
    )
    dialog = DungeonCatalogDialog(places, 0)
    dialog.search.setText("100 cochon")
    application.processEvents()
    assert dialog.result_count.text() == "1 donjon"
    assert not dialog.cards[0].isVisibleTo(dialog)
    assert dialog.cards[1].isVisibleTo(dialog)
    dialog._choose(1)
    assert dialog.selected_index == 1
