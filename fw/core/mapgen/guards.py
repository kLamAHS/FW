"""The determinism helpers, and the reasons they exist.

The promise is that the same world generates the same map on any machine, forever — a
golden coordinate test is worthless otherwise, and a writer who regenerates and gets a
different continent has been lied to about what "generate" means. That promise is not
kept by intending it. It is kept by never doing any of four specific things, and these
are the helpers that make not doing them easy.

**Never iterate a set of strings.** `hash(str)` is salted per process, so
`frozenset[tuple[str, str]]` yields its members in a different order under a different
PYTHONHASHSEED. Sets of integers happen to be stable, which makes this failure mode
worse rather than better: it passes every test on one machine.

**Never read facts in database order.** `facts_where` returns rows in whatever order
SQLite finds them, which changes as the file is edited.

**Never let a float's low bits into an identity.** Accumulating in a different order
changes the last bit, and a digest over unrounded floats then differs between two runs
that drew the same map.

**Never call a transcendental.** `math.sin`, `cos`, `exp` and `**0.5` are computed by
the platform's libm, which is not required to be correctly rounded and demonstrably
differs between builds. `sqrt`, `hypot` and `dist` *are* correctly rounded by IEEE 754
and are fine.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from typing import Any, TypeVar

T = TypeVar("T")

# How finely a coordinate is pinned before it reaches a digest or a comparison. A
# thousandth of a world unit is far below anything drawable and far above the noise
# that different summation orders introduce.
PRECISION = 1e-6


def quantise(value: float, step: float = PRECISION) -> float:
    """A float pinned to a grid, so two runs that agree can be seen to agree."""
    return round(value / step) * step


def canonical_json(value: Any) -> str:
    """JSON that is byte-identical for equal values.

    Sorted keys, no incidental whitespace, and every float quantised — the three ways
    two identical structures otherwise serialise differently.
    """
    return json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _canonical(value: Any) -> Any:
    if isinstance(value, float):
        pinned = quantise(value)
        # -0.0 and 0.0 are equal and serialise differently.
        return 0.0 if pinned == 0.0 else pinned
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda p: str(p[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        # A set has no order; give it one rather than letting the hash seed pick.
        return sorted((_canonical(v) for v in value), key=canonical_json)
    return value


def stable(items: Iterable[T], key: Callable[[T], Any] | None = None) -> list[T]:
    """A sequence in an order that does not depend on how it was built.

    Use this the moment anything leaves a set or a dict whose insertion order came from
    a query, a graph walk or another set.
    """
    if key is None:
        return sorted(items, key=canonical_json)
    return sorted(items, key=lambda item: canonical_json(key(item)))


def sorted_facts(facts: Sequence[Any]) -> list[Any]:
    """Facts in a fixed order, whatever order the database returned them in.

    Ordered by what the fact *says* rather than by its id: two worlds built by the same
    script have different ULIDs, and a generator that read them in id order would draw
    them two different maps.
    """
    return sorted(facts, key=lambda f: (
        getattr(f, "predicate_key", "") or "",
        (getattr(f, "object_id", None) or getattr(f, "value", None) or ""),
        _or(getattr(f, "valid_from", None), -1 << 62),
        _or(getattr(f, "valid_to", None), 1 << 62),
        getattr(f, "note", "") or "",
    ))


def _or(value: int | None, fallback: int) -> int:
    return fallback if value is None else value


def rcp_exp(x: float) -> float:
    """A rational stand-in for exp(-x), for x >= 0, accurate enough to shape a field.

    `math.exp` is a libm call and libm is not required to be correctly rounded; two
    machines can disagree in the last bit, which is enough to move a coastline by a
    cell and break a golden file. This is a Padé-style rational approximation built
    from arithmetic only, so every machine computes the same bits.

    It is monotone decreasing, exactly 1 at 0, and stays within 6e-8 of exp(-x) across
    [0, 8] — far tighter than any field here can express.
    """
    if x <= 0.0:
        return 1.0
    if x > 40.0:
        return 0.0
    # exp(-x) = 2^-k * exp(-r), splitting off whole halvings keeps r small.
    halvings = 0
    while x > 0.5:
        x *= 0.5
        halvings += 1
    # Padé [3/3] of exp(-r) about 0, exact to ~1e-9 for |r| <= 0.5.
    numerator = 120.0 + x * (-60.0 + x * (12.0 - x))
    denominator = 120.0 + x * (60.0 + x * (12.0 + x))
    value = numerator / denominator
    for _ in range(halvings):
        value *= value
    return value
