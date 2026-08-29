"""Turning a question into one SQL statement (§49).

The fact spine is `(subject, predicate, object | value, when)`, indexed both ways, and
almost every question a writer has about their notes is a conjunction over it: entities
of some type, with some fact, not having some other fact, alive on some day. That is a
`SELECT` with an `EXISTS` per condition, and SQLite does it in one pass over indexes it
already has.

Three rules hold everything together:

**One statement.** Not a Python loop over `world.facts_where`: a filter that fetches
every candidate and then discards most of them reads the whole world to answer "which
five settlements have no ruler", and gets slower every year the writer works.

**Every parameter is bound.** Nothing a writer types is ever concatenated into SQL.
Predicate and type keys are checked against the vocabulary before they are used, and
the only strings that reach the statement text are ones this module wrote.

**The branch chain is not optional.** Every table reference goes through
`World.branch_scope`, and facts additionally through `World.fact_visibility`, so a query
run on a what-if answers with the what-if's world and not with canon's.
"""

from __future__ import annotations

import time
from typing import Any

from fw.core.model.vocabulary import ENTITY_TYPES_BY_KEY, PREDICATES_BY_KEY
from fw.core.query.language import (
    MOST,
    Answer,
    Condition,
    Query,
    QueryError,
    Row,
    Within,
)

ORDER_SQL = {
    "name": "e.name COLLATE NOCASE",
    "type": "e.type_key, e.name COLLATE NOCASE",
    "created": "e.created_at",
    "existence": "e.exists_from IS NULL, e.exists_from",
}


def run(world, query: Query) -> Answer:
    """Answer one question, in one statement, with the working shown."""
    query = query.check()
    notes: list[str] = []
    if query.is_empty:
        notes.append("This asks nothing in particular, so it is everything in the "
                     "world, newest questions first.")

    started = time.perf_counter()
    reachable: dict[str, int] | None = None
    if query.within is not None:
        reachable = _reach(world, query.within)
        if not reachable:
            return Answer(query=query, rows=(), total=0, sql="", params=(),
                          ms=int((time.perf_counter() - started) * 1000),
                          notes=(*notes, "Nothing is within reach of that."))

    where, params = _clauses(world, query, reachable)
    scope, scope_params = world.branch_scope("e")
    sql = ("SELECT e.id, e.name, e.type_key, e.summary, e.confidence, "
           "e.exists_from, e.exists_to FROM entity e WHERE " + scope)
    bound: list[Any] = list(scope_params)
    for clause, clause_params in zip(where, params, strict=True):
        sql += f" AND {clause}"
        bound.extend(clause_params)

    counted = world.db.scalar(
        f"SELECT count(*) FROM ({sql})", bound)
    sql += (" ORDER BY " + ORDER_SQL[query.order]
            + (" DESC" if query.descending else "") + " LIMIT ?")
    bound.append(min(query.limit, MOST))

    rows = [Row(id=r["id"], name=r["name"], type_key=r["type_key"],
                summary=r["summary"] or "", confidence=r["confidence"],
                exists_from=r["exists_from"], exists_to=r["exists_to"],
                distance=reachable.get(r["id"]) if reachable else None)
            for r in world.db.query(sql, bound)]
    if query.explain and rows:
        rows = _explained(world, query, rows)

    return Answer(query=query, rows=tuple(rows), total=int(counted or 0), sql=sql,
                  params=tuple(bound), notes=tuple(notes),
                  ms=int((time.perf_counter() - started) * 1000))


# ---- the filters -----------------------------------------------------------

