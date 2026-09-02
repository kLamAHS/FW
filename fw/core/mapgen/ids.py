"""One identity scheme, shared by the plan, the ledger and the namer.

A generated feature has to be recognisable across runs, or none of propose-then-accept
works: rejecting a river and regenerating would offer it again, a renamed town would
revert, and applying twice would draw everything twice.

So a feature's identity comes from *what it is* — the region it belongs to, the cell it
sits on, the name the writer gave the thing it hangs off — and never from an entity id.
Entity ids are ULIDs, random per world: two worlds built by the same script have
different ones, so an identity derived from them would make the same map unrecognisable
between two copies of the same world, and would make a plan's digest depend on which
file it happened to be computed in.

The feature id and the name key are derived from the *same* key parts, which is what
lets a rename survive both mechanisms at once.
"""

from __future__ import annotations

import hashlib

# Bumped when a change would move features that ought to stay put. Everything derived
# here changes with it, so a new algorithm never inherits the old one's ledger.
ALGORITHM = "mapgen/2"

_UNIT = "\x1f"          # the ASCII unit separator: cannot occur in a name

_PREFIX = {
    "coast": "cst", "island": "isl", "region": "rgn", "range": "rng",
    "hills": "hil", "water": "wat", "sea": "sea", "river": "riv",
    "lake": "lak", "natural": "nat", "settlement": "stl", "castle": "cas",
    "ruin": "run", "road": "rd", "lane": "lan",
}

KINDS = tuple(sorted(_PREFIX))


def feature_id(kind: str, *key_parts: str | int) -> str:
    """A stable id for one generated feature.

    Short enough to read in a diff, long enough that a collision is not a thing that
    happens: six bytes over a few thousand features.
    """
    if kind not in _PREFIX:
        raise ValueError(f"unknown feature kind {kind!r}")
    payload = _UNIT.join(str(part) for part in (ALGORITHM, kind, *key_parts))
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=6).hexdigest()
    return f"{_PREFIX[kind]}_{digest}"


def name_key(kind: str, anchors: tuple[str, ...], ordinal: int = 0) -> str:
    """The key a name is remembered under.

    Readable rather than hashed, because it is stored as a fact on the writer's own
    entity and they may well look at it: `river|The Vale of Renn|00`.
    """
    return f"{kind}|" + "|".join(sorted(anchors)) + f"|{ordinal:02d}"


def content_key(*parts: str | int) -> str:
    """A short stable discriminator, for telling two same-named things apart.

    Ordering same-named entities by their content rather than their id is the whole
    point: `blake2b` over what they say is the same in every copy of the world.
    """
    payload = _UNIT.join(str(part) for part in parts)
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=4).hexdigest()


def shape_signature(coordinates) -> str:
    """A digest of one drawn shape, for telling "unchanged" from "moved".

    Rounded to a tenth of a world unit first: the client draws to one decimal place, so
    two shapes that differ below that are the same shape as far as anyone can see, and
    treating them as different would rewrite the whole map on every run.
    """
    flat: list[str] = []

    def walk(node) -> None:
        if isinstance(node, (int, float)):
            flat.append(f"{float(node):.1f}")
            return
        for child in node:
            walk(child)

    walk(coordinates)
    payload = ",".join(flat)
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=8).hexdigest()


_KIND_OF = {prefix: kind for kind, prefix in _PREFIX.items()}


def kind_of(feature_id: str) -> str | None:
    """Which kind of feature an id belongs to, without needing the plan it came from.

    Used to scope a retirement: a brief asking only for settlements must not sweep away
    the rivers it never looked at.
    """
    prefix, _, _ = feature_id.partition("_")
    return _KIND_OF.get(prefix)
