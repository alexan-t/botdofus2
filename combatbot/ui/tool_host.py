"""Présentation des outils avancés (calibration, projection, recette, HUD, annotations).

Sans présentateur (application historique seule, tests), les outils gardent leur fenêtre habituelle.
DofBot2 enregistre un présentateur qui les ouvre en plein écran dans sa propre fenêtre (écran A7)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QDialog


TOOLS = {
    "zones": ("Calibrer les zones", "Déplacez les cadres sur l'aperçu, puis validez chaque zone"),
    "proj": ("Projection de grille", "Alignez la grille isométrique sur les cellules du combat"),
    "recette": ("Recette de grille", "Jugez la grille GameData sur une capture réelle"),
    "hud": ("Revue HUD PA/PM", "Confirmez ou corrigez la lecture du bot, sans voir sa prédiction"),
    "collecte": ("Collecte HUD réelle", "Capture PA/PM pendant vos combats, sans aucune action"),
    "entites": ("Annoter les entités", "Placez joueur et ennemis par cellule sur chaque frame"),
    "phase": ("Phase et tour", "Indiquez l'état du combat sur chaque frame"),
    "annot": ("Annotation", "Vérité terrain humaine pour cette observation"),
    "portee": ("Preuve portée / LOS (4C)", "Marquez les cellules que le client affiche ciblables pour ce sort"),
}

Presenter = Callable[[QDialog, str], bool]
_presenter: Presenter | None = None


def set_presenter(presenter: Presenter | None) -> None:
    global _presenter
    _presenter = presenter


def present_tool(dialog: QDialog, key: str) -> bool:
    """À appeler avant ``exec()``/``show()`` ; False = aucun présentateur, garder l'affichage habituel."""
    if key not in TOOLS:
        raise KeyError(f"Outil inconnu : {key}")
    return _presenter is not None and _presenter(dialog, key)
