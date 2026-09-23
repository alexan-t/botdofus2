"""Validation offline sur un client réel : agrégats, preuves et exports.

Lecture seule du client. Les niveaux de preuve restent distincts :
ARCHIVE RECONNUE → ENTRY INDEXÉE → DLM EXTRAIT → DLM PARSÉ → CELLULES PARSÉES
→ TOPOLOGIE DÉMONTRÉE → FIGHT CELLS DÉMONTRÉES.
Les fight cells ne sont jamais démontrées ici : il faut une observation
manuelle de la phase de placement, hors de ce module.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import random
import time

import numpy as np

from .errors import GameDataError
from .formats.d2o import D2OFile
from .formats.maps import CELL_COUNT
from .models import GridCoordinate
from .topology import MAP_WIDTH, ROWS, adjacency, cell_to_grid, grid_to_cell, row_col

FLAG_BITS = 16
EDGE_ROWS = 2  # two half-rows at top and bottom
ARROW_BITS = (9, 10, 11, 12)  # historical arrow names are not assumed
ARROW_MASK = sum(1 << bit for bit in ARROW_BITS)


def _edges(pairs: dict[int, tuple[int, ...]]) -> list[tuple[int, int]]:
    return sorted({(a, b) if a < b else (b, a) for a, neighbours in pairs.items() for b in neighbours})


def _mirror(cell: int) -> int:
    row, col = divmod(cell, MAP_WIDTH)
    return row * MAP_WIDTH + (MAP_WIDTH - 1 - col)


def candidate_adjacencies() -> dict[str, list[tuple[int, int]]]:
    """Formula edges and alternatives used as controls, never as topology."""
    formula = _edges(adjacency())
    rng = random.Random(3)
    return {
        "formula_4": formula,
        # Same half-rows, opposite stagger direction.
        "mirrored_stagger_4": sorted({tuple(sorted((_mirror(a), _mirror(b)))) for a, b in formula}),
        # Naive 14 x 40 rectangle, ignoring the stagger.
        "rectangle_14x40": [(c, c + d) for c in range(CELL_COUNT) for d in (1, MAP_WIDTH)
                            if c + d < CELL_COUNT and (d != 1 or c % MAP_WIDTH != MAP_WIDTH - 1)],
        "random_pairs": [tuple(sorted(rng.sample(range(CELL_COUNT), 2))) for _ in range(len(formula))],
    }


def geometry_selfcheck() -> dict:
    coordinates = [cell_to_grid(c) for c in range(CELL_COUNT)]
    unique = len(set(coordinates)) == CELL_COUNT
    round_trip = all(grid_to_cell(coordinates[c]) == c for c in range(CELL_COUNT))
    pairs = adjacency()
    symmetric = all(a in pairs[b] for a, ns in pairs.items() for b in ns)
    xs, ys = [p.x for p in coordinates], [p.y for p in coordinates]
    degrees = Counter(len(ns) for ns in pairs.values())
    # Outside points must not map back to a cell.
    inside = {(p.x, p.y) for p in coordinates}
    outside = sum(grid_to_cell(GridCoordinate(x, y)) is not None
                  for x in range(min(xs) - 2, max(xs) + 3) for y in range(min(ys) - 2, max(ys) + 3)
                  if (x, y) not in inside)
    return {"bijection": unique, "round_trip": round_trip, "symmetric_neighbours": symmetric,
            "x_range": [min(xs), max(xs)], "y_range": [min(ys), max(ys)],
            "degree_distribution": dict(sorted(degrees.items())), "outside_points_mapped": outside}


class MapValidator:
    """Streams over all indexed maps; keeps compact aggregates only."""

    def __init__(self, provider):
        self.provider = provider
        self.progress = 0
        self.total = 0
        self.cancelled = False

    def run(self, map_ids=None) -> dict:
        provider = self.provider
        ids = tuple(provider.list_maps() if map_ids is None else map_ids)
        self.total, self.progress = len(ids), 0
        started = time.perf_counter()
        controls = {name: (np.array([a for a, _ in edges]), np.array([b for _, b in edges]))
                    for name, edges in candidate_adjacencies().items()}
        disagreement = {name: Counter() for name in controls}
        versions, envelopes, cell_counts, absent = Counter(), Counter(), Counter(), Counter()
        bits_maps, flag_values, floor_values, positioned = Counter(), Counter(), Counter(), Counter()
        speed, move_zone, linked_zone, tactical = Counter(), Counter(), Counter(), Counter()
        red_blue = Counter()
        red_counts, blue_counts = Counter(), Counter()
        failures, per_map = [], {}
        stages, stagger_votes = Counter(), Counter()
        for map_id in ids:
            if self.cancelled:
                break
            self.progress += 1
            stages["entry_indexed"] += 1
            try:
                game_map = provider.get_map(map_id, track=False)
            except (GameDataError, OSError) as exc:
                diagnostic = getattr(exc, "diagnostic", None)
                code = getattr(exc, "code", "READ_FAILED")
                # Extracted = decompressed with a valid envelope, even if the body then fails.
                if code in ("UNKNOWN_VERSION", "ENCRYPTED") or (diagnostic and diagnostic["stage"] != "envelope"):
                    stages["dlm_extracted"] += 1
                failures.append({"map_id": map_id, "source": provider.map_source(map_id), "code": code,
                                 "error": str(exc)[:600], "diagnostic": diagnostic})
                continue
            stages["dlm_extracted"] += 1
            stages["dlm_parsed"] += 1
            envelope = game_map.metadata["envelope"]
            versions[envelope["version"]] += 1
            envelopes[(envelope["compression"], envelope["encrypted"], envelope["encryption_version"])] += 1
            cells = game_map.cells
            cell_counts[len(cells)] += 1
            absent[sum(c.raw_flags is None for c in cells)] += 1
            if len(cells) != CELL_COUNT or any(int(c.cell_id) != i for i, c in enumerate(cells)):
                continue
            stages["cells_parsed"] += 1
            tactical[game_map.metadata["tactical_mode_template_id"]] += 1
            walk = [c.walkable for c in cells]
            los = [c.line_of_sight for c in cells]
            present_bits = 0
            red = blue = 0
            for c in cells:
                flags = c.raw_flags
                if flags is None:
                    continue
                present_bits |= flags
                flag_values[flags] += 1
                speed[c.speed] += 1
                move_zone[c.move_zone] += 1
                linked_zone["absent" if c.linked_zone is None else c.linked_zone] += 1
                floor_values[c.floor] += 1
                if c.map_change_data or flags & ARROW_MASK:
                    positioned[(c.map_change_data, flags & ARROW_MASK, int(c.cell_id))] += 1
                if c.red_hint or c.blue_hint:
                    red += bool(c.red_hint)
                    blue += bool(c.blue_hint)
                    red_blue["cells_red_and_blue"] += bool(c.red_hint and c.blue_hint)
                    red_blue["placement_hint_cells"] += 1
                    red_blue["hint_walkable"] += bool(c.walkable)
                    red_blue["hint_non_walkable_during_fight"] += bool(c.non_walkable_during_fight)
                    red_blue["hint_los"] += bool(c.line_of_sight)
            for bit in range(FLAG_BITS):
                if present_bits >> bit & 1:
                    bits_maps[bit] += 1
            walk_array = np.fromiter((w is True for w in walk), dtype=np.bool_, count=CELL_COUNT)
            los_array = np.fromiter((v is True for v in los), dtype=np.bool_, count=CELL_COUNT)
            map_diff = {}
            for name, (left, right) in controls.items():
                counter = disagreement[name]
                counter["edges"] += len(left)
                map_diff[name] = int(np.count_nonzero(walk_array[left] != walk_array[right]))
                counter["walk_diff"] += map_diff[name]
                counter["los_diff"] += int(np.count_nonzero(los_array[left] != los_array[right]))
            formula, mirrored = map_diff["formula_4"], map_diff["mirrored_stagger_4"]
            stagger_votes["formula_better" if formula < mirrored else
                          "mirrored_better" if mirrored < formula else "tie"] += 1
            red_counts[red] += 1
            blue_counts[blue] += 1
            red_blue["maps_with_red"] += red > 0
            red_blue["maps_with_blue"] += blue > 0
            red_blue["maps_with_both"] += red > 0 and blue > 0
            red_blue["maps_red_equals_blue"] += red > 0 and red == blue
            meta = game_map.metadata
            per_map[map_id] = {
                "sub_area_id": meta["sub_area_id"], "tactical": meta["tactical_mode_template_id"],
                "neighbours": (meta["top_neighbour_id"], meta["bottom_neighbour_id"],
                               meta["left_neighbour_id"], meta["right_neighbour_id"]),
                "walkable": sum(bool(w) for w in walk), "red": red, "blue": blue,
                "walk_mask": sum(1 << i for i, w in enumerate(walk) if w),
                "red_cells": [int(c.cell_id) for c in cells if c.red_hint],
                "blue_cells": [int(c.cell_id) for c in cells if c.blue_hint],
            }
        elapsed = time.perf_counter() - started
        bits_cells = Counter()
        for flags, count in flag_values.items():
            for bit in range(FLAG_BITS):
                if flags >> bit & 1:
                    bits_cells[bit] += count
        change_rows = {bit: Counter() for bit in range(8)}
        change_cols = {bit: Counter() for bit in range(8)}
        arrow_rows = {bit: Counter() for bit in ARROW_BITS}
        arrow_cols = {bit: Counter() for bit in ARROW_BITS}
        for (change, arrows, cell), count in positioned.items():
            row, col = divmod(cell, MAP_WIDTH)
            for bit in range(8):
                if change >> bit & 1:
                    change_rows[bit][row] += count
                    change_cols[bit][col] += count
            for bit in ARROW_BITS:
                if arrows >> bit & 1:
                    arrow_rows[bit][row] += count
                    arrow_cols[bit][col] += count
        stages["archive_recognised"] = len({(provider.map_source(m) or "::").split("::")[0] for m in ids})
        components = self._hint_components(per_map)
        rates = {name: {"edges": c["edges"],
                        "walkability_disagreement": c["walk_diff"] / c["edges"] if c["edges"] else None,
                        "los_disagreement": c["los_diff"] / c["edges"] if c["edges"] else None}
                 for name, c in disagreement.items()}
        self.per_map = per_map
        return {
            "maps_total": len(ids), "seconds": elapsed, "stages": dict(stages),
            "versions": {str(k): v for k, v in sorted(versions.items())},
            "envelopes": [{"compression": k[0], "encrypted": k[1], "encryption_version": k[2], "maps": v}
                          for k, v in envelopes.items()],
            "cell_counts": {str(k): v for k, v in cell_counts.items()},
            "absent_cells_per_map": {str(k): v for k, v in absent.items()},
            "failures": failures,
            "flag_bits": {str(bit): {"cells": bits_cells[bit], "maps": bits_maps[bit]} for bit in range(FLAG_BITS)},
            "speed": {str(k): v for k, v in sorted(speed.items())},
            "move_zone": {str(k): v for k, v in sorted(move_zone.items())},
            "linked_zone": {str(k): v for k, v in sorted(linked_zone.items(), key=lambda kv: str(kv[0]))},
            "floor_range": [min(floor_values, default=None), max(floor_values, default=None)],
            "floor_distinct_values": len(floor_values),
            "tactical_mode_template_ids": {str(k): v for k, v in sorted(tactical.items())},
            "topology_controls": rates,
            "stagger_votes_per_map": dict(stagger_votes),
            "map_change_bits": self._edge_profile(change_rows, change_cols),
            "arrow_bits": self._edge_profile(arrow_rows, arrow_cols),
            "red_blue": {**dict(red_blue), "red_count_distribution": {str(k): v for k, v in sorted(red_counts.items())},
                         "blue_count_distribution": {str(k): v for k, v in sorted(blue_counts.items())},
                         **components},
        }

    @staticmethod
    def _edge_profile(rows: dict, cols: dict) -> dict:
        profile = {}
        for bit in rows:
            total = sum(rows[bit].values())
            if not total:
                profile[str(bit)] = {"cells": 0}
                continue
            profile[str(bit)] = {
                "cells": total,
                "top_rows_0_1": sum(rows[bit][r] for r in range(EDGE_ROWS)) / total,
                "bottom_rows_38_39": sum(rows[bit][r] for r in range(ROWS - EDGE_ROWS, ROWS)) / total,
                "left_col_0": cols[bit][0] / total,
                "right_col_13": cols[bit][MAP_WIDTH - 1] / total,
                "row_histogram": {str(k): v for k, v in sorted(rows[bit].items())},
                "col_histogram": {str(k): v for k, v in sorted(cols[bit].items())},
            }
        return profile

    @staticmethod
    def _hint_components(per_map: dict) -> dict:
        pairs = adjacency(corners=True)
        sizes = Counter()
        for record in per_map.values():
            for key in ("red_cells", "blue_cells"):
                remaining = set(record[key])
                while remaining:
                    stack, size = [remaining.pop()], 0
                    while stack:
                        cell = stack.pop()
                        size += 1
                        for n in pairs[cell]:
                            if n in remaining:
                                remaining.remove(n)
                                stack.append(n)
                    sizes[size] += 1
        return {"hint_component_sizes_8_connexity": {str(k): v for k, v in sorted(sizes.items())}}


def cross_check_d2o(client_root: Path, per_map: dict) -> dict:
    """Independent tables vs parsed DLM headers. World coords are not cell coords."""
    common = client_root / "data" / "common"
    result: dict = {}
    try:
        positions = D2OFile(common / "MapPositions.d2o")
        agree = Counter()
        world: dict[int, tuple[int, int]] = {}
        for key, obj in positions.objects():
            map_id = int(obj["id"])
            world[map_id] = (obj["posX"], obj["posY"])
            record = per_map.get(map_id)
            if record is None:
                agree["position_without_parsed_map"] += 1
                continue
            agree["compared"] += 1
            agree["sub_area_equal"] += obj["subAreaId"] == record["sub_area_id"]
            agree["tactical_equal"] += obj["tacticalModeTemplateId"] == record["tactical"]
        agree["parsed_map_without_position"] = sum(m not in world for m in per_map)
        neighbours = Counter()
        for map_id, record in per_map.items():
            if map_id not in world:
                continue
            x, y = world[map_id]
            for target, (dx, dy) in zip(record["neighbours"], ((0, -1), (0, 1), (-1, 0), (1, 0))):
                if target not in world:
                    neighbours["target_unknown_in_client"] += 1
                    continue
                neighbours["compared"] += 1
                neighbours["adjacent_in_expected_direction"] += world[target] == (x + dx, y + dy)
        result["MapPositions"] = {"objects": len(positions.index), **dict(agree),
                                  "dlm_neighbours_vs_world_positions": dict(neighbours),
                                  "fields": positions.schema()[0]["fields"]}
    except (GameDataError, OSError, KeyError, ValueError) as exc:
        result["MapPositions"] = {"error": str(exc)}
    try:
        scroll = D2OFile(common / "MapScrollActions.d2o")
        agree = Counter()
        for key, obj in scroll.objects():
            record = per_map.get(int(obj["id"]))
            if record is None:
                continue
            top, bottom, left, right = record["neighbours"]
            for side, value in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
                if obj[f"{side}Exists"]:
                    target = int(obj[f"{side}MapId"])
                    agree["compared"] += 1
                    agree["equal_to_dlm_neighbour"] += target == value
                    agree["dlm_neighbour_exists_in_client"] += value in per_map
                    agree["scroll_target_exists_in_client"] += target in per_map
        # Scroll actions are overrides: most replace DLM neighbour IDs absent from this client.
        result["MapScrollActions"] = {"objects": len(scroll.index), **dict(agree)}
    except (GameDataError, OSError, KeyError, ValueError) as exc:
        result["MapScrollActions"] = {"error": str(exc)}
    try:
        references = D2OFile(common / "MapReferences.d2o")
        counts = Counter()
        for key, obj in references.objects():
            record = per_map.get(int(obj["mapId"]))
            cell = obj["cellId"]
            if record is None or not 0 <= cell < CELL_COUNT:
                counts["not_compared"] += 1
                continue
            counts["compared"] += 1
            counts["walkable"] += bool(record["walk_mask"] >> cell & 1)
        base = [r["walkable"] / CELL_COUNT for r in per_map.values()]
        result["MapReferences"] = {"objects": len(references.index), **dict(counts),
                                   "walkable_rate": counts["walkable"] / counts["compared"] if counts["compared"] else None,
                                   "baseline_walkable_rate": sum(base) / len(base) if base else None}
    except (GameDataError, OSError, KeyError, ValueError) as exc:
        result["MapReferences"] = {"error": str(exc)}
    return result


def topology_decision(selfcheck: dict, maps: dict) -> dict:
    """Explicit criteria; any failure keeps the topology unverified."""
    controls = maps["topology_controls"]
    formula = controls["formula_4"]["walkability_disagreement"]
    mirrored = controls["mirrored_stagger_4"]["walkability_disagreement"]
    randomised = controls["random_pairs"]["walkability_disagreement"]
    edges = maps["map_change_bits"]
    edge_bits = [bit for bit, p in edges.items() if p.get("cells") and max(
        p["top_rows_0_1"], p["bottom_rows_38_39"], p["left_col_0"], p["right_col_13"]) >= 0.9]
    criteria = {
        "bijection_round_trip": selfcheck["bijection"] and selfcheck["round_trip"] and not selfcheck["outside_points_mapped"],
        "symmetric_neighbours": selfcheck["symmetric_neighbours"],
        "formula_beats_mirrored_stagger": formula is not None and mirrored is not None and formula < mirrored,
        "formula_far_below_random": formula is not None and randomised is not None and formula < randomised / 2,
        "map_change_bits_on_edges": len(edge_bits) >= 4,
    }
    return {"criteria": criteria, "edge_bits_concentrated": edge_bits,
            "logical_topology_verified": all(criteria.values()),
            "screen_projection_verified": False,
            "fight_cells_verified": False}
