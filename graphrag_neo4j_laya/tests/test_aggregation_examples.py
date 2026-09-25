"""
tests/test_aggregation_examples.py

Every worked example question in AGGREGATION_METHODS.md, executed against the
quickstart fixture graph.

`test_plan_render.py` covers the plan language itself (validation, rendering,
the acceptance examples of the plan document). This file covers the *questions*
the analysis document promises an answer for — above all the eight natural
questions of the "Doğal dil karşılıkları" table (AGGREGATION_METHODS.md:320) —
and pins each documented demo result to a real query.

Each test names the section it comes from. Where the plan language can express
the question, the query is built as a QueryPlan and rendered by the Kùzu
renderer, so the test proves both the grammar and the number. What the grammar
still cannot express — an `OPTIONAL MATCH` over a second relation type, needed
by the work counts of questions 2 and 3 — is in `TestBeyondPlanGrammar`, which
runs the document's hand-written Cypher verbatim.

Numbers reflect the quickstart's two known alignment errors: Newton -> Calculus
is stored as BORN_IN and Einstein -> General Relativity as DISCOVERED.
"""

from __future__ import annotations

import pytest

from graphrag.retrieval.planner.plan import (
    FieldRef, Filter, Having, Hop, Metric, QueryPlan,
)
from graphrag.retrieval.planner.render_kuzu import fetch, render

V0_NAME = FieldRef("v0", "name")
V1_NAME = FieldRef("v1", "name")
V2_NAME = FieldRef("v2", "name")
V0_COMMUNITY = FieldRef("v0", "communityId")
V1_PAGERANK = FieldRef("v1", "pagerank")
E0_TYPE = FieldRef("e0", "type")
COUNT = Metric("count")

# The three entities Method 5 bands as theories in AGGREGATION_METHODS.md:640.
SURE_THEORIES = ["General Relativity", "Universal Gravitation"]
UNCERTAIN_THEORIES = ["Spacetime Curvature"]


def _rows(db, plan: QueryPlan) -> list[dict]:
    rows, _ = fetch(db, plan)
    return rows


def _rounded(rows: list[dict], digits: int = 4) -> list[dict]:
    return [{k: round(v, digits) if isinstance(v, float) else v for k, v in row.items()} for row in rows]


def _group(keys, metrics, **kw) -> QueryPlan:
    """A one-hop `group` over every edge, the shape `group_edges` would have."""
    kw.setdefault("hops", [Hop(None, "out")])
    return QueryPlan(operation="group", keys=list(keys), metrics=list(metrics), **kw)


