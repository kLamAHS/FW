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
SPINE_SEARCH = 6          # how far a smoothed vertex may be pulled back onto land
BLEND_CELLS = 9.0         # how far a border's smoothing reaches inland

# How far the writer's stated heights are spread before they are believed. Wide enough
# that no region has an edge; narrow enough that a highland is still highest where they
# said it was.
REGION_STRIDE = 3
REGION_ROUNDS = 4

# The continental profile: how far inland the ground goes on rising, as a fraction of
# the lattice, and how much of its height a cell has already reached at the shore.
INLAND_REACH = 0.28
SHORE_SHARE = 0.30

# A swell far longer than any territory, so the world has broad highs and basins that
# have nothing to do with who owns them.
TECTONIC_SCALE = 0.55
TECTONIC_WEIGHT = 0.26

# Relief the continent has whether or not anybody called it mountainous, at three scales.
# Without this the plains are a flat plate that erosion cannot get any purchase on: no
# gradient, no stream power, no valleys, and a map whose entire interior is one colour.
BROAD_RELIEF = 0.30
HILL_RELIEF = 0.16
FINE_RELIEF = 0.06

# How much of its ruggedness the lowest ground keeps, and over what height the ground
# goes from a flood plain to broken country.
BASIN_CALM = 0.16
RUGGED_SPAN = 0.42

# The anatomy of a range, in lattice cells and fractions.
STEP_CELLS = 1.6          # how finely a ridge is walked when its height is laid out
PASS_FLOOR = 0.42         # how low the crest may fall at a saddle, as a share of a peak
END_TAPER = 0.34          # over what share of its length a range comes down to nothing
SPUR_SPACING = 0.13       # how often a spur leaves the spine, as a share of its length
SPUR_REACH_LOW = 0.35     # a spur's length, as a share of the apron
SPUR_REACH_HIGH = 0.55
SPUR_HEIGHT = 0.62        # how high a spur starts, as a share of the crest beside it
SPUR_LEAN = 0.8           # how far off perpendicular a spur may leave
SPUR_WANDER = 2.2         # how far a spur bends along its length, in cells
SHOULDER_LOW = 0.16       # a shoulder's height, as a share of the crest
SHOULDER_HIGH = 0.22
RIDGE_GRAIN = 0.55        # how broken the lift is


@dataclass(frozen=True)
class Strike:
    """Which way a range runs, and how that was decided."""

    vector: tuple[float, float]
    source: str                   # drawn | border | coast | prose
    because: str


@dataclass(frozen=True)
class Ridge:
    """One line of high ground, with its height varying along its length.

    A range is not one of these but a system of them: a main spine, the spurs that
    branch off it, and the shoulders that flank it. Keeping them as separate ridges
    rather than as a single distance-to-spine is what gives a range an interior —
    side valleys, enclosed basins, a pass between two peaks — instead of a smooth
    ramp up to a line and down again.
    """

    points: tuple[tuple[float, float, float], ...]     # x, y, crest height here
    kind: str                                          # spine | spur | shoulder


@dataclass
class Range:
    """One mountain range: a system of lines on the ground, not a patch of colour."""

    key: str
    region_keys: tuple[str, ...]
    strike: Strike
    spine: tuple[tuple[float, float], ...]
    crest: float
    width: float
    apron: float
    # How long it has stood. A young range is high, sharp and narrow; an old one has
    # been worn down and spread out, so it is lower, rounder and far wider. It is the
    # difference between the Alps and the Appalachians, and it is one number.
    age: float = 0.35
    # How broken the crest is: whether it runs as a wall or as a chain of peaks with
    # passes between them.
    roughness: float = 0.5
    ridges: tuple[Ridge, ...] = ()
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
        cells = _largest_piece(grid, [
            (i, j) for j in range(grid.size) for i in range(grid.size)
            if owner[j][i] == index and not sea[j][i]])
        if len(cells) < 12:
            continue
        strike = _strike_of(cells, key)
        spine = _lay_spine(grid, cells, strike, seed=f"{seed}|{key}")
        if len(spine) < 2:
            continue
        # How old the range is, and how broken. Both are stable properties of the
        # range rather than of the run, so they come from the seed and its name.
        age = 0.15 + 0.65 * noise.unit(f"{seed}|age", len(out))
        broken = 0.30 + 0.60 * noise.unit(f"{seed}|broken", len(out))
        mountain = Range(
            key=key, region_keys=(key,), strike=strike, spine=tuple(spine),
            # How high depends on what kind of high ground it is. A range of downs is
            # still a range — the Sheepshead Hills are on the map — but it must not
            # stand as tall as a range of crags.
            crest=0.30 + 0.62 * share,
            width=grid.size * CREST_WIDTH,
            apron=grid.size * APRON_WIDTH,
            age=age, roughness=broken,
            because=(f"{key} is {round(share * 100)}% high ground",
                     strike.because),
        )
        mountain.ridges = _ridgelines(mountain, f"{seed}|{key}")
        out.append(mountain)
    return out


