"""Régressions LOT 3B-2A-R : dispositions D2P et DLM v11 observées sur le client réel.

Toutes les fixtures sont générées ici, minimales, sans octet provenant du client.
Elles reproduisent la *structure* mesurée (voir GAME-DATA-REAL-VALIDATION.md).
"""
import struct
import zlib

import pytest

from combatbot.gamedata import GameDataError, LocalGameDataProvider
from combatbot.gamedata.formats.archives import (
    D2PLayoutVariant, detect_d2p_layout, read_d2p_index, read_d2p_layout,
)
from combatbot.gamedata.formats.d2o import D2OFile
from combatbot.gamedata.formats.maps import DlmParseError, inspect_dlm, parse_dlm
from combatbot.gamedata.models import GridCoordinate
from combatbot.gamedata.topology import (
    CELL_COUNT, adjacency, build_topology, cell_to_grid, grid_to_cell, neighbors,
)
from combatbot.gamedata.validation import MapValidator, geometry_selfcheck
from test_gamedata import make_d2p, utf

# --- D2P builders -----------------------------------------------------------


def make_d2p_index_first(entries, *, properties=None, property_field="end"):
    """Layout observed in content/maps and content/gfx/world: props | index | data."""
    properties = properties or {}
    property_bytes = b"".join(utf(k) + utf(v) for k, v in properties.items())
    index, data = b"", b""
    for name, payload in entries:
        index += utf(name) + struct.pack(">ii", len(data), len(payload))
        data += payload
    index_offset = 2 + len(property_bytes)
    data_offset = index_offset + len(index)
    last = {"end": index_offset, "count": len(properties)}.get(property_field, property_field)
    footer = struct.pack(">6I", data_offset, len(entries), index_offset, len(entries), 2, last)
    return b"\x02\x01" + property_bytes + index + data + footer


def write(tmp_path, payload, name="archive.d2p"):
    path = tmp_path / name
    path.write_bytes(payload)
    return path


# --- DLM builder reproducing the measured v11 structure ---------------------

FIXTURE = struct.pack(">ihhhhhBBBB", 1, 0, 0, 0, 1000, 1000, 0, 0, 0, 255)


def cell_record(floor=0, flags=0, speed=0, change=0, zone=0, linked=0):
    record = struct.pack(">bHbBB", floor, flags, speed, change, zone)
    if not flags & 1 and not flags & 128:
        record += bytes([linked])
    return record


def make_real_dlm(map_id=101, *, version=11, cells=None, tactical=7, sub_area=5,
                  neighbours=(11, 12, 13, 14), fixtures=(1, 2), layers=2, encrypted=False,
                  extra=b"", fg_count_override=None):
    body = struct.pack(">IBi4iiIIHhhi", 0, 0, sub_area, *neighbours, -1, 0xFF000000, 0xFF666666,
                       100, 0, 0, tactical)
    body += bytes([fixtures[0]]) + FIXTURE * fixtures[0]
    body += bytes([fg_count_override if fg_count_override is not None else fixtures[1]]) + FIXTURE * fixtures[1]
    body += struct.pack(">ii", 0, 1234) + bytes([layers])
    for layer in range(layers):
        body += struct.pack(">BH", layer, 1) + struct.pack(">HH", layer, 2)
        body += bytes([2]) + bytes(19) + bytes([33]) + bytes(18)
    cells = cells or {}
    for identifier in range(CELL_COUNT):
        body += cell_record(**cells.get(identifier, {}))
    body += extra
    raw = b"M" + struct.pack(">BIBBi", version, map_id, int(encrypted), 1, len(body)) + body
    return zlib.compress(raw)


def decoded(payload):
    return zlib.decompress(payload)


def recompress(raw):
    return zlib.compress(raw)


# --- D2P ---------------------------------------------------------------------


