"""Building one landmass, and finding its coastline.

The first generator drew each region as a polygon of forty-four rays cast from its
centre. A star polygon cannot be concave enough for a bay, cannot enclose an island,
and cannot share an edge with its neighbour — which is why its maps had open water
between regions that border each other, and coastlines made of smooth lumps. That is
not a tuning problem; it is what the shape can express.

So the land is built as one scalar field and its coastline is contoured out of it.
Bays, gulfs, capes, peninsulas, isthmuses, offshore islands and inland lakes all fall
out of one pass, because none of them are special cases: they are all just where the
field crosses sea level.

Where the land *is* comes from the writer. Distance to a radial shelf makes a disc;
distance to the graph they described — this region borders that one, this one is on the
sea — makes a continent shaped like their world: elongated where their regions run in a
line, branched where one borders three, pinched into a neck where two halves are joined
by a single border. Four things had to be right, each found by looking at the picture:

  * regions are lobes and borders are corridors, or the land reads as a sausage;
  * three regions that all border each other need the triangle between them filled, or a
    densely-connected world grows lakes in its middle;
  * the *distance* is warped, not merely the height — a distance field ramps smoothly,
    so its level set is a circle and no amount of added noise moves the coast far;
  * coastal detail follows the writer's own prose, so a mountainous coast is deeply
    indented and a plain one is smooth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from fw.core.mapgen import noise, shapes
from fw.core.mapgen.grid import Field, Grid, disc, line

SEA_LEVEL = 0.22

# How far land reaches around a region's heart, and how wide it is along a mere border,
# as fractions of the lattice. A border means two regions touch, not that they are
# joined by a tube, so the corridor is nearly as wide as the lobe.
LOBE = 0.125
NECK = 0.115
HEART = 0.30                  # the fraction of a lobe seeded at zero distance

MARGIN = 0.82                 # nothing may reach past this fraction of the half-width
SMALLEST_ISLAND = 4.0         # in square lattice cells


@dataclass
class Landform:
    """The land, as both a field and a set of drawable shapes."""

    grid: Grid
    height: Field
    sea: list[list[bool]]
    rings: tuple[tuple[list[tuple[float, float]], bool], ...] = ()
    anchors: dict[str, tuple[int, int]] = dataclass_field(default_factory=dict)
    land_cells: int = 0
    notes: list[str] = dataclass_field(default_factory=list)

    def coastlines(self):
        """The outer rings — the shores of the land."""
        return tuple(ring for ring, is_land in self.rings if is_land)

    def inland_waters(self):
        """Rings wound the other way: lakes and inland seas."""
        return tuple(ring for ring, is_land in self.rings if not is_land)


def build(grid: Grid, *, anchors: dict[str, tuple[int, int]],
          weights: dict[str, float], roughness: dict[str, float],
          borders: set[tuple[str, str]], seed: str) -> Landform:
    """Grow a continent around the skeleton the writer's borders describe."""
    size = grid.size
    keys = sorted(anchors)
    if not keys:
        return Landform(grid=grid, height=grid.filled(-1.0),
                        sea=[[True] * size for _ in range(size)])

    lobe = max(4.0, size * LOBE)
    neck = max(3.0, size * NECK)

    hearts: list[tuple[int, int]] = []
    for key in keys:
        hearts.extend(disc(anchors[key], lobe * HEART * max(weights.get(key, 1.0), 0.3)))
    corridors = _corridors(anchors, borders, keys)

    to_heart = grid.distance_from(hearts)
    to_border = grid.distance_from(corridors) if corridors else None
    owner = grid.claimed_by([(anchors[key], max(weights.get(key, 1.0), 0.2))
                             for key in keys])

    half = (size - 1) / 2.0
    broken = [min(1.0, roughness.get(key, 0.1) * 3.2) for key in keys]
    height = grid.filled()
    for j in range(size):
        for i in range(size):
            rough = broken[owner[j][i]] if 0 <= owner[j][i] < len(broken) else 0.2
            # Warping the distance, not the height. A distance field ramps smoothly, so
            # its level set is a circle whatever noise is added on top; stretching and
            # squeezing the distance itself moves the coastline in and out by tens of
            # cells, which is what a bay actually is.
            pull = 0.55 + 1.15 * noise.fbm(f"{seed}|warp", i / 22.0, j / 22.0, octaves=4)
            grain = noise.fbm(f"{seed}|shore", i / (11.0 - 4.0 * rough),
                              j / (11.0 - 4.0 * rough), octaves=3) - 0.5
            core = 1.0 - min(2.0, to_heart[j][i] * pull / lobe)
            if to_border is not None:
                # A corridor is warped far less than a lobe. At full strength the warp
                # can pinch a border in two, and a continent that comes apart where the
                # writer said two regions meet is not a map of their world — it also
                # sent the roads between them straight across open water.
                steady = 0.85 + 0.30 * noise.fbm(f"{seed}|warp", i / 22.0, j / 22.0,
                                                 octaves=4)
                core = max(core, 1.0 - min(2.0, to_border[j][i] * steady / neck))
            dx, dy = abs(i - half) / half, abs(j - half) / half
            edge = 1.0 - max(0.0, (max(dx, dy) - MARGIN) / (1.0 - MARGIN))
            height[j][i] = (0.62 * core + (0.16 + 0.28 * rough) * grain) * edge

    # One light blur. Contouring the raw field threads single-cell channels and grows
    # hairs off the coast; this removes them without touching anything read as a bay.
    height = grid.blurred(height)
    sea = [[height[j][i] <= SEA_LEVEL for i in range(size)] for j in range(size)]
    rings = shapes.outlines(height, SEA_LEVEL, smallest=SMALLEST_ISLAND)
    form = Landform(grid=grid, height=height, sea=sea, rings=tuple(rings),
                    land_cells=sum(1 for row in sea for wet in row if not wet))
    # Settle the hearts before asking whether the land is in pieces: a heart the
    # coastline drowned is not a severed region, it is a region standing in the water,
    # and reporting the one as the other sends the reader looking for the wrong bug.
    form.anchors = settle_anchors(form, anchors)
    form.notes.extend(_severed(grid, sea, form.anchors, keys))
    return form


