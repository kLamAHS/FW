"""Draw a world's map exactly the way the application draws it, with no server running.

Every real defect the generator has shipped was found by *looking* at the output, not
by reading a test. But the old version of this script drew its own picture — hex fills
the generator no longer emits, its own layer order, no icons and no labels — so what it
showed was not what the writer would see, and a defect it revealed might not exist.

This one is a parity renderer. The features and the draw plan come from the same
`/api/map` handler the client calls (through the in-process test client, so no port is
opened); the relief PNG is the server's own render, embedded; the colours are parsed
out of the client's stylesheet; and the painter's order — ground, polygons, hatch,
lines, icons, names — mirrors `MapView.tsx`. When this picture is wrong, the map is
wrong.

    python scripts/render_map.py demo.fwworld --out /tmp/map.png --generate
    python scripts/render_map.py demo.fwworld --mode occupies --dark --at 4200
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fw.core.world import World  # noqa: E402

STYLESHEET = Path(__file__).resolve().parent.parent / "web" / "src" / "styles.css"

# MapView's own compositing rules, mirrored.
REDUNDANT_OVER_GROUND = {"land", "features", "waters"}

# MapView's `BORDER_IN_ATLAS`: what a frontier gains when the wash under it goes thin.
BORDER_IN_ATLAS = 1.45
LABEL_HALO_WIDTH = 3.0

STAR = ((0, -1), (0.225, -0.309), (0.951, -0.309), (0.363, 0.118), (0.588, 0.809),
        (0, 0.382), (-0.588, 0.809), (-0.363, 0.118), (-0.951, -0.309),
        (-0.225, -0.309))


def _blocks(css: str) -> list[tuple[str, str]]:
    """Every rule in the sheet as (selector, body), comments stripped.

    A hand-rolled brace walk rather than a regex: the sheet nests (`@media` holds
    rules) and a regex over the whole file cannot tell a declaration inside a rule
    from one inside a comment. Which was not academic — a commented-out example of
    a custom property parsed as a live declaration and, because the old parser let
    the last duplicate win, could silently replace the real palette.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out: list[tuple[str, str]] = []
    depth, start, head = 0, 0, ""
    for n, ch in enumerate(css):
        if ch == "{":
            if depth == 0:
                head = css[start:n].strip()
                start = n + 1
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append((head, css[start:n]))
                start = n + 1
    return out


def _declared(body: str) -> list[tuple[str, str]]:
    """The custom properties a rule body sets, including an unterminated last one."""
    return re.findall(r"--([a-z0-9-]+)\s*:\s*([^;}]+)", body)


DARK_QUERY = "prefers-color-scheme: dark"


def stylesheet_colours(dark: bool = False, palette: str = "") -> dict[str, str]:
    """Every CSS custom property, resolved, from the client's own stylesheet.

    Read per rule rather than by splitting the file on the dark media query. The
    split was load-bearing and fragile in three ways worth naming, because each one
    failed *silently* into a grey or wrongly-themed picture: reformatting the media
    query at all dropped the whole dark palette; a second dark block was ignored;
    and an accessibility palette had no position that worked — before the query it
    overwrote the defaults, after it, it was invisible.

    Precedence is the cascade's own: the base `:root`, then the requested palette,
    then dark overrides of both. `var(--x)` references are chased so a role defined
    in terms of another resolves to paint.
    """
    values: dict[str, str] = {}
    wanted = f'[data-palette="{palette}"]' if palette else ""

    for head, body in _blocks(STYLESHEET.read_text()):
        if DARK_QUERY in head:
            if not dark:
                continue
            for inner_head, inner_body in _blocks(body):
                if _selects(inner_head, wanted):
                    values.update(_declared(inner_body))
        elif _selects(head, wanted):
            values.update(_declared(body))

    def resolve(value: str, seen: frozenset = frozenset()) -> str:
        match = re.match(r"var\(\s*--([a-z0-9-]+)\s*[,)]", value.strip())
        if match and match.group(1) not in seen:
            inner = values.get(match.group(1))
            if inner is not None:
                return resolve(inner, seen | {match.group(1)})
        return value.strip()

    return {name: resolve(value) for name, value in values.items()}


