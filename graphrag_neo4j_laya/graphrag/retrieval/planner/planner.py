"""
graphrag/retrieval/planner/planner.py

Guided query planner — builds a QueryPlan step by step.

At every step code enumerates the legal moves (from the live graph and the
plan whitelists) and the decision model only picks one of them, Pangu-style
("don't generate, discriminate"). Relation types and directions come from
what actually exists on the current frontier, so the model cannot choose a
relation or direction that is not in the graph.

Each step is recorded in a trace with the selected option, its probability
and the runner-up. The plan confidence is the weakest step's probability.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from config.settings import settings
from graphrag.models.decision_factory import get_decision_model

from . import candidates, semantic_filter
from .describe import describe_plan
from .plan import (
    LIST_METRIC_OPS, FieldRef, Filter, Having, Hop, Metric, Order, QueryPlan, validate_plan,
)
from .render_kuzu import fetch

logger = logging.getLogger(__name__)

_OPERATION_OPTIONS = {
    "count": "The question asks how many things there are (a number).",
    "list":  "The question asks which or who: every matching entity should be listed.",
    "rank":  "The question asks which entity has the most or the least of something.",
    "group": "The question asks for a breakdown: a number per type, per entity or per group.",
}
_HOP_INSTRUCTION = "Which next step brings this graph query closer to answering the question?"
_HAVING_INSTRUCTION = "Does the question keep only groups whose count or total is above or below a number?"
_COMPARISON_OPTIONS = {
    ">": "greater than", ">=": "at least", "<": "less than", "<=": "at most", "=": "exactly equal to",
}
_KEEP_THRESHOLD = 0.5
_FRONTIER_LIMIT = 1000


@dataclass
class StepTrace:
    step: str
    options: list[str]
    selected: str
    probability: float
    runner_up: str | None = None
    runner_up_probability: float = 0.0
    forced: bool = False
    overridden: bool = False

    @property
    def margin(self) -> float:
        return self.probability - self.runner_up_probability


@dataclass
class PlannerResult:
    plan: QueryPlan
    trace: list[StepTrace] = field(default_factory=list)
    confidence: float = 1.0
    description: str = ""
    # Entity kind the target must have (semantic_filter.PREDICATE_KINDS), or None.
    semantic: str | None = None


class _Planning:
    """State of one plan() call."""

    def __init__(self, planner: "GuidedQueryPlanner", question: str, overrides: dict[str, str]) -> None:
        self.p = planner
        self.question = question
        self.overrides = overrides
        self.model = get_decision_model()
        self.trace: list[StepTrace] = []
        self.plan = QueryPlan(operation="list", limit=planner.row_limit)
        self.semantic: str | None = None

    def context(self) -> str:
        so_far = describe_plan(self.plan, self.p.relation_schema) if self.trace else "(empty)"
        return f"User question: {self.question}\nQuery plan so far: {so_far}"

    def forced(self, step: str, selected: str) -> str:
        self.trace.append(StepTrace(step, [selected], selected, 1.0, forced=True))
        return selected

    def choose(self, step: str, instruction: str, options: dict[str, str]) -> str:
        result = self.model.choice_detailed(self.context(), instruction, options)
        probs = result.raw_probs or {result.selected: result.score}
        ranked = sorted(options, key=lambda k: probs.get(k, 0.0), reverse=True)
        selected = result.selected if result.selected in options else ranked[0]
        overridden = step in self.overrides and self.overrides[step] in options
        if overridden:
            selected = self.overrides[step]
        runner_up = next((k for k in ranked if k != selected), None)
        self.trace.append(StepTrace(
            step, list(options), selected, probs.get(selected, 0.0),
            runner_up, probs.get(runner_up, 0.0) if runner_up else 0.0, overridden=overridden,
        ))
        return selected

    def yes(self, step: str, instruction: str) -> bool:
        if step in self.overrides:
            answer = self.overrides[step] == "yes"
            self.trace.append(StepTrace(step, ["yes", "no"], "yes" if answer else "no", 1.0, overridden=True))
            return answer
        p = self.model.noul_detailed(self.context(), instruction).score
        answer = p >= _KEEP_THRESHOLD
        self.trace.append(StepTrace(
            step, ["yes", "no"], "yes" if answer else "no", p if answer else 1.0 - p,
            "no" if answer else "yes", 1.0 - p if answer else p,
        ))
        return answer

    # ── steps ────────────────────────────────────────────────────────────────

    def operation(self) -> None:
        # Every operation error measured on the labelled set was a `group`
        # question read as something else, or `rank` chosen for a question with
        # nothing to rank, so the wording decides what it can and the model
        # picks among the rest.
        #
        # "How many relations of each type ...?" asks for one answer per type;
        # asked as a Choice, every such question came back as `count` or
        # `list`. A superlative still goes to the Choice, where `rank` lives.
        #
        # "... at least 2 outgoing relations" compares a per-entity quantity
        # with a number, which is a group plus a HAVING; counting the groups
        # that pass is not expressible in this grammar anyway, so a threshold
        # goes to `group` whichever way the question is phrased.
        ranking = candidates.asks_for_a_ranking(self.question)
        if not ranking and (candidates.asks_per_group(self.question)
                            or candidates.sets_a_threshold(self.question)):
            self.plan.operation = self.forced("operation", "group")
            return
        options = dict(_OPERATION_OPTIONS)
        if not ranking:
            options.pop("rank")          # nothing to order by
        if candidates.asks_for_a_number(self.question):
            options.pop("list")          # "how many" asks for a number, not for the entities
        self.plan.operation = self.choose("operation", "What kind of answer does the question ask for?",
                                          options)

    def start(self, seeds: list[str]) -> None:
        # Measured as the weakest step of the planner: asked as a Choice, the
        # model picked "the whole graph" for all 14 questions that named their
        # seed (P(all) = 0.995 for "How many places was Isaac Newton born in?").
        # Whether a question names an entity is a question about the text, not
        # a judgement, so code answers it.
        anchor = seeds[0] if seeds else None
        if anchor and candidates.mentions_entity(self.question, anchor):
            self.plan.start = anchor
            self.forced("start", "anchor")
        else:
            self.forced("start", "all")

    def frontier(self) -> list[str] | None:
        if not self.plan.hops:
            return [self.plan.start] if self.plan.start else None
        probe = QueryPlan(operation="list", start=self.plan.start, hops=list(self.plan.hops),
                          limit=_FRONTIER_LIMIT)
        rows, _ = fetch(self.p.db, probe)
        return [r[f"{probe.target}_name"] for r in rows]

    def hops(self) -> None:
        least, exact = candidates.hops_asked_for(self.question, self.p.relation_schema,
                                                 self.p.relation_words)
        named = candidates.names_one_relation(self.question, self.p.relation_schema,
                                              self.p.relation_words)
        for i in range(self.p.max_hops):
            step = f"hop{i}"
            moves = self.p.db.frontier_moves(self.frontier())
            if not moves:
                self.forced(step, "stop")
                return
            options = candidates.hop_options(self.plan, moves, self.p.relation_schema)
            if i == 0:
                # Whether the answer needs a relation at all is readable from
                # the question, and measured as the model's worst decision: of
                # the ten questions that start from the whole graph it got the
                # first hop right in three.
                #
                # A question that names no relation ("How many theories are
                # there in the graph?") is about the entities themselves, so
                # the plan stays at the start node. Every other plan takes at
                # least one hop: an anchored plan that stops here returns the
                # anchor itself, which answers no aggregate question, and a
                # question that does name a relation is asking about it.
                if not self.plan.start and not candidates.mentions_a_relation(
                        self.question, self.p.relation_schema, self.p.relation_words):
                    self.forced(step, "stop")
                    return
                options.pop("stop")
            elif i < least:
                # The question asks for a longer path than the plan has
                # walked. Measured: offered `stop` here, the model took it in
                # all four two-hop questions of the labelled set.
                options.pop("stop")
            elif exact:
                # It said how many steps, and the plan has taken them.
                self.forced(step, "stop")
                return
            if named:
                # A question that names one relation type and no other says
                # which step that type is by where the graph can take it. It
                # is usually the first ("... that Isaac Newton **authored**"),
                # but not always: "how many others **developed** something
                # that Isaac Newton is **connected to**" names the far step,
                # and Newton has no DEVELOPED relation to take. So the type is
                # spent on the first hop that offers it, and after that the
                # question has said all it says about types. Naming two types
                # says nothing about which comes first, and the words come out
                # of the schema's own descriptions, so a schema written in
                # another language than the question matches nothing and the
                # model keeps the whole choice.
                narrowed = candidates.only_this_relation(options, named)
                if narrowed:
                    if "stop" in options:
                        narrowed["stop"] = options["stop"]
                    options, named = narrowed, None
            if i and not candidates.asks_for_the_others(self.question):
                # Reversing the hop just taken walks back to the entities the
                # plan came from, so it answers nothing the plan does not
                # already hold — unless that is the question ("who else was
                # born where Einstein was born"), which says so in words.
                # Kept when it is all that is left, so that the plan stops
                # rather than losing its only move.
                back = candidates.the_way_back(self.plan.hops[-1])
                if back in options and len(options) > 1:
                    options.pop(back)
            # One option is not a choice, and asking would let a meaningless
            # probability into the plan's confidence.
            selected = (self.forced(step, next(iter(options))) if len(options) == 1
                        else self.choose(step, _HOP_INSTRUCTION, options))
            if selected == "stop":
                return
            rel_type, direction = selected.split(":")
            self.plan.hops.append(Hop(None if rel_type == "any" else rel_type, direction))

    def filters(self, numbers: list[int | float]) -> None:
        # A walk of two or more steps comes back through the entity it left:
        # every two-hop question of the labelled set asks for the others
        # ("how many *others* developed something Newton is connected to"),
        # and none of them counts the start entity as part of its answer.
        # Asked as a Noul, the model kept it in half of them.
        if self.plan.start and len(self.plan.hops) >= 2:
            self.forced("exclude_start", "yes")
            self.plan.filters.append(
                Filter(FieldRef(self.plan.target, "name"), "!=", self.plan.start))
        # A number belongs to a node's own value only where the question names
        # one of the two a node carries. Otherwise it is the top-k of a
        # ranking ("en çok giden ilişkisi olan **2** varlık") or a threshold
        # on a group ("**1**'den fazla geçen"), and the shape step reads it
        # out of the same wording. Asked as a Noul, the model spent the
        # number here in both, and filtered the community by a top-k.
        # A ranking spends its number on the top-k ("PageRank'ı en yüksek **3**
        # varlık"), even when it names the field it ranks by.
        field = None if self.plan.operation == "rank" else candidates.numeric_field_named(self.question)
        if not numbers or not field:
            self.forced("filter_numeric", "no")
            return
        self.forced("filter_numeric", "yes")
        entities = {v: _which_entity(v, self.plan) for v in candidates.node_vars(self.plan)}
        var = (self.forced("filter_field", next(iter(entities))) if len(entities) == 1
               else self.choose("filter_field",
                                f"Whose {field} does the question compare with a number?", entities))
        said_op = candidates.comparison_from_wording(self.question)
        op = (self.forced("filter_op", said_op) if said_op
              else self.choose("filter_op", "How is the value compared with the number?",
                               _COMPARISON_OPTIONS))
        self.plan.filters.append(Filter(FieldRef(var, field), op,
                                        self._number("filter_value", numbers)))

    def shape(self, numbers: list[int | float]) -> None:
        if self.plan.operation not in ("group", "rank"):
            return
        # Asked one by one, every key came back yes: on the labelled set the
        # per-key probabilities sat between 0.75 and 0.96 whether the key was
        # the right one or not, and five-key groups came out of questions
        # whose answer has one. So the field is read from the question and
        # the model picks which entity's field it is, as a single Choice.
        keys = candidates.group_key_candidates(self.plan)
        field = candidates.grouped_by(self.question)
        keys = [(v, f) for v, f in keys if f == field] or keys
        if field == "name" and len(self.plan.hops) == 1:
            # On a one-hop plan, grouping by the far end is the same question
            # asked with the hop reversed, and the direction has already been
            # read from the wording. Measured: offered both ends, the model
            # took the far one every time, and no labelled answer wants it.
            keys = [(v, f) for v, f in keys if v == "v0"] or keys
        options = {f"{v}.{f}": _key_words(v, f, self.plan) for v, f in keys}
        chosen = (self.forced("key", next(iter(options))) if len(options) == 1
                  else self.choose("key", "What is the answer broken down by?", options))
        self.plan.keys.append(FieldRef(*chosen.split(".")))

        said = candidates.metric_from_wording(self.question, self.plan, field)
        metric = _parse_metric(
            self.forced("metric", said) if said
            else self.choose("metric", "What should be computed for each group?",
                             _metric_options(self.plan, self.question)))
        self.plan.metrics.append(metric)
        if self.plan.operation == "rank":
            self.plan.order = [Order(metric, descending=True)]
            self.plan.limit = candidates.small_limit(numbers)
        elif metric.op in LIST_METRIC_OPS:
            pass  # a list of values cannot be compared with a number
        elif numbers and (said_op := candidates.comparison_from_wording(self.question)):
            # "at least 2" is the HAVING, operator and all: asking whether to
            # add one, and which way it compares, asks the question twice.
            self.forced("having", "yes")
            self.forced("having_op", said_op)
            self.plan.having.append(Having(metric, said_op, self._number("having_value", numbers)))
        elif numbers and self.yes("having", _HAVING_INSTRUCTION):
            op = self.choose("having_op", "How is the group value compared with the number?", _COMPARISON_OPTIONS)
            self.plan.having.append(Having(metric, op, self._number("having_value", numbers)))

    def semantic_step(self) -> None:
        # The filter keeps only entities of one kind, so it is worth asking
        # about only for a kind the question actually names — and not for one
        # a hop already guarantees: every BORN_IN target is a place, so "how
        # many places was he born in" needs no filter on top, and a kind that
        # names an entity in the middle of the path ("the work he wrote, and
        # the concepts it relates to") is not a restriction on the answer
        # either. Measured: offered all seven kinds, the step put one on four
        # questions that ask for none and left it off two that need one.
        words = semantic_filter.PREDICATE_WORDS
        implied = {candidates.kind_implied_by(hop.rel_type, self.p.relation_schema, words)
                   for hop in self.plan.hops}
        named = [kind for kind in candidates.kinds_named(self.question, words)
                 if kind not in implied]
        if not named:
            self.forced("semantic", semantic_filter.NO_PREDICATE)
            return
        if not self.plan.hops and len(named) == 1:
            # A plan over the whole graph that walks nowhere returns every
            # entity, so the kind is the only thing the question restricts.
            self.semantic = self.forced("semantic", named[0])
            return
        options = {k: v for k, v in semantic_filter.PREDICATE_KINDS.items()
                   if k == semantic_filter.NO_PREDICATE or k in named}
        kind = self.choose("semantic", semantic_filter.PREDICATE_INSTRUCTION, options)
        if kind != semantic_filter.NO_PREDICATE:
            self.semantic = kind

    def _number(self, step: str, numbers: list[int | float]) -> int | float:
        if len(numbers) == 1:
            self.forced(step, str(numbers[0]))
            return numbers[0]
        options = {str(n): f"the number {n} mentioned in the question" for n in numbers}
        return numbers[list(options).index(self.choose(step, "Which number from the question is meant?", options))]


def _key_words(var: str, fld: str, plan: QueryPlan) -> str:
    if var.startswith("e"):
        return f"relation type of step {int(var[1:]) + 1}"
    return f"{'community' if fld == 'communityId' else 'name'} of {_which_entity(var, plan)}"


def _which_entity(var: str, plan: QueryPlan) -> str:
    """
    Which entity of the path *var* is, in words.

    "the start entity" only means something when the plan starts somewhere:
    for a plan over the whole graph, v0 is every entity the first relation
    leaves from (or arrives at, if that relation is walked backwards).
    """
    step = int(var[1:])
    if step:
        return f"the step-{step} entity, at the far end of the path"
    if plan.start:
        return f"the entity the query starts at ({plan.start})"
    if plan.hops:
        arrow = "leaves from" if plan.hops[0].direction == "out" else "arrives at"
        return f"the entity each step-1 relation {arrow}"
    return "the entity"


def _metric_options(plan: QueryPlan, question: str = "") -> dict[str, str]:
    target = plan.target
    options = {
        "count": "the number of matching paths or edges",
        f"count_distinct:{target}.name": "the number of distinct entities reached",
    }
    # PageRank is the only number a node carries, and a question that wants
    # its average says so. Measured: offered unasked, it was chosen for four
    # questions that ask how many relations a group has.
    words = {"sum": "total", "avg": "average", "min": "lowest", "max": "highest"}
    if candidates.asks_about_importance(question):
        for v in candidates.node_vars(plan):
            for op, word in words.items():
                options[f"{op}:{v}.pagerank"] = (
                    f"the {word} PageRank (importance) of {_which_entity(v, plan)}")
    # A list cannot be ordered, compared with a number, or read as a count,
    # so `collect` is offered only where the answer may be a list: not to a
    # `rank`, not to a question asking how many, and not to one that compares
    # the group with a number.
    lists_allowed = (plan.operation != "rank"
                     and not candidates.asks_for_a_number(question)
                     and not candidates.sets_a_threshold(question))
    if lists_allowed:
        for e in candidates.edge_vars(plan):
            step = int(e[1:]) + 1
            options[f"collect:{e}.type"] = (
                f"the names of the relation types used in step {step}, listed per group"
            )
    return options


def _parse_metric(key: str) -> Metric:
    if key == "count":
        return Metric("count")
    op, ref = key.split(":")
    var, fld = ref.split(".")
    return Metric(op, FieldRef(var, fld))


class GuidedQueryPlanner:
    """
    Build a QueryPlan for *question* on *db*.

    `plan()` never raises: backend or model failures return None so the
    caller can fall back to the existing retrieval routes.
    """

    def __init__(
        self,
        db,
        relation_schema: dict[str, str] | None = None,
        relation_words: dict[str, tuple[str, ...]] | None = None,
        max_hops: int | None = None,
        row_limit: int | None = None,
    ) -> None:
        self.db = db
        self.relation_schema = relation_schema or {}
        self.relation_words = relation_words or {}
        self.max_hops = max_hops if max_hops is not None else settings.aggregate_max_hops
        self.row_limit = row_limit if row_limit is not None else settings.aggregate_row_limit

    def plan(self, question: str, seeds: list[str], overrides: dict[str, str] | None = None) -> PlannerResult | None:
        state = _Planning(self, question, overrides or {})
        numbers = candidates.extract_numbers(question)
        try:
            state.operation()
            state.start(seeds)
            state.hops()
            state.filters(numbers)
            state.shape(numbers)
            state.semantic_step()
            validate_plan(state.plan)
        except Exception as exc:  # noqa: BLE001 — planner must never break the pipeline
            logger.warning("Guided planner failed for %r: %s", question, exc)
            return None
        # Overridden steps are a deliberate repair; the plan check vouches for them.
        description = describe_plan(state.plan, self.relation_schema)
        if state.semantic:
            description += f" ({semantic_filter.describe_predicate(state.semantic)})"
        asked = [t.probability for t in state.trace if not t.forced and not t.overridden]
        return PlannerResult(
            plan=state.plan,
            trace=state.trace,
            confidence=min(asked) if asked else 1.0,
            description=description,
            semantic=state.semantic,
        )
