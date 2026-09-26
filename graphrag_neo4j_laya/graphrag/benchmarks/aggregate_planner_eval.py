"""
graphrag/benchmarks/aggregate_planner_eval.py

Step-level evaluation of the guided query planner on a labelled question set
(examples/data/aggregate_eval.json) over the quickstart science graph.

For every question it measures routing (is an aggregate question routed to
`aggregate`, is a non-aggregate one kept away from it) and, for aggregate
questions, how many planner steps match the labelled plan, whether the plan
matches exactly and whether its result on the graph matches the labelled
rows. The back-translation check is run on the labelled plan and on a
known-wrong variant, and the min- and product-based plan confidences are
compared on how well they separate correct plans from wrong ones.

Seeds come from the question set, so seed selection errors do not leak into
the planner numbers. For semantic-filter questions the labelled `members`
stand in for the per-candidate Noul answers, so the result match measures
the plan, not the candidate scoring.

Usage (run from graphrag_neo4j_laya/)
-----
    python -m graphrag.benchmarks.aggregate_planner_eval
    python -m graphrag.benchmarks.aggregate_planner_eval --out benchmarks/results/aggregate_eval.json -v

Output
------
    Terminal table with the summary and the flag targets
    JSON file with the summary and one record per question
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from graphrag.retrieval.planner.describe import describe_plan
from graphrag.retrieval.planner.plan import FieldRef, Filter, Having, Hop, Order, QueryPlan, validate_plan
from graphrag.retrieval.planner.planner import _parse_metric
from graphrag.retrieval.planner.render_kuzu import fetch
from graphrag.retrieval.planner.semantic_filter import PREDICATE_KINDS, describe_predicate, with_members
from graphrag.retrieval.router import QueryIntent

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT / "examples" / "data" / "aggregate_eval.json"
DEFAULT_OUT = Path("benchmarks/results/aggregate_planner_eval.json")

STEPS = ("operation", "start", "hop_types", "hop_directions", "stop", "filters", "keys", "metrics", "having",
         "semantic")
INTENTS = ("aggregate", "local", "multi_hop", "global")
CATEGORIES = ("simple", "grouped", "two_hop", "semantic", "non_aggregate")
DEFAULT_RANK_LIMIT = 5

# Flag decision inputs from the plan (U6), not a definition of done.
TARGETS = {"result_match": 0.80, "misrouting_rate": 0.0, "hop_directions": 0.90}


# ── Question set ─────────────────────────────────────────────────────────────

def load_eval_set(path: Path = DEFAULT_DATA) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _ref(text: str) -> FieldRef:
    var, fld = text.split(".")
    return FieldRef(var, fld)


def parse_plan(spec: dict[str, Any], row_limit: int = 200) -> QueryPlan:
    """Build a QueryPlan from a question-set plan spec."""
    hops = []
    for hop in spec.get("hops", []):
        rel_type, direction = hop.split(":")
        hops.append(Hop(None if rel_type == "any" else rel_type, direction))
    metrics = [_parse_metric(m) for m in spec.get("metrics", [])]
    plan = QueryPlan(
        operation=spec["operation"],
        start=spec.get("start"),
        hops=hops,
        filters=[Filter(_ref(f), op, v) for f, op, v in spec.get("filters", [])],
        keys=[_ref(k) for k in spec.get("keys", [])],
        metrics=metrics,
        having=[Having(_parse_metric(m), op, v) for m, op, v in spec.get("having", [])],
        limit=spec.get("limit", DEFAULT_RANK_LIMIT if spec["operation"] == "rank" else row_limit),
    )
    if plan.operation == "rank" and metrics:
        plan.order = [Order(metrics[0], descending=True)]
    validate_plan(plan)
    return plan


def describe_spec(spec: dict[str, Any], relation_schema: dict[str, str] | None = None) -> str:
    """The description the planner would give this spec, predicate included."""
    text = describe_plan(parse_plan(spec), relation_schema or {})
    return f"{text} ({describe_predicate(spec['semantic'])})" if spec.get("semantic") else text


def validate_item(item: dict[str, Any]) -> None:
    """Raise ValueError when a question-set record is malformed."""
    for key in ("id", "lang", "category", "intent", "question", "seeds"):
        if key not in item:
            raise ValueError(f"{item.get('id', '?')}: missing {key!r}")
    if item["intent"] not in INTENTS:
        raise ValueError(f"{item['id']}: unknown intent {item['intent']!r}")
    if item["category"] not in CATEGORIES:
        raise ValueError(f"{item['id']}: unknown category {item['category']!r}")
    if item["intent"] != "aggregate":
        return
    for key in ("plan", "wrong_plan", "expected"):
        if key not in item:
            raise ValueError(f"{item['id']}: aggregate item needs {key!r}")
    gold = parse_plan(item["plan"])
    parse_plan(item["wrong_plan"])
    if gold.start and gold.start not in item["seeds"]:
        raise ValueError(f"{item['id']}: plan start {gold.start!r} is not a seed")
    if item["plan"] == item["wrong_plan"]:
        raise ValueError(f"{item['id']}: wrong_plan equals plan")
    semantic = item["plan"].get("semantic")
    if semantic is not None and (semantic not in PREDICATE_KINDS or "members" not in item):
        raise ValueError(f"{item['id']}: a semantic plan needs a known kind and 'members'")


def build_graph(db, data: dict[str, Any], data_path: Path = DEFAULT_DATA) -> dict[str, str]:
    """Load the question set's graph into *db*; returns the relation schema."""
    source = json.loads((Path(data_path).parent / data["entities_from"]).read_text(encoding="utf-8"))
    db.create_schema()
    for name, description in source["entities"].items():
        db.upsert_node(name, properties={"description": description})
    for s, rel_type, t in data["edges"]:
        db.upsert_edge(s, t, rel_type)
    db.run_pagerank()
    db.run_community_detection()
    return source.get("schema", {})


