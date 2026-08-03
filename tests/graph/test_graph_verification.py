"""Post-load verification: the twenty-seven checks, and each one failing for its own reason.

Three kinds of test, split by what they need:

    unmarked   the report models, the expectation read off an export, and **every check's
               failure mode**, produced by handing `build_report` a doctored `DatabaseFacts`.
               No server, no driver.
    @neo4j     the live 5.26.28 Community server: a correct small load passing all twenty-seven,
               the breakages that only a database can produce, and the one test that
               matters most — that `read_database` measures what the pure tests simulate

`pytest -m "not live and not neo4j"` is green with no container running.

**The nine marked tests here skip whenever the database holds a run they did not write**, which
is the machine's normal state, so a green `pytest tests/graph` has not run them. That is the
correct behaviour — verification counts the *whole* database, so it cannot be measured beside
another run's rows — and it is stated here rather than left as a skip count for a reader to
notice. `FKG_GRAPH_TESTS_MAY_WIPE=1` makes the fixture wipe instead of skip; it destroys every
graph in the database and reloading afterwards is the operator's. See `test_lifecycle.py`'s
docstring and V1_GRAPH_PROTOTYPE §6.5.

**The real 28,836-node export is never loaded here.** This file writes a 14-node, 19-edge
synthetic export shaped like a miniature of it: §5.5's provenance block on every row, all
twelve §3.2 relationship types, all four secondary labels, an `:Unresolved` participant with
§4.2's per-event key, and two relationship-claim edges. Every row carries
`graph_run_id = "graph-test-verification"`, and cleanup deletes exactly that. Like
`test_loader.py`, this file never wipes the database — replacement is the lifecycle stage's
business — and the live fixture *skips* rather than deleting if it finds a run it did not
write.

**Why the failure tests are worth more than the passing one.** A verifier that returns
`passed=True` against a good load has proved nothing: so does `return True`. What is asserted
below is discrimination — break exactly one thing, and exactly the check that owns it fails,
naming what differed.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from graph.core.models import BASE_LABELS, GraphEdge, GraphNode
from graph.core.verification_report import MAX_EXAMPLES, Check, VerificationReport
from graph.stages.load.connection import (
    GraphSettings,
    MissingGraphCredentialError,
    UnresolvedGraphTargetError,
    driver_for,
    load_settings,
)
from graph.stages.load.lifecycle import (
    LOAD_MARKER_LABEL,
    RUN_ID_PROPERTY,
    LoadVerificationError,
    begin_load,
    graph_node_count,
    wipe,
)
from graph.stages.load.loader import load_export
from graph.stages.load.schema import apply_schema
from graph.stages.load.verification import (
    CONFLICT_GUARD_CODE,
    CONFLICT_GUARD_IDENTITY_FIELDS,
    EVENT_SCOPE_SEPARATOR,
    EXTRACTION_RUN_ID_PROPERTY,
    LOADER_OWNED_RELATIONSHIP_PROPERTIES,
    ONTOLOGY_HASH_PROPERTY,
    PROJECTION_VERSION_PROPERTY,
    PROVENANCE_PROPERTIES,
    RELATIONSHIP_CLAIM_ID_PREFIX,
    DatabaseFacts,
    EventDates,
    ObservationIdentity,
    Offenders,
    ProvenanceFact,
    UnresolvedEdge,
    build_report,
    citation_statements,
    content_digest,
    edge_content_of,
    expected_graph,
    node_content_of,
    node_keys_statement,
    raise_for_failures,
    read_database,
    refused_identities,
    verify_graph,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The same operator opt-in `test_lifecycle.py` reads, for the same nine-tests-skip reason —
#: see this file's docstring and V1_GRAPH_PROTOTYPE §6.5.
MAY_WIPE = os.environ.get("FKG_GRAPH_TESTS_MAY_WIPE", "") == "1"

TEST_RUN_ID = "graph-test-verification"
FOREIGN_RUN_ID = "graph-test-verification-foreign"
EXTRACTION_RUN_ID = "extract-test-verification"
ONTOLOGY_HASH = "0f0e0d0c0b0a09080706050403020100"
PROJECTION_VERSION = "1.1.0"

#: §5.5's provenance block, on every node and every edge of the synthetic export.
PROVENANCE: dict[str, Any] = {
    RUN_ID_PROPERTY: TEST_RUN_ID,
    EXTRACTION_RUN_ID_PROPERTY: EXTRACTION_RUN_ID,
    ONTOLOGY_HASH_PROPERTY: ONTOLOGY_HASH,
    PROJECTION_VERSION_PROPERTY: PROJECTION_VERSION,
}


# -- a small synthetic export, shaped like the real one --------------------------------------


def _node(key: str, labels: tuple[str, ...], **properties: Any) -> GraphNode:
    base = labels[0]
    return GraphNode(
        key=key,
        base_label=base,
        labels=labels,
        properties={f"{base.lower()}_id": key, **PROVENANCE, **properties},
    )


def _edge(
    relationship_type: str, source: GraphNode, target: GraphNode, **properties: Any
) -> GraphEdge:
    return GraphEdge(
        edge_key=f"{relationship_type}:{source.key}:{target.key}",
        type=relationship_type,
        source_key=source.key,
        source_base_label=source.base_label,
        target_key=target.key,
        target_base_label=target.base_label,
        properties={**PROVENANCE, **properties},
    )


DOCUMENT = _node("test:verify:doc", ("Document",), form="8-K")
PASSAGE = _node(
    "test:verify:doc#p1",
    ("Passage",),
    document_id=DOCUMENT.key,
    text="A subsidiary of the Company entered into a credit facility.",
)
EVENT_KEY = "evt:credit-facility-established:2022-10-19:test"
COMPANY = _node(
    "test:verify:opendoor",
    ("Entity", "PublicCompany", "Company"),
    entity_text="Opendoor Technologies Inc.",
    resolved=True,
)
PERSON = _node("test:verify:person", ("Entity", "Person"), entity_text="A. Person", resolved=True)
FACILITY = _node(
    "test:verify:facility", ("Entity", "CreditFacility"), entity_text="the facility", resolved=True
)
#: §4.2's per-event key. The `#evt:` scope is the whole guarantee, and criterion 9 reads it.
UNNAMED = _node(
    f"test:verify:unnamed_subsidiary{EVENT_SCOPE_SEPARATOR}{EVENT_KEY}",
    ("Entity", "Subsidiary", "Company", "Unresolved"),
    entity_text="a subsidiary of the Company",
    resolved=False,
    scoped_to_event_id=EVENT_KEY,
)
EVENT = _node(
    EVENT_KEY,
    ("Event",),
    event_type_id="credit_facility_established",
    claim_id="claim:event:test0001",
    passage_id=PASSAGE.key,
    document_id=DOCUMENT.key,
    occurred_on="2022-10-19",
)
REVENUE = _node("test:verify:revenue", ("Metric",), label="Revenue")
CONTRIBUTION = _node("test:verify:contribution", ("Metric",), label="Contribution profit")
OBSERVATION = _node(
    "test:verify:obs:1",
    ("Observation",),
    claim_id="claim:metric-observation:test0001",
    passage_id=PASSAGE.key,
    document_id=DOCUMENT.key,
    value=1234.5,
    metric_id="test:verify:revenue",
    period_key="2022Q3",
    source_lane="normalized_table",
)
WARNED = _node(
    "test:verify:obs:2",
    ("Observation", "Warned"),
    claim_id="claim:metric-observation:test0002",
    passage_id=PASSAGE.key,
    document_id=DOCUMENT.key,
    value=7.0,
    metric_id="test:verify:revenue",
    period_key="2022Q3",
    source_lane="normalized_narrative",
    validation_state="warned",
    warning_codes=["unpreferred_source_lane"],
)
#: §10.10's conflict guard, in miniature. The refused reading's `(metric_id, period_key)` lives
#: only inside `detail` — `concept_ids` is empty on all five of the real run's issues — so the
#: sentence shape here is the extractor's own, and the period it names (`2022Q4`) is deliberately
#: *not* the period the two observations above carry: a passage may hold both a refused reading
#: and accepted ones, and §10.10 is about telling them apart rather than about the passage.
ISSUE = _node(
    "test:verify:issue:1",
    ("Issue",),
    code=CONFLICT_GUARD_CODE,
    passage_id=PASSAGE.key,
    document_id=DOCUMENT.key,
    detail=(
        "'Revenue': 2 cells of one row resolve to test:verify:revenue at 2022Q4 with different "
        "values, so the column mapping is not uniquely supported"
    ),
)
NOT_ATTEMPTED = _node(
    "test:verify:issue:2",
    ("Issue", "NotAttempted"),
    code="NO_STORED_ANSWER",
    passage_id=PASSAGE.key,
    document_id=DOCUMENT.key,
)
REJECTED = _node(
    "test:verify:issue:3",
    ("Issue", "Rejected"),
    code="REJECTED_BY_VALIDATION",
    passage_id=PASSAGE.key,
    document_id=DOCUMENT.key,
)

SYNTHETIC_NODES: tuple[GraphNode, ...] = (
    DOCUMENT,
    PASSAGE,
    COMPANY,
    PERSON,
    FACILITY,
    UNNAMED,
    EVENT,
    REVENUE,
    CONTRIBUTION,
    OBSERVATION,
    WARNED,
    ISSUE,
    NOT_ATTEMPTED,
    REJECTED,
)

#: All twelve §3.2 types. The two relationship-claim edges carry a `claim:relationship:` id,
#: which is how criterion 6 finds them; the two vocabulary-sourced ones carry none, which is
#: §10.4's corrected second branch.
SYNTHETIC_EDGES: tuple[GraphEdge, ...] = (
    _edge("PART_OF", PASSAGE, DOCUMENT, document_id=DOCUMENT.key),
    _edge("FOUND_IN", ISSUE, PASSAGE, code=ISSUE.properties["code"], passage_id=PASSAGE.key),
    _edge("FOUND_IN", NOT_ATTEMPTED, PASSAGE, code="NO_STORED_ANSWER", passage_id=PASSAGE.key),
    _edge("FOUND_IN", REJECTED, PASSAGE, code="REJECTED_BY_VALIDATION", passage_id=PASSAGE.key),
    _edge("CONCERNS_METRIC", ISSUE, REVENUE),
    _edge("HAS_OBSERVATION", REVENUE, OBSERVATION, claim_id=OBSERVATION.properties["claim_id"]),
    _edge("HAS_OBSERVATION", REVENUE, WARNED, claim_id=WARNED.properties["claim_id"]),
    _edge("OBSERVATION_OF_SUBJECT", OBSERVATION, COMPANY),
    _edge("OBSERVATION_OF_SUBJECT", WARNED, COMPANY),
    _edge("EVIDENCED_BY", OBSERVATION, PASSAGE, passage_id=PASSAGE.key, quoted_text="1,234.5"),
    _edge("EVIDENCED_BY", WARNED, PASSAGE, passage_id=PASSAGE.key, quoted_text="7.0"),
    _edge("EVIDENCED_BY", EVENT, PASSAGE, passage_id=PASSAGE.key, quoted_text="entered into"),
    _edge("PARTICIPATES_IN", COMPANY, EVENT, role="counterparty"),
    _edge("PARTICIPATES_IN", UNNAMED, EVENT, role="borrower"),
    _edge("PLACEHOLDER_FOR", UNNAMED, COMPANY, resolved=False, role="borrower"),
    _edge(
        "BORROWS_UNDER",
        UNNAMED,
        FACILITY,
        claim_id=f"{RELATIONSHIP_CLAIM_ID_PREFIX}test0001",
        passage_id=PASSAGE.key,
        document_id=DOCUMENT.key,
    ),
    _edge(
        "HOLDS_POSITION_AT",
        PERSON,
        COMPANY,
        claim_id=f"{RELATIONSHIP_CLAIM_ID_PREFIX}test0002",
        passage_id=PASSAGE.key,
        document_id=DOCUMENT.key,
    ),
    _edge("DISTINCT_FROM", REVENUE, CONTRIBUTION, assertion="ontology_definition"),
    _edge("RECONCILES_TO", CONTRIBUTION, REVENUE, assertion="ontology_definition"),
)

EXPECTED = expected_graph(SYNTHETIC_NODES, SYNTHETIC_EDGES)


# -- the database a correct load of that export would hold ------------------------------------


def facts_from(
    nodes: tuple[GraphNode, ...] = SYNTHETIC_NODES,
    edges: tuple[GraphEdge, ...] = SYNTHETIC_EDGES,
) -> DatabaseFacts:
    """What `read_database` should measure after a correct load — simulated, not read.

    Every unmarked failure test below starts here and breaks one field. The simulation is not
    taken on trust: `test_the_live_read_measures_what_the_simulation_claims` asserts it equals
    what the real server returns for the same export.
    """
    node_keys_by_label = {
        label: tuple(node.key for node in nodes if node.base_label == label)
        for label in BASE_LABELS
    }
    evidenced = {edge.source_key for edge in edges if edge.type == "EVIDENCED_BY"}
    unevidenced = tuple(
        node.key
        for node in nodes
        if node.base_label in ("Observation", "Event") and node.key not in evidenced
    )
    unresolved_keys = {node.key for node in nodes if "Unresolved" in node.labels}
    provenance = {}
    for name in PROVENANCE_PROPERTIES:
        values = {
            str(row.properties[name])
            for row in (*nodes, *edges)
            if name in row.properties
        }
        provenance[name] = ProvenanceFact(
            values=frozenset(values),
            missing_nodes=sum(1 for node in nodes if name not in node.properties),
            missing_relationships=sum(1 for edge in edges if name not in edge.properties),
        )
    return DatabaseFacts(
        node_count=len(nodes),
        relationship_count=len(edges),
        label_counts=dict(Counter(label for node in nodes for label in node.labels)),
        type_counts=dict(Counter(edge.type for edge in edges)),
        node_keys=node_keys_by_label,
        relationship_claim_edge_keys=tuple(
            edge.edge_key
            for edge in edges
            if str(edge.properties.get("claim_id", "")).startswith(RELATIONSHIP_CLAIM_ID_PREFIX)
        ),
        endpoint_shape=Offenders(),
        marker_relationships=Offenders(),
        unevidenced=Offenders(count=len(unevidenced), examples=unevidenced[:MAX_EXAMPLES]),
        unresolved_entities=tuple(
            (node.key, node.properties.get("resolved"))
            for node in nodes
            if "Unresolved" in node.labels
        ),
        unresolved_edges=tuple(
            UnresolvedEdge(
                type=edge.type,
                edge_key=edge.edge_key,
                claim_id=edge.properties.get("claim_id"),
            )
            for edge in edges
            if edge.source_key in unresolved_keys or edge.target_key in unresolved_keys
        ),
        unresolved_passage_citations=Offenders(),
        unresolved_document_citations=Offenders(),
        provenance=provenance,
        node_property_keys=frozenset(
            key for node in nodes for key in node.properties
        ),
        relationship_property_keys=frozenset(
            key for edge in edges for key in edge.properties
        )
        | frozenset(LOADER_OWNED_RELATIONSHIP_PROPERTIES),
        # A faithful load stores exactly what the export carries, so the digests a correct
        # database would produce are the export's own — computed through the same two functions
        # `expected_graph` uses, which is what makes a *value* difference show up as a
        # difference rather than as two independently-wrong numbers agreeing.
        node_content=frozenset(node_content_of(node) for node in nodes),
        relationship_content=frozenset(edge_content_of(edge) for edge in edges),
        event_dates=tuple(
            EventDates(
                key=node.key,
                announced_on=node.properties.get("announced_on"),
                occurred_on=node.properties.get("occurred_on"),
            )
            for node in nodes
            if node.base_label == "Event"
        ),
        observation_identities=tuple(
            ObservationIdentity(
                key=node.key,
                fields=tuple(
                    (name, node.properties.get(name))
                    for name in CONFLICT_GUARD_IDENTITY_FIELDS
                ),
            )
            for node in nodes
            if node.base_label == "Observation"
        ),
    )


def dataclasses_replace(facts: DatabaseFacts, **broken: Any) -> DatabaseFacts:
    import dataclasses

    return dataclasses.replace(facts, **broken)


def report_with(**broken: Any) -> VerificationReport:
    """A report over the correct facts with named fields replaced. One breakage per call."""
    return build_report(EXPECTED, dataclasses_replace(facts_from(), **broken))


def failed_names(report: VerificationReport) -> set[str]:
    return {check.name for check in report.failures}


# -- the report models, with no database and no export ----------------------------------------


def test_the_report_model_imports_no_driver():
    """`graph/core/verification_report.py` is the half of this stage that must stay database-free.

    A subprocess rather than an assertion about `sys.modules` in this process: the test suite
    imports the driver several times over, so the only honest way to ask "does importing the
    report drag a driver in" is to import it somewhere nothing else has.
    """
    probe = (
        "import sys; import graph.core.verification_report as m; "
        "assert 'neo4j' not in sys.modules, sorted(k for k in sys.modules if 'neo4j' in k); "
        "print(m.Check.comparing('x', expected=1, actual=1).passed)"
    )
    finished = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == "True"


def test_a_scalar_check_names_expected_and_actual():
    check = Check.comparing("total_node_count", expected=28836, actual=28835)
    assert not check.passed
    assert check.expected == "28836" and check.actual == "28835"
    assert "expected: 28836" in check.describe()


def test_a_count_check_names_the_labels_that_differ_and_not_the_ones_that_match():
    check = Check.comparing_counts(
        "node_counts_by_base_label",
        expected={"Issue": 17127, "Event": 6, "Metric": 26},
        actual={"Issue": 17127, "Event": 5, "Metric": 26},
        subject="labels",
    )
    assert not check.passed
    assert check.detail == "Event: expected 6, found 5"
    assert "Metric" not in check.detail


def test_a_count_check_reports_an_absent_key_as_zero():
    check = Check.comparing_counts(
        "relationship_counts_by_type",
        expected={"BORROWS_UNDER": 1},
        actual={},
        subject="types",
    )
    assert not check.passed and check.detail == "BORROWS_UNDER: expected 1, found 0"


def test_a_set_check_names_missing_and_unexpected_keys():
    check = Check.comparing_sets(
        "issues_present",
        expected=["a", "b", "c"],
        actual=["a", "b", "d"],
        subject="issue keys",
    )
    assert not check.passed
    assert check.expected == "3 issue keys" and check.actual == "3 issue keys"
    assert "missing 1: 'c'" in check.detail and "unexpected 1: 'd'" in check.detail


def test_a_set_check_caps_the_examples_it_names():
    check = Check.comparing_sets(
        "issues_present",
        expected=[f"k{index}" for index in range(20)],
        actual=[],
        subject="issue keys",
    )
    assert "and 15 more" in check.detail
    assert check.detail.count("'k") == MAX_EXAMPLES


def test_a_forbidding_check_passes_only_on_zero():
    assert Check.forbidding("x", subject="ghosts", found=0).passed
    check = Check.forbidding("x", subject="ghosts", found=3, examples=("a", "b"))
    assert not check.passed and check.actual == "3 ghosts" and "'a', 'b'" in check.detail


def test_a_report_cannot_claim_success_while_carrying_a_failure():
    report = VerificationReport(
        graph_run_id="r",
        node_count=1,
        relationship_count=0,
        expected_node_count=1,
        expected_relationship_count=0,
        checks=(
            Check.comparing("a", expected=1, actual=1),
            Check.comparing("b", expected=1, actual=2),
        ),
    )
    assert not report.passed
    assert failed_names(report) == {"b"}
    assert report.check("a").passed
    with pytest.raises(KeyError):
        report.check("never_ran")
    assert "FAILED" in report.describe() and "1 of 2 checks failed" in report.describe()


def test_raise_for_failures_names_every_failing_check():
    report = report_with(node_count=3, relationship_count=4)
    with pytest.raises(LoadVerificationError) as refused:
        raise_for_failures(report)
    message = str(refused.value)
    assert "total_node_count" in message and "total_relationship_count" in message
    assert TEST_RUN_ID in message


def test_raise_for_failures_returns_the_report_when_everything_held():
    report = build_report(EXPECTED, facts_from())
    assert raise_for_failures(report) is report


# -- the expectation, read off the export ------------------------------------------------------


def test_the_expectation_is_derived_from_the_export_and_not_declared():
    assert EXPECTED.node_count == len(SYNTHETIC_NODES) == 14
    assert EXPECTED.edge_count == len(SYNTHETIC_EDGES) == 19
    assert EXPECTED.node_counts_by_base_label == {
        "Document": 1, "Passage": 1, "Entity": 4, "Event": 1,
        "Metric": 2, "Observation": 2, "Issue": 3,
    }
    assert EXPECTED.secondary_label_counts["Warned"] == 1
    assert EXPECTED.secondary_label_counts["Unresolved"] == 1
    assert set(EXPECTED.edge_counts_by_type) == {edge.type for edge in SYNTHETIC_EDGES}
    assert len(EXPECTED.edge_counts_by_type) == 12, "all twelve §3.2 types are covered"
    assert EXPECTED.graph_run_id == TEST_RUN_ID
    assert EXPECTED.provenance[ONTOLOGY_HASH_PROPERTY] == frozenset({ONTOLOGY_HASH})
    assert len(EXPECTED.relationship_claim_edge_keys) == 2


def test_a_correct_load_passes_every_check_with_no_database():
    report = build_report(EXPECTED, facts_from())
    assert report.passed, report.describe()
    assert len(report.checks) == 27
    assert len({check.name for check in report.checks}) == 27, "check names are unique"


def test_the_refused_reading_identity_is_read_off_the_export_s_own_issue_rows():
    """§10.10's expectation is derived, not declared: a verifier holding four hard-coded tuples
    would pass a load of a different run."""
    assert EXPECTED.refused_reading_identities == (
        ("test:verify:revenue", "2022Q4", PASSAGE.key),
    )
    assert EXPECTED.observations_on_refused_passages == {OBSERVATION.key, WARNED.key}
    # An issue of the same code whose `detail` names no identity yields none — which is why the
    # real run has four identities from five conflict-guard issues.
    vague = ISSUE.model_copy(
        update={"properties": {**ISSUE.properties,
                               "detail": "duration groups cannot be assigned to columns uniquely"}}
    )
    assert refused_identities((vague,)) == ()


# -- one breakage each: the discrimination the verifier exists for ----------------------------


def test_a_missing_node_fails_the_total_and_names_the_two_numbers():
    report = report_with(node_count=len(SYNTHETIC_NODES) - 1)
    assert "total_node_count" in failed_names(report)
    check = report.check("total_node_count")
    assert check.expected == "14" and check.actual == "13"


def test_a_missing_relationship_fails_the_relationship_total():
    report = report_with(relationship_count=len(SYNTHETIC_EDGES) - 1)
    assert "total_relationship_count" in failed_names(report)


def test_a_label_count_that_drifted_names_the_label():
    counts = dict(facts_from().label_counts)
    counts["Observation"] = 1
    report = report_with(label_counts=counts)
    assert "node_counts_by_base_label" in failed_names(report)
    assert report.check("node_counts_by_base_label").detail == (
        "Observation: expected 2, found 1"
    )


def test_a_secondary_label_that_drifted_names_the_label():
    counts = dict(facts_from().label_counts)
    counts["Warned"] = 0
    report = report_with(label_counts=counts)
    assert failed_names(report) >= {"node_counts_by_secondary_label", "warned_nodes_match_export"}
    assert "Warned: expected 1, found 0" in report.check(
        "node_counts_by_secondary_label"
    ).detail
    warned = report.check("warned_nodes_match_export")
    assert warned.expected == "1" and warned.actual == "0"


def test_a_relationship_type_count_that_drifted_names_the_type():
    counts = dict(facts_from().type_counts)
    counts["EVIDENCED_BY"] = 2
    report = report_with(type_counts=counts)
    assert "relationship_counts_by_type" in failed_names(report)
    assert report.check("relationship_counts_by_type").detail == (
        "EVIDENCED_BY: expected 3, found 2"
    )


def test_an_endpoint_the_export_named_and_the_database_lacks_is_dangling():
    """The failure `MATCH` cannot raise: an edge whose endpoint never loaded (§6.3, §10.2)."""
    keys = dict(facts_from().node_keys)
    keys["Metric"] = (CONTRIBUTION.key,)
    report = report_with(node_keys=keys)
    assert "no_dangling_relationships" in failed_names(report)
    check = report.check("no_dangling_relationships")
    assert check.expected.startswith("0 ") and REVENUE.key in check.detail


def test_a_relationship_whose_endpoint_is_not_a_data_node_is_dangling():
    report = report_with(endpoint_shape=Offenders(count=1, examples=("PART_OF:a:b",)))
    assert "no_dangling_relationships" in failed_names(report)
    assert "PART_OF:a:b" in report.check("no_dangling_relationships").detail


def test_a_relationship_attached_to_the_loader_s_marker_is_dangling():
    """The `:GraphLoad` exclusion rests on the marker being isolated, so that is checked too."""
    report = report_with(marker_relationships=Offenders(count=1, examples=("LOADED_BY",)))
    assert "no_dangling_relationships" in failed_names(report)


def test_a_fact_with_no_evidence_fails_and_names_it():
    report = report_with(unevidenced=Offenders(count=1, examples=(OBSERVATION.key,)))
    assert "observations_and_events_are_evidenced" in failed_names(report)
    check = report.check("observations_and_events_are_evidenced")
    assert check.actual == "1 facts with no EVIDENCED_BY" and OBSERVATION.key in check.detail


def test_a_duplicated_observation_key_fails_even_though_the_set_matches():
    """§5.2's constraint should make this impossible; the check is what notices if it is gone."""
    keys = dict(facts_from().node_keys)
    keys["Observation"] = keys["Observation"] + (OBSERVATION.key,)
    report = report_with(node_keys=keys)
    assert "observation_keys_present_exactly_once" in failed_names(report)
    assert "present more than once" in report.check(
        "observation_keys_present_exactly_once"
    ).detail


