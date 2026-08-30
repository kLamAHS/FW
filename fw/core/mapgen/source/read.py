"""One reading of the world, in a handful of bulk queries.

There were six traversals before this. `profile_region` walked the facts of each region
one at a time; `generate` opened the world again for authored outlines, for borders, for
the writer's own settlements, for holdings, for terrain features, for populations and for
capitals; `Namer.from_world` walked every entity for the name corpus; and `read_ledger`
walked them again for provenance. Six answers to "what is in this world", assembled at six
different moments, none of them written down.

This is one answer, written down. Everything after it reads the `WorldReading` and nothing
reads `World` — which is the property that makes a stage's output a function of its input
rather than of when it happened to run.

Three rules the reader keeps and the thing it replaced did not:

**Nothing the map itself wrote is read back.** An entity tagged `generated-map` is the
map's own output; a fact marked speculative and stamped by mapgen likewise. Reading them
is how a map ends up laid out around its own previous coastline, drifting a little further
every time the writer presses the button.

**Everything is ordered by what it says, never by what the database returned.** Facts go
through `guards.sorted_facts`; every collection comes out a sorted tuple.

**Nothing keyed on an entity id reaches the outside.** Keys are made from names.
"""

from __future__ import annotations

from fw.core.mapgen import guards
from fw.core.mapgen.attributes import (
    CLIMATE_WORDS,
    DEFAULT_TERRAIN,
    TERRAIN_KINDS,
    TERRAIN_WORDS,
    _find_word,
)
from fw.core.mapgen.findings import Finding, note, ordered
from fw.core.mapgen.source import graph as border_graph
from fw.core.mapgen.source import scan
from fw.core.mapgen.source.claims import Basis, Claims, Reading, known, unstated
from fw.core.mapgen.source.reading import (
    AUTHORITIES,
    EventReading,
    HouseReading,
    Key,
    RegionReading,
    ResourceReading,
    RouteReading,
    SettlementReading,
    TitleReading,
    WaterReading,
    WorldReading,
    key_for,
)
from fw.core.model.records import GENERATED_TAG as _GENERATED_TAG

# Defined on the entity itself: the lists and the continuity checks
# have to recognise the map's own suggestions too.
GENERATED_TAG = _GENERATED_TAG

# The types the map has anything to say about. A person is not on a map.
PLACE_TYPES = ("region", "settlement", "holding", "site", "terrain_feature")
GROUP_TYPES = ("house", "order", "guild", "company", "tribe", "clan", "dynasty")
WATER_KINDS = {"river": "river", "lake": "lake", "bay": "bay", "sea": "sea",
               "strait": "strait", "mere": "lake", "loch": "lake", "sound": "strait"}

# The writer's word for a thing that comes out of the ground, mapped onto the six fields
# the map keeps. Deliberately narrow: a word not here claims nothing, which is better
# than deciding that "amber" means ore and sinking a mine in a forest.
RESOURCE_KINDS = {
    "grain": "arable", "wheat": "arable", "corn": "arable", "barley": "arable",
    "farmland": "arable", "farms": "arable", "crops": "arable", "orchards": "arable",
    "cattle": "pasture", "sheep": "pasture", "wool": "pasture", "horses": "pasture",
    "herds": "pasture", "pasture": "pasture", "grazing": "pasture",
    "timber": "timber", "wood": "timber", "lumber": "timber", "forest": "timber",
    "stone": "stone", "quarry": "stone", "quarries": "stone", "marble": "stone",
    "slate": "stone", "granite": "stone", "salt": "stone", "salterns": "stone",
    "iron": "ore", "ore": "ore", "silver": "ore", "gold": "ore", "copper": "ore",
    "tin": "ore", "lead": "ore", "mines": "ore", "mining": "ore",
    "fish": "fish", "fishing": "fish", "fisheries": "fish", "whaling": "fish",
}

# What makes an event the kind that leaves a mark on the ground.
DESTRUCTION_WORDS = ("battle", "siege", "sack", "burn", "razed", "war", "massacre",
                     "flood", "fire", "plague", "ruin", "fell", "slaughter")

SEA_WORDS = ("coast", "sea", "port", "harbour", "harbor", "shore", "ocean", "strand")


