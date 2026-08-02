"""The projection's third half: check what was built, then write bytes that never move.

`nodes.py` builds nodes and `edges.py` builds edges; neither checks the other, deliberately —
a module that both minted a key and asserted the key was projected would be checking itself
(`edges.build_edges`' own docstring). This module is where the two meet, and it is the only
place in the layer that touches a filesystem.

**Determinism is the whole contract** (V1_GRAPH_PROTOTYPE §4.4). Rows go out in
`models.NODE_SORT_KEY` / `models.EDGE_SORT_KEY` order and are encoded with
`json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`, which is
character-for-character the rule `extraction/stages/catalog/public.py:84-86` applies to its own
catalogs — one JSONL convention across the repository, not two that happen to agree today.
No clock, no uuid, no random value and no unsorted iteration reaches either file: the
provenance a node carries is derived (`graph.core.manifest.make_graph_run_id`), and the only
clock in a graph run lives in `manifest.json`.

**Four refusals, all loud** (§6.4, §10 criteria 2 and 11):

1. an edge endpoint that no projected node carries — named by `edge_key` *and* missing key;
2. a repeated node key or edge key — the loader `MERGE`s on both, so a collision would
   silently discard a fact rather than report one;
3. an empty node key — `GraphNode` already refuses one at construction, and this second gate
   exists because §2.3 trap 2 says `passage_id`/`document_id` can legally be `""` in a
   catalog, so the day someone relaxes the model the export still stops;
4. a warning derivation that disagrees with the run's own aggregate (§5.5, 186 here).

**`manifest.json` is the completion marker, and no second marker is written.** Extraction
writes `run.complete` last because its manifest is not a file list; here the manifest already
carries the two artifact digests, so it answers both "did this finish" and "does it still hold
what it finished with". A second last-written file would give one directory two answers to one
question (§6.5's manifest row asks for exactly this, "written last, after nodes.jsonl and
edges.jsonl, following the repository's completion-marker convention").
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ontology.contracts import Ontology

from graph.core.citations import EmptyPassageCitationError
from graph.core.derivation import derive_warnings, warning_total
from graph.core.inputs import ExtractionRunInputs, GraphInputError
from graph.core.keys import NodeKeyError, PlaceholderAttributionError
from graph.core.models import (
    EDGE_SORT_KEY,
    GRAPH_PROJECTION_VERSION,
    GraphEdge,
    GraphExport,
    GraphNode,
    NODE_SORT_KEY,
)

from .edges import (
    DuplicateEdgeKeyError as EdgeKeyCollision,
    EdgeProjectionError,
    MalformedEdgePropertyError,
    build_edges,
)
from .nodes import PROVENANCE_KEYS, NodeProjectionError, build_nodes

NODES_FILENAME = "nodes.jsonl"
EDGES_FILENAME = "edges.jsonl"
REJECTED_FILENAME = "rejected.jsonl"
MANIFEST_FILENAME = "manifest.json"
PARTIAL_SUFFIX = ".partial"

#: Where a projection that refused a row lands: `<graph_run_id>.rejected/`, beside the run id
#: rather than on top of it. See `GraphRunWriter.finalize_rejected`.
REJECTED_SUFFIX = ".rejected"


# -- refusals ---------------------------------------------------------------------------


class ExportError(GraphInputError):
    """Base for every refusal this stage makes about an already-built export."""


class DanglingEndpointError(ExportError):
    """An edge points at a key no projected node carries.

    Named here rather than left to the loader on purpose: §6.3 records that Cypher's `MATCH`
    on an endpoint *filters* instead of raising, so an export shipped with a dangling edge
    loads quietly and one fact goes missing. Catching it against the node set is the cheap
    half of that guarantee, and it works with no database running.
    """


class DuplicateKeyError(ExportError):
    """One key names two different rows. The loader `MERGE`s on it; one would be lost."""


class EmptyNodeKeyError(ExportError):
    """A node key that is blank. `""` is a legal catalog value and never an identity."""


class WarningReconciliationError(ExportError):
    """The re-derived warning set and the run's recorded aggregate disagree (§5.5)."""


# -- rejected rows ----------------------------------------------------------------------

#: Codes for the two stage refusals that declare none of their own. `edges.py` gives
#: `DuplicateEdgeKeyError` and `MalformedEdgePropertyError` no `code` attribute and this stage
#: does not modify its siblings, so the code is assigned here and named for what it is. Every
#: other refusal carries §6.4's code on the class.
_ASSIGNED_CODES: tuple[tuple[type[BaseException], str], ...] = (
    (EdgeKeyCollision, "DUPLICATE_EDGE_KEY"),
    (MalformedEdgePropertyError, "MALFORMED_EDGE_PROPERTY"),
    (NodeKeyError, "MISSING_REQUIRED_KEY"),
    (PlaceholderAttributionError, "MISSING_REQUIRED_KEY"),
)

