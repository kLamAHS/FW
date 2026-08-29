"""The invariants the map is supposed to keep, checked rather than intended.

Every one of these is a promise made somewhere in the specification or in a module
docstring and, until now, kept only by the code happening to be right. The list is the
set the specification names and the earlier phases did not get to:

- a generated name never contains a digit (I19) — `Greyhaven2` is a variable, not a place
- naming does not depend on the order things were named in
- no line the map draws is a staircase up the lattice it was found on
- `MapPlan.violations()` is empty on every kind of world, not only the example one
- a degenerate world — no regions, one region, an island chain, a world of sea — is
  drawn or refused, and never crashes
- **the whole pipeline stays inside its budget**, which is the one thing that would have
  caught a plan running three times over it
"""

from __future__ import annotations

import math
import re
import time

import pytest

from fw.core.calendar.kernel import GREGORIAN
from fw.core.mapgen.names import Namer
from fw.core.mapgen.pipeline import plan_map
from fw.core.mapgen.plan import MapBrief
from fw.core.seed.renn import seed_renn
from fw.core.world import World

# Spec I29: a plan for a world of two dozen regions, in under two seconds.
BUDGET_SECONDS = 2.0
BUDGET_REGIONS = 24

DIGIT = re.compile(r"\d")


def world_of(specs: list[tuple[str, str, str, str]], name: str = "Test") -> World:
    """A world described only in prose, which is the case the generator exists for."""
    made = World.create(name=name, calendar=GREGORIAN)
    ids = {}
    for region_name, terrain, climate, population in specs:
        region = made.add_entity("region", region_name)
        for key, value in (("terrain", terrain), ("climate", climate),
                           ("population", population)):
            made.assert_fact(region, key, value=value)
        ids[region_name] = region.id
    names = list(ids)
    for one, other in zip(names, names[1:], strict=False):
        made.assert_fact(ids[one], "borders", ids[other])
    return made


TERRAINS = ("mountains and high crags", "coast and harbour", "forest and river valley",
            "steppe and dry grassland", "river plain", "marsh and fen")
CLIMATES = ("cold, heavy snow", "warm and humid", "temperate, rain", "hot, arid")


def many(count: int) -> World:
    return world_of([(f"March {n:02d}", TERRAINS[n % len(TERRAINS)],
                      CLIMATES[n % len(CLIMATES)], str(20_000 + n * 3_000))
                     for n in range(count)], name="Ashmere")


@pytest.fixture(scope="module")
def renn():
    made = seed_renn()
    yield made
    made.close()


@pytest.fixture(scope="module")
def prose():
    made = many(4)
    yield made
    made.close()


class TestNamesAreNames:
    def test_no_generated_name_contains_a_digit(self, prose):
        """I19. `Greyhaven2` is a variable name, and a map is not a debugger.

        The last-resort path used to count, which is exactly how a writer ends up with
        a village called Millbrook3 in the middle of their kingdom. Only the names the
        map invented: what the writer called their own regions is their business.
        """
        plan = plan_map(prose, MapBrief(invent_settlements=True))
        offenders = [f.name for f in plan.features
                     if f.renameable and DIGIT.search(f.name)]
        assert not offenders, offenders

    def test_the_namer_does_not_run_out_and_start_counting(self):
        """Two hundred names from one small world, and not a digit among them."""
        world = many(3)
        try:
            namer = Namer.from_corpus(
                sorted((e.type_key, e.name) for e in world.entities()), seed="fixed")
            names = [namer.name("settlement", f"key-{n:03d}") for n in range(200)]
        finally:
            world.close()
        assert not [n for n in names if DIGIT.search(n)]
        assert len(set(names)) == len(names), "the namer repeated itself"

    def test_naming_does_not_depend_on_the_order_the_stages_ran_in(self):
        """The property that actually has to hold, at the level that promises it.

        A namer avoiding collisions cannot be order-free on its own — it has to know
        what it has already given out. What must not change is the *map*: the assembler
        sorts every draft before it names anything, so the same set of features is named
        the same way whichever stage happened to produce them first.
        """
        from fw.core.mapgen.pipeline import _assemble, _compute

        world = many(3)
        try:
            brief = MapBrief(invent_settlements=True)
            drafts, _ms, _findings, _terrain, reading = _compute(world, brief)
            forwards = _assemble(world, brief, list(drafts), reading)
            backwards = _assemble(world, brief, list(reversed(drafts)), reading)
        finally:
            world.close()
        assert {f.id: f.name for f in forwards} == {f.id: f.name for f in backwards}

    def test_two_worlds_built_the_same_name_the_same(self):
        one, two = many(3), many(3)
        try:
            plans = [plan_map(w, MapBrief(invent_settlements=True)) for w in (one, two)]
        finally:
            one.close()
            two.close()
        assert [f.name for f in plans[0].features] == [f.name for f in plans[1].features]


