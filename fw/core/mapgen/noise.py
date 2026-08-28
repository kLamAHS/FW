"""Deterministic value noise, in pure Python.

The generator must produce the same map on every machine, every run, forever — a golden
test asserting coordinates is worthless otherwise, and a writer who regenerates and gets a
different continent has been lied to about what "generate" means.

Python's built-in `hash()` is salted per process (PYTHONHASHSEED), and `random` is only
reproducible if nothing else ever touches the same instance. Both are traps. So the noise
here is a *pure function* of its integer inputs, hashed with BLAKE2b — same bytes on every
platform, every Python build, in any order of evaluation. Nothing here holds state.
"""

from __future__ import annotations

import hashlib
import math
import struct
from functools import lru_cache

# How many bytes of digest a single sample consumes. Four gives ~4.3 billion steps
# between 0 and 1, far finer than any coordinate lattice needs.
_SAMPLE_BYTES = 4
_SCALE = float(1 << (8 * _SAMPLE_BYTES))


# Adjacent samples share lattice corners — four neighbouring points ask about the same
# four hashes — so a cache cuts the work by roughly four with no effect on the answer.
# The function is pure, so caching cannot change what it returns.
@lru_cache(maxsize=1 << 18)
def unit(seed: str, *coords: int) -> float:
    """A stable number in [0, 1) for a seed and a lattice point."""
    payload = seed.encode("utf-8") + b"|" + struct.pack(f">{len(coords)}q", *coords)
    digest = hashlib.blake2b(payload, digest_size=_SAMPLE_BYTES).digest()
    return int.from_bytes(digest, "big") / _SCALE


def signed(seed: str, *coords: int) -> float:
    """The same, in [-1, 1)."""
    return unit(seed, *coords) * 2.0 - 1.0


def _smooth(t: float) -> float:
    """Quintic ease, so interpolated noise has no visible lattice creases."""
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def value2d(seed: str, x: float, y: float) -> float:
    """Smoothly interpolated value noise at a continuous point, in [0, 1).

    Bilinear interpolation between four hashed lattice corners, eased so the derivative
    is continuous — the difference between rolling hills and a quilt of flat squares.
    """
    x0, y0 = math.floor(x), math.floor(y)
    fx, fy = _smooth(x - x0), _smooth(y - y0)

    c00 = unit(seed, x0, y0)
    c10 = unit(seed, x0 + 1, y0)
    c01 = unit(seed, x0, y0 + 1)
    c11 = unit(seed, x0 + 1, y0 + 1)

    top = c00 + (c10 - c00) * fx
    bottom = c01 + (c11 - c01) * fx
    return top + (bottom - top) * fy


def fbm(seed: str, x: float, y: float, *, octaves: int = 4,
        lacunarity: float = 2.0, gain: float = 0.5) -> float:
    """Fractal noise: a few octaves of value noise summed, in [0, 1).

    One octave is too smooth to read as terrain; four gives a coastline with bays and a
    range with foothills, at four hashes per sample rather than the hundreds a proper
    simulation would want.
    """
    total = 0.0
    amplitude = 1.0
    frequency = 1.0
    norm = 0.0
    for octave in range(octaves):
        # The octave index enters the seed rather than the coordinates, so octaves are
        # independent fields instead of the same field read at different zooms.
        total += amplitude * value2d(f"{seed}#{octave}", x * frequency, y * frequency)
        norm += amplitude
        amplitude *= gain
        frequency *= lacunarity
    return total / norm if norm else 0.0


def jitter(seed: str, key: str, spread: float) -> float:
    """A stable offset in [-spread, +spread] for a named thing.

    Used to nudge a settlement off a perfectly regular lattice: real towns are not on a
    grid, but they must land in the same not-a-grid place every time.
    """
    payload = f"{seed}|{key}".encode()
    digest = hashlib.blake2b(payload, digest_size=_SAMPLE_BYTES).digest()
    return (int.from_bytes(digest, "big") / _SCALE * 2.0 - 1.0) * spread


def shuffled(seed: str, items: list) -> list:
    """A stable permutation — a deterministic stand-in for random.shuffle."""
    order = sorted(range(len(items)), key=lambda i: (unit(seed, i), i))
    return [items[i] for i in order]
