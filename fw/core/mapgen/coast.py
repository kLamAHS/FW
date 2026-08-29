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

The second thing that had to change was the order. The land used to be grown *around*
the writer's regions — a lobe per region, a corridor per stated border — and that is
backwards. A continent is not the union of its provinces. Three regions in a line gave a
continent shaped like a sausage, fifteen per cent of the canvas, because that is what
three lobes in a line are; and every fact about the resulting map, down to which coast
got the rain, was a consequence of a political arrangement rather than of any geography.

So the landmass is shaped first, and knows nothing about who lives on it. It is a few
overlapping masses under a heavy domain warp, cut off at a sea level chosen so the
continent fills the map. The writer's regions are then *placed on it*, and the ground is
divided among them. Politics is downstream of geography, which is the right way round and
also the only way the coastline can be interesting: a shore that has to bulge out to
every region's heart cannot have a deep gulf in it, because some region's heart is
always in the way.

Four things had to be right, each found by looking at the picture:

  * the coast is warped in the *domain*, not the height — a distance field ramps
    smoothly, so its level set is a circle and no amount of noise added on top moves the
    shore far. Displacing where the field is *sampled* moves it by tens of cells, which
    is what a gulf actually is;
  * the sea level is a quantile, not a constant, so the continent is the same size
    whatever the noise happened to do;
  * a coast varies along its own length — drowned and fjorded here, a smooth
    sedimentary sweep there — because a coastline of uniform roughness reads as a
    texture rather than as a place;
  * land is pushed away from the canvas edge, or the map reads as a crop of a bigger
    one that was never drawn.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from fw.core.mapgen import noise, shapes
from fw.core.mapgen.grid import Field, Grid
from fw.core.mapgen.grid import sample as grid_sample

SEA_LEVEL = 0.22              # nominal only: the working level is a quantile, below

# The share of the canvas that ends up dry. Chosen by eye against the printed maps a
# novelist has in mind, which are almost always a continent with a margin of sea around
# it rather than a coastline crossing the page.
LAND_SHARE = 0.56

# The masses the continent is made of, as fractions of the lattice. Real continents are
# not one blob: they are old cores that collided, which is why they have gulfs between
# their lobes and peninsulas trailing off them. Three or four overlapping masses of
# unequal size give that; one gives a potato.
MASSES = (3, 5)               # how many, inclusive
MASS_RADIUS = (0.30, 0.46)    # each mass's reach, as a fraction of the half-width
MASS_SPREAD = 0.36            # how far their centres wander from the middle

# How far the sampling point is displaced, in lattice cells, and over what distance the
# displacement itself varies. This is the number that decides whether the coast has
# gulfs and peninsulas or merely bumps.
WARP_REACH = 0.20             # as a fraction of the lattice
WARP_SCALE = 0.30             # ditto: the wavelength of the warp itself

# The character of the shore, varying along its own length: how deeply indented, and
# over what distance that character changes.
REGIME_SCALE = 0.42
REGIME_LOW = 0.35             # a sedimentary sweep
REGIME_HIGH = 1.30            # a drowned, fjorded coast

SHORE_SCALE = 0.075           # wavelength of the coastal detail, as a fraction
SHORE_WEIGHT = 0.30           # how much of the field it is

# How much a region the writer drew by hand lifts the ground under it, and over how
# many cells that lift comes in from the traced edge.
HOLD_CLEARANCE = 0.20         # how far above the waterline drawn ground is lifted
HOLD_LOW = 0.34               # below this share of the swell, the writer drew nothing
HOLD_HIGH = 0.86              # above it, they certainly did
HOLD_QUANTILE = 0.22          # the share of drawn ground that may still end up wet
HOLD_GRAIN_LOW = 0.62         # how much of the lift is unroughened
HOLD_GRAIN_HIGH = 0.78
HOLD_GRAIN_SCALE = 0.085      # wavelength of that roughening, as a fraction
HOLD_STRIDE = 5
HOLD_ROUNDS = 7

