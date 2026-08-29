"""C12: the things a map leaves behind, and the ones it never reached.

Three of them, and each is a way the map spilled into the rest of the application:

**A proposal is not the writer's world.** An accepted map's towns keep the tag that
lets the next run recognise its own work, so a filter on the tag alone hides the
writer's own accepted places from them. The rule is the tag *and* speculative
confidence, and it is the same rule for the entity lists and for continuity — otherwise
one accepted map fills the writer's settlement list and their checks page with things
they never wrote.

**An island is not reachable by wishing.** `coast.SMALLEST_ISLAND` guarantees a map has
islands, the router works over segments, and nothing had ever drawn one to an island —
so a writer asking how long it takes to reach Renncape was told there is no way at all,
of a place their own map put in the sea.

**A generated town has a date or honestly has none.** The map cannot know when a town
was founded, but it does know a town cannot predate the country it stands in.
"""

from __future__ import annotations

import pytest

from fw.core.calendar.kernel import GREGORIAN
from fw.core.continuity.engine import check
from fw.core.mapgen.apply import apply_plan
from fw.core.mapgen.decide import Decision, DecisionSet
from fw.core.mapgen.pipeline import plan_map
from fw.core.mapgen.plan import GENERATED_TAG, MapBrief
from fw.core.model.records import PROPOSED_TAG
from fw.core.seed.renn import seed_renn
from fw.core.world import World


@pytest.fixture
def renn():
    made = seed_renn()
    yield made
    made.close()


def accept_everything(world: World) -> None:
    plan = plan_map(world, MapBrief(invent_settlements=True))
    apply_plan(world, plan, DecisionSet(plan_id=plan.plan_id, decisions=tuple(
        Decision(feature_id=f.id, accept=True) for f in plan.features)))


class TestAProposalIsNotTheWritersWorld:
    def test_a_suggestion_is_recognised_as_one(self, renn):
        made = renn.add_entity("settlement", "Nowhere", confidence="speculative",
                               tags=[GENERATED_TAG])
        assert renn.get_entity(made.id).is_a_map_proposal

    def test_the_first_generator_s_mark_counts_too(self, renn):
        """Worlds drawn by the old code carry `proposed` and nothing else."""
        made = renn.add_entity("settlement", "Elsewhere", confidence="speculative",
                               tags=[PROPOSED_TAG])
        assert renn.get_entity(made.id).is_a_map_proposal

    def test_a_town_the_writer_accepted_is_theirs(self, renn):
        """It keeps its tag so the next run knows its own work — and stays visible."""
        accept_everything(renn)
        made = [e for e in renn.entities("settlement") if GENERATED_TAG in e.tags]
        assert made, "the map created nothing"
        assert not any(e.is_a_map_proposal for e in made), \
            "an accepted town would vanish from the writer's own list"

    def test_something_uncertain_the_writer_wrote_is_not_hidden(self, renn):
        """Speculative alone is not the map's mark. A writer's maybe is theirs."""
        made = renn.add_entity("settlement", "Maybe", confidence="speculative")
        assert not renn.get_entity(made.id).is_a_map_proposal

    def test_the_checks_do_not_complain_about_towns_nobody_wrote(self, renn):
        """A page of complaints about the map's own suggestions is a page nobody reads."""
        before = len(check(renn).violations)
        town = renn.add_entity("settlement", "Ghosttown", confidence="speculative",
                               tags=[GENERATED_TAG],
                               exists_from=renn.day(500))
        renn.assert_fact(town, "population", value="900000")
        assert len(check(renn).violations) == before


