"""Growing a map from region attributes (§34, §66, §67, §92)."""

from __future__ import annotations

import pytest

from fw.core.calendar.kernel import GREGORIAN, CivilDate
from fw.core.geo.routing import LAND, Router
from fw.core.mapgen.attributes import (
    ROUTING_TERRAIN,
    TERRAIN_KINDS,
    profile_region,
    read_climate,
    read_terrain,
)
from fw.core.mapgen.generate import (
    CELL,
    GENERATED,
    GENERATOR,
    GRID,
    MIN_SPACING_CELLS,
    MapGenerator,
    generate_map,
)
from fw.core.mapgen.territory import MAX_RING_VERTICES
from fw.core.seed.renn import PRESENT_YEAR
from fw.core.world import World, WorldError


def build_world(name: str = "Ashmere") -> World:
    """A world with regions described in prose and nothing drawn — the common case."""
    w = World.create(name=name, calendar=GREGORIAN)
    specs = [
        ("The Iron Spine", "mountains and high crags", "cold, heavy snow", "60000", "iron"),
        ("The Sunlit Coast", "coast and harbour", "warm and humid", "140000", "fish"),
        ("The Amber Steppe", "steppe and dry grassland", "hot, arid", "40000", "horses"),
        ("Greenhollow", "forest and river valley", "temperate, rain", "90000", "timber"),
    ]
    ids = {}
    for region_name, terrain, climate, population, resource in specs:
        region = w.add_entity("region", region_name)
        w.assert_fact(region, "terrain", value=terrain)
        w.assert_fact(region, "climate", value=climate)
        w.assert_fact(region, "population", value=population)
        w.assert_fact(region, "note", value=resource)
        ids[region_name] = region.id
    w.assert_fact(ids["The Iron Spine"], "borders", ids["Greenhollow"])
    w.assert_fact(ids["The Sunlit Coast"], "borders", ids["Greenhollow"])
    return w


@pytest.fixture
def blank() -> World:
    w = build_world()
    yield w
    w.close()


class TestReadingWhatTheWriterWrote:
    def test_prose_terrain_becomes_weighted_kinds(self):
        assert read_terrain("mountains and forest") == {"mountain": 1.0, "forest": 0.5}
        assert read_terrain("coast and marsh") == {"coast": 1.0, "marsh": 0.5}
        assert read_terrain("") == {}
        assert read_terrain("somewhere indescribable") == {}

    def test_prose_climate_becomes_temperature_and_moisture(self):
        temp, wet = read_climate("cold, heavy snow in Darkening")
        assert temp is not None and temp < -0.3
        hot, dry = read_climate("hot, arid")
        assert hot is not None and hot > 0.3
        assert dry is not None and dry < 0.2
        assert read_climate("") == (None, None)

    def test_a_profile_says_where_every_number_came_from(self, renn: World):
        profile = profile_region(renn, renn.entity_named("The Northmarch").id)
        assert profile.dominant == "mountain"
        assert profile.temperature < 0                      # "cold, heavy snow"
        assert "mountains and forest" in profile.why("terrain")
        assert "Iron" in profile.resources

    def test_an_undescribed_region_says_it_is_defaulting(self, blank: World):
        bare = blank.add_entity("region", "The Blank")
        profile = profile_region(blank, bare.id)
        assert profile.dominant == "plain"
        assert "nothing is recorded" in profile.why("terrain")


