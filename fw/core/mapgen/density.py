"""How worked the land is, as one field (V2 §7, §14).

A real map does not go from wilderness to city wall in one cell. Around every old town
there is a country of fields, orchards, mills and cleared woods that took centuries to
make, thinning with distance until the woods win again — and the same gradient decides
which cart tracks are worth drawing, how many labels a neighbourhood can carry, and
where the frontier actually *feels* like one. This module computes that gradient once,
as `develop` ∈ [0, 1] per lattice cell, from three things the generator already knows:

- where the settlements are and how much each rank of place works its country,
- how long each has stood (a town founded a decade ago has not cleared its valley),
- where the traffic runs (a highway drags cultivation along itself).

It is persisted with the other terrain fields so every later consumer — the relief
renderer's farmland, Phase D's label budgets — reads the same answer the plan was made
from. Pure arithmetic throughout: no trig, no exponentials, deterministic to the byte.
"""

from __future__ import annotations

import math

Field = list[list[float]]

# How far a place's work reaches, in lattice cells — a day's walk out and back, not a
# province. The falloff is quadratic, so the last third of the reach is nearly wild.
REACH = 9.0

# How much country each rank of place works. Not the road-traffic weights: a fortress
# out-pulls a village on the road network and is surrounded by less tilled ground than
# one, because a garrison is fed from elsewhere.
WORKED = {
    "capital": 1.0, "city": 0.9, "port": 0.75, "harbour": 0.75, "harbor": 0.75,
    "market town": 0.6, "town": 0.5, "fortress": 0.35, "village": 0.3, "hamlet": 0.18,
}
JUST_A_PLACE = 0.3                 # a rank the table has never heard of

# A highway cell's pull against a capital's own core. Traffic is normalised to the
# busiest cell first, and taken at its square root so a moderately used road still
# shows — raw traffic spans orders of magnitude.
TRAFFIC_WORTH = 0.35

# Days of standing until a place has fully made its country. Roughly three centuries
# in a 365-day year; a place with no founding date is honestly "just there" and counts
# as mature rather than as new (the same reading `built_on=None` gets).
MATURE = 110_000
# What a place works the day it is founded: people arrive with axes, not orchards.
RAW_GROUND = 0.4


def grown(age_days: int | None) -> float:
    """How much of its full reach a place of this age has cleared."""
    if age_days is None:
        return 1.0
    if age_days <= 0:
        return RAW_GROUND
    return RAW_GROUND + (1.0 - RAW_GROUND) * min(age_days / MATURE, 1.0)


def develop(size: int, sea: list[list[bool]],
            seats: list[tuple[tuple[int, int], float, float]],
            traffic: Field | None = None) -> Field:
    """The develop field. `seats` is (cell, worked, grown) per known place."""
    field = [[0.0] * size for _ in range(size)]
    reach = int(REACH)
    for (ci, cj), worked, cleared in seats:
        for j in range(max(0, cj - reach), min(size, cj + reach + 1)):
            for i in range(max(0, ci - reach), min(size, ci + reach + 1)):
                if sea[j][i]:
                    continue
                span = math.hypot(i - ci, j - cj)
                if span >= REACH:
                    continue
                near = 1.0 - span / REACH
                field[j][i] += worked * cleared * near * near
    if traffic is not None:
        busiest = max((value for row in traffic for value in row), default=0.0)
        if busiest > 0.0:
            for j in range(size):
                for i in range(size):
                    if not sea[j][i] and traffic[j][i] > 0.0:
                        field[j][i] += (TRAFFIC_WORTH
                                        * math.sqrt(traffic[j][i] / busiest))
    for j in range(size):
        row = field[j]
        for i in range(size):
            if row[i] > 1.0:
                row[i] = 1.0
    return field
