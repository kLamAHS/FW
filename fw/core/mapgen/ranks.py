"""What kind of place a settlement is, decided once.

The map asks this in two quite different situations and must answer the same way in
both. The generator asks it while planning, with the writer's whole world read into a
`WorldReading`. `GET /api/map` asks it about a town the writer placed by hand on a
world that was never generated, where there is no reading and no plan — only the facts
they wrote.

Before this existed the second caller had no answer at all, so an authored settlement
reached the client with no rank, `cartography._icon_band` fell through to its
"regional" default, and the client — which opens at the "world" band, where deeper
bands draw at opacity zero — showed the writer none of their own towns. The seeded
example world opened on three provinces, a river, two roads and not one place.

The rule itself is the one the generator already had, and its own docstring records
this same class of mistake being fixed once before: "they wrote `settlement_type` on
every town in the example world — capital, port, fortress, market town — and the map
read the population instead and called all six of them towns."
"""

from __future__ import annotations

# Where the writer has not said, the population says. Thresholds unchanged from
# `MapGenerator._rank_for_population`, which is now one of this function's callers.
CITY = 20_000
TOWN = 4_000


def rank_of(stated: str | None, population: int | None,
            fallback: str = "town") -> str:
    """The writer's own word for a place; failing that, what its size implies.

    Their word wins outright and is never second-guessed: a writer who calls a place of
    six hundred people a city has said something about the world, not made an error.
    """
    if stated:
        return stated.strip().lower()
    if population:
        return rank_for_population(int(population))
    return fallback


def rank_for_population(people: int) -> str:
    if people >= CITY:
        return "city"
    if people >= TOWN:
        return "town"
    return "village"
