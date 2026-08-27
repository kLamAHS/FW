"""HTTP adapter tests.

The API is a translation layer, so these check translation: that routes exist, that they
return the shape the client expects, and that the interesting query parameters actually
change the answer. The world logic itself is tested against the core, not through HTTP.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fw.api.app import create_app
from fw.core.world import World


@pytest.fixture
def client(renn: World) -> TestClient:
    return TestClient(create_app(renn))


@pytest.fixture
def present(client: TestClient) -> int:
    return client.get("/api/world").json()["present_day"]


def entity_named(client: TestClient, name: str) -> dict:
    results = client.get("/api/search", params={"q": name}).json()
    match = next(e for e in results if e["name"] == name)
    return match


class TestWorldEndpoints:
    def test_world_summary(self, client):
        body = client.get("/api/world").json()
        assert body["name"] == "The Kingdom of Renn"
        assert body["counts"]["total"] == 35
        assert body["calendar"]["name"] == "Rennish"
        assert body["calendar"]["days_in_year"] == 355
        assert len(body["calendar"]["months"]) == 5
        # the slider needs a span that actually contains the world's history
        assert body["span"]["first"] < body["present_day"] < body["span"]["last"]

    def test_vocabulary_drives_the_ui(self, client):
        """The client renders forms from this, so every part must be present."""
        body = client.get("/api/vocabulary").json()
        assert len(body["entity_types"]) > 20
        assert len(body["predicates"]) > 50
        assert body["scales"]
        assert any(law["key"] == "male_preference_primogeniture"
                   for law in body["succession_laws"])
        assert any(p["key"] == "messenger" for p in body["transport_profiles"])

    def test_date_rendering(self, client, present):
        body = client.get(f"/api/date/{present}").json()
        assert body["month_name"] in ("Frostwane", "Seedfall", "Highsun",
                                      "Harvestide", "Darkening")
        assert body["era"] == "AK"
        assert body["season"]

    def test_snapshots(self, client):
        body = client.get("/api/snapshots").json()
        assert any(s["name"] == "Before the Red War" for s in body)
        assert all("date" in s for s in body)


class TestEntities:
    def test_list_and_filter(self, client):
        assert len(client.get("/api/entities").json()) == 35
        people = client.get("/api/entities", params={"type_key": "person"}).json()
        assert len(people) == 10
        assert all(e["type_key"] == "person" for e in people)

    def test_list_at_a_date_hides_the_unfounded(self, client, renn):
        early = renn.day(100)
        names = {e["name"] for e in
                 client.get("/api/entities", params={"at": early}).json()}
        assert "Greyhaven" not in names          # founded 120
        assert "Rennford" in names               # founded 94

    def test_entity_page_bundle(self, client):
        """§75/§76: one round trip for everything the page and side panel need."""
        greyhaven = entity_named(client, "Greyhaven")
        body = client.get(f"/api/entities/{greyhaven['id']}").json()
        assert body["entity"]["name"] == "Greyhaven"
        assert body["facts"]
        assert body["geometry"]["kind"] == "point"
        assert any(s["title"].startswith("The Winter Feast") for s in body["scenes"])

    def test_person_page_carries_titles_and_knowledge(self, client):
        mara = entity_named(client, "Lady Mara")
        body = client.get(f"/api/entities/{mara['id']}").json()
        assert any(k["stance"] == "knows" for k in body["knowledge"])

    def test_create_update_delete(self, client):
        created = client.post("/api/entities", json={
            "type_key": "settlement", "name": "Newhaven", "summary": "A new port",
        })
        assert created.status_code == 201
        eid = created.json()["id"]

        patched = client.patch(f"/api/entities/{eid}", json={"name": "Renamed"})
        assert patched.json()["name"] == "Renamed"
        assert any(e["name"] == "Renamed"
                   for e in client.get("/api/search", params={"q": "Renamed"}).json())

        assert client.delete(f"/api/entities/{eid}").status_code == 204
        assert client.get(f"/api/entities/{eid}").status_code == 404

    def test_unknown_entity_is_404(self, client):
        assert client.get("/api/entities/nonexistent").status_code == 404
        assert client.patch("/api/entities/nope", json={"name": "x"}).status_code == 404

    def test_bad_entity_type_is_a_clean_400(self, client):
        response = client.post("/api/entities",
                               json={"type_key": "wyvern", "name": "Smoke"})
        assert response.status_code == 400
        assert "unknown entity type" in response.json()["detail"]


class TestFacts:
    def test_create_and_filter(self, client):
        a = entity_named(client, "House Marr")
        b = entity_named(client, "House Orren")
        created = client.post("/api/facts", json={
            "subject_id": a["id"], "predicate_key": "at_war_with",
            "object_id": b["id"], "valid_from": 0,
        })
        assert created.status_code == 201
        assert created.json()["predicate_label"] == "at war with"

        facts = client.get("/api/facts", params={"predicate_key": "at_war_with"}).json()
        assert len(facts) == 1

    def test_ending_a_fact_preserves_it(self, client, renn):
        a = entity_named(client, "House Marr")
        b = entity_named(client, "House Orren")
        fact = client.post("/api/facts", json={
            "subject_id": a["id"], "predicate_key": "allied_with",
            "object_id": b["id"], "valid_from": renn.day(200),
        }).json()

        ended = client.post(f"/api/facts/{fact['id']}/end",
                            params={"on_day": renn.day(230)}).json()
        assert ended["valid_to"] == renn.day(230)

        def alliance_holds_on(day: int) -> bool:
            # Scoped to this pair: the seeded world has other alliances of its own.
            return any(
                f["id"] == fact["id"]
                for f in client.get("/api/facts", params={
                    "predicate_key": "allied_with", "subject_id": a["id"], "at": day,
                }).json()
            )

        # Still true before it ended, no longer true after — and never deleted.
        assert alliance_holds_on(renn.day(210))
        assert not alliance_holds_on(renn.day(240))
        assert client.get("/api/facts", params={
            "predicate_key": "allied_with", "subject_id": a["id"]}).json()


class TestWorldState:
    def test_state_changes_with_the_date(self, client, renn):
        """§3: the timeline slider's whole purpose."""
        early = client.get("/api/state", params={"day": renn.day(100)}).json()
        late = client.get("/api/state", params={"day": renn.day(241)}).json()
        assert len(late["entities"]) > len(early["entities"])
        assert early["date"]["year"] == 100
        assert late["date"]["year"] == 241

    def test_secrets_can_be_excluded(self, client, present):
        with_secrets = client.get(
            "/api/state", params={"day": present}).json()
        without = client.get(
            "/api/state", params={"day": present, "include_secret": False}).json()
        assert len(without["facts"]) < len(with_secrets["facts"])

    def test_map_carries_control_and_changes_over_time(self, client, renn):
        """§11 and §36: who holds what, visible on the map, per date."""
        body = client.get("/api/map", params={"day": renn.day(241)}).json()
        assert set(body["layers"]) >= {"regions", "roads", "settlements", "waterways"}
        greyhaven = next(f for f in body["features"] if f["name"] == "Greyhaven")
        control = greyhaven["control"]
        assert control["legally_owns"][0]["name"] == "House Marr"
        assert control["administers"][0]["name"] == "House Veyne"
        assert control["claims"][0]["name"] == "House Orren"

        early = client.get("/api/map", params={"day": renn.day(100)}).json()
        assert not any(f["name"] == "Greyhaven" for f in early["features"])

    def test_map_layer_filter(self, client, present):
        body = client.get("/api/map", params={"day": present, "layer": "roads"}).json()
        assert body["layers"] == ["roads"]


