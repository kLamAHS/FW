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

import time

from fw.core.mapgen import ledger as ledger_module
from fw.core.mapgen import shapes
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

    drafts, stage_ms, stage_findings, terrain = _compute(world, brief)
    findings.extend(stage_findings)
    if not drafts:
        findings.append(note(
            "unplaced",
            "there are no regions yet — a map grows from regions, so name a few and "
            "say what they are like"))
        return _empty(world, brief, findings)

    features = _assemble(world, brief, drafts)
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
                        Terrain | None]:
    """Run the geography and collect drafts. No writes."""
    from fw.core.mapgen.generate import MapGenerator

    findings: list[Finding] = []
    timings: dict[str, int] = {}
    generator = MapGenerator(world, seed=brief.seed or None, at=brief.at)

    regions = generator.regions_of_the_world()
    if not regions:
        return [], timings, findings, None

    mark = time.perf_counter()
    generator.profiles = {
        r.id: _profile(world, r.id, brief.at) for r in regions}
    authored = generator._authored_outlines()
    generator._build_landmass(authored)
    generator._assign_cells(regions, authored)
    generator._build_fields()
    rivers = generator._trace_rivers()
    generator._classify_ground()
    generator._build_costs()
    placements = generator._site_settlements(propose=brief.invent_settlements)
    timings["geography"] = int((time.perf_counter() - mark) * 1000)

    mark = time.perf_counter()
    namer = Namer.from_world(world, seed=generator.seed)
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
        drafts.extend(_river_drafts(generator, rivers, namer))
    if brief.wants("settlement"):
        drafts.extend(_settlement_drafts(generator, placements, namer))
    if brief.wants("road"):
        drafts.extend(_road_drafts(generator, placements))
    timings["drafting"] = int((time.perf_counter() - mark) * 1000)
    return drafts, timings, findings, _terrain_of(generator)


def _terrain_of(generator) -> Terrain | None:
    """The surface this plan was worked out on, to be kept if it is accepted.

    Three fields and no more. Height is what the relief is lit from; cover and standing
    water are what the ground is coloured by. Everything else the generator held — the
    flow network, the temperature, the rock hardness — is either recoverable from these
    or was scaffolding, and a world file is a thing a writer keeps for years.
    """
    from fw.core.mapgen.generate import GRID, SEA_LEVEL

    if not generator.elevation or generator.vegetation is None:
        return None
    grid = generator._grid()
    return Terrain(
        seed=generator.seed, size=GRID, span=grid.span,
        origin_x=grid.origin_x, origin_y=grid.origin_y, sea_level=SEA_LEVEL,
        fields={
            "elevation": generator.elevation,
            "canopy": generator.vegetation.canopy,
            "marsh": generator.vegetation.marsh,
        })


