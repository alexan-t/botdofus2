"""Lecture bornée big-endian et décompression limitée."""
import struct
import zlib

from ..errors import GameDataError

MAX_MAP_BYTES = 16 * 1024 * 1024
MAX_INDEX_BYTES = 32 * 1024 * 1024


class Reader:
    def __init__(self, data: bytes):
        self.data = memoryview(data)
        self.position = 0

    @property
    def remaining(self) -> int:
        return len(self.data) - self.position

    def take(self, count: int) -> bytes:
        if count < 0 or count > self.remaining:
            raise GameDataError("TRUNCATED", f"Fichier tronqué à l'offset {self.position} : {count} octets attendus")
        start = self.position
        self.position += count
        return bytes(self.data[start:self.position])

    def number(self, fmt: str) -> int:
        return struct.unpack(">" + fmt, self.take(struct.calcsize(">" + fmt)))[0]

    def utf(self) -> str:
        size = self.number("H")
        if size > 4096:
            raise GameDataError("LIMIT", "Chaîne d'index trop longue")
        try:
            return self.take(size).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GameDataError("CORRUPT", "Chaîne d'index UTF-8 invalide") from exc


def map_payload(data: bytes) -> tuple[bytes, bool]:
    if len(data) > MAX_MAP_BYTES:
        raise GameDataError("LIMIT", "Map trop volumineuse")
    if data.startswith(b"M"):
        return data, False
    try:
        decoder = zlib.decompressobj()
        output = decoder.decompress(data, MAX_MAP_BYTES + 1)
        if len(output) > MAX_MAP_BYTES or decoder.unconsumed_tail:
            raise GameDataError("LIMIT", "Map décompressée trop volumineuse")
        if not decoder.eof or decoder.unused_data:
            raise GameDataError("CORRUPT", "Flux zlib tronqué ou données supplémentaires")
        if not output.startswith(b"M"):
            raise GameDataError("UNKNOWN_FORMAT", "Magic DLM absent après décompression")
        return output, True
    except zlib.error as exc:
        raise GameDataError("UNKNOWN_FORMAT", "Ni DLM brut (0x4D), ni DLM zlib valide") from exc
