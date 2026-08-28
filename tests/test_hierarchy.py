"""Containment and belonging (§2, §12, §54): what is inside a place, who is in a group."""

from __future__ import annotations

import pytest

from fw.core.derive.hierarchy import Hierarchy
from fw.core.seed.renn import PRESENT_YEAR
from fw.core.world import World


@pytest.fixture
def day(renn: World) -> int:
    return renn.day(PRESENT_YEAR)


class TestPlaceContents:
    def test_a_realm_holds_its_regions_and_their_settlements(self, renn: World, day):
        tree = Hierarchy(renn).contents(renn.entity_named("The Kingdom of Renn").id, at=day)
        regions = {c.entity.name for c in tree.children}
        assert regions == {"The Northmarch", "The Vale of Renn", "The Salt Reach"}

        north = next(c for c in tree.children if c.entity.name == "The Northmarch")
        assert {c.entity.name for c in north.children} == {"Greyhaven", "Northwatch"}
        assert tree.count() >= 8

    def test_cities_sort_before_hamlets(self, renn: World, day):
        """A region should read capital, city, town, village — not alphabetically."""
        vale = Hierarchy(renn).contents(renn.entity_named("The Vale of Renn").id, at=day)
        assert vale.children[0].settlement_type == "capital"     # Rennford leads

    def test_the_chain_upward_is_a_breadcrumb(self, renn: World, day):
        chain = Hierarchy(renn).chain_above(renn.entity_named("Greyhaven").id, at=day)
        assert [e.name for e in chain] == ["The Northmarch", "The Kingdom of Renn"]

    def test_a_place_that_contains_itself_does_not_hang(self, world: World):
        """Worlds do contain loops; a walk that trusted the data would never return."""
        a = world.add_entity("region", "Ouroboros")
        b = world.add_entity("settlement", "Inner")
        world.assert_fact(b, "located_in", a)
        world.assert_fact(a, "located_in", b)          # the loop
        tree = Hierarchy(world).contents(a.id)
        assert tree is not None
        assert tree.count() >= 1                        # terminated, and found the child

    def test_contents_respect_the_date(self, renn: World):
        """A settlement founded later is not in its region yet."""
        early = Hierarchy(renn).contents(
            renn.entity_named("The Northmarch").id, at=renn.day(100))
        assert "Greyhaven" not in {c.entity.name for c in early.children}

    def test_an_unknown_place_is_nothing_rather_than_a_crash(self, renn: World):
        assert Hierarchy(renn).contents("no-such-place") is None


class TestGroupsInAPlace:
    def test_everyone_in_the_north(self, renn: World, day):
        """The question the whole slice exists for."""
        found = Hierarchy(renn).groups_in(
            renn.entity_named("The Northmarch").id, at=day)
        names = {g.name for g, _ in found}
        assert {"House Marr", "House Dray", "The Hillfolk",
                "The Ironmongers of Red Ford"} <= names

    def test_a_seat_inside_the_region_still_counts(self, renn: World, day):
        """House Marr is seated in Northwatch, which is *in* the Northmarch — asking
        about the region must find it without the writer restating the obvious."""
        found = dict((g.name, how) for g, how in Hierarchy(renn).groups_in(
            renn.entity_named("The Northmarch").id, at=day))
        assert "seated in Northwatch" in found["House Marr"]

    def test_only_the_seat_when_nesting_is_off(self, renn: World, day):
        shallow = Hierarchy(renn).groups_in(
            renn.entity_named("The Northmarch").id, at=day, include_nested=False)
        assert "House Marr" not in {g.name for g, _ in shallow}
        assert "The Hillfolk" in {g.name for g, _ in shallow}   # active in the region


class TestGroupRosters:
    def test_a_house_lists_its_people_and_its_lesser_houses(self, renn: World, day):
        roster = Hierarchy(renn).members_of(renn.entity_named("House Marr").id, at=day)
        by_name = {m.entity.name: m.relation for m in roster}
        assert by_name.get("House Dray") == "branch"
        assert "Edric" in by_name

    def test_minor_houses_are_walkable_from_the_banner(self, renn: World, day):
        """vassal_of and subgroup_of are transitive, so one walk finds the whole tree."""
        branches = Hierarchy(renn).branches_of(
            renn.entity_named("House Veyne").id, at=day)
        names = {e.name: depth for e, depth in branches}
        assert names.get("House Marr") == 1        # sworn directly
        assert names.get("House Dray") == 2        # under Marr, two steps down
        assert names.get("House Pell") == 1        # a cadet branch of Veyne

    def test_a_group_says_where_it_belongs(self, renn: World, day):
        seats = Hierarchy(renn).seats_of(
            renn.entity_named("The Ironmongers of Red Ford").id, at=day)
        described = {p.name: how for p, how in seats}
        assert described["Red Ford"] == "based in"
        assert described["The Northmarch"] == "active in"

    def test_the_roster_names_the_head_first(self, world: World):
        guild = world.add_entity("guild", "The Guild")
        master = world.add_entity("person", "The Master")
        hand = world.add_entity("person", "A Hand")
        world.assert_fact(hand, "member_of", guild)
        world.assert_fact(master, "head_of", guild)
        roster = Hierarchy(world).members_of(guild.id)
        assert [m.entity.name for m in roster] == ["The Master", "A Hand"]

    def test_every_kind_of_group_is_listed(self, renn: World, day):
        kinds = {g.type_key for g in Hierarchy(renn).groups(at=day)}
        assert {"house", "guild", "order", "tribe", "company"} <= kinds


class TestVocabularySync:
    def test_an_older_world_gains_predicates_added_since(self, tmp_path):
        """A world written before a predicate existed must not be locked out of it."""
        path = tmp_path / "elder.fwworld"
        w = World.create(path, name="Elder")
        w.db.execute("DELETE FROM predicate WHERE key IN ('based_in', 'subgroup_of')")
        w.db.execute("DELETE FROM entity_type WHERE key = 'order'")
        w.close()

        again = World.open(path)
        try:
            keys = {r["key"] for r in again.db.query("SELECT key FROM predicate")}
            assert {"based_in", "subgroup_of"} <= keys
            order = again.add_entity("order", "The Order")     # the type came back too
            place = again.add_entity("settlement", "Hall")
            again.assert_fact(order, "based_in", place)        # and the predicate works
        finally:
            again.close()

    def test_sync_leaves_a_writer_s_own_edits_alone(self, world: World):
        world.db.execute(
            "UPDATE predicate SET label = 'stands with' WHERE key = 'allied_with'")
        world.add_predicate("bonded_to", "bonded to", symmetric=True)
        world.sync_vocabulary()
        assert world.db.scalar(
            "SELECT label FROM predicate WHERE key = 'allied_with'") == "stands with"
        assert world.db.scalar(
            "SELECT count(*) FROM predicate WHERE key = 'bonded_to'") == 1
