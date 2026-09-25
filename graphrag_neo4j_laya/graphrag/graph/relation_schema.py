"""
graphrag/graph/relation_schema.py

Relation description registry for the guided query planner.

The planner offers hop moves as Choice options, and an option only means
something to the decision model if it carries a description ("was born in a
place"), not just a type name ("BORN_IN"). Those descriptions live in the
dataset schema handed to the OntologyAligner at ingestion time and are not
stored in the graph, so they are loaded here from the same JSON file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_relation_schema(path: str | None) -> dict[str, str]:
    """
    Load `{relation_type: description}` from *path*.

    Accepts either a dataset file with a top-level "schema" key (the
    examples/data/*.json layout) or a plain mapping. Returns an empty dict
    when no path is configured or the file cannot be read; the planner then
    falls back to bare type names.
    """
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Relation schema %s could not be loaded: %s", path, exc)
        return {}
    schema = data.get("schema", data) if isinstance(data, dict) else {}
    return {str(k): str(v) for k, v in schema.items() if isinstance(v, str)}
