"""LOT 3B-6B : mesures phase/tour contre la vérité humaine (jamais contre une suggestion).

Une prédiction UNKNOWN est une abstention : elle n'est jamais comptée comme juste, mais elle
n'est pas une erreur dangereuse. Erreur dangereuse = affirmer « mon tour » quand ce n'est pas
le cas (ou l'inverse), ou affirmer une phase fausse.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from combatbot.corpus.repository import CorpusRepository

UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class StateFrame:
    observation_id: str
    session_id: str
    frame_index: int
    split: str | None
    phase: str
    turn: str | None


def truth_frames(repository: CorpusRepository, splits: tuple[str, ...] | None = None) -> list[StateFrame]:
    """Frames à vérité phase/tour humaine, triées par combat puis par ordre de capture."""
    frames = []
    for entry in repository.list_entries():
        try:
            annotation = repository.read_annotation(entry.observation_id)
            if not (annotation and annotation.combat_state_confirmed):
                continue
            split = (repository.read_observation(entry).get("capture") or {}).get("entity_split_declared")
        except (OSError, ValueError):
            continue
        if splits is not None and split not in splits:
            continue
        frames.append(StateFrame(entry.observation_id, entry.session_id, entry.frame_index, split,
                                 str(annotation.combat_phase_truth), annotation.turn_owner_truth))
    return sorted(frames, key=lambda item: (item.session_id, item.frame_index))


def _transitions(sequence: list[str]) -> list[tuple[int, str, str]]:
    return [(index, sequence[index - 1], value) for index, value in enumerate(sequence)
            if index and value != sequence[index - 1]]


def evaluate(truth: list[StateFrame], predictions: dict[str, tuple[str, str | None]],
             transition_tolerance: int = 2) -> dict[str, object]:
    """``predictions`` : observation_id → (phase, tour). Une frame sans prédiction compte UNKNOWN."""
    phase_confusion: Counter = Counter()
    turn_confusion: Counter = Counter()
    phase = Counter({"correct": 0, "wrong": 0, "abstained": 0, "truth_unknown": 0})
    turn = Counter({"correct": 0, "wrong": 0, "abstained": 0,
                    "dangerous_claimed_my_turn": 0, "dangerous_missed_my_turn": 0})
    for frame in truth:
        predicted_phase, predicted_turn = predictions.get(frame.observation_id, (UNKNOWN, None))
        phase_confusion[(frame.phase, predicted_phase)] += 1
        if frame.phase == UNKNOWN:
            phase["truth_unknown"] += 1
        elif predicted_phase == UNKNOWN:
            phase["abstained"] += 1
        elif predicted_phase == frame.phase:
            phase["correct"] += 1
        else:
            phase["wrong"] += 1
        if frame.phase == "FIGHTING" and frame.turn not in (None, UNKNOWN):
            shown_turn = predicted_turn if predicted_phase == "FIGHTING" else UNKNOWN
            shown_turn = shown_turn or UNKNOWN
            turn_confusion[(frame.turn, shown_turn)] += 1
            if shown_turn == UNKNOWN:
                turn["abstained"] += 1
            elif shown_turn == frame.turn:
                turn["correct"] += 1
            else:
                turn["wrong"] += 1
                turn["dangerous_" + ("claimed_my_turn" if shown_turn == "PLAYER" else "missed_my_turn")] += 1
    # Transitions : chaque changement de vérité doit être retrouvé à ± tolérance frames.
    by_session: dict[str, list[StateFrame]] = {}
    for frame in truth:
        by_session.setdefault(frame.session_id, []).append(frame)
    transitions = Counter({"truth": 0, "found": 0})
    for frames in by_session.values():
        truth_keys = [f"{f.phase}:{f.turn}" if f.phase == "FIGHTING" else f.phase for f in frames]
        predicted_keys = []
        for frame in frames:
            p_phase, p_turn = predictions.get(frame.observation_id, (UNKNOWN, None))
            predicted_keys.append(f"{p_phase}:{p_turn}" if p_phase == "FIGHTING" else p_phase)
        predicted_changes = _transitions(predicted_keys)
        for index, before, after in _transitions(truth_keys):
            transitions["truth"] += 1
            if any(abs(p_index - index) <= transition_tolerance and p_after == after
                   for p_index, _p_before, p_after in predicted_changes):
                transitions["found"] += 1
    decided_phase = phase["correct"] + phase["wrong"]
    decided_turn = turn["correct"] + turn["wrong"]
    phase_total = phase["correct"] + phase["wrong"] + phase["abstained"]
    turn_total = sum(turn[key] for key in ("correct", "wrong", "abstained"))
    return {
        "frames": len(truth),
        "phase": {**phase, "precision": decided_phase and phase["correct"] / decided_phase,
                  "coverage": phase_total and decided_phase / phase_total},
        "turn": {**turn, "precision": decided_turn and turn["correct"] / decided_turn,
                 "coverage": turn_total and decided_turn / turn_total},
        "transitions": {**transitions, "recall": transitions["truth"] and transitions["found"] / transitions["truth"]},
        "phase_confusion": {f"{a}->{b}": n for (a, b), n in sorted(phase_confusion.items())},
        "turn_confusion": {f"{a}->{b}": n for (a, b), n in sorted(turn_confusion.items())},
    }


def distribution(truth: list[StateFrame]) -> dict[str, dict[str, int]]:
    """Frames par split et par état (pour savoir s'il manque du placement, des résultats…)."""
    result: dict[str, Counter] = {}
    for frame in truth:
        key = f"{frame.phase}:{frame.turn}" if frame.phase == "FIGHTING" else frame.phase
        result.setdefault(frame.split or "non_declare", Counter())[key] += 1
    return {split: dict(sorted(counts.items())) for split, counts in result.items()}
