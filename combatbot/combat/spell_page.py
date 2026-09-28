"""Page de sorts affichée : **confirmée par l'utilisateur**, jamais supposée (pas de « page 1 » par défaut).

La page n'est pas lisible de façon prouvée à l'écran. Le mécanisme est donc fail-closed :

1. l'utilisateur confirme « la barre affiche la page N » ; on mémorise la signature de la barre à cet
   instant (hash visuel de chaque case, même découpage que le scan des sorts) ;
2. à chaque frame, la signature courante est comparée à celle mémorisée : si trop de cases diffèrent,
   la barre a changé (autre page, autre personnage, autre layout) et la page redevient inconnue ;
3. une page inconnue donne ``visible_page=None`` : tout CAST est REFUSED par le traducteur 5A1.

La signature ne sert qu'à **invalider**, jamais à déduire une autre page. Module pur (hashs en hexadécimal).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

MAX_HASH_DISTANCE = 10          # bits sur 64 (dHash 8×8) : icône grisée/cooldown encore reconnue
MIN_AGREEMENT = 0.8             # part minimale des cases non vides qui doivent concorder
MIN_COMPARED = 2                # en dessous, rien de prouvé → page inconnue


def hash_distance(a: str, b: str) -> int:
    return (int(a, 16) ^ int(b, 16)).bit_count()


@dataclass(frozen=True)
class ConfirmedSpellPage:
    page: int
    signature: Mapping[int, str | None]            # case → hash (None = case vide)
    confirmed_at: str

    def to_dict(self) -> dict[str, object]:
        return {"page": self.page, "signature": {str(slot): value for slot, value in self.signature.items()},
                "confirmed_at": self.confirmed_at}

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> "ConfirmedSpellPage":
        signature = raw.get("signature")
        if not isinstance(signature, Mapping) or int(raw["page"]) < 1:
            raise ValueError("Page de sorts confirmée invalide")
        return cls(int(raw["page"]), {int(slot): (str(value) if value else None) for slot, value in signature.items()},
                   str(raw.get("confirmed_at", "")))


def signature_agreement(current: Mapping[int, str | None], reference: Mapping[int, str | None]
                        ) -> tuple[bool, str]:
    """(la barre est-elle toujours celle confirmée ?, raison)."""
    if set(current) != set(reference):
        return False, "découpage de la barre différent"
    compared = agree = 0
    for slot, expected in reference.items():
        observed = current[slot]
        if expected is None and observed is None:
            continue
        compared += 1
        if expected is not None and observed is not None and hash_distance(expected, observed) <= MAX_HASH_DISTANCE:
            agree += 1
    if compared < MIN_COMPARED:
        return False, "trop peu de cases occupées pour reconnaître la barre"
    ratio = agree / compared
    if ratio < MIN_AGREEMENT:
        return False, f"barre changée ({agree}/{compared} cases concordantes)"
    return True, f"barre identique ({agree}/{compared} cases concordantes)"


def visible_page(confirmed: ConfirmedSpellPage | None, current: Mapping[int, str | None] | None
                 ) -> tuple[int | None, str]:
    if confirmed is None:
        return None, "page de sorts jamais confirmée"
    if current is None:
        return None, "barre de sorts illisible"
    same, reason = signature_agreement(current, confirmed.signature)
    return (confirmed.page, reason) if same else (None, reason)


SETTING_KEY = "confirmed_spell_page"


def load_confirmed_page(storage, profile_id: int) -> ConfirmedSpellPage | None:
    """``storage`` : objet exposant ``get_profile_setting`` ; valeur illisible = jamais confirmée."""
    raw = storage.get_profile_setting(profile_id, SETTING_KEY, None)
    if not isinstance(raw, Mapping):
        return None
    try:
        return ConfirmedSpellPage.from_dict(raw)
    except (KeyError, TypeError, ValueError):
        return None


def save_confirmed_page(storage, profile_id: int, confirmed: ConfirmedSpellPage | None) -> None:
    storage.set_profile_setting(profile_id, SETTING_KEY, confirmed.to_dict() if confirmed is not None else None)
