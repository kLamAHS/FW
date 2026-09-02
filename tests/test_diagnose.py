"""Geography a reader would question (V2 §44) — notes, never errors."""

from __future__ import annotations

from types import SimpleNamespace

from fw.core.mapgen import diagnose
from fw.core.mapgen.drafts import FeatureDraft, ShapeSpec
from fw.core.mapgen.generate import GRID


def flat(value=0.0):
    return [[value] * GRID for _ in range(GRID)]


def rig(**over):
    """A generator with exactly the surface diagnose reads, all of it dry and calm."""
    base = dict(
        sea=[[False] * GRID for _ in range(GRID)],
        elevation=flat(0.5),
        channel=set(),
        erosion=SimpleNamespace(flow=flat(0.0)),
        vegetation=SimpleNamespace(marsh=flat(0.0)),
        holds=None,
        frontiers=[],
        owner=[[None] * GRID for _ in range(GRID)],
        profiles={},
        _cell_of=lambda x, y: (int(x), int(y)),
        road_network=lambda known: SimpleNamespace(routes=()),
    )
    base.update(over)
    return SimpleNamespace(**base)


def town(x, y, rank="village", name="Teston", entity_id="e1"):
    return SimpleNamespace(x=float(x), y=float(y), rank=rank, name=name,
                           entity_id=entity_id)


def line(kind, coords, *, role="spine", detail=None):
    return FeatureDraft(
        kind=kind, key_parts=(kind, "t"),
        shapes=(ShapeSpec(role=role, kind="line", coordinates=coords,
                          layer="waterways"),),
        detail=detail or {})


class TestUphillRivers:
    def test_a_river_that_ends_higher_than_it_began_is_called_on_it(self):
        ground = flat(0.5)
        for j in range(GRID):
            ground[j][20] = 0.9                       # the mouth stands on a ridge
        g = rig(elevation=ground)
        said = diagnose._uphill_rivers(
            g, [line("river", [[5.0, 5.0], [20.0, 5.0]],
                     detail={"strahler": 3})])
        assert len(said) == 1
        assert "uphill" in said[0].message or "higher" in said[0].message

    def test_water_running_downhill_is_no_news(self):
        ground = flat(0.5)
        for j in range(GRID):
            ground[j][5] = 0.9                        # the source stands high
        g = rig(elevation=ground)
        assert diagnose._uphill_rivers(
            g, [line("river", [[5.0, 5.0], [20.0, 5.0]])]) == []


class TestWaterlessTowns:
    def test_a_town_in_the_dry_is_asked_what_it_drinks(self):
        flow = flat(0.0)
        for j in range(GRID):
            flow[j][100] = 5.0                        # one river, far away
        g = rig(erosion=SimpleNamespace(flow=flow))
        said = diagnose._waterless_towns(g, [town(20, 20, name="Dryhall")])
        assert len(said) == 1 and "Dryhall" in said[0].message

    def test_a_town_on_a_stream_is_not(self):
        flow = flat(0.0)
        for j in range(GRID):
            flow[j][22] = 5.0
            flow[j][100] = 500.0                      # the big river, elsewhere
        g = rig(erosion=SimpleNamespace(flow=flow))
        assert diagnose._waterless_towns(g, [town(20, 20)]) == []


class TestIsolatedTowns:
    def test_a_city_no_road_or_lane_reaches_is_noted(self):
        g = rig(road_network=lambda known: SimpleNamespace(
            routes=(SimpleNamespace(joins=(0, 1)),)))
        known = [town(10, 10, "village", "A"), town(30, 30, "village", "B"),
                 town(50, 50, "city", "Lonely")]
        said = diagnose._isolated_towns(g, [], known)
        assert len(said) == 1 and "Lonely" in said[0].message

    def test_a_lane_counts_as_being_reached(self):
        g = rig(road_network=lambda known: SimpleNamespace(
            routes=(SimpleNamespace(joins=(0, 1)),)))
        known = [town(10, 10, "village", "A"), town(30, 30, "village", "B"),
                 town(50, 50, "port", "Seahold")]
        lane = FeatureDraft(kind="lane", key_parts=("sail", "x"),
                            detail={"tier": "coastal", "lands_at": "Seahold",
                                    "between": ["A", "Seahold"]})
        assert diagnose._isolated_towns(g, [lane], known) == []

    def test_a_lonely_hamlet_is_just_a_hamlet(self):
        g = rig(road_network=lambda known: SimpleNamespace(routes=()))
        known = [town(10, 10, "hamlet", "A"), town(30, 30, "hamlet", "B")]
        assert diagnose._isolated_towns(g, [], known) == []


class TestFenRoads:
    """Walked over route cells, never the drawn line — a straight fen crossing
    simplifies to its two endpoints and a vertex count misses it entirely."""

    @staticmethod
    def crossing(cells, grade="road"):
        route = SimpleNamespace(cells=tuple(cells), grade=grade, joins=(0, 1))
        return lambda known: SimpleNamespace(routes=(route,))

    def test_a_road_through_open_fen_wants_a_causeway(self):
        marsh = flat(0.0)
        for i in range(30, 40):
            marsh[10][i] = 0.8
        g = rig(vegetation=SimpleNamespace(marsh=marsh),
                road_network=self.crossing([(i, 10) for i in range(25, 45)]))
        known = [town(25, 10, name="Wetford"), town(44, 10, name="Dryhall")]
        said = diagnose._fen_roads(g, known)
        assert len(said) == 1 and "causeway" in said[0].message
        assert "Dryhall" in said[0].message and "Wetford" in said[0].message

    def test_a_road_on_dry_ground_says_nothing(self):
        g = rig(road_network=self.crossing([(i, 10) for i in range(25, 45)]))
        known = [town(25, 10), town(44, 10)]
        assert diagnose._fen_roads(g, known) == []

    def test_one_wet_cell_is_a_puddle_not_a_fen(self):
        marsh = flat(0.0)
        marsh[10][30] = 0.8
        g = rig(vegetation=SimpleNamespace(marsh=marsh),
                road_network=self.crossing([(i, 10) for i in range(25, 45)]))
        known = [town(25, 10), town(44, 10)]
        assert diagnose._fen_roads(g, known) == []


class TestUnwatchedTowers:
    def test_a_march_tower_far_from_any_border_is_noted(self):
        g = rig(holds=SimpleNamespace(sites=[
                    SimpleNamespace(watches="march", cell=(70, 70), rank="tower")]),
                frontiers=[SimpleNamespace(cells=((5, 5), (5, 6)))])
        said = diagnose._unwatched_towers(g)
        assert len(said) == 1 and "march" in said[0].message

    def test_a_tower_on_the_border_watches_it(self):
        g = rig(holds=SimpleNamespace(sites=[
                    SimpleNamespace(watches="march", cell=(6, 6), rank="tower")]),
                frontiers=[SimpleNamespace(cells=((5, 5), (5, 6)))])
        assert diagnose._unwatched_towers(g) == []

    def test_a_world_with_no_borders_asks_nothing_of_its_towers(self):
        g = rig(holds=SimpleNamespace(sites=[
                    SimpleNamespace(watches="march", cell=(70, 70), rank="tower")]))
        assert diagnose._unwatched_towers(g) == []
