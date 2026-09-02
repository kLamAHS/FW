"""The twelve houses of each palette, as drawn and as a dichromat sees them.

    python scripts/palette_chart.py [out.png]

Seven rows of twelve chips, and the argument for the whole exercise is rows 1 and 4:
the default palette put through the two commonest confusion axes, where four of its
houses collapse into one blue-grey and three more into one olive. Rows 3 and 6 are
the annealed blocks under the same projection, where all twelve still read apart.

Rows, top to bottom:
    0  default palette, as drawn
    1  default palette, as deuteranopia sees it
    2  the deuteranopia block, as drawn
    3  the deuteranopia block, as deuteranopia sees it
    4  default palette, as protanopia sees it
    5  the protanopia block, as drawn
    6  the protanopia block, as protanopia sees it
"""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from fw.core.mapgen import raster  # noqa: E402

# The same Brettel-style projections `tests/test_mapgen_guards.py` simulates with, so
# the picture and the guard are looking at the same thing.
DEUTERAN = ((0.625, 0.375, 0.0), (0.70, 0.30, 0.0), (0.0, 0.30, 0.70))
PROTAN = ((0.567, 0.433, 0.0), (0.558, 0.442, 0.0), (0.0, 0.242, 0.758))

ROOT = pathlib.Path(__file__).resolve().parent.parent
css = (ROOT / "web" / "src" / "styles.css").read_text()


def holders(selector):
    body = css.split(selector, 1)[1].split("\n}", 1)[0]
    return [re.search(rf"--map-holder-{n}:\s*(#[0-9a-f]{{6}})", body).group(1)
            for n in range(12)]


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[n:n + 2], 16) for n in (0, 2, 4))


def sim(c, m):
    return tuple(min(255, max(0, int(sum(a * v for a, v in zip(row, c, strict=True)))))
                 for row in m)


ROWS = [
    ("default, as drawn", holders(":root {"), None),
    ("default, deuteranopia", holders(":root {"), DEUTERAN),
    ("deuteranopia block, as drawn", holders(':root[data-palette="deuteranopia"] {'), None),
    ("deuteranopia block, simulated", holders(':root[data-palette="deuteranopia"] {'), DEUTERAN),
    ("default, protanopia", holders(":root {"), PROTAN),
    ("protanopia block, as drawn", holders(':root[data-palette="protanopia"] {'), None),
    ("protanopia block, simulated", holders(':root[data-palette="protanopia"] {'), PROTAN),
]

CHIP, GAP, PAD = 60, 6, 12
width = PAD * 2 + 12 * CHIP + 11 * GAP
height = PAD * 2 + len(ROWS) * (CHIP + GAP) - GAP
px = bytearray(b"\xf2" * (width * height * 3))

for r, (label, cols, matrix) in enumerate(ROWS):
    top = PAD + r * (CHIP + GAP)
    for c, colour in enumerate(cols):
        v = rgb(colour)
        if matrix:
            v = sim(v, matrix)
        left = PAD + c * (CHIP + GAP)
        for y in range(top, top + CHIP):
            for x in range(left, left + CHIP):
                k = (y * width + x) * 3
                px[k], px[k + 1], px[k + 2] = v
    print(f"{r:2d} {label}")

out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                   else ROOT / "docs" / "reference" / "palettes.png")
out.write_bytes(raster.encode(width, height, px))
print(f"wrote {out}")
