"""Where a place gets what it does not grow (§18, §19, §42).

§19 asks this system one concrete question — *"Where does Greyhaven get its grain? … and
trace the supply path"* — and every piece of the answer was already in the world with
nothing joining them. The seed records that Greyhaven `imports` Grain and the Vale
`exports` it, with a comment on the next line reading "the dependency §42 asks about",
and the router has always been able to cost the journey between two places.

What is asserted here is the trace, not a simulation: §68 and §116 both warn against
adding economics for its own sake, so nothing computes a yield from soil and labour. The
interesting assertions are the negative ones — a town whose only supplier is unreachable
is a story, and that is what the writer opened this screen for.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fw.api.app import create_app
from fw.core.derive.supply import SupplyAnalyst
from fw.core.seed.renn import seed_renn


@pytest.fixture
def world():
    made = seed_renn()
    yield made
    made.close()


@pytest.fixture
def client(world):
    return TestClient(create_app(world))


@pytest.fixture
def day(world):
    return world.day(241)


def named(world, name: str) -> str:
    return next(e.id for e in world.entities() if e.name == name)


def kinds(findings) -> set[str]:
    return {f["kind"] if isinstance(f, dict) else f.kind for f in findings}


class TestTheQuestionTheBriefAsks:
    def test_where_does_greyhaven_get_its_grain(self, world, day):
        """§19's own worked example, traced end to end."""
        answer = SupplyAnalyst(world).where_it_comes_from(
            named(world, "Greyhaven"), named(world, "Grain"), day)

        assert [s.name for s in answer.sources] == ["The Vale of Renn"]
        vale = answer.sources[0]
        assert vale.exports and vale.level == "high"
        assert vale.days and vale.days > 0
        assert vale.path_names[-1] == "Greyhaven"

    def test_the_answer_is_a_sentence_with_its_evidence(self, world, day):
        """§67: a derived conclusion a writer cannot check is a black box."""
        answer = SupplyAnalyst(world).where_it_comes_from(
            named(world, "Greyhaven"), named(world, "Grain"), day)
        supply = next(f for f in answer.findings if f.kind == "supply")
        assert "Greyhaven gets Grain from The Vale of Renn" in supply.text
        assert "days by wagon" in supply.text
        assert any("exports Grain" in e for e in supply.evidence)

    def test_a_region_supplies_through_its_towns(self, world, day):
        """"The Vale exports grain" is how a writer says it; the road network joins
        settlements. The journey has to start somewhere real."""
        answer = SupplyAnalyst(world).where_it_comes_from(
            named(world, "Greyhaven"), named(world, "Grain"), day)
        vale = answer.sources[0]
        assert vale.path[0] != named(world, "The Vale of Renn")
        assert "in The Vale of Renn" in vale.note

    def test_goods_move_by_wagon_not_by_courier(self, world, day):
        """A messenger's four days is not a grain convoy's twelve, and §19 asks for
        travel time — so the profile is part of the answer rather than a default
        nobody chose."""
        place, grain = named(world, "Greyhaven"), named(world, "Grain")
        slow = SupplyAnalyst(world).where_it_comes_from(place, grain, day)
        fast = SupplyAnalyst(world, profile="messenger").where_it_comes_from(
            place, grain, day)
        assert fast.sources[0].days < slow.sources[0].days


class TestTheAnswersAWriterActuallyWants:
    def test_a_single_supplier_is_called_out_as_fragile(self, world, day):
        """The point of the screen. One road and one supplier is a plot."""
        answer = SupplyAnalyst(world).where_it_comes_from(
            named(world, "Greyhaven"), named(world, "Grain"), day)
        fragile = next(f for f in answer.findings if f.kind == "fragile")
        assert "only source" in fragile.text

    def test_needing_something_nobody_makes_is_the_loudest_answer(self, world, day):
        spice = world.add_entity("resource", "Silk")
        world.assert_fact(named(world, "Greyhaven"), "imports", spice.id,
                          strength="high")
        answer = SupplyAnalyst(world).where_it_comes_from(
            named(world, "Greyhaven"), spice.id, day)
        gap = next(f for f in answer.findings if f.kind == "gap")
        assert "Nobody in this world produces Silk" in gap.text
        assert gap.weight == 5

    def test_a_supplier_no_road_reaches_is_not_a_supplier(self, world, day):
        """A place with grain and no way to move it does not feed anybody."""
        away = world.add_entity("settlement", "Farhold")
        world.assert_fact(away.id, "exports", named(world, "Grain"), strength="high")
        answer = SupplyAnalyst(world).where_it_comes_from(
            named(world, "Greyhaven"), named(world, "Grain"), day)
        farhold = next(s for s in answer.sources if s.name == "Farhold")
        assert not farhold.reachable
        assert "No road" in farhold.note

    def test_reachable_suppliers_come_first(self, world, day):
        away = world.add_entity("settlement", "Farhold")
        world.assert_fact(away.id, "exports", named(world, "Grain"), strength="very_high")
        answer = SupplyAnalyst(world).where_it_comes_from(
            named(world, "Greyhaven"), named(world, "Grain"), day)
        assert answer.sources[0].reachable
        assert answer.sources[-1].name == "Farhold"