def _clauses(world, query: Query,
             reachable: dict[str, int] | None) -> tuple[list[str], list[list[Any]]]:
    where: list[str] = []
    params: list[list[Any]] = []

    if query.types:
        for key in query.types:
            if key not in ENTITY_TYPES_BY_KEY and not _is_custom(world, key):
                raise QueryError(f"there is no such kind of thing as {key!r}")
        where.append("e.type_key IN (" + ",".join("?" for _ in query.types) + ")")
        params.append(list(query.types))

    if query.name_contains:
        where.append("e.name LIKE ? ESCAPE '\\'")
        params.append([f"%{_escaped(query.name_contains)}%"])

    if query.confidence:
        where.append("e.confidence IN (" + ",".join("?" for _ in query.confidence) + ")")
        params.append(list(query.confidence))

    for tag in query.tags:
        # Tags are stored as a JSON array on the row. `EXISTS` over `json_each` is what
        # makes "has this tag" an index-free but linear-in-tags test rather than a
        # substring match, which would find `port` inside `important`.
        where.append("EXISTS (SELECT 1 FROM json_each(e.tags) WHERE json_each.value = ?)")
        params.append([tag])

    if query.exists_on is not None:
        where.append("(e.exists_from IS NULL OR e.exists_from <= ?) "
                     "AND (e.exists_to IS NULL OR e.exists_to >= ?)")
        params.append([query.exists_on, query.exists_on])
    if query.began_after is not None:
        where.append("e.exists_from IS NOT NULL AND e.exists_from >= ?")
        params.append([query.began_after])
    if query.began_before is not None:
        where.append("e.exists_from IS NOT NULL AND e.exists_from <= ?")
        params.append([query.began_before])

    if reachable is not None:
        keys = sorted(reachable)
        where.append("e.id IN (" + ",".join("?" for _ in keys) + ")")
        params.append(keys)

    for condition in query.conditions:
        clause, bound = _fact_clause(world, condition)
        where.append(clause)
        params.append(bound)

    return where, params


def _fact_clause(world, condition: Condition) -> tuple[str, list[Any]]:
    """One `EXISTS` over the fact spine, in the direction asked for."""
    if condition.predicate not in PREDICATES_BY_KEY and not _is_custom_predicate(
            world, condition.predicate):
        raise QueryError(
            f"nothing in this world is recorded with {condition.predicate!r}")

    mine = "f.subject_id" if condition.direction == "out" else "f.object_id"
    theirs = "f.object_id" if condition.direction == "out" else "f.subject_id"

    scope, scope_params = world.branch_scope("f")
    live, live_params = world.fact_visibility("f")
    inner = [f"{mine} = e.id", scope, live, "f.predicate_key = ?"]
    bound: list[Any] = [*scope_params, *live_params, condition.predicate]

    if condition.object_id:
        inner.append(f"{theirs} = ?")
        bound.append(condition.object_id)
    if condition.object_type:
        inner.append(
            f"EXISTS (SELECT 1 FROM entity o WHERE o.id = {theirs} "
            f"AND o.type_key = ?)")
        bound.append(condition.object_type)
    if condition.strength:
        inner.append("f.strength IN ("
                     + ",".join("?" for _ in condition.strength) + ")")
        bound.extend(condition.strength)
    if condition.at is not None:
        inner.append("(f.valid_from IS NULL OR f.valid_from <= ?) "
                     "AND (f.valid_to IS NULL OR f.valid_to >= ?)")
        bound.extend([condition.at, condition.at])

    test, value = condition.test, condition.value
    if test == "is":
        inner.append("f.value = ?")
        bound.append(value)
    elif test == "is_not":
        inner.append("(f.value IS NULL OR f.value <> ?)")
        bound.append(value)
    elif test == "contains":
        inner.append("f.value LIKE ? ESCAPE '\\'")
        bound.append(f"%{_escaped(value)}%")
    elif test == "starts_with":
        inner.append("f.value LIKE ? ESCAPE '\\'")
        bound.append(f"{_escaped(value)}%")
    elif test == "missing":
        inner.append("(f.value IS NULL OR f.value = '')")
    elif test in ("greater_than", "less_than"):
        # A writer's numbers arrive as prose — "41,000", "about 60000" — so the
        # comparison strips everything that is not a digit rather than trusting the
        # column to hold a number. A value with no digits in it simply never matches.
        op = ">" if test == "greater_than" else "<"
        inner.append(
            "CAST(NULLIF(replace(replace(replace(replace(replace(replace(replace("
            "replace(replace(replace(replace(f.value, ',', ''), ' ', ''), 'about', ''),"
            " 'roughly', ''), 'some', ''), 'a', ''), 'e', ''), 'i', ''), 'o', ''),"
            " 'u', ''), '~', ''), '') AS INTEGER) " + op + " ?")
        bound.append(_number(value))

    clause = "EXISTS (SELECT 1 FROM fact f WHERE " + " AND ".join(inner) + ")"
    return (f"NOT {clause}" if condition.negate else clause), bound


