"""One world, from an empty file to a map you can ask questions about.

Every other test file checks one seam. This one checks that the seams meet: a writer
starts with nothing, describes three countries in prose, and by the end has a lit
continent with named rivers and labelled marches, a line of succession over a title they
invented, a secret somebody knows, and the ability to ask their own notes a question and
get an answer with the working shown.

The point is the *order*. Each phase of this work assumed the one before it — the map is
built out of one reading of the world, the labels are built out of the map, the queries
are built out of the fact spine the map writes into — and a suite of per-module tests
cannot notice when two of those stop agreeing. This walks the whole chain once.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fw.api.app import create_app
from fw.core.calendar.kernel import GREGORIAN
from fw.core.continuity.engine import check
from fw.core.world import World

PROSE = [
    ("The Iron Spine", "mountains and high crags", "cold, heavy snow", "60000", "iron"),
    ("The Sunlit Coast", "coast and harbour", "warm and humid", "140000", "fish"),
    ("Greenhollow", "forest and river valley", "temperate, rain", "90000", "timber"),
]


@pytest.fixture
def fresh():
    """A world with nothing in it, which is where every writer starts."""
    made = World.create(name="Ashmere", calendar=GREGORIAN)
    yield made
    made.close()


def describe(world: World) -> dict[str, str]:
    ids = {}
    for name, terrain, climate, people, produces in PROSE:
        region = world.add_entity("region", name, summary=f"{terrain}, {climate}.")
        for key, value in (("terrain", terrain), ("climate", climate),
                           ("population", people)):
            world.assert_fact(region, key, value=value)
        stuff = world.add_entity("resource", produces.title())
        world.assert_fact(region, "produces", stuff, strength="high")
        ids[name] = region.id
    world.assert_fact(ids["The Iron Spine"], "borders", ids["Greenhollow"])
    world.assert_fact(ids["The Sunlit Coast"], "borders", ids["Greenhollow"])
    return ids


class TestFromProseToAMapYouCanRead:
    def test_the_whole_chain_holds_together(self, fresh):
        regions = describe(fresh)
        client = TestClient(create_app(fresh))

        # --- the map is proposed, not written (§66) ------------------------
        before = len(fresh.geometries())
        plan = client.post("/api/map/plan",
                           json={"invent_settlements": True}).json()
        assert len(fresh.geometries()) == before, "planning wrote to the world"
        assert plan["features"], "three described countries and no map"
        assert plan["reading_fingerprint"], "the plan cannot say which world it read"

        kinds = {f["kind"] for f in plan["features"]}
        assert {"coast", "region", "river", "settlement", "road"} <= kinds, kinds

        # Every feature owes the writer a sentence (§67).
        assert all(f["why"] for f in plan["features"])

        # --- and then accepted ---------------------------------------------
        report = client.post("/api/map/apply", json={
            "plan": plan,
            "decisions": [{"feature_id": f["id"], "accept": True}
                          for f in plan["features"]],
        }).json()
        assert report["summary"]
        assert len(fresh.geometries()) > before

        # --- the map draws itself, with names on it (C11) ------------------
        drawn = client.get("/api/map").json()
        draw = drawn["draw"]
        assert draw["bounds"]["width"] > 0
        assert draw["labels"], "a map with no names on it"
        assert draw["icons"], "no places drawn"
        assert draw["legend"], "no key"
        # Ranks reached the picture: places are not all one dot.
        assert len({icon["shape"] for icon in draw["icons"]}) > 1
        # And nothing in the generator emitted a colour.
        for feature in drawn["features"]:
            assert not any(str(v).startswith("#")
                           for v in (feature["style"] or {}).values())

        # --- the world the map made is a world (§49) -----------------------
        answer = client.post("/api/query", json={"query": {
            "types": ["settlement"], "explain": True}}).json()
        assert answer["rows"], "the map made towns and the world cannot find them"
        assert answer["sql"] and answer["ms"] >= 0

        # A town the map proposed is not the writer's own list, until they say so.
        theirs = client.get("/api/entities",
                            params={"type_key": "settlement",
                                    "hide_generated": True}).json()
        assert len(theirs) == len(answer["rows"]), \
            "an accepted town vanished from the writer's own list"

        # --- politics, over ground that now exists (§8) --------------------
        house = fresh.add_entity("house", "House Ashe")
        lord = fresh.add_entity("person", "Lord Ashe")
        fresh.assert_fact(house, "legally_owns", regions["Greenhollow"])
        title = client.post("/api/titles", json={
            "name": "Warden of Greenhollow", "rank": 2,
            "territory_id": regions["Greenhollow"]}).json()["id"]
        client.post(f"/api/titles/{title}/grants", json={"holder_id": lord.id})
        line = client.get(f"/api/succession/{title}").json()
        assert line["title_name"] == "Warden of Greenhollow"

        held = client.post("/api/query", json={"query": {
            "conditions": [{"predicate": "legally_owns", "direction": "in",
                            "object_id": house.id}]}}).json()
        assert [r["name"] for r in held["rows"]] == ["Greenhollow"]

        # --- and something nobody is supposed to know (§6) -----------------
        secret = client.post("/api/secrets", json={
            "name": "The charter is forged",
            "truth": "The Warden's grant was written after the old lord died."}).json()
        client.post("/api/knowledge", json={
            "observer_id": lord.id, "secret_id": secret["id"], "stance": "knows"})
        assert any(s["name"] == "The charter is forged"
                   for s in client.get("/api/secrets").json())

        # --- nothing the map did upset the writer's own world --------------
        complaints = check(fresh)
        assert not complaints.errors, [v.message for v in complaints.errors]

    def test_a_second_map_of_the_same_world_is_the_same_map(self, fresh):
        """The property everything else rests on. Drift here is a map that wanders."""
        describe(fresh)
        client = TestClient(create_app(fresh))
        first = client.post("/api/map/plan", json={"invent_settlements": True}).json()
        second = client.post("/api/map/plan", json={"invent_settlements": True}).json()
        assert first["plan_id"] == second["plan_id"]
        assert first["reading_fingerprint"] == second["reading_fingerprint"]
        assert ([f["name"] for f in first["features"]]
                == [f["name"] for f in second["features"]])

    def test_accepting_a_map_is_one_thing_to_undo(self, fresh):
        """A map that took hundreds of undos to remove would be a trap (§59)."""
        describe(fresh)
        client = TestClient(create_app(fresh))
        plan = client.post("/api/map/plan", json={"invent_settlements": True}).json()
        client.post("/api/map/apply", json={
            "plan": plan,
            "decisions": [{"feature_id": f["id"], "accept": True}
                          for f in plan["features"]]})
        assert fresh.geometries()
        fresh.undo()
        assert not fresh.geometries(), "one Ctrl+Z did not take the map back"

    def test_a_writer_who_turns_the_map_down_keeps_their_world(self, fresh):
        """§66: nothing is written that was not accepted."""
        describe(fresh)
        client = TestClient(create_app(fresh))
        plan = client.post("/api/map/plan", json={"invent_settlements": True}).json()
        client.post("/api/map/apply", json={
            "plan": plan,
            "decisions": [{"feature_id": f["id"], "accept": False}
                          for f in plan["features"]]})
        assert not [e for e in fresh.entities("settlement")]
        assert len(fresh.entities("region")) == len(PROSE)

    def test_the_map_can_be_asked_for_a_year_and_answers_for_that_year(self, fresh):
        """§36. The whole world is temporal, and the map is part of the world.

        Both halves, because only asserting the absence would pass on a plan that had
        quietly ignored the date and drawn the present twice.
        """
        ids = describe(fresh)
        fresh.update_entity(ids["Greenhollow"], exists_from=fresh.day(1500))
        client = TestClient(create_app(fresh))

        def regions(year: int) -> set[str]:
            plan = client.post("/api/map/plan",
                               json={"at": fresh.day(year)}).json()
            return {f["name"] for f in plan["features"] if f["kind"] == "region"}

        assert "Greenhollow" not in regions(1000)
        assert "Greenhollow" in regions(2000)
