"""Uncertain dates (spec §3) and interval reasoning (§46, §47).

The brief requires absolute dates, relative dates, uncertain dates, date ranges,
approximate dates and unknown dates — and requires that they all still sort, still drive
the timeline, and still feed continuity checking. A single representation covers all of
them: a closed interval `[earliest, latest]` of day indices, either end open.

    exact(d)        -> [d, d]
    circa(d, 2y)    -> [d - 2y, d + 2y]
    between(a, b)   -> [a, b]
    before(d)       -> [None, d]
    after(d)        -> [d, None]
    UNKNOWN         -> [None, None]

Continuity work then reduces to interval arithmetic, and — importantly for §46's
requirement that violations have severity levels — the distinction between *certainly*
wrong and *possibly* wrong falls straight out of it. Two intervals that cannot overlap
are a contradiction; two that merely might overlap are a warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

NEG_INF = -(2**62)
POS_INF = 2**62


class Precision(str, Enum):
    """How the date was expressed, kept for display and for phrasing violations."""
    EXACT = "exact"
    CIRCA = "circa"
    RANGE = "range"
    BEFORE = "before"
    AFTER = "after"
    UNKNOWN = "unknown"


@dataclass(frozen=True, order=False)
class UncertainDate:
    """A day index that may not be precisely known.

    `earliest`/`latest` are inclusive bounds in day indices; None means unbounded.
    """

    earliest: int | None
    latest: int | None
    precision: Precision = Precision.EXACT
    note: str = ""

    def __post_init__(self) -> None:
        if self.earliest is not None and self.latest is not None and self.earliest > self.latest:
            raise ValueError(
                f"uncertain date has earliest {self.earliest} after latest {self.latest}"
            )

    # ---- bounds -----------------------------------------------------------

    @property
    def lo(self) -> int:
        return NEG_INF if self.earliest is None else self.earliest

    @property
    def hi(self) -> int:
        return POS_INF if self.latest is None else self.latest

    @property
    def is_known(self) -> bool:
        return self.earliest is not None or self.latest is not None

    @property
    def is_exact(self) -> bool:
        return self.earliest is not None and self.earliest == self.latest

    @property
    def midpoint(self) -> int | None:
        """A single representative day, for placing a marker on a timeline."""
        if self.earliest is not None and self.latest is not None:
            return (self.earliest + self.latest) // 2
        return self.earliest if self.earliest is not None else self.latest

    def sort_key(self) -> tuple[int, int]:
        """Total order over all uncertain dates, so a mixed list still sorts.

        Unknown dates sort last rather than first: a date nobody recorded belongs at the
        end of a chronology, not before the beginning of history.
        """
        if not self.is_known:
            return (POS_INF, POS_INF)
        return (self.lo, self.hi)

    # ---- interval relations ----------------------------------------------

    def overlaps(self, other: UncertainDate) -> bool:
        """Could these refer to the same moment?"""
        return self.lo <= other.hi and other.lo <= self.hi

    def definitely_before(self, other: UncertainDate) -> bool:
        """True only when no reading of either date allows self >= other."""
        return self.hi < other.lo

    def definitely_after(self, other: UncertainDate) -> bool:
        return self.lo > other.hi

    def possibly_before(self, other: UncertainDate) -> bool:
        return self.lo < other.hi

    def contains_day(self, day: int) -> bool:
        return self.lo <= day <= self.hi

    def intersect(self, other: UncertainDate) -> UncertainDate | None:
        """The tightest date consistent with both statements, or None if they conflict."""
        if not self.overlaps(other):
            return None
        lo = max(self.lo, other.lo)
        hi = min(self.hi, other.hi)
        return UncertainDate(
            None if lo == NEG_INF else lo,
            None if hi == POS_INF else hi,
            Precision.EXACT if lo == hi else Precision.RANGE,
        )

    def days_between(self, other: UncertainDate) -> tuple[int, int] | None:
        """(minimum, maximum) days from self to other, or None if either is unbounded.

        This is what lets §46 ask whether a journey fits: the minimum gap is the time the
        traveller is *guaranteed* to have, so a journey longer than that is suspect.
        """
        if not (self.is_known and other.is_known):
            return None
        if self.earliest is None or self.latest is None:
            return None
        if other.earliest is None or other.latest is None:
            return None
        return (other.earliest - self.latest, other.latest - self.earliest)

    def __str__(self) -> str:
        return describe(self)


UNKNOWN = UncertainDate(None, None, Precision.UNKNOWN)


def exact(day: int) -> UncertainDate:
    return UncertainDate(day, day, Precision.EXACT)


def between(first: int, last: int) -> UncertainDate:
    return UncertainDate(first, last, Precision.RANGE)


def before(day: int) -> UncertainDate:
    """Strictly before `day`."""
    return UncertainDate(None, day - 1, Precision.BEFORE)


def after(day: int) -> UncertainDate:
    """Strictly after `day`."""
    return UncertainDate(day + 1, None, Precision.AFTER)


def circa(day: int, slack: int) -> UncertainDate:
    """Approximately `day`, give or take `slack` days either way."""
    if slack < 0:
        raise ValueError("slack must not be negative")
    return UncertainDate(day - slack, day + slack, Precision.CIRCA)


def describe(date: UncertainDate, calendar=None) -> str:
    """Render an uncertain date. With a calendar, renders in-world dates."""
    def fmt(index: int | None) -> str:
        if index is None:
            return "?"
        return calendar.format(index) if calendar is not None else str(index)

    match date.precision:
        case Precision.UNKNOWN:
            return "unknown"
        case Precision.EXACT:
            return fmt(date.earliest)
        case Precision.CIRCA:
            return f"c. {fmt(date.midpoint)}"
        case Precision.BEFORE:
            return f"before {fmt(None if date.latest is None else date.latest + 1)}"
        case Precision.AFTER:
            return f"after {fmt(None if date.earliest is None else date.earliest - 1)}"
        case _:
            return f"{fmt(date.earliest)} to {fmt(date.latest)}"


@dataclass(frozen=True)
class Interval:
    """A span of validity: when a fact was true (§3).

    `start`/`end` are themselves uncertain, because "House Marr held Greyhaven from
    sometime in the 310s until 428" is a perfectly ordinary worldbuilding statement.
    An open `end` means still true.
    """

    start: UncertainDate = UNKNOWN
    end: UncertainDate = UNKNOWN

    @property
    def lo(self) -> int:
        return self.start.lo

    @property
    def hi(self) -> int:
        return self.end.hi

    def holds_on(self, day: int) -> bool:
        """Was this fact true on `day`, on the most permissive reading?

        World-state-at-date uses this. It is deliberately generous: a fact whose dates are
        vague still shows up, because hiding it would silently lie to the writer.
        """
        return self.start.lo <= day <= self.end.hi

    def certainly_holds_on(self, day: int) -> bool:
        """True on every reading of the dates — used when a check must not cry wolf."""
        return self.start.hi <= day <= self.end.lo

    def overlaps(self, other: Interval) -> bool:
        return self.lo <= other.hi and other.lo <= self.hi

    def __str__(self) -> str:
        if not self.start.is_known and not self.end.is_known:
            return "always"
        if not self.end.is_known:
            return f"from {self.start}"
        if not self.start.is_known:
            return f"until {self.end}"
        return f"{self.start} to {self.end}"


ALWAYS = Interval()
