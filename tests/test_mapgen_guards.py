"""The rules that keep the same world generating the same map, enforced rather than meant.

A golden coordinate test is worthless if the generator can drift, and a writer who
regenerates and gets a different continent has been lied to about what "generate" means.
Determinism is not kept by intending it; it is kept by never doing four specific things,
and these tests are what stops any of them being done again by accident.
"""

from __future__ import annotations

import ast
import math
import pathlib
import subprocess
import sys

import pytest

from fw.core.mapgen import guards, ids, noise
from fw.core.mapgen.findings import Finding, ordered, warn
from fw.core.world import World

MAPGEN = pathlib.Path(__file__).resolve().parent.parent / "fw" / "core" / "mapgen"

# Correctly rounded by IEEE 754 on every platform, so these are safe.
ALLOWED_MATH = {"hypot", "dist", "sqrt", "floor", "ceil", "fabs", "isqrt",
                "isfinite", "isnan", "isinf", "inf", "nan", "copysign", "fmod",
                "trunc", "prod", "comb"}


def modules() -> list[pathlib.Path]:
    return sorted(p for p in MAPGEN.glob("**/*.py") if p.name != "__init__.py")


class TestNoPlatformMath:
    """`math.sin`, `cos`, `exp` and `x ** 0.5` go through the platform's libm, which is
    not required to be correctly rounded and demonstrably differs between builds. One
    differing last bit moves a coastline by a cell, which breaks a golden file and the
    promise it stands for. `sqrt`, `hypot` and `dist` are exempt: IEEE 754 requires
    those to be correctly rounded."""

    def test_no_module_calls_a_transcendental(self):
        offences: list[str] = []
        for path in modules():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "math"
                        and node.attr not in ALLOWED_MATH):
                    offences.append(f"{path.name}:{node.lineno} math.{node.attr}")
        assert not offences, "libm is not reproducible across platforms: " + \
                             ", ".join(offences)

    def test_no_module_raises_to_a_fractional_power(self):
        offences: list[str] = []
        for path in modules():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
                    exponent = node.right
                    integral = (isinstance(exponent, ast.Constant)
                                and isinstance(exponent.value, int))
                    if not integral:
                        offences.append(f"{path.name}:{node.lineno}")
        assert not offences, "x ** 0.5 is a libm call; use math.sqrt: " + \
                             ", ".join(offences)

    def test_the_direction_table_replaces_sin_and_cos(self):
        assert len(noise.DIRECTIONS) == 32
        assert all(abs(math.hypot(x, y) - 1.0) < 1e-12 for x, y in noise.DIRECTIONS)
        assert noise.direction(0) == (1.0, 0.0)
        assert noise.direction(32) == noise.direction(0)      # wraps

    def test_rcp_exp_is_close_enough_to_be_a_stand_in(self):
        worst = max(abs(guards.rcp_exp(x / 100.0) - math.exp(-x / 100.0))
                    for x in range(0, 801))
        assert worst < 1e-6, f"rcp_exp drifts by {worst:.2e}"
        assert guards.rcp_exp(0.0) == 1.0
        assert all(guards.rcp_exp(x / 50.0) >= guards.rcp_exp((x + 1) / 50.0)
                   for x in range(400))


class TestNoStringSetIteration:
    def test_a_set_of_string_tuples_really_does_reorder(self):
        """The reason the rule exists. If this ever stops being true the rule can go —
        until then, a generator that iterates one is a generator that draws two
        different maps on two machines."""
        script = ("s = {('a','b'), ('c','d'), ('e','f'), ('g','h'), ('i','j')};"
                  "print(list(s))")
        seen = {subprocess.run([sys.executable, "-c", script], capture_output=True,
                               text=True, env={"PYTHONHASHSEED": str(n), "PATH": ""}
                               ).stdout.strip()
                for n in (1, 2, 3, 4, 5, 6, 7, 8)}
        assert len(seen) > 1, "string set ordering looks stable; verify before relaxing"

    def test_a_set_of_int_tuples_does_not_reorder(self):
        """Which is what makes the string case dangerous: it passes on one machine."""
        script = "s = {(1,2),(3,4),(5,6),(7,8),(9,10)}; print(list(s))"
        seen = {subprocess.run([sys.executable, "-c", script], capture_output=True,
                               text=True, env={"PYTHONHASHSEED": str(n), "PATH": ""}
                               ).stdout.strip()
                for n in (1, 2, 3, 4, 5)}
        assert len(seen) == 1

    def test_stable_orders_a_set_the_same_way_every_time(self):
        pairs = {("b", "a"), ("a", "z"), ("a", "b")}
        assert guards.stable(pairs) == [("a", "b"), ("a", "z"), ("b", "a")]


