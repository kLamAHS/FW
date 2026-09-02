"""Borders, and the castles that watch them.

The two are one subject. A frontier is only interesting because of what it costs to cross
it, and a castle is only worth building where crossing is expensive — so the same
measurements decide both, and the assertions here are mostly that each of them is reading
the ground rather than a label.

What is deliberately *not* asserted is that a border follows a ridge. It does not, it
cannot be made to by weighting the flood, and `Grid.claimed_from` records the two
measurements that establish it. What the map does instead is go and look at what its
borders turned out to run along, and say so.
"""

from __future__ import annotations

import collections

import pytest

from fw.core.mapgen import hold, movement, territory
from fw.core.mapgen.generate import GRID, MapGenerator
from fw.core.mapgen.grid import Grid, stands_above
from fw.core.seed.renn import seed_renn


@pytest.fixture(scope="module")
def built():
    world = seed_renn()
    generator = MapGenerator(world, seed="renn")
    generator.generate()
    yield generator
    world.close()


def land_of(generator):
    return [(i, j) for j in range(GRID) for i in range(GRID)
            if not generator.sea[j][i]]


class TestTerritoryIsWhatTheTownsHold:
    def test_the_ground_round_a_town_belongs_to_that_town_s_region(self, built):
        """Which is the whole claim: territory is the country the towns hold.

        Grown from an anchor this could fail freely — the anchor is a made-up point and
        a town could sit in a neighbour's reach without anything noticing.
        """
        grid = built._grid()
        for site in built.settlement.sites:
            i, j = site.cell
            mine = built.partition.owner[j][i]
            assert mine >= 0, f"a town at {site.cell} in nobody's country"
            near = [(a, b) for a, b in grid.neighbours(i, j)
                    if not built.sea[b][a]]
            same = sum(1 for a, b in near if built.partition.owner[b][a] == mine)
            assert same * 2 >= len(near), (
                f"the town at {site.cell} holds less than half the ground touching it")

    def test_every_acre_belongs_to_somebody(self, built):
        for i, j in land_of(built):
            assert built.partition.owner[j][i] >= 0, f"{(i, j)} belongs to nobody"

    def test_no_region_is_cut_in_two_by_a_neighbour(self, built):
        """Seeding from several towns can strand a pocket; the repair hands it over.

        Islands are exempt, and are what makes this worth writing carefully: a piece with
        no land neighbour of another colour is off the coast, and offshore land belonging
        to the nearest mainland is the map working. What is not allowed is a pocket with
        another region's land against it — that is a region cut in two.
        """
        grid = built._grid()
        land = set(land_of(built))
        seen: set[tuple[int, int]] = set()
        pieces: dict[int, list[tuple[int, bool, tuple[int, int]]]] = (
            collections.defaultdict(list))
        for start in sorted(land):
            if start in seen:
                continue
            mine = built.partition.owner[start[1]][start[0]]
            count, touches = 0, False
            stack = [start]
            seen.add(start)
            while stack:
                i, j = stack.pop()
                count += 1
                for ni, nj in grid.neighbours(i, j, diagonal=False):
                    if (ni, nj) not in land:
                        continue
                    if built.partition.owner[nj][ni] == mine:
                        if (ni, nj) not in seen:
                            seen.add((ni, nj))
                            stack.append((ni, nj))
                    else:
                        touches = True
            pieces[mine].append((count, touches, start))

        for owner, found in pieces.items():
            biggest = max(count for count, _, _ in found)
            for count, touches, start in found:
                if count == biggest:
                    continue
                assert not touches, (
                    f"region {owner} holds a pocket of {count} cells at {start} with "
                    "another region's land against it, so it has been cut in two")

    def test_the_writers_weights_still_decide_the_sizes(self, built):
        """The towns decide a region's shape. The writer decides how much there is of it.

        So the region with the most people is still the largest, which is the thing that
        would break if the number of towns were allowed to leak into the rate.
        """
        share = {built.profiles[rid].name: built.partition.share(
            built.profiles[rid].name) for rid in built.profiles}
        people = {built.profiles[rid].name: built._population_of(rid)
                  for rid in built.profiles}
        biggest = max(share, key=lambda key: share[key])
        assert people[biggest] == max(people.values())


