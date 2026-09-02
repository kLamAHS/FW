"""Computing a whole map without writing any of it.

`plan_map` is the pure half of generation: it reads the world, works out a map, and
returns it. Nothing here touches the database — which is the whole point, because it is
what lets the writer look at a map before it exists, argue with it, and throw it away at
no cost.

At this stage the geography still comes from the first generator: same land, same rivers,
same towns in the same places. What has changed is the shape of the operation. Every
stage now emits `FeatureDraft`s instead of rows, one assembler turns those into a plan
with stable ids and rendered explanations, and `apply.py` is the only thing that writes.
The stages themselves are replaced one at a time after this, each behind the same seam.
"""

from __future__ import annotations

import heapq
import math
import time

from fw.core.mapgen import cartography, diagnose, importance, shapes
from fw.core.mapgen import ledger as ledger_module
from fw.core.mapgen.drafts import (
    FactSpec,
    FeatureDraft,
    NameRequest,
    Reason,
    SegmentSpec,
    ShapeSpec,
    SubjectSpec,
)
from fw.core.mapgen.features import NAME_HINT as _FEATURE_HINT
from fw.core.mapgen.findings import Finding, note, warn
from fw.core.mapgen.ids import feature_id, kind_of, name_key
from fw.core.mapgen.names import Namer
from fw.core.mapgen.plan import (
    GENERATED_TAG,
    MapBrief,
    MapPlan,
    PlannedFeature,
    PlanStats,
    Retirement,
    Terrain,
    digest_of,
    order_features,
)
from fw.core.mapgen.source.reading import WorldReading, key_for
from fw.core.world import World, WorldError


def plan_map(world: World, brief: MapBrief | None = None) -> MapPlan:
    """Work out a map for this world. Writes nothing."""
    brief = brief or MapBrief()
    started = time.perf_counter()
    findings: list[Finding] = []

    if world.branch_name != "canon":
        # Geometry has no branch overlay, so a what-if that generated a map would draw
        # canon's coastline and its own on top of each other, and could delete neither.
        # Saying so is better than drawing that.
        return _empty(world, brief, [warn(
            "inherited-branch",
            "a map can only be drawn on the main timeline — this what-if inherits the "
            "map from canon, and cannot redraw it")])

    drafts, stage_ms, stage_findings, terrain, reading = _compute(world, brief)
    findings.extend(stage_findings)
    if not drafts:
        findings.append(note(
            "unplaced",
            "there are no regions yet — a map grows from regions, so name a few and "
            "say what they are like"))
        return _empty(world, brief, findings)

    features = _assemble(world, brief, drafts, reading)
    retiring = _retirements(world, brief, features)
    stats = PlanStats(
        features_by_kind=_counts(features),
        vertices=sum(_vertices(s.coordinates) for f in features for s in f.shapes),
        new_entities=sum(1 for f in features if f.invented),
        facts=sum(len(f.facts) for f in features),
        segments=sum(len(f.segments) for f in features),
        stage_ms=stage_ms,
        plan_ms=int((time.perf_counter() - started) * 1000),
    )
    return MapPlan(
        plan_id=digest_of(brief, features),
        # Declared since the plan existed and always empty until now. It is what lets a
        # stored plan say "this is still the plan for this world" without comparing
        # entity ids, which differ between two copies of the same world.
        reading_fingerprint=reading.fingerprint() if reading else "",
        world_name=world.name,
        branch=world.branch_name,
        brief=brief,
        features=features,
        retiring=retiring,
        terrain=terrain,
        stats=stats,
        findings=tuple(findings),
    )


# ---- the compute, still the first generator's ------------------------------

def _compute(world: World, brief: MapBrief
             ) -> tuple[list[FeatureDraft], dict[str, int], list[Finding],
                        Terrain | None, WorldReading | None]:
    """Run the geography and collect drafts. No writes."""
    from fw.core.mapgen.generate import MapGenerator

    findings: list[Finding] = []
    timings: dict[str, int] = {}
    generator = MapGenerator(world, seed=brief.seed or None, at=brief.at)

    regions = generator.regions_of_the_world()
    if not regions:
        return [], timings, findings, None, None

    mark = time.perf_counter()
    # One reading, and the profiles come out of it. Planning used to build its own
    # profiles here with `profile_region` while `generate` built them from the reading,
    # which is two answers to the same question — and the one thing a propose-then-accept
    # split cannot survive is the proposal and the apply disagreeing about the world.
    generator.read_the_world()
    authored, rivers = generator.build_the_world()
    placements = generator._site_settlements(propose=brief.invent_settlements)
    timings["geography"] = int((time.perf_counter() - mark) * 1000)

    # What the later stages noticed and could not quietly resolve. A march described for
    # its fisheries with no coast, a city standing on ground that would feed a hamlet:
    # each of these is either something the writer knows and has a reason for, or
    # something they had not thought about, and both are worth one sentence.
    # What the reading itself noticed in their prose — a port in a landlocked march, a
    # town four houses hold at once, a battle at a place founded after it.
    if generator.reading is not None:
        findings.extend(generator.reading.findings)
    if generator.resources is not None:
        findings.extend(generator.resources.notes)
    if generator.vegetation is not None:
        findings.extend(generator.vegetation.notes)
    findings.extend(generator.settlement_findings())
    findings.extend(generator.frontier_findings())

    mark = time.perf_counter()
    drafts: list[FeatureDraft] = []
    if brief.wants("coast"):
        drafts.extend(_coast_drafts(generator))
    if brief.wants("region"):
        drafts.extend(_region_drafts(generator, authored))
    if brief.wants("range"):
        drafts.extend(_range_drafts(generator))
    if brief.wants("natural"):
        drafts.extend(_feature_drafts(generator))
    if brief.wants("river"):
        drafts.extend(_river_drafts(generator, rivers))
    if brief.wants("settlement"):
        drafts.extend(_settlement_drafts(generator, placements))
    if brief.wants("road"):
        drafts.extend(_road_drafts(generator, placements))
    if brief.wants("castle"):
        drafts.extend(_castle_drafts(generator, placements))
    if brief.wants("road") and brief.wants("coast"):
        drafts.extend(_sea_lane_drafts(generator, placements))
    # Every draft leaves knowing how much it matters (V2 §31). Graded here, after all
    # drafting, so a stage cannot forget it and nothing downstream has to guess a
    # hierarchy back out of icon sizes.
    importance.grade(drafts, generator.reading)
    # What a reader would question, asked before a reader can (V2 §44). After all
    # drafting, because the questions are about the finished proposal — a town no
    # road reaches is only knowable once the roads are drafted.
    findings.extend(diagnose.study(generator, drafts,
                                   _known_places(generator, placements)))
    timings["drafting"] = int((time.perf_counter() - mark) * 1000)
    return (drafts, timings, findings, _terrain_of(generator, placements),
            generator.reading)


def _terrain_of(generator, placements) -> Terrain | None:
    """The surface this plan was worked out on, to be kept if it is accepted.

    Height is what the relief is lit from; cover and standing water are what the
    ground is coloured by; the flow is what makes the valleys legible — the renderer
    carves the drainage into the picture, and a saved world whose map cannot show its
    own valleys is a world that has forgotten why they are there. The develop field is
    the human ground the same way (V2 §14): how worked each acre is, kept beside the
    physical fields so farmland, track culling and label budgets all read one answer.
    Everything else the generator held — temperature, rock hardness — is recoverable
    or was scaffolding, and a world file is a thing a writer keeps for years.
    """
    from fw.core.mapgen import density
    from fw.core.mapgen.generate import GRID, SEA_LEVEL

    if not generator.elevation or generator.vegetation is None:
        return None
    grid = generator._grid()

    known = _known_places(generator, placements)
    network = (generator.road_network(known) if len(known) >= 2 else None)
    seats = []
    for place in sorted(known, key=lambda p: (p.name, p.x, p.y)):
        told = (_founding(generator, place.entity_id)
                if place.entity_id else {})
        founded = told.get("founded")
        age = (generator.at - founded
               if generator.at is not None and founded is not None else None)
        seats.append((generator._cell_of(place.x, place.y),
                      density.WORKED.get(place.rank.lower(),
                                         density.JUST_A_PLACE),
                      density.grown(age)))

    return Terrain(
        seed=generator.seed, size=GRID, span=grid.span,
        origin_x=grid.origin_x, origin_y=grid.origin_y, sea_level=SEA_LEVEL,
        fields={
            "elevation": generator.elevation,
            "canopy": generator.vegetation.canopy,
            "marsh": generator.vegetation.marsh,
            # Stored as the square root — the discharge exponent erosion itself
            # uses. Raw flow spans four orders of magnitude, which quantised to
            # sixteen bits loses exactly the small channels; in root space the
            # store's precision survives the trip at every size of stream.
            "flow": [[math.sqrt(value) for value in row]
                     for row in generator.erosion.flow],
            # Integer class codes (shore.CODE), spread a few cells into the sea so
            # a shallow pixel knows whose water it is. Integers survive the
            # sixteen-bit trip exactly; readers round.
            "shoreline": generator.shore.seaward,
            "develop": density.develop(
                GRID, generator.sea, seats,
                traffic=network.traffic if network else None),
        })


