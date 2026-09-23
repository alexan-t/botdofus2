"""CLI de recette réelle de la grille GameData (LOT 3B-2R), lecture seule.

Chaque capture utilise ``capture_client(hwnd, activate=False)`` : aucune
activation, aucun clic, aucune touche envoyée à DOFUS. Le map ID est fourni
par l'utilisateur (``/mapid``). Données : data/validation/grid-real/<session>/.

Exemples :
  python scripts/grid_recipe.py init --transform-json T.json --transform-source lot3b2
  python scripts/grid_recipe.py capture --session S --map-id 123 --source verified --kind A --transform T1
  python scripts/grid_recipe.py verdict --session S --map-id 123 --alignment correct --edges 11111
  python scripts/grid_recipe.py report --session S
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

import cv2  # noqa: E402

from combatbot import __version__  # noqa: E402
from combatbot.vision.coordinates import ClientSize, LayoutSignature  # noqa: E402
from combatbot.vision.gamedata_grid import GameDataTopologySource  # noqa: E402
from combatbot.vision.grid_fit import candidate_union, fit_grid_from_candidates  # noqa: E402
from combatbot.vision.grid_projection import GridScreenTransform  # noqa: E402
from combatbot.vision.grid_recipe import EDGE_KEYS, RealGridValidationSession, transform_drift  # noqa: E402
from combatbot.vision.models import Calibration, CapturedFrame, ClientRect, RelativeRect, ZoneEvidence  # noqa: E402

DATA = root / "data" / "validation" / "grid-real"
CACHE = root / "data" / "gamedata" / "cache"
SOURCES = {"verified": "user_verified_mapid", "guess": "manual_guess"}


def load_calibration(profile_id: int) -> Calibration:
    row = sqlite3.connect(f"file:{root / 'data' / 'pythonbot.sqlite3'}?mode=ro", uri=True).execute(
        "select client_width, client_height, zones_json, metadata_json, layout_signature "
        "from calibrations where profile_id=?", (profile_id,)).fetchone()
    if row is None:
        raise SystemExit("Aucune calibration de zones pour ce profil")
    zones = {name: RelativeRect.from_dict(value) for name, value in json.loads(row[2]).items()}
    meta = {name: ZoneEvidence.from_dict(value) for name, value in json.loads(row[3] or "{}").items()}
    return Calibration(profile_id, row[0], row[1], zones, meta, row[4] or "")


def dofus_window():
    from combatbot.vision.window import list_dofus_windows
    windows = [w for w in list_dofus_windows() if "dofus 2" in w.title.lower()]
    if len(windows) != 1:
        raise SystemExit(f"Une seule fenêtre DOFUS attendue, trouvé : {[w.title for w in windows]}")
    return windows[0]


def grab(hwnd: int) -> CapturedFrame:
    from combatbot.vision.capture import capture_client
    return capture_client(hwnd, activate=False)  # read-only: no focus change, no input


def topology(client: str, map_id: int):
    return GameDataTopologySource.for_client(client, CACHE).topology(map_id)


def cmd_init(args) -> None:
    from combatbot.vision.window import enable_dpi_awareness, window_dpi
    enable_dpi_awareness()
    window = dofus_window()
    frame = grab(window.hwnd)
    calibration = load_calibration(args.profile)
    if not calibration.compatible(frame.client.width, frame.client.height):
        raise SystemExit("Calibration de zones incompatible avec la taille actuelle du client")
    signature = LayoutSignature.create(frame.client.size, {n: r.to_normalized_rect() for n, r in calibration.zones.items()})
    environment = {"pythonbot_version": __version__, "window_title": window.title, "hwnd": window.hwnd,
                   "client_size": frame.client.size.to_dict(), "dpi": window_dpi(window.hwnd),
                   "layout_signature": signature.to_dict(), "layout_digest": signature.digest,
                   "combat_zone": calibration.zones["combat"].to_dict(), "profile_id": args.profile}
    session = RealGridValidationSession.create(DATA, environment, args.session)
    raw = json.loads(Path(args.transform_json).read_text(encoding="utf-8"))
    transform = GridScreenTransform.from_dict(raw.get("transform", raw))
    tid = session.add_transform(transform, args.transform_source, method=args.transform_method, notes=args.notes)
    print(json.dumps({"session": session.session_id, "initial_transform": tid, "environment": environment}, indent=1))


def cmd_capture(args) -> None:
    from combatbot.vision.window import enable_dpi_awareness
    enable_dpi_awareness()
    session = RealGridValidationSession.load(DATA, args.session)
    window = dofus_window()
    frame = grab(window.hwnd)
    calibration = load_calibration(session.environment.get("profile_id", 1))
    if frame.client.size.to_dict() != session.environment["client_size"]:
        print(f"ATTENTION : taille client {frame.client.size.to_dict()} ≠ session {session.environment['client_size']}")
    combat = calibration.crop(frame, "combat")
    source = SOURCES[args.source]
    session.declare_map(args.map_id, source, args.label)
    record = session.add_capture(map_id=args.map_id, map_id_source=source, kind=args.kind,
                                 transform_id=args.transform, frame=frame.image, combat_image=combat,
                                 topology=topology(args.client, args.map_id), stale=args.stale,
                                 notes=args.notes, red_blue=args.red_blue,
                                 context={"window_title": window.title, "client": frame.client.to_dict(),
                                          "capture_source": frame.source, "mode": args.mode})
    metrics = record["metrics"]
    keys = ("candidate_count", "inlier_count", "fit_status", "orientation", "best_score", "second_score",
            "score_margin", "integer_offset", "residual_mean_px", "residual_median_px", "residual_max_px",
            "projection_confidence", "topology_consistency", "best_shifted_u_score", "best_shifted_v_score",
            "margin_vs_shift", "automatic_flags")
    print(json.dumps({"capture_id": record["capture_id"], "files": record["files"],
                      **{k: metrics[k] for k in keys}, "applied_score": metrics["applied_score"],
                      "lattice_drift": metrics["lattice_drift"], "red_blue_auto": record.get("red_blue_auto")},
                     indent=1, ensure_ascii=False, default=str))


def cmd_rerender(args) -> None:
    from combatbot.vision.grid_recipe import rerender
    session = RealGridValidationSession.load(DATA, args.session)
    capture = next(c for c in session.captures if c["capture_id"] == args.capture)
    rerender(session, args.capture, topology(args.client, capture["map_id"]))
    print("ok")


def cmd_redblue_check(args) -> None:
    """Image for the human: GameData hint outlines (with IDs) and automatic extra detections."""
    import numpy as np
    from combatbot.vision.grid_projection import GridProjector
    session = RealGridValidationSession.load(DATA, args.session)
    capture = next(c for c in session.captures if c["capture_id"] == args.capture)
    image = cv2.imread(str(session.directory / capture["files"]["combat"]))
    grid = GridProjector(session.transform(capture["transform_id"])).project(topology(args.client, capture["map_id"]))
    auto = capture["red_blue_auto"]
    for colour, bgr in (("red", (0, 0, 255)), ("blue", (255, 120, 0))):
        for cell_id, width, label in ([(c, 3, str(c)) for c in auto[colour]["expected"]] +
                                      [(c, 2, f"{c}?") for c in auto[colour]["false_positive"]]):
            cell = grid.cell(cell_id)
            points = np.array([p.rounded() for p in cell.polygon], np.int32)
            cv2.polylines(image, [points], True, bgr if width == 3 else (255, 255, 255), width, cv2.LINE_AA)
            x, y = cell.center.rounded()
            cv2.putText(image, label, (x - 14, y + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    path = session.directory / "captures" / args.capture / "redblue_check.png"
    cv2.imwrite(str(path), image)
    print(path)


def cmd_fit_transform(args) -> None:
    """Recalibration from a stored capture: the fit's k-th NORMAL alternative becomes a new transform."""
    session = RealGridValidationSession.load(DATA, args.session)
    capture = next(c for c in session.captures if c["capture_id"] == args.capture)
    image = cv2.imread(str(session.directory / capture["files"]["combat"]))
    fit = fit_grid_from_candidates(candidate_union(image), (image.shape[1], image.shape[0]),
                                   topology=topology(args.client, capture["map_id"]))
    chosen = fit.alternatives[args.alternative]
    tid = session.add_transform(chosen, "recalibrated", map_id=capture["map_id"],
                                method=f"auto alternative {args.alternative} ({fit.status.value})", notes=args.notes)
    print(json.dumps({"transform_id": tid, "fit_status": fit.status.value, "score": fit.score, "margin": fit.margin,
                      "drift_vs_applied": transform_drift(chosen, session.transform(capture["transform_id"]))},
                     indent=1))


