"""The writer's world, read once, in the shapes the map needs.

Everything below is keyed by a `Key` — `"region/the-northmarch"` — and never by an entity
id. That is not tidiness. Entity ids are ULIDs, minted per world, so two copies of the
same world have different ones; anything that reaches a seed, a name, a feature key or the
plan's digest has to be derived from what the writer wrote instead, or the same world
generates two different maps and a golden test means nothing.

Every value that could have come from more than one place is a `Reading`, which carries
where it came from and can quote the sentence. Everything is a tuple, sorted, because a
list that came back in SQLite's order is a map that changes when rows are added.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from fw.core.mapgen.findings import Finding
from fw.core.mapgen.source.claims import Basis, Reading, unstated
from fw.core.mapgen.source.graph import BorderGraph
from fw.core.mapgen.source.scan import Mention

Key = str
Point = tuple[float, float]


def key_for(type_key: str, name: str) -> Key:
    """A stable, id-free handle for a thing the writer named."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return f"{type_key}/{slug or 'unnamed'}"


@dataclass(frozen=True)
class ResourceReading:
    key: Key
    name: str
    entity_id: str | None
    kind: str                            # arable | pasture | timber | stone | ore | fish
    word: str                            # the writer's own word for it
    strength: str | None
    region_keys: tuple[Key, ...]
    basis: Basis
    because: str


@dataclass(frozen=True)
class RegionReading:
    key: Key
    name: str
    entity_id: str | None
    terrain_mix: Reading[tuple[tuple[str, float], ...]]
    temperature: Reading[float]
    moisture: Reading[float]
    population: Reading[int]
    sea_facing: Reading[float]           # 0..1, NOT a bool: a coast is a matter of degree
    defensibility: Reading[float]
    shape_role: str                      # core | neck | cape | island
    founded: int | None = None           # `exists_from`: when the march came to be
    ended: int | None = None
    resource_keys: tuple[Key, ...] = ()
    parent_key: Key | None = None
    settlement_keys: tuple[Key, ...] = ()
    neighbour_keys: tuple[Key, ...] = ()
    mentions: tuple[Mention, ...] = ()
    authored_ring: tuple[Point, ...] | None = None

    @property
    def mix(self) -> dict[str, float]:
        return dict(self.terrain_mix.value)

    @property
    def dominant(self) -> str:
        mix = self.terrain_mix.value
        return max(mix, key=lambda pair: (pair[1], pair[0]))[0] if mix else "plain"

    @property
    def coastal(self) -> bool:
        """The old boolean, for everything that still asks in those terms."""
        return self.sea_facing.value >= 0.5


@dataclass(frozen=True)
class SettlementReading:
    key: Key
    name: str
    entity_id: str | None
    type_key: str                        # settlement | holding | site
    region_key: Key | None
    rank: Reading[str]
    population: Reading[int]
    seat_of: tuple[Key, ...] = ()        # houses and orders based_in here
    founded: int | None = None
    ended: int | None = None
    port: Reading[bool] = field(default_factory=lambda: unstated(False))
    authored_point: Point | None = None
    mentions: tuple[Mention, ...] = ()

    @property
    def held_by(self) -> Key | None:
        return self.seat_of[0] if self.seat_of else None


@dataclass(frozen=True)
class WaterReading:
    key: Key
    name: str
    entity_id: str | None
    kind: str                            # river | lake | bay | sea | strait
    through_keys: tuple[Key, ...] = ()   # settlements it flows through, in order
    region_keys: tuple[Key, ...] = ()
    navigable: Reading[bool] = field(default_factory=lambda: unstated(False))
    authored_line: tuple[Point, ...] | None = None


@dataclass(frozen=True)
class RouteReading:
    key: Key
    name: str
    entity_id: str | None
    kind: str                            # road | trade_route
    endpoint_keys: tuple[Key, ...] = ()  # from `connects`
    goods: tuple[str, ...] = ()
    authored_line: tuple[Point, ...] | None = None


# §11's sharpest distinction, kept as four separate facts because they may name four
# separate houses at once. Greyhaven, in the example world, is exactly that: House Marr
# owns it in law, House Veyne runs it day to day, the Crown taxes it, and House Orren
# claims it outright. A map that collapses those to one fill has thrown away the most
# interesting thing anyone ever wrote about the town.
AUTHORITIES = ("legally_owns", "administers", "occupies", "taxes")