class TestNaturalLanguageQuestions:
    """AGGREGATION_METHODS.md:320 — "Doğal dil karşılıkları", questions 1-8."""

    def test_q1_scientists_born_per_city(self, science_graph):
        # "Hangi şehirde kaç bilim insanı doğmuş?"
        plan = _group([V1_NAME], [Metric("count_distinct", V0_NAME)], hops=[Hop("BORN_IN", "out")])
        assert _rows(science_graph, plan) == [
            {"v1_name": "Calculus", "count_distinct_v0_name": 1},       # misaligned BORN_IN edge
            {"v1_name": "Ulm", "count_distinct_v0_name": 1},
            {"v1_name": "Woolsthorpe", "count_distinct_v0_name": 1},
        ]

    def test_q3_nobody_shares_einsteins_birthplace(self, science_graph):
        # "Einstein'la aynı şehirde doğan başka bilim insanı var mı?" — the path
        # part of question 3; the work counts need an OPTIONAL MATCH (see below).
        plan = QueryPlan(
            operation="list",
            start="Albert Einstein",
            hops=[Hop("BORN_IN", "out"), Hop("BORN_IN", "in")],
            filters=[Filter(V2_NAME, "!=", "Albert Einstein")],
        )
        rows, truncated = fetch(science_graph, plan)
        # An empty result is a valid answer, not a failure (plan KTD9).
        assert rows == [] and truncated is False

    def test_q4_contributions_per_person(self, science_graph):
        # "Kim kaç katkı yapmış? Keşif, geliştirme ve yazılan eserleri ayrı ayrı
        # göster." — relation type whitelist, count and collect per subject.
        plan = _group(
            [V0_NAME], [COUNT, Metric("collect", E0_TYPE)],
            filters=[Filter(E0_TYPE, "in", ["DISCOVERED", "DEVELOPED", "AUTHORED"])],
        )
        assert _rows(science_graph, plan) == [
            {"v0_name": "Albert Einstein", "count_all": 1, "collect_e0_type": ["DISCOVERED"]},
            {"v0_name": "Gottfried Leibniz", "count_all": 1, "collect_e0_type": ["DEVELOPED"]},
            {"v0_name": "Isaac Newton", "count_all": 1, "collect_e0_type": ["AUTHORED"]},
            {"v0_name": "LIGO", "count_all": 1, "collect_e0_type": ["DISCOVERED"]},
        ]

    def test_q5_what_connects_to_universal_gravitation(self, science_graph):
        # "Evrensel Kütleçekimi'ne hangi kavramlar hangi ilişkiyle bağlanıyor?"
        plan = _group([E0_TYPE, V1_NAME], [COUNT], start="Universal Gravitation",
                      hops=[Hop(None, "in")])
        assert _rows(science_graph, plan) == [
            {"e0_type": "EXTENDS", "v1_name": "General Relativity", "count_all": 1},
            {"e0_type": "RELATED_TO", "v1_name": "Principia Mathematica", "count_all": 1},
        ]

    def test_q6_top_three_by_average_target_pagerank(self, science_graph):
        # "En merkezi konularla ilgilenen üç varlık hangisi?"
        plan = QueryPlan(operation="rank", hops=[Hop(None, "out")], keys=[V0_NAME],
                         metrics=[Metric("avg", V1_PAGERANK)], limit=3)
        rows, truncated = fetch(science_graph, plan)
        assert _rounded(rows) == [
            {"v0_name": "Principia Mathematica", "avg_v1_pagerank": 0.1306},
            {"v0_name": "LIGO", "avg_v1_pagerank": 0.1209},
            {"v0_name": "Gottfried Leibniz", "avg_v1_pagerank": 0.1106},
        ]
        # A top-k cut is a truncation: more subjects exist below the third row.
        assert truncated is True

    def test_q7_two_hop_work_to_concept(self, science_graph):
        # "Bilim insanlarının çalışmaları başka hangi kavramlara yol açmış?"
        plan = _group([V0_NAME, V1_NAME], [COUNT, Metric("collect", FieldRef("e1", "type"))],
                      hops=[Hop(None, "out"), Hop(None, "out")])
        assert _rows(science_graph, plan) == [
            {"v0_name": "Albert Einstein", "v1_name": "General Relativity", "count_all": 3,
             "collect_e1_type": ["EXTENDS", "EXPLAINS", "PREDICTED"]},
            {"v0_name": "Isaac Newton", "v1_name": "Principia Mathematica", "count_all": 1,
             "collect_e1_type": ["RELATED_TO"]},
        ]

    def test_q8_more_than_one_birthplace(self, science_graph):
        # "Birden fazla yerde doğmuş görünen biri var mı?" — the data quality check.
        distinct_places = Metric("count_distinct", V1_NAME)
        plan = _group([V0_NAME], [distinct_places], hops=[Hop("BORN_IN", "out")],
                      having=[Having(distinct_places, ">", 1)])
        assert _rows(science_graph, plan) == [
            {"v0_name": "Isaac Newton", "count_distinct_v1_name": 2},
        ]


class TestBeyondPlanGrammar:
    """
    AGGREGATION_METHODS.md:347 — the work counts of questions 2 and 3 need an
    `OPTIONAL MATCH` over a second relation type, which the plan grammar cannot
    express: a plan is one chain of mandatory hops. These run the document's own
    Cypher, pin the documented results and mark the grammar's edge.
    """

    # Verbatim from AGGREGATION_METHODS.md:347 ("Kùzu 0.11.3'te doğrulandı").
    WORKS_PER_BIRTHPLACE = """
MATCH (p:Entity)-[b:RELATES_TO]->(c:Entity) WHERE b.type = 'BORN_IN'
OPTIONAL MATCH (p)-[a:RELATES_TO]->(w:Entity) WHERE a.type = 'AUTHORED'
RETURN c.name AS City, p.name AS Scientist, COUNT(w) AS Works
ORDER BY City, Scientist
"""
    SAME_CITY_WORKS = """
MATCH (e:Entity {name: 'Albert Einstein'})-[b1:RELATES_TO]->(c:Entity)<-[b2:RELATES_TO]-(p:Entity)
WHERE b1.type = 'BORN_IN' AND b2.type = 'BORN_IN' AND p.name <> e.name
OPTIONAL MATCH (p)-[a:RELATES_TO]->(w:Entity) WHERE a.type = 'AUTHORED'
RETURN c.name AS City, p.name AS Scientist, COUNT(w) AS Works
"""

    def test_q2_works_counted_per_birthplace(self, science_graph):
        # "Bilim insanlarının yazdığı eserleri doğum yerlerine göre say."
        assert science_graph.run_read_query(self.WORKS_PER_BIRTHPLACE, {}) == [
            {"City": "Calculus", "Scientist": "Isaac Newton", "Works": 1},  # misaligned BORN_IN edge
            {"City": "Ulm", "Scientist": "Albert Einstein", "Works": 0},
            {"City": "Woolsthorpe", "Scientist": "Isaac Newton", "Works": 1},
        ]

    def test_q3_same_city_works_is_empty(self, science_graph):
        assert science_graph.run_read_query(self.SAME_CITY_WORKS, {}) == []

    def test_a_plan_cannot_make_the_second_hop_optional(self, science_graph):
        # The nearest plan makes AUTHORED mandatory, so Einstein disappears
        # instead of being counted with zero works.
        plan = _group([V1_NAME, V0_NAME], [COUNT],
                      hops=[Hop("BORN_IN", "out"), Hop("AUTHORED", "in")])
        assert [r["v0_name"] for r in _rows(science_graph, plan)] == []


