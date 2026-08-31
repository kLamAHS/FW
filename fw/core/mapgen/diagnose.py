"""Geography a reader would question, said out loud (V2 §44).

A map can be internally consistent and still make a novelist's copy-editor reach for a
pencil: a city with no water, a port no road serves, a river that appears to climb. Each
check here reads the same in-plan fields the stages themselves worked from, and says
what it noticed as an `implausible` *note* — never an error, because the writer may know
exactly why: the city drinks from wells, the port died a century ago, and telling them
so twice would be the map arguing with its author (§66). Most of these should never
fire on generated geometry at all; when one does, it has caught either the generator
or the world, and both are worth one sentence in the proposal.
"""

from __future__ import annotations

from fw.core.mapgen.findings import Finding, note

# The ranks whose isolation or thirst a reader would question. A hamlet with no road
# is a hamlet; a city with none is a plot hole.
MAJOR = ("capital", "city", "port", "harbour", "harbor", "market town")
# How close a town must stand to running or standing water before nobody asks where
# the water comes from, in lattice cells.
NEAR_WATER = 4
STANDING_WATER = 0.22               # marsh above this is a mere's ground (see shade)
# What counts as a stream worth drinking from, as a share of the biggest river's flow.
# Not the *drawable* channel set — that is the top 2.2% of flow, the rivers worth ink,
# and measured on the example world most towns stand six to twelve cells from one
# while sitting directly on a real stream below the drawing cut.
STREAM = 0.01
# Ground a road wants a causeway over, and how many cells of it in a row earn the note.
FEN = 0.45
CAUSEWAY = 3
# How far a tower said to watch the march may stand from any border it could watch.
WATCH_REACH = 8
# How much a river may "climb" end to end before it is called on it — quantisation and
# smoothing jitter, not a grade.
UPHILL = 0.02


def study(generator, drafts, known) -> list[Finding]:
    """Every plausibility question the plan can answer about itself."""
    out: list[Finding] = []
    out.extend(_uphill_rivers(generator, drafts))
    out.extend(_waterless_towns(generator, known))
    out.extend(_isolated_towns(generator, drafts, known))
    out.extend(_fen_roads(generator, drafts))
    out.extend(_unwatched_towers(generator))
    return out


def _uphill_rivers(generator, drafts) -> list[Finding]:
    """A river that ends higher than it began. Pure self-check: erosion guarantees
    the receiver tree drains, so this firing means a stage bent a course after."""
    out = []
    for draft in drafts:
        if draft.kind != "river":
            continue
        for shape in draft.shapes:
            if shape.role != "spine" or len(shape.coordinates) < 2:
                continue
            (x0, y0), (x1, y1) = shape.coordinates[0], shape.coordinates[-1]
            i0, j0 = generator._cell_of(float(x0), float(y0))
            i1, j1 = generator._cell_of(float(x1), float(y1))
            rise = generator.elevation[j1][i1] - generator.elevation[j0][i0]
            if rise > UPHILL:
                out.append(note(
                    "implausible",
                    f"a river of order {draft.detail.get('strahler', '?')} ends "
                    f"{rise:.2f} higher than it began — water does not do that"))
    return out


def _waterless_towns(generator, known) -> list[Finding]:
    from fw.core.mapgen.generate import GRID

    flow = generator.erosion.flow
    marsh = generator.vegetation.marsh
    biggest = max((flow[j][i] for j in range(GRID) for i in range(GRID)
                   if not generator.sea[j][i]), default=0.0)
    if biggest <= 0.0:
        return []

    def wet(i: int, j: int) -> bool:
        return (generator.sea[j][i] or marsh[j][i] >= STANDING_WATER
                or flow[j][i] / biggest >= STREAM)

    out = []
    for place in known:
        ci, cj = generator._cell_of(place.x, place.y)
        if any(wet(i, j)
               for dj in range(-NEAR_WATER, NEAR_WATER + 1)
               for di in range(-NEAR_WATER, NEAR_WATER + 1)
               for i, j in ((ci + di, cj + dj),)
               if 0 <= i < GRID and 0 <= j < GRID):
            continue
        who = place.name or "a proposed town"
        out.append(note(
            "implausible",
            f"{who} stands more than a morning's walk from any river, mere or "
            f"shore — a reader will ask what it drinks"))
    return out


def _isolated_towns(generator, drafts, known) -> list[Finding]:
    """A major town no road or lane reaches. The network joins everything it can
    reach over land, so what this actually catches is a ranked place cut off by
    water or impassable ground with no sea lane picking it up."""
    if len(known) < 2:
        return []
    network = generator.road_network(known)
    joined = {index for route in network.routes for index in route.joins}
    sailed: set[str] = set()
    for draft in drafts:
        if draft.kind != "lane":
            continue
        sailed.add(str(draft.detail.get("lands_at") or ""))
        sailed.update(str(name) for name in draft.detail.get("between", ()))
    out = []
    for index, place in enumerate(known):
        if place.rank.lower() not in MAJOR:
            continue
        if index in joined or (place.name and place.name in sailed):
            continue
        who = place.name or "a proposed town"
        out.append(note(
            "implausible",
            f"{who} ranks as a {place.rank} and neither road nor lane reaches "
            f"it — nobody is carrying its trade"))
    return out


def _fen_roads(generator, drafts) -> list[Finding]:
    marsh = generator.vegetation.marsh
    out = []
    for draft in drafts:
        if draft.kind != "road":
            continue
        worst = 0
        for shape in draft.shapes:
            if shape.kind != "line":
                continue
            run = 0
            for x, y in shape.coordinates:
                i, j = generator._cell_of(float(x), float(y))
                if not generator.sea[j][i] and marsh[j][i] >= FEN:
                    run += 1
                    worst = max(worst, run)
                else:
                    run = 0
        if worst >= CAUSEWAY:
            grade = draft.detail.get("grade", "road")
            out.append(note(
                "implausible",
                f"a {grade} runs {worst} cells through open fen with nothing "
                f"said about a causeway"))
    return out


def _unwatched_towers(generator) -> list[Finding]:
    """A tower said to watch the march, with no march anywhere near it."""
    if generator.holds is None or not generator.frontiers:
        return []
    border = {cell for frontier in generator.frontiers for cell in frontier.cells}
    out = []
    for place in generator.holds.sites:
        if place.watches != "march":
            continue
        i, j = place.cell
        gap = min(max(abs(i - a), abs(j - b)) for a, b in border)
        if gap > WATCH_REACH:
            where = generator.owner[j][i]
            region = (generator.profiles[where].name
                      if where in generator.profiles else "its region")
            out.append(note(
                "implausible",
                f"a {place.rank} in {region} is said to watch the march, and "
                f"stands {gap} cells from any border"))
    return out
