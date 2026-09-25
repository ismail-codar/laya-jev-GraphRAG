"""
graphrag/retrieval/router.py

Intent Router — Phase 2 Pre-Traversal Function #1 (functions.md)

Uses the CHOICE primitive to route queries to one of three strategies:
    local      → Score-Gated BFS (single-hop, low-latency)
    multi_hop  → Semantic A* Search (multi-hop, idea.md §4)
    global     → Community summary synthesis (high-level, broad)
    aggregate  → Guided query planner (count / list / rank / group), offered
                 only when settings.aggregate_route_enabled is on

The Choice primitive is semantically correct here:
  - It selects ONE option from a predefined set
  - Each option has a natural-language description
  - The decision model (Laya or Jev) picks the best fit in a single call
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from config.settings import settings
from graphrag.models.decision_factory import get_decision_model

logger = logging.getLogger(__name__)


class QueryIntent(str, Enum):
    LOCAL     = "local"
    MULTI_HOP = "multi_hop"
    GLOBAL    = "global"
    AGGREGATE = "aggregate"


# Routing schema: keys are option labels, values are natural-language descriptions
# Presented to the Choice primitive as the options dict.
_ROUTE_OPTIONS: dict[str, str] = {
    "local":     "The question asks about one specific fact of one entity.",
    "multi_hop": (
        "The question asks how two or more entities are connected, "
        "requiring a chain of facts."
    ),
    "global":    "The question asks for a broad summary or overview of a whole topic.",
}

_AGGREGATE_OPTION = (
    "The question asks to count, rank, total or list ALL items of some kind "
    "(how many, which has the most, list every, per type)."
)

@dataclass
class RouteDecision:
    intent:     QueryIntent
    confidence: float
    # Best non-aggregate intent, used when the aggregate route declines.
    fallback:   QueryIntent


_ROUTE_INSTRUCTION = (
    "Which graph retrieval strategy should be used to answer this question? "
    "Select the strategy that best matches the query's complexity and scope."
)


class IntentRouter:
    """
    Zero-shot query intent classifier using the Choice primitive.

    Laya backend: scores each option independently, returns highest.
    Jev backend:  asks a single 'choice' question — one parallel API call.
    """

    def __init__(self) -> None:
        self._model = get_decision_model()

    def route(self, user_query: str) -> QueryIntent:
        """
        Classify *user_query* into one of the retrieval strategies.

        Parameters
        ----------
        user_query:
            Raw user question string.

        Returns
        -------
        QueryIntent
            The routing decision (local | multi_hop | global | aggregate).
        """
        return self.route_detailed(user_query).intent

    def route_detailed(self, user_query: str) -> RouteDecision:
        """Like route(), plus the selected option's probability and a fallback intent."""
        options = dict(_ROUTE_OPTIONS)
        if settings.aggregate_route_enabled:
            options["aggregate"] = _AGGREGATE_OPTION

        context = f"User question: {user_query}"
        result = self._model.choice_detailed(context, _ROUTE_INSTRUCTION, options)

        label    = result.selected or "multi_hop"   # safe default
        intent   = QueryIntent(label)
        probs    = result.raw_probs or {}
        fallback = max(_ROUTE_OPTIONS, key=lambda k: probs.get(k, 0.0)) if probs else "multi_hop"

        logger.info(
            "Query routed → %s (confidence=%.2f, backend=%s)",
            intent, result.confidence, result.backend,
        )
        logger.debug("Router raw probs: %s", result.raw_probs)
        return RouteDecision(
            intent=intent,
            confidence=float(probs.get(label, result.score)),
            fallback=QueryIntent(fallback),
        )