class TestAnIslandIsReachable:
    def test_every_island_gets_a_crossing(self, renn):
        plan = plan_map(renn, MapBrief(invent_settlements=True))
        islands = {f.id for f in plan.features if f.kind == "island"}
        lanes = [f for f in plan.features if f.kind == "lane"]
        assert lanes, "islands, and no way to any of them"
        assert {f.anchor_id for f in lanes} <= islands

    def test_a_crossing_is_made_of_sea(self, renn):
        plan = plan_map(renn, MapBrief(invent_settlements=True))
        for lane in (f for f in plan.features if f.kind == "lane"):
            assert [s.medium for s in lane.segments] == ["sea"]

    def test_a_crossing_lands_somewhere_a_ship_could_put_in(self):
        """The invariant a crossing must keep, whatever the world looks like.

        Preferring a port is the *rule*; landing at one is not always possible, and on
        the example world it is not — see the test below. What is never allowed is a
        landing with no water near it at all.
        """
        from fw.core.mapgen import pipeline
        from fw.core.mapgen.generate import MapGenerator

        world = seed_renn()
        try:
            generator = MapGenerator(world)
            generator.read_the_world()
            generator.build_the_world()
            placed = generator._site_settlements(propose=True)
            lanes = pipeline._sea_lane_drafts(generator, placed)
            assert lanes
            by_name = {p.name: p for p in
                       list(placed) + generator._already_placed(placed)}
            for lane in lanes:
                where = by_name[lane.detail["lands_at"]]
                cell = generator._cell_of(where.x, where.y)
                assert generator._sea_within(cell, pipeline.QUAY_REACH), \
                    f"{where.name} has no water within reach and a ship lands there"
        finally:
            world.close()

    def test_a_port_the_coastline_missed_is_reported(self, renn):
        """Greyhaven and Blackmere are ports the generated sea never reaches.

        Their towns do not move (§66) and the coast is grown from their own regions, so
        the map cannot resolve it — but a port thirty leagues inland is a port no ship
        reaches, and every crossing will land somewhere else, which the writer would
        certainly want to know.
        """
        plan = plan_map(renn, MapBrief(invent_settlements=True))
        said = [f.message for f in plan.findings
                if "the coastline came out" in f.message]
        assert any("Blackmere" in m for m in said), said

    def test_a_crossing_is_named_after_its_island(self, renn):
        """And the island is named this same run, so the name is filled in after."""
        plan = plan_map(renn, MapBrief(invent_settlements=True))
        islands = {f.id: f.name for f in plan.features if f.kind == "island"}
        for lane in (f for f in plan.features if f.kind == "lane"):
            island = islands[lane.anchor_id]
            assert island.removeprefix("The ") in lane.name
            assert not lane.name.startswith("The The")

    def test_a_crossing_is_offered_rather_than_drawn(self, renn):
        """§66: a shipping lane is a claim about the world, so the writer decides."""
        plan = plan_map(renn, MapBrief(invent_settlements=True))
        assert all(not f.default_accept for f in plan.features if f.kind == "lane")

    def test_the_island_can_be_reached_once_the_crossing_is_accepted(self, renn):
        """The whole point. Before this the answer was "there is no way at all"."""
        from fw.core.geo.routing import Router

        accept_everything(renn)
        crossings = [s for s in renn.route_segments() if s.medium == "sea"]
        assert crossings, "no sea segment reached the world"
        route = Router(renn).route(crossings[0].to_entity_id,
                                   crossings[0].from_entity_id, profile="ship")
        assert route is not None and route.days > 0


class TestAGeneratedTownHasADateOrHonestlyHasNone:
    def test_a_proposed_town_is_no_older_than_its_country(self):
        world = World.create(name="Ashmere", calendar=GREGORIAN)
        try:
            founded = world.day(800)
            region = world.add_entity("region", "The Late March", exists_from=founded)
            world.assert_fact(region, "terrain", value="forest and river valley")
            world.assert_fact(region, "population", value="80000")
            other = world.add_entity("region", "The Old March")
            world.assert_fact(other, "terrain", value="coast and harbour")
            world.assert_fact(other, "population", value="40000")
            world.assert_fact(region, "borders", other)

            plan = plan_map(world, MapBrief(invent_settlements=True))
            dated = {f.detail["region"]: f.subject.exists_from
                     for f in plan.features
                     if f.kind == "settlement" and f.subject and f.subject.mode == "new"}
            assert dated.get("The Late March") == founded
            # And no claim at all about the country they never dated.
            assert dated.get("The Old March") is None
        finally:
            world.close()

    def test_an_undated_world_gets_no_invented_history(self, renn):
        """The example world dates no region, so no proposed town claims a founding."""
        plan = plan_map(renn, MapBrief(invent_settlements=True))
        invented = [f for f in plan.features
                    if f.kind == "settlement" and f.subject and f.subject.mode == "new"]
        assert invented
        assert all(f.subject.exists_from is None for f in invented)

    def test_the_date_survives_being_accepted(self):
        world = World.create(name="Ashmere", calendar=GREGORIAN)
        try:
            founded = world.day(800)
            region = world.add_entity("region", "The Late March", exists_from=founded)
            world.assert_fact(region, "terrain", value="forest and river valley")
            world.assert_fact(region, "population", value="80000")
            accept_everything(world)
            made = [e for e in world.entities("settlement")
                    if GENERATED_TAG in e.tags]
            assert made and all(e.exists_from == founded for e in made)
        finally:
            world.close()


class TestTheApplicationCanSeeTheDifference:
    def test_the_entity_list_can_leave_out_what_was_only_suggested(self, renn):
        from fastapi.testclient import TestClient

        from fw.api.app import create_app

        renn.add_entity("settlement", "Suggested", confidence="speculative",
                        tags=[GENERATED_TAG])
        client = TestClient(create_app(renn))
        everything = client.get("/api/entities", params={"type_key": "settlement"})
        theirs = client.get("/api/entities",
                            params={"type_key": "settlement", "hide_generated": True})
        assert "Suggested" in {e["name"] for e in everything.json()}
        assert "Suggested" not in {e["name"] for e in theirs.json()}
        assert "Rennford" in {e["name"] for e in theirs.json()}

    def test_a_journey_can_be_asked_about_an_island(self, renn):
        from fastapi.testclient import TestClient

        from fw.api.app import create_app

        accept_everything(renn)
        client = TestClient(create_app(renn))
        places = client.get("/api/travel/places").json()
        assert any(p["type_key"] == "terrain_feature" for p in places), \
            "the picker offers towns only, and the crossing goes to an island"
