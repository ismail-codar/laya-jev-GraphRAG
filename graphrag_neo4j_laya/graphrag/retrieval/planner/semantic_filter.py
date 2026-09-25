"""
graphrag/retrieval/planner/semantic_filter.py

Semantic filter step: predicates the schema cannot express ("is it a
theory?") applied per candidate with Noul.

The kind of entity comes from a closed list chosen with Choice, never free
text. Execution is two-phase: the plan's target values are fetched without
the predicate, each candidate is scored with Noul on its name and
description and banded into sure / uncertain / rejected, and the sure set
(and sure + uncertain for the upper bound) goes back into the plan as an IN
filter. Results are therefore reported as a [sure, sure + uncertain] range.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from graphrag.models.decision_factory import get_decision_model

from .plan import FieldRef, Filter, QueryPlan
from .render_kuzu import fetch

NO_PREDICATE = "none"
PREDICATE_KINDS = {
    NO_PREDICATE:   "no restriction: the question is about entities of any kind",
    "person":       "a person",
    "theory":       "a scientific theory, law or mathematical idea",
    "place":        "a place such as a city, village or country",
    "work":         "a written work such as a book or paper",
    "organisation": "an organisation, institution or society",
    "phenomenon":   "a natural phenomenon",
}
PREDICATE_INSTRUCTION = "Does the question only ask about entities of one kind? If so, which kind?"
SURE_THRESHOLD = 0.70
REJECT_THRESHOLD = 0.30


@dataclass
class SemanticSplit:
    kind: str
    sure: list[tuple[str, float]] = field(default_factory=list)
    uncertain: list[tuple[str, float]] = field(default_factory=list)
    rejected: list[tuple[str, float]] = field(default_factory=list)

    @property
    def sure_names(self) -> list[str]:
        return [n for n, _ in self.sure]

    @property
    def uncertain_names(self) -> list[str]:
        return [n for n, _ in self.uncertain]


def describe_predicate(kind: str) -> str:
    return f"keep only entities that are {PREDICATE_KINDS[kind]}"


def candidate_names(db, plan: QueryPlan, max_candidates: int) -> list[str] | None:
    """Target values of *plan* without the predicate; None when over the cap."""
    listing = QueryPlan(operation="list", start=plan.start, hops=list(plan.hops),
                        filters=list(plan.filters), limit=max_candidates)
    rows, truncated = fetch(db, listing)
    if truncated:
        return None
    return [r[f"{listing.target}_name"] for r in rows]


def split_candidates(db, names: list[str], kind: str) -> SemanticSplit:
    model = get_decision_model()
    instruction = f"Is this entity {PREDICATE_KINDS[kind]}?"
    split = SemanticSplit(kind)
    for name in names:
        p = model.noul_detailed(f"Entity: {name}\nDescription: {db.get_node_text(name)}", instruction).score
        band = split.sure if p >= SURE_THRESHOLD else split.rejected if p <= REJECT_THRESHOLD else split.uncertain
        band.append((name, p))
    return split


def with_members(plan: QueryPlan, names: list[str]) -> QueryPlan:
    """*plan* restricted to targets whose name is in *names*."""
    return replace(plan, filters=[*plan.filters, Filter(FieldRef(plan.target, "name"), "in", list(names))])
