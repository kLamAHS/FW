"""The continent — the worked example world this application ships (spec §115).

A temperate continent that had a history before anyone conquered it. Six peoples were
already here, arranged by their own geography: the Merra on the western coast with the
best middleman position on the map, the Carthi in the river basin that feeds everyone,
the Vardi in the wet uplands with the timber, the Selli in the northern forests, the
Talari in the warm southern hills, the Arthi scattered through the mountains where the
Carth rises, and the Orri on the dry steppe beyond the rain shadow with the horses.

Then the Nyri came from Nyreland across the Northern Sea — not raiders but a
centralised, literate, well-organised kingdom that arrived because a Carthi claimant
asked for help and stayed because nobody could make them leave. Over forty years
(612–653) they took the interior. They never took Merran, and that is the fault line
the last four hundred and fifty years have run along: Nyren grows the grain, Merran
owns the river's mouth, and Carthain's kings in the upper valley say both of them are
newcomers.

Structured as a package rather than one file because the content divides cleanly and
there is a great deal of it: the ground, the peoples, what happened, and who is alive
now. This world doubles as the example a new writer first opens, so it arrives with its
map already grown — see `with_map`.
"""

from __future__ import annotations

from fw.core.calendar.kernel import Calendar, Era, Month, Season
from fw.core.world import World

# The Crown's Reckoning. Not Gregorian, and deliberately not the same shape as the
# Rennish calendar either: eight months, a 364-day year, six-day weeks. §60 makes
# calendars data, and an engine that only works on twelve months is not honouring it.
RECKONING = Calendar(
    name="The Crown's Reckoning",
    months=(
        Month("Hearthmoon", 44), Month("Thawmoon", 45), Month("Seedmoon", 46),
        Month("Longlight", 47), Month("Highsun", 47), Month("Goldfall", 46),
        Month("Rainmoon", 45), Month("Darkmoon", 44),
    ),
    weekdays=("Kingsday", "Ploughday", "Marketday", "Riversday", "Forgeday", "Restday"),
    leap_every=4,
    # The eras are an argument, not a ruler. "The Age of Petty Kings" is the Nyri's own
    # name for everything before them — every valley its king, every road its toll —
    # and no Carthi historian has ever willingly written it. "The Nyri Peace" is the
    # same claim about the present, and Merran will not write that one. §3's dividers
    # exist for exactly this: a period is a position, and the world holds both.
    eras=(
        Era("The Long Reckoning", "LR", end_year=0, counts_backward=True),
        Era("The Age of Petty Kings", "PK", 1, 611),
        Era("The Conquest", "TC", 612, 653),
        Era("The Nyri Peace", "NP", 654),
    ),
    seasons=(Season("Deepwinter", 1), Season("Greening", 70), Season("Highsummer", 160),
             Season("Harvest", 250), Season("Fading", 320)),
)

# The story's present, four hundred and fifty years after the crown was set down.
PRESENT_YEAR = 1100


def seed_nyren(path: str = ":memory:", *, with_map: bool = False) -> World:
    """Build the example world and return it.

    `with_map` also grows and accepts a map, which is what the two places that hand
    this world to a *person* do — `fw seed` and the launcher's example button. It is
    off by default so tests that only want the facts do not pay for the ground.
    """
    from . import geography, history, peoples, story

    w = World.create(path, name="The Continent",
                     description="Nyren, Merran and Carthain, four hundred years "
                                 "after the Conquest.",
                     calendar=RECKONING)
    ground = geography.build(w)
    built = dict(ground)
    _join(built, peoples.build(w, ground))
    _join(built, history.build(w, ground))
    story.build(w, built)
    # Drawn against the ground's own dict, never the merged one. A region and the
    # realm named after it are two entities, and hanging the coast's polygon on the
    # kingdom is a mistake nothing downstream can see: the shape simply stops being
    # an authored region, the generator finds no claim there, and that country's
    # territory grows from the arbitrary point the layout dropped it on instead.
    geography.draw(w, ground)

    # The planner needs statistics before the graph walks are fast. See store/db.py.
    w.analyze()
    if with_map:
        _grow_the_map(w)
        w.analyze()
    return w


def _join(into: dict, more: dict) -> None:
    """Merge, loudly. Two things in this world may not share a name in one namespace.

    They may share one in the world — Merran is a coast and a state, Nyreland a place
    and a kingdom — but the seed's own index must keep them apart, and it did not: the
    realm overwrote the region, `geography.draw` put the coast's outline on the crown,
    and two of eight countries came out somewhere else entirely on the map. Nothing
    failed; the picture was just wrong.
    """
    clash = sorted(set(into) & set(more))
    if clash:
        raise ValueError(f"the seed built two things called {clash}")
    into.update(more)


def _grow_the_map(w: World) -> None:
    """Give the example world the map it is an example of.

    Everything is accepted, because a proposal nobody has answered is not a map. But
    `invent_settlements` stays off: §66 says inventing a noun is opt-in, and the towns
    the writer placed are the ones the history is about.
    """
    from fw.core.mapgen.apply import apply_plan
    from fw.core.mapgen.decide import DecisionSet
    from fw.core.mapgen.pipeline import plan_map
    from fw.core.mapgen.plan import MapBrief

    proposal = plan_map(w, MapBrief())
    apply_plan(w, proposal, DecisionSet.accept_all(proposal))
