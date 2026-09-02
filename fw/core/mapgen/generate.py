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
    cartography,
    climate,
    coast,
    erode,
    guards,
    hold,
    hydrology,
    movement,
    noise,
    ranks,
    relief,
    resources,
    roads,
    settle,
    shade,
    shapes,
    shore,
    source,
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
    profiles_from,
)
from fw.core.mapgen.grid import Field, Grid
from fw.core.mapgen.layout import Site, arrange
from fw.core.mapgen.source.reading import key_for
from fw.core.model.records import GENERATED_TAG as _GENERATED_TAG
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
# How deep the shelf gets, in the same units as the land. The renderer's sea ramp is
# defined over the same number — one constant, one home — because for a phase they
# disagreed (0.22 modelled, 0.10 rendered) and the outer half of every shelf drew as
# one flat colour with a uniform bright band hugging the coast.
SHELF_DEPTH = shade.SHELF_DEPTH
SHORE_CELLS = 4.0              # over how many cells inland the land takes over the shore
RIVER_SHARE = 0.022            # the share of land cells that carry a channel
OUTLINE_RAYS = 44              # vertices per generated region outline
ROAD_TOLERANCE = 0.6           # how far a drawn road may cut a corner, in leagues
WATER_TOLERANCE = 0.4          # and a river, which wanders more and is watched closer
SHORE_FLOOR = 0.002            # how far above the waterline the driest land must sit
MIN_SPACING_CELLS = 3.0        # settlements no closer than this, in lattice cells
                               # (kept by settle.TIERS, which spaces every rank wider)

# Marks every shape this module writes, so a regenerate can find its own work and leave
# the writer's alone. The client passes unknown style keys through untouched.
GENERATED = "generated_by"
GENERATOR = "mapgen/1"
# Defined on the entity itself: the lists and the continuity checks
# have to recognise the map's own suggestions too.
GENERATED_TAG = _GENERATED_TAG

LAYER_REGIONS = "regions"
LAYER_WATER = "waterways"
LAYER_SETTLEMENTS = "settlements"
LAYER_ROADS = "roads"
LAYER_CASTLES = "castles"

# How many sites the map offers beyond the settlements the writer has already named.
PROPOSAL_HEADROOM = 8

