"""§7's three checks, run in one pass, answering with a report rather than an exception.

Responsibility: decide whether the graph a story run is about to read still matches the inputs
it was built from. This ships before any retrieval because §17.8 shows every other check in the
plan passing a fact from a superseded run — a bounded, cited, verified draft quoting numbers
from a graph that no longer exists is the failure mode the rest of the plan cannot see.

The three checks, and which one carries the weight:

1. **Graph vs extraction** — `sha256(run.complete)` of the directory the graph manifest names
   against `inputs.run_complete_sha256`, **and then the digests `run.complete` itself records
   for the ten files beside it**. **Digest-based, and that is the whole point.** An extraction
   run was once regenerated in place under an unchanged `run_id` (§18); an id comparison passes
   that, a digest does not. `tests/story/test_story_freshness.py` builds exactly that fixture
   and asserts the id check would have let it through.

   Hashing the marker alone was not enough, and shipping it that way was the defect R2a
   repairs. `run.complete` is a *manifest* of the other files
   (`extraction/core/run_directory.py:7-10`: "this run finished" and "this run still holds what
   it finished with" are the same question only because the marker lists them), so overwriting
   `claims.jsonl` leaves the marker byte-identical and the gate silent — measured, before the
   fix, as `passed=True codes=[]` against a `claims.jsonl` replaced with eight bytes. The gate
   is what every other control in this layer stands on, so it verifies what the marker records
   rather than only that the record is unchanged.
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

    Three checks and not one: "the directory the manifest names is not here", "it is here and
    its marker holds different bytes" and "the marker is unchanged and the files it lists are
    not" are three different operator actions — restore an archive, rebuild the graph, or find
    out who wrote into a finished run — and collapsing them would make the report say `digest
    mismatch` about a digest nobody could compute.

    The third is the one R2a added. See the module docstring: the marker is a manifest, and a
    gate that hashed only the manifest passed a `claims.jsonl` replaced with eight bytes.
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

    present = FreshnessCheck.comparing(
        "extraction_run_directory",
        RefusalCode.EXTRACTION_RUN_DIRECTORY_MISSING,
        expected="present",
        observed="present",
        detail=str(directory))

    if not marker.is_file():
        return (
            present,
            FreshnessCheck.refusing(
                "package_input_digest",
                RefusalCode.PACKAGE_INPUT_DIGEST_MISMATCH,
                expected=identity.run_complete_sha256 or "<none recorded>",
                observed=f"{directory / COMPLETION_MARKER} does not exist",
                detail=f"{COMPLETION_MARKER} is written last and is the extraction run's "
                       "completion marker; a directory without one is an unfinished run, not "
                       "the run this graph was projected from"),
        )

    # D2: `file_digest` and `read_text` both reach the filesystem, and the gate runs before
    # every command — a marker whose permissions changed, or whose bytes are not UTF-8, is a
    # state to report. `UnicodeDecodeError` is a `ValueError` and *not* an `OSError`, which is
    # the exact confusion that let a traceback out of this stage once already.
    try:
        observed = file_digest(marker)
        recorded = marker_contents(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return (
            present,
            FreshnessCheck.refusing(
                "package_input_digest",
                RefusalCode.PACKAGE_INPUT_DIGEST_MISMATCH,
                expected=identity.run_complete_sha256 or "<none recorded>",
                observed=f"{marker} could not be read: {type(exc).__name__}: {exc}",
                detail="the completion marker is the extraction run's manifest of itself; one "
                       "that cannot be read leaves every digest below it uncomputable"),
        )

    return (
        present,
        FreshnessCheck.comparing(
            "package_input_digest",
            RefusalCode.PACKAGE_INPUT_DIGEST_MISMATCH,
            expected=identity.run_complete_sha256 or "<none recorded>",
            observed=observed,
            detail=f"sha256({marker}); the extraction run id "
                   f"{identity.extraction_run_id!r} is *not* what identifies these bytes — one "
                   "id has named two different runs (§18), which is why this check hashes"),
        _contents_check(directory, recorded),
    )


def marker_contents(text: str) -> dict[str, str]:
    """`run.complete` parsed back into `{name: sha256}`, the inverse of `render_marker`.

    Parsed here rather than through `extraction.core.run_directory.RunDirectory` because that
    type reads catalogs and raises `IncompleteRunError`, and this stage needs a parse that can
    fail into a refusal. The format is three lines of code, and
    `tests/story/test_story_freshness.py` round-trips it against `render_marker` itself so the
    two cannot drift apart silently.

    A line that is not `{digest}  {name}` raises `ValueError`: the caller turns that into a
    refusal, because a marker this reader cannot parse is a marker whose claims it cannot
    check, which is not the same as one whose claims hold.
    """
    files: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        digest, separator, name = line.partition("  ")
        if not separator or not name.strip():
            raise ValueError(
                f"{line!r} is not a '{{sha256}}  {{name}}' line; see "
                "extraction/core/run_directory.py:render_marker for the format")
        files[name] = digest
    return files


def _contents_check(directory: Path, recorded: dict[str, str]) -> FreshnessCheck:
    """Every file `run.complete` lists, re-hashed against the digest it records.

    **Unlisted files are not a refusal, and that is a decision rather than an omission.** An
    extra file cannot change any digest the marker records, so no fact the graph quotes can
    have moved; every consumer of a run directory reads the catalogs it was told about by name.
    Refusing here would mean a stray editor swap file, a `.DS_Store` or an operator's copy of a
    catalog turning the gate that runs before *every* command into a hard stop over bytes
    nothing reads. They are named in `detail` instead, so a reader can still see them.

    Cost, measured 2026-08-03 over the whole of `extraction_input_checks` on the real run
    (`extract-v1-lexical-833f7bcfbce9`, ten files, 33 MB): **113–136 ms** with the repository on
    `/mnt/c` (the WSL drvfs mount, which is what makes it that slow) and **9–22 ms** for the
    same bytes on a native Linux filesystem. Both are affordable in a gate §7 says runs before
    every command — F12 already measured a dead server costing 35 s through `execute_query`, so
    a tenth of a second of hashing is not what makes this gate skippable. It therefore hashes
    all ten files rather than sampling; sampling would leave the defect this check exists to
    close open for whichever file was not drawn.

    `expected` is the empty list of differences and `observed` is the differences found, so the
    verdict is a comparison of real values rather than a string built to agree with itself. A
    healthy row therefore renders `<empty>`; the count of files verified is in `detail`.
    """
    differences: list[str] = []
    for name in sorted(recorded):
        if Path(name).is_absolute() or ".." in Path(name).parts:
            # The writer takes names, not paths (`RunDirectoryWriter.write`), so a marker
            # naming something outside its own directory is a marker to disbelieve — and this
            # check must not be the thing that reaches out and hashes it.
            differences.append(f"{name}: not a name inside the run directory")
            continue
        path = directory / name
        if not path.is_file():
            differences.append(f"{name}: listed as {recorded[name]} but the file is absent")
            continue
        try:
            actual = file_digest(path)
        except OSError as exc:
            differences.append(
                f"{name}: listed as {recorded[name]} but could not be read: "
                f"{type(exc).__name__}: {exc}")
            continue
        if actual != recorded[name]:
            differences.append(f"{name}: {COMPLETION_MARKER} records {recorded[name]}, "
                               f"the file holds {actual}")

    try:
        unlisted = sorted(
            entry.name for entry in directory.iterdir()
            if entry.is_file() and entry.name != COMPLETION_MARKER and entry.name not in recorded)
    except OSError:
        # A directory listing that fails cannot change any verdict above — every listed file
        # was reached by name — and this stage does not raise (D2), so the note is dropped.
        unlisted = []
    detail = (f"{len(recorded)} files listed by {COMPLETION_MARKER} under {directory}, each "
              "re-hashed; the marker is a manifest of the run, so verifying only the marker's "
              "own bytes verifies nothing it lists")
    if unlisted:
        detail += f"; not listed and not checked: {', '.join(unlisted)}"
    return FreshnessCheck.comparing(
        "extraction_run_contents",
        RefusalCode.PACKAGE_INPUT_DIGEST_MISMATCH,
        expected=(),
        observed=tuple(differences),
        detail=detail)


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
    "marker_contents",
]