def _selects(head: str, wanted: str) -> bool:
    """Whether a rule sets the palette we are painting with.

    `:root` is the base every palette starts from; a `[data-palette=...]` block
    counts only when that palette was asked for. Anything else — a component rule
    that happens to set a custom property — is styling, not palette.
    """
    head = head.strip()
    if head.startswith(":root") and "[data-palette" not in head:
        return True
    return bool(wanted) and wanted in head


def paint(css: dict[str, str], role: str, fallback: str = "#888888") -> str:
    return css.get(f"map-{role}", css.get(role, fallback))


def the_map(world: World, *, day: int | None, mode: str, seen_as: str | None,
            scale: int):
    """The same three requests the client makes, through the in-process client."""
    from fastapi.testclient import TestClient

    from fw.api.app import create_app

    client = TestClient(create_app(world))
    query: dict = {"mode": mode}
    if day is not None:
        query["day"] = day
    if seen_as:
        query["as"] = seen_as
    data = client.get("/api/map", params=query).json()
    relief = client.get("/api/map/relief").json()
    png = None
    if relief.get("available"):
        got = client.get(f"/api/map/relief.png?scale={scale}")
        if got.status_code == 200:
            png = got.content
    return data, relief, png


def fill_for(feature: dict, mode: str, holders: dict[str, str],
             css: dict[str, str]) -> str:
    """MapView's `fillFor`, resolved to a literal colour."""
    holder = (feature.get("control", {}).get(mode) or [None])[0]
    if holder and holders.get(holder["id"]):
        return paint(css, holders[holder["id"]])
    role = (feature.get("style") or {}).get("role")
    if role:
        return paint(css, str(role))
    style = feature.get("style") or {}
    return str(style.get("fill") or style.get("stroke") or css.get("ink-faint", "#777"))


def _points(ring) -> str:
    return " ".join(f"{float(p[0]):.1f},{float(p[1]):.1f}" for p in ring)


def _polygon_path(rings) -> str:
    return " ".join(
        "M" + " L".join(f"{float(p[0]):.1f},{float(p[1]):.1f}" for p in ring) + " Z"
        for ring in rings)


def _line_path(points) -> str:
    return "M" + " L".join(f"{float(p[0]):.1f},{float(p[1]):.1f}" for p in points)


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _icon(icon: dict, css: dict[str, str]) -> str:
    """One place, as `Place` in MapView draws it."""
    x, y, r = float(icon["x"]), float(icon["y"]), float(icon["radius"])
    fill = (paint(css, icon["holder_role"]) if icon.get("holder_role")
            else paint(css, icon["role"]))
    panel = css.get("panel", "#ffffff")
    shape = icon.get("shape", "disc")
    out: list[str] = []
    if shape == "star":
        d = " ".join(f"{'M' if i == 0 else 'L'}{x + dx * r:.1f},{y + dy * r:.1f}"
                     for i, (dx, dy) in enumerate(STAR)) + " Z"
        out.append(f'<path d="{d}" fill="{fill}" stroke="{panel}" stroke-width="2"/>')
    elif shape == "keep":
        s = r * 0.78
        out.append(f'<rect x="{x - s:.1f}" y="{y - s:.1f}" width="{2 * s:.1f}" '
                   f'height="{2 * s:.1f}" transform="rotate(45 {x:.1f} {y:.1f})" '
                   f'fill="{fill}" stroke="{panel}" stroke-width="2"/>')
    elif shape == "tower":
        out.append(f'<rect x="{x - r * 0.5:.1f}" y="{y - r:.1f}" width="{r:.1f}" '
                   f'height="{2 * r:.1f}" fill="{fill}" stroke="{panel}" '
                   f'stroke-width="1.5"/>')
    elif shape == "ring":
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{panel}" '
                   f'stroke="{fill}" stroke-width="3"/>')
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r * 0.34:.1f}" '
                   f'fill="{fill}"/>')
    elif shape == "anchor":
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{fill}" '
                   f'stroke="{panel}" stroke-width="2"/>')
        out.append(f'<path d="M{x:.1f},{y - r * 0.7:.1f}V{y + r * 0.7:.1f}'
                   f'M{x - r * 0.6:.1f},{y + r * 0.15:.1f} '
                   f'A{r * 0.6:.1f},{r * 0.6:.1f} 0 0 0 '
                   f'{x + r * 0.6:.1f},{y + r * 0.15:.1f}" fill="none" '
                   f'stroke="{panel}" stroke-width="1.4"/>')
    else:
        radius = r * 0.7 if shape == "dot" else r
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" '
                   f'fill="{fill}" stroke="{panel}" stroke-width="2"/>')
    if icon.get("contested"):
        out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r + 5:.1f}" fill="none" '
                   f'stroke="{paint(css, "contested")}" stroke-width="1.5" '
                   f'stroke-dasharray="3 3"/>')
    return "".join(out)


