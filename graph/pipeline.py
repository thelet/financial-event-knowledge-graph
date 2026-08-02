"""The orchestrator: it owns ordering, and nothing else.

Read the run, check the ontology is the one that produced it, derive the run id, reconcile the
warnings, project, check, write. Every step is one call into `graph.core` or
`graph.stages.projection`; no rule about nodes, edges, keys or catalogs is stated here, which
is the property that lets the projection be tested without ever building a directory.

**Two orderings are load-bearing and neither is incidental.**

*Warnings before nodes.* `reconcile_warnings` runs before `build_nodes`, which reconciles again
internally. The failure a reader needs is "this export's warning state does not match the run",
and the export is the artifact that would ship it — so the check belongs to the thing that
writes, and the builder's own check becomes a second gate that can never fire first.

*Manifest last.* `nodes.jsonl`, then `edges.jsonl`, then `manifest.json` — the manifest carries
both artifact digests, so writing it last is what makes it the completion marker (§6.5). The
order is recorded in `WriteRecord.order` rather than asserted in a comment, because a rule
about ordering that no test can see is a rule that quietly stops holding.

**A rejected row exits non-zero and writes no manifest.** §6.4: a graph that silently drops 3%
of its claims looks exactly like a graph that did not. The refusal is still finalized —
`rejected.jsonl` is the auditable record of *why* — into `<graph_run_id>.rejected/`, beside
the run id and never on top of it, so a failed projection cannot remove a complete one
(`export.GraphRunWriter.finalize_rejected`). The absence of `manifest.json` is what says it
did not finish.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import GraphContext
from .core.inputs import ExtractionRunInputs, load_run
from .core.manifest import (
    GraphRunManifest,
    build_counts,
    check_ontology_matches_run,
    code_commit,
    extraction_inputs_block,
    input_content_digest,
    make_graph_run_id,
    utc_now_iso,
)
from .core.models import GRAPH_PROJECTION_VERSION, GraphExport
from .stages.projection.edges import edge_counts
from .stages.projection.export import (
    EDGES_FILENAME,
    MANIFEST_FILENAME,
    NODES_FILENAME,
    REJECTABLE,
    REJECTED_FILENAME,
    CatalogProjection,
    GraphRunWriter,
    RejectedRow,
    WarningReconciliation,
    WriteRecord,
    build_provenance,
    nodes_by_label,
    reconcile_warnings,
    rejection_of,
    render_rejections,
    write_artifacts,
)


@dataclass(frozen=True)
class ProjectionOutcome:
    """What one `graph project` did. `ok` is false exactly when a row was rejected."""

    graph_run_id: str
    directory: Path
    provenance: dict[str, str]
    export: GraphExport | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    reconciliation: WarningReconciliation | None = None
    rejections: tuple[RejectedRow, ...] = ()
    written: WriteRecord | None = None

    @property
    def ok(self) -> bool:
        return not self.rejections

    @property
    def write_order(self) -> tuple[str, ...]:
        return self.written.order if self.written else ()


def graph_run_id_for(
    inputs: ExtractionRunInputs, *, input_digest: str | None = None
) -> str:
    """The directory this run projects into. Clock-free, so two projections land together.

    Derived from the loaded inputs rather than from `manifest.json` alone: the manifest names
    the run, and the content digest names the bytes, and only the pair distinguishes a slice
    of a run from the run itself (`core.manifest.input_content_digest`).

    `input_digest` is a *reuse* parameter, not a default: `project` needs the same digest for
    the manifest and hashes seven files to get it, and computing it twice would be two answers
    to one question. Omitted, it is computed here from the same inputs.
    """
    return make_graph_run_id(
        extraction_run_id=inputs.manifest.run_id,
        ontology_definition_hash=inputs.manifest.ontology_definition_hash,
        input_digest=input_digest if input_digest is not None
        else input_content_digest(inputs),
    )


def project(
    context: GraphContext,
    extraction_run: Path | str,
    *,
    catalog_directory: Path | str | None = None,
) -> ProjectionOutcome:
    """Project one finalized extraction run into `data/graph_runs/<graph_run_id>/`."""
    directory = context.resolve_extraction_run(extraction_run)
    inputs = load_run(directory, catalog_directory=catalog_directory)
    ontology = context.ontology

    check_ontology_matches_run(
        inputs.manifest,
        ontology_id=str(ontology.metadata.ontology_id),
        definition_hash=ontology.definition_hash)

    input_digest = input_content_digest(inputs)
    graph_run_id = graph_run_id_for(inputs, input_digest=input_digest)
    # Read once: the nodes' provenance and the manifest must not be able to disagree about
    # which commit produced them, and two `git rev-parse` calls are two answers.
    graph_commit = code_commit(context.root)
    provenance = build_provenance(
        extraction_run_id=inputs.manifest.run_id,
        graph_run_id=graph_run_id,
        ontology_id=str(ontology.metadata.ontology_id),
        ontology_version=str(ontology.metadata.semantic_version),
        ontology_definition_hash=ontology.definition_hash,
        extraction_code_commit=inputs.manifest.code_commit,
        graph_code_commit=graph_commit,
    )

    reconciliation = reconcile_warnings(inputs, ontology)

    writer = GraphRunWriter(context.graph_runs_root, graph_run_id)
    try:
        export = CatalogProjection(ontology=ontology, provenance=provenance).project(inputs)
    except REJECTABLE as exc:
        # Fail-fast builders: one refusal, one row. See `export.RejectedRow` on why
        # `row_index` and `file` are null rather than guessed.
        rejections = (rejection_of(exc),)
        writer.begin()
        writer.write(REJECTED_FILENAME, render_rejections(rejections))
        # Never `finalize()`: that replaces `<graph_run_id>/`, and a refusal has nothing to
        # put there. `<graph_run_id>.rejected/` is the auditable record beside it.
        written = writer.finalize_rejected()
        return ProjectionOutcome(
            graph_run_id=graph_run_id, directory=written.directory, provenance=provenance,
            reconciliation=reconciliation, rejections=rejections, written=written)

    writer.begin()
    digests = write_artifacts(writer, export)
    manifest = GraphRunManifest(
        graph_run_id=graph_run_id,
        graph_projection_version=GRAPH_PROJECTION_VERSION,
        created_at=utc_now_iso(),
        extraction_run_id=inputs.manifest.run_id,
        extraction_code_commit=inputs.manifest.code_commit,
        graph_code_commit=graph_commit,
        ontology_id=str(ontology.metadata.ontology_id),
        ontology_version=str(ontology.metadata.semantic_version),
        ontology_definition_hash=ontology.definition_hash,
        corpus=dict(inputs.manifest.corpus),
        scope=dict(inputs.manifest.scope),
        counts=build_counts(
            nodes=len(export.nodes),
            edges=len(export.edges),
            nodes_by_label=nodes_by_label(export.nodes),
            edges_by_type=edge_counts(export.edges),
            rejected=0),
        warning_reconciliation=reconciliation.as_block(),
        artifacts={NODES_FILENAME: digests[NODES_FILENAME],
                   EDGES_FILENAME: digests[EDGES_FILENAME]},
        inputs=extraction_inputs_block(
            directory, inputs.manifest, input_digest=input_digest, root=context.root),
    )
    writer.write(MANIFEST_FILENAME, manifest.render())
    written = writer.finalize()

    return ProjectionOutcome(
        graph_run_id=graph_run_id, directory=written.directory, provenance=provenance,
        export=export, manifest=manifest.as_dict(), reconciliation=reconciliation,
        written=written)


__all__ = ["ProjectionOutcome", "graph_run_id_for", "project"]
