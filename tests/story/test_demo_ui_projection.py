"""What `story/demo_ui/projection.py` promises the renderer, and what it promises §16.

Offline by default. `RecordedReadExecutor` from `conftest.py` is the database for everything
except the `@pytest.mark.neo4j` block at the bottom, which reads the loaded graph and asserts
the counts and the digests this module's docstring records. `pytest -m "not neo4j"` is green
with no container running.

**The tests here are about the claim, not the shape.** Three claims in particular:

1. *Every read is bounded and timed.* The bound is asked for as `row_limit + 1` so truncation is
   observed rather than inferred, and the timeout is recorded by the fake and asserted, because
   §16's "a timeout on every statement" is a property a caller has to be shown keeping.
2. *Every edge endpoint resolves to a node in the same payload, and every node id that claims to
   be read from the graph came out of a row.* The trace contract requires a highlight id to
   resolve to something real; a projection that minted ids would break it silently.
3. *Two builds of the same rows are byte-identical.* `content_digest` is the check, and a test
   that only compared node *sets* would pass while the layout moved under a renderer that seeds
   itself from order.

The structural rules on the Cypher itself — fixed at import time, no write clause, allowlisted
labels, no `OBSERVATION_OF_SUBJECT` — are enforced package-wide by
`tests/story/test_story_retrieval_cypher.py`, which walks `story/**/*.py` and therefore covers
this module. What is asserted here is the part that scan does not reach: that each statement's
last sort key is unique within its result, which is what makes the deterministic sample
deterministic rather than merely ordered.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from conftest import RecordedReadExecutor, make_candidate  # type: ignore[import-not-found]
from story.demo_ui.projection import (
    COVERAGE_STATEMENT,
    DEFAULT_TIMEOUT_SECONDS,
    EVIDENCE_BACKBONE_STATEMENT,
    MAX_OBSERVATIONS_PER_METRIC,
    MAX_ROW_LIMIT,
    MAX_SUBGRAPH_METRIC_IDS,
    NODE_COMPANY,
    NODE_DOCUMENT,
    NODE_METRIC,
    NODE_OBSERVATION,
    NODE_PASSAGE,
    NODE_PERIOD,
    OVERVIEW_STATEMENT,
    PROJECTION_NAMES,
    PROJECTION_STATEMENTS,
    SUBGRAPH_EVIDENCE_STATEMENT,
    SUBGRAPH_STORY_STATEMENT,
    ProjectionError,
    build_candidate_subgraph,
    build_coverage,
    build_evidence_backbone,
    build_overview,
    build_projection,
    build_subgraph,
    content_digest,
    read_snapshot,
)
from story.stages.freshness.loaded_graph import LOAD_MARKERS

# -- fixtures from the real corpus's shape ---------------------------------------------------

#: One `:GraphLoad` marker as the live server returns it (verified 2026-08-04). The counts are
#: the real ones, so the disclosure's arithmetic is exercised against a real pair of numbers.
MARKER_ROW = {
    "graph_run_id": "graph-v1-0483dc6b4b10",
    "status": "complete",
    "node_count": 28836,
    "edge_count": 35600,
    "completed_at": "2026-08-03T16:37:44+00:00",
}

OBSERVATION_A = "obs:adjusted-gross-margin:opendoor:2022Q3:normalized-table:3eabe78a6d25"
OBSERVATION_B = "obs:gaap-gross-margin:opendoor:2022Q3:normalized-table:aa11bb22cc33"
PASSAGE_A = "norm:0001801169:0001801169-22-000108:open-20220930.htm#p139"
DOCUMENT_A = "norm:0001801169:0001801169-22-000108:open-20220930.htm"


def overview_row(**overrides: Any) -> dict[str, Any]:
    """One row of `OVERVIEW_STATEMENT`, with the field names the statement actually returns."""
    row: dict[str, Any] = {
        "metric_id": "adjusted_gross_margin",
        "metric_label": "Adjusted Gross Margin",
        "metric_category": "non_gaap_financial",
        "metric_unit": "percent",
        "metric_gaap_status": "non_gaap",
        "observation_id": OBSERVATION_A,
        "period_key": "2022Q3",
        "value": 3.3,
        "observation_unit": "percent",
        "scale": "units",
        "period_start": "2022-07-01",
        "period_end": "2022-09-30",
        "instant_date": None,
        "subject_entity_id": "opendoor",
        "validation_state": "clean",
        "source_lane": "normalized_table",
        "passage_id": PASSAGE_A,
        "passage_kind": "table",
        "passage_char_count": 2164,
        "document_id": DOCUMENT_A,
        "document_form": "10-Q",
        "document_filing_date": "2022-11-03",
        "evidence_edge_count": 1,
    }
    row.update(overrides)
    return row


def coverage_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "metric_id": "adjusted_gross_margin",
        "metric_label": "Adjusted Gross Margin",
        "metric_category": "non_gaap_financial",
        "metric_unit": "percent",
        "metric_gaap_status": "non_gaap",
        "period_key": "2022Q3",
        "observation_count": 3,
        "first_observation_id": OBSERVATION_A,
        "period_anchor": "2022-09-30",
        "subject_entity_id": "opendoor",
    }
    row.update(overrides)
    return row


def backbone_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "metric_id": "adjusted_gross_margin",
        "metric_label": "Adjusted Gross Margin",
        "metric_category": "non_gaap_financial",
        "passage_id": PASSAGE_A,
        "passage_kind": "table",
        "passage_char_count": 2164,
        "document_id": DOCUMENT_A,
        "document_form": "10-Q",
        "document_filing_date": "2022-11-03",
        "document_title": "Opendoor Technologies Inc. 10-Q",
        "cited_observation_count": 4,
        "first_observation_id": OBSERVATION_A,
    }
    row.update(overrides)
    return row


def evidence_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "metric_id": "adjusted_gross_margin",
        "metric_label": "Adjusted Gross Margin",
        "metric_category": "non_gaap_financial",
        "metric_unit": "percent",
        "metric_gaap_status": "non_gaap",
        "observation_id": OBSERVATION_A,
        "period_key": "2022Q3",
        "value": 3.3,
        "observation_unit": "percent",
        "subject_entity_id": "opendoor",
        "validation_state": "clean",
        "quoted_text": "3.3",
        "evidence_table_id": DOCUMENT_A + "#b269",
        "passage_id": PASSAGE_A,
        "passage_kind": "table",
        "passage_char_count": 2164,
        "document_id": DOCUMENT_A,
        "document_form": "10-Q",
        "document_filing_date": "2022-11-03",
        "document_title": "Opendoor Technologies Inc. 10-Q",
    }
    row.update(overrides)
    return row


def executor_for(statement: str, rows: list[dict[str, Any]]) -> RecordedReadExecutor:
    """A fake answering one projection statement and the load marker, and nothing else."""
    return RecordedReadExecutor(
        rows_by_statement={statement: tuple(rows), LOAD_MARKERS: (MARKER_ROW,)})


def nodes_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in payload["nodes"]}


# -- the statements themselves ---------------------------------------------------------------


@pytest.mark.parametrize("statement", PROJECTION_STATEMENTS)
def test_every_statement_ends_its_ordering_on_a_key_unique_within_the_result(
    statement: str,
) -> None:
    """A `LIMIT` over an unordered tail is a different picture every run.

    The package-wide scan checks that a `LIMIT` exists; it applies the unique-last-key rule only
    to `story/stages/retrieval/`. This module makes the same promise and so states it here:
    every statement's last sort key is an id, and every id in this graph is unique per row.
    """
    order_by = re.search(r"\bORDER BY\b(.*?)\bLIMIT\b", statement, re.S)
    assert order_by, "a bounded statement that does not order is not deterministic"
    last_key = order_by.group(1).strip().rstrip(",").split(",")[-1].strip()
    assert last_key.endswith(("_id", "observation_id")), (
        f"the last sort key is {last_key!r}, which is not unique per row")


@pytest.mark.parametrize("statement", PROJECTION_STATEMENTS)
def test_every_statement_binds_its_limit_rather_than_writing_a_number(statement: str) -> None:
    """The bound comes from this module, never from a request body."""
    assert "LIMIT $row_limit" in statement


def test_no_statement_reaches_the_entity_label_or_the_subject_relationship() -> None:
    """The reason the company node is synthesised, asserted rather than described.

    Both are refused by `tests/story/test_story_retrieval_cypher.py` for the whole package. It is
    restated here because it is *why* this module has a `synthesised` flag at all — delete the
    flag and this test is the one that explains what was lost.
    """
    for statement in PROJECTION_STATEMENTS:
        assert ":Entity" not in statement
        assert "OBSERVATION_OF_SUBJECT" not in statement


# -- bounds and timeouts ---------------------------------------------------------------------


def test_every_read_carries_the_explicit_timeout_it_was_given() -> None:
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])

    build_overview(executor, timeout_seconds=7.5)

    assert [call.timeout_seconds for call in executor.calls] == [7.5, 7.5]


def test_the_default_timeout_is_positive_and_is_what_a_caller_gets_by_default() -> None:
    executor = executor_for(COVERAGE_STATEMENT, [coverage_row()])

    build_coverage(executor)

    assert DEFAULT_TIMEOUT_SECONDS > 0
    assert {call.timeout_seconds for call in executor.calls} == {DEFAULT_TIMEOUT_SECONDS}


@pytest.mark.parametrize("timeout", [0, 0.0, -1.0])
def test_a_non_positive_timeout_is_refused_before_any_read_is_issued(timeout: float) -> None:
    """F10: the server reads zero as *no timeout*, so a zero here is the ceiling disappearing."""
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])

    with pytest.raises(ProjectionError):
        build_overview(executor, timeout_seconds=timeout)

    assert executor.calls == []


@pytest.mark.parametrize("row_limit", [0, -5, MAX_ROW_LIMIT + 1])
def test_a_row_limit_outside_the_declared_range_is_refused(row_limit: int) -> None:
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])

    with pytest.raises(ProjectionError):
        build_overview(executor, row_limit=row_limit)


def test_the_statement_is_asked_for_one_more_row_than_the_bound_so_truncation_is_observed(
) -> None:
    """`len(rows) == row_limit` cannot tell a full page from a bound that bit. The extra row can."""
    rows = [overview_row(observation_id=f"obs:{index}") for index in range(4)]
    executor = executor_for(OVERVIEW_STATEMENT, rows)

    payload = build_overview(executor, row_limit=3)

    overview_call = executor.calls[-1]
    assert overview_call.parameters["row_limit"] == 4
    assert payload["bounds"]["truncated"] is True
    assert payload["bounds"]["rows_returned"] == 3
    assert payload["counts"]["nodes_by_type"][NODE_OBSERVATION] == 3


def test_a_result_inside_the_bound_is_not_reported_as_truncated() -> None:
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])

    payload = build_overview(executor, row_limit=50)

    assert payload["bounds"]["truncated"] is False


def test_the_sample_size_is_bound_as_a_parameter_and_is_itself_bounded() -> None:
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])

    build_overview(executor, observations_per_metric=5)
    assert executor.calls[-1].parameters["observations_per_metric"] == 5

    with pytest.raises(ProjectionError):
        build_overview(executor, observations_per_metric=MAX_OBSERVATIONS_PER_METRIC + 1)
    with pytest.raises(ProjectionError):
        build_overview(executor, observations_per_metric=0)


# -- the overview payload --------------------------------------------------------------------


def test_the_overview_draws_the_evidence_spine_it_read_and_marks_nothing_of_it_synthesised(
) -> None:
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])

    payload = build_overview(executor)
    found = nodes_by_id(payload)

    assert payload["counts"]["nodes_by_type"] == {
        NODE_METRIC: 1, NODE_OBSERVATION: 1, NODE_PASSAGE: 1, NODE_DOCUMENT: 1, NODE_COMPANY: 1}
    assert payload["counts"]["edges_by_type"] == {
        "has_observation": 1, "evidenced_by": 1, "part_of": 1, "about_subject": 1}
    for edge in payload["edges"]:
        if edge["type"] == "about_subject":
            continue
        assert edge["synthesised"] is False
        assert edge["graph_relationship_type"] in {"HAS_OBSERVATION", "EVIDENCED_BY", "PART_OF"}
    assert found[OBSERVATION_A]["value"] == 3.3
    assert found[PASSAGE_A]["label"] == "p139"
    assert found[DOCUMENT_A]["label"] == "10-Q 2022-11-03"


def test_node_ids_are_the_real_graph_ids_the_rows_carried_and_nothing_is_minted() -> None:
    """The trace contract's requirement: a highlight id has to resolve to something real.

    Every node read from the graph must carry an id that appeared in a row. The two synthesised
    kinds are exempt and say so — the company's id is the `subject_entity_id` value, and a period
    node has no graph node behind it at all.
    """
    row = overview_row()
    executor = executor_for(OVERVIEW_STATEMENT, [row])

    payload = build_overview(executor)

    real_ids = {row["metric_id"], row["observation_id"], row["passage_id"], row["document_id"]}
    for node in payload["nodes"]:
        if node["synthesised"]:
            continue
        assert node["id"] in real_ids, f"{node['id']!r} is not an id any row carried"


def test_the_company_node_is_marked_derived_and_says_where_it_came_from() -> None:
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])

    payload = build_overview(executor)
    company = nodes_by_id(payload)["opendoor"]

    assert company["type"] == NODE_COMPANY
    assert company["synthesised"] is True
    assert company["read_from_graph"] is False
    assert company["derived_from"] == "observation.subject_entity_id"
    assert "derived, not read" in company["note"]


def test_the_subject_edge_is_marked_derived_and_claims_no_graph_relationship() -> None:
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])

    payload = build_overview(executor)
    subject_edges = [edge for edge in payload["edges"] if edge["type"] == "about_subject"]

    assert subject_edges
    for edge in subject_edges:
        assert edge["synthesised"] is True
        assert edge["read_from_graph"] is False
        assert "graph_relationship_type" not in edge
        assert edge["derived_from"] == "observation.subject_entity_id"


def test_every_edge_endpoint_resolves_to_a_node_in_the_same_payload() -> None:
    rows = [overview_row(),
            overview_row(metric_id="gaap_gross_margin", observation_id=OBSERVATION_B,
                         passage_id=None, document_id=None, evidence_edge_count=0)]
    executor = executor_for(OVERVIEW_STATEMENT, rows)

    payload = build_overview(executor)
    ids = set(nodes_by_id(payload))

    for edge in payload["edges"]:
        assert edge["source"] in ids
        assert edge["target"] in ids


def test_an_observation_with_no_evidence_keeps_its_node_and_grows_no_dangling_edge() -> None:
    """An absent passage is an ordinary row, not a fault: `OPTIONAL MATCH` returns nulls."""
    executor = executor_for(
        OVERVIEW_STATEMENT,
        [overview_row(passage_id=None, passage_kind=None, passage_char_count=None,
                      document_id=None, document_form=None, document_filing_date=None,
                      evidence_edge_count=0)])

    payload = build_overview(executor)

    assert NODE_PASSAGE not in payload["counts"]["nodes_by_type"]
    assert "evidenced_by" not in payload["counts"]["edges_by_type"]
    assert OBSERVATION_A in nodes_by_id(payload)


def test_the_overview_discloses_that_it_is_a_projection_and_names_the_snapshot() -> None:
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])

    payload = build_overview(executor)

    assert "not the whole graph" in payload["disclosure"]
    assert payload["snapshot"]["graph_run_id"] == "graph-v1-0483dc6b4b10"
    assert payload["snapshot"]["marker_node_count"] == 28836
    assert payload["snapshot"]["total_node_count"] == 28837
    assert payload["snapshot"]["edge_count"] == 35600


def test_the_legend_counts_the_types_present_and_no_others() -> None:
    """A legend naming a type the picture does not contain is a legend that lies about it."""
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])

    payload = build_overview(executor)
    legend = {entry["type"]: entry for entry in payload["legend"]["nodes"]}

    assert set(legend) == set(payload["counts"]["nodes_by_type"])
    for kind, count in payload["counts"]["nodes_by_type"].items():
        assert legend[kind]["count"] == count
        assert legend[kind]["description"]
    assert legend[NODE_COMPANY]["source"] == "derived"
    assert legend[NODE_METRIC]["source"] == "graph"
    edge_legend = {entry["type"]: entry for entry in payload["legend"]["edges"]}
    assert edge_legend["about_subject"]["source"] == "derived"
    assert edge_legend["has_observation"]["source"] == "graph"


def test_the_payload_is_json_serialisable_exactly_as_the_server_will_send_it() -> None:
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])

    encoded = json.dumps(build_overview(executor))

    assert '"content_digest"' in encoded


# -- determinism -----------------------------------------------------------------------------


def test_two_builds_of_the_same_rows_are_byte_identical() -> None:
    rows = [overview_row(observation_id=f"obs:{index}") for index in range(5)]

    first = build_overview(executor_for(OVERVIEW_STATEMENT, rows))
    second = build_overview(executor_for(OVERVIEW_STATEMENT, rows))

    assert first["content_digest"] == second["content_digest"]
    assert json.dumps(first["nodes"]) == json.dumps(second["nodes"])
    assert json.dumps(first["edges"]) == json.dumps(second["edges"])


def test_the_digest_moves_when_the_order_moves_so_it_cannot_pass_over_a_reordering() -> None:
    """`content_digest` deliberately does not normalise order away.

    The renderer seeds its layout from the node list, so a payload that reordered between runs
    would draw differently while holding the same nodes. A digest over a *set* would call that
    identical, which is the failure this test exists to make impossible.
    """
    rows = [overview_row(observation_id="obs:a"), overview_row(observation_id="obs:b")]
    forward = build_overview(executor_for(OVERVIEW_STATEMENT, rows))
    backward = build_overview(executor_for(OVERVIEW_STATEMENT, list(reversed(rows))))

    assert {node["id"] for node in forward["nodes"]} == {n["id"] for n in backward["nodes"]}
    assert forward["content_digest"] != backward["content_digest"]


def test_the_digest_excludes_nothing_that_is_drawn_and_ignores_the_timing() -> None:
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])
    payload = build_overview(executor)

    assert payload["content_digest"] == content_digest(payload["nodes"], payload["edges"])
    assert "query_ms" in payload["timing"]


def test_a_repeated_row_adds_no_duplicate_node_or_edge() -> None:
    """A metric arrives on twenty rows of the real overview. First write wins, deterministically."""
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row(), overview_row()])

    payload = build_overview(executor)

    assert payload["counts"]["nodes"] == 5
    assert payload["counts"]["edges"] == 4


# -- coverage --------------------------------------------------------------------------------


def test_coverage_keeps_a_metric_with_no_observations_as_an_isolated_node() -> None:
    """Nine of the 26 metrics are in this state live, `revenue` among them. It is the point."""
    executor = executor_for(COVERAGE_STATEMENT, [
        coverage_row(),
        coverage_row(metric_id="revenue", metric_label="Revenue", period_key=None,
                     observation_count=0, first_observation_id=None, period_anchor=None,
                     subject_entity_id=None),
    ])

    payload = build_coverage(executor)
    found = nodes_by_id(payload)

    assert "revenue" in found
    assert found["revenue"]["synthesised"] is False
    touched = {edge["source"] for edge in payload["edges"]} | {
        edge["target"] for edge in payload["edges"]}
    assert "revenue" not in touched


def test_coverage_marks_the_period_node_and_the_cell_edge_as_derived() -> None:
    executor = executor_for(COVERAGE_STATEMENT, [coverage_row()])

    payload = build_coverage(executor)
    period = nodes_by_id(payload)["period:2022Q3"]
    cell = [edge for edge in payload["edges"] if edge["type"] == "covers_period"][0]

    assert period["type"] == NODE_PERIOD
    assert period["synthesised"] is True
    assert period["derived_from"] == "observation.period_key"
    assert cell["synthesised"] is True
    assert cell["observation_count"] == 3
    assert "graph_relationship_type" not in cell


# -- the evidence backbone -------------------------------------------------------------------


def test_the_backbone_synthesises_no_company_because_no_row_carries_a_subject() -> None:
    """The statement aggregates observations away, so there is nothing to derive a subject from.

    Inventing one anyway would be this module asserting something the query did not read, which
    is the exact failure the `synthesised` flag exists to make visible elsewhere.
    """
    executor = executor_for(EVIDENCE_BACKBONE_STATEMENT, [backbone_row()])

    payload = build_evidence_backbone(executor)

    assert NODE_COMPANY not in payload["counts"]["nodes_by_type"]
    assert payload["counts"]["nodes_by_type"] == {
        NODE_METRIC: 1, NODE_PASSAGE: 1, NODE_DOCUMENT: 1}
    assert payload["counts"]["edges_by_type"] == {"cites_passage": 1, "part_of": 1}


def test_the_metric_to_passage_edge_is_derived_and_carries_the_count_it_aggregated() -> None:
    executor = executor_for(EVIDENCE_BACKBONE_STATEMENT, [backbone_row()])

    payload = build_evidence_backbone(executor)
    cites = [edge for edge in payload["edges"] if edge["type"] == "cites_passage"][0]
    part_of = [edge for edge in payload["edges"] if edge["type"] == "part_of"][0]

    assert cites["synthesised"] is True
    assert cites["cited_observation_count"] == 4
    assert part_of["synthesised"] is False
    assert part_of["graph_relationship_type"] == "PART_OF"


# -- the focused subgraph --------------------------------------------------------------------


def test_the_story_view_draws_facts_and_the_subject_and_no_sources() -> None:
    executor = RecordedReadExecutor(rows_by_statement={SUBGRAPH_STORY_STATEMENT: (
        {**overview_row(), "observation_passage_id": PASSAGE_A,
         "observation_document_id": DOCUMENT_A},)})

    payload = build_subgraph(executor, metric_ids=["adjusted_gross_margin"],
                             period_keys=["2022Q3"], view="story")

    assert payload["view"] == "story"
    assert payload["counts"]["nodes_by_type"] == {
        NODE_METRIC: 1, NODE_OBSERVATION: 1, NODE_COMPANY: 1}
    observation = nodes_by_id(payload)[OBSERVATION_A]
    assert observation["passage_id"] == PASSAGE_A
    assert observation["foreign_keys_derived_from"] == (
        "observation.passage_id, observation.document_id")


def test_the_evidence_view_puts_the_quote_on_the_edge_where_the_graph_keeps_it() -> None:
    """C3: `quoted_text` is a property of `EVIDENCED_BY`, not of the passage."""
    executor = RecordedReadExecutor(
        rows_by_statement={SUBGRAPH_EVIDENCE_STATEMENT: (evidence_row(),)})

    payload = build_subgraph(executor, metric_ids=["adjusted_gross_margin"],
                             period_keys=["2022Q3"], view="evidence")
    evidenced_by = [edge for edge in payload["edges"] if edge["type"] == "evidenced_by"][0]

    assert evidenced_by["quoted_text"] == "3.3"
    assert evidenced_by["synthesised"] is False
    assert set(payload["counts"]["nodes_by_type"]) == {
        NODE_METRIC, NODE_OBSERVATION, NODE_PASSAGE, NODE_DOCUMENT, NODE_COMPANY}


def test_the_two_views_issue_two_different_statements() -> None:
    executor = RecordedReadExecutor()

    build_subgraph(executor, metric_ids=["m"], period_keys=["p"], view="story")
    build_subgraph(executor, metric_ids=["m"], period_keys=["p"], view="evidence")

    assert executor.calls[0].statement == SUBGRAPH_STORY_STATEMENT
    assert executor.calls[1].statement == SUBGRAPH_EVIDENCE_STATEMENT


def test_an_unknown_view_is_refused_rather_than_defaulted() -> None:
    with pytest.raises(ProjectionError):
        build_subgraph(RecordedReadExecutor(), metric_ids=["m"], period_keys=["p"],
                       view="everything")


@pytest.mark.parametrize("selection", [[], (), [""], ["  "], "adjusted_gross_margin"])
def test_an_empty_or_scalar_selection_is_refused(selection: Any) -> None:
    with pytest.raises(ProjectionError):
        build_subgraph(RecordedReadExecutor(), metric_ids=selection, period_keys=["2022Q3"])


def test_a_selection_wider_than_the_declared_maximum_is_refused() -> None:
    too_many = [f"metric_{index}" for index in range(MAX_SUBGRAPH_METRIC_IDS + 1)]

    with pytest.raises(ProjectionError):
        build_subgraph(RecordedReadExecutor(), metric_ids=too_many, period_keys=["2022Q3"])


def test_the_selection_is_sorted_and_deduplicated_so_two_orderings_agree() -> None:
    """Two clients naming the same metrics must get the same picture and the same digest."""
    forward = RecordedReadExecutor()
    backward = RecordedReadExecutor()

    build_subgraph(forward, metric_ids=["b", "a", "a"], period_keys=["2022Q3"])
    build_subgraph(backward, metric_ids=["a", "b"], period_keys=["2022Q3"])

    assert forward.calls[0].parameters["metric_ids"] == ["a", "b"]
    assert forward.calls[0].parameters == backward.calls[0].parameters


def test_a_candidate_supplies_its_own_metrics_and_periods() -> None:
    executor = RecordedReadExecutor()
    candidate = make_candidate()

    build_candidate_subgraph(executor, candidate)

    assert executor.calls[0].parameters["metric_ids"] == list(candidate.metric_ids)
    assert executor.calls[0].parameters["period_keys"] == sorted(candidate.anchor_period_keys)


def test_an_object_that_is_not_a_candidate_is_refused_with_its_type_named() -> None:
    with pytest.raises(ProjectionError, match="metric_ids"):
        build_candidate_subgraph(RecordedReadExecutor(), object())


# -- dispatch --------------------------------------------------------------------------------


@pytest.mark.parametrize("name", PROJECTION_NAMES)
def test_every_declared_projection_name_builds(name: str) -> None:
    executor = RecordedReadExecutor(rows_by_statement={LOAD_MARKERS: (MARKER_ROW,)})

    payload = build_projection(executor, name)

    assert payload["projection"] == name
    assert payload["snapshot"]["graph_run_id"] == "graph-v1-0483dc6b4b10"


def test_an_unknown_projection_name_is_refused_rather_than_returning_the_overview() -> None:
    with pytest.raises(ProjectionError, match="unknown projection"):
        build_projection(RecordedReadExecutor(), "everything")


def test_a_supplied_snapshot_saves_the_marker_read() -> None:
    executor = executor_for(OVERVIEW_STATEMENT, [overview_row()])

    build_overview(executor, snapshot={"graph_run_id": "already-known"})

    assert [call.statement for call in executor.calls] == [OVERVIEW_STATEMENT]


def test_an_absent_load_marker_is_reported_rather_than_raised_on() -> None:
    """A header that could not draw would hide the state §7's gate exists to show."""
    snapshot = read_snapshot(RecordedReadExecutor())

    assert snapshot["graph_run_id"] is None
    assert snapshot["marker_count"] == 0
    assert snapshot["total_node_count"] is None