def read_world(world, *, at: int | None = None, north: str = "up") -> WorldReading:
    """Everything the map needs to know about the writer's world, read once."""
    entities = [e for e in world.entities() if GENERATED_TAG not in e.tags]
    alive = {e.id: e for e in entities
             if at is None or e.exists_on(at) or e.type_key not in PLACE_TYPES}
    facts = [f for f in guards.sorted_facts(world.facts_where(at=at))
             if not _is_the_maps_own(f)]
    geometry = world.geometry_index(at=at)

    by_id = {e.id: e for e in entities}
    keys: dict[str, Key] = {e.id: key_for(e.type_key, e.name) for e in entities}
    gazetteer = frozenset(e.name.lower() for e in entities if e.name)

    out_facts: dict[tuple[str, str], list] = {}
    in_facts: dict[tuple[str, str], list] = {}
    for fact in facts:
        out_facts.setdefault((fact.subject_id, fact.predicate_key), []).append(fact)
        if fact.object_id:
            in_facts.setdefault((fact.object_id, fact.predicate_key), []).append(fact)

    findings: list[Finding] = []
    mentions = _mentions(entities, keys, gazetteer)

    regions = _regions(world, alive, by_id, keys, out_facts, in_facts, geometry,
                       mentions, findings, at)
    settlements = _settlements(alive, by_id, keys, out_facts, in_facts, geometry,
                               mentions, at)
    houses = _houses(alive, by_id, keys, out_facts)
    waters = _waters(alive, by_id, keys, out_facts, geometry)
    routes = _routes(alive, by_id, keys, out_facts, geometry)
    resources = _resources(alive, by_id, keys, out_facts, in_facts)
    titles = _titles(world, by_id, keys, at)
    events = _events(world, keys, at)

    stated = set()
    for (subject, predicate), rows in out_facts.items():
        if predicate != "borders":
            continue
        for fact in rows:
            if fact.object_id and subject in keys and fact.object_id in keys:
                stated.add((keys[subject], keys[fact.object_id]))
    borders = border_graph.build([r.key for r in regions], stated)
    if not borders.planar_possible:
        findings.append(note(
            "adjacency",
            f"You have said {len(borders.edges)} of your {len(borders.nodes)} regions "
            "border one another, and a flat map cannot hold that many at once. Some "
            "border will come out as a corner rather than an edge",
            subjects=tuple(sorted(r.key for r in regions))))

    regions = tuple(_with_shape_role(r, borders) for r in regions)
    findings.extend(_ports_without_a_coast(regions, settlements))

    named = {r.key: r.name for r in (*regions, *settlements, *houses)}
    reading = WorldReading(
        world_name=world.name, at=at, north=north,
        branch=getattr(world, "branch_name", "canon"),
        regions=regions, settlements=settlements, waters=waters, routes=routes,
        houses=houses, titles=titles, events=events, resources=resources,
        borders=borders,
        names=tuple(sorted(e.name for e in entities if e.name)),
        corpus=tuple(sorted((e.type_key, e.name) for e in entities if e.name)),
        seasons=_seasons(world), presence=_presence(world, keys))
    findings.extend(_ground_two_houses_claim(reading, named))
    findings.extend(_events_before_their_ground(settlements, events))

    return WorldReading(**{**reading.__dict__, "findings": ordered(findings)})


def _ports_without_a_coast(regions, settlements) -> list[Finding]:
    """A harbour in a country the writer never said reaches the sea.

    Not corrected — their town is where they put it, and a march can perfectly well own
    one strip of shore they never thought to mention. But the map will not invent a port
    in a country described as a river plain (§66), so if they have put one there
    themselves the two statements are worth showing them side by side.
    """
    inland = {r.key: r for r in regions if not r.sea_facing.stated}
    out: list[Finding] = []
    for place in settlements:
        region = inland.get(place.region_key or "")
        if region is None:
            continue
        if place.rank.value.lower() not in ("port", "harbour", "harbor") \
                and not place.port.value:
            continue
        out.append(note(
            "contradiction",
            f"You have {place.name} as a port, and nothing you have written about "
            f"{region.name} reaches the sea. The map will not give an inland country a "
            "coast on its own, so if it has one, its terrain is the place to say so",
            subjects=(place.name, region.name),
            quotes=tuple(q for q in (place.rank.quote,) if q)))
    return out


