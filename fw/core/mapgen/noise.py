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


def field(seed: str, size: int, *, wavelength: float, octaves: int = 4,
          stride: int = 1) -> list[list[float]]:
    """A whole lattice of fractal noise, sampled every `stride` cells and interpolated.

    Most of the fields the generator builds vary over tens of cells — where the coast
    bulges, how rough a stretch of shore is, how hard the rock is — and evaluating them
    per cell asks a dozen hashes to describe something that has barely changed since the
    last answer. Sampling them at the scale they actually vary over and interpolating
    between costs a fraction and produces the same field, because a field with no content
    below the sampling scale loses nothing by not being asked about it.

    The stride is the caller's to choose and is a claim about the field: pass one that is
    a large fraction of the wavelength and the interpolation will show, as flats and
    creases. A quarter of the wavelength is comfortable.
    """
    if stride < 1:
        stride = 1
    span = (size + stride - 1) // stride + 2
    step = stride / wavelength
    coarse = [[fbm(seed, cx * step, cy * step, octaves=octaves)
               for cx in range(span)] for cy in range(span)]
    if stride == 1:
        return [row[:size] for row in coarse[:size]]

    out: list[list[float]] = []
    limit = span - 1.001
    for j in range(size):
        y = j / stride
        if y > limit:
            y = limit
        cj = int(y)
        fy = y - cj
        near, far = coarse[cj], coarse[cj + 1]
        row = [0.0] * size
        for i in range(size):
            x = i / stride
            if x > limit:
                x = limit
            ci = int(x)
            fx = x - ci
            top = near[ci] + (near[ci + 1] - near[ci]) * fx
            low = far[ci] + (far[ci + 1] - far[ci]) * fx
            row[i] = top + (low - top) * fy
        out.append(row)
    return out


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


# ---------------------------------------------------------------------------
# A fixed ring of unit vectors, so nothing in the generator ever calls sin or cos.
#
# `math.sin` and `math.cos` are libm calls, and libm is not required to be correctly
# rounded: two machines can disagree in the last bit. That is enough to move a
# coastline by a cell, which is enough to break a golden file and to break the promise
# that a world generates the same map everywhere. These values were computed once, at
# the time this was written, and are now literals like any other constant.
DIRECTIONS: tuple[tuple[float, float], ...] = (
    (+1, +0),
    (+0.98078528040323043, +0.19509032201612825),
    (+0.92387953251128674, +0.38268343236508978),
    (+0.83146961230254524, +0.55557023301960218),
    (+0.70710678118654757, +0.70710678118654746),
    (+0.55557023301960229, +0.83146961230254524),
    (+0.38268343236508984, +0.92387953251128674),
    (+0.19509032201612833, +0.98078528040323043),
    (+6.123233995736766e-17, +1),
    (-0.19509032201612819, +0.98078528040323043),
    (-0.38268343236508973, +0.92387953251128674),
    (-0.55557023301960196, +0.83146961230254546),
    (-0.70710678118654746, +0.70710678118654757),
    (-0.83146961230254535, +0.55557023301960218),
    (-0.92387953251128674, +0.38268343236508989),
    (-0.98078528040323043, +0.19509032201612861),
    (-1, +1.2246467991473532e-16),
    (-0.98078528040323043, -0.19509032201612836),
    (-0.92387953251128685, -0.38268343236508967),
    (-0.83146961230254546, -0.55557023301960196),
    (-0.70710678118654768, -0.70710678118654746),
    (-0.55557023301960218, -0.83146961230254524),
    (-0.38268343236509034, -0.92387953251128652),
    (-0.19509032201612866, -0.98078528040323032),
    (-1.8369701987210297e-16, -1),
    (+0.1950903220161283, -0.98078528040323043),
    (+0.38268343236509, -0.92387953251128663),
    (+0.55557023301960184, -0.83146961230254546),
    (+0.70710678118654735, -0.70710678118654768),
    (+0.83146961230254524, -0.55557023301960218),
    (+0.92387953251128652, -0.38268343236509039),
    (+0.98078528040323032, -0.19509032201612872),
)


def direction(index: int) -> tuple[float, float]:
    """The unit vector at `index`/32 of a turn."""
    return DIRECTIONS[index % len(DIRECTIONS)]


def bearing(seed: str, *coords: int) -> tuple[float, float]:
    """A stable unit vector for a seed and a lattice point."""
    return direction(int(unit(seed, *coords) * len(DIRECTIONS)))


def around(step: int, of: int) -> tuple[float, float]:
    """The unit vector `step`/`of` of the way round a circle.

    Interpolated between two table entries and renormalised, so any number of evenly
    spread directions is available without a libm call. `sqrt` is exempt from that ban:
    IEEE 754 requires it to be correctly rounded, so every machine agrees.
    """
    if of <= 0:
        return DIRECTIONS[0]
    position = (step % of) * len(DIRECTIONS) / of
    low = int(position) % len(DIRECTIONS)
    high = (low + 1) % len(DIRECTIONS)
    t = position - int(position)
    ax, ay = DIRECTIONS[low]
    bx, by = DIRECTIONS[high]
    x, y = ax + (bx - ax) * t, ay + (by - ay) * t
    length = math.sqrt(x * x + y * y) or 1.0
    return (x / length, y / length)
