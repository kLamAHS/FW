"""One continent, contoured — and every acre of it owned (§34, §92)."""

from __future__ import annotations

import time

import pytest

from fw.core.calendar.kernel import GREGORIAN
from fw.core.mapgen import coast, territory
from fw.core.mapgen.attributes import profile_region
from fw.core.mapgen.generate import GRID, MapGenerator
from fw.core.world import World


def world_of(regions: list[tuple[str, str, str]],
             borders: list[tuple[str, str]], name: str = "Andalor") -> World:
    w = World.create(name=name, calendar=GREGORIAN)
    ids = {}
    for region_name, terrain, population in regions:
        region = w.add_entity("region", region_name)
        w.assert_fact(region, "terrain", value=terrain)
        w.assert_fact(region, "population", value=population)
        ids[region_name] = region.id
    for a, b in borders:
        w.assert_fact(ids[a], "borders", ids[b])
    return w


CHAIN = [("The Frostmarch", "mountains and crags", "80000"),
         ("The Wolfswold", "deep forest and hills", "120000"),
         ("The Neck", "marsh and fen", "9000"),
         ("The Riverlands", "river plain and meadow", "310000"),
         ("The Sunspear Coast", "coast and harbour", "240000")]
CHAIN_BORDERS = [("The Frostmarch", "The Wolfswold"), ("The Wolfswold", "The Neck"),
                 ("The Neck", "The Riverlands"),
                 ("The Riverlands", "The Sunspear Coast")]


def shaped(world: World, seed: str = "fixed") -> MapGenerator:
    generator = MapGenerator(world, seed=seed)
    regions = list(world.entities("region"))
    generator.profiles = {r.id: profile_region(world, r.id) for r in regions}
    authored = generator._authored_outlines()
    generator._build_landmass(authored)
    generator._assign_cells(regions, authored)
    return generator


@pytest.fixture
def chain() -> World:
    w = world_of(CHAIN, CHAIN_BORDERS)
    yield w
    w.close()


class TestOneContinent:
    def test_the_land_is_one_piece(self, chain: World):
        """A writer's borders are what tie a continent together. Land in pieces where
        they said two regions meet is not a map of their world."""
        generator = shaped(chain)
        assert generator.landform.notes == []
        assert len(generator.landform.coastlines()) >= 1

    def test_a_stated_border_is_realised_on_the_ground(self, chain: World):
        generator = shaped(chain)
        assert territory.audit(generator.partition, generator._border_pairs()) == []

    def test_a_coastline_is_not_a_circle(self, chain: World):
        """The point of contouring: a shape that can be concave. A star polygon cast
        from a centre cannot be, which is why the old maps had no bays."""
        generator = shaped(chain)
        ring = generator.landform.coastlines()[0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        middle = (sum(xs) / len(xs), sum(ys) / len(ys))
        reaches = sorted(((x - middle[0]) ** 2 + (y - middle[1]) ** 2) ** 0.5
                         for x, y in ring)
        assert reaches[-1] > reaches[len(reaches) // 2] * 1.5

    def test_no_region_is_left_offshore(self, chain: World):
        """A region whose heart drowned claims nothing and vanishes without a word."""
        generator = shaped(chain)
        for region_id, profile in generator.profiles.items():
            assert generator.partition.counts.get(profile.name, 0) > 0, profile.name
            assert generator._outline(region_id), profile.name

    def test_a_world_with_no_borders_is_still_one_world(self):
        w = world_of(CHAIN, [])
        try:
            assert shaped(w).landform.notes == []
        finally:
            w.close()

    def test_a_single_region_gets_a_map(self):
        w = world_of([("The Only Place", "plain", "10000")], [])
        try:
            generator = shaped(w)
            assert generator.landform.land_cells > 0
            assert generator._outline(list(generator.profiles)[0])
        finally:
            w.close()


class TestEveryAcreIsOwned:
    def test_no_land_is_left_unclaimed(self, chain: World):
        """Unclaimed land renders as grout between the regions, and a writer reads that
        as ground nobody has thought about."""
        generator = shaped(chain)
        for j in range(GRID):
            for i in range(GRID):
                if not generator.sea[j][i]:
                    assert generator.partition.owner[j][i] >= 0

    def test_no_region_owns_open_water(self, chain: World):
        generator = shaped(chain)
        for j in range(GRID):
            for i in range(GRID):
                if generator.sea[j][i]:
                    assert generator.partition.owner[j][i] == -1

    def test_a_bigger_population_holds_more_ground(self, chain: World):
        generator = shaped(chain)
        assert (generator.partition.counts["The Riverlands"]
                > generator.partition.counts["The Neck"])

    def test_the_shares_add_up(self, chain: World):
        generator = shaped(chain)
        total = sum(generator.partition.share(p.name)
                    for p in generator.profiles.values())
        assert abs(total - 1.0) < 0.02


class TestTheWritersDrawingWins:
    def test_a_drawn_region_keeps_its_own_ground(self, chain: World):
        region = chain.entity_named("The Riverlands")
        chain.add_geometry(region.id, "polygon",
                           [[[300, 300], [520, 300], [520, 520], [300, 520],
                             [300, 300]]], layer="regions")
        generator = shaped(chain)
        index = generator.partition.keys.index("The Riverlands")
        grid = generator._grid()
        inside = [grid.cell_of(x, y) for x, y in
                  ((340, 340), (400, 400), (480, 480))]
        held = [generator.partition.owner[j][i] for i, j in inside
                if not generator.sea[j][i]]
        assert held and all(owner == index for owner in held)


class TestDeterminism:
    def test_the_same_world_grows_the_same_continent(self):
        first, second = world_of(CHAIN, CHAIN_BORDERS), world_of(CHAIN, CHAIN_BORDERS)
        try:
            a, b = shaped(first), shaped(second)
            assert a.landform.coastlines() == b.landform.coastlines()
            assert a.partition.counts == b.partition.counts
        finally:
            first.close()
            second.close()

    def test_a_different_seed_grows_a_different_continent(self):
        w = world_of(CHAIN, CHAIN_BORDERS)
        try:
            assert (shaped(w, "one").landform.coastlines()
                    != shaped(w, "two").landform.coastlines())
        finally:
            w.close()


class TestCost:
    def test_a_dozen_regions_shape_inside_the_budget(self):
        regions = [(f"Region {n:02d}", "plain and hills", "90000") for n in range(12)]
        borders = [(regions[n][0], regions[n + 1][0]) for n in range(11)]
        borders += [(regions[n][0], regions[n + 3][0]) for n in range(9)]
        w = world_of(regions, borders)
        try:
            started = time.perf_counter()
            shaped(w)
            elapsed = time.perf_counter() - started
            assert elapsed < 3.0, f"shaping took {elapsed:.1f}s"
        finally:
            w.close()


class TestSettlingAnchors:
    def test_an_anchor_at_sea_moves_to_the_nearest_land(self, chain: World):
        generator = shaped(chain)
        form = generator.landform
        wet = next(((i, j) for j in range(GRID) for i in range(GRID)
                    if form.sea[j][i]), None)
        assert wet is not None
        moved = coast.settle_anchors(form, {"x": wet})["x"]
        assert not form.sea[moved[1]][moved[0]]
