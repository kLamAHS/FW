"""Water running over ground, and what it must be true of afterwards.

Erosion is the stage the whole map hangs off — rivers follow the valleys it cuts,
settlements want the flats it lays down, roads want the passes it leaves — so the
properties asserted here are the ones everything downstream assumes without checking.
The important ones are invariants rather than numbers: no sink anywhere, nothing carried
uphill, and the same continent on every machine.
"""

from __future__ import annotations

import math

import pytest

from fw.core.mapgen import erode, noise
from fw.core.mapgen.grid import Grid

SIZE = 64


def slope_world(size: int = SIZE, *, seed: str = "t"):
    """A ramp from a ridge down to the sea on three sides, with fractal detail on it."""
    grid = Grid(size=size, span=float(size))
    height = grid.filled(0.0)
    sea = [[False] * size for _ in range(size)]
    half = (size - 1) / 2.0
    for j in range(size):
        for i in range(size):
            dx, dy = (i - half) / half, (j - half) / half
            dome = max(0.0, 1.0 - math.sqrt(dx * dx + dy * dy) * 1.2)
            grain = noise.fbm(seed, i / 9.0, j / 9.0, octaves=4) - 0.5
            value = dome * 0.7 + grain * 0.18
            height[j][i] = value
            sea[j][i] = value < 0.05
    return grid, height, sea


def local_relief(field, sea, size: int) -> float:
    """Mean height range in a five-cell window — how dissected the ground is."""
    total = count = 0.0
    for j in range(3, size - 3, 2):
        for i in range(3, size - 3, 2):
            if sea[j][i]:
                continue
            window = [field[b][a] for b in range(j - 2, j + 3)
                      for a in range(i - 2, i + 3)]
            total += max(window) - min(window)
            count += 1
    return total / count if count else 0.0


@pytest.fixture(scope="module")
def worn():
    grid, height, sea = slope_world()
    return grid, height, sea, erode.erode(grid, elevation=height, sea=sea, seed="t")


