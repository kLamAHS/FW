"""What the writer wrote, arriving on the map.

The generator had a superb model of the ground and almost no model of the world. It
read a region's `terrain` and its `climate` and nothing else — seven of the world's
thirty-four entity types, ten of its ninety-three predicates — so House Marr was
`based_in` Northwatch and the stage that puts castles somewhere had no idea whose seat
it was, and the Iron Road existed as a road entity with `connects` facts and the stage
that lays roads had never read it.

This is the other half of that: not that the reading *can* answer those questions, which
`test_mapgen_source.py` asserts, but that the map is now built out of the answers.
"""

from __future__ import annotations

import pytest

from fw.core.mapgen.pipeline import plan_map
from fw.core.mapgen.plan import MapBrief
from fw.core.seed.renn import seed_renn


@pytest.fixture(scope="module")
def world():
    made = seed_renn()
    yield made
    made.close()


@pytest.fixture(scope="module")
def plan(world):
    return plan_map(world, MapBrief(invent_settlements=True))


def reasons(plan, kind: str) -> dict[str, tuple[str, ...]]:
    return {f.name: f.why for f in plan.features if f.kind == kind}


class TestACastleKnowsWhoseItIs:
    def test_every_keep_stands_in_somebody_s_country(self, plan):
        said = reasons(plan, "castle")
        assert said, "no castles"
        for name, why in said.items():
            assert any("country" in line for line in why), f"{name}: {why}"

    def test_the_march_goes_to_the_house_that_owns_it(self, plan):
        """Not to whichever hall is nearest, which is a different question.

        House Dray and House Marr are both seated at Northwatch and it is Marr that
        legally owns the Northmarch. A nearest-hall rule also handed two keeps to the
        Ironmongers of Red Ford, who are a guild and hold no ground at all.
        """
        houses = [line.split("'s country")[0].removeprefix("in ")
                  for why in reasons(plan, "castle").values()
                  for line in why if "country" in line]
        assert set(houses) == {"House Marr", "House Veyne", "House Orren"}
        assert "The Ironmongers of Red Ford" not in houses

    def test_the_keeps_are_shared_out_between_them(self, plan):
        import collections
        houses = collections.Counter(
            line.split("'s country")[0].removeprefix("in ")
            for why in reasons(plan, "castle").values()
            for line in why if "country" in line)
        assert set(houses.values()) == {3}, houses


class TestACastleSaysWhatItIsFor:
    def test_every_keep_declares_an_archetype(self, plan):
        from fw.core.mapgen.pipeline import ARCHETYPES

        keeps = [f for f in plan.features if f.kind == "castle"]
        assert keeps, "no castles"
        for keep in keeps:
            assert keep.detail["archetype"] == ARCHETYPES[keep.detail["watches"]], (
                keep.name)

    def test_an_invented_keep_says_whose_it_is_from_the_house_s_side(self, plan):
        """The vocabulary has no `held_by`: "occupied by" is conjugation, `occupies`
        is the predicate. So the keep's holding fact names the house as its subject —
        under the same authority the house holds the country, no vaguer, no grander.
        """
        invented = [f for f in plan.features if f.kind == "castle"
                    and f.subject is not None and f.subject.mode == "new"]
        assert invented, "no invented castles"
        housed = [spec for f in invented for spec in f.facts if spec.subject_ref]
        assert housed, "no keep says whose it is"
        for spec in housed:
            assert spec.predicate_key in ("legally_owns", "administers", "occupies")
            assert spec.object_ref is None, "the keep itself is the fact's object"

    def test_accepting_a_keep_writes_the_house_fact_and_only_once(self):
        from fw.core.mapgen.apply import apply_plan
        from fw.core.mapgen.decide import DecisionSet
        from fw.core.model.records import GENERATED_TAG

        AUTHORITIES = ("legally_owns", "administers", "occupies")

        def house_facts(world):
            keeps = [e for e in world.entities("holding") if GENERATED_TAG in e.tags]
            return sorted(
                (keep.name, fact.predicate_key, fact.subject_id)
                for keep in keeps for fact in world.facts_where(object_id=keep.id)
                if fact.predicate_key in AUTHORITIES)

        world = seed_renn()
        try:
            plan = plan_map(world, MapBrief(invent_settlements=True))
            apply_plan(world, plan, DecisionSet.accept_all(plan))
            held = house_facts(world)
            assert held, "no accepted keep has a house fact"
            for name, _, subject_id in held:
                holder = world.get_entity(subject_id)
                assert holder is not None and holder.type_key == "house", name
            # Re-accepting must take the reversed fact back before saying it again —
            # it hangs off the *house*, which is not the entity being re-drawn.
            again = plan_map(world, MapBrief(invent_settlements=True))
            apply_plan(world, again, DecisionSet.accept_all(again))
            assert house_facts(world) == held
        finally:
            world.close()


