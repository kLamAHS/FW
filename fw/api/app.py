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

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fw.api import schemas as S
from fw.core.calendar.kernel import CalendarError
from fw.core.continuity.engine import ContinuityEngine, Severity
from fw.core.derive.dependency import DependencyAnalyst
from fw.core.derive.hierarchy import GROUP_TYPES, Hierarchy
from fw.core.derive.scene_context import SceneContextEngine
from fw.core.genealogy.kinship import Genealogy
from fw.core.genealogy.layout import layout_pedigree
from fw.core.geo.routing import PROFILES, Router
from fw.core.library import Library, LibraryError
from fw.core.mapgen import cartography, ledger
from fw.core.mapgen import plan as MG
from fw.core.mapgen.generate import generate_map
from fw.core.model.vocabulary import PREDICATES_BY_KEY
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
        }

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

    # ---- entities ---------------------------------------------------------

    @app.get("/api/entities", response_model=list[S.EntityOut])
    def list_entities(
        type_key: str | None = None,
        at: int | None = None,
        limit: int = Query(500, le=5000),
    ) -> list[S.EntityOut]:
        entities = world.entities(type_key, limit=limit)
        if at is not None:
            entities = [e for e in entities if e.exists_on(at)]
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
    def get_state(day: int, include_secret: bool = True) -> S.StateOut:
        state = world.state_at(day, include_secret=include_secret)
        return S.StateOut(
            day=day,
            date=_date_out(world, day),
            entities=[_entity_out(e) for e in state.entities.values()],
            facts=[_fact_out(world, f) for f in state.facts],
            titles=state.titles,
        )

    @app.get("/api/map", response_model=S.MapOut)
    def get_map(day: int | None = None, layer: str | None = None,
                mode: str = "legally_owns", labels: bool = True) -> S.MapOut:
        """§34/§35/§36: geometry for a date, with the control facts attached.

        Each feature carries who owns, administers, occupies, taxes and claims it on that
        day, so §11's distinction is visible on the map rather than only on entity pages.
        """
        at = day if day is not None else app.state.present_day
        features = []
        for geometry in world.geometries(at=at, layer=layer):
            entity = world.get_entity(geometry.entity_id)
            if entity is None or not entity.exists_on(at):
                continue
            control = {}
            for fact in world.facts_where(object_id=entity.id, at=at):
                if fact.predicate_key in ("legally_owns", "administers", "occupies",
                                          "taxes", "claims", "rules"):
                    whose = world.get_entity(fact.subject_id)
                    if whose:
                        control.setdefault(fact.predicate_key, []).append({
                            "id": whose.id, "name": whose.name,
                        })
            features.append({
                "id": geometry.id,
                "entity_id": entity.id,
                "name": entity.name,
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
                        draw=drawn.as_dict())

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
            at=app.state.present_day,
            include=tuple(payload.include) if payload.include else MG.MapBrief().include,
            invent_settlements=payload.invent_settlements,
            north=payload.north,
            prevailing_wind=payload.prevailing_wind,
        )
        current = holder.get()
        proposal = plan_map(current, brief)
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

        plan = MG.MapPlan.from_dict(payload.plan)
        answers = DecisionSet(plan_id=plan.plan_id, decisions=tuple(
            Decision(feature_id=d.feature_id, accept=d.accept, name=d.name,
                     pinned=d.pinned)
            for d in payload.decisions))
        try:
            report = apply_plan(holder.get(), plan, answers)
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
        scene = world.add_scene(
            payload.title, day=payload.day, end_day=payload.end_day,
            location_id=payload.location_id, pov_id=payload.pov_id,
            objective=payload.objective, conflict=payload.conflict,
            outcome=payload.outcome, notes=payload.notes,
            participants=payload.participants,
        )
        return {"id": scene.id, "title": scene.title, "day": scene.day}

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

    # ---- travel (§22) -----------------------------------------------------

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
    )


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
