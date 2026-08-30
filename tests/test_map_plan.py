"""A map that does not exist yet: propose, argue with it, then accept (§66, §67)."""

from __future__ import annotations

import pytest

from fw.core.calendar.kernel import GREGORIAN
from fw.core.mapgen import ledger as ledger_module
from fw.core.mapgen.apply import PlanStale, apply_plan
from fw.core.mapgen.decide import NAMESPACE, Decision, DecisionSet, carry, load_standing
from fw.core.mapgen.pipeline import plan_map
from fw.core.mapgen.plan import MapBrief, MapPlan
from fw.core.world import World


@pytest.fixture
def prose() -> World:
    """A world described only in words, so everything on its map is generated."""
    w = World.create(name="Ashmere", calendar=GREGORIAN)
    specs = [
        ("The Iron Spine", "mountains and high crags", "cold, heavy snow", "60000"),
        ("The Sunlit Coast", "coast and harbour", "warm and humid", "140000"),
        ("The Amber Steppe", "steppe and dry grassland", "hot, arid", "40000"),
        ("Greenhollow", "forest and river valley", "temperate, rain", "90000"),
    ]
    ids = {}
    for name, terrain, climate, population in specs:
        region = w.add_entity("region", name)
        for key, value in (("terrain", terrain), ("climate", climate),
                           ("population", population)):
            w.assert_fact(region, key, value=value)
        ids[name] = region.id
    w.assert_fact(ids["The Iron Spine"], "borders", ids["Greenhollow"])
    w.assert_fact(ids["The Sunlit Coast"], "borders", ids["Greenhollow"])
    yield w
    w.close()


def full(world: World) -> MapPlan:
    return plan_map(world, MapBrief(invent_settlements=True))


def revisions(world: World) -> int:
    return world.db.one("SELECT COUNT(*) AS n FROM revision")["n"]


class TestPlanningIsPure:
    def test_planning_writes_nothing(self, prose: World):
        before = revisions(prose)
        touched: list[str] = []
        prose.db.conn.set_trace_callback(
            lambda sql: touched.append(sql)
            if sql.strip().upper().startswith(("INSERT", "UPDATE", "DELETE")) else None)
        try:
            full(prose)
        finally:
            prose.db.conn.set_trace_callback(None)
        assert revisions(prose) == before
        assert not touched, f"planning wrote: {touched[:3]}"

    def test_two_identical_worlds_plan_identically(self):
        """Not similar — the same plan id. Entity ids differ between the two."""
        plans = []
        for _ in range(2):
            w = World.create(name="Twin", calendar=GREGORIAN)
            try:
                for name, terrain in (("North", "mountains"), ("South", "coast")):
                    region = w.add_entity("region", name)
                    w.assert_fact(region, "terrain", value=terrain)
                    w.assert_fact(region, "population", value="50000")
                plans.append(plan_map(w, MapBrief(seed="fixed")))
            finally:
                w.close()
        assert plans[0].plan_id == plans[1].plan_id
        assert [f.id for f in plans[0].features] == [f.id for f in plans[1].features]

    def test_a_different_seed_gives_a_different_plan(self, prose: World):
        assert (plan_map(prose, MapBrief(seed="one")).plan_id
                != plan_map(prose, MapBrief(seed="two")).plan_id)

    def test_a_plan_round_trips_through_json(self, prose: World):
        plan = full(prose)
        again = MapPlan.from_dict(plan.to_dict())
        assert again.plan_id == plan.plan_id
        assert [f.id for f in again.features] == [f.id for f in plan.features]
        assert again.features[0].shapes == plan.features[0].shapes

    def test_a_plan_holds_together(self, prose: World):
        assert full(prose).violations() == []

    def test_every_feature_argues_for_itself(self, prose: World):
        for feature in full(prose).features:
            assert feature.because().endswith(".")
            assert feature.name in feature.because()


