"""
graphrag/retrieval/planner/describe.py

Template back-translation of a (partial) QueryPlan into English. Used as the
"plan so far" context for each planner step and for the final check that the
plan answers the user's question. English because option descriptions and
graph content are English.
"""

from __future__ import annotations

from .plan import FieldRef, Metric, QueryPlan

_OP_WORDS = {
    "=": "equal to", "!=": "not equal to", "<": "less than", "<=": "at most",
    ">": "greater than", ">=": "at least", "in": "one of", "not_in": "none of",
}
_METRIC_WORDS = {
    "count": "number of", "count_distinct": "number of distinct", "sum": "total",
    "avg": "average", "min": "minimum", "max": "maximum", "collect": "list of every",
}
_FIELD_WORDS = {"name": "name", "pagerank": "PageRank", "communityId": "community", "type": "relation type"}


def _var(var: str) -> str:
    kind, index = var[0], int(var[1:])
    if kind == "e":
        return f"the step-{index + 1} relation"
    return "the start entity" if index == 0 else f"the step-{index} entity"


def _ref(ref: FieldRef) -> str:
    return f"{_FIELD_WORDS.get(ref.field, ref.field)} of {_var(ref.var)}"


def _metric(metric: Metric) -> str:
    if metric.ref is None:
        return "number of matches"
    return f"{_METRIC_WORDS[metric.op]} {_ref(metric.ref)}"


def _value(value) -> str:
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    return f"'{value}'" if isinstance(value, str) else str(value)


def describe_start(plan: QueryPlan) -> str:
    return f"Start at the entity '{plan.start}'" if plan.start else "Start from every entity"


def describe_hops(plan: QueryPlan, relation_schema: dict[str, str]) -> list[str]:
    parts = []
    for hop in plan.hops:
        if hop.rel_type is None:
            rel = "any relation"
        else:
            desc = relation_schema.get(hop.rel_type)
            rel = f"a {hop.rel_type} relation" + (f" ({desc})" if desc else "")
        if hop.direction == "out":
            parts.append(f"step to entities it has {rel} to")
        else:
            parts.append(f"step to entities that have {rel} to it")
    return parts


def describe_filters(plan: QueryPlan) -> list[str]:
    return [
        f"keep only matches where the {_ref(f.ref)} is {_OP_WORDS[f.op]} {_value(f.value)}"
        for f in plan.filters
    ]


def describe_result(plan: QueryPlan) -> str:
    target = _var(plan.target)
    if plan.operation == "count":
        return f"count the distinct entities reached ({target})"
    if plan.operation == "list":
        return f"list the distinct entities reached ({target})"
    keys = ", ".join(_ref(k) for k in plan.keys) or "nothing"
    metrics = ", ".join(_metric(m) for m in plan.metrics) or "nothing"
    verb = "rank by" if plan.operation == "rank" else "compute"
    text = f"group by {keys} and {verb} {metrics}"
    for h in plan.having:
        text += f", keeping groups whose {_metric(h.metric)} is {_OP_WORDS[h.op]} {_value(h.value)}"
    return text


def describe_plan(plan: QueryPlan, relation_schema: dict[str, str] | None = None) -> str:
    parts = [describe_start(plan)]
    parts += describe_hops(plan, relation_schema or {})
    parts += describe_filters(plan)
    parts.append(describe_result(plan))
    return ", then ".join(parts) + "."