MARGIN = 0.70                 # nothing may reach past this fraction of the half-width
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
          borders: set[tuple[str, str]], seed: str,
          must_hold: list[list[tuple[int, int]]] | None = None) -> Landform:
    """Shape one continent, then find its shore.

    `anchors` and `weights` no longer decide where the land is — the continent is shaped
    from the seed alone, so that a writer who adds a fourth region does not get a
    differently-shaped world. What they are still for is settling: a region's heart is
    moved onto the ground afterwards, and `must_hold` names any cells a drawn region
    occupies, which are held above sea level whatever the noise wanted.
    """
    size = grid.size
    keys = sorted(anchors)
    if not keys:
        return Landform(grid=grid, height=grid.filled(-1.0),
                        sea=[[True] * size for _ in range(size)])

    height = _mass(grid, seed)

    # The waterline is settled from the continent alone, *before* anything the writer
    # drew is honoured. Otherwise a world with three drawn provinces and a world with
    # none get differently-sized continents for no geographical reason: the lift shifts
    # the quantile, and the sea rises to compensate somewhere the writer never mentioned.
    level = _waterline(height, size, LAND_SHARE)
    if must_hold:
        # Lifting drawn ground adds land, which moves the waterline, which changes how
        # much lifting the drawn ground needed. Two passes settle it: the lift targets an
        # absolute height rather than a relative one, so re-running it against the new
        # level converges instead of compounding. Without this the continent grew to fill
        # the canvas — three provinces on a map is not a reason for the sea to retreat.
        for _ in range(2):
            _hold(grid, height, must_hold, level, seed)
            level = _waterline(height, size, LAND_SHARE)

    # One light blur. Contouring the raw field threads single-cell channels and grows
    # hairs off the coast; this removes them without touching anything read as a bay.
    height = grid.blurred(height)
    sea = [[height[j][i] <= level for i in range(size)] for j in range(size)]
    if must_hold:
        # A drawn region is the writer's word and outranks the quantile. Held cells are
        # dry even where the blur pulled them under.
        for cells in must_hold:
            for i, j in cells:
                if 0 <= i < size and 0 <= j < size:
                    sea[j][i] = False

    # The field the rest of the generator reads is re-based so that sea level is exactly
    # zero. Everything downstream — erosion, the shore contour, the shelf tint — wants a
    # single continuous surface with the waterline at a known height, rather than a
    # height field plus a threshold that has to be passed around beside it.
    for j in range(size):
        row = height[j]
        for i in range(size):
            row[i] -= level

    rings = shapes.outlines(height, 0.0, smallest=SMALLEST_ISLAND)
    form = Landform(grid=grid, height=height, sea=sea, rings=tuple(rings),
                    land_cells=sum(1 for row in sea for wet in row if not wet))
    # Settle the hearts before asking whether the land is in pieces: a heart the
    # coastline drowned is not a severed region, it is a region standing in the water,
    # and reporting the one as the other sends the reader looking for the wrong bug.
    form.anchors = settle_anchors(form, anchors)
    form.notes.extend(_severed(grid, sea, form.anchors, keys))
    return form


def _mass(grid: Grid, seed: str) -> Field:
    """The continent, before anybody lives on it.

    A few overlapping masses, sampled through a heavy domain warp, with coastal detail
    whose strength varies along the shore, all pushed away from the canvas edge. Four
    fields, no politics.
    """
    size = grid.size
    half = (size - 1) / 2.0
    centres = _masses(size, seed)

    warp_reach = size * WARP_REACH
    warp_scale = size * WARP_SCALE
    regime_scale = size * REGIME_SCALE
    shore_scale = size * SHORE_SCALE

    # Each of these varies over tens of cells, so each is computed at the scale it
    # actually varies over and read back by interpolation. Evaluated per cell instead,
    # the four of them are most of the cost of shaping a continent, and they answer
    # almost exactly the same number — a field with no content below the sampling scale
    # loses nothing by not being asked about it.
    gulf_x = noise.field(f"{seed}|gulfx", size, wavelength=warp_scale, octaves=4,
                         stride=max(1, int(warp_scale / 5)))
    gulf_y = noise.field(f"{seed}|gulfy", size, wavelength=warp_scale, octaves=4,
                         stride=max(1, int(warp_scale / 5)))
    regimes = noise.field(f"{seed}|regime", size, wavelength=regime_scale, octaves=2,
                          stride=max(1, int(regime_scale / 5)))
    shore = noise.field(f"{seed}|shore", size, wavelength=shore_scale, octaves=5,
                        stride=max(1, int(shore_scale / 5)))

    height = grid.filled()
    for j in range(size):
        for i in range(size):
            # Displacing where the field is sampled, not what it returns. A distance
            # field ramps smoothly, so adding noise to its value moves the shore by the
            # amplitude of the noise; moving the sample point moves it by however far
            # the ramp travels in that distance, which is tens of cells.
            wx = i + (gulf_x[j][i] - 0.5) * 2.0 * warp_reach
            wy = j + (gulf_y[j][i] - 0.5) * 2.0 * warp_reach

            core = 0.0
            for cx, cy, radius in centres:
                reach = 1.0 - math.dist((wx, wy), (cx, cy)) / radius
                if reach > core:
                    core = reach

            # How indented this stretch of coast is. A shore of uniform roughness reads
            # as a texture; real coasts change character along their length, because the
            # rock does.
            regime = REGIME_LOW + (REGIME_HIGH - REGIME_LOW) * regimes[j][i]
            # Read at the warped position, so the detail travels with the coast it is on.
            grain = grid_sample(shore, wx, wy) - 0.5

            dx, dy = abs(i - half) / half, abs(j - half) / half
            edge = 1.0 - max(0.0, (max(dx, dy) - MARGIN) / (1.0 - MARGIN))
            height[j][i] = (core + SHORE_WEIGHT * regime * grain) * edge
    return height


