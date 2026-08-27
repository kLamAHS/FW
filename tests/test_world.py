"""Store and world-model tests (spec §2, §3, §11, §49, §53, §60, §77)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from fw.core.calendar.uncertain import Interval, circa, exact
from fw.core.store.db import Database, StoreError
from fw.core.world import World, WorldError


class TestWorldFile:
    def test_a_world_is_one_portable_file(self, tmp_path: Path):
        """§63: the project, the backup and the export are the same object."""
        path = tmp_path / "renn.fwworld"
        w = World.create(path, name="Renn")
        w.add_entity("house", "House Marr")
        w.close()

        assert path.exists()
        copy = tmp_path / "backup.fwworld"
        copy.write_bytes(path.read_bytes())

        reopened = World.open(copy)
        assert reopened.name == "Renn"
        assert [e.name for e in reopened.entities("house")] == ["House Marr"]
        reopened.close()

    def test_refuses_a_foreign_sqlite_file(self, tmp_path: Path):
        alien = tmp_path / "notours.db"
        conn = sqlite3.connect(alien)
        conn.execute("CREATE TABLE t (a)")
        conn.close()
        with pytest.raises(StoreError, match="not a world file"):
            Database(alien)

    def test_refuses_a_file_from_a_newer_build(self, tmp_path: Path):
        path = tmp_path / "future.fwworld"
        World.create(path).close()
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA user_version = 9999")
        conn.close()
        with pytest.raises(StoreError, match="newer version"):
            Database(path)

    def test_open_missing_file_is_an_error(self, tmp_path: Path):
        with pytest.raises(StoreError):
            Database(tmp_path / "nope.fwworld", create=False)

    def test_foreign_keys_are_enforced(self, world: World):
        """Off by default in SQLite, which would silently permit orphaned facts."""
        with pytest.raises(sqlite3.IntegrityError):
            world.db.insert("fact", {
                "id": "x", "project_id": world.project_id, "branch_id": world.branch_id,
                "subject_id": "nonexistent", "predicate_key": "trusts", "value": "yes",
                "created_at": "now", "updated_at": "now",
            })

    def test_starting_vocabulary_is_installed(self, world: World):
        assert world.db.scalar("SELECT count(*) FROM entity_type") > 20
        assert world.db.scalar("SELECT count(*) FROM predicate") > 50
        assert world.db.scalar("SELECT count(*) FROM scale") >= 4


class TestEntities:
    def test_add_and_read(self, world: World):
        e = world.add_entity("person", "Lady Mara", summary="Of House Veyne")
        assert world.get_entity(e.id).name == "Lady Mara"
        assert world.entity_named("Lady Mara").id == e.id
        assert world.count_entities("person") == 1

    def test_unknown_type_is_refused_with_a_useful_message(self, world: World):
        with pytest.raises(WorldError, match="unknown entity type"):
            world.add_entity("wyvern", "Smoke")

    def test_writers_can_invent_their_own_types(self, world: World):
        """§60: a custom type must behave exactly like a built-in one."""
        world.add_entity_type("wyvern", "Wyvern", "Wyverns", category="creatures")
        w = world.add_entity("wyvern", "Smoke")
        assert world.entities("wyvern") == [w]
        assert world.search("Smoke")[0].id == w.id

    def test_writers_can_invent_their_own_predicates(self, world: World):
        world.add_predicate("bonded_to", "bonded to", symmetric=True)
        a = world.add_entity("person", "Rider")
        world.add_entity_type("wyvern", "Wyvern")
        b = world.add_entity("wyvern", "Smoke")
        world.assert_fact(a, "bonded_to", b)
        assert len(world.facts_where("bonded_to")) == 1

    def test_existence_is_temporal(self, world: World):
        """A settlement founded in 240 must not appear on a map of 215."""
        town = world.add_entity("settlement", "Newford", exists_from=world.day(240))
        assert not town.exists_on(world.day(215))
        assert town.exists_on(world.day(300))
        assert town.id not in world.state_at(world.day(215)).entities
        assert town.id in world.state_at(world.day(300)).entities

    def test_undated_entities_exist_always(self, world: World):
        """Silence means 'it is just there', not 'it does not exist'."""
        e = world.add_entity("region", "The Marches")
        assert e.exists_on(-10_000)
        assert e.exists_on(10_000)

    def test_update_reindexes_search(self, world: World):
        e = world.add_entity("settlement", "Oldname")
        world.update_entity(e.id, name="Newname")
        assert world.search("Newname")
        assert not [x for x in world.search("Oldname") if x.id == e.id]

    def test_delete(self, world: World):
        e = world.add_entity("settlement", "Doomed")
        world.delete_entity(e.id)
        assert world.get_entity(e.id) is None
        assert not world.search("Doomed")


class TestFacts:
    def test_a_property_and_a_relationship_are_the_same_shape(self, world: World):
        """The fact spine: both carry dates, confidence and secrecy identically."""
        p = world.add_entity("person", "Mara")
        h = world.add_entity("house", "House Veyne")
        prop = world.assert_fact(p, "occupation", value="knight", valid_from=world.day(220))
        rel = world.assert_fact(p, "member_of", h, valid_from=world.day(220))
        assert not prop.is_relationship and rel.is_relationship
        assert prop.holds_on(world.day(230)) and rel.holds_on(world.day(230))
        assert not prop.holds_on(world.day(210))

    def test_fact_needs_an_object_or_a_value(self, world: World):
        p = world.add_entity("person", "Mara")
        with pytest.raises(WorldError, match="object entity or a value"):
            world.assert_fact(p, "occupation")

    def test_unknown_predicate_is_refused(self, world: World):
        p = world.add_entity("person", "Mara")
        with pytest.raises(WorldError, match="unknown predicate"):
            world.assert_fact(p, "vibes_with", value="x")

    def test_asymmetric_feeling(self, world: World):
        """§5: Mara may trust Edric while Edric distrusts Mara."""
        mara = world.add_entity("person", "Mara")
        edric = world.add_entity("person", "Edric")
        world.assert_fact(mara, "trusts", edric, strength="deeply_trusts")
        world.assert_fact(edric, "trusts", mara, strength="distrusts")
        out = world.facts_where("trusts", subject_id=mara.id)
        back = world.facts_where("trusts", subject_id=edric.id)
        assert out[0].strength == "deeply_trusts"
        assert back[0].strength == "distrusts"

    def test_transfer_closes_history_rather_than_erasing_it(self, world: World):
        """§106.3 / §79: conquest ends a fact, it does not make it never have been true."""
        marr = world.add_entity("house", "House Marr")
        orren = world.add_entity("house", "House Orren")
        town = world.add_entity("settlement", "Greyhaven")
        world.assert_fact(marr, "legally_owns", town, valid_from=world.day(312))
        world.transfer("legally_owns", town, orren, world.day(428))

        before = world.state_at(world.day(400)).facts_by_predicate("legally_owns")
        after = world.state_at(world.day(500)).facts_by_predicate("legally_owns")
        assert [f.subject_id for f in before] == [marr.id]
        assert [f.subject_id for f in after] == [orren.id]

    def test_uncertain_validity(self, world: World):
        a = world.add_entity("house", "House Marr")
        b = world.add_entity("settlement", "Greyhaven")
        f = world.assert_fact(
            a, "legally_owns", b,
            interval=Interval(start=circa(world.day(312), 400), end=exact(world.day(428))),
        )
        stored = world.get_fact(f.id)
        assert stored.valid_from < world.day(312) < stored.valid_from_hi

    def test_secrecy_can_be_filtered(self, world: World):
        """§6: what the world knows and what is true are different queries."""
        a = world.add_entity("person", "Mara")
        b = world.add_entity("person", "Edric")
        world.assert_fact(a, "feels_about", b, strength="loves", secrecy="secret")
        assert len(world.facts_where("feels_about")) == 1
        assert len(world.facts_where("feels_about", include_secret=False)) == 0
        assert world.facts_where("feels_about")[0].is_secret


class TestBidirectionalLinking:
    def test_the_inverse_is_resolved_not_duplicated(self, world: World):
        """§77 and §106.1: never make the writer enter the same fact twice."""
        veyne = world.add_entity("house", "House Veyne")
        grey = world.add_entity("settlement", "Greyhaven")
        world.assert_fact(veyne, "legally_owns", grey)

        assert world.db.scalar("SELECT count(*) FROM fact") == 1     # one row, not two

        from_house = world.facts_about(veyne.id)
        from_town = world.facts_about(grey.id)
        assert [(f.predicate_key, f.object_id) for f in from_house] == [("legally_owns", grey.id)]
        assert [(f.predicate_key, f.object_id) for f in from_town] == [
            ("legally_owned_by", veyne.id)
        ]

    def test_symmetric_predicates_read_the_same_from_both_sides(self, world: World):
        a = world.add_entity("house", "House Marr")
        b = world.add_entity("house", "House Orren")
        world.assert_fact(a, "at_war_with", b)
        assert world.facts_about(b.id)[0].predicate_key == "at_war_with"
        assert world.facts_about(b.id)[0].object_id == a.id


class TestTerritorialControl:
    def test_four_authorities_at_once(self, world: World):
        """§11: the brief's sharpest distinction, and it must hold simultaneously."""
        orren = world.add_entity("house", "House Orren")
        veyne = world.add_entity("house", "House Veyne")
        marr = world.add_entity("house", "House Marr")
        crown = world.add_entity("realm", "The Crown")
        region = world.add_entity("region", "The Reach")

        world.assert_fact(orren, "legally_owns", region)
        world.assert_fact(veyne, "administers", region)
        world.assert_fact(marr, "occupies", region)
        world.assert_fact(crown, "taxes", region)
        world.assert_fact(veyne, "claims", region)

        state = world.state_at(world.day(300))
        holders = {
            f.predicate_key: state.entities[f.subject_id].name
            for f in state.facts
            if f.object_id == region.id
        }
        assert holders == {
            "legally_owns": "House Orren",
            "administers": "House Veyne",
            "occupies": "House Marr",
            "taxes": "The Crown",
            "claims": "House Veyne",
        }


