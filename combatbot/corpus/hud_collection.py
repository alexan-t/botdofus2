"""Collecte HUD réelle en lecture seule : capture PA/PM, déduplication, enregistrement au corpus.

Aucun lecteur n'est exécuté ici : la valeur sera saisie par l'humain dans la revue HUD, sans
prédiction affichée. Aucune action n'est envoyée au client.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from typing import Any
from uuid import uuid4

import cv2
import numpy as np

from combatbot import __version__
from combatbot.corpus.models import CorpusEntry, CorpusManifest, SCHEMA_VERSION
from combatbot.corpus.repository import CorpusRepository

CONTEXTS = ("exploration", "placement", "combat", "inconnu")


def crop_difference(first: np.ndarray | None, second: np.ndarray | None) -> float:
    """Écart moyen absolu en niveaux de gris ; infini si les crops ne sont pas comparables."""
    if first is None or second is None or first.shape != second.shape or first.size == 0:
        return float("inf")
    to_gray = (lambda image: cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image)
    return float(np.abs(to_gray(first).astype(np.int16) - to_gray(second).astype(np.int16)).mean())


@dataclass(frozen=True)
class CaptureOutcome:
    saved: bool
    reason: str
    entry: CorpusEntry | None = None
    difference: float | None = None


class HUDCollectionSession:
    """Une session de collecte = un ``session_id`` de corpus, frames numérotées dans l'ordre.

    Un compteur immobile n'est enregistré qu'une fois : une capture est ignorée si PA et PM
    sont quasi identiques aux derniers crops enregistrés (``duplicate_threshold``).
    """

    # Mesure réelle 3B-4R2 : deux rendus du même compteur diffèrent de ≤ 0,003 niveau de gris
    # en moyenne, mais un PM 6 → 0 (icône identique) ne diffère que de 1,94. Au-dessus de 0,5,
    # un changement de chiffre n'est donc jamais pris pour un doublon.
    DUPLICATE_THRESHOLD = 0.5

    def __init__(self, repository: CorpusRepository, session_id: str | None = None, *,
                 duplicate_threshold: float = DUPLICATE_THRESHOLD) -> None:
        self.repository = repository
        self.session_id = session_id or f"hud-collect-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        self.duplicate_threshold = duplicate_threshold
        self.saved = 0
        self.duplicates = 0
        self._last: tuple[np.ndarray, np.ndarray] | None = None
        used = [entry.frame_index for entry in repository.list_entries() if entry.session_id == self.session_id]
        self._next_frame = max(used, default=-1) + 1

    def capture(self, frame: np.ndarray, ap_crop: np.ndarray | None, mp_crop: np.ndarray | None,
                metadata: dict[str, Any], *, context: str = "inconnu", manual: bool = False) -> CaptureOutcome:
        if context not in CONTEXTS:
            raise ValueError(f"Contexte inconnu : {context}")
        if ap_crop is None or mp_crop is None or ap_crop.size == 0 or mp_crop.size == 0:
            return CaptureOutcome(False, "Zone PA ou PM absente : calibrez les zones PA/PM")
        if self._last is not None:
            difference = max(crop_difference(ap_crop, self._last[0]), crop_difference(mp_crop, self._last[1]))
            if difference <= self.duplicate_threshold:
                self.duplicates += 1
                return CaptureOutcome(False, "Compteurs identiques à la dernière capture : ignorée", None,
                                      difference)
        else:
            difference = None
        entry = self._write(frame, ap_crop, mp_crop, metadata, context, manual)
        self._last = (ap_crop.copy(), mp_crop.copy())
        self.saved += 1
        return CaptureOutcome(True, "Capture enregistrée", entry, difference)

    def _write(self, frame: np.ndarray, ap_crop: np.ndarray, mp_crop: np.ndarray,
               metadata: dict[str, Any], context: str, manual: bool) -> CorpusEntry:
        repository = self.repository
        manifest = repository.load_manifest()
        frame_index = self._next_frame
        observation_id = f"obs_{uuid4().hex[:16]}"
        destination = repository.sessions / self.session_id / observation_id
        (destination / "hud").mkdir(parents=True)
        for path, image in ((destination / "frame.png", frame), (destination / "hud" / "ap_original.png", ap_crop),
                            (destination / "hud" / "mp_original.png", mp_crop)):
            if not cv2.imwrite(str(path), image):
                raise OSError(f"Impossible d'écrire {path.name}")
        document = {
            "schema_version": SCHEMA_VERSION,
            "observation_id": observation_id,
            "session_id": self.session_id,
            "frame_index": frame_index,
            "created_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "capture": {**metadata, "pythonbot_version": __version__, "collection": "HUD Real Collection",
                        "context": context, "manual": manual, "session_id": self.session_id,
                        "frame_index": frame_index, "actions_sent": False},
            # Aucune prédiction : la vérité sera saisie par l'humain sans biais du lecteur.
            "prediction": {},
            "files": {"frame": "frame.png", "overlay": "frame.png",
                      "ap": "hud/ap_original.png", "mp": "hud/mp_original.png"},
        }
        temporary = destination / "observation.json.tmp"
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, destination / "observation.json")
        base = destination.relative_to(repository.root).as_posix()
        entry = CorpusEntry(
            observation_id, self.session_id, frame_index,
            {"frame": f"{base}/frame.png", "overlay": f"{base}/frame.png",
             "observation": f"{base}/observation.json",
             "ap_crop": f"{base}/hud/ap_original.png", "mp_crop": f"{base}/hud/mp_original.png"},
            tags=("hud-collection", context),
        )
        repository.save_manifest(CorpusManifest(manifest.entries + (entry,)))
        self._next_frame += 1
        return entry


def capture_metadata(frame, calibration, transform) -> dict[str, Any]:
    """Métadonnées relatives au client : aucune coordonnée écran absolue n'est requise."""
    return {
        "client_size": [frame.client.width, frame.client.height],
        "capture_source": getattr(frame, "source", None),
        "calibration": {
            "profile_id": calibration.profile_id,
            "client_size": [calibration.client_width, calibration.client_height],
            "layout_signature": calibration.layout_signature,
            "zones": {name: rect.to_dict() for name, rect in calibration.zones.items()},
        },
        "layout_transform": transform.to_dict(),
        "coordinate_spaces": {"capture": "client", "hud": "client"},
    }


def extract_hud_crops(frame, calibration) -> tuple[np.ndarray | None, np.ndarray | None, Any]:
    """Même découpe que l'observateur réel (zones normalisées + LayoutTransform)."""
    from combatbot.vision.combat_observer import _zone

    transform = calibration.layout_transform(frame)
    return (_zone(frame, calibration, transform, "ap"), _zone(frame, calibration, transform, "mp"), transform)