class TestABorderIsMeasuredNotIntended:
    def test_every_pair_that_touches_gets_a_frontier(self, built):
        assert built.frontiers, "a continent with three regions and no borders"
        for frontier in built.frontiers:
            assert frontier.length >= territory.SHORTEST_FRONTIER
            assert frontier.between[0] != frontier.between[1]

    def test_the_shares_of_what_it_runs_along_add_up(self, built):
        for frontier in built.frontiers:
            total = (frontier.crest + frontier.water + frontier.fen
                     + frontier.coast + frontier.open_country)
            assert abs(total - 1.0) < 1e-9

    def test_high_ground_means_higher_than_this_world_not_than_a_constant(self, built):
        """The measure has to discriminate, and at a fixed 0.02 it did not.

        Ninety per cent of the continent cleared that, so every border on every map came
        back running along a crest. Asserting that the cut sits inside the world's own
        distribution is asserting that the answer means something.
        """
        grid = built._grid()
        rises = sorted(stands_above(grid, built.elevation, built.sea, i, j)
                       for i, j in land_of(built))
        cut = rises[int(len(rises) * territory.CREST_QUANTILE)]
        above = sum(1 for value in rises if value >= cut) / len(rises)
        assert 0.15 < above < 0.4, f"{above:.0%} of the land counts as high ground"

    def test_an_open_frontier_is_reported_and_a_defended_one_is_not(self, built):
        said = {tuple(sorted(f.subjects)) for f in built.frontier_findings()}
        for frontier in built.frontiers:
            key = tuple(sorted(frontier.between))
            if frontier.open_country >= territory.MOSTLY_OPEN:
                assert key in said, f"{key} is wide open and nobody said so"
            else:
                assert key not in said


class TestCastlesWatchSomething:
    def test_every_castle_has_a_case(self, built):
        assert built.holds.sites
        for place in built.holds.sites:
            if place.invented:
                assert place.reasons, f"a castle at {place.cell} for no reason"

    def test_none_of_them_is_in_the_sea_or_a_bog(self, built):
        for place in built.holds.sites:
            i, j = place.cell
            assert not built.sea[j][i]
            assert built.vegetation.marsh[j][i] < 0.5, "a castle in a fen"

    def test_they_are_spread_across_the_kingdom_not_heaped_in_one_march(self, built):
        """Passes are worth most and passes are all in the mountains.

        Without room per region the example kingdom put seven of its nine castles in the
        one mountain march and every last one of them watched a pass, leaving the coast
        and the open border with nothing — which is the opposite of an answer.
        """
        where = collections.Counter(
            built.owner[h.cell[1]][h.cell[0]] for h in built.holds.sites)
        assert len(where) >= 2, "every castle in the kingdom is in one region"
        assert max(where.values()) <= hold.PER_REGION

    def test_they_do_not_all_watch_the_same_thing(self, built):
        watching = collections.Counter(h.watches for h in built.holds.sites)
        assert len(watching) >= 2, f"every castle watches the same thing: {watching}"

    def test_a_castle_outranks_a_tower_by_what_it_stands_over(self, built):
        for place in built.holds.sites:
            if place.rank == "castle":
                assert place.watches in hold.HELD_AGAINST_AN_ARMY
            if place.rank == "tower":
                assert place.watches not in hold.HELD_AGAINST_AN_ARMY
                assert place.watches != "road"

    def test_no_two_of_them_share_a_cell_or_crowd_one(self, built):
        cells = [h.cell for h in built.holds.sites]
        for n, one in enumerate(cells):
            for other in cells[n + 1:]:
                gap = max(abs(one[0] - other[0]), abs(one[1] - other[1]))
                assert gap >= hold.SPACING, f"{one} and {other} are {gap} apart"

    def test_a_hold_at_a_crossing_really_is_at_one(self, built):
        near = {c.cell for c in built.movement.crossings()}
        for place in built.holds.sites:
            if place.watches not in hold.HELD_AGAINST_AN_ARMY:
                continue
            gap = min(max(abs(place.cell[0] - a), abs(place.cell[1] - b))
                      for a, b in near)
            assert gap <= hold.CROSSING_REACH + 1, (
                f"a {place.rank} said to watch a {place.watches} is {gap} cells from "
                "the nearest crossing of any kind")


