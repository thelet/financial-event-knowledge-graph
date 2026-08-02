"""The export stage's one promise: two projections of one run produce the same bytes.

V1_GRAPH_PROTOTYPE §4.4 states it and §6.5 says how to check it — project twice, compare
`sha256` of `nodes.jsonl` and `edges.jsonl`. So that is what these tests do, on the committed
fixture and, when the run is present, on the real one. Everything else here defends the same
property from a different direction: the run id must not contain a clock, the rows must not
contain a timestamp or a path this layer invented, the order must be the *declared* order
rather than whatever the builders emitted, and the manifest must be written last so a
half-written directory cannot be mistaken for a finished one.

Four refusals are tested by construction rather than by finding a broken run: a dangling
endpoint, a duplicate node key, a duplicate edge key and an empty key. The real corpus is
clean on all four today — 0 dangling, 0 duplicates — which is exactly why the checks need a
hand-built counterexample to prove they can fail at all.

**One measured surprise, recorded rather than smoothed over** *(verified 2026-08-02)*:
`nodes.jsonl` *does* contain absolute filesystem paths — 23 of them on the real run, 9 on the
fixture — all of them the value of `provider_model_id`, which `claims.jsonl` carries in
`extractor_metadata` and `nodes._promoted_metadata` promotes to a node property. They are the
local path of the GGUF model that answered the narrative and event prompts. The export
introduces none of its own, and the inherited ones are constant across runs so determinism is
untouched; `test_artifacts_hold_no_path_this_layer_invented` therefore asserts the precise
truth — the only absolute paths present came out of the extraction catalog — instead of the
tidier claim that would have been false.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIXTURE_RUN, REAL_CATALOG, REAL_RUN, REAL_RUN_AVAILABLE, REAL_RUN_REASON
from graph.cli import EXIT_FAILED, EXIT_OK, main
from graph.context import build_graph_context
from graph.core.inputs import GraphInputError
from graph.core.manifest import input_content_digest, make_graph_run_id
from graph.core.models import (
    EDGE_SORT_KEY,
    GRAPH_PROJECTION_VERSION,
    GraphEdge,
    GraphExport,
    GraphNode,
    NODE_SORT_KEY,
)
from graph.pipeline import graph_run_id_for, project
from graph.stages.projection.export import (
    EDGES_FILENAME,
    MANIFEST_FILENAME,
    NODES_FILENAME,
    REJECTED_FILENAME,
    REJECTED_SUFFIX,
    GraphRunWriter,
    DanglingEndpointError,
    DuplicateKeyError,
    EmptyNodeKeyError,
    WarningReconciliationError,
    check_export,
    reconcile_warnings,
    sort_key_order_holds,
)

requires_real_run = pytest.mark.skipif(not REAL_RUN_AVAILABLE, reason=REAL_RUN_REASON)

#: G0's answer to P11, and the number this stage must keep producing.
REAL_NODE_COUNT = 28_836
REAL_EDGE_COUNT = 35_603
REAL_WARNINGS = 186

#: A full ISO timestamp — `2026-08-02T16:02:44+00:00`. Deliberately not a bare date: an
#: `occurred_on` of `2022-10-19` is a filed fact and belongs in the export; a wall clock does
#: not (§4.4).
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

#: Absolute filesystem paths, in the three shapes this repository can produce.
ABSOLUTE_PATH = re.compile(r"(/home/|/tmp/|/mnt/|[A-Za-z]:\\)")

#: The one property that legitimately holds one — see the module docstring.
INHERITED_PATH_PROPERTY = "provider_model_id"


# -- helpers ------------------------------------------------------------------------------


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def key_order(value: object, path: str = "") -> list[str]:
    """Every mapping in the document whose keys are not in sorted order, named by path."""
    unsorted: list[str] = []
    if isinstance(value, dict):
        if list(value) != sorted(value):
            unsorted.append(path or "<root>")
        for key, item in value.items():
            unsorted.extend(key_order(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            unsorted.extend(key_order(item, f"{path}[{index}]"))
    return unsorted


def run_projection(context, run, root: Path, catalog: Path | None = None):
    """One projection into its own runs root, so two of them cannot share a directory."""
    return project(
        dataclasses.replace(context, graph_runs_root=root), run, catalog_directory=catalog)


@pytest.fixture(scope="session")
def graph_context():
    return build_graph_context()


@pytest.fixture(scope="session")
def fixture_pair(graph_context, tmp_path_factory):
    """Two independent projections of the committed fixture, into two roots."""
    return (
        run_projection(graph_context, FIXTURE_RUN, tmp_path_factory.mktemp("fixture_a")),
        run_projection(graph_context, FIXTURE_RUN, tmp_path_factory.mktemp("fixture_b")),
    )


@pytest.fixture(scope="session")
def real_pair(graph_context, tmp_path_factory):
    if not REAL_RUN_AVAILABLE:  # pragma: no cover - environment-dependent
        pytest.skip(REAL_RUN_REASON)
    return (
        run_projection(graph_context, REAL_RUN, tmp_path_factory.mktemp("real_a"),
                       REAL_CATALOG),
        run_projection(graph_context, REAL_RUN, tmp_path_factory.mktemp("real_b"),
                       REAL_CATALOG),
    )


@pytest.fixture
def copied_fixture(tmp_path) -> Path:
    """A writable copy of the fixture, for the two tests that must corrupt an input.

    A copy, never the fixture itself: `tests/fixtures/graph/README.md` says every line there
    is byte-identical to a real run and must not be hand-edited. Corrupting a copy proves the
    same thing and leaves the corpus intact.
    """
    destination = tmp_path / "corpus"
    shutil.copytree(FIXTURE_RUN.parent, destination)
    return destination / FIXTURE_RUN.name


# -- byte identity ------------------------------------------------------------------------


def test_two_projections_of_the_fixture_are_byte_identical(fixture_pair):
    first, second = fixture_pair
    for name in (NODES_FILENAME, EDGES_FILENAME):
        assert sha256(first.directory / name) == sha256(second.directory / name), name
        assert (first.directory / name).read_bytes() == (second.directory / name).read_bytes()


def test_the_manifest_records_the_digests_the_files_actually_have(fixture_pair):
    """The manifest's `artifacts` block is a claim about bytes; check it against the bytes."""
    outcome, _ = fixture_pair
    for name in (NODES_FILENAME, EDGES_FILENAME):
        assert outcome.manifest["artifacts"][name] == sha256(outcome.directory / name)


