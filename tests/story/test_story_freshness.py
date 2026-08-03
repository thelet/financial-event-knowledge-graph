"""§7's staleness gate, offline against synthetic fixtures and live against the loaded run.

Two halves, and the split is the point. Everything that decides a refusal is a pure comparison
over two values, so every one of §7's codes is fired here by a tampered manifest under
`tmp_path` and a scripted executor — no database, no `data/`, no network. The `neo4j`-marked
half then asserts the same gate passes against the real graph, which is the only way to know
the offline fixtures describe the thing they claim to.

**Nothing under `data/` is modified.** Every stale fixture is built by copying
`tests/story/fixtures/graph_run_manifest.json` into `tmp_path` and changing one field. The
fixture is the real manifest of `graph-v1-0483dc6b4b10`, committed verbatim so the offline
tests exercise the real shape rather than a shape this file invented; a `neo4j`-marked test
asserts it is still byte-identical to the one on disk, so it cannot drift silently.

**The test this whole step exists for** is
`test_the_digest_check_catches_a_regenerated_run_that_the_id_check_would_have_passed`. It
builds an extraction directory whose id matches the graph manifest and whose bytes do not, runs
an explicit id-only gate over it to show that gate returning clean, and then runs the real one.
The claim that §7 must be digest-based is executable rather than asserted in a comment.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pytest

from graph.core.manifest import GraphRunManifest
from ontology import load_ontology

from story.core.graph_identity import (
    GRAPH_MANIFEST_FILENAME,
    GraphIdentity,
    GraphIdentityError,
    GraphRunManifestDocument,
    read_graph_identity,
)
from story.core.models import HealthStatus
from story.stages.freshness import (
    FRESHNESS_STATEMENTS,
    FRESHNESS_TIMEOUT_SECONDS,
    FreshnessCheck,
    FreshnessReport,
    LoadedGraph,
    LoadMarkerRow,
    RefusalCode,
    check_freshness,
    loaded_graph_checks,
)
from story.stages.freshness import freshness_report as freshness_report_module
from story.stages.freshness import gate as gate_module
from story.stages.freshness import loaded_graph as loaded_graph_module

from conftest import RecordedReadExecutor

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GRAPH_MANIFEST_FIXTURE = FIXTURES / "graph_run_manifest.json"

#: The completion marker of an extraction run, re-derived here rather than imported so a test
#: that stages a fake run does not depend on the constant it is checking against.
RUN_COMPLETE = "run.complete"

FIXTURE = json.loads(GRAPH_MANIFEST_FIXTURE.read_text(encoding="utf-8"))
GRAPH_RUN_ID = FIXTURE["graph_run_id"]
EXTRACTION_RUN_ID = FIXTURE["extraction_run_id"]
NODE_COUNT = FIXTURE["counts"]["nodes"]
EDGE_COUNT = FIXTURE["counts"]["edges"]
OBSERVATION_COUNT = FIXTURE["counts"]["nodes_by_label"]["Observation"]
ONTOLOGY_ID = FIXTURE["ontology_id"]
ONTOLOGY_HASH = FIXTURE["ontology_definition_hash"]

#: Arbitrary bytes standing in for a real `run.complete`. Its *content* is irrelevant to every
#: test here — what matters is that two different byte strings hash differently, which is the
#: only property §7 check 1 rests on.
FIRST_RUN_BYTES = b"6ff6  claims.jsonl\n1a2b  observations.jsonl\n"
REGENERATED_RUN_BYTES = b"6ff6  claims.jsonl\n9c9c  observations.jsonl\n"


# -- staging a graph run and an extraction run under tmp_path -----------------------------------


@dataclass(frozen=True)
class StagedRun:
    """A repository-shaped `tmp_path`: one graph run, one extraction run, nothing else."""

    root: Path
    graph_runs_root: Path
    extraction_directory: Path
    manifest_path: Path
    run_complete_sha256: str


def stage_run(
    tmp_path: Path,
    *,
    run_complete: bytes | None = FIRST_RUN_BYTES,
    recorded_digest: str | None = None,
    mutate: Callable[[dict[str, Any]], None] | None = None,
    extraction_manifest: dict[str, Any] | None = None,
) -> StagedRun:
    """The real manifest, re-pointed at a synthetic extraction run, then optionally tampered.

    `recorded_digest` defaults to the true digest of `run_complete`, so the staged run is
    *fresh* unless a test says otherwise. Every staleness fixture below is one deviation from
    this baseline, which is what makes each test's subject a single line.
    """
    graph_runs_root = tmp_path / "data" / "graph_runs"
    directory = graph_runs_root / GRAPH_RUN_ID
    directory.mkdir(parents=True)

    relative = f"data/extraction_runs/{EXTRACTION_RUN_ID}"
    extraction_directory = tmp_path / relative
    digest = ""
    if run_complete is not None:
        extraction_directory.mkdir(parents=True)
        (extraction_directory / RUN_COMPLETE).write_bytes(run_complete)
        digest = hashlib.sha256(run_complete).hexdigest()
    if extraction_manifest is not None:
        extraction_directory.mkdir(parents=True, exist_ok=True)
        (extraction_directory / "manifest.json").write_text(
            json.dumps(extraction_manifest), encoding="utf-8")

    manifest = json.loads(GRAPH_MANIFEST_FIXTURE.read_text(encoding="utf-8"))
    manifest["inputs"]["extraction_run_directory"] = relative
    manifest["inputs"]["run_complete_sha256"] = (
        digest if recorded_digest is None else recorded_digest)
    if mutate is not None:
        mutate(manifest)
    manifest_path = directory / GRAPH_MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return StagedRun(
        root=tmp_path,
        graph_runs_root=graph_runs_root,
        extraction_directory=extraction_directory,
        manifest_path=manifest_path,
        run_complete_sha256=digest,
    )


def scripted_executor(
    *,
    markers: tuple[dict[str, Any], ...] | None = None,
    node_count: int = NODE_COUNT,
    edge_count: int = EDGE_COUNT,
    observation_count: int = OBSERVATION_COUNT,
    node_run_ids: tuple[str, ...] = (GRAPH_RUN_ID,),
    edge_run_ids: tuple[str, ...] = (GRAPH_RUN_ID,),
    observation_run_ids: tuple[str, ...] = (GRAPH_RUN_ID,),
    ontology_hashes: tuple[str, ...] = (ONTOLOGY_HASH,),
    health: HealthStatus | None = None,
) -> RecordedReadExecutor:
    """A database that answers §7's four statements. Keyed by exact statement text.

    `RecordedReadExecutor` comes from `tests/story/conftest.py` unchanged — S0c put it there
    precisely so S0b and S1 would not each grow one, and nothing here needed it widened. It is
    imported by name as well as used as a fixture because these tests build several executors
    per test with different scripts.
    """
    default_marker = {
        "graph_run_id": GRAPH_RUN_ID, "status": "complete",
        "node_count": NODE_COUNT, "edge_count": EDGE_COUNT,
        "completed_at": "2026-08-03T16:37:44+00:00",
    }
    return RecordedReadExecutor(
        rows_by_statement={
            loaded_graph_module.LOAD_MARKERS:
                (default_marker,) if markers is None else markers,
            loaded_graph_module.DATA_NODE_INVENTORY: ({
                "node_count": node_count,
                "graph_run_ids": list(node_run_ids),
                "ontology_definition_hashes": list(ontology_hashes),
            },),
            loaded_graph_module.EDGE_INVENTORY: ({
                "edge_count": edge_count, "graph_run_ids": list(edge_run_ids),
            },),
            loaded_graph_module.OBSERVATION_INVENTORY: ({
                "observation_count": observation_count,
                "graph_run_ids": list(observation_run_ids),
            },),
        },
        health=health or HealthStatus(ok=True, status="ok", detail="recorded"),
    )


def run_gate(staged: StagedRun, executor: RecordedReadExecutor, **overrides: Any):
    return check_freshness(
        executor=executor,
        graph_run_id=GRAPH_RUN_ID,
        graph_runs_root=staged.graph_runs_root,
        root=staged.root,
        **{"ontology_definition_hash": ONTOLOGY_HASH, **overrides},
    )


# -- the identity reader ------------------------------------------------------------------------


def test_the_identity_reader_declares_only_fields_the_graph_layer_actually_writes():
    """The duplication `story/core/graph_identity.py` takes on, checked rather than trusted.

    `GraphRunManifest` is a frozen dataclass with no reader, so the story layer restates the
    field names it needs. A rename upstream would otherwise surface as a `GraphIdentityError`
    on a perfectly fresh graph — the gate refusing because it could not parse, reported as
    staleness.
    """
    written = {field.name for field in dataclasses.fields(GraphRunManifest)}
    declared = set(GraphRunManifestDocument.model_fields)

    assert declared <= written, sorted(declared - written)
    assert {"graph_run_id", "counts", "inputs", "ontology_definition_hash"} <= declared


def test_the_committed_graph_manifest_fixture_parses_into_the_identity_block_the_plan_pins():
    """§13.13's identity block, re-pinned after the F0 rebase, read off the real manifest."""
    identity = GraphIdentity.from_document(GraphRunManifestDocument.model_validate(FIXTURE))

    assert identity.graph_run_id == "graph-v1-0483dc6b4b10"
    assert identity.extraction_run_id == "extract-v1-lexical-833f7bcfbce9"
    assert identity.run_complete_sha256 == (
        "1cc8f7b01c0405311e70f5306635809c73685ed8e079cb35b92a7c36b8179d8e")
    assert identity.ontology_version == "2.0.0"
    assert identity.ontology_definition_hash == (
        "bb94f522ba1224702289d8e0646f5fdd8fc6d31cd341f604a7f879ee87e1af34")
    assert identity.graph_projection_version == "1.2.0"
    assert (identity.node_count, identity.edge_count) == (28836, 35600)


