"""What the ground is good for.

The old map had resources because the writer had listed some — "iron and timber" on a
region page — and every cell of that region was equally irony. That is a note attached
to a name, not a fact about a place, and nothing could be done with it: a town could not
be sited near the iron because there was no near.

Here each one is a field, and each is worked out from things the physical model already
knows. Soil is where erosion put the sediment it carried. Grain grows where there is
soil and water and not too much slope. Timber is the canopy. Stone is where the rock is
exposed, which is where the ground is steep and high and the soil has all been washed
off it. Fish are on the shelf. None of it is invented, and all of it can be pointed at:
this valley grows grain *because* it is flat and wet and forty feet of silt deep.

What the writer said still wins, and wins in the same way it does for woodland — as a
statement about the region as a whole, which the model then distributes. Someone who
writes "the Iron Spine, for its mines" gets a march whose ore is where ore would be, and
enough of it to be worth the name.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.mapgen import noise
from fw.core.mapgen.findings import Finding
from fw.core.mapgen.grid import Field, Grid

# Grain. It wants soil, water it can reach, and ground level enough to plough.
ARABLE_SLOPE = 0.075          # above this, nothing is ploughed
# Soil is not only what the rivers brought. Three quarters of the continent has no
# settled sediment at all — deposition puts it in the valleys, which is where deposition
# should put it — and taking that as the whole story left the kingdom able to farm four
# tenths of one per cent of itself. Ordinary ground weathers its own soil; a flood plain
# has that *and* the silt on top, and is the better farmland for it.
ARABLE_RESIDUAL = 0.55        # how good the soil is on gentle ground with no silt
ARABLE_SILT = 0.004           # depth of settled sediment that makes it as good as it gets
ARABLE_WET_LOW = 0.22         # too dry to farm without irrigation nobody has
ARABLE_WET_HIGH = 0.60        # and no better for being wetter than this
ARABLE_COLD = -0.62           # and it will not ripen below this

# Pasture takes the ground grain will not: steeper, thinner, colder, wetter.
PASTURE_SLOPE = 0.20
PASTURE_COLD = -0.85

# Stone and ore want the opposite of soil — rock the weather has stripped bare.
STONE_SLOPE = 0.11
STONE_SOIL = 0.004            # more settled silt than this and the rock is buried
ORE_HEIGHT = 0.34             # ore is found in the roots of ranges, which is to say high
ORE_SCALE = 13.0              # and in veins, not evenly
ORE_RARITY = 0.60             # most of a range has none
ORE_RISE = 0.14               # over what height above the threshold a vein comes good

# Fish want shallow water: the shelf, not the deep.
FISH_DEPTH = 0.075            # depth at which the shelf stops being shelf
FISH_REACH = 3                # how far inland a village can be and still be a fishing one

# How far the writer's own list of a region's resources tilts the ground under it.
CLAIM_PULL = 0.55
MEANINGFUL_REGION = 30

# Below this average, the model has effectively found none of something and scaling it up
# would be multiplying nothing; it is seeded instead, over this share of the region.
BARELY_THERE = 1e-4
SEEDED_SHARE = 0.09

KINDS = ("arable", "pasture", "timber", "stone", "ore", "fish")


@dataclass
class Resources:
    """What each cell offers, as fields between nothing and plenty."""

    fields: dict[str, Field]
    notes: list[Finding] = field(default_factory=list)

    def at(self, kind: str, i: int, j: int) -> float:
        grid = self.fields.get(kind)
        return grid[j][i] if grid else 0.0

    def share(self, kind: str, sea: list[list[bool]], at: float = 0.5) -> float:
        grid = self.fields.get(kind)
        if not grid:
            return 0.0
        land = good = 0
        for j in range(len(grid)):
            for i in range(len(grid)):
                if sea[j][i]:
                    continue
                land += 1
                if grid[j][i] >= at:
                    good += 1
        return good / land if land else 0.0


def plan_resources(grid: Grid, *, elevation: Field, slope: Field, soil: Field,
                   water_table: Field, moisture: Field, temperature: Field,
                   canopy: Field, marsh: Field, sea: list[list[bool]],
                   owner: list[list[int]], keys: tuple[str, ...],
                   claimed: dict[str, dict[str, float]], seed: str,
                   sea_level: float = 0.0) -> Resources:
    """Derive what the ground offers, then let the writer's own notes tilt it."""
    size = grid.size
    ore_veins = noise.field(f"{seed}|ore", size, wavelength=ORE_SCALE, octaves=3,
                            stride=2)

    out = {kind: grid.filled(0.0) for kind in KINDS}
    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                continue
            steep = slope[j][i]
            deep = soil[j][i]
            warm = temperature[j][i]
            wet = moisture[j][i]

            soil_here = ARABLE_RESIDUAL + (1.0 - ARABLE_RESIDUAL) * _rises(
                deep, ARABLE_SILT)
            plough = (_falls(steep, ARABLE_SLOPE)
                      * soil_here
                      * _between(wet, ARABLE_WET_LOW, ARABLE_WET_HIGH)
                      * _rises(warm - ARABLE_COLD, 0.35)
                      * (1.0 - marsh[j][i]))
            out["arable"][j][i] = plough

            # Grazing takes what the plough will not, so it is deliberately the
            # complement: ground that is merely steep, or thin, or cold, is pasture.
            out["pasture"][j][i] = (_falls(steep, PASTURE_SLOPE)
                                    * _rises(warm - PASTURE_COLD, 0.30)
                                    * (1.0 - marsh[j][i])
                                    * (1.0 - 0.7 * canopy[j][i])
                                    * (1.0 - 0.55 * plough))

            out["timber"][j][i] = canopy[j][i]

            bare = _rises(steep, STONE_SLOPE) * _falls(deep, STONE_SOIL)
            out["stone"][j][i] = bare
            height = elevation[j][i] - sea_level
            if height > ORE_HEIGHT:
                vein = max(0.0, (ore_veins[j][i] - ORE_RARITY) / (1.0 - ORE_RARITY))
                out["ore"][j][i] = vein * _rises(height - ORE_HEIGHT, ORE_RISE)

    _fish(grid, out["fish"], elevation=elevation, sea=sea, sea_level=sea_level)

    # Where each resource would be if it were anywhere, for a region the writer says has
    # some and the model gave none. See `_honour`.
    proxy = {
        "ore": [[elevation[j][i] - sea_level for i in range(size)] for j in range(size)],
        "stone": slope,
        "timber": canopy,
        "fish": out["fish"],
        "arable": [[1.0 - slope[j][i] for i in range(size)] for j in range(size)],
        "pasture": [[1.0 - slope[j][i] for i in range(size)] for j in range(size)],
    }
    notes = _honour(grid, out, owner=owner, keys=keys, claimed=claimed, sea=sea,
                    proxy=proxy)
    return Resources(fields=out, notes=notes)