@requires_real_run
def test_two_projections_of_the_real_run_are_byte_identical(real_pair):
    first, second = real_pair
    for name in (NODES_FILENAME, EDGES_FILENAME):
        assert sha256(first.directory / name) == sha256(second.directory / name), name


# -- byte identity across two processes ------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]


def project_in_subprocess(run: Path, root: Path, *, hash_seed: str) -> Path:
    """One `python -m graph project` in its own interpreter, with `PYTHONHASHSEED` set.

    The seed is why this exists. Every determinism test above runs inside one pytest process,
    where `PYTHONHASHSEED` is whatever that process started with — identical for both halves
    of the comparison — so a builder that iterated a `set` into a property would produce the
    *same* wrong order twice and pass. Two processes with two seeds is the only arrangement
    that can see it. It is also what §6.5 literally describes: project twice, compare
    `sha256`.
    """
    environment = {**os.environ, "PYTHONHASHSEED": hash_seed}
    result = subprocess.run(
        [sys.executable, "-m", "graph", "--runs-root", str(root), "project", str(run)],
        cwd=str(REPO_ROOT), env=environment, capture_output=True, text=True, timeout=1800)
    assert result.returncode == 0, result.stdout + result.stderr
    directories = [p for p in root.iterdir() if (p / MANIFEST_FILENAME).is_file()]
    assert len(directories) == 1, directories
    return directories[0]


@pytest.mark.parametrize("run_name", ["fixture"])
def test_two_processes_with_different_hash_seeds_produce_identical_bytes(
    tmp_path, run_name
):
    """§4.4 across process boundaries, on the committed fixture — no `data/` needed."""
    first = project_in_subprocess(FIXTURE_RUN, tmp_path / "seed_0", hash_seed="0")
    second = project_in_subprocess(FIXTURE_RUN, tmp_path / "seed_1", hash_seed="12345")

    assert first.name == second.name  # the run id is a function of the inputs, not the seed
    for name in (NODES_FILENAME, EDGES_FILENAME):
        assert sha256(first / name) == sha256(second / name), name
        assert (first / name).read_bytes() == (second / name).read_bytes()


@requires_real_run
def test_the_real_run_is_byte_identical_across_two_seeded_processes(tmp_path):
    """The same check on 28,836 nodes, where an unordered iteration has room to show."""
    first = project_in_subprocess(REAL_RUN, tmp_path / "seed_0", hash_seed="0")
    second = project_in_subprocess(REAL_RUN, tmp_path / "seed_1", hash_seed="98765")

    assert first.name == second.name
    for name in (NODES_FILENAME, EDGES_FILENAME):
        assert sha256(first / name) == sha256(second / name), name


