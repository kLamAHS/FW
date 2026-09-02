"""The relief reaching the writer.

Everything else the map endpoints return is geometry, which the client draws. Relief is
an image, and an image travels differently: it is rendered once, cached, and fetched by
the browser rather than threaded through the client's data layer. These pin the parts of
that path a change could silently break — that there is nothing to draw before a map is
accepted, that what comes back is a real PNG of the accepted ground, and that asking
twice does not render twice.
"""

from __future__ import annotations

import struct

import pytest
from fastapi.testclient import TestClient

from fw.api.app import create_app
from fw.core.mapgen.apply import apply_plan
from fw.core.mapgen.pipeline import plan_map


@pytest.fixture
def client(renn):
    return TestClient(create_app(world=renn))


class TestBeforeAnyMapIsAccepted:
    def test_there_is_no_ground_to_draw(self, client):
        assert client.get("/api/map/relief").json() == {"available": False}

    def test_asking_for_the_picture_says_so_plainly(self, client):
        response = client.get("/api/map/relief.png")
        assert response.status_code == 404
        assert "no map" in response.json()["detail"]


class TestOnceItIs:
    @pytest.fixture
    def drawn(self, renn, client):
        apply_plan(renn, plan_map(renn))
        return client

    def test_the_bounds_say_where_the_ground_belongs(self, drawn):
        bounds = drawn.get("/api/map/relief").json()
        assert bounds["available"] is True
        assert bounds["width"] > 0 and bounds["height"] > 0
        assert bounds["updated_at"]

    def test_the_picture_is_a_png_of_the_right_size(self, drawn):
        response = drawn.get("/api/map/relief.png?scale=4")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        body = response.content
        assert body[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", body[16:24])
        assert width == height
        assert width == 144 * 4, "the scale asked for is not the scale rendered"

    def test_the_scale_is_bounded_both_ways(self, drawn):
        """A writer, or a bored browser, must not be able to ask for a gigapixel."""
        small = drawn.get("/api/map/relief.png?scale=1").content
        big = drawn.get("/api/map/relief.png?scale=99").content
        assert struct.unpack(">II", small[16:24])[0] >= 144 * 2
        assert struct.unpack(">II", big[16:24])[0] <= 144 * 14

    def test_asking_twice_renders_once(self, drawn):
        first = drawn.get("/api/map/relief.png?scale=3")
        second = drawn.get("/api/map/relief.png?scale=3")
        assert first.content == second.content
        assert len(first.content) > 1000

    def test_a_different_scale_is_a_different_picture(self, drawn):
        assert (drawn.get("/api/map/relief.png?scale=3").content
                != drawn.get("/api/map/relief.png?scale=4").content)

    def test_the_picture_is_mostly_not_one_colour(self, drawn):
        """A render that failed quietly comes back as a flat rectangle.

        Read as the spread of BLOCK means rather than as the count of distinct bytes
        in the compressed stream. Those bytes are PNG *filter deltas*, so what the old
        assertion measured was how much neighbouring pixels differ — and since the
        paper grain went in, neighbouring pixels differ everywhere by construction.
        Measured on a blank grey sheet with nothing but grain on it, the old check
        still fails (13 distinct, needing 20), so it had not gone vacuous; but what
        keeps it honest is now the grain's amplitude rather than anything the map
        does. A block mean averages the grain away and leaves the structure, which is
        the thing that actually goes missing when a render fails quietly: the same
        blank sheet spreads 1.0 against a floor of 30, and a real relief spreads far
        past it.
        """
        from fw.core.mapgen import raster

        body = drawn.get("/api/map/relief.png?scale=4").content
        width, height, pixels = raster.decode(body)
        step = max(1, width // 16)
        means = []
        for top in range(0, height - step, step):
            for left in range(0, width - step, step):
                total = 0
                for y in range(top, top + step):
                    row = (y * width + left) * 3
                    total += sum(pixels[row:row + step * 3])
                means.append(total / (step * step * 3))
        assert max(means) - min(means) > 30.0, "the relief came back flat"
        assert len(means) > 100
