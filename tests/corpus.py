"""The adversarial world corpus (V2 §47).

Every map test used to invent its own little world — seven files, seven slightly
different builders, and every renderer decision quietly tuned against the one seeded
world. This is the shared cast: the same builders behind one signature, plus the
torture worlds the brief asks for by name. A change that survives `everything()` has
been looked at from more angles than Renn.

Callers own the worlds they ask for: every constructor returns an open `World` and
closing it is the caller's job (a fixture's `yield`/`close`, usually).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from fw.core.calendar.kernel import GREGORIAN
from fw.core.seed.renn import seed_renn
from fw.core.world import World

# (name, terrain prose, climate prose, population). The four facts every region
# builder in the old fixtures set, in the order they all set them.
Spec = tuple[str, str, str, str]

TERRAINS = ("mountains and high crags", "coast and harbour", "deep forest and hills",
            "steppe and dry grassland", "river plain and meadow", "marsh and fen")
CLIMATES = ("cold, heavy snow", "warm and humid", "hot, arid", "temperate, rain")


def world_of(specs: Sequence[Spec], *, name: str = "Corpus",
             borders: str | Iterable[tuple[int, int]] = "chain") -> World:
    """The one region builder. `borders` is "chain", "cycle", or explicit pairs."""
    w = World.create(name=name, calendar=GREGORIAN)
    ids = []
    for region_name, terrain, climate, population in specs:
        region = w.add_entity("region", region_name)
        w.assert_fact(region.id, "terrain", value=terrain)
        if climate:
            w.assert_fact(region.id, "climate", value=climate)
        if population:
            w.assert_fact(region.id, "population", value=population)
        ids.append(region.id)
    if borders == "chain":
        pairs = list(zip(range(len(ids) - 1), range(1, len(ids)), strict=False))
    elif borders == "cycle":
        pairs = [(i, (i + 1) % len(ids)) for i in range(len(ids))]
    else:
        pairs = list(borders)
    for a, b in pairs:
        w.assert_fact(ids[a], "borders", ids[b])
    return w


def many(count: int, *, name: str = "Many") -> World:
    """`count` regions cycling the terrain and climate tables — the scale dial."""
    return world_of(
        [(f"March {n + 1}", TERRAINS[n % len(TERRAINS)],
          CLIMATES[n % len(CLIMATES)], str(20_000 + 7_000 * (n % 5)))
         for n in range(count)],
        name=name)


def town(w: World, name: str, rank: str, region_id: str) -> str:
    made = w.add_entity("settlement", name)
    w.assert_fact(made.id, "settlement_type", value=rank)
    w.assert_fact(made.id, "located_in", region_id)
    return made.id


def region_ids(w: World) -> list[str]:
    return [e.id for e in w.entities() if e.type_key == "region"]


# ---- the torture worlds (V2 §47) -------------------------------------------


def archipelago() -> World:
    """Many islands and the lanes between them — the island/lane retirement case."""
    return world_of([
        ("The Broken Isles", "isles, skerries and coast", "warm and humid", "30000"),
        ("The Sound", "coast and harbour", "temperate, rain", "60000"),
        ("Wrackholm", "coast, cliffs and heath", "cold, heavy snow", "15000"),
        ("The Outer Banks", "coast and dunes", "warm and humid", "9000"),
    ], name="Archipelago")


def delta() -> World:
    """Dense waterways on flat ground — the hydrology stress case."""
    return world_of([
        ("The Silt", "river plain and delta", "warm and humid", "220000"),
        ("The Fens", "marsh and fen", "temperate, rain", "40000"),
        ("The Headwaters", "forest and river valley", "temperate, rain", "80000"),
        ("The High Ground", "hills and downs", "temperate, rain", "50000"),
    ], name="Delta")


def alps() -> World:
    """Extreme relief: one pass country, three walls of rock."""
    return world_of([
        ("The White Teeth", "mountains and high crags", "cold, heavy snow", "12000"),
        ("The Grey Wall", "mountains and glaciers", "cold, heavy snow", "8000"),
        ("The Kneel", "highland and passes", "cold, heavy snow", "25000"),
        ("The Vale Below", "river plain and meadow", "temperate, rain", "160000"),
    ], name="Alps")


def empire() -> World:
    """Twenty-four regions — the label-density and payload-size ceiling."""
    return many(24, name="Empire")


def frontier() -> World:
    """Sparse, thin facts, tiny populations — the empty-space budget case."""
    return world_of([
        ("The Out-Marches", "steppe and dry grassland", "", "4000"),
        ("Nowhere Much", "plain", "", ""),
        ("The Edge", "badlands and waste", "hot, arid", "2000"),
    ], name="Frontier")


def city_belt() -> World:
    """A dense band of ranked settlements — the label-collision worst case."""
    w = world_of([
        ("The Belt", "river plain and meadow", "temperate, rain", "900000"),
        ("The Hinterland", "forest and hills", "temperate, rain", "120000"),
        ("The Coastward", "coast and harbour", "warm and humid", "300000"),
    ], name="City Belt")
    belt, hinter, coastward = region_ids(w)
    ranks = ("capital", "city", "city", "market town", "town", "town",
             "town", "village", "village", "village", "hamlet", "hamlet")
    for n, rank in enumerate(ranks):
        region = (belt, coastward, hinter)[n % 3]
        town(w, f"{rank.title()} {n + 1}", rank, region)
    return w


def long_coast() -> World:
    """Six coastal marches in a chain — bays, ports, and coast-class variety.

    The two ports are the cabotage case: writer-ranked harbours a coasting lane
    should join, hugging the shore between them.
    """
    w = world_of([
        ("The North Strand", "coast and cliffs", "cold, heavy snow", "40000"),
        ("The Grey Shore", "coast and harbour", "temperate, rain", "90000"),
        ("The Middle Sands", "coast and dunes", "warm and humid", "30000"),
        ("The Salt Marsh", "coast and marsh", "warm and humid", "20000"),
        ("The South Reach", "coast and harbour", "hot, arid", "70000"),
        ("The Last Point", "coast, heath and cliffs", "temperate, rain", "10000"),
    ], name="Long Coast")
    _, grey, _, _, reach, _ = region_ids(w)
    town(w, "Greywick", "port", grey)
    town(w, "Southhaven", "port", reach)
    return w


def civil_war() -> World:
    """Two houses over the same ground — contested rendering and §11's layers."""
    w = world_of([
        ("The Crownlands", "river plain and meadow", "temperate, rain", "400000"),
        ("The Rebel March", "forest and hills", "temperate, rain", "150000"),
        ("The Prize", "coast and harbour", "warm and humid", "200000"),
    ], name="Civil War")
    crown_land, march, prize = region_ids(w)
    seat = town(w, "Kingsport", "capital", crown_land)
    rebel_seat = town(w, "Thornhall", "fortress", march)
    crown = w.add_entity("house", "House Solane")
    rebels = w.add_entity("house", "House Varr")
    w.assert_fact(crown.id, "based_in", seat)
    w.assert_fact(rebels.id, "based_in", rebel_seat)
    w.assert_fact(crown.id, "legally_owns", crown_land)
    w.assert_fact(crown.id, "legally_owns", prize)
    w.assert_fact(rebels.id, "legally_owns", march)
    w.assert_fact(rebels.id, "occupies", prize)      # the war, on the map
    w.assert_fact(rebels.id, "claims", crown_land)
    return w


