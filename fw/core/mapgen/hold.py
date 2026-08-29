"""Castles: where holding a piece of ground holds a great deal more than the ground.

A castle is not a large house and it is not where a lord happened to want to live. It is
a machine for controlling movement, and it is expensive enough that nobody builds one
except where movement is worth controlling. So this stage does not ask where the finest
view is. It asks what there is on this map that an army has to come *through* — a pass,
a ford, the neck of a harbour, the one road across a march — and then which of those
places has ground a garrison can hold.

Everything it needs was worked out by the stages before it and none of it is new
geography. The crossings come from `movement`, which found them by looking at what the
water and the height actually do. The traffic comes from `roads`, which knows how much of
the kingdom's carting goes over each stretch because it laid the network. The frontiers
come from `territory`, which knows where one country stops. A castle is the place where
those three answers agree, which is why the important ones fall out of the arithmetic
rather than having to be reasoned about: the pass on the border that the highway climbs
is *already* the highest-scoring cell on the map before anybody has said the word castle.

What this deliberately does not do is put a castle beside every town. A town's walls are
the town's business. A castle stands where the country needs watching, and a fair number
of them stand nowhere near anybody.
"""

from __future__ import annotations

from dataclasses import dataclass

from fw.core.mapgen import noise
from fw.core.mapgen.grid import Field, Grid, stands_above
from fw.core.mapgen.movement import Movement, nearest_crossing

# What a castle is for, in the order it is worth building one.
#
# A crossing is the whole argument: it is a place with no way round, so a garrison there
# is worth ten anywhere else. Traffic says how much is lost when it closes. A frontier
# says whether the thing on the other side is a neighbour or a rival. And a seat is worth
# something because a lord has to sleep somewhere — but only something, or every castle
# ends up in a town and the passes go unwatched, which is what a castle is *for*.
WEIGHT_CROSSING = {"pass": 3.4, "ford": 2.6, "harbour": 2.2}
WEIGHT_TRAFFIC = 2.4
WEIGHT_FRONTIER = 2.0
WEIGHT_SEAT = 1.0

# How far from a thing still counts as commanding it, in cells. A keep above a pass is
# not standing in the pass; it is looking down into it, which is the point.
CROSSING_REACH = 2.5
FRONTIER_REACH = 5.0
SEAT_REACH = 6.0

# Ground a garrison cannot hold, and ground nobody would build on.
MARSH_REFUSAL = 3.0
STEEP_REFUSAL = 1.6              # a crag is not a castle site; a spur above one is
STEEP_FROM = 0.20

# What counts as commanding ground, as a quantile of this world's own relief. Absolute
# figures do not survive a change of continent — see `settle.COMMANDING_QUANTILE`, which
# learned it the expensive way.
COMMANDING_QUANTILE = 0.72
WEIGHT_HEIGHT = 1.8

# Which of the things a hold can watch are worth a fortress rather than a garrison.
HELD_AGAINST_AN_ARMY = ("pass", "ford", "harbour")

# The ranks are not a threshold on the score, and two attempts at making them one are
# why. Against absolute figures the example kingdom came out nine castles and no keeps
# while the next world over came out one castle and eleven keeps — which says nothing
# about either kingdom and everything about a number fitted to neither. Against a share
# of the best hold on the same map, a world whose scores happen to cluster comes out
# eleven castles and one keep, for the same reason in a different disguise.
#
# So the rank says what the place is *for*, which is a fact about it and not about the
# distribution it landed in. A castle stands over something an army has no way round. A
# keep watches a road, or a lesser crossing. A tower is a pair of eyes on an empty march,
# and there is no shame in it: most of the border is empty march.
#
# Unlike settlements these are not nested — a tower is not a small castle serving a keep
# the way a village serves a town — so there is one spacing for all three.
RANKS = ("castle", "keep", "tower")

# Nothing below this is worth cutting stone for, so a placid world gets no castles rather
# than a ring of follies.
WORTH_HOLDING = 2.6
SPACING = 7

