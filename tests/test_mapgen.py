"""Growing a map from region attributes (§34, §66, §67, §92)."""

from __future__ import annotations

import pytest

from fw.core.calendar.kernel import GREGORIAN
from fw.core.mapgen.attributes import profile_region, read_climate, read_terrain
from fw.core.mapgen.generate import (
    CELL,
    GENERATED,
    GENERATOR,
    MIN_SPACING_CELLS,
    MapGenerator,
    generate_map,
)
from fw.core.seed.renn import PRESENT_YEAR
from fw.core.world import World, WorldError


def build_world(name: str = "Ashmere") -> World:
    """A world with regions described in prose and nothing drawn — the common case."""
    w = World.create(name=name, calendar=GREGORIAN)
    specs = [
        ("The Iron Spine", "mountains and high crags", "cold, heavy snow", "60000", "iron"),
        ("The Sunlit Coast", "coast and harbour", "warm and humid", "140000", "fish"),
        ("The Amber Steppe", "steppe and dry grassland", "hot, arid", "40000", "horses"),
        ("Greenhollow", "forest and river valley", "temperate, rain", "90000", "timber"),
    ]
    ids = {}
    for region_name, terrain, climate, population, resource in specs:
        region = w.add_entity("region", region_name)
        w.assert_fact(region, "terrain", value=terrain)
        w.assert_fact(region, "climate", value=climate)
        w.assert_fact(region, "population", value=population)
        w.assert_fact(region, "note", value=resource)
        ids[region_name] = region.id
    w.assert_fact(ids["The Iron Spine"], "borders", ids["Greenhollow"])
    w.assert_fact(ids["The Sunlit Coast"], "borders", ids["Greenhollow"])
    return w


@pytest.fixture
def blank() -> World:
    w = build_world()
    yield w
    w.close()


class TestReadingWhatTheWriterWrote:
    def test_prose_terrain_becomes_weighted_kinds(self):
        assert read_terrain("mountains and forest") == {"mountain": 1.0, "forest": 0.5}
        assert read_terrain("coast and marsh") == {"coast": 1.0, "marsh": 0.5}
        assert read_terrain("") == {}
        assert read_terrain("somewhere indescribable") == {}

    def test_prose_climate_becomes_temperature_and_moisture(self):
        temp, wet = read_climate("cold, heavy snow in Darkening")
        assert temp is not None and temp < -0.3
        hot, dry = read_climate("hot, arid")
        assert hot is not None and hot > 0.3
        assert dry is not None and dry < 0.2
        assert read_climate("") == (None, None)

    def test_a_profile_says_where_every_number_came_from(self, renn: World):
        profile = profile_region(renn, renn.entity_named("The Northmarch").id)
        assert profile.dominant == "mountain"
        assert profile.temperature < 0                      # "cold, heavy snow"
        assert "mountains and forest" in profile.why("terrain")
        assert "Iron" in profile.resources

    def test_an_undescribed_region_says_it_is_defaulting(self, blank: World):
        bare = blank.add_entity("region", "The Blank")
        profile = profile_region(blank, bare.id)
        assert profile.dominant == "plain"
        assert "nothing is recorded" in profile.why("terrain")


class TestGeneratedGeography:
    def test_a_world_of_prose_becomes_a_map(self, blank: World):
        report = generate_map(blank, seed="ashmere")
        assert len(report.regions_drawn) == 4
        assert report.rivers                                  # water found its way down
        assert report.placements
        assert report.roads
        assert "drew 4 regions" in report.summary()

    def test_rivers_never_run_uphill(self, blank: World):
        """Guaranteed by construction — depressions are filled before routing — and
        asserted here because it is the invariant a fictional map is judged on."""
        generator = MapGenerator(blank, seed="ashmere")
        generator.generate()
        for geometry in blank.geometries(layer="waterways"):
            heights = [generator.elevation[j][i]
                       for i, j in (generator._cell_of(x, y)
                                    for x, y in geometry.coordinates)]
            assert all(b <= a + 1e-6 for a, b in zip(heights, heights[1:], strict=False))

    def test_rivers_reach_water_and_are_worth_drawing(self, blank: World):
        generator = MapGenerator(blank, seed="ashmere")
        generator.generate()
        lines = blank.geometries(layer="waterways")
        assert lines
        for geometry in lines:
            assert len(geometry.coordinates) >= 4          # not a puddle

    def test_settlements_do_not_crowd_each_other(self, blank: World):
        report = generate_map(blank, seed="ashmere")
        spots = [(p.x, p.y) for p in report.placements]
        for a in range(len(spots)):
            for b in range(a + 1, len(spots)):
                gap = ((spots[a][0] - spots[b][0]) ** 2
                       + (spots[a][1] - spots[b][1]) ** 2) ** 0.5
                assert gap >= MIN_SPACING_CELLS * CELL - 1.0

    def test_region_outlines_are_closed_rings_the_client_can_draw(self, blank: World):
        generate_map(blank, seed="ashmere")
        for geometry in blank.geometries(layer="regions"):
            rings = geometry.coordinates
            assert isinstance(rings[0][0], list)      # polygon = array of RINGS
            assert rings[0][0] == rings[0][-1]        # closed, as every seeded ring is
            assert len(rings[0]) < 80                 # bounded: the client spreads these

    def test_terrain_actually_changes_the_outcome(self, blank: World):
        """A mountain region and a coast must not generate the same ground."""
        generator = MapGenerator(blank, seed="ashmere")
        generator.generate()
        by_name = {p.name: p for p in generator.profiles.values()}
        spine = by_name["The Iron Spine"]
        coast = by_name["The Sunlit Coast"]
        assert spine.base_elevation > coast.base_elevation + 0.3
        assert spine.roughness > coast.roughness
        assert spine.temperature < coast.temperature

    def test_a_world_with_no_regions_says_what_to_do(self):
        empty = World.create(name="Nothing", calendar=GREGORIAN)
        try:
            with pytest.raises(WorldError, match="a map grows from regions"):
                generate_map(empty)
        finally:
            empty.close()


