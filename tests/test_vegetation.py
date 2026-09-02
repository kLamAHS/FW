"""Cover as a density, and marsh as a consequence of drainage.

The claims here are the ones that separate a continuous model from a table lookup. A
forest that is a *value* has no edge, thins with height, and stops at a treeline without
anyone drawing one; a forest that is a classification has none of those properties, and
no amount of tuning gives them to it. Likewise a marsh that comes out of slope, water
table and drainage lands on flood plains, and one that comes out of a temperature and
rainfall table lands wherever the table says.
"""

from __future__ import annotations

import pytest

from fw.core.mapgen import vegetation
from fw.core.mapgen.generate import GRID, MapGenerator
from fw.core.seed.renn import seed_renn


@pytest.fixture(scope="module")
def grown():
    world = seed_renn()
    generator = MapGenerator(world, seed="renn")
    generator.generate(propose_settlements=False)
    yield generator
    world.close()


def land_of(generator):
    return [(i, j) for j in range(GRID) for i in range(GRID)
            if not generator.sea[j][i]]


class TestCoverIsADensity:
    def test_the_canopy_takes_every_value_and_not_two(self, grown):
        """The whole point. A classification gives a histogram with two spikes in it."""
        land = land_of(grown)
        values = [grown.vegetation.canopy[j][i] for i, j in land]
        middling = [v for v in values if 0.15 < v < 0.85]
        assert len(middling) > len(land) * 0.15, (
            "almost nothing is partly wooded, so this is a classification wearing a "
            "density's clothes")

    def test_a_wood_has_an_edge_that_wanders(self, grown):
        """A boundary from a continuous field is ragged; one from a polygon is not.

        Counted as the length of the boundary against the area enclosed. A wood with a
        smooth edge has a low ratio; a real one, with bays and spurs and outlying copses,
        has a high one.
        """
        canopy = grown.vegetation.canopy
        inside = edge = 0
        for i, j in land_of(grown):
            if canopy[j][i] < 0.5:
                continue
            inside += 1
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ni, nj = i + di, j + dj
                if not (0 <= ni < GRID and 0 <= nj < GRID):
                    continue
                if canopy[nj][ni] < 0.5:
                    edge += 1
                    break
        assert inside > 50, "there is barely any wood to measure"
        assert edge / inside > 0.16, (
            f"only {edge / inside:.0%} of the wood is edge, which is a blob")

    def test_the_treeline_is_what_thins_the_wood_up_a_mountain(self):
        """The treeline term, on its own, with everything else held still.

        Worth having because the two whole-world tests below CANNOT see it. Setting
        `_limits`' height term to 1.0 — trees ignoring the treeline entirely — leaves
        both of them passing, because high ground is also cold and the cold ramp alone
        strips the summits. So a test named for the treeline was measuring the cold.

        Here warmth is held at a comfortable value and only the height moves, which is
        the one arrangement where the treeline is the only thing that can be speaking.
        """
        warm = 0.8
        line = vegetation.treeline_of(warm)
        below = vegetation._limits(line - 0.05, warm, marshy=0.0)
        crossing = vegetation._limits(line + 0.05, warm, marshy=0.0)
        far = vegetation._limits(line + vegetation.TREELINE_FADE + 0.05,
                                 warm, marshy=0.0)
        assert below > 0.0, "nothing grows below the treeline in a warm place"
        assert crossing < below, "crossing the treeline does not thin the wood"
        assert far == 0.0, "the wood does not stop at all, it only fades"

    def test_a_claimed_wood_carries_its_own_treeline_higher(self):
        """§66 in the vegetation model: if the writer says there are trees up there,
        the map believes them rather than overruling it with a constant."""
        warm = 0.8
        just_above = vegetation.treeline_of(warm) + 0.1
        ordinary = vegetation._limits(just_above, warm, marshy=0.0, hardy=0.0)
        claimed = vegetation._limits(just_above, warm, marshy=0.0, hardy=1.0)
        assert claimed > ordinary, "the writer said trees and the treeline ignored them"

    def test_high_ground_carries_less_wood_than_low(self):
        """Asked of a world that HAS high ground, which Renn very nearly does not.

        This used to run on the seeded world and rested on fifteen cells. Renn is a
        lowland kingdom: only fifteen of its unclaimed land cells sit above the
        treeline at all, every one of their canopy values between 0.13 and 0.19,
        against five and a half thousand cells below. Fifteen samples decided a claim
        about how vegetation behaves everywhere, and when a change to the seed's own
        outlines moved which fifteen, the ratio crossed the threshold — 0.374 against
        a limit of 0.350 — with nothing about the vegetation having changed.

        It is named for what it measures now. The old name said "the forest stops at
        the treeline", and it does not test that: see the unit test above, which does.

        Measured across the corpus, the property is not marginal at all where there is
        ground to measure it: `alps` gives 0.000 over 1424 cells above the line and
        `empire` 0.017 over 712. So the fix is not a looser threshold, which would
        have hidden a real regression on those worlds; it is to ask the question
        somewhere it can be answered, and to refuse to answer it on too small a sample.

        Measured where the writer has not claimed a wood: where they have, the map
        takes their word for it that the trees are hardier and carries the treeline
        higher (`HARDY_TREELINE`), so the ordinary treeline is not what applies there.
        """
        import corpus

        world = corpus.CORPUS["alps"]()
        try:
            grown = MapGenerator(world, seed="alps")
            grown.generate(propose_settlements=False)
            silent = {index for index, profile in grown.profiles.items()
                      if profile.terrain_mix.get("forest", 0.0) <= 0.0}
            ground = [(i, j) for i, j in land_of(grown) if grown.owner[j][i] in silent]
            high = [(i, j) for i, j in ground
                    if grown.elevation[j][i] > vegetation.treeline_of(
                        grown.temperature[j][i]) + 0.1]
            low = [(i, j) for i, j in ground if 0.05 < grown.elevation[j][i] < 0.3]
            assert len(high) >= 200 and len(low) >= 200, (
                f"only {len(high)} cells above the treeline and {len(low)} below — "
                f"too few to say anything about either")
            above = sum(grown.vegetation.canopy[j][i] for i, j in high) / len(high)
            below = sum(grown.vegetation.canopy[j][i] for i, j in low) / len(low)
            assert above < below * 0.35, (
                f"the canopy is {above:.3f} above the treeline and {below:.3f} below it")
        finally:
            world.close()

    def test_the_seeded_world_is_wooded_low_and_bare_high(self, grown):
        """What Renn's own sample can support, which is not a ratio.

        Renn has almost no ground above its treeline, so this asks the weaker question
        it can actually answer: of the unclaimed ground, the highest cells are not the
        most wooded. The strong form is the test above, on a world with alps in it.
        """
        silent = {index for index, profile in grown.profiles.items()
                  if profile.terrain_mix.get("forest", 0.0) <= 0.0}
        ground = [(i, j) for i, j in land_of(grown) if grown.owner[j][i] in silent]
        assert len(ground) > 500, "not enough unclaimed ground to measure"
        by_height = sorted(ground, key=lambda c: grown.elevation[c[1]][c[0]])
        share = max(1, len(by_height) // 10)
        top = by_height[-share:]
        bottom = by_height[:share]
        highest = sum(grown.vegetation.canopy[j][i] for i, j in top) / len(top)
        lowest = sum(grown.vegetation.canopy[j][i] for i, j in bottom) / len(bottom)
        assert highest < lowest, (
            f"the highest tenth of the ground carries {highest:.3f} of canopy and "
            f"the lowest {lowest:.3f} — the trees are not thinning with height")

    def test_the_wet_side_of_the_world_is_the_wooded_side(self, grown):
        land = land_of(grown)
        by_rain = sorted(land, key=lambda c: grown.moisture[c[1]][c[0]])
        third = len(by_rain) // 3
        dry = sum(grown.vegetation.canopy[j][i] for i, j in by_rain[:third]) / third
        wet = sum(grown.vegetation.canopy[j][i] for i, j in by_rain[-third:]) / third
        assert wet > dry * 1.5, f"dry ground carries {dry:.3f}, wet ground {wet:.3f}"

    def test_the_bands_the_renderer_draws_are_all_reached(self, grown):
        shares = [grown.vegetation.wooded_share(grown.sea, band)
                  for band in vegetation.BANDS]
        assert shares == sorted(shares), "a denser band covers more ground than a thinner"
        assert shares[0] > 0.0, "nothing anywhere is closed forest"
        assert shares[-1] < 0.95, "the whole continent is under trees"


class TestMarshComesFromTheWater:
    def test_marshes_are_low_flat_and_badly_drained(self, grown):
        wet = [(i, j) for i, j in land_of(grown) if grown.vegetation.marsh[j][i] > 0.15]
        assert wet, "the map has no wetland at all"
        for i, j in wet:
            assert grown.erosion.slope[j][i] < 0.05, "a marsh on a slope"
            assert grown.vegetation.water_table[j][i] > 0.5, "a marsh in dry ground"

    def test_a_marsh_is_near_its_river(self, grown):
        """Wetland belongs on a flood plain, which is to say near the drainage."""
        wet = [(i, j) for i, j in land_of(grown) if grown.vegetation.marsh[j][i] > 0.15]
        assert wet
        near = sum(1 for i, j in wet if grown.vegetation.water_table[j][i] > 0.7)
        assert near > len(wet) * 0.6, (
            "most of the wetland is not on ground with a high water table")

    def test_wetland_is_a_small_part_of_the_world(self, grown):
        land = land_of(grown)
        wet = sum(1 for i, j in land if grown.vegetation.marsh[j][i] > 0.15)
        assert 0 < wet < len(land) * 0.12, (
            f"{wet / len(land):.1%} of the continent is marsh")

    def test_the_water_table_is_highest_where_the_water_goes(self, grown):
        land = land_of(grown)
        by_flow = sorted(land, key=lambda c: grown.erosion.flow[c[1]][c[0]])
        third = len(by_flow) // 3
        dry = sum(grown.vegetation.water_table[j][i] for i, j in by_flow[:third]) / third
        wet = sum(grown.vegetation.water_table[j][i]
                  for i, j in by_flow[-third:]) / third
        assert wet > dry, "ground that drains a big catchment is not the wetter ground"


class TestTheWriterIsHeard:
    def test_a_region_is_as_wooded_as_the_writer_said(self, grown):
        """Author sovereignty: their sentence is the fact, the model supplies variation.

        "Mountains and forest" is a claim about a whole march, so it is kept as one: the
        region's average cover is the share they named. The model is left to say *where*
        inside it the trees are, which the next test checks is still happening.

        This pins a real inversion. Read cell by cell instead, the one march the writer
        described as forest came out the *least* wooded region on the map — the model
        outvoted them everywhere at once because the ground was also cold and high.
        """
        named = [rid for rid, profile in grown.profiles.items()
                 if profile.terrain_mix.get("forest", 0.0) > 0.3]
        if not named:
            pytest.skip("the example world names no forest region")
        for region_id in named:
            cells = [(i, j) for i, j in land_of(grown)
                     if grown.owner[j][i] == region_id]
            if len(cells) < 30:
                continue
            said = grown.profiles[region_id].terrain_mix["forest"]
            mean = sum(grown.vegetation.canopy[j][i] for i, j in cells) / len(cells)
            assert abs(mean - said) < 0.08, (
                f"the writer called this region {said:.0%} forest and the map made it "
                f"{mean:.0%}")

    def test_and_the_model_still_decides_where(self, grown):
        """Honouring the average must not flatten the wood into an even wash."""
        named = [rid for rid, profile in grown.profiles.items()
                 if profile.terrain_mix.get("forest", 0.0) > 0.3]
        if not named:
            pytest.skip("the example world names no forest region")
        for region_id in named:
            cells = [(i, j) for i, j in land_of(grown)
                     if grown.owner[j][i] == region_id]
            if len(cells) < 30:
                continue
            by_rain = sorted(cells, key=lambda c: grown.moisture[c[1]][c[0]])
            third = max(1, len(by_rain) // 3)
            dry = sum(grown.vegetation.canopy[j][i]
                      for i, j in by_rain[:third]) / third
            wet = sum(grown.vegetation.canopy[j][i]
                      for i, j in by_rain[-third:]) / third
            assert wet > dry * 1.25, (
                f"inside the region the wet ground is {wet:.2f} wooded and the dry "
                f"{dry:.2f}, so the wood has been spread evenly rather than placed")
