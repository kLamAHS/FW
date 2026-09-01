"""What the last map left behind, and whether the writer has since made it theirs.

Regenerating has to answer one question about every shape already on the map: is this
mine to redraw? Getting it wrong in one direction duplicates the whole world on every
run; getting it wrong in the other deletes a town the writer wrote three chapters about.

So every generated thing is stamped — the entity with a tag, each shape with its
feature's id in `props` — and this reads those stamps back. `style` is deliberately not
where the stamp lives: the client passes style keys through to what it draws, so
provenance kept there is one careless render away from being painted on the map.

The interesting part is `writer_touched`. A generated settlement the writer has renamed,
dated, written a summary for or asserted a fact about is not really generated any more.
The generator's job at that point is to stop claiming it — not to take it away.
"""

from __future__ import annotations

from dataclasses import dataclass

from fw.core.mapgen import guards
from fw.core.mapgen.ids import ALGORITHM
from fw.core.mapgen.plan import GENERATED_TAG, PROVENANCE_KEY
from fw.core.model.records import Geometry
from fw.core.world import World

# What the previous generation stamped with, so its work is still recognised rather
# than duplicated alongside the new map.
LEGACY_MARK = ("generated_by", "mapgen/1")

TAG_PREFIX = "mapgen:"

# The curated, kind-contracted semantics a feature carries (`PlannedFeature.detail`,
# whose shape DETAIL_KEYS enforces). Kept beside the provenance, not inside it: the
# provenance never crosses the wire, this always does.
SEMANTICS_KEY = "sem"


@dataclass(frozen=True)
class LedgerRow:
    """One feature this world already carries from a previous run."""

    feature_id: str | None            # None for a shape from the old generator
    entity_id: str | None
    geometries: tuple[Geometry, ...]
    name_at_write: str
    summary_at_write: str
    signature: str
    pinned: bool
    generation: str
    # The route segments this feature stamped. A road whose every stretch of ink was
    # already drawn by busier roads has no geometry at all — only its segment — and a
    # ledger that read only shapes forgot it existed: every re-apply of an identical
    # plan then minted a fresh entity and a duplicate segment.
    segments: tuple = ()

    @property
    def geometry_ids(self) -> tuple[str, ...]:
        return tuple(g.id for g in self.geometries)

    @property
    def segment_signatures(self) -> tuple[str, ...]:
        """What this feature last said in the travel graph, as stamped."""
        return tuple(sorted(str(provenance(s).get("sig") or "")
                            for s in self.segments))


def provenance(geometry) -> dict:
    """The stamp on one shape or segment, or an empty dict if it is the writer's own."""
    marker = (geometry.props or {}).get(PROVENANCE_KEY)
    return marker if isinstance(marker, dict) else {}


def segment_signature(segment) -> str:
    """A route segment's identity, independent of how its ends were referenced.

    The plan names an end as a draft ref and the store as an entity id, so the ends
    cannot take part; the payload is what "the same segment" means across a re-apply.
    Works on a `SegmentSpec` and a stored `RouteSegment` alike — they share every
    field this reads.
    """
    seasons = ",".join(segment.closed_seasons or ())
    return (f"{segment.length:.1f}|{segment.medium}|{segment.quality:.2f}|"
            f"{segment.terrain}|{segment.danger}|{segment.built_on}|{seasons}")


def semantics(geometry: Geometry) -> dict:
    """What the generator understood about this shape — stream order, road grade,
    coast character — as distinct from the provenance beside it.

    This is the half of `props` that IS meant to reach a renderer: `/api/map`
    serialises exactly this dict and nothing else from props, so the provenance
    stays unpaintable while the cartography stops guessing semantics back out of
    stroke widths.
    """
    told = (geometry.props or {}).get(SEMANTICS_KEY)
    return told if isinstance(told, dict) else {}


def is_generated(geometry: Geometry) -> bool:
    if provenance(geometry):
        return True
    # The first generator wrote its mark into `style`. Recognising it is what stops a
    # world drawn by the old code sprouting a second, overlapping map.
    key, value = LEGACY_MARK
    return (geometry.style or {}).get(key) == value


def stamp(feature_id: str, role: str, signature: str, name: str,
          *, pinned: bool = False, summary: str = "",
          sem: dict | None = None) -> dict:
    """The provenance to write onto a shape.

    The name and summary are recorded as written, so a later run can tell "the writer
    changed this" from "this is exactly what we put here".
    """
    marked = {PROVENANCE_KEY: {"gen": ALGORITHM, "feature": feature_id, "role": role,
                               "sig": signature, "name": name, "pinned": pinned,
                               "summary": summary}}
    if sem:
        marked[SEMANTICS_KEY] = dict(sem)
    return marked


