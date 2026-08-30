"""The continuity engine (spec §46, §47, §48).

Every rule is a small object that reads the world and yields violations. Adding a check
means adding a rule, and a world can suppress any individual finding with a reason, because
§46 insists intentional exceptions must be possible: a writer who *means* a dead man to
appear at the feast should be able to say so once and not be nagged again.

Severity is not decoration. The brief asks for levels, and the uncertain-date model earns
them honestly: when two dates *cannot* overlap on any reading, that is an ERROR; when they
merely might not, that is a WARNING. A checker that cried wolf over every vague date would
be turned off within a day, and then it would catch nothing at all.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum

from fw.core.genealogy.kinship import Genealogy
from fw.core.geo.routing import Router
from fw.core.world import World


class Severity(str, Enum):
    ERROR = "error"        # cannot be true on any reading of the data
    WARNING = "warning"    # suspicious, but the dates are loose enough to allow it
    NOTICE = "notice"      # probably fine; worth a glance

    @property
    def rank(self) -> int:
        return {"error": 3, "warning": 2, "notice": 1}[self.value]


@dataclass
class Violation:
    rule_key: str
    severity: Severity
    message: str
    entity_ids: tuple[str, ...] = ()
    day: int | None = None
    detail: str = ""

    @property
    def fingerprint(self) -> str:
        """A stable identity for this specific finding, so it can be suppressed.

        Hashed from the rule and the entities involved rather than the message text, so
        rewording a message does not resurrect every violation a writer has dismissed.
        """
        return self.fingerprint_as(self.rule_key)

    def fingerprint_as(self, rule_key: str) -> str:
        """The same identity, computed under another rule key.

        The key is *inside* the hash, so a rule that is renamed cannot have its stored
        suppressions rewritten in SQL: the column would move and the hash would not, and
        the row would then match nothing under either name. Renaming is carried here
        instead, by taking the old fingerprint and looking for that too.
        """
        basis = f"{rule_key}|{'|'.join(sorted(self.entity_ids))}|{self.day}"
        return hashlib.sha256(basis.encode()).hexdigest()[:16]


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)
    suppressed: int = 0
    checked_rules: tuple[str, ...] = ()
    #: rule key -> the sentence a writer should read. The key is the suppression's
    #: identity and a machine's name for the check; showing it to the writer is why
    #: renaming a rule was a user-facing change rather than a tidy-up.
    labels: dict[str, str] = field(default_factory=dict)

    def by_severity(self, severity: Severity) -> list[Violation]:
        return [v for v in self.violations if v.severity is severity]

    @property
    def errors(self) -> list[Violation]:
        return self.by_severity(Severity.ERROR)

    @property
    def warnings(self) -> list[Violation]:
        return self.by_severity(Severity.WARNING)

    def summary(self) -> str:
        if not self.violations:
            return f"No continuity problems found ({len(self.checked_rules)} checks run)."
        counts = {s: len(self.by_severity(s)) for s in Severity}
        parts = [f"{n} {s.value}{'s' if n != 1 else ''}" for s, n in counts.items() if n]
        text = ", ".join(parts)
        if self.suppressed:
            text += f" ({self.suppressed} suppressed)"
        return text


class Rule:
    """Base class. A rule reads the world and yields violations."""

    key = "rule"
    label = "Rule"
    description = ""
    #: Keys this rule has had before. A writer's dismissal is stored against the key it
    #: was filed under and fingerprinted with it, so a rename that did not carry these
    #: would silently resurrect every warning they had already looked at and decided was
    #: intentional — §46's promise, quietly broken with no error anywhere.
    legacy_keys: tuple[str, ...] = ()

    def check(self, ctx: Context) -> Iterator[Violation]:  # pragma: no cover
        raise NotImplementedError


@dataclass
class Context:
    """Shared, pre-loaded state so twenty rules do not each re-read the world."""

    world: World
    genealogy: Genealogy
    router: Router

    def name(self, entity_id: str | None) -> str:
        if not entity_id:
            return "someone"
        entity = self.world.get_entity(entity_id)
        return entity.name if entity else entity_id

    def date(self, day: int | None) -> str:
        return "an unknown date" if day is None else self.world.calendar.format(day)


# ---------------------------------------------------------------- lifespan rules

class DeadCharacterActs(Rule):
    key = "dead_character_acts"
    label = "A dead character takes part in something"
    description = "§46/§47: 'Elia is listed at the Battle of Orren in 231 but died in 229.'"

    def check(self, ctx: Context) -> Iterator[Violation]:
        for event in ctx.world.events():
            if event.start_day is None:
                continue
            for entity, role in ctx.world.event_participants(event.id):
                if entity.exists_to is not None and event.start_day > entity.exists_to:
                    yield Violation(
                        self.key, Severity.ERROR,
                        f"{entity.name} takes part in {event.name} on "
                        f"{ctx.date(event.start_day)}, but died on "
                        f"{ctx.date(entity.exists_to)}.",
                        entity_ids=(entity.id, event.id), day=event.start_day,
                        detail=f"role: {role}",
                    )


class UnbornCharacterActs(Rule):
    key = "unborn_character_acts"
    label = "A character acts before being born"

    def check(self, ctx: Context) -> Iterator[Violation]:
        for event in ctx.world.events():
            if event.start_day is None:
                continue
            for entity, _ in ctx.world.event_participants(event.id):
                if entity.exists_from is not None and event.start_day < entity.exists_from:
                    yield Violation(
                        self.key, Severity.ERROR,
                        f"{entity.name} takes part in {event.name} on "
                        f"{ctx.date(event.start_day)}, but was not born until "
                        f"{ctx.date(entity.exists_from)}.",
                        entity_ids=(entity.id, event.id), day=event.start_day,
                    )


class DeadCharacterInScene(Rule):
    key = "dead_character_in_scene"
    label = "A dead character appears in a scene"
    description = "§46: 'Dead character appears without explanation.'"

    def check(self, ctx: Context) -> Iterator[Violation]:
        for scene in ctx.world.scenes():
            if scene.day is None:
                continue
            for entity in ctx.world.scene_participants(scene.id):
                if entity.exists_to is not None and scene.day > entity.exists_to:
                    yield Violation(
                        self.key, Severity.ERROR,
                        f"{entity.name} appears in “{scene.title}” on "
                        f"{ctx.date(scene.day)}, but died on {ctx.date(entity.exists_to)}.",
                        entity_ids=(entity.id, scene.id), day=scene.day,
                    )
                if entity.exists_from is not None and scene.day < entity.exists_from:
                    yield Violation(
                        self.key, Severity.ERROR,
                        f"{entity.name} appears in “{scene.title}” on "
                        f"{ctx.date(scene.day)}, before being born on "
                        f"{ctx.date(entity.exists_from)}.",
                        entity_ids=(entity.id, scene.id), day=scene.day,
                    )


class MarriagePredatesBirth(Rule):
    key = "marriage_predates_birth"
    label = "A marriage predates a participant's birth"
    description = "§46: 'Marriage predates one participant's birth.'"

    def check(self, ctx: Context) -> Iterator[Violation]:
        for fact in ctx.world.facts_where("married_to"):
            if fact.valid_from is None:
                continue
            for pid in (fact.subject_id, fact.object_id):
                person = ctx.world.get_entity(pid) if pid else None
                if (person and person.exists_from is not None
                        and fact.valid_from < person.exists_from):
                    yield Violation(
                        self.key, Severity.ERROR,
                        f"A marriage beginning {ctx.date(fact.valid_from)} predates "
                        f"{person.name}'s birth on {ctx.date(person.exists_from)}.",
                        entity_ids=(fact.subject_id, fact.object_id or ""),
                        day=fact.valid_from,
                    )


class ParentBornAfterChild(Rule):
    key = "parent_born_after_child"
    label = "A parent is younger than their child"

    def check(self, ctx: Context) -> Iterator[Violation]:
        g = ctx.genealogy
        for child_id, person in g.people.items():
            if person.born is None:
                continue
            for parent_id in g.parentage(child_id).all_ids:
                parent = g.people.get(parent_id)
                if parent is None or parent.born is None:
                    continue
                if parent.born >= person.born:
                    yield Violation(
                        self.key, Severity.ERROR,
                        f"{parent.name} is recorded as a parent of {person.name}, "
                        f"but was born {ctx.date(parent.born)} — not before "
                        f"{ctx.date(person.born)}.",
                        entity_ids=(parent_id, child_id), day=person.born,
                    )


class ImplausibleParentAge(Rule):
    key = "implausible_parent_age"
    label = "A parent is implausibly young"
    description = "§48: 'characters cannot give birth before a configurable minimum age.'"

    def __init__(self, minimum_age_years: int = 12) -> None:
        self.minimum_age_years = minimum_age_years

    def check(self, ctx: Context) -> Iterator[Violation]:
        g = ctx.genealogy
        year = ctx.world.calendar.common_year_days
        threshold = self.minimum_age_years * year
        for child_id, person in g.people.items():
            if person.born is None:
                continue
            for parent_id in g.parentage(child_id).all_ids:
                parent = g.people.get(parent_id)
                if parent is None or parent.born is None:
                    continue
                age_days = person.born - parent.born
                if 0 < age_days < threshold:
                    yield Violation(
                        self.key, Severity.WARNING,
                        f"{parent.name} would have been about "
                        f"{age_days // year} when {person.name} was born.",
                        entity_ids=(parent_id, child_id), day=person.born,
                        detail=f"minimum age is set to {self.minimum_age_years}",
                    )


class PosthumousChild(Rule):
    key = "posthumous_child"
    label = "A child born long after a parent's death"

    def check(self, ctx: Context) -> Iterator[Violation]:
        g = ctx.genealogy
        year = ctx.world.calendar.common_year_days
        for child_id, person in g.people.items():
            if person.born is None:
                continue
            for parent_id in g.parentage(child_id).biological:
                parent = g.people.get(parent_id)
                if parent is None or parent.died is None:
                    continue
                if person.born > parent.died + year:
                    yield Violation(
                        self.key, Severity.WARNING,
                        f"{person.name} was born {ctx.date(person.born)}, more than a "
                        f"year after {parent.name} died on {ctx.date(parent.died)}.",
                        entity_ids=(parent_id, child_id), day=person.born,
                    )


# ---------------------------------------------------------------- title rules

class TitleHeldByTheDead(Rule):
    key = "title_held_by_the_dead"
    label = "A title is held by someone dead"
    description = "§48: 'title holders must be alive.'"

    def check(self, ctx: Context) -> Iterator[Violation]:
        for title in ctx.world.titles():
            for holding in ctx.world.title_holdings(title.id):
                holder = ctx.world.get_entity(holding.holder_id)
                if holder is None or holder.exists_to is None:
                    continue
                if holding.to_day is None or holding.to_day > holder.exists_to:
                    yield Violation(
                        self.key, Severity.ERROR,
                        f"{holder.name} holds {title.name} past their death on "
                        f"{ctx.date(holder.exists_to)}.",
                        entity_ids=(holder.id, title.id), day=holder.exists_to,
                    )


class TitleUsedBeforeCreation(Rule):
    key = "title_used_before_creation"
    label = "A title is used before it existed"
    description = "§46: 'Title is used before it was created.'"

    def check(self, ctx: Context) -> Iterator[Violation]:
        for title in ctx.world.titles():
            if title.created_on is None:
                continue
            for holding in ctx.world.title_holdings(title.id):
                if holding.from_day is not None and holding.from_day < title.created_on:
                    holder = ctx.world.get_entity(holding.holder_id)
                    yield Violation(
                        self.key, Severity.ERROR,
                        f"{ctx.name(holding.holder_id)} holds {title.name} from "
                        f"{ctx.date(holding.from_day)}, but the title was not created "
                        f"until {ctx.date(title.created_on)}.",
                        entity_ids=((holder.id if holder else ""), title.id),
                        day=holding.from_day,
                    )


class OverlappingTitleHolders(Rule):
    key = "overlapping_title_holders"
    label = "Two people hold one title at once"

    def check(self, ctx: Context) -> Iterator[Violation]:
        for title in ctx.world.titles():
            holdings = [h for h in ctx.world.title_holdings(title.id) if not h.disputed]
            for i, a in enumerate(holdings):
                for b in holdings[i + 1:]:
                    a_end = a.to_day if a.to_day is not None else float("inf")
                    b_end = b.to_day if b.to_day is not None else float("inf")
                    a_start = a.from_day if a.from_day is not None else float("-inf")
                    b_start = b.from_day if b.from_day is not None else float("-inf")
                    # Touching at a single day is a handover, not an overlap.
                    if a_start < b_end and b_start < a_end:
                        yield Violation(
                            self.key, Severity.WARNING,
                            f"{ctx.name(a.holder_id)} and {ctx.name(b.holder_id)} both "
                            f"hold {title.name} at the same time. If this is a disputed "
                            f"claim, mark one holding as disputed.",
                            entity_ids=(a.holder_id, b.holder_id, title.id),
                        )


# ---------------------------------------------------------------- place rules

class PlaceUsedBeforeItExisted(Rule):
    key = "place_used_before_existence"
    label = "A place is used before it existed"
    description = "§46: 'A road is used before construction.'"

    def check(self, ctx: Context) -> Iterator[Violation]:
        for scene in ctx.world.scenes():
            if scene.day is None or scene.location_id is None:
                continue
            place = ctx.world.get_entity(scene.location_id)
            if place is None or place.exists_from is None:
                continue
            if scene.day < place.exists_from:
                yield Violation(
                    self.key, Severity.ERROR,
                    f"“{scene.title}” is set at {place.name} on {ctx.date(scene.day)}, "
                    f"but {place.name} was not founded until "
                    f"{ctx.date(place.exists_from)}.",
                    entity_ids=(place.id, scene.id), day=scene.day,
                )

        for event in ctx.world.events():
            if event.start_day is None or event.location_id is None:
                continue
            place = ctx.world.get_entity(event.location_id)
            if place is None or place.exists_from is None:
                continue
            if event.start_day < place.exists_from:
                yield Violation(
                    self.key, Severity.ERROR,
                    f"{event.name} happens at {place.name} on "
                    f"{ctx.date(event.start_day)}, before it existed.",
                    entity_ids=(place.id, event.id), day=event.start_day,
                )


class RouteUsedBeforeBuilt(Rule):
    key = "route_used_before_built"
    label = "A road carries traffic before it was built"

    def check(self, ctx: Context) -> Iterator[Violation]:
        for segment in ctx.world.route_segments():
            if segment.built_on is None or segment.entity_id is None:
                continue
            road = ctx.world.get_entity(segment.entity_id)
            if road is None or road.exists_from is None:
                continue
            if segment.built_on < road.exists_from:
                yield Violation(
                    self.key, Severity.WARNING,
                    f"A stretch of {road.name} is dated {ctx.date(segment.built_on)}, "
                    f"before the road itself ({ctx.date(road.exists_from)}).",
                    entity_ids=(road.id,), day=segment.built_on,
                )


class SettlementWithoutRegion(Rule):
    key = "settlement_without_region"
    label = "A settlement belongs nowhere"
    description = "§48: 'settlement must belong to a region.'"

    def check(self, ctx: Context) -> Iterator[Violation]:
        for town in ctx.world.entities("settlement"):
            if not ctx.world.facts_where("located_in", subject_id=town.id):
                yield Violation(
                    self.key, Severity.NOTICE,
                    f"{town.name} is not placed in any region.",
                    entity_ids=(town.id,),
                )


# ---------------------------------------------------------------- travel rules

class ImpossibleJourney(Rule):
    key = "impossible_journey"
    label = "A journey the timeline does not allow"
    description = ("§46: 'Journey requires three days but scene timeline allows one.' "
                   "The hardest check in the brief, and the reason routing is core.")

    def __init__(self, profile: str = "horse") -> None:
        self.profile = profile

    def check(self, ctx: Context) -> Iterator[Violation]:
        scenes = [s for s in ctx.world.scenes() if s.day is not None and s.location_id]
        by_person: dict[str, list] = {}
        for scene in scenes:
            for person in ctx.world.scene_participants(scene.id):
                by_person.setdefault(person.id, []).append(scene)

        for person_id, person_scenes in by_person.items():
            person_scenes.sort(key=lambda s: s.day)
            for first, second in zip(person_scenes, person_scenes[1:], strict=False):
                if first.location_id == second.location_id:
                    continue
                available = second.day - (first.end_day or first.day)
                needed = ctx.router.travel_time(
                    first.location_id, second.location_id,
                    profile=self.profile, day=first.day,
                )
                if needed is None:
                    yield Violation(
                        self.key, Severity.WARNING,
                        f"{ctx.name(person_id)} moves from "
                        f"{ctx.name(first.location_id)} to "
                        f"{ctx.name(second.location_id)}, but no route connects them "
                        f"at that date.",
                        entity_ids=(person_id, first.id, second.id), day=second.day,
                    )
                    continue
                if needed > available:
                    # Comfortably impossible is an error; merely tight is a warning.
                    severity = (Severity.ERROR if available < needed * 0.6
                                else Severity.WARNING)
                    yield Violation(
                        self.key, severity,
                        f"{ctx.name(person_id)} is at {ctx.name(first.location_id)} on "
                        f"{ctx.date(first.day)} and at {ctx.name(second.location_id)} on "
                        f"{ctx.date(second.day)}, but the journey takes about "
                        f"{needed:.1f} days and only {available} are available.",
                        entity_ids=(person_id, first.id, second.id), day=second.day,
                        detail=f"by {self.profile}",
                    )


class PersonInTwoPlacesAtOnce(Rule):
    key = "person_in_two_places"
    label = "A character is in two places on the same day"
    description = "§46: 'Character appears in two distant locations on the same date.'"

    def check(self, ctx: Context) -> Iterator[Violation]:
        by_day: dict[tuple[str, int], set[str]] = {}
        for scene in ctx.world.scenes():
            if scene.day is None or not scene.location_id:
                continue
            for person in ctx.world.scene_participants(scene.id):
                by_day.setdefault((person.id, scene.day), set()).add(scene.location_id)
        for (person_id, day), places in by_day.items():
            if len(places) > 1:
                yield Violation(
                    self.key, Severity.ERROR,
                    f"{ctx.name(person_id)} is in {len(places)} different places on "
                    f"{ctx.date(day)}: "
                    + ", ".join(sorted(ctx.name(p) for p in places)) + ".",
                    entity_ids=(person_id,), day=day,
                )


# ---------------------------------------------------------------- knowledge rules

class KnowsBeforeLearning(Rule):
    key = "knows_before_learning"
    label = "A character knows a secret before learning it"
    description = "§46/§48: 'characters cannot know information before learning it.'"

    def check(self, ctx: Context) -> Iterator[Violation]:
        for secret in ctx.world.secrets():
            for state in ctx.world.knowledge_of(secret.id):
                if state.acquired_on is None:
                    continue
                observer = ctx.world.get_entity(state.observer_id)
                if (observer and observer.exists_from is not None
                        and state.acquired_on < observer.exists_from):
                    yield Violation(
                        self.key, Severity.ERROR,
                        f"{observer.name} learns “{secret.name}” on "
                        f"{ctx.date(state.acquired_on)}, before being born.",
                        entity_ids=(state.observer_id, secret.id),
                        day=state.acquired_on,
                    )
                if not state.acquired_from:
                    continue
                source = ctx.world.get_entity(state.acquired_from)
                if (source and source.exists_to is not None
                        and state.acquired_on > source.exists_to):
                    yield Violation(
                        self.key, Severity.ERROR,
                        f"{ctx.name(state.observer_id)} learns “{secret.name}” "
                        f"from {source.name} on {ctx.date(state.acquired_on)}, "
                        f"after {source.name} died.",
                        entity_ids=(state.observer_id, source.id),
                        day=state.acquired_on,
                    )


class SecretRevealedInSceneBeforeKnown(Rule):
    key = "scene_precedes_knowledge"
    label = "A character acts on a secret before learning it"

    def check(self, ctx: Context) -> Iterator[Violation]:
        for secret in ctx.world.secrets():
            for state in ctx.world.knowledge_of(secret.id, stance="knows"):
                if state.acquired_on is None or state.scene_id is None:
                    continue
                scene = ctx.world.get_scene(state.scene_id)
                if scene and scene.day is not None and scene.day < state.acquired_on:
                    yield Violation(
                        self.key, Severity.ERROR,
                        f"{ctx.name(state.observer_id)} acts on “{secret.name}” in "
                        f"“{scene.title}” before learning it.",
                        entity_ids=(state.observer_id, secret.id), day=scene.day,
                    )


# ---------------------------------------------------------------- fact rules

class FactOutlivesItsParticipants(Rule):
    key = "fact_outlives_participants"
    label = "A relationship continues past a participant's death"

    def check(self, ctx: Context) -> Iterator[Violation]:
        # Marriage ends at death in most worlds, but plenty of relationships legitimately
        # outlive their subject ("buried at", "founded by"), so this is narrow on purpose.
        for predicate in ("vassal_of", "married_to", "commands", "administers"):
            for fact in ctx.world.facts_where(predicate):
                if fact.valid_to is None:
                    continue
                subject = ctx.world.get_entity(fact.subject_id)
                if subject is None or subject.exists_to is None:
                    continue
                if subject.type_key == "person" and fact.valid_to > subject.exists_to:
                    yield Violation(
                        self.key, Severity.WARNING,
                        f"{subject.name}'s “{predicate}” continues to "
                        f"{ctx.date(fact.valid_to)}, past their death on "
                        f"{ctx.date(subject.exists_to)}.",
                        entity_ids=(subject.id,), day=fact.valid_to,
                    )


class ContradictoryExclusiveControl(Rule):
    key = "contradictory_control"
    label = "Two owners of the same thing at the same time"
    description = "§11: administration and occupation may overlap; legal ownership may not."

    def check(self, ctx: Context) -> Iterator[Violation]:
        for predicate in ("legally_owns", "capital_of"):
            by_object: dict[str, list] = {}
            for fact in ctx.world.facts_where(predicate):
                if fact.object_id:
                    by_object.setdefault(fact.object_id, []).append(fact)
            for object_id, facts in by_object.items():
                for i, a in enumerate(facts):
                    for b in facts[i + 1:]:
                        if a.subject_id == b.subject_id:
                            continue
                        a_start = a.valid_from if a.valid_from is not None else float("-inf")
                        a_end = a.valid_to if a.valid_to is not None else float("inf")
                        b_start = b.valid_from if b.valid_from is not None else float("-inf")
                        b_end = b.valid_to if b.valid_to is not None else float("inf")
                        if a_start < b_end and b_start < a_end:
                            yield Violation(
                                self.key, Severity.WARNING,
                                f"{ctx.name(a.subject_id)} and {ctx.name(b.subject_id)} "
                                f"both legally own {ctx.name(object_id)} at the same "
                                f"time. If the ownership is contested, record one as a "
                                f"claim instead.",
                                entity_ids=(a.subject_id, b.subject_id, object_id),
                            )


class EffectPrecedesCause(Rule):
    """Its label, description and body all described a cause-and-effect check; only the
    class name and the key said something else — and the key is what the writer reads
    under every violation, and what a dismissal is filed against."""

    key = "effect_precedes_cause"
    legacy_keys = ("event_before_place_founded",)
    label = "An event predates its own cause"
    description = "§32: an effect must not precede its cause."

    def check(self, ctx: Context) -> Iterator[Violation]:
        events = {e.id: e for e in ctx.world.events()}
        # Branch-scoped like every other read. It was not: a causal link recorded on a
        # what-if raised a violation against canon, and canon's raised one against the
        # what-if, which is the single mistake the whole overlay model exists to prevent.
        scope, params = ctx.world.branch_scope()
        for row in ctx.world.db.query(
            f"SELECT cause_id, effect_id FROM causal_link "
            f"WHERE project_id = ? AND ({scope} OR branch_id IS NULL)",
            (ctx.world.project_id, *params),
        ):
            cause, effect = events.get(row["cause_id"]), events.get(row["effect_id"])
            if not cause or not effect:
                continue
            if cause.start_day is None or effect.start_day is None:
                continue
            if effect.start_day < cause.start_day:
                yield Violation(
                    self.key, Severity.ERROR,
                    f"“{effect.name}” ({ctx.date(effect.start_day)}) is recorded as an "
                    f"effect of “{cause.name}” ({ctx.date(cause.start_day)}), but "
                    f"happened first.",
                    entity_ids=(cause.id, effect.id), day=effect.start_day,
                )


DEFAULT_RULES: tuple[Rule, ...] = (
    DeadCharacterActs(),
    UnbornCharacterActs(),
    DeadCharacterInScene(),
    MarriagePredatesBirth(),
    ParentBornAfterChild(),
    ImplausibleParentAge(),
    PosthumousChild(),
    TitleHeldByTheDead(),
    TitleUsedBeforeCreation(),
    OverlappingTitleHolders(),
    PlaceUsedBeforeItExisted(),
    RouteUsedBeforeBuilt(),
    SettlementWithoutRegion(),
    ImpossibleJourney(),
    PersonInTwoPlacesAtOnce(),
    KnowsBeforeLearning(),
    SecretRevealedInSceneBeforeKnown(),
    FactOutlivesItsParticipants(),
    ContradictoryExclusiveControl(),
    EffectPrecedesCause(),
)


class ContinuityEngine:
    def __init__(self, world: World, rules: tuple[Rule, ...] = DEFAULT_RULES) -> None:
        self.world = world
        self.rules = rules

    def run(self, *, minimum: Severity = Severity.NOTICE,
            include_suppressed: bool = False) -> Report:
        ctx = Context(
            world=self.world,
            genealogy=Genealogy(self.world),
            router=Router(self.world),
        )
        suppressed = self.world.suppressions()
        report = Report(checked_rules=tuple(r.key for r in self.rules))
        # A town the map has suggested and the writer has not accepted is not part of
        # their world yet, and checking it against their world produces a page of
        # complaints about things they never wrote. An accepted one *is* theirs, keeps
        # its tag so the next run recognises its own work, and is checked like anything
        # else (§66).
        proposals = {e.id for e in self.world.entities() if e.is_a_map_proposal}

        labels: dict[str, str] = {}
        for rule in self.rules:
            for violation in rule.check(ctx):
                if violation.severity.rank < minimum.rank:
                    continue
                if proposals.intersection(violation.entity_ids):
                    continue
                if not include_suppressed and self._dismissed(
                        rule, violation, suppressed):
                    report.suppressed += 1
                    continue
                labels[violation.rule_key] = rule.label
                report.violations.append(violation)

        report.violations.sort(key=lambda v: (-v.severity.rank, v.rule_key, v.message))
        report.labels = labels
        return report

    def _dismissed(self, rule: Rule, violation: Violation,
                   suppressed: set[tuple[str, str]]) -> bool:
        """Whether the writer has already looked at this and said it was intentional.

        Checked under the rule's current name and under every name it has had. A match
        on an old one is re-filed under the new key, so the lookup costs nothing the
        second time and the stale row is simply never consulted again — which is the
        only way to carry a rename, because the key is hashed into the fingerprint and
        the hash's other inputs are not stored on the suppression row.
        """
        if (violation.rule_key, violation.fingerprint) in suppressed:
            return True
        for was in rule.legacy_keys:
            if (was, violation.fingerprint_as(was)) in suppressed:
                self.world.suppress(
                    rule.key, violation.fingerprint,
                    "carried over when this check was renamed")
                suppressed.add((violation.rule_key, violation.fingerprint))
                return True
        return False


def check(world: World, **kw) -> Report:
    return ContinuityEngine(world).run(**kw)
