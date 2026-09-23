"""Index D2P 2.1 / D2O ; aucun chargement global des archives.

Deux dispositions D2P sont démontrées sur le client privé 2.64.5 (248/248 archives) :

- DATA_BEFORE_INDEX : ``02 01 | données | index | propriétés | footer`` ;
  footer = (début données, taille données, début index, nb entrées,
  début propriétés, nb propriétés).
- INDEX_BEFORE_DATA : ``02 01 | propriétés | index | données | footer`` ;
  footer = (début données ou 0 si archive vide, nb entrées, début index,
  nb entrées, début propriétés, fin propriétés ou nb propriétés).

La variante est déduite des offsets du footer, jamais du nom du fichier.
Toute autre disposition est refusée (UNKNOWN_LAYOUT).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
import struct

from ..errors import GameDataError
from .binary import MAX_INDEX_BYTES, Reader

D2P_HEADER = b"\x02\x01"
D2P_FOOTER_BYTES = 24
MAX_D2P_ENTRIES = 200_000
MAX_D2P_PROPERTIES = 10_000
MIN_INDEX_ENTRY_BYTES = 2 + 8  # empty UTF name + offset + length


def safe_entry_name(name: str) -> str:
    path = PurePosixPath(name)
    if not name or "\\" in name or ":" in name or "\x00" in name or path.is_absolute() or ".." in path.parts:
        raise GameDataError("CORRUPT", "Nom d'entrée archive invalide")
    return name


class D2PLayoutVariant(str, Enum):
    DATA_BEFORE_INDEX = "DATA_BEFORE_INDEX"
    INDEX_BEFORE_DATA = "INDEX_BEFORE_DATA"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class D2PRegion:
    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class D2PLayout:
    file_size: int
    footer: tuple[int, ...]
    variant: D2PLayoutVariant
    data_region: D2PRegion
    index_region: D2PRegion
    property_region: D2PRegion
    entry_count: int
    property_field: int

    def to_dict(self) -> dict:
        value = asdict(self)
        value["variant"] = self.variant.value
        return value


def detect_d2p_layout(file_size: int, footer: tuple[int, ...]) -> D2PLayout:
    """Pure detection from the six footer fields; every region tiles the file."""
    if len(footer) != 6 or file_size < len(D2P_HEADER) + D2P_FOOTER_BYTES:
        raise GameDataError("TRUNCATED", "D2P trop court pour son footer")
    f0, f1, f2, f3, f4, f5 = footer
    header, end = len(D2P_HEADER), file_size - D2P_FOOTER_BYTES
    if f0 == header and f2 == f0 + f1 and f2 <= f4 <= end:
        return D2PLayout(file_size, tuple(footer), D2PLayoutVariant.DATA_BEFORE_INDEX,
                         D2PRegion(f0, f2), D2PRegion(f2, f4), D2PRegion(f4, end), f3, f5)
    if f4 == header and f4 <= f2 <= end and f1 == f3:
        if f0 == 0 and f3 == 0 and f2 == end:
            # Empty chained archive observed: no data region at all.
            data, index = D2PRegion(end, end), D2PRegion(f2, end)
        elif f2 <= f0 <= end:
            data, index = D2PRegion(f0, end), D2PRegion(f2, f0)
        else:
            data = None
        if data is not None:
            return D2PLayout(file_size, tuple(footer), D2PLayoutVariant.INDEX_BEFORE_DATA,
                             data, index, D2PRegion(f4, f2), f3, f5)
    raise GameDataError("UNKNOWN_LAYOUT", f"Disposition D2P non démontrée : footer={list(footer)}, taille={file_size}")


def read_d2p_layout(path: Path) -> D2PLayout:
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        if size < len(D2P_HEADER) + D2P_FOOTER_BYTES:
            raise GameDataError("TRUNCATED", "D2P trop court pour son footer")
        stream.seek(0)
        if stream.read(2) != D2P_HEADER:
            raise GameDataError("UNKNOWN_VERSION", "Magic/version D2P autre que 2.1")
        stream.seek(size - D2P_FOOTER_BYTES)
        return detect_d2p_layout(size, struct.unpack(">6I", stream.read(D2P_FOOTER_BYTES)))


def read_d2p_index(path: Path) -> dict:
    layout = read_d2p_layout(path)
    count, index_region = layout.entry_count, layout.index_region
    if count > MAX_D2P_ENTRIES or index_region.size > MAX_INDEX_BYTES or layout.property_region.size > MAX_INDEX_BYTES:
        raise GameDataError("LIMIT", "Index D2P trop volumineux")
    if count * MIN_INDEX_ENTRY_BYTES > index_region.size:
        raise GameDataError("CORRUPT", "Région d'index D2P trop petite pour le nombre d'entrées")
    with path.open("rb") as stream:
        stream.seek(index_region.start)
        index_bytes = stream.read(index_region.size)
        stream.seek(layout.property_region.start)
        property_bytes = stream.read(layout.property_region.size)
    reader = Reader(index_bytes)
    entries, spans = {}, []
    data_size = layout.data_region.size
    for _ in range(count):
        name = safe_entry_name(reader.utf())
        offset, entry_length = reader.number("i"), reader.number("i")
        if name in entries:
            raise GameDataError("CORRUPT", f"Entrée D2P dupliquée : {name}")
        # Offsets are relative to the data region, whatever its position.
        if offset < 0 or entry_length < 0 or offset > data_size or entry_length > data_size - offset:
            raise GameDataError("CORRUPT", f"Entrée D2P hors de la région de données : {name}")
        entries[name] = {"offset": layout.data_region.start + offset, "length": entry_length}
        spans.append((offset, offset + entry_length))
    if reader.remaining:
        raise GameDataError("CORRUPT", f"Index D2P non consommé exactement : {reader.remaining} octets restants")
    spans.sort()
    referenced, previous_end = 0, 0
    for start, end in spans:
        if start < previous_end and end > start:
            raise GameDataError("CORRUPT", "Entrées D2P chevauchantes")
        referenced += end - start
        previous_end = max(previous_end, end)
    reader = Reader(property_bytes)
    props = {}
    while reader.remaining:
        key, value = reader.utf(), reader.utf()
        if key in props or len(props) >= MAX_D2P_PROPERTIES:
            raise GameDataError("CORRUPT", "Propriété D2P dupliquée ou trop nombreuse")
        props[key] = value
    if layout.variant is D2PLayoutVariant.DATA_BEFORE_INDEX:
        valid_property_field = layout.property_field == len(props)
    else:
        valid_property_field = layout.property_field in (len(props), layout.property_region.end)
    if not valid_property_field:
        raise GameDataError("CORRUPT", "Champ propriétés du footer D2P incohérent")
    # Linked archives are inventory members only; properties never open paths.
    return {"format": "D2P 2.1", "endian": "big", "entries": entries, "properties": props,
            "layout": layout.variant.value, "footer": list(layout.footer), "regions": layout.to_dict(),
            "data_unreferenced_bytes": data_size - referenced}


def inspect_d2o(path: Path) -> dict:
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(0)
        header = stream.read(7)
        if len(header) != 7:
            raise GameDataError("TRUNCATED", "En-tête D2O tronqué")
        if header[:3] != b"D2O":
            raise GameDataError("UNKNOWN_FORMAT", "Magic D2O non reconnu (enveloppes non prises en charge)")
        offset = struct.unpack(">I", header[3:])[0]
        if not 7 <= offset <= size - 4:
            raise GameDataError("CORRUPT", "Offset index D2O hors fichier")
        stream.seek(offset)
        length = struct.unpack(">I", stream.read(4))[0]
        if length % 8 or length > MAX_INDEX_BYTES or offset + 4 + length > size:
            raise GameDataError("CORRUPT", "Taille index D2O incohérente")
        reader = Reader(stream.read(length))
        identifiers = set()
        for _ in range(length // 8):
            identifier, position = reader.number("i"), reader.number("I")
            if identifier in identifiers or not 7 <= position < offset:
                raise GameDataError("CORRUPT", "Objet D2O dupliqué ou hors zone de données")
            identifiers.add(identifier)
        return {"format": "D2O index", "endian": "big", "object_count": len(identifiers),
                "classes_decoded": False}
