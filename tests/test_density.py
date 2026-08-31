"""The develop field: how worked the land is (V2 §7, §14)."""

from __future__ import annotations

from fw.core.mapgen import density

SIZE = 24


def plain(size: int = SIZE) -> list[list[bool]]:
    return [[False] * size for _ in range(size)]


class TestTheGradient:
    def test_it_falls_with_distance_until_the_woods_win(self):
        field = density.develop(SIZE, plain(), [((12, 12), 1.0, 1.0)])
        middle = field[12][12]
        assert middle > 0
        along = [field[12][12 + step] for step in range(int(density.REACH) + 1)]
        assert all(a >= b for a, b in zip(along, along[1:], strict=False)), along
        assert along[-1] == 0.0, "a place's work reaches further than its reach"

    def test_a_capital_works_more_country_than_a_hamlet(self):
        crown = density.develop(SIZE, plain(),
                                [((12, 12), density.WORKED["capital"], 1.0)])
        hamlet = density.develop(SIZE, plain(),
                                 [((12, 12), density.WORKED["hamlet"], 1.0)])
        assert crown[12][12] > hamlet[12][12]
        assert crown[12][16] > hamlet[12][16]

    def test_a_young_town_has_not_cleared_its_valley(self):
        assert density.grown(0) < density.grown(density.MATURE // 2)
        assert density.grown(density.MATURE // 2) < density.grown(density.MATURE)
        assert density.grown(density.MATURE) == density.grown(density.MATURE * 3)

    def test_a_place_with_no_date_is_just_there_not_new(self):
        """The same reading `built_on=None` gets: honestly unknown means mature."""
        assert density.grown(None) == 1.0

    def test_the_sea_is_never_developed(self):
        wet = plain()
        for j in range(SIZE):
            wet[j][0] = True
        field = density.develop(SIZE, wet, [((2, 12), 1.0, 1.0)])
        assert all(field[j][0] == 0.0 for j in range(SIZE))

    def test_traffic_drags_cultivation_along_the_road(self):
        traffic = [[0.0] * SIZE for _ in range(SIZE)]
        for i in range(SIZE):
            traffic[5][i] = 4.0
        field = density.develop(SIZE, plain(), [], traffic=traffic)
        assert field[5][10] > 0.0
        assert field[15][10] == 0.0

    def test_many_old_cities_cannot_push_past_one(self):
        seats = [((12 + di, 12 + dj), 1.0, 1.0)
                 for di in (-1, 0, 1) for dj in (-1, 0, 1)]
        field = density.develop(SIZE, plain(), seats)
        assert max(value for row in field for value in row) == 1.0

    def test_the_same_inputs_give_the_same_field(self):
        seats = [((6, 6), 0.9, 1.0), ((18, 18), 0.3, 0.5)]
        assert (density.develop(SIZE, plain(), seats)
                == density.develop(SIZE, plain(), seats))
