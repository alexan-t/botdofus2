"""CLI du banc de mesure LOT 3B-0 : ``python -m combatbot.benchmark``."""

from __future__ import annotations

import argparse
from pathlib import Path

from combatbot.corpus.benchmark import run_benchmark, write_reports
from combatbot.corpus.repository import CorpusRepository
from combatbot.runtime import app_data_root, PROJECT_ROOT


def main(argv: list[str] | None = None) -> int:
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
    parser.add_argument("--hud-split", action="store_true",
                        help="LOT 3B-4R2 : reconstruit explicitement le split HUD (TEST gelé) et affiche la distribution")
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
    repository = CorpusRepository(args.corpus_root)
    if args.grid_validation:
        return _grid_validation(repository, args)
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
    if not registry_path.is_file():
        raise ValueError("Registre gelé requis : préciser --entity-split-registry")
    plan = prepare_installation(data, read_json(registry_path))
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
