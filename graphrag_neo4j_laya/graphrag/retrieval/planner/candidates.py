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


# The same wording that makes a question a threshold question also says how
# the group is compared: "at least 2" is >= 2, "1'den fazla" is > 1.
_COMPARISONS = (
    (">=", re.compile(r"at least|en\s+az\s+\d", re.IGNORECASE)),
    ("<=", re.compile(r"at most|en\s+fazla\s+\d|en\s+çok\s+\d", re.IGNORECASE)),
    (">", re.compile(r"more than|greater than|\d+['’]?[dt][ae]n\s+(fazla|çok)", re.IGNORECASE)),
    ("<", re.compile(r"less than|fewer than|\d+['’]?[dt][ae]n\s+az", re.IGNORECASE)),
)


def comparison_from_wording(text: str) -> str | None:
    """The operator the question's threshold means, if it sets one."""
    for op, pattern in _COMPARISONS:
        if pattern.search(text):
            return op
    return None


def sets_a_threshold(text: str) -> bool:
    """Does the question compare a quantity with a number ("at least 2")?"""
    return bool(_THRESHOLD_RE.search(text))


def asks_for_a_number(text: str) -> bool:
    """Does the question ask how many, rather than which?"""
    return bool(_COUNT_QUESTION_RE.search(text))


# A question that never mentions a relation is about the entities themselves,
# not about anything they are connected to. The vocabulary is deliberately
# generic: matching against the relation schema's own descriptions would read
# "list every place in the graph" as a BORN_IN question, because that type is
# described as "born in a place".
_RELATION_WORD_RE = re.compile(
    r"(?<!\w)("
    r"relation\w*|edge\w*|connect\w*|connection\w*|link\w*|related|"
    r"point(s|ing)?\s+to|step\w*|hop\w*|path\w*|"
    r"ilişki\w*|bağl\w*|kenar\w*|adım\w*"
    r")(?!\w)",
    re.IGNORECASE,
)


# A schema description is a verb phrase — "wrote, published or authored a
# work" — so the words that name the relation are the ones before its object.
# Taking the object nouns too would read "list every place in the graph" as a
# BORN_IN question, since that type is "was born in a place".
_OBJECT_WORDS = frozenset(("a", "an", "another", "any", "some", "something", "someone", "the"))
_FUNCTION_WORDS = frozenset(("was", "were", "is", "are", "or", "and", "of", "in", "on",
                             "upon", "to", "first", "other"))


def relation_words_of(description: str) -> frozenset[str]:
    """The words of *description* that name the relation rather than its object."""
    words = []
    for word in re.findall(r"[a-z]+", description.lower()):
        if word in _OBJECT_WORDS:
            break
        if word not in _FUNCTION_WORDS:
            words.append(word)
    return frozenset(words)


