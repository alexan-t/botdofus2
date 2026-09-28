"""DLM v11 non chiffré : lecteur strict, réimplémenté indépendamment.

Observé sur le client privé 2.64.5 : 12 154 entrées DLM, toutes v11, zlib,
non chiffrées ; 12 153 lues à l'octet près. Voir GAME-DATA-REAL-VALIDATION.md.
Aucune conversion cellule/pixel, aucune récupération de clé, aucun rendu.

Chaque échec produit un diagnostic DLM_PARSE_ERROR borné (étape, offset,
champ attendu, octets restants, dernier champ validé, 16 octets suivants).
"""
from __future__ import annotations

import struct

from ..errors import GameDataError
from ..models import DofusCellId, GameMap, GameMapCell
from .binary import Reader, map_payload

SUPPORTED_VERSIONS = (11,)
CELL_COUNT = 560  # schema constant; every readable real map ends exactly after 560 records
ABSENT_FLOOR = -128
ABSENT_FLOOR_BYTE = 0x80
CELL_RECORD = struct.Struct(">bHbBB")  # floor, flags, speed, map change, move zone
ELEMENT_SIZES = {2: 19, 33: 18}  # graphical, sound: skipped, never rendered
FIXTURE_BYTES = 18
HEX_PREVIEW = 16


class DlmParseError(GameDataError):
    def __init__(self, code: str, message: str, diagnostic: dict):
        self.diagnostic = diagnostic
        details = " ".join(f"{key}={value}" for key, value in diagnostic.items())
        super().__init__(code, f"{message} — DLM_PARSE_ERROR {details}")


class _Stream:
    """Reader wrapper that remembers the stage/field for forensic errors.

    Labels are formatted only when an error is raised, to keep bulk scans fast.
    """

    _structs: dict[str, struct.Struct] = {}

    def __init__(self, reader: Reader, map_id=None, version=None):
        self.data, self.position = reader.data, reader.position
        self.size = len(self.data)
        self.map_id, self.version = map_id, version
        self.stage, self.index = "envelope", None
        self.last = ("-", None, "")

    @property
    def remaining(self) -> int:
        return self.size - self.position

    def label(self, stage=None, index=None, field="") -> str:
        if stage is None:
            stage, index = self.stage, self.index
        name = stage if index is None else f"{stage}[{index}]"
        return f"{name}.{field}" if field else name

    def error(self, code: str, message: str, expected: str) -> DlmParseError:
        position = self.position
        return DlmParseError(code, message, {
            "map": self.map_id, "version": self.version, "offset": position,
            "stage": self.label(), "expected": expected, "remaining": self.remaining,
            "last": self.label(*self.last) if self.last[0] != "-" else "-",
            "next": bytes(self.data[position:position + HEX_PREVIEW]).hex() or "-",
        })

    def number(self, fmt: str, field: str):
        unpacker = self._structs.get(fmt)
        if unpacker is None:
            unpacker = self._structs[fmt] = struct.Struct(">" + fmt)
        if unpacker.size > self.size - self.position:
            raise self.error("TRUNCATED", "Flux DLM tronqué", f"{field}:{unpacker.size}o")
        value = unpacker.unpack_from(self.data, self.position)[0]
        self.position += unpacker.size
        self.last = (self.stage, self.index, field)
        return value

    def skip(self, count: int, field: str) -> None:
        if count > self.size - self.position:
            raise self.error("TRUNCATED", "Flux DLM tronqué", f"{field}:{count}o")
        self.position += count
        self.last = (self.stage, self.index, field)


def inspect_dlm(data: bytes) -> tuple[dict, Reader]:
    payload, compressed = map_payload(data)
    reader = Reader(payload)
    stream = _Stream(reader)
    if stream.number("c", "magic") != b"M":
        raise stream.error("UNKNOWN_FORMAT", "Magic DLM invalide", "magic=M")
    version = stream.version = stream.number("B", "version")
    map_id = stream.map_id = stream.number("I", "map_id")
    encrypted, encryption_version, data_length = False, None, None
    if version >= 7:
        flag = stream.number("B", "encrypted")
        if flag not in (0, 1):
            raise stream.error("CORRUPT", "Drapeau chiffrement DLM invalide", "encrypted∈{0,1}")
        encrypted = bool(flag)
        encryption_version, data_length = stream.number("B", "encryption_version"), stream.number("i", "data_length")
        if data_length < 0 or (encrypted and data_length != stream.remaining):
            raise stream.error("CORRUPT", "Longueur du contenu chiffré incohérente", f"data_length={stream.remaining}")
        if not encrypted and data_length not in (0, stream.remaining):
            raise stream.error("CORRUPT", "Longueur du contenu DLM incohérente", f"data_length={stream.remaining}")
    reader.position = stream.position
    return {"format": "DLM envelope", "map_id": map_id, "version": version,
            "endian": "big", "compression": "zlib" if compressed else "none",
            "encrypted": encrypted, "encryption_version": encryption_version,
            "declared_data_length": data_length, "compressed_bytes": len(data),
            "decoded_bytes": len(payload)}, reader


