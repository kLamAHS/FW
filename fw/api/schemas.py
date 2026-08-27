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
    """A day index rendered every way the UI might want it."""

    day: int
    text: str
    year: int
    month: int
    month_name: str
    day_of_month: int
    weekday: str
    season: str | None = None
    era: str | None = None


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


class SceneIn(BaseModel):
    title: str
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


class SuppressIn(BaseModel):
    rule_key: str
    fingerprint: str
    reason: str = ""


class WorldCreate(BaseModel):
    name: str
    example: bool = False     # seed the §115 Kingdom of Renn instead of starting empty


class WorldOpen(BaseModel):
    file: str                 # a bare *.fwworld name inside the library, never a path