class TestGraphAndPedigree:
    def test_graph(self, client):
        body = client.get("/api/graph").json()
        assert body["nodes"] and body["edges"]
        assert all({"source", "target", "predicate"} <= set(e) for e in body["edges"])

    def test_graph_category_filter(self, client):
        politics = client.get("/api/graph", params={"categories": "politics"}).json()
        assert politics["edges"]
        assert all(e["category"] == "politics" for e in politics["edges"])

    def test_graph_can_centre_on_one_entity(self, client):
        mara = entity_named(client, "Lady Mara")
        body = client.get("/api/graph",
                          params={"centre": mara["id"], "hops": 1}).json()
        full = client.get("/api/graph").json()
        assert len(body["nodes"]) < len(full["nodes"])

    def test_pedigree_layout(self, client):
        body = client.get("/api/pedigree").json()
        assert body["people"]
        names = {p["name"] for p in body["people"]}
        assert {"Old King Renn", "King Aldren", "Prince Oren", "Lady Mara"} <= names
        # generations are ranked, not scattered
        by_name = {p["name"]: p for p in body["people"]}
        assert by_name["Old King Renn"]["generation"] == 0
        assert by_name["King Aldren"]["generation"] == 1
        assert by_name["Prince Oren"]["generation"] == 2
        assert by_name["Prince Oren"]["y"] > by_name["King Aldren"]["y"]

    def test_pedigree_marks_the_disputed_parentage(self, client):
        """§39: 'indicate uncertain parentage' rather than silently picking one."""
        body = client.get("/api/pedigree").json()
        assert any(link["uncertain"] for link in body["links"])

    def test_kin_query(self, client):
        """§49: 'who is related to Lady Mara within three generations?'"""
        mara = entity_named(client, "Lady Mara")
        body = client.get(f"/api/kin/{mara['id']}", params={"hops": 3}).json()
        names = {k["name"] for k in body}
        assert "Lord Corren" in names
        assert any(k["relationship"] for k in body)


