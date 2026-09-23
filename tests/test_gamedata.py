"""Fixtures fabriquées ici depuis une description de structure, sans asset client."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import struct
import zlib

import pytest

from combatbot.gamedata import GameDataError, LocalGameDataProvider
from combatbot.gamedata.discovery import discover_client
from combatbot.gamedata.formats.archives import inspect_d2o, read_d2p_index
from combatbot.gamedata.formats.binary import Reader, map_payload
from combatbot.gamedata.formats.maps import inspect_dlm, parse_dlm

# Synthetic map: six explicit cells then absent-cell markers, not real game data.
FLAGS = (0, 1 | 8, 2, 16, 32, 128)


def make_dlm(map_id=123, *, version=11, compressed=True, encrypted=False, graphics=False):
    body = bytes(47) + bytes(2) + bytes(8)
    if graphics:
        # one layer, one graphical cell, one graphical + one sound element
        body += struct.pack(">BBHHH", 1, 2, 1, 0, 2) + bytes([2]) + bytes(19) + bytes([33]) + bytes(18)
    else:
        body += bytes([0])
    for index in range(560):
        if index >= len(FLAGS):
            body += b"\x80"
            continue
        flags = FLAGS[index]
        body += struct.pack(">bHbbb", -2, flags, 0, 0, 0)
        if not flags & 1 and not flags & 128:
            body += bytes([0])
    result = b"M" + struct.pack(">BIBBi", version, map_id, int(encrypted), 0, len(body)) + body
    return zlib.compress(result) if compressed else result


def utf(text):
    encoded = text.encode()
    return struct.pack(">H", len(encoded)) + encoded


def corrected_length(payload):
    return payload[:8] + struct.pack(">i", len(payload) - 12) + payload[12:]


def make_d2p(entries, *, properties=None):
    data = b""
    index = b""
    for name, payload in entries:
        index += utf(name) + struct.pack(">ii", len(data), len(payload))
        data += payload
    properties = properties or {}
    property_bytes = b"".join(utf(key) + utf(value) for key, value in properties.items())
    footer = struct.pack(">6I", 2, len(data), 2 + len(data), len(entries),
                         2 + len(data) + len(index), len(properties))
    return b"\x02\x01" + data + index + property_bytes + footer


@pytest.fixture
def client(tmp_path):
    root = tmp_path / "client"
    root.mkdir()
    (root / "content" / "maps0").mkdir(parents=True)
    (root / "content" / "maps0" / "maps.d2p").write_bytes(make_d2p([
        ("0/123.dlm", make_dlm()), ("1/124.dlm", make_dlm(124)),
    ]))
    (root / "version.txt").write_text("2.64.5.0", encoding="utf-8")
    return root


def test_provider_without_client():
    provider = LocalGameDataProvider()
    report = provider.scan_client()
    assert report.status == "NOT_CONFIGURED"
    assert report.message == "Dossier du client DOFUS non configuré"
    assert provider.list_maps() == ()
    with pytest.raises(GameDataError, match="non configuré"):
        provider.get_map(123)


def test_missing_empty_and_drive_root(tmp_path):
    assert discover_client(tmp_path / "absent").status == "INVALID_DIRECTORY"
    assert discover_client(tmp_path).status == "EMPTY"
    assert discover_client(Path(tmp_path.anchor)).status == "INVALID_DIRECTORY"


def test_inventory_headers_hashes_and_declared_version(client):
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in client.rglob("*") if p.is_file()}
    report = discover_client(client, hashes=True)
    assert report.detected_version == "2.64.5.0"
    archive = next(entry for entry in report.files if entry.extension == ".d2p")
    assert archive.header_hex.startswith("0201")
    assert archive.sha256 == before[str(client / archive.relative_path)]
    assert archive.modified_utc and archive.size > 0
    assert archive.relative_path == "content/maps0/maps.d2p"
    assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in client.rglob("*") if p.is_file()}


def test_version_is_never_inferred_from_directory_name(tmp_path):
    root = tmp_path / "DOFUS-2.64.5"
    root.mkdir()
    (root / "unknown.bin").write_bytes(b"random")
    report = discover_client(root)
    assert report.detected_version is None
    assert report.files[0].classification == "inconnu"


def test_air_descriptor_version_and_conflict(client):
    (client / "application.xml").write_text(
        '<application xmlns="urn:adobe:air:application:3.0"><id>Dofus</id><versionNumber>2.65.0</versionNumber></application>',
        encoding="utf-8",
    )
    report = discover_client(client)
    assert report.detected_version is None
    assert any("contradictoires" in error for error in report.errors)


def test_permission_refused_is_reported(client, monkeypatch):
    original = Path.open
    def denied(path, *args, **kwargs):
        if path.suffix == ".d2p":
            raise PermissionError("fixture: accès refusé")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    report = discover_client(client)
    assert any("accès refusé" in error for error in report.errors)


def test_d2p_index_without_payload_extraction(client):
    index = read_d2p_index(client / "content/maps0/maps.d2p")
    assert index["endian"] == "big"
    assert set(index["entries"]) == {"0/123.dlm", "1/124.dlm"}
    assert index["entries"]["0/123.dlm"]["offset"] == 2


@pytest.mark.parametrize("payload", [b"", b"\x02\x01", b"XX" + bytes(40),
    b"\x02\x01" + bytes(24), make_d2p([("../123.dlm", b"M")]),
    make_d2p([("x", b"1"), ("x", b"2")])])
def test_invalid_archive_is_rejected(tmp_path, payload):
    path = tmp_path / "bad.d2p"
    path.write_bytes(payload)
    with pytest.raises(GameDataError):
        read_d2p_index(path)


def test_out_of_bounds_archive_entry(tmp_path):
    payload = bytearray(make_d2p([("x", b"a")]))
    payload[6:10] = struct.pack(">i", 1000)  # entry offset, outside payload
    path = tmp_path / "bad.d2p"
    path.write_bytes(payload)
    with pytest.raises(GameDataError):
        read_d2p_index(path)


def test_d2o_index_only_and_bad_endianness(tmp_path):
    path = tmp_path / "Maps.d2o"
    path.write_bytes(b"D2O" + struct.pack(">I", 11) + bytes(4) + struct.pack(">Iii", 8, 77, 7))
    assert inspect_d2o(path)["object_count"] == 1
    assert inspect_d2o(path)["classes_decoded"] is False
    path.write_bytes(b"D2O" + struct.pack("<I", 11) + bytes(20))
    with pytest.raises(GameDataError):
        inspect_d2o(path)


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("graphics", [False, True])
def test_minimal_dlm_cells_and_optional_fields(compressed, graphics):
    payload = make_dlm(compressed=compressed, graphics=graphics)
    metadata, _ = inspect_dlm(payload)
    assert metadata["map_id"] == 123 and metadata["version"] == 11
    assert metadata["compression"] == ("zlib" if compressed else "none")
    game_map = parse_dlm(payload, "synthetic")
    assert len(game_map.cells) == 560
    assert [int(c.cell_id) for c in game_map.cells] == list(range(560))
    assert game_map.cells[0].walkable is True and game_map.cells[0].floor == -20
    assert game_map.cells[1].walkable is False and game_map.cells[1].line_of_sight is False
    assert game_map.cells[2].walkable is True and game_map.cells[2].non_walkable_during_fight is True
    assert game_map.cells[3].blue_hint is True and game_map.cells[4].red_hint is True
    assert game_map.cells[6].walkable is None and game_map.cells[6].floor is None
    assert all(c.fight_start_allowed is None and c.logical_position is None and c.movement_cost is None for c in game_map.cells)
    assert game_map.width is None and not game_map.compatibility_verified


def test_no_cell_to_screen_or_cell_to_our_grid_assumptions():
    game_map = parse_dlm(make_dlm(), "synthetic")
    summary = game_map.summary()
    assert summary["fight_cells"] is None
    assert summary["walkable"] == 5
    assert summary["walkability_unknown"] == 554
    assert summary["map_parsed"] and not summary["topology_extracted"]
    serialized = json.loads(json.dumps(game_map.to_dict()))
    assert serialized["cells"][0]["logical_position"] is None
    assert serialized["cells"][0]["cell_id"] == 0


@pytest.mark.parametrize("payload,code", [
    (make_dlm(version=12), "UNKNOWN_VERSION"),
    (make_dlm(encrypted=True), "ENCRYPTED"),
    (corrected_length(make_dlm(compressed=False)[:-1]), "TRUNCATED"),
    (corrected_length(make_dlm(compressed=False) + b"x"), "UNKNOWN_LAYOUT"),
    (make_dlm(compressed=False)[:-1], "CORRUPT"),
    (b"random", "UNKNOWN_FORMAT"),
    (zlib.compress(b"random"), "UNKNOWN_FORMAT"),
    (make_dlm()[:-2], "CORRUPT"),
])
def test_unsupported_or_corrupt_dlm(payload, code):
    with pytest.raises(GameDataError) as caught:
        parse_dlm(payload, "synthetic")
    assert caught.value.code == code


def test_decompression_limit(monkeypatch):
    import combatbot.gamedata.formats.binary as binary
    monkeypatch.setattr(binary, "MAX_MAP_BYTES", 1024)
    with pytest.raises(GameDataError) as caught:
        map_payload(zlib.compress(b"M" + bytes(4096)))
    assert caught.value.code == "LIMIT"


def test_provider_cache_readonly_and_exports(client, tmp_path):
    cache = tmp_path / "outputs" / "cache"
    original = {p.relative_to(client).as_posix(): (p.stat().st_mtime_ns, p.read_bytes()) for p in client.rglob("*") if p.is_file()}
    provider = LocalGameDataProvider(client, cache_dir=cache)
    report = provider.scan_client()
    assert report.indexed_maps == 2 and report.readable_maps is None
    assert not report.map_parsed and report.verdict == "NO"
    assert provider.list_maps() == (123, 124)
    game_map = provider.get_map(123)
    assert provider.get_map(123) is game_map
    assert provider.get_fight_cells(123) is None
    topology = provider.get_map_topology(123)
    # Logical topology only (validated on the real client); no pixel projection.
    assert topology.coordinates_verified and len(topology.adjacency) == 560
    assert topology.coordinates[14].x == 1 and topology.coordinates[14].y == 0
    assert report.readable_maps == 1 and report.verdict == "PARTIAL"
    assert report.scan_seconds >= 0 and report.python_peak_bytes > 0
    destination = provider.export_map(123, tmp_path / "outputs" / "map_123.json")
    assert json.loads(destination.read_text(encoding="utf-8"))["map_id"] == 123
    assert original == {p.relative_to(client).as_posix(): (p.stat().st_mtime_ns, p.read_bytes()) for p in client.rglob("*") if p.is_file()}
    second = LocalGameDataProvider(client, cache_dir=cache)
    assert second.scan_client().index_cache_hit
    assert second.get_map(124).map_id == 124


def test_stale_map_and_index_invalidated(client, tmp_path):
    cache = tmp_path / "cache"
    provider = LocalGameDataProvider(client, cache_dir=cache)
    provider.scan_client()
    provider.get_map(123)
    path = client / "content/maps0/maps.d2p"
    previous = path.stat().st_mtime_ns
    os.utime(path, ns=(previous + 2_000_000_000, previous + 2_000_000_000))
    with pytest.raises(GameDataError) as caught:
        provider.get_map(123)
    assert caught.value.code == "STALE_INDEX"
    assert provider.report.readable_maps == 0 and not provider.report.map_parsed
    assert provider.report.verdict == "NO"
    assert not provider.scan_client().index_cache_hit
    assert provider.get_map(123).map_id == 123


def test_bad_json_cache_is_discarded(client, tmp_path):
    provider = LocalGameDataProvider(client, cache_dir=tmp_path / "cache")
    provider.scan_client()
    next((tmp_path / "cache").glob("*.json")).write_text("{broken", encoding="utf-8")
    assert not provider.scan_client().index_cache_hit
    assert provider.get_map(123).map_id == 123


def test_malformed_cache_record_is_rebuilt(client, tmp_path):
    provider = LocalGameDataProvider(client, cache_dir=tmp_path / "cache")
    provider.scan_client()
    path = next((tmp_path / "cache").glob("*.json"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    del raw["maps"]["123"][0]["entry"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert not provider.scan_client().index_cache_hit
    assert provider.get_map(123).map_id == 123


def test_no_writes_even_exports_or_cache_inside_client(client):
    provider = LocalGameDataProvider(client, cache_dir=client / "cache")
    report = provider.scan_client()
    assert any("Écriture interdite" in error for error in report.errors)
    with pytest.raises(GameDataError, match="Écriture interdite"):
        provider.export_map(123, client / "map.json")
    with pytest.raises(GameDataError, match="Écriture interdite"):
        provider.export_report(client / "report.json")
    assert not (client / "cache").exists() and not (client / "map.json").exists()


def test_unknown_missing_and_ambiguous_maps(client):
    provider = LocalGameDataProvider(client)
    provider.scan_client()
    with pytest.raises(GameDataError) as caught:
        provider.get_map(999)
    assert caught.value.code == "MAP_NOT_FOUND"
    (client / "duplicate.dlm").write_bytes(make_dlm())
    provider.scan_client()
    with pytest.raises(GameDataError) as caught:
        provider.get_map(123)
    assert caught.value.code == "AMBIGUOUS_MAP"


def test_archive_filename_does_not_override_actual_map_id(client):
    (client / "wrong.d2p").write_bytes(make_d2p([("999.dlm", make_dlm(888))]))
    provider = LocalGameDataProvider(client)
    provider.scan_client()
    with pytest.raises(GameDataError) as caught:
        provider.get_map(999)
    assert caught.value.code == "MAP_ID_MISMATCH"


def test_loose_dlm_index_uses_header_not_filename(client):
    (client / "wrong_name.dlm").write_bytes(make_dlm(999))
    provider = LocalGameDataProvider(client)
    provider.scan_client()
    assert provider.get_map(999).map_id == 999


def test_linked_archive_property_is_not_followed(client):
    (client / "linked.d2p").write_bytes(make_d2p([], properties={"link": "../../outside.d2p"}))
    provider = LocalGameDataProvider(client)
    report = provider.scan_client()
    assert report.formats["D2P 2.1 index"] == 2
    assert provider.list_maps() == (123, 124)


def test_corrupt_archive_does_not_hide_good_archive(client):
    (client / "broken.d2p").write_bytes(b"oops")
    provider = LocalGameDataProvider(client)
    report = provider.scan_client()
    assert report.errors and provider.get_map(123).map_id == 123


def test_discovery_does_not_follow_directory_links(client, monkeypatch):
    from combatbot.gamedata import discovery
    outside = client / "shortcut"
    outside.mkdir()
    (outside / "hidden.dlm").write_bytes(make_dlm(999))
    original = discovery.is_link
    monkeypatch.setattr(discovery, "is_link", lambda path: path == outside or original(path))
    report = discover_client(client)
    assert all("hidden" not in file.relative_path for file in report.files)
    assert any("ignoré" in error for error in report.errors)
