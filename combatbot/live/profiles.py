"""Choix non ambigu du profil pour la recette : jamais deviné.

Un seul profil en base → celui-là. Plusieurs profils → aucun choix automatique, même si un « dernier
profil utilisé » est mémorisé : la commande exacte à relancer est rendue avec la liste des profils.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProfileResolution:
    profile_id: int | None
    reason: str
    candidates: tuple[tuple[int, str], ...]

    def to_dict(self) -> dict[str, object]:
        return {"profile_id": self.profile_id, "reason": self.reason,
                "candidates": [{"id": pid, "label": label} for pid, label in self.candidates]}


def resolve_profile(storage, requested: int | None = None) -> ProfileResolution:
    """``storage`` : objet exposant ``list_profiles`` (Storage)."""
    profiles = tuple((int(item.id), str(item.label)) for item in storage.list_profiles() if item.id is not None)
    ids = {pid for pid, _label in profiles}
    if requested is not None:
        if requested in ids:
            return ProfileResolution(requested, "profil demandé explicitement", profiles)
        return ProfileResolution(None, f"profil {requested} introuvable", profiles)
    if not profiles:
        return ProfileResolution(None, "aucun profil en base", profiles)
    if len(profiles) == 1:
        return ProfileResolution(profiles[0][0], "profil unique en base", profiles)
    return ProfileResolution(None, f"{len(profiles)} profils en base : choix ambigu, préciser -ProfileId", profiles)