def _ground_two_houses_claim(reading, named: dict) -> list[Finding]:
    """Ground somebody claims that somebody else holds (§11).

    Not an error and not corrected: a contested holding is the most interesting kind of
    holding a writer can invent, and Greyhaven — owned by Marr in law, run by Veyne,
    taxed by the Crown and claimed outright by Orren — is the example world's whole
    point. What the map cannot do is paint four colours on one town, so it says which
    one it chose and why, and shows the writer the other three.
    """
    out: list[Finding] = []
    for place, authority in reading.authorities().items():
        if not authority.disputed and not authority.layered:
            continue
        here = named.get(place, place)
        holds = [f"{named.get(key, key)} {word}"
                 for word, key in (("owns it in law", authority.owns),
                                   ("administers it", authority.administers),
                                   ("occupies it", authority.occupies),
                                   ("taxes it", authority.taxes)) if key]
        claims = [named.get(key, key) for key in authority.claims
                  if key not in authority.held_by]
        if claims:
            holds.append(" and ".join(claims)
                         + (" claims it" if len(claims) == 1 else " claim it"))
        chosen = named.get(authority.effective or "", "nobody")
        out.append(note(
            "contradiction",
            f"{here} is held four ways at once — " + "; ".join(holds)
            + f". The map has coloured it for {chosen}, who is the one actually in "
              "charge; the rest is on the place itself",
            subjects=(here, *sorted(claims)),
            quotes=tuple(holds)))
    return out


def _events_before_their_ground(settlements, events) -> list[Finding]:
    """A battle at a town that had not been founded yet.

    The map cannot draw the reason for a place out of a thing that had not happened, and
    a writer moving a founding date by a century will not notice they have stranded six
    events behind it. Both dates are quoted, because the fix is one of the two and only
    they know which.
    """
    founded = {s.key: s for s in settlements if s.founded is not None}
    out: list[Finding] = []
    for event in events:
        place = founded.get(event.place_key or "")
        if place is None or event.day is None or event.day >= place.founded:
            continue
        out.append(note(
            "contradiction",
            f"{event.name} happened at {place.name}, and {place.name} was not founded "
            f"until afterwards. The map has drawn the town and left the event off it",
            subjects=(event.name, place.name),
            quotes=tuple(q for q in (event.year_text,) if q)))
    return out


def _is_the_maps_own(fact) -> bool:
    """A fact the map wrote about itself is not source material.

    Both halves matter. `props["mapgen"]` alone would discard a fact the writer edited
    after the map proposed it — which is precisely the fact they most meant. Speculative
    alone would discard their own tentative notes.
    """
    props = getattr(fact, "props", None) or {}
    return bool(props.get("mapgen")) and fact.confidence == "speculative"


def _presence(world, keys) -> dict[str, int]:
    """How often each place carries the story: the scenes the writer set there.

    Counted for every scene whatever the map's date — a place the story keeps
    returning to matters on every day's map, not only after the chapters happen.
    """
    counts: dict[str, int] = {}
    for scene in world.scenes():
        place = keys.get(getattr(scene, "location_id", None) or "")
        if place:
            counts[place] = counts.get(place, 0) + 1
    return counts


def _mentions(entities, keys, gazetteer) -> dict[str, tuple]:
    """Every landform noun the writer wrote, indexed by the entity whose prose it was in."""
    out: dict[str, tuple] = {}
    for entity in entities:
        text = entity.summary or ""
        if not text:
            continue
        found = scan.scan(text, f"{keys[entity.id]}#summary", gazetteer=gazetteer)
        if found:
            out[entity.id] = found
    return out


# ---- regions ----------------------------------------------------------------


