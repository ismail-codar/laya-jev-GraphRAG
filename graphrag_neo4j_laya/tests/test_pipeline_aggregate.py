"""
tests/test_pipeline_aggregate.py

The aggregate branch of GraphRAGPipeline.query(): routing thresholds,
fallback to the existing routes, skipped Phase 4 stages, answer modes, the
router-bypassing query_aggregate() API and the ablation guard.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from config.settings import settings
from graphrag.pipeline import _NO_SEEDS_RESPONSE, GraphRAGPipeline
from graphrag.retrieval.planner.executor import AggregateResult
from graphrag.retrieval.planner.plan import QueryPlan
from graphrag.retrieval.post_traversal import CitationResult
from graphrag.retrieval.router import QueryIntent, RouteDecision


def _result() -> AggregateResult:
    return AggregateResult(
        plan=QueryPlan(operation="count"), description="count things", cypher="MATCH ...", params={},
        rows=[{"count_distinct_v0_name": 14}], truncated=False, confidence=0.9, check_probability=0.9,
        facts=[{"name": "Graph database count", "text": "Graph database count: 14 distinct entities match.",
                "score": 1.0}],
        answer="14 recorded in the graph",
    )


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setattr(settings, "aggregate_route_enabled", True)
    monkeypatch.setattr(settings, "aggregate_route_min_confidence", 0.5)
    monkeypatch.setattr(settings, "aggregate_answer_mode", "template")
    monkeypatch.setattr(settings, "decision_model_backend", "laya")
    p = GraphRAGPipeline.__new__(GraphRAGPipeline)
    p._db = MagicMock()
    p._db.get_node_text.side_effect = lambda name: name
    p._router = MagicMock()
    p._seeds = MagicMock()
    p._seeds.select.return_value = ["Isaac Newton"]
    p._astar = MagicMock()
    p._astar.search.return_value = [{"path": ["Isaac Newton", "Calculus"], "edges": ["BORN_IN"], "score": 0.9}]
    p._bfs = MagicMock()
    p._llm = MagicMock()
    p._llm.generate.return_value = "LLM answer"
    p._aggregate = MagicMock()
    p._aggregate_warned = False
    return p


@pytest.fixture
def phase4():
    with patch("graphrag.pipeline.rerank_context", side_effect=lambda nodes, q: nodes) as rerank, \
         patch("graphrag.pipeline.resolve_conflicts", return_value=([], [])) as conflicts, \
         patch("graphrag.pipeline.hallucination_gate", return_value=(True, 0.9)) as gate, \
         patch("graphrag.pipeline.verify_citations",
               return_value=CitationResult(is_faithful=True, noul_score=0.95, answer="")) as cite:
        yield {"rerank": rerank, "conflicts": conflicts, "gate": gate, "cite": cite}


def _route(p, intent, confidence, fallback=QueryIntent.MULTI_HOP):
    p._router.route_detailed.return_value = RouteDecision(intent, confidence, fallback)


class TestAggregateBranch:
    def test_success_skips_rerank_and_gate(self, pipeline, phase4):
        _route(pipeline, QueryIntent.AGGREGATE, 0.9)
        pipeline._aggregate.run.return_value = _result()
        assert pipeline.query("Graph'ta kaç varlık var?") == "14 recorded in the graph"
        assert phase4["rerank"].call_count == 0
        assert phase4["gate"].call_count == 0
        pipeline._astar.search.assert_not_called()

    def test_low_router_confidence_falls_back_without_planning(self, pipeline, phase4):
        # AE4: a connection question that the router only weakly calls aggregate.
        _route(pipeline, QueryIntent.AGGREGATE, 0.3, fallback=QueryIntent.MULTI_HOP)
        answer = pipeline.query("Newton ile Einstein nasıl bağlantılı?")
        pipeline._aggregate.run.assert_not_called()
        pipeline._astar.search.assert_called()
        assert answer == "LLM answer"

    def test_declined_plan_falls_back_to_existing_route(self, pipeline, phase4):
        # AE5: the planner returns None (low plan confidence or failed check).
        _route(pipeline, QueryIntent.AGGREGATE, 0.9, fallback=QueryIntent.LOCAL)
        pipeline._aggregate.run.return_value = None
        pipeline._bfs.retrieve.return_value = []
        assert pipeline.query("q") == "LLM answer"
        pipeline._bfs.retrieve.assert_called_once()
        assert phase4["rerank"].call_count == 1

    def test_llm_mode_verifies_against_facts(self, pipeline, phase4, monkeypatch):
        monkeypatch.setattr(settings, "aggregate_answer_mode", "llm")
        _route(pipeline, QueryIntent.AGGREGATE, 0.9)
        result = _result()
        pipeline._aggregate.run.return_value = result
        assert pipeline.query("q") == "LLM answer"
        assert "Graph database count: 14" in pipeline._llm.generate.call_args.args[0]
        phase4["cite"].assert_called_once_with("LLM answer", result.facts)

    def test_llm_mode_flags_unverified_answer(self, pipeline, phase4, monkeypatch):
        monkeypatch.setattr(settings, "aggregate_answer_mode", "llm")
        _route(pipeline, QueryIntent.AGGREGATE, 0.9)
        pipeline._aggregate.run.return_value = _result()
        phase4["cite"].return_value = CitationResult(is_faithful=False, noul_score=0.1, answer="")
        assert pipeline.query("q").startswith("[⚠️ UNVERIFIED]")


class TestWithoutSeeds:
    """A question about the whole graph has no entry point to find."""

    def test_the_plan_still_runs(self, pipeline, phase4):
        _route(pipeline, QueryIntent.AGGREGATE, 0.9)
        pipeline._seeds.select.return_value = []
        pipeline._aggregate.run.return_value = _result()
        assert pipeline.query("How many theories are there in the graph?") == "14 recorded in the graph"
        assert pipeline._aggregate.run.call_args.args == ("How many theories are there in the graph?", [])

    def test_a_traversal_route_still_says_it_found_no_entry_point(self, pipeline, phase4):
        _route(pipeline, QueryIntent.LOCAL, 0.9)
        pipeline._seeds.select.return_value = []
        assert pipeline.query("Where was Albert Einstein born?") == _NO_SEEDS_RESPONSE
        assert pipeline._bfs.retrieve.call_count == 0

    def test_a_declined_plan_without_seeds_does_not_traverse(self, pipeline, phase4):
        _route(pipeline, QueryIntent.AGGREGATE, 0.9)
        pipeline._seeds.select.return_value = []
        pipeline._aggregate.run.return_value = None
        assert pipeline.query("How many theories are there in the graph?") == _NO_SEEDS_RESPONSE
        assert pipeline._astar.search.call_count == 0


class TestGlobalRoute:
    """The graph as a whole is the entry point, so its communities are it."""

    def test_the_summaries_are_the_context(self, pipeline, phase4):
        _route(pipeline, QueryIntent.GLOBAL, 0.9)
        pipeline._seeds.select.return_value = []
        with patch("graphrag.pipeline.community_summary.summaries",
                   return_value=[{"name": "Community 1", "text": "A group of 9 entities",
                                  "score": 1.0}]) as summaries:
            assert pipeline.query("What are the main themes of this knowledge graph?") == "LLM answer"
        assert summaries.call_count == 1
        assert pipeline._astar.search.call_count == 0
        assert "Community 1" in pipeline._llm.generate.call_args.args[0]

    def test_a_graph_without_communities_walks_from_the_seeds(self, pipeline, phase4):
        _route(pipeline, QueryIntent.GLOBAL, 0.9)
        with patch("graphrag.pipeline.community_summary.summaries", return_value=[]):
            assert pipeline.query("What are the main themes of this knowledge graph?") == "LLM answer"
        assert pipeline._astar.search.call_count == 1

    def test_no_communities_and_no_seeds_says_so(self, pipeline, phase4):
        _route(pipeline, QueryIntent.GLOBAL, 0.9)
        pipeline._seeds.select.return_value = []
        with patch("graphrag.pipeline.community_summary.summaries", return_value=[]):
            assert pipeline.query("What is this graph about?") == _NO_SEEDS_RESPONSE

    def test_the_other_routes_do_not_summarise(self, pipeline, phase4):
        _route(pipeline, QueryIntent.LOCAL, 0.9)
        with patch("graphrag.pipeline.community_summary.summaries") as summaries:
            pipeline.query("Where was Albert Einstein born?")
        assert summaries.call_count == 0


class TestQueryAggregate:
    def test_works_with_flag_off(self, pipeline, monkeypatch):
        monkeypatch.setattr(settings, "aggregate_route_enabled", False)
        pipeline._aggregate.run.return_value = _result()
        assert pipeline.query_aggregate("q").answer == "14 recorded in the graph"
        pipeline._aggregate.run.assert_called_once_with("q", ["Isaac Newton"])
        pipeline._router.route_detailed.assert_not_called()


class TestAblationGuard:
    def test_ablation_backend_falls_back_and_warns_once(self, pipeline, phase4, monkeypatch, caplog):
        monkeypatch.setattr(settings, "decision_model_backend", "ablation")
        _route(pipeline, QueryIntent.AGGREGATE, 0.9)
        with caplog.at_level(logging.WARNING):
            pipeline.query("q")
            pipeline.query("q")
        pipeline._aggregate.run.assert_not_called()
        assert caplog.text.count("Aggregate route unavailable") == 1
        assert pipeline.query_aggregate("q") is None