def _fish(grid: Grid, into: Field, *, elevation: Field, sea: list[list[bool]],
          sea_level: float) -> None:
    """Shallow water near enough to land to be worked from it.

    A fishery is a fact about the *shore*, not about the sea: nobody lives on the water.
    So the value lands on the land cells within reach of shelf, which is what lets a
    village be sited for its fishing.
    """
    size = grid.size
    shelf = [[0.0] * size for _ in range(size)]
    for j in range(size):
        for i in range(size):
            if not sea[j][i]:
                continue
            deep = sea_level - elevation[j][i]
            shelf[j][i] = _falls(deep, FISH_DEPTH)

    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                continue
            best = 0.0
            for b in range(max(0, j - FISH_REACH), min(size, j + FISH_REACH + 1)):
                for a in range(max(0, i - FISH_REACH), min(size, i + FISH_REACH + 1)):
                    if sea[b][a] and shelf[b][a] > best:
                        best = shelf[b][a]
            into[j][i] = best


def _honour(grid: Grid, out: dict[str, Field], *, owner: list[list[int]],
            keys: tuple[str, ...], claimed: dict[str, dict[str, float]],
            sea: list[list[bool]], proxy: dict[str, Field]) -> list[Finding]:
    """Give a region what the writer said it has, keeping the model's sense of where.

    The same shape as the woodland claim, and for the same reason. "The Iron Spine, for
    its mines" is a statement about a march, not about an acre: what it fixes is that
    there is ore there, and where inside the march the ore is remains a question about
    the rock.

    Two cases, and the second is the one that matters. Where the model already found
    some, it is scaled up until the region has as much as was claimed. Where the model
    found *none at all* — which is what happened to the Iron Spine, four hundred cells
    and not a gram of ore in any of them, because ore is deliberately scarce and its
    veins had fallen elsewhere — scaling nothing gives nothing. So it is seeded instead,
    on the ground where that resource would be if it were anywhere: ore high up, stone
    where the rock is bare, timber in the thickest wood.

    Only when even that has nowhere to go is it a contradiction worth reporting: a
    landlocked march that lists its fisheries is telling the map something it cannot make
    true, and saying so is more use than a quiet lake.
    """
    size = grid.size
    notes: list[Finding] = []
    for index, key in enumerate(keys):
        wanted = claimed.get(key)
        if not wanted:
            continue
        cells = [(i, j) for j in range(size) for i in range(size)
                 if owner[j][i] == index and not sea[j][i]]
        if len(cells) < MEANINGFUL_REGION:
            continue
        empty: list[str] = []
        for kind, strength in sorted(wanted.items()):
            field_of = out.get(kind)
            if field_of is None:
                continue
            mean = sum(field_of[j][i] for i, j in cells) / len(cells)
            if mean > BARELY_THERE:
                target = mean + (strength - mean) * CLAIM_PULL
                if target > mean:
                    gain = target / mean
                    for i, j in cells:
                        lifted = field_of[j][i] * gain
                        field_of[j][i] = 1.0 if lifted > 1.0 else lifted
                continue
            if not _seed_claim(field_of, cells, proxy.get(kind), strength):
                empty.append(kind)
        if empty:
            notes.append(Finding(
                code="contradiction", severity="note",
                message=(f"you list {', '.join(empty)} in {key}, and there is nowhere in "
                         "that country it could be. The map has not invented any — if it "
                         "should be there, the ground may want to be higher, wetter or "
                         "nearer the sea than it is"),
                subjects=(key,)))
    return notes


