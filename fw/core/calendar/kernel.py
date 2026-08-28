"""Fictional calendars (spec §3).

Time is a first-class system rather than a text field. The rule that makes every later
temporal feature possible is that **facts are never stored as a year**: they are stored
against an integer *day index*, and a Calendar converts between that index and an in-world
civil date. A `year` column would make custom months, custom month lengths, leap rules and
eras unimplementable (§3, §60).

Day index 0 is the first day of year 1 of the calendar. Indices before that are negative,
so a world can have history preceding its own epoch.
"""

from __future__ import annotations

from dataclasses import dataclass


class CalendarError(ValueError):
    """A date that this calendar cannot represent."""


@dataclass(frozen=True)
class Month:
    name: str
    days: int
    def __post_init__(self) -> None:
        if self.days < 1:
            raise CalendarError(f"month {self.name!r} must have at least one day")


@dataclass(frozen=True)
class Era:
    """A named span of years — 'Age of Kings', or a world's own equivalent of BC and AD.

    Eras are display-level: they rename years without changing the underlying day index,
    which is why a world can rename its eras, or invent new ones, without rewriting a
    single stored fact.

    Three behaviours, in rising order of ambition:

    - **Label only** (`reckons_from` unset, forward): the year keeps its absolute number
      and the era's abbreviation is appended — "312 AK".
    - **Renumbering** (`reckons_from` set): the era counts its own years from that
      absolute year, so an era reckoning from 200 calls absolute 312 "113".
    - **Backward** (`counts_backward`): years grow as time runs *earlier*, which is what
      makes BC expressible. An era reckoning backwards from 1 calls absolute year 0
      "1 BC" and absolute -99 "100 BC".

    The BC/AD pair's famous missing year zero needs no special rule: with the earlier era
    ending at absolute 0 and reckoning backwards from 1, and the later era starting at
    absolute 1, absolute 0 *is* "1 BC" and absolute 1 *is* "1 AD". A world that would
    rather have a year zero simply starts its forward era at absolute 0.

    `start_year` and `end_year` are inclusive; either may be None for an era that is open
    at that end — which is how "everything before the founding" is said.
    """
    name: str
    abbreviation: str
    start_year: int | None = None
    end_year: int | None = None
    counts_backward: bool = False
    reckons_from: int | None = None

    def contains(self, year: int) -> bool:
        return ((self.start_year is None or year >= self.start_year)
                and (self.end_year is None or year <= self.end_year))

    @property
    def origin(self) -> int:
        """The absolute year this era calls its year 1 (or 0, counting backwards)."""
        if self.reckons_from is not None:
            return self.reckons_from
        if self.counts_backward:
            # An unstated backward reckoning starts just after the era's last year, so
            # the final year of the era is its year 1 — "1 BC" ends the age.
            return (self.end_year + 1) if self.end_year is not None else 1
        return self.start_year if self.start_year is not None else 1

    def year_of(self, year: int) -> int:
        """What this era calls an absolute year."""
        if self.counts_backward:
            return self.origin - year
        if self.reckons_from is None:
            return year            # label-only: the absolute year keeps its number
        return year - self.origin + 1

    def absolute_year(self, era_year: int) -> int:
        """The inverse of `year_of` — what the world stores for a year said in era terms."""
        if self.counts_backward:
            return self.origin - era_year
        if self.reckons_from is None:
            return era_year
        return era_year + self.origin - 1

    @property
    def span(self) -> float:
        """How many years the era covers; infinite when open at either end."""
        if self.start_year is None or self.end_year is None:
            return float("inf")
        return self.end_year - self.start_year


@dataclass(frozen=True)
class Season:
    """A season, defined by the day-of-year on which it begins (1-based)."""
    name: str
    start_day_of_year: int


@dataclass(frozen=True)
class CivilDate:
    """A date as a person in the world would say it."""
    year: int
    month: int          # 1-based
    day: int            # 1-based

    def __str__(self) -> str:
        return f"{self.year}-{self.month:02d}-{self.day:02d}"