def test_a_missing_event_key_fails_the_event_check_naming_the_key():
    keys = dict(facts_from().node_keys)
    keys["Event"] = ()
    report = report_with(node_keys=keys)
    assert "event_keys_present_exactly_once" in failed_names(report)
    assert EVENT_KEY in report.check("event_keys_present_exactly_once").detail


def test_a_lost_relationship_claim_edge_fails_its_own_check():
    facts = facts_from()
    report = report_with(relationship_claim_edge_keys=facts.relationship_claim_edge_keys[:1])
    assert "relationship_claim_edges_present_exactly_once" in failed_names(report)
    check = report.check("relationship_claim_edges_present_exactly_once")
    assert check.expected == "2 relationship-claim edges" and "missing 1" in check.detail


def test_a_missing_issue_fails_and_names_the_issue_id():
    keys = dict(facts_from().node_keys)
    keys["Issue"] = (ISSUE.key,)
    report = report_with(node_keys=keys)
    assert "issues_present" in failed_names(report)
    assert NOT_ATTEMPTED.key in report.check("issues_present").detail


def test_a_citation_of_an_absent_passage_or_document_fails_its_own_check():
    report = report_with(
        unresolved_passage_citations=Offenders(count=2, examples=("doc#p9",)),
        unresolved_document_citations=Offenders(count=1, examples=("doc",)),
    )
    assert failed_names(report) == {"cited_passages_present", "cited_documents_present"}
    assert "doc#p9" in report.check("cited_passages_present").detail


