"""The world database schema.

One `.fwworld` file is one SQLite database. The project, the backup and the export are the
same object, so the brief's "portable project export" (§63) is `cp` rather than a feature.

The central decision here is the **fact spine**. §3 requires that *every relevant entity
property or relationship* may carry a temporal range, and that is not satisfiable by columns
on an entity table. So a property and a relationship are the same row shape: an assertion of
`(subject, predicate, object-or-value)` with a validity interval and provenance. A person's
hair colour and their oath of fealty are stored identically, which is why temporality (§3),
confidence (§57), secrecy (§6), sourcing (§58) and alternate timelines (§105) apply uniformly
to both without a second implementation.

Everything the writer can invent — entity types, predicates, qualitative scales, calendars,
succession laws — is data in tables, never a Python class. That is what makes §60's
customisation free rather than a rewrite, and it is why the software is not locked to
European-medieval fantasy.
"""

from __future__ import annotations

SCHEMA_VERSION = 4

# `application_id` marks the file as ours so a stray SQLite database is not mistaken for a
# world. The value is "FWLD" read as big-endian ASCII.
APPLICATION_ID = 0x46574C44

SCHEMA = """
-- ---------------------------------------------------------------- project & branching

CREATE TABLE project (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    calendar_id   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
) STRICT;

-- §105 alternate timelines. Every fact belongs to a branch; 'canon' always exists.
-- Present from the first migration on purpose: adding a branch column to a populated
-- world later would be a painful migration, adding it now is one column.
CREATE TABLE branch (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    parent_id     TEXT REFERENCES branch(id),
    branched_at   INTEGER,             -- day index the branch diverges from its parent
    is_canon      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    UNIQUE (project_id, name)
) STRICT;

-- ---------------------------------------------------------------- calendars (§3)

CREATE TABLE calendar (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    weekdays       TEXT NOT NULL,       -- JSON array of names
    leap_every     INTEGER,
    leap_except    TEXT NOT NULL DEFAULT '[]',
    leap_always    TEXT NOT NULL DEFAULT '[]',
    leap_month     INTEGER NOT NULL DEFAULT 1,
    epoch_weekday  INTEGER NOT NULL DEFAULT 0,
    seasons        TEXT NOT NULL DEFAULT '[]',
    UNIQUE (project_id, name)
) STRICT;

CREATE TABLE calendar_month (
    calendar_id   TEXT NOT NULL REFERENCES calendar(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    name          TEXT NOT NULL,
    days          INTEGER NOT NULL,
    PRIMARY KEY (calendar_id, position)
) STRICT;

CREATE TABLE era (
    id            TEXT PRIMARY KEY,
    calendar_id   TEXT NOT NULL REFERENCES calendar(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    abbreviation  TEXT NOT NULL,
    start_year    INTEGER NOT NULL,
    end_year      INTEGER
) STRICT;

-- ---------------------------------------------------------------- the type system (§60)

CREATE TABLE entity_type (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    key           TEXT NOT NULL,          -- 'person', 'settlement', 'house'
    label         TEXT NOT NULL,
    plural        TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'other',
    icon          TEXT NOT NULL DEFAULT '',
    -- §56 progressive complexity: which fields a beginner sees before expanding
    core_fields   TEXT NOT NULL DEFAULT '[]',
    is_builtin    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (project_id, key)
) STRICT;

-- §5 user-defined qualitative scales: 'deeply trusts' .. 'actively hostile'.
-- Ordinal positions let the UI sort and colour them without forcing the writer to use numbers.
CREATE TABLE scale (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    key           TEXT NOT NULL,
    label         TEXT NOT NULL,
    steps         TEXT NOT NULL,          -- JSON array of {value,label,rank}
    UNIQUE (project_id, key)
) STRICT;

-- A predicate is what makes a fact mean something. `kind` distinguishes a property
-- ('prop': subject -> literal value) from a relationship ('rel': subject -> entity).
CREATE TABLE predicate (
    id                    TEXT PRIMARY KEY,
    project_id            TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    key                   TEXT NOT NULL,
    label                 TEXT NOT NULL,
    kind                  TEXT NOT NULL CHECK (kind IN ('prop', 'rel')),
    -- §77 bidirectional linking: naming the inverse once means both entity pages show the
    -- link, and the writer never enters the same fact twice (§106.1).
    inverse_key           TEXT,
    symmetric             INTEGER NOT NULL DEFAULT 0,
    -- transitive predicates ('vassal_of', 'located_in') are the ones worth walking
    transitive            INTEGER NOT NULL DEFAULT 0,
    datatype              TEXT NOT NULL DEFAULT 'text',
    scale_key             TEXT,
    domain_type_keys      TEXT NOT NULL DEFAULT '[]',
    range_type_keys       TEXT NOT NULL DEFAULT '[]',
    category              TEXT NOT NULL DEFAULT 'other',
    description           TEXT NOT NULL DEFAULT '',
    is_builtin            INTEGER NOT NULL DEFAULT 0,
    UNIQUE (project_id, key)
) STRICT;

-- ---------------------------------------------------------------- entities

CREATE TABLE entity (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    type_key        TEXT NOT NULL,
    name            TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    -- Existence is temporal too: a settlement founded in 240 should not appear on a map
    -- of year 215, and a character has a lifespan (§36, §46).
    exists_from     INTEGER,
    exists_to       INTEGER,
    exists_from_hi  INTEGER,     -- upper bound of an uncertain start
    exists_to_lo    INTEGER,     -- lower bound of an uncertain end
    branch_id       TEXT NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    confidence      TEXT NOT NULL DEFAULT 'canon',
    tags            TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
) STRICT;

CREATE INDEX ix_entity_project_type ON entity(project_id, type_key, name);
CREATE INDEX ix_entity_branch       ON entity(branch_id, type_key);
CREATE INDEX ix_entity_existence    ON entity(project_id, exists_from, exists_to);

-- §105: a branch never copies the world. An entity inherited from an ancestor branch
-- is changed *in the branch* by a patch of field values laid over it at read time —
-- canon rows are never written from a branch.
CREATE TABLE entity_override (
    branch_id     TEXT NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    entity_id     TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    changes       TEXT NOT NULL DEFAULT '{}',      -- JSON: the edited fields only
    PRIMARY KEY (branch_id, entity_id)
) STRICT;

-- ---------------------------------------------------------------- the fact spine

CREATE TABLE fact (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    branch_id       TEXT NOT NULL REFERENCES branch(id) ON DELETE CASCADE,

    subject_id      TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    predicate_key   TEXT NOT NULL,
    object_id       TEXT REFERENCES entity(id) ON DELETE CASCADE,   -- for kind='rel'
    value           TEXT,                                           -- for kind='prop'

    -- Reification: a fact *about another fact*. §33 wants competing accounts of one
    -- claim and §57 wants "publicly believed" and "canonical secret" to coexist as two
    -- assertions rather than one field that must pick a side. Without this, a fact can
    -- only ever be about an entity, and those sections need a second, parallel mechanism
    -- that shares none of the spine's temporality or provenance. It is one nullable
    -- column now against a migration and a divided query surface later.
    about_fact_id   TEXT REFERENCES fact(id) ON DELETE CASCADE,

    -- §105 branch overlays: a branch changes an inherited fact by writing its own row
    -- that *supersedes* the ancestor's. Reads on the branch hide the superseded row;
    -- reads on the ancestor never see the branch's. A superseding row whose props
    -- carry {"branch_tombstone": true} deletes the fact for the branch alone.
    supersedes_id   TEXT REFERENCES fact(id) ON DELETE CASCADE,

    -- §3 temporal validity. Bounds are day indices; the *_hi / *_lo columns carry the
    -- uncertainty, so 'from sometime in the 310s' is representable without losing the
    -- ability to index and range-scan.
    valid_from      INTEGER,
    valid_from_hi   INTEGER,
    valid_to        INTEGER,
    valid_to_lo     INTEGER,
    precision       TEXT NOT NULL DEFAULT 'exact',

    -- §57 status, §6 secrecy, §58 sourcing. On every fact, not a subset.
    confidence      TEXT NOT NULL DEFAULT 'canon',
    secrecy         TEXT NOT NULL DEFAULT 'public',
    strength        TEXT,
    source_id       TEXT REFERENCES source(id) ON DELETE SET NULL,
    note            TEXT NOT NULL DEFAULT '',
    props           TEXT NOT NULL DEFAULT '{}',

    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    CHECK (object_id IS NOT NULL OR value IS NOT NULL)
) STRICT;

-- These four indexes carry essentially every query in the application. The composite
-- (predicate, subject) and (predicate, object) pairs are what let recursive CTEs walk the
-- graph both ways without a scan -- see the ANALYZE note in fw/core/store/db.py.
CREATE INDEX ix_fact_subject   ON fact(branch_id, subject_id, predicate_key);
CREATE INDEX ix_fact_object    ON fact(branch_id, object_id, predicate_key);
CREATE INDEX ix_fact_pred_subj ON fact(branch_id, predicate_key, subject_id);
CREATE INDEX ix_fact_pred_obj  ON fact(branch_id, predicate_key, object_id);
CREATE INDEX ix_fact_temporal  ON fact(branch_id, predicate_key, valid_from, valid_to);
CREATE INDEX ix_fact_about     ON fact(about_fact_id) WHERE about_fact_id IS NOT NULL;
CREATE INDEX ix_fact_supersedes ON fact(supersedes_id) WHERE supersedes_id IS NOT NULL;

-- ---------------------------------------------------------------- events & causality

CREATE TABLE event (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    branch_id      TEXT NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    entity_id      TEXT REFERENCES entity(id) ON DELETE CASCADE,
    type_key       TEXT NOT NULL DEFAULT 'event',
    name           TEXT NOT NULL,
    summary        TEXT NOT NULL DEFAULT '',
    start_day      INTEGER,
    start_day_hi   INTEGER,
    end_day        INTEGER,
    end_day_lo     INTEGER,
    precision      TEXT NOT NULL DEFAULT 'exact',
    location_id    TEXT REFERENCES entity(id) ON DELETE SET NULL,
    confidence     TEXT NOT NULL DEFAULT 'canon',
    secrecy        TEXT NOT NULL DEFAULT 'public',
    props          TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
) STRICT;

CREATE INDEX ix_event_time ON event(branch_id, start_day, end_day);

CREATE TABLE event_participant (
    event_id     TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    entity_id    TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    role         TEXT NOT NULL DEFAULT 'participant',   -- participant | witness | victim ...
    note         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (event_id, entity_id, role)
) STRICT;

CREATE INDEX ix_participant_entity ON event_participant(entity_id);

-- §32 causal chains: flood -> crop failure -> grain shortage -> unrest -> rebellion.
-- branch_id is nullable for files migrated from before branches; NULL reads as canon.
-- Uniqueness of a (cause, effect) pair is per branch, via the expression index below.
CREATE TABLE causal_link (
    id           TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    cause_id     TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    effect_id    TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL DEFAULT 'caused',
    confidence   TEXT NOT NULL DEFAULT 'canon',
    note         TEXT NOT NULL DEFAULT '',
    branch_id    TEXT REFERENCES branch(id) ON DELETE CASCADE
) STRICT;

CREATE UNIQUE INDEX ux_causal_pair
    ON causal_link(cause_id, effect_id, ifnull(branch_id, ''));
CREATE INDEX ix_causal_branch ON causal_link(branch_id);

-- §33 the same event, told differently by different parties
CREATE TABLE interpretation (
    id           TEXT PRIMARY KEY,
    event_id     TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
    holder_id    TEXT REFERENCES entity(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    account      TEXT NOT NULL DEFAULT ''
) STRICT;

-- ---------------------------------------------------------------- knowledge (§6)

-- What is true, what a character knows, what they believe, what they merely suspect, and
-- what they wrongly believe are five different things. Separating them is what makes
-- dramatic irony queryable rather than something the writer must hold in their head.
CREATE TABLE secret (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    branch_id     TEXT NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    truth         TEXT NOT NULL DEFAULT '',       -- what is actually the case
    about_id      TEXT REFERENCES entity(id) ON DELETE CASCADE,
    fact_id       TEXT REFERENCES fact(id) ON DELETE SET NULL,
    severity      TEXT NOT NULL DEFAULT 'major',
    created_at    TEXT NOT NULL
) STRICT;

CREATE TABLE knowledge_state (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    branch_id     TEXT NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    observer_id   TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    secret_id     TEXT REFERENCES secret(id) ON DELETE CASCADE,
    -- knows | believes | suspects | misinformed | unaware
    stance        TEXT NOT NULL,
    -- §6 'who knows that another person knows' -- second-order awareness
    about_observer_id TEXT REFERENCES entity(id) ON DELETE CASCADE,
    acquired_on   INTEGER,
    acquired_from TEXT REFERENCES entity(id) ON DELETE SET NULL,
    scene_id      TEXT,
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
) STRICT;

CREATE INDEX ix_knowledge_secret   ON knowledge_state(branch_id, secret_id, stance);
CREATE INDEX ix_knowledge_observer ON knowledge_state(branch_id, observer_id);

-- ---------------------------------------------------------------- titles (§8)

CREATE TABLE title (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    branch_id         TEXT NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    entity_id         TEXT REFERENCES entity(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    rank              INTEGER NOT NULL DEFAULT 0,
    territory_id      TEXT REFERENCES entity(id) ON DELETE SET NULL,
    succession_law    TEXT NOT NULL DEFAULT 'male_preference_primogeniture',
    -- Succession walks the title's own line, not the last holder's children: the heir may
    -- be a cousin reachable only through the dynasty's founder.
    dynasty_root_id   TEXT REFERENCES entity(id) ON DELETE SET NULL,
    created_on        INTEGER,
    abolished_on      INTEGER,
    props             TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL
) STRICT;

-- branch_id is nullable for files from before branches; NULL reads as canon.
CREATE TABLE title_holding (
    id            TEXT PRIMARY KEY,
    title_id      TEXT NOT NULL REFERENCES title(id) ON DELETE CASCADE,
    holder_id     TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    from_day      INTEGER,
    to_day        INTEGER,
    how           TEXT NOT NULL DEFAULT 'inheritance',
    disputed      INTEGER NOT NULL DEFAULT 0,
    note          TEXT NOT NULL DEFAULT '',
    branch_id     TEXT REFERENCES branch(id) ON DELETE CASCADE
) STRICT;

CREATE INDEX ix_holding_title  ON title_holding(title_id, from_day, to_day);
CREATE INDEX ix_holding_branch ON title_holding(branch_id);

-- ---------------------------------------------------------------- geography (§34, §91)

-- Geometry is versioned by validity, never overwritten: §91 insists that political
-- geography support changing boundaries rather than replacing current regions.
CREATE TABLE geometry (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    branch_id     TEXT NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    entity_id     TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,        -- point | line | polygon
    coordinates   TEXT NOT NULL,        -- GeoJSON-shaped array in world units
    valid_from    INTEGER,
    valid_to      INTEGER,
    layer         TEXT NOT NULL DEFAULT 'base',
    style         TEXT NOT NULL DEFAULT '{}',
    -- §92 fictional maps may be intentionally vague
    approximate   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
) STRICT;

CREATE INDEX ix_geometry_entity ON geometry(branch_id, entity_id);
CREATE INDEX ix_geometry_layer  ON geometry(branch_id, layer, valid_from, valid_to);

-- R*Tree bounding boxes, so 'what is in this viewport' is a 0.4 ms lookup at 20k features
-- rather than a scan. rowid joins back to geometry via geometry_rtree_map.
CREATE VIRTUAL TABLE geometry_bbox USING rtree(id, min_x, max_x, min_y, max_y);

CREATE TABLE geometry_rtree_map (
    rtree_id      INTEGER PRIMARY KEY,
    geometry_id   TEXT NOT NULL UNIQUE REFERENCES geometry(id) ON DELETE CASCADE
) STRICT;

-- §20 roads and §21 waterways as a routable network
CREATE TABLE route_segment (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    branch_id         TEXT NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    entity_id         TEXT REFERENCES entity(id) ON DELETE CASCADE,   -- the road/river
    from_entity_id    TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    to_entity_id      TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    medium            TEXT NOT NULL DEFAULT 'road',   -- road | river | sea | pass
    length            REAL NOT NULL,
    quality           REAL NOT NULL DEFAULT 1.0,
    terrain           TEXT NOT NULL DEFAULT 'plain',
    built_on          INTEGER,
    ruined_on         INTEGER,
    closed_seasons    TEXT NOT NULL DEFAULT '[]',
    danger            TEXT NOT NULL DEFAULT 'low',
    toll_holder_id    TEXT REFERENCES entity(id) ON DELETE SET NULL,
    props             TEXT NOT NULL DEFAULT '{}'
) STRICT;

CREATE INDEX ix_segment_from ON route_segment(branch_id, from_entity_id);
CREATE INDEX ix_segment_to   ON route_segment(branch_id, to_entity_id);

-- ---------------------------------------------------------------- manuscript (§43, §44)

CREATE TABLE work (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'novel',
    position      INTEGER NOT NULL DEFAULT 0,
    summary       TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE chapter (
    id            TEXT PRIMARY KEY,
    work_id       TEXT NOT NULL REFERENCES work(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    position      INTEGER NOT NULL DEFAULT 0,
    summary       TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE TABLE scene (
    id             TEXT PRIMARY KEY,
    project_id     TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    branch_id      TEXT NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    chapter_id     TEXT REFERENCES chapter(id) ON DELETE SET NULL,
    title          TEXT NOT NULL,
    position       INTEGER NOT NULL DEFAULT 0,
    day            INTEGER,
    end_day        INTEGER,
    location_id    TEXT REFERENCES entity(id) ON DELETE SET NULL,
    pov_id         TEXT REFERENCES entity(id) ON DELETE SET NULL,
    objective      TEXT NOT NULL DEFAULT '',
    conflict       TEXT NOT NULL DEFAULT '',
    outcome        TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
) STRICT;

CREATE INDEX ix_scene_day ON scene(branch_id, day);

CREATE TABLE scene_participant (
    scene_id     TEXT NOT NULL REFERENCES scene(id) ON DELETE CASCADE,
    entity_id    TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    role         TEXT NOT NULL DEFAULT 'present',
    PRIMARY KEY (scene_id, entity_id)
) STRICT;

-- ---------------------------------------------------------------- provenance (§58, §59)

CREATE TABLE source (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL DEFAULT 'author_note',
    label         TEXT NOT NULL,
    detail        TEXT NOT NULL DEFAULT '',
    scene_id      TEXT REFERENCES scene(id) ON DELETE SET NULL
) STRICT;

-- §59 revision history. Append-only: the row records what changed, so history is never
-- lost to an overwrite, satisfying §106.3. `action_id` groups every record written in
-- one transaction into one *user action* — the unit undo and redo operate on.
CREATE TABLE revision (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    table_name    TEXT NOT NULL,
    row_id        TEXT NOT NULL,
    action        TEXT NOT NULL,          -- insert | update | delete | undo
    before        TEXT,
    after         TEXT,
    at            TEXT NOT NULL,
    note          TEXT NOT NULL DEFAULT '',
    action_id     TEXT NOT NULL DEFAULT ''
) STRICT;

CREATE INDEX ix_revision_row    ON revision(table_name, row_id, id);
CREATE INDEX ix_revision_action ON revision(action_id, id);

-- §46 'allow intentional exceptions'
CREATE TABLE continuity_suppression (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    rule_key      TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    UNIQUE (project_id, rule_key, fingerprint)
) STRICT;

-- §80 named snapshots: a label on a date, not a copy of the world
CREATE TABLE snapshot (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
    branch_id     TEXT NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    day           INTEGER NOT NULL,
    note          TEXT NOT NULL DEFAULT ''
) STRICT;

-- ---------------------------------------------------------------- search (§53)

-- FTS5 with the trigram tokenizer alongside a porter-stemmed index: the porter index
-- answers word queries, the trigram index answers the fuzzy/substring matching §53 asks
-- for ('eyhav' finds Greyhaven), and neither needs a dependency.
CREATE VIRTUAL TABLE entity_fts USING fts5(
    name, summary, tags, entity_id UNINDEXED, type_key UNINDEXED,
    tokenize = 'porter unicode61'
);

CREATE VIRTUAL TABLE entity_trigram USING fts5(
    name, entity_id UNINDEXED,
    tokenize = 'trigram'
);
"""


