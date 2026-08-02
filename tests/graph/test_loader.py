"""The strict export reader and the batched loader (V1_GRAPH_PROTOTYPE §6.3, §10.2).

Three kinds of test, split by what they need rather than by what they assert:

    unmarked         the reader, the statement builders, the grouping, and the short-write
                     comparison itself — no database, driven through a stub transaction
    unmarked, skip   the real export under gitignored `data/`, which encodes the measurements
                     `loader.py` hard-codes (15 label sets, 13 edge groups, 12 types)
    @neo4j           the live 5.26.28 Community server: idempotence, property parity, and the
                     two short-write failures that only a real `MATCH` can produce

`pytest -m "not live and not neo4j"` is green with no container running.

**The marked tests never load the real export and never wipe the database.** They write a
13-node, 14-edge synthetic export whose every node and relationship carries
`graph_run_id = "graph-test-loader"`, and delete exactly those before and after each test.
Replacement is the lifecycle stage's business; a test that wiped the graph would delete another
stage's run to prove a point about its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest

from graph.core.models import GraphEdge, GraphNode
from graph.stages.load.connection import (
    GraphSettings,
    MissingGraphCredentialError,
    UnresolvedGraphTargetError,
    driver_for,
    load_settings,
)
from graph.stages.load.loader import (
    ALLOWED_LABELS,
    ALLOWED_RELATIONSHIP_TYPES,
    DisallowedLabelError,
    DisallowedRelationshipTypeError,
    ShortWriteError,
    batches,
    edge_parameters,
    edge_statement,
    endpoint_diagnostic_statement,
    group_edges,
    group_nodes,
    load_edges,
    load_export,
    load_nodes,
    node_parameters,
    node_statement,
    write_edge_batch,
    write_node_batch,
)
from graph.stages.load.reader import (
    EDGES_FILENAME,
    NODES_FILENAME,
    GraphExportReadError,
    property_fault,
    read_edges,
    read_export,
    read_nodes,
)
from graph.stages.load.schema import apply_schema

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The finalized G1 projection. Gitignored, so every assertion about it skips when it is absent
#: rather than failing — the same convention `tests/graph/conftest.py` uses for the run itself.
REAL_EXPORT = REPO_ROOT / "data" / "graph_runs" / "graph-v1-886059d862ce"
REAL_EXPORT_AVAILABLE = (REAL_EXPORT / NODES_FILENAME).is_file()

#: Every synthetic node and relationship carries this, and cleanup deletes exactly it.
TEST_RUN_ID = "graph-test-loader"


# -- a small synthetic export -------------------------------------------------------------------


def _node(key: str, labels: tuple[str, ...], **properties: Any) -> GraphNode:
    base = labels[0]
    return GraphNode(
        key=key,
        base_label=base,
        labels=labels,
        properties={
            f"{base.lower()}_id": key,
            "graph_run_id": TEST_RUN_ID,
            **properties,
        },
    )


def _edge(
    relationship_type: str, source: GraphNode, target: GraphNode, **properties: Any
) -> GraphEdge:
    return GraphEdge(
        edge_key=f"test:loader:edge:{relationship_type.lower()}:{source.key}:{target.key}",
        type=relationship_type,
        source_key=source.key,
        source_base_label=source.base_label,
        target_key=target.key,
        target_base_label=target.base_label,
        properties={"graph_run_id": TEST_RUN_ID, **properties},
    )


ACME = _node(
    "test:loader:entity:acme",
    ("Entity", "PublicCompany", "Company"),
    entity_text="Acme Inc.",
    resolved=True,
    mention_count=3,
    confidence=0.75,
    # The three list shapes the real export contains: a populated string array, an empty array,
    # and — by omission — no null, because a null property cannot be stored at all.
    aliases=["Acme", "Acme Inc."],
    block_ids=[],
)
UNNAMED = _node(
    "test:loader:entity:unnamed",
    ("Entity", "Subsidiary", "Company", "Unresolved"),
    resolved=False,
)
PERSON = _node("test:loader:entity:person", ("Entity", "Person"), entity_text="A. Person")
EVENT = _node("test:loader:event:1", ("Event",), event_type_id="credit_agreement_entered")
REVENUE = _node("test:loader:metric:revenue", ("Metric",), label="Revenue")
CONTRIBUTION = _node("test:loader:metric:contribution", ("Metric",), label="Contribution profit")
OBSERVATION = _node("test:loader:obs:1", ("Observation",), value=1234.5, period_key="2022Q3")
WARNED = _node("test:loader:obs:2", ("Observation", "Warned"), value=7.0, period_key="2022Q3")
PASSAGE = _node("test:loader:passage:1", ("Passage",), text="Acme entered a credit agreement.")
DOCUMENT = _node("test:loader:document:1", ("Document",), form="8-K")
ISSUE = _node("test:loader:issue:1", ("Issue",), code="AMBIGUOUS_COLUMN_ALIGNMENT")
NOT_ATTEMPTED = _node("test:loader:issue:2", ("Issue", "NotAttempted"), code="NO_STORED_ANSWER")
REJECTED = _node("test:loader:issue:3", ("Issue", "Rejected"), code="REJECTED_BY_VALIDATION")

SYNTHETIC_NODES: tuple[GraphNode, ...] = (
    ACME,
    UNNAMED,
    PERSON,
    EVENT,
    REVENUE,
    CONTRIBUTION,
    OBSERVATION,
    WARNED,
    PASSAGE,
    DOCUMENT,
    ISSUE,
    NOT_ATTEMPTED,
    REJECTED,
)

#: One edge of every one of §3.2's twelve types, in the thirteen groups the real export has —
#: `EVIDENCED_BY` is the type that occurs with two different source base labels.
SYNTHETIC_EDGES: tuple[GraphEdge, ...] = (
    _edge("PARTICIPATES_IN", ACME, EVENT, role="counterparty"),
    _edge("EVIDENCED_BY", OBSERVATION, PASSAGE, quoted_text="Revenue was $1,234.5"),
    _edge("EVIDENCED_BY", EVENT, PASSAGE, quoted_text="entered a credit agreement"),
    _edge("HAS_OBSERVATION", REVENUE, OBSERVATION),
    _edge("HAS_OBSERVATION", REVENUE, WARNED),
    _edge("OBSERVATION_OF_SUBJECT", OBSERVATION, ACME),
    _edge("PART_OF", PASSAGE, DOCUMENT),
    _edge("FOUND_IN", ISSUE, PASSAGE),
    _edge("CONCERNS_METRIC", ISSUE, REVENUE),
    _edge("DISTINCT_FROM", REVENUE, CONTRIBUTION, assertion="ontology_definition"),
    _edge("RECONCILES_TO", CONTRIBUTION, REVENUE, assertion="ontology_definition"),
    _edge("PLACEHOLDER_FOR", UNNAMED, ACME),
    _edge("HOLDS_POSITION_AT", PERSON, ACME, position="Chief Executive Officer"),
    _edge("BORROWS_UNDER", UNNAMED, ACME, lane="events"),
)


def write_export(directory: Path, nodes=SYNTHETIC_NODES, edges=SYNTHETIC_EDGES) -> Path:
    """The synthetic export as the two artifacts on disk, so the reader can be driven for real."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / NODES_FILENAME).write_text(
        "".join(json.dumps(node.model_dump(mode="json"), sort_keys=True) + "\n" for node in nodes),
        encoding="utf-8",
    )
    (directory / EDGES_FILENAME).write_text(
        "".join(json.dumps(edge.model_dump(mode="json"), sort_keys=True) + "\n" for edge in edges),
        encoding="utf-8",
    )
    return directory