def test_a_manifest_that_grows_a_field_this_reader_never_saw_is_still_readable(tmp_path):
    """Allow-subset, deliberately: the projection may record a new block, and refusing to read
    a fresh graph over a diagnostic key would be a false alarm dressed as staleness."""
    staged = stage_run(tmp_path, mutate=lambda m: m.update(
        {"embedding_reconciliation": {"agreed": True}, "counts_v2": {"nodes": 1}}))

    identity = read_graph_identity(staged.graph_runs_root, GRAPH_RUN_ID)

    assert identity.node_count == NODE_COUNT


def test_a_manifest_missing_the_block_the_gate_reads_is_a_typed_refusal(tmp_path):
    """A declared field that disappeared upstream still fails — ignoring unknown keys is not
    the same as accepting unknown data."""
    staged = stage_run(tmp_path, mutate=lambda m: m["inputs"].pop("run_complete_sha256"))

    with pytest.raises(GraphIdentityError) as raised:
        read_graph_identity(staged.graph_runs_root, GRAPH_RUN_ID)

    assert "run_complete_sha256" in str(raised.value)


def test_a_graph_run_directory_that_does_not_exist_raises_the_typed_identity_error(tmp_path):
    with pytest.raises(GraphIdentityError) as raised:
        read_graph_identity(tmp_path / "data" / "graph_runs", "graph-v1-neverprojected")

    assert "graph-v1-neverprojected" in str(raised.value)


