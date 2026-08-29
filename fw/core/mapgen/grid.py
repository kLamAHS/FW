"""The lattice the map is computed on, and the sweeps that answer questions about it.

Every field the generator builds — height, rainfall, drainage, travel cost — lives on
one square lattice, so this owns the arithmetic that turns a cell into a place and back,
and the three whole-lattice operations everything else is built from.

The important one is `distance_from`. The obvious way to ask "how far is this cell from
the nearest region border" is to loop over every border at every cell, which is
O(cells x regions): fine for five regions and ruinous for two hundred. Two sweeps answer
it for every cell at once, and the cost stops depending on how much world the writer has
built.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

INFINITY = float("inf")
_DIAGONAL = math.sqrt(2.0)

Field = list[list[float]]
Cell = tuple[int, int]


@dataclass(frozen=True)
class Grid:
    """A square lattice over a square of world.

    `size` is cells per side and `span` the world units they cover, so a cell is
    `span / size` units across. Fictional worlds have no coordinate system (§34), so
    these are the same arbitrary units the writer drew in.
    """

    size: int
    span: float
    origin_x: float = 0.0
    origin_y: float = 0.0

    @property
    def cell(self) -> float:
        return self.span / self.size

    def centre(self, i: int, j: int) -> tuple[float, float]:
        """The world point at the middle of a cell."""
        return (self.origin_x + (i + 0.5) * self.cell,
                self.origin_y + (j + 0.5) * self.cell)

    def cell_of(self, x: float, y: float) -> Cell:
        """The cell a world point falls in, clamped to the lattice."""
        i = int((x - self.origin_x) / self.cell)
        j = int((y - self.origin_y) / self.cell)
        return (max(0, min(self.size - 1, i)), max(0, min(self.size - 1, j)))

    def to_world(self, points: Iterable[tuple[float, float]]) -> list[list[float]]:
        """Lattice coordinates — including the fractional ones a contour returns."""
        return [[self.origin_x + x * self.cell, self.origin_y + y * self.cell]
                for x, y in points]

    def holds(self, i: int, j: int) -> bool:
        return 0 <= i < self.size and 0 <= j < self.size

    def cells(self) -> Iterator[Cell]:
        for j in range(self.size):
            for i in range(self.size):
                yield i, j

    def neighbours(self, i: int, j: int, *, diagonal: bool = True) -> Iterator[Cell]:
        steps = ((1, 0), (-1, 0), (0, 1), (0, -1))
        if diagonal:
            steps += ((1, 1), (1, -1), (-1, 1), (-1, -1))
        for di, dj in steps:
            ni, nj = i + di, j + dj
            if self.holds(ni, nj):
                yield ni, nj

    def filled(self, value: float = 0.0) -> Field:
        return [[value] * self.size for _ in range(self.size)]

    # ---- whole-lattice sweeps ---------------------------------------------

    def distance_from(self, sources: Iterable[Cell]) -> Field:
        """Distance in cells from the nearest source, by two chamfer sweeps.

        Forward then backward, taking orthogonal steps at 1 and diagonal at root two.
        The answer is within a few percent of true Euclidean distance, which is far
        inside the tolerance of anything that reads it, and it costs two passes rather
        than a search.
        """
        size = self.size
        grid = [[INFINITY] * size for _ in range(size)]
        for i, j in sources:
            if self.holds(i, j):
                grid[j][i] = 0.0

        for j in range(size):
            row, above = grid[j], grid[j - 1] if j else None
            for i in range(size):
                best = row[i]
                if i and row[i - 1] + 1.0 < best:
                    best = row[i - 1] + 1.0
                if above is not None:
                    if above[i] + 1.0 < best:
                        best = above[i] + 1.0
                    if i and above[i - 1] + _DIAGONAL < best:
                        best = above[i - 1] + _DIAGONAL
                    if i + 1 < size and above[i + 1] + _DIAGONAL < best:
                        best = above[i + 1] + _DIAGONAL
                row[i] = best

        for j in range(size - 1, -1, -1):
            row, below = grid[j], grid[j + 1] if j + 1 < size else None
            for i in range(size - 1, -1, -1):
                best = row[i]
                if i + 1 < size and row[i + 1] + 1.0 < best:
                    best = row[i + 1] + 1.0
                if below is not None:
                    if below[i] + 1.0 < best:
                        best = below[i] + 1.0
                    if i + 1 < size and below[i + 1] + _DIAGONAL < best:
                        best = below[i + 1] + _DIAGONAL
                    if i and below[i - 1] + _DIAGONAL < best:
                        best = below[i - 1] + _DIAGONAL
                row[i] = best
        return grid

    def claimed_by(self, seeds: Iterable[tuple[Cell, float]], *,
                   passable=None) -> list[list[int]]:
        """Which seed reaches each cell first — a partition, by one flood.

        Seeds are ((i, j), rate): a bigger rate spreads faster, so a region the writer
        gave a large population claims more ground than a small one, without anyone
        having to compute a weighted Voronoi diagram.

        Ties break on the seed's index, so the same seeds always produce the same
        partition — a flood that broke ties on heap order would redraw every border
        whenever an unrelated region was added.
        """
        owner = [[-1] * self.size for _ in range(self.size)]
        heap: list[tuple[float, int, int, int]] = []
        rates: list[float] = []
        for index, ((i, j), rate) in enumerate(seeds):
            rates.append(max(rate, 1e-6))
            if self.holds(i, j):
                heapq.heappush(heap, (0.0, index, i, j))
        while heap:
            cost, index, i, j = heapq.heappop(heap)
            if owner[j][i] != -1:
                continue
            owner[j][i] = index
            step = 1.0 / rates[index]
            for ni, nj in self.neighbours(i, j, diagonal=False):
                if owner[nj][ni] == -1 and (passable is None or passable(ni, nj)):
                    heapq.heappush(heap, (cost + step, index, ni, nj))
        return owner

    def eased_across(self, field: Field, owner: list[list[int]],
                     sea: list[list[bool]], *, reach: float = 9.0,
                     rounds: int = 6) -> Field:
        """Smooth a per-region field at its borders without flattening its interior.

        Anything derived per region — how high the ground is, how wet it is — steps at
        every border, and the eye finds a straight edge in a landscape instantly. It is
        the surest giveaway of a generated map.

        Blurring it away is not the answer: enough blur to hide the seam also halves
        the difference between a mountain march and a river plain, which is the one
        thing the field was supposed to say. So the blur is applied only near the
        borders and each region keeps its own character further in — a smooth crossing,
        and nothing flattened behind it.
        """
        smoothed = self.blurred(field, rounds=rounds)
        inland = self.distance_from(self._frontier(owner, sea))
        out = [row[:] for row in field]
        for j in range(self.size):
            for i in range(self.size):
                if sea[j][i]:
                    continue
                t = min(1.0, inland[j][i] / reach) if reach > 0 else 1.0
                eased = t * t * (3.0 - 2.0 * t)
                out[j][i] = smoothed[j][i] + eased * (field[j][i] - smoothed[j][i])
        return self.blurred(out, rounds=1)

    def _frontier(self, owner: list[list[int]],
                  sea: list[list[bool]]) -> list[Cell]:
        """Land cells that look across at a different region."""
        out: list[Cell] = []
        for j in range(self.size):
            for i in range(self.size):
                if sea[j][i]:
                    continue
                mine = owner[j][i]
                for ni, nj in self.neighbours(i, j, diagonal=False):
                    if not sea[nj][ni] and owner[nj][ni] != mine:
                        out.append((i, j))
                        break
        return out

    def blurred(self, field: Field, rounds: int = 1) -> Field:
        """A light box pass.

        Contouring a raw fractal field threads single-cell channels and grows hairs off
        the coast. One blur costs almost nothing and removes them without touching
        anything the eye reads as a bay.
        """
        for _ in range(rounds):
            out = [row[:] for row in field]
            for j in range(1, self.size - 1):
                previous, row, following = field[j - 1], field[j], field[j + 1]
                for i in range(1, self.size - 1):
                    out[j][i] = (
                        row[i] * 4.0
                        + row[i - 1] + row[i + 1] + previous[i] + following[i]
                        + (previous[i - 1] + previous[i + 1]
                           + following[i - 1] + following[i + 1]) * 0.5
                    ) / 12.0
            field = out
        return field


def disc(centre: tuple[float, float], radius: float) -> Iterator[Cell]:
    """Every cell inside a radius — how a region's heart is seeded."""
    cx, cy = centre
    reach = int(radius) + 1
    for dj in range(-reach, reach + 1):
        for di in range(-reach, reach + 1):
            if di * di + dj * dj <= radius * radius:
                yield int(cx) + di, int(cy) + dj


def line(start: tuple[float, float], end: tuple[float, float]) -> Iterator[Cell]:
    """Every cell along a straight run — how a border corridor is seeded."""
    ax, ay = start
    bx, by = end
    steps = max(1, int(round(max(abs(bx - ax), abs(by - ay)))))
    previous: Cell | None = None
    for k in range(steps + 1):
        t = k / steps
        cell = (int(round(ax + (bx - ax) * t)), int(round(ay + (by - ay) * t)))
        if cell != previous:
            yield cell
            previous = cell
