"""
graphrag/graph/kuzu_client.py

Kùzu embedded graph database client.
Runs entirely locally in the Python process (no Docker required).
"""

from __future__ import annotations

import logging
import re
from typing import Any
import numpy as np
import networkx as nx

from config.settings import settings
from .base import BaseGraphClient

try:
    import kuzu
except ImportError:
    kuzu = None

logger = logging.getLogger(__name__)

# Second line of defence for run_read_query: the renderer only emits MATCH /
# WITH / RETURN queries, so any of these keywords means the text did not come
# from it.
_NON_READ_KEYWORDS = re.compile(
    r"\b(CREATE|SET|DELETE|DETACH|MERGE|REMOVE|DROP|ALTER|COPY|ATTACH|USE|"
    r"INSTALL|LOAD|EXPORT|IMPORT|CALL|CHECKPOINT|BEGIN|COMMIT|ROLLBACK)\b",
    re.IGNORECASE,
)

# field -> (MATCH pattern, expression) for distinct_values
_DISTINCT_FIELDS = {
    "name":        ("(n:Entity)", "n.name"),
    "communityId": ("(n:Entity)", "n.communityId"),
    "type":        ("(:Entity)-[n:RELATES_TO]->(:Entity)", "n.type"),
}


class KuzuClient(BaseGraphClient):
    def __init__(self, db_path: str | None = None) -> None:
        if kuzu is None:
            raise ImportError("Kuzu is not installed. Run `pip install kuzu`")
        db_path = db_path or settings.kuzu_db_path
        self.db = kuzu.Database(db_path)
        self.conn = kuzu.Connection(self.db)
        logger.info("Kùzu embedded database connected at %s", db_path)

        # In-memory vector store fallback since Kuzu's native vector index
        # is still maturing and we want guaranteed stability right now.
        self._embeddings: dict[str, np.ndarray] = {}

    def close(self) -> None:
        # Kùzu holds the database files open until both handles are closed.
        # Waiting for process exit is enough on POSIX but not on Windows,
        # where an open file keeps its directory from being removed.
        for handle in (self.conn, self.db):
            if handle is not None:
                handle.close()
        self.conn = self.db = None
        self._embeddings.clear()

    def create_schema(self) -> None:
        try:
            self.conn.execute("CREATE NODE TABLE Entity(name STRING, description STRING, pagerank DOUBLE, communityId INT64, PRIMARY KEY (name))")
            self.conn.execute("CREATE REL TABLE RELATES_TO(FROM Entity TO Entity, type STRING)")
            logger.info("Kùzu schema created.")
        except RuntimeError as e:
            if "already exists" in str(e).lower():
                logger.info("Kùzu schema already exists.")
            else:
                raise

    def upsert_node(self, name: str, label: str = "Entity", properties: dict[str, Any] | None = None) -> None:
        # Kuzu's MERGE syntax
        query = "MERGE (n:Entity {name: $name})"
        self.conn.execute(query, parameters={"name": name})
        description = (properties or {}).get("description")
        if description:
            self.conn.execute(
                "MATCH (n:Entity {name: $name}) SET n.description = $description",
                parameters={"name": name, "description": description},
            )

    def get_node_text(self, name: str) -> str:
        results = self.conn.execute(
            "MATCH (n:Entity {name: $name}) RETURN n.description", parameters={"name": name}
        )
        if results.has_next():
            description = results.get_next()[0]
            if description:
                return description
        return name

    def set_embedding(self, name: str, embedding: list[float]) -> None:
        self._embeddings[name] = np.array(embedding, dtype=np.float32)

    def upsert_edge(self, source: str, target: str, rel_type: str, properties: dict[str, Any] | None = None) -> None:
        query = """
        MATCH (a:Entity {name: $src}), (b:Entity {name: $tgt})
        MERGE (a)-[r:RELATES_TO {type: $rel}]->(b)
        """
        self.conn.execute(query, parameters={"src": source, "tgt": target, "rel": rel_type})

    def delete_edge(self, source: str, target: str, rel_type: str) -> None:
        query = """
        MATCH (a:Entity {name: $src})-[r:RELATES_TO {type: $rel}]->(b:Entity {name: $tgt})
        DELETE r
        """
        self.conn.execute(query, parameters={"src": source, "tgt": target, "rel": rel_type})

    def get_neighbors(self, node_name: str) -> list[dict[str, Any]]:
        query = """
        MATCH (n:Entity {name: $name})-[r:RELATES_TO]->(target:Entity)
        RETURN target.name, r.type, target.pagerank
        """
        results = self.conn.execute(query, parameters={"name": node_name})
        
        neighbors = []
        while results.has_next():
            row = results.get_next()
            # Kuzu returns a list for the row values
            neighbors.append({
                "target_name": row[0],
                "type":        row[1],
                "pagerank":    row[2] if row[2] is not None else 0.0,
            })
        return neighbors

    def vector_search(self, query_embedding: list[float], top_k: int = 20) -> list[dict[str, Any]]:
        if not self._embeddings:
            return []
        q_vec = np.array(query_embedding, dtype=np.float32)
        names = list(self._embeddings.keys())
        matrix = np.stack(list(self._embeddings.values()))
        sims = (matrix @ q_vec) / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(q_vec) + 1e-9)
        top_indices = np.argsort(sims)[::-1][:top_k]
        return [{"name": names[i], "score": float(sims[i])} for i in top_indices]

    # ── Introspection (guided query planner) ─────────────────────────────────

    def frontier_moves(self, frontier: list[str] | None) -> list[dict[str, Any]]:
        if frontier is not None and not frontier:
            return []
        moves = []
        for direction, pattern in (("out", "(a:Entity)-[r:RELATES_TO]->(b:Entity)"),
                                   ("in",  "(a:Entity)<-[r:RELATES_TO]-(b:Entity)")):
            where = "WHERE a.name IN $names " if frontier is not None else ""
            # count(DISTINCT) last: Kùzu 0.11.3 zeroes aggregates listed after it.
            query = (
                f"MATCH {pattern} {where}"
                "RETURN r.type, count(*), count(DISTINCT b.name) ORDER BY r.type"
            )
            params = {"names": list(frontier)} if frontier is not None else {}
            results = self.conn.execute(query, parameters=params)
            while results.has_next():
                rel_type, edge_count, neighbor_count = results.get_next()
                moves.append({
                    "type":           rel_type,
                    "direction":      direction,
                    "edge_count":     edge_count,
                    "neighbor_count": neighbor_count,
                })
        return moves

    def distinct_values(self, field: str, limit: int = 50) -> list[Any]:
        if field not in _DISTINCT_FIELDS:
            raise ValueError(f"distinct_values: unsupported field {field!r}")
        pattern, expr = _DISTINCT_FIELDS[field]
        query = (
            f"MATCH {pattern} WHERE {expr} IS NOT NULL "
            f"RETURN DISTINCT {expr} ORDER BY {expr} LIMIT $limit"
        )
        results = self.conn.execute(query, parameters={"limit": limit})
        values = []
        while results.has_next():
            values.append(results.get_next()[0])
        return values

    def run_read_query(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        if _NON_READ_KEYWORDS.search(query):
            raise ValueError("run_read_query: only read-only MATCH queries are allowed")
        results = self.conn.execute(query, parameters=params)
        columns = results.get_column_names()
        rows = []
        while results.has_next():
            rows.append(dict(zip(columns, results.get_next())))
        return rows

    def _get_networkx_graph(self) -> nx.DiGraph:
        query = "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) RETURN a.name, b.name"
        results = self.conn.execute(query)
        G = nx.DiGraph()
        while results.has_next():
            row = results.get_next()
            G.add_edge(row[0], row[1])
        return G

    def run_pagerank(self, graph_name: str = "entity_graph", damping_factor: float = 0.85, max_iterations: int = 20) -> None:
        G = self._get_networkx_graph()
        pr = nx.pagerank(G, alpha=damping_factor, max_iter=max_iterations)
        
        for node, score in pr.items():
            query = "MATCH (n:Entity {name: $name}) SET n.pagerank = $score"
            self.conn.execute(query, parameters={"name": node, "score": score})
        logger.info("Kùzu PageRank computed via NetworkX and written back.")

    def run_community_detection(self, graph_name: str = "entity_graph") -> None:
        G = self._get_networkx_graph().to_undirected()
        components = nx.connected_components(G)
        
        for c_id, comp in enumerate(components):
            for node in comp:
                query = "MATCH (n:Entity {name: $name}) SET n.communityId = $cid"
                self.conn.execute(query, parameters={"name": node, "cid": c_id})
        logger.info("Kùzu WCC computed via NetworkX and written back.")