def _coast_drafts(generator) -> list[FeatureDraft]:
    """The land itself, as a shape.

    Without it the map is a set of region polygons and the sea shows through every gap
    between them — and there is nothing for the biomes and relief to be painted on.
    The mainland and each island are separate features so a writer can reject an island
    they did not ask for without losing the continent.
    """
    out: list[FeatureDraft] = []
    form = generator.landform
    if form is None:
        return out
    shores = list(form.coastlines())
    waters = list(form.inland_waters())
    if not shores:
        return out
    grid = generator._grid()
    for ordinal, ring in enumerate(shores):
        points = shapes.closed(grid.to_world(ring))
        area = shapes.area(ring)
        mainland = ordinal == 0
        runs = _shore_runs(generator, points)
        out.append(FeatureDraft(
            kind="coast" if mainland else "island",
            key_parts=("landmass", ordinal),
            # A terrain feature, not a region. Made a region, the landmass joins the
            # world's regions on the next run and the whole map is laid out around its
            # own coastline — the generator reading its own output back as source.
            subject=SubjectSpec(mode="new", type_key="terrain_feature",
                                tags=(GENERATED_TAG,),
                                summary_template="The land itself, as the map found it."),
            shapes=(
                (ShapeSpec(role="outline", kind="polygon", coordinates=[points],
                           layer="land", style={"role": "land"}, approximate=True),)
                # The coast in its own ink, drawn as a set of runs rather than one
                # even stroke (V2 §4): a cliff coast is a hard dark line, a marsh
                # coast is broken and soft, a beach is light. The land polygon still
                # carries the fill; these carry the character, which the shore
                # classifier has known since Phase B and nothing has drawn.
                + tuple(ShapeSpec(role="shore", kind="line", coordinates=run,
                                  layer="land",
                                  style={"role": "coastline", "shore": kind,
                                         "stroke-width": SHORE_INK[kind][0],
                                         **({"dash": True} if SHORE_INK[kind][1]
                                            else {})},
                                  approximate=True)
                        for kind, run in _shore_lines(points, runs))
                # The inland waters belong to the mainland: they are holes in it, and
                # drawing them anywhere else would leave lakes floating in the sea.
                + (tuple(ShapeSpec(role="hole", kind="polygon",
                                   coordinates=[shapes.closed(grid.to_world(hole))],
                                   layer="waters", style={"role": "water"},
                                   approximate=True)
                         for hole in waters) if mainland else ())
            ),
            reasons=(Reason(
                kind="authored", weight=1.0,
                template=("the shape your regions and their borders make"
                          if mainland else "ground the sea cut off from the mainland")),),
            fixed_name=generator.world.name if mainland else "",
            name_request=None if mainland else NameRequest(
                key=name_key("island", ("landmass",), ordinal),
                kind="region", hint="coast"),
            detail={"landmass": ordinal, "area": round(area, 1),
                    "shore": runs},
        ))
    return out


# How each coast class is inked: stroke width, and whether the line is broken.
# A cliff is where the land ends abruptly and the line says so; a marsh coast is a
# argument between land and water and is drawn as one; a beach barely commits.
SHORE_INK: dict[str, tuple[float, bool]] = {
    "cliff": (2.4, False),
    "fjord": (2.2, False),
    "rocky": (1.7, False),
    "delta": (1.0, True),
    "estuary": (1.2, False),
    "marsh": (1.0, True),
    "sheltered": (1.3, False),
    "beach": (1.1, False),
    "open": (1.3, False),
}


def _shore_lines(ring: list, runs: list[list]) -> list[tuple[str, list]]:
    """The ring cut into the runs the classifier found, each as its own line.

    Consecutive runs share their boundary vertex, so the strokes meet exactly
    rather than leaving a gap of paper between two characters of coast.
    """
    out: list[tuple[str, list]] = []
    at = 0
    for kind, count in runs:
        if kind not in SHORE_INK:
            at += int(count)
            continue
        end = min(len(ring) - 1, at + int(count))
        if end - at >= 1:
            out.append((kind, [list(p) for p in ring[at:end + 1]]))
        at = end
        if at >= len(ring) - 1:
            break
    return out


def _shore_runs(generator, ring) -> list[list]:
    """The coast's character along this ring, run-length encoded (V2 §4).

    One `[class, vertices]` pair per stretch, aligned to the drawn ring's own
    vertices, so a renderer can vary the stroke without re-deriving the geography.
    """
    from fw.core.mapgen import shore as shore_module

    told = generator.shore
    if told is None or not ring:
        return []
    grid = generator._grid()
    codes: list[int] = []
    for x, y in ring:
        i, j = grid.cell_of(x, y)
        code = told.classes.get((i, j), 0)
        if code == 0:
            # The smoothed ring can stand a cell off the classified shore; take
            # the strongest voice within one cell rather than reporting silence.
            votes: dict[int, int] = {}
            for dj in (-1, 0, 1):
                for di in (-1, 0, 1):
                    near = told.classes.get((i + di, j + dj))
                    if near:
                        votes[near] = votes.get(near, 0) + 1
            code = (max(sorted(votes), key=lambda c: (votes[c], -c))
                    if votes else 0)
        codes.append(code)

    # The cell-level smoothing does not survive the mapping onto ring vertices —
    # neighbouring vertices straddle cells and flip — so the vote runs again in
    # vertex space, around the ring, until character comes in stretches. Held
    # mouth classes stay held here too.
    held = {shore_module.CODE["delta"], shore_module.CODE["estuary"]}
    count = len(codes)
    if count > 4:
        for _ in range(3):
            smoothed = list(codes)
            for n, code in enumerate(codes):
                if code in held:
                    continue
                votes = {}
                for step in (-2, -1, 0, 1, 2):
                    near = codes[(n + step) % count]
                    if near not in held:
                        votes[near] = votes.get(near, 0) + 1
                if votes:
                    smoothed[n] = max(sorted(votes), key=lambda c: (votes[c], -c))
            codes = smoothed

    runs: list[list] = []
    for code in codes:
        kind = shore_module.NAME.get(code, "open")
        if runs and runs[-1][0] == kind:
            runs[-1][1] += 1
        else:
            runs.append([kind, 1])
    return runs


def _region_drafts(generator, authored: dict) -> list[FeatureDraft]:
    from fw.core.mapgen.generate import LAYER_REGIONS, _terrain_role

    politics = generator.political()
    borders = _border_arcs(generator)
    out: list[FeatureDraft] = []
    for region_id in sorted(generator.profiles):
        profile = generator.profiles[region_id]
        # An authored region is drawn too, and this used to be where it was skipped:
        # "the writer drew it; it is not ours to redraw". That reading of §66 cost the
        # map its shape. `coast._hold` already argues the other one, about these very
        # provinces: a ring a writer drags round a country is a claim about WHERE THE
        # COUNTRY IS, not about where its coastline runs, and honouring it literally is
        # over-reading it. The terrain layer has always spread that claim into a swell
        # rather than tracing the pencil; the drawing layer traced the pencil, so the
        # seeded world came out as three quadrilaterals lying across a coast — which is
        # exactly what a reader called it.
        #
        # So the region is drawn as the territory its claim implies: the same traced
        # outline a generated region gets, shared with its neighbours and stroked once.
        # §66 is untouched, because nothing the writer drew is moved or rewritten — the
        # authored ring keeps its own geometry row and is what the editor shows. What
        # changes is only which of the two the MAP draws, and `/api/map` prefers this
        # one where it exists.
        ring = generator._outline(region_id)
        if ring is None:
            continue
        held = politics.get(region_id, {})
        name = profile.name
        edges = tuple(
            ShapeSpec(role="border", kind="line",
                      coordinates=[[round(x, 1), round(y, 1)] for x, y in points],
                      layer=LAYER_REGIONS,
                      style={"role": "border", "stroke-width": 1.2,
                             **({"dash": True} if kind == "surveyed" else {})},
                      approximate=True)
            for points, kind in borders.get(name, ()))
        out.append(FeatureDraft(
            kind="region",
            key_parts=(profile.name,),
            subject=SubjectSpec(mode="existing", type_key="region",
                                entity_id=region_id),
            shapes=(ShapeSpec(role="outline", kind="polygon", coordinates=[ring],
                              layer=LAYER_REGIONS,
                              # Two fills, not two shapes: a political map is the same
                              # country in a different colour, so the client switches
                              # mode rather than turning a second layer on over the
                              # first and getting the borders twice. Who holds it is
                              # semantics, not paint — it rides `detail["politics"]`;
                              # three style keys said the same thing here for a phase
                              # and were read by nothing.
                              # The polygon carries no edge of its own either: its
                              # border arcs are shared with its neighbours and stroked
                              # once each, below — a ring that stroked itself drew
                              # every frontier twice and the coastline three times.
                              style={"role": _terrain_role(profile.dominant),
                                     "edge": "none"},
                              approximate=True),
                    *edges),
            reasons=(Reason(kind="authored", weight=1.0,
                            template=("traced from the shape you drew, out to its own "
                                      "coast and in to its neighbours"
                                      if region_id in authored
                                      else "drawn where its neighbours leave room"),
                            evidence=profile.why("terrain")),
                     *_holding_reasons(held),
                     *_frontier_reasons(generator, profile.name)),
            detail={"share": round(len(_cells_of(generator, region_id))
                                   / max(1, generator.land_cells()), 3),
                    "dominant": profile.dominant,
                    "frontiers": _frontier_detail(generator, name),
                    **({"politics": held} if held else {})},
        ))
    return out