def _regions(world, alive, by_id, keys, out_facts, in_facts, geometry, mentions,
             findings, at) -> tuple[RegionReading, ...]:
    rows: list[RegionReading] = []
    for entity in sorted((e for e in alive.values() if e.type_key == "region"),
                         key=lambda e: (e.name, e.id)):
        key = keys[entity.id]
        terrain = _terrain_of(entity, out_facts, mentions, findings)
        temperature, moisture = _climate_of(entity, out_facts, mentions, findings,
                                            terrain)
        rows.append(RegionReading(
            key=key, name=entity.name, entity_id=entity.id,
            terrain_mix=terrain, temperature=temperature, moisture=moisture,
            population=_population_of(entity, out_facts),
            sea_facing=_sea_facing(entity, terrain, mentions),
            defensibility=_magnitude(entity, out_facts, "defensibility"),
            shape_role="core",
            founded=entity.exists_from, ended=entity.exists_to,
            resource_keys=_resource_keys(entity, out_facts, keys),
            parent_key=_one_object(entity, out_facts, "located_in", keys),
            settlement_keys=tuple(sorted(
                keys[f.subject_id] for f in in_facts.get((entity.id, "located_in"), ())
                if f.subject_id in alive
                and by_id[f.subject_id].type_key in ("settlement", "holding", "site"))),
            mentions=mentions.get(entity.id, ()),
            authored_ring=_ring(geometry, entity.id),
        ))
    return tuple(rows)


def _terrain_of(entity, out_facts, mentions, findings) -> Reading:
    """What kind of country this is, from whatever the writer gave the map."""
    claims: Claims = Claims(fallback=((DEFAULT_TERRAIN, 1.0),),
                            because="nothing is recorded about its terrain, so it is taken as open country")
    for fact in out_facts.get((entity.id, "terrain_kind"), ()):
        if fact.value in TERRAIN_KINDS:
            claims.add(((fact.value, 1.0),), Basis.TOKEN,
                       f"you set its terrain to {fact.value}",
                       quote=fact.value, source="fact:terrain_kind")
    for fact in out_facts.get((entity.id, "terrain"), ()):
        mix = _read_terrain(fact.value or "")
        if mix:
            claims.add(mix, Basis.PROSE_PROP, f'you wrote "{fact.value}"',
                       quote=fact.value or "", source="fact:terrain")
    for mention in mentions.get(entity.id, ()):
        kind = TERRAIN_WORDS.get(mention.head)
        if kind:
            claims.add(((kind, 1.0),), Basis.SUMMARY,
                       f'your description of it says "{mention.surface}"',
                       quote=mention.sentence, source=mention.record_key)
    settled = claims.settled()
    if settled.contested:
        findings.append(note(
            "contradiction",
            f"You have described {entity.name} two ways at once — "
            + " and ".join(f'"{c.quote}"' for c in settled.claims[:2] if c.quote)
            + ". The map has taken the first; if the other is what you meant, say it "
              "in the terrain field",
            subjects=(entity.name,),
            quotes=tuple(c.quote for c in settled.claims[:2] if c.quote)))
    return settled


def _read_terrain(text: str) -> tuple[tuple[str, float], ...]:
    """The same reading `attributes.read_terrain` does, as a sorted tuple.

    A tuple and not a dict, because this ends up inside a fingerprint and a dict's repr
    depends on insertion order.
    """
    hits: list[tuple[int, str]] = []
    for word, kind in sorted(TERRAIN_WORDS.items()):
        at = _find_word(text, word)
        if at is not None:
            hits.append((at, kind))
    weights: dict[str, float] = {}
    for _, kind in sorted(hits):
        weights.setdefault(kind, 1.0 / (len(weights) + 1))
    return tuple(sorted(weights.items()))