#: What a rejection may be raised from. Everything else — a mismatched observation id, a
#: catalog conflict, an ontology mismatch — is a *run* failure and is not a malformed row, so
#: it propagates instead of being filed as one row the reader might think was skipped.
#: `EmptyPassageCitationError` is here because it is a malformed *row* — a citation with no
#: locatable passage — raised now from `graph.core.citations` for both stages rather than
#: from the node builder alone (§2.3 trap 2).
REJECTABLE = (NodeProjectionError, EdgeProjectionError, NodeKeyError,
              PlaceholderAttributionError, EmptyPassageCitationError)


@dataclass(frozen=True)
class RejectedRow:
    """§6.4's shape: `{row_index, file, code, detail}`, written to `rejected.jsonl`.

    **`row_index` and `file` are `null`, and that is a measured limitation rather than an
    oversight.** `build_nodes` and `build_edges` are fail-fast: they raise on the first row
    they cannot project and their exceptions carry a code but no catalog name and no ordinal.
    Filling either in from the exception message would be inventing provenance for a row the
    builder never identified, and making them real means giving the two builders a per-row
    rejection contract — a change to modules this stage does not own. The keys stay in the
    shape §6.4 declares so the file does not change shape when that happens; `detail` carries
    the exception class, which is what actually names the refusal today.
    """

    code: str
    detail: str
    row_index: int | None = None
    file: str | None = None

    def as_row(self) -> dict[str, Any]:
        return {"row_index": self.row_index, "file": self.file,
                "code": self.code, "detail": self.detail}


def rejection_of(exc: BaseException) -> RejectedRow:
    """One typed refusal as a rejected row, with §6.4's code."""
    code = getattr(exc, "code", None)
    if not isinstance(code, str):
        code = next((assigned for cls, assigned in _ASSIGNED_CODES if isinstance(exc, cls)),
                    "UNCODED_PROJECTION_REFUSAL")
    return RejectedRow(code=code, detail=f"{type(exc).__name__}: {exc}")


# -- the on-disk row shapes -------------------------------------------------------------


def node_row(node: GraphNode) -> dict[str, Any]:
    """What one line of `nodes.jsonl` holds. The loader's input contract (§6.3).

    `labels` is a list rather than a set, because §3.1's concrete labels carry the
    specialization order and a set would both lose it and make the bytes depend on hash
    iteration — the two failure modes §4.4 exists to prevent.
    """
    return {
        "key": node.key,
        "base_label": node.base_label,
        "labels": list(node.labels),
        "properties": node.properties,
    }


def edge_row(edge: GraphEdge) -> dict[str, Any]:
    """What one line of `edges.jsonl` holds. Endpoint base labels included, per §6.3."""
    return {
        "edge_key": edge.edge_key,
        "type": edge.type,
        "source_key": edge.source_key,
        "source_base_label": edge.source_base_label,
        "target_key": edge.target_key,
        "target_base_label": edge.target_base_label,
        "properties": edge.properties,
    }


def render_rows(rows: Iterable[Mapping[str, Any]]) -> str:
    """The repository's JSONL encoding, restated in one place for both artifacts.

    `sort_keys=True` at every depth, `ensure_ascii=False` so a filed sentence keeps its own
    characters, and the compact separators the catalog writer uses. Rows are written in the
    order given: sorting is the caller's business and belongs to `GraphExport.sorted()`, which
    owns §4.4's declared order.
    """
    return "".join(
        json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows)


def render_nodes(nodes: Sequence[GraphNode]) -> str:
    return render_rows(node_row(node) for node in nodes)


def render_edges(edges: Sequence[GraphEdge]) -> str:
    return render_rows(edge_row(edge) for edge in edges)


def render_rejections(rows: Sequence[RejectedRow]) -> str:
    return render_rows(row.as_row() for row in rows)


# -- validation -------------------------------------------------------------------------


