"""What a journey costs, and what the router is allowed to ignore.

The Router prices a stretch from its length, the transport's speed over that terrain
and quality, the season, and — since this file exists — how dangerous it is. Every
one of those is a claim about the world the writer can check, so each has a test that
would fail if the router quietly stopped reading it.
"""

from __future__ import annotations

import pytest

from fw.core.geo.routing import DANGER_COST, Router
from fw.core.model.records import RouteSegment


def _segment(sid: str, a: str, b: str, *, danger: str = "low",
             length: float = 100.0) -> RouteSegment:
    return RouteSegment(id=sid, from_entity_id=a, to_entity_id=b, length=length,
                        medium="road", quality=1.0, terrain="plain", danger=danger)


class _Calendar:
    """Enough calendar for the router: a day only has to name a season."""

    @staticmethod
    def season(day: int | None) -> str:
        return "summer"


class _Segments:
    """A world stub. Deliberately minimal: the point of building the graph out of
    `RouteSegment`s by hand is that these tests say what the COST FUNCTION does,
    without a corpus world's geography deciding the answer for them."""

    calendar = _Calendar()

    def __init__(self, segments):
        self._segments = segments

    def route_segments(self):
        return list(self._segments)


def _days(segments, origin, destination, **kwargs) -> float:
    return Router(_Segments(segments)).travel_time(origin, destination, **kwargs)


class TestDangerCosts:
    """The generator has claimed the router prices danger since sea lanes were drawn
    — `pipeline.LANE_DANGER` sets "moderate" under a comment saying so — and for
    several phases the router simply did not read the field. The comment is the part
    that did the damage: it stopped anyone checking.
    """

    def test_a_perilous_road_costs_more_than_a_safe_one(self):
        safe = _days([_segment("s", "a", "b")], "a", "b")
        risky = _days([_segment("s", "a", "b", danger="high")], "a", "b")
        assert risky > safe
        assert risky == pytest.approx(safe * DANGER_COST["high"])

    def test_the_router_will_go_round_a_dangerous_stretch(self):
        """The point of pricing danger at all: it has to change a decision, not just
        a number. A short perilous road against a longer safe pair of roads."""
        direct = _segment("direct", "a", "c", danger="extreme", length=100.0)
        around = [_segment("one", "a", "b", length=70.0),
                  _segment("two", "b", "c", length=70.0)]
        chosen = Router(_Segments([direct, *around])).route("a", "c")
        assert chosen is not None
        assert [leg.segment_id for leg in chosen.legs] == ["one", "two"], (
            "the router took the short dangerous road over the long safe one")
        # And with the danger gone it takes the direct road, so the detour above is
        # the danger talking and not the lengths.
        plain = _segment("direct", "a", "c", length=100.0)
        back = Router(_Segments([plain, *around])).route("a", "c")
        assert [leg.segment_id for leg in back.legs] == ["direct"]

    def test_an_unknown_word_is_priced_as_safe_and_not_as_free(self):
        """A writer may type anything: `danger` is free text with no scale behind it.
        Unrecognised means "no penalty", which is the conservative direction — an
        invented penalty is a lie about the world, a missing one is only a road no
        worse than it looks."""
        odd = _days([_segment("s", "a", "b", danger="hair-raising")], "a", "b")
        safe = _days([_segment("s", "a", "b", danger="low")], "a", "b")
        assert odd == pytest.approx(safe)

    def test_every_danger_the_generator_writes_has_a_price(self):
        """The silent 1.0 is how a danger system dies quietly: the generator starts
        writing a word the table has never heard of, every route keeps costing what
        it did, and nothing anywhere says so."""
        from fw.core.mapgen import pipeline

        written = {pipeline.LANE_DANGER, "low"}
        missing = sorted(word for word in written if word not in DANGER_COST)
        assert not missing, f"the generator writes {missing} and the router prices it free"


class TestTheRouterStillReadsTheRest:
    def test_a_closed_season_takes_the_stretch_out_of_the_graph(self):
        shut = RouteSegment(id="s", from_entity_id="a", to_entity_id="b", length=10.0,
                            closed_seasons=("winter",))
        assert _days([shut], "a", "b", season="summer") is not None
        assert _days([shut], "a", "b", season="winter") is None

    def test_a_road_is_not_walked_before_it_is_built(self):
        later = RouteSegment(id="s", from_entity_id="a", to_entity_id="b",
                             length=10.0, built_on=500)
        assert _days([later], "a", "b", day=400) is None
        assert _days([later], "a", "b", day=600) is not None