def test_an_unresolved_entity_without_a_per_event_key_fails():
    report = report_with(unresolved_entities=(("unnamed_subsidiary", False),))
    assert "unresolved_entities_are_scoped_per_event" in failed_names(report)
    assert "unnamed_subsidiary" in report.check(
        "unresolved_entities_are_scoped_per_event"
    ).detail


def test_an_unresolved_entity_claiming_to_be_resolved_fails():
    report = report_with(unresolved_entities=((UNNAMED.key, True),))
    assert "unresolved_entities_are_scoped_per_event" in failed_names(report)
    assert "resolved is not false" in report.check(
        "unresolved_entities_are_scoped_per_event"
    ).detail


@pytest.mark.parametrize("relationship_type", ["SUBSIDIARY_OF", "HAS_SUBSIDIARY", "ACQUIRED"])
def test_an_identity_asserting_edge_on_an_unresolved_entity_fails(relationship_type):
    """§10.7. A claim id does not authorise it: the filing named a role, not a company."""
    edges = facts_from().unresolved_edges + (
        UnresolvedEdge(
            type=relationship_type,
            edge_key=f"{relationship_type}:x:y",
            claim_id=f"{RELATIONSHIP_CLAIM_ID_PREFIX}whatever",
        ),
    )
    report = report_with(unresolved_edges=edges)
    assert "unresolved_entities_assert_no_identity" in failed_names(report)
    detail = report.check("unresolved_entities_assert_no_identity").detail
    assert relationship_type in detail and "asserts an identity" in detail


