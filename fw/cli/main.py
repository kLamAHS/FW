"""The `fw` command.

Everything the application can do is reachable without a browser. That is partly a
convenience and partly a design guarantee: if a feature can only be exercised through the
UI, it is not really in the core, and the layering the whole architecture rests on has
quietly stopped being true.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fw.core.continuity.engine import ContinuityEngine, Severity
from fw.core.derive.dependency import DependencyAnalyst
from fw.core.derive.scene_context import SceneContextEngine
from fw.core.genealogy.kinship import Genealogy
from fw.core.geo.routing import PROFILES, Router
from fw.core.seed.renn import PRESENT_YEAR, seed_renn
from fw.core.succession.engine import SuccessionEngine
from fw.core.world import World, WorldError

DEFAULT_PATH = "world.fwworld"


def _open(path: str) -> World:
    if not Path(path).exists():
        sys.exit(f"No world at {path}. Create one with:  fw new {path}")
    return World.open(path)


def _resolve(world: World, text: str) -> str:
    """Accept an id or a name, so the CLI is usable without copying identifiers."""
    if world.get_entity(text) is not None:
        return text
    entity = world.entity_named(text)
    if entity is not None:
        return entity.id
    matches = world.search(text, limit=5)
    if len(matches) == 1:
        return matches[0].id
    if not matches:
        sys.exit(f"Nothing in this world matches {text!r}.")
    sys.exit(f"{text!r} is ambiguous: " + ", ".join(m.name for m in matches))


# ---------------------------------------------------------------- commands

def cmd_new(args) -> None:
    if Path(args.path).exists() and not args.force:
        sys.exit(f"{args.path} already exists. Pass --force to overwrite.")
    if Path(args.path).exists():
        Path(args.path).unlink()
    world = World.create(args.path, name=args.name)
    print(f"Created {args.path} — “{world.name}”")
    world.close()


def cmd_seed(args) -> None:
    """Write the §115 example world to a file."""
    path = Path(args.path)
    if path.exists() and not args.force:
        sys.exit(f"{path} already exists. Pass --force to overwrite.")
    if path.exists():
        path.unlink()
    world = seed_renn(str(path))
    print(f"Seeded {path} — “{world.name}”")
    print(f"  {world.count_entities()} entities, "
          f"{world.db.scalar('SELECT count(*) FROM fact')} facts, "
          f"{len(world.events())} events, {len(world.scenes())} scenes")
    print(f"  Start the app with:  fw serve {path}")
    world.close()


def cmd_info(args) -> None:
    world = _open(args.path)
    print(f"{world.name}")
    print(f"  calendar: {world.calendar.name} — "
          f"{len(world.calendar.months)} months, "
          f"{world.calendar.common_year_days} days a year")
    rows = world.db.query(
        "SELECT type_key, count(*) AS n FROM entity WHERE branch_id = ? "
        "GROUP BY type_key ORDER BY n DESC", (world.branch_id,))
    print(f"  {world.count_entities()} entities:")
    for row in rows:
        print(f"    {row['n']:4}  {row['type_key']}")
    print(f"  {world.db.scalar('SELECT count(*) FROM fact')} facts, "
          f"{len(world.events())} events, {len(world.secrets())} secrets, "
          f"{len(world.scenes())} scenes")
    world.close()


def cmd_search(args) -> None:
    world = _open(args.path)
    results = world.search(args.query, limit=args.limit)
    if not results:
        print("Nothing found.")
    for entity in results:
        print(f"  {entity.name}  ({entity.type_key})")
        if entity.summary:
            print(f"      {entity.summary}")
    world.close()


def cmd_state(args) -> None:
    """§3: what was true on a given day."""
    world = _open(args.path)
    day = world.day(args.year, args.month, args.day)
    state = world.state_at(day)
    print(f"{world.calendar.format(day, with_weekday=True)}")
    print(f"  {len(state.entities)} entities exist, {len(state.facts)} facts hold")

    control = [f for f in state.facts
               if f.predicate_key in ("legally_owns", "administers", "occupies", "taxes")]
    if control:
        print("\n  Territorial control")
        for fact in sorted(control, key=lambda f: f.predicate_key):
            holder = state.entities.get(fact.subject_id)
            target = state.entities.get(fact.object_id)
            if holder and target:
                print(f"    {target.name}: {fact.predicate_key.replace('_',' ')} "
                      f"by {holder.name}")

    living = [e for e in state.living()]
    if living:
        print(f"\n  Living characters ({len(living)})")
        for person in sorted(living, key=lambda e: e.name):
            print(f"    {person.name}")
    world.close()


def cmd_succession(args) -> None:
    world = _open(args.path)
    title = world.title_named(args.title) if args.title else None
    if title is None and args.title:
        sys.exit(f"No title named {args.title!r}. "
                 f"Known: {', '.join(t.name for t in world.titles())}")
    titles = [title] if title else world.titles()
    day = world.day(args.year, args.month, args.day) if args.year else None

    engine = SuccessionEngine(world)
    for t in titles:
        at = day if day is not None else world.day(PRESENT_YEAR)
        result = engine.compute(
            t.id, at, law_key=args.law,
            force_illegitimate=({_resolve(world, args.illegitimate)}
                                if args.illegitimate else None),
            assume_dead=({_resolve(world, args.dead)} if args.dead else None),
        )
        print(result.explain())
        print()
    world.close()


def cmd_check(args) -> None:
    """§46: run the continuity rules."""
    world = _open(args.path)
    report = ContinuityEngine(world).run(minimum=Severity(args.minimum))
    print(report.summary())
    for violation in report.violations:
        print(f"  [{violation.severity.value:7}] {violation.message}")
        if violation.detail:
            print(f"            {violation.detail}")
        if args.show_fingerprints:
            print(f"            suppress with: fw suppress {args.path} "
                  f"{violation.rule_key} {violation.fingerprint}")
    world.close()
    sys.exit(1 if report.errors and args.strict else 0)


def cmd_suppress(args) -> None:
    world = _open(args.path)
    world.suppress(args.rule_key, args.fingerprint, args.reason)
    print(f"Suppressed {args.rule_key}/{args.fingerprint}")
    world.close()


def cmd_route(args) -> None:
    """§22: how long does it take to get there?"""
    world = _open(args.path)
    origin = _resolve(world, args.origin)
    destination = _resolve(world, args.destination)
    day = world.day(args.year) if args.year else None
    router = Router(world)

    profiles = [args.profile] if args.profile else list(PROFILES)
    for key in profiles:
        route = router.route(origin, destination, profile=key, day=day,
                             party_size=args.party_size)
        if route is None:
            print(f"  {PROFILES[key].label:22} no route under these conditions")
        else:
            print(f"  {PROFILES[key].label:22} {route.days:6.1f} days   "
                  + " → ".join(world.get_entity(i).name for i in route.path))
    world.close()


def cmd_scene(args) -> None:
    """§44: everything relevant to a scene."""
    world = _open(args.path)
    scenes = world.scenes()
    if args.scene:
        matches = [s for s in scenes if args.scene.lower() in s.title.lower()]
        if not matches:
            sys.exit(f"No scene matching {args.scene!r}. "
                     f"Known: {', '.join(s.title for s in scenes)}")
        scenes = matches[:1]
    if not scenes:
        sys.exit("This world has no scenes yet.")

    engine = SceneContextEngine(world)
    for scene in scenes:
        print(engine.build(scene.id).render())
        print()
    world.close()


def cmd_why(args) -> None:
    """§51: why does this matter?"""
    world = _open(args.path)
    entity_id = _resolve(world, args.entity)
    day = world.day(args.year) if args.year else world.day(PRESENT_YEAR)
    result = DependencyAnalyst(world).why_it_matters(entity_id, day)
    print(f"Why {result['entity']['name']} matters:")
    for finding in result["findings"]:
        marker = "·" if finding["kind"] == "authored" else "→"
        print(f"  {marker} {finding['text']}")
        for line in finding["evidence"]:
            print(f"      {line}")
    print(f"\n  {result['note']}")
    world.close()


def cmd_impact(args) -> None:
    """§52/§85: what changes if this disappears?"""
    world = _open(args.path)
    entity_id = _resolve(world, args.entity)
    day = world.day(args.year) if args.year else world.day(PRESENT_YEAR)
    result = DependencyAnalyst(world).what_if_removed(entity_id, day)
    print(f"If {result['entity']['name']} were removed:")
    if not result["consequences"]:
        print("  Nothing in the model depends on it.")
    for finding in result["consequences"]:
        print(f"  → {finding['text']}")
        for line in finding["evidence"]:
            print(f"      {line}")
    print(f"\n  {result['note']}")
    world.close()


def cmd_kin(args) -> None:
    world = _open(args.path)
    entity_id = _resolve(world, args.entity)
    genealogy = Genealogy(world)
    near = world.neighbours(entity_id, ["parent_of", "legal_parent_of", "married_to"],
                            hops=args.hops)
    print(f"Kin of {world.get_entity(entity_id).name} within {args.hops} steps:")
    for other_id, distance in sorted(near.items(), key=lambda kv: kv[1]):
        other = world.get_entity(other_id)
        label = genealogy.relationship_between(entity_id, other_id) or "kin"
        print(f"  {distance}  {other.name} — {label}")
    world.close()


def cmd_export(args) -> None:
    """§62: JSON export of the whole world."""
    world = _open(args.path)
    payload = {
        "world": {"name": world.name},
        "calendar": {
            "name": world.calendar.name,
            "months": [{"name": m.name, "days": m.days} for m in world.calendar.months],
            "weekdays": list(world.calendar.weekdays),
        },
        "entities": [
            {"id": e.id, "type": e.type_key, "name": e.name, "summary": e.summary,
             "exists_from": e.exists_from, "exists_to": e.exists_to,
             "tags": list(e.tags)}
            for e in world.entities()
        ],
        "facts": [
            {"subject": f.subject_id, "predicate": f.predicate_key,
             "object": f.object_id, "value": f.value,
             "from": f.valid_from, "to": f.valid_to,
             "confidence": f.confidence, "secrecy": f.secrecy,
             "strength": f.strength, "note": f.note}
            for f in world.facts_where()
        ],
        "events": [
            {"id": e.id, "name": e.name, "type": e.type_key, "start": e.start_day,
             "end": e.end_day, "summary": e.summary}
            for e in world.events()
        ],
    }
    text = json.dumps(payload, indent=2)
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out} ({len(text)} bytes)")
    else:
        print(text)
    world.close()


def cmd_restore(args) -> None:
    """§59: undo a recorded change from the command line."""
    # Two optional positionals means `fw restore 123` parses 123 as the *path* and
    # then tries to open a world file named "123". A bare number in the path slot is
    # the revision id the writer obviously meant — unless --list expects no revision,
    # or a world file by that numeric name actually exists, where a path is a path.
    if (args.revision is None and not args.list
            and args.path != DEFAULT_PATH and args.path.isdigit()
            and not Path(args.path).exists()):
        args.revision = int(args.path)
        args.path = DEFAULT_PATH
    world = _open(args.path)
    try:
        if args.list:
            deleted = world.recently_deleted(limit=20)
            if not deleted:
                print("Nothing deleted that can be restored.")
            for d in deleted:
                print(f"  {d['revision_id']:6}  {d['name']}  ({d['type_key']}, {d['at']})")
            return
        if args.revision is None:
            sys.exit("Give a revision id, or --list to see what can be restored.")
        try:
            print(world.restore(args.revision))
        except WorldError as exc:
            # A refusal ("already exists", "restore that entity first") is an answer,
            # not a crash — no traceback.
            sys.exit(str(exc))
    finally:
        world.close()


def _library_dir(args, world) -> Path:
    """Where the Worlds screen looks for saves.

    An explicitly chosen library always wins. Otherwise, serving a specific world file
    makes that file's own directory the library — so the world being served appears in
    the listing and switching away is never a one-way door — and the launcher with no
    world at all uses ./worlds.
    """
    if args.library is not None:
        return Path(args.library)
    if world is not None:
        return Path(str(world.db.path)).resolve().parent
    return Path("worlds")


def cmd_serve(args) -> None:
    import uvicorn

    from fw.api.app import create_app
    from fw.core.library import Library

    # A named path is opened directly. With no path (and no world at the default
    # name), the app starts on the launcher instead: the writer picks a save or makes
    # a new world in the browser, rather than being forced into any template.
    world = None
    if args.path != DEFAULT_PATH or Path(args.path).exists():
        world = _open(args.path)
    library = Library(_library_dir(args, world))

    app = create_app(world, library=library)
    url = f"http://{args.host}:{args.port}"
    if world is not None:
        print(f"“{world.name}” is at {url}")
    else:
        print(f"FW is at {url} — create or open a world from there.")
        print(f"  Saves live in {library.directory.resolve()}, one portable file each.")
    print("  Nothing leaves this machine; the server is bound to localhost.")

    if args.open_browser:
        import threading
        import webbrowser

        # uvicorn.run blocks; give it a moment to bind before the browser knocks.
        threading.Timer(1.2, webbrowser.open, [url]).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


# ---------------------------------------------------------------- wiring

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fw",
        description="A worldbuilding application for fiction writers.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, handler, help_text, *, needs_path=True):
        p = sub.add_parser(name, help=help_text, description=help_text)
        if needs_path:
            p.add_argument("path", nargs="?", default=DEFAULT_PATH,
                           help=f"world file (default: {DEFAULT_PATH})")
        p.set_defaults(func=handler)
        return p

    p = add("new", cmd_new, "Create an empty world.")
    p.add_argument("--name", default="Untitled world")
    p.add_argument("--force", action="store_true")

    p = add("seed", cmd_seed, "Create the Kingdom of Renn example world.")
    p.add_argument("--force", action="store_true")

    add("info", cmd_info, "Summarise a world.")

    p = add("search", cmd_search, "Search across everything.")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=20)

    p = add("state", cmd_state, "Show what was true on a given date.")
    p.add_argument("year", type=int)
    p.add_argument("month", type=int, nargs="?", default=1)
    p.add_argument("day", type=int, nargs="?", default=1)

    p = add("succession", cmd_succession, "Compute the line of succession to a title.")
    p.add_argument("--title")
    p.add_argument("--year", type=int)
    p.add_argument("--month", type=int, default=1)
    p.add_argument("--day", type=int, default=1)
    p.add_argument("--law", help="override the title's succession law")
    p.add_argument("--illegitimate", metavar="PERSON",
                   help="hypothetical: treat this person as illegitimate")
    p.add_argument("--dead", metavar="PERSON",
                   help="hypothetical: treat this person as dead")

    p = add("check", cmd_check, "Run continuity checks.")
    p.add_argument("--minimum", choices=["notice", "warning", "error"], default="notice")
    p.add_argument("--strict", action="store_true", help="exit non-zero on any error")
    p.add_argument("--show-fingerprints", action="store_true")

    p = add("suppress", cmd_suppress, "Mark a continuity finding as intentional.")
    p.add_argument("rule_key")
    p.add_argument("fingerprint")
    p.add_argument("--reason", default="")

    p = add("route", cmd_route, "How long does it take to travel between two places?")
    p.add_argument("origin")
    p.add_argument("destination")
    p.add_argument("--profile", choices=list(PROFILES))
    p.add_argument("--year", type=int)
    p.add_argument("--party-size", type=int)

    p = add("scene", cmd_scene, "Show everything relevant to a scene.")
    p.add_argument("--scene", help="match a scene by title")

    p = add("why", cmd_why, "Why does this matter?")
    p.add_argument("entity")
    p.add_argument("--year", type=int)

    p = add("impact", cmd_impact, "What changes if this disappears?")
    p.add_argument("entity")
    p.add_argument("--year", type=int)

    p = add("kin", cmd_kin, "Who is related to this person?")
    p.add_argument("entity")
    p.add_argument("--hops", type=int, default=3)

    p = add("export", cmd_export, "Export the world as JSON.")
    p.add_argument("--out")

    p = add("restore", cmd_restore, "Undo a recorded change.")
    p.add_argument("revision", type=int, nargs="?", help="revision id to restore")
    p.add_argument("--list", action="store_true",
                   help="list deletions that can still be undone")

    p = add("serve", cmd_serve, "Start the local web application.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--log-level", default="warning")
    p.add_argument("--library", default=None,
                   help="directory the Worlds screen lists saves from "
                        "(default: worlds/, or the served file's own directory)")
    p.add_argument("--open", dest="open_browser", action="store_true",
                   help="open the app in the default browser once the server is up")

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