class TestApplying:
    def test_a_whole_map_is_one_undoable_action(self, prose: World):
        plan = full(prose)
        before = len(prose.geometries())
        report = apply_plan(prose, plan, DecisionSet.defaults(plan))
        assert report.action_id
        assert len(prose.geometries()) > before
        prose.undo()
        assert len(prose.geometries()) == before

    def test_applying_the_same_plan_twice_writes_nothing(self, prose: World):
        plan = full(prose)
        apply_plan(prose, plan, DecisionSet.defaults(plan))
        before = revisions(prose)
        again = apply_plan(prose, plan, DecisionSet.defaults(plan))
        assert revisions(prose) == before
        assert again.action_id is None
        assert again.counts.get("created", 0) == 0

    def test_re_planning_and_re_applying_does_not_duplicate(self, prose: World):
        first = full(prose)
        apply_plan(prose, first, DecisionSet.accept_all(first))
        drawn = len(prose.geometries())
        second = full(prose)
        apply_plan(prose, second, DecisionSet.accept_all(second))
        assert len(prose.geometries()) == drawn

    def test_inventing_a_place_is_opt_in(self, prose: World):
        """§66 in the type signature: siting the writer's own place is expected,
        inventing one is a suggestion."""
        plan = full(prose)
        invented = [f for f in plan.features if f.invented and f.kind == "settlement"]
        assert invented
        assert all(not f.default_accept for f in invented)
        report = apply_plan(prose, plan, DecisionSet.defaults(plan))
        assert report.counts.get("rejected", 0) >= len(invented)

    def test_accepting_everything_creates_the_invented_places(self, prose: World):
        plan = full(prose)
        report = apply_plan(prose, plan, DecisionSet.accept_all(plan))
        assert report.counts.get("created", 0) >= len(plan.features) - 1

    def test_a_partial_accept_can_be_finished_from_the_same_plan(self, prose: World):
        plan = full(prose)
        rivers = [f.id for f in plan.features if f.kind == "river"]
        assert rivers
        half = DecisionSet(plan_id=plan.plan_id, decisions=tuple(
            Decision(feature_id=f.id, accept=f.id in rivers) for f in plan.features))
        apply_plan(prose, plan, half)
        # the writer looks at it, then accepts the rest — from the plan they still hold
        rest = apply_plan(prose, plan, DecisionSet.accept_all(plan))
        assert rest.counts.get("created", 0) > 0
        assert rest.counts.get("unchanged", 0) >= len(rivers)


class TestDecisionsStick:
    def test_a_rejected_feature_stays_rejected(self, prose: World):
        plan = full(prose)
        victim = plan.by_kind("river")[0]
        apply_plan(prose, plan, DecisionSet(plan_id=plan.plan_id, decisions=(
            Decision(feature_id=victim.id, accept=False),)))
        assert load_standing(prose).get(victim.id).accept is False
        # and a fresh plan, applied with no instructions at all, still leaves it out
        again = plan_map(prose, MapBrief(invent_settlements=True))
        report = apply_plan(prose, again)
        rejected = {o.feature_id for o in report.outcomes if o.op == "rejected"}
        assert victim.id in rejected

    def test_a_rename_survives_a_re_plan(self, prose: World):
        plan = full(prose)
        river = plan.by_kind("river")[0]
        apply_plan(prose, plan, DecisionSet(plan_id=plan.plan_id, decisions=tuple(
            [Decision(feature_id=river.id, accept=True, name="The Kingswater")]
            + [Decision(feature_id=f.id, accept=f.default_accept)
               for f in plan.features if f.id != river.id])))
        named = [e for e in prose.entities("waterway") if e.name == "The Kingswater"]
        assert named, [e.name for e in prose.entities("waterway")]
        again = plan_map(prose, MapBrief(invent_settlements=True))
        apply_plan(prose, again)
        assert [e for e in prose.entities("waterway") if e.name == "The Kingswater"]

    def test_a_rename_of_the_writers_own_place_is_refused(self, prose: World):
        town = prose.add_entity("settlement", "Ashford")
        prose.assert_fact(town, "located_in", prose.entity_named("Greenhollow"))
        plan = full(prose)
        theirs = [f for f in plan.features
                  if f.subject and f.subject.mode == "existing"]
        assert theirs, "the writer's own settlement should be in the plan"
        assert all(not f.renameable for f in theirs)
        apply_plan(prose, plan, DecisionSet(plan_id=plan.plan_id, decisions=(
            Decision(feature_id=theirs[0].id, accept=True, name="Not Yours"),)))
        assert prose.entity_named("Ashford") is not None
        assert prose.entity_named("Not Yours") is None

    def test_pinning_takes_a_feature_out_of_the_generators_hands(self, prose: World):
        plan = full(prose)
        river = plan.by_kind("river")[0]
        apply_plan(prose, plan, DecisionSet.accept_all(plan))
        prose.remember(NAMESPACE, river.id,
                       Decision(feature_id=river.id, accept=True, pinned=True).as_dict())
        # pin it, then move the world under it and regenerate
        report = apply_plan(prose, plan)
        blocked = [o for o in report.outcomes if o.op == "blocked"]
        assert [o.feature_id for o in blocked] == [river.id]

    def test_rejecting_a_settlement_drops_the_road_that_needed_it(self, prose: World):
        plan = full(prose)
        roads = [f for f in plan.by_kind("road") if f.depends_on]
        if not roads:
            pytest.skip("this world's roads all join places the writer already made")
        needed = roads[0].depends_on[0]
        report = apply_plan(prose, plan, DecisionSet(plan_id=plan.plan_id, decisions=(
            Decision(feature_id=needed, accept=False),)))
        dropped = {o.feature_id: o.why for o in report.outcomes if o.op == "rejected"}
        assert roads[0].id in dropped
        assert "turned down" in dropped[roads[0].id]


