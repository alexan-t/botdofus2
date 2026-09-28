"""La recette Windows en une commande reste en lecture seule (vérification statique, sans PowerShell)."""
from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_windows_acceptance.ps1"


def test_script_is_readable_by_windows_powershell_5() -> None:
    assert SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf")      # UTF-8 avec BOM : accents lus correctement


def test_script_runs_every_required_step() -> None:
    text = SCRIPT.read_text(encoding="utf-8-sig")
    for flag in ("--execution-selftest", "--observation-e2e", "--dry-run-plans", "--acceptance", "--profile-id"):
        assert flag in text
    for test in ("test_browser_titled_dofus_is_not_a_game_window", "test_rapidocr_reads_synthetic_visible_text",
                 "test_window_selection_by_handle_and_client_rect"):
        assert test in text
    assert "Get-CorpusSnapshot" in text and "SUMMARY.md" in text and "PYTHONBOT_DATA_DIR" in text


def test_script_never_acts_in_the_game() -> None:
    text = SCRIPT.read_text(encoding="utf-8-sig")
    assert "Remove-Item Env:DOFBOT_ALLOW_REAL_INPUT" in text
    assert not re.search(r"DofBot2\.exe|Dofus\.exe|Start-Process|SendKeys", text, re.IGNORECASE)
    assert not re.search(r"-m\s+pip", text)                   # aucune installation automatique
    assert "--assume-logical-range" in text
    assumption = text.split('if ($AssumeLogicalRange) { $DryRun += "--assume-logical-range" }')
    assert len(assumption) == 2                               # hypothèse ajoutée uniquement par le switch
    assert "--install" not in text and "--entities" not in text   # aucun banc qui écrit gabarits/profils