# -- the run id ---------------------------------------------------------------------------


def test_graph_run_id_is_identical_across_two_projections(fixture_pair):
    first, second = fixture_pair
    assert first.graph_run_id == second.graph_run_id
    assert first.directory.name == first.graph_run_id


def test_graph_run_id_carries_no_clock_and_is_derived(fixture_pair):
    """`graph-v1-<hash12>` over (version, run id, ontology hash, input digest) — nothing else.

    Recomputed from the four inputs rather than compared to a literal: a literal would go
    stale the moment the projection version moves, and the property under test is that the id
    is a *function of its inputs*, not that it is one particular string today. The manifest
    records the input digest for exactly this reason — the recomputation must not need the
    run directory.
    """
    outcome, _ = fixture_pair
    assert re.fullmatch(r"graph-v\d+-[0-9a-f]{12}", outcome.graph_run_id)
    assert outcome.graph_run_id == make_graph_run_id(
        extraction_run_id=outcome.manifest["extraction_run_id"],
        ontology_definition_hash=outcome.manifest["ontology_definition_hash"],
        input_digest=outcome.manifest["inputs"]["input_content_digest"])
    # No year, no month, no clock — every character after the prefix is lowercase hex.
    assert not TIMESTAMP.search(outcome.graph_run_id)


def test_changing_any_derivation_input_changes_the_run_id():
    base = make_graph_run_id(extraction_run_id="run-a", ontology_definition_hash="hash-a",
                             input_digest="bytes-a")
    assert base != make_graph_run_id(
        extraction_run_id="run-b", ontology_definition_hash="hash-a",
        input_digest="bytes-a")
    assert base != make_graph_run_id(
        extraction_run_id="run-a", ontology_definition_hash="hash-b",
        input_digest="bytes-a")
    assert base != make_graph_run_id(
        extraction_run_id="run-a", ontology_definition_hash="hash-a",
        input_digest="bytes-b")
    assert base != make_graph_run_id(
        extraction_run_id="run-a", ontology_definition_hash="hash-a",
        input_digest="bytes-a", projection_version="2.0.0")


def test_an_empty_input_digest_is_refused():
    """The parameter has no default, and an empty string is not a substitute for one.

    A default here would restore the collision it exists to prevent, silently, in whichever
    call site was not updated.
    """
    with pytest.raises(GraphInputError, match="input_digest"):
        make_graph_run_id(extraction_run_id="run-a", ontology_definition_hash="hash-a",
                          input_digest="   ")


# -- R3: two different inputs may never name one directory ---------------------------------


def test_two_directories_with_one_manifest_get_two_run_ids(graph_context, tmp_path):
    """The bug this digest exists for, proved offline with no dependence on `data/`.

    `tests/fixtures/graph/extraction_run/` ships the run's manifest **verbatim** — its README
    says so deliberately — so `run_id`, `ontology_definition_hash` and the projection version
    are identical for the 39-row slice and the 2,717-row run it came from. Before 2026-08-03
    those three *were* the whole id: both minted `graph-v1-380c18fe3b9f`, and projecting the
    fixture into the default runs root removed the real export
    (`GraphRunWriter.finalize` replaces its destination).

    Reproduced here with two copies of the fixture that differ by one `issues.jsonl` row and
    share a manifest byte-for-byte. They must project into two directories, and both must
    survive.
    """
    first = tmp_path / "corpus_a"
    shutil.copytree(FIXTURE_RUN.parent, first)
    second = tmp_path / "corpus_b"
    shutil.copytree(FIXTURE_RUN.parent, second)

    issues = second / FIXTURE_RUN.name / "issues.jsonl"
    lines = issues.read_text(encoding="utf-8").splitlines()
    issues.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")

    manifest_a = (first / FIXTURE_RUN.name / "manifest.json").read_bytes()
    manifest_b = (second / FIXTURE_RUN.name / "manifest.json").read_bytes()
    assert manifest_a == manifest_b

    root = tmp_path / "runs"
    a = run_projection(graph_context, first / FIXTURE_RUN.name, root)
    b = run_projection(graph_context, second / FIXTURE_RUN.name, root)
    assert a.graph_run_id != b.graph_run_id
    for outcome in (a, b):
        assert (outcome.directory / MANIFEST_FILENAME).is_file()
        assert (outcome.directory / NODES_FILENAME).is_file()


