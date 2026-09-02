"""The world through somebody's eyes (§93, §94).

§94 asks for the world viewed from a selected perspective — objective, House Veyne, Mara,
the Northern Church — and names five things it may change: known information, political
labels, territorial claims, historical interpretation, geography knowledge. Every one of
those already existed in this application and nothing joined them, so the README named
this first among what was not yet built.

What is asserted here is that a perspective is a *lens*, never the world: the objective
map is unchanged by anything a perspective does, ignorance is opt-in so an unannotated
world renders exactly as before, and every difference can say why it is there (§67).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fw.api.app import create_app
from fw.core.derive.perspective import Perspective, who_can_be_one
from fw.core.seed.renn import seed_renn


@pytest.fixture
def world():
    made = seed_renn()
    yield made
    made.close()


@pytest.fixture
def client(world):
    return TestClient(create_app(world))


def named(world, part: str) -> str:
    return next(e.id for e in world.entities() if part in e.name)


def features(payload) -> set[str]:
    return {f["name"] for f in payload["features"]}


def held(payload, place: str, mode: str = "legally_owns") -> list[str]:
    feature = next(f for f in payload["features"] if f["name"] == place)
    return [h["name"] for h in (feature["control"].get(mode) or [])]


class TestTheMapFromNowhereIsStillThere:
    def test_no_perspective_changes_nothing(self, client):
        """The default has to be exactly the map that shipped, or this is a regression
        dressed as a feature."""
        plain = client.get("/api/map").json()
        assert plain["seen_as"] is None and plain["seen_as_name"] == ""
        assert plain["features"]

    def test_a_perspective_never_writes(self, client, world):
        """A lens over the world, not a change to it."""
        before = len(world.geometries()), len(world.facts_where())
        client.get("/api/map", params={"as": named(world, "House Orren")})
        client.get("/api/state", params={"day": world.day(241),
                                         "as": named(world, "House Marr")})
        assert (len(world.geometries()), len(world.facts_where())) == before

    def test_the_objective_map_is_unaffected_by_somebody_s_ignorance(self, client,
                                                                    world):
        mara = named(world, "Mara")
        world.assert_fact(mara, "unaware_of", named(world, "Blackmere"))
        assert "Blackmere" in features(client.get("/api/map").json())

    def test_somebody_who_has_said_nothing_sees_the_ordinary_world(self, client, world):
        """Ignorance is opt-in (§93 says "optionally"), so an unannotated observer is
        not blind — otherwise switching perspective would empty the map."""
        plain = client.get("/api/map").json()
        quiet = client.get("/api/map",
                           params={"as": named(world, "Edric")}).json()
        assert features(quiet) == features(plain)


class TestWhatTheySeeAndDoNotSee:
    def test_a_place_they_have_never_heard_of_is_missing(self, client, world):
        """§93's own example: the objective map versus Mara's understanding of it."""
        mara = named(world, "Mara")
        assert "Blackmere" in features(client.get("/api/map",
                                                  params={"as": mara}).json())
        world.assert_fact(mara, "unaware_of", named(world, "Blackmere"))
        assert "Blackmere" not in features(client.get("/api/map",
                                                      params={"as": mara}).json())

    def test_they_learn_of_it_and_it_comes_back(self, client, world):
        """Ignorance is a dated fact, so finding out is ending it — not deleting it.

        Which is §106.3's rule: history is not overwritten, and "she did not know until
        the spring" stays true of the spring.
        """
        mara, place = named(world, "Mara"), named(world, "Blackmere")
        fact = world.assert_fact(mara, "unaware_of", place,
                                 valid_from=world.day(200))
        world.end_fact(fact.id, world.day(240))

        early = client.get("/api/map", params={"as": mara,
                                               "day": world.day(220)}).json()
        late = client.get("/api/map", params={"as": mara,
                                              "day": world.day(250)}).json()
        assert "Blackmere" not in features(early)
        assert "Blackmere" in features(late)


class TestWhatTheyCallThings:
    def test_the_map_uses_their_names(self, client, world):
        """§94's political labels, drawn rather than described."""
        marr = named(world, "House Marr")
        seen = client.get("/api/state", params={"day": world.day(241),
                                                "as": marr}).json()
        oren = named(world, "Prince Oren")
        assert next(e["name"] for e in seen["entities"] if e["id"] == oren) \
            == "The Pretender"

    def test_everyone_else_still_sees_his_own_name(self, client, world):
        plain = client.get("/api/state", params={"day": world.day(241)}).json()
        oren = named(world, "Prince Oren")
        assert next(e["name"] for e in plain["entities"] if e["id"] == oren) \
            == "Prince Oren"


