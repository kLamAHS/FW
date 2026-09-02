"""What the writer drew themselves (§66).

The whole generator is built around one principle: what the writer drew is truth, and
the map builds around it rather than over it. `_authored_outlines` refuses to redraw a
region they outlined, `coast.py` grows the continent to fit their borders,
`_castles_the_writer_drew` pins their keeps where they put them, and the retirement
sweep is careful never to touch a shape it did not make.

None of that had a way in. `add_geometry` and `delete_geometry` had no route and the
client had no drawing tool, so every one of those paths was reachable only for the
seeded example world — the map honoured a drawing nobody could make.

What is asserted here is the property, not the plumbing: a border the writer draws
changes the continent the map grows, and survives every later run of it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fw.api.app import create_app
from fw.core.calendar.kernel import GREGORIAN
from fw.core.mapgen import ledger
from fw.core.mapgen.apply import apply_plan
from fw.core.mapgen.decide import Decision, DecisionSet
from fw.core.mapgen.pipeline import plan_map
from fw.core.mapgen.plan import MapBrief
from fw.core.world import World

SPECS = [
    ("The Iron Spine", "mountains and high crags"),
    ("The Sunlit Coast", "coast and harbour"),
    ("Greenhollow", "forest and river valley"),
]


def build() -> tuple[World, dict[str, str]]:
    world = World.create(name="Ashmere", calendar=GREGORIAN)
    ids: dict[str, str] = {}
    for name, terrain in SPECS:
        region = world.add_entity("region", name)
        world.assert_fact(region, "terrain", value=terrain)
        world.assert_fact(region, "population", value="60000")
        ids[name] = region.id
    world.assert_fact(ids["The Iron Spine"], "borders", ids["Greenhollow"])
    world.assert_fact(ids["The Sunlit Coast"], "borders", ids["Greenhollow"])
    return world, ids


@pytest.fixture
def world():
    made, _ids = build()
    yield made
    made.close()


@pytest.fixture
def ids(world):
    return {e.name: e.id for e in world.entities("region")}


@pytest.fixture
def client(world):
    return TestClient(create_app(world))


BORDER = [[[60, 60], [300, 60], [300, 300], [60, 300]]]


def coastline(plan) -> list:
    return next(f for f in plan.features if f.kind == "coast").shapes[0].coordinates[0]


class TestAWriterCanDraw:
    def test_a_polygon_a_line_and_a_point(self, client, ids):
        for kind, layer, shape in (("polygon", "regions", BORDER),
                                   ("line", "roads", [[0, 0], [50, 50]]),
                                   ("point", "settlements", [5, 5])):
            answer = client.post("/api/geometry", json={
                "entity_id": ids["Greenhollow"], "kind": kind,
                "layer": layer, "coordinates": shape})
            assert answer.status_code == 201, answer.json()

    def test_a_polygon_is_closed_for_them(self, client, ids, world):
        """A writer clicking the last corner has finished the shape.

        Asking them to click the first one again is asking them to know how a polygon
        is stored.
        """
        client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "polygon",
            "layer": "regions", "coordinates": BORDER})
        ring = world.geometries(layer="regions")[0].coordinates[0]
        assert len(ring) == 5 and ring[0] == ring[-1]

    def test_a_shape_can_be_rubbed_out_again(self, client, ids, world):
        made = client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "point",
            "layer": "settlements", "coordinates": [5, 5]}).json()
        assert client.delete(f"/api/geometry/{made['id']}").status_code == 204
        assert not world.geometries(layer="settlements")

    def test_drawing_undoes_like_everything_else(self, client, ids, world):
        client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "polygon",
            "layer": "regions", "coordinates": BORDER})
        assert world.geometries(layer="regions")
        world.undo()
        assert not world.geometries(layer="regions")


class TestTheMapBuildsAroundIt:
    def test_a_border_the_writer_drew_reshapes_the_continent(self, client, ids, world):
        """The property the whole thing exists for, and the one nothing could reach."""
        before = coastline(plan_map(world, MapBrief()))
        client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "polygon",
            "layer": "regions", "coordinates": BORDER})
        after = coastline(plan_map(world, MapBrief()))
        assert before != after, "the writer drew a border and the land ignored it"

    def test_the_map_draws_the_country_their_ring_claims(self, client, ids, world):
        """It used to skip the region entirely, and that cost the map its shape.

        The rule was "the writer drew it; it is not ours to redraw", and it meant an
        authored region got no traced outline and no shared border arcs — so the map
        fell back to stroking the raw ring, and the seeded world came out as three
        quadrilaterals lying across a coastline. A reader looked at it and said so.

        `coast._hold` already argues the other reading, about those very provinces: a
        ring dragged round a country says WHERE THE COUNTRY IS, not where its coast
        runs, and honouring it literally is over-reading it. The terrain layer has
        always spread that claim into a swell rather than tracing the pencil. This is
        the same reading carried through to the ink.

        §66 is untouched and the three tests below check it: the ring is not moved,
        not restyled, not swept away by a regeneration, and never mistaken for the
        map's own work. What changed is only which of the two shapes gets inked.
        """
        client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "polygon",
            "layer": "regions", "coordinates": BORDER})
        plan = plan_map(world, MapBrief())
        drawn = {f.name for f in plan.features if f.kind == "region"}
        assert {"Greenhollow", "The Iron Spine", "The Sunlit Coast"} <= drawn
        theirs = next(f for f in plan.features
                      if f.kind == "region" and f.name == "Greenhollow")
        ring = next(s for s in theirs.shapes if s.kind == "polygon")
        # Against the RING's own point count, not `len(BORDER)` — that is a list of
        # rings, so it is 1, and comparing against it would pass for any shape at all.
        assert len(ring.coordinates[0]) > len(BORDER[0]) + 1, (
            f"the traced territory has {len(ring.coordinates[0])} points, no richer "
            f"than the {len(BORDER[0]) + 1}-point ring it came from")
        assert ring.style.get("edge") == "none", (
            "the plate strokes its own ring, so every frontier is drawn twice")

    def test_the_writers_ring_still_reaches_the_client(self, client, ids, world):
        """Hiding it from the picture must not hide it from the writer.

        MapView builds its "Drawn by you" panel from `features.filter(f =>
        !f.generated)`, and that panel is the only place in the client that lists a
        writer's own shape and the only one carrying its "rub out" button. Dropping
        the ring from the payload — which is the tidier-looking way to stop drawing
        two outlines per country — takes away their ability to see or delete the thing
        they drew. So it stays on the wire, paired with the plate instead.
        """
        client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "polygon",
            "layer": "regions", "coordinates": BORDER})
        plan = plan_map(world, MapBrief())
        apply_plan(world, plan, DecisionSet(plan_id=plan.plan_id, decisions=tuple(
            Decision(feature_id=f.id, accept=True) for f in plan.features)))
        served = client.get("/api/map").json()["features"]
        rings = [f for f in served if not f["generated"] and f["kind"] == "polygon"]
        assert rings, "the writer's own ring never reached the client"
        ring = rings[0]
        assert ring["superseded_by"], "the ring is not paired with its plate"
        plate = next(f for f in served if f["id"] == ring["superseded_by"])
        assert plate["generated"] and plate["traced_from"] == ring["id"]

    def test_their_shape_is_never_taken_for_the_map_s_own(self, client, ids, world):
        """`ledger.is_generated` is what a regeneration retires by."""
        client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "polygon",
            "layer": "regions", "coordinates": BORDER})
        drawn = world.geometries(layer="regions")[0]
        assert not ledger.is_generated(drawn)
        assert not drawn.props, "a drawn shape came back carrying provenance"

    def test_a_regeneration_never_sweeps_it_away(self, client, ids, world):
        """A writer whose coastline vanished on the second run would never trust it."""
        client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "polygon",
            "layer": "regions", "coordinates": BORDER})
        theirs = world.geometries(layer="regions")[0].id
        for _ in range(2):
            plan = plan_map(world, MapBrief(invent_settlements=True))
            assert theirs not in {r.feature_id for r in plan.retiring}
            apply_plan(world, plan, DecisionSet(plan_id=plan.plan_id, decisions=tuple(
                Decision(feature_id=f.id, accept=True) for f in plan.features)))
        assert any(g.id == theirs for g in world.geometries(layer="regions"))


class TestItRefusesAShapeItCannotDraw:
    def test_a_corner_that_is_not_two_numbers(self, client, ids):
        answer = client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "line",
            "layer": "roads", "coordinates": [[0, 0], ["east", 3]]})
        assert answer.status_code == 422
        assert "not two ordinary numbers" in answer.json()["detail"]

    def test_a_corner_that_is_not_a_finite_number(self, client, ids):
        """NaN reaches the R*Tree, which stores it and then answers wrongly forever.

        Strict JSON has no `NaN`, so a normal encoder cannot even send this — but
        Python's parser accepts it, so a raw body can, and the corruption it causes has
        no error message anywhere near its cause.
        """
        raw = ('{"entity_id": "' + ids["Greenhollow"] + '", "kind": "point", '
               '"layer": "settlements", "coordinates": [NaN, 5]}')
        answer = client.post("/api/geometry", content=raw,
                             headers={"Content-Type": "application/json"})
        assert answer.status_code == 422

    def test_a_polygon_of_one_corner(self, client, ids):
        answer = client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "polygon",
            "layer": "regions", "coordinates": [[[1, 1]]]})
        assert "at least 3 points" in answer.json()["detail"]

    def test_a_layer_nobody_paints(self, client, ids):
        answer = client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "point",
            "layer": "the moon", "coordinates": [1, 1]})
        assert answer.status_code == 422
        assert "regions" in answer.json()["detail"], "it should say what the layers are"

    def test_a_kind_that_is_not_a_shape(self, client, ids):
        answer = client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "blob",
            "layer": "regions", "coordinates": [1, 1]})
        assert answer.status_code == 422

    def test_a_shape_for_nobody(self, client):
        answer = client.post("/api/geometry", json={
            "entity_id": "nobody", "kind": "point",
            "layer": "settlements", "coordinates": [1, 1]})
        assert answer.status_code == 404

    def test_provenance_is_not_the_client_s_to_set(self, client, ids, world):
        """A client that could stamp a shape as generated could have the next run
        sweep away the writer's own coastline."""
        client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "polygon",
            "layer": "regions", "coordinates": BORDER,
            "props": {"mapgen": {"gen": "mapgen/2", "feature": "rgn_forged"}}})
        drawn = world.geometries(layer="regions")[0]
        assert not drawn.props and not ledger.is_generated(drawn)

    def test_a_shape_with_more_corners_than_anyone_draws(self, client, ids):
        answer = client.post("/api/geometry", json={
            "entity_id": ids["Greenhollow"], "kind": "line", "layer": "roads",
            "coordinates": [[n, n] for n in range(5000)]})
        assert answer.status_code == 422