def test_projecting_the_same_input_twice_still_lands_in_one_directory(fixture_pair):
    """The other half of R3: the digest must not make the id vary between two runs."""
    first, second = fixture_pair
    assert first.graph_run_id == second.graph_run_id
    assert (first.manifest["inputs"]["input_content_digest"]
            == second.manifest["inputs"]["input_content_digest"])


@requires_real_run
def test_the_fixture_and_the_real_run_project_into_different_directories(
    fixture_inputs, real_inputs
):
    """The measured collision, closed. Same manifest fields, two ids."""
    assert (fixture_inputs.manifest.run_id == real_inputs.manifest.run_id
            == "extract-v1-lexical-2422c4252c07")
    assert (fixture_inputs.manifest.ontology_definition_hash
            == real_inputs.manifest.ontology_definition_hash)
    assert input_content_digest(fixture_inputs) != input_content_digest(real_inputs)
    assert graph_run_id_for(fixture_inputs) != graph_run_id_for(real_inputs)
    # And the real run still lands where the shipped export lives.
    assert re.fullmatch(r"graph-v\d+-[0-9a-f]{12}", graph_run_id_for(real_inputs))


@requires_real_run
def test_the_input_digest_is_stable_across_two_reads_of_one_run(real_inputs):
    from graph.core.inputs import load_run

    assert input_content_digest(real_inputs) == input_content_digest(
        load_run(REAL_RUN, catalog_directory=REAL_CATALOG))


@requires_real_run
def test_the_real_run_projects_into_one_directory_twice(real_pair):
    first, second = real_pair
    assert first.graph_run_id == second.graph_run_id


# -- what may not reach the artifacts ------------------------------------------------------


@pytest.mark.parametrize("name", [NODES_FILENAME, EDGES_FILENAME])
def test_artifacts_hold_no_iso_timestamp(fixture_pair, name):
    outcome, _ = fixture_pair
    text = (outcome.directory / name).read_text(encoding="utf-8")
    assert not TIMESTAMP.search(text)


@requires_real_run
@pytest.mark.parametrize("name", [NODES_FILENAME, EDGES_FILENAME])
def test_real_artifacts_hold_no_iso_timestamp(real_pair, name):
    first, _ = real_pair
    assert not TIMESTAMP.search((first.directory / name).read_text(encoding="utf-8"))


