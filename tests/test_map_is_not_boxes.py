"""Whether the finished map reads as a picture or as a diagram of a lattice.

A writer looked at the seeded world and said it "kinda looks pretty squarish … like 3
squares mashed together", and nothing in this suite could see it. Every existing shape
measure looks at an internal: `test_borders.py` walks `territory.trace_arcs` over the
partition, and `test_mapgen_invariants.py` walks the plan, gated to steps under a
lattice cell so a legitimately straight road is not called a staircase. Both are right
about what they measure and neither can answer "does the whole thing look drawn on".

So this one reads the `/api/map` payload — the same bytes the browser and the parity
renderer both consume — and measures the picture. The cause found at the time was that
the map drew the writer's four-corner pencil quads instead of the territory they claim:
100% of the seed's own ink lay in long straight runs, and that was 85% of all such ink
on the map. `region_ring_vertices` is the sharp guard against exactly that returning;
the two shares are the blunt ones that would notice a new source of it.

Every assertion prints its own number, because a metric nobody can read is a metric
nobody tunes.
"""

from __future__ import annotations

import math

import goldenlib
import pytest

from fw.core.mapgen import shapes
from fw.core.mapgen.generate import CELL
from fw.core.seed.nyren import seed_nyren
from fw.core.seed.renn import seed_renn

# Tangents rather than angles: an exact comparison, no libm, and the same arithmetic
# the generator is held to. `min/max <= tan θ` is "within θ of an axis"; for two
# segments, `|cross| <= tan θ · dot` with a positive dot is "they turn by less than θ".
TAN_2 = 0.0349207695
TAN_5 = 0.0874886143

# A run this long reads as a deliberate straight line rather than a chord of a curve.
# The seed's quads had edges of 260 to 380 units; a smoothed coast's chords are single
# digits.
LONG_RUN = 100.0


# ---- reading the picture ----------------------------------------------------

def payload(name: str) -> dict:
    """A corpus world, planned and accepted, as `/api/map` serves it.

    The goldens' cache, not a second one: building a corpus world costs a full plan and
    apply, and that suite has already paid for these twelve.
    """
    return goldenlib.map_payload(name)


def rings_of(coordinates) -> list[list[tuple[float, float]]]:
    """Every polyline in a feature's coordinates, whatever nesting it arrived in.

    Lines are a list of points, polygons a list of rings, and either may be wrapped
    one deeper. Recursing on "is the first element a number" reads all four shapes
    without the caller having to know which it has.
    """
    if not coordinates:
        return []
    head = coordinates[0]
    if isinstance(head, (int, float)):
        return []
    if head and isinstance(head[0], (int, float)):
        return [[(float(p[0]), float(p[1])) for p in coordinates]]
    out: list[list[tuple[float, float]]] = []
    for part in coordinates:
        out.extend(rings_of(part))
    return out


def drawn(payload: dict, *, layer: str | None = None) -> list[list[tuple[float, float]]]:
    """The ink the map actually lays down.

    A superseded ring is skipped: it is the writer's own pencil line, which the client
    hides unless asked for it (`MapView` `shown()`), so counting it would measure a
    shape nobody is looking at. What guards against the quad becoming the drawn shape
    again is `test_a_country_is_never_drawn_as_a_quadrilateral`, not this.
    """
    out: list[list[tuple[float, float]]] = []
    for feature in payload["features"]:
        if feature.get("superseded_by") or feature.get("kind") == "point":
            continue
        if layer is not None and feature.get("layer") != layer:
            continue
        for ring in rings_of(feature.get("coordinates")):
            if feature.get("kind") == "polygon" and len(ring) > 2 \
                    and ring[0] != ring[-1]:
                ring = [*ring, ring[0]]        # a ring's closing edge is drawn too
            out.append(ring)
    return out


# ---- the measures -----------------------------------------------------------

