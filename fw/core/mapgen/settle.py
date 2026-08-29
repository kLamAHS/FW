"""Where people live, and why there rather than a mile away.

A town is not where the ground is nicest. It is where something makes people stop: a
river that has to be crossed and can be here, a pass that is the only way over, a bay
that will hold ships, the last place upriver a boat can reach. Around that there has to
be enough country to feed it. Those two — a reason to stop and a hinterland to eat —
are what this stage is, and both of them are now things the map actually knows.

The old version scored each cell of each region and took the best few, with the number
of settlements coming from a quota per region. That gets towns in reasonable places and
gets everything else wrong. It cannot make one town bigger than another for any reason
to do with the land, because size came from the writer's population figure and position
came from a score, and the two never met. And a quota means a region with a great harbour
and a region with none get the same number of ports.

Here size is a *consequence*. Sites are chosen for their reasons; then every acre is
assigned to whichever settlement is cheapest to reach — which is what a market area is —
and a settlement's rank comes from what its own market area can feed. A city is a city
because a great deal of country has nowhere nearer to sell its grain. That also means a
settlement can turn out to be a hamlet, and it should: most of them were.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from fw.core.mapgen import noise
from fw.core.mapgen.grid import Field, Grid
from fw.core.mapgen.movement import Crossing, Movement
from fw.core.mapgen.resources import KINDS as RESOURCE_KINDS
from fw.core.mapgen.resources import Resources

# How far a town reaches for the things it carries away, and how far it will reach for
# something the writer has told us that country is *for*.
#
# The two differ because they answer different questions. Whether to mention the fishing
# is about what is at hand — say it of a town seven cells inland and it is nonsense.
# Whether a march named for its iron has its towns near the iron is about what the place
# is, and the answer has to reach as far as the seams do: the mines are at the top of the
# valley, the town is at the bottom, and on this lattice that is further than a village
# looks at its own fields.
REACH = 3
REACH_CLAIMED = 8

# How far a settlement's hinterland reaches when its provision is measured, in cells.
# A day's cart each way, near enough; beyond that grain is worth less than the journey.
HINTERLAND = 5

# What the land offers, as food. Grain feeds most people, pasture fewer per acre, and a
# fishery feeds a village handsomely but does not scale into a city.
FOOD = {"arable": 1.0, "pasture": 0.42, "fish": 0.55}

# What makes a place worth stopping at, against what its country is worth.
WEIGHT_FOOD = 3.0
WEIGHT_WATER = 1.4            # fresh water at hand
# A ford, a pass, a harbour — and they are not worth the same. A harbour is worth most,
# because a port's hinterland is the sea and nothing else on this list gives a place a
# reason to exist that does not depend on the country round it. Weighted the same, the
# example kingdom put every one of its forty settlements at a ford or a pass and not one
# on the coast, which for a realm with thirty sheltered anchorages is absurd.
WEIGHT_CROSSING = {"harbour": 3.1, "ford": 1.9, "pass": 1.7}
WEIGHT_DEFENCE = 0.9          # ground that stands above what surrounds it
WEIGHT_REACH = 1.1            # and country that is cheap to get about in
WEIGHT_RESOURCE = 0.7         # and something worth carrying away

# And what rules a place out.
MARSH_REFUSAL = 2.6
STEEP_REFUSAL = 2.2
HIGH_REFUSAL = 1.8
HIGH_FROM = 0.55

# How near a crossing counts as being at it.
CROSSING_REACH = 2.5

# The tiers, as (name, how far apart, how much country one needs, how many at most as a
# share of the whole budget).
#
# A settlement hierarchy is *nested*: hamlets sell to villages, villages to towns, towns
# to cities. That is why it exists at all, and it is why ranking a flat list of equally
# spaced places by the size of their market areas cannot produce one — a partition among
# equals gives equal shares, so the first attempt found eight cities, fifteen towns and
# three villages, which is not a kingdom, it is a conurbation.
#
# Choosing at several scales instead gives the thing itself. A few places stand far
# enough apart to draw on a whole province; more stand at a day's ride; most are a walk
# from their neighbours. How many of each falls out of how much country there is and
# what shape it is, rather than out of a quota.
TIERS = (
    ("city", 26, 30.0, 0.10),
    ("town", 13, 12.0, 0.26),
    ("village", 7, 3.5, 0.55),
    ("hamlet", 4, 1.0, 1.00),
)


@dataclass(frozen=True)
class Site:
    """One place people live, and the case for it."""

    cell: tuple[int, int]
    rank: str
    score: float
    support: float                       # what its market area can feed
    reasons: tuple[str, ...] = ()
    crossing: str = ""                   # ford | pass | harbour, if it is at one
    entity_id: str | None = None         # if the writer already has one here

    @property
    def invented(self) -> bool:
        return self.entity_id is None


@dataclass
class Settlement:
    """Where people are, and the working that put them there."""

    sites: tuple[Site, ...]
    provision: Field                     # what each cell's neighbourhood can feed
    market: list[list[int]]              # index into `sites`; -1 where nobody is nearer

    def of_rank(self, rank: str) -> tuple[Site, ...]:
        return tuple(s for s in self.sites if s.rank == rank)


def plan_settlement(grid: Grid, *, resources: Resources, movement: Movement,
                    elevation: Field, slope: Field, flow: Field, marsh: Field,
                    sea: list[list[bool]], seed: str, wanted: int,
                    fixed: dict[tuple[int, int], str] | None = None,
                    room: dict[str, int] | None = None,
                    region_of: list[list[str]] | None = None,
                    words: dict[str, dict[str, str]] | None = None,
                    seafaring: set[str] | None = None,
                    sea_level: float = 0.0) -> Settlement:
    """Site settlements, then let their market areas decide how big they are."""
    size = grid.size
    provision = _provision(grid, resources=resources, sea=sea)
    at_crossing = _crossings_by_cell(movement)
    within = _within_reach(grid, resources, sea, REACH)
    # Only the resources somebody has actually named need the longer reach.
    spoken = {kind for said in (words or {}).values() for kind in said}
    within_claimed = _within_reach(grid, resources, sea, REACH_CLAIMED, only=spoken)
    score, reasons, ports = _score(grid, provision=provision, movement=movement,
                                   at_crossing=at_crossing, elevation=elevation,
                                   slope=slope, flow=flow, marsh=marsh, sea=sea,
                                   resources=resources, within=within,
                                   within_claimed=within_claimed,
                                   region_of=region_of,
                                   words=words or {}, seafaring=seafaring,
                                   seed=seed, sea_level=sea_level)

    chosen = _choose(grid, score=score, provision=provision, sea=sea, wanted=wanted,
                     fixed=fixed or {}, room=room, region_of=region_of,
                     ports=ports, seafaring=seafaring,
                     workings=_workings(grid, within_claimed, words or {},
                                        region_of=region_of, sea=sea))
    if not chosen:
        return Settlement(sites=(), provision=provision,
                          market=[[-1] * size for _ in range(size)])

    market = _markets(grid, [cell for cell, _, _ in chosen], movement=movement, sea=sea)
    support = _support(grid, market=market, provision=provision, sea=sea,
                       count=len(chosen))

    sites: list[Site] = []
    for index, (cell, rank, entity_id) in enumerate(chosen):
        i, j = cell
        fed = support[index]
        sites.append(Site(
            cell=cell, rank=rank, score=score[j][i], support=round(fed, 2),
            reasons=tuple(reasons.get(cell, ())),
            crossing=at_crossing.get(cell, Crossing(cell, "", "")).kind,
            entity_id=entity_id))
    sites = _needs_a_hinterland(sites)
    # Biggest first, so a reader of the list meets the cities before the hamlets.
    order = {name: n for n, (name, _, _, _) in enumerate(TIERS)}
    sites.sort(key=lambda s: (order.get(s.rank, 99), -s.support, s.cell))
    return Settlement(sites=tuple(sites), provision=provision, market=market)


def _workings(grid: Grid, within_claimed: dict[str, Field],
              words: dict[str, dict[str, str]], *,
              region_of: list[list[str]] | None,
              sea: list[list[bool]]) -> dict[str, set[tuple[int, int]]]:
    """Where each region's own named resource is close enough to work.

    Only the ones that put a settlement somewhere it would otherwise never be. Grain and
    grazing are already what the scoring is mostly made of, and fish is what a harbour is
    for; iron, stone and timber are the ones that draw people up a valley nobody would
    otherwise farm, which is what a mining town is.
    """
    if region_of is None:
        return {}
    size = grid.size
    out: dict[str, set[tuple[int, int]]] = {}
    for region_id, said in sorted(words.items()):
        cells: set[tuple[int, int]] = set()
        for kind in sorted(said):
            if kind not in DRAWS_PEOPLE:
                continue
            field = within_claimed.get(kind)
            if not field:
                continue
            for j in range(size):
                row = field[j]
                for i in range(size):
                    if (not sea[j][i] and row[i] >= WORTH_CLAIMED
                            and region_of[j][i] == region_id):
                        cells.add((i, j))
        if cells:
            out[region_id] = cells
    return out


def _needs_a_hinterland(sites: list[Site]) -> list[Site]:
    """A city has to have some country behind it. Nothing else is enforced here.

    The tier a place gets is about elbow room, which is most of what makes a city: a
    market town is one because there is no other for a day's ride. Its *support* is a
    different thing — how rich the country it draws on happens to be — and the two are
    allowed to disagree. A wealthy village beside a town is an ordinary sight and the map
    should be able to draw one.

    What is not ordinary is a city with almost nothing behind it, and the example kingdom
    produced one: eleven units of hinterland among towns with a hundred and seventy,
    because it had been squeezed between two others and its market area had nowhere to
    go. So the rule is a floor and not a ranking. Trying to fix it by ordering the ranks
    by support instead cascaded — it demoted a perfectly good second city for standing
    four units below the largest town — which is what comes of repairing one axis with
    another.

    The floor is the middle site's own support, so it scales with the world rather than
    being a number chosen against one map.
    """
    if len(sites) < 4:
        return sites
    middle = sorted(site.support for site in sites)[len(sites) // 2]
    order = [name for name, _, _, _ in TIERS]
    out: list[Site] = []
    for site in sites:
        rank = site.rank
        if rank == order[0] and site.support < middle:
            rank = order[1]
        out.append(Site(cell=site.cell, rank=rank, score=site.score,
                        support=site.support, reasons=site.reasons,
                        crossing=site.crossing, entity_id=site.entity_id))
    return out


# ---- what the country round a place is worth --------------------------------


def _provision(grid: Grid, *, resources: Resources, sea: list[list[bool]]) -> Field:
    """What each cell's neighbourhood could feed, in arbitrary mouths.

    A town does not eat its own acre; it eats its hinterland, so the food is gathered
    over a radius before anything is scored. That single change is what stops settlements
    landing on the one perfect field in a barren country.
    """
    size = grid.size
    food = grid.filled(0.0)
    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                continue
            food[j][i] = sum(weight * resources.at(kind, i, j)
                             for kind, weight in FOOD.items())

    # Summed by rows and then by columns rather than by looking at a square of a
    # hundred and twenty-one cells around each of twenty thousand. The total over a
    # square is the total of the totals over its rows, so two sweeps with a running sum
    # give the same answer for a fraction of the work — and this is the hottest thing in
    # the stage by some way.
    across = grid.filled(0.0)
    for j in range(size):
        row = food[j]
        running = sum(row[0:min(size, HINTERLAND + 1)])
        for i in range(size):
            across[j][i] = running
            leaving = i - HINTERLAND
            arriving = i + HINTERLAND + 1
            if leaving >= 0:
                running -= row[leaving]
            if arriving < size:
                running += row[arriving]

    out = grid.filled(0.0)
    for i in range(size):
        running = sum(across[b][i] for b in range(0, min(size, HINTERLAND + 1)))
        for j in range(size):
            if not sea[j][i]:
                out[j][i] = running
            leaving = j - HINTERLAND
            arriving = j + HINTERLAND + 1
            if leaving >= 0:
                running -= across[leaving][i]
            if arriving < size:
                running += across[arriving][i]
    return out


def _crossings_by_cell(movement: Movement) -> dict[tuple[int, int], Crossing]:
    best: dict[tuple[int, int], Crossing] = {}
    for crossing in movement.crossings():
        held = best.get(crossing.cell)
        if held is None or crossing.strength > held.strength:
            best[crossing.cell] = crossing
    return best


def _score(grid: Grid, *, provision: Field, movement: Movement,
           at_crossing: dict[tuple[int, int], Crossing], elevation: Field,
           slope: Field, flow: Field, marsh: Field, sea: list[list[bool]],
           resources: Resources, within: dict[str, Field],
           within_claimed: dict[str, Field], region_of: list[list[str]] | None,
           words: dict[str, dict[str, str]], seafaring: set[str] | None,
           seed: str, sea_level: float) -> tuple[Field, dict, set]:
    """How good a place each cell is to found something, and the case for it."""
    size = grid.size
    land = [(i, j) for j in range(size) for i in range(size) if not sea[j][i]]
    if not land:
        return grid.filled(0.0), {}, set()

    most_fed = max(provision[j][i] for i, j in land) or 1.0
    most_water = max(flow[j][i] for i, j in land) or 1.0
    cheapest = min(movement.cost[j][i] for i, j in land) or 1.0

    # Where the nearest crossing is, so a town can be *near* a ford without standing in
    # the river. One sweep answers it for every cell.
    reach, kind_at, strength_at = _nearest_crossing(grid, at_crossing, sea)

    score = grid.filled(0.0)
    why: dict[tuple[int, int], list[str]] = {}
    ports: set[tuple[int, int]] = set()
    for i, j in land:
        because: list[str] = []
        fed = provision[j][i] / most_fed
        value = WEIGHT_FOOD * fed
        if fed > 0.55:
            because.append("good country to feed it")

        carried = flow[j][i] / most_water
        if carried > 0.002:
            water = min(1.0, carried * 14.0)
            value += WEIGHT_WATER * water
            because.append("on the river" if carried > 0.05 else "beside fresh water")

        near = reach[j][i]
        if near <= CROSSING_REACH:
            close = 1.0 - near / (CROSSING_REACH + 1.0)
            kind = kind_at[j][i]
            # A harbour is only a harbour in a country the writer describes as reaching
            # the sea. The continent is shaped from the seed now, so it can perfectly
            # well put a sheltered bay on the edge of a march described as a river
            # plain — and when it does, the writer's sentence wins. Their vale does not
            # acquire a port because the lattice put water near it (§66). That the water
            # is there is worth telling them, and the map does, as a note rather than by
            # quietly founding a seaport.
            if kind == "harbour" and seafaring is not None:
                where = region_of[j][i] if region_of else None
                if where not in seafaring:
                    kind = ""
            # Weighted by how good a crossing it is as well as how near. A ford anyone
            # can wade is worth stopping at; a marginal one on a brook is not, and
            # counting the two the same is how a map ends up with a town at every
            # puddle the lattice happened to call a ford.
            if kind == "harbour":
                ports.add((i, j))
            if kind:
                value += (WEIGHT_CROSSING.get(kind, 1.5) * close
                          * (0.45 + 0.55 * strength_at[j][i]))
                because.append(
                    _CROSSING_WHY.get(kind, "where the ground lets people through"))

        standing = _stands_above(grid, elevation, i, j, sea)
        if standing > 0.02:
            value += WEIGHT_DEFENCE * min(1.0, standing / 0.10)
            because.append("standing above the ground around it")

        value += WEIGHT_REACH * min(1.0, cheapest / movement.cost[j][i])

        # And what the country round it is good for besides grain, said in the writer's
        # own words where they gave any. A town beside the only iron on the map is there
        # for the iron, and being told so is most of what an explanation is for.
        where = region_of[j][i] if region_of else None
        spoken = words.get(where, {}) if where else {}
        kind, most = _best_nearby(within, within_claimed, i, j, prefer=set(spoken))
        if kind and kind != "arable" and most >= (
                WORTH_CLAIMED if kind in spoken else WORTH_MENTIONING):
            said = spoken.get(kind)
            because.append(f"{said} close at hand" if said
                           else _RESOURCE_WHY.get(kind, f"{kind} nearby"))
            value += WEIGHT_RESOURCE * most

        value -= MARSH_REFUSAL * marsh[j][i]
        value -= STEEP_REFUSAL * min(1.0, slope[j][i] / 0.16)
        above = elevation[j][i] - sea_level - HIGH_FROM
        if above > 0.0:
            value -= HIGH_REFUSAL * min(1.0, above / 0.35)

        # A stable nudge, so equally good ground does not tie forever and towns do not
        # come out on a lattice.
        value += noise.unit(f"{seed}|site", i, j) * 0.22
        score[j][i] = value
        if because:
            why[(i, j)] = because
    return score, why, ports


_CROSSING_WHY = {
    "ford": "at the ford",
    "pass": "below the pass",
    "harbour": "with a sheltered harbour",
}

# How each resource reads in a sentence about why a town is where it is. The writer's
# own word is used instead wherever they gave one — somebody who wrote "iron" should be
# told their town is near the iron, not near the ore.
_RESOURCE_WHY = {
    "arable": "good country to feed it",
    "pasture": "grazing round about",
    "timber": "timber close at hand",
    "stone": "stone to build with",
    "ore": "ore in the hills nearby",
    "fish": "fishing off the shore",
}

# How much of a resource has to be at hand before it is worth naming as a reason, and
# the lower bar for one the writer has named themselves. Their word is the interesting
# fact: a march they described for its iron should be told its town is by the iron, even
# though ore is scarce by construction and there is always more timber than metal.
WORTH_MENTIONING = 0.45
WORTH_CLAIMED = 0.10

# Which named resources are worth putting a settlement at, rather than merely worth
# mentioning about one that was going there anyway. Ore, stone and timber are got where
# they are and carried away; grain and grazing are got where people already live, and
# fish is what a harbour is for.
DRAWS_PEOPLE = ("ore", "stone", "timber")


def _within_reach(grid: Grid, resources: Resources, sea: list[list[bool]],
                  reach: int, only: set[str] | None = None) -> dict[str, Field]:
    """The most of each resource within a town's reach of every cell.

    A town's resources are its hinterland's, not its own acre's. Nobody builds *on* the
    seam — the mines are up the valley and the town is at the bottom of it, which is why
    an ore field and a settlement field never overlap and why looking only at the cell
    and its neighbours found a march the writer had named for its iron and explained
    every one of its towns by the fishing.

    Computed as two one-dimensional sweeps per resource rather than a window per cell:
    the largest value in a square is the largest of the largest in each row of it, so a
    radius that would otherwise cost eighty-one lookups a cell costs two.
    """
    size = grid.size
    out: dict[str, Field] = {}
    for kind in RESOURCE_KINDS:
        if only is not None and kind not in only:
            continue
        field_of = resources.fields.get(kind)
        if field_of is None:
            continue
        across = grid.filled(0.0)
        for j in range(size):
            row = field_of[j]
            for i in range(size):
                lo, hi = max(0, i - reach), min(size, i + reach + 1)
                across[j][i] = max(row[lo:hi])
        # Transposed so the second sweep is a slice of a list, like the first. Reading
        # down a column of rows instead builds a generator per cell — two and a half
        # million of them over the six resources, which measured as one of the costliest
        # things in the whole stage.
        columns = [[across[j][i] for j in range(size)] for i in range(size)]
        down = grid.filled(0.0)
        for i in range(size):
            column = columns[i]
            for j in range(size):
                lo, hi = max(0, j - reach), min(size, j + reach + 1)
                down[j][i] = max(column[lo:hi])
        out[kind] = down
    return out


def _best_nearby(within: dict[str, Field], within_claimed: dict[str, Field],
                 i: int, j: int, prefer: set[str]) -> tuple[str, float]:
    """The most worth mentioning within a town's reach, and how much of it.

    Anything the writer named for this region is looked for first. Taking whichever
    field happens to be largest instead sounds neutral and is not: ore is scarce by
    construction and timber is not, so a march described for its mines had every one of
    its towns explained by the woods.
    """
    best, most = "", 0.0
    spoken, spoken_most = "", 0.0
    for kind, field_of in sorted(within.items()):
        value = field_of[j][i]
        if value > most:
            best, most = kind, value
    for kind in sorted(prefer):
        field_of = within_claimed.get(kind)
        if field_of is None:
            continue
        value = field_of[j][i]
        if value > spoken_most:
            spoken, spoken_most = kind, value
    return (spoken, spoken_most) if spoken else (best, most)


def _nearest_crossing(grid: Grid, at_crossing: dict[tuple[int, int], Crossing],
                      sea: list[list[bool]]
                      ) -> tuple[Field, list[list[str]], Field]:
    """Distance to the nearest crossing, what kind it was, and how good a one.

    One sweep carries the crossing's index out across the whole lattice, and the index
    is then read back for the other two. Carrying the index rather than the values means
    a cell cannot end up with one crossing's kind and another's strength.
    """
    size = grid.size
    kinds = sorted(at_crossing.items())
    if not kinds:
        return grid.filled(math.inf), [[""] * size for _ in range(size)], grid.filled(0.0)
    order = {cell: n for n, (cell, _) in enumerate(kinds)}
    reach, carried = grid.nearest_from(
        [(cell, float(order[cell] + 1)) for cell, _ in kinds])
    labels = [[""] * size for _ in range(size)]
    strength = grid.filled(0.0)
    for j in range(size):
        for i in range(size):
            index = int(carried[j][i]) - 1
            if 0 <= index < len(kinds):
                labels[j][i] = kinds[index][1].kind
                strength[j][i] = kinds[index][1].strength
    return reach, labels, strength


def _stands_above(grid: Grid, elevation: Field, i: int, j: int,
                  sea: list[list[bool]]) -> float:
    """How far a cell rises over the ground within a short walk of it."""
    size = grid.size
    here = elevation[j][i]
    lowest = here
    for b in range(max(0, j - 3), min(size, j + 4)):
        for a in range(max(0, i - 3), min(size, i + 4)):
            if not sea[b][a] and elevation[b][a] < lowest:
                lowest = elevation[b][a]
    return here - lowest


# ---- choosing, and then sizing ----------------------------------------------


def _choose(grid: Grid, *, score: Field, provision: Field, sea: list[list[bool]],
            wanted: int, fixed: dict[tuple[int, int], str],
            room: dict[str, int] | None = None,
            region_of: list[list[str]] | None = None,
            ports: set[tuple[int, int]] | None = None,
            seafaring: set[str] | None = None,
            workings: dict[str, set[tuple[int, int]]] | None = None
            ) -> list[tuple[tuple[int, int], str, str | None]]:
    """The best places at each scale, with the writer's own kept whatever they score.

    Worked from the top down. A city is chosen from anywhere; a town from what is left
    once the cities have their room; and so on. A settlement's tier is therefore decided
    by how much country it has to itself, which is the thing that actually makes a city a
    city — not by a score threshold, and not by a population the writer typed.
    """
    size = grid.size
    taken: list[tuple[int, int]] = []
    out: list[tuple[tuple[int, int], str, str | None]] = []

    ranked = sorted(
        ((score[j][i], i, j) for j in range(size) for i in range(size)
         if not sea[j][i] and (i, j) not in fixed),
        key=lambda c: (-c[0], c[2], c[1]))

    # How many more each region can hold, if the caller said. Without this the map picks
    # the best sites on the continent, which are all in the one good vale, and a writer
    # asking about their northern march is told nothing about it. The writer's own
    # settlements count against their region's room, because they are settlements.
    #
    # The rooms have to add up to `wanted`. The tiers below are shares of that number and
    # the rooms are what may actually be spent, so rooms that fall short of it silently
    # cut every tier — the top ones first, since they are chosen when the rooms are still
    # full of the writer's own towns. That is how a map came out with nine towns, two
    # hamlets, and no cities or villages at all.
    left = dict(room) if room else None

    def belongs(cell: tuple[int, int]) -> str | None:
        if region_of is None:
            return None
        return region_of[cell[1]][cell[0]] or None

    # The writer's places first and unconditionally. They are not candidates, and their
    # tier is settled below by the same rule as everyone else's.
    for cell in sorted(fixed):
        out.append((cell, "", fixed[cell]))
        taken.append(cell)
        where = belongs(cell)
        if left is not None and where in left:
            left[where] -= 1

    # A country the writer describes as reaching the sea gets a port, before anything
    # else is placed. Left to the general scoring it may not: on this continent the best
    # ground in the Salt Reach turned out to be two river fords well inland, so a march
    # whose name is Salt and whose description is the coast came out with no harbour on
    # it at all. Their sentence is the fact; the map's business is where the port goes,
    # not whether there is one.
    if seafaring and ports and region_of is not None:
        for where in sorted(seafaring):
            if left is not None and left.get(where, 0) <= 0:
                continue
            best = None
            for _value, i, j in ranked:
                if (i, j) not in ports or belongs((i, j)) != where:
                    continue
                if any(max(abs(i - a), abs(j - b)) < TIERS[-1][1] for a, b in taken):
                    continue
                best = (i, j)
                break
            if best is None:
                continue
            out.append((best, "", None))
            taken.append(best)
            if left is not None and where in left:
                left[where] -= 1

    # And the same for a country the writer named for what comes out of its rock. The
    # Iron Spine's ore is up in the crags and its liveable ground is twenty cells away on
    # the coast, so the general scoring gave it three hamlets, every one of them fishing,
    # in a march whose name is Iron. Nobody farms a mountain: the reason there is a town
    # up there at all is the seam, and a map that cannot say so has dropped the one fact
    # the writer gave it about the place.
    for where in sorted(workings or {}):
        if left is not None and left.get(where, 0) <= 0:
            continue
        seam = workings[where]
        if any(cell in seam for cell in taken):
            continue                       # somebody is already at the workings
        best = None
        for _value, i, j in ranked:
            if (i, j) not in seam:
                continue
            if any(max(abs(i - a), abs(j - b)) < TIERS[-1][1] for a, b in taken):
                continue
            best = (i, j)
            break
        if best is None:
            continue
        out.append((best, "", None))
        taken.append(best)
        if left is not None and where in left:
            left[where] -= 1

    for name, spacing, floor, share in TIERS:
        quota = int(wanted * share)
        here = 0
        for _value, i, j in ranked:
            # The budget counts the writer's own settlements. They are settlements: a
            # world with six named towns and a budget of twenty wants fourteen more, not
            # twenty more, and the difference is the difference between a proposal and a
            # chore.
            if here >= quota or len(out) >= wanted:
                break
            if (i, j) in {cell for cell, _, _ in out}:
                continue
            if provision[j][i] < floor:
                continue
            if any(max(abs(i - a), abs(j - b)) < spacing for a, b in taken):
                continue
            where = belongs((i, j))
            if left is not None and where is not None and left.get(where, 0) <= 0:
                continue
            out.append(((i, j), name, None))
            taken.append((i, j))
            here += 1
            if left is not None and where in left:
                left[where] -= 1

    # The writer's places take the tier their own country earns them, decided by which
    # spacing they would have been chosen at had the map been placing them.
    settled: list[tuple[tuple[int, int], str, str | None]] = []
    others = [cell for cell, _, _ in out]
    for cell, name, entity_id in out:
        if name:
            settled.append((cell, name, entity_id))
            continue
        settled.append((cell, _tier_for(cell, others, provision), entity_id))
    return settled


def _tier_for(cell: tuple[int, int], others: list[tuple[int, int]],
              provision: Field) -> str:
    """The tier a place earns from its elbow room and the country round it.

    Used for the writer's own settlements, which are placed before anything is scored and
    so cannot be assigned a tier by the choosing. Measuring how far the nearest other
    settlement is asks the same question the choosing asks, in the other direction.
    """
    i, j = cell
    nearest = min((max(abs(i - a), abs(j - b)) for a, b in others if (a, b) != cell),
                  default=999)
    for name, spacing, floor, _share in TIERS:
        if nearest >= spacing and provision[j][i] >= floor:
            return name
    return TIERS[-1][0]


def _markets(grid: Grid, cells: list[tuple[int, int]], *, movement: Movement,
             sea: list[list[bool]]) -> list[list[int]]:
    """Which settlement each acre is cheapest to reach — a market area.

    Grown as one search from every settlement at once, over the travel cost rather than
    over distance, so a market area stops at a range and runs a long way up a valley.
    That is what a hinterland is, and it is why a town on the wrong side of a mountain
    from good country does not get to eat it.
    """
    size = grid.size
    owner = [[-1] * size for _ in range(size)]
    spent = [[math.inf] * size for _ in range(size)]
    heap: list[tuple[float, int, int, int]] = []
    for index, (i, j) in enumerate(cells):
        if not sea[j][i]:
            spent[j][i] = 0.0
            heapq.heappush(heap, (0.0, index, i, j))
    while heap:
        paid, index, i, j = heapq.heappop(heap)
        if owner[j][i] != -1:
            continue
        owner[j][i] = index
        for ni, nj in grid.neighbours(i, j, diagonal=False):
            if sea[nj][ni] or owner[nj][ni] != -1:
                continue
            step = paid + movement.cost[nj][ni]
            if step < spent[nj][ni]:
                spent[nj][ni] = step
                heapq.heappush(heap, (step, index, ni, nj))
    return owner


def _support(grid: Grid, *, market: list[list[int]], provision: Field,
             sea: list[list[bool]], count: int) -> list[float]:
    """What each market area can feed.

    The provision field is already a neighbourhood total, so summing it over a market
    area counts every acre several times — which is fine and deliberate, because what is
    being compared is areas against each other and the double counting is even. Dividing
    it back out would be arithmetic for its own sake.
    """
    out = [0.0] * count
    size = grid.size
    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                continue
            index = market[j][i]
            if 0 <= index < count:
                out[index] += provision[j][i]
    scale = float(HINTERLAND * 2 + 1) ** 2
    return [value / scale for value in out]



