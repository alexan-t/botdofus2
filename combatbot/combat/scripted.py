"""Doublures déterministes pour rejouer la boucle d'action sans DOFUS (tests et auto-test offline).

- ``FakeClock`` : horloge + attente simulées (``sleep`` avance le temps) ;
- ``ScriptedObserver`` : rend une séquence écrite d'états (None = aucune frame) ; chaque frame est
  capturée après une latence simulée, donc toujours postérieure à l'action qui la précède ;
- ``FakeSender`` : exécuteur factice qui enregistre les actions, peut échouer ou déclencher un
  événement (F9, changement d'état) au moment de l'envoi. Aucune entrée n'est jamais envoyée.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from combatbot.combat.closed_loop import Observation
from combatbot.combat.mouse_executor import SendResult
from combatbot.combat.state import RealCombatState


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@dataclass(frozen=True)
class ScriptedFrame:
    """Frame avec identité de fenêtre / layout forcée (pour simuler un changement)."""
    state: RealCombatState
    hwnd: int | None = None
    layout_digest: str | None = None


class ScriptedObserver:
    def __init__(self, script: Iterable[RealCombatState | ScriptedFrame | None], *, clock: FakeClock, hwnd: int,
                 layout_digest: str, latency_s: float = 0.05) -> None:
        self.script = list(script)
        self.clock = clock
        self.hwnd = hwnd
        self.layout_digest = layout_digest
        self.latency_s = latency_s
        self.frame_id = 0
        self.calls = 0

    def push(self, *items: RealCombatState | ScriptedFrame | None) -> None:
        self.script.extend(items)

    def observe(self) -> Observation | None:
        self.calls += 1
        self.clock.sleep(self.latency_s)
        if not self.script:
            return None
        item = self.script.pop(0)
        if item is None:
            return None
        self.frame_id += 1
        if isinstance(item, ScriptedFrame):
            return Observation(item.state, self.clock(), self.frame_id,
                               item.hwnd if item.hwnd is not None else self.hwnd,
                               item.layout_digest if item.layout_digest is not None else self.layout_digest)
        return Observation(item, self.clock(), self.frame_id, self.hwnd, self.layout_digest)


@dataclass
class FakeSender:
    """``fail_on`` : indices d'envoi qui échouent ; ``on_send`` : rappel appelé à chaque envoi."""
    fail_on: frozenset[int] = frozenset()
    on_send: Callable[[int, object], None] | None = None
    sent: list[tuple[str, str]] = field(default_factory=list)
    attempts: int = 0

    def send(self, action, geometry, *, correlation_id: str) -> SendResult:
        index = self.attempts
        self.attempts += 1
        if self.on_send is not None:
            self.on_send(index, action)
        if index in self.fail_on:
            return SendResult(False, ("échec simulé de l'exécuteur",))
        self.sent.append((correlation_id, action.semantic_target))
        return SendResult(True, (), action.screen_point)
