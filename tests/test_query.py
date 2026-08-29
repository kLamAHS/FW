"""Asking the world questions (§49).

The brief calls this one of the application's most important features. The module it
was to live in was zero bytes, so the only questions a writer could put to their own
notes were the ones somebody had already built a screen for.

The acceptance list is the questions the specification and this codebase already name —
"which houses serve House Veyne?", "who is related to Lady Mara within three
generations?" — together with the ones a fact spine ought to be able to answer and could
not: who holds nothing, what existed on a date and not before it, which places the
writer marked uncertain, who is sworn to somebody two steps away. Each is written the
way the form would build it, because that is the only way a writer can ask it.
"""

from __future__ import annotations

import pytest

from fw.core import query as Q
from fw.core.seed.renn import seed_renn


@pytest.fixture(scope="module")
def renn():
    made = seed_renn()
    yield made
    made.close()


@pytest.fixture(scope="module")
def who(renn):
    return {e.name: e.id for e in renn.entities()}


def names(answer: Q.Answer) -> list[str]:
    return [row.name for row in answer.rows]


class TestTheQuestionsTheBriefAsks:
    def test_which_houses_serve_house_veyne(self, renn, who):
        answer = Q.run(renn, Q.Query(types=("house",), conditions=(
            Q.Condition(predicate="vassal_of", object_id=who["House Veyne"]),)))
        assert names(answer) == ["House Marr"]

    def test_who_is_related_to_lady_mara_within_three_generations(self, renn, who):
        """Three generations is a walk, not a join, which is why `Within` exists."""
        mara = next(name for name in who if "Mara" in name)
        answer = Q.run(renn, Q.Query(within=Q.Within(
            start_id=who[mara], predicates=("parent_of", "married_to"), hops=3)))
        assert len(answer.rows) >= 4
        assert all(row.distance is not None and 1 <= row.distance <= 3
                   for row in answer.rows)
        assert mara not in names(answer), "she is not within three steps of herself"

    def test_every_settlement_the_writer_called_a_port(self, renn):
        answer = Q.run(renn, Q.Query(types=("settlement",), conditions=(
            Q.Condition(predicate="settlement_type", test="is", value="port"),)))
        assert names(answer) == ["Blackmere", "Greyhaven"]

    def test_which_towns_are_bigger_than_twenty_thousand(self, renn):
        """A writer's numbers arrive as prose, so the comparison reads through it."""
        answer = Q.run(renn, Q.Query(types=("settlement",), conditions=(
            Q.Condition(predicate="population", test="greater_than", value="20000"),)))
        assert names(answer) == ["Rennford"]

    def test_which_houses_answer_to_nobody(self, renn):
        """The negative question, which a filter that can only add clauses cannot ask."""
        answer = Q.run(renn, Q.Query(types=("house",), conditions=(
            Q.Condition(predicate="vassal_of", negate=True),)))
        assert "House Veyne" not in names(answer)
        assert "House Dray" in names(answer)

    def test_what_house_marr_holds_in_law(self, renn, who):
        """Read the other way round: the entity as the object of the fact."""
        answer = Q.run(renn, Q.Query(conditions=(
            Q.Condition(predicate="legally_owns", direction="in",
                        object_id=who["House Marr"]),)))
        assert "The Northmarch" in names(answer)

    def test_who_holds_greyhaven_and_under_which_authority(self, renn, who):
        """§11's four authorities, asked one at a time — which is the whole point."""
        under = {}
        for authority in ("legally_owns", "administers", "taxes", "claims"):
            answer = Q.run(renn, Q.Query(conditions=(
                Q.Condition(predicate=authority, object_id=who["Greyhaven"]),)))
            under[authority] = names(answer)
        assert under["legally_owns"] == ["House Marr"]
        assert under["administers"] == ["House Veyne"]
        assert under["claims"] == ["House Orren"]
        assert under["taxes"] and under["taxes"] != under["legally_owns"]

    def test_which_places_the_writer_is_unsure_about(self, renn):
        made = renn.add_entity("settlement", "Maybe Ford", confidence="rumored")
        try:
            answer = Q.run(renn, Q.Query(confidence=("rumored", "disputed")))
            assert "Maybe Ford" in names(answer)
        finally:
            renn.delete_entity(made.id)

    def test_what_existed_on_a_date(self, renn):
        early = Q.run(renn, Q.Query(types=("settlement",), exists_on=renn.day(100)))
        late = Q.run(renn, Q.Query(types=("settlement",), exists_on=renn.day(240)))
        assert set(names(early)) < set(names(late))

    def test_what_was_founded_in_a_span_of_years(self, renn):
        answer = Q.run(renn, Q.Query(
            types=("settlement",),
            began_after=renn.day(120), began_before=renn.day(165)))
        assert names(answer) and all(
            renn.day(120) <= row.exists_from <= renn.day(165) for row in answer.rows)

    def test_who_is_seated_in_a_town_of_the_northmarch(self, renn, who):
        """Two joins deep: a group based in a settlement, and the settlement's type."""
        answer = Q.run(renn, Q.Query(conditions=(
            Q.Condition(predicate="based_in", object_type="settlement"),)))
        assert "House Marr" in names(answer)

    def test_everything_under_house_veyne_s_banner(self, renn, who):
        """A directed walk. Both-ways would drag in the Crown above them."""
        answer = Q.run(renn, Q.Query(within=Q.Within(
            start_id=who["House Veyne"], predicates=("vassal_of",),
            direction="in", hops=3)))
        assert "House Marr" in names(answer)
        assert "House Renn" not in names(answer), "walked up as well as down"

    def test_what_a_house_is_strong_at(self, renn):
        answer = Q.run(renn, Q.Query(conditions=(
            Q.Condition(predicate="produces", strength=("high", "very_high")),)))
        assert names(answer)

    def test_a_search_for_a_word_in_a_name(self, renn):
        answer = Q.run(renn, Q.Query(name_contains="ford"))
        assert {"Rennford", "Red Ford"} <= set(names(answer))

    def test_two_conditions_narrow_rather_than_widen(self, renn, who):
        both = Q.run(renn, Q.Query(types=("settlement",), conditions=(
            Q.Condition(predicate="settlement_type", test="is", value="port"),
            Q.Condition(predicate="located_in", object_id=who["The Salt Reach"]))))
        assert names(both) == ["Blackmere"]

    def test_an_answer_can_say_why_each_row_is_one(self, renn, who):
        answer = Q.run(renn, Q.Query(types=("house",), explain=True, conditions=(
            Q.Condition(predicate="vassal_of", object_id=who["House Veyne"]),)))
        assert answer.rows[0].because
        assert "House Veyne" in " ".join(answer.rows[0].because)