class TestWhoseGroundItIs:
    def test_a_claimant_s_map_shows_the_ground_as_theirs(self, client, world):
        """The seeded world contests Greyhaven four ways — owned in law by Marr,
        administered by Veyne, taxed by Renn, claimed by Orren. That disagreement is
        what a perspective is for."""
        orren = named(world, "House Orren")
        assert held(client.get("/api/map").json(), "Greyhaven") == ["House Marr"]
        assert held(client.get("/api/map", params={"as": orren}).json(),
                    "Greyhaven") == ["House Orren"]

    def test_the_rest_of_the_world_still_looks_like_itself(self, client, world):
        """The claim is substituted into the authority being shown rather than the map
        being switched to `claims` — switching would leave every place nobody claims
        uncoloured, which is a worse picture than the objective one and not what the
        claimant believes either."""
        orren = named(world, "House Orren")
        plain = client.get("/api/map").json()
        theirs = client.get("/api/map", params={"as": orren}).json()
        assert theirs["draw"]["mode"] == plain["draw"]["mode"]
        for place in ("Rennford", "Millbrook", "Northwatch"):
            assert held(theirs, place) == held(plain, place)

    def test_a_claim_does_not_move_ground_on_anyone_else_s_map(self, client, world):
        marr = named(world, "House Marr")
        assert held(client.get("/api/map", params={"as": marr}).json(),
                    "Greyhaven") == ["House Marr"]


class TestItSaysWhatItChangedAndWhy:
    def test_every_difference_carries_its_reason(self, client, world):
        """§67 refuses black boxes, and a view that quietly altered a map is the purest
        kind: the writer must be able to see *why* and disagree."""
        marr = named(world, "House Marr")
        told = client.get(f"/api/perspectives/{marr}").json()
        assert told["observer_name"] == "House Marr"
        kinds = {d["kind"] for d in told["differences"]}
        assert {"renamed", "told"} <= kinds
        assert all(d["evidence"] for d in told["differences"])

    def test_the_objective_view_has_nothing_to_explain(self, world):
        assert Perspective(world, None, world.day(241)).differences() == []

    def test_ignorance_is_explained_too(self, client, world):
        mara = named(world, "Mara")
        world.assert_fact(mara, "unaware_of", named(world, "Blackmere"))
        told = client.get(f"/api/perspectives/{mara}").json()
        hidden = next(d for d in told["differences"] if d["kind"] == "hidden")
        assert "Blackmere" in hidden["text"]


class TestWhoIsWorthOffering:
    def test_only_parties_who_have_said_something(self, client, world):
        """A picker of every entity would be hundreds of choices that change nothing."""
        offered = {row["name"] for row in client.get("/api/perspectives").json()}
        assert {"House Marr", "House Renn", "House Orren"} <= offered
        assert "Lady Mara" not in offered      # she has said nothing yet

    def test_saying_something_puts_you_on_the_list(self, client, world):
        mara = named(world, "Mara")
        world.assert_fact(mara, "unaware_of", named(world, "Blackmere"))
        offered = {row["name"] for row in client.get("/api/perspectives").json()}
        assert "Lady Mara" in offered

    def test_the_list_says_why_each_one_is_on_it(self, client, world):
        rows = {row["name"]: row["because"]
                for row in client.get("/api/perspectives").json()}
        assert "territory they claim" in rows["House Orren"]
        assert "name for somebody" in rows["House Marr"]

    def test_it_is_a_reading_not_a_query_per_feature(self, world):
        """Applied to every feature on a map, so it reads what the observer says once.

        Asserted because the obvious implementation is a query inside `sees()`, which is
        a few hundred round trips on a map of any size.
        """
        marr = named(world, "House Marr")
        seen = Perspective(world, marr, world.day(241))
        before = world.db.conn.total_changes
        for _ in range(500):
            seen.sees(marr)
            seen.name_for(marr, "House Marr")
        assert world.db.conn.total_changes == before


class TestNobodyRealIsRefused:
    def test_a_map_seen_by_nobody_is_refused(self, client):
        answer = client.get("/api/map", params={"as": "nope"})
        assert answer.status_code == 404
        assert "nobody" in answer.json()["detail"]

    def test_the_state_too(self, client, world):
        assert client.get("/api/state", params={"day": world.day(241),
                                                "as": "nope"}).status_code == 404


class TestItRespectsTheTimeline:
    def test_a_perspective_is_of_one_day(self, world):
        """A claim that has ended is not a claim, so an old map is not recoloured."""
        orren, place = named(world, "House Orren"), named(world, "Greyhaven")
        claim = next(f for f in world.facts_where("claims", subject_id=orren)
                     if f.object_id == place)
        # The claim begins in 238; it is pressed for two years and then dropped.
        world.end_fact(claim.id, world.day(240))
        assert Perspective(world, orren, world.day(239)).claims(place)
        assert not Perspective(world, orren, world.day(250)).claims(place)

    def test_who_can_be_one_is_branch_aware(self, world):
        """An opinion invented on a what-if does not make somebody a perspective on
        canon."""
        mara = named(world, "Mara")
        world.create_branch("what-if")
        alt = world.on_branch("what-if")
        alt.add_interpretation("The Widow", entity_id=mara, holder_id=mara)
        assert "Lady Mara" not in {r["name"] for r in who_can_be_one(world)}
        assert "Lady Mara" in {r["name"] for r in who_can_be_one(alt)}
