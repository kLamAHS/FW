"""What kind of shore each stretch of coast is (V2 §4).

The single strongest "generated" tell in the old renders was that every mile of coast
had the same character: one wavelength of crenellation, one bright shelf band, from
fjord country to fen. Real coasts alternate — long depositional sweeps, drowned rocky
inlets, mud where a river arrives — and the difference between stretches is itself
information about the land behind them.

Everything the classification needs already exists: the slope erosion left at the
water, the marsh the vegetation stage found, the sediment the rivers dropped, the
bathymetry offshore, the mouths hydrology classified, and the very regime field the
coast was *drawn* from (rebuilt bit-for-bit from the seed — the same noise that
decided where the shore is indented decides here that it reads as drowned).

Classes come out in long runs because their drivers vary slowly, and a mode filter
kills the speckle; the art direction's floor (a class holds for a stretch, not a
cell) is measured in the tests rather than forced by fiat.
"""

from __future__ import annotations

from dataclasses import dataclass

from fw.core.mapgen import noise
from fw.core.mapgen.grid import Field

Cell = tuple[int, int]

# The closed vocabulary, and the integer each class travels as in the raster.
# 0 is "not shore". Order is priority: where two claims overlap, the lower wins.
CLASSES = ("delta", "estuary", "marsh", "fjord", "cliff", "rocky",
           "sheltered", "beach")
CODE = {name: n + 1 for n, name in enumerate(CLASSES)}
NAME = {n + 1: name for n, name in enumerate(CLASSES)}

MOUTH_REACH = 2          # cells around a classified mouth that take its class
CLIFF_SLOPE = 0.055      # fall per cell at which a shore is a wall
ROCKY_SLOPE = 0.030
MARSH_SHORE = 0.10       # marsh intensity at which the shore is reeds
FJORD_REGIME = 0.72      # the coast field's own "drowned" end
SHELTER_WINDOW = 4       # half-width of the bay-shelter window
SHELTER_LAND = 0.62      # land share around a cell that reads as an enclosed water
SMOOTH_PASSES = 3        # mode-filter rounds along the shore
SEAWARD = 3              # how far the class is spread into the sea for the renderer

# The regime noise must be rebuilt exactly as the coast built it.
_REGIME_SCALE = 0.42


@dataclass(frozen=True)
class Shoreline:
    """Every shore cell classified, plus the same classes spread into the shallows."""

    classes: dict[Cell, int]             # shore (land) cells only
    seaward: Field                       # class codes over nearby sea, 0 elsewhere

    def kind_of(self, cell: Cell) -> str:
        return NAME.get(self.classes.get(cell, 0), "")


def classify(size: int, *, sea: list[list[bool]], elevation: Field, slope: Field,
             marsh: Field, seed: str, mouths: dict[Cell, str]) -> Shoreline:
    """Read the character of every stretch of coast. Pure, no writes.

    `mouths` maps river-mouth cells to hydrology's own kind for them ("delta",
    "estuary", or "mouth") — the one input that is not a field, because a delta is
    where a specific river arrives, not a texture.
    """
    shore = _shore_cells(size, sea)
    regime_scale = size * _REGIME_SCALE
    regimes = noise.field(f"{seed}|regime", size, wavelength=regime_scale,
                          octaves=2, stride=max(1, int(regime_scale / 5)))

    classes: dict[Cell, int] = {}
    for cell in shore:
        classes[cell] = _class_of(size, cell, sea, slope, marsh, regimes, mouths)
    for _ in range(SMOOTH_PASSES):
        classes = _smoothed(classes)
    return Shoreline(classes=classes,
                     seaward=_spread(size, sea, classes))


def _shore_cells(size: int, sea) -> list[Cell]:
    out: list[Cell] = []
    for j in range(size):
        for i in range(size):
            if sea[j][i]:
                continue
            for ni, nj in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                if 0 <= ni < size and 0 <= nj < size and sea[nj][ni]:
                    out.append((i, j))
                    break
    return out


def _class_of(size: int, cell: Cell, sea, slope: Field, marsh: Field,
              regimes: Field, mouths: dict[Cell, str]) -> int:
    i, j = cell
    for (mi, mj), kind in mouths.items():
        if abs(mi - i) <= MOUTH_REACH and abs(mj - j) <= MOUTH_REACH:
            if kind == "delta":
                return CODE["delta"]
            if kind == "estuary":
                return CODE["estuary"]
    if marsh[j][i] >= MARSH_SHORE:
        return CODE["marsh"]
    steep = slope[j][i]
    if steep >= CLIFF_SLOPE:
        # Drowned and steep is a fjord coast; merely steep is a cliff. The regime
        # is the same field that indented the shore here in the first place.
        return CODE["fjord"] if regimes[j][i] >= FJORD_REGIME else CODE["cliff"]
    if steep >= ROCKY_SLOPE:
        return CODE["rocky"]
    if _enclosed(size, cell, sea) >= SHELTER_LAND:
        return CODE["sheltered"]
    return CODE["beach"]


def _enclosed(size: int, cell: Cell, sea) -> float:
    """How much of the water off this shore is held by land — a bay, or open sea."""
    i, j = cell
    land = total = 0
    for dj in range(-SHELTER_WINDOW, SHELTER_WINDOW + 1):
        for di in range(-SHELTER_WINDOW, SHELTER_WINDOW + 1):
            ni, nj = i + di, j + dj
            if 0 <= ni < size and 0 <= nj < size:
                total += 1
                if not sea[nj][ni]:
                    land += 1
    return land / total if total else 0.0


def _smoothed(classes: dict[Cell, int]) -> dict[Cell, int]:
    """One mode-filter pass along the shore, so a class never lives on one cell.

    Mouth classes are held fast: a delta two cells wide is still a delta, and the
    neighbouring sweep of beach must not vote it away.
    """
    out: dict[Cell, int] = {}
    held = (CODE["delta"], CODE["estuary"])
    for (i, j), code in classes.items():
        if code in held:
            out[(i, j)] = code
            continue
        votes: dict[int, int] = {code: 1}
        for dj in (-2, -1, 0, 1, 2):
            for di in (-2, -1, 0, 1, 2):
                if not (di or dj):
                    continue
                near = classes.get((i + di, j + dj))
                if near is not None and near not in held:
                    votes[near] = votes.get(near, 0) + 1
        out[(i, j)] = max(sorted(votes), key=lambda c: (votes[c], -c))
    return out


def _spread(size: int, sea, classes: dict[Cell, int]) -> Field:
    """The shore's classes, carried a few cells out to sea for the renderer.

    The picture's shore effects — a sand line, a mud fan, a hard dark edge — are
    painted on the water, and a sea pixel needs to know whose water it is. A short
    breadth-first spread from the classified shore answers that.
    """
    out = [[0.0] * size for _ in range(size)]
    frontier: list[Cell] = []
    for (i, j), code in sorted(classes.items()):
        out[j][i] = float(code)
        frontier.append((i, j))
    for _ in range(SEAWARD):
        next_front: list[Cell] = []
        for i, j in frontier:
            code = out[j][i]
            for ni, nj in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
                if (0 <= ni < size and 0 <= nj < size and sea[nj][ni]
                        and out[nj][ni] == 0.0):
                    out[nj][ni] = code
                    next_front.append((ni, nj))
        frontier = next_front
    return out
