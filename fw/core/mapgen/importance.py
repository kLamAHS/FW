"""How much each thing on the map matters (V2 §31).

Until now the map had no answer: a capital, a hamlet and an unnamed brook carried the
same graphical weight, and every later decision that needed a hierarchy — which label
survives a crowded corner, which town is drawn at the world zoom, what the story mode
dims — had nowhere to look. This is that one number, computed once at plan time and
carried in `detail` like everything else a feature knows about itself.

Three components, because the brief's own examples pull in three directions: geography
(a city on the only ford of a great river), politics (a modest town that is somebody's
seat), and story (the village where six chapters happen matters more than its census
says). The components are kept in `importance_of` beside the total, so §67 holds — a
writer looking at a big label can see *why* the map thought the place deserved it.

Deliberately not: a simulation, a popularity contest between kinds (a coast always
outranks a road — that ordering is the composition's business, in `cartography.TYPE`),
or a writer-facing dial. It is derived, and rederived when the world changes.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from fw.core.mapgen.drafts import FeatureDraft

if TYPE_CHECKING:
    from fw.core.mapgen.source.reading import WorldReading

# What a settlement's stated size is worth before geography and story move it. The
# writer's own vocabulary, lowercased; anything unrecognised lands in the middle.
RANK_WORTH = {
    "capital": 1.0, "city": 0.85, "port": 0.7, "market town": 0.6,
    "fortress": 0.55, "town": 0.5, "village": 0.3, "hamlet": 0.18,
    "castle": 0.5, "keep": 0.4, "tower": 0.3,
}
DEFAULT_WORTH = 0.45

# Where each kind starts. Not a ranking between kinds — the solver has its own tiers —
# but how much of the [0,1] range a typical instance of the kind occupies, so a great
# river and a minor one end up far apart while two coasts do not.
KIND_FLOOR = {
    "coast": 0.9, "island": 0.45, "region": 0.55, "sea": 0.6, "water": 0.4,
    "lake": 0.4, "range": 0.5, "hills": 0.35, "river": 0.35, "natural": 0.25,
    "road": 0.3, "lane": 0.25, "settlement": 0.0, "castle": 0.0, "ruin": 0.2,
}

# The reasons that mark strategic ground. A town at a ford or a pass matters beyond
# its size, and the stages already argued exactly this in their `Reason.kind`s.
STRATEGIC = ("confluence", "mouth", "harbour", "ford", "pass", "crossing")


def grade(drafts: Iterable[FeatureDraft], reading: WorldReading | None) -> None:
    """Write `importance` and its breakdown into every draft's detail.

    Runs after all drafting, before assembly, so every feature on every plan carries
    the score — `DETAIL_KEYS` makes that a contract rather than a habit.
    """
    by_entity = dict(reading.by_entity()) if reading is not None else {}
    for draft in drafts:
        geography = _geography(draft)
        politics = _politics(draft, reading, by_entity)
        story = _story(draft, reading, by_entity)
        total = min(1.0, 0.55 * geography + 0.20 * politics + 0.25 * story)
        draft.detail["importance"] = round(total, 3)
        draft.detail["importance_of"] = {
            "geography": round(geography, 3),
            "politics": round(politics, 3),
            "story": round(story, 3),
        }


def _geography(draft: FeatureDraft) -> float:
    """The physical case: size, order, and standing on strategic ground."""
    detail = draft.detail
    base = KIND_FLOOR.get(draft.kind, 0.3)
    if draft.kind in ("settlement", "castle", "ruin"):
        base = RANK_WORTH.get(str(detail.get("rank", "")).lower(), DEFAULT_WORTH)
    elif draft.kind == "region":
        base += 0.45 * float(detail.get("share", 0.0))
    elif draft.kind == "river":
        # Order runs 1..5; a trunk river should stand clear of its brooks.
        base += 0.11 * (float(detail.get("strahler", 1)) - 1.0)
    elif draft.kind == "range":
        base += min(0.2, 0.05 * float(detail.get("elongation", 0.0)))
    strategic = [r.weight for r in draft.reasons if r.kind in STRATEGIC]
    if strategic:
        base += 0.15 * max(strategic)
    return min(1.0, base)


def _politics(draft: FeatureDraft, reading, by_entity: dict) -> float:
    """The human case: held ground, and halls somebody rules from."""
    if draft.kind == "region":
        held = draft.detail.get("politics") or {}
        return 0.6 if held.get("holder_key") else 0.0
    if draft.kind in ("settlement", "castle"):
        if any(r.kind == "seat" for r in draft.reasons):
            return 0.7
        key = _key_of(draft, by_entity)
        if (reading is not None and key is not None
                and reading.seat_of(key) is not None):
            return 0.7
        return 0.2 if draft.kind == "castle" else 0.0
    return 0.0


def _story(draft: FeatureDraft, reading, by_entity: dict) -> float:
    """The narrative case: where the writer keeps setting the story.

    Scenes count more than events — an event is a fact about the world, a scene is
    the writer spending pages there — and a destructive event counts more than a
    quiet one, because ruin is what a map remembers.
    """
    key = _key_of(draft, by_entity)
    if reading is None or key is None:
        return 0.0
    told = 0.25 * reading.presence.get(key, 0)
    for event in reading.events_at(key):
        told += 0.35 if event.destructive else 0.2
    return min(1.0, told)


def _key_of(draft: FeatureDraft, by_entity: dict):
    entity_id = draft.subject.entity_id if draft.subject is not None else None
    return by_entity.get(entity_id) if entity_id else None
