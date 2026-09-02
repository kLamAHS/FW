"""The drainage network as a *system*, read off what erosion already worked out.

Erosion routes every cell's water and cuts valleys accordingly — and then the map
used to throw the network away and re-derive a cartoon of it: each traced source
walked to the sea as its own "river", so one real river system arrived as a sheaf of
overlapping strands, the `strahler` in its detail was a drawing width plus one, and a
depression was something the pit-filling existed to erase rather than a lake.

This module keeps the physics and adds the reading (V2 §6). From the receiver tree
and the flow field it builds the channel forest once, computes true Strahler order on
it, cuts it into arcs at the confluences, gathers arcs into river *systems* — a
mainstem and its significant tributaries, none sharing a cell — classifies every
mouth (delta, estuary, or plain), and reads the standing water out of the fill depth
the drain pass left behind.

Numbers here follow the art direction's restraint budgets (docs/art-direction.md):
at most three lakes, at most two deltas, tributaries drawn only from order two.
"""

from __future__ import annotations

from dataclasses import dataclass

from fw.core.mapgen.grid import Field

Cell = tuple[int, int]

# A channel becomes worth drawing as a tributary at this Strahler order. Order-one
# headwater brooks are relief, not features (the budget's own line).
TRIBUTARY_ORDER = 2

# Standing water. The erosion model *maintains* "everything drains" as an invariant
# — its pit-filling runs every round, so by the end there is no depression left to
# read water out of (measured: ~20 cells of residual fill on a whole continent).
# What the physics does leave is the water table: a lake here is the deep core of a
# broad marsh basin — flat, wet, undrained ground the vegetation stage already
# found — which is where a mere sits in real fen country too.
LAKE_MARSH = 0.22
LAKE_CELLS = 7
MOST_LAKES = 3

# Mouth classification. A delta wants heavy sediment arriving on a shallow shelf;
# an estuary is the opposite — the sea reaching into the valley.
MOST_DELTAS = 2
DELTA_SETTLED = 0.010            # sediment along the final reach
SHALLOW_SHARE = 0.5              # of SHELF_DEPTH: shallower than this is delta ground
ESTUARY_SHARE = 0.85             # deeper than this off the mouth reads as drowned


@dataclass(frozen=True)
class Arc:
    """One stretch of channel between confluences — the unit rivers are built from."""

    cells: tuple[Cell, ...]              # top (source or junction) to bottom
    order: int                           # Strahler, constant along an arc
    discharge: float                     # flow at the bottom cell


@dataclass(frozen=True)
class RiverSystem:
    """A mouth, its mainstem, and every tributary worth a line of its own."""

    mouth: Cell
    mouth_kind: str                      # "delta" | "estuary" | "mouth"
    mainstem: tuple[Cell, ...]           # source to mouth
    order: int                           # Strahler at the mouth
    discharge: float
    tributaries: tuple[Arc, ...]         # order >= TRIBUTARY_ORDER, mainstem excluded


@dataclass(frozen=True)
class Lake:
    """Standing water the drain pass found, kept instead of erased."""

    cells: tuple[Cell, ...]
    surface: float                       # the spill level (drained height)
    depth: float                         # deepest fill
    outlet: Cell | None                  # where its water leaves, if anywhere


@dataclass(frozen=True)
class Hydrology:
    systems: tuple[RiverSystem, ...]
    lakes: tuple[Lake, ...]
    order: Field                         # Strahler per cell, 0 off the network

    def lake_cells(self) -> frozenset[Cell]:
        return frozenset(cell for lake in self.lakes for cell in lake.cells)


def study(size: int, *, sea: list[list[bool]], flow: Field,
          downstream: list[list[int]], marsh: Field, settled: Field,
          elevation: Field, sea_level: float, shelf_depth: float,
          share: float, most_rivers: int = 6) -> Hydrology:
    """Read the drainage as a system. Pure, deterministic, no writes.

    `share` is the flow quantile that separates a channel from a hillslope — the
    same dial `_trace_rivers` always used, handed in so the two cannot drift.
    """
    channel = _channel_cells(size, sea, flow, share)
    traced, donors = _forest(size, sea, channel, downstream)
    order = _strahler(traced, donors)
    lakes = _lakes(size, sea, marsh, elevation, downstream)
    systems = _systems(size, sea, flow, downstream, traced, donors, order,
                       settled, elevation, sea_level, shelf_depth, most_rivers)

    order_field = [[0 for _ in range(size)] for _ in range(size)]
    for (i, j), o in order.items():
        order_field[j][i] = o
    return Hydrology(systems=systems, lakes=lakes, order=order_field)


