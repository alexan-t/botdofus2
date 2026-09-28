"""FAST-3B7 : banc de bout en bout de l'observation (prédictions enregistrées vs vérités humaines)."""
from __future__ import annotations

import json
from pathlib import Path

from combatbot.benchmark import main as benchmark_main
from combatbot.corpus.acceptance import FAIL, NOT_EVALUABLE, PARTIAL, PASS, evaluate_observation_e2e
from combatbot.corpus.models import Annotation
from combatbot.corpus.observation_e2e_metrics import (
    ABSTAINED, CORRECT, INCOMPLETE, NO_TRUTH, NOT_RECORDED, WRONG, Frame, FramePrediction, FrameTruth,
    domain_verdict, evaluate_frames, frame_outcomes, prediction_from_document, truth_from_annotation,
)
from combatbot.corpus.observation_e2e_benchmark import markdown_report, run_observation_e2e
from combatbot.corpus.repository import CorpusRepository
from collections import Counter
from tests.test_corpus import make_debug

CONFIRMED = "2026-09-28T10:00:00+00:00"


def frame(truth: FrameTruth, prediction: FramePrediction, split: str = "validation", index: int = 0) -> Frame:
    return Frame(f"obs{index}", "s1", index, split, truth, prediction)


def full_prediction(**changes) -> FramePrediction:
    values = dict(map_status="RESOLVED", map_id=153880322, combat_state="COMBAT", player_cell=312,
                  player_recorded=True, enemy_cells=frozenset({268, 341}),
                  occupancy={312: "OCCUPIED", 268: "OCCUPIED", 341: "OCCUPIED", 300: "FREE"},
                  ap=6, mp=3, hud_recorded=True, phase="FIGHTING", turn="PLAYER", grid_source="GAMEDATA_PROJECTED",
                  grid_visibility="VISIBLE", alignment="ALIGNED",
                  stage_ms={"capture": 20.0, "map": 5.0, "grid": 30.0, "total": 120.0})
    values.update(changes)
    return FramePrediction(**values)


def full_truth(**changes) -> FrameTruth:
    values = dict(map_id=153880322, combat=True, player_cell=312, player_visible=True, entities_confirmed=True,
                  enemy_cells=frozenset({268, 341}), empty_cells=frozenset({300}), ap=6, mp=3,
                  phase="FIGHTING", turn="PLAYER")
    values.update(changes)
    return FrameTruth(**values)


def test_all_correct_frame_passes_measured_domains_but_overall_stays_incomplete() -> None:
    report = evaluate_frames([frame(full_truth(), full_prediction())])
    for domain in ("map", "combat", "player", "enemies", "occupancy", "ap", "mp", "phase", "turn"):
        assert report["domains"][domain]["status"] == PASS, domain
    # Résultat de combat et visibilité de grille n'ont pas de vérité par frame : jamais PASS.
    assert report["domains"]["result"]["status"] == NOT_EVALUABLE
    assert report["overall"] == INCOMPLETE
    assert report["end_to_end_ms"]["median"] == 120.0 and report["latency_ms"]["grid"]["frames"] == 1


def test_missing_truth_is_never_a_success() -> None:
    report = evaluate_frames([frame(FrameTruth(), full_prediction())])
    for domain in ("map", "combat", "player", "enemies", "occupancy", "ap", "mp", "phase", "turn"):
        assert report["domains"][domain]["status"] == NOT_EVALUABLE, domain
        assert report["domains"][domain]["metrics"]["frames_without_truth"] == 1
        assert report["domains"][domain]["metrics"]["correct"] == 0
    assert report["overall"] == INCOMPLETE