@dataclass(frozen=True)
class HouseReading:
    key: Key
    name: str
    entity_id: str | None
    type_key: str                        # house | order | guild | company | tribe
    seat_key: Key | None = None          # based_in -> a settlement
    seat_region_key: Key | None = None
    active_region_keys: tuple[Key, ...] = ()
    liege_key: Key | None = None
    depth: int = 0                       # 0 = answers to nobody; seats placed in order
    holds_keys: tuple[Key, ...] = ()     # legally_owns | administers, for the layout
    owns_keys: tuple[Key, ...] = ()      # legally_owns
    administers_keys: tuple[Key, ...] = ()
    occupies_keys: tuple[Key, ...] = ()
    taxes_keys: tuple[Key, ...] = ()
    claims_keys: tuple[Key, ...] = ()    # not an authority — a dispute about one

    def under(self, authority: str) -> tuple[Key, ...]:
        return {"legally_owns": self.owns_keys,
                "administers": self.administers_keys,
                "occupies": self.occupies_keys,
                "taxes": self.taxes_keys,
                "claims": self.claims_keys}.get(authority, ())


@dataclass(frozen=True)
class Authority:
    """Who holds one place, under each of the four authorities separately."""

    place_key: Key
    owns: Key | None = None
    administers: Key | None = None
    occupies: Key | None = None
    taxes: Key | None = None
    claims: tuple[Key, ...] = ()

    @property
    def effective(self) -> Key | None:
        """Who is actually in charge, which is not always who owns it.

        An army in the streets outranks a charter, and a steward who has run the place
        for thirty years outranks an absent owner. This is the one the political fill
        colours by; the other three are how it explains itself.
        """
        return self.occupies or self.administers or self.owns

    @property
    def held_by(self) -> tuple[Key, ...]:
        return tuple(sorted({k for k in (self.owns, self.administers,
                                         self.occupies, self.taxes) if k}))

    @property
    def layered(self) -> bool:
        """More than one house has some authority here — the interesting case."""
        return len(self.held_by) > 1

    @property
    def disputed(self) -> bool:
        """Somebody claims it who does not hold it under any authority."""
        return any(claim not in self.held_by for claim in self.claims)


@dataclass(frozen=True)
class TitleReading:
    key: Key
    name: str
    territory_key: Key | None
    holder_key: Key | None               # on the day asked for
    rank: int
    holder_name: str = ""                # so a label does not have to unpick a key


@dataclass(frozen=True)
class EventReading:
    key: Key
    name: str
    entity_id: str | None
    kind: str                            # war | battle | treaty | event
    day: int | None
    year_text: str                       # already through the world's own calendar
    destructive: bool
    place_key: Key | None
    quote: str