def parse_dlm(data: bytes, source: str) -> GameMap:
    envelope, reader = inspect_dlm(data)
    if envelope["version"] not in SUPPORTED_VERSIONS:
        raise GameDataError("UNKNOWN_VERSION", f"DLM v{envelope['version']} : seul le schéma v11 est implémenté")
    if envelope["encrypted"]:
        raise GameDataError("ENCRYPTED", "DLM chiffré : contenu non lu, aucune clé recherchée")
    s = _Stream(reader, envelope["map_id"], envelope["version"])
    s.last = ("envelope", None, "data_length")
    s.stage = "header"
    meta = {
        "relative_id": s.number("I", "relative_id"), "map_type": s.number("B", "map_type"),
        "sub_area_id": s.number("i", "sub_area_id"),
        "top_neighbour_id": s.number("i", "top_neighbour_id"),
        "bottom_neighbour_id": s.number("i", "bottom_neighbour_id"),
        "left_neighbour_id": s.number("i", "left_neighbour_id"),
        "right_neighbour_id": s.number("i", "right_neighbour_id"),
        "shadow_bonus_on_entities": s.number("i", "shadow_bonus_on_entities"),
        "background_color": s.number("I", "background_color"), "grid_color": s.number("I", "grid_color"),
        "zoom_scale": s.number("H", "zoom_scale"), "zoom_offset_x": s.number("h", "zoom_offset_x"),
        "zoom_offset_y": s.number("h", "zoom_offset_y"),
        "tactical_mode_template_id": s.number("i", "tactical_mode_template_id"),
        "envelope": envelope,
    }
    for name in ("background", "foreground"):
        s.stage = name + "_fixtures"
        count = s.number("B", "count")
        meta[f"{name}_fixture_count"] = count
        s.skip(count * FIXTURE_BYTES, f"fixtures[{count}]")
    s.stage, s.index = "ground", None
    meta["unknown_int"] = s.number("i", "unknown_int")
    meta["ground_crc"] = s.number("i", "ground_crc")
    s.stage = "layers"
    layer_count = s.number("B", "layer_count")
    meta["layer_count"] = layer_count
    graphics_cells = elements_total = 0
    layer_cells: dict[int, tuple[int, ...]] = {}
    for layer_index in range(layer_count):
        s.stage, s.index = "layer", layer_index
        layer_id = s.number("B", "layer_id")
        graphics_count = s.number("H", "cell_count")
        if graphics_count > CELL_COUNT:
            raise s.error("CORRUPT", "Trop de cellules graphiques", f"cell_count<={CELL_COUNT}")
        graphics_ids = set()
        for _ in range(graphics_count):
            s.stage = "layer.graphical_cell"
            cell_id, elements = s.number("H", "cell_id"), s.number("H", "element_count")
            if cell_id >= CELL_COUNT or cell_id in graphics_ids:
                raise s.error("CORRUPT", "Identifiant cellule graphique invalide", f"cell_id<{CELL_COUNT}, unique")
            graphics_ids.add(cell_id)
            s.stage = "layer.graphical_element"
            for _ in range(elements):
                kind = s.number("B", "element_type")
                if kind not in ELEMENT_SIZES:
                    raise s.error("UNKNOWN_ELEMENT", f"Élément DLM inconnu : {kind}", "element_type∈{2,33}")
                s.skip(ELEMENT_SIZES[kind], "graphical" if kind == 2 else "sound")
            elements_total += elements
        graphics_cells += graphics_count
        layer_cells[layer_id] = tuple(sorted(set(layer_cells.get(layer_id, ())) | graphics_ids))
    meta["graphical_cells"], meta["graphical_elements"] = graphics_cells, elements_total
    # Cellules portant au moins un élément graphique, par calque (0 = sol) : empreinte de forme.
    meta["layer_cells"] = layer_cells
    cells = []
    data = s.data
    s.stage = "cell"
    for identifier in range(CELL_COUNT):
        s.index = identifier
        position = s.position
        if s.size - position >= CELL_RECORD.size and data[position] != ABSENT_FLOOR_BYTE:
            # Fast path, same fields as the forensic path below.
            floor, flags, speed, map_change, move_zone = CELL_RECORD.unpack_from(data, position)
            s.position = position + CELL_RECORD.size
            s.last = ("cell", identifier, "move_zone")
        else:
            floor = s.number("b", "floor")
            if floor == ABSENT_FLOOR:
                cells.append(GameMapCell(DofusCellId(identifier)))
                continue
            flags = s.number("H", "flags")
            speed = s.number("b", "speed")
            map_change = s.number("B", "map_change_data")
            move_zone = s.number("B", "move_zone")
        linked_zone = None
        # Linked-zone byte present only for movable (bit 0 clear), non-farm (bit 7 clear) cells.
        if not (flags & 1) and not (flags & 128):
            linked_zone = s.number("B", "linked_zone")
        cells.append(GameMapCell(
            DofusCellId(identifier), walkable=not bool(flags & 1),
            line_of_sight=not bool(flags & 8), floor=floor * 10,
            non_walkable_during_fight=bool(flags & 2),
            non_walkable_during_roleplay=bool(flags & 4), raw_flags=flags,
            blue_hint=bool(flags & 16), red_hint=bool(flags & 32),
            speed=speed, map_change_data=map_change, move_zone=move_zone, linked_zone=linked_zone,
        ))
    if s.remaining:
        s.stage, s.index = "end_of_stream", None
        raise s.error("UNKNOWN_LAYOUT", f"DLM : {s.remaining} octets résiduels, schéma non validé", "end_of_stream")
    return GameMap(envelope["map_id"], tuple(cells), source, envelope["version"], metadata=meta, warnings=(
        "Schéma v11 lu à l'octet près ; sémantique de jeu des drapeaux non certifiée.",
        "560 indices implicites dans l'ordre du flux (DofusCellId), distincts de toute cellule visuelle.",
        "Rouge/bleu et interdiction de déplacement en combat ne prouvent pas les placements d'équipe.",
    ))