def test_unknown_predictions_are_abstentions_not_successes() -> None:
    prediction = full_prediction(map_status="AMBIGUOUS", map_id=None, combat_state="UNKNOWN", player_cell=None,
                                 ap=None, mp=None, phase="UNKNOWN", turn="UNKNOWN",
                                 occupancy={312: "UNKNOWN", 268: "UNKNOWN", 341: "UNKNOWN", 300: "UNKNOWN"})
    outcomes = frame_outcomes(frame(full_truth(), prediction))
    for domain in ("map", "combat", "player", "ap", "mp", "phase", "turn", "occupancy"):
        assert outcomes[domain]["outcome"] == ABSTAINED, domain
    report = evaluate_frames([frame(full_truth(), prediction)])
    assert report["domains"]["ap"]["status"] == NOT_EVALUABLE   # 0 affirmation : mesure vide
    assert report["domains"]["ap"]["metrics"]["unknown_rate"] == 1.0
    assert report["domains"]["occupancy"]["metrics"]["cell_unknown_rate"] == 1.0


def test_partial_annotation_measures_only_annotated_domains() -> None:
    truth = FrameTruth(ap=6, mp=3)             # seule la revue HUD a été faite
    report = evaluate_frames([frame(truth, full_prediction(mp=None))])
    assert report["domains"]["ap"]["status"] == PASS
    assert report["domains"]["mp"]["status"] == NOT_EVALUABLE
    assert report["domains"]["player"]["status"] == NOT_EVALUABLE
    mixed = evaluate_frames([frame(truth, full_prediction(), index=0), frame(truth, full_prediction(ap=None), index=1)])
    assert mixed["domains"]["ap"]["status"] == PARTIAL
    assert mixed["domains"]["ap"]["metrics"]["coverage"] == 0.5


def test_wrong_answers_fail_and_dangerous_turn_is_counted() -> None:
    truth = full_truth(turn="OTHER")
    report = evaluate_frames([frame(truth, full_prediction(ap=7, enemy_cells=frozenset({268})))])
    assert report["domains"]["ap"]["status"] == FAIL and report["domains"]["ap"]["wrong_examples"]
    assert report["domains"]["turn"]["status"] == FAIL
    assert report["domains"]["turn"]["metrics"]["dangerous_claimed_my_turn"] == 1
    assert report["domains"]["enemies"]["status"] == FAIL
    assert report["domains"]["enemies"]["metrics"]["cell_precision"] == 1.0
    assert report["domains"]["enemies"]["metrics"]["cell_recall"] == 0.5
    assert report["overall"] == "FAIL"


def test_not_visible_player_and_old_pipeline_fields() -> None:
    hidden = full_truth(player_visible=False, player_cell=None)
    assert frame_outcomes(frame(hidden, full_prediction(player_cell=None)))["player"]["outcome"] == CORRECT
    assert frame_outcomes(frame(hidden, full_prediction(player_cell=312)))["player"]["outcome"] == WRONG
    old = FramePrediction()                  # ancienne observation : rien d'enregistré
    outcomes = frame_outcomes(frame(full_truth(), old))
    assert {outcomes[key]["outcome"] for key in ("map", "combat", "player", "enemies", "ap", "phase")} == {NOT_RECORDED}
    assert domain_verdict(Counter({NOT_RECORDED: 3})) == (NOT_EVALUABLE, [
        "vérités présentes mais aucune affirmation : mesure vide, pas un succès"])
    assert domain_verdict(Counter({NO_TRUTH: 5}))[0] == NOT_EVALUABLE


def test_turn_is_judged_only_when_truth_is_fighting_with_owner() -> None:
    placement = full_truth(phase="PLACEMENT", turn=None)
    assert frame_outcomes(frame(placement, full_prediction()))["turn"]["outcome"] == NO_TRUTH
    # Tour prédit hors d'une phase FIGHTING prédite : affiché UNKNOWN, donc abstention.
    outcome = frame_outcomes(frame(full_truth(), full_prediction(phase="PLACEMENT")))
    assert outcome["turn"]["outcome"] == ABSTAINED and outcome["phase"]["outcome"] == WRONG


def test_map_truth_requires_user_verified_mapid() -> None:
    document = {"grid_snapshot": {"map_id_declared": 42, "map_id_source": "manual_guess"}}
    truth = truth_from_annotation(None, document)
    assert truth.map_id is None and truth.map_declared_unverified
    verified = truth_from_annotation(None, {"grid_snapshot": {"map_id_declared": 42,
                                                              "map_id_source": "user_verified_mapid"}})
    assert verified.map_id == 42
    report = evaluate_frames([frame(truth, full_prediction())])
    assert "non vérifié par /mapid" in " ".join(report["domains"]["map"]["notes"])


