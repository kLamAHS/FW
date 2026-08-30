"""The last of the `World` methods a writer could not reach (§20, §21, §58, §60, §80).

Six write methods were written, schema-backed and used by the seed, and had no route
and no form: `add_source`, `add_snapshot`, `add_route_segment`, `add_entity_type`,
`add_predicate` — plus `delete_snapshot` and `delete_route_segment`, which did not
exist at all. Each one had a *reader* already shipped, which is the worse half of the
problem: the fact line rendered a citation nothing could create, the timeline drew
snapshot chips only the seeded world could have, and the README said, accurately, that
custom types worked "through the API or the CLI rather than a screen".

What is asserted is the round trip that matters — make it through the API, and see the
part of the application that was already written pick it up.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fw.api.app import create_app
from fw.core.geo.routing import SAILED
from fw.core.seed.renn import seed_renn


@pytest.fixture
def world():
    made = seed_renn()
    yield made
    made.close()


@pytest.fixture
def client(world):
    return TestClient(create_app(world))


def place(world, part: str) -> str:
    return next(e.id for e in world.entities("settlement") if part in e.name)


class TestAFactCanSayWhereItCameFrom:
    def test_a_source_reaches_the_fact_line_that_renders_it(self, client, world):
        """The reader shipped last commit; this is the half that was missing."""
        source = client.post("/api/sources", json={
            "label": "Chapter 3 draft", "kind": "chapter"}).json()
        marr = world.entity_named("House Marr").id
        client.post("/api/facts", json={
            "subject_id": marr, "predicate_key": "motto",
            "value": "Iron endures, twice", "source_id": source["id"]})
        lines = client.get(f"/api/entities/{marr}").json()["facts"]
        cited = next(f for f in lines if f["value"] == "Iron endures, twice")
        assert cited["source"] == "Chapter 3 draft"

    def test_the_kinds_are_the_ones_the_brief_names(self, client):
        for kind in ("author_note", "chapter", "manuscript_scene",
                     "historical_document", "timeline_event"):
            assert client.post("/api/sources", json={
                "label": f"a {kind}", "kind": kind}).status_code == 201

    def test_a_kind_nobody_uses_is_refused_with_the_ones_that_exist(self, client):
        answer = client.post("/api/sources", json={"label": "X", "kind": "a dream"})
        assert answer.status_code == 422
        assert "chapter" in answer.json()["detail"]

    def test_citing_a_source_that_does_not_exist_is_refused(self, client, world):
        """`fact.source_id` is ON DELETE SET NULL, not a hard constraint — a bad id
        would be stored and then render as no citation at all, which reads to the
        writer as a citation they made and the software lost."""
        answer = client.post("/api/facts", json={
            "subject_id": world.entity_named("House Marr").id,
            "predicate_key": "motto", "value": "Nothing", "source_id": "nope"})
        assert answer.status_code == 404

    def test_a_source_with_no_label_is_refused(self, client):
        assert client.post("/api/sources", json={"label": "  "}).status_code == 422

    def test_recording_a_source_undoes_like_everything_else(self, client, world):
        client.post("/api/sources", json={"label": "A note"})
        assert any(s["label"] == "A note" for s in world.sources())
        world.undo()
        assert not any(s["label"] == "A note" for s in world.sources())


class TestAMomentCanBeNamed:
    def test_a_named_date_appears_on_the_timeline(self, client):
        made = client.post("/api/snapshots", json={
            "name": "Before the Red War", "day": 81000}).json()
        assert made["date"]["text"]
        listed = client.get("/api/snapshots").json()
        assert "Before the Red War" in {s["name"] for s in listed}

    def test_a_name_can_be_taken_back_off(self, client, world):
        made = client.post("/api/snapshots", json={
            "name": "A slip", "day": 81000}).json()
        assert client.delete(f"/api/snapshots/{made['id']}").status_code == 204
        assert not any(s["name"] == "A slip" for s in world.snapshots())

    def test_forgetting_a_moment_leaves_the_world_that_day_alone(self, client, world):
        """The name goes; the day and everything true on it stay."""
        before = len(world.entities())
        made = client.post("/api/snapshots", json={"name": "X", "day": 81000}).json()
        client.delete(f"/api/snapshots/{made['id']}")
        assert len(world.entities()) == before

    def test_a_moment_with_no_name_is_refused(self, client):
        assert client.post("/api/snapshots",
                           json={"name": " ", "day": 100}).status_code == 422

    def test_forgetting_a_moment_nobody_named_is_refused(self, client):
        assert client.delete("/api/snapshots/nope").status_code == 404

    def test_naming_a_moment_undoes_like_everything_else(self, client, world):
        client.post("/api/snapshots", json={"name": "Mistake", "day": 100})
        assert any(s["name"] == "Mistake" for s in world.snapshots())
        world.undo()
        assert not any(s["name"] == "Mistake" for s in world.snapshots())


class TestTheWriterCanLayTheirOwnRoad:
    def test_a_road_the_map_never_drew_changes_the_journey(self, client, world):
        """The travel engine could always route over these and only the generator and
        the seed could make one.

        Blackmere and Red Ford are three stops apart in the seeded world, so a writer
        who knows there is a direct road can be checked against a real before and after
        rather than against a journey that already worked.
        """
        here, there = place(world, "Blackmere"), place(world, "Red Ford")
        ask = lambda: client.get("/api/route", params={          # noqa: E731
            "origin_id": here, "destination_id": there, "profile": "horse"}).json()
        before = ask()
        assert len(before["path"]) > 2, "they were already neighbours"

        made = client.post("/api/segments", json={
            "from_entity_id": here, "to_entity_id": there, "length": 20,
            "medium": "road", "quality": 0.9, "terrain": "plain"})
        assert made.status_code == 201, made.json()

        after = ask()
        assert after["path"] == [here, there], "the writer's road was not taken"
        assert after["days"] < before["days"]

    def test_a_boat_road_over_dry_ground_is_refused_in_words(self, client, world):
        """The exact defect the generator shipped: a sea lane whose terrain said
        "plain" scored zero against every water profile and vanished from every route,
        with no error anywhere near the cause."""
        answer = client.post("/api/segments", json={
            "from_entity_id": place(world, "Greyhaven"),
            "to_entity_id": place(world, "Rennford"),
            "length": 40, "medium": "sea", "terrain": "plain"})
        assert answer.status_code == 422
        assert "water" in answer.json()["detail"]

    @pytest.mark.parametrize("medium", SAILED)
    def test_a_wet_way_can_actually_be_sailed(self, client, world, medium):
        """Spelled once, and checked by sailing it. A medium the router did not count
        as sailed would be walked over water, score zero against every profile, and
        disappear from every journey — silently."""
        here, there = place(world, "Blackmere"), place(world, "Red Ford")
        assert client.post("/api/segments", json={
            "from_entity_id": here, "to_entity_id": there, "length": 20,
            "medium": medium, "terrain": "water"}).status_code == 201
        route = client.get("/api/route", params={
            "origin_id": here, "destination_id": there, "profile": "barge"}).json()
        assert route["path"] == [here, there], f"a {medium} nothing can travel"

    def test_a_season_this_calendar_does_not_have_is_refused(self, client, world):
        """The seed itself once closed a pass in "Darkening", which is a month."""
        answer = client.post("/api/segments", json={
            "from_entity_id": place(world, "Greyhaven"),
            "to_entity_id": place(world, "Rennford"), "length": 40,
            "closed_seasons": ["Darkening"]})
        assert answer.status_code == 422
        assert "season" in answer.json()["detail"]

    def test_a_road_from_a_place_to_itself_is_refused(self, client, world):
        here = place(world, "Greyhaven")
        assert client.post("/api/segments", json={
            "from_entity_id": here, "to_entity_id": here,
            "length": 40}).status_code == 422

    def test_a_road_of_no_length_is_refused(self, client, world):
        assert client.post("/api/segments", json={
            "from_entity_id": place(world, "Greyhaven"),
            "to_entity_id": place(world, "Rennford"),
            "length": 0}).status_code == 422

    def test_a_road_to_nowhere_is_refused(self, client, world):
        assert client.post("/api/segments", json={
            "from_entity_id": place(world, "Greyhaven"),
            "to_entity_id": "nowhere", "length": 40}).status_code == 404

    def test_a_road_can_be_taken_back_off_the_map(self, client, world):
        made = client.post("/api/segments", json={
            "from_entity_id": place(world, "Greyhaven"),
            "to_entity_id": place(world, "Rennford"), "length": 40}).json()
        assert client.delete(f"/api/segments/{made['id']}").status_code == 204
        assert not any(s.id == made["id"] for s in world.route_segments())


class TestTheWorldNeedNotBeMedievalEurope:
    def test_a_custom_kind_of_thing_behaves_like_the_builtin_ones(self, client):
        """§60's whole point: not a second-class extension, the same machinery."""
        client.post("/api/vocabulary/entity-types", json={
            "key": "star_system", "label": "Star system", "plural": "Star systems",
            "category": "geography"})
        made = client.post("/api/entities", json={
            "type_key": "star_system", "name": "Tau Ceti"}).json()
        assert made["type_key"] == "star_system"
        assert "star_system" in {t["key"] for t in
                                 client.get("/api/vocabulary").json()["entity_types"]}
        found = client.post("/api/query", json={
            "query": {"types": ["star_system"]}}).json()
        assert [r["name"] for r in found["rows"]] == ["Tau Ceti"]

    def test_a_custom_relationship_can_be_asserted_and_asked_about(self, client):
        client.post("/api/vocabulary/entity-types", json={
            "key": "star_system", "label": "Star system"})
        client.post("/api/vocabulary/predicates", json={
            "key": "orbits", "label": "orbits", "kind": "rel",
            "category": "geography"})
        sun = client.post("/api/entities", json={
            "type_key": "star_system", "name": "Sol"}).json()["id"]
        world_ = client.post("/api/entities", json={
            "type_key": "star_system", "name": "Tau Ceti"}).json()["id"]
        client.post("/api/facts", json={
            "subject_id": world_, "predicate_key": "orbits", "object_id": sun})
        answer = client.post("/api/query", json={"query": {
            "conditions": [{"predicate": "orbits", "object_id": sun}]}}).json()
        assert [r["name"] for r in answer["rows"]] == ["Tau Ceti"]

    def test_a_key_that_cannot_be_one_is_refused_in_words(self, client):
        answer = client.post("/api/vocabulary/entity-types", json={
            "key": "3 bodies!", "label": "Bodies"})
        assert answer.status_code == 422
        assert "letters" in answer.json()["detail"]

    def test_a_key_is_tidied_rather_than_rejected_for_a_space(self, client):
        made = client.post("/api/vocabulary/entity-types", json={
            "key": "Star System", "label": "Star system"}).json()
        assert made["key"] == "star_system"

    def test_a_kind_this_world_already_has_is_refused(self, client):
        assert client.post("/api/vocabulary/entity-types", json={
            "key": "person", "label": "Person"}).status_code == 409

    def test_an_inverse_that_names_nothing_is_refused(self, client):
        """§77: the inverse is what puts the fact on the other page too. A name for a
        predicate that does not exist would put it on nobody's."""
        answer = client.post("/api/vocabulary/predicates", json={
            "key": "orbits", "label": "orbits", "inverse_key": "is_orbited_by"})
        assert answer.status_code == 404

    def test_a_predicate_that_is_neither_a_link_nor_a_value_is_refused(self, client):
        assert client.post("/api/vocabulary/predicates", json={
            "key": "hums", "label": "hums", "kind": "vibe"}).status_code == 422