def walk(points, step: float = 4.0):
    """Points spaced evenly along a polyline, each with the length it stands for.

    Sampling rather than one midpoint per segment: a share measured on midpoints is a
    share weighted by where the writer happened to click. One 50-unit segment whose
    middle strays near a river counted its whole length; one drawn as five 10-unit
    segments counted a fifth of it. The picture is the same either way, so the measure
    must be too.
    """
    for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
        length = math.hypot(bx - ax, by - ay)
        if length < 1e-9:
            continue
        pieces = max(1, int(length / step))
        for k in range(pieces):
            t = (k + 0.5) / pieces
            yield (ax + (bx - ax) * t, ay + (by - ay) * t), length / pieces


def segments(lines):
    for points in lines:
        for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
            length = math.hypot(bx - ax, by - ay)
            if length > 1e-9:
                yield (bx - ax, by - ay), length


def axis_share(lines) -> float:
    """The share of the map's length running within 5° of a lattice axis."""
    axis = total = 0.0
    for (dx, dy), length in segments(lines):
        total += length
        if min(abs(dx), abs(dy)) <= TAN_5 * max(abs(dx), abs(dy)):
            axis += length
    return axis / total if total else 0.0


def long_straight_share(lines) -> float:
    """The share of the map's length spent in straight runs of 100 units or more.

    A run is maximal: consecutive segments are joined while each turn is under 2°, and
    the run is counted only once it is long enough to read as a ruled line. Measuring
    per segment instead would call every chord of a smooth curve straight.
    """
    straight = total = 0.0
    for points in lines:
        run = 0.0
        previous = None
        for vector, length in segments([points]):
            if previous is not None:
                cross = abs(previous[0] * vector[1] - previous[1] * vector[0])
                dot = previous[0] * vector[0] + previous[1] * vector[1]
                if not (dot > 0 and cross <= TAN_2 * dot):
                    straight += run if run >= LONG_RUN else 0.0
                    run = 0.0
            run += length
            total += length
            previous = vector
        straight += run if run >= LONG_RUN else 0.0
    return straight / total if total else 0.0


def over_water_share(payload: dict) -> float:
    """How much of the region outlines runs across sea rather than along the ground.

    A country's edge is a fact about land. When the map drew the writer's quad, The
    Northmarch ran 34% of its outline over open water and The Salt Reach 15% — two
    rectangles laid over a coastline rather than a border following one.

    "Over water" means more than a lattice cell out, not merely on the wet side of the
    line. A border that follows the coast IS the coast, and the two are simplified
    separately, so a bare inside/outside test called a third of every map's outlines
    wet — measured on Renn, 2165 of the 2611 outside units were within five of the
    shore. That is the two lines disagreeing by a rounding, not a border at sea.
    """
    land = [ring for feature in payload["features"]
            if feature.get("layer") == "land" and feature.get("kind") == "polygon"
            for ring in rings_of(feature.get("coordinates"))]
    if not land:
        return 0.0
    wet = total = 0.0
    for points in drawn(payload, layer="regions"):
        for where, length in walk(points):
            total += length
            if any(shapes.contains(ring, where) for ring in land):
                continue
            if min(near(where, ring) for ring in land) > CELL:
                wet += length
    return wet / total if total else 0.0


# ---- the whole picture, on every corpus world -------------------------------

