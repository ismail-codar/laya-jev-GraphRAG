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


# "at least 2" is a threshold, not a superlative: it belongs to HAVING, not to
# an ordering. Stripped before the superlative match below. Turkish "en az" is
# a superlative on its own ("the fewest") and a threshold in front of a number
# ("en az 2"), which is why that branch requires the digits.
_THRESHOLD_RE = re.compile(
    r"(?<!\w)("
    r"at (least|most)|more than|less than|fewer than|greater than|"
    r"en\s+(az|fazla|çok)\s+\d+|"
    r"\d+['’]?[dt][ae]n\s+(fazla|az|çok)"
    r")(?!\w)",
    re.IGNORECASE,
)
_SUPERLATIVE_RE = re.compile(
    r"(?<!\w)(most|least|highest|lowest|largest|smallest|top|maximum|minimum|fewest|"
    r"en\s+(çok|fazla|az|büyük|küçük|yüksek|düşük))(?!\w)",
    re.IGNORECASE,
)
# "for each X" / "her X" asks for one answer per X, which is what `group`
# returns. English "every" is deliberately not a marker — "list every place"
# is a plain list — and Turkish "her şey" / "her biri" mean "everything" and
# "each one", neither of which names a class to break the answer down by.
_DISTRIBUTIVE_RE = re.compile(r"(?<!\w)(each|per|her(?!\s+(şey|biri|hangi)))(?!\w)", re.IGNORECASE)
_COUNT_QUESTION_RE = re.compile(r"(?<!\w)(how many|how much|kaç|ne kadar)(?!\w)", re.IGNORECASE)


def asks_for_a_ranking(text: str) -> bool:
    """Does the question ask which entity has the most or the least of something?"""
    return bool(_SUPERLATIVE_RE.search(_THRESHOLD_RE.sub(" ", text)))


def asks_per_group(text: str) -> bool:
    """Does the question ask for an answer per category ("of each type")?"""
    return bool(_DISTRIBUTIVE_RE.search(text))


def sets_a_threshold(text: str) -> bool:
    """Does the question compare a quantity with a number ("at least 2")?"""
    return bool(_THRESHOLD_RE.search(text))


def asks_for_a_number(text: str) -> bool:
    """Does the question ask how many, rather than which?"""
    return bool(_COUNT_QUESTION_RE.search(text))


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
