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
        """Lattice coordinates — including the fractional ones a contour returns.

        Half a cell out from the raw index, because a lattice coordinate names a
        *sample*, and a sample sits at its cell's middle: this is `centre` with the
        integers relaxed. Without the half the whole contoured half of the map —
        coastlines, islands, lake rings, region polygons, border arcs, the natural
        features — was drawn half a cell up and left of the ground it came from,
        while everything sited from a cell (rivers, roads, towns) was drawn on it.
        A river then crossed its own coastline before it reached the water, and the
        drawn shore sat off the lit one. Measured on the example world: the drawn
        coast straddles the rendered shore 58% of the time without the half and 75%
        with it.
        """
        half = self.cell * 0.5
        return [[self.origin_x + x * self.cell + half,
                 self.origin_y + y * self.cell + half]
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

    def nearest_from(self, sources: Iterable[tuple[Cell, float]]) -> tuple[Field, Field]:
        """Distance to the nearest source, and what that source was carrying.

        The same two chamfer sweeps as `distance_from`, but each cell also inherits a
        value from whichever source turned out to be nearest. That is what lets a whole
        mountain system — a dozen ridges, each with its own height varying along its
        length — be turned into a height field in one pass rather than one pass per
        ridge. Ask "how far to the nearest ridge, and how high was it there", and the
        answer to both arrives together.
        """
        size = self.size
        far = [[INFINITY] * size for _ in range(size)]
        carried = [[0.0] * size for _ in range(size)]
        for (i, j), value in sources:
            if self.holds(i, j) and value > carried[j][i]:
                far[j][i] = 0.0
                carried[j][i] = value

        for j in range(size):
            row, hold = far[j], carried[j]
            above, held = (far[j - 1], carried[j - 1]) if j else (None, None)
            for i in range(size):
                best, value = row[i], hold[i]
                if i and row[i - 1] + 1.0 < best:
                    best, value = row[i - 1] + 1.0, hold[i - 1]
                if above is not None:
                    if above[i] + 1.0 < best:
                        best, value = above[i] + 1.0, held[i]
                    if i and above[i - 1] + _DIAGONAL < best:
                        best, value = above[i - 1] + _DIAGONAL, held[i - 1]
                    if i + 1 < size and above[i + 1] + _DIAGONAL < best:
                        best, value = above[i + 1] + _DIAGONAL, held[i + 1]
                row[i], hold[i] = best, value

        for j in range(size - 1, -1, -1):
            row, hold = far[j], carried[j]
            below, held = ((far[j + 1], carried[j + 1]) if j + 1 < size
                           else (None, None))
            for i in range(size - 1, -1, -1):
                best, value = row[i], hold[i]
                if i + 1 < size and row[i + 1] + 1.0 < best:
                    best, value = row[i + 1] + 1.0, hold[i + 1]
                if below is not None:
                    if below[i] + 1.0 < best:
                        best, value = below[i] + 1.0, held[i]
                    if i + 1 < size and below[i + 1] + _DIAGONAL < best:
                        best, value = below[i + 1] + _DIAGONAL, held[i + 1]
                    if i and below[i - 1] + _DIAGONAL < best:
                        best, value = below[i - 1] + _DIAGONAL, held[i - 1]
                row[i], hold[i] = best, value
        return far, carried

    def claimed_by(self, seeds: Iterable[tuple[Cell, float]], *,
                   passable=None, cost: Field | None = None) -> list[list[int]]:
        """One seed per claimant. See `claimed_from` for several."""
        return self.claimed_from((((cell,), rate) for cell, rate in seeds),
                                 passable=passable, cost=cost)

    def claimed_from(self, groups: Iterable[tuple[Iterable[Cell], float]], *,
                     passable=None, cost: Field | None = None) -> list[list[int]]:
        """Which claimant reaches each cell first — a partition, by one flood.

        Each claimant is (cells, rate): it spreads outward from all of its cells at once,
        and a bigger rate spreads faster, so a region the writer gave a large population
        claims more ground than a small one without anyone having to compute a weighted
        Voronoi diagram.

        With a `cost` field the flood spreads over the *ground* rather than over the
        plane, so reach means what it means to somebody riding it: the land a claimant
        can actually get to. Where a road up a valley is worth ten miles of fen, the
        territory runs up the valley.

        What that does *not* do, though it is the first thing one expects of it, is put
        the border on the range. A barrier between two claimants adds the same crossing
        cost to both of them everywhere beyond it, so it slides the line where their
        costs are equal rather than catching it: measured on the example continent, cost
        weighting moved one per cent of the land and left the border sitting on ground
        marginally *easier* than its surroundings. Making a thin barrier expensive along
        every drainage divide did not help either, because at this lattice a divide is
        not a regional feature — every region spans a dozen catchments, so a border must
        cross divides wherever it runs. Where a border does follow the country, it is
        because of what it is grown *from*, not what it is grown *over*.

        Ties break on the claimant's index, so the same seeds always produce the same
        partition — a flood that broke ties on heap order would redraw every border
        whenever an unrelated region was added.
        """
        owner = [[-1] * self.size for _ in range(self.size)]
        heap: list[tuple[float, int, int, int]] = []
        rates: list[float] = []
        for index, (cells, rate) in enumerate(groups):
            rates.append(max(rate, 1e-6))
            for i, j in cells:
                if self.holds(i, j):
                    heapq.heappush(heap, (0.0, index, i, j))
        while heap:
            paid, index, i, j = heapq.heappop(heap)
            if owner[j][i] != -1:
                continue
            owner[j][i] = index
            rate = rates[index]
            for ni, nj in self.neighbours(i, j, diagonal=False):
                if owner[nj][ni] != -1 or (passable is not None
                                           and not passable(ni, nj)):
                    continue
                step = (cost[nj][ni] if cost is not None else 1.0) / rate
                if step == math.inf:
                    continue
                heapq.heappush(heap, (paid + step, index, ni, nj))
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

    def spread(self, field: Field, *, stride: int = 6, rounds: int = 8) -> Field:
        """Smooth a field over tens of cells, cheaply, leaving no edge anywhere.

        `eased_across` exists to hide a border while keeping a region's interior; this is
        the opposite instruction, and the two are needed for different things. When the
        writer says one region is high ground and its neighbour is a plain, the ground
        itself does not step at the line between them — it swells across it over a
        distance far larger than any border. Getting that with `blurred` would take
        dozens of passes over the whole lattice, which is seconds.

        So the field is averaged down to a coarse grid, smoothed there, and interpolated
        back. A field whose only remaining content is broad is exactly what a coarse grid
        can hold, so nothing is lost, and the cost falls by the square of the stride.
        """
        size = self.size
        span = (size + stride - 1) // stride
        coarse = [[0.0] * span for _ in range(span)]
        counts = [[0] * span for _ in range(span)]
        for j in range(size):
            cj = j // stride
            row, tally = coarse[cj], counts[cj]
            source = field[j]
            for i in range(size):
                ci = i // stride
                row[ci] += source[i]
                tally[ci] += 1
        for cj in range(span):
            for ci in range(span):
                if counts[cj][ci]:
                    coarse[cj][ci] /= counts[cj][ci]

        for _ in range(rounds):
            out = [row[:] for row in coarse]
            for cj in range(span):
                for ci in range(span):
                    total, weight = 0.0, 0.0
                    for dj in (-1, 0, 1):
                        nj = cj + dj
                        if not 0 <= nj < span:
                            continue
                        for di in (-1, 0, 1):
                            ni = ci + di
                            if 0 <= ni < span:
                                w = 4.0 if not (di or dj) else (1.0 if not (di and dj)
                                                                else 0.5)
                                total += coarse[nj][ni] * w
                                weight += w
                    out[cj][ci] = total / weight
            coarse = out

        out = self.filled()
        limit = span - 1.001
        for j in range(size):
            y = (j + 0.5) / stride - 0.5
            y = 0.0 if y < 0.0 else (limit if y > limit else y)
            cj = int(y)
            fy = y - cj
            near, far = coarse[cj], coarse[cj + 1]
            row = out[j]
            for i in range(size):
                x = (i + 0.5) / stride - 0.5
                x = 0.0 if x < 0.0 else (limit if x > limit else x)
                ci = int(x)
                fx = x - ci
                top = near[ci] + (near[ci + 1] - near[ci]) * fx
                low = far[ci] + (far[ci + 1] - far[ci]) * fx
                row[i] = top + (low - top) * fy
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


def sample(field: Field, x: float, y: float) -> float:
    """Read a lattice field at a continuous position, bilinearly.

    Fields that are computed coarsely and read at warped positions need this, and there
    is exactly one right way to write it, so it lives here rather than three times.
    """
    size = len(field)
    limit = size - 1.001
    if x < 0.0:
        x = 0.0
    elif x > limit:
        x = limit
    if y < 0.0:
        y = 0.0
    elif y > limit:
        y = limit
    i, j = int(x), int(y)
    fx, fy = x - i, y - j
    near, far = field[j], field[j + 1]
    top = near[i] + (near[i + 1] - near[i]) * fx
    low = far[i] + (far[i + 1] - far[i]) * fx
    return top + (low - top) * fy


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


def stands_above(grid: Grid, field: Field, sea: list[list[bool]], i: int, j: int, *,
                 reach: int = 3) -> float:
    """How far a cell rises over the lowest land within `reach` of it.

    Against the lowest rather than the mean, because every question this answers is about
    somebody climbing — a garrison to be reached, a town to be stormed, a border to be
    crossed — and they will come by the easiest way there is, not by the average one.

    One definition, because there were three: settlement, castles and borders all want
    this number and had each grown their own, with the arguments in a different order in
    every copy. Two of them also compared it against the same absolute constant, which
    turned out to be true of ninety per cent of a continent.
    """
    size = grid.size
    here = field[j][i]
    lowest = here
    for b in range(max(0, j - reach), min(size, j + reach + 1)):
        for a in range(max(0, i - reach), min(size, i + reach + 1)):
            if not sea[b][a] and field[b][a] < lowest:
                lowest = field[b][a]
    return here - lowest
