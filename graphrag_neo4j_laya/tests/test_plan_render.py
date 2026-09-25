"""
tests/test_plan_render.py

QueryPlan validation, Kùzu Cypher rendering and plan descriptions. Rendering
tests execute against the quickstart fixture graph so every number from
AGGREGATION_METHODS.md is reproduced by a real query.
"""

from __future__ import annotations

import pytest

from graphrag.retrieval.planner.plan import (
    FieldRef, Filter, Having, Hop, Metric, Order, QueryPlan, validate_plan,
)
from graphrag.retrieval.planner.render_kuzu import fetch, render
from graphrag.retrieval.planner.describe import describe_plan


def _ae1() -> QueryPlan:
    # "Calculus'u kaç kişi geliştirdi?"
    return QueryPlan(operation="count", start="Calculus", hops=[Hop("DEVELOPED", "in")])


def _ae3() -> QueryPlan:
    # "Einstein'ın doğduğu yerde doğan başka kim var?"
    return QueryPlan(
        operation="list",
        start="Albert Einstein",
        hops=[Hop("BORN_IN", "out"), Hop("BORN_IN", "in")],
        filters=[Filter(FieldRef("v2", "name"), "!=", "Albert Einstein")],
    )


def _grouped(keys, metrics, **kw) -> QueryPlan:
    return QueryPlan(operation="group", start=None, hops=[Hop(None, "out")], keys=keys, metrics=metrics, **kw)


class TestRender:
    def test_ae1_values_are_parameters(self):
        rendered = render(_ae1())
        assert "Calculus" not in rendered.cypher
        assert "DEVELOPED" not in rendered.cypher
        assert set(rendered.params.values()) >= {"Calculus", "DEVELOPED"}
        assert "<-[e0:RELATES_TO]-" in rendered.cypher

    def test_ae1_count(self, science_graph):
        rows, truncated = fetch(science_graph, _ae1())
        # Newton's edge was aligned as BORN_IN, so only Leibniz is recorded.
        assert rows == [{"count_distinct_v1_name": 1}]
        assert truncated is False

    def test_ae2_group_by_relation_type(self, science_graph):
        plan = _grouped([FieldRef("e0", "type")], [Metric("count")])
        rows, _ = fetch(science_graph, plan)
        counts = {r["e0_type"]: r["count_all"] for r in rows}
        assert counts["BORN_IN"] == 3 and counts["DISCOVERED"] == 2
        assert sum(counts.values()) == 12

    def test_multi_level_multi_metric_table(self, science_graph):
        pr = FieldRef("v1", "pagerank")
        plan = _grouped(
            [FieldRef("v0", "communityId"), FieldRef("v0", "name"), FieldRef("e0", "type")],
            [Metric("sum", pr), Metric("count"), Metric("avg", pr), Metric("min", pr),
             Metric("max", pr), Metric("count_distinct", FieldRef("v1", "name"))],
        )
        rendered = render(plan)
        # DISTINCT aggregates must come last (Kùzu 0.11.3 zeroes those after it).
        assert rendered.cypher.index("count(DISTINCT") > rendered.cypher.index("max(")
        rows, _ = fetch(science_graph, plan)
        assert len(rows) == 11
        newton = next(r for r in rows if r["v0_name"] == "Isaac Newton" and r["e0_type"] == "BORN_IN")
        assert newton["count_all"] == 2
        assert newton["count_distinct_v1_name"] == 2
        assert round(newton["sum_v1_pagerank"], 4) == 0.1757
        assert round(newton["avg_v1_pagerank"], 4) == 0.0878
        assert round(newton["min_v1_pagerank"], 4) == 0.0650
        assert round(newton["max_v1_pagerank"], 4) == 0.1106

    def test_having_keeps_newton_born_in_only(self, science_graph):
        distinct_targets = Metric("count_distinct", FieldRef("v1", "name"))
        plan = _grouped(
            [FieldRef("v0", "name"), FieldRef("e0", "type")], [distinct_targets],
            having=[Having(distinct_targets, ">", 1)],
        )
        rows, _ = fetch(science_graph, plan)
        assert rows == [{"v0_name": "Isaac Newton", "e0_type": "BORN_IN", "count_distinct_v1_name": 2}]

    def test_numeric_filter(self, science_graph):
        plan = _grouped(
            [FieldRef("e0", "type")], [Metric("count")],
            filters=[Filter(FieldRef("v1", "pagerank"), ">", 0.1)],
        )
        rows, _ = fetch(science_graph, plan)
        assert {r["e0_type"] for r in rows} == {
            "EXTENDS", "RELATED_TO", "PREDICTED", "DISCOVERED", "DEVELOPED", "BORN_IN",
        }

    def test_rank_orders_by_first_metric_desc(self, science_graph):
        plan = QueryPlan(
            operation="rank", start=None, hops=[Hop(None, "out")],
            keys=[FieldRef("v0", "name")], metrics=[Metric("count")], limit=3,
        )
        rows, _ = fetch(science_graph, plan)
        assert [r["v0_name"] for r in rows[:2]] == ["Isaac Newton", "General Relativity"]
        assert [r["count_all"] for r in rows] == [4, 3, 2]

    def test_ae3_two_hops_empty_result(self, science_graph):
        rows, truncated = fetch(science_graph, _ae3())
        assert rows == [] and truncated is False

    def test_two_hops_without_filter_returns_start(self, science_graph):
        plan = _ae3()
        plan.filters = []
        rows, _ = fetch(science_graph, plan)
        assert rows == [{"v2_name": "Albert Einstein"}]

    def test_in_filter(self, science_graph):
        plan = QueryPlan(
            operation="count", start=None, hops=[Hop(None, "out")],
            filters=[Filter(FieldRef("v0", "name"), "in", ["Isaac Newton", "Gottfried Leibniz"])],
        )
        rows, _ = fetch(science_graph, plan)
        # Newton reaches 4 entities; Leibniz only reaches Calculus, which Newton shares.
        assert rows == [{"count_distinct_v1_name": 4}]

    def test_count_without_hops_counts_entities(self, science_graph):
        rows, _ = fetch(science_graph, QueryPlan(operation="count", start=None))
        assert rows == [{"count_distinct_v0_name": 14}]

    def test_truncation(self, science_graph):
        plan = QueryPlan(operation="list", start="Isaac Newton", hops=[Hop(None, "out")], limit=2)
        assert render(plan).params["limit"] == 3
        rows, truncated = fetch(science_graph, plan)
        assert len(rows) == 2 and truncated is True

    def test_exact_limit_is_not_truncated(self, science_graph):
        plan = QueryPlan(operation="list", start="Isaac Newton", hops=[Hop(None, "out")], limit=4)
        rows, truncated = fetch(science_graph, plan)
        assert len(rows) == 4 and truncated is False

    def test_injection_attempt_is_only_a_parameter(self, science_graph):
        payload = "x' OR 1=1 // MATCH (n) DETACH DELETE n"
        plan = QueryPlan(operation="count", start=payload, hops=[Hop(None, "out")])
        assert payload not in render(plan).cypher
        rows, _ = fetch(science_graph, plan)
        assert rows == [{"count_distinct_v1_name": 0}]


