"""LOT 3B-6B : détecteur de phase de combat et de tour, OBSERVATION SEULE.

Appris uniquement sur des vérités HUMAINES TRAIN (annotation « phase et tour »). Indices :
- le bouton fin de tour (« PRÊT » en placement, « TERMINER LE TOUR » en combat) : jaune vif quand
  c'est au joueur d'agir, foncé sinon, remplacé par les icônes de menu hors combat ;
- une vignette du client entier (fenêtre de résultats, écran d'exploration).
Classifieur des plus proches voisins avec ABSTENTION : trop loin de tout exemple connu, ou deux
états trop proches → UNKNOWN. Un bouton masqué (infobulle) ne permet pas de conclure au tour : le
suivi temporel garde alors le dernier état sûr pendant une durée bornée (TTL), jamais au-delà.
Aucune action n'est déclenchée.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path

import cv2
import numpy as np

from combatbot.vision.combat_state import CombatPhase, SemanticCombatState, TurnOwner

MODEL_VERSION = "combat-state-knn-v3"
# Tour décidé par la seule couleur du bouton (TRAIN : « mon tour » visible ≥ 0,33 de jaune vif,
# « tour d'un autre » 0,00 de jaune vif et ≈ 0,6 de jaune foncé). Bouton masqué → tour inconnu.
PLAYER_BRIGHT_MIN = 0.15
OTHER_BRIGHT_MAX = 0.05
OTHER_DIM_MIN = 0.30
STATE_LABELS = ("OUT_OF_COMBAT", "PLACEMENT", "FIGHTING:PLAYER", "FIGHTING:OTHER", "RESULTS")
BUTTON_SIZE = (32, 12)
CLIENT_SIZE = (32, 18)


def button_colours(button: np.ndarray | None) -> tuple[float, float]:
    """Part de pixels jaune vif (bouton actif) et jaune foncé (bouton inactif)."""
    if button is None or not getattr(button, "size", 0):
        return 0.0, 0.0
    hsv = cv2.cvtColor(button, cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    yellow = (hue >= 25) & (hue <= 50)
    bright = float(np.mean(yellow & (saturation > 150) & (value > 200)))
    dim = float(np.mean(yellow & (saturation > 120) & (value > 70) & (value <= 200)))
    return bright, dim


def _thumbnail(image: np.ndarray | None, size: tuple[int, int]) -> np.ndarray:
    if image is None or not getattr(image, "size", 0):
        return np.zeros(size[0] * size[1], np.float32)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    small = cv2.resize(gray, size, interpolation=cv2.INTER_AREA).astype(np.float32).flatten()
    return (small - small.mean()) / (small.std() + 1e-6)


def button_text_profile(button: np.ndarray | None, bins: int = 12) -> np.ndarray:
    """Profil vertical du texte dans le bouton jaune : « PRÊT » (1 ligne) ≠ « TERMINER LE TOUR » (2)."""
    if button is None or not getattr(button, "size", 0):
        return np.zeros(bins, np.float32)
    hsv = cv2.cvtColor(button, cv2.COLOR_BGR2HSV)
    yellow = (hsv[..., 0] >= 25) & (hsv[..., 0] <= 50) & (hsv[..., 1] > 120) & (hsv[..., 2] > 70)
    if yellow.mean() < 0.3:
        return np.zeros(bins, np.float32)      # pas de bouton jaune visible (menu, infobulle)
    width = yellow.shape[1]
    text = (~yellow[:, int(width * 0.2):int(width * 0.8)]).mean(axis=1).astype(np.float32)
    return cv2.resize(text.reshape(-1, 1), (1, bins), interpolation=cv2.INTER_AREA).flatten()


def extract_features(end_turn: np.ndarray | None, client: np.ndarray | None) -> np.ndarray:
    """Couleurs du bouton (poids fort), forme du bouton (texte PRÊT / TERMINER, menu), vignette client."""
    bright, dim = button_colours(end_turn)
    button = _thumbnail(end_turn, BUTTON_SIZE) / np.sqrt(BUTTON_SIZE[0] * BUTTON_SIZE[1])
    scene = _thumbnail(client, CLIENT_SIZE) / np.sqrt(CLIENT_SIZE[0] * CLIENT_SIZE[1])
    profile = button_text_profile(end_turn) * 3.0
    return np.concatenate([np.array([bright, dim], np.float32) * 4.0, profile, button * 1.6,
                           scene * 0.7]).astype(np.float32)


def turn_from_button(bright: float, dim: float) -> TurnOwner:
    """Le seul indice du tour : jamais déduit d'une scène voisine ni de la mémoire."""
    if bright >= PLAYER_BRIGHT_MIN:
        return TurnOwner.PLAYER
    if bright <= OTHER_BRIGHT_MAX and dim >= OTHER_DIM_MIN:
        return TurnOwner.OTHER
    return TurnOwner.UNKNOWN


def label_to_state(label: str) -> tuple[CombatPhase, TurnOwner]:
    if label.startswith("FIGHTING:"):
        return CombatPhase.FIGHTING, TurnOwner(label.split(":", 1)[1])
    return CombatPhase(label), TurnOwner.UNKNOWN


