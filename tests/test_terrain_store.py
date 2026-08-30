"""Keeping the ground, and getting it back.

The surface a map was drawn from is the one thing the generator produces that is not
geometry, and it is what the relief is lit from. These are the properties that make
storing it worth doing rather than recomputing it: that it survives the round trip
closely enough to light, that it is small enough to live in a world file a writer keeps
for years, and that it is what they accepted rather than whatever the generator would
say today.
"""

from __future__ import annotations

import pytest

from fw.core.mapgen.apply import apply_plan
from fw.core.mapgen.pipeline import plan_map
from fw.core.store.fields import pack_fields, unpack_fields


class TestThePackedFields:
    def test_a_field_survives_the_round_trip(self):
        size = 24
        fields = {
            "elevation": [[(i * 7 + j * 3) % 41 / 41.0 - 0.4 for i in range(size)]
                          for j in range(size)],
            "canopy": [[((i + j) % 11) / 11.0 for i in range(size)] for j in range(size)],
        }
        back = unpack_fields(size, pack_fields(size, fields))
        assert sorted(back) == sorted(fields)
        worst = max(abs(back[name][j][i] - fields[name][j][i])
                    for name in fields for j in range(size) for i in range(size))
        assert worst < 1e-4, f"quantisation lost {worst}"

    def test_a_flat_field_survives(self):
        """Zero range is the case a naive scaling divides by."""
        size = 8
        flat = {"still": [[0.375] * size for _ in range(size)]}
        back = unpack_fields(size, pack_fields(size, flat))
        assert all(value == 0.375 for row in back["still"] for value in row)

    def test_it_is_small_enough_to_keep(self):
        size = 144
        fields = {name: [[(i * j) % 97 / 97.0 for i in range(size)]
                         for j in range(size)]
                  for name in ("elevation", "canopy", "marsh")}
        assert len(pack_fields(size, fields)) < 200_000

    def test_a_blob_from_somewhere_else_is_refused(self):
        with pytest.raises(ValueError):
            unpack_fields(8, b"not a field blob at all")

    def test_a_field_of_the_wrong_size_is_refused(self):
        blob = pack_fields(8, {"a": [[0.0] * 8 for _ in range(8)]})
        with pytest.raises(ValueError):
            unpack_fields(16, blob)


class TestTheGroundIsKept:
    def test_accepting_a_map_keeps_the_surface_it_was_drawn_from(self, renn):
        assert renn.terrain() is None, "there is ground before a map was accepted"
        plan = plan_map(renn)
        assert plan.terrain is not None, "the plan carries no surface"
        apply_plan(renn, plan)

        kept = renn.terrain()
        assert kept is not None
        assert sorted(kept["fields"]) == ["canopy", "elevation", "flow", "marsh",
                                          "shoreline"]
        assert kept["size"] == plan.terrain.size
        assert kept["seed"] == plan.terrain.seed
        for name, field in kept["fields"].items():
            assert len(field) == kept["size"]
            original = plan.terrain.fields[name]
            worst = max(abs(field[j][i] - original[j][i])
                        for j in range(0, kept["size"], 7)
                        for i in range(0, kept["size"], 7))
            assert worst < 1e-3, f"{name} came back changed by {worst}"

    def test_the_surface_is_not_part_of_the_plan_the_writer_is_holding(self, renn):
        """It is a consequence of the brief, not part of the proposal.

        Two plans that propose the same map are the same plan whatever surface each was
        computed on — which is what lets a writer accept half a plan and finish it later
        from the copy they are still holding.
        """
        plan = plan_map(renn)
        assert "terrain" not in plan.to_dict()
        assert len(str(plan.to_dict())) < 4_000_000

    def test_accepting_a_second_map_replaces_the_ground(self, renn):
        apply_plan(renn, plan_map(renn))
        first = renn.terrain()
        apply_plan(renn, plan_map(renn, None))
        second = renn.terrain()
        assert second is not None and first is not None
        rows = renn.db.query("SELECT id FROM terrain")
        assert len(rows) == 1, "a second map left a second surface behind"
