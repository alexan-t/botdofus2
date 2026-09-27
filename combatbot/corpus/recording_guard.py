"""LOT 3B-6C : garde d'enregistrement du corpus. Mieux vaut perdre une frame qu'en garder une fausse.

Une frame n'est enregistrée que si la map est RÉSOLUE (automatiquement, ou déclarée à la main en
secours), si la topologie GameData chargée est bien celle de cette map, et si une grille VISIBLE est
alignée. Une grille non visible ou incertaine (exploration, fenêtre de résultats) n'empêche pas
l'enregistrement — ces frames servent à la phase/tour — mais la frame est marquée
``grid-not-aligned`` et exclue de l'annotation des entités.
"""
from __future__ import annotations

GRID_NOT_ALIGNED_TAG = "grid-not-aligned"

MANUAL_SOURCES = ("user_verified_mapid", "manual_guess")


def recording_block_reason(packet) -> str | None:
    observation = packet.observation
    grid = observation.grid
    if grid.grid_source != "GAMEDATA_PROJECTED":
        return "Enregistrement bloqué : map/grille non résolue (grille GameData absente)."
    resolution = (packet.metadata or {}).get("map_resolution")
    manual = grid.map_id_source in MANUAL_SOURCES
    if not manual:
        if not resolution or resolution.get("status") != "RESOLVED" or resolution.get("map_id") is None:
            status = (resolution or {}).get("status", "UNKNOWN")
            return f"Enregistrement bloqué : map/grille non résolue (map {status})."
        if grid.map_id_declared != resolution.get("map_id"):
            return "Enregistrement bloqué : la grille chargée n'est pas encore celle de la map détectée."
    visibility = grid.grid_visibility_state
    alignment = (grid.alignment or {}).get("status")
    if visibility == "VISIBLE" and alignment != "ALIGNED":
        return "Enregistrement bloqué : map détectée, mais projection de grille à vérifier."
    return None


def recording_tags(packet) -> tuple[str, ...]:
    """Frame gardée pour la phase/tour mais sans grille alignée : jamais utilisée pour les entités."""
    grid = packet.observation.grid
    aligned = grid.grid_visibility_state == "VISIBLE" and (grid.alignment or {}).get("status") == "ALIGNED"
    return () if aligned else (GRID_NOT_ALIGNED_TAG,)
