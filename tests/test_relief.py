"""Mountains that are ranges, and rain that falls on one side of them."""

from __future__ import annotations

import statistics
import time

import pytest

from fw.core.calendar.kernel import GREGORIAN
from fw.core.mapgen.attributes import profile_region
from fw.core.mapgen.climate import dryness_across
from fw.core.mapgen.generate import GRID, MapGenerator
from fw.core.world import World

MOUNTAIN_WORLD = [
    ("The Iron Spine", "mountains, crags and high peaks", "cold, heavy snow", "70000"),
    ("The Riverlands", "river plain and meadow", "temperate, rain", "300000"),
    ("The Sunspear Coast", "coast and harbour", "warm and humid", "180000"),
    ("The Downs", "hills and downs", "mild", "120000"),
]
BORDERS = [("The Iron Spine", "The Riverlands"),
           ("The Riverlands", "The Sunspear Coast"),
           ("The Riverlands", "The Downs"),
           ("The Iron Spine", "The Downs")]


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


def shaped(world: World, seed: str = "fixed") -> MapGenerator:
    generator = MapGenerator(world, seed=seed)
    regions = list(world.entities("region"))
    generator.profiles = {r.id: profile_region(world, r.id) for r in regions}
    authored = generator._authored_outlines()
    generator._build_landmass(authored)
    generator._assign_cells(regions, authored)
    generator._build_fields()
    return generator


@pytest.fixture
def mountains() -> World:
    w = world_of(MOUNTAIN_WORLD, BORDERS)
    yield w
    w.close()


def spine_of(generator, key: str = "The Iron Spine"):
    return next(r for r in generator.relief.ranges if r.key == key)


def land_of(generator):
    return [(i, j) for j in range(GRID) for i in range(GRID)
            if not generator.sea[j][i]]


