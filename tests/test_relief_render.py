"""The picture, and the file it goes in.

Almost every real defect this generator has shipped was found by looking at a rendered
map rather than by reading a test — roads across a mountain range, harbours on an inland
vale, a continent in three pieces. That is an argument for rendering often, not for
leaving the renderer untested: it is the one component whose output nothing else can
check, so the few things that *can* be asserted about it are worth pinning.

Three kinds of claim are made here. That the encoder produces a real PNG, which is
checked by decoding it. That the shading means what it says — a slope facing the light is
brighter than one facing away, and the shore is where the field crosses sea level rather
than where the lattice does. And that both are reproducible, because a world file that
renders differently on two machines is not a world file.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from fw.core.mapgen import noise, raster, shade
from fw.core.mapgen.grid import Grid

SIZE = 32


def decode(data: bytes) -> tuple[int, int, bytes]:
    """A minimal PNG reader, so the encoder is checked against the format and not itself."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    position, pixels, header = 8, b"", None
    while position < len(data):
        length = struct.unpack(">I", data[position:position + 4])[0]
        kind = data[position + 4:position + 8]
        body = data[position + 8:position + 8 + length]
        expected = struct.unpack(">I", data[position + 8 + length:position + 12 + length])[0]
        assert zlib.crc32(kind + body) == expected, f"bad CRC on {kind!r}"
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            pixels += body
        position += 12 + length
    assert header is not None, "no IHDR"
    width, height, depth, colour, compression, filtering, interlace = header
    assert (depth, colour) == (8, 2), "expected 8-bit RGB"
    assert (compression, filtering, interlace) == (0, 0, 0)

    raw = zlib.decompress(pixels)
    stride = width * 3
    rows, previous, cursor = [], bytearray(stride), 0
    for _ in range(height):
        kind = raw[cursor]
        cursor += 1
        row = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        if kind == 1:
            for n in range(3, stride):
                row[n] = (row[n] + row[n - 3]) & 0xFF
        elif kind == 2:
            for n in range(stride):
                row[n] = (row[n] + previous[n]) & 0xFF
        else:
            assert kind == 0, f"unexpected filter {kind}"
        rows.append(bytes(row))
        previous = row
    return width, height, b"".join(rows)


class TestThePngIsAPng:
    def test_it_round_trips_exactly(self):
        source = bytearray()
        for y in range(48):
            for x in range(64):
                source += bytes(((x * 4 + y) % 256, (y * 7) % 256, (x * x) % 256))
        width, height, back = decode(raster.encode(64, 48, source))
        assert (width, height) == (64, 48)
        assert back == bytes(source)

    def test_a_flat_image_compresses_to_almost_nothing(self):
        """Filtering is doing its job. A megapixel of one colour is not a megabyte."""
        flat = bytearray(b"\x80\x90\xa0" * (256 * 256))
        assert len(raster.encode(256, 256, flat)) < 2000

    def test_the_wrong_number_of_bytes_is_refused(self):
        with pytest.raises(ValueError):
            raster.encode(4, 4, bytearray(10))


def ridge_world(size: int = SIZE):
    """A single ridge running north to south, in a sea."""
    grid = Grid(size=size, span=float(size))
    height = grid.filled(0.0)
    half = (size - 1) / 2.0
    for j in range(size):
        for i in range(size):
            across = abs(i - half) / half
            height[j][i] = 0.55 * (1.0 - across) - 0.10
    return grid, height


class TestTheShadingMeansSomething:
    def test_the_lit_flank_is_brighter_than_the_shadowed_one(self):
        """The light comes from the north-west, as it does on every printed relief map.

        A reader shown light from below reads every valley as a ridge, so this is not a
        matter of taste — get the sign wrong and the terrain inverts.
        """
        grid, height = ridge_world()
        image = shade.render(grid, elevation=height, seed="s", scale=4, sea_level=0.0)
        middle = image.height // 2
        quarter = image.width // 4
        west = sum(image.sample(quarter, middle))
        east = sum(image.sample(image.width - quarter, middle))
        assert west > east, "the slope facing the light is not the brighter one"

    def test_the_shore_is_a_contour_and_not_a_lattice_edge(self):
        """The coastline must be finer than the simulation it came from.

        Rendered at four pixels to the cell, a shore taken from a land mask can only
        ever step in blocks of four; one taken from the interpolated field moves within
        the cell. So the boundary column varies from row to row, and it is that variation
        being present at all that says the field, not the mask, drew it.
        """
        size = SIZE
        grid = Grid(size=size, span=float(size))
        height = grid.filled(0.0)
        for j in range(size):
            for i in range(size):
                wobble = noise.fbm("shore", i / 5.0, j / 5.0, octaves=3) - 0.5
                height[j][i] = (i - size * 0.5) / size + 0.35 * wobble
        image = shade.render(grid, elevation=height, seed="s", scale=4, sea_level=0.0)
        edges = []
        for y in range(0, image.height, 3):
            row = [x for x in range(image.width)
                   if image.land[y * image.width + x]]
            if row:
                edges.append(min(row))
        assert len(edges) > 4
        assert len(set(edges)) > len(edges) // 3, (
            "the shore steps in whole lattice cells, so it was drawn from a mask")
        assert any(edge % image.scale for edge in edges), (
            "every shore pixel lands on a cell boundary")

    def test_the_sea_is_the_sea_and_the_land_is_not(self):
        grid, height = ridge_world()
        image = shade.render(grid, elevation=height, seed="s", scale=4, sea_level=0.0)
        assert 0.2 < image.land_share() < 0.9
        for y in range(0, image.height, 5):
            for x in range(0, image.width, 5):
                red, green, blue = image.sample(x, y)
                if image.land[y * image.width + x]:
                    assert blue <= max(red, green) + 6, "land is painted like water"
                else:
                    assert blue > red, "water is not painted like water"

    def test_higher_ground_is_tinted_differently_from_low(self):
        grid, height = ridge_world()
        image = shade.render(grid, elevation=height, seed="s", scale=4, sea_level=0.0)
        crest = image.sample(image.width // 2, image.height // 2)
        low = None
        for x in range(image.width):
            if image.land[(image.height // 2) * image.width + x]:
                low = image.sample(x, image.height // 2)
                break
        assert low is not None
        assert crest != low, "the hypsometric tint is not varying with height"


class TestNothingDependsOnLuck:
    def test_the_same_field_renders_the_same_bytes(self):
        grid, height = ridge_world()
        one = shade.render(grid, elevation=height, seed="s", scale=3, sea_level=0.0)
        two = shade.render(grid, elevation=height, seed="s", scale=3, sea_level=0.0)
        assert one.pixels == two.pixels
        assert (raster.encode(one.width, one.height, one.pixels)
                == raster.encode(two.width, two.height, two.pixels))

    def test_a_different_seed_moves_the_detail_but_not_the_shape(self):
        grid, height = ridge_world()
        one = shade.render(grid, elevation=height, seed="a", scale=3, sea_level=0.0)
        two = shade.render(grid, elevation=height, seed="b", scale=3, sea_level=0.0)
        assert one.pixels != two.pixels, "the seed does not reach the render"
        assert abs(one.land_share() - two.land_share()) < 0.08, (
            "the seed is moving the coastline, not just the texture on it")
