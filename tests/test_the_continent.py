"""The example world this application ships, and the two things that quietly broke it.

Both of these were found by looking at a render, not by any assertion, and neither
raised so much as a warning. That is the whole argument for testing a seed at all: a
world that builds without error can still describe somewhere that is not the place its
author meant.
"""

from __future__ import annotations

import pytest

from fw.core.mapgen.generate import MapGenerator
from fw.core.seed.nyren import PRESENT_YEAR, seed_nyren


@pytest.fixture(scope="module")
def continent():
    made = seed_nyren()
    yield made
    made.close()


class TestTheGroundTheGeneratorIsGiven:
    def test_every_country_reaches_the_generator_as_a_claim(self, continent):
        """A region and the realm named after it are two entities, and the seed's own
        index collided them: `built |= history.build(...)` let the realm Merran
        overwrite the region Merran, so `draw` hung the coast's outline on the crown.
        Nothing failed. Two of eight countries simply grew from the arbitrary point the
        layout had dropped them on, four hundred units from where they belong.
        """
        generator = MapGenerator(continent, seed="test")
        generator.read_the_world()
        authored = generator._authored_outlines()
        missing = sorted(profile.name for key, profile in generator.profiles.items()
                         if key not in authored)
        assert not missing, f"no authored ring reached the generator for {missing}"

    def test_the_homeland_is_not_drawn_on_a_map_of_the_continent(self, continent):
        """`coast._build_landmass` shapes ONE continent — its first line says so — so a
        second landmass across a strait is outside the model. Given one, it filled the
        gap: the Northern Sea stopped existing and the crossing the whole history turns
        on became a land bridge. Nyreland is a place in this world and not a country on
        this map, and it has to stay that way while the coast model is what it is.
        """
        nyreland = continent.entity_named("Nyreland")
        assert nyreland is not None, "Nyreland has left the world, not just the map"
        assert nyreland.type_key != "region", (
            "Nyreland is a region again, so the generator will grow territory for it "
            "and bridge the Northern Sea")
        drawn = [g for g in continent.geometries() if g.entity_id == nyreland.id]
        assert not drawn, "Nyreland is being drawn again"


class TestWhatTheBriefAsksFor:
    def test_the_peoples_outlast_the_countries_that_lost(self, continent):
        """A people is not a country. The Carthi lost politically without disappearing,
        and if the model cannot say that, the setting's central idea is not in it."""
        carthi = continent.entity_named("The Carthi")
        assert carthi is not None and carthi.type_key == "culture"
        lives = {f.object_id for f in continent.facts_where(subject_id=carthi.id)
                 if f.predicate_key == "active_in"}
        assert len(lives) >= 3, "the Carthi survive in one place only"
        nyren = continent.entity_named("Nyren")
        basin = continent.entity_named("The Carth Basin")
        holds = [f for f in continent.facts_where(object_id=basin.id, at=continent.day(PRESENT_YEAR))
                 if f.predicate_key == "legally_owns" and f.subject_id == nyren.id]
        assert holds, "the Carthi homeland is not held by Nyren, which is the point"

    def test_the_river_reaches_the_sea_through_the_country_that_did_not_fall(
            self, continent):
        """Everything else is downstream of this. Merran is rich because the Carth's
        mouth is in Merran, and Nyren wants Orra because of the same sentence."""
        carth = continent.entity_named("The Carth")
        through = {continent.get_entity(f.object_id).name
                   for f in continent.facts_where(subject_id=carth.id)
                   if f.predicate_key == "flows_through"}
        assert "Orra" in through
        merran = continent.entity_named("Merran")
        orra = continent.entity_named("Orra")
        owns = [f for f in continent.facts_where(object_id=orra.id, at=continent.day(PRESENT_YEAR))
                if f.predicate_key == "legally_owns" and f.subject_id == merran.id]
        claims = [f for f in continent.facts_where(object_id=orra.id, at=continent.day(PRESENT_YEAR))
                  if f.predicate_key == "claims"]
        assert owns and claims, "Orra is not contested, so the setting has no engine"

    def test_the_example_meets_the_floor_the_brief_sets(self, continent):
        counts = continent.counts_by_type()
        assert continent.count_entities() >= 35              # §115's floor
        for kind in ("realm", "region", "settlement", "person", "culture", "language",
                     "dynasty", "house", "clan", "road", "waterway", "trade_route",
                     "resource"):
            assert counts.get(kind), f"the example world has no {kind}"
        assert len(continent.scenes()) >= 2
        assert len(continent.events()) >= 10
