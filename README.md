# FW — a worldbuilding application for fiction writers

An external cognitive model of a fictional world, built from `DesignSpec.pdf` in this
repository.

The brief's premise is that a writer should never have to hold their whole world in their
head. So this is not a wiki with a family-tree page bolted on: it is one interconnected,
temporal model that can be asked questions.

- **What was true in year 215?** Every fact carries a validity interval, so one slider
  moves the map, the borders, the titles, the living cast and the alliances together.
- **Who inherits?** Succession is computed from the genealogy under a chosen law, as of a
  date — and "what if he were declared illegitimate?" is a question, not an edit.
- **Why is this scene tense?** Open it and the relationships, secrets, goals and recent
  history that bear on the room are already there, ranked.
- **Does any of this contradict itself?** Twenty continuity rules run over the world,
  including the one that catches a journey the timeline does not allow.
- **What if it had gone differently?** Fork an alternate timeline (§105): it inherits the
  whole world, everything you change in it stays in it, and the main timeline cannot be
  touched from a branch — succession, maps and state all answer per-timeline.
- **Take it back.** Every change is one Ctrl+Z away — creations, edits, deletions with
  their whole cascade, restores — with redo, per timeline, surviving a restart. Deleted
  things also wait in Recently deleted on the dashboard.

## Running it

**Windows:** double-click `run.bat`. **macOS / Linux:** run `./run.sh`. The first run
installs everything it needs (Python 3.11+ must already be on the machine); after that it
goes straight to the app in your browser. You start on the launcher: name a new world and
begin, open one of your saves, or — only if you ask for it — create the example Kingdom of
Renn to see what everything does.

Every world is one portable `.fwworld` file in the `worlds/` folder. Copying that file is
a backup; copying it to another machine is moving your world.

From a terminal instead:

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"

.venv/bin/fw serve                 # the launcher: create/open worlds in the browser
.venv/bin/fw serve my.fwworld     # or serve one specific world file
```

The browser client ships prebuilt in `web/dist` (so the launchers need no Node); to
rebuild it from source, `cd web && npm install && npm run build`. The API and the CLI are
complete without it, and `fw serve` will say so rather than showing a broken page.

```bash
.venv/bin/fw seed demo.fwworld     # write the worked example world (§115) to a file
                                   # (grows its map too; --no-map for the facts alone)
```

```bash
fw succession demo.fwworld --title "King of Nyren" --year 1100 --month 5 --day 41
fw succession demo.fwworld --title "King of Nyren" --illegitimate "Prince Hadren"
fw route demo.fwworld Meret Hadrin
fw scene demo.fwworld
fw why demo.fwworld Orra
fw impact demo.fwworld Orra
fw check demo.fwworld
```

## The example world

`fw seed` builds the continent: a temperate landmass that had a history before anyone
conquered it. Six peoples arranged by their own geography — the Merra on the western
coast with the best middleman position on the map, the Carthi in the river basin that
feeds everyone, the Vardi in the wet uplands with the timber, the Selli in the northern
forests, the Talari in the warm southern hills, the Arthi in the mountains where the
Carth rises, and the Orri on the dry steppe beyond the rain shadow with the horses.

Then the Nyri came from Nyreland across the Northern Sea — not raiders but a
centralised, literate kingdom that arrived because a Carthi claimant asked for help and
stayed because nobody could make them leave. Over forty years (612–653) they took the
interior. They never took Merran, and that is the fault line the four hundred and fifty
years since have run along: Nyren grows the grain, Merran owns the mouth of the river,
and Carthain's kings in the upper valley say both of them are newcomers.

It is the demonstration dataset §115 asks for — realms, regions, settlements, noble
houses, a royal dynasty, twelve characters, a disputed succession, roads, a major river,
resources, trade routes, wars, a secret and an active political crisis — and it arrives
with its map already grown, so the first thing you open is an atlas rather than an empty
sheet. Pass `--no-map` to skip that.

The disputed inheritance, the secret and the crisis are one thing. Old King Renn fathered
Aldren and Corren. Aldren's legal children are Oren and Elia; Corren's are Caros and Mara.
But Oren's *biological* father is Corren — which is §57's worked example of a fact that is
publicly believed and canonically false at the same time, and the reason the succession is
contested.

The world doubles as the integration-test fixture, so the tests exercise the same world a
new user first opens.

## Architecture

A headless Python domain core with thin adapters. The HTTP layer and the React client are
both adapters; all world logic lives in `fw/core` and is testable with no server and no
browser.

```
fw/core/     the domain. Never imports the web framework — enforced in CI.
  calendar/    fictional calendars, eras, uncertain dates, day-index conversion
  model/       records and the starting vocabulary of types and predicates
  store/       SQLite schema, connection policy, migrations
  genealogy/   parentage, kinship, pedigree layout
  succession/  succession laws and the heir-ordering engine
  geo/         routing over the road and river network
  continuity/  the rule registry
  derive/      scene context and dependency analysis
  seed/        the Kingdom of Renn
  world.py     the World facade — the core's public surface
