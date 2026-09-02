"""The parts of the world model a writer could not reach (§8, §6, §43, §49).

`World.add_title`, `grant_title`, `add_secret` and `set_knowledge` were all written,
tested and revision-logged, and none of them had an HTTP route or a form. The `chapter`
table and its foreign key from `scene` had been in the schema since the first migration
and nothing could set one. Which meant succession, scene context and half the dashboard
were, for any world but the seeded demo, screens with no way to fill them: a writer could
not create a title, so nothing could be inherited; could not record a secret, so nobody
could know one; could not put a scene in a chapter, so every scene was loose in the world
rather than in their book.

Asserted here as the round trip that matters — make it through the API, and see the
engine that was already written pick it up.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fw.api.app import create_app
from fw.core.seed.renn import seed_renn


@pytest.fixture
def world():
    made = seed_renn()
    yield made
    made.close()


@pytest.fixture
def client(world):
    return TestClient(create_app(world))


def person(world, part: str) -> str:
    return next(e.id for e in world.entities("person") if part in e.name)


class TestATitleCanBeMadeAndGranted:
    def test_a_new_title_appears_in_the_list(self, client):
        made = client.post("/api/titles", json={
            "name": "Lord of the Blackmere", "rank": 2}).json()
        assert made["id"]
        assert "Lord of the Blackmere" in {t["name"] for t in client.get(
            "/api/titles").json()}

    def test_the_succession_engine_picks_it_up(self, client, world):
        """The point of the whole thing: an engine that already worked, given work."""
        aldren = person(world, "Aldren")
        title = client.post("/api/titles", json={
            "name": "Keeper of the Ford", "rank": 1,
            "succession_law": "absolute_primogeniture"}).json()["id"]
        client.post(f"/api/titles/{title}/grants", json={"holder_id": aldren})
        line = client.get(f"/api/succession/{title}").json()
        assert line["title_name"] == "Keeper of the Ford"
        assert line["law_key"] == "absolute_primogeniture"
        assert line["line"], "a title with a holder and no heirs at all"

    def test_a_holding_says_who_and_from_when(self, client, world):
        edric = person(world, "Edric")
        title = client.post("/api/titles", json={"name": "Warden of Nothing"}).json()
        got = client.post(f"/api/titles/{title['id']}/grants", json={
            "holder_id": edric, "from_day": 80000, "how": "conquest",
            "disputed": True}).json()
        assert got["how"] == "conquest" and got["disputed"] is True
        listed = next(t for t in client.get("/api/titles").json()
                      if t["id"] == title["id"])
        assert listed["holdings"][0]["how"] == "conquest"

    def test_a_law_nobody_has_is_refused_with_the_ones_that_exist(self, client):
        answer = client.post("/api/titles", json={"name": "X",
                                                  "succession_law": "by_arm_wrestle"})
        assert answer.status_code == 422
        assert "primogeniture" in answer.json()["detail"]

    def test_a_grant_to_nobody_is_refused(self, client):
        title = client.post("/api/titles", json={"name": "Y"}).json()["id"]
        assert client.post(f"/api/titles/{title}/grants",
                           json={"holder_id": "nobody"}).status_code == 404

    def test_a_holding_cannot_end_before_it_begins(self, client, world):
        title = client.post("/api/titles", json={"name": "Z"}).json()["id"]
        answer = client.post(f"/api/titles/{title}/grants", json={
            "holder_id": person(world, "Edric"), "from_day": 900, "to_day": 100})
        assert answer.status_code == 422

    def test_making_a_title_undoes_like_everything_else(self, client, world):
        client.post("/api/titles", json={"name": "Undo Me"})
        assert any(t.name == "Undo Me" for t in world.titles())
        world.undo()
        assert not any(t.name == "Undo Me" for t in world.titles())


class TestASecretCanBeRecordedAndKnown:
    def test_the_truth_lives_once_and_the_stances_beside_it(self, client, world):
        secret = client.post("/api/secrets", json={
            "name": "The forged will",
            "truth": "It was written a week after he died.",
            "severity": "catastrophic"}).json()
        client.post("/api/knowledge", json={
            "observer_id": person(world, "Edric"), "secret_id": secret["id"],
            "stance": "knows"})
        client.post("/api/knowledge", json={
            "observer_id": person(world, "Mara"), "secret_id": secret["id"],
            "stance": "suspects"})
        listed = next(s for s in client.get("/api/secrets").json()
                      if s["name"] == "The forged will")
        assert set(listed["by_stance"]) == {"knows", "suspects"}
        assert listed["truth"].startswith("It was written")

    def test_the_second_order_case_the_brief_names(self, client, world):
        """Edric does not merely believe the wrong thing.

        He believes that *Mara* believes it, and a scene turns on the difference — which
        a boolean on the fact cannot hold at all.
        """
        secret = client.post("/api/secrets", json={"name": "The debt"}).json()["id"]
        client.post("/api/knowledge", json={
            "observer_id": person(world, "Edric"), "secret_id": secret,
            "about_observer_id": person(world, "Mara"), "stance": "misinformed"})
        listed = next(s for s in client.get("/api/secrets").json()
                      if s["name"] == "The debt")
        assert listed["by_stance"]["misinformed"][0]["about"]["name"]

    def test_a_stance_nobody_uses_is_refused_with_the_ones_that_exist(self, client,
                                                                     world):
        secret = client.post("/api/secrets", json={"name": "S"}).json()["id"]
        answer = client.post("/api/knowledge", json={
            "observer_id": person(world, "Edric"), "secret_id": secret,
            "stance": "reckons"})
        assert answer.status_code == 422 and "suspects" in answer.json()["detail"]

    def test_a_severity_nobody_uses_is_refused(self, client):
        assert client.post("/api/secrets", json={
            "name": "S", "severity": "apocalyptic"}).status_code == 422

    def test_knowing_a_secret_that_does_not_exist_is_refused(self, client, world):
        assert client.post("/api/knowledge", json={
            "observer_id": person(world, "Edric"), "secret_id": "nope",
            "stance": "knows"}).status_code == 404

    def test_the_scene_context_engine_picks_it_up(self, client, world):
        """The other engine that was written and had nothing to work on."""
        secret = client.post("/api/secrets", json={
            "name": "The bridge was sold", "truth": "For six hundred marks."}).json()
        edric = person(world, "Edric")
        client.post("/api/knowledge", json={
            "observer_id": edric, "secret_id": secret["id"], "stance": "knows"})
        scene = client.post("/api/scenes", json={
            "title": "The reckoning", "day": world.day(240),
            "participants": [edric]}).json()
        context = client.get(f"/api/scenes/{scene['id']}/context").json()
        assert any("bridge was sold" in line["text"] or
                   line["secret_name"] == "The bridge was sold"
                   for line in context["secrets"])


class TestTheBookItselfCanBeMade:
    """The half of §43 the previous commit left open.

    A scene could be *placed in* a chapter and nothing could make one, so the scene
    form's "where it sits in the book" field appeared for the seeded demo and for
    nobody else — the writer could see the shape of something they could not use.
    """

    def test_a_book_and_its_chapters(self, client):
        book = client.post("/api/works", json={"title": "The Iron Road"}).json()
        client.post("/api/chapters", json={"work_id": book["id"], "title": "The Ford"})
        client.post("/api/chapters", json={"work_id": book["id"], "title": "The Keep",
                                           "position": 1})
        listed = client.get("/api/works").json()
        made = next(w for w in listed if w["title"] == "The Iron Road")
        assert [c["title"] for c in made["chapters"]] == ["The Ford", "The Keep"]

    def test_the_book_shows_what_is_written_in_it(self, client):
        """The only useful question about a manuscript is what is in it, in order."""
        book = client.post("/api/works", json={"title": "The Iron Road"}).json()["id"]
        chapter = client.post("/api/chapters", json={
            "work_id": book, "title": "The Ford"}).json()["id"]
        client.post("/api/scenes", json={"title": "At the ford", "chapter_id": chapter,
                                         "position": 1})
        client.post("/api/scenes", json={"title": "Before the ford",
                                         "chapter_id": chapter, "position": 0})
        client.post("/api/scenes", json={"title": "Loose"})
        written = next(w for w in client.get("/api/works").json()
                       if w["title"] == "The Iron Road")
        assert [s["title"] for s in written["chapters"][0]["scenes"]] == [
            "Before the ford", "At the ford"]
        assert written["loose_scenes"] >= 1

    def test_a_chapter_of_a_book_that_does_not_exist_is_refused(self, client):
        answer = client.post("/api/chapters", json={"work_id": "nope", "title": "One"})
        assert answer.status_code == 404
        assert "no such book" in answer.json()["detail"]

    def test_a_book_with_no_title_is_refused(self, client):
        assert client.post("/api/works", json={"title": "   "}).status_code == 422

    def test_making_a_book_undoes_like_everything_else(self, client, world):
        """§59. A writer who mistypes a title presses Ctrl+Z and expects it gone —
        and until this had a route nobody could make one by accident, so nobody
        found out that `add_work` never wrote to the revision log at all."""
        before = len(world.works())
        client.post("/api/works", json={"title": "Wrong Title"})
        assert any(w["title"] == "Wrong Title" for w in world.works())
        world.undo()
        assert len(world.works()) == before
        assert not any(w["title"] == "Wrong Title" for w in world.works())

    def test_undoing_a_chapter_leaves_the_book_it_was_in(self, client, world):
        """Undo is a stack, so this is the chapter's own action coming back off it."""
        book = client.post("/api/works", json={"title": "The Iron Road"}).json()["id"]
        chapter = client.post("/api/chapters", json={
            "work_id": book, "title": "The Ford"}).json()["id"]
        world.undo()
        assert any(w["id"] == book for w in world.works())
        assert not any(c["id"] == chapter for c in world.chapters())


