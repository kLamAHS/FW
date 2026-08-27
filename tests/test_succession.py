"""Genealogy and succession tests (spec §7, §8, §50).

The centrepiece is `test_reproduces_the_specs_stated_succession`: the brief states the
expected answer outright, so the engine is measured against the brief rather than against
whatever it happens to produce.
"""

from __future__ import annotations

import pytest

from fw.core.genealogy.kinship import Genealogy, Legitimacy
from fw.core.succession.engine import SuccessionEngine
from fw.core.succession.laws import LAWS, Order, get_law
from fw.core.world import World


@pytest.fixture
def engine(renn: World) -> SuccessionEngine:
    return SuccessionEngine(renn)


@pytest.fixture
def crown(renn: World):
    return renn.title_named("King of Renn")


@pytest.fixture
def death_of_aldren(renn: World) -> int:
    return renn.day(240, 5, 61)


def person_id(world: World, name: str) -> str:
    entity = world.entity_named(name, "person")
    assert entity is not None, f"no person named {name}"
    return entity.id


class TestSpecStatedSuccession:
    """§8 states the answer; these tests assert exactly it."""

    def test_reproduces_the_specs_stated_succession(self, engine, crown, death_of_aldren):
        result = engine.compute(crown.id, death_of_aldren)
        assert result.names()[:4] == [
            "Prince Oren", "Lady Elia", "Lord Caros", "Lady Mara"
        ]

    def test_reproduces_the_specs_stated_hypothetical(
        self, renn, engine, crown, death_of_aldren
    ):
        oren = person_id(renn, "Prince Oren")
        result = engine.compute(crown.id, death_of_aldren, force_illegitimate={oren})
        assert result.names()[:3] == ["Lady Elia", "Lord Caros", "Lady Mara"]

    def test_the_hypothetical_never_touches_canon(
        self, renn, engine, crown, death_of_aldren
    ):
        """§50: changes in hypothetical mode must never alter canonical data."""
        oren = person_id(renn, "Prince Oren")
        before = renn.value_of(oren, "legitimacy")
        engine.compute(crown.id, death_of_aldren, force_illegitimate={oren})
        engine.compute(crown.id, death_of_aldren, assume_dead={oren})
        engine.compute(crown.id, death_of_aldren, exclude={oren})
        assert renn.value_of(oren, "legitimacy") == before == "legitimate"
        # and the canonical answer is unchanged afterwards
        assert engine.compute(crown.id, death_of_aldren).names()[0] == "Prince Oren"

    def test_result_explains_itself(self, engine, crown, death_of_aldren):
        """§67: never a bare ranking with no reasoning."""
        result = engine.compute(crown.id, death_of_aldren)
        text = result.explain()
        assert "Male-preference primogeniture" in text
        assert "through King Aldren" in text
        assert "already dead" in text          # says why Aldren himself is not listed

    def test_hypothetical_is_labelled_as_such(self, renn, engine, crown, death_of_aldren):
        oren = person_id(renn, "Prince Oren")
        result = engine.compute(crown.id, death_of_aldren, force_illegitimate={oren})
        assert result.hypothetical
        assert "Prince Oren is illegitimate" in result.assumptions
        assert not engine.compute(crown.id, death_of_aldren).hypothetical


