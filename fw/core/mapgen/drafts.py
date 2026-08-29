"""The one seam every stage of the map emits into.

A stage that knows how to find a river should not also know about entity ids, undo,
branches or the writer's past decisions. So no stage writes anything: each emits
`FeatureDraft`s — what it found, where, and why — and one assembler turns those into a
plan, and one applier turns an accepted plan into rows.

Two consequences fall out that are worth the indirection.

**A draft has no name yet.** Naming is one batch pass at the end, because a name may
need to mention its neighbours ("where the White Knife meets the Bite") and a stage
that named as it went would need whatever comes after it to already exist. So reasons
are templates with slots, rendered once every name is known.

**A draft has no id.** Identity comes from `key_parts` — the region it belongs to, the
cell it sits on — never from an entity id, so the same map is recognisable across two
copies of the same world and a rejected feature stays rejected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Where a shape is drawn. The client hides and shows by layer, so a new one is a new
# checkbox in front of the writer.
LAYERS = ("land", "waters", "regions", "relief", "features", "waterways",
          "settlements", "roads", "castles")

# What a shape is *for*, which is how a regeneration recognises its own previous work
# and how the client decides what to paint it with.
ROLES = ("outline", "spine", "point", "ring", "hole", "segment", "fill")


@dataclass(frozen=True)
class Reason:
    """Why a thing is where it is (§67).

    Rendered to prose *after* naming, so a reason may refer to a feature that does not
    have a name yet — which is most of them, since the interesting reasons are about
    neighbours.
    """

    kind: str                    # confluence | mouth | harbour | ford | pass | seat |
                                 # market | authored | history | resource | crossing
    weight: float
    template: str                # "where {0} meets {1}" — {n} indexes `refs`
    refs: tuple[str, ...] = ()   # draft keys, or names already in the world
    evidence: str = ""           # "the river you named; its only navigable mouth"

    def render(self, names: dict[str, str]) -> str:
        """The sentence, once the things it mentions have names."""
        resolved = [names.get(ref, ref) for ref in self.refs]
        try:
            return self.template.format(*resolved)
        except (IndexError, KeyError):
            # A reason that cannot be rendered is a bug, but a map that refuses to draw
            # because of a punctuation error in an explanation is a worse one.
            return self.template


@dataclass(frozen=True)
class NameRequest:
    """A name this feature needs, and everything the namer should know to choose one."""

    key: str                     # ids.name_key(...) — stable, derived from the source
    kind: str                    # a names.CORPORA key
    hint: str = ""               # why it is here: 'ford', 'harbour', 'pass', ...
    near: tuple[str, ...] = ()   # neighbouring names the writer already chose


@dataclass(frozen=True)
class ShapeSpec:
    """One drawn thing. Coordinates are in world units, at one decimal place."""

    role: str
    kind: str                    # point | line | polygon
    coordinates: Any
    layer: str
    style: dict[str, str] = field(default_factory=dict)
    approximate: bool = True     # §92: a generated border is not a surveyed one


@dataclass(frozen=True)
class FactSpec:
    """Something the map asserts about a feature — always as speculation, never canon."""

    predicate_key: str
    object_ref: str | None = None      # an entity id, or "@<draft key>"
    value: str | None = None
    confidence: str = "speculative"
    note: str = ""


@dataclass(frozen=True)
class SegmentSpec:
    """A road or navigable reach, in the travel engine's own vocabulary."""

    from_ref: str                      # an entity id, or "@<draft key>"
    to_ref: str
    length: float
    medium: str = "road"               # road | river | sea
    quality: float = 0.8
    terrain: str = "plain"             # MUST be a key of routing.LAND
    closed_seasons: tuple[str, ...] = ()
    danger: str = "low"


@dataclass(frozen=True)
class SubjectSpec:
    """The thing in the world a feature is about — one the writer made, or a new one."""

    mode: str                          # "existing" | "new"
    type_key: str
    entity_id: str | None = None       # set iff mode == "existing"
    summary_template: str = ""
    tags: tuple[str, ...] = ()
    confidence: str = "speculative"


@dataclass(frozen=True)
class FeatureDraft:
    """What every geometry stage emits. Knows nothing about ids, ledgers or writes."""

    kind: str
    key_parts: tuple[str | int, ...]
    # When the thing this feature is about was itself generated, it already carries an
    # id from the run that made it. Reusing it is what stops a proposed town, once
    # accepted, coming back next run as a *different* town beside the first.
    known_id: str | None = None
    subject: SubjectSpec | None = None
    anchor_key: tuple[str | int, ...] | None = None   # hang shapes off another draft
    shapes: tuple[ShapeSpec, ...] = ()
    facts: tuple[FactSpec, ...] = ()
    segments: tuple[SegmentSpec, ...] = ()
    reasons: tuple[Reason, ...] = ()
    name_request: NameRequest | None = None           # None -> keeps the subject's name
    # A name that is already decided and not the namer's to invent — the mainland is
    # called after the world, not after a syllable model.
    fixed_name: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    depends_on_keys: tuple[tuple[str | int, ...], ...] = ()
    # §66 in one field: siting a place the writer already made is accepted by default;
    # inventing one is opt-in.
    default_accept: bool = True
    causal: bool = False               # rejecting this needs a full re-plan
