"""CLI du banc de mesure LOT 3B-0 : ``python -m combatbot.benchmark``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from combatbot.corpus.benchmark import run_benchmark, write_reports
from combatbot.corpus.repository import CorpusRepository
from combatbot.runtime import app_data_root, PROJECT_ROOT


def _force_utf8_stdout() -> None:
    """Les rapports contiennent « ≥ » et « ⚠ » : la console Windows est en cp1252."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(description="Mesure la baseline du corpus réel PythonBot")
    parser.add_argument("--corpus-root", type=Path, help="Racine data/corpus à utiliser")
    parser.add_argument("--output-dir", type=Path, help="Dossier de rapport")
    parser.add_argument("--grid-validation", action="store_true",
                        help="LOT 3B-3 : visibilité de grille, combat, alignement, map périmée (corpus lot3b2r)")
    parser.add_argument("--hud-reader", action="store_true",
                        help="LOT 3B-4 : inventaire et mesure du lecteur spécialisé PA/PM")
    parser.add_argument("--hud-rapidocr", action="store_true",
                        help="Inclut le fallback RapidOCR dans le benchmark HUD")
    parser.add_argument("--entity-splits", nargs="+", choices=("train", "validation", "test"))
    parser.add_argument("--entity-layout", help="Digest de disposition à mesurer")
    parser.add_argument("--entities", action="store_true",
                        help="LOT 3B-5 : entités par cellule et suivi global (vérité humaine)")
    parser.add_argument("--entities-3b5d", action="store_true",
                        help="LOT 3B-5D : splits déclarés par combat, vérités EMPTY échantillonnées, verdicts")
    parser.add_argument("--freeze-sha", help="3B-5D : commit de gel exigé pour mesurer TEST")
    parser.add_argument("--diagnostic-rerun", action="store_true",
                        help="3B-5D : re-mesure TEST déjà vus, enregistrée comme diagnostic (pas un TEST)")
    parser.add_argument("--hud-split", action="store_true",
                        help="LOT 3B-4R2 : reconstruit explicitement le split HUD (TEST gelé) et affiche la distribution")
    parser.add_argument("--combat-state", action="store_true",
                        help="3B-6B : mesurer le détecteur phase/tour (TRAIN un combat laissé de côté, VALIDATION)")
    parser.add_argument("--install-combat-state-model", action="store_true",
                        help="3B-6B : installer le modèle phase/tour appris sur les vérités humaines TRAIN")
    parser.add_argument("--map-resolution", action="store_true",
                        help="3B-6C : rejouer le corpus pour mesurer la résolution automatique de map")
    parser.add_argument("--acceptance", action="store_true",
                        help="3B-7 : recette consolidée (rapports des bancs + sessions réelles), sans rien relancer")
    parser.add_argument("--observation-e2e", action="store_true",
                        help="FAST-3B7 : observation complète frame par frame contre les vérités humaines "
                             "(prédictions enregistrées seulement, sans DOFUS ; filtre : --entity-splits)")
    parser.add_argument("--dry-run-plans", action="store_true",
                        help="FAST-4D/5A0 : rejoue le corpus à travers état → plan → exécuteur dry-run (aucune action)")
    parser.add_argument("--profile-id", type=int, help="Profil dont les sorts confirmés sont utilisés (défaut : dernier)")
    parser.add_argument("--assume-logical-range", action="store_true",
                        help="Hypothèse explicite, non prouvée : portée = distance logique |dx|+|dy| (tracée dans le rapport)")
    parser.add_argument("--data-dir", type=Path, action="append",
                        help="3B-7 : dossier data/ à inspecter (répétable ; défaut : sources, runtime, LocalAppData)")
    parser.add_argument("--limit", type=int, help="Nombre maximal de frames (diagnostic)")
    parser.add_argument("--client", type=Path, help="Dossier client GameData (défaut : réglage de l'application)")
    parser.add_argument("--install-runtime-profiles", action="store_true",
                        help="Installation explicite TRAIN vers LocalAppData, avec backup et bascule atomique")
    parser.add_argument("--dry-run", action="store_true", help="Préparer l'installation sans aucune écriture runtime")
    parser.add_argument("--runtime-data", type=Path, help="Stockage runtime (défaut LocalAppData/PythonBot/data)")
    parser.add_argument("--entity-split-registry", type=Path, help="Registre gelé à conserver au premier déploiement")
    parser.add_argument("--restore-runtime-profiles", type=Path, help="Restaurer explicitement une sauvegarde de profils")
    args = parser.parse_args(argv)
    if args.restore_runtime_profiles:
        import json
        from combatbot.entity_runtime import restore_backup, runtime_data_directory
        print(json.dumps(restore_backup(args.runtime_data or runtime_data_directory(), args.restore_runtime_profiles), indent=2))
        return 0
    if args.install_runtime_profiles:
        if not args.entities:
            parser.error("--install-runtime-profiles exige --entities")
        return _install_entities(args)
    if args.acceptance:
        from combatbot.corpus.acceptance import markdown_report, run_acceptance, write_acceptance
        report = run_acceptance(args.data_dir)
        json_path, _ = write_acceptance(report, args.output_dir or (PROJECT_ROOT / "data" / "benchmarks"))
        print(markdown_report(report))
        print(f"JSON : {json_path}")
        return 0
    repository = CorpusRepository(args.corpus_root)
    if args.observation_e2e:
        from combatbot.corpus.observation_e2e_benchmark import markdown_report as e2e_markdown
        from combatbot.corpus.observation_e2e_benchmark import run_observation_e2e, write_report as write_e2e
        report = run_observation_e2e(repository, tuple(args.entity_splits) if args.entity_splits else None)
        json_path, markdown_path = write_e2e(report, args.output_dir or (app_data_root() / "data" / "benchmarks"))
        print(e2e_markdown(report))
        print(f"JSON : {json_path}")
        print(f"Markdown : {markdown_path}")
        return 0
    if args.dry_run_plans:
        return _dry_run_plans(repository, args)
    if args.combat_state or args.install_combat_state_model:
        return _combat_state(repository, args)
    if args.map_resolution:
        return _map_resolution(repository, args)
    if args.grid_validation:
        return _grid_validation(repository, args)
    if args.entities_3b5d:
        return _entities_3b5d(repository, args)
    if args.entities:
        from combatbot.corpus.entity_benchmark import run_entity_benchmark, write_entity_report
        report = run_entity_benchmark(repository, splits=tuple(args.entity_splits) if args.entity_splits else None,
                                      layout=args.entity_layout)
        output = args.output_dir or (PROJECT_ROOT / "data" / "benchmarks")
        json_path, markdown_path = write_entity_report(report, output)
        print(f"Entités : {report['frames']} frame(s) annotée(s), splits {report['splits']}, "
              f"groupes {report['groups']} — statut {report['status']}")
        for scope in ("test", "validation", "all"):
            if scope in report["after"]:
                print(f"  {scope.upper()} AVANT {report['before'][scope]['enemies']}")
                print(f"  {scope.upper()} APRÈS {report['after'][scope]['enemies']}")
                print(f"  {scope.upper()} joueur APRÈS {report['after'][scope]['player']}")
        print(f"JSON : {json_path}")
        print(f"Markdown : {markdown_path}")
        return 0
    if args.hud_split:
        from combatbot.corpus.hud_dataset import build_split_registry, distribution_text, inventory
        registry = build_split_registry(repository)
        print(f"Registre : {len(registry['groups'])} groupe(s), TEST gelé : {registry['frozen_test']}")
        if registry["missing_frozen_test"]:
            print(f"ATTENTION groupes TEST gelés introuvables : {registry['missing_frozen_test']}")
        print(distribution_text(inventory(repository)))
        return 0
    if args.hud_reader:
        from combatbot.corpus.hud_dataset import export_one_seven, run_hud_benchmark, write_report
        report = run_hud_benchmark(repository, rapidocr=args.hud_rapidocr)
        print(report["distribution_text"])
        output = args.output_dir or (app_data_root() / "data" / "benchmarks")
        json_path, markdown_path = write_report(report, output)
        one_seven_json, one_seven_plate = export_one_seven(repository, output)
        print(f"HUD : {report['labelled_examples']}/{report['inventory_examples']} crop(s) avec vérité humaine")
        print(f"Vérité terrain : {report['ground_truth_status']} — {report['review']}")
        print(f"Statut : {report['status']}")
        print(f"1/7 : {report['one_seven']['verdict']} — {one_seven_json}"
              + (f" + {one_seven_plate}" if one_seven_plate else ""))
        print(f"JSON : {json_path}")
        print(f"Markdown : {markdown_path}")
        return 0
    report = run_benchmark(repository)
    output = args.output_dir or (app_data_root() / "data" / "benchmarks")
    json_path, markdown_path = write_reports(report, output)
    print(f"Corpus : {report['corpus']['manifest_entries']} observation(s), "
          f"{report['corpus']['annotated_observations']} annotée(s)")
    print(f"JSON : {json_path}")
    print(f"Markdown : {markdown_path}")
    return 0