def test_an_unclaimed_edge_on_an_unresolved_entity_fails_even_if_it_is_not_identity_asserting():
    """The rule is an allowlist: a predicate nobody has considered fails rather than passes."""
    edges = facts_from().unresolved_edges + (
        UnresolvedEdge(type="LENDS_UNDER", edge_key="LENDS_UNDER:x:y", claim_id=None),
    )
    report = report_with(unresolved_edges=edges)
    assert "unresolved_entities_assert_no_identity" in failed_names(report)
    assert "neither" in report.check("unresolved_entities_assert_no_identity").detail


def test_the_three_edges_the_real_run_puts_on_its_unresolved_entity_are_allowed():
    """PLACEHOLDER_FOR, PARTICIPATES_IN and one relationship claim — the measured shape."""
    assert build_report(EXPECTED, facts_from()).check(
        "unresolved_entities_assert_no_identity"
    ).passed
    assert {edge.type for edge in facts_from().unresolved_edges} == {
        "PLACEHOLDER_FOR", "PARTICIPATES_IN", "BORROWS_UNDER"
    }


def test_two_graph_run_ids_fail_the_identity_check_naming_both():
    provenance = dict(facts_from().provenance)
    provenance[RUN_ID_PROPERTY] = ProvenanceFact(
        values=frozenset({TEST_RUN_ID, FOREIGN_RUN_ID})
    )
    report = report_with(provenance=provenance)
    assert "graph_run_id_is_consistent" in failed_names(report)
    check = report.check("graph_run_id_is_consistent")
    assert TEST_RUN_ID in check.actual and FOREIGN_RUN_ID in check.actual
    assert check.expected == TEST_RUN_ID


