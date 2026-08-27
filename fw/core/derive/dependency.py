"""Dependency analysis: "why does this matter?" and "what if it disappears?"

Spec §51, §52, §84, §85 and §86 all ask variants of one question — what is this thing
load-bearing for? — and §66/§67 govern how the answer must be presented:

    Derived information must show how it was calculated.
    …
    Avoid unexplained black-box conclusions.

So every finding here carries its evidence and is explicitly labelled as inference rather
than canon. §14 is blunt about it: derived information is "suggestions rather than
unquestionable truth". The writer decides what is true; this only points at what the model
already says.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from fw.core.genealogy.kinship import Genealogy
from fw.core.geo.routing import Router
from fw.core.succession.engine import SuccessionEngine
from fw.core.world import World


@dataclass
class Finding:
    """One derived observation, with the reason it was drawn.

    `evidence` is not decoration. A writer shown "Greyhaven is strategically vital" with no
    reasoning has learned nothing they can act on or disagree with; shown "because it is the
    only harbour in the Northmarch and House Marr's iron leaves through it", they can.
    """

    text: str
    weight: int
    evidence: list[str] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)
    kind: str = "inference"


class DependencyAnalyst:
    def __init__(self, world: World) -> None:
        self.world = world

    # ------------------------------------------------------------- §51

    def why_it_matters(self, entity_id: str, day: int) -> dict:
        """§51: select anything and ask why it is important.

        The brief's own examples are a bridge ("controls a major river crossing, links a
        grain region to the capital, owned by House Veyne, produces toll revenue") and a
        person ("third in succession, heir to two estates, marriage connects rival houses,
        knows a royal secret, commands a border army"). Both are answered by reading what
        the world already asserts, not by inventing significance.
        """
        entity = self.world.get_entity(entity_id)
        if entity is None:
            return {"error": f"no entity {entity_id}"}

        findings: list[Finding] = []
        findings += self._authored_importance(entity_id, day)
        findings += self._structural_importance(entity_id, day)
        if entity.type_key == "person":
            findings += self._personal_importance(entity_id, day)
        else:
            findings += self._place_importance(entity_id, day)

        findings.sort(key=lambda f: -f.weight)
        return {
            "entity": {"id": entity.id, "name": entity.name, "type_key": entity.type_key},
            "day": day,
            "findings": [asdict(f) for f in findings],
            "note": "Derived from the world model. Suggestions, not canon (§14, §66).",
        }

    def _authored_importance(self, entity_id: str, day: int) -> list[Finding]:
        """Things the writer said outright rank above anything inferred."""
        out = []
        entity = self.world.get_entity(entity_id)
        if entity and entity.summary:
            out.append(Finding(
                entity.summary, weight=100, kind="authored",
                evidence=["Written by you in this entity's summary."],
                entity_ids=[entity_id],
            ))
        for fact in self.world.facts_where(subject_id=entity_id, at=day):
            if fact.predicate_key in ("strategic_value", "prestige", "wealth",
                                      "military_strength", "defensibility"):
                out.append(Finding(
                    f"{fact.predicate_key.replace('_', ' ').capitalize()}: {fact.value}",
                    weight=60, kind="authored",
                    evidence=["Recorded directly on this entity."],
                    entity_ids=[entity_id],
                ))
        return out

    def _structural_importance(self, entity_id: str, day: int) -> list[Finding]:
        out = []
        incoming = self.world.facts_where(object_id=entity_id, at=day)

        contested = [f for f in incoming if f.predicate_key == "claims"]
        if contested:
            names = [self._name(f.subject_id) for f in contested]
            out.append(Finding(
                f"Contested: claimed by {', '.join(names)}.",
                weight=90,
                evidence=[f"{n} has a standing claim on it." for n in names],
                entity_ids=[f.subject_id for f in contested] + [entity_id],
            ))

        authorities = {
            f.predicate_key: self._name(f.subject_id)
            for f in incoming
            if f.predicate_key in ("legally_owns", "administers", "occupies", "taxes")
        }
        if len(authorities) > 1 and len(set(authorities.values())) > 1:
            described = ", ".join(
                f"{who} {what.replace('_', ' ')} it" for what, who in authorities.items()
            )
            out.append(Finding(
                f"Authority over it is divided: {described}.",
                weight=85,
                evidence=["Different parties hold ownership, administration, occupation "
                          "and taxation — a standing source of friction (§11)."],
                entity_ids=[entity_id],
            ))

        dependants = [f for f in incoming if f.predicate_key == "depends_on"]
        if dependants:
            names = [self._name(f.subject_id) for f in dependants]
            out.append(Finding(
                f"{len(dependants)} other things depend on it: {', '.join(names)}.",
                weight=80,
                evidence=[f"{n} is recorded as depending on it." for n in names],
                entity_ids=[f.subject_id for f in dependants],
            ))
        return out

    def _personal_importance(self, entity_id: str, day: int) -> list[Finding]:
        out: list[Finding] = []

        # Position in any line of succession — the brief's own first example.
        engine = SuccessionEngine(self.world)
        for title in self.world.titles():
            try:
                result = engine.compute(title.id, day, limit=8)
            except ValueError:
                continue
            for claimant in result.line:
                if claimant.id == entity_id:
                    out.append(Finding(
                        f"Number {claimant.position} in line for {title.name}.",
                        weight=95 - claimant.position,
                        evidence=[
                            f"Computed under {result.law.label}"
                            + (f", {claimant.note}" if claimant.note else "") + "."
                        ],
                        entity_ids=[entity_id, title.id],
                    ))

        held = self.world.titles_held_by(entity_id, at=day)
        if held:
            out.append(Finding(
                "Holds " + ", ".join(t.name for t in held) + ".",
                weight=88, kind="authored",
                evidence=["Recorded as the current holder."],
                entity_ids=[entity_id] + [t.id for t in held],
            ))

        # Secrets: both what they know and what is known about them.
        knows = []
        exposed = []
        for secret in self.world.secrets():
            for state in self.world.knowledge_of(secret.id, at=day):
                if state.observer_id == entity_id and state.stance == "knows":
                    knows.append(secret.name)
            if secret.about_id == entity_id:
                holders = [
                    self._name(s.observer_id)
                    for s in self.world.knowledge_of(secret.id, stance="knows", at=day)
                ]
                if holders:
                    exposed.append((secret.name, holders))
        if knows:
            out.append(Finding(
                "Knows " + ", ".join(f"“{s}”" for s in knows) + ".",
                weight=82,
                evidence=["Holding a secret is leverage, and a reason to be silenced."],
                entity_ids=[entity_id],
            ))
        for name, holders in exposed:
            out.append(Finding(
                f"Vulnerable: “{name}” is about them, and {len(holders)} "
                f"{'person knows' if len(holders) == 1 else 'people know'} it "
                f"({', '.join(holders)}).",
                weight=86,
                evidence=["Anyone holding this secret has power over them."],
                entity_ids=[entity_id],
            ))

        # A marriage that bridges rival houses (§51's own example).
        genealogy = Genealogy(self.world)
        for spouse_id in genealogy.spouses_of(entity_id):
            mine = self._houses_of(entity_id, day)
            theirs = self._houses_of(spouse_id, day)
            for a in mine:
                for b in theirs:
                    if a != b and self._are_rivals(a, b, day):
                        out.append(Finding(
                            f"Their marriage to {self._name(spouse_id)} bridges "
                            f"{self._name(a)} and {self._name(b)}, who are rivals.",
                            weight=84,
                            evidence=[f"{self._name(a)} and {self._name(b)} are recorded "
                                      f"as rivals, and this marriage crosses them."],
                            entity_ids=[entity_id, spouse_id, a, b],
                        ))
        return out

    def _place_importance(self, entity_id: str, day: int) -> list[Finding]:
        out: list[Finding] = []
        segments = [
            s for s in self.world.route_segments()
            if entity_id in (s.from_entity_id, s.to_entity_id)
        ]
        if len(segments) >= 3:
            out.append(Finding(
                f"A junction: {len(segments)} routes meet here.",
                weight=75,
                evidence=[f"{len(segments)} road or river segments connect to it."],
                entity_ids=[entity_id],
            ))
        elif len(segments) == 1:
            other = (segments[0].to_entity_id if segments[0].from_entity_id == entity_id
                     else segments[0].from_entity_id)
            out.append(Finding(
                f"A dead end: reachable only from {self._name(other)}.",
                weight=70,
                evidence=["Only one route touches it, so cutting that route isolates it."],
                entity_ids=[entity_id, other],
            ))

        imports = self.world.facts_where("imports", subject_id=entity_id, at=day)
        if imports:
            names = [self._name(f.object_id) for f in imports]
            out.append(Finding(
                f"Cannot feed or supply itself: imports {', '.join(names)}.",
                weight=72,
                evidence=["Recorded as importing these, so a blockade or a bad road bites."],
                entity_ids=[entity_id],
            ))

        exports = self.world.facts_where("exports", subject_id=entity_id, at=day)
        if exports:
            out.append(Finding(
                f"Exports {', '.join(self._name(f.object_id) for f in exports)}.",
                weight=65, kind="authored",
                evidence=["Its exports are somebody else's imports."],
                entity_ids=[entity_id],
            ))
        return out

    # ------------------------------------------------------------- §52, §85

    def what_if_removed(self, entity_id: str, day: int) -> dict:
        """§52 and §85: simulate removal and report what depends on it.

        The brief's example is removing Greyhaven's port: northern exports decline, grain
        imports get dearer, House Marr loses customs revenue, trade shifts to Blackmere.
        Everything below is computed by re-running the real engines with the entity taken
        out, so the consequences are the model's own, not a canned list.
        """
        entity = self.world.get_entity(entity_id)
        if entity is None:
            return {"error": f"no entity {entity_id}"}

        consequences: list[Finding] = []

        # Who loses a route, and by how much.
        router = Router(self.world)
        surviving = [
            s for s in router.segments
            if entity_id not in (s.from_entity_id, s.to_entity_id)
        ]
        settlements = [
            e for e in self.world.entities("settlement")
            if e.id != entity_id and e.exists_on(day)
        ]
        for origin in settlements:
            for destination in settlements:
                if origin.id >= destination.id:
                    continue
                before = router.travel_time(origin.id, destination.id, day=day)
                router.segments = surviving
                after = router.travel_time(origin.id, destination.id, day=day)
                router.segments = self.world.route_segments()
                if before is None:
                    continue
                if after is None:
                    consequences.append(Finding(
                        f"{origin.name} and {destination.name} would be cut off from "
                        f"each other entirely.",
                        weight=95,
                        evidence=[f"Every route between them passes through "
                                  f"{entity.name}."],
                        entity_ids=[origin.id, destination.id],
                    ))
                elif after > before * 1.4:
                    consequences.append(Finding(
                        f"{origin.name} to {destination.name} would take "
                        f"{after:.1f} days instead of {before:.1f}.",
                        weight=70,
                        evidence=[f"The direct route runs through {entity.name}; "
                                  f"the detour costs {after - before:.1f} days."],
                        entity_ids=[origin.id, destination.id],
                    ))

        # Who is left short of something.
        for fact in self.world.facts_where("exports", subject_id=entity_id, at=day):
            resource = self._name(fact.object_id)
            importers = [
                self._name(f.subject_id)
                for f in self.world.facts_where("imports", object_id=fact.object_id, at=day)
                if f.subject_id != entity_id
            ]
            if importers:
                consequences.append(Finding(
                    f"{resource} would stop flowing to {', '.join(importers)}.",
                    weight=85,
                    evidence=[f"{entity.name} exports {resource}, and they import it."],
                    entity_ids=[entity_id],
                ))

        for fact in self.world.facts_where("depends_on", object_id=entity_id, at=day):
            dependant = self._name(fact.subject_id)
            consequences.append(Finding(
                f"{dependant} depends on it directly and would be affected.",
                weight=90,
                evidence=[fact.note or f"{dependant} is recorded as depending on it."],
                entity_ids=[fact.subject_id],
            ))

        # Who loses standing or revenue.
        for fact in self.world.facts_where(object_id=entity_id, at=day):
            if fact.predicate_key in ("legally_owns", "taxes", "administers"):
                holder = self._name(fact.subject_id)
                verb = {"legally_owns": "would lose it outright",
                        "taxes": "would lose the revenue from it",
                        "administers": "would lose the office of administering it"}
                consequences.append(Finding(
                    f"{holder} {verb[fact.predicate_key]}.",
                    weight=80,
                    evidence=[f"{holder} currently {fact.predicate_key.replace('_',' ')} "
                              f"{entity.name}."],
                    entity_ids=[fact.subject_id, entity_id],
                ))

        # A person's removal reshuffles every line they stand in.
        if entity.type_key == "person":
            engine = SuccessionEngine(self.world)
            for title in self.world.titles():
                try:
                    before = engine.compute(title.id, day, limit=6)
                    after = engine.compute(title.id, day, assume_dead={entity_id}, limit=6)
                except ValueError:
                    continue
                if before.names() != after.names() and after.line:
                    consequences.append(Finding(
                        f"Succession to {title.name} would become: "
                        + ", ".join(c.name for c in after.line[:4]) + ".",
                        weight=92,
                        evidence=[
                            "Currently: " + ", ".join(c.name for c in before.line[:4]) + ".",
                            f"Recomputed under {before.law.label} with them removed.",
                        ],
                        entity_ids=[title.id],
                    ))

        # Deduplicate: several paths can arrive at the same sentence.
        seen: set[str] = set()
        unique: list[Finding] = []
        for finding in sorted(consequences, key=lambda f: -f.weight):
            if finding.text in seen:
                continue
            seen.add(finding.text)
            unique.append(finding)

        return {
            "entity": {"id": entity.id, "name": entity.name, "type_key": entity.type_key},
            "day": day,
            "consequences": [asdict(f) for f in unique],
            "note": "Analytical inference, not canon, until you accept it (§52).",
        }

    # ---- helpers ----------------------------------------------------------

    def _name(self, entity_id: str | None) -> str:
        if not entity_id:
            return "?"
        entity = self.world.get_entity(entity_id)
        return entity.name if entity else entity_id

    def _houses_of(self, person_id: str, day: int) -> list[str]:
        return [
            f.object_id
            for f in self.world.facts_where("member_of", subject_id=person_id, at=day)
            if f.object_id
        ]

    def _are_rivals(self, a_id: str, b_id: str, day: int) -> bool:
        for predicate in ("rival_of", "at_war_with"):
            for fact in self.world.facts_where(predicate, subject_id=a_id, at=day):
                if fact.object_id == b_id:
                    return True
            for fact in self.world.facts_where(predicate, subject_id=b_id, at=day):
                if fact.object_id == a_id:
                    return True
        return False