class TestSuccessionEndpoint:
    def test_the_specs_answer_over_http(self, client, renn):
        titles = client.get("/api/titles").json()
        crown = next(t for t in titles if t["name"] == "King of Renn")
        body = client.get(f"/api/succession/{crown['id']}",
                          params={"day": renn.day(240, 5, 61)}).json()
        assert [c["name"] for c in body["line"]][:4] == [
            "Prince Oren", "Lady Elia", "Lord Caros", "Lady Mara"
        ]
        assert not body["hypothetical"]
        assert body["explanation"]

    def test_hypothetical_over_http(self, client, renn):
        titles = client.get("/api/titles").json()
        crown = next(t for t in titles if t["name"] == "King of Renn")
        oren = entity_named(client, "Prince Oren")
        body = client.get(
            f"/api/succession/{crown['id']}",
            params={"day": renn.day(240, 5, 61), "illegitimate": oren["id"]},
        ).json()
        assert [c["name"] for c in body["line"]][:3] == [
            "Lady Elia", "Lord Caros", "Lady Mara"
        ]
        assert body["hypothetical"]
        assert body["assumptions"]

        # and canon is untouched
        again = client.get(f"/api/succession/{crown['id']}",
                           params={"day": renn.day(240, 5, 61)}).json()
        assert again["line"][0]["name"] == "Prince Oren"

    def test_law_override(self, client, renn):
        titles = client.get("/api/titles").json()
        crown = next(t for t in titles if t["name"] == "King of Renn")
        body = client.get(
            f"/api/succession/{crown['id']}",
            params={"day": renn.day(240, 5, 61), "law_key": "ultimogeniture"},
        ).json()
        assert body["line"][0]["name"] == "Lady Mara"
        assert body["law_label"] == "Ultimogeniture"

    def test_unknown_title(self, client):
        assert client.get("/api/succession/nope").status_code == 404

    def test_titles_include_current_holder(self, client, renn):
        titles = client.get("/api/titles", params={"at": renn.day(230)}).json()
        crown = next(t for t in titles if t["name"] == "King of Renn")
        assert crown["holder"]["name"] == "King Aldren"


