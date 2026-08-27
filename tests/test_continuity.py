"""Routing and continuity tests (spec §22, §24, §46, §47, §48)."""

from __future__ import annotations

import pytest

from fw.core.continuity.engine import (
    DEFAULT_RULES,
    ContinuityEngine,
    Severity,
    Violation,
)
from fw.core.geo.routing import PROFILES, Router
from fw.core.world import World


class TestRouting:
    @pytest.fixture
    def router(self, renn: World) -> Router:
        return Router(renn)

    def test_the_specs_own_question(self, renn, router):
        """§22: 'How long does it take to travel from Greyhaven to Rennford?'"""
        gh = renn.entity_named("Greyhaven").id
        rf = renn.entity_named("Rennford").id
        route = router.route(gh, rf, profile="horse", season="Highsummer")
        assert route is not None
        assert 3 < route.days < 4
        names = [renn.get_entity(i).name for i in route.path]
        assert names == ["Greyhaven", "Red Ford", "Millbrook", "Rennford"]

    def test_transport_changes_the_answer(self, renn, router):
        gh = renn.entity_named("Greyhaven").id
        rf = renn.entity_named("Rennford").id
        times = {
            p: router.travel_time(gh, rf, profile=p, season="Highsummer")
            for p in ("messenger", "horse", "walking", "wagon", "army")
        }
        assert times["messenger"] < times["horse"] < times["walking"] < times["army"]

    def test_water_and_land_take_different_routes(self, renn, router):
        """A barge cannot use a road, and a rider cannot use the river."""
        gh = renn.entity_named("Greyhaven").id
        rf = renn.entity_named("Rennford").id
        barge = router.route(gh, rf, profile="barge", season="Highsummer")
        horse = router.route(gh, rf, profile="horse", season="Highsummer")
        assert barge is not None and horse is not None
        assert len(barge.legs) == 1                    # straight down the river
        assert len(horse.legs) == 3                    # by road, through two towns
        assert all(leg.medium == "river" for leg in barge.legs)
        assert all(leg.medium == "road" for leg in horse.legs)

    def test_a_frozen_river_is_not_a_route(self, renn, router):
        """§20 seasonal closures: 'no route' is a useful answer."""
        gh = renn.entity_named("Greyhaven").id
        rf = renn.entity_named("Rennford").id
        assert router.route(gh, rf, profile="barge", season="Highsummer") is not None
        assert router.route(gh, rf, profile="barge", season="Deepwinter") is None

    def test_season_is_derived_from_the_date(self, renn, router):
        gh = renn.entity_named("Greyhaven").id
        rf = renn.entity_named("Rennford").id
        midwinter = renn.day(241, 1, 5)
        route = router.route(gh, rf, profile="barge", day=midwinter)
        assert route is None                            # Deepwinter, so the river is shut

    def test_a_road_cannot_be_used_before_it_is_built(self, renn, router):
        gh = renn.entity_named("Greyhaven").id
        nw = renn.entity_named("Northwatch").id
        # The pass road is built in 202.
        assert router.route(gh, nw, profile="horse", day=renn.day(190)) is None
        assert router.route(gh, nw, profile="horse", day=renn.day(210)) is not None

    def test_a_large_army_moves_slower_than_a_small_one(self, renn, router):
        gh = renn.entity_named("Greyhaven").id
        rf = renn.entity_named("Rennford").id
        small = router.travel_time(gh, rf, profile="army", party_size=200,
                                   season="Highsummer")
        large = router.travel_time(gh, rf, profile="army", party_size=8000,
                                   season="Highsummer")
        assert large > small

    def test_route_explains_itself(self, renn, router):
        gh = renn.entity_named("Greyhaven").id
        rf = renn.entity_named("Rennford").id
        text = router.route(gh, rf, profile="horse", season="Highsummer").explain(renn)
        assert "Greyhaven to Rennford" in text
        assert "Red Ford" in text
        assert "days" in text

    def test_unreachable_returns_none(self, renn, router):
        gh = renn.entity_named("Greyhaven").id
        island = renn.add_entity("settlement", "Nowhere")
        assert router.route(gh, island.id, profile="horse") is None

    def test_unknown_profile_raises(self, renn, router):
        gh = renn.entity_named("Greyhaven").id
        rf = renn.entity_named("Rennford").id
        with pytest.raises(ValueError, match="unknown transport profile"):
            router.route(gh, rf, profile="dragon")

    def test_reachable_within(self, renn, router):
        gh = renn.entity_named("Greyhaven").id
        near = router.reachable_within(gh, 3.0, profile="messenger", season="Highsummer")
        assert renn.entity_named("Rennford").id in near
        assert all(t <= 3.0 for t in near.values())

    def test_every_advertised_profile_works(self, renn, router):
        """§22 lists eight ways to travel; none may be a label with nothing behind it."""
        gh = renn.entity_named("Greyhaven").id
        rf = renn.entity_named("Rennford").id
        for key in PROFILES:
            result = router.route(gh, rf, profile=key, season="Highsummer")
            assert result is None or result.days > 0


