"""The world as one party sees it (§93, §94).

§94 asks for the world viewed "from a selected perspective" — objective, House Veyne,
Mara, the Northern Church, the Merchant Guild — and names five things a perspective may
change: known information, political labels, territorial claims, historical interpretation
and geography knowledge. §93 adds the map half: what a character or a culture knows
*geographically*, and the writer's own example is "objective map versus Mara's
understanding of the world".

Nothing here is new machinery. Every one of those five already exists somewhere in this
application and nothing joined them:

    known information      knowledge_state, and secrecy on the fact spine
    political labels       `interpretation` with an entity_id (the previous commit)
    territorial claims     §11's five authorities, `claims` among them
    historical interpretation  `interpretation` with an event_id
    geography knowledge    `unaware_of` on the fact spine

So a perspective is a *reading*, like `WorldReading` in the map generator: it computes
nothing the world does not already say, writes nothing, and answers questions about one
observer on one day.

**Ignorance is opt-in.** §93 says "optionally represent", and a perspective that hid
everything the writer had not explicitly granted would make the feature useless on any
world but a heavily annotated one — switch to House Marr and see an empty map. So the
default is that an observer knows of everything, and `unaware_of` marks the exceptions.
That is also the honest reading of §66: the writer says what is hidden; the software does
not guess.

**Every difference carries its reason.** §67 refuses black boxes and §14 insists derived
information is "suggestions rather than unquestionable truth", so `differences()` returns
the same `Finding` shape the dependency analyst uses — what changed, and the fact that
changed it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fw.core.derive.dependency import Finding
from fw.core.world import World

# What an observer may be blind to, said as a fact so it is dated, undoable, branch-aware
# and askable in the query builder like everything else. `knows_about` is its opposite and
# has existed since the first vocabulary; this is the half that was missing.
UNAWARE = "unaware_of"


@dataclass
class Perspective:
    """One observer's view of the world on one day. Reads only; writes nothing."""

    world: World
    observer_id: str | None
    day: int
    _blind: set[str] = field(default_factory=set, init=False)
    _labels: dict[str, str] = field(default_factory=dict, init=False)
    _accounts: dict[str, str] = field(default_factory=dict, init=False)
    _claims: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        if self.observer_id is None:
            return
        # Everything this observer says, read once. A perspective is applied to every
        # feature on a map, so a per-feature query would be a few hundred round trips.
        self._blind = {
            fact.object_id
            for fact in self.world.facts_where(UNAWARE, subject_id=self.observer_id,
                                               at=self.day)
            if fact.object_id
        }
        for told in self.world.interpretations(holder_id=self.observer_id):
            if told.entity_id:
                self._labels[told.entity_id] = told.label
            elif told.event_id:
                self._accounts[told.event_id] = told.account or told.label
        self._claims = {
            fact.object_id
            for fact in self.world.facts_where("claims", subject_id=self.observer_id,
                                               at=self.day)
            if fact.object_id
        }

    # ---- the five things a perspective may change -------------------------

    @property
    def objective(self) -> bool:
        """The view from nowhere, which is what the application shows by default."""
        return self.observer_id is None

    def sees(self, entity_id: str) -> bool:
        """Whether this observer has heard of something.

        True unless the writer has said otherwise: fog is drawn where they drew it.
        """
        return entity_id not in self._blind

    def name_for(self, entity_id: str, canon: str) -> str:
        """What they call it, or what everyone else calls it."""
        return self._labels.get(entity_id, canon)

    def account_of(self, event_id: str) -> str | None:
        """Their version of what happened, if they have one."""
        return self._accounts.get(event_id)

    def claims(self, entity_id: str) -> bool:
        """Whether this observer says the place is theirs, whatever the law says."""
        return entity_id in self._claims

    def holder_of(self, entity_id: str,
                  control: dict[str, list[dict[str, str]]],
                  mode: str) -> list[dict[str, str]]:
        """Who holds a place, as this observer would say it.

        Their own claim is substituted into whichever authority the map is showing,
        rather than the map being switched wholesale to `claims`. Switching the mode
        would leave every place *nobody* claims uncoloured, so House Orren's map would
        be one province and a grey continent — a worse picture than the objective one,
        and not what they believe either. Substituting says the true thing: the map is
        the ordinary map, with the ground they say is theirs shown as theirs.
        """
        if self.observer_id and entity_id in self._claims:
            me = self.world.get_entity(self.observer_id)
            if me is not None:
                return [{"id": me.id, "name": me.name}]
        return control.get(mode, [])

    # ---- and what it costs the writer to know ------------------------------

    def differences(self) -> list[Finding]:
        """Everything this view changes, with the reason for each (§67).

        Without this a perspective is a black box that quietly alters a map, which is
        precisely what §67 forbids: the writer must be able to see that House Marr's map
        differs *because* House Marr claims the Northmarch, and disagree.
        """
        if self.objective:
            return []
        who = self._name(self.observer_id)
        out: list[Finding] = []

        for entity_id in sorted(self._blind):
            out.append(Finding(
                text=f"{who} has never heard of {self._name(entity_id)}.",
                weight=3, kind="hidden", entity_ids=[entity_id],
                evidence=[f"{who} is recorded as unaware of it on this date."]))

        for entity_id, label in sorted(self._labels.items()):
            canon = self._name(entity_id)
            if label != canon:
                out.append(Finding(
                    text=f"{who} calls {canon} “{label}”.",
                    weight=2, kind="renamed", entity_ids=[entity_id],
                    evidence=[f"{who}'s own name for them."]))

        for entity_id in sorted(self._claims):
            out.append(Finding(
                text=f"{who} claims {self._name(entity_id)}, "
                     f"so their map shows it as theirs.",
                weight=4, kind="claimed", entity_ids=[entity_id],
                evidence=["A claim recorded on this date, which the law may not agree "
                          "with."]))

        for event_id, account in sorted(self._accounts.items()):
            event = self.world.get_event(event_id)
            if event is not None:
                out.append(Finding(
                    text=f"{who} tells {event.name} differently: {account}",
                    weight=2, kind="told", entity_ids=[],
                    evidence=[f"{who}'s account of it."]))

        out.sort(key=lambda f: (-f.weight, f.text))
        return out

    def _name(self, entity_id: str | None) -> str:
        if not entity_id:
            return "Nobody"
        found = self.world.get_entity(entity_id)
        return found.name if found else "something no longer in the world"


def who_can_be_one(world: World) -> list[dict[str, str]]:
    """Everybody whose view of the world differs from everyone else's (§94).

    Offering every entity would be a picker of hundreds in which almost every choice
    changes nothing. A perspective is worth having only for a party that has said
    something — an opinion, a claim, or an ignorance — so that is the list.
    """
    voices: dict[str, set[str]] = {}

    def note(entity_id: str | None, because: str) -> None:
        if entity_id:
            voices.setdefault(entity_id, set()).add(because)

    for told in world.interpretations():
        note(told.holder_id, "an account of their own"
             if told.event_id else "their own name for somebody")
    for fact in world.facts_where("claims"):
        note(fact.subject_id, "territory they claim")
    for fact in world.facts_where(UNAWARE):
        note(fact.subject_id, "places they have not heard of")

    out = []
    for entity_id, reasons in voices.items():
        entity = world.get_entity(entity_id)
        if entity is None:
            continue
        out.append({"id": entity.id, "name": entity.name, "type_key": entity.type_key,
                    "because": "; ".join(sorted(reasons))})
    return sorted(out, key=lambda row: row["name"])