@pytest.mark.parametrize("name", goldenlib.DRAWPLAN_WORLDS)
class TestTheMapDoesNotReadAsBoxes:
    def test_little_of_it_is_ruled_straight_lines(self, name):
        """Calibrated against the thing it exists to catch. Putting the three
        quadrilaterals back into the Renn payload takes this from 4.5% to 16.1%, so a
        limit of 0.20 — the first number written down for it — would have watched the
        whole complaint go past. The corpus runs 2.2% to 6.7%."""
        share = long_straight_share(drawn(payload(name)))
        assert share < 0.10, (
            f"{share:.1%} of the {name} map's ink lies in straight runs of "
            f"{LONG_RUN:.0f} units or more; the corpus runs 2.2% to 6.7% and the "
            "three quadrilaterals put it at 16.1%")

    def test_it_is_not_drawn_along_the_lattice(self, name):
        """Looser than `test_borders.py`'s 0.15, deliberately: that one measures the
        border arcs alone, and this is the whole picture, where a road that runs due
        east between two towns is straight and correct. Measured across the corpus,
        19.8% (frontier) to 30.9% (empire, which is two dozen regions and therefore
        mostly frontier). The three quads were 90.1% *as shapes*.

        The bluntest of the four, and worth saying so: the experiment that calibrated
        the run measure moves this one only 24.0% to 29.5%, because three rectangles
        are a small share of a map's total ink however loudly they read. It is here to
        notice a whole map drifting onto the lattice, not to notice three boxes —
        `test_a_country_is_never_drawn_as_a_quadrilateral` does that."""
        share = axis_share(drawn(payload(name)))
        assert share < 0.35, (
            f"{share:.1%} of the {name} map's ink runs within 5° of a lattice axis; "
            "the corpus runs 19.8% to 30.9% and the authored quads were 90.1%")

    def test_a_country_is_never_drawn_as_a_quadrilateral(self, name):
        """The sharp one: it fails the instant anything draws the pencil again.

        A ring the writer drew is exempt, and only because it says so on the wire —
        `superseded_by` is set by the server from provenance, not from any style key
        a client could send.
        """
        crude = []
        for feature in payload(name)["features"]:
            if feature.get("layer") != "regions" or feature.get("kind") != "polygon":
                continue
            if feature.get("superseded_by"):
                continue
            for ring in rings_of(feature.get("coordinates")):
                corners = len(ring) - (1 if len(ring) > 1 and ring[0] == ring[-1] else 0)
                if corners <= 6:
                    crude.append(f"{feature.get('name')} ({corners} corners)")
        assert not crude, (
            f"the {name} map draws a country as a box: {', '.join(crude)}")

    def test_a_border_does_not_run_out_to_sea(self, name):
        share = over_water_share(payload(name))
        assert share < 0.12, (
            f"{share:.1%} of the {name} map's region outlines run more than a lattice "
            f"cell out to sea; a country's edge is a claim about land. The corpus runs "
            "0.1% to 5.4%; The Northmarch drawn as a quadrilateral ran 34%")


# ---- the seed's own twelve shapes -------------------------------------------

def authored(world, layer: str) -> dict[str, list[tuple[float, float]]]:
    out = {}
    for geometry in world.geometries(layer=layer):
        entity = world.get_entity(geometry.entity_id)
        for ring in rings_of(geometry.coordinates):
            out[entity.name if entity else geometry.id] = ring
    return out


def near(point, points) -> float:
    """How far a point is from a polyline, measured to its segments, not its corners."""
    best = float("inf")
    px, py = point
    for (ax, ay), (bx, by) in zip(points, points[1:], strict=False):
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        if span < 1e-12:
            best = min(best, math.dist(point, (ax, ay)))
            continue
        t = ((px - ax) * dx + (py - ay) * dy) / span
        t = 0.0 if t < 0 else (1.0 if t > 1 else t)
        best = min(best, math.dist(point, (ax + t * dx, ay + t * dy)))
    return best


def hausdorff(one, other) -> float:
    return max(max(near(p, other) for p in one),
               max(near(p, one) for p in other))


# Two lines that meet converge where they meet, and no guard should ask a writer to
# draw otherwise: the Carth reaches the sea at Orra, which is the port, so the coast
# road and the river share a quay; a tributary and the road that crosses it share the
# confluence town. So the corridor is measured everywhere except within this much of a
# place the two lines genuinely touch.
JOIN_CLEAR = 45.0
TOUCHING = 2.0