class TestBordersAreStrokedOnce:
    def test_an_arc_knows_when_it_runs_against_the_sea(self, built):
        arcs = territory.drawn_arcs(built.partition, built.sea)
        assert any(left is None or right is None for left, right, _ in arcs), \
            "a coastal kingdom with no coastal arc"
        assert any(left is not None and right is not None for left, right, _ in arcs), \
            "several regions and no frontier between any two"

    def test_only_the_inland_arcs_are_stroked(self, built):
        """The coastline wins: a border re-drawn along the shore is the double line."""
        from fw.core.mapgen import pipeline

        borders = pipeline._border_arcs(built)
        drawn = sum(len(runs) for runs in borders.values())
        inland = sum(1 for left, right, pts in
                     territory.drawn_arcs(built.partition, built.sea)
                     if left is not None and right is not None and len(pts) >= 2)
        assert drawn == inland
        assert drawn, "no borders at all"

    def test_each_frontier_arc_belongs_to_exactly_one_region(self, built):
        from fw.core.mapgen import pipeline

        borders = pipeline._border_arcs(built)
        seen: set = set()
        for runs in borders.values():
            for points, _kind in runs:
                key = tuple(tuple(p) for p in points)
                assert key not in seen and tuple(reversed(key)) not in seen
                seen.add(key)


class TestTheSharedMeasuresAreShared:
    def test_a_claimant_may_spread_from_several_places_at_once(self):
        grid = Grid(size=9, span=9.0, origin_x=0.0, origin_y=0.0)
        owner = grid.claimed_from([([(0, 0), (8, 8)], 1.0), ([(8, 0)], 1.0)])
        assert owner[0][0] == 0 and owner[8][8] == 0
        assert owner[0][8] == 1

    def test_cost_makes_reach_mean_travelling(self):
        """A wall of dear ground down the middle keeps the left-hand claimant out."""
        size = 11
        grid = Grid(size=size, span=float(size), origin_x=0.0, origin_y=0.0)
        cost = [[20.0 if i == 5 else 1.0 for i in range(size)] for _ in range(size)]
        plain = grid.claimed_by([((0, 5), 1.0), ((10, 5), 1.0)])
        walled = grid.claimed_by([((0, 5), 1.0), ((10, 5), 1.0)], cost=cost)
        assert plain[5][7] == 1
        # With the wall, the left claimant cannot afford anything past it.
        assert all(walled[j][i] == 1 for j in range(size) for i in range(6, size))

    def test_one_answer_for_how_far_a_place_stands_above_its_country(self, built):
        """Three modules wanted this and each had grown its own. Now there is one."""
        grid = built._grid()
        i, j = next(iter(land_of(built)))
        assert stands_above(grid, built.elevation, built.sea, i, j) >= 0.0

    def test_the_crossing_sweep_agrees_with_the_crossings(self, built):
        reach, kinds, _strength = movement.nearest_crossing(
            built._grid(), built.movement.crossings())
        for crossing in built.movement.crossings():
            i, j = crossing.cell
            assert reach[j][i] == 0.0
            assert kinds[j][i] in {c.kind for c in built.movement.crossings()
                                   if c.cell == (i, j)}
