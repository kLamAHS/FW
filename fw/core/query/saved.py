"""Questions the writer wants to keep asking.

In `app_state`, which already exists, is branch-scoped and is revision-logged — so a
saved query undoes like everything else, a what-if can keep its own, and no migration
was needed to add the feature. The namespace is the only new thing here.
"""

from __future__ import annotations

import re

from fw.core.query.language import Query, QueryError, Saved

NAMESPACE = "queries"


def key_for(name: str) -> str:
    """A stable handle made from the name, so saving twice replaces rather than doubles."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if not slug:
        raise QueryError("a saved question needs a name")
    return slug


def save(world, name: str, query: Query, *, note: str = "") -> Saved:
    row = Saved(key=key_for(name), name=name.strip(), query=query.check(), note=note)
    world.remember(NAMESPACE, row.key, row.as_dict())
    return row


def saved(world) -> tuple[Saved, ...]:
    """Every kept question, in the order they were named."""
    out = []
    for key in world.recall_all(NAMESPACE):
        raw = world.recall(NAMESPACE, key)
        if isinstance(raw, dict) and raw.get("name"):
            out.append(Saved.from_dict(raw))
    return tuple(sorted(out, key=lambda row: (row.name.lower(), row.key)))


def forget(world, key: str) -> None:
    world.forget(NAMESPACE, key)
