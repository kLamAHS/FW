"""Where the words go.

The generator names twenty-six natural features and then draws every one of them
without a label. The Northfells, The Iron Flow, Redweald, the Rennholt, Northmere,
Greymere, the Saltflow, the Blackrush — all worked out from the ground, all named from
the writer's own vocabulary, and none of them written anywhere on the map. Only regions
and settlements got any text at all, regions at their centroid, which for a crescent
puts the name in the sea.

What a map needs from a label is not hard to state and is easy to get wrong:

**A name has to sit on the thing it names.** A centroid is not inside a concave shape,
and a country is very often concave. The name of an area goes along its *medial spine* —
the ridge of the distance-to-edge field, which is the line furthest from every border at
once — so it is inside the shape by construction and it follows the shape's own bend.

**Text must never be upside down.** A river label follows the river, and a river that
runs east to west would print the name backwards. The reach is reversed when it does.

**Two names must never sit on each other.** Every placed label knocks out the box it
occupies, and a later one that cannot find a clear box is not placed. Which means some
names do not appear, and the order things are considered in decides *which* — so the
order is derived from what the labels are, never from what order they arrived in.

Nothing here computes an angle. A label that is not horizontal is emitted as a path for
the client to run the text along, which is both what a cartographer would do and what
lets this module keep the whole-package rule against `atan2`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from fw.core.mapgen import shapes

Point = tuple[float, float]
Box = tuple[float, float, float, float]      # x0, y0, x1, y1

# How wide each character is, as a fraction of the type size, for a serif face. Not
# measured from a font file — the client picks its own — but the shape of the table is
# what matters: an "i" is not an "m", and a label boxed as though it were reserves half
# the map. Anything not listed is an ordinary lower-case letter.
NARROW = "ijlt.,;:'`!|()[]{}I "
WIDE = "mwMW@%"
CAPS = "ABCDEFGHJKLNOPQRSTUVXYZ0123456789"
NARROW_EM = 0.30
WIDE_EM = 0.92
CAPS_EM = 0.68
ORDINARY_EM = 0.52

# A label sits a little clear of what it names and of its neighbours. In world units at
# type size 1; scaled with the label.
BREATHING_ROOM = 0.55        # of the type size, added around the knockout box
CAP_HEIGHT = 0.72            # of the type size — what the box is tall
DESCENDER = 0.22

# How far a point label is offered away from its point, in multiples of the icon radius.
# The order is the order a cartographer tries: upper right first, then the rest.
OFFSETS: tuple[tuple[float, float, str], ...] = (
    (1.0, -0.35, "start"),      # to the right, slightly high — the default
    (-1.0, -0.35, "end"),       # to the left
    (1.0, 1.15, "start"),       # below right
    (-1.0, 1.15, "end"),
    (1.0, -1.35, "start"),      # above right
    (-1.0, -1.35, "end"),
    (0.0, -1.55, "middle"),     # straight above
    (0.0, 1.75, "middle"),      # straight below
)

# How fine the lattice is that a spine is found on. 56 across the longer side of the
# shape's own box: fine enough that the ridge of a long thin march is a line rather than
# a dot, coarse enough that a big region costs a few thousand point-in-polygon tests.
SPINE_CELLS = 56
CHAMFER_ORTH = 3             # the (3, 4) chamfer: integer, exact, and within 2% of
CHAMFER_DIAG = 4             # Euclidean, which is far closer than the lattice itself
# How narrow the shape may get before the spine stops, as a share of its widest point.
SPINE_FLOOR = 0.40
# How much better an off-level step has to be before the spine takes it.
LEVEL_PREFERENCE = 0.82
# How small a name may be set to make it fit, as a share of the size it asked for.
SHRINK_TO = 0.6
# How far past the width of what it names a level name may reach, and the size below
# which it is not worth setting at all.
OVERHANG = 1.35
LEGIBLE = 7.0


@dataclass(frozen=True)
class Wanted:
    """A label somebody wants placed, with everything needed to place it.

    Exactly one of `point`, `ring` and `line` is set, and which one it is decides how
    the label is laid out: beside a place, along the spine of an area, or down a reach.
    """

    key: str
    text: str
    kind: str                       # region | sea | settlement | castle | river | ...
    tier: int                       # 0 must be placed, 2 is placed if there is room
    role: str                       # a CSS role; this module emits no colours
    size: float                     # type size, in world units
    point: Point | None = None
    ring: tuple[Point, ...] = ()
    line: tuple[Point, ...] = ()
    clearance: float = 0.0          # the icon's radius, for a point label
    weight: float = 0.0             # bigger is placed first inside a tier

    def order(self) -> tuple:
        """The order labels are considered in — from what they are, never from when.

        A solver whose output depends on the order its input arrived in is a map whose
        names move when a row is added to a table.
        """
        return (self.tier, -self.weight, -self.size, self.kind, self.text, self.key)


@dataclass(frozen=True)
class Placed:
    """One label, where it goes and what shape it takes."""

    key: str
    text: str
    kind: str
    tier: int
    role: str
    size: float
    x: float
    y: float
    anchor: str = "middle"          # start | middle | end
    # When set, the client runs the text along this path instead of putting it at (x, y).
    # A path rather than an angle: a country's name should bend the way the country
    # does, and a rotation cannot do that.
    path: tuple[Point, ...] = ()
    boxes: tuple[Box, ...] = ()

    def as_dict(self, *, debug: bool = False) -> dict:
        out: dict = {"key": self.key, "text": self.text, "kind": self.kind,
                     "tier": self.tier, "role": self.role,
                     "size": round(self.size, 2),
                     "x": round(self.x, 1), "y": round(self.y, 1),
                     "anchor": self.anchor}
        if self.path:
            out["path"] = [[round(x, 1), round(y, 1)] for x, y in self.path]
        if debug:
            # The reserved boxes, for the debug overlay only — the ordinary payload
            # does not carry the solver's scaffolding to every client on every day.
            out["boxes"] = [[round(v, 1) for v in box] for box in self.boxes]
        return out


@dataclass(frozen=True)
class Dropped:
    """One name that is not on the map, and the reason it is not.

    The reason is the point (V2 §50): a bare key told the writer nothing, and the
    legend's old copy — "zoom in for them" — described a behaviour the map did not
    have.
    """

    key: str
    text: str
    kind: str
    tier: int
    reason: str          # "no room" | "nothing to hang it on"

    def as_dict(self) -> dict:
        return {"key": self.key, "text": self.text, "kind": self.kind,
                "tier": self.tier, "reason": self.reason}


@dataclass
class Reserved:
    """The boxes already spoken for, and whether a new one fits.

    A uniform bucket grid rather than a list: a map with four hundred labels tests each
    candidate against the handful in its own neighbourhood instead of against all of
    them, and the placement is identical either way.
    """

    cell: float = 40.0
    buckets: dict[tuple[int, int], list[Box]] = field(default_factory=dict)

    def free(self, box: Box) -> bool:
        for key in self._keys(box):
            for other in self.buckets.get(key, ()):
                if (box[0] < other[2] and other[0] < box[2]
                        and box[1] < other[3] and other[1] < box[3]):
                    return False
        return True

    def take(self, box: Box) -> None:
        for key in self._keys(box):
            self.buckets.setdefault(key, []).append(box)

    def _keys(self, box: Box):
        for j in range(int(box[1] // self.cell), int(box[3] // self.cell) + 1):
            for i in range(int(box[0] // self.cell), int(box[2] // self.cell) + 1):
                yield (i, j)


def solve(wanted: list[Wanted], *, reserved: Reserved | None = None,
          ) -> tuple[tuple[Placed, ...], tuple[Dropped, ...]]:
    """Place what fits, in a stated order, and say what did not — and why not.

    Greedy: every label is offered its candidate positions in a cartographer's order of
    preference and takes the first that is clear. Greedy is the right shape here because
    the alternative — a global optimisation over label positions — is NP-hard, takes
    orders of magnitude longer, and produces an arrangement nobody can predict from the
    map. What matters far more than optimality is that the *same* map always labels the
    same way, which is what `Wanted.order` is for.
    """
    taken = reserved if reserved is not None else Reserved()
    placed: list[Placed] = []
    dropped: list[Dropped] = []
    for want in sorted(wanted, key=Wanted.order):
        got = _place(want, taken)
        if got is None:
            shapeless = want.point is None and not want.ring and not want.line
            dropped.append(Dropped(
                key=want.key, text=want.text, kind=want.kind, tier=want.tier,
                reason="nothing to hang it on" if shapeless else "no room"))
            continue
        for box in got.boxes:
            taken.take(box)
        placed.append(got)
    return tuple(placed), tuple(dropped)


def _place(want: Wanted, taken: Reserved) -> Placed | None:
    if want.point is not None:
        return _beside(want, taken)
    if want.ring:
        return _across(want, taken)
    if want.line:
        return _along(want, taken)
    return None


# ---- a name beside a place -------------------------------------------------

def _beside(want: Wanted, taken: Reserved) -> Placed | None:
    """A settlement's name, offered the eight positions around its icon."""
    x, y = want.point or (0.0, 0.0)
    width = width_of(want.text, want.size)
    gap = max(want.clearance, want.size * 0.5) + want.size * 0.35
    for dx, dy, anchor in OFFSETS:
        cx = x + dx * gap
        cy = y + dy * gap + want.size * CAP_HEIGHT * 0.5
        box = _box(cx, cy, width, want.size, anchor)
        if taken.free(box):
            return Placed(key=want.key, text=want.text, kind=want.kind, tier=want.tier,
                          role=want.role, size=want.size, x=round(cx, 1),
                          y=round(cy, 1), anchor=anchor, boxes=(box,))
    return None