def _border_arcs(generator) -> dict[str, list[tuple[list, str]]]:
    """Each internal frontier arc, once, assigned to the one region that draws it.

    Coastal arcs — one side against the sea — are not here at all: the coastline wins,
    and a border re-stroked along it is the double line the V2 brief calls out. Arcs
    are classed by what the frontier they belong to runs along: "natural" ground reads
    solid, "surveyed" open country reads dashed, which is §92's approximation contract
    applied to politics.
    """
    from fw.core.mapgen import territory

    if generator.partition is None:
        return {}
    out: dict[str, list[tuple[list, str]]] = {}
    for left, right, points in territory.drawn_arcs(generator.partition,
                                                    generator.sea):
        if left is None or right is None or len(points) < 2:
            continue
        # Whichever side draws it, it is drawn once. This used to drop an arc whose
        # two sides were both authored, which on the seeded world — where all three
        # provinces are the writer's — meant every internal frontier went undrawn and
        # the map fell back to stroking the raw rings. Now that an authored region is
        # drafted like any other, both sides can draw, so the rule is simply: the
        # first side by name keeps it.
        keeper = left if left <= right else right
        out.setdefault(keeper, []).append(
            (points, _frontier_kind(generator, left, right)))
    return out


def _frontier_kind(generator, left: str, right: str) -> str:
    for frontier in generator.frontiers:
        if {left, right} == set(frontier.between):
            return ("natural" if frontier.runs_along != "open country"
                    else "surveyed")
    # Too short to have been measured (`SHORTEST_FRONTIER`): a corner, drawn as if
    # someone had to agree on it.
    return "surveyed"


def _frontier_detail(generator, name: str) -> list[dict]:
    """What this region's borders follow, as data beside the prose reasons."""
    return [{"with": (f.between[1] if f.between[0] == name else f.between[0]),
             "along": f.runs_along,
             "kind": ("natural" if f.runs_along != "open country"
                      else "surveyed"),
             "length": f.length}
            for f in generator.frontiers if name in f.between]


# How the map says who holds a place, one sentence per authority the writer named.
# Four separate facts (§11) and four separate sentences: "held by X" over ground that X
# owns in law but has not entered in thirty years is the map telling the writer
# something they did not write.
_AUTHORITY_WORDS = {
    "legally_owns": "{who} owns it in law",
    "administers": "{who} administers it",
    "occupies": "{who} has soldiers in it",
    "taxes": "{who} collects its taxes",
}


def _holding_reasons(held: dict) -> tuple[Reason, ...]:
    if not held:
        return ()
    out = [Reason(kind="seat", weight=0.9,
                  template=_AUTHORITY_WORDS[word].format(who=who))
           for word, who in sorted(held.get("under", {}).items())
           if who and word in _AUTHORITY_WORDS]
    for claimant in held.get("claimed_by", ()):
        out.append(Reason(kind="seat", weight=0.7,
                          template=f"{claimant} claims it, and does not hold it"))
    if held.get("title"):
        holder = held.get("title_holder") or ""
        out.append(Reason(
            kind="seat", weight=0.8,
            template=(f"the {held['title']} is {holder}" if holder
                      else f"the {held['title']} is vacant")))
    return tuple(out)


def _frontier_reasons(generator, name: str) -> tuple[Reason, ...]:
    """What lies between this country and each of its neighbours.

    The single most useful derived fact about a border and the one a coloured fill hides
    completely: whether the march with the next country climbs a ridge, follows a river,
    or crosses forty miles of wheat. The writer gets it on the region itself, where they
    will be reading about the place, rather than only as a warning when it is open.
    """
    out: list[Reason] = []
    for frontier in generator.frontiers:
        if name not in frontier.between:
            continue
        other = frontier.between[1] if frontier.between[0] == name else frontier.between[0]
        out.append(Reason(
            kind="crossing", weight=0.6,
            template=f"its border with {other} runs along {frontier.runs_along}",
            evidence=f"{frontier.length} cells of it"))
    return tuple(out[:3])


def _cells_of(generator, region_id: str) -> list[tuple[int, int]]:
    from fw.core.mapgen.generate import GRID
    return [(i, j) for j in range(GRID) for i in range(GRID)
            if generator.owner[j][i] == region_id and not generator.sea[j][i]]


# What a ship does compared with a cart on a made road: faster over distance, and less
# reliable, which the router models through quality and danger.
#
# Deliberately *not* closed for a season. Nobody sails in winter, and the map would like
# to say so — but it does not know which of a writer's seasons winter is. Naming one
# ("Darkening" is the example world's) closes the crossing on no day at all of any other
# world's calendar, and the store rightly refuses it. Closing a route is a fact about
# their world, and theirs to write (§66).
LANE_QUALITY = 0.9
LANE_DANGER = "moderate"
# How far inland a place may stand and still count as having a quay, in lattice cells.
# Eight is about fifty world units on a nine-hundred-unit map — the width of a coastal
# strip, not of a country. At three, the whole example continent had two quays on it and
# every crossing landed at the same village.
QUAY_REACH = 8
# What a cell of cart road costs against a cell of open water, when choosing where a
# crossing lands. Sailing is the easy part.
INLAND_COST = 6
# What a cell of sea costs by how far from shore it lies, in cells. A coaster works the
# shore; open water is a different proposition in any pre-modern world — so the cheapest
# way between two ports hugs the coast, and only a genuinely narrow crossing pays for
# blue water. The last band is everything beyond the table.
EXPOSURE_BANDS = ((2, 1), (5, 2), (9, 4))
OPEN_WATER_COST = 6
# Each port trades with its nearest few, not with everyone: n² lanes between every pair
# of harbours is a shipping map, and this is an atlas.
CABOTAGE_PARTNERS = 2


def _sea_lane_drafts(generator, placements) -> list[FeatureDraft]:
    """A shipping lane from each island to the nearest port, over water — and the
    coasting runs between the writer's own ports.

    Islands are travel orphans. `coast.SMALLEST_ISLAND` guarantees a map has some, the
    router works over segments, and nothing had ever drawn one to an island — so a
    writer who asks how long it takes to reach Renncape is told there is no way at all,
    of a place their own map put in the sea.

    "Nearest" is nearest *by sea*, found by walking the water outward from the island's
    own shore. A straight line to the nearest port as the gull flies is not a crossing:
    measured on the example world, every one of them ran overland, one of them for 56 of
    its 62 cells — a sea route across the middle of a continent, with a length nobody
    could sail and a line drawn over the mountains.
    """
    from fw.core.mapgen.generate import CELL, LAYER_ROADS

    form = generator.landform
    if form is None:
        return []
    seen = {p.entity_id for p in placements if p.entity_id}
    known = list(placements) + [p for p in generator._already_placed(placements)
                                if p.entity_id not in seen]
    if not known:
        return []

    out: list[FeatureDraft] = _cabotage_drafts(generator, known)
    shores = list(form.coastlines())
    if len(shores) < 2:
        return out

    grid = generator._grid()
    for ordinal, ring in enumerate(shores):
        if ordinal == 0:
            continue                                # the mainland needs no ferry
        edge = {grid.cell_of(x, y) for x, y in grid.to_world(ring)}
        came, far = _sail_from(generator, edge)
        if not came:
            continue
        harbour, landing = _nearest_by_sea(generator, known, far)
        if harbour is None or landing is None:
            continue
        path = _wake(generator, came, landing)
        if len(path) < 2:
            continue
        points = shapes.simplify(shapes.eased(path, rounds=2), CELL * 0.4)
        points = [[round(x, 1), round(y, 1)] for x, y in points]
        points[-1] = [round(harbour.x, 1), round(harbour.y, 1)]
        span = sum(math.dist(points[n], points[n + 1])
                   for n in range(len(points) - 1))
        needs = ((_settlement_key(generator, harbour),)
                 if harbour.entity_id is None else ())
        out.append(FeatureDraft(
            kind="lane",
            key_parts=("lane", ordinal, _endpoint_id(generator, harbour)),
            anchor_key=("landmass", ordinal),
            shapes=(ShapeSpec(role="segment", kind="line", coordinates=points,
                              layer=LAYER_ROADS,
                              style={"role": "waterway", "dash": True,
                                     "stroke-width": 1.8},
                              approximate=True),),
            segments=(SegmentSpec(
                from_ref="@" + feature_id("island", "landmass", ordinal),
                to_ref=_ref_for(generator, harbour),
                length=round(span, 1), medium="sea", quality=LANE_QUALITY,
                # The ground a ship crosses is water. The router's water profiles score
                # terrain against `routing.WATER`, so a sea segment laid over "plain"
                # scores zero and is silently dropped — a crossing drawn on the map and
                # absent from every answer about travelling it.
                terrain="water", danger=LANE_DANGER),),
            depends_on_keys=(("landmass", ordinal), *needs),
            reasons=(Reason(kind="harbour", weight=1.0,
                            template=f"the shortest crossing to {harbour.name}",
                            evidence=f"{round(span)} of open water"),),
            # Named for where it lands, which is how a crossing is named. Not left to
            # the namer: a shipping lane is not a place, and inventing a word for it
            # would put a noun in the world that nobody wrote.
            name_template="The {0} crossing",
            name_refs=(feature_id("island", "landmass", ordinal),),
            name_request=None,
            default_accept=False,
            detail={"tier": "sea", "span": round(span, 1),
                    "lands_at": harbour.name},
        ))
    return out


