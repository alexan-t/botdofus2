"""Géométrie de grille, chemins et ligne de vue pour la simulation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from combatbot.models import Cell


@dataclass(frozen=True)
class Grid:
    width: int
    height: int
    obstacles: frozenset[Cell]

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("La grille doit avoir une taille positive")
        if any(not self.inside(cell) for cell in self.obstacles):
            raise ValueError("Un obstacle est hors de la grille")

    def inside(self, cell: Cell) -> bool:
        return 0 <= cell.x < self.width and 0 <= cell.y < self.height

    def neighbors(self, cell: Cell) -> tuple[Cell, ...]:
        candidates = (
            Cell(cell.x + 1, cell.y), Cell(cell.x - 1, cell.y),
            Cell(cell.x, cell.y + 1), Cell(cell.x, cell.y - 1),
        )
        return tuple(p for p in candidates if self.inside(p))

    def reachable(self, start: Cell, max_steps: int, occupied: set[Cell] | frozenset[Cell] = frozenset()) -> dict[Cell, int]:
        if not self.inside(start) or max_steps < 0:
            raise ValueError("Départ ou nombre de pas invalide")
        costs = {start: 0}
        queue = deque([start])
        blocked = self.obstacles | occupied
        while queue:
            current = queue.popleft()
            if costs[current] == max_steps:
                continue
            for neighbor in self.neighbors(current):
                if neighbor not in blocked and neighbor not in costs:
                    costs[neighbor] = costs[current] + 1
                    queue.append(neighbor)
        return costs

    def line_of_sight(self, start: Cell, end: Cell, occupied: set[Cell] | frozenset[Cell] = frozenset()) -> bool:
        """Bresenham; endpoints are allowed, intermediate blocked cells are not."""
        if not self.inside(start) or not self.inside(end):
            return False
        x, y = start.x, start.y
        dx, dy = abs(end.x - x), abs(end.y - y)
        sx = 1 if x < end.x else -1
        sy = 1 if y < end.y else -1
        error = dx - dy
        blocked = self.obstacles | occupied
        while (x, y) != (end.x, end.y):
            twice = 2 * error
            if twice > -dy:
                error -= dy
                x += sx
            if twice < dx:
                error += dx
                y += sy
            if (x, y) != (end.x, end.y) and Cell(x, y) in blocked:
                return False
        return True