def _masses(size: int, seed: str) -> list[tuple[float, float, float]]:
    """Where the continent's cores sit, and how far each reaches.

    Placed round a circle rather than at random points: scattered centres clump, and two
    masses on top of each other are one mass with a wasted draw. Spread evenly and then
    jittered, they overlap enough to make one landmass and not so much as to make a disc.
    """
    half = (size - 1) / 2.0
    low, high = MASSES
    count = low + int(noise.unit(f"{seed}|masses") * (high - low + 1))
    if count > high:
        count = high
    out: list[tuple[float, float, float]] = []
    for n in range(count):
        ax, ay = noise.around(n, count)
        wander = MASS_SPREAD * (0.45 + 0.55 * noise.unit(f"{seed}|spread", n))
        cx = half + ax * wander * half + noise.jitter(seed, f"massx{n}", half * 0.08)
        cy = half + ay * wander * half + noise.jitter(seed, f"massy{n}", half * 0.08)
        small, large = MASS_RADIUS
        radius = half * (small + (large - small) * noise.unit(f"{seed}|reach", n))
        out.append((cx, cy, radius))
    return out


def _hold(grid: Grid, height: Field, must_hold: list[list[tuple[int, int]]],
          level: float, seed: str) -> None:
    """Raise the ground under anything the writer drew, without tracing their pencil.

    The continent is shaped from the seed, which knows nothing about a region whose
    outline the writer traced by hand. Where those disagree the writer wins — that is not
    a tuning choice, it is the difference between a tool that renders their world and one
    that renders its own.

    But it matters *what* they said. A region outline is a claim about where a country
    is, not about where the coastline runs; a writer who drags a rough quadrilateral
    round a province has said "the Reach is over here", not "the Reach has four corners
    and two of its coasts are dead straight". Reading it as a coastline is over-reading
    it, and the map says so out loud — the seeded example world's three provinces are
    four-cornered boxes, and honouring them literally stamped their corners into the
    shore.

    So the lift is spread far wider than the outline before it is applied. The ground
    under a drawn region rises, reliably enough that it is dry land and its neighbours
    are where the writer put them, and the polygon survives as a swell rather than as an
    edge. If the writer wants a coastline exactly there, they can draw one, and a traced
    shore that detailed will hold its own shape through the spreading.
    """
    size = grid.size
    inside: set[tuple[int, int]] = set()
    for cells in must_hold:
        inside.update(cells)
    if not inside:
        return
    claim = grid.filled(0.0)
    for i, j in sorted(inside):
        if grid.holds(i, j):
            claim[j][i] = 1.0
    swell = grid.spread(claim, stride=HOLD_STRIDE, rounds=HOLD_ROUNDS)
    peak = max((value for row in swell for value in row), default=0.0)
    if peak <= 0.0:
        return
    gain = 1.0 / peak

    # How far the drawn ground is under water, taken over the region as a whole rather
    # than cell by cell. Raising each low cell to a floor is the obvious thing and it
    # produces a plate: every cell below the line comes up to exactly the line, so the
    # region ends up dead flat with the outline of the polygon stamped round it. Lifting
    # the whole area by one amount instead keeps every fold of ground the geography gave
    # it — and leaves the genuinely low corners under water, which is right. A writer who
    # draws a box round a province has not promised that all four corners are dry.
    drawn = sorted(height[j][i] for i, j in inside if grid.holds(i, j))
    if not drawn:
        return
    footing = drawn[int(len(drawn) * HOLD_QUANTILE)]
    lift = level + HOLD_CLEARANCE - footing
    if lift <= 0.0:
        return

    half = (size - 1) / 2.0
    hold_scale = size * HOLD_GRAIN_SCALE
    grain = noise.field(f"{seed}|held", size, wavelength=hold_scale, octaves=4,
                        stride=max(1, int(hold_scale / 5)))
    for j in range(size):
        row, rise = height[j], swell[j]
        for i in range(size):
            # The spread is used for its *gradient*, not its reach: a cell counts as
            # drawn-on only where the swell is strong, and the band between the two
            # thresholds is what softens the outline into a slope rather than a traced
            # edge. Taking the whole swell instead floods the map — the first attempt put
            # four fifths of the canvas above water off three provinces.
            # Roughened, or the swell's own contour becomes the coastline wherever the
            # lift only just clears the water — and that contour is a smoothed copy of
            # whatever the writer drew, so a province sketched as a box gets two dead
            # straight coasts. The same grain the rest of the shore has, applied here,
            # breaks it up into something that reads as land meeting sea.
            strength = rise[i] * gain * (HOLD_GRAIN_LOW + HOLD_GRAIN_HIGH * grain[j][i])
            if strength <= HOLD_LOW:
                continue
            strength = min(1.0, (strength - HOLD_LOW) / (HOLD_HIGH - HOLD_LOW))
            strength = strength * strength * (3.0 - 2.0 * strength)
            # And still not past the rim. A continent that reaches the edge of the canvas
            # reads as a crop of a bigger map that was never drawn.
            dx, dy = abs(i - half) / half, abs(j - half) / half
            edge = 1.0 - max(0.0, (max(dx, dy) - MARGIN) / (1.0 - MARGIN))
            row[i] += lift * strength * edge


