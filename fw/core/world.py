"""The World: the public surface of the domain core.

Everything above this line (the HTTP API, the CLI, the React client) is an adapter.
Everything below it (succession, continuity, routing, derivation) is a consumer. No engine
reaches into SQL directly; they all come through here, which is what keeps the schema
changeable and the engines testable.

The one method worth reading first is `state_at`. §3 asks for a world-state-at-date query
so that a timeline slider can drive maps, titles, borders, alliances, marriages and living
characters all at once. Because *every* fact carries a validity interval, that query is a
single indexed range scan rather than a special case per subsystem — which is the whole
reason the temporal decision had to be made before any feature work.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import secrets
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fw.core.calendar.kernel import GREGORIAN, Calendar, Era, Month, Season
from fw.core.calendar.uncertain import Interval
from fw.core.ids import new_id
from fw.core.model.records import (
    Entity,
    Event,
    Fact,
    Geometry,
    Knowledge,
    RouteSegment,
    Scene,
    Secret,
    Title,
    TitleHolding,
)
from fw.core.model.vocabulary import (
    ENTITY_TYPES,
    PREDICATES,
    PREDICATES_BY_KEY,
    SCALES,
    inverse_of,
)
from fw.core.store.db import Database, decode_json, encode_json, now_iso
from fw.core.store.fields import pack_fields, unpack_fields


class WorldError(RuntimeError):
    pass


# The entity columns a writer may edit — shared by update_entity and the restore path so
# a restore can never write a column an edit could not.
_ENTITY_EDITABLE = frozenset({
    "name", "summary", "exists_from", "exists_to", "exists_from_hi", "exists_to_lo",
    "confidence", "tags",
})

# How to put back each kind of row a delete cascade destroys. `refs` maps a column to
# (target table, what-to-do-when-the-target-is-missing): 'skip' mirrors ON DELETE
# CASCADE (the row cannot outlive its target), 'null' mirrors ON DELETE SET NULL (the
# row survives with the link cleared). `key` names the columns that identify a row for
# duplicate checks when the table has no `id`; `unique` adds a schema UNIQUE constraint
# the insert would otherwise trip over. Tables absent from this map cannot be restored,
# whatever a crafted revision row claims.
_RESTORE_SPEC: dict[str, dict[str, Any]] = {
    "fact": {"refs": {"subject_id": ("entity", "skip"), "object_id": ("entity", "skip"),
                      "about_fact_id": ("fact", "skip"),
                      "supersedes_id": ("fact", "null"),
                      "source_id": ("source", "null")}},
    "event": {"refs": {"entity_id": ("entity", "skip"),
                       "location_id": ("entity", "null")}},
    "scene": {"refs": {"chapter_id": ("chapter", "null"),
                       "location_id": ("entity", "null"),
                       "pov_id": ("entity", "null")}},
    "title": {"refs": {"entity_id": ("entity", "skip"),
                       "territory_id": ("entity", "null"),
                       "dynasty_root_id": ("entity", "null")}},
    "secret": {"refs": {"about_id": ("entity", "skip"), "fact_id": ("fact", "null")}},
    "geometry": {"refs": {"entity_id": ("entity", "skip")}},
    "app_state": {"refs": {"branch_id": ("branch", "skip")}},
    "route_segment": {"refs": {"entity_id": ("entity", "skip"),
                               "from_entity_id": ("entity", "skip"),
                               "to_entity_id": ("entity", "skip"),
                               "toll_holder_id": ("entity", "null")}},
    "title_holding": {"refs": {"title_id": ("title", "skip"),
                               "holder_id": ("entity", "skip"),
                               "branch_id": ("branch", "skip")}},
    "event_participant": {"refs": {"event_id": ("event", "skip"),
                                   "entity_id": ("entity", "skip")},
                          "key": ("event_id", "entity_id", "role")},
    "scene_participant": {"refs": {"scene_id": ("scene", "skip"),
                                   "entity_id": ("entity", "skip")},
                          "key": ("scene_id", "entity_id")},
    "interpretation": {"refs": {"event_id": ("event", "skip"),
                                "holder_id": ("entity", "skip")}},
    "knowledge_state": {"refs": {"observer_id": ("entity", "skip"),
                                 "secret_id": ("secret", "skip"),
                                 "about_observer_id": ("entity", "skip"),
                                 "acquired_from": ("entity", "null")}},
    "causal_link": {"refs": {"cause_id": ("event", "skip"),
                             "effect_id": ("event", "skip"),
                             "branch_id": ("branch", "null")},
                    "unique": ("cause_id", "effect_id", "branch_id")},
    "entity_override": {"refs": {"branch_id": ("branch", "skip"),
                                 "entity_id": ("entity", "skip")},
                        "key": ("branch_id", "entity_id")},
    "era": {"refs": {"calendar_id": ("calendar", "skip")}},
}

# Tables whose updates undo by writing the previous values straight back. Anything
# here must be in _RESTORE_SPEC too, so its inserts and deletes invert as well.
_PLAIN_UPDATABLE = ("era", "app_state")

# Parents before children, so an event exists again before its participants do. Facts
# are handled separately (they can be about each other and need a fixpoint).
_RESTORE_ORDER = ("event", "title", "secret", "scene", "geometry", "route_segment",
                  "title_holding", "event_participant", "scene_participant",
                  "interpretation", "knowledge_state", "causal_link",
                  "entity_override", "era", "app_state")

# How undo's toast names a change to each kind of row.
_ACTION_NOUNS: dict[str, str] = {
    "event": "the event", "scene": "the scene", "title": "the title",
    "secret": "the secret", "title_holding": "a title grant",
    "knowledge_state": "a knowledge note", "geometry": "map geometry",
    "route_segment": "a route segment", "causal_link": "a causal link",
    "event_participant": "an event participation",
    "scene_participant": "a scene participation",
    "interpretation": "an interpretation",
    "app_state": "a map decision",
}

# What the FK cascade takes with a row of each table — the children a raw DELETE must
# snapshot into the log first, or they would be the one loss the log cannot repair.
_CHILD_TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "event": (("event_participant", "event_id"), ("causal_link", "cause_id"),
              ("causal_link", "effect_id"), ("interpretation", "event_id")),
    "title": (("title_holding", "title_id"),),
    "secret": (("knowledge_state", "secret_id"),),
    "scene": (("scene_participant", "scene_id"),),
}

# SET NULL columns on rows that *survive* a delete: the cascade clears the reference
# but keeps the row, so a restore should offer to re-link it. Only these (table,
# column) pairs may ever be relinked, whatever a crafted revision row claims.
_RELINK_COLUMNS: dict[str, dict[str, str]] = {
    "scene": {"location_id": "entity", "pov_id": "entity"},
    "event": {"location_id": "entity"},
    "title": {"territory_id": "entity", "dynasty_root_id": "entity"},
    "knowledge_state": {"acquired_from": "entity"},
    "route_segment": {"toll_holder_id": "entity"},
    "secret": {"fact_id": "fact"},
}


def _row_key(table: str, row: Any) -> str:
    """The revision-log row_id for a row — its id, or the joined composite key."""
    key = _RESTORE_SPEC.get(table, {}).get("key", ("id",))
    return "/".join(str(row[column]) for column in key)


def _chunked(values: Iterable[Any], size: int = 500) -> Iterable[list[Any]]:
    """Split ids into groups small enough for one IN (...) list.

    SQLITE_MAX_VARIABLE_NUMBER is 999 on many builds; one flat list over every fact
    touching a hub entity would make that entity impossible to delete at exactly the
    scale (§99) where deletion matters most.
    """
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _rows_in(db: Database, table: str, column: str, ids: Iterable[Any]) -> list[Any]:
    """SELECT * WHERE column IN (ids), chunked — the one spelling of that query."""
    out: list[Any] = []
    for chunk in _chunked(ids):
        marks = ",".join("?" for _ in chunk)
        out += db.query(f"SELECT * FROM {table} WHERE {column} IN ({marks})", chunk)
    return out


@dataclass
class StateAtDate:
    """A snapshot of what was true on one day (§3, §36)."""

    day: int
    entities: dict[str, Entity]
    facts: list[Fact]
    titles: dict[str, str | None] = field(default_factory=dict)  # title_id -> holder_id

    def facts_by_predicate(self, key: str) -> list[Fact]:
        return [f for f in self.facts if f.predicate_key == key]

    def living(self) -> list[Entity]:
        return [e for e in self.entities.values() if e.type_key == "person"]


class World:
    """One project inside one world file."""

    def __init__(self, db: Database, project_id: str, branch_id: str) -> None:
        self.db = db
        self.project_id = project_id
        self.branch_id = branch_id
        self._calendar: Calendar | None = None
        # §105: a branch reads its whole ancestor chain, overlaid with its own rows.
        # The chain is ordered nearest-first (this branch, its parent, … canon), which
        # is also the precedence order for entity overrides.
        chain: list[str] = [branch_id]
        cursor = branch_id
        while True:
            row = self.db.one("SELECT parent_id FROM branch WHERE id = ?", (cursor,))
            parent = row["parent_id"] if row else None
            if not parent or parent in chain:
                break
            chain.append(parent)
            cursor = parent
        self._chain: tuple[str, ...] = tuple(chain)
        self._in_chain = "branch_id IN (" + ",".join("?" for _ in chain) + ")"
        # Undo state. Actions are grouped in the log by action_id; inversions announce
        # themselves with a marker record, so which actions currently stand undone is
        # reconstructed from the file itself — undo history survives a restart.
        # The token must be genuinely random: a ULID prefix is a millisecond timestamp,
        # and two handles opened in the same millisecond would then weave their
        # transactions into each other's actions.
        self._session_token = secrets.token_hex(6)
        self._undone: set[str] = set()                  # action ids currently undone
        self._redo: list[tuple[str, str]] = []          # (target, its inversion)
        self._inversions: dict[str, str] = {}           # inversion action -> target
        self._load_undo_state()

    # ---- lifecycle --------------------------------------------------------

    @classmethod
    def create(
        cls,
        path: str | Path = ":memory:",
        *,
        name: str = "Untitled world",
        description: str = "",
        calendar: Calendar | None = None,
    ) -> World:
        db = Database(path)
        project_id, branch_id = new_id(), new_id()
        stamp = now_iso()
        with db.transaction():
            db.insert("project", {
                "id": project_id, "name": name, "description": description,
                "created_at": stamp, "updated_at": stamp,
            })
            db.insert("branch", {
                "id": branch_id, "project_id": project_id, "name": "canon",
                "is_canon": 1, "created_at": stamp,
            })
            world = cls(db, project_id, branch_id)
            world._install_vocabulary()
            world.set_calendar(calendar or GREGORIAN)
        db.analyze()
        return world

    @classmethod
    def open(cls, path: str | Path, *, branch: str = "canon",
             sync: bool = True) -> World:
        db = Database(path, create=False)
        row = db.one("SELECT id FROM project ORDER BY created_at LIMIT 1")
        if row is None:
            raise WorldError(f"{path} contains no project")
        project_id = row["id"]
        branch_row = db.one(
            "SELECT id FROM branch WHERE project_id = ? AND name = ?", (project_id, branch)
        )
        if branch_row is None:
            raise WorldError(f"no branch named {branch!r}")
        world = cls(db, project_id, branch_row["id"])
        if sync:
            # Off when a world is only being *looked at*: the launcher opens every save
            # to list it, and a vocabulary top-up there would rewrite — and re-sort —
            # the writer's whole library just by rendering the screen.
            world.sync_vocabulary()
        return world

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> World:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def name(self) -> str:
        return self.db.scalar("SELECT name FROM project WHERE id = ?", (self.project_id,))

    def sync_vocabulary(self) -> int:
        """Add builtin types and predicates a world was created before.

        The default vocabulary grows as the application does, and a world written last
        year should not be locked out of a predicate this year's build knows about —
        that would make "unknown predicate" the reward for having started early. Only
        *missing* keys are inserted, so a writer's edits to a builtin row, and every
        row they invented themselves, are left exactly alone.
        """
        added = 0
        have_types = {r["key"] for r in self.db.query(
            "SELECT key FROM entity_type WHERE project_id = ?", (self.project_id,))}
        missing_types = [t for t in ENTITY_TYPES if t.key not in have_types]
        if missing_types:
            self.db.insert_many("entity_type", [{
                "id": new_id(), "project_id": self.project_id, "key": t.key,
                "label": t.label, "plural": t.plural, "category": t.category,
                "icon": t.icon, "core_fields": list(t.core_fields), "is_builtin": 1,
            } for t in missing_types])
            added += len(missing_types)

        have_scales = {r["key"] for r in self.db.query(
            "SELECT key FROM scale WHERE project_id = ?", (self.project_id,))}
        missing_scales = [s for s in SCALES if s.key not in have_scales]
        if missing_scales:
            self.db.insert_many("scale", [{
                "id": new_id(), "project_id": self.project_id, "key": s.key,
                "label": s.label, "steps": list(s.steps),
            } for s in missing_scales])
            added += len(missing_scales)

        have_predicates = {r["key"] for r in self.db.query(
            "SELECT key FROM predicate WHERE project_id = ?", (self.project_id,))}
        missing_predicates = [p for p in PREDICATES if p.key not in have_predicates]
        if missing_predicates:
            self.db.insert_many("predicate", [{
                "id": new_id(), "project_id": self.project_id, "key": p.key,
                "label": p.label, "kind": p.kind, "inverse_key": p.inverse_key,
                "symmetric": int(p.symmetric), "transitive": int(p.transitive),
                "datatype": p.datatype, "scale_key": p.scale_key,
                "domain_type_keys": list(p.domain_type_keys),
                "range_type_keys": list(p.range_type_keys), "category": p.category,
                "description": p.description, "is_builtin": 1,
            } for p in missing_predicates])
            added += len(missing_predicates)
        return added

    def _install_vocabulary(self) -> None:
        """Seed the starting types, predicates and scales. All of them editable after."""
        self.db.insert_many("entity_type", [{
            "id": new_id(), "project_id": self.project_id, "key": t.key, "label": t.label,
            "plural": t.plural, "category": t.category, "icon": t.icon,
            "core_fields": list(t.core_fields), "is_builtin": 1,
        } for t in ENTITY_TYPES])

        self.db.insert_many("scale", [{
            "id": new_id(), "project_id": self.project_id, "key": s.key,
            "label": s.label, "steps": list(s.steps),
        } for s in SCALES])

        self.db.insert_many("predicate", [{
            "id": new_id(), "project_id": self.project_id, "key": p.key, "label": p.label,
            "kind": p.kind, "inverse_key": p.inverse_key, "symmetric": int(p.symmetric),
            "transitive": int(p.transitive), "datatype": p.datatype,
            "scale_key": p.scale_key, "domain_type_keys": list(p.domain_type_keys),
            "range_type_keys": list(p.range_type_keys), "category": p.category,
            "description": p.description, "is_builtin": 1,
        } for p in PREDICATES])

    # ---- calendar ---------------------------------------------------------

    def set_calendar(self, calendar: Calendar) -> None:
        cal_id = new_id()
        with self.db.transaction():
            self.db.insert("calendar", {
                "id": cal_id, "project_id": self.project_id, "name": calendar.name,
                "weekdays": list(calendar.weekdays), "leap_every": calendar.leap_every,
                "leap_except": list(calendar.leap_except),
                "leap_always": list(calendar.leap_always),
                "leap_month": calendar.leap_month,
                "epoch_weekday": calendar.epoch_weekday,
                "seasons": [{"name": s.name, "start": s.start_day_of_year}
                            for s in calendar.seasons],
            })
            self.db.insert_many("calendar_month", [{
                "calendar_id": cal_id, "position": i + 1, "name": m.name, "days": m.days,
            } for i, m in enumerate(calendar.months)])
            self.db.insert_many("era", [{
                "id": new_id(), "calendar_id": cal_id, "name": e.name,
                "abbreviation": e.abbreviation, "start_year": e.start_year,
                "end_year": e.end_year,
                "counts_backward": int(e.counts_backward),
                "reckons_from": e.reckons_from,
            } for e in calendar.eras])
            self.db.update("project", self.project_id,
                           {"calendar_id": cal_id, "updated_at": now_iso()})
        self._calendar = calendar

    @property
    def calendar(self) -> Calendar:
        if self._calendar is None:
            self._calendar = self._load_calendar()
        return self._calendar

    def _load_calendar(self) -> Calendar:
        cal_id = self.db.scalar(
            "SELECT calendar_id FROM project WHERE id = ?", (self.project_id,)
        )
        if cal_id is None:
            return GREGORIAN
        row = self.db.one("SELECT * FROM calendar WHERE id = ?", (cal_id,))
        if row is None:
            return GREGORIAN
        months = tuple(
            Month(m["name"], m["days"])
            for m in self.db.query(
                "SELECT name, days FROM calendar_month WHERE calendar_id = ? ORDER BY position",
                (cal_id,),
            )
        )
        eras = tuple(
            Era(e["name"], e["abbreviation"], e["start_year"], e["end_year"],
                counts_backward=bool(e["counts_backward"]),
                reckons_from=e["reckons_from"])
            for e in self.db.query(
                "SELECT * FROM era WHERE calendar_id = ? "
                # NULLs sort first, which puts an era open at the start — a world's
                # own BC — before everything it precedes.
                "ORDER BY start_year, name", (cal_id,)
            )
        )
        seasons = tuple(
            Season(s["name"], s["start"]) for s in decode_json(row["seasons"], [])
        )
        return Calendar(
            name=row["name"],
            months=months,
            weekdays=tuple(decode_json(row["weekdays"], [])) or GREGORIAN.weekdays,
            leap_every=row["leap_every"],
            leap_except=tuple(decode_json(row["leap_except"], [])),
            leap_always=tuple(decode_json(row["leap_always"], [])),
            leap_month=row["leap_month"],
            epoch_weekday=row["epoch_weekday"],
            eras=eras,
            seasons=seasons,
        )

    def day(self, year: int, month: int = 1, day: int = 1) -> int:
        return self.calendar.date(year, month, day)

    # ---- eras (§3): the writer's own time dividers ------------------------

    def _calendar_id(self) -> str:
        cal_id = self.db.scalar(
            "SELECT calendar_id FROM project WHERE id = ?", (self.project_id,))
        if not cal_id:
            raise WorldError("this world has no calendar to add eras to")
        return cal_id

    def eras(self) -> list[dict]:
        """Every era of this world's calendar, earliest first."""
        return [dict(r) | {"counts_backward": bool(r["counts_backward"])}
                for r in self.db.query(
                    "SELECT * FROM era WHERE calendar_id = ? ORDER BY start_year, name",
                    (self._calendar_id(),))]

    def add_era(self, name: str, abbreviation: str, *, start_year: int | None = None,
                end_year: int | None = None, counts_backward: bool = False,
                reckons_from: int | None = None) -> str:
        """Declare a time divider — a world's own AD, or its own BC.

        Nothing about stored facts changes: eras rename years for reading and writing,
        which is why one can be added to a finished world without touching its history.
        """
        name, abbreviation = name.strip(), abbreviation.strip()
        if not name or not abbreviation:
            raise WorldError("an era needs a name and a short form")
        if (start_year is not None and end_year is not None
                and end_year < start_year):
            raise WorldError("an era cannot end before it begins")
        cal_id = self._calendar_id()
        if self.db.one(
            "SELECT 1 FROM era WHERE calendar_id = ? AND lower(abbreviation) = lower(?)",
            (cal_id, abbreviation),
        ):
            raise WorldError(f"this calendar already has an era called {abbreviation!r}")
        eid = new_id()
        with self.db.transaction():
            self.db.insert("era", {
                "id": eid, "calendar_id": cal_id, "name": name,
                "abbreviation": abbreviation, "start_year": start_year,
                "end_year": end_year, "counts_backward": int(counts_backward),
                "reckons_from": reckons_from,
            })
            self._log_revision("era", eid, "insert", None,
                               {"name": name, "abbreviation": abbreviation})
        self._calendar = None            # the cached calendar is now stale
        return eid

    def update_era(self, era_id: str, **changes: Any) -> None:
        allowed = {"name", "abbreviation", "start_year", "end_year",
                   "counts_backward", "reckons_from"}
        payload = {k: v for k, v in changes.items() if k in allowed}
        if not payload:
            return
        if "counts_backward" in payload:
            payload["counts_backward"] = int(bool(payload["counts_backward"]))
        with self.db.transaction():
            row = self.db.one("SELECT * FROM era WHERE id = ?", (era_id,))
            if row is None:
                raise WorldError("no such era")
            # The same guards as add_era. Without them an edit could leave two ages
            # sharing an abbreviation, and a date typed in one would silently parse
            # against the other.
            merged = {**dict(row), **payload}
            if (merged["start_year"] is not None and merged["end_year"] is not None
                    and merged["end_year"] < merged["start_year"]):
                raise WorldError("an era cannot end before it begins")
            if self.db.one(
                "SELECT 1 FROM era WHERE calendar_id = ? AND id != ? "
                "AND lower(abbreviation) = lower(?)",
                (row["calendar_id"], era_id, merged["abbreviation"]),
            ):
                raise WorldError(
                    f"this calendar already has an era called "
                    f"{merged['abbreviation']!r}")
            before = {k: row[k] for k in payload}
            self.db.update("era", era_id, payload)
            self._log_revision("era", era_id, "update", before, payload)
        self._calendar = None

    def delete_era(self, era_id: str) -> None:
        with self.db.transaction():
            row = self.db.one("SELECT * FROM era WHERE id = ?", (era_id,))
            if row is None:
                return
            self._log_revision("era", era_id, "delete", dict(row), None)
            self.db.execute("DELETE FROM era WHERE id = ?", (era_id,))
        self._calendar = None

    # ---- branches (§105) --------------------------------------------------
    #
    # A branch is an alternate timeline that *overlays* its ancestors rather than
    # copying them: reads see the ancestor chain's rows plus the branch's own; writes
    # from a branch never touch an ancestor's rows. Changing something inherited makes
    # a branch-local override — a superseding fact row, or an entity field patch —
    # and deleting an inherited fact makes a tombstone. Canon stays exactly as it was.

    @property
    def branch_name(self) -> str:
        return self.db.scalar("SELECT name FROM branch WHERE id = ?", (self.branch_id,))

    @property
    def is_canon(self) -> bool:
        return bool(self.db.scalar(
            "SELECT is_canon FROM branch WHERE id = ?", (self.branch_id,)))

    def branches(self) -> list[dict]:
        return [
            {"id": r["id"], "name": r["name"], "is_canon": bool(r["is_canon"]),
             "parent_id": r["parent_id"], "branched_at": r["branched_at"],
             "open": r["id"] == self.branch_id}
            for r in self.db.query(
                "SELECT * FROM branch WHERE project_id = ? ORDER BY created_at",
                (self.project_id,))
        ]

    def create_branch(self, name: str, *, branched_at: int | None = None) -> str:
        """A new timeline forking from this one. Returns the new branch's name."""
        name = name.strip()
        if not name:
            raise WorldError("give the timeline a name")
        try:
            self.db.insert("branch", {
                "id": new_id(), "project_id": self.project_id, "name": name,
                "parent_id": self.branch_id, "branched_at": branched_at,
                "is_canon": 0, "created_at": now_iso(),
            })
        except sqlite3.IntegrityError as exc:
            raise WorldError(f"a timeline named {name!r} already exists") from exc
        return name

    def counts_by_type(self) -> dict[str, int]:
        return {r["type_key"]: r["n"] for r in self.db.query(
            f"SELECT type_key, count(*) AS n FROM entity WHERE {self._in_chain} "
            "GROUP BY type_key ORDER BY n DESC", self._chain)}

    def count_facts(self) -> int:
        live, live_params = self._live_fact("fact")
        return self.db.scalar(
            f"SELECT count(*) FROM fact WHERE {self._in_chain} AND {live}",
            [*self._chain, *live_params])

    def count_events(self) -> int:
        return self.db.scalar(
            f"SELECT count(*) FROM event WHERE {self._in_chain}", self._chain)

    def span(self) -> dict[str, int | None]:
        """The first and last day this timeline mentions anywhere."""
        marks = self._in_chain
        bounds = self.db.one(
            f"""SELECT min(d) AS lo, max(d) AS hi FROM (
                   SELECT min(exists_from) AS d FROM entity WHERE {marks}
                   UNION ALL SELECT max(exists_to)  FROM entity WHERE {marks}
                   UNION ALL SELECT min(valid_from) FROM fact   WHERE {marks}
                   UNION ALL SELECT max(valid_to)   FROM fact   WHERE {marks}
                   UNION ALL SELECT min(start_day)  FROM event  WHERE {marks}
                   UNION ALL SELECT max(end_day)    FROM event  WHERE {marks}
                   UNION ALL SELECT max(start_day)  FROM event  WHERE {marks}
               )""",
            [*self._chain] * 7,
        )
        return {"lo": bounds["lo"] if bounds else None,
                "hi": bounds["hi"] if bounds else None}

    def on_branch(self, name: str) -> World:
        """A view of this same file on another timeline — shares the connection."""
        row = self.db.one(
            "SELECT id FROM branch WHERE project_id = ? AND name = ?",
            (self.project_id, name))
        if row is None:
            raise WorldError(f"no timeline named {name!r}")
        return World(self.db, self.project_id, row["id"])

    def _live_fact(self, alias: str) -> tuple[str, list[str]]:
        """SQL for a fact row's visibility under this branch's overlays.

        Hidden when it is a tombstone, or when a row in the chain supersedes it. On
        canon (chain of one) no overlay can apply, so this collapses to a constant —
        the hot paths pay nothing until a branch actually exists.
        """
        if len(self._chain) == 1:
            return "1 = 1", []
        # Rank branches by distance: nearer overrides beat farther ones, so when an
        # ancestor and a descendant both supersede the same fact, only the nearest
        # row speaks — never both, never the farther one.
        rank = "CASE {col} " + " ".join(
            f"WHEN ? THEN {i}" for i in range(len(self._chain))) + " END"
        my_rank = rank.format(col=f"{alias}.branch_id")
        their_rank = rank.format(col="__s.branch_id")
        condition = (
            f"json_extract({alias}.props, '$.branch_tombstone') IS NULL "
            f"AND NOT EXISTS (SELECT 1 FROM fact __o "
            f"WHERE __o.supersedes_id = {alias}.id AND __o.{self._in_chain}) "
            f"AND ({alias}.supersedes_id IS NULL OR NOT EXISTS ("
            f"SELECT 1 FROM fact __s "
            f"WHERE __s.supersedes_id = {alias}.supersedes_id "
            f"AND __s.{self._in_chain} AND ({their_rank}) < ({my_rank})))"
        )
        chain = list(self._chain)
        return condition, chain + chain + chain + chain

    def _override_map(self) -> dict[str, dict]:
        """entity_id -> merged field patch for this chain, farthest branch first, so
        a child branch's edit of one field never discards its parent's edit of
        another."""
        if len(self._chain) == 1:
            return {}
        by_branch: dict[str, list] = {}
        for r in self.db.query(
            f"SELECT branch_id, entity_id, changes FROM entity_override "
            f"WHERE {self._in_chain}", list(self._chain),
        ):
            by_branch.setdefault(r["branch_id"], []).append(r)
        merged: dict[str, dict] = {}
        for branch in reversed(self._chain):            # canon-side first
            for r in by_branch.get(branch, ()):
                merged.setdefault(r["entity_id"], {}).update(
                    decode_json(r["changes"], {}))
        return merged

    def _patched(self, entity: Entity, changes: dict | None) -> Entity:
        if not changes:
            return entity
        fields = {k: v for k, v in changes.items() if k in _ENTITY_EDITABLE}
        if "tags" in fields:
            fields["tags"] = tuple(fields["tags"] or [])
        return dataclasses.replace(entity, **fields)

    def _entity_override_for(self, entity_id: str) -> dict | None:
        if len(self._chain) == 1:
            return None
        merged: dict = {}
        for branch in reversed(self._chain):            # canon-side first
            row = self.db.one(
                "SELECT changes FROM entity_override "
                "WHERE branch_id = ? AND entity_id = ?", (branch, entity_id))
            if row is not None:
                merged.update(decode_json(row["changes"], {}))
        return merged or None

    def _override_entity(self, entity_id: str, payload: dict) -> None:
        """Record branch-local field values over an inherited entity."""
        with self.db.transaction():
            key = f"{self.branch_id}/{entity_id}"
            existing = self.db.one(
                "SELECT changes FROM entity_override "
                "WHERE branch_id = ? AND entity_id = ?",
                (self.branch_id, entity_id))
            if existing is None:
                self.db.insert("entity_override", {
                    "branch_id": self.branch_id, "entity_id": entity_id,
                    "changes": payload,
                })
                self._log_revision("entity_override", key, "insert", None,
                                   {"changes": payload})
            else:
                before = decode_json(existing["changes"], {})
                merged = {**before, **payload}
                self.db.execute(
                    "UPDATE entity_override SET changes = ? "
                    "WHERE branch_id = ? AND entity_id = ?",
                    (json.dumps(merged), self.branch_id, entity_id))
                self._log_revision("entity_override", key, "update",
                                   {"changes": before}, {"changes": merged})

    def _branch_override_row(self, fact_id: str):
        """This branch's own superseding row for an inherited fact, if any."""
        return self.db.one(
            "SELECT * FROM fact WHERE supersedes_id = ? AND branch_id = ?",
            (fact_id, self.branch_id))

    def _override_fact(self, orig, changes: dict) -> str:
        """Write branch-local changes over an inherited fact; returns the row id that
        now speaks for it in this branch."""
        with self.db.transaction():
            existing = self._branch_override_row(orig["id"])
            if existing is not None:
                before = {k: _snapshot(existing).get(k) for k in changes}
                self.db.update("fact", existing["id"],
                               {**changes, "updated_at": now_iso()})
                self._log_revision("fact", existing["id"], "update", before, changes)
                return existing["id"]
            copy = _snapshot(orig)
            copy.update(changes)
            copy["id"] = new_id()
            copy["branch_id"] = self.branch_id
            copy["supersedes_id"] = orig["id"]
            copy["created_at"] = copy["updated_at"] = now_iso()
            self.db.insert("fact", copy)
            self._log_revision("fact", copy["id"], "insert", None, {
                "subject_id": copy.get("subject_id"),
                "predicate_key": copy.get("predicate_key"),
                "object_id": copy.get("object_id"), "value": copy.get("value"),
                "supersedes": orig["id"],
            })
            return copy["id"]

    # ---- revisions (§59) --------------------------------------------------

    def _log_revision(self, table: str, row_id: str, action: str,
                      before: dict | None, after: dict | None,
                      *, note: str = "") -> None:
        """Append to the revision log. Never updated, never deleted.

        §59 asks for revision history and restore points, and §106.3 forbids overwriting
        history. The log records what changed rather than snapshotting the row, so it is a
        readable diff — and because it is written inside the same transaction as the
        change, a rollback takes its log entry with it. `note` marks records that belong
        to a delete's cascade batch, so restore can find them by name rather than by
        guessing from timestamps. `action_id` groups everything one transaction wrote
        into one *user action* — the unit undo works on — and any genuinely new action
        forfeits whatever was waiting to be redone, as undo systems must.
        """
        action_id = self._current_action_id()
        if action_id not in self._inversions and self._redo:
            self._redo.clear()
        self.db.insert("revision", {
            "project_id": self.project_id, "table_name": table, "row_id": row_id,
            "action": action, "before": before, "after": after, "at": now_iso(),
            "note": note, "action_id": action_id,
        })

    def _current_action_id(self) -> str:
        """The id of the user action being written: branch, session, transaction.

        The branch prefix is what keeps undo timeline-scoped: an action made on a
        what-if is never the target of Ctrl+Z on canon, and vice versa — the shared
        log stays one history, read through the timeline that wrote each entry.
        """
        return f"{self.branch_id}:{self._session_token}:{self.db.transaction_serial}"

    def _action_is_ours(self, action_id: str) -> bool:
        """Whether an action belongs to this timeline.

        Two-part ids predate branch-prefixed ids and are attributed to canon — the
        only build that wrote branch actions without a prefix was never released, so
        in practice every unprefixed action really was canon's."""
        head, _, rest = action_id.partition(":")
        if ":" not in rest:                     # legacy two-part id -> canon's
            return len(self._chain) == 1
        return head == self.branch_id

    def revisions_for(self, row_id: str, *, limit: int = 50) -> list[dict]:
        """The change history of one row, newest first."""
        return [
            {**dict(r), "before": decode_json(r["before"], None),
             "after": decode_json(r["after"], None)}
            for r in self.db.query(
                "SELECT * FROM revision WHERE project_id = ? AND row_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (self.project_id, row_id, limit),
            )
        ]

    def recently_edited(self, *, limit: int = 10) -> list[tuple[Entity, str]]:
        """Entities with the newest changes, for §74's dashboard section.

        Fact changes are attributed to their subject entity, so adding a relationship
        counts as editing the person it is about — which is how a writer thinks of it.
        A *deleted* fact's subject comes from the delete record's own snapshot, because
        the row it pointed at is gone; looking it up in the fact table would silently
        drop the newest edit from the list and misorder the rest.
        """
        rows = self.db.query(
            "SELECT table_name, row_id, before, at FROM revision "
            "WHERE project_id = ? AND table_name IN ('entity', 'fact') "
            "ORDER BY id DESC LIMIT ?",
            (self.project_id, limit * 8),
        )
        out: list[tuple[Entity, str]] = []
        seen: set[str] = set()
        for row in rows:
            if row["table_name"] == "entity":
                entity_id = row["row_id"]
            elif row["table_name"] == "fact":
                entity_id = self.db.scalar(
                    "SELECT subject_id FROM fact WHERE id = ?", (row["row_id"],))
                if entity_id is None:
                    before = decode_json(row["before"], None)
                    entity_id = (before or {}).get("subject_id")
            else:
                continue
            if not entity_id or entity_id in seen:
                continue
            seen.add(entity_id)
            entity = self.get_entity(entity_id)
            if entity is None:
                continue          # deleted entities stay off the dashboard
            out.append((entity, row["at"]))
            if len(out) >= limit:
                break
        return out

    def get_revision(self, revision_id: int) -> dict | None:
        row = self.db.one(
            "SELECT * FROM revision WHERE id = ? AND project_id = ?",
            (revision_id, self.project_id),
        )
        if row is None:
            return None
        return {**dict(row), "before": decode_json(row["before"], None),
                "after": decode_json(row["after"], None)}

    def recently_deleted(self, *, limit: int = 10) -> list[dict]:
        """Entity deletions that have not been restored — the way back in (§59).

        A deleted entity has no page left to restore it from, so this list is where the
        writer finds it again. Only the *newest* deletion of an entity is offered: after
        a delete–restore–delete cycle the older record still exists, but restoring it
        would resurrect a stale version, so it is not presented as the way back.
        """
        out: list[dict] = []
        seen: set[str] = set()
        for row in self.db.query(
            "SELECT id, row_id, before, at FROM revision "
            "WHERE project_id = ? AND table_name = 'entity' AND action = 'delete' "
            "ORDER BY id DESC LIMIT ?",
            (self.project_id, limit * 4),
        ):
            if row["row_id"] in seen:
                continue                     # an older deletion of the same entity
            seen.add(row["row_id"])
            if self.get_entity(row["row_id"]) is not None:
                continue                     # already restored
            snapshot = decode_json(row["before"], None) or {}
            out.append({
                "revision_id": row["id"], "entity_id": row["row_id"],
                "name": snapshot.get("name", "?"),
                "type_key": snapshot.get("type_key", "?"), "at": row["at"],
            })
            if len(out) >= limit:
                break
        return out

    def restore(self, revision_id: int) -> str:
        """Undo one recorded change (§59 restore points).

        Restoring is itself a logged change — the log stays append-only, and undoing a
        restore is just restoring the restore. A deleted entity comes back with
        everything the FK cascade took with it — facts, but also its events, titles,
        secrets, geometry, participations and knowledge — because the delete logged the
        whole batch under a marker before the cascade ran.
        """
        rev = self.get_revision(revision_id)
        if rev is None:
            raise WorldError(f"no revision {revision_id}")
        if rev["table_name"] not in ("entity", "fact"):
            raise WorldError(f"cannot restore {rev['table_name']!r} revisions")
        with self.db.transaction():
            if rev["action"] == "delete":
                return self._restore_deleted(rev)
            if rev["action"] == "update":
                return self._restore_update(rev)
            raise WorldError(
                "an insert revision has nothing to restore — deleting the row is how a "
                "creation is undone, and that produces its own restorable record"
            )

    def _table_columns(self, table: str) -> set[str]:
        return {r["name"] for r in self.db.query(f"PRAGMA table_info({table})")}

    def _restore_deleted(self, rev: dict) -> str:
        row_id = rev["row_id"]
        # Snapshot keys come from the world file; only real columns may pass into an
        # INSERT's column list, whatever the log claims.
        snapshot = {k: v for k, v in (rev["before"] or {}).items()
                    if k in self._table_columns(rev["table_name"])}
        if rev["table_name"] == "entity":
            if self.db.one("SELECT 1 FROM entity WHERE id = ?", (row_id,)):
                raise WorldError(f"{snapshot.get('name', row_id)} already exists")
            if not {"id", "type_key", "name"} <= set(snapshot):
                raise WorldError("this delete record is too incomplete to restore")
            self._insert_entity_snapshot(snapshot, rev["id"])
            facts, others = self._restore_cascade(rev)
            name = snapshot.get("name", row_id)
            parts = []
            if facts:
                parts.append(f"{facts} connection{'s' if facts != 1 else ''}")
            if others:
                parts.append(f"{others} related record{'s' if others != 1 else ''}")
            if parts:
                return f"Restored {name} and {' and '.join(parts)}."
            return f"Restored {name}."

        # a single fact — plus whatever its own deletion cascaded (facts about it)
        self._restore_row("fact", snapshot, rev["id"], strict=True)
        facts, others = self._restore_cascade(rev)
        label = snapshot.get("predicate_key", "fact")
        extra = facts + others
        if extra:
            return (f"Restored the {label} connection and {extra} dependent "
                    f"record{'s' if extra != 1 else ''}.")
        return f"Restored the {label} connection."

    def _insert_entity_snapshot(self, snapshot: dict, source_revision: int) -> None:
        """Re-insert one entity row from its (column-filtered) delete snapshot."""
        self.db.insert("entity", snapshot)
        self._index_entity(snapshot["id"], snapshot["type_key"], snapshot["name"],
                           snapshot.get("summary", ""), snapshot.get("tags") or [])
        self._log_revision("entity", snapshot["id"], "insert", None, {
            "name": snapshot["name"], "type_key": snapshot["type_key"],
            "restored_from": source_revision,
        })

    def _restore_cascade(self, rev: dict) -> tuple[int, int]:
        """Bring back the rows logged in the same delete batch as `rev`.

        The delete wrote every cascade victim contiguously before its own record, all
        under the transaction lock and all carrying a `cascade:<row_id>` note — so
        walking revision ids downwards while the note matches recovers exactly that
        batch, whatever the clock said. (The earlier version matched on the second-
        resolution timestamp, which both split batches across a second boundary and
        merged unrelated same-second deletes.)

        Restores parents before children so FK targets exist when their dependants are
        inserted, then re-links surviving rows whose SET NULL references the cascade
        cleared. Returns (facts, other rows) restored.
        """
        marker = f"cascade:{rev['row_id']}"
        batch: list[dict] = []
        cursor = rev["id"] - 1
        while True:
            prior = self.get_revision(cursor)
            if prior is None or prior.get("note") != marker:
                break
            batch.append(prior)
            cursor -= 1

        deletes = [prior for prior in batch if prior["action"] == "delete"]
        relinks = [prior for prior in batch if prior["action"] == "update"]

        facts, others = self._replay_deletes(deletes)
        for prior in relinks:
            others += self._apply_relink(prior)
        return facts, others

    def _replay_deletes(self, deletes: list[dict]) -> tuple[int, int]:
        """Re-insert delete records in dependency order, whatever order they arrive in.

        Entities first, then facts — which can be *about* other facts (reification), so
        they insert in passes until a pass makes no progress, a claim always preceding
        the facts about it; what remains is about a fact that never came back, and
        stays gone exactly as the FK would insist. Then everything else parents-first.
        Both restore and undo replay through here, so neither can drop a cascade child
        by replaying it before its parent exists.
        """
        by_table: dict[str, list[dict]] = {}
        for prior in deletes:
            by_table.setdefault(prior["table_name"], []).append(prior)

        facts = others = 0
        for prior in by_table.pop("entity", []):
            others += self._undelete_entity(prior)
        pending = by_table.pop("fact", [])
        while pending:
            waiting: list[dict] = []
            for prior in pending:
                snapshot = prior["before"] or {}
                about = snapshot.get("about_fact_id")
                if about and not self.db.one(
                        "SELECT 1 FROM fact WHERE id = ?", (about,)):
                    waiting.append(prior)
                    continue
                if self._restore_row("fact", snapshot, prior["id"], strict=False):
                    facts += 1
            if len(waiting) == len(pending):
                break
            pending = waiting
        for table in _RESTORE_ORDER:
            for prior in by_table.get(table, []):
                if self._restore_row(table, prior["before"] or {}, prior["id"],
                                     strict=False):
                    others += 1
        return facts, others

    def _undelete_entity(self, rev: dict) -> int:
        """Re-insert one entity from its delete record, if it can come back."""
        snapshot = {k: v for k, v in (rev["before"] or {}).items()
                    if k in self._table_columns("entity")}
        if not {"id", "type_key", "name"} <= set(snapshot) or self.db.one(
                "SELECT 1 FROM entity WHERE id = ?", (snapshot.get("id"),)):
            return 0
        self._insert_entity_snapshot(snapshot, rev["id"])
        return 1

    def _restore_row(self, table: str, snapshot: dict, source_revision: int,
                     *, strict: bool) -> bool:
        """Re-insert one row from its delete snapshot.

        `strict` raises when the row cannot come back (the writer asked for this exact
        record); the batch path skips instead, because an entity restore should bring
        back what it can rather than fail on one absent counterpart. Both the table
        name and the snapshot keys come from the world file, so both are validated
        against the real schema before they touch SQL.
        """
        spec = _RESTORE_SPEC.get(table)
        if spec is None:
            if strict:
                raise WorldError(f"cannot restore {table!r} rows")
            return False
        snapshot = {k: v for k, v in snapshot.items()
                    if k in self._table_columns(table)}
        key: tuple[str, ...] = spec.get("key", ("id",))
        if any(snapshot.get(column) is None for column in key):
            if strict:
                raise WorldError("this delete record is too incomplete to restore")
            return False
        for identity in (key, spec.get("unique")):
            if identity is None:
                continue
            condition = " AND ".join(f"{column} = ?" for column in identity)
            if self.db.one(f"SELECT 1 FROM {table} WHERE {condition}",
                           [snapshot.get(column) for column in identity]):
                if strict:
                    raise WorldError("this record already exists — nothing to restore")
                return False
        for column, (target, on_missing) in spec["refs"].items():
            value = snapshot.get(column)
            if value is None:
                continue
            if self.db.one(f"SELECT 1 FROM {target} WHERE id = ?", (value,)):
                continue
            if on_missing == "null":
                # An ON DELETE SET NULL reference: the row survives without it.
                snapshot[column] = None
                continue
            # An ON DELETE CASCADE reference: the row cannot outlive its target.
            if strict:
                if target == "entity":
                    raise WorldError(
                        "one side of this connection no longer exists — "
                        "restore that entity first"
                    )
                raise WorldError(
                    f"this record depends on a {target} that no longer exists — "
                    "restore that first"
                )
            return False
        if table == "causal_link":
            cause, effect = snapshot.get("cause_id"), snapshot.get("effect_id")
            if cause and effect and self._causally_reaches(effect, cause):
                # The world moved on while this link was deleted: putting it back would
                # now close a causal loop. The DAG invariant outranks the restore.
                if strict:
                    raise WorldError("restoring this link would close a causal loop")
                return False
        try:
            self.db.insert(table, snapshot)
        except sqlite3.IntegrityError as exc:
            # A crafted or truncated snapshot (a NOT NULL column missing, a reference
            # the checks above do not model) must not abort the whole batch restore.
            if strict:
                raise WorldError(f"this record cannot be restored: {exc}") from exc
            return False
        if table == "geometry":
            coordinates = snapshot.get("coordinates")
            if isinstance(coordinates, str):
                coordinates = decode_json(coordinates, [])
            self._index_geometry(snapshot["id"], coordinates)
        after = {column: snapshot[column]
                 for column in ("name", "subject_id", "predicate_key", "object_id",
                                "value", "title", "role", "stance", "kind")
                 if column in snapshot}
        after["restored_from"] = source_revision
        self._log_revision(table, _row_key(table, snapshot), "insert", None, after)
        return True

    def _apply_relink(self, prior: dict) -> int:
        """Put back one SET NULL reference the cascade cleared, if still safe to.

        Skipped when the row is gone, the column has been re-pointed since, or the old
        target did not come back — a relink must never overwrite a later decision.
        """
        table = prior["table_name"]
        allowed = _RELINK_COLUMNS.get(table)
        if not allowed:
            return 0
        row = self.db.one(f"SELECT * FROM {table} WHERE id = ?", (prior["row_id"],))
        if row is None:
            return 0
        changes: dict[str, Any] = {}
        for column, value in (prior["before"] or {}).items():
            target = allowed.get(column)
            if target is None or value is None or row[column] is not None:
                continue
            if not self.db.one(f"SELECT 1 FROM {target} WHERE id = ?", (value,)):
                continue
            changes[column] = value
        if not changes:
            return 0
        self.db.update(table, prior["row_id"], changes)
        self._log_revision(table, prior["row_id"], "update",
                           dict.fromkeys(changes), changes)
        return 1

    def _restore_update(self, rev: dict) -> str:
        row_id = rev["row_id"]
        before = rev["before"] or {}
        if not before:
            raise WorldError("this change recorded no prior values to restore")
        if rev["table_name"] == "entity":
            if self.get_entity(row_id) is None:
                raise WorldError(
                    "the entity no longer exists — restore its deletion first")
            payload = {k: v for k, v in before.items() if k in _ENTITY_EDITABLE}
            if not payload:
                # update_entity would silently no-op on an empty payload, and a restore
                # that changes nothing must not report success.
                raise WorldError("this change names no restorable columns")
            self.update_entity(row_id, **payload)    # logs its own inverse record
            return "Restored the earlier values."
        row = self.db.one("SELECT * FROM fact WHERE id = ?", (row_id,))
        if row is None:
            raise WorldError("the fact no longer exists — restore its deletion first")
        # Revision JSON comes from the world file, and a world file must never be able
        # to smuggle SQL — only real fact columns may be named, whatever the log says.
        data = dict(row)
        before = {key: value for key, value in before.items() if key in data}
        if not before:
            raise WorldError("this change names no restorable columns")
        current = {key: data[key] for key in before}
        self.db.update("fact", row_id, {**before, "updated_at": now_iso()})
        self._log_revision("fact", row_id, "update", current, before)
        return "Restored the earlier values."

    # ---- undo / redo (§59) ------------------------------------------------
    #
    # Undo works on whole user actions: everything one transaction logged, grouped by
    # action_id. Inverting an action writes ordinary revisions (the log stays
    # append-only) under a new action id, announced by a marker record — so redo is
    # just inverting the inversion, and which actions currently stand undone can be
    # reconstructed from the file after a restart. Only what the revision log covers
    # (entities and facts, with everything their deletes cascade through) is undoable;
    # anything older than the action_id column simply sits beyond undo's reach.

    def undo(self) -> str:
        """Take back the most recent action. Raises when there is nothing to."""
        with self.db.transaction():
            self._load_undo_state()      # the log is the truth, other handles included
            target = self._newest_action()
            if target is None:
                raise WorldError("nothing to undo")
            inversion = self._current_action_id()
            self._inversions[inversion] = target
            self._log_revision("undo", target, "undo", None, {"kind": "undo"})
            self._invert_action(target)
            self._undone.add(target)
            self._redo.append((target, inversion))
        self._calendar = None      # an undone era changes how every date reads
        return f"Undid {self._describe_action(target)}."

    def redo(self) -> str:
        """Reinstate the most recently undone action."""
        # Read and pop inside the transaction: it holds the connection lock, so two
        # concurrent requests cannot both grab (and doubly apply) the same entry.
        with self.db.transaction():
            self._load_undo_state()      # the log is the truth, other handles included
            if not self._redo:
                raise WorldError("nothing to redo")
            target, inversion = self._redo[-1]
            fresh = self._current_action_id()
            self._inversions[fresh] = inversion
            self._log_revision("undo", inversion, "undo", None, {"kind": "redo"})
            self._invert_action(inversion)
            self._undone.discard(target)
            self._redo.pop()
        self._calendar = None
        return f"Redid {self._describe_action(target)}."

    def undo_state(self) -> dict:
        """What the toolbar needs: whether each button would do anything, and to what."""
        self._load_undo_state()
        target = self._newest_action()
        return {
            "can_undo": target is not None,
            "undo": self._describe_action(target) if target else None,
            "can_redo": bool(self._redo),
            "redo": (self._describe_action(self._redo[-1][0])
                     if self._redo else None),
        }

    def _revisions_newest_first(self) -> Iterable[tuple[int, str]]:
        """(id, action_id) for every grouped revision, newest first, in pages.

        The one spelling of the walk that both undo-target selection and redo
        forfeiture use — two copies would eventually disagree about what counts.
        """
        last_id: int | None = None
        while True:
            sql = ("SELECT id, action_id FROM revision "
                   "WHERE project_id = ? AND action_id != ''")
            params: list[Any] = [self.project_id]
            if last_id is not None:
                sql += " AND id < ?"
                params.append(last_id)
            sql += " ORDER BY id DESC LIMIT 500"
            rows = self.db.query(sql, params)
            if not rows:
                return
            for row in rows:
                last_id = row["id"]
                yield row["id"], row["action_id"]

    def _load_undo_state(self) -> None:
        """(Re)build undo state from the log's inversion markers.

        Called at open and again before every undo, redo and state read — the log is
        the shared truth, so a second handle on the same file (a CLI beside the
        server, say) forfeits this handle's redo exactly as an edit here would, and
        this handle never mistakes the other's inversions for real actions.
        """
        self._undone.clear()
        self._inversions.clear()
        self._redo = []
        pending: dict[str, tuple[int, str]] = {}    # target -> (marker id, inversion)
        for marker in self.db.query(
            "SELECT id, row_id, action_id FROM revision "
            "WHERE project_id = ? AND table_name = 'undo' ORDER BY id",
            (self.project_id,),
        ):
            target = marker["row_id"]
            if not self._action_is_ours(marker["action_id"]):
                # another timeline's undo history; register the inversion so it is
                # never mistaken for a real action, but track nothing else from it
                self._inversions[marker["action_id"]] = target
                continue
            # A marker whose target is itself a known inversion is a redo: the
            # original action that inversion had taken back stands again.
            original = self._inversions.get(target)
            self._inversions[marker["action_id"]] = target
            if original is not None:
                self._undone.discard(original)
                pending.pop(original, None)
            else:
                self._undone.add(target)
                pending[target] = (marker["id"], marker["action_id"])
        # A real action recorded *after* an undo forfeits that undo's redo — a stale
        # redo would clobber the newer edit it lost to.
        newest_real = 0
        for rev_id, action_id in self._revisions_newest_first():
            if action_id not in self._inversions and self._action_is_ours(action_id):
                newest_real = rev_id
                break
        self._redo = [(t, inv) for t, (marker_id, inv) in
                      sorted(pending.items(), key=lambda kv: kv[1][0])
                      if marker_id > newest_real]

    def _newest_action(self) -> str | None:
        """The most recent real user action that is not already undone.

        Walks the log newest-first with early termination — never an aggregate over the
        whole history, and never a fixed window that heavy undo traffic could exhaust.
        """
        seen: set[str] = set()
        for _, aid in self._revisions_newest_first():
            if aid in seen:
                continue
            seen.add(aid)
            if (self._action_is_ours(aid)
                    and aid not in self._inversions and aid not in self._undone):
                return aid
        return None

    def _invert_action(self, action_id: str) -> int:
        """Apply the inverse of every record in one action.

        Deletes are inverted first — replayed in dependency order by the same machinery
        restore uses, so a cascade's children all find their parents — then updates
        (their targets exist again by now), then inserts (newest-first, children
        removed before parents). The mixed shapes — a delete with its relink updates,
        a restore with its re-inserts, a transfer's end-and-assert pair — all come out
        right under that one ordering.
        """
        revs = [
            {**dict(r), "before": decode_json(r["before"], None),
             "after": decode_json(r["after"], None)}
            for r in self.db.query(
                "SELECT * FROM revision WHERE action_id = ? AND table_name != 'undo' "
                "ORDER BY id DESC",
                (action_id,),
            )
        ]
        facts, others = self._replay_deletes(
            [rev for rev in revs if rev["action"] == "delete"])
        changed = facts + others
        for rev in revs:
            if rev["action"] == "update":
                changed += self._unupdate(rev)
        for rev in revs:
            if rev["action"] == "insert":
                changed += self._uninsert(rev)
        return changed

    def _unupdate(self, rev: dict) -> int:
        """Inverse of an update record: apply its `before` values again."""
        table, row_id = rev["table_name"], rev["row_id"]
        before = rev["before"] or {}
        if not before:
            return 0
        if table == "entity":
            if self.get_entity(row_id) is None:
                return 0
            payload = {k: v for k, v in before.items() if k in _ENTITY_EDITABLE}
            if not payload:
                return 0
            self.update_entity(row_id, **payload)    # logs its own inverse record
            return 1
        if table == "fact":
            row = self.db.one("SELECT * FROM fact WHERE id = ?", (row_id,))
            if row is None:
                return 0
            data = dict(row)
            payload = {k: v for k, v in before.items() if k in data}
            if not payload:
                return 0
            current = {k: data[k] for k in payload}
            self.db.update("fact", row_id, {**payload, "updated_at": now_iso()})
            self._log_revision("fact", row_id, "update", current, payload)
            return 1
        if table in _PLAIN_UPDATABLE:
            row = self.db.one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
            if row is None:
                return 0
            data = dict(row)
            payload = {k: v for k, v in before.items() if k in data}
            if not payload:
                return 0
            current = {k: data[k] for k in payload}
            self.db.update(table, row_id, payload)
            self._log_revision(table, row_id, "update", current, payload)
            return 1
        if table == "entity_override":
            parts = row_id.split("/")
            if len(parts) != 2:
                return 0
            row = self.db.one(
                "SELECT changes FROM entity_override "
                "WHERE branch_id = ? AND entity_id = ?", parts)
            if row is None:
                return 0
            payload = before.get("changes")
            if not isinstance(payload, dict):
                return 0
            current = decode_json(row["changes"], {})
            self.db.execute(
                "UPDATE entity_override SET changes = ? "
                "WHERE branch_id = ? AND entity_id = ?",
                (json.dumps(payload), *parts))
            self._log_revision("entity_override", row_id, "update",
                               {"changes": current}, {"changes": payload})
            return 1
        # a SET NULL relink written during a delete or restore
        allowed = _RELINK_COLUMNS.get(table)
        if not allowed:
            return 0
        row = self.db.one(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
        if row is None:
            return 0
        changes: dict[str, Any] = {}
        for column, value in before.items():
            target = allowed.get(column)
            if target is None:
                continue
            if value is not None and not self.db.one(
                    f"SELECT 1 FROM {target} WHERE id = ?", (value,)):
                continue
            changes[column] = value
        if not changes:
            return 0
        current = {column: row[column] for column in changes}
        self.db.update(table, row_id, changes)
        self._log_revision(table, row_id, "update", current, changes)
        return 1

    def _uninsert(self, rev: dict) -> int:
        """Inverse of an insert record: remove the row it created."""
        table, row_id = rev["table_name"], rev["row_id"]
        if table == "entity":
            if self.get_entity(row_id) is None:
                return 0
            self.delete_entity(row_id)               # logs its own full cascade
            return 1
        if table == "fact":
            fact_row = self.db.one("SELECT * FROM fact WHERE id = ?", (row_id,))
            if fact_row is None:
                return 0
            # Physical, not routed: undoing an insert removes exactly that row. The
            # tombstone-preserving routing would otherwise turn undo of a branch
            # override into yet another override.
            self._delete_fact_row(fact_row)
            return 1
        spec = _RESTORE_SPEC.get(table)
        if spec is None:
            return 0
        key: tuple[str, ...] = spec.get("key", ("id",))
        parts = row_id.split("/")
        if len(parts) != len(key):
            return 0     # a composite key whose value contained the separator
        condition = " AND ".join(f"{column} = ?" for column in key)
        row = self.db.one(f"SELECT * FROM {table} WHERE {condition}", parts)
        if row is None:
            return 0
        if table == "geometry":
            self.db.execute(
                "DELETE FROM geometry_bbox WHERE id IN ("
                " SELECT rtree_id FROM geometry_rtree_map WHERE geometry_id = ?)",
                (row["id"],))
        # The DELETE below cascades: children attached since this row was created
        # (a holding granted after a restore, say) must be snapshotted first, under
        # the batch marker, or they would be gone beyond any log's help.
        if "id" in row.keys() and table in _CHILD_TABLES:  # noqa: SIM118 — Row, not dict
            marker = f"cascade:{row['id']}"
            logged: set[str] = set()
            for child_table, column in _CHILD_TABLES[table]:
                for child in self.db.query(
                    f"SELECT * FROM {child_table} WHERE {column} = ?", (row["id"],)
                ):
                    child_key = f"{child_table}:{_row_key(child_table, child)}"
                    if child_key in logged:
                        continue
                    logged.add(child_key)
                    self._log_revision(child_table, _row_key(child_table, child),
                                       "delete", _snapshot(child), None, note=marker)
        self._log_revision(table, row_id, "delete", _snapshot(row), None)
        self.db.execute(f"DELETE FROM {table} WHERE {condition}", parts)
        return 1

    def _describe_action(self, action_id: str | None) -> str:
        """A human phrase for an action, from its most significant record.

        Significance, not recency: creating an event logs the event and then its
        participants, and the phrase should name the event, not the last participant.
        """
        if action_id is None:
            return "nothing"
        # Fetch the best record by precedence directly — a windowed scan would let a
        # big cascade push the entity that names the action out of view.
        precedence = ("entity", "fact", "event", "scene", "title", "secret")
        row = None
        for want in precedence:
            row = self.db.one(
                "SELECT * FROM revision WHERE action_id = ? AND table_name = ? "
                "ORDER BY id DESC LIMIT 1",
                (action_id, want),
            )
            if row is not None:
                break
        if row is None:
            row = self.db.one(
                "SELECT * FROM revision WHERE action_id = ? "
                "AND table_name != 'undo' ORDER BY id DESC LIMIT 1",
                (action_id,),
            )
        if row is None:
            return "an empty action"
        before = decode_json(row["before"], None) or {}
        after = decode_json(row["after"], None) or {}
        table, act = row["table_name"], row["action"]
        if table == "entity":
            name = after.get("name") or before.get("name")
            if name is None:
                entity = self.get_entity(row["row_id"])
                name = entity.name if entity else "an entity"
            if act == "insert":
                return f"creating {name}"
            if act == "delete":
                return f"deleting {name}"
            return f"an edit to {name}"
        if table == "fact":
            predicate = (after.get("predicate_key") or before.get("predicate_key")
                         or "connection")
            predicate = str(predicate).replace("_", " ")
            if act == "insert" and after.get("supersedes"):
                return f"a timeline change to a {predicate} connection"
            if act == "insert":
                return f"recording a {predicate} connection"
            if act == "delete":
                return f"removing a {predicate} connection"
            return f"an edit to a {predicate} connection"
        noun = _ACTION_NOUNS.get(table)
        if noun:
            name = (after.get("name") or after.get("title")
                    or before.get("name") or before.get("title"))
            phrase = f"{noun} “{name}”" if name else noun
            if act == "insert":
                return f"creating {phrase}"
            if act == "delete":
                return f"deleting {phrase}"
            return f"an edit to {phrase}"
        count = self.db.scalar(
            "SELECT count(*) FROM revision WHERE action_id = ? "
            "AND table_name != 'undo'", (action_id,))
        return f"{count} recorded change{'s' if count != 1 else ''}"

    # ---- entities ---------------------------------------------------------

    def add_entity(
        self,
        type_key: str,
        name: str,
        *,
        summary: str = "",
        exists_from: int | None = None,
        exists_to: int | None = None,
        exists_from_hi: int | None = None,
        exists_to_lo: int | None = None,
        confidence: str = "canon",
        tags: Sequence[str] = (),
        entity_id: str | None = None,
    ) -> Entity:
        if not self.db.one(
            "SELECT 1 FROM entity_type WHERE project_id = ? AND key = ?",
            (self.project_id, type_key),
        ):
            raise WorldError(
                f"unknown entity type {type_key!r} — add it with add_entity_type() first"
            )
        eid = entity_id or new_id()
        stamp = now_iso()
        with self.db.transaction():
            self.db.insert("entity", {
                "id": eid, "project_id": self.project_id, "type_key": type_key,
                "name": name, "summary": summary, "exists_from": exists_from,
                "exists_to": exists_to, "exists_from_hi": exists_from_hi,
                "exists_to_lo": exists_to_lo, "branch_id": self.branch_id,
                "confidence": confidence, "tags": list(tags),
                "created_at": stamp, "updated_at": stamp,
            })
            self._index_entity(eid, type_key, name, summary, tags)
            self._log_revision("entity", eid, "insert", None,
                               {"name": name, "type_key": type_key})
        return Entity(
            id=eid, type_key=type_key, name=name, summary=summary,
            exists_from=exists_from, exists_to=exists_to,
            exists_from_hi=exists_from_hi, exists_to_lo=exists_to_lo,
            confidence=confidence, tags=tuple(tags),
            branch_id=self.branch_id, project_id=self.project_id,
        )

    def _index_entity(self, eid, type_key, name, summary, tags) -> None:
        self.db.execute("DELETE FROM entity_fts WHERE entity_id = ?", (eid,))
        self.db.execute("DELETE FROM entity_trigram WHERE entity_id = ?", (eid,))
        self.db.execute(
            "INSERT INTO entity_fts (name, summary, tags, entity_id, type_key) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, summary, " ".join(tags), eid, type_key),
        )
        self.db.execute(
            "INSERT INTO entity_trigram (name, entity_id) VALUES (?, ?)", (name, eid)
        )

    def add_entity_type(self, key: str, label: str, plural: str = "",
                        category: str = "other", icon: str = "",
                        core_fields: Sequence[str] = ()) -> None:
        """§60: writers create their own entity types, and they behave exactly like ours."""
        self.db.insert("entity_type", {
            "id": new_id(), "project_id": self.project_id, "key": key, "label": label,
            "plural": plural or f"{label}s", "category": category, "icon": icon,
            "core_fields": list(core_fields), "is_builtin": 0,
        })

    def add_predicate(self, key: str, label: str, kind: str = "rel", **kw: Any) -> None:
        """§60: and their own relationship types."""
        self.db.insert("predicate", {
            "id": new_id(), "project_id": self.project_id, "key": key, "label": label,
            "kind": kind, "inverse_key": kw.get("inverse_key"),
            "symmetric": int(kw.get("symmetric", False)),
            "transitive": int(kw.get("transitive", False)),
            "datatype": kw.get("datatype", "text"), "scale_key": kw.get("scale_key"),
            "domain_type_keys": list(kw.get("domain_type_keys", [])),
            "range_type_keys": list(kw.get("range_type_keys", [])),
            "category": kw.get("category", "other"),
            "description": kw.get("description", ""), "is_builtin": 0,
        })

    def update_entity(self, entity_id: str, **changes: Any) -> None:
        payload = {k: v for k, v in changes.items() if k in _ENTITY_EDITABLE}
        if not payload:
            return
        with self.db.transaction():
            row = self.db.one("SELECT * FROM entity WHERE id = ?", (entity_id,))
            if row is not None and row["branch_id"] != self.branch_id:
                # An inherited entity: the change lives in this branch as a field
                # patch — the ancestor's row is never written from here.
                if row["branch_id"] not in self._chain:
                    raise WorldError("that entity is not part of this timeline")
                self._override_entity(entity_id, payload)
                return
            payload["updated_at"] = now_iso()
            data = dict(row) if row else {}
            # Decode JSON columns so `before` and `after` share one shape — a diff
            # renderer must not see '["a"]' against ['a', 'b'] on every tags edit.
            before = {
                k: (decode_json(data[k], []) if k == "tags" else data[k])
                for k in payload if k != "updated_at" and k in data
            }
            self.db.update("entity", entity_id, payload)
            e = self.get_entity(entity_id)
            if e is not None:
                self._index_entity(e.id, e.type_key, e.name, e.summary, e.tags)
            self._log_revision(
                "entity", entity_id, "update", before,
                {k: v for k, v in payload.items() if k != "updated_at"},
            )

    def delete_entity(self, entity_id: str) -> None:
        with self.db.transaction():
            row = self.db.one("SELECT * FROM entity WHERE id = ?", (entity_id,))
            if row is not None and row["branch_id"] != self.branch_id:
                if row["branch_id"] not in self._chain:
                    raise WorldError("that entity is not part of this timeline")
                # In an alternate timeline the inherited world is not erasable — a
                # person dies, a city falls, but neither un-happens. Ending its
                # existence is the branch's way; deleting belongs to the timeline
                # that actually owns the row.
                raise WorldError(
                    f"{row['name']} belongs to the main timeline — in a branch, end "
                    "its existence instead of deleting it"
                )
            if row is not None:
                # The FK cascade is about to take every row that hangs off this entity —
                # facts, but also its events, titles, secrets, geometry, participations,
                # holdings and knowledge. Log each one first, in full, under a batch
                # marker, or the most destructive operation in the application would be
                # the one the recovery log knows least about. The marker is what restore
                # walks: matching on it is exact where matching on the timestamp split
                # batches across a second boundary and merged same-second deletes.
                marker = f"cascade:{entity_id}"
                doomed = self._entity_cascade(entity_id)
                for table, victim in doomed:
                    self._log_revision(table, _row_key(table, victim), "delete",
                                       _snapshot(victim), None, note=marker)
                for table, rid, cleared in self._entity_relinks(entity_id, doomed):
                    self._log_revision(table, rid, "update", cleared,
                                       dict.fromkeys(cleared), note=marker)
                # The R*Tree is not FK-aware; drop this entity's boxes by hand or they
                # orphan when the geometry rows cascade.
                self.db.execute(
                    "DELETE FROM geometry_bbox WHERE id IN ("
                    " SELECT m.rtree_id FROM geometry_rtree_map m"
                    " JOIN geometry g ON g.id = m.geometry_id WHERE g.entity_id = ?)",
                    (entity_id,))
                self._log_revision("entity", entity_id, "delete", _snapshot(row), None)
            self.db.execute("DELETE FROM entity_fts WHERE entity_id = ?", (entity_id,))
            self.db.execute("DELETE FROM entity_trigram WHERE entity_id = ?", (entity_id,))
            self.db.execute("DELETE FROM entity WHERE id = ?", (entity_id,))

    def _entity_cascade(self, entity_id: str) -> list[tuple[str, Any]]:
        """Every row the FK cascade will delete with this entity, as (table, row).

        Ordered parents before children — the same order restore replays them in.
        """
        q = self.db.query

        def rows_in(table: str, column: str, ids: Iterable[str]) -> list:
            return _rows_in(self.db, table, column, ids)

        facts = {r["id"]: r for r in q(
            "SELECT * FROM fact WHERE subject_id = ? OR object_id = ?",
            (entity_id, entity_id))}
        # facts *about* dying facts die with them, transitively (reification chains)
        for fid, r in self._reified_dependants(facts).items():
            facts.setdefault(fid, r)

        events = {r["id"]: r for r in q(
            "SELECT * FROM event WHERE entity_id = ?", (entity_id,))}
        titles = {r["id"]: r for r in q(
            "SELECT * FROM title WHERE entity_id = ?", (entity_id,))}
        secrets = {r["id"]: r for r in q(
            "SELECT * FROM secret WHERE about_id = ?", (entity_id,))}

        def merged(direct: list, indirect: list, key=lambda r: r["id"]) -> list:
            out: dict[Any, Any] = {}
            for r in [*direct, *indirect]:
                out.setdefault(key(r), r)
            return list(out.values())

        doomed: list[tuple[str, Any]] = []
        doomed += [("fact", r) for r in facts.values()]
        doomed += [("event", r) for r in events.values()]
        doomed += [("title", r) for r in titles.values()]
        doomed += [("secret", r) for r in secrets.values()]
        doomed += [("geometry", r) for r in q(
            "SELECT * FROM geometry WHERE entity_id = ?", (entity_id,))]
        doomed += [("route_segment", r) for r in q(
            "SELECT * FROM route_segment WHERE entity_id = ? "
            "OR from_entity_id = ? OR to_entity_id = ?",
            (entity_id, entity_id, entity_id))]
        doomed += [("title_holding", r) for r in merged(
            q("SELECT * FROM title_holding WHERE holder_id = ?", (entity_id,)),
            rows_in("title_holding", "title_id", titles))]
        doomed += [("event_participant", r) for r in merged(
            q("SELECT * FROM event_participant WHERE entity_id = ?", (entity_id,)),
            rows_in("event_participant", "event_id", events),
            key=lambda r: (r["event_id"], r["entity_id"], r["role"]))]
        doomed += [("scene_participant", r) for r in q(
            "SELECT * FROM scene_participant WHERE entity_id = ?", (entity_id,))]
        doomed += [("entity_override", r) for r in q(
            "SELECT * FROM entity_override WHERE entity_id = ?", (entity_id,))]
        doomed += [("interpretation", r) for r in merged(
            q("SELECT * FROM interpretation WHERE holder_id = ?", (entity_id,)),
            rows_in("interpretation", "event_id", events))]
        doomed += [("knowledge_state", r) for r in merged(
            q("SELECT * FROM knowledge_state WHERE observer_id = ? "
              "OR about_observer_id = ?", (entity_id, entity_id)),
            rows_in("knowledge_state", "secret_id", secrets))]
        doomed += [("causal_link", r) for r in merged(
            rows_in("causal_link", "cause_id", events),
            rows_in("causal_link", "effect_id", events))]
        return doomed

    def _entity_relinks(
        self, entity_id: str, doomed: list[tuple[str, Any]],
    ) -> list[tuple[str, str, dict]]:
        """(table, row_id, cleared columns) for surviving rows whose reference to this
        entity the cascade will SET NULL — logged so a restore can re-link them."""
        dying: dict[str, set[str]] = {}
        dying_facts: set[str] = set()
        for table, victim in doomed:
            if table == "fact":
                dying_facts.add(victim["id"])
            # sqlite3.Row iterates *values*, so `"id" in victim` would test the wrong
            # thing — .keys() is the correct spelling here, not a SIM118 slip.
            if "id" in victim.keys():  # noqa: SIM118
                dying.setdefault(table, set()).add(victim["id"])

        out: list[tuple[str, str, dict]] = []
        # The entity-target columns derive from _RELINK_COLUMNS — the same map restore
        # reads — so those cannot drift. Fact-target relinks (secret.fact_id) key on
        # dying *fact* ids rather than this entity and are handled below; a future
        # non-entity relink target needs its own block there.
        for table, column_targets in _RELINK_COLUMNS.items():
            columns = [c for c, target in column_targets.items() if target == "entity"]
            if not columns:
                continue
            condition = " OR ".join(f"{c} = ?" for c in columns)
            for r in self.db.query(f"SELECT * FROM {table} WHERE {condition}",
                                   [entity_id] * len(columns)):
                if r["id"] in dying.get(table, set()):
                    continue        # the row itself dies; its snapshot keeps the link
                cleared = {c: entity_id for c in columns if r[c] == entity_id}
                out.append((table, r["id"], cleared))
        # secrets that survive but point at a dying fact lose that link the same way
        for r in _rows_in(self.db, "secret", "fact_id", dying_facts):
            if r["id"] not in dying.get("secret", set()):
                out.append(("secret", r["id"], {"fact_id": r["fact_id"]}))
        return out

    def _reified_dependants(self, seed_fact_ids: Iterable[str]) -> dict[str, Any]:
        """Facts *about* the given facts, transitively — the reification closure that
        dies with them under the about_fact_id FK."""
        found: dict[str, Any] = {}
        frontier = list(seed_fact_ids)
        while frontier:
            rows = _rows_in(self.db, "fact", "about_fact_id", frontier)
            frontier = [r["id"] for r in rows if r["id"] not in found]
            for r in rows:
                found.setdefault(r["id"], r)
        return found

    def get_entity(self, entity_id: str) -> Entity | None:
        row = self.db.one("SELECT * FROM entity WHERE id = ?", (entity_id,))
        if row is None or row["branch_id"] not in self._chain:
            return None
        return self._patched(_entity(row), self._entity_override_for(entity_id))

    def entity_named(self, name: str, type_key: str | None = None) -> Entity | None:
        sql = f"SELECT * FROM entity WHERE {self._in_chain} AND name = ?"
        params: list[Any] = [*self._chain, name]
        if type_key:
            sql += " AND type_key = ?"
            params.append(type_key)
        row = self.db.one(sql, params)
        if row is None:
            return None
        return self._patched(_entity(row), self._entity_override_for(row["id"]))

    def entities(self, type_key: str | None = None, *, limit: int | None = None) -> list[Entity]:
        sql = f"SELECT * FROM entity WHERE {self._in_chain}"
        params: list[Any] = [*self._chain]
        if type_key:
            sql += " AND type_key = ?"
            params.append(type_key)
        sql += " ORDER BY name"
        if limit:
            sql += f" LIMIT {int(limit)}"
        overrides = self._override_map()
        return [self._patched(_entity(r), overrides.get(r["id"]))
                for r in self.db.query(sql, params)]

    def count_entities(self, type_key: str | None = None) -> int:
        if type_key:
            return self.db.scalar(
                f"SELECT count(*) FROM entity WHERE {self._in_chain} AND type_key = ?",
                (*self._chain, type_key),
            )
        return self.db.scalar(
            f"SELECT count(*) FROM entity WHERE {self._in_chain}", self._chain
        )

    # ---- facts ------------------------------------------------------------

    def assert_fact(
        self,
        subject: str | Entity,
        predicate_key: str,
        obj: str | Entity | None = None,
        *,
        value: Any = None,
        interval: Interval | None = None,
        valid_from: int | None = None,
        valid_to: int | None = None,
        confidence: str = "canon",
        secrecy: str = "public",
        strength: str | None = None,
        source_id: str | None = None,
        note: str = "",
        props: dict[str, Any] | None = None,
        about_fact_id: str | None = None,
    ) -> Fact:
        """Record that something is (or was) so.

        The inverse is *not* written as a second row. §106.1 says the writer must never
        enter the same fact twice, and storing both directions would mean two rows that can
        drift apart. Instead the predicate declares its inverse and reads resolve it — see
        `facts_about`, which returns incoming edges already flipped.
        """
        subject_id = subject.id if isinstance(subject, Entity) else subject
        object_id = obj.id if isinstance(obj, Entity) else obj

        predicate = PREDICATES_BY_KEY.get(predicate_key)
        if predicate is None and not self.db.one(
            "SELECT 1 FROM predicate WHERE project_id = ? AND key = ?",
            (self.project_id, predicate_key),
        ):
            raise WorldError(f"unknown predicate {predicate_key!r}")
        if object_id is None and value is None:
            raise WorldError(
                f"fact {predicate_key!r} needs either an object entity or a value"
            )

        if interval is not None:
            valid_from = interval.start.earliest
            valid_to = interval.end.latest
            valid_from_hi = interval.start.latest
            valid_to_lo = interval.end.earliest
            precision = interval.start.precision.value
        else:
            valid_from_hi = valid_to_lo = None
            precision = "exact"

        fid = new_id()
        stamp = now_iso()
        with self.db.transaction():
            self.db.insert("fact", {
                "id": fid, "project_id": self.project_id, "branch_id": self.branch_id,
                "subject_id": subject_id, "predicate_key": predicate_key,
                "object_id": object_id, "about_fact_id": about_fact_id,
                "value": None if value is None else str(value),
                "valid_from": valid_from, "valid_from_hi": valid_from_hi,
                "valid_to": valid_to, "valid_to_lo": valid_to_lo, "precision": precision,
                "confidence": confidence, "secrecy": secrecy, "strength": strength,
                "source_id": source_id, "note": note, "props": props or {},
                "created_at": stamp, "updated_at": stamp,
            })
            self._log_revision("fact", fid, "insert", None, {
                "subject_id": subject_id, "predicate_key": predicate_key,
                "object_id": object_id, "value": None if value is None else str(value),
                "valid_from": valid_from, "valid_to": valid_to,
            })
        return Fact(
            id=fid, subject_id=subject_id, predicate_key=predicate_key,
            object_id=object_id, value=None if value is None else str(value),
            about_fact_id=about_fact_id,
            valid_from=valid_from, valid_from_hi=valid_from_hi,
            valid_to=valid_to, valid_to_lo=valid_to_lo, precision=precision,
            confidence=confidence, secrecy=secrecy, strength=strength,
            source_id=source_id, note=note, props=props or {}, branch_id=self.branch_id,
        )

    def end_fact(self, fact_id: str, on_day: int) -> None:
        """Close a fact's validity rather than deleting it.

        §106.3: timeline-aware information must not overwrite history. When House Orren
        takes Greyhaven, House Marr's control *ended* — it did not become untrue.
        """
        with self.db.transaction():
            row = self.db.one("SELECT * FROM fact WHERE id = ?", (fact_id,))
            if row is None:
                # Logging an update for a row that never existed would pollute the
                # append-only log with a phantom.
                raise WorldError(f"no fact {fact_id!r} to end")
            if row["valid_from"] is not None and on_day < row["valid_from"]:
                raise WorldError(
                    "cannot end a fact before it began — that would leave an interval "
                    "true on no day at all, which silently erases the fact from every "
                    "view while appearing to keep it"
                )
            if row["branch_id"] != self.branch_id:
                if row["branch_id"] not in self._chain:
                    raise WorldError("that fact is not part of this timeline")
                # ending an inherited fact happens in this branch alone
                self._override_fact(row, {"valid_to": on_day})
                return
            self.db.update("fact", fact_id, {"valid_to": on_day, "updated_at": now_iso()})
            self._log_revision("fact", fact_id, "update",
                               {"valid_to": row["valid_to"]}, {"valid_to": on_day})

    def delete_fact(self, fact_id: str) -> None:
        """Remove a fact outright.

        Ending a fact is almost always right; deletion is for the entry that was simply a
        mistake. The revision log keeps what was deleted, so even this is recoverable —
        including any facts *about* this one, which the FK cascade takes with it.
        """
        with self.db.transaction():
            row = self.db.one("SELECT * FROM fact WHERE id = ?", (fact_id,))
            if row is None:
                return
            if row["branch_id"] != self.branch_id:
                if row["branch_id"] not in self._chain:
                    raise WorldError("that fact is not part of this timeline")
                # Deleting an inherited fact is a branch-local tombstone: the row
                # stands in every other timeline, hidden in this one. Facts *about*
                # it die with it in canon, so they must be hidden here too — an
                # inherited one gets its own tombstone, a branch-local one is simply
                # deleted.
                closure = [row, *self._reified_dependants([fact_id]).values()]
                closure_ids = {doomed["id"] for doomed in closure}
                for doomed in closure:
                    if doomed["branch_id"] == self.branch_id:
                        if (doomed["supersedes_id"] is not None
                                and doomed["supersedes_id"] in closure_ids):
                            # the branch's own edit of an inherited row also in this
                            # closure: its tombstone is written when that row is
                            # processed — deleting it here would destroy the tombstone
                            continue
                        if self.db.one("SELECT 1 FROM fact WHERE id = ?",
                                       (doomed["id"],)):
                            self.delete_fact(doomed["id"])   # own row: the real path
                        continue
                    if doomed["branch_id"] not in self._chain:
                        continue        # another timeline's row; not ours to hide
                    effective = self._branch_override_row(doomed["id"]) or doomed
                    props = decode_json(effective["props"], {}) or {}
                    if props.get("branch_tombstone"):
                        continue
                    props["branch_tombstone"] = True
                    self._override_fact(doomed, {"props": props})
                return
            if row["supersedes_id"] is not None:
                target = self.db.one("SELECT * FROM fact WHERE id = ?",
                                     (row["supersedes_id"],))
                if (target is not None and target["branch_id"] != self.branch_id
                        and target["branch_id"] in self._chain):
                    # This row is the branch's own edit of an inherited fact, and the
                    # writer is deleting the fact they see. Hard-deleting the override
                    # would resurrect the inherited original here — the intent is
                    # "gone in this timeline", so the override becomes a tombstone.
                    props = decode_json(row["props"], {}) or {}
                    props["branch_tombstone"] = True
                    self._override_fact(target, {"props": props})
                    return
            self._delete_fact_row(row)

    def _delete_fact_row(self, row) -> None:
        """Physically remove one fact row, with its full cascade logged.

        The raw operation behind delete_fact's routing — and the one undo must use
        directly, because inverting an "insert" means removing exactly that row, never
        re-routing through intent-preserving tombstone logic.
        """
        fact_id = row["id"]
        with self.db.transaction():
            marker = f"cascade:{fact_id}"
            dependants = self._reified_dependants([fact_id])
            dependants.pop(fact_id, None)      # its own record closes the batch below
            for r in dependants.values():
                self._log_revision("fact", r["id"], "delete", _snapshot(r), None,
                                   note=marker)
            for r in _rows_in(self.db, "secret", "fact_id", [fact_id, *dependants]):
                self._log_revision("secret", r["id"], "update",
                                   {"fact_id": r["fact_id"]}, {"fact_id": None},
                                   note=marker)
            self._log_revision("fact", fact_id, "delete", _snapshot(row), None)
            self.db.execute("DELETE FROM fact WHERE id = ?", (fact_id,))

    def transfer(
        self,
        predicate_key: str,
        obj: str | Entity,
        new_subject: str | Entity,
        on_day: int,
        **kw: Any,
    ) -> Fact:
        """Hand something over: close the incumbent's fact, open the successor's.

        This is the shape of every conquest, inheritance and appointment in the model, and
        the reason a map of year 215 and a map of year 315 can disagree without either
        being wrong.
        """
        object_id = obj.id if isinstance(obj, Entity) else obj
        # One transaction: the closing of the incumbent's interval and the opening of
        # the successor's are one event in the world — atomic on disk, and one action
        # to undo, never a half-transfer.
        with self.db.transaction():
            for existing in self.facts_where(predicate_key, object_id=object_id,
                                             at=on_day):
                self.end_fact(existing.id, on_day)
            return self.assert_fact(new_subject, predicate_key, object_id,
                                    valid_from=on_day, **kw)

    def get_fact(self, fact_id: str) -> Fact | None:
        row = self.db.one("SELECT * FROM fact WHERE id = ?", (fact_id,))
        if row is None or row["branch_id"] not in self._chain:
            return None
        # Follow this timeline's overrides to the row that actually speaks for the
        # fact here — possibly through several branches, ending at a tombstone. When
        # more than one chain branch overrides the same row, the nearest one speaks.
        rank = {b: i for i, b in enumerate(self._chain)}
        while len(self._chain) > 1:
            overrides = self.db.query(
                f"SELECT * FROM fact WHERE supersedes_id = ? AND {self._in_chain}",
                (row["id"], *self._chain))
            if not overrides:
                break
            row = min(overrides, key=lambda r: rank[r["branch_id"]])
        if (decode_json(row["props"], {}) or {}).get("branch_tombstone"):
            return None
        return _fact(row)

    def facts_where(
        self,
        predicate_key: str | None = None,
        *,
        subject_id: str | None = None,
        object_id: str | None = None,
        at: int | None = None,
        include_secret: bool = True,
    ) -> list[Fact]:
        live, live_params = self._live_fact("fact")
        sql = [f"SELECT * FROM fact WHERE {self._in_chain} AND {live}"]
        params: list[Any] = [*self._chain, *live_params]
        if predicate_key:
            sql.append("AND predicate_key = ?")
            params.append(predicate_key)
        if subject_id:
            sql.append("AND subject_id = ?")
            params.append(subject_id)
        if object_id:
            sql.append("AND object_id = ?")
            params.append(object_id)
        if at is not None:
            sql.append("AND (valid_from IS NULL OR valid_from <= ?)")
            sql.append("AND (valid_to IS NULL OR valid_to >= ?)")
            params.extend([at, at])
        if not include_secret:
            sql.append("AND secrecy NOT IN ('secret','deep_secret')")
        return [_fact(r) for r in self.db.query(" ".join(sql), params)]

    def facts_about(self, entity_id: str, *, at: int | None = None) -> list[Fact]:
        """Every fact touching this entity, with incoming edges flipped to read outward.

        §77: if House Veyne controls Greyhaven, Greyhaven's page must show House Veyne
        without the writer having entered the fact a second time.
        """
        outgoing = self.facts_where(subject_id=entity_id, at=at)
        incoming = self.facts_where(object_id=entity_id, at=at)
        flipped = []
        for f in incoming:
            inverse = inverse_of(f.predicate_key)
            if inverse is None:
                continue
            flipped.append(Fact(
                id=f.id, subject_id=entity_id, predicate_key=inverse,
                object_id=f.subject_id, value=f.value,
                valid_from=f.valid_from, valid_from_hi=f.valid_from_hi,
                valid_to=f.valid_to, valid_to_lo=f.valid_to_lo, precision=f.precision,
                confidence=f.confidence, secrecy=f.secrecy, strength=f.strength,
                source_id=f.source_id, note=f.note, props=f.props, branch_id=f.branch_id,
            ))
        return outgoing + flipped

    def value_of(self, entity_id: str, predicate_key: str, *, at: int | None = None) -> str | None:
        facts = self.facts_where(predicate_key, subject_id=entity_id, at=at)
        return facts[-1].value if facts else None

    # ---- world state (§3, §36) --------------------------------------------

    def state_at(self, day: int, *, include_secret: bool = True) -> StateAtDate:
        """Everything true on one day.

        A single indexed range scan over the fact spine, because temporality is uniform.
        The equivalent in a design where dates were bolted onto individual subsystems would
        be one bespoke query per subsystem, and they would drift.
        """
        if len(self._chain) == 1:
            entity_rows = self.db.query(
                """SELECT * FROM entity
                   WHERE branch_id = ?
                     AND (exists_from IS NULL OR exists_from <= ?)
                     AND (exists_to   IS NULL OR exists_to   >= ?)""",
                (self.branch_id, day, day),
            )
            entities = {r["id"]: _entity(r) for r in entity_rows}
        else:
            # Branch overrides can move an entity's existence either way, so the
            # interval test must run on the *patched* values, not in SQL.
            overrides = self._override_map()
            entities = {}
            for r in self.db.query(
                f"SELECT * FROM entity WHERE {self._in_chain}", self._chain,
            ):
                e = self._patched(_entity(r), overrides.get(r["id"]))
                if e.exists_on(day):
                    entities[e.id] = e

        live, live_params = self._live_fact("fact")
        sql = f"""SELECT * FROM fact
                 WHERE {self._in_chain} AND {live}
                   AND (valid_from IS NULL OR valid_from <= ?)
                   AND (valid_to   IS NULL OR valid_to   >= ?)"""
        params: list[Any] = [*self._chain, *live_params, day, day]
        if not include_secret:
            sql += " AND secrecy NOT IN ('secret','deep_secret')"
        facts = [_fact(r) for r in self.db.query(sql, params)]
        # A fact whose participants had not yet been born (or were long dead) is not part
        # of the world on this day, even if its own dates are silent.
        facts = [
            f for f in facts
            if f.subject_id in entities and (f.object_id is None or f.object_id in entities)
        ]

        holders = {}
        for row in self.db.query(
            f"""SELECT t.id AS title_id, h.holder_id
               FROM title t LEFT JOIN title_holding h
                 ON h.title_id = t.id
                AND (h.{self._in_chain} OR h.branch_id IS NULL)
                AND (h.from_day IS NULL OR h.from_day <= ?)
                AND (h.to_day   IS NULL OR h.to_day   >= ?)
               WHERE t.{self._in_chain}
               ORDER BY h.from_day ASC NULLS FIRST""",
            (*self._chain, day, day, *self._chain),
        ):
            holders[row["title_id"]] = row["holder_id"]

        return StateAtDate(day=day, entities=entities, facts=facts, titles=holders)

    # ---- graph traversal (§49) --------------------------------------------

    def follow(
        self,
        start_id: str,
        predicate_key: str,
        *,
        direction: str = "out",
        max_depth: int = 10,
        at: int | None = None,
    ) -> list[tuple[str, int]]:
        """Walk a transitive predicate, returning (entity_id, depth) pairs.

        Recursive CTE rather than a Python loop so SQLite does the work in one statement.
        This is the query that motivated the ANALYZE policy in the store — see db.py.

        The walk carries the path it took so it can refuse to revisit a node. Worlds do
        contain loops — mutual oaths, a region recorded as inside its own province — and a
        plain `UNION` will not stop on one: it deduplicates whole rows, and `(House A, 3)`
        differs from `(House A, 5)`, so a two-node cycle happily generates rows all the way
        to the depth limit. Excluding nodes already on the path terminates properly, and
        grouping by id keeps the shortest route to anything reachable two ways.
        """
        temporal = ""
        if at is not None:
            temporal = ("AND (f.valid_from IS NULL OR f.valid_from <= ?) "
                        "AND (f.valid_to   IS NULL OR f.valid_to   >= ?)")

        if direction == "out":
            next_id = "f.object_id"
            join = "f.subject_id = w.id"
        elif direction == "in":
            next_id = "f.subject_id"
            join = "f.object_id = w.id"
        else:
            raise WorldError("direction must be 'out' or 'in'")

        scope = self._in_chain
        live, live_params = self._live_fact("f")
        sql = f"""
            WITH RECURSIVE walk(id, depth, path) AS (
                SELECT ?, 0, ',' || ? || ','
                UNION ALL
                SELECT {next_id}, w.depth + 1, w.path || {next_id} || ','
                FROM fact f JOIN walk w ON {join}
                WHERE f.{scope} AND {live} AND f.predicate_key = ?
                  AND f.object_id IS NOT NULL
                  AND w.depth < ?
                  AND instr(w.path, ',' || {next_id} || ',') = 0
                  {temporal}
            )
            SELECT id, min(depth) AS depth FROM walk WHERE depth > 0
            GROUP BY id ORDER BY depth, id
        """
        # Placeholder order follows the SQL above: start id twice (value and path seed),
        # the branch chain, the visibility chain, predicate, depth guard, then the two
        # temporal bounds if a date was given.
        params: list[Any] = [start_id, start_id, *self._chain, *live_params,
                             predicate_key, max_depth]
        if at is not None:
            params.extend([at, at])
        return [(r["id"], r["depth"]) for r in self.db.query(sql, params)]

    def neighbours(
        self,
        start_id: str,
        predicate_keys: Sequence[str],
        *,
        hops: int = 1,
        at: int | None = None,
    ) -> dict[str, int]:
        """Everyone within `hops` of `start_id` along any of `predicate_keys`, either way.

        Answers §49's "who is related to Lady Mara within three generations". Written as a
        Python breadth-first walk over indexed point lookups rather than one recursive CTE:
        with several predicates and both directions the CTE becomes unreadable, and the
        measured difference is negligible (0.15 ms vs 0.13 ms at 200k relationships).
        """
        placeholders = ", ".join("?" for _ in predicate_keys)
        temporal = ""
        extra: list[Any] = []
        if at is not None:
            temporal = ("AND (valid_from IS NULL OR valid_from <= ?) "
                        "AND (valid_to   IS NULL OR valid_to   >= ?)")
            extra = [at, at]

        live, live_params = self._live_fact("fact")
        out_sql = (f"SELECT object_id AS other FROM fact WHERE {self._in_chain} "
                   f"AND {live} "
                   f"AND subject_id = ? AND predicate_key IN ({placeholders}) "
                   f"AND object_id IS NOT NULL {temporal}")
        in_sql = (f"SELECT subject_id AS other FROM fact WHERE {self._in_chain} "
                  f"AND {live} "
                  f"AND object_id = ? AND predicate_key IN ({placeholders}) {temporal}")

        seen: dict[str, int] = {start_id: 0}
        frontier = [start_id]
        for depth in range(1, hops + 1):
            nxt: list[str] = []
            for node in frontier:
                params = [*self._chain, *live_params, node, *predicate_keys, *extra]
                for sql in (out_sql, in_sql):
                    for row in self.db.query(sql, params):
                        other = row["other"]
                        if other and other not in seen:
                            seen[other] = depth
                            nxt.append(other)
            frontier = nxt
            if not frontier:
                break
        del seen[start_id]
        return seen

    # ---- search (§53) -----------------------------------------------------

    def search(self, text: str, *, limit: int = 30, type_key: str | None = None) -> list[Entity]:
        """Universal search: exact and stemmed first, then fuzzy substring.

        The porter index answers word queries; the trigram index catches the case §53 calls
        fuzzy match, where the writer half-remembers a name. Results keep porter-order
        first because an exact hit should never rank below a substring accident.
        """
        text = text.strip()
        if not text:
            return []

        ids: list[str] = []
        seen: set[str] = set()

        def in_scope(entity_id: str) -> bool:
            return self.db.one(
                f"SELECT 1 FROM entity WHERE id = ? AND {self._in_chain}",
                (entity_id, *self._chain)) is not None

        def collect(sql: str, params: tuple) -> None:
            """Page through ranked candidates until `limit` in-scope ids are found.

            The indexes are not branch-aware, so any other timeline's rows can crowd
            the top of the ranking without bound — a fixed pool merely raises the
            bar. Filtering to this timeline *while* paging is the only exact answer.
            """
            offset = 0
            page = max(limit, 25)
            while len(ids) < limit:
                rows = self.db.query(f"{sql} LIMIT ? OFFSET ?",
                                     (*params, page, offset))
                if not rows:
                    return
                offset += page
                for row in rows:
                    eid = row["entity_id"]
                    if eid in seen:
                        continue
                    seen.add(eid)
                    if in_scope(eid):
                        ids.append(eid)
                        if len(ids) >= limit:
                            return

        # Weighted ranking: a hit in the NAME outranks a hit in the summary or
        # tags. Unweighted bm25 put Northwatch above The Northmarch for the query
        # "Northmarch", because Northwatch's summary mentions the region — precisely
        # the wrong entity picked with full confidence. A query the FTS parser
        # rejects degrades to the fuzzy pass rather than raising at the user.
        with contextlib.suppress(Exception):
            collect(
                "SELECT entity_id FROM entity_fts WHERE entity_fts MATCH ? "
                "ORDER BY bm25(entity_fts, 10.0, 2.0, 1.0)",
                (_fts_query(text),),
            )

        if len(ids) < limit:
            collect(
                "SELECT entity_id FROM entity_trigram WHERE name LIKE ?",
                (f"%{text}%",),
            )

        found = [self.get_entity(i) for i in ids]     # patches branch overrides
        result = [e for e in found if e is not None]
        if type_key:
            result = [e for e in result if e.type_key == type_key]
        return result[:limit]

    # ---- events -----------------------------------------------------------

    def add_event(
        self,
        name: str,
        *,
        type_key: str = "event",
        summary: str = "",
        start_day: int | None = None,
        end_day: int | None = None,
        location_id: str | None = None,
        participants: Iterable[tuple[str, str]] = (),
        confidence: str = "canon",
        secrecy: str = "public",
        props: dict | None = None,
        entity_id: str | None = None,
    ) -> Event:
        eid = new_id()
        stamp = now_iso()
        with self.db.transaction():
            self.db.insert("event", {
                "id": eid, "project_id": self.project_id, "branch_id": self.branch_id,
                "type_key": type_key, "name": name, "summary": summary,
                "start_day": start_day, "end_day": end_day, "location_id": location_id,
                # entity_id ties the event's lifetime to an entity: it dies (and is
                # restored) with them, which the delete cascade models in full.
                "entity_id": entity_id,
                "confidence": confidence, "secrecy": secrecy, "props": props or {},
                "created_at": stamp, "updated_at": stamp,
            })
            self._log_revision("event", eid, "insert", None, {"name": name})
            for entity_id, role in participants:
                self.db.insert("event_participant", {
                    "event_id": eid, "entity_id": entity_id, "role": role,
                })
                self._log_revision("event_participant", f"{eid}/{entity_id}/{role}",
                                   "insert", None, {"role": role})
        return Event(id=eid, name=name, type_key=type_key, summary=summary,
                     start_day=start_day, end_day=end_day, location_id=location_id,
                     confidence=confidence, secrecy=secrecy, props=props or {})

    def get_event(self, event_id: str) -> Event | None:
        row = self.db.one("SELECT * FROM event WHERE id = ?", (event_id,))
        if row is None or row["branch_id"] not in self._chain:
            return None
        return _event(row)

    def events(self, *, first: int | None = None, last: int | None = None) -> list[Event]:
        sql = f"SELECT * FROM event WHERE {self._in_chain}"
        params: list[Any] = [*self._chain]
        if first is not None:
            sql += " AND (end_day IS NULL AND start_day >= ? OR end_day >= ?)"
            params.extend([first, first])
        if last is not None:
            sql += " AND (start_day IS NULL OR start_day <= ?)"
            params.append(last)
        sql += " ORDER BY start_day"
        return [_event(r) for r in self.db.query(sql, params)]

    def event_participants(self, event_id: str) -> list[tuple[Entity, str]]:
        rows = self.db.query(
            "SELECT e.*, p.role FROM event_participant p JOIN entity e ON e.id = p.entity_id "
            "WHERE p.event_id = ?",
            (event_id,),
        )
        overrides = self._override_map()
        return [(self._patched(_entity(r), overrides.get(r["id"])), r["role"])
                for r in rows]

    def events_involving(self, entity_id: str, *, first: int | None = None,
                         last: int | None = None) -> list[Event]:
        sql = (f"SELECT e.* FROM event e JOIN event_participant p ON p.event_id = e.id "
               f"WHERE e.{self._in_chain} AND p.entity_id = ?")
        params: list[Any] = [*self._chain, entity_id]
        if first is not None:
            sql += " AND (e.start_day IS NULL OR e.start_day >= ?)"
            params.append(first)
        if last is not None:
            sql += " AND (e.start_day IS NULL OR e.start_day <= ?)"
            params.append(last)
        sql += " ORDER BY e.start_day"
        return [_event(r) for r in self.db.query(sql, params)]

    def link_cause(self, cause_id: str, effect_id: str, *, kind: str = "caused",
                   note: str = "") -> bool:
        """Record that one event led to another (§32).

        Returns False when the link already exists: recording the same causation twice
        is a double-click, not an error, and the schema's UNIQUE constraint should not
        surface as a crash. Self-links and cycles are refused outright — a chain that
        loops makes an event its own consequence and sends every downstream traversal
        in circles.
        """
        if cause_id == effect_id:
            raise WorldError("an event cannot cause itself")
        # One transaction around check-then-insert: it holds the connection lock, so
        # two concurrent requests cannot both pass the guards and then collide on the
        # UNIQUE constraint — or worse, commit A→B and B→A as a loop.
        with self.db.transaction():
            if self.db.one(
                f"SELECT 1 FROM causal_link WHERE cause_id = ? AND effect_id = ? "
                f"AND ({self._in_chain} OR branch_id IS NULL)",
                (cause_id, effect_id, *self._chain),
            ):
                return False
            if self._causally_reaches(effect_id, cause_id):
                raise WorldError(
                    "that would close a causal loop — the second event already leads "
                    "back to the first"
                )
            link_id = new_id()
            self.db.insert("causal_link", {
                "id": link_id, "project_id": self.project_id, "cause_id": cause_id,
                "effect_id": effect_id, "kind": kind, "note": note,
                "branch_id": self.branch_id,
            })
            self._log_revision("causal_link", link_id, "insert", None, {"kind": kind})
        return True

    def _causally_reaches(self, origin_id: str, target_id: str) -> bool:
        """Whether target lies downstream of origin along causal links.

        Full reachability, no depth cap: a cap would let a chain longer than the cap
        close a cycle unnoticed. The UNION works over bare ids, not paths, so the walk
        reaches a fixpoint — and terminates — even if a crafted file already holds a
        loop.
        """
        return self.db.one(
            f"""WITH RECURSIVE reach(id) AS (
                   SELECT ?
                   UNION
                   SELECT c.effect_id FROM causal_link c
                   JOIN reach ON c.cause_id = reach.id
                   WHERE c.{self._in_chain} OR c.branch_id IS NULL
               )
               SELECT 1 FROM reach WHERE id = ?""",
            (origin_id, *self._chain, target_id),
        ) is not None

    def consequences_of(self, event_id: str, *, max_depth: int = 6) -> list[tuple[str, int]]:
        """§32's consequence explorer: the causal chain downstream of an event.

        An event reachable along two chains (a diamond) is reported once, at its
        shortest distance — the UNION alone dedupes (id, depth) pairs, not ids.
        """
        rows = self.db.query(
            f"""WITH RECURSIVE chain(id, depth) AS (
                   SELECT ?, 0
                   UNION
                   SELECT c.effect_id, chain.depth + 1
                   FROM causal_link c JOIN chain ON c.cause_id = chain.id
                   WHERE chain.depth < ?
                     AND (c.{self._in_chain} OR c.branch_id IS NULL)
               )
               SELECT id, min(depth) AS depth FROM chain
               WHERE depth > 0 AND id <> ?
               GROUP BY id ORDER BY depth, id""",
            (event_id, max_depth, *self._chain, event_id),
        )
        return [(r["id"], r["depth"]) for r in rows]

    # ---- titles (§8) ------------------------------------------------------

    def add_title(self, name: str, *, rank: int = 0, territory_id: str | None = None,
                  succession_law: str = "male_preference_primogeniture",
                  dynasty_root_id: str | None = None, created_on: int | None = None,
                  entity_id: str | None = None) -> Title:
        tid = new_id()
        with self.db.transaction():
            self.db.insert("title", {
                "id": tid, "project_id": self.project_id, "branch_id": self.branch_id,
                "entity_id": entity_id, "name": name, "rank": rank,
                "territory_id": territory_id, "succession_law": succession_law,
                "dynasty_root_id": dynasty_root_id, "created_on": created_on,
                "created_at": now_iso(),
            })
            self._log_revision("title", tid, "insert", None, {"name": name})
        return Title(id=tid, name=name, rank=rank, territory_id=territory_id,
                     succession_law=succession_law, dynasty_root_id=dynasty_root_id,
                     created_on=created_on, entity_id=entity_id)

    def titles(self) -> list[Title]:
        return [_title(r) for r in self.db.query(
            f"SELECT * FROM title WHERE {self._in_chain} ORDER BY rank DESC, name",
            self._chain,
        )]

    def get_title(self, title_id: str) -> Title | None:
        row = self.db.one("SELECT * FROM title WHERE id = ?", (title_id,))
        if row is None or row["branch_id"] not in self._chain:
            return None
        return _title(row)

    def title_named(self, name: str) -> Title | None:
        row = self.db.one(f"SELECT * FROM title WHERE {self._in_chain} AND name = ?",
                          (*self._chain, name))
        return _title(row) if row else None

    def grant_title(self, title_id: str, holder_id: str, *, from_day: int | None = None,
                    to_day: int | None = None, how: str = "inheritance",
                    disputed: bool = False, note: str = "") -> TitleHolding:
        hid = new_id()
        with self.db.transaction():
            self.db.insert("title_holding", {
                "id": hid, "title_id": title_id, "holder_id": holder_id,
                "from_day": from_day, "to_day": to_day, "how": how,
                "disputed": int(disputed), "note": note,
                "branch_id": self.branch_id,
            })
            self._log_revision("title_holding", hid, "insert", None, {"how": how})
        return TitleHolding(id=hid, title_id=title_id, holder_id=holder_id,
                            from_day=from_day, to_day=to_day, how=how,
                            disputed=disputed, note=note)

    def title_holdings(self, title_id: str) -> list[TitleHolding]:
        return [_holding(r) for r in self.db.query(
            f"SELECT * FROM title_holding WHERE title_id = ? "
            f"AND ({self._in_chain} OR branch_id IS NULL) ORDER BY from_day",
            (title_id, *self._chain),
        )]

    def title_holder_on(self, title_id: str, day: int) -> str | None:
        return self.db.scalar(
            f"""SELECT holder_id FROM title_holding
               WHERE title_id = ?
                 AND ({self._in_chain} OR branch_id IS NULL)
                 AND (from_day IS NULL OR from_day <= ?)
                 AND (to_day   IS NULL OR to_day   >= ?)
               ORDER BY from_day DESC LIMIT 1""",
            (title_id, *self._chain, day, day),
        )

    def titles_held_by(self, holder_id: str, *, at: int | None = None) -> list[Title]:
        sql = (f"SELECT DISTINCT t.* FROM title t "
               f"JOIN title_holding h ON h.title_id = t.id "
               f"WHERE h.holder_id = ? AND (h.{self._in_chain} OR h.branch_id IS NULL)")
        params: list[Any] = [holder_id, *self._chain]
        if at is not None:
            sql += (" AND (h.from_day IS NULL OR h.from_day <= ?)"
                    " AND (h.to_day   IS NULL OR h.to_day   >= ?)")
            params.extend([at, at])
        titles = [_title(r) for r in self.db.query(sql, params)]
        if at is None:
            return titles
        # Agree with title_holder_on: on a day, you hold a title only if you are the
        # one it resolves to — a later grant (a branch's coup, say) displaces you.
        return [t for t in titles if self.title_holder_on(t.id, at) == holder_id]

    # ---- secrets and knowledge (§6) ---------------------------------------

    def add_secret(self, name: str, *, truth: str = "", about_id: str | None = None,
                   fact_id: str | None = None, severity: str = "major") -> Secret:
        sid = new_id()
        with self.db.transaction():
            self.db.insert("secret", {
                "id": sid, "project_id": self.project_id, "branch_id": self.branch_id,
                "name": name, "truth": truth, "about_id": about_id, "fact_id": fact_id,
                "severity": severity, "created_at": now_iso(),
            })
            self._log_revision("secret", sid, "insert", None, {"name": name})
        return Secret(id=sid, name=name, truth=truth, about_id=about_id,
                      fact_id=fact_id, severity=severity)

    def secrets(self) -> list[Secret]:
        return [_secret(r) for r in self.db.query(
            f"SELECT * FROM secret WHERE {self._in_chain} ORDER BY name", self._chain
        )]

    def set_knowledge(self, observer_id: str, secret_id: str, stance: str, *,
                      about_observer_id: str | None = None,
                      acquired_on: int | None = None,
                      acquired_from: str | None = None,
                      scene_id: str | None = None, note: str = "") -> Knowledge:
        kid = new_id()
        with self.db.transaction():
            self.db.insert("knowledge_state", {
                "id": kid, "project_id": self.project_id, "branch_id": self.branch_id,
                "observer_id": observer_id, "secret_id": secret_id, "stance": stance,
                "about_observer_id": about_observer_id, "acquired_on": acquired_on,
                "acquired_from": acquired_from, "scene_id": scene_id, "note": note,
                "created_at": now_iso(),
            })
            self._log_revision("knowledge_state", kid, "insert", None,
                               {"stance": stance})
        return Knowledge(id=kid, observer_id=observer_id, secret_id=secret_id,
                         stance=stance, about_observer_id=about_observer_id,
                         acquired_on=acquired_on, acquired_from=acquired_from,
                         scene_id=scene_id, note=note)

    def knowledge_of(self, secret_id: str, *, stance: str | None = None,
                     at: int | None = None) -> list[Knowledge]:
        """§6: 'who knows X' and 'who believes X' must be separately answerable."""
        sql = f"SELECT * FROM knowledge_state WHERE {self._in_chain} AND secret_id = ?"
        params: list[Any] = [*self._chain, secret_id]
        if stance:
            sql += " AND stance = ?"
            params.append(stance)
        if at is not None:
            sql += " AND (acquired_on IS NULL OR acquired_on <= ?)"
            params.append(at)
        return [_knowledge(r) for r in self.db.query(sql, params)]

    def knowledge_held_by(self, observer_id: str, *, at: int | None = None) -> list[Knowledge]:
        sql = f"SELECT * FROM knowledge_state WHERE {self._in_chain} AND observer_id = ?"
        params: list[Any] = [*self._chain, observer_id]
        if at is not None:
            sql += " AND (acquired_on IS NULL OR acquired_on <= ?)"
            params.append(at)
        return [_knowledge(r) for r in self.db.query(sql, params)]

    # ---- manuscript (§43, §44) --------------------------------------------

    def add_work(self, title: str, *, kind: str = "novel", position: int = 0,
                 summary: str = "") -> str:
        wid = new_id()
        self.db.insert("work", {"id": wid, "project_id": self.project_id, "title": title,
                                "kind": kind, "position": position, "summary": summary})
        return wid

    def add_chapter(self, work_id: str, title: str, *, position: int = 0,
                    summary: str = "") -> str:
        cid = new_id()
        self.db.insert("chapter", {"id": cid, "work_id": work_id, "title": title,
                                   "position": position, "summary": summary})
        return cid

    def add_scene(self, title: str, *, chapter_id: str | None = None, position: int = 0,
                  day: int | None = None, end_day: int | None = None,
                  location_id: str | None = None, pov_id: str | None = None,
                  objective: str = "", conflict: str = "", outcome: str = "",
                  notes: str = "", participants: Iterable[str] = ()) -> Scene:
        sid = new_id()
        stamp = now_iso()
        with self.db.transaction():
            self.db.insert("scene", {
                "id": sid, "project_id": self.project_id, "branch_id": self.branch_id,
                "chapter_id": chapter_id, "title": title, "position": position,
                "day": day, "end_day": end_day, "location_id": location_id,
                "pov_id": pov_id, "objective": objective, "conflict": conflict,
                "outcome": outcome, "notes": notes,
                "created_at": stamp, "updated_at": stamp,
            })
            self._log_revision("scene", sid, "insert", None, {"title": title})
            for entity_id in participants:
                self.db.insert("scene_participant",
                               {"scene_id": sid, "entity_id": entity_id, "role": "present"})
                self._log_revision("scene_participant", f"{sid}/{entity_id}",
                                   "insert", None, None)
        return Scene(id=sid, title=title, chapter_id=chapter_id, position=position,
                     day=day, end_day=end_day, location_id=location_id, pov_id=pov_id,
                     objective=objective, conflict=conflict, outcome=outcome, notes=notes)

    def scenes(self) -> list[Scene]:
        return [_scene(r) for r in self.db.query(
            f"SELECT * FROM scene WHERE {self._in_chain} ORDER BY position, day",
            self._chain,
        )]

    def get_scene(self, scene_id: str) -> Scene | None:
        row = self.db.one("SELECT * FROM scene WHERE id = ?", (scene_id,))
        if row is None or row["branch_id"] not in self._chain:
            return None
        return _scene(row)

    def scene_participants(self, scene_id: str) -> list[Entity]:
        overrides = self._override_map()
        return [self._patched(_entity(r), overrides.get(r["id"]))
                for r in self.db.query(
                    "SELECT e.* FROM scene_participant p JOIN entity e ON e.id = p.entity_id "
                    "WHERE p.scene_id = ? ORDER BY e.name",
                    (scene_id,),
                )]

    # ---- geography --------------------------------------------------------

    def add_geometry(self, entity_id: str, kind: str, coordinates: Any, *,
                     valid_from: int | None = None, valid_to: int | None = None,
                     layer: str = "base", style: dict | None = None,
                     approximate: bool = False,
                     props: dict | None = None) -> Geometry:
        """Draw a shape. `style` is what the client renders with; `props` is what the
        application knows about the shape and never draws — provenance above all."""
        gid = new_id()
        with self.db.transaction():
            self.db.insert("geometry", {
                "id": gid, "project_id": self.project_id, "branch_id": self.branch_id,
                "entity_id": entity_id, "kind": kind, "coordinates": coordinates,
                "valid_from": valid_from, "valid_to": valid_to, "layer": layer,
                "style": style or {}, "props": props or {},
                "approximate": int(approximate), "created_at": now_iso(),
            })
            self._index_geometry(gid, coordinates)
            self._log_revision("geometry", gid, "insert", None, {"kind": kind})
        return Geometry(id=gid, entity_id=entity_id, kind=kind, coordinates=coordinates,
                        valid_from=valid_from, valid_to=valid_to, layer=layer,
                        style=style or {}, approximate=approximate, props=props or {})

    def _index_geometry(self, geometry_id: str, coordinates: Any) -> None:
        xs, ys = _flatten_coords(coordinates)
        if not xs:
            return
        cur = self.db.execute(
            "INSERT INTO geometry_bbox (min_x, max_x, min_y, max_y) VALUES (?, ?, ?, ?)",
            (min(xs), max(xs), min(ys), max(ys)),
        )
        self.db.insert("geometry_rtree_map",
                       {"rtree_id": cur.lastrowid, "geometry_id": geometry_id})

    def delete_geometry(self, geometry_id: str) -> None:
        """Remove one shape, its R*Tree box and its index row.

        The R*Tree is a virtual table and knows nothing about foreign keys, so its box
        outlives the row unless it is removed by hand — a leak that grows the file
        forever. Both existing delete paths do this; so must this one.
        """
        with self.db.transaction():
            row = self.db.one("SELECT * FROM geometry WHERE id = ?", (geometry_id,))
            if row is None:
                return
            if row["branch_id"] != self.branch_id:
                raise WorldError(
                    "that shape belongs to another timeline — redraw it there")
            self.db.execute(
                "DELETE FROM geometry_bbox WHERE id IN ("
                " SELECT rtree_id FROM geometry_rtree_map WHERE geometry_id = ?)",
                (geometry_id,))
            self._log_revision("geometry", geometry_id, "delete", _snapshot(row), None)
            self.db.execute("DELETE FROM geometry WHERE id = ?", (geometry_id,))

    def geometry_index(self, *, at: int | None = None,
                       layer: str | None = None) -> dict[str, list[Geometry]]:
        """Every shape, grouped by entity, from ONE query.

        `geometry_for` loads and decodes the whole table per call, so asking it about
        three hundred settlements in a loop costs three hundred full scans. Anything
        that walks many entities builds this once instead.
        """
        out: dict[str, list[Geometry]] = {}
        for geometry in self.geometries(at=at, layer=layer):
            out.setdefault(geometry.entity_id, []).append(geometry)
        return out

    def geometries(self, *, at: int | None = None, layer: str | None = None) -> list[Geometry]:
        sql = f"SELECT * FROM geometry WHERE {self._in_chain}"
        params: list[Any] = [*self._chain]
        if layer:
            sql += " AND layer = ?"
            params.append(layer)
        if at is not None:
            sql += (" AND (valid_from IS NULL OR valid_from <= ?)"
                    " AND (valid_to   IS NULL OR valid_to   >= ?)")
            params.extend([at, at])
        # Explicit order: without it the planner decides, so paint order — and any test
        # asserting over it — changes the day ANALYZE runs.
        sql += " ORDER BY layer, entity_id, id"
        return [_geometry(r) for r in self.db.query(sql, params)]

    def geometry_for(self, entity_id: str, *, at: int | None = None) -> Geometry | None:
        rows = self.geometries(at=at)
        for g in rows:
            if g.entity_id == entity_id:
                return g
        return None

    def geometries_in_view(self, min_x: float, min_y: float, max_x: float, max_y: float,
                           *, at: int | None = None) -> list[Geometry]:
        """§34: what falls inside the viewport, via the R*Tree index."""
        rows = self.db.query(
            """SELECT m.geometry_id FROM geometry_bbox b
               JOIN geometry_rtree_map m ON m.rtree_id = b.id
               WHERE b.max_x >= ? AND b.min_x <= ? AND b.max_y >= ? AND b.min_y <= ?""",
            (min_x, max_x, min_y, max_y),
        )
        wanted = {r["geometry_id"] for r in rows}
        return [g for g in self.geometries(at=at) if g.id in wanted]

    # ---- the ground itself -------------------------------------------------

    def save_terrain(self, *, seed: str, size: int, span: float, origin_x: float,
                     origin_y: float, sea_level: float,
                     fields: dict[str, list[list[float]]]) -> None:
        """Keep the surface the accepted map was drawn from.

        Everything else the map generator emits is geometry, and geometry is what the
        browser draws. Relief is not geometry: it is a height at every position, and the
        picture a reader recognises as a physical map is made by lighting that surface.
        The outlines derived from it cannot be lit.

        Kept rather than recomputed on demand, because the generator is a function of the
        world and the world moves. A writer who adds a region after accepting a map would
        otherwise find the mountains had shifted under the towns they had already placed.
        These are the fields they accepted, and they stay until a new map is accepted.
        """
        blob = pack_fields(size, fields)
        row = self.db.one(
            "SELECT id FROM terrain WHERE project_id = ? AND branch_id = ?",
            (self.project_id, self.branch_id))
        columns = {
            "seed": seed, "size": size, "span": span, "origin_x": origin_x,
            "origin_y": origin_y, "sea_level": sea_level, "fields": blob,
            "updated_at": now_iso(),
        }
        if row is None:
            self.db.execute(
                "INSERT INTO terrain (id, project_id, branch_id, seed, size, span, "
                "origin_x, origin_y, sea_level, fields, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), self.project_id, self.branch_id, seed, size, span,
                 origin_x, origin_y, sea_level, blob, columns["updated_at"]))
        else:
            self.db.execute(
                "UPDATE terrain SET seed = ?, size = ?, span = ?, origin_x = ?, "
                "origin_y = ?, sea_level = ?, fields = ?, updated_at = ? WHERE id = ?",
                (seed, size, span, origin_x, origin_y, sea_level, blob,
                 columns["updated_at"], row["id"]))

    def terrain(self) -> dict | None:
        """The stored surface, or None if no map has been accepted on this branch."""
        row = self.db.one(
            "SELECT * FROM terrain WHERE project_id = ? AND branch_id = ?",
            (self.project_id, self.branch_id))
        if row is None:
            return None
        return {
            "seed": row["seed"], "size": row["size"], "span": row["span"],
            "origin_x": row["origin_x"], "origin_y": row["origin_y"],
            "sea_level": row["sea_level"], "updated_at": row["updated_at"],
            "fields": unpack_fields(row["size"], row["fields"]),
        }

    # ---- remembered decisions (§66) ---------------------------------------

    def remember(self, namespace: str, key: str, value: Any) -> None:
        """Record a decision the writer made, so the next run honours it.

        This is where "I rejected that river" and "I renamed that town" live. It is not
        a fact about the world — the world does not contain a rejected river — and it
        cannot live in the client, because a decision the writer loses by opening a new
        browser is a decision they have to make again every time.

        Branch-scoped, because a what-if may keep what canon rejected. Logged like any
        other change, so it undoes with the action that made it.
        """
        row = self._state_row(namespace, key)
        with self.db.transaction():
            if row is None:
                sid = new_id()
                # Encoded here rather than left to the insert helper: that only
                # JSON-encodes dicts and lists, so a bare string would be stored raw
                # and read back as undecodable.
                self.db.insert("app_state", {
                    "id": sid, "project_id": self.project_id,
                    "branch_id": self.branch_id, "namespace": namespace, "key": key,
                    "value": encode_json(value), "updated_at": now_iso(),
                })
                self._log_revision("app_state", sid, "insert", None, {"value": value})
            else:
                before = decode_json(row["value"], None)
                self.db.execute(
                    "UPDATE app_state SET value = ?, updated_at = ? WHERE id = ?",
                    (encode_json(value), now_iso(), row["id"]))
                self._log_revision("app_state", row["id"], "update",
                                   {"value": before}, {"value": value})

    def _state_row(self, namespace: str, key: str):
        return self.db.one(
            "SELECT * FROM app_state WHERE project_id = ? AND branch_id = ? "
            "AND namespace = ? AND key = ?",
            (self.project_id, self.branch_id, namespace, key))

    def recall(self, namespace: str, key: str) -> Any | None:
        row = self._state_row(namespace, key)
        return decode_json(row["value"], None) if row else None

    def recall_all(self, namespace: str) -> dict[str, Any]:
        """Every decision in a namespace, in key order."""
        return {r["key"]: decode_json(r["value"], None) for r in self.db.query(
            "SELECT key, value FROM app_state WHERE project_id = ? AND branch_id = ? "
            "AND namespace = ? ORDER BY key",
            (self.project_id, self.branch_id, namespace))}

    def forget(self, namespace: str, key: str) -> None:
        row = self._state_row(namespace, key)
        if row is None:
            return
        with self.db.transaction():
            self.db.execute("DELETE FROM app_state WHERE id = ?", (row["id"],))
            self._log_revision("app_state", row["id"], "delete", _snapshot(row), None)

    def current_action_id(self) -> str:
        """The id of the action being written — what undo groups on."""
        return self._current_action_id()

    def add_route_segment(self, from_entity_id: str, to_entity_id: str, length: float, *,
                          medium: str = "road", quality: float = 1.0,
                          terrain: str = "plain", entity_id: str | None = None,
                          built_on: int | None = None, ruined_on: int | None = None,
                          closed_seasons: Sequence[str] = (), danger: str = "low",
                          toll_holder_id: str | None = None,
                          props: dict | None = None) -> RouteSegment:
        # A closure is tested against the season the day falls in, so a name that is not
        # one of this calendar's seasons closes the route on no day of any year — the road
        # reads as impassable in the writer's notes and is wide open in every travel
        # answer. The example world shipped with exactly that, closing a mountain pass in
        # "Darkening", which is a month. Refusing here is the only place that can tell the
        # difference, and it can say what the calendar does call its seasons.
        known = {s.name for s in self.calendar.seasons}
        unknown = [s for s in closed_seasons if s not in known]
        if unknown:
            names = ", ".join(sorted(known)) or "none — this calendar has no seasons"
            raise WorldError(
                f"{unknown[0]!r} is not a season of the {self.calendar.name} calendar "
                f"(its seasons are: {names})")
        sid = new_id()
        with self.db.transaction():
            self.db.insert("route_segment", {
                "id": sid, "project_id": self.project_id, "branch_id": self.branch_id,
                "entity_id": entity_id, "from_entity_id": from_entity_id,
                "to_entity_id": to_entity_id, "medium": medium, "length": length,
                "quality": quality, "terrain": terrain, "built_on": built_on,
                "ruined_on": ruined_on, "closed_seasons": list(closed_seasons),
                "danger": danger, "toll_holder_id": toll_holder_id,
                "props": props or {},
            })
            self._log_revision("route_segment", sid, "insert", None, {"medium": medium})
        return RouteSegment(id=sid, from_entity_id=from_entity_id,
                            to_entity_id=to_entity_id, length=length, medium=medium,
                            quality=quality, terrain=terrain, entity_id=entity_id,
                            built_on=built_on, ruined_on=ruined_on,
                            closed_seasons=tuple(closed_seasons), danger=danger,
                            toll_holder_id=toll_holder_id, props=props or {})

    def route_segments(self) -> list[RouteSegment]:
        # Ordered, because an unordered read makes a generated network impossible to
        # diff against the last one and a golden test impossible to write.
        return [_segment(r) for r in self.db.query(
            f"SELECT * FROM route_segment WHERE {self._in_chain} "
            "ORDER BY from_entity_id, to_entity_id, id", self._chain
        )]

    # ---- snapshots (§80) --------------------------------------------------

    def add_snapshot(self, name: str, day: int, *, note: str = "") -> str:
        sid = new_id()
        self.db.insert("snapshot", {
            "id": sid, "project_id": self.project_id, "branch_id": self.branch_id,
            "name": name, "day": day, "note": note,
        })
        return sid

    def snapshots(self) -> list[dict]:
        return [dict(r) for r in self.db.query(
            f"SELECT * FROM snapshot WHERE {self._in_chain} ORDER BY day", self._chain
        )]

    # ---- suppressions (§46) -----------------------------------------------

    def suppress(self, rule_key: str, fingerprint: str, reason: str = "") -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO continuity_suppression "
            "(id, project_id, rule_key, fingerprint, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_id(), self.project_id, rule_key, fingerprint, reason, now_iso()),
        )

    def suppressions(self) -> set[tuple[str, str]]:
        return {
            (r["rule_key"], r["fingerprint"])
            for r in self.db.query(
                "SELECT rule_key, fingerprint FROM continuity_suppression "
                "WHERE project_id = ?",
                (self.project_id,),
            )
        }

    # ---- maintenance ------------------------------------------------------

    def analyze(self) -> None:
        self.db.analyze()


