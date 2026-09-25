"""
Shared fixtures.

`science_graph` builds the quickstart graph (examples/data/science_history.json)
in a throw-away Kùzu database without running Laya: the 12 edges below are the
ones that survive edge verification, with the relation types exactly as the
OntologyAligner produced them — including its known misalignments
(Newton -> Calculus as BORN_IN, Einstein -> General Relativity as DISCOVERED).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

DATA_PATH = Path(__file__).resolve().parents[1] / "examples" / "data" / "science_history.json"

SCIENCE_EDGES = [
    ("Albert Einstein", "BORN_IN", "Ulm"),
    ("Albert Einstein", "DISCOVERED", "General Relativity"),
    ("General Relativity", "EXTENDS", "Universal Gravitation"),
    ("General Relativity", "EXPLAINS", "Spacetime Curvature"),
    ("General Relativity", "PREDICTED", "Gravitational Waves"),
    ("Gottfried Leibniz", "DEVELOPED", "Calculus"),
    ("Isaac Newton", "AUTHORED", "Principia Mathematica"),
    ("Isaac Newton", "BORN_IN", "Calculus"),
    ("Isaac Newton", "LEADS", "Royal Society"),
    ("Isaac Newton", "BORN_IN", "Woolsthorpe"),
    ("LIGO", "DISCOVERED", "Gravitational Waves"),
    ("Principia Mathematica", "RELATED_TO", "Universal Gravitation"),
]


@pytest.fixture(scope="session")
def science_data() -> dict:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def science_graph(tmp_path_factory, science_data):
    from graphrag.graph.kuzu_client import KuzuClient

    db = KuzuClient(db_path=str(tmp_path_factory.mktemp("kuzu") / "science.kuzu"))
    db.create_schema()
    for name, description in science_data["entities"].items():
        db.upsert_node(name, properties={"description": description})
    for source, rel_type, target in SCIENCE_EDGES:
        db.upsert_edge(source, target, rel_type)
    db.run_pagerank()
    db.run_community_detection()
    yield db
    db.close()