def cmd_verdict(args) -> None:
    session = RealGridValidationSession.load(DATA, args.session)
    edges = {key: flag == "1" for key, flag in zip(EDGE_KEYS, args.edges)}
    session.set_verdict(args.map_id, alignment=args.alignment, edges=edges, shift_direction=args.shift,
                        notes=args.notes, major_anomaly=args.anomaly, transform_id=args.transform)
    print(json.dumps(session.map_status(session.map_record(args.map_id)), ensure_ascii=False))


def cmd_reuse(args) -> None:
    session = RealGridValidationSession.load(DATA, args.session)
    session.record_reuse(args.map_id, args.applied, reused_exactly=args.exact == "yes",
                         recalibrated_transform_id=args.recalibrated, notes=args.notes)
    print(json.dumps(session.map_record(args.map_id)["reuse"], indent=1))


def cmd_stale(args) -> None:
    session = RealGridValidationSession.load(DATA, args.session)
    session.add_stale_test(old_map_id=args.old, new_map_id=args.new, before_capture=args.before,
                           after_capture=args.after, notes=args.notes)
    print("stale test enregistré")


def cmd_redblue(args) -> None:
    session = RealGridValidationSession.load(DATA, args.session)
    parse = lambda value: [int(v) for v in value.split(",") if v.strip()] if value is not None else None  # noqa: E731
    session.add_red_blue_annotation(map_id=args.map_id, capture_id=args.capture, red_match=args.red_match,
                                    blue_match=args.blue_match, real_red=parse(args.real_red),
                                    real_blue=parse(args.real_blue), notes=args.notes)
    print(json.dumps(session.red_blue[-1], indent=1, ensure_ascii=False))