def test_d2p_index_before_data_is_supported(tmp_path):
    path = write(tmp_path, make_d2p_index_first([("0/101.dlm", b"abc"), ("1/102.dlm", b"de")],
                                                properties={"link": "maps1.d2p"}))
    layout = read_d2p_layout(path)
    assert layout.variant is D2PLayoutVariant.INDEX_BEFORE_DATA
    assert layout.property_region.start == 2 and layout.property_region.end == layout.index_region.start
    assert layout.index_region.end == layout.data_region.start
    assert layout.data_region.end == path.stat().st_size - 24
    index = read_d2p_index(path)
    assert index["layout"] == "INDEX_BEFORE_DATA"
    assert index["properties"] == {"link": "maps1.d2p"}
    data = path.read_bytes()
    first = index["entries"]["0/101.dlm"]
    assert data[first["offset"]:first["offset"] + first["length"]] == b"abc"


def test_d2p_data_before_index_if_valid(tmp_path):
    path = write(tmp_path, make_d2p([("a.bin", b"xyz")], properties={"link": "next.d2p"}))
    layout = read_d2p_layout(path)
    assert layout.variant is D2PLayoutVariant.DATA_BEFORE_INDEX
    assert layout.data_region.start == 2 and layout.data_region.end == layout.index_region.start
    assert read_d2p_index(path)["properties"] == {"link": "next.d2p"}


def test_d2p_entry_can_reference_data_after_index(tmp_path):
    path = write(tmp_path, make_d2p_index_first([("x.dlm", b"payload")]))
    index = read_d2p_index(path)
    entry = index["entries"]["x.dlm"]
    assert entry["offset"] > index["regions"]["index_region"]["end"] - 1
    assert entry["offset"] >= index["regions"]["data_region"]["start"]


@pytest.mark.parametrize("builder", [make_d2p, make_d2p_index_first])
def test_d2p_entry_cannot_escape_file(tmp_path, builder):
    payload = bytearray(builder([("x", b"abcd")]))
    position = payload.index(b"\x00\x01x") + 3
    payload[position:position + 4] = struct.pack(">i", 1)  # offset 1 + length 4 > data size 4
    with pytest.raises(GameDataError, match="hors de la région"):
        read_d2p_index(write(tmp_path, bytes(payload)))
    payload[position:position + 4] = struct.pack(">i", -1)
    with pytest.raises(GameDataError):
        read_d2p_index(write(tmp_path, bytes(payload)))
    payload[position:position + 4] = struct.pack(">i", 0)
    payload[position + 4:position + 8] = struct.pack(">i", 2**31 - 1)  # no overflow path
    with pytest.raises(GameDataError):
        read_d2p_index(write(tmp_path, bytes(payload)))


def test_d2p_index_region_is_bounded(tmp_path):
    # Declared count cannot fit in the index region.
    payload = bytearray(make_d2p_index_first([("x", b"a")]))
    payload[-24 + 4:-24 + 8] = struct.pack(">I", 5000)
    payload[-24 + 12:-24 + 16] = struct.pack(">I", 5000)
    with pytest.raises(GameDataError, match="trop petite"):
        read_d2p_index(write(tmp_path, bytes(payload)))
    # Index region with unread residual bytes.
    good = make_d2p_index_first([("x", b"a"), ("y", b"b")])
    footer = list(struct.unpack(">6I", good[-24:]))
    footer[1] = footer[3] = 1
    with pytest.raises(GameDataError, match="exactement"):
        read_d2p_index(write(tmp_path, good[:-24] + struct.pack(">6I", *footer)))


@pytest.mark.parametrize("footer", [
    (20, 1, 30, 1, 2, 30),       # data starts inside the index
    (2, 10, 20, 1, 30, 0),       # data region does not reach the index
    (2, 10, 12, 1, 8, 0),        # properties before index in data-first layout
    (30, 1, 20, 1, 3, 20),       # properties not right after header
    (30, 2, 20, 1, 2, 20),       # entry-count fields disagree
    (0, 0, 10, 1, 2, 0),         # "empty" marker with entries
    (0, 0, 0, 0, 0, 0),
])
def test_d2p_overlapping_invalid_regions_rejected(footer):
    with pytest.raises(GameDataError) as caught:
        detect_d2p_layout(100, footer)
    assert caught.value.code == "UNKNOWN_LAYOUT"