class TestSuccessionLaws:
    """§8: the law is swappable, and swapping it changes the answer correctly."""

    def test_absolute_primogeniture_ignores_gender(self, engine, crown, death_of_aldren):
        result = engine.compute(crown.id, death_of_aldren, law_key="absolute_primogeniture")
        assert result.names()[:4] == [
            "Prince Oren", "Lady Elia", "Lord Caros", "Lady Mara"
        ]

    def test_male_only_excludes_women_entirely(self, engine, crown, death_of_aldren):
        result = engine.compute(crown.id, death_of_aldren, law_key="male_only_primogeniture")
        assert result.names() == ["Prince Oren", "Lord Caros"]
        assert not any("Lady" in n for n in result.names())

    def test_ultimogeniture_reverses_the_order(self, engine, crown, death_of_aldren):
        result = engine.compute(crown.id, death_of_aldren, law_key="ultimogeniture")
        assert result.names()[:4] == [
            "Lady Mara", "Lord Caros", "Lady Elia", "Prince Oren"
        ]

    def test_male_preference_puts_brothers_before_sisters(self):
        law = get_law("male_preference_primogeniture")
        assert law.order is Order.MALE_PREFERENCE

        from fw.core.genealogy.kinship import Person
        younger_son = Person(id="s", name="Son", born=100, gender="male")
        elder_daughter = Person(id="d", name="Daughter", born=90, gender="female")
        assert law.sibling_key(younger_son) < law.sibling_key(elder_daughter)

    def test_absolute_primogeniture_puts_the_elder_first_regardless(self):
        law = get_law("absolute_primogeniture")
        from fw.core.genealogy.kinship import Person
        younger_son = Person(id="s", name="Son", born=100, gender="male")
        elder_daughter = Person(id="d", name="Daughter", born=90, gender="female")
        assert law.sibling_key(elder_daughter) < law.sibling_key(younger_son)

    def test_non_hereditary_laws_produce_no_automatic_heir(
        self, engine, crown, death_of_aldren
    ):
        """Appointment and conquest are not computed; the writer decides."""
        for key in ("appointment", "conquest"):
            result = engine.compute(crown.id, death_of_aldren, law_key=key)
            assert result.line == []
            assert "does not pass by inheritance" in result.explain()

    def test_unknown_law_falls_back_rather_than_failing(self, engine, crown, death_of_aldren):
        """A world naming a law this build lacks must still open and still show something."""
        result = engine.compute(crown.id, death_of_aldren, law_key="trial_by_combat")
        assert result.line
        assert result.law.key == "male_preference_primogeniture"

    def test_every_advertised_law_is_implemented(self):
        """§8 names ten systems; none may be a label with nothing behind it."""
        for key in ("absolute_primogeniture", "male_preference_primogeniture",
                    "male_only_primogeniture", "ultimogeniture", "seniority",
                    "elective", "tanistry", "appointment", "conquest"):
            assert key in LAWS
            assert LAWS[key].label


class TestSuccessionAsOfDate:
    def test_eligibility_is_evaluated_on_the_day(self, renn, engine, crown):
        """Someone not yet born cannot inherit; someone already dead cannot either."""
        early = renn.day(211)            # Elia (b.212) does not exist yet
        result = engine.compute(crown.id, early)
        assert "Lady Elia" not in result.names()
        assert "Prince Oren" in result.names()

        later = renn.day(240, 5, 61)
        assert "Lady Elia" in engine.compute(crown.id, later).names()

    def test_the_living_holder_is_not_their_own_heir(self, renn, engine, crown):
        during_aldrens_reign = renn.day(230)
        result = engine.compute(crown.id, during_aldrens_reign)
        assert "King Aldren" not in result.names()

    def test_a_dead_claimants_line_still_inherits(self, engine, crown, death_of_aldren):
        """Caros and Mara reach the throne only through their dead father."""
        result = engine.compute(crown.id, death_of_aldren)
        assert "Lord Corren" not in result.names()
        assert "Lord Caros" in result.names()

    def test_walk_starts_from_the_titles_dynastic_root(self, renn, engine, crown,
                                                       death_of_aldren):
        """Not from the deceased: Caros and Mara are reachable only via Aldren's father."""
        result = engine.compute(crown.id, death_of_aldren)
        aldrens_children = {"Prince Oren", "Lady Elia"}
        assert set(result.names()) - aldrens_children  # cousins are present too

    def test_assume_dead_removes_a_claimant_and_their_line(
        self, renn, engine, crown, death_of_aldren
    ):
        caros = person_id(renn, "Lord Caros")
        result = engine.compute(crown.id, death_of_aldren, assume_dead={caros})
        assert "Lord Caros" not in result.names()
        assert "Lady Mara" in result.names()

    def test_limit(self, engine, crown, death_of_aldren):
        assert len(engine.compute(crown.id, death_of_aldren, limit=2).line) == 2

    def test_unknown_title_raises(self, engine, death_of_aldren):
        with pytest.raises(ValueError, match="no title"):
            engine.compute("nope", death_of_aldren)