# -- against the live server -----------------------------------------------------------------


@pytest.fixture()
def live_executor():
    """The running instance, or a skip. Read-only: nothing here writes, so nothing is undone."""
    from story.providers.neo4j_connection import (  # noqa: PLC0415
        StoryGraphCredentialError,
        StoryGraphUnconfiguredError,
        load_neo4j_settings,
        open_read_executor,
    )

    resolved = load_neo4j_settings()
    try:
        resolved.resolved_uri, resolved.resolved_database, resolved.auth
    except (StoryGraphUnconfiguredError, StoryGraphCredentialError) as exc:
        pytest.skip(f"no Neo4j target configured on this machine: {type(exc).__name__}")
    executor = open_read_executor(resolved)
    health = executor.verify_connectivity()
    if not health.ok:
        executor.close()
        pytest.skip(f"local Neo4j is unreachable ({health.status}: {health.detail})")
    with executor:
        yield executor


@pytest.mark.neo4j
def test_the_live_overview_is_the_bounded_projection_this_module_documents(live_executor):
    """The counts in the module docstring, measured rather than asserted from memory."""
    payload = build_overview(live_executor)

    assert payload["bounds"]["rows_returned"] == 308
    assert payload["bounds"]["truncated"] is False
    assert payload["counts"]["nodes_by_type"] == {
        NODE_METRIC: 17, NODE_OBSERVATION: 308, NODE_PASSAGE: 52, NODE_DOCUMENT: 25,
        NODE_COMPANY: 1}
    assert payload["counts"]["nodes"] == 403
    assert payload["counts"]["edges"] == 976
    assert payload["counts"]["synthesised_nodes"] == 1
    assert payload["snapshot"]["total_node_count"] == 28837
    assert payload["snapshot"]["edge_count"] == 35600