def write_rows(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )
    return path


def node_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "key": "test:loader:issue:row",
        "base_label": "Issue",
        "labels": ["Issue"],
        "properties": {"issue_id": "test:loader:issue:row", "code": "X"},
    }
    row.update(overrides)
    return row


# -- the strict reader ---------------------------------------------------------------------------


def test_the_reader_round_trips_the_synthetic_export(tmp_path):
    contents = read_export(write_export(tmp_path / "export"))
    assert contents.nodes == SYNTHETIC_NODES
    assert contents.edges == SYNTHETIC_EDGES


def test_an_unknown_field_is_refused_by_name(tmp_path):
    path = write_rows(tmp_path / NODES_FILENAME, [node_row(colour="blue")])
    with pytest.raises(GraphExportReadError) as refused:
        read_nodes(path)
    assert refused.value.line_number == 1
    assert "unknown field 'colour'" in str(refused.value)


def test_a_missing_field_is_refused_by_name(tmp_path):
    row = node_row()
    del row["labels"]
    path = write_rows(tmp_path / NODES_FILENAME, [row])
    with pytest.raises(GraphExportReadError) as refused:
        read_nodes(path)
    assert "missing field 'labels'" in str(refused.value)


def test_an_empty_key_is_refused(tmp_path):
    path = write_rows(tmp_path / NODES_FILENAME, [node_row(key="   ")])
    with pytest.raises(GraphExportReadError) as refused:
        read_nodes(path)
    assert "non-empty" in str(refused.value)