class TestGeneratedGeography:
    def test_a_world_of_prose_becomes_a_map(self, blank: World):
        report = generate_map(blank, seed="ashmere")
        assert len(report.regions_drawn) == 4
        assert report.rivers                                  # water found its way down
        assert report.placements
        assert report.roads
        assert "drew 4 regions" in report.summary()

    def test_rivers_never_run_uphill(self, blank: World):
        """Guaranteed by construction — depressions are filled before routing — and
        asserted here because it is the invariant a fictional map is judged on."""
        generator = MapGenerator(blank, seed="ashmere")
        generator.generate()
        for geometry in blank.geometries(layer="waterways"):
            heights = [generator.elevation[j][i]
                       for i, j in (generator._cell_of(x, y)
                                    for x, y in geometry.coordinates)]
            assert all(b <= a + 1e-6 for a, b in zip(heights, heights[1:], strict=False))

    def test_rivers_reach_water_and_are_worth_drawing(self, blank: World):
        generator = MapGenerator(blank, seed="ashmere")
        generator.generate()
        lines = blank.geometries(layer="waterways")
        assert lines
        for geometry in lines:
            assert len(geometry.coordinates) >= 4          # not a puddle

    def test_settlements_do_not_crowd_each_other(self, blank: World):
        report = generate_map(blank, seed="ashmere")
        spots = [(p.x, p.y) for p in report.placements]
        for a in range(len(spots)):
            for b in range(a + 1, len(spots)):
                gap = ((spots[a][0] - spots[b][0]) ** 2
                       + (spots[a][1] - spots[b][1]) ** 2) ** 0.5
                assert gap >= MIN_SPACING_CELLS * CELL - 1.0

    def test_region_outlines_are_closed_rings_the_client_can_draw(self, blank: World):
        generate_map(blank, seed="ashmere")
        for geometry in blank.geometries(layer="regions"):
            rings = geometry.coordinates
            assert isinstance(rings[0][0], list)      # polygon = array of RINGS
            assert rings[0][0] == rings[0][-1]        # closed, as every seeded ring is
            # Bounded, but no longer at the 45 vertices a ray-cast star polygon had:
            # a region is traced from the ground it actually holds now, so its shape is
            # as concave as its territory is and needs more of them to say so.
            assert len(rings[0]) <= MAX_RING_VERTICES

    def test_terrain_actually_changes_the_outcome(self, blank: World):
        """A mountain region and a coast must not generate the same ground."""
        generator = MapGenerator(blank, seed="ashmere")
        generator.generate()
        by_name = {p.name: p for p in generator.profiles.values()}
        spine = by_name["The Iron Spine"]
        coast = by_name["The Sunlit Coast"]
        assert spine.base_elevation > coast.base_elevation + 0.3
        assert spine.roughness > coast.roughness
        assert spine.temperature < coast.temperature

    def test_a_world_with_no_regions_says_what_to_do(self):
        empty = World.create(name="Nothing", calendar=GREGORIAN)
        try:
            with pytest.raises(WorldError, match="a map grows from regions"):
                generate_map(empty)
        finally:
            empty.close()


class TestDeterminism:
    def test_the_same_world_generates_the_same_map(self):
        """Not 'similar' — identical. A golden coordinate test is worthless otherwise."""
        first, second = build_world(), build_world()
        try:
            a = generate_map(first, seed="fixed")
            b = generate_map(second, seed="fixed")
            assert [(p.name, p.x, p.y) for p in a.placements] == \
                   [(p.name, p.x, p.y) for p in b.placements]
            assert ([g.coordinates for g in first.geometries(layer="regions")]
                    == [g.coordinates for g in second.geometries(layer="regions")])
        finally:
            first.close()
            second.close()

    def test_a_different_seed_gives_a_different_map(self):
        first, second = build_world(), build_world()
        try:
            a = generate_map(first, seed="one")
            b = generate_map(second, seed="two")
            assert [(p.x, p.y) for p in a.placements] != [(p.x, p.y) for p in b.placements]
        finally:
            first.close()
            second.close()


class TestItProposesRatherThanOverwrites:
    def test_authored_geometry_is_never_touched(self, renn: World):
        """§66: what the writer drew is truth. The generator builds around it."""
        before = {g.entity_id: g.coordinates
                  for g in renn.geometries()
                  if not (g.style or {}).get(GENERATED)}
        generate_map(renn, at=renn.day(PRESENT_YEAR))
        after = {g.entity_id: g.coordinates
                 for g in renn.geometries()
                 if not (g.style or {}).get(GENERATED)}
        assert before == after

    def test_a_drawn_region_is_kept_and_reported_as_kept(self, renn: World):
        report = generate_map(renn, at=renn.day(PRESENT_YEAR))
        assert set(report.regions_kept) == {
            "The Northmarch", "The Vale of Renn", "The Salt Reach"}
        assert report.regions_drawn == []

    def test_regenerating_replaces_only_its_own_work(self, renn: World):
        day = renn.day(PRESENT_YEAR)
        authored = len([g for g in renn.geometries()
                        if not (g.style or {}).get(GENERATED)])
        generate_map(renn, at=day)
        first = len([g for g in renn.geometries()
                     if (g.style or {}).get(GENERATED) == GENERATOR])
        assert first > 0

        generate_map(renn, at=day)
        again = [g for g in renn.geometries() if (g.style or {}).get(GENERATED) == GENERATOR]
        still_authored = len([g for g in renn.geometries()
                              if not (g.style or {}).get(GENERATED)])
        # the last run's work was swept, not stacked; the writer's is untouched
        assert len(again) == first
        assert still_authored == authored

    def test_proposals_are_marked_as_guesses(self, blank: World):
        report = generate_map(blank, seed="ashmere")
        proposed = [p for p in report.placements if p.proposed]
        assert proposed
        for placement in proposed[:5]:
            entity = blank.get_entity(placement.entity_id)
            assert entity.confidence == "speculative"     # §57: never mistaken for canon
            assert "proposed" in entity.tags
            assert entity.summary.startswith(placement.name)

    def test_a_generated_border_is_drawn_as_uncertain(self, blank: World):
        """§92: fictional maps may be vague, and a computed border certainly is."""
        generate_map(blank, seed="ashmere")
        regions = blank.geometries(layer="regions")
        assert regions
        assert all(g.approximate for g in regions)

    def test_generation_can_be_declined(self, blank: World):
        report = generate_map(blank, seed="ashmere", propose_settlements=False)
        assert [p for p in report.placements if p.proposed] == []
        assert report.regions_drawn                      # the land is still drawn


