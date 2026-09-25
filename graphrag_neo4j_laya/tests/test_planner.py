"""
tests/test_planner.py

GuidedQueryPlanner against the quickstart fixture graph with a scripted
decision model: every Choice/Noul answer is fixed in advance, so these tests
exercise the candidate generation and plan assembly, not Laya itself.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from graphrag.retrieval.planner.candidates import extract_numbers
from graphrag.retrieval.planner.plan import FieldRef, Filter, Hop, Metric
from graphrag.retrieval.planner.planner import GuidedQueryPlanner
from tests.scripted_model import ScriptedModel


def _plan(db, model, question, seeds, overrides=None, **kw):
    with patch("graphrag.retrieval.planner.planner.get_decision_model", return_value=model):
        planner = GuidedQueryPlanner(db, relation_schema={"DEVELOPED": "developed an idea"}, **kw)
        return planner.plan(question, seeds, overrides=overrides)


class TestAcceptancePlans:
    def test_ae1_count_incoming_developed(self, science_graph):
        model = ScriptedModel(choices=["count", "DEVELOPED:in", "stop"])
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus", "Isaac Newton"])
        assert result.plan.operation == "count"
        assert result.plan.start == "Calculus"
        assert result.plan.hops == [Hop("DEVELOPED", "in")]
        # Only moves that exist on Calculus are offered.
        assert set(model.choice_calls[1]) == {"DEVELOPED:in", "BORN_IN:in", "any:in"}

    def test_ae2_group_by_relation_type(self, science_graph):
        model = ScriptedModel(choices=["group", "any:out", "stop", "count"],
                              batch={"key:e0.type": 0.9})
        result = _plan(science_graph, model, "Her ilişki tipinde kaç kenar var?", ["Calculus"])
        assert result.plan.start is None
        assert result.plan.hops == [Hop(None, "out")]
        assert result.plan.keys == [FieldRef("e0", "type")]
        assert result.plan.metrics == [Metric("count")]
        assert model.batch_calls == 1

    def test_ae3_two_hops_excluding_start(self, science_graph):
        model = ScriptedModel(choices=["list", "BORN_IN:out", "BORN_IN:in", "stop"], nouls=[0.9])
        result = _plan(science_graph, model, "Einstein'ın doğduğu yerde doğan başka kim var?", ["Albert Einstein"])
        assert result.plan.hops == [Hop("BORN_IN", "out"), Hop("BORN_IN", "in")]
        assert result.plan.filters == [Filter(FieldRef("v2", "name"), "!=", "Albert Einstein")]


class TestStartStep:
    """
    The start step is decided by code, not by the model: the seed is the
    anchor when the question names it. Measured reason — asked as a Choice,
    the model chose "the whole graph" for all 14 seeded questions of the
    labelled set.
    """

    def test_a_named_seed_becomes_the_anchor(self, science_graph):
        model = ScriptedModel(choices=["count", "DEVELOPED:in", "stop"])
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus"])
        assert result.plan.start == "Calculus"
        assert not any("anchor" in options for options in model.choice_calls)

    def test_a_seed_the_question_never_names_is_ignored(self, science_graph):
        # SeedSelector returns a best vector match even when the question
        # names nothing; that is not a reason to anchor on it.
        model = ScriptedModel(choices=["count", "stop"])
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
        result = _plan(science_graph, ScriptedModel(choices=["count", "stop"]),
                       "ulmus ağacı graph'ta var mı?", ["Ulm"])
        assert result.plan.start is None

    def test_the_step_is_forced_so_it_cannot_lower_confidence(self, science_graph):
        model = ScriptedModel(choices=["count", "DEVELOPED:in", "stop"], prob=0.8)
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus"])
        start = next(t for t in result.trace if t.step == "start")
        assert start.forced and start.selected == "anchor"
        assert result.confidence == pytest.approx(0.8)


class TestHopLoop:
    def test_an_anchored_plan_is_not_offered_stop_at_hop0(self, science_graph):
        # Stopping before the first hop would return the anchor itself.
        model = ScriptedModel(choices=["count", "DEVELOPED:in", "stop"])
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus"])
        assert result.plan.start == "Calculus"
        assert "stop" not in model.choice_calls[1]
        assert "stop" in model.choice_calls[2]          # still offered from hop1 on
        assert result.plan.hops == [Hop("DEVELOPED", "in")]

    def test_an_unanchored_plan_may_stop_at_hop0(self, science_graph):
        # A zero-hop plan over the whole graph is a real answer ("how many
        # entities are there?"), so `stop` stays on the table.
        model = ScriptedModel(choices=["count", "stop"])
        result = _plan(science_graph, model, "Graph'ta kaç varlık var?", [])
        assert "stop" in model.choice_calls[1]
        assert result.plan.hops == []

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
        model = ScriptedModel(choices=["count", "stop"])
        result = _plan(science_graph, model, "Graph'ta kaç varlık var?", [])
        assert result.plan.start is None and result.plan.hops == []
        assert not any("anchor" in options for options in model.choice_calls)


class TestFiltersAndShape:
    def test_numeric_filter_uses_numbers_from_question(self, science_graph):
        model = ScriptedModel(choices=["group", "any:out", "stop", "v1.pagerank", ">", "count"],
                              nouls=[0.9], batch={"key:e0.type": 0.9})
        result = _plan(science_graph, model, "PageRank'ı 0,1'den büyük hedeflere giden kenarlar, tipe göre", [])
        assert result.plan.filters == [Filter(FieldRef("v1", "pagerank"), ">", 0.1)]

    def test_no_numbers_skips_numeric_filter(self, science_graph):
        model = ScriptedModel(choices=["count", "DEVELOPED:in", "stop"])
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus"])
        assert result.plan.filters == []
        assert not any(t.step.startswith("filter") for t in result.trace)

    def test_rank_uses_small_number_as_limit(self, science_graph):
        model = ScriptedModel(choices=["rank", "any:out", "stop", "count"],
                              nouls=[0.1], batch={"key:v0.name": 0.9})
        result = _plan(science_graph, model, "En çok bağlantısı olan 3 varlık hangisi?", [])
        assert result.plan.limit == 3
        assert result.plan.keys == [FieldRef("v0", "name")]

    def test_at_least_one_key_is_kept(self, science_graph):
        model = ScriptedModel(choices=["group", "any:out", "stop", "count"],
                              batch={"key:e0.type": 0.3, "key:v0.name": 0.2})
        result = _plan(science_graph, model, "q", [])
        assert result.plan.keys == [FieldRef("e0", "type")]


class TestCollectMetric:
    """`collect` gathers the relation types of a step; group plans only."""

    @staticmethod
    def _metric_options(model) -> dict[str, str]:
        return next(o for o in model.choice_calls if "count" in o and any(k.startswith("count_distinct") for k in o))

    def test_group_can_collect_the_relation_types(self, science_graph):
        model = ScriptedModel(choices=["group", "any:out", "stop", "collect:e0.type"],
                              batch={"key:v0.name": 0.9})
        result = _plan(science_graph, model, "Kim hangi tür katkılar yapmış?", [])
        assert result.plan.metrics == [Metric("collect", FieldRef("e0", "type"))]
        assert "list of every relation type" in result.description

    def test_every_step_can_be_collected(self, science_graph):
        model = ScriptedModel(choices=["group", "any:out", "any:out", "collect:e1.type"],
                              batch={"key:v0.name": 0.9})
        result = _plan(science_graph, model, "q", [], max_hops=2)
        assert result.plan.metrics == [Metric("collect", FieldRef("e1", "type"))]
        assert {"collect:e0.type", "collect:e1.type"} <= set(self._metric_options(model))

    def test_rank_is_never_offered_collect(self, science_graph):
        # A list cannot be ordered, so ranking by it would make the plan invalid.
        model = ScriptedModel(choices=["rank", "any:out", "stop", "count"],
                              nouls=[0.1], batch={"key:v0.name": 0.9})
        _plan(science_graph, model, "En çok bağlantısı olan 3 varlık hangisi?", [])
        assert not any(k.startswith("collect:") for k in self._metric_options(model))

    def test_a_collected_list_is_never_compared_with_a_number(self, science_graph):
        # The question holds a number, so `having` would normally be asked.
        model = ScriptedModel(choices=["group", "any:out", "stop", "collect:e0.type"],
                              nouls=[0.1], batch={"key:v0.name": 0.9})
        result = _plan(science_graph, model, "2 ve üzeri tür katkı yapanlar hangi türlerde?", [])
        assert result.plan.having == []
        assert not any(t.step.startswith("having") for t in result.trace)


class TestTraceAndFailures:
    def test_trace_and_confidence(self, science_graph):
        model = ScriptedModel(choices=["count", "DEVELOPED:in", "stop"], prob=0.8)
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