def _severed(grid: Grid, sea, anchors: dict[str, tuple[int, int]],
             keys: list[str]) -> list[str]:
    """Regions the land left on separate islands, when nobody said they were islands.

    Said out loud rather than quietly repaired: the writer's own borders are what tie
    the continent together, so land in pieces usually means their notes are in pieces.
    """
    size = grid.size
    label = [[-1] * size for _ in range(size)]
    groups = 0
    for j in range(size):
        for i in range(size):
            if sea[j][i] or label[j][i] >= 0:
                continue
            stack = [(i, j)]
            label[j][i] = groups
            while stack:
                ci, cj = stack.pop()
                for ni, nj in grid.neighbours(ci, cj):
                    if not sea[nj][ni] and label[nj][ni] < 0:
                        label[nj][ni] = groups
                        stack.append((ni, nj))
            groups += 1
    homes: dict[int, list[str]] = {}
    for key in keys:
        i, j = anchors[key]
        if grid.holds(i, j) and not sea[j][i]:
            homes.setdefault(label[j][i], []).append(key)
    if len(homes) <= 1:
        return []
    parts = ["/".join(sorted(names)) for _, names in sorted(homes.items())]
    return [f"the land came out in {len(homes)} pieces: {'; '.join(parts)}"]


def settle_anchors(form: Landform,
                   anchors: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
    """Move any region's heart that ended up offshore to the nearest dry ground.

    The layout places a region before the land exists, and the coastline is warped
    afterwards, so a heart can end up under water — and a region whose heart is at sea
    claims nothing at all and vanishes from the map without a word. It keeps its place
    in the world; it just stands on the nearest ground.
    """
    grid = form.grid
    out: dict[str, tuple[int, int]] = {}
    for key in sorted(anchors):
        i, j = anchors[key]
        i = max(0, min(grid.size - 1, i))
        j = max(0, min(grid.size - 1, j))
        if not form.sea[j][i]:
            out[key] = (i, j)
            continue
        out[key] = _nearest_land(form, (i, j)) or (i, j)
    return out


def _nearest_land(form: Landform, start: tuple[int, int]) -> tuple[int, int] | None:
    """Breadth-first, so it is the nearest and always the same one."""
    grid = form.grid
    seen = {start}
    frontier = [start]
    while frontier:
        nxt: list[tuple[int, int]] = []
        for cell in frontier:
            for neighbour in grid.neighbours(*cell):
                if neighbour in seen:
                    continue
                seen.add(neighbour)
                ni, nj = neighbour
                if not form.sea[nj][ni]:
                    return neighbour
                nxt.append(neighbour)
        frontier = sorted(nxt)
    return None


def _corridors(anchors: dict[str, tuple[int, int]],
               borders: set[tuple[str, str]], keys: list[str]) -> list[tuple[int, int]]:
    """The ground that must be land because the writer said two regions meet on it.

    Border lines, plus the triangle between any three regions that all border each
    other. Without the triangles a densely connected world came out with large lakes in
    the middle of the continent, in the gaps its three-way corners left behind.
    """
    known = set(keys)
    adjacency: dict[str, set[str]] = {}
    cells: list[tuple[int, int]] = []
    for a, b in sorted(borders):
        if a not in known or b not in known or a == b:
            continue
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)
        cells.extend(line(anchors[a], anchors[b]))

    linked = sorted(adjacency)
    for x in range(len(linked)):
        for y in range(x + 1, len(linked)):
            if linked[y] not in adjacency[linked[x]]:
                continue
            for z in range(y + 1, len(linked)):
                if (linked[z] in adjacency[linked[x]]
                        and linked[z] in adjacency[linked[y]]):
                    cells.extend(_triangle(anchors[linked[x]], anchors[linked[y]],
                                           anchors[linked[z]]))

    cells.extend(_join_the_rest(anchors, adjacency, keys))
    return cells


