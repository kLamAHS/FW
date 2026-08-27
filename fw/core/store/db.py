"""SQLite access for a world file.

Two things here are load-bearing rather than boilerplate.

**ANALYZE is not optional.** Recursive CTEs are how this application walks the world graph:
vassal chains, descent, kinship, supply routes. Benchmarking at the brief's own §99 scale
(50,000 entities / 200,000 relationships) found the kinship traversal taking 264 ms — and
the cause was not the query shape. Without table statistics SQLite's planner declines the
indexes on the recursive step and degrades to a scan. After ANALYZE the same query runs in
0.26 ms, a thousandfold difference. So the store runs ANALYZE after any bulk load and
`PRAGMA optimize` on close. Skipping this would look like "SQLite cannot do graphs" and
invite an expensive, wrong rewrite to a graph database -- exactly what §64 warns against.

**The connection is configured, not defaulted.** WAL so a long read cannot block a write,
foreign keys on (they are off by default in SQLite, which silently permits orphans in a
database whose whole value is referential integrity), and a busy timeout so a concurrent
request waits rather than raising.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fw.core.store.schema import APPLICATION_ID, SCHEMA, SCHEMA_VERSION


class StoreError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    """A connection to one `.fwworld` file (or `:memory:` for tests)."""

    def __init__(self, path: str | Path = ":memory:", *, create: bool = True) -> None:
        self.path = str(path)
        is_new = self.path == ":memory:" or not Path(self.path).exists()
        if is_new and not create:
            raise StoreError(f"no world file at {self.path}")

        # The web adapter runs sync endpoints in a threadpool, so requests do not all
        # arrive on the thread that opened the file. SQLite itself is built in serialized
        # mode here (threadsafety 3), so sharing the connection is safe at the C level —
        # `check_same_thread` is a Python-side guard, not a SQLite constraint.
        #
        # What is *not* safe is our own transaction bracketing: two threads interleaving
        # BEGIN and COMMIT would commit each other's work. The re-entrant lock below makes
        # a transaction atomic with respect to other threads while still allowing nested
        # calls on the same thread.
        if sqlite3.threadsafety < 1:  # pragma: no cover - depends on the build
            raise StoreError(
                "this Python's sqlite3 was built without thread safety; "
                "the application cannot serve requests safely"
            )
        self._lock = threading.RLock()
        # Counts outermost transactions. The revision log uses it to group every record
        # written in one transaction into one user action — the unit undo operates on.
        self.transaction_serial = 0
        self.conn = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False
        )
        self.conn.row_factory = sqlite3.Row
        self._configure()

        if is_new:
            self._create_schema()
        else:
            self._verify()

    # ---- setup ------------------------------------------------------------

    def _configure(self) -> None:
        cur = self.conn.cursor()
        # WAL is meaningless for :memory: and SQLite will refuse it; that is harmless.
        if self.path != ":memory:":
            cur.execute("PRAGMA journal_mode = WAL")
        cur.execute("PRAGMA synchronous = NORMAL")
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("PRAGMA busy_timeout = 5000")
        cur.execute("PRAGMA temp_store = MEMORY")
        cur.execute("PRAGMA cache_size = -65536")       # 64 MB
        cur.execute("PRAGMA analysis_limit = 400")      # keep ANALYZE cheap
        cur.execute("PRAGMA mmap_size = 268435456")

    def _create_schema(self) -> None:
        # executescript issues its own COMMIT, so it must not sit inside our transaction
        # helper -- the helper's COMMIT would then find no transaction to close.
        self.conn.executescript(SCHEMA)
        self.conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _verify(self) -> None:
        app_id = self.conn.execute("PRAGMA application_id").fetchone()[0]
        if app_id != APPLICATION_ID:
            raise StoreError(
                f"{self.path} is a SQLite database but not a world file "
                f"(application_id {app_id:#x}, expected {APPLICATION_ID:#x})"
            )
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise StoreError(
                f"{self.path} was written by a newer version of the application "
                f"(schema {version}, this build understands {SCHEMA_VERSION})"
            )
        if version < SCHEMA_VERSION:
            self._migrate(version)

    def _migrate(self, from_version: int) -> None:
        from fw.core.store.schema import MIGRATIONS

        # Each step commits atomically WITH its user_version bump: a crash between the
        # two would otherwise leave a file that re-runs the migration on every open and
        # dies on it ("duplicate column"). user_version lives in the database header
        # and rolls back with the transaction, so wrapping both is enough. Migration
        # scripts must therefore not manage their own transactions.
        for version in range(from_version + 1, SCHEMA_VERSION + 1):
            statement = MIGRATIONS.get(version)
            if statement:
                self.conn.executescript(
                    f"BEGIN;\n{statement}\n;\nPRAGMA user_version = {version};\nCOMMIT;"
                )
            else:
                self.conn.execute(f"PRAGMA user_version = {version}")

    # ---- transactions -----------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Explicit transaction. Nesting reuses the outer one rather than failing."""
        with self._lock:
            if self.conn.in_transaction:
                yield self.conn
                return
            self.transaction_serial += 1
            self.conn.execute("BEGIN")
            try:
                yield self.conn
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    # ---- queries ----------------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def insert(self, table: str, values: dict[str, Any]) -> None:
        columns = ", ".join(values)
        holders = ", ".join("?" for _ in values)
        with self._lock:
            self.conn.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({holders})",
                [_encode(v) for v in values.values()],
            )

    def insert_many(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        columns = list(rows[0])
        holders = ", ".join("?" for _ in columns)
        with self._lock:
            self.conn.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({holders})",
                [[_encode(row[c]) for c in columns] for row in rows],
            )

    def update(self, table: str, row_id: str, values: dict[str, Any]) -> None:
        assignments = ", ".join(f"{c} = ?" for c in values)
        with self._lock:
            self.conn.execute(
                f"UPDATE {table} SET {assignments} WHERE id = ?",
                [*(_encode(v) for v in values.values()), row_id],
            )

    # ---- maintenance ------------------------------------------------------

    def analyze(self) -> None:
        """Refresh planner statistics. See the module docstring -- this is not optional."""
        self.conn.execute("ANALYZE")

    def optimize(self) -> None:
        self.conn.execute("PRAGMA optimize")

    def vacuum(self) -> None:
        self.conn.execute("VACUUM")

    def close(self) -> None:
        # Best-effort: a failure to refresh statistics must never stop a file from closing.
        with suppress(sqlite3.Error):
            self.optimize()
        self.conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _encode(value: Any) -> Any:
    """STRICT tables reject Python types SQLite has no column type for."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, separators=(",", ":"))
    return value


_UNSET = object()


def decode_json(value: str | None, default: Any = _UNSET) -> Any:
    """Parse a JSON column, or return `default` (an explicit None is honoured).

    The old fallback `default if default is not None else {}` made None impossible to
    return, so a revision with no prior state decoded as {} while the API contract said
    null — every consumer distinguishing "no prior state" by null would misclassify.
    """
    fallback = {} if default is _UNSET else default
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback
