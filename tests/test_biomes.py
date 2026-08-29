"""What grows where, and which stretches of it are places in their own right."""

from __future__ import annotations

import pytest

from fw.core.calendar.kernel import GREGORIAN
from fw.core.geo.routing import LAND
from fw.core.mapgen import biome as biome_module
from fw.core.mapgen import features as features_module
from fw.core.mapgen.attributes import ROUTING_TERRAIN, TERRAIN_KINDS, profile_region
from fw.core.mapgen.generate import GRID, MapGenerator
from fw.core.mapgen.grid import Grid
from fw.core.world import World

DESCRIBED = [
    ("The Wolfswold", "deep forest and low hills", "cold, wet", "120000"),
    ("The Neck", "marsh, fen and bog", "damp, fog", "9000"),
    ("The Reach", "farmland, orchards and fields", "fair, mild", "420000"),
    ("The Ashwaste", "desert, dunes and badlands", "scorching, arid", "60000"),
    ("The Frostmarch", "mountains and high crags", "frozen, heavy snow", "80000"),
]
BORDERS = [("The Wolfswold", "The Neck"), ("The Neck", "The Reach"),
           ("The Reach", "The Ashwaste"), ("The Wolfswold", "The Frostmarch"),
           ("The Frostmarch", "The Reach")]


def world_of(regions, borders, name="Andalor") -> World:
    w = World.create(name=name, calendar=GREGORIAN)
    ids = {}
    for region_name, terrain, weather, population in regions:
        region = w.add_entity("region", region_name)
        w.assert_fact(region, "terrain", value=terrain)
        w.assert_fact(region, "climate", value=weather)
        w.assert_fact(region, "population", value=population)
        ids[region_name] = region.id
    for a, b in borders:
        w.assert_fact(ids[a], "borders", ids[b])
    return w


def grown(world: World, seed: str = "fixed") -> MapGenerator:
    generator = MapGenerator(world, seed=seed)
    regions = list(world.entities("region"))
    generator.profiles = {r.id: profile_region(world, r.id) for r in regions}
    authored = generator._authored_outlines()
    generator._build_landmass(authored)
    generator._assign_cells()
    generator._build_fields()
    generator._trace_rivers()
    generator._classify_ground()
    return generator


def share_in(generator, region_name: str) -> dict[str, float]:
    cells = generator.partition.cells_of(region_name)
    if not cells:
        return {}
    tally: dict[str, int] = {}
    for i, j in cells:
        kind = generator.biome.terrain(i, j)
        tally[kind] = tally.get(kind, 0) + 1
    return {k: v / len(cells) for k, v in tally.items()}


@pytest.fixture
def described() -> World:
    w = world_of(DESCRIBED, BORDERS)
    yield w
    w.close()


class TestTheTableItself:
    def test_every_temperature_and_moisture_lands_somewhere(self):
        """A gap in the table is a cell with no vegetation at all."""
        for t in range(-100, 101, 5):
            for m in range(0, 101, 5):
                kind = biome_module._from_weather(0.3, t / 100, m / 100, 0.10)
                assert kind in TERRAIN_KINDS

    def test_every_biome_is_ground_the_router_understands(self):
        """A road over a biome the travel engine has never heard of is a road nobody
        can use — the defect this whole vocabulary exists to prevent."""
        for kind in biome_module.KINDS:
            assert kind in ROUTING_TERRAIN
            assert ROUTING_TERRAIN[kind] in LAND

    def test_it_gets_colder_before_it_gets_icy(self):
        assert biome_module._from_weather(0.95, -0.9, 0.5, 0.10) == "glacier"
        assert biome_module._from_weather(0.95, -0.5, 0.5, 0.10) != "glacier"

    def test_dry_and_warm_is_desert_wherever_it_is(self):
        assert biome_module._from_weather(0.3, 0.5, 0.05, 0.10) == "desert"
        assert biome_module._from_weather(0.3, 0.0, 0.05, 0.10) == "desert"