class TestWhoBenefits:
    def test_who_depends_on_a_place(self, world, day):
        """§117's "Who benefits?", which the application could not answer at all."""
        told = SupplyAnalyst(world).who_depends_on(named(world, "Millbrook"), day)
        assert any("Greyhaven depends on Millbrook" in f.text for f in told)

    def test_a_supplier_learns_who_leans_on_it(self, world, day):
        told = SupplyAnalyst(world).who_depends_on(named(world, "The Vale of Renn"), day)
        assert any("Greyhaven takes Grain" in f.text for f in told)

    def test_producing_and_exporting_one_thing_is_one_dependency(self, world, day):
        """The Vale both produces and exports grain. Said twice it reads as two
        separate dependencies, which is a claim about the world nobody made."""
        told = SupplyAnalyst(world).who_depends_on(named(world, "The Vale of Renn"), day)
        grain = [f for f in told if "Grain" in f.text]
        assert len(grain) == 1


class TestItFollowsTheTimeline:
    def test_a_road_not_yet_built_supplies_nobody(self, world):
        """The router applies construction dates, so an answer is of one day."""
        place, grain = named(world, "Greyhaven"), named(world, "Grain")
        analyst = SupplyAnalyst(world)
        early = analyst.where_it_comes_from(place, grain, world.day(1))
        assert not any(s.reachable for s in early.sources)
        assert kinds(early.findings) == {"gap"}

    def test_a_supply_that_has_ended_is_not_a_supply(self, world, day):
        vale = named(world, "The Vale of Renn")
        fact = next(f for f in world.facts_where("exports", subject_id=vale)
                    if f.object_id == named(world, "Grain"))
        world.end_fact(fact.id, world.day(239))
        answer = SupplyAnalyst(world).where_it_comes_from(
            named(world, "Greyhaven"), named(world, "Grain"), day)
        # The Vale still *produces* it, so it remains a source — but no longer an
        # exporter, which is the distinction the two predicates exist for.
        assert not answer.sources[0].exports


class TestOverTheWire:
    def test_the_route_answers_the_brief_s_question(self, client, world):
        answer = client.get(
            f"/api/supply/{named(world, 'Greyhaven')}/{named(world, 'Grain')}").json()
        assert answer["resource_name"] == "Grain"
        assert answer["sources"][0]["name"] == "The Vale of Renn"
        assert "supply" in kinds(answer["findings"])

    def test_everything_a_place_needs_in_one_read(self, client, world):
        whole = client.get(f"/api/supply/{named(world, 'Greyhaven')}").json()
        assert whole["place_name"] == "Greyhaven"
        assert [n["resource_name"] for n in whole["needs"]] == ["Grain"]

    def test_it_says_who_leans_on_the_place_too(self, client, world):
        whole = client.get(f"/api/supply/{named(world, 'Millbrook')}").json()
        assert any("Greyhaven depends on" in f["text"]
                   for f in whole["depended_on_by"])

    def test_a_place_that_does_not_exist_is_refused(self, client):
        assert client.get("/api/supply/nope").status_code == 404

    def test_a_commodity_that_does_not_exist_is_refused(self, client, world):
        assert client.get(
            f"/api/supply/{named(world, 'Greyhaven')}/nope").status_code == 404


class TestTheLevelCanFinallyBeSet:
    def test_the_economy_predicates_carry_a_scale(self, client):
        """§18's simple mode is `grain production: high`, and the fact form hides its
        strength control for any predicate without a scale — so the seeded world held
        `produces … strength="high"` that the application could not author."""
        predicates = {p["key"]: p for p in
                      client.get("/api/vocabulary").json()["predicates"]}
        for key in ("produces", "consumes", "imports", "exports"):
            assert predicates[key]["scale_key"] == "magnitude"

    def test_the_question_form_can_offer_the_steps(self, client):
        words = client.get("/api/query/vocabulary").json()
        magnitude = [s["key"] for s in words["strengths"] if s["scale"] == "magnitude"]
        assert magnitude == ["none", "low", "medium", "high", "very_high"]

    def test_which_regions_produce_something_at_high_level(self, client):
        """§49's own shape of example, engine-complete and unaskable until now."""
        answer = client.post("/api/query", json={"query": {
            "types": ["region"],
            "conditions": [{"predicate": "produces", "strength": ["high"]}]}}).json()
        assert {r["name"] for r in answer["rows"]} == {
            "The Northmarch", "The Salt Reach", "The Vale of Renn"}

    def test_a_road_can_carry_a_commodity(self, client, world):
        """§19 wants a commodity on a route. `carries` was *read* by the map generator
        when collecting a road's goods, was in no vocabulary, and was written by
        nothing — a dead read three layers deep."""
        road = named(world, "The Iron Road")
        made = client.post("/api/facts", json={
            "subject_id": road, "predicate_key": "carries",
            "object_id": named(world, "Grain"), "strength": "high"})
        assert made.status_code == 201
        assert any(f.object_id == named(world, "Grain")
                   for f in world.facts_where("carries", subject_id=road))