def named_relations(
    text: str,
    relation_schema: dict[str, str],
    relation_words: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    """
    Every relation type *text* names.

    A type is named either by a word of its description, or by one of the
    words the schema lists for it. A listed word matches a question word that
    starts with it, which is how a Turkish suffix is covered ("yazdı" matches
    "yazdığı"); a description word has to match whole, since English inflects
    little and a prefix would make "born" out of "borne" and "place".
    """
    said = {w.lower() for w in re.findall(r"[^\W\d_]+", text, re.UNICODE)}
    listed = relation_words or {}
    named = []
    for rel_type, description in relation_schema.items():
        stems = listed.get(rel_type, ())
        if relation_words_of(description) & said or any(w.startswith(s) for s in stems for w in said):
            named.append(rel_type)
    return named


def names_one_relation(
    text: str,
    relation_schema: dict[str, str],
    relation_words: dict[str, tuple[str, ...]] | None = None,
) -> str | None:
    """
    The single relation type *text* names, if it names exactly one.

    Naming two ("the theory Einstein discovered") says nothing about which
    one the next hop is, so the model keeps the choice.
    """
    named = named_relations(text, relation_schema, relation_words)
    return named[0] if len(named) == 1 else None


def mentions_a_relation(
    text: str,
    relation_schema: dict[str, str] | None = None,
    relation_words: dict[str, tuple[str, ...]] | None = None,
) -> bool:
    """Does the question talk about a relation between entities at all?"""
    if _RELATION_WORD_RE.search(text):
        return True
    return bool(named_relations(text, relation_schema or {}, relation_words))


# "exactly two steps away", "iki adım ötede": the question says how long the
# path is, which is the only wording that gives the hop count outright.
_STEP_COUNT_RE = re.compile(
    r"(?<!\w)(?P<count>\d+|two|three|iki|üç)\s+(steps?|hops?|adım\w*)(?!\w)",
    re.IGNORECASE,
)
_STEP_WORDS = {"two": 2, "iki": 2, "three": 3, "üç": 3}


def hops_asked_for(
    text: str,
    relation_schema: dict[str, str] | None = None,
    relation_words: dict[str, tuple[str, ...]] | None = None,
) -> tuple[int, bool]:
    """
    How many hops the question asks for: (the fewest, whether it says exactly).

    Two readings give a path longer than one step. The question can say so —
    "exactly two steps away" — and then the count is exact. Or it can refer to
    a relation twice, once by naming a type and once in passing ("everything
    that the theory Einstein *discovered* is *connected to*"): the named one
    describes the entity in the middle, so there is a step on either side of
    it. That reading gives a lower bound, not a count.
    """
    stated = _STEP_COUNT_RE.search(text)
    if stated:
        raw = stated.group("count").lower()
        count = int(raw) if raw.isdigit() else _STEP_WORDS[raw]
        if count >= 1:
            return count, True
    references = len(named_relations(text, relation_schema or {}, relation_words))
    if _RELATION_WORD_RE.search(text):
        references += 1
    return (2, False) if references >= 2 else (1, False)


# "who else was born there", "başka kim": the question asks for the entities
# beside the ones it came from, which is the one reading that wants a hop
# back along the relation just taken.
_THE_OTHERS_RE = re.compile(r"(?<!\w)(other|others|another|else|başka|diğer|öteki)(?!\w)",
                            re.IGNORECASE)


def asks_for_the_others(text: str) -> bool:
    """Does the question ask for the entities beside the ones it started from?"""
    return bool(_THE_OTHERS_RE.search(text))


def the_way_back(hop: Hop) -> str:
    """The option key that reverses *hop*, walking back where it came from."""
    return f"{hop.rel_type or 'any'}:{'in' if hop.direction == 'out' else 'out'}"


def only_this_relation(options: dict[str, str], rel_type: str) -> dict[str, str]:
    """The hop options of *rel_type* alone, or all of them if it has none here."""
    kept = {key: text for key, text in options.items() if key.split(":")[0] == rel_type}
    return kept or options


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


# What the answer is broken down by is a noun in the question: "of each
# **type**", "her **varlık**". Only three things can be grouped by, so the
# vocabulary is that small: the relation type, the community, or — for
# anything else that can be named — the entity itself.
_FIELD_WORDS = (
    ("type", re.compile(r"(?<!\w)(types?|kinds?|tür\w*|tip\w*|çeşit\w*)(?!\w)", re.IGNORECASE)),
    ("communityId", re.compile(r"(?<!\w)(communit(y|ies)|clusters?|topluluk\w*|küme\w*)(?!\w)",
                               re.IGNORECASE)),
)


def grouped_by(text: str) -> str | None:
    """
    The field the question says the answer is broken down by, if it says.

    A distributive marker points straight at it — the noun after "each" or
    "her" is the group ("Her **varlık** hangi ilişki tiplerini kullanıyor?"
    groups by entity although it also says "tip"). Without one the whole
    question is read, which is how a threshold question names its group
    ("1'den fazla geçen ilişki **türleri**").
    """
    after = _DISTRIBUTIVE_RE.search(text)
    if after:
        # The noun the marker governs, not the rest of the sentence: "Her
        # varlık hangi ilişki tiplerini" groups by entity, and reading on
        # would find "tip" and group by relation type instead.
        scope = " ".join(re.findall(r"[^\W\d_]+", text[after.end():], re.UNICODE)[:2])
    else:
        scope = text
    for field, pattern in _FIELD_WORDS:
        if pattern.search(scope):
            return field
    return "name" if scope.strip() else None


# PageRank is the one number a node carries, and no question asks for its
# average without saying so.
_IMPORTANCE_RE = re.compile(
    r"(?<!\w)(pagerank|importance|important|influen\w*|central\w*|"
    r"önem\w*|etkili|merkez\w*)(?!\w)", re.IGNORECASE)


def asks_about_importance(text: str) -> bool:
    """Does the question ask about how important an entity is?"""
    return bool(_IMPORTANCE_RE.search(text))


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


_DISTINCT_RE = re.compile(r"(?<!\w)(distinct|different|unique|farklı|ayrı|değişik)(?!\w)",
                          re.IGNORECASE)
_WHICH_RE = re.compile(r"(?<!\w)(which|what|hangi\w*)(?!\w)", re.IGNORECASE)


def metric_from_wording(text: str, plan: QueryPlan, key_field: str | None) -> str | None:
    """
    The metric the question asks for, when it says what it is counting.

    A question that asks how many, compares a group with a number, or ranks
    the groups wants a count — of distinct entities if it says "distinct", of
    matches otherwise. Asked instead, the model answered `count_distinct` to
    every grouped question of the labelled set, right or wrong. A question
    that asks *which* relation types, grouped by something else, wants the
    types themselves listed per group. Anything else is left to the model,
    and so is every question that asks about importance, where the PageRank
    aggregates are the point.
    """
    if asks_about_importance(text):
        return None
    if asks_for_a_number(text) or sets_a_threshold(text) or plan.operation == "rank":
        return f"count_distinct:{plan.target}.name" if _DISTINCT_RE.search(text) else "count"
    edges = edge_vars(plan)
    if (plan.operation == "group" and key_field != "type" and edges
            and _WHICH_RE.search(text) and _FIELD_WORDS[0][1].search(text)):
        return f"collect:{edges[0]}.type"
    return None


def small_limit(numbers: list[int | float], default: int = 5, maximum: int = 50) -> int:
    """First integer in the question that looks like a top-k, else *default*."""
    for n in numbers:
        if isinstance(n, int) and 1 <= n <= maximum:
            return n
    return default