def _cabotage_drafts(generator, known) -> list[FeatureDraft]:
    """The coasting runs between the writer's own ports (§11 of the V2 brief).

    Port-to-port, each to its nearest few by *priced* sea: every cell of water costs
    more the further from shore it lies, so the runs hug the coast the way a coaster
    actually would, and only a narrow strait gets crossed open. Both ends are places
    the writer ranked as ports themselves, so the lane explains their world rather
    than adding to it — which is why, unlike an invented island crossing, it is
    accepted by default.
    """
    from fw.core.mapgen.generate import CELL, LAYER_ROADS

    # Indexed by position in this name-sorted list everywhere below, never by name:
    # two ports the writer gave the same name would otherwise share one Dijkstra
    # field and one partner slot, and entity ids differ between twin worlds.
    ports = sorted((p for p in known if p.entity_id
                    and p.rank.lower() in ("port", "harbour", "harbor")),
                   key=lambda p: (p.name, p.x, p.y))
    if len(ports) < 2:
        return []
    offshore = _offshore(generator)
    sailed: dict[int, tuple[dict, dict]] = {}
    for n, port in enumerate(ports):
        seeds = _quay_seeds(generator, port)
        if seeds:
            sailed[n] = _coastwise(generator, seeds, offshore)

    chosen: dict[tuple[int, int], tuple] = {}
    for n, _port in enumerate(ports):
        if n not in sailed:
            continue
        _came, best = sailed[n]
        calls = []
        for m, other in enumerate(ports):
            if m == n or m not in sailed:
                continue
            cell = generator._cell_of(other.x, other.y)
            landings = [(best[step] + inland * INLAND_COST, step)
                        for step, inland in _quayside(generator, cell)
                        if step in best]
            if not landings:
                continue
            cost, landing = min(landings)
            calls.append((cost, m, other, landing))
        calls.sort(key=lambda call: (call[0], call[1]))
        for _cost, m, other, landing in calls[:CABOTAGE_PARTNERS]:
            pair = (n, m) if n < m else (m, n)
            if pair not in chosen:
                chosen[pair] = (n, other, landing)

    out: list[FeatureDraft] = []
    for pair in sorted(chosen):
        origin_index, target, landing = chosen[pair]
        origin = ports[origin_index]
        came, _best = sailed[origin_index]
        cells = [landing]
        while cells[-1] in came:
            cells.append(came[cells[-1]])
        cells.reverse()
        path = [generator._centre(i, j) for i, j in cells]
        points = shapes.simplify(shapes.eased(path, rounds=2), CELL * 0.4)
        points = [[round(x, 1), round(y, 1)] for x, y in points]
        if len(points) < 2:
            points = [[0.0, 0.0], [0.0, 0.0]]
        points[0] = [round(origin.x, 1), round(origin.y, 1)]
        points[-1] = [round(target.x, 1), round(target.y, 1)]
        span = sum(math.dist(points[n], points[n + 1])
                   for n in range(len(points) - 1))
        ends = tuple(sorted((_endpoint_id(generator, origin),
                             _endpoint_id(generator, target))))
        out.append(FeatureDraft(
            kind="lane",
            key_parts=("sail",) + ends,
            anchor_key=("landmass", 0),
            shapes=(ShapeSpec(role="segment", kind="line", coordinates=points,
                              layer=LAYER_ROADS,
                              style={"role": "waterway", "dash": True,
                                     "stroke-width": 1.8},
                              approximate=True),),
            segments=(SegmentSpec(
                from_ref=_ref_for(generator, origin),
                to_ref=_ref_for(generator, target),
                length=round(span, 1), medium="sea", quality=LANE_QUALITY,
                # `routing.WATER` again — see the island crossing's segment above
                # for why anything but "water" silently vanishes from every route.
                terrain="water", danger=LANE_DANGER),),
            depends_on_keys=(("landmass", 0),),
            reasons=(Reason(
                kind="harbour", weight=1.0,
                template=(f"the coasting trade between {origin.name} "
                          f"and {target.name}, both harbours by your own account"),
                evidence=f"{round(span)} along the shore"),),
            # Named for its ends, like the island crossing for where it lands — a
            # run is not a place, and no noun is invented for it.
            name_template="The {0}–{1} run",
            name_refs=ends,
            name_request=None,
            default_accept=True,
            detail={"tier": "coastal", "span": round(span, 1),
                    "lands_at": target.name,
                    "between": [origin.name, target.name]},
        ))
    return out


def _offshore(generator) -> dict:
    """How far from the nearest shore every sea cell lies, in cells."""
    from fw.core.mapgen.generate import GRID

    far: dict = {}
    for j in range(GRID):
        for i in range(GRID):
            if generator.sea[j][i] and any(
                    not generator.sea[nj][ni]
                    for ni, nj in ((i + di, j + dj)
                                   for dj in (-1, 0, 1) for di in (-1, 0, 1))
                    if 0 <= ni < GRID and 0 <= nj < GRID):
                far[(i, j)] = 0
    frontier = sorted(far)
    steps = 0
    while frontier:
        steps += 1
        nxt: list = []
        for here in frontier:
            for step in _sea_neighbours(generator, here):
                if step not in far:
                    far[step] = steps
                    nxt.append(step)
        frontier = sorted(nxt)
    return far


def _sail_cost(offshore_cells: int) -> int:
    for edge, cost in EXPOSURE_BANDS:
        if offshore_cells <= edge:
            return cost
    return OPEN_WATER_COST


def _quay_seeds(generator, place) -> dict:
    """Where this port's boats sit on the water, each priced by the walk to it."""
    cell = generator._cell_of(place.x, place.y)
    seeds: dict = {}
    for step, inland in _quayside(generator, cell):
        cost = inland * INLAND_COST
        if step not in seeds or cost < seeds[step]:
            seeds[step] = cost
    return seeds


def _coastwise(generator, seeds: dict, offshore: dict) -> tuple[dict, dict]:
    """Every sea cell reachable from these seeds, priced by exposure.

    Unlike `_sail_from`, which counts steps because its question is only *which* port,
    this one prices each cell by `_sail_cost` of its distance to shore. Integer costs
    and fully-ordered heap entries keep it deterministic; on equal cost the lower cell
    wins, every run.
    """
    best = dict(seeds)
    came: dict = {}
    queue = [(cost, cell) for cell, cost in sorted(seeds.items())]
    heapq.heapify(queue)
    while queue:
        cost, cell = heapq.heappop(queue)
        if cost > best.get(cell, cost):
            continue
        for step in _sea_neighbours(generator, cell):
            through = cost + _sail_cost(offshore.get(step, 0))
            if step not in best or through < best[step]:
                best[step] = through
                came[step] = cell
                heapq.heappush(queue, (through, step))
    return came, best


def _sail_from(generator, edge: set) -> tuple[dict, dict]:
    """Walk the water outward from one island's shore.

    Eight-connected breadth-first over sea cells only, which is what makes "nearest"
    mean nearest by sea. Diagonal steps cost the same as orthogonal ones — the answer
    wanted is which port, not how many leagues, and the length is measured from the
    drawn line afterwards.
    """
    frontier = [step for cell in sorted(edge)
                for step in _sea_neighbours(generator, cell)]
    came: dict = {}
    far: dict = {}
    for cell in frontier:
        far.setdefault(cell, 0)
    frontier = sorted(far)
    steps = 0
    while frontier:
        steps += 1
        nxt: list = []
        for here in frontier:
            for step in _sea_neighbours(generator, here):
                if step not in far:
                    far[step] = steps
                    came[step] = here
                    nxt.append(step)
        frontier = sorted(nxt)
    return came, far


def _sea_neighbours(generator, cell) -> list:
    from fw.core.mapgen.generate import GRID

    i, j = cell
    out = []
    for dj in (-1, 0, 1):
        for di in (-1, 0, 1):
            if di == dj == 0:
                continue
            ni, nj = i + di, j + dj
            if 0 <= ni < GRID and 0 <= nj < GRID and generator.sea[nj][ni]:
                out.append((ni, nj))
    return out


def _nearest_by_sea(generator, known, far: dict):
    """The port the water reaches soonest, and the sea cell it is reached from.

    Ports first and then anywhere: a ship puts in where there is a quay, and a lane
    that lands at an inland market town is a lane nobody could sail.
    """
    best = None
    for place in known:
        cell = generator._cell_of(place.x, place.y)
        touching = [(far[step] + inland * INLAND_COST, far[step], step)
                    for step, inland in _quayside(generator, cell) if step in far]
        if not touching:
            continue
        # A town a few cells from the water pays for the cart ride: a crossing that
        # lands eight cells inland is drawn as a sea lane running over the fields, and
        # counting the walk keeps a genuinely coastal town ahead of one that is merely
        # near.
        cost, _distance, landing = min(touching)
        rank = 0 if place.rank.lower() in ("port", "harbour", "harbor") else 1
        score = (rank, cost, place.name)
        if best is None or score < best[0]:
            best = (score, place, landing)
    return (best[1], best[2]) if best else (None, None)


def _quayside(generator, cell, reach: int = QUAY_REACH) -> list:
    """The water within reach of a place, with how far inland the place is from each.

    Not only the eight cells touching it. A port stands *at* the water and a lattice
    cell is a few miles across, so a harbour whose own cell happens to sit one square
    inland has no adjacent sea at all — and looking only at neighbours found exactly one
    quay on the whole example continent, so every crossing landed at the same village.
    """
    from fw.core.mapgen.generate import GRID

    i, j = cell
    out = []
    for dj in range(-reach, reach + 1):
        for di in range(-reach, reach + 1):
            ni, nj = i + di, j + dj
            if 0 <= ni < GRID and 0 <= nj < GRID and generator.sea[nj][ni]:
                out.append(((ni, nj), max(abs(di), abs(dj))))
    return out


