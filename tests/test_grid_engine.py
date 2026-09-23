import pytest

from combatbot.engine import CombatEngine
from combatbot.grid import Grid
from combatbot.models import Cell, CombatState, Spell, Strategy, StrategyMode


def demo_spell(**changes) -> Spell:
    fields = dict(
        name="Sort de test", ap_cost=3, min_range=1, max_range=4,
        modifiable_range=False, line_cast=False, line_of_sight=True,
        per_turn=2, per_target=2, priority=10, damage=6,
    )
    fields.update(changes)
    return Spell(**fields)


def run_to_end(engine: CombatEngine, limit: int = 400) -> None:
    for _ in range(limit):
        if engine.state == CombatState.FINISHED:
            return
        engine.step()
    pytest.fail("La simulation n'a pas atteint FINISHED")


def test_grid_reachability_obstacles_and_line_of_sight() -> None:
    grid = Grid(5, 5, frozenset({Cell(2, 1), Cell(2, 2), Cell(2, 3)}))
    reachable = grid.reachable(Cell(1, 2), 2, {Cell(1, 1)})
    assert reachable[Cell(1, 2)] == 0
    assert Cell(2, 2) not in reachable
    assert Cell(1, 1) not in reachable
    assert Cell(3, 2) not in reachable
    assert not grid.line_of_sight(Cell(1, 2), Cell(3, 2))
    assert grid.line_of_sight(Cell(0, 0), Cell(1, 0))


def test_state_machine_movement_ap_mp_and_victory() -> None:
    engine = CombatEngine([demo_spell()], Strategy())
    assert engine.state == CombatState.IDLE
    engine.start()
    assert engine.state == CombatState.COMBAT_DETECTED
    engine.step()
    assert engine.state == CombatState.WAIT_TURN
    engine.step()
    assert (engine.state, engine.ap, engine.mp, engine.turn) == (CombatState.ANALYZE, 6, 3, 1)
    run_to_end(engine)

    assert engine.outcome == "victoire"
    assert engine.turn == 2
    assert all(not enemy.alive for enemy in engine.enemies)
    assert engine.xp == 120 and engine.kamas == 35
    assert any("Déplacement" in message for message in engine.history)
    assert any("PA restants" in message for message in engine.history)
    assert engine.ap >= 0 and engine.mp >= 0
    assert engine.player.cell not in engine.grid.obstacles


def test_spell_line_range_los_and_cast_limit() -> None:
    engine = CombatEngine([demo_spell(ap_cost=1, damage=1, line_cast=True)], Strategy())
    engine.start()
    engine.step()
    engine.step()
    enemy = engine.enemies[0]
    engine.player.cell = Cell(5, 3)  # diagonale : lancer en ligne interdit
    assert engine.available_spells(enemy) == []
    engine.player.cell = Cell(7, 3)
    assert len(engine.available_spells(enemy)) == 1
    engine.player.cell = Cell(3, 2)  # obstacle en (4,2) : ligne de vue coupée
    enemy.cell = Cell(5, 2)
    assert engine.available_spells(enemy) == []

    engine.player.cell = Cell(7, 3)
    enemy.cell = Cell(7, 2)
    for _ in range(5):
        engine.step()
    assert engine._casts_per_turn["Sort de test"] == 2
    assert engine.ap == 4
    assert engine.available_spells(enemy) == []


def test_survival_strategy_retreats_at_low_hp() -> None:
    engine = CombatEngine([demo_spell()], Strategy(mode=StrategyMode.SURVIVAL, hp_threshold=50))
    engine.start()
    engine.step()
    engine.step()
    engine.player.hp = 10
    initial = min(engine.player.cell.distance(e.cell) for e in engine.enemies)
    engine.step()  # décision
    assert engine.pending is not None and engine.pending.kind == "move"
    engine.step()  # exécution
    assert min(engine.player.cell.distance(e.cell) for e in engine.enemies) > initial
    assert engine.mp < 3


def test_defeat_and_no_spell_validation() -> None:
    with pytest.raises(ValueError, match="au moins un sort"):
        CombatEngine([], Strategy()).start()
    engine = CombatEngine([demo_spell(damage=0)], Strategy())
    engine.start()
    run_to_end(engine)
    assert engine.outcome == "défaite"
    assert engine.player.hp == 0
    assert engine.xp == 0 and engine.kamas == 0
