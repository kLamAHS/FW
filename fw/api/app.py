"""The HTTP adapter.

A thin translation layer: each route unpacks a request, calls the core, and packs the
result. No world logic lives here — that is the contract `import-linter` enforces in CI, and
it is what keeps the succession engine testable without a server.

The app serves the built React client too, so a writer runs one command and opens one page.
Everything is bound to localhost by default: §63 says the payload is unpublished manuscripts
and private creative work, so the network surface should be the loopback interface and
nothing else.
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fw.api import schemas as S
from fw.core import query as QY
from fw.core.calendar.kernel import CalendarError
from fw.core.continuity.engine import ContinuityEngine, Severity
from fw.core.derive.dependency import DependencyAnalyst
from fw.core.derive.hierarchy import GROUP_TYPES, Hierarchy
from fw.core.derive.perspective import Perspective, who_can_be_one
from fw.core.derive.scene_context import SceneContextEngine
from fw.core.derive.supply import SupplyAnalyst
from fw.core.genealogy.kinship import Genealogy
from fw.core.genealogy.layout import layout_pedigree
from fw.core.geo.routing import LAND as ROUTING_LAND
from fw.core.geo.routing import PROFILES, Router
from fw.core.geo.routing import SAILED as ROUTING_SAILED
from fw.core.library import Library, LibraryError
from fw.core.mapgen import cartography, ledger
from fw.core.mapgen import drafts as MG_DRAFTS
from fw.core.mapgen import plan as MG
from fw.core.mapgen.generate import generate_map
from fw.core.model.vocabulary import (
    CONFIDENCE_LEVELS,
    KNOWLEDGE_STANCES,
    PREDICATES_BY_KEY,
    SCALES,
    SECRET_SEVERITIES,
)
from fw.core.store.db import StoreError
from fw.core.succession.engine import SuccessionEngine
from fw.core.succession.laws import LAWS
from fw.core.world import World, WorldError

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class OpenWorld:
    """The world the server currently has open — switchable while it runs.

    The launcher screen exists so the writer is never forced into a template world;
    that means the server must be able to start with *no* world and open one later.
    """

    def __init__(self, world: World | None = None) -> None:
        self.world = world

    def get(self) -> World:
        if self.world is None:
            raise HTTPException(
                409, "no world is open — create one or open a save first")
        return self.world

    def replace(self, world: World) -> World | None:
        old, self.world = self.world, world
        return old


class _WorldProxy:
    """Lets every route keep saying `world.entities()` while the world can change.

    Attribute access resolves against whatever is open *now*; with nothing open it is
    a 409, which the client reads as "show the launcher"."""

    def __init__(self, holder: OpenWorld) -> None:
        object.__setattr__(self, "_holder", holder)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._holder.get(), name)


def create_app(world: World | None = None, *, library: Library | None = None,
               present_day: int | None = None) -> FastAPI:
    app = FastAPI(
        title="FW — worldbuilding",
        description="An external cognitive model of a fictional world.",
        version="0.1.0",
    )
    holder = OpenWorld(world)
    app.state.holder = holder
    app.state.present_day = (
        present_day if present_day is not None
        else (_guess_present_day(world) if world is not None else 0)
    )
    # From here down, `world` is the proxy: every route reads through the holder, so
    # opening a different save retargets all of them at once.
    world = _WorldProxy(holder)  # type: ignore[assignment]

    @app.exception_handler(WorldError)
    async def _world_error(_request, exc: WorldError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # ---- the library (saves) ----------------------------------------------

    def _open_path(path: Path) -> dict[str, str]:
        try:
            fresh = World.open(path)
        except (StoreError, WorldError, sqlite3.DatabaseError) as exc:
            # A corrupt or foreign file is an answer for the launcher, not a crash.
            raise HTTPException(400, str(exc)) from exc
        stale = holder.replace(fresh)
        if stale is not None:
            _retire(stale)
        app.state.present_day = _guess_present_day(fresh)
        return {"file": path.name, "name": fresh.name}

    @app.get("/api/worlds")
    def list_worlds() -> dict[str, Any]:
        """The launcher's data: every save, and which one is open."""
        current = holder.world
        open_name = None
        if current is not None:
            open_name = Path(str(current.db.path)).name
        if library is None:
            return {"library": None, "worlds": [], "open": open_name}
        library.ensure()
        return {
            "library": str(library.directory),
            "worlds": [vars(entry) for entry in library.worlds()],
            "open": open_name,
        }

    @app.post("/api/worlds", status_code=201)
    def create_world(payload: S.WorldCreate) -> dict[str, str]:
        """A new save — empty, or seeded with the example kingdom if asked."""
        if library is None:
            raise HTTPException(
                400, "this server was started on a single world file")
        try:
            path = library.create(payload.name, example=payload.example)
        except LibraryError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _open_path(path)

    @app.post("/api/worlds/open")
    def open_world(payload: S.WorldOpen) -> dict[str, str]:
        if library is None:
            raise HTTPException(
                400, "this server was started on a single world file")
        try:
            path = library.path_of(payload.file)
        except LibraryError as exc:
            raise HTTPException(404, str(exc)) from exc
        return _open_path(path)

    # ---- timelines (§105) --------------------------------------------------

    @app.get("/api/branches")
    def list_branches() -> list[dict[str, Any]]:
        return world.branches()

    @app.post("/api/branches", status_code=201)
    def create_branch(payload: S.BranchIn) -> dict[str, str]:
        """Fork a new timeline from the current one, and switch to it."""
        current = holder.get()
        name = current.create_branch(payload.name, branched_at=payload.branched_at)
        # Same connection, different branch — nothing to retire or close.
        holder.world = current.on_branch(name)
        app.state.present_day = _guess_present_day(holder.world)
        return {"name": name}

    @app.post("/api/branches/open")
    def open_branch(payload: S.BranchOpen) -> dict[str, str]:
        current = holder.get()
        try:
            holder.world = current.on_branch(payload.name)
        except WorldError as exc:
            raise HTTPException(404, str(exc)) from exc
        app.state.present_day = _guess_present_day(holder.world)
        return {"name": payload.name}

    # ---- world ------------------------------------------------------------

    @app.get("/api/world", response_model=S.WorldSummary)
    def get_world() -> S.WorldSummary:
        counts = world.counts_by_type()
        counts["total"] = sum(counts.values())
        counts["facts"] = world.count_facts()
        counts["events"] = world.count_events()
        counts["scenes"] = len(world.scenes())
        counts["secrets"] = len(world.secrets())

        return S.WorldSummary(
            name=world.name,
            description=world.db.scalar(
                "SELECT description FROM project WHERE id = ?", (world.project_id,)) or "",
            present_day=app.state.present_day,
            calendar=_calendar_out(world),
            counts=counts,
            span=_world_span(world),
            branch={"name": world.branch_name, "is_canon": world.is_canon},
        )

    @app.get("/api/vocabulary")
    def get_vocabulary() -> dict[str, Any]:
        """Entity types, predicates and scales — everything the UI needs to render forms."""
        return {
            "entity_types": [dict(r) for r in world.db.query(
                "SELECT key, label, plural, category, icon, core_fields FROM entity_type "
                "WHERE project_id = ? ORDER BY category, label", (world.project_id,))],
            "predicates": [dict(r) for r in world.db.query(
                "SELECT key, label, kind, inverse_key, symmetric, transitive, category, "
                "scale_key, description FROM predicate WHERE project_id = ? "
                "ORDER BY category, label", (world.project_id,))],
            "scales": [dict(r) for r in world.db.query(
                "SELECT key, label, steps FROM scale WHERE project_id = ?",
                (world.project_id,))],
            "succession_laws": [
                {"key": k, "label": v.label, "description": v.description}
                for k, v in LAWS.items()
            ],
            "transport_profiles": [
                {"key": k, "label": v.label, "description": v.description}
                for k, v in PROFILES.items()
            ],
            # Served rather than spelled again in the client: a form offering a word the
            # server refuses is a form that produces errors nobody can act on, and a
            # form missing a word the server takes hides a feature that exists.
            "source_kinds": [{"key": k, "label": v}
                             for k, v in SOURCE_KINDS.items()],
            "route_media": list(SEGMENT_MEDIA),
            "route_sailed": list(ROUTING_SAILED),
            "route_terrains": list(SEGMENT_TERRAINS),
        }

    # §60. The README has said, accurately, that this works "through the API or the CLI
    # rather than a screen". Both writers were there all along; a world that is not a
    # medieval European kingdom needed a Python prompt to say so.

    @app.post("/api/vocabulary/entity-types", status_code=201)
    def create_entity_type(payload: S.EntityTypeIn) -> dict[str, Any]:
        key = _checked_key(payload.key)
        label = payload.label.strip()
        if not label:
            raise HTTPException(422, "a kind of thing needs a name to call it by")
        if world.db.one("SELECT id FROM entity_type WHERE project_id = ? AND key = ?",
                        (world.project_id, key)):
            raise HTTPException(409, f"this world already has a {key!r}")
        world.add_entity_type(key, label, plural=payload.plural.strip(),
                              category=payload.category.strip() or "other",
                              icon=payload.icon.strip())
        return {"key": key, "label": label}

    @app.post("/api/vocabulary/predicates", status_code=201)
    def create_predicate(payload: S.PredicateIn) -> dict[str, Any]:
        key = _checked_key(payload.key)
        label = payload.label.strip()
        if not label:
            raise HTTPException(422, "a relationship needs a name to call it by")
        if payload.kind not in ("rel", "prop"):
            raise HTTPException(
                422, "a predicate either points at something ('rel') or holds a "
                     "value ('prop')")
        known = {p["key"] for p in world.db.query(
            "SELECT key FROM predicate WHERE project_id = ?", (world.project_id,))}
        if key in known:
            raise HTTPException(409, f"this world already has a {key!r}")
        # §77: the inverse is what puts the fact on the other entity's page too. A name
        # for a predicate that does not exist would put it on nobody's.
        if payload.inverse_key and payload.inverse_key not in known:
            raise HTTPException(
                404, f"there is no {payload.inverse_key!r} to be the other side of it")
        world.add_predicate(
            key, label, kind=payload.kind, inverse_key=payload.inverse_key,
            symmetric=payload.symmetric, transitive=payload.transitive,
            datatype=payload.datatype, category=payload.category.strip() or "other",
            description=payload.description)
        return {"key": key, "label": label}

    @app.get("/api/date/{day}", response_model=S.DateOut)
    def get_date(day: int) -> S.DateOut:
        return _date_out(world, day)

    @app.get("/api/day", response_model=S.DateOut)
    def get_day(year: int, month: int = 1, day: int = 1,
                era: str | None = None) -> S.DateOut:
        """Civil date → day index, for form inputs.

        The client could compute this itself from the calendar payload, but leap rules
        make client-side conversion a second implementation waiting to disagree with the
        first. One source of truth; the round trip is a few milliseconds on localhost.

        Naming an era reads the year in that era's terms, so "100 BR" can be typed as
        readily as it is displayed — a backward era means 100 BR is an earlier year than
        50 BR, and the conversion is the calendar's business, not the form's.
        """
        try:
            return _date_out(world, world.calendar.date_in_era(year, month, day, era))
        except CalendarError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/eras")
    def list_eras() -> list[dict[str, Any]]:
        """§3: the world's own time dividers."""
        return world.eras()

    @app.post("/api/eras", status_code=201)
    def create_era(payload: S.EraIn) -> dict[str, str]:
        return {"id": world.add_era(
            payload.name, payload.abbreviation, start_year=payload.start_year,
            end_year=payload.end_year, counts_backward=payload.counts_backward,
            reckons_from=payload.reckons_from)}

    @app.patch("/api/eras/{era_id}", status_code=204)
    def patch_era(era_id: str, payload: S.EraPatch) -> None:
        world.update_era(era_id, **payload.model_dump(exclude_unset=True))

    @app.delete("/api/eras/{era_id}", status_code=204)
    def remove_era(era_id: str) -> None:
        world.delete_era(era_id)

    @app.get("/api/recent")
    def get_recent(limit: int = Query(8, le=50)) -> list[dict[str, Any]]:
        """§74: recently edited entities, for the dashboard."""
        return [
            {"entity": _entity_out(entity).model_dump(), "at": at}
            for entity, at in world.recently_edited(limit=limit)
        ]

    @app.get("/api/entities/{entity_id}/history")
    def get_history(entity_id: str, limit: int = Query(50, le=200)) -> list[dict[str, Any]]:
        """§59: the change history of one entity, newest first."""
        if world.get_entity(entity_id) is None:
            raise HTTPException(404, f"no entity {entity_id}")
        return world.revisions_for(entity_id, limit=limit)

    @app.get("/api/deleted")
    def get_deleted(limit: int = Query(10, le=50)) -> list[dict[str, Any]]:
        """§59: deletions that can still be undone. A deleted entity has no page left,
        so this list is where the writer finds the way back."""
        return world.recently_deleted(limit=limit)

    @app.post("/api/revisions/{revision_id}/restore")
    def restore_revision(revision_id: int) -> dict[str, str]:
        return {"message": world.restore(revision_id)}

    @app.get("/api/undo")
    def get_undo_state() -> dict[str, Any]:
        """Whether undo/redo would do anything, and what — for the toolbar."""
        return world.undo_state()

    @app.post("/api/undo")
    def do_undo() -> dict[str, str]:
        return {"message": world.undo()}

    @app.post("/api/redo")
    def do_redo() -> dict[str, str]:
        return {"message": world.redo()}

    @app.get("/api/snapshots")
    def get_snapshots() -> list[dict[str, Any]]:
        return [
            {**s, "date": _date_out(world, s["day"]).model_dump()}
            for s in world.snapshots()
        ]

    # ---- whose world is this? (§93, §94) ----------------------------------

    @app.get("/api/perspectives")
    def list_perspectives() -> list[dict[str, Any]]:
        """Everybody whose view of the world differs from everyone else's.

        Not every entity: a picker of hundreds in which almost every choice changes
        nothing would be worse than no picker. A party earns a place here by having said
        something — an account, a claim, or an ignorance.
        """
        return who_can_be_one(world)

    @app.get("/api/perspectives/{observer_id}")
    def describe_perspective(observer_id: str, day: int | None = None) -> dict[str, Any]:
        """What changes when you look through their eyes, and why each thing changes.

        §67 refuses black boxes, and a view that quietly altered a map would be the
        purest kind: the writer has to be able to see that House Marr's map differs
        *because* House Marr claims the Northmarch — and to disagree.
        """
        at = day if day is not None else app.state.present_day
        seen = _seen_by(world, observer_id, at)
        return {
            "observer_id": observer_id,
            "observer_name": _name_of(world, observer_id),
            "day": at,
            "differences": [_finding_out(world, f) for f in seen.differences()],
        }

    @app.get("/api/interpretations")
    def list_interpretations(event_id: str | None = None, entity_id: str | None = None,
                             holder_id: str | None = None) -> list[dict[str, Any]]:
        """The same event told differently, and the same person named differently (§33).

        Returned with the holder's name resolved, because "House Marr" is the whole point
        of the row and an id is not something a writer can read.
        """
        return [{
            "id": row.id, "label": row.label, "account": row.account,
            "event_id": row.event_id, "entity_id": row.entity_id,
            "holder_id": row.holder_id,
            "holder_name": _name_of(world, row.holder_id),
            "subject_name": (_name_of(world, row.entity_id) if row.entity_id
                             else _event_name(world, row.event_id)),
        } for row in world.interpretations(event_id=event_id, entity_id=entity_id,
                                           holder_id=holder_id)]

    @app.post("/api/interpretations", status_code=201)
    def create_interpretation(payload: S.InterpretationIn) -> dict[str, Any]:
        label = payload.label.strip()
        if not label:
            raise HTTPException(422, "an account needs something to call it")
        if (payload.event_id is None) == (payload.entity_id is None):
            raise HTTPException(
                422, "an account is of one event or one thing — not both, and not neither")
        _require_entities(payload.entity_id, payload.holder_id)
        if payload.event_id and world.get_event(payload.event_id) is None:
            raise HTTPException(404, "there is no such event to have an account of")
        made = world.add_interpretation(
            label, event_id=payload.event_id, entity_id=payload.entity_id,
            holder_id=payload.holder_id, account=payload.account)
        return {"id": made.id, "label": made.label}

    @app.delete("/api/interpretations/{interpretation_id}", status_code=204)
    def forget_interpretation(interpretation_id: str) -> Response:
        try:
            world.delete_interpretation(interpretation_id)
        except WorldError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(status_code=204)

    @app.post("/api/snapshots", status_code=201)
    def create_snapshot(payload: S.SnapshotIn) -> dict[str, Any]:
        name = payload.name.strip()
        if not name:
            raise HTTPException(422, "a moment needs a name to be remembered by")
        snapshot_id = world.add_snapshot(name, payload.day, note=payload.note)
        return {"id": snapshot_id, "name": name, "day": payload.day,
                "date": _date_out(world, payload.day).model_dump()}

    @app.delete("/api/snapshots/{snapshot_id}", status_code=204)
    def forget_snapshot(snapshot_id: str) -> Response:
        """Take the name back off. The day, and everything true on it, stays."""
        try:
            world.delete_snapshot(snapshot_id)
        except WorldError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(status_code=204)

    # ---- entities ---------------------------------------------------------

    @app.get("/api/entities", response_model=list[S.EntityOut])
    def list_entities(
        type_key: str | None = None,
        at: int | None = None,
        limit: int = Query(500, le=5000),
        hide_generated: bool = False,
    ) -> list[S.EntityOut]:
        """Everything in the world, or everything the writer put there (§66).

        A map proposes towns and castles by the dozen, and the writer's own list of
        settlements is where they look for the ones they wrote. `hide_generated` leaves
        out what the map suggested and they have not accepted — never what they accepted,
        which is theirs now and keeps its tag only so the next run knows it again.
        """
        entities = world.entities(type_key, limit=limit)
        if at is not None:
            entities = [e for e in entities if e.exists_on(at)]
        if hide_generated:
            entities = [e for e in entities if not e.is_a_map_proposal]
        return [_entity_out(e) for e in entities]

    @app.post("/api/entities", response_model=S.EntityOut, status_code=201)
    def create_entity(payload: S.EntityIn) -> S.EntityOut:
        entity = world.add_entity(
            payload.type_key, payload.name, summary=payload.summary,
            exists_from=payload.exists_from, exists_to=payload.exists_to,
            confidence=payload.confidence, tags=payload.tags,
        )
        return _entity_out(entity)

    @app.get("/api/entities/{entity_id}")
    def get_entity(entity_id: str, at: int | None = None) -> dict[str, Any]:
        """One entity with everything its page needs, in a single round trip.

        §76's side panel and §75's entity page both want the same bundle, and making the
        client stitch four requests together would show as a flicker on every click.
        """
        entity = world.get_entity(entity_id)
        if entity is None:
            raise HTTPException(404, f"no entity {entity_id}")

        facts = world.facts_about(entity_id, at=at)
        events = world.events_involving(entity_id)
        titles = world.titles_held_by(entity_id, at=at)

        knowledge = []
        for state in world.knowledge_held_by(entity_id, at=at):
            secret = next((s for s in world.secrets() if s.id == state.secret_id), None)
            if secret:
                knowledge.append({
                    "secret_id": secret.id, "secret_name": secret.name,
                    "stance": state.stance,
                    "about_observer_id": state.about_observer_id,
                    "acquired_on": state.acquired_on, "note": state.note,
                })

        geometry = world.geometry_for(entity_id, at=at)
        scenes = [
            {"id": s.id, "title": s.title, "day": s.day}
            for s in world.scenes()
            if s.location_id == entity_id
            or entity_id in {p.id for p in world.scene_participants(s.id)}
        ]

        return {
            "entity": _entity_out(entity).model_dump(),
            # Where this sits: a city inside its region inside its realm. One walk of at
            # most a few hops, and it saves the writer holding the map in their head.
            "within": [
                {"id": e.id, "name": e.name, "type_key": e.type_key}
                for e in Hierarchy(holder.get()).chain_above(entity_id, at=at)
            ],
            "facts": [_fact_out(world, f).model_dump() for f in facts],
            "events": [
                {"id": e.id, "name": e.name, "start_day": e.start_day,
                 "type_key": e.type_key, "summary": e.summary}
                for e in events
            ],
            "titles": [
                {"id": t.id, "name": t.name, "rank": t.rank,
                 "succession_law": t.succession_law}
                for t in titles
            ],
            "knowledge": knowledge,
            "geometry": (
                {"kind": geometry.kind, "coordinates": geometry.coordinates,
                 "layer": geometry.layer}
                if geometry else None
            ),
            "scenes": scenes,
        }

    @app.patch("/api/entities/{entity_id}", response_model=S.EntityOut)
    def patch_entity(entity_id: str, payload: S.EntityPatch) -> S.EntityOut:
        if world.get_entity(entity_id) is None:
            raise HTTPException(404, f"no entity {entity_id}")
        # exclude_unset, not exclude_none: an explicit null means "clear this" — the
        # edit form promises that a blanked year removes a date, and exclude_none was
        # silently discarding exactly that request. Fields with no meaningful null are
        # still guarded, so name: null cannot reach the NOT NULL column.
        changes = payload.model_dump(exclude_unset=True)
        for key in ("name", "summary", "confidence", "tags"):
            if key in changes and changes[key] is None:
                changes.pop(key)
        world.update_entity(entity_id, **changes)
        return _entity_out(world.get_entity(entity_id))

    @app.delete("/api/entities/{entity_id}", status_code=204)
    def delete_entity(entity_id: str) -> None:
        if world.get_entity(entity_id) is None:
            raise HTTPException(404, f"no entity {entity_id}")
        world.delete_entity(entity_id)

    # ---- facts ------------------------------------------------------------

    @app.get("/api/facts", response_model=list[S.FactOut])
    def list_facts(
        predicate_key: str | None = None,
        subject_id: str | None = None,
        object_id: str | None = None,
        at: int | None = None,
    ) -> list[S.FactOut]:
        facts = world.facts_where(predicate_key, subject_id=subject_id,
                                  object_id=object_id, at=at)
        return [_fact_out(world, f) for f in facts]

    @app.post("/api/facts", response_model=S.FactOut, status_code=201)
    def create_fact(payload: S.FactIn) -> S.FactOut:
        fact = world.assert_fact(
            payload.subject_id, payload.predicate_key, payload.object_id,
            value=payload.value, valid_from=payload.valid_from,
            valid_to=payload.valid_to, confidence=payload.confidence,
            secrecy=payload.secrecy, strength=payload.strength, note=payload.note,
            source_id=_checked_source(world, payload.source_id),
            about_fact_id=_checked_fact(world, payload.about_fact_id),
        )
        return _fact_out(world, fact)

    @app.delete("/api/facts/{fact_id}", status_code=204)
    def delete_fact(fact_id: str) -> None:
        world.delete_fact(fact_id)

    @app.post("/api/facts/{fact_id}/end", response_model=S.FactOut)
    def end_fact(fact_id: str, on_day: int) -> S.FactOut:
        """Close a fact's validity rather than deleting it (§106.3)."""
        if world.get_fact(fact_id) is None:
            raise HTTPException(404, f"no fact {fact_id}")
        world.end_fact(fact_id, on_day)
        return _fact_out(world, world.get_fact(fact_id))

    # ---- world state (§3, §36) --------------------------------------------

    @app.get("/api/state", response_model=S.StateOut)
    def get_state(day: int, include_secret: bool = True,
                  seen_as: str | None = Query(None, alias="as")) -> S.StateOut:
        """What was true on one day — objectively, or as far as one party knows (§94)."""
        seen = _seen_by(world, seen_as, day)
        state = world.state_at(day, include_secret=include_secret)
        entities = [e for e in state.entities.values() if seen.sees(e.id)]
        known = {e.id for e in entities}
        return S.StateOut(
            day=day,
            date=_date_out(world, day),
            entities=[_entity_out(e).model_copy(
                          update={"name": seen.name_for(e.id, e.name)})
                      for e in entities],
            facts=[_fact_out(world, f) for f in state.facts
                   if f.subject_id in known
                   and (f.object_id is None or f.object_id in known)],
            titles=state.titles,
        )

    @app.get("/api/map", response_model=S.MapOut)
    def get_map(day: int | None = None, layer: str | None = None,
                mode: str = "legally_owns", labels: bool = True,
                seen_as: str | None = Query(None, alias="as")) -> S.MapOut:
        """§34/§35/§36: geometry for a date, with the control facts attached.

        Each feature carries who owns, administers, occupies, taxes and claims it on that
        day, so §11's distinction is visible on the map rather than only on entity pages.

        `as` draws it through somebody's eyes (§93, §94): places they have never heard of
        are absent, everything is labelled with their name for it, and the colouring
        follows their claims rather than the law's. Absent, it is the map from nowhere,
        which is what the writer sees by default.
        """
        at = day if day is not None else app.state.present_day
        seen = _seen_by(world, seen_as, at)
        features = []
        for geometry in world.geometries(at=at, layer=layer):
            entity = world.get_entity(geometry.entity_id)
            if entity is None or not entity.exists_on(at):
                continue
            if not seen.sees(entity.id):
                continue        # §93: they have never heard of this place
            control = {}
            for fact in world.facts_where(object_id=entity.id, at=at):
                if fact.predicate_key in ("legally_owns", "administers", "occupies",
                                          "taxes", "claims", "rules"):
                    whose = world.get_entity(fact.subject_id)
                    if whose:
                        control.setdefault(fact.predicate_key, []).append({
                            "id": whose.id, "name": whose.name,
                        })
            if not seen.objective and seen.claims(entity.id):
                # §94's territorial claims. Their claim is substituted into whatever
                # authority the map is showing, so the disagreement is rendered rather
                # than described — and the rest of the world still looks like itself.
                control = {**control, mode: seen.holder_of(entity.id, control, mode)}
            features.append({
                "id": geometry.id,
                "entity_id": entity.id,
                "name": seen.name_for(entity.id, entity.name),
                "type_key": entity.type_key,
                "kind": geometry.kind,
                "coordinates": geometry.coordinates,
                "layer": geometry.layer,
                "style": geometry.style,
                "approximate": geometry.approximate,
                # Whose shape this is. The map labels the writer's own things first,
                # and the client draws a generated edge as the guess it is.
                "generated": ledger.is_generated(geometry),
                "control": control,
            })
        layers = sorted({f["layer"] for f in features})
        ground = holder.get().terrain()
        extent = ([ground["origin_x"], ground["origin_y"],
                   ground["origin_x"] + ground["span"],
                   ground["origin_y"] + ground["span"]] if ground else None)
        drawn = cartography.draw(features, mode=mode, ground=extent, label=labels,
                                 world_name=world.name)
        return S.MapOut(day=at, layers=layers, features=features,
                        draw=drawn.as_dict(),
                        seen_as=seen_as, seen_as_name=_name_of(world, seen_as))

    @app.post("/api/geometry", status_code=201)
    def draw_a_shape(payload: S.GeometryIn) -> dict[str, Any]:
        """Draw something on the map yourself (§66).

        The principle the whole generator is built around — `_authored_outlines` refuses
        to redraw a region the writer drew, the coastline grows around their borders,
        their castles are pinned where they put them — and until now nothing could make
        such a shape, so that entire branch was reachable only for the seeded world.

        What comes back carries no provenance, which is exactly what makes
        `ledger.is_generated` say False: the next run treats it as truth, builds around
        it, and never retires it.
        """
        if world.get_entity(payload.entity_id) is None:
            raise HTTPException(404, f"no entity {payload.entity_id}")
        if payload.layer not in MAP_LAYERS:
            raise HTTPException(
                422, f"nothing draws on a layer called {payload.layer!r} "
                     f"(the layers are: {', '.join(MAP_LAYERS)})")
        coordinates = _checked_shape(payload.kind, payload.coordinates)
        drawn = world.add_geometry(
            payload.entity_id, payload.kind, coordinates,
            layer=payload.layer, style=payload.style,
            approximate=payload.approximate,
            valid_from=payload.valid_from, valid_to=payload.valid_to)
        return {"id": drawn.id, "entity_id": drawn.entity_id, "kind": drawn.kind,
                "layer": drawn.layer, "coordinates": drawn.coordinates}

    @app.delete("/api/geometry/{geometry_id}", status_code=204)
    def erase_a_shape(geometry_id: str) -> Response:
        """Rub one out. Undoable like everything else, and it takes its R*Tree box."""
        try:
            world.delete_geometry(geometry_id)
        except WorldError as exc:
            raise HTTPException(409, str(exc)) from exc
        return Response(status_code=204)

    @app.post("/api/map/plan")
    def plan_the_map(payload: S.PlanMapIn) -> dict[str, Any]:
        """Work out a map and return it without writing a thing (§66).

        The writer sees the whole proposal — every coastline, river, town and road,
        each with the case for it — before any of it exists. Nothing here touches the
        world, so looking costs nothing and a map they dislike is closed rather than
        undone.
        """
        from fw.core.mapgen.pipeline import plan_map

        brief = MG.MapBrief(
            seed=payload.seed or "",
            at=payload.at if payload.at is not None else app.state.present_day,
            include=tuple(payload.include) if payload.include else MG.MapBrief().include,
            invent_settlements=payload.invent_settlements,
            north=payload.north,
            prevailing_wind=payload.prevailing_wind,
        )
        current = holder.get()
        proposal = plan_map(current, brief)
        _remember_the_ground(current, proposal)
        # Labelled the same way the accepted map will be: a writer choosing between a
        # proposal and what they have needs the two drawn alike (§66).
        return {**proposal.to_dict(),
                "draw": cartography.from_plan(
                    proposal, world_name=current.name).as_dict()}

    @app.post("/api/map/apply")
    def apply_the_map(payload: S.ApplyMapIn) -> dict[str, Any]:
        """Write the parts of a plan the writer accepted, as one undoable action."""
        from fw.core.mapgen.apply import PlanStale, apply_plan
        from fw.core.mapgen.decide import Decision, DecisionSet

        current = holder.get()
        plan = _with_the_ground(current, MG.MapPlan.from_dict(payload.plan))
        answers = DecisionSet(plan_id=plan.plan_id, decisions=tuple(
            Decision(feature_id=d.feature_id, accept=d.accept, name=d.name,
                     pinned=d.pinned)
            for d in payload.decisions))
        try:
            report = apply_plan(current, plan, answers)
        except PlanStale as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return report.as_dict()

    @app.get("/api/map/relief.png")
    def the_relief(scale: int = 6) -> Response:
        """The ground itself, lit — the picture the vector map is drawn over.

        Everything else this endpoint's neighbours return is geometry, and the client
        draws geometry. Relief is not geometry: it is a height at every position, and
        the difference between a map that reads as a physical place and one that reads
        as a diagram is almost entirely in lighting that surface. So it comes back as an
        image, and the client puts it underneath everything else.

        Rendered from the fields the writer accepted rather than from a fresh run of the
        generator, so the mountains do not move under the towns already standing on them
        when a region is added. Cached on the fields' own timestamp: rendering is a
        couple of seconds and the answer only changes when a new map is accepted.
        """
        world = holder.get()
        ground = world.terrain()
        if ground is None:
            raise HTTPException(
                status_code=404,
                detail="no map has been accepted yet, so there is no ground to draw")

        scale = max(2, min(14, scale))
        stamp = (world.project_id, world.branch_id, ground["updated_at"], scale)
        cached = getattr(app.state, "relief_cache", None)
        if cached and cached[0] == stamp:
            return Response(content=cached[1], media_type="image/png",
                            headers={"Cache-Control": "no-cache"})

        from fw.core.mapgen import raster, shade
        from fw.core.mapgen.grid import Grid

        fields = ground["fields"]
        picture = shade.render(
            Grid(size=ground["size"], span=ground["span"],
                 origin_x=ground["origin_x"], origin_y=ground["origin_y"]),
            elevation=fields["elevation"], seed=ground["seed"], scale=scale,
            sea_level=ground["sea_level"], canopy=fields.get("canopy"),
            marsh=fields.get("marsh"))
        png = raster.encode(picture.width, picture.height, picture.pixels)
        app.state.relief_cache = (stamp, png)
        return Response(content=png, media_type="image/png",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/api/map/relief")
    def the_relief_bounds() -> dict[str, Any]:
        """Where the relief image belongs on the map, and whether there is one."""
        ground = holder.get().terrain()
        if ground is None:
            return {"available": False}
        span = ground["span"]
        return {
            "available": True,
            "x": ground["origin_x"], "y": ground["origin_y"],
            "width": span, "height": span,
            "updated_at": ground["updated_at"],
        }

    @app.post("/api/map/generate")
    def generate_the_map(payload: S.GenerateMapIn) -> dict[str, Any]:
        """§34: grow a map from what the regions say about themselves.

        Everything the run writes is marked as generated, so running it again replaces
        its own work and never the writer's — and the whole thing is one undoable
        action, so a map the writer dislikes is one Ctrl+Z away.
        """
        report = generate_map(
            holder.get(), seed=payload.seed or None,
            at=app.state.present_day,
            propose_settlements=payload.propose_settlements)
        # Kept as it was: one press, one map, one undo. The two-step route above is
        # for writers who would rather look before it lands.
        return {
            "summary": report.summary(),
            "regions_drawn": report.regions_drawn,
            "regions_kept": report.regions_kept,
            "rivers": report.rivers,
            "roads": report.roads,
            "notes": report.notes,
            "placements": [
                {"entity_id": p.entity_id, "name": p.name, "x": p.x, "y": p.y,
                 "rank": p.rank, "proposed": p.proposed, "why": p.because()}
                for p in report.placements
            ],
        }

    # ---- search (§53) -----------------------------------------------------

    @app.get("/api/search", response_model=list[S.EntityOut])
    def search(q: str, type_key: str | None = None,
               limit: int = Query(30, le=200)) -> list[S.EntityOut]:
        return [_entity_out(e) for e in world.search(q, limit=limit, type_key=type_key)]

    # ---- graph (§38) ------------------------------------------------------

    @app.get("/api/graph", response_model=S.GraphOut)
    def get_graph(
        day: int | None = None,
        categories: str | None = None,
        centre: str | None = None,
        hops: int = 2,
        limit: int = Query(400, le=2000),
    ) -> S.GraphOut:
        at = day if day is not None else app.state.present_day
        wanted = set(categories.split(",")) if categories else None

        if centre:
            keys = [
                k for k, p in PREDICATES_BY_KEY.items()
                if p.kind == "rel" and (wanted is None or p.category in wanted)
            ]
            near = world.neighbours(centre, keys, hops=hops, at=at)
            scope = set(near) | {centre}
        else:
            scope = None

        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for fact in world.facts_where(at=at):
            if fact.object_id is None:
                continue
            predicate = PREDICATES_BY_KEY.get(fact.predicate_key)
            if predicate is None or predicate.kind != "rel":
                continue
            if wanted is not None and predicate.category not in wanted:
                continue
            if scope is not None and not (
                fact.subject_id in scope and fact.object_id in scope
            ):
                continue
            for eid in (fact.subject_id, fact.object_id):
                if eid not in nodes:
                    entity = world.get_entity(eid)
                    if entity is None:
                        continue
                    nodes[eid] = {
                        "id": entity.id, "name": entity.name,
                        "type_key": entity.type_key,
                    }
            if fact.subject_id in nodes and fact.object_id in nodes:
                edges.append({
                    "source": fact.subject_id, "target": fact.object_id,
                    "predicate": fact.predicate_key,
                    "label": predicate.label,
                    "category": predicate.category,
                    "strength": fact.strength,
                    "secret": fact.is_secret,
                    "symmetric": predicate.symmetric,
                })
            if len(edges) >= limit:
                break
        return S.GraphOut(nodes=list(nodes.values()), edges=edges)

    # ---- genealogy (§39) --------------------------------------------------

    @app.get("/api/pedigree", response_model=S.PedigreeOut)
    def get_pedigree(
        root_id: str | None = None,
        lens: str = "legal",
        collapsed: str | None = None,
        living_only_on: int | None = None,
        house_id: str | None = None,
    ) -> S.PedigreeOut:
        genealogy = Genealogy(world)
        if root_id is None:
            root_id = _default_pedigree_root(world, genealogy)
        if root_id is None:
            return S.PedigreeOut(root_id=None, width=0, height=0,
                                 people=[], unions=[], links=[])
        result = layout_pedigree(
            genealogy, root_id, lens=lens,
            collapsed=set(collapsed.split(",")) if collapsed else None,
            living_only_on=living_only_on, house_id=house_id,
        )
        return S.PedigreeOut(
            root_id=result.root_id, width=result.width, height=result.height,
            people=[vars(p) for p in result.people],
            unions=[vars(u) for u in result.unions],
            links=[vars(link) for link in result.links],
        )

    @app.get("/api/kin/{entity_id}")
    def get_kin(entity_id: str, hops: int = 3) -> list[dict[str, Any]]:
        """§49: 'who is related to Lady Mara within three generations?'"""
        genealogy = Genealogy(world)
        near = world.neighbours(
            entity_id, ["parent_of", "legal_parent_of", "married_to"], hops=hops)
        out = []
        for other_id, distance in sorted(near.items(), key=lambda kv: kv[1]):
            entity = world.get_entity(other_id)
            if entity is None:
                continue
            out.append({
                "id": entity.id, "name": entity.name, "distance": distance,
                "relationship": genealogy.relationship_between(entity_id, other_id),
            })
        return out

    # ---- succession (§8) --------------------------------------------------

    @app.get("/api/titles")
    def list_titles(at: int | None = None) -> list[dict[str, Any]]:
        day = at if at is not None else app.state.present_day
        out = []
        for title in world.titles():
            holder_id = world.title_holder_on(title.id, day)
            holder = world.get_entity(holder_id) if holder_id else None
            out.append({
                "id": title.id, "name": title.name, "rank": title.rank,
                "succession_law": title.succession_law,
                "territory_id": title.territory_id,
                "holder": ({"id": holder.id, "name": holder.name} if holder else None),
                "holdings": [
                    {"holder_id": h.holder_id,
                     "holder_name": (world.get_entity(h.holder_id).name
                                     if world.get_entity(h.holder_id) else "?"),
                     "from_day": h.from_day, "to_day": h.to_day,
                     "how": h.how, "disputed": h.disputed}
                    for h in world.title_holdings(title.id)
                ],
            })
        return out

    @app.post("/api/titles", status_code=201)
    def create_title(payload: S.TitleIn) -> dict[str, Any]:
        """§8. Make a title, so there is something for anyone to inherit.

        `World.add_title` has existed since the world model did, revision-logged and
        branch-scoped, with no route and no form — so succession worked on the seeded
        example world and on nothing a writer built themselves.
        """
        if payload.succession_law not in LAWS:
            raise HTTPException(
                422, f"unknown succession law {payload.succession_law!r} "
                     f"(the laws are: {', '.join(sorted(LAWS))})")
        for field, value in (("territory_id", payload.territory_id),
                             ("dynasty_root_id", payload.dynasty_root_id),
                             ("entity_id", payload.entity_id)):
            if value and world.get_entity(value) is None:
                raise HTTPException(404, f"no entity {value} for {field}")
        title = world.add_title(
            payload.name, rank=payload.rank, territory_id=payload.territory_id,
            succession_law=payload.succession_law,
            dynasty_root_id=payload.dynasty_root_id, created_on=payload.created_on,
            entity_id=payload.entity_id)
        return {"id": title.id, "name": title.name, "rank": title.rank,
                "succession_law": title.succession_law,
                "territory_id": title.territory_id}

    @app.post("/api/titles/{title_id}/grants", status_code=201)
    def grant_a_title(title_id: str, payload: S.GrantIn) -> dict[str, Any]:
        """Who holds it, and from when. §8's own example is a succession dispute, and
        a dispute needs two grants that overlap — which `disputed` is for."""
        if world.get_title(title_id) is None:
            raise HTTPException(404, f"no title {title_id}")
        if world.get_entity(payload.holder_id) is None:
            raise HTTPException(404, f"no entity {payload.holder_id}")
        if (payload.from_day is not None and payload.to_day is not None
                and payload.to_day < payload.from_day):
            raise HTTPException(422, "a holding cannot end before it begins")
        holding = world.grant_title(
            title_id, payload.holder_id, from_day=payload.from_day,
            to_day=payload.to_day, how=payload.how, disputed=payload.disputed,
            note=payload.note)
        return {"id": holding.id, "title_id": title_id,
                "holder_id": holding.holder_id, "from_day": holding.from_day,
                "to_day": holding.to_day, "how": holding.how,
                "disputed": holding.disputed}

    @app.get("/api/succession/{title_id}", response_model=S.SuccessionOut)
    def get_succession(
        title_id: str,
        day: int | None = None,
        law_key: str | None = None,
        exclude: str | None = None,
        illegitimate: str | None = None,
        assume_dead: str | None = None,
    ) -> S.SuccessionOut:
        """§8 and §50. The hypothetical parameters never write to the world."""
        at = day if day is not None else app.state.present_day
        try:
            result = SuccessionEngine(world).compute(
                title_id, at, law_key=law_key,
                exclude=set(exclude.split(",")) if exclude else None,
                force_illegitimate=(set(illegitimate.split(","))
                                    if illegitimate else None),
                assume_dead=set(assume_dead.split(",")) if assume_dead else None,
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

        return S.SuccessionOut(
            title_id=result.title_id, title_name=result.title_name,
            law_key=result.law.key, law_label=result.law.label, day=result.day,
            hypothetical=result.hypothetical, assumptions=list(result.assumptions),
            line=[
                S.ClaimantOut(position=c.position, id=c.id, name=c.name, note=c.note)
                for c in result.line
            ],
            excluded=[
                {"id": e.person.id, "name": e.person.name, "reason": e.reason}
                for e in result.excluded
            ],
            explanation=result.explain(),
        )

    # ---- events and causality (§31, §32) ----------------------------------

    def _require_entities(*entity_ids: str | None) -> None:
        """404 on any id that names no entity — a ghost reference would otherwise
        surface as an FK failure halfway through the insert, which reads as a crash."""
        for entity_id in entity_ids:
            if entity_id and world.get_entity(entity_id) is None:
                raise HTTPException(404, f"no entity {entity_id}")

    @app.post("/api/scenes", status_code=201)
    def create_scene(payload: S.SceneIn) -> dict[str, Any]:
        _require_entities(payload.location_id, payload.pov_id, *payload.participants)
        if payload.chapter_id and not any(
                c["id"] == payload.chapter_id for c in world.chapters()):
            raise HTTPException(404, f"no chapter {payload.chapter_id}")
        scene = world.add_scene(
            payload.title, chapter_id=payload.chapter_id, position=payload.position,
            day=payload.day, end_day=payload.end_day,
            location_id=payload.location_id, pov_id=payload.pov_id,
            objective=payload.objective, conflict=payload.conflict,
            outcome=payload.outcome, notes=payload.notes,
            participants=payload.participants,
        )
        return {"id": scene.id, "title": scene.title, "day": scene.day}

    @app.get("/api/chapters")
    def list_chapters(work_id: str | None = None) -> list[dict[str, Any]]:
        """The book, so a scene can be put in it rather than only in the world."""
        return world.chapters(work_id)

    @app.get("/api/sources")
    def list_sources() -> list[dict[str, Any]]:
        """Everywhere the writer has cited (§58)."""
        return [{**row, "label_kind": SOURCE_KINDS.get(row["kind"], row["kind"])}
                for row in world.sources()]

    @app.post("/api/sources", status_code=201)
    def create_source(payload: S.SourceIn) -> dict[str, Any]:
        label = payload.label.strip()
        if not label:
            raise HTTPException(422, "a source needs something to call it")
        if payload.kind not in SOURCE_KINDS:
            raise HTTPException(
                422, "a source can be " + _in_words(sorted(SOURCE_KINDS)))
        if payload.scene_id and world.get_scene(payload.scene_id) is None:
            raise HTTPException(404, "there is no such scene to cite")
        source_id = world.add_source(label, kind=payload.kind,
                                     detail=payload.detail,
                                     scene_id=payload.scene_id)
        return {"id": source_id, "label": label, "kind": payload.kind}

    @app.get("/api/works")
    def list_works() -> list[dict[str, Any]]:
        """The manuscripts, each with its chapters and what is written in them (§43).

        The world and the book are the same project seen twice: a writer keeps notes on
        Greenhollow so they can write chapter nine, and the point of the scene table is
        to sit between the two. Returned nested rather than as three flat lists because
        the only useful question is "what is in this book, in order".
        """
        scenes = world.scenes()
        chapters = world.chapters()
        by_chapter: dict[str, list[dict[str, Any]]] = {}
        for scene in scenes:
            if scene.chapter_id:
                by_chapter.setdefault(scene.chapter_id, []).append(
                    {"id": scene.id, "title": scene.title, "day": scene.day,
                     "position": scene.position})
        return [{
            "id": work["id"], "title": work["title"], "kind": work["kind"],
            "position": work["position"], "summary": work["summary"],
            "chapters": [{
                "id": chapter["id"], "title": chapter["title"],
                "position": chapter["position"], "summary": chapter["summary"],
                "scenes": sorted(by_chapter.get(chapter["id"], []),
                                 key=lambda s: (s["position"], s["day"] or 0)),
            } for chapter in chapters if chapter["work_id"] == work["id"]],
            "loose_scenes": sum(1 for s in scenes if not s.chapter_id),
        } for work in world.works()]

    @app.post("/api/works", status_code=201)
    def create_work(payload: S.WorkIn) -> dict[str, Any]:
        title = payload.title.strip()
        if not title:
            raise HTTPException(422, "a book needs a title")
        work_id = world.add_work(title, kind=payload.kind.strip() or "novel",
                                 position=payload.position,
                                 summary=payload.summary)
        return {"id": work_id, "title": title}

    @app.post("/api/chapters", status_code=201)
    def create_chapter(payload: S.ChapterIn) -> dict[str, Any]:
        title = payload.title.strip()
        if not title:
            raise HTTPException(422, "a chapter needs a title")
        if not any(w["id"] == payload.work_id for w in world.works()):
            raise HTTPException(404, "there is no such book to put a chapter in")
        chapter_id = world.add_chapter(payload.work_id, title,
                                       position=payload.position,
                                       summary=payload.summary)
        return {"id": chapter_id, "title": title, "work_id": payload.work_id}

    @app.post("/api/events", status_code=201)
    def create_event(payload: S.EventIn) -> dict[str, Any]:
        _require_entities(payload.location_id,
                          *[p.id for p in payload.participants])
        event = world.add_event(
            payload.name, type_key=payload.type_key, summary=payload.summary,
            start_day=payload.start_day, end_day=payload.end_day,
            location_id=payload.location_id,
            participants=[(p.id, p.role) for p in payload.participants],
        )
        return {"id": event.id, "name": event.name, "start_day": event.start_day}

    @app.post("/api/causal-links", status_code=201)
    def create_causal_link(payload: S.CausalLinkIn) -> JSONResponse:
        """§32: record that one event led to another.

        Linking the same pair twice is answered with 200 rather than an error — the
        writer's intent is already satisfied. Self-links and causal loops are refused
        by the core (400 via the WorldError handler).
        """
        for event_id in (payload.cause_id, payload.effect_id):
            if world.get_event(event_id) is None:
                raise HTTPException(404, f"no event {event_id}")
        created = world.link_cause(payload.cause_id, payload.effect_id,
                                   note=payload.note)
        if not created:
            return JSONResponse(status_code=200,
                                content={"status": "already linked"})
        return JSONResponse(status_code=201, content={"status": "linked"})

    @app.get("/api/events")
    def list_events(first: int | None = None, last: int | None = None) -> list[dict]:
        return [
            {
                "id": e.id, "name": e.name, "type_key": e.type_key,
                "summary": e.summary, "start_day": e.start_day, "end_day": e.end_day,
                "location_id": e.location_id,
                "date_text": (world.calendar.format(e.start_day)
                              if e.start_day is not None else ""),
                "participants": [
                    {"id": p.id, "name": p.name, "role": role}
                    for p, role in world.event_participants(e.id)
                ],
            }
            for e in world.events(first=first, last=last)
        ]

    @app.get("/api/events/{event_id}/consequences")
    def get_consequences(event_id: str) -> list[dict[str, Any]]:
        """§32's consequence explorer."""
        events = {e.id: e for e in world.events()}
        out = []
        for eid, depth in world.consequences_of(event_id):
            event = events.get(eid)
            if event:
                out.append({
                    "id": event.id, "name": event.name, "depth": depth,
                    "start_day": event.start_day, "summary": event.summary,
                })
        return out

    # ---- hierarchy: places and the groups inside them (§2, §12, §54) -------

    def _place_node(node) -> dict[str, Any]:
        return {
            "entity": _entity_out(node.entity).model_dump(),
            "depth": node.depth,
            "settlement_type": node.settlement_type,
            "inside": node.count(),
            "children": [_place_node(c) for c in node.children],
            "groups": [_entity_out(g).model_dump() for g in node.groups],
            "people": [_entity_out(p).model_dump() for p in node.people],
            "other": [_entity_out(o).model_dump() for o in node.other],
        }

    @app.get("/api/places/{place_id}/contents")
    def get_contents(place_id: str, at: int | None = None,
                     max_depth: int = Query(6, le=12)) -> dict[str, Any]:
        """Everything and everyone inside a place, as a tree."""
        day = at if at is not None else app.state.present_day
        hierarchy = Hierarchy(holder.get())
        tree = hierarchy.contents(place_id, at=day, max_depth=max_depth)
        if tree is None:
            raise HTTPException(404, f"no entity {place_id}")
        return {
            "tree": _place_node(tree),
            "within": [_entity_out(e).model_dump()
                       for e in hierarchy.chain_above(place_id, at=day)],
            "groups": [
                {"entity": _entity_out(g).model_dump(), "how": how}
                for g, how in hierarchy.groups_in(place_id, at=day)
            ],
        }

    @app.get("/api/groups")
    def list_groups(at: int | None = None) -> list[dict[str, Any]]:
        """§54: every group of people in the world, with its seat and its size."""
        day = at if at is not None else app.state.present_day
        return [
            {**summary, "entity": _entity_out(summary["entity"]).model_dump()}
            for summary in Hierarchy(holder.get()).summaries(at=day)
        ]

    @app.get("/api/groups/{group_id}")
    def get_group(group_id: str, at: int | None = None) -> dict[str, Any]:
        """One group: who belongs to it, what sits under it, and where it belongs."""
        day = at if at is not None else app.state.present_day
        current = holder.get()
        if current.get_entity(group_id) is None:
            raise HTTPException(404, f"no entity {group_id}")
        hierarchy = Hierarchy(current)
        return {
            "entity": _entity_out(current.get_entity(group_id)).model_dump(),
            "members": [
                {"entity": _entity_out(m.entity).model_dump(),
                 "relation": m.relation, "note": m.note}
                for m in hierarchy.members_of(group_id, at=day)
            ],
            "branches": [
                {"entity": _entity_out(e).model_dump(), "depth": depth}
                for e, depth in hierarchy.branches_of(group_id, at=day)
            ],
            "seats": [
                {"entity": _entity_out(p).model_dump(), "how": how}
                for p, how in hierarchy.seats_of(group_id, at=day)
            ],
            "above": [_entity_out(e).model_dump()
                      for e in hierarchy.chain_above(group_id, at=day)],
            "group_types": list(GROUP_TYPES),
        }

    # ---- secrets and knowledge (§6) ---------------------------------------

    @app.get("/api/secrets")
    def list_secrets(at: int | None = None) -> list[dict[str, Any]]:
        out = []
        for secret in world.secrets():
            states = world.knowledge_of(secret.id, at=at)
            by_stance: dict[str, list] = {}
            for state in states:
                observer = world.get_entity(state.observer_id)
                if observer is None:
                    continue
                about = (world.get_entity(state.about_observer_id)
                         if state.about_observer_id else None)
                by_stance.setdefault(state.stance, []).append({
                    "id": observer.id, "name": observer.name,
                    "about": ({"id": about.id, "name": about.name} if about else None),
                    "acquired_on": state.acquired_on, "note": state.note,
                })
            subject = world.get_entity(secret.about_id) if secret.about_id else None
            out.append({
                "id": secret.id, "name": secret.name, "truth": secret.truth,
                "severity": secret.severity,
                "about": ({"id": subject.id, "name": subject.name} if subject else None),
                "by_stance": by_stance,
            })
        return out

    @app.post("/api/secrets", status_code=201)
    def create_secret(payload: S.SecretIn) -> dict[str, Any]:
        """§6. A secret is a thing that is true and that not everyone has been told.

        The truth lives here once; who thinks what about it lives in the knowledge
        states, which is the distinction the brief insists on — "who knows X" and "who
        believes X" have to be separately answerable, and a boolean cannot do it.
        """
        if payload.severity not in SECRET_SEVERITIES:
            raise HTTPException(
                422, f"unknown severity {payload.severity!r} "
                     f"(they are: {', '.join(SECRET_SEVERITIES)})")
        if payload.about_id and world.get_entity(payload.about_id) is None:
            raise HTTPException(404, f"no entity {payload.about_id}")
        secret = world.add_secret(
            payload.name, truth=payload.truth, about_id=payload.about_id,
            fact_id=payload.fact_id, severity=payload.severity)
        return {"id": secret.id, "name": secret.name, "truth": secret.truth,
                "about_id": secret.about_id, "severity": secret.severity}

    @app.post("/api/knowledge", status_code=201)
    def record_knowledge(payload: S.KnowledgeIn) -> dict[str, Any]:
        """Who thinks what about a secret, and since when.

        `about_observer_id` is the second-order case the brief names: Edric does not
        merely believe the wrong thing, he believes that *Mara* believes it, and a scene
        turns on the difference.
        """
        if payload.stance not in KNOWLEDGE_STANCES:
            raise HTTPException(
                422, f"unknown stance {payload.stance!r} "
                     f"(they are: {', '.join(KNOWLEDGE_STANCES)})")
        if not any(s.id == payload.secret_id for s in world.secrets()):
            raise HTTPException(404, f"no secret {payload.secret_id}")
        for field, value in (("observer_id", payload.observer_id),
                             ("about_observer_id", payload.about_observer_id),
                             ("acquired_from", payload.acquired_from)):
            if value and world.get_entity(value) is None:
                raise HTTPException(404, f"no entity {value} for {field}")
        state = world.set_knowledge(
            payload.observer_id, payload.secret_id, payload.stance,
            about_observer_id=payload.about_observer_id,
            acquired_on=payload.acquired_on, acquired_from=payload.acquired_from,
            scene_id=payload.scene_id, note=payload.note)
        return {"id": state.id, "observer_id": state.observer_id,
                "secret_id": state.secret_id, "stance": state.stance,
                "about_observer_id": state.about_observer_id,
                "acquired_on": state.acquired_on, "note": state.note}

    # ---- asking the world questions (§49) ---------------------------------

    @app.post("/api/query")
    def ask(payload: S.QueryIn) -> dict[str, Any]:
        """Put a question to the world and get the answer, with the working shown.

        The brief calls this one of the application's most important features and the
        module it lives in was zero bytes until now. A question is structured data
        rather than text, so every question the engine can answer is one the form can
        offer and there is no syntax to get wrong.
        """
        try:
            return QY.run(world, QY.Query.from_dict(payload.query)).as_dict()
        except QY.QueryError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/query/vocabulary")
    def query_vocabulary() -> dict[str, Any]:
        """Everything a question can be built out of, for the form to enumerate."""
        return {
            "directions": list(QY.DIRECTIONS),
            "tests": list(QY.TESTS),
            "orders": list(QY.ORDERS),
            "confidence": list(CONFIDENCE_LEVELS),
            "tags": world.all_tags(),
            # `Condition.strength` has been in the query language and the engine all
            # along, and the form could not offer it — so §49's own example and §18's
            # "which regions produce grain *at high level*" were unaskable.
            "strengths": [
                {"key": step["value"], "label": step["label"], "scale": scale.key}
                for scale in SCALES for step in scale.steps
            ],
        }

    @app.get("/api/queries")
    def list_saved_queries() -> list[dict[str, Any]]:
        return [row.as_dict() for row in QY.saved(world)]

    @app.post("/api/queries", status_code=201)
    def save_a_query(payload: S.SaveQueryIn) -> dict[str, Any]:
        """Keep a question, so it can be asked again when the answer has changed."""
        try:
            row = QY.save(world, payload.name, QY.Query.from_dict(payload.query),
                          note=payload.note)
        except QY.QueryError as exc:
            raise HTTPException(422, str(exc)) from exc
        return row.as_dict()

    @app.delete("/api/queries/{key}", status_code=204)
    def forget_a_query(key: str) -> Response:
        QY.forget(world, key)
        return Response(status_code=204)

    # ---- scenes (§44) -----------------------------------------------------

    @app.get("/api/scenes")
    def list_scenes() -> list[dict[str, Any]]:
        return [
            {
                "id": s.id, "title": s.title, "day": s.day,
                "date_text": world.calendar.format(s.day) if s.day is not None else "",
                "location_id": s.location_id,
                "location_name": (world.get_entity(s.location_id).name
                                  if s.location_id and world.get_entity(s.location_id)
                                  else None),
                "pov_id": s.pov_id, "objective": s.objective, "conflict": s.conflict,
                "position": s.position,
            }
            for s in world.scenes()
        ]

    @app.get("/api/scenes/{scene_id}/context", response_model=S.SceneContextOut)
    def get_scene_context(scene_id: str) -> S.SceneContextOut:
        try:
            ctx = SceneContextEngine(world).build(scene_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

        return S.SceneContextOut(
            scene_id=ctx.scene.id,
            title=ctx.scene.title,
            date_text=ctx.date_text,
            location=_entity_out(ctx.location) if ctx.location else None,
            participants=[_entity_out(p) for p in ctx.participants],
            relationships=[
                {
                    "text": item.describe(),
                    "subject": item.subject.name if item.subject else "",
                    "subject_id": item.subject.id if item.subject else "",
                    "object": item.object.name if item.object else "",
                    "object_id": item.object.id if item.object else "",
                    "predicate": item.fact.predicate_key,
                    "strength": item.fact.strength,
                    "secret": item.fact.is_secret,
                    "note": item.fact.note,
                    "score": item.score,
                    "reasons": list(item.reasons),
                }
                for item in ctx.relationships
            ],
            secrets=[
                {
                    "text": line.describe(),
                    "secret_id": line.secret.id,
                    "secret_name": line.secret.name,
                    "observer": line.observer.name,
                    "observer_id": line.observer.id,
                    "stance": line.stance,
                    "about": line.about_observer.name if line.about_observer else None,
                    "note": line.note,
                }
                for line in ctx.secrets
            ],
            goals=[
                {"person": person.name, "person_id": person.id, "kind": kind, "text": text}
                for person, kind, text in ctx.goals
            ],
            recent_events=[
                {"id": e.id, "name": e.name, "days_ago": days, "summary": e.summary}
                for e, days in ctx.recent_events
            ],
            tensions=ctx.tensions,
            world_state_notes=ctx.world_state_notes,
        )

    # ---- continuity (§46, §47) --------------------------------------------

    @app.get("/api/continuity", response_model=S.ContinuityOut)
    def get_continuity(minimum: str = "notice",
                       include_suppressed: bool = False) -> S.ContinuityOut:
        report = ContinuityEngine(world).run(
            minimum=Severity(minimum), include_suppressed=include_suppressed)
        return S.ContinuityOut(
            summary=report.summary(),
            violations=[
                S.ViolationOut(
                    rule_key=v.rule_key, severity=v.severity.value, message=v.message,
                    entity_ids=[i for i in v.entity_ids if i], day=v.day,
                    detail=v.detail, fingerprint=v.fingerprint,
                )
                for v in report.violations
            ],
            suppressed=report.suppressed,
            rules_run=len(report.checked_rules),
        )

    @app.post("/api/continuity/suppress", status_code=204)
    def suppress(payload: S.SuppressIn) -> None:
        """§46: allow intentional exceptions."""
        world.suppress(payload.rule_key, payload.fingerprint, payload.reason)

    # ---- where a place gets what it does not grow (§18, §19, §42) ----------

    @app.get("/api/supply/{place_id}")
    def what_it_needs(place_id: str, day: int | None = None,
                      profile: str = "wagon") -> dict[str, Any]:
        """§19: everything this place says it needs, and where each could come from.

        Traced rather than simulated — §68 and §116 both warn against adding economics
        for its own sake. Nothing here computes a yield from soil and labour; it joins
        who says they produce a thing to who says they need it, and asks the router how
        long the journey takes on the day in question.
        """
        if world.get_entity(place_id) is None:
            raise HTTPException(404, "there is no such place")
        at = day if day is not None else app.state.present_day
        analyst = SupplyAnalyst(world, profile=profile)
        return {
            "place_id": place_id,
            "place_name": _name_of(world, place_id),
            "day": at,
            "profile": profile,
            "needs": [_named_supply(world, row.as_dict())
                      for row in analyst.needs_of(place_id, at)],
            "depended_on_by": [_finding_out(world, f)
                               for f in analyst.who_depends_on(place_id, at)],
            # §86: what a house is worth, counted from what it holds rather than read
            # off an arbitrary `prestige: high` label — which is the thing the brief
            # says to avoid.
            "standing": [_finding_out(world, f)
                         for f in analyst.standing_of(place_id, at)],
        }

    @app.get("/api/supply/{place_id}/{resource_id}")
    def where_one_thing_comes_from(place_id: str, resource_id: str,
                                   day: int | None = None,
                                   profile: str = "wagon") -> dict[str, Any]:
        """The spec's own question: where does Greyhaven get its grain?"""
        _require_entities(place_id, resource_id)
        at = day if day is not None else app.state.present_day
        return _named_supply(world, SupplyAnalyst(
            world, profile=profile).where_it_comes_from(
                place_id, resource_id, at).as_dict())

    # ---- travel (§22) -----------------------------------------------------

    @app.get("/api/travel/places")
    def travel_places() -> list[dict[str, Any]]:
        """Everywhere a journey can start or end.

        Not just settlements: the map draws a crossing to every island it makes, and an
        island is a place a ship puts in at rather than a town. A picker built from
        settlements alone could not offer the one journey the crossing exists for.
        """
        out: list[dict[str, Any]] = []
        for entity_id in Router(world).places():
            entity = world.get_entity(entity_id)
            if entity is None:
                continue
            out.append({"id": entity.id, "name": entity.name,
                        "type_key": entity.type_key})
        return out

    @app.post("/api/segments", status_code=201)
    def create_segment(payload: S.SegmentIn) -> dict[str, Any]:
        """Lay a road, river or crossing the map never drew (§20, §21).

        The travel engine has always been able to route over these; only the generator
        and the seed could make one, so a writer whose story turns on the old ford road
        could ask for a journey along it and be told there was no way through.
        """
        _require_entities(payload.from_entity_id, payload.to_entity_id,
                          payload.entity_id, payload.toll_holder_id)
        if payload.from_entity_id == payload.to_entity_id:
            raise HTTPException(422, "a road has to go somewhere else")
        if payload.length <= 0 or not math.isfinite(payload.length):
            raise HTTPException(422, "a road needs a length greater than nothing")
        if not 0 < payload.quality <= 1:
            raise HTTPException(
                422, "quality runs from just above 0 (a rutted track) to 1 (a paved road)")
        if payload.medium not in SEGMENT_MEDIA:
            raise HTTPException(422, "a way can be " + _in_words(sorted(SEGMENT_MEDIA)))
        if payload.terrain not in SEGMENT_TERRAINS:
            raise HTTPException(422,
                                "the ground can be " + _in_words(sorted(SEGMENT_TERRAINS)))
        # A boat on a plain scores zero against every profile and vanishes from every
        # route without a word — the exact defect the generator shipped for sea lanes.
        wet = payload.medium in ROUTING_SAILED
        if wet != (payload.terrain == "water"):
            raise HTTPException(
                422, f"a {payload.medium} runs over "
                     f"{'water' if wet else 'dry ground'}, so its ground must "
                     f"{'be' if wet else 'not be'} 'water' — otherwise no traveller "
                     f"can use it and nothing will say why")
        try:
            segment = world.add_route_segment(
                payload.from_entity_id, payload.to_entity_id, payload.length,
                medium=payload.medium, quality=payload.quality,
                terrain=payload.terrain, entity_id=payload.entity_id,
                built_on=payload.built_on, ruined_on=payload.ruined_on,
                closed_seasons=payload.closed_seasons, danger=payload.danger,
                toll_holder_id=payload.toll_holder_id)
        except WorldError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"id": segment.id, "from_entity_id": segment.from_entity_id,
                "to_entity_id": segment.to_entity_id, "medium": segment.medium,
                "length": segment.length}

    @app.delete("/api/segments/{segment_id}", status_code=204)
    def erase_segment(segment_id: str) -> Response:
        try:
            world.delete_route_segment(segment_id)
        except WorldError as exc:
            raise HTTPException(404, str(exc)) from exc
        return Response(status_code=204)

    @app.get("/api/route", response_model=S.RouteOut)
    def get_route(
        origin_id: str,
        destination_id: str,
        profile: str = "horse",
        day: int | None = None,
        party_size: int | None = None,
    ) -> S.RouteOut:
        try:
            route = Router(world).route(
                origin_id, destination_id, profile=profile, day=day,
                party_size=party_size)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if route is None:
            raise HTTPException(
                404,
                "No route under these conditions. A road may not have been built yet, "
                "or a pass or river may be closed for the season.",
            )
        return S.RouteOut(
            origin_id=route.origin_id, destination_id=route.destination_id,
            profile=route.profile.key, days=round(route.days, 2),
            distance=route.distance, path=route.path,
            path_names=[
                world.get_entity(i).name if world.get_entity(i) else i
                for i in route.path
            ],
            legs=[vars(leg) for leg in route.legs],
            explanation=route.explain(world),
        )

    # ---- analysis (§51, §52, §85) -----------------------------------------

    @app.get("/api/why/{entity_id}")
    def get_why(entity_id: str, day: int | None = None) -> dict[str, Any]:
        """§51: 'Why does this matter?'"""
        at = day if day is not None else app.state.present_day
        return DependencyAnalyst(world).why_it_matters(entity_id, at)

    @app.get("/api/impact/{entity_id}")
    def get_impact(entity_id: str, day: int | None = None) -> dict[str, Any]:
        """§52/§85: 'What changes if this disappears?'"""
        at = day if day is not None else app.state.present_day
        return DependencyAnalyst(world).what_if_removed(entity_id, at)

    # ---- static client ----------------------------------------------------

    if (WEB_DIST / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            """Serve the client, letting it own its own routing."""
            candidate = WEB_DIST / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(WEB_DIST / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def not_built() -> HTMLResponse:
            """Say what to do rather than returning a bare 404.

            The API is fully usable without the client, so this is a missing front end
            rather than a broken install — the page should say so.
            """
            return HTMLResponse(
                "<!doctype html><meta charset='utf-8'>"
                "<title>FW — the client has not been built</title>"
                "<style>body{font:16px/1.6 system-ui;margin:8vh auto;max-width:34rem;"
                "padding:0 1.5rem;color:#16181d}code{background:#efece6;padding:2px 6px;"
                "border-radius:4px}</style>"
                "<h1>The world is loaded; the client is not built.</h1>"
                "<p>The API is running and complete — try "
                "<a href='/api/world'>/api/world</a> or "
                "<a href='/docs'>/docs</a>.</p>"
                "<p>To build the browser client:</p>"
                "<pre><code>cd web &amp;&amp; npm install &amp;&amp; npm run build</code></pre>"
                "<p>Then restart <code>fw serve</code>. "
                "Everything is also available from the command line — try "
                "<code>fw scene</code> or <code>fw succession</code>.</p>",
            )

    return app


# ---------------------------------------------------------------- helpers

# How long a switched-away world stays open before its connection is closed. Routes run
# in a threadpool, so a request that resolved the old world through the proxy can still
# be mid-query when the switch lands; closing immediately would fail it with "cannot
# operate on a closed database". Requests finish in milliseconds — this is a wide margin,
# not a schedule.
RETIRE_AFTER_SECONDS = 10.0


def _retire(world: World) -> None:
    import threading

    timer = threading.Timer(RETIRE_AFTER_SECONDS, world.close)
    timer.daemon = True     # never keep the process alive for a courtesy close
    timer.start()


def _entity_out(entity) -> S.EntityOut:
    return S.EntityOut(
        id=entity.id, type_key=entity.type_key, name=entity.name,
        summary=entity.summary, exists_from=entity.exists_from,
        exists_to=entity.exists_to, confidence=entity.confidence,
        tags=list(entity.tags),
    )


# The surface the last proposed map was drawn from, kept for the apply that follows it.
#
# A plan crosses to the client and back as JSON, and its terrain — around three quarters
# of a megabyte of floats, which the client never looks at — is deliberately not on that
# wire. But `apply_plan` stores that surface as the ground the accepted map stands on, so
# a plan returning from a browser used to arrive with `terrain=None` and write a map with
# nothing underneath it: every relief the *application* accepted was flat, and only worlds
# built from Python ever had lit mountains. The seeded demo hid it, because its ground is
# written by the seed script.
#
# One slot, because the shape of the operation is propose-then-accept and the accept
# follows its own proposal. A miss is not a wrong answer, only a slower one.
_LAST_GROUND: tuple[tuple[str, str, str], Any] | None = None


def _remember_the_ground(world: World, plan) -> None:
    global _LAST_GROUND
    if plan.terrain is not None and plan.terrain.fields:
        _LAST_GROUND = ((world.project_id, world.branch_id, plan.plan_id), plan.terrain)


def _with_the_ground(world: World, plan):
    """The plan, with the heightfield the wire could not carry put back on it.

    A plan is a pure function of the world and the brief, so the surface can always be
    had again: the one this server computed is kept, and on a miss it is worked out
    afresh and used only if it proves to be the same plan.
    """
    if plan.terrain is not None and plan.terrain.fields:
        return plan
    want = (world.project_id, world.branch_id, plan.plan_id)
    if _LAST_GROUND is not None and _LAST_GROUND[0] == want:
        return replace(plan, terrain=_LAST_GROUND[1])

    from fw.core.mapgen.pipeline import plan_map

    again = plan_map(world, plan.brief)
    if again.plan_id != plan.plan_id or again.terrain is None:
        # The world moved under the plan. Its own surface is gone and this one belongs
        # to a different map, so the plan is written as the writer saw it and whatever
        # ground is already stored is left alone — better a map over the old mountains
        # than one over mountains that were never proposed.
        return plan
    _remember_the_ground(world, again)
    return replace(plan, terrain=again.terrain)


# What a way between two places can be, and what it runs over (§20, §21). Mirrors
# `routing.LAND`/`WATER`, which is where a terrain has to be known for anything to move
# along it — a segment over ground no profile scores is a road nobody can use.
def _checked_key(raw: str) -> str:
    """The key a writer typed, or a sentence about why it cannot be one.

    Keys are what facts are stored against and what a saved question refers to, so they
    are the one part of a custom type that cannot be renamed later without rewriting
    every row that used it. Kept to the shape the built-in ones already have.
    """
    key = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if not key:
        raise HTTPException(422, "it needs a key — a short word to store it under")
    if not key.replace("_", "").isalnum() or not key[0].isalpha():
        raise HTTPException(
            422, f"{raw!r} cannot be a key: use letters, digits and underscores, "
                 f"starting with a letter — like 'star_system' or 'orbits'")
    if len(key) > 64:
        raise HTTPException(422, "that key is too long to live on every fact")
    return key


# The wet media come from the router itself rather than being spelled again here: it
# decides whether a segment is sailed or walked by testing exactly that set, so a word
# offered here that it did not know (a ferry, say) would be routed over dry ground,
# score zero against water, and drop out of every journey without an error anywhere.
SEGMENT_MEDIA = ("road", "track", "pass") + ROUTING_SAILED
SEGMENT_TERRAINS = tuple(ROUTING_LAND)      # already carries "water"


# What a source can be (§58, which names these five). Kept here rather than in the core
# because it is a vocabulary the API offers a form, not a rule the world model enforces —
# `source.kind` is a free TEXT column and an imported world may carry anything.
SOURCE_KINDS = {
    "author_note": "an author's note",
    "chapter": "a chapter",
    "manuscript_scene": "a scene in the manuscript",
    "historical_document": "a document from inside the world",
    "timeline_event": "something on the timeline",
}


def _checked_source(world: World, source_id: str | None) -> str | None:
    """The cited source, or a 404 saying there is no such thing.

    `fact.source_id` is ON DELETE SET NULL rather than a hard constraint, so a bad id
    would be written and then silently render as no citation at all — a fact the writer
    believes they sourced and which says nothing.
    """
    if source_id and world.get_source(source_id) is None:
        raise HTTPException(404, "there is no such source to cite")
    return source_id


def _seen_by(world: World, observer_id: str | None, day: int) -> Perspective:
    """The reading for `?as=`, or the view from nowhere when it is absent (§94).

    A perspective that named nobody real would silently render the objective world under
    a banner claiming otherwise, which is worse than an error: the writer would believe
    they were looking at House Marr's map.
    """
    if observer_id and world.get_entity(observer_id) is None:
        raise HTTPException(404, "there is nobody by that name to see the world as")
    return Perspective(world, observer_id, day)


def _finding_out(world: World, finding) -> dict[str, Any]:
    """A derived finding on the wire, with its evidence and the things it names.

    The names are resolved here rather than in the client because an id is not something
    a writer can read, and a row of identical "look at it" links is worse than no links.
    """
    return {
        "text": finding.text, "weight": finding.weight, "kind": finding.kind,
        "evidence": list(finding.evidence),
        "entity_ids": list(finding.entity_ids),
        "entity_names": [_name_of(world, e) for e in finding.entity_ids],
    }


def _named_supply(world: World, supply: dict[str, Any]) -> dict[str, Any]:
    """The same name resolution, for the findings a supply trace carries."""
    return {**supply,
            "findings": [{**f, "entity_names": [_name_of(world, e)
                                                for e in f["entity_ids"]]}
                         for f in supply["findings"]]}


def _checked_fact(world: World, fact_id: str | None) -> str | None:
    """The fact this one is about, or a 404 (§33).

    `about_fact_id` is ON DELETE CASCADE, so a bad id would be written and the row would
    then be invisible to the reification closure — a disagreement recorded against
    nothing, which reads to the writer as a note the software lost.
    """
    if fact_id and world.get_fact(fact_id) is None:
        raise HTTPException(404, "there is no such fact to be about")
    return fact_id


def _name_of(world: World, entity_id: str | None) -> str:
    if not entity_id:
        return ""
    found = world.get_entity(entity_id)
    return found.name if found else ""


def _event_name(world: World, event_id: str | None) -> str:
    if not event_id:
        return ""
    found = world.get_event(event_id)
    return found.name if found else ""


def _in_words(items) -> str:
    """"a, b or c" — an error a writer can act on rather than a set literal."""
    items = list(items)
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + " or " + items[-1]


# The layers the client can draw on and knows how to paint. A shape on a layer nobody
# renders is a shape the writer cannot see, which reads to them as a lost drawing.
MAP_LAYERS = MG_DRAFTS.LAYERS

# How many vertices one drawn shape may carry. Generous — a hand-drawn coastline is a
# few hundred — and finite, because the R*Tree box is computed over every one of them
# and a runaway client should not be able to make the file unopenable.
MOST_VERTICES = 4000


def _checked_shape(kind: str, coordinates: Any) -> Any:
    """The writer's shape, or a sentence saying what is wrong with it.

    Every number is checked for being finite as well as numeric. A NaN reaches the
    R*Tree index, which stores it happily and then answers every bounding-box query
    wrongly forever — a corruption with no error message anywhere near its cause.
    """
    if kind == "point":
        return list(_points(coordinates, want=1, what="A point", kind=kind))[0]
    if kind == "line":
        return [list(p) for p in _points(coordinates, want=2, what="A line", kind=kind)]
    if kind == "polygon":
        rings = coordinates if isinstance(coordinates, list) else []
        if not rings or not all(isinstance(r, list) for r in rings):
            raise HTTPException(422, "A polygon is a list of rings, and this has none")
        out = []
        for n, ring in enumerate(rings):
            points = list(_points(ring, want=3, kind=kind,
                                  what=("The outline" if n == 0 else f"Hole {n}")))
            # Closed by the server rather than demanded of the client: a writer clicking
            # the last corner has finished the shape, and asking them to click the first
            # one again is asking them to know how polygons are stored.
            if points[0] != points[-1]:
                points.append(points[0])
            out.append([list(p) for p in points])
        return out
    raise HTTPException(422, f"A shape is a point, a line or a polygon, not {kind!r}")


def _points(raw: Any, *, want: int, what: str, kind: str):
    if not isinstance(raw, list) or len(raw) < want:
        raise HTTPException(
            422, f"{what} needs at least {want} point{'s' if want > 1 else ''}, "
                 f"and this has {len(raw) if isinstance(raw, list) else 0}")
    if kind == "point" and raw and isinstance(raw[0], (int, float)):
        raw = [raw]                      # a bare [x, y] is one point, not two numbers
    if len(raw) > MOST_VERTICES:
        raise HTTPException(422, f"{what} has {len(raw)} points, and {MOST_VERTICES} "
                                 "is as many as one shape may carry")
    for point in raw:
        if (not isinstance(point, (list, tuple)) or len(point) != 2
                or not all(isinstance(v, (int, float))
                           and not isinstance(v, bool)
                           and math.isfinite(v) for v in point)):
            raise HTTPException(
                422, f"{what} has a corner that is not two ordinary numbers: {point!r}")
        yield (float(point[0]), float(point[1]))


def _fact_out(world: World, fact) -> S.FactOut:
    subject = world.get_entity(fact.subject_id)
    target = world.get_entity(fact.object_id) if fact.object_id else None
    predicate = PREDICATES_BY_KEY.get(fact.predicate_key)
    return S.FactOut(
        id=fact.id, subject_id=fact.subject_id,
        subject_name=subject.name if subject else "",
        predicate_key=fact.predicate_key,
        predicate_label=predicate.label if predicate else fact.predicate_key,
        object_id=fact.object_id, object_name=target.name if target else None,
        value=fact.value, valid_from=fact.valid_from, valid_to=fact.valid_to,
        confidence=fact.confidence, secrecy=fact.secrecy, strength=fact.strength,
        note=fact.note, is_secret=fact.is_secret,
        valid_from_text=(world.calendar.format(fact.valid_from)
                         if fact.valid_from is not None else ""),
        valid_to_text=(world.calendar.format(fact.valid_to)
                       if fact.valid_to is not None else ""),
        source=_source_label(world, fact.source_id),
        about_fact_id=fact.about_fact_id,
    )


def _source_label(world: World, source_id: str | None) -> str:
    if not source_id:
        return ""
    row = world.get_source(source_id)
    return str(row.get("label") or "") if row else ""


def _date_out(world: World, day: int) -> S.DateOut:
    calendar = world.calendar
    civil = calendar.from_index(day)
    era = calendar.era(civil.year)
    return S.DateOut(
        day=day, text=calendar.format(day), year=civil.year, month=civil.month,
        month_name=calendar.month_name(civil.month), day_of_month=civil.day,
        weekday=calendar.weekday(day), season=calendar.season(day),
        era=era.abbreviation if era else None,
        era_name=era.name if era else None,
        era_year=era.year_of(civil.year) if era else None,
    )


def _calendar_out(world: World) -> S.CalendarOut:
    calendar = world.calendar
    return S.CalendarOut(
        name=calendar.name,
        months=[{"name": m.name, "days": m.days} for m in calendar.months],
        weekdays=list(calendar.weekdays),
        days_in_year=calendar.common_year_days,
        eras=[{"name": e.name, "abbreviation": e.abbreviation,
               "start_year": e.start_year, "end_year": e.end_year,
               "counts_backward": e.counts_backward,
               "reckons_from": e.reckons_from}
              for e in calendar.eras],
        seasons=[{"name": s.name, "start": s.start_day_of_year}
                 for s in calendar.seasons],
    )


def _world_span(world: World) -> dict[str, int]:
    """The range the timeline slider should cover: everything the world talks about."""
    bounds = world.span()
    calendar = world.calendar
    lo = bounds["lo"] if bounds["lo"] is not None else 0
    hi = bounds["hi"] if bounds["hi"] is not None else calendar.date(2, 1, 1)
    if hi <= lo:
        hi = lo + calendar.common_year_days
    # A little air either side, so the ends of history are not flush with the slider.
    margin = calendar.common_year_days * 2
    return {"first": lo - margin, "last": hi + margin}


def _guess_present_day(world: World) -> int:
    """Default the timeline to where the story is: the latest scene, else the last event."""
    scenes = [s.day for s in world.scenes() if s.day is not None]
    if scenes:
        return max(scenes)
    events = [e.start_day for e in world.events() if e.start_day is not None]
    if events:
        return max(events)
    return world.calendar.date(1, 1, 1)


def _default_pedigree_root(world: World, genealogy: Genealogy) -> str | None:
    """The forebear with the most descendants — the trunk of the largest tree."""
    best, best_size = None, -1
    for person_id in genealogy.people:
        if genealogy.parents_of(person_id):
            continue
        size = len(genealogy.descendants_of(person_id))
        if size > best_size:
            best, best_size = person_id, size
    return best
