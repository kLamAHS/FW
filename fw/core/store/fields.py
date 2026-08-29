"""Packing whole-lattice fields into a blob, and getting them back.

The map generator computes the world on a square lattice — height, tree cover, standing
water — and the accepted map's lattice has to be kept, because the relief a reader
recognises as a physical map is made by lighting that surface and the outlines derived
from it cannot be lit.

Kept as one compressed blob rather than as a table of cells. A hundred and forty-four
squared is twenty thousand numbers per field, and a row each would be a third of a
million rows for a picture — a shape that says "queryable" about something nothing will
ever query. What is wanted is the whole field at once, every time, and that is a blob.

Sixteen bits a cell, scaled to each field's own range. That is a part in sixty-five
thousand of the difference between the deepest ocean and the highest peak, which is far
finer than a lattice cell several miles across can mean, and it halves the payload
against float32 for nothing anybody can see.
"""

from __future__ import annotations

import struct
import zlib

MAGIC = b"FWTF"
VERSION = 1
_STEPS = 65535
_LEVEL = 6


def pack_fields(size: int, fields: dict[str, list[list[float]]]) -> bytes:
    """Several same-sized lattices into one blob, each scaled to its own range."""
    names = sorted(fields)
    header = bytearray()
    header += MAGIC
    header += struct.pack(">BHH", VERSION, size, len(names))
    body = bytearray()
    for name in names:
        grid = fields[name]
        if len(grid) != size or any(len(row) != size for row in grid):
            raise ValueError(f"field {name!r} is not {size} by {size}")
        flat = [value for row in grid for value in row]
        low = min(flat)
        high = max(flat)
        span = high - low
        label = name.encode("utf-8")
        header += struct.pack(">B", len(label)) + label
        header += struct.pack(">dd", low, high)
        if span <= 0.0:
            body += b"\x00\x00" * (size * size)
            continue
        scale = _STEPS / span
        body += b"".join(
            struct.pack(">H", int((value - low) * scale + 0.5)) for value in flat)
    return bytes(header) + zlib.compress(bytes(body), _LEVEL)


def unpack_fields(size: int, blob: bytes) -> dict[str, list[list[float]]]:
    """The inverse. Raises rather than guessing if the blob is not one of ours."""
    if blob[:4] != MAGIC:
        raise ValueError("not a field blob")
    version, stored_size, count = struct.unpack(">BHH", blob[4:9])
    if version != VERSION:
        raise ValueError(f"field blob version {version} is not readable here")
    if stored_size != size:
        raise ValueError(f"field blob is {stored_size} wide, expected {size}")

    cursor = 9
    ranges: list[tuple[str, float, float]] = []
    for _ in range(count):
        length = blob[cursor]
        cursor += 1
        name = blob[cursor:cursor + length].decode("utf-8")
        cursor += length
        low, high = struct.unpack(">dd", blob[cursor:cursor + 16])
        cursor += 16
        ranges.append((name, low, high))

    body = zlib.decompress(blob[cursor:])
    cells = size * size
    out: dict[str, list[list[float]]] = {}
    at = 0
    for name, low, high in ranges:
        span = high - low
        scale = span / _STEPS if span > 0.0 else 0.0
        raw = struct.unpack(f">{cells}H", body[at:at + cells * 2])
        at += cells * 2
        values = [low + step * scale for step in raw]
        out[name] = [values[j * size:(j + 1) * size] for j in range(size)]
    return out