# The writer's own words for what a region has, mapped onto the fields the map keeps.
# Deliberately narrow: a word not in here claims nothing, which is better than guessing
# that "amber" means ore and putting a mine in a forest.
RESOURCE_WORDS = {
    "grain": "arable", "wheat": "arable", "corn": "arable", "barley": "arable",
    "farmland": "arable", "farms": "arable", "crops": "arable", "orchards": "arable",
    "cattle": "pasture", "sheep": "pasture", "wool": "pasture", "horses": "pasture",
    "herds": "pasture", "pasture": "pasture", "grazing": "pasture",
    "timber": "timber", "wood": "timber", "lumber": "timber", "forest": "timber",
    "stone": "stone", "quarry": "stone", "quarries": "stone", "marble": "stone",
    "slate": "stone", "granite": "stone",
    "iron": "ore", "ore": "ore", "silver": "ore", "gold": "ore", "copper": "ore",
    "tin": "ore", "lead": "ore", "mines": "ore", "mining": "ore",
    "fish": "fish", "fishing": "fish", "fisheries": "fish", "whaling": "fish",
    # Salt is got from a pan or a mine, and the Salt Reach is named for it — a region
    # whose defining resource claims nothing is the map failing to hear its own writer.
    "salt": "stone", "salterns": "stone", "saltpans": "stone",
}


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
    # The writer's own settlement_type word, lowercased and open-ended ("capital",
    # "market town", "fortress"...), else a population tier, else the site's.
    rank: str
    score: float
    reasons: list[str] = field(default_factory=list)
    proposed: bool = False
    # What the siting knew and used to throw away (V2 §12): the crossing the place
    # stands on, how well its market area feeds it, and its lattice cell. Blank for
    # a place read back from stored geometry — that ground was never scored.
    crossing: str = ""             # ford | pass | harbour | ""
    support: float = 0.0
    cell: tuple[int, int] | None = None

    def because(self) -> str:
        if not self.reasons:
            if self.entity_id and not self.proposed:
                # The writer's own. The ground having nothing to say about it is not a
                # criticism — a town can be somewhere for a reason no map holds — but it
                # is worth not pretending otherwise.
                return (f"{self.name} is where you put it; the ground makes no "
                        "particular case either way.")
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
        self.from_sea: list[list[int]] = []
        self.inland_max: int = 1
        self.authored_cells: dict[str, set[tuple[int, int]]] = {}
        self.landform: coast.Landform | None = None
        self.relief: relief.Relief | None = None
        self.erosion: erode.Erosion | None = None
        self.vegetation: vegetation.Vegetation | None = None
        self.hydrology: hydrology.Hydrology | None = None
        self.shore: shore.Shoreline | None = None
        self.movement: movement.Movement | None = None
        self.resources: resources.Resources | None = None
        self.settlement: settle.Settlement | None = None
        self.holds: hold.Holds | None = None
        self.reading: source.WorldReading | None = None
        self._keys: dict[str, str] = {}
        self._outlines: dict[str, list[list[list[float]]]] = {}
        self._outlines_for: int | None = None
        self.frontiers: list[territory.Frontier] = []
        self._network: roads.Roads | None = None
        self._network_for: tuple | None = None
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

        self.read_the_world()
        authored, rivers = self.build_the_world()
        placements = self._site_settlements(propose=propose_settlements)
        holds = self._site_castles(placements)

        # Everything in one transaction: without it a generated map is hundreds of
        # separate undoable actions, and the writer's first Ctrl+Z gets one polygon back.
        with self.world.db.transaction():
            self._clear_previous()
            self._write_regions(authored)
            self._write_rivers(rivers)
            self._write_settlements(placements)
            self._write_roads(placements)
            self._write_holds(holds)
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

    def read_the_world(self) -> None:
        """One reading of the writer's world, before any of it is drawn.

        Everything the map knows about their world comes from here now. It used to be
        gathered six separate times — once per region for the profiles, and again in this
        module for outlines, borders, settlements, holdings, features, populations and
        capitals, and again in the namer, and again in the ledger — with the answer kept
        nowhere and no two of them guaranteed to agree.
        """
        self.reading = source.read_world(self.world, at=self.at)
        self.profiles = profiles_from(self.reading)
        # Entity id to key, kept: everything downstream that has an id in hand and wants
        # to know what the writer said about it goes through here, and rebuilding the
        # map inside a sort key is how a stage goes quadratic without anyone noticing.
        self._keys = dict(self.reading.by_entity())

    def build_the_world(self) -> tuple[dict[str, list[list[float]]],
                                       list[list[tuple[int, int]]]]:
        """Everything the map knows, before a word of it is written down.

        One sequence, in the order the causes run in, and in one place: it used to be
        spelled out twice, once for generating a map and once for proposing one, and two
        copies of an order that keeps growing is a way to find out much later that a plan
        and a generate of the same world disagree about what is on it.

        The order is the argument, and the two passes over the partition are the part of
        it worth explaining. The land has to be divided once before the fields exist,
        because the fields need to know which country the writer called mountainous —
        but all that pass can do is divide the plane between made-up seed points, so what
        it draws is a weighted Voronoi diagram and not a political map.

        Then the ground is built, and the cost of crossing it, and then the towns, which
        are sited from that ground. And *then* the land is divided again — from the towns
        this time, over the cost of reaching them. That is the pass whose borders mean
        something: a march holds the country its halls can reach, so a valley behind a
        range that every one of its towns would have to climb belongs to whoever is on
        the near side of it, which is how it would have gone.
        """
        authored = self._authored_outlines()
        self._build_landmass(authored)
        self._assign_cells()
        self._build_fields()
        rivers = self._trace_rivers()
        # The drainage as a *system* (V2 §6): true stream order, mainstem-and-
        # tributary rivers, mouth kinds, and the meres in the wet basins. Read off
        # what erosion and vegetation already worked out, never recomputed.
        self.hydrology = hydrology.study(
            GRID, sea=self.sea, flow=self.erosion.flow,
            downstream=self.erosion.downstream, marsh=self.vegetation.marsh,
            settled=self.erosion.settled, elevation=self.elevation,
            sea_level=SEA_LEVEL, shelf_depth=SHELF_DEPTH, share=RIVER_SHARE)
        # And the coast's character (V2 §4), from the same fields plus the mouths
        # just classified — a delta is where a specific river arrives.
        self.shore = shore.classify(
            GRID, sea=self.sea, elevation=self.elevation,
            slope=self.erosion.slope, marsh=self.vegetation.marsh,
            seed=self.seed,
            mouths={s.mouth: s.mouth_kind for s in self.hydrology.systems})
        self._build_movement()
        self._build_civilisation()
        self._assign_cells(cost=self.movement.cost, from_the_towns=True)
        self._read_the_frontiers()
        self._classify_ground()
        self._build_costs()
        return authored, rivers

    def _assign_cells(self, *, cost: Field | None = None,
                      from_the_towns: bool = False) -> None:
        """Give every acre of the continent to somebody.

        A partition rather than a race between growing blobs: unclaimed land renders as
        grout between the regions on a political fill, and a writer reads that as ground
        nobody has thought about rather than as an artefact of how it was drawn.

        Run twice — see `build_the_world`. The first pass divides the plane from the
        region seeds, which is all that can be done before there is any ground. The
        second divides the country between the towns, once there are towns.
        """
        name_of = {rid: self.profiles[rid].name for rid in self.profiles}
        claimed = {name_of[rid]: {(i, j) for i, j in cells if not self.sea[j][i]}
                   for rid, cells in self.authored_cells.items()}
        for region_id, cells in list(self.authored_cells.items()):
            self.authored_cells[region_id] = {
                (i, j) for i, j in cells if not self.sea[j][i]}

        seats = None
        if from_the_towns and self.settlement is not None:
            seats = {}
            for site in self.settlement.sites:
                region_id = self.owner[site.cell[1]][site.cell[0]]
                if region_id:
                    seats.setdefault(name_of[region_id], []).append(site.cell)
        self.partition = territory.grow(
            self._grid(), self.sea, anchors=self._anchors, weights=self._weights,
            claimed=claimed, cost=cost, seats=seats)
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
        self._keep_the_shore()

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

    def _build_movement(self) -> None:
        """What it costs to cross every acre, and what that opens up.

        Before the resources and before the people, because everything after it is
        downstream of the answer: where a border settles, what a hinterland reaches,
        which crossings are worth a town, where a road can go at all.

        Travel cost used to be looked up from the dominant terrain of whichever region
        owned a cell, which is the elevation field's mistake in another costume — a marsh
        cost what its province cost on average, so a road would cross one rather than
        take the dry hillside beside it, and every acre of a march called mountainous was
        equally steep including its river valleys.
        """
        assert self.erosion is not None and self.vegetation is not None
        self.movement = movement.plan_movement(
            self._grid(), elevation=self.elevation, slope=self.erosion.slope,
            flow=self.erosion.flow, canopy=self.vegetation.canopy,
            marsh=self.vegetation.marsh, downstream=self.erosion.downstream,
            sea=self.sea, sea_level=SEA_LEVEL)

    def _build_civilisation(self) -> None:
        """What the ground offers, and where that puts people.

        In that order, and the order is the point. A town is not where the ground is
        nicest — it is where a river has to be crossed and can be here, or where the
        only pass over a range comes down, or where a bay will hold ships. So the
        crossings have to exist before anybody is placed at one, and the resources
        before anybody is fed by them.

        None of this used to be true. Resources were a list on a region page, so every
        acre of a march was equally iron and nothing could be sited *near* the iron
        because there was no near; and settlements were scored per region against a
        quota, so a march with a great harbour and one with none got the same number of
        ports.
        """
        assert self.erosion is not None and self.vegetation is not None
        assert self.movement is not None
        grid = self._grid()
        keys = self.partition.keys
        index_of = {key: n for n, key in enumerate(keys)}
        owner = [[index_of.get(self.profiles[rid].name, -1) if rid else -1
                  for rid in row] for row in self.owner]

        self.resources = resources.plan_resources(
            grid, elevation=self.elevation, slope=self.erosion.slope,
            soil=self.erosion.settled, water_table=self.vegetation.water_table,
            moisture=self.moisture, temperature=self.temperature,
            canopy=self.vegetation.canopy, marsh=self.vegetation.marsh,
            sea=self.sea, owner=owner, keys=keys,
            claimed=self._claimed_resources(), seed=self.seed, sea_level=SEA_LEVEL)

        self.settlement = settle.plan_settlement(
            grid, resources=self.resources, movement=self.movement,
            elevation=self.elevation, slope=self.erosion.slope,
            flow=self.erosion.flow, marsh=self.vegetation.marsh, sea=self.sea,
            seed=self.seed, wanted=self._settlements_the_world_implies(),
            fixed=self._settlements_the_writer_drew(),
            room=self._room_per_region(),
            region_of=self.owner, words=self._resource_words(),
            seafaring={rid for rid in sorted(self.profiles)
                       if self.profiles[rid].coastal},
            sea_level=SEA_LEVEL)

    def _settlements_the_world_implies(self) -> int:
        """How many places there are to live, before anybody asks who lives in them.

        The sum of what each region's people and land imply, with room on top: the map
        offers more sites than the writer has named towns, and the surplus is what it
        proposes. Capped, because a proposal the writer has to turn down forty times is
        not a proposal, it is a chore.
        """
        wanted = sum(self._settlements_wanted(self.profiles[rid])
                     for rid in sorted(self.profiles))
        return max(6, min(48, wanted + PROPOSAL_HEADROOM))

    def _room_per_region(self) -> dict[str, int]:
        """The budget, shared out, so that every region's share adds up to the whole.

        The two numbers have to agree, and for a while they did not. The tiers are cut
        from the *total* — a tenth of it are cities, a quarter towns — while the choosing
        may only put a settlement where its region still has room, and the rooms were the
        regions' own base figures with none of the headroom in them. So a world wanting
        eighteen places had ten to give: the city tier went looking for its one city with
        the rooms already spent on the writer's own towns, found nowhere it was allowed to
        put one, and the villages and hamlets never ran at all. Eleven towns and two
        hamlets came out, which is not a hierarchy — it is a list.

        Sharing the headroom out in proportion, largest first so the arithmetic is stable
        and the remainder falls to the biggest country, makes the rooms sum to the budget
        and lets every tier reach the ground.
        """
        base = {rid: self._settlements_wanted(self.profiles[rid])
                for rid in sorted(self.profiles)}
        if not base:
            return {}
        total = sum(base.values()) or 1
        spare = max(0, self._settlements_the_world_implies() - sum(base.values()))
        order = sorted(base, key=lambda rid: (-base[rid], rid))
        out = dict(base)
        given = 0
        for rid in order[1:]:
            share = spare * base[rid] // total
            out[rid] += share
            given += share
        out[order[0]] += spare - given          # the remainder, to the largest region
        return out

    def _settlements_the_writer_drew(self) -> dict[tuple[int, int], str]:
        """Cells holding a settlement the writer placed themselves, which do not move."""
        index = self.world.geometry_index(at=self.at, layer=LAYER_SETTLEMENTS)
        out: dict[tuple[int, int], str] = {}
        for region_id in sorted(self.profiles):
            for entity_id in self.profiles[region_id].settlements:
                for geometry in index.get(entity_id, []):
                    if (geometry.style or {}).get(GENERATED):
                        continue
                    place = geometry.coordinates
                    cell = self._cell_of(float(place[0]), float(place[1]))
                    if not self.sea[cell[1]][cell[0]]:
                        out[cell] = entity_id
        return out

    def _resource_words(self) -> dict[str, dict[str, str]]:
        """Each region's own word for what it has, keyed by the field it maps onto.

        So an explanation can say "iron close at hand" to a writer who wrote iron,
        rather than "ore in the hills nearby" — which is the same fact in a vocabulary
        they did not choose.
        """
        out: dict[str, dict[str, str]] = {}
        for region_id in sorted(self.profiles):
            said: dict[str, str] = {}
            for named in self.profiles[region_id].resources:
                word = named.strip().lower()
                kind = RESOURCE_WORDS.get(word)
                if kind and kind not in said:
                    said[kind] = word
            if said:
                out[region_id] = said
        return out

    def _claimed_resources(self) -> dict[str, dict[str, float]]:
        """What the writer said each region has, in the vocabulary the fields use."""
        wanted: dict[str, dict[str, float]] = {}
        for region_id in sorted(self.profiles):
            profile = self.profiles[region_id]
            asked: dict[str, float] = {}
            for named in profile.resources:
                kind = RESOURCE_WORDS.get(named.strip().lower())
                if kind:
                    asked[kind] = max(asked.get(kind, 0.0), 0.55)
            if asked:
                wanted[profile.name] = asked
        return wanted

    def _keep_the_shore(self) -> None:
        """Land is above the water. It has to be said, because two stages disagree.

        The mask is the writer's answer: it holds the ground they drew, above the
        waterline, whatever the noise wanted. The elevation field is the map's answer,
        and by the time erosion has cut a mouth down to base level and the shelf has
        faded the first cells inland toward it, four hundred of them sit a hundredth
        below zero — land to every stage that asks the mask, sea to every stage that
        reads the field.

        Nothing is subtle about the consequence. The relief shades them as water, the
        coastline is contoured around them, and the region borders — which are traced
        from ownership — run out across what looks like open sea to enclose them.

        So the field is brought up to the mask rather than the mask down to the field.
        That way round because the mask is where author sovereignty lives: a cell the
        writer drew inside their own province may not be quietly drowned by an erosion
        pass. The lift is small — a hundredth of the world's relief at the median — and
        it lands on ground that is already the shore.
        """
        floor = SEA_LEVEL + SHORE_FLOOR
        for j in range(GRID):
            row, wet = self.elevation[j], self.sea[j]
            for i in range(GRID):
                if not wet[i] and row[i] < floor:
                    row[i] = floor

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

        # A source is a channel cell with no channel cell draining into it.
        feeds_into: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for i, j in sorted(channel):
            step = self._downhill(i, j)
            if step is not None:
                feeds_into.setdefault(step, []).append((i, j))
        sources = [cell for cell in sorted(channel) if not feeds_into.get(cell)]

        rivers: list[list[tuple[int, int]]] = []
        for head in sources:
            path = [head]
            seen = {head}
            cursor = head
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
        """Place the writer's settlements well, then suggest the ones the land implies.

        The sites themselves come from `settle`, which chose them for reasons the ground
        supplies — a ford, a pass, an anchorage, country enough to feed a market. What is
        left here is deciding who lives at each: the writer's own towns take the best of
        them inside their own region, biggest first, which is how they came to be the
        biggest; anything still standing empty is offered as a proposal.
        """
        assert self.settlement is not None
        index = self.world.geometry_index(at=self.at, layer=LAYER_SETTLEMENTS)
        placements: list[Placement] = []
        spare = [site for site in self.settlement.sites if site.entity_id is None]
        used: set[tuple[int, int]] = set()

        for region_id in sorted(self.profiles):
            profile = self.profiles[region_id]
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
                    continue          # the writer put it there; leave it alone (§66)
                site = self._best_site_in(region_id, spare, used)
                if site is None:
                    break
                used.add(site.cell)
                placements.append(self._placement_at(
                    site, entity.id, entity.name, region_id, proposed=False))

        if propose:
            # The sites were already shared out per region when they were chosen — see
            # the `room` argument to `plan_settlement` — so what is left here is simply
            # offering the ones nobody named.
            for site in spare:
                if site.cell in used:
                    continue
                region_id = self.owner[site.cell[1]][site.cell[0]]
                if not region_id:
                    continue
                used.add(site.cell)
                placements.append(self._placement_at(
                    site, None,
                    self._propose_name(self.profiles[region_id], len(placements)),
                    region_id, proposed=True))
        return placements

    def settlement_findings(self) -> list:
        """Where the writer's own towns and the ground under them disagree.

        Not a correction — their town is where they put it and stays there. But a city
        standing on ground the map reckons would feed a hamlet is worth one sentence,
        because it is usually either a thing the writer knows (it lives on trade, or
        tribute, or it is a fortress) or a thing they had not thought about.
        """
        from fw.core.mapgen.findings import note

        assert self.settlement is not None
        order = {name: n for n, (name, _, _, _) in enumerate(settle.TIERS)}
        out = []
        for site in self.settlement.sites:
            if not site.entity_id:
                continue
            entity = self.world.get_entity(site.entity_id)
            people = self._population_of(site.entity_id)
            if entity is None or not people:
                continue
            said = self._rank_for_population(people)
            if order.get(site.rank, 9) - order.get(said, 9) < 2:
                continue
            out.append(note(
                "scale",
                f"{entity.name} has {people:,} people, and the country within a day of "
                f"it would feed something nearer a {site.rank}. Nothing has been moved — "
                "if it lives on trade or tribute or is there to hold a pass, that is "
                "worth writing down somewhere",
                subjects=(entity.name,)))
        out.extend(self._harbours_the_sea_did_not_reach())
        return out

    # How near the water a place has to be for a ship to put in, in lattice cells. The
    # same reach the crossings use, and for the same reason: a cell is a few miles.
    QUAY_REACH = 8

    def _harbours_the_sea_did_not_reach(self) -> list:
        """A town the writer called a port, with the coastline drawn somewhere else.

        Their town does not move (§66) and the coast is grown from their own regions, so
        this is a disagreement the map cannot resolve on its own — but it is one they
        would certainly want to know about, because a port that is thirty leagues inland
        is a port no ship reaches, and every crossing the map draws will land elsewhere.
        """
        from fw.core.mapgen.findings import note

        if self.settlement is None:
            return []
        out = []
        for site in self.settlement.sites:
            if not site.entity_id:
                continue
            entity = self.world.get_entity(site.entity_id)
            if entity is None:
                continue
            if self._rank_of(site.entity_id, site.rank) not in ("port", "harbour",
                                                                "harbor"):
                continue
            if self._sea_within(site.cell, self.QUAY_REACH):
                continue
            leagues = round(self.from_sea[site.cell[1]][site.cell[0]] * CELL)
            out.append(note(
                "contradiction",
                f"You have {entity.name} as a port, and the coastline came out about "
                f"{leagues} away from it. It has not been moved — but no ship reaches "
                "it, so any crossing the map draws will land somewhere else",
                subjects=(entity.name,)))
        return out

    def _sea_within(self, cell: tuple[int, int], reach: int) -> bool:
        i, j = cell
        for dj in range(-reach, reach + 1):
            for di in range(-reach, reach + 1):
                ni, nj = i + di, j + dj
                if 0 <= ni < GRID and 0 <= nj < GRID and self.sea[nj][ni]:
                    return True
        return False

    def _best_site_in(self, region_id: str, spare, used: set):
        """The best unclaimed site inside a region, or None if it has run out."""
        for site in spare:
            if site.cell in used:
                continue
            if self.owner[site.cell[1]][site.cell[0]] == region_id:
                return site
        return None

    def _placement_at(self, site, entity_id, name: str, region_id: str, *,
                      proposed: bool) -> Placement:
        i, j = site.cell
        x, y = self._centre(i, j)
        # Nudge off the exact cell centre, stably, so a map does not look like a grid.
        x += noise.jitter(self.seed, f"px:{name}:{i}:{j}", CELL * 0.35)
        y += noise.jitter(self.seed, f"py:{name}:{i}:{j}", CELL * 0.35)
        # What the writer said about how big a place is beats what the map worked out,
        # and where they said nothing the map's own reading stands.
        rank = self._rank_of(entity_id, site.rank)
        # The same veto the reasons get (§66): a harbour crossing in a march the
        # writer never called coastal is not a harbour the map may claim.
        crossing = site.crossing
        profile = self.profiles.get(region_id)
        if crossing == "harbour" and (profile is None or not profile.coastal):
            crossing = ""
        return Placement(
            entity_id=entity_id, name=name,
            x=round(max(MARGIN, min(SPAN - MARGIN, x)), 1),
            y=round(max(MARGIN, min(SPAN - MARGIN, y)), 1),
            region_id=region_id, rank=rank, score=round(site.score, 3),
            reasons=self._reasons_the_region_allows(site, region_id),
            proposed=proposed,
            crossing=crossing, support=site.support, cell=site.cell,
        )

    def _reasons_the_region_allows(self, site, region_id: str) -> list[str]:
        """A site's case, answering to the country it finally stands in.

        Sites are chosen before the land is divided for the last time — they have to be,
        because the last division is grown from them — so a place can be scored as part
        of one march and end up inside another. Mostly that changes nothing. The harbour
        is the exception, because a port is the one reason that the writer has an
        explicit veto over: a march they described as a river plain does not acquire a
        seaport because the coastline came near it (§66), and it must not acquire one by
        the back door either, by inheriting the case made for a neighbour that does reach
        the sea.
        """
        if self.profiles[region_id].coastal:
            return list(site.reasons)
        return [why for why in site.reasons if "harbour" not in why]

    @staticmethod
    def _rank_for_population(people: int) -> str:
        return ranks.rank_for_population(people)

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
        for route in self.road_network(known).routes:
            a, b = route.joins
            p, q = known[a], known[b]
            path = list(route.cells)
            points = self._road_line(p, path, q)
            self.world.add_geometry(
                self._road_entity(), "line", points, layer=LAYER_ROADS,
                style={"role": "road", GENERATED: GENERATOR})
            length = sum(math.dist(points[n], points[n + 1])
                         for n in range(len(points) - 1))
            self.world.add_route_segment(
                p.entity_id, q.entity_id, round(length, 1),
                medium="road", quality=0.8, entity_id=self._road_entity(),
                terrain=self._road_terrain(path))
            laid += 1
        self.report.roads = laid

    def _water_line(self, path: list[tuple[int, int]]) -> list[list[float]]:
        """A river's cells, as a line somebody could have drawn.

        The same argument the roads make, and the roads were doing it alone: a channel
        found by walking a lattice arrives as a staircase, and no river on any map has
        ever run four cells due south and then three due east. Both ends are pinned —
        a reach has to still meet the reach above and below it.
        """
        points = [self._centre(i, j) for i, j in path]
        if len(points) < 3:
            return [[round(x, 1), round(y, 1)] for x, y in points]
        drawn = shapes.simplify(shapes.eased(points), WATER_TOLERANCE)
        return [[round(x, 1), round(y, 1)] for x, y in drawn]

    def _road_line(self, start: Placement, path: list[tuple[int, int]],
                   end: Placement) -> list[list[float]]:
        """A route's cells, as a line somebody could have drawn.

        The cells come from a search over a lattice of a hundred and forty-four squares,
        so a road across level country arrives as fourteen steps due south and then
        eleven due east. Cutting the corners takes the staircase off it, and simplifying
        afterwards throws away the points that cutting added to the stretches that were
        genuinely straight — so the smoothed road reaches the client with rather fewer
        vertices than the raw one, not four times as many.
        """
        points: list[tuple[float, float]] = [(start.x, start.y)]
        points.extend(self._centre(i, j) for i, j in path[1:-1])
        points.append((end.x, end.y))
        drawn = shapes.simplify(shapes.eased(points), ROAD_TOLERANCE)
        return [[round(x, 1), round(y, 1)] for x, y in drawn]

    def _roads_the_writer_has(self, known: list[Placement]) -> list[tuple[int, int, str]]:
        """The roads and trade routes already in the world, as pairs of places.

        The Iron Road joins Greyhaven to Rennford; the Salt Run joins Blackmere to
        Rennford. Both have been in the world as `connects` facts since it was written,
        and the stage that lays roads has never read either — so the map drew its own
        network beside the writer's rather than out of it.
        """
        if self.reading is None:
            return []
        where = {p.entity_id: n for n, p in enumerate(known) if p.entity_id}
        by_key = {s.key: s.entity_id for s in self.reading.settlements if s.entity_id}
        out: list[tuple[int, int, str]] = []
        seen: set[tuple[int, int]] = set()
        for route in self.reading.routes:
            ends = [where[by_key[key]] for key in route.endpoint_keys
                    if by_key.get(key) in where]
            for a, b in zip(ends, ends[1:], strict=False):
                pair = (min(a, b), max(a, b))
                if a != b and pair not in seen:
                    seen.add(pair)
                    out.append((pair[0], pair[1], route.name))
        return out

    def road_network(self, known: list[Placement]):
        """The bundled road network between the places handed in, worked out once.

        Cached on the list it was asked about, because the plan path asks for it and
        then asks again per draft, and laying the whole network twice is the most
        expensive thing in the stage.
        """
        key = tuple((p.x, p.y, p.rank) for p in known)
        if getattr(self, "_network_for", None) == key:
            return self._network
        # The writer's own rank vocabulary included: a written capital used to fall
        # to the 1.0 default — below a village — and generated hamlet-level traffic,
        # so the roads out of it never bundled into anything (V2 §10).
        weight = {"capital": 6.0, "city": 5.0, "port": 4.5, "harbour": 4.5,
                  "harbor": 4.5, "market town": 3.5, "fortress": 3.0,
                  "town": 3.0, "village": 1.6, "hamlet": 1.0}
        self._network = roads.plan_roads(
            self._grid(),
            places=[self._cell_of(p.x, p.y) for p in known],
            weights=[weight.get(p.rank, 1.0) for p in known],
            cost=self.cost, sea=self.sea,
            demanded=self._roads_the_writer_has(known))
        self._network_for = key
        return self._network

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
                        region_id=region_id, rank=self._rank_of(sid, "town"),
                        score=0.0,
                        cell=self._cell_of(float(geometry.coordinates[0]),
                                           float(geometry.coordinates[1]))))
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
            owner=owner, keys=keys,
            canopy=self.vegetation.canopy if self.vegetation else None,
            marsh=self.vegetation.marsh if self.vegetation else None)
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
        """The price of crossing every cell, taken from the ground rather than the map.

        It used to be looked up from the dominant terrain of whichever region owned the
        cell, which is the elevation field's mistake in another costume: a marsh cost
        what its province cost on average, so a road would cross one rather than take
        the dry hillside beside it, and every acre of a march called mountainous was
        equally steep including its river valleys.
        """
        assert self.movement is not None
        self.cost = self.movement.cost

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
        """Every region gets the territory its claim implies, the writer's included.

        This is the second copy of a decision `pipeline._region_drafts` also makes, and
        for a while the two disagreed: that one was changed to draw an authored region's
        traced extent and this one still skipped it, so the same world came out with
        different regions depending on which way it was generated. Two code paths
        disagreeing about one decision is how a map ends up with two answers.

        `regions_kept` still means what it says — the writer's own ring is kept, always,
        untouched — but a kept ring is no longer a reason to draw nothing. See
        `coast._hold` for why a ring is a claim about where a country is rather than a
        line its coast follows.
        """
        for region_id in sorted(self.profiles):
            profile = self.profiles[region_id]
            if region_id in authored:
                self.report.regions_kept.append(profile.name)
            ring = self._outline(region_id)
            if ring is None:
                continue
            self.world.add_geometry(
                region_id, "polygon", [ring], layer=LAYER_REGIONS,
                approximate=True,          # §92: a generated border is not a surveyed one
                style={"role": _terrain_role(profile.dominant),
                       GENERATED: GENERATOR})
            self.report.regions_drawn.append(profile.name)

    def _outline(self, region_id: str) -> list[list[float]] | None:
        """A region's shape, assembled from the borders it shares with its neighbours.

        Every border on the map is traced once and both its regions draw the same line,
        so the edge between two marches is one edge rather than two that agree to within
        a lattice cell — which is what it used to be, measured, all the way along.
        """
        name = self.profiles[region_id].name
        rings = self._all_outlines().get(name) or []
        return rings[0] if rings else None

    def _all_outlines(self) -> dict[str, list[list[list[float]]]]:
        """Every region's rings, from one walk of the ownership field, kept.

        The walk is shared by construction, so doing it per region would both cost n
        times as much and throw away the sharing it exists for.
        """
        key = id(self.partition)
        if self._outlines_for != key:
            self._outlines = territory.outlines(self.partition, self.sea)
            self._outlines_for = key
        return self._outlines

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
            points = self._water_line(path)
            name = f"Unnamed river {n + 1}"
            river = self.world.add_entity(
                "waterway", name, confidence="speculative", tags=[GENERATED_TAG],
                summary="Traced by the map generator; rename it and it is yours.")
            self.world.add_geometry(
                river.id, "line", points, layer=LAYER_WATER,
                style={"role": "waterway", GENERATED: GENERATOR})
            self.report.rivers.append(name)

    def _read_the_frontiers(self) -> None:
        """Go and look at what the borders turned out to run along.

        Not what they were made to follow — they were not made to follow anything, and
        `Grid.claimed_from` explains at length why they could not have been. Whether the
        line between two marches climbs a ridge or crosses forty miles of wheat is the
        most consequential fact on a political map and the one a coloured fill hides
        completely, so it is measured after the fact and told to the writer.
        """
        assert self.partition is not None and self.erosion is not None
        biggest = max((self.erosion.flow[j][i] for j in range(GRID)
                       for i in range(GRID) if not self.sea[j][i]), default=1.0) or 1.0
        self.frontiers = territory.frontiers(
            self.partition, elevation=self.elevation, flow=self.erosion.flow,
            marsh=self.vegetation.marsh, sea=self.sea, biggest_flow=biggest)

    def frontier_findings(self) -> list:
        """The borders nothing defends, which is where the writer's wars will be.

        A note rather than a warning: an open frontier is not a mistake, it is a fact,
        and usually a more interesting one than a tidy border along a range. What it is
        not is visible, which is the whole reason for saying it.
        """
        from fw.core.mapgen.findings import note

        out = []
        for frontier in self.frontiers:
            if frontier.open_country < territory.MOSTLY_OPEN:
                continue
            a, b = frontier.between
            out.append(note(
                "adjacency",
                f"Nothing much stands between {a} and {b}: "
                f"{frontier.open_country:.0%} of the border between them is open "
                f"country, and the rest is not a great deal harder. Two neighbours who "
                f"can walk into each other is a fact worth knowing about, in either "
                f"direction",
                subjects=(a, b)))
        return out

    def _site_castles(self, placements: list[Placement]) -> list:
        """Where the map would put a castle, once it knows what there is to hold.

        Last of the stages, and it has to be: a castle is placed against the crossings,
        the roads and the borders, and none of those exist until the towns do. It is also
        the stage that most obviously reads as a consequence rather than a decoration —
        the highest-scoring cell on the example continent is the pass the highway climbs
        on the march between two regions, which is three separate earlier answers
        agreeing without being asked to.
        """
        assert self.movement is not None and self.settlement is not None
        known = list(placements) + self._already_placed(placements)
        frontier = {cell for f in self.frontiers for cell in f.cells}
        network = (self.road_network(known) if len(known) >= 2 else None)
        traffic = network.traffic if network else self._grid().filled(0.0)
        self.holds = hold.plan_holds(
            self._grid(), movement=self.movement, traffic=traffic,
            frontier_cells=frontier,
            seats=[self._cell_of(p.x, p.y) for p in known],
            elevation=self.elevation, slope=self.erosion.slope,
            marsh=self.vegetation.marsh, sea=self.sea, seed=self.seed,
            wanted=min(hold.CEILING, max(2, len(self.profiles) * hold.PER_REGION)),
            fixed=self._castles_the_writer_drew(),
            room={rid: hold.PER_REGION for rid in sorted(self.profiles)},
            region_of=self.owner, houses=self._halls())
        return list(self.holds.sites)

    # Who can hold a castle. A guild has a hall and a company has a camp; neither holds
    # a march. Getting this wrong put two keeps in the hands of the Ironmongers of Red
    # Ford and left House Marr, which legally owns three of the writer's places, with
    # none at all.
    LANDED = ("house", "dynasty", "order", "clan", "tribe")

    def political(self) -> dict[str, dict[str, object]]:
        """Who holds each region, under each of §11's four authorities and by title.

        Not from whose hall is nearest, which was the first attempt and is a different
        question with a different answer: House Dray and House Marr are both seated at
        Northwatch, and it is Marr that owns the Northmarch.

        The four authorities are kept apart all the way to the fill. The map has to
        choose one colour, and it chooses whoever is actually in charge — an army in the
        streets over a steward, a steward over an absent charter — but it carries the
        other three onto the shape, so a writer looking at a march coloured for the
        house that runs it can still see who owns it and who is taxing it.
        """
        if self.reading is None:
            return {}
        landed = {h.key for h in self.reading.houses if h.type_key in self.LANDED}
        named = {h.key: h.name for h in self.reading.houses}
        out: dict[str, dict[str, object]] = {}
        for region_id, profile in self.profiles.items():
            region = self.reading.region(key_for("region", profile.name))
            if region is None:
                continue
            held = self.reading.authority_over(region.key)
            # A guild has a hall and a company has a camp; neither holds a march. A
            # nearest-hall rule put two keeps in the hands of the Ironmongers of Red
            # Ford and left House Marr, which owns three of the writer's places, none.
            under = {word: key for word, key in
                     (("legally_owns", held.owns), ("administers", held.administers),
                      ("occupies", held.occupies), ("taxes", held.taxes))
                     if key in landed}
            chosen = (under.get("occupies") or under.get("administers")
                      or under.get("legally_owns"))
            title = self.reading.holder_of(region.key)
            if chosen is None and title is None:
                continue
            out[region_id] = {
                "region_key": region.key,
                "holder_key": chosen,
                "holder": named.get(chosen or "", ""),
                # Which of the four this colour actually stands for, so the legend can
                # say "as administered" rather than implying a single kind of holding.
                "authority": next((word for word in ("occupies", "administers",
                                                     "legally_owns")
                                   if under.get(word) == chosen and chosen), ""),
                "under": {word: named.get(key, "") for word, key in sorted(under.items())},
                "claimed_by": tuple(named.get(k, k) for k in held.claims
                                    if k not in held.held_by),
                "title": title.name if title else "",
                "title_holder": title.holder_name if title else "",
                "title_holder_key": title.holder_key if title else None,
            }
        return out

    def _landholders(self) -> dict[str, tuple[str, str]]:
        """Whose house each region is, for the stages that only need the one answer."""
        return {rid: (str(row["holder"]), str(row["holder_key"]))
                for rid, row in self.political().items() if row.get("holder_key")}

    def _halls(self) -> dict[tuple[int, int], tuple[str, str]]:
        """Whose country each acre is, for the stage that puts castles on it."""
        holders = self._landholders()
        if not holders:
            return {}
        where: dict[tuple[int, int], tuple[str, str]] = {}
        for j in range(GRID):
            for i in range(GRID):
                if self.sea[j][i]:
                    continue
                held = holders.get(self.owner[j][i] or "")
                if held:
                    where[(i, j)] = held
        return where

    def _castles_the_writer_drew(self) -> dict[tuple[int, int], str]:
        """Cells holding a castle the writer placed themselves, which do not move."""
        index = self.world.geometry_index(at=self.at, layer=LAYER_CASTLES)
        out: dict[tuple[int, int], str] = {}
        for entity in self.world.entities("holding"):
            if GENERATED_TAG in entity.tags:
                continue
            if self.at is not None and not entity.exists_on(self.at):
                continue
            for geometry in index.get(entity.id, []):
                if (geometry.style or {}).get(GENERATED) or geometry.kind != "point":
                    continue
                cell = self._cell_of(float(geometry.coordinates[0]),
                                     float(geometry.coordinates[1]))
                if not self.sea[cell[1]][cell[0]]:
                    out[cell] = entity.id
        return out

    def _write_holds(self, holds: list) -> None:
        for place in holds:
            entity_id = place.entity_id
            x, y = self._centre(*place.cell)
            x += noise.jitter(self.seed, f"hx:{place.cell}", CELL * 0.3)
            y += noise.jitter(self.seed, f"hy:{place.cell}", CELL * 0.3)
            if entity_id is None:
                where = self.owner[place.cell[1]][place.cell[0]]
                proposal = self.world.add_entity(
                    "holding", self._propose_hold_name(place, where),
                    confidence="speculative",
                    summary="; ".join(place.reasons) or "a place worth holding",
                    tags=["proposed"])
                entity_id = proposal.id
                if where:
                    self.world.assert_fact(entity_id, "located_in", where)
            self.world.add_geometry(
                entity_id, "point",
                [round(max(MARGIN, min(SPAN - MARGIN, x)), 1),
                 round(max(MARGIN, min(SPAN - MARGIN, y)), 1)],
                layer=LAYER_CASTLES,
                style={GENERATED: GENERATOR, "rank": place.rank})

    def _propose_hold_name(self, place, region_id: str | None) -> str:
        """A placeholder that says what it is and whose — never an invention.

        With a house it can say so: an unnamed keep of House Marr is a far more useful
        thing to be offered than an unnamed keep.
        """
        if getattr(place, "house", ""):
            return f"Unnamed {place.rank} of {place.house} at the {place.watches}"
        where = self.profiles[region_id].name if region_id else "the march"
        return f"Unnamed {place.rank} at the {place.watches} ({where})"

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

    def _said_of(self, entity_id: str | None):
        """What the writer wrote about this place, if it is one of theirs."""
        if self.reading is None or not entity_id:
            return None
        key = self._keys.get(entity_id)
        return self.reading.settlement(key) if key else None

    def _population_of(self, entity_id: str) -> int:
        """How many people, out of the one reading — regions and towns alike."""
        if self.reading is None:
            return 0
        key = self._keys.get(entity_id)
        if key is None:
            return 0
        row = self.reading.region(key) or self.reading.settlement(key)
        return int(row.population.value) if row else 0

    def _rank_of(self, entity_id: str | None, fallback: str) -> str:
        """What kind of place this is, in the writer's own word where they gave one.

        They wrote `settlement_type` on every town in the example world — capital, port,
        fortress, market town — and the map read the population instead and called all
        six of them towns. A capital drawn as a town is the settlement hierarchy the
        last three phases worked out, thrown away at the last step (§66).
        """
        said = self._said_of(entity_id)
        if said is None:
            return fallback
        # The rule itself lives in `ranks`, because `/api/map` has to reach the same
        # answer about a town the writer placed by hand on a world that was never
        # generated — where there is no reading to ask. Two copies of a settlement
        # rank rule is the defect this phase has spent three commits unpicking.
        return ranks.rank_of(
            said.rank.value if said.rank.stated else None,
            said.population.value or None, fallback)

    def _is_capital(self, entity_id: str) -> bool:
        said = self._said_of(entity_id)
        return bool(said and said.rank.value.lower() == "capital")


def _terrain_role(kind: str) -> str:
    return cartography.terrain_role(kind)


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