def _climate_of(entity, out_facts, mentions, findings, terrain) -> tuple[Reading, Reading]:
    warm: Claims = Claims(fallback=0.0, because="you did not say, so it is temperate")
    wet: Claims = Claims(fallback=0.5, because="you did not say, so it is neither")
    for fact in out_facts.get((entity.id, "temperature"), ()):
        value = _signed(fact.value)
        if value is not None:
            warm.add(value, Basis.TOKEN, "you set its temperature",
                     quote=fact.value or "", source="fact:temperature")
    for fact in out_facts.get((entity.id, "rainfall"), ()):
        value = _signed(fact.value)
        if value is not None:
            wet.add(max(0.0, min(1.0, value)), Basis.TOKEN, "you set its rainfall",
                    quote=fact.value or "", source="fact:rainfall")
    for fact in out_facts.get((entity.id, "climate"), ()):
        temps, wets = _read_climate(fact.value or "")
        if temps is not None:
            warm.add(temps, Basis.PROSE_PROP, f'you wrote "{fact.value}"',
                     quote=fact.value or "", source="fact:climate")
        if wets is not None:
            wet.add(wets, Basis.PROSE_PROP, f'you wrote "{fact.value}"',
                    quote=fact.value or "", source="fact:climate")

    warmth, wetness = warm.settled(), wet.settled()
    # The writer's own two words disagreeing is the interesting case, and the thing this
    # replaced could not represent at all: it averaged them and said nothing.
    dry = wetness.value < 0.2
    cold = warmth.value < -0.3
    mix = dict(terrain.value)
    if dry and mix.get("marsh"):
        findings.append(note(
            "contradiction",
            f"{entity.name} is described as marsh and as dry. The map has drawn the "
            "marsh, because that is the thing that shows",
            subjects=(entity.name,), quotes=(wetness.quote, terrain.quote)))
    if cold and mix.get("desert") and wetness.value < 0.1:
        findings.append(note(
            "contradiction",
            f"{entity.name} reads as a cold desert. That is a real place — Antarctica "
            "is one — but if you meant hot sand, say so in the climate",
            subjects=(entity.name,), quotes=(warmth.quote,)))
    return warmth, wetness


def _read_climate(text: str) -> tuple[float | None, float | None]:
    temps: list[float] = []
    wets: list[float] = []
    for word, (temp, wet) in sorted(CLIMATE_WORDS.items()):
        if _find_word(text, word) is None:
            continue
        if temp is not None:
            temps.append(temp)
        if wet is not None:
            wets.append(wet)
    return (sum(temps) / len(temps) if temps else None,
            sum(wets) / len(wets) if wets else None)


def _sea_facing(entity, terrain, mentions) -> Reading:
    """How much of this country meets the sea — a share, not a yes or no.

    A march with one anchorage and a march that is all coast are not the same place, and
    a boolean cannot tell them apart. What it decides is whether the map may put a port
    here at all, which is the writer's to say (§66).
    """
    mix = dict(terrain.value)
    share = min(1.0, mix.get("coast", 0.0) + mix.get("ocean", 0.0))
    if share:
        return known(max(0.6, share), Basis.PROSE_PROP,
                     "you described it as reaching the sea", quote=terrain.quote)
    for mention in mentions.get(entity.id, ()):
        if mention.head in ("coast", "shore", "bay", "harbour", "harbor", "anchorage",
                            "estuary", "sea"):
            return known(0.7, Basis.SUMMARY,
                         f'your description of it says "{mention.surface}"',
                         quote=mention.sentence)
    text = f"{entity.name} {entity.summary or ''}".lower()
    if any(word in text for word in SEA_WORDS):
        return known(0.6, Basis.MENTION, "the words you used for it reach the sea",
                     quote=entity.summary or "")
    return unstated(0.0, "nothing you wrote about it reaches the sea")


def _with_shape_role(region: RegionReading, borders) -> RegionReading:
    """Whether this region is a neck, a cape, an island or plain country.

    Straight out of the border graph: an articulation point is a province whose removal
    cuts the kingdom in two, which is exactly what a neck is. Nobody had this before, so
    the coastline has never known where to pinch.
    """
    if borders.is_neck(region.key):
        role = "neck"
    elif not borders.neighbours(region.key):
        role = "island"
    elif len(borders.neighbours(region.key)) == 1:
        role = "cape"
    else:
        role = "core"
    return RegionReading(**{**region.__dict__, "shape_role": role})


# ---- the rest of the world ---------------------------------------------------


