"""Mesures recalculables depuis les sorties enregistrées, sans vision ni apprentissage."""
from collections import defaultdict


def tracking_metrics(frames: list[dict], *, greedy: bool = False) -> dict:
    groups = defaultdict(list)
    excluded = 0
    for frame in frames:
        if not (frame.get("tracking_identity_confirmed") is True
                and frame.get("tracking_identity_source") == "human_ui_review"
                and frame.get("tracking_confirmed_at")
                and frame.get("tracking_sequence_id") == frame.get("group_id")):
            excluded += 1
            continue
        groups[frame["group_id"]].append(frame)
    truth_count = matched = switches = fragments = false_reassociations = lost = recovered = stable = 0
    explicit_occlusions = 0
    for rows in groups.values():
        rows.sort(key=lambda f: (f["timestamp"], f["frame_index"]))
        last_ids, owners, missing, last_states = {}, {}, set(), {}
        for frame in rows:
            truth = {str(t["track_id"]): int(t["cell_id"]) for t in frame["truth_identities"] if t.get("track_id")}
            explicit_occlusions += len(frame.get("occluded_truth", []))
            truth_count += len(truth)
            if greedy:
                tracks = [{"track_id": k, "cell_id": v, "kind": "ENEMY", "state": "OBSERVED",
                           "observed_this_frame": True} for k, v in frame["greedy_tracks"].items()]
            else:
                tracks = [t for t in frame["tracked_entities"] if t["kind"] == "ENEMY"]
            cell_ids = {t["cell_id"]: t["track_id"] for t in tracks
                        if t["state"] == "OBSERVED" and t["observed_this_frame"]}
            current_states = {t["track_id"]: t["state"] for t in tracks}
            for track, state in current_states.items():
                lost += int(state == "LOST" and last_states.get(track) != "LOST")
                recovered += int(state == "OBSERVED" and last_states.get(track) in ("LOST", "OCCLUDED"))
            if greedy:
                lost += sum(state == "OBSERVED" and track not in current_states for track, state in last_states.items())
            last_states.update(current_states)
            if greedy:
                for track in last_states.keys() - current_states.keys():
                    last_states[track] = "LOST"
            for identity, cell in truth.items():
                prediction = cell_ids.get(cell)
                if prediction is None:
                    if identity in last_ids:
                        missing.add(identity)
                    continue
                matched += 1
                changed = identity in last_ids and last_ids[identity] != prediction
                switches += int(changed)
                stable += int(not changed)
                fragments += int(identity in missing)
                missing.discard(identity)
                false_reassociations += int(prediction in owners and owners[prediction] != identity)
                owners[prediction] = identity
                last_ids[identity] = prediction
    return {"status": "MEASURED" if matched else "NOT_EVALUABLE", "verified_sequences": len(groups),
            "excluded_frames": excluded, "identity_observations": truth_count,
            "exact_track_associations": matched, "stable_associations": stable,
            "id_switches": switches if matched else None, "fragmentations": fragments if matched else None,
            "false_reassociations": false_reassociations if matched else None,
            "lost_tracks": lost, "recovered_tracks": recovered,
            "tracking_coverage": matched / truth_count if truth_count else None,
            "switch_rate": switches / matched if matched else None,
            "false_reassociation_rate": false_reassociations / matched if matched else None,
            "explicit_occlusion_annotations": explicit_occlusions,
            "occlusion_status": "NOT_OBSERVED" if not explicit_occlusions else "PARTIAL",
            "definitions": {"exact_track_associations": "Piste observée sur la cellule humaine exacte ; IDs arbitraires",
                            "fragmentations": "Reprise après interruption d'appariement d'une identité visible",
                            "lost_tracks": "Transitions LOST ; disparition pour la référence gloutonne",
                            "recovered_tracks": "Transition OCCLUDED/LOST vers OBSERVED, pas une preuve d'occlusion réelle"}}


def tracking_scopes(frames: list[dict]) -> dict:
    return {scope: {"global": tracking_metrics(rows), "greedy": tracking_metrics(rows, greedy=True)}
            for scope, rows in (("train", [f for f in frames if f["split"] == "train"]),
                                ("validation", [f for f in frames if f["split"] == "validation"]),
                                ("test", [f for f in frames if f["split"] == "test"]),
                                ("all_verified", frames))}