class TestItIsOneUndoableAction:
    def test_a_whole_map_undoes_in_one_step(self, blank: World):
        """Hundreds of writes must be one action, or the writer's first Ctrl+Z gets a
        single polygon back and the other four hundred stay."""
        generate_map(blank, seed="ashmere")
        assert blank.geometries()

        blank.undo()
        assert blank.geometries() == []
        assert [e for e in blank.entities("settlement")] == []

        blank.redo()
        assert blank.geometries()


class TestExplanations:
    def test_every_placement_argues_for_itself(self, blank: World):
        report = generate_map(blank, seed="ashmere")
        for placement in report.placements:
            sentence = placement.because()
            assert sentence.startswith(placement.name)
            assert sentence.endswith(".")

    def test_the_reasons_name_the_region_s_own_resources(self, blank: World):
        report = generate_map(blank, seed="ashmere")
        spine = [p for p in report.placements
                 if "Iron Spine" in p.name]
        assert spine
        assert any("iron" in r for p in spine for r in p.reasons)

    def test_a_coast_gets_harbours_and_an_inland_plain_does_not(self, renn: World):
        """Authored intent wins over generated geography: a river plain is not a port."""
        report = generate_map(renn, at=renn.day(PRESENT_YEAR))
        vale = [p for p in report.placements if "Vale of Renn" in p.name]
        reach = [p for p in report.placements if "Salt Reach" in p.name]
        assert vale and reach
        assert not any("harbour" in r for p in vale for r in p.reasons)
        assert any("harbour" in r for p in reach for r in p.reasons)


class TestRegenerationIsClean:
    """Regressions from the adversarial review of this slice."""

    def test_rerunning_leaves_no_orphans_behind(self, renn: World):
        """The first draft swept geometry only, so every rerun leaked a river entity
        and duplicated every road segment — forever."""
        day = renn.day(PRESENT_YEAR)
        counts = []
        for _ in range(3):
            generate_map(renn, at=day)
            counts.append((
                len(renn.entities("waterway")),
                len(renn.entities("road")),
                renn.db.scalar("SELECT count(*) FROM route_segment"),
            ))
        assert counts[0] == counts[1] == counts[2]

    def test_a_city_keeps_its_own_shape(self, renn: World):
        """Roads used to hang off the settlement they started from, so asking a city
        for its geometry handed back a road."""
        day = renn.day(PRESENT_YEAR)
        greyhaven = renn.entity_named("Greyhaven")
        generate_map(renn, at=day)
        shape = renn.geometry_for(greyhaven.id)
        assert shape.kind == "point"
        assert shape.layer == "settlements"

    def test_generating_on_a_branch_explains_itself_instead_of_crashing(self, renn: World):
        day = renn.day(PRESENT_YEAR)
        generate_map(renn, at=day)
        renn.create_branch("what if")
        fork = renn.on_branch("what if")
        report = generate_map(fork, at=day)          # must not raise
        assert any("what-if cannot redraw" in note for note in report.notes)

    def test_a_drawn_region_grows_from_its_own_ground(self, renn: World):
        """An authored region was being re-seeded somewhere else entirely, so it ended
        up owning an island far from anything the writer drew."""
        generator = MapGenerator(renn, at=renn.day(PRESENT_YEAR))
        generator.generate()
        for region_id, drawn in generator.authored_cells.items():
            owned = {(i, j) for j in range(GRID) for i in range(GRID)
                     if generator.owner[j][i] == region_id}
            assert owned & drawn, "a region owns no part of what the writer drew"
            # Its mainland territory is one piece, reachable from the drawn ground. The
            # bug this pins gave a region a block of the continent forty cells away with
            # somebody else's country in between.
            reached = set(owned & drawn)
            frontier = list(reached)
            while frontier:
                i, j = frontier.pop()
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    step = (i + di, j + dj)
                    if step in owned and step not in reached:
                        reached.add(step)
                        frontier.append(step)

            # What is left over is allowed, but only if it is genuinely an island. Now
            # that the continent is shaped before anybody is placed on it, it comes with
            # offshore land, and every acre of that is given to somebody — so a region
            # holding a nearby island is the map working, not the map failing. A piece
            # reachable *over land* from the region's main body is a different matter:
            # that is the region's own country, cut in two by a neighbour.
            for cell in sorted(owned - reached):
                walked = {cell}
                stack = [cell]
                joined = False
                while stack and not joined:
                    i, j = stack.pop()
                    for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        step = (i + di, j + dj)
                        if not (0 <= step[0] < GRID and 0 <= step[1] < GRID):
                            continue
                        if generator.sea[step[1]][step[0]] or step in walked:
                            continue
                        if step in reached:
                            joined = True
                            break
                        walked.add(step)
                        stack.append(step)
                assert not joined, (
                    f"{cell} is joined to its region by land but owned across a gap, "
                    "so the region has been cut in two by a neighbour")


