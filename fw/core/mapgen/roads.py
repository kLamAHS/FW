"""Roads, and why several of them turn out to be one road.

A road network built as "shortest path between each pair of towns" gives a fan of
separate lines that happen to run alongside each other for most of their length, which
is not what a road network looks like from above and not what one is. Real roads bundle:
the reason the Great North Road exists is that everybody going north went the same way
for the first hundred miles, and the reason they did is that somebody had already
cleared it.

That is the whole mechanism here, and it is one line. Routes are laid one at a time, and
a cell that already carries a road is *cheaper* to travel than one that does not. So the
second route between two places on the same side of the kingdom finds it worth a detour
to join the first, and the tenth is barely a route at all — it is a spur onto a trunk.
Nothing counts the bundles or plans them; they are what falls out of laying a road on
ground where a road already is.

What comes out is then ranked by how much of the kingdom's traffic each stretch carries,
which is a number the laying already produced: a highway is not a wider road, it is a
road more people are on.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from fw.core.mapgen.grid import Field, Grid

# What a cell costs once a road runs through it, as a share of the raw ground. This is
# the number that decides whether a network bundles or fans: at one, nothing bundles;
# near zero, every road in the world becomes one road with spurs.
MADE_ROAD = 0.35

# How much traffic a stretch has to carry to earn each name, as a share of the busiest.
GRADES = (("highway", 0.45), ("road", 0.16), ("track", 0.0))

# How many extra links beyond a spanning tree, as a share of the settlements. A tree
# connects everything and no more, and a kingdom whose roads form a tree has no way
# round anything: one landslide and the north is cut off.
REDUNDANCY = 0.25


@dataclass(frozen=True)
class Route:
    """One stretch of made road, and what it joins."""

    cells: tuple[tuple[int, int], ...]
    grade: str
    traffic: float                  # share of the kingdom's journeys using this stretch
    joins: tuple[int, int]          # indices into the settlements handed in
    because: str = ""

    @property
    def length(self) -> int:
        return len(self.cells)


@dataclass
class Roads:
    """The network, and the working behind it.

    Two views of the same thing, because two different jobs need different shapes.
    `routes` is one road per pair of places joined, which is what the travel engine
    wants — a segment has to run from somewhere to somewhere for a journey to be costed
    along it. `network` is the same roads cut into stretches of one grade and drawn once
    each, which is what a picture wants: a trunk with branches, rather than nine lines on
    top of one another of which each claims to be a highway.
    """

    routes: tuple[Route, ...]
    network: tuple[Route, ...]
    traffic: Field                  # per cell, journeys passing through

    def carried(self, i: int, j: int) -> float:
        return self.traffic[j][i]


def plan_roads(grid: Grid, *, places: list[tuple[int, int]], weights: list[float],
               cost: Field, sea: list[list[bool]]) -> Roads:
    """Join the settlements, letting each road make the next one cheaper.

    `weights` is how much traffic each place generates — a city sends more carts than a
    hamlet — and it decides the order roads are laid in as well as how busy they end up.
    Laying the busiest first matters: the trunk has to exist before anything can bundle
    onto it.
    """
    size = grid.size
    if len(places) < 2:
        return Roads(routes=(), network=(), traffic=grid.filled(0.0))

    made = grid.filled(1.0)                    # what a cell costs, discounted as it is used
    traffic = grid.filled(0.0)
    links = _links(places, weights, cost, grid, sea)

    routes: list[Route] = []
    for weight, a, b in links:
        path = _cheapest(grid, places[a], places[b], cost=cost, made=made, sea=sea)
        if len(path) < 2:
            continue
        for i, j in path:
            traffic[j][i] += weight
            # The discount is applied once and does not compound: a road is a road, and
            # a stretch carrying ten routes is not ten times easier to walk than one
            # carrying a single route.
            made[j][i] = MADE_ROAD
        routes.append(Route(cells=tuple(path), grade="track", traffic=weight,
                            joins=(a, b)))

    busiest = max((max(traffic[j][i] for i in range(size)) for j in range(size)),
                  default=0.0) or 1.0

    # A road's own grade is the *quietest* stretch it runs along, because that is what
    # the road is: a lane that joins a highway for two miles is a lane. Taking the
    # busiest instead called every road out of the capital a highway.
    joined: list[Route] = []
    for route in routes:
        quietest = min(traffic[j][i] for i, j in route.cells) / busiest
        joined.append(Route(cells=route.cells, grade=_grade(quietest),
                            traffic=round(quietest, 3), joins=route.joins,
                            because=_because(quietest)))
    joined.sort(key=lambda r: (-r.traffic, r.cells[0]))
    return Roads(routes=tuple(joined),
                 network=_stretches(routes, traffic, busiest), traffic=traffic)


def _stretches(routes: list[Route], traffic: Field, busiest: float) -> tuple[Route, ...]:
    """Turn the routes into the network they actually made, drawn once.

    A route is not a thing on the ground; the road is. Twelve routes out of one city all
    run down the same street for the first mile, and reporting each of them whole means
    that street is drawn twelve times — and, worse, that each of the twelve is called a
    highway because it touches one. Grading a route by its busiest cell says the lane at
    the far end of it is a highway too, which is the mistake the rivers made before their
    widths were cut into reaches.

    Cutting each route into runs of one grade and dropping identical runs is not enough
    either: two routes that share a trunk and leave it at different points produce runs
    that overlap without matching, so a third of the network still came out drawn twice.

    So the routes are reduced to the set of *links* they used, each link graded by the
    traffic through it, and the links are then walked into the longest chains that keep
    one grade. Every stretch of road appears exactly once, and where a highway becomes a
    road it becomes one at a junction rather than wherever a route happened to end.
    """
    grade_of: dict[tuple[tuple[int, int], tuple[int, int]], str] = {}
    carried_on: dict[tuple[tuple[int, int], tuple[int, int]], float] = {}
    joins: dict[tuple[tuple[int, int], tuple[int, int]], tuple[int, int]] = {}
    for route in routes:
        for a, b in zip(route.cells, route.cells[1:], strict=False):
            link = (a, b) if a <= b else (b, a)
            if link in grade_of:
                continue
            # A link is only as busy as its quieter end: the traffic that crosses it is
            # what both ends have in common.
            carried = min(traffic[a[1]][a[0]], traffic[b[1]][b[0]]) / busiest
            grade_of[link] = _grade(carried)
            carried_on[link] = carried
            joins[link] = route.joins

    neighbours: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for a, b in grade_of:
        neighbours.setdefault(a, []).append(b)
        neighbours.setdefault(b, []).append(a)
    for cell in neighbours:
        neighbours[cell].sort()

    unwalked = set(grade_of)
    out: list[Route] = []
    for link in sorted(grade_of):
        if link not in unwalked:
            continue
        grade = grade_of[link]
        unwalked.discard(link)
        chain = [link[0], link[1]]
        # Out from both ends, while the road carries on as the same grade and does not
        # fork. A fork is a junction, and a junction is where one road ends.
        for end in (0, 1):
            while True:
                head = chain[0] if end == 0 else chain[-1]
                inside = chain[1] if end == 0 else chain[-2]
                onward = [n for n in neighbours.get(head, [])
                          if n != inside
                          and ((head, n) if head <= n else (n, head)) in unwalked
                          and grade_of[(head, n) if head <= n else (n, head)] == grade]
                if len(onward) != 1:
                    break
                step = onward[0]
                unwalked.discard((head, step) if head <= step else (step, head))
                if end == 0:
                    chain.insert(0, step)
                else:
                    chain.append(step)

        cells = tuple(chain)
        busiest_on = max(
            carried_on[(a, b) if a <= b else (b, a)]
            for a, b in zip(cells, cells[1:], strict=False))
        out.append(Route(cells=cells, grade=grade, traffic=round(busiest_on, 3),
                         joins=joins[link], because=_because(busiest_on)))
    out.sort(key=lambda r: (-r.traffic, r.cells[0]))
    return tuple(out)


def _links(places: list[tuple[int, int]], weights: list[float], cost: Field,
           grid: Grid, sea: list[list[bool]]) -> list[tuple[float, int, int]]:
    """Which pairs to join, busiest first.

    A spanning tree over an estimate of what each link would cost, so everywhere is
    reachable, plus a few of the best remaining links so that it is not a tree — a
    kingdom whose roads form a tree has exactly one way to get anywhere, and one washed
    out bridge cuts a province off.

    The estimate is straight-line distance weighted by the ground at either end, and it
    only has to be good enough to *choose* the links: the ones chosen are then routed
    properly. Routing every pair to find out which pairs to route is the same work
    n-squared times over.
    """
    count = len(places)
    guesses: list[tuple[float, int, int]] = []
    for a in range(count):
        for b in range(a + 1, count):
            (ax, ay), (bx, by) = places[a], places[b]
            ground = (cost[ay][ax] + cost[by][bx]) * 0.5
            guesses.append((math.dist(places[a], places[b]) * ground, a, b))
    guesses.sort()

    parent = list(range(count))

    def find(n: int) -> int:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    chosen: list[tuple[int, int]] = []
    spare: list[tuple[float, int, int]] = []
    for guess, a, b in guesses:
        ra, rb = find(a), find(b)
        if ra == rb:
            spare.append((guess, a, b))
            continue
        parent[ra] = rb
        chosen.append((a, b))

    # And a handful of the shortest links the tree did not need, which is where the
    # alternatives come from.
    extra = int(count * REDUNDANCY)
    chosen.extend((a, b) for _guess, a, b in spare[:extra])

    # Laid busiest first, so the trunk is there for the rest to bundle onto. Ties break
    # on the pair, so the network is the same on every run.
    out = [((weights[a] + weights[b]), a, b) for a, b in chosen]
    out.sort(key=lambda link: (-link[0], link[1], link[2]))
    return out


def _cheapest(grid: Grid, origin: tuple[int, int], target: tuple[int, int], *,
              cost: Field, made: Field, sea: list[list[bool]]) -> list[tuple[int, int]]:
    """The cheapest way across the ground, counting made road as cheaper than ground."""
    size = grid.size
    best = {origin: 0.0}
    came: dict[tuple[int, int], tuple[int, int]] = {}
    heap: list[tuple[float, tuple[int, int]]] = [(0.0, origin)]
    while heap:
        paid, cell = heapq.heappop(heap)
        if cell == target:
            break
        if paid > best.get(cell, math.inf):
            continue
        i, j = cell
        for dj in (-1, 0, 1):
            for di in (-1, 0, 1):
                if not (di or dj):
                    continue
                ni, nj = i + di, j + dj
                if not (0 <= ni < size and 0 <= nj < size) or sea[nj][ni]:
                    continue
                step = cost[nj][ni] * made[nj][ni] * (1.4142 if di and dj else 1.0)
                if step == math.inf:
                    continue
                fresh = paid + step
                if fresh < best.get((ni, nj), math.inf):
                    best[(ni, nj)] = fresh
                    came[(ni, nj)] = cell
                    heapq.heappush(heap, (fresh, (ni, nj)))
    if target not in came and target != origin:
        return []
    path = [target]
    cursor = target
    while cursor != origin and cursor in came:
        cursor = came[cursor]
        path.append(cursor)
    path.reverse()
    return path


def _grade(carried: float) -> str:
    for name, floor in GRADES:
        if carried >= floor:
            return name
    return GRADES[-1][0]


def _because(carried: float) -> str:
    if carried >= GRADES[0][1]:
        return "most of the kingdom's traffic comes this way"
    if carried >= GRADES[1][1]:
        return "a made road between places that trade"
    return "a track between neighbours"
