"""Turning a field into shapes a map can draw.

The generator computes on a lattice and the writer's map holds polygons, so something
has to cross between them. That crossing is the single decision that most determines
whether a generated map reads as a place.

The shipping generator crossed it by casting rays from each region's centre. A star
polygon cannot be concave enough for a bay, cannot enclose an island, and cannot share
an edge with its neighbour — which is why its maps have sea channels between regions
that border each other, and coastlines made of smooth lumps.

Contouring has no such limits. One pass of marching squares over a height field yields
bays, gulfs, capes, peninsulas, isthmuses, offshore islands and inland lakes, because
none of those are special cases: they are all just where the field crosses sea level.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

Point = tuple[float, float]
Ring = list[Point]
Field = list[list[float]]

# Which cell edges a contour crosses, per corner-above-level bitmask. Corners are
# numbered clockwise from the top-left and edges by the corner they start at, so an
# entry (3, 0) means "in through the left edge, out through the top".
_CROSSINGS: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((3, 0),), 2: ((0, 1),), 3: ((3, 1),), 4: ((1, 2),), 6: ((0, 2),),
    7: ((3, 2),), 8: ((2, 3),), 9: ((2, 0),), 11: ((2, 1),), 12: ((1, 3),),
    13: ((1, 0),), 14: ((0, 3),),
    # The ambiguous saddles. Either resolution is defensible; this one is consistent,
    # which is what a deterministic generator actually needs.
    5: ((3, 0), (1, 2)), 10: ((0, 1), (2, 3)),
}


def signed_area(ring: Sequence[Point]) -> float:
    """Positive one way round the ring, negative the other.

    This is how an enclosed lake is told from a coastline: a contour tracer winds them
    oppositely. Which sign means which is a property of the tracer and must be derived
    from the rings themselves, never assumed — assuming it once drew a whole continent
    as a hole in the sea.
    """
    total = 0.0
    count = len(ring)
    for k in range(count):
        x1, y1 = ring[k]
        x2, y2 = ring[(k + 1) % count]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def area(ring: Sequence[Point]) -> float:
    return abs(signed_area(ring))


def _crossing(a: Point, b: Point, va: float, vb: float, level: float) -> Point:
    """Where the contour cuts a cell edge, interpolated so it is not stair-stepped."""
    t = 0.5 if vb == va else (level - va) / (vb - va)
    t = max(0.0, min(1.0, t))
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def contours(field: Field, level: float) -> list[Ring]:
    """Every closed iso-line at `level`, in lattice coordinates.

    Marching squares. Rings come back in a stable order — the traversal starts from
    sorted endpoints — so the same field always yields the same rings in the same
    order, which is what makes a golden coordinate test possible.
    """
    rows = len(field)
    if rows < 2:
        return []
    columns = len(field[0])
    links: dict[Point, list[Point]] = {}
    for j in range(rows - 1):
        lower, upper = field[j], field[j + 1]
        for i in range(columns - 1):
            values = (lower[i], lower[i + 1], upper[i + 1], upper[i])
            mask = 0
            for bit, value in zip((1, 2, 4, 8), values, strict=True):
                if value > level:
                    mask += bit
            if mask in (0, 15):
                continue
            corners = ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1))
            edges = [
                _crossing(corners[k], corners[(k + 1) % 4],
                          values[k], values[(k + 1) % 4], level)
                for k in range(4)
            ]
            for out_edge, in_edge in _CROSSINGS[mask]:
                start = (round(edges[out_edge][0], 4), round(edges[out_edge][1], 4))
                end = (round(edges[in_edge][0], 4), round(edges[in_edge][1], 4))
                links.setdefault(start, []).append(end)

    rings: list[Ring] = []
    walked: set[Point] = set()
    for start in sorted(links):
        if start in walked:
            continue
        ring: Ring = [start]
        walked.add(start)
        cursor = start
        while True:
            step = next((p for p in links.get(cursor, ()) if p not in walked), None)
            if step is None:
                break
            ring.append(step)
            walked.add(step)
            cursor = step
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def simplify(points: Sequence[Point], tolerance: float) -> Ring:
    """Douglas-Peucker, iteratively.

    A raw lattice contour is thousands of points, most of them saying nothing. The
    recursion is written as an explicit stack because a pathological ring — a long
    near-straight coast — recurses once per point, and a generated map should not be
    able to raise RecursionError.
    """
    count = len(points)
    if count < 3:
        return list(points)
    keep = [False] * count
    keep[0] = keep[-1] = True
    stack = [(0, count - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        ax, ay = points[first]
        bx, by = points[last]
        span = math.hypot(bx - ax, by - ay)
        worst, index = -1.0, first
        for k in range(first + 1, last):
            px, py = points[k]
            if span:
                gap = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / span
            else:
                gap = math.hypot(px - ax, py - ay)
            if gap > worst:
                worst, index = gap, k
        if worst > tolerance:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, kept in zip(points, keep, strict=True) if kept]


def smoothed(ring: Sequence[Point], rounds: int = 2) -> Ring:
    """Chaikin corner cutting, so a lattice-derived coast stops looking like stairs.

    Each round replaces every corner with two points a quarter and three quarters along
    its edges. Two rounds is enough to read as drawn rather than rasterised; more and
    the bays start to close up.
    """
    points = list(ring)
    for _ in range(rounds):
        if len(points) < 3:
            return points
        cut: Ring = []
        for k in range(len(points)):
            ax, ay = points[k]
            bx, by = points[(k + 1) % len(points)]
            cut.append((ax * 0.75 + bx * 0.25, ay * 0.75 + by * 0.25))
            cut.append((ax * 0.25 + bx * 0.75, ay * 0.25 + by * 0.75))
        points = cut
    return points


def eased(line: Sequence[Point], rounds: int = 2) -> Ring:
    """The same corner cutting for an open line, with both of its ends pinned.

    `smoothed` treats what it is given as a ring, which is right for a coast and wrong
    for a road: a road has two ends and they are the doorsteps of two towns, so they may
    not move. Everything between them was walked over the lattice a cell at a time and
    arrives as a staircase — long runs of one direction meeting at right angles, which is
    the one thing no road on any map has ever done.

    Cutting the corners is the same argument the coastline makes, applied to the lines
    drawn across it: the lattice is scaffolding, and what reaches the page should show
    the shape it stood for rather than the scaffold.
    """
    points = list(line)
    for _ in range(rounds):
        if len(points) < 3:
            return points
        cut: Ring = [points[0]]
        for k in range(len(points) - 1):
            ax, ay = points[k]
            bx, by = points[k + 1]
            cut.append((ax * 0.75 + bx * 0.25, ay * 0.75 + by * 0.25))
            cut.append((ax * 0.25 + bx * 0.75, ay * 0.25 + by * 0.75))
        cut.append(points[-1])
        points = cut
    return points


def closed(ring: Sequence[Point]) -> list[list[float]]:
    """A ring as the client wants it: a list of pairs, first point repeated last."""
    out = [[float(x), float(y)] for x, y in ring]
    if out and out[0] != out[-1]:
        out.append(list(out[0]))
    return out


def bounded(ring: Sequence[Point], most: int) -> Ring:
    """A ring that will still be at most `most` points once it is closed.

    Something has to stop a pathological coastline shipping ten thousand vertices to the
    browser. Raising the tolerance loses the smallest wiggles first, which is the right
    thing to lose.

    The budget counts the closing point. Every ring that reaches the client has its first
    point repeated at the end, so bounding the open ring to `most` ships `most + 1` — a
    cap that is wrong by one exactly when it binds, which is the only time anybody looks
    at it. Leaving room for the repeat here means the number means what its name says at
    the place it matters, which is the payload.
    """
    ceiling = most - 1 if most > 1 else most
    points = list(ring)
    tolerance = 0.25
    while len(points) > ceiling and tolerance < 64.0:
        points = simplify(points, tolerance)
        tolerance *= 1.6
    return points


def outlines(field: Field, level: float, *, tolerance: float = 0.5,
             smoothing: int = 2, smallest: float = 4.0,
             most: int = 240) -> list[tuple[Ring, bool]]:
    """Every shape at a threshold, tidied, each flagged as enclosing the high side.

    The flag is derived from the largest ring, which is necessarily an outer boundary:
    everything wound the same way is land, everything wound the other way is a lake.
    """
    shapes: list[tuple[Ring, float]] = []
    for ring in contours(field, level):
        simple = simplify(ring, tolerance)
        if len(simple) < 5 or area(simple) < smallest:
            continue
        shapes.append((bounded(smoothed(simple, smoothing), most), signed_area(simple)))
    if not shapes:
        return []
    shapes.sort(key=lambda pair: area(pair[0]), reverse=True)
    outward = shapes[0][1] > 0
    return [(ring, (sign > 0) == outward) for ring, sign in shapes]


def centroid(ring: Sequence[Point]) -> Point:
    """The area centroid — where a region's label wants to sit."""
    size = signed_area(ring)
    if not size:
        xs = [p[0] for p in ring] or [0.0]
        ys = [p[1] for p in ring] or [0.0]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    cx = cy = 0.0
    count = len(ring)
    for k in range(count):
        x1, y1 = ring[k]
        x2, y2 = ring[(k + 1) % count]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (cx / (6.0 * size), cy / (6.0 * size))


def contains(ring: Sequence[Point], point: Point) -> bool:
    """Ray casting, for deciding which coastline a lake sits inside."""
    x, y = point
    inside = False
    count = len(ring)
    for k in range(count):
        x1, y1 = ring[k]
        x2, y2 = ring[(k + 1) % count]
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) / (y2 - y1) * (x2 - x1)
            if crossing > x:
                inside = not inside
    return inside


def perimeter(ring: Sequence[Point]) -> float:
    count = len(ring)
    return sum(math.dist(ring[k], ring[(k + 1) % count]) for k in range(count))


def longest_axis(ring: Iterable[Point]) -> tuple[Point, Point]:
    """The two points furthest apart in a ring — the line a region's label runs along."""
    points = list(ring)
    if len(points) < 2:
        return ((0.0, 0.0), (0.0, 0.0))
    # A full pairwise pass is O(n^2); rings are bounded to a few hundred points, and
    # the honest answer matters more here than a rotating-calipers approximation.
    best = (points[0], points[1])
    far = -1.0
    for a in range(len(points)):
        for b in range(a + 1, len(points)):
            gap = math.dist(points[a], points[b])
            if gap > far:
                far, best = gap, (points[a], points[b])
    return best