class TestTheWritersWorkIsSafe:
    def test_authored_geometry_is_never_touched(self, prose: World):
        region = prose.entity_named("Greenhollow")
        mine = prose.add_geometry(region.id, "polygon",
                                  [[[10, 10], [90, 10], [90, 90], [10, 10]]],
                                  layer="regions")
        plan = full(prose)
        apply_plan(prose, plan, DecisionSet.accept_all(plan))
        assert prose.geometry_index().get(region.id)
        assert any(g.id == mine.id for g in prose.geometries())

    def test_a_generated_place_the_writer_adopted_is_promoted_not_deleted(
            self, prose: World):
        plan = full(prose)
        apply_plan(prose, plan, DecisionSet.accept_all(plan))
        river = [e for e in prose.entities("waterway")
                 if ledger_module.feature_of(e)][0]
        prose.update_entity(river.id, summary="Where Hallow drowned in the spring.")

        # Regenerate under a different seed: a different map, so last run's river is
        # not in it. Narrowing the brief would not do — asking for just settlements
        # must never sweep away the rivers it did not look at.
        other = plan_map(prose, MapBrief(seed="elsewhere", invent_settlements=True))
        report = apply_plan(prose, other)
        promoted = {o.feature_id for o in report.outcomes if o.op == "promoted"}
        assert promoted
        kept = prose.get_entity(river.id)
        assert kept is not None
        assert "generated-map" not in kept.tags

    def test_an_untouched_generated_place_is_retired_cleanly(self, prose: World):
        plan = full(prose)
        apply_plan(prose, plan, DecisionSet.accept_all(plan))
        rivers = [e.id for e in prose.entities("waterway")]
        assert rivers
        other = plan_map(prose, MapBrief(seed="elsewhere", invent_settlements=True))
        apply_plan(prose, other)
        gone = [rid for rid in rivers if prose.get_entity(rid) is None]
        assert gone, "a river nobody touched should not survive a different map"

    def test_narrowing_the_brief_does_not_retire_what_it_did_not_look_at(
            self, prose: World):
        """Asking for just the settlements is not a way to delete your rivers."""
        plan = full(prose)
        apply_plan(prose, plan, DecisionSet.accept_all(plan))
        rivers = [e.id for e in prose.entities("waterway")]
        thin = plan_map(prose, MapBrief(include=("settlement",)))
        apply_plan(prose, thin)
        assert all(prose.get_entity(rid) is not None for rid in rivers)


class TestThePlanStaysInsideItsOwnVocabulary:
    """Roles, layers and finding codes are closed sets, and a plan is checked against
    them before anything is written.

    Pinned here rather than left to `apply` because that is the wrong place to find out.
    Adding river reaches as separate shapes invented `reach0`, `reach1`, `reach2` as
    roles; every plan containing a river then failed to apply, and it surfaced as six
    unrelated-looking failures in tests about retiring and adopting places.
    """

    def test_every_shape_a_plan_proposes_has_a_known_role_and_layer(self, renn: World):
        from fw.core.mapgen.drafts import LAYERS, ROLES
        from fw.core.mapgen.pipeline import plan_map

        plan = plan_map(renn)
        assert plan.features, "nothing to check"
        for feature in plan.features:
            for shape in feature.shapes:
                assert shape.role in ROLES, (
                    f"{feature.kind} {feature.name!r} invented the role "
                    f"{shape.role!r}")
                assert shape.layer in LAYERS, (
                    f"{feature.kind} {feature.name!r} invented the layer "
                    f"{shape.layer!r}")

    def test_a_freshly_computed_plan_holds_together(self, renn: World):
        """`violations` is what `apply` refuses on, so it must be empty to begin with."""
        from fw.core.mapgen.pipeline import plan_map

        assert plan_map(renn).violations() == []