class TestASceneCanGoInTheBook:
    def test_a_scene_can_be_placed_in_a_chapter(self, client, world):
        work = world.add_work("The Iron Road")
        chapter = world.add_chapter(work, "The Ford")
        made = client.post("/api/scenes", json={
            "title": "At the ford", "chapter_id": chapter}).json()
        placed = next(s for s in world.scenes() if s.id == made["id"])
        assert placed.chapter_id == chapter

    def test_the_chapters_are_listed_with_the_book_they_are_in(self, client, world):
        work = world.add_work("The Iron Road")
        world.add_chapter(work, "The Ford")
        listed = client.get("/api/chapters").json()
        assert any(c["title"] == "The Ford" and c["work_title"] == "The Iron Road"
                   for c in listed)

    def test_a_chapter_that_does_not_exist_is_refused(self, client):
        assert client.post("/api/scenes", json={
            "title": "Nowhere", "chapter_id": "nope"}).status_code == 404

    def test_a_scene_with_no_chapter_is_still_a_scene(self, client):
        """Most scenes are written before anybody knows which chapter they are in."""
        assert client.post("/api/scenes", json={"title": "Loose"}).status_code == 201


class TestTheWorldAnswersQuestions:
    def test_a_question_comes_back_with_its_working(self, client, world):
        veyne = world.entity_named("House Veyne").id
        answer = client.post("/api/query", json={"query": {
            "types": ["house"],
            "conditions": [{"predicate": "vassal_of", "object_id": veyne}],
            "explain": True}}).json()
        assert [r["name"] for r in answer["rows"]] == ["House Marr"]
        assert answer["rows"][0]["because"]
        assert "FROM entity e" in answer["sql"]

    def test_a_question_that_cannot_mean_anything_is_refused_in_words(self, client):
        answer = client.post("/api/query", json={"query": {"types": ["dragon"]}})
        assert answer.status_code == 422
        assert "no such kind of thing" in answer.json()["detail"]

    def test_the_form_can_enumerate_everything_a_question_is_made_of(self, client):
        words = client.get("/api/query/vocabulary").json()
        assert {"directions", "tests", "orders", "confidence", "tags"} <= set(words)
        assert "greater_than" in words["tests"]

    def test_a_question_can_be_kept_and_asked_again(self, client):
        client.post("/api/queries", json={
            "name": "The fords", "note": "for chapter nine",
            "query": {"name_contains": "ford"}})
        kept = client.get("/api/queries").json()
        assert kept and kept[0]["name"] == "The fords"
        again = client.post("/api/query", json={"query": kept[0]["query"]}).json()
        assert any("Ford" in r["name"] or "ford" in r["name"]
                   for r in again["rows"])
        assert client.delete(f"/api/queries/{kept[0]['key']}").status_code == 204
        assert client.get("/api/queries").json() == []


class TestWhatWasAlwaysStoredIsNowShown:
    def test_a_fact_line_carries_its_dates_and_where_it_came_from(self, client, world):
        """All three written on every fact since the first migration, rendered nowhere.

        Which is worse than absent: a writer who dated a fact, or cited the note it came
        from, could not see on the line that they had.
        """
        source = world.add_source("Chapter 3 draft", kind="manuscript")
        marr = world.entity_named("House Marr").id
        world.assert_fact(marr, "motto", value="Iron endures, twice",
                          valid_from=world.day(150), source_id=source)
        lines = client.get(f"/api/entities/{marr}").json()["facts"]
        cited = next(f for f in lines if f["value"] == "Iron endures, twice")
        assert cited["source"] == "Chapter 3 draft"
        assert cited["valid_from_text"]

    def test_a_fact_nobody_dated_says_nothing_rather_than_guessing(self, client, world):
        marr = world.entity_named("House Marr").id
        lines = client.get(f"/api/entities/{marr}").json()["facts"]
        undated = [f for f in lines if f["valid_from"] is None]
        assert undated and all(f["valid_from_text"] == "" for f in undated)