# The voice arrives on the wire since D1: a face key plus tracking, exactly what
# MapView's FACE_CSS resolves. The family names here are the bundled faces embedded
# by `_font_css` below — never a system stack, or the pixels stop matching the client.
FACE_SVG = {
    "serif": ("Alegreya", 400, "normal"),
    "serif-italic": ("Alegreya", 400, "italic"),
    "sc": ("Alegreya SC", 400, "normal"),
    "sans": ("Alegreya Sans", 400, "normal"),
    "sans-medium": ("Alegreya Sans", 500, "normal"),
    "sans-bold": ("Alegreya Sans", 700, "normal"),
}


def _label(label: dict, css: dict[str, str], n: int) -> str:
    """One name, as `Name` in MapView draws it — halo, voice colours, curves."""
    role = str(label.get("role") or "label")
    fill = css.get(f"map-{role}", css.get("map-label", "#16181d"))
    halo = css.get("map-halo", "#f7f5f1")
    size = float(label["size"])
    family, weight, style = FACE_SVG.get(str(label.get("face") or "serif"),
                                         FACE_SVG["serif"])
    tracking = float(label.get("tracking") or 0.0)
    extra = f' letter-spacing="{tracking}em"' if tracking else ""
    if weight != 400:
        extra += f' font-weight="{weight}"'
    if style != "normal":
        extra += f' font-style="{style}"'
    text = label["text"]
    weight_of_halo = float(label.get("halo") or LABEL_HALO_WIDTH)
    common = (f'font-size="{size:.1f}" fill="{fill}" stroke="{halo}" '
              f'stroke-width="{weight_of_halo}" stroke-linejoin="round" '
              f'paint-order="stroke" font-family="{family}"'
              + extra)
    path = label.get("path")
    if path and len(path) > 1:
        return (f'<defs><path id="lp{n}" d="{_line_path(path)}" fill="none"/></defs>'
                f'<text {common} text-anchor="middle" dy="{size * 0.34:.1f}">'
                f'<textPath href="#lp{n}" startOffset="50%">{_escape(text)}'
                f'</textPath></text>')
    anchor = {"start": "start", "end": "end"}.get(str(label.get("anchor")), "middle")
    return (f'<text x="{float(label["x"]):.1f}" y="{float(label["y"]):.1f}" '
            f'text-anchor="{anchor}" {common}>{_escape(text)}</text>')