class TestCanonicalJson:
    def test_key_order_and_float_noise_do_not_change_the_bytes(self):
        left = {"b": 1, "a": [0.1 + 0.2, 1.0]}
        right = {"a": [0.30000000000000004, 1.0], "b": 1}
        assert guards.canonical_json(left) == guards.canonical_json(right)

    def test_negative_zero_is_zero(self):
        assert guards.canonical_json([-0.0]) == guards.canonical_json([0.0])

    def test_a_set_gets_an_order_rather_than_the_hash_seed_s(self):
        assert (guards.canonical_json({"x": {"b", "a"}})
                == guards.canonical_json({"x": {"a", "b"}}))


class TestFactsAreReadInAStatedOrder:
    def test_no_module_reads_facts_without_sorting_them(self):
        """SQLite returns rows in whatever order it finds them, and that order changes
        as the file is edited. Reading facts raw makes a map that changes when the
        writer edits something unrelated."""
        offences: list[str] = []
        for path in modules():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("facts_where", "facts_about")):
                    continue
                if not _wrapped_in_sorted_facts(tree, node):
                    offences.append(f"{path.name}:{node.lineno}")
        assert not offences, ("facts must be read through guards.sorted_facts: "
                              + ", ".join(offences))


def _wrapped_in_sorted_facts(tree: ast.AST, target: ast.Call) -> bool:
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "sorted_facts"
                and any(child is target for child in ast.walk(node))):
            return True
    return False


class TestIdentity:
    def test_a_feature_id_depends_on_what_it_is_and_nothing_else(self):
        assert ids.feature_id("river", 4, 9) == ids.feature_id("river", 4, 9)
        assert ids.feature_id("river", 4, 9) != ids.feature_id("river", 9, 4)
        assert ids.feature_id("lake", 4, 9) != ids.feature_id("river", 4, 9)

    def test_an_unknown_kind_is_refused_rather_than_hashed(self):
        with pytest.raises(ValueError):
            ids.feature_id("banana", 1)

    def test_a_shape_that_moved_a_hundredth_of_a_unit_is_the_same_shape(self):
        assert (ids.shape_signature([[1.04, 2.0], [3.0, 4.0]])
                == ids.shape_signature([[1.0, 2.0], [3.0, 4.0]]))
        assert (ids.shape_signature([[1.4, 2.0]])
                != ids.shape_signature([[1.0, 2.0]]))

    def test_a_name_key_is_readable_and_order_independent(self):
        assert (ids.name_key("river", ("b", "a"), 3)
                == ids.name_key("river", ("a", "b"), 3) == "river|a|b|03")


class TestFindings:
    def test_a_finding_refuses_a_code_nobody_displays(self):
        with pytest.raises(ValueError):
            Finding(code="oops", severity="note", message="x")
        with pytest.raises(ValueError):
            Finding(code="scale", severity="fatal", message="x")

    def test_findings_come_back_worst_first_in_a_stable_order(self):
        got = ordered([Finding("scale", "note", "b"), warn("adjacency", "a"),
                       Finding("scale", "note", "a")])
        assert [f.message for f in got] == ["a", "a", "b"]
        assert got[0].severity == "warning"