def test_d2p_overlapping_entries_rejected(tmp_path):
    payload = bytearray(make_d2p_index_first([("a", b"12"), ("b", b"34")]))
    position = payload.index(b"\x00\x01b") + 3
    payload[position:position + 4] = struct.pack(">i", 1)
    with pytest.raises(GameDataError, match="chevauchantes"):
        read_d2p_index(write(tmp_path, bytes(payload)))


def test_d2p_empty_chained_archive(tmp_path):
    # Measured: 43-byte archive, footer (0, 0, 19, 0, 2, 1) with one link property.
    path = write(tmp_path, b"\x02\x01" + utf("link") + utf("gfx27.d2p") + struct.pack(">6I", 0, 0, 19, 0, 2, 1))
    assert path.stat().st_size == 43
    index = read_d2p_index(path)
    assert index["layout"] == "INDEX_BEFORE_DATA" and index["entries"] == {}
    assert index["properties"] == {"link": "gfx27.d2p"}


@pytest.mark.parametrize("field,ok", [("end", True), ("count", True), (99, False)])
def test_d2p_property_field_variants(tmp_path, field, ok):
    path = write(tmp_path, make_d2p_index_first([("x", b"a")], properties={"link": "n.d2p"}, property_field=field))
    if ok:
        assert read_d2p_index(path)["properties"] == {"link": "n.d2p"}
    else:
        with pytest.raises(GameDataError, match="propriétés"):
            read_d2p_index(path)


def test_detect_layout_uses_offsets_not_file_name(tmp_path):
    payload = make_d2p_index_first([("x", b"a")])
    for name in ("maps0.d2p", "sprites.d2p", "whatever.d2p"):
        assert read_d2p_layout(write(tmp_path, payload, name)).variant is D2PLayoutVariant.INDEX_BEFORE_DATA


def test_real_layout_fixture_discovers_maps(tmp_path):
    root = tmp_path / "client"
    (root / "content" / "maps").mkdir(parents=True)
    (root / "content" / "maps" / "maps0.d2p").write_bytes(make_d2p_index_first(
        [("1/101.dlm", make_real_dlm(101)), ("2/102.dlm", make_real_dlm(102))],
        properties={"link": "maps1.d2p"}))
    (root / "content" / "maps" / "maps1.d2p").write_bytes(make_d2p_index_first([("3/103.dlm", make_real_dlm(103))]))
    provider = LocalGameDataProvider(root)
    report = provider.scan_client()
    assert not report.errors
    assert provider.list_maps() == (101, 102, 103)
    detail = report.format_details["content/maps/maps0.d2p"]
    assert detail["layout"] == "INDEX_BEFORE_DATA" and detail["dlm_entries"] == 2
    assert provider.get_map(103).metadata["tactical_mode_template_id"] == 7
    result = MapValidator(provider).run()
    assert result["stages"]["cells_parsed"] == 3 and result["versions"] == {"11": 3}


def test_duplicate_map_ids_rejected(tmp_path):
    root = tmp_path / "client"
    root.mkdir()
    (root / "a.d2p").write_bytes(make_d2p_index_first([("1/101.dlm", make_real_dlm(101))]))
    (root / "b.d2p").write_bytes(make_d2p([("9/101.dlm", make_real_dlm(101))]))
    provider = LocalGameDataProvider(root)
    report = provider.scan_client()
    assert any("ambiguë" in error for error in report.errors)
    with pytest.raises(GameDataError) as caught:
        provider.get_map(101)
    assert caught.value.code == "AMBIGUOUS_MAP"


# --- DLM ---------------------------------------------------------------------


def test_real_version_dlm_envelope():
    envelope, _ = inspect_dlm(make_real_dlm(4242))
    assert envelope["version"] == 11 and envelope["map_id"] == 4242
    assert envelope["compression"] == "zlib" and envelope["encrypted"] is False
    assert envelope["encryption_version"] == 1
    assert envelope["declared_data_length"] == envelope["decoded_bytes"] - 12


