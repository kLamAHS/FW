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

import pathlib
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
        """Read as CHROMATICITY, because a plain difference cannot see the tint.

        This used to assert that a crest pixel and a low pixel were not the same
        colour, which they never are: the two sit at different angles to the light, so
        they differ by the hillshade whatever the hypsometric ramp does. Measured with
        the ramp replaced by one flat colour, the old assertion still passed — it had
        never tested the thing it was named for. (The paper grain would have made it
        vacuous too, by a second route, but it was already vacuous.)

        Shade multiplies all three channels together; only the tint changes their
        relation to each other. So normalise each block mean by its own total and the
        lighting drops out. Measured: 0.105 apart with the real ramp against 0.011
        with a flat one.
        """
        grid, height = ridge_world()
        image = shade.render(grid, elevation=height, seed="s", scale=4, sea_level=0.0)
        middle = image.height // 2

        def chromaticity(x: int, y: int, reach: int = 6):
            # A block, not a pixel: it averages the paper grain away, and the tint is
            # a property of the ground rather than of any one speck of it.
            total = [0, 0, 0]
            for j in range(y - reach, y + reach + 1):
                for i in range(x - reach, x + reach + 1):
                    red, green, blue = image.sample(i, j)
                    total[0] += red
                    total[1] += green
                    total[2] += blue
            share = sum(total) or 1
            return [value / share for value in total]

        low_x = next(x for x in range(8, image.width)
                     if image.land[middle * image.width + x])
        high = chromaticity(image.width // 2, middle)
        low = chromaticity(low_x + 8, middle)
        apart = sum(abs(a - b) for a, b in zip(high, low, strict=True))
        assert apart > 0.04, (
            f"the hypsometric tint is not varying with height ({apart:.4f} apart)")


class TestTheNightPlate:
    """The relief is the one part of the map painted from Python rather than from a
    CSS role, so the stylesheet's "every role has a dark value" guard cannot see it.
    For a phase it did not have one: a dark theme darkened the sea, the paper and the
    type and left a brightly lit daytime continent lying in the middle of them.
    """

    def test_the_night_sea_meets_the_colour_the_client_paints_beyond_it(self):
        """The seam that pins the whole night transform.

        The client fills everything outside the raster with `--map-sea`, so the deep
        end of the sea ramp has to BE that colour or the map ends in a visible
        rectangle. That is true by day and has to stay true by night, and it is the
        reason the night transform is anchored on the sea rather than chosen: these
        three numbers are `--map-sea`'s dark value over its light one.
        """
        import re

        sheet = (pathlib.Path(__file__).resolve().parent.parent
                 / "web" / "src" / "styles.css").read_text()
        night_block = sheet.split("prefers-color-scheme: dark", 1)[1]
        wanted = re.search(r"--map-sea:\s*#([0-9a-fA-F]{6})", night_block).group(1)
        deep = shade.SEA_RAMP[-1][1:]
        got = "".join(f"{int(c * n * 255.0 + 0.5):02x}"
                      for c, n in zip(deep, shade.NIGHT, strict=True))
        assert got == wanted.lower(), (
            f"the night sea ramp ends at #{got} and the stylesheet paints "
            f"#{wanted} beyond it — the map would end in a visible rectangle")

    def test_the_night_plate_is_darker_but_still_reads_as_a_map(self):
        grid, height = ridge_world()
        day = shade.render(grid, elevation=height, seed="s", scale=4, sea_level=0.0)
        night = shade.render(grid, elevation=height, seed="s", scale=4,
                             sea_level=0.0, night=True)

        def mean(image, want_land):
            total = count = 0
            for k in range(image.width * image.height):
                if bool(image.land[k]) is want_land:
                    total += sum(image.pixels[k * 3:k * 3 + 3])
                    count += 1
            return total / (3 * max(count, 1))

        assert mean(night, True) < mean(day, True) * 0.6, "the night plate still glares"
        # The coast is legible because the land is lighter than the water. Scaling
        # each ramp by its OWN role's light-to-dark ratio inverted exactly this —
        # the stylesheet darkens land and sea by different amounts — and the night
        # shore came out brighter than the night lowland.
        assert mean(night, True) > mean(night, False), (
            "the night sea is brighter than the night land, so the coast inverts")

    def test_the_night_plate_is_byte_identical_twice(self):
        grid, height = ridge_world()
        one = shade.render(grid, elevation=height, seed="s", scale=3,
                           sea_level=0.0, night=True)
        two = shade.render(grid, elevation=height, seed="s", scale=3,
                           sea_level=0.0, night=True)
        assert one.pixels == two.pixels


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
