"""
graphrag/retrieval/planner/candidates.py

Code-side candidate generation. The decision model can pick among values but
cannot produce them, so every value that ends up in a plan (thresholds,
limits, option sets) is generated here.
"""

from __future__ import annotations

import re

from .describe import describe_hops
from .plan import NODE_FIELDS, Hop, QueryPlan

# "0,1" (Turkish decimal comma) and "0.1" both mean 0.1; "1,000"-style
# thousands separators are not expected in questions.
_NUMBER_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)(?![\d])")


def extract_numbers(text: str) -> list[int | float]:
    return [int(raw) if raw.isdigit() else float(raw.replace(",", ".")) for raw in _NUMBER_RE.findall(text)]


def mentions_entity(text: str, name: str) -> bool:
    """
    Does *text* name the entity *name*?

    The seed comes from a vector search, so it may be the best match for a
    question that never says it. Matching is on word boundaries, which in
    Turkish means the apostrophe of a suffix counts as one ("Calculus'a",
    "Newton'un"), and a multi-word name also matches on its last word alone,
    the usual short form for a person.
    """
    for form in _name_forms(name):
        if re.search(rf"(?<!\w){re.escape(form)}(?!\w)", text, re.IGNORECASE):
            return True
    return False


def _name_forms(name: str) -> list[str]:
    forms = [name]
    words = name.split()
    if len(words) > 1 and len(words[-1]) >= 4:
        forms.append(words[-1])
    return forms


def hop_options(plan: QueryPlan, moves: list[dict], relation_schema: dict[str, str]) -> dict[str, str]:
    """Choice options for the next hop: every existing move, `any:<dir>`, and stop."""
    options: dict[str, str] = {}
    by_direction: dict[str, int] = {}
    for move in moves:
        hop = Hop(move["type"], move["direction"])
        options[f"{move['type']}:{move['direction']}"] = describe_hops(
            QueryPlan(operation=plan.operation, hops=[hop]), relation_schema
        )[0]
        by_direction[move["direction"]] = by_direction.get(move["direction"], 0) + 1
    for direction, count in by_direction.items():
        if count > 1:
            options[f"any:{direction}"] = describe_hops(
                QueryPlan(operation=plan.operation, hops=[Hop(None, direction)]), relation_schema
            )[0]
    options["stop"] = "stop here: the entities reached so far are the ones the question asks about"
    return options


def node_vars(plan: QueryPlan) -> list[str]:
    return [f"v{i}" for i in range(len(plan.hops) + 1)]


def edge_vars(plan: QueryPlan) -> list[str]:
    return [f"e{i}" for i in range(len(plan.hops))]


def group_key_candidates(plan: QueryPlan) -> list[tuple[str, str]]:
    """(var, field) pairs a result can be grouped by."""
    keys = [(v, f) for v in node_vars(plan) for f in ("name", "communityId")]
    keys += [(e, "type") for e in edge_vars(plan)]
    return keys


def numeric_field_candidates(plan: QueryPlan) -> list[tuple[str, str]]:
    return [(v, f) for v in node_vars(plan) for f in NODE_FIELDS if f != "name"]


def small_limit(numbers: list[int | float], default: int = 5, maximum: int = 50) -> int:
    """First integer in the question that looks like a top-k, else *default*."""
    for n in numbers:
        if isinstance(n, int) and 1 <= n <= maximum:
            return n
    return default
