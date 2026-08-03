"""The orchestrator: it owns ordering, and nothing else.

Read the run, check the ontology is the one that produced it, derive the run id, reconcile the
warnings, project, check, write. Every step is one call into `graph.core` or
`graph.stages.projection`; no rule about nodes, edges, keys or catalogs is stated here, which
is the property that lets the projection be tested without ever building a directory.

**Three verbs, and only the first has no database.** `project` writes the export; `load` puts
one into Neo4j; `verify` reads one back and reconciles it. `load` and `verify` own exactly two
things the stages below them deliberately do not: opening and closing the driver, and reading
`manifest.json` to learn which `graph_run_id` a directory holds. Everything else is one call
into `graph.stages.load`.

The driver is opened here rather than in `cli.py` so a caller with no terminal — a test, a
notebook — gets the same ordering, and closed in a `with` so a refusal in the middle of a load
does not leak a connection. `graph/stages/load/connection.py` is still the only module that
*constructs* one (§11).

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

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .context import GraphContext
from .core.inputs import ExtractionRunInputs, load_run
from .core.verification_report import VerificationReport
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
from .stages.load.connection import GraphSettings, driver_for, load_settings
from .stages.load.lifecycle import LoadOutcome, load_graph_run
from .stages.load.reader import read_export
from .stages.load.verification import raise_for_failures, verify_graph


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


# -- the two verbs that need a database (§6.1) ---------------------------------------------------


def resolve_export(directory: Path | str, *, settings: GraphSettings | None = None) -> Path:
    """An export directory, given either a path or a bare `graph_run_id`.

    The same two spellings `GraphContext.resolve_extraction_run` accepts, for the same reason: a
    shell tab-completes the path and a manifest records the id. `graph_runs_root` comes from
    `config/graph.yaml` rather than from `GraphContext`, because `load` and `verify` are already
    holding settings for the connection and a second source for one directory name is a second
    answer.
    """
    candidate = Path(directory)
    if (candidate / MANIFEST_FILENAME).is_file() or candidate.is_dir():
        return candidate
    root = (settings or load_settings()).graph_runs_root
    return root / str(directory)


def export_run_id(directory: Path) -> str:
    """The `graph_run_id` a finished export claims, read from its completion marker.

    `manifest.json` and not the directory name: the name is what somebody typed, the manifest is
    what the projection wrote last (§6.5). A directory with no manifest did not finish, and
    loading it would put a half-projection in the database — so this raises rather than falling
    back to the name. `lifecycle.check_export_run_identity` then re-checks the answer against
    every row before anything is written.
    """
    path = directory / MANIFEST_FILENAME
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} does not exist; that projection did not finish (§6.5) and must not be loaded"
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    run_id = str(manifest.get("graph_run_id", "")).strip()
    if not run_id:
        raise ValueError(f"{path} declares no graph_run_id")
    return run_id


def load(directory: Path | str, *, replace: bool = False) -> LoadOutcome:
    """Read one export and load it, wiping first only if `replace` was explicitly asked for.

    Ordering, which is all this function owns: resolve the directory, read the manifest for the
    run id, read both artifacts through the strict reader (a malformed property must be refused
    before a connection is opened, not after 20,000 nodes), *then* open the driver and hand the
    whole thing to `lifecycle.load_graph_run`, which owns the rest.
    """
    settings = load_settings()
    export = resolve_export(directory, settings=settings)
    graph_run_id = export_run_id(export)
    contents = read_export(export)
    with driver_for(settings) as driver:
        return load_graph_run(
            driver, settings, graph_run_id, contents.nodes, contents.edges, replace=replace
        )


def verify(directory: Path | str) -> VerificationReport:
    """§10's sweep over the loaded graph, against the export it claims to hold.

    Returns the report rather than raising, so a caller can print every failing criterion; the
    CLI calls `raise_for_failures` after printing. Read-only — nothing here writes to the
    database, including no completion marker.
    """
    settings = load_settings()
    export = resolve_export(directory, settings=settings)
    contents = read_export(export)
    with driver_for(settings) as driver:
        return verify_graph(driver, settings, contents.nodes, contents.edges)


__all__ = [
    "ProjectionOutcome",
    "export_run_id",
    "graph_run_id_for",
    "load",
    "project",
    "raise_for_failures",
    "resolve_export",
    "verify",
]