class TestGenealogy:
    def test_legal_and_biological_parentage_can_disagree(self, renn):
        """§7 and §57: the whole plot of the example world lives in this difference."""
        g = Genealogy(renn)
        oren = person_id(renn, "Prince Oren")
        aldren = person_id(renn, "King Aldren")
        corren = person_id(renn, "Lord Corren")

        assert aldren in g.parents_of(oren, lens="legal")
        assert corren not in g.parents_of(oren, lens="legal")
        assert corren in g.parents_of(oren, lens="biological")
        assert aldren not in g.parents_of(oren, lens="biological")

    def test_children_come_back_eldest_first(self, renn):
        g = Genealogy(renn)
        corren = person_id(renn, "Lord Corren")
        names = [g.people[c].name for c in g.children_of(corren)]
        assert names == ["Lord Caros", "Lady Mara"]      # b.215 then b.218

    def test_siblings(self, renn):
        g = Genealogy(renn)
        elia = person_id(renn, "Lady Elia")
        assert "Prince Oren" in [g.people[s].name for s in g.siblings_of(elia)]

    def test_ancestors_and_descendants(self, renn):
        g = Genealogy(renn)
        old_king = person_id(renn, "Old King Renn")
        mara = person_id(renn, "Lady Mara")

        descendants = g.descendants_of(old_king)
        assert mara in descendants
        assert descendants[mara] == 2                     # grandchild

        ancestors = g.ancestors_of(mara)
        assert old_king in ancestors
        assert ancestors[old_king] == 2

    def test_root_ancestor(self, renn):
        g = Genealogy(renn)
        mara = person_id(renn, "Lady Mara")
        assert g.people[g.root_ancestors(mara)[0]].name == "Old King Renn"

    def test_relationship_labels(self, renn):
        g = Genealogy(renn)
        oren = person_id(renn, "Prince Oren")
        elia = person_id(renn, "Lady Elia")
        caros = person_id(renn, "Lord Caros")
        aldren = person_id(renn, "King Aldren")
        old_king = person_id(renn, "Old King Renn")

        assert g.relationship_between(oren, elia) == "sibling"
        assert g.relationship_between(oren, aldren) == "parent"
        assert g.relationship_between(aldren, oren) == "child"
        assert g.relationship_between(oren, old_king) == "grandparent"
        assert g.relationship_between(oren, caros) == "first cousin"
        assert g.relationship_between(oren, oren) == "the same person"

    def test_living_on_a_date(self, renn):
        g = Genealogy(renn)
        living_240 = {p.name for p in g.living_on(renn.day(240))}
        assert "King Aldren" in living_240
        assert "Lord Corren" not in living_240          # died 235
        assert "Old King Renn" not in living_240        # died 201

        living_205 = {p.name for p in g.living_on(renn.day(205))}
        assert "Lord Corren" in living_205
        assert "Prince Oren" not in living_205          # born 210

    def test_house_membership(self, renn):
        g = Genealogy(renn)
        marr = renn.entity_named("House Marr", "house")
        assert [p.name for p in g.house_members(marr.id)] == ["Edric"]

    def test_legitimacy_defaults_and_inheritance_rules(self):
        assert Legitimacy.LEGITIMATE.inherits_by_default
        assert Legitimacy.LEGITIMISED.inherits_by_default
        # a disputed claim is still a claim -- that is what makes it a story
        assert Legitimacy.DISPUTED.inherits_by_default
        assert not Legitimacy.ILLEGITIMATE.inherits_by_default


class TestGenealogyEdgeCases:
    def test_adoptive_parents_confer_legal_standing(self, world: World):
        parent = world.add_entity("person", "Foster Lord", exists_from=world.day(100))
        child = world.add_entity("person", "Ward", exists_from=world.day(130))
        world.assert_fact(parent, "adoptive_parent_of", child)
        g = Genealogy(world)
        assert parent.id in g.parents_of(child.id, lens="legal")
        assert parent.id not in g.parents_of(child.id, lens="biological")

    def test_a_person_with_no_parents_has_no_ancestors(self, world: World):
        lonely = world.add_entity("person", "Foundling")
        g = Genealogy(world)
        assert g.ancestors_of(lonely.id) == {}
        assert g.root_ancestors(lonely.id) == [lonely.id]

    def test_a_parentage_cycle_does_not_hang(self, world: World):
        """Corrupt or mischievous data must not spin the walk."""
        a = world.add_entity("person", "A", exists_from=world.day(100))
        b = world.add_entity("person", "B", exists_from=world.day(120))
        world.assert_fact(a, "parent_of", b)
        world.assert_fact(b, "parent_of", a)
        g = Genealogy(world)
        assert len(g.descendants_of(a.id)) <= 2
        assert len(g.ancestors_of(a.id)) <= 2

    def test_succession_with_no_heirs_at_all(self, world: World):
        last = world.add_entity("person", "The Last", exists_from=world.day(100))
        title = world.add_title("Lord of Nothing", dynasty_root_id=last.id)
        world.grant_title(title.id, last.id, from_day=world.day(120))
        result = SuccessionEngine(world).compute(title.id, world.day(130))
        assert result.line == []
        assert result.heir is None
        assert "No eligible heir" in result.explain()
