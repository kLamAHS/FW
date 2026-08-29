"""Movement, resources and settlement — the three stages that put people on the ground.

The order they run in is the argument. A town is not where the ground is nicest; it is
where a river has to be crossed and can be here, or where the only pass over a range
comes down, or where a bay will hold ships. So the crossings have to exist before anybody
is placed at one, and what the country is worth has to be known before anybody is fed by
it. What is asserted here is mostly that: that each stage is reading the one below it
rather than a label somebody typed.
"""

from __future__ import annotations

import collections
import math

import pytest

from fw.core.mapgen import movement, resources, roads, settle
from fw.core.mapgen.generate import (
    CELL,
    GRID,
    MIN_SPACING_CELLS,
    RESOURCE_WORDS,
    MapGenerator,
)
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


class TestTheGroundCostsWhatItIs:
    def test_a_marsh_costs_more_than_the_dry_ground_beside_it(self, built):
        """The old cost came from the region's average terrain, so it could not."""
        wet = [(i, j) for i, j in land_of(built)
               if built.vegetation.marsh[j][i] > 0.2]
        if not wet:
            pytest.skip("no marsh on this continent")
        for i, j in wet[:20]:
            dry = [(a, b) for a, b in built._grid().neighbours(i, j)
                   if not built.sea[b][a] and built.vegetation.marsh[b][a] < 0.02
                   and built.erosion.slope[b][a] <= built.erosion.slope[j][i]]
            for a, b in dry:
                assert built.movement.cost[j][i] > built.movement.cost[b][a]

    def test_a_slope_costs_more_than_the_flat(self, built):
        land = land_of(built)
        steep = [c for c in land if built.erosion.slope[c[1]][c[0]] > 0.12]
        flat = [c for c in land if built.erosion.slope[c[1]][c[0]] < 0.02]
        assert steep and flat
        worst = sum(built.movement.cost[j][i] for i, j in steep) / len(steep)
        easy = sum(built.movement.cost[j][i] for i, j in flat) / len(flat)
        assert worst > easy * 1.5, f"steep {worst:.2f} against flat {easy:.2f}"

    def test_the_sea_cannot_be_walked(self, built):
        for j in range(0, GRID, 5):
            for i in range(0, GRID, 5):
                if built.sea[j][i]:
                    assert built.movement.cost[j][i] == float("inf")


class TestThePlacesTheGroundOpensUp:
    def test_a_ford_is_on_a_river_and_is_wadeable(self, built):
        assert built.movement.fords, "a continent with rivers and no fords"
        biggest = max(built.erosion.flow[j][i] for i, j in land_of(built))
        for ford in built.movement.fords:
            i, j = ford.cell
            carried = built.erosion.flow[j][i] / biggest
            assert movement.FORD_MIN <= carried <= movement.FORD_FLOW
            assert built.erosion.slope[j][i] <= movement.FORD_SLOPE

    def test_a_pass_is_lower_than_the_divide_it_crosses(self, built):
        if not built.movement.passes:
            pytest.skip("no watershed on this continent has a saddle in it")
        for crossing in built.movement.passes[:10]:
            i, j = crossing.cell
            here = built.elevation[j][i]
            ridge = [built.elevation[b][a]
                     for a in range(max(0, i - 4), min(GRID, i + 5))
                     for b in range(max(0, j - 4), min(GRID, j + 5))
                     if not built.sea[b][a]]
            assert max(ridge) - here >= movement.PASS_DROP

    def test_a_harbour_is_sheltered_and_the_water_off_it_floats(self, built):
        assert built.movement.harbours, "a coast with no anchorage anywhere"
        for harbour in built.movement.harbours:
            i, j = harbour.cell
            assert not built.sea[j][i], "a harbour standing in the water"
            assert any(built.sea[b][a] for a, b in built._grid().neighbours(i, j))
            assert built.erosion.slope[j][i] <= movement.HARBOUR_SLOPE

    def test_crossings_are_not_reported_twenty_times_over(self, built):
        """Twenty adjacent cells of the same ford are one ford."""
        for group in (built.movement.fords, built.movement.passes,
                      built.movement.harbours):
            for n, one in enumerate(group):
                for other in group[n + 1:]:
                    apart = max(abs(one.cell[0] - other.cell[0]),
                                abs(one.cell[1] - other.cell[1]))
                    assert apart >= min(movement.FORD_SPACING, movement.HARBOUR_SPACING)

    def test_a_watershed_is_a_watershed_and_not_a_gutter(self, built):
        land = land_of(built)
        named = collections.Counter(
            built.movement.basin[j][i] for i, j in land
            if built.movement.basin[j][i] != movement.MINOR)
        assert 2 <= len(named) <= 60, f"{len(named)} watersheds is not a map"
        assert sum(named.values()) > len(land) * 0.3


