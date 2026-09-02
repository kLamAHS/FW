"""The same event, told three ways — and the same man, called two things (§33, §94).

`interpretation` has been in the schema since the first migration, with a documented
rationale and three rows in the seeded world, and **nothing ever read it**. The only
writer was the seed, through a raw `db.insert`, so even the demo world's accounts of the
Red Ford were outside the revision log and could not be undone. A writer building their
own world could not record that House Marr calls the battle a massacre at all.

§94 is why this comes first: “show me the world as House Marr sees it” has to answer with
their version of history *and* their name for the Pretender, and those are one shape — a
party, a thing, and what that party says about it. One table carries both, so a
perspective reads from one place.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fw.api.app import create_app
from fw.core.seed.renn import seed_renn
from fw.core.store.schema import APPLICATION_ID, SCHEMA, SCHEMA_VERSION
from fw.core.world import World, WorldError


@pytest.fixture
def world():
    made = seed_renn()
    yield made
    made.close()


@pytest.fixture
def client(world):
    return TestClient(create_app(world))


def red_ford(world) -> str:
    return next(e.id for e in world.events() if "Red Ford" in e.name)


def oren(world) -> str:
    return next(e.id for e in world.entities("person") if "Oren" in e.name)


def house(world, part: str) -> str:
    return next(e.id for e in world.entities() if part in e.name)


class TestABattleCanBeToldMoreThanOneWay:
    def test_the_seeded_accounts_can_finally_be_read(self, client, world):
        """Three rows that existed and no code could reach."""
        told = client.get("/api/interpretations",
                          params={"event_id": red_ford(world)}).json()
        assert {row["label"] for row in told} == {
            "The Crown's account", "The northern account", "The clerical account"}
        northern = next(r for r in told if r["label"] == "The northern account")
        assert northern["holder_name"] == "House Marr"
        assert "massacre" in northern["account"]

    def test_an_account_nobody_owns_is_allowed(self, client, world):
        """“The clerical account” belongs to no entity in this world.

        Requiring a holder would make the writer invent a church before they could record
        what everybody says.
        """
        told = client.get("/api/interpretations",
                          params={"event_id": red_ford(world)}).json()
        clerical = next(r for r in told if r["label"] == "The clerical account")
        assert clerical["holder_id"] is None and clerical["holder_name"] == ""

    def test_a_writer_can_add_one(self, client, world):
        made = client.post("/api/interpretations", json={
            "label": "The Veyne account", "event_id": red_ford(world),
            "holder_id": house(world, "Veyne"),
            "account": "A necessary correction, regrettably bloody."})
        assert made.status_code == 201
        assert "The Veyne account" in {
            r["label"] for r in client.get("/api/interpretations").json()}

    def test_an_account_can_be_taken_back(self, client, world):
        made = client.post("/api/interpretations", json={
            "label": "A slip", "event_id": red_ford(world)}).json()
        assert client.delete(f"/api/interpretations/{made['id']}").status_code == 204
        assert not any(r.label == "A slip" for r in world.interpretations())


class TestTheSameManCalledTwoThings:
    def test_a_house_can_have_its_own_name_for_somebody(self, client, world):
        """§94's political labels. What a house calls a man is where it stands."""
        named = client.get("/api/interpretations",
                           params={"entity_id": oren(world)}).json()
        by_holder = {r["holder_name"]: r["label"] for r in named}
        assert by_holder["House Renn"] == "His Highness the Prince"
        assert by_holder["House Marr"] == "The Pretender"

    def test_one_party_s_whole_view_can_be_asked_for(self, client, world):
        """What a perspective needs: everything House Marr says, in one read."""
        theirs = client.get("/api/interpretations",
                            params={"holder_id": house(world, "Marr")}).json()
        assert {r["label"] for r in theirs} == {"The northern account", "The Pretender"}


