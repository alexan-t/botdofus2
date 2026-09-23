"""Schéma de classes D2O et décodage borné d'objets, réimplémenté indépendamment.

Structure lue (big-endian) : ``D2O`` | offset index (u32) | objets | index
(taille u32 puis couples id/offset) | nb classes | définitions de classes
(id, nom, paquet, champs nom/type, types de vecteur). Un bloc de recherche
éventuel après les classes est ignoré et signalé.

Aucune donnée D2O n'est écrite ; seuls les champs demandés sont décodés.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import struct

from ..errors import GameDataError
from .binary import MAX_INDEX_BYTES, Reader

MAX_D2O_BYTES = 256 * 1024 * 1024
MAX_CLASSES = 4096
MAX_FIELDS = 1024
MAX_VECTOR_ITEMS = 1_000_000
MAX_DEPTH = 16
NULL_OBJECT = -1431655766  # 0xAAAAAAAA as signed int32

PRIMITIVES = {-1: "int", -2: "bool", -3: "string", -4: "number", -5: "i18n", -6: "uint"}
VECTOR = -99


@dataclass(frozen=True)
class D2OField:
    name: str
    type_id: int
    # For vectors: nested (type_name, type_id) pairs, outermost first.
    vector_types: tuple[tuple[str, int], ...] = ()

    def type_name(self) -> str:
        if self.type_id == VECTOR:
            return "vector<" + ",".join(name for name, _ in self.vector_types) + ">"
        return PRIMITIVES.get(self.type_id, f"object#{self.type_id}")


@dataclass(frozen=True)
class D2OClass:
    class_id: int
    name: str
    package: str
    fields: tuple[D2OField, ...]


class D2OFile:
    """Keeps the whole file in memory: only use on the few map-related tables."""

    def __init__(self, path: Path):
        size = path.stat().st_size
        if size > MAX_D2O_BYTES:
            raise GameDataError("LIMIT", "D2O trop volumineux")
        self.path = path
        self.data = path.read_bytes()
        if self.data[:3] != b"D2O" or len(self.data) < 7:
            raise GameDataError("UNKNOWN_FORMAT", "Magic D2O non reconnu")
        index_offset = struct.unpack_from(">I", self.data, 3)[0]
        if not 7 <= index_offset <= len(self.data) - 4:
            raise GameDataError("CORRUPT", "Offset index D2O hors fichier")
        reader = Reader(self.data[index_offset:])
        length = reader.number("I")
        if length % 8 or length > MAX_INDEX_BYTES or length > reader.remaining:
            raise GameDataError("CORRUPT", "Taille index D2O incohérente")
        self.index: dict[int, int] = {}
        for _ in range(length // 8):
            key, position = reader.number("i"), reader.number("I")
            if key in self.index or not 7 <= position < index_offset:
                raise GameDataError("CORRUPT", "Objet D2O dupliqué ou hors zone de données")
            self.index[key] = position
        class_count = reader.number("i")
        if not 0 <= class_count <= MAX_CLASSES:
            raise GameDataError("LIMIT", "Nombre de classes D2O incohérent")
        self.classes: dict[int, D2OClass] = {}
        for _ in range(class_count):
            class_id = reader.number("i")
            name, package = reader.utf(), reader.utf()
            field_count = reader.number("i")
            if not 0 <= field_count <= MAX_FIELDS or class_id in self.classes:
                raise GameDataError("CORRUPT", "Définition de classe D2O incohérente")
            fields = []
            for _ in range(field_count):
                field_name, type_id = reader.utf(), reader.number("i")
                vector_types = []
                current = type_id
                while current == VECTOR:
                    if len(vector_types) >= MAX_DEPTH:
                        raise GameDataError("LIMIT", "Vecteurs D2O trop imbriqués")
                    inner_name, current = reader.utf(), reader.number("i")
                    vector_types.append((inner_name, current))
                fields.append(D2OField(field_name, type_id, tuple(vector_types)))
            self.classes[class_id] = D2OClass(class_id, name, package, tuple(fields))
        self.trailing_bytes = reader.remaining  # search/processor block, not decoded

    def schema(self) -> list[dict]:
        return [{"class_id": c.class_id, "name": c.name, "package": c.package,
                 "fields": {f.name: f.type_name() for f in c.fields}} for c in self.classes.values()]

    def get(self, key: int) -> dict:
        if key not in self.index:
            raise GameDataError("OBJECT_NOT_FOUND", f"Objet D2O {key} absent")
        reader = Reader(memoryview(self.data)[self.index[key]:])
        return self._object(reader, reader.number("i"), 0)

    def objects(self):
        for key in self.index:
            yield key, self.get(key)

    def _object(self, reader: Reader, class_id: int, depth: int) -> dict:
        if depth > MAX_DEPTH:
            raise GameDataError("LIMIT", "Objets D2O trop imbriqués")
        definition = self.classes.get(class_id)
        if definition is None:
            raise GameDataError("CORRUPT", f"Classe D2O inconnue : {class_id}")
        value = {"_class": definition.name}
        for field in definition.fields:
            value[field.name] = self._value(reader, field.type_id, field.vector_types, depth)
        return value

    def _value(self, reader: Reader, type_id: int, vector_types, depth: int):
        if type_id == -1:
            return reader.number("i")
        if type_id == -2:
            return reader.number("?")
        if type_id == -3:
            size = reader.number("H")
            return reader.take(size).decode("utf-8", errors="replace")
        if type_id == -4:
            return reader.number("d")
        if type_id in (-5, -6):
            return reader.number("I")
        if type_id == VECTOR:
            if not vector_types:
                raise GameDataError("CORRUPT", "Type de vecteur D2O absent")
            count = reader.number("i")
            if not 0 <= count <= MAX_VECTOR_ITEMS:
                raise GameDataError("LIMIT", "Vecteur D2O incohérent")
            inner = vector_types[0][1]
            return [self._value(reader, inner, vector_types[1:], depth + 1) for _ in range(count)]
        if type_id > 0:
            class_id = reader.number("i")
            return None if class_id == NULL_OBJECT else self._object(reader, class_id, depth + 1)
        raise GameDataError("CORRUPT", f"Type de champ D2O inconnu : {type_id}")
