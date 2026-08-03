"""§7's three checks, run in one pass, answering with a report rather than an exception.

Responsibility: decide whether the graph a story run is about to read still matches the inputs
it was built from. This ships before any retrieval because §17.8 shows every other check in the
plan passing a fact from a superseded run — a bounded, cited, verified draft quoting numbers
from a graph that no longer exists is the failure mode the rest of the plan cannot see.

The three checks, and which one carries the weight:

1. **Graph vs extraction** — `sha256(run.complete)` of the directory the graph manifest names
   against `inputs.run_complete_sha256`. **Digest-based, and that is the whole point.** An
   extraction run was once regenerated in place under an unchanged `run_id` (§18); an id
   comparison passes that, a digest does not. `tests/story/test_story_freshness.py` builds
   exactly that fixture and asserts the id check would have let it through.
2. **Neo4j vs export** — the `:GraphLoad` marker's `graph_run_id`, its `status`, its counts, and
   the distinct `graph_run_id` across the loaded nodes and the `:Observation`s.
3. **Ontology** — `ontology_definition_hash` on the loaded nodes against the loaded ontology's.

**Order is load-bearing twice.** The filesystem checks run before the database is touched, so a
checkout with no extraction directory gets its answer without a connection; and connectivity is
verified before any statement is issued, because F12 measured `verify_connectivity()` failing in
0.0 s against a refused port while `execute_query` retries for **35 seconds**. A gate that took
thirty-five seconds to say "the database is down" is a gate nobody runs, and §7 says every
command that builds a package or a draft runs this one first.

**What is deliberately *not* caught.** A `Neo4jError` from a malformed statement, or a
`StoryGraphUnreachableError` from a server that died between the probe and the read, propagates.
Staleness is a state to report; a broken query is a defect in this file, and swallowing it into
a `FreshnessCheck` would file a bug report as a data problem.

**Zero writes.** Every statement is a constant in `loaded_graph.py`; this module issues none of
its own and holds no Cypher at all.
"""

from __future__ import annotations

from pathlib import Path

from extraction.core.run_directory import COMPLETION_MARKER, file_digest
from ontology import load_ontology
from ontology.core.errors import OntologyError

from story.contracts import ReadQueryExecutor
from story.core.graph_identity import GraphIdentity, GraphIdentityError, read_graph_identity
from story.stages.freshness.freshness_report import (
    FreshnessCheck,
    FreshnessReport,
    RefusalCode,
)
from story.stages.freshness.loaded_graph import (
    FRESHNESS_TIMEOUT_SECONDS,
    LoadedGraph,
    read_loaded_graph,
)

#: `graph/stages/load/lifecycle.py`'s `STATUS_COMPLETE`, re-stated for the reason
#: `LOAD_MARKER_LABEL` is: `graph.stages` is not a surface this package may import
#: (WORKSTREAM_BOUNDARY §4). A `neo4j`-marked test reads the real marker, so a change upstream
#: fails a test rather than silently widening what counts as a finished load.
STATUS_COMPLETE = "complete"


def check_freshness(
    *,
    executor: ReadQueryExecutor,
    graph_run_id: str,
    graph_runs_root: Path,
    root: Path,
    ontology_definition_hash: str | None = None,
    timeout_seconds: float = FRESHNESS_TIMEOUT_SECONDS,
) -> FreshnessReport:
    """Run §7's three checks against one graph run and return what they found.

    `executor` is a `ReadQueryExecutor` and is **injected, never constructed** (D1) — this stage
    imports no driver, and `story/context.py` is the only module that opens one.

    `root` is what a repo-relative `inputs.extraction_run_directory` resolves against, and it is
    separate from `graph_runs_root` because the two can legitimately point elsewhere: a test
    stages both under `tmp_path`, and an operator may pass `--graph-runs-root` at a copied
    export while the extraction runs stay in the repository.

    `ontology_definition_hash` is a string rather than an `Ontology` because one string is all
    §7's third check needs, and requiring the whole protocol would mean a test either loading
    the real ontology or stubbing eight methods to assert a hash comparison. Left `None` it
    loads the ontology the manifest names, which is the behaviour every caller wants.
    """
    try:
        identity = read_graph_identity(graph_runs_root, graph_run_id)
    except GraphIdentityError as exc:
        return FreshnessReport(graph_run_id=graph_run_id, checks=(
            FreshnessCheck.refusing(
                "graph_manifest",
                RefusalCode.GRAPH_MANIFEST_UNREADABLE,
                expected=f"a readable manifest for {graph_run_id}",
                observed=str(exc),
                detail="the gate cannot say whether a run is stale without knowing what it "
                       "claimed to be built from"),
        ))

    checks: list[FreshnessCheck] = [
        FreshnessCheck.comparing(
            "graph_manifest",
            RefusalCode.GRAPH_MANIFEST_UNREADABLE,
            expected=graph_run_id,
            observed=identity.graph_run_id,
            detail=f"read from {Path(graph_runs_root) / graph_run_id}; projection "
                   f"{identity.graph_projection_version}, extraction run "
                   f"{identity.extraction_run_id}"),
        *extraction_input_checks(identity, root=root),
    ]

    # F12: `verify_connectivity()` fails in 0.0s against a refused port while `execute_query`
    # retries a managed transaction for 35 seconds. The handshake goes first so a stopped
    # container is reported in milliseconds by the gate every other command runs.
    health = executor.verify_connectivity()
    checks.append(FreshnessCheck.comparing(
        "graph_reachable",
        RefusalCode.GRAPH_UNREACHABLE,
        expected="ok",
        observed=health.status,
        detail=health.detail or ""))
    if not health.ok:
        # Nothing below this line can be answered, and every one of them would answer *wrongly*
        # — an unreachable database reports zero nodes, which is a count mismatch that would
        # send an operator to look at the export.
        return FreshnessReport(graph_run_id=graph_run_id, checks=tuple(checks))

    loaded = read_loaded_graph(executor, timeout_seconds=timeout_seconds)
    checks.extend(loaded_graph_checks(
        identity, loaded, ontology_definition_hash=ontology_definition_hash))
    return FreshnessReport(graph_run_id=graph_run_id, checks=tuple(checks))