class TestMethod1Examples:
    """AGGREGATION_METHODS.md:112 — the `AggregateSpec` example table."""

    def test_how_many_people_developed_calculus(self, science_graph):
        # count · Calculus · DEVELOPED · in. The document's snippet at :233
        # shows 2 because it assumes a correct alignment; on the real demo graph
        # Newton's edge sits under BORN_IN, so only Leibniz is recorded.
        plan = QueryPlan(operation="count", start="Calculus", hops=[Hop("DEVELOPED", "in")])
        assert _rows(science_graph, plan) == [{"count_distinct_v1_name": 1}]

    def test_how_many_relations_does_newton_have(self, science_graph):
        plan = QueryPlan(operation="count", start="Isaac Newton", hops=[Hop(None, "out")])
        assert _rows(science_graph, plan) == [{"count_distinct_v1_name": 4}]

    def test_which_works_did_newton_author(self, science_graph):
        plan = QueryPlan(operation="list", start="Isaac Newton", hops=[Hop("AUTHORED", "out")])
        assert _rows(science_graph, plan) == [{"v1_name": "Principia Mathematica"}]

    def test_which_entity_has_the_most_connections(self, science_graph):
        plan = QueryPlan(operation="rank", hops=[Hop(None, "out")], keys=[V0_NAME],
                         metrics=[COUNT], limit=3)
        assert _rows(science_graph, plan) == [
            {"v0_name": "Isaac Newton", "count_all": 4},
            {"v0_name": "General Relativity", "count_all": 3},
            {"v0_name": "Albert Einstein", "count_all": 2},
        ]

    def test_who_was_born_somewhere(self, science_graph):
        # "Kimler bir yerde doğmuş?" — `list` always returns the last hop's
        # entity (the place), so naming the *subjects* is a `group` plan.
        plan = _group([V0_NAME], [COUNT], hops=[Hop("BORN_IN", "out")])
        assert [r["v0_name"] for r in _rows(science_graph, plan)] == [
            "Albert Einstein", "Isaac Newton",
        ]


class TestGroupEdgesCalls:
    """AGGREGATION_METHODS.md:403 — the four `group_edges` example calls."""

    def test_multi_level_multi_metric_table(self, science_graph):
        plan = _group(
            [V0_COMMUNITY, V0_NAME, E0_TYPE],
            [Metric("sum", V1_PAGERANK), COUNT, Metric("avg", V1_PAGERANK),
             Metric("min", V1_PAGERANK), Metric("max", V1_PAGERANK),
             Metric("count_distinct", V1_NAME)],
        )
        rows = _rows(science_graph, plan)
        assert len(rows) == 11
        assert all(r["v0_communityId"] == 0 for r in rows)          # WCC finds one component
        # Ten of eleven groups hold a single edge, so avg = min = max there.
        degenerate = [r for r in rows if r["count_all"] == 1]
        assert len(degenerate) == 10
        assert all(r["avg_v1_pagerank"] == r["min_v1_pagerank"] == r["max_v1_pagerank"]
                   for r in degenerate)

    def test_data_health_check_more_than_one_target(self, science_graph):
        distinct_targets = Metric("count_distinct", V1_NAME)
        plan = _group([V0_NAME, E0_TYPE], [distinct_targets],
                      having=[Having(distinct_targets, ">", 1)])
        assert _rows(science_graph, plan) == [
            {"v0_name": "Isaac Newton", "e0_type": "BORN_IN", "count_distinct_v1_name": 2},
        ]

    def test_edges_to_high_pagerank_targets_by_relation(self, science_graph):
        plan = _group([E0_TYPE], [COUNT], filters=[Filter(V1_PAGERANK, ">", 0.1)])
        assert _rows(science_graph, plan) == [
            {"e0_type": "BORN_IN", "count_all": 1},
            {"e0_type": "DEVELOPED", "count_all": 1},
            {"e0_type": "DISCOVERED", "count_all": 1},
            {"e0_type": "EXTENDS", "count_all": 1},
            {"e0_type": "PREDICTED", "count_all": 1},
            {"e0_type": "RELATED_TO", "count_all": 1},
        ]

    def test_top_three_subjects_in_community_zero(self, science_graph):
        plan = QueryPlan(operation="rank", hops=[Hop(None, "out")], keys=[V0_NAME],
                         metrics=[COUNT], filters=[Filter(V0_COMMUNITY, "=", 0)], limit=3)
        assert _rows(science_graph, plan) == [
            {"v0_name": "Isaac Newton", "count_all": 4},
            {"v0_name": "General Relativity", "count_all": 3},
            {"v0_name": "Albert Einstein", "count_all": 2},
        ]


