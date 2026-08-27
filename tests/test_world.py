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


class TestReification:
    """A fact about another fact (§33, §57).

    The column exists before any feature needs it, because the alternative — a second,
    parallel mechanism for assertions-about-assertions — would share none of the fact
    spine's temporality, confidence or secrecy, and would split every related query.
    """

    def test_a_fact_can_be_about_another_fact(self, world: World):
        aldren = world.add_entity("person", "King Aldren")
        oren = world.add_entity("person", "Prince Oren")
        crown = world.add_entity("house", "The Crown")

        parentage = world.assert_fact(aldren, "legal_parent_of", oren)
        account = world.assert_fact(
            crown, "knows_about", oren, about_fact_id=parentage.id,
            confidence="disputed", note="The Crown's public position on the parentage.",
        )

        assert world.get_fact(account.id).about_fact_id == parentage.id
        assert world.get_fact(parentage.id).about_fact_id is None

    def test_two_accounts_of_one_claim_coexist(self, world: World):
        """§57: 'publicly believed' and 'canonical secret' are two assertions, not a field."""
        aldren = world.add_entity("person", "King Aldren")
        corren = world.add_entity("person", "Lord Corren")
        oren = world.add_entity("person", "Prince Oren")

        public = world.assert_fact(aldren, "legal_parent_of", oren, confidence="canon")
        private = world.assert_fact(corren, "parent_of", oren,
                                    confidence="canon", secrecy="deep_secret")

        # Both are true at once, and each keeps its own provenance.
        assert public.secrecy == "public"
        assert private.is_secret
        assert len(world.facts_where(subject_id=corren.id)) == 1
        assert len(world.facts_where(object_id=oren.id, include_secret=False)) == 1

    def test_deleting_the_subject_fact_removes_facts_about_it(self, world: World):
        a = world.add_entity("person", "A")
        b = world.add_entity("person", "B")
        base = world.assert_fact(a, "trusts", b)
        about = world.assert_fact(b, "knows_about", a, about_fact_id=base.id)

        world.db.execute("DELETE FROM fact WHERE id = ?", (base.id,))
        assert world.get_fact(about.id) is None    # cascaded, not orphaned


class TestRevisionLog:
    """§59: every mutation leaves an append-only trace."""

    def test_create_update_delete_are_all_logged(self, world: World):
        e = world.add_entity("settlement", "Newtown")
        world.update_entity(e.id, name="Renamed")
        world.delete_entity(e.id)

        history = world.revisions_for(e.id)
        actions = [h["action"] for h in history]
        assert actions == ["delete", "update", "insert"]     # newest first
        update = history[1]
        assert update["before"]["name"] == "Newtown"
        assert update["after"]["name"] == "Renamed"

    def test_fact_lifecycle_is_logged(self, world: World):
        a = world.add_entity("house", "House A")
        b = world.add_entity("settlement", "B-town")
        fact = world.assert_fact(a, "legally_owns", b, valid_from=world.day(100))
        world.end_fact(fact.id, world.day(200))
        world.delete_fact(fact.id)

        history = world.revisions_for(fact.id)
        actions = [h["action"] for h in history]
        assert actions == ["delete", "update", "insert"]
        assert history[1]["after"]["valid_to"] == world.day(200)
        # the deletion kept what was lost, so even a deletion is recoverable
        assert history[0]["before"]["predicate_key"] == "legally_owns"

    def test_deleting_a_missing_fact_is_a_quiet_no_op(self, world: World):
        world.delete_fact("nonexistent")

    def test_recently_edited_attributes_facts_to_their_subject(self, world: World):
        person = world.add_entity("person", "Mara")
        other = world.add_entity("person", "Edric")
        world.assert_fact(person, "trusts", other, strength="deeply_trusts")

        recent = world.recently_edited(limit=5)
        names = [entity.name for entity, _ in recent]
        # the fact edit surfaces as Mara, not as an opaque fact id
        assert names[0] == "Mara"
        # each entity appears once, however many changes it has
        assert len(names) == len(set(names))

    def test_recently_edited_skips_deleted_entities(self, world: World):
        e = world.add_entity("settlement", "Doomed")
        world.delete_entity(e.id)
        assert "Doomed" not in [x.name for x, _ in world.recently_edited()]


class TestSearchRanking:
    def test_a_name_hit_outranks_a_summary_mention(self, renn: World):
        """Searching 'Northmarch' must find The Northmarch itself first — not
        Northwatch, whose summary merely mentions the region. Caught by driving the
        entity picker in a browser: it chose the wrong entity with full confidence."""
        names = [e.name for e in renn.search("Northmarch")]
        assert names[0] == "The Northmarch"
        assert "Northwatch" in names          # still found, just not first


