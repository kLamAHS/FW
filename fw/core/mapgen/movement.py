"""How hard the ground is to cross, and the places where crossing it is easy.

This is the first stage that is about people rather than about rock and water, and it
comes before settlements for the same reason erosion comes before rivers: it is what
decides them. A town is not usually where the ground is nicest. It is where a road has
to go — at the ford, below the pass, at the head of the navigable water — because that
is where travellers stop and where the tolls are.

The cost of a cell used to be looked up from the terrain of whichever region owned it,
which is the same politics-first mistake the elevation field made: a marsh cost what its
province cost on average, so a road would cheerfully cross one and refuse a dry hillside
next door. Here every term comes from the physical model — how steep the ground is, what
is growing on it, how much water is standing in it, how big the river is.

Three kinds of place fall out of that and are worth naming, because each of them is a
reason for a town:

  fords    — where a river can be crossed on foot. Not anywhere: a river is a barrier,
             and the few places it is not are the places roads converge on;
  passes   — the low saddles on a drainage divide. A divide is a wall to a traveller,
             and a pass is the door in it. Found from the basins erosion already worked
             out rather than from anything new;
  harbours — sheltered water beside land gentle enough to build on. Shelter is the whole
             point: an exposed beach is not a harbour however deep the water is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from fw.core.mapgen.grid import Field, Grid

# What a cell costs to cross before anything is done to it: one day's walk on level dry
# open ground. Everything else is a multiple of this.
LEVEL = 1.0

# Slope. Walking cost rises steeply — a gradient a road will not take at all is not much
# steeper than one it takes slowly, which is why roads switchback.
SLOPE_BITE = 26.0
SLOPE_CEILING = 9.0

# Cover. A wood is slower than a meadow and a marsh is very much slower than either.
FOREST_DRAG = 1.5             # at a closed canopy
MARSH_DRAG = 5.0              # at standing water

# Height, for the cold and thin air above the treeline rather than for the climb, which
# the slope term already has.
HIGH_DRAG = 1.6
HIGH_FROM = 0.55

# Water. A river is a barrier whose cost grows with what it carries, and the sea is not
# crossable on foot at all.
RIVER_TOLL = 14.0             # at the largest river on the map
# A ford is only interesting on a river that is a barrier without one. Below the first
# figure the water is a brook people cross wherever they like; above the second it wants
# a bridge or a boat whatever the bed is doing.
FORD_MIN = 0.07
FORD_FLOW = 0.32
FORD_SLOPE = 0.05             # and only where its bed is gentle enough to stand in

# What counts as a watershed worth calling one. Every cell that reaches the sea by its
# own short valley is technically a basin of its own — the example continent has seven
# hundred and seventy-seven — and if all of them count then every field boundary is a
# divide and a "pass" means nothing. Twenty-two of those seven hundred hold two thirds
# of the land between them, and those are the ones a traveller plans around.
BASIN_SHARE = 0.01
MINOR = -2                    # a cell draining by some rivulet of its own

# A pass has to be a real dip in a divide, not the lowest cell of a flat one.
PASS_DROP = 0.035             # how far below the divide around it a saddle must sit
PASS_SPACING = 8              # and how far apart two passes must be, in cells
FORD_SPACING = 7
HARBOUR_SPACING = 6

# Nothing is reported from the outermost cells. The land is suppressed towards the rim
# so the continent does not read as a crop, and the artefacts of that suppression are
# not geography: the first attempt put a third of its mountain passes on the map's edge.
RIM = 4

# A harbour wants water in front of it and land behind it, and shelter from both.
HARBOUR_SHELTER = 0.55        # share of the surrounding compass that must be land
HARBOUR_SLOPE = 0.09          # the shore itself has to be gentle enough to build on
HARBOUR_DEPTH = 0.02          # and the water deep enough to float something


@dataclass(frozen=True)
class Crossing:
    """A place the ground lets people through, and why it does."""

    cell: tuple[int, int]
    kind: str                    # ford | pass | harbour
    because: str
    strength: float = 1.0        # how good a one it is, 0 to 1


@dataclass
class Movement:
    """What it costs to go anywhere, and the places worth going through."""

    cost: Field                       # per cell, in level-going days
    basin: list[list[int]]            # which outlet a cell drains to; -1 at sea
    fords: tuple[Crossing, ...] = ()
    passes: tuple[Crossing, ...] = ()
    harbours: tuple[Crossing, ...] = ()

    def crossings(self) -> tuple[Crossing, ...]:
        return self.fords + self.passes + self.harbours

    def at(self, i: int, j: int) -> float:
        return self.cost[j][i]


def plan_movement(grid: Grid, *, elevation: Field, slope: Field, flow: Field,
                  canopy: Field, marsh: Field, downstream: list[list[int]],
                  sea: list[list[bool]], sea_level: float = 0.0) -> Movement:
    """Work out the cost of the ground, then find the places it opens up."""
    size = grid.size
    biggest = max((flow[j][i] for j in range(size) for i in range(size)
                   if not sea[j][i]), default=1.0) or 1.0

    cost = _cost_field(grid, elevation=elevation, slope=slope, flow=flow,
                       canopy=canopy, marsh=marsh, sea=sea, biggest=biggest,
                       sea_level=sea_level)
    basin = _basins(grid, elevation=elevation, downstream=downstream, sea=sea)
    return Movement(
        cost=cost,
        basin=basin,
        fords=_fords(grid, flow=flow, slope=slope, sea=sea, biggest=biggest),
        passes=_passes(grid, elevation=elevation, basin=basin, sea=sea),
        harbours=_harbours(grid, elevation=elevation, slope=slope, sea=sea,
                           sea_level=sea_level),
    )


def _cost_field(grid: Grid, *, elevation: Field, slope: Field, flow: Field,
                canopy: Field, marsh: Field, sea: list[list[bool]], biggest: float,
                sea_level: float) -> Field:
    """A day's travel, per cell, from the ground and what is on it."""
    size = grid.size
    out = grid.filled(LEVEL)
    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                out[j][i] = math.inf
                continue
            climb = 1.0 + SLOPE_BITE * slope[j][i] * slope[j][i]
            if climb > SLOPE_CEILING:
                climb = SLOPE_CEILING
            price = LEVEL * climb
            price *= 1.0 + FOREST_DRAG * canopy[j][i]
            price *= 1.0 + MARSH_DRAG * marsh[j][i]
            above = elevation[j][i] - sea_level - HIGH_FROM
            if above > 0.0:
                price *= 1.0 + HIGH_DRAG * min(1.0, above / (1.0 - HIGH_FROM))
            # The river itself. Not a wall — people cross rivers — but the price of
            # doing it is what makes a ford somewhere rather than everywhere.
            carried = flow[j][i] / biggest
            if carried > 0.004:
                price += RIVER_TOLL * carried
            out[j][i] = price
    return out