class TestRollUp:
    """
    AGGREGATION_METHODS.md:316 — "Roll-up, bir seviyeyi `RETURN`'den
    çıkarmakla yapılır": the same query at three grouping levels.
    """

    def test_community_and_subject_level(self, science_graph):
        rows = _rows(science_graph, _group([V0_COMMUNITY, V0_NAME], [COUNT]))
        counts = {r["v0_name"]: r["count_all"] for r in rows}
        assert counts["Isaac Newton"] == 4
        assert counts["General Relativity"] == 3
        assert counts["Albert Einstein"] == 2

    def test_community_and_relation_level(self, science_graph):
        rows = _rows(science_graph, _group([V0_COMMUNITY, E0_TYPE], [COUNT]))
        counts = {r["e0_type"]: r["count_all"] for r in rows}
        assert counts.pop("BORN_IN") == 3
        assert counts.pop("DISCOVERED") == 2
        assert set(counts.values()) == {1}

    def test_community_level_only(self, science_graph):
        assert _rows(science_graph, _group([V0_COMMUNITY], [COUNT])) == [
            {"v0_communityId": 0, "count_all": 12},
        ]


class TestFeasibilityTable:
    """
    AGGREGATION_METHODS.md:835 — the example questions of the per-operation
    feasibility tables, for the rows marked ✓ in the Method 6 column.
    """

    def test_top_k_by_in_degree(self, science_graph):
        # "En çok atıf alan varlık?" — keys on the hop target instead of the source.
        plan = QueryPlan(operation="rank", hops=[Hop(None, "out")], keys=[V1_NAME],
                         metrics=[COUNT], limit=3)
        assert _rows(science_graph, plan) == [
            {"v1_name": "Calculus", "count_all": 2},
            {"v1_name": "Gravitational Waves", "count_all": 2},
            {"v1_name": "Universal Gravitation", "count_all": 2},
        ]

    def test_count_distinct(self, science_graph):
        # "Newton kaç farklı yere bağlı?"
        plan = QueryPlan(operation="count", start="Isaac Newton", hops=[Hop(None, "out")])
        assert _rows(science_graph, plan) == [{"count_distinct_v1_name": 4}]

    def test_average_target_pagerank_per_relation(self, science_graph):
        # "İlişki tipine göre ortalama hedef pagerank?"
        plan = _group([E0_TYPE], [Metric("avg", V1_PAGERANK)])
        rows = {r["e0_type"]: round(r["avg_v1_pagerank"], 4) for r in _rows(science_graph, plan)}
        assert len(rows) == 9
        assert rows["EXTENDS"] == 0.1306
        assert rows["BORN_IN"] == 0.084       # Woolsthorpe, Calculus and Ulm averaged
        assert rows["DISCOVERED"] == 0.0987

    def test_property_equality_filter(self, science_graph):
        # "Topluluk 0'daki özneler" — every subject is in community 0.
        plan = _group([V0_NAME], [COUNT], filters=[Filter(V0_COMMUNITY, "=", 0)])
        assert len(_rows(science_graph, plan)) == 6

    def test_multi_value_in_filter(self, science_graph):
        # "Newton veya Leibniz'in ilişkileri"
        plan = _group([V0_NAME], [COUNT],
                      filters=[Filter(V0_NAME, "in", ["Isaac Newton", "Gottfried Leibniz"])])
        assert _rows(science_graph, plan) == [
            {"v0_name": "Gottfried Leibniz", "count_all": 1},
            {"v0_name": "Isaac Newton", "count_all": 4},
        ]

    def test_multi_step_path_filter(self, science_graph):
        # "Einstein'ın doğduğu şehirde doğan herkes" — the row the document
        # marks ✗ for methods 1-5 and ✓ only for the guided planner.
        plan = QueryPlan(
            operation="list", start="Albert Einstein",
            hops=[Hop("BORN_IN", "out"), Hop("BORN_IN", "in")],
            filters=[Filter(V2_NAME, "!=", "Albert Einstein")],
        )
        assert "<-[e1:RELATES_TO]-" in render(plan).cypher
        assert _rows(science_graph, plan) == []


