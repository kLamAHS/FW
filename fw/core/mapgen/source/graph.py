"""What the writer said borders what, as a graph the map can reason over.

Until now this was a set of name pairs. A set is enough to seed regions near the
neighbours they were said to have, and not enough for anything else — and there are three
questions the layout stages have wanted answered for a long time.

**Is this even drawable?** Four regions can all claim to border each other and a plane
cannot always oblige. Euler's condition on a planar graph is one line of arithmetic and it
says, before anything is drawn, that some border is going to be lost. Saying which is more
use than either failing or pretending.

**Where does the country pinch?** An articulation point is a region whose removal cuts the
kingdom in two — which is exactly a neck, an isthmus, the one province the road has to go
through. The coastline has never known where those are, so it has never narrowed there.

**How far is everything from everything?** Hop counts over stated adjacency are what let a
region infer from its neighbours when the writer said nothing about it themselves.

Tarjan's algorithm, written as an explicit stack. A recursive one is shorter and a world
with three hundred regions in a chain would end it with a RecursionError.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class BorderEdge:
    """One stated or inferred adjacency, and how sure of it the map is."""

    a: str                              # always a < b
    b: str
    hard: bool                          # the writer said so, in a `borders` fact
    confidence: float
    because: str


@dataclass(frozen=True)
class BorderGraph:
    """Who borders whom, with the structure that falls out of it."""

    nodes: tuple[str, ...]
    edges: tuple[BorderEdge, ...]
    adjacency: Mapping[str, tuple[str, ...]]
    components: tuple[tuple[str, ...], ...]     # sorted, largest first
    articulation: tuple[str, ...]               # cut vertices — neck hints
    bridges: tuple[tuple[str, str], ...]
    planar_possible: bool

    def neighbours(self, key: str) -> tuple[str, ...]:
        return self.adjacency.get(key, ())

    def hops(self) -> Mapping[str, Mapping[str, int]]:
        """Breadth-first distance between every pair, for inferring from neighbours."""
        out: dict[str, dict[str, int]] = {}
        for start in self.nodes:
            seen = {start: 0}
            wave = [start]
            while wave:
                nxt: list[str] = []
                for here in wave:
                    for step in self.neighbours(here):
                        if step not in seen:
                            seen[step] = seen[here] + 1
                            nxt.append(step)
                wave = sorted(nxt)
            out[start] = seen
        return out

    def is_neck(self, key: str) -> bool:
        return key in self.articulation


def build(nodes: list[str], stated: set[tuple[str, str]]) -> BorderGraph:
    """The graph of who borders whom, from what the writer wrote.

    `stated` is a set of name pairs and is sorted on the way in — a set of strings is
    never iterated in an order that reaches an output, because that order is not stable
    across processes and a map that changed with it would not be reproducible.
    """
    keys = tuple(sorted(set(nodes)))
    known = set(keys)
    pairs = sorted({(min(a, b), max(a, b)) for a, b in stated
                    if a in known and b in known and a != b})

    edges = tuple(BorderEdge(a=a, b=b, hard=True, confidence=1.0,
                             because=f"you wrote that {a} borders {b}")
                  for a, b in pairs)

    adjacency: dict[str, list[str]] = {key: [] for key in keys}
    for edge in edges:
        adjacency[edge.a].append(edge.b)
        adjacency[edge.b].append(edge.a)
    settled: Mapping[str, tuple[str, ...]] = {
        key: tuple(sorted(value)) for key, value in adjacency.items()}

    return BorderGraph(
        nodes=keys, edges=edges, adjacency=settled,
        components=_components(keys, settled),
        articulation=_articulation(keys, settled),
        bridges=_bridges(keys, settled),
        planar_possible=_planar_possible(len(keys), len(edges)),
    )


def _components(keys: tuple[str, ...],
                adjacency: Mapping[str, tuple[str, ...]]) -> tuple[tuple[str, ...], ...]:
    seen: set[str] = set()
    found: list[tuple[str, ...]] = []
    for start in keys:
        if start in seen:
            continue
        piece: list[str] = []
        stack = [start]
        seen.add(start)
        while stack:
            here = stack.pop()
            piece.append(here)
            for step in adjacency[here]:
                if step not in seen:
                    seen.add(step)
                    stack.append(step)
        found.append(tuple(sorted(piece)))
    found.sort(key=lambda piece: (-len(piece), piece))
    return tuple(found)


def _planar_possible(nodes: int, edges: int) -> bool:
    """Euler's necessary condition. Not sufficient, and does not need to be.

    A simple planar graph on n >= 3 vertices has at most 3n - 6 edges. More than that and
    no arrangement on a plane can realise every stated border, so the map knows in
    advance that one will be lost and can say which rather than silently dropping it.
    Testing planarity properly is four hundred lines that tell you only *that* it failed.
    """
    return edges <= 3 * nodes - 6 if nodes >= 3 else True


def _search(keys: tuple[str, ...], adjacency: Mapping[str, tuple[str, ...]]):
    """One iterative depth-first pass, yielding what both Tarjan tests need.

    Returns discovery times, low-link values, and each node's parent in the forest.
    Iterative because a chain of three hundred regions is a perfectly ordinary thing for
    a writer to describe and a recursive walk would end it with a RecursionError.
    """
    when: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    clock = 0
    for root in keys:
        if root in when:
            continue
        parent[root] = None
        stack: list[tuple[str, int]] = [(root, 0)]
        when[root] = low[root] = clock
        clock += 1
        while stack:
            here, index = stack[-1]
            if index < len(adjacency[here]):
                stack[-1] = (here, index + 1)
                step = adjacency[here][index]
                if step not in when:
                    parent[step] = here
                    when[step] = low[step] = clock
                    clock += 1
                    stack.append((step, 0))
                elif step != parent[here]:
                    low[here] = min(low[here], when[step])
            else:
                stack.pop()
                if stack:
                    up = stack[-1][0]
                    low[up] = min(low[up], low[here])
    return when, low, parent


def _articulation(keys: tuple[str, ...],
                  adjacency: Mapping[str, tuple[str, ...]]) -> tuple[str, ...]:
    """The regions whose removal would cut the kingdom in two — its necks."""
    when, low, parent = _search(keys, adjacency)
    children: dict[str, int] = dict.fromkeys(keys, 0)
    for up in parent.values():
        if up is not None:
            children[up] += 1

    cut: set[str] = set()
    for node in keys:
        if parent[node] is None:
            if children[node] > 1:
                cut.add(node)               # a root is a cut vertex with two subtrees
            continue
        for step in adjacency[node]:
            if parent.get(step) == node and low[step] >= when[node]:
                cut.add(node)
    return tuple(sorted(cut))


def _bridges(keys: tuple[str, ...],
             adjacency: Mapping[str, tuple[str, ...]]) -> tuple[tuple[str, str], ...]:
    """The borders that are the only way through — cut one and the map falls apart."""
    when, low, parent = _search(keys, adjacency)
    found: set[tuple[str, str]] = set()
    for node in keys:
        for step in adjacency[node]:
            if parent.get(step) == node and low[step] > when[node]:
                found.add((min(node, step), max(node, step)))
    return tuple(sorted(found))