def _entities_3b5d(repository: CorpusRepository, args) -> int:
    import json
    from combatbot.corpus.entity_benchmark import run_entity_benchmark, write_entity_report
    from combatbot.corpus.validation_3b5d import check_freeze, guard_test_run, markdown_3b5d, verdicts
    splits = tuple(args.entity_splits or ("train", "validation"))
    run = None
    if "test" in splits:
        if not args.freeze_sha:
            raise SystemExit("TEST exige --freeze-sha : geler et committer le code avant la mesure.")
        freeze = check_freeze(args.freeze_sha, PROJECT_ROOT)
    report = run_entity_benchmark(repository, splits=splits, layout=args.entity_layout, declared_only=True)
    if "test" in splits:
        groups = sorted({f["group_id"] for f in report["frames_detail"] if f["split"] == "test"})
        if not groups:
            raise SystemExit("Aucun combat TEST déclaré et annoté.")
        run = guard_test_run(repository, groups, freeze, diagnostic_rerun=args.diagnostic_rerun)
    results = [verdicts(report, split) for split in splits]
    output = args.output_dir or (PROJECT_ROOT / "data" / "validation" / "lot3b5d" / "benchmarks")
    write_entity_report(report, output)
    stem = "-".join(splits)
    (output / f"verdicts-{stem}.json").write_text(json.dumps({"run": run, "results": results}, indent=2),
                                                  encoding="utf-8")
    (output / f"entities-3b5d-{stem}.md").write_text(markdown_3b5d(report, results, run), encoding="utf-8")
    print(markdown_3b5d(report, results, run))
    return 0