# ── Comparison ───────────────────────────────────────────────────────────────

def _filter_set(filters: list[Filter]) -> set:
    return {(f.ref, f.op, tuple(f.value) if isinstance(f.value, list) else f.value) for f in filters}


def compare_steps(
    expected: QueryPlan,
    actual: QueryPlan | None,
    expected_semantic: str | None = None,
    actual_semantic: str | None = None,
) -> dict[str, bool]:
    if actual is None:
        return dict.fromkeys(STEPS, False)
    return {
        "operation":      actual.operation == expected.operation,
        "start":          actual.start == expected.start,
        "hop_types":      [h.rel_type for h in actual.hops] == [h.rel_type for h in expected.hops],
        "hop_directions": [h.direction for h in actual.hops] == [h.direction for h in expected.hops],
        "stop":           len(actual.hops) == len(expected.hops),
        "filters":        _filter_set(actual.filters) == _filter_set(expected.filters),
        "keys":           set(actual.keys) == set(expected.keys),
        "metrics":        actual.metrics == expected.metrics,
        "having":         set(actual.having) == set(expected.having),
        "semantic":       actual_semantic == expected_semantic,
    }


def is_exact(steps: dict[str, bool], expected: QueryPlan, actual: QueryPlan | None) -> bool:
    if actual is None or not all(steps.values()):
        return False
    return expected.operation != "rank" or actual.limit == expected.limit


def _normalise(rows: list[list[Any]], ordered: bool) -> list[list[Any]]:
    rows = [[round(v, 6) if isinstance(v, float) else v for v in row] for row in rows]
    return rows if ordered else sorted(rows, key=repr)


def result_rows(db, plan: QueryPlan) -> list[list[Any]]:
    rows, _ = fetch(db, plan)
    return _normalise([list(r.values()) for r in rows], ordered=plan.operation == "rank")


def expected_rows(item: dict[str, Any]) -> list[list[Any]]:
    return _normalise(item["expected"], ordered=item["plan"]["operation"] == "rank")


def separation(scores: list[float], labels: list[bool]) -> float | None:
    """Share of (correct, wrong) pairs where the correct plan scores higher (ties count half)."""
    pos = [s for s, ok in zip(scores, labels) if ok]
    neg = [s for s, ok in zip(scores, labels) if not ok]
    if not pos or not neg:
        return None
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


# ── Call counting ────────────────────────────────────────────────────────────

class CountingModel:
    """Decision-model proxy that counts calls per primitive."""

    _COUNTED = ("choice_detailed", "noul_detailed", "ask_batch")

    def __init__(self, model) -> None:
        self._model = model
        self.calls = dict.fromkeys(self._COUNTED, 0)

    def __getattr__(self, name: str):
        attr = getattr(self._model, name)
        if name in self._COUNTED:
            def counted(*args, **kwargs):
                self.calls[name] += 1
                return attr(*args, **kwargs)
            return counted
        return attr

    def snapshot(self) -> dict[str, int]:
        return dict(self.calls)


# ── Evaluation ───────────────────────────────────────────────────────────────