def _profile(world: World, region_id: str, at: int | None):
    from fw.core.mapgen.attributes import profile_region
    return profile_region(world, region_id, at=at)


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
                           layer="land", style={"fill": "#cfd3a4"}, approximate=True),)
                # The inland waters belong to the mainland: they are holes in it, and
                # drawing them anywhere else would leave lakes floating in the sea.
                + (tuple(ShapeSpec(role="hole", kind="polygon",
                                   coordinates=[shapes.closed(grid.to_world(hole))],
                                   layer="waters", style={"fill": "#3f5b6c"},
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
            detail={"landmass": ordinal, "area": round(area, 1)},
        ))
    return out


def _region_drafts(generator, authored: dict) -> list[FeatureDraft]:
    from fw.core.mapgen.generate import LAYER_REGIONS, _terrain_colour

    out: list[FeatureDraft] = []
    for region_id in sorted(generator.profiles):
        profile = generator.profiles[region_id]
        if region_id in authored:
            continue                      # the writer drew it; it is not ours to redraw
        ring = generator._outline(region_id)
        if ring is None:
            continue
        out.append(FeatureDraft(
            kind="region",
            key_parts=(profile.name,),
            subject=SubjectSpec(mode="existing", type_key="region",
                                entity_id=region_id),
            shapes=(ShapeSpec(role="outline", kind="polygon", coordinates=[ring],
                              layer=LAYER_REGIONS,
                              style={"fill": _terrain_colour(profile.dominant)},
                              approximate=True),),
            reasons=(Reason(kind="authored", weight=1.0,
                            template="drawn where its neighbours leave room",
                            evidence=profile.why("terrain")),),
            detail={"share": round(len(_cells_of(generator, region_id))
                                   / max(1, generator.land_cells()), 3),
                    "dominant": profile.dominant},
        ))
    return out


def _cells_of(generator, region_id: str) -> list[tuple[int, int]]:
    from fw.core.mapgen.generate import GRID
    return [(i, j) for j in range(GRID) for i in range(GRID)
            if generator.owner[j][i] == region_id and not generator.sea[j][i]]


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
                              layer="relief", style={"stroke": "#6b6459"},
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
_FEATURE_STYLE = {"forest": "#6f8656", "marsh": "#8d9a72", "downs": "#bfb98c",
                  "moor": "#a9a184", "waste": "#ddcb9a", "ice": "#dde5e8"}


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
                          style={"fill": _FEATURE_STYLE.get(feature.kind, "#9aa583")},
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


def _river_drafts(generator, rivers, namer: Namer) -> list[FeatureDraft]:
    from fw.core.mapgen.generate import LAYER_WATER

    # Measured against the biggest river on the map, not against each river's own mouth.
    # Per-river, every river spans the same widths — a beck and a trunk are drawn
    # identically, each thickening along its own length — which is a width that says
    # "how far along this river are you" rather than "how much water is this". The point
    # of drawing five widths is that a reader can tell the Trident from a tributary.
    widest = max(
        (max(generator.flow[j][i] for i, j in path) for path in rivers), default=1.0
    ) or 1.0

    out: list[FeatureDraft] = []
    for path in rivers:
        mouth, source = path[-1], path[0]
        # A running maximum, not the flow as read. The flow field shares each cell's
        # water between all its downhill neighbours, which is what keeps the drainage
        # from snapping to eight compass directions — but it means the figure at a cell
        # on the traced course can be lower than the one just upstream of it, where the
        # water took more than one way down. Read literally, a river then narrows and
        # widens along its length like a string of sausages. Water does not leave a
        # river, so the width does not go back down.
        carried: list[float] = []
        so_far = 0.0
        for i, j in path:
            so_far = max(so_far, generator.flow[j][i])
            carried.append(so_far)
        biggest = carried[-1] or 1.0
        bands = [_band(value / widest) for value in carried]

        # One shape per run of equal width, each overlapping the next by a point so the
        # river is continuous where it steps. Emitting a shape per *segment* instead
        # would be five times the vertices for the same picture.
        shapes: list[ShapeSpec] = []
        start = 0
        for n in range(1, len(path) + 1):
            if n < len(path) and bands[n] == bands[start]:
                continue
            run = path[start:min(n + 1, len(path))]
            if len(run) >= 2:
                shapes.append(ShapeSpec(
                    # The first reach is the river's spine and the rest are segments of
                    # it. Roles are a closed vocabulary on purpose — the same argument as
                    # the finding codes — so a river's reaches are named from it rather
                    # than given a scheme of their own.
                    role="spine" if start == 0 else "segment",
                    kind="line",
                    coordinates=[[round(x, 1), round(y, 1)]
                                 for x, y in (generator._centre(i, j) for i, j in run)],
                    layer=LAYER_WATER,
                    style={"stroke": "#4a7fa5",
                           "stroke-width": RIVER_WIDTHS[bands[start]]},
                    approximate=True))
            start = n
        if not shapes:
            continue

        out.append(FeatureDraft(
            kind="river",
            key_parts=(mouth[0], mouth[1], source[0], source[1]),
            subject=SubjectSpec(
                mode="new", type_key="waterway", tags=(GENERATED_TAG,),
                summary_template="Traced by the map; rename it and it is yours."),
            shapes=tuple(shapes),
            reasons=(Reason(kind="mouth", weight=1.0,
                            template="runs from the high ground to the sea"),),
            name_request=NameRequest(
                key=name_key("river", (str(mouth[0]), str(mouth[1]))),
                kind="waterway", hint="river"),
            detail={"mouth": list(mouth), "strahler": bands[-1] + 1,
                    "discharge": round(biggest, 1)},
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


def _settlement_drafts(generator, placements, namer: Namer) -> list[FeatureDraft]:
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

    out: list[FeatureDraft] = []
    for placement in list(placements) + standing:
        region_name = generator.profiles[placement.region_id].name
        invented = placement.entity_id is None
        key = _settlement_key(generator, placement)
        reasons = tuple(Reason(kind="market", weight=1.0, template=text)
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
            detail={"rank": placement.rank, "region": region_name},
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


def _road_drafts(generator, placements) -> list[FeatureDraft]:
    import math

    from fw.core.mapgen.generate import LAYER_ROADS

    # Every place the map knows, proposed ones included. Waiting for a town to exist
    # before drawing the road to it meant the first run drew no roads at all and the
    # second drew twelve — so the same map came out different on its second look.
    seen = {p.entity_id for p in placements if p.entity_id}
    known = list(placements) + [p for p in generator._already_placed(placements)
                                if p.entity_id not in seen]
    if len(known) < 2:
        return []
    edges = generator._road_edges(known) if hasattr(generator, "_road_edges") else []
    out: list[FeatureDraft] = []
    for order, (a, b) in enumerate(edges):
        p, q = known[a], known[b]
        path = generator._route(generator._cell_of(p.x, p.y),
                                generator._cell_of(q.x, q.y))
        points = [[p.x, p.y]] + [[round(x, 1), round(y, 1)]
                                 for x, y in (generator._centre(i, j)
                                              for i, j in path[1:-1])]
        points.append([q.x, q.y])
        length = sum(math.dist(points[n], points[n + 1])
                     for n in range(len(points) - 1))
        # A road to a town the writer turned down is a road to nowhere, so it says
        # which towns it needs and goes when they go.
        needs = tuple(_settlement_key(generator, end)
                      for end in (p, q) if end.entity_id is None)
        ends = tuple(sorted((_endpoint_id(generator, p), _endpoint_id(generator, q))))
        out.append(FeatureDraft(
            kind="road",
            key_parts=ends,
            subject=SubjectSpec(mode="new", type_key="road", tags=(GENERATED_TAG,),
                                summary_template="Laid by the map between the places "
                                                 "it knows."),
            shapes=(ShapeSpec(role="segment", kind="line", coordinates=points,
                              layer=LAYER_ROADS, style={"stroke": "#8a7550"},
                              approximate=True),),
            segments=(SegmentSpec(
                from_ref=_ref_for(generator, p),
                to_ref=_ref_for(generator, q),
                length=round(length, 1), medium="road", quality=0.8,
                terrain=generator._road_terrain(path)),),
            depends_on_keys=needs,
            reasons=(Reason(kind="crossing", weight=1.0,
                            template="the easiest ground between {0} and {1}",
                            refs=(p.name, q.name)),),
            name_request=NameRequest(key=name_key("road", ends, order),
                                     kind="road", hint=""),
            detail={"tier": "road", "span": round(length, 1)},
        ))
    return out


# ---- drafts into a plan ----------------------------------------------------

def _assemble(world: World, brief: MapBrief,
              drafts: list[FeatureDraft]) -> tuple[PlannedFeature, ...]:
    """Mint ids, choose names, render explanations."""
    namer = Namer.from_world(world, seed=brief.seed or world.name)
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
        if kind is not None and not brief.wants(kind):
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
