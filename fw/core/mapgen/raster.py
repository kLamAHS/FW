"""A PNG encoder, because the map has to be looked at.

The relief renderer produces an image — a hillshaded, tinted, textured picture of the
ground — and an image has to leave the process somehow. Everything else the generator
emits is geometry, which the browser draws; relief is not geometry. It is a couple of
million shaded samples, and sending those as vectors would be absurd.

So: PNG, in about a hundred lines of stdlib. `zlib` does the compression and `struct`
does the chunk headers; there is nothing else to it, and it is worth noting how little
that is against taking on Pillow — which would be a compiled dependency, in an
application whose whole promise is that a writer can open their world file in ten years
on a machine that does not exist yet.

Encoding is not part of the world model and nothing here needs to be reproducible bit
for bit across platforms, but it is anyway: zlib at a fixed level on identical bytes
gives identical output, so two runs of the generator produce the same file.
"""

from __future__ import annotations

import struct
import zlib

_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Compression level. Six is zlib's default and the knee of the curve: nine costs several
# times as long on a two-megapixel relief image to save a couple of per cent.
_LEVEL = 6


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def encode(width: int, height: int, pixels: bytearray | bytes) -> bytes:
    """Eight-bit RGB, row-major, three bytes a pixel, into a PNG file.

    Each scanline gets a filter byte. Filtering is not an optimisation to skip: a relief
    image is mostly smooth gradient, and predicting each byte from the one above turns
    long ramps into runs of near-zero that deflate to almost nothing. Choosing the filter
    per row by the standard sum-of-absolute-differences heuristic is a few lines and
    roughly halves the file against no filtering at all.
    """
    if len(pixels) != width * height * 3:
        raise ValueError(f"expected {width * height * 3} bytes, got {len(pixels)}")

    stride = width * 3
    raw = bytearray()
    previous = bytes(stride)
    for y in range(height):
        row = bytes(pixels[y * stride:(y + 1) * stride])
        up = _filter_up(row, previous)
        sub = _filter_sub(row)
        # The heuristic: whichever filter leaves the smallest total magnitude compresses
        # best, near enough, and costs one pass to decide.
        candidates = ((_weight(up), 2, up), (_weight(sub), 1, sub), (_weight(row), 0, row))
        _, kind, chosen = min(candidates, key=lambda c: (c[0], c[1]))
        raw.append(kind)
        raw += chosen
        previous = row

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (_SIGNATURE
            + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), _LEVEL))
            + _chunk(b"IEND", b""))


def _filter_up(row: bytes, previous: bytes) -> bytes:
    return bytes((row[n] - previous[n]) & 0xFF for n in range(len(row)))


def _filter_sub(row: bytes) -> bytes:
    out = bytearray(row[:3])
    for n in range(3, len(row)):
        out.append((row[n] - row[n - 3]) & 0xFF)
    return bytes(out)


def _weight(row: bytes) -> int:
    """Sum of the bytes read as signed — the standard filter-choice heuristic."""
    return sum(b if b < 128 else 256 - b for b in row)
