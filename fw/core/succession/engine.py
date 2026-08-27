"""The succession engine (spec §8).

Computes the order of heirs to a title, as of a date, under a chosen law — and does it
without ever mutating canon, so the writer can ask "what if Oren is declared illegitimate"
and get an answer rather than a changed world (§8, §50).

The brief states the expected result outright, and the test suite asserts exactly it:

    Death of King Aldren
    Current succession: 1. Prince Oren  2. Lady Elia  3. Lord Caros  4. Lady Mara
    If Oren is declared illegitimate: 1. Lady Elia  2. Lord Caros  3. Lady Mara

Two things about that example are worth stating, because they are easy to get wrong:

**The walk starts from the title's dynastic root, not from the deceased.** Caros and Mara
are not Aldren's children; they are reachable only through Aldren's own father. Succession
is a property of the title's line, not of whoever happened to die.

**Eligibility is evaluated as of a date.** Whether someone is alive, legitimate and of a
permitted gender are all questions about a moment, which is only answerable because every
fact in the store carries a validity interval.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.genealogy.kinship import Genealogy, Legitimacy, Person
from fw.core.succession.laws import SuccessionLaw, get_law
from fw.core.world import World


@dataclass
class Claimant:
    """One position in the line, with the reasoning that put it there (§67)."""

    person: Person
    position: int
    degree: int
    path: tuple[str, ...] = ()
    note: str = ""

    @property
    def id(self) -> str:
        return self.person.id

    @property
    def name(self) -> str:
        return self.person.name


@dataclass
class Exclusion:
    """Someone who would otherwise have had a claim, and why they do not.

    Kept and returned rather than silently dropped: §67 insists the software explain its
    conclusions, and "why is my character not in the line of succession?" is precisely the
    question a writer will ask.
    """

    person: Person
    reason: str


@dataclass
class Succession:
    title_id: str
    title_name: str
    law: SuccessionLaw
    day: int
    line: list[Claimant] = field(default_factory=list)
    excluded: list[Exclusion] = field(default_factory=list)
    hypothetical: bool = False
    assumptions: tuple[str, ...] = ()

    @property
    def heir(self) -> Claimant | None:
        return self.line[0] if self.line else None

    def names(self) -> list[str]:
        return [c.name for c in self.line]

    def explain(self) -> str:
        """A plain-language account of the result, for the UI and the CLI."""
        lines = [f"Succession to {self.title_name} under {self.law.label}:"]
        if self.assumptions:
            lines.append("  Assuming: " + "; ".join(self.assumptions))
        if not self.line:
            lines.append("  No eligible heir.")
        for c in self.line:
            lines.append(f"  {c.position}. {c.name}" + (f" — {c.note}" if c.note else ""))
        for e in self.excluded:
            lines.append(f"  (excluded: {e.person.name} — {e.reason})")
        return "\n".join(lines)


class SuccessionEngine:
    def __init__(self, world: World, genealogy: Genealogy | None = None) -> None:
        self.world = world
        self.genealogy = genealogy or Genealogy(world)

    def compute(
        self,
        title_id: str,
        day: int,
        *,
        law_key: str | None = None,
        exclude: set[str] | None = None,
        force_illegitimate: set[str] | None = None,
        assume_dead: set[str] | None = None,
        limit: int = 12,
    ) -> Succession:
        """Rank the claimants to a title on a given day.

        The `exclude` / `force_illegitimate` / `assume_dead` arguments are how §50's
        hypothetical mode works: they are parameters to the calculation, so no hypothesis
        can ever write to the database.
        """
        title = self.world.get_title(title_id)
        if title is None:
            raise ValueError(f"no title {title_id!r}")

        law = get_law(law_key or title.succession_law)
        exclude = set(exclude or ())
        force_illegitimate = set(force_illegitimate or ())
        assume_dead = set(assume_dead or ())

        assumptions = []
        for pid in sorted(force_illegitimate):
            person = self.genealogy.people.get(pid)
            if person:
                assumptions.append(f"{person.name} is illegitimate")
        for pid in sorted(assume_dead):
            person = self.genealogy.people.get(pid)
            if person:
                assumptions.append(f"{person.name} is dead")
        for pid in sorted(exclude):
            person = self.genealogy.people.get(pid)
            if person:
                assumptions.append(f"{person.name} is set aside")

        result = Succession(
            title_id=title_id, title_name=title.name, law=law, day=day,
            hypothetical=bool(exclude or force_illegitimate or assume_dead),
            assumptions=tuple(assumptions),
        )

        if not law.hereditary:
            result.excluded.append(Exclusion(
                Person(id="", name=law.label),
                f"{law.label} does not pass by inheritance; "
                "the holder is chosen rather than computed",
            ))
            if law.key in ("appointment", "conquest"):
                return result

        root_id = self._dynastic_root(title, day)
        if root_id is None:
            return result

        current_holder = self.world.title_holder_on(title_id, day)
        excluded_ids = exclude | assume_dead

        candidates: list[tuple[tuple, Person, int, tuple[str, ...]]] = []
        self._walk(
            root_id, law, day, excluded_ids, force_illegitimate,
            depth=0, path=(), sort_prefix=(), out=candidates,
            result=result, holder_id=current_holder,
        )

        if not law.depth_first:
            # Seniority and its relatives rank the whole pool by age, not by line.
            candidates.sort(key=lambda c: law.sibling_key(c[1]))

        if law.max_degree is not None:
            candidates = [c for c in candidates if c[2] <= law.max_degree]

        for position, (_, person, degree, path) in enumerate(candidates[:limit], start=1):
            result.line.append(Claimant(
                person=person, position=position, degree=degree, path=path,
                note=self._describe_claim(person, path, root_id),
            ))
        return result

    # ---- internals --------------------------------------------------------

    def _dynastic_root(self, title, day: int) -> str | None:
        """Where the walk begins.

        An explicit `dynasty_root_id` wins. Otherwise we climb from the current or most
        recent holder to the earliest known forebear, because the heir may be a cousin only
        reachable through a common ancestor several generations up.
        """
        if title.dynasty_root_id:
            return title.dynasty_root_id

        holder = self.world.title_holder_on(title.id, day)
        if holder is None:
            holdings = self.world.title_holdings(title.id)
            past = [h for h in holdings if h.from_day is None or h.from_day <= day]
            holder = past[-1].holder_id if past else (holdings[0].holder_id if holdings else None)
        if holder is None:
            return None

        roots = self.genealogy.root_ancestors(holder)
        return roots[0] if roots else holder

    def _walk(
        self,
        person_id: str,
        law: SuccessionLaw,
        day: int,
        excluded: set[str],
        force_illegitimate: set[str],
        *,
        depth: int,
        path: tuple[str, ...],
        sort_prefix: tuple,
        out: list,
        result: Succession,
        holder_id: str | None,
        seen: set[str] | None = None,
    ) -> None:
        """Depth-first descent: each child, then that child's whole line, then the next."""
        seen = seen if seen is not None else {person_id}
        if depth > 40:
            return

        children = self.genealogy.children_of(person_id)
        ordered = sorted(
            (self.genealogy.people[c] for c in children if c in self.genealogy.people),
            key=law.sibling_key,
        )

        for index, child in enumerate(ordered):
            if child.id in seen:
                continue
            seen.add(child.id)
            child_path = path + (child.id,)
            child_sort = sort_prefix + (index,)

            reason = self._ineligibility(
                child, law, day, excluded, force_illegitimate, holder_id
            )
            if reason is None:
                out.append((child_sort, child, depth + 1, child_path))
            elif (reason != "is the current holder"
                    and not any(e.person.id == child.id for e in result.excluded)):
                result.excluded.append(Exclusion(child, reason))

            # A person barred by gender under a male-only law also cannot transmit a claim,
            # so their line is not walked. Under other laws an excluded person's children
            # may still inherit -- a dead heir's son is exactly how primogeniture works.
            blocks_line = (
                law.allowed_genders and not law.permits_gender(child.gender)
            )
            if not blocks_line:
                self._walk(
                    child.id, law, day, excluded, force_illegitimate,
                    depth=depth + 1, path=child_path, sort_prefix=child_sort,
                    out=out, result=result, holder_id=holder_id, seen=seen,
                )

    def _ineligibility(
        self,
        person: Person,
        law: SuccessionLaw,
        day: int,
        excluded: set[str],
        force_illegitimate: set[str],
        holder_id: str | None,
    ) -> str | None:
        """Why this person cannot inherit, or None if they can."""
        if person.id == holder_id:
            return "is the current holder"
        if person.id in excluded:
            return "set aside by hypothesis"
        if not person.alive_on(day):
            if person.died is not None and person.died < day:
                return "already dead"
            return "not yet born"
        if not law.permits_gender(person.gender):
            return f"{law.label} does not admit {person.gender or 'this'} heirs"
        if law.require_legitimate:
            legitimacy = (
                Legitimacy.ILLEGITIMATE if person.id in force_illegitimate
                else person.legitimacy
            )
            if not legitimacy.inherits_by_default:
                return f"{legitimacy.value} birth"
        return None

    def _describe_claim(self, person: Person, path: tuple[str, ...], root_id: str) -> str:
        """§67: say how the claim runs, rather than presenting a bare ranking."""
        if len(path) <= 1:
            return ""
        via = [self.genealogy.people[p].name for p in path[:-1] if p in self.genealogy.people]
        return "through " + ", ".join(via) if via else ""


def succession_for(world: World, title_id: str, day: int, **kw) -> Succession:
    """Convenience wrapper for callers that do not hold an engine."""
    return SuccessionEngine(world).compute(title_id, day, **kw)
