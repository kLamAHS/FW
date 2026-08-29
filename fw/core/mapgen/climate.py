"""Where the rain falls, and where it does not.

Moisture used to be a property of a region: the writer said "wet" and every cell in it
was wet. That is fine as far as it goes, and it goes nowhere — it cannot produce the one
piece of physical geography a reader notices immediately, which is that the far side of
a mountain range is dry.

So the rain is carried. Wind comes off the sea holding water, rises when the ground
rises, drops what it cannot hold on the windward flank, and arrives over the lee side
with nothing left. A range therefore has a green side and a brown side, and a desert
appears where the map says one should be rather than where the writer had to put one.

The writer still wins. Where they have said what a region's weather is, their words are
what the region gets, and the model only fills the silence — and says which it did.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from fw.core.mapgen import noise
from fw.core.mapgen.findings import Finding, note
from fw.core.mapgen.grid import Field, Grid
from fw.core.mapgen.guards import rcp_exp

# How much of the reconciled moisture comes from what the writer wrote, where they
# wrote anything. Their word is most of it; the model supplies the texture.
PROSE_WEIGHT = 0.72

LAPSE = 0.9               # how much colder the peaks are than the shore
RAIN_PER_RISE = 5.0

# What the air is carrying where it enters the map: saturated if it arrives over water,
# and something moderate if the very edge of the map is land.
OCEAN_FETCH = 0.92
EDGE_LAND = 0.35

# How much of the rain that falls on a cell of land goes back into the air over it.
# Continental moisture recycling is a large fraction in reality, and it is what keeps a
# continental interior habitable rather than arid.
RECYCLED = 0.62       # how sharply a rise wrings the air out
BLEND_CELLS = 8.0         # how far a border's smoothing reaches inland


@dataclass(frozen=True)
class Wind:
    """Which way the weather comes from, and how that was decided."""

    vector: tuple[float, float]
    source: str                   # prose | brief | coast | default
    because: str


@dataclass
class Climate:
    rain: Field                   # what falls, before anything is said about it
    temperature: Field            # -1 frozen .. +1 scorching
    moisture: Field               # 0..1, reconciled with the writer's prose
    shadow: Field                 # 0..1: how much drier than the windward side
    wind: Wind
    findings: tuple[Finding, ...] = ()
    notes: list[str] = field(default_factory=list)


def plan_climate(grid: Grid, *, elevation: Field, sea: list[list[bool]],
                 from_sea: list[list[int]], owner: list[list[int]],
                 keys: tuple[str, ...], temperature_of: dict[str, float],
                 moisture_of: dict[str, float], stated: dict[str, bool],
                 seed: str, sea_level: float = 0.10,
                 prevailing: str = "") -> Climate:
    """Carry the weather across the land."""
    wind = _wind(grid, sea=sea, prevailing=prevailing)
    rain = _sweep(grid, elevation=elevation, sea=sea, wind=wind, seed=seed,
                  sea_level=sea_level)
    rain = grid.blurred(rain)
    temperature = _temperature(grid, elevation=elevation, sea=sea, owner=owner,
                               keys=keys, temperature_of=temperature_of,
                               sea_level=sea_level)
    moisture, shadow = _reconcile(grid, rain=rain, sea=sea, owner=owner, keys=keys,
                                  moisture_of=moisture_of, stated=stated, seed=seed)
    # The writer's word for a region's weather is a fact about the region, so it steps
    # at the border exactly as the base height did — and a moisture map in polygons is
    # as much of a giveaway as an elevation one.
    moisture = grid.eased_across(moisture, owner, sea, reach=BLEND_CELLS, rounds=5)

    findings: list[Finding] = []
    driest = min((moisture[j][i] for j in range(grid.size) for i in range(grid.size)
                  if not sea[j][i]), default=0.5)
    if driest < 0.18:
        findings.append(note(
            "self-check",
            f"the lee of the high ground comes out arid — {wind.because}"))
    return Climate(rain=rain, temperature=temperature, moisture=moisture,
                   shadow=shadow, wind=wind, findings=tuple(findings))


# ---- which way the weather comes from --------------------------------------

_BEARINGS = {"north": (0.0, 1.0), "south": (0.0, -1.0),
             "east": (-1.0, 0.0), "west": (1.0, 0.0),
             "northwest": (0.7071, 0.7071), "northeast": (-0.7071, 0.7071),
             "southwest": (0.7071, -0.7071), "southeast": (-0.7071, -0.7071)}


def _wind(grid: Grid, *, sea: list[list[bool]], prevailing: str) -> Wind:
    """The prevailing wind: what the writer said, else off the widest stretch of sea.

    Weather comes from the ocean, so a continent with its long coast to the west gets
    westerlies. Deriving it from the shape of their own world beats asking, and beats
    a constant.
    """
    named = (prevailing or "").strip().lower()
    if named in _BEARINGS:
        return Wind(vector=_BEARINGS[named], source="brief",
                    because=f"you said the wind comes from the {named}")

    size = grid.size
    edge = max(1, size // 8)
    water = {
        "west": sum(1 for j in range(size) for i in range(edge) if sea[j][i]),
        "east": sum(1 for j in range(size) for i in range(size - edge, size)
                    if sea[j][i]),
        "north": sum(1 for j in range(edge) for i in range(size) if sea[j][i]),
        "south": sum(1 for j in range(size - edge, size) for i in range(size)
                     if sea[j][i]),
    }
    side = max(sorted(water), key=lambda k: water[k])
    if not water[side]:
        return Wind(vector=_BEARINGS["west"], source="default",
                    because="there is no open sea to carry weather in from")
    return Wind(vector=_BEARINGS[side], source="coast",
                because=f"the widest open water lies to the {side}, so the weather "
                        f"comes in from there")


# ---- carrying the rain -----------------------------------------------------

def _sweep(grid: Grid, *, elevation: Field, sea: list[list[bool]], wind: Wind,
           seed: str, sea_level: float) -> Field:
    """One upwind pass: air picks water up over the sea and drops it climbing land.

    Walked in the wind's own direction so each cell is reached after the cell upwind of
    it — an ordinary sweep in the right order, rather than an iterative solve, which is
    what keeps this affordable in pure Python.
    """
    size = grid.size
    wx, wy = wind.vector
    rain = grid.filled(0.0)
    carried = grid.filled(0.0)

    # Order the cells so that upwind comes first — the whole sweep depends on it, and
    # getting the sign backwards means every cell reads an upwind neighbour that has
    # not been filled in yet, so no air ever carries anything and no rain falls at all.
    # Sorting on the projection *along* the wind is exact and needs no special-casing
    # for diagonals.
    order = sorted(((i * wx + j * wy), i, j)
                   for j in range(size) for i in range(size))

    for _key, i, j in order:
        upwind = (i - int(round(wx)), j - int(round(wy)))
        if grid.holds(*upwind):
            incoming = carried[upwind[1]][upwind[0]]
        elif sea[j][i]:
            # The ocean does not stop at the edge of the picture. Air arriving over water
            # from beyond the frame has crossed water to get here, so it arrives loaded.
            # Starting it at nothing instead made the upwind margin of every map a
            # desert: measured, a coastal plain with open sea immediately upwind of it
            # was receiving exactly zero rain, because the air needed a dozen cells of
            # fetch to pick anything up and the frame did not give it a dozen cells.
            incoming = OCEAN_FETCH
        else:
            # Land at the very edge, with no sea upwind of it inside the map at all.
            incoming = EDGE_LAND
        if sea[j][i]:
            # Over water the air loads up; a long fetch arrives wetter than a strait.
            carried[j][i] = min(1.0, incoming + 0.09)
            rain[j][i] = 1.0
            continue
        rise = 0.0
        if grid.holds(*upwind) and not sea[upwind[1]][upwind[0]]:
            rise = max(0.0, elevation[j][i] - elevation[upwind[1]][upwind[0]])
        # Air holds less the higher and colder it gets; what it cannot hold, it drops.
        capacity = rcp_exp(max(0.0, elevation[j][i] - sea_level) * RAIN_PER_RISE)
        dropped = max(0.0, incoming - incoming * capacity) + rise * 1.6 * incoming
        dropped = min(incoming, dropped)
        # A little always falls, or the deep lee reads as lunar rather than dry.
        dropped = max(dropped, incoming * 0.04)
        rain[j][i] = dropped
        # Land puts water back into the air, and how much depends on how much just fell
        # on it. Without any recharge the sweep is a one-way budget — whatever the air
        # picked up over the sea is all it will ever have — so on a continent sixty cells
        # wide it is exhausted long before the middle and the whole interior comes out a
        # desert, the windward flank of an inland range included, which then casts no
        # shadow because there is nothing left to drop.
        #
        # Recycling what fell is the right form, and topping the air back up towards
        # saturation is not: the latter recharges a rain shadow just as fast as a river
        # plain, so the lee of a range refills within a dozen cells and the shadow
        # disappears — which is exactly what it did. Ground that rain has just fallen on
        # gives water back; ground in a lee has none to give.
        left = max(0.0, incoming - dropped)
        carried[j][i] = min(1.0, left + RECYCLED * dropped)
    return rain


def _temperature(grid: Grid, *, elevation: Field, sea: list[list[bool]],
                 owner: list[list[int]], keys: tuple[str, ...],
                 temperature_of: dict[str, float], sea_level: float) -> Field:
    """The region's own climate, cooled by height. Peaks are cold everywhere."""
    size = grid.size
    out = grid.filled(0.0)
    for j in range(size):
        for i in range(size):
            index = owner[j][i]
            key = keys[index] if 0 <= index < len(keys) else None
            base = temperature_of.get(key, 0.0) if key else 0.0
            if sea[j][i]:
                out[j][i] = base
                continue
            out[j][i] = max(-1.0, min(
                1.0, base - LAPSE * max(0.0, elevation[j][i] - sea_level)))
    return out


