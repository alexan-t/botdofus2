"""LOT 3B-6B : apprentissage et mesure du détecteur phase/tour sur les vérités humaines.

- TRAIN : validation croisée « un combat laissé de côté » (le combat mesuré n'apprend jamais).
- VALIDATION : modèle appris sur TOUT le TRAIN, mesuré une fois ; jamais utilisé pour apprendre.
- TEST : non utilisé ici.
Les frames sont rejouées dans l'ordre de chaque combat avec le suivi temporel (TTL), comme en direct.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

from combatbot.corpus.combat_state_metrics import StateFrame, evaluate, truth_frames
from combatbot.corpus.repository import CorpusRepository
from combatbot.ui.combat_state_dialog import _read_image
from combatbot.vision.combat_state_detector import (
    STATE_LABELS, SemanticCombatStateTracker, extract_features, fit_model,
)


def _label(frame: StateFrame) -> str:
    return f"FIGHTING:{frame.turn}" if frame.phase == "FIGHTING" else frame.phase


def load_samples(repository: CorpusRepository, splits: tuple[str, ...]) -> list[dict]:
    entries = {entry.observation_id: entry for entry in repository.list_entries()}
    samples = []
    for frame in truth_frames(repository, splits):
        entry = entries[frame.observation_id]
        if "client_frame" not in entry.paths:
            continue
        end_turn = _read_image(repository.resolve(entry.paths["end_turn_crop"])) if "end_turn_crop" in entry.paths \
            else None
        client = _read_image(repository.resolve(entry.paths["client_frame"]))
        created = repository.read_observation(entry).get("created_at")
        try:
            stamp = datetime.fromisoformat(str(created)).timestamp()
        except (TypeError, ValueError):
            stamp = float(frame.frame_index)
        samples.append({"frame": frame, "label": _label(frame), "feature": extract_features(end_turn, client),
                        "time": stamp})
    return samples


def _replay(model, samples: list[dict], *, tracker: bool) -> dict[str, tuple[str, str | None]]:
    predictions = {}
    by_combat: dict[str, list[dict]] = defaultdict(list)
    for sample in samples:
        by_combat[sample["frame"].session_id].append(sample)
    for items in by_combat.values():
        smoother = SemanticCombatStateTracker()
        for sample in sorted(items, key=lambda item: item["frame"].frame_index):
            state = model.predict(sample["feature"])
            if tracker:
                state = smoother.update(state, sample["time"])
            turn = state.turn_owner.value if state.phase.value == "FIGHTING" else None
            predictions[sample["frame"].observation_id] = (state.phase.value, turn)
    return predictions


def leave_one_combat_out(samples: list[dict], *, tracker: bool = True, **parameters) -> dict:
    combats = sorted({sample["frame"].session_id for sample in samples})
    predictions: dict[str, tuple[str, str | None]] = {}
    for held_out in combats:
        training = [(s["feature"], s["label"], s["frame"].session_id) for s in samples
                    if s["frame"].session_id != held_out and s["label"] in STATE_LABELS]
        model = fit_model(training, **parameters)
        predictions.update(_replay(model, [s for s in samples if s["frame"].session_id == held_out],
                                   tracker=tracker))
    return {"combats": len(combats), **evaluate([s["frame"] for s in samples], predictions)}


def build_runtime_model(repository: CorpusRepository, data_root: Path, **parameters) -> dict:
    """Modèle runtime : TOUTES les vérités humaines TRAIN, jamais VALIDATION ni TEST."""
    samples = load_samples(repository, ("train",))
    training = [(s["feature"], s["label"], s["frame"].session_id) for s in samples if s["label"] in STATE_LABELS]
    if not training:
        return {"installed": False, "reason": "Aucune vérité phase/tour TRAIN"}
    combats = sorted({item[2] for item in training})
    model = fit_model(training, provenance={"truth_source": "human_confirmed", "training_split": "train",
                                            "train_frames": len(training), "train_combats": combats},
                      **parameters)
    target = model.save(Path(data_root) / "combat_state_model")
    return {"installed": True, "path": str(target), "train_frames": len(training), "train_combats": len(combats)}


def run_combat_state_benchmark(repository: CorpusRepository, **parameters) -> dict:
    train = load_samples(repository, ("train",))
    report = {"train_frames": len(train),
              "train_loco_with_tracker": leave_one_combat_out(train, tracker=True, **parameters),
              "train_loco_raw": leave_one_combat_out(train, tracker=False, **parameters)}
    validation = load_samples(repository, ("validation",))
    if validation and train:
        model = fit_model([(s["feature"], s["label"], s["frame"].session_id) for s in train
                           if s["label"] in STATE_LABELS], **parameters)
        report["validation"] = {"frames": len(validation),
                                **evaluate([s["frame"] for s in validation], _replay(model, validation, tracker=True))}
    return report


def markdown_summary(report: dict) -> str:
    def block(title: str, result: dict) -> list[str]:
        phase, turn, transitions = result["phase"], result["turn"], result["transitions"]
        return [f"## {title}", "",
                f"- Phase : justes {phase['correct']}, fausses **{phase['wrong']}**, abstentions {phase['abstained']} "
                f"— précision {phase['precision']:.3f}, couverture {phase['coverage']:.3f}",
                f"- Tour : justes {turn['correct']}, faux **{turn['wrong']}** (dont « mon tour » affirmé à tort "
                f"{turn['dangerous_claimed_my_turn']}, manqué {turn['dangerous_missed_my_turn']}), abstentions "
                f"{turn['abstained']} — précision {turn['precision']:.3f}, couverture {turn['coverage']:.3f}",
                f"- Transitions retrouvées (± 2 frames) : {transitions['found']}/{transitions['truth']}",
                f"- Confusions phase : {result['phase_confusion']}",
                f"- Confusions tour : {result['turn_confusion']}", ""]
    lines = ["# Détecteur phase/tour (3B-6B)", "", f"Frames TRAIN : {report['train_frames']}", ""]
    lines += block("TRAIN — un combat laissé de côté, avec suivi temporel", report["train_loco_with_tracker"])
    lines += block("TRAIN — un combat laissé de côté, frame par frame", report["train_loco_raw"])
    if "validation" in report:
        lines += block("VALIDATION (modèle appris sur tout le TRAIN)", report["validation"])
    return "\n".join(lines)