class TestTheWriterWins:
    def test_a_region_comes_out_as_what_its_writer_called_it(self, described: World):
        generator = grown(described)
        for name, want in (("The Wolfswold", "forest"), ("The Neck", "marsh"),
                           ("The Reach", "farmland"), ("The Ashwaste", "desert"),
                           ("The Frostmarch", "mountain")):
            shares = share_in(generator, name)
            best = max(shares, key=lambda k: shares[k]) if shares else None
            assert best == want, f"{name}: said {want}, got {shares}"

    def test_ice_has_to_be_asked_for(self, described: World):
        """The most dramatic thing a biome can be must never arrive as the model's
        idea of variety inside a region the writer called forest."""
        generator = grown(described)
        assert share_in(generator, "The Wolfswold").get("glacier", 0.0) == 0.0

    def test_an_undescribed_region_takes_its_weather(self):
        w = world_of([("The Unsaid", "", "hot and parched", "50000"),
                      ("The Other", "plain", "temperate, rain", "50000")],
                     [("The Unsaid", "The Other")])
        try:
            shares = share_in(grown(w), "The Unsaid")
            assert shares
            dry = shares.get("desert", 0) + shares.get("steppe", 0)
            assert dry > 0.3, shares
        finally:
            w.close()

    def test_a_region_says_where_its_ground_came_from(self, described: World):
        generator = grown(described)
        assert "you described it as" in generator.biome.because["The Wolfswold"]


class TestNamedCountry:
    def test_a_wood_is_a_thing_with_a_shape(self, described: World):
        generator = grown(described)
        woods = [f for f in generator.features.features if f.kind == "forest"]
        assert woods
        for wood in woods:
            assert wood.rings
            for ring in wood.rings:
                assert ring[0] == ring[-1]
                assert 4 <= len(ring) <= 200

    def test_the_marsh_and_the_waste_are_found_too(self, described: World):
        kinds = {f.kind for f in grown(described).features.features}
        assert {"marsh", "waste"} <= kinds, kinds

    def test_speckle_is_not_a_place(self, described: World):
        for feature in grown(described).features.features:
            assert feature.area >= features_module.SMALLEST

    def test_two_patches_touching_at_a_corner_are_two_patches(self):
        grid = Grid(size=12, span=120.0)

        class Painted:
            def terrain(self, i, j):
                return "forest" if (i < 5 and j < 5) or (i >= 5 and j >= 5) else "plain"

        sea = [[False] * 12 for _ in range(12)]
        groups = features_module._components(grid, Painted(), "forest", sea, set())
        assert len(groups) == 2

    def test_a_river_breaks_a_wood_in_two(self):
        grid = Grid(size=12, span=120.0)

        class AllTrees:
            def terrain(self, i, j):
                return "forest"

        sea = [[False] * 12 for _ in range(12)]
        channel = {(i, 6) for i in range(12)}
        groups = features_module._components(grid, AllTrees(), "forest", sea, channel)
        assert len(groups) == 2

    def test_a_feature_says_why_it_is_there(self, described: World):
        for feature in grown(described).features.features:
            assert "unbroken" in feature.because


class TestAdoption:
    def test_a_wood_the_writer_named_is_adopted_not_duplicated(self, described: World):
        wolfswood = described.add_entity(
            "terrain_feature", "The Wolfswood",
            summary="Old forest, dark under the branches.")
        generator = grown(described)
        adopted = [f for f in generator.features.features
                   if f.entity_id == wolfswood.id]
        assert len(adopted) == 1
        assert adopted[0].name == "The Wolfswood"
        assert adopted[0].kind == "forest"

    def test_a_feature_with_nowhere_to_be_is_reported_not_invented(self):
        w = world_of([("The Sands", "desert and dunes", "scorching, arid", "20000"),
                      ("The Dust", "desert", "hot, parched", "20000")],
                     [("The Sands", "The Dust")])
        try:
            w.add_entity("terrain_feature", "The Drowned Marsh",
                         summary="A bog nobody crosses.")
            generator = grown(w)
            assert any("nowhere on this map" in n for n in generator.features.notes)
        finally:
            w.close()


class TestDeterminism:
    def test_the_same_world_grows_the_same_country(self):
        first, second = world_of(DESCRIBED, BORDERS), world_of(DESCRIBED, BORDERS)
        try:
            a, b = grown(first), grown(second)
            assert a.biome.codes == b.biome.codes
            assert ([(f.kind, f.area) for f in a.features.features]
                    == [(f.kind, f.area) for f in b.features.features])
        finally:
            first.close()
            second.close()

    def test_every_land_cell_has_a_kind(self, described: World):
        generator = grown(described)
        for j in range(GRID):
            for i in range(GRID):
                if not generator.sea[j][i]:
                    assert generator.biome.terrain(i, j) in TERRAIN_KINDS
