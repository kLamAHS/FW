"""Parentage and descent (spec §7).

The brief asks for biological, legal, adoptive and foster parents, disputed and uncertain
and secret parentage, legitimacy and legitimisation. Collapsing those into one "parent"
edge would destroy exactly the information dynastic fiction runs on — the whole plot of the
example world is that Prince Oren's biological father is not his legal one.

So parentage is read through a lens: `Parentage.biological` and `Parentage.legal` may
disagree, and the succession engine chooses which it cares about (usually legal, because
inheritance follows law rather than blood — until somebody proves otherwise in public,
which is the story).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

from fw.core.model.records import Entity
from fw.core.world import World

# Which predicate expresses which kind of parenthood.
PARENT_PREDICATES = {
    "parent_of": "biological",
    "legal_parent_of": "legal",
    "adoptive_parent_of": "adoptive",
    "foster_parent_of": "foster",
}

UNION_PREDICATES = ("married_to", "betrothed_to", "consort_of")


class Legitimacy(str, Enum):
    LEGITIMATE = "legitimate"
    ILLEGITIMATE = "illegitimate"
    DISPUTED = "disputed"
    LEGITIMISED = "legitimised"
    UNKNOWN = "unknown"

    @property
    def inherits_by_default(self) -> bool:
        """Whether this status lets a child into the line of succession unaided.

        Disputed counts as in: the drama of a disputed claim is that the claimant *is*
        in the line until a court, a council or a war says otherwise.
        """
        return self in (Legitimacy.LEGITIMATE, Legitimacy.LEGITIMISED,
                        Legitimacy.DISPUTED, Legitimacy.UNKNOWN)


@dataclass
class Person:
    """A person as the genealogy engine sees them."""

    id: str
    name: str
    born: int | None = None
    died: int | None = None
    gender: str | None = None
    legitimacy: Legitimacy = Legitimacy.LEGITIMATE
    house_id: str | None = None

    def alive_on(self, day: int) -> bool:
        if self.born is not None and day < self.born:
            return False
        if self.died is not None and day > self.died:
            return False
        return True


@dataclass
class Parentage:
    """Who a person's parents are, by kind. Each maps parent id -> kind."""

    biological: dict[str, str] = field(default_factory=dict)
    legal: dict[str, str] = field(default_factory=dict)

    @property
    def all_ids(self) -> set[str]:
        return set(self.biological) | set(self.legal)


