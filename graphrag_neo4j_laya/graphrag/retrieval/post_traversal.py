"""
graphrag/retrieval/post_traversal.py

Phase 4: Post-Traversal & Evaluation (functions.md)

Four pipeline functions run sequentially after traversal completes
and before the LLM synthesises its final answer:

  1. rerank_context()        — Score primitive: strip bottom 20% of nodes
  2. resolve_conflicts()     — Choice primitive: pick more credible source
  3. hallucination_gate()    — Noul primitive:  context sufficiency check
  4. verify_citations()      — Noul primitive:  post-synthesis faithfulness check

All functions use get_decision_model() and work with ANY backend (Laya/Jev/Ablation).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from graphrag.models.decision_factory import get_decision_model
from config.settings import settings

logger = logging.getLogger(__name__)

# ── Thresholds (also settable via .env) ───────────────────────────────────────
_HALLUCINATION_GATE_THRESHOLD = 0.50   # P(sufficient) < 0.5 → abstain
_CITATION_VERIFY_THRESHOLD    = 0.90   # P(faithful)   > 0.9 → safe to return
_RERANK_BOTTOM_PERCENTILE     = 0.20   # Drop lowest 20% of context nodes
_MAX_CITATION_CLAIMS          = 20     # Upper bound on per-claim Noul calls

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


# ── Data Structures ────────────────────────────────────────────────────────────

@dataclass
class PostTraversalResult:
    """Output of the full post-traversal pipeline."""
    nodes:            list[dict[str, Any]]   # Reranked, deduplicated nodes
    is_sufficient:    bool                   # Did hallucination gate pass?
    gate_score:       float                  # Hallucination gate P(sufficient)
    citation_verified: bool | None           # None if LLM hasn't run yet
    conflict_resolutions: list[dict]         # Log of conflict decisions


@dataclass
class CitationResult:
    """Result of citation verification after LLM synthesis."""
    is_faithful:  bool
    noul_score:   float                  # min P(supported) over all claims
    answer:       str
    claim_scores: list[tuple[str, float]] = field(default_factory=list)


def _split_claims(answer: str) -> list[str]:
    """Split an answer into claims: one per line, then one per sentence."""
    claims: list[str] = []
    for line in answer.splitlines():
        line = line.strip().lstrip("-*•").strip()
        claims.extend(c.strip() for c in _SENTENCE_RE.split(line) if c.strip())
    return claims


# ── 1. Context Reranking (Score primitive) ─────────────────────────────────────

def rerank_context(
    nodes: list[dict[str, Any]],
    user_query: str,
    bottom_percentile: float = _RERANK_BOTTOM_PERCENTILE,
) -> list[dict[str, Any]]:
    """
    Strip the bottom *bottom_percentile* of nodes by relevance score.

    From functions.md:
        "Pass the final selected subgraph. Score each node's direct utility
         to the query. Filter out the bottom 20% to save LLM context tokens."

    Parameters
    ----------
    nodes : list[dict]
        Each node must have a 'name' key and optionally a 'text' key.
    user_query : str
        The original user question.
    bottom_percentile : float
        Fraction of low-scoring nodes to drop (default 0.20 = 20%).

    Returns
    -------
    list[dict]
        Reranked and pruned list of node dicts.
    """
    if not nodes:
        return nodes

    model = get_decision_model()
    instruction = f"Score the direct utility of this information to: '{user_query}'"

    scored: list[tuple[float, dict]] = []
    for node in nodes:
        context = f"Entity: {node.get('name', '')}. Info: {node.get('text', '')}"
        s = model.score(context, instruction)
        scored.append((s, node))

    scored.sort(key=lambda x: x[0], reverse=True)

    cutoff = max(1, int(len(scored) * (1.0 - bottom_percentile)))
    pruned = [n for _, n in scored[:cutoff]]

    logger.info(
        "Context reranking: %d → %d nodes (dropped bottom %.0f%%)",
        len(nodes), len(pruned), bottom_percentile * 100,
    )
    return pruned


# ── 2. Conflict Resolution (Choice primitive) ──────────────────────────────────

def resolve_conflicts(
    conflicts: list[tuple[dict[str, Any], dict[str, Any]]],
    user_query: str,
) -> tuple[list[dict[str, Any]], list[dict]]:
    """
    Resolve contradictory data by choosing the more credible/recent source.

    From functions.md:
        "Pass both source chunks to the model. Ask: 'Which source is more
         credible/recent?' The model uses Choice to declare the winner."

    Parameters
    ----------
    conflicts : list[tuple[dict, dict]]
        Pairs of conflicting nodes: (node_a, node_b).
    user_query : str
        Used for context in the choice decision.

    Returns
    -------
    tuple[list[dict], list[dict]]
        (resolved_nodes, resolution_log)
    """
    if not conflicts:
        return [], []

    model = get_decision_model()
    resolved: list[dict[str, Any]] = []
    log: list[dict] = []

    for node_a, node_b in conflicts:
        name_a = node_a.get("name", "Source A")
        name_b = node_b.get("name", "Source B")
        text_a = node_a.get("text", "")[:300]
        text_b = node_b.get("text", "")[:300]

        context = (
            f"Query: {user_query}\n\n"
            f"Source A ({name_a}): {text_a}\n\n"
            f"Source B ({name_b}): {text_b}"
        )
        instruction = "Which source is more credible, specific, and directly answers the query?"
        options = {
            "source_a": f"Use '{name_a}': {text_a[:100]}",
            "source_b": f"Use '{name_b}': {text_b[:100]}",
        }

        result = model.choice_detailed(context, instruction, options)
        winner = node_a if result.selected == "source_a" else node_b
        resolved.append(winner)
        log.append({
            "conflict": (name_a, name_b),
            "winner":   winner.get("name"),
            "confidence": result.confidence,
            "backend":  result.backend,
        })
        logger.info(
            "Conflict resolved: '%s' vs '%s' → '%s' (conf=%.2f)",
            name_a, name_b, winner.get("name"), result.confidence,
        )

    return resolved, log


# ── 3. Hallucination Gatekeeper (Noul primitive) ───────────────────────────────

def hallucination_gate(
    context_nodes: list[dict[str, Any]],
    user_query: str,
    threshold: float = _HALLUCINATION_GATE_THRESHOLD,
) -> tuple[bool, float]:
    """
    Check whether the retrieved context is factually sufficient to answer the query.

    From functions.md:
        "Ask: 'Is the retrieved context factually sufficient to answer the question
         without guessing?' If Noul < 0.50, trigger a fallback to a web search."

    Parameters
    ----------
    context_nodes : list[dict]
        The reranked, conflict-resolved nodes.
    user_query : str
        The original user question.
    threshold : float
        Minimum P(sufficient) to proceed. Below this, the pipeline abstains.

    Returns
    -------
    tuple[bool, float]
        (is_sufficient, noul_score)
        If False, the pipeline should trigger a fallback (web search or "I don't know").
    """
    if not context_nodes:
        logger.warning("Hallucination gate: empty context → FAIL")
        return False, 0.0

    model = get_decision_model()

    # Build a summary of the retrieved context
    context_summary = " | ".join(
        f"{n.get('name', '?')}: {n.get('text', '')[:200]}"
        for n in context_nodes[:10]
    )
    context = f"Question: {user_query}\n\nRetrieved knowledge: {context_summary}"
    instruction = "Does the retrieved knowledge contain the answer to the question?"

    noul_score = model.noul(context, instruction)
    is_sufficient = noul_score >= threshold

    logger.info(
        "Hallucination gate: noul=%.3f, threshold=%.2f → %s",
        noul_score, threshold, "PASS" if is_sufficient else "FAIL (abstain)",
    )
    return is_sufficient, noul_score


# ── 4. Citation Verification (Noul primitive) ─────────────────────────────────

def verify_citations(
    llm_answer: str,
    context_nodes: list[dict[str, Any]],
    threshold: float = _CITATION_VERIFY_THRESHOLD,
) -> CitationResult:
    """
    Verify that every claim in the LLM's answer is grounded in the context.

    From functions.md:
        "Pass the LLM's final answer and the subgraph context to the model.
         Ask: 'Is every claim in the generated answer strictly supported by the context?'
         If Noul > 0.90, return to the user."

    Parameters
    ----------
    llm_answer : str
        The generated answer from the LLM synthesis step.
    context_nodes : list[dict]
        The nodes used to generate the answer.
    threshold : float
        Minimum P(faithful) to pass.

    Returns
    -------
    CitationResult
        is_faithful (bool), noul_score (float), answer (str)
        If not faithful, the answer should be flagged or re-generated.
    """
    if not llm_answer.strip():
        return CitationResult(is_faithful=False, noul_score=0.0, answer=llm_answer)

    # Each claim is checked on its own and the weakest one decides. Judging the
    # whole answer at once lets supported lines mask a fabricated one: measured
    # on the quickstart, a faithful answer scored 0.87 while the same answer
    # plus an invented claim scored 0.94. Per claim: 0.97 vs 0.00.
    claims = _split_claims(llm_answer)
    if len(claims) > _MAX_CITATION_CLAIMS:
        logger.warning(
            "Citation verification: %d claims, checking the first %d",
            len(claims), _MAX_CITATION_CLAIMS,
        )
        claims = claims[:_MAX_CITATION_CLAIMS]
    if not claims:
        return CitationResult(is_faithful=False, noul_score=0.0, answer=llm_answer)

    model = get_decision_model()

    context_text = "\n".join(
        f"- {n.get('name', '?')}: {n.get('text', '')}" for n in context_nodes[:10]
    )
    instruction = "Is this claim supported by the context knowledge?"
    claim_scores = [
        (claim, model.noul(f"Context knowledge:\n{context_text}\n\nClaim: {claim}", instruction))
        for claim in claims
    ]

    noul_score = min(score for _, score in claim_scores)
    is_faithful = noul_score >= threshold

    logger.info(
        "Citation verification: min noul=%.3f over %d claims, threshold=%.2f → %s",
        noul_score, len(claim_scores), threshold, "VERIFIED" if is_faithful else "FLAGGED",
    )
    for claim, score in claim_scores:
        if score < threshold:
            logger.info("  unsupported claim (P=%.3f): %s", score, claim)
    return CitationResult(
        is_faithful=is_faithful,
        noul_score=noul_score,
        answer=llm_answer,
        claim_scores=claim_scores,
    )
