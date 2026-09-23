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
    args = parser.parse_args(argv)
    repository = CorpusRepository(args.corpus_root)
    report = run_benchmark(repository)
    output = args.output_dir or (app_data_root() / "data" / "benchmarks")
    json_path, markdown_path = write_reports(report, output)
    print(f"Corpus : {report['corpus']['manifest_entries']} observation(s), "
          f"{report['corpus']['annotated_observations']} annotée(s)")
    print(f"JSON : {json_path}")
    print(f"Markdown : {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