class TestItRefusesAnAccountOfNothing:
    def test_an_account_of_both_an_event_and_a_person_is_refused(self, client, world):
        answer = client.post("/api/interpretations", json={
            "label": "X", "event_id": red_ford(world), "entity_id": oren(world)})
        assert answer.status_code == 422
        assert "not both" in answer.json()["detail"]

    def test_an_account_of_neither_is_refused(self, client):
        answer = client.post("/api/interpretations", json={"label": "X"})
        assert answer.status_code == 422

    def test_the_database_itself_refuses_it_too(self, world):
        """The CHECK constraint, not only the route — a second writer cannot slip past."""
        with pytest.raises((sqlite3.IntegrityError, WorldError)):
            world.db.execute(
                "INSERT INTO interpretation (id, event_id, entity_id, holder_id, label, "
                "account) VALUES ('x', NULL, NULL, NULL, 'Nothing', '')")

    def test_an_account_with_no_label_is_refused(self, client, world):
        assert client.post("/api/interpretations", json={
            "label": "  ", "event_id": red_ford(world)}).status_code == 422

    def test_an_account_of_an_event_that_does_not_exist_is_refused(self, client):
        assert client.post("/api/interpretations", json={
            "label": "X", "event_id": "nope"}).status_code == 404

    def test_an_account_held_by_nobody_real_is_refused(self, client, world):
        assert client.post("/api/interpretations", json={
            "label": "X", "event_id": red_ford(world),
            "holder_id": "nope"}).status_code == 404


class TestItUndoesLikeEverythingElse:
    def test_recording_an_account_undoes(self, client, world):
        client.post("/api/interpretations", json={
            "label": "Undo me", "event_id": red_ford(world)})
        assert any(r.label == "Undo me" for r in world.interpretations())
        world.undo()
        assert not any(r.label == "Undo me" for r in world.interpretations())

    def test_the_seeded_accounts_are_in_the_log_at_all(self, world):
        """They were written with a raw insert, so they were not.

        Which meant the demo world shipped with three rows no `Ctrl+Z` could reach and no
        restore could recover — a hole nobody found because nothing could make a fourth.
        """
        logged = world.db.query(
            "SELECT row_id FROM revision WHERE table_name = 'interpretation'")
        assert len(list(logged)) >= 5

    def test_deleting_a_holder_takes_their_accounts_and_gives_them_back(self, world):
        """A house's opinions die with the house, and come back with it."""
        marr = house(world, "Marr")
        assert world.interpretations(holder_id=marr)
        world.delete_entity(marr)
        assert not world.interpretations(holder_id=marr)
        world.undo()
        assert len(world.interpretations(holder_id=marr)) == 2


class TestAnOpinionBelongsToItsTimeline:
    """§105. The table had no branch of its own — it was scoped through the event.

    Nothing noticed, because nothing read it. It stops working the moment a row can be
    about an *entity*: entities are inherited down the branch chain, so a name invented
    on a what-if would attach to the canon row and leak into canon with nothing to
    filter on. Caught here rather than the first time somebody looked at a what-if.
    """

    def test_a_name_invented_on_a_what_if_stays_there(self, world):
        subject = oren(world)
        world.create_branch("what-if")
        alt = world.on_branch("what-if")
        alt.add_interpretation("The Usurper", entity_id=subject)

        assert "The Usurper" in {i.label for i in alt.interpretations(entity_id=subject)}
        assert "The Usurper" not in {
            i.label for i in world.interpretations(entity_id=subject)}

    def test_a_what_if_still_inherits_what_canon_says(self, world):
        """A branch is an overlay, not a fresh start."""
        subject = oren(world)
        world.create_branch("what-if")
        alt = world.on_branch("what-if")
        assert {i.label for i in alt.interpretations(entity_id=subject)} == {
            "His Highness the Prince", "The Pretender"}

    def test_an_account_from_another_timeline_cannot_be_deleted_from_this_one(self,
                                                                             world):
        world.create_branch("what-if")
        alt = world.on_branch("what-if")
        theirs = alt.add_interpretation("The Usurper", entity_id=oren(world))
        with pytest.raises(WorldError, match="another timeline"):
            world.delete_interpretation(theirs.id)