class TestReviewFindings:
    """Regression tests for the adversarial review of the editing slice."""

    def test_delete_entity_logs_every_cascaded_fact_in_full(self, world: World):
        """The FK cascade must not destroy facts the log knows nothing about."""
        hub = world.add_entity("house", "Hub House")
        other = world.add_entity("person", "Vassal")
        fact = world.assert_fact(other, "vassal_of", hub, secrecy="secret",
                                 strength="overwhelming", note="sworn at midwinter")
        world.delete_entity(hub.id)

        history = world.revisions_for(fact.id)
        assert [h["action"] for h in history] == ["delete", "insert"]
        snapshot = history[0]["before"]
        # the snapshot is the complete row, not a lossy subset
        assert snapshot["secrecy"] == "secret"
        assert snapshot["strength"] == "overwhelming"
        assert snapshot["note"] == "sworn at midwinter"
        assert snapshot["predicate_key"] == "vassal_of"

    def test_entity_delete_snapshot_is_the_full_row(self, world: World):
        e = world.add_entity("settlement", "Doomed", summary="A summary worth keeping",
                             exists_from=world.day(100), tags=["northern"])
        world.delete_entity(e.id)
        snapshot = world.revisions_for(e.id)[0]["before"]
        assert snapshot["summary"] == "A summary worth keeping"
        assert snapshot["exists_from"] == world.day(100)
        assert snapshot["tags"] == ["northern"]

    def test_end_fact_refuses_a_day_before_the_fact_began(self, world: World):
        """An inverted interval is true on no day — the fact would silently vanish."""
        a = world.add_entity("house", "A")
        b = world.add_entity("settlement", "B")
        fact = world.assert_fact(a, "legally_owns", b, valid_from=world.day(300))
        with pytest.raises(WorldError, match="before it began"):
            world.end_fact(fact.id, world.day(200))
        # the fact is untouched and still visible
        assert world.get_fact(fact.id).valid_to is None

    def test_end_fact_refuses_a_missing_fact(self, world: World):
        """No phantom revisions for rows that never existed."""
        with pytest.raises(WorldError, match="no fact"):
            world.end_fact("ghost", 5)
        assert world.revisions_for("ghost") == []

    def test_recently_edited_survives_fact_deletion(self, world: World):
        """The newest edit must not vanish because its fact row is gone."""
        mara = world.add_entity("person", "Mara")
        edric = world.add_entity("person", "Edric")
        fact = world.assert_fact(mara, "trusts", edric, strength="deeply_trusts")
        world.delete_fact(fact.id)

        names = [e.name for e, _ in world.recently_edited(limit=5)]
        assert names[0] == "Mara"       # the deletion is her newest edit

    def test_update_before_and_after_share_one_shape(self, world: World):
        """tags must not appear as a JSON string on one side and a list on the other."""
        e = world.add_entity("settlement", "Tagged", tags=["north"])
        world.update_entity(e.id, tags=["north", "ally"])
        update = world.revisions_for(e.id)[0]
        assert update["before"]["tags"] == ["north"]
        assert update["after"]["tags"] == ["north", "ally"]

    def test_decode_json_honours_an_explicit_none_default(self):
        from fw.core.store.db import decode_json
        assert decode_json(None, None) is None
        assert decode_json("", None) is None
        assert decode_json(None) == {}
        assert decode_json("[1]", None) == [1]


class TestRestore:
    """§59 restore points: every recorded change can be walked back."""

    def test_a_deleted_entity_returns_with_its_cascaded_facts(self, renn: World):
        marr = renn.entity_named("House Marr")
        connections = len(renn.facts_about(marr.id))
        assert connections > 0

        renn.delete_entity(marr.id)
        assert renn.entity_named("House Marr") is None

        deleted = renn.recently_deleted()
        assert deleted[0]["name"] == "House Marr"

        message = renn.restore(deleted[0]["revision_id"])
        assert "House Marr" in message
        back = renn.entity_named("House Marr")
        assert back is not None
        assert back.summary == marr.summary
        assert len(renn.facts_about(back.id)) == connections
        # and it no longer shows as deleted
        assert all(d["name"] != "House Marr" for d in renn.recently_deleted())

    def test_restoring_an_update_inverts_it_and_is_itself_reversible(self, world: World):
        e = world.add_entity("settlement", "Oldname")
        world.update_entity(e.id, name="Newname")

        rename = world.revisions_for(e.id)[0]
        world.restore(rename["id"])
        assert world.get_entity(e.id).name == "Oldname"

        # the restore logged its own inverse, so restoring it is a redo
        undo = world.revisions_for(e.id)[0]
        world.restore(undo["id"])
        assert world.get_entity(e.id).name == "Newname"

    def test_a_deleted_fact_can_come_back_alone(self, world: World):
        a = world.add_entity("person", "A")
        b = world.add_entity("person", "B")
        fact = world.assert_fact(a, "trusts", b, strength="deeply_trusts",
                                 note="a note worth keeping")
        world.delete_fact(fact.id)
        delete_rev = world.revisions_for(fact.id)[0]

        world.restore(delete_rev["id"])
        restored = world.get_fact(fact.id)
        assert restored is not None
        assert restored.strength == "deeply_trusts"
        assert restored.note == "a note worth keeping"

    def test_restoring_a_fact_whose_endpoint_is_gone_says_so(self, world: World):
        a = world.add_entity("person", "A")
        b = world.add_entity("person", "B")
        fact = world.assert_fact(a, "trusts", b)
        world.delete_fact(fact.id)
        fact_delete = world.revisions_for(fact.id)[0]
        world.delete_entity(b.id)

        with pytest.raises(WorldError, match="restore that entity first"):
            world.restore(fact_delete["id"])

    def test_restore_refuses_nonsense(self, world: World):
        with pytest.raises(WorldError, match="no revision"):
            world.restore(999_999)
        e = world.add_entity("settlement", "Made")
        insert_rev = world.revisions_for(e.id)[0]
        with pytest.raises(WorldError, match="nothing to restore"):
            world.restore(insert_rev["id"])

    def test_restore_ignores_hostile_snapshot_keys(self, world: World):
        """A crafted world file must not smuggle SQL through revision JSON."""
        e = world.add_entity("settlement", "Victim")
        world.delete_entity(e.id)
        rev_id = world.recently_deleted()[0]["revision_id"]
        # poison the snapshot with a key that is not a column
        import json
        row = world.db.one("SELECT before FROM revision WHERE id = ?", (rev_id,))
        poisoned = {**json.loads(row["before"]),
                    "name) VALUES ('x'); DROP TABLE entity; --": 1}
        world.db.execute("UPDATE revision SET before = ? WHERE id = ?",
                         (json.dumps(poisoned), rev_id))

        world.restore(rev_id)                      # must not raise, must not execute
        assert world.entity_named("Victim") is not None
        assert world.db.scalar("SELECT count(*) FROM entity") >= 1


