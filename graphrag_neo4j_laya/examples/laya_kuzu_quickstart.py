"""
examples/laya_kuzu_quickstart.py

End-to-end GraphRAG demo with Laya (local System One model) on Kùzu
(embedded graph DB) — no Docker, no API key, runs on CPU or CUDA.

What it does
------------
Phase 1 — Ingestion (from pre-extracted triples in examples/data/*.json)
    Edge Verification    → Laya Noul    (is the triple supported by its source chunk?)
    Ontology Alignment   → Laya Choice  (raw relation string → canonical edge type)
    PageRank + WCC       → NetworkX, written back to Kùzu
Phases 2-4 — Query (full GraphRAGPipeline)
    Intent Routing → Seed Validation → A*/BFS traversal → Reranking
    → Hallucination Gate → Synthesis → Citation Verification

Answer synthesis defaults to an extractive synthesiser (no GPU needed). Pass
``--llm llama`` to use Llama-3.1-8B NF4 instead (requires CUDA + bitsandbytes).

Usage (run from graphrag_neo4j_laya/)
-----
    python examples/laya_kuzu_quickstart.py
    python examples/laya_kuzu_quickstart.py --query "Where was Isaac Newton born?"
    python examples/laya_kuzu_quickstart.py --data my_graph.json --max-depth 3 -v
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# This demo is pinned to Laya + Kùzu; environment variables take precedence
# over .env in pydantic-settings, so set them before `config` is imported.
os.environ["DECISION_MODEL_BACKEND"] = "laya"
os.environ["GRAPH_DB_TYPE"] = "kuzu"
os.environ.setdefault("KUZU_DB_PATH", str(ROOT / "examples" / ".kuzu_demo"))
os.environ.setdefault("SEED_COLBERT_TOP_N", "8")    # fewer seed candidates → faster on CPU
os.environ.setdefault("BFS_PRUNE_THRESHOLD", "0.5")
os.environ.setdefault("SEED_FINAL_TOP_K", "3")
os.environ.setdefault("USE_TF", "0")

from config.settings import settings                             # noqa: E402
from graphrag.graph.kuzu_client import KuzuClient                # noqa: E402
from graphrag.ingestion.edge_verifier import EdgeVerifier        # noqa: E402
from graphrag.ingestion.ontology_aligner import OntologyAligner  # noqa: E402
from graphrag.pipeline import GraphRAGPipeline                   # noqa: E402
from graphrag.retrieval.seed_selector import _get_embedder       # noqa: E402


class ExtractiveSynthesizer:
    """
    GPU-free stand-in for the LLM: answers with the verified graph context
    itself (entity + description), in the order the pipeline ranked it.
    """

    def generate(self, prompt: str, **_: object) -> str:
        return "\n".join(re.findall(r"^- .+$", prompt, flags=re.MULTILINE))


def build_graph(db: KuzuClient, data: dict, support_threshold: float) -> None:
    entities: dict[str, str] = data["entities"]
    documents: dict[str, str] = data["documents"]
    db.create_schema()

    # Nodes + embeddings (name + description, so seeds match on meaning)
    names = list(entities)
    vectors = _get_embedder().encode(
        [f"{n}: {entities[n]}" for n in names], normalize_embeddings=True
    )
    for name, vec in zip(names, vectors):
        db.upsert_node(name, properties={"description": entities[name]})
        db.set_embedding(name, vec.tolist())
    print(f"\n[1/4] {len(names)} entities loaded into Kùzu")

    # Edge verification against the source chunk (Laya Noul)
    print(f"\n[2/4] Edge verification against source text (Laya Noul, keep >= {support_threshold})")
    verifier = EdgeVerifier(db)
    verified: list[tuple[str, str, str]] = []
    for src, raw_rel, tgt, doc_id in data["triples"]:
        p = verifier.verify_against_source(src, raw_rel, tgt, documents[doc_id])
        keep = p >= support_threshold
        print(f"  {'keep ' if keep else 'PRUNE'} P={p:.3f}  {src} -[{raw_rel}]-> {tgt}")
        if keep:
            verified.append((src, raw_rel, tgt))

    # Ontology alignment (Laya Choice)
    print("\n[3/4] Ontology alignment (Laya Choice)")
    aligner = OntologyAligner(schema=data.get("schema"))
    for src, raw_rel, tgt in verified:
        rel = aligner.align(raw_rel, source=src, target=tgt)
        db.upsert_edge(src, tgt, rel)
        print(f"  {raw_rel!r:>26} → {rel}")

    # Structural signals for A*
    db.run_pagerank()
    db.run_community_detection()
    print("\n[4/4] PageRank + communities written to Kùzu")


def main() -> None:
    parser = argparse.ArgumentParser(description="Laya + Kùzu GraphRAG quickstart")
    parser.add_argument("--data", type=Path, default=ROOT / "examples" / "data" / "science_history.json")
    parser.add_argument("--query", "-q", action="append",
                        help="Query to run (repeatable). Defaults to the dataset's queries.")
    parser.add_argument("--max-depth", "-d", type=int, default=3)
    parser.add_argument("--support-threshold", type=float, default=0.5,
                        help="Minimum Laya P(source supports edge) to keep a triple")
    parser.add_argument("--llm", choices=["extractive", "llama"], default="extractive")
    parser.add_argument("--verbose", "-v", action="store_true", help="Log every pipeline phase")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="  %(name)s | %(message)s")
    if args.verbose:
        logging.getLogger("graphrag").setLevel(logging.INFO)

    data = json.loads(args.data.read_text(encoding="utf-8"))
    print(f"Laya: {settings.laya_model_id} (subfolder={settings.laya_model_subfolder or 'root'})")
    print(f"Graph DB: Kùzu at {settings.kuzu_db_path}")

    # The Kùzu embedding index lives in process memory, so rebuild per run.
    db_path = Path(settings.kuzu_db_path)
    if db_path.is_dir():
        shutil.rmtree(db_path)
    elif db_path.exists():
        db_path.unlink()
    db = KuzuClient(str(db_path))

    t0 = time.perf_counter()
    build_graph(db, data, args.support_threshold)
    print(f"Ingestion finished in {time.perf_counter() - t0:.1f}s")

    llm = ExtractiveSynthesizer() if args.llm == "extractive" else None
    pipeline = GraphRAGPipeline(graph_client=db, llm=llm)

    for query in args.query or data.get("queries", []):
        print("\n" + "=" * 70 + f"\nQ: {query}")
        t0 = time.perf_counter()
        answer = pipeline.query(query, max_depth=args.max_depth)
        print(f"\n{answer}\n({time.perf_counter() - t0:.1f}s)")


if __name__ == "__main__":
    main()