def test_a_row_that_is_not_an_object_is_refused(tmp_path):
    (tmp_path / NODES_FILENAME).write_text('["not", "a", "row"]\n', encoding="utf-8")
    with pytest.raises(GraphExportReadError) as refused:
        read_nodes(tmp_path / NODES_FILENAME)
    assert "expected an object" in str(refused.value)


def test_a_nested_map_property_is_refused_naming_the_row_and_the_property(tmp_path):
    """§6.4's `MALFORMED_EVENT_PROPERTY`, on the way out of the file instead of into it."""
    rows = [
        node_row(),
        node_row(
            key="test:loader:issue:nested",
            properties={
                "issue_id": "test:loader:issue:nested",
                "detail": {"nested": "map"},
            },
        ),
    ]
    path = write_rows(tmp_path / NODES_FILENAME, rows)
    with pytest.raises(GraphExportReadError) as refused:
        read_nodes(path)
    assert refused.value.line_number == 2
    assert refused.value.key == "test:loader:issue:nested"
    assert refused.value.property_name == "detail"
    assert "never a nested structure" in str(refused.value)


def test_a_heterogeneous_list_is_refused_and_a_homogeneous_one_is_not():
    assert property_fault("block_ids", ["b1", "b2"]) is None
    assert property_fault("block_ids", []) is None
    assert property_fault("counts", [1, 2, 3]) is None
    mixed = property_fault("block_ids", ["b1", 2])
    assert mixed is not None and "one primitive type" in mixed
    nested_list = property_fault("block_ids", [["b1"]])
    assert nested_list is not None and "primitives only" in nested_list


def test_a_null_property_is_refused_because_it_would_delete_the_key():
    fault = property_fault("occurred_on", None)
    assert fault is not None and "removes the key" in fault


def test_an_integer_outside_neo4j_range_is_refused():
    assert property_fault("count", 2**63 - 1) is None
    fault = property_fault("count", 2**63)
    assert fault is not None and "64-bit" in fault


def test_nan_is_refused_because_it_is_not_json(tmp_path):
    (tmp_path / NODES_FILENAME).write_text(
        '{"key": "k", "base_label": "Issue", "labels": ["Issue"], '
        '"properties": {"value": NaN}}\n',
        encoding="utf-8",
    )
    with pytest.raises(GraphExportReadError) as refused:
        read_nodes(tmp_path / NODES_FILENAME)
    assert "unreadable JSON" in str(refused.value)


def test_a_repeated_node_key_is_refused_because_the_second_row_would_vanish(tmp_path):
    path = write_rows(tmp_path / NODES_FILENAME, [node_row(), node_row()])
    with pytest.raises(GraphExportReadError) as refused:
        read_nodes(path)
    assert "already appeared on line 1" in str(refused.value)


def test_a_repeated_edge_key_is_refused(tmp_path):
    row = json.loads(SYNTHETIC_EDGES[0].model_dump_json())
    path = write_rows(tmp_path / EDGES_FILENAME, [row, row])
    with pytest.raises(GraphExportReadError) as refused:
        read_edges(path)
    assert "already appeared on line 1" in str(refused.value)