class Genealogy:
    """An in-memory view of the world's kinship graph.

    Loaded once and queried many times: pedigree layout, succession, kin queries and half
    the continuity rules all walk this, and re-reading the database per question would turn
    a 2,000-person dynasty into a slideshow.
    """

    def __init__(self, world: World, *, at: int | None = None) -> None:
        self.world = world
        self.at = at
        self.people: dict[str, Person] = {}
        self._children: dict[str, list[str]] = {}
        self._parents: dict[str, Parentage] = {}
        self._unions: dict[str, set[str]] = {}
        self._load()

    # ---- loading ----------------------------------------------------------

    def _load(self) -> None:
        for entity in self.world.entities("person"):
            self.people[entity.id] = self._to_person(entity)

        for predicate, kind in PARENT_PREDICATES.items():
            for fact in self.world.facts_where(predicate):
                if fact.object_id is None:
                    continue
                parent, child = fact.subject_id, fact.object_id
                if parent not in self.people or child not in self.people:
                    continue
                self._children.setdefault(parent, [])
                if child not in self._children[parent]:
                    self._children[parent].append(child)
                p = self._parents.setdefault(child, Parentage())
                # An adoptive or foster parent is a legal parent for inheritance purposes
                # unless the world says otherwise; a biological parent is only that.
                if kind == "biological":
                    p.biological[parent] = kind
                    p.legal.setdefault(parent, kind)
                else:
                    p.legal[parent] = kind

        self._apply_legal_displacement()

        for predicate in UNION_PREDICATES:
            for fact in self.world.facts_where(predicate):
                if fact.object_id is None:
                    continue
                self._unions.setdefault(fact.subject_id, set()).add(fact.object_id)
                self._unions.setdefault(fact.object_id, set()).add(fact.subject_id)

    def _apply_legal_displacement(self) -> None:
        """Resolve who counts as a legal parent when blood and law disagree.

        Declaring an explicit legal parent displaces a *competing* biological claim, not
        every other parent. When the world states that Aldren is Oren's legal father, that
        unseats Corren — but Oren's mother is still his mother, and an earlier version of
        this that dropped every biological-default parent quietly disinherited Sera.

        "Competing" is judged by the parent's gender, which is the closest thing the model
        has to a parental role. Where a gender is unknown the biological parent is kept:
        wrongly including a parent is a visible, correctable mistake, while wrongly
        dropping one silently changes who can inherit.
        """
        for child_id, parentage in self._parents.items():
            declared = {
                pid: kind for pid, kind in parentage.legal.items() if kind != "biological"
            }
            if not declared:
                continue
            declared_genders = {
                self.people[pid].gender for pid in declared if pid in self.people
            } - {None}
            kept = dict(declared)
            for pid, kind in parentage.legal.items():
                if kind != "biological":
                    continue
                gender = self.people[pid].gender if pid in self.people else None
                if gender is None or gender not in declared_genders:
                    kept[pid] = kind
            self._parents[child_id].legal = kept

    def _to_person(self, entity: Entity) -> Person:
        raw = self.world.value_of(entity.id, "legitimacy")
        try:
            legitimacy = Legitimacy(raw) if raw else Legitimacy.LEGITIMATE
        except ValueError:
            legitimacy = Legitimacy.UNKNOWN
        house_id = None
        for fact in self.world.facts_where("member_of", subject_id=entity.id):
            target = self.world.get_entity(fact.object_id) if fact.object_id else None
            if target is not None and target.type_key in ("house", "dynasty", "clan"):
                house_id = target.id
                break
        return Person(
            id=entity.id,
            name=entity.name,
            born=entity.exists_from,
            died=entity.exists_to,
            gender=self.world.value_of(entity.id, "gender"),
            legitimacy=legitimacy,
            house_id=house_id,
        )

    # ---- queries ----------------------------------------------------------

    def parents_of(self, person_id: str, *, lens: str = "legal") -> list[str]:
        p = self._parents.get(person_id)
        if p is None:
            return []
        return list(p.legal if lens == "legal" else p.biological)

    def parentage(self, person_id: str) -> Parentage:
        return self._parents.get(person_id, Parentage())

    def children_of(self, person_id: str, *, lens: str = "legal") -> list[str]:
        """Children, eldest first. Birth order is the backbone of primogeniture."""
        kids = [
            c for c in self._children.get(person_id, [])
            if person_id in self.parents_of(c, lens=lens)
        ]
        return sorted(kids, key=lambda c: (self.people[c].born is None,
                                           self.people[c].born or 0,
                                           self.people[c].name))

    def spouses_of(self, person_id: str) -> list[str]:
        return sorted(self._unions.get(person_id, set()))

    def siblings_of(self, person_id: str, *, lens: str = "legal") -> list[str]:
        out: set[str] = set()
        for parent in self.parents_of(person_id, lens=lens):
            out.update(self.children_of(parent, lens=lens))
        out.discard(person_id)
        return sorted(out, key=lambda s: (self.people[s].born or 0, self.people[s].name))

    def descendants_of(self, person_id: str, *, lens: str = "legal",
                       max_depth: int = 30) -> dict[str, int]:
        found: dict[str, int] = {}
        frontier = [(person_id, 0)]
        while frontier:
            current, depth = frontier.pop()
            if depth >= max_depth:
                continue
            for child in self.children_of(current, lens=lens):
                if child not in found:
                    found[child] = depth + 1
                    frontier.append((child, depth + 1))
        return found

    def ancestors_of(self, person_id: str, *, lens: str = "legal",
                     max_depth: int = 30) -> dict[str, int]:
        found: dict[str, int] = {}
        frontier = [(person_id, 0)]
        while frontier:
            current, depth = frontier.pop()
            if depth >= max_depth:
                continue
            for parent in self.parents_of(current, lens=lens):
                if parent not in found:
                    found[parent] = depth + 1
                    frontier.append((parent, depth + 1))
        return found

    def root_ancestors(self, person_id: str, *, lens: str = "legal") -> list[str]:
        """The earliest known forebears — where a succession walk starts."""
        ancestors = self.ancestors_of(person_id, lens=lens)
        return [a for a in ancestors if not self.parents_of(a, lens=lens)] or [person_id]

    def living_on(self, day: int) -> list[Person]:
        return [p for p in self.people.values() if p.alive_on(day)]

    def generation_of(self, person_id: str, root_id: str, *, lens: str = "legal") -> int | None:
        descendants = self.descendants_of(root_id, lens=lens)
        if person_id == root_id:
            return 0
        return descendants.get(person_id)

    def house_members(self, house_id: str) -> list[Person]:
        return [p for p in self.people.values() if p.house_id == house_id]

    def relationship_between(self, a_id: str, b_id: str, *, lens: str = "legal") -> str | None:
        """A plain-language kinship label, or None if no close link is found.

        Deliberately shallow: the point is to answer "how is this person related to that
        one" at a glance in the UI, not to produce a full canon-law consanguinity table.
        """
        if a_id == b_id:
            return "the same person"
        if b_id in self.parents_of(a_id, lens=lens):
            return "parent"
        if b_id in self.children_of(a_id, lens=lens):
            return "child"
        if b_id in self.siblings_of(a_id, lens=lens):
            shared = set(self.parents_of(a_id, lens=lens)) & set(self.parents_of(b_id, lens=lens))
            both = set(self.parents_of(a_id, lens=lens)) | set(self.parents_of(b_id, lens=lens))
            return "sibling" if len(shared) == len(both) else "half-sibling"
        if b_id in self.spouses_of(a_id):
            return "spouse"

        a_ancestors = self.ancestors_of(a_id, lens=lens)
        b_ancestors = self.ancestors_of(b_id, lens=lens)
        if b_id in a_ancestors:
            return "grandparent" if a_ancestors[b_id] == 2 else "ancestor"
        if a_id in b_ancestors:
            return "grandchild" if b_ancestors[a_id] == 2 else "descendant"

        shared_ancestors = set(a_ancestors) & set(b_ancestors)
        if shared_ancestors:
            nearest = min(shared_ancestors, key=lambda x: a_ancestors[x] + b_ancestors[x])
            da, db = a_ancestors[nearest], b_ancestors[nearest]
            if da == 2 and db == 2:
                return "first cousin"
            if {da, db} == {1, 2}:
                return "aunt or uncle" if da > db else "niece or nephew"
            return f"kin ({da} up, {db} down)"
        return None


def load_people(world: World, ids: Iterable[str]) -> dict[str, Person]:
    """Convenience for tests and engines that only need a handful of people."""
    genealogy = Genealogy(world)
    return {i: genealogy.people[i] for i in ids if i in genealogy.people}