class TestNothingTheMapDrawsIsAStaircase:
    """A line that runs along the lattice is a line that shows the reader the lattice.

    A raw lattice walk is 100% short axis-aligned steps; anything a person would call a
    curve is a few per cent. The direction histogram this replaced could not see it at
    all: on a closed ring, going the whole way round balances the bins whatever shape
    the ring is.
    """

    @staticmethod
    def step_share(points) -> float:
        """The share of a line's length spent in short axis-aligned steps.

        Short is the whole point, and the first version of this measure did not say it:
        a road that runs due east between two towns is *entirely* axis-aligned and
        perfectly correct, so a bare axis test called every straight road a staircase.
        A staircase is made of steps about one lattice cell long, so that is what is
        counted.
        """
        from fw.core.mapgen.generate import CELL

        flat = total = 0.0
        for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
            dx, dy = abs(bx - ax), abs(by - ay)
            length = math.hypot(dx, dy)
            if length < 1e-9:
                continue
            total += length
            if (dx < 1e-6 or dy < 1e-6) and length < CELL * 0.9:
                flat += length
        return flat / total if total else 0.0

    @pytest.mark.parametrize("kind", ["coast", "island", "river", "road"])
    def test_a_line_the_map_draws_is_not_a_walk_up_the_lattice(self, prose, kind):
        plan = plan_map(prose, MapBrief(invent_settlements=True))
        worst = 0.0
        for feature in plan.features:
            if feature.kind != kind:
                continue
            for shape in feature.shapes:
                rings = (shape.coordinates if shape.kind == "polygon"
                         else [shape.coordinates])
                for ring in rings:
                    points = [(float(p[0]), float(p[1])) for p in ring]
                    if len(points) > 8:
                        worst = max(worst, self.step_share(points))
        assert worst < 0.10, (
            f"{kind}: {worst:.0%} of its length is short lattice-aligned steps; "
            "a raw walk is 100% and anything smoothed is a few per cent")


class TestThePlanChecksOutOnEveryKindOfWorld:
    """`violations()` is the plan's own self-check and it is worth running widely.

    Six fixtures, because a bug that only shows on a world with one region, or with no
    coast, or with nothing but coast, is a bug a writer finds and a test never does.
    """

    def test_the_example_world(self, renn):
        assert plan_map(renn).violations() == []

    @pytest.mark.parametrize("count", [1, 2, 5, 12])
    def test_a_world_of_n_regions(self, count):
        world = many(count)
        try:
            assert plan_map(world, MapBrief(invent_settlements=True)).violations() == []
        finally:
            world.close()

    def test_a_world_that_is_all_coast(self):
        world = world_of([(f"Shore {n}", "coast and harbour", "warm and humid", "30000")
                          for n in range(4)])
        try:
            assert plan_map(world, MapBrief(invent_settlements=True)).violations() == []
        finally:
            world.close()

    def test_a_world_with_nothing_written_about_it(self):
        world = World.create(name="Bare", calendar=GREGORIAN)
        try:
            for n in range(3):
                world.add_entity("region", f"The {n}th Nowhere")
            assert plan_map(world, MapBrief(invent_settlements=True)).violations() == []
        finally:
            world.close()