class TestARoadDrawnEntirelyByBusierRoadsIsStillRemembered:
    def test_re_applying_an_identical_plan_writes_nothing_for_covered_roads(self):
        """The ink dedup can leave a route with no shapes of its own — every stretch
        already drawn by busier roads. Such a feature exists only as a travel-graph
        segment, and a ledger that read only shapes forgot it: each re-apply of the
        identical plan minted a fresh road entity and a duplicate segment.
        """
        from fw.core.mapgen.apply import apply_plan
        from fw.core.mapgen.decide import DecisionSet

        world = seed_renn()
        try:
            plan = plan_map(world, MapBrief(invent_settlements=True))
            covered = [f for f in plan.features
                       if f.kind == "road" and not f.shapes]
            assert covered, "Renn no longer has a fully-covered road; rig one"
            apply_plan(world, plan, DecisionSet.accept_all(plan))
            roads = sum(1 for _ in world.entities("road"))
            segments = len(world.route_segments())
            before = world.db.one("SELECT COUNT(*) AS n FROM revision")["n"]
            again = apply_plan(world, plan, DecisionSet.accept_all(plan))
            assert again.action_id is None, "an identical plan wrote something"
            assert world.db.one("SELECT COUNT(*) AS n FROM revision")["n"] == before
            assert sum(1 for _ in world.entities("road")) == roads
            assert len(world.route_segments()) == segments
        finally:
            world.close()


class TestTheMapDoesNotSecondGuessTheExampleWorld:
    def test_the_plausibility_notes_are_silent_on_renn(self, plan):
        """V2 §44's own gate: loud on rigged worlds, silent on a sound one. Every
        check is tuned against this — a diagnostic that complains about the example
        world is measuring its own thresholds, not the geography."""
        said = [f.message for f in plan.findings if f.code == "implausible"]
        assert said == [], said


class TestTheWritersOwnRoadsAreLaidFirst:
    def test_a_road_the_writer_named_is_on_the_map_as_theirs(self, plan):
        said = reasons(plan, "road")
        laid = [why for why in said.values()
                if any("you laid" in line for line in why)]
        assert laid, f"the Iron Road and the Salt Run reached nothing: {said}"

    def test_the_generated_network_bundles_onto_them(self, plan):
        """A made road is cheaper to travel, so the rest of the network should find it.

        Roads that each go their own way are the failure this is watching for: it means
        the writer's own route was pinned and then ignored.
        """
        cells: dict[tuple[float, float], int] = {}
        for feature in plan.features:
            if feature.kind != "road":
                continue
            for shape in feature.shapes:
                for point in shape.coordinates:
                    key = (round(float(point[0]), 1), round(float(point[1]), 1))
                    cells[key] = cells.get(key, 0) + 1
        shared = sum(1 for n in cells.values() if n > 1)
        assert shared, "no two roads share a single point"


