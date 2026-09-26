"""
tests/test_planner.py

GuidedQueryPlanner against the quickstart fixture graph with a scripted
decision model: every Choice/Noul answer is fixed in advance, so these tests
exercise the candidate generation and plan assembly, not Laya itself.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from graphrag.retrieval.planner import candidates
from graphrag.retrieval.planner.candidates import extract_numbers
from graphrag.retrieval.planner.plan import FieldRef, Filter, Having, Hop, Metric
from graphrag.retrieval.planner.planner import GuidedQueryPlanner
from tests.scripted_model import ScriptedModel


def _plan(db, model, question, seeds, overrides=None, **kw):
    with patch("graphrag.retrieval.planner.planner.get_decision_model", return_value=model):
        planner = GuidedQueryPlanner(db, relation_schema={"DEVELOPED": "developed an idea"}, **kw)
        return planner.plan(question, seeds, overrides=overrides)


class TestAcceptancePlans:
    def test_ae1_count_incoming_developed(self, science_graph):
        model = ScriptedModel(choices=["count", "DEVELOPED:in"])
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus", "Isaac Newton"])
        assert result.plan.operation == "count"
        assert result.plan.start == "Calculus"
        assert result.plan.hops == [Hop("DEVELOPED", "in")]
        # Only moves that exist on Calculus are offered.
        assert set(model.choice_calls[1]) == {"DEVELOPED:in", "BORN_IN:in", "any:in"}

    def test_ae2_group_by_relation_type(self, science_graph):
        model = ScriptedModel(choices=["any:out", "stop"])
        result = _plan(science_graph, model, "Her ilişki tipinde kaç kenar var?", ["Calculus"])
        assert result.plan.start is None
        assert result.plan.hops == [Hop(None, "out")]
        assert result.plan.keys == [FieldRef("e0", "type")]
        assert result.plan.metrics == [Metric("count")]
        # "Her ilişki tipinde" names the field, and one step has one relation
        # variable, so there is nothing left to ask.
        assert next(t for t in result.trace if t.step == "key").forced

    def test_ae3_two_hops_excluding_start(self, science_graph):
        model = ScriptedModel(choices=["list", "BORN_IN:out", "BORN_IN:in", "stop"], nouls=[0.9])
        result = _plan(science_graph, model, "Einstein'ın doğduğu yerde doğan başka kim var?", ["Albert Einstein"])
        assert result.plan.hops == [Hop("BORN_IN", "out"), Hop("BORN_IN", "in")]
        assert result.plan.filters == [Filter(FieldRef("v2", "name"), "!=", "Albert Einstein")]


class TestOperationStep:
    """
    Code removes the operations the wording rules out; the model picks among
    the rest. Every operation error on the labelled set was a `group` question
    read as something else, or `rank` chosen with nothing to rank.
    """

    @staticmethod
    def _offered(model) -> set[str]:
        return set(model.choice_calls[0])

    @pytest.mark.parametrize("question", [
        "How many relations of each type does Isaac Newton have?",
        "Isaac Newton'un her ilişki türünden kaç farklı hedefi var?",
    ])
    def test_a_distributive_question_is_a_group(self, science_graph, question):
        model = ScriptedModel(choices=["any:out", "stop"])
        result = _plan(science_graph, model, question, [])
        assert result.plan.operation == "group"
        assert next(t for t in result.trace if t.step == "operation").forced

    @pytest.mark.parametrize("question", [
        "List every place in the graph.",          # "every" is not distributive
        "Her şey kaç tane?",                       # "her şey" is "everything"
    ])
    def test_these_are_not_distributive(self, science_graph, question):
        model = ScriptedModel(choices=["count"])
        result = _plan(science_graph, model, question, [])
        assert not next(t for t in result.trace if t.step == "operation").forced

    def test_a_superlative_wins_over_a_distributive_marker(self, science_graph):
        # "the most per type" is still a ranking, so the Choice decides.
        model = ScriptedModel(choices=["rank", "any:out", "stop"], nouls=[0.1])
        result = _plan(science_graph, model, "Which type has the most edges of each kind?", [])
        assert result.plan.operation == "rank"

    def test_rank_needs_a_superlative(self, science_graph):
        model = ScriptedModel(choices=["group", "any:out", "stop", "count"])
        _plan(science_graph, model, "Which entities have 2 outgoing relations?", [])
        assert "rank" not in self._offered(model)

    @pytest.mark.parametrize("question", [
        "En çok giden ilişkisi olan 2 varlık hangisi?",
        "Which entity has the most connections?",
    ])
    def test_a_superlative_keeps_rank(self, science_graph, question):
        model = ScriptedModel(choices=["rank", "any:out", "stop", "count"],
                              nouls=[0.1])
        _plan(science_graph, model, question, [])
        assert "rank" in self._offered(model)

    @pytest.mark.parametrize("question", [
        "Which entities have at least 2 outgoing relations?",
        "Which relation types occur more than once?",
        "Graph'ta 1'den fazla geçen ilişki türleri hangileri?",
        "En az 2 ilişkisi olan varlıklar hangileri?",
    ])
    def test_a_threshold_question_is_a_group(self, science_graph, question):
        model = ScriptedModel(choices=["any:out", "stop"])
        result = _plan(science_graph, model, question, [])
        assert result.plan.operation == "group"
        assert next(t for t in result.trace if t.step == "operation").forced

    @pytest.mark.parametrize("question", [
        "Which entities have at least 2 outgoing relations?",
        "En az 2 ilişkisi olan varlıklar hangileri?",
    ])
    def test_a_threshold_is_not_a_superlative(self, science_graph, question):
        # "at least 2" / "en az 2" belongs to HAVING, not to an ordering,
        # although both end in a word the superlative match would take.
        assert not candidates.asks_for_a_ranking(question)

    def test_a_superlative_before_a_number_still_ranks(self, science_graph):
        # "en çok ... 2 varlık" is "the top 2", not a threshold.
        assert candidates.asks_for_a_ranking("En çok giden ilişkisi olan 2 varlık hangisi?")
        assert not candidates.sets_a_threshold("En çok giden ilişkisi olan 2 varlık hangisi?")

    def test_a_bare_number_is_not_a_threshold(self, science_graph):
        assert not candidates.sets_a_threshold("Which entities have 2 outgoing relations?")

    @pytest.mark.parametrize("question", [
        "How many entities are there in the graph?",
        "Isaac Newton kaç eser yazdı?",
    ])
    def test_a_how_many_question_is_never_a_list(self, science_graph, question):
        model = ScriptedModel(choices=["count", "stop"])
        _plan(science_graph, model, question, [])
        assert "list" not in self._offered(model)

    def test_a_which_question_keeps_list(self, science_graph):
        model = ScriptedModel(choices=["list", "stop"])
        _plan(science_graph, model, "List every place in the graph.", [])
        assert "list" in self._offered(model)

    def test_both_rules_can_apply_at_once(self, science_graph):
        model = ScriptedModel(choices=["count", "stop"])
        _plan(science_graph, model, "Isaac Newton kaç eser yazdı?", [])
        assert self._offered(model) == {"count", "group"}


class TestStartStep:
    """
    The start step is decided by code, not by the model: the seed is the
    anchor when the question names it. Measured reason — asked as a Choice,
    the model chose "the whole graph" for all 14 seeded questions of the
    labelled set.
    """

    def test_a_named_seed_becomes_the_anchor(self, science_graph):
        model = ScriptedModel(choices=["count", "DEVELOPED:in"])
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus"])
        assert result.plan.start == "Calculus"
        assert not any("anchor" in options for options in model.choice_calls)

    def test_a_seed_the_question_never_names_is_ignored(self, science_graph):
        # SeedSelector returns a best vector match even when the question
        # names nothing; that is not a reason to anchor on it.
        model = ScriptedModel(choices=["count"])
        result = _plan(science_graph, model, "Graph'ta kaç varlık var?", ["Isaac Newton"])
        assert result.plan.start is None

    @pytest.mark.parametrize("question, seed", [
        ("Calculus'a kaç varlık bağlı?", "Calculus"),                    # Turkish suffix
        ("Einstein'ın doğduğu yer neresi?", "Albert Einstein"),          # surname alone
        ("How many places was Isaac Newton born in?", "Isaac Newton"),   # full name
        ("isaac newton kaç eser yazdı?", "Isaac Newton"),                # case
    ])
    def test_forms_that_count_as_naming_the_seed(self, science_graph, question, seed):
        result = _plan(science_graph, ScriptedModel(choices=["count"]), question, [seed])
        assert result.plan.start == seed

    def test_a_longer_word_does_not_match_a_short_name(self, science_graph):
        result = _plan(science_graph, ScriptedModel(choices=["count"]),
                       "ulmus ağacı graph'ta var mı?", ["Ulm"])
        assert result.plan.start is None

    def test_the_step_is_forced_so_it_cannot_lower_confidence(self, science_graph):
        model = ScriptedModel(choices=["count", "DEVELOPED:in"], prob=0.8)
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus"])
        start = next(t for t in result.trace if t.step == "start")
        assert start.forced and start.selected == "anchor"
        assert result.confidence == pytest.approx(0.8)


class TestHopLoop:
    def test_an_anchored_plan_is_not_offered_stop_at_hop0(self, science_graph):
        # Stopping before the first hop would return the anchor itself.
        model = ScriptedModel(choices=["count", "DEVELOPED:in"])
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus"])
        assert result.plan.start == "Calculus"
        assert "stop" not in model.choice_calls[1]
        assert result.plan.hops == [Hop("DEVELOPED", "in")]

    def test_stop_is_offered_again_from_hop1_on(self, science_graph):
        model = ScriptedModel(choices=["list", "any:out", "stop"])
        result = _plan(science_graph, model, "Albert Einstein neyle ilişkili?", ["Albert Einstein"])
        assert "stop" not in model.choice_calls[1]
        assert "stop" in model.choice_calls[2]
        assert result.plan.hops == [Hop(None, "out")]

    def test_a_question_about_no_relation_stops_at_hop0(self, science_graph):
        # A zero-hop plan over the whole graph is a real answer ("how many
        # entities are there?"), and the question names no relation, so code
        # stops there rather than asking.
        model = ScriptedModel(choices=["count"])
        result = _plan(science_graph, model, "Graph'ta kaç varlık var?", [])
        assert result.plan.hops == []
        assert next(t for t in result.trace if t.step == "hop0").forced
        assert not any("stop" in options for options in model.choice_calls)

    def test_a_question_about_a_relation_may_not_stop_at_hop0(self, science_graph):
        model = ScriptedModel(choices=["count", "any:out", "stop"])
        result = _plan(science_graph, model, "Kaç tane ilişki var?", [])
        assert "stop" not in model.choice_calls[1]
        assert result.plan.hops == [Hop(None, "out")]

    def test_a_named_relation_takes_the_other_types_off_hop0(self, science_graph):
        # The fixture schema describes DEVELOPED as "developed an idea", so
        # "developed" names it and nothing else.
        model = ScriptedModel(choices=["list", "DEVELOPED:out", "stop"])
        result = _plan(science_graph, model, "Who developed something?", [])
        assert result.plan.hops == [Hop("DEVELOPED", "out")]
        assert not any(k.startswith("any:") for k in model.choice_calls[1])

    def test_a_named_relation_leaves_the_direction_to_the_model(self, science_graph):
        model = ScriptedModel(choices=["list", "DEVELOPED:in"])
        result = _plan(science_graph, model, "Who developed something?", [])
        assert result.plan.hops == [Hop("DEVELOPED", "in")]
        assert set(model.choice_calls[1]) == {"DEVELOPED:out", "DEVELOPED:in"}

    def test_only_hop0_is_restricted(self, science_graph):
        model = ScriptedModel(choices=["list", "DEVELOPED:out", "any:in"])
        result = _plan(science_graph, model, "Who developed something?", [], max_hops=2)
        assert result.plan.hops == [Hop("DEVELOPED", "out"), Hop(None, "in")]
        assert any(k.startswith("any:") for k in model.choice_calls[2])

    def test_a_relation_named_by_its_object_is_not_named(self, science_graph):
        # "an idea" is what DEVELOPED acts on, not what it is called.
        assert candidates.names_one_relation("Which idea is it?", {"DEVELOPED": "developed an idea"}) is None
        assert candidates.names_one_relation("Who developed it?", {"DEVELOPED": "developed an idea"}) == "DEVELOPED"

    def test_a_schema_word_names_the_relation_in_another_language(self, science_graph):
        # The description is English, so a Turkish question names nothing
        # until the schema lists the words that name the relation.
        schema, words = {"DEVELOPED": "developed an idea"}, {"DEVELOPED": ("geliştir",)}
        assert candidates.names_one_relation("Calculus'u kim geliştirdi?", schema) is None
        assert candidates.names_one_relation("Calculus'u kim geliştirdi?", schema, words) == "DEVELOPED"

    def test_a_schema_word_matches_a_suffixed_form(self, science_graph):
        schema, words = {"AUTHORED": "wrote a work"}, {"AUTHORED": ("yazdı",)}
        for question in ("Newton ne yazdı?", "Newton'un yazdığı eser hangisi?"):
            assert candidates.names_one_relation(question, schema, words) == "AUTHORED"
        # A shorter word than the stem is not a prefix of it.
        assert candidates.names_one_relation("Newton ne yazar?", schema, words) is None

    def test_a_schema_word_reaches_hop0(self, science_graph):
        model = ScriptedModel(choices=["list", "DEVELOPED:in"])
        result = _plan(science_graph, model, "Calculus'u kim geliştirdi?", [],
                       relation_words={"DEVELOPED": ("geliştir",)})
        assert result.plan.hops == [Hop("DEVELOPED", "in")]
        assert set(model.choice_calls[1]) == {"DEVELOPED:out", "DEVELOPED:in"}

    def test_a_schema_word_counts_as_mentioning_a_relation(self, science_graph):
        # Without it the question names no relation at all and stops at hop0.
        model = ScriptedModel(choices=["count", "DEVELOPED:out", "stop"])
        result = _plan(science_graph, model, "Kaç şey geliştirilmiş?", [],
                       relation_words={"DEVELOPED": ("geliştir",)})
        assert result.plan.hops == [Hop("DEVELOPED", "out")]
        assert _plan(science_graph, ScriptedModel(choices=["count"]),
                     "Kaç şey geliştirilmiş?", []).plan.hops == []

    def test_two_named_relations_leave_the_choice_alone(self, science_graph):
        schema = {"DEVELOPED": "developed an idea", "AUTHORED": "wrote or authored a work"}
        assert candidates.names_one_relation("Who developed and wrote something?", schema) is None

    def test_a_single_option_is_forced_rather_than_asked(self, science_graph):
        # Leibniz has one move in the fixture graph, and it is the one the
        # question names, so nothing is left to decide.
        model = ScriptedModel(choices=["list", "stop"])
        result = _plan(science_graph, model, "What did Gottfried Leibniz develop?", ["Gottfried Leibniz"])
        hop0 = next(t for t in result.trace if t.step == "hop0")
        assert hop0.forced and hop0.selected == "DEVELOPED:out"

    def test_a_stated_step_count_walks_exactly_that_far(self, science_graph):
        model = ScriptedModel(choices=["count", "any:out", "RELATED_TO:out"])
        result = _plan(science_graph, model, "How many entities are exactly two steps away from "
                                             "Isaac Newton?", ["Isaac Newton"], max_hops=3)
        assert result.plan.hops == [Hop(None, "out"), Hop("RELATED_TO", "out")]
        assert "stop" not in model.choice_calls[2]          # hop1 may not stop short
        assert next(t for t in result.trace if t.step == "hop2").forced   # nor walk on

    def test_a_relation_named_twice_over_walks_two_steps(self, science_graph):
        # "the idea Leibniz developed" describes the entity in the middle, so
        # there is a step on either side of it.
        # Leibniz has one move in the fixture graph, so hop0 needs no Choice.
        model = ScriptedModel(choices=["list", "any:in", "stop"])
        result = _plan(science_graph, model,
                       "List everything the idea Gottfried Leibniz developed is connected to.",
                       ["Gottfried Leibniz"])
        assert len(result.plan.hops) == 2
        assert "stop" not in model.choice_calls[1]

    def test_one_reference_to_a_relation_may_stop_after_one_hop(self, science_graph):
        model = ScriptedModel(choices=["list", "stop"])
        result = _plan(science_graph, model, "What did Gottfried Leibniz develop?",
                       ["Gottfried Leibniz"])
        assert len(result.plan.hops) == 1
        assert "stop" in model.choice_calls[1]

    def test_what_the_question_asks_for(self, science_graph):
        schema, words = {"DEVELOPED": "developed an idea"}, {"DEVELOPED": ("geliştir",)}
        asked = lambda q: candidates.hops_asked_for(q, schema, words)
        assert asked("What is Newton connected to?") == (1, False)
        assert asked("What did Newton develop?") == (1, False)
        assert asked("What is the idea Newton developed connected to?") == (2, False)
        assert asked("Newton'un geliştirdiği şeyle ilişkili olanlar?") == (2, False)
        assert asked("How many entities are exactly two steps away?") == (2, True)
        assert asked("Newton'dan 3 adım uzaktaki varlıklar?") == (3, True)

    def test_the_way_back_is_not_offered(self, science_graph):
        # BORN_IN:out then BORN_IN:in returns to the entities the plan came
        # from, which answers nothing it does not already hold.
        model = ScriptedModel(choices=["list", "AUTHORED:out", "stop"])
        result = _plan(science_graph, model, "Isaac Newton ne yazdı?", ["Isaac Newton"])
        assert result.plan.hops == [Hop("AUTHORED", "out")]
        assert "AUTHORED:in" not in model.choice_calls[2]
        assert "RELATED_TO:out" in model.choice_calls[2]

    def test_the_way_back_is_offered_when_the_question_asks_for_the_others(self, science_graph):
        model = ScriptedModel(choices=["list", "BORN_IN:out", "BORN_IN:in", "stop"], nouls=[0.9])
        result = _plan(science_graph, model, "Einstein'ın doğduğu yerde doğan başka kim var?",
                       ["Albert Einstein"])
        assert result.plan.hops == [Hop("BORN_IN", "out"), Hop("BORN_IN", "in")]

    def test_the_way_back_is_kept_when_it_is_the_only_move(self, science_graph):
        # Calculus' only neighbour leads straight back, so the plan stops
        # rather than being left without a move.
        model = ScriptedModel(choices=["count", "DEVELOPED:in"])
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus"])
        assert result.plan.hops == [Hop("DEVELOPED", "in")]
        assert next(t for t in result.trace if t.step == "hop1").forced

    def test_empty_frontier_forces_stop_without_asking(self, science_graph):
        model = ScriptedModel(choices=["list"])
        result = _plan(science_graph, model, "Banana Bread neyle ilişkili?", ["Banana Bread"])
        assert result.plan.hops == []
        assert not any("stop" in options for options in model.choice_calls)
        assert any(t.step == "hop0" and t.forced for t in result.trace)

    def test_max_hops_is_respected(self, science_graph):
        model = ScriptedModel(choices=["list"])  # then always the first non-stop move
        result = _plan(science_graph, model, "q", ["Isaac Newton"], max_hops=2)
        assert len(result.plan.hops) <= 2
        assert sum(1 for opts in model.choice_calls if "stop" in opts) <= 2

    def test_no_seeds_starts_from_all_without_asking(self, science_graph):
        model = ScriptedModel(choices=["count"])
        result = _plan(science_graph, model, "Graph'ta kaç varlık var?", [])
        assert result.plan.start is None and result.plan.hops == []
        assert not any("anchor" in options for options in model.choice_calls)


class TestFiltersAndShape:
    def test_numeric_filter_uses_numbers_from_question(self, science_graph):
        model = ScriptedModel(choices=["group", "any:out", "stop", "v1", ">", "count"],
                              nouls=[0.9])
        result = _plan(science_graph, model, "PageRank'ı 0,1'den büyük hedeflere giden kenarlar, tipe göre", [])
        assert result.plan.filters == [Filter(FieldRef("v1", "pagerank"), ">", 0.1)]

    def test_no_numbers_skips_numeric_filter(self, science_graph):
        model = ScriptedModel(choices=["count", "DEVELOPED:in"])
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus"])
        assert result.plan.filters == []
        # The step still appears in the trace, but it costs nothing: with no
        # number and no field named, the answer is not the model's to give.
        assert all(t.forced for t in result.trace if t.step.startswith("filter"))

    def test_a_number_without_a_field_is_not_a_filter(self, science_graph):
        # "1'den fazla" compares the size of a group with a number; no node
        # carries a value the question could mean.
        model = ScriptedModel(choices=["any:out", "stop"])
        result = _plan(science_graph, model, "Graph'ta 1'den fazla geçen ilişki türleri hangileri?", [])
        assert result.plan.filters == []

    def test_a_ranking_spends_its_number_on_the_top_k(self, science_graph):
        # The question names PageRank, but its number is the top-k.
        model = ScriptedModel(choices=["rank", "max:v0.pagerank"], nouls=[0.1])
        result = _plan(science_graph, model, "PageRank'ı en yüksek 3 varlık hangisi?", [])
        assert result.plan.filters == []
        assert result.plan.limit == 3

    def test_rank_uses_small_number_as_limit(self, science_graph):
        model = ScriptedModel(choices=["rank", "any:out", "stop"], nouls=[0.1])
        result = _plan(science_graph, model, "En çok bağlantısı olan 3 varlık hangisi?", [])
        assert result.plan.limit == 3
        assert result.plan.keys == [FieldRef("v0", "name")]

    def test_the_key_is_the_field_the_question_names(self, science_graph):
        model = ScriptedModel(choices=["any:out", "stop"])
        result = _plan(science_graph, model, "Her ilişki tipinden kaç tane var?", [])
        assert result.plan.keys == [FieldRef("e0", "type")]

    def test_one_hop_groups_by_the_near_end(self, science_graph):
        # Grouping by the far end of a single hop is the same question with
        # the hop reversed, and the direction is already decided.
        model = ScriptedModel(choices=["group", "any:out", "stop", "count"])
        result = _plan(science_graph, model, "ilişkiler neye göre gruplandı?", [])
        assert result.plan.keys == [FieldRef("v0", "name")]
        assert next(t for t in result.trace if t.step == "key").forced

    def test_a_longer_path_leaves_the_end_to_the_model(self, science_graph):
        model = ScriptedModel(choices=["group", "any:out", "any:out", "v2.name", "count"])
        result = _plan(science_graph, model, "ilişkiler neye göre gruplandı?", [], max_hops=2)
        assert result.plan.keys == [FieldRef("v2", "name")]
        assert set(model.choice_calls[3]) == {"v0.name", "v1.name", "v2.name"}

    def test_exactly_one_key_is_chosen(self, science_graph):
        model = ScriptedModel(choices=["group", "any:out", "stop", "count"])
        result = _plan(science_graph, model, "ilişkiler neye göre gruplandı?", [])
        assert len(result.plan.keys) == 1


    def test_a_threshold_is_the_having_operator_and_all(self, science_graph):
        model = ScriptedModel(choices=["any:out", "stop"])
        result = _plan(science_graph, model, "En az 2 ilişkisi olan varlıklar hangileri?", [])
        assert result.plan.having == [Having(Metric("count"), ">=", 2)]
        assert all(t.forced for t in result.trace if t.step.startswith("having"))

    @pytest.mark.parametrize("question, op", [
        ("Which entities have at least 2 relations?", ">="),
        ("Which entities have at most 2 relations?", "<="),
        ("Which types occur more than 1 time?", ">"),
        ("Which types occur less than 3 times?", "<"),
        ("Graph'ta 1'den fazla geçen ilişki türleri?", ">"),
        ("2'den az ilişkisi olan varlıklar?", "<"),
    ])
    def test_the_comparison_is_read_from_the_wording(self, science_graph, question, op):
        assert candidates.comparison_from_wording(question) == op

    def test_a_number_without_a_threshold_still_asks(self, science_graph):
        # "3 varlık" is a limit, not a comparison, so nothing is forced.
        assert candidates.comparison_from_wording("En çok bağlantısı olan 3 varlık hangisi?") is None


class TestCollectMetric:
    """`collect` gathers the relation types of a step; group plans only."""

    @staticmethod
    def _metric_options(model) -> dict[str, str]:
        return next(o for o in model.choice_calls if "count" in o and any(k.startswith("count_distinct") for k in o))

    def test_group_can_collect_the_relation_types(self, science_graph):
        model = ScriptedModel(choices=["group", "any:out", "stop", "collect:e0.type"])
        result = _plan(science_graph, model, "Kim hangi tür ilişkiler kurmuş?", [])
        assert result.plan.metrics == [Metric("collect", FieldRef("e0", "type"))]
        assert "list of every relation type" in result.description

    def test_every_step_can_be_collected(self, science_graph):
        model = ScriptedModel(choices=["group", "any:out", "any:out", "v0.name", "collect:e1.type"])
        result = _plan(science_graph, model, "ilişkiler nereye gidiyor?", [], max_hops=2)
        assert result.plan.metrics == [Metric("collect", FieldRef("e1", "type"))]
        assert {"collect:e0.type", "collect:e1.type"} <= set(self._metric_options(model))

    def test_rank_is_never_offered_collect(self, science_graph):
        # A list cannot be ordered, so ranking by it would make the plan
        # invalid. The metric is a Choice here because the question asks
        # about importance.
        model = ScriptedModel(choices=["rank", "any:out", "stop", "max:v0.pagerank"],
                              nouls=[0.1])
        _plan(science_graph, model, "PageRank'ı en yüksek 3 varlık hangisi?", [])
        assert not any(k.startswith("collect:") for k in self._metric_options(model))

    def test_a_collected_list_is_never_compared_with_a_number(self, science_graph):
        # The question holds a number, so `having` would normally be asked.
        model = ScriptedModel(choices=["group", "any:out", "stop", "collect:e0.type"],
                              nouls=[0.1])
        result = _plan(science_graph, model, "2 ve üzeri ilişki kuranlar hangi türlerde?", [])
        assert result.plan.having == []
        assert not any(t.step.startswith("having") for t in result.trace)


class TestTraceAndFailures:
    def test_trace_and_confidence(self, science_graph):
        model = ScriptedModel(choices=["count", "DEVELOPED:in"], prob=0.8)
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus"])
        steps = [t.step for t in result.trace]
        assert steps[:3] == ["operation", "start", "hop0"]
        hop0 = next(t for t in result.trace if t.step == "hop0")
        assert hop0.selected == "DEVELOPED:in" and hop0.probability == pytest.approx(0.8)
        assert hop0.runner_up is not None and hop0.margin > 0
        assert result.confidence == pytest.approx(min(t.probability for t in result.trace if not t.forced))

    def test_override_replaces_a_step(self, science_graph):
        model = ScriptedModel(choices=["count", "DEVELOPED:in", "stop"], prob=0.8)
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus"],
                       overrides={"hop0": "BORN_IN:in"})
        assert result.plan.hops == [Hop("BORN_IN", "in")]
        hop0 = next(t for t in result.trace if t.step == "hop0")
        # The overridden option keeps the probability the model gave it.
        assert hop0.overridden and hop0.probability == pytest.approx(0.2 / 2)

    def test_model_error_returns_none(self, science_graph):
        class Broken(ScriptedModel):
            def choice_detailed(self, *a):
                raise RuntimeError("backend down")

        assert _plan(science_graph, Broken(), "q", ["Calculus"]) is None

    def test_db_without_planner_support_returns_none(self):
        from unittest.mock import MagicMock
        db = MagicMock()
        db.frontier_moves.side_effect = NotImplementedError
        assert _plan(db, ScriptedModel(choices=["count"]), "q", ["x"]) is None


class TestExtractNumbers:
    @pytest.mark.parametrize("text, expected", [
        ("PageRank'ı 0,1'den büyük", [0.1]),
        ("greater than 0.1", [0.1]),
        ("en çok 3 varlık", [3]),
        ("1900 ile 2000 arası", [1900, 2000]),
        ("sayı yok", []),
    ])
    def test_extract(self, text, expected):
        assert extract_numbers(text) == expected