def _install_entities(args) -> int:
    import json
    from combatbot.entity_runtime import (
        active_profile_directory, runtime_data_directory, read_json, prepare_installation, install_prepared,
    )
    data = (args.runtime_data or runtime_data_directory()).resolve()
    registry_path = args.entity_split_registry or active_profile_directory(data) / "entity_split_registry.v2.json"
    if not registry_path.is_file():
        # Préserve le TEST 3B-5B du projet lors de la première installation, sans répartition nouvelle.
        registry_path = PROJECT_ROOT / "data/validation/lot3b5b-reprise/corpus/manifests/entity_split_registry.json"
    declared_only = not registry_path.is_file()
    if declared_only:
        # LOT 3B-5D (runtime neuf) : aucun registre historique ; seuls les combats au split déclaré
        # à la capture sont utilisés, TRAIN seulement pour les profils.
        print("Aucun registre historique : splits déclarés à la capture uniquement.", flush=True)
    seed = {"schema_version": 1, "groups": {}} if declared_only else read_json(registry_path)
    plan = prepare_installation(data, seed, declared_only=declared_only)
    print("DRY-RUN : corpus lu uniquement ; apprentissage TRAIN ; destination :", data, flush=True)
    print(json.dumps([{k: v for k, v in s.items() if k != "diagnostics"} for s in plan["summary"]], indent=2), flush=True)
    print("Fichiers prévus :", ", ".join(plan["files"]), flush=True)
    if args.dry_run:
        return 0
    result = install_prepared(data, plan)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "runtime-installation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


