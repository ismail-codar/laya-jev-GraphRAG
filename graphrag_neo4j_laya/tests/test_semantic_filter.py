"""
tests/test_semantic_filter.py

Semantic filter step: closed kind list, per-candidate Noul bands, the
[sure, sure + uncertain] range in facts and answers, and the candidate cap.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from graphrag.retrieval.planner.executor import AggregateExecutor
from graphrag.retrieval.planner.planner import GuidedQueryPlanner
from graphrag.retrieval.planner.semantic_filter import SURE_THRESHOLD, REJECT_THRESHOLD
from tests.scripted_model import ScriptedModel, _result

THEORIES = "How many theories are there in the graph?"


class EntityModel(ScriptedModel):
    """Per-candidate Noul answers keyed by entity name; other Noul calls (the check) pass."""

    def __init__(self, entity_probs: dict[str, float], **kw) -> None:
        super().__init__(**kw)
        self.entity_probs = entity_probs
        self.entity_calls: list[str] = []

    def noul_detailed(self, context, instruction):
        if context.startswith("Entity: "):
            name = context[len("Entity: "):].split("\n")[0]
            self.entity_calls.append(name)
            p = self.entity_probs.get(name, 0.05)
            return _result("noul", None, {"yes": p, "no": 1 - p})
        self.noul_calls += 1
        return _result("noul", None, {"yes": 0.9, "no": 0.1})


@contextmanager
def _model(model):
    with patch("graphrag.retrieval.planner.planner.get_decision_model", return_value=model), \
         patch("graphrag.retrieval.planner.executor.get_decision_model", return_value=model), \
         patch("graphrag.retrieval.planner.semantic_filter.get_decision_model", return_value=model):
        yield


def _run(db, model, question, seeds, **kw):
    with _model(model):
        return AggregateExecutor(db, GuidedQueryPlanner(db), **kw).run(question, seeds)


_THEORY_PROBS = {"General Relativity": 0.9, "Universal Gravitation": 0.85, "Calculus": 0.5}


class TestCountRange:
    def test_uncertain_candidates_give_a_range(self, science_graph):
        model = EntityModel(_THEORY_PROBS, choices=["count", "stop", "theory"])
        result = _run(science_graph, model, THEORIES, [])
        assert result.semantic.sure_names == ["General Relativity", "Universal Gravitation"]
        assert result.semantic.uncertain_names == ["Calculus"]
        assert result.rows == [{"count_distinct_v0_name": 2}]
        assert result.upper_rows == [{"count_distinct_v0_name": 3}]
        assert "between 2 and 3 distinct entities" in result.facts[0]["text"]
        assert "uncertain: Calculus" in result.facts[0]["text"]
        assert result.answer.startswith("Between 2 and 3 recorded in the graph: General Relativity, Universal Gravitation")
        assert "theory" in result.description

    def test_no_uncertain_candidates_give_a_single_number(self, science_graph):
        probs = {"General Relativity": 0.9, "Universal Gravitation": 0.85}
        model = EntityModel(probs, choices=["count", "stop", "theory"])
        result = _run(science_graph, model, THEORIES, [])
        assert result.upper_rows is None
        assert result.answer == "2 recorded in the graph: General Relativity, Universal Gravitation"
        assert "between" not in result.facts[0]["text"]

    def test_every_candidate_is_scored_once(self, science_graph):
        model = EntityModel(_THEORY_PROBS, choices=["count", "stop", "theory"])
        _run(science_graph, model, THEORIES, [])
        assert len(model.entity_calls) == len(set(model.entity_calls)) == 14

    def test_bands(self):
        assert REJECT_THRESHOLD < 0.5 < SURE_THRESHOLD


class TestGroupedSemanticFilter:
    def test_in_filter_is_applied_to_the_group_query(self, science_graph):
        # Edges into theories, per relation type.
        model = EntityModel(
            _THEORY_PROBS,
            choices=["group", "any:out", "stop", "count", "theory"],
            batch={"key:e0.type": 0.9},
        )
        result = _run(science_graph, model, "How many relations of each type point to theories?", [])
        assert result.rows == [
            {"e0_type": "DISCOVERED", "count_all": 1},
            {"e0_type": "EXTENDS", "count_all": 1},
            {"e0_type": "RELATED_TO", "count_all": 1},
        ]
        assert {r["e0_type"] for r in result.upper_rows} == {"BORN_IN", "DEVELOPED", "DISCOVERED", "EXTENDS", "RELATED_TO"}
        assert "e0_type=BORN_IN: count_all=up to 1" in result.answer
        assert "e0_type=DISCOVERED: count_all=1" in result.answer


class TestSkipAndCap:
    def test_candidate_cap_declines_the_plan(self, science_graph):
        model = EntityModel(_THEORY_PROBS, choices=["count", "stop", "theory"])
        assert _run(science_graph, model, THEORIES, [], max_candidates=5) is None
        assert model.entity_calls == []

    def test_no_predicate_leaves_the_plan_unchanged(self, science_graph):
        model = EntityModel(_THEORY_PROBS, choices=["count", "stop", "none"])
        result = _run(science_graph, model, "How many entities are there in the graph?", [])
        assert result.semantic is None and result.upper_rows is None
        assert result.plan.filters == []
        assert result.rows == [{"count_distinct_v0_name": 14}]
        assert model.entity_calls == []