def test_real_version_cell_count_and_metadata():
    game_map = parse_dlm(make_real_dlm(55, tactical=3, sub_area=10, neighbours=(1, 2, 3, 4)), "fixture")
    assert len(game_map.cells) == 560
    assert all(c.raw_flags is not None for c in game_map.cells)  # real maps: no absent cell
    meta = game_map.metadata
    assert meta["tactical_mode_template_id"] == 3 and meta["sub_area_id"] == 10
    assert (meta["top_neighbour_id"], meta["bottom_neighbour_id"],
            meta["left_neighbour_id"], meta["right_neighbour_id"]) == (1, 2, 3, 4)
    assert meta["layer_count"] == 2 and meta["graphical_elements"] == 4
    assert meta["background_fixture_count"] == 1 and meta["foreground_fixture_count"] == 2


@pytest.mark.parametrize("version", [9, 10, 12])
def test_unknown_version_rejected(version):
    with pytest.raises(GameDataError) as caught:
        parse_dlm(make_real_dlm(version=version), "fixture")
    assert caught.value.code == "UNKNOWN_VERSION"


def test_encrypted_map_reported_not_parsed():
    payload = make_real_dlm(encrypted=True)
    envelope, _ = inspect_dlm(payload)
    assert envelope["encrypted"] is True
    with pytest.raises(GameDataError) as caught:
        parse_dlm(payload, "fixture")
    assert caught.value.code == "ENCRYPTED"


def test_truncated_real_layout_rejected_with_forensic_diagnostic():
    raw = decoded(make_real_dlm(77))
    cut = raw[:-300]
    cut = cut[:8] + struct.pack(">i", len(cut) - 12) + cut[12:]
    with pytest.raises(DlmParseError) as caught:
        parse_dlm(recompress(cut), "fixture")
    diagnostic = caught.value.diagnostic
    assert caught.value.code == "TRUNCATED"
    assert diagnostic["map"] == 77 and diagnostic["version"] == 11
    assert diagnostic["stage"].startswith("cell[") and diagnostic["remaining"] < 7
    assert diagnostic["offset"] == len(cut) - diagnostic["remaining"]
    assert "DLM_PARSE_ERROR" in str(caught.value)
    assert len(diagnostic["next"]) <= 32


def test_residual_bytes_rejected():
    raw = decoded(make_real_dlm(78, extra=b"\x01\x02\x03"))
    with pytest.raises(DlmParseError) as caught:
        parse_dlm(recompress(raw), "fixture")
    assert caught.value.code == "UNKNOWN_LAYOUT"
    assert caught.value.diagnostic["stage"] == "end_of_stream"
    assert caught.value.diagnostic["remaining"] == 3 and caught.value.diagnostic["next"] == "010203"


def test_fixture_count_overflow_is_not_silently_repaired():
    # Measured on real map 0: 417 foreground fixtures written after a count byte of 161.
    raw = decoded(make_real_dlm(0, fixtures=(1, 257), fg_count_override=1))
    with pytest.raises(DlmParseError) as caught:
        parse_dlm(recompress(raw), "fixture")
    assert caught.value.code in ("UNKNOWN_LAYOUT", "UNKNOWN_ELEMENT", "CORRUPT", "TRUNCATED")


def test_cell_flags_preserved():
    cells = {
        0: {"flags": 0, "linked": 9},
        1: {"flags": 1 | 8, "speed": -2, "change": 0x40},
        2: {"flags": 2 | 16, "zone": 3, "linked": 4},
        3: {"flags": 32 | 64, "linked": 1},
        4: {"flags": 128},                   # no linked-zone byte
        5: {"flags": 256 | 512 | 4096, "floor": -3, "linked": 2},
    }
    game_map = parse_dlm(make_real_dlm(cells=cells), "fixture")
    c = game_map.cells
    assert c[0].walkable is True and c[0].linked_zone == 9 and c[0].line_of_sight is True
    assert c[1].walkable is False and c[1].line_of_sight is False and c[1].linked_zone is None
    assert c[1].speed == -2 and c[1].map_change_data == 0x40
    assert c[2].non_walkable_during_fight is True and c[2].blue_hint is True and c[2].move_zone == 3
    assert c[3].red_hint is True and c[3].raw_flags == 96
    assert c[4].walkable is True and c[4].linked_zone is None
    assert c[5].raw_flags == 256 | 512 | 4096 and c[5].floor == -30
    assert all(cell.fight_start_allowed is None for cell in c)


