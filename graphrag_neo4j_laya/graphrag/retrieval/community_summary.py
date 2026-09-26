"""
graphrag/retrieval/community_summary.py

Community summaries for the `global` route.

A question about the graph as a whole — "What are the main themes of this
knowledge graph?" — has no entry point to start from. Whatever entity a
vector search matches is one of many, and walking out from it answers a
question nobody asked, so the route used to give an account of one corner
of the graph and call it the whole.

The graph already carries what the answer needs: community detection writes
a `communityId` on every node and PageRank writes how central each one is.
A summary is assembled from those two — each community, its most central
members with what the graph says they are, and the relation types that hold
it together — as one context node per community, which the synthesis step
then writes an answer from.

Nothing is generated here and nothing is guessed: every line is read out of
the graph. Backends that cannot run a read query (everything but Kùzu) raise
NotImplementedError, and the route falls back to its traversal.
"""

from __future__ import annotations

import logging
from typing import Any

from config.settings import settings

logger = logging.getLogger(__name__)

# Constant queries with bound parameters: nothing from the question reaches
# the database (the planner's R6 invariant holds here too).
_MEMBERS = """
MATCH (v:Entity)
WHERE v.communityId IS NOT NULL
RETURN v.communityId AS community, v.name AS name, v.pagerank AS pagerank
ORDER BY v.communityId, v.pagerank DESC
LIMIT $scan
"""

_RELATIONS = """
MATCH (a:Entity)-[e:RELATES_TO]->(b:Entity)
WHERE a.communityId = b.communityId
RETURN a.communityId AS community, e.type AS type, count(*) AS relations
ORDER BY relations DESC
LIMIT $scan
"""


def _rows(db, query: str, scan: int) -> list[dict[str, Any]]:
    return db.run_read_query(query, {"scan": scan})


def _described(db, names: list[str]) -> list[str]:
    """The members with the graph's own description of them, where it has one."""
    told = []
    for name in names:
        try:
            # The description ends the sentence the summary puts it in, so
            # its own full stop would double up.
            text = (db.get_node_text(name) or "").strip().rstrip(".")
        except Exception:  # noqa: BLE001 — a backend without node text still summarises
            text = ""
        told.append(f"{name} — {text}" if text and text != name else name)
    return told


def summaries(
    db,
    max_communities: int | None = None,
    members_each: int | None = None,
    scan: int | None = None,
) -> list[dict[str, Any]]:
    """
    One context node per community, the largest first.

    Returns an empty list when the backend cannot read the graph this way or
    when no community has been detected yet — the caller then falls back to
    its traversal.
    """
    max_communities = max_communities or settings.global_max_communities
    members_each = members_each or settings.global_members_per_community
    scan = scan or settings.global_scan_limit
    try:
        member_rows = _rows(db, _MEMBERS, scan)
        relation_rows = _rows(db, _RELATIONS, scan)
    except NotImplementedError:
        logger.info("Community summaries need a backend with run_read_query — falling back")
        return []
    except Exception as exc:  # noqa: BLE001 — never break the pipeline
        logger.warning("Community summaries failed: %s", exc)
        return []

    members: dict[Any, list[str]] = {}
    sizes: dict[Any, int] = {}
    for row in member_rows:
        community = row["community"]
        sizes[community] = sizes.get(community, 0) + 1
        names = members.setdefault(community, [])
        if len(names) < members_each:
            names.append(row["name"])

    relations: dict[Any, list[str]] = {}
    for row in relation_rows:
        relations.setdefault(row["community"], []).append(f"{row['type']} ×{row['relations']}")

    described = settings.global_described_members
    biggest = sorted(sizes, key=lambda c: (-sizes[c], str(c)))[:max_communities]
    nodes = []
    for community in biggest:
        names = members[community]
        text = (f"A group of {sizes[community]} entities in the graph. Most central: "
                f"{'; '.join(_described(db, names[:described]))}.")
        if names[described:]:
            text += f" Also in it: {', '.join(names[described:])}."
        inside = relations.get(community)
        if inside:
            text += f" Relations inside it: {', '.join(inside)}."
        nodes.append({"name": f"Community {community}", "text": text, "score": 1.0})
    logger.info("Community summaries: %d of %d communities", len(nodes), len(sizes))
    return nodes
