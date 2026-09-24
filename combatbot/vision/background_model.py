"""Modèle de fond prudent par cellule projetée (LOT 3B-5).

Objectif : savoir si une cellule a changé visuellement, pas maximiser les FREE. Une cellule ne
devient FREE que si : traversable statiquement (GameData), grille VISIBLE et ALIGNED, fond appris
sur plusieurs frames cohérentes, aucune preuve d'entité, écart au fond sous tolérance, et
cohérence sur plusieurs frames consécutives. Sinon : UNKNOWN.

Le fond n'est jamais appris depuis une frame où la cellule porte une preuve d'entité, même faible.
Absence de changement ≠ FREE automatiquement.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BackgroundConfig:
    min_frames: int = 3          # échantillons de fond requis avant tout FREE
    max_spread: float = 4.0      # dispersion Lab (ΔE moyen) tolérée entre échantillons
    tolerance: float = 6.0       # ΔE maximal courant vs fond appris
    consecutive: int = 2         # frames FREE-candidates consécutives exigées
    history: int = 12


class CellBackgroundModel:
    """Fond par (contexte de projection, cellule). Un changement de map ou de projection repart de zéro."""

    def __init__(self, config: BackgroundConfig | None = None) -> None:
        self.config = config or BackgroundConfig()
        self._key: tuple | None = None
        self._samples: dict[int, deque] = {}
        self._streak: dict[int, int] = {}

    def reset(self, key: tuple | None = None) -> None:
        self._key = key
        self._samples.clear()
        self._streak.clear()

    def use_context(self, key: tuple) -> None:
        """Nouvelle map ou nouvelle projection : l'ancien fond ne vaut plus rien."""
        if key != self._key:
            self.reset(key)

    @staticmethod
    def eligible(cell, context) -> bool:
        return bool(getattr(cell, "static_traversable", None) is True and context is not None
                    and context.grid_trusted)

    def learn(self, key: tuple, cell_id: int, ground_lab, cell, context, *, entity_evidence: bool) -> bool:
        """Ajoute un échantillon de fond ; refusé si la moindre preuve d'entité existe."""
        self.use_context(key)
        if entity_evidence or not self.eligible(cell, context):
            self._streak[cell_id] = 0
            return False
        samples = self._samples.setdefault(cell_id, deque(maxlen=self.config.history))
        samples.append(np.asarray(ground_lab, dtype=np.float64))
        return True

    def ready(self, cell_id: int) -> bool:
        samples = self._samples.get(cell_id)
        if not samples or len(samples) < self.config.min_frames:
            return False
        stack = np.stack(samples)
        spread = float(np.linalg.norm(stack - stack.mean(axis=0), axis=1).mean())
        return spread <= self.config.max_spread

    def delta(self, cell_id: int, ground_lab) -> float | None:
        if not self.ready(cell_id):
            return None
        reference = np.stack(self._samples[cell_id]).mean(axis=0)
        return float(np.linalg.norm(np.asarray(ground_lab, dtype=np.float64) - reference))

    def is_free(self, cell_id: int, ground_lab, cell, context) -> bool:
        """Appelé par le détecteur seulement pour une cellule sans preuve d'entité."""
        if cell is None or not self.eligible(cell, context):
            self._streak[cell_id] = 0
            return False
        delta = self.delta(cell_id, ground_lab)
        if delta is None or delta > self.config.tolerance:
            self._streak[cell_id] = 0
            return False
        self._streak[cell_id] = self._streak.get(cell_id, 0) + 1
        return self._streak[cell_id] >= self.config.consecutive