class TestValidation:
    @pytest.mark.parametrize("plan", [
        QueryPlan(operation="count", start=None, filters=[Filter(FieldRef("v0", "description"), "=", "x")]),
        QueryPlan(operation="count", start=None, filters=[Filter(FieldRef("v0", "name"), "LIKE", "x")]),
        QueryPlan(operation="count", start=None, hops=[Hop(None, "out")],
                  filters=[Filter(FieldRef("v3", "name"), "=", "x")]),
        QueryPlan(operation="count", start=None, filters=[Filter(FieldRef("e0", "type"), "=", "x")]),
        QueryPlan(operation="count", start=None, filters=[Filter(FieldRef("v0", "name"), "in", "x")]),
        QueryPlan(operation="group", start=None, metrics=[Metric("count")]),
        QueryPlan(operation="group", start=None, keys=[FieldRef("v0", "name")]),
        QueryPlan(operation="group", start=None, keys=[FieldRef("v0", "name")],
                  metrics=[Metric("sum", FieldRef("v0", "name"))]),
        QueryPlan(operation="rank", start=None, keys=[FieldRef("v0", "name")]),
        QueryPlan(operation="count", start=None, hops=[Hop(None, "both")]),
        QueryPlan(operation="explode", start=None),
        QueryPlan(operation="count", start=None, limit=0),
        QueryPlan(operation="group", start=None, keys=[FieldRef("v0", "name")], metrics=[Metric("count")],
                  order=[Order(FieldRef("v0", "pagerank"))]),
    ])
    def test_invalid_plans_are_rejected(self, plan):
        with pytest.raises(ValueError):
            validate_plan(plan)

    def test_render_validates(self):
        with pytest.raises(ValueError):
            render(QueryPlan(operation="count", start=None, filters=[Filter(FieldRef("v0", "x"), "=", 1)]))


class TestDescribe:
    def test_ae1_description_mentions_type_direction_and_operation(self):
        text = describe_plan(_ae1(), {"DEVELOPED": "formulated, invented or developed an idea"})
        assert "Calculus" in text and "DEVELOPED" in text
        assert "formulated, invented or developed an idea" in text
        assert "count" in text.lower()
        assert "to it" in text  # incoming direction

    def test_group_description_names_keys_and_metrics(self):
        plan = _grouped([FieldRef("e0", "type")], [Metric("avg", FieldRef("v1", "pagerank"))])
        text = describe_plan(plan, {})
        assert "every entity" in text
        assert "relation type" in text and "average" in text

    def test_filter_description(self):
        text = describe_plan(_ae3(), {})
        assert "not equal to 'Albert Einstein'" in text