def test_prediction_extraction_from_recorded_document() -> None:
    document = {
        "capture": {"map_resolution": {"status": "RESOLVED", "map_id": 7}, "stage_ms": {"total": 88.5, "hud": 4},
                    "semantic_combat_state": {"phase": "FIGHTING", "turn_owner": "OTHER"}},
        "prediction": {"combat_state": "COMBAT", "player_cell_id": 12, "ap": 5, "mp": None, "entities": [],
                       "enemies": [{"cell_id": 30, "observed_this_frame": True},
                                   {"cell_id": 31, "observed_this_frame": False}],
                       "grid": {"grid_source": "GAMEDATA_PROJECTED", "cells": [{"cell_id": 30, "state": "OCCUPIED"}],
                                "alignment": {"status": "ALIGNED"}, "grid_visibility": {"state": "VISIBLE"}}},
    }
    prediction = prediction_from_document(document)
    assert (prediction.map_id, prediction.phase, prediction.turn) == (7, "FIGHTING", "OTHER")
    assert prediction.enemy_cells == frozenset({30})   # piste occultée : n'occupe rien
    assert prediction.occupancy == {30: "OCCUPIED"} and prediction.stage_ms == {"total": 88.5, "hud": 4.0}
    assert prediction.alignment == "ALIGNED" and prediction.grid_visibility == "VISIBLE"
    ambiguous = prediction_from_document({"capture": {"map_resolution": {"status": "AMBIGUOUS", "map_id": 9}}})
    assert ambiguous.map_id is None and ambiguous.map_status == "AMBIGUOUS"


def test_benchmark_on_repository_and_cli(tmp_path: Path, capsys) -> None:
    repository = CorpusRepository(tmp_path / "corpus")
    confirmed = repository.import_debug(make_debug(tmp_path / "d1", "one", ap=7, mp=1), session_id="s1", frame_index=0)
    repository.import_debug(make_debug(tmp_path / "d2", "two", ap=5, mp=1), session_id="s1", frame_index=1)
    repository.save_annotation(Annotation(observation_id=confirmed.observation_id, ap_truth=7, mp_truth=1,
                                          truth_source="human_confirmed", confirmed_at=CONFIRMED))
    snapshot = {path: path.read_bytes() for path in repository.root.rglob("*") if path.is_file()}
    report = run_observation_e2e(repository)
    assert report["frames"] == 2 and report["actions"] == "NONE"
    assert report["domains"]["ap"]["status"] == PASS and report["domains"]["ap"]["metrics"]["frames_with_truth"] == 1
    assert report["domains"]["player"]["status"] == NOT_EVALUABLE
    assert "PA" in markdown_report(report)
    after = {path: path.read_bytes() for path in repository.root.rglob("*") if path.is_file()}
    assert after == snapshot   # lecture seule : corpus et vérités intacts
    output = tmp_path / "out"
    assert benchmark_main(["--observation-e2e", "--corpus-root", str(repository.root),
                           "--output-dir", str(output)]) == 0
    written = json.loads((output / "observation-e2e.json").read_text(encoding="utf-8"))
    assert written["overall"] == INCOMPLETE and (output / "observation-e2e.md").exists()
    assert "Observation de bout en bout" in capsys.readouterr().out
    verdict = evaluate_observation_e2e(written)
    assert verdict.status == PARTIAL


def test_acceptance_verdict_for_e2e_report() -> None:
    assert evaluate_observation_e2e({"frames": 0}).status == NOT_EVALUABLE
    empty = evaluate_frames([frame(FrameTruth(), full_prediction())])
    assert evaluate_observation_e2e(empty).status == NOT_EVALUABLE
    wrong = evaluate_frames([frame(full_truth(), full_prediction(ap=1))])
    assert evaluate_observation_e2e(wrong).status == FAIL
