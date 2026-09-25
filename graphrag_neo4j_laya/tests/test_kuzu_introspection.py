"""
tests/test_kuzu_introspection.py

Kùzu-backed tests for the graph introspection methods used by the guided
query planner: frontier moves, categorical value candidates, the read-only
query runner, and the relation description registry.
"""

from __future__ import annotations

import json

import pytest


def _moves(moves: list[dict]) -> dict[tuple[str, str], int]:
    return {(m["type"], m["direction"]): m["edge_count"] for m in moves}


class TestFrontierMoves:
    def test_calculus_has_only_incoming_edges(self, science_graph):
        moves = _moves(science_graph.frontier_moves(["Calculus"]))
        assert moves == {("DEVELOPED", "in"): 1, ("BORN_IN", "in"): 1}

    def test_newton_outgoing_edges(self, science_graph):
        moves = _moves(science_graph.frontier_moves(["Isaac Newton"]))
        assert moves == {("AUTHORED", "out"): 1, ("BORN_IN", "out"): 2, ("LEADS", "out"): 1}

    def test_neighbor_count_is_distinct(self, science_graph):
        moves = science_graph.frontier_moves(["Isaac Newton"])
        born_in = next(m for m in moves if m["type"] == "BORN_IN")
        assert born_in["neighbor_count"] == 2

    def test_all_nodes_frontier_matches_rollup(self, science_graph):
        out = {t: n for (t, d), n in _moves(science_graph.frontier_moves(None)).items() if d == "out"}
        assert out == {
            "BORN_IN": 3, "DISCOVERED": 2, "AUTHORED": 1, "DEVELOPED": 1, "EXPLAINS": 1,
            "EXTENDS": 1, "LEADS": 1, "PREDICTED": 1, "RELATED_TO": 1,
        }

    def test_unknown_anchor_returns_empty(self, science_graph):
        assert science_graph.frontier_moves(["Nobody"]) == []

    def test_empty_frontier_returns_empty(self, science_graph):
        assert science_graph.frontier_moves([]) == []


class TestDistinctValues:
    def test_community_ids_skip_null(self, science_graph):
        # Banana Bread lost its only edge, so its communityId stays NULL.
        assert science_graph.distinct_values("communityId") == [0]

    def test_relation_types(self, science_graph):
        types = science_graph.distinct_values("type")
        assert "BORN_IN" in types and len(types) == 9

    def test_limit_is_applied(self, science_graph):
        assert len(science_graph.distinct_values("name", limit=3)) == 3

    def test_unknown_field_rejected(self, science_graph):
        with pytest.raises(ValueError):
            science_graph.distinct_values("description")


class TestRunReadQuery:
    def test_returns_rows_as_dicts(self, science_graph):
        rows = science_graph.run_read_query(
            "MATCH (a:Entity {name: $name}) RETURN a.name AS name, a.communityId AS community",
            {"name": "Isaac Newton"},
        )
        assert rows == [{"name": "Isaac Newton", "community": 0}]

    @pytest.mark.parametrize("query", [
        "CREATE (n:Entity {name: 'x'})",
        "MATCH (n:Entity) set n.name = 'x'",
        "MATCH (n:Entity) DETACH DELETE n",
        "COPY Entity FROM 'x.csv'",
        "ATTACH 'other.kuzu' AS other",
        "LOAD EXTENSION json",
        "CALL show_tables() RETURN *",
    ])
    def test_rejects_non_read_statements(self, science_graph, query):
        with pytest.raises(ValueError):
            science_graph.run_read_query(query, {})

    def test_property_named_like_keyword_is_not_rejected(self, science_graph):
        # Only whole keywords are rejected; `offset`-like identifiers pass.
        rows = science_graph.run_read_query(
            "MATCH (a:Entity {name: $name}) RETURN a.name AS dataset", {"name": "LIGO"}
        )
        assert rows == [{"dataset": "LIGO"}]


class TestRelationSchema:
    def test_loads_dataset_schema(self, tmp_path, science_data):
        from graphrag.graph.relation_schema import load_relation_schema

        path = tmp_path / "data.json"
        path.write_text(json.dumps(science_data), encoding="utf-8")
        schema = load_relation_schema(str(path))
        assert schema["BORN_IN"] == "was born in a place"

    def test_accepts_plain_mapping(self, tmp_path):
        from graphrag.graph.relation_schema import load_relation_schema

        path = tmp_path / "schema.json"
        path.write_text(json.dumps({"LEADS": "leads an organisation"}), encoding="utf-8")
        assert load_relation_schema(str(path)) == {"LEADS": "leads an organisation"}

    def test_missing_path_returns_empty(self, tmp_path):
        from graphrag.graph.relation_schema import load_relation_schema

        assert load_relation_schema(None) == {}
        assert load_relation_schema(str(tmp_path / "missing.json")) == {}