def render_svg(data: dict, relief: dict, png: bytes | None, *,
               css: dict[str, str], mode: str, width: int = 1400,
               labels: bool = True, band: str = "world",
               presentation: str = "auto") -> str:
    draw = data.get("draw") or {}
    bounds = draw.get("bounds") or {"x": 0, "y": 0, "width": 100, "height": 100}
    x0, y0 = float(bounds["x"]), float(bounds["y"])
    w, h = float(bounds["width"]), float(bounds["height"])
    height = int(width * (h / w)) if w else width
    holders = {str(k): str(v) for k, v in (draw.get("holders") or {}).items()}
    ground = bool(png)
    # MapView: 'auto' is not a third way of drawing the map, it is whichever of the
    # two suits the ground underneath.
    atlas = ground if presentation == "auto" else presentation == "atlas"
    contested_colour = paint(css, "contested")

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}">',
        # The open water past the rendered ground — MapView paints it on the wrapper.
        f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'fill="{paint(css, "sea")}"/>',
        f'<defs><pattern id="contested-hatch" width="8" height="8" '
        f'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<line x1="0" y1="0" x2="0" y2="8" stroke="{contested_colour}" '
        f'stroke-width="1.6"/></pattern></defs>',
    ]
    if png:
        uri = "data:image/png;base64," + base64.b64encode(png).decode()
        out.append(f'<image href="{uri}" x="{relief["x"]}" y="{relief["y"]}" '
                   f'width="{relief["width"]}" height="{relief["height"]}" '
                   f'preserveAspectRatio="none"/>')

    features = data.get("features") or []
    for f in (f for f in features if f["kind"] == "polygon"):
        fill = fill_for(f, mode, holders, css)
        # MapView's `fillOpacityFor`: land, natural features and open water answer to
        # the relief and not to the presentation — with the ground drawn they are the
        # same ink twice, with it off they are the only ground there is.
        if f["layer"] in REDUNDANT_OVER_GROUND:
            opacity = 0.0 if ground else 0.3
        else:
            opacity = 0.12 if atlas else 0.3
        # MapView: a polygon marked edge:none carries no stroke of its own — its
        # borders arrive as shared arcs, each stroked once, and the coastline wins
        # on its seaward side.
        edge = "none" if (f.get("style") or {}).get("edge") == "none" else fill
        # MapView: water's edge is where the ground stops, not a guess to be moved.
        waters = f["layer"] == "waters"
        dash = ('' if waters else
                ' stroke-dasharray="7 5"' if f.get("approximate") else "")
        wide = 1.0 if waters else 1.5
        d = _polygon_path(f["coordinates"])
        out.append(f'<path d="{d}" fill="{fill}" fill-opacity="{opacity}" '
                   f'stroke="{edge}" stroke-width="{wide}"{dash}/>')
        if (f.get("control") or {}).get("claims"):
            out.append(f'<path d="{d}" fill="url(#contested-hatch)" stroke="none"/>')

    for f in (f for f in features if f["kind"] == "line"):
        style = f.get("style") or {}
        wide = style.get("stroke-width") or (3.5 if f["layer"] == "waterways" else 2.5)
        # MapView: in atlas presentation the frontier takes the weight the fill gave up.
        if style.get("role") == "border" and atlas:
            wide *= BORDER_IN_ATLAS
        # Only an explicit dash in the style: MapView does not dash approximate
        # *lines* (a generated river is a guess about a real river, not a sketch).
        dash = ' stroke-dasharray="6 4"' if style.get("dash") else ""
        # MapView: a border arc paints in border ink whoever holds either side, and
        # a shore run in coastline ink — both are edges of the ground, not of a
        # holding.
        role_of = style.get("role")
        stroke = (paint(css, "border") if role_of == "border"
                  else paint(css, "coastline") if role_of == "coastline"
                  else fill_for(f, mode, holders, css))
        out.append(f'<path d="{_line_path(f["coordinates"])}" fill="none" '
                   f'stroke="{stroke}" '
                   f'stroke-width="{wide}" stroke-linecap="round" '
                   f'stroke-linejoin="round"{dash}/>')

    # Since D4 the composition is banded: MapView picks the band from its zoom, a
    # still image picks it from the caller. Icons filter to the band; labels come
    # already solved per band.
    order = {"world": 0, "regional": 1, "local": 2}
    depth = order.get(band, 0)
    for icon in draw.get("icons") or []:
        if order.get(str(icon.get("band") or "world"), 0) <= depth:
            out.append(_icon(icon, css))
    if labels:
        named = draw.get("labels") or {}
        shown = named.get(band, []) if isinstance(named, dict) else named
        for n, label in enumerate(shown):
            out.append(_label(label, css, n))
    out.append("</svg>")
    return "\n".join(out)


