"""Erosion: the stage that turns a raised surface into somewhere water has been.

A generated height field, however carefully its ranges are laid, still reads as paint.
Peaks sit on flat ground, valleys are noise rather than drainage, and rivers wander
because there is nothing for them to have cut. Every one of those is the same absence:
the surface has never had water on it.

So this is where the map stops being drawn and starts being *worn*. Three processes run
together, each of them the simplest form that produces its signature:

  incision   — a channel cuts in proportion to how much water passes through it, so
               trunk valleys deepen while headwaters barely move. This is what makes a
               range branch into a drainage network instead of a bumpy ridge.
  deposition — what a channel cuts has to go somewhere. It travels downstream and settles
               where the ground flattens, which builds alluvial fans at the foot of a
               range, floodplains along the trunk, and a delta at the mouth.
  creep      — hillslopes round off and shed their material downhill, which is what
               puts foothills between a mountain and the plain rather than a step.

The incision solver is the implicit form (Braun & Willett): each cell is solved against
its receiver's *already updated* height, walking downstream to upstream, so no timestep
can overshoot and a channel can never be cut below the one it drains into. That matters
more here than the accuracy does — an explicit solver at a step large enough to be worth
running in pure Python punches holes in the terrain, and a hole is a lake the writer
never asked for.

Everything inside runs on flat lists rather than the nested rows the rest of the
generator passes around, and against a neighbour table built once per lattice size. That
is not gratuitous: erosion touches every cell eight times in each of six passes per
round, and the nested-list version of exactly this code took 1.6 seconds — the whole
budget for planning a map — where this takes a fraction of it. The conversion happens at
the door, so nothing else has to know.

Nothing here uses libm. `sqrt` is exempt from that ban because IEEE 754 requires it to be
correctly rounded, so every machine agrees on it; that is why the discharge exponent is
one half and not the 0.45 a geomorphologist would prefer.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from functools import lru_cache

from fw.core.mapgen import noise
from fw.core.mapgen.findings import Finding
from fw.core.mapgen.grid import Field, Grid

# How hard water cuts. Multiplied by the square root of the catchment, so this is the
# fraction of the way a headwater cell moves toward its receiver in one round; a trunk
# draining ten thousand cells moves a hundred times as far. At this strength a headwater
# barely stirs while a trunk closes much of the gap to its bed in a single round, and
# that spread is what turns a slope into a drainage network.
INCISION = 0.20

# How readily the loosened material settles again, per round, on flat ground. Deposition
# is what separates a landscape from a quarry: without it a range erodes into a stump and
# the plain around it stays exactly as flat as it started.
SETTLING = 0.9

# Slope at which deposition has essentially stopped. Below this the ground is flat enough
# to drop its load; above it, material keeps moving.
SETTLE_SLOPE = 0.020

# Below this catchment a cell is a hillslope rather than a channel: creep shapes it and
# running water does not cut it. Without a threshold every cell on the map is a stream
# head, which dissects hillsides into a corduroy of parallel gullies — the single most
# recognisable tell of a terrain generator that has had erosion bolted onto it.
CHANNEL_HEAD = 9.0

# How much of a channel's incision is shared with the ground either side of it.
#
# Steepest-descent routing sends all of a catchment's water down a single line of cells,
# so left alone it cuts a slot one cell wide and vertical-sided — which renders as a
# staircase of dark pixels, and is the reason a first look at an eroded map so often
# reads as worse than the smooth one it replaced. Real valleys are far wider than they
# are deep. Spreading each round's cut over the cell's neighbourhood before applying it
# turns the slot into a V, and it is the difference between a drainage network you can
# see and one you can only measure.
WIDENING = 1.60

# Hillslope creep per round, as a share of the way to the neighbourhood mean.
CREEP = 0.24

# Rounds of the whole cycle. Each one re-routes the water, so valleys captured in an
# early round steer the later ones — which is where dendritic branching comes from. Four
# is where the network stops rearranging and starts merely deepening.
ROUNDS = 4

# How much softer the softest rock is than the hardest. Uniform rock erodes into uniform
# terrain; a little variation is what gives one valley a gorge and the next a bowl.
ROCK_CONTRAST = 0.7
ROCK_SCALE = 17.0        # lattice cells per unit of the rock-hardness field
ROCK_STRIDE = 4          # the field varies slowly, so sample it coarsely and interpolate

# The nudge that keeps a filled depression strictly descending, so every land cell has a
# downhill path and no channel ever has to run uphill.
DRAIN_STEP = 1e-5

_DIAGONAL = math.sqrt(2.0)
_INVERSE_DIAGONAL = 1.0 / _DIAGONAL


@dataclass
class Erosion:
    """The worn surface, and everything the wearing worked out on the way.

    The fields here are not by-products: `flow` is the drainage network every later
    stage reads, `settled` is where the soil is deep, and `cut` is how the map explains
    a valley to the writer as something that was carved rather than drawn.
    """

    elevation: Field                     # the worn surface
    uplift: Field                        # what it looked like before any water ran
    flow: Field                          # cells draining through each cell
    slope: Field                         # local fall per cell, after wearing
    cut: Field                           # height removed by incision
    settled: Field                       # height added back as sediment
    downstream: list[list[int]]          # packed j * size + i, or -1 at base level
    notes: list[Finding] = field(default_factory=list)

    def carved(self) -> float:
        """Mean depth of incision over the ground that was cut at all."""
        depths = [value for row in self.cut for value in row if value > 0.0]
        return sum(depths) / len(depths) if depths else 0.0

    def deepest(self) -> float:
        return max((value for row in self.cut for value in row), default=0.0)


def erode(grid: Grid, *, elevation: Field, sea: list[list[bool]], seed: str,
          rounds: int = ROUNDS, incision: float = INCISION,
          creep: float = CREEP, rainfall: Field | None = None) -> Erosion:
    """Run water over a raised surface until it looks like it has been there a while.

    `rainfall` is optional and deliberately so. Erosion runs *before* the climate stage,
    because the climate reads the eroded relief — a rain shadow needs a range with a
    finished profile to sit behind. Passing rain here is for a second pass, once the
    weather is known and the difference between a wet flank and a dry one is worth
    resolving.
    """
    size = grid.size
    table = _neighbourhood(size)
    height = [value for row in elevation for value in row]
    uplift = height[:]
    wet = [value for row in sea for value in row]
    rain = ([value for row in rainfall for value in row]
            if rainfall is not None else None)
    hardness = _hardness(size, seed)

    cells = size * size
    total_cut = [0.0] * cells
    total_settled = [0.0] * cells
    receiver: list[int] = [-1] * cells
    flow: list[float] = [0.0] * cells

    for _ in range(max(0, rounds)):
        height = _drain(size, table, height, wet)
        receiver, order, reach = _route(size, table, height, wet)
        flow = _accumulate(size, table, height, wet, rain)
        cut = _incise(height, receiver, order, wet, flow, reach, hardness, incision)
        _widen(size, table, height, wet, cut, WIDENING)
        settled = _deposit(height, receiver, order, wet, flow, reach, cut)
        for k in range(cells):
            total_cut[k] += cut[k]
            total_settled[k] += settled[k]
        height = _creep(size, table, height, wet, creep)

    height = _drain(size, table, height, wet)
    receiver, order, _ = _route(size, table, height, wet)
    flow = _accumulate(size, table, height, wet, rain)

    return Erosion(
        elevation=_rows(size, height),
        uplift=_rows(size, uplift),
        flow=_rows(size, flow),
        slope=_rows(size, _slopes(size, table, height, wet)),
        cut=_rows(size, total_cut),
        settled=_rows(size, total_settled),
        downstream=[receiver[j * size:(j + 1) * size] for j in range(size)],
        notes=[])


def _rows(size: int, flat: list[float]) -> Field:
    return [flat[j * size:(j + 1) * size] for j in range(size)]


@lru_cache(maxsize=4)
def _neighbourhood(size: int) -> tuple[tuple[tuple[int, ...], ...],
                                       tuple[tuple[int, ...], ...]]:
    """Every cell's neighbours, orthogonal and diagonal kept apart.

    Built once per lattice and shared by every pass. They are separate because the two
    differ only by the step between them, and separating them lets the hot loops iterate
    plain integers: no tuple to unpack per neighbour, and no multiply at all on the four
    orthogonal ones. That sounds like a triviality and is worth about a third of this
    stage — the loops here run some eight million times over a map.
    """
    orthogonal: list[tuple[int, ...]] = []
    diagonal: list[tuple[int, ...]] = []
    for j in range(size):
        for i in range(size):
            straight: list[int] = []
            across: list[int] = []
            for dj in (-1, 0, 1):
                nj = j + dj
                if not 0 <= nj < size:
                    continue
                for di in (-1, 0, 1):
                    ni = i + di
                    if not (di or dj) or not 0 <= ni < size:
                        continue
                    (across if di and dj else straight).append(nj * size + ni)
            orthogonal.append(tuple(straight))
            diagonal.append(tuple(across))
    return tuple(orthogonal), tuple(diagonal)


# ---- the surface water runs on ------------------------------------------------


def _drain(size: int, table, height: list[float],
           sea: list[bool]) -> list[float]:
    """Raise every pit to its lowest outlet, so all land drains somewhere.

    Fractal detail riddles a surface with hollows a cell or two across, and water landing
    in one has nowhere to go. Flooding inward from the coast and never letting a cell
    settle below the pass it was reached through leaves a surface where every land cell
    has a strictly descending path to the sea. The invariant is established here rather
    than checked afterwards, which is why nothing downstream has to handle a sink.

    The plain form of this pushes every cell of the map through a heap. The refinement is
    that a cell already at or below the level the flood has reached does not need
    ordering against anything — it is going to be raised to exactly here plus a nudge
    whenever it is visited — so it goes on a queue instead, and the heap only ever holds
    the cells that are genuinely higher than the front. On a continent, where most of the
    interior is either plain or basin, that is the great majority of them.
    """
    orthogonal, diagonal = table
    cells = size * size
    out = height[:]
    closed = bytearray(cells)
    heap: list[tuple[float, int]] = []
    queue: list[int] = []
    last = size - 1

    for k in range(cells):
        column = k % size
        if (sea[k] or k < size or k >= cells - size
                or column == 0 or column == last):
            closed[k] = 1
            heap.append((out[k], k))
    heapq.heapify(heap)
    push, pop = heapq.heappush, heapq.heappop

    head = 0
    while heap or head < len(queue):
        if head < len(queue):
            k = queue[head]
            head += 1
            level = out[k]
        else:
            level, k = pop(heap)
        floor = level + DRAIN_STEP
        for nk in orthogonal[k]:
            if closed[nk]:
                continue
            closed[nk] = 1
            if height[nk] <= level:
                out[nk] = floor
                queue.append(nk)
            else:
                out[nk] = height[nk]
                push(heap, (height[nk], nk))
        for nk in diagonal[k]:
            if closed[nk]:
                continue
            closed[nk] = 1
            if height[nk] <= level:
                out[nk] = floor
                queue.append(nk)
            else:
                out[nk] = height[nk]
                push(heap, (height[nk], nk))
        # The queue grows without bound over a large basin; discard what it has already
        # walked rather than carrying a list the length of the continent.
        if head > 4096:
            del queue[:head]
            head = 0
    return out


def _route(size: int, table, height: list[float],
           sea: list[bool]) -> tuple[list[int], list[int], list[float]]:
    """Steepest descent from every cell, plus an order that never visits a cell first.

    The order is what makes the whole stage linear rather than a sort per round: walk it
    forwards and every cell's receiver has already been handled; walk it backwards and
    every cell's contributors have. One depth-first pass from the coast builds it.

    Ties break on the lower packed index, because the neighbour tables are in index order
    and only a strictly steeper fall displaces the incumbent. That is what makes the
    network identical on every run rather than dependent on iteration order. The step to
    each cell's receiver comes back too: incision and deposition both need it, and
    finding it again by searching the neighbour list would cost more than carrying it.
    """
    orthogonal, diagonal = table
    cells = size * size
    receiver = [-1] * cells
    reach = [1.0] * cells
    donors: list[list[int]] = [[] for _ in range(cells)]
    roots: list[int] = []

    for k in range(cells):
        # The sea is base level: it receives water and passes none on, so there is
        # nothing to work out for it. Skipping the eight-neighbour scan on every wet cell
        # is most of a pass on a map that is half ocean.
        if sea[k]:
            roots.append(k)
            continue
        here = height[k]
        best, gradient, step = -1, 0.0, 1.0
        for nk in orthogonal[k]:
            fall = here - height[nk]
            if fall > gradient:
                gradient, best, step = fall, nk, 1.0
        for nk in diagonal[k]:
            fall = (here - height[nk]) * _INVERSE_DIAGONAL
            if fall > gradient:
                gradient, best, step = fall, nk, _INVERSE_DIAGONAL
        if best < 0:
            roots.append(k)
        else:
            receiver[k] = best
            reach[k] = step
            donors[best].append(k)

    order: list[int] = []
    append = order.append
    stack = roots[::-1]
    while stack:
        node = stack.pop()
        append(node)
        feeders = donors[node]
        if feeders:
            stack.extend(reversed(feeders))
    return receiver, order, reach


def _accumulate(size: int, table, height: list[float], sea: list[bool],
                rainfall: list[float] | None) -> list[float]:
    """How much water passes through each cell, spread across every downhill way out.

    Sending all of a cell's water to its single steepest neighbour is the obvious thing
    and it is wrong in a way that is impossible to unsee once noticed: there are only
    eight directions, so on any broad slope the channels snap to one of them and the map
    grows a corduroy of straight parallel gullies running due south and due east. It is
    not subtle. It was the first thing visible when erosion was switched on here.

    Dividing each cell's water between *all* its lower neighbours has no preferred
    direction at all. Divides come out soft, which is what they are on real ground, and a
    valley floor collects its water over several cells rather than one — so incision
    widens a valley instead of slotting it.

    The share each neighbour gets goes as the *fourth power* of how much lower it is, and
    that exponent is the whole balance of the thing. Sharing in simple proportion to the
    drop is far too generous: on any smooth slope every downhill neighbour is nearly as
    good as every other, so the water fans out and never gathers. Measured, the largest
    channel on a two-thousand-cell landmass carried forty cells' worth of water — a sheet
    running off a roof, not a river. At the fourth power a channel that is clearly the
    steepest way down takes nearly all of it, while a cell on an indistinct divide still
    shares, which is exactly the distinction that separates a valley floor from a ridge.

    The single steepest neighbour is still what incision solves against; it is only the
    water that is shared. Sorting by height replaces the topological order, because with
    every cell donating to several there is no tree to walk.
    """
    orthogonal, diagonal = table
    cells = size * size
    if rainfall is None:
        flow = [0.0 if sea[k] else 1.0 for k in range(cells)]
    else:
        flow = [0.0 if sea[k] else rainfall[k] for k in range(cells)]

    # Descending height, land only. Python's sort is stable, so cells of equal height
    # keep index order and the network is the same on every run.
    order = sorted((k for k in range(cells) if not sea[k]),
                   key=height.__getitem__, reverse=True)
    for k in order:
        here = height[k]
        straight, across = orthogonal[k], diagonal[k]
        total = 0.0
        for nk in straight:
            fall = here - height[nk]
            if fall > 0.0:
                fall *= fall
                total += fall * fall
        for nk in across:
            fall = here - height[nk]
            if fall > 0.0:
                fall = fall * fall * _INVERSE_DIAGONAL
                total += fall * fall
        if total <= 0.0:
            continue
        share = flow[k] / total
        for nk in straight:
            fall = here - height[nk]
            if fall > 0.0:
                fall *= fall
                flow[nk] += share * fall * fall
        for nk in across:
            fall = here - height[nk]
            if fall > 0.0:
                fall = fall * fall * _INVERSE_DIAGONAL
                flow[nk] += share * fall * fall
    return flow


# ---- the three processes ------------------------------------------------------


def _incise(height: list[float], receiver: list[int], order: list[int],
            sea: list[bool], flow: list[float], reach: list[float],
            hardness: list[float], strength: float) -> list[float]:
    """Cut each channel in proportion to the water through it, downstream first.

    The implicit step: a cell is solved against its receiver's *new* height, so the
    result is a weighted average of where the cell was and where the channel below it now
    is. It cannot overshoot, whatever the strength, which is what allows a step large
    enough to be worth taking in pure Python. It also means one pass carries the sea's
    base level all the way to the headwaters, rather than one cell of the trunk per
    round — a whole river adjusts to its mouth at once.
    """
    cut = [0.0] * len(height)
    root = math.sqrt
    for k in order:
        target = receiver[k]
        if target < 0 or sea[k]:
            continue
        here = height[k]
        below = height[target]
        if here <= below:
            continue
        stream = flow[k] - CHANNEL_HEAD
        if stream <= 0.0:
            continue                     # a hillslope, not a channel: creep shapes it
        # sqrt of the catchment: the discharge exponent. Correctly rounded everywhere,
        # unlike the fractional power a textbook would write.
        pull = strength * root(stream) * reach[k] / hardness[k]
        worn = (here + pull * below) / (1.0 + pull)
        cut[k] = here - worn
        height[k] = worn
    return cut


def _widen(size: int, table, height: list[float], sea: list[bool],
           cut: list[float], share: float) -> None:
    """Let a channel's cut spill sideways, so a valley has flanks rather than walls.

    Applied after the incision pass and before deposition, on the round's cut rather than
    on the height, so the valley opens out as it deepens instead of being smoothed back
    flat afterwards. The cell keeps most of what it cut; the rest is taken off its
    neighbours, weighted by how much they stand above it — which is what makes the widening
    follow the valley wall instead of gnawing at the ground downstream.
    """
    orthogonal, diagonal = table
    lifted: list[tuple[int, float]] = []
    for k, depth in enumerate(cut):
        if depth <= 0.0:
            continue
        here = height[k]
        spread = depth * share
        for near, weight in ((orthogonal[k], 1.0), (diagonal[k], _INVERSE_DIAGONAL)):
            for nk in near:
                if sea[nk]:
                    continue
                above = height[nk] - here
                if above <= 0.0:
                    continue
                take = spread * weight * 0.25
                if take > above:
                    take = above
                lifted.append((nk, take))
    for nk, take in lifted:
        height[nk] -= take


def _deposit(height: list[float], receiver: list[int], order: list[int],
             sea: list[bool], flow: list[float], reach: list[float],
             cut: list[float]) -> list[float]:
    """Carry what was cut downstream, and drop it where the ground flattens.

    This is the half that makes the difference. Incision alone wears a range down into a
    stump on a plain that is exactly as flat as it started; carrying the material out and
    letting it settle is what builds the apron at the range's foot, the flood plain along
    the trunk and the fan at the mouth — the three places a map most obviously looks
    built rather than grown.
    """
    cells = len(height)
    settled = [0.0] * cells
    carried = [0.0] * cells
    ceiling = SETTLE_SLOPE

    for k in reversed(order):
        load = carried[k] + cut[k]
        if load <= 0.0:
            continue
        target = receiver[k]
        if target < 0 or sea[k]:
            continue                       # the sea takes whatever reaches it
        fall = (height[k] - height[target]) * reach[k]
        if fall >= ceiling:
            carried[target] += load      # steep ground keeps its load moving
            continue
        # What settles is a *concentration*, not a share of the load: the same tonne of
        # silt spread over a trunk draining half a continent is a film, and dropped into
        # a headwater gully is a landslide. Dividing by the catchment is what says so —
        # and it is also what stops deposition running away, which the share form does
        # spectacularly, piling a mountain in the middle of a flood plain.
        flatness = 1.0 - fall / ceiling if fall > 0.0 else 1.0
        drop = SETTLING * flatness * load / flow[k]
        if drop > load:
            drop = load
        settled[k] = drop
        height[k] = height[k] + drop
        carried[target] += load - drop
    return settled


def _creep(size: int, table, height: list[float], sea: list[bool],
           rate: float) -> list[float]:
    """Hillslopes round off and shed material downhill.

    Incision alone leaves knife-edge divides between valleys, because nothing is acting
    on the ground *between* the channels. Creep is what puts a shoulder on a ridge and a
    skirt of foothills at the foot of a range — the transition that makes a mountain look
    attached to the plain rather than set down on it.
    """
    orthogonal, diagonal = table
    out = height[:]
    for k in range(size * size):
        if sea[k]:
            continue
        straight = orthogonal[k]
        across = diagonal[k]
        total = 0.0
        for nk in straight:
            total += height[nk]
        corners = 0.0
        for nk in across:
            corners += height[nk]
        weight = len(straight) + len(across) * _INVERSE_DIAGONAL
        if weight:
            here = height[k]
            mean = (total + corners * _INVERSE_DIAGONAL) / weight
            out[k] = here + rate * (mean - here)
    return out


# ---- what the rock is made of -------------------------------------------------


def _hardness(size: int, seed: str) -> list[float]:
    """A slowly varying rock-hardness field, so not every valley has the same profile.

    Uniform rock erodes into uniform terrain — every catchment the same shape at a
    different size, which the eye reads as repetition even when it cannot say why. A
    broad, smooth variation is enough: one range weathers into round shoulders and the
    next holds a gorge.

    It varies over seventeen cells, so it is sampled every four and interpolated between.
    Sampling it per cell would cost a quarter of a million hashes to describe something
    that barely changes over the distance — the same field, at sixteen times the price.
    """
    stride = ROCK_STRIDE
    coarse_span = size // stride + 2
    coarse = [
        noise.fbm(f"{seed}|rock", (cx * stride) / ROCK_SCALE, (cy * stride) / ROCK_SCALE,
                  octaves=3)
        for cy in range(coarse_span) for cx in range(coarse_span)
    ]
    out = [1.0] * (size * size)
    for j in range(size):
        cy, fy = divmod(j, stride)
        ty = fy / stride
        base = cy * coarse_span
        above = base + coarse_span
        for i in range(size):
            cx, fx = divmod(i, stride)
            tx = fx / stride
            top = coarse[base + cx] + (coarse[base + cx + 1] - coarse[base + cx]) * tx
            low = coarse[above + cx] + (coarse[above + cx + 1] - coarse[above + cx]) * tx
            grain = top + (low - top) * ty
            out[j * size + i] = 1.0 + ROCK_CONTRAST * (grain - 0.5)
    return out


def _slopes(size: int, table, height: list[float],
            sea: list[bool]) -> list[float]:
    """Steepest fall to any neighbour, per cell — read by soils, marsh and vegetation."""
    orthogonal, diagonal = table
    out = [0.0] * (size * size)
    for k in range(size * size):
        if sea[k]:
            continue
        here = height[k]
        steepest = 0.0
        for nk in orthogonal[k]:
            fall = here - height[nk]
            if fall < 0.0:
                fall = -fall
            if fall > steepest:
                steepest = fall
        for nk in diagonal[k]:
            fall = here - height[nk]
            if fall < 0.0:
                fall = -fall
            fall *= _INVERSE_DIAGONAL
            if fall > steepest:
                steepest = fall
        out[k] = steepest
    return out
