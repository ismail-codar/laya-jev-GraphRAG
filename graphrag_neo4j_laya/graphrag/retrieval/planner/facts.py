"""
graphrag/retrieval/planner/facts.py

Turn an executed plan into context nodes for Phase 4 and into a template
answer. Nodes use the pipeline's {name, text, score} shape so synthesis and
verify_citations consume them unchanged; group results get one node per row
so each claim is checked against a single row.

With a semantic filter the plan runs twice (sure set, sure + uncertain set);
`upper_rows` holds the second result and every number becomes a range.
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


def _count(rows: list[dict[str, Any]], metric_columns: list[str]) -> int:
    return rows[0][metric_columns[0]] if rows else 0


def _range(lo: Any, hi: Any) -> str:
    if lo is None:
        return f"up to {_fmt(hi)}"
    return _fmt(lo) if lo == hi else f"{_fmt(lo)} to {_fmt(hi)}"


def _group_lines(
    rows: list[dict[str, Any]],
    upper_rows: list[dict[str, Any]] | None,
    key_columns: list[str],
    metric_columns: list[str],
) -> list[tuple[str, str]]:
    """(keys, metrics) text per group; metrics are ranges when *upper_rows* is given."""
    if upper_rows is None:
        return [_row_text(r, key_columns, metric_columns) for r in rows]
    lower = {tuple(r[c] for c in key_columns): r for r in rows}
    lines = []
    for row in upper_rows:
        low = lower.get(tuple(row[c] for c in key_columns))
        keys = ", ".join(f"{c}={_fmt(row[c])}" for c in key_columns)
        metrics = ", ".join(f"{c}={_range(low[c] if low else None, row[c])}" for c in metric_columns)
        lines.append((keys, metrics))
    return lines


def _uncertain_text(uncertain: list[str] | None) -> str:
    return f"; uncertain: {', '.join(uncertain)}" if uncertain else ""


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
    upper_rows: list[dict[str, Any]] | None = None,
    uncertain: list[str] | None = None,
) -> list[dict[str, Any]]:
    def node(name: str, text: str) -> dict[str, Any]:
        return {"name": name, "text": text, "score": 1.0}

    query = f"Query: {description}"
    if plan.operation == "count":
        count = _count(rows, metric_columns)
        upper = _count(upper_rows, metric_columns) if upper_rows is not None else count
        if upper == 0:
            return [node("Graph database count", f"{_NO_MATCH}. {query}")]
        if upper != count:
            sure = ", ".join(names or []) or "none"
            return [node("Graph database count",
                         f"Graph database count: between {count} and {upper} distinct entities match "
                         f"(sure: {sure}{_uncertain_text(uncertain)}). {query}")]
        listed = f" ({_names_text(names, False, plan.limit)})" if names else ""
        return [node("Graph database count", f"Graph database count: {count} distinct entities match{listed}. {query}")]

    shown = upper_rows if upper_rows is not None else rows
    if not shown:
        return [node("Graph database aggregate", f"{_NO_MATCH}. {query}")]

    if plan.operation == "list":
        values = [_fmt(r[key_columns[0]]) for r in rows]
        listed = _names_text(values, truncated, plan.limit) if values else "no sure matches"
        return [node("Graph database list", f"Graph database list ({listed}{_uncertain_text(uncertain)}). {query}")]

    size = f"first {len(shown)} of at least {plan.limit + 1} groups" if truncated else f"{len(shown)} groups, complete"
    facts = [node("Graph database aggregate", f"Graph database aggregate ({size}{_uncertain_text(uncertain)}). {query}")]
    for i, (keys, metrics) in enumerate(_group_lines(rows, upper_rows, key_columns, metric_columns), 1):
        facts.append(node(f"Graph database aggregate row {i}", f"Group ({keys}): {metrics}."))
    return facts


def template_answer(
    plan: QueryPlan,
    rows: list[dict[str, Any]],
    truncated: bool,
    key_columns: list[str],
    metric_columns: list[str],
    names: list[str] | None = None,
    upper_rows: list[dict[str, Any]] | None = None,
    uncertain: list[str] | None = None,
) -> str:
    """Deterministic answer text; no LLM involved."""
    more = " (more exist; showing the first results)" if truncated else ""
    unsure = f" (uncertain: {', '.join(uncertain)})" if uncertain else ""
    if plan.operation == "count":
        count = _count(rows, metric_columns)
        upper = _count(upper_rows, metric_columns) if upper_rows is not None else count
        if upper == 0:
            return f"{_NO_MATCH}."
        amount = str(count) if upper == count else f"Between {count} and {upper}"
        suffix = f": {', '.join(names)}" if names else ""
        return f"{amount} recorded in the graph{suffix}{unsure}"
    shown = upper_rows if upper_rows is not None else rows
    if not shown:
        return f"{_NO_MATCH}."
    if plan.operation == "list":
        return ", ".join(_fmt(r[key_columns[0]]) for r in rows) + unsure + more
    lines = [f"- {keys}: {metrics}" for keys, metrics in _group_lines(rows, upper_rows, key_columns, metric_columns)]
    return "\n".join(lines) + unsure + more
