"""Dividing the land between the regions, and drawing the borders that result.

Once the continent exists, every acre of it belongs to somebody. That is not a detail:
a map with unclaimed gaps between its regions renders as grout on a political fill, and
the writer reads it as land nobody has thought about rather than as an artefact.

So the partition is total — every land cell has an owner — and the borders are *traced*
from it rather than drawn independently. That is what makes a shared border shared: the
line between two regions is one line, computed once, and both neighbours get the same
coordinates. Two independently-cast outlines never quite agree, which is the other
reason the old map had water between regions that touch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.mapgen import shapes
from fw.core.mapgen.grid import Grid

# A region drawn from fewer cells than this is not a shape, it is a speck.
SMALLEST_REGION = 6.0
MAX_RING_VERTICES = 240


@dataclass
class Partition:
    """Who owns each cell of the land."""

    grid: Grid
    owner: list[list[int]]                    # index into `keys`; -1 is sea
    keys: tuple[str, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def region_at(self, i: int, j: int) -> str | None:
        index = self.owner[j][i]
        return self.keys[index] if 0 <= index < len(self.keys) else None

    def cells_of(self, key: str) -> list[tuple[int, int]]:
        if key not in self.keys:
            return []
        index = self.keys.index(key)
        return [(i, j) for j in range(self.grid.size) for i in range(self.grid.size)
                if self.owner[j][i] == index]

    def share(self, key: str) -> float:
        total = sum(self.counts.values()) or 1
        return round(self.counts.get(key, 0) / total, 3)


def grow(grid: Grid, sea: list[list[bool]], *, anchors: dict[str, tuple[int, int]],
         weights: dict[str, float],
         claimed: dict[str, set[tuple[int, int]]] | None = None) -> Partition:
    """Give every land cell to a region.

    Regions spread from their hearts at a rate set by how much of the world they hold,
    so a kingdom of four hundred thousand takes more ground than a mountain march of
    forty — and the border falls where the two meet, which is what a border is.

    Cells inside a shape the writer drew themselves are given to that region outright:
    their drawing is not a suggestion.
    """
    keys = tuple(sorted(anchors))
    if not keys:
        return Partition(grid=grid, owner=[[-1] * grid.size for _ in range(grid.size)])

    index_of = {key: n for n, key in enumerate(keys)}
    owner = grid.claimed_by(
        [(anchors[key], max(weights.get(key, 1.0), 0.2)) for key in keys],
        passable=lambda i, j: not sea[j][i],
    )

    for j in range(grid.size):
        for i in range(grid.size):
            if sea[j][i]:
                owner[j][i] = -1

    for key in sorted(claimed or {}):
        for i, j in sorted(claimed[key]):
            if grid.holds(i, j) and not sea[j][i]:
                owner[j][i] = index_of[key]

    stranded = _adopt_the_rest(grid, sea, owner)

    counts: dict[str, int] = {}
    for row in owner:
        for value in row:
            if value >= 0:
                counts[keys[value]] = counts.get(keys[value], 0) + 1
    notes = []
    if stranded:
        notes.append(f"{stranded} cells of land lie off the main body and were given "
                     f"to the nearest coast")
    return Partition(grid=grid, owner=owner, keys=keys, counts=counts, notes=notes)


def _adopt_the_rest(grid: Grid, sea: list[list[bool]],
                    owner: list[list[int]]) -> int:
    """Give the nearest region any land the flood could not reach.

    An island off a coast, or a spit joined to the mainland only at a corner. Leaving it
    unowned would be a third state for a cell to be in, and every reader of the map
    would have to handle it: on a political fill it renders as a hole. Whose island it
    is, is a question the writer can answer later; that it is *somebody's* is the safe
    assumption, and a real map makes it too.
    """
    frontier = [(i, j) for j in range(grid.size) for i in range(grid.size)
                if owner[j][i] >= 0]
    stranded = sum(1 for j in range(grid.size) for i in range(grid.size)
                   if not sea[j][i] and owner[j][i] < 0)
    if not frontier or not stranded:
        return 0
    while frontier:
        nxt: list[tuple[int, int]] = []
        for i, j in frontier:
            mine = owner[j][i]
            for ni, nj in grid.neighbours(i, j):
                if not sea[nj][ni] and owner[nj][ni] < 0:
                    owner[nj][ni] = mine
                    nxt.append((ni, nj))
        frontier = sorted(nxt)

    # An island the walk could not reach, because reaching it means crossing water.
    # A second flood that ignores the shoreline answers "whose coast is it off", which
    # is how an island gets claimed in the world as well as on the map.
    remaining = [(i, j) for j in range(grid.size) for i in range(grid.size)
                 if not sea[j][i] and owner[j][i] < 0]
    if remaining:
        reach = {(i, j): owner[j][i] for j in range(grid.size)
                 for i in range(grid.size) if owner[j][i] >= 0}
        wave = sorted(reach)
        while wave and any(owner[j][i] < 0 for i, j in remaining):
            nxt = []
            for i, j in wave:
                mine = reach[(i, j)]
                for neighbour in grid.neighbours(i, j):
                    if neighbour in reach:
                        continue
                    reach[neighbour] = mine
                    ni, nj = neighbour
                    if not sea[nj][ni]:
                        owner[nj][ni] = mine
                    nxt.append(neighbour)
            wave = sorted(nxt)
    return stranded


def outline(partition: Partition, key: str) -> list[list[list[float]]]:
    """A region's shape, traced from the ground it actually holds.

    Contoured from the ownership field rather than cast as rays, so the shape can be as
    concave as the territory is — a region that wraps around a bay looks like one — and
    so the edge it shares with a neighbour is the same edge on both maps.
    """
    if key not in partition.keys:
        return []
    grid = partition.grid
    index = partition.keys.index(key)
    mask = grid.filled(0.0)
    for j in range(grid.size):
        for i in range(grid.size):
            if partition.owner[j][i] == index:
                mask[j][i] = 1.0

    rings: list[list[list[float]]] = []
    for ring, encloses in shapes.outlines(grid.blurred(mask), 0.5,
                                          smallest=SMALLEST_REGION,
                                          most=MAX_RING_VERTICES):
        if not encloses:
            continue                    # a hole in a region is another region, not a gap
        rings.append(shapes.closed(grid.to_world(ring)))
    return rings


def audit(partition: Partition, borders: set[tuple[str, str]]) -> list[str]:
    """Which of the writer's stated borders the map failed to realise.

    Four regions can all claim to border each other, and a plane cannot always oblige.
    Saying which border was lost is more use than either failing or pretending.
    """
    touching: set[tuple[str, str]] = set()
    grid = partition.grid
    for j in range(grid.size):
        for i in range(grid.size):
            mine = partition.owner[j][i]
            if mine < 0:
                continue
            for ni, nj in grid.neighbours(i, j, diagonal=False):
                theirs = partition.owner[nj][ni]
                if theirs >= 0 and theirs != mine:
                    a, b = partition.keys[mine], partition.keys[theirs]
                    touching.add((min(a, b), max(a, b)))
    stated = {(min(a, b), max(a, b)) for a, b in borders
              if a in partition.keys and b in partition.keys and a != b}
    return sorted(f"{a} and {b}" for a, b in stated - touching)
