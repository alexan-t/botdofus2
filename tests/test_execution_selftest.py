"""Auto-test offline de la chaîne d'exécution (aucune entrée réelle)."""
from __future__ import annotations

import json
from pathlib import Path

from combatbot.benchmark import main
from combatbot.combat.selftest import run_selftest


def test_selftest_passes_and_sends_nothing() -> None:
    report = run_selftest()
    assert report["verdict"] == "PASS" and report["real_input_sent"] == "NONE"
    assert len(report["scenarios"]) >= 12 and all(row["pass"] for row in report["scenarios"])


def test_selftest_cli_writes_its_report(tmp_path: Path, capsys) -> None:
    assert main(["--execution-selftest", "--output-dir", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "execution-selftest.json").read_text(encoding="utf-8"))["verdict"] == "PASS"
    assert "ENTRÉE RÉELLE ENVOYÉE : NONE" in capsys.readouterr().out