class TestRangesAreLinear:
    def test_high_ground_gets_a_range_and_crags_stand_taller_than_downs(
            self, mountains: World):
        generator = shaped(mountains)
        crests = {r.key: r.crest for r in generator.relief.ranges}
        assert "The Iron Spine" in crests
        assert "The Downs" in crests, "a range of downs is still a range"
        assert crests["The Iron Spine"] > crests["The Downs"] * 1.25

    def test_a_range_is_a_spine_and_not_a_blob(self, mountains: World):
        """The whole point. A raised region with noise on it reads as a bumpy plateau;
        real ranges run in a direction."""
        mountain = spine_of(shaped(mountains))
        assert mountain.elongation >= 1.8
        assert len(mountain.spine) >= 4

    def test_the_strike_follows_the_shape_of_the_ground(self, mountains: World):
        mountain = spine_of(shaped(mountains))
        assert mountain.strike.source == "drawn"
        assert "along the shape of" in mountain.strike.because

    def test_a_plain_gets_no_range(self):
        w = world_of([("The Flats", "plain and meadow", "mild", "100000"),
                      ("The Fields", "farmland", "fair", "100000")],
                     [("The Flats", "The Fields")])
        try:
            assert shaped(w).relief.ranges == ()
        finally:
            w.close()

    def test_the_ground_is_highest_on_the_spine(self, mountains: World):
        generator = shaped(mountains)
        mountain = spine_of(generator)
        middle = mountain.spine[len(mountain.spine) // 2]
        i, j = int(middle[0]), int(middle[1])
        crest = generator.elevation[j][i]
        far = [generator.elevation[b][a] for a, b in land_of(generator)
               if abs(a - i) + abs(b - j) > GRID * 0.25]
        assert crest > statistics.mean(far) * 1.4

    def test_foothills_fall_away_from_the_crest(self, mountains: World):
        generator = shaped(mountains)
        mountain = spine_of(generator)
        heights = []
        for step in (0, 4, 9, 15):
            middle = mountain.spine[len(mountain.spine) // 2]
            i = min(GRID - 1, int(middle[0]) + step)
            j = int(middle[1])
            if not generator.sea[j][i]:
                heights.append(generator.elevation[j][i])
        assert len(heights) >= 3
        assert heights[0] > heights[-1]


class TestNoSeamAtABorder:
    def test_a_border_leaves_no_terrace_in_the_ground(self, mountains: World):
        """The eye finds a straight edge in a landscape instantly, and a base height
        that steps at every border is the surest giveaway of a generated map."""
        generator = shaped(mountains)
        same, across = [], []
        base = generator.relief.base
        for j in range(1, GRID - 1):
            for i in range(1, GRID - 1):
                if generator.sea[j][i]:
                    continue
                for ni, nj in ((i + 1, j), (i, j + 1)):
                    if generator.sea[nj][ni]:
                        continue
                    step = abs(base[j][i] - base[nj][ni])
                    (same if generator.owner[j][i] == generator.owner[nj][ni]
                     else across).append(step)
        same.sort()
        across.sort()
        assert across[int(len(across) * 0.99)] <= same[int(len(same) * 0.99)] * 1.25

    def test_a_region_still_keeps_the_height_it_was_given(self, mountains: World):
        """Smoothing the seam must not flatten the world: enough blur to hide a border
        also halves the difference between a mountain march and a river plain."""
        generator = shaped(mountains)
        means: dict[int, list[float]] = {}
        for i, j in land_of(generator):
            means.setdefault(generator.owner[j][i], []).append(
                generator.relief.base[j][i])
        averages = sorted(statistics.mean(v) for v in means.values() if len(v) > 20)
        assert averages[-1] - averages[0] > 0.15

    def test_the_ground_never_cliffs(self, mountains: World):
        generator = shaped(mountains)
        steps = []
        for i, j in land_of(generator):
            for ni, nj in generator._grid().neighbours(i, j, diagonal=False):
                if not generator.sea[nj][ni]:
                    steps.append(abs(generator.elevation[j][i]
                                     - generator.elevation[nj][ni]))
        steps.sort()
        assert steps[int(len(steps) * 0.99)] < 0.16


class TestRainShadow:
    def test_rain_falls_on_the_windward_flank_and_not_the_lee(self):
        """Tested on a wall built for the purpose rather than on a generated range.

        A range in a real world may stand along the wind, or half in the sea, and then
        there is no shadow to find and the test measures noise. The mechanism is what
        matters: air climbing a ridge drops its water on the way up and arrives over
        the far side with none.
        """
        from fw.core.mapgen import climate as climate_module
        from fw.core.mapgen.grid import Grid

        size = 60
        grid = Grid(size=size, span=600.0)
        sea = [[i < 6 for i in range(size)] for _ in range(size)]
        elevation = grid.filled(0.0)
        for j in range(size):
            for i in range(size):
                if sea[j][i]:
                    continue
                # a wall across the middle, running north to south
                elevation[j][i] = 0.12 + 0.7 * max(0.0, 1.0 - abs(i - 30) / 9.0)
        wind = climate_module.Wind(vector=(1.0, 0.0), source="brief",
                                   because="from the west")
        rain = climate_module._sweep(grid, elevation=elevation, sea=sea, wind=wind,
                                     seed="t", sea_level=0.10)
        windward = sum(rain[j][i] for j in range(size)
                       for i in range(22, 29)) / (size * 7)
        lee = sum(rain[j][i] for j in range(size)
                  for i in range(32, 39)) / (size * 7)
        assert windward > lee * 3.0, f"{windward:.4f} windward vs {lee:.4f} lee"

    def test_a_generated_range_across_the_wind_casts_a_shadow(self,
                                                              mountains: World):
        generator = shaped(mountains)
        wx, wy = generator.climate.wind.vector
        for mountain in generator.relief.ranges:
            sx, sy = mountain.strike.vector
            if abs(sx * -wy + sy * wx) < 0.55:
                continue          # this one runs along the wind; no shadow to find
            windward, lee = dryness_across(generator._grid(), generator.climate,
                                           mountain.spine, generator.sea)
            if windward and lee:
                assert windward > lee, f"{windward:.3f} windward vs {lee:.3f} lee"

    def test_the_wind_comes_off_the_widest_water(self, mountains: World):
        wind = shaped(mountains).climate.wind
        assert wind.source in ("coast", "default")
        assert "sea" in wind.because or "water" in wind.because

    def test_the_writer_can_say_which_way_the_wind_blows(self, mountains: World):
        generator = MapGenerator(mountains, seed="fixed")
        regions = list(mountains.entities("region"))
        generator.profiles = {r.id: profile_region(mountains, r.id) for r in regions}
        authored = generator._authored_outlines()
        generator._build_landmass(authored)
        generator._assign_cells(regions, authored)
        from fw.core.mapgen import climate as climate_module
        wind = climate_module._wind(generator._grid(), sea=generator.sea,
                                    prevailing="south")
        assert wind.source == "brief"
        assert wind.vector == (0.0, -1.0)

    def test_peaks_are_cold_whatever_the_region_says(self, mountains: World):
        generator = shaped(mountains)
        mountain = spine_of(generator)
        middle = mountain.spine[len(mountain.spine) // 2]
        i, j = int(middle[0]), int(middle[1])
        shore = [generator.temperature[b][a] for a, b in land_of(generator)
                 if generator.from_sea[b][a] <= 2]
        assert generator.temperature[j][i] < statistics.mean(shore)


class TestProseWins:
    def test_what_the_writer_said_carries_the_moisture(self):
        w = world_of([("The Wet", "plain", "drenched, monsoon rain", "100000"),
                      ("The Dry", "plain", "parched, arid", "100000")],
                     [("The Wet", "The Dry")])
        try:
            generator = shaped(w)
            means: dict[str, list[float]] = {}
            for i, j in land_of(generator):
                means.setdefault(generator.owner[j][i], []).append(
                    generator.moisture[j][i])
            by_name = {generator.profiles[rid].name: means.get(rid, [])
                       for rid in generator.profiles}
            assert by_name["The Wet"] and by_name["The Dry"]
            assert (statistics.mean(by_name["The Wet"])
                    > statistics.mean(by_name["The Dry"]) * 1.5)
        finally:
            w.close()

    def test_the_model_speaks_when_the_writer_is_silent(self):
        w = world_of([("The Unsaid", "plain", "", "100000"),
                      ("The Other", "hills", "", "100000")],
                     [("The Unsaid", "The Other")])
        try:
            generator = shaped(w)
            wet = [generator.moisture[j][i] for i, j in land_of(generator)]
            assert max(wet) - min(wet) > 0.2, "an unspoken climate should still vary"
        finally:
            w.close()


class TestDeterminismAndCost:
    def test_the_same_world_gives_the_same_relief(self):
        first = world_of(MOUNTAIN_WORLD, BORDERS)
        second = world_of(MOUNTAIN_WORLD, BORDERS)
        try:
            a, b = shaped(first), shaped(second)
            assert a.elevation == b.elevation
            assert [r.spine for r in a.relief.ranges] == [r.spine
                                                          for r in b.relief.ranges]
        finally:
            first.close()
            second.close()

    def test_relief_and_weather_stay_in_budget(self, mountains: World):
        generator = MapGenerator(mountains, seed="fixed")
        regions = list(mountains.entities("region"))
        generator.profiles = {r.id: profile_region(mountains, r.id) for r in regions}
        authored = generator._authored_outlines()
        generator._build_landmass(authored)
        generator._assign_cells(regions, authored)
        started = time.perf_counter()
        generator._build_fields()
        assert time.perf_counter() - started < 1.5
