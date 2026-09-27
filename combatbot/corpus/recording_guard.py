"""LOT 3B-6C : garde d'enregistrement du corpus. Mieux vaut perdre une frame qu'en garder une fausse.

Une frame n'est enregistrée que si la map est RÉSOLUE (automatiquement, ou déclarée à la main en
secours), si la topologie GameData chargée est bien celle de cette map, et si la grille est alignée
(ou clairement non affichée : hors combat, écran de résultats — utile à la phase/tour).
"""
from __future__ import annotations

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
    if visibility not in ("VISIBLE", "NOT_VISIBLE"):
        return "Enregistrement bloqué : visibilité de la grille incertaine."
    return None