class TestRestoreCascade:
    """A delete logs its whole FK cascade under a batch marker, and restore replays it."""

    def test_restore_brings_back_everything_the_cascade_took(self, world: World):
        """Not just facts: titles, holdings, participations, knowledge, secrets,
        geometry and route segments all die with an entity and must all return."""
        day = world.day(200, 1, 1)
        lord = world.add_entity("person", "Lord Doomed")
        friend = world.add_entity("person", "Friend")
        seat = world.add_entity("settlement", "Seat")

        world.assert_fact(lord, "trusts", friend, strength="deeply_trusts")
        title = world.add_title("Lord of Seat", entity_id=lord.id)
        world.grant_title(title.id, lord.id, from_day=day)
        event = world.add_event(
            "The duel", start_day=day,
            participants=[(lord.id, "duelist"), (friend.id, "witness")])
        scene = world.add_scene("A quiet word", day=day, location_id=seat.id,
                                pov_id=lord.id, participants=[lord.id, friend.id])
        secret = world.add_secret("The lord's debt", about_id=lord.id)
        world.set_knowledge(friend.id, secret.id, "suspects")
        world.add_geometry(lord.id, "point", [3.0, 4.0])
        world.add_route_segment(seat.id, lord.id, 12.0)

        world.delete_entity(lord.id)

        # the cascade really did run this deep
        assert world.titles() == []
        assert world.secrets() == []
        assert world.route_segments() == []
        assert [p.id for p in world.scene_participants(scene.id)] == [friend.id]
        assert [p.id for p, _ in world.event_participants(event.id)] == [friend.id]
        assert world.get_scene(scene.id).pov_id is None   # SET NULL on a survivor

        message = world.restore(world.recently_deleted()[0]["revision_id"])
        assert "Lord Doomed" in message and "related record" in message

        back = world.entity_named("Lord Doomed")
        assert back is not None and back.id == lord.id
        assert len(world.facts_about(lord.id)) == 1
        title_back = world.title_named("Lord of Seat")
        assert title_back is not None
        holdings = world.title_holdings(title_back.id)
        assert [h.holder_id for h in holdings] == [lord.id]
        assert {p.id for p, _ in world.event_participants(event.id)} == {lord.id,
                                                                         friend.id}
        assert {p.id for p in world.scene_participants(scene.id)} == {lord.id,
                                                                      friend.id}
        secrets = world.secrets()
        assert [s.name for s in secrets] == ["The lord's debt"]
        assert [k.observer_id for k in world.knowledge_of(secrets[0].id)] == [friend.id]
        assert world.geometry_for(lord.id) is not None
        assert len(world.route_segments()) == 1
        # and the surviving scene got its point-of-view re-linked
        assert world.get_scene(scene.id).pov_id == lord.id

    def test_two_deletes_in_the_same_second_stay_separate(self, world: World):
        """The old batch heuristic matched on the timestamp, so two entities deleted
        in the same second swept each other's facts into one restore."""
        a = world.add_entity("person", "A")
        b = world.add_entity("person", "B")
        c = world.add_entity("person", "C")
        world.assert_fact(a, "trusts", c)
        world.assert_fact(b, "trusts", c)

        world.delete_entity(a.id)
        world.delete_entity(b.id)          # lands within the same wall-clock second

        rev_b = next(d for d in world.recently_deleted() if d["name"] == "B")
        world.restore(rev_b["revision_id"])
        assert world.get_entity(b.id) is not None
        assert world.get_entity(a.id) is None            # A stays deleted
        assert len(world.facts_about(b.id)) == 1
        # and A's own restore still has its fact waiting
        rev_a = next(d for d in world.recently_deleted() if d["name"] == "A")
        world.restore(rev_a["revision_id"])
        assert len(world.facts_about(a.id)) == 1

    def test_delete_restore_delete_offers_only_the_newest_version(self, world: World):
        region = world.add_entity("region", "The March")
        e = world.add_entity("settlement", "Phoenix")
        world.assert_fact(e, "located_in", region)

        world.delete_entity(e.id)
        world.restore(world.recently_deleted()[0]["revision_id"])
        world.update_entity(e.id, summary="Risen once")
        world.assert_fact(region, "administers", e)
        world.delete_entity(e.id)

        offers = [d for d in world.recently_deleted() if d["name"] == "Phoenix"]
        assert len(offers) == 1                          # not one per deletion
        world.restore(offers[0]["revision_id"])
        back = world.get_entity(e.id)
        assert back.summary == "Risen once"              # the newest version returned
        assert len(world.facts_about(e.id)) == 2

    def test_deleting_a_fact_takes_and_restores_the_claims_about_it(self, world: World):
        a = world.add_entity("person", "A")
        b = world.add_entity("person", "B")
        f1 = world.assert_fact(a, "trusts", b)
        f2 = world.assert_fact(b, "trusts", a, about_fact_id=f1.id, note="doubted")
        world.add_secret("Sealed letter", about_id=a.id, fact_id=f1.id)

        world.delete_fact(f1.id)
        assert world.get_fact(f2.id) is None             # cascaded with its subject
        assert world.secrets()[0].fact_id is None        # SET NULL on the survivor

        rev = world.revisions_for(f1.id)[0]
        message = world.restore(rev["id"])
        assert "dependent" in message
        assert world.get_fact(f1.id) is not None
        restored_about = world.get_fact(f2.id)
        assert restored_about is not None
        assert restored_about.note == "doubted"
        assert world.secrets()[0].fact_id == f1.id       # re-linked

    def test_deleting_an_entity_cleans_and_restore_rebuilds_its_map_index(
            self, world: World):
        e = world.add_entity("settlement", "Mapped")
        world.add_geometry(e.id, "point", [10.0, 20.0])
        boxes = world.db.scalar("SELECT count(*) FROM geometry_bbox")

        world.delete_entity(e.id)
        # the R*Tree is not FK-aware; a leak here grows the file forever
        assert world.db.scalar("SELECT count(*) FROM geometry_bbox") == boxes - 1

        world.restore(world.recently_deleted()[0]["revision_id"])
        assert world.db.scalar("SELECT count(*) FROM geometry_bbox") == boxes
        assert world.geometry_for(e.id) is not None

    def test_restoring_a_fact_twice_says_it_is_already_there(self, world: World):
        a = world.add_entity("person", "A")
        b = world.add_entity("person", "B")
        fact = world.assert_fact(a, "trusts", b)
        world.delete_fact(fact.id)
        rev = world.revisions_for(fact.id)[0]

        world.restore(rev["id"])
        with pytest.raises(WorldError, match="already exists"):
            world.restore(rev["id"])

    def test_restoring_an_update_with_no_usable_columns_refuses(self, world: World):
        """A restore that would change nothing must not report success."""
        import json
        e = world.add_entity("settlement", "S")
        world.update_entity(e.id, name="S2")
        rev = world.revisions_for(e.id)[0]
        world.db.execute("UPDATE revision SET before = ? WHERE id = ?",
                         (json.dumps({"bogus_column": 1}), rev["id"]))
        with pytest.raises(WorldError, match="no restorable columns"):
            world.restore(rev["id"])


