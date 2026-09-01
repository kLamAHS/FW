"""Measure the bundled map faces and write the generator's em tables.

The label solver estimates text width to build collision boxes, and an estimate
measured against one machine's Palatino is fiction on a machine that substitutes
Georgia. Since D1 the client renders labels in the OFL faces committed under
web/public/fonts, so the honest table is measured from exactly those files — once,
offline, here — and committed as literals in fw/core/mapgen/typefaces.py, which
keeps fw.core stdlib-pure.

Chromium's canvas measures at the font's own unit grid: at 100px every advance is
an exact multiple of 1/unitsPerEm, so the emitted floats are dyadic fractions that
round-trip byte-identically on every machine. Kerning is deliberately ignored
(a per-character table cannot see pairs; the solver's BREATHING_ROOM absorbs the
~0.1em worst pairs); letter-spacing is applied by the solver per glyph INCLUDING
the last, which is CSS semantics, verified against this same canvas.

Run:  .venv/bin/python scripts/measure_type.py   (rewrites fw/core/mapgen/typefaces.py)
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.browsers import find_chromium  # noqa: E402

FONTS = REPO / "web" / "public" / "fonts"
OUT = REPO / "fw" / "core" / "mapgen" / "typefaces.py"

# face key -> font file. The face key is what cartography.TYPE names and what the
# client maps back to a family/weight/style — one measured table per key.
FACES = {
    "serif": "Alegreya-var.ttf",
    "serif-italic": "Alegreya-Italic-var.ttf",
    "sc": "AlegreyaSC-Regular.ttf",
    "sans": "AlegreyaSans-Regular.ttf",
    "sans-medium": "AlegreyaSans-Medium.ttf",
    "sans-bold": "AlegreyaSans-Bold.ttf",
}

# Every character a label can plausibly carry: printable ASCII plus the non-ASCII
# letters the namer and the corpus worlds actually produce (Åsgardh-upon-Øre), and
# the punctuation the pipeline writes into names (en dash, typographic apostrophe).
CHARS = ([chr(c) for c in range(32, 127)]
         + list("–’ÅåÄäÖöØøÆæÉéÈèÊêÜüÑñÇçÍíÓóÚúÀàÂâÔô"))

MEASURE = """
async ([b64, chars]) => {
  const face = new FontFace('Probe', 'url(data:font/ttf;base64,' + b64 + ')');
  await face.load();
  document.fonts.add(face);
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  ctx.font = '100px Probe';
  const out = {};
  for (const ch of chars) out[ch] = ctx.measureText(ch).width / 100;
  return out;
}
"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    tables: dict[str, dict[str, float]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=find_chromium(),
                                    args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content("<body></body>")
        for face, filename in FACES.items():
            b64 = base64.b64encode((FONTS / filename).read_bytes()).decode()
            tables[face] = page.evaluate(MEASURE, [b64, CHARS])
        browser.close()

    lines = ['"""Em widths of the bundled map faces, measured — never edited by hand.',
             "",
             "Written by scripts/measure_type.py from the exact font files under",
             "web/public/fonts. Every value is an advance at the font's own unit grid",
             "(a dyadic fraction), so the table is byte-identical on every machine.",
             "The solver's collision boxes are built from these; a hand-tweaked value",
             'is a label that overlaps what it was measured not to."""',
             "",
             "from __future__ import annotations",
             ""]
    lines.append("EM: dict[str, dict[str, float]] = {")
    for face in FACES:
        table = tables[face]
        lines.append(f"    {face!r}: {{")
        row: list[str] = []
        for ch in CHARS:
            row.append(f"{ch!r}: {table[ch]!r}")
            if len(row) == 4:
                lines.append("        " + ", ".join(row) + ",")
                row = []
        if row:
            lines.append("        " + ", ".join(row) + ",")
        lines.append("    },")
    lines.append("}")
    lines.append("")
    lines.append("# A character the table has never seen (the writer's own alphabet)")
    lines.append("# falls back to its face's lowercase-x advance: mid-range, safe.")
    lines.append("FALLBACK: dict[str, float] = {")
    for face in FACES:
        lines.append(f"    {face!r}: {tables[face]['x']!r},")
    lines.append("}")
    lines.append("")

    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT} ({len(FACES)} faces x {len(CHARS)} chars)")
    for face in FACES:
        t = tables[face]
        print(f"  {face}: i={t['i']:.4f} m={t['m']:.4f} A={t['A']:.4f} x={t['x']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
