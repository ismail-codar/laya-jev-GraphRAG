"""
tests/test_community_summary.py

Community summaries for the `global` route: what they read out of the graph,
how they order it, and what they do on a backend that cannot read it.
"""

from __future__ import annotations

import pytest

from graphrag.retrieval.community_summary import summaries


class _Db:
    """A graph that answers the two summary queries and nothing else."""

    def __init__(self, members, relations, texts=None):
        self._members, self._relations, self._texts = members, relations, texts or {}
        self.scans = []

    def run_read_query(self, query, params):
        self.scans.append(params["scan"])
        return self._members if "v.communityId IS NOT NULL" in query else self._relations

    def get_node_text(self, name):
        return self._texts.get(name, "")


def _members(*rows):
    return [{"community": c, "name": n, "pagerank": p} for c, n, p in rows]


class TestWhatTheSummarySays:
    def test_one_node_per_community_biggest_first(self):
        db = _Db(_members((1, "a", 0.3), (1, "b", 0.2), (2, "c", 0.9)), [])
        nodes = summaries(db)
        assert [n["name"] for n in nodes] == ["Community 1", "Community 2"]
        assert "A group of 2 entities" in nodes[0]["text"]
        assert "Most central: a; b." in nodes[0]["text"]

    def test_the_relations_inside_the_community_are_named(self):
        db = _Db(_members((1, "a", 0.3)),
                 [{"community": 1, "type": "AUTHORED", "relations": 2}])
        assert "Relations inside it: AUTHORED ×2." in summaries(db)[0]["text"]

    def test_the_graph_s_own_description_is_quoted(self):
        db = _Db(_members((1, "a", 0.3)), [], {"a": "The first letter."})
        assert "a — The first letter." in summaries(db)[0]["text"]

    def test_members_past_the_described_ones_are_still_named(self):
        db = _Db(_members(*[(1, f"e{i}", 1.0 - i / 100) for i in range(5)]), [],
                 {"e0": "the first"})
        text = summaries(db)[0]["text"]
        assert "e0 — the first" in text
        assert "Also in it: e3, e4." in text

    def test_the_members_are_capped_but_the_size_is_not(self):
        db = _Db(_members(*[(1, f"e{i}", 1.0 - i / 100) for i in range(20)]), [])
        node = summaries(db, members_each=3)[0]
        assert "A group of 20 entities" in node["text"]
        assert "e3" not in node["text"]

    def test_the_largest_communities_win(self):
        rows = _members(*[(1, f"a{i}", 0.5) for i in range(3)],
                        *[(2, f"b{i}", 0.5) for i in range(5)])
        assert [n["name"] for n in summaries(_Db(rows, []), max_communities=1)] == ["Community 2"]


class TestWhenTheGraphCannotAnswer:
    def test_a_backend_without_read_queries_summarises_nothing(self):
        class NoReads(_Db):
            def run_read_query(self, query, params):
                raise NotImplementedError

        assert summaries(NoReads([], [])) == []

    def test_a_failing_query_does_not_break_the_route(self):
        class Broken(_Db):
            def run_read_query(self, query, params):
                raise RuntimeError("connection lost")

        assert summaries(Broken([], [])) == []

    def test_no_communities_means_no_summaries(self):
        assert summaries(_Db([], [])) == []


class TestOnTheRealGraph:
    def test_the_fixture_graph_summarises_itself(self, science_graph):
        nodes = summaries(science_graph)
        assert nodes, "the fixture graph has communities"
        text = nodes[0]["text"]
        assert "entities in the graph" in text
        assert "Relations inside it:" in text
        assert "BORN_IN" in text
