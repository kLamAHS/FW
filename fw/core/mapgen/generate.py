"""Growing a map from what the writer already wrote (§34, §66, §67, §92).

The writer names three regions, says one is cold mountains rich in iron and another is a
temperate river plain, puts six cities in them, and asks for a map. This builds one:
land shaped like the regions describe themselves, rivers that run downhill and merge,
cities where cities actually appear — at confluences, on harbours, at fords and passes —
and roads between them.

Three rules govern the whole thing.

**It proposes; it never overwrites.** A region the writer has drawn is left exactly as
drawn (§66). Anything generated is marked as generated and can be swept away and redone,
so regeneration is safe and reversible rather than destructive.

**It explains itself.** Every placement carries a sentence saying why, in the writer's
terms — "sited at the meeting of two rivers, with iron within a day's ride" — because a
number a novelist cannot interrogate is a number they cannot use (§67).

**It is the same map every time.** No RNG, no clocks, no set iteration: every random-
looking value is a pure hash of the world's seed and a coordinate, so the same world
generates byte-identical geometry on any machine, and a test can assert coordinates.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from fw.core.mapgen import (
    biome as biome_module,
)
from fw.core.mapgen import (
    climate,
    coast,
    erode,
    guards,
    noise,
    relief,
    territory,
    vegetation,
)
from fw.core.mapgen import (
    features as features_module,
)
from fw.core.mapgen.attributes import (
    DEFAULT_TERRAIN,
    ROUTING_TERRAIN,
    RegionProfile,
    profile_region,
)
from fw.core.mapgen.grid import Field, Grid
from fw.core.mapgen.layout import Site, arrange
from fw.core.world import World, WorldError

# The canvas. Fictional worlds have no coordinate system (§34), so these are the same
# arbitrary units the example world was drawn in, and the same rough magnitudes.
SPAN = 900.0
MARGIN = 60.0
GRID = 144                     # lattice cells per side
CELL = SPAN / GRID

# Where the water stands in the elevation field. Zero, and not by coincidence: the
# continent's own field is rebased at the shore, so "above sea level" and "positive" are
# the same statement and every stage can be handed one continuous surface instead of a
# height plus a threshold that has to travel beside it. It was 0.10 while land elevations
# began around there, and leaving it at 0.10 after the rebase made the climate treat
# every coastal plain as open ocean — the air arrived dry over the whole continent and
# the rain shadow vanished, which is the kind of failure that shows up as "the map looks
# a bit flat" rather than as an error.
SEA_LEVEL = 0.0
SHELF_CELLS = 14.0             # how far offshore the sea floor keeps falling
SHELF_DEPTH = 0.22             # how deep it gets, in the same units as the land
SHORE_CELLS = 4.0              # over how many cells inland the land takes over the shore
RIVER_SHARE = 0.022            # the share of land cells that carry a channel
OUTLINE_RAYS = 44              # vertices per generated region outline
MIN_SPACING_CELLS = 3.0        # settlements no closer than this, in lattice cells

# Marks every shape this module writes, so a regenerate can find its own work and leave
# the writer's alone. The client passes unknown style keys through untouched.
GENERATED = "generated_by"
GENERATOR = "mapgen/1"
GENERATED_TAG = "generated-map"

LAYER_REGIONS = "regions"
LAYER_WATER = "waterways"
LAYER_SETTLEMENTS = "settlements"
LAYER_ROADS = "roads"

# How much slower a league of each terrain is to cross, for road costing.
TRAVEL_COST = {"ocean": 9.0, "marsh": 3.4, "mountain": 4.2, "glacier": 5.0,
               "highland": 2.4, "hills": 1.8, "forest": 1.6, "desert": 2.0,
               "steppe": 1.2, "coast": 1.1, "plain": 1.0, "farmland": 1.0}


def _is_generated(geometry) -> bool:
    """Whether this shape came from a generator — either of them.

    Imported late: the ledger knows about plans, and plans are built on top of this
    module. Reaching for it here rather than duplicating the rule keeps one answer to
    "is this mine?".
    """
    from fw.core.mapgen.ledger import is_generated
    return is_generated(geometry)


@dataclass
class Placement:
    """One settlement put somewhere, and the case for it."""

    entity_id: str | None          # None for a proposal not yet in the world
    name: str
    x: float
    y: float
    region_id: str
    rank: str                      # capital | city | town | village
    score: float
    reasons: list[str] = field(default_factory=list)
    proposed: bool = False

    def because(self) -> str:
        if not self.reasons:
            return f"{self.name} sits here for want of anywhere better."
        return f"{self.name} sits here — " + "; ".join(self.reasons) + "."


@dataclass
class GenerationReport:
    """What a run did, in terms a writer can read and act on."""

    regions_drawn: list[str] = field(default_factory=list)
    regions_kept: list[str] = field(default_factory=list)
    rivers: list[str] = field(default_factory=list)
    placements: list[Placement] = field(default_factory=list)
    roads: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = []
        if self.regions_drawn:
            bits.append(f"drew {len(self.regions_drawn)} regions")
        if self.regions_kept:
            bits.append(f"kept {len(self.regions_kept)} you had already drawn")
        if self.rivers:
            bits.append(f"traced {len(self.rivers)} rivers")
        placed = [p for p in self.placements if not p.proposed]
        proposed = [p for p in self.placements if p.proposed]
        if placed:
            bits.append(f"placed {len(placed)} settlements")
        if proposed:
            bits.append(f"proposed {len(proposed)} new ones")
        if self.roads:
            bits.append(f"laid {self.roads} roads")
        return ("The map " + ", ".join(bits) + ".") if bits else "Nothing to draw yet."


class MapGenerator:
    """Builds a world map from region attributes. Deterministic for a given seed."""

    def __init__(self, world: World, *, seed: str | None = None,
                 at: int | None = None) -> None:
        self.world = world
        # The seed defaults to the world's own name, so the same world regenerates the
        # same map without the writer having to keep a number safe somewhere.
        self.seed = seed or f"{world.name}"
        self.at = at
        self.profiles: dict[str, RegionProfile] = {}
        self.owner: list[list[str | None]] = []      # cell -> region id
        self.elevation: list[list[float]] = []
        self.moisture: list[list[float]] = []
        self.sea: list[list[bool]] = []
        self.flow: list[list[float]] = []
        self.channel: set[tuple[int, int]] = set()
        self.river_threshold: float = 0.0
        self.from_sea: list[list[int]] = []
        self.inland_max: int = 1
        self.authored_cells: dict[str, set[tuple[int, int]]] = {}
        self.landform: coast.Landform | None = None
        self.relief: relief.Relief | None = None
        self.erosion: erode.Erosion | None = None
        self.vegetation: vegetation.Vegetation | None = None
        self.uplift: Field = []
        self.climate: climate.Climate | None = None
        self.biome: biome_module.BiomeField | None = None
        self.features: features_module.FeatureSet | None = None
        self.temperature: list[list[float]] = []
        self.partition: territory.Partition | None = None
        self._anchors: dict[str, tuple[int, int]] = {}
        self._weights: dict[str, float] = {}
        self.cost: list[list[float]] = []
        self._roads_entity: str | None = None
        self.report = GenerationReport()

    # ---- the whole run ----------------------------------------------------

    def generate(self, *, propose_settlements: bool = True) -> GenerationReport:
        """Build the map and write it, as one undoable action."""
        regions = self.regions_of_the_world()
        if not regions:
            self.report.notes.append(
                "There are no regions yet — a map is grown from regions, so name a "
                "few and say what they are like.")
            return self.report

        self.profiles = {r.id: profile_region(self.world, r.id, at=self.at)
                         for r in regions}
        authored = self._authored_outlines()
        self._build_landmass(authored)
        self._assign_cells(regions, authored)
        self._build_fields()
        rivers = self._trace_rivers()
        self._classify_ground()
        self._build_costs()
        placements = self._site_settlements(propose=propose_settlements)

        # Everything in one transaction: without it a generated map is hundreds of
        # separate undoable actions, and the writer's first Ctrl+Z gets one polygon back.
        with self.world.db.transaction():
            self._clear_previous()
            self._write_regions(authored)
            self._write_rivers(rivers)
            self._write_settlements(placements)
            self._write_roads(placements)
        return self.report

    # ---- regions ----------------------------------------------------------

    def regions_of_the_world(self) -> list:
        """The writer's regions — never the map's own.

        Anything the generator made carries the generated tag, and reading it back as
        source material is how a map ends up laid out around its own previous
        coastline. The rule is one line and it belongs in one place.
        """
        return [e for e in self.world.entities("region")
                if GENERATED_TAG not in e.tags
                and (self.at is None or e.exists_on(self.at))]

    def _authored_outlines(self) -> dict[str, list[list[float]]]:
        """Region shapes the writer drew themselves. These are truth; we build around
        them rather than over them (§66)."""
        out: dict[str, list[list[float]]] = {}
        index = self.world.geometry_index(at=self.at, layer=LAYER_REGIONS)
        for region_id in self.profiles:
            for geometry in index.get(region_id, []):
                if geometry.kind != "polygon":
                    continue
                # Our own earlier work is not the writer's drawing. Missing this is
                # what made regenerating build a different world every time: the first
                # run's region outlines came back as authored borders on the second,
                # so the land was reshaped around them and every feature moved.
                if _is_generated(geometry):
                    continue
                rings = geometry.coordinates
                if rings and rings[0]:
                    out[region_id] = [[float(p[0]), float(p[1])] for p in rings[0]]
                break
        return out

    def _build_landmass(self, authored: dict[str, list[list[float]]]) -> None:
        """Shape one continent, and find its coastline.

        The land is a single scalar field whose sea-level contour *is* the shore, grown
        around the skeleton the writer's borders describe. See `coast.py` for why it is
        built that way rather than as a polygon per region.
        """
        self.authored_cells = {
            region_id: {(i, j) for j in range(GRID) for i in range(GRID)
                        if _inside(ring, *self._centre(i, j))}
            for region_id, ring in authored.items()
        }
        grid = self._grid()
        anchors, weights, roughness = self._skeleton(authored)
        self.landform = coast.build(
            grid, anchors=anchors, weights=weights, roughness=roughness,
            borders=self._border_pairs(), seed=self.seed,
            must_hold=[sorted(cells)
                       for _, cells in sorted(self.authored_cells.items())
                       if cells])
        self.sea = self.landform.sea
        # Already settled onto dry ground: a heart the coastline drowned would claim
        # nothing, and the region would vanish from the map without a word.
        self._anchors = self.landform.anchors
        self._weights = weights
        self._measure_from_sea()

    def _grid(self) -> Grid:
        return Grid(size=GRID, span=SPAN - 2 * MARGIN,
                    origin_x=MARGIN, origin_y=MARGIN)

    def _skeleton(self, authored: dict[str, list[list[float]]]):
        """Where each region sits, from what the writer said borders what.

        Keyed on region *names*, never ids: ids are ULIDs, random per world, so two
        copies of the same world would lay out differently and neither the plan digest
        nor a golden file would mean anything.
        """
        grid = self._grid()
        by_name = {self.profiles[rid].name: rid for rid in sorted(self.profiles)}
        sites = []
        for name in sorted(by_name):
            region_id = by_name[name]
            profile = self.profiles[region_id]
            fixed = None
            cells = self.authored_cells.get(region_id)
            if cells:
                # The writer drew it. Their centre is the anchor; it does not move.
                mid_x = sum(c[0] for c in cells) / len(cells)
                mid_y = sum(c[1] for c in cells) / len(cells)
                fixed = grid.centre(int(mid_x), int(mid_y))
            sites.append(Site(key=name, coastal=profile.coastal, fixed=fixed,
                              weight=0.7 + 0.6 * min(
                                  1.0, self._population_of(region_id) / 150_000)))
        # A generous margin. Land is deliberately suppressed towards the canvas edge so
        # no coastline reads as a cut-off, so a region laid out in that band gets its
        # ground drowned — and with no borders stated to pull them together, regions
        # spread to the corners and the continent came apart there.
        placed = arrange(sites, self._border_pairs(), seed=self.seed,
                         span=SPAN - 2 * MARGIN, margin=(SPAN - 2 * MARGIN) * 0.16)
        anchors = {name: grid.cell_of(x + MARGIN, y + MARGIN)
                   for name, (x, y) in placed.items()}
        weights = {site.key: site.weight for site in sites}
        roughness = {name: self.profiles[by_name[name]].roughness for name in by_name}
        self._region_of_name = by_name
        return anchors, weights, roughness

    def _border_pairs(self) -> set[tuple[str, str]]:
        """The writer's `borders` facts, as pairs of region names."""
        name_of = {rid: self.profiles[rid].name for rid in self.profiles}
        pairs: set[tuple[str, str]] = set()
        for region_id in sorted(self.profiles):
            for other in self._borders_of(region_id):
                if other in name_of:
                    a, b = name_of[region_id], name_of[other]
                    pairs.add((min(a, b), max(a, b)))
        return pairs

    def _measure_from_sea(self) -> None:
        """Distance inland, in cells — what tells a coast from an interior."""
        self.from_sea = [[0 if self.sea[j][i] else -1 for i in range(GRID)]
                         for j in range(GRID)]
        frontier = [(i, j) for j in range(GRID) for i in range(GRID) if self.sea[j][i]]
        depth = 0
        while frontier:
            depth += 1
            nxt = []
            for i, j in frontier:
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ni, nj = i + di, j + dj
                    if 0 <= ni < GRID and 0 <= nj < GRID and self.from_sea[nj][ni] < 0:
                        self.from_sea[nj][ni] = depth
                        nxt.append((ni, nj))
            frontier = nxt
        self.inland_max = max(
            (self.from_sea[j][i] for j in range(GRID) for i in range(GRID)), default=1)

    def _assign_cells(self, regions, authored: dict[str, list[list[float]]]) -> None:
        """Give every acre of the continent to somebody.

        A partition rather than a race between growing blobs: unclaimed land renders as
        grout between the regions on a political fill, and a writer reads that as ground
        nobody has thought about rather than as an artefact of how it was drawn.
        """
        name_of = {rid: self.profiles[rid].name for rid in self.profiles}
        claimed = {name_of[rid]: {(i, j) for i, j in cells if not self.sea[j][i]}
                   for rid, cells in self.authored_cells.items()}
        for region_id, cells in list(self.authored_cells.items()):
            self.authored_cells[region_id] = {
                (i, j) for i, j in cells if not self.sea[j][i]}

        self.partition = territory.grow(
            self._grid(), self.sea, anchors=self._anchors, weights=self._weights,
            claimed=claimed)
        id_of = {name: rid for rid, name in name_of.items()}
        self.owner = [[None] * GRID for _ in range(GRID)]
        for j in range(GRID):
            for i in range(GRID):
                name = self.partition.region_at(i, j)
                if name is not None:
                    self.owner[j][i] = id_of.get(name)

    def _seed_points(self, regions, authored) -> dict[str, tuple[int, int]]:
        """Where each undrawn region starts growing.

        Regions that border one another start near one another, so the finished map
        agrees with what the writer said about who touches whom. Everything else is
        spread evenly around the canvas and nudged by a stable hash, which keeps the
        result organic without keeping it random.
        """
        placed: dict[str, tuple[float, float]] = {}
        for region_id, ring in authored.items():
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            placed[region_id] = (sum(xs) / len(xs), sum(ys) / len(ys))

        ordered = sorted(regions, key=lambda r: r.name)
        centre = SPAN / 2
        radius = SPAN * 0.3
        seeds: dict[str, tuple[int, int]] = {}
        for index, region in enumerate(ordered):
            neighbours = [placed[n] for n in self._borders_of(region.id)
                          if n in placed]
            if neighbours:
                # Sit beside what it borders, pushed outward from their centre of mass.
                nx = sum(p[0] for p in neighbours) / len(neighbours)
                ny = sum(p[1] for p in neighbours) / len(neighbours)
                dx, dy = noise.bearing(self.seed, index, 7)
                x = nx + dx * radius * 0.75
                y = ny + dy * radius * 0.75
            else:
                dx, dy = noise.around(index, max(1, len(ordered)))
                x = centre + dx * radius
                y = centre + dy * radius
            # Keyed on the region's NAME, never its id: ids are random per world, so
            # seeding from them would mean two identical worlds grew different maps —
            # and `hash()` is salted per process, so even one world would drift between
            # machines. A name is stable, meaningful, and the writer's own.
            x += noise.jitter(self.seed, f"seedx:{region.name}", SPAN * 0.05)
            y += noise.jitter(self.seed, f"seedy:{region.name}", SPAN * 0.05)
            placed[region.id] = (x, y)
            seeds[region.id] = self._land_near(
                self._cell_of(x, y),
                coastal=self.profiles[region.id].coastal, key=region.name)
        return seeds

    def _land_near(self, cell: tuple[int, int], *, coastal: bool,
                   key: str) -> tuple[int, int]:
        """The nearest land cell of the right character to start a region from.

        A region the writer called coastal wants a seed near the water; one they called
        a vale wants to start well inland. Getting this right is what stops an inland
        region growing itself a harbour.
        """
        want = 2 if coastal else max(3, int(self.inland_max * 0.55))
        best = None
        best_cost = math.inf
        for j in range(GRID):
            for i in range(GRID):
                if self.sea[j][i] or self.owner[j][i] is not None:
                    continue
                inland = self.from_sea[j][i]
                cost = (abs(inland - want) * 2.0
                        + math.dist((i, j), cell) * 0.35
                        + noise.unit(f"{self.seed}~seat", i, j) * 0.5)
                if cost < best_cost:
                    best_cost, best = cost, (i, j)
        return best or cell

    def _borders_of(self, region_id: str) -> list[str]:
        out = []
        for fact in guards.sorted_facts(
                self.world.facts_where("borders", subject_id=region_id, at=self.at)):
            if fact.object_id:
                out.append(fact.object_id)
        for fact in guards.sorted_facts(
                self.world.facts_where("borders", object_id=region_id, at=self.at)):
            out.append(fact.subject_id)
        return sorted(set(out))

    # ---- the land ---------------------------------------------------------

    def _build_fields(self) -> None:
        """Elevation, then erosion, then weather — in that order, because it is causal.

        The height comes from `relief`, which lays mountain ranges as oriented ridges
        rather than raising a region wholesale. Then water is run over it for as long as
        it takes to wear a drainage network into it, which is what turns a raised surface
        into terrain: valleys that branch, foothills that fall away, and channels that
        already know where they are before anyone asks for a river.

        Only then does the weather run. A rain shadow is cast by a range's finished
        profile, not its first draft — put the climate first and the lee of a mountain is
        computed against a mountain that erosion has yet to shape. The same argument
        orders everything after this: soils follow the water, vegetation follows the
        soils, and nothing is allowed to be a cause of what came before it.
        """
        grid = self._grid()
        keys = self.partition.keys
        index_of = {key: n for n, key in enumerate(keys)}
        owner = [[index_of.get(self.profiles[rid].name, -1) if rid else -1
                  for rid in row] for row in self.owner]
        by_name = {self.profiles[rid].name: self.profiles[rid]
                   for rid in sorted(self.profiles)}

        self.relief = relief.plan_relief(
            grid, sea=self.sea, from_sea=self.from_sea, owner=owner, keys=keys,
            terrain_mix={k: p.terrain_mix for k, p in by_name.items()},
            base_height={k: p.base_elevation for k, p in by_name.items()},
            roughness={k: p.roughness for k, p in by_name.items()},
            seed=self.seed, sea_level=SEA_LEVEL)

        self.uplift = self._with_bathymetry(self.relief.elevation)

        self.erosion = erode.erode(
            grid, elevation=self.uplift, sea=self.sea, seed=self.seed)
        self.elevation = self.erosion.elevation

        self.climate = climate.plan_climate(
            grid, elevation=self.elevation, sea=self.sea, from_sea=self.from_sea,
            owner=owner, keys=keys,
            temperature_of={k: p.temperature for k, p in by_name.items()},
            moisture_of={k: p.moisture for k, p in by_name.items()},
            stated={k: "moisture" in p.traces and "no rainfall" not in p.why("moisture")
                    for k, p in by_name.items()},
            seed=self.seed, sea_level=SEA_LEVEL)
        self.moisture = self.climate.moisture
        self.temperature = self.climate.temperature

        for j in range(GRID):
            for i in range(GRID):
                if self.sea[j][i]:
                    self.moisture[j][i] = 1.0

        # Cover comes last of the physical stages, because it is a consequence of every
        # one of them: the rain that reaches a place, the warmth it has, the slope it
        # stands on, and how well the ground under it drains. A wood is not a thing the
        # map decides to put somewhere.
        self.vegetation = vegetation.plan_vegetation(
            grid, elevation=self.elevation, slope=self.erosion.slope,
            flow=self.erosion.flow, downstream=self.erosion.downstream,
            moisture=self.moisture,
            temperature=self.temperature, sea=self.sea, owner=owner, keys=keys,
            wooded={k: p.terrain_mix.get("forest", 0.0) for k, p in by_name.items()
                    if "forest" in p.terrain_mix},
            seed=self.seed, sea_level=SEA_LEVEL)

    def _with_bathymetry(self, land: Field) -> Field:
        """One continuous surface, sea floor included — not a height plus a mask.

        The renderer draws the shore as a contour of this field, so where the field stops
        being continuous the shore stops being a coastline and becomes a staircase of
        lattice cells. That was exactly what the first drafts looked like.

        The sea floor is not invented here: it is the continent's own field, which
        already runs smoothly down through the waterline because the coast was contoured
        out of it rather than masked. All that is added is depth — the field falls away
        slowly near the shore and faster further out, so there is a shelf. That costs one
        distance sweep and buys a shore that can be enlarged without falling apart, water
        that reads as shallow where a river arrives, and — because erosion sees a real
        outlet rather than a wall — river mouths that reach the coast instead of stopping
        a cell short of it.
        """
        assert self.landform is not None
        grid = self._grid()
        offshore = grid.distance_from(
            [(i, j) for j in range(GRID) for i in range(GRID) if not self.sea[j][i]])
        shore = self.landform.height
        out = [row[:] for row in land]
        for j in range(GRID):
            for i in range(GRID):
                if self.sea[j][i]:
                    reach = min(1.0, offshore[j][i] / SHELF_CELLS)
                    out[j][i] = shore[j][i] - SHELF_DEPTH * reach * reach
                    continue
                # And on the land side, the same field, faded out over the first few
                # cells inland.
                #
                # Without this the two halves of the surface do not join. The relief
                # field starts a coastal plain some way above the water — that is what a
                # coastal plain is — so the first land cell stood a tenth of the world's
                # whole relief above the sea cell beside it, and the shore was a cliff
                # exactly one cell wide. A contour drawn through a one-cell cliff can
                # only be positioned to the nearest cell, which is why the coastline came
                # out as a staircase however finely it was rendered: the resolution was
                # never the problem, the discontinuity was.
                inland = min(1.0, self.from_sea[j][i] / SHORE_CELLS)
                eased = inland * inland * (3.0 - 2.0 * inland)
                out[j][i] = shore[j][i] + (out[j][i] - shore[j][i]) * eased
        return out

    def _edge_falloff(self, i: int, j: int) -> float:
        """1 in the interior, shelving to 0 at the rim, so the continent has a coast."""
        shore = GRID * 0.09
        nearest = min(i, j, GRID - 1 - i, GRID - 1 - j)
        if nearest >= shore:
            return 1.0
        # t ** 0.75, by two correctly-rounded square roots rather than a libm pow.
        t = max(0.0, nearest / shore)
        return math.sqrt(math.sqrt(t * t * t))

    def _downhill(self, i: int, j: int) -> tuple[int, int] | None:
        """The neighbour the water goes to, as erosion decided.

        Asked of the erosion's own receiver array rather than recomputed from the
        heights, so a river follows the valley that was cut for it. Recomputing would
        usually agree and occasionally not, and the occasions are exactly the flat ground
        where a disagreement puts a river across a floodplain instead of down it.
        """
        if self.erosion is not None:
            target = self.erosion.downstream[j][i]
            if target < 0:
                return None
            return (target % GRID, target // GRID)
        return None

    def _trace_rivers(self) -> list[list[tuple[int, int]]]:
        """Where water collects and runs to the sea.

        The drainage network is not computed here any more — erosion already worked it
        out, and had to, because a channel's discharge is what told it how deeply to cut.
        Recomputing it afterwards would be asking the same question twice and risking two
        answers: a river drawn along a valley the erosion did not carve is the kind of
        detail a reader notices without being able to say why the map feels wrong.

        What is left is choosing which of those channels are rivers. A quantile of the
        flow on land, not a share of total rainfall: a share does not survive a change of
        lattice, because the total scales with the number of cells while a single river's
        catchment does not. That quietly dried the whole world up when the resolution was
        raised — four channel cells and one river on a continent.
        """
        assert self.erosion is not None
        self.flow = self.erosion.flow
        land = [(i, j) for j in range(GRID) for i in range(GRID)
                if not self.sea[j][i]]

        ranked = sorted(self.flow[j][i] for i, j in land)
        threshold = (ranked[max(0, int(len(ranked) * (1.0 - RIVER_SHARE)) - 1)]
                     if ranked else 1.0)

        channel = {(i, j) for i, j in land if self.flow[j][i] >= threshold}
        self.channel = channel
        self.river_threshold = threshold

        # A source is a channel cell with no channel cell draining into it.
        feeds_into: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for i, j in sorted(channel):
            step = self._downhill(i, j)
            if step is not None:
                feeds_into.setdefault(step, []).append((i, j))
        sources = [cell for cell in sorted(channel) if not feeds_into.get(cell)]

        rivers: list[list[tuple[int, int]]] = []
        for source in sources:
            path = [source]
            seen = {source}
            cursor = source
            for _ in range(GRID * 3):
                step = self._downhill(*cursor)
                if step is None or step in seen:
                    break
                path.append(step)
                seen.add(step)
                cursor = step
                if self.sea[step[1]][step[0]]:
                    break                       # reached the sea
            if len(path) >= 4:
                rivers.append(path)
        # Longest first, and only a handful: a map crowded with creeks reads as noise.
        rivers.sort(key=lambda p: (-len(p), p[0]))
        return rivers[:6]

    # ---- settlements ------------------------------------------------------

    def _site_settlements(self, *, propose: bool) -> list[Placement]:
        """Place the writer's settlements well, then suggest the ones a land implies."""
        placements: list[Placement] = []
        taken: list[tuple[float, float]] = []

        index = self.world.geometry_index(at=self.at, layer=LAYER_SETTLEMENTS)
        for region_id in sorted(self.profiles):
            profile = self.profiles[region_id]
            candidates = self._score_region(region_id)
            if not candidates:
                continue

            existing = [self.world.get_entity(sid) for sid in profile.settlements]
            existing = [e for e in existing if e is not None]
            # Biggest first: the capital and the cities get the best ground, which is
            # how they came to be the capital and the cities.
            existing.sort(key=lambda e: (-self._population_of(e.id),
                                         not self._is_capital(e.id), e.name))
            for entity in existing:
                authored = [g for g in index.get(entity.id, [])
                            if not (g.style or {}).get(GENERATED)]
                if authored:
                    place = authored[0].coordinates
                    taken.append((float(place[0]), float(place[1])))
                    continue          # the writer put it there; leave it alone (§66)
                spot = self._take_best(candidates, taken)
                if spot is None:
                    break
                placements.append(self._placement(
                    entity.id, entity.name, spot, region_id, proposed=False))
                taken.append((placements[-1].x, placements[-1].y))

            if propose:
                wanted = self._settlements_wanted(profile) - len(existing)
                for n in range(max(0, wanted)):
                    spot = self._take_best(candidates, taken)
                    if spot is None:
                        break
                    name = self._propose_name(profile, n)
                    placements.append(self._placement(
                        None, name, spot, region_id, proposed=True))
                    taken.append((placements[-1].x, placements[-1].y))
        return placements

    def _settlements_wanted(self, profile: RegionProfile) -> int:
        """How many settlements a region's people and land imply.

        Roughly one market town per twenty thousand people, tempered by how hard the
        ground is to live on — mountains and marsh hold fewer, farmland more.
        """
        if not profile.population:
            return 1
        base = max(1, round(profile.population / 20000))
        hardship = {"mountain": 0.5, "glacier": 0.3, "desert": 0.4, "marsh": 0.6,
                    "highland": 0.7, "hills": 0.85}.get(profile.dominant, 1.0)
        return max(1, min(8, round(base * hardship)))

    def _score_region(self, region_id: str) -> list[tuple[float, int, int, list[str]]]:
        """Every cell of a region, scored for how good a place it is to live."""
        profile = self.profiles[region_id]
        inside_only = self.authored_cells.get(region_id)
        resource_note = (f"{profile.resources[0].lower()} close at hand"
                         if profile.resources else None)
        out = []
        for j in range(GRID):
            for i in range(GRID):
                if self.owner[j][i] != region_id or self.sea[j][i]:
                    continue
                # A region grows a hinterland so the land is continuous, but a town
                # belonging to it must stand inside the border the writer actually
                # drew — otherwise the map labels a place for a region it is not in.
                if inside_only is not None and (i, j) not in inside_only:
                    continue
                score = 0.0
                why: list[str] = []

                if (i, j) in self.channel:
                    feeders = sum(
                        1 for dj in (-1, 0, 1) for di in (-1, 0, 1)
                        if (di or dj) and (i + di, j + dj) in self.channel
                        and 0 <= i + di < GRID and 0 <= j + dj < GRID
                        and self._downhill(i + di, j + dj) == (i, j))
                    score += 3.0 if feeders >= 2 else 2.2
                    why.append("where two rivers meet" if feeders >= 2
                               else "on a river")
                elif self.flow[j][i] >= self.river_threshold * 0.3:
                    score += 1.0
                    why.append("beside fresh water")

                # Only where the writer's own description reaches the sea. Generated
                # geography must not overrule authored intent (§66): a region called a
                # river plain does not get a port because the lattice put water nearby.
                if profile.coastal and self._touches_sea(i, j):
                    score += 2.6
                    why.append("with a harbour")

                arable = self.moisture[j][i] * (1.0 - self.elevation[j][i])
                score += arable * 2.2
                if arable > 0.45:
                    why.append("good ground for grain")

                relief = self._relief(i, j)
                if relief > 0.16:
                    score += 1.3
                    why.append("high enough to defend")
                elif self.elevation[j][i] > 0.75:
                    score -= 1.4          # liveable, but nobody builds a city on a peak

                if resource_note and profile.resources:
                    score += 0.8
                    why.append(resource_note)

                # A stable nudge so equally good ground does not tie forever, and so
                # towns do not land on a lattice.
                score += noise.unit(self.seed, i, j) * 0.35
                out.append((score, i, j, why))
        out.sort(key=lambda c: (-c[0], c[1], c[2]))
        return out

    def _take_best(self, candidates, taken) -> tuple[float, int, int, list[str]] | None:
        """The best remaining cell that is not crowding somewhere already chosen."""
        for entry in candidates:
            _, i, j, _ = entry
            x, y = self._centre(i, j)
            if all(math.dist((x, y), spot) >= MIN_SPACING_CELLS * CELL
                   for spot in taken):
                candidates.remove(entry)
                return entry
        return None

    def _placement(self, entity_id, name, spot, region_id, *, proposed) -> Placement:
        score, i, j, why = spot
        x, y = self._centre(i, j)
        # Nudge off the exact cell centre, stably, so a map does not look like a grid.
        x += noise.jitter(self.seed, f"px:{name}:{i}:{j}", CELL * 0.35)
        y += noise.jitter(self.seed, f"py:{name}:{i}:{j}", CELL * 0.35)
        return Placement(
            entity_id=entity_id, name=name,
            x=round(max(MARGIN, min(SPAN - MARGIN, x)), 1),
            y=round(max(MARGIN, min(SPAN - MARGIN, y)), 1),
            region_id=region_id, rank=self._rank_for(score), score=round(score, 3),
            reasons=why, proposed=proposed,
        )

    @staticmethod
    def _rank_for(score: float) -> str:
        if score >= 6.0:
            return "city"
        if score >= 4.0:
            return "town"
        return "village"

    def _propose_name(self, profile: RegionProfile, n: int) -> str:
        """A placeholder name that says what it is and where — never a fake invention.

        Naming is the writer's, not ours: a generated name would be one more thing to
        find and correct later.
        """
        return f"Unnamed settlement {n + 1} ({profile.name})"

    # ---- roads ------------------------------------------------------------

    def _write_roads(self, placements: list[Placement]) -> None:
        """Connect the settlements, cheapest links first — a minimum spanning tree.

        Roads follow need and terrain, so the network that appears is the one the land
        would actually have grown: a spine along the easy ground with spurs into the
        hills, rather than everything joined to everything.
        """
        known = [p for p in placements if p.entity_id] + self._already_placed(placements)
        if len(known) < 2:
            return
        # Which pairs to join is decided on a cheap estimate — distance weighted by the
        # ground either end sits on. Only the roads actually chosen are then routed
        # properly, so the expensive part runs n-1 times rather than n²/2.
        laid = 0
        for a, b in self._road_edges(known):
            p, q = known[a], known[b]
            path = self._route(self._cell_of(p.x, p.y), self._cell_of(q.x, q.y))
            points = [[p.x, p.y]] + [[round(x, 1), round(y, 1)]
                                     for x, y in (self._centre(i, j)
                                                  for i, j in path[1:-1])]
            points.append([q.x, q.y])
            self.world.add_geometry(
                self._road_entity(), "line", points, layer=LAYER_ROADS,
                style={"stroke": "#8a7550", GENERATED: GENERATOR})
            length = sum(math.dist(points[n], points[n + 1])
                         for n in range(len(points) - 1))
            self.world.add_route_segment(
                p.entity_id, q.entity_id, round(length, 1),
                medium="road", quality=0.8, entity_id=self._road_entity(),
                terrain=self._road_terrain(path))
            laid += 1
        self.report.roads = laid

    def _road_edges(self, known: list[Placement]) -> list[tuple[int, int]]:
        """Which pairs of places to join: a minimum spanning tree on a cheap estimate.

        Distance weighted by the ground either end sits on. Only the roads actually
        chosen are then routed properly, so the expensive part runs n-1 times rather
        than n squared over two — which is the difference between 160ms and a second.
        """
        edges = []
        for a in range(len(known)):
            for b in range(a + 1, len(known)):
                p, q = known[a], known[b]
                terrain = max(TRAVEL_COST.get(self.profiles[p.region_id].dominant, 1.4),
                              TRAVEL_COST.get(self.profiles[q.region_id].dominant, 1.4))
                edges.append((math.dist((p.x, p.y), (q.x, q.y)) * terrain, a, b))
        edges.sort()

        parent = list(range(len(known)))

        def find(n: int) -> int:
            while parent[n] != n:
                parent[n] = parent[parent[n]]
                n = parent[n]
            return n

        chosen: list[tuple[int, int]] = []
        for _cost, a, b in edges:
            ra, rb = find(a), find(b)
            if ra == rb:
                continue
            parent[ra] = rb
            chosen.append((a, b))
        return chosen

    def land_cells(self) -> int:
        """How many lattice cells are dry land — the denominator for a region's share."""
        return sum(1 for row in self.sea for wet in row if not wet)

    def _road_terrain(self, path: list[tuple[int, int]]) -> str:
        """What ground this road crosses, said in the travel engine's words.

        A road drawn on the map is only half a road: the writer's real question is "how
        long does it take", and that is answered by the router, which costs a whole
        segment at one terrain. So the honest single answer is the ground the road spends
        most of its length on — not the terrain of the region it starts in, which
        described a mountain road as running over ice and made it untravellable.

        Water is skipped rather than counted. A road never crosses the sea (those cells
        cost infinity), but a region the writer named for its coast or its gulf can still
        be mostly ocean, and a road tagged `water` is one no traveller on land can use.
        """
        tally: dict[str, int] = {}
        for i, j in path:
            region_id = self.owner[j][i]
            kind = self.profiles[region_id].dominant if region_id else DEFAULT_TERRAIN
            going = ROUTING_TERRAIN.get(kind, "plain")
            if going == "water":
                continue
            tally[going] = tally.get(going, 0) + 1
        if not tally:
            return "plain"
        # Most cells wins; ties break alphabetically, so the same world always names the
        # same road the same way.
        return min(tally, key=lambda t: (-tally[t], t))

    def _already_placed(self, placements: list[Placement]) -> list[Placement]:
        """Settlements the writer positioned themselves, so roads reach them too."""
        moved = {p.entity_id for p in placements}
        out = []
        index = self.world.geometry_index(at=self.at, layer=LAYER_SETTLEMENTS)
        for region_id, profile in sorted(self.profiles.items()):
            for sid in profile.settlements:
                if sid in moved:
                    continue
                for geometry in index.get(sid, []):
                    if geometry.kind != "point":
                        continue
                    entity = self.world.get_entity(sid)
                    if entity is None:
                        continue
                    out.append(Placement(
                        entity_id=sid, name=entity.name,
                        x=float(geometry.coordinates[0]),
                        y=float(geometry.coordinates[1]),
                        region_id=region_id, rank="town", score=0.0))
                    break
        return out

    def _classify_ground(self) -> None:
        """What grows where, and which stretches of it are places in their own right.

        After the rivers, because a watercourse breaks a wood in two, and a forest that
        spans both banks reads as a mistake even when it is technically true.
        """
        grid = self._grid()
        keys = self.partition.keys
        index_of = {key: n for n, key in enumerate(keys)}
        owner = [[index_of.get(self.profiles[rid].name, -1) if rid else -1
                  for rid in row] for row in self.owner]
        by_name = {self.profiles[rid].name: self.profiles[rid]
                   for rid in sorted(self.profiles)}

        # Only regions whose terrain the writer actually described get a claim. The
        # profile defaults an undescribed region to open country, and honouring that
        # default as though it were their word made every unwritten region a plain
        # regardless of its weather — the model's whole job in the gaps.
        claims = {k: p.terrain_mix for k, p in by_name.items()
                  if "nothing is recorded" not in p.why("terrain")}
        self.biome = biome_module.classify(
            grid, elevation=self.elevation, temperature=self.temperature,
            moisture=self.moisture, sea=self.sea, owner=owner, keys=keys,
            claims=claims, seed=self.seed, sea_level=SEA_LEVEL)
        self.features = features_module.plan_features(
            grid, biome=self.biome, sea=self.sea, channel=self.channel,
            owner=owner, keys=keys)
        notes = features_module.adopt(list(self.features.features),
                                      self._authored_features())
        self.features.notes.extend(notes)

    def _authored_features(self) -> dict[str, tuple[str, str, tuple[int, int] | None]]:
        """Stretches of country the writer has already named, so they are adopted."""
        out: dict[str, tuple[str, str, tuple[int, int] | None]] = {}
        index = self.world.geometry_index(at=self.at)
        for entity in self.world.entities("terrain_feature"):
            if GENERATED_TAG in entity.tags:
                continue
            kind = self._feature_kind_of(entity)
            if kind is None:
                continue
            at = None
            for geometry in index.get(entity.id, []):
                if geometry.kind == "point":
                    at = self._cell_of(float(geometry.coordinates[0]),
                                       float(geometry.coordinates[1]))
                    break
            out[entity.name] = (entity.id, kind, at)
        return out

    def _feature_kind_of(self, entity) -> str | None:
        """Which kind of country a named feature is, from its own words."""
        from fw.core.mapgen.attributes import read_terrain
        stated = guards.sorted_facts(
            self.world.facts_where("feature_kind", subject_id=entity.id, at=self.at))
        if stated and stated[-1].value in features_module.NAME_HINT:
            return stated[-1].value
        text = f"{entity.name} {entity.summary or ''}"
        mix = read_terrain(text)
        for biome_kind, feature_kind in sorted(features_module.NAMED_KINDS.items()):
            if biome_kind in mix:
                return feature_kind
        return None

    def _build_costs(self) -> None:
        """The price of crossing every cell, worked out once.

        Terrain plus steepness. Computing it inside the search instead would re-derive
        each cell's relief every time a road considered stepping onto it.
        """
        self.cost = [[1.0] * GRID for _ in range(GRID)]
        for j in range(GRID):
            for i in range(GRID):
                if self.sea[j][i]:
                    self.cost[j][i] = math.inf
                    continue
                region_id = self.owner[j][i]
                terrain = self.profiles[region_id].dominant if region_id else "plain"
                self.cost[j][i] = (TRAVEL_COST.get(terrain, 1.4)
                                   * (1.0 + self._relief(i, j) * 6.0))

    def _route(self, origin: tuple[int, int],
               target: tuple[int, int]) -> list[tuple[int, int]]:
        """The cheapest way across the ground between two cells.

        Dijkstra, stopping the moment the destination is settled — a road bends around
        a mountain and follows a valley, which is both what roads do and what the
        travel-time engine already assumes about them.
        """
        best = {origin: 0.0}
        came: dict[tuple[int, int], tuple[int, int]] = {}
        heap = [(0.0, origin)]
        while heap:
            cost, cell = heapq.heappop(heap)
            if cell == target:
                break
            if cost > best.get(cell, math.inf):
                continue
            i, j = cell
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    if not (di or dj):
                        continue
                    ni, nj = i + di, j + dj
                    if not (0 <= ni < GRID and 0 <= nj < GRID):
                        continue
                    step = self.cost[nj][ni] * (1.4142 if di and dj else 1.0)
                    if step == math.inf:
                        continue
                    fresh = cost + step
                    if fresh < best.get((ni, nj), math.inf):
                        best[(ni, nj)] = fresh
                        came[(ni, nj)] = cell
                        heapq.heappush(heap, (fresh, (ni, nj)))
        path = [target]
        cursor = target
        while cursor != origin and cursor in came:
            cursor = came[cursor]
            path.append(cursor)
        path.reverse()
        return path if len(path) > 1 else [origin, target]

    # ---- writing ----------------------------------------------------------

    def _clear_previous(self) -> None:
        """Sweep away the last run's work, and only that.

        Everything this module writes is marked, so a rerun removes its own output and
        never a line the writer drew. The rivers and the road network are *entities*,
        so they are deleted outright — the FK cascade takes their geometry and route
        segments with them, and without that a second run left orphan rivers and a
        duplicate road for every road it laid.

        Only this timeline's rows are touched. A branch inherits canon's generated map
        but cannot delete canon's rows, so its own map is layered over the top and the
        writer is told rather than crashed at.
        """
        for entity in self.world.entities("waterway") + self.world.entities("road"):
            if GENERATED_TAG in entity.tags:
                try:
                    self.world.delete_entity(entity.id)
                except WorldError:
                    self._note_inherited()

        for geometries in self.world.geometry_index(at=self.at).values():
            for geometry in geometries:
                if (geometry.style or {}).get(GENERATED) != GENERATOR:
                    continue
                try:
                    self.world.delete_geometry(geometry.id)
                except WorldError:
                    self._note_inherited()

    def _note_inherited(self) -> None:
        note = ("Some of this map was generated on the main timeline, which a what-if "
                "cannot redraw. Regenerate there to replace it.")
        if note not in self.report.notes:
            self.report.notes.append(note)

    def _write_regions(self, authored: dict[str, list[list[float]]]) -> None:
        for region_id in sorted(self.profiles):
            profile = self.profiles[region_id]
            if region_id in authored:
                self.report.regions_kept.append(profile.name)
                continue
            ring = self._outline(region_id)
            if ring is None:
                continue
            self.world.add_geometry(
                region_id, "polygon", [ring], layer=LAYER_REGIONS,
                approximate=True,          # §92: a generated border is not a surveyed one
                style={"fill": _terrain_colour(profile.dominant),
                       GENERATED: GENERATOR})
            self.report.regions_drawn.append(profile.name)

    def _outline(self, region_id: str) -> list[list[float]] | None:
        """A region's shape, traced from the ground it holds.

        Contoured rather than cast as rays, so the shape is as concave as the territory
        is and the edge it shares with a neighbour is the same edge on both maps.
        """
        name = self.profiles[region_id].name
        rings = territory.outline(self.partition, name)
        return rings[0] if rings else None

    def _road_entity(self) -> str:
        """One entity owning every generated road.

        Hanging road lines off the settlements they start from made a city's own
        geometry ambiguous — `geometry_for` would hand back a road polyline instead of
        the city's point. One road network, like the example world's Iron Road, keeps
        each entity's shape its own and gives a rerun a single thing to remove.
        """
        if self._roads_entity is None:
            self._roads_entity = self.world.add_entity(
                "road", "Generated roads", confidence="speculative",
                tags=[GENERATED_TAG],
                summary="Laid by the map generator between the settlements it knows.",
            ).id
        return self._roads_entity

    def _write_rivers(self, rivers: list[list[tuple[int, int]]]) -> None:
        for n, path in enumerate(rivers):
            points = [[round(x, 1), round(y, 1)]
                      for x, y in (self._centre(i, j) for i, j in path)]
            name = f"Unnamed river {n + 1}"
            river = self.world.add_entity(
                "waterway", name, confidence="speculative", tags=[GENERATED_TAG],
                summary="Traced by the map generator; rename it and it is yours.")
            self.world.add_geometry(
                river.id, "line", points, layer=LAYER_WATER,
                style={"stroke": "#4a7fa5", GENERATED: GENERATOR})
            self.report.rivers.append(name)

    def _write_settlements(self, placements: list[Placement]) -> None:
        for placement in placements:
            entity_id = placement.entity_id
            if entity_id is None:
                # A proposal is a real entity marked 'speculative', so it can be looked
                # at, renamed and kept — or deleted in one click if it is not wanted.
                proposal = self.world.add_entity(
                    "settlement", placement.name, confidence="speculative",
                    summary=placement.because(), tags=["proposed"])
                entity_id = proposal.id
                placement.entity_id = entity_id
                self.world.assert_fact(entity_id, "located_in", placement.region_id)
                self.world.assert_fact(entity_id, "settlement_type",
                                       value=placement.rank, confidence="speculative")
            self.world.add_geometry(
                entity_id, "point", [placement.x, placement.y],
                layer=LAYER_SETTLEMENTS,
                style={GENERATED: GENERATOR, "rank": placement.rank})
            self.report.placements.append(placement)

    # ---- lattice helpers --------------------------------------------------

    def _centre(self, i: float, j: float) -> tuple[float, float]:
        return (MARGIN + (i + 0.5) * (SPAN - 2 * MARGIN) / GRID,
                MARGIN + (j + 0.5) * (SPAN - 2 * MARGIN) / GRID)

    def _cell_of(self, x: float, y: float) -> tuple[int, int]:
        i = int((x - MARGIN) / max(1e-6, (SPAN - 2 * MARGIN)) * GRID)
        j = int((y - MARGIN) / max(1e-6, (SPAN - 2 * MARGIN)) * GRID)
        return (max(0, min(GRID - 1, i)), max(0, min(GRID - 1, j)))

    def _touches_sea(self, i: int, j: int) -> bool:
        """Is there open water next door? A harbour needs one, an inland town has none."""
        for dj in (-1, 0, 1):
            for di in (-1, 0, 1):
                ni, nj = i + di, j + dj
                if 0 <= ni < GRID and 0 <= nj < GRID and self.sea[nj][ni]:
                    return True
        return False

    def _relief(self, i: int, j: int) -> float:
        """How much the ground rises around a cell — what makes a site defensible."""
        here = self.elevation[j][i]
        drops = []
        for dj in (-1, 0, 1):
            for di in (-1, 0, 1):
                ni, nj = i + di, j + dj
                if (di or dj) and 0 <= ni < GRID and 0 <= nj < GRID:
                    drops.append(here - self.elevation[nj][ni])
        return max(drops) if drops else 0.0

    def _population_of(self, entity_id: str) -> int:
        facts = guards.sorted_facts(
            self.world.facts_where("population", subject_id=entity_id, at=self.at))
        if not facts or not facts[-1].value:
            return 0
        digits = "".join(ch for ch in facts[-1].value if ch.isdigit())
        return int(digits) if digits else 0

    def _is_capital(self, entity_id: str) -> bool:
        return bool(guards.sorted_facts(
            self.world.facts_where("capital_of", subject_id=entity_id, at=self.at)))


def _terrain_colour(kind: str) -> str:
    return {"mountain": "#6d6a63", "glacier": "#b9c6cc", "hills": "#7d7a55",
            "highland": "#75725c", "forest": "#4f6b4a", "plain": "#6f7c4e",
            "farmland": "#7d8a52", "steppe": "#8a8558", "desert": "#b3a075",
            "marsh": "#5d6b62", "coast": "#7c7358", "ocean": "#41627a"}.get(
        kind, "#6f7c4e")


def _inside(ring: list[list[float]], x: float, y: float) -> bool:
    """Ray-casting point-in-polygon, so authored outlines can be rasterised."""
    inside = False
    n = len(ring)
    for a in range(n):
        x1, y1 = ring[a]
        x2, y2 = ring[(a + 1) % n]
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) / (y2 - y1) * (x2 - x1)
            if crossing > x:
                inside = not inside
    return inside


def generate_map(world: World, *, seed: str | None = None, at: int | None = None,
                 propose_settlements: bool = True) -> GenerationReport:
    """Grow a map for this world. Returns what it did and why."""
    if world.entities("region") == []:
        raise WorldError(
            "a map grows from regions — name a region or two, say what they are like, "
            "and generate again")
    return MapGenerator(world, seed=seed, at=at).generate(
        propose_settlements=propose_settlements)