def check_node_keys(nodes: Sequence[GraphNode]) -> None:
    """Every node key non-empty and unique (§4.1, §5.2's uniqueness constraints)."""
    seen: dict[str, GraphNode] = {}
    for node in nodes:
        if not node.key or not node.key.strip():
            raise EmptyNodeKeyError(
                f"a :{node.base_label} node has an empty key; an empty id is never an "
                "identity (§2.3 trap 2)")
        earlier = seen.get(node.key)
        if earlier is not None:
            raise DuplicateKeyError(
                f"node key {node.key!r} is projected twice, as :{earlier.base_label} and "
                f":{node.base_label}; §5.2 constrains it unique and the loader MERGEs on it")
        seen[node.key] = node


def check_edge_keys(edges: Sequence[GraphEdge]) -> None:
    """Every `edge_key` unique. An identical repeat is a collision too — see below.

    `edges.build_edges` already collapses *byte-identical* repeats (`CONCERNS_METRIC` and
    `PLACEHOLDER_FOR` are set-like by construction) and refuses disagreeing ones, so anything
    reaching here twice is a bug in that collapse or an export assembled by hand. Re-checking
    costs one pass and makes the file's guarantee true of the file rather than of a builder.
    """
    seen: set[str] = set()
    for edge in edges:
        if edge.edge_key in seen:
            raise DuplicateKeyError(
                f"edge key {edge.edge_key!r} is projected twice; the loader MERGEs on it and "
                "one of the two would be silently discarded")
        seen.add(edge.edge_key)


def check_endpoints(nodes: Sequence[GraphNode], edges: Sequence[GraphEdge]) -> None:
    """Every endpoint of every edge exists among the projected nodes (§10 criterion 2)."""
    keys = {node.key for node in nodes}
    for edge in edges:
        for side, key in (("source_key", edge.source_key), ("target_key", edge.target_key)):
            if key not in keys:
                raise DanglingEndpointError(
                    f"edge {edge.edge_key!r} ({edge.type}) has a dangling {side}: "
                    f"{key!r} is not a projected node key. A MATCH on a missing endpoint "
                    "filters rather than fails (§6.3), so the load would drop this edge "
                    "silently.")


def check_export(export: GraphExport) -> None:
    """Every structural guarantee the export claims, checked on the value, not on a database."""
    check_node_keys(export.nodes)
    check_edge_keys(export.edges)
    check_endpoints(export.nodes, export.edges)


# -- warning reconciliation (§5.5) ------------------------------------------------------


@dataclass(frozen=True)
class WarningReconciliation:
    """The re-derived warning total beside the run's own, and whether they may be compared.

    `covers_whole_run` is not a hedge. `tests/fixtures/graph/` ships the run's manifest
    verbatim — its README says a manifest edited to match a slice would no longer be the run's
    manifest — so the aggregate there describes 2,707 observations beside 30 fixture rows.
    Comparing those two numbers would fail for a reason that is not a defect. The comparison
    is therefore made when the manifest's own counts say the input *is* the whole run, and the
    real-run test proves it runs there. This mirrors `nodes._manifest_describes_these_rows`,
    which gates the same reconciliation inside the node builder.
    """

    derived: int
    manifest_total: int
    agreed: bool
    covers_whole_run: bool
    warned_claims: int

    def as_block(self) -> dict[str, Any]:
        return {
            "derived": self.derived,
            "manifest_total": self.manifest_total,
            "agreed": self.agreed,
            "covers_whole_run": self.covers_whole_run,
            "warned_claims": self.warned_claims,
        }


def reconcile_warnings(
    inputs: ExtractionRunInputs, ontology: Ontology
) -> WarningReconciliation:
    """Replay the ontology's own validator over the run's observations and compare (§5.5).

    Deliberately run *before* `build_nodes`, which reconciles again internally: the failure a
    reader needs to see is "this export's warning state does not match the run", and the
    export is the thing that would ship it. The second check inside the node builder can then
    never fire first, and costs one extra replay — 2,707 validations, measured in seconds, for
    a check §10 criterion 11 makes an acceptance test.
    """
    derived = derive_warnings(inputs.observations, inputs.evidence, ontology)
    total = warning_total(derived)
    expected = inputs.manifest.ontology_validation_warnings
    counts = inputs.manifest.counts
    covers = (counts.get("observations") == len(inputs.observations)
              and counts.get("claims") == len(inputs.claims))
    reconciliation = WarningReconciliation(
        derived=total, manifest_total=expected, agreed=total == expected,
        covers_whole_run=covers, warned_claims=len(derived))
    if covers and not reconciliation.agreed:
        raise WarningReconciliationError(
            f"derived {total} warning(s) over {len(derived)} warned observation(s); the run "
            f"manifest records {expected}. The denominators differ — the manifest counts "
            f"warnings over all {counts.get('claims')} claims including events and "
            f"relationships, this derivation over the {counts.get('observations')} "
            "observations only — so a disagreement is a changed observation rule or a new "
            "event/relationship-level warning, never a rounding difference (§5.5).")
    return reconciliation


