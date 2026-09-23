"""Machine à états et règles d'un combat entièrement simulé."""

from __future__ import annotations

from collections import Counter

from combatbot.grid import Grid
from combatbot.models import (
    Action, Actor, Cell, CombatEvent, CombatSnapshot, CombatState,
    Spell, Strategy, StrategyMode, TargetPriority,
)


class CombatEngine:
    MAX_TURNS = 30

    def __init__(self, spells: list[Spell], strategy: Strategy, player_name: str = "Personnage test") -> None:
        for spell in spells:
            spell.validate()
        strategy.validate()
        self.spells = sorted(spells, key=lambda item: (-item.priority, item.ap_cost, item.name))
        self.strategy = strategy
        self.player_name = player_name.strip() or "Personnage test"
        self.grid = Grid(9, 7, frozenset({Cell(4, 1), Cell(4, 2), Cell(4, 4), Cell(4, 5)}))
        self.state = CombatState.IDLE
        self.player = Actor(self.player_name, Cell(1, 3), 30, 30)
        self.enemies = [Actor("Ennemi A", Cell(7, 2), 12, 12), Actor("Ennemi B", Cell(7, 5), 12, 12)]
        self.turn = 0
        self.ap = 0
        self.mp = 0
        self.pending: Action | None = None
        self.selected_target: str | None = None
        self.selected_spell: str | None = None
        self.last_action = ""
        self.history: list[str] = []
        self.outcome: str | None = None
        self.xp = 0
        self.kamas = 0
        self._events: list[CombatEvent] = []
        self._casts_per_turn: Counter[str] = Counter()
        self._casts_per_target: Counter[tuple[str, str]] = Counter()

    def start(self) -> None:
        if self.state not in (CombatState.IDLE, CombatState.FINISHED):
            raise RuntimeError("Un combat est déjà en cours")
        if not self.spells:
            raise ValueError("Configurez au moins un sort simulé avant de démarrer")
        self.__init__(self.spells, self.strategy, self.player_name)
        self.state = CombatState.COMBAT_DETECTED
        self._emit("combat_detected", "Combat simulé détecté", state=self.state.value)

    def stop(self) -> None:
        if self.state != CombatState.IDLE:
            self._emit("stopped", "Simulation arrêtée par l'utilisateur")
        self.state = CombatState.IDLE
        self.pending = None

    def step(self) -> None:
        if self.state in (CombatState.IDLE, CombatState.FINISHED):
            return
        if self.state == CombatState.COMBAT_DETECTED:
            self.state = CombatState.WAIT_TURN
            self._emit("state_transition", "Attente du tour du joueur", state=self.state.value)
        elif self.state == CombatState.WAIT_TURN:
            if self.turn >= self.MAX_TURNS:
                self._finish("défaite", "Limite de tours de la simulation atteinte")
                return
            self.turn += 1
            self.ap, self.mp = 6, 3
            self._casts_per_turn.clear()
            self._casts_per_target.clear()
            self.state = CombatState.ANALYZE
            self._emit("turn_started", f"Tour {self.turn} : 6 PA, 3 PM", turn=self.turn)
        elif self.state == CombatState.ANALYZE:
            self.pending = self._choose_action()
            if self.pending.kind == "end_turn":
                self.state = CombatState.END_TURN
                self.selected_target = self.selected_spell = None
                self._emit("decision", "Fin du tour choisie", turn=self.turn)
            else:
                self.state = CombatState.EXECUTE
                self.selected_target = self.pending.target
                self.selected_spell = self._spell_by_id(self.pending.spell_id).name if self.pending.kind == "cast" else None
                detail = self.selected_spell or f"déplacement vers {self.pending.destination}"
                self._emit("decision", f"Cible : {self.selected_target} ; action : {detail}", turn=self.turn)
        elif self.state == CombatState.EXECUTE:
            self._execute_pending()
        elif self.state == CombatState.END_TURN:
            self._enemy_turn()
            if self.player.alive:
                self.state = CombatState.WAIT_TURN
                self._emit("turn_ended", f"Fin du tour {self.turn}", turn=self.turn)
            else:
                self._finish("défaite", "Le personnage simulé a été vaincu")

    def snapshot(self) -> CombatSnapshot:
        return CombatSnapshot(
            state=self.state,
            turn=self.turn,
            player=Actor(self.player.name, self.player.cell, self.player.hp, self.player.max_hp),
            enemies=tuple(Actor(a.name, a.cell, a.hp, a.max_hp) for a in self.enemies),
            obstacles=self.grid.obstacles,
            width=self.grid.width,
            height=self.grid.height,
            ap=self.ap,
            mp=self.mp,
            selected_target=self.selected_target,
            selected_spell=self.selected_spell,
            last_action=self.last_action,
            history=tuple(self.history),
            outcome=self.outcome,
            xp=self.xp,
            kamas=self.kamas,
        )

    def drain_events(self) -> list[CombatEvent]:
        events, self._events = self._events, []
        return events

    def available_spells(self, enemy: Actor) -> list[Spell]:
        return [spell for spell in self.spells if self._can_cast(spell, self.player.cell, enemy)]

    def _spell_by_id(self, spell_id: int | None) -> Spell:
        for index, spell in enumerate(self.spells):
            if (spell.id if spell.id is not None else index) == spell_id:
                return spell
        raise RuntimeError("Sort simulé introuvable")

    def _can_cast(self, spell: Spell, origin: Cell, enemy: Actor) -> bool:
        if not enemy.alive or self.ap - spell.ap_cost < self.strategy.reserve_ap:
            return False
        if self._casts_per_turn[spell.name] >= spell.per_turn:
            return False
        if self._casts_per_target[(spell.name, enemy.name)] >= spell.per_target:
            return False
        distance = origin.distance(enemy.cell)
        if not spell.min_range <= distance <= spell.max_range:
            return False
        if spell.line_cast and origin.x != enemy.cell.x and origin.y != enemy.cell.y:
            return False
        other_enemies = {actor.cell for actor in self.enemies if actor.alive and actor.name != enemy.name}
        if spell.line_of_sight and not self.grid.line_of_sight(origin, enemy.cell, other_enemies):
            return False
        return True

    def _ordered_enemies(self) -> list[Actor]:
        enemies = [enemy for enemy in self.enemies if enemy.alive]
        if self.strategy.target_priority == TargetPriority.LOWEST_HP:
            return sorted(enemies, key=lambda e: (e.hp, self.player.cell.distance(e.cell), e.name))
        return sorted(enemies, key=lambda e: (self.player.cell.distance(e.cell), e.hp, e.name))

    def _choose_action(self) -> Action:
        enemies = self._ordered_enemies()
        if not enemies:
            return Action("end_turn")
        low_hp = self.player.hp * 100 <= self.player.max_hp * self.strategy.hp_threshold
        if self.strategy.mode == StrategyMode.SURVIVAL and low_hp and self.mp > 0:
            retreat = self._choose_movement(enemies[0], retreat=True)
            if retreat is not None:
                return retreat
        for enemy in enemies:
            for index, spell in enumerate(self.spells):
                if self._can_cast(spell, self.player.cell, enemy):
                    return Action("cast", enemy.name, spell.id if spell.id is not None else index, cost=spell.ap_cost)
        if self.mp > 0:
            for enemy in enemies:
                move = self._choose_movement(enemy)
                if move is not None:
                    return move
        return Action("end_turn")

    def _choose_movement(self, target: Actor, retreat: bool = False) -> Action | None:
        occupied = {enemy.cell for enemy in self.enemies if enemy.alive}
        reachable = self.grid.reachable(self.player.cell, self.mp, occupied)
        candidates = [(cell, cost) for cell, cost in reachable.items() if cost > 0]
        if not candidates:
            return None
        if retreat:
            current_safety = min(self.player.cell.distance(e.cell) for e in self.enemies if e.alive)
            best = max(candidates, key=lambda item: (
                min(item[0].distance(e.cell) for e in self.enemies if e.alive), -item[1], -item[0].x, -item[0].y,
            ))
            if min(best[0].distance(e.cell) for e in self.enemies if e.alive) <= current_safety:
                return None
        else:
            castable = [
                item for item in candidates
                if any(self._can_cast(spell, item[0], target) for spell in self.spells)
            ]
            if castable:
                if self.strategy.mode == StrategyMode.DISTANCE:
                    best = max(castable, key=lambda item: (item[0].distance(target.cell), -item[1], -item[0].x, -item[0].y))
                else:
                    best = min(castable, key=lambda item: (item[0].distance(target.cell), item[1], item[0].x, item[0].y))
            else:
                best = min(candidates, key=lambda item: (item[0].distance(target.cell), item[1], item[0].x, item[0].y))
                if best[0].distance(target.cell) >= self.player.cell.distance(target.cell):
                    return None
        return Action("move", target.name, destination=best[0], cost=best[1])

    def _execute_pending(self) -> None:
        action = self.pending
        if action is None:
            raise RuntimeError("Aucune décision à exécuter")
        if action.kind == "cast":
            spell = self._spell_by_id(action.spell_id)
            enemy = next((e for e in self.enemies if e.name == action.target), None)
            if enemy is None or not self._can_cast(spell, self.player.cell, enemy):
                raise RuntimeError("Sort ou cible devenu indisponible")
            self.ap -= spell.ap_cost
            enemy.hp = max(0, enemy.hp - spell.damage)
            self._casts_per_turn[spell.name] += 1
            self._casts_per_target[(spell.name, enemy.name)] += 1
            self._emit("cast", f"{spell.name} sur {enemy.name} : -{spell.damage} PV, {self.ap} PA restants",
                       spell=spell.name, target=enemy.name, ap=self.ap, enemy_hp=enemy.hp)
            if not any(e.alive for e in self.enemies):
                self._finish("victoire", "Tous les ennemis simulés sont vaincus")
                return
        elif action.kind == "move":
            occupied = {enemy.cell for enemy in self.enemies if enemy.alive}
            costs = self.grid.reachable(self.player.cell, self.mp, occupied)
            if action.destination not in costs or costs[action.destination] != action.cost:
                raise RuntimeError("Déplacement devenu inaccessible")
            self.player.cell = action.destination
            self.mp -= action.cost
            self._emit("move", f"Déplacement vers ({self.player.cell.x}, {self.player.cell.y}) : -{action.cost} PM, {self.mp} PM restants",
                       x=self.player.cell.x, y=self.player.cell.y, mp=self.mp)
        else:
            raise RuntimeError(f"Action inconnue : {action.kind}")
        self.pending = None
        self.state = CombatState.ANALYZE

    def _enemy_turn(self) -> None:
        for enemy in self.enemies:
            if enemy.alive and enemy.cell.distance(self.player.cell) <= 4 and self.grid.line_of_sight(enemy.cell, self.player.cell):
                self.player.hp = max(0, self.player.hp - 2)
                self._emit("enemy_attack", f"{enemy.name} inflige 2 dégâts simulés ; {self.player.hp} PV restants",
                           enemy=enemy.name, player_hp=self.player.hp)
                if not self.player.alive:
                    break

    def _finish(self, outcome: str, message: str) -> None:
        self.state = CombatState.FINISHED
        self.outcome = outcome
        self.xp = 120 if outcome == "victoire" else 0
        self.kamas = 35 if outcome == "victoire" else 0
        self._emit("combat_result", f"{message} — {outcome}", outcome=outcome, xp=self.xp, kamas=self.kamas)

    def _emit(self, event: str, message: str, **context: object) -> None:
        self.last_action = message
        self.history.append(message)
        self.history = self.history[-100:]
        self._events.append(CombatEvent("INFO", event, message, context))
