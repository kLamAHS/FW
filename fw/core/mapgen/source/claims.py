"""Where a value came from, and how sure of it the map is entitled to be.

Every number the generator builds land from is somebody's claim about the world. The
writer typed "cold, heavy snow" into a region's climate; they wrote "the only pass over
the Kingsback" in a summary; they drew a polygon; or they said nothing at all and the map
had to pick something. Those are not the same kind of fact and must not be averaged as
though they were.

So a value is never a bare float. It is a `Reading` — the value, where it came from, how
much of the writer's own sentence it is entitled to quote back at them, and whether
anything else disagreed. The `Basis` ladder *is* the conflict policy: higher wins, and
when two claims sit on the same rung the earlier one in a content-derived order takes it,
which is what makes the same world read the same way twice.

The version of this that came before could not represent a contested value at all. It was
an if/elif chain that took a token over prose over a default, kept one sentence saying
which branch had fired, and threw the losers away — so a writer who wrote "cold desert"
got a number with no way to discover that their own two words had disagreed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Generic, TypeVar

T = TypeVar("T")


class Basis(IntEnum):
    """Where a value came from. Higher wins; this ladder IS the conflict policy."""

    DEFAULT = 0        # nothing was written, and the map had to choose
    NEIGHBOUR = 1      # inferred from a bordering region
    INHERITED = 2      # from the containing region or realm, through located_in
    MENTION = 3        # prose in some other entity's summary
    SUMMARY = 4        # prose in this entity's own summary
    PROSE_PROP = 5     # this entity's own `terrain` or `climate` fact
    TOKEN = 6          # an exact declared value
    AUTHORED = 7       # the writer drew it or placed it themselves


# How much the map is entitled to lean on each rung. Not evenly spaced: the step from
# saying nothing to saying something offhand is larger than the step from an offhand
# mention to a considered one, and drawing a thing yourself is qualitatively different
# from describing it.
BASIS_CONFIDENCE: dict[Basis, float] = {
    Basis.DEFAULT: 0.0,
    Basis.NEIGHBOUR: 0.25,
    Basis.INHERITED: 0.40,
    Basis.MENTION: 0.55,
    Basis.SUMMARY: 0.70,
    Basis.PROSE_PROP: 0.85,
    Basis.TOKEN: 0.95,
    Basis.AUTHORED: 1.0,
}


@dataclass(frozen=True)
class Claim(Generic[T]):
    """One assertion about one thing, and the words behind it."""

    value: T
    basis: Basis
    because: str                 # a sentence for the writer, in their own words
    quote: str = ""              # the exact prose, so the client can highlight it
    source: str = ""             # "fact:terrain", "summary", "drawn"
    order: int = 0               # content-derived tie-break; NEVER a row id

    @property
    def confidence(self) -> float:
        return BASIS_CONFIDENCE[self.basis]


@dataclass(frozen=True)
class Reading(Generic[T]):
    """A derived value that can explain itself. Never None; always usable.

    `claims` holds every one that was made, winner first, so a stage that wants to say
    "you told me two things here" can, and the plan's findings can quote both.
    """

    value: T
    basis: Basis
    confidence: float
    because: str
    claims: tuple[Claim[T], ...] = ()
    contested: bool = False

    @property
    def is_default(self) -> bool:
        return self.basis is Basis.DEFAULT

    @property
    def stated(self) -> bool:
        """Did the writer say anything at all about this?"""
        return self.basis > Basis.DEFAULT

    @property
    def quote(self) -> str:
        return self.claims[0].quote if self.claims else ""


def settle(claims: list[Claim[T]], *, fallback: T,
           because: str = "you did not say, so the map chose") -> Reading[T]:
    """Resolve competing claims into one value that remembers the others.

    Sorted by rung, then by the claim's own content order — never by the order the rows
    happened to come back in, which is what makes two identically built worlds read the
    same. A claim is *contested* when another claim on the same rung wanted a different
    value: that is a disagreement the writer made and the map may not quietly average
    away, so it is recorded and reported rather than resolved.
    """
    if not claims:
        return Reading(value=fallback, basis=Basis.DEFAULT,
                       confidence=BASIS_CONFIDENCE[Basis.DEFAULT], because=because)
    ranked = sorted(claims, key=lambda c: (-int(c.basis), c.order, str(c.value)))
    winner = ranked[0]
    rivals = [c for c in ranked[1:]
              if c.basis == winner.basis and c.value != winner.value]
    return Reading(value=winner.value, basis=winner.basis,
                   confidence=winner.confidence, because=winner.because,
                   claims=tuple(ranked), contested=bool(rivals))


def known(value: T, basis: Basis, because: str, **kw) -> Reading[T]:
    """A reading with exactly one claim behind it — the common case."""
    claim = Claim(value=value, basis=basis, because=because, **kw)
    return Reading(value=value, basis=basis, confidence=claim.confidence,
                   because=because, claims=(claim,))


def unstated(value: T, because: str = "you did not say, so the map chose") -> Reading[T]:
    return Reading(value=value, basis=Basis.DEFAULT,
                   confidence=BASIS_CONFIDENCE[Basis.DEFAULT], because=because)


@dataclass
class Claims(Generic[T]):
    """A little collector, so a reader can gather claims and settle them at the end."""

    fallback: T
    because: str = "you did not say, so the map chose"
    made: list[Claim[T]] = field(default_factory=list)

    def add(self, value: T, basis: Basis, because: str, **kw) -> None:
        self.made.append(Claim(value=value, basis=basis, because=because,
                               order=len(self.made), **kw))

    def settled(self) -> Reading[T]:
        return settle(self.made, fallback=self.fallback, because=self.because)
