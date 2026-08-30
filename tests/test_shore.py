"""What kind of shore each stretch of coast is (V2 §4).

The classifier's promise is variation *between stretches*, not per-cell noise: a
steep shore reads as cliff, a flat open one as beach, reeds where the marsh meets
the water, and a delta exactly where hydrology said a silt-heavy river arrives. The
smoothing must kill one-cell speckle without voting away a real mouth.
"""

from __future__ import annotations

from fw.core.mapgen import shore


def flat(size, value=0.0):
    return [[value] * size for _ in range(size)]


def island(size, reach=4):
    """A square island in the middle of the sea."""
    sea = [[True] * size for _ in range(size)]
    mid = size // 2
    for j in range(mid - reach, mid + reach + 1):
        for i in range(mid - reach, mid + reach + 1):
            sea[j][i] = False
    return sea


class TestTheClassesFollowTheGround:
    def test_a_steep_shore_is_a_wall_and_a_flat_one_is_not(self):
        size = 16
        sea = island(size)
        slope = flat(size)
        for j in range(size):
            slope[j][4] = 0.08                    # the west face is steep
        told = shore.classify(size, sea=sea, elevation=flat(size, 0.2),
                              slope=slope, marsh=flat(size), seed="s", mouths={})
        kinds = {told.kind_of(cell) for cell in told.classes}
        assert kinds & {"cliff", "fjord"}
        assert kinds & {"beach", "sheltered"}

    def test_reeds_where_the_marsh_meets_the_water(self):
        size = 16
        sea = island(size)
        marsh = flat(size)
        for j in range(size):
            marsh[j][12] = 0.3                    # the east face is fen
        told = shore.classify(size, sea=sea, elevation=flat(size, 0.2),
                              slope=flat(size), marsh=marsh, seed="s", mouths={})
        east = [cell for cell in told.classes if cell[0] == 12]
        assert east
        assert {told.kind_of(cell) for cell in east} == {"marsh"}

    def test_a_delta_sits_where_the_river_arrives_and_survives_the_vote(self):
        size = 16
        sea = island(size)
        mouth = (4, size // 2)
        told = shore.classify(size, sea=sea, elevation=flat(size, 0.2),
                              slope=flat(size), marsh=flat(size), seed="s",
                              mouths={mouth: "delta"})
        assert told.kind_of(mouth) == "delta"
        # And only near the mouth — the far shore is unmoved by it.
        far = [cell for cell in told.classes if cell[0] == 11]
        assert "delta" not in {told.kind_of(cell) for cell in far}

    def test_one_odd_cell_is_voted_away(self):
        size = 20
        sea = island(size, reach=6)
        slope = flat(size)
        slope[size // 2][4] = 0.08                # a single steep cell in a sweep
        told = shore.classify(size, sea=sea, elevation=flat(size, 0.2),
                              slope=slope, marsh=flat(size), seed="s", mouths={})
        assert told.kind_of((4, size // 2)) != "cliff"

    def test_the_shallows_know_whose_water_they_are(self):
        size = 16
        sea = island(size)
        told = shore.classify(size, sea=sea, elevation=flat(size, 0.2),
                              slope=flat(size), marsh=flat(size), seed="s",
                              mouths={})
        offshore = told.seaward[size // 2][4 - 1]     # one cell out to sea
        assert offshore > 0.5
        assert told.seaward[0][0] == 0.0              # the open ocean is nobody's


class TestOnTheDrawnMap:
    def test_the_coast_carries_its_character_in_runs(self):
        import corpus

        from fw.core.mapgen.pipeline import plan_map
        from fw.core.mapgen.plan import MapBrief

        world = corpus.long_coast()
        try:
            plan = plan_map(world, MapBrief(seed="golden"))
            coast = next(f for f in plan.features if f.kind == "coast")
            runs = coast.detail["shore"]
            assert runs, "a coast with no character at all"
            kinds = {kind for kind, _ in runs}
            assert len(kinds) >= 2, (
                f"every mile of this coast reads the same: {kinds}")
            vertices = sum(count for _, count in runs)
            assert vertices == len(coast.shapes[0].coordinates[0])
            # The art direction's floor: character holds for stretches, and the
            # ring is not a speckle of one-vertex classes.
            assert sum(1 for _, count in runs if count >= 4) >= len(runs) * 0.3
        finally:
            world.close()
