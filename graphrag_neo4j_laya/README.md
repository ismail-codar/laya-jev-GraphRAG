# laya-jev-GraphRAG

![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)
![PyTorch](https://img.shields.io/badge/PyTorch-CUDA-EE4C2C.svg)
![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)
![Databases](https://img.shields.io/badge/DB-Neo4j%20%7C%20Memgraph%20%7C%20AGE%20%7C%20Kùzu-018bff.svg)
![Backend](https://img.shields.io/badge/AI%20Backend-Laya%20%7C%20Jev%20%7C%20Ablation-blueviolet.svg)

**Türkçe:** [README_TR.md](README_TR.md)

**laya-jev-GraphRAG** is a **graph-database-agnostic Agentic GraphRAG framework** — a production-ready intelligence layer you drop on top of your existing graph database to make it fully agentic. It doesn't replace your graph DB; it gives it a brain.

Instead of hard-wiring GraphRAG logic to a single database, this framework **completely decouples the AI decision layer from the storage layer**. The same complete **4-phase pipeline** (Ingestion → Pre-Retrieval → Traversal → Post-Retrieval) and evaluation runs identically across Neo4j, Memgraph, Apache AGE, and Kùzu — switched with one environment variable.

At its core, this framework **continuously evaluates every single edge and relationship** across the entire lifecycle of the data. From the moment data enters the graph (Ingestion/Pre-Retrieval), through real-time multi-hop navigation (Traversal), to final synthesis (Post-Retrieval), the engine actively scores, builds, and prunes connections.

Every decision inside that pipeline — from semantic chunking and intent routing to custom A\* traversal and hallucination gating — is handled by swappable **System One models** (local Laya / cloud Jev) using three deterministic mathematical primitives instead of slow generative LLM calls:

| Primitive | What It Does | Where It's Used |
|-----------|---------|---------------|
| **`Score`** | Evaluates edges & relationships `[0, 1]` | Edge verification during ingestion, A\* traversal heuristic, context reranking |
| **`Noul`**  | Binary judgement `P(yes)` `[0, 1]`   | Semantic chunking, entity disambiguation, early termination, hallucination gate, citation verification |
| **`Choice`**| Categorical selection       | Intent routing, ontology alignment, conflict resolution |

Switch the **AI decision model** AND the **graph database** — both independently, with a single environment variable each.

---

## 🛑 Why Traditional GraphRAG Fails

Traditional GraphRAG has three fundamental problems:

1. **LLM at every hop** — Evaluating 5 edges at depth 4 = 20 serial LLM calls = 30–90 seconds of latency and frequent context-window overflow.
2. **No edge verification** — Hallucinated relationships extracted at ingestion time are blindly trusted forever. Bad data compounds through every hop.
3. **Database lock-in** — Traditional implementations hard-wire graph logic to a single database. Swapping Neo4j for Memgraph or AGE requires rewriting the entire pipeline.

## ⚡ The Solution: End-to-End System One Evaluation

This framework solves all three — replacing LLM routing with System One models, actively verifying every edge during ingestion, and abstracting the database entirely behind a unified interface:

| Phase | What Laya/Jev Evaluates |
|-------|------------------------|
| **Ingestion** | Scores edge validity to purge hallucinations, disambiguates entities to prevent graph bloat, and aligns relationships to enforce strict schema |
| **Pre-Retrieval** | Routes query intent to skip unnecessary compute, and validates seed nodes to guarantee the search starts at the optimal mathematical anchor |
| **Traversal** | Laya/Jev dynamically score edges during custom A\* search for intelligent semantic routing, while gating early termination to prevent context bloat and save compute |
| **Post-Retrieval** | Reranks context to maximize token density, resolves contradictory sources for accuracy, gates hallucinations, and strictly verifies citations before LLM synthesis |

The generative LLM (Llama-3.1-8B) only runs **once**, at the very end, to synthesise the already-verified subgraph into a final answer.

---

## 🧠 Architecture Philosophy: Three Separated Layers

Most GraphRAG systems tightly couple storage, reasoning, and generation into one hard-to-swap stack. This framework separates them into **three fully independent layers**:

```
┌───────────────────────────────────────────────────────┐
│  LAYER 1 — STORAGE  (Your Graph DB)                   │
│  Neo4j · Memgraph · Apache AGE · Kùzu                 │
│  Handles: graph structure, PageRank, Cypher queries    │
└───────────────────────────┬───────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────┐
│  LAYER 2 — DECISION  (Laya / Jev)   ← The CPU         │
│  Handles: every routing, scoring, and gating decision  │
│  Score · Noul · Choice across all 19 pipeline steps    │
└───────────────────────────┬───────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────┐
│  LAYER 3 — GENERATION  (Llama-3.1-8B 4-bit)           │
│  Handles: entity extraction (ingestion) +              │
│           final answer synthesis (post-retrieval)      │
│  Runs exactly TWICE per document lifecycle             │
└───────────────────────────────────────────────────────┘
```

Laya/Jev act as the **CPU of your knowledge graph** — the high-speed decision engine routing data between storage and generation without ever generating a single token themselves.

### 🔄 The 2-Axis Swappability

This is the key architectural decision: **both axes are independent**.

| Axis | Options | How to Switch |
|------|---------|---------------|
| **AI Decision Model** | Laya (local GPU) ↔ Jev (cloud API) ↔ Ablation (both) | `DECISION_MODEL_BACKEND=laya\|jev\|ablation` |
| **Graph Database** | Neo4j ↔ Memgraph ↔ Apache AGE ↔ Kùzu | `GRAPH_DB_TYPE=neo4j\|memgraph\|postgres_age\|kuzu` |

You can run **Jev + Neo4j** in production, **Laya + Kùzu** for local development with zero Docker, or **Ablation + Memgraph** to generate training data — all from the same codebase with zero code changes.

### 🔁 The RLCD Flywheel: From Cloud to Fully Local

The framework is designed around a self-improving loop:

```
1. START  →  Deploy with Jev (cloud API, zero-shot accurate, instant setup)
2. COLLECT →  Ablation mode logs every Laya vs. Jev decision side-by-side to JSONL
3. TRAIN   →  Use those JSONL logs as RLCD synthetic training data to fine-tune Laya
4. SWITCH  →  Flip DECISION_MODEL_BACKEND=laya for 100% local, zero API cost
```

The end state: a **fully local, frontier-quality GraphRAG engine** on your own hardware with no API dependency and no data leaving your network.

---

## 🔌 Backend Configuration

```bash
# AI Decision Model (.env)
DECISION_MODEL_BACKEND=laya      # Local (CUDA or CPU), free, 100% private
DECISION_MODEL_BACKEND=jev       # TypeSafe cloud API, zero-shot ready, ~50ms/call
DECISION_MODEL_BACKEND=ablation  # Run BOTH, log side-by-side for RLCD fine-tuning

# Graph Database (.env)
GRAPH_DB_TYPE=neo4j          # Production: index-free adjacency, native GDS
GRAPH_DB_TYPE=memgraph       # In-memory Bolt: identical Cypher, low-latency analytics
GRAPH_DB_TYPE=postgres_age   # PostgreSQL + Apache AGE: unified SQL/graph stack
GRAPH_DB_TYPE=kuzu           # Embedded local: no Docker, zero setup for development
```

| Feature | Laya (Local) | Jev (Cloud) |
|---------|-------------|-------------|
| Model | `convaiinnovations/laya` (multilingual 322M, default) | `jev-1.13` (TypeSafe) |
| Latency | ~33 ms (RTX 5060 FP16) | ~50 ms (API round-trip) |
| Cost | Free | ~$0.042 / M tokens |
| Privacy | 100% local | API |
| Batch | GPU-batched | True parallel (1 API call) |

All four graph DB backends implement the same `BaseGraphClient` interface. **Zero code changes** needed when switching databases.

---

## 🗺️ The 19-Function Pipeline

```
┌─────────────────────────────────────────────────────────┐
│  PHASE 1 — Ingestion (Offline, runs once per document)  │
│                                                         │
│  1. Semantic Chunking      → Noul   (boundary detect)   │
│  2. Entity Extraction      → LLM    (Llama-3.1-8B)      │
│  3. Entity Disambiguation  → Noul   (merge duplicates)   │
│  4. Edge Verification      → Score  (prune hallucinated) │
│  5. Ontology Alignment     → Choice (snap to schema)     │
│  6. Community Detection    → Leiden / NetworkX           │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│  PHASE 2 — Pre-Traversal (Per query)                    │
│                                                         │
│  7. Intent Routing         → Choice (local/multi/global) │
│  8. Dense Seed Retrieval   → Embedding cosine           │
│  9. Seed Validation        → Score  (filter bad seeds)   │
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│  PHASE 3 — A* Traversal (Per query)                     │
│                                                         │
│  10. Neighborhood Fetch    → Bolt / Cypher              │
│  11. Edge Scoring          → Score  (semantic heuristic) │
│  12. Structural Anchoring  → PageRank (centrality)       │
│  13. Path Pruning          → Beam cutoff                │
│  14. Early Termination     → Noul   (context sufficient?)│
└─────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────────┐
│  PHASE 4 — Post-Traversal (Per query)                   │
│                                                         │
│  15. Context Reranking     → Score  (drop low-relevance) │
│  16. Conflict Resolution   → Choice (pick credible src)  │
│  17. Hallucination Gate    → Noul   (abstain if unsafe)  │
│  18. Answer Synthesis      → LLM    (Llama-3.1-8B 4-bit) │
│  19. Citation Verification → Noul   (flag ungrounded)    │
└─────────────────────────────────────────────────────────┘
```

> **📚 Want to see the full breakdown of all 19 functions?**
> Check out the [Architecture Deep Dive (ARCHITECTURE.md)](ARCHITECTURE.md) for a complete breakdown of every primitive and routing decision across all 4 phases.

> **💡 Is this a Static Knowledge Store or an Agentic Memory? (And wild use cases)**
> Check out [Use Cases (USE_CASES.md)](USE_CASES.md) to see how the decoupled intelligence layer allows this to act as both a high-fidelity query engine (fraud, bio-med) and a self-organizing memory store for autonomous agents etc.

---

## 🚀 Getting Started

### Installation

```bash
git clone https://github.com/bodepudimuneendra-netizen/laya-jev-GraphRAG.git
cd laya-jev-GraphRAG/graphrag_neo4j_laya

# Optional: PyTorch with CUDA 12.4 (skip on CPU-only machines, Laya also runs on CPU)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124

# All remaining dependencies (includes `laya` and `kuzu`)
pip install -r requirements.txt
```

### Quickstart: Laya + Kùzu, no Docker needed

The fastest way to see the whole pipeline work is the bundled example. It uses **Laya** (local, no API key) as the decision model and **Kùzu** (embedded, runs inside the Python process) as the graph database. It runs on CPU. A GPU makes it faster but isn't needed.

```bash
cd graphrag_neo4j_laya
python examples/laya_kuzu_quickstart.py
```

The first run downloads the Laya multilingual checkpoint (~1.3 GB) and the multilingual embedding model. After that, the full run (ingestion plus three queries) takes about a minute on CPU.

What the script does, using `examples/data/science_history.json`:

| Step | Laya primitive | What happens |
|------|----------------|--------------|
| 1. Load entities | — | 14 nodes with descriptions and embeddings go into Kùzu |
| 2. Edge verification | `Noul` | Each extracted triple is checked against the source text it came from; unsupported triples are dropped |
| 3. Ontology alignment | `Choice` | Free-text relations (`"first detected"`) are mapped onto the dataset schema (`DISCOVERED`) |
| 4. Structure | — | PageRank + connected components are written back to Kùzu |
| 5. Query | `Choice` · `Score` · `Noul` | Intent routing, seed validation, A\*/BFS traversal, reranking, hallucination gate, citation check |

Output from a CPU run (default multilingual checkpoint):

```text
[2/4] Edge verification against source text (Laya Noul, keep >= 0.5)
  keep  P=0.996  Isaac Newton -[was born in]-> Woolsthorpe
  keep  P=0.974  LIGO -[first detected]-> Gravitational Waves
  ...
  PRUNE P=0.000  Isaac Newton -[baked]-> Banana Bread        ← hallucinated, removed
  PRUNE P=0.006  LIGO -[was born in]-> Ulm                   ← hallucinated, removed

[3/4] Ontology alignment (Laya Choice)
                     'wrote' → AUTHORED
          'was president of' → LEADS
            'first detected' → DISCOVERED

Q: Where was Isaac Newton born?
- Woolsthorpe: Isaac Newton BORN_IN Woolsthorpe. Hamlet in Lincolnshire, England, where Isaac Newton was born.
- Isaac Newton: English physicist and mathematician, author of the laws of motion and universal gravitation.

Q: How is Newton's work connected to Einstein's theory of gravity?
I don't have enough verified information in my knowledge graph to confidently answer this question. ...

Q: Yerçekimi dalgalarını ilk kim tespit etti?
- Gravitational Waves: LIGO DISCOVERED Gravitational Waves. Ripples in spacetime predicted by general relativity.
...
```

Every outcome in that run comes from a pipeline safety check doing its job:
- **Q1:** routed `local`, answered from the verified `BORN_IN` edge, citation check passed.
- **Q2:** routed `multi_hop`. The hallucination gate judged the retrieved context too weak and the pipeline abstained rather than guess.
- **Q3** (Turkish, against English data): retrieval found the `LIGO DISCOVERED Gravitational Waves` fact, and every claim in the answer passed the citation check (weakest claim 0.92).

#### Quickstart options

```bash
# Your own questions (repeatable)
python examples/laya_kuzu_quickstart.py -q "Where was Albert Einstein born?" -q "Who wrote Principia Mathematica?"

# Log every phase: intent, seeds, gate and citation scores
python examples/laya_kuzu_quickstart.py -v

# Your own dataset, deeper A* search, stricter edge verification
python examples/laya_kuzu_quickstart.py --data my_graph.json --max-depth 4 --support-threshold 0.8

# Real LLM synthesis instead of the extractive answer (needs CUDA + bitsandbytes)
python examples/laya_kuzu_quickstart.py --llm llama
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--data` | `examples/data/science_history.json` | Dataset to ingest |
| `-q / --query` | dataset `queries` | Question to run (repeatable) |
| `-d / --max-depth` | `3` | Maximum A\* hop depth |
| `--support-threshold` | `0.5` | Minimum Laya `P(source supports edge)` to keep a triple |
| `--llm` | `extractive` | `extractive` returns the verified context itself (no GPU); `llama` uses Llama-3.1-8B NF4 |
| `-v / --verbose` | off | Print the log line for every pipeline phase |

The script pins `DECISION_MODEL_BACKEND=laya` and `GRAPH_DB_TYPE=kuzu`, and rebuilds the database on each run. Everything else (Laya checkpoint, thresholds) still comes from `.env`.

#### Bring your own data

The dataset is a single JSON file. `triples` are what an LLM extraction step would produce: a free-text relation plus the id of the document chunk it was extracted from.

```json
{
  "schema":    { "BORN_IN": "was born in a place", "DISCOVERED": "discovered, detected or first observed something" },
  "documents": { "doc1": "Marie Curie was born in Warsaw. She discovered polonium and radium." },
  "entities":  { "Marie Curie": "Physicist and chemist, pioneer of radioactivity research.",
                 "Warsaw": "Capital city of Poland." },
  "triples":   [ ["Marie Curie", "was born in", "Warsaw", "doc1"] ],
  "queries":   [ "Where was Marie Curie born?" ]
}
```

`schema` is optional; without it the built-in 17-type ontology in `graphrag/ingestion/ontology_aligner.py` is used. A small schema written for your domain aligns much better.

### Usage examples (Python)

#### 1. Use the Laya primitives directly

```python
from graphrag.models.laya import get_laya

laya = get_laya()   # loads convaiinnovations/laya (multilingual) once, then stays resident

# Score: ordinal relevance in [0, 1]
laya.score(
    "Fact: LIGO DISCOVERED Gravitational Waves.",
    "How relevant is this fact to answering: 'Who first detected gravitational waves?'",
)   # → 0.65

# Noul: binary P(yes)
laya.noul(
    "Source text: Isaac Newton was born in Woolsthorpe.\n\n"
    "Extracted relationship: Isaac Newton -[baked]-> Banana Bread",
    "Does the source text explicitly support this extracted relationship?",
)   # → 0.03

# Choice: returns one option key; all options are scored in a single forward pass
laya.choice(
    "User question: How is Newton's work connected to Einstein's theory of gravity?",
    "What does the question ask about?",
    {
        "local":     "The answer is one fact about one named entity.",
        "multi_hop": "The answer is the chain of relations between two named entities.",
        "global":    "The answer is a summary of the graph as a whole.",
    },
)   # → "multi_hop"

# Several questions about the same state, answered in one forward pass
laya.ask_batch(
    "Customer: my invoice was charged twice this month, please refund one.",
    {
        "is_billing": {"type": "noul",   "instruction": "Is this a billing issue?"},
        "urgency":    {"type": "score",  "instruction": "How urgent is this request?"},
        "team":       {"type": "choice", "instruction": "Route to which team?",
                       "options": {"billing": "Billing team", "tech": "Technical support"}},
    },
)   # → {"is_billing": DecisionResult(score=0.81, ...), "team": DecisionResult(selected="billing", ...), ...}
```

Every `*_detailed` method (`score_detailed`, `noul_detailed`, `choice_detailed`) returns a `DecisionResult` carrying the probabilities, confidence and latency.

#### 2. Build and query a Kùzu graph from your own code

```python
import os
os.environ["GRAPH_DB_TYPE"] = "kuzu"            # or set it in .env
os.environ["DECISION_MODEL_BACKEND"] = "laya"

from sentence_transformers import SentenceTransformer

from config.settings import settings
from graphrag.graph.kuzu_client import KuzuClient
from graphrag.ingestion.edge_verifier import EdgeVerifier
from graphrag.ingestion.ontology_aligner import OntologyAligner
from graphrag.pipeline import GraphRAGPipeline

source = "Marie Curie was born in Warsaw. She discovered polonium and radium."
entities = {
    "Marie Curie": "Physicist and chemist, pioneer of research on radioactivity.",
    "Warsaw": "Capital city of Poland, where Marie Curie was born.",
    "Paris": "Capital city of France.",
    "Polonium": "Radioactive chemical element discovered by Marie Curie in 1898.",
    "Radium": "Radioactive chemical element discovered by the Curies in 1898.",
}
triples = [
    ("Marie Curie", "was born in", "Warsaw"),
    ("Marie Curie", "discovered", "Polonium"),
    ("Marie Curie", "discovered", "Radium"),
    ("Marie Curie", "was born in", "Paris"),     # hallucinated → rejected (P≈0.01)
]

db = KuzuClient()                               # KUZU_DB_PATH, default ./kuzu_db
db.create_schema()
embedder = SentenceTransformer(settings.embed_model_id)
for name, description in entities.items():
    db.upsert_node(name, properties={"description": description})
    db.set_embedding(name, embedder.encode(f"{name}: {description}", normalize_embeddings=True).tolist())

verifier, aligner = EdgeVerifier(db), OntologyAligner()
for src, raw_rel, tgt in triples:
    if verifier.verify_against_source(src, raw_rel, tgt, source) >= 0.5:        # Noul
        db.upsert_edge(src, tgt, aligner.align(raw_rel, source=src, target=tgt))  # Choice
db.run_pagerank()


class EchoLLM:
    """Stand-in synthesiser: returns the verified context lines (no GPU needed)."""
    def generate(self, prompt: str, **_) -> str:
        return "\n".join(l for l in prompt.splitlines() if l.startswith("- "))


pipeline = GraphRAGPipeline(graph_client=db, llm=EchoLLM())   # llm=None → Llama-3.1-8B
print(pipeline.query("Where was Marie Curie born?"))
```

> **Kùzu note:** `KuzuClient` keeps node embeddings in process memory. Build the graph and query it **in the same process** (as above and in the quickstart). If you run `python -m graphrag.pipeline` against a Kùzu database built by another process, seed selection finds no entry points. Use Neo4j, Memgraph or AGE for a graph that outlives the process.

#### 3. Run the full CLI pipeline (Neo4j / Memgraph / AGE + Llama)

```bash
docker compose up -d neo4j          # or: memgraph | apache-age (see docker-compose.yml)
cp .env.example .env                # set GRAPH_DB_TYPE=neo4j, HUGGINGFACE_TOKEN=...
python -m graphrag.pipeline --query "What caused the 2008 financial crisis?" --max-depth 4
```

```python
from graphrag.pipeline import GraphRAGPipeline

pipeline = GraphRAGPipeline()       # graph DB + decision model from .env, Llama for synthesis
print(pipeline.query("What caused the 2008 financial crisis?"))
```

#### 4. Choose a Laya checkpoint

Laya loads through the official [`laya`](https://pypi.org/project/laya/) package. Pick the checkpoint in `.env`:

```bash
LAYA_MODEL_ID=convaiinnovations/laya
LAYA_MODEL_SUBFOLDER=multilingual     # default: mmBERT-base, 322M, 100+ languages (Turkish included)
# LAYA_MODEL_SUBFOLDER=               # English, ModernBERT-large, 421M
# LAYA_MODEL_SUBFOLDER=typed-decisions # English specialist for 4 synthetic workflows
LAYA_DEVICE=                          # cuda | cpu, auto-detected when empty
```

Same quickstart, CPU, both checkpoints:

| Checkpoint | Hallucinated edges pruned | Queries answered (verified / flagged / abstained) | Ingestion |
|------------|---------------------------|---------------------------------------------------|-----------|
| `multilingual` (default) | 2 / 2 | 2 / 0 / 1 | ~25 s |
| English root | 2 / 2 | 1 / 0 / 2 | ~50 s |

#### 5. Run the ablation harness

```python
from graphrag.models.ablation import AblationHarness

harness = AblationHarness()
result = harness.compare(
    context="Isaac Newton published Principia Mathematica in 1687.",
    instruction="Score the historical significance of this event.",
)
print(f"Laya: {result['laya']['score']:.3f} @ {result['laya']['latency_ms']:.0f}ms")
print(f"Jev:  {result['jev']['score']:.3f}  @ {result['jev']['latency_ms']:.0f}ms")
print(f"Δ:    {result['delta']:.4f}")
```

#### 6. Aggregate questions (experimental guided query planner)

Questions like "how many", "which has the most" or "list every" need the database to count, not the LLM. The guided query planner builds a typed query plan step by step: code enumerates the legal moves from the live graph (relation types and directions that exist on the current frontier, whitelisted fields and operators), and Laya only picks among them. The plan is rendered to parameterised Kùzu Cypher, read back against the question — the plan and its runner-up are scored with the same Noul and the better one runs — and executed. Details and measurements: [AGGREGATION_METHODS.md](AGGREGATION_METHODS.md), method 6.

Kùzu only, and not available with `DECISION_MODEL_BACKEND=ablation`. The router takes this route when the question asks for a number, for every match, for a ranking or for a breakdown — which is read from its words, not chosen by the model. It is **on by default**:

```bash
# .env
AGGREGATE_ROUTE_ENABLED=false     # turn the route off and leave the router its three strategies
AGGREGATE_ANSWER_MODE=template    # 'template' (no LLM, default) or 'llm' (facts + citation check)
RELATION_SCHEMA_PATH=examples/data/science_history.json   # relation descriptions for the planner
```

With the flag off, call the planner directly. It bypasses the router and returns the plan, the Cypher, the rows and the step trace, or `None` when no plan was accepted:

```python
result = pipeline.query_aggregate("How many places was Isaac Newton born in?")
if result:
    print(result.description)   # the plan in plain English
    print(result.cypher, result.params)
    print(result.answer)        # template answer
```

Measure the planner on the labelled question set (`examples/data/aggregate_eval.json`, 40 questions) with the real Laya model:

```bash
python -m graphrag.benchmarks.aggregate_planner_eval   # prints a table, writes benchmarks/results/aggregate_planner_eval.json
```

#### 7. Questions about the whole graph (`global` route)

"What are the main themes of this knowledge graph?" names no entity to start from, so walking out from whatever a vector search matched answers a question nobody asked. The `global` route reads the graph's own partition instead: community detection writes a `communityId` on every node and PageRank writes how central each one is, so each community is summarised as its most central members — with the graph's own description of them — and the relation types that hold it together. Those summaries are the context the answer is written from; nothing is generated on the way. The hallucination gate is skipped here for the reason the aggregate route skips it: the summaries are the graph's own partition, not a retrieval that may have missed something. Citation verification still holds the answer to them.

The route is taken when the question names the graph and asks about the whole of it ("overview", "main themes", "özet"), which is read from its words. Community summaries need a backend that can run a read query (Kùzu); elsewhere the route falls back to its earlier shallow walk from the seeds.

```bash
# .env
GLOBAL_MAX_COMMUNITIES=8          # communities summarised, largest first
GLOBAL_MEMBERS_PER_COMMUNITY=8    # members named in each summary
GLOBAL_DESCRIBED_MEMBERS=3        # of those, how many are quoted with their description
```

### What Laya can and can't judge

Laya is a System One decision model. It judges the **state you give it**, and it has no world knowledge of its own. In practice that means:
- **Verify edges against their source text** (`EdgeVerifier.verify_against_source`), not in isolation. Asked "is *Newton → born in → Woolsthorpe* valid?" without evidence, Laya scores it about the same as *Newton → baked → Banana Bread*. Given the source chunk, it separates them cleanly (0.996 vs 0.000).
- **Evidence checks catch unsupported entities, not every wrong relation.** A triple whose target never appears in the source (`born in Paris`, P≈0.01) is rejected. A wrong relation between two entities that are both in the source (`Marie Curie invented Warsaw`, P≈0.98) can slip through.
- **The citation check is per claim, and same-language only.** `verify_citations` splits the answer into sentences, checks each against the context and keeps the weakest score, so one invented sentence fails the whole answer (0.00 vs 0.99 for the supported ones). Laya can't match a claim across languages, though: a correct Turkish claim against English context scores ~0.01, so an LLM answer written in another language than the graph is always flagged `[⚠️ UNVERIFIED]`. It errs safe, and never passes a wrong claim. Keep answers in the language of the graph if you need them verified.
- **Treat thresholds as tunable.** The model card flags the probabilities as over-confident and not yet calibrated. Adjust `BFS_PRUNE_THRESHOLD`, `SEED_FINAL_TOP_K` and the gate/citation thresholds in `graphrag/retrieval/post_traversal.py` against your own data.

---

## 📁 Project Structure

```
graphrag_neo4j_laya/
├── graphrag/
│   ├── graph/                    # DB abstraction layer
│   │   ├── base.py               # BaseGraphClient ABC
│   │   ├── factory.py            # GRAPH_DB_TYPE selector
│   │   ├── neo4j_client.py       # Neo4j (Bolt + GDS)
│   │   ├── memgraph_client.py    # Memgraph (Bolt)
│   │   ├── age_client.py         # Apache AGE (PostgreSQL)
│   │   └── kuzu_client.py        # Kùzu (embedded)
│   ├── models/                   # AI decision layer
│   │   ├── base_decision.py      # BaseDecisionModel ABC (Score/Noul/Choice)
│   │   ├── laya.py               # Local Laya model via the `laya` package
│   │   ├── jev.py                # TypeSafe Jev API client
│   │   ├── ablation.py           # Side-by-side comparison harness
│   │   └── decision_factory.py   # DECISION_MODEL_BACKEND selector
│   ├── ingestion/                # Phase 1: offline pipeline
│   │   ├── chunker.py            # Noul boundary chunking
│   │   ├── entity_extractor.py   # LLM NER + Noul disambiguation
│   │   ├── edge_verifier.py      # Score-based edge pruning
│   │   ├── ontology_aligner.py   # Choice-based schema alignment
│   │   └── community.py          # Leiden community detection
│   ├── retrieval/                # Phases 2-4: query pipeline
│   │   ├── router.py             # Choice: intent routing
│   │   ├── community_summary.py  # `global` route: communities read from the graph
│   │   ├── seed_selector.py      # Score: seed validation
│   │   ├── post_traversal.py     # Score/Choice/Noul: post-processing
│   │   └── traversal/
│   │       ├── astar.py          # A* semantic traversal + early exit
│   │       └── bfs.py            # Score-gated BFS (local intent)
│   ├── benchmarks/               # Performance analysis
│   │   ├── hop_latency.py        # Per-hop DB latency across all backends
│   │   ├── laya_vs_jev.py        # Side-by-side model comparison
│   │   ├── greedy_vs_astar.py    # Search strategy comparison
│   │   └── vram_monitor.py       # GPU memory tracking
│   └── pipeline.py               # Full end-to-end orchestrator
├── examples/
│   ├── laya_kuzu_quickstart.py   # End-to-end demo: Laya + Kùzu, no Docker
│   └── data/science_history.json # Demo dataset (documents, triples, schema, queries)
├── config/
│   ├── settings.py               # Pydantic settings (all thresholds)
│   └── .env.example              # Template with all toggles
└── requirements.txt
```

---

## 🔬 The Ablation Mode: RLCD Data Factory

Setting `DECISION_MODEL_BACKEND=ablation` silently runs both Laya and Jev in parallel for every primitive call and logs the results to a JSONL file:

```json
{"primitive": "score", "context": "...", "instruction": "...",
 "laya": {"score": 0.83, "latency_ms": 34.1},
 "jev":  {"score": 0.79, "latency_ms": 48.3},
 "delta": 0.04}
```

This JSONL log is ready-to-use RLCD training data to fine-tune Laya towards Jev-level zero-shot accuracy — the path to a fully local, frontier-quality GraphRAG engine.

---

## 📊 Performance

| Backend | Edge Score Latency | Traversal (4-hop) | VRAM |
|---------|-------------------|-------------------|------|
| Laya (RTX 5060 FP16) | ~33 ms | ~150 ms | ~1.2 GB |
| Jev (cloud API) | ~50 ms | ~220 ms | 0 |
| LLM-per-hop (baseline) | ~2,000 ms | ~15,000 ms | ~6 GB |

---

## 🤝 Contributing

Contributions welcome:
- Additional graph DB connectors (Nebula, TigerGraph, FalkorDB, etc.)
- Laya fine-tuning scripts from ablation JSONL logs
- Jev async/streaming support
- New ingestion sources (PDF, HTML, Markdown)


---

## 📄 License

Licensed under the Apache 2.0 License.

Built on:
- [Laya](https://huggingface.co/convaiinnovations/laya) (Apache 2.0, mmBERT / ModernBERT)
- [TypeSafe Jev API](https://typesafe.ai)
- [Neo4j](https://neo4j.com) · [Memgraph](https://memgraph.com) · [Apache AGE](https://age.apache.org) · [Kùzu](https://kuzudb.com)