def _join_the_rest(anchors: dict[str, tuple[int, int]],
                   adjacency: dict[str, set[str]],
                   keys: list[str]) -> list[tuple[int, int]]:
    """Join whatever the writer's borders left in separate pieces.

    A world is one world unless its writer says otherwise. Someone who has named ten
    regions and no borders at all wants a continent, not an archipelago — and someone
    whose borders describe two clusters has simply not got round to saying how the two
    halves meet. Joining nearest pair to nearest pair is the smallest assumption that
    answers both; a region that really is an island says so in its own terrain, and
    that is a different mechanism.
    """
    parent = {key: key for key in keys}

    def find(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for key in keys:
        for other in sorted(adjacency.get(key, ())):
            if other in parent:
                parent[find(key)] = find(other)

    candidates = sorted(
        (math.dist(anchors[a], anchors[b]), a, b)
        for n, a in enumerate(keys) for b in keys[n + 1:])
    cells: list[tuple[int, int]] = []
    for _distance, a, b in candidates:
        if find(a) == find(b):
            continue
        parent[find(a)] = find(b)
        cells.extend(line(anchors[a], anchors[b]))
    return cells


def _triangle(a: tuple[int, int], b: tuple[int, int],
              c: tuple[int, int]) -> list[tuple[int, int]]:
    """Every cell inside a triangle, by a barycentric test over its bounding box."""
    (ax, ay), (bx, by), (cx, cy) = a, b, c
    twice = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    if not twice:
        return []
    out: list[tuple[int, int]] = []
    for j in range(min(ay, by, cy), max(ay, by, cy) + 1):
        for i in range(min(ax, bx, cx), max(ax, bx, cx) + 1):
            u = ((bx - ax) * (j - ay) - (i - ax) * (by - ay)) / twice
            v = ((i - ax) * (cy - ay) - (cx - ax) * (j - ay)) / twice
            if u >= 0.0 and v >= 0.0 and u + v <= 1.0:
                out.append((i, j))
    return out
