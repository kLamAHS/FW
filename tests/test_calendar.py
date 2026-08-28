"""Calendar and uncertain-date tests (spec §3).

The calendar is the subsystem most likely to be subtly wrong for years without anyone
noticing, so it gets property-based coverage on top of the worked examples.
"""

from __future__ import annotations

from datetime import date as _pydate

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from fw.core.calendar.kernel import (
    GREGORIAN,
    Calendar,
    CalendarError,
    CivilDate,
    Era,
    Month,
    Season,
)
from fw.core.calendar.uncertain import (
    UNKNOWN,
    Interval,
    Precision,
    after,
    before,
    between,
    circa,
    exact,
)

# A deliberately non-Gregorian calendar: nothing about it matches Earth, which is the
# point — the brief insists worlds must not be forced into medieval-European assumptions.
RENNISH = Calendar(
    name="Rennish",
    months=(
        Month("Frostwane", 61), Month("Seedfall", 73), Month("Highsun", 80),
        Month("Harvestide", 73), Month("Darkening", 68),
    ),
    weekdays=("Kingsday", "Mareday", "Orrenday", "Veyneday", "Marrday",
              "Fordday", "Restday", "Hallow", "Emberday", "Lastday"),
    leap_every=4,
    leap_month=1,
    eras=(Era("Age of Founding", "AF", 1, 199), Era("Age of Kings", "AK", 200)),
    seasons=(Season("Deepwinter", 1), Season("Greening", 62), Season("Highsummer", 135),
             Season("Harvest", 215), Season("Fading", 288)),
)


class TestGregorianGroundTruth:
    """The one calendar with an independent oracle: Python's own datetime."""

    EPOCH = _pydate(1, 1, 1).toordinal()

    def test_matches_python_datetime_across_two_centuries(self):
        for n in range(0, 200 * 365, 31):
            real = _pydate.fromordinal(self.EPOCH + n)
            ours = GREGORIAN.from_index(n)
            assert (ours.year, ours.month, ours.day) == (real.year, real.month, real.day)

    def test_leap_rules(self):
        assert GREGORIAN.is_leap_year(2024)
        assert GREGORIAN.is_leap_year(2000)
        assert not GREGORIAN.is_leap_year(1900)
        assert not GREGORIAN.is_leap_year(2023)
        assert GREGORIAN.month_length(2024, 2) == 29
        assert GREGORIAN.month_length(1900, 2) == 28

    def test_weekday(self):
        assert GREGORIAN.weekday(GREGORIAN.date(2024, 1, 1)) == "Monday"
        assert GREGORIAN.weekday(GREGORIAN.date(2000, 1, 1)) == "Saturday"


class TestCustomCalendar:
    def test_year_shape(self):
        assert RENNISH.common_year_days == 355
        assert RENNISH.year_length(4) == 356      # leap
        assert RENNISH.year_length(5) == 355
        assert len(RENNISH.weekdays) == 10

    def test_exhaustive_roundtrip_over_forty_years(self):
        """Every representable day in 40 years converts both ways without loss."""
        for year in range(1, 41):
            for month in range(1, len(RENNISH.months) + 1):
                for day in range(1, RENNISH.month_length(year, month) + 1):
                    civil = CivilDate(year, month, day)
                    assert RENNISH.from_index(RENNISH.to_index(civil)) == civil

    def test_leap_day_is_representable_only_in_leap_years(self):
        assert RENNISH.month_length(4, 1) == 62
        assert RENNISH.to_index(CivilDate(4, 1, 62))
        with pytest.raises(CalendarError):
            RENNISH.to_index(CivilDate(5, 1, 62))

    def test_day_indices_are_contiguous_across_a_year_boundary(self):
        last = RENNISH.to_index(CivilDate(7, 5, 68))
        first = RENNISH.to_index(CivilDate(8, 1, 1))
        assert first == last + 1

    def test_eras(self):
        assert RENNISH.era(150).abbreviation == "AF"
        assert RENNISH.era(312).abbreviation == "AK"
        assert "AK" in RENNISH.format(RENNISH.date(312, 1, 1))

    def test_seasons(self):
        assert RENNISH.season(RENNISH.date(300, 1, 1)) == "Deepwinter"
        assert RENNISH.season(RENNISH.date(300, 3, 1)) == "Highsummer"

    def test_pre_epoch_years(self):
        for year in (0, -1, -4, -100):
            index = RENNISH.date(year, 1, 1)
            assert RENNISH.from_index(index) == CivilDate(year, 1, 1)
            assert index < 0

    def test_rejects_impossible_dates(self):
        with pytest.raises(CalendarError):
            RENNISH.to_index(CivilDate(300, 9, 1))       # no ninth month
        with pytest.raises(CalendarError):
            RENNISH.to_index(CivilDate(300, 1, 99))      # month is only 61 days


