"""Draw a world's map to a picture, with no server and no client.

Every real defect the generator has shipped so far was found by *looking* at the
output, not by reading a test: every region an island, harbours on an inland vale,
roads cutting dead straight across a mountain range, settlements labelled for the
wrong region. Tests pin what you already thought to check; the picture shows what you
did not.

Standing up FastAPI, Vite and the React client to see one map costs about half a
minute. This reads the world file directly and writes an SVG, and — if a Chromium is
on the machine — rasterises it so it can be looked at without a browser at all.

    python scripts/render_map.py demo.fwworld --out /tmp/map.png --generate
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fw.core.world import World  # noqa: E402

# Drawing order, back to front: everything is painted over the land, and the labels
# are painted over everything.
LAYER_ORDER = ("base", "regions", "biomes", "waterways", "roads", "borders",
               "settlements", "sites", "labels")

FALLBACK_FILL = "#b9c4a0"
SEA = "#3f5b6c"
PAPER = "#e8e0cc"


def _bounds(features: list) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(node) -> None:
        if isinstance(node, (int, float)):
            return
        if (len(node) == 2 and all(isinstance(v, (int, float)) for v in node)):
            xs.append(float(node[0]))
            ys.append(float(node[1]))
            return
        for child in node:
            walk(child)

    for feature in features:
        walk(feature.coordinates)
    if not xs:
        return 0.0, 0.0, 100.0, 100.0
    pad = max(20.0, (max(xs) - min(xs)) * 0.03)
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad


def _points(ring) -> str:
    return " ".join(f"{float(p[0]):.1f},{float(p[1]):.1f}" for p in ring)


def render_svg(world: World, *, at: int | None = None, width: int = 1400) -> str:
    features = [g for g in world.geometries(at=at)]
    features.sort(key=lambda g: (LAYER_ORDER.index(g.layer)
                                 if g.layer in LAYER_ORDER else len(LAYER_ORDER),
                                 g.id))
    min_x, min_y, max_x, max_y = _bounds(features)
    span_x, span_y = max_x - min_x, max_y - min_y
    height = int(width * (span_y / span_x)) if span_x else width

    names = {e.id: e.name for e in world.entities()}
    out: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="{min_x:.1f} {min_y:.1f} {span_x:.1f} {span_y:.1f}">',
        f'<rect x="{min_x:.1f}" y="{min_y:.1f}" width="{span_x:.1f}" '
        f'height="{span_y:.1f}" fill="{SEA}"/>',
    ]
    labels: list[str] = []

    for f in features:
        style = f.style or {}
        dash = ' stroke-dasharray="7 5"' if f.approximate else ""
        if f.kind == "polygon":
            fill = style.get("fill", FALLBACK_FILL)
            for ring in f.coordinates:
                out.append(f'<polygon points="{_points(ring)}" fill="{fill}" '
                           f'fill-opacity="0.85" stroke="{fill}" '
                           f'stroke-width="1.2"{dash}/>')
        elif f.kind == "line":
            stroke = style.get("stroke", "#666")
            wide = 2.6 if f.layer == "waterways" else 1.8
            out.append(f'<polyline points="{_points(f.coordinates)}" fill="none" '
                       f'stroke="{stroke}" stroke-width="{wide}" '
                       f'stroke-linecap="round" stroke-linejoin="round"{dash}/>')
        elif f.kind == "point":
            x, y = float(f.coordinates[0]), float(f.coordinates[1])
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.4" '
                       f'fill="{style.get("fill", "#7a2b2b")}" '
                       f'stroke="{PAPER}" stroke-width="1.2"/>')
            name = names.get(f.entity_id)
            if name:
                labels.append(
                    f'<text x="{x + 5:.1f}" y="{y + 3:.1f}" font-size="9" '
                    f'font-family="Georgia,serif" fill="#2b2118" '
                    f'stroke="{PAPER}" stroke-width="2.4" paint-order="stroke">'
                    f'{_escape(name)}</text>')

    out.extend(labels)
    out.append("</svg>")
    return "\n".join(out)


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def rasterise(svg: str, destination: Path) -> bool:
    """Turn the SVG into a PNG with the Chromium already on the machine."""
    try:
        from playwright.sync_api import sync_playwright

        from scripts.browsers import find_chromium
    except ImportError:
        return False
    binary = find_chromium()
    if binary is None:
        return False
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=binary, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content(f'<body style="margin:0">{svg}</body>')
        page.locator("svg").screenshot(path=str(destination))
        browser.close()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", help="path to a .fwworld file")
    parser.add_argument("--out", default="/tmp/map.png")
    parser.add_argument("--at", type=int, default=None, help="day index to draw")
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--generate", action="store_true",
                        help="generate a map into the world first")
    parser.add_argument("--seed", default="render")
    args = parser.parse_args()

    world = World.open(args.world)
    try:
        if args.generate:
            from fw.core.mapgen.generate import generate_map
            print(generate_map(world, seed=args.seed).summary())
        svg = render_svg(world, at=args.at, width=args.width)
    finally:
        world.close()

    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix == ".svg" or not rasterise(svg, destination):
        destination = destination.with_suffix(".svg")
        destination.write_text(svg)
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