def _largest_piece(grid: Grid,
                   cells: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The biggest connected block of a region's ground.

    Now that the continent is shaped before anybody is placed on it, a region can hold
    two islands and a headland. Reading a strike from all of it at once gives the
    principal axis of the *scatter*, which is a direction no piece of ground actually
    runs in, and lays a range along a line that spends half its length at sea. The main
    body of the region is the part that has a shape worth reading.
    """
    if not cells:
        return []
    owned = set(cells)
    seen: set[tuple[int, int]] = set()
    best: list[tuple[int, int]] = []
    for start in sorted(owned):
        if start in seen:
            continue
        piece = [start]
        seen.add(start)
        stack = [start]
        while stack:
            i, j = stack.pop()
            for ni, nj in grid.neighbours(i, j):
                if (ni, nj) in owned and (ni, nj) not in seen:
                    seen.add((ni, nj))
                    piece.append((ni, nj))
                    stack.append((ni, nj))
        if len(piece) > len(best):
            best = piece
    return sorted(best)


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
    return _onto_land(_smooth(spine), owned)


def _onto_land(points: list[tuple[float, float]],
               owned: set[tuple[int, int]]) -> list[tuple[float, float]]:
    """Pull any vertex the smoothing pushed off the region back onto its ground.

    Each vertex is placed on ground the region holds, and then the smoothing averages
    them — which is the point, since an unsmoothed spine is a zigzag — but averaging
    across the mouth of a bay puts the curve in the water. A range whose spine crosses
    open sea has a gap in it: the raising stage lifts no water, so the crest simply stops
    and starts again, and the writer is shown a mountain range in two halves with a
    strait through the middle that nothing in their world accounts for.
    """
    if not owned:
        return points
    out: list[tuple[float, float]] = []
    for x, y in points:
        if (int(round(x)), int(round(y))) in owned:
            out.append((x, y))
            continue
        # The nearest ground the region actually holds. Searched outward in rings so it
        # is the nearest one and always the same one.
        best: tuple[int, int] | None = None
        for reach in range(1, SPINE_SEARCH + 1):
            ring = [(int(round(x)) + di, int(round(y)) + dj)
                    for dj in range(-reach, reach + 1)
                    for di in range(-reach, reach + 1)
                    if max(abs(di), abs(dj)) == reach]
            found = [c for c in sorted(ring) if c in owned]
            if found:
                best = min(found, key=lambda c: (math.dist(c, (x, y)), c))
                break
        if best is not None:
            out.append((float(best[0]), float(best[1])))
    return out


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
    """The smooth ground the continent tends toward, before any range is laid on it.

    This used to be a lookup: every cell took the base height of whichever region owned
    it. That is the single most damaging line the generator ever had. It makes elevation
    a fact about *politics*, so the map comes out as one flat plate per province with a
    straight edge between them — which is exactly what a reader means when they say a
    generated world looks like painted zones rather than one continuous place. Blurring
    the seams does not help, because the plates are still plates a few cells further in.

    So the ground is built from geography instead, and none of it knows where a border
    is:

      the continental profile — land rises away from its own coast. Every real
      continent does this, and it is most of why an interior reads as an interior;
      the tectonic swell   — a very long-wavelength field, so the world has broad highs
      and basins that have nothing to do with anybody's territory;
      the grain            — ordinary detail, at the scale of a range's foothills.

    What the writer said about a region still counts, and counts for a lot, but it enters
    as a *swell* rather than a plate: their stated heights are spread over tens of cells,
    so a march the writer called high ground raises the country around it and no line is
    drawn anywhere. The border between a highland and a plain then falls somewhere on a
    slope, which is where such borders actually fall.
    """
    size = grid.size

    # What the writer said, before it is spread out. Cells nobody owns — open water, and
    # the ground beyond the last region — take the world's mean rather than a default, so
    # an unclaimed margin does not drag the whole coast down to a fixed number.
    stated = [base_height.get(key, 0.30) for key in keys]
    mean = sum(stated) / len(stated) if stated else 0.30
    claimed = grid.filled(mean)
    coarse_rough = grid.filled(0.06)
    for j in range(size):
        for i in range(size):
            index = owner[j][i]
            if 0 <= index < len(stated):
                claimed[j][i] = stated[index]
                coarse_rough[j][i] = roughness.get(keys[index], 0.06)
    swell = grid.spread(claimed, stride=REGION_STRIDE, rounds=REGION_ROUNDS)
    rough_field = grid.spread(coarse_rough, stride=REGION_STRIDE, rounds=REGION_ROUNDS)

    inland_reach = size * INLAND_REACH
    swell_scale = size * TECTONIC_SCALE

    # Sampled at the scale each one varies over rather than per cell. Only the finest
    # band needs a sample in every cell; the tectonic swell is eighty cells across and
    # asking it a hundred and forty times along each row is a hundred and forty answers
    # to the same question.
    tectonics = noise.field(f"{seed}|swell", size, wavelength=swell_scale, octaves=3,
                            stride=max(1, int(swell_scale / 6)))
    uplands = noise.field(f"{seed}|uplands", size, wavelength=31.0, octaves=3, stride=5)
    hill_field = noise.field(seed, size, wavelength=11.0, octaves=4, stride=2)
    fine_field = noise.field(f"{seed}|grain", size, wavelength=3.7, octaves=3, stride=1)

    field = grid.filled(0.0)
    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                continue
            # Rise away from the water. Over four cells this used to step by a sixth of
            # the full height each cell, which put a dark rim of cliff around every coast.
            inland = min(1.0, from_sea[j][i] / inland_reach)
            profile = inland * inland * (3.0 - 2.0 * inland)

            tectonic = tectonics[j][i] - 0.5
            # Relief at three scales, because a continent has it at three scales: broad
            # uplands and basins tens of cells across, hill country at the scale a day's
            # walk crosses, and the grain below that. One octave band gives an even
            # stipple that erosion cannot organise into anything, and reads as noise
            # rather than as country.
            broad = uplands[j][i] - 0.5
            hills = hill_field[j][i] - 0.5
            fine = fine_field[j][i] - 0.5

            # How rugged this ground is at all is itself a fact about where it is. High
            # ground is broken; a basin is not, because a basin is where everything the
            # high ground shed has been settling for as long as there has been weather.
            # Without this the relief amplitude is a constant and the whole continent
            # comes out uniformly hilly — no plains, no river basin, nowhere for a city
            # to be founded that is not on a slope, and a texture the eye reads as noise
            # laid over the map rather than as country.
            upland = swell[j][i] + TECTONIC_WEIGHT * tectonic + BROAD_RELIEF * broad
            ruggedness = (upland - sea_level) / max(RUGGED_SPAN, 1e-6)
            if ruggedness < 0.0:
                ruggedness = 0.0
            elif ruggedness > 1.0:
                ruggedness = 1.0
            ruggedness = BASIN_CALM + (1.0 - BASIN_CALM) * ruggedness * ruggedness

            rough = rough_field[j][i]
            height = (upland
                      + ruggedness * ((HILL_RELIEF + 2.0 * rough) * hills
                                      + FINE_RELIEF * fine))
            # Deliberately not clamped up to sea level. Clamping puts a plateau of
            # ground at exactly the waterline all round the continent, and the shore is
            # drawn as the contour at that height — so the contour runs across dead flat
            # ground, has no gradient to follow, and snaps to whichever cells happen to
            # round the right way. That is a coastline made of ten-pixel stairs, and it
            # is a much more visible fault than the one the clamp was there to prevent.
            #
            # A cell of a region that comes out below the waterline is simply a cell of
            # that region under water, which is a thing that happens to countries, and
            # the shelf it stands on is continuous with the sea floor beside it.
            field[j][i] = sea_level + (height - sea_level) * (
                SHORE_SHARE + (1.0 - SHORE_SHARE) * profile)
    return field


def _ridgelines(mountain: Range, seed: str) -> tuple[Ridge, ...]:
    """Turn a range's spine into the system of ridges a range actually is.

    A range raised as "distance to one line" is a smooth ramp up to a wall and down the
    other side. That is not what a range looks like from above, and more to the point it
    is not something water can do anything with: there is nowhere for a valley to start,
    so erosion can only score the flanks with parallel gullies.

    Three things are built here, and each answers one of those:

      the crest varies along its length, between peaks and the saddles between them, so
      the range has summits and passes rather than a uniform height — and a pass is what
      a road will later want, so it has to exist in the rock before anybody looks for it;

      spurs branch off the spine, alternating sides and shortening as they go, which is
      what turns the flanks into a comb of valleys with basins between them;

      shoulders run parallel and lower, which is what foothills are.

    All of it comes out as ridges with a height at each point, so the raising stage does
    not have to know which is which.
    """
    spine = mountain.spine
    if len(spine) < 2:
        return ()

    # An old range is low, wide and rounded; a young one is high, narrow and sharp.
    youth = 1.0 - mountain.age
    ridges: list[Ridge] = []

    walked = _walk(spine, STEP_CELLS)
    if len(walked) < 2:
        return ()

    profile: list[tuple[float, float, float]] = []
    span = len(walked) - 1
    for n, (x, y) in enumerate(walked):
        t = n / span if span else 0.0
        # Peaks and saddles along the crest. The floor is what stops a pass becoming a
        # gap the range is cut in two by.
        wave = noise.fbm(f"{seed}|crest", n / (5.5 + 7.0 * (1.0 - mountain.roughness)),
                         0.0, octaves=3)
        relief = PASS_FLOOR + (1.0 - PASS_FLOOR) * wave
        # And the whole range tapers towards its ends, or it stops dead in the plain.
        taper = min(1.0, 2.0 * min(t, 1.0 - t) / max(END_TAPER, 1e-6))
        taper = taper * taper * (3.0 - 2.0 * taper)
        profile.append((x, y, mountain.crest * relief * taper))
    ridges.append(Ridge(points=tuple(profile), kind="spine"))

    # Spurs. Spaced along the spine, alternating sides, each shorter and lower than the
    # crest it leaves — a range's flanks are a comb, not a plane.
    gap = max(2, int(len(walked) * SPUR_SPACING))
    side = 1.0
    for n in range(gap, len(walked) - gap, gap):
        x, y, high = profile[n]
        if high <= 0.0:
            continue
        ax, ay = _direction(walked, n)
        px, py = -ay * side, ax * side
        # Not exactly perpendicular: a spur that leaves at a right angle every time
        # reads as a fishbone.
        lean = (noise.unit(f"{seed}|spur", n) - 0.5) * SPUR_LEAN
        px, py = px + ax * lean, py + ay * lean
        length = mountain.apron * (SPUR_REACH_LOW + SPUR_REACH_HIGH
                                   * noise.unit(f"{seed}|reach", n))
        steps = max(2, int(length / STEP_CELLS))
        points: list[tuple[float, float, float]] = []
        for k in range(steps + 1):
            t = k / steps
            wander = (noise.unit(f"{seed}|bend", n * 31 + k) - 0.5) * SPUR_WANDER
            points.append((x + px * length * t - ay * wander,
                           y + py * length * t + ax * wander,
                           high * (1.0 - t) * (1.0 - t) * SPUR_HEIGHT))
        ridges.append(Ridge(points=tuple(points), kind="spur"))
        side = -side

    # Shoulders: a lower line either side of the crest, which is what a range's foothills
    # are. Offset further out on an old range, because that is what age does to one.
    offset = mountain.width * (1.4 + 2.2 * mountain.age)
    for sign in (1.0, -1.0):
        points = []
        for n, (x, y, high) in enumerate(profile):
            ax, ay = _direction(walked, n)
            points.append((x - ay * offset * sign, y + ax * offset * sign,
                           high * (SHOULDER_LOW + SHOULDER_HIGH * youth)))
        ridges.append(Ridge(points=tuple(points), kind="shoulder"))

    return tuple(ridges)


def _walk(spine: tuple[tuple[float, float], ...],
          step: float) -> list[tuple[float, float]]:
    """Resample a polyline at an even spacing, so height varies with distance."""
    out: list[tuple[float, float]] = [spine[0]]
    carry = 0.0
    for a, b in zip(spine, spine[1:], strict=False):
        run = math.dist(a, b)
        if run <= 0.0:
            continue
        walked = step - carry
        while walked <= run:
            t = walked / run
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
            walked += step
        carry = run - (walked - step)
    return out


def _direction(points: list[tuple[float, float]], n: int) -> tuple[float, float]:
    """The unit direction the line is running in at one of its points."""
    a = points[max(0, n - 1)]
    b = points[min(len(points) - 1, n + 1)]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    return (dx / length, dy / length) if length else (1.0, 0.0)


def _raise(grid: Grid, base: Field, ranges: list[Range], *,
           sea: list[list[bool]], seed: str,
           sea_level: float) -> tuple[Field, Field]:
    """Lift the ground around every ridge of every range, in one sweep.

    The ridges are rasterised into the lattice carrying their own height, and then a
    single distance transform answers, for every cell at once, how far the nearest ridge
    is and how high it stood there. That is why a range can be a system of a dozen lines
    rather than one: the cost does not depend on how many there are.

    The width of a range follows its height, because that is how mountains work — a
    thousand-metre ridge does not fall to the plain in the same distance a three-thousand
    one does. So a spur is narrow and the main crest broad, without either being told.
    """
    size = grid.size
    elevation = [row[:] for row in base]
    ridge = grid.filled(0.0)
    if not ranges:
        return elevation, ridge

    sources: dict[tuple[int, int], float] = {}
    for mountain in ranges:
        for line in mountain.ridges:
            for a, b in zip(line.points, line.points[1:], strict=False):
                run = max(math.dist((a[0], a[1]), (b[0], b[1])), 1e-6)
                steps = max(1, int(run))
                for k in range(steps + 1):
                    t = k / steps
                    x = a[0] + (b[0] - a[0]) * t
                    y = a[1] + (b[1] - a[1]) * t
                    high = a[2] + (b[2] - a[2]) * t
                    if high <= 0.0:
                        continue
                    cell = (int(x), int(y))
                    if (0 <= cell[0] < size and 0 <= cell[1] < size
                            and high > sources.get(cell, 0.0)):
                        sources[cell] = high

    if not sources:
        return elevation, ridge

    far, high_of = grid.nearest_from(sorted(sources.items()))
    tallest = max(sources.values())
    # The roughening is asked for at every cell a range reaches, which on a mountainous
    # world is most of the map — so it is one field computed once rather than a fractal
    # sample per cell. It only has to break the lift up, so half a wavelength of
    # interpolation costs it nothing.
    grain_field = noise.field(f"{seed}|ridge", size, wavelength=6.0, octaves=4, stride=2)

    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                continue
            crest_height = high_of[j][i]
            if crest_height <= 0.0:
                continue
            share = crest_height / tallest
            width = size * CREST_WIDTH * (0.35 + 0.65 * share)
            apron = size * APRON_WIDTH * (0.30 + 0.70 * share)
            near = far[j][i]
            if near > apron:
                continue
            # Two falloffs: a tight crest and a broad apron. One alone gives either a
            # wall with no foothills or a dome with no ridge.
            crest = _falloff(near / max(width, 1e-6))
            skirt = _falloff(near / max(apron, 1e-6))
            lift = crest_height * (0.72 * crest + 0.28 * skirt)
            # Roughen the lift, not the ground: a range is broken where it is high.
            grain = grain_field[j][i] - 0.5
            lift *= 1.0 + grain * RIDGE_GRAIN * (0.35 + 0.65 * crest)
            if lift <= 0.0:
                continue
            raised = elevation[j][i] + lift
            if raised > 1.0:
                raised = 1.0
            if raised > elevation[j][i]:
                portion = (raised - base[j][i]) / max(raised, 1e-6)
                ridge[j][i] = max(ridge[j][i], min(1.0, portion))
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