class TestItRefusesRatherThanGuesses:
    def test_a_type_nobody_has_is_refused_in_words(self, renn):
        with pytest.raises(Q.QueryError, match="no such kind of thing"):
            Q.run(renn, Q.Query(types=("dragon",)))

    def test_a_predicate_nobody_has_is_refused_in_words(self, renn):
        with pytest.raises(Q.QueryError, match="recorded with"):
            Q.run(renn, Q.Query(conditions=(Q.Condition(predicate="smells_of"),)))

    def test_an_impossible_range_is_refused(self, renn):
        with pytest.raises(Q.QueryError, match="begins after it ends"):
            Q.run(renn, Q.Query(began_after=900, began_before=100))

    def test_a_walk_of_no_steps_is_refused(self, renn, who):
        with pytest.raises(Q.QueryError, match="reaches nowhere"):
            Q.run(renn, Q.Query(within=Q.Within(
                start_id=who["House Veyne"], predicates=("vassal_of",), hops=0)))

    def test_a_wildcard_a_writer_typed_is_taken_literally(self, renn):
        """Somebody searching for `100%` means a hundred per cent."""
        made = renn.add_entity("settlement", "The 100% tithe")
        try:
            assert names(Q.run(renn, Q.Query(name_contains="100%"))) == \
                   ["The 100% tithe"]
            assert not names(Q.run(renn, Q.Query(name_contains="%%%")))
        finally:
            renn.delete_entity(made.id)

    def test_nothing_a_writer_types_reaches_the_statement(self, renn):
        """The one that matters: a query is parameters, never string-building."""
        answer = Q.run(renn, Q.Query(name_contains="'; DROP TABLE entity; --"))
        assert not answer.rows
        assert renn.entities("settlement"), "the world is still here"

    def test_an_answer_is_capped_however_much_is_asked_for(self, renn):
        answer = Q.run(renn, Q.Query(limit=100_000))
        assert answer.query.limit <= Q.language.MOST