def _basins(grid: Grid, *, elevation: Field, downstream: list[list[int]],
            sea: list[list[bool]]) -> list[list[int]]:
    """Which mouth each cell's water reaches, so a divide can be seen.

    Walked from the lowest ground up. A receiver is lower than the cell that drains into
    it — erosion guarantees that — so taking the land in order of height answers every
    receiver before any of its donors, and one pass does it. Relaxing until nothing
    changes would give the same answer for a number of passes proportional to the length
    of the longest river.

    Every land cell ends up labelled with the sea cell its water finally arrives at, and
    two cells with different labels are on opposite sides of a watershed — which, to
    somebody on foot, is a wall.
    """
    size = grid.size
    out = [[-1] * size for _ in range(size)]
    land = [(i, j) for j in range(size) for i in range(size) if not sea[j][i]]
    for i, j in sorted(land, key=lambda c: (elevation[c[1]][c[0]], c[1], c[0])):
        target = downstream[j][i]
        if target < 0:
            out[j][i] = j * size + i          # its own outlet: an endorheic basin
            continue
        ti, tj = target % size, target // size
        out[j][i] = target if sea[tj][ti] else out[tj][ti]
        if out[j][i] < 0:
            out[j][i] = j * size + i

    # And then only the ones big enough to be a watershed rather than a gutter.
    held: dict[int, int] = {}
    for i, j in land:
        held[out[j][i]] = held.get(out[j][i], 0) + 1
    floor = len(land) * BASIN_SHARE
    for i, j in land:
        if held[out[j][i]] < floor:
            out[j][i] = MINOR
    return out


def _fords(grid: Grid, *, flow: Field, slope: Field, sea: list[list[bool]],
           biggest: float) -> tuple[Crossing, ...]:
    """Where a river can be waded.

    A river is fordable where it is small enough and its bed gentle enough, and those
    two together are rarer than either alone — which is the point. A ford is a scarce
    thing, and being the only one for thirty miles is how a great many towns came to be
    where they are.
    """
    size = grid.size
    out: list[Crossing] = []
    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                continue
            if not _inland(grid, i, j):
                continue
            carried = flow[j][i] / biggest
            if carried < FORD_MIN or carried > FORD_FLOW:
                continue                       # a brook is not a ford; a torrent is not one either
            if slope[j][i] > FORD_SLOPE:
                continue
            out.append(Crossing(
                cell=(i, j), kind="ford",
                # The best ford is on the biggest river that can still be waded: that is
                # the one people will go out of their way for.
                strength=carried / FORD_FLOW,
                because="the river runs wide and shallow enough to wade here"))
    out.sort(key=lambda c: (-c.strength, c.cell))
    return tuple(_thinned(out, FORD_SPACING))


