"""Turning a field into shapes a map can draw."""

from __future__ import annotations

import math

from fw.core.mapgen import shapes
from fw.core.mapgen.grid import Grid, disc, line


def blob(size: int, cx: float, cy: float, radius: float) -> list[list[float]]:
    """A disc of high ground, so the contour has a known answer."""
    return [[1.0 - math.hypot(i - cx, j - cy) / radius for i in range(size)]
            for j in range(size)]


class TestContours:
    def test_a_disc_contours_to_one_ring_of_about_the_right_size(self):
        field = blob(40, 20, 20, 12)
        rings = shapes.contours(field, 0.5)
        assert len(rings) == 1
        # the 0.5 level of that cone sits at radius 6
        for x, y in rings[0]:
            assert abs(math.hypot(x - 20, y - 20) - 6.0) < 0.8

    def test_two_discs_contour_to_two_rings(self):
        field = [[max(a, b) for a, b in zip(ra, rb, strict=True)]
                 for ra, rb in zip(blob(60, 15, 30, 10), blob(60, 45, 30, 10),
                                   strict=True)]
        assert len(shapes.contours(field, 0.5)) == 2

    def test_a_hole_is_wound_against_the_land_around_it(self):
        """An island with a lake in it: the lake must not be drawn as land."""
        field = blob(60, 30, 30, 25)
        for j in range(60):
            for i in range(60):
                if math.hypot(i - 30, j - 30) < 6:
                    field[j][i] = -1.0            # a lake in the middle
        found = shapes.outlines(field, 0.4, smallest=1.0)
        assert len(found) == 2
        assert found[0][1] is True                # the coast, largest first
        assert found[1][1] is False               # the lake

    def test_a_flat_field_has_no_coastline(self):
        assert shapes.contours([[0.2] * 20 for _ in range(20)], 0.5) == []

    def test_a_degenerate_field_does_not_crash(self):
        assert shapes.contours([], 0.5) == []
        assert shapes.contours([[1.0]], 0.5) == []


class TestTidying:
    def test_simplify_keeps_the_shape_and_drops_the_noise(self):
        straight = [(float(k), 0.0) for k in range(50)]
        assert shapes.simplify(straight, 0.1) == [(0.0, 0.0), (49.0, 0.0)]

    def test_simplify_survives_a_ring_that_would_recurse_once_per_point(self):
        """Written as a stack precisely so a long coast cannot raise RecursionError."""
        spiral = [(k * 0.01, (k % 2) * 0.9) for k in range(4000)]
        assert len(shapes.simplify(spiral, 0.05)) > 100

    def test_smoothing_cuts_corners_without_moving_the_shape(self):
        square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        smooth = shapes.smoothed(square, 2)
        assert len(smooth) == 16
        assert 80.0 < shapes.area(smooth) < 100.0        # rounded, not shrunken away

    def test_a_ring_is_bounded_for_the_browser(self):
        wobbly = [(math.cos(k / 300) * 50 + math.sin(k) * 0.4,
                   math.sin(k / 300) * 50 + math.cos(k) * 0.4) for k in range(1900)]
        assert len(shapes.bounded(wobbly, 240)) <= 240

    def test_closed_rings_repeat_their_first_point(self):
        ring = shapes.closed([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])
        assert ring[0] == ring[-1]


class TestMeasurements:
    def test_area_and_winding(self):
        square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
        assert shapes.area(square) == 16.0
        assert shapes.signed_area(square) > 0
        assert shapes.signed_area(list(reversed(square))) < 0

    def test_centroid_of_a_square_is_its_middle(self):
        square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
        x, y = shapes.centroid(square)
        assert abs(x - 2.0) < 1e-9 and abs(y - 2.0) < 1e-9

    def test_contains_tells_inside_from_outside(self):
        square = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
        assert shapes.contains(square, (2.0, 2.0))
        assert not shapes.contains(square, (5.0, 2.0))

    def test_the_longest_axis_is_the_line_a_label_runs_along(self):
        long_thin = [(0.0, 0.0), (30.0, 1.0), (30.0, 3.0), (0.0, 2.0)]
        a, b = shapes.longest_axis(long_thin)
        assert math.dist(a, b) > 29.0