class TestMethod6Examples:
    """AGGREGATION_METHODS.md:735 — the guided planner's own worked examples."""

    def test_how_many_places_was_newton_born_in(self, science_graph):
        plan = QueryPlan(operation="count", start="Isaac Newton", hops=[Hop("BORN_IN", "out")])
        rendered = render(plan)
        assert rendered.cypher == (
            "MATCH (v0:Entity)-[e0:RELATES_TO]->(v1:Entity) "
            "WHERE v0.name = $p1 AND e0.type = $p0 "
            "RETURN count(DISTINCT v1.name) AS count_distinct_v1_name LIMIT $limit"
        )
        assert rendered.params == {"p0": "BORN_IN", "p1": "Isaac Newton", "limit": 201}
        # 2: Woolsthorpe and, through the aligner error, Calculus.
        assert _rows(science_graph, plan) == [{"count_distinct_v1_name": 2}]

    def test_what_einsteins_discovery_leads_to(self, science_graph):
        # list · start = Albert Einstein · hops = [DISCOVERED:out, any:out]
        plan = QueryPlan(
            operation="list", start="Albert Einstein",
            hops=[Hop("DISCOVERED", "out"), Hop(None, "out")],
            filters=[Filter(V2_NAME, "!=", "Albert Einstein")],
        )
        assert [r["v2_name"] for r in _rows(science_graph, plan)] == [
            "Gravitational Waves", "Spacetime Curvature", "Universal Gravitation",
        ]


class TestSemanticFilterWithAggregation:
    """
    AGGREGATION_METHODS.md:661 — "Anlamsal filtre + gruplu agregasyon": the
    sure set of Method 5 enters the plan as an IN filter, and the uncertain set
    turns every metric into a [sure, sure + uncertain] range.
    """

    def _theory_edges(self, names, direction):
        return _group([E0_TYPE], [COUNT], hops=[Hop(None, direction)],
                      filters=[Filter(V0_NAME, "in", names)])

    def test_sure_theories_grouped_by_relation(self, science_graph):
        plan = self._theory_edges(SURE_THEORIES, "out")
        assert _rows(science_graph, plan) == [
            {"e0_type": "EXPLAINS", "count_all": 1},
            {"e0_type": "EXTENDS", "count_all": 1},
            {"e0_type": "PREDICTED", "count_all": 1},
        ]

    def test_uncertain_candidates_widen_the_metric(self, science_graph):
        lower = _rows(science_graph, self._theory_edges(SURE_THEORIES, "in"))
        upper = _rows(science_graph, self._theory_edges(SURE_THEORIES + UNCERTAIN_THEORIES, "in"))
        assert sum(r["count_all"] for r in lower) == 3
        assert sum(r["count_all"] for r in upper) == 4    # + EXPLAINS into Spacetime Curvature
        assert {r["e0_type"] for r in upper} - {r["e0_type"] for r in lower} == {"EXPLAINS"}


class TestEveryExampleIsParameterised:
    """
    R6: whatever the question, the rendered Cypher carries no user value. The
    entity names and relation types above must appear only in `params`.
    """

    @pytest.mark.parametrize("plan", [
        QueryPlan(operation="count", start="Calculus", hops=[Hop("DEVELOPED", "in")]),
        QueryPlan(operation="list", start="Albert Einstein",
                  hops=[Hop("BORN_IN", "out"), Hop("BORN_IN", "in")],
                  filters=[Filter(V2_NAME, "!=", "Albert Einstein")]),
        _group([V0_NAME], [COUNT], filters=[Filter(E0_TYPE, "in", ["DISCOVERED", "AUTHORED"])]),
        _group([E0_TYPE], [COUNT], filters=[Filter(V0_NAME, "in", SURE_THEORIES)]),
    ])
    def test_no_value_is_interpolated(self, plan):
        rendered = render(plan)
        values = [v for v in rendered.params.values() if isinstance(v, str)]
        values += [v for lst in rendered.params.values() if isinstance(lst, list) for v in lst]
        assert values, "expected at least one parameterised value"
        for value in values:
            assert value not in rendered.cypher
