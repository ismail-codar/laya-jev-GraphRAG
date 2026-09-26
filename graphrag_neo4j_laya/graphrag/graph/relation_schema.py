"""
graphrag/graph/relation_schema.py

Relation description registry for the guided query planner.

The planner offers hop moves as Choice options, and an option only means
something to the decision model if it carries a description ("was born in a
place"), not just a type name ("BORN_IN"). Those descriptions live in the
dataset schema handed to the OntologyAligner at ingestion time and are not
stored in the graph, so they are loaded here from the same JSON file.

An entry may also carry the words that name the relation in a language the
description is not written in. The planner reads the words of an English
description out of the description itself, so a Turkish question that names
its relation with a Turkish verb ("kaç eser yazdı") matches nothing until the
schema says so.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


class RelationSchema(NamedTuple):
    """What the planner knows about the relation types by name."""

    descriptions: dict[str, str]
    words: dict[str, tuple[str, ...]]


EMPTY = RelationSchema({}, {})


def parse_relation_schema(schema: Any) -> RelationSchema:
    """
    Split a raw `{type: entry}` mapping into descriptions and relation words.

    An entry is either the description alone,

        "BORN_IN": "was born in a place"

    or the description together with the words that name the relation:

        "BORN_IN": {"description": "was born in a place",
                    "words": ["doğdu", "doğum"]}

    A word matches a question word that starts with it, which is how Turkish
    suffixes are covered ("doğdu" matches "doğduğu"). Both forms may appear in
    one schema; an entry with no words is matched by its description alone.
    """
    descriptions: dict[str, str] = {}
    words: dict[str, tuple[str, ...]] = {}
    if not isinstance(schema, dict):
        return EMPTY
    for key, entry in schema.items():
        name = str(key)
        if isinstance(entry, str):
            descriptions[name] = entry
            continue
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("description"), str):
            descriptions[name] = entry["description"]
        listed = entry.get("words")
        if isinstance(listed, list):
            stems = tuple(str(w).strip().lower() for w in listed if str(w).strip())
            if stems:
                words[name] = stems
    return RelationSchema(descriptions, words)


def load_relation_schema(path: str | None) -> RelationSchema:
    """
    Load the relation schema from *path*.

    Accepts either a dataset file with a top-level "schema" key (the
    examples/data/*.json layout) or a plain mapping. Returns an empty schema
    when no path is configured or the file cannot be read; the planner then
    falls back to bare type names.
    """
    if not path:
        return EMPTY
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Relation schema %s could not be loaded: %s", path, exc)
        return EMPTY
    return parse_relation_schema(data.get("schema", data) if isinstance(data, dict) else None)
