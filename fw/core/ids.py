"""Stable identifiers (spec §101).

ULIDs rather than UUID4: they sort by creation time, so inserting a batch of entities
produces sequential primary keys instead of scattering B-tree writes, and a listing in
id order is a listing in creation order without a separate column.
"""

from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # excludes I, L, O, U
_DECODE = {c: i for i, c in enumerate(_CROCKFORD)}

_last_ms = 0
_last_rand = 0


def new_id() -> str:
    """A 26-character ULID: 48 bits of millisecond timestamp, 80 bits of randomness.

    Ids generated within the same millisecond increment rather than re-randomise, so
    ordering is strict even in a tight seeding loop.
    """
    global _last_ms, _last_rand
    ms = int(time.time() * 1000)
    if ms == _last_ms:
        _last_rand += 1
    else:
        _last_ms = ms
        _last_rand = int.from_bytes(os.urandom(10), "big")
    return _encode(ms, 10) + _encode(_last_rand, 16)


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def timestamp_ms(ulid: str) -> int:
    """Recover the creation time embedded in a ULID."""
    value = 0
    for ch in ulid[:10]:
        value = (value << 5) | _DECODE[ch.upper()]
    return value


def is_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 26
        and all(c in _DECODE for c in value.upper())
    )