def cmd_promote(args) -> None:
    """Import recipe captures into the real corpus through the actual observer pipeline."""
    from combatbot.corpus.models import Annotation
    from combatbot.corpus.repository import CorpusRepository
    from combatbot.vision.combat_observer import OverlayOptions, RealCombatObserver, save_debug_observation
    from combatbot.vision.gamedata_grid import GameDataGridResolver
    from combatbot.vision.grid_profile import CombatGridProfileV2, ManualMapIdentity, MapIdSource
    session = RealGridValidationSession.load(DATA, args.session)
    calibration = load_calibration(session.environment.get("profile_id", 1))
    signature = LayoutSignature.from_dict(session.environment["layout_signature"]).to_json()
    source = GameDataTopologySource.for_client(args.client, CACHE)
    repository = CorpusRepository(root / "data" / "corpus")
    repository.ensure_layout()
    annotated = {r["capture_id"] for r in session.red_blue}
    promoted = []
    for index, capture in enumerate(session.captures):
        record = session.map_record(capture["map_id"])
        verdict = record.get("verdict") or {}
        status, reasons = session.map_status(record)
        mode = capture.get("context", {}).get("mode")
        tags = ["lot3b2r", f"mode_{mode}", f"map_status_{status.lower()}"]
        if capture["map_id_source"] == "user_verified_mapid":
            tags.append("mapid_verified")
        if capture["stale_map"]:
            tags.append("projection_stale_map")
        elif verdict.get("alignment") == "correct":
            tags += ["projection_good", "grid_real_verified"]
        elif verdict.get("alignment") == "ambigu":
            tags.append("projection_ambiguous")
        if capture["kind"] == "D" and mode == "placement":
            tags.append("placement_phase")
        if capture["capture_id"] in annotated:
            tags.append("red_blue_validation")
        profile = CombatGridProfileV2(session.transform(capture["transform_id"]), signature, "recipe",
                                      map_id=capture["map_id"], confirmed_by_user=True)
        identity = ManualMapIdentity()
        identity.declare(capture["map_id"], MapIdSource(capture["map_id_source"]))
        resolver = GameDataGridResolver(profile=profile, topology_source=source, map_identity=identity)
        image = cv2.imread(str(session.directory / capture["files"]["frame"]))
        client = capture.get("context", {}).get("client") or {"left": 0, "top": 0,
                                                               "width": image.shape[1], "height": image.shape[0]}
        frame = CapturedFrame(0, ClientRect.from_dict(client), image)
        observer = RealCombatObserver(0, calibration, frame_provider=lambda f=frame: f,
                                      number_reader=lambda crop: (None, 0.0), grid_resolver=resolver,
                                      overlay_options=OverlayOptions(walkability=True, cell_ids=True),
                                      capture_context={"session_id": session.session_id, "profile": "recette"})
        observer._frame_index = index
        packet = observer.observe()
        target = root / "data" / "debug" / "grid-recipe" / capture["capture_id"]
        target.mkdir(parents=True, exist_ok=True)
        save_debug_observation(packet, target)
        entry = repository.import_debug(target, session_id=session.session_id, frame_index=index, tags=tuple(tags))
        comments = (f"LOT 3B-2R {capture['capture_id']} ({capture['kind_label']}, mode {mode}) ; map {capture['map_id']} "
                    f"({capture['map_id_source']}) ; stale={capture['stale_map']} ; verdict {verdict.get('alignment')} "
                    f"bords {[k for k in ('edge_top_ok', 'edge_bottom_ok', 'edge_left_ok', 'edge_right_ok', 'center_ok') if verdict.get(k)]} ; "
                    f"statut carte {status} {reasons}")
        repository.save_annotation(Annotation(entry.observation_id, combat_truth=mode in ("placement", "combat"),
                                              comments=comments))
        promoted.append({"capture_id": capture["capture_id"], "observation_id": entry.observation_id, "tags": tags})
    (session.directory / "corpus-promotion.json").write_text(json.dumps(promoted, indent=1, ensure_ascii=False),
                                                           encoding="utf-8")
    print(json.dumps({"promoted": len(promoted)}, indent=1))


