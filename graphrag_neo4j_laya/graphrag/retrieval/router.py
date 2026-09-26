"""
graphrag/retrieval/router.py

Intent Router — Phase 2 Pre-Traversal Function #1 (functions.md)

Uses the CHOICE primitive to route queries to one of three strategies:
    local      → Score-Gated BFS (single-hop, low-latency)
    multi_hop  → Semantic A* Search (multi-hop, idea.md §4)
    global     → Community summary synthesis (high-level, broad)
    aggregate  → Guided query planner (count / list / rank / group), taken
                 when the question asks for one and the
                 settings.aggregate_route_enabled flag is on

The Choice primitive is semantically correct for the first three:
  - It selects ONE option from a predefined set
  - Each option has a natural-language description
  - The decision model (Laya or Jev) picks the best fit in a single call

The aggregate route is not one of them. Whether a question asks for a number,
for every match, for a ranking or for a breakdown is readable in its words
("how many", "list all", "the most", "of each type"), and offered as a fourth
option it was measured as the router's worst decision: 13 of 24 aggregate
questions went to `local` or `multi_hop`, while three questions that ask for
nothing of the kind were pulled into it. So code answers that one, and the
model picks among the three retrieval strategies as before.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

from config.settings import settings
from graphrag.models.decision_factory import get_decision_model
from graphrag.retrieval.planner import candidates

logger = logging.getLogger(__name__)


class QueryIntent(str, Enum):
    LOCAL     = "local"
    MULTI_HOP = "multi_hop"
    GLOBAL    = "global"
    AGGREGATE = "aggregate"


# Routing schema: keys are option labels, values are natural-language descriptions
# Presented to the Choice primitive as the options dict.
# Described by the shape of the answer rather than by the strategy's
# "complexity and scope": measured over the labelled non-aggregate questions,
# the older wording put seven of ten in `local` (3 / 10 right) and this one
# gets 7 / 10, with every local and multi-hop question right.
_ROUTE_OPTIONS: dict[str, str] = {
    "local":     "The answer is one fact about one named entity.",
    "multi_hop": "The answer is the chain of relations between two named entities.",
    "global":    "The answer is a summary of the graph as a whole.",
}

# `global` is the one route the model would not take: over the nine labelled
# questions that ask for it, it picked `global` twice and `multi_hop` six
# times, under four different wordings of the options. What those questions
# have in common is their subject — they ask about the graph itself, not
# about anything in it — and they say both halves of that: they name the
# graph ("this knowledge graph", "bu grafın") and they ask to be told about
# it as a whole rather than for one fact ("overview", "main themes", "özet").
# A question that names the graph without asking for the whole of it stays
# with the model: it may well be about one entity in it.
_THE_GRAPH_RE = re.compile(r"(?<!\w)(graph|graf\w*)(?!\w)", re.IGNORECASE)
_AS_A_WHOLE_RE = re.compile(
    r"(?<!\w)(overview|summary|summaris\w*|summariz\w*|describe|description|"
    r"theme\w*|topics?|big picture|about|"
    r"özet\w*|genel\w*|tema\w*|çerçeve\w*|anlat\w*|konu\w*)(?!\w)",
    re.IGNORECASE)


def asks_about_the_graph_itself(text: str) -> bool:
    """Does the question ask what the graph as a whole is about?"""
    return bool(_THE_GRAPH_RE.search(text) and _AS_A_WHOLE_RE.search(text))


@dataclass
class RouteDecision:
    intent:     QueryIntent
    confidence: float
    # Best non-aggregate intent, used when the aggregate route declines.
    fallback:   QueryIntent


_ROUTE_INSTRUCTION = "What does the question ask about?"


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
        context = f"User question: {user_query}"
        result = self._model.choice_detailed(context, _ROUTE_INSTRUCTION, dict(_ROUTE_OPTIONS))

        probs    = result.raw_probs or {}
        fallback = max(_ROUTE_OPTIONS, key=lambda k: probs.get(k, 0.0)) if probs else "multi_hop"
        # The model always names a retrieval strategy; it is the fallback the
        # aggregate route falls back to, and the route itself when the
        # question neither asks for something to count, list, rank or break
        # down, nor asks what the graph as a whole is about.
        aggregate = (settings.aggregate_route_enabled
                     and candidates.asks_for_an_aggregate(user_query))
        if aggregate:
            label, confidence = "aggregate", 1.0
        elif asks_about_the_graph_itself(user_query):
            label, confidence = "global", 1.0
        else:
            label = result.selected or "multi_hop"
            confidence = float(probs.get(label, result.score))
        intent = QueryIntent(label)

        logger.info(
            "Query routed → %s (confidence=%.2f, backend=%s)",
            intent, result.confidence, result.backend,
        )
        logger.debug("Router raw probs: %s", result.raw_probs)
        return RouteDecision(
            intent=intent,
            confidence=confidence,
            fallback=QueryIntent(fallback),
        )
