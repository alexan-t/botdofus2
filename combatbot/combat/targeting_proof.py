"""LOT-4C-LIVE-PROOF : vérité du client sur la portée et la LOS, et comparaison de règles candidates.

Principe : l'utilisateur sélectionne **lui-même** un sort dans DOFUS. Le client affiche alors les
cellules ciblables. PythonBot enregistre la frame (flux « Enregistrer l'observation » existant, aucune
action) et l'utilisateur annote chaque cellule : ciblable, non ciblable, obstacle. Ce module :

- porte l'échantillon (``ProofSample``) et son stockage JSON, hors corpus (aucune vérité modifiée) ;
- calcule la couverture des cas exigés (en ligne / hors ligne, avec / sans obstacle, portée min, portée
  max, juste au-delà, portée modifiable) ;
- compare des règles **candidates** à la vérité, cellule par cellule.

Aucune règle n'est adoptée ici : ``targeting.RangeMetric`` reste UNVERIFIED tant qu'une décision
humaine, appuyée sur un rapport sans désaccord et à couverture complète, ne l'a pas changé dans le code.
Répartition TRAIN/TEST déterministe par identifiant d'échantillon : aucun candidat n'est réglé.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable, Iterable, Mapping

from combatbot.combat.pathfinding import CombatMap
from combatbot.gamedata.topology import cell_to_grid

REQUIRED_CASES = ("IN_LINE", "OFF_LINE", "WITH_OBSTACLE", "NO_OBSTACLE", "AT_MIN_RANGE", "AT_MAX_RANGE",
                  "BEYOND_MAX_RANGE", "MODIFIABLE_RANGE")
MIN_MAPS = 2


@dataclass(frozen=True)
class ProofSample:
    sample_id: str
    observation_id: str | None
    map_id: int
    caster_cell_id: int
    spell_key: str
    spell_name: str | None
    min_range: int
    max_range: int
    modifiable_range: bool | None
    line_cast: bool | None
    line_of_sight: bool | None
    range_bonus: int | None                       # bonus de portée connu (None = inconnu)
    targetable: frozenset[int] = frozenset()      # cellules affichées ciblables par le client
    not_targetable: frozenset[int] = frozenset()  # cellules vérifiées NON ciblables
    obstacles: frozenset[int] = frozenset()       # occupants / obstacles dynamiques vus
    annotated_by: str = "human"
    notes: str = ""

    @property
    def split(self) -> str:
        """Répartition figée : jamais choisie après coup."""
        return "TEST" if int(hashlib.sha256(self.sample_id.encode()).hexdigest(), 16) % 4 == 0 else "TRAIN"

    def validate(self) -> None:
        if self.targetable & self.not_targetable:
            raise ValueError("Une cellule ne peut pas être à la fois ciblable et non ciblable")
        if self.min_range < 0 or self.max_range < self.min_range:
            raise ValueError("Portée min/max invalide")
        if not self.targetable and not self.not_targetable:
            raise ValueError("Aucune cellule annotée")

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        for name in ("targetable", "not_targetable", "obstacles"):
            data[name] = sorted(data[name])
        data["split"] = self.split
        return data

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ProofSample":
        values = {name: raw.get(name) for name in cls.__dataclass_fields__}
        for name in ("targetable", "not_targetable", "obstacles"):
            values[name] = frozenset(int(cell) for cell in raw.get(name) or ())
        values["annotated_by"] = values["annotated_by"] or "human"
        values["notes"] = values["notes"] or ""
        sample = cls(**values)
        sample.validate()
        return sample


class TargetingProofStore:
    """Un fichier JSON par échantillon, sous ``<données>/targeting_proof/`` (jamais dans le corpus)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def save(self, sample: ProofSample) -> Path:
        sample.validate()
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{sample.sample_id}.json"
        path.write_text(json.dumps(sample.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self) -> list[ProofSample]:
        samples = []
        for path in sorted(self.root.glob("*.json")):
            try:
                samples.append(ProofSample.from_dict(json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError, KeyError):
                continue
        return samples


# ------------------------------------------------------------------ géométrie exacte des candidats
def _coordinates(cell: int) -> tuple[int, int]:
    coordinate = cell_to_grid(int(cell))
    return coordinate.x, coordinate.y


def segment_cells(a: int, b: int) -> tuple[frozenset[int], frozenset[int]]:
    """Cellules traversées par le segment centre→centre (hors extrémités), et cellules seulement
    touchées en un coin. Arithmétique rationnelle exacte sur les coordonnées GameData."""
    from combatbot.gamedata.models import GridCoordinate
    from combatbot.gamedata.topology import grid_to_cell
    (x0, y0), (x1, y1) = _coordinates(a), _coordinates(b)
    crossed, corners = set(), set()
    for i in range(min(x0, x1), max(x0, x1) + 1):
        for j in range(min(y0, y1), max(y0, y1) + 1):
            if (i, j) in ((x0, y0), (x1, y1)):
                continue
            low, high = Fraction(0), Fraction(1)
            clipped = True
            for delta, start, lo, hi in ((x1 - x0, x0, Fraction(2 * i - 1, 2), Fraction(2 * i + 1, 2)),
                                         (y1 - y0, y0, Fraction(2 * j - 1, 2), Fraction(2 * j + 1, 2))):
                if delta == 0:
                    if not lo <= start <= hi:
                        clipped = False
                    continue
                t0, t1 = (lo - start) / delta, (hi - start) / delta
                low, high = max(low, min(t0, t1)), min(high, max(t0, t1))
            if not clipped or low > high:
                continue
            cell = grid_to_cell(GridCoordinate(i, j))
            if cell is None:
                continue
            (crossed if high > low else corners).add(int(cell))
    return frozenset(crossed), frozenset(corners)


def manhattan(a: int, b: int) -> int:
    (x0, y0), (x1, y1) = _coordinates(a), _coordinates(b)
    return abs(x1 - x0) + abs(y1 - y0)


def chebyshev(a: int, b: int) -> int:
    (x0, y0), (x1, y1) = _coordinates(a), _coordinates(b)
    return max(abs(x1 - x0), abs(y1 - y0))


def in_line(a: int, b: int) -> bool:
    (x0, y0), (x1, y1) = _coordinates(a), _coordinates(b)
    return x0 == x1 or y0 == y1


Candidate = Callable[[CombatMap, ProofSample, int], bool | None]     # None = inconnu


def _within_range(sample: ProofSample, distance: int) -> bool | None:
    bonus = 0
    if sample.modifiable_range:
        if sample.range_bonus is None:
            return None
        bonus = sample.range_bonus
    return sample.min_range <= distance <= sample.max_range + bonus


def _los(combat_map: CombatMap, sample: ProofSample, target: int, *, strict: bool) -> bool | None:
    crossed, corners = segment_cells(sample.caster_cell_id, target)
    checked = crossed | corners if strict else crossed
    for cell in checked:
        if cell in sample.obstacles:
            return False
        static = combat_map.line_of_sight.get(cell)
        if static is None:
            return None
        if static is False:
            return False
    return True


def make_candidate(distance: Callable[[int, int], int], los: str | None) -> Candidate:
    def candidate(combat_map: CombatMap, sample: ProofSample, target: int) -> bool | None:
        within = _within_range(sample, distance(sample.caster_cell_id, target))
        if within is not True:
            return within
        if sample.line_cast and not in_line(sample.caster_cell_id, target):
            return False
        if los is None or not sample.line_of_sight:
            return True
        return _los(combat_map, sample, target, strict=los == "strict")
    return candidate


CANDIDATES: dict[str, Candidate] = {
    "manhattan + LOS stricte (coins bloquants)": make_candidate(manhattan, "strict"),
    "manhattan + LOS tolérante (coins passants)": make_candidate(manhattan, "lenient"),
    "manhattan sans LOS": make_candidate(manhattan, None),
    "chebyshev + LOS stricte": make_candidate(chebyshev, "strict"),
}


def case_tags(combat_map: CombatMap, sample: ProofSample, target: int) -> set[str]:
    tags = {"IN_LINE" if in_line(sample.caster_cell_id, target) else "OFF_LINE"}
    crossed, corners = segment_cells(sample.caster_cell_id, target)
    blocked = any(cell in sample.obstacles or combat_map.line_of_sight.get(cell) is False
                  for cell in crossed | corners)
    tags.add("WITH_OBSTACLE" if blocked else "NO_OBSTACLE")
    distance = manhattan(sample.caster_cell_id, target)
    bonus = (sample.range_bonus or 0) if sample.modifiable_range else 0
    if distance == sample.min_range:
        tags.add("AT_MIN_RANGE")
    if distance == sample.max_range + bonus:
        tags.add("AT_MAX_RANGE")
    if distance == sample.max_range + bonus + 1:
        tags.add("BEYOND_MAX_RANGE")
    if sample.modifiable_range:
        tags.add("MODIFIABLE_RANGE")
    return tags


def compare(samples: Iterable[ProofSample], map_provider: Callable[[int], CombatMap | None],
            candidates: Mapping[str, Candidate] = CANDIDATES) -> dict[str, object]:
    samples = list(samples)
    coverage: set[str] = set()
    maps: set[int] = set()
    results = {name: {"agree": 0, "disagree": 0, "unknown": 0, "disagreements": []} for name in candidates}
    skipped = []
    for sample in samples:
        combat_map = map_provider(sample.map_id)
        if combat_map is None:
            skipped.append({"sample_id": sample.sample_id, "reason": "topologie de map indisponible"})
            continue
        maps.add(sample.map_id)
        for target, truth in ([(cell, True) for cell in sorted(sample.targetable)]
                              + [(cell, False) for cell in sorted(sample.not_targetable)]):
            coverage |= case_tags(combat_map, sample, target)
            for name, candidate in candidates.items():
                predicted = candidate(combat_map, sample, target)
                bucket = results[name]
                if predicted is None:
                    bucket["unknown"] += 1
                elif predicted == truth:
                    bucket["agree"] += 1
                else:
                    bucket["disagree"] += 1
                    if len(bucket["disagreements"]) < 50:
                        bucket["disagreements"].append({"sample_id": sample.sample_id, "split": sample.split,
                                                        "target": target, "truth": truth})
    missing = [case for case in REQUIRED_CASES if case not in coverage]
    complete = not missing and len(maps) >= MIN_MAPS
    for bucket in results.values():
        if bucket["disagree"]:
            bucket["verdict"] = "REJETÉ"
        elif not bucket["agree"]:
            bucket["verdict"] = "NON ÉVALUABLE"
        else:
            bucket["verdict"] = "COHÉRENT" if complete and not bucket["unknown"] else "COHÉRENT, PREUVE INSUFFISANTE"
    return {"schema_version": 1, "report": "4c-proof", "samples": len(samples), "maps": sorted(maps),
            "splits": {split: sum(sample.split == split for sample in samples) for split in ("TRAIN", "TEST")},
            "coverage": sorted(coverage), "missing_cases": missing, "skipped": skipped, "candidates": results,
            "rule_status": "UNVERIFIED",
            "note": "Aucune règle n'est adoptée automatiquement ; seul un candidat COHÉRENT à couverture complète "
                    "peut être proposé pour décision humaine."}