def _settlements(alive, by_id, keys, out_facts, in_facts, geometry, mentions,
                 at) -> tuple[SettlementReading, ...]:
    rows: list[SettlementReading] = []
    wanted = ("settlement", "holding", "site")
    for entity in sorted((e for e in alive.values() if e.type_key in wanted),
                         key=lambda e: (e.name, e.id)):
        key = keys[entity.id]
        seats = tuple(sorted(
            keys[f.subject_id] for predicate in ("based_in", "seat_of")
            for f in in_facts.get((entity.id, predicate), ())
            if f.subject_id in by_id and by_id[f.subject_id].type_key in GROUP_TYPES))
        rows.append(SettlementReading(
            key=key, name=entity.name, entity_id=entity.id, type_key=entity.type_key,
            region_key=_one_object(entity, out_facts, "located_in", keys),
            rank=_rank_of(entity, out_facts),
            population=_population_of(entity, out_facts),
            seat_of=seats, founded=entity.exists_from, ended=entity.exists_to,
            port=_port_of(entity, mentions),
            authored_point=_point(geometry, entity.id),
            mentions=mentions.get(entity.id, ()),
        ))
    return tuple(rows)


def _rank_of(entity, out_facts) -> Reading:
    for fact in out_facts.get((entity.id, "settlement_type"), ()):
        if fact.value:
            return known(fact.value, Basis.TOKEN, f"you called it a {fact.value}",
                         quote=fact.value, source="fact:settlement_type")
    return unstated("town", "you did not say how big it is")


def _port_of(entity, mentions) -> Reading:
    for mention in mentions.get(entity.id, ()):
        if mention.head in ("harbour", "harbor", "anchorage", "mouth", "estuary"):
            return known(True, Basis.SUMMARY,
                         f'your description of it says "{mention.surface}"',
                         quote=mention.sentence)
    return unstated(False)


def _houses(alive, by_id, keys, out_facts) -> tuple[HouseReading, ...]:
    rows: list[HouseReading] = []
    for entity in sorted((e for e in alive.values() if e.type_key in GROUP_TYPES),
                         key=lambda e: (e.name, e.id)):
        seat = seat_region = None
        active: list[Key] = []
        for predicate in ("based_in", "active_in"):
            for fact in out_facts.get((entity.id, predicate), ()):
                target = by_id.get(fact.object_id or "")
                if target is None:
                    continue
                if target.type_key in ("settlement", "holding", "site"):
                    if predicate == "based_in" and seat is None:
                        seat = keys[target.id]
                elif target.type_key == "region":
                    if predicate == "based_in" and seat_region is None:
                        seat_region = keys[target.id]
                    active.append(keys[target.id])
        held = {predicate: tuple(sorted({
            keys[fact.object_id]
            for fact in out_facts.get((entity.id, predicate), ())
            if fact.object_id in keys}))
            for predicate in (*AUTHORITIES, "claims")}
        rows.append(HouseReading(
            key=keys[entity.id], name=entity.name, entity_id=entity.id,
            type_key=entity.type_key, seat_key=seat, seat_region_key=seat_region,
            active_region_keys=tuple(sorted(set(active))),
            liege_key=_one_object(entity, out_facts, "vassal_of", keys),
            # What the layout stages use — where a house has a legal or practical
            # interest — kept as the union it always was, with the four authorities
            # beside it for anything that needs to tell them apart.
            holds_keys=tuple(sorted(set(held["legally_owns"])
                                    | set(held["administers"]))),
            owns_keys=held["legally_owns"],
            administers_keys=held["administers"],
            occupies_keys=held["occupies"],
            taxes_keys=held["taxes"],
            claims_keys=held["claims"]))
    return tuple(_with_depth(rows))


def _with_depth(rows: list[HouseReading]) -> list[HouseReading]:
    """How far each house sits below the crown, so seats are placed in that order."""
    liege = {r.key: r.liege_key for r in rows}
    out: list[HouseReading] = []
    for row in rows:
        depth, here, seen = 0, row.key, {row.key}
        while liege.get(here) and liege[here] not in seen:
            here = liege[here]
            seen.add(here)
            depth += 1
        out.append(HouseReading(**{**row.__dict__, "depth": depth}))
    return out


def _waters(alive, by_id, keys, out_facts, geometry) -> tuple[WaterReading, ...]:
    rows: list[WaterReading] = []
    for entity in sorted((e for e in alive.values() if e.type_key == "waterway"),
                         key=lambda e: (e.name, e.id)):
        through = tuple(
            keys[f.object_id] for f in out_facts.get((entity.id, "flows_through"), ())
            if f.object_id in keys)
        rows.append(WaterReading(
            key=keys[entity.id], name=entity.name, entity_id=entity.id,
            kind=_water_kind(entity), through_keys=through,
            navigable=_flag(entity, out_facts, "navigable"),
            authored_line=_line(geometry, entity.id)))
    return tuple(rows)