class TestDeterminism:
    def test_the_same_world_generates_the_same_map(self):
        """Not 'similar' — identical. A golden coordinate test is worthless otherwise."""
        first, second = build_world(), build_world()
        try:
            a = generate_map(first, seed="fixed")
            b = generate_map(second, seed="fixed")
            assert [(p.name, p.x, p.y) for p in a.placements] == \
                   [(p.name, p.x, p.y) for p in b.placements]
            assert ([g.coordinates for g in first.geometries(layer="regions")]
                    == [g.coordinates for g in second.geometries(layer="regions")])
        finally:
            first.close()
            second.close()

    def test_a_different_seed_gives_a_different_map(self):
        first, second = build_world(), build_world()
        try:
            a = generate_map(first, seed="one")
            b = generate_map(second, seed="two")
            assert [(p.x, p.y) for p in a.placements] != [(p.x, p.y) for p in b.placements]
        finally:
            first.close()
            second.close()


class TestItProposesRatherThanOverwrites:
    def test_authored_geometry_is_never_touched(self, renn: World):
        """§66: what the writer drew is truth. The generator builds around it."""
        before = {g.entity_id: g.coordinates
                  for g in renn.geometries()
                  if not (g.style or {}).get(GENERATED)}
        generate_map(renn, at=renn.day(PRESENT_YEAR))
        after = {g.entity_id: g.coordinates
                 for g in renn.geometries()
                 if not (g.style or {}).get(GENERATED)}
        assert before == after

    def test_a_drawn_region_is_kept_and_reported_as_kept(self, renn: World):
        report = generate_map(renn, at=renn.day(PRESENT_YEAR))
        assert set(report.regions_kept) == {
            "The Northmarch", "The Vale of Renn", "The Salt Reach"}
        assert report.regions_drawn == []

    def test_regenerating_replaces_only_its_own_work(self, renn: World):
        day = renn.day(PRESENT_YEAR)
        authored = len([g for g in renn.geometries()
                        if not (g.style or {}).get(GENERATED)])
        generate_map(renn, at=day)
        first = len([g for g in renn.geometries()
                     if (g.style or {}).get(GENERATED) == GENERATOR])
        assert first > 0

        generate_map(renn, at=day)
        again = [g for g in renn.geometries() if (g.style or {}).get(GENERATED) == GENERATOR]
        still_authored = len([g for g in renn.geometries()
                              if not (g.style or {}).get(GENERATED)])
        # the last run's work was swept, not stacked; the writer's is untouched
        assert len(again) == first
        assert still_authored == authored

    def test_proposals_are_marked_as_guesses(self, blank: World):
        report = generate_map(blank, seed="ashmere")
        proposed = [p for p in report.placements if p.proposed]
        assert proposed
        for placement in proposed[:5]:
            entity = blank.get_entity(placement.entity_id)
            assert entity.confidence == "speculative"     # §57: never mistaken for canon
            assert "proposed" in entity.tags
            assert entity.summary.startswith(placement.name)

    def test_a_generated_border_is_drawn_as_uncertain(self, blank: World):
        """§92: fictional maps may be vague, and a computed border certainly is."""
        generate_map(blank, seed="ashmere")
        regions = blank.geometries(layer="regions")
        assert regions
        assert all(g.approximate for g in regions)

    def test_generation_can_be_declined(self, blank: World):
        report = generate_map(blank, seed="ashmere", propose_settlements=False)
        assert [p for p in report.placements if p.proposed] == []
        assert report.regions_drawn                      # the land is still drawn


class TestItIsOneUndoableAction:
    def test_a_whole_map_undoes_in_one_step(self, blank: World):
        """Hundreds of writes must be one action, or the writer's first Ctrl+Z gets a
        single polygon back and the other four hundred stay."""
        generate_map(blank, seed="ashmere")
        assert blank.geometries()

        blank.undo()
        assert blank.geometries() == []
        assert [e for e in blank.entities("settlement")] == []

        blank.redo()
        assert blank.geometries()


class TestExplanations:
    def test_every_placement_argues_for_itself(self, blank: World):
        report = generate_map(blank, seed="ashmere")
        for placement in report.placements:
            sentence = placement.because()
            assert sentence.startswith(placement.name)
            assert sentence.endswith(".")

    def test_the_reasons_name_the_region_s_own_resources(self, blank: World):
        report = generate_map(blank, seed="ashmere")
        spine = [p for p in report.placements
                 if "Iron Spine" in p.name]
        assert spine
        assert any("iron" in r for p in spine for r in p.reasons)

    def test_a_coast_gets_harbours_and_an_inland_plain_does_not(self, renn: World):
        """Authored intent wins over generated geography: a river plain is not a port."""
        report = generate_map(renn, at=renn.day(PRESENT_YEAR))
        vale = [p for p in report.placements if "Vale of Renn" in p.name]
        reach = [p for p in report.placements if "Salt Reach" in p.name]
        assert vale and reach
        assert not any("harbour" in r for p in vale for r in p.reasons)
        assert any("harbour" in r for p in reach for r in p.reasons)
