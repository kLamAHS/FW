"""Every country has its name at the zoom the map opens in.

The art direction asks for a zero tier-0 drop rate at the world band, and nothing
checked it — because the only report available, `unlabelled`, cannot see this failure.
A name the world band loses but the regional band places is not "unlabelled"; it is
simply absent from the view the reader starts in, and the panel stays silent. Measured
before this guard existed, ten of `empire`'s twenty-four marches had no name at the
opening view and the suite had been green over it for the whole programme.

So the guard reads the world band directly and asks what is missing from it, rather than
asking what the map admits to having dropped.
"""

from __future__ import annotations

import goldenlib
import pytest

# Names that belong to the ground rather than to a dot: a country, a sea, a landmass,
# a range. A settlement's name is budgeted differently and legitimately thins out.
AREA_KINDS = ("region", "sea", "landmass", "range")


def named_at(draw: dict, band: str) -> set[str]:
    return {n["text"] for n in draw["labels"].get(band, [])
            if n.get("tier") == 0 and n.get("kind") in AREA_KINDS}


@pytest.mark.parametrize("name", goldenlib.DRAWPLAN_WORLDS)
def test_every_country_is_named_at_the_world_band(name: str):
    draw = goldenlib.map_payload(name)["draw"]
    world = named_at(draw, "world")
    deeper = (named_at(draw, "regional") | named_at(draw, "local")) - world
    silent = {u["text"] for u in draw.get("unlabelled", ())
              if u.get("tier") == 0 and u.get("kind") in AREA_KINDS}
    missing = sorted(deeper | silent)
    assert not missing, (
        f"the {name} map opens without naming {len(missing)} of its own countries: "
        f"{', '.join(missing)} — {len(world)} tier-0 area names are on the world band. "
        "A name the reader must zoom in to find is not on the map they were given.")


class TestASpineFollowsTheShapesLongAxis:
    """The bug under most of the above, and a plain rectangle shows it.

    `_ridge` advanced one lattice COLUMN at a time, so a shape whose long axis ran north
    to south was measured across its width — and a name is sized against its spine, so
    such a country could not be labelled at all. On the map it cost The Merran Coast, a
    coastal strip 109 units wide and 260 tall, which reported a 51-unit spine for a
    231-unit name. No fixture is needed to catch it and none can drift.
    """

    @staticmethod
    def box(wide: float, tall: float):
        return [(400 - wide / 2, 400 - tall / 2), (400 + wide / 2, 400 - tall / 2),
                (400 + wide / 2, 400 + tall / 2), (400 - wide / 2, 400 + tall / 2)]

    @pytest.mark.parametrize("long_side,short_side", [(300, 100), (260, 120)])
    def test_a_rectangle_and_the_same_rectangle_turned_have_the_same_spine(
            self, long_side, short_side):
        from fw.core.mapgen import labels

        lying = labels.spine(self.box(long_side, short_side))
        standing = labels.spine(self.box(short_side, long_side))
        across = labels._length(lying)
        down = labels._length(standing)
        assert across > short_side, (
            f"a {long_side}x{short_side} rectangle spines at {across:.0f}, "
            "which is not along its length")
        assert abs(across - down) < 1.0, (
            f"the same rectangle spines at {across:.0f} lying down and {down:.0f} "
            "standing up; the walk is following the lattice, not the shape")