def _passes(grid: Grid, *, elevation: Field, basin: list[list[int]],
            sea: list[list[bool]]) -> tuple[Crossing, ...]:
    """The low saddles on a watershed.

    A divide is where two basins meet, and to a traveller it is a wall: cross it anywhere
    and you climb. A pass is where it dips. Found by walking the divide and keeping the
    cells that sit well below the divide around them, which is what a saddle is.
    """
    size = grid.size
    divide: list[tuple[int, int]] = []
    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                continue
            mine = basin[j][i]
            if mine == MINOR or not _inland(grid, i, j):
                continue
            for ni, nj in grid.neighbours(i, j, diagonal=False):
                if (not sea[nj][ni] and basin[nj][ni] != MINOR
                        and basin[nj][ni] != mine):
                    divide.append((i, j))
                    break
    if not divide:
        return ()

    on_divide = set(divide)
    out: list[Crossing] = []
    for i, j in divide:
        here = elevation[j][i]
        # How high the divide stands nearby. A saddle is low against its own ridge, not
        # against the map: a pass through a great range is still high ground.
        ridge = [elevation[b][a]
                 for a in range(max(0, i - 4), min(size, i + 5))
                 for b in range(max(0, j - 4), min(size, j + 5))
                 if (a, b) in on_divide]
        if len(ridge) < 4:
            continue
        crest = max(ridge)
        if crest - here < PASS_DROP:
            continue
        out.append(Crossing(
            cell=(i, j), kind="pass",
            strength=min(1.0, (crest - here) / (PASS_DROP * 4.0)),
            because="the watershed dips here, so the ground lets a road through"))
    out.sort(key=lambda c: (-c.strength, c.cell))
    return tuple(_thinned(out, PASS_SPACING))


def _harbours(grid: Grid, *, elevation: Field, slope: Field, sea: list[list[bool]],
              sea_level: float) -> tuple[Crossing, ...]:
    """Sheltered water with buildable land behind it.

    Shelter is what makes a harbour, and it is the one thing a coastline drawn as a
    smooth curve cannot offer: a straight beach with deep water off it is a bad harbour
    and a bay with a headland either side is a good one. Counting how much of the
    surrounding compass is land measures exactly that, and it only means anything now
    that the coast has bays in it.
    """
    size = grid.size
    out: list[Crossing] = []
    for j in range(size):
        for i in range(size):
            if sea[j][i] or slope[j][i] > HARBOUR_SLOPE or not _inland(grid, i, j):
                continue
            water = [(a, b) for a, b in grid.neighbours(i, j) if sea[b][a]]
            if not water:
                continue                       # not on the coast at all
            # How enclosed the water off this shore is, measured a little way out.
            around = 0
            land = 0
            for a in range(max(0, i - 3), min(size, i + 4)):
                for b in range(max(0, j - 3), min(size, j + 4)):
                    around += 1
                    if not sea[b][a]:
                        land += 1
            shelter = land / around if around else 0.0
            if shelter < HARBOUR_SHELTER:
                continue
            deep = max(sea_level - elevation[b][a] for a, b in water)
            if deep < HARBOUR_DEPTH:
                continue                       # a mud flat, not an anchorage
            out.append(Crossing(
                cell=(i, j), kind="harbour",
                strength=min(1.0, shelter),
                because="the water here is sheltered and the shore gentle enough to land on"))
    out.sort(key=lambda c: (-c.strength, c.cell))
    return tuple(_thinned(out, HARBOUR_SPACING))


def _inland(grid: Grid, i: int, j: int) -> bool:
    """Away from the rim, where the land is artificially suppressed."""
    return RIM <= i < grid.size - RIM and RIM <= j < grid.size - RIM


def _thinned(places: list[Crossing], spacing: int) -> list[Crossing]:
    """Keep the best of each cluster.

    Twenty adjacent cells of the same ford are one ford. Reported as twenty, every later
    stage that counts crossings — how defensible a place is, how many roads meet there —
    is counting the lattice rather than the world.
    """
    kept: list[Crossing] = []
    for place in places:
        if all(max(abs(place.cell[0] - other.cell[0]),
                   abs(place.cell[1] - other.cell[1])) >= spacing for other in kept):
            kept.append(place)
    return kept
