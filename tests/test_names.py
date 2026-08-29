"""Naming generated places in the writer's own voice (§67)."""

from __future__ import annotations

from fw.core.calendar.kernel import GREGORIAN
from fw.core.mapgen.names import AVOID, MORPHEMES, Namer, _join, _parse
from fw.core.world import World


def _namer(world: World, **kw) -> Namer:
    """The namer a run gets: learned from the reading, not from the world."""
    return Namer.from_corpus(
        sorted((e.type_key, e.name) for e in world.entities()), **kw)


def world_of(names: list[tuple[str, str]], title: str = "Test") -> World:
    w = World.create(name=title, calendar=GREGORIAN)
    for type_key, name in names:
        w.add_entity(type_key, name)
    return w


RENNISH = [("settlement", n) for n in
           ("Greyhaven", "Rennford", "Blackmere", "Northwatch", "Millbrook", "Red Ford")]
ELVISH = [("settlement", n) for n in
          ("Ysolde", "Aubrienne", "Kethiel", "Maranthe", "Silvanor", "Ellowyn")]


class TestReadingTheWritersNames:
    def test_a_compound_is_taken_apart(self):
        assert _parse("greyhaven").prefixes == ("grey",)
        assert _parse("greyhaven").ending == "haven"
        assert _parse("red ford").prefixes == ("red",)

    def test_a_generic_head_is_not_mistaken_for_a_prefix(self):
        """'River Renn' is a river called Renn, not a place called River — reading it
        the other way round produced regions named 'The Kingdomwater'."""
        assert _parse("river renn").prefixes == ("renn",)
        assert _parse("kingdom of renn").prefixes == ("renn",)
        assert _parse("vale of renn").of_head == "vale"
        assert _parse("vale of renn").ending == "vale"     # their word for a valley

    def test_a_trailing_generic_is_stripped(self):
        # _parse reads an already-normalised name; the article is gone by then.
        assert _parse("iron road").prefixes == ("iron",)
        assert _parse("the iron road").prefixes == ("iron",)   # and never yields 'the'

    def test_an_unpronounceable_prefix_is_refused(self):
        """Northwatch is a fine name and a terrible prefix: Northwatchmarch is not a
        word anyone would write."""
        assert "northwatch" not in _parse("northwatch pass road").prefixes

    def test_the_seam_elides_a_doubled_letter(self):
        assert _join("north", "haven") == "northaven"
        assert _join("grey", "ford") == "greyford"

    def test_it_learns_this_world_and_not_a_generic_list(self):
        w = world_of(RENNISH)
        try:
            model = _namer(w)._kind("settlement")
            assert set(model.prefixes) == {"black", "grey", "mill", "north", "red",
                                           "renn"}
            assert set(model.endings) == {"ford", "brook", "haven", "mere", "watch"}
            assert model.compounds
        finally:
            w.close()


class TestTheNamesThemselves:
    def test_a_name_is_built_from_this_world_s_own_parts(self):
        w = world_of(RENNISH)
        try:
            namer = _namer(w, seed="s")
            made = [namer.name("settlement", f"k{i}", hint="ford") for i in range(4)]
            for name in made:
                assert name.lower().replace(" ", "").endswith("ford"), name
                assert any(name.lower().startswith(p) for p in
                           ("black", "grey", "mill", "north", "red", "renn")), name
        finally:
            w.close()

    def test_the_ending_says_what_the_ground_is(self):
        w = world_of(RENNISH)
        try:
            namer = _namer(w, seed="s")
            harbour = namer.name("settlement", "a", hint="harbour").lower()
            height = namer.name("settlement", "b", hint="height").lower()
            assert "haven" in harbour
            assert "watch" in height
        finally:
            w.close()

    def test_a_world_that_does_not_compound_gets_its_own_sound(self):
        """Ysolde and Aubrienne must not become Ysoldeford."""
        w = world_of(ELVISH)
        try:
            namer = _namer(w, seed="s")
            assert not namer._kind("settlement").compounds
            made = [namer.name("settlement", f"k{i}", hint="ford") for i in range(5)]
            assert not any(m.lower().endswith("ford") for m in made), made
            assert all(m.isalpha() and len(m) >= 4 for m in made), made
        finally:
            w.close()

    def test_a_name_is_never_one_the_writer_already_used(self):
        w = world_of(RENNISH)
        try:
            namer = _namer(w, seed="s")
            existing = {n.lower() for _, n in RENNISH}
            made = [namer.name("settlement", f"k{i}", hint="") for i in range(20)]
            assert not ({m.lower() for m in made} & existing)
        finally:
            w.close()

    def test_no_two_places_share_a_name(self):
        w = world_of(RENNISH)
        try:
            namer = _namer(w, seed="s")
            made = [namer.name("settlement", f"k{i}") for i in range(40)]
            assert len(set(made)) == len(made)
        finally:
            w.close()

    def test_the_article_follows_the_world_s_habit(self):
        w = world_of([("region", n) for n in
                      ("The Northmarch", "The Salt Reach", "The Greywold",
                       "The Redmoor", "The Blackfells")])
        try:
            namer = _namer(w, seed="s")
            made = [namer.name("region", f"g{i}", hint="region") for i in range(6)]
            assert all(m.startswith("The ") for m in made), made
        finally:
            w.close()

    def test_accidental_english_is_refused(self):
        w = world_of(RENNISH + [("settlement", "Marketon")])
        try:
            namer = _namer(w, seed="s")
            made = [namer.name("settlement", f"k{i}", hint="junction") for i in range(12)]
            assert not ({m.lower().replace(" ", "") for m in made} & AVOID), made
        finally:
            w.close()


class TestDeterminism:
    def test_the_same_world_and_key_give_the_same_name(self):
        first, second = world_of(RENNISH), world_of(RENNISH)
        try:
            a = _namer(first, seed="fixed")
            b = _namer(second, seed="fixed")
            keys = [("settlement", f"k{i}", "ford") for i in range(6)]
            assert ([a.name(k, key, hint=h) for k, key, h in keys]
                    == [b.name(k, key, hint=h) for k, key, h in keys])
        finally:
            first.close()
            second.close()

    def test_a_different_seed_names_a_different_town(self):
        w = world_of(RENNISH)
        try:
            a = _namer(w, seed="one")
            b = _namer(w, seed="two")
            assert ([a.name("settlement", f"k{i}") for i in range(6)]
                    != [b.name("settlement", f"k{i}") for i in range(6)])
        finally:
            w.close()


class TestThinWorlds:
    def test_a_world_with_almost_no_names_still_names_things(self):
        w = world_of([("region", "Ash")])
        try:
            namer = _namer(w, seed="s")
            made = [namer.name("settlement", f"k{i}") for i in range(5)]
            assert len(set(made)) == 5
            assert all(m and m[0].isupper() for m in made), made
        finally:
            w.close()

    def test_an_empty_world_does_not_crash(self):
        w = World.create(name="Void", calendar=GREGORIAN)
        try:
            namer = _namer(w, seed="s")
            assert namer.name("settlement", "k0")
        finally:
            w.close()

    def test_every_morpheme_claims_some_ground(self):
        """A morpheme with no hints could never be chosen for a reason."""
        assert all(hints for hints in MORPHEMES.values())
