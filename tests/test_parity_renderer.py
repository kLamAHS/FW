"""The parity renderer must draw what the client draws.

`scripts/render_map.py` exists so a map can be *looked at* without a browser, and
every visual defect this project has found was found by looking. That only works
while the still picture agrees with the app — a parity renderer that quietly differs
is worse than none, because it is trusted. These are the differences that have
actually happened, kept as tests so they cannot happen twice.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import render_map  # noqa: E402


def _plan(icons: list[dict] | None = None) -> dict:
    return {
        "draw": {
            "bounds": {"x": 0, "y": 0, "width": 100, "height": 100},
            "holders": {},
            "icons": icons or [],
            "labels": {"world": [], "regional": [], "local": []},
        },
        "features": [],
    }


def _svg(data: dict, *, png: bytes | None, **kwargs) -> str:
    css = render_map.stylesheet_colours()
    return render_map.render_svg(data, {"x": 0, "y": 0, "width": 100, "height": 100},
                                 png, css=css, mode="legally_owns", **kwargs)


class TestTheGroundDecidesTheBackground:
    """MapView paints the open sea on the wrapper, and only when the ground is
    drawn: `style={groundShown ? { background: OPEN_WATER } : undefined}`. With no
    relief the client's `.map-wrap` keeps `--panel`, which is white.

    Painting it unconditionally was a parity break with teeth. On a world with no
    accepted relief the app draws its regions over white and this drew them over
    deep sea, so a holder fill at 0.12 opacity came out around `#527090` here
    against `#f1ebe6` in the browser: the same map and not the same picture, and
    the picture is the thing being judged.
    """

    def test_no_ground_means_no_sea_behind_the_map(self):
        svg = _svg(_plan(), png=None)
        sea = render_map.paint(render_map.stylesheet_colours(), "sea")
        assert f'fill="{sea}"' not in svg, "the sea is painted with no ground under it"

    def test_ground_brings_the_open_water_back(self):
        svg = _svg(_plan(), png=b"not really a png, only its truthiness is read")
        sea = render_map.paint(render_map.stylesheet_colours(), "sea")
        assert f'fill="{sea}"' in svg, "the open water past the ground is missing"


class TestIconsStackTheSameWayUp:
    """MapView emits one `<g>` per band in BANDS order, so where two icons overlap
    the narrower band paints on top — a town shows over the castle beside it, not
    under it. `draw.icons` is not sorted by band, so walking it straight inverted
    the pair. Painter order is not decoration: it decides which of two things a
    reader sees.
    """

    def test_a_local_icon_is_drawn_after_a_regional_one(self):
        # Deliberately out of band order in the wire list, which is how they arrive.
        icons = [
            {"key": "a", "band": "local", "shape": "disc", "x": 10, "y": 10,
             "radius": 4, "role": "settlement"},
            {"key": "b", "band": "world", "shape": "disc", "x": 11, "y": 10,
             "radius": 6, "role": "settlement"},
            {"key": "c", "band": "regional", "shape": "disc", "x": 12, "y": 10,
             "radius": 5, "role": "castle"},
        ]
        svg = _svg(_plan(icons), png=None, band="local")
        # Recover the emission order by each icon's own radius, which is unique here.
        radii = [float(r) for r in re.findall(r'\br="([0-9.]+)"', svg)]
        assert radii == [6.0, 5.0, 4.0], (
            f"icons came out {radii}, not widest band first — an icon in a narrower "
            f"band must paint over one in a wider band, as the client's groups do")

    def test_a_band_narrower_than_the_view_is_not_drawn_at_all(self):
        icons = [
            {"key": "a", "band": "local", "shape": "disc", "x": 10, "y": 10,
             "radius": 4, "role": "settlement"},
            {"key": "b", "band": "world", "shape": "disc", "x": 11, "y": 10,
             "radius": 6, "role": "settlement"},
        ]
        svg = _svg(_plan(icons), png=None, band="world")
        radii = [float(r) for r in re.findall(r'\br="([0-9.]+)"', svg)]
        assert radii == [6.0], f"the local icon leaked into the world band: {radii}"


class TestThePaletteFlagCannotBeMisspelled:
    def test_every_offered_palette_is_one_the_stylesheet_defines(self):
        """A typo used to exit 0 and draw the default palette — handing the reader
        who asked for a colour-blind palette exactly the map they cannot read."""
        found = render_map.palettes()
        assert found >= {"deuteranopia", "protanopia", "quiet", "high-contrast"}
        assert "deuteranopa" not in found
