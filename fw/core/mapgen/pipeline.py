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
from fw.core.mapgen.drafts import (
    FactSpec,
    FeatureDraft,
    NameRequest,
    Reason,
    SegmentSpec,
    ShapeSpec,
    SubjectSpec,
)
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

    drafts, stage_ms, stage_findings = _compute(world, brief)
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
        stats=stats,
        findings=tuple(findings),
    )


# ---- the compute, still the first generator's ------------------------------

def _compute(world: World, brief: MapBrief
             ) -> tuple[list[FeatureDraft], dict[str, int], list[Finding]]:
    """Run the geography and collect drafts. No writes."""
    from fw.core.mapgen.generate import MapGenerator

    findings: list[Finding] = []
    timings: dict[str, int] = {}
    generator = MapGenerator(world, seed=brief.seed or None, at=brief.at)

    regions = [e for e in world.entities("region")
               if brief.at is None or e.exists_on(brief.at)]
    if not regions:
        return [], timings, findings

    mark = time.perf_counter()
    generator.profiles = {
        r.id: _profile(world, r.id, brief.at) for r in regions}
    authored = generator._authored_outlines()
    generator._build_landmass(authored)
    generator._assign_cells(regions, authored)
    generator._build_fields()
    rivers = generator._trace_rivers()
    generator._build_costs()
    placements = generator._site_settlements(propose=brief.invent_settlements)
    timings["geography"] = int((time.perf_counter() - mark) * 1000)

    mark = time.perf_counter()
    namer = Namer.from_world(world, seed=generator.seed)
    drafts: list[FeatureDraft] = []
    if brief.wants("region"):
        drafts.extend(_region_drafts(generator, authored))
    if brief.wants("river"):
        drafts.extend(_river_drafts(generator, rivers, namer))
    if brief.wants("settlement"):
        drafts.extend(_settlement_drafts(generator, placements, namer))
    if brief.wants("road"):
        drafts.extend(_road_drafts(generator, placements))
    timings["drafting"] = int((time.perf_counter() - mark) * 1000)
    return drafts, timings, findings


def _profile(world: World, region_id: str, at: int | None):
    from fw.core.mapgen.attributes import profile_region
    return profile_region(world, region_id, at=at)


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


def _river_drafts(generator, rivers, namer: Namer) -> list[FeatureDraft]:
    from fw.core.mapgen.generate import LAYER_WATER

    out: list[FeatureDraft] = []
    for path in rivers:
        points = [[round(x, 1), round(y, 1)]
                  for x, y in (generator._centre(i, j) for i, j in path)]
        mouth, source = path[-1], path[0]
        out.append(FeatureDraft(
            kind="river",
            key_parts=(mouth[0], mouth[1], source[0], source[1]),
            subject=SubjectSpec(
                mode="new", type_key="waterway", tags=(GENERATED_TAG,),
                summary_template="Traced by the map; rename it and it is yours."),
            shapes=(ShapeSpec(role="spine", kind="line", coordinates=points,
                              layer=LAYER_WATER, style={"stroke": "#4a7fa5"},
                              approximate=True),),
            reasons=(Reason(kind="mouth", weight=1.0,
                            template="runs from the high ground to the sea"),),
            name_request=NameRequest(
                key=name_key("river", (str(mouth[0]), str(mouth[1]))),
                kind="waterway", hint="river"),
            detail={"mouth": list(mouth), "strahler": 1},
        ))
    return out


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
        if draft.name_request is not None:
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