def _clock_bearing_paths(value: object, path: str = "") -> list[str]:
    """Every leaf in the document whose value looks like a wall-clock timestamp."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_clock_bearing_paths(item, f"{path}.{key}" if path else key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_clock_bearing_paths(item, f"{path}[{index}]"))
    elif isinstance(value, str) and TIMESTAMP.search(value):
        found.append(path)
    return found


def test_the_manifest_is_the_only_place_a_clock_appears(fixture_pair):
    """The name says "only", so "only" is what is asserted.

    The test used to check that `created_at` parses as a timestamp — true, and no evidence
    for the claim in its own name. Both halves now: no clock in either artifact, and inside
    the manifest exactly two clock-bearing fields, each named.

    `inputs.extraction_created_at` is the second one and is *not* this layer's clock: it is
    copied out of the extraction manifest, so it is a fact about the input rather than a
    reading taken during this run, and it is identical across two projections. Naming it is
    the point — a third entry appearing here is a clock that crept in.
    """
    outcome, second = fixture_pair
    for name in (NODES_FILENAME, EDGES_FILENAME):
        assert not TIMESTAMP.search((outcome.directory / name).read_text(encoding="utf-8"))
    assert sorted(_clock_bearing_paths(outcome.manifest)) == [
        "created_at", "inputs.extraction_created_at"]
    assert TIMESTAMP.fullmatch(outcome.manifest["created_at"][:16])
    # The copied one is an input fact and must be equal across two runs; `created_at` is the
    # only field two projections of one run may legitimately differ on.
    assert (outcome.manifest["inputs"]["extraction_created_at"]
            == second.manifest["inputs"]["extraction_created_at"])


@pytest.mark.parametrize("name", [NODES_FILENAME, EDGES_FILENAME])
def test_artifacts_hold_no_path_this_layer_invented(fixture_pair, name):
    """No absolute path the export produced — and the inherited ones named, not waved away.

    See the module docstring: `provider_model_id` arrives from `claims.jsonl` and is promoted
    by the node builder, so the honest assertion is that every absolute path in the export is
    that property's value and that none of them points at this repository or this run.
    """
    outcome, _ = fixture_pair
    offenders: dict[str, str] = {}
    for row in rows(outcome.directory / name):
        for key, value in row["properties"].items():
            if isinstance(value, str) and ABSOLUTE_PATH.search(value):
                offenders[key] = value
    assert set(offenders) <= {INHERITED_PATH_PROPERTY}, offenders
    text = (outcome.directory / name).read_text(encoding="utf-8")
    assert str(outcome.directory) not in text
    assert str(Path(__file__).resolve().parents[2]) not in text


@requires_real_run
def test_the_real_export_inherits_exactly_the_paths_the_catalog_carries(real_pair):
    """23 nodes, one property, no edges. A change in this number is a change upstream."""
    first, _ = real_pair
    carriers = [row for row in rows(first.directory / NODES_FILENAME)
                if INHERITED_PATH_PROPERTY in row["properties"]
                and ABSOLUTE_PATH.search(row["properties"][INHERITED_PATH_PROPERTY])]
    assert len(carriers) == 23
    for row in rows(first.directory / EDGES_FILENAME):
        for value in row["properties"].values():
            assert not (isinstance(value, str) and ABSOLUTE_PATH.search(value))


# -- order and encoding --------------------------------------------------------------------


def test_nodes_are_written_in_NODE_SORT_KEY_order(fixture_pair):
    outcome, _ = fixture_pair
    written = [(row["base_label"], row["key"]) for row in
               rows(outcome.directory / NODES_FILENAME)]
    assert written == sorted(written)
    assert written == [NODE_SORT_KEY(node) for node in outcome.export.nodes]


def test_edges_are_written_in_EDGE_SORT_KEY_order(fixture_pair):
    outcome, _ = fixture_pair
    written = [(row["type"], row["source_key"], row["target_key"], row["edge_key"])
               for row in rows(outcome.directory / EDGES_FILENAME)]
    assert written == sorted(written)
    assert written == [EDGE_SORT_KEY(edge) for edge in outcome.export.edges]


def test_the_export_value_itself_is_in_declared_order(fixture_pair):
    outcome, _ = fixture_pair
    assert sort_key_order_holds(outcome.export)


@pytest.mark.parametrize("name", [NODES_FILENAME, EDGES_FILENAME])
def test_json_keys_are_sorted_at_every_depth(fixture_pair, name):
    outcome, _ = fixture_pair
    for index, row in enumerate(rows(outcome.directory / name)):
        assert key_order(row) == [], f"{name}:{index + 1}"


@requires_real_run
@pytest.mark.parametrize("name", [NODES_FILENAME, EDGES_FILENAME])
def test_real_json_keys_are_sorted_at_every_depth(real_pair, name):
    first, _ = real_pair
    for index, row in enumerate(rows(first.directory / name)):
        assert key_order(row) == [], f"{name}:{index + 1}"


def test_rows_use_the_repositorys_compact_encoding(fixture_pair):
    """The same `separators` and `ensure_ascii` the catalog writer uses, checked on bytes."""
    outcome, _ = fixture_pair
    line = (outcome.directory / NODES_FILENAME).read_text(encoding="utf-8").splitlines()[0]
    assert ", " not in line[:line.index('"properties"')]
    assert line == json.dumps(
        json.loads(line), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# -- the directory ------------------------------------------------------------------------


def test_manifest_is_written_last(fixture_pair):
    outcome, _ = fixture_pair
    assert outcome.write_order == (NODES_FILENAME, EDGES_FILENAME, MANIFEST_FILENAME)
    assert outcome.write_order[-1] == MANIFEST_FILENAME


def test_a_finished_projection_holds_exactly_three_files(fixture_pair):
    outcome, _ = fixture_pair
    assert sorted(p.name for p in outcome.directory.iterdir()) == [
        EDGES_FILENAME, MANIFEST_FILENAME, NODES_FILENAME]


def test_no_partial_directory_survives(fixture_pair):
    outcome, _ = fixture_pair
    assert not (outcome.directory.parent / f"{outcome.graph_run_id}.partial").exists()


def test_the_manifest_names_both_commits_separately(fixture_pair):
    """G0: the extraction manifest's own commit is not the commit that produced its rows."""
    outcome, _ = fixture_pair
    manifest = outcome.manifest
    assert manifest["extraction_code_commit"] == "4d3ae1e8e2b90356932a33c6b611e444d1396faa"
    assert "graph_code_commit" in manifest
    assert manifest["graph_projection_version"] == GRAPH_PROJECTION_VERSION
    assert manifest["inputs"]["extraction_run_id"] == manifest["extraction_run_id"]


