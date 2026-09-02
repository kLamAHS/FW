"""The world library: the writer's saves.

A world is one `.fwworld` file (§63), so the library is nothing more than a directory of
them — copying a file in is importing it, copying it out is a backup. This module is the
headless logic behind the launcher screen: list what is there, create a new world, and
resolve a chosen file safely.

Every file name that comes back from a client is treated as hostile until proven to be a
plain name inside the library directory — the server must never open a path a browser
composed.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from fw.core.calendar.kernel import GREGORIAN
from fw.core.store.db import StoreError
from fw.core.world import World, WorldError

SUFFIX = ".fwworld"


class LibraryError(RuntimeError):
    pass


@dataclass
class WorldEntry:
    """One save, as the launcher lists it."""

    file: str                    # bare file name inside the library, never a path
    name: str
    modified: float
    size: int
    entities: int = 0
    problem: str = ""            # non-empty when the file could not be read


class Library:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def ensure(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    # ---- listing -----------------------------------------------------------

    def worlds(self) -> list[WorldEntry]:
        """Every world in the library, newest first.

        A file that fails to open is still listed, with the problem named — silently
        hiding a corrupt save would read as the writer's world vanishing.
        """
        out: list[WorldEntry] = []
        if not self.directory.is_dir():
            return out
        for path in sorted(self.directory.glob(f"*{SUFFIX}"),
                           key=_safe_mtime, reverse=True):
            modified = size = 0
            try:
                stat = path.stat()      # a dangling symlink fails here, not in glob
                modified, size = stat.st_mtime, stat.st_size
                # Listing is a read: opening with a vocabulary top-up would rewrite
                # every save in the library just to draw the launcher.
                world = World.open(path, sync=False)
                try:
                    out.append(WorldEntry(
                        file=path.name, name=world.name,
                        modified=modified, size=size,
                        entities=world.count_entities(),
                    ))
                finally:
                    world.close()
            except (StoreError, WorldError, sqlite3.DatabaseError, OSError) as exc:
                out.append(WorldEntry(
                    file=path.name, name=path.stem, modified=modified,
                    size=size, problem=str(exc),
                ))
        return out

    # ---- creating ----------------------------------------------------------

    def create(self, name: str, *, example: bool = False) -> Path:
        """Create a new world file named after the world, and return its path.

        `example` seeds the §115 Kingdom of Renn instead of an empty world — the tour
        of what the application can do, opted into rather than imposed.
        """
        name = name.strip()
        if not name:
            raise LibraryError("give the world a name")
        try:
            self.ensure()
            path = self._fresh_path(name)
            if example:
                from fw.core.seed.renn import seed_renn

                # With its map: this is the "show me what this does" button, and an
                # example world with no ground under it demonstrates the opposite.
                world = seed_renn(str(path), with_map=True)
                world.close()
            else:
                # An empty world starts on an Earth-like calendar; §60 makes calendars
                # data, so a custom one is an edit away rather than a blocking question.
                world = World.create(path, name=name, calendar=GREGORIAN)
                world.close()
        except OSError as exc:
            # A filesystem refusal (read-only disk, exotic characters the slug kept,
            # a full drive) is an answer for the writer, not a stack trace.
            raise LibraryError(f"could not create that world here: {exc}") from exc
        return path

    def _fresh_path(self, name: str) -> Path:
        stem = _slug(name)
        path = self.directory / f"{stem}{SUFFIX}"
        counter = 2
        while path.exists():
            path = self.directory / f"{stem}-{counter}{SUFFIX}"
            counter += 1
        return path

    # ---- resolving ---------------------------------------------------------

    def path_of(self, file: str) -> Path:
        """Turn a client-supplied file name into a path inside the library — or refuse.

        Anything that is not a bare `*.fwworld` name is rejected outright: no
        separators, no parent references, no absolute paths, whatever the request says.
        """
        if (not file or file != Path(file).name or file in (".", "..")
                or not file.endswith(SUFFIX)):
            raise LibraryError(f"{file!r} is not a world in this library")
        path = self.directory / file
        if not path.is_file():
            raise LibraryError(f"there is no world named {file!r} in the library")
        return path


def _slug(name: str) -> str:
    """A file-system-safe stem from a world name; falls back rather than failing.

    Capped well under NAME_MAX (255 bytes on the common filesystems) so an epic title
    becomes a long-ish file name rather than an OSError — the world keeps its full
    name inside the file either way.
    """
    stem = re.sub(r"[^\w\- ]", "", name, flags=re.UNICODE).strip()
    stem = re.sub(r"\s+", "-", stem).lower()
    return stem[:80].rstrip("-") or "world"


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
