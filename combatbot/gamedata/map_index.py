"""LOT 3B-6C : index spatial offline des maps du client (lecture seule).

Sources locales : MapPositions.d2o (coordonnées monde, sous-zone, worldMap, outdoor), SubAreas.d2o
(zone, niveau), Areas.d2o, i18n_fr.d2i (noms affichés à l'écran), en-têtes DLM (voisins haut/bas/
gauche/droite) et MapScrollActions.d2o (voisins surchargés). L'index est mis en cache dans le
runtime PythonBot et reconstruit dès qu'un fichier source change (empreinte taille + mtime).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import time
import unicodedata

INDEX_SCHEMA = "map-spatial-index-v1"
SOURCE_TABLES = ("common/MapPositions.d2o", "common/SubAreas.d2o", "common/Areas.d2o",
                 "common/MapScrollActions.d2o", "i18n/i18n_fr.d2i")


@dataclass(frozen=True)
class MapRecord:
    map_id: int
    x: int
    y: int
    world_map: int
    sub_area_id: int
    area_id: int | None
    level: int | None
    outdoor: bool
    is_transition: bool
    sub_area_name: str | None
    area_name: str | None
    neighbours: tuple[int, ...] = ()


def normalize_name(text: str | None) -> str:
    """Casse, accents, apostrophes typographiques et espaces normalisés (comparaison OCR)."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text.replace("’", "'").replace("`", "'"))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.lower().replace("(", " ").replace(")", " ").split())