# ---------------------------------------------------------------- row -> record

def _entity(row) -> Entity:
    return Entity(
        id=row["id"], type_key=row["type_key"], name=row["name"],
        summary=row["summary"], exists_from=row["exists_from"],
        exists_to=row["exists_to"], exists_from_hi=row["exists_from_hi"],
        exists_to_lo=row["exists_to_lo"], confidence=row["confidence"],
        tags=tuple(decode_json(row["tags"], [])), branch_id=row["branch_id"],
        project_id=row["project_id"],
    )


def _fact(row) -> Fact:
    return Fact(
        id=row["id"], subject_id=row["subject_id"], predicate_key=row["predicate_key"],
        object_id=row["object_id"], value=row["value"],
        about_fact_id=row["about_fact_id"], valid_from=row["valid_from"],
        valid_from_hi=row["valid_from_hi"], valid_to=row["valid_to"],
        valid_to_lo=row["valid_to_lo"], precision=row["precision"],
        confidence=row["confidence"], secrecy=row["secrecy"], strength=row["strength"],
        source_id=row["source_id"], note=row["note"],
        props=decode_json(row["props"], {}), branch_id=row["branch_id"],
    )


def _event(row) -> Event:
    return Event(
        id=row["id"], name=row["name"], type_key=row["type_key"], summary=row["summary"],
        start_day=row["start_day"], start_day_hi=row["start_day_hi"],
        end_day=row["end_day"], end_day_lo=row["end_day_lo"], precision=row["precision"],
        location_id=row["location_id"], confidence=row["confidence"],
        secrecy=row["secrecy"], entity_id=row["entity_id"],
        props=decode_json(row["props"], {}),
    )


