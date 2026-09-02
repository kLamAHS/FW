"""The composition and the ground, pinned (V2 §45, §51).

Every corpus world's DrawPlan — labels, icons, legend, what would not fit — is
compared against a committed snapshot, and the relief render's pixels against a
committed hash. A failure here is not necessarily a bug: it is the suite saying the
picture changed, and the answer is either to fix the regression or to run
`scripts/regenerate_goldens.py` and put the diff in front of a person.

The snapshots are normalised (ids projected out) so two builds of the same world
compare equal; everything that remains is deterministic by the twin-world tests.
"""

from __future__ import annotations

import goldenlib
import pytest


@pytest.mark.parametrize("name", goldenlib.DRAWPLAN_WORLDS)
def test_the_composition_is_what_it_was(name: str):
    want = goldenlib.read_golden("drawplan", name)
    if want is None:
        pytest.skip(f"no golden for {name!r} — run scripts/regenerate_goldens.py")
    got = goldenlib.drawplan_of(name)
    assert got == want, (
        f"the {name} map composes differently now — if that is intended, run "
        f"scripts/regenerate_goldens.py and review the diff")


@pytest.mark.parametrize("name", goldenlib.RELIEF_WORLDS)
def test_the_ground_is_what_it_was(name: str):
    want = goldenlib.read_golden("relief", name)
    if want is None:
        pytest.skip(f"no golden for {name!r} — run scripts/regenerate_goldens.py")
    world = goldenlib.accepted(name)
    try:
        got = goldenlib.relief_signature(world)
    finally:
        world.close()
    assert got == want["signature"], (
        f"the {name} relief renders differently now — if that is intended, run "
        f"scripts/regenerate_goldens.py and look at the picture before pinning it")
