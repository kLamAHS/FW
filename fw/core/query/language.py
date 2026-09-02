"""A question about the world, as data (§49).

The brief calls this one of the application's most important features, and the file it
was to live in was zero bytes. What it asks for is a writer being able to put a question
to their own notes — "which houses serve House Veyne?", "who is related to Lady Mara
within three generations?", "which settlements have no ruler on this date?" — and get an
answer they can trust, save, and ask again next year when the answer has changed.

The shape here is a **structured filter, not a language**. A query is a small tree of
dataclasses that a writer's form fills in and the engine compiles to one SQL statement.
That choice is the whole design:

- A text query language means a parser, a grammar to document, and error messages about
  syntax written for somebody who came here to write a novel.
- A structured filter can be *built by a form*, so every question the engine can answer
  is a question the interface can offer, and there is no way to write one that does not
  parse.
- And it round-trips through JSON without ambiguity, which is what lets a query be saved
  and re-run — the feature that turns a question into a standing one.

Everything is a frozen dataclass with `as_dict`/`from_dict`, because a saved query is
stored in the world file and has to survive the application being upgraded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

# How a fact condition reads the world. `out` is the entity as subject — "House Marr
# *legally owns* the Northmarch"; `in` is the entity as object — "the Northmarch *is
# legally owned by* House Marr". Both directions matter and a query engine offering only
# one answers half the questions anybody has.
DIRECTIONS = ("out", "in")

# What a value condition can do. Deliberately few: every one of these is a thing a
# writer can pick from a dropdown and understand without being told what it means.
TESTS = ("is", "is_not", "contains", "starts_with", "exists", "missing",
         "greater_than", "less_than")

ORDERS = ("name", "type", "created", "existence")

MOST = 500                      # the ceiling on one answer, so a slip cannot hang the app


class QueryError(ValueError):
    """A query that cannot be run, with a sentence a writer can act on."""


@dataclass(frozen=True)
class Condition:
    """One thing that must be true of an entity's facts.

    `predicate` names the relationship or property; `direction` says which end of it the
    entity is; and the test is applied to the object or the value. A condition with no
    test at all asks only that the fact exists, which is how "everything that is sworn to
    anybody" is written.
    """

    predicate: str
    direction: str = "out"
    test: str = "exists"
    value: str = ""                       # what the test compares against
    object_id: str = ""                   # or: the other end must be this entity
    object_type: str = ""                 # or: the other end must be of this type
    strength: tuple[str, ...] = ()        # weak | moderate | strong | very_high | ...
    at: int | None = None                 # the fact must be valid on this day
    negate: bool = False                  # "who has *no* liege"

    def check(self) -> None:
        if not self.predicate:
            raise QueryError("a condition needs a predicate to look at")
        if self.direction not in DIRECTIONS:
            raise QueryError(
                f"a fact is read 'out' from an entity or 'in' to it, not "
                f"{self.direction!r}")
        if self.test not in TESTS:
            raise QueryError(f"{self.test!r} is not something a condition can do "
                             f"(they are: {', '.join(TESTS)})")

    def as_dict(self) -> dict:
        return {"predicate": self.predicate, "direction": self.direction,
                "test": self.test, "value": self.value,
                "object_id": self.object_id, "object_type": self.object_type,
                "strength": list(self.strength), "at": self.at,
                "negate": self.negate}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Condition:
        return cls(
            predicate=str(raw.get("predicate") or ""),
            direction=str(raw.get("direction") or "out"),
            test=str(raw.get("test") or "exists"),
            value=str(raw.get("value") or ""),
            object_id=str(raw.get("object_id") or ""),
            object_type=str(raw.get("object_type") or ""),
            strength=tuple(raw.get("strength") or ()),
            at=raw.get("at"),
            negate=bool(raw.get("negate", False)),
        )


@dataclass(frozen=True)
class Within:
    """Everything within so many steps of one entity, along named relationships.

    §49's own example — "who is related to Lady Mara within three generations?" — is
    this and nothing else, and it cannot be written as a fact condition: three
    generations is a walk, not a join.
    """

    start_id: str
    predicates: tuple[str, ...] = ()
    hops: int = 1
    direction: str = "either"             # out | in | either
    at: int | None = None
    include_start: bool = False

    def check(self) -> None:
        if not self.start_id:
            raise QueryError("a 'within' needs somebody to start from")
        if not self.predicates:
            raise QueryError("a 'within' needs at least one relationship to follow")
        if self.hops < 1:
            raise QueryError("a 'within' of no steps reaches nowhere")
        if self.direction not in (*DIRECTIONS, "either"):
            raise QueryError(f"unknown direction {self.direction!r}")

    def as_dict(self) -> dict:
        return {"start_id": self.start_id, "predicates": list(self.predicates),
                "hops": self.hops, "direction": self.direction, "at": self.at,
                "include_start": self.include_start}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Within:
        return cls(
            start_id=str(raw.get("start_id") or ""),
            predicates=tuple(raw.get("predicates") or ()),
            hops=int(raw.get("hops") or 1),
            direction=str(raw.get("direction") or "either"),
            at=raw.get("at"),
            include_start=bool(raw.get("include_start", False)),
        )


@dataclass(frozen=True)
class Query:
    """One question about the world.

    Every field narrows: an entity has to satisfy all of them. "Any of these" lives
    inside a field — several types, several tags — because that is the shape a form can
    offer, and a general boolean tree is a language again.
    """

    types: tuple[str, ...] = ()           # any of these entity types
    name_contains: str = ""
    tags: tuple[str, ...] = ()            # all of these tags
    confidence: tuple[str, ...] = ()      # any of these confidence levels
    exists_on: int | None = None          # in the world on this day
    began_after: int | None = None
    began_before: int | None = None
    conditions: tuple[Condition, ...] = ()
    within: Within | None = None
    order: str = "name"
    descending: bool = False
    limit: int = 100
    # What the answer explains. Off by default because it costs a second pass, and a
    # writer scanning a list of names does not need the case for each one until they ask.
    explain: bool = False

    def check(self) -> Query:
        """Refuse a query that cannot mean anything, in words rather than a traceback."""
        if self.order not in ORDERS:
            raise QueryError(f"cannot order by {self.order!r} "
                             f"(you can order by: {', '.join(ORDERS)})")
        if self.limit < 1:
            raise QueryError("a query that asks for no rows has no answer")
        if (self.began_after is not None and self.began_before is not None
                and self.began_after > self.began_before):
            raise QueryError("the range begins after it ends")
        for condition in self.conditions:
            condition.check()
        if self.within is not None:
            self.within.check()
        return replace(self, limit=min(self.limit, MOST))

    @property
    def is_empty(self) -> bool:
        """A query with nothing in it. Answering it with the whole world is a trap."""
        return not (self.types or self.name_contains or self.tags or self.confidence
                    or self.conditions or self.within or self.exists_on is not None
                    or self.began_after is not None or self.began_before is not None)

    def as_dict(self) -> dict:
        return {
            "types": list(self.types), "name_contains": self.name_contains,
            "tags": list(self.tags), "confidence": list(self.confidence),
            "exists_on": self.exists_on, "began_after": self.began_after,
            "began_before": self.began_before,
            "conditions": [c.as_dict() for c in self.conditions],
            "within": self.within.as_dict() if self.within else None,
            "order": self.order, "descending": self.descending, "limit": self.limit,
            "explain": self.explain,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Query:
        return cls(
            types=tuple(raw.get("types") or ()),
            name_contains=str(raw.get("name_contains") or ""),
            tags=tuple(raw.get("tags") or ()),
            confidence=tuple(raw.get("confidence") or ()),
            exists_on=raw.get("exists_on"),
            began_after=raw.get("began_after"),
            began_before=raw.get("began_before"),
            conditions=tuple(Condition.from_dict(c)
                             for c in raw.get("conditions") or ()),
            within=(Within.from_dict(raw["within"]) if raw.get("within") else None),
            order=str(raw.get("order") or "name"),
            descending=bool(raw.get("descending", False)),
            limit=int(raw.get("limit") or 100),
            explain=bool(raw.get("explain", False)),
        )


@dataclass(frozen=True)
class Row:
    """One answer, and why it is one."""

    id: str
    name: str
    type_key: str
    summary: str = ""
    confidence: str = "canon"
    exists_from: int | None = None
    exists_to: int | None = None
    because: tuple[str, ...] = ()         # the facts that matched, in the writer's words
    distance: int | None = None           # steps from the start of a `within`

    def as_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "type_key": self.type_key,
                "summary": self.summary, "confidence": self.confidence,
                "exists_from": self.exists_from, "exists_to": self.exists_to,
                "because": list(self.because), "distance": self.distance}


@dataclass(frozen=True)
class Answer:
    """What a query found, and what it did to find it."""

    query: Query
    rows: tuple[Row, ...] = ()
    total: int = 0                        # before the limit
    sql: str = ""                         # shown to anyone who wants to see the working
    params: tuple[Any, ...] = ()
    ms: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def truncated(self) -> bool:
        return self.total > len(self.rows)

    def as_dict(self) -> dict:
        return {"query": self.query.as_dict(),
                "rows": [r.as_dict() for r in self.rows],
                "total": self.total, "truncated": self.truncated,
                "sql": self.sql, "ms": self.ms, "notes": list(self.notes)}


@dataclass(frozen=True)
class Saved:
    """A question the writer wants to keep asking.

    Kept in `app_state`, which is branch-scoped and revision-logged already, so a saved
    query undoes like everything else and a what-if can have its own.
    """

    key: str
    name: str
    query: Query
    note: str = ""

    def as_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "note": self.note,
                "query": self.query.as_dict()}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Saved:
        return cls(key=str(raw.get("key") or ""), name=str(raw.get("name") or ""),
                   query=Query.from_dict(raw.get("query") or {}),
                   note=str(raw.get("note") or ""))


def as_sequence(value: Any) -> Sequence[str]:
    """A field a form may send as one string or as a list, read the same either way."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value else ()
    return tuple(str(v) for v in value)
