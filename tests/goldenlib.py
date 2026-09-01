"""How a golden is taken (V2 §45).

Two families, both anchored to the corpus so the renderer answers to more worlds than
Renn. The DrawPlan snapshot is a *normalised projection*: geometry ids are ULIDs,
random per world file, so the raw plan would differ between two identical builds —
everything else (text, positions, sizes, roles) is deterministic and is exactly what a
regression in composition changes. The relief signature hashes the decoded pixels,
not the PNG bytes, so a zlib build cannot fail the suite while a single pixel can.

`scripts/regenerate_goldens.py` rewrites the files when a change is intended; the
diff it produces is the review artefact.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import corpus

from fw.core.mapgen.apply import apply_plan
from fw.core.mapgen.decide import DecisionSet
from fw.core.mapgen.pipeline import plan_map
from fw.core.mapgen.plan import MapBrief
from fw.core.world import World

GOLDEN_DIR = Path(__file__).resolve().parent / "goldens"
SEED = "golden"

# Every corpus world sits under the DrawPlan snapshot; the relief signature runs on
# the four whose terrain is the point.
DRAWPLAN_WORLDS = tuple(corpus.CORPUS)
RELIEF_WORLDS = ("renn", "alps", "delta", "long_coast")
RELIEF_SCALE = 4


def accepted(name: str) -> World:
    """A corpus world with its map planned and everything accepted."""
    world = corpus.CORPUS[name]()
    plan = plan_map(world, MapBrief(seed=SEED, invent_settlements=True))
    apply_plan(world, plan, DecisionSet.accept_all(plan))
    return world


def drawplan_snapshot(world: World) -> dict:
    """The composition, with the per-file randomness projected out."""
    from fastapi.testclient import TestClient

    from fw.api.app import create_app

    drawn = TestClient(create_app(world)).get("/api/map").json()["draw"]

    def named(name: dict) -> dict:
        return {"text": name["text"], "kind": name["kind"], "tier": name["tier"],
                "role": name["role"], "size": name["size"], "x": name["x"],
                "y": name["y"], "anchor": name["anchor"],
                "face": name["face"], "tracking": name["tracking"],
                "halo": name["halo"],
                **({"path": name["path"]} if name.get("path") else {})}

    return {
        "bounds": drawn["bounds"],
        "mode": drawn["mode"],
        # Banded since D4: one solved composition per zoom band.
        "labels": {band: sorted((named(n) for n in shown),
                                key=lambda n: (n["text"], n["x"], n["y"]))
                   for band, shown in sorted(drawn["labels"].items())},
        "icons": sorted(
            ({"name": i["name"], "shape": i["shape"], "rank": i["rank"],
              "x": i["x"], "y": i["y"], "radius": i["radius"], "role": i["role"],
              "holder_role": i["holder_role"], "contested": i["contested"],
              "band": i["band"]}
             for i in drawn["icons"]),
            key=lambda i: (i["name"], i["x"], i["y"])),
        "legend": [{"label": e["label"], "role": e["role"], "swatch": e["swatch"],
                    "note": e["note"]} for e in drawn["legend"]],
        "unlabelled": sorted(
            ({"text": u["text"], "kind": u["kind"], "reason": u["reason"]}
             for u in drawn["unlabelled"]),
            key=lambda u: (u["text"], u["kind"])),
    }


def render_ground(world: World, scale: int = RELIEF_SCALE):
    """The relief exactly as `/api/map/relief.png` renders it."""
    from fw.core.mapgen import shade
    from fw.core.mapgen.grid import Grid

    ground = world.terrain()
    assert ground, "no accepted terrain to render"
    fields = ground["fields"]
    return shade.render(
        Grid(size=ground["size"], span=ground["span"],
             origin_x=ground["origin_x"], origin_y=ground["origin_y"]),
        elevation=fields["elevation"], seed=ground["seed"], scale=scale,
        sea_level=ground["sea_level"], canopy=fields.get("canopy"),
        marsh=fields.get("marsh"), flow=fields.get("flow"),
        shoreline=fields.get("shoreline"))


def relief_signature(world: World) -> str:
    """A hash of the rendered ground's pixels — the raster half's golden.

    Taken over the pixels rather than the file, so a zlib build cannot fail the
    suite while a single pixel still can.
    """
    picture = render_ground(world)
    digest = hashlib.sha256()
    digest.update(f"{picture.width}x{picture.height}".encode())
    digest.update(bytes(picture.pixels))
    return digest.hexdigest()


def pixel_difference(png_a: bytes, png_b: bytes) -> float:
    """Mean absolute channel difference in [0, 255] — the perceptual dial for a
    phase that *intends* drift and wants to say how much."""
    from fw.core.mapgen import raster

    wa, ha, pixels_a = raster.decode(png_a)
    wb, hb, pixels_b = raster.decode(png_b)
    if (wa, ha) != (wb, hb):
        return 255.0
    total = sum(abs(a - b) for a, b in zip(pixels_a, pixels_b, strict=True))
    return total / max(1, len(pixels_a))


def stored(kind: str, name: str) -> Path:
    return GOLDEN_DIR / kind / f"{name}.json"


def read_golden(kind: str, name: str):
    path = stored(kind, name)
    return json.loads(path.read_text()) if path.exists() else None


def write_golden(kind: str, name: str, value) -> None:
    path = stored(kind, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, sort_keys=True) + "\n")
