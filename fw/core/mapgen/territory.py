"""Dividing the land between the regions, and drawing the borders that result.

Once the continent exists, every acre of it belongs to somebody. That is not a detail:
a map with unclaimed gaps between its regions renders as grout on a political fill, and
the writer reads it as land nobody has thought about rather than as an artefact.

So the partition is total — every land cell has an owner — and the borders are *traced*
from it rather than drawn independently. That is what makes a shared border shared: the
line between two regions is one line, computed once, and both neighbours get the same
coordinates. Two independently-cast outlines never quite agree, which is the other
reason the old map had water between regions that touch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.mapgen import shapes
from fw.core.mapgen.grid import Field, Grid, stands_above

# A region drawn from fewer cells than this is not a shape, it is a speck.
SMALLEST_REGION = 6.0
MAX_RING_VERTICES = 240


@dataclass
class Partition:
    """Who owns each cell of the land."""

    grid: Grid
    owner: list[list[int]]                    # index into `keys`; -1 is sea
    keys: tuple[str, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def region_at(self, i: int, j: int) -> str | None:
        index = self.owner[j][i]
        return self.keys[index] if 0 <= index < len(self.keys) else None

    def cells_of(self, key: str) -> list[tuple[int, int]]:
        if key not in self.keys:
            return []
        index = self.keys.index(key)
        return [(i, j) for j in range(self.grid.size) for i in range(self.grid.size)
                if self.owner[j][i] == index]

    def share(self, key: str) -> float:
        total = sum(self.counts.values()) or 1
        return round(self.counts.get(key, 0) / total, 3)


def grow(grid: Grid, sea: list[list[bool]], *, anchors: dict[str, tuple[int, int]],
         weights: dict[str, float],
         claimed: dict[str, set[tuple[int, int]]] | None = None,
         cost: Field | None = None,
         seats: dict[str, list[tuple[int, int]]] | None = None) -> Partition:
    """Give every land cell to a region.

    Regions spread at a rate set by how much of the world they hold, so a kingdom of four
    hundred thousand takes more ground than a mountain march of forty — and the border
    falls where the two meet, which is what a border is.

    *What* they spread from is the interesting parameter. An anchor is a made-up point:
    it is where the layout happened to drop a region before there was any ground, and a
    territory grown from one is a weighted Voronoi cell wearing a coastline. Given
    `seats` — the region's own towns — it grows instead from where its people actually
    are, and the shape that comes out is the country those towns hold. A valley on the
    far side of a range from every hall in the march stops belonging to it, which is
    correct, and is a thing an anchor can never express because a point has no shape.

    The rate stays the writer's, not the towns': a region with many small places does not
    thereby annex its neighbours. They decide the shape; the writer decides the size.

    With `cost`, reach is measured in travelling rather than in distance.

    Cells inside a shape the writer drew themselves are given to that region outright:
    their drawing is not a suggestion.
    """
    keys = tuple(sorted(anchors))
    if not keys:
        return Partition(grid=grid, owner=[[-1] * grid.size for _ in range(grid.size)])

    index_of = {key: n for n, key in enumerate(keys)}
    from_where = {key: (seats or {}).get(key) or [anchors[key]] for key in keys}
    owner = grid.claimed_from(
        [(from_where[key], max(weights.get(key, 1.0), 0.2)) for key in keys],
        passable=lambda i, j: not sea[j][i],
        cost=cost,
    )

    for j in range(grid.size):
        for i in range(grid.size):
            if sea[j][i]:
                owner[j][i] = -1

    for key in sorted(claimed or {}):
        for i, j in sorted(claimed[key]):
            if grid.holds(i, j) and not sea[j][i]:
                owner[j][i] = index_of[key]

    stranded = _adopt_the_rest(grid, sea, owner)
    held = {cell for cells in (claimed or {}).values() for cell in cells}
    severed = _no_exclaves(grid, sea, owner, held)

    counts: dict[str, int] = {}
    for row in owner:
        for value in row:
            if value >= 0:
                counts[keys[value]] = counts.get(keys[value], 0) + 1
    notes = []
    if severed:
        notes.append(f"{severed} cells lay in a pocket cut off from the rest of their "
                     f"region and were given to the neighbour around them")
    if stranded:
        notes.append(f"{stranded} cells of land lie off the main body and were given "
                     f"to the nearest coast")
    return Partition(grid=grid, owner=owner, keys=keys, counts=counts, notes=notes)


def _no_exclaves(grid: Grid, sea: list[list[bool]], owner: list[list[int]],
                 held: set[tuple[int, int]]) -> int:
    """Hand any pocket of a region that its own country does not reach to its neighbour.

    Growing a region from one anchor could not produce a pocket: a flood from a single
    point is connected by construction. Growing it from all of its towns can, and does —
    two halls at opposite ends of a march with a neighbour's town between them each claim
    their own country, and the neighbour takes the middle. What comes out is a region in
    two pieces with somebody else's land in the gap, and on a political fill that reads as
    a mistake whether or not it is one.

    The part that has to be got right is *what may receive*. Deciding every move from one
    snapshot and applying them together does not work and does not even fail quietly: two
    pockets facing each other across a border are each handed to the other, they swap, and
    the next pass swaps them back — measured at thirty-five cells changing hands on every
    pass, for ever. So a pocket may only be given to a piece that is *staying put*: each
    region's largest piece, which by definition is not going anywhere. Nothing that is
    itself loose can receive, so nothing can be handed back, and each round strictly
    reduces the loose ground until there is none.

    Islands are left alone throughout. A piece with no land neighbour of any other colour
    is off the coast, and offshore land belonging to the nearest mainland is the map
    working — the same reasoning as `_adopt_the_rest`. So is a pocket inside a shape the
    writer drew: they may well have meant it, and their drawing is not a suggestion.
    """
    moved = 0
    for _round in range(len(owner) + 1):
        pieces = _pieces(grid, sea, owner)
        staying: dict[int, int] = {}
        for index, piece in enumerate(pieces):
            mine = piece.owner
            if mine not in staying or len(piece.cells) > len(pieces[staying[mine]].cells):
                staying[mine] = index

        loose = [index for index, piece in enumerate(pieces)
                 if index != staying.get(piece.owner) and piece.touching
                 and not any(cell in held for cell in piece.cells)]
        if not loose:
            return moved

        settled = {index for index in staying.values()}
        shifted = 0
        for index in loose:
            piece = pieces[index]
            # Only to a piece that is staying put, and only to one it actually touches,
            # or the region it joins would be in two pieces itself.
            options = {other: edge for other, edge in piece.touching.items()
                       if other in settled}
            if not options:
                continue
            winner = max(sorted(options), key=lambda other: options[other])
            colour = pieces[winner].owner
            for i, j in piece.cells:
                owner[j][i] = colour
            shifted += len(piece.cells)
        if shifted:
            moved += shifted
            continue
        # What is left touches nothing that is staying put, which on a continent means
        # one thing: an island, shared out cell by cell by the flood that gave the
        # offshore land away, and now held in pieces by two regions who both have to
        # cross water to reach it. An island is one place. It goes to whoever holds
        # most of it.
        moved += _whole_islands(pieces, staying, owner, held)
        return moved
    return moved


def _whole_islands(pieces: list[_Piece], staying: dict[int, int],
                   owner: list[list[int]], held: set[tuple[int, int]]) -> int:
    """Give each landmass with no mainland in it to the region holding most of it."""
    settled = set(staying.values())
    group: dict[int, int] = {}                 # piece index -> group root

    def root(index: int) -> int:
        while group.get(index, index) != index:
            index = group[index]
        return index

    for index, piece in enumerate(pieces):
        group.setdefault(index, index)
        for other in piece.touching:
            group.setdefault(other, other)
            a, b = root(index), root(other)
            if a != b:
                group[a] = b

    together: dict[int, list[int]] = {}
    for index in range(len(pieces)):
        together.setdefault(root(index), []).append(index)

    moved = 0
    for members in together.values():
        if any(index in settled for index in members):
            continue                            # part of the mainland
        if any(cell in held for index in members for cell in pieces[index].cells):
            continue                            # the writer drew it
        share: dict[int, int] = {}
        for index in members:
            share[pieces[index].owner] = (share.get(pieces[index].owner, 0)
                                          + len(pieces[index].cells))
        if len(share) < 2:
            continue
        winner = max(sorted(share), key=lambda key: share[key])
        for index in members:
            if pieces[index].owner == winner:
                continue
            for i, j in pieces[index].cells:
                owner[j][i] = winner
            moved += len(pieces[index].cells)
    return moved


@dataclass(frozen=True)
class _Piece:
    """One connected run of land under one owner, and who is against its edge."""

    owner: int
    cells: tuple[tuple[int, int], ...]
    touching: dict[int, int]               # piece index -> cells of shared edge


def _pieces(grid: Grid, sea: list[list[bool]],
            owner: list[list[int]]) -> list[_Piece]:
    """Every connected piece of land, by owner, and which pieces border which."""
    size = grid.size
    label = [[-1] * size for _ in range(size)]
    cells_of: list[list[tuple[int, int]]] = []
    owners: list[int] = []
    for j in range(size):
        for i in range(size):
            if sea[j][i] or owner[j][i] < 0 or label[j][i] >= 0:
                continue
            mine = owner[j][i]
            index = len(cells_of)
            label[j][i] = index
            cells: list[tuple[int, int]] = []
            stack = [(i, j)]
            while stack:
                a, b = stack.pop()
                cells.append((a, b))
                for na, nb in grid.neighbours(a, b, diagonal=False):
                    if (not sea[nb][na] and owner[nb][na] == mine
                            and label[nb][na] < 0):
                        label[nb][na] = index
                        stack.append((na, nb))
            cells_of.append(cells)
            owners.append(mine)

    touching: list[dict[int, int]] = [{} for _ in cells_of]
    for index, cells in enumerate(cells_of):
        for a, b in cells:
            for na, nb in grid.neighbours(a, b, diagonal=False):
                if sea[nb][na] or owner[nb][na] < 0:
                    continue
                other = label[nb][na]
                if other != index:
                    touching[index][other] = touching[index].get(other, 0) + 1
    return [_Piece(owner=owners[index], cells=tuple(cells_of[index]),
                   touching=touching[index])
            for index in range(len(cells_of))]


def _adopt_the_rest(grid: Grid, sea: list[list[bool]],
                    owner: list[list[int]]) -> int:
    """Give the nearest region any land the flood could not reach.

    An island off a coast, or a spit joined to the mainland only at a corner. Leaving it
    unowned would be a third state for a cell to be in, and every reader of the map
    would have to handle it: on a political fill it renders as a hole. Whose island it
    is, is a question the writer can answer later; that it is *somebody's* is the safe
    assumption, and a real map makes it too.
    """
    frontier = [(i, j) for j in range(grid.size) for i in range(grid.size)
                if owner[j][i] >= 0]
    stranded = sum(1 for j in range(grid.size) for i in range(grid.size)
                   if not sea[j][i] and owner[j][i] < 0)
    if not frontier or not stranded:
        return 0
    while frontier:
        nxt: list[tuple[int, int]] = []
        for i, j in frontier:
            mine = owner[j][i]
            for ni, nj in grid.neighbours(i, j):
                if not sea[nj][ni] and owner[nj][ni] < 0:
                    owner[nj][ni] = mine
                    nxt.append((ni, nj))
        frontier = sorted(nxt)

    # An island the walk could not reach, because reaching it means crossing water.
    # A second flood that ignores the shoreline answers "whose coast is it off", which
    # is how an island gets claimed in the world as well as on the map.
    remaining = [(i, j) for j in range(grid.size) for i in range(grid.size)
                 if not sea[j][i] and owner[j][i] < 0]
    if remaining:
        reach = {(i, j): owner[j][i] for j in range(grid.size)
                 for i in range(grid.size) if owner[j][i] >= 0}
        wave = sorted(reach)
        while wave and any(owner[j][i] < 0 for i, j in remaining):
            nxt = []
            for i, j in wave:
                mine = reach[(i, j)]
                for neighbour in grid.neighbours(i, j):
                    if neighbour in reach:
                        continue
                    reach[neighbour] = mine
                    ni, nj = neighbour
                    if not sea[nj][ni]:
                        owner[nj][ni] = mine
                    nxt.append(neighbour)
            wave = sorted(nxt)
    return stranded


def outline(partition: Partition, key: str) -> list[list[list[float]]]:
    """A region's shape, traced from the ground it actually holds.

    Contoured from the ownership field rather than cast as rays, so the shape can be as
    concave as the territory is — a region that wraps around a bay looks like one — and
    so the edge it shares with a neighbour is the same edge on both maps.
    """
    if key not in partition.keys:
        return []
    grid = partition.grid
    index = partition.keys.index(key)
    mask = grid.filled(0.0)
    for j in range(grid.size):
        for i in range(grid.size):
            if partition.owner[j][i] == index:
                mask[j][i] = 1.0

    rings: list[list[list[float]]] = []
    for ring, encloses in shapes.outlines(grid.blurred(mask), 0.5,
                                          smallest=SMALLEST_REGION,
                                          most=MAX_RING_VERTICES):
        if not encloses:
            continue                    # a hole in a region is another region, not a gap
        rings.append(shapes.closed(grid.to_world(ring)))
    return rings


def audit(partition: Partition, borders: set[tuple[str, str]]) -> list[str]:
    """Which of the writer's stated borders the map failed to realise.

    Four regions can all claim to border each other, and a plane cannot always oblige.
    Saying which border was lost is more use than either failing or pretending.
    """
    touching: set[tuple[str, str]] = set()
    grid = partition.grid
    for j in range(grid.size):
        for i in range(grid.size):
            mine = partition.owner[j][i]
            if mine < 0:
                continue
            for ni, nj in grid.neighbours(i, j, diagonal=False):
                theirs = partition.owner[nj][ni]
                if theirs >= 0 and theirs != mine:
                    a, b = partition.keys[mine], partition.keys[theirs]
                    touching.add((min(a, b), max(a, b)))
    stated = {(min(a, b), max(a, b)) for a, b in borders
              if a in partition.keys and b in partition.keys and a != b}
    return sorted(f"{a} and {b}" for a, b in stated - touching)


# ---- what a border actually runs along --------------------------------------
#
# A frontier is the most consequential thing on a political map and the least often
# stated. Whether two countries are separated by a range or by forty miles of wheat is
# the difference between a quiet march and the place every war starts, and a writer with
# a coloured map in front of them cannot see which they have.

# What counts as high ground, as a quantile of how much every acre of *this* world
# rises over its own surroundings. A fixed figure cannot do it: at the one first tried,
# 0.02, ninety per cent of the land qualified and every border on every map came back
# running along a crest, which is the answer a measurement gives when it is not
# measuring. Asking the world what high means for it costs one sort.
CREST_QUANTILE = 0.75
# What share of the world's flow makes a watercourse a thing armies have to bridge.
BORDER_RIVER = 0.10
BORDER_MARSH = 0.25
# A frontier below this many cells is a corner where three regions meet, not a border.
SHORTEST_FRONTIER = 4
# Above this share of open ground, the frontier is worth telling the writer about.
MOSTLY_OPEN = 0.6


@dataclass(frozen=True)
class Frontier:
    """Where two regions meet, and what — if anything — is in the way."""

    between: tuple[str, str]
    cells: tuple[tuple[int, int], ...]
    crest: float                      # share of it running along high ground
    water: float                      # share of it on a river
    fen: float                        # share of it in marsh
    coast: float                      # share of it against the sea

    @property
    def length(self) -> int:
        return len(self.cells)

    @property
    def open_country(self) -> float:
        """The share of it that nothing defends."""
        return max(0.0, 1.0 - self.crest - self.water - self.fen - self.coast)

    @property
    def runs_along(self) -> str:
        """The one thing it mostly follows, in the words a person would use."""
        best, share = "open country", self.open_country
        for name, value in (("high ground", self.crest), ("a river", self.water),
                            ("marsh", self.fen), ("the shore", self.coast)):
            if value > share:
                best, share = name, value
        return best


def frontiers(partition: Partition, *, elevation: Field, flow: Field,
              marsh: Field, sea: list[list[bool]],
              biggest_flow: float) -> list[Frontier]:
    """Every border on the map, and the country it runs through.

    Measured rather than intended. The generator does not put borders on ridges — it
    cannot, and `Grid.claimed_from` says why — so what a border turned out to follow is
    a fact about the world that has to be gone and looked at.
    """
    grid = partition.grid
    size = grid.size
    land = [(i, j) for j in range(size) for i in range(size) if not sea[j][i]]
    if not land:
        return []
    rises = sorted(stands_above(grid, elevation, sea, i, j) for i, j in land)
    crest_from = rises[min(len(rises) - 1, int(len(rises) * CREST_QUANTILE))]

    shared: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for j in range(size):
        for i in range(size):
            mine = partition.owner[j][i]
            if mine < 0:
                continue
            for ni, nj in grid.neighbours(i, j, diagonal=False):
                theirs = partition.owner[nj][ni]
                if theirs >= 0 and theirs != mine:
                    pair = (min(mine, theirs), max(mine, theirs))
                    shared.setdefault(pair, []).append((i, j))

    out: list[Frontier] = []
    for pair in sorted(shared):
        cells = sorted(set(shared[pair]))
        if len(cells) < SHORTEST_FRONTIER:
            continue
        crest = water = fen = shore = 0
        for i, j in cells:
            if _touches_sea(grid, sea, i, j):
                shore += 1
            elif flow[j][i] / biggest_flow >= BORDER_RIVER:
                water += 1
            elif marsh[j][i] >= BORDER_MARSH:
                fen += 1
            elif stands_above(grid, elevation, sea, i, j) >= crest_from:
                crest += 1
        count = float(len(cells))
        out.append(Frontier(
            between=(partition.keys[pair[0]], partition.keys[pair[1]]),
            cells=tuple(cells), crest=crest / count, water=water / count,
            fen=fen / count, coast=shore / count))
    out.sort(key=lambda f: (-f.length, f.between))
    return out


def _touches_sea(grid: Grid, sea: list[list[bool]], i: int, j: int) -> bool:
    return any(sea[nj][ni] for ni, nj in grid.neighbours(i, j))