class TestCalendarProperties:
    """Property-based: generated calendars, generated dates."""

    @staticmethod
    def _calendar(month_days, leap_every, leap_month):
        return Calendar(
            name="Generated",
            months=tuple(Month(f"M{i}", d) for i, d in enumerate(month_days)),
            leap_every=leap_every,
            leap_month=leap_month,
        )

    @given(
        month_days=st.lists(st.integers(min_value=1, max_value=90), min_size=1, max_size=14),
        leap_every=st.one_of(st.none(), st.integers(min_value=1, max_value=8)),
        year=st.integers(min_value=-60, max_value=400),
        month_pick=st.integers(min_value=0, max_value=13),
        day_pick=st.integers(min_value=0, max_value=200),
    )
    def test_roundtrip_for_any_calendar_and_any_date(
        self, month_days, leap_every, year, month_pick, day_pick
    ):
        cal = self._calendar(month_days, leap_every, leap_month=1)
        month = month_pick % len(cal.months) + 1
        day = day_pick % cal.month_length(year, month) + 1
        civil = CivilDate(year, month, day)
        assert cal.from_index(cal.to_index(civil)) == civil

    @given(
        month_days=st.lists(st.integers(min_value=1, max_value=60), min_size=1, max_size=8),
        leap_every=st.one_of(st.none(), st.integers(min_value=2, max_value=6)),
        index=st.integers(min_value=-40_000, max_value=200_000),
    )
    def test_index_roundtrip_for_any_day(self, month_days, leap_every, index):
        cal = self._calendar(month_days, leap_every, leap_month=1)
        assert cal.to_index(cal.from_index(index)) == index

    @given(
        month_days=st.lists(st.integers(min_value=1, max_value=40), min_size=1, max_size=6),
        leap_every=st.one_of(st.none(), st.integers(min_value=2, max_value=5)),
        year=st.integers(min_value=-30, max_value=200),
    )
    def test_year_length_equals_the_gap_between_year_starts(self, month_days, leap_every, year):
        cal = self._calendar(month_days, leap_every, leap_month=1)
        assert cal.days_before_year(year + 1) - cal.days_before_year(year) == cal.year_length(year)

    @given(
        month_days=st.lists(st.integers(min_value=1, max_value=40), min_size=1, max_size=6),
        a=st.integers(min_value=-5_000, max_value=50_000),
        b=st.integers(min_value=-5_000, max_value=50_000),
    )
    def test_index_order_matches_calendar_order(self, month_days, a, b):
        assume(a != b)
        cal = self._calendar(month_days, leap_every=4, leap_month=1)
        da, db = cal.from_index(a), cal.from_index(b)
        earlier = (da.year, da.month, da.day) < (db.year, db.month, db.day)
        assert earlier == (a < b)


class TestUncertainDates:
    def test_all_forms_sort_on_one_axis(self):
        dates = [
            ("battle", exact(1000)),
            ("born", circa(800, 730)),
            ("founded", between(100, 300)),
            ("treaty", after(1200)),
            ("lost", before(50)),
            ("forgotten", UNKNOWN),
        ]
        order = [name for name, _ in sorted(dates, key=lambda t: t[1].sort_key())]
        assert order.index("lost") < order.index("founded") < order.index("battle")
        # a date nobody recorded belongs at the end of a chronology, not the beginning
        assert order[-1] == "forgotten"

    def test_exactness_and_knownness(self):
        assert exact(5).is_exact
        assert not circa(5, 2).is_exact
        assert not UNKNOWN.is_known
        assert before(10).is_known

    def test_before_and_after_are_strict(self):
        assert not before(10).contains_day(10)
        assert before(10).contains_day(9)
        assert not after(10).contains_day(10)
        assert after(10).contains_day(11)

    def test_definite_versus_possible_ordering(self):
        """This distinction is what gives §46 its severity levels."""
        certainly = exact(100)
        later = exact(200)
        assert certainly.definitely_before(later)
        assert not later.definitely_before(certainly)

        vague_a, vague_b = circa(100, 80), circa(200, 80)
        assert not vague_a.definitely_before(vague_b)   # they might overlap
        assert vague_a.overlaps(vague_b)

    def test_spec_47_contradiction(self):
        """§47: 'Elia died 229 but participated in a battle in 231.'"""
        death, battle = exact(229), exact(231)
        assert death.definitely_before(battle)
        assert not death.overlaps(battle)

    def test_intersect(self):
        assert exact(5).intersect(between(1, 10)) == exact(5)
        assert between(1, 10).intersect(between(5, 20)) == between(5, 10)
        assert between(1, 3).intersect(between(9, 12)) is None

    def test_days_between(self):
        assert exact(10).days_between(exact(13)) == (3, 3)
        # a vague start means the traveller may have had as little as 1 day or as many as 5
        assert circa(10, 2).days_between(exact(13)) == (1, 5)
        assert UNKNOWN.days_between(exact(3)) is None

    def test_rejects_inverted_range(self):
        with pytest.raises(ValueError):
            between(100, 50)

    def test_describe(self):
        assert str(UNKNOWN) == "unknown"
        assert str(exact(5)) == "5"
        assert "c." in str(circa(5, 2))
        assert str(before(10)) == "before 10"
        assert str(after(10)) == "after 10"
        assert circa(5, 2).precision is Precision.CIRCA


