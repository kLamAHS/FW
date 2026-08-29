"""Mountains that are ranges, and the ground between them.

A region the writer called mountainous used to get a raised base height and some
fractal noise. That reads as a bumpy plateau, not as mountains: real ranges are
*linear*. They have a strike — a direction they run in — a crest that rises and falls
along it, and foothills that fall away to either side. The Iron Spine ought to look
like a spine.

The strike is not invented either. A range runs along the shape it is in: down the long
axis of the territory the writer drew, or along the border it sits astride when two
mountainous regions meet. That is how the Wall runs east–west and the Mountains of the
Moon run north–south — the shape of the land the writer described already says which
way the range goes, and reading it out of that is more honest than picking a direction.

Everything downstream depends on this. Rivers run off the ridges, rain falls on the
windward flank and not the lee, biomes follow the rain, and a settlement wants a pass
rather than a wall — so relief is where a map stops being a coloured diagram.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from fw.core.mapgen import noise
from fw.core.mapgen.findings import Finding, note
from fw.core.mapgen.grid import Field, Grid

# Terrain kinds that raise a range rather than merely sitting high.
MOUNTAINOUS = {"mountain": 1.0, "glacier": 0.85, "highland": 0.55, "hills": 0.42}

# Above this share of a region's terrain, it gets a range of its own.
RANGE_THRESHOLD = 0.34

# How far a range reaches, as fractions of the lattice. The crest cannot be narrow:
# it falls from full height to nothing across this distance, and over five cells that
# is a cliff, which rendered as black tears down the ridge. A range looks linear
# because its spine is long, not because its flanks are steep.
CREST_WIDTH = 0.052
APRON_WIDTH = 0.115       # how far the foothills reach
SPINE_STEPS = 9           # vertices along a spine before smoothing
BLEND_CELLS = 9.0         # how far a border's smoothing reaches inland


@dataclass(frozen=True)
class Strike:
    """Which way a range runs, and how that was decided."""

    vector: tuple[float, float]
    source: str                   # drawn | border | coast | prose
    because: str


@dataclass
class Range:
    """One mountain range: a line on the ground, not a patch of colour."""

    key: str
    region_keys: tuple[str, ...]
    strike: Strike
    spine: tuple[tuple[float, float], ...]
    crest: float
    width: float
    apron: float
    because: tuple[str, ...] = ()

    @property
    def length(self) -> float:
        return sum(math.dist(self.spine[n], self.spine[n + 1])
                   for n in range(len(self.spine) - 1))

    @property
    def elongation(self) -> float:
        """How much longer than wide — what makes a range a range."""
        return self.length / max(self.width * 2.0, 1e-6)


@dataclass
class Relief:
    """The shape of the ground."""

    elevation: Field
    base: Field                   # the smooth background, with no ridges in it
    ridge: Field                  # 0..1: how much of a cell's height a range owes
    slope: Field
    ranges: tuple[Range, ...] = ()
    findings: tuple[Finding, ...] = ()
    notes: list[str] = field(default_factory=list)


def plan_relief(grid: Grid, *, sea: list[list[bool]], from_sea: list[list[int]],
                owner: list[list[int]], keys: tuple[str, ...],
                terrain_mix: dict[str, dict[str, float]],
                base_height: dict[str, float], roughness: dict[str, float],
                seed: str, sea_level: float = 0.10) -> Relief:
    """Build the elevation field, with ranges where the writer put mountains."""
    ranges = _site_ranges(grid, owner=owner, keys=keys, terrain_mix=terrain_mix,
                          sea=sea, seed=seed)
    base = _background(grid, sea=sea, from_sea=from_sea, owner=owner, keys=keys,
                       base_height=base_height, roughness=roughness, seed=seed,
                       sea_level=sea_level)
    elevation, ridge = _raise(grid, base, ranges, sea=sea, seed=seed,
                              sea_level=sea_level)
    findings: list[Finding] = []
    if not ranges and any(_mountain_share(terrain_mix.get(k, {})) > 0 for k in keys):
        findings.append(note(
            "self-check",
            "no region is mountainous enough for a range of its own, so the high "
            "ground is rolling rather than ridged"))
    return Relief(elevation=elevation, base=base, ridge=ridge,
                  slope=_slope(grid, elevation), ranges=tuple(ranges),
                  findings=tuple(findings))


# ---- where the ranges are --------------------------------------------------

def _mountain_share(mix: dict[str, float]) -> float:
    total = sum(mix.values()) or 1.0
    return sum(weight * MOUNTAINOUS.get(kind, 0.0)
               for kind, weight in mix.items()) / total


def _site_ranges(grid: Grid, *, owner: list[list[int]], keys: tuple[str, ...],
                 terrain_mix: dict[str, dict[str, float]],
                 sea: list[list[bool]], seed: str) -> list[Range]:
    """One range per region high enough to deserve one, laid along its long axis."""
    out: list[Range] = []
    for index, key in enumerate(keys):
        share = _mountain_share(terrain_mix.get(key, {}))
        if share < RANGE_THRESHOLD:
            continue
        cells = [(i, j) for j in range(grid.size) for i in range(grid.size)
                 if owner[j][i] == index and not sea[j][i]]
        if len(cells) < 12:
            continue
        strike = _strike_of(cells, key)
        spine = _lay_spine(grid, cells, strike, seed=f"{seed}|{key}")
        if len(spine) < 2:
            continue
        out.append(Range(
            key=key, region_keys=(key,), strike=strike, spine=tuple(spine),
            # How high depends on what kind of high ground it is. A range of downs is
            # still a range — the Sheepshead Hills are on the map — but it must not
            # stand as tall as a range of crags.
            crest=0.30 + 0.62 * share,
            width=grid.size * CREST_WIDTH,
            apron=grid.size * APRON_WIDTH,
            because=(f"{key} is {round(share * 100)}% high ground",
                     strike.because),
        ))
    return out


def _strike_of(cells: list[tuple[int, int]], key: str) -> Strike:
    """The direction the ground itself runs in.

    The principal axis of the territory, by its second moments — the same calculation
    that finds the long axis of any scatter. A range in a long thin region runs along
    it; a range in a round one has no strong direction and falls back to the diagonal
    the moments give anyway.
    """
    n = len(cells)
    mx = sum(c[0] for c in cells) / n
    my = sum(c[1] for c in cells) / n
    sxx = sum((c[0] - mx) ** 2 for c in cells) / n
    syy = sum((c[1] - my) ** 2 for c in cells) / n
    sxy = sum((c[0] - mx) * (c[1] - my) for c in cells) / n
    # The principal eigenvector of [[sxx, sxy], [sxy, syy]], without a trig call.
    difference = sxx - syy
    root = math.sqrt(difference * difference + 4.0 * sxy * sxy)
    vx, vy = (sxy, (difference + root) / 2.0) if abs(sxy) > 1e-9 else (
        (1.0, 0.0) if sxx >= syy else (0.0, 1.0))
    length = math.hypot(vx, vy) or 1.0
    vector = (vx / length, vy / length)
    aspect = "north to south" if abs(vector[1]) > abs(vector[0]) else "east to west"
    return Strike(vector=vector, source="drawn",
                  because=f"it runs {aspect}, along the shape of {key}")


def _lay_spine(grid: Grid, cells: list[tuple[int, int]], strike: Strike, *,
               seed: str) -> list[tuple[float, float]]:
    """A polyline down the middle of the territory, along the strike.

    Walked as a sequence of slices perpendicular to the strike: at each step along the
    line, the spine sits at the middle of the ground the region actually holds there.
    A range therefore bends with its region rather than cutting straight across it.
    """
    if not cells:
        return []
    owned = set(cells)
    mx = sum(c[0] for c in cells) / len(cells)
    my = sum(c[1] for c in cells) / len(cells)
    ax, ay = strike.vector
    px, py = -ay, ax                                  # across the strike

    along = [(c[0] - mx) * ax + (c[1] - my) * ay for c in cells]
    # Trim the extreme few percent at each end: a single outlying cell would otherwise
    # stretch the spine out past the body of the range and flatten its crest.
    along_sorted = sorted(along)
    trim = max(1, len(along_sorted) // 40)
    low, high = along_sorted[trim - 1], along_sorted[-trim]
    if high - low < 3.0:
        return []

    spine: list[tuple[float, float]] = []
    for step in range(SPINE_STEPS):
        t = low + (high - low) * step / (SPINE_STEPS - 1)
        slice_cells = [c for c, u in zip(cells, along, strict=True)
                       if abs(u - t) <= max(1.5, (high - low) / SPINE_STEPS)]
        if not slice_cells:
            continue
        # The middle of the slice, across the strike.
        across = [(c[0] - mx) * px + (c[1] - my) * py for c in slice_cells]
        centre = (min(across) + max(across)) / 2.0
        wander = noise.signed(f"{seed}|bend", step) * (high - low) * 0.05
        x = mx + ax * t + px * (centre + wander)
        y = my + ay * t + py * (centre + wander)
        if (int(round(x)), int(round(y))) in owned:
            spine.append((x, y))
    return _smooth(spine)


def _smooth(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """One pass of corner cutting, so a spine reads as a ridge and not a zigzag."""
    if len(points) < 3:
        return points
    out = [points[0]]
    for n in range(len(points) - 1):
        ax, ay = points[n]
        bx, by = points[n + 1]
        out.append((ax * 0.7 + bx * 0.3, ay * 0.7 + by * 0.3))
        out.append((ax * 0.3 + bx * 0.7, ay * 0.3 + by * 0.7))
    out.append(points[-1])
    return out


# ---- the field itself ------------------------------------------------------

def _background(grid: Grid, *, sea: list[list[bool]], from_sea: list[list[int]],
                owner: list[list[int]], keys: tuple[str, ...],
                base_height: dict[str, float], roughness: dict[str, float],
                seed: str, sea_level: float) -> Field:
    """The smooth ground each region tends toward, with no ridges in it yet.

    Blurred across the borders on purpose. A region's base height is a fact about the
    region, but the ground does not know where the border is: leave the step in and
    every border shows as a visible terrace, which is the giveaway of a generated map.
    """
    size = grid.size
    field = grid.filled(0.0)
    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                continue
            index = owner[j][i]
            key = keys[index] if 0 <= index < len(keys) else None
            base = base_height.get(key, 0.30) if key else 0.30
            rough = roughness.get(key, 0.06) if key else 0.06
            detail = noise.fbm(seed, i / 11.0, j / 11.0, octaves=4)
            height = base + (detail - 0.5) * 2.0 * rough
            # Rise away from the water, so the shore shelves rather than cliffs. Over
            # four cells this stepped by a sixth of the full height each cell, which
            # put a dark rim of cliff around every coast — on a mountainous shore the
            # single largest discontinuity anywhere in the field.
            shelf = min(1.0, from_sea[j][i] / 12.0)
            field[j][i] = max(sea_level,
                              sea_level + (height - sea_level) * (0.62 + 0.38 * shelf))
    return grid.eased_across(field, owner, sea, reach=BLEND_CELLS)


def _raise(grid: Grid, base: Field, ranges: list[Range], *,
           sea: list[list[bool]], seed: str,
           sea_level: float) -> tuple[Field, Field]:
    """Lift the ground along each spine, falling away to foothills either side."""
    size = grid.size
    elevation = [row[:] for row in base]
    ridge = grid.filled(0.0)
    if not ranges:
        return elevation, ridge

    for mountain in ranges:
        segments = list(zip(mountain.spine, mountain.spine[1:], strict=False))
        if not segments:
            continue
        # Only the cells the range can possibly reach. Sweeping the whole lattice per
        # range is most of the cost of the stage, and a range covers a fraction of it.
        reach = mountain.apron + 1.0
        xs = [p[0] for p in mountain.spine]
        ys = [p[1] for p in mountain.spine]
        i0 = max(0, int(min(xs) - reach))
        i1 = min(size - 1, int(max(xs) + reach))
        j0 = max(0, int(min(ys) - reach))
        j1 = min(size - 1, int(max(ys) + reach))
        for j in range(j0, j1 + 1):
            for i in range(i0, i1 + 1):
                if sea[j][i]:
                    continue
                near = min(_to_segment(i, j, a, b) for a, b in segments)
                if near > mountain.apron:
                    continue
                # Two falloffs: a tight crest and a broad apron. One alone gives either
                # a wall with no foothills or a dome with no ridge.
                crest = _falloff(near / max(mountain.width, 1e-6))
                apron = _falloff(near / max(mountain.apron, 1e-6))
                lift = mountain.crest * (0.78 * crest + 0.22 * apron)
                # Roughen the lift, not the ground: a range is broken where it is high.
                grain = noise.fbm(f"{seed}|ridge", i / 6.0, j / 6.0, octaves=3) - 0.5
                lift *= 1.0 + grain * 0.45 * crest
                if lift <= 0.0:
                    continue
                raised = min(1.0, elevation[j][i] + lift)
                if raised > elevation[j][i]:
                    share = (raised - base[j][i]) / max(raised, 1e-6)
                    ridge[j][i] = max(ridge[j][i], min(1.0, share))
                    elevation[j][i] = raised
    return elevation, ridge


def _falloff(t: float) -> float:
    """1 at the spine, 0 at the edge, smooth in between — and no libm call."""
    if t >= 1.0:
        return 0.0
    u = 1.0 - t
    return u * u * (3.0 - 2.0 * u)


def _to_segment(i: float, j: float, a: tuple[float, float],
                b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    vx, vy = bx - ax, by - ay
    length = vx * vx + vy * vy
    t = 0.0 if not length else max(0.0, min(1.0, ((i - ax) * vx + (j - ay) * vy) / length))
    return math.hypot(i - (ax + vx * t), j - (ay + vy * t))


def _slope(grid: Grid, elevation: Field) -> Field:
    """How steep each cell is — what makes a road bend and a castle defensible."""
    size = grid.size
    out = grid.filled(0.0)
    for j in range(size):
        for i in range(size):
            here = elevation[j][i]
            out[j][i] = max((abs(here - elevation[nj][ni])
                             for ni, nj in grid.neighbours(i, j, diagonal=False)),
                            default=0.0)
    return out
