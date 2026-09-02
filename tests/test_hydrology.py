"""The drainage as a system (V2 §6).

Erosion always knew the network — it cut the valleys from it — and the map used to
re-derive a cartoon: every source walked to the sea as its own river, `strahler` was
a drawing width plus one, and a wet basin was something the pit-filling erased. What
is asserted here is the reading: true stream order on hand-built forests, mainstems
that take the biggest branch, mouths classified from the fields that already exist,
and the restraint budgets (three lakes, two deltas) actually enforced.
"""

from __future__ import annotations

import pytest

from fw.core.mapgen import hydrology
from fw.core.mapgen.hydrology import MOST_LAKES, _lakes, _mainstem, _strahler


def forest(*edges):
    """donors from (feeder -> target) pairs."""
    donors: dict = {}
    cells = set()
    for feeder, target in edges:
        donors.setdefault(target, []).append(feeder)
        cells.add(feeder)
        cells.add(target)
    return cells, donors


class TestStrahlerIsReal:
    def test_a_single_thread_is_order_one_everywhere(self):
        cells, donors = forest(((3, 0), (2, 0)), ((2, 0), (1, 0)))
        order = _strahler(cells, donors)
        assert set(order.values()) == {1}

    def test_two_equal_tributaries_step_the_order_up(self):
        cells, donors = forest(
            ((0, 0), (1, 1)), ((2, 0), (1, 1)),      # two order-1 heads meet
            ((1, 1), (1, 2)))
        order = _strahler(cells, donors)
        assert order[(0, 0)] == order[(2, 0)] == 1
        assert order[(1, 1)] == 2
        assert order[(1, 2)] == 2                     # carried, not stepped again

    def test_an_unequal_meeting_carries_the_larger(self):
        cells, donors = forest(
            ((0, 0), (1, 1)), ((2, 0), (1, 1)),      # order 2 forms at (1,1)
            ((4, 0), (1, 2)),                        # a lone order-1 brook
            ((1, 1), (1, 2)))                        # meets the order-2 trunk
        order = _strahler(cells, donors)
        assert order[(1, 2)] == 2

    def test_two_order_twos_make_an_order_three(self):
        cells, donors = forest(
            ((0, 0), (1, 1)), ((2, 0), (1, 1)),
            ((4, 0), (5, 1)), ((6, 0), (5, 1)),
            ((1, 1), (3, 2)), ((5, 1), (3, 2)))
        order = _strahler(cells, donors)
        assert order[(3, 2)] == 3

    def test_order_never_falls_downstream(self):
        cells, donors = forest(
            ((0, 0), (1, 1)), ((2, 0), (1, 1)),
            ((1, 1), (1, 2)), ((1, 2), (1, 3)))
        order = _strahler(cells, donors)
        assert order[(1, 1)] <= order[(1, 2)] <= order[(1, 3)]


class TestTheMainstemTakesTheBiggerBranch:
    def test_it_walks_up_the_larger_water(self):
        cells, donors = forest(
            ((0, 0), (1, 1)), ((2, 0), (1, 1)),      # big branch: order 2
            ((4, 4), (1, 2)),                        # small branch: order 1
            ((1, 1), (1, 2)))
        order = _strahler(cells, donors)
        flow = [[1.0] * 8 for _ in range(8)]
        flow[1][1] = 9.0                             # the big branch carries more
        stem = _mainstem((1, 2), donors, order, flow)
        assert stem[-1] == (1, 2)
        assert (1, 1) in stem and (4, 4) not in stem
        assert stem[0] in ((0, 0), (2, 0))           # a true head, not the brook


class TestLakesAreBudgeted:
    def blob(self, marsh, cx, cy, reach=2, wet=0.3):
        for j in range(cy - reach, cy + reach + 1):
            for i in range(cx - reach, cx + reach + 1):
                marsh[j][i] = wet

    def test_a_broad_wet_basin_is_a_lake(self):
        size = 12
        sea = [[False] * size for _ in range(size)]
        marsh = [[0.0] * size for _ in range(size)]
        level = [[0.5] * size for _ in range(size)]
        down = [[-1] * size for _ in range(size)]
        self.blob(marsh, 5, 5)
        lakes = _lakes(size, sea, marsh, level, down)
        assert len(lakes) == 1
        assert len(lakes[0].cells) == 25
        assert lakes[0].outlet is None

    def test_a_damp_corner_is_not(self):
        size = 12
        sea = [[False] * size for _ in range(size)]
        marsh = [[0.0] * size for _ in range(size)]
        level = [[0.5] * size for _ in range(size)]
        down = [[-1] * size for _ in range(size)]
        self.blob(marsh, 5, 5, reach=0)              # one lonely wet cell
        assert _lakes(size, sea, marsh, level, down) == ()

    def test_the_budget_holds_however_wet_the_world(self):
        size = 24
        sea = [[False] * size for _ in range(size)]
        marsh = [[0.0] * size for _ in range(size)]
        level = [[0.5] * size for _ in range(size)]
        down = [[-1] * size for _ in range(size)]
        for cx, cy in ((3, 3), (10, 3), (17, 3), (3, 12), (10, 12)):
            self.blob(marsh, cx, cy)
        assert len(_lakes(size, sea, marsh, level, down)) == MOST_LAKES


# Module-scoped rather than a class fixture on `self`: pytest is retiring
# class-scoped fixtures defined as instance methods, and the escalated warning
# errored every test in the class.
@pytest.fixture(scope="module")
def studied():
    import corpus

    from fw.core.mapgen.generate import (
        MapGenerator,
    )

    world = corpus.delta()
    try:
        g = MapGenerator(world, seed="golden")
        g.regions_of_the_world()
        g.read_the_world()
        g.build_the_world()
        yield g.hydrology
    finally:
        world.close()


class TestOnRealGround:

    def test_systems_do_not_share_a_mainstem_cell(self, studied):
        seen: set = set()
        for system in studied.systems:
            cells = set(system.mainstem)
            assert not (cells & seen)
            seen |= cells

    def test_tributaries_meet_their_own_trunk(self, studied):
        for system in studied.systems:
            stem = set(system.mainstem)
            for arc in system.tributaries:
                assert arc.cells[-1] in stem, "a tributary that joins nothing"
                assert arc.order >= hydrology.TRIBUTARY_ORDER

    def test_the_order_raster_agrees_with_the_systems(self, studied):
        for system in studied.systems:
            i, j = system.mouth
            assert studied.order[j][i] == system.order

    def test_the_delta_budget_holds(self, studied):
        deltas = [s for s in studied.systems if s.mouth_kind == "delta"]
        assert len(deltas) <= hydrology.MOST_DELTAS
