"""The Wolfswood, the Neck, the Sheepshead Hills — named things, not colours.

A biome map says a patch of ground is wooded. That is not the same as there being a
forest there. A forest on a map is a *thing*: it has a shape, an extent, a name, and
somewhere in the writer's notes it is where a character got lost. Until it has those it
is a shade of green and nobody can refer to it.

So contiguous ground of the same character becomes one feature, with its own outline,
its own name in the writer's register, and its own entity to hang facts on. A wood the
writer has already named is *adopted* rather than duplicated — the generator finds the
trees they were talking about and gives that shape their name, which is the whole point
of generating from source material rather than beside it.

Water breaks a wood in two. Two patches touching at a single corner are two patches.
Both of those are decisions with consequences, and both are made here rather than left
to whatever a flood fill happens to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.mapgen import shapes
from fw.core.mapgen.grid import Grid

# Which biomes become named features, and what a writer calls one of them.
NAMED_KINDS: dict[str, str] = {
    "forest": "forest",
    "marsh": "marsh",
    "hills": "downs",
    "highland": "moor",
    "desert": "waste",
    "glacier": "ice",
}

# The noun each kind wants in its name, in the namer's closed vocabulary.
NAME_HINT: dict[str, str] = {
    "forest": "forest", "marsh": "marsh", "downs": "upland",
    "moor": "upland", "waste": "arid", "ice": "region",
}

SMALLEST = 26           # cells; below this a patch is speckle, not a place
MAX_FEATURES = 24       # per world, largest first — a map is not a botanical survey


@dataclass
class NaturalFeature:
    """One named stretch of country."""

    kind: str                        # forest | marsh | downs | moor | waste | ice
    biome: str                       # the terrain kind it is made of
    cells: tuple[tuple[int, int], ...]
    rings: tuple[list[list[float]], ...]
    region_keys: tuple[str, ...]
    entity_id: str | None = None     # set when the writer already named it
    name: str = ""
    because: str = ""

    @property
    def centre(self) -> tuple[int, int]:
        return (round(sum(c[0] for c in self.cells) / len(self.cells)),
                round(sum(c[1] for c in self.cells) / len(self.cells)))

    @property
    def area(self) -> int:
        return len(self.cells)


@dataclass
class FeatureSet:
    features: tuple[NaturalFeature, ...] = ()
    notes: list[str] = field(default_factory=list)


def plan_features(grid: Grid, *, biome, sea: list[list[bool]],
                  channel: set[tuple[int, int]], owner: list[list[int]],
                  keys: tuple[str, ...],
                  authored: dict[str, tuple[str, str]] | None = None) -> FeatureSet:
    """Find the named country in the biome field.

    `authored` maps a lowercase name to (entity_id, kind) for features the writer has
    already made, so their Wolfswood is adopted rather than a second one invented
    beside it.
    """
    found: list[NaturalFeature] = []
    for biome_kind, feature_kind in sorted(NAMED_KINDS.items()):
        for component in _components(grid, biome, biome_kind, sea, channel):
            if len(component) < SMALLEST:
                continue
            found.append(_shape_up(grid, component, biome_kind, feature_kind,
                                   owner, keys))

    found.sort(key=lambda f: (-f.area, f.centre))
    notes: list[str] = []
    if len(found) > MAX_FEATURES:
        notes.append(f"{len(found) - MAX_FEATURES} smaller stretches of country were "
                     f"left unnamed to keep the map readable")
        found = found[:MAX_FEATURES]
    return FeatureSet(features=tuple(found), notes=notes)


def _components(grid: Grid, biome, kind: str, sea: list[list[bool]],
                channel: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Contiguous ground of one kind.

    Four-connected, so two patches meeting at a corner are two patches — a wood joined
    to another by a single diagonal is not one wood, and treating it as one produces
    sprawling features that follow no shape on the ground.

    A watercourse breaks a component. Rivers are what forests stop at, and a wood that
    spans both banks of a river reads as a mistake even when it is technically true.
    """
    size = grid.size
    seen = [[False] * size for _ in range(size)]
    out: list[list[tuple[int, int]]] = []
    for j in range(size):
        for i in range(size):
            if seen[j][i] or sea[j][i] or (i, j) in channel:
                continue
            if biome.terrain(i, j) != kind:
                continue
            stack = [(i, j)]
            seen[j][i] = True
            group: list[tuple[int, int]] = []
            while stack:
                ci, cj = stack.pop()
                group.append((ci, cj))
                for ni, nj in grid.neighbours(ci, cj, diagonal=False):
                    if (seen[nj][ni] or sea[nj][ni] or (ni, nj) in channel
                            or biome.terrain(ni, nj) != kind):
                        continue
                    seen[nj][ni] = True
                    stack.append((ni, nj))
            out.append(sorted(group))
    return out


def _shape_up(grid: Grid, cells: list[tuple[int, int]], biome_kind: str,
              feature_kind: str, owner: list[list[int]],
              keys: tuple[str, ...]) -> NaturalFeature:
    """Trace one component's outline, and note whose country it is in."""
    mask = grid.filled(0.0)
    for i, j in cells:
        mask[j][i] = 1.0
    rings = [shapes.closed(grid.to_world(ring))
             for ring, encloses in shapes.outlines(grid.blurred(mask), 0.5,
                                                   smallest=float(SMALLEST) * 0.4,
                                                   most=160)
             if encloses]
    share: dict[str, int] = {}
    for i, j in cells:
        index = owner[j][i]
        if 0 <= index < len(keys):
            share[keys[index]] = share.get(keys[index], 0) + 1
    in_regions = tuple(k for k, _ in sorted(share.items(), key=lambda p: (-p[1], p[0])))
    where = f" in {in_regions[0]}" if in_regions else ""
    return NaturalFeature(
        kind=feature_kind, biome=biome_kind, cells=tuple(cells),
        rings=tuple(rings), region_keys=in_regions,
        because=f"{len(cells)} cells of unbroken {biome_kind}{where}")


def adopt(features: list[NaturalFeature],
          authored: dict[str, tuple[str, str, tuple[int, int] | None]]) -> list[str]:
    """Give the writer's own names to the ground they were describing.

    A writer who has an entity called the Wolfswood and a generated wood in the same
    part of the world means one forest, not two. Matching by the kind of country and
    then by nearness is crude, and being crude out loud is better than inventing a
    second Wolfswood beside the first.
    """
    notes: list[str] = []
    for name in sorted(authored):
        entity_id, kind, at = authored[name]
        free = [f for f in features if f.entity_id is None and f.kind == kind]
        if not free:
            notes.append(f"there is nowhere on this map for “{name}” — no stretch of "
                         f"{kind} big enough to be it")
            continue
        if at is None:
            chosen = max(free, key=lambda f: (f.area, f.centre))
        else:
            chosen = min(free, key=lambda f: (abs(f.centre[0] - at[0])
                                              + abs(f.centre[1] - at[1]),
                                              -f.area, f.centre))
        chosen.entity_id = entity_id
        chosen.name = name
    return notes
