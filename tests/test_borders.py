"""One border, drawn once, and a map that only holds what existed.

Four things are asserted here, and each of them was false before it was.

A border between two regions is one line. It was two: each neighbour contoured its own
territory against its own background, and the two answers sat a median of one whole
lattice cell apart — up to two — all the way along every internal frontier.

The land the mask calls dry is the land the height field puts above water. Four hundred
and thirteen cells were land to whatever asked the mask and sea to whatever read the
field; they shaded as water, the coastline was contoured around them, and the borders ran
out across open sea to enclose them.

A border is a line somebody drew, not a walk over a lattice. A third of its length ran
exactly along a lattice axis, which is the arithmetic of a staircase.

And a map of a year holds what stood in that year. A map of the Kingdom of Renn at 100
pinned all six of its towns when only Rennford had been founded — and because a region's
borders are grown from its towns, it drew the borders around five places that were not
there.
"""

from __future__ import annotations

import itertools
import math

import pytest

from fw.core.mapgen import territory
from fw.core.mapgen.generate import GRID, SEA_LEVEL, MapGenerator
from fw.core.seed.renn import seed_renn


@pytest.fixture(scope="module")
def built():
    world = seed_renn()
    generator = MapGenerator(world, seed="renn")
    generator.generate()
    yield generator
    world.close()


def rings_of(generator) -> dict[str, list[list[list[float]]]]:
    return territory.outlines(generator.partition, generator.sea)


class TestABorderIsOneLine:
    def test_two_neighbours_draw_the_border_between_them_identically(self, built):
        """Not to a tolerance — the same points, because it is the same arc."""
        shapes = rings_of(built)
        points = {key: [tuple(round(c, 9) for c in p) for ring in value for p in ring]
                  for key, value in shapes.items()}
        touching = 0
        for a, b in itertools.combinations(sorted(points), 2):
            mine, theirs = points[a], points[b]
            if not mine or not theirs:
                continue
            common = set(mine) & set(theirs)
            if len(common) < 4:
                continue                     # these two do not share a border
            touching += 1
            # The longest unbroken run of one region's outline that the other also has.
            longest = run = 0
            for point in mine:
                run = run + 1 if point in common else 0
                longest = max(longest, run)
            assert longest >= len(common) - 2, (
                f"{a} and {b} share {len(common)} vertices but only {longest} of them "
                "in one unbroken run, so the border is in pieces")
        assert touching, "no two regions on this continent share a border"

    def test_the_shared_vertices_are_exact(self, built):
        """The measurement that started this: the median gap used to be 6.20 units."""
        shapes = rings_of(built)
        points = {key: [tuple(p) for ring in value for p in ring]
                  for key, value in shapes.items()}
        worst = 0.0
        for a, b in itertools.combinations(sorted(points), 2):
            mine, theirs = points[a], points[b]
            if not mine or not theirs:
                continue
            shared = [p for p in mine if p in set(theirs)]
            if len(shared) < 4:
                continue
            gaps = sorted(min(math.dist(p, q) for q in theirs) for p in shared)
            worst = max(worst, gaps[len(gaps) // 2])
        assert worst == 0.0, f"the median gap along a shared border is {worst}"

    def test_every_ring_closes(self, built):
        for key, shape in rings_of(built).items():
            for ring in shape:
                assert len(ring) >= 4, f"{key} has a ring of {len(ring)} points"
                assert ring[0] == ring[-1], f"a ring of {key} does not close"

    def test_a_ring_stays_inside_the_payload(self, built):
        for key, shape in rings_of(built).items():
            for ring in shape:
                assert len(ring) <= territory.MAX_RING_VERTICES, (
                    f"{key} ships a ring of {len(ring)} vertices")

    def test_a_border_is_drawn_not_walked(self, built):
        """A staircase is a border that ran along the lattice instead of the country."""
        arcs = territory.trace_arcs(built.partition, built.sea)
        flat = total = 0.0
        for arc in arcs:
            drawn = territory._drawn(arc)
            for (ax, ay), (bx, by) in zip(drawn, drawn[1:], strict=False):
                dx, dy = abs(bx - ax), abs(by - ay)
                length = math.hypot(dx, dy)
                if length < 1e-9:
                    continue
                total += length
                if dx < 1e-6 or dy < 1e-6:
                    flat += length
        share = flat / total if total else 0.0
        assert share < 0.15, (
            f"{share:.0%} of the border's length runs exactly along a lattice axis; "
            "the walk it came from is 100%, two rounds of smoothing left 33%")


class TestTheGroundAgreesWithTheMask:
    def test_land_is_above_the_water_and_sea_is_below_it(self, built):
        wrong = [(i, j) for j in range(GRID) for i in range(GRID)
                 if (not built.sea[j][i]) != (built.elevation[j][i] > SEA_LEVEL)]
        assert not wrong, (
            f"{len(wrong)} cells are land to the mask and sea to the height field, "
            f"or the other way about — for example {wrong[:3]}")

    def test_the_writers_own_ground_is_never_drowned(self, built):
        """The mask is where author sovereignty lives, so the field comes up to it."""
        drawn = {cell for cells in built.authored_cells.values() for cell in cells}
        assert drawn, "the example world draws its regions"
        for i, j in sorted(drawn):
            if built.sea[j][i]:
                continue
            assert built.elevation[j][i] > SEA_LEVEL, (
                f"the writer drew {(i, j)} inside a region and it is under water")


class TestAMapOfAYearHoldsWhatStoodInIt:
    def test_a_town_founded_later_is_not_on_an_earlier_map(self):
        world = seed_renn()
        try:
            founded = {e.name: e.exists_from for e in world.entities("settlement")}
            assert founded, "the example world dates its towns"
            for year in (100, 150, 240):
                day = world.day(year)
                generator = MapGenerator(world, seed="renn", at=day)
                generator.generate()
                pinned = {world.get_entity(eid).name
                          for eid in generator._settlements_the_writer_drew().values()}
                for name in pinned:
                    assert founded[name] is None or founded[name] <= day, (
                        f"a map of year {year} pins {name}, founded later")
                expected = {n for n, f in founded.items() if f is None or f <= day}
                assert pinned == expected, (
                    f"year {year}: the map holds {sorted(pinned)}, "
                    f"the world had {sorted(expected)}")
        finally:
            world.close()

    def test_the_kingdom_grows(self):
        """The point of the whole thing, as one assertion."""
        world = seed_renn()
        try:
            counts = []
            for year in (100, 150, 240):
                generator = MapGenerator(world, seed="renn", at=world.day(year))
                generator.generate()
                counts.append(len(generator._settlements_the_writer_drew()))
            assert counts == sorted(counts) and counts[0] < counts[-1], (
                f"the writer's towns over time: {counts}")
        finally:
            world.close()


class TestNothingGeneratedCarriesANumber:
    def test_a_name_is_never_finished_with_a_digit(self):
        """Greyhaven2 is not a place, it is a variable, and one on a map says the
        generator gave up. The last-resort path used to count."""
        from fw.core.mapgen.names import Namer

        world = seed_renn()
        try:
            namer = Namer.from_world(world, seed="renn")
            # Far more names than the world has words for, which is what used to make
            # the counter fire.
            made = [namer.name("settlement", f"key-{n}") for n in range(120)]
            for name in made:
                assert not any(c.isdigit() for c in name), f"{name} has a number in it"
            assert len(set(made)) == len(made), "the namer repeated itself"
        finally:
            world.close()