@dataclass(frozen=True)
class WorldReading:
    """The writer's world, read once, id-light, fully explained.

    Nothing downstream of this may touch `World` again. That is the rule the whole
    package exists for: a stage that reads the world itself is a stage whose answer
    depends on when it ran and what else had been written.
    """

    world_name: str
    at: int | None
    north: str
    branch: str
    regions: tuple[RegionReading, ...] = ()
    settlements: tuple[SettlementReading, ...] = ()
    waters: tuple[WaterReading, ...] = ()
    routes: tuple[RouteReading, ...] = ()
    houses: tuple[HouseReading, ...] = ()
    titles: tuple[TitleReading, ...] = ()
    events: tuple[EventReading, ...] = ()
    resources: tuple[ResourceReading, ...] = ()
    borders: BorderGraph | None = None
    findings: tuple[Finding, ...] = ()
    names: tuple[str, ...] = ()          # every name in the world, for the gazetteer
    # (type_key, name) for everything the writer named, which is what the namer learns
    # its syllables from. Kept here so the naming pass does not have to open the world
    # again — the seventh traversal, and the last one.
    corpus: tuple[tuple[str, str], ...] = ()
    seasons: tuple[str, ...] = ()
    # How often each place carries the story: scenes the writer set there, by key.
    # The one narrative signal the map reads — an event is a fact about the world,
    # a scene is the writer spending pages in a place.
    presence: Mapping[Key, int] = field(default_factory=dict)

    def region(self, key: Key) -> RegionReading | None:
        return self._first(self.regions, key)

    def settlement(self, key: Key) -> SettlementReading | None:
        return self._first(self.settlements, key)

    def house(self, key: Key) -> HouseReading | None:
        return self._first(self.houses, key)

    def settlements_in(self, region: Key) -> tuple[SettlementReading, ...]:
        return tuple(s for s in self.settlements if s.region_key == region)

    def seat_of(self, settlement: Key) -> HouseReading | None:
        """Whose hall this is — the thing a castle has never known about itself."""
        for house in self.houses:
            if house.seat_key == settlement:
                return house
        return None

    def holder_of(self, region: Key) -> TitleReading | None:
        for title in self.titles:
            if title.territory_key == region and title.holder_key:
                return title
        return None

    def events_at(self, place: Key) -> tuple[EventReading, ...]:
        return tuple(e for e in self.events if e.place_key == place)

    def authority_over(self, place: Key) -> Authority:
        """Who holds this ground, kept as §11's four separate answers.

        Iterated in the houses' own sorted order, so where two houses assert the same
        authority over the same place the earlier name wins and does so identically on
        every run — which is the difference between a stable map and one that changes
        colour when a row is added.
        """
        found: dict[str, Key] = {}
        claims: list[Key] = []
        for house in self.houses:
            for authority in AUTHORITIES:
                if place in house.under(authority):
                    found.setdefault(authority, house.key)
            if place in house.claims_keys:
                claims.append(house.key)
        return Authority(place_key=place, owns=found.get("legally_owns"),
                         administers=found.get("administers"),
                         occupies=found.get("occupies"), taxes=found.get("taxes"),
                         claims=tuple(sorted(set(claims))))

    def authorities(self) -> Mapping[Key, Authority]:
        """Every place anybody holds, so a fill can be built in one pass."""
        places = {key for house in self.houses for authority in AUTHORITIES
                  for key in house.under(authority)}
        places |= {key for house in self.houses for key in house.claims_keys}
        return {place: self.authority_over(place) for place in sorted(places)}

    @staticmethod
    def _first(rows, key):
        for row in rows:
            if row.key == key:
                return row
        return None

    def fingerprint(self) -> str:
        """A digest of everything read, with the ids stripped out.

        Two identically built worlds must fingerprint the same. That is what makes it
        possible to say "this plan is still the plan for this world" without comparing
        entity ids, which differ between two copies of the same thing.
        """
        parts: list[str] = [self.world_name, str(self.at), self.north]
        for region in self.regions:
            parts.append(f"R|{region.key}|{region.terrain_mix.value}|"
                         f"{region.temperature.value:.6f}|{region.moisture.value:.6f}|"
                         f"{region.population.value}|{region.sea_facing.value:.4f}")
        for place in self.settlements:
            parts.append(f"S|{place.key}|{place.region_key}|{place.rank.value}|"
                         f"{place.population.value}|{place.founded}|{place.authored_point}")
        for water in self.waters:
            parts.append(f"W|{water.key}|{water.kind}|{water.through_keys}")
        for route in self.routes:
            parts.append(f"T|{route.key}|{route.kind}|{route.endpoint_keys}")
        for house in self.houses:
            parts.append(f"H|{house.key}|{house.seat_key}|{house.liege_key}|"
                         f"{house.owns_keys}|{house.administers_keys}|"
                         f"{house.occupies_keys}|{house.taxes_keys}")
        for title in self.titles:
            parts.append(f"L|{title.key}|{title.territory_key}|{title.holder_key}")
        for event in self.events:
            parts.append(f"E|{event.key}|{event.day}|{event.place_key}")
        for stuff in self.resources:
            parts.append(f"P|{stuff.key}|{stuff.kind}|{stuff.region_keys}")
        if self.borders:
            parts.append("B|" + "|".join(f"{e.a}~{e.b}" for e in self.borders.edges))
        return hashlib.blake2b("\n".join(parts).encode("utf-8"),
                               digest_size=16).hexdigest()

    def by_entity(self) -> Mapping[str, Key]:
        """Entity id to key, for the one place that has to write geometry back."""
        out: dict[str, Key] = {}
        for rows in (self.regions, self.settlements, self.waters, self.routes,
                     self.houses, self.resources):
            for row in rows:
                if row.entity_id:
                    out[row.entity_id] = row.key
        return out
