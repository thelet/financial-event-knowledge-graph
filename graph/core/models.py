"""What a projection emits: nodes, edges, and the export that holds them.

The vocabulary of *this* layer, not of Neo4j and not of the ontology. A `GraphNode` knows
its base label (what a uniqueness constraint attaches to, V1_GRAPH_PROTOTYPE §3.1) and its
concrete labels (what Browser styling and readable Cypher use); it does not know Cypher, a
driver, or a database session. Nothing here imports one.

The two sort keys are part of the contract rather than a detail of the writer: §4.4 requires
two projections of one run to produce byte-identical exports, and that guarantee is only as
strong as a *declared* order every producer applies.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

#: Bumped when the projection changes shape (§5.5 provenance). Not the ontology version and
#: not the extraction layout version — three different things that move independently.
#:
#: 1.1.0 — the G1 review repairs changed the emitted shape materially and the version had to
#: follow: null-valued properties are no longer written (53,951 of them, because Cypher's
#: `SET n += {k: null}` removes the key, so a stored null was a fiction the database would not
#: hold); the three date twins were removed as byte-identical copies carrying no information;
#: and `EVIDENCED_BY` gained `quoted_text`, `table_id` and `block_ids`, which it had been
#: dropping for all 2,707 observations. Bumped here rather than left at 1.0.0 because the run
#: id digests this constant, and two materially different exports sharing one id is the
#: collision §4.4 exists to prevent. Safe to bump now: nothing consumes the export yet.
#:
#: 1.2.0 — F0 Part E. Two shape changes, both additive and neither optional to record:
#: an eighth base label `:EvidenceSource` for evidence that names no filed passage
#: (F0 §2.3), and ontology instance properties — `cik`, `tickers`, `exchange`, `mic` — which
#: `nodes.py` had been dropping on all four declared instances. A consumer that read a 1.1.0
#: export and a 1.2.0 one as the same shape would see an `:Entity` gain properties and a node
#: class it has no constraint for; the version is what makes that a stated change rather than
#: a surprise.
GRAPH_PROJECTION_VERSION = "1.2.0"

#: The eight base labels §3.1 declares. A base label is what §5.2's uniqueness constraints
#: are created on, so the set is closed: a projection that invents a ninth would create a
#: node class no constraint protects.
#:
#: `EvidenceSource` joined the seven on 2026-08-03 (F0 §2.3). It is a *base* label rather than
#: a concrete one because `EVIDENCED_BY` has to point at it: `GraphEdge` refuses an endpoint
#: whose base label is not declared here, and the loader `MATCH`es endpoints on the base label
#: so the match uses a constraint index instead of scanning. The five kinds it covers are
#: concrete labels on top of it, exactly as `:Entity` carries `:PublicCompany`.
BASE_LABELS = ("Entity", "Metric", "Observation", "Event", "Passage", "Document", "Issue",
               "EvidenceSource")


class GraphNode(BaseModel):
    """One node, keyed by an extraction id (§4.1) or by §4.2's per-event placeholder key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    base_label: str
    #: `base_label` first, then the concrete labels most-specific-first. **Deliberately not
    #: sorted alphabetically**: §3.1 derives concrete entity labels from the payload's own
    #: type plus its `is_a` ancestors — `:Entity:AssetBackedDebtFacility:CreditFacility:
    #: CreditAgreement:Agreement` — and that chain carries the specialization order.
    #: `registry.ancestors()` returns it deterministically, so ordering is reproducible
    #: without being alphabetical. What is enforced here is the part determinism depends on:
    #: a fixed first element and no repeats.
    labels: tuple[str, ...]
    properties: dict[str, Any]

    @field_validator("key")
    @classmethod
    def _key_is_usable(cls, value: str) -> str:
        # §2.3 trap 2: `passage_id` / `document_id` / `document_type` can be empty strings in
        # the catalogs. Legal there, never a node key.
        if not value.strip():
            raise ValueError("node key must be a non-empty string")
        return value

    @field_validator("base_label")
    @classmethod
    def _base_label_is_declared(cls, value: str) -> str:
        if value not in BASE_LABELS:
            raise ValueError(f"base_label {value!r} is not one of {BASE_LABELS}")
        return value

    @model_validator(mode="after")
    def _labels_lead_with_base(self) -> "GraphNode":
        if not self.labels:
            raise ValueError("labels must contain at least the base label")
        if self.labels[0] != self.base_label:
            raise ValueError(
                f"labels[0] is {self.labels[0]!r}, expected base_label {self.base_label!r}"
            )
        if len(set(self.labels)) != len(self.labels):
            raise ValueError(f"labels contain a repeat: {self.labels}")
        if any(not label.strip() for label in self.labels):
            raise ValueError(f"labels contain an empty label: {self.labels}")
        return self


class GraphEdge(BaseModel):
    """One directed edge, keyed for idempotent loading (§4.1).

    Endpoints carry their base labels because the loader `MATCH`es on the base label — §5.2's
    constraints are per base label, and a `MATCH` without one scans every node.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    edge_key: str
    type: str
    source_key: str
    source_base_label: str
    target_key: str
    target_base_label: str
    properties: dict[str, Any]

    @field_validator("edge_key", "type", "source_key", "target_key")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("edge_key, type and endpoint keys must be non-empty")
        return value

    @field_validator("source_base_label", "target_base_label")
    @classmethod
    def _endpoint_label_is_declared(cls, value: str) -> str:
        if value not in BASE_LABELS:
            raise ValueError(f"endpoint base label {value!r} is not one of {BASE_LABELS}")
        return value


#: §4.4's declared export order. Assigned rather than written as `def` so the contract other
#: modules import is exactly the callable, not a wrapper around one.
NODE_SORT_KEY = lambda node: (node.base_label, node.key)  # noqa: E731
EDGE_SORT_KEY = lambda edge: (edge.type, edge.source_key, edge.target_key, edge.edge_key)  # noqa: E731


class GraphExport(BaseModel):
    """The whole projection of one extraction run, before anything is written or loaded.

    Deliberately not a file and not a session: the export is the value both `nodes.jsonl` /
    `edges.jsonl` and the G2 loader are produced from, which is what makes §4.4's
    byte-identity claim testable with no database and no disk.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    projection_version: str = GRAPH_PROJECTION_VERSION

    def sorted(self) -> "GraphExport":
        """The same export in §4.4's declared order. Idempotent."""
        return self.model_copy(update={
            "nodes": tuple(sorted(self.nodes, key=NODE_SORT_KEY)),
            "edges": tuple(sorted(self.edges, key=EDGE_SORT_KEY)),
        })


__all__ = [
    "BASE_LABELS",
    "EDGE_SORT_KEY",
    "GRAPH_PROJECTION_VERSION",
    "GraphEdge",
    "GraphExport",
    "GraphNode",
    "NODE_SORT_KEY",
]