# -- the real export, which is what the loader's constants were measured from ---------------------


real_export_only = pytest.mark.skipif(
    not REAL_EXPORT_AVAILABLE,
    reason=f"{REAL_EXPORT} is absent — data/ is gitignored, so the measured-shape assertions "
    "only run on a machine that has produced the projection",
)


@real_export_only
def test_the_real_export_has_the_fifteen_label_sets_and_thirteen_edge_groups_the_loader_assumes():
    """The measurement `loader.py` hard-codes, re-run. If a re-projection changes it, this fails
    here rather than as an unexplained statement count in a load report."""
    contents = read_export(REAL_EXPORT)
    assert len(contents.nodes) == 28836
    assert len(contents.edges) == 35603

    grouped_nodes = group_nodes(contents.nodes)
    assert len(grouped_nodes) == 15
    assert sum(len(rows) for rows in grouped_nodes.values()) == 28836

    grouped_edges = group_edges(contents.edges)
    assert len(grouped_edges) == 13
    assert len({key[0] for key in grouped_edges}) == 12


@real_export_only
def test_every_label_and_type_in_the_real_export_is_on_the_allowlist():
    contents = read_export(REAL_EXPORT)
    labels = {label for node in contents.nodes for label in node.labels}
    assert labels <= set(ALLOWED_LABELS)
    assert {edge.type for edge in contents.edges} == set(ALLOWED_RELATIONSHIP_TYPES)


@real_export_only
def test_every_group_of_the_real_export_builds_a_statement_with_no_value_in_it():
    """The parameterization claim, over the whole corpus rather than one example.

    A value that reached the statement text would be an injection; the check is that the only
    Cypher built from the export is built from label and type names, which are allowlisted.
    """
    contents = read_export(REAL_EXPORT)
    for labels in group_nodes(contents.nodes):
        statement = node_statement(labels)
        assert "$rows" in statement and "row.key" in statement
    for relationship_type, source, target in group_edges(contents.edges):
        statement = edge_statement(relationship_type, source, target)
        assert "$rows" in statement

    # One row of each kind, checked value by value: nothing it carries occurs in its statement.
    sample_node = contents.nodes[0]
    node_text = node_statement(sample_node.labels)
    assert sample_node.key not in node_text
    for value in sample_node.properties.values():
        if isinstance(value, str) and value:
            assert value not in node_text
    sample_edge = contents.edges[0]
    edge_text = edge_statement(
        sample_edge.type, sample_edge.source_base_label, sample_edge.target_base_label
    )
    for value in (sample_edge.edge_key, sample_edge.source_key, sample_edge.target_key):
        assert value not in edge_text


# -- grouping and statement construction ----------------------------------------------------------


def test_nodes_are_grouped_by_their_exact_label_set():
    grouped = group_nodes(SYNTHETIC_NODES)
    # 13 nodes, 12 label sets: the two `:Metric` rows share one, which is what makes the group
    # the unit of work rather than the row.
    assert len(grouped) == 12
    assert grouped[("Issue",)] == [ISSUE]
    assert grouped[("Issue", "NotAttempted")] == [NOT_ATTEMPTED]
    assert grouped[("Issue", "Rejected")] == [REJECTED]
    assert list(grouped) == sorted(grouped), "group order must be deterministic"


def test_edges_are_grouped_by_type_and_both_endpoint_base_labels():
    grouped = group_edges(SYNTHETIC_EDGES)
    assert len(grouped) == 13
    assert len({key[0] for key in grouped}) == 12
    assert len(grouped[("EVIDENCED_BY", "Observation", "Passage")]) == 1
    assert len(grouped[("EVIDENCED_BY", "Event", "Passage")]) == 1
    assert len(grouped[("HAS_OBSERVATION", "Metric", "Observation")]) == 2


def test_the_node_statement_merges_on_the_base_label_key_and_sets_labels_statically():
    assert node_statement(("Issue", "Rejected")) == "\n".join(
        [
            "UNWIND $rows AS row",
            "MERGE (n:Issue {issue_id: row.key})",
            "SET n:Rejected",
            "SET n += row.properties",
            "RETURN count(n) AS written, count(DISTINCT n) AS distinct_written",
        ]
    )
    assert "SET n:" not in node_statement(("Passage",))
    assert "MERGE (n:Entity {entity_id: row.key})" in node_statement(ACME.labels)
    assert "SET n:PublicCompany:Company" in node_statement(ACME.labels)


