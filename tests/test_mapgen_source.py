"""Reading the writer's world — the stage everything else was supposed to depend on.

The map used to read seven of the world's thirty-four entity types and ten of its
ninety-three predicates, and it rediscovered even that six separate times per plan. House
Marr was `based_in` Northwatch and the stage that puts castles somewhere had no idea whose
seat it was; the Iron Road existed as a road entity with `connects` facts and the stage
that lays roads had never read it.

What is asserted here is mostly that the writer's own material now arrives: whose hall a
town is, who holds a title over a region, which places a road joins, where a battle
happened, when a town was founded. And three properties that make the reading safe to
build on — it writes nothing, it is the same for two identical worlds, and no entity id
escapes it.
"""

from __future__ import annotations

import collections

import pytest

from fw.core.mapgen import source
from fw.core.mapgen.source import scan
from fw.core.mapgen.source.claims import Basis, Claim, settle, unstated
from fw.core.seed.renn import seed_renn


@pytest.fixture(scope="module")
def world():
    made = seed_renn()
    yield made
    made.close()


@pytest.fixture(scope="module")
def reading(world):
    return source.read_world(world, at=world.day(240))


class TestTheLadderIsTheConflictPolicy:
    def test_the_higher_rung_wins(self):
        got = settle([Claim("plain", Basis.SUMMARY, "a passing mention"),
                      Claim("mountain", Basis.TOKEN, "you set it")], fallback="?")
        assert got.value == "mountain" and got.basis is Basis.TOKEN

    def test_a_reading_keeps_the_claims_it_beat(self):
        got = settle([Claim("plain", Basis.SUMMARY, "a"), Claim("mountain", Basis.TOKEN, "b")],
                     fallback="?")
        assert [c.value for c in got.claims] == ["mountain", "plain"]

    def test_two_claims_on_one_rung_that_disagree_are_contested(self):
        got = settle([Claim("marsh", Basis.PROSE_PROP, "a"),
                      Claim("desert", Basis.PROSE_PROP, "b")], fallback="?")
        assert got.contested, "the writer said two things and the map averaged them"

    def test_agreeing_claims_are_not_contested(self):
        got = settle([Claim("marsh", Basis.PROSE_PROP, "a"),
                      Claim("marsh", Basis.PROSE_PROP, "b")], fallback="?")
        assert not got.contested

    def test_nothing_written_is_a_usable_default_that_says_so(self):
        got = unstated(0.5)
        assert got.value == 0.5 and got.is_default and not got.stated

    def test_settling_does_not_depend_on_the_order_they_arrived(self):
        pair = [Claim("a", Basis.SUMMARY, "x", order=1),
                Claim("b", Basis.SUMMARY, "y", order=0)]
        assert settle(pair, fallback="?").value == settle(
            list(reversed(pair)), fallback="?").value


class TestReadingTheirSentences:
    def test_the_only_pass_is_a_constraint_not_a_description(self):
        found = scan.scan("The only pass over the Kingsback.", "x")
        assert found and found[0].head == "pass" and found[0].exclusive

    def test_a_named_river_is_a_name_and_a_bare_one_is_not(self):
        named = scan.scan("The River Renn rises in the north.", "x")
        bare = scan.scan("A river runs through it.", "x")
        assert [m.proper_name for m in named] == ["The River Renn"]
        assert [m.proper_name for m in bare] == [None]

    def test_a_capital_that_only_opens_a_sentence_names_nothing(self):
        """Otherwise every description beginning with an adjective invents a place."""
        found = scan.scan("Sheltered anchorage at the mouth of the Renn.", "x")
        assert [m.proper_name for m in found if m.proper_name] == []

    def test_a_name_that_opens_a_sentence_is_still_a_name(self):
        found = scan.scan("Redwater Ford is the only crossing for forty leagues.", "x")
        assert "Redwater Ford" in [m.proper_name for m in found]

    def test_a_sentence_ties_a_landform_to_the_places_it_names(self):
        found = scan.scan("The pass above Northwatch guards the road north.", "x",
                          gazetteer=frozenset({"northwatch"}))
        passes = [m for m in found if m.head == "pass"]
        assert passes and passes[0].anchors == ("northwatch",)
        assert passes[0].relation == "guards"

    def test_a_word_outside_the_lexicon_claims_nothing(self):
        assert scan.scan("Amber and lacquer and quiet ambition.", "x") == ()

    def test_mentions_come_back_in_the_order_they_were_written(self):
        found = scan.scan("A marsh below, and beyond it a range.", "x")
        assert [m.head for m in found] == ["marsh", "range"]