# How many the map will offer, per region, and the ceiling whatever the world is like.
PER_REGION = 3
CEILING = 24


@dataclass(frozen=True)
class Hold:
    """One castle, and what it is there to watch."""

    cell: tuple[int, int]
    rank: str
    score: float
    watches: str                          # pass | ford | harbour | road | march | seat
    reasons: tuple[str, ...] = ()
    entity_id: str | None = None          # if the writer already has one here

    @property
    def invented(self) -> bool:
        return self.entity_id is None


@dataclass
class Holds:
    """The castles, and the field that chose them."""

    sites: tuple[Hold, ...]
    worth: Field                          # per cell, what holding it would be worth

    def of_rank(self, rank: str) -> tuple[Hold, ...]:
        return tuple(h for h in self.sites if h.rank == rank)


def plan_holds(grid: Grid, *, movement: Movement, traffic: Field,
               frontier_cells: set[tuple[int, int]], seats: list[tuple[int, int]],
               elevation: Field, slope: Field, marsh: Field,
               sea: list[list[bool]], seed: str, wanted: int,
               fixed: dict[tuple[int, int], str] | None = None,
               room: dict[str, int] | None = None,
               region_of: list[list[str]] | None = None) -> Holds:
    """Find the places worth fortifying, and say what each one is for."""
    size = grid.size
    land = [(i, j) for j in range(size) for i in range(size) if not sea[j][i]]
    if not land:
        return Holds(sites=(), worth=grid.filled(0.0))

    worth, watches, why = _worth(
        grid, movement=movement, traffic=traffic, frontier_cells=frontier_cells,
        seats=seats, elevation=elevation, slope=slope, marsh=marsh, sea=sea,
        land=land, seed=seed)
    chosen = _choose(grid, worth=worth, land=land, wanted=wanted,
                     fixed=fixed or {}, room=room, region_of=region_of)

    scores = sorted(worth[j][i] for (i, j), _ in chosen)
    middling = scores[len(scores) // 2] if scores else 0.0
    sites = [Hold(cell=cell,
                  rank=_rank(watches.get(cell, "march"),
                             worth[cell[1]][cell[0]], middling),
                  score=round(worth[cell[1]][cell[0]], 3),
                  watches=watches.get(cell, "march"),
                  reasons=tuple(why.get(cell, ())), entity_id=entity_id)
             for cell, entity_id in chosen]
    order = {name: n for n, name in enumerate(RANKS)}
    sites.sort(key=lambda h: (order.get(h.rank, 99), -h.score, h.cell))
    return Holds(sites=tuple(sites), worth=worth)


def _worth(grid: Grid, *, movement: Movement, traffic: Field,
           frontier_cells: set[tuple[int, int]], seats: list[tuple[int, int]],
           elevation: Field, slope: Field, marsh: Field, sea: list[list[bool]],
           land: list[tuple[int, int]], seed: str
           ) -> tuple[Field, dict[tuple[int, int], str], dict[tuple[int, int], list[str]]]:
    """What holding each acre would be worth, and the sentence that says why."""
    busiest = max(traffic[j][i] for i, j in land) or 1.0
    reach, kind_at, strength_at = nearest_crossing(grid, movement.crossings())

    to_frontier = grid.distance_from(sorted(frontier_cells))
    to_seat = grid.distance_from(sorted(seats))

    rises = sorted(stands_above(grid, elevation, sea, i, j) for i, j in land)
    commanding = rises[min(len(rises) - 1, int(len(rises) * COMMANDING_QUANTILE))] or 1e-6

    worth = grid.filled(0.0)
    watches: dict[tuple[int, int], str] = {}
    why: dict[tuple[int, int], list[str]] = {}
    for i, j in land:
        because: list[str] = []
        value = 0.0
        holds = "march"

        gap, kind = reach[j][i], kind_at[j][i]
        if kind and gap <= CROSSING_REACH:
            close = 1.0 - gap / (CROSSING_REACH + 1.0)
            value += (WEIGHT_CROSSING.get(kind, 1.5) * close
                      * (0.4 + 0.6 * strength_at[j][i]))
            holds = kind
            because.append(_CROSSING_WHY[kind])

        carried = traffic[j][i] / busiest
        if carried > 0.02:
            value += WEIGHT_TRAFFIC * carried
            if holds == "march":
                holds = "road"
            because.append("astride the road")

        edge = to_frontier[j][i]
        if edge <= FRONTIER_REACH:
            value += WEIGHT_FRONTIER * (1.0 - edge / (FRONTIER_REACH + 1.0))
            because.append("on the march itself")

        near = to_seat[j][i]
        if near <= SEAT_REACH:
            # Beside a town, not in it: a castle sharing a cell with a market is a
            # gatehouse, and the map already drew the market.
            value += WEIGHT_SEAT * (1.0 - abs(near - 2.0) / (SEAT_REACH + 1.0))
            if holds == "march":
                holds = "seat"
            because.append("within sight of the town it answers for")

        standing = stands_above(grid, elevation, sea, i, j)
        value += WEIGHT_HEIGHT * min(1.5, standing / commanding)
        if standing >= commanding:
            because.append("on ground a garrison can hold")

        value -= MARSH_REFUSAL * marsh[j][i]
        value -= STEEP_REFUSAL * max(0.0, slope[j][i] - STEEP_FROM) / STEEP_FROM

        # A stable nudge, so equal ground does not tie forever.
        value += noise.unit(f"{seed}|hold", i, j) * 0.15
        worth[j][i] = value
        watches[(i, j)] = holds
        if because:
            why[(i, j)] = because
    return worth, watches, why


def _choose(grid: Grid, *, worth: Field, land: list[tuple[int, int]], wanted: int,
            fixed: dict[tuple[int, int], str],
            room: dict[str, int] | None,
            region_of: list[list[str]] | None
            ) -> list[tuple[tuple[int, int], str | None]]:
    """The best places to hold, spaced out, with the writer's own kept whatever they cost.

    No tiers to walk down, unlike settlements: there is no sense in which a tower serves
    a keep the way a village serves a town, so one pass in order of what a place is worth
    is the whole selection.

    But the same per-region room, and for the same reason. Passes are the most valuable
    thing on this list and they are all in the mountains, so a single ranking gave the
    example kingdom nine castles of which seven stood in the one mountain march and every
    last one of them was watching a pass — while the marches with the fords, the harbours
    and the open border got nothing at all. A writer asking what defends their coast
    deserves an answer about their coast.
    """
    out: list[tuple[tuple[int, int], str | None]] = []
    taken: list[tuple[int, int]] = []
    left = dict(room) if room else None

    def belongs(cell: tuple[int, int]) -> str | None:
        if region_of is None:
            return None
        return region_of[cell[1]][cell[0]] or None

    for cell in sorted(fixed):
        out.append((cell, fixed[cell]))
        taken.append(cell)
        where = belongs(cell)
        if left is not None and where in left:
            left[where] -= 1

    ranked = sorted(land, key=lambda c: (-worth[c[1]][c[0]], c[1], c[0]))
    for i, j in ranked:
        if len(out) >= wanted:
            break
        if worth[j][i] < WORTH_HOLDING:
            break                          # ranked, so nothing after this is worth it
        if (i, j) in fixed:
            continue
        if any(max(abs(i - a), abs(j - b)) < SPACING for a, b in taken):
            continue
        where = belongs((i, j))
        if left is not None and where is not None and left.get(where, 0) <= 0:
            continue
        out.append(((i, j), None))
        taken.append((i, j))
        if left is not None and where in left:
            left[where] -= 1
    return out


def _rank(watches: str, worth: float, middling: float) -> str:
    """What to call a hold: from what it stands over, and how much of it there is."""
    if watches in HELD_AGAINST_AN_ARMY:
        return "castle" if worth >= middling else "keep"
    return "keep" if watches == "road" else "tower"


_CROSSING_WHY = {
    "pass": "commanding the pass",
    "ford": "holding the crossing",
    "harbour": "over the anchorage",
}