def test_the_edge_statement_matches_both_endpoints_and_never_merges_one():
    statement = edge_statement("PARTICIPATES_IN", "Entity", "Event")
    assert statement == "\n".join(
        [
            "UNWIND $rows AS row",
            "MATCH (s:Entity {entity_id: row.source_key})",
            "MATCH (t:Event {event_id: row.target_key})",
            "MERGE (s)-[r:PARTICIPATES_IN {edge_key: row.edge_key}]->(t)",
            "SET r += row.properties",
            "RETURN count(r) AS written, count(DISTINCT r) AS distinct_written",
        ]
    )
    assert "MERGE (s:" not in statement and "MERGE (t:" not in statement


def test_no_apoc_procedure_appears_in_any_statement():
    statements = [node_statement(labels) for labels in group_nodes(SYNTHETIC_NODES)]
    statements += [edge_statement(*key) for key in group_edges(SYNTHETIC_EDGES)]
    statements += [
        endpoint_diagnostic_statement(key[1], key[2]) for key in group_edges(SYNTHETIC_EDGES)
    ]
    assert not [text for text in statements if "apoc" in text.lower()]


@pytest.mark.parametrize(
    "label",
    [
        "Issue`) DETACH DELETE (n",
        "Issue Rejected",
        "Issue}",
        "",
        "Fabricated",
        "issue",
    ],
)
def test_a_label_off_the_allowlist_never_reaches_cypher(label):
    with pytest.raises(DisallowedLabelError):
        node_statement(("Issue", label))


def test_a_base_label_that_is_not_a_base_label_is_refused():
    with pytest.raises(DisallowedLabelError):
        node_statement(("Warned", "Observation"))


@pytest.mark.parametrize(
    "relationship_type",
    ["FOUND_IN`]->() DETACH DELETE (n)-[:X", "found_in", "INVENTED_TYPE", ""],
)
def test_a_relationship_type_off_the_allowlist_never_reaches_cypher(relationship_type):
    with pytest.raises(DisallowedRelationshipTypeError):
        edge_statement(relationship_type, "Issue", "Passage")


def test_a_disallowed_edge_type_is_refused_before_any_node_is_written():
    """`load_export` builds every edge statement first, so the refusal precedes the node writes.

    Driven with a driver that raises if it is touched at all: the assertion is that nothing was
    sent, which a stub returning plausible counts could not make.
    """

    class RefusingDriver:
        def session(self, **_: Any):  # pragma: no cover - reaching this is the failure
            raise AssertionError("the loader opened a session before validating the export")

    invented = GraphEdge(
        edge_key="test:loader:edge:invented",
        type="INVENTED_TYPE",
        source_key=ISSUE.key,
        source_base_label="Issue",
        target_key=PASSAGE.key,
        target_base_label="Passage",
        properties={},
    )
    with pytest.raises(DisallowedRelationshipTypeError):
        load_export(RefusingDriver(), load_settings(), SYNTHETIC_NODES, (invented,))


def test_values_travel_as_parameters_and_never_as_text():
    hostile = GraphNode(
        key="test:loader:issue:`) DETACH DELETE (n) //",
        base_label="Issue",
        labels=("Issue",),
        properties={"code": "') RETURN 1 //"},
    )
    statement = node_statement(hostile.labels)
    parameters = node_parameters(hostile)
    assert hostile.key not in statement
    assert "RETURN 1 //" not in statement
    assert parameters == {"key": hostile.key, "properties": {"code": "') RETURN 1 //"}}
    assert edge_parameters(SYNTHETIC_EDGES[0])["edge_key"] == SYNTHETIC_EDGES[0].edge_key


def test_batches_partitions_without_losing_or_duplicating_a_row():
    rows = list(range(7))
    assert list(batches(rows, 3)) == [[0, 1, 2], [3, 4, 5], [6]]
    assert list(batches([], 3)) == []
    with pytest.raises(Exception):
        list(batches(rows, 0))