def evaluate(
    db,
    items: list[dict[str, Any]],
    *,
    router,
    planner,
    check: Callable[[str, str], float],
    relation_schema: dict[str, str] | None = None,
    route_threshold: float = 0.5,
    counter: CountingModel | None = None,
) -> list[dict[str, Any]]:
    """One record per question; see summarise() for the aggregate numbers."""
    schema = relation_schema or {}
    records = []
    for item in items:
        question, is_aggregate = item["question"], item["intent"] == "aggregate"
        before = counter.snapshot() if counter else None
        t0 = time.perf_counter()
        decision = router.route_detailed(question)
        routed_aggregate = (decision.intent == QueryIntent.AGGREGATE
                            and decision.confidence >= route_threshold)
        rec: dict[str, Any] = {
            "id": item["id"], "lang": item["lang"], "category": item["category"],
            "intent": item["intent"], "question": question,
            "routed": decision.intent.value, "route_confidence": decision.confidence,
            "route_correct": routed_aggregate if is_aggregate
                             else decision.intent.value == item["intent"],
            "misrouted": not is_aggregate and routed_aggregate,
            "route_ms": (time.perf_counter() - t0) * 1000,
        }
        if is_aggregate:
            rec.update(_evaluate_plan(db, item, planner, check, schema))
        if counter:
            after = counter.snapshot()
            rec["calls"] = {k: after[k] - before[k] for k in after}
        records.append(rec)
    return records


def _evaluate_plan(db, item, planner, check, schema) -> dict[str, Any]:
    question = item["question"]
    gold = parse_plan(item["plan"])
    gold_semantic = item["plan"].get("semantic")
    t0 = time.perf_counter()
    result = planner.plan(question, item["seeds"])
    plan_ms = (time.perf_counter() - t0) * 1000
    actual = result.plan if result else None
    actual_semantic = result.semantic if result else None
    steps = compare_steps(gold, actual, gold_semantic, actual_semantic)
    if actual is not None and actual_semantic is not None:
        # Labelled members stand in for the per-candidate Noul answers.
        actual = with_members(actual, item["members"] if actual_semantic == gold_semantic else [])
    asked = [t.probability for t in (result.trace if result else []) if not t.forced and not t.overridden]
    return {
        "plan_ms": plan_ms,
        "plan_description": result.description if result else None,
        # The hops in the same "TYPE:direction" spelling the labelled set uses,
        # so that a hop failure can be read off the record.
        "gold_hops": list(item["plan"].get("hops", [])),
        "plan_hops": [f"{h.rel_type or 'any'}:{h.direction}" for h in actual.hops] if actual else None,
        "steps": steps,
        "exact_plan_match": is_exact(steps, gold, actual),
        "result_match": actual is not None and result_rows(db, actual) == expected_rows(item),
        "confidence_min": result.confidence if result else None,
        "confidence_product": math.prod(asked) if result else None,
        "check_gold": check(question, describe_spec(item["plan"], schema)),
        "check_wrong": check(question, describe_spec(item["wrong_plan"], schema)),
    }