class TestSceneContextEndpoint:
    def test_the_winter_feast(self, client):
        """§44's worked example, end to end."""
        scenes = client.get("/api/scenes").json()
        feast = next(s for s in scenes if "Winter Feast" in s["title"])
        body = client.get(f"/api/scenes/{feast['id']}/context").json()

        assert body["location"]["name"] == "Greyhaven"
        names = {p["name"] for p in body["participants"]}
        assert names == {"Lady Mara", "Tomas", "Edric", "Queen Sera", "Prince Oren"}

        text = " ".join(r["text"] for r in body["relationships"])
        assert "Lady Mara" in text and "Edric" in text

        secrets = " ".join(s["text"] for s in body["secrets"])
        assert "Lady Mara knows" in secrets
        assert "Queen Sera knows that Lady Mara knows" in secrets
        assert "Prince Oren is wrong about" in secrets

        assert body["goals"]
        assert any("execution" in e["name"] for e in body["recent_events"])
        assert body["tensions"]

    def test_relationships_are_ranked_not_dumped(self, client):
        """§45: rank by relevance; do not overwhelm."""
        scenes = client.get("/api/scenes").json()
        feast = next(s for s in scenes if "Winter Feast" in s["title"])
        body = client.get(f"/api/scenes/{feast['id']}/context").json()
        scores = [r["score"] for r in body["relationships"]]
        assert scores == sorted(scores, reverse=True)
        assert len(body["relationships"]) <= 12
        assert all(r["reasons"] for r in body["relationships"])

    def test_unknown_scene(self, client):
        assert client.get("/api/scenes/nope/context").status_code == 404


class TestContinuityEndpoint:
    def test_clean_world(self, client):
        body = client.get("/api/continuity").json()
        assert body["rules_run"] >= 20
        assert not [v for v in body["violations"] if v["severity"] == "error"]

    def test_suppression_round_trip(self, client, renn):
        ghost = renn.add_entity("person", "Ghost", exists_from=renn.day(100),
                                exists_to=renn.day(150))
        renn.add_event("A haunting", start_day=renn.day(200),
                       participants=[(ghost.id, "participant")])

        before = client.get("/api/continuity").json()
        violation = next(v for v in before["violations"]
                         if v["rule_key"] == "dead_character_acts")

        assert client.post("/api/continuity/suppress", json={
            "rule_key": violation["rule_key"],
            "fingerprint": violation["fingerprint"],
            "reason": "He is a ghost.",
        }).status_code == 204

        after = client.get("/api/continuity").json()
        assert not [v for v in after["violations"]
                    if v["rule_key"] == "dead_character_acts"]
        assert after["suppressed"] == 1


class TestRouteEndpoint:
    def test_route(self, client, renn):
        gh = entity_named(client, "Greyhaven")
        rf = entity_named(client, "Rennford")
        body = client.get("/api/route", params={
            "origin_id": gh["id"], "destination_id": rf["id"], "profile": "horse",
        }).json()
        assert body["path_names"] == ["Greyhaven", "Red Ford", "Millbrook", "Rennford"]
        assert 3 < body["days"] < 4
        assert body["explanation"]

    def test_no_route_explains_why(self, client, renn):
        gh = entity_named(client, "Greyhaven")
        rf = entity_named(client, "Rennford")
        response = client.get("/api/route", params={
            "origin_id": gh["id"], "destination_id": rf["id"],
            "profile": "barge", "day": renn.day(241, 1, 5),
        })
        assert response.status_code == 404
        assert "season" in response.json()["detail"]

    def test_bad_profile(self, client):
        gh = entity_named(client, "Greyhaven")
        rf = entity_named(client, "Rennford")
        response = client.get("/api/route", params={
            "origin_id": gh["id"], "destination_id": rf["id"], "profile": "dragon",
        })
        assert response.status_code == 400


