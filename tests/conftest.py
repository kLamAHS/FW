"""Shared fixtures."""

from __future__ import annotations

import pytest

from fw.core.calendar.kernel import Calendar, Era, Month, Season
from fw.core.world import World

# The world's own calendar. Nothing about it matches Earth, which is the point: if the
# engines only work on a 12-month, 365-day year then §60's promise is not being kept.
RENNISH = Calendar(
    name="Rennish",
    months=(
        Month("Frostwane", 61), Month("Seedfall", 73), Month("Highsun", 80),
        Month("Harvestide", 73), Month("Darkening", 68),
    ),
    weekdays=("Kingsday", "Mareday", "Orrenday", "Veyneday", "Marrday",
              "Fordday", "Restday", "Hallow", "Emberday", "Lastday"),
    leap_every=4,
    eras=(Era("Age of Founding", "AF", 1, 199), Era("Age of Kings", "AK", 200)),
    seasons=(Season("Deepwinter", 1), Season("Greening", 62), Season("Highsummer", 135),
             Season("Harvest", 215), Season("Fading", 288)),
)


@pytest.fixture
def world() -> World:
    """An empty world with the Rennish calendar."""
    w = World.create(name="Test world", calendar=RENNISH)
    yield w
    w.close()


@pytest.fixture
def renn() -> World:
    """The seeded example world of §115. Shared by most integration tests."""
    from fw.core.seed.renn import seed_renn

    w = seed_renn()
    yield w
    w.close()