class TestWhatTheGroundIsGoodFor:
    def test_grain_is_on_the_flat_and_not_on_the_crags(self, built):
        land = land_of(built)
        flat = [c for c in land if built.erosion.slope[c[1]][c[0]] < 0.03]
        steep = [c for c in land if built.erosion.slope[c[1]][c[0]] > 0.12]
        assert flat and steep
        ploughed = sum(built.resources.at("arable", i, j) for i, j in flat) / len(flat)
        crags = sum(built.resources.at("arable", i, j) for i, j in steep) / len(steep)
        assert ploughed > crags * 3.0

    def test_stone_is_where_the_rock_is_bare(self, built):
        land = land_of(built)
        steep = [c for c in land if built.erosion.slope[c[1]][c[0]] > 0.12]
        flat = [c for c in land if built.erosion.slope[c[1]][c[0]] < 0.03]
        assert steep and flat
        assert (sum(built.resources.at("stone", i, j) for i, j in steep) / len(steep)
                > sum(built.resources.at("stone", i, j) for i, j in flat) / len(flat))

    def test_fishing_is_on_the_shore_and_not_inland(self, built):
        land = land_of(built)
        shore = [c for c in land if built.from_sea[c[1]][c[0]] <= 2]
        inland = [c for c in land if built.from_sea[c[1]][c[0]] > 12]
        assert shore and inland
        assert (sum(built.resources.at("fish", i, j) for i, j in shore) / len(shore)
                > sum(built.resources.at("fish", i, j) for i, j in inland) / len(inland))

    def test_a_region_the_writer_named_for_something_has_some_of_it(self, built):
        """Their sentence is the fact. A march named for its mines has mines.

        The model is free to decide *where* — up the valley, in the crags — and does,
        but it may not answer "no there aren't". A first version could only scale what
        it had already found, so a march with no ore anywhere stayed at none however
        firmly the writer had said otherwise.
        """
        for region_id in sorted(built.profiles):
            profile = built.profiles[region_id]
            cells = [(i, j) for i, j in land_of(built)
                     if built.owner[j][i] == region_id]
            if len(cells) < resources.MEANINGFUL_REGION:
                continue
            for named in profile.resources:
                kind = RESOURCE_WORDS.get(named.strip().lower())
                if kind is None:
                    continue
                best = max(built.resources.at(kind, i, j) for i, j in cells)
                assert best > 0.1, (
                    f"{profile.name} is described for its {named} and has none")


    def test_a_region_named_for_its_rock_has_somebody_at_the_rock(self, built):
        """The writer's fact has to reach the ground, not just the region's page.

        The Iron Spine's ore is up in the crags and the only country in it worth farming
        is twenty cells away on the coast, so the general scoring gave that march three
        hamlets and every one of them was fishing. Nobody farms a mountain: the reason
        anybody is up there is the seam.
        """
        words = built._resource_words()
        drawing = {rid: said for rid, said in words.items()
                   if any(kind in settle.DRAWS_PEOPLE for kind in said)}
        if not drawing:
            pytest.skip("nobody named a resource that draws people")
        for region_id, said in drawing.items():
            kinds = [k for k in said if k in settle.DRAWS_PEOPLE]
            here = [(i, j) for i, j in land_of(built)
                    if built.owner[j][i] == region_id]
            if not any(built.resources.at(k, i, j) >= settle.WORTH_CLAIMED
                       for k in kinds for i, j in here):
                continue                      # the map found none to put anybody at
            mine = [s for s in built.settlement.sites
                    if built.owner[s.cell[1]][s.cell[0]] == region_id]
            assert mine, f"{region_id} has nobody in it at all"
            assert any(
                built.resources.at(k, a, b) >= settle.WORTH_CLAIMED
                for s in mine for k in kinds
                for a, b in here
                if max(abs(a - s.cell[0]), abs(b - s.cell[1])) <= settle.REACH_CLAIMED
            ), (f"{built.profiles[region_id].name} is named for its "
                f"{'/'.join(said[k] for k in kinds)} and nobody lives near any")