@dataclass
class CombatStateModel:
    """k plus proches voisins sur des exemples TRAIN humains ; seuils d'abstention explicites."""

    features: np.ndarray
    labels: tuple[str, ...]
    groups: tuple[str, ...] = ()
    k: int = 3
    max_distance: float = 1.2       # au-delà : scène jamais vue → UNKNOWN
    min_margin: float = 0.34        # part des votes pondérés d'écart entre les deux meilleurs états
    provenance: dict = field(default_factory=dict)

    def predict(self, feature: np.ndarray) -> SemanticCombatState:
        if not len(self.labels):
            return SemanticCombatState(reasons=("aucun exemple TRAIN",))
        distances = np.linalg.norm(self.features - feature[None, :], axis=1)
        order = np.argsort(distances)[:self.k]
        nearest = float(distances[order[0]])
        # Votes par PHASE (les deux tours d'un combat votent ensemble pour « combat »).
        votes: dict[str, float] = {}
        for index in order:
            phase_label = self.labels[index].split(":", 1)[0]
            votes[phase_label] = votes.get(phase_label, 0.0) + 1.0 / (distances[index] + 0.05)
        total = sum(votes.values())
        ranked = sorted(votes.items(), key=lambda item: -item[1])
        best, best_score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = float((best_score - second) / total)
        bright, dim = float(feature[0]) / 4.0, float(feature[1]) / 4.0
        evidence = {"nearest_distance": round(nearest, 3), "margin": round(margin, 3),
                    "votes": {key: round(float(value / total), 3) for key, value in ranked},
                    "button_bright": round(bright, 3), "button_dim": round(dim, 3)}
        if nearest > self.max_distance:
            return SemanticCombatState(reasons=("scène trop différente des exemples TRAIN",), evidence=evidence)
        if margin < self.min_margin:
            return SemanticCombatState(reasons=(f"hésitation {ranked[0][0]} / {ranked[1][0]}",), evidence=evidence)
        phase = CombatPhase(best)
        turn = turn_from_button(bright, dim) if phase is CombatPhase.FIGHTING else TurnOwner.UNKNOWN
        reasons = (f"k-NN phase {best}",) + ((f"bouton → {turn.value}",) if phase is CombatPhase.FIGHTING else ())
        return SemanticCombatState(phase, turn, round(min(1.0, margin), 3), reasons, evidence)

    # ------------------------------------------------------------------ persistance
    def save(self, directory: Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(directory / "model.npz", features=self.features,
                            labels=np.array(self.labels), groups=np.array(self.groups))
        (directory / "provenance.json").write_text(json.dumps({
            "version": MODEL_VERSION, "k": self.k, "max_distance": self.max_distance,
            "min_margin": self.min_margin, **self.provenance}, ensure_ascii=False, indent=2), encoding="utf-8")
        return directory

    @classmethod
    def load(cls, directory: Path) -> "CombatStateModel | None":
        directory = Path(directory)
        try:
            meta = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
            if meta.get("version") != MODEL_VERSION or meta.get("truth_source") != "human_confirmed" \
                    or meta.get("training_split") != "train":
                return None
            data = np.load(directory / "model.npz")
            return cls(data["features"], tuple(str(x) for x in data["labels"]),
                       tuple(str(x) for x in data["groups"]), int(meta["k"]), float(meta["max_distance"]),
                       float(meta["min_margin"]), meta)
        except (OSError, ValueError, KeyError):
            return None


@dataclass
class SemanticCombatStateTracker:
    """Lissage temporel borné de la PHASE : une phase incertaine garde la dernière phase sûre au plus
    ``ttl`` secondes ; un changement de phase doit être vu ``confirm_frames`` fois (sauf confiance
    élevée). Le TOUR n'est jamais mémorisé : il vient de la frame courante, sinon UNKNOWN."""

    ttl: float = 2.5
    confirm_frames: int = 2
    immediate_confidence: float = 0.8
    _state: SemanticCombatState = field(default_factory=SemanticCombatState)
    _last_sure: float = -1e9
    _candidate: str | None = None
    _candidate_count: int = 0

    def update(self, observed: SemanticCombatState, now: float) -> SemanticCombatState:
        if observed.phase is CombatPhase.UNKNOWN:
            if self._state.phase is not CombatPhase.UNKNOWN and now - self._last_sure <= self.ttl:
                return SemanticCombatState(self._state.phase, TurnOwner.UNKNOWN, self._state.confidence * 0.8,
                                           ("phase maintenue (TTL), tour non observé",) + observed.reasons,
                                           observed.evidence)
            self._state = SemanticCombatState(reasons=observed.reasons, evidence=observed.evidence)
            return self._state
        key = observed.phase.value
        if self._state.phase is CombatPhase.UNKNOWN or key == self._state.phase.value \
                or observed.confidence >= self.immediate_confidence:
            self._state, self._last_sure, self._candidate, self._candidate_count = observed, now, None, 0
            return observed
        self._candidate_count = self._candidate_count + 1 if self._candidate == key else 1
        self._candidate = key
        if self._candidate_count >= self.confirm_frames:
            self._state, self._last_sure, self._candidate, self._candidate_count = observed, now, None, 0
            return observed
        return SemanticCombatState(self._state.phase, TurnOwner.UNKNOWN, self._state.confidence,
                                   ("changement de phase non confirmé",), observed.evidence)


def fit_model(samples: list[tuple[np.ndarray, str, str]], *, provenance: dict | None = None,
              **parameters) -> CombatStateModel:
    """``samples`` : (caractéristiques, étiquette STATE_LABELS, groupe = combat). UNKNOWN exclu."""
    kept = [item for item in samples if item[1] in STATE_LABELS]
    features = np.stack([item[0] for item in kept]) if kept else np.zeros((0, 1), np.float32)
    return CombatStateModel(features, tuple(item[1] for item in kept), tuple(item[2] for item in kept),
                            provenance={"built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                        **(provenance or {})}, **parameters)
