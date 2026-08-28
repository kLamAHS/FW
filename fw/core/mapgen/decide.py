"""What the writer said about the last map, remembered.

Propose-then-accept is only worth anything if the answers stick. A writer who rejects a
river, regenerates, and is offered the same river again has not been given control; they
have been given a chore. So every answer is written to `app_state` under the feature's
stable id, and every later plan reads them back.

Three levels, in order of who wins:

  1. **What the map suggests.** Siting a place the writer already made is accepted by
     default; inventing a new one is not (§66).
  2. **What the writer decided before.** Remembered, and branch-scoped, because a
     what-if may keep what canon rejected.
  3. **What they are saying now.** The request in front of us.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from fw.core.mapgen.findings import Finding, note
from fw.core.mapgen.plan import MapPlan
from fw.core.world import World

NAMESPACE = "mapgen.decisions"


@dataclass(frozen=True)
class Decision:
    """One answer about one feature."""

    feature_id: str
    accept: bool = True
    name: str | None = None      # ignored when the feature is not renameable
    pinned: bool = False         # taken out of the generator's hands for good
    note: str = ""

    def as_dict(self) -> dict:
        return {"accept": self.accept, "name": self.name,
                "pinned": self.pinned, "note": self.note}

    @classmethod
    def from_dict(cls, feature_id: str, raw: dict) -> Decision:
        return cls(feature_id=feature_id, accept=bool(raw.get("accept", True)),
                   name=raw.get("name"), pinned=bool(raw.get("pinned", False)),
                   note=str(raw.get("note") or ""))


@dataclass(frozen=True)
class DecisionSet:
    """Every answer about one plan."""

    plan_id: str
    decisions: tuple[Decision, ...] = ()

    def get(self, feature_id: str) -> Decision | None:
        for decision in self.decisions:
            if decision.feature_id == feature_id:
                return decision
        return None

    def accepted(self) -> frozenset[str]:
        return frozenset(d.feature_id for d in self.decisions if d.accept)

    def with_decision(self, decision: Decision) -> DecisionSet:
        kept = tuple(d for d in self.decisions if d.feature_id != decision.feature_id)
        return replace(self, decisions=tuple(sorted(kept + (decision,),
                                                    key=lambda d: d.feature_id)))

    @classmethod
    def defaults(cls, plan: MapPlan) -> DecisionSet:
        """What the map suggests, before the writer has said anything."""
        return cls(plan_id=plan.plan_id, decisions=tuple(
            Decision(feature_id=f.id, accept=f.default_accept)
            for f in plan.features))

    @classmethod
    def accept_all(cls, plan: MapPlan) -> DecisionSet:
        return cls(plan_id=plan.plan_id, decisions=tuple(
            Decision(feature_id=f.id, accept=True) for f in plan.features))

    @classmethod
    def from_dict(cls, raw: dict) -> DecisionSet:
        return cls(plan_id=str(raw.get("plan_id") or ""), decisions=tuple(
            sorted((Decision.from_dict(str(d["feature_id"]), d)
                    for d in raw.get("decisions") or ()),
                   key=lambda d: d.feature_id)))

    def as_dict(self) -> dict:
        return {"plan_id": self.plan_id,
                "decisions": [{"feature_id": d.feature_id, **d.as_dict()}
                              for d in self.decisions]}


# ---- what the writer decided before ----------------------------------------

def load_standing(world: World) -> DecisionSet:
    """Every answer the writer has given about generated features, on this timeline."""
    remembered = world.recall_all(NAMESPACE)
    return DecisionSet(plan_id="", decisions=tuple(
        Decision.from_dict(feature_id, raw or {})
        for feature_id, raw in sorted(remembered.items())))


def save_standing(world: World, decisions: dict[str, Decision]) -> bool:
    """Remember the answers, so the next plan does not ask again.

    Only the answers worth remembering: an acceptance that matches what the map would
    have done anyway is not a decision, and storing it would fill the file with noise
    and make "the writer chose this" impossible to see.

    Returns whether anything actually changed, because an apply that stored nothing new
    must report no action at all rather than an empty one for the writer to undo.
    """
    already = world.recall_all(NAMESPACE)
    wrote = False
    for feature_id in sorted(decisions):
        decision = decisions[feature_id]
        if decision.accept and not decision.name and not decision.pinned:
            continue
        # Re-saving an identical answer is not a decision; it is a revision row, and
        # it would make applying the same plan twice look like a change to undo.
        if already.get(feature_id) == decision.as_dict():
            continue
        world.remember(NAMESPACE, feature_id, decision.as_dict())
        wrote = True
    return wrote


def resolve(plan: MapPlan, standing: DecisionSet,
            requested: DecisionSet | None) -> dict[str, Decision]:
    """One answer per feature: what the map suggests, overruled by what the writer said.

    A feature the request does not mention keeps its standing answer, and a feature with
    no standing answer keeps the map's suggestion — so a writer can accept a plan by
    saying nothing at all, and nothing they said before is quietly discarded.
    """
    out: dict[str, Decision] = {}
    for feature in plan.features:
        answer = Decision(feature_id=feature.id, accept=feature.default_accept)
        remembered = standing.get(feature.id)
        if remembered is not None:
            answer = remembered
        if requested is not None:
            asked = requested.get(feature.id)
            if asked is not None:
                answer = asked
        # A rename of the writer's own place is not the generator's to make.
        if answer.name and not feature.renameable:
            answer = replace(answer, name=None)
        # Anything a rejected feature needed goes with it.
        out[feature.id] = answer
    return _cascade(plan, out)


def _cascade(plan: MapPlan, answers: dict[str, Decision]) -> dict[str, Decision]:
    """Drop whatever depended on something that was turned down.

    A road to a town the writer rejected is a road to nowhere. Repeated until nothing
    changes, because a dependency chain can be several deep.
    """
    changed = True
    while changed:
        changed = False
        for feature in plan.features:
            answer = answers.get(feature.id)
            if answer is None or not answer.accept:
                continue
            for needed in feature.depends_on:
                dependency = answers.get(needed)
                if dependency is not None and not dependency.accept:
                    answers[feature.id] = replace(
                        answer, accept=False,
                        note=f"needs {needed}, which you turned down")
                    changed = True
                    break
    return answers


def carry(previous: DecisionSet, fresh: MapPlan) -> tuple[DecisionSet, list[Finding]]:
    """Move a set of answers onto a newly computed plan.

    Feature ids are derived from what a feature *is*, so most answers land. The ones
    that do not are said out loud rather than dropped: a writer whose rejection silently
    stopped applying would find the river back on their map with no explanation.
    """
    live = {f.id for f in fresh.features}
    kept = tuple(d for d in previous.decisions if d.feature_id in live)
    lost = [d for d in previous.decisions if d.feature_id not in live]
    findings: list[Finding] = []
    if lost:
        findings.append(note(
            "self-check",
            f"{len(lost)} of your earlier choices were about things this map no longer "
            f"proposes, so they no longer apply.",
            subjects=tuple(sorted(d.feature_id for d in lost)),
        ))
    return DecisionSet(plan_id=fresh.plan_id, decisions=kept), findings