def _water_kind(entity) -> str:
    text = f"{entity.name} {entity.summary or ''}".lower()
    for word, kind in sorted(WATER_KINDS.items()):
        if _find_word(text, word) is not None:
            return kind
    return "river"


def _routes(alive, by_id, keys, out_facts, geometry) -> tuple[RouteReading, ...]:
    rows: list[RouteReading] = []
    for entity in sorted((e for e in alive.values()
                          if e.type_key in ("road", "trade_route")),
                         key=lambda e: (e.name, e.id)):
        ends = tuple(sorted({
            keys[f.object_id] for f in out_facts.get((entity.id, "connects"), ())
            if f.object_id in keys}))
        goods = tuple(sorted({
            (by_id[f.object_id].name if f.object_id in by_id else f.value or "")
            for predicate in ("carries", "exports", "trades_with")
            for f in out_facts.get((entity.id, predicate), ())} - {""}))
        rows.append(RouteReading(
            key=keys[entity.id], name=entity.name, entity_id=entity.id,
            kind=entity.type_key, endpoint_keys=ends, goods=goods,
            authored_line=_line(geometry, entity.id)))
    return tuple(rows)


def _resources(alive, by_id, keys, out_facts, in_facts) -> tuple[ResourceReading, ...]:
    """What the ground gives, from the writer's own word for it.

    Both shapes they use: a `resource` entity a region `produces`, and a bare note on the
    region saying "iron". The second is how most people write it, and it was read as an
    opaque string before this.
    """
    rows: list[ResourceReading] = []
    seen: set[tuple[str, str]] = set()
    for entity in sorted((e for e in alive.values() if e.type_key == "resource"),
                         key=lambda e: (e.name, e.id)):
        kind = RESOURCE_KINDS.get(entity.name.strip().lower())
        if not kind:
            continue
        where = tuple(sorted({
            keys[f.subject_id] for f in in_facts.get((entity.id, "produces"), ())
            if f.subject_id in keys}))
        rows.append(ResourceReading(
            key=keys[entity.id], name=entity.name, entity_id=entity.id, kind=kind,
            word=entity.name.strip().lower(), strength=None, region_keys=where,
            basis=Basis.TOKEN, because=f"you gave {entity.name} to that country"))
        seen.add((kind, entity.name.strip().lower()))

    for entity in sorted((e for e in alive.values() if e.type_key == "region"),
                         key=lambda e: (e.name, e.id)):
        for fact in out_facts.get((entity.id, "note"), ()):
            word = (fact.value or "").strip().lower()
            kind = RESOURCE_KINDS.get(word)
            if not kind or (kind, word) in seen:
                continue
            rows.append(ResourceReading(
                key=f"resource/{word}", name=word, entity_id=None, kind=kind,
                word=word, strength=fact.strength,
                region_keys=(keys[entity.id],), basis=Basis.PROSE_PROP,
                because=f'you noted "{word}" against {entity.name}'))
            seen.add((kind, word))
    return tuple(sorted(rows, key=lambda r: (r.kind, r.key)))


def _titles(world, by_id, keys, at) -> tuple[TitleReading, ...]:
    """Who holds what, on the day asked for. The political map has never known."""
    rows: list[TitleReading] = []
    for title in world.titles():
        holder = world.title_holder_on(title.id, at) if at is not None else None
        who = by_id.get(holder or "")
        rows.append(TitleReading(
            key=key_for("title", title.name), name=title.name,
            territory_key=keys.get(title.territory_id or ""),
            holder_key=keys.get(holder or ""), rank=title.rank,
            holder_name=who.name if who else ""))
    return tuple(sorted(rows, key=lambda t: (-t.rank, t.key)))


