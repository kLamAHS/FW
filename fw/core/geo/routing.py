"""Travel and logistics (spec §22).

The writer should be able to ask "how long does it take to travel from Greyhaven to
Rennford?" and get an answer that accounts for distance, road quality, terrain, season,
transport type and party size — and the same machinery then answers §24's military
questions ("which route would an invading army use?") and feeds the continuity check in
§46 that catches a journey the scene's timeline does not allow.

Dijkstra over the route network, in plain Python. The network is small — even a densely
mapped world has thousands of segments, not millions — and keeping it dependency-free
matters more here than the constant factor.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from fw.core.model.records import RouteSegment
from fw.core.world import World


@dataclass(frozen=True)
class TransportProfile:
    """How a traveller moves (§22).

    `base_speed` is in world distance units per day on good flat road. Terrain multipliers
    scale it. `water` marks a profile that travels by water and cannot use roads (and vice
    versa), which is why a barge and a courier get genuinely different routes rather than
    the same route at different speeds.
    """

    key: str
    label: str
    base_speed: float
    water: bool = False
    terrain: dict[str, float] = field(default_factory=dict)
    description: str = ""

    def speed_on(self, terrain: str, quality: float) -> float:
        multiplier = self.terrain.get(terrain, 0.0)
        return self.base_speed * multiplier * max(quality, 0.05)


LAND = {"plain": 1.0, "hill": 0.75, "mountain": 0.45, "forest": 0.7,
        "marsh": 0.5, "desert": 0.8, "water": 0.0}
WATER = {"water": 1.0}

# The ways that are sailed rather than walked. Named here because this is where the
# distinction is *used* — a medium missing from this set is routed over dry ground,
# scores zero against water, and drops out of every journey without an error anywhere.
SAILED = ("river", "sea", "canal")

PROFILES: dict[str, TransportProfile] = {
    p.key: p
    for p in (
        TransportProfile("messenger", "Messenger", 60, terrain=LAND,
                         description="A rider with remounts, carrying nothing but words."),
        TransportProfile("horse", "Rider", 40, terrain=LAND),
        TransportProfile("carriage", "Carriage", 25, terrain={**LAND, "mountain": 0.25}),
        TransportProfile("walking", "On foot", 20, terrain=LAND),
        TransportProfile("wagon", "Wagon train", 15, terrain={**LAND, "mountain": 0.25},
                         description="Goods overland. Slow, and stopped by bad ground."),
        TransportProfile("army", "Army on the march", 12,
                         terrain={**LAND, "hill": 0.6, "mountain": 0.3},
                         description="Includes baggage; §24's invasion routes use this."),
        TransportProfile("barge", "River barge", 30, water=True, terrain=WATER),
        TransportProfile("ship", "Sailing ship", 55, water=True, terrain=WATER),
    )
}


@dataclass
class Leg:
    from_id: str
    to_id: str
    segment_id: str
    medium: str
    length: float
    days: float


@dataclass
class Route:
    """A computed journey, with its reasoning attached (§67)."""

    origin_id: str
    destination_id: str
    profile: TransportProfile
    days: float
    legs: list[Leg] = field(default_factory=list)
    season: str | None = None
    day: int | None = None

    @property
    def distance(self) -> float:
        return sum(leg.length for leg in self.legs)

    @property
    def path(self) -> list[str]:
        if not self.legs:
            return [self.origin_id]
        return [self.legs[0].from_id] + [leg.to_id for leg in self.legs]

    def explain(self, world: World) -> str:
        def name(i: str) -> str:
            e = world.get_entity(i)
            return e.name if e else i

        if not self.legs:
            return f"No route by {self.profile.label.lower()}."
        head = (f"{name(self.origin_id)} to {name(self.destination_id)} "
                f"by {self.profile.label.lower()}: "
                f"{self.days:.1f} days over {self.distance:.0f} units")
        if self.season:
            head += f" (in {self.season})"
        steps = [f"    {name(leg.from_id)} to {name(leg.to_id)} — "
                 f"{leg.length:.0f} units by {leg.medium}, {leg.days:.1f} days"
                 for leg in self.legs]
        return head + "\n" + "\n".join(steps)


# What danger costs a traveller, as a multiplier on the days a stretch takes.
#
# Two of these four are all the system produces today: the column defaults to "low"
# and the generator writes "moderate" on sea lanes and nothing else. The other two are
# here for a writer, because `danger` is a free text column with no scale behind it
# and no editor — a writer who types "high" on the road through the pass should not be
# told their word is not a word. Anything unrecognised is priced as safe, which is the
# conservative direction: an invented penalty is a lie about the world, where a missing
# one is only a road that is no worse than it looks.
#
# `test_travel.py` asserts every value the GENERATOR can write is in here, because the
# silent 1.0 that makes an unknown word safe is exactly how a danger system dies
# quietly.
DANGER_COST = {"low": 1.0, "moderate": 1.35, "high": 1.8, "extreme": 2.5}


def _stretch_days(segment: RouteSegment, transport: TransportProfile,
                  party_size: int | None) -> float | None:
    """What one stretch costs this traveller, in days. `None` if impassable.

    ONE definition, used both to choose the route and to report it. They were two —
    the Dijkstra weight carried the party-size penalty and the reported leg carried
    only `length / speed` — so `Route.days` and `sum(leg.days)` disagreed whenever a
    large party travelled, which was rare enough to go unnoticed. Pricing danger made
    it every sea voyage on every map, because the generator marks every lane
    "moderate"; and the API serves both numbers, with `explain()` printing them in the
    same paragraph. A journey that shows a reader two different totals is worse than
    one that shows a wrong total.
    """
    speed = transport.speed_on(segment.terrain, segment.quality)
    if speed <= 0:
        return None
    days = segment.length / speed
    # A dangerous road is not a slower road, but it costs a traveller the same way:
    # you go in company, you wait for one, you take the long way round the wood. The
    # generator has been SAYING the router does this since sea lanes were drawn —
    # `pipeline.LANE_DANGER` sets "moderate" under a comment claiming the router
    # "models through quality and danger" — and the router did not. A comment
    # asserting a behaviour the code lacks is worth less than nothing: it stops
    # anyone checking.
    days *= DANGER_COST.get(segment.danger, 1.0)
    if party_size and party_size > 500:
        # A large body of people moves slower than its own marching speed: forage,
        # water and column length all bite. A rough, honest penalty beats a
        # precise-looking model the writer cannot check.
        days *= 1.0 + min(party_size / 10_000, 0.6)
    return days


class Router:
    def __init__(self, world: World) -> None:
        self.world = world
        self.segments = world.route_segments()

    def route(
        self,
        origin_id: str,
        destination_id: str,
        *,
        profile: str = "horse",
        day: int | None = None,
        season: str | None = None,
        party_size: int | None = None,
    ) -> Route | None:
        """The fastest route, or None if none exists under these conditions.

        `day` and `season` are what make this temporal: a road built in 202 cannot carry a
        traveller in 190, and a pass closed by snow is not a route in Darkening. Answering
        "no route" is as useful to a writer as answering "nine days".
        """
        transport = PROFILES.get(profile)
        if transport is None:
            raise ValueError(f"unknown transport profile {profile!r}")

        if season is None and day is not None:
            season = self.world.calendar.season(day)

        adjacency: dict[str, list[tuple[str, float, RouteSegment]]] = {}
        for segment in self.segments:
            if not segment.usable_on(day, season):
                continue
            if transport.water != (segment.medium in SAILED):
                continue
            days = _stretch_days(segment, transport, party_size)
            if days is None:
                continue
            adjacency.setdefault(segment.from_entity_id, []).append(
                (segment.to_entity_id, days, segment))
            adjacency.setdefault(segment.to_entity_id, []).append(
                (segment.from_entity_id, days, segment))

        best: dict[str, float] = {origin_id: 0.0}
        previous: dict[str, tuple[str, RouteSegment]] = {}
        queue: list[tuple[float, str]] = [(0.0, origin_id)]
        visited: set[str] = set()

        while queue:
            cost, node = heapq.heappop(queue)
            if node in visited:
                continue
            visited.add(node)
            if node == destination_id:
                break
            for neighbour, weight, segment in adjacency.get(node, []):
                candidate = cost + weight
                if candidate < best.get(neighbour, float("inf")):
                    best[neighbour] = candidate
                    previous[neighbour] = (node, segment)
                    heapq.heappush(queue, (candidate, neighbour))

        if destination_id not in best:
            return None

        legs: list[Leg] = []
        cursor = destination_id
        while cursor != origin_id:
            prior, segment = previous[cursor]
            legs.append(Leg(
                from_id=prior, to_id=cursor, segment_id=segment.id,
                medium=segment.medium, length=segment.length,
                days=_stretch_days(segment, transport, party_size) or float("inf"),
            ))
            cursor = prior
        legs.reverse()

        return Route(
            origin_id=origin_id, destination_id=destination_id, profile=transport,
            days=best[destination_id], legs=legs, season=season, day=day,
        )

    def travel_time(self, origin_id: str, destination_id: str, **kw) -> float | None:
        route = self.route(origin_id, destination_id, **kw)
        return route.days if route else None

    def reachable_within(self, origin_id: str, days: float, **kw) -> dict[str, float]:
        """Everywhere reachable inside a time budget — the 'who could be here' question.

        Everywhere the routes reach, not every settlement. An island is a place a ship
        can put in at and never a settlement, so a list built from settlements alone
        answered "nowhere" for the one journey a reader most wants timed.
        """
        out: dict[str, float] = {}
        for entity_id in self.places():
            if entity_id == origin_id:
                continue
            time = self.travel_time(origin_id, entity_id, **kw)
            if time is not None and time <= days:
                out[entity_id] = time
        return out

    def places(self) -> tuple[str, ...]:
        """Every entity a route can start or end at, in a stated order."""
        ends = {segment.from_entity_id for segment in self.segments}
        ends |= {segment.to_entity_id for segment in self.segments}
        ends |= {entity.id for entity in self.world.entities("settlement")}
        named = [(self.world.get_entity(eid), eid) for eid in ends]
        return tuple(eid for entity, eid in
                     sorted(named, key=lambda pair: (
                         pair[0].name if pair[0] else "", pair[1]))
                     if entity is not None)
