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
RAIN_PER_RISE = 5.0       # how sharply a rise wrings the air out
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
        incoming = 0.0
        if grid.holds(*upwind):
            incoming = carried[upwind[1]][upwind[0]]
        if sea[j][i]:
            # Over water the air loads up; a long fetch arrives wetter than a strait.
            carried[j][i] = min(1.0, incoming + 0.09)
            rain[j][i] = 1.0
            continue
        rise = 0.0
        if grid.holds(*upwind) and not sea[upwind[1]][upwind[0]]:
            rise = max(0.0, elevation[j][i] - elevation[upwind[1]][upwind[0]])
        elif not grid.holds(*upwind):
            incoming = 0.35
        # Air holds less the higher and colder it gets; what it cannot hold, it drops.
        capacity = rcp_exp(max(0.0, elevation[j][i] - sea_level) * RAIN_PER_RISE)
        dropped = max(0.0, incoming - incoming * capacity) + rise * 1.6 * incoming
        dropped = min(incoming, dropped)
        # A little always falls, or the deep lee reads as lunar rather than dry.
        dropped = max(dropped, incoming * 0.04)
        rain[j][i] = dropped
        carried[j][i] = max(0.0, incoming - dropped)
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

    moisture = grid.filled(1.0)
    shadow = grid.filled(0.0)
    for i, j in land:
        index = owner[j][i]
        key = keys[index] if 0 <= index < len(keys) else None
        modelled = min(1.0, rain[j][i] / (middle * 2.0))
        grain = noise.fbm(f"{seed}|wet", i / 15.0, j / 15.0, octaves=3)
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

    Not used by the pipeline — this is what the test asks, and the honest way to
    answer "is there actually a rain shadow" is to measure one.
    """
    wx, wy = climate.wind.vector
    windward: list[float] = []
    lee: list[float] = []
    for j in range(grid.size):
        for i in range(grid.size):
            if sea[j][i]:
                continue
            near = min((math.dist((i, j), point) for point in mountain_spine),
                       default=1e9)
            if near > grid.size * 0.16 or near < grid.size * 0.03:
                continue
            closest = min(mountain_spine, key=lambda p: math.dist((i, j), p))
            side = (i - closest[0]) * -wx + (j - closest[1]) * -wy
            (windward if side > 0 else lee).append(climate.rain[j][i])
    return (sum(windward) / len(windward) if windward else 0.0,
            sum(lee) / len(lee) if lee else 0.0)