def _events(world, keys, at) -> tuple[EventReading, ...]:
    rows: list[EventReading] = []
    for event in world.events():
        if at is not None and event.start_day is not None and event.start_day > at:
            continue
        # From what the event *is* and what it is called, not from its summary. The
        # Peace of Millbrook's summary mentions the war it ended, and reading that made
        # a treaty destructive.
        kind = (getattr(event, "type_key", "event") or "event").lower()
        text = f"{kind} {event.name}".lower()
        rows.append(EventReading(
            key=key_for("event", event.name), name=event.name, entity_id=event.id,
            kind=kind,
            day=event.start_day,
            year_text=_year_text(world, event.start_day),
            destructive=any(word in text for word in DESTRUCTION_WORDS),
            place_key=keys.get(getattr(event, "location_id", None) or ""),
            quote=event.name))
    return tuple(sorted(rows, key=lambda e: (e.day if e.day is not None else 1 << 62,
                                             e.key)))


def _year_text(world, day: int | None) -> str:
    if day is None:
        return ""
    try:
        return world.calendar.format(day)
    except Exception:
        return str(day)


def _seasons(world) -> tuple[str, ...]:
    seasons = getattr(getattr(world, "calendar", None), "seasons", ()) or ()
    return tuple(getattr(s, "name", str(s)) for s in seasons)


# ---- small readers -----------------------------------------------------------


def _population_of(entity, out_facts) -> Reading:
    for fact in out_facts.get((entity.id, "population"), ()):
        digits = "".join(ch for ch in (fact.value or "") if ch.isdigit())
        if digits:
            return known(int(digits), Basis.TOKEN,
                         f"you gave it {int(digits):,} people",
                         quote=fact.value or "", source="fact:population")
    return unstated(0, "you did not say how many people live there")


def _magnitude(entity, out_facts, predicate) -> Reading:
    scale = {"none": 0.0, "low": 0.25, "medium": 0.5, "high": 0.75, "very_high": 1.0}
    for fact in out_facts.get((entity.id, predicate), ()):
        value = scale.get((fact.value or fact.strength or "").lower())
        if value is not None:
            return known(value, Basis.TOKEN, f"you called its {predicate} {fact.value}",
                         quote=fact.value or "", source=f"fact:{predicate}")
    return unstated(0.5)


def _flag(entity, out_facts, predicate) -> Reading:
    for fact in out_facts.get((entity.id, predicate), ()):
        value = (fact.value or "").strip().lower()
        if value in ("true", "yes", "1"):
            return known(True, Basis.TOKEN, f"you marked it {predicate}")
        if value in ("false", "no", "0"):
            return known(False, Basis.TOKEN, f"you marked it not {predicate}")
    return unstated(False)


def _one_object(entity, out_facts, predicate, keys) -> Key | None:
    for fact in out_facts.get((entity.id, predicate), ()):
        if fact.object_id in keys:
            return keys[fact.object_id]
    return None


def _resource_keys(entity, out_facts, keys) -> tuple[Key, ...]:
    out: set[Key] = set()
    for fact in out_facts.get((entity.id, "produces"), ()):
        if fact.object_id in keys:
            out.add(keys[fact.object_id])
    for fact in out_facts.get((entity.id, "note"), ()):
        word = (fact.value or "").strip().lower()
        if word in RESOURCE_KINDS:
            out.add(f"resource/{word}")
    return tuple(sorted(out))


def _signed(text: str | None) -> float | None:
    try:
        return float((text or "").strip())
    except (TypeError, ValueError):
        return None


def _ring(geometry, entity_id) -> tuple[tuple[float, float], ...] | None:
    for shape in geometry.get(entity_id, ()):
        if shape.kind == "polygon" and not _is_generated(shape):
            rings = shape.coordinates
            if rings and rings[0]:
                return tuple((float(p[0]), float(p[1])) for p in rings[0])
    return None


def _line(geometry, entity_id) -> tuple[tuple[float, float], ...] | None:
    for shape in geometry.get(entity_id, ()):
        if shape.kind == "line" and not _is_generated(shape):
            return tuple((float(p[0]), float(p[1])) for p in shape.coordinates)
    return None


def _point(geometry, entity_id) -> tuple[float, float] | None:
    for shape in geometry.get(entity_id, ()):
        if shape.kind == "point" and not _is_generated(shape):
            place = shape.coordinates
            return (float(place[0]), float(place[1]))
    return None


def _is_generated(shape) -> bool:
    return bool((shape.style or {}).get("generated_by"))