def test_a_row_carrying_no_run_id_fails_even_when_the_value_set_matches():
    provenance = dict(facts_from().provenance)
    provenance[RUN_ID_PROPERTY] = ProvenanceFact(
        values=frozenset({TEST_RUN_ID}), missing_nodes=1, missing_relationships=2
    )
    report = report_with(provenance=provenance)
    check = report.check("graph_run_id_is_consistent")
    assert not check.passed
    assert "1 node(s) and 2 relationship(s) carry no graph_run_id" in check.detail


def test_a_second_extraction_run_id_fails_its_own_check():
    provenance = dict(facts_from().provenance)
    provenance[EXTRACTION_RUN_ID_PROPERTY] = ProvenanceFact(
        values=frozenset({EXTRACTION_RUN_ID, "extract-somebody-else"})
    )
    report = report_with(provenance=provenance)
    assert failed_names(report) == {"extraction_run_id_is_consistent"}


def test_an_ontology_hash_that_is_not_the_export_s_fails():
    provenance = dict(facts_from().provenance)
    provenance[ONTOLOGY_HASH_PROPERTY] = ProvenanceFact(values=frozenset({"deadbeef"}))
    report = report_with(provenance=provenance)
    assert failed_names(report) == {"ontology_definition_hash_matches_export"}
    check = report.check("ontology_definition_hash_matches_export")
    assert check.expected == ONTOLOGY_HASH and check.actual == "deadbeef"


def test_a_projection_version_that_is_not_the_export_s_fails():
    provenance = dict(facts_from().provenance)
    provenance[PROJECTION_VERSION_PROPERTY] = ProvenanceFact(values=frozenset({"9.9.9"}))
    report = report_with(provenance=provenance)
    assert failed_names(report) == {"graph_projection_version_matches_export"}


def test_a_property_the_export_never_wrote_fails_property_parity_naming_it():
    report = report_with(
        node_property_keys=facts_from().node_property_keys | {"invented_by_somebody"}
    )
    assert "node_property_keys_match_export" in failed_names(report)
    assert "unexpected 1: 'invented_by_somebody'" in report.check(
        "node_property_keys_match_export"
    ).detail


def test_a_property_the_export_wrote_and_the_database_lacks_fails_the_other_direction():
    report = report_with(
        node_property_keys=facts_from().node_property_keys - {"period_key"}
    )
    check = report.check("node_property_keys_match_export")
    assert not check.passed and "missing 1: 'period_key'" in check.detail


def test_a_property_whose_value_changed_fails_parity_even_though_every_key_is_still_there():
    """The failure the twenty-two checks that came before it could not see. `SET n += row.properties` writing 999 where
    the export said 1234.5 leaves every count, every key and every name exactly as expected."""
    altered = OBSERVATION.model_copy(
        update={"properties": {**OBSERVATION.properties, "value": 999.0}}
    )
    facts = facts_from()
    report = report_with(
        node_content=(facts.node_content - {node_content_of(OBSERVATION)})
        | {node_content_of(altered)}
    )
    assert failed_names(report) == {"node_property_values_match_export"}
    check = report.check("node_property_values_match_export")
    # The key is inside the digest string, so the row that differs is named on both sides.
    assert check.detail.count(OBSERVATION.key) == 2
    assert "missing 1" in check.detail and "unexpected 1" in check.detail
    # …and every key-name check still passes, which is the point of the new one.
    assert report.check("node_property_keys_match_export").passed


def test_one_relationship_losing_one_property_fails_value_parity():
    """`relationship_property_keys_match_export` compares the *union* of names, so an edge that
    lost a property passes as long as any other edge still carries it. This does not."""
    intact = SYNTHETIC_EDGES[9]  # EVIDENCED_BY, carries quoted_text — as do two others
    stripped = intact.model_copy(
        update={"properties": {k: v for k, v in intact.properties.items() if k != "quoted_text"}}
    )
    facts = facts_from()
    report = report_with(
        relationship_content=(facts.relationship_content - {edge_content_of(intact)})
        | {edge_content_of(stripped)}
    )
    assert failed_names(report) == {"relationship_property_values_match_export"}
    assert report.check("relationship_property_keys_match_export").passed


def test_two_identical_property_maps_digest_the_same_whatever_order_they_arrive_in():
    """The digest must not report a difference that is only a key ordering: the export writes
    properties sorted and `properties(n)` returns them in the server's own order."""
    forwards = {"a": 1, "b": [1, 2], "c": "x", "d": 1.5, "e": True}
    backwards = {name: forwards[name] for name in reversed(list(forwards))}
    assert content_digest(forwards) == content_digest(backwards)
    assert content_digest(forwards) != content_digest({**forwards, "d": 1.6})
    # An int and a float of the same magnitude are different values and must digest differently.
    assert content_digest({"v": 7}) != content_digest({"v": 7.0})


