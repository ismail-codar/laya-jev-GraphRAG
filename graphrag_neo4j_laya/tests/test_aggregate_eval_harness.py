"""
tests/test_aggregate_eval_harness.py

The labelled question set and the planner evaluation harness, driven by an
oracle router/planner so the numbers are known in advance.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from graphrag.benchmarks.aggregate_planner_eval import (
    STEPS, CountingModel, evaluate, expected_rows, format_report, load_eval_set, parse_plan,
    result_rows, separation, summarise, validate_item,
)
from graphrag.retrieval.planner.plan import Hop
from graphrag.retrieval.planner.planner import PlannerResult, StepTrace
from graphrag.retrieval.router import QueryIntent, RouteDecision
from tests.conftest import SCIENCE_EDGES
from tests.scripted_model import ScriptedModel

DATA = load_eval_set()
ITEMS = DATA["items"]
AGGREGATE = [i for i in ITEMS if i["intent"] == "aggregate"]


class OracleRouter:
    def __init__(self, overrides: dict[str, QueryIntent] | None = None) -> None:
        self.intents = {i["question"]: QueryIntent(i["intent"]) for i in ITEMS}
        self.intents.update(overrides or {})

    def route_detailed(self, question):
        return RouteDecision(self.intents[question], 0.9, QueryIntent.LOCAL)


class OraclePlanner:
    def __init__(self, plans: dict[str, object] | None = None) -> None:
        self.plans = {i["question"]: parse_plan(i["plan"]) for i in AGGREGATE}
        self.plans.update(plans or {})

    def plan(self, question, seeds):
        return PlannerResult(self.plans[question], [StepTrace("operation", ["count"], "count", 0.9)], 0.9)


def _gold_check(question, description):
    """High for the labelled plan's description, low for anything else."""
    gold = {i["question"]: i for i in AGGREGATE}[question]
    from graphrag.retrieval.planner.describe import describe_plan
    return 0.9 if description == describe_plan(parse_plan(gold["plan"]), {}) else 0.1


def _run(db, router=None, planner=None):
    records = evaluate(db, ITEMS, router=router or OracleRouter(), planner=planner or OraclePlanner(),
                       check=_gold_check)
    return records, summarise(records)


class TestQuestionSet:
    @pytest.mark.parametrize("item", ITEMS, ids=[i["id"] for i in ITEMS])
    def test_record_passes_schema_check(self, item):
        validate_item(item)

    def test_composition(self):
        counts = {c: sum(i["category"] == c for i in ITEMS) for c in ("simple", "grouped", "two_hop", "non_aggregate")}
        assert counts == {"simple": 10, "grouped": 6, "two_hop": 4, "non_aggregate": 10}
        assert sum(i["lang"] == "tr" for i in ITEMS) >= len(ITEMS) / 3
        assert len({i["id"] for i in ITEMS}) == len(ITEMS)

    def test_graph_matches_the_test_fixture(self):
        assert [tuple(e) for e in DATA["edges"]] == SCIENCE_EDGES

    @pytest.mark.parametrize("item", AGGREGATE, ids=[i["id"] for i in AGGREGATE])
    def test_labelled_plan_reproduces_expected_rows(self, science_graph, item):
        assert result_rows(science_graph, parse_plan(item["plan"])) == expected_rows(item)

    def test_malformed_record_is_rejected(self):
        bad = dict(AGGREGATE[0], plan={"operation": "count", "hops": ["any:sideways"]})
        with pytest.raises(ValueError):
            validate_item(bad)
        with pytest.raises(ValueError):
            validate_item({k: v for k, v in AGGREGATE[0].items() if k != "wrong_plan"})


class TestHarness:
    def test_oracle_scores_everything_correct(self, science_graph):
        _, summary = _run(science_graph)
        assert all(v == 1.0 for v in summary["steps"].values())
        assert summary["exact_plan_match"] == summary["result_match"] == 1.0
        assert summary["misrouting_rate"] == 0.0
        assert summary["aggregate_route_recall"] == 1.0
        assert summary["check"] == {"threshold": 0.5, "gold_pass_rate": 1.0, "wrong_reject_rate": 1.0}
        assert all(summary["targets"].values())
        assert set(summary["by_lang"]) == {"en", "tr"}

    def test_wrong_direction_only_drops_direction_and_result(self, science_graph):
        item = next(i for i in AGGREGATE if i["id"] == "s02")
        gold = parse_plan(item["plan"])
        flipped = replace(gold, hops=[Hop("BORN_IN", "in")])
        records, summary = _run(science_graph, planner=OraclePlanner({item["question"]: flipped}))
        n = len(AGGREGATE)
        assert summary["steps"]["hop_directions"] == pytest.approx((n - 1) / n)
        assert all(summary["steps"][s] == 1.0 for s in STEPS if s != "hop_directions")
        rec = next(r for r in records if r["id"] == "s02")
        assert rec["result_match"] is False and rec["exact_plan_match"] is False
        assert summary["result_match"] == pytest.approx((n - 1) / n)

    def test_non_aggregate_routed_to_aggregate_counts_as_misrouting(self, science_graph):
        item = next(i for i in ITEMS if i["id"] == "n01")
        _, summary = _run(science_graph, router=OracleRouter({item["question"]: QueryIntent.AGGREGATE}))
        assert summary["misrouting_rate"] == pytest.approx(0.1)
        assert summary["targets"]["misrouting_rate"] is False

    def test_declined_plan_counts_as_wrong(self, science_graph):
        class Declining(OraclePlanner):
            def plan(self, question, seeds):
                return None

        _, summary = _run(science_graph, planner=Declining())
        assert summary["result_match"] == 0.0
        assert summary["confidence_separation"] == {"min": None, "product": None}

    def test_call_counts_are_recorded(self, science_graph):
        counter = CountingModel(ScriptedModel())
        counter.noul_detailed("ctx", "q")
        records = evaluate(science_graph, ITEMS[:1], router=OracleRouter(), planner=OraclePlanner(),
                           check=lambda q, d: counter.noul_detailed(q, d).score, counter=counter)
        assert records[0]["calls"] == {"choice_detailed": 0, "noul_detailed": 2, "ask_batch": 0}

    def test_report_renders(self, science_graph):
        _, summary = _run(science_graph)
        report = format_report(summary)
        assert "Exact plan match" in report and "Flag targets" in report


def test_separation():
    assert separation([0.9, 0.8, 0.2], [True, True, False]) == 1.0
    assert separation([0.5, 0.5], [True, False]) == 0.5
    assert separation([0.9], [True]) is None