class TestCausality:
    """§32: causal chains must stay a DAG and read back deduplicated."""

    def test_linking_twice_is_a_quiet_no_op(self, world: World):
        a = world.add_event("Flood")
        b = world.add_event("Crop failure")
        assert world.link_cause(a.id, b.id) is True
        assert world.link_cause(a.id, b.id) is False
        assert world.db.scalar("SELECT count(*) FROM causal_link") == 1

    def test_self_links_and_loops_are_refused(self, world: World):
        a = world.add_event("Flood")
        b = world.add_event("Crop failure")
        c = world.add_event("Unrest")
        world.link_cause(a.id, b.id)
        world.link_cause(b.id, c.id)
        with pytest.raises(WorldError, match="cause itself"):
            world.link_cause(a.id, a.id)
        with pytest.raises(WorldError, match="causal loop"):
            world.link_cause(c.id, a.id)      # would make the chain circular

    def test_a_diamond_reports_each_consequence_once(self, world: World):
        a = world.add_event("Flood")
        b = world.add_event("Crop failure")
        c = world.add_event("Livestock loss")
        d = world.add_event("Famine")
        world.link_cause(a.id, b.id)
        world.link_cause(a.id, c.id)
        world.link_cause(b.id, d.id)
        world.link_cause(c.id, d.id)

        out = world.consequences_of(a.id)
        ids = [eid for eid, _ in out]
        assert ids.count(d.id) == 1
        assert dict(out)[d.id] == 2            # at its shortest distance

    def test_a_loop_is_refused_however_long_the_chain(self, world: World):
        """The guard must walk the whole chain — a depth cap would let a saga-length
        causal chain close a cycle unnoticed."""
        chain = [world.add_event(f"Event {i}") for i in range(70)]
        for a, b in zip(chain, chain[1:], strict=False):
            world.link_cause(a.id, b.id)
        with pytest.raises(WorldError, match="causal loop"):
            world.link_cause(chain[-1].id, chain[0].id)


class TestCascadeAtScale:
    def test_a_hub_entity_with_hundreds_of_facts_deletes_and_restores(
            self, world: World):
        """The id lists behind the cascade queries must be chunked: SQLite caps bound
        variables at 999 on common builds, and a kingdom everyone is located_in
        would otherwise be impossible to delete at exactly the scale that matters."""
        hub = world.add_entity("region", "Everywhere")
        others = [world.add_entity("person", f"P{i}") for i in range(120)]
        for i, other in enumerate(others):
            f = world.assert_fact(other, "located_in", hub, note=f"n{i}")
            # pile several facts per person, and a claim about each placement,
            # so the dying-fact id list comfortably exceeds one chunk
            world.assert_fact(other, "claims", hub)
            world.assert_fact(other, "occupies", hub)
            world.assert_fact(other, "administers", hub)
            world.assert_fact(other, "taxes", hub, about_fact_id=f.id)

        n_facts = len(world.facts_about(hub.id))
        assert n_facts == 600
        world.delete_entity(hub.id)
        assert world.facts_about(hub.id) == []

        world.restore(world.recently_deleted()[0]["revision_id"])
        assert len(world.facts_about(hub.id)) == n_facts

    def test_restore_will_not_replay_a_link_that_now_closes_a_loop(self, world: World):
        """The world can move on while a link sits deleted: if the DAG grew a path the
        other way, replaying the old link must be skipped, not commit a cycle."""
        owner = world.add_entity("person", "Chronicler")
        a = world.add_event("A", entity_id=owner.id)   # dies (and returns) with them
        x = world.add_event("X")
        b = world.add_event("B")
        world.link_cause(x.id, a.id)
        world.link_cause(a.id, b.id)

        world.delete_entity(owner.id)                  # takes A and both links
        world.link_cause(b.id, x.id)                   # legal now: A's chain is gone

        world.restore(world.recently_deleted()[0]["revision_id"])
        assert world.get_entity(owner.id) is not None
        # A and one of its links return; the one that would close B→X→A→B does not
        events = {e.name for e in world.events()}
        assert "A" in events
        links = world.db.query("SELECT cause_id, effect_id FROM causal_link")
        pairs = {(r["cause_id"], r["effect_id"]) for r in links}
        assert (b.id, x.id) in pairs                   # the newer decision stands
        assert len(pairs & {(x.id, a.id), (a.id, b.id)}) == 1
        # and the graph is still a DAG: nothing reaches itself
        for eid in (a.id, b.id, x.id):
            downstream = [i for i, _ in world.consequences_of(eid, max_depth=32)]
            assert eid not in downstream