class TestTheAnswerIsHonestAboutItself:
    def test_it_says_how_many_there_were_before_the_limit(self, renn):
        answer = Q.run(renn, Q.Query(types=("person",), limit=2))
        assert len(answer.rows) == 2
        assert answer.total > 2 and answer.truncated

    def test_it_shows_its_working(self, renn):
        answer = Q.run(renn, Q.Query(types=("settlement",)))
        assert "FROM entity e WHERE" in answer.sql
        assert answer.sql.count("?") == len(answer.params)

    def test_an_empty_question_says_it_is_asking_for_everything(self, renn):
        answer = Q.run(renn, Q.Query())
        assert answer.notes and "nothing in particular" in answer.notes[0]

    def test_the_same_question_twice_gives_the_same_answer(self, renn):
        query = Q.Query(types=("settlement",), order="name")
        assert names(Q.run(renn, query)) == names(Q.run(renn, query))


class TestAQuestionStaysInsideItsOwnTimeline:
    def test_a_what_if_answers_with_its_own_world(self, renn):
        """The mistake the whole overlay model exists to prevent."""
        renn.create_branch("what if the Marr rose")
        fork = renn.on_branch("what if the Marr rose")
        fork.add_entity("settlement", "Marrhold")
        query = Q.Query(types=("settlement",))
        assert "Marrhold" in names(Q.run(fork, query))
        assert "Marrhold" not in names(Q.run(renn, query))

    def test_canon_s_own_places_are_still_visible_from_a_what_if(self, renn):
        fork = renn.on_branch("what if the Marr rose")
        assert "Rennford" in names(Q.run(fork, Q.Query(types=("settlement",))))


class TestKeepingAQuestion:
    def test_a_saved_question_comes_back(self, renn):
        query = Q.Query(types=("settlement",), name_contains="ford")
        Q.save(renn, "Fords", query, note="for the crossings chapter")
        try:
            kept = {row.name: row for row in Q.saved(renn)}
            assert "Fords" in kept
            assert kept["Fords"].query.name_contains == "ford"
            assert kept["Fords"].note == "for the crossings chapter"
        finally:
            Q.forget(renn, "fords")

    def test_saving_the_same_name_twice_replaces_it(self, renn):
        Q.save(renn, "Ports", Q.Query(types=("settlement",)))
        Q.save(renn, "Ports", Q.Query(types=("house",)))
        try:
            kept = [row for row in Q.saved(renn) if row.name == "Ports"]
            assert len(kept) == 1 and kept[0].query.types == ("house",)
        finally:
            Q.forget(renn, "ports")

    def test_a_saved_question_undoes_like_everything_else(self, renn):
        Q.save(renn, "Undo me", Q.Query(types=("house",)))
        assert any(row.name == "Undo me" for row in Q.saved(renn))
        renn.undo()
        assert not any(row.name == "Undo me" for row in Q.saved(renn))

    def test_a_question_with_no_name_is_refused(self, renn):
        with pytest.raises(Q.QueryError, match="needs a name"):
            Q.save(renn, "   ", Q.Query())


class TestAQuestionSurvivesBeingWrittenDown:
    def test_it_round_trips_through_json(self):
        query = Q.Query(
            types=("house", "settlement"), name_contains="ford", tags=("coastal",),
            confidence=("canon",), exists_on=240, began_after=1, began_before=900,
            conditions=(Q.Condition(predicate="vassal_of", direction="in",
                                    test="contains", value="x", strength=("high",),
                                    at=5, negate=True),),
            within=Q.Within(start_id="e1", predicates=("parent_of",), hops=2),
            order="type", descending=True, limit=42, explain=True)
        assert Q.Query.from_dict(query.as_dict()) == query

    def test_a_question_from_an_older_version_still_reads(self):
        """Only the keys it knew about. A saved query outlives the form that made it."""
        old = {"types": ["house"], "conditions": [{"predicate": "vassal_of"}]}
        query = Q.Query.from_dict(old)
        assert query.types == ("house",)
        assert query.conditions[0].test == "exists"