def name_similarity(a: str | None, b: str | None) -> float:
    """Ratio de Levenshtein sur les noms normalisés (1.0 = identiques)."""
    left, right = normalize_name(a), normalize_name(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    previous = list(range(len(right) + 1))
    for i, char_a in enumerate(left, 1):
        current = [i]
        for j, char_b in enumerate(right, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return 1.0 - previous[-1] / max(len(left), len(right))


class MapSpatialIndex:
    def __init__(self, records: dict[int, MapRecord] | list[MapRecord], *, fingerprint: str = "",
                 built_at: str = "") -> None:
        values = records.values() if isinstance(records, dict) else records
        self.maps: dict[int, MapRecord] = {record.map_id: record for record in values}
        self.fingerprint = fingerprint
        self.built_at = built_at
        self._by_coords: dict[tuple[int, int], tuple[int, ...]] = {}
        self._by_subarea: dict[int, tuple[int, ...]] = {}
        coords, subareas = defaultdict(list), defaultdict(list)
        for record in self.maps.values():
            coords[(record.x, record.y)].append(record.map_id)
            subareas[record.sub_area_id].append(record.map_id)
        self._by_coords = {key: tuple(sorted(value)) for key, value in coords.items()}
        self._by_subarea = {key: tuple(sorted(value)) for key, value in subareas.items()}

    def __len__(self) -> int:
        return len(self.maps)

    def get_map(self, map_id: int) -> MapRecord | None:
        return self.maps.get(int(map_id))

    def candidates_by_coords(self, x: int, y: int) -> tuple[MapRecord, ...]:
        return tuple(self.maps[item] for item in self._by_coords.get((int(x), int(y)), ()))

    def candidates_by_subarea(self, sub_area_id: int) -> tuple[MapRecord, ...]:
        return tuple(self.maps[item] for item in self._by_subarea.get(int(sub_area_id), ()))

    def neighbours(self, map_id: int) -> tuple[int, ...]:
        record = self.maps.get(int(map_id))
        return record.neighbours if record else ()

    def world_context(self, map_id: int) -> dict[str, object] | None:
        record = self.maps.get(int(map_id))
        if record is None:
            return None
        return {"world_map": record.world_map, "sub_area_id": record.sub_area_id, "area_id": record.area_id,
                "sub_area_name": record.sub_area_name, "area_name": record.area_name, "level": record.level,
                "outdoor": record.outdoor}

    # ------------------------------------------------------------------ persistance
    def to_dict(self) -> dict[str, object]:
        return {"schema": INDEX_SCHEMA, "fingerprint": self.fingerprint, "built_at": self.built_at,
                "maps": [asdict(record) for record in self.maps.values()]}

    @classmethod
    def from_dict(cls, raw: dict) -> "MapSpatialIndex":
        if raw.get("schema") != INDEX_SCHEMA:
            raise ValueError("Schéma d'index de maps inconnu")
        records = [MapRecord(**{**item, "neighbours": tuple(item.get("neighbours") or ())}) for item in raw["maps"]]
        return cls(records, fingerprint=str(raw.get("fingerprint", "")), built_at=str(raw.get("built_at", "")))


def source_fingerprint(client_root: Path) -> str:
    """Empreinte des tables sources + archives de maps : tout changement invalide l'index."""
    data = Path(client_root) / "data"
    parts = []
    for relative in SOURCE_TABLES:
        path = data / relative
        stat = path.stat()
        parts.append((relative, stat.st_size, stat.st_mtime_ns))
    maps = Path(client_root) / "content" / "maps"
    for path in sorted(maps.glob("*.d2p")) if maps.is_dir() else ():
        stat = path.stat()
        parts.append((path.name, stat.st_size, stat.st_mtime_ns))
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def build_index(client_root: Path, *, with_neighbours: bool = True, progress=None) -> MapSpatialIndex:
    """Construit l'index depuis les fichiers du client (≈ 1 min avec les voisins DLM)."""
    from combatbot.gamedata.formats.d2i import D2IFile
    from combatbot.gamedata.formats.d2o import D2OFile
    client_root = Path(client_root)
    data = client_root / "data"
    fingerprint = source_fingerprint(client_root)
    i18n = D2IFile(data / "i18n" / "i18n_fr.d2i")
    sub_areas = {int(o["id"]): o for _key, o in D2OFile(data / "common" / "SubAreas.d2o").objects()}
    areas = {int(o["id"]): o for _key, o in D2OFile(data / "common" / "Areas.d2o").objects()}
    neighbours: dict[int, set[int]] = defaultdict(set)
    if with_neighbours:
        from combatbot.gamedata.provider import LocalGameDataProvider
        provider = LocalGameDataProvider(client_root)
        provider.scan_client()
        for index, map_id in enumerate(provider.list_maps()):
            try:
                metadata = provider.get_map(map_id, track=False).metadata
            except (OSError, ValueError):
                continue
            for side in ("top", "bottom", "left", "right"):
                target = metadata.get(f"{side}_neighbour_id")
                if isinstance(target, int) and target > 0:
                    neighbours[map_id].add(target)
            if progress and index % 1000 == 0:
                progress(index)
        try:
            for _key, obj in D2OFile(data / "common" / "MapScrollActions.d2o").objects():
                for side in ("top", "bottom", "left", "right"):
                    if obj.get(f"{side}Exists"):
                        neighbours[int(obj["id"])].add(int(obj[f"{side}MapId"]))
        except (OSError, ValueError, KeyError):
            pass
    records = []
    for _key, obj in D2OFile(data / "common" / "MapPositions.d2o").objects():
        map_id = int(obj["id"])
        sub_area = sub_areas.get(int(obj["subAreaId"]))
        area = areas.get(int(sub_area["areaId"])) if sub_area else None
        records.append(MapRecord(
            map_id, int(obj["posX"]), int(obj["posY"]), int(obj["worldMap"]), int(obj["subAreaId"]),
            int(sub_area["areaId"]) if sub_area else None, int(sub_area["level"]) if sub_area else None,
            bool(obj["outdoor"]), bool(obj["isTransition"]),
            i18n.text(sub_area["nameId"]) if sub_area else None, i18n.text(area["nameId"]) if area else None,
            tuple(sorted(neighbours.get(map_id, ())))))
    return MapSpatialIndex(records, fingerprint=fingerprint,
                           built_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))


def load_or_build(client_root: Path, cache_dir: Path, *, progress=None) -> MapSpatialIndex:
    """Index en cache s'il correspond à l'empreinte actuelle du client, sinon reconstruit."""
    cache_dir = Path(cache_dir)
    path = cache_dir / "map_spatial_index.json"
    fingerprint = source_fingerprint(client_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("fingerprint") == fingerprint:
            return MapSpatialIndex.from_dict(raw)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    index = build_index(client_root, progress=progress)
    cache_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(index.to_dict(), ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    return index


def ambiguity_report(index: MapSpatialIndex) -> dict[str, object]:
    """Nombre de candidats par clé (coords seules, + worldMap, + niveau, + sous-zone)."""
    def histogram(key) -> dict[str, object]:
        groups: dict[object, int] = Counter(key(record) for record in index.maps.values())
        buckets = Counter()
        maps = Counter()
        for size in groups.values():
            bucket = "1" if size == 1 else "2" if size == 2 else "3" if size == 3 else "4-9" if size < 10 else "10+"
            buckets[bucket] += 1
            maps[bucket] += size
        return {"keys": len(groups), "keys_by_candidate_count": dict(sorted(buckets.items())),
                "maps_by_candidate_count": dict(sorted(maps.items())),
                "unique_map_share": round(maps["1"] / max(1, len(index)), 4)}
    return {
        "total_maps": len(index),
        "xy": histogram(lambda r: (r.x, r.y)),
        "xy_world": histogram(lambda r: (r.x, r.y, r.world_map)),
        "xy_world_level": histogram(lambda r: (r.x, r.y, r.world_map, r.level)),
        "xy_subarea": histogram(lambda r: (r.x, r.y, r.sub_area_id)),
        # Ce que l'écran affiche : zone + sous-zone + coordonnées + niveau.
        "xy_displayed_names": histogram(lambda r: (r.x, r.y, normalize_name(r.area_name),
                                                   normalize_name(r.sub_area_name), r.level)),
        "maps_with_neighbours": sum(bool(r.neighbours) for r in index.maps.values()),
    }