class TestContinuityEngine:
    def test_the_seeded_world_is_clean(self, renn: World):
        """The demo world must not ship with continuity errors of its own."""
        report = ContinuityEngine(renn).run()
        assert report.errors == [], "\n".join(v.message for v in report.errors)

    def test_all_rules_run(self, renn: World):
        report = ContinuityEngine(renn).run()
        assert len(report.checked_rules) == len(DEFAULT_RULES)
        assert len(report.checked_rules) >= 20

    def test_spec_47_dead_character_at_a_battle(self, world: World):
        """§47's worked example: died 229, listed at a battle in 231."""
        elia = world.add_entity("person", "Elia", exists_from=world.day(200),
                                exists_to=world.day(229))
        battle = world.add_event("The Battle of Orren", start_day=world.day(231),
                                 participants=[(elia.id, "participant")])
        report = ContinuityEngine(world).run()
        messages = [v.message for v in report.errors]
        assert any("Elia" in m and "died" in m for m in messages)
        assert any(v.rule_key == "dead_character_acts" for v in report.errors)

    def test_character_acts_before_birth(self, world: World):
        late = world.add_entity("person", "Late", exists_from=world.day(250))
        world.add_event("An early council", start_day=world.day(200),
                        participants=[(late.id, "participant")])
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "unborn_character_acts" for v in report.errors)

    def test_dead_character_appears_in_a_scene(self, world: World):
        """§46: 'Dead character appears without explanation.'"""
        ghost = world.add_entity("person", "Ghost", exists_from=world.day(100),
                                 exists_to=world.day(200))
        hall = world.add_entity("settlement", "The Hall")
        world.add_scene("A late supper", day=world.day(220), location_id=hall.id,
                        participants=[ghost.id])
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "dead_character_in_scene" for v in report.errors)

    def test_marriage_predates_a_birth(self, world: World):
        """§46: 'Marriage predates one participant's birth.'"""
        a = world.add_entity("person", "A", exists_from=world.day(180))
        b = world.add_entity("person", "B", exists_from=world.day(220))
        world.assert_fact(a, "married_to", b, valid_from=world.day(200))
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "marriage_predates_birth" for v in report.errors)

    def test_parent_younger_than_child(self, world: World):
        parent = world.add_entity("person", "Parent", exists_from=world.day(250))
        child = world.add_entity("person", "Child", exists_from=world.day(200))
        world.assert_fact(parent, "parent_of", child)
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "parent_born_after_child" for v in report.errors)

    def test_minimum_parent_age_is_configurable(self, world: World):
        """§48: 'characters cannot give birth before a configurable minimum age.'"""
        parent = world.add_entity("person", "Parent", exists_from=world.day(200))
        child = world.add_entity("person", "Child", exists_from=world.day(208))
        world.assert_fact(parent, "parent_of", child)
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "implausible_parent_age" for v in report.warnings)

        from fw.core.continuity.engine import ImplausibleParentAge
        lenient = ContinuityEngine(world, rules=(ImplausibleParentAge(5),)).run()
        assert lenient.violations == []

    def test_title_held_by_the_dead(self, world: World):
        """§48: 'title holders must be alive.'"""
        lord = world.add_entity("person", "Lord", exists_from=world.day(100),
                                exists_to=world.day(200))
        title = world.add_title("Lord of Somewhere")
        world.grant_title(title.id, lord.id, from_day=world.day(150))   # never ends
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "title_held_by_the_dead" for v in report.errors)

    def test_title_used_before_creation(self, world: World):
        """§46: 'Title is used before it was created.'"""
        lord = world.add_entity("person", "Lord", exists_from=world.day(100))
        title = world.add_title("A New Title", created_on=world.day(200))
        world.grant_title(title.id, lord.id, from_day=world.day(150),
                          to_day=world.day(300))
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "title_used_before_creation" for v in report.errors)

    def test_scene_before_its_location_was_founded(self, world: World):
        town = world.add_entity("settlement", "Newtown", exists_from=world.day(300))
        world.add_scene("Too early", day=world.day(250), location_id=town.id)
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "place_used_before_existence" for v in report.errors)

    def test_person_in_two_places_on_one_day(self, world: World):
        """§46: 'Character appears in two distant locations on the same date.'"""
        person = world.add_entity("person", "Mara", exists_from=world.day(100))
        here = world.add_entity("settlement", "Here")
        there = world.add_entity("settlement", "There")
        day = world.day(200)
        world.add_scene("Scene A", day=day, location_id=here.id, participants=[person.id])
        world.add_scene("Scene B", day=day, location_id=there.id, participants=[person.id])
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "person_in_two_places" for v in report.errors)

    def test_impossible_journey(self, world: World):
        """§46's hardest check: 'journey requires three days but timeline allows one'."""
        person = world.add_entity("person", "Rider", exists_from=world.day(100))
        a = world.add_entity("settlement", "Aford")
        b = world.add_entity("settlement", "Bfield")
        world.add_route_segment(a.id, b.id, 400, quality=1.0, terrain="plain")
        world.add_scene("Departure", day=world.day(200, 1, 1), location_id=a.id,
                        participants=[person.id])
        world.add_scene("Arrival", day=world.day(200, 1, 2), location_id=b.id,
                        participants=[person.id])
        report = ContinuityEngine(world).run()
        journey = [v for v in report.violations if v.rule_key == "impossible_journey"]
        assert journey
        assert "days are available" in journey[0].message or "available" in journey[0].message

    def test_a_journey_with_enough_time_is_not_flagged(self, world: World):
        person = world.add_entity("person", "Rider", exists_from=world.day(100))
        a = world.add_entity("settlement", "Aford")
        b = world.add_entity("settlement", "Bfield")
        world.add_route_segment(a.id, b.id, 40, quality=1.0, terrain="plain")
        world.add_scene("Departure", day=world.day(200, 1, 1), location_id=a.id,
                        participants=[person.id])
        world.add_scene("Arrival", day=world.day(200, 1, 20), location_id=b.id,
                        participants=[person.id])
        report = ContinuityEngine(world).run()
        assert not [v for v in report.violations if v.rule_key == "impossible_journey"]

    def test_knows_a_secret_before_being_born(self, world: World):
        """§48: 'characters cannot know information before learning it.'"""
        person = world.add_entity("person", "Child", exists_from=world.day(220))
        secret = world.add_secret("A secret")
        world.set_knowledge(person.id, secret.id, "knows", acquired_on=world.day(200))
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "knows_before_learning" for v in report.errors)

    def test_learns_from_someone_already_dead(self, world: World):
        teller = world.add_entity("person", "Teller", exists_from=world.day(100),
                                  exists_to=world.day(200))
        hearer = world.add_entity("person", "Hearer", exists_from=world.day(150))
        secret = world.add_secret("A secret")
        world.set_knowledge(hearer.id, secret.id, "knows", acquired_on=world.day(250),
                            acquired_from=teller.id)
        report = ContinuityEngine(world).run()
        assert any("after" in v.message and "died" in v.message for v in report.errors)

    def test_effect_precedes_its_cause(self, world: World):
        cause = world.add_event("The cause", start_day=world.day(300))
        effect = world.add_event("The effect", start_day=world.day(200))
        world.link_cause(cause.id, effect.id)
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "event_before_place_founded" for v in report.errors)

    def test_two_legal_owners_at_once(self, world: World):
        """§11: administration may overlap; legal ownership may not."""
        a = world.add_entity("house", "House A")
        b = world.add_entity("house", "House B")
        town = world.add_entity("settlement", "Contested")
        world.assert_fact(a, "legally_owns", town, valid_from=world.day(100))
        world.assert_fact(b, "legally_owns", town, valid_from=world.day(150))
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "contradictory_control" for v in report.warnings)

    def test_a_clean_handover_is_not_flagged(self, world: World):
        a = world.add_entity("house", "House A")
        b = world.add_entity("house", "House B")
        town = world.add_entity("settlement", "Greyhaven")
        world.assert_fact(a, "legally_owns", town, valid_from=world.day(100))
        world.transfer("legally_owns", town, b, world.day(200))
        report = ContinuityEngine(world).run()
        assert not [v for v in report.violations if v.rule_key == "contradictory_control"]

    def test_settlement_with_no_region(self, world: World):
        world.add_entity("settlement", "Orphan")
        report = ContinuityEngine(world).run()
        assert any(v.rule_key == "settlement_without_region" for v in report.violations)


