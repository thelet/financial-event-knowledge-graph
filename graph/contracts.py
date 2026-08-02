"""The public contract of the graph layer.

Dependency-light by rule, the same rule `extraction/contracts.py` states: typing, this
package's core models, and nothing else. **No driver, no HTTP client, no configuration, no
storage import** — and in particular no `neo4j`, which V1_GRAPH_PROTOTYPE §11 confines to
`graph/stages/load/` and to no other module in the repository.

Two protocols, at two very different stages of life:

    GraphProjection   implemented at G1; pure, database-free, deterministic
    GraphStore        declared here at G1 and deliberately unimplemented until G2

Declaring `GraphStore` early is not speculation, it is the boundary that keeps the driver
out: the projection is written against a protocol nothing in `graph/core` or
`graph/stages/projection` may import an implementation of, so "the loader is the only module
that imports neo4j" is a structural fact rather than a convention (§11's structural tests).
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from .core.models import GraphEdge, GraphExport, GraphNode


@runtime_checkable
class GraphProjection(Protocol):
    """Extraction catalogs plus ontology, in; nodes and edges, out.

    A pure function of (extraction run catalogs, ontology definitions, normalization
    catalogs, projection version) — no timestamp, no random value, no clock (§4.4). Two
    projections of one run must produce byte-identical exports, which is only checkable
    because `project` returns a value rather than writing a file.

    `inputs` is left structural: `graph.core.inputs.ExtractionRunInputs` is what the
    composition root passes, and naming it here would put a catalog reader in the contract
    every consumer imports.
    """

    @property
    def version(self) -> str:
        """The projection version stamped on every node and edge (§5.5 provenance)."""

    def project(self, inputs: object) -> GraphExport: ...


@runtime_checkable
class GraphStore(Protocol):
    """Write access to the graph database. **Declared for G2, implemented nowhere at G1.**

    Wipe-and-replace rather than incremental (§6.2): a run is a whole graph, and an
    incremental load needs a retirement policy that cannot be designed before a single run
    has been looked at.

    `write_nodes` and `write_edges` return the number of rows actually written, because
    Cypher's `MATCH` on an endpoint *filters* — it does not raise — so a dangling edge is
    silently skipped unless the caller counts what came back (§6.3, a correction the plan
    records against its own first draft).
    """

    def wipe(self) -> None: ...

    def ensure_constraints(self) -> None: ...

    def write_nodes(self, nodes: Sequence[GraphNode]) -> int: ...

    def write_edges(self, edges: Sequence[GraphEdge]) -> int: ...


__all__ = ["GraphProjection", "GraphStore"]