def _font_css() -> str:
    """The bundled faces, embedded as data URIs so the page needs no server.

    The same files the client serves from /fonts and the width tables were measured
    from — a substitute serif from fontconfig is exactly the drift the bundling
    exists to end.
    """
    fonts = Path(__file__).resolve().parent.parent / "web" / "public" / "fonts"
    faces = (("Alegreya", "Alegreya-var.ttf", "400 900", "normal"),
             ("Alegreya", "Alegreya-Italic-var.ttf", "400 900", "italic"),
             ("Alegreya SC", "AlegreyaSC-Regular.ttf", "400", "normal"),
             ("Alegreya Sans", "AlegreyaSans-Regular.ttf", "400", "normal"),
             ("Alegreya Sans", "AlegreyaSans-Medium.ttf", "500", "normal"),
             ("Alegreya Sans", "AlegreyaSans-Bold.ttf", "700", "normal"))
    out = []
    for family, filename, weight, style in faces:
        file = fonts / filename
        if not file.exists():
            continue
        b64 = base64.b64encode(file.read_bytes()).decode()
        out.append(f"@font-face {{ font-family: '{family}'; font-weight: {weight}; "
                   f"font-style: {style}; "
                   f"src: url(data:font/ttf;base64,{b64}); }}")
    return "\n".join(out)


def rasterise(svg: str, destination: Path, background: str) -> bool:
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
        page.set_content(f"<style>{_font_css()}</style>"
                         f'<body style="margin:0;background:{background}">{svg}</body>')
        page.evaluate("document.fonts.ready")
        page.locator("svg").screenshot(path=str(destination))
        browser.close()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("world", help="path to a .fwworld file")
    parser.add_argument("--out", default="/tmp/map.png")
    parser.add_argument("--at", type=int, default=None, help="day index to draw")
    parser.add_argument("--mode", default="legally_owns",
                        help="which authority colours the map")
    parser.add_argument("--as", dest="seen_as", default=None,
                        help="observer entity id — whose eyes (§94)")
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--scale", type=int, default=8, help="relief render scale")
    parser.add_argument("--dark", action="store_true", help="the night palette")
    parser.add_argument("--palette", default=None,
                        help="an alternative palette: deuteranopia, protanopia, "
                             "quiet, high-contrast")
    parser.add_argument("--presentation", default="auto",
                        choices=("auto", "atlas", "analytical"),
                        help="how the political plate is coloured")
    parser.add_argument("--no-labels", action="store_true")
    parser.add_argument("--band", default="world",
                        choices=("world", "regional", "local"),
                        help="which zoom band's composition to draw")
    parser.add_argument("--generate", action="store_true",
                        help="plan and accept a map into the world first")
    parser.add_argument("--seed", default="render")
    args = parser.parse_args()

    world = World.open(args.world)
    try:
        if args.generate:
            from fw.core.mapgen.apply import apply_plan
            from fw.core.mapgen.decide import DecisionSet
            from fw.core.mapgen.pipeline import plan_map
            from fw.core.mapgen.plan import MapBrief

            plan = plan_map(world, MapBrief(seed=args.seed, at=args.at,
                                            invent_settlements=True))
            report = apply_plan(world, plan, DecisionSet.accept_all(plan))
            print(f"accepted {sum(report.counts.values())} features "
                  f"({report.counts})")
        data, relief, png = the_map(world, day=args.at, mode=args.mode,
                                    seen_as=args.seen_as, scale=args.scale)
    finally:
        world.close()

    css = stylesheet_colours(dark=args.dark, palette=args.palette or "")
    svg = render_svg(data, relief, png, css=css, mode=args.mode, width=args.width,
                     labels=not args.no_labels, band=args.band,
                     presentation=args.presentation)

    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix == ".svg" or not rasterise(
            svg, destination, css.get("map-sea", "#4a6580")):
        destination = destination.with_suffix(".svg")
        destination.write_text(svg)
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