class TestSeverityAndSuppression:
    def test_severity_ordering_puts_errors_first(self, world: World):
        person = world.add_entity("person", "Ghost", exists_from=world.day(100),
                                  exists_to=world.day(200))
        world.add_entity("settlement", "Orphan")            # a NOTICE
        world.add_event("Later", start_day=world.day(300),
                        participants=[(person.id, "participant")])   # an ERROR
        report = ContinuityEngine(world).run()
        ranks = [v.severity.rank for v in report.violations]
        assert ranks == sorted(ranks, reverse=True)

    def test_minimum_severity_filters(self, world: World):
        world.add_entity("settlement", "Orphan")
        assert ContinuityEngine(world).run().violations
        assert ContinuityEngine(world).run(minimum=Severity.WARNING).violations == []

    def test_intentional_exceptions_can_be_suppressed(self, world: World):
        """§46: 'Allow intentional exceptions.'"""
        ghost = world.add_entity("person", "Ghost", exists_from=world.day(100),
                                 exists_to=world.day(200))
        world.add_event("The haunting", start_day=world.day(300),
                        participants=[(ghost.id, "participant")])

        report = ContinuityEngine(world).run()
        violation = next(v for v in report.errors if v.rule_key == "dead_character_acts")

        world.suppress(violation.rule_key, violation.fingerprint,
                       reason="He is a ghost. That is the whole point.")

        after = ContinuityEngine(world).run()
        assert not [v for v in after.errors if v.rule_key == "dead_character_acts"]
        assert after.suppressed == 1

        with_suppressed = ContinuityEngine(world).run(include_suppressed=True)
        assert [v for v in with_suppressed.errors if v.rule_key == "dead_character_acts"]

    def test_fingerprint_survives_rewording(self):
        """A suppression must not be undone by editing a message."""
        a = Violation("rule", Severity.ERROR, "One wording", entity_ids=("x", "y"), day=5)
        b = Violation("rule", Severity.ERROR, "Quite another", entity_ids=("y", "x"), day=5)
        assert a.fingerprint == b.fingerprint

        different_day = Violation("rule", Severity.ERROR, "One wording",
                                  entity_ids=("x", "y"), day=6)
        assert a.fingerprint != different_day.fingerprint

    def test_summary_text(self, world: World):
        assert "No continuity problems" in ContinuityEngine(world).run().summary()
        world.add_entity("settlement", "Orphan")
        assert "notice" in ContinuityEngine(world).run().summary()