def test_provenance_reaches_every_node_and_every_edge(fixture_pair):
    outcome, _ = fixture_pair
    expected = {"extraction_run_id", "graph_run_id", "graph_projection_version", "ontology_id",
                "ontology_version", "ontology_definition_hash", "extraction_code_commit",
                "graph_code_commit"}
    for node in outcome.export.nodes:
        assert expected <= set(node.properties)
    for edge in outcome.export.edges:
        assert expected <= set(edge.properties)


# -- the four refusals, on hand-built exports ----------------------------------------------


def node(key: str, base_label: str = "Entity") -> GraphNode:
    return GraphNode(key=key, base_label=base_label, labels=(base_label,), properties={})


def edge(edge_key: str, source: str, target: str) -> GraphEdge:
    return GraphEdge(
        edge_key=edge_key, type="PART_OF", source_key=source, source_base_label="Entity",
        target_key=target, target_base_label="Entity", properties={})


def test_a_dangling_endpoint_fails_and_names_the_missing_key():
    export = GraphExport(
        nodes=(node("opendoor"),),
        edges=(edge("PART_OF:opendoor:ghost", "opendoor", "ghost"),))
    with pytest.raises(DanglingEndpointError) as caught:
        check_export(export)
    message = str(caught.value)
    assert "'ghost'" in message
    assert "PART_OF:opendoor:ghost" in message
    assert "target_key" in message


def test_a_dangling_source_is_caught_too():
    export = GraphExport(
        nodes=(node("opendoor"),),
        edges=(edge("PART_OF:ghost:opendoor", "ghost", "opendoor"),))
    with pytest.raises(DanglingEndpointError) as caught:
        check_export(export)
    assert "source_key" in str(caught.value)


def test_a_duplicate_node_key_fails():
    export = GraphExport(nodes=(node("opendoor"), node("opendoor", "Metric")))
    with pytest.raises(DuplicateKeyError) as caught:
        check_export(export)
    assert "'opendoor'" in str(caught.value)


def test_a_duplicate_edge_key_fails():
    export = GraphExport(
        nodes=(node("a"), node("b")),
        edges=(edge("PART_OF:a:b", "a", "b"), edge("PART_OF:a:b", "b", "a")))
    with pytest.raises(DuplicateKeyError) as caught:
        check_export(export)
    assert "PART_OF:a:b" in str(caught.value)


def test_an_empty_node_key_fails_the_export():
    """`GraphNode` refuses one at construction; the export refuses one anyway.

    Built with `model_construct` to bypass the model validator on purpose — the point of the
    second gate is that it holds if the first is ever relaxed, and §2.3 trap 2 says an empty
    `passage_id` is a legal catalog value, so the pressure to relax it is real.
    """
    blank = GraphNode.model_construct(
        key="", base_label="Passage", labels=("Passage",), properties={})
    with pytest.raises(EmptyNodeKeyError):
        check_export(GraphExport(nodes=(blank,)))


def test_the_real_export_has_no_dangling_endpoint_and_no_duplicate(fixture_pair):
    """The clean case, asserted on the value rather than assumed from the run's silence."""
    outcome, _ = fixture_pair
    check_export(outcome.export)


# -- warning reconciliation ----------------------------------------------------------------


def test_reconciliation_is_recorded_for_the_fixture(fixture_pair):
    """9 derived against the run's 186, not compared — the fixture ships the run's manifest."""
    outcome, _ = fixture_pair
    block = outcome.manifest["warning_reconciliation"]
    assert block["derived"] == 9
    assert block["manifest_total"] == REAL_WARNINGS
    assert block["covers_whole_run"] is False


@requires_real_run
def test_reconciliation_agrees_on_the_real_run(real_pair):
    first, _ = real_pair
    block = first.manifest["warning_reconciliation"]
    assert block == {"agreed": True, "covers_whole_run": True, "derived": REAL_WARNINGS,
                     "manifest_total": REAL_WARNINGS, "warned_claims": REAL_WARNINGS}


def test_a_disagreeing_warning_count_fails_the_export(fixture_inputs, ontology):
    """Make the manifest claim the fixture *is* the whole run, and the comparison must fire."""
    counts = {**fixture_inputs.manifest.counts,
              "observations": len(fixture_inputs.observations),
              "claims": len(fixture_inputs.claims)}
    lying = dataclasses.replace(
        fixture_inputs, manifest=fixture_inputs.manifest.model_copy(update={"counts": counts}))
    with pytest.raises(WarningReconciliationError) as caught:
        reconcile_warnings(lying, ontology)
    message = str(caught.value)
    assert "9 warning(s)" in message
    assert str(REAL_WARNINGS) in message


