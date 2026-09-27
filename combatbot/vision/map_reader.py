"""LOT 3B-6C : lecture des informations de map affichées en haut à gauche du client.

Le client affiche « Zone (Sous-zone) » puis « x,y, Niveau N » ; dans les donjons et lieux nommés
il affiche le nom de la map (« Cour du Bouftou Royal - Première salle ») puis « x,y » seul.
Une lecture n'est COMPLÈTE que si
les deux lignes sont entières : un sprite qui masque une partie du texte peut faire lire un autre
chiffre avec une forte confiance OCR (cas réel : « 7,-2 » lu « 7,-4 » derrière un personnage).
Une lecture incomplète n'est jamais utilisée pour résoudre la map. Aucun chiffre n'est corrigé.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import re
import time

import numpy as np

from combatbot.gamedata.map_index import normalize_name

# ROI par défaut, relative au client (x, y, largeur, hauteur) : survit au déplacement de la fenêtre.
DEFAULT_MAP_INFO_ROI = (0.0, 0.0, 0.30, 0.14)
COORDINATE_LIMIT = 200
_COORDS = re.compile(r"^\s*\[?\s*(-?\d{1,3})\s*,\s*(-?\d{1,3})\s*\]?\s*$")
_INFO = re.compile(r"(?<![\d,\-])(-?\d{1,3})\s*,\s*(-?\d{1,3})\s*,\s*niveau\s*(\d{1,3})(?![\d])")
_NAMES = re.compile(r"^\s*([^()]+?)\s*\(([^()]+)\)\s*$")


@dataclass(frozen=True)
class CoordinateObservation:
    x: int
    y: int
    confidence: float
    source: str
    raw_text: str


@dataclass(frozen=True)
class MapInfoObservation:
    coordinates: CoordinateObservation | None
    level: int | None
    area_name: str | None
    sub_area_name: str | None
    complete: bool
    reason: str
    raw_lines: tuple[str, ...] = ()
    timestamp: float = 0.0
    # Format « lieu nommé » (donjon, temple…) : nom de la map au lieu de « Zone (Sous-zone) ».
    map_name: str | None = None

    @property
    def key(self) -> tuple | None:
        if not self.complete or self.coordinates is None:
            return None
        if self.map_name is not None:
            return (self.coordinates.x, self.coordinates.y, "map_name", normalize_name(self.map_name))
        return (self.coordinates.x, self.coordinates.y, normalize_name(self.area_name),
                normalize_name(self.sub_area_name), self.level)

    def to_dict(self) -> dict[str, object]:
        coords = self.coordinates
        return {"x": coords.x if coords else None, "y": coords.y if coords else None,
                "confidence": coords.confidence if coords else 0.0, "source": coords.source if coords else None,
                "level": self.level, "area_name": self.area_name, "sub_area_name": self.sub_area_name,
                "map_name": self.map_name,
                "complete": self.complete, "reason": self.reason, "raw_lines": list(self.raw_lines)}


def parse_coordinates(text: str, *, source: str = "TEXT", confidence: float = 1.0) -> CoordinateObservation | None:
    """« [4,-12] », « 4,-12 », « 4, -12 », « [ 4 , -12 ] » ; tout autre texte est refusé."""
    match = _COORDS.match(text or "")
    if match is None:
        return None
    x, y = int(match.group(1)), int(match.group(2))
    if abs(x) > COORDINATE_LIMIT or abs(y) > COORDINATE_LIMIT:
        return None
    return CoordinateObservation(x, y, confidence, source, text)


def parse_map_info(lines: list[tuple[str, float]], *, source: str = "RAPIDOCR",
                   timestamp: float = 0.0) -> MapInfoObservation:
    """Lignes OCR (texte, score) déjà triées dans l'ordre de lecture."""
    raw = tuple(text for text, _score in lines)
    info_index = None
    info_match = None
    for index, (text, _score) in enumerate(lines):
        match = _INFO.search(normalize_name(text).replace(" ,", ","))
        if match is not None:
            if info_match is not None:
                return MapInfoObservation(None, None, None, None, False, "SEVERAL_COORDINATE_LINES", raw, timestamp)
            info_index, info_match = index, match
    if info_match is None:
        return _parse_named_map(lines, raw, source, timestamp)
    x, y, level = (int(info_match.group(i)) for i in (1, 2, 3))
    if abs(x) > COORDINATE_LIMIT or abs(y) > COORDINATE_LIMIT:
        return MapInfoObservation(None, None, None, None, False, "COORDINATES_OUT_OF_RANGE", raw, timestamp)
    info_text, info_score = lines[info_index]
    coordinates = CoordinateObservation(x, y, float(info_score), source, info_text)
    names = " ".join(text for text, _score in lines[:info_index]).strip()
    name_match = _NAMES.match(names)
    if name_match is None:
        return MapInfoObservation(coordinates, level, None, None, False, "AREA_NAME_INCOMPLETE", raw, timestamp)
    name_score = min((score for _text, score in lines[:info_index]), default=0.0)
    if min(info_score, name_score) < 0.9:
        return MapInfoObservation(coordinates, level, name_match.group(1), name_match.group(2), False,
                                  "LOW_OCR_SCORE", raw, timestamp)
    return MapInfoObservation(coordinates, level, name_match.group(1).strip(), name_match.group(2).strip(), True,
                              "COMPLETE", raw, timestamp)