class TestTheGroundSurvivesTheRoundTrip:
    """A map accepted through the application, not through Python.

    `apply_plan` stores the heightfield the accepted map was drawn from, and the client
    renders it as the lit ground under everything. But the plan crosses to the browser
    and back as JSON, and the terrain — three quarters of a megabyte the client never
    reads — is not on that wire, so every map the *application* accepted arrived with no
    surface under it and drew flat. Only worlds built from Python had mountains, which
    is why the seeded demo never showed it.
    """

    def test_a_map_accepted_over_the_wire_still_has_ground(self, client, world):
        plan = client.post("/api/map/plan",
                           json={"invent_settlements": True}).json()
        assert "terrain" not in plan, "the heightfield is on the wire after all"
        client.post("/api/map/apply", json={
            "plan": plan,
            "decisions": [{"feature_id": f["id"], "accept": True}
                          for f in plan["features"]]})
        assert world.terrain() is not None, "the map was accepted onto no ground"
        assert client.get("/api/map/relief").json()["available"]

    def test_the_lit_ground_can_actually_be_drawn(self, client):
        plan = client.post("/api/map/plan", json={}).json()
        client.post("/api/map/apply", json={
            "plan": plan,
            "decisions": [{"feature_id": f["id"], "accept": True}
                          for f in plan["features"]]})
        picture = client.get("/api/map/relief.png", params={"scale": 2})
        assert picture.status_code == 200
        assert picture.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_the_ground_is_the_one_the_writer_looked_at(self, client, world):
        """Not merely *a* surface: the surface of the plan they accepted.

        The world can move between proposing and accepting, and the towns in the plan
        stand where they do because of the mountains it was drawn over. The ground kept
        is the proposal's own, not a fresh one.
        """
        plan = client.post("/api/map/plan", json={}).json()
        proposed = plan_map(world, MapBrief.from_dict(plan["brief"])).terrain
        world.add_entity("region", "Latecomer")
        client.post("/api/map/apply", json={
            "plan": plan,
            "decisions": [{"feature_id": f["id"], "accept": True}
                          for f in plan["features"]]})
        # Compared with a tolerance: the store packs the fields down for size, so a
        # stored height is the proposal's to about a millionth and never exactly.
        kept = world.terrain()["fields"]["elevation"]
        # strict: two grids of different shapes are not the same surface at all.
        drift = max(abs(a - b)
                    for ra, rb in zip(kept, proposed.fields["elevation"], strict=True)
                    for a, b in zip(ra, rb, strict=True))
        assert drift < 1e-4, f"the ground moved by {drift}, so it is another map's"

    def test_ground_is_never_taken_from_a_different_map(self, client, world):
        """The fallback recomputes, and a recomputed map can be another map entirely.

        The server keeps one proposal's surface, so a writer working in two worlds at
        once falls back to working it out again — and if the world moved in between,
        what comes back belongs to a map nobody was shown. Putting the accepted towns
        on it would move the mountains out from under them.
        """
        plan = client.post("/api/map/plan", json={}).json()

        elsewhere, _ids = build()                 # evicts the remembered surface
        TestClient(create_app(elsewhere)).post("/api/map/plan", json={})

        world.add_entity("region", "Latecomer")
        world.assert_fact(world.entity_named("Latecomer").id, "terrain",
                          value="desert and salt flats")
        client.post("/api/map/apply", json={
            "plan": plan,
            "decisions": [{"feature_id": f["id"], "accept": True}
                          for f in plan["features"]]})
        elsewhere.close()
        assert world.terrain() is None, "the map was given a surface from another map"
