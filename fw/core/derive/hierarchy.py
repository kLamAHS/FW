"""Containment and belonging (§2, §12, §54).

Two questions a worldbuilder asks constantly, and which a flat entity list cannot answer:

- **What is inside this place?** A realm holds regions, a region holds cities, a city
  holds holdings — and the writer wants the whole tree at once, not one hop at a time.
- **Who belongs to this?** Not only the noble houses: the guilds, orders, tribes, sects,
  free companies and minor houses that sit under a banner or inside a border.

Both are already expressible on the fact spine — `located_in` is transitive, `member_of`
and `subgroup_of` exist — so nothing here is a new store. What was missing is the *walk*:
the recursion, the branch-correct reads, and the shape a page can render. Answers respect
the date, because a settlement founded later is not in the region yet, and they respect
the open timeline, because every read goes through the World facade.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.model.records import Entity
from fw.core.world import World

# Where a group belongs. `based_in` is its seat, `active_in` its reach; a writer who has
# only said `located_in` still gets found, because saying "the guild is in Greyhaven" is
# a reasonable thing to have written before the finer predicates existed.
PRESENCE_PREDICATES = ("based_in", "active_in", "located_in")

# What counts as a place rather than a group. Anything else that turns up inside a place
# is reported under its own type rather than recursed into.
PLACE_TYPES = ("realm", "region", "settlement", "holding", "site", "terrain_feature")

# Types that are groups of people, for the roster views. Writers add their own (§60), so
# this is a hint for grouping, never a gate: an unknown type is still shown.
GROUP_TYPES = ("house", "dynasty", "clan", "faction", "organization", "guild",
               "religion", "culture", "order", "tribe", "company", "household")

# Settlement ranks, largest first, so a region's cities sort before its hamlets.
SETTLEMENT_RANK = ("capital", "city", "town", "borough", "village", "hamlet",
                   "outpost", "ruin")


@dataclass
class PlaceNode:
    """One place and everything the writer put inside it."""

    entity: Entity
    depth: int
    children: list[PlaceNode] = field(default_factory=list)
    groups: list[Entity] = field(default_factory=list)     # seated or active here
    people: list[Entity] = field(default_factory=list)     # located here
    other: list[Entity] = field(default_factory=list)      # anything else inside
    settlement_type: str | None = None

    def count(self) -> int:
        """Everything at or below this node, itself excluded."""
        return (len(self.groups) + len(self.people) + len(self.other)
                + sum(1 + child.count() for child in self.children))


@dataclass
class Membership:
    """One entity's belonging to a group, and how it belongs."""

    entity: Entity
    relation: str          # 'member' | 'branch' | 'cadet branch' | 'sworn' | 'head'
    note: str = ""


