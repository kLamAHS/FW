"""What grows where — and reconciling that with what the writer said grows there.

Temperature and moisture decide vegetation. That is a real relationship and a
well-mapped one: cold and dry is tundra, warm and wet is forest, warm and dry is
desert, and the boundaries between them are gentle. Running it gives a map whose
forests sit where forests belong — on the wet side of the range, in the river valleys —
rather than wherever a region happens to have been labelled.

But the writer's word beats the model, always. Someone who wrote "deep forest and low
hills" gets a wooded region even if the rain the model carried there says otherwise —
because their sentence is the fact and the model is an inference. What the model
supplies is the *variation* their sentence does not contain: which parts of the wood
are thickest, where it gives way to moor, where the trees stop at the water.

Every biome is one of the terrain kinds the rest of the application already knows, so a
road over any of them still costs what the travel engine says it costs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.mapgen import noise
from fw.core.mapgen.attributes import TERRAIN_KINDS
from fw.core.mapgen.grid import Field, Grid

# The vegetation table, as (coldest, warmest, driest, wettest) bands. Read in order;
# the first band a cell falls in wins, so the order is the classification.
#
# Deliberately coarse. A finer table would imply a precision the inputs do not have —
# these are numbers read out of a novelist's adjectives.
WHITTAKER: tuple[tuple[str, float, float, float, float], ...] = (
    ("glacier", -1.01, -0.62, 0.00, 1.01),
    ("steppe",  -0.62, -0.25, 0.00, 0.32),
    ("forest",  -0.62, -0.25, 0.32, 1.01),     # taiga, in this vocabulary
    ("desert",  -0.25,  1.01, 0.00, 0.17),
    ("steppe",  -0.25,  0.34, 0.17, 0.36),
    ("farmland", -0.25, 0.34, 0.36, 0.52),
    ("plain",   -0.25,  0.34, 0.52, 0.66),
    ("forest",  -0.25,  0.34, 0.66, 1.01),
    ("steppe",   0.34,  1.01, 0.17, 0.40),
    ("farmland", 0.34,  1.01, 0.40, 0.58),
    ("forest",   0.34,  1.01, 0.58, 1.01),
)

# Above these heights the ground itself decides, whatever the weather says.
MOUNTAIN_AT = 0.66
HILLS_AT = 0.46
# Permanent ice is rare and high. Set this low and every cold region's crest turns
# white — a world the writer called "cold, wet forest" came out a third glacier.
GLACIER_ABOVE = 0.86
GLACIER_BELOW = -0.70         # and it has to be genuinely frozen, not merely cold

MARSH_WETNESS = 0.86          # how wet low flat ground has to be to be a marsh
MARSH_HEIGHT = 0.26

# How strongly a region's own claimed terrain pulls its cells toward that kind. High:
# what the writer named first should be most of what the region is, and the model's job
# is the variation between — at 0.62 a region described as forest came out mostly hills.
CLAIM_PULL = 0.80


@dataclass
class BiomeField:
    """A terrain kind for every cell, and why."""

    grid: Grid
    codes: list[list[int]]
    kinds: tuple[str, ...]
    because: dict[str, str] = field(default_factory=dict)

    def terrain(self, i: int, j: int) -> str:
        code = self.codes[j][i]
        return self.kinds[code] if 0 <= code < len(self.kinds) else "plain"

    def cells_of(self, kind: str) -> list[tuple[int, int]]:
        if kind not in self.kinds:
            return []
        code = self.kinds.index(kind)
        return [(i, j) for j in range(self.grid.size) for i in range(self.grid.size)
                if self.codes[j][i] == code]

    def tally(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.codes:
            for code in row:
                if 0 <= code < len(self.kinds):
                    counts[self.kinds[code]] = counts.get(self.kinds[code], 0) + 1
        return counts


KINDS = tuple(sorted(TERRAIN_KINDS))


def classify(grid: Grid, *, elevation: Field, temperature: Field, moisture: Field,
             sea: list[list[bool]], owner: list[list[int]], keys: tuple[str, ...],
             claims: dict[str, dict[str, float]], seed: str,
             sea_level: float = 0.10) -> BiomeField:
    """Give every land cell a terrain kind."""
    size = grid.size
    codes = [[-1] * size for _ in range(size)]
    index_of = {kind: n for n, kind in enumerate(KINDS)}
    because: dict[str, str] = {}

    for key in keys:
        mix = claims.get(key) or {}
        if mix:
            named = ", ".join(k for k, _ in sorted(mix.items(), key=lambda p: -p[1])[:2])
            because[key] = f"you described it as {named}"
        else:
            because[key] = "you did not say, so its weather decided"

    # One field rather than a fractal sample per cell. It varies over half a dozen
    # cells, which is what gives a region's ground its patchiness; asking for it twenty
    # thousand times separately was one of the two largest costs in planning a map.
    patches = noise.field(f"{seed}|biome", size, wavelength=6.5, octaves=3, stride=1)

    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                codes[j][i] = index_of["ocean"]
                continue
            kind = _from_weather(elevation[j][i], temperature[j][i], moisture[j][i],
                                 sea_level)
            index = owner[j][i]
            key = keys[index] if 0 <= index < len(keys) else None
            claimed = claims.get(key) if key else None
            if claimed:
                kind = _pull_toward(kind, claimed, i, j, patches)
            # Permanent ice has to be asked for. It is the most dramatic thing a biome
            # can be, so it must never arrive as the model's idea of variety inside a
            # region the writer called forest — which is where a third of one came from.
            if kind == "glacier" and claimed and "glacier" not in claimed:
                kind = "mountain" if elevation[j][i] >= MOUNTAIN_AT else "hills"
            codes[j][i] = index_of.get(kind, index_of["plain"])
    return BiomeField(grid=grid, codes=codes, kinds=KINDS, because=because)


def _from_weather(height: float, temperature: float, wetness: float,
                  sea_level: float) -> str:
    """What the weather alone would grow here."""
    if height >= GLACIER_ABOVE and temperature < GLACIER_BELOW:
        return "glacier"
    if height >= MOUNTAIN_AT:
        return "mountain"
    if height >= HILLS_AT:
        return "hills"
    if height <= MARSH_HEIGHT + sea_level and wetness >= MARSH_WETNESS:
        return "marsh"
    for kind, coldest, warmest, driest, wettest in WHITTAKER:
        if coldest <= temperature < warmest and driest <= wetness < wettest:
            return kind
    return "plain"


def _pull_toward(kind: str, claimed: dict[str, float], i: int, j: int,
                 patches: Field) -> str:
    """Bend a cell toward what the writer said the region is.

    Not a wholesale overwrite. A region described as "forest and low hills" should be
    mostly wood with hills through it, and the parts the model made high should stay
    high — a forest painted over a mountaintop is not what they meant either.
    """
    # Only true mountain overrides what the writer said. Ice does not: a region they
    # called deep forest is a forest even where the model made it high and cold, and
    # letting the ice win turned a third of one world white.
    if kind == "mountain":
        return kind
    total = sum(claimed.values()) or 1.0
    # Coherent noise, not per-cell. Rolling independently at every cell dithers the
    # region into salt and pepper: a forest with farmland speckled through it, cell by
    # cell. Sampling a smooth field instead gives patches a few cells across — a wood
    # with clearings in it, which is what the writer meant.
    roll = patches[j][i]
    running = 0.0
    for candidate, weight in sorted(claimed.items(), key=lambda p: (-p[1], p[0])):
        running += (weight / total) * CLAIM_PULL
        if roll < running:
            # Their word for open water or shoreline describes the region's edge, not
            # its middle; taking it literally would flood the interior.
            return kind if candidate in ("ocean", "coast") else candidate
    return kind
