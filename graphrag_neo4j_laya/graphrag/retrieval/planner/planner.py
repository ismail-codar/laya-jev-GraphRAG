"""
graphrag/retrieval/planner/planner.py

Guided query planner — builds a QueryPlan step by step.

At every step code enumerates the legal moves (from the live graph and the
plan whitelists) and the decision model only picks one of them, Pangu-style
("don't generate, discriminate"). Relation types and directions come from
what actually exists on the current frontier, so the model cannot choose a
relation or direction that is not in the graph.

Each step is recorded in a trace with the selected option, its probability
and the runner-up. The plan confidence is the weakest step's probability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from config.settings import settings
from graphrag.models.decision_factory import get_decision_model

from . import candidates, semantic_filter
from .describe import describe_plan
from .plan import (
    LIST_METRIC_OPS, FieldRef, Filter, Having, Hop, Metric, Order, QueryPlan, validate_plan,
)
from .render_kuzu import fetch

logger = logging.getLogger(__name__)

_OPERATION_OPTIONS = {
    "count": "The question asks how many things there are (a number).",
    "list":  "The question asks which or who: every matching entity should be listed.",
    "rank":  "The question asks which entity has the most or the least of something.",
    "group": "The question asks for a breakdown: a number per type, per entity or per group.",
}
_START_INSTRUCTION = "Does the question ask about this one specific entity, or about the whole graph?"
_HOP_INSTRUCTION = "Which next step brings this graph query closer to answering the question?"
_EXCLUDE_INSTRUCTION = "Does the question ask for entities other than the start entity itself?"
_NUMERIC_FILTER_INSTRUCTION = "Does the question keep only entities whose numeric value is above or below a number?"
_HAVING_INSTRUCTION = "Does the question keep only groups whose count or total is above or below a number?"
_COMPARISON_OPTIONS = {
    ">": "greater than", ">=": "at least", "<": "less than", "<=": "at most", "=": "exactly equal to",
}
_KEEP_THRESHOLD = 0.5
_FRONTIER_LIMIT = 1000


@dataclass
class StepTrace:
    step: str
    options: list[str]
    selected: str
    probability: float
    runner_up: str | None = None
    runner_up_probability: float = 0.0
    forced: bool = False
    overridden: bool = False

    @property
    def margin(self) -> float:
        return self.probability - self.runner_up_probability


@dataclass
class PlannerResult:
    plan: QueryPlan
    trace: list[StepTrace] = field(default_factory=list)
    confidence: float = 1.0
    description: str = ""
    # Entity kind the target must have (semantic_filter.PREDICATE_KINDS), or None.
    semantic: str | None = None


class _Planning:
    """State of one plan() call."""

    def __init__(self, planner: "GuidedQueryPlanner", question: str, overrides: dict[str, str]) -> None:
        self.p = planner
        self.question = question
        self.overrides = overrides
        self.model = get_decision_model()
        self.trace: list[StepTrace] = []
        self.plan = QueryPlan(operation="list", limit=planner.row_limit)
        self.semantic: str | None = None

    def context(self) -> str:
        so_far = describe_plan(self.plan, self.p.relation_schema) if self.trace else "(empty)"
        return f"User question: {self.question}\nQuery plan so far: {so_far}"

    def forced(self, step: str, selected: str) -> str:
        self.trace.append(StepTrace(step, [selected], selected, 1.0, forced=True))
        return selected

    def choose(self, step: str, instruction: str, options: dict[str, str]) -> str:
        result = self.model.choice_detailed(self.context(), instruction, options)
        probs = result.raw_probs or {result.selected: result.score}
        ranked = sorted(options, key=lambda k: probs.get(k, 0.0), reverse=True)
        selected = result.selected if result.selected in options else ranked[0]
        overridden = step in self.overrides and self.overrides[step] in options
        if overridden:
            selected = self.overrides[step]
        runner_up = next((k for k in ranked if k != selected), None)
        self.trace.append(StepTrace(
            step, list(options), selected, probs.get(selected, 0.0),
            runner_up, probs.get(runner_up, 0.0) if runner_up else 0.0, overridden=overridden,
        ))
        return selected

    def yes(self, step: str, instruction: str) -> bool:
        if step in self.overrides:
            answer = self.overrides[step] == "yes"
            self.trace.append(StepTrace(step, ["yes", "no"], "yes" if answer else "no", 1.0, overridden=True))
            return answer
        p = self.model.noul_detailed(self.context(), instruction).score
        answer = p >= _KEEP_THRESHOLD
        self.trace.append(StepTrace(
            step, ["yes", "no"], "yes" if answer else "no", p if answer else 1.0 - p,
            "no" if answer else "yes", 1.0 - p if answer else p,
        ))
        return answer

    # ── steps ────────────────────────────────────────────────────────────────

    def operation(self) -> None:
        self.plan.operation = self.choose("operation", "What kind of answer does the question ask for?",
                                          _OPERATION_OPTIONS)

    def start(self, seeds: list[str]) -> None:
        if not seeds:
            self.forced("start", "all")
            return
        anchor = seeds[0]
        options = {
            "anchor": f"The question is about the specific entity '{anchor}'.",
            "all":    "The question is about all entities in the graph, not one specific entity.",
        }
        if self.choose("start", _START_INSTRUCTION, options) == "anchor":
            self.plan.start = anchor

    def frontier(self) -> list[str] | None:
        if not self.plan.hops:
            return [self.plan.start] if self.plan.start else None
        probe = QueryPlan(operation="list", start=self.plan.start, hops=list(self.plan.hops),
                          limit=_FRONTIER_LIMIT)
        rows, _ = fetch(self.p.db, probe)
        return [r[f"{probe.target}_name"] for r in rows]

    def hops(self) -> None:
        for i in range(self.p.max_hops):
            step = f"hop{i}"
            moves = self.p.db.frontier_moves(self.frontier())
            if not moves:
                self.forced(step, "stop")
                return
            selected = self.choose(step, _HOP_INSTRUCTION,
                                   candidates.hop_options(self.plan, moves, self.p.relation_schema))
            if selected == "stop":
                return
            rel_type, direction = selected.split(":")
            self.plan.hops.append(Hop(None if rel_type == "any" else rel_type, direction))

    def filters(self, numbers: list[int | float]) -> None:
        target = self.plan.target
        if self.plan.start and len(self.plan.hops) >= 2 and self.yes("exclude_start", _EXCLUDE_INSTRUCTION):
            self.plan.filters.append(Filter(FieldRef(target, "name"), "!=", self.plan.start))
        if not numbers or not self.yes("filter_numeric", _NUMERIC_FILTER_INSTRUCTION):
            return
        fields = {
            f"{v}.{f}": f"the {f} of {'the start entity' if v == 'v0' else 'the step-' + v[1:] + ' entity'}"
            for v, f in candidates.numeric_field_candidates(self.plan)
        }
        var, fld = self.choose("filter_field", "Which value does the question compare with a number?", fields).split(".")
        op = self.choose("filter_op", "How is the value compared with the number?", _COMPARISON_OPTIONS)
        value = self._number("filter_value", numbers)
        self.plan.filters.append(Filter(FieldRef(var, fld), op, value))

    def shape(self, numbers: list[int | float]) -> None:
        if self.plan.operation not in ("group", "rank"):
            return
        keys = candidates.group_key_candidates(self.plan)
        questions = {
            f"key:{v}.{f}": {"type": "noul", "instruction": f"Should the answer be broken down per {_key_words(v, f)}?"}
            for v, f in keys
        }
        answers = self.model.ask_batch(self.context(), questions)
        probs = {name: answers[name].score for name in questions}
        kept = [name for name, p in probs.items() if p >= _KEEP_THRESHOLD] or [max(probs, key=probs.get)]
        for name in kept:
            self.trace.append(StepTrace(name, ["yes", "no"], "yes", max(probs[name], 1 - probs[name])))
            v, f = name[4:].split(".")
            self.plan.keys.append(FieldRef(v, f))

        metric = _parse_metric(self.choose("metric", "What should be computed for each group?",
                                           _metric_options(self.plan)))
        self.plan.metrics.append(metric)
        if self.plan.operation == "rank":
            self.plan.order = [Order(metric, descending=True)]
            self.plan.limit = candidates.small_limit(numbers)
        elif metric.op in LIST_METRIC_OPS:
            pass  # a list of values cannot be compared with a number
        elif numbers and self.yes("having", _HAVING_INSTRUCTION):
            op = self.choose("having_op", "How is the group value compared with the number?", _COMPARISON_OPTIONS)
            self.plan.having.append(Having(metric, op, self._number("having_value", numbers)))

    def semantic_step(self) -> None:
        kind = self.choose("semantic", semantic_filter.PREDICATE_INSTRUCTION, semantic_filter.PREDICATE_KINDS)
        if kind != semantic_filter.NO_PREDICATE:
            self.semantic = kind

    def _number(self, step: str, numbers: list[int | float]) -> int | float:
        if len(numbers) == 1:
            self.forced(step, str(numbers[0]))
            return numbers[0]
        options = {str(n): f"the number {n} mentioned in the question" for n in numbers}
        return numbers[list(options).index(self.choose(step, "Which number from the question is meant?", options))]


def _key_words(var: str, fld: str) -> str:
    if var.startswith("e"):
        return f"relation type of step {int(var[1:]) + 1}"
    who = "start entity" if var == "v0" else f"step-{var[1:]} entity"
    return f"{'community' if fld == 'communityId' else 'name'} of the {who}"


def _metric_options(plan: QueryPlan) -> dict[str, str]:
    target = plan.target
    options = {
        "count": "the number of matching paths or edges",
        f"count_distinct:{target}.name": "the number of distinct entities reached",
    }
    words = {"sum": "total", "avg": "average", "min": "lowest", "max": "highest"}
    for v in candidates.node_vars(plan):
        who = "start entity" if v == "v0" else f"step-{v[1:]} entity"
        for op, word in words.items():
            options[f"{op}:{v}.pagerank"] = f"the {word} PageRank (importance) of the {who}"
    # A list cannot be ordered, so `collect` is offered to group plans only.
    if plan.operation != "rank":
        for e in candidates.edge_vars(plan):
            step = int(e[1:]) + 1
            options[f"collect:{e}.type"] = (
                f"the names of the relation types used in step {step}, listed per group"
            )
    return options


def _parse_metric(key: str) -> Metric:
    if key == "count":
        return Metric("count")
    op, ref = key.split(":")
    var, fld = ref.split(".")
    return Metric(op, FieldRef(var, fld))


class GuidedQueryPlanner:
    """
    Build a QueryPlan for *question* on *db*.

    `plan()` never raises: backend or model failures return None so the
    caller can fall back to the existing retrieval routes.
    """

    def __init__(
        self,
        db,
        relation_schema: dict[str, str] | None = None,
        max_hops: int | None = None,
        row_limit: int | None = None,
    ) -> None:
        self.db = db
        self.relation_schema = relation_schema or {}
        self.max_hops = max_hops if max_hops is not None else settings.aggregate_max_hops
        self.row_limit = row_limit if row_limit is not None else settings.aggregate_row_limit

    def plan(self, question: str, seeds: list[str], overrides: dict[str, str] | None = None) -> PlannerResult | None:
        state = _Planning(self, question, overrides or {})
        numbers = candidates.extract_numbers(question)
        try:
            state.operation()
            state.start(seeds)
            state.hops()
            state.filters(numbers)
            state.shape(numbers)
            state.semantic_step()
            validate_plan(state.plan)
        except Exception as exc:  # noqa: BLE001 — planner must never break the pipeline
            logger.warning("Guided planner failed for %r: %s", question, exc)
            return None
        # Overridden steps are a deliberate repair; the plan check vouches for them.
        description = describe_plan(state.plan, self.relation_schema)
        if state.semantic:
            description += f" ({semantic_filter.describe_predicate(state.semantic)})"
        asked = [t.probability for t in state.trace if not t.forced and not t.overridden]
        return PlannerResult(
            plan=state.plan,
            trace=state.trace,
            confidence=min(asked) if asked else 1.0,
            description=description,
            semantic=state.semantic,
        )
