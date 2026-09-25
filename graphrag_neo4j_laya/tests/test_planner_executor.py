"""
tests/test_planner_executor.py

AggregateExecutor: back-translation check, single repair, execution on the
fixture graph, fact nodes and template answers.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from graphrag.retrieval.planner.executor import AggregateExecutor
from graphrag.retrieval.planner.plan import FieldRef, Hop
from graphrag.retrieval.planner.planner import GuidedQueryPlanner
from tests.scripted_model import ScriptedModel

AE1 = "Calculus'u kaç kişi geliştirdi?"


@contextmanager
def _model(model):
    with patch("graphrag.retrieval.planner.planner.get_decision_model", return_value=model), \
         patch("graphrag.retrieval.planner.executor.get_decision_model", return_value=model):
        yield


def _run(db, model, question, seeds, **kw):
    with _model(model):
        return AggregateExecutor(db, GuidedQueryPlanner(db), **kw).run(question, seeds)


class TestCheckAndRepair:
    def test_passing_check_executes_once(self, science_graph):
        model = ScriptedModel(choices=["count", "anchor", "DEVELOPED:in", "stop"], nouls=[0.9])
        result = _run(science_graph, model, AE1, ["Calculus"])
        assert result.rows == [{"count_distinct_v1_name": 1}]
        assert result.repaired is False
        assert model.noul_calls == 1

    def test_failed_check_repairs_weakest_step(self, science_graph):
        # hop0 is the least certain step (0.5); its runner-up DEVELOPED:in is tried.
        model = ScriptedModel(
            choices=["count", "anchor", "BORN_IN:in", "stop", "none"] * 2,
            probs=[0.9, 0.9, 0.5, 0.9, 0.9] * 2,
            nouls=[0.2, 0.9],
        )
        result = _run(science_graph, model, AE1, ["Calculus"])
        assert result.repaired is True
        assert result.plan.hops == [Hop("DEVELOPED", "in")]
        assert result.names == ["Gottfried Leibniz"]

    def test_failed_repair_returns_none(self, science_graph):
        model = ScriptedModel(choices=["count", "anchor", "BORN_IN:in", "stop"] * 2,
                              probs=[0.9, 0.9, 0.5, 0.9] * 2, nouls=[0.2, 0.2])
        assert _run(science_graph, model, AE1, ["Calculus"]) is None

    def test_low_confidence_plan_is_not_checked(self, science_graph):
        model = ScriptedModel(choices=["count", "anchor", "DEVELOPED:in", "stop"], prob=0.2)
        assert _run(science_graph, model, AE1, ["Calculus"], min_confidence=0.3) is None
        assert model.noul_calls == 0


class TestFactsAndAnswers:
    def test_count_answer_names_the_entities(self, science_graph):
        model = ScriptedModel(choices=["count", "anchor", "DEVELOPED:in", "stop"], nouls=[0.9])
        result = _run(science_graph, model, AE1, ["Calculus"])
        assert result.answer.startswith("1 ")
        assert "Gottfried Leibniz" in result.answer
        assert len(result.facts) == 1
        fact = result.facts[0]
        assert set(fact) == {"name", "text", "score"} and fact["score"] == 1.0
        assert "1" in fact["text"] and "Gottfried Leibniz" in fact["text"]

    def test_group_result_has_one_fact_per_row(self, science_graph):
        model = ScriptedModel(choices=["group", "any:out", "stop", "count"],
                              batch={"key:e0.type": 0.9}, nouls=[0.9])
        result = _run(science_graph, model, "Her ilişki tipinde kaç kenar var?", [])
        assert result.plan.keys == [FieldRef("e0", "type")]
        assert len(result.facts) == 1 + len(result.rows)
        born_in = next(f for f in result.facts if "BORN_IN" in f["text"])
        assert "3" in born_in["text"]
        assert "BORN_IN" in result.answer

    def test_truncated_list_does_not_claim_completeness(self, science_graph):
        model = ScriptedModel(choices=["list", "anchor", "any:out", "stop"], nouls=[0.9])
        with _model(model):
            executor = AggregateExecutor(science_graph, GuidedQueryPlanner(science_graph, row_limit=2))
            result = executor.run("Newton neyle ilişkili?", ["Isaac Newton"])
        assert result.truncated is True
        assert "complete" not in result.facts[0]["text"]
        assert "at least 3" in result.facts[0]["text"]
        assert "more" in result.answer

    def test_empty_result_is_reported_not_failed(self, science_graph):
        model = ScriptedModel(choices=["list", "anchor", "BORN_IN:out", "BORN_IN:in", "stop"],
                              nouls=[0.9, 0.9])
        result = _run(science_graph, model, "Einstein'ın doğduğu yerde doğan başka kim var?",
                      ["Albert Einstein"])
        assert result.rows == []
        assert "No matching entities" in result.facts[0]["text"]
        assert "No matching entities" in result.answer


class TestFailures:
    def test_db_error_returns_none(self, science_graph, caplog):
        db = MagicMock(wraps=science_graph)
        db.run_read_query.side_effect = RuntimeError("disk gone")
        model = ScriptedModel(choices=["count", "anchor", "DEVELOPED:in", "stop"], nouls=[0.9])
        with caplog.at_level(logging.WARNING):
            assert _run(db, model, AE1, ["Calculus"]) is None
        assert "disk gone" in caplog.text

    def test_planner_failure_returns_none(self, science_graph):
        planner = MagicMock()
        planner.plan.return_value = None
        with _model(ScriptedModel()):
            assert AggregateExecutor(science_graph, planner).run(AE1, ["Calculus"]) is None