def test_an_event_whose_dates_were_collapsed_fails_the_announcement_check():
    """§10.6. The event carries `occurred_on` and no `announced_on`; a load that filled the
    second in from the first would be asserting the filing said something it did not."""
    facts = facts_from()
    collapsed = EventDates(
        key=EVENT_KEY, announced_on=EVENT.properties["occurred_on"],
        occurred_on=EVENT.properties["occurred_on"],
    )
    report = report_with(event_dates=(collapsed,))
    assert "event_dates_preserved_independently" in failed_names(report)
    detail = report.check("event_dates_preserved_independently").detail
    assert "announced_on=None" in detail and "announced_on='2022-10-19'" in detail
    # A dropped event is visible here too, rather than only in the totals.
    assert not build_report(EXPECTED, dataclasses_replace(facts, event_dates=())).check(
        "event_dates_preserved_independently"
    ).passed


def test_an_observation_carrying_a_refused_identity_fails_and_names_it():
    """§10.10, the criterion the plan writes as a *post-load* check for exactly this reason: the
    input is clean, so what this catches is a projection bug that manufactured a reading the
    extractor refused."""
    metric_id, period_key, passage_id = EXPECTED.refused_reading_identities[0]
    resurrected = ObservationIdentity(
        key="test:verify:obs:resurrected",
        fields=(("metric_id", metric_id), ("period_key", period_key), ("passage_id", passage_id)),
    )
    report = report_with(
        observation_identities=facts_from().observation_identities + (resurrected,)
    )
    assert "refused_readings_are_not_resurrected" in failed_names(report)
    check = report.check("refused_readings_are_not_resurrected")
    assert "test:verify:obs:resurrected" in check.detail and period_key in check.detail


def test_suppressing_the_accepted_readings_on_that_passage_fails_the_converse():
    """A projection that emitted nothing at all on the refused passage would satisfy the check
    above perfectly. §10.5's converse says a refusal is per reading, never per node."""
    report = report_with(
        observation_identities=tuple(
            identity
            for identity in facts_from().observation_identities
            if identity.key != OBSERVATION.key
        )
    )
    assert "observations_on_refused_passages_are_present" in failed_names(report)
    assert OBSERVATION.key in report.check(
        "observations_on_refused_passages_are_present"
    ).detail


def test_the_loader_s_own_edge_key_is_documented_rather_than_reported_as_an_invention():
    """`edge_key` is set by `loader.edge_statement`'s MERGE and is in no `properties` map."""
    assert "edge_key" not in EXPECTED.relationship_property_keys
    assert LOADER_OWNED_RELATIONSHIP_PROPERTIES == ("edge_key",)
    passing = build_report(EXPECTED, facts_from())
    assert passing.check("relationship_property_keys_match_export").passed
    report = report_with(
        relationship_property_keys=facts_from().relationship_property_keys - {"edge_key"}
    )
    assert not report.check("relationship_property_keys_match_export").passed


# -- the real export, read but never loaded ----------------------------------------------------

#: The finalized G1 projection. Gitignored, so these skip when it is absent rather than failing
#: — the convention `tests/graph/conftest.py` and `test_loader.py` already use. **Read only.**
#: 28,836 nodes through the strict reader and the expectation costs 1.8 s and no database
#: *(measured 2026-08-03)*; loading it is not this file's business.
REAL_EXPORT = REPO_ROOT / "data" / "graph_runs" / "graph-v1-886059d862ce"
REAL_EXPORT_AVAILABLE = (REAL_EXPORT / "nodes.jsonl").is_file()

real_export_only = pytest.mark.skipif(
    not REAL_EXPORT_AVAILABLE,
    reason=f"{REAL_EXPORT} is absent — data/ is gitignored",
)


@pytest.fixture(scope="module")
def real_export():
    if not REAL_EXPORT_AVAILABLE:  # pragma: no cover - environment-dependent
        pytest.skip(f"{REAL_EXPORT} is absent")
    from graph.stages.load.reader import read_export

    contents = read_export(REAL_EXPORT)
    return contents.nodes, contents.edges


@real_export_only
def test_the_expectation_read_off_the_real_export_is_the_run_s_own_manifest(real_export):
    """The numbers the brief quotes, derived rather than typed in — including the four
    relationship-claim edges and the 186 `:Warned` observations §10.11 asks for by name."""
    expected = expected_graph(*real_export)
    assert (expected.node_count, expected.edge_count) == (28836, 35603)
    assert expected.node_counts_by_base_label == {
        "Issue": 17127, "Passage": 8776, "Observation": 2707,
        "Document": 185, "Metric": 26, "Entity": 9, "Event": 6,
    }
    assert expected.edge_counts_by_type == {
        "FOUND_IN": 17127, "PART_OF": 8776, "EVIDENCED_BY": 2713, "HAS_OBSERVATION": 2707,
        "OBSERVATION_OF_SUBJECT": 2707, "CONCERNS_METRIC": 1520, "DISTINCT_FROM": 36,
        "PARTICIPATES_IN": 10, "HOLDS_POSITION_AT": 3, "RECONCILES_TO": 2,
        "BORROWS_UNDER": 1, "PLACEHOLDER_FOR": 1,
    }
    assert expected.secondary_label_counts["Warned"] == 186
    assert expected.secondary_label_counts["Unresolved"] == 1
    assert len(expected.relationship_claim_edge_keys) == 4
    assert expected.graph_run_id == "graph-v1-886059d862ce"
    assert expected.provenance[PROJECTION_VERSION_PROPERTY] == frozenset({"1.1.0"})


@real_export_only
def test_the_real_export_would_pass_every_check_if_it_loaded_faithfully(real_export):
    """The checks are run against the real run's *shape* without touching the database.

    This is what catches a check that only works on a fourteen-node fixture — the per-event
    unresolved key, the three edges allowed on it, the 2,707 observations each evidenced, the
    114 node property keys. What it cannot prove is that the Cypher measures those things; the
    marked tests do that on a small load, and the real load is the operator's to run.
    """
    nodes, edges = real_export
    report = build_report(expected_graph(nodes, edges), facts_from(nodes, edges))
    assert report.passed, report.describe()
    assert report.node_count == 28836 and report.relationship_count == 35603


# -- statement construction -------------------------------------------------------------------


def test_a_node_key_statement_is_built_only_from_the_declared_base_labels():
    assert "MATCH (n:Issue)" in node_keys_statement("Issue")
    assert "n.issue_id AS key" in node_keys_statement("Issue")
    assert f"NOT n:{LOAD_MARKER_LABEL}" in node_keys_statement("Issue")
    with pytest.raises(ValueError):
        node_keys_statement("Warned")
    with pytest.raises(ValueError):
        node_keys_statement("Issue) DETACH DELETE (n")


def test_every_node_statement_excludes_the_loader_s_marker():
    """The one exclusion the whole verification rests on, asserted over the statements."""
    from graph.stages.load import verification

    node_statements = [
        verification.TOTAL_NODES_STATEMENT,
        verification.LABEL_COUNTS_STATEMENT,
        verification.NODE_PROPERTY_KEYS_STATEMENT,
        verification.UNEVIDENCED_STATEMENT,
        verification.UNRESOLVED_ENTITIES_STATEMENT,
        *(node_keys_statement(label) for label in BASE_LABELS),
        citation_statements("Passage")[0],
        citation_statements("Document")[0],
    ]
    excludes_marker = re.compile(rf"NOT \w+:{LOAD_MARKER_LABEL}\b")
    for statement in node_statements:
        assert excludes_marker.search(statement), statement