class TestAnalysisEndpoints:
    def test_why_it_matters(self, client):
        """§51, and §67's requirement that conclusions carry their reasoning."""
        gh = entity_named(client, "Greyhaven")
        body = client.get(f"/api/why/{gh['id']}").json()
        assert body["findings"]
        assert all(f["evidence"] for f in body["findings"])
        text = " ".join(f["text"] for f in body["findings"])
        assert "claimed by" in text or "divided" in text

    def test_why_a_person_matters(self, client):
        oren = entity_named(client, "Prince Oren")
        body = client.get(f"/api/why/{oren['id']}").json()
        text = " ".join(f["text"] for f in body["findings"])
        assert "in line for" in text
        assert "Vulnerable" in text

    def test_what_if_removed(self, client):
        """§52/§85."""
        gh = entity_named(client, "Greyhaven")
        body = client.get(f"/api/impact/{gh['id']}").json()
        assert body["consequences"]
        assert all(c["evidence"] for c in body["consequences"])
        assert "not canon" in body["note"]

    def test_removing_a_person_reshuffles_succession(self, client):
        oren = entity_named(client, "Prince Oren")
        body = client.get(f"/api/impact/{oren['id']}").json()
        text = " ".join(c["text"] for c in body["consequences"])
        assert "Succession to King of Renn would become" in text
        assert "Lady Elia" in text


class TestEventsEndpoint:
    def test_events_and_participants(self, client):
        body = client.get("/api/events").json()
        war = next(e for e in body if e["name"] == "The Red War")
        assert war["participants"]
        assert war["date_text"]

    def test_causal_chain(self, client):
        """§32's consequence explorer."""
        events = client.get("/api/events").json()
        tolls = next(e for e in events if "Iron Toll" in e["name"])
        chain = client.get(f"/api/events/{tolls['id']}/consequences").json()
        names = [c["name"] for c in chain]
        assert "The Red War" in names
        assert "The Battle of Red Ford" in names
        assert chain == sorted(chain, key=lambda c: c["depth"])


class TestSecretsEndpoint:
    def test_layered_knowledge(self, client):
        """§6: knows / believes / suspects / misinformed are separate answers."""
        body = client.get("/api/secrets").json()
        secret = body[0]
        assert secret["about"]["name"] == "Prince Oren"
        stances = secret["by_stance"]
        assert {"knows", "suspects", "misinformed", "unaware"} <= set(stances)
        assert {p["name"] for p in stances["knows"]} >= {"Lady Mara", "Queen Sera"}
        assert stances["misinformed"][0]["name"] == "Prince Oren"
        # the second-order layer
        assert any(p["about"] for p in stances["knows"])


class TestPatchClearsDates:
    def test_an_explicit_null_clears_a_date(self, client):
        """The edit form promises 'leave the year blank for always'; exclude_none was
        silently discarding exactly that request, so a death could never be undone."""
        created = client.post("/api/entities", json={
            "type_key": "person", "name": "Undying", "exists_to": 500,
        }).json()
        patched = client.patch(f"/api/entities/{created['id']}",
                               json={"exists_to": None}).json()
        assert patched["exists_to"] is None

    def test_an_omitted_field_stays_untouched(self, client):
        created = client.post("/api/entities", json={
            "type_key": "person", "name": "Dated", "exists_from": 100, "exists_to": 500,
        }).json()
        patched = client.patch(f"/api/entities/{created['id']}",
                               json={"name": "Renamed"}).json()
        assert patched["exists_from"] == 100
        assert patched["exists_to"] == 500

    def test_null_never_reaches_a_not_null_column(self, client):
        created = client.post("/api/entities", json={
            "type_key": "person", "name": "Named",
        }).json()
        response = client.patch(f"/api/entities/{created['id']}", json={"name": None})
        assert response.status_code == 200
        assert response.json()["name"] == "Named"

    def test_ending_before_the_beginning_is_a_400(self, client, renn):
        a = entity_named(client, "House Marr")
        b = entity_named(client, "Blackmere")
        fact = client.post("/api/facts", json={
            "subject_id": a["id"], "predicate_key": "claims",
            "object_id": b["id"], "valid_from": renn.day(300),
        }).json()
        response = client.post(f"/api/facts/{fact['id']}/end",
                               params={"on_day": renn.day(200)})
        assert response.status_code == 400
        assert "before it began" in response.json()["detail"]