def test_a_graph_run_directory_with_no_manifest_is_an_unfinished_projection(tmp_path):
    """The manifest is written last, so its absence is unambiguous."""
    (tmp_path / GRAPH_RUN_ID).mkdir()

    with pytest.raises(GraphIdentityError) as raised:
        read_graph_identity(tmp_path, GRAPH_RUN_ID)

    assert GRAPH_MANIFEST_FILENAME in str(raised.value)


def test_a_directory_whose_manifest_names_another_run_identifies_nothing(tmp_path):
    staged = stage_run(tmp_path, mutate=lambda m: m.update(graph_run_id="graph-v1-somethingelse"))

    with pytest.raises(GraphIdentityError) as raised:
        read_graph_identity(staged.graph_runs_root, GRAPH_RUN_ID)

    assert "graph-v1-somethingelse" in str(raised.value)


def test_a_repo_relative_extraction_directory_resolves_against_the_root_it_is_given(tmp_path):
    identity = GraphIdentity.from_document(GraphRunManifestDocument.model_validate(FIXTURE))

    assert identity.extraction_run_path(tmp_path) == (
        tmp_path / "data" / "extraction_runs" / EXTRACTION_RUN_ID)


def test_an_absolute_extraction_directory_wins_over_the_root(tmp_path):
    """A projection run against a directory outside the repository records an absolute path."""
    document = json.loads(GRAPH_MANIFEST_FIXTURE.read_text(encoding="utf-8"))
    document["inputs"]["extraction_run_directory"] = str(tmp_path / "elsewhere")
    identity = GraphIdentity.from_document(GraphRunManifestDocument.model_validate(document))

    assert identity.extraction_run_path(Path("/ignored")) == tmp_path / "elsewhere"


# -- the gate, passing ---------------------------------------------------------------------------


def test_a_matching_graph_passes_every_check_and_the_report_says_which(tmp_path):
    staged = stage_run(tmp_path)

    report = run_gate(staged, scripted_executor())

    assert report.passed is True
    assert report.refusals == ()
    assert report.refusal_codes == ()
    assert {check.name for check in report.checks} == {
        "graph_manifest", "extraction_run_directory", "package_input_digest", "graph_reachable",
        "load_marker_present", "load_status_complete", "load_marker_graph_run_id",
        "node_graph_run_id", "edge_graph_run_id", "observation_graph_run_id",
        "load_marker_counts", "loaded_element_counts", "node_ontology_definition_hash"}
    assert report.check("package_input_digest").observed == staged.run_complete_sha256


def test_the_gate_compares_against_the_ontology_this_checkout_loads_when_none_is_supplied(
        tmp_path):
    """The default path, and the reason §7's third check is worth running at all: the manifest
    agreeing with itself proves nothing, so the comparison is against the ontology package as
    it is *now*."""
    staged = stage_run(tmp_path)
    loaded_now = load_ontology(ONTOLOGY_ID).definition_hash

    report = check_freshness(
        executor=scripted_executor(ontology_hashes=(loaded_now,)),
        graph_run_id=GRAPH_RUN_ID, graph_runs_root=staged.graph_runs_root, root=staged.root)

    assert report.check("node_ontology_definition_hash").passed is True
    assert report.check("node_ontology_definition_hash").expected == loaded_now