def test_a_disagreeing_warning_count_writes_nothing(graph_context, copied_fixture, tmp_path):
    """The pipeline stops before the writer: no directory, not even a partial one."""
    manifest_path = copied_fixture / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["counts"]["observations"] = 30
    document["counts"]["claims"] = 39
    manifest_path.write_text(json.dumps(document), encoding="utf-8")

    root = tmp_path / "runs"
    with pytest.raises(WarningReconciliationError):
        run_projection(graph_context, copied_fixture, root)
    assert not root.exists() or list(root.iterdir()) == []


# -- rejected rows -------------------------------------------------------------------------


def test_a_malformed_event_property_is_rejected_and_writes_no_manifest(
    graph_context, copied_fixture, tmp_path
):
    """§6.4: a row the projection cannot project is filed and the run exits non-zero.

    A nested map as an event property is the refusal §5.4 names — Neo4j stores no map as a
    property value — and it is reachable by editing one field of one copied row.
    """
    events = copied_fixture / "events.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["properties"] = {"broken": {"nested": "map"}}
    events.write_text("\n".join([json.dumps(first), *lines[1:]]) + "\n", encoding="utf-8")

    root = tmp_path / "runs"
    outcome = run_projection(graph_context, copied_fixture, root)

    assert outcome.ok is False
    assert outcome.export is None
    assert [row.code for row in outcome.rejections] == ["MALFORMED_EVENT_PROPERTY"]
    written = rows(outcome.directory / REJECTED_FILENAME)
    assert written[0]["code"] == "MALFORMED_EVENT_PROPERTY"
    assert set(written[0]) == {"row_index", "file", "code", "detail"}
    assert "MalformedEventPropertyError" in written[0]["detail"]
    assert not (outcome.directory / MANIFEST_FILENAME).exists()
    assert not (outcome.directory / NODES_FILENAME).exists()


def test_the_cli_exits_non_zero_on_a_rejected_row(copied_fixture, tmp_path, capsys):
    events = copied_fixture / "events.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["properties"] = {"broken": ["a", 1]}
    events.write_text("\n".join([json.dumps(first), *lines[1:]]) + "\n", encoding="utf-8")

    code = main(["--runs-root", str(tmp_path / "runs"), "project", str(copied_fixture)])
    assert code == EXIT_FAILED
    assert "REJECTED" in capsys.readouterr().out


def test_the_cli_exits_zero_on_the_fixture(tmp_path, capsys):
    code = main(["--runs-root", str(tmp_path / "runs"), "project", str(FIXTURE_RUN)])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "nodes.jsonl" in out and "edges.jsonl" in out


# -- R4: a refusal never destroys a complete projection ------------------------------------


def test_a_rejected_projection_lands_beside_the_run_id_not_on_it(
    graph_context, copied_fixture, tmp_path
):
    """`finalize` replaced its destination; `finalize_rejected` may not reach it at all."""
    events = copied_fixture / "events.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["properties"] = {"broken": {"nested": "map"}}
    events.write_text("\n".join([json.dumps(first), *lines[1:]]) + "\n", encoding="utf-8")

    root = tmp_path / "runs"
    outcome = run_projection(graph_context, copied_fixture, root)

    assert outcome.ok is False
    assert outcome.directory.name == f"{outcome.graph_run_id}{REJECTED_SUFFIX}"
    assert (outcome.directory / REJECTED_FILENAME).is_file()
    assert not (root / outcome.graph_run_id).exists()


def test_a_refusal_leaves_a_previously_complete_projection_intact(
    graph_context, copied_fixture, tmp_path
):
    """The loss R4 names, end to end: good projection, then a refusal at the same id.

    The id is computed first and a complete directory is planted at it, because the input
    digest means a *corrupted* run no longer collides with the good one — which is R3's fix,
    not R4's. R4 is the guarantee that a refusal cannot remove whatever is standing at its
    id, whatever put it there: an earlier projection, or a rerun after an environment change.
    """
    from graph.core.inputs import load_run

    events = copied_fixture / "events.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["properties"] = {"broken": {"nested": "map"}}
    events.write_text("\n".join([json.dumps(first), *lines[1:]]) + "\n", encoding="utf-8")

    root = tmp_path / "runs"
    doomed_id = graph_run_id_for(load_run(copied_fixture))
    complete = root / doomed_id
    complete.mkdir(parents=True)
    (complete / NODES_FILENAME).write_text('{"key":"kept"}\n', encoding="utf-8")
    (complete / EDGES_FILENAME).write_text('{"edge_key":"kept"}\n', encoding="utf-8")
    (complete / MANIFEST_FILENAME).write_text('{"graph_run_id":"kept"}\n', encoding="utf-8")

    code = main(["--runs-root", str(root), "project", str(copied_fixture)])
    assert code == EXIT_FAILED

    assert sorted(p.name for p in complete.iterdir()) == [
        EDGES_FILENAME, MANIFEST_FILENAME, NODES_FILENAME]
    assert (complete / NODES_FILENAME).read_text(encoding="utf-8") == '{"key":"kept"}\n'
    assert (root / f"{doomed_id}{REJECTED_SUFFIX}" / REJECTED_FILENAME).is_file()