def extraction_input_checks(
    identity: GraphIdentity, *, root: Path
) -> tuple[FreshnessCheck, ...]:
    """§7 check 1, on the filesystem alone. No database, no ontology, no network.

    Two checks and not one: "the directory the manifest names is not here" and "it is here and
    holds different bytes" are different operator actions — restore an archive, or rebuild the
    graph — and collapsing them into one refusal would make the report say `digest mismatch`
    about a digest nobody could compute.
    """
    directory = identity.extraction_run_path(root)
    marker = directory / COMPLETION_MARKER

    if not directory.is_dir():
        return (
            FreshnessCheck.refusing(
                "extraction_run_directory",
                RefusalCode.EXTRACTION_RUN_DIRECTORY_MISSING,
                expected=f"a directory at {identity.extraction_run_directory}",
                observed=f"{directory} does not exist",
                detail=f"the graph manifest was projected from extraction run "
                       f"{identity.extraction_run_id}; without its bytes the digest §7 checks "
                       "cannot be computed at all"),
        )
    if not marker.is_file():
        return (
            FreshnessCheck.comparing(
                "extraction_run_directory",
                RefusalCode.EXTRACTION_RUN_DIRECTORY_MISSING,
                expected="present",
                observed="present",
                detail=str(directory)),
            FreshnessCheck.refusing(
                "package_input_digest",
                RefusalCode.PACKAGE_INPUT_DIGEST_MISMATCH,
                expected=identity.run_complete_sha256 or "<none recorded>",
                observed=f"{directory / COMPLETION_MARKER} does not exist",
                detail=f"{COMPLETION_MARKER} is written last and is the extraction run's "
                       "completion marker; a directory without one is an unfinished run, not "
                       "the run this graph was projected from"),
        )

    observed = file_digest(marker)
    return (
        FreshnessCheck.comparing(
            "extraction_run_directory",
            RefusalCode.EXTRACTION_RUN_DIRECTORY_MISSING,
            expected="present",
            observed="present",
            detail=str(directory)),
        FreshnessCheck.comparing(
            "package_input_digest",
            RefusalCode.PACKAGE_INPUT_DIGEST_MISMATCH,
            expected=identity.run_complete_sha256 or "<none recorded>",
            observed=observed,
            detail=f"sha256({marker}); the extraction run id "
                   f"{identity.extraction_run_id!r} is *not* what identifies these bytes — one "
                   "id has named two different runs (§18), which is why this check hashes"),
    )