# -- the short-write comparison, driven with no database ------------------------------------------


class StubResult:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def single(self) -> dict[str, Any] | None:
        return self._records[0] if self._records else None

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._records)


class StubTransaction:
    """A transaction that reports whatever counts a test wants, and remembers what it was sent."""

    def __init__(self, written: int, distinct: int, missing: list[dict[str, Any]] | None = None):
        self.written = written
        self.distinct = distinct
        self.missing = missing or []
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def run(self, query: str, parameters: dict[str, Any] | None = None, **_: Any) -> StubResult:
        self.calls.append((query, parameters))
        if "OPTIONAL MATCH" in query:
            return StubResult(self.missing)
        return StubResult([{"written": self.written, "distinct_written": self.distinct}])


def test_a_node_batch_that_wrote_every_row_returns_the_count():
    rows = [node_parameters(node) for node in (ISSUE, NOT_ATTEMPTED)]
    transaction = StubTransaction(written=2, distinct=2)
    assert write_node_batch(transaction, node_statement(("Issue",)), rows, group=":Issue") == 2
    assert transaction.calls[0][1] == {"rows": rows}


def test_a_short_node_batch_raises_and_names_the_keys():
    rows = [node_parameters(node) for node in (ISSUE, NOT_ATTEMPTED)]
    with pytest.raises(ShortWriteError) as refused:
        write_node_batch(
            StubTransaction(written=1, distinct=1),
            node_statement(("Issue",)),
            rows,
            group=":Issue",
        )
    assert refused.value.submitted == 2 and refused.value.written == 1
    assert ISSUE.key in str(refused.value)


def test_two_node_rows_sharing_a_key_are_a_short_write_even_though_the_row_count_matches():
    """`count(n)` alone would say 2 of 2. The second row overwrote the first and is gone."""
    rows = [node_parameters(ISSUE), node_parameters(ISSUE)]
    with pytest.raises(ShortWriteError) as refused:
        write_node_batch(
            StubTransaction(written=2, distinct=1),
            node_statement(("Issue",)),
            rows,
            group=":Issue",
        )
    assert refused.value.keys == (ISSUE.key,)
    assert "merged onto one node" in str(refused.value)


def test_a_short_edge_batch_names_every_edge_key_and_which_end_was_missing():
    edges = SYNTHETIC_EDGES[:2]
    rows = [edge_parameters(edge) for edge in edges]
    transaction = StubTransaction(
        written=1,
        distinct=1,
        missing=[
            {
                "edge_key": edges[1].edge_key,
                "source_key": edges[1].source_key,
                "target_key": edges[1].target_key,
                "source_missing": False,
                "target_missing": True,
            }
        ],
    )
    with pytest.raises(ShortWriteError) as refused:
        write_edge_batch(
            transaction,
            edge_statement("EVIDENCED_BY", "Observation", "Passage"),
            endpoint_diagnostic_statement("Observation", "Passage"),
            rows,
            group="[:EVIDENCED_BY]",
        )
    assert refused.value.keys == (edges[1].edge_key,)
    assert refused.value.missing_endpoints[0].target_missing is True
    assert "MATCH filters rather than raising" in str(refused.value)


def test_an_edge_batch_that_wrote_every_row_never_runs_the_diagnostic():
    rows = [edge_parameters(SYNTHETIC_EDGES[0])]
    transaction = StubTransaction(written=1, distinct=1)
    written = write_edge_batch(
        transaction,
        edge_statement("PARTICIPATES_IN", "Entity", "Event"),
        endpoint_diagnostic_statement("Entity", "Event"),
        rows,
        group="[:PARTICIPATES_IN]",
    )
    assert written == 1
    assert len(transaction.calls) == 1


# -- against the live server -----------------------------------------------------------------


def live_settings() -> GraphSettings:
    settings = load_settings()
    try:
        settings.resolved_uri, settings.resolved_database, settings.auth
    except (UnresolvedGraphTargetError, MissingGraphCredentialError) as exc:
        pytest.skip(f"no Neo4j target configured on this machine: {type(exc).__name__}")
    return settings


