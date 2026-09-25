"""Échantillon de cellules à trancher EMPTY / OCCUPIED / UNKNOWN (LOT 3B-5D).

La sélection dépend seulement de l'identifiant d'observation et de la grille GameData projetée
enregistrée : jamais d'une prédiction du détecteur, jamais d'une annotation humaine. Elle est donc
fixée avant que l'annotateur regarde la frame, et reproductible. Les cellules traversables
entièrement visibles sont réparties en 3 × 3 zones (haut/centre/bas × gauche/centre/droite) selon
leur centre ; chaque zone fournit ``per_zone`` cellules, ordonnées par un hachage stable.
"""
from __future__ import annotations

import hashlib

SAMPLE_VERSION = "zones3x3-sha256-v1"
SAMPLE_LABELS = ("EMPTY", "OCCUPIED", "UNKNOWN")
ZONE_NAMES = tuple(f"{row}-{column}" for row in ("haut", "centre", "bas")
                   for column in ("gauche", "centre", "droite"))


def _visible(cell: dict, frame_size: tuple[int, int] | None) -> bool:
    if frame_size is None:
        return True
    width, height = frame_size
    return all(0 <= float(x) < width and 0 <= float(y) < height for x, y in cell["polygon"])


def _order(observation_id: str, cell_id: int) -> str:
    return hashlib.sha256(f"{SAMPLE_VERSION}|{observation_id}|{cell_id}".encode()).hexdigest()


def sample_cells(cells: list[dict], observation_id: str, *, frame_size: tuple[int, int] | None = None,
                 per_zone: int = 1) -> list[dict]:
    """``cells`` : cellules projetées annotables (``projected_cells``). Retour : ``cell_id`` + zone."""
    visible = [cell for cell in cells if cell.get("cell_id") is not None and cell.get("center")
               and _visible(cell, frame_size)]
    if not visible:
        return []
    xs = [float(cell["center"][0]) for cell in visible]
    ys = [float(cell["center"][1]) for cell in visible]
    x0, y0 = min(xs), min(ys)
    width, height = max(max(xs) - x0, 1e-6), max(max(ys) - y0, 1e-6)
    zones: dict[str, list[int]] = {name: [] for name in ZONE_NAMES}
    for cell in visible:
        column = min(2, int(3 * (float(cell["center"][0]) - x0) / width))
        row = min(2, int(3 * (float(cell["center"][1]) - y0) / height))
        zones[ZONE_NAMES[3 * row + column]].append(int(cell["cell_id"]))
    selected = []
    for zone in ZONE_NAMES:
        for cell_id in sorted(zones[zone], key=lambda value: _order(observation_id, value))[:per_zone]:
            selected.append({"cell_id": cell_id, "zone": zone})
    return selected