def test_no_statement_calls_apoc():
    from graph.stages.load import verification

    statements = [
        value
        for name, value in vars(verification).items()
        if name.endswith("_STATEMENT") and isinstance(value, str)
    ]
    statements += [
        *(node_keys_statement(label) for label in BASE_LABELS),
        *citation_statements("Passage"),
        *citation_statements("Document"),
    ]
    assert len(statements) >= 12
    for statement in statements:
        assert "apoc" not in statement.lower(), statement
    assert "$prefix" in verification.RELATIONSHIP_CLAIM_EDGES_STATEMENT
    assert "$base_labels" in verification.ENDPOINT_SHAPE_STATEMENT


def test_a_citation_statement_exists_only_for_the_two_citable_types():
    nodes, relationships = citation_statements("Passage")
    assert "MATCH (t:Passage) WHERE t.passage_id = n.passage_id" in nodes
    assert "MATCH (t:Passage) WHERE t.passage_id = r.passage_id" in relationships
    with pytest.raises(ValueError):
        citation_statements("Observation")


# -- against the live server -------------------------------------------------------------------


def live_settings() -> GraphSettings:
    settings = load_settings()
    try:
        settings.resolved_uri, settings.resolved_database, settings.auth
    except (UnresolvedGraphTargetError, MissingGraphCredentialError) as exc:
        pytest.skip(f"no Neo4j target configured on this machine: {type(exc).__name__}")
    return settings