def _wake(generator, came: dict, landing) -> list[tuple[float, float]]:
    """The path the walk came by, from the island's shore to the landing, in world units."""
    cells = [landing]
    while cells[-1] in came:
        cells.append(came[cells[-1]])
    cells.reverse()
    return [generator._centre(i, j) for i, j in cells]


def _closest(points, to) -> tuple[float, float]:
    best = min(points, key=lambda p: math.dist((p[0], p[1]), to))
    return (float(best[0]), float(best[1]))


def _range_drafts(generator) -> list[FeatureDraft]:
    """Each mountain range as the line it runs along.

    A spine rather than a filled shape: what the reader wants to know is where the
    range goes and which way, and the client draws its own ridge glyphs along the line.
    Filling it in would also mean the range and the region it is in fight over the same
    ground on a political map.
    """
    out: list[FeatureDraft] = []
    if generator.relief is None:
        return out
    for mountain in generator.relief.ranges:
        points = [[round(x, 1), round(y, 1)]
                  for x, y in (generator._centre(i, j) for i, j in mountain.spine)]
        if len(points) < 2:
            continue
        crest = mountain.spine[len(mountain.spine) // 2]
        out.append(FeatureDraft(
            kind="range",
            key_parts=(mountain.key, int(crest[0]), int(crest[1])),
            subject=SubjectSpec(mode="new", type_key="terrain_feature",
                                tags=(GENERATED_TAG,),
                                summary_template="The high ground of this country."),
            shapes=(ShapeSpec(role="spine", kind="line", coordinates=points,
                              layer="relief", style={"role": "ridge"},
                              approximate=True),),
            reasons=tuple(Reason(kind="authored", weight=1.0, template=text)
                          for text in mountain.because),
            name_request=NameRequest(
                key=name_key("range", (mountain.key,)), kind="region", hint="upland"),
            # The strike as a vector, not an angle. Turning it into degrees needs
            # atan2, which is a libm call and so is banned from anything that reaches
            # the plan's digest — and the client rotates a glyph just as easily from
            # two numbers as from one.
            detail={"strike": [round(mountain.strike.vector[0], 4),
                               round(mountain.strike.vector[1], 4)],
                    "crest": round(mountain.crest, 3),
                    "elongation": round(mountain.elongation, 2)},
        ))
    return out


# What a reader calls each stretch of country, and the colour it is drawn in.



def _feature_drafts(generator) -> list[FeatureDraft]:
    """The Wolfswood, the Neck, the Sheepshead Hills — as named polygons.

    A wood the writer has already named keeps their name and their entity; the map's
    job there is only to find the trees they were talking about and draw them.
    """
    out: list[FeatureDraft] = []
    if generator.features is None:
        return out
    for feature in generator.features.features:
        if not feature.rings:
            continue
        where = feature.region_keys[0] if feature.region_keys else "the map"
        out.append(FeatureDraft(
            kind="natural",
            key_parts=(feature.kind, where, feature.centre[0], feature.centre[1]),
            known_id=None,
            subject=(SubjectSpec(mode="existing", type_key="terrain_feature",
                                 entity_id=feature.entity_id)
                     if feature.entity_id else
                     SubjectSpec(mode="new", type_key="terrain_feature",
                                 tags=(GENERATED_TAG,),
                                 summary_template="Unbroken country, found by the map.")),
            shapes=tuple(
                ShapeSpec(role="fill", kind="polygon", coordinates=[ring],
                          layer="features",
                          style={"role": cartography.cover_role(feature.kind)},
                          approximate=True)
                for ring in feature.rings),
            facts=((FactSpec("feature_kind", value=feature.kind),)
                   if not feature.entity_id else ()),
            reasons=(Reason(kind="authored", weight=1.0, template=feature.because),),
            name_request=(None if feature.entity_id else NameRequest(
                key=name_key(feature.kind, (where,),
                             feature.centre[0] * 1000 + feature.centre[1]),
                kind="region",
                hint=_FEATURE_HINT.get(feature.kind, ""))),
            detail={"feature_kind": feature.kind, "area_cells": feature.area,
                    "regions": list(feature.region_keys)},
        ))
    return out


# The widths a river is drawn at, thinnest first, and the share of its own catchment at
# which it steps up to each. Five, because that is about as many as a reader can tell
# apart on a printed map, and because a river that is one width for its whole length is
# the single most obvious way a drawn map differs from a real one: the Trident is not the
# same river at Riverrun and at the Bay.
RIVER_WIDTHS = (1.2, 1.9, 2.8, 4.0, 5.6)
RIVER_STEPS = (0.0, 0.06, 0.16, 0.36, 0.66)


def _river_drafts(generator, rivers) -> list[FeatureDraft]:
    """Rivers as systems (V2 §6): a mainstem, its tributaries, and its lakes.

    The old tracer walked every source to the sea as its own river, so one real
    system arrived as a sheaf of overlapping strands and the `strahler` in its
    detail was a drawing width plus one. Now the hydrology's own graph is drawn:
    one trunk per mouth, each order-two tributary joined to it at its confluence,
    true stream order in the detail, and the meres the wet basins hold.
    """
    hyd = generator.hydrology
    if hyd is None:
        return []
    widest = max((s.discharge for s in hyd.systems), default=1.0) or 1.0

    out: list[FeatureDraft] = []
    for system in hyd.systems:
        trunk_key = (system.mouth[0], system.mouth[1],
                     system.mainstem[0][0], system.mainstem[0][1])
        trunk = _one_river(
            generator, list(system.mainstem), key_parts=trunk_key,
            widest=widest, order=system.order,
            mouth_kind=system.mouth_kind,
            reasons=(Reason(kind="mouth", weight=1.0,
                            template="runs from the high ground to the sea"),))
        if trunk is None:
            continue
        out.append(trunk)
        for arc in system.tributaries:
            junction, source = arc.cells[-1], arc.cells[0]
            tributary = _one_river(
                generator, list(arc.cells),
                key_parts=(junction[0], junction[1], source[0], source[1]),
                widest=widest, order=arc.order,
                mouth_kind="confluence",
                reasons=(Reason(kind="confluence", weight=0.9,
                                template="feeds {0} at their meeting",
                                refs=(feature_id("river", *trunk_key),)),))
            if tributary is not None:
                out.append(tributary)
    out.extend(_lake_drafts(generator, hyd))
    return out


def _one_river(generator, path, *, key_parts, widest, order,
               mouth_kind, reasons) -> FeatureDraft | None:
    from fw.core.mapgen.generate import LAYER_WATER

    mouth, source = path[-1], path[0]
    # A running maximum, not the flow as read. The flow field shares each cell's
    # water between all its downhill neighbours, which is what keeps the drainage
    # from snapping to eight compass directions — but it means the figure at a cell
    # on the traced course can be lower than the one just upstream of it. Water does
    # not leave a river, so the drawn width does not go back down.
    carried: list[float] = []
    so_far = 0.0
    for i, j in path:
        so_far = max(so_far, generator.flow[j][i])
        carried.append(so_far)
    biggest = carried[-1] or 1.0
    bands = [_band(value / widest) for value in carried]

    # One shape per run of equal width, each overlapping the next by a point so the
    # river is continuous where it steps. A course crossing a mere is drawn straight
    # through — the water is the same colour, and a split would break the invariant
    # that a river's reaches join.
    shapes: list[ShapeSpec] = []
    start = 0
    for n in range(1, len(path) + 1):
        if n < len(path) and bands[n] == bands[start]:
            continue
        run = path[start:min(n + 1, len(path))]
        if len(run) >= 2:
            shapes.append(ShapeSpec(
                # The first reach is the river's spine and the rest are segments of
                # it — roles are a closed vocabulary, like the finding codes.
                role="spine" if not shapes else "segment",
                kind="line",
                coordinates=generator._water_line(run),
                layer=LAYER_WATER,
                style={"role": "waterway",
                       "stroke-width": RIVER_WIDTHS[bands[start]]},
                approximate=True))
        start = n
    if not shapes:
        return None
    shapes.extend(_delta_arms(generator, path, mouth_kind, bands[-1]))

    return FeatureDraft(
        kind="river",
        key_parts=key_parts,
        subject=SubjectSpec(
            mode="new", type_key="waterway", tags=(GENERATED_TAG,),
            summary_template="Traced by the map; rename it and it is yours."),
        shapes=tuple(shapes),
        reasons=reasons,
        name_request=NameRequest(
            key=name_key("river", (str(mouth[0]), str(mouth[1]))),
            kind="waterway", hint="river"),
        detail={"mouth": list(mouth), "strahler": order,
                "discharge": round(biggest, 1),
                "source_elevation": round(
                    generator.elevation[source[1]][source[0]], 3),
                "mouth_kind": mouth_kind},
    )


def _delta_arms(generator, path, mouth_kind: str, band: int) -> list[ShapeSpec]:
    """A delta's distributaries: two short arms fanning seaward of the mouth.

    Drawn, not simulated — the classification already said the shelf is shallow and
    the sediment heavy; the arms are what that looks like. The fan is built from the
    river's own final direction and its perpendicular, so no angles anywhere.
    """
    from fw.core.mapgen.generate import LAYER_WATER

    if mouth_kind != "delta" or len(path) < 3:
        return []
    (ax, ay) = generator._centre(*path[-3])
    (bx, by) = generator._centre(*path[-1])
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy) or 1.0
    dx, dy = dx / length, dy / length
    px, py = -dy, dx                       # the perpendicular, for the spread
    reach = generator._grid().span / generator._grid().size * 1.8
    arms: list[ShapeSpec] = []
    for side in (-0.55, 0.55):
        tip = (bx + (dx + px * side) * reach, by + (dy + py * side) * reach)
        mid = (bx + (dx + px * side * 0.4) * reach * 0.5,
               by + (dy + py * side * 0.4) * reach * 0.5)
        arms.append(ShapeSpec(
            role="arm", kind="line",
            coordinates=[[round(bx, 1), round(by, 1)],
                         [round(mid[0], 1), round(mid[1], 1)],
                         [round(tip[0], 1), round(tip[1], 1)]],
            layer=LAYER_WATER,
            style={"role": "waterway",
                   "stroke-width": RIVER_WIDTHS[max(0, band - 2)]},
            approximate=True))
    return arms


