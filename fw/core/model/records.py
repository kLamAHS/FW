"""In-memory shapes for what the store holds.

Deliberately plain frozen dataclasses rather than ORM rows: the engines below (succession,
continuity, routing) operate on these, and keeping them free of database machinery is what
lets those engines be tested with hand-built objects and no database at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fw.core.calendar.uncertain import Interval, Precision, UncertainDate

# The tag every generated shape and every entity the map invents carries. Defined here,
# where the entity is, rather than in the generator: three separate parts of the
# application have to be able to tell the map's own suggestions from the writer's world
# — the entity lists, the continuity checks, and the generator's own anti-drift rule —
# and a string literal repeated in three places is a rename waiting to go wrong.
GENERATED_TAG = "generated-map"
PROPOSED_TAG = "proposed"          # the first generator's mark, still in old worlds


@dataclass(frozen=True)
class Entity:
    id: str
    type_key: str
    name: str
    summary: str = ""
    exists_from: int | None = None
    exists_to: int | None = None
    exists_from_hi: int | None = None
    exists_to_lo: int | None = None
    confidence: str = "canon"
    tags: tuple[str, ...] = ()
    branch_id: str = ""
    project_id: str = ""

    @property
    def existence(self) -> Interval:
        return Interval(
            start=UncertainDate(self.exists_from, self.exists_from_hi or self.exists_from),
            end=UncertainDate(self.exists_to_lo or self.exists_to, self.exists_to),
        )

    def exists_on(self, day: int) -> bool:
        """Was this in the world on `day`?

        An entity with no dates exists always — a writer who has not said when a village
        was founded means "it is just there", not "it does not exist".
        """
        if self.exists_from is not None and day < self.exists_from:
            return False
        if self.exists_to is not None and day > self.exists_to:
            return False
        return True

    @property
    def is_a_map_proposal(self) -> bool:
        """Something the map suggested and the writer has not accepted.

        Both halves matter. An entity the writer accepted keeps its `generated-map` tag,
        because that is how the next run recognises its own previous work and does not
        propose the town twice — so the tag alone would hide the writer's own world from
        them. Speculative alone would hide every uncertain thing they wrote themselves.
        """
        if self.confidence != "speculative":
            return False
        return bool({GENERATED_TAG, PROPOSED_TAG} & set(self.tags or ()))

    def certainly_exists_on(self, day: int) -> bool:
        lo = self.exists_from_hi if self.exists_from_hi is not None else self.exists_from
        hi = self.exists_to_lo if self.exists_to_lo is not None else self.exists_to
        if lo is not None and day < lo:
            return False
        if hi is not None and day > hi:
            return False
        return True


@dataclass(frozen=True)
class Fact:
    """One assertion: subject–predicate–(object | value), valid over an interval.

    A property and a relationship differ only in which of `object_id` / `value` is set.
    """

    id: str
    subject_id: str
    predicate_key: str
    object_id: str | None = None
    value: str | None = None
    # A fact about another fact (§33, §57) — e.g. "the Crown's account of this claim".
    about_fact_id: str | None = None
    valid_from: int | None = None
    valid_from_hi: int | None = None
    valid_to: int | None = None
    valid_to_lo: int | None = None
    precision: str = "exact"
    confidence: str = "canon"
    secrecy: str = "public"
    strength: str | None = None
    source_id: str | None = None
    note: str = ""
    props: dict[str, Any] = field(default_factory=dict)
    branch_id: str = ""

    @property
    def is_relationship(self) -> bool:
        return self.object_id is not None

    @property
    def interval(self) -> Interval:
        return Interval(
            start=UncertainDate(
                self.valid_from,
                self.valid_from_hi if self.valid_from_hi is not None else self.valid_from,
                Precision(self.precision) if self.precision in Precision._value2member_map_
                else Precision.EXACT,
            ),
            end=UncertainDate(
                self.valid_to_lo if self.valid_to_lo is not None else self.valid_to,
                self.valid_to,
            ),
        )

    def holds_on(self, day: int) -> bool:
        if self.valid_from is not None and day < self.valid_from:
            return False
        if self.valid_to is not None and day > self.valid_to:
            return False
        return True

    @property
    def is_secret(self) -> bool:
        return self.secrecy in ("secret", "deep_secret")


@dataclass(frozen=True)
class Event:
    id: str
    name: str
    type_key: str = "event"
    summary: str = ""
    start_day: int | None = None
    start_day_hi: int | None = None
    end_day: int | None = None
    end_day_lo: int | None = None
    precision: str = "exact"
    location_id: str | None = None
    confidence: str = "canon"
    secrecy: str = "public"
    entity_id: str | None = None
    props: dict[str, Any] = field(default_factory=dict)

    @property
    def day(self) -> int | None:
        return self.start_day

    def occurs_within(self, first: int, last: int) -> bool:
        start = self.start_day if self.start_day is not None else first
        end = self.end_day if self.end_day is not None else start
        return start <= last and end >= first


@dataclass(frozen=True)
class Title:
    id: str
    name: str
    rank: int = 0
    entity_id: str | None = None
    territory_id: str | None = None
    succession_law: str = "male_preference_primogeniture"
    dynasty_root_id: str | None = None
    created_on: int | None = None
    abolished_on: int | None = None
    props: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TitleHolding:
    id: str
    title_id: str
    holder_id: str
    from_day: int | None = None
    to_day: int | None = None
    how: str = "inheritance"
    disputed: bool = False
    note: str = ""

    def holds_on(self, day: int) -> bool:
        if self.from_day is not None and day < self.from_day:
            return False
        if self.to_day is not None and day > self.to_day:
            return False
        return True


@dataclass(frozen=True)
class Secret:
    id: str
    name: str
    truth: str = ""
    about_id: str | None = None
    fact_id: str | None = None
    severity: str = "major"


@dataclass(frozen=True)
class Interpretation:
    """What one party says about an event or a person (§33, §94).

    The same battle is a liberation to one house and a massacre to another, and the same
    man is the King to one and the Pretender to the other. Both are one shape — a holder,
    a subject, and what the holder calls it — which is why one table carries both and a
    perspective (§94) can read historical interpretation and political labels from the
    same place. Exactly one of `event_id` and `entity_id` is set.

    `holder_id` may be None: "the clerical account" is a version nobody in particular owns,
    and refusing to record it would force the writer to invent a church to hold it.
    """

    id: str
    label: str
    account: str = ""
    event_id: str | None = None
    entity_id: str | None = None
    holder_id: str | None = None


@dataclass(frozen=True)
class Knowledge:
    """Who stands in what relation to a secret (§6).

    `about_observer_id` carries second-order awareness: Sera knows *that Mara knows*, which
    is a different fact from Sera knowing the secret herself, and is exactly the layer the
    brief asks for to make dramatic irony inspectable.
    """

    id: str
    observer_id: str
    secret_id: str
    stance: str
    about_observer_id: str | None = None
    acquired_on: int | None = None
    acquired_from: str | None = None
    scene_id: str | None = None
    note: str = ""


@dataclass(frozen=True)
class Scene:
    id: str
    title: str
    chapter_id: str | None = None
    position: int = 0
    day: int | None = None
    end_day: int | None = None
    location_id: str | None = None
    pov_id: str | None = None
    objective: str = ""
    conflict: str = ""
    outcome: str = ""
    notes: str = ""


@dataclass(frozen=True)
class Geometry:
    id: str
    entity_id: str
    kind: str
    coordinates: Any
    valid_from: int | None = None
    valid_to: int | None = None
    layer: str = "base"
    style: dict[str, Any] = field(default_factory=dict)
    approximate: bool = False
    # Provenance the client never draws — which generation made this shape and which
    # feature of it. `style` is for drawing; this is for knowing.
    props: dict[str, Any] = field(default_factory=dict)

    def holds_on(self, day: int) -> bool:
        if self.valid_from is not None and day < self.valid_from:
            return False
        if self.valid_to is not None and day > self.valid_to:
            return False
        return True


@dataclass(frozen=True)
class RouteSegment:
    id: str
    from_entity_id: str
    to_entity_id: str
    length: float
    medium: str = "road"
    quality: float = 1.0
    terrain: str = "plain"
    entity_id: str | None = None
    built_on: int | None = None
    ruined_on: int | None = None
    closed_seasons: tuple[str, ...] = ()
    danger: str = "low"
    toll_holder_id: str | None = None
    props: dict[str, Any] = field(default_factory=dict)

    def usable_on(self, day: int | None, season: str | None) -> bool:
        if day is not None:
            if self.built_on is not None and day < self.built_on:
                return False
            if self.ruined_on is not None and day > self.ruined_on:
                return False
        if season is not None and season in self.closed_seasons:
            return False
        return True