def entity_tags(feature_id: str) -> tuple[str, ...]:
    return (GENERATED_TAG, f"{TAG_PREFIX}{feature_id}")


def feature_of(entity) -> str | None:
    for tag in entity.tags or ():
        if tag.startswith(TAG_PREFIX):
            return tag[len(TAG_PREFIX):]
    return None


def read_ledger(world: World, *, at: int | None = None) -> dict[str, LedgerRow]:
    """Every generated feature this world already carries, by feature id.

    Shapes from the old generator, which had no feature ids, come back under the key
    `legacy:<geometry id>` so a rebuild can retire them without mistaking them for
    anything in the new plan.
    """
    by_entity: dict[str, list[Geometry]] = {}
    rows: dict[str, LedgerRow] = {}
    loose: list[Geometry] = []

    index = world.geometry_index(at=at)
    for entity_id in sorted(index):
        for geometry in index[entity_id]:
            if not is_generated(geometry):
                continue
            marker = provenance(geometry)
            feature_id = marker.get("feature")
            if feature_id:
                by_entity.setdefault(feature_id, []).append(geometry)
            else:
                loose.append(geometry)

    # What each feature said in the travel graph. Read beside the shapes because a
    # feature can exist as a segment alone — a road whose ink was all drawn by busier
    # roads — and forgetting it re-created it on every apply.
    said: dict[str, list] = {}
    for segment in world.route_segments():
        marker = provenance(segment)
        feature_id = marker.get("feature")
        if feature_id:
            said.setdefault(str(feature_id), []).append(segment)

    entities = {e.id: e for e in world.entities()}
    for feature_id in sorted(by_entity):
        shapes = tuple(sorted(by_entity[feature_id], key=lambda g: (g.layer, g.id)))
        first = provenance(shapes[0])
        entity_id = shapes[0].entity_id
        rows[feature_id] = LedgerRow(
            feature_id=feature_id,
            entity_id=entity_id if entity_id in entities else None,
            geometries=shapes,
            name_at_write=str(first.get("name") or ""),
            summary_at_write=str(first.get("summary") or ""),
            signature=str(first.get("sig") or ""),
            pinned=bool(first.get("pinned", False)),
            generation=str(first.get("gen") or ALGORITHM),
            segments=tuple(sorted(said.get(feature_id, ()),
                                  key=lambda s: s.id)),
        )

    for feature_id in sorted(set(said) - set(rows)):
        spoken = tuple(sorted(said[feature_id], key=lambda s: s.id))
        first = provenance(spoken[0])
        entity_id = spoken[0].entity_id
        rows[feature_id] = LedgerRow(
            feature_id=feature_id,
            entity_id=entity_id if entity_id in entities else None,
            geometries=(),
            name_at_write=str(first.get("name") or ""),
            summary_at_write=str(first.get("summary") or ""),
            signature="",
            pinned=bool(first.get("pinned", False)),
            generation=str(first.get("gen") or ALGORITHM),
            segments=spoken,
        )

    for geometry in sorted(loose, key=lambda g: g.id):
        rows[f"legacy:{geometry.id}"] = LedgerRow(
            feature_id=None, entity_id=geometry.entity_id, geometries=(geometry,),
            name_at_write="", summary_at_write="", signature="", pinned=False,
            generation="mapgen/1")
    return rows


def writer_touched(world: World, entity_id: str | None, name_at_write: str,
                   *, summary_at_write: str = "", at: int | None = None) -> bool:
    """Whether the writer has made a generated thing their own.

    Renaming it, giving it dates, writing a summary, or asserting anything about it that
    the generator did not assert. Any of those and it stops being the generator's to
    delete — which is the difference between a tool that tidies up after itself and one
    that eats the writer's work.
    """
    if entity_id is None:
        return False
    entity = world.get_entity(entity_id)
    if entity is None:
        return False
    if name_at_write and entity.name != name_at_write:
        return True
    # A summary the generator itself wrote is not the writer touching anything. Only a
    # change to it counts — otherwise every proposed town looks adopted the moment it
    # is created, and the map can never take back its own work.
    if (entity.summary or "").strip() and entity.summary != summary_at_write:
        return True
    if entity.exists_from is not None or entity.exists_to is not None:
        return True
    for fact in guards.sorted_facts(world.facts_about(entity_id)):
        marker = (getattr(fact, "props", None) or {}).get(PROVENANCE_KEY)
        if not marker:
            return True
    return False