def delete_test_rows(driver, settings: GraphSettings) -> None:
    """Delete exactly what this file creates — never `MATCH (n) DETACH DELETE n`."""
    driver.execute_query(
        "MATCH (n) WHERE n.graph_run_id IN $runs DETACH DELETE n",
        {"runs": [TEST_RUN_ID, FOREIGN_RUN_ID]},
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
        apply_schema(driver, settings)
        delete_test_rows(driver, settings)
        # The verification counts the *whole* database, so a run this file did not write would
        # be measured as a failure of the load under test. Skip rather than delete it — unless
        # an operator opted in, which is the only way these nine tests run beside a real graph.
        if graph_node_count(driver, settings):
            if not MAY_WIPE:
                pytest.skip(
                    "the database holds a run this test did not write; post-load verification "
                    "counts every node, so it cannot run beside another run's rows. Set "
                    "FKG_GRAPH_TESTS_MAY_WIPE=1 to wipe it and run them anyway"
                )
            wipe(driver, settings)
        try:
            yield driver, settings
        finally:
            delete_test_rows(driver, settings)
            driver.execute_query(
                f"MATCH (m:{LOAD_MARKER_LABEL}) WHERE m.graph_run_id IN $runs DELETE m",
                {"runs": [TEST_RUN_ID, FOREIGN_RUN_ID]},
                database_=settings.resolved_database,
            )


@pytest.fixture()
def loaded_graph(live_graph):
    driver, settings = live_graph
    load_export(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    return driver, settings


@pytest.mark.neo4j
def test_a_correct_small_load_passes_every_check(loaded_graph):
    driver, settings = loaded_graph
    report = verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    assert report.passed, report.describe()
    assert len(report.checks) == 27
    assert report.node_count == len(SYNTHETIC_NODES)
    assert report.relationship_count == len(SYNTHETIC_EDGES)
    assert report.graph_run_id == TEST_RUN_ID


@pytest.mark.neo4j
def test_the_live_read_measures_what_the_simulation_claims(loaded_graph):
    """The bridge between the unmarked tests and reality.

    Every failure test above doctors `facts_from()`. If that simulation drifted from what the
    Cypher actually returns, all of them would pass while verifying nothing — so the two are
    compared field by field, once, against a real load.
    """
    driver, settings = loaded_graph
    measured = read_database(driver, settings)
    simulated = facts_from()
    assert measured.node_count == simulated.node_count
    assert measured.relationship_count == simulated.relationship_count
    assert measured.label_counts == simulated.label_counts
    assert measured.type_counts == simulated.type_counts
    assert {label: sorted(keys) for label, keys in measured.node_keys.items()} == {
        label: sorted(keys) for label, keys in simulated.node_keys.items()
    }
    assert sorted(measured.relationship_claim_edge_keys) == sorted(
        simulated.relationship_claim_edge_keys
    )
    assert measured.node_property_keys == simulated.node_property_keys
    assert measured.relationship_property_keys == simulated.relationship_property_keys
    assert measured.provenance == simulated.provenance
    # Value parity, measured against simulated: this is the assertion that says a float, a bool
    # and an empty list survive the round trip through Bolt as the same values the export wrote.
    assert measured.node_content == simulated.node_content
    assert measured.relationship_content == simulated.relationship_content
    assert sorted(measured.event_dates, key=lambda d: d.key) == sorted(
        simulated.event_dates, key=lambda d: d.key
    )
    assert sorted(measured.observation_identities, key=lambda o: o.key) == sorted(
        simulated.observation_identities, key=lambda o: o.key
    )
    assert measured.unevidenced == simulated.unevidenced == Offenders()
    assert measured.endpoint_shape == measured.marker_relationships == Offenders()
    assert dict(measured.unresolved_entities) == dict(simulated.unresolved_entities)
    assert {edge.type for edge in measured.unresolved_edges} == {
        edge.type for edge in simulated.unresolved_edges
    }


@pytest.mark.neo4j
def test_the_loader_s_completion_marker_is_excluded_from_every_count(loaded_graph):
    """How `:GraphLoad` is excluded, proved rather than described.

    `begin_load` writes the marker lifecycle owns. If any count above included it, the totals
    would be one high and property parity would report `status`, `started_at`, `wiped` and
    `schema_version` as properties the database invented.
    """
    driver, settings = loaded_graph
    marker = begin_load(driver, settings, TEST_RUN_ID, wiped=False)
    assert marker.status == "loading"
    report = verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    assert report.passed, report.describe()
    assert report.node_count == len(SYNTHETIC_NODES)
    total, _, _ = driver.execute_query(
        "MATCH (n) RETURN count(n) AS c", database_=settings.resolved_database
    )
    assert total[0]["c"] == len(SYNTHETIC_NODES) + 1, "the marker really is in the database"


@pytest.mark.neo4j
def test_deleting_a_node_leaves_the_edge_that_named_it_dangling(loaded_graph):
    """`MATCH` filters rather than raising, so this is the failure the counts must catch."""
    driver, settings = loaded_graph
    driver.execute_query(
        "MATCH (m:Metric {metric_id: $key}) DETACH DELETE m",
        {"key": CONTRIBUTION.key},
        database_=settings.resolved_database,
    )
    report = verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    assert not report.passed
    dangling = report.check("no_dangling_relationships")
    assert not dangling.passed and CONTRIBUTION.key in dangling.detail
    # The same deletion is visible as three failures, and that is the report working: the node
    # is gone (1), its label count dropped (2), and its DISTINCT_FROM edge went with it (3).
    assert failed_names(report) >= {
        "no_dangling_relationships", "total_node_count", "node_counts_by_base_label"
    }


@pytest.mark.neo4j
def test_dropping_an_evidenced_by_fails_only_the_evidence_and_count_checks(loaded_graph):
    driver, settings = loaded_graph
    driver.execute_query(
        "MATCH (o:Observation {observation_id: $key})-[r:EVIDENCED_BY]->() DELETE r",
        {"key": OBSERVATION.key},
        database_=settings.resolved_database,
    )
    report = verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    evidence = report.check("observations_and_events_are_evidenced")
    assert not evidence.passed and OBSERVATION.key in evidence.detail
    assert failed_names(report) == {
        "observations_and_events_are_evidenced",
        "total_relationship_count",
        "relationship_counts_by_type",
        # A deleted relationship is a deleted content digest too. Named here rather than
        # excluded: value parity is per row, so anything that removes a row is one of its
        # failures, and a check that stayed silent about a missing edge would be the weaker one.
        "relationship_property_values_match_export",
    }


@pytest.mark.neo4j
def test_a_stray_property_fails_property_parity_and_nothing_else(loaded_graph):
    driver, settings = loaded_graph
    driver.execute_query(
        "MATCH (o:Observation {observation_id: $key}) SET o.helpfully_added = 'by hand'",
        {"key": OBSERVATION.key},
        database_=settings.resolved_database,
    )
    report = verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    # Two failures, not one: the *name* nobody exported, and the *content* of the one row that
    # grew it. Counts, labels, keys and provenance are all untouched, which is the discrimination
    # being asserted — a stray property is not reported as a missing node.
    assert failed_names(report) == {
        "node_property_keys_match_export", "node_property_values_match_export"
    }
    assert "'helpfully_added'" in report.check("node_property_keys_match_export").detail
    assert OBSERVATION.key in report.check("node_property_values_match_export").detail


@pytest.mark.neo4j
def test_a_changed_property_value_fails_only_value_parity(loaded_graph):
    """The failure the twenty-two checks that came before it were blind to, on the server.

    Nothing else moves: the node is still there, still `:Observation`, still carrying exactly
    the same property *names*. Only the number changed, and only one check notices.
    """
    driver, settings = loaded_graph
    driver.execute_query(
        "MATCH (o:Observation {observation_id: $key}) SET o.value = 999.0",
        {"key": OBSERVATION.key},
        database_=settings.resolved_database,
    )
    report = verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    assert failed_names(report) == {"node_property_values_match_export"}
    assert report.check("node_property_values_match_export").detail.count(OBSERVATION.key) == 2


@pytest.mark.neo4j
def test_one_edge_losing_a_property_fails_value_parity_though_the_union_still_has_it(loaded_graph):
    """`quoted_text` is on three `EVIDENCED_BY` edges, so removing it from one leaves the union
    of relationship property names unchanged — which is all the key-name check ever compared."""
    driver, settings = loaded_graph
    driver.execute_query(
        "MATCH ()-[r {edge_key: $key}]->() REMOVE r.quoted_text",
        {"key": SYNTHETIC_EDGES[9].edge_key},
        database_=settings.resolved_database,
    )
    report = verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    assert failed_names(report) == {"relationship_property_values_match_export"}
    assert report.check("relationship_property_keys_match_export").passed


@pytest.mark.neo4j
def test_an_event_whose_announcement_date_was_filled_in_fails_on_the_server(loaded_graph):
    """§10.6 against the database. The event carries no `announced_on` at all — Neo4j stores no
    null — so writing one is the collapse the criterion exists to forbid."""
    driver, settings = loaded_graph
    driver.execute_query(
        "MATCH (e:Event {event_id: $key}) SET e.announced_on = e.occurred_on",
        {"key": EVENT_KEY},
        database_=settings.resolved_database,
    )
    report = verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    assert "event_dates_preserved_independently" in failed_names(report)
    assert EVENT_KEY in report.check("event_dates_preserved_independently").detail


@pytest.mark.neo4j
def test_a_resurrected_refused_reading_fails_on_the_server(loaded_graph):
    """§10.10, produced rather than simulated: an observation minted for the identity the
    extractor refused, on the passage whose `:Issue` records the refusal."""
    driver, settings = loaded_graph
    metric_id, period_key, passage_id = EXPECTED.refused_reading_identities[0]
    driver.execute_query(
        "CREATE (o:Observation {observation_id: $key, metric_id: $m, period_key: $p, "
        "passage_id: $pg, graph_run_id: $run})",
        {
            "key": "test:verify:obs:resurrected", "m": metric_id, "p": period_key,
            "pg": passage_id, "run": TEST_RUN_ID,
        },
        database_=settings.resolved_database,
    )
    report = verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    check = report.check("refused_readings_are_not_resurrected")
    assert not check.passed
    assert "test:verify:obs:resurrected" in check.detail and period_key in check.detail


@pytest.mark.neo4j
def test_two_graph_run_ids_in_one_database_fail_the_identity_check(loaded_graph):
    driver, settings = loaded_graph
    driver.execute_query(
        "MATCH (i:Issue {issue_id: $key}) SET i.graph_run_id = $foreign",
        {"key": REJECTED.key, "foreign": FOREIGN_RUN_ID},
        database_=settings.resolved_database,
    )
    report = verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    check = report.check("graph_run_id_is_consistent")
    assert not check.passed
    assert FOREIGN_RUN_ID in check.actual and TEST_RUN_ID in check.actual
    assert "exactly one value" in check.detail


@pytest.mark.neo4j
def test_an_unresolved_entity_given_a_subsidiary_of_fails_the_identity_check(loaded_graph):
    """§10.7's whole point, produced against the server rather than argued.

    The relationship type is written literally into this test's own Cypher because it is not on
    `loader.ALLOWED_RELATIONSHIP_TYPES` — the loader could not create it, which is precisely
    why the verifier must be able to notice one that arrived some other way.
    """
    driver, settings = loaded_graph
    driver.execute_query(
        "MATCH (u:Unresolved {entity_id: $unresolved}), (c:Entity {entity_id: $company}) "
        "CREATE (u)-[:SUBSIDIARY_OF {edge_key: $edge_key, graph_run_id: $run}]->(c)",
        {
            "unresolved": UNNAMED.key,
            "company": COMPANY.key,
            "edge_key": "SUBSIDIARY_OF:hand-written",
            "run": TEST_RUN_ID,
        },
        database_=settings.resolved_database,
    )
    report = verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    identity = report.check("unresolved_entities_assert_no_identity")
    assert not identity.passed
    assert "SUBSIDIARY_OF" in identity.detail and "asserts an identity" in identity.detail
    assert failed_names(report) >= {"unresolved_entities_assert_no_identity"}


@pytest.mark.neo4j
def test_the_verification_leaves_the_database_as_it_found_it(loaded_graph):
    """Read-only: not a comment, an assertion. Nothing below writes."""
    driver, settings = loaded_graph
    before = graph_node_count(driver, settings)
    verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    verify_graph(driver, settings, SYNTHETIC_NODES, SYNTHETIC_EDGES)
    assert graph_node_count(driver, settings) == before
    rows, _, _ = driver.execute_query(
        "MATCH ()-[r]->() RETURN count(r) AS c", database_=settings.resolved_database
    )
    assert rows[0]["c"] == len(SYNTHETIC_EDGES)
