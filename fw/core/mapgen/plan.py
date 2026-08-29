"""A map that does not exist yet.

The generator used to write straight into the world: press the button and several
hundred rows appeared. That is the wrong shape for this application. §66 says derived
data must never be confused with what the writer authored, and a writer who has to undo
a map to find out they did not want it is being asked to trust a black box.

So generation is two halves. `plan_map` computes a whole map and writes *nothing* —
this module is what it returns. The writer looks at it, rejects the river that runs
through their capital, renames the town, and only then does `apply_plan` commit what
survived, in one undoable action.

Two rules make that work across runs.

**A feature's identity is derived from what it is**, never from an entity id — so
regenerating recognises last run's river as the same river, and a rejection or a rename
sticks to it.

**The plan's digest covers the proposal only**, never the diff against the world. That
is what lets a writer accept half a plan, look at it, and accept the rest without the
generator re-proposing a different map underneath them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from fw.core.mapgen import guards
from fw.core.mapgen.drafts import FactSpec, SegmentSpec, ShapeSpec, SubjectSpec
from fw.core.mapgen.findings import Finding
from fw.core.mapgen.ids import ALGORITHM

PLAN_FORMAT = 1
GENERATED_TAG = "generated-map"
PROVENANCE_KEY = "mapgen"

# Ceilings, so a pathological world cannot ship a map the browser cannot draw.
MAX_RING_VERTICES = 400
MAX_PLAN_VERTICES = 40_000

# Drawing and reading order: land first, roads last, so the plan lists features the way
# a reader meets them rather than the way the pipeline happened to compute them.
KIND_ORDER = ("coast", "island", "region", "range", "hills", "sea", "water", "lake",
              "river", "natural", "settlement", "castle", "ruin", "road")

# What each kind must say about itself. A plan whose details do not match is a bug in
# the stage that emitted it, and `violations()` is how it gets caught in a test rather
# than in front of the writer.
DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    "coast": ("landmass", "area"),
    "island": ("landmass", "area"),
    "region": ("share", "dominant"),
    "range": ("strike", "crest"),
    "hills": ("crest",),
    "sea": ("water_kind",),
    "water": ("water_kind",),
    "lake": ("area",),
    "river": ("mouth", "strahler"),
    "natural": ("feature_kind", "area_cells"),
    "settlement": ("rank",),
    "castle": ("rank",),
    "ruin": ("rank",),
    "road": ("tier", "span"),
}


@dataclass(frozen=True)
class MapBrief:
    """Everything the writer can turn.

    Hashed into the plan id, and nothing else is: two runs with the same brief over the
    same world are the same proposal, whatever has since been drawn.
    """

    seed: str = ""                      # "" -> the world's own name
    at: int | None = None
    include: tuple[str, ...] = ("coast", "region", "range", "water", "river",
                                "natural", "settlement", "road")
    invent_settlements: bool = False    # §66: inventing a noun is opt-in
    north: str = "up"
    prevailing_wind: str = ""           # "" -> read from the writer's prose

    def as_dict(self) -> dict:
        return {"seed": self.seed, "at": self.at, "include": list(self.include),
                "invent_settlements": self.invent_settlements, "north": self.north,
                "prevailing_wind": self.prevailing_wind}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> MapBrief:
        return cls(
            seed=str(raw.get("seed") or ""),
            at=raw.get("at"),
            include=tuple(raw.get("include") or cls.include),
            invent_settlements=bool(raw.get("invent_settlements", False)),
            north=str(raw.get("north") or "up"),
            prevailing_wind=str(raw.get("prevailing_wind") or ""),
        )

    def wants(self, kind: str) -> bool:
        return kind in self.include


@dataclass(frozen=True)
class PlannedFeature:
    """One thing the map proposes, and the case for it."""

    id: str
    kind: str
    name: str
    subject: SubjectSpec | None = None
    anchor_id: str | None = None
    shapes: tuple[ShapeSpec, ...] = ()
    facts: tuple[FactSpec, ...] = ()
    segments: tuple[SegmentSpec, ...] = ()
    why: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    default_accept: bool = True
    renameable: bool = True
    causal: bool = False
    # The diff against what is already in the world. Deliberately outside the digest:
    # see the module docstring.
    status: str = "new"          # new | unchanged | changed | rejected-before | promoted
    world_entity_id: str | None = None
    world_geometry_ids: tuple[str, ...] = ()

    def because(self) -> str:
        """The case for this feature, in one sentence a novelist can read."""
        if not self.why:
            return f"{self.name} is here for want of anywhere better."
        return f"{self.name} — " + "; ".join(self.why[:3]) + "."

    @property
    def invented(self) -> bool:
        return self.subject is not None and self.subject.mode == "new"

    def proposal(self) -> dict:
        """Just the proposal — what the digest is taken over.

        Entity ids are redacted. They are ULIDs, random per world, so two copies of the
        same world would otherwise hash to different plans and nothing about identity
        would survive being copied. The feature id already carries the entity's name,
        which is the part of it that means anything.
        """
        return {
            "id": self.id, "kind": self.kind, "name": self.name,
            "subject": _subject_dict(self.subject, redact=True),
            "anchor_id": self.anchor_id,
            "shapes": [_shape_dict(s) for s in self.shapes],
            "facts": [_fact_dict(f, redact=True) for f in self.facts],
            "segments": [_segment_dict(s, redact=True) for s in self.segments],
            "why": list(self.why), "detail": self.detail,
            "depends_on": list(self.depends_on),
            "default_accept": self.default_accept, "renameable": self.renameable,
            "causal": self.causal,
        }

    def as_dict(self) -> dict:
        out = self.proposal()
        out.update({"subject": _subject_dict(self.subject),
                    "facts": [_fact_dict(f) for f in self.facts],
                    "segments": [_segment_dict(s) for s in self.segments],
                    "status": self.status, "world_entity_id": self.world_entity_id,
                    "world_geometry_ids": list(self.world_geometry_ids)})
        return out

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> PlannedFeature:
        return cls(
            id=str(raw["id"]), kind=str(raw["kind"]), name=str(raw.get("name") or ""),
            subject=_subject_of(raw.get("subject")),
            anchor_id=raw.get("anchor_id"),
            shapes=tuple(_shape_of(s) for s in raw.get("shapes") or ()),
            facts=tuple(_fact_of(f) for f in raw.get("facts") or ()),
            segments=tuple(_segment_of(s) for s in raw.get("segments") or ()),
            why=tuple(raw.get("why") or ()),
            detail=dict(raw.get("detail") or {}),
            depends_on=tuple(raw.get("depends_on") or ()),
            default_accept=bool(raw.get("default_accept", True)),
            renameable=bool(raw.get("renameable", True)),
            causal=bool(raw.get("causal", False)),
            status=str(raw.get("status") or "new"),
            world_entity_id=raw.get("world_entity_id"),
            world_geometry_ids=tuple(raw.get("world_geometry_ids") or ()),
        )


@dataclass(frozen=True)
class Retirement:
    """Something a previous run drew that this one does not.

    `writer_touched` is the difference between deleting it and handing it over: a
    generated town the writer has since renamed, dated or written about is theirs now,
    and the generator's job is to stop claiming it, not to take it away.
    """

    feature_id: str
    name: str
    entity_id: str | None
    geometry_ids: tuple[str, ...]
    writer_touched: bool
    why: str

    def as_dict(self) -> dict:
        return {"feature_id": self.feature_id, "name": self.name,
                "entity_id": self.entity_id,
                "geometry_ids": list(self.geometry_ids),
                "writer_touched": self.writer_touched, "why": self.why}


@dataclass(frozen=True)
class PlanStats:
    features_by_kind: Mapping[str, int] = field(default_factory=dict)
    vertices: int = 0
    new_entities: int = 0
    facts: int = 0
    segments: int = 0
    stage_ms: Mapping[str, int] = field(default_factory=dict)
    plan_ms: int = 0

    def as_dict(self) -> dict:
        return {"features_by_kind": dict(self.features_by_kind),
                "vertices": self.vertices, "new_entities": self.new_entities,
                "facts": self.facts, "segments": self.segments,
                "stage_ms": dict(self.stage_ms), "plan_ms": self.plan_ms}


@dataclass(frozen=True)
class MapPlan:
    """A whole map, computed and not yet written."""

    plan_id: str
    world_name: str
    branch: str
    brief: MapBrief
    features: tuple[PlannedFeature, ...] = ()
    retiring: tuple[Retirement, ...] = ()
    stats: PlanStats = field(default_factory=PlanStats)
    findings: tuple[Finding, ...] = ()
    reading_fingerprint: str = ""
    plan_format: int = PLAN_FORMAT
    algorithm: str = ALGORITHM

    # ---- reading ----------------------------------------------------------

    def feature(self, feature_id: str) -> PlannedFeature | None:
        for candidate in self.features:
            if candidate.id == feature_id:
                return candidate
        return None

    def by_kind(self, kind: str) -> tuple[PlannedFeature, ...]:
        return tuple(f for f in self.features if f.kind == kind)

    def summary(self) -> str:
        """What this plan proposes, in the writer's terms."""
        if not self.features:
            return "Nothing to draw yet."
        counts = self.stats.features_by_kind or _count(self.features)
        nouns = {"coast": "coastline", "island": "islands", "region": "regions",
                 "range": "mountain ranges", "hills": "hill country", "sea": "seas",
                 "water": "waters", "lake": "lakes", "river": "rivers",
                 "natural": "forests and marshes", "settlement": "settlements",
                 "castle": "castles", "ruin": "ruins", "road": "roads"}
        bits = [f"{counts[kind]} {nouns.get(kind, kind)}"
                for kind in KIND_ORDER if counts.get(kind)]
        invented = sum(1 for f in self.features if f.invented and not f.default_accept)
        text = "This map proposes " + ", ".join(bits) + "."
        if invented:
            text += f" {invented} of them are new places, and are off by default."
        if self.retiring:
            text += f" {len(self.retiring)} things from the last map are not in this one."
        return text

    # ---- the shape on the wire --------------------------------------------

    def to_dict(self) -> dict:
        return {
            "plan_format": self.plan_format, "algorithm": self.algorithm,
            "plan_id": self.plan_id, "reading_fingerprint": self.reading_fingerprint,
            "world_name": self.world_name, "branch": self.branch,
            "brief": self.brief.as_dict(),
            "features": [f.as_dict() for f in self.features],
            "retiring": [r.as_dict() for r in self.retiring],
            "stats": self.stats.as_dict(),
            "findings": [f.as_dict() for f in self.findings],
            "summary": self.summary(),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> MapPlan:
        return cls(
            plan_format=int(raw.get("plan_format", PLAN_FORMAT)),
            algorithm=str(raw.get("algorithm") or ALGORITHM),
            plan_id=str(raw["plan_id"]),
            reading_fingerprint=str(raw.get("reading_fingerprint") or ""),
            world_name=str(raw.get("world_name") or ""),
            branch=str(raw.get("branch") or "canon"),
            brief=MapBrief.from_dict(raw.get("brief") or {}),
            features=tuple(PlannedFeature.from_dict(f) for f in raw.get("features") or ()),
            retiring=tuple(
                Retirement(feature_id=str(r["feature_id"]), name=str(r.get("name") or ""),
                           entity_id=r.get("entity_id"),
                           geometry_ids=tuple(r.get("geometry_ids") or ()),
                           writer_touched=bool(r.get("writer_touched", False)),
                           why=str(r.get("why") or ""))
                for r in raw.get("retiring") or ()),
            stats=PlanStats(**{k: v for k, v in (raw.get("stats") or {}).items()
                               if k in PlanStats.__dataclass_fields__}),
            findings=tuple(
                Finding(code=str(f["code"]), severity=str(f["severity"]),
                        message=str(f["message"]),
                        subjects=tuple(f.get("subjects") or ()),
                        quotes=tuple(f.get("quotes") or ()),
                        feature_id=f.get("feature_id"))
                for f in raw.get("findings") or ()),
        )

    # ---- checking ---------------------------------------------------------

    def violations(self) -> list[str]:
        """Everything wrong with this plan. Always empty on a good run.

        A self-check rather than a test helper: a plan that refers to a feature it does
        not contain, or draws a shape on a layer nobody renders, is a bug that reaches
        the writer as a missing river rather than as an exception.
        """
        problems: list[str] = []
        seen: set[str] = set()
        for feature in self.features:
            if feature.id in seen:
                problems.append(f"duplicate feature id {feature.id}")
            seen.add(feature.id)
        for feature in self.features:
            if feature.kind not in KIND_ORDER:
                problems.append(f"{feature.id}: unknown kind {feature.kind!r}")
            if not feature.name:
                problems.append(f"{feature.id}: has no name")
            if (feature.subject is None) == (feature.anchor_id is None):
                problems.append(
                    f"{feature.id}: needs exactly one of a subject or an anchor")
            if feature.anchor_id and feature.anchor_id not in seen | {
                    f.id for f in self.features}:
                problems.append(f"{feature.id}: anchored to a feature not in the plan")
            for needed in feature.depends_on:
                if needed not in {f.id for f in self.features}:
                    problems.append(f"{feature.id}: depends on {needed}, which is absent")
            if feature.subject is not None and feature.subject.mode == "existing":
                if not feature.subject.entity_id:
                    problems.append(f"{feature.id}: existing subject with no entity")
                if feature.renameable:
                    problems.append(
                        f"{feature.id}: the writer's own place must not be renameable")
            for wanted in DETAIL_KEYS.get(feature.kind, ()):
                if wanted not in feature.detail:
                    problems.append(f"{feature.id}: detail is missing {wanted!r}")
            for shape in feature.shapes:
                problems.extend(_shape_problems(feature.id, shape))
        vertices = sum(_count_vertices(s.coordinates)
                       for f in self.features for s in f.shapes)
        if vertices > MAX_PLAN_VERTICES:
            problems.append(f"{vertices} vertices is more than a browser should be sent")
        return problems


def _shape_problems(feature_id: str, shape: ShapeSpec) -> list[str]:
    from fw.core.mapgen.drafts import LAYERS, ROLES
    out = []
    if shape.layer not in LAYERS:
        out.append(f"{feature_id}: nothing draws the layer {shape.layer!r}")
    if shape.role not in ROLES:
        out.append(f"{feature_id}: unknown shape role {shape.role!r}")
    if shape.kind not in ("point", "line", "polygon"):
        out.append(f"{feature_id}: unknown shape kind {shape.kind!r}")
    if shape.kind == "polygon":
        for ring in shape.coordinates or []:
            if len(ring) > MAX_RING_VERTICES:
                out.append(f"{feature_id}: a ring of {len(ring)} vertices is too fine")
            if len(ring) >= 2 and list(ring[0]) != list(ring[-1]):
                out.append(f"{feature_id}: a polygon ring is not closed")
    return out


def _count_vertices(coordinates) -> int:
    if isinstance(coordinates, (int, float)):
        return 0
    if coordinates and all(isinstance(v, (int, float)) for v in coordinates):
        return 1
    return sum(_count_vertices(child) for child in coordinates)


def _count(features) -> dict[str, int]:
    counts: dict[str, int] = {}
    for feature in features:
        counts[feature.kind] = counts.get(feature.kind, 0) + 1
    return counts


def order_features(features: list[PlannedFeature]) -> tuple[PlannedFeature, ...]:
    """Canonical order: by kind as a reader meets them, then by id."""
    rank = {kind: n for n, kind in enumerate(KIND_ORDER)}
    return tuple(sorted(features, key=lambda f: (rank.get(f.kind, 99), f.id)))


def digest_of(brief: MapBrief, features: tuple[PlannedFeature, ...]) -> str:
    """The plan's identity: the proposal, and nothing about the world it will land in.

    Excluding the diff is the single load-bearing decision in the whole apply path. It
    is what lets a writer accept half a plan, look at what happened, and accept the
    rest from the plan they are still holding — because accepting the first half
    changed the world, and if the world were in the digest the plan they hold would
    have gone stale in their hands.
    """
    body = {
        "plan_format": PLAN_FORMAT,
        "algorithm": ALGORITHM,
        "brief": brief.as_dict(),
        "features": [f.proposal() for f in features],
    }
    return hashlib.blake2b(guards.canonical_json(body).encode("utf-8"),
                           digest_size=16).hexdigest()


def with_status(feature: PlannedFeature, **changes) -> PlannedFeature:
    return replace(feature, **changes)


# ---- the small conversions -------------------------------------------------

def _subject_dict(subject: SubjectSpec | None, *, redact: bool = False) -> dict | None:
    if subject is None:
        return None
    return {"mode": subject.mode, "type_key": subject.type_key,
            "entity_id": None if redact else subject.entity_id,
            "summary_template": subject.summary_template,
            "tags": list(subject.tags), "confidence": subject.confidence}


def _subject_of(raw) -> SubjectSpec | None:
    if not raw:
        return None
    return SubjectSpec(mode=str(raw["mode"]), type_key=str(raw["type_key"]),
                       entity_id=raw.get("entity_id"),
                       summary_template=str(raw.get("summary_template") or ""),
                       tags=tuple(raw.get("tags") or ()),
                       confidence=str(raw.get("confidence") or "speculative"))


def _shape_dict(shape: ShapeSpec) -> dict:
    return {"role": shape.role, "kind": shape.kind,
            "coordinates": shape.coordinates, "layer": shape.layer,
            "style": dict(shape.style), "approximate": shape.approximate}


def _shape_of(raw) -> ShapeSpec:
    return ShapeSpec(role=str(raw["role"]), kind=str(raw["kind"]),
                     coordinates=raw["coordinates"], layer=str(raw["layer"]),
                     style=dict(raw.get("style") or {}),
                     approximate=bool(raw.get("approximate", True)))


def _fact_dict(fact: FactSpec, *, redact: bool = False) -> dict:
    reference = fact.object_ref
    if redact and reference and not reference.startswith("@"):
        reference = "<entity>"
    return {"predicate_key": fact.predicate_key, "object_ref": reference,
            "value": fact.value, "confidence": fact.confidence, "note": fact.note}


def _fact_of(raw) -> FactSpec:
    return FactSpec(predicate_key=str(raw["predicate_key"]),
                    object_ref=raw.get("object_ref"), value=raw.get("value"),
                    confidence=str(raw.get("confidence") or "speculative"),
                    note=str(raw.get("note") or ""))


def _segment_dict(segment: SegmentSpec, *, redact: bool = False) -> dict:
    def ref(value: str) -> str:
        return value if (value.startswith("@") or not redact) else "<entity>"

    return {"from_ref": ref(segment.from_ref), "to_ref": ref(segment.to_ref),
            "length": segment.length, "medium": segment.medium,
            "quality": segment.quality, "terrain": segment.terrain,
            "closed_seasons": list(segment.closed_seasons), "danger": segment.danger}


def _segment_of(raw) -> SegmentSpec:
    return SegmentSpec(from_ref=str(raw["from_ref"]), to_ref=str(raw["to_ref"]),
                       length=float(raw["length"]),
                       medium=str(raw.get("medium") or "road"),
                       quality=float(raw.get("quality", 0.8)),
                       terrain=str(raw.get("terrain") or "plain"),
                       closed_seasons=tuple(raw.get("closed_seasons") or ()),
                       danger=str(raw.get("danger") or "low"))