# ---- a name across an area -------------------------------------------------

def _across(want: Wanted, taken: Reserved) -> Placed | None:
    """A region's or a sea's name, along the line furthest from all of its edges."""
    path = spine(want.ring)
    if len(path) < 2:
        return _at_the_pole(want, taken)
    length = _length(path)
    size = want.size
    width = width_of(want.text, size)
    # Shrink to fit rather than refuse: a name too big for its country is the map
    # shouting, and a name a little small still tells the reader whose ground this is.
    while width > length * 0.92 and size > want.size * SHRINK_TO:
        size *= 0.9
        width = width_of(want.text, size)
    if width > length * 0.98:
        # Too long for the shape at any readable size. A crescent is the ordinary case:
        # its spine is the horizontal band through its fattest part, and the arms curl
        # away from any line a name could run along. Set it level at the widest point
        # instead, overhanging the border if it must — which is what an atlas does with
        # a small country, and better than leaving the country unnamed.
        return _at_the_pole(want, taken, mid=_midpoint(path))

    trimmed = _centred(path, width)
    boxes = _boxes_along(trimmed, size)
    if not all(taken.free(box) for box in boxes):
        # One more try, straight across the middle: a spine blocked by a river's label
        # is common and a country with no name at all is worse than a level one.
        mid = trimmed[len(trimmed) // 2]
        flat = _box(mid[0], mid[1], width, size, "middle")
        if not taken.free(flat):
            return None
        return Placed(key=want.key, text=want.text, kind=want.kind, tier=want.tier,
                      role=want.role, size=size, x=round(mid[0], 1),
                      y=round(mid[1], 1), anchor="middle", boxes=(flat,))
    mid = trimmed[len(trimmed) // 2]
    return Placed(key=want.key, text=want.text, kind=want.kind, tier=want.tier,
                  role=want.role, size=size, x=round(mid[0], 1), y=round(mid[1], 1),
                  anchor="middle", path=_curve_or_nothing(trimmed, size),
                  boxes=tuple(boxes))


def _at_the_pole(want: Wanted, taken: Reserved,
                 mid: Point | None = None) -> Placed | None:
    """The name set level at the point furthest from every edge.

    Sized to the shape, not to what the kind of thing usually gets. A skerry the size of
    a full stop is still an island and still has a name, and setting it in the type a
    continent gets writes a word across the sea four times the length of the rock it
    belongs to.
    """
    where = mid or pole(want.ring)
    if where is None:
        return None
    size = _fitted(want.text, want.size, want.ring)
    width = width_of(want.text, size)
    box = _box(where[0], where[1], width, size, "middle")
    if not taken.free(box):
        return None
    return Placed(key=want.key, text=want.text, kind=want.kind, tier=want.tier,
                  role=want.role, size=size, x=round(where[0], 1),
                  y=round(where[1], 1), anchor="middle", boxes=(box,))


def _fitted(text: str, size: float, ring: tuple[Point, ...]) -> float:
    """The largest type that keeps a name in proportion to the thing it names.

    A name may overhang a small country a little — an atlas does that — but not by
    multiples of its width, and never below the size at which it stops being readable.
    """
    if not ring:
        return size
    across = max(p[0] for p in ring) - min(p[0] for p in ring)
    if across <= 0:
        return size
    room = across * OVERHANG
    natural = width_of(text, size)
    if natural <= room:
        return size
    return max(LEGIBLE, size * room / natural)


def _midpoint(path: tuple[Point, ...] | list[Point]) -> Point:
    return path[len(path) // 2]


def pole(ring: tuple[Point, ...], *, cells: int = SPINE_CELLS) -> Point | None:
    """The centre of the largest circle the shape will hold.

    Where a name goes when it will not follow a line — and, unlike a centroid, always
    inside the shape.
    """
    inside, size, x0, y0, wide, tall = _raster(ring, cells)
    if not any(inside):
        return None
    far = _distance(inside, wide, tall)
    best = max(range(len(far)), key=lambda k: (far[k], -k))
    return (x0 + (best % wide + 0.5) * size, y0 + (best // wide + 0.5) * size)


def spine(ring: tuple[Point, ...], *, cells: int = SPINE_CELLS) -> tuple[Point, ...]:
    """The line down the middle of a shape, for its name to sit on.

    The ridge of the distance-to-edge field. Start at the point furthest from any edge —
    which is the centre of the largest circle the shape will hold — and walk outwards
    both ways, each step to whichever forward neighbour is furthest from an edge. What
    comes back is inside the shape by construction, however concave the shape is, and
    bends the way it bends.

    A centroid, which is what this replaces, is outside a crescent altogether.
    """
    inside, size, x0, y0, wide, tall = _raster(ring, cells)
    return spine_of_mask(inside, size, x0, y0, wide, tall)


def spine_of_mask(inside: list[bool], size: float, x0: float, y0: float,
                  wide: int, tall: int) -> tuple[Point, ...]:
    """The same walk, over a mask somebody else rasterised.

    The sea has no ring — it is everything the land is not — and a sea wants its name
    across it as much as a country does.
    """
    if not any(inside):
        return ()
    far = _distance(inside, wide, tall)
    best = max(range(len(far)), key=lambda k: (far[k], -k))
    if far[best] <= CHAMFER_ORTH:
        return ()

    # Stop before the shape narrows to nothing. The true medial axis of a square is an
    # X through its centre, so a walk that simply follows the ridge dives into a corner
    # and comes out as a diagonal name across a level country. A floor set from the
    # widest point keeps the spine in the part of the shape a name can live in.
    floor = max(CHAMFER_ORTH + 1, far[best] * SPINE_FLOOR)
    walk = _ridge(far, wide, tall, best, +1, floor)
    walk = list(reversed(_ridge(far, wide, tall, best, -1, floor)))[:-1] + walk
    world = [(x0 + (k % wide + 0.5) * size, y0 + (k // wide + 0.5) * size)
             for k in walk]
    if len(world) < 2:
        return ()
    # Smoothed once and thinned: the raw walk is a staircase on the lattice, and a name
    # running along a staircase reads as a name someone has damaged.
    eased = shapes.eased(world, rounds=2)
    return tuple(shapes.simplify(eased, size * 0.35)) or tuple(eased)


def _raster(ring: tuple[Point, ...], cells: int):
    """The shape, as a boolean lattice over its own bounding box.

    With a one-cell margin of outside all the way round. Without it a shape that fills
    its own bounding box — a square march, a rectangle the writer drew — has no outside
    above its top row, so the distance transform never starts there and the ridge comes
    out along the top edge instead of down the middle.
    """
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    span = max(x1 - x0, y1 - y0)
    if span <= 0:
        return [], 1.0, x0, y0, 1, 1
    size = span / cells
    wide = max(1, int((x1 - x0) / size) + 1) + 2
    tall = max(1, int((y1 - y0) / size) + 1) + 2
    x0 -= size
    y0 -= size
    inside = [shapes.contains(ring, (x0 + (k % wide + 0.5) * size,
                                     y0 + (k // wide + 0.5) * size))
              for k in range(wide * tall)]
    return inside, size, x0, y0, wide, tall


def _distance(inside: list[bool], wide: int, tall: int) -> list[int]:
    """How far each cell is from the outside, by a two-pass (3, 4) chamfer.

    Integer arithmetic throughout, so it is exactly the same on every machine — which a
    Euclidean transform, or anything with a square root in it, would not be.
    """
    big = 1 << 24
    far = [0 if not v else big for v in inside]
    for j in range(tall):
        for i in range(wide):
            k = j * wide + i
            if not far[k]:
                continue
            best = far[k]
            if i:
                best = min(best, far[k - 1] + CHAMFER_ORTH)
            if j:
                best = min(best, far[k - wide] + CHAMFER_ORTH)
                if i:
                    best = min(best, far[k - wide - 1] + CHAMFER_DIAG)
                if i + 1 < wide:
                    best = min(best, far[k - wide + 1] + CHAMFER_DIAG)
            far[k] = best
    for j in range(tall - 1, -1, -1):
        for i in range(wide - 1, -1, -1):
            k = j * wide + i
            if not far[k]:
                continue
            best = far[k]
            if i + 1 < wide:
                best = min(best, far[k + 1] + CHAMFER_ORTH)
            if j + 1 < tall:
                best = min(best, far[k + wide] + CHAMFER_ORTH)
                if i:
                    best = min(best, far[k + wide - 1] + CHAMFER_DIAG)
                if i + 1 < wide:
                    best = min(best, far[k + wide + 1] + CHAMFER_DIAG)
            far[k] = best
    return far


def _ridge(far: list[int], wide: int, tall: int, start: int, step: int,
           floor: float) -> list[int]:
    """Walk from the widest point outwards, one column at a time.

    Column at a time rather than free wandering, because a label runs across a shape and
    not around it: a walk allowed to go any direction follows the ridge into a spiral in
    anything shaped like a ring. And level unless a step up or down is clearly better —
    a line that changes row to gain a hundredth of a cell reads as a wobble in the name.
    """
    walk = [start]
    here = start
    while True:
        i, j = here % wide, here // wide
        nxt = i + step
        if not 0 <= nxt < wide:
            break
        best: tuple[float, int] | None = None
        for dj in (0, -1, 1):
            if not 0 <= j + dj < tall:
                continue
            k = nxt + (j + dj) * wide
            if far[k] < floor:
                continue
            score = far[k] * (1.0 if dj == 0 else LEVEL_PREFERENCE)
            if best is None or score > best[0] or (score == best[0] and k < best[1]):
                best = (score, k)
        if best is None:
            break
        here = best[1]
        walk.append(here)
    return walk


# ---- a name along a reach --------------------------------------------------

def _along(want: Wanted, taken: Reserved) -> Placed | None:
    """A river's or a road's name, following it, and never upside down."""
    line = list(want.line)
    if len(line) < 2:
        return None
    # Text runs left to right, so a reach that runs right to left is read along
    # backwards. Reversing the *path* is the whole fix, and it is invisible to a reader.
    if line[-1][0] < line[0][0]:
        line.reverse()
    width = width_of(want.text, want.size)
    if _length(line) < width * 1.05:
        return None
    trimmed = _centred(line, width)
    boxes = _boxes_along(trimmed, want.size)
    if not all(taken.free(box) for box in boxes):
        return None
    mid = trimmed[len(trimmed) // 2]
    return Placed(key=want.key, text=want.text, kind=want.kind, tier=want.tier,
                  role=want.role, size=want.size, x=round(mid[0], 1),
                  y=round(mid[1], 1), anchor="middle",
                  path=_curve_or_nothing(trimmed, want.size), boxes=tuple(boxes))


# ---- geometry and metrics --------------------------------------------------

def width_of(text: str, size: float) -> float:
    """How wide a piece of text will be, near enough to reserve room for it."""
    total = 0.0
    for ch in text:
        if ch in NARROW:
            total += NARROW_EM
        elif ch in WIDE:
            total += WIDE_EM
        elif ch in CAPS:
            total += CAPS_EM
        else:
            total += ORDINARY_EM
    return total * size


def _box(x: float, y: float, width: float, size: float, anchor: str) -> Box:
    room = size * BREATHING_ROOM
    left = {"start": x, "middle": x - width / 2, "end": x - width}[anchor]
    top = y - size * CAP_HEIGHT
    return (left - room, top - room * 0.5,
            left + width + room, y + size * DESCENDER + room * 0.5)


def _curve_or_nothing(path: list[Point], size: float) -> tuple[Point, ...]:
    """A path only when the label really bends.

    A straight horizontal line is a `textPath` that draws exactly like plain text and
    gives up letter-spacing and hinting to do it. Most region names come out level, so
    most of them should be ordinary text.
    """
    ys = [y for _, y in path]
    if max(ys) - min(ys) <= size * 0.20:
        return ()
    return tuple(path)


def _boxes_along(path: list[Point], size: float) -> list[Box]:
    """A run of small boxes down a label's path, rather than one big one.

    One box round a bent label reserves the whole bend, which on a river that turns a
    corner is most of a province. A chain of them follows the text instead.

    Sampled at a fixed spacing rather than taken at the path's own vertices: a spine
    that came back straight is two points, and boxing only those reserved the two ends
    of a country's name and left every letter between them free — so a town's label sat
    squarely in the middle of "THE KINGDOM OF RENN" and both were drawn.
    """
    room = size * (CAP_HEIGHT + BREATHING_ROOM) * 0.5
    return [(x - room, y - room, x + room, y + room)
            for x, y in _sampled(path, room)]


def _sampled(path: list[Point] | tuple[Point, ...], step: float) -> list[Point]:
    """The path, with a point at least every `step` along it."""
    pts = list(path)
    if len(pts) < 2 or step <= 0:
        return pts
    out: list[Point] = [pts[0]]
    for (ax, ay), (bx, by) in zip(pts, pts[1:], strict=False):
        run = math.dist((ax, ay), (bx, by))
        if run <= 0:
            continue
        for n in range(1, int(run / step) + 1):
            t = n * step / run
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
        out.append((bx, by))
    return out


def _length(points: list[Point] | tuple[Point, ...]) -> float:
    total = 0.0
    for a, b in zip(points, points[1:], strict=False):
        total += math.dist(a, b)
    return total


def _centred(points: list[Point] | tuple[Point, ...], width: float) -> list[Point]:
    """The middle `width` of a path, so the text sits in the middle of what it names."""
    pts = list(points)
    total = _length(pts)
    if total <= width:
        return pts
    skip = (total - width) / 2
    out: list[Point] = []
    walked = 0.0
    for (ax, ay), (bx, by) in zip(pts, pts[1:], strict=False):
        step = math.dist((ax, ay), (bx, by))
        if step <= 0:
            continue
        start, end = walked, walked + step
        walked = end
        if end < skip or start > skip + width:
            continue
        lo = max(0.0, (skip - start) / step)
        hi = min(1.0, (skip + width - start) / step)
        first = (ax + (bx - ax) * lo, ay + (by - ay) * lo)
        last = (ax + (bx - ax) * hi, ay + (by - ay) * hi)
        if not out:
            out.append(first)
        out.append(last)
    return out if len(out) >= 2 else pts