@dataclass(frozen=True)
class Calendar:
    """A user-defined calendar (§3, §60).

    `leap_every` / `leap_except` implement Gregorian-style rules: a year is a leap year when
    divisible by `leap_every`, unless divisible by one of `leap_except`, unless divisible by
    one of `leap_always`. Set `leap_every` to None for a calendar with no leap rule at all.
    """

    name: str
    months: tuple[Month, ...]
    weekdays: tuple[str, ...] = ("Firstday", "Secondday", "Thirdday", "Fourthday",
                                 "Fifthday", "Sixthday", "Seventhday")
    leap_every: int | None = None
    leap_except: tuple[int, ...] = ()
    leap_always: tuple[int, ...] = ()
    leap_month: int = 1                      # 1-based month that gains the extra day
    eras: tuple[Era, ...] = ()
    seasons: tuple[Season, ...] = ()
    epoch_weekday: int = 0                   # weekday index of day 0

    def __post_init__(self) -> None:
        if not self.months:
            raise CalendarError("a calendar needs at least one month")
        if not self.weekdays:
            raise CalendarError("a calendar needs at least one weekday")
        if not 1 <= self.leap_month <= len(self.months):
            raise CalendarError(f"leap_month {self.leap_month} is not a month of this calendar")
        if self.leap_every is not None and self.leap_every < 1:
            raise CalendarError("leap_every must be positive")

    # ---- year shape -------------------------------------------------------

    @property
    def common_year_days(self) -> int:
        return sum(m.days for m in self.months)

    def is_leap_year(self, year: int) -> bool:
        if self.leap_every is None:
            return False
        if any(year % n == 0 for n in self.leap_always):
            return True
        if any(year % n == 0 for n in self.leap_except):
            return False
        return year % self.leap_every == 0

    def year_length(self, year: int) -> int:
        return self.common_year_days + (1 if self.is_leap_year(year) else 0)

    def month_length(self, year: int, month: int) -> int:
        self._check_month(month)
        extra = 1 if (self.is_leap_year(year) and month == self.leap_month) else 0
        return self.months[month - 1].days + extra

    def month_name(self, month: int) -> str:
        self._check_month(month)
        return self.months[month - 1].name

    def _check_month(self, month: int) -> None:
        if not 1 <= month <= len(self.months):
            raise CalendarError(f"month {month} out of range 1..{len(self.months)}")

    # ---- conversion -------------------------------------------------------
    #
    # Converting year-by-year would be O(years) and this is on the hot path of every
    # temporal query, so leap years are counted arithmetically instead: the number of
    # leap years in [1, y) is a closed-form inclusion-exclusion over the leap rules.

    def _leaps_before(self, year: int) -> int:
        """How many leap years fall in [1, year).

        Python's floor division makes one expression cover negative years too: for
        year < 1 the result is negative, meaning "leap days to subtract when walking
        backwards from the epoch", which is exactly what days_before_year needs.
        """
        if self.leap_every is None:
            return 0
        total = (year - 1) // self.leap_every
        for ex in self.leap_except:
            total -= (year - 1) // ex
        for al in self.leap_always:
            total += (year - 1) // al
        return total

    def days_before_year(self, year: int) -> int:
        """Day index of the first day of `year`."""
        return (year - 1) * self.common_year_days + self._leaps_before(year)

    def to_index(self, date: CivilDate) -> int:
        """Civil date -> absolute day index. This is what gets stored on every fact."""
        self._check_month(date.month)
        length = self.month_length(date.year, date.month)
        if not 1 <= date.day <= length:
            raise CalendarError(
                f"day {date.day} out of range 1..{length} for "
                f"{self.month_name(date.month)} {date.year}"
            )
        index = self.days_before_year(date.year)
        for m in range(1, date.month):
            index += self.month_length(date.year, m)
        return index + date.day - 1

    def from_index(self, index: int) -> CivilDate:
        """Absolute day index -> civil date."""
        year = self._year_of(index)
        remainder = index - self.days_before_year(year)
        for m in range(1, len(self.months) + 1):
            length = self.month_length(year, m)
            if remainder < length:
                return CivilDate(year, m, remainder + 1)
            remainder -= length
        raise CalendarError(f"could not place day index {index}")  # pragma: no cover

    def _year_of(self, index: int) -> int:
        """Estimate the year arithmetically, then correct by at most a step or two."""
        year = index // self.common_year_days + 1
        while self.days_before_year(year) > index:
            year -= 1
        while self.days_before_year(year + 1) <= index:
            year += 1
        return year

    # ---- convenience ------------------------------------------------------

    def date(self, year: int, month: int = 1, day: int = 1) -> int:
        """Shorthand: `cal.date(312, 4, 2)` -> day index."""
        return self.to_index(CivilDate(year, month, day))

    def year_start(self, year: int) -> int:
        return self.days_before_year(year)

    def year_end(self, year: int) -> int:
        """Day index of the last day of `year`."""
        return self.days_before_year(year + 1) - 1

    def weekday(self, index: int) -> str:
        return self.weekdays[(index + self.epoch_weekday) % len(self.weekdays)]

    def day_of_year(self, index: int) -> int:
        """1-based day within its year."""
        return index - self.days_before_year(self._year_of(index)) + 1

    def season(self, index: int) -> str | None:
        if not self.seasons:
            return None
        doy = self.day_of_year(index)
        ordered = sorted(self.seasons, key=lambda s: s.start_day_of_year)
        current = ordered[-1].name  # before the first boundary we are still in the last season
        for s in ordered:
            if doy >= s.start_day_of_year:
                current = s.name
        return current

    def era(self, year: int) -> Era | None:
        """The era a year belongs to.

        Eras may overlap — a writer can declare a Regency inside an Age — so the rule is
        *most specific wins*: the narrowest era containing the year, then the one that
        starts latest, then declaration order. Picking merely the first match would make
        the answer depend on the order rows came back from the database.
        """
        candidates = [e for e in self.eras if e.contains(year)]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda e: (e.span, -(e.start_year if e.start_year is not None else -10**9)),
        )

    def era_named(self, abbreviation: str) -> Era | None:
        """Look an era up by abbreviation (case-insensitively), for parsing dates."""
        wanted = abbreviation.strip().casefold()
        for e in self.eras:
            if e.abbreviation.casefold() == wanted:
                return e
        return None

    def year_in_era(self, year: int) -> tuple[int, Era | None]:
        """An absolute year as the world says it, with the era that names it."""
        era = self.era(year)
        return (era.year_of(year) if era else year), era

    def absolute_year(self, era_year: int, abbreviation: str | None = None) -> int:
        """Turn a year said in era terms back into the absolute year facts are stored by.

        With no era named, the number is already absolute. This is the parsing half of
        the calendar: without it a writer could read '100 BR' but never type it.
        """
        if not abbreviation:
            return era_year
        era = self.era_named(abbreviation)
        if era is None:
            raise CalendarError(f"this calendar has no era called {abbreviation!r}")
        return era.absolute_year(era_year)

    def date_in_era(self, era_year: int, month: int = 1, day: int = 1,
                    abbreviation: str | None = None) -> int:
        """Day index from a date said in era terms — '3 Frostwane, 100 BR'."""
        return self.to_index(
            CivilDate(self.absolute_year(era_year, abbreviation), month, day))

    def format(self, index: int, *, with_weekday: bool = False, with_era: bool = True) -> str:
        """Render a day index the way the world would say it."""
        d = self.from_index(index)
        year, era = self.year_in_era(d.year) if with_era else (d.year, None)
        text = f"{d.day} {self.month_name(d.month)} {year}"
        if era is not None:
            text += f" {era.abbreviation}"
        if with_weekday:
            text = f"{self.weekday(index)}, {text}"
        return text


GREGORIAN = Calendar(
    name="Gregorian",
    months=(
        Month("January", 31), Month("February", 28), Month("March", 31),
        Month("April", 30), Month("May", 31), Month("June", 30),
        Month("July", 31), Month("August", 31), Month("September", 30),
        Month("October", 31), Month("November", 30), Month("December", 31),
    ),
    weekdays=("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    leap_every=4, leap_except=(100,), leap_always=(400,), leap_month=2,
    seasons=(Season("Winter", 1), Season("Spring", 80), Season("Summer", 172),
             Season("Autumn", 264), Season("Winter", 355)),
)
