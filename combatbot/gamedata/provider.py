"""Provider offline et cache d'index ; aucune dépendance au moteur de combat."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import json
from pathlib import Path, PurePosixPath
import time
import tracemalloc
from typing import Protocol

from .discovery import INTERESTING, discover_client, within_client
from .errors import GameDataError
from .formats.archives import inspect_d2o, read_d2p_index
from .formats.binary import MAX_MAP_BYTES
from .formats.maps import inspect_dlm, parse_dlm
from .models import GameMap, GameMapCell, GridTopology, ScanReport
from .topology import COORDINATES_VERIFIED as TOPOLOGY_VERIFIED, build_topology

CACHE_SCHEMA = "gamedata-probe-v4-d2p-layouts"
MAX_CACHE_BYTES = 32 * 1024 * 1024


class GameDataProvider(Protocol):
    def scan_client(self) -> ScanReport: ...
    def list_maps(self) -> tuple[int, ...]: ...
    def get_map(self, map_id: int) -> GameMap: ...
    def get_map_cells(self, map_id: int) -> tuple[GameMapCell, ...]: ...
    def get_fight_cells(self, map_id: int) -> tuple[GameMapCell, ...] | None: ...
    def get_map_topology(self, map_id: int) -> GridTopology: ...


class LocalGameDataProvider:
    def __init__(self, folder: str | Path | None = None, *, cache_dir: Path | None = None):
        self.folder = folder
        self.cache_dir = cache_dir
        self.report = ScanReport()
        self._root: Path | None = None
        self._maps: dict[int, list[dict]] = {}
        self._fingerprints: dict[str, tuple[int, int]] = {}
        self._map_cache: OrderedDict[int, GameMap] = OrderedDict()
        self._readable: set[int] = set()

    def scan_client(self) -> ScanReport:
        started = time.perf_counter()
        owns_trace = not tracemalloc.is_tracing()
        if owns_trace:
            tracemalloc.start()
        try:
            self._scan()
            return self.report
        finally:
            self.report.scan_seconds = time.perf_counter() - started
            self.report.python_peak_bytes = tracemalloc.get_traced_memory()[1]
            if owns_trace:
                tracemalloc.stop()

    def _scan(self) -> None:
        self._maps.clear()
        self._map_cache.clear()
        self._readable.clear()
        self._fingerprints.clear()
        self._root = None
        self.report = discover_client(self.folder)
        if self.report.status not in ("SCANNED", "EMPTY"):
            return
        self._root = Path(self.report.client_path)
        interesting = [entry for entry in self.report.files if entry.extension in INTERESTING]
        self._fingerprints = {entry.relative_path: (entry.size, entry.mtime_ns) for entry in interesting}
        fingerprint = hashlib.sha256(json.dumps(self._fingerprints, sort_keys=True).encode()).hexdigest()
        start = time.perf_counter()
        cache = self._load_index_cache(fingerprint)
        if cache is not None:
            self._maps = {int(key): value for key, value in cache["maps"].items()}
            self.report.formats = cache["formats"]
            self.report.format_details = cache["format_details"]
            self.report.errors.extend(cache["errors"])
            self.report.index_cache_hit = True
        else:
            parse_errors = []
            for entry in interesting:
                try:
                    path = within_client(self._root, entry.relative_path)
                    if entry.extension == ".d2p":
                        index = read_d2p_index(path)
                        self._format("D2P 2.1 index")
                        self.report.format_details[entry.relative_path] = {
                            "format": "D2P 2.1", "endian": "big", "index_valid": True,
                            "entry_count": len(index["entries"]), "properties": index["properties"],
                            "layout": index["layout"], "footer": index["footer"],
                            "regions": index["regions"],
                            "data_unreferenced_bytes": index["data_unreferenced_bytes"],
                            "dlm_entries": sum(name.lower().endswith(".dlm") for name in index["entries"]),
                        }
                        for name, position in index["entries"].items():
                            stem = PurePosixPath(name).stem
                            if name.lower().endswith(".dlm") and stem.isascii() and stem.isdecimal():
                                self._add_map(int(stem), {"path": entry.relative_path, "entry": name, **position})
                    elif entry.extension == ".d2o":
                        self.report.format_details[entry.relative_path] = inspect_d2o(path)
                        self._format("D2O index")
                    else:
                        if entry.size > MAX_MAP_BYTES:
                            raise GameDataError("LIMIT", "Map trop volumineuse")
                        with path.open("rb") as stream:
                            metadata, _ = inspect_dlm(stream.read(MAX_MAP_BYTES + 1))
                        self.report.format_details[entry.relative_path] = metadata
                        self._format("DLM envelope")
                        self._add_map(metadata["map_id"], {"path": entry.relative_path, "entry": None,
                                                         "offset": 0, "length": entry.size})
                except (OSError, ValueError) as exc:
                    parse_errors.append(f"{entry.relative_path}: {exc}")
            self.report.errors.extend(parse_errors)
            self._save_index_cache(fingerprint, parse_errors)
        self.report.index_seconds = time.perf_counter() - start
        self.report.indexed_maps = len(self._maps)
        self.report.readable_maps = None  # an index entry is not a parsed map
        for map_id, records in self._maps.items():
            if len(records) > 1:
                self.report.errors.append(f"Map {map_id} ambiguë : {len(records)} sources, chargement refusé")
        self.report.verdict_reason = (
            "Index disponible ; aucune topologie complète ni compatibilité 2.64.5 démontrée."
            if self._maps else "Aucune map indexée exploitable dans le dossier sélectionné."
        )

    def _format(self, name: str) -> None:
        self.report.formats[name] = self.report.formats.get(name, 0) + 1

    def _add_map(self, map_id: int, record: dict) -> None:
        self._maps.setdefault(map_id, []).append(record)

    def _cache_path(self) -> Path | None:
        if self.cache_dir is None or self._root is None:
            return None
        key = hashlib.sha256(str(self._root).encode()).hexdigest()[:20]
        return self.cache_dir / f"index_{key}.json"

    def _load_index_cache(self, fingerprint: str) -> dict | None:
        path = self._cache_path()
        if path is None:
            return None
        try:
            if not path.is_file() or path.stat().st_size > MAX_CACHE_BYTES:
                return None
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("schema") != CACHE_SCHEMA or raw.get("root") != str(self._root) or raw.get("fingerprint") != fingerprint:
                return None
            if not isinstance(raw.get("maps"), dict) or not isinstance(raw.get("formats"), dict):
                return None
            if not isinstance(raw.get("errors"), list):
                return None
            if not isinstance(raw.get("format_details"), dict):
                return None
            # Cache is an optimisation, not an authority for paths or dimensions.
            for key, records in raw["maps"].items():
                if not key.isdecimal() or not isinstance(records, list) or not records:
                    return None
                for record in records:
                    if not isinstance(record, dict) or "entry" not in record:
                        return None
                    if record["entry"] is not None and not isinstance(record["entry"], str):
                        return None
                    if record["path"] not in self._fingerprints:
                        return None
                    size = self._fingerprints[record["path"]][0]
                    offset, length = record["offset"], record["length"]
                    if type(offset) is not int or type(length) is not int or offset < 0 or length < 0 or offset + length > size:
                        return None
            return raw
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return None

    def _save_index_cache(self, fingerprint: str, errors: list[str]) -> None:
        path = self._cache_path()
        if path is None:
            return
        try:
            self._write_json(path, {"schema": CACHE_SCHEMA, "root": str(self._root), "fingerprint": fingerprint,
                                    "maps": self._maps, "formats": self.report.formats,
                                    "format_details": self.report.format_details, "errors": errors})
        except (OSError, ValueError) as exc:
            self.report.errors.append(f"Cache non enregistré : {exc}")

    def list_maps(self) -> tuple[int, ...]:
        """IDs candidats ; les noms D2P sont vérifiés contre l'en-tête au chargement."""
        return tuple(sorted(self._maps))

    def map_source(self, map_id: int) -> str | None:
        records = self._maps.get(map_id) or []
        if len(records) != 1:
            return None
        record = records[0]
        return record["path"] + (f"::{record['entry']}" if record["entry"] else "")

    def get_map(self, map_id: int, *, track: bool = True) -> GameMap:
        """track=False: bulk validation, no report/cache side effects."""
        if not track:
            return self._get_map(map_id, track=False)
        started = time.perf_counter()
        try:
            return self._get_map(map_id)
        except (OSError, ValueError) as exc:
            error = exc if isinstance(exc, GameDataError) else GameDataError("READ_FAILED", f"Lecture locale impossible : {exc}")
            if type(map_id) is int:
                self._map_cache.pop(map_id, None)
                was_readable = map_id in self._readable
                self._readable.discard(map_id)
                self.report.tested_maps[map_id] = {"map_parsed": False, "code": error.code, "error": str(error)}
                if was_readable:
                    self.report.readable_maps = len(self._readable)
                    self.report.cells_readable = bool(self._readable)
                    self.report.map_parsed = bool(self._readable)
                    if not self._readable:
                        self.report.verdict = "NO"
                        self.report.verdict_reason = "Les données précédemment lues ne sont plus valides ; relancez Analyser."
            if error is exc:
                raise
            raise error from exc
        finally:
            self.report.last_map_seconds = time.perf_counter() - started

    def _get_map(self, map_id: int, *, track: bool = True) -> GameMap:
        if type(map_id) is not int or map_id < 0:
            raise GameDataError("INVALID_MAP_ID", "Map ID : entier positif ou nul attendu")
        if self._root is None:
            raise GameDataError("NOT_CONFIGURED", self.report.message)
        records = self._maps.get(map_id)
        if not records:
            raise GameDataError("MAP_NOT_FOUND", f"Map ID {map_id} absent de l'index")
        if len(records) != 1:
            raise GameDataError("AMBIGUOUS_MAP", f"Map ID {map_id} : plusieurs sources, aucun choix implicite")
        record = records[0]
        path = within_client(self._root, record["path"])
        info = path.stat()
        if (info.st_size, info.st_mtime_ns) != self._fingerprints[record["path"]]:
            self._map_cache.pop(map_id, None)
            raise GameDataError("STALE_INDEX", "Fichier client modifié ; relancez Analyser")
        if track and map_id in self._map_cache:
            self._map_cache.move_to_end(map_id)
            return self._map_cache[map_id]
        if record["length"] > MAX_MAP_BYTES:
            raise GameDataError("LIMIT", "Map trop volumineuse")
        with path.open("rb") as stream:
            stream.seek(record["offset"])
            data = stream.read(record["length"])
        if len(data) != record["length"]:
            raise GameDataError("TRUNCATED", "Entrée map tronquée")
        source = record["path"] + (f"::{record['entry']}" if record["entry"] else "")
        result = parse_dlm(data, source)
        if result.map_id != map_id:
            raise GameDataError("MAP_ID_MISMATCH", "Le map ID du nom de fichier diffère de l'en-tête DLM")
        after = path.stat()
        if (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise GameDataError("STALE_INDEX", "Fichier client modifié pendant la lecture")
        if not track:
            return result
        self._map_cache[map_id] = result
        while len(self._map_cache) > 8:
            self._map_cache.popitem(last=False)
        self._readable.add(map_id)
        self.report.tested_maps[map_id] = {**result.metadata["envelope"], **result.summary(), "source": source}
        self.report.readable_maps = len(self._readable)
        self.report.cells_readable = True
        self.report.map_parsed = True
        self.report.verdict = "PARTIAL"
        self.report.verdict_reason = "Cell IDs et drapeaux lus (schéma v11 exact) ; placements d'équipe non démontrés."
        return result

    def get_map_cells(self, map_id: int) -> tuple[GameMapCell, ...]:
        return self.get_map(map_id).cells

    def get_fight_cells(self, map_id: int) -> tuple[GameMapCell, ...] | None:
        cells = self.get_map_cells(map_id)
        if any(cell.fight_start_allowed is None for cell in cells):
            return None
        return tuple(cell for cell in cells if cell.fight_start_allowed)

    def get_map_topology(self, map_id: int) -> GridTopology:
        """Logical topology (topology.py). coordinates_verified reflects the
        real-client validation of LOT 3B-2A-R; it says nothing about pixels."""
        return build_topology(self.get_map(map_id), coordinates_verified=TOPOLOGY_VERIFIED)

    def _write_json(self, destination: Path, value: dict) -> Path:
        target = Path(destination).resolve()
        if self._root is not None and target.is_relative_to(self._root):
            raise GameDataError("READ_ONLY_CLIENT", "Écriture interdite dans le dossier client, y compris cache et exports")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        # Resolve the temporary path too: do not follow a pre-existing link.
        if temporary.exists() or temporary.is_symlink():
            raise GameDataError("EXPORT_BUSY", "Fichier temporaire déjà présent ; choisissez un autre export")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False, indent=2)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def export_json(self, destination: Path, value: dict) -> Path:
        return self._write_json(destination, value)

    def export_report(self, destination: Path) -> Path:
        return self._write_json(destination, self.report.to_dict())

    def export_map(self, map_id: int, destination: Path) -> Path:
        return self._write_json(destination, self.get_map(map_id).to_dict())