# -- the check that matters: digests, not ids -----------------------------------------------------


def id_only_gate(identity: GraphIdentity, root: Path) -> list[str]:
    """The gate §7 rejected, written out so the rejection is a measurement.

    An id-based check has exactly two things to compare: that a directory named by the recorded
    `extraction_run_id` exists, and that its manifest agrees about its own `run_id`. Both hold
    across a run regenerated in place, which is why S0b hashes instead.
    """
    directory = identity.extraction_run_path(root)
    refusals = []
    if not directory.is_dir():
        refusals.append("extraction_run_directory_missing")
    else:
        run_id = json.loads((directory / "manifest.json").read_text())["run_id"]
        if run_id != identity.extraction_run_id:
            refusals.append("extraction_run_id_mismatch")
    return refusals


def test_the_digest_check_catches_a_regenerated_run_that_the_id_check_would_have_passed(tmp_path):
    """§7's whole reason to exist, as a fixture rather than as a paragraph.

    The extraction run was regenerated in place: same directory, same `run_id` in its own
    manifest, different bytes in `run.complete`. Nothing about its *identity* moved, and the
    graph on top of it is now describing rows that no longer exist.
    """
    staged = stage_run(
        tmp_path,
        run_complete=REGENERATED_RUN_BYTES,
        recorded_digest=hashlib.sha256(FIRST_RUN_BYTES).hexdigest(),
        extraction_manifest={"run_id": EXTRACTION_RUN_ID},
    )
    identity = read_graph_identity(staged.graph_runs_root, GRAPH_RUN_ID)

    assert id_only_gate(identity, staged.root) == [], (
        "the id-based check must pass here, or this fixture does not demonstrate anything")

    report = run_gate(staged, scripted_executor())

    assert report.passed is False
    assert report.refusal_codes == (RefusalCode.PACKAGE_INPUT_DIGEST_MISMATCH,)
    refusal = report.check("package_input_digest")
    assert refusal.expected == hashlib.sha256(FIRST_RUN_BYTES).hexdigest()
    assert refusal.observed == hashlib.sha256(REGENERATED_RUN_BYTES).hexdigest()
    assert refusal.expected != refusal.observed


# -- the gate, refusing -----------------------------------------------------------------------


def test_a_missing_extraction_directory_refuses_instead_of_raising_file_not_found(tmp_path):
    staged = stage_run(tmp_path, run_complete=None,
                       recorded_digest=hashlib.sha256(FIRST_RUN_BYTES).hexdigest())

    report = run_gate(staged, scripted_executor())

    assert report.passed is False
    assert RefusalCode.EXTRACTION_RUN_DIRECTORY_MISSING in report.refusal_codes
    assert str(staged.extraction_directory) in report.check("extraction_run_directory").observed
    with pytest.raises(KeyError):
        report.check("package_input_digest")


def test_an_extraction_directory_without_a_completion_marker_refuses_on_the_digest(tmp_path):
    """A run directory with no `run.complete` is an unfinished run, not the run this graph was
    projected from — and there is nothing to hash, so the digest check refuses rather than
    comparing against an invented value."""
    staged = stage_run(tmp_path, run_complete=None,
                       recorded_digest=hashlib.sha256(FIRST_RUN_BYTES).hexdigest())
    staged.extraction_directory.mkdir(parents=True)

    report = run_gate(staged, scripted_executor())

    assert report.check("extraction_run_directory").passed is True
    assert report.check("package_input_digest").passed is False
    assert report.refusal_codes == (RefusalCode.PACKAGE_INPUT_DIGEST_MISMATCH,)
    assert RUN_COMPLETE in report.check("package_input_digest").observed


def test_an_unreadable_graph_manifest_refuses_with_a_code_rather_than_raising(tmp_path):
    directory = tmp_path / "data" / "graph_runs" / GRAPH_RUN_ID
    directory.mkdir(parents=True)
    (directory / GRAPH_MANIFEST_FILENAME).write_text("{ not json", encoding="utf-8")

    report = check_freshness(
        executor=scripted_executor(), graph_run_id=GRAPH_RUN_ID,
        graph_runs_root=tmp_path / "data" / "graph_runs", root=tmp_path)

    assert report.passed is False
    assert report.refusal_codes == (RefusalCode.GRAPH_MANIFEST_UNREADABLE,)
    assert len(report.checks) == 1


def test_a_database_that_is_down_is_refused_before_any_statement_is_issued(tmp_path):
    """F12: `verify_connectivity()` fails in 0.0s against a refused port while `execute_query`
    retries a managed transaction for 35 seconds. The empty call log is what proves the gate
    asks the cheap question first — §7 runs before every command, and a thirty-five-second
    gate is one nobody runs."""
    staged = stage_run(tmp_path)
    executor = scripted_executor(
        health=HealthStatus(ok=False, status="unavailable", detail="bolt://127.0.0.1:1"))

    report = run_gate(staged, executor)

    assert executor.calls == []
    assert report.passed is False
    assert report.refusal_codes == (RefusalCode.GRAPH_UNREACHABLE,)
    assert report.check("package_input_digest").passed is True, (
        "the filesystem half of the gate must still answer when the database does not")