class TestRiversAreDrawnByTheWaterInThem:
    """Spec section 52: five widths, chosen by discharge.

    A river drawn one width for its whole length is one of the plainest ways a made map
    differs from a real one, and drawing every river the same width is the other.
    """

    def test_a_river_only_ever_widens(self, renn: World):
        from fw.core.mapgen.pipeline import plan_map

        generator = MapGenerator(renn, at=renn.day(PRESENT_YEAR))
        generator.generate()
        plan = plan_map(renn)
        rivers = plan.by_kind("river")
        assert rivers, "no rivers to measure"
        for river in rivers:
            widths = [shape.style.get("stroke-width") for shape in river.shapes]
            assert all(w is not None for w in widths), "a reach with no width"
            assert widths == sorted(widths), (
                f"{river.name} narrows and widens along its length: {widths}")

    def test_the_reaches_of_a_river_join_up(self, renn: World):
        """Each reach shares an endpoint with the next, or the river has gaps in it."""
        from fw.core.mapgen.pipeline import plan_map

        for river in plan_map(renn).by_kind("river"):
            reaches = list(river.shapes)
            for before, after in zip(reaches, reaches[1:], strict=False):
                assert before.coordinates[-1] == after.coordinates[0], (
                    f"{river.name} has a gap between reaches")

    def test_a_bigger_river_is_drawn_wider_than_a_smaller_one(self, renn: World):
        """The width says how much water, not how far along the river you are."""
        from fw.core.mapgen.pipeline import RIVER_WIDTHS, _band

        assert _band(1.0) == len(RIVER_WIDTHS) - 1
        assert _band(0.0) == 0
        assert _band(0.5) > _band(0.05), "a river ten times the size is drawn the same"
        assert RIVER_WIDTHS == tuple(sorted(RIVER_WIDTHS))


class TestProseIsReadAsWritten:
    def test_ordinary_words_are_not_mistaken_for_terrain(self):
        """'a nice year' was read as arctic, because 'ice' is inside 'nice'."""
        assert read_climate("a nice year") == (None, None)
        assert read_climate("well drained") == (None, None)
        assert read_terrain("orange groves") == {}
        assert "ocean" not in read_terrain("seasonal marshland")

    def test_order_decides_which_terrain_dominates(self):
        assert read_terrain("hills and forest")["hills"] == 1.0
        assert read_terrain("forest and hills")["forest"] == 1.0

    def test_plurals_and_endings_still_count(self):
        assert read_terrain("mountains and forested slopes") == {
            "mountain": 1.0, "forest": 0.5}


