"""Préparation live : choix du profil, sorts prêts, page de sorts confirmée (lecture seule, aucune action)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from combatbot.benchmark import main
from combatbot.combat.spell_page import (
    ConfirmedSpellPage, load_confirmed_page, save_confirmed_page, signature_agreement, visible_page,
)
from combatbot.live.profiles import resolve_profile
from combatbot.live.spell_readiness import markdown_report, spell_readiness
from combatbot.vision.models import Profile
from combatbot.storage import Storage
from combatbot.vision.icons import bar_signature, scan_spell_bar, slot_icons
from combatbot.vision.models import IconCandidate

FULL = {"name": "Flèche", "ap_cost": 3, "min_range": 1, "max_range": 5, "modifiable_range": 1, "line_cast": 0,
        "line_of_sight": 1, "per_turn": 2, "per_target": 1}


def storage_with(tmp_path: Path, *labels: str) -> tuple[Storage, list[int]]:
    storage = Storage(tmp_path / "db.sqlite3")
    return storage, [storage.save_profile(Profile(None, label)) for label in labels]


def test_profile_is_only_chosen_when_unambiguous(tmp_path: Path) -> None:
    storage, _ = storage_with(tmp_path)                      # une base neuve contient « Profil 1 »
    single = resolve_profile(storage)
    assert single.profile_id is not None and single.reason == "profil unique en base"
    storage.save_profile(Profile(None, "Cra"))
    storage.set_setting("dofbot2_last_profile", single.profile_id)          # jamais utilisé pour deviner
    ambiguous = resolve_profile(storage)
    assert ambiguous.profile_id is None and "ambigu" in ambiguous.reason and len(ambiguous.candidates) == 2
    assert resolve_profile(storage, single.profile_id).profile_id == single.profile_id
    assert resolve_profile(storage, 999).profile_id is None


def test_spell_readiness_explains_every_refusal(tmp_path: Path) -> None:
    storage, (profile,) = storage_with(tmp_path, "Iop")
    ready = storage.save_scan_candidate(profile, IconCandidate(1, 1, b"png", "a", .9, .9, "Inconnu"))
    storage.save_profile_spell_fields(ready, FULL, confirm=True)
    partial = storage.save_scan_candidate(profile, IconCandidate(1, 2, b"png", "b", .9, .9, "Inconnu"))
    storage.save_profile_spell_fields(partial, {"ap_cost": 4})
    storage.save_scan_candidate(profile, IconCandidate(2, 1, b"png", "c", .9, .95, "Reconnu"))
    before = [dict(row) for row in storage.list_profile_spells(profile)]
    report = spell_readiness(storage, profile)
    assert report["total"] == 3 and report["decision_ready"] == 1
    by_slot = {(item["page"], item["slot"]): item for item in report["spells"]}
    assert by_slot[(1, 1)]["decision_ready"] and by_slot[(1, 1)]["refusal_reasons"] == []
    assert any("À vérifier" in reason for reason in by_slot[(1, 2)]["refusal_reasons"])
    assert any("inconnu : " in reason for reason in by_slot[(2, 1)]["refusal_reasons"])
    assert "| 1 | 1 | Flèche | 3 | 1–5 |" in markdown_report(report)
    assert [dict(row) for row in storage.list_profile_spells(profile)] == before          # rien modifié


def bar(columns: int = 4, rows: int = 1, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    image = np.full((48 * rows, 48 * columns, 3), 20, np.uint8)
    for index in range(columns * rows - 1):                      # dernière case vide
        y, x = (index // columns) * 48, (index % columns) * 48
        image[y + 6:y + 42, x + 6:x + 42] = rng.integers(0, 255, (36, 36, 3), dtype=np.uint8)
    return image


def test_bar_signature_uses_the_scan_cut_and_marks_empty_slots() -> None:
    image = bar()
    signature = bar_signature(image, 4, 1)
    assert list(signature) == [1, 2, 3, 4] and signature[4] is None and all(signature[i] for i in (1, 2, 3))
    scan = scan_spell_bar(image, page=1, columns=4, rows=1)
    assert {item.slot: item.visual_hash for item in scan.candidates} == {i: signature[i] for i in (1, 2, 3)}
    assert [slot for slot, _icon in slot_icons(image, 4, 1)] == [1, 2, 3, 4]


def test_confirmed_page_is_invalidated_when_the_bar_changes(tmp_path: Path) -> None:
    reference = bar_signature(bar(seed=0), 4, 1)
    confirmed = ConfirmedSpellPage(2, reference, "t")
    assert visible_page(None, reference) == (None, "page de sorts jamais confirmée")
    assert visible_page(confirmed, None)[0] is None
    assert visible_page(confirmed, reference)[0] == 2
    dimmed = bar_signature((bar(seed=0) * 0.6).astype(np.uint8), 4, 1)       # sorts grisés : même barre
    assert visible_page(confirmed, dimmed)[0] == 2
    other = bar_signature(bar(seed=1), 4, 1)
    page, reason = visible_page(confirmed, other)
    assert page is None and "barre changée" in reason
    assert signature_agreement(bar_signature(bar(5, 1), 5, 1), reference)[0] is False
    empty = {1: None, 2: None, 3: None, 4: None}
    assert visible_page(ConfirmedSpellPage(1, empty, "t"), empty)[0] is None
    storage, (profile,) = storage_with(tmp_path, "Iop")
    assert load_confirmed_page(storage, profile) is None                       # jamais de page 1 par défaut
    save_confirmed_page(storage, profile, confirmed)
    assert load_confirmed_page(storage, profile) == confirmed
    save_confirmed_page(storage, profile, None)
    assert load_confirmed_page(storage, profile) is None
    with pytest.raises(ValueError):
        ConfirmedSpellPage.from_dict({"page": 0, "signature": {}})


def test_cli_profile_resolution_and_readiness(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PYTHONBOT_DATA_DIR", str(tmp_path))
    (tmp_path / "data").mkdir()
    from combatbot.runtime import database_path
    storage = Storage(database_path())
    first = storage.save_profile(Profile(None, "Iop"))       # + « Profil 1 » créé par la base : ambigu
    storage.close()
    assert main(["--resolve-profile"]) == 2
    assert json.loads(capsys.readouterr().out)["profile_id"] is None
    assert main(["--spell-readiness"]) == 2 and f"--profile-id {first}" in capsys.readouterr().out
    assert main(["--spell-readiness", "--profile-id", str(first), "--output-dir", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "profile-spells-readiness.md").exists()
