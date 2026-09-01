"""Writing an accepted plan. The only module in the generator that touches the world.

Everything upstream of here is pure: a plan can be computed, looked at, argued with and
thrown away without the world knowing it happened. This is where it lands, and it has
four jobs that all have to be right at once.

**One undoable action.** A map is hundreds of rows. If they are hundreds of actions, the
writer's first Ctrl+Z gets one polygon back, which is not undo. Everything goes in one
transaction, under one action id.

**Applying twice must write nothing the second time.** The plan's features have stable
ids and their shapes have signatures, so a feature that is already in the world exactly
as proposed is left entirely alone — no rows, no revisions, no action to undo.

**Retiring must never eat the writer's work.** A generated town they have since renamed,
dated, or written about is theirs. The generator stops claiming it — strips its tag and
leaves everything else — rather than deleting it.

**A rejection must stick.** Answers are remembered against feature ids in the same
transaction, so the next plan does not offer the same river again.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.mapgen import guards
from fw.core.mapgen import ledger as ledger_module
from fw.core.mapgen.decide import Decision, DecisionSet, load_standing, resolve, save_standing
from fw.core.mapgen.drafts import FactSpec, SegmentSpec, ShapeSpec
from fw.core.mapgen.findings import Finding, note, warn
from fw.core.mapgen.ids import kind_of, shape_signature
from fw.core.mapgen.plan import PROVENANCE_KEY, MapPlan, PlannedFeature
from fw.core.world import World, WorldError

# What each op means, in the order a report should read them.
OPS = ("created", "updated", "unchanged", "promoted", "retired", "rejected",
       "blocked", "dropped", "kept-authored")


class PlanStale(WorldError):
    """The world moved under a plan. Carries a fresh one and the answers so far."""

    def __init__(self, message: str, fresh: MapPlan | None = None,
                 carried: DecisionSet | None = None) -> None:
        super().__init__(message)
        self.fresh = fresh
        self.carried = carried


@dataclass(frozen=True)
class FeatureOutcome:
    feature_id: str
    name: str
    op: str
    entity_id: str | None = None
    geometry_ids: tuple[str, ...] = ()
    why: str = ""

    def as_dict(self) -> dict:
        return {"feature_id": self.feature_id, "name": self.name, "op": self.op,
                "entity_id": self.entity_id,
                "geometry_ids": list(self.geometry_ids), "why": self.why}


@dataclass(frozen=True)
class ApplyReport:
    plan_id: str
    action_id: str | None = None
    outcomes: tuple[FeatureOutcome, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)
    findings: tuple[Finding, ...] = ()

    def summary(self) -> str:
        if not self.counts:
            return "Nothing changed."
        parts = [f"{self.counts[op]} {op}" for op in OPS if self.counts.get(op)]
        return "The map: " + ", ".join(parts) + "."

    def as_dict(self) -> dict:
        return {"plan_id": self.plan_id, "action_id": self.action_id,
                "outcomes": [o.as_dict() for o in self.outcomes],
                "counts": dict(self.counts),
                "findings": [f.as_dict() for f in self.findings],
                "summary": self.summary()}


def apply_plan(world: World, plan: MapPlan,
               decisions: DecisionSet | None = None,
               *, verify: bool = True) -> ApplyReport:
    """Write the accepted parts of a plan, as one undoable action."""
    if verify:
        problems = plan.violations()
        if problems:
            raise PlanStale("this map does not hold together: " + "; ".join(problems[:3]))
    if decisions is not None and decisions.plan_id and decisions.plan_id != plan.plan_id:
        raise PlanStale(
            "those answers were about a different map — look at this one before applying")

    if plan.branch and plan.branch != world.branch_name:
        raise PlanStale(
            f"this map was worked out on {plan.branch!r} and you are on "
            f"{world.branch_name!r}")

    answers = resolve(plan, load_standing(world), decisions)
    writer = _Writer(world, plan, answers)
    return writer.run()


class _Writer:
    """One pass over the plan, inside one transaction."""

    def __init__(self, world: World, plan: MapPlan,
                 answers: dict[str, Decision]) -> None:
        self.world = world
        self.plan = plan
        self.answers = answers
        self.ledger = ledger_module.read_ledger(world, at=plan.brief.at)
        self.outcomes: list[FeatureOutcome] = []
        self.findings: list[Finding] = list(plan.findings)
        self.entity_of: dict[str, str] = {}      # feature id -> entity id
        self.wrote = False
        # The segments already in the world, read once and only if something is
        # rewritten — the price of taking back a feature's old sayings.
        self._old_segments: list | None = None

    # ---- the run ----------------------------------------------------------

    def run(self) -> ApplyReport:
        accepted = [f for f in self.plan.features if self._accepts(f)]
        if not accepted and not self.plan.retiring:
            return self._report(None)

        action_id: str | None = None
        with self.world.db.transaction():
            for feature in accepted:
                self._write(feature)
            self._retire(accepted)
            self._keep_the_ground()
            self._remember()
            if self.wrote:
                action_id = self.world.current_action_id()

        for feature in self.plan.features:
            if not self._accepts(feature):
                answer = self.answers.get(feature.id)
                self.outcomes.append(FeatureOutcome(
                    feature_id=feature.id, name=feature.name, op="rejected",
                    why=(answer.note if answer and answer.note
                         else "you turned this one down")))
        return self._report(action_id)

    def _accepts(self, feature: PlannedFeature) -> bool:
        answer = self.answers.get(feature.id)
        return bool(answer and answer.accept)

    def _report(self, action_id: str | None) -> ApplyReport:
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.op] = counts.get(outcome.op, 0) + 1
        return ApplyReport(plan_id=self.plan.plan_id, action_id=action_id,
                           outcomes=tuple(self.outcomes), counts=counts,
                           findings=tuple(self.findings))

    # ---- one feature ------------------------------------------------------

    def _write(self, feature: PlannedFeature) -> None:
        answer = self.answers.get(feature.id)
        name = (answer.name if answer and answer.name and feature.renameable
                else feature.name)
        row = self.ledger.get(feature.id)

        # Pinned either by the mark on what was drawn, or by the writer saying so —
        # the answer they gave is a pin whether or not anything has been drawn yet.
        pinned = (row is not None and row.pinned) or bool(answer and answer.pinned)
        if pinned and row is not None:
            self.outcomes.append(FeatureOutcome(
                feature_id=feature.id, name=name, op="blocked",
                entity_id=row.entity_id, geometry_ids=row.geometry_ids,
                why="you pinned this, so the generator leaves it alone"))
            if row.entity_id:
                self.entity_of[feature.id] = row.entity_id
            return

        entity_id = self._subject(feature, name, row)
        if entity_id is None:
            self.outcomes.append(FeatureOutcome(
                feature_id=feature.id, name=name, op="dropped",
                why="the thing this belongs to is not in the world"))
            return
        self.entity_of[feature.id] = entity_id

        wanted = {shape_signature(s.coordinates): s for s in feature.shapes}
        present = {ledger_module.provenance(g).get("sig", ""): g
                   for g in (row.geometries if row else ())}
        # "Unchanged" means the shapes AND what the map understands about them. A row
        # written before a stage learned something new (a river's true order, a coast's
        # character) differs in semantics though not a vertex moved — and comparing
        # canonically is what lets re-accepting a plan upgrade an old map in place.
        same_story = all(
            guards.canonical_json(ledger_module.semantics(g))
            == guards.canonical_json(feature.detail)
            for g in (row.geometries if row else ()))
        # What it says in the travel graph counts too — both ways. A feature can be a
        # segment and nothing else (a road whose ink was all drawn by busier roads),
        # and a segment can change under an unmoved line (a re-dated built_on).
        same_saying = (row is not None and row.segment_signatures == tuple(
            sorted(ledger_module.segment_signature(spec)
                   for spec in feature.segments)))
        told = bool(present) or bool(row.segments if row else ())
        if (row is not None and told and set(wanted) == set(present) - {""}
                and same_story and same_saying):
            self.outcomes.append(FeatureOutcome(
                feature_id=feature.id, name=name, op="unchanged",
                entity_id=entity_id, geometry_ids=row.geometry_ids,
                why="nothing about it moved"))
            return

        for geometry in (row.geometries if row else ()):
            self.world.delete_geometry(geometry.id)
            self.wrote = True
        if row is not None:
            self._take_back_sayings(feature.id, entity_id)

        drawn: list[str] = []
        for shape in feature.shapes:
            drawn.append(self._draw(feature, shape, entity_id, name))
        for spec in feature.facts:
            self._assert(feature, entity_id, spec)
        for spec in feature.segments:
            self._segment(feature, spec, name)

        self.outcomes.append(FeatureOutcome(
            feature_id=feature.id, name=name,
            op="updated" if row is not None else "created",
            entity_id=entity_id, geometry_ids=tuple(drawn),
            why=feature.because()))

    def _subject(self, feature: PlannedFeature, name: str, row) -> str | None:
        """The entity this feature is about — found, renamed, or made."""
        if feature.anchor_id is not None:
            return self.entity_of.get(feature.anchor_id)

        subject = feature.subject
        if subject is None:
            return None
        if subject.mode == "existing":
            return subject.entity_id if self.world.get_entity(subject.entity_id) else None

        if row is not None and row.entity_id:
            existing = self.world.get_entity(row.entity_id)
            if existing is not None:
                if existing.name != name:
                    self.world.update_entity(row.entity_id, name=name)
                    self.wrote = True
                return row.entity_id

        made = self.world.add_entity(
            subject.type_key, name,
            summary=subject.summary_template.format(name=name)
            if subject.summary_template else "",
            exists_from=subject.exists_from,
            tags=list(subject.tags) + list(ledger_module.entity_tags(feature.id)),
        )
        self.wrote = True
        return made.id

    def _draw(self, feature: PlannedFeature, shape: ShapeSpec,
              entity_id: str, name: str) -> str:
        entity = self.world.get_entity(entity_id)
        drawn = self.world.add_geometry(
            entity_id, shape.kind, shape.coordinates, layer=shape.layer,
            style=dict(shape.style), approximate=shape.approximate,
            props=ledger_module.stamp(feature.id, shape.role,
                                      shape_signature(shape.coordinates), name,
                                      summary=(entity.summary if entity else ""),
                                      sem=feature.detail),
        )
        self.wrote = True
        return drawn.id

    def _assert(self, feature: PlannedFeature, entity_id: str,
                spec: FactSpec) -> None:
        target = self._resolve_ref(spec.object_ref) if spec.object_ref else None
        if spec.object_ref and target is None:
            return
        subject = entity_id
        if spec.subject_ref:
            # The sentence runs the other way: the named subject speaks, and this
            # feature's entity is what it speaks about.
            subject = self._resolve_ref(spec.subject_ref)
            if subject is None:
                return
            target = target or entity_id
        try:
            self.world.assert_fact(
                subject, spec.predicate_key, target, value=spec.value,
                confidence=spec.confidence, note=spec.note,
                props=ledger_module.stamp(feature.id, "fact", "", "")
            )
            self.wrote = True
        except WorldError as exc:
            # The vocabulary a world predates: say so rather than losing the map.
            self.findings.append(warn(
                "missing-predicate",
                f"this world has no “{spec.predicate_key}” to record, so that part of "
                f"the map was left unsaid ({exc})"))

    def _segment(self, feature: PlannedFeature, spec: SegmentSpec,
                 name: str = "") -> None:
        origin = self._resolve_ref(spec.from_ref)
        target = self._resolve_ref(spec.to_ref)
        if origin is None or target is None:
            return
        self.world.add_route_segment(
            origin, target, spec.length, medium=spec.medium, quality=spec.quality,
            terrain=spec.terrain, entity_id=self.entity_of.get(feature.id),
            closed_seasons=spec.closed_seasons, danger=spec.danger,
            built_on=spec.built_on,
            # A real signature and the name as written, not blanks: a feature that is
            # a segment and nothing else is remembered by exactly this stamp.
            props=ledger_module.stamp(
                feature.id, "segment",
                ledger_module.segment_signature(spec), name),
        )
        self.wrote = True

    def _resolve_ref(self, ref: str | None) -> str | None:
        if not ref:
            return None
        if ref.startswith("@"):
            return self.entity_of.get(ref[1:])
        return ref if self.world.get_entity(ref) else None

    def _take_back_sayings(self, feature_id: str, entity_id: str | None) -> None:
        """What this feature asserted last time, deleted before it says it again.

        Re-drawing used to only delete the *shapes*: every re-accepted plan then added
        another copy of the same `located_in` and another identical route segment, and
        the router quietly counted the road twice. Only the feature's own stamped
        sayings go; the writer's facts about the same entity are not the map's to touch.
        """
        for fact in guards.sorted_facts(
                self.world.facts_about(entity_id) if entity_id else ()):
            marker = (getattr(fact, "props", None) or {}).get(PROVENANCE_KEY) or {}
            if marker.get("feature") == feature_id:
                self.world.delete_fact(fact.id)
                self.wrote = True
        if self._old_segments is None:
            self._old_segments = self.world.route_segments()
        for segment in self._old_segments:
            marker = (segment.props or {}).get(PROVENANCE_KEY) or {}
            if marker.get("feature") == feature_id:
                self.world.delete_route_segment(segment.id)
                self.wrote = True

    # ---- clearing the last map -------------------------------------------

    def _retire(self, accepted: list[PlannedFeature]) -> None:
        """Take back what this run does not propose — and only what is still ours."""
        keep = {f.id for f in accepted}
        for feature_id in sorted(self.ledger):
            if feature_id in keep:
                continue
            row = self.ledger[feature_id]
            if row.pinned:
                continue
            # Only kinds this map actually looked at. Narrowing the brief is not a way
            # to delete everything else.
            kind = kind_of(feature_id)
            if kind is not None and not self.plan.brief.covers(kind):
                continue
            touched = ledger_module.writer_touched(
                self.world, row.entity_id, row.name_at_write,
                summary_at_write=row.summary_at_write, at=self.plan.brief.at)
            if touched:
                self._promote(feature_id, row)
                continue
            for geometry in row.geometries:
                self.world.delete_geometry(geometry.id)
                self.wrote = True
            if row.entity_id and self.world.get_entity(row.entity_id) is not None:
                self.world.delete_entity(row.entity_id)
                self.wrote = True
            self.outcomes.append(FeatureOutcome(
                feature_id=feature_id, name=row.name_at_write, op="retired",
                entity_id=row.entity_id, geometry_ids=row.geometry_ids,
                why="the new map does not have it"))

    def _promote(self, feature_id: str, row) -> None:
        """Hand a generated thing over to the writer, rather than deleting it.

        They have renamed it, dated it, or written about it. It is theirs now, and the
        only right move is to stop claiming it.
        """
        entity = self.world.get_entity(row.entity_id) if row.entity_id else None
        name = entity.name if entity else row.name_at_write
        if entity is not None:
            kept = [t for t in entity.tags
                    if t != "generated-map" and not t.startswith(ledger_module.TAG_PREFIX)]
            self.world.update_entity(entity.id, tags=kept)
            self.wrote = True
        self.outcomes.append(FeatureOutcome(
            feature_id=feature_id, name=name, op="promoted",
            entity_id=row.entity_id, geometry_ids=row.geometry_ids,
            why="you have made this yours, so the map has stopped claiming it"))
        self.findings.append(note(
            "self-check",
            f"“{name}” was generated, but you have since made it your own, so it has "
            f"been left alone rather than redrawn."))

    def _keep_the_ground(self) -> None:
        """Store the surface the accepted map was drawn from.

        Written whenever anything of a plan is accepted, and not per feature: the ground
        is not one of the things being accepted or rejected. A writer who turns off half
        the rivers has not asked for a different continent, and a writer who accepts
        nothing at all has not asked for one either — which is why this sits inside the
        same transaction as the writes and behind the same "did anything happen" test.
        """
        terrain = self.plan.terrain
        if terrain is None or not terrain.fields:
            return
        self.world.save_terrain(
            seed=terrain.seed, size=terrain.size, span=terrain.span,
            origin_x=terrain.origin_x, origin_y=terrain.origin_y,
            sea_level=terrain.sea_level, fields=terrain.fields)

    def _remember(self) -> None:
        keep = {feature_id: answer for feature_id, answer in self.answers.items()
                if not answer.accept or answer.name or answer.pinned}
        if keep and save_standing(self.world, keep):
            self.wrote = True


def outcomes_by_op(report: ApplyReport) -> dict[str, list[FeatureOutcome]]:
    grouped: dict[str, list[FeatureOutcome]] = {}
    for outcome in report.outcomes:
        grouped.setdefault(outcome.op, []).append(outcome)
    return grouped
