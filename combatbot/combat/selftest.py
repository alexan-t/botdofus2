"""Auto-test offline de la chaîne d'exécution — ``python -m combatbot.benchmark --execution-selftest``.

Rejoue des scénarios écrits (map synthétique entièrement praticable, géométrie synthétique, sorts
synthétiques) à travers le vrai code : traduction 5A1, garde-fous, coordinateur 5B, tour 5C,
boucle 6A, placement 6B, lancement 6C, exécuteur souris désactivé 5A2. Les actions partent vers un
``FakeSender`` ou un backend qui enregistre : **aucune entrée réelle**. Vérifie aussi que la porte
globale d'entrée réelle est fermée sur la machine qui exécute l'auto-test.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from combatbot.combat.closed_loop import ClosedLoopCoordinator, LoopConfig
from combatbot.combat.fight_loop import FightConfig, FightLoop, FightOutcome
from combatbot.combat.launch import (
    LaunchRequested, LaunchSent, LaunchState, LaunchStateMachine, PhaseObserved, TargetConfirmed,
)
from combatbot.combat.mouse_executor import MouseActionExecutor, RecordingBackend, WindowState
from combatbot.combat.pathfinding import CombatMap
from combatbot.combat.placement import CellSource, PlacementState, PlacementStatus, decide_placement
from combatbot.combat.safety import REAL_INPUT_GATE, EmergencyStop, SafetyGuard, SafetyLimits
from combatbot.combat.screen_actions import ScreenGeometry, SpellBarLayout, translate_step
from combatbot.combat.scripted import FakeClock, FakeSender, ScriptedObserver
from combatbot.combat.spells import CombatSpell, SpellProvenance, SpellSlot
from combatbot.combat.state import EnemyState, RealCombatState
from combatbot.combat.targeting import RangeMetric, TargetingRules
from combatbot.combat.turn_runner import SingleTurnRunner, TurnConfig, TurnOutcome, make_planner
from combatbot.gamedata.models import DofusCellId, GameMap, GameMapCell, GridCoordinate
from combatbot.gamedata.topology import CELL_COUNT, cell_to_grid, grid_to_cell
from combatbot.vision.coordinates import ClientSize, LayoutTransform, NormalizedRect, ScreenPoint
from combatbot.vision.grid_projection import GridScreenTransform

MAP_ID = 1
PLAYER = 300
HYPOTHESIS = TargetingRules(range_metric=RangeMetric.LOGICAL_MANHATTAN)   # synthétique : jamais le jeu réel


def _offset(cell: int, dx: int, dy: int) -> int:
    origin = cell_to_grid(cell)
    return int(grid_to_cell(GridCoordinate(origin.x + dx, origin.y + dy)))


ENEMY = _offset(PLAYER, 2, 0)
MAP = CombatMap.from_game_map(GameMap(MAP_ID, tuple(GameMapCell(DofusCellId(i), walkable=True,
                                                                non_walkable_during_fight=False)
                                                    for i in range(CELL_COUNT)), "selftest", 11))
SPELL = CombatSpell(key="selftest:1", name="Sort synthétique", slot=SpellSlot(1, 1), ap_cost=3, min_range=1,
                    max_range=2, modifiable_range=False, line_cast=False, line_of_sight=False, per_turn=2,
                    per_target=2, provenance=SpellProvenance.HUMAN_CONFIRMED)
GEOMETRY = ScreenGeometry(
    hwnd=1, layout=LayoutTransform(ScreenPoint(100, 50), ClientSize(1000, 800), NormalizedRect(0.05, 0.05, 0.82, 0.725)),
    layout_digest="selftest", zones={"spell_bar": NormalizedRect(0.5, 0.9, 0.4, 0.08),
                                     "end_turn": NormalizedRect(0.9, 0.9, 0.08, 0.06)},
    grid_transform=GridScreenTransform.from_cell_size((33.0, 18.5), 54.0, 27.0), grid_confidence=1.0,
    spell_bar=SpellBarLayout(10, 2, 1), map_id=MAP_ID)


def _state(**changes) -> RealCombatState:
    values = dict(map_id=MAP_ID, player_cell_id=PLAYER, ap=6, mp=3, enemies=(EnemyState("E1", ENEMY),),
                  phase="FIGHTING", turn="PLAYER", safe_for_decision=True, turn_number=2)
    values.update(changes)
    return RealCombatState(**values)


def _turn(script, sender=None):
    clock = FakeClock()
    guard = SafetyGuard(SafetyLimits(), EmergencyStop(), clock)
    observer = ScriptedObserver(script, clock=clock, hwnd=GEOMETRY.hwnd, layout_digest=GEOMETRY.layout_digest)
    sender = sender or FakeSender()
    loop = ClosedLoopCoordinator(observer, sender, guard=guard, spells={SPELL.key: SPELL},
                                 config=LoopConfig(effect_timeout_s=2.0), clock=clock, sleep=clock.sleep)
    planner = make_planner(lambda map_id: MAP if map_id == MAP_ID else None, [SPELL], rules=HYPOTHESIS)
    return SingleTurnRunner(observer, planner, loop, config=TurnConfig(wait_player_turn_s=5), clock=clock,
                            sleep=clock.sleep), sender


def _scenarios():
    turn, sender = _turn([_state(), _state(ap=3), _state(ap=0), _state(turn="OTHER")])
    yield "tour complet : 2 sorts puis fin de tour", "COMPLETE", turn.run(GEOMETRY).outcome.value

    turn, _sender = _turn([_state(), _state(ap=3, safe_for_decision=False)])
    yield "grille UNKNOWN après une action", "ABORTED", turn.run(GEOMETRY).outcome.value

    turn, _sender = _turn([_state()])
    yield "aucune observation après l'action", "TIMED_OUT", turn.run(GEOMETRY).outcome.value

    turn, _sender = _turn([_state(ap=None, safe_for_decision=False)] * 200)
    yield "PA inconnus : mon tour jamais sûr", "TIMED_OUT", turn.run(GEOMETRY).outcome.value

    holder = {}
    turn, _sender = _turn([_state(), _state(ap=3)],
                          FakeSender(on_send=lambda i, _a: holder["t"].stop.trigger("F9") if i == 1 else None))
    holder["t"] = turn
    yield "F9 entre deux actions", "ABORTED", turn.run(GEOMETRY).outcome.value

    turn, _sender = _turn([_state(ap=2), _state(turn="OTHER"), _state(turn="OTHER"), _state(ap=2),
                           _state(turn="OTHER"), _state(phase="RESULTS", turn=None)])
    loop = FightLoop(turn.observer, turn, lambda: GEOMETRY, config=FightConfig(wait_turn_s=30, unknown_grace_s=3),
                     clock=turn.clock, sleep=turn.sleep)
    yield "combat multi-tours jusqu'aux résultats", FightOutcome.RESULT.value, loop.run().outcome.value

    step = make_planner(lambda _m: MAP, [SPELL], rules=HYPOTHESIS)(_state()).steps[-1]
    action = translate_step(step, GEOMETRY, {SPELL.key: SPELL}).actions[0]
    origin, size = GEOMETRY.layout.client_screen_origin, GEOMETRY.layout.client_size
    probe = type("Probe", (), {"current": lambda self, hwnd: WindowState(1, 1, round(origin.x), round(origin.y),
                                                                          size.width, size.height)})()
    backend = RecordingBackend()
    result = MouseActionExecutor(backend, window_probe=probe, stop=EmergencyStop()).send(action, GEOMETRY,
                                                                                         correlation_id="selftest")
    yield "exécuteur souris construit par défaut", "DISABLED", \
        "DISABLED" if not result.sent and not backend.calls else "SENT"
    real = RecordingBackend(real=True)
    result = MouseActionExecutor(real, window_probe=probe, enabled=True, stop=EmergencyStop()).send(
        action, GEOMETRY, correlation_id="selftest-real")
    yield "backend réel activé sans autorisation", "REFUSED", "REFUSED" if not result.sent and not real.calls else "SENT"

    near = _offset(PLAYER, 1, 0)
    hinted = PlacementState.from_gamedata_hints(MAP_ID, "PLACEMENT", {near}, PLAYER, {ENEMY})
    yield "placement sur indices GameData", "BLOCKED", decide_placement(hinted, MAP).status.value
    observed = replace(hinted, source=CellSource.OBSERVED_ACTIVE)
    decision = decide_placement(observed, MAP)
    yield "placement sur cases observées actives", PlacementStatus.MOVE.value, decision.status.value

    machine = LaunchStateMachine()
    for event in (TargetConfirmed(0, "G1", MAP_ID, 0.95), LaunchRequested(1), LaunchSent(2),
                  PhaseObserved(3, "RESULTS")):
        machine.handle(event)
    yield "lancement : résultats avant le combat", LaunchState.FAILED.value, machine.state.value


def run_selftest() -> dict[str, object]:
    rows = [{"scenario": name, "expected": expected, "observed": observed, "pass": expected == observed}
            for name, expected, observed in _scenarios()]
    gate = REAL_INPUT_GATE.refusal()
    rows.append({"scenario": "porte d'entrée réelle fermée sur cette machine", "expected": "CLOSED",
                 "observed": "CLOSED" if gate is not None else "OPEN", "pass": gate is not None, "detail": gate})
    return {"schema_version": 1, "lot": "FAST-EXECUTION-CORE", "benchmark": "execution-selftest",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "verdict": "PASS" if all(row["pass"] for row in rows) else "FAIL", "scenarios": rows,
            "real_input_sent": "NONE"}


def markdown_report(report: dict) -> str:
    lines = ["# Auto-test offline de la chaîne d'exécution", "",
             f"Verdict : **{report['verdict']}** · ENTRÉE RÉELLE ENVOYÉE : {report['real_input_sent']}", "",
             "| Scénario | Attendu | Observé | |", "|---|---|---|---|"]
    lines += [f"| {row['scenario']} | {row['expected']} | {row['observed']} | {'OK' if row['pass'] else 'ÉCHEC'} |"
              for row in report["scenarios"]]
    return "\n".join(lines) + "\n"


def write_report(report: dict, output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path, md_path = output / "execution-selftest.json", output / "execution-selftest.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, md_path