def _title(row) -> Title:
    return Title(
        id=row["id"], name=row["name"], rank=row["rank"], entity_id=row["entity_id"],
        territory_id=row["territory_id"], succession_law=row["succession_law"],
        dynasty_root_id=row["dynasty_root_id"], created_on=row["created_on"],
        abolished_on=row["abolished_on"], props=decode_json(row["props"], {}),
    )


def _holding(row) -> TitleHolding:
    return TitleHolding(
        id=row["id"], title_id=row["title_id"], holder_id=row["holder_id"],
        from_day=row["from_day"], to_day=row["to_day"], how=row["how"],
        disputed=bool(row["disputed"]), note=row["note"],
    )


def _secret(row) -> Secret:
    return Secret(id=row["id"], name=row["name"], truth=row["truth"],
                  about_id=row["about_id"], fact_id=row["fact_id"],
                  severity=row["severity"])


def _knowledge(row) -> Knowledge:
    return Knowledge(
        id=row["id"], observer_id=row["observer_id"], secret_id=row["secret_id"],
        stance=row["stance"], about_observer_id=row["about_observer_id"],
        acquired_on=row["acquired_on"], acquired_from=row["acquired_from"],
        scene_id=row["scene_id"], note=row["note"],
    )


def _scene(row) -> Scene:
    return Scene(
        id=row["id"], title=row["title"], chapter_id=row["chapter_id"],
        position=row["position"], day=row["day"], end_day=row["end_day"],
        location_id=row["location_id"], pov_id=row["pov_id"],
        objective=row["objective"], conflict=row["conflict"], outcome=row["outcome"],
        notes=row["notes"],
    )