class TestRefusals:
    def test_answers_about_another_map_are_refused(self, prose: World):
        plan = full(prose)
        with pytest.raises(PlanStale, match="different map"):
            apply_plan(prose, plan, DecisionSet(plan_id="not-this-one"))

    def test_planning_on_a_what_if_says_why_it_cannot(self, prose: World):
        prose.create_branch("what if")
        fork = prose.on_branch("what if")
        plan = plan_map(fork)
        assert plan.features == ()
        assert plan.findings and plan.findings[0].code == "inherited-branch"

    def test_a_world_with_no_regions_says_what_to_do(self):
        empty = World.create(name="Nothing", calendar=GREGORIAN)
        try:
            plan = plan_map(empty)
            assert plan.features == ()
            assert "regions" in plan.findings[0].message
        finally:
            empty.close()

    def test_narrowing_the_brief_does_not_propose_what_it_dropped(self, prose: World):
        plan = plan_map(prose, MapBrief(include=("river",)))
        assert {f.kind for f in plan.features} == {"river"}


class TestCarryingAnswersForward:
    def test_answers_move_onto_a_fresh_plan(self, prose: World):
        plan = full(prose)
        answers = DecisionSet(plan_id=plan.plan_id, decisions=(
            Decision(feature_id=plan.features[0].id, accept=False),))
        moved, findings = carry(answers, plan)
        assert moved.get(plan.features[0].id).accept is False
        assert not findings

    def test_an_answer_about_something_gone_is_said_out_loud(self, prose: World):
        plan = full(prose)
        stale = DecisionSet(plan_id="old", decisions=(
            Decision(feature_id="riv_deadbeef", accept=False),))
        moved, findings = carry(stale, plan)
        assert moved.decisions == ()
        assert findings and "no longer" in findings[0].message


class TestWhatTheMapUnderstoodSurvives:
    """The semantics channel (V2 §2): `PlannedFeature.detail` used to die at accept.

    The generator knew a river's order and a range's strike and wrote none of it down,
    so the renderer of an accepted map was left guessing semantics back out of stroke
    widths. Now the curated detail dict is stored beside the provenance and is the one
    part of `props` that crosses the wire.
    """

    def test_what_the_map_understood_survives_acceptance(self, prose: World):
        from fw.core.mapgen import guards

        plan = full(prose)
        apply_plan(prose, plan, DecisionSet.defaults(plan))
        told = {f.id: f.detail for f in plan.features}
        stamped = 0
        for geometry in prose.geometries():
            marker = ledger_module.provenance(geometry)
            if not marker:
                continue
            stamped += 1
            want = told[marker["feature"]]
            assert (guards.canonical_json(ledger_module.semantics(geometry))
                    == guards.canonical_json(want))
        assert stamped > 0

    def test_a_map_from_before_the_generator_spoke_upgrades_in_place(self, prose: World):
        """The whole migration story: no schema change, no script — re-accepting the
        proposal rewrites any row whose stored understanding differs, and a world
        that never re-plans keeps working because every reader tolerates absence."""
        import json

        plan = full(prose)
        apply_plan(prose, plan, DecisionSet.defaults(plan))
        # Strip the semantics the way a pre-V2 file simply never had them.
        for row in prose.db.query("SELECT id, props FROM geometry"):
            props = json.loads(row["props"])
            props.pop("sem", None)
            prose.db.execute("UPDATE geometry SET props = ? WHERE id = ?",
                             (json.dumps(props), row["id"]))
        assert all(not ledger_module.semantics(g) for g in prose.geometries())

        again = apply_plan(prose, plan, DecisionSet.defaults(plan))
        assert again.counts.get("updated", 0) > 0
        assert any(ledger_module.semantics(g) for g in prose.geometries())

    def test_the_writers_own_shape_carries_none(self, prose: World):
        region = next(e for e in prose.entities() if e.type_key == "region")
        drawn = prose.add_geometry(region.id, "point", [10.0, 10.0], layer="regions")
        assert ledger_module.semantics(drawn) == {}

    def test_the_wire_carries_semantics_and_not_provenance(self, prose: World):
        from fastapi.testclient import TestClient

        from fw.api.app import create_app

        plan = full(prose)
        apply_plan(prose, plan, DecisionSet.defaults(plan))
        served = TestClient(create_app(prose)).get("/api/map").json()
        generated = [f for f in served["features"] if f["generated"]]
        assert generated
        assert any(f["semantics"] for f in generated)
        for feature in served["features"]:
            # The ledger's rule holds: what identifies a shape as the generator's to
            # replace is not paintable and not readable from a client.
            assert "props" not in feature
            assert "sig" not in feature.get("semantics", {})