def delete_test_rows(driver, settings: GraphSettings) -> None:
    """Delete exactly what this file creates. Never `MATCH (n) DETACH DELETE n`.

    Replacement belongs to the lifecycle stage (§6.2); a test that wiped the database would
    destroy whatever run another stage had loaded in order to check its own counts.
    """
    driver.execute_query(
        "MATCH (n) WHERE n.graph_run_id = $run DETACH DELETE n",
        {"run": TEST_RUN_ID},
        database_=settings.resolved_database,
    )


@pytest.fixture()
def live_graph():
    settings = live_settings()
    try:
        driver = driver_for(settings)
        driver.verify_connectivity()
    except Exception as exc:  # noqa: BLE001 - any connection failure is a skip, not a failure
        pytest.skip(
            f"local Neo4j is unreachable ({type(exc).__name__}); start it with "
            "`docker compose up -d`"
        )
    with driver:
        apply_schema(driver, settings)  # idempotent; the loader's MERGEs need the constraints
        delete_test_rows(driver, settings)
        try:
            yield driver, settings
        finally:
            delete_test_rows(driver, settings)


def count_test_nodes(driver, settings) -> int:
    rows, _, _ = driver.execute_query(
        "MATCH (n) WHERE n.graph_run_id = $run RETURN count(n) AS c",
        {"run": TEST_RUN_ID},
        database_=settings.resolved_database,
    )
    return rows[0]["c"]


def count_test_edges(driver, settings) -> int:
    rows, _, _ = driver.execute_query(
        "MATCH ()-[r]->() WHERE r.graph_run_id = $run RETURN count(r) AS c",
        {"run": TEST_RUN_ID},
        database_=settings.resolved_database,
    )
    return rows[0]["c"]


@pytest.mark.neo4j
def test_the_small_export_loads_every_row_in_every_group(live_graph):
    driver, settings = live_graph
    result = load_export(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)

    assert result.label_set_count == 12 and result.edge_group_count == 13
    assert result.nodes_submitted == result.nodes_written == len(SYNTHETIC_NODES)
    assert result.edges_submitted == result.edges_written == len(SYNTHETIC_EDGES)
    assert all(group.submitted == group.written for group in result.node_groups)
    assert all(group.submitted == group.written for group in result.edge_groups)
    assert count_test_nodes(driver, settings) == len(SYNTHETIC_NODES)
    assert count_test_edges(driver, settings) == len(SYNTHETIC_EDGES)


@pytest.mark.neo4j
def test_properties_and_labels_arrive_exactly_as_the_export_wrote_them(live_graph):
    """Property parity: nothing renamed, coerced, dropped or invented, empty list included."""
    driver, settings = live_graph
    load_export(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)

    rows, _, _ = driver.execute_query(
        "MATCH (n:Entity {entity_id: $key}) RETURN properties(n) AS p, labels(n) AS l",
        {"key": ACME.key},
        database_=settings.resolved_database,
    )
    assert rows[0]["p"] == ACME.properties
    assert set(rows[0]["l"]) == set(ACME.labels)

    edge = SYNTHETIC_EDGES[0]
    rows, _, _ = driver.execute_query(
        "MATCH ()-[r {edge_key: $key}]->() RETURN properties(r) AS p, type(r) AS t",
        {"key": edge.edge_key},
        database_=settings.resolved_database,
    )
    assert rows[0]["t"] == edge.type
    assert rows[0]["p"] == {**edge.properties, "edge_key": edge.edge_key}