class TestEveryDropReachesTheSea:
    """The invariant the whole rest of the generator is written against.

    Rivers are traced by walking downhill until the water arrives somewhere. If a single
    land cell has no lower neighbour, that walk stops in the middle of a continent — and
    the first draft of this generator drew no rivers at all for exactly that reason,
    because fractal detail riddles a surface with hollows a cell or two across.
    """

    def test_no_land_cell_is_a_sink(self, worn):
        grid, _, sea, result = worn
        stranded = [(i, j) for j in range(SIZE) for i in range(SIZE)
                    if not sea[j][i] and result.downstream[j][i] < 0
                    and 0 < i < SIZE - 1 and 0 < j < SIZE - 1]
        assert not stranded, f"{len(stranded)} cells have nowhere to drain"

    def test_water_only_ever_moves_downhill(self, worn):
        _, _, sea, result = worn
        for j in range(SIZE):
            for i in range(SIZE):
                target = result.downstream[j][i]
                if sea[j][i] or target < 0:
                    continue
                ti, tj = target % SIZE, target // SIZE
                assert result.elevation[tj][ti] <= result.elevation[j][i], (
                    f"({i},{j}) drains uphill to ({ti},{tj})")

    def test_following_the_water_always_arrives(self, worn):
        """No cycles: the walk downhill terminates from every cell on the map."""
        _, _, sea, result = worn
        for j in range(0, SIZE, 3):
            for i in range(0, SIZE, 3):
                if sea[j][i]:
                    continue
                seen, cursor, steps = set(), (i, j), 0
                while steps < SIZE * SIZE:
                    if cursor in seen:
                        pytest.fail(f"a loop starting at ({i},{j})")
                    seen.add(cursor)
                    target = result.downstream[cursor[1]][cursor[0]]
                    if target < 0:
                        break
                    cursor = (target % SIZE, target // SIZE)
                    steps += 1
                else:
                    pytest.fail(f"the walk from ({i},{j}) never arrived")


class TestItLooksLikeWaterHasBeenThere:
    def test_erosion_dissects_the_ground(self, worn):
        """The point of the exercise: more local relief afterwards, not less.

        A smoothing pass would also change the surface, and would lower this number.
        Erosion has to *increase* it — cutting valleys into a slope makes the ground
        rougher at the scale of a few cells even as it lowers the whole surface.
        """
        _, height, sea, result = worn
        before = local_relief(height, sea, SIZE)
        after = local_relief(result.elevation, sea, SIZE)
        assert after > before * 1.15, f"{before:.4f} -> {after:.4f}: barely touched"

    def test_the_biggest_channels_carry_most_of_the_water(self, worn):
        """A drainage network, not a sheet: flow has to concentrate."""
        _, _, sea, result = worn
        flows = sorted((result.flow[j][i] for j in range(SIZE) for i in range(SIZE)
                        if not sea[j][i]), reverse=True)
        assert flows, "no land"
        top = flows[:max(1, len(flows) // 50)]
        assert sum(top) > 0.25 * sum(flows), (
            "the top two per cent of cells carry less than a quarter of the water, "
            "which is a sheet running off a roof rather than a river system")

    def test_a_range_keeps_its_height(self, worn):
        """Erosion wears the ground down; it must not demolish it.

        Deposition proportional to a *share* of the sediment load rather than to its
        concentration does exactly that in reverse — it piles a mountain in the middle of
        a flood plain, and the summit of the map ends up higher than anything the uplift
        ever put there.
        """
        _, height, sea, result = worn
        before = max(height[j][i] for j in range(SIZE) for i in range(SIZE))
        after = max(result.elevation[j][i] for j in range(SIZE) for i in range(SIZE))
        assert after <= before + 1e-9, "erosion raised the highest point"
        assert after > before * 0.7, "erosion flattened the range"

    def test_sediment_settles_low_down_and_not_on_the_tops(self, worn):
        """Alluvium is in the valleys. Nothing else would be alluvium.

        Measured against elevation rather than against slope, and the distinction is not
        pedantry: the steepest fall to *any* neighbour is large on a valley floor at the
        foot of a wall, which is precisely where sediment belongs. Asking whether the
        ground is low is asking the question the answer is about.
        """
        _, _, sea, result = worn
        land = [(i, j) for j in range(SIZE) for i in range(SIZE) if not sea[j][i]]
        by_height = sorted(land, key=lambda c: result.elevation[c[1]][c[0]])
        third = len(by_height) // 3
        low = sum(result.settled[j][i] for i, j in by_height[:third]) / third
        high = sum(result.settled[j][i] for i, j in by_height[-third:]) / third
        assert low > high * 10.0, (
            f"sediment settles at {low:.5f} per cell low down and {high:.5f} on the "
            f"tops, which is not what a flood plain is")

    def test_most_of_what_is_cut_is_carried_off_the_land(self, worn):
        """The sea is where the sediment budget balances.

        If the land keeps most of what it erodes, the deposition term is filling the
        valleys as fast as the channels cut them and nothing is happening at all.
        """
        _, _, _, result = worn
        cut = sum(value for row in result.cut for value in row)
        kept = sum(value for row in result.settled for value in row)
        assert cut > 0.0
        assert kept < cut * 0.5, "the land is keeping more than half of what it erodes"


class TestNothingDependsOnLuck:
    def test_the_same_ground_wears_the_same_way(self):
        grid, height, sea = slope_world(32)
        first = erode.erode(grid, elevation=[r[:] for r in height], sea=sea, seed="s")
        second = erode.erode(grid, elevation=[r[:] for r in height], sea=sea, seed="s")
        assert first.elevation == second.elevation
        assert first.flow == second.flow
        assert first.downstream == second.downstream

    def test_a_different_seed_wears_it_differently(self):
        grid, height, sea = slope_world(32)
        one = erode.erode(grid, elevation=[r[:] for r in height], sea=sea, seed="a")
        two = erode.erode(grid, elevation=[r[:] for r in height], sea=sea, seed="b")
        assert one.elevation != two.elevation

    def test_the_input_is_not_written_on(self):
        """`uplift` has to still be the surface before any water ran.

        The whole stage works in place for speed, so this is the one thing that could
        quietly go wrong and would not show up anywhere else — the explanation shown to
        the writer for why a valley is there compares the two.
        """
        grid, height, sea = slope_world(32)
        original = [row[:] for row in height]
        result = erode.erode(grid, elevation=height, sea=sea, seed="s")
        assert height == original, "erode() overwrote the field it was handed"
        assert result.uplift == original


class TestDegenerateGround:
    def test_a_flat_world_does_not_crash(self):
        grid = Grid(size=16, span=16.0)
        height = grid.filled(0.4)
        sea = [[False] * 16 for _ in range(16)]
        result = erode.erode(grid, elevation=height, sea=sea, seed="s")
        assert len(result.elevation) == 16

    def test_a_world_with_no_land_does_not_crash(self):
        grid = Grid(size=16, span=16.0)
        height = grid.filled(-0.2)
        sea = [[True] * 16 for _ in range(16)]
        result = erode.erode(grid, elevation=height, sea=sea, seed="s")
        assert result.carved() == 0.0