def test_the_writer_keeps_a_complete_directory_when_a_rejection_uses_its_id(tmp_path):
    """The same guarantee at the writer, where the two finalizations are decided."""
    root = tmp_path / "runs"
    good = GraphRunWriter(root, "graph-v1-abcdef012345")
    good.begin()
    good.write(NODES_FILENAME, "{}\n")
    good.write(EDGES_FILENAME, "{}\n")
    good.write(MANIFEST_FILENAME, "{}\n")
    kept = good.finalize().directory
    assert good.previous_is_complete

    bad = GraphRunWriter(root, "graph-v1-abcdef012345")
    bad.begin()
    bad.write(REJECTED_FILENAME, "{}\n")
    record = bad.finalize_rejected()

    assert record.directory == root / f"graph-v1-abcdef012345{REJECTED_SUFFIX}"
    assert sorted(p.name for p in kept.iterdir()) == [
        EDGES_FILENAME, MANIFEST_FILENAME, NODES_FILENAME]
    assert not (kept / REJECTED_FILENAME).exists()


def test_runs_lists_the_complete_projection_and_not_the_rejected_one(
    graph_context, copied_fixture, tmp_path, capsys
):
    """§6.5's completion marker, applied to the new directory name."""
    root = tmp_path / "runs"
    good = run_projection(graph_context, FIXTURE_RUN, root)

    events = copied_fixture / "events.jsonl"
    lines = events.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["properties"] = {"broken": {"nested": "map"}}
    events.write_text("\n".join([json.dumps(first), *lines[1:]]) + "\n", encoding="utf-8")
    bad = run_projection(graph_context, copied_fixture, root)
    assert bad.ok is False

    capsys.readouterr()
    assert main(["--runs-root", str(root), "runs"]) == EXIT_OK
    listed = capsys.readouterr().out.split()
    assert listed == [good.graph_run_id]
    assert bad.directory.name not in listed


# -- the real run's numbers ------------------------------------------------------------------


@requires_real_run
def test_real_run_counts(real_pair):
    """P11's answer, asserted: 28,836 nodes and 35,603 edges."""
    first, second = real_pair
    for outcome in (first, second):
        counts = outcome.manifest["counts"]
        assert counts["nodes"] == REAL_NODE_COUNT
        assert counts["edges"] == REAL_EDGE_COUNT
        assert counts["rejected"] == 0
        assert len(outcome.export.nodes) == REAL_NODE_COUNT
        assert len(outcome.export.edges) == REAL_EDGE_COUNT
    assert first.manifest["counts"] == second.manifest["counts"]


@requires_real_run
def test_real_run_nodes_by_label(real_pair):
    first, _ = real_pair
    assert first.manifest["counts"]["nodes_by_label"] == {
        "Document": 185, "Entity": 9, "Event": 6, "Issue": 17_127, "Metric": 26,
        "Observation": 2_707, "Passage": 8_776}


@requires_real_run
def test_real_run_edges_by_type(real_pair):
    first, _ = real_pair
    assert first.manifest["counts"]["edges_by_type"] == {
        "BORROWS_UNDER": 1, "CONCERNS_METRIC": 1_520, "DISTINCT_FROM": 36,
        "EVIDENCED_BY": 2_713, "FOUND_IN": 17_127, "HAS_OBSERVATION": 2_707,
        "HOLDS_POSITION_AT": 3, "OBSERVATION_OF_SUBJECT": 2_707, "PARTICIPATES_IN": 10,
        "PART_OF": 8_776, "PLACEHOLDER_FOR": 1, "RECONCILES_TO": 2}


@requires_real_run
def test_real_run_manifest_records_the_run_it_read(real_pair):
    first, _ = real_pair
    inputs = first.manifest["inputs"]
    assert inputs["extraction_run_id"] == REAL_RUN.name
    assert inputs["run_complete_sha256"] is not None
    assert not Path(inputs["extraction_run_directory"]).is_absolute()
