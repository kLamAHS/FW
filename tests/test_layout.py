"""Turning "the Northmarch borders the Vale" into a continent's skeleton."""

from __future__ import annotations

import math
import time

from fw.core.mapgen.layout import Site, arrange, spread


def gap(places, a: str, b: str) -> float:
    return math.dist(places[a], places[b])


class TestBordersBecomeAdjacency:
    def test_regions_that_border_end_up_closer_than_regions_that_do_not(self):
        sites = [Site(k) for k in ("alder", "birch", "cedar", "elm")]
        places = arrange(sites, {("alder", "birch"), ("birch", "cedar"),
                                 ("cedar", "elm")})
        assert gap(places, "alder", "birch") < gap(places, "alder", "elm")
        assert gap(places, "cedar", "elm") < gap(places, "alder", "elm")

    def test_a_chain_lays_out_as_a_chain(self):
        """Five regions in a line must not settle into a ring."""
        keys = ["r1", "r2", "r3", "r4", "r5"]
        places = arrange([Site(k) for k in keys],
                         {(keys[i], keys[i + 1]) for i in range(4)})
        # the ends of a chain are further apart than any neighbouring pair
        ends = gap(places, "r1", "r5")
        assert all(gap(places, keys[i], keys[i + 1]) < ends for i in range(4))

    def test_a_hub_sits_among_its_neighbours(self):
        spokes = ["a", "b", "c", "d", "e"]
        places = arrange([Site(k) for k in ["hub", *spokes]],
                         {("hub", s) for s in spokes})
        hx, hy = places["hub"]
        mid_x = sum(places[s][0] for s in spokes) / len(spokes)
        mid_y = sum(places[s][1] for s in spokes) / len(spokes)
        assert math.dist((hx, hy), (mid_x, mid_y)) < spread(places) * 0.2

    def test_two_unconnected_groups_are_two_places(self):
        places = arrange([Site(k) for k in ("a", "b", "c", "d")],
                         {("a", "b"), ("c", "d")})
        assert gap(places, "a", "b") < gap(places, "a", "c")
        assert gap(places, "c", "d") < gap(places, "b", "d")


class TestCoastAndInland:
    def test_a_coastal_region_sits_further_out_than_a_landlocked_one(self):
        sites = [Site("heart"), Site("shore1", coastal=True),
                 Site("shore2", coastal=True), Site("shore3", coastal=True),
                 Site("inland", coastal=False)]
        borders = {("heart", "shore1"), ("heart", "shore2"), ("heart", "shore3"),
                   ("heart", "inland"), ("inland", "shore1")}
        places = arrange(sites, borders)
        cx = sum(p[0] for p in places.values()) / len(places)
        cy = sum(p[1] for p in places.values()) / len(places)
        out = sum(math.dist(places[k], (cx, cy)) for k in
                  ("shore1", "shore2", "shore3")) / 3
        assert math.dist(places["inland"], (cx, cy)) < out


class TestTheWriterSDrawingWins:
    def test_a_region_the_writer_placed_is_never_moved(self):
        sites = [Site("drawn", fixed=(120.0, 340.0)), Site("new1"), Site("new2")]
        places = arrange(sites, {("drawn", "new1"), ("new1", "new2")})
        assert places["drawn"] == (120.0, 340.0)

    def test_generated_regions_settle_around_a_drawn_one(self):
        sites = [Site("drawn", fixed=(400.0, 400.0)), Site("near"), Site("far")]
        places = arrange(sites, {("drawn", "near")})
        assert gap(places, "drawn", "near") < gap(places, "drawn", "far")


class TestItAlwaysAnswers:
    def test_a_single_region_is_placed(self):
        places = arrange([Site("only")], set())
        assert len(places) == 1

    def test_no_regions_is_no_places(self):
        assert arrange([], set()) == {}

    def test_a_world_with_no_borders_still_spreads_out(self):
        keys = [f"r{n}" for n in range(6)]
        places = arrange([Site(k) for k in keys], set())
        for a in range(len(keys)):
            for b in range(a + 1, len(keys)):
                assert gap(places, keys[a], keys[b]) > 1.0

    def test_borders_naming_regions_that_do_not_exist_are_ignored(self):
        places = arrange([Site("a"), Site("b")], {("a", "ghost"), ("a", "b")})
        assert set(places) == {"a", "b"}

    def test_a_region_bordering_itself_does_not_hang(self):
        assert set(arrange([Site("a"), Site("b")], {("a", "a"), ("a", "b")})) == {"a", "b"}

    def test_nothing_lands_outside_the_canvas(self):
        keys = [f"r{n}" for n in range(9)]
        places = arrange([Site(k) for k in keys],
                         {(keys[i], keys[i + 1]) for i in range(8)},
                         span=900.0, margin=60.0)
        assert all(55.0 <= x <= 845.0 and 55.0 <= y <= 845.0
                   for x, y in places.values())


class TestDeterminism:
    def test_the_same_world_lays_out_the_same_way(self):
        sites = [Site(k) for k in ("a", "b", "c", "d", "e")]
        borders = {("a", "b"), ("b", "c"), ("c", "d"), ("d", "e"), ("a", "e")}
        assert arrange(sites, borders, seed="x") == arrange(sites, borders, seed="x")

    def test_the_order_the_regions_arrive_in_does_not_matter(self):
        keys = ["a", "b", "c", "d"]
        borders = {("a", "b"), ("b", "c"), ("c", "d")}
        forward = arrange([Site(k) for k in keys], borders)
        backward = arrange([Site(k) for k in reversed(keys)], borders)
        assert forward == backward

    def test_a_different_seed_gives_a_different_world(self):
        sites = [Site(k) for k in ("a", "b", "c", "d")]
        borders = {("a", "b"), ("b", "c"), ("c", "d")}
        assert arrange(sites, borders, seed="one") != arrange(sites, borders, seed="two")


class TestScale:
    def test_two_hundred_regions_lay_out_inside_the_budget(self):
        keys = [f"r{n:03d}" for n in range(200)]
        borders = {(keys[n], keys[n + 1]) for n in range(199)}
        borders |= {(keys[n], keys[n + 7]) for n in range(193)}
        started = time.perf_counter()
        places = arrange([Site(k) for k in keys], borders)
        elapsed = time.perf_counter() - started
        assert len(places) == 200
        assert elapsed < 4.0, f"layout of 200 regions took {elapsed:.1f}s"
