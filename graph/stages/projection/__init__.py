"""Extraction catalogs plus ontology, in; `GraphNode`s and `GraphEdge`s, out.

Pure and database-free (V1_GRAPH_PROTOTYPE §4.4): no clock, no random value, no driver. The
node builder is here; the edge builder and the export live beside it.
"""

from .edges import build_edges
from .nodes import build_nodes

__all__ = ["build_edges", "build_nodes"]