class TestAFactCanBeAboutAnotherFact:
    """§33's reification. `assert_fact` has taken `about_fact_id` since the first
    migration and it was on neither wire shape, so a recorded disagreement was invisible.
    """

    def test_a_disagreement_can_be_recorded_and_read_back(self, client, world):
        marr = house(world, "Marr")
        claim = client.post("/api/facts", json={
            "subject_id": marr, "predicate_key": "motto",
            "value": "Iron endures"}).json()
        about = client.post("/api/facts", json={
            "subject_id": marr, "predicate_key": "note",
            "value": "The northern lords dispute this wording",
            "about_fact_id": claim["id"]}).json()
        assert about["about_fact_id"] == claim["id"]
        lines = client.get(f"/api/entities/{marr}").json()["facts"]
        assert any(f["about_fact_id"] == claim["id"] for f in lines)

    def test_a_fact_about_a_fact_that_does_not_exist_is_refused(self, client, world):
        answer = client.post("/api/facts", json={
            "subject_id": house(world, "Marr"), "predicate_key": "note",
            "value": "About nothing", "about_fact_id": "nope"})
        assert answer.status_code == 404


class TestTheMigrationCarriesAnOlderWorldAcross:
    def test_a_file_written_before_this_change_still_opens(self, tmp_path: Path):
        """Migration 9 rebuilds the table, so an existing world must survive it.

        A migration tested only against a fresh database is not tested at all: the fresh
        path runs `SCHEMA` and the upgrade path is the one every real world takes. The
        file here is the current schema with exactly one table wound back to its version-8
        shape — `event_id NOT NULL`, no `entity_id`, and no branch scoping — which is what
        a world written before this change actually looks like.
        """
        path = tmp_path / "old.fwworld"
        _write_a_version_8_world(path)

        world = World.open(path)
        try:
            kept = world.interpretations()
            assert [r.label for r in kept] == ["The old account"]
            assert kept[0].event_id == "evt" and kept[0].entity_id is None
            assert world.db.one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION

            # The backfill took the branch from the event the row was about, so the row
            # is on canon rather than nowhere — without it `interpretations()` would
            # return nothing at all and the account would look deleted.
            assert kept[0].id == "i1"

            # And the rebuilt table takes the new shape.
            made = world.add_interpretation("A new label", entity_id="ent")
            assert made.entity_id == "ent"
            assert {r.label for r in world.interpretations()} == {
                "The old account", "A new label"}
        finally:
            world.close()


def _write_a_version_8_world(path: Path) -> None:
    """The current schema with `interpretation` wound back to how version 8 wrote it."""
    old_table = """
CREATE TABLE interpretation (
    id           TEXT PRIMARY KEY,
    event_id     TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    holder_id    TEXT REFERENCES entity(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    account      TEXT NOT NULL DEFAULT ''
) STRICT;
"""
    start = SCHEMA.index("CREATE TABLE interpretation (")
    end = SCHEMA.index("CREATE TABLE secret (")
    was = SCHEMA[:start] + old_table.strip() + "\n\n" + SCHEMA[end:]
    # The three indexes added with the rebuild did not exist at version 8 either.
    was = "\n".join(line for line in was.splitlines()
                    if "ix_interpretation_" not in line)

    conn = sqlite3.connect(path)
    conn.executescript(was)
    conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    conn.execute("PRAGMA user_version = 8")
    conn.executescript("""
        INSERT INTO project VALUES
            ('p', 'Old world', '', NULL, '2026-01-01', '2026-01-01');
        INSERT INTO branch VALUES ('b', 'p', 'canon', NULL, NULL, 1, '2026-01-01');
        INSERT INTO entity VALUES ('ent', 'p', 'person', 'Somebody', '', NULL, NULL,
            NULL, NULL, 'b', 'canon', '[]', '2026-01-01', '2026-01-01');
        INSERT INTO event (id, project_id, branch_id, name, created_at, updated_at)
            VALUES ('evt', 'p', 'b', 'A battle', '2026-01-01', '2026-01-01');
        INSERT INTO interpretation VALUES
            ('i1', 'evt', NULL, 'The old account', 'As it was.');
    """)
    conn.commit()
    conn.close()