def _geometry(row) -> Geometry:
    return Geometry(
        id=row["id"], entity_id=row["entity_id"], kind=row["kind"],
        coordinates=decode_json(row["coordinates"], []),
        valid_from=row["valid_from"], valid_to=row["valid_to"], layer=row["layer"],
        style=decode_json(row["style"], {}), approximate=bool(row["approximate"]),
        props=decode_json(row["props"], {}),
    )


def _segment(row) -> RouteSegment:
    return RouteSegment(
        id=row["id"], from_entity_id=row["from_entity_id"],
        to_entity_id=row["to_entity_id"], length=row["length"], medium=row["medium"],
        quality=row["quality"], terrain=row["terrain"], entity_id=row["entity_id"],
        built_on=row["built_on"], ruined_on=row["ruined_on"],
        closed_seasons=tuple(decode_json(row["closed_seasons"], [])),
        danger=row["danger"], toll_holder_id=row["toll_holder_id"],
        props=decode_json(row["props"], {}),
    )


def _snapshot(row) -> dict:
    """A full-row copy for a delete revision, with JSON columns decoded.

    A delete is the one action where the log entry must be the complete row — a partial
    snapshot makes "even a deletion is recoverable" a false promise, restoring a fact
    without its secrecy, note, strength or uncertainty bounds.
    """
    data = dict(row)
    for key in ("tags", "props", "core_fields"):
        if key in data:
            data[key] = decode_json(data[key], [])
    return data


def _flatten_coords(coordinates: Any) -> tuple[list[float], list[float]]:
    """Pull every (x, y) out of a point / line / polygon coordinate structure."""
    xs: list[float] = []
    ys: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)):
            if (len(node) == 2 and all(isinstance(v, (int, float)) for v in node)):
                xs.append(float(node[0]))
                ys.append(float(node[1]))
            else:
                for child in node:
                    walk(child)

    walk(coordinates)
    return xs, ys


def _fts_query(text: str) -> str:
    """Turn user text into an FTS5 query, quoting terms so punctuation cannot break it."""
    terms = [t for t in text.replace('"', " ").split() if t]
    if not terms:
        return '""'
    return " ".join(f'"{t}"*' for t in terms)