def ended_region() -> World:
    """A region that stopped existing — dated ground under an undated map."""
    w = world_of([
        ("The Living Land", "river plain and meadow", "temperate, rain", "100000"),
        ("The Drowned March", "coast and marsh", "warm and humid", "30000"),
        ("The High Home", "hills and downs", "temperate, rain", "60000"),
    ], name="Ended")
    drowned = next(e for e in w.entities() if e.name == "The Drowned March")
    w.update_entity(drowned.id, exists_to=w.day(200))
    return w


def authored_lines() -> World:
    """A writer-drawn river and road — geometry kinds no old fixture exercised."""
    w = world_of([
        ("The Penwood", "forest and river valley", "temperate, rain", "80000"),
        ("The Open Vale", "river plain and meadow", "temperate, rain", "120000"),
    ], name="Authored Lines")
    penwood, vale = region_ids(w)
    river = w.add_entity("waterway", "The Inkwater")
    w.add_geometry(river.id, "line",
                   [[120.0, 80.0], [200.0, 160.0], [320.0, 260.0], [430.0, 420.0]],
                   layer="waterways", style={"role": "waterway"})
    a = town(w, "Pennbridge", "town", penwood)
    b = town(w, "Vale End", "town", vale)
    road = w.add_entity("road", "The Writer's Road")
    w.assert_fact(road.id, "connects", a)
    w.assert_fact(road.id, "connects", b)
    w.add_geometry(road.id, "line",
                   [[140.0, 100.0], [260.0, 210.0], [400.0, 380.0]],
                   layer="roads", style={"role": "road"})
    return w


def label_hostile() -> World:
    """Names chosen to hurt the solver: long, accented, and nearly identical."""
    w = world_of([
        ("The Unconscionably Long March of the Endless Eastern Emptiness",
         "steppe and dry grassland", "hot, arid", "50000"),
        ("Åsgardh-upon-Øre", "coast and harbour", "cold, heavy snow", "80000"),
        ("Sørmark", "forest and hills", "cold, heavy snow", "60000"),
        ("Sörmark", "marsh and fen", "temperate, rain", "30000"),
    ], name="Label Hostile")
    first, *_ = region_ids(w)
    town(w, "Llanfairpwllgwyngyllgogerychwyrndrobwll", "city", first)
    return w


# ---- the roll call ----------------------------------------------------------

# What the golden harness iterates. Renn is in the cast because it is the world the
# writer actually meets; everything else is here to stop the renderer being tuned
# against Renn alone (the brief's own warning).
CORPUS: dict[str, Callable[[], World]] = {
    "renn": seed_renn,
    "archipelago": archipelago,
    "delta": delta,
    "alps": alps,
    "empire": empire,
    "frontier": frontier,
    "city_belt": city_belt,
    "long_coast": long_coast,
    "civil_war": civil_war,
    "ended_region": ended_region,
    "authored_lines": authored_lines,
    "label_hostile": label_hostile,
}
