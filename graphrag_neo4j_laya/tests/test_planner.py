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
        model = ScriptedModel(choices=["count", "anchor", "DEVELOPED:in", "stop"])
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus", "Isaac Newton"])
        assert result.plan.operation == "count"
        assert result.plan.start == "Calculus"
        assert result.plan.hops == [Hop("DEVELOPED", "in")]
        # Only moves that exist on Calculus are offered.
        assert set(model.choice_calls[2]) == {"DEVELOPED:in", "BORN_IN:in", "any:in", "stop"}

    def test_ae2_group_by_relation_type(self, science_graph):
        model = ScriptedModel(choices=["group", "all", "any:out", "stop", "count"],
                              batch={"key:e0.type": 0.9})
        result = _plan(science_graph, model, "Her ilişki tipinde kaç kenar var?", ["Calculus"])
        assert result.plan.start is None
        assert result.plan.hops == [Hop(None, "out")]
        assert result.plan.keys == [FieldRef("e0", "type")]
        assert result.plan.metrics == [Metric("count")]
        assert model.batch_calls == 1

    def test_ae3_two_hops_excluding_start(self, science_graph):
        model = ScriptedModel(choices=["list", "anchor", "BORN_IN:out", "BORN_IN:in", "stop"], nouls=[0.9])
        result = _plan(science_graph, model, "Einstein'ın doğduğu yerde doğan başka kim var?", ["Albert Einstein"])
        assert result.plan.hops == [Hop("BORN_IN", "out"), Hop("BORN_IN", "in")]
        assert result.plan.filters == [Filter(FieldRef("v2", "name"), "!=", "Albert Einstein")]


class TestHopLoop:
    def test_empty_frontier_forces_stop_without_asking(self, science_graph):
        model = ScriptedModel(choices=["list", "anchor"])
        result = _plan(science_graph, model, "Banana Bread neyle ilişkili?", ["Banana Bread"])
        assert result.plan.hops == []
        assert not any("stop" in options for options in model.choice_calls)
        assert any(t.step == "hop0" and t.forced for t in result.trace)

    def test_max_hops_is_respected(self, science_graph):
        model = ScriptedModel(choices=["list", "anchor"])  # then always the first non-stop move
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
        model = ScriptedModel(choices=["count", "anchor", "DEVELOPED:in", "stop"])
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


class TestTraceAndFailures:
    def test_trace_and_confidence(self, science_graph):
        model = ScriptedModel(choices=["count", "anchor", "DEVELOPED:in", "stop"], prob=0.8)
        result = _plan(science_graph, model, "Calculus'u kaç kişi geliştirdi?", ["Calculus"])
        steps = [t.step for t in result.trace]
        assert steps[:3] == ["operation", "start", "hop0"]
        hop0 = next(t for t in result.trace if t.step == "hop0")
        assert hop0.selected == "DEVELOPED:in" and hop0.probability == pytest.approx(0.8)
        assert hop0.runner_up is not None and hop0.margin > 0
        assert result.confidence == pytest.approx(min(t.probability for t in result.trace if not t.forced))

    def test_override_replaces_a_step(self, science_graph):
        model = ScriptedModel(choices=["count", "anchor", "DEVELOPED:in", "stop"], prob=0.8)
        result = _plan(science_graph, model, "q", ["Calculus"], overrides={"hop0": "BORN_IN:in"})
        assert result.plan.hops == [Hop("BORN_IN", "in")]
        hop0 = next(t for t in result.trace if t.step == "hop0")
        # The overridden option keeps the probability the model gave it.
        assert hop0.overridden and hop0.probability == pytest.approx(0.2 / 3)

    def test_model_error_returns_none(self, science_graph):
        class Broken(ScriptedModel):
            def choice_detailed(self, *a):
                raise RuntimeError("backend down")

        assert _plan(science_graph, Broken(), "q", ["Calculus"]) is None

    def test_db_without_planner_support_returns_none(self):
        from unittest.mock import MagicMock
        db = MagicMock()
        db.frontier_moves.side_effect = NotImplementedError
        assert _plan(db, ScriptedModel(choices=["count", "anchor"]), "q", ["x"]) is None


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