class TestUndoRedo:
    """§59 taken to its natural end: whole actions taken back and reinstated."""

    def test_undo_uncreates_and_redo_recreates(self, world: World):
        e = world.add_entity("settlement", "Fleeting")
        assert "Fleeting" in world.undo()
        assert world.get_entity(e.id) is None
        assert "Fleeting" in world.redo()
        assert world.get_entity(e.id) is not None

    def test_undo_walks_backwards_through_actions(self, world: World):
        a = world.add_entity("person", "First")
        b = world.add_entity("person", "Second")
        world.undo()                                   # takes back Second
        assert world.get_entity(b.id) is None
        assert world.get_entity(a.id) is not None
        world.undo()                                   # then First
        assert world.get_entity(a.id) is None

    def test_undo_of_an_edit_restores_the_earlier_values(self, world: World):
        e = world.add_entity("settlement", "Oldtown", summary="original")
        world.update_entity(e.id, name="Newtown", summary="rewritten")
        world.undo()
        back = world.get_entity(e.id)
        assert back.name == "Oldtown"
        assert back.summary == "original"
        world.redo()
        assert world.get_entity(e.id).name == "Newtown"

    def test_undo_of_a_delete_brings_the_whole_cascade_back(self, world: World):
        lord = world.add_entity("person", "Lord Brief")
        seat = world.add_entity("settlement", "Seat")
        world.assert_fact(lord, "rules", seat)
        title = world.add_title("Lord of Seat", entity_id=lord.id)
        world.grant_title(title.id, lord.id)

        world.delete_entity(lord.id)
        assert world.titles() == []

        assert "Lord Brief" in world.undo()
        assert world.get_entity(lord.id) is not None
        assert len(world.facts_about(lord.id)) == 1
        assert [t.name for t in world.titles()] == ["Lord of Seat"]

        world.redo()                                   # deleted again, whole
        assert world.get_entity(lord.id) is None
        assert world.titles() == []

    def test_undo_of_a_transfer_inverts_both_halves(self, world: World):
        marr = world.add_entity("house", "House Marr")
        orren = world.add_entity("house", "House Orren")
        town = world.add_entity("settlement", "Greyhaven")
        world.assert_fact(marr, "legally_owns", town, valid_from=world.day(312))
        world.transfer("legally_owns", town, orren, world.day(428))

        world.undo()
        owners = world.facts_where("legally_owns", object_id=town.id,
                                   at=world.day(500))
        assert [f.subject_id for f in owners] == [marr.id]   # open again, alone

        world.redo()
        owners = world.facts_where("legally_owns", object_id=town.id,
                                   at=world.day(500))
        assert [f.subject_id for f in owners] == [orren.id]

    def test_a_new_action_forfeits_the_redo(self, world: World):
        world.add_entity("settlement", "A")
        world.undo()
        world.add_entity("settlement", "B")            # a real new action
        with pytest.raises(WorldError, match="nothing to redo"):
            world.redo()

    def test_nothing_to_undo_is_an_answer(self, world: World):
        with pytest.raises(WorldError, match="nothing to undo"):
            world.undo()
        with pytest.raises(WorldError, match="nothing to redo"):
            world.redo()

    def test_undo_state_reports_what_the_buttons_would_do(self, world: World):
        state = world.undo_state()
        assert state == {"can_undo": False, "undo": None,
                         "can_redo": False, "redo": None}
        world.add_entity("settlement", "Somewhere")
        state = world.undo_state()
        assert state["can_undo"] is True
        assert "Somewhere" in state["undo"]
        world.undo()
        state = world.undo_state()
        assert state["can_redo"] is True
        assert "Somewhere" in state["redo"]

    def test_undo_history_survives_a_reopen(self, tmp_path):
        """The undone-set is reconstructed from the log's markers, so closing the
        file mid-history does not resurrect what was taken back."""
        path = tmp_path / "undoable.fwworld"
        w = World.create(path, name="Persist")
        w.add_entity("settlement", "Kept")
        doomed = w.add_entity("settlement", "Taken back")
        w.undo()
        w.close()

        reopened = World.open(path)
        try:
            assert reopened.get_entity(doomed.id) is None
            # undo does not re-target the already-undone action…
            assert "Kept" in reopened.undo_state()["undo"]
            # …and the redo stack carried across, too
            state = reopened.undo_state()
            assert state["can_redo"] is True
            assert "Taken back" in state["redo"]
            reopened.redo()
            assert reopened.get_entity(doomed.id) is not None
        finally:
            reopened.close()

    def test_undoing_a_restore_is_just_another_undo(self, world: World):
        e = world.add_entity("settlement", "Twice-lost")
        world.delete_entity(e.id)
        world.restore(world.recently_deleted()[0]["revision_id"])
        assert world.get_entity(e.id) is not None

        assert "Twice-lost" in world.undo()            # takes back the restore
        assert world.get_entity(e.id) is None
        world.redo()
        assert world.get_entity(e.id) is not None

    def test_pre_migration_history_sits_beyond_undo(self, world: World):
        """Rows logged before the action_id column exists carry '' — they must never
        be mis-grouped into one giant action."""
        e = world.add_entity("settlement", "Old times")
        world.db.execute("UPDATE revision SET action_id = ''")
        with pytest.raises(WorldError, match="nothing to undo"):
            world.undo()
        assert world.get_entity(e.id) is not None


class TestSchemaMigration:
    def test_a_version_1_file_is_upgraded_in_place(self, tmp_path):
        """The first real migration: files from before action_id must open, gain the
        column, and work — history intact."""
        import sqlite3 as sql

        from fw.core.world import World as W
        path = tmp_path / "old.fwworld"
        w = W.create(path, name="Elder")
        w.add_entity("settlement", "Ancient")
        w.close()

        # forge a version-1 file: strip everything the later migrations add, then
        # rewind user_version — the reopen below must walk v1 → v2 → v3 cleanly
        conn = sql.connect(path)
        conn.execute("DROP INDEX ix_revision_action")
        conn.execute("ALTER TABLE revision DROP COLUMN action_id")
        conn.execute("DROP INDEX ix_fact_supersedes")
        conn.execute("ALTER TABLE fact DROP COLUMN supersedes_id")
        conn.execute("DROP TABLE entity_override")
        conn.execute("DROP INDEX ix_holding_branch")
        conn.execute("ALTER TABLE title_holding DROP COLUMN branch_id")
        conn.executescript("""
            CREATE TABLE causal_link_v1 (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, cause_id TEXT NOT NULL,
                effect_id TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'caused',
                confidence TEXT NOT NULL DEFAULT 'canon',
                note TEXT NOT NULL DEFAULT '',
                UNIQUE (cause_id, effect_id)) STRICT;
            INSERT INTO causal_link_v1
                SELECT id, project_id, cause_id, effect_id, kind, confidence, note
                FROM causal_link;
            DROP TABLE causal_link;
            ALTER TABLE causal_link_v1 RENAME TO causal_link;
        """)
        conn.execute("PRAGMA user_version = 1")
        conn.close()

        upgraded = W.open(path)
        try:
            assert upgraded.entity_named("Ancient") is not None
            columns = {r["name"] for r in upgraded.db.query(
                "PRAGMA table_info(revision)")}
            assert "action_id" in columns
            assert "supersedes_id" in {r["name"] for r in upgraded.db.query(
                "PRAGMA table_info(fact)")}
            assert upgraded.db.one("SELECT 1 FROM sqlite_master "
                                   "WHERE name = 'entity_override'") is not None
            # old rows carry '' (beyond undo); new actions are undoable again
            fresh = upgraded.add_entity("settlement", "Modern")
            assert "Modern" in upgraded.undo()
            assert upgraded.get_entity(fresh.id) is None
        finally:
            upgraded.close()


