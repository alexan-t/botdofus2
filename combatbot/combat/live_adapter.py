"""Adaptateur (préparé, non branché) : paquet d'observation réelle → ``closed_loop.Observation``.

Le futur branchement de la boucle d'action sur ``RealCombatObserver`` n'aura qu'à appeler
``observation_from_packet`` à chaque frame. L'identité de layout est calculée exactement comme dans
``ScreenGeometry.from_calibration`` : si la calibration ne s'applique plus au client (taille, ratio,
signature), le digest est None et toute étape en cours est interrompue par le coordinateur.
Typage canard : aucun import de vision (numpy / OpenCV) ici.
"""
from __future__ import annotations

from combatbot.combat.closed_loop import Observation
from combatbot.combat.state import RealCombatState


def layout_digest_for(calibration, client_width: int, client_height: int) -> str | None:
    compatibility = calibration.layout_compatibility(client_width, client_height)
    if not compatibility.compatible or compatibility.requires_revalidation:
        return None
    return str(compatibility.details.get("current_digest") or "") or None


def observation_from_packet(packet, *, frame_id: int, at: float, hwnd: int | None, client_width: int,
                            client_height: int, calibration, map_id: int | None) -> Observation:
    semantic = packet.metadata.get("semantic_combat_state") or {}
    state = RealCombatState.from_observation(packet.observation, map_id=map_id, phase=semantic.get("phase"),
                                             turn=semantic.get("turn_owner"))
    return Observation(state, at, frame_id, hwnd, layout_digest_for(calibration, client_width, client_height))