class TestTraversal:
    @pytest.fixture
    def feudal(self, world: World):
        crown = world.add_entity("realm", "The Crown")
        veyne = world.add_entity("house", "House Veyne")
        marr = world.add_entity("house", "House Marr")
        knight = world.add_entity("house", "Ser Aldric")
        world.assert_fact(veyne, "vassal_of", crown)
        world.assert_fact(marr, "vassal_of", veyne)
        world.assert_fact(knight, "vassal_of", marr)
        return world, crown, veyne, marr, knight

    def test_upward_chain(self, feudal):
        w, crown, veyne, marr, knight = feudal
        chain = w.follow(knight.id, "vassal_of")
        assert [w.get_entity(i).name for i, _ in chain] == [
            "House Marr", "House Veyne", "The Crown"
        ]

    def test_downward_chain(self, feudal):
        """§49: 'which houses serve House Veyne?'"""
        w, crown, veyne, marr, knight = feudal
        under = w.follow(veyne.id, "vassal_of", direction="in")
        assert {w.get_entity(i).name for i, _ in under} == {"House Marr", "Ser Aldric"}

    def test_depth_limit(self, feudal):
        w, crown, veyne, marr, knight = feudal
        assert len(w.follow(knight.id, "vassal_of", max_depth=1)) == 1

    def test_traversal_respects_dates(self, world: World):
        """An allegiance that had not been sworn yet must not appear in the chain."""
        a = world.add_entity("house", "A")
        b = world.add_entity("house", "B")
        world.assert_fact(a, "vassal_of", b, valid_from=world.day(300))
        assert world.follow(a.id, "vassal_of", at=world.day(250)) == []
        assert len(world.follow(a.id, "vassal_of", at=world.day(350))) == 1

    def test_kin_within_n_generations(self, world: World):
        """§49: 'who is related to Lady Mara within three generations?'"""
        people = {n: world.add_entity("person", n)
                  for n in ("Great", "Grand", "Parent", "Mara", "Child", "Stranger")}
        for parent, child in (("Great", "Grand"), ("Grand", "Parent"),
                              ("Parent", "Mara"), ("Mara", "Child")):
            world.assert_fact(people[parent], "parent_of", people[child])

        near = world.neighbours(people["Mara"].id, ["parent_of"], hops=2)
        names = {world.get_entity(i).name for i in near}
        assert names == {"Parent", "Grand", "Child"}
        assert "Stranger" not in names

        far = world.neighbours(people["Mara"].id, ["parent_of"], hops=3)
        assert "Great" in {world.get_entity(i).name for i in far}

    def test_cycles_terminate(self, world: World):
        """A world can contain a mutual oath; the walk must not spin."""
        a = world.add_entity("house", "A")
        b = world.add_entity("house", "B")
        world.assert_fact(a, "vassal_of", b)
        world.assert_fact(b, "vassal_of", a)
        assert len(world.follow(a.id, "vassal_of", max_depth=20)) <= 2
        assert len(world.neighbours(a.id, ["vassal_of"], hops=10)) == 1