def _reconcile(grid: Grid, *, rain: Field, sea: list[list[bool]],
               owner: list[list[int]], keys: tuple[str, ...],
               moisture_of: dict[str, float], stated: dict[str, bool],
               seed: str) -> tuple[Field, Field]:
    """Blend what fell with what the writer said, and measure the rain shadow.

    Where the writer described the weather, their words carry most of the answer — the
    model only says which parts of their region are wetter than the rest. Where they
    said nothing, the model speaks.
    """
    size = grid.size
    land = [(i, j) for j in range(size) for i in range(size) if not sea[j][i]]
    if not land:
        return grid.filled(0.5), grid.filled(0.0)

    fell = sorted(rain[j][i] for i, j in land)
    middle = fell[len(fell) // 2] or 1e-6
    wettest = fell[-1] or 1e-6

    # One field rather than a fractal sample per land cell: it varies over fifteen cells
    # and is only there to keep the moisture from reading as a smooth gradient.
    grain_field = noise.field(f"{seed}|wet", size, wavelength=15.0, octaves=3, stride=3)

    moisture = grid.filled(1.0)
    shadow = grid.filled(0.0)
    for i, j in land:
        index = owner[j][i]
        key = keys[index] if 0 <= index < len(keys) else None
        modelled = min(1.0, rain[j][i] / (middle * 2.0))
        grain = grain_field[j][i]
        modelled = max(0.0, min(1.0, modelled * 0.82 + grain * 0.18))
        if key is not None and stated.get(key):
            said = moisture_of.get(key, 0.5)
            moisture[j][i] = max(0.0, min(
                1.0, said * PROSE_WEIGHT + modelled * (1.0 - PROSE_WEIGHT)))
        else:
            moisture[j][i] = modelled
        shadow[j][i] = max(0.0, 1.0 - rain[j][i] / wettest)
    return moisture, shadow


def dryness_across(grid: Grid, climate: Climate, mountain_spine,
                   sea: list[list[bool]]) -> tuple[float, float]:
    """Mean rain on the windward and lee flanks of a spine.

    Not used by the pipeline — this is what the test asks, and the honest way to answer
    "is there actually a rain shadow" is to measure one.

    Measuring it is fiddlier than it sounds, and the first version got a real shadow
    wrong. A wide band either side of the spine takes in the far coast on both flanks,
    and a coastal cell with an inlet upwind of it is wet whichever side of the mountain
    it stands on — so the average reported no shadow at all while a transect through the
    crest showed rain falling from 0.37 to 0.0006 over twelve cells. Three restrictions
    fix it: only the middle of the range, where a flank is a flank rather than the end of
    one; only ground close enough to be on the mountain; and only ground with unbroken
    land between it and the crest, so that water the air crossed on the way is not
    counted as the mountain's doing.
    """
    if len(mountain_spine) < 3:
        return (0.0, 0.0)
    wx, wy = climate.wind.vector
    low = int(len(mountain_spine) * 0.2)
    high = max(low + 1, int(len(mountain_spine) * 0.8))
    middle = list(mountain_spine[low:high])

    near_limit = grid.size * 0.02
    far_limit = grid.size * 0.11

    windward: list[float] = []
    lee: list[float] = []
    for j in range(grid.size):
        for i in range(grid.size):
            if sea[j][i]:
                continue
            closest = min(middle, key=lambda p: math.dist((i, j), p))
            near = math.dist((i, j), closest)
            if near > far_limit or near < near_limit:
                continue
            if not _dry_land_between(sea, (i, j), closest):
                continue
            side = (i - closest[0]) * -wx + (j - closest[1]) * -wy
            (windward if side > 0 else lee).append(climate.rain[j][i])
    return (sum(windward) / len(windward) if windward else 0.0,
            sum(lee) / len(lee) if lee else 0.0)


def _dry_land_between(sea: list[list[bool]], start: tuple[int, int],
                      finish: tuple[float, float]) -> bool:
    """Whether the straight run from a cell to the crest stays on land throughout."""
    ax, ay = start
    bx, by = finish
    steps = max(1, int(max(abs(bx - ax), abs(by - ay))))
    for k in range(steps + 1):
        t = k / steps
        i = int(round(ax + (bx - ax) * t))
        j = int(round(ay + (by - ay) * t))
        if 0 <= j < len(sea) and 0 <= i < len(sea[0]) and sea[j][i]:
            return False
    return True