def loaded_graph_checks(
    identity: GraphIdentity,
    loaded: LoadedGraph,
    *,
    ontology_definition_hash: str | None = None,
) -> tuple[FreshnessCheck, ...]:
    """§7 checks 2 and 3, as a pure function of two values.

    Pure on purpose: every refusal below is reproducible in a test by writing a different number
    into a hand-built `LoadedGraph`, with no fake executor and no scripted Cypher in the way.
    """
    expected_counts = {"nodes": identity.node_count, "edges": identity.edge_count}
    checks = [
        FreshnessCheck.comparing(
            "load_marker_present",
            RefusalCode.LOAD_INCOMPLETE,
            expected=1,
            observed=len(loaded.markers),
            detail=f"exactly one (:GraphLoad) node; found {len(loaded.markers)}"),
        FreshnessCheck.comparing(
            "load_status_complete",
            RefusalCode.LOAD_INCOMPLETE,
            expected=(STATUS_COMPLETE,),
            observed=loaded.marker_statuses,
            detail="a marker left at 'loading' or 'failed' means the load never finished; §10 "
                   "writes the completion marker last precisely so this is detectable"),
        FreshnessCheck.comparing(
            "load_marker_graph_run_id",
            RefusalCode.GRAPH_RUN_ID_MISMATCH,
            expected=(identity.graph_run_id,),
            observed=loaded.marker_graph_run_ids),
        FreshnessCheck.comparing(
            "node_graph_run_id",
            RefusalCode.GRAPH_RUN_ID_MISMATCH,
            expected=(identity.graph_run_id,),
            observed=loaded.node_graph_run_ids,
            detail="the distinct graph_run_id over every loaded node; more than one means two "
                   "runs were merged into one database (§6.2)"),
        FreshnessCheck.comparing(
            "edge_graph_run_id",
            RefusalCode.GRAPH_RUN_ID_MISMATCH,
            expected=(identity.graph_run_id,),
            observed=loaded.edge_graph_run_ids),
        FreshnessCheck.comparing(
            "observation_graph_run_id",
            RefusalCode.GRAPH_RUN_ID_MISMATCH,
            expected=(identity.graph_run_id,),
            observed=loaded.observation_graph_run_ids,
            detail=f"{loaded.observation_count} :Observation nodes; §7 names this label because "
                   "an observation from a superseded run is the fact that ends up quoted"),
        FreshnessCheck.comparing(
            "load_marker_counts",
            RefusalCode.COUNT_MISMATCH,
            expected=expected_counts,
            observed=_marker_counts(loaded),
            detail="what the loader recorded it wrote, against what the export says it "
                   "produced"),
        FreshnessCheck.comparing(
            "loaded_element_counts",
            RefusalCode.COUNT_MISMATCH,
            expected=expected_counts,
            observed={"nodes": loaded.node_count, "edges": loaded.edge_count},
            detail="counted now, excluding the loader's own (:GraphLoad) marker — the database "
                   "holds one more node than the export wrote, and a count that included it "
                   "would refuse every correct run"),
    ]
    checks.append(_ontology_check(identity, loaded, ontology_definition_hash))
    return tuple(checks)


def _marker_counts(loaded: LoadedGraph) -> dict[str, object]:
    """The marker's own counts, or `<absent>` where the load never set them.

    Absent rather than zero: Neo4j stores no null, the loader `REMOVE`s these on an unfinished
    load, and reporting `nodes=0` for a measurement nobody took would send a reader to compare
    an empty database against a 28,836-node export.
    """
    if len(loaded.markers) != 1:
        return {"nodes": "<no single marker>", "edges": "<no single marker>"}
    marker = loaded.markers[0]
    return {
        "nodes": "<absent>" if marker.node_count is None else marker.node_count,
        "edges": "<absent>" if marker.edge_count is None else marker.edge_count,
    }


def _ontology_check(
    identity: GraphIdentity, loaded: LoadedGraph, ontology_definition_hash: str | None
) -> FreshnessCheck:
    """§7 check 3: the vocabulary the nodes were projected under is the one loaded now.

    The comparison is against the *loaded* ontology rather than against the manifest's copy of
    the hash, which would only prove the manifest agrees with itself.
    `graph/core/manifest.py:check_ontology_matches_run` raises `OntologyMismatchError` on the way
    in; this is the same claim asked on the way out, after the ontology package has had every
    opportunity to change underneath a finished graph.
    """
    if ontology_definition_hash is None:
        try:
            ontology_definition_hash = load_ontology(identity.ontology_id).definition_hash
        except OntologyError as exc:
            return FreshnessCheck.refusing(
                "node_ontology_definition_hash",
                RefusalCode.ONTOLOGY_HASH_MISMATCH,
                expected=f"the definition_hash of ontology {identity.ontology_id!r}",
                observed=f"it did not load: {exc}",
                detail="the graph names a vocabulary this checkout cannot produce, so no "
                       "comparison is possible and the facts cannot be interpreted")
    return FreshnessCheck.comparing(
        "node_ontology_definition_hash",
        RefusalCode.ONTOLOGY_HASH_MISMATCH,
        expected=(ontology_definition_hash,),
        observed=loaded.ontology_definition_hashes,
        detail=f"ontology {identity.ontology_id} {identity.ontology_version}; the distinct "
               "ontology_definition_hash over every loaded node against the hash this checkout "
               "computes now")


__all__ = [
    "STATUS_COMPLETE",
    "check_freshness",
    "extraction_input_checks",
    "loaded_graph_checks",
]