def test_a_database_with_no_load_marker_refuses_with_load_incomplete(tmp_path):
    staged = stage_run(tmp_path)

    report = run_gate(staged, scripted_executor(markers=()))

    assert RefusalCode.LOAD_INCOMPLETE in report.refusal_codes
    assert report.check("load_marker_present").observed == "0"


def test_a_marker_left_loading_refuses_with_load_incomplete(tmp_path):
    """The loader writes `status: complete` last and `REMOVE`s the counts until it does, so a
    marker still saying `loading` is a load that never finished."""
    staged = stage_run(tmp_path)
    executor = scripted_executor(markers=({
        "graph_run_id": GRAPH_RUN_ID, "status": "loading",
        "node_count": None, "edge_count": None, "completed_at": None},))

    report = run_gate(staged, executor)

    assert report.passed is False
    assert RefusalCode.LOAD_INCOMPLETE in report.refusal_codes
    assert report.check("load_status_complete").observed == "loading"
    assert report.check("load_marker_counts").observed == "edges=<absent> nodes=<absent>", (
        "an absent count must not be reported as zero — Neo4j stores no null, so a 0 here "
        "would be a measurement nobody took")


def test_a_marker_naming_another_graph_run_refuses_with_graph_run_id_mismatch(tmp_path):
    staged = stage_run(tmp_path)
    executor = scripted_executor(markers=({
        "graph_run_id": "graph-v1-886059d862ce", "status": "complete",
        "node_count": NODE_COUNT, "edge_count": EDGE_COUNT, "completed_at": "x"},))

    report = run_gate(staged, executor)

    assert report.passed is False
    assert RefusalCode.GRAPH_RUN_ID_MISMATCH in report.refusal_codes
    assert report.check("load_marker_graph_run_id").observed == "graph-v1-886059d862ce"


def test_observations_carrying_a_superseded_run_id_refuse(tmp_path):
    """§17.8's failure, at the node that carries it: an `:Observation` from a superseded run is
    the fact that ends up quoted in a published post."""
    staged = stage_run(tmp_path)

    report = run_gate(staged, scripted_executor(
        observation_run_ids=("graph-v1-886059d862ce",)))

    assert report.passed is False
    assert RefusalCode.GRAPH_RUN_ID_MISMATCH in report.refusal_codes
    assert report.check("observation_graph_run_id").passed is False
    assert report.check("node_graph_run_id").passed is True


def test_a_database_holding_two_runs_at_once_refuses(tmp_path):
    staged = stage_run(tmp_path)

    report = run_gate(staged, scripted_executor(
        node_run_ids=(GRAPH_RUN_ID, "graph-v1-886059d862ce")))

    assert RefusalCode.GRAPH_RUN_ID_MISMATCH in report.refusal_codes
    assert "graph-v1-886059d862ce" in report.check("node_graph_run_id").observed


def test_a_marker_whose_counts_disagree_with_the_export_refuses_with_count_mismatch(tmp_path):
    staged = stage_run(tmp_path)
    executor = scripted_executor(markers=({
        "graph_run_id": GRAPH_RUN_ID, "status": "complete",
        "node_count": NODE_COUNT - 4, "edge_count": EDGE_COUNT, "completed_at": "x"},))

    report = run_gate(staged, executor)

    assert report.refusal_codes == (RefusalCode.COUNT_MISMATCH,)
    assert report.check("load_marker_counts").expected == f"edges={EDGE_COUNT} nodes={NODE_COUNT}"
    assert report.check("loaded_element_counts").passed is True


def test_a_database_that_lost_nodes_since_the_load_refuses_with_count_mismatch(tmp_path):
    """The marker records what the loader believed it wrote; this counts what is there now."""
    staged = stage_run(tmp_path)

    report = run_gate(staged, scripted_executor(node_count=NODE_COUNT - 1))

    assert report.refusal_codes == (RefusalCode.COUNT_MISMATCH,)
    assert report.check("load_marker_counts").passed is True
    assert report.check("loaded_element_counts").passed is False


def test_nodes_projected_under_another_ontology_refuse_with_ontology_hash_mismatch(tmp_path):
    staged = stage_run(tmp_path)

    report = run_gate(staged, scripted_executor(ontology_hashes=("e8d4af70" + "0" * 56,)))

    assert report.passed is False
    assert report.refusal_codes == (RefusalCode.ONTOLOGY_HASH_MISMATCH,)