class Hierarchy:
    """Walks containment and belonging over the fact spine."""

    def __init__(self, world: World) -> None:
        self.world = world

    # ---- places -----------------------------------------------------------

    def contents(self, place_id: str, *, at: int | None = None,
                 max_depth: int = 6) -> PlaceNode | None:
        """The tree of everything inside a place.

        Recursion is bounded and cycle-guarded: worlds do contain loops — a region
        recorded as inside its own province — and a walk that trusted the data would
        never come back.
        """
        root = self.world.get_entity(place_id)
        if root is None:
            return None
        return self._node(root, at=at, depth=0, max_depth=max_depth, seen={place_id})

    def _node(self, entity: Entity, *, at: int | None, depth: int, max_depth: int,
              seen: set[str]) -> PlaceNode:
        node = PlaceNode(entity=entity, depth=depth,
                         settlement_type=self._settlement_type(entity.id, at))
        if depth >= max_depth:
            return node

        for child in self._directly_inside(entity.id, at=at):
            if child.id in seen:
                continue                      # already on this path; a loop
            seen.add(child.id)
            if child.type_key in PLACE_TYPES:
                node.children.append(self._node(child, at=at, depth=depth + 1,
                                                max_depth=max_depth, seen=seen))
            elif child.type_key == "person":
                node.people.append(child)
            elif child.type_key in GROUP_TYPES:
                node.groups.append(child)
            else:
                node.other.append(child)

        # Groups seated or operating here arrive by their own predicates rather than by
        # containment, so they are gathered separately and merged without duplicates.
        known = {g.id for g in node.groups}
        for group in self._groups_at(entity.id, at=at):
            if group.id not in known and group.id not in seen:
                known.add(group.id)
                seen.add(group.id)
                node.groups.append(group)

        node.children.sort(key=lambda c: (_rank_of(c.settlement_type), c.entity.name))
        for bucket in (node.groups, node.people, node.other):
            bucket.sort(key=lambda e: e.name)
        return node

    def _settlement_type(self, entity_id: str, at: int | None) -> str | None:
        """A settlement's rank — city, town, village — as a dated property fact."""
        facts = self.world.facts_where("settlement_type", subject_id=entity_id, at=at)
        return facts[0].value if facts else None

    def _here_on(self, entity_id: str, at: int | None) -> Entity | None:
        """The entity, if it exists on this date.

        A containment fact often carries no dates of its own — "Greyhaven is in the
        Northmarch" is simply true — so the date has to be tested against the *thing*.
        Without this, a map of year 100 lists a city founded in 120.
        """
        entity = self.world.get_entity(entity_id)
        if entity is None:
            return None
        if at is not None and not entity.exists_on(at):
            return None
        return entity

    def _directly_inside(self, place_id: str, *, at: int | None) -> list[Entity]:
        """Everything one hop inside, by `located_in` read from the place's side."""
        out: dict[str, Entity] = {}
        for fact in self.world.facts_where("located_in", object_id=place_id, at=at):
            child = self._here_on(fact.subject_id, at)
            if child is not None:
                out[child.id] = child
        return list(out.values())

    def _groups_at(self, place_id: str, *, at: int | None) -> list[Entity]:
        out: dict[str, Entity] = {}
        for predicate in ("based_in", "active_in"):
            for fact in self.world.facts_where(predicate, object_id=place_id, at=at):
                group = self._here_on(fact.subject_id, at)
                if group is not None:
                    out[group.id] = group
        return list(out.values())

    def groups_in(self, place_id: str, *, at: int | None = None,
                  include_nested: bool = True) -> list[tuple[Entity, str]]:
        """Every group belonging to a place — and, by default, to anything inside it.

        This is the question "who is in the North?": the houses seated in its castles,
        the guilds working its towns, the orders that merely range across it.
        """
        places = [place_id]
        if include_nested:
            places += [eid for eid, _ in self.world.follow(
                place_id, "located_in", direction="in", max_depth=6, at=at)]
        found: dict[str, tuple[Entity, str]] = {}
        for pid in places:
            here = self.world.get_entity(pid)
            where = here.name if here else ""
            for predicate, how in (("based_in", "seated in"),
                                   ("active_in", "active in")):
                for fact in self.world.facts_where(predicate, object_id=pid, at=at):
                    group = self._here_on(fact.subject_id, at)
                    if group is None or group.id in found:
                        continue
                    found[group.id] = (group, f"{how} {where}" if where else how)
            for fact in self.world.facts_where("located_in", object_id=pid, at=at):
                sub = self._here_on(fact.subject_id, at)
                if sub is not None and sub.type_key in GROUP_TYPES \
                        and sub.id not in found:
                    found[sub.id] = (sub, f"in {where}" if where else "in this place")
        return sorted(found.values(), key=lambda pair: pair[0].name)

    def chain_above(self, entity_id: str, *, at: int | None = None) -> list[Entity]:
        """The containment chain upward — a city, then its region, then its realm."""
        out: list[Entity] = []
        seen = {entity_id}
        current = entity_id
        for _ in range(8):
            facts = self.world.facts_where("located_in", subject_id=current, at=at)
            nxt = next((f.object_id for f in facts
                        if f.object_id and f.object_id not in seen), None)
            if nxt is None:
                break
            parent = self.world.get_entity(nxt)
            if parent is None:
                break
            out.append(parent)
            seen.add(nxt)
            current = nxt
        return out

    # ---- groups -----------------------------------------------------------

    def members_of(self, group_id: str, *, at: int | None = None) -> list[Membership]:
        """Everyone and everything that belongs to a group.

        Members, branches, cadet branches, sworn bodies and its head all answer "who
        belongs here", so one roster carries them all with the relation named.
        """
        out: dict[str, Membership] = {}
        for predicate, relation in (("member_of", "member"),
                                    ("subgroup_of", "branch"),
                                    ("cadet_branch_of", "cadet branch"),
                                    ("sworn_to", "sworn"),
                                    ("vassal_of", "vassal")):
            for fact in self.world.facts_where(predicate, object_id=group_id, at=at):
                member = self.world.get_entity(fact.subject_id)
                if member is None or member.id in out:
                    continue
                out[member.id] = Membership(member, relation, fact.note)
        for fact in self.world.facts_where("head_of", object_id=group_id, at=at):
            head = self.world.get_entity(fact.subject_id)
            if head is not None:
                out[head.id] = Membership(head, "head", fact.note)
        return sorted(out.values(), key=lambda m: (m.relation != "head", m.entity.name))

    #: How a lesser body can hang beneath a greater one. A real chain mixes them —
    #: a cadet house sworn to a house that is vassal to another — so the walk must
    #: cross predicates rather than follow one at a time.
    UNDER_PREDICATES = ("subgroup_of", "vassal_of", "cadet_branch_of", "sworn_to")

    def branches_of(self, group_id: str, *, at: int | None = None,
                    max_depth: int = 6) -> list[tuple[Entity, int]]:
        """Every lesser body under this one, however deep — the minor-house tree.

        A breadth-first walk over all the ways one body sits under another, taken
        together: House Dray is a branch of House Marr, which is vassal to House Veyne,
        and asking Veyne for its lesser houses must reach Dray. Following a single
        predicate at a time would stop at the first change of kind.
        """
        found: dict[str, int] = {}
        frontier = [group_id]
        seen = {group_id}
        for depth in range(1, max_depth + 1):
            nxt: list[str] = []
            for node in frontier:
                for predicate in self.UNDER_PREDICATES:
                    for fact in self.world.facts_where(predicate, object_id=node, at=at):
                        lesser = self._here_on(fact.subject_id, at)
                        if lesser is None or lesser.id in seen:
                            continue
                        seen.add(lesser.id)
                        found[lesser.id] = depth
                        nxt.append(lesser.id)
            frontier = nxt
            if not frontier:
                break
        out = []
        for eid, depth in found.items():
            entity = self.world.get_entity(eid)
            if entity is not None:
                out.append((entity, depth))
        return sorted(out, key=lambda pair: (pair[1], pair[0].name))

    def seats_of(self, group_id: str, *, at: int | None = None) -> list[tuple[Entity, str]]:
        """Where a group belongs, from its own side — its seat, then its reach."""
        out: list[tuple[Entity, str]] = []
        seen: set[str] = set()
        for predicate, how in (("based_in", "based in"), ("active_in", "active in"),
                               ("located_in", "in")):
            for fact in self.world.facts_where(predicate, subject_id=group_id, at=at):
                if not fact.object_id or fact.object_id in seen:
                    continue
                place = self.world.get_entity(fact.object_id)
                if place is not None:
                    seen.add(place.id)
                    out.append((place, how))
        return out

    def groups(self, *, at: int | None = None) -> list[Entity]:
        """Every group in the world, for the roster view."""
        out: list[Entity] = []
        for type_key in GROUP_TYPES:
            out.extend(self.world.entities(type_key))
        if at is not None:
            out = [e for e in out if e.exists_on(at)]
        return sorted(out, key=lambda e: (e.type_key, e.name))


def _rank_of(settlement_type: str | None) -> int:
    """Sort key so a region lists its cities before its hamlets."""
    if not settlement_type:
        return len(SETTLEMENT_RANK)
    key = settlement_type.strip().lower()
    return SETTLEMENT_RANK.index(key) if key in SETTLEMENT_RANK else len(SETTLEMENT_RANK)