def _lake_drafts(generator, hyd) -> list[FeatureDraft]:
    """The meres the wet basins hold, drawn the way the woods are.

    The same mask-to-rings walk as a natural feature, because a lake is one: the
    vegetation stage found the flat, wet, undrained ground; the deep core of a broad
    basin is open water rather than reeds.
    """
    from fw.core.mapgen import hydrology
    from fw.core.mapgen import shapes as shapes_module

    grid = generator._grid()
    out: list[FeatureDraft] = []
    for number, lake in enumerate(hyd.lakes):
        mask = grid.filled(0.0)
        for i, j in lake.cells:
            mask[j][i] = 1.0
        # `smallest` is in the field's own cell² space — a mere at the size floor
        # covers LAKE_CELLS of them, so the ring filter tracks the same dial.
        rings = [shapes_module.closed(grid.to_world(ring))
                 for ring, encloses in shapes_module.outlines(
                     grid.blurred(mask), 0.30,
                     smallest=hydrology.LAKE_CELLS * 0.6, most=120)
                 if encloses]
        if not rings:
            continue
        anchor = min(lake.cells)
        out.append(FeatureDraft(
            kind="lake",
            key_parts=(anchor[0], anchor[1]),
            subject=SubjectSpec(
                mode="new", type_key="waterway", tags=(GENERATED_TAG,),
                summary_template="Standing water the map found; rename it and "
                                 "it is yours."),
            shapes=tuple(ShapeSpec(role="outline", kind="polygon",
                                   coordinates=[ring], layer="waters",
                                   style={"role": "water"}, approximate=True)
                         for ring in rings),
            reasons=(Reason(kind="history", weight=0.8,
                            template="standing water in a basin the rivers "
                                     "cannot drain"),),
            name_request=NameRequest(
                key=name_key("lake", (str(anchor[0]), str(anchor[1]))),
                kind="waterway", hint="lake"),
            detail={"area": len(lake.cells),
                    "outlet": list(lake.outlet) if lake.outlet else None,
                    "number": number},
        ))
    return out


def _band(share: float) -> int:
    """Which drawn width a stretch of river falls in, by the share of its own catchment.

    Relative to the river rather than to the map, so a modest river still thickens along
    its length instead of being drawn hairline everywhere because a bigger one exists
    somewhere else. Discharge goes as catchment area, so the steps are spread wide at the
    bottom: most of a river's length carries very little of its water.
    """
    for band in range(len(RIVER_STEPS) - 1, -1, -1):
        if share >= RIVER_STEPS[band]:
            return band
    return 0


def _known_id(world: World, placement) -> str | None:
    """The id a previous run gave this place, if this run made it."""
    if not placement.entity_id:
        return None
    entity = world.get_entity(placement.entity_id)
    return ledger_module.feature_of(entity) if entity else None


def _settlement_key(generator, placement) -> tuple[str | int, ...]:
    """A settlement's identity: what it is, never a random entity id.

    Shared with the road drafts, so a road can say which towns it needs — and be
    dropped with them when the writer turns those towns down.
    """
    if placement.entity_id is None:
        region_name = generator.profiles[placement.region_id].name
        return ("n", region_name, int(placement.x), int(placement.y))
    return ("e", placement.name)


# What a kind of event did to the ground it happened on. §67 asks every feature for a
# sentence, and "the ford where the Red War was decided" is a better sentence than
# anything the map can derive from slope and traffic — it is the writer's own.
_EVENT_WORDS = {
    "battle": "{name} was fought here",
    "war": "{name} was decided here",
    "siege": "{name} was laid here",
    "treaty": "{name} was signed here",
    "coronation": "{name} was held here",
    "founding": "{name} — this is the place",
}


def _event_reasons(generator, entity_id: str | None) -> tuple[Reason, ...]:
    """What the writer said happened here, as the reason the place is on the map.

    `event.location_id` has been in the world since the first migration and no stage has
    ever read it. Six events in the example world name a place, and every one of them is
    a better account of why that place matters than a movement cost.
    """
    reading = getattr(generator, "reading", None)
    if reading is None or not entity_id:
        return ()
    key = reading.by_entity().get(entity_id)
    if key is None:
        return ()
    out: list[Reason] = []
    for event in reading.events_at(key):
        template = _EVENT_WORDS.get(event.kind, "{name} happened here")
        said = template.format(name=event.name)
        out.append(Reason(
            kind="history",
            # A battle here is the loudest thing that ever happened to a town, and a
            # treaty is the second; both outrank the market that put it here.
            weight=1.0 if event.destructive else 0.85,
            template=f"{said}, in {event.year_text}" if event.year_text else said,
            evidence=event.quote))
    # Newest first — the last thing that happened to a place is what it is known for.
    out.sort(key=lambda r: r.template, reverse=True)
    return tuple(out[:2])


def _no_older_than(generator, placement) -> int | None:
    """The earliest a proposed town can honestly be said to have stood.

    A map cannot work out when a town was founded — nothing in the ground says so. What
    it *can* say is that a town does not predate the country it stands in, and where the
    writer dated that country, that bound is a real answer and a better one than a null
    date on a map drawn for the year 240 (§66). Where they dated nothing it stays null,
    which the world reads as "it is just there" rather than as a claim.
    """
    reading = getattr(generator, "reading", None)
    if reading is None:
        return None
    profile = generator.profiles.get(placement.region_id)
    region = reading.region(key_for("region", profile.name)) if profile else None
    return region.founded if region else None


def _founding(generator, entity_id: str | None) -> dict:
    """When the writer said the town began, in their own calendar.

    `exists_from` has been on every settlement since the seed was written and no map
    has ever shown it. It is not a reason a place is where it is — a founding date
    explains nothing about geography — so it goes on the feature as a fact about it,
    which is what a reader hovering a town wants first.
    """
    reading = getattr(generator, "reading", None)
    if reading is None or not entity_id:
        return {}
    key = reading.by_entity().get(entity_id)
    place = reading.settlement(key) if key else None
    if place is None or place.founded is None:
        return {}
    out = {"founded": place.founded}
    if place.ended is not None:
        out["ended"] = place.ended
    return out


def _known_places(generator, placements) -> list:
    """Everyone the map knows — the one list roads, lanes and morphology share.

    Built identically at every call site on purpose: `road_network` caches on this
    list's coordinates, and two departments asking about different lists would lay
    the whole network twice and could disagree about who joins what.
    """
    seen = {p.entity_id for p in placements if p.entity_id}
    return list(placements) + [p for p in generator._already_placed(placements)
                               if p.entity_id not in seen]


def _settlement_semantics(generator, placement, known, network) -> dict:
    """What the siting knew about this place, said out loud (V2 §12).

    The crossing it stands on, how well its country feeds it, whether it is a port,
    and the shape a distant reader would see: a walled hold, a harbour town strung
    along its quay, a bridge town on its river, a street village on its road, or a
    plain clustered market.
    """
    told: dict = {}
    if placement.crossing:
        told["crossing"] = placement.crossing
    if placement.support:
        told["support"] = round(placement.support, 2)

    said = generator._said_of(placement.entity_id)
    port = (placement.crossing == "harbour"
            or placement.rank.lower() in ("port", "harbour", "harbor")
            or bool(said is not None and said.port.value))
    if port:
        told["port"] = True

    walled = (placement.rank.lower() in ("fortress", "citadel", "stronghold")
              or bool(said is not None and said.seat_of))
    on_road, heading = _street_of(generator, placement, known, network)
    if walled:
        morphology = "walled"
    elif port:
        morphology = "harbour"
    elif placement.crossing == "ford" or _riverside(generator, placement):
        morphology = "riverbank"
    elif on_road:
        morphology = "street"
    else:
        morphology = "clustered"
    told["morphology"] = morphology
    if heading is not None:
        told["orientation"] = heading
    return told


def _riverside(generator, placement) -> bool:
    if placement.cell is None:
        return False
    i, j = placement.cell
    return any((i + di, j + dj) in generator.channel
               for dj in (-1, 0, 1) for di in (-1, 0, 1))


def _street_of(generator, placement, known, network) -> tuple[bool, list | None]:
    """Whether a road runs through this place, and which way it leaves.

    The heading is a unit vector along the busiest road out, so a drawn footprint
    can lie along its street the way a real road-town does. No angles anywhere.
    """
    if network is None or placement.cell is None:
        return False, None
    for route in network.routes:
        a, b = route.joins
        for end in (a, b):
            place = known[end]
            if place is not placement and (place.x, place.y) != (placement.x,
                                                                 placement.y):
                continue
            if len(route.cells) < 2:
                continue
            step = route.cells[1] if end == a else route.cells[-2]
            sx, sy = generator._centre(*step)
            dx, dy = sx - placement.x, sy - placement.y
            span = math.hypot(dx, dy) or 1.0
            return True, [round(dx / span, 2), round(dy / span, 2)]
    return False, None


