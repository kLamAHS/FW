"""Wire shapes for the HTTP layer.

Deliberately thin: these mirror the core records rather than adding a second model. The
API is an adapter, and an adapter that starts making its own decisions about what a fact
means is the beginning of two competing domain models.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EntityOut(BaseModel):
    id: str
    type_key: str
    name: str
    summary: str = ""
    exists_from: int | None = None
    exists_to: int | None = None
    confidence: str = "canon"
    tags: list[str] = Field(default_factory=list)


class EntityIn(BaseModel):
    type_key: str
    name: str
    summary: str = ""
    exists_from: int | None = None
    exists_to: int | None = None
    confidence: str = "canon"
    tags: list[str] = Field(default_factory=list)


class EntityPatch(BaseModel):
    name: str | None = None
    summary: str | None = None
    exists_from: int | None = None
    exists_to: int | None = None
    confidence: str | None = None
    tags: list[str] | None = None


class FactOut(BaseModel):
    id: str
    subject_id: str
    subject_name: str = ""
    predicate_key: str
    predicate_label: str = ""
    object_id: str | None = None
    object_name: str | None = None
    value: str | None = None
    valid_from: int | None = None
    valid_to: int | None = None
    confidence: str = "canon"
    secrecy: str = "public"
    strength: str | None = None
    note: str = ""
    is_secret: bool = False
    # Written on every fact since the first migration and rendered nowhere, which is
    # worse than absent: a writer who cited a note could not see that they had.
    valid_from_text: str = ""
    valid_to_text: str = ""
    source: str = ""


class FactIn(BaseModel):
    subject_id: str
    predicate_key: str
    object_id: str | None = None
    value: str | None = None
    valid_from: int | None = None
    valid_to: int | None = None
    confidence: str = "canon"
    secrecy: str = "public"
    strength: str | None = None
    note: str = ""


class DateOut(BaseModel):
    """A day index rendered every way the UI might want it.

    `year` stays the absolute year facts are stored against; `era_year` is what the
    world's own reckoning calls it, which for a backward era counts the other way.
    """

    day: int
    text: str
    year: int
    month: int
    month_name: str
    day_of_month: int
    weekday: str
    season: str | None = None
    era: str | None = None
    era_name: str | None = None
    era_year: int | None = None


class CalendarOut(BaseModel):
    name: str
    months: list[dict[str, Any]]
    weekdays: list[str]
    days_in_year: int
    eras: list[dict[str, Any]]
    seasons: list[dict[str, Any]]


class WorldSummary(BaseModel):
    name: str
    description: str = ""
    present_day: int
    calendar: CalendarOut
    counts: dict[str, int]
    span: dict[str, int]
    branch: dict[str, Any] = Field(default_factory=dict)   # {name, is_canon}


class StateOut(BaseModel):
    day: int
    date: DateOut
    entities: list[EntityOut]
    facts: list[FactOut]
    titles: dict[str, str | None]


class ClaimantOut(BaseModel):
    position: int
    id: str
    name: str
    note: str = ""


class SuccessionOut(BaseModel):
    title_id: str
    title_name: str
    law_key: str
    law_label: str
    day: int
    hypothetical: bool
    assumptions: list[str]
    line: list[ClaimantOut]
    excluded: list[dict[str, str]]
    explanation: str


class ViolationOut(BaseModel):
    rule_key: str
    severity: str
    message: str
    entity_ids: list[str]
    day: int | None = None
    detail: str = ""
    fingerprint: str


class ContinuityOut(BaseModel):
    summary: str
    violations: list[ViolationOut]
    suppressed: int
    rules_run: int


class RouteOut(BaseModel):
    origin_id: str
    destination_id: str
    profile: str
    days: float
    distance: float
    path: list[str]
    path_names: list[str]
    legs: list[dict[str, Any]]
    explanation: str


class SceneContextOut(BaseModel):
    scene_id: str
    title: str
    date_text: str
    location: EntityOut | None = None
    participants: list[EntityOut]
    relationships: list[dict[str, Any]]
    secrets: list[dict[str, Any]]
    goals: list[dict[str, str]]
    recent_events: list[dict[str, Any]]
    tensions: list[str]
    world_state_notes: list[str]


class PedigreeOut(BaseModel):
    root_id: str | None
    width: float
    height: float
    people: list[dict[str, Any]]
    unions: list[dict[str, Any]]
    links: list[dict[str, Any]]


class GraphOut(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


class MapOut(BaseModel):
    day: int
    layers: list[str]
    features: list[dict[str, Any]]
    # How to draw it: the frame, the labels, the icons and the key, worked out on the
    # server so the same map is labelled the same way whatever is looking at it.
    draw: dict[str, Any] = {}


class QueryIn(BaseModel):
    """A question, as the form built it (§49). See `fw.core.query.language`."""

    query: dict[str, Any] = {}


class SaveQueryIn(BaseModel):
    name: str
    note: str = ""
    query: dict[str, Any] = {}


# ---- the write surfaces for §8's titles and §6's secrets ------------------
#
# Every one of these `World` methods has existed since the world model was written,
# is revision-logged and branch-scoped, and had no HTTP route and no form. Which meant
# succession, scene context and half the dashboard were dead code for any world but the
# seeded demo: a writer could not create a title, so nothing could be inherited; could
# not record a secret, so nobody could know one.

class TitleIn(BaseModel):
    name: str
    rank: int = 0
    territory_id: str | None = None
    succession_law: str = "male_preference_primogeniture"
    dynasty_root_id: str | None = None
    created_on: int | None = None
    entity_id: str | None = None


class GrantIn(BaseModel):
    holder_id: str
    from_day: int | None = None
    to_day: int | None = None
    how: str = "inheritance"
    disputed: bool = False
    note: str = ""


class SecretIn(BaseModel):
    name: str
    truth: str = ""
    about_id: str | None = None
    fact_id: str | None = None
    severity: str = "major"


class KnowledgeIn(BaseModel):
    observer_id: str
    secret_id: str
    stance: str
    about_observer_id: str | None = None
    acquired_on: int | None = None
    acquired_from: str | None = None
    scene_id: str | None = None
    note: str = ""


class SceneIn(BaseModel):
    title: str
    # Which chapter it belongs to (§43/§44). The column and its foreign key have been in
    # the schema since the first migration and nothing could set one, so every scene a
    # writer made was loose in the world rather than in their book.
    chapter_id: str | None = None
    position: int = 0
    day: int | None = None
    end_day: int | None = None
    location_id: str | None = None
    pov_id: str | None = None
    objective: str = ""
    conflict: str = ""
    outcome: str = ""
    notes: str = ""
    participants: list[str] = Field(default_factory=list)


class EventParticipantIn(BaseModel):
    id: str
    role: str = "participant"


class EventIn(BaseModel):
    name: str
    type_key: str = "event"
    summary: str = ""
    start_day: int | None = None
    end_day: int | None = None
    location_id: str | None = None
    participants: list[EventParticipantIn] = Field(default_factory=list)


class CausalLinkIn(BaseModel):
    cause_id: str
    effect_id: str
    note: str = ""


class EraIn(BaseModel):
    name: str
    abbreviation: str
    start_year: int | None = None
    end_year: int | None = None
    counts_backward: bool = False
    reckons_from: int | None = None


class EraPatch(BaseModel):
    name: str | None = None
    abbreviation: str | None = None
    start_year: int | None = None
    end_year: int | None = None
    counts_backward: bool | None = None
    reckons_from: int | None = None


class GenerateMapIn(BaseModel):
    seed: str | None = None
    propose_settlements: bool = True


class PlanMapIn(BaseModel):
    """What the writer can turn before a map is worked out."""

    seed: str | None = None
    # Which year to draw. §36 makes the whole world temporal and the map is part of the
    # world: a map of 1000 must not show a country founded in 1500. `MapBrief` has
    # carried the date since the plan existed and this had no way to set one, so every
    # proposal was of the present whatever the timeline said.
    at: int | None = None
    include: list[str] | None = None
    invent_settlements: bool = False
    north: str = "up"
    prevailing_wind: str = ""


class DecisionIn(BaseModel):
    feature_id: str
    accept: bool = True
    name: str | None = None
    pinned: bool = False


class ApplyMapIn(BaseModel):
    """A plan, answered. The plan comes back verbatim so the server writes exactly
    what the writer looked at rather than something recomputed underneath them."""

    plan: dict
    decisions: list[DecisionIn] = []


class SuppressIn(BaseModel):
    rule_key: str
    fingerprint: str
    reason: str = ""


class WorldCreate(BaseModel):
    name: str
    example: bool = False     # seed the §115 Kingdom of Renn instead of starting empty


class WorldOpen(BaseModel):
    file: str                 # a bare *.fwworld name inside the library, never a path


class BranchIn(BaseModel):
    name: str
    branched_at: int | None = None    # the day the timeline forks, usually "today"


class BranchOpen(BaseModel):
    name: str