class TestGrid:
    def test_a_cell_round_trips_to_its_own_centre(self):
        grid = Grid(size=40, span=800.0)
        for i, j in ((0, 0), (17, 3), (39, 39)):
            assert grid.cell_of(*grid.centre(i, j)) == (i, j)

    def test_a_point_outside_the_lattice_is_clamped_not_crashed(self):
        grid = Grid(size=10, span=100.0)
        assert grid.cell_of(-500.0, 9000.0) == (0, 9)

    def test_distance_from_matches_the_straight_line(self):
        grid = Grid(size=60, span=600.0)
        far = grid.distance_from([(30, 30)])
        for i, j in ((30, 30), (35, 30), (30, 44), (12, 51)):
            true = math.hypot(i - 30, j - 30)
            assert abs(far[j][i] - true) <= 0.06 * true + 1e-9

    def test_distance_from_nothing_is_everywhere_infinite(self):
        grid = Grid(size=8, span=80.0)
        assert all(v == math.inf for row in grid.distance_from([]) for v in row)

    def test_the_partition_gives_every_cell_to_someone(self):
        grid = Grid(size=40, span=400.0)
        owner = grid.claimed_by([((5, 5), 1.0), ((35, 8), 1.0), ((20, 34), 1.0)])
        assert all(o >= 0 for row in owner for o in row)
        assert owner[5][5] == 0 and owner[8][35] == 1

    def test_a_faster_seed_claims_more_ground(self):
        grid = Grid(size=40, span=400.0)
        owner = grid.claimed_by([((10, 20), 3.0), ((30, 20), 1.0)])
        counts = [sum(row.count(k) for row in owner) for k in (0, 1)]
        assert counts[0] > counts[1] * 1.5

    def test_the_partition_does_not_depend_on_heap_order(self):
        """Ties break on the seed's index, so adding a distant region must not redraw
        the border between two others."""
        grid = Grid(size=30, span=300.0)
        two = grid.claimed_by([((5, 15), 1.0), ((25, 15), 1.0)])
        assert two[15][14] == 0 and two[15][16] == 1

    def test_the_partition_respects_impassable_ground(self):
        grid = Grid(size=30, span=300.0)
        owner = grid.claimed_by([((2, 15), 1.0), ((27, 15), 1.0)],
                                passable=lambda i, j: i != 15)
        assert owner[15][14] == 0 and owner[15][16] == 1
        assert all(row[15] == -1 for row in owner)

    def test_blurring_pulls_a_spike_down_without_moving_the_flat(self):
        grid = Grid(size=9, span=90.0)
        field = grid.filled(0.0)
        field[4][4] = 12.0
        out = grid.blurred(field)
        assert out[4][4] < 5.0 and out[4][5] > 0.0
        assert out[0][0] == 0.0

    def test_a_disc_and_a_line_seed_the_cells_they_should(self):
        assert (5, 5) in set(disc((5, 5), 2.0))
        assert (9, 5) not in set(disc((5, 5), 2.0))
        drawn = list(line((0, 0), (6, 0)))
        assert drawn[0] == (0, 0) and drawn[-1] == (6, 0) and len(drawn) == 7


class TestAnOpenLineIsEasedWithoutMovingItsEnds:
    def test_both_ends_stay_exactly_where_they_were(self):
        line = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0)]
        out = shapes.eased(line)
        assert out[0] == line[0] and out[-1] == line[-1]

    def test_a_right_angle_stops_being_a_right_angle(self):
        """The corner is what the easing is for: a lattice path is all right angles."""
        line = [(0.0, 0.0), (0.0, 10.0), (10.0, 10.0)]
        out = shapes.eased(line)
        corner = min(math.dist(p, (0.0, 10.0)) for p in out)
        assert corner > 1.0, "the corner is still on the corner"

    def test_it_never_leaves_the_ground_the_line_covered(self):
        """Corner cutting stays inside the hull of what it cuts, so a road cannot
        wander off the country its path crossed."""
        line = [(2.0, 3.0), (2.0, 40.0), (30.0, 40.0), (30.0, 12.0)]
        out = shapes.eased(line, rounds=3)
        assert all(2.0 <= x <= 30.0 and 3.0 <= y <= 40.0 for x, y in out)

    def test_a_line_too_short_to_have_a_corner_is_returned_as_it_is(self):
        assert shapes.eased([(1.0, 1.0), (4.0, 4.0)]) == [(1.0, 1.0), (4.0, 4.0)]