def _settlement_drafts(generator, placements) -> list[FeatureDraft]:
    """Every settlement the map knows about — including the ones it did not move.

    A town this run does not mention is a town the apply retires. So a place the
    generator proposed last time, and the writer accepted, has to be proposed again
    every run or it is deleted and re-invented on alternate runs forever. That is
    exactly what happened: the map flipped between two states with a period of two.
    """
    from fw.core.mapgen.generate import LAYER_SETTLEMENTS

    seen = {p.entity_id for p in placements if p.entity_id}
    standing = [p for p in generator._already_placed(placements)
                if p.entity_id not in seen]
    known = list(placements) + standing
    network = generator.road_network(known) if len(known) >= 2 else None

    out: list[FeatureDraft] = []
    for placement in known:
        region_name = generator.profiles[placement.region_id].name
        invented = placement.entity_id is None
        key = _settlement_key(generator, placement)
        history = _event_reasons(generator, placement.entity_id)
        reasons = history + tuple(Reason(kind="market", weight=1.0, template=text)
                                  for text in placement.reasons[:3])
        if not reasons:
            # §67: every feature owes the writer a sentence, and "this is where you
            # put it" is a true and useful one.
            reasons = (Reason(kind="authored", weight=1.0,
                              template="stands where you placed it"),)
        out.append(FeatureDraft(
            kind="settlement",
            key_parts=key,
            known_id=_known_id(generator.world, placement),
            subject=(SubjectSpec(mode="new", type_key="settlement",
                                 tags=(GENERATED_TAG,),
                                 exists_from=_no_older_than(generator, placement),
                                 summary_template="Proposed by the map.")
                     if invented else
                     SubjectSpec(mode="existing", type_key="settlement",
                                 entity_id=placement.entity_id)),
            shapes=(ShapeSpec(role="point", kind="point",
                              coordinates=[round(placement.x, 1),
                                           round(placement.y, 1)],
                              layer=LAYER_SETTLEMENTS,
                              style={"rank": placement.rank}, approximate=True),),
            facts=((FactSpec("located_in", object_ref=placement.region_id),
                    FactSpec("settlement_type", value=placement.rank))
                   if invented else ()),
            reasons=reasons,
            name_request=(NameRequest(
                key=name_key("settlement", (region_name,),
                             int(placement.x) * 1000 + int(placement.y)),
                kind="settlement", hint=_hint_of(placement))
                if invented else None),
            # §66 in one flag: putting the writer's own town on the map is expected;
            # inventing a town is a suggestion they have to accept.
            default_accept=not invented,
            detail={"rank": placement.rank, "region": region_name,
                    **_settlement_semantics(generator, placement, known, network),
                    **_founding(generator, placement.entity_id)},
        ))
    return out


# What a hold is *for*, in the atlas's vocabulary — keyed by what it watches, which is
# how `hold._worth` chose the ground in the first place.
ARCHETYPES = {"pass": "mountain stronghold", "ford": "river castle",
              "harbour": "coastal fortress", "march": "border fortress",
              "seat": "citadel", "road": "manor"}


def _house_entity(generator, house_key: str) -> str | None:
    """The writer's entity behind a hold's house, for the fact written back."""
    if not house_key or generator.reading is None:
        return None
    for house in generator.reading.houses:
        if house.key == house_key:
            return house.entity_id
    return None


def _castle_drafts(generator, placements) -> list[FeatureDraft]:
    """The places worth holding, and what each of them holds.

    Proposed rather than drawn: a castle is a noun, and inventing a noun is the writer's
    to accept (§66). The reasons are the whole value of the proposal — a keep offered
    with no account of itself is just a pin — so what it watches goes in the sentence.
    """
    from fw.core.mapgen.generate import LAYER_CASTLES

    holds = generator._site_castles(placements)
    politics = generator.political()
    out: list[FeatureDraft] = []
    for place in holds:
        invented = place.entity_id is None
        where = generator.owner[place.cell[1]][place.cell[0]]
        region_name = generator.profiles[where].name if where else ""
        x, y = generator._centre(*place.cell)
        reasons = (_event_reasons(generator, place.entity_id)
                   + tuple(Reason(kind="crossing", weight=1.0, template=text)
                           for text in place.reasons[:3]))
        if not reasons:
            reasons = (Reason(kind="authored", weight=1.0,
                              template="stands where you placed it"),)
        facts: list[FactSpec] = []
        if invented and where:
            facts.append(FactSpec("located_in", object_ref=where))
            # The fact `Hold.house_key` was minted for: the house that holds the
            # country holds the keep proposed in it, said under the same authority
            # the country itself is held under — no vaguer, no grander.
            house_id = _house_entity(generator, place.house_key)
            authority = str((politics.get(where) or {}).get("authority") or "")
            if house_id and authority in ("legally_owns", "administers", "occupies"):
                facts.append(FactSpec(
                    authority, subject_ref=house_id,
                    note="proposed with the keep, which stands in this "
                         "house's country"))
        out.append(FeatureDraft(
            kind="castle",
            key_parts=("h", region_name, place.cell[0], place.cell[1]),
            subject=(SubjectSpec(mode="new", type_key="holding",
                                 tags=(GENERATED_TAG,),
                                 summary_template="Proposed by the map.")
                     if invented else
                     SubjectSpec(mode="existing", type_key="holding",
                                 entity_id=place.entity_id)),
            shapes=(ShapeSpec(role="point", kind="point",
                              coordinates=[round(x, 1), round(y, 1)],
                              layer=LAYER_CASTLES,
                              style={"rank": place.rank}, approximate=True),),
            facts=tuple(facts),
            reasons=reasons,
            name_request=(NameRequest(
                key=name_key("castle", (region_name,),
                             place.cell[0] * 1000 + place.cell[1]),
                kind="castle", hint=place.watches)
                if invented else None),
            default_accept=not invented,
            detail={"rank": place.rank, "watches": place.watches,
                    "archetype": ARCHETYPES.get(place.watches, "border fortress")},
        ))
    return out


def _hint_of(placement) -> str:
    """Why this town is here, in the namer's closed vocabulary."""
    joined = " ".join(placement.reasons).lower()
    for needle, hint in (("two rivers", "ford"), ("river", "ford"),
                         ("harbour", "harbour"), ("sheltered", "harbour"),
                         ("pass", "pass"), ("high", "height"),
                         ("marsh", "marsh"), ("iron", "ore"), ("mine", "ore")):
        if needle in joined:
            return hint
    return ""


def _endpoint_id(generator, placement) -> str:
    """The feature id of a place a road runs to.

    A road used to be identified by its endpoints' *names*. Accepting a proposed town
    gives it a real name, so every road to it changed identity the next run and was
    drawn all over again beside the first. What a road joins does not change when the
    towns are renamed, so that is what it is keyed on.
    """
    known = _known_id(generator.world, placement)
    if known:
        return known
    return feature_id("settlement", *_settlement_key(generator, placement))


def _ref_for(generator, placement) -> str:
    """How a segment names one of its ends: an entity if it has one, else the draft."""
    if placement.entity_id:
        return placement.entity_id
    return "@" + _endpoint_id(generator, placement)


# How a road of each grade is drawn, and how much quicker it is to travel.
ROAD_WIDTHS = {"highway": 3.4, "road": 2.2, "track": 1.3}
ROAD_QUALITY = {"highway": 0.95, "road": 0.8, "track": 0.6}


def _road_drafts(generator, placements) -> list[FeatureDraft]:
    from fw.core.mapgen.generate import LAYER_ROADS

    # Every place the map knows, proposed ones included. Waiting for a town to exist
    # before drawing the road to it meant the first run drew no roads at all and the
    # second drew twelve — so the same map came out different on its second look.
    known = _known_places(generator, placements)
    if len(known) < 2:
        return []
    network = generator.road_network(known)
    fords = {crossing.cell for crossing in generator.movement.fords}
    # The travel graph rides the per-pair routes — a segment must run from one place
    # the router knows to another — but the *ink* dedups against the link table, so
    # a street twelve routes share is drawn once, at the grade the ground carries
    # (V2 §10). The routes come busiest-first, so a shared corridor is inked at its
    # trunk's width and each later route draws only the stretch that is its own.
    inked: set = set()
    out: list[FeatureDraft] = []
    for order, route in enumerate(network.routes):
        a, b = route.joins
        p, q = known[a], known[b]
        path = list(route.cells)
        points = generator._road_line(p, path, q)
        length = sum(math.dist(points[n], points[n + 1])
                     for n in range(len(points) - 1))
        shapes = _road_shapes(generator, route, p, q, network.links, inked,
                              LAYER_ROADS)
        # A road to a town the writer turned down is a road to nowhere, so it says
        # which towns it needs and goes when they go.
        needs = tuple(_settlement_key(generator, end)
                      for end in (p, q) if end.entity_id is None)
        ends = tuple(sorted((_endpoint_id(generator, p), _endpoint_id(generator, q))))
        # §66: a road the writer named is theirs — the drawn road IS their entity,
        # not a generated twin laid beside it with an invented name.
        theirs = _writers_route(generator, route.given) if route.given else None
        out.append(FeatureDraft(
            kind="road",
            key_parts=ends,
            subject=(SubjectSpec(mode="existing", type_key="road",
                                 entity_id=theirs)
                     if theirs else
                     SubjectSpec(mode="new", type_key="road", tags=(GENERATED_TAG,),
                                 summary_template="Laid by the map between the "
                                                  "places it knows.")),
            shapes=shapes,
            segments=(SegmentSpec(
                from_ref=_ref_for(generator, p),
                to_ref=_ref_for(generator, q),
                length=round(length, 1), medium="road",
                # A highway is a made road and a track is a path through the heather,
                # and a traveller on one arrives a good deal sooner than on the other.
                quality=ROAD_QUALITY.get(route.grade, 0.7),
                terrain=generator._road_terrain(path),
                # A road is no older than the younger of the two places it joins —
                # an honest bound, not a construction date (V2 §10).
                built_on=_no_younger_end(generator, p, q)),),
            depends_on_keys=needs,
            reasons=(Reason(kind="crossing", weight=1.0,
                            template="the easiest ground between {0} and {1}",
                            refs=(p.name, q.name)),
                     Reason(kind="crossing", weight=0.8,
                            template=route.because),),
            fixed_name=route.given,
            name_request=(None if route.given else
                          NameRequest(key=name_key("road", ends, order),
                                      kind="road", hint="")),
            detail={"grade": route.grade, "traffic": route.traffic,
                    "span": round(length, 1),
                    "crossings": _river_crossings(generator, path, fords,
                                                  route.grade)},
        ))
    return out