# -- counts -----------------------------------------------------------------------------


def nodes_by_label(nodes: Iterable[GraphNode]) -> dict[str, int]:
    """`base_label -> count`, sorted. Base labels rather than concrete ones, so the breakdown
    sums to the node total and lines up with §5.2's per-base-label uniqueness constraints."""
    counts: dict[str, int] = {}
    for node in nodes:
        counts[node.base_label] = counts.get(node.base_label, 0) + 1
    return {key: counts[key] for key in sorted(counts)}


# -- the projection itself --------------------------------------------------------------


def build_provenance(
    *,
    extraction_run_id: str,
    graph_run_id: str,
    ontology_id: str,
    ontology_version: str,
    ontology_definition_hash: str,
    extraction_code_commit: str | None,
    graph_code_commit: str | None,
    graph_projection_version: str = GRAPH_PROJECTION_VERSION,
) -> dict[str, str]:
    """§5.5's eight fields, on every node and every edge.

    An absent commit becomes `""` rather than `None`: `nodes._checked_provenance` requires a
    scalar, and Neo4j stores no null property — a node whose `graph_code_commit` is an empty
    string says "not recorded" in the one vocabulary the property model has. The manifest
    keeps the honest `null`, because JSON has one.
    """
    provenance = {
        "extraction_run_id": extraction_run_id,
        "graph_run_id": graph_run_id,
        "graph_projection_version": graph_projection_version,
        "ontology_id": ontology_id,
        "ontology_version": ontology_version,
        "ontology_definition_hash": ontology_definition_hash,
        "extraction_code_commit": extraction_code_commit or "",
        "graph_code_commit": graph_code_commit or "",
    }
    missing = [key for key in PROVENANCE_KEYS if key not in provenance]
    if missing:  # pragma: no cover - a guard against the two lists drifting apart
        raise ExportError(f"provenance is missing {missing}")
    return provenance


@dataclass(frozen=True)
class CatalogProjection:
    """`contracts.GraphProjection`, implemented: catalogs plus ontology in, an export out.

    Pure and database-free. It builds, sorts and checks; it writes nothing, which is what lets
    §4.4's byte-identity claim be tested without a filesystem.
    """

    ontology: Ontology
    provenance: Mapping[str, Any]

    @property
    def version(self) -> str:
        return str(self.provenance.get("graph_projection_version", GRAPH_PROJECTION_VERSION))

    def project(self, inputs: object) -> GraphExport:
        # `contracts.GraphProjection` leaves `inputs` structural so a catalog reader stays out
        # of the contract every consumer imports; the narrowing happens here instead.
        if not isinstance(inputs, ExtractionRunInputs):
            raise ExportError(
                f"project() takes an ExtractionRunInputs, got {type(inputs).__name__}")
        export = GraphExport(
            nodes=build_nodes(inputs, ontology=self.ontology, provenance=self.provenance),
            edges=build_edges(inputs, ontology=self.ontology, provenance=self.provenance),
            projection_version=self.version,
        ).sorted()
        check_export(export)
        return export


def sort_key_order_holds(export: GraphExport) -> bool:
    """Whether the export is already in §4.4's declared order. Used by the determinism tests."""
    nodes = [NODE_SORT_KEY(node) for node in export.nodes]
    edges = [EDGE_SORT_KEY(edge) for edge in export.edges]
    return nodes == sorted(nodes) and edges == sorted(edges)


# -- writing ----------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteRecord:
    """What a writer did, in the order it did it. `order[-1]` must be `manifest.json`."""

    directory: Path
    order: tuple[str, ...]
    digests: Mapping[str, str]