def _parse_named_map(lines: list[tuple[str, float]], raw: tuple[str, ...], source: str,
                     timestamp: float) -> MapInfoObservation:
    """Lieu nommé : lignes de nom puis une ligne contenant UNIQUEMENT « x,y ».

    Une ligne « x,y, » tronquée (sprite devant « Niveau ») n'est pas une ligne de coordonnées seule ;
    un nom « Zone (Sous-zone) » sans niveau est une lecture incomplète, jamais un nom de map.
    """
    coordinate_lines = [(index, parse_coordinates(text, source=source, confidence=float(score)))
                        for index, (text, score) in enumerate(lines)]
    coordinate_lines = [(index, value) for index, value in coordinate_lines if value is not None]
    if len(coordinate_lines) != 1:
        reason = "SEVERAL_COORDINATE_LINES" if coordinate_lines else "NO_COMPLETE_COORDINATE_LINE"
        return MapInfoObservation(None, None, None, None, False, reason, raw, timestamp)
    index, coordinates = coordinate_lines[0]
    if index != len(lines) - 1:
        return MapInfoObservation(coordinates, None, None, None, False, "TEXT_AFTER_COORDINATES", raw, timestamp)
    name = " ".join(text for text, _score in lines[:index]).strip()
    if not name:
        return MapInfoObservation(coordinates, None, None, None, False, "MAP_NAME_MISSING", raw, timestamp)
    if "(" in name or ")" in name:
        return MapInfoObservation(coordinates, None, None, None, False, "LEVEL_MISSING", raw, timestamp)
    score = min([coordinates.confidence] + [float(value) for _text, value in lines[:index]])
    if score < 0.9:
        return MapInfoObservation(coordinates, None, None, None, False, "LOW_OCR_SCORE", raw, timestamp,
                                  map_name=name)
    return MapInfoObservation(coordinates, None, None, None, True, "COMPLETE_NAMED_MAP", raw, timestamp,
                              map_name=name)


def extract_map_info_roi(client_image: np.ndarray, roi: tuple[float, float, float, float] = DEFAULT_MAP_INFO_ROI):
    height, width = client_image.shape[:2]
    x, y, w, h = roi
    return client_image[int(y * height):int((y + h) * height), int(x * width):int((x + w) * width)].copy()


class MapCoordinateReader:
    """Lecture OCR de la ROI d'informations de map. ``engine`` : fonction image → (lignes, scores, boîtes)."""

    def __init__(self, engine=None, roi: tuple[float, float, float, float] = DEFAULT_MAP_INFO_ROI) -> None:
        self._engine = engine
        self.roi = roi

    def _ocr(self, image: np.ndarray) -> list[tuple[str, float]]:
        if self._engine is not None:
            return list(self._engine(image))
        from combatbot.vision.tooltip import _ocr_engine
        result = _ocr_engine()(image, use_cls=False)
        texts = tuple(getattr(result, "txts", None) or ())
        scores = tuple(getattr(result, "scores", None) or ())
        boxes = getattr(result, "boxes", None)
        rows = []
        for index, (text, score) in enumerate(zip(texts, scores)):
            box = boxes[index] if boxes is not None and len(boxes) > index else None
            top = float(np.min(np.asarray(box)[:, 1])) if box is not None else float(index)
            left = float(np.min(np.asarray(box)[:, 0])) if box is not None else 0.0
            rows.append((top, left, str(text), float(score)))
        # Ordre de lecture : lignes de haut en bas (tolérance 12 px), puis de gauche à droite.
        rows.sort(key=lambda row: (round(row[0] / 12), row[1]))
        merged: list[tuple[str, float]] = []
        last_line = None
        for top, _left, text, score in rows:
            line = round(top / 12)
            if merged and line == last_line:
                previous_text, previous_score = merged[-1]
                merged[-1] = (f"{previous_text} {text}", min(previous_score, score))
            else:
                merged.append((text, score))
            last_line = line
        return merged

    def read(self, client_image: np.ndarray, *, roi_image: np.ndarray | None = None,
             timestamp: float | None = None) -> MapInfoObservation:
        stamp = time.time() if timestamp is None else timestamp
        image = roi_image if roi_image is not None else extract_map_info_roi(client_image, self.roi)
        if image is None or not image.size:
            return MapInfoObservation(None, None, None, None, False, "NO_ROI", (), stamp)
        try:
            lines = self._ocr(image)
        except Exception as exc:  # noqa: BLE001 - OCR indisponible : lecture inconnue, jamais inventée
            return MapInfoObservation(None, None, None, None, False, f"OCR_ERROR: {exc}", (), stamp)
        return parse_map_info(lines, timestamp=stamp)


@dataclass
class CoordinateConsensus:
    """Accepte une lecture complète si ``required`` des ``window`` dernières concordent, dont la
    dernière : une lecture isolée ne change rien, un vrai changement n'est pas retenu longtemps."""

    window: int = 3
    required: int = 2
    history: deque = field(default_factory=lambda: deque(maxlen=3))

    def __post_init__(self) -> None:
        self.history = deque(maxlen=self.window)

    def update(self, observation: MapInfoObservation) -> MapInfoObservation | None:
        self.history.append(observation)
        latest = observation.key
        if latest is None:
            return None
        agreeing = [item for item in self.history if item.key == latest]
        return observation if len(agreeing) >= self.required else None

    @property
    def pending_change(self) -> bool:
        """Dernière lecture complète différente de la précédente lecture complète."""
        keys = [item.key for item in self.history if item.key is not None]
        return len(keys) >= 2 and keys[-1] != keys[-2]

    def reset(self) -> None:
        self.history.clear()
