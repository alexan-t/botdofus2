"""Catalogue local des donjons déclaré par le client DOFUS.

La table ``Dungeons.d2o`` fournit le niveau conseillé, les cartes du donjon et les
cartes d'entrée/sortie. ``MapPositions.d2o`` et ``i18n_fr.d2i`` complètent les noms
de salles. Le client est toujours lu en lecture seule.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from combatbot.gamedata.errors import GameDataError
from combatbot.gamedata.formats.d2i import D2IFile
from combatbot.gamedata.formats.d2o import D2OFile


@dataclass(frozen=True)
class DungeonRoom:
    map_id: int
    name: str | None = None


@dataclass(frozen=True)
class DungeonRecord:
    dungeon_id: int
    name: str
    optimal_level: int
    rooms: tuple[DungeonRoom, ...]
    entrance_map_id: int
    exit_map_id: int


def load_dungeons(client_root: Path) -> tuple[DungeonRecord, ...]:
    """Lit le catalogue réel du client, sans modifier ses fichiers."""
    data = Path(client_root) / "data"
    dungeons = D2OFile(data / "common" / "Dungeons.d2o")
    positions = D2OFile(data / "common" / "MapPositions.d2o")
    i18n = D2IFile(data / "i18n" / "i18n_fr.d2i")
    position_names: dict[int, str | None] = {}

    def room_name(map_id: int) -> str | None:
        if map_id in position_names:
            return position_names[map_id]
        try:
            position = positions.get(map_id)
        except (GameDataError, KeyError, OSError, ValueError):
            value = None
        else:
            name_id = int(position.get("nameId") or 0)
            value = i18n.text(name_id) if name_id else None
        position_names[map_id] = value
        return value

    records = []
    for _key, raw in dungeons.objects():
        name = i18n.text(int(raw["nameId"]))
        if not name:
            name = f"Donjon {int(raw['id'])}"
        map_ids = tuple(int(value) for value in raw["mapIds"])
        records.append(DungeonRecord(
            dungeon_id=int(raw["id"]),
            name=name,
            optimal_level=int(raw["optimalPlayerLevel"]),
            rooms=tuple(DungeonRoom(map_id, room_name(map_id)) for map_id in map_ids),
            entrance_map_id=int(raw["entranceMapId"]),
            exit_map_id=int(raw["exitMapId"]),
        ))
    return tuple(sorted(records, key=lambda item: (item.optimal_level, item.name.casefold(), item.dungeon_id)))