class GraphRunWriter:
    """Stages into `<graph_run_id>.partial/`, renames into place, manifest written last.

    The same discipline `extraction/core/run_directory.py` states and for the same reason: a
    partial result must be unambiguously incomplete. Here "complete" is the presence of
    `manifest.json`, so a directory holding `rejected.jsonl` and no manifest is a projection
    that refused a row — legible without reading a line of it.

    **Two finalizations, because a failure and a success may not share a destination**
    *(corrected 2026-08-03 after review)*. `finalize` replaces `<graph_run_id>/`; that is
    correct for a complete projection, which is a byte-identical rebuild of whatever was
    there. `finalize_rejected` never touches it, and lands in `<graph_run_id>.rejected/`
    instead. The original code used `finalize` for both, so a run that refused one row
    removed a complete export and left `rejected.jsonl` in its place — the loss the
    docstring on `finalize` was claiming could not happen.

    Deliberately dumb about content: it takes names and strings. Deciding what a graph run
    holds is the pipeline's business, and a writer that knew would be a second place the file
    list is stated.
    """

    def __init__(self, root: Path | str, graph_run_id: str) -> None:
        self.root = Path(root)
        self.graph_run_id = graph_run_id
        self._order: list[str] = []
        self._digests: dict[str, str] = {}

    @property
    def final(self) -> Path:
        return self.root / self.graph_run_id

    @property
    def staging(self) -> Path:
        return self.root / f"{self.graph_run_id}{PARTIAL_SUFFIX}"

    @property
    def rejected(self) -> Path:
        return self.root / f"{self.graph_run_id}{REJECTED_SUFFIX}"

    @property
    def previous_is_complete(self) -> bool:
        """Whether `<graph_run_id>/` already holds a finished projection (§6.5's marker)."""
        return (self.final / MANIFEST_FILENAME).is_file()

    def begin(self) -> Path:
        """A fresh staging directory. An earlier partial is removed rather than resumed —
        its producer crashed and nothing records how far it got."""
        if self.staging.exists():
            shutil.rmtree(self.staging)
        self.staging.mkdir(parents=True)
        self._order = []
        self._digests = {}
        return self.staging

    def write(self, name: str, text: str) -> Path:
        path = self.staging / name
        path.write_text(text, encoding="utf-8", newline="\n")
        self._order.append(name)
        self._digests[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return path

    @property
    def digests(self) -> dict[str, str]:
        return dict(self._digests)

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(self._order)

    def finalize(self) -> WriteRecord:
        """Rename the staging directory over the final one, atomically.

        The destination is removed only *after* staging is complete, so a graph run id never
        names a half-written directory: either the previous complete projection is there, or
        this complete one is. The statement is true because this method is reachable only
        with a complete projection staged — a refusal takes `finalize_rejected`, which has no
        path to `self.final` at all — and because `graph_run_id` now derives from the input
        bytes, so a *different* input can no longer arrive at this destination.

        A complete projection may replace a complete projection: same id means same inputs
        and the same code, so the two are byte-identical by §4.4 and the replacement is a
        no-op a reader can verify.
        """
        if self.final.exists():
            shutil.rmtree(self.final)
        os.replace(self.staging, self.final)
        return WriteRecord(
            directory=self.final, order=self.order, digests=self.digests)

    def finalize_rejected(self) -> WriteRecord:
        """Land a refused projection in `<graph_run_id>.rejected/`, leaving `final` alone.

        A rejection is not a projection of this run — it is a record of why there is none —
        so it may not occupy the name a projection would have. Only a previous *rejected*
        directory is removed: it describes the same refusal from the same inputs, and
        keeping both would leave a reader deciding which of two identical files is current.
        """
        if self.rejected.exists():
            shutil.rmtree(self.rejected)
        os.replace(self.staging, self.rejected)
        return WriteRecord(
            directory=self.rejected, order=self.order, digests=self.digests)


def write_artifacts(writer: GraphRunWriter, export: GraphExport) -> dict[str, str]:
    """`nodes.jsonl` then `edges.jsonl`. Returns their digests for the manifest."""
    writer.write(NODES_FILENAME, render_nodes(export.nodes))
    writer.write(EDGES_FILENAME, render_edges(export.edges))
    return {name: writer.digests[name] for name in (NODES_FILENAME, EDGES_FILENAME)}


__all__ = [
    "CatalogProjection",
    "DanglingEndpointError",
    "DuplicateKeyError",
    "EDGES_FILENAME",
    "EmptyNodeKeyError",
    "ExportError",
    "GraphRunWriter",
    "MANIFEST_FILENAME",
    "NODES_FILENAME",
    "PARTIAL_SUFFIX",
    "REJECTABLE",
    "REJECTED_FILENAME",
    "REJECTED_SUFFIX",
    "RejectedRow",
    "WarningReconciliation",
    "WarningReconciliationError",
    "WriteRecord",
    "build_provenance",
    "check_edge_keys",
    "check_endpoints",
    "check_export",
    "check_node_keys",
    "edge_row",
    "node_row",
    "nodes_by_label",
    "reconcile_warnings",
    "rejection_of",
    "render_edges",
    "render_nodes",
    "render_rejections",
    "render_rows",
    "sort_key_order_holds",
]
