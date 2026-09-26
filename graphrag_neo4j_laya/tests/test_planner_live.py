"""
tests/test_planner_live.py

The guided query planner with the real Laya model, one test per question of
the labelled set (examples/data/aggregate_eval.json).

The other planner tests drive the planner with scripted or oracle models, so
they prove the machinery, not the choices Laya makes. These tests run the
same evaluation as `python -m graphrag.benchmarks.aggregate_planner_eval`
and fail every question the real model gets wrong: an aggregate question
fails when it is not routed to `aggregate`, when any planner step differs
from the labelled plan, or when the plan's rows differ from the labelled
rows; a non-aggregate question fails when it is misrouted.

Opt in with `pytest --live` (run_tests_report.cmd passes it).
"""

from __future__ import annotations

import pytest

from graphrag.benchmarks.aggregate_planner_eval import load_eval_set

pytestmark = pytest.mark.live

ITEMS = load_eval_set()["items"]


@pytest.fixture(scope="module")
def live_records():
    pytest.importorskip("laya", reason="the laya package is not installed")
    from graphrag.benchmarks.aggregate_planner_eval import run_live

    records, _ = run_live()
    return {r["id"]: r for r in records}


def _problems(rec: dict) -> list[str]:
    problems = []
    if not rec["route_correct"]:
        expected = "aggregate" if rec["intent"] == "aggregate" else rec["intent"]
        problems.append(f"route: {rec['routed']} (conf {rec['route_confidence']:.2f}), expected {expected}")
    if rec["intent"] != "aggregate":
        return problems
    if rec["plan_description"] is None:
        problems.append("planner returned no plan")
        return problems
    wrong = [step for step, ok in rec["steps"].items() if not ok]
    if wrong:
        problems.append("wrong steps: " + ", ".join(wrong))
    elif not rec["exact_plan_match"]:
        problems.append("plan differs from the labelled plan (limit)")
    if not rec["result_match"]:
        problems.append(f"rows: {rec['actual_rows']}, expected {rec['expected_rows']}")
    if problems:
        problems.append(f"plan: {rec['plan_description']}")
        problems.append(f"gold: {rec['gold_description']}")
    return problems


@pytest.mark.parametrize("item", ITEMS, ids=[f"{i['id']}-{i['lang']}-{i['category']}" for i in ITEMS])
def test_planner_question(live_records, item):
    rec = live_records[item["id"]]
    problems = _problems(rec)
    assert not problems, f"{item['question']!r}\n  " + "\n  ".join(problems)
