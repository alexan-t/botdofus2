from __future__ import annotations

import pytest

from combatbot.vision.coordinates import (
    ClientBox, ClientPoint, ClientSize, CombatPoint, LayoutCompatibilityReason,
    LayoutSignature, LayoutTransform, NormalizedPoint, NormalizedRect, ScreenPoint,
    ROUNDTRIP_TOLERANCE, compatibility,
)
from combatbot.vision.models import Calibration, RelativeRect


def transform(origin=(100, 200), size=(1000, 600), combat=(0.1, 0.2, 0.7, 0.6)) -> LayoutTransform:
    return LayoutTransform(ScreenPoint(*origin), ClientSize(*size), NormalizedRect(*combat))


@pytest.mark.parametrize("size,point", [
    ((800, 600), (0, 0)), ((800, 600), (800, 600)), ((1920, 1080), (913.25, 741.5)),
    ((2560, 1377), (1, 1376)),
])
def test_client_normalized_roundtrip(size, point) -> None:
    layout = transform(size=size)
    original = ClientPoint(*point)
    restored = layout.normalized_to_client(layout.client_to_normalized(original))
    assert restored.x == pytest.approx(original.x, abs=ROUNDTRIP_TOLERANCE)
    assert restored.y == pytest.approx(original.y, abs=ROUNDTRIP_TOLERANCE)


@pytest.mark.parametrize("origin", [(0, 0), (100, 200), (-1920, 40)])
def test_screen_client_roundtrip(origin) -> None:
    layout = transform(origin=origin)
    original = ScreenPoint(origin[0] + 333.5, origin[1] + 122.25)
    restored = layout.client_to_screen(layout.screen_to_client(original))
    assert restored == original


def test_combat_client_roundtrip() -> None:
    layout = transform(size=(1000, 500), combat=(0.1, 0.2, 0.7, 0.6))
    assert layout.combat_client_box == ClientBox(100, 100, 700, 300)
    original = ClientPoint(460.5, 210.25)
    combat = layout.client_to_combat(original)
    assert combat == CombatPoint(360.5, 110.25)
    assert layout.combat_to_client(combat) == original


def test_combat_zone_offset() -> None:
    layout = transform(origin=(500, 250), size=(1200, 800), combat=(.2, .1, .6, .7))
    assert layout.combat_to_client(CombatPoint(0, 0)) == ClientPoint(240, 80)
    assert layout.client_to_screen(ClientPoint(240, 80)) == ScreenPoint(740, 330)


def test_window_moved_does_not_change_normalized_layout() -> None:
    first = transform(origin=(0, 0))
    moved = transform(origin=(1700, -200))
    client = ClientPoint(250, 300)
    assert first.client_to_normalized(client) == moved.client_to_normalized(client)
    assert first.combat_zone == moved.combat_zone


def test_same_layout_different_screen_origin() -> None:
    first = transform(origin=(10, 20))
    second = transform(origin=(1010, 720))
    assert first.client_to_screen(ClientPoint(0, 0)) != second.client_to_screen(ClientPoint(0, 0))
    signature = LayoutSignature.create(first.client_size, {"combat": first.combat_zone})
    other = LayoutSignature.create(second.client_size, {"combat": second.combat_zone})
    assert compatibility(other, signature).reason is LayoutCompatibilityReason.COMPATIBLE


def test_client_resize_same_aspect() -> None:
    saved = LayoutSignature.create(ClientSize(1000, 600), {"combat": NormalizedRect(.1, .2, .7, .6)})
    current = LayoutSignature.create(ClientSize(1250, 750), {"combat": NormalizedRect(.1, .2, .7, .6)})
    result = compatibility(current, saved)
    assert result.compatible and result.reason is LayoutCompatibilityReason.CLIENT_SIZE_SCALED


def test_client_resize_changed_aspect() -> None:
    saved = LayoutSignature.create(ClientSize(1000, 600), {"combat": NormalizedRect(.1, .2, .7, .6)})
    current = LayoutSignature.create(ClientSize(1200, 600), {"combat": NormalizedRect(.1, .2, .7, .6)})
    result = compatibility(current, saved)
    assert not result.compatible and result.reason is LayoutCompatibilityReason.ASPECT_RATIO_CHANGED


def test_points_on_borders() -> None:
    layout = transform(size=(800, 600))
    assert layout.normalized_to_client(NormalizedPoint(0, 0)) == ClientPoint(0, 0)
    assert layout.normalized_to_client(NormalizedPoint(1, 1)) == ClientPoint(800, 600)
    assert layout.client_to_normalized(ClientPoint(800, 600)) == NormalizedPoint(1, 1)


def test_rounding_policy() -> None:
    assert ClientPoint(10.49, 11.5).rounded() == (10, 12)
    assert ClientPoint(10.5, 12.5).rounded() == (10, 12)  # arrondi au pair de Python


def test_layout_signature_serialization() -> None:
    signature = LayoutSignature.create(
        ClientSize(800, 600),
        {"spell_bar": NormalizedRect(.2, .8, .6, .1), "combat": NormalizedRect(.1, .1, .8, .65)},
        {"spell_bar": "abc123"},
    )
    restored = LayoutSignature.from_json(signature.to_json())
    assert restored == signature and restored.digest == signature.digest
 

def test_layout_signature_compatibility() -> None:
    signature = LayoutSignature.create(
        ClientSize(800, 600),
        {"spell_bar": NormalizedRect(.2, .8, .6, .1), "combat": NormalizedRect(.1, .1, .8, .65)},
        {"spell_bar": "abc123"},
    )
    changed = LayoutSignature.create(
        ClientSize(800, 600), {"combat": NormalizedRect(.12, .1, .78, .65),
                              "spell_bar": NormalizedRect(.2, .8, .6, .1)},
        {"spell_bar": "abc123"},
    )
    assert compatibility(changed, signature).reason is LayoutCompatibilityReason.COMBAT_ZONE_CHANGED


def test_layout_transform_serialization() -> None:
    layout = transform(origin=(-800, 150), size=(1600, 900), combat=(.05, .08, .9, .72))
    assert LayoutTransform.from_dict(layout.to_dict()) == layout


def test_legacy_calibration_loads() -> None:
    calibration = Calibration(1, 800, 600, {"combat": RelativeRect(.1, .1, .8, .7)},
                              layout_signature="8e1b-old-digest")
    result = calibration.layout_compatibility(800, 600)
    assert result.compatible
    assert result.reason is LayoutCompatibilityReason.LEGACY_SIGNATURE
    assert result.requires_revalidation