MIGRATIONS: dict[int, str] = {
    # Version 1 is the initial schema above. Later versions append here; `user_version`
    # in the file records which have been applied.
    #
    # 2: revisions gain `action_id`, grouping the records of one transaction into one
    #    undoable user action. Rows from before this migration keep '' and are simply
    #    outside undo's reach — still restorable individually, never mis-grouped.
    #
    # NOTE: migration statements are split on ';' and run one by one inside a guarded
    # transaction — no statement may contain an embedded semicolon or manage its own
    # transaction.
    2: """
        ALTER TABLE revision ADD COLUMN action_id TEXT NOT NULL DEFAULT '';
        CREATE INDEX ix_revision_action ON revision(action_id, id);
    """,
    # 3: §105 branch overlays become real. Facts gain `supersedes_id` (a branch's own
    #    row hiding an inherited one), entity_override carries per-branch field
    #    patches, and causal links become branch-scoped — the old global UNIQUE pair
    #    constraint is rebuilt as a per-branch unique index, with existing rows
    #    assigned to the canon branch.
    3: """
        ALTER TABLE fact ADD COLUMN supersedes_id TEXT REFERENCES fact(id) ON DELETE CASCADE;
        CREATE INDEX ix_fact_supersedes ON fact(supersedes_id) WHERE supersedes_id IS NOT NULL;
        CREATE TABLE entity_override (
            branch_id  TEXT NOT NULL REFERENCES branch(id) ON DELETE CASCADE,
            entity_id  TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
            changes    TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (branch_id, entity_id)
        ) STRICT;
        CREATE TABLE causal_link_v3 (
            id           TEXT PRIMARY KEY,
            project_id   TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
            cause_id     TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
            effect_id    TEXT NOT NULL REFERENCES event(id) ON DELETE CASCADE,
            kind         TEXT NOT NULL DEFAULT 'caused',
            confidence   TEXT NOT NULL DEFAULT 'canon',
            note         TEXT NOT NULL DEFAULT '',
            branch_id    TEXT REFERENCES branch(id) ON DELETE CASCADE
        ) STRICT;
        INSERT INTO causal_link_v3
            SELECT id, project_id, cause_id, effect_id, kind, confidence, note,
                   (SELECT id FROM branch WHERE is_canon = 1)
            FROM causal_link;
        DROP TABLE causal_link;
        ALTER TABLE causal_link_v3 RENAME TO causal_link;
        CREATE UNIQUE INDEX ux_causal_pair
            ON causal_link(cause_id, effect_id, ifnull(branch_id, ''));
        CREATE INDEX ix_causal_branch ON causal_link(branch_id)
    """,
    # 4: title grants become branch-scoped too — a coup tried on a what-if must not
    #    crown anyone on the main timeline. Existing rows keep NULL, which every
    #    timeline reads as canon's.
    4: """
        ALTER TABLE title_holding ADD COLUMN branch_id TEXT REFERENCES branch(id) ON DELETE CASCADE;
        CREATE INDEX ix_holding_branch ON title_holding(branch_id)
    """,
}
