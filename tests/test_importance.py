"""How much each thing on the map matters (V2 §31).

The map had no hierarchy: a capital, a hamlet and an unnamed brook carried the same
graphical weight, and every decision that needed one — which label survives a crowded
corner, what a story mode dims — had nowhere to look. The score is graded at plan time
into `detail`, with its components beside it so a writer can see why (§67).
"""

from __future__ import annotations

import pytest

from fw.core.calendar.kernel import GREGORIAN
from fw.core.mapgen.pipeline import plan_map
from fw.core.mapgen.plan import MapBrief
from fw.core.world import World


@pytest.fixture
def storied() -> World:
    """Two regions, three settlements the writer ranked, and a story that keeps
    returning to the smallest of them."""
    w = World.create(name="Weald", calendar=GREGORIAN)
    weald = w.add_entity("region", "The Weald")
    w.assert_fact(weald.id, "terrain", value="forest and river valley")
    w.assert_fact(weald.id, "population", value="120000")
    shore = w.add_entity("region", "The Shore")
    w.assert_fact(shore.id, "terrain", value="coast and harbour")
    w.assert_fact(shore.id, "population", value="80000")
    w.assert_fact(weald.id, "borders", shore.id)

    def town(name: str, rank: str, region) -> str:
        made = w.add_entity("settlement", name)
        w.assert_fact(made.id, "settlement_type", value=rank)
        w.assert_fact(made.id, "located_in", region.id)
        return made.id

    w.ids = {
        "capital": town("Kingsholt", "capital", weald),
        "quiet": town("Dour", "hamlet", weald),
        "storied": town("Brand", "hamlet", shore),
        "struck": town("Ashen", "hamlet", shore),
    }
    # The writer keeps setting scenes in Brand, a hamlet the census ignores.
    for title in ("The oath", "The return", "The fire in the hall"):
        w.add_scene(title, location_id=w.ids["storied"])
    # Ashen was sacked once; Dour hosted a feast.
    w.add_event("Sack of Ashen", type_key="battle",
                location_id=w.ids["struck"], start_day=w.day(120))
    w.add_event("The Long Feast", type_key="feast",
                location_id=w.ids["quiet"], start_day=w.day(120))
    yield w
    w.close()


def feature_named(plan, name: str):
    return next(f for f in plan.features if f.name == name)


class TestTheHierarchyExists:
    def test_every_feature_carries_a_score_and_its_working(self, storied: World):
        plan = plan_map(storied, MapBrief())
        assert plan.features
        for feature in plan.features:
            score = feature.detail["importance"]
            assert 0.0 <= score <= 1.0
            told = feature.detail["importance_of"]
            assert set(told) == {"geography", "politics", "story"}

    def test_a_capital_outranks_a_hamlet(self, storied: World):
        plan = plan_map(storied, MapBrief())
        capital = feature_named(plan, "Kingsholt").detail["importance"]
        hamlet = feature_named(plan, "Dour").detail["importance"]
        assert capital > hamlet

    def test_the_coast_outranks_a_road(self, storied: World):
        plan = plan_map(storied, MapBrief())
        coast = max(f.detail["importance"] for f in plan.features
                    if f.kind == "coast")
        roads = [f.detail["importance"] for f in plan.features if f.kind == "road"]
        assert all(coast > r for r in roads)


class TestTheStoryWeighs:
    def test_pages_spent_in_a_place_lift_it(self, storied: World):
        """Brand and Dour are the same size on the census; Brand carries three
        chapters. The map should know the difference."""
        plan = plan_map(storied, MapBrief())
        storied_town = feature_named(plan, "Brand")
        quiet = feature_named(plan, "Dour")
        assert (storied_town.detail["importance_of"]["story"]
                > quiet.detail["importance_of"]["story"])
        assert storied_town.detail["importance"] > quiet.detail["importance"]

    def test_ruin_weighs_more_than_a_feast(self, storied: World):
        plan = plan_map(storied, MapBrief())
        sacked = feature_named(plan, "Ashen")
        feasted = feature_named(plan, "Dour")
        assert (sacked.detail["importance_of"]["story"]
                > feasted.detail["importance_of"]["story"])

    def test_the_components_are_shown_not_summed_in_secret(self, storied: World):
        """§67: the writer can check the working. The total never exceeds what the
        components argue for."""
        plan = plan_map(storied, MapBrief())
        for feature in plan.features:
            told = feature.detail["importance_of"]
            ceiling = (0.55 * told["geography"] + 0.20 * told["politics"]
                       + 0.25 * told["story"])
            assert feature.detail["importance"] <= round(ceiling, 3) + 0.001


class TestASeatIsPolitical:
    def test_a_house_seat_lifts_its_town(self, storied: World):
        house = storied.add_entity("house", "House Varn")
        storied.assert_fact(house.id, "based_in", storied.ids["quiet"])
        plan = plan_map(storied, MapBrief())
        seat = feature_named(plan, "Dour")
        assert seat.detail["importance_of"]["politics"] > 0