def cmd_report(args) -> None:
    session = RealGridValidationSession.load(DATA, args.session)
    baseline = session.baseline()
    path = session.directory / "baseline.json"
    path.write_text(json.dumps(baseline, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(baseline, indent=1, ensure_ascii=False, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--client", default=r"C:\Users\Thoma\AppData\Local\Alea\Client")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init"); p.add_argument("--session"); p.add_argument("--profile", type=int, default=1)
    p.add_argument("--transform-json", required=True); p.add_argument("--transform-source", required=True)
    p.add_argument("--transform-method", default=""); p.add_argument("--notes", default=""); p.set_defaults(func=cmd_init)
    p = sub.add_parser("capture"); p.add_argument("--session", required=True); p.add_argument("--map-id", type=int, required=True)
    p.add_argument("--source", choices=sorted(SOURCES), required=True); p.add_argument("--kind", required=True)
    p.add_argument("--transform", required=True); p.add_argument("--label", default="")
    p.add_argument("--stale", action="store_true"); p.add_argument("--red-blue", action="store_true")
    p.add_argument("--mode", choices=("exploration", "placement", "combat"), required=True)
    p.add_argument("--notes", default=""); p.set_defaults(func=cmd_capture)
    p = sub.add_parser("rerender"); p.add_argument("--session", required=True); p.add_argument("--capture", required=True)
    p.set_defaults(func=cmd_rerender)
    p = sub.add_parser("fit-transform"); p.add_argument("--session", required=True); p.add_argument("--capture", required=True)
    p.add_argument("--alternative", type=int, default=0); p.add_argument("--notes", default=""); p.set_defaults(func=cmd_fit_transform)
    p = sub.add_parser("verdict"); p.add_argument("--session", required=True); p.add_argument("--map-id", type=int, required=True)
    p.add_argument("--alignment", required=True); p.add_argument("--edges", required=True, help="5 chiffres : haut bas gauche droite centre")
    p.add_argument("--shift", default=""); p.add_argument("--notes", default=""); p.add_argument("--anomaly", action="store_true")
    p.add_argument("--transform"); p.set_defaults(func=cmd_verdict)
    p = sub.add_parser("reuse"); p.add_argument("--session", required=True); p.add_argument("--map-id", type=int, required=True)
    p.add_argument("--applied", required=True); p.add_argument("--exact", choices=("yes", "no"), required=True)
    p.add_argument("--recalibrated"); p.add_argument("--notes", default=""); p.set_defaults(func=cmd_reuse)
    p = sub.add_parser("stale"); p.add_argument("--session", required=True); p.add_argument("--old", type=int, required=True)
    p.add_argument("--new", type=int, required=True); p.add_argument("--before", required=True); p.add_argument("--after")
    p.add_argument("--notes", default=""); p.set_defaults(func=cmd_stale)
    p = sub.add_parser("redblue"); p.add_argument("--session", required=True); p.add_argument("--map-id", type=int, required=True)
    p.add_argument("--capture", required=True); p.add_argument("--red-match", required=True); p.add_argument("--blue-match", required=True)
    p.add_argument("--real-red"); p.add_argument("--real-blue"); p.add_argument("--notes", default=""); p.set_defaults(func=cmd_redblue)
    p = sub.add_parser("redblue-check"); p.add_argument("--session", required=True); p.add_argument("--capture", required=True)
    p.set_defaults(func=cmd_redblue_check)
    p = sub.add_parser("promote"); p.add_argument("--session", required=True); p.set_defaults(func=cmd_promote)
    p = sub.add_parser("report"); p.add_argument("--session", required=True); p.set_defaults(func=cmd_report)
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
