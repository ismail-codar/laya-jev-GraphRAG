"""
graphrag/pipeline.py

End-to-end GraphRAG orchestrator.

Wires together ALL pipeline components in the correct order (functions.md):

    Phase 1 (offline): Ingestion → Chunking, NER, Disambiguation,
                        Edge Verification, Ontology Alignment
    Phase 2: Intent routing → QueryIntent (local / multi_hop / global / aggregate)
    Phase 2: Seed selection → top-K entry nodes
    Phase 3: Graph traversal → relevant subgraph paths (+ Early Termination)
    Phase 4: Post-traversal:
             → Context Reranking       (Score)
             → Conflict Resolution     (Choice)
             → Hallucination Gate      (Noul)  — abstain if P < 0.5
             → LLM Synthesis           (Llama)
             → Citation Verification   (Noul)  — flag if P < 0.9

Usage (CLI)
-----------
    python -m graphrag.pipeline --query "What causes apple to fall?"

Usage (library)
---------------
    from graphrag.pipeline import GraphRAGPipeline
    pipeline = GraphRAGPipeline()
    result = pipeline.query("What causes apple to fall?")
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

from config.settings import settings
from graphrag.graph.base import BaseGraphClient
from graphrag.graph.factory import get_graph_client
from graphrag.graph.relation_schema import load_relation_schema
from graphrag.models.llm import get_llm
from graphrag.retrieval.planner.executor import AggregateExecutor, AggregateResult
from graphrag.retrieval.planner.planner import GuidedQueryPlanner
from graphrag.retrieval.router import IntentRouter, QueryIntent
from graphrag.retrieval.seed_selector import SeedSelector
from graphrag.retrieval.traversal.astar import LayaGraphNavigator
from graphrag.retrieval.traversal.bfs import ScoreGatedBFS
from graphrag.retrieval.post_traversal import (
    rerank_context,
    resolve_conflicts,
    hallucination_gate,
    verify_citations,
)

logger = logging.getLogger(__name__)

# Fallback answer when the hallucination gate fails
_ABSTAIN_RESPONSE = (
    "I don't have enough verified information in my knowledge graph to confidently "
    "answer this question. Please try a more specific query or check external sources."
)

# Response when citation verification fails
_FLAGGED_PREFIX = "[⚠️ UNVERIFIED] "
_NO_SEEDS_RESPONSE = "I could not find relevant entry points in the knowledge graph."


class GraphRAGPipeline:
    """
    Full Agentic GraphRAG pipeline — all 19 functions.md stages.

    Parameters
    ----------
    graph_client:
        Optional pre-constructed graph client (useful for testing).
    llm:
        Optional object with a ``generate(prompt: str) -> str`` method used for
        answer synthesis. Defaults to the local Llama-3.1 8B NF4 model.
    """

    def __init__(self, graph_client: BaseGraphClient | None = None, llm: Any = None) -> None:
        self._db     = graph_client or get_graph_client()
        self._router = IntentRouter()
        self._seeds  = SeedSelector(self._db)
        self._astar  = LayaGraphNavigator(self._db)
        self._bfs    = ScoreGatedBFS(self._db)
        self._llm    = llm or get_llm()
        self._aggregate: AggregateExecutor | None = None
        self._aggregate_warned = False

    # ── Path → node list conversion ───────────────────────────────────────────

    def _node_text(self, name: str, fact: str | None = None) -> str:
        """Node description, prefixed with the traversed fact that reached it."""
        text = self._db.get_node_text(name)
        return f"{fact}. {text}" if fact else text

    def _paths_to_nodes(self, paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert traversal path dicts into node dicts for post-processing."""
        seen: set[str] = set()
        nodes: list[dict] = []
        for p in paths:
            path, edges = p.get("path", []), p.get("edges", [])
            for i, name in enumerate(path):
                if name not in seen:
                    seen.add(name)
                    fact = f"{path[i - 1]} {edges[i - 1]} {name}" if 0 < i <= len(edges) else None
                    nodes.append({
                        "name":  name,
                        "text":  self._node_text(name, fact),
                        "score": p.get("score", 0.0) * (0.9 ** i),  # decay by hop depth
                    })
        return nodes

    def _edges_to_nodes(self, edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        nodes: list[dict] = []
        for e in edges:
            for key in ("source", "target"):
                name = e.get(key, "")
                if name and name not in seen:
                    seen.add(name)
                    fact = (
                        f"{e['source']} {e['edge_type']} {name}"
                        if key == "target" and e.get("edge_type") else None
                    )
                    nodes.append({
                        "name":  name,
                        "text":  self._node_text(name, fact),
                        "score": e.get("score", 0.0),
                    })
        return nodes

    @staticmethod
    def _format_nodes(nodes: list[dict[str, Any]]) -> str:
        if not nodes:
            return "No relevant information found."
        return "\n".join(f"- {n['name']}: {n.get('text', '')}" for n in nodes)

    # ── Aggregate route (guided query planner) ───────────────────────────────

    def _aggregate_executor(self) -> AggregateExecutor | None:
        if settings.decision_model_backend == "ablation":
            # AblationModel implements only score(); the planner needs choice/noul.
            if not self._aggregate_warned:
                logger.warning("Aggregate route unavailable with the ablation backend")
                self._aggregate_warned = True
            return None
        if self._aggregate is None:
            schema = load_relation_schema(settings.relation_schema_path)
            planner = GuidedQueryPlanner(self._db, schema.descriptions, schema.words)
            self._aggregate = AggregateExecutor(self._db, planner)
        return self._aggregate

    def _aggregate_answer(self, user_query: str, result: AggregateResult) -> str:
        if settings.aggregate_answer_mode != "llm":
            return result.answer
        prompt = (
            f"User question: {user_query}\n\n"
            f"Graph database result:\n{self._format_nodes(result.facts)}\n\n"
            f"Based only on the above result, provide a concise and accurate answer. "
            f"Do not add information that is not present in the result."
        )
        answer = self._llm.generate(prompt)
        citation = verify_citations(answer, result.facts)
        if not citation.is_faithful:
            logger.warning("Aggregate citation FAILED (P=%.3f) — prefixing answer", citation.noul_score)
            return _FLAGGED_PREFIX + answer
        return answer

    # ── Public API ────────────────────────────────────────────────────────────

    def query_aggregate(self, user_query: str) -> AggregateResult | None:
        """
        Run the guided query planner directly, bypassing the router and the
        aggregate_route_enabled flag. Returns the full result (plan, Cypher,
        rows, trace) or None when no plan was accepted.
        """
        executor = self._aggregate_executor()
        if executor is None:
            return None
        return executor.run(user_query, self._seeds.select(user_query))

    def query(self, user_query: str, max_depth: int = 4) -> str:
        """
        Execute a full GraphRAG query across all 4 phases.

        Parameters
        ----------
        user_query : str
            Natural-language question from the user.
        max_depth : int
            Maximum hop depth for A* traversal.

        Returns
        -------
        str
            Final synthesised, citation-verified answer.
        """
        logger.info("=" * 60)
        logger.info("GraphRAG query: %r", user_query)

        # ── Phase 2: Intent Routing (Choice) ──────────────────────────────────
        decision = self._router.route_detailed(user_query)
        intent = decision.intent
        logger.info("Phase 2 — Intent: %s", intent)

        # ── Phase 2: Seed Node Selection ──────────────────────────────────────
        # Entry points for the traversal routes. The aggregate route needs
        # none: it reads its anchor from the question ("Isaac Newton kaç eser
        # yazdı?") and otherwise starts from every entity, which is what a
        # question about the whole graph asks for. So an empty list ends the
        # traversal routes only, and is decided after the plan has had its
        # turn.
        seed_names = self._seeds.select(user_query)
        logger.info("Phase 2 — Seeds: %s", seed_names or "(none)")

        # ── Phase 3 (aggregate): guided query planner → DB ────────────────────
        # Rerank and the hallucination gate are skipped: the DB result is
        # complete by construction and pruning it would lose data.
        if intent == QueryIntent.AGGREGATE:
            executor = (
                self._aggregate_executor()
                if decision.confidence >= settings.aggregate_route_min_confidence else None
            )
            result = executor.run(user_query, seed_names) if executor else None
            if result is not None:
                logger.info("Phase 3 — Aggregate plan: %s", result.description)
                return self._aggregate_answer(user_query, result)
            logger.info("Phase 3 — Aggregate route declined → %s", decision.fallback)
            intent = decision.fallback

        if not seed_names:
            logger.info("Phase 3 — No entry points for the %s route", intent)
            return _NO_SEEDS_RESPONSE

        # ── Phase 3: Graph Traversal (+ Early Termination via Noul) ──────────
        raw_nodes: list[dict[str, Any]] = []
        if intent == QueryIntent.MULTI_HOP:
            all_paths: list[dict] = []
            for seed in seed_names:
                all_paths.extend(self._astar.search(seed, user_query, max_depth=max_depth))
            raw_nodes = self._paths_to_nodes(all_paths)

        elif intent == QueryIntent.LOCAL:
            edges = self._bfs.retrieve(seed_names, user_query)
            # Seeds are the entry points the query matched; keep them as context too
            # (after the edges, so nodes reached by an edge keep that fact in their text)
            raw_nodes = self._edges_to_nodes(
                edges + [{"source": s, "score": 1.0} for s in seed_names]
            )

        else:  # Global
            all_paths = []
            for seed in seed_names:
                all_paths.extend(self._astar.search(seed, user_query, max_depth=2, max_paths=3))
            raw_nodes = self._paths_to_nodes(all_paths)

        logger.info("Phase 3 — Retrieved %d raw nodes", len(raw_nodes))

        # ── Phase 4a: Context Reranking (Score) ───────────────────────────────
        reranked_nodes = rerank_context(raw_nodes, user_query)
        logger.info("Phase 4a — Reranked: %d nodes remain", len(reranked_nodes))

        # ── Phase 4b: Conflict Resolution (Choice) ────────────────────────────
        # Detect conflicts: nodes with the same name but different text (simplified heuristic)
        name_map: dict[str, list[dict]] = {}
        for n in reranked_nodes:
            name_map.setdefault(n["name"], []).append(n)
        conflict_pairs = [
            (group[0], group[1])
            for group in name_map.values()
            if len(group) >= 2
        ]
        resolved_nodes, conflict_log = resolve_conflicts(conflict_pairs, user_query)
        # Merge: keep unique nodes + resolution winners
        final_nodes = [n for n in reranked_nodes if n["name"] not in {p[0]["name"] for p in conflict_pairs}]
        final_nodes.extend(resolved_nodes)
        logger.info("Phase 4b — Conflicts resolved: %d decisions", len(conflict_log))

        # ── Phase 4c: Hallucination Gate (Noul) ───────────────────────────────
        is_sufficient, gate_score = hallucination_gate(final_nodes, user_query)
        if not is_sufficient:
            logger.warning("Phase 4c — Hallucination gate FAILED (P=%.3f) → abstaining", gate_score)
            return _ABSTAIN_RESPONSE
        logger.info("Phase 4c — Gate passed (P=%.3f)", gate_score)

        # ── Phase 4d: LLM Synthesis ────────────────────────────────────────────
        context_text = self._format_nodes(final_nodes)
        prompt = (
            f"User question: {user_query}\n\n"
            f"Relevant knowledge graph context:\n{context_text}\n\n"
            f"Based only on the above context, provide a concise and accurate answer. "
            f"Do not add information that is not present in the context."
        )
        answer = self._llm.generate(prompt)
        logger.info("Phase 4d — LLM synthesis complete (%d chars)", len(answer))

        # ── Phase 4e: Citation Verification (Noul) ────────────────────────────
        citation = verify_citations(answer, final_nodes)
        if not citation.is_faithful:
            logger.warning(
                "Phase 4e — Citation FAILED (P=%.3f) — prefixing answer", citation.noul_score
            )
            return _FLAGGED_PREFIX + answer

        logger.info("Phase 4e — Citation verified (P=%.3f)", citation.noul_score)
        return answer


def main() -> None:  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Agentic GraphRAG Pipeline")
    parser.add_argument("--query", "-q", required=True, help="User query string")
    parser.add_argument("--max-depth", "-d", type=int, default=4, help="Max A* depth")
    args = parser.parse_args()

    pipeline = GraphRAGPipeline()
    answer = pipeline.query(args.query, max_depth=args.max_depth)
    print(f"\nAnswer:\n{answer}\n")


if __name__ == "__main__":
    main()
