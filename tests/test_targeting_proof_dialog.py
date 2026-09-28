"""Collecte de la preuve 4C : annotation humaine d'une frame enregistrée (rien n'est cliqué dans DOFUS)."""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from combatbot.combat.spells import CombatSpell, SpellProvenance
from combatbot.combat.targeting_proof import TargetingProofStore
from combatbot.ui.targeting_proof_dialog import TargetingProofDialog
from test_entity_corpus import at, build_corpus

SPELL = CombatSpell(key="profile:1", name="Flèche", min_range=1, max_range=4, modifiable_range=True,
                    line_cast=False, line_of_sight=True, provenance=SpellProvenance.HUMAN_CONFIRMED)


def resolve_maps(repository) -> None:
    for entry in repository.list_entries():
        path = repository.resolve(entry.paths["observation"])
        document = json.loads(path.read_text(encoding="utf-8"))
        document["capture"]["map_resolution"] = {"status": "RESOLVED", "map_id": 42}
        path.write_text(json.dumps(document), encoding="utf-8")


def test_dialog_builds_and_stores_a_client_truth_sample(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    repository = build_corpus(tmp_path, ("combat-a",))
    store = TargetingProofStore(tmp_path / "targeting_proof")
    snapshot = {item: item.read_bytes() for item in repository.root.rglob("*") if item.is_file()}
    dialog = TargetingProofDialog(repository, [SPELL, CombatSpell(key="profile:2")], store)
    assert dialog.spell.count() == 1                          # sort sans portée : exclu
    with pytest.raises(ValueError, match="Map non prouvée"):
        dialog.build_sample()
    resolve_maps(repository)
    dialog._show()
    with pytest.raises(ValueError, match="lanceur"):
        dialog.build_sample()
    dialog._choose_caster()
    dialog.cycle(at(0))
    dialog.cycle(at(2))                                        # ciblable
    dialog.cycle(at(3)), dialog.cycle(at(3))                   # non ciblable
    dialog.cycle(at(1, 1)), dialog.cycle(at(1, 1)), dialog.cycle(at(1, 1))     # obstacle
    dialog.cycle(at(-1)), dialog.cycle(at(-1)), dialog.cycle(at(-1)), dialog.cycle(at(-1))   # effacée
    dialog.bonus.setValue(2)
    sample = dialog.build_sample()
    assert (sample.caster_cell_id, sample.map_id, sample.range_bonus) == (at(0), 42, 2)
    assert sample.targetable == {at(2)} and sample.not_targetable == {at(3)} and sample.obstacles == {at(1, 1)}
    dialog._save()
    assert store.load() == [sample]
    changed = {item: item.read_bytes() for item in repository.root.rglob("*") if item.is_file()}
    assert set(changed) == set(snapshot)                        # aucune vérité du corpus ajoutée ni supprimée
    dialog.close()
