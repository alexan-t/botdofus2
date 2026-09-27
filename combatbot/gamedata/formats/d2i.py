"""Lecteur D2I (textes i18n du client DOFUS 2), lecture seule.

Format (big-endian) : int32 position de l'index ; à cette position int32 taille de l'index puis
des entrées (int32 clé, bool diacritique, int32 position [, int32 position sans accent]). Chaque
texte est un UTF (uint16 longueur + UTF-8). Seuls les textes demandés sont lus.
"""
from __future__ import annotations

from pathlib import Path
import struct

from combatbot.gamedata.errors import GameDataError


class D2IFile:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._data = self.path.read_bytes()
        self.index: dict[int, int] = {}
        try:
            (position,) = struct.unpack_from(">i", self._data, 0)
            (length,) = struct.unpack_from(">i", self._data, position)
            cursor, end = position + 4, position + 4 + length
            while cursor < end:
                key, diacritical, pointer = struct.unpack_from(">i?i", self._data, cursor)
                cursor += 9
                if diacritical:
                    cursor += 4
                self.index[key] = pointer
        except struct.error as exc:
            raise GameDataError("CORRUPT", f"Index D2I illisible : {exc}") from exc

    def text(self, key: int) -> str | None:
        pointer = self.index.get(int(key))
        if pointer is None:
            return None
        try:
            (length,) = struct.unpack_from(">H", self._data, pointer)
            return self._data[pointer + 2:pointer + 2 + length].decode("utf-8")
        except (struct.error, UnicodeDecodeError):
            return None
