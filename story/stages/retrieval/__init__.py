"""S1 — the safe retrieval layer. §9's tools, §16's limits, and no driver anywhere in it.

Four modules, split by concern rather than for symmetry:

    cypher.py             the eleven statements, as plain string constants and nothing else
    lucene_escaping.py    a term list to a Lucene query that can only mean those words
    metric_metadata.py    C4 — the ontology answers for a metric, the graph only says whether
                          it was projected
    graph_tools.py        the parameter contract, the bounds, the trace, the five answers

`results.py` holds the answer vocabulary the other four and the tests all agree on.

**The one thing to know before calling anything here**: `BoundedGraphRetriever` takes a
`story.contracts.ReadQueryExecutor` and constructs no connection. `story/context.py` builds
the executor (D1); every tool in this package is exercisable against
`tests/story/conftest.py`'s recorded executor with `neo4j` uninstalled.
"""

from __future__ import annotations

from story.stages.retrieval.graph_tools import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_CONTEXT_NEIGHBOURS,
    TOOL_NAMES,
    TOOL_PARAMETERS,
    BoundedGraphRetriever,
)
from story.stages.retrieval.lucene_escaping import build_query, escape_term
from story.stages.retrieval.results import (
    CITATION_FIELDS,
    MAX_ROWS,
    UNAVAILABLE_TOOLS,
    result_code,
)

__all__ = [
    "CITATION_FIELDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_CONTEXT_NEIGHBOURS",
    "MAX_ROWS",
    "TOOL_NAMES",
    "TOOL_PARAMETERS",
    "UNAVAILABLE_TOOLS",
    "BoundedGraphRetriever",
    "build_query",
    "escape_term",
    "result_code",
]