class TestReDrawingIsNotReSaying:
    """Re-writing a feature used to delete its shapes and then re-assert its facts and
    re-lay its route segments beside the old ones — every re-accepted plan added
    another `located_in` and the router quietly counted each road twice."""

    def test_a_rewrite_does_not_duplicate_facts_or_segments(self, prose: World):
        import json

        plan = full(prose)
        apply_plan(prose, plan, DecisionSet.defaults(plan))
        facts = prose.db.one("SELECT COUNT(*) AS n FROM fact")["n"]
        segments = prose.db.one("SELECT COUNT(*) AS n FROM route_segment")["n"]

        # Force every feature down the rewrite path (stored semantics differ).
        for row in prose.db.query("SELECT id, props FROM geometry"):
            props = json.loads(row["props"])
            if props.pop("sem", None) is not None:
                prose.db.execute("UPDATE geometry SET props = ? WHERE id = ?",
                                 (json.dumps(props), row["id"]))
        again = apply_plan(prose, plan, DecisionSet.defaults(plan))
        assert again.counts.get("updated", 0) > 0
        assert prose.db.one("SELECT COUNT(*) AS n FROM fact")["n"] == facts
        assert (prose.db.one("SELECT COUNT(*) AS n FROM route_segment")["n"]
                == segments)

    def test_the_writers_facts_survive_a_rewrite(self, prose: World):
        """Only the feature's own stamped sayings go. A fact the writer asserted
        about a generated town is theirs, whatever redrew the town."""
        import json

        plan = full(prose)
        apply_plan(prose, plan, DecisionSet.defaults(plan))
        town = next(g.entity_id for g in prose.geometries()
                    if ledger_module.provenance(g))
        prose.assert_fact(town, "note", value="the miller's son was born here")
        for row in prose.db.query("SELECT id, props FROM geometry"):
            props = json.loads(row["props"])
            if props.pop("sem", None) is not None:
                prose.db.execute("UPDATE geometry SET props = ? WHERE id = ?",
                                 (json.dumps(props), row["id"]))
        apply_plan(prose, plan, DecisionSet.defaults(plan))
        assert any(f.value == "the miller's son was born here"
                   for f in prose.facts_about(town))


class TestABriefOwnsWhatItsGatesDrew:
    """Islands ride the coast gate and lanes need both parents, so `wants` could
    never retire either: an island the new map does not have stood forever."""

    def test_the_default_brief_covers_its_dependent_kinds(self):
        brief = MapBrief()
        assert not brief.wants("island") and brief.covers("island")
        assert not brief.wants("lane") and brief.covers("lane")

    def test_narrowing_still_protects_what_was_not_looked_at(self):
        settlements_only = MapBrief(include=("settlement",))
        assert not settlements_only.covers("island")
        assert not settlements_only.covers("lane")
        assert not settlements_only.covers("river")
        # Dropping the coast protects the lanes that land on it, exactly as the
        # drafting gate would have refused to draw them.
        no_coast = MapBrief(include=("road", "settlement"))
        assert not no_coast.covers("lane")


class TestAnIslandCanBeTakenBack:
    def test_a_redrawn_archipelago_leaves_no_orphan_islands(self):
        """End to end over the gate fix: islands are keyed by ordinal, so when a
        re-seeded coast has fewer of them the excess must be retired rather than
        standing forever — which is exactly what `wants`-gated retirement could
        never do, because no brief ever says "island"."""
        import corpus

        from fw.core.mapgen.ids import kind_of

        w = corpus.archipelago()
        try:
            crowded = plan_map(w, MapBrief(seed="reef"))     # 13 islands
            apply_plan(w, crowded, DecisionSet.defaults(crowded))
            sparse = plan_map(w, MapBrief(seed="five"))      # 5 islands
            retired_kinds = {kind_of(r.feature_id) for r in sparse.retiring}
            assert "island" in retired_kinds, (
                "eight islands left this map; retirement must offer them")
            apply_plan(w, sparse, DecisionSet.defaults(sparse))
            drawn = sum(1 for g in w.geometries()
                        if (ledger_module.provenance(g).get("feature") or "")
                        .startswith("isl_"))
            wanted = sum(1 for f in sparse.features if f.kind == "island")
            assert drawn == wanted
        finally:
            w.close()