class TestTheBorderGraph:
    def test_the_middle_of_a_chain_is_a_neck(self, reading):
        """An articulation point is a province whose loss cuts the kingdom in two."""
        assert reading.borders.articulation == ("region/the-vale-of-renn",)

    def test_a_region_knows_what_shape_it_plays(self, reading):
        roles = {r.key: r.shape_role for r in reading.regions}
        assert roles["region/the-vale-of-renn"] == "neck"
        assert set(roles.values()) <= {"core", "neck", "cape", "island"}

    def test_an_impossible_border_graph_is_reported_and_not_crashed(self):
        from fw.core.mapgen.source import graph

        five = [f"region/r{n}" for n in range(5)]
        pairs = {(a, b) for a in five for b in five if a < b}
        built = graph.build(five, pairs)
        assert not built.planar_possible, "K5 cannot be drawn on a plane"

    def test_a_long_chain_does_not_blow_the_stack(self):
        from fw.core.mapgen.source import graph

        keys = [f"region/{n:04d}" for n in range(600)]
        pairs = {(keys[n], keys[n + 1]) for n in range(len(keys) - 1)}
        built = graph.build(keys, pairs)
        assert len(built.articulation) == len(keys) - 2


class TestTheWritersOwnMaterialArrives:
    def test_a_town_knows_whose_seat_it_is(self, reading):
        """`based_in` has been in the world all along and the map never read it."""
        seated = {s.key: reading.seat_of(s.key) for s in reading.settlements}
        named = {k: v.name for k, v in seated.items() if v}
        assert named, "nobody's hall is anywhere"
        assert named.get("settlement/northwatch") == "House Dray"
        assert named.get("settlement/rennford") == "House Veyne"

    def test_a_region_knows_who_holds_it(self, reading):
        warden = reading.holder_of("region/the-northmarch")
        assert warden is not None, "the Warden of the Northmarch holds nothing"
        assert warden.name == "Warden of the Northmarch"
        assert warden.holder_key and warden.holder_key.startswith("person/")

    def test_a_road_knows_which_towns_it_joins(self, reading):
        joins = {r.key: r.endpoint_keys for r in reading.routes}
        assert joins.get("road/the-iron-road") == (
            "settlement/greyhaven", "settlement/rennford")
        assert any(r.kind == "trade_route" for r in reading.routes), "no trade routes"

    def test_a_river_knows_the_towns_it_runs_through_in_order(self, reading):
        renn = next(w for w in reading.waters if w.key == "waterway/the-river-renn")
        assert renn.through_keys == ("settlement/rennford", "settlement/millbrook",
                                     "settlement/red-ford")

    def test_a_battle_knows_where_it_was_fought(self, reading):
        fought = {e.key: e.place_key for e in reading.events if e.destructive}
        assert fought.get("event/the-battle-of-red-ford") == "settlement/red-ford"

    def test_a_treaty_is_not_destructive_because_it_ended_a_war(self, reading):
        peace = next(e for e in reading.events if e.key == "event/the-peace-of-millbrook")
        assert not peace.destructive

    def test_a_town_knows_when_it_was_founded(self, reading):
        founded = {s.key: s.founded for s in reading.settlements}
        assert all(v is not None for v in founded.values()), founded

    def test_a_house_knows_how_far_below_the_crown_it_sits(self, reading):
        depth = {h.key: h.depth for h in reading.houses}
        assert depth.get("house/house-veyne") == 1
        assert depth.get("house/house-marr") == 2

    def test_a_resource_is_read_as_a_kind_the_ground_can_carry(self, reading):
        kinds = {r.word: r.kind for r in reading.resources}
        assert kinds.get("iron") == "ore" and kinds.get("grain") == "arable"
        assert kinds.get("salt") == "stone", "the Salt Reach's own resource"

    def test_a_coast_is_a_matter_of_degree_and_not_a_flag(self, reading):
        facing = {r.key: r.sea_facing.value for r in reading.regions}
        assert facing["region/the-salt-reach"] > 0.5
        assert facing["region/the-vale-of-renn"] == 0.0
        assert all(0.0 <= v <= 1.0 for v in facing.values())


