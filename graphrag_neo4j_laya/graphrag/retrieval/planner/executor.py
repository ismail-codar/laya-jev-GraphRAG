"""
graphrag/retrieval/planner/executor.py

Plan -> check -> (repair) -> execute -> facts.

Before a plan touches the database it is read back against the question with
one Noul call per candidate. The step with the smallest margin between its
choice and the runner-up is switched to the runner-up, the plan is rebuilt,
and the two are scored the same way: the better one runs, ties going to the
plan the planner built. The score is a comparison, not a grade — measured on
the labelled set, its level tracks the question and not the plan, so a fixed
threshold rejected half the correct plans. What still declines a plan is its
own confidence (the weakest step's probability), and then the pipeline falls
back to its existing routes.

A plan with a semantic filter runs in two phases (see semantic_filter.py):
candidates are scored first, then the plan runs on the sure set and, when
some candidates are uncertain, again on sure + uncertain for the upper bound.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from config.settings import settings
from graphrag.models.decision_factory import get_decision_model

from . import semantic_filter
from .facts import build_facts, template_answer
from .plan import QueryPlan
from .planner import GuidedQueryPlanner, PlannerResult, StepTrace
from .render_kuzu import fetch, render

logger = logging.getLogger(__name__)

_CHECK_INSTRUCTION = "Does this query return exactly what the question asks for?"


@dataclass
class AggregateResult:
    plan: QueryPlan
    description: str
    cypher: str
    params: dict[str, Any]
    rows: list[dict[str, Any]]
    truncated: bool
    confidence: float
    check_probability: float
    trace: list[StepTrace] = field(default_factory=list)
    repaired: bool = False
    names: list[str] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    semantic: semantic_filter.SemanticSplit | None = None
    upper_rows: list[dict[str, Any]] | None = None


def _repair_step(trace: list[StepTrace]) -> StepTrace | None:
    """The asked step whose choice beat its runner-up by the smallest margin."""
    candidates = [t for t in trace if not t.forced and t.runner_up and not t.step.startswith("key:")]
    return min(candidates, key=lambda t: t.margin, default=None)


class AggregateExecutor:
    def __init__(
        self,
        db,
        planner: GuidedQueryPlanner,
        min_confidence: float | None = None,
        max_candidates: int | None = None,
    ) -> None:
        self.db = db
        self.planner = planner
        self.min_confidence = settings.aggregate_min_confidence if min_confidence is None else min_confidence
        self.max_candidates = (
            settings.aggregate_semantic_max_candidates if max_candidates is None else max_candidates
        )

    def check(self, question: str, description: str) -> float:
        """Noul P(yes) that a plan described as *description* answers *question*."""
        context = f"User question: {question}\nDatabase query: {description}"
        return get_decision_model().noul_detailed(context, _CHECK_INSTRUCTION).score

    def _confident(self, result: PlannerResult | None) -> bool:
        if result is None:
            return False
        if result.confidence < self.min_confidence:
            logger.info("Plan confidence %.2f below %.2f — skipping",
                        result.confidence, self.min_confidence)
            return False
        return True

    def run(self, question: str, seeds: list[str]) -> AggregateResult | None:
        try:
            return self._run(question, seeds)
        except Exception as exc:  # noqa: BLE001 — never break the pipeline
            logger.warning("Aggregate route failed for %r: %s", question, exc)
            return None

    def _run(self, question: str, seeds: list[str]) -> AggregateResult | None:
        result = self.planner.plan(question, seeds)
        if not self._confident(result):
            return None
        p = self.check(question, result.check_text)
        repaired = False
        step = _repair_step(result.trace)
        if step is not None:
            other = self.planner.plan(question, seeds, overrides={step.step: step.runner_up})
            if self._confident(other):
                q = self.check(question, other.check_text)
                if q > p:
                    logger.info("Read back better with %s=%s (%.2f over %.2f)",
                                step.step, step.runner_up, q, p)
                    result, p, repaired = other, q, True

        plan, split, upper_plan = result.plan, None, None
        if result.semantic:
            candidates = semantic_filter.candidate_names(self.db, plan, self.max_candidates)
            if candidates is None:
                logger.info("Semantic filter over %d candidates — skipping", self.max_candidates)
                return None
            split = semantic_filter.split_candidates(self.db, candidates, result.semantic)
            if split.uncertain:
                upper_plan = semantic_filter.with_members(plan, split.sure_names + split.uncertain_names)
            plan = semantic_filter.with_members(plan, split.sure_names)
        rendered = render(plan)
        rows, truncated = fetch(self.db, plan)
        upper_rows = fetch(self.db, upper_plan)[0] if upper_plan else None
        names: list[str] = []
        if plan.operation == "count" and rows and rows[0][rendered.metric_columns[0]]:
            listing = QueryPlan(operation="list", start=plan.start, hops=list(plan.hops),
                                filters=list(plan.filters), limit=plan.limit)
            names = [r[f"{listing.target}_name"] for r in fetch(self.db, listing)[0]]

        args = (plan, rows, truncated, rendered.key_columns, rendered.metric_columns)
        ranges = {"upper_rows": upper_rows, "uncertain": split.uncertain_names if split else None}
        return AggregateResult(
            plan=plan,
            description=result.description,
            cypher=rendered.cypher,
            params=rendered.params,
            rows=rows,
            truncated=truncated,
            confidence=result.confidence,
            check_probability=p,
            trace=result.trace,
            repaired=repaired,
            names=names,
            facts=build_facts(plan, result.description, rows, truncated,
                              rendered.key_columns, rendered.metric_columns, names, **ranges),
            answer=template_answer(*args, names=names, **ranges),
            semantic=split,
            upper_rows=upper_rows,
        )