def _reach(world, within: Within) -> dict[str, int]:
    """Everything within so many steps, using the world's own walk.

    `World.neighbours` is a breadth-first walk over indexed point lookups and it already
    handles both directions, several predicates at once, cycles and dates — writing a
    second one here to keep the query in a single statement would be a worse walk with
    the same answer.
    """
    for key in within.predicates:
        if key not in PREDICATES_BY_KEY and not _is_custom_predicate(world, key):
            raise QueryError(f"nothing in this world is recorded with {key!r}")
    found = dict(world.neighbours(within.start_id, list(within.predicates),
                                  hops=within.hops, at=within.at))
    if within.direction in ("out", "in"):
        # `neighbours` walks both ways at once, which is right for kinship and wrong for
        # "everyone under House Veyne". A directed walk is `follow`, one predicate at a
        # time, and the shallowest depth wins where two paths meet.
        found = {}
        for key in within.predicates:
            for entity_id, depth in world.follow(
                    within.start_id, key, direction=within.direction,
                    max_depth=within.hops, at=within.at):
                found[entity_id] = min(found.get(entity_id, depth), depth)
    if within.include_start:
        found.setdefault(within.start_id, 0)
    else:
        found.pop(within.start_id, None)
    return found


# ---- saying why ------------------------------------------------------------

def _explained(world, query: Query, rows: list[Row]) -> list[Row]:
    """The facts that made each row an answer, in the writer's own words.

    One query for all the rows at once, not one per row: a hundred answers each fetching
    their own facts is a hundred round trips to say what one statement already knows.
    """
    predicates = [c.predicate for c in query.conditions]
    if not predicates:
        return rows
    ids = [row.id for row in rows]
    scope, scope_params = world.branch_scope("f")
    live, live_params = world.fact_visibility("f")
    sql = (
        "SELECT f.subject_id, f.object_id, f.predicate_key, f.value, "
        "       s.name AS subject_name, o.name AS object_name "
        "FROM fact f "
        "LEFT JOIN entity s ON s.id = f.subject_id "
        "LEFT JOIN entity o ON o.id = f.object_id "
        f"WHERE {scope} AND {live} "
        "AND f.predicate_key IN (" + ",".join("?" for _ in predicates) + ") "
        "AND (f.subject_id IN (" + ",".join("?" for _ in ids) + ") "
        "  OR f.object_id IN (" + ",".join("?" for _ in ids) + ")) "
        "ORDER BY f.predicate_key, f.id")
    bound = [*scope_params, *live_params, *predicates, *ids, *ids]

    said: dict[str, list[str]] = {}
    for record in world.db.query(sql, bound):
        predicate = PREDICATES_BY_KEY.get(record["predicate_key"])
        phrase = predicate.label if predicate else record["predicate_key"]
        tail = record["object_name"] or record["value"] or ""
        for entity_id, sentence in (
                (record["subject_id"], f"{phrase} {tail}".strip()),
                (record["object_id"],
                 f"{record['subject_name'] or ''} {phrase} it".strip())):
            if entity_id in set(ids):
                said.setdefault(entity_id, []).append(sentence)

    return [Row(**{**row.__dict__, "because": tuple(said.get(row.id, ())[:4])})
            for row in rows]


# ---- odds and ends ---------------------------------------------------------

def _escaped(text: str) -> str:
    """A `LIKE` pattern with the writer's own wildcards taken literally.

    Somebody searching for `100%` means a hundred per cent, not "anything at all".
    """
    return (text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))


def _number(text: str) -> int:
    digits = "".join(ch for ch in str(text) if ch.isdigit())
    return int(digits) if digits else 0


def _is_custom(world, type_key: str) -> bool:
    """A type this world defined for itself (§60), which the shared list cannot know."""
    return bool(world.db.one(
        "SELECT 1 FROM entity_type WHERE project_id = ? AND key = ?",
        (world.project_id, type_key)))


def _is_custom_predicate(world, key: str) -> bool:
    return bool(world.db.one(
        "SELECT 1 FROM predicate WHERE project_id = ? AND key = ?",
        (world.project_id, key)))
