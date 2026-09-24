"""CLI du banc de mesure LOT 3B-0 : ``python -m combatbot.benchmark``."""

from __future__ import annotations

import argparse
from pathlib import Path

from combatbot.corpus.benchmark import run_benchmark, write_reports
from combatbot.corpus.repository import CorpusRepository
from combatbot.runtime import app_data_root


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
    parser.add_argument("--hud-split", action="store_true",
                        help="LOT 3B-4R2 : reconstruit explicitement le split HUD (TEST gelé) et affiche la distribution")
    parser.add_argument("--client", type=Path, help="Dossier client GameData (défaut : réglage de l'application)")
    args = parser.parse_args(argv)
    repository = CorpusRepository(args.corpus_root)
    if args.grid_validation:
        return _grid_validation(repository, args)
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