def _road_shapes(generator, route, p, q, links: dict, inked: set,
                 layer: str) -> tuple[ShapeSpec, ...]:
    """One route's ink: its un-drawn stretches, each at its own link grade.

    Split where the grade changes and where another route already inked the ground.
    The first and last vertices snap to the places themselves when the run reaches
    them, exactly as the whole line used to.
    """
    path = list(route.cells)
    runs: list[tuple[str, list]] = []
    for n in range(len(path) - 1):
        link = (path[n], path[n + 1]) if path[n] <= path[n + 1] else (
            path[n + 1], path[n])
        if link in inked:
            if runs and runs[-1][1]:
                runs.append(("", []))            # a gap: someone drew this stretch
            continue
        inked.add(link)
        grade = links.get(link, route.grade)
        if not runs or runs[-1][0] != grade or not runs[-1][1]:
            runs.append((grade, [path[n], path[n + 1]]))
        else:
            runs[-1][1].append(path[n + 1])
    shapes: list[ShapeSpec] = []
    for grade, run in runs:
        if len(run) < 2:
            continue
        points = [list(generator._centre(i, j)) for i, j in run]
        if run[0] == path[0]:
            points[0] = [p.x, p.y]
        if run[-1] == path[-1]:
            points[-1] = [q.x, q.y]
        drawn = _road_run_line(points)
        shapes.append(ShapeSpec(
            role="spine" if not shapes else "segment", kind="line",
            coordinates=drawn, layer=layer,
            style={"role": "road", "stroke-width": ROAD_WIDTHS.get(grade, 2.0)},
            approximate=True))
    return tuple(shapes)


def _road_run_line(points: list) -> list:
    """One run's cells as a drawable line — the same easing the whole road got."""
    from fw.core.mapgen.generate import ROAD_TOLERANCE

    eased = shapes.simplify(shapes.eased([tuple(p) for p in points]),
                            ROAD_TOLERANCE)
    return [[round(x, 1), round(y, 1)] for x, y in eased]


def _writers_route(generator, given: str) -> str | None:
    """The entity behind a road the writer named, if the reading has it."""
    if generator.reading is None:
        return None
    for route in generator.reading.routes:
        if route.name == given and route.entity_id:
            return route.entity_id
    return None


def _no_younger_end(generator, p, q) -> int | None:
    """The later founding of the two ends — the earliest day the road makes sense."""
    days = []
    for place in (p, q):
        if place.entity_id:
            told = _founding(generator, place.entity_id)
            if told.get("founded") is not None:
                days.append(told["founded"])
        else:
            bound = _no_older_than(generator, place)
            if bound is not None:
                days.append(bound)
    return max(days) if days else None


def _river_crossings(generator, path: list, fords: set,
                     grade: str) -> list[list]:
    """Where this road meets real water, and how it gets across (V2 §10).

    A ford where the movement stage found one wadeable; a bridge where a highway
    crosses a channel with no ford to use; nothing at all for a track through a
    stream — a reader assumes travellers wade.
    """
    told: list[list] = []
    for cell in path:
        i, j = cell
        if cell in fords:
            x, y = generator._centre(i, j)
            told.append([round(x, 1), round(y, 1), "ford"])
        elif cell in generator.channel and grade == "highway":
            x, y = generator._centre(i, j)
            told.append([round(x, 1), round(y, 1), "bridge"])
    return told[:3]                     # the budget: a road is not a viaduct


# ---- drafts into a plan ----------------------------------------------------

def _assemble(world: World, brief: MapBrief, drafts: list[FeatureDraft],
              reading=None) -> tuple[PlannedFeature, ...]:
    """Mint ids, choose names, render explanations."""
    namer = (Namer.from_corpus(reading.corpus, seed=brief.seed or world.name)
             if reading is not None
             else Namer.from_world(world, seed=brief.seed or world.name))
    ids_by_key: dict[tuple, str] = {}
    for draft in drafts:
        ids_by_key[draft.key_parts] = (
            draft.known_id or feature_id(draft.kind, *draft.key_parts))

    names: dict[str, str] = {}
    for draft in sorted(drafts, key=lambda d: (d.kind, str(d.key_parts))):
        fid = ids_by_key[draft.key_parts]
        if draft.fixed_name:
            names[fid] = draft.fixed_name
        elif draft.name_request is not None:
            names[fid] = namer.name(draft.name_request.kind, draft.name_request.key,
                                    hint=draft.name_request.hint)
        elif draft.subject is not None and draft.subject.entity_id:
            entity = world.get_entity(draft.subject.entity_id)
            names[fid] = entity.name if entity else "Unnamed"
        else:
            names[fid] = "Unnamed"

    # A second pass for the names made out of other names. One pass cannot do it: a
    # crossing is called after the town it lands at, and that town may be one this same
    # run is inventing, so its name does not exist until the first pass has finished.
    for draft in drafts:
        if not draft.name_template:
            continue
        fid = ids_by_key[draft.key_parts]
        names[fid] = draft.name_template.format(
            *(_in_a_phrase(names.get(ref, ref)) for ref in draft.name_refs))

    features: list[PlannedFeature] = []
    for draft in drafts:
        fid = ids_by_key[draft.key_parts]
        why = tuple(reason.render(names) for reason in draft.reasons)
        features.append(PlannedFeature(
            id=fid, kind=draft.kind, name=names[fid],
            subject=draft.subject,
            anchor_id=(ids_by_key.get(draft.anchor_key)
                       if draft.anchor_key is not None else None),
            shapes=draft.shapes, facts=draft.facts, segments=draft.segments,
            why=why, detail=dict(draft.detail),
            depends_on=tuple(ids_by_key[k] for k in draft.depends_on_keys
                             if k in ids_by_key),
            default_accept=draft.default_accept,
            renameable=not (draft.subject is not None
                            and draft.subject.mode == "existing"),
            causal=draft.causal,
        ))
    return order_features(features)


def _in_a_phrase(name: str) -> str:
    """A name used inside a longer name loses its article.

    "The Salt Coast" plus "The {0} crossing" is "The The Salt Coast crossing", and
    English has a rule for this: the article belongs to the phrase, not to the name
    inside it.
    """
    return name[4:] if name[:4].lower() == "the " else name


def _retirements(world: World, brief: MapBrief,
                 features: tuple[PlannedFeature, ...]) -> tuple[Retirement, ...]:
    """What the last map left that this one does not propose."""
    live = {f.id for f in features}
    out: list[Retirement] = []
    for fid, row in sorted(ledger_module.read_ledger(world, at=brief.at).items()):
        if fid in live or row.pinned:
            continue
        # A brief asking only for settlements must not sweep away the rivers it never
        # looked at. Narrowing what you are drawing is not the same as saying the rest
        # should go.
        kind = kind_of(fid)
        if kind is not None and not brief.covers(kind):
            continue
        touched = ledger_module.writer_touched(
            world, row.entity_id, row.name_at_write,
            summary_at_write=row.summary_at_write, at=brief.at)
        out.append(Retirement(
            feature_id=fid, name=row.name_at_write or "an unnamed shape",
            entity_id=row.entity_id, geometry_ids=row.geometry_ids,
            writer_touched=touched,
            why=("you have made this yours, so it will be left alone"
                 if touched else "the new map does not have it")))
    return tuple(out)


def _empty(world: World, brief: MapBrief, findings: list[Finding]) -> MapPlan:
    return MapPlan(plan_id=digest_of(brief, ()), world_name=world.name,
                   branch=world.branch_name, brief=brief,
                   findings=tuple(findings))


def _counts(features) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        counts[feature.kind] = counts.get(feature.kind, 0) + 1
    return counts


def _vertices(coordinates) -> int:
    if isinstance(coordinates, (int, float)):
        return 0
    if coordinates and all(isinstance(v, (int, float)) for v in coordinates):
        return 1
    return sum(_vertices(child) for child in coordinates)


def generate(world: World, brief: MapBrief | None = None):
    """Plan and accept in one call — the old one-button behaviour, unchanged."""
    from fw.core.mapgen.apply import apply_plan
    from fw.core.mapgen.decide import DecisionSet

    plan = plan_map(world, brief)
    if not plan.features:
        raise WorldError(plan.findings[0].message if plan.findings
                         else "there is nothing to draw")
    return plan, apply_plan(world, plan, DecisionSet.defaults(plan))
