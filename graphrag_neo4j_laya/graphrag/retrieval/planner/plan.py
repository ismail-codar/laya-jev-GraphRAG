"""
graphrag/retrieval/planner/plan.py

Typed query plan for the guided query planner.

The planner never writes Cypher. It builds a QueryPlan out of choices the
decision model made among code-generated candidates, and a dialect renderer
turns the plan into a parameterised query. Every identifier a renderer may
emit (fields, operators, functions) comes from the whitelists below; every
value travels as a query parameter.

Variables:
    v0 .. vN   nodes; v0 is the start, v{i+1} is reached by hop i
    e0 .. eN-1 the edge traversed by hop i
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

OPERATIONS = ("count", "list", "rank", "group")
DIRECTIONS = ("out", "in")
NODE_FIELDS = ("name", "pagerank", "communityId")
EDGE_FIELDS = ("type",)
NUMERIC_FIELDS = ("pagerank", "communityId")
FILTER_OPS = ("=", "!=", "<", "<=", ">", ">=", "in", "not_in")
METRIC_OPS = ("count", "count_distinct", "sum", "avg", "min", "max")
NUMERIC_METRIC_OPS = ("sum", "avg", "min", "max")
MAX_LIMIT = 10_000

_VAR_RE = re.compile(r"^([ve])(\d+)$")


@dataclass(frozen=True)
class Hop:
    """One traversal step; `rel_type=None` follows any relation type."""
    rel_type: str | None
    direction: str = "out"


@dataclass(frozen=True)
class FieldRef:
    var: str
    field: str

    @property
    def alias(self) -> str:
        return f"{self.var}_{self.field}"


@dataclass(frozen=True)
class Filter:
    ref: FieldRef
    op: str
    value: Any


@dataclass(frozen=True)
class Metric:
    """`op="count"` with `ref=None` counts rows (edges or paths)."""
    op: str
    ref: FieldRef | None = None

    @property
    def alias(self) -> str:
        if self.ref is None:
            return f"{self.op}_all"
        return f"{self.op}_{self.ref.alias}"


@dataclass(frozen=True)
class Having:
    metric: Metric
    op: str
    value: Any


@dataclass(frozen=True)
class Order:
    key: FieldRef | Metric
    descending: bool = False


@dataclass
class QueryPlan:
    operation: str
    start: str | None = None
    hops: list[Hop] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    keys: list[FieldRef] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    having: list[Having] = field(default_factory=list)
    order: list[Order] = field(default_factory=list)
    limit: int = 200

    @property
    def target(self) -> str:
        """The last node variable, i.e. what count/list operate on."""
        return f"v{len(self.hops)}"


def _check_ref(ref: FieldRef, plan: QueryPlan) -> None:
    m = _VAR_RE.match(ref.var)
    if not m:
        raise ValueError(f"unknown variable {ref.var!r}")
    kind, index = m.group(1), int(m.group(2))
    if kind == "v":
        if index > len(plan.hops):
            raise ValueError(f"variable {ref.var!r} does not exist in a {len(plan.hops)}-hop plan")
        if ref.field not in NODE_FIELDS:
            raise ValueError(f"field {ref.field!r} is not allowed on nodes")
    else:
        if index >= len(plan.hops):
            raise ValueError(f"variable {ref.var!r} does not exist in a {len(plan.hops)}-hop plan")
        if ref.field not in EDGE_FIELDS:
            raise ValueError(f"field {ref.field!r} is not allowed on edges")


def _check_metric(metric: Metric, plan: QueryPlan) -> None:
    if metric.op not in METRIC_OPS:
        raise ValueError(f"unknown metric {metric.op!r}")
    if metric.ref is None:
        if metric.op != "count":
            raise ValueError(f"metric {metric.op!r} needs a field")
        return
    _check_ref(metric.ref, plan)
    if metric.op in NUMERIC_METRIC_OPS and metric.ref.field not in NUMERIC_FIELDS:
        raise ValueError(f"metric {metric.op!r} needs a numeric field, got {metric.ref.field!r}")


def _check_comparison(op: str, value: Any) -> None:
    if op not in FILTER_OPS:
        raise ValueError(f"unknown operator {op!r}")
    if op in ("in", "not_in") and not isinstance(value, (list, tuple)):
        raise ValueError(f"operator {op!r} needs a list value")


def validate_plan(plan: QueryPlan) -> None:
    """Raise ValueError when the plan cannot be rendered safely."""
    if plan.operation not in OPERATIONS:
        raise ValueError(f"unknown operation {plan.operation!r}")
    if not 1 <= plan.limit <= MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    for hop in plan.hops:
        if hop.direction not in DIRECTIONS:
            raise ValueError(f"unknown direction {hop.direction!r}")
    for f in plan.filters:
        _check_ref(f.ref, plan)
        _check_comparison(f.op, f.value)
    for key in plan.keys:
        _check_ref(key, plan)
    for metric in plan.metrics:
        _check_metric(metric, plan)
    for h in plan.having:
        _check_metric(h.metric, plan)
        _check_comparison(h.op, h.value)
        if h.metric not in plan.metrics:
            raise ValueError("having must refer to a metric the plan returns")
    if plan.operation in ("group", "rank"):
        if not plan.keys:
            raise ValueError(f"{plan.operation!r} needs at least one key")
        if not plan.metrics:
            raise ValueError(f"{plan.operation!r} needs at least one metric")
    for o in plan.order:
        if o.key not in plan.keys and o.key not in plan.metrics:
            raise ValueError("order must refer to a returned key or metric")