def test_an_ontology_this_checkout_cannot_load_refuses_rather_than_raising(tmp_path):
    """The graph names a vocabulary this checkout cannot produce, so no comparison is possible
    and the facts cannot be interpreted — a refusal, not a traceback."""
    staged = stage_run(tmp_path, mutate=lambda m: m.update(ontology_id="withdrawn_v9"))

    report = check_freshness(
        executor=scripted_executor(), graph_run_id=GRAPH_RUN_ID,
        graph_runs_root=staged.graph_runs_root, root=staged.root)

    assert report.refusal_codes == (RefusalCode.ONTOLOGY_HASH_MISMATCH,)
    assert "withdrawn_v9" in report.check("node_ontology_definition_hash").expected


def test_every_refusal_code_the_step_promises_can_actually_fire():
    """A guard on the tests above: a code no fixture reaches is a refusal nobody has seen."""
    fired = {
        RefusalCode.GRAPH_MANIFEST_UNREADABLE, RefusalCode.EXTRACTION_RUN_DIRECTORY_MISSING,
        RefusalCode.PACKAGE_INPUT_DIGEST_MISMATCH, RefusalCode.GRAPH_UNREACHABLE,
        RefusalCode.LOAD_INCOMPLETE, RefusalCode.GRAPH_RUN_ID_MISMATCH,
        RefusalCode.COUNT_MISMATCH, RefusalCode.ONTOLOGY_HASH_MISMATCH,
    }
    assert fired == set(RefusalCode)


# -- the marker, and the count it would otherwise break -------------------------------------------


def test_the_load_marker_is_excluded_from_the_live_count_so_a_correct_graph_passes():
    """The 28,837-against-28,836 trap, asserted at the statement that avoids it.

    The loader writes one `(:GraphLoad)` node of its own and excludes it from the counts it
    records, so the database holds exactly one more node than the export wrote. A live count
    without `WHERE NOT n:GraphLoad` would refuse the current, correct run every time it ran.
    """
    assert "WHERE NOT n:GraphLoad" in loaded_graph_module.DATA_NODE_INVENTORY

    identity = GraphIdentity.from_document(GraphRunManifestDocument.model_validate(FIXTURE))
    loaded = LoadedGraph(
        markers=(LoadMarkerRow(graph_run_id=GRAPH_RUN_ID, status="complete",
                               node_count=NODE_COUNT, edge_count=EDGE_COUNT),),
        node_count=NODE_COUNT, edge_count=EDGE_COUNT, observation_count=OBSERVATION_COUNT,
        node_graph_run_ids=(GRAPH_RUN_ID,), edge_graph_run_ids=(GRAPH_RUN_ID,),
        observation_graph_run_ids=(GRAPH_RUN_ID,), ontology_definition_hashes=(ONTOLOGY_HASH,))

    checks = loaded_graph_checks(identity, loaded, ontology_definition_hash=ONTOLOGY_HASH)

    assert all(check.passed for check in checks), [c.describe() for c in checks if not c.passed]


def test_counting_the_marker_as_data_would_refuse_the_correct_run():
    """The false positive, made visible: the same graph, counted with the marker included."""
    identity = GraphIdentity.from_document(GraphRunManifestDocument.model_validate(FIXTURE))
    with_marker = LoadedGraph(
        markers=(LoadMarkerRow(graph_run_id=GRAPH_RUN_ID, status="complete",
                               node_count=NODE_COUNT, edge_count=EDGE_COUNT),),
        node_count=NODE_COUNT + 1, edge_count=EDGE_COUNT, observation_count=OBSERVATION_COUNT,
        node_graph_run_ids=(GRAPH_RUN_ID,), edge_graph_run_ids=(GRAPH_RUN_ID,),
        observation_graph_run_ids=(GRAPH_RUN_ID,), ontology_definition_hashes=(ONTOLOGY_HASH,))

    checks = loaded_graph_checks(identity, with_marker, ontology_definition_hash=ONTOLOGY_HASH)
    failed = [check for check in checks if not check.passed]

    assert [check.name for check in failed] == ["loaded_element_counts"]
    assert failed[0].code is RefusalCode.COUNT_MISMATCH


# -- read-only, bounded, and nothing else ----------------------------------------------------------


#: Every clause that changes the database, plus the two procedure families that can. Matched
#: word-boundary against the upper-cased statement, so `graph_run_id` cannot look like a `SET`.
WRITE_KEYWORDS = ("CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP", "FOREACH",
                  "LOAD CSV", "APOC", "IMPORT")

STAGE_MODULES = (freshness_report_module, gate_module, loaded_graph_module)


def cypher_in(module) -> dict[str, str]:
    """Every module-level string that reads like a statement, by attribute name."""
    return {
        name: value for name, value in vars(module).items()
        if isinstance(value, str) and not name.startswith("__")
        and ("MATCH" in value or "RETURN" in value or "CALL" in value)
    }


