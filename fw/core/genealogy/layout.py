"""Pedigree layout (spec §39).

§39 asks for a specialised pedigree view, distinct from the generic relationship graph,
that stays readable for very large families. A force-directed graph is the wrong tool:
descent has a direction and generations are ranks, and a physics simulation throws that
structure away.

So this is a generational layout. Every person is assigned a generation from the root, then
ordered within their generation so that families stay together and couples stay adjacent,
then given coordinates. Marriages are drawn as links between adjacent nodes; children hang
below the union.

Layout is computed here rather than in the browser for a reason the plan settles: the
result is a deterministic `{node_id: (x, y)}` mapping, so "does the family tree look right"
becomes "are the coordinates right", which can be asserted in a test and reviewed in a diff.
Pixels cannot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.genealogy.kinship import Genealogy

NODE_WIDTH = 170
NODE_HEIGHT = 64
SIBLING_GAP = 28
GENERATION_GAP = 130


@dataclass
class LaidOutPerson:
    id: str
    name: str
    x: float
    y: float
    generation: int
    born: int | None = None
    died: int | None = None
    gender: str | None = None
    legitimacy: str = "legitimate"
    house_id: str | None = None
    collapsed: bool = False
    hidden_descendants: int = 0


@dataclass
class LaidOutUnion:
    """A marriage or partnership, drawn as a bar between two people."""

    a_id: str
    b_id: str
    x: float
    y: float


@dataclass
class LaidOutLink:
    """A parent-to-child line. `kind` drives the stroke: blood, legal, adoptive."""

    parent_id: str
    child_id: str
    kind: str = "biological"
    uncertain: bool = False


@dataclass
class Pedigree:
    people: list[LaidOutPerson] = field(default_factory=list)
    unions: list[LaidOutUnion] = field(default_factory=list)
    links: list[LaidOutLink] = field(default_factory=list)
    width: float = 0
    height: float = 0
    root_id: str | None = None

    def coordinates(self) -> dict[str, tuple[float, float]]:
        """The layout as bare numbers — what the golden tests assert against."""
        return {p.id: (p.x, p.y) for p in self.people}

    def overlaps(self) -> list[tuple[str, str]]:
        """Any two boxes sharing space. Should always be empty."""
        clashes = []
        for i, a in enumerate(self.people):
            for b in self.people[i + 1:]:
                if (abs(a.x - b.x) < NODE_WIDTH and abs(a.y - b.y) < NODE_HEIGHT):
                    clashes.append((a.id, b.id))
        return clashes


def layout_pedigree(
    genealogy: Genealogy,
    root_id: str,
    *,
    lens: str = "legal",
    max_generations: int = 12,
    collapsed: set[str] | None = None,
    living_only_on: int | None = None,
    house_id: str | None = None,
) -> Pedigree:
    """Lay out the descent from `root_id`.

    `collapsed` implements §39's branch collapsing: a collapsed person keeps their box but
    their descendants are omitted and counted, so a 2,000-person dynasty can be explored a
    branch at a time instead of rendered all at once.
    """
    collapsed = collapsed or set()
    pedigree = Pedigree(root_id=root_id)

    if root_id not in genealogy.people:
        return pedigree

    def included(person_id: str) -> bool:
        person = genealogy.people.get(person_id)
        if person is None:
            return False
        if living_only_on is not None and not person.alive_on(living_only_on):
            return False
        if house_id is not None and person.house_id != house_id:
            return False
        return True

    # ---- 1. assign generations, depth-first so siblings stay in birth order
    generations: dict[int, list[str]] = {}
    placed: dict[str, int] = {}

    def walk(person_id: str, generation: int) -> None:
        if generation >= max_generations or person_id in placed:
            return
        placed[person_id] = generation
        generations.setdefault(generation, []).append(person_id)
        if person_id in collapsed:
            return
        for child in genealogy.children_of(person_id, lens=lens):
            if included(child):
                walk(child, generation + 1)

    walk(root_id, 0)

    # ---- 2. bring in spouses, placed beside their partner in the same generation
    for generation in sorted(generations):
        for person_id in list(generations[generation]):
            for spouse in genealogy.spouses_of(person_id):
                if spouse in placed or not included(spouse):
                    continue
                index = generations[generation].index(person_id)
                generations[generation].insert(index + 1, spouse)
                placed[spouse] = generation

    # ---- 3. coordinates. x by position within the generation, y by generation.
    for generation in sorted(generations):
        row = generations[generation]
        total = len(row) * NODE_WIDTH + max(len(row) - 1, 0) * SIBLING_GAP
        start = -total / 2
        for index, person_id in enumerate(row):
            person = genealogy.people[person_id]
            x = start + index * (NODE_WIDTH + SIBLING_GAP)
            y = generation * (NODE_HEIGHT + GENERATION_GAP)
            hidden = 0
            if person_id in collapsed:
                hidden = len(genealogy.descendants_of(person_id, lens=lens))
            pedigree.people.append(LaidOutPerson(
                id=person.id, name=person.name, x=x, y=y, generation=generation,
                born=person.born, died=person.died, gender=person.gender,
                legitimacy=person.legitimacy.value, house_id=person.house_id,
                collapsed=person_id in collapsed, hidden_descendants=hidden,
            ))

    positions = {p.id: p for p in pedigree.people}

    # ---- 4. unions and parent links
    seen_unions: set[tuple[str, str]] = set()
    for person in pedigree.people:
        for spouse in genealogy.spouses_of(person.id):
            if spouse not in positions:
                continue
            pair = tuple(sorted((person.id, spouse)))
            if pair in seen_unions:
                continue
            seen_unions.add(pair)
            a, b = positions[pair[0]], positions[pair[1]]
            pedigree.unions.append(LaidOutUnion(
                a_id=pair[0], b_id=pair[1],
                x=(a.x + b.x) / 2 + NODE_WIDTH / 2,
                y=(a.y + b.y) / 2 + NODE_HEIGHT / 2,
            ))

    for person in pedigree.people:
        parentage = genealogy.parentage(person.id)
        for parent_id, kind in parentage.legal.items():
            if parent_id in positions:
                pedigree.links.append(LaidOutLink(parent_id, person.id, kind))
        # A biological parent who is not also a legal parent gets a distinct, dashed
        # link — §39 asks the pedigree to *indicate* uncertain and disputed parentage
        # rather than quietly pick one answer.
        for parent_id in parentage.biological:
            if parent_id in positions and parent_id not in parentage.legal:
                pedigree.links.append(
                    LaidOutLink(parent_id, person.id, "biological", uncertain=True)
                )

    if pedigree.people:
        xs = [p.x for p in pedigree.people]
        ys = [p.y for p in pedigree.people]
        pedigree.width = max(xs) - min(xs) + NODE_WIDTH
        pedigree.height = max(ys) - min(ys) + NODE_HEIGHT
    return pedigree
