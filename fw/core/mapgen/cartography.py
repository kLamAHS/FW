"""Turning a pile of geometry into a map somebody can read.

Everything before this module answers *where things are*. This one answers *how the
page is drawn*: what size each town's dot is, where every name sits, which colour role
each shape takes, what the legend has to say, and how far the view has to reach to hold
all of it. It is the last stage of C11 and it belongs on the server for three reasons.

**Labels are a solved layout problem, not a styling detail.** Which names fit is decided
by measuring text against shapes, and two clients doing that independently would label
the same map two different ways. Doing it once means the picture the writer exports is
the picture they were looking at.

**The client should not have to know what a `keep` is.** A hamlet, a city and a castle
were all one dot because the rank was on the geometry's style and nothing turned it into
a size. The hierarchy the generator worked out was invisible in the thing that shows it.

**Colour is a role here and a value in the stylesheet.** Nothing in this package emits a
hex. `#6d6a63` in Python is a colour that cannot follow a theme, cannot be overridden by
a writer who finds it muddy, and has to be duplicated in the client for the legend to
match. A role — `terrain-mountain` — is one name that both ends agree on.

The input is the same list of feature dictionaries `GET /api/map` already returns, so
this reads no world and holds no database handle, and it works on a plan as readily as
on an accepted map.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fw.core.mapgen import labels, shapes

Point = tuple[float, float]

# §11's four authorities, plus the claim that disputes them. The map colours by one at a
# time and says which — collapsing them to "the owner" is the distinction the brief
# calls its sharpest, thrown away in the one place it would be most visible.
MODES = ("legally_owns", "administers", "occupies", "taxes", "claims")
MODE_WORDS = {
    "legally_owns": "Owned in law by",
    "administers": "Administered by",
    "occupies": "Militarily occupied by",
    "taxes": "Taxed by",
    "claims": "Claimed by",
}

# Every colour the map uses, as a role. The stylesheet gives each one a light value and
# a dark one; Python never sees either. `terrain-*` are the political fill's ground
# tints, `cover-*` the vegetation washes, and the rest are the furniture.
ROLES = (
    "land", "water", "sea", "border", "coastline", "ridge", "waterway", "road",
    "highway", "track", "settlement", "castle", "port", "contested", "label",
    # The paper colour every label is haloed with. In the list so the theme guard
    # covers the one colour the whole map's text depends on.
    "halo",
    "label-water", "label-region", "label-relief",
    "terrain-mountain", "terrain-glacier", "terrain-hills", "terrain-highland",
    "terrain-forest", "terrain-plain", "terrain-farmland", "terrain-steppe",
    "terrain-desert", "terrain-marsh", "terrain-coast", "terrain-ocean",
    "cover-forest", "cover-marsh", "cover-downs", "cover-moor", "cover-waste",
    "cover-ice",
    # Holders are numbered rather than named: a house has no colour of its own, it has
    # a place in the map's palette, and which place has to be the same on every layer.
    *(f"holder-{n}" for n in range(8)),
)

TERRAIN_ROLES = {
    "mountain": "terrain-mountain", "glacier": "terrain-glacier",
    "hills": "terrain-hills", "highland": "terrain-highland",
    "forest": "terrain-forest", "plain": "terrain-plain",
    "farmland": "terrain-farmland", "steppe": "terrain-steppe",
    "desert": "terrain-desert", "marsh": "terrain-marsh",
    "coast": "terrain-coast", "ocean": "terrain-ocean",
}

COVER_ROLES = {"forest": "cover-forest", "marsh": "cover-marsh",
               "downs": "cover-downs", "moor": "cover-moor",
               "waste": "cover-waste", "ice": "cover-ice"}

HOLDER_ROLES = tuple(f"holder-{n}" for n in range(8))

# What a place of each rank is drawn as, and how big. A city is not a hamlet and a
# castle is not a small town; the generator has known the difference for two phases and
# the picture has not. Radius in world units at the map's own scale.
RANKS: dict[str, tuple[str, float]] = {
    # rank -> (icon, radius). Label tiers live in TYPE, which is where the solver
    # reads them; a third column here was carried for two phases and read by nothing.
    "capital": ("star", 8.5),
    "city": ("ring", 7.0),
    "port": ("anchor", 6.5),
    "market town": ("ring", 6.0),
    "town": ("disc", 5.5),
    "fortress": ("keep", 6.5),
    "village": ("disc", 4.2),
    "hamlet": ("dot", 3.4),
    "castle": ("keep", 6.0),
    "keep": ("keep", 5.0),
    "tower": ("tower", 4.2),
}
DEFAULT_RANK = ("disc", 5.0)

# How big each kind of name is set, in world units, and how hard the map tries to fit
# it. Tier 0 is placed before anything else can take the room.
TYPE: dict[str, tuple[float, int]] = {
    "sea": (26.0, 0),
    "region": (22.0, 0),
    # A continent is named after the regions on it. Bigger and sparser, but placed
    # second: a country whose name will not fit because the landmass took the room is a
    # worse map than one where the continent goes unnamed.
    "landmass": (24.0, 1),
    "capital": (15.0, 0),
    "range": (14.0, 1),
    "city": (13.0, 1),
    "town": (11.5, 1),
    "river": (11.0, 1),
    "feature": (11.0, 2),
    "village": (10.0, 2),
    "castle": (10.0, 2),
    "road": (9.5, 2),
}

# What a line on each layer is, so a brook drawn on the natural-features layer is not
# labelled as though it were a road. Anything else is a natural feature, which is the
# safe answer: it gets a feature's size and a feature's tier.
LINE_KINDS = {"waterways": "river", "relief": "range", "roads": "road",
              "features": "feature"}
LINE_ROLES = {"river": "label-water", "range": "label-relief", "road": "label",
              "feature": "label"}

# How much room to leave round everything, so a name at the edge is not cut in half.
MARGIN = 70.0


@dataclass(frozen=True)
class Bounds:
    x: float
    y: float
    width: float
    height: float

    def as_dict(self) -> dict:
        return {"x": round(self.x, 1), "y": round(self.y, 1),
                "width": round(self.width, 1), "height": round(self.height, 1)}


@dataclass(frozen=True)
class Icon:
    """One place, drawn as what it is."""

    key: str
    entity_id: str
    name: str
    shape: str                  # star | ring | disc | dot | keep | tower | anchor
    rank: str
    x: float
    y: float
    radius: float
    role: str
    holder_role: str = ""       # the political fill's colour for whoever holds it
    holder_name: str = ""
    contested: bool = False

    def as_dict(self) -> dict:
        return {"key": self.key, "entity_id": self.entity_id, "name": self.name,
                "shape": self.shape, "rank": self.rank,
                "x": round(self.x, 1), "y": round(self.y, 1),
                "radius": round(self.radius, 1), "role": self.role,
                "holder_role": self.holder_role, "holder_name": self.holder_name,
                "contested": self.contested}


@dataclass(frozen=True)
class LegendEntry:
    """One line of the key, so the map explains its own vocabulary."""

    key: str
    label: str
    role: str
    swatch: str                 # fill | line | dashed | star | ring | disc | dot | keep
    note: str = ""
    entity_id: str = ""

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "role": self.role,
                "swatch": self.swatch, "note": self.note,
                "entity_id": self.entity_id}


@dataclass(frozen=True)
class DrawPlan:
    """Everything about how the map is drawn, worked out once, on the server."""

    bounds: Bounds
    mode: str
    labels: tuple[labels.Placed, ...] = ()
    icons: tuple[Icon, ...] = ()
    legend: tuple[LegendEntry, ...] = ()
    holders: Mapping[str, str] = field(default_factory=dict)   # entity id -> role
    unlabelled: tuple[labels.Dropped, ...] = ()

    def as_dict(self, *, debug: bool = False) -> dict:
        return {
            "bounds": self.bounds.as_dict(),
            "mode": self.mode,
            "labels": [label.as_dict(debug=debug) for label in self.labels],
            "icons": [icon.as_dict() for icon in self.icons],
            "legend": [entry.as_dict() for entry in self.legend],
            "holders": dict(self.holders),
            "unlabelled": [gone.as_dict() for gone in self.unlabelled],
        }


def terrain_role(kind: str) -> str:
    return TERRAIN_ROLES.get(kind, "terrain-plain")


def cover_role(kind: str) -> str:
    return COVER_ROLES.get(kind, "cover-downs")


def from_plan(plan, *, world_name: str = "") -> DrawPlan:
    """The same drawing, for a map that does not exist yet (§66).

    A proposal the writer is deciding on has to be labelled the same way the accepted
    map will be, or they are choosing between a picture and a different picture.
    """
    return draw([{
        "id": feature.id,
        "entity_id": (feature.subject.entity_id if feature.subject else "")
                     or feature.id,
        "name": feature.name,
        "type_key": feature.subject.type_key if feature.subject else "",
        "kind": shape.kind,
        "coordinates": shape.coordinates,
        "layer": shape.layer,
        "style": shape.style,
        "approximate": shape.approximate,
        "generated": True,
        "control": {},
    } for feature in plan.features for shape in feature.shapes],
        world_name=world_name)


def draw(features: Sequence[Mapping[str, Any]], *, mode: str = "legally_owns",
         ground: Sequence[float] | None = None, label: bool = True,
         world_name: str = "") -> DrawPlan:
    """Work out how to draw a map, from the same features the API already returns.

    `ground` is the rendered relief's own extent when there is one, so the view holds
    the lit surface as well as the shapes drawn on it. `world_name` is what the world is
    called, which the mainland is named after and therefore does not repeat.
    """
    if mode not in MODES:
        mode = "legally_owns"
    bounds = _bounds(features, ground)
    holders = _holder_roles(features)
    icons = _icons(features, mode, holders)
    placed: tuple[labels.Placed, ...] = ()
    missed: tuple[labels.Dropped, ...] = ()
    if label:
        placed, missed = labels.solve(
            _wanted(features, icons, bounds, world_name))
    return DrawPlan(bounds=bounds, mode=mode, labels=placed, icons=tuple(icons),
                    legend=_legend(features, icons, mode, holders), holders=holders,
                    unlabelled=missed)


# ---- the frame -------------------------------------------------------------

def _bounds(features: Sequence[Mapping[str, Any]],
            ground: Sequence[float] | None) -> Bounds:
    """A view that holds everything, worked out here rather than in the browser.

    The client used to do this by spreading every coordinate on the map into
    `Math.min(...xs)`. Past about sixty-five thousand arguments that throws a
    `RangeError`, and the map goes blank with nothing in the console to say why — which
    is exactly the size a continent with rivers and roads reaches.
    """
    xs: list[float] = []
    ys: list[float] = []
    if ground and len(ground) >= 4:
        xs.extend((float(ground[0]), float(ground[2])))
        ys.extend((float(ground[1]), float(ground[3])))
    for feature in features:
        for x, y in _points(feature.get("coordinates")):
            xs.append(x)
            ys.append(y)
    if not xs:
        return Bounds(0.0, 0.0, 900.0, 800.0)
    x = min(xs) - MARGIN
    y = min(ys) - MARGIN
    return Bounds(x=x, y=y, width=max(xs) - x + MARGIN, height=max(ys) - y + MARGIN)


def _points(node: Any) -> Iterable[Point]:
    """Every coordinate pair in a nest of rings, lines or a bare point."""
    if not isinstance(node, (list, tuple)):
        return
    if len(node) == 2 and all(isinstance(v, (int, float)) for v in node):
        yield (float(node[0]), float(node[1]))
        return
    for child in node:
        yield from _points(child)


# ---- who is coloured what --------------------------------------------------

def _holder_roles(features: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """A palette slot per holder, assigned in a stated order.

    By name, not by whichever feature happened to be read first: the same house has to
    be the same colour on a map drawn today and one drawn after the writer adds a town.
    """
    seen: dict[str, str] = {}
    for feature in features:
        for holders in (feature.get("control") or {}).values():
            for holder in holders:
                seen.setdefault(str(holder.get("id")), str(holder.get("name") or ""))
    ordered = sorted(seen.items(), key=lambda pair: (pair[1], pair[0]))
    return {entity_id: HOLDER_ROLES[n % len(HOLDER_ROLES)]
            for n, (entity_id, _name) in enumerate(ordered)}


def _holder_of(feature: Mapping[str, Any], mode: str) -> Mapping[str, Any] | None:
    holders = (feature.get("control") or {}).get(mode) or []
    return holders[0] if holders else None


# ---- what each place is drawn as -------------------------------------------

def _icons(features: Sequence[Mapping[str, Any]], mode: str,
           holders: Mapping[str, str]) -> list[Icon]:
    out: list[Icon] = []
    for feature in features:
        if feature.get("kind") != "point":
            continue
        coordinates = feature.get("coordinates") or []
        if len(coordinates) < 2:
            continue
        style = feature.get("style") or {}
        rank = str(style.get("rank") or _rank_of(feature))
        shape, radius = RANKS.get(rank.lower(), DEFAULT_RANK)
        holder = _holder_of(feature, mode)
        out.append(Icon(
            key=str(feature.get("id")),
            entity_id=str(feature.get("entity_id") or ""),
            name=str(feature.get("name") or ""),
            shape=shape, rank=rank,
            x=float(coordinates[0]), y=float(coordinates[1]), radius=radius,
            role="castle" if _is_castle(feature) else "settlement",
            holder_role=holders.get(str(holder.get("id")), "") if holder else "",
            holder_name=str(holder.get("name")) if holder else "",
            contested=bool((feature.get("control") or {}).get("claims")),
        ))
    out.sort(key=lambda icon: (icon.name, icon.key))
    return out


def _is_castle(feature: Mapping[str, Any]) -> bool:
    return (feature.get("layer") == "castles"
            or feature.get("type_key") == "holding")


def _rank_of(feature: Mapping[str, Any]) -> str:
    return "castle" if _is_castle(feature) else "town"


# ---- what wants a name -----------------------------------------------------

def _wanted(features: Sequence[Mapping[str, Any]], icons: Sequence[Icon],
            bounds: Bounds, world_name: str = "") -> list[labels.Wanted]:
    """Everything on the map that has a name, and how badly it wants to show it.

    One name per *thing*, not per shape. A river reaches the sea as four segments and a
    kingdom's generated roads all belong to one entity: labelling geometry rather than
    entities wrote "Generated roads" across the map nine times and pushed out the towns
    that could not find room around them.
    """
    want: list[labels.Wanted] = []
    by_id = {icon.key: icon for icon in icons}
    land: list[tuple[Point, ...]] = []

    for feature in features:
        if feature.get("kind") == "polygon" and feature.get("layer") == "land":
            for ring in feature.get("coordinates") or []:
                land.append(tuple(_points(ring)))

    for feature in _one_per_thing(features):
        name = str(feature.get("name") or "").strip()
        kind = feature.get("kind")
        layer = str(feature.get("layer") or "")
        if not name:
            continue
        key = str(feature.get("id"))
        if kind == "polygon":
            rings = feature.get("coordinates") or []
            if not rings:
                continue
            ring = tuple(_points(rings[0]))
            if len(ring) < 4:
                continue
            style_kind = ("landmass" if layer == "land"
                          else "region" if layer == "regions" else "feature")
            # The mainland is named after the world, and the map is *of* that world:
            # writing it across the middle puts a fourth country-sized name beside the
            # three countries, and a reader takes it for a fourth country. An island
            # with a name of its own still gets one.
            if style_kind == "landmass" and name == world_name:
                continue
            size, tier = TYPE[style_kind]
            want.append(labels.Wanted(
                key=key, text=name, kind=style_kind, tier=_tier(tier, feature),
                role=("label-region" if style_kind in ("region", "landmass")
                      else "label"),
                size=size, ring=ring, weight=shapes.area(ring)))
        elif kind == "line":
            line = tuple(_points(feature.get("coordinates")))
            if len(line) < 2:
                continue
            style_kind = LINE_KINDS.get(layer, "feature")
            size, tier = TYPE[style_kind]
            want.append(labels.Wanted(
                key=key, text=name, kind=style_kind, tier=_tier(tier, feature),
                role=LINE_ROLES.get(style_kind, "label"),
                size=size, line=line, weight=labels._length(line)))
        elif kind == "point":
            icon = by_id.get(key)
            if icon is None:
                continue
            style_kind = _label_kind(icon.rank)
            size, tier = TYPE[style_kind]
            want.append(labels.Wanted(
                key=key, text=name, kind=style_kind, tier=_tier(tier, feature),
                role="label", size=size, point=(icon.x, icon.y),
                clearance=icon.radius, weight=icon.radius))

    want.extend(_sea_wanted(land, bounds))
    return want


def _tier(tier: int, feature: Mapping[str, Any]) -> int:
    """A thing the writer drew themselves is labelled before a thing the map made.

    The Iron Road is one of two roads in the world whose name its author chose, and it
    went unlabelled while nine generated brooks took the room. §66 again: what they
    wrote outranks what was worked out.
    """
    return tier if feature.get("generated") else max(0, tier - 1)


def _one_per_thing(features: Sequence[Mapping[str, Any]]
                   ) -> list[Mapping[str, Any]]:
    """The one shape of each entity a name should go on: its biggest or longest.

    Points win outright — a town's name belongs beside the town, not along the road out
    of it — and among shapes of the same sort the largest is the one with room for the
    text.
    """
    best: dict[str, tuple[tuple[int, float], Mapping[str, Any]]] = {}
    for feature in features:
        entity_id = str(feature.get("entity_id") or feature.get("id") or "")
        rank = _label_worth(feature)
        current = best.get(entity_id)
        if current is None or rank > current[0]:
            best[entity_id] = (rank, feature)
    return [feature for _rank, feature in
            sorted(best.values(), key=lambda pair: str(pair[1].get("id")))]


def _label_worth(feature: Mapping[str, Any]) -> tuple[int, float]:
    kind = feature.get("kind")
    if kind == "point":
        return (2, 0.0)
    if kind == "polygon":
        rings = feature.get("coordinates") or []
        ring = tuple(_points(rings[0])) if rings else ()
        return (1, shapes.area(ring) if len(ring) >= 3 else 0.0)
    line = tuple(_points(feature.get("coordinates")))
    return (0, labels._length(line) if len(line) >= 2 else 0.0)


def _label_kind(rank: str) -> str:
    plain = rank.lower()
    if plain == "capital":
        return "capital"
    if plain in ("city", "port", "market town"):
        return "city"
    if plain in ("town", "fortress"):
        return "town"
    if plain in ("castle", "keep", "tower"):
        return "castle"
    return "village"


def _sea_wanted(land: list[tuple[Point, ...]], bounds: Bounds) -> list[labels.Wanted]:
    """The open water, which is a shape too, and the one nobody ever labels.

    Rasterised rather than turned into a polygon: the sea is the complement of every
    landmass at once, holes and islands included, and a complement is far easier to
    hold as a mask than as a ring with the right winding.
    """
    if not land:
        return []
    # Inset by the frame's own margin, so the name of the sea does not end up half over
    # the edge of the view — which is where the widest stretch of open water usually is.
    x0, y0 = bounds.x + MARGIN, bounds.y + MARGIN
    width, height = bounds.width - 2 * MARGIN, bounds.height - 2 * MARGIN
    cells = 52
    size = max(width, height) / cells
    if size <= 0:
        return []
    wide = max(1, int(width / size) + 1)
    tall = max(1, int(height / size) + 1)
    inside: list[bool] = []
    for k in range(wide * tall):
        point = (x0 + (k % wide + 0.5) * size, y0 + (k // wide + 0.5) * size)
        inside.append(not any(shapes.contains(ring, point) for ring in land))
    spine = labels.spine_of_mask(inside, size, x0, y0, wide, tall)
    if len(spine) < 2:
        return []
    size_pt, tier = TYPE["sea"]
    # An open sea with no name of its own still deserves the words, and "the open sea"
    # is what a map of a coast says when the writer has not said otherwise.
    return [labels.Wanted(key="sea", text="The Open Sea", kind="sea", tier=tier,
                          role="label-water", size=size_pt, ring=(), line=spine,
                          weight=1e9)]


# ---- the key ---------------------------------------------------------------

def _legend(features: Sequence[Mapping[str, Any]], icons: Sequence[Icon],
            mode: str, holders: Mapping[str, str]) -> list[LegendEntry]:
    """What is on this map, in the words the map itself uses.

    Built from what was actually drawn rather than from a fixed list, so a world with
    no castles has no castle in its key and a reader is never told to look for
    something that is not there.
    """
    out: list[LegendEntry] = []

    ranks = sorted({icon.rank for icon in icons if icon.role == "settlement"})
    for rank in ranks:
        shape, _radius = RANKS.get(rank.lower(), DEFAULT_RANK)
        out.append(LegendEntry(key=f"rank:{rank}", label=rank.title(),
                               role="settlement", swatch=shape))
    if any(icon.role == "castle" for icon in icons):
        out.append(LegendEntry(key="rank:castle", label="Castle or keep",
                               role="castle", swatch="keep"))

    layers = {str(f.get("layer") or "") for f in features}
    if "waterways" in layers:
        out.append(LegendEntry(key="line:river", label="River", role="waterway",
                               swatch="line"))
    if "roads" in layers:
        out.append(LegendEntry(key="line:road", label="Road", role="road",
                               swatch="line",
                               note="thicker where more of the kingdom's traffic goes"))
    if any(f.get("approximate") for f in features):
        out.append(LegendEntry(
            key="dashed", label="Drawn by the map, not surveyed", role="border",
            swatch="dashed",
            note="a dashed edge is the map's guess and yours to move"))
    if any(icon.contested for icon in icons):
        out.append(LegendEntry(key="contested", label="Claimed by somebody else",
                               role="contested", swatch="ring"))

    named: dict[str, str] = {}
    for feature in features:
        for holder in (feature.get("control") or {}).get(mode) or []:
            named[str(holder.get("id"))] = str(holder.get("name") or "")
    for entity_id, name in sorted(named.items(), key=lambda pair: (pair[1], pair[0])):
        out.append(LegendEntry(key=f"holder:{entity_id}", label=name,
                               role=holders.get(entity_id, "holder-0"), swatch="fill",
                               note=MODE_WORDS[mode], entity_id=entity_id))
    return out