class TestInterval:
    def test_holds_on_is_permissive_for_vague_facts(self):
        """A fact with vague dates still surfaces: hiding it would silently mislead."""
        vague = Interval(start=circa(100, 20), end=circa(200, 20))
        assert vague.holds_on(85)          # possible under the earliest reading
        assert not vague.certainly_holds_on(85)
        assert vague.certainly_holds_on(150)

    def test_open_ended_interval_means_still_true(self):
        ongoing = Interval(start=exact(100))
        assert ongoing.holds_on(100)
        assert ongoing.holds_on(10_000)
        assert not ongoing.holds_on(99)

    def test_always(self):
        assert Interval().holds_on(0)
        assert Interval().holds_on(-999)

    def test_overlaps(self):
        a = Interval(start=exact(100), end=exact(200))
        b = Interval(start=exact(150), end=exact(250))
        c = Interval(start=exact(300), end=exact(400))
        assert a.overlaps(b)
        assert not a.overlaps(c)

    def test_str(self):
        assert str(Interval()) == "always"
        assert str(Interval(start=exact(5))) == "from 5"
        assert str(Interval(end=exact(9))) == "until 9"


class TestEraReckoning:
    """§3: a world's own BC/AD — time dividers the writer defines."""

    # Before the Reckoning / After the Reckoning: the same shape as BC/AD, with the
    # earlier era counting backwards and no year zero between them.
    RECKONING = Calendar(
        name="Reckoned",
        months=RENNISH.months,
        weekdays=RENNISH.weekdays,
        leap_every=4,
        eras=(Era("Before the Reckoning", "BR", end_year=0, counts_backward=True),
              Era("After the Reckoning", "AR", start_year=1, reckons_from=1)),
    )

    def test_a_backward_era_counts_the_other_way(self):
        cal = self.RECKONING
        assert cal.era(-99).abbreviation == "BR"
        assert cal.era(-99).year_of(-99) == 100
        assert cal.era(0).year_of(0) == 1           # absolute 0 is 1 BR
        assert cal.era(1).year_of(1) == 1           # absolute 1 is 1 AR

    def test_there_is_no_year_zero_between_the_eras(self):
        """The BC/AD convention falls out of the bounds rather than needing a rule."""
        cal = self.RECKONING
        rendered = [cal.format(cal.date(y, 1, 1)) for y in (-1, 0, 1, 2)]
        assert [r.split()[-2:] for r in rendered] == [
            ["2", "BR"], ["1", "BR"], ["1", "AR"], ["2", "AR"]]

    def test_era_years_round_trip_through_the_day_index(self):
        cal = self.RECKONING
        for era_year, abbrev in ((100, "BR"), (1, "BR"), (1, "AR"), (241, "AR")):
            day = cal.date_in_era(era_year, 2, 3, abbrev)
            year, era = cal.year_in_era(cal.from_index(day).year)
            assert (year, era.abbreviation) == (era_year, abbrev)

    def test_a_backward_era_orders_the_way_time_runs(self):
        """100 BR must be *earlier* than 50 BR, however the numbers read."""
        cal = self.RECKONING
        assert cal.date_in_era(100, 1, 1, "BR") < cal.date_in_era(50, 1, 1, "BR")
        assert cal.date_in_era(50, 1, 1, "BR") < cal.date_in_era(1, 1, 1, "AR")

    def test_parsing_an_unknown_era_says_so(self):
        with pytest.raises(CalendarError, match="no era called"):
            self.RECKONING.absolute_year(5, "ZZ")

    def test_a_label_only_era_keeps_the_absolute_year(self):
        """The existing worlds' behaviour: an era with no reckoning just adds its name."""
        assert RENNISH.era(312).year_of(312) == 312
        assert RENNISH.format(RENNISH.date(312, 1, 1)).endswith("312 AK")

    def test_a_forward_era_can_renumber_from_its_own_founding(self):
        cal = Calendar(name="Founded", months=RENNISH.months,
                       eras=(Era("Age of Founding", "AF", start_year=200,
                                 reckons_from=200),))
        assert cal.era(312).year_of(312) == 113
        assert cal.absolute_year(113, "AF") == 312

    def test_the_narrowest_era_wins_when_they_overlap(self):
        """A regency inside an age is the more specific answer, whatever the row order."""
        cal = Calendar(name="Overlapping", months=RENNISH.months,
                       eras=(Era("Long Age", "LA", start_year=1, end_year=500),
                             Era("The Regency", "RG", start_year=240, end_year=246)))
        assert cal.era(243).abbreviation == "RG"
        assert cal.era(250).abbreviation == "LA"
        flipped = Calendar(name="Overlapping", months=RENNISH.months,
                           eras=tuple(reversed(cal.eras)))
        assert flipped.era(243).abbreviation == "RG"      # order must not decide it

    def test_a_year_outside_every_era_still_renders(self):
        cal = Calendar(name="Gapped", months=RENNISH.months,
                       eras=(Era("Late Age", "LA", start_year=500),))
        assert cal.era(100) is None
        assert cal.format(cal.date(100, 1, 1)).endswith("100")