fw/api/      FastAPI routers
fw/cli/      the `fw` command
web/         React 19 + TypeScript + Vite
tests/       pytest
```

### The fact spine

The decision everything rests on: **a property and a relationship are the same thing.** §3
requires that every property *or* relationship may carry a temporal range, which columns on
an entity table cannot express. So both are one row shape:

```
fact(subject, predicate, object | value, valid_from, valid_to, confidence,
     secrecy, source, branch, about_fact_id)
```

A person's hair colour and their oath of fealty store identically, so temporality (§3),
confidence (§57), secrecy (§6), sourcing (§58) and alternate-timeline branching (§105) apply
uniformly to both without a second implementation. `predicate.inverse_key` names the inverse
once, which is why §77's bidirectional linking is a lookup rather than a duplicated row, and
why §106.1 — never enter the same fact twice — is enforced by the schema rather than by
discipline.

Entity types, predicates and scales are **rows, not classes**. The built-ins are installed
through the same API a writer's own custom type would use, so §60's extensibility is
structural rather than promised.

### Storage

One `.fwworld` file is one SQLite database, so §63's "portable project export" is `cp`.
STRICT tables, foreign keys on, FTS5 for search (including trigram for fuzzy matching) and
R\*Tree for map viewports — all from the standard library, no extra dependency.

**`ANALYZE` is not optional.** Recursive CTEs carry every graph traversal in this
application. Benchmarked at the brief's own §99 scale — 50,000 entities and 200,000
relationships — the kinship traversal took **264 ms** without table statistics and **0.26 ms**
with them: without `ANALYZE`, SQLite's planner declines the indexes on the recursive step and
degrades to scans. The store runs it after bulk load and `PRAGMA optimize` on close. Meeting
this after the graph views were built would have looked like "SQLite cannot do graphs" and
invited exactly the wrong, expensive rewrite §64 warns against.

At that same scale: world-state-at-date 12.9 ms, transitive vassal chains 4.2 ms, full
descendant subtree 21.3 ms, map viewport over 20k features 0.39 ms, search across 50k
entities 0.62 ms.

## Testing

```bash
.venv/bin/python -m pytest      # 210 tests
.venv/bin/ruff check fw/
.venv/bin/lint-imports          # the layering contract
cd web && npm run build         # typechecks as part of the build
python scripts/screenshot.py    # drives the real UI in a browser
```

Three things are tested in ways worth mentioning:

- **The calendar is checked against an oracle.** Gregorian output is compared against
  Python's own proleptic calendar across two centuries, and `hypothesis` round-trips
  randomly generated calendars — arbitrary month counts and lengths, arbitrary leap rules,
  pre-epoch years.
- **Succession is checked against the brief.** §8 states the expected answer outright, so
  the test asserts that ordering rather than whatever the engine happens to produce.
- **Layout is tested as coordinates, not pixels.** `layout_pedigree` returns
  `{node_id: (x, y)}`, so "does the family tree look right" becomes "are the coordinates
  right" — which diffs in review, where a screenshot does not.

## Scope

Implemented: the entity and relationship model with custom types — structurally, through
the same rows the built-ins use, and added from a screen of their own — temporal facts,
world-state-at-date, genealogy with legal/biological/adoptive parentage, twelve succession
laws with hypotheticals, the four-way territorial distinction (§11), layered temporal maps,
the relationship graph, the pedigree, events and causal chains, layered knowledge and
secrets, scene context with relevance ranking, twenty continuity rules with suppressions,
routing, search, and the "why does this matter" / "what if it vanished" analyses.

Building happens in the client too: create entities (three fields, everything else behind
one disclosure, per §56), write scenes and record events with participants and roles (§31,
§44), place a scene in a chapter of the manuscript (§43), create a title and grant it so
there is something to inherit (§8), record a secret and who thinks what about it (§6),
link one event to its consequences (§32), record relationships and properties with
in-world dates, end a fact on the timeline's current date rather than deleting it (§106.3's
rule as the one-click path), and edit or delete from the side panel without losing your
place (§76). And ask the world questions (§49): a structured filter over the fact spine,
built from a form rather than typed, compiled to one SQL statement, with the answer's
working shown and the question keepable so it can be asked again when the answer has
changed. Every mutation is written to the §59 revision log inside the same
transaction — deletions record the complete row, including the facts an entity takes with
it — and any recorded change can be walked back: a deleted entity is restored from the
dashboard with its connections, an edit from the entity's own change history, and a restore
is itself a logged, reversible change. The dashboard shows what changed most recently
(§74), and `fw restore` does all of this from the command line.

The map is grown from what the writer wrote rather than from noise: one reading of their
world (regions, houses, titles, roads, rivers, events, resources — not two fields), a
continent shaped before politics, erosion and rain shadow, vegetation, movement cost,
settlements sited from the ground, roads that bundle into highways, castles at the passes,
borders that follow where the towns stop reaching, and a name for everything it draws.
Nothing is written until the writer accepts it, feature by feature, with the case for each
one in a sentence (§66, §67).

And it can be read through somebody's eyes (§93, §94). The same battle is told three ways
and the same man is called two things — §33's `interpretation`, which had a table and no
reader — and a perspective joins that to §11's claims and to what a party has never heard
of, so House Orren's map shows the Northmarch as theirs, House Marr's list calls Prince
Oren the Pretender, and a place Lady Mara does not know about is simply not on her map.
Ignorance is opt-in: a world nobody has annotated renders exactly as it did before, because
§93 asks for this *optionally* and a lens that hid everything unstated would be useless.
The objective world is one click away, and the view says exactly what it changed and why
(§67) rather than quietly altering a map.

And it answers the question §19 asks it: *where does Greyhaven get its grain?* The facts
were all there and nothing joined them, so the answer is a trace rather than a simulation
— the Vale exports it, the road runs Red Ford to Greyhaven, it is 2.8 days by wagon on
this date, and the Vale is the only source Greyhaven can reach, which is a plot. Seasonal
closures and construction dates apply, so a supply line can be open in summer and gone in
winter. The same reading answers §117's *“Who benefits?”*: what a house is worth is
counted from the ground it holds, the people on it and the roads it can tax, rather than
read off a `prestige: high` label somebody typed.

Deliberately not yet built, and reachable because of the decisions above:

- **Economic simulation.** Supply is *traced* (§19) — who says they produce a thing, who
  says they need it, and how long the road takes on the day in question — and nothing
  computes a yield from soil, labour and rainfall. §68 and §116 both warn against adding
  simulation for its own sake.
- **The plausibility assistant (§73), AI features (§103) and plugins (§102).**
- **Prose.** Scenes carry metadata and context, not manuscript text.
- **Perspective outside the map.** The map, the world state and the entity list can be
  read through one party's eyes; the graph, the pedigree and the timeline are still the
  view from nowhere.

## The brief's own principles

§116 asks that every feature be measured against one question: *does this help a writer
understand, navigate, reason about, or write their fictional world more easily?* Where a
feature here looks unusual, that is generally the reason — the relevance ranking in scene
context exists because an unranked panel of four hundred facts sends the writer straight
back to remembering things themselves, and every derived conclusion carries its evidence
because §67 refuses black boxes.