def _seed_claim(field_of: Field, cells: list[tuple[int, int]],
                where: Field | None, strength: float) -> bool:
    """Put a resource on the ground best suited to it, and say whether that was possible.

    The share of a region that gets any is small and deliberately so: a seam is a seam.
    What decides *which* cells is the proxy — high ground for ore, bare rock for stone —
    so the writer's iron ends up in their mountains rather than sprinkled evenly over a
    march that happens to contain some.
    """
    if where is None:
        return False
    ranked = sorted(cells, key=lambda c: (-where[c[1]][c[0]], c[1], c[0]))
    if where[ranked[0][1]][ranked[0][0]] <= 0.0:
        return False                       # nowhere in this country suits it at all
    take = max(1, int(len(ranked) * SEEDED_SHARE))
    for rank, (i, j) in enumerate(ranked[:take]):
        # Richest where the ground suits it best, thinning out from there.
        fade = 1.0 - rank / take
        field_of[j][i] = max(field_of[j][i], strength * (0.35 + 0.65 * fade))
    return True


def _rises(value: float, over: float) -> float:
    """0 at nothing, 1 at `over`, smooth between."""
    if over <= 0.0:
        return 1.0 if value > 0.0 else 0.0
    t = value / over
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def _falls(value: float, by: float) -> float:
    """1 at nothing, 0 at `by`."""
    return 1.0 - _rises(value, by)


def _between(value: float, low: float, high: float) -> float:
    """Rises to 1 across the band and stays there — a floor, not a window.

    Deliberately not a bell. Land does not stop growing grain for being wetter than the
    ideal; it stops for being waterlogged, and the marsh term says that separately.
    """
    return _rises(value - low, high - low)