class TestGeneratedRoadsAreTravellable:
    """A road on the map that no traveller can use is worse than no road.

    The map's terrain vocabulary is richer than the travel engine's — the map draws
    "hills", the router costs "hill" — and an unknown terrain does not raise: it scores
    zero and the segment is silently dropped from every route. So the whole generated
    network was drawn, and invisible to "how long does it take to get there?".
    """

    def test_every_map_terrain_has_a_travel_equivalent(self):
        assert set(ROUTING_TERRAIN) == set(TERRAIN_KINDS), (
            "a terrain kind the map can draw has no travel cost, so roads through it "
            "vanish from every journey")

    def test_no_travel_equivalent_is_unknown_to_the_router(self):
        unknown = set(ROUTING_TERRAIN.values()) - set(LAND)
        assert not unknown, f"the router has no terrain named {sorted(unknown)}"

    def test_generated_roads_carry_a_terrain_the_router_understands(self, blank: World):
        generate_map(blank)
        segments = [s for s in blank.route_segments() if s.medium == "road"]
        assert segments, "the fixture should have produced roads to check"
        for segment in segments:
            assert segment.terrain in LAND, (
                f"road tagged {segment.terrain!r}, which no land profile can travel")
            assert LAND[segment.terrain] > 0, "a road that is water is nobody's road"

    def test_a_traveller_can_actually_use_a_generated_road(self, blank: World):
        """The end-to-end version: generate, then ask for a journey and get one."""
        report = generate_map(blank)
        placed = [p for p in report.placements if p.entity_id]
        assert len(placed) >= 2
        router = Router(blank)
        journeys = [router.route(a.entity_id, b.entity_id, profile="horse")
                    for a, b in zip(placed, placed[1:], strict=False)]
        assert any(j is not None for j in journeys), (
            "no pair of generated settlements is reachable over the generated roads")

    def test_a_road_is_named_for_the_ground_it_crosses(self, blank: World):
        """Not for the region it starts in — a road out of the mountains is not all
        mountain, and the router charges the whole segment at whatever it is told."""
        generator = MapGenerator(blank, seed="ashmere")
        generator.generate()
        spine = next(rid for rid, p in generator.profiles.items()
                     if p.name == "The Iron Spine")
        crags = [(i, j) for j in range(GRID) for i in range(GRID)
                 if generator.owner[j][i] == spine]
        assert crags
        assert generator._road_terrain(crags) == "mountain"
        assert generator._road_terrain([]) == "plain"

    def test_a_road_over_a_region_named_for_the_sea_is_still_dry_land(self, blank: World):
        """A writer may call a region the Gulf or the Shallows. The road across it is
        still a road, and tagging it `water` would hide it from every land traveller."""
        generator = MapGenerator(blank, seed="ashmere")
        generator.generate()
        drowned = next(iter(sorted(generator.profiles)))
        generator.profiles[drowned].terrain_mix = {"ocean": 1.0}
        cells = [(i, j) for j in range(GRID) for i in range(GRID)
                 if generator.owner[j][i] == drowned]
        assert cells
        assert generator._road_terrain(cells) == "plain"


class TestClosuresUseSeasonNames:
    def test_a_month_name_is_refused_as_a_closed_season(self, renn: World):
        """The pass was shipped closed in "Darkening", which is a month, so it was
        closed on no day of any year while reading as impassable in the notes."""
        a, b = (renn.entity_named("Greyhaven").id, renn.entity_named("Rennford").id)
        with pytest.raises(WorldError) as caught:
            renn.add_route_segment(a, b, 10, closed_seasons=["Darkening"])
        assert "Fading" in str(caught.value)          # says what it should have written

    def test_the_seeded_pass_really_closes(self, renn: World):
        """§115's pass road is described as shut by snow; it has to actually shut."""
        pass_road = renn.entity_named("The Northwatch Pass Road")
        segments = [s for s in renn.route_segments() if s.entity_id == pass_road.id]
        assert segments
        closures = {season for s in segments for season in s.closed_seasons}
        assert closures, "the pass closes in no season at all"
        seasons = {s.name for s in renn.calendar.seasons}
        assert closures <= seasons, f"{closures - seasons} are not seasons"

        # A day in Darkening, the month the pass's own summary says it shuts in.
        winter = renn.calendar.to_index(CivilDate(PRESENT_YEAR, 5, 40))
        assert renn.calendar.season(winter) in closures
        summer = renn.calendar.to_index(CivilDate(PRESENT_YEAR, 3, 20))   # in Highsun
        for segment in segments:
            season = renn.calendar.season(winter)
            if season in segment.closed_seasons:
                assert not segment.usable_on(winter, season)
            assert segment.usable_on(summer, renn.calendar.season(summer))