class TestSearch:
    def test_exact_stemmed_and_fuzzy(self, world: World):
        """§53 requires exact match, fuzzy match, and matching on properties."""
        world.add_entity("settlement", "Greyhaven", summary="A port of the iron coast")
        world.add_entity("settlement", "Rennford")
        world.add_entity("house", "House Marr", tags=["northern"])

        assert [e.name for e in world.search("Greyhaven")] == ["Greyhaven"]
        assert "Greyhaven" in [e.name for e in world.search("eyhav")]      # fuzzy substring
        assert "Greyhaven" in [e.name for e in world.search("iron")]       # summary
        assert "House Marr" in [e.name for e in world.search("northern")]  # tags

    def test_type_filter_and_empty_query(self, world: World):
        world.add_entity("settlement", "Ford")
        world.add_entity("house", "Ford")
        assert len(world.search("Ford")) == 2
        assert len(world.search("Ford", type_key="house")) == 1
        assert world.search("   ") == []

    def test_punctuation_does_not_raise(self, world: World):
        world.add_entity("settlement", "Greyhaven")
        assert world.search('"; DROP TABLE entity; --') == []
        assert world.count_entities() == 1


class TestStateAtDate:
    def test_facts_about_the_unborn_are_excluded(self, world: World):
        """A marriage cannot show on a map of a year before either party existed."""
        a = world.add_entity("person", "Early", exists_from=world.day(100),
                             exists_to=world.day(150))
        b = world.add_entity("person", "Late", exists_from=world.day(300))
        world.assert_fact(a, "married_to", b)          # deliberately undated
        assert world.state_at(world.day(320)).facts == []

    def test_secret_facts_can_be_hidden_from_a_view(self, world: World):
        a = world.add_entity("person", "Mara")
        b = world.add_entity("person", "Edric")
        world.assert_fact(a, "feels_about", b, strength="loves", secrecy="secret")
        assert len(world.state_at(world.day(300)).facts) == 1
        assert world.state_at(world.day(300), include_secret=False).facts == []