class TestWhoLivesWhereAndWhy:
    def test_every_settlement_the_map_invents_argues_from_the_ground(self, built):
        """A place nobody asked for has to earn itself.

        Only the invented ones. A town the writer put on the map stays whether or not the
        ground has anything to say about it — that is what author sovereignty means — and
        on this continent one of them stands on ground that is unremarkable in every way
        the map can measure. Asserting a case for that one would be asserting that the
        map may overrule them.
        """
        assert built.settlement.sites
        for site in built.settlement.sites:
            if site.invented:
                assert site.reasons, f"a settlement at {site.cell} with no case for it"

    def test_the_hierarchy_is_a_hierarchy(self, built):
        """More small places than big ones, which is what a settlement pattern is.

        Asserted tier against tier rather than as one ratio, because the ratio passed a
        map of nine towns, two hamlets and nothing else — every place the same size but
        for two, which is a list of towns and not a kingdom. The tiers are nested, so
        each one down must be at least as numerous as the one above it. The hamlets are
        excused: they are chosen last and take whatever is left of the budget, so a world
        that spends it on villages honestly has none.
        """
        counts = collections.Counter(s.rank for s in built.settlement.sites)
        order = [name for name, _, _, _ in settle.TIERS]
        seen = [counts.get(name, 0) for name in order]
        assert sum(seen) == len(built.settlement.sites)
        for above, below in zip(order, order[1:-1], strict=False):
            assert counts.get(above, 0) <= counts.get(below, 0), (
                f"{counts.get(above, 0)} {above}s over {counts.get(below, 0)} "
                f"{below}s is upside down")
        assert sum(1 for n in seen if n) >= 3, "a kingdom of one size of place"

    def test_the_budget_is_shared_out_and_not_quietly_halved(self, built):
        """Every region's room has to add up to the number the tiers are cut from.

        They did not, and the map lost its hierarchy over it: the tiers were shares of a
        budget of eighteen while the regions between them had room for ten, so the city
        tier went looking with every region already full and the villages never ran.
        """
        room = built._room_per_region()
        assert sum(room.values()) == built._settlements_the_world_implies()
        assert all(n >= 1 for n in room.values()), "a region with room for nobody"

    def test_nobody_is_founded_in_a_bog_or_on_a_crag(self, built):
        for site in built.settlement.sites:
            i, j = site.cell
            assert not built.sea[j][i]
            assert built.vegetation.marsh[j][i] < 0.5, "a town in a fen"

    def test_a_city_has_country_behind_it(self, built):
        cities = built.settlement.of_rank("city")
        if not cities:
            pytest.skip("this world grew no cities")
        middle = sorted(s.support for s in built.settlement.sites)[
            len(built.settlement.sites) // 2]
        for city in cities:
            assert city.support >= middle

    def test_settlements_are_not_on_top_of_each_other(self, built):
        cells = [s.cell for s in built.settlement.sites]
        for n, one in enumerate(cells):
            for other in cells[n + 1:]:
                assert one != other

    def test_the_tiers_keep_the_spacing_the_map_promises(self):
        """`MIN_SPACING_CELLS` used to be kept by a rejection loop that no longer exists.

        The tiers keep it now — every rank is chosen at a spacing wider than the floor —
        and this is the assertion that says so, so that lowering a tier's spacing fails
        here rather than as a puzzling settlement-crowding failure two modules away.
        """
        for rank, spacing, _, _ in settle.TIERS:
            assert spacing >= MIN_SPACING_CELLS, f"{rank} is sited too close in"

    def test_the_writers_own_towns_are_where_they_put_them(self, built):
        drew = built._settlements_the_writer_drew()
        assert drew, "the example world draws its settlements"
        kept = {s.cell for s in built.settlement.sites if s.entity_id}
        for cell in drew:
            assert cell in kept, f"a town the writer placed at {cell} was moved"


def known_of(generator):
    """The places a road may run between: the writer's, and the ones the map proposed."""
    return [p for p in generator.report.placements if p.entity_id] + \
        generator._already_placed(generator.report.placements)


@pytest.fixture(scope="module")
def network(built):
    if len(known_of(built)) < 3:
        pytest.skip("too few places to make a network")
    return built.road_network(known_of(built))


class TestRoadsBundle:
    def test_roads_share_their_ground(self, network):
        """The point of the exercise: routes that run together become one road."""
        used = collections.Counter()
        for route in network.routes:
            for cell in route.cells:
                used[cell] += 1
        shared = sum(1 for n in used.values() if n > 1)
        assert shared > len(used) * 0.08, (
            f"only {shared} of {len(used)} cells carry more than one route, so nothing "
            "bundled")

    def test_the_network_is_drawn_once(self, network):
        """Each stretch of the network appears in exactly one place, junctions apart."""
        used = collections.Counter()
        for route in network.network:
            for cell in route.cells:
                used[cell] += 1
        twice = sum(1 for n in used.values() if n > 1)
        assert twice < len(used) * 0.15, "the network is drawn on top of itself"

    def test_a_grade_means_traffic(self, network):
        for route in network.routes:
            assert route.grade in {name for name, _ in roads.GRADES}
        by_grade = collections.defaultdict(list)
        for route in network.routes:
            by_grade[route.grade].append(route.traffic)
        if "highway" in by_grade and "track" in by_grade:
            assert min(by_grade["highway"]) > max(by_grade["track"])

    def test_a_road_stays_on_land(self, built, network):
        for route in network.routes:
            for i, j in route.cells:
                assert not built.sea[j][i], "a road across open water"

    def test_the_drawn_road_stays_on_land_too(self, built, network):
        """Cutting a corner must not cut across a bay.

        The line the client draws is not the line the search walked: its corners are cut
        so that a lattice path stops looking like a staircase, and a cut corner moves the
        road off the cells it was made of. On a shore that is exactly where the water is.
        """
        for route in network.routes:
            ends = (known_of(built)[route.joins[0]], known_of(built)[route.joins[1]])
            line = built._road_line(ends[0], list(route.cells), ends[1])
            for (ax, ay), (bx, by) in zip(line, line[1:], strict=False):
                steps = max(2, int(math.dist((ax, ay), (bx, by)) / (CELL * 0.4)))
                for k in range(steps + 1):
                    i, j = built._cell_of(ax + (bx - ax) * k / steps,
                                          ay + (by - ay) * k / steps)
                    assert not built.sea[j][i], f"the drawn road crosses water at {i},{j}"

    def test_the_drawn_road_is_lighter_than_the_path_it_came_from(self, network, built):
        """Smoothing must not cost the client four times the vertices."""
        raw = sum(len(r.cells) for r in network.routes)
        drawn = sum(len(built._road_line(known_of(built)[r.joins[0]], list(r.cells),
                                         known_of(built)[r.joins[1]]))
                    for r in network.routes)
        assert drawn < raw, f"{drawn} drawn vertices against {raw} lattice cells"