@pytest.mark.neo4j
def test_the_live_coverage_view_shows_the_metrics_that_have_no_observations(live_executor):
    payload = build_coverage(live_executor)

    metrics = [node for node in payload["nodes"] if node["type"] == NODE_METRIC]
    connected = {edge["source"] for edge in payload["edges"] if edge["type"] == "covers_period"}
    isolated = [node["id"] for node in metrics if node["id"] not in connected]

    assert payload["bounds"]["rows_returned"] == 546
    assert len(metrics) == 26
    assert len(isolated) == 9
    assert "revenue" in isolated


@pytest.mark.neo4j
def test_the_live_evidence_backbone_is_the_expensive_one_and_stays_inside_its_bound(
    live_executor,
):
    payload = build_evidence_backbone(live_executor)

    assert payload["bounds"]["rows_returned"] == 691
    assert payload["bounds"]["truncated"] is False
    assert payload["counts"]["nodes"] == 215
    assert payload["counts"]["edges"] == 841


@pytest.mark.neo4j
@pytest.mark.parametrize("view", ["story", "evidence"])
def test_the_live_focused_subgraph_for_the_demo_candidate(live_executor, view: str):
    payload = build_subgraph(live_executor,
                             metric_ids=["adjusted_gross_margin", "gaap_gross_margin"],
                             period_keys=["2022Q3"], view=view)

    assert payload["bounds"]["rows_returned"] == 11
    assert payload["counts"]["nodes"] == (14 if view == "story" else 25)
    assert payload["counts"]["edges"] == (22 if view == "story" else 40)


@pytest.mark.neo4j
@pytest.mark.parametrize("build", [build_overview, build_coverage, build_evidence_backbone])
def test_two_live_builds_of_the_same_projection_produce_the_same_digest(live_executor, build):
    """The determinism claim against the real database, not against a scripted fake."""
    first = build(live_executor)
    second = build(live_executor)

    assert first["content_digest"] == second["content_digest"]
    assert json.dumps(first["nodes"]) == json.dumps(second["nodes"])


@pytest.mark.neo4j
def test_the_live_snapshot_names_the_loaded_run(live_executor):
    snapshot = read_snapshot(live_executor)

    assert snapshot["marker_count"] == 1
    assert snapshot["status"] == "complete"
    assert snapshot["graph_run_id"].startswith("graph-v1-")
