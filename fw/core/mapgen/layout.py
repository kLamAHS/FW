"""Where each region goes, from what the writer said about which borders which.

A writer does not give their regions coordinates. They say the Northmarch borders the
Vale of Renn, the Salt Reach is on the coast, and the Vale is inland — and that is
genuinely enough to draw a map from, because those statements are a graph and a graph
has a shape.

So this is the step that turns the writer's sentences into a continent's skeleton: a
position for every region such that regions that border each other are adjacent, regions
that do not are not, coastal regions reach the edge and landlocked ones do not. The
landmass is then grown around that skeleton, which is why the resulting continent is
shaped like *their* world rather than like a blob — elongated where their regions run in
a line, branched where one borders three, pinched into a neck where two halves of the
world are joined by a single border.

Force-directed rather than a planar embedding: a writer's borders are frequently
non-planar (four regions can all claim to border each other) and an embedder that
refuses such a graph is useless, whereas forces simply settle for the best compromise
and say nothing. Deterministic throughout — the starting ring, the jitter and every
sum are ordered, because a map that moved every time it was drawn would be a lie.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from fw.core.mapgen import noise

ROUNDS = 260                # force iterations at the default size
IDEAL = 1.0                 # the distance a shared border wants, in layout units
APART = 1.9                 # how far two unrelated regions push each other
COOLING = 0.94


@dataclass(frozen=True)
class Site:
    """One region, as the layout sees it."""

    key: str
    weight: float = 1.0                        # relative extent
    coastal: bool = False
    fixed: tuple[float, float] | None = None   # already drawn: not ours to move


def arrange(sites: list[Site], borders: set[tuple[str, str]], *,
            seed: str = "layout", span: float = 900.0,
            margin: float = 60.0) -> dict[str, tuple[float, float]]:
    """A position in world units for every region.

    `borders` is an undirected set of key pairs. Regions the writer has already drawn
    pass a `fixed` position and are never moved — §66: the map proposes, it does not
    overwrite.
    """
    if not sites:
        return {}
    ordered = sorted(sites, key=lambda s: s.key)
    index = {site.key: n for n, site in enumerate(ordered)}
    edges = _edges(borders, index)

    places = _start(ordered, edges, seed)
    _settle(ordered, edges, places, seed)
    return _fit(ordered, places, span=span, margin=margin)


# ---- setting out -----------------------------------------------------------

def _edges(borders: set[tuple[str, str]],
           index: dict[str, int]) -> list[tuple[int, int]]:
    """Border pairs as index pairs, deduplicated and ordered."""
    found: set[tuple[int, int]] = set()
    for a, b in borders:
        if a in index and b in index and a != b:
            found.add((min(index[a], index[b]), max(index[a], index[b])))
    return sorted(found)


def _components(count: int, edges: list[tuple[int, int]]) -> list[list[int]]:
    """Groups of regions joined by borders — a world can be two continents."""
    parent = list(range(count))

    def find(n: int) -> int:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for a, b in edges:
        parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for n in range(count):
        groups.setdefault(find(n), []).append(n)
    return [groups[root] for root in sorted(groups)]


def _start(sites: list[Site], edges: list[tuple[int, int]],
           seed: str) -> list[list[float]]:
    """Opening positions: each connected group on its own ring, groups set apart.

    A ring rather than a random scatter, because forces resolve a ring quickly and
    reproducibly, and because two regions starting on the same point never separate.
    """
    groups = _components(len(sites), edges)
    places: list[list[float]] = [[0.0, 0.0] for _ in sites]
    columns = max(1, math.isqrt(len(groups)))
    for order, group in enumerate(groups):
        gx = (order % columns) * 6.0
        gy = (order // columns) * 6.0
        radius = 0.6 + 0.34 * len(group)
        for k, n in enumerate(group):
            angle = 2.0 * math.pi * k / len(group)
            wobble = noise.signed(f"{seed}|start", n) * 0.28
            places[n] = [gx + math.cos(angle) * radius * (1.0 + wobble),
                         gy + math.sin(angle) * radius * (1.0 + wobble)]
    for n, site in enumerate(sites):
        if site.fixed is not None:
            places[n] = [site.fixed[0], site.fixed[1]]
    return places


# ---- settling --------------------------------------------------------------

def _settle(sites: list[Site], edges: list[tuple[int, int]],
            places: list[list[float]], seed: str) -> None:
    count = len(sites)
    # Repulsion is every pair against every other. That is fine for the dozens of
    # regions a writer actually has, and would not be for hundreds, so the number of
    # rounds comes down as the world grows rather than the run time going up.
    rounds = max(60, int(ROUNDS * min(1.0, 40.0 / max(count, 1))))
    heat = 1.0
    for _ in range(rounds):
        push = [[0.0, 0.0] for _ in range(count)]

        for a in range(count):
            ax, ay = places[a]
            for b in range(a + 1, count):
                bx, by = places[b]
                dx, dy = ax - bx, ay - by
                gap = math.hypot(dx, dy) or 1e-6
                if gap < APART:
                    force = (APART - gap) / gap * 0.5
                    push[a][0] += dx * force
                    push[a][1] += dy * force
                    push[b][0] -= dx * force
                    push[b][1] -= dy * force

        for a, b in edges:
            ax, ay = places[a]
            bx, by = places[b]
            dx, dy = bx - ax, by - ay
            gap = math.hypot(dx, dy) or 1e-6
            # A shared border wants the two hearts about `IDEAL` apart — near enough to
            # touch, far enough that neither swallows the other.
            want = IDEAL * (sites[a].weight + sites[b].weight) * 0.5
            force = (gap - want) / gap * 0.34
            push[a][0] += dx * force
            push[a][1] += dy * force
            push[b][0] -= dx * force
            push[b][1] -= dy * force

        cx = sum(p[0] for p in places) / count
        cy = sum(p[1] for p in places) / count
        for n, site in enumerate(sites):
            dx, dy = places[n][0] - cx, places[n][1] - cy
            reach = math.hypot(dx, dy) or 1e-6
            # A coast is the outside of a landmass and an inland region is not, so the
            # writer saying "this one is on the sea" is a statement about where it sits
            # in the whole, not only about its own edge.
            drift = 0.16 if site.coastal else -0.11
            push[n][0] += dx / reach * drift
            push[n][1] += dy / reach * drift

        for n, site in enumerate(sites):
            if site.fixed is not None:
                continue                      # the writer drew it; it does not move
            places[n][0] += max(-0.6, min(0.6, push[n][0])) * heat
            places[n][1] += max(-0.6, min(0.6, push[n][1])) * heat
        heat *= COOLING


# ---- fitting the canvas ----------------------------------------------------

def _fit(sites: list[Site], places: list[list[float]], *,
         span: float, margin: float) -> dict[str, tuple[float, float]]:
    """Scale and centre the settled layout into the canvas.

    Regions the writer drew are already in world units, so if any are fixed the layout
    is fitted *to them* rather than rescaled — moving the writer's own map to suit a
    generated one would be exactly backwards.
    """
    anchored = [(n, s) for n, s in enumerate(sites) if s.fixed is not None]
    xs = [p[0] for p in places]
    ys = [p[1] for p in places]
    width = max(xs) - min(xs)
    height = max(ys) - min(ys)

    if anchored:
        return {site.key: (places[n][0], places[n][1])
                for n, site in enumerate(sites)}

    usable = span - 2.0 * margin
    scale = min(usable / width if width else usable,
                usable / height if height else usable)
    if not math.isfinite(scale) or scale <= 0:
        scale = 1.0
    mid_x = (max(xs) + min(xs)) / 2.0
    mid_y = (max(ys) + min(ys)) / 2.0
    return {
        site.key: (span / 2.0 + (places[n][0] - mid_x) * scale,
                   span / 2.0 + (places[n][1] - mid_y) * scale)
        for n, site in enumerate(sites)
    }


def spread(places: dict[str, tuple[float, float]]) -> float:
    """How far the laid-out world reaches — used to size the lattice around it."""
    if len(places) < 2:
        return 0.0
    xs = [p[0] for p in places.values()]
    ys = [p[1] for p in places.values()]
    return max(max(xs) - min(xs), max(ys) - min(ys))
