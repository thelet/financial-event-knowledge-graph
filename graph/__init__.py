"""Projecting one finalized extraction run into an inspectable graph.

A consumer, never a producer: the graph reads extraction's catalogs, the ontology's
definitions and normalization's corpus rows, and changes none of them. If a graph need
implies an extraction change it becomes a founder decision, not a quiet edit
(V1_GRAPH_PROTOTYPE §12).

At G1 this package is database-free by design. `graph.core` holds the input contract — typed
readers, key policy, and the two derivations the run does not record — and nothing in it
imports a driver.
"""

from .contracts import GraphProjection, GraphStore
from .core.models import (
    BASE_LABELS,
    EDGE_SORT_KEY,
    GRAPH_PROJECTION_VERSION,
    GraphEdge,
    GraphExport,
    GraphNode,
    NODE_SORT_KEY,
)

__all__ = [
    "BASE_LABELS",
    "EDGE_SORT_KEY",
    "GRAPH_PROJECTION_VERSION",
    "GraphEdge",
    "GraphExport",
    "GraphNode",
    "GraphProjection",
    "GraphStore",
    "NODE_SORT_KEY",
]
