"""
graphrag/retrieval/planner/executor.py

Plan -> check -> (repair) -> execute -> facts.

Before a plan touches the database its template description is checked
against the question with one Noul call. If the check fails, the step with
the smallest margin between its choice and the runner-up is switched to the
runner-up and the plan is rebuilt and checked once more. Anything that does
not pass returns None so the pipeline falls back to its existing routes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from config.settings import settings
from graphrag.models.decision_factory import get_decision_model

from .facts import build_facts, template_answer
from .plan import QueryPlan
from .planner import GuidedQueryPlanner, PlannerResult, StepTrace
from .render_kuzu import fetch, render

logger = logging.getLogger(__name__)

_CHECK_INSTRUCTION = "Does this database query answer the user's question?"


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
        check_threshold: float | None = None,
    ) -> None:
        self.db = db
        self.planner = planner
        self.min_confidence = settings.aggregate_min_confidence if min_confidence is None else min_confidence
        self.check_threshold = (
            settings.aggregate_check_min_confidence if check_threshold is None else check_threshold
        )

    def check(self, question: str, description: str) -> float:
        """Noul P(yes) that a plan described as *description* answers *question*."""
        context = f"User question: {question}\nDatabase query: {description}"
        return get_decision_model().noul_detailed(context, _CHECK_INSTRUCTION).score

    def _accepted(self, question: str, result: PlannerResult | None) -> tuple[bool, float]:
        if result is None:
            return False, 0.0
        if result.confidence < self.min_confidence:
            logger.info("Plan confidence %.2f below %.2f — skipping", result.confidence, self.min_confidence)
            return False, 0.0
        p = self.check(question, result.description)
        return p >= self.check_threshold, p

    def run(self, question: str, seeds: list[str]) -> AggregateResult | None:
        try:
            return self._run(question, seeds)
        except Exception as exc:  # noqa: BLE001 — never break the pipeline
            logger.warning("Aggregate route failed for %r: %s", question, exc)
            return None

    def _run(self, question: str, seeds: list[str]) -> AggregateResult | None:
        result = self.planner.plan(question, seeds)
        ok, p = self._accepted(question, result)
        repaired = False
        if not ok and result is not None and result.confidence >= self.min_confidence:
            step = _repair_step(result.trace)
            if step is None:
                return None
            logger.info("Plan check failed (%.2f); retrying with %s=%s", p, step.step, step.runner_up)
            result = self.planner.plan(question, seeds, overrides={step.step: step.runner_up})
            ok, p = self._accepted(question, result)
            repaired = True
        if not ok:
            return None

        plan = result.plan
        rendered = render(plan)
        rows, truncated = fetch(self.db, plan)
        names: list[str] = []
        if plan.operation == "count" and rows and rows[0][rendered.metric_columns[0]]:
            listing = QueryPlan(operation="list", start=plan.start, hops=list(plan.hops),
                                filters=list(plan.filters), limit=plan.limit)
            names = [r[f"{listing.target}_name"] for r in fetch(self.db, listing)[0]]

        args = (plan, rows, truncated, rendered.key_columns, rendered.metric_columns)
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
                              rendered.key_columns, rendered.metric_columns, names),
            answer=template_answer(*args, names=names),
        )
