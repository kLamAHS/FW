"""Re-pin the map goldens after an intended change (V2 §45).

Rewrites the DrawPlan snapshots and relief signatures under tests/goldens/, and drops
a low-scale reference PNG per relief world under docs/reference/ so the change can be
*looked at* — the diff of the JSON and the pair of pictures are the review artefacts.

    python scripts/regenerate_goldens.py            # everything
    python scripts/regenerate_goldens.py alps renn  # just these worlds
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import goldenlib  # noqa: E402

REFERENCE = ROOT / "docs" / "reference"


def main() -> int:
    chosen = set(sys.argv[1:])
    for name in goldenlib.DRAWPLAN_WORLDS:
        if chosen and name not in chosen:
            continue
        world = goldenlib.accepted(name)
        try:
            goldenlib.write_golden("drawplan", name,
                                   goldenlib.drawplan_snapshot(world))
            print(f"drawplan/{name} pinned")
            if name in goldenlib.RELIEF_WORLDS:
                goldenlib.write_golden("relief", name, {
                    "scale": goldenlib.RELIEF_SCALE,
                    "signature": goldenlib.relief_signature(world),
                })
                from fw.core.mapgen import raster

                REFERENCE.mkdir(parents=True, exist_ok=True)
                picture = goldenlib.render_ground(world)
                png = raster.encode(picture.width, picture.height, picture.pixels)
                (REFERENCE / f"relief-{name}.png").write_bytes(png)
                print(f"relief/{name} pinned (+ docs/reference/relief-{name}.png)")
        finally:
            world.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