class TestUndoRedoHardening:
    """Regressions from the adversarial review of the undo slice."""

    def test_undo_of_a_delete_keeps_the_grants_and_participations(self, world: World):
        """Cascade children must replay through the dependency-ordered machinery:
        a flat newest-first replay put holdings before their titles and lost them."""
        lord = world.add_entity("person", "Held")
        title = world.add_title("Holder of Things", entity_id=lord.id)
        world.grant_title(title.id, lord.id)
        event = world.add_event("A day", participants=[(lord.id, "participant")])

        world.delete_entity(lord.id)
        world.undo()
        back_title = world.title_named("Holder of Things")
        assert back_title is not None
        assert [h.holder_id for h in world.title_holdings(back_title.id)] == [lord.id]
        assert [p.id for p, _ in world.event_participants(event.id)] == [lord.id]

    def test_a_forfeited_redo_stays_forfeited_after_reopen(self, tmp_path):
        """In-session, a new action clears the redo stack; the reconstruction must
        reach the same answer, or a stale redo would clobber the newer edit."""
        path = tmp_path / "forfeit.fwworld"
        w = World.create(path, name="Forfeit")
        e = w.add_entity("settlement", "Town")
        w.update_entity(e.id, name="Y")
        w.undo()                                      # back to Town
        w.update_entity(e.id, name="Z")               # forfeits the redo of Y
        with pytest.raises(WorldError, match="nothing to redo"):
            w.redo()
        w.close()

        reopened = World.open(path)
        try:
            assert reopened.undo_state()["can_redo"] is False
            with pytest.raises(WorldError, match="nothing to redo"):
                reopened.redo()
            assert reopened.get_entity(e.id).name == "Z"
        finally:
            reopened.close()

    def test_two_handles_never_share_a_session_token(self, tmp_path):
        """The token must be random, not a timestamp prefix: two handles opened in
        the same millisecond would otherwise weave their actions together."""
        path = tmp_path / "tokens.fwworld"
        World.create(path, name="T").close()
        handles = [World.open(path) for _ in range(8)]
        try:
            tokens = {h._session_token for h in handles}
            assert len(tokens) == len(handles)
        finally:
            for h in handles:
                h.close()

    def test_uninsert_logs_what_its_cascade_takes(self, world: World):
        """A child row that undo cannot reach (here: a grant whose history predates
        the action_id column) still dies when its parent is uninserted — that loss
        must be snapshotted into the log, and redo must bring both back."""
        holder = world.add_entity("person", "Holder")
        title = world.add_title("The Seat")
        world.grant_title(title.id, holder.id)
        # age the grant out of undo's reach, as pre-migration history is
        world.db.execute(
            "UPDATE revision SET action_id = '' WHERE table_name = 'title_holding'")

        world.undo()          # targets the title creation; the grant still exists
        assert world.title_named("The Seat") is None
        assert world.db.one(
            "SELECT 1 FROM revision WHERE table_name = 'title_holding' "
            "AND action = 'delete' AND note LIKE 'cascade:%'") is not None

        world.redo()          # title returns — and the grant its cascade took
        again = world.title_named("The Seat")
        assert again is not None
        assert [h.holder_id for h in world.title_holdings(again.id)] == [holder.id]

    def test_heavy_undo_traffic_does_not_starve_the_walk(self, world: World):
        """Inversion records must never push real actions out of reach."""
        e = world.add_entity("settlement", "Yo-yo")
        for _ in range(120):
            world.undo()
            world.redo()
        assert world.undo_state()["can_undo"] is True
        world.undo()
        assert world.get_entity(e.id) is None

    def test_events_scenes_and_links_are_undoable_too(self, world: World):
        """Ctrl+Z must target what the writer just did — and the UI creates events,
        scenes and causal links, not only entities and facts."""
        who = world.add_entity("person", "Witness")
        event = world.add_event("The fire", participants=[(who.id, "witness")])
        assert "The fire" in world.undo_state()["undo"]
        world.undo()
        assert world.events() == []
        world.redo()
        assert [e.name for e in world.events()] == ["The fire"]
        assert [p.id for p, _ in world.event_participants(event.id)] == [who.id]

        scene = world.add_scene("A parley", participants=[who.id])
        world.undo()
        assert world.get_scene(scene.id) is None
        world.redo()
        assert world.get_scene(scene.id) is not None
        assert [p.id for p in world.scene_participants(scene.id)] == [who.id]

        second = world.add_event("The flood")
        world.link_cause(second.id, event.id)
        assert "causal link" in world.undo_state()["undo"]
        world.undo()
        assert world.consequences_of(second.id) == []
        world.redo()
        assert world.consequences_of(second.id) == [(event.id, 1)]

    def test_child_tables_match_the_schema(self, world: World):
        """_CHILD_TABLES mirrors the schema's ON DELETE CASCADE edges by hand; if the
        schema grows an edge this map misses, undo would silently lose rows. Fail
        loudly here instead."""
        from fw.core.world import _CHILD_TABLES
        tables = [r["name"] for r in world.db.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'")]
        for parent, declared in _CHILD_TABLES.items():
            actual = set()
            for table in tables:
                for fk in world.db.query(f"PRAGMA foreign_key_list({table})"):
                    if fk["table"] == parent and fk["on_delete"] == "CASCADE":
                        actual.add((table, fk["from"]))
            assert set(declared) == actual, (
                f"_CHILD_TABLES[{parent!r}] disagrees with the schema")

    def test_a_big_cascade_is_still_described_by_its_entity(self, world: World):
        """The toast must name what Ctrl+Z will take back even when the action holds
        more records than any display window."""
        hub = world.add_entity("region", "Manyfold")
        for i in range(60):
            other = world.add_entity("person", f"P{i}")
            world.assert_fact(other, "located_in", hub)
        world.delete_entity(hub.id)                     # one action, 60+ records
        assert "Manyfold" in world.undo_state()["undo"]

    def test_a_second_handle_forfeits_this_ones_redo(self, tmp_path):
        """Two handles on one file: an edit through either forfeits a pending redo,
        because the log — not any handle's memory — is the truth."""
        path = tmp_path / "shared.fwworld"
        a = World.create(path, name="Shared")
        e = a.add_entity("settlement", "Town")
        a.update_entity(e.id, name="Y")
        a.undo()                                       # back to Town; redo holds Y
        b = World.open(path)
        try:
            b.update_entity(e.id, name="Z")            # the other handle moves on
            with pytest.raises(WorldError, match="nothing to redo"):
                a.redo()                               # must not clobber Z with Y
            assert a.get_entity(e.id).name == "Z"
            # and undo through A takes back B's edit — the newest action — not
            # something older from A's own history
            assert "edit" in a.undo()
            assert a.get_entity(e.id).name == "Town"
        finally:
            b.close()
            a.close()


class TestBranches:
    """§105 alternate timelines: overlays, never copies; canon never written from a
    branch."""

    def test_a_branch_inherits_the_world_and_keeps_its_own_additions(
            self, world: World):
        mara = world.add_entity("person", "Mara")
        world.create_branch("what if")
        fork = world.on_branch("what if")

        assert fork.get_entity(mara.id) is not None       # inherited
        ghost = fork.add_entity("person", "Only Here")
        assert fork.get_entity(ghost.id) is not None
        assert world.get_entity(ghost.id) is None          # invisible to canon
        assert world.count_entities("person") == 1
        assert fork.count_entities("person") == 2

    def test_ending_an_inherited_fact_is_branch_local(self, world: World):
        marr = world.add_entity("house", "House Marr")
        town = world.add_entity("settlement", "Greyhaven")
        fact = world.assert_fact(marr, "legally_owns", town,
                                 valid_from=world.day(300))
        world.create_branch("the fall")
        fork = world.on_branch("the fall")

        fork.end_fact(fact.id, fork.day(320))
        # the branch sees ownership closed…
        assert fork.facts_where("legally_owns", at=fork.day(330)) == []
        assert len(fork.facts_where("legally_owns", at=fork.day(310))) == 1
        # …canon never felt it
        canon = world.facts_where("legally_owns", at=world.day(330))
        assert [f.id for f in canon] == [fact.id]
        assert world.get_fact(fact.id).valid_to is None

        # a second branch edit updates the same override, not a second copy
        fork.end_fact(fact.id, fork.day(325))
        overrides = fork.db.query(
            "SELECT * FROM fact WHERE supersedes_id = ?", (fact.id,))
        assert len(overrides) == 1

    def test_deleting_an_inherited_fact_is_a_tombstone(self, world: World):
        a = world.add_entity("person", "A")
        b = world.add_entity("person", "B")
        fact = world.assert_fact(a, "trusts", b)
        world.create_branch("estranged")
        fork = world.on_branch("estranged")

        fork.delete_fact(fact.id)
        assert fork.facts_where("trusts") == []
        assert fork.facts_about(a.id) == []
        assert len(world.facts_where("trusts")) == 1       # canon keeps it

    def test_inherited_entities_are_patched_not_copied(self, world: World):
        town = world.add_entity("settlement", "Greyhaven", summary="A port.")
        world.create_branch("renamed")
        fork = world.on_branch("renamed")

        fork.update_entity(town.id, name="Blackhaven")
        assert fork.get_entity(town.id).name == "Blackhaven"
        assert fork.get_entity(town.id).summary == "A port."
        assert world.get_entity(town.id).name == "Greyhaven"

        # a branch of the branch merges field patches, nearest winning per field
        fork.create_branch("deeper")
        deeper = fork.on_branch("deeper")
        deeper.update_entity(town.id, summary="A ruin.")
        e = deeper.get_entity(town.id)
        assert e.name == "Blackhaven"                      # parent's rename holds
        assert e.summary == "A ruin."                      # own patch on top

    def test_a_branch_death_changes_state_and_succession(self, renn: World):
        """The flagship §105 question: what if the heir were already dead?"""
        oren = renn.entity_named("Prince Oren")
        crown = renn.title_named("King of Renn")
        day = renn.day(241, 1, 1)
        canon_line = [c.name for c in __import__(
            "fw.core.succession.engine", fromlist=["SuccessionEngine"]
        ).SuccessionEngine(renn).compute(crown.id, day).line]
        assert canon_line[0] == "Prince Oren"

        renn.create_branch("orenless")
        fork = renn.on_branch("orenless")
        fork.update_entity(oren.id, exists_to=fork.day(240, 1, 1))

        assert oren.id in renn.state_at(day).entities       # canon: alive
        assert oren.id not in fork.state_at(day).entities   # branch: gone

        from fw.core.succession.engine import SuccessionEngine
        branch_line = [c.name for c in
                       SuccessionEngine(fork).compute(crown.id, day).line]
        assert branch_line[0] != "Prince Oren"
        assert canon_line[0] == "Prince Oren"               # canon unchanged

    def test_inherited_entities_cannot_be_deleted_from_a_branch(self, world: World):
        keep = world.add_entity("person", "Kept")
        world.create_branch("careful")
        fork = world.on_branch("careful")
        with pytest.raises(WorldError, match="end its existence"):
            fork.delete_entity(keep.id)
        # but the branch's own creations are its to delete
        own = fork.add_entity("person", "Fleeting")
        fork.delete_entity(own.id)
        assert fork.get_entity(own.id) is None

    def test_branch_causal_links_stay_in_the_branch(self, world: World):
        a = world.add_event("Flood")
        b = world.add_event("Famine")
        world.create_branch("worse")
        fork = world.on_branch("worse")
        fork.link_cause(a.id, b.id)
        assert fork.consequences_of(a.id) == [(b.id, 1)]
        assert world.consequences_of(a.id) == []

    def test_graph_walks_respect_overrides(self, world: World):
        region = world.add_entity("region", "March")
        town = world.add_entity("settlement", "Town")
        village = world.add_entity("settlement", "Village")
        world.assert_fact(town, "located_in", region)
        fact = world.assert_fact(village, "located_in", town)
        world.create_branch("moved")
        fork = world.on_branch("moved")
        fork.delete_fact(fact.id)                           # village unmoored here

        assert dict(world.follow(region.id, "located_in", direction="in")) == {
            town.id: 1, village.id: 2}
        assert dict(fork.follow(region.id, "located_in", direction="in")) == {
            town.id: 1}
        assert village.id not in fork.neighbours(region.id, ["located_in"], hops=3)

    def test_search_is_branch_aware(self, world: World):
        world.add_entity("settlement", "Common Town")
        world.create_branch("alt")
        fork = world.on_branch("alt")
        fork.add_entity("settlement", "Fork Town")

        assert {e.name for e in fork.search("Town")} == {"Common Town", "Fork Town"}
        assert {e.name for e in world.search("Town")} == {"Common Town"}

    def test_branch_overrides_are_undoable(self, world: World):
        a = world.add_entity("person", "A")
        b = world.add_entity("person", "B")
        fact = world.assert_fact(a, "trusts", b)
        world.create_branch("doubt")
        fork = world.on_branch("doubt")

        fork.end_fact(fact.id, fork.day(300))
        assert fork.facts_where("trusts", at=fork.day(400)) == []
        fork.undo()
        assert len(fork.facts_where("trusts", at=fork.day(400))) == 1
        fork.redo()
        assert fork.facts_where("trusts", at=fork.day(400)) == []

    def test_duplicate_branch_names_are_refused(self, world: World):
        world.create_branch("twice")
        with pytest.raises(WorldError, match="already exists"):
            world.create_branch("twice")

    def test_a_branch_title_grant_never_crowns_anyone_on_canon(self, world: World):
        king = world.add_entity("person", "King")
        usurper = world.add_entity("person", "Usurper")
        crown = world.add_title("The Crown")
        world.grant_title(crown.id, king.id, from_day=world.day(200))

        world.create_branch("coup")
        fork = world.on_branch("coup")
        fork.grant_title(crown.id, usurper.id, from_day=fork.day(220))

        day = world.day(230)
        assert world.title_holder_on(crown.id, day) == king.id      # canon safe
        assert fork.title_holder_on(crown.id, day) == usurper.id    # coup real here
        assert world.state_at(day).titles[crown.id] == king.id
        assert [t.name for t in world.titles_held_by(usurper.id)] == []
        assert [t.name for t in fork.titles_held_by(usurper.id)] == ["The Crown"]

    def test_undo_is_timeline_scoped(self, world: World):
        """Ctrl+Z on canon must never target a branch's action, and vice versa."""
        world.add_entity("person", "Canon One")
        world.create_branch("aside")
        fork = world.on_branch("aside")
        ghost = fork.add_entity("person", "Branch Ghost")

        # canon's undo takes back canon's newest action, not the branch's
        assert "Canon One" in world.undo_state()["undo"]
        world.undo()
        assert world.entity_named("Canon One") is None
        assert fork.get_entity(ghost.id) is not None    # untouched

        # the branch's undo takes back its own
        assert "Branch Ghost" in fork.undo_state()["undo"]
        fork.undo()
        assert fork.get_entity(ghost.id) is None

    def test_competing_overrides_resolve_to_the_nearest_branch(self, world: World):
        """When an ancestor and a descendant both supersede one fact, the descendant
        must see exactly one row — the nearest — never both."""
        a = world.add_entity("person", "A")
        b = world.add_entity("person", "B")
        fact = world.assert_fact(a, "trusts", b)
        world.create_branch("b1")
        b1 = world.on_branch("b1")
        b1.create_branch("b2")
        b2 = b1.on_branch("b2")

        b2.end_fact(fact.id, b2.day(200))      # child overrides first
        b1.end_fact(fact.id, b1.day(100))      # then the parent, independently

        seen = b2.facts_where("trusts")
        assert len(seen) == 1
        assert seen[0].valid_to == b2.day(200)             # b2's own, not b1's
        assert b2.get_fact(fact.id).valid_to == b2.day(200)
        assert [f.valid_to for f in b1.facts_where("trusts")] == [b1.day(100)]

    def test_a_branch_tombstone_takes_the_facts_about_it(self, world: World):
        a = world.add_entity("person", "A")
        b = world.add_entity("person", "B")
        fact = world.assert_fact(a, "trusts", b)
        meta = world.assert_fact(b, "trusts", a, about_fact_id=fact.id)
        world.create_branch("severed")
        fork = world.on_branch("severed")

        fork.delete_fact(fact.id)
        assert fork.facts_where("trusts") == []             # meta went with it
        assert fork.get_fact(meta.id) is None
        assert len(world.facts_where("trusts")) == 2        # canon keeps both

    def test_by_id_getters_are_timeline_scoped(self, world: World):
        world.create_branch("aside")
        fork = world.on_branch("aside")
        scene = fork.add_scene("Only there")
        title = fork.add_title("Only theirs")
        event = fork.add_event("Only then")

        assert world.get_scene(scene.id) is None
        assert world.get_title(title.id) is None
        assert world.get_event(event.id) is None
        assert fork.get_scene(scene.id) is not None
        assert fork.get_title(title.id) is not None
        assert fork.get_event(event.id) is not None

    def test_the_toast_names_a_timeline_change_for_what_it_is(self, world: World):
        a = world.add_entity("person", "A")
        b = world.add_entity("person", "B")
        fact = world.assert_fact(a, "trusts", b)
        world.create_branch("doubt")
        fork = world.on_branch("doubt")
        fork.end_fact(fact.id, fork.day(300))
        assert "timeline change" in fork.undo_state()["undo"]
