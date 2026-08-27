"""Scene context and the narrative relevance engine (spec §44, §45, §96).

The brief's most concrete promise to a writer:

    Scene: Winter Feast at Greyhaven
    Participants: Mara, Tomas, Edric, Queen Sera, Prince Oren
    …
    The writer should not need to remember these facts manually.

So opening a scene surfaces the relationships, secrets, goals and recent history that bear
on it. §45 adds the essential constraint: *rank* facts by relevance and "avoid overwhelming
the writer with every fact in the database". A panel that dumps four hundred facts is the
same as no panel at all — the writer goes back to holding it in their head, which is the
one thing this application exists to prevent.

Relevance is therefore scored, not filtered. Every candidate fact earns points for the
things that make a fact matter *in this room, on this day*: it involves two people who are
both present, it is secret, it is recent, it bears on an active goal. Nothing is hidden
outright; things are ordered, and the tail is cut.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.genealogy.kinship import Genealogy
from fw.core.model.records import Entity, Event, Fact, Scene, Secret
from fw.core.model.vocabulary import inverse_of
from fw.core.world import World

# §11's authorities, which are what a scene's location most needs stated.
CONTROL_PREDICATES = ("legally_owns", "administers", "occupies", "taxes", "claims", "rules")

# Predicates whose presence in a room is inherently dramatic. Weighted because "X hates Y"
# earns its place at a dinner table more than "X speaks Rennish".
CHARGED_PREDICATES = {
    "feels_about": 6, "trusts": 6, "owes_debt_to": 6, "rival_of": 5,
    "at_war_with": 5, "fears": 5, "resents": 5, "killed": 8,
    "married_to": 4, "betrothed_to": 4, "loyal_to": 3, "protects": 3,
    "vassal_of": 2, "member_of": 1, "claims": 4, "allied_with": 2,
}


@dataclass
class ScoredFact:
    fact: Fact
    score: float
    reasons: tuple[str, ...]
    subject: Entity | None = None
    object: Entity | None = None

    def describe(self) -> str:
        subject = self.subject.name if self.subject else "?"
        target = self.object.name if self.object else (self.fact.value or "?")
        strength = f" ({self.fact.strength})" if self.fact.strength else ""
        return f"{subject} — {self.fact.predicate_key}{strength} → {target}"


@dataclass
class KnowledgeLine:
    secret: Secret
    observer: Entity
    stance: str
    about_observer: Entity | None = None
    note: str = ""

    def describe(self) -> str:
        if self.about_observer is not None:
            return (f"{self.observer.name} knows that "
                    f"{self.about_observer.name} knows “{self.secret.name}”")
        verb = {"knows": "knows", "believes": "believes", "suspects": "suspects",
                "misinformed": "is wrong about", "unaware": "does not know"}
        return f"{self.observer.name} {verb.get(self.stance, self.stance)} “{self.secret.name}”"


@dataclass
class SceneContext:
    """Everything worth knowing when writing this scene."""

    scene: Scene
    participants: list[Entity] = field(default_factory=list)
    location: Entity | None = None
    day: int | None = None
    date_text: str = ""
    relationships: list[ScoredFact] = field(default_factory=list)
    secrets: list[KnowledgeLine] = field(default_factory=list)
    goals: list[tuple[Entity, str, str]] = field(default_factory=list)
    recent_events: list[tuple[Event, int]] = field(default_factory=list)
    tensions: list[str] = field(default_factory=list)
    world_state_notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        """The panel as text — used by the CLI and mirrored by the UI."""
        out = [f"{self.scene.title}"]
        if self.date_text:
            place = f" at {self.location.name}" if self.location else ""
            out.append(f"  {self.date_text}{place}")
        if self.participants:
            out.append("  Present: " + ", ".join(p.name for p in self.participants))

        if self.relationships:
            out.append("\n  Relevant relationships")
            for item in self.relationships:
                secret = " [secret]" if item.fact.is_secret else ""
                note = f" — {item.fact.note}" if item.fact.note else ""
                out.append(f"    {item.describe()}{secret}{note}")

        if self.secrets:
            out.append("\n  Relevant secrets")
            for line in self.secrets:
                out.append(f"    {line.describe()}"
                           + (f" — {line.note}" if line.note else ""))

        if self.goals:
            out.append("\n  Active goals")
            for person, kind, text in self.goals:
                label = "privately" if kind == "private_goal" else "openly"
                out.append(f"    {person.name} {label} wants: {text}")

        if self.recent_events:
            out.append("\n  Recent history")
            for event, days_ago in self.recent_events:
                when = "earlier the same day" if days_ago == 0 else f"{days_ago} days earlier"
                out.append(f"    {event.name} — {when}")

        if self.tensions:
            out.append("\n  Why this room is tense")
            for line in self.tensions:
                out.append(f"    {line}")

        return "\n".join(out)


class SceneContextEngine:
    def __init__(self, world: World) -> None:
        self.world = world
        self.genealogy = Genealogy(world)

    def build(
        self,
        scene_id: str,
        *,
        max_relationships: int = 12,
        max_events: int = 6,
        recent_window_days: int | None = None,
    ) -> SceneContext:
        scene = self.world.get_scene(scene_id)
        if scene is None:
            raise ValueError(f"no scene {scene_id!r}")

        participants = self.world.scene_participants(scene_id)
        present = {p.id for p in participants}
        location = (self.world.get_entity(scene.location_id)
                    if scene.location_id else None)
        day = scene.day
        window = recent_window_days or (self.world.calendar.common_year_days // 2)

        ctx = SceneContext(
            scene=scene, participants=participants, location=location, day=day,
            date_text=self.world.calendar.format(day) if day is not None else "",
        )

        ctx.relationships = self._relationships(present, day, max_relationships)
        ctx.secrets = self._secrets(present, day)
        ctx.goals = self._goals(participants, day)
        ctx.recent_events = self._recent_events(present, day, window, max_events)
        ctx.tensions = self._tensions(ctx)
        ctx.world_state_notes = self._world_notes(location, day)
        return ctx

    # ---- components -------------------------------------------------------

    def _relationships(self, present: set[str], day: int | None,
                       limit: int) -> list[ScoredFact]:
        """§45: score, then cut — never dump."""
        scored: list[ScoredFact] = []
        seen: set[str] = set()

        for person_id in present:
            for fact in self.world.facts_where(subject_id=person_id, at=day):
                if fact.id in seen or fact.object_id is None:
                    continue
                seen.add(fact.id)
                score, reasons = self._score(fact, present)
                if score <= 0:
                    continue
                scored.append(ScoredFact(
                    fact=fact, score=score, reasons=tuple(reasons),
                    subject=self.world.get_entity(fact.subject_id),
                    object=self.world.get_entity(fact.object_id),
                ))

        scored.sort(key=lambda s: (-s.score, s.fact.predicate_key))
        return scored[:limit]

    def _score(self, fact: Fact, present: set[str]) -> tuple[float, list[str]]:
        score = float(CHARGED_PREDICATES.get(fact.predicate_key, 0))
        reasons: list[str] = []
        if score:
            reasons.append("a charged relationship")

        # The single strongest signal: both ends of this relationship are in the room.
        if fact.object_id in present:
            score += 8
            reasons.append("both parties are present")

        if fact.is_secret:
            score += 5
            reasons.append("secret")
        if fact.confidence in ("disputed", "rumored"):
            score += 2
            reasons.append(fact.confidence)
        if fact.strength in ("deeply_trusts", "hates", "loves", "distrusts",
                             "overwhelming", "very_high"):
            score += 3
            reasons.append("strongly felt")
        if fact.note:
            score += 1
        return score, reasons

    def _secrets(self, present: set[str], day: int | None) -> list[KnowledgeLine]:
        """The layered knowledge §6 asks for, restricted to people in the room."""
        lines: list[KnowledgeLine] = []
        for secret in self.world.secrets():
            states = self.world.knowledge_of(secret.id, at=day)
            if not any(s.observer_id in present for s in states):
                continue
            for state in states:
                if state.observer_id not in present:
                    continue
                observer = self.world.get_entity(state.observer_id)
                if observer is None:
                    continue
                about = (self.world.get_entity(state.about_observer_id)
                         if state.about_observer_id else None)
                # Second-order awareness only matters here if its subject is also present.
                if about is not None and about.id not in present:
                    continue
                lines.append(KnowledgeLine(
                    secret=secret, observer=observer, stance=state.stance,
                    about_observer=about, note=state.note,
                ))
        order = {"knows": 0, "misinformed": 1, "suspects": 2, "believes": 3, "unaware": 4}
        lines.sort(key=lambda line: (order.get(line.stance, 9), line.observer.name))
        return lines

    def _goals(self, participants: list[Entity],
               day: int | None) -> list[tuple[Entity, str, str]]:
        goals: list[tuple[Entity, str, str]] = []
        for person in participants:
            for key in ("private_goal", "surface_goal"):
                value = self.world.value_of(person.id, key, at=day)
                if value:
                    goals.append((person, key, value))
        return goals

    def _recent_events(self, present: set[str], day: int | None, window: int,
                       limit: int) -> list[tuple[Event, int]]:
        if day is None:
            return []
        out: list[tuple[Event, int]] = []
        for event in self.world.events(first=day - window, last=day):
            if event.start_day is None:
                continue
            participants = {e.id for e, _ in self.world.event_participants(event.id)}
            # An event matters here if someone in the room was part of it.
            if participants and not (participants & present):
                continue
            out.append((event, day - event.start_day))
        out.sort(key=lambda pair: pair[1])
        return out[:limit]

    def _tensions(self, ctx: SceneContext) -> list[str]:
        """§96: say plainly why the conversation is difficult."""
        lines: list[str] = []
        present = {p.id for p in ctx.participants}

        # Someone knows something about someone else in the room who does not know it.
        for line in ctx.secrets:
            if line.stance != "knows" or line.about_observer is not None:
                continue
            subject = (self.world.get_entity(line.secret.about_id)
                       if line.secret.about_id else None)
            if subject is None or subject.id not in present:
                continue
            unaware = [
                other.observer.name for other in ctx.secrets
                if other.observer.id == subject.id
                and other.stance in ("misinformed", "unaware")
            ]
            if unaware:
                lines.append(
                    f"{line.observer.name} knows “{line.secret.name}” and "
                    f"{subject.name}, who is in the room, does not."
                )

        # Two people in the room want incompatible things (§95).
        goals = {p.id: text for p, kind, text in ctx.goals if kind == "private_goal"}
        for a_id, a_text in goals.items():
            for b_id, b_text in goals.items():
                if a_id >= b_id:
                    continue
                if self._goals_conflict(a_text, b_text):
                    a = self.world.get_entity(a_id)
                    b = self.world.get_entity(b_id)
                    lines.append(
                        f"{a.name} and {b.name} want incompatible things: "
                        f"“{a_text}” against “{b_text}”."
                    )

        # A debt or an outright hostility across the table.
        for item in ctx.relationships:
            if item.fact.object_id not in present:
                continue
            if item.fact.predicate_key == "owes_debt_to":
                lines.append(f"{item.subject.name} owes {item.object.name} a debt.")
            elif item.fact.strength in ("hates", "distrusts"):
                lines.append(
                    f"{item.subject.name} {item.fact.strength.replace('_', ' ')} "
                    f"{item.object.name}, who is present."
                )
        return lines

    @staticmethod
    def _goals_conflict(a: str, b: str) -> bool:
        """A deliberately shallow heuristic, offered as a prompt rather than a verdict.

        §14 and §103 both insist derived conclusions are suggestions. Two goals naming the
        same person with opposite verbs are worth *asking* the writer about; claiming to
        have detected a plot contradiction would be overreach.
        """
        opposed = (
            ("crowned", "discredited"), ("crowned", "disinherited"),
            ("reveal", "suppress"), ("reveal", "conceal"), ("protect", "destroy"),
            ("war", "peace"), ("keep", "take"),
        )
        a_low, b_low = a.lower(), b.lower()
        return any(
            (x in a_low and y in b_low) or (y in a_low and x in b_low)
            for x, y in opposed
        )

    def _world_notes(self, location: Entity | None, day: int | None) -> list[str]:
        """§83: what was true here, at this moment."""
        if location is None or day is None:
            return []
        notes: list[str] = []
        for fact in self.world.facts_where(object_id=location.id, at=day):
            holder = self.world.get_entity(fact.subject_id)
            if holder is None or fact.predicate_key not in CONTROL_PREDICATES:
                continue
            # Read the fact from the place's side, which means the inverse predicate:
            # "administers" seen from Greyhaven is "administered by". Conjugating the
            # forward key instead produces "Greyhaven is administers by House Veyne".
            inverse = inverse_of(fact.predicate_key) or fact.predicate_key
            notes.append(f"{location.name} is {inverse.replace('_', ' ')} {holder.name}.")
        notes.sort()
        season = self.world.calendar.season(day)
        if season:
            notes.append(f"The season is {season}.")
        return notes