def _waterline(height: Field, size: int, share: float) -> float:
    """The level that leaves the intended share of the canvas dry.

    A fixed sea level makes the size of the continent a property of whatever the noise
    happened to do — the same generator gave one world a landmass filling the page and
    the next a chain of islands. Choosing the level instead of the land makes the
    continent the same size every time while leaving its shape entirely to the field.
    """
    ranked = sorted(value for row in height for value in row)
    if not ranked:
        return 0.0
    index = int(len(ranked) * (1.0 - share))
    if index >= len(ranked):
        index = len(ranked) - 1
    return ranked[index]


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
    """Move any region's heart that ended up offshore onto the mainland.

    The layout places a region before the land exists — it has to, since the continent is
    now shaped from the seed and knows nothing about who lives on it — so a heart can end
    up under water. A region whose heart is at sea claims nothing at all and vanishes from
    the map without a word. It keeps its place in the world; it just stands on the nearest
    ground.

    On the *mainland*, specifically, and not merely the nearest dry cell. Since the
    continent is shaped independently it comes with islands, and an anchor that settles
    onto a five-cell islet takes a whole country with it: the region is then a separate
    landmass, the map reports the continent as having come apart, and a writer who never
    said anything about islands is shown their kingdom scattered across an archipelago.
    """
    grid = form.grid
    mainland = _mainland(form)
    out: dict[str, tuple[int, int]] = {}
    for key in sorted(anchors):
        i, j = anchors[key]
        i = max(0, min(grid.size - 1, i))
        j = max(0, min(grid.size - 1, j))
        if (i, j) in mainland:
            out[key] = (i, j)
            continue
        out[key] = _nearest_of(grid, mainland, (i, j)) or (i, j)
    return out


def _mainland(form: Landform) -> set[tuple[int, int]]:
    """The largest connected body of land — the continent, as against its islands."""
    grid = form.grid
    size = grid.size
    seen: set[tuple[int, int]] = set()
    best: set[tuple[int, int]] = set()
    for start in ((i, j) for j in range(size) for i in range(size)
                  if not form.sea[j][i]):
        if start in seen:
            continue
        piece = {start}
        seen.add(start)
        stack = [start]
        while stack:
            i, j = stack.pop()
            for ni, nj in grid.neighbours(i, j):
                if not form.sea[nj][ni] and (ni, nj) not in seen:
                    seen.add((ni, nj))
                    piece.add((ni, nj))
                    stack.append((ni, nj))
        if len(piece) > len(best):
            best = piece
    return best


def _nearest_of(grid: Grid, wanted: set[tuple[int, int]],
                start: tuple[int, int]) -> tuple[int, int] | None:
    """Breadth-first, so it is the nearest and always the same one."""
    if not wanted:
        return None
    seen = {start}
    frontier = [start]
    while frontier:
        nxt: list[tuple[int, int]] = []
        for cell in frontier:
            if cell in wanted:
                return cell
            for neighbour in grid.neighbours(*cell):
                if neighbour not in seen:
                    seen.add(neighbour)
                    nxt.append(neighbour)
        frontier = sorted(nxt)
    return None