@pytest.mark.neo4j
def test_loading_the_same_export_twice_changes_nothing(live_graph):
    """§6.5's idempotence check, on the small export."""
    driver, settings = live_graph
    first = load_export(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    nodes_after_first = count_test_nodes(driver, settings)
    edges_after_first = count_test_edges(driver, settings)
    properties_after_first, _, _ = driver.execute_query(
        "MATCH (n) WHERE n.graph_run_id = $run "
        "RETURN n.graph_run_id AS run, count(keys(n)) AS c, sum(size(keys(n))) AS total",
        {"run": TEST_RUN_ID},
        database_=settings.resolved_database,
    )

    second = load_export(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)

    assert second.nodes_written == first.nodes_written
    assert second.edges_written == first.edges_written
    assert count_test_nodes(driver, settings) == nodes_after_first == len(SYNTHETIC_NODES)
    assert count_test_edges(driver, settings) == edges_after_first == len(SYNTHETIC_EDGES)
    properties_after_second, _, _ = driver.execute_query(
        "MATCH (n) WHERE n.graph_run_id = $run "
        "RETURN n.graph_run_id AS run, count(keys(n)) AS c, sum(size(keys(n))) AS total",
        {"run": TEST_RUN_ID},
        database_=settings.resolved_database,
    )
    assert properties_after_second[0]["total"] == properties_after_first[0]["total"]


@pytest.mark.neo4j
def test_an_edge_whose_endpoint_is_missing_fails_the_load_and_names_the_edge_key(live_graph):
    """The load-bearing case. `MATCH` filters silently; only the count comparison catches it.

    The bad row is deliberately put in a batch beside a good one, so the assertion that the
    batch rolled back is meaningful: neither edge exists afterwards.
    """
    driver, settings = live_graph
    load_nodes(driver, settings, SYNTHETIC_NODES)

    ghost = GraphNode(
        key="test:loader:passage:never-loaded",
        base_label="Passage",
        labels=("Passage",),
        properties={"passage_id": "test:loader:passage:never-loaded"},
    )
    dangling = _edge("EVIDENCED_BY", OBSERVATION, ghost)
    good = SYNTHETIC_EDGES[1]

    with pytest.raises(ShortWriteError) as refused:
        load_edges(driver, settings, (good, dangling))

    assert dangling.edge_key in str(refused.value)
    assert refused.value.keys == (dangling.edge_key,)
    assert refused.value.missing_endpoints[0].target_missing is True
    assert refused.value.missing_endpoints[0].source_missing is False
    assert refused.value.submitted == 2 and refused.value.written == 1
    # The failing batch rolled back, so the *good* row is gone too — a partial group is never
    # left behind for the verification stage to find and misread as a complete one.
    assert count_test_edges(driver, settings) == 0
    assert count_test_nodes(driver, settings) == len(SYNTHETIC_NODES), "no endpoint was conjured"


@pytest.mark.neo4j
def test_two_node_rows_sharing_a_key_fail_the_load_on_the_live_server(live_graph):
    """The node-side short write, produced rather than stubbed: `count(DISTINCT n)` is 1 of 2."""
    driver, settings = live_graph
    twin = GraphNode(
        key=ISSUE.key,
        base_label="Issue",
        labels=("Issue",),
        properties={"issue_id": ISSUE.key, "graph_run_id": TEST_RUN_ID, "code": "OTHER"},
    )
    with pytest.raises(ShortWriteError) as refused:
        load_nodes(driver, settings, (ISSUE, twin))
    assert refused.value.keys == (ISSUE.key,)
    assert count_test_nodes(driver, settings) == 0, "the failing batch must roll back"


@pytest.mark.neo4j
def test_a_malformed_property_fails_before_a_single_row_is_written(live_graph, tmp_path):
    """The ordering claim, checked against the database rather than argued.

    The bad property is on the **last** node of the file, so a reader that validated lazily
    would have let twelve rows through before noticing.
    """
    driver, settings = live_graph
    directory = tmp_path / "export"
    write_export(directory)
    with (directory / NODES_FILENAME).open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "key": "test:loader:event:malformed",
                    "base_label": "Event",
                    "labels": ["Event"],
                    "properties": {
                        "event_id": "test:loader:event:malformed",
                        "graph_run_id": TEST_RUN_ID,
                        "amount": {"value": 1, "currency": "USD"},
                    },
                },
                sort_keys=True,
            )
            + "\n"
        )

    with pytest.raises(GraphExportReadError) as refused:
        contents = read_export(directory)
        load_export(driver, settings, contents.nodes, contents.edges)

    assert refused.value.property_name == "amount"
    assert count_test_nodes(driver, settings) == 0
