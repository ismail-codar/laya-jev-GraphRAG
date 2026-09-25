"""
graphrag/retrieval/planner/facts.py

Turn an executed plan into context nodes for Phase 4 and into a template
answer. Nodes use the pipeline's {name, text, score} shape so synthesis and
verify_citations consume them unchanged; group results get one node per row
so each claim is checked against a single row.
"""

from __future__ import annotations

from typing import Any

from .plan import QueryPlan

_NO_MATCH = "No matching entities are recorded in the graph"


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _row_text(row: dict[str, Any], key_columns: list[str], metric_columns: list[str]) -> tuple[str, str]:
    keys = ", ".join(f"{c}={_fmt(row[c])}" for c in key_columns)
    metrics = ", ".join(f"{c}={_fmt(row[c])}" for c in metric_columns)
    return keys, metrics


def _names_text(names: list[str], truncated: bool, limit: int) -> str:
    listed = ", ".join(names)
    if truncated:
        return f"first {len(names)} of at least {limit + 1}: {listed}"
    return f"complete: {listed}"


def build_facts(
    plan: QueryPlan,
    description: str,
    rows: list[dict[str, Any]],
    truncated: bool,
    key_columns: list[str],
    metric_columns: list[str],
    names: list[str] | None = None,
) -> list[dict[str, Any]]:
    def node(name: str, text: str) -> dict[str, Any]:
        return {"name": name, "text": text, "score": 1.0}

    query = f"Query: {description}"
    if plan.operation == "count":
        count = rows[0][metric_columns[0]] if rows else 0
        if count == 0:
            return [node("Graph database count", f"{_NO_MATCH}. {query}")]
        listed = f" ({_names_text(names, False, plan.limit)})" if names else ""
        return [node("Graph database count", f"Graph database count: {count} distinct entities match{listed}. {query}")]

    if not rows:
        return [node("Graph database aggregate", f"{_NO_MATCH}. {query}")]

    if plan.operation == "list":
        values = [_fmt(r[key_columns[0]]) for r in rows]
        return [node("Graph database list", f"Graph database list ({_names_text(values, truncated, plan.limit)}). {query}")]

    size = f"first {len(rows)} of at least {plan.limit + 1} groups" if truncated else f"{len(rows)} groups, complete"
    facts = [node("Graph database aggregate", f"Graph database aggregate ({size}). {query}")]
    for i, row in enumerate(rows, 1):
        keys, metrics = _row_text(row, key_columns, metric_columns)
        facts.append(node(f"Graph database aggregate row {i}", f"Group ({keys}): {metrics}."))
    return facts


def template_answer(
    plan: QueryPlan,
    rows: list[dict[str, Any]],
    truncated: bool,
    key_columns: list[str],
    metric_columns: list[str],
    names: list[str] | None = None,
) -> str:
    """Deterministic answer text; no LLM involved."""
    more = " (more exist; showing the first results)" if truncated else ""
    if plan.operation == "count":
        count = rows[0][metric_columns[0]] if rows else 0
        if count == 0:
            return f"{_NO_MATCH}."
        suffix = f": {', '.join(names)}" if names else ""
        return f"{count} recorded in the graph{suffix}"
    if not rows:
        return f"{_NO_MATCH}."
    if plan.operation == "list":
        return ", ".join(_fmt(r[key_columns[0]]) for r in rows) + more
    lines = []
    for row in rows:
        keys, metrics = _row_text(row, key_columns, metric_columns)
        lines.append(f"- {keys}: {metrics}")
    return "\n".join(lines) + more