# ---- the channel forest ------------------------------------------------------


def _channel_cells(size: int, sea, flow: Field, share: float) -> set[Cell]:
    land = [(i, j) for j in range(size) for i in range(size) if not sea[j][i]]
    ranked = sorted(flow[j][i] for i, j in land)
    if not ranked:
        return set()
    threshold = ranked[max(0, int(len(ranked) * (1.0 - share)) - 1)]
    return {(i, j) for i, j in land if flow[j][i] >= threshold}


def _step(size: int, downstream, cell: Cell) -> Cell | None:
    packed = downstream[cell[1]][cell[0]]
    return None if packed < 0 else (packed % size, packed // size)


def _forest(size: int, sea, channel: set[Cell], downstream
            ) -> tuple[set[Cell], dict[Cell, list[Cell]]]:
    """Every cell any channel's water crosses on its way down, and who feeds whom.

    Walked from the channel cells rather than taken as the channel set itself,
    because a channel's course can dip below the flow threshold for a cell or two
    where the water spread — and a river with holes in it is not a network.
    """
    traced: set[Cell] = set()
    for head in sorted(channel):
        cursor: Cell | None = head
        for _ in range(size * 3):
            if cursor is None or cursor in traced:
                break
            traced.add(cursor)
            if sea[cursor[1]][cursor[0]]:
                break
            cursor = _step(size, downstream, cursor)
    donors: dict[Cell, list[Cell]] = {}
    for cell in sorted(traced):
        if sea[cell[1]][cell[0]]:
            continue
        target = _step(size, downstream, cell)
        if target is not None and target in traced:
            donors.setdefault(target, []).append(cell)
    return traced, donors


def _strahler(traced: set[Cell], donors: dict[Cell, list[Cell]]) -> dict[Cell, int]:
    """True stream order over the forest, leaves first.

    The receiver tree has no cycles, so one pass over a dependency order does it:
    a source is order one; where two tributaries of equal order meet, the order
    steps up; otherwise the larger carries through.
    """
    fed_by: dict[Cell, Cell] = {feeder: target
                                for target, feeders in donors.items()
                                for feeder in feeders}
    order: dict[Cell, int] = {}
    ready = [cell for cell in sorted(traced) if not donors.get(cell)]
    waiting = {cell: len(donors.get(cell, ())) for cell in traced}
    while ready:
        cell = ready.pop()
        feeders = donors.get(cell, ())
        if not feeders:
            order[cell] = 1
        else:
            tops = sorted((order[f] for f in feeders), reverse=True)
            order[cell] = (tops[0] + 1
                           if len(tops) > 1 and tops[0] == tops[1] else tops[0])
        target = fed_by.get(cell)
        if target is not None:
            waiting[target] -= 1
            if waiting[target] == 0:
                ready.append(target)
    return order


# ---- rivers as systems -------------------------------------------------------


def _systems(size: int, sea, flow: Field, downstream, traced: set[Cell],
             donors: dict[Cell, list[Cell]], order: dict[Cell, int],
             settled: Field, elevation: Field, sea_level: float,
             shelf_depth: float, most: int) -> tuple[RiverSystem, ...]:
    mouths = sorted(
        (cell for cell in traced
         if not sea[cell[1]][cell[0]]
         and (lambda t: t is None or sea[t[1]][t[0]])(_step(size, downstream, cell))),
        key=lambda c: (-flow[c[1]][c[0]], c))

    systems: list[RiverSystem] = []
    deltas = 0
    for mouth in mouths[:most]:
        mainstem = _mainstem(mouth, donors, order, flow)
        if len(mainstem) < 4:
            continue
        tributaries = _tributaries(mainstem, donors, order, flow)
        kind = _mouth_kind(size, sea, mouth, mainstem, settled, elevation,
                           sea_level, shelf_depth)
        if kind == "delta":
            if deltas >= MOST_DELTAS:
                kind = "mouth"           # the budget: two deltas per map
            else:
                deltas += 1
        systems.append(RiverSystem(
            mouth=mouth, mouth_kind=kind, mainstem=tuple(mainstem),
            order=order.get(mouth, 1), discharge=flow[mouth[1]][mouth[0]],
            tributaries=tributaries))
    return tuple(systems)


def _mainstem(mouth: Cell, donors: dict[Cell, list[Cell]],
              order: dict[Cell, int], flow: Field) -> list[Cell]:
    """Walk upstream from the mouth taking the biggest branch at every fork."""
    path = [mouth]
    cursor = mouth
    seen = {mouth}
    while True:
        feeders = [f for f in donors.get(cursor, ()) if f not in seen]
        if not feeders:
            break
        cursor = max(feeders,
                     key=lambda f: (order.get(f, 0), flow[f[1]][f[0]], (-f[0], -f[1])))
        path.append(cursor)
        seen.add(cursor)
    path.reverse()                       # source first, the way a river is drawn
    return path


def _tributaries(mainstem: list[Cell], donors: dict[Cell, list[Cell]],
                 order: dict[Cell, int], flow: Field) -> tuple[Arc, ...]:
    """Every order-≥2 branch off the mainstem, each traced up its own biggest arm.

    One line per tributary, joined to the trunk at its confluence. Sub-tributaries
    are deliberately not recursed into: the budget's own line is that order-one
    brooks are relief, and a second level of order-two forks is more ink than
    structure at the map's scale.
    """
    stem = set(mainstem)
    out: list[Arc] = []
    for junction in mainstem:
        for feeder in sorted(donors.get(junction, ())):
            if feeder in stem or order.get(feeder, 1) < TRIBUTARY_ORDER:
                continue
            branch = _mainstem(feeder, donors, order, flow)
            branch.append(junction)      # meet the trunk, so the join is drawn
            if len(branch) >= 4:
                out.append(Arc(cells=tuple(branch),
                               order=order.get(feeder, 1),
                               discharge=flow[feeder[1]][feeder[0]]))
    out.sort(key=lambda arc: (-arc.discharge, arc.cells[0]))
    return tuple(out)


def _mouth_kind(size: int, sea, mouth: Cell, mainstem: list[Cell],
                settled: Field, elevation: Field, sea_level: float,
                shelf_depth: float, reach: int = 3) -> str:
    """What the river does when it meets the sea.

    Sediment arriving on a shallow shelf builds a delta; a deep drowned approach is
    an estuary; anything else is a plain mouth. Read from fields that already exist —
    the deposition the erosion recorded and the bathymetry under the nearby sea.
    """
    tail = mainstem[-min(len(mainstem), 4):]
    dropped = sum(settled[j][i] for i, j in tail) / len(tail)

    depths: list[float] = []
    mi, mj = mouth
    for dj in range(-reach, reach + 1):
        for di in range(-reach, reach + 1):
            ni, nj = mi + di, mj + dj
            if 0 <= ni < size and 0 <= nj < size and sea[nj][ni]:
                depths.append(sea_level - elevation[nj][ni])
    if not depths:
        return "mouth"
    offshore = sum(depths) / len(depths)
    if offshore <= shelf_depth * SHALLOW_SHARE and dropped >= DELTA_SETTLED:
        return "delta"
    if offshore >= shelf_depth * ESTUARY_SHARE:
        return "estuary"
    return "mouth"


# ---- lakes -------------------------------------------------------------------


def _lakes(size: int, sea, marsh: Field, elevation: Field,
           downstream) -> tuple[Lake, ...]:
    """The broad wet basins worth keeping as open water, biggest first."""
    deep = {(i, j) for j in range(size) for i in range(size)
            if not sea[j][i] and marsh[j][i] >= LAKE_MARSH}
    lakes: list[Lake] = []
    seen: set[Cell] = set()
    for start in sorted(deep):
        if start in seen:
            continue
        patch = [start]
        seen.add(start)
        cursor = 0
        while cursor < len(patch):
            i, j = patch[cursor]
            cursor += 1
            for ni, nj in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                near = (ni, nj)
                if near in deep and near not in seen:
                    seen.add(near)
                    patch.append(near)
        if len(patch) < LAKE_CELLS:
            continue
        patch.sort()
        members = set(patch)
        depth = max(marsh[j][i] for i, j in patch)
        surface = min(elevation[j][i] for i, j in patch)
        outlet = None
        spill = min(patch, key=lambda c: (elevation[c[1]][c[0]], c))
        cursor_cell: Cell | None = spill
        for _ in range(size * 3):
            if cursor_cell is None:
                break
            if cursor_cell not in members:
                outlet = None if sea[cursor_cell[1]][cursor_cell[0]] else cursor_cell
                break
            cursor_cell = _step(size, downstream, cursor_cell)
        lakes.append(Lake(cells=tuple(patch), surface=round(surface, 4),
                          depth=depth, outlet=outlet))
    lakes.sort(key=lambda lake: (-len(lake.cells) * lake.depth, lake.cells[0]))
    return tuple(lakes[:MOST_LAKES])