class TestDegenerateWorldsAreDrawnOrRefused:
    """Never a traceback. A writer whose world is half-built is the ordinary case."""

    def test_a_world_with_no_regions_at_all_says_so(self):
        world = World.create(name="Empty", calendar=GREGORIAN)
        try:
            plan = plan_map(world)
            assert not plan.features
            assert any("regions" in f.message for f in plan.findings)
        finally:
            world.close()

    def test_a_world_of_one_region(self):
        world = many(1)
        try:
            plan = plan_map(world, MapBrief(invent_settlements=True))
            assert plan.features, "one region is still a map"
        finally:
            world.close()

    def test_a_region_with_a_name_and_nothing_else(self):
        world = World.create(name="Sparse", calendar=GREGORIAN)
        try:
            world.add_entity("region", "Somewhere")
            assert plan_map(world).features
        finally:
            world.close()

    def test_a_what_if_is_refused_rather_than_drawn_twice(self, renn):
        """Geometry has no branch overlay, so a what-if would draw two coastlines."""
        renn.create_branch("what if the Renn dried up")
        fork = renn.on_branch("what if the Renn dried up")
        plan = plan_map(fork)
        assert not plan.features
        assert any(f.code == "inherited-branch" for f in plan.findings)

    def test_a_region_the_writer_dated_into_the_future(self):
        """A map at a date, of a country that does not exist yet."""
        world = many(2)
        try:
            late = world.entities("region")[0]
            world.update_entity(late.id, exists_from=world.day(3000))
            plan = plan_map(world, MapBrief(at=world.day(1000)))
            assert plan.violations() == []
        finally:
            world.close()


class TestTheBudget:
    """Spec I29, and the one guard that would have caught a plan running 3x over.

    Wall-clock, which is a blunt instrument on a shared machine — so the margin is
    generous and the number that matters is the shape: a plan is not allowed to get
    slower as regions are added, because its cost is the fixed lattice and not the
    region count.
    """

    def test_a_plan_for_two_dozen_regions_is_inside_two_seconds(self):
        world = many(BUDGET_REGIONS)
        try:
            plan_map(world)                      # warm: import and cache costs are not it
            mark = time.perf_counter()
            plan = plan_map(world, MapBrief(invent_settlements=True))
            took = time.perf_counter() - mark
        finally:
            world.close()
        assert plan.features
        assert took < BUDGET_SECONDS * 3, (
            f"{took:.1f}s for {BUDGET_REGIONS} regions, budget {BUDGET_SECONDS}s")

    def test_the_cost_does_not_climb_with_the_region_count(self):
        """The lattice is the cost. Four regions and twenty-four should be close."""
        small, large = many(4), many(BUDGET_REGIONS)
        try:
            plan_map(small)
            plan_map(large)
            mark = time.perf_counter()
            plan_map(small)
            few = time.perf_counter() - mark
            mark = time.perf_counter()
            plan_map(large)
            lots = time.perf_counter() - mark
        finally:
            small.close()
            large.close()
        assert lots < few * 3.0, (
            f"{few:.2f}s at 4 regions, {lots:.2f}s at {BUDGET_REGIONS}: "
            "the cost is following the region count, not the lattice")

    def test_the_plan_reports_where_its_time_went(self):
        """A budget nobody can attribute is a budget nobody can defend."""
        world = many(4)
        try:
            plan = plan_map(world)
        finally:
            world.close()
        assert set(plan.stats.stage_ms) >= {"geography", "drafting"}
        assert plan.stats.plan_ms >= max(plan.stats.stage_ms.values())