def _rate(values: list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarise(records: list[dict[str, Any]], check_threshold: float = 0.5) -> dict[str, Any]:
    agg = [r for r in records if r["intent"] == "aggregate"]
    non_agg = [r for r in records if r["intent"] != "aggregate"]
    planned = [r for r in agg if r["confidence_min"] is not None]
    labels = [r["exact_plan_match"] for r in planned]
    summary: dict[str, Any] = {
        "questions": len(records),
        "aggregate_questions": len(agg),
        "steps": {s: _rate([r["steps"][s] for r in agg]) for s in STEPS},
        "exact_plan_match": _rate([r["exact_plan_match"] for r in agg]),
        "result_match": _rate([r["result_match"] for r in agg]),
        "aggregate_route_recall": _rate([r["route_correct"] for r in agg]),
        "misrouting_rate": _rate([r["misrouted"] for r in non_agg]),
        "route_accuracy": _rate([r["route_correct"] for r in records]),
        "by_lang": {},
        "check": {
            "threshold": check_threshold,
            "gold_pass_rate": _rate([r["check_gold"] >= check_threshold for r in agg]),
            "wrong_reject_rate": _rate([r["check_wrong"] < check_threshold for r in agg]),
        },
        "confidence_separation": {
            "min": separation([r["confidence_min"] for r in planned], labels),
            "product": separation([r["confidence_product"] for r in planned], labels),
        },
        "latency_ms": {
            "route_mean": _mean([r["route_ms"] for r in records]),
            "plan_mean": _mean([r["plan_ms"] for r in agg]),
        },
    }
    for lang in sorted({r["lang"] for r in records}):
        lang_agg = [r for r in agg if r["lang"] == lang]
        summary["by_lang"][lang] = {
            "questions": sum(r["lang"] == lang for r in records),
            "exact_plan_match": _rate([r["exact_plan_match"] for r in lang_agg]),
            "result_match": _rate([r["result_match"] for r in lang_agg]),
            "route_accuracy": _rate([r["route_correct"] for r in records if r["lang"] == lang]),
        }
    counted = [r["calls"] for r in records if "calls" in r]
    if counted:
        summary["calls_mean"] = {k: _mean([c[k] for c in counted]) for k in counted[0]}
    summary["targets"] = {
        "result_match": _meets(summary["result_match"], TARGETS["result_match"], higher=True),
        "misrouting_rate": _meets(summary["misrouting_rate"], TARGETS["misrouting_rate"], higher=False),
        "hop_directions": _meets(summary["steps"]["hop_directions"], TARGETS["hop_directions"], higher=True),
    }
    return summary


def _meets(value: float | None, target: float, higher: bool) -> bool | None:
    if value is None:
        return None
    return value >= target if higher else value <= target


def format_report(summary: dict[str, Any]) -> str:
    def pct(v):
        return "   n/a" if v is None else f"{v * 100:5.1f}%"

    lines = [
        "=" * 60,
        f"Guided query planner — {summary['questions']} questions "
        f"({summary['aggregate_questions']} aggregate)",
        "=" * 60,
        "Step accuracy (aggregate questions)",
    ]
    lines += [f"  {s:<16} {pct(v)}" for s, v in summary["steps"].items()]
    lines += [
        f"Exact plan match     {pct(summary['exact_plan_match'])}",
        f"Result match         {pct(summary['result_match'])}",
        f"Aggregate recall     {pct(summary['aggregate_route_recall'])}",
        f"Misrouting rate      {pct(summary['misrouting_rate'])}",
        f"Route accuracy       {pct(summary['route_accuracy'])}",
        "By language",
    ]
    for lang, s in summary["by_lang"].items():
        lines.append(f"  {lang}: exact {pct(s['exact_plan_match'])}  result {pct(s['result_match'])}  "
                     f"route {pct(s['route_accuracy'])}  (n={s['questions']})")
    check = summary["check"]
    sep = summary["confidence_separation"]
    lines += [
        f"Check (threshold {check['threshold']:.2f}): gold pass {pct(check['gold_pass_rate'])}  "
        f"wrong reject {pct(check['wrong_reject_rate'])}",
        f"Confidence separation: min {pct(sep['min'])}  product {pct(sep['product'])}",
        f"Latency: route {summary['latency_ms']['route_mean']:.0f} ms  "
        f"plan {(summary['latency_ms']['plan_mean'] or 0):.0f} ms (mean)",
    ]
    if "calls_mean" in summary:
        lines.append("Calls per question: " + "  ".join(f"{k} {v:.1f}" for k, v in summary["calls_mean"].items()))
    lines.append("Flag targets: " + "  ".join(
        f"{k} {'ok' if v else 'n/a' if v is None else 'MISS'}" for k, v in summary["targets"].items()))
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    from config.settings import settings
    from graphrag.graph.kuzu_client import KuzuClient
    from graphrag.models.decision_factory import get_decision_model
    from graphrag.retrieval.planner.executor import AggregateExecutor
    from graphrag.retrieval.planner.planner import GuidedQueryPlanner
    from graphrag.retrieval.router import IntentRouter

    # Pinned to Laya with the aggregate intent offered, whatever .env says.
    settings.decision_model_backend = "laya"
    settings.aggregate_route_enabled = True

    data = load_eval_set(args.data)
    for item in data["items"]:
        validate_item(item)

    counter = CountingModel(get_decision_model())
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
         patch("graphrag.retrieval.router.get_decision_model", return_value=counter), \
         patch("graphrag.retrieval.planner.planner.get_decision_model", return_value=counter), \
         patch("graphrag.retrieval.planner.executor.get_decision_model", return_value=counter):
        db = KuzuClient(db_path=str(Path(tmp) / "eval.kuzu"))
        try:
            schema = build_graph(db, data, args.data)
            planner = GuidedQueryPlanner(db, schema)
            records = evaluate(
                db, data["items"],
                router=IntentRouter(),
                planner=planner,
                check=AggregateExecutor(db, planner).check,
                relation_schema=schema,
                route_threshold=settings.aggregate_route_min_confidence,
                counter=counter,
            )
        finally:
            db.close()

    summary = summarise(records, settings.aggregate_check_min_confidence)
    print(format_report(summary))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": summary, "records": records}, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"\nWritten to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