class TestRememberedDecisions:
    def test_a_decision_round_trips(self, world: World):
        world.remember("mapgen", "riv_abc", {"accepted": False})
        assert world.recall("mapgen", "riv_abc") == {"accepted": False}
        assert world.recall("mapgen", "nothing") is None

    def test_decisions_come_back_in_key_order(self, world: World):
        for key in ("c", "a", "b"):
            world.remember("mapgen", key, key)
        assert list(world.recall_all("mapgen")) == ["a", "b", "c"]

    def test_a_decision_undoes_like_anything_else(self, world: World):
        world.remember("mapgen", "riv_abc", {"accepted": False})
        world.remember("mapgen", "riv_abc", {"accepted": True})
        world.undo()
        assert world.recall("mapgen", "riv_abc") == {"accepted": False}
        world.undo()
        assert world.recall("mapgen", "riv_abc") is None

    def test_forgetting_undoes_too(self, world: World):
        world.remember("mapgen", "k", 1)
        world.forget("mapgen", "k")
        assert world.recall("mapgen", "k") is None
        world.undo()
        assert world.recall("mapgen", "k") == 1

    def test_forgetting_what_was_never_remembered_is_quiet(self, world: World):
        world.forget("mapgen", "never")

    def test_a_what_if_decides_for_itself(self, world: World):
        world.remember("mapgen", "riv_abc", "canon says yes")
        world.create_branch("what if")
        fork = world.on_branch("what if")
        assert fork.recall("mapgen", "riv_abc") is None
        fork.remember("mapgen", "riv_abc", "the what-if says no")
        assert world.recall("mapgen", "riv_abc") == "canon says yes"
        assert fork.recall("mapgen", "riv_abc") == "the what-if says no"


# Where the world is allowed to be touched: the reader that turns it into a
# `WorldReading`, the two modules that write a map back, the orchestrator, the ledger
# that reads provenance and the store of remembered decisions. Everything else is a
# stage, and a stage that reads the world is a stage whose output depends on when it
# ran and what else had been written by then — which is exactly what made the same
# world generate a different map on the second press.
MAY_TOUCH_THE_WORLD = {
    "read.py",        # source/: the one reading, which is the point of the rule
    "generate.py", "apply.py", "pipeline.py", "decide.py", "ledger.py",
}


class TestOnlyOneReadingOfTheWorld:
    """C2's rule, enforced rather than meant.

    The generator used to read the world six times per plan — once per region for the
    profiles, again for authored outlines, again for the writer's own settlements,
    again for holdings and features and capitals, again in the namer, again in the
    ledger — and no two of those readings were guaranteed to agree with each other.
    """

    def test_no_stage_imports_the_world(self):
        offences: list[str] = []
        for path in modules():
            if path.name in MAY_TOUCH_THE_WORLD:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == "fw.core.world":
                    offences.append(f"{path.name}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    offences.extend(f"{path.name}:{node.lineno}" for a in node.names
                                    if a.name.startswith("fw.core.world"))
        assert not offences, ("a stage reads the world instead of the reading: "
                             + ", ".join(offences))

    def test_no_stage_calls_a_method_on_a_world(self):
        """Catches the duck-typed route the import check cannot see."""
        offences: list[str] = []
        for path in modules():
            if path.name in MAY_TOUCH_THE_WORLD:
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "world"):
                    offences.append(f"{path.name}:{node.lineno} "
                                    f"world.{node.func.attr}()")
        assert not offences, ", ".join(offences)

    def test_a_plan_reads_the_world_once(self):
        """Not "few times". Once — and the count is the assertion."""
        from fw.core.mapgen import source
        from fw.core.mapgen.pipeline import plan_map
        from fw.core.seed.renn import seed_renn

        world = seed_renn()
        try:
            calls: list[int] = []
            real = source.read_world

            def counted(*args, **kw):
                calls.append(1)
                return real(*args, **kw)

            source.read_world = counted
            try:
                import fw.core.mapgen.generate as generate_module
                generate_module.source.read_world = counted
                plan_map(world)
            finally:
                source.read_world = real
                generate_module.source.read_world = real
            assert len(calls) == 1, f"the world was read {len(calls)} times"
        finally:
            world.close()

    def test_the_plan_records_which_world_it_was_read_from(self):
        """`reading_fingerprint` was declared when the plan was and always empty."""
        from fw.core.mapgen.pipeline import plan_map
        from fw.core.seed.renn import seed_renn

        world = seed_renn()
        try:
            plan = plan_map(world)
            assert len(plan.reading_fingerprint) == 32
        finally:
            world.close()