def _dry_run_plans(repository: CorpusRepository, args) -> int:
    from combatbot.combat.pathfinding import CombatMap
    from combatbot.combat.spells import load_profile_spells
    from combatbot.combat.targeting import CONSERVATIVE_RULES, RangeMetric, TargetingRules
    from combatbot.corpus.dry_run_replay import markdown_report, run_dry_run_replay, write_report
    from combatbot.runtime import database_path
    from combatbot.storage import Storage

    storage = Storage(database_path())
    try:
        profile_id = args.profile_id or storage.get_setting("dofbot2_last_profile")
        spells = load_profile_spells(storage, int(profile_id)) if profile_id else ()
    finally:
        storage.close()
    client = _client_directory(args)
    provider = None
    if client is not None:
        from combatbot.gamedata.provider import LocalGameDataProvider
        provider = LocalGameDataProvider(client)
        provider.scan_client()

    def map_provider(map_id: int):
        return CombatMap.from_topology(provider.get_map_topology(map_id)) if provider is not None else None

    rules = TargetingRules(range_metric=RangeMetric.LOGICAL_MANHATTAN) if args.assume_logical_range \
        else CONSERVATIVE_RULES
    report = run_dry_run_replay(repository, map_provider, spells, rules=rules, limit=args.limit)
    report["profile_id"] = profile_id
    report["client"] = str(client) if client else None
    json_path, markdown_path = write_report(report, args.output_dir or (app_data_root() / "data" / "benchmarks"))
    print(markdown_report(report))
    print(f"JSON : {json_path}")
    print(f"Markdown : {markdown_path}")
    return 0


def _client_directory(args):
    if args.client is not None:
        return args.client
    try:
        from combatbot.runtime import database_path
        from combatbot.storage import Storage
        storage = Storage(database_path())
        client = storage.get_setting("dofus_client_directory")
        storage.close()
        return Path(client) if client else None
    except Exception:  # noqa: BLE001 - réglage absent : dossier inconnu
        return None


def _combat_state(repository: CorpusRepository, args) -> int:
    import json
    from combatbot.corpus.combat_state_benchmark import (
        build_runtime_model, markdown_summary, run_combat_state_benchmark,
    )
    root = args.runtime_data or (app_data_root() / "data")
    if args.install_combat_state_model:
        print(json.dumps(build_runtime_model(repository, root), ensure_ascii=False, indent=2), flush=True)
    if args.combat_state:
        report = run_combat_state_benchmark(repository)
        output = args.output_dir or (root / "benchmarks")
        output.mkdir(parents=True, exist_ok=True)
        (output / "combat-state.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                                  encoding="utf-8")
        (output / "combat-state.md").write_text(markdown_summary(report), encoding="utf-8")
        print(markdown_summary(report), flush=True)
    return 0


def _map_resolution(repository: CorpusRepository, args) -> int:
    import json
    from combatbot.corpus.map_resolution_benchmark import markdown_summary, run_map_resolution_benchmark
    from combatbot.gamedata.map_index import load_or_build
    client = _client_directory(args)
    if client is None:
        print("Dossier client GameData inconnu : --client requis", flush=True)
        return 2
    root = app_data_root() / "data"
    from combatbot.gamedata.provider import LocalGameDataProvider
    from combatbot.vision.map_resolver import GameDataShapeSource
    index = load_or_build(Path(client), root / "gamedata" / "cache")
    provider = LocalGameDataProvider(Path(client))
    provider.scan_client()
    shapes = GameDataShapeSource(provider, index)
    output = args.output_dir or (root / "benchmarks")
    output.mkdir(parents=True, exist_ok=True)
    runs = ((False, None, False, "-baseline"), (False, shapes, True, ""), (True, shapes, True, "-with-one-confirmation"))
    for simulate, shape_source, hypotheses, suffix in runs:
        report = run_map_resolution_benchmark(repository, index, limit=args.limit,
                                              simulate_human_confirmation=simulate, shapes=shape_source,
                                              track_hypotheses=hypotheses)
        (output / f"map-resolution{suffix}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        (output / f"map-resolution{suffix}.md").write_text(markdown_summary(report), encoding="utf-8")
        print(markdown_summary(report), flush=True)
    return 0


def _grid_validation(repository: CorpusRepository, args) -> int:
    import json
    from combatbot.corpus.grid_validation_benchmark import (
        markdown_summary, run_grid_validation_benchmark, topology_source_for,
    )
    client = args.client
    if client is None:
        try:
            from combatbot.runtime import database_path
            from combatbot.storage import Storage
            storage = Storage(database_path())
            client = storage.get_setting("dofus_client_directory")
            storage.close()
        except Exception:  # noqa: BLE001 - missing settings simply mean "not available"
            client = None
    root = app_data_root() / "data"
    report = run_grid_validation_benchmark(repository, topology_source_for(client, root / "gamedata" / "cache"))
    output = args.output_dir or (root / "benchmarks")
    output.mkdir(parents=True, exist_ok=True)
    (output / "grid-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                                 encoding="utf-8")
    (output / "grid-validation.md").write_text(markdown_summary(report), encoding="utf-8")
    print(markdown_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