# --- Topology ------------------------------------------------------------------


def test_cell_id_grid_round_trip_is_a_bijection():
    check = geometry_selfcheck()
    assert check["bijection"] and check["round_trip"] and check["symmetric_neighbours"]
    assert check["outside_points_mapped"] == 0
    assert check["x_range"] == [0, 33] and check["y_range"] == [-19, 13]


@pytest.mark.parametrize("cell,expected", [
    (0, (0, 0)), (1, (1, 1)), (13, (13, 13)), (14, (1, 0)), (27, (14, 13)), (28, (1, -1)), (559, (33, -6)),
])
def test_known_cell_coordinates(cell, expected):
    assert cell_to_grid(cell) == GridCoordinate(*expected)
    assert grid_to_cell(GridCoordinate(*expected)) == cell


def test_neighbours_centre_edges_and_corners():
    assert sorted(neighbors(15)) == [1, 2, 29, 30]
    assert neighbors(0) == (14,)
    assert neighbors(559) == (545,)
    assert sorted(neighbors(13)) == [26, 27]
    assert all(len(neighbors(c, corners=True)) <= 8 for c in range(CELL_COUNT))
    pairs = adjacency()
    assert all(a in pairs[b] for a in pairs for b in pairs[a])


@pytest.mark.parametrize("bad", [-1, 560, 10_000, 1.5, "3", None])
def test_invalid_cell_ids_rejected(bad):
    with pytest.raises(ValueError):
        cell_to_grid(bad)


def test_outside_coordinates_have_no_cell():
    for coordinate in (GridCoordinate(-1, 0), GridCoordinate(0, 1), GridCoordinate(40, 0), GridCoordinate(14, 14)):
        assert grid_to_cell(coordinate) is None


def test_walkability_does_not_change_topology():
    open_map = parse_dlm(make_real_dlm(), "open")
    blocked = parse_dlm(make_real_dlm(cells={i: {"flags": 1} for i in range(CELL_COUNT)}), "blocked")
    assert build_topology(open_map, coordinates_verified=False).adjacency == \
        build_topology(blocked, coordinates_verified=False).adjacency


# --- D2O ---------------------------------------------------------------------


def make_d2o():
    objects = struct.pack(">i", 1) + struct.pack(">diiI", 101.0, -3, 7, 2) + struct.pack(">i", 2) + \
        struct.pack(">ii", 5, 6) + struct.pack(">?", True)
    index_offset = 7 + len(objects)
    classes = struct.pack(">i", 1) + struct.pack(">i", 1) + utf("MapPosition") + utf("com.fixture") + \
        struct.pack(">i", 6) + utf("id") + struct.pack(">i", -4) + utf("posX") + struct.pack(">i", -1) + \
        utf("posY") + struct.pack(">i", -1) + utf("tacticalModeTemplateId") + struct.pack(">i", -6) + \
        utf("cells") + struct.pack(">i", -99) + utf("Vector.<int>") + struct.pack(">i", -1) + \
        utf("outdoor") + struct.pack(">i", -2)
    index = struct.pack(">I", 8) + struct.pack(">iI", 101, 7)
    return b"D2O" + struct.pack(">I", index_offset) + objects + index + classes


def test_d2o_schema_and_objects(tmp_path):
    path = tmp_path / "MapPositions.d2o"
    path.write_bytes(make_d2o())
    table = D2OFile(path)
    assert table.schema()[0]["fields"]["cells"] == "vector<Vector.<int>>"
    assert table.get(101) == {"_class": "MapPosition", "id": 101.0, "posX": -3, "posY": 7,
                              "tacticalModeTemplateId": 2, "cells": [5, 6], "outdoor": True}
    with pytest.raises(GameDataError):
        table.get(999)


def test_d2o_corrupt_class_table(tmp_path):
    data = bytearray(make_d2o())
    position = data.index(b"\x00\x0bMapPosition") - 8
    data[position:position + 4] = struct.pack(">i", -5)  # class count
    path = tmp_path / "bad.d2o"
    path.write_bytes(bytes(data))
    with pytest.raises(GameDataError):
        D2OFile(path)