def test_no_statement_the_freshness_stage_owns_contains_a_write_keyword():
    """§16 and WORKSTREAM_BOUNDARY §3: the gate reads and never writes.

    Scanned over the assembled constants rather than the source, because two of them are built
    by f-string interpolation of the label — a source grep would read the fragments and miss
    the statement.
    """
    offences = []
    for module in STAGE_MODULES:
        for name, statement in cypher_in(module).items():
            upper = statement.upper()
            for keyword in WRITE_KEYWORDS:
                if re.search(rf"\b{keyword}\b", upper):
                    offences.append(f"{module.__name__}.{name} contains {keyword}: {statement}")
    assert offences == []


def test_the_stage_declares_every_statement_it_holds():
    """`FRESHNESS_STATEMENTS` is what a test asserts the gate issued, so it must be complete —
    a fifth statement added beside it would be invisible to every check above."""
    found = {statement for module in STAGE_MODULES for statement in cypher_in(module).values()}

    assert found == set(FRESHNESS_STATEMENTS)
    assert len(FRESHNESS_STATEMENTS) == 4


def string_constants(path: Path) -> list[tuple[int, str]]:
    """Every string literal that is not a docstring, with its line.

    Docstrings are excluded because this stage's own modules discuss `CREATE`, `MERGE`, `SET`
    and `DELETE` by name in explaining why they are absent, and a scan that could not tell
    prose from a statement would push the explanation out of the code.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_no_cypher_fragment_in_the_statement_module_names_a_write_clause():
    """The source-level half: a write keyword hidden in a fragment concatenated later.

    Scoped to `loaded_graph.py` because that is the only module allowed to hold Cypher at all —
    which the test below is what makes true. Scanning the other two for keywords would flag
    ordinary English (`"the load that would have set this"` upper-cases to a `SET`), and a
    structural test that punishes explanatory prose gets deleted rather than fixed.
    """
    offences = [
        f"loaded_graph.py:{line} {keyword}: {text!r}"
        for line, text in string_constants(Path(loaded_graph_module.__file__))
        for keyword in WRITE_KEYWORDS
        if re.search(rf"\b{keyword}\b", text.upper())
    ]
    assert offences == []


@pytest.mark.parametrize("module", [freshness_report_module, gate_module],
                         ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_only_the_statement_module_holds_cypher_at_all(module):
    """The judgment, made executable: the write-keyword scan has exactly one file to read.

    A `MATCH` appearing in `gate.py` would put a statement outside the module the scan covers,
    which is how a stage acquires an unreviewed query.
    """
    offences = [
        f"{module.__name__}:{line} {text!r}"
        for line, text in string_constants(Path(module.__file__))
        if any(re.search(rf"\b{clause}\b", text.upper())
               for clause in ("MATCH", "RETURN", "CALL", "WHERE"))
    ]
    assert offences == []


def test_the_gate_issues_only_the_four_read_statements_the_stage_declares(tmp_path):
    staged = stage_run(tmp_path)
    executor = scripted_executor()

    run_gate(staged, executor)

    assert [call.statement for call in executor.calls] == list(FRESHNESS_STATEMENTS)


def test_every_statement_the_gate_issues_carries_a_positive_timeout(tmp_path):
    """§16 requires one on every statement, and F10 measured that `0` means *no timeout* rather
    than *expire immediately* — so `read()` refuses a non-positive value and the gate must pass
    a real one."""
    staged = stage_run(tmp_path)
    executor = scripted_executor()

    run_gate(staged, executor)

    assert executor.calls
    assert all(call.timeout_seconds == FRESHNESS_TIMEOUT_SECONDS for call in executor.calls)
    assert FRESHNESS_TIMEOUT_SECONDS > 0


def test_a_caller_supplied_timeout_reaches_every_statement(tmp_path):
    staged = stage_run(tmp_path)
    executor = scripted_executor()

    run_gate(staged, executor, timeout_seconds=2.5)

    assert {call.timeout_seconds for call in executor.calls} == {2.5}


# -- the report is a value ---------------------------------------------------------------------


def failing_check(name: str = "x") -> FreshnessCheck:
    return FreshnessCheck.comparing(
        name, RefusalCode.COUNT_MISMATCH, expected=1, observed=2)


def test_a_report_holding_a_refusal_cannot_claim_it_passed():
    """`passed` is derived, never stored — §7 says there is no flag that skips this gate, so
    "passed while holding a refusal" must not be a representable state."""
    report = FreshnessReport(graph_run_id=GRAPH_RUN_ID, checks=(failing_check(),))

    assert report.passed is False
    assert "passed" not in FreshnessReport.model_fields


def test_a_report_that_ran_no_check_has_not_passed():
    """An empty `all()` is `True`, which is the one way a gate that examined nothing could
    authorise a story run."""
    assert FreshnessReport(graph_run_id=GRAPH_RUN_ID).passed is False


def test_refusal_codes_are_reported_once_each_in_the_order_the_checks_ran():
    """Ordered by occurrence because the first refusal is usually the cause: a graph loaded
    from a superseded export fails the digest check and every count below it."""
    report = FreshnessReport(graph_run_id=GRAPH_RUN_ID, checks=(
        FreshnessCheck.comparing("a", RefusalCode.PACKAGE_INPUT_DIGEST_MISMATCH,
                                 expected="x", observed="y"),
        failing_check("b"),
        failing_check("c"),
    ))

    assert report.refusal_codes == (
        RefusalCode.PACKAGE_INPUT_DIGEST_MISMATCH, RefusalCode.COUNT_MISMATCH)


def test_asking_a_report_for_a_check_that_never_ran_raises(tmp_path):
    report = FreshnessReport(graph_run_id=GRAPH_RUN_ID, checks=(failing_check(),))

    with pytest.raises(KeyError):
        report.check("never_ran")


def test_a_refusal_names_what_it_expected_and_what_it_observed():
    """Every check is actionable or it is noise: `describe()` must carry the code and both
    sides, so a failure can be dispatched without re-deriving it."""
    rendered = failing_check("load_marker_counts").describe()

    assert "REFUSE" in rendered
    assert "count_mismatch" in rendered
    assert "expected: 1" in rendered and "observed: 2" in rendered


def test_a_check_compares_values_and_not_their_rendered_forms():
    """`28836` and `"28836"` print identically and are not the same measurement."""
    check = FreshnessCheck.comparing("n", RefusalCode.COUNT_MISMATCH, expected=28836,
                                     observed="28836")

    assert check.passed is False
    assert check.expected == check.observed


# -- live, against the loaded run ------------------------------------------------------------------


@pytest.fixture
def live_executor():
    """The real adapter against the local instance, skipped when nothing is running."""
    from story.providers.neo4j_connection import open_read_executor

    executor = open_read_executor()
    health = executor.verify_connectivity()
    if not health.ok:
        executor.close()
        pytest.skip(f"local Neo4j is unreachable ({health.status}: {health.detail}); "
                    "start it with `docker compose up -d`")
    with executor:
        yield executor


@pytest.mark.neo4j
def test_the_loaded_run_passes_every_freshness_check_against_the_real_graph(live_executor):
    """All three of §7's checks, against the real manifest, the real extraction directory and
    the real database. The offline fixtures describe a shape; this is the shape."""
    report = check_freshness(
        executor=live_executor,
        graph_run_id=GRAPH_RUN_ID,
        graph_runs_root=REPO_ROOT / "data" / "graph_runs",
        root=REPO_ROOT)

    assert report.passed is True, report.describe()
    assert len(report.checks) == 13
    assert report.check("package_input_digest").observed == (
        "1cc8f7b01c0405311e70f5306635809c73685ed8e079cb35b92a7c36b8179d8e")


@pytest.mark.neo4j
def test_the_live_database_holds_one_more_node_than_the_export_and_the_gate_still_passes(
        live_executor):
    """The marker, measured rather than assumed — the false positive this stage had to avoid."""
    every_node = live_executor.read(
        "MATCH (n) RETURN count(n) AS total", {}, timeout_seconds=FRESHNESS_TIMEOUT_SECONDS)
    data_nodes = live_executor.read(
        loaded_graph_module.DATA_NODE_INVENTORY, {},
        timeout_seconds=FRESHNESS_TIMEOUT_SECONDS)

    assert every_node[0]["total"] == data_nodes[0]["node_count"] + 1
    assert data_nodes[0]["node_count"] == NODE_COUNT


@pytest.mark.neo4j
def test_the_marker_the_loader_wrote_carries_the_property_names_this_stage_reads(live_executor):
    """`LOAD_MARKER_LABEL`, `status`, `node_count` and `edge_count` are re-stated from
    `graph/stages/load/lifecycle.py`, which this package may not import (§4). This is what
    keeps the duplication from drifting: a rename upstream fails here."""
    rows = live_executor.read(
        loaded_graph_module.LOAD_MARKERS, {}, timeout_seconds=FRESHNESS_TIMEOUT_SECONDS)

    assert len(rows) == 1
    assert rows[0]["graph_run_id"] == GRAPH_RUN_ID
    assert rows[0]["status"] == gate_module.STATUS_COMPLETE
    assert (rows[0]["node_count"], rows[0]["edge_count"]) == (NODE_COUNT, EDGE_COUNT)


@pytest.mark.neo4j
def test_the_committed_manifest_fixture_is_still_the_manifest_of_the_loaded_run():
    """Committed verbatim so the offline tests exercise the real shape. Marked `neo4j` because
    that is this suite's spelling of "the loaded run is present on this machine" — the fixture
    is meaningless as a claim on a checkout that has no `data/`."""
    on_disk = REPO_ROOT / "data" / "graph_runs" / GRAPH_RUN_ID / GRAPH_MANIFEST_FILENAME

    assert json.loads(on_disk.read_text(encoding="utf-8")) == FIXTURE
