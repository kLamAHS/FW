"""Where a place gets what it does not grow (§18, §19, §42, §86).

§19 asks this system one concrete question:

    Where does Greyhaven get its grain? … and trace the supply path.

Every piece of the answer was already in the world and nothing joined them. The seed
records that Greyhaven `imports` Grain and the Vale `exports` it — with a comment on the
next line reading *"the dependency §42 asks about"* — and `Router` can cost any journey
between two places with the season and the construction dates applied. What was missing
was the join: the supply path is a search over the road network for somebody who has what
this place needs.

This is emphatically **not** an economic simulation. §68 and §116 both warn against
adding simulation for its own sake, and nothing here computes a yield from soil, labour
and rainfall. It traces what the writer wrote: who says they produce a thing, who says
they need it, and how long the journey between them takes on the day in question. Every
answer carries its evidence, because §67 refuses conclusions a writer cannot check.

The interesting answers are the negative ones. A town that imports grain nobody exports,
or whose only supplier is four weeks away over a pass that closes in winter, is a story —
and that is what a writer is looking at this screen for.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.derive.dependency import Finding
from fw.core.geo.routing import Router
from fw.core.world import World

# How much of a thing a place says it has or wants. `magnitude`'s own steps, ranked so a
# high producer is offered before a low one — the writer's word, not a number we invented.
LEVELS = {"very_high": 4, "high": 3, "medium": 2, "low": 1, "none": 0}

# What counts as having something to spare, and as needing it. `produces` is what the
# ground gives; `exports` is what leaves. A place that produces and does not export is
# feeding itself, which is why both are read and only `exports` is treated as an offer.
GIVES = ("exports", "produces")
NEEDS = ("imports", "consumes")


@dataclass(frozen=True)
class Source:
    """One place that could supply one commodity, and what the journey costs."""

    entity_id: str
    name: str
    level: str
    exports: bool
    days: float | None = None
    distance: float | None = None
    path: tuple[str, ...] = ()
    path_names: tuple[str, ...] = ()
    note: str = ""

    @property
    def reachable(self) -> bool:
        return self.days is not None


@dataclass
class Supply:
    """One commodity a place needs, and everywhere it could come from."""

    resource_id: str
    resource_name: str
    level: str
    sources: list[Source] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "resource_id": self.resource_id, "resource_name": self.resource_name,
            "level": self.level,
            "sources": [
                {"entity_id": s.entity_id, "name": s.name, "level": s.level,
                 "exports": s.exports, "days": s.days, "distance": s.distance,
                 "path": list(s.path), "path_names": list(s.path_names), "note": s.note}
                for s in self.sources
            ],
            "findings": [
                {"text": f.text, "weight": f.weight, "kind": f.kind,
                 "evidence": list(f.evidence), "entity_ids": list(f.entity_ids)}
                for f in self.findings
            ],
        }


class SupplyAnalyst:
    """Reads only. Traces what the writer wrote; invents no economics."""

    def __init__(self, world: World, *, profile: str = "wagon") -> None:
        self.world = world
        # Goods move by wagon, not by messenger. The profile matters: §19 asks for
        # travel time, and a courier's four days is not a grain convoy's twelve.
        self.profile = profile
        self._router = Router(world)
        self._on_the_network = set(self._router.places())

    # ---- the question §19 asks ---------------------------------------------

    def where_it_comes_from(self, place_id: str, resource_id: str,
                            day: int) -> Supply:
        """Everywhere one commodity could reach one place from, nearest first."""
        resource = self.world.get_entity(resource_id)
        supply = Supply(resource_id=resource_id,
                        resource_name=resource.name if resource else "it",
                        level=self._level(place_id, resource_id, NEEDS, day))

        for holder_id, level, exports in self._who_has(resource_id, day):
            if holder_id == place_id:
                continue
            supply.sources.append(self._journey(place_id, holder_id, level, exports, day))

        supply.sources.sort(key=lambda s: (not s.reachable,
                                           s.days if s.days is not None else 0.0,
                                           -LEVELS.get(s.level, 0), s.name))
        supply.findings = self._what_it_means(place_id, supply, day)
        return supply

    def needs_of(self, place_id: str, day: int) -> list[Supply]:
        """Everything a place says it needs, and where each could come from."""
        wanted: dict[str, str] = {}
        for predicate in NEEDS:
            for fact in self.world.facts_where(predicate, subject_id=place_id, at=day):
                if fact.object_id:
                    wanted.setdefault(fact.object_id, fact.strength or "")
        return [self.where_it_comes_from(place_id, resource_id, day)
                for resource_id in sorted(wanted, key=lambda r: self._name(r))]

    def who_depends_on(self, place_id: str, day: int) -> list[Finding]:
        """Who would feel it if this place stopped — §117's "Who benefits?" inverted.

        Read from what this place exports or produces against who imports the same
        thing, so it is the writer's own facts joined rather than a guess.
        """
        out: list[Finding] = []
        here = self._name(place_id)
        # A place that both produces and exports the same thing is one supplier, not
        # two. `GIVES` is ordered exports-first, so the first pass wins and the second
        # is skipped — saying it twice would read as two separate dependencies.
        said: set[tuple[str, str]] = set()
        for predicate in GIVES:
            for fact in self.world.facts_where(predicate, subject_id=place_id, at=day):
                if not fact.object_id:
                    continue
                good = self._name(fact.object_id)
                for taker in self._who_wants(fact.object_id, day):
                    if taker == place_id or (taker, fact.object_id) in said:
                        continue
                    said.add((taker, fact.object_id))
                    out.append(Finding(
                        text=f"{self._name(taker)} takes {good}, and {here} "
                             f"{'exports' if predicate == 'exports' else 'produces'} it.",
                        weight=3 if predicate == "exports" else 2,
                        kind="depends", entity_ids=[taker, fact.object_id],
                        evidence=[f"{here} {predicate} {good}; "
                                  f"{self._name(taker)} imports it."]))
        for fact in self.world.facts_where("depends_on", object_id=place_id, at=day):
            out.append(Finding(
                text=f"{self._name(fact.subject_id)} depends on {here}.",
                weight=4, kind="depends", entity_ids=[fact.subject_id],
                evidence=[fact.note or "Recorded directly by the writer."]))
        out.sort(key=lambda f: (-f.weight, f.text))
        return out

    def standing_of(self, holder_id: str, day: int) -> list[Finding]:
        """What a house is actually worth, rolled up from what it holds (§86).

        §86 asks how economic power and political power interact, and the application
        could only answer it from an arbitrary `prestige: high` label a writer typed —
        which is precisely the thing the brief says to avoid. This counts instead: the
        ground they hold, the people on it, what comes out of it, and the roads they can
        tax. Every line names the holding it came from, so the writer can disagree with
        the arithmetic rather than with a number.
        """
        here = self._name(holder_id)
        out: list[Finding] = []
        held: list[str] = []
        people = 0
        goods: dict[str, str] = {}

        for predicate in ("legally_owns", "administers", "taxes", "occupies"):
            for fact in self.world.facts_where(predicate, subject_id=holder_id, at=day):
                place = fact.object_id
                if not place or place in held:
                    continue
                held.append(place)
                count = self.world.value_of(place, "population", at=day)
                if count and count.isdigit():
                    people += int(count)
                for gives in GIVES:
                    for what in self.world.facts_where(gives, subject_id=place, at=day):
                        if what.object_id:
                            goods.setdefault(self._name(what.object_id),
                                             what.strength or "")

        if held:
            out.append(Finding(
                text=f"{here} holds {len(held)} place"
                     f"{'' if len(held) == 1 else 's'}"
                     + (f", and about {people:,} people live on them" if people else ""),
                weight=4, kind="standing", entity_ids=held,
                evidence=[", ".join(sorted(self._name(p) for p in held))]))
        if goods:
            out.append(Finding(
                text="What that ground gives: "
                     + ", ".join(f"{good}"
                                 + (f" ({level.replace('_', ' ')})" if level else "")
                                 for good, level in sorted(goods.items())),
                weight=3, kind="standing", entity_ids=held,
                evidence=["Read from what the places they hold produce and export."]))

        tolls = [segment for segment in self.world.route_segments()
                 if segment.toll_holder_id == holder_id]
        if tolls:
            out.append(Finding(
                text=f"{here} takes a toll on {len(tolls)} "
                     f"road{'' if len(tolls) == 1 else 's'}.",
                weight=3, kind="standing", entity_ids=[],
                evidence=["A toll is money that arrives without anything being grown."]))

        leaning = self.who_depends_on(holder_id, day)
        for place in held:
            leaning.extend(self.who_depends_on(place, day))
        if leaning:
            out.append(Finding(
                text=(f"1 other place leans on what {here} holds."
                      if len(leaning) == 1
                      else f"{len(leaning)} other places lean on what {here} holds."),
                weight=5, kind="standing",
                entity_ids=sorted({e for f in leaning for e in f.entity_ids}),
                evidence=[f.text for f in leaning[:4]]))
        out.sort(key=lambda f: (-f.weight, f.text))
        return out

    # ---- the reading behind it ---------------------------------------------

    def _who_has(self, resource_id: str, day: int) -> list[tuple[str, str, bool]]:
        """Everyone who says they have this, exporters before mere producers."""
        found: dict[str, tuple[str, bool]] = {}
        for predicate in GIVES:
            for fact in self.world.facts_where(predicate, object_id=resource_id, at=day):
                level = fact.strength or ""
                exports = predicate == "exports"
                was = found.get(fact.subject_id)
                if was is None or (exports and not was[1]):
                    found[fact.subject_id] = (level or (was[0] if was else ""), exports)
        return [(entity_id, level, exports)
                for entity_id, (level, exports) in found.items()]

    def _who_wants(self, resource_id: str, day: int) -> list[str]:
        return [fact.subject_id for predicate in NEEDS
                for fact in self.world.facts_where(predicate, object_id=resource_id,
                                                   at=day)
                if fact.subject_id]

    def _journey(self, to_id: str, from_id: str, level: str, exports: bool,
                 day: int) -> Source:
        """How the goods would actually get there, on this day.

        A supplier is only a supplier if there is a way. The route engine already applies
        seasonal closures and construction dates, so "the pass is shut in Deepwinter" is
        an answer this returns rather than a caveat somebody has to remember.
        """
        best = None
        # A region is a supplier the way a country is: the grain leaves through its
        # towns. "The Vale of Renn exports grain" is how a writer says it, and the road
        # network joins settlements, so the journey starts at whichever place inside it
        # is nearest — which is also the honest answer to "where does it come from".
        for start in self._departures(from_id, day):
            try:
                route = self._router.route(start, to_id, profile=self.profile, day=day)
            except Exception:                 # noqa: BLE001 — no route is an answer
                route = None
            if route is not None and (best is None or route.days < best.days):
                best = route
        if best is None:
            return Source(entity_id=from_id, name=self._name(from_id), level=level,
                          exports=exports,
                          note="No road, river or lane joins them on this date.")
        names = tuple(self._name(step) for step in best.path)
        note = ""
        if best.path and best.path[0] != from_id:
            note = f"by way of {names[0]}, in {self._name(from_id)}"
        return Source(entity_id=from_id, name=self._name(from_id), level=level,
                      exports=exports, days=round(best.days, 1),
                      distance=round(best.distance, 1),
                      path=tuple(best.path), path_names=names, note=note)

    def _departures(self, holder_id: str, day: int) -> list[str]:
        """Where goods can actually leave from: the place itself, or its towns."""
        if holder_id in self._on_the_network:
            return [holder_id]
        inside = [fact.subject_id
                  for fact in self.world.facts_where("located_in", object_id=holder_id,
                                                     at=day)
                  if fact.subject_id in self._on_the_network]
        return sorted(inside)

    def _what_it_means(self, place_id: str, supply: Supply, day: int) -> list[Finding]:
        """The sentences a writer is actually here for."""
        here = self._name(place_id)
        good = supply.resource_name
        out: list[Finding] = []

        reachable = [s for s in supply.sources if s.reachable]
        if not supply.sources:
            out.append(Finding(
                text=f"Nobody in this world produces {good}, and {here} needs it.",
                weight=5, kind="gap", entity_ids=[supply.resource_id],
                evidence=[f"No place records producing or exporting {good}."]))
        elif not reachable:
            out.append(Finding(
                text=f"{here} needs {good} and no road reaches anyone who has it.",
                weight=5, kind="gap", entity_ids=[supply.resource_id],
                evidence=[f"{len(supply.sources)} places have {good}; none is "
                          f"reachable by {self.profile} on this date."]))
        else:
            nearest = reachable[0]
            out.append(Finding(
                text=f"{here} gets {good} from {nearest.name}"
                     + (f" ({nearest.note})" if nearest.note else "")
                     + f" — {nearest.days} days by {self.profile}, "
                     f"{' → '.join(nearest.path_names)}.",
                weight=4, kind="supply",
                entity_ids=[nearest.entity_id, supply.resource_id],
                evidence=[f"{nearest.name} "
                          f"{'exports' if nearest.exports else 'produces'} {good}"
                          + (f" ({nearest.level.replace('_', ' ')})"
                             if nearest.level else "") + "."]))
            if len(reachable) == 1:
                out.append(Finding(
                    text=f"{nearest.name} is the only source of {good} {here} can "
                         f"reach. If that road closes, {here} goes without.",
                    weight=5, kind="fragile",
                    entity_ids=[nearest.entity_id, supply.resource_id],
                    evidence=["One reachable supplier on this date."]))
        out.sort(key=lambda f: (-f.weight, f.text))
        return out

    def _level(self, subject_id: str, object_id: str,
               predicates: tuple[str, ...], day: int) -> str:
        for predicate in predicates:
            for fact in self.world.facts_where(predicate, subject_id=subject_id,
                                               object_id=object_id, at=day):
                if fact.strength:
                    return fact.strength
        return ""

    def _name(self, entity_id: str | None) -> str:
        if not entity_id:
            return ""
        found = self.world.get_entity(entity_id)
        return found.name if found else "somewhere no longer in the world"
