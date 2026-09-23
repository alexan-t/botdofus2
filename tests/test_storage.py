from combatbot.models import CombatEvent, Spell, Strategy, StrategyMode, TargetPriority
from combatbot.storage import Storage


def test_sqlite_settings_spells_logs_and_statistics(tmp_path) -> None:
    path = tmp_path / "combat.sqlite3"
    storage = Storage(path)
    assert len(storage.list_spells()) == 1
    spell = storage.list_spells()[0]
    assert spell.name == "Sort simulé A"
    storage.delete_spell(spell.id)
    assert storage.list_spells() == []
    storage.set_setting("player_name", "Testeur")
    storage.save_strategy(Strategy(StrategyMode.MELEE, 25, TargetPriority.LOWEST_HP, 1))
    new_id = storage.save_spell(Spell("Sort libre", 2, 0, 3, True, True, False, 3, 1, 7, 4))
    storage.record_event(CombatEvent("INFO", "test", "Action test", {"ap": 2}))
    storage.record_combat("victoire", 3, 100, 20)
    storage.record_combat("défaite", 5, 0, 0)
    storage.close()

    reopened = Storage(path)
    assert reopened.get_setting("player_name") == "Testeur"
    assert reopened.load_strategy().mode == StrategyMode.MELEE
    assert reopened.load_strategy().target_priority == TargetPriority.LOWEST_HP
    assert [s.id for s in reopened.list_spells()] == [new_id]
    assert reopened.statistics() == {
        "combats": 2, "victories": 1, "defeats": 1, "xp": 100, "kamas": 20,
    }
    assert reopened.recent_events()[0]["context_json"] == '{"ap": 2}'
    reopened.delete_spell(new_id)
    reopened.close()

    empty = Storage(path)
    assert empty.list_spells() == []  # Ne réinsère pas la démo après une suppression volontaire.
    empty.close()
