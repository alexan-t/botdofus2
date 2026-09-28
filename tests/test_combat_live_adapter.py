"""Adaptateur paquet réel → Observation de la boucle (aucun branchement, aucune action)."""
from __future__ import annotations

from combatbot.combat.closed_loop import critical_change
from combatbot.combat.live_adapter import layout_digest_for, observation_from_packet
from combatbot.vision.combat_models import ObservationPacket
from tests.test_combat_screen_actions import calibration, geometry
from tests.test_dry_run_replay import PLAYER, observation


def packet(**metadata) -> ObservationPacket:
    return ObservationPacket(observation(), None, None, 12.0, metadata)


def test_packet_becomes_a_loop_observation_with_the_geometry_digest() -> None:
    geo = geometry()
    result = observation_from_packet(packet(semantic_combat_state={"phase": "FIGHTING", "turn_owner": "PLAYER"}),
                                     frame_id=7, at=5.0, hwnd=geo.hwnd, client_width=1000, client_height=800,
                                     calibration=calibration(), map_id=123)
    assert (result.frame_id, result.at, result.hwnd) == (7, 5.0, geo.hwnd)
    assert result.layout_digest == geo.layout_digest and critical_change(result, geo) is None
    assert (result.state.phase, result.state.turn, result.state.player_cell_id) == ("FIGHTING", "PLAYER", PLAYER)
    assert result.state.safe_for_decision is True


def test_unknown_semantics_and_incompatible_layout_stay_unknown() -> None:
    result = observation_from_packet(packet(), frame_id=1, at=0.0, hwnd=42, client_width=1600, client_height=700,
                                     calibration=calibration(), map_id=None)
    assert result.state.phase is None and result.state.turn is None and result.layout_digest is None
    assert critical_change(result, geometry()) == "layout / calibration changé"
    assert layout_digest_for(calibration(), 1000, 800) == geometry().layout_digest
