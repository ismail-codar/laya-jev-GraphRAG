"""
graphrag/retrieval/planner/render_kuzu.py

Render a QueryPlan to parameterised Kùzu Cypher.

Kùzu stores every relation in one RELATES_TO table with a `type` string
property, so relation types are bound as parameters like any other value.
Only whitelisted identifiers from plan.py are interpolated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .plan import FieldRef, Metric, QueryPlan, validate_plan

_COMPARISON = {"=": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">=", "in": "IN"}


@dataclass
class RenderedQuery:
    cypher: str
    params: dict[str, Any]
    key_columns: list[str] = field(default_factory=list)
    metric_columns: list[str] = field(default_factory=list)


class _Params:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def add(self, value: Any) -> str:
        name = f"p{len(self.values)}"
        self.values[name] = list(value) if isinstance(value, tuple) else value
        return f"${name}"


def _field(ref: FieldRef) -> str:
    return f"{ref.var}.{ref.field}"


def _metric_expr(metric: Metric) -> str:
    if metric.ref is None:
        return "count(*)"
    if metric.op == "count_distinct":
        return f"count(DISTINCT {_field(metric.ref)})"
    return f"{metric.op}({_field(metric.ref)})"


def _comparison(expr: str, op: str, value: Any, params: _Params) -> str:
    if op == "not_in":
        return f"NOT {expr} IN {params.add(value)}"
    return f"{expr} {_COMPARISON[op]} {params.add(value)}"


def _match(plan: QueryPlan, params: _Params) -> str:
    pattern = "(v0:Entity)"
    where = []
    for i, hop in enumerate(plan.hops):
        edge = f"[e{i}:RELATES_TO]"
        arrow = f"-{edge}->" if hop.direction == "out" else f"<-{edge}-"
        pattern += f"{arrow}(v{i + 1}:Entity)"
        if hop.rel_type is not None:
            where.append(f"e{i}.type = {params.add(hop.rel_type)}")
    if plan.start is not None:
        where.insert(0, f"v0.name = {params.add(plan.start)}")
    for f in plan.filters:
        where.append(_comparison(_field(f.ref), f.op, f.value, params))
    return f"MATCH {pattern}" + (f" WHERE {' AND '.join(where)}" if where else "")


def _shape(plan: QueryPlan) -> tuple[list[FieldRef], list[Metric], bool]:
    """Resolve operation defaults into (keys, metrics, distinct_rows)."""
    target_name = FieldRef(plan.target, "name")
    if plan.operation == "count":
        return [], [Metric("count_distinct", target_name)], False
    if plan.operation == "list":
        return [target_name], [], True
    return list(plan.keys), list(plan.metrics), False


def render(plan: QueryPlan) -> RenderedQuery:
    validate_plan(plan)
    params = _Params()
    match = _match(plan, params)
    keys, metrics, distinct = _shape(plan)

    # count(DISTINCT) last: Kùzu 0.11.3 silently zeroes aggregates listed after it.
    metrics = sorted(metrics, key=lambda m: m.op == "count_distinct")
    projections = [f"{_field(k)} AS {k.alias}" for k in keys]
    projections += [f"{_metric_expr(m)} AS {m.alias}" for m in metrics]
    columns = [k.alias for k in keys] + [m.alias for m in metrics]

    if plan.having:
        conditions = [_comparison(h.metric.alias, h.op, h.value, params) for h in plan.having]
        body = (
            f"{match} WITH {', '.join(projections)} WHERE {' AND '.join(conditions)} "
            f"RETURN {', '.join(columns)}"
        )
    else:
        body = f"{match} RETURN {'DISTINCT ' if distinct else ''}{', '.join(projections)}"

    order = [f"{o.key.alias}{' DESC' if o.descending else ''}" for o in plan.order]
    if not order and plan.operation == "rank":
        order = [f"{metrics[0].alias} DESC"] + [k.alias for k in keys]
    if not order:
        order = [k.alias for k in keys]
    if order:
        body += f" ORDER BY {', '.join(order)}"

    # One extra row tells the caller whether the result was truncated.
    params.values["limit"] = plan.limit + 1
    body += " LIMIT $limit"
    return RenderedQuery(
        cypher=body,
        params=params.values,
        key_columns=[k.alias for k in keys],
        metric_columns=[m.alias for m in metrics],
    )


def fetch(db, plan: QueryPlan) -> tuple[list[dict[str, Any]], bool]:
    """Render, execute and trim to `plan.limit`. Returns (rows, truncated)."""
    rendered = render(plan)
    rows = db.run_read_query(rendered.cypher, rendered.params)
    truncated = len(rows) > plan.limit
    return rows[: plan.limit], truncated