class TestItSaysWhatItNoticed:
    def test_a_port_in_a_landlocked_country_is_reported(self, reading):
        said = " ".join(f.message for f in reading.findings)
        assert "Greyhaven" in said and "Northmarch" in said, said

    def test_a_finding_quotes_the_writer(self, reading):
        for finding in reading.findings:
            assert finding.message and finding.subjects

    def test_every_value_can_say_where_it_came_from(self, reading):
        for region in reading.regions:
            for what in (region.terrain_mix, region.temperature, region.moisture,
                         region.population, region.sea_facing):
                assert what.because, f"{region.key} cannot explain one of its values"


class TestTheReadingIsSafeToBuildOn:
    def test_reading_the_world_writes_nothing(self, world):
        seen: list[str] = []
        world.db.conn.set_trace_callback(
            lambda sql: seen.append(sql.strip().split()[0].upper()))
        try:
            source.read_world(world, at=world.day(240))
        finally:
            world.db.conn.set_trace_callback(None)
        assert not [q for q in seen if q in ("INSERT", "UPDATE", "DELETE")]

    def test_it_reads_the_world_in_a_handful_of_queries(self, world):
        """Six separate traversals before this, and the answer kept nowhere."""
        seen: list[str] = []
        world.db.conn.set_trace_callback(lambda sql: seen.append(sql))
        try:
            source.read_world(world, at=world.day(240))
        finally:
            world.db.conn.set_trace_callback(None)
        assert len(seen) <= 16, f"{len(seen)} statements: {collections.Counter(seen)}"

    def test_two_identically_built_worlds_read_the_same(self):
        one, two = seed_renn(), seed_renn()
        try:
            assert not ({e.id for e in one.entities()} & {e.id for e in two.entities()})
            first = source.read_world(one, at=one.day(240))
            second = source.read_world(two, at=two.day(240))
            assert first.fingerprint() == second.fingerprint()
        finally:
            one.close()
            two.close()

    def test_no_entity_id_reaches_the_fingerprint(self, world, reading):
        mark = reading.fingerprint()
        for entity in world.entities():
            assert entity.id not in mark

    def test_reading_it_twice_gives_the_same_answer(self, world):
        first = source.read_world(world, at=world.day(240))
        second = source.read_world(world, at=world.day(240))
        assert first.fingerprint() == second.fingerprint()

    def test_every_collection_is_ordered(self, reading):
        assert [r.key for r in reading.regions] == sorted(r.key for r in reading.regions)
        assert [s.key for s in reading.settlements] == sorted(
            s.key for s in reading.settlements)
        for house in reading.houses:
            assert list(house.holds_keys) == sorted(house.holds_keys)

    def test_a_map_of_an_early_year_holds_only_what_stood_then(self, world):
        early = source.read_world(world, at=world.day(100))
        late = source.read_world(world, at=world.day(240))
        assert len(early.settlements) < len(late.settlements)
        assert {s.key for s in early.settlements} <= {s.key for s in late.settlements}

    def test_the_map_does_not_read_its_own_work_back(self, world):
        """An entity the map made is not source material for the next map."""
        made = world.add_entity("settlement", "Mapmade", confidence="speculative",
                                tags=["generated-map"])
        try:
            reading = source.read_world(world, at=world.day(240))
            assert made.id not in {s.entity_id for s in reading.settlements}
        finally:
            world.delete_entity(made.id)
