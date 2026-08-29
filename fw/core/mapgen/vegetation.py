"""What grows where, as a density rather than a label.

The map used to answer "is this forest?" with yes or no, because a cell held one biome
out of a list and forest was one of the entries. That is a modelling decision with a
visible consequence: a wood drawn from it has a hard edge, every part of it is equally
thick, and the edge follows whatever produced the classification — usually a border. A
reader does not have to know why that looks wrong to see that it does.

So nothing here classifies. Every position gets a *canopy density* between nothing and
closed forest, worked out from things that are already true of that position: how much
rain reaches it, how warm it is, how steep it stands, how near the surface its water
table sits. Where the density happens to cross a threshold is where a reader will say
the wood begins, and because the density is continuous and has fractal variation in it,
that line is ragged, has clearings inside it, thins as it climbs, and stops at the
treeline — none of which had to be implemented, because all of them are what a threshold
on a continuous field does.

The same argument gives the marsh. A marsh is not a kind of place that can be assigned;
it is what low, flat, badly-drained ground *is*. Working it out from slope, water table
and drainage puts marshes at river mouths and in the dead ground behind levees, which is
where they are, instead of wherever a table happened to say "wet and warm".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.mapgen import noise
from fw.core.mapgen.findings import Finding
from fw.core.mapgen.grid import Field, Grid

# How wet the ground has to be before trees do well, and where they stop caring.
DRY_LIMIT = 0.28              # below this, no closed canopy at any temperature
WET_ENOUGH = 0.62             # above this, moisture is no longer what limits the trees

# The cold end. Trees thin and stop; the number is where they have stopped entirely.
# Set well below where a temperate reader's instinct puts it, because boreal forest is
# the largest forest on Earth and it grows in country nobody would call mild: a cutoff
# that felt right for oak wiped the trees off a march the writer had called wooded.
COLD_LIMIT = -1.00
COLD_COMFORT = -0.55          # and where the cold has stopped mattering

# The treeline, as a height, and how far it moves with temperature. A warm world carries
# its forest higher up the mountain, which is the whole reason a treeline is a line and
# not a contour someone drew.
TREELINE = 0.58
TREELINE_WARMTH = 0.24
TREELINE_FADE = 0.16          # over what height the wood thins out to nothing

# Very steep ground holds less soil, so it holds fewer trees. Not none: a wooded gorge
# is a real thing, and zeroing this put a bald stripe down every valley.
STEEP_LIMIT = 0.30
STEEP_KEEP = 0.45

# The grain of a wood: how much of the canopy is variation, and over what distance.
GRAIN = 0.42
GRAIN_SCALE = 7.5
CLEARING_SCALE = 3.1
CLEARING = 0.20

# Marsh. Waterlogged ground is flat, wet and unable to drain — all three, which is why
# it is worked out rather than assigned.
MARSH_SLOPE = 0.020           # steeper than this and the water leaves
MARSH_TABLE = 0.70            # and the water table has to be this near the surface
MARSH_FADE = 0.012            # over what slope the marsh gives out

# The water table, measured as height above the nearest drainage: how far a cell stands
# above the first channel its water reaches. This is the honest measure and a weighted
# mixture of slope and catchment is not — a cell can be flat, well watered and eighty
# metres up the side of a hill, and it is not a marsh. Ground a hand's breadth above the
# river is a flood plain whatever else is true of it.
TABLE_REACH = 0.10            # height above the local channel at which the table is deep
CHANNEL_SHARE = 0.06          # the share of land carrying enough water to be drainage

# What the writer said outranks all of it. This is how far a region they called wooded is
# pulled towards being wooded, and it is deliberately most of the way.
CLAIM_PULL = 0.78

# What a writer's word does to the limits, where they have said a region is wooded.
#
# The first attempt let the model veto them: a march described as forest came out with a
# canopy of 0.001, because the ground was high and the air was cold and the treeline said
# no. That is the model misunderstanding its job. The second attempt put a floor under
# the result, which honours the sentence but makes the treeline meaningless inside the
# region — trees at the same density on the summit as in the valley.
#
# This is the third and it is the one that is actually right. A writer who says their
# cold high march is wooded has told us something about *their world's trees*: they are
# hardier than the ones the model was calibrated on. So the claim raises the treeline and
# lowers the cold limit rather than overriding what those then say. The wood survives;
# the summits above it are still bare; and the shape of the wood is still a consequence
# of the ground. The disagreement is worth reporting either way, which is what the
# finding below is for.
HARDY_TREELINE = 0.34         # how far a claimed wood raises its own treeline
HARDY_COLD = 0.55             # and how much cold it will stand
CLAIMED_FLOOR = 0.30          # and what survives even above that raised line

# Above this share of a claimed wood standing on ground the model calls treeless, the
# tension is worth telling the writer about rather than quietly resolving.
CONTRADICTION_SHARE = 0.5

# Below this many cells, a region is too small for its mean to mean anything.
MEANINGFUL_REGION = 30

# The bands a reader sees. Not used to compute anything — the density is the model — but
# the renderer needs somewhere to put a threshold, and naming them here keeps the picture
# and the prose describing it in step.
BANDS = (0.92, 0.76, 0.51, 0.24, 0.07)


@dataclass
class Vegetation:
    """Cover, as continuous fields."""

    canopy: Field                 # 0 open, 1 closed forest
    marsh: Field                  # 0 dry, 1 standing water in the ground
    water_table: Field            # 0 deep, 1 at the surface
    drainage: Field               # 0 cannot shed water, 1 sheds it readily
    notes: list[Finding] = field(default_factory=list)

    def wooded_share(self, sea: list[list[bool]], at: float = 0.24) -> float:
        """The share of land a reader would call wooded, at a given band."""
        wooded = land = 0
        for j in range(len(self.canopy)):
            for i in range(len(self.canopy)):
                if sea[j][i]:
                    continue
                land += 1
                if self.canopy[j][i] >= at:
                    wooded += 1
        return wooded / land if land else 0.0


def plan_vegetation(grid: Grid, *, elevation: Field, slope: Field, flow: Field,
                    downstream: list[list[int]], moisture: Field, temperature: Field,
                    sea: list[list[bool]], owner: list[list[int]],
                    keys: tuple[str, ...], wooded: dict[str, float], seed: str,
                    sea_level: float = 0.0) -> Vegetation:
    """Work out cover from the ground and the weather, then hear the writer out."""
    size = grid.size
    table = _water_table(grid, elevation=elevation, flow=flow,
                         downstream=downstream, sea=sea)
    drainage = _drainage(grid, slope=slope, sea=sea)
    marsh = _marsh(grid, table=table, drainage=drainage, slope=slope, sea=sea)

    grain = noise.field(f"{seed}|canopy", size, wavelength=GRAIN_SCALE, octaves=4,
                        stride=2)
    clearings = noise.field(f"{seed}|glade", size, wavelength=CLEARING_SCALE, octaves=3,
                            stride=1)

    claimed = grid.filled(-1.0)
    for j in range(size):
        for i in range(size):
            index = owner[j][i]
            if 0 <= index < len(keys):
                said = wooded.get(keys[index])
                if said is not None:
                    claimed[j][i] = said
    # Spread, so a region the writer called wooded raises the country around it rather
    # than stamping its outline into the treeline. The same argument as the ground
    # heights: the wood does not know where the border is.
    stated = grid.spread([[max(0.0, v) for v in row] for row in claimed],
                         stride=3, rounds=4)
    speaks = grid.spread([[1.0 if v >= 0.0 else 0.0 for v in row] for row in claimed],
                         stride=3, rounds=4)

    canopy = grid.filled(0.0)
    argued = claimed_cells = 0
    argued_cells: list[tuple[int, int]] = []
    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                continue
            voice = speaks[j][i]
            said = stated[j][i] / voice if voice > 0.02 else 0.0
            hardy = min(1.0, said * min(1.0, voice) * 2.0)
            density = _canopy_at(
                height=elevation[j][i], wet=moisture[j][i], warm=temperature[j][i],
                steep=slope[j][i], marshy=marsh[j][i], hardy=hardy,
                grain=grain[j][i], glade=clearings[j][i])
            if voice > 0.02:
                pull = CLAIM_PULL * min(1.0, voice)
                density = density * (1.0 - pull) + said * pull
                # Above even the raised treeline the ground is bare, whoever says what.
                # A summit with trees on it is the one thing no reader forgives.
                limit = _limits(elevation[j][i], temperature[j][i], marsh[j][i], hardy)
                density *= CLAIMED_FLOOR + (1.0 - CLAIMED_FLOOR) * limit
                if said > 0.25:
                    claimed_cells += 1
                    if _limits(elevation[j][i], temperature[j][i], marsh[j][i]) < 0.1:
                        argued += 1
                        argued_cells.append((i, j))
            canopy[j][i] = 0.0 if density < 0.0 else (1.0 if density > 1.0 else density)

    notes: list[Finding] = []
    if claimed_cells and argued >= claimed_cells * CONTRADICTION_SHARE:
        notes.append(Finding(
            code="contradiction", severity="note",
            message=(
                "you describe woodland on ground that would be above the treeline on "
                f"Earth — {round(100 * argued / claimed_cells)}% of it. The wood is drawn "
                "where you put it, and the map has taken your word for it that these "
                "trees are hardier than ours: it has raised the treeline here rather "
                "than moved them. If that is not what you meant, the country under the "
                "wood wants to be lower or milder."),
            subjects=tuple(sorted(
                {keys[owner[j][i]] for i, j in argued_cells
                 if 0 <= owner[j][i] < len(keys)}))))

    _honour(grid, canopy, owner=owner, keys=keys, wooded=wooded, sea=sea)

    return Vegetation(canopy=canopy, marsh=marsh, water_table=table, drainage=drainage,
                      notes=notes)


def _honour(grid: Grid, canopy: Field, *, owner: list[list[int]],
            keys: tuple[str, ...], wooded: dict[str, float],
            sea: list[list[bool]]) -> None:
    """Make a region as wooded as the writer said it was, keeping the model's pattern.

    "Mountains and forest" is a statement about a *whole march* — how much of it is under
    trees — and not about any acre of it. So it is honoured as an average: the region's
    canopy is scaled until its mean is the share the writer named, and everything about
    *where* the trees are inside it is left to the model. The wet valleys stay the
    thickest, the dry lee stays the thinnest, the summits stay bare; there is simply the
    amount of wood the writer asked for.

    Reading the claim cell by cell instead was the mistake this replaces, and it produced
    the exact inversion the whole design is meant to avoid: the one march the writer had
    described as forest came out the least wooded region on the map, at two thirds of the
    world average, because it was also cold and high and the model outvoted them
    everywhere at once.
    """
    size = grid.size
    for index, key in enumerate(keys):
        share = wooded.get(key)
        if share is None or share <= 0.0:
            continue
        cells = [(i, j) for j in range(size) for i in range(size)
                 if owner[j][i] == index and not sea[j][i]]
        if len(cells) < MEANINGFUL_REGION:
            continue
        wanted = min(1.0, share)
        # A few passes rather than a formula: the clamp at one makes the relationship
        # between the gain and the mean non-linear, and three corrections land inside a
        # per cent of the target from any starting point.
        for _ in range(4):
            mean = sum(canopy[j][i] for i, j in cells) / len(cells)
            if mean <= 1e-6:
                for i, j in cells:
                    canopy[j][i] = wanted
                break
            if abs(mean - wanted) < 0.01:
                break
            gain = wanted / mean
            for i, j in cells:
                lifted = canopy[j][i] * gain
                canopy[j][i] = 1.0 if lifted > 1.0 else lifted


def _canopy_at(*, height: float, wet: float, warm: float, steep: float, marshy: float,
               grain: float, glade: float, hardy: float = 0.0) -> float:
    """How much of a cell is under trees."""
    density = _ramp(wet, DRY_LIMIT, WET_ENOUGH)
    density *= _limits(height, warm, marshy, hardy)
    if steep > STEEP_LIMIT:
        density *= STEEP_KEEP
    elif steep > 0.0:
        density *= 1.0 - (1.0 - STEEP_KEEP) * (steep / STEEP_LIMIT)
    # The grain is what makes a wood look like a wood: an edge that wanders, thin places,
    # and glades inside it. Multiplying rather than adding keeps it out of ground that
    # was never going to grow trees anyway — otherwise a desert acquires a stipple.
    density *= (1.0 - GRAIN) + 2.0 * GRAIN * grain
    density *= 1.0 - CLEARING * glade
    return density


def _limits(height: float, warm: float, marshy: float, hardy: float = 0.0) -> float:
    """The three things that stop a tree wherever it is: cold, height, and standing water.

    `hardy` is how strongly the writer has said there are trees here, and it moves the
    limits rather than being applied after them — see the note by HARDY_TREELINE.
    """
    cold = _ramp(warm, COLD_LIMIT - HARDY_COLD * hardy, COLD_COMFORT)
    line = TREELINE + TREELINE_WARMTH * warm + HARDY_TREELINE * hardy
    above = _ramp(line + TREELINE_FADE - height, 0.0, TREELINE_FADE)
    return cold * above * (1.0 - 0.75 * marshy)


def _ramp(value: float, low: float, high: float) -> float:
    """0 below, 1 above, smooth between — the shape every threshold here wants."""
    if high <= low:
        return 1.0 if value >= high else 0.0
    t = (value - low) / (high - low)
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def _water_table(grid: Grid, *, elevation: Field, flow: Field,
                 downstream: list[list[int]], sea: list[list[bool]]) -> Field:
    """How near the surface the ground water stands, from height above the drainage.

    For every cell, how far it stands above the first channel its own water reaches. That
    single number is what decides whether ground is waterlogged, and it is why a marsh
    turns up on a flood plain and a bog on a valley floor rather than scattered over
    every damp field on the map: a cell can be flat, well watered and forty feet up the
    side of a hill, and none of that makes it wet.

    It costs one pass. Cells are walked from the lowest up, so a cell's receiver has
    always been answered before the cell is — the receiver is by construction lower —
    and each one either *is* drainage, and stands nothing above it, or stands as far
    above it as its receiver did plus its own drop.
    """
    size = grid.size
    land = [(i, j) for j in range(size) for i in range(size) if not sea[j][i]]
    if not land:
        return grid.filled(1.0)

    ranked = sorted(flow[j][i] for i, j in land)
    edge = ranked[max(0, int(len(ranked) * (1.0 - CHANNEL_SHARE)) - 1)]

    above = grid.filled(0.0)
    for i, j in sorted(land, key=lambda c: (elevation[c[1]][c[0]], c[1], c[0])):
        if flow[j][i] >= edge:
            continue                                   # this cell is the drainage
        target = downstream[j][i]
        if target < 0:
            continue
        ti, tj = target % size, target // size
        if sea[tj][ti]:
            continue
        drop = elevation[j][i] - elevation[tj][ti]
        above[j][i] = above[tj][ti] + (drop if drop > 0.0 else 0.0)

    out = grid.filled(1.0)
    for i, j in land:
        out[j][i] = 1.0 - _ramp(above[j][i], 0.0, TABLE_REACH)
    return grid.blurred(out)


def _drainage(grid: Grid, *, slope: Field, sea: list[list[bool]]) -> Field:
    """How readily a cell sheds the water that reaches it."""
    size = grid.size
    out = grid.filled(1.0)
    for j in range(size):
        for i in range(size):
            if not sea[j][i]:
                out[j][i] = _ramp(slope[j][i], 0.0, 0.05)
    return out


def _marsh(grid: Grid, *, table: Field, drainage: Field, slope: Field,
           sea: list[list[bool]]) -> Field:
    """Ground with water in it that has nowhere to go.

    All three conditions at once, multiplied rather than added: flat ground that drains
    is a meadow, wet ground on a slope is a spring line, and neither is a marsh. Only
    where a cell is flat *and* wet *and* cannot drain does it become one, which is why
    marshes come out along the lower reaches of rivers and behind the bars at their
    mouths rather than scattered over every damp field.
    """
    size = grid.size
    out = grid.filled(0.0)
    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                continue
            flat = 1.0 - _ramp(slope[j][i], MARSH_SLOPE, MARSH_SLOPE + MARSH_FADE)
            if flat <= 0.0:
                continue
            wet = _ramp(table[j][i], MARSH_TABLE, 1.0)
            held = 1.0 - drainage[j][i]
            out[j][i] = flat * wet * held
    return grid.blurred(out)


def treeline_of(temperature: float) -> float:
    """The height trees stop at, for a given warmth — used to explain a bare summit."""
    return TREELINE + TREELINE_WARMTH * temperature


def band_of(density: float) -> int:
    """Which of the drawn density bands a value falls in; 0 is open ground."""
    for n, edge in enumerate(BANDS):
        if density >= edge:
            return len(BANDS) - n
    return 0