def junctions(one, other) -> list[tuple[float, float]]:
    """Where these two lines actually meet — a confluence, a ford, a shared quay.

    The first version of this excused only ENDS the two lines have in common, which was
    too narrow twice over. A road passing through the town where a tributary joins its
    river meets it in the middle of both, and a short tributary converging on a junction
    scored 20% purely because it is short. Neither is a line drawn twice.

    It does not weaken the thing this guards against. Measured on the old example
    world's road, which WAS its river reversed: under this rule it still reads 100%
    alongside, because two lines that never part have junctions everywhere and the
    stretches between them are still the same line.
    """
    met: list[tuple[float, float]] = []
    for point in [*one, *other]:
        if (near(point, one) <= TOUCHING and near(point, other) <= TOUCHING
                and all(math.dist(point, seen) > 1.0 for seen in met)):
            met.append(point)
    return met


def corridor_share(one, other, within: float = 12.0) -> float:
    """How much of a line runs alongside another, close enough to be the same line."""
    met = junctions(one, other)
    shared = total = 0.0
    for where, length in walk(one):
        if any(math.dist(where, place) <= JOIN_CLEAR for place in met):
            continue
        total += length
        if near(where, other) <= within:
            shared += length
    return shared / total if total else 0.0


# Both hand-drawn worlds: the continent this application ships, and the older example
# the suite still uses as a fixture. The guard exists because of what the second one
# used to be, and it has to watch the first, which is the one a writer opens.
SEEDS = {"nyren": seed_nyren, "renn": seed_renn}


@pytest.fixture(scope="module", params=sorted(SEEDS))
def seeded(request):
    made = SEEDS[request.param]()
    yield made
    made.close()


class TestTheSeedWasDrawnByAHand:
    """The example world is authored before any terrain exists, so its shapes cannot
    follow generated ground. They have only to be plausible — which four-corner
    quadrilaterals, and a road that was the river reversed, were not."""

    def test_no_province_is_a_quadrilateral(self, seeded):
        crude = {name: len(ring) for name, ring in authored(seeded, "regions").items()
                 if len(ring) < 12}
        assert not crude, f"drawn with too few corners to be a hand: {crude}"

    def test_a_province_is_not_drawn_along_the_lattice(self, seeded):
        """A share, not a ban. Demanding that no edge at all run within 5° of an axis
        was the first version of this and it was wrong: a hand placing twenty points
        round a province will set two of them level now and then, and forbidding it
        buys jitter rather than plausibility. The three quads were 90.1% axis by
        length; the shapes that replaced them are 12.0%, and 6 of their 52 edges
        happen to run flat."""
        for name, ring in authored(seeded, "regions").items():
            share = axis_share([ring])
            assert share < 0.25, (
                f"{share:.1%} of {name}'s outline runs within 5° of a lattice axis; "
                "the quadrilateral it replaced was 90.1%")

    def test_every_river_wanders(self, seeded):
        """Sinuosity: the length it runs against the distance it covers. The first
        River Renn measured 1.027 — a river drawn as a slightly bent ruler."""
        for name, river in authored(seeded, "waterways").items():
            run = sum(math.dist(a, b) for a, b in zip(river, river[1:], strict=False))
            sinuosity = run / math.dist(river[0], river[-1])
            assert sinuosity > 1.12, f"{name}'s sinuosity is {sinuosity:.3f}"

    def test_no_two_authored_lines_are_the_same_line(self, seeded):
        """The Iron Road *was* the River Renn: reversed, one vertex short, and never
        more than 8.80 units away over a 473-unit run. A road runs through its towns
        and a river runs beside them; they meet at the ford and nowhere else."""
        lines = {**authored(seeded, "waterways"), **authored(seeded, "roads")}
        names = sorted(lines)
        for i, one in enumerate(names):
            for other in names[i + 1:]:
                apart = hausdorff(lines[one], lines[other])
                shared = max(corridor_share(lines[one], lines[other]),
                             corridor_share(lines[other], lines[one]))
                assert apart > 12.0 and shared < 0.18, (
                    f"{one} and {other} are the same line: {apart:.1f} units apart "
                    f"at their furthest, {shared:.0%} of one running alongside the "
                    "other")