class TestThingsThatHappenedSomewhere:
    def test_a_town_is_explained_by_what_happened_there(self, plan):
        """`event.location_id` has been in the world since the first migration.

        Six events in the example world name a place and no stage had ever read one.
        """
        said = reasons(plan, "settlement")
        assert any("The Battle of Red Ford was fought here" in line
                   for line in said.get("Red Ford", ())), said.get("Red Ford")

    def test_a_treaty_is_signed_rather_than_fought(self, plan):
        said = reasons(plan, "settlement")
        assert any("The Peace of Millbrook was signed here" in line
                   for line in said.get("Millbrook", ()))

    def test_the_explanation_carries_the_year_in_the_world_s_own_calendar(self, plan):
        line = next(line for line in reasons(plan, "settlement")["Red Ford"]
                    if "Battle" in line)
        assert "AK" in line, line


class TestWhoHoldsTheGround:
    def test_the_four_authorities_are_kept_apart(self, world):
        """§11's sharpest distinction, and the one a single fill destroys.

        House Marr owns Greyhaven in law, House Veyne runs it, the Crown taxes it and
        House Orren claims it. Collapsed to one answer, three of those disappear.
        """
        from fw.core.mapgen import source

        reading = source.read_world(world, at=world.day(240))
        held = reading.authority_over("settlement/greyhaven")
        assert held.owns == "house/house-marr"
        assert held.administers == "house/house-veyne"
        assert held.taxes and held.taxes != held.owns
        assert held.claims == ("house/house-orren",)

    def test_the_map_colours_for_whoever_is_actually_in_charge(self, world):
        from fw.core.mapgen import source

        reading = source.read_world(world, at=world.day(240))
        held = reading.authority_over("settlement/greyhaven")
        assert held.effective == "house/house-veyne", "an absent charter outranked a steward"
        assert held.layered and held.disputed

    def test_a_region_carries_its_holder_and_says_under_which_authority(self, world):
        from fw.core.mapgen.generate import MapGenerator

        generator = MapGenerator(world, at=world.day(240))
        generator.read_the_world()
        generator.build_the_world()
        politics = generator.political()
        assert politics, "nobody holds anything"
        by_name = {generator.profiles[rid].name: row for rid, row in politics.items()}
        assert by_name["The Northmarch"]["holder"] == "House Marr"
        assert by_name["The Northmarch"]["authority"] == "legally_owns"

    def test_a_region_carries_the_title_over_it(self, world):
        from fw.core.mapgen.generate import MapGenerator

        generator = MapGenerator(world, at=world.day(240))
        generator.read_the_world()
        generator.build_the_world()
        north = next(row for rid, row in generator.political().items()
                     if generator.profiles[rid].name == "The Northmarch")
        assert north["title"] == "Warden of the Northmarch"
        assert north["title_holder"], "the Warden is nobody"

    def test_the_writer_is_told_when_four_houses_hold_one_town(self, plan):
        said = [f.message for f in plan.findings if f.code == "contradiction"]
        assert any("held four ways at once" in m and "Greyhaven" in m for m in said), said


class TestTheMapIsBuiltFromOneReading:
    def test_a_plan_and_a_generate_agree_about_the_world(self, world):
        """They used to build their region profiles two different ways.

        `plan_map` called `profile_region` per region; `generate` built them from the
        reading. Two answers to the same question is the one thing a propose-then-accept
        split cannot survive.
        """
        from fw.core.mapgen import source
        from fw.core.mapgen.attributes import profiles_from
        from fw.core.mapgen.generate import MapGenerator

        generator = MapGenerator(world)
        generator.read_the_world()
        direct = profiles_from(source.read_world(world))
        assert {k: v.terrain_mix for k, v in generator.profiles.items()} == \
               {k: v.terrain_mix for k, v in direct.items()}

    def test_the_plan_says_which_reading_it_came_from(self, plan):
        assert plan.reading_fingerprint

    def test_two_plans_of_the_same_world_read_it_the_same(self, world):
        first = plan_map(world, MapBrief(invent_settlements=True))
        second = plan_map(world, MapBrief(invent_settlements=True))
        assert first.reading_fingerprint == second.reading_fingerprint
