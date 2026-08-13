"""What the nine §9 tools return, what they refuse, and what they refuse to guess.

Offline by default. `RecordedReadExecutor` from `conftest.py` is the database for everything
that is not marked `neo4j`: it records the statement, the parameters and the timeout, and hands
back a scripted result keyed by the exact statement text — so a query changed by one character
stops matching, which is the failure a test wants.

The ontology is loaded for real. It is a first-party package that reads committed YAML, it is
the authority C4 makes it, and a fake registry would let `get_metric_definition` pass while
returning bounds nothing in the repository agrees with. `gross margin` denoting two metrics is
a measurement of the shipped ontology, not a fixture.

The `neo4j`-marked tests at the bottom drive every tool against the live graph. They use the
2022Q3 cluster and they read the expected values **out of the graph** before asserting on them
where the assertion is about a relationship between two numbers; where the assertion is the
number itself — `quoted_text == '(211)'` off the `EVIDENCED_BY` edge — it is pinned, because
that one is the point of correction C3 and a test that recomputed it would prove nothing.
"""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from story.contracts import GraphRetriever
from story.core.models import RetrievalOutcome
from story.stages.detection.canonicalization import record_from_rows
from story.stages.retrieval import cypher
from story.stages.retrieval.graph_tools import (
    DEFAULT_HANDLE_LIMIT,
    DEFAULT_INNER_SEARCH_LIMIT,
    MAX_CONTEXT_NEIGHBOURS,
    SEVERITY_ORDER,
    TOOL_PARAMETERS,
    BoundedGraphRetriever,
    passage_window_ids,
)
from story.stages.retrieval.lucene_escaping import RESERVED_WORDS, build_query, escape_term
from story.stages.retrieval.metric_metadata import default_registry
from story.stages.retrieval.results import (
    CITATION_FIELDS,
    MAX_ROWS,
    UNAVAILABLE_TOOLS,
    result_code,
)

from conftest import RecordedReadExecutor  # type: ignore[import-not-found]

#: One real observation from the 2022Q3 cluster, in the shape `FACT_EVIDENCE_FOR_OBSERVATION`
#: returns it. Copied from a live read on 2026-08-03 rather than invented, so the offline tests
#: assert against the graph's actual field names and the actual `(211)` quote.
EVIDENCE_ROW: dict[str, Any] = {
    "observation_id": "obs:adjusted-ebitda:opendoor:2022Q3:normalized-table:ca9391dd3a50",
    "metric_id": "adjusted_ebitda",
    "period_key": "2022Q3",
    "value": -211_000_000.0,
    "unit": "USD",
    "scale": "millions",
    "currency": "USD",
    "row_label": "Adjusted EBITDA",
    "column_label": "2022",
    "row_index": 12,
    "value_column_index": 3,
    "period_header_row_index": 3,
    "period_header_column_index": 2,
    "metric_label_row_index": 12,
    "source_lane": "normalized_table",
    "validation_state": "clean",
    "quoted_text": "(211)",
    "table_id": "norm:0001801169:0001801169-22-000108:open-20220930.htm#b228",
    "block_ids": ["norm:0001801169:0001801169-22-000108:open-20220930.htm#b228"],
    "evidence_kind": "normalized_table",
    "ontology_declared": True,
    "source_url": "https://www.sec.gov/Archives/edgar/data/1801169/000180116922000108/open-20220930.htm",
    "passage_id": "norm:0001801169:0001801169-22-000108:open-20220930.htm#p120",
    "passage_kind": "table",
    "passage_char_count": 2164,
    "passage_table_id": None,
    "section_id": "norm:0001801169:0001801169-22-000108:open-20220930.htm#s80",
    "document_id": "norm:0001801169:0001801169-22-000108:open-20220930.htm",
    "form": "10-Q",
    "document_type": "narrative_primary",
    "accession": "0001801169-22-000108",
    "filing_date": "2022-11-10",
    "report_date": "2022-09-30",
}


#: The same observation in the shape `METRIC_HISTORY` returns it, and the narrative counterpart
#: that stands for the 14 observations with no table identity at all. Both copied from a live
#: read on 2026-08-13 — the numbers below are what the graph held, not what a cell resolver
#: would like them to be, which is the point of committing them.
#:
#: `row_index == metric_label_row_index == 12` and `value_column_index == 3` against
#: `period_header_column_index == 2`: the value and its period header sit in different columns
#: of the same table, which is true of 2,125 of the 2,690 table-backed observations.
HISTORY_ROW: dict[str, Any] = {
    "observation_id": "obs:adjusted-ebitda:opendoor:2022Q3:normalized-table:ca9391dd3a50",
    "metric_id": "adjusted_ebitda",
    "period_key": "2022Q3",
    "value": -211_000_000.0,
    "unit": "USD",
    "scale": "millions",
    "currency": "USD",
    "period_start": "2022-07-01",
    "period_end": "2022-09-30",
    "instant_date": None,
    "period_shape": "duration",
    "row_label": "Adjusted EBITDA",
    "column_label": "2022",
    "row_index": 12,
    "value_column_index": 3,
    "period_header_row_index": 3,
    "period_header_column_index": 2,
    "metric_label_row_index": 12,
    "source_lane": "normalized_table",
    "assertion_type": "reported",
    "validation_state": "clean",
    "warning_codes": [],
    "ambiguity_codes": [],
    "subject_entity_id": "opendoor",
    "passage_id": "norm:0001801169:0001801169-22-000108:open-20220930.htm#p120",
    "document_id": "norm:0001801169:0001801169-22-000108:open-20220930.htm",
}

NARRATIVE_HISTORY_ROW: dict[str, Any] = {
    "observation_id": "obs:adjusted-ebitda:opendoor:2022Q2:normalized-narrative:603c21481ff3",
    "metric_id": "adjusted_ebitda",
    "period_key": "2022Q2",
    "value": 218_000_000.0,
    "unit": "USD",
    "scale": "millions",
    "currency": "USD",
    "period_start": "2022-04-01",
    "period_end": "2022-06-30",
    "instant_date": None,
    "period_shape": "duration",
    "row_label": None,
    "column_label": None,
    "row_index": None,
    "value_column_index": None,
    "period_header_row_index": None,
    "period_header_column_index": None,
    "metric_label_row_index": None,
    "source_lane": "normalized_narrative",
    "assertion_type": "reported",
    "validation_state": "warned",
    "warning_codes": ["unpreferred_source_lane"],
    "ambiguity_codes": [],
    "subject_entity_id": "opendoor",
    "passage_id": "norm:0001801169:0001801169-22-000075:q22022formxex992sharehol.htm#p7",
    "document_id": "norm:0001801169:0001801169-22-000075:q22022formxex992sharehol.htm",
}

NARRATIVE_EVIDENCE_ROW: dict[str, Any] = {
    "observation_id": "obs:adjusted-ebitda:opendoor:2022Q2:normalized-narrative:603c21481ff3",
    "metric_id": "adjusted_ebitda",
    "period_key": "2022Q2",
    "value": 218_000_000.0,
    "unit": "USD",
    "scale": "millions",
    "currency": "USD",
    "row_label": None,
    "column_label": None,
    "row_index": None,
    "value_column_index": None,
    "period_header_row_index": None,
    "period_header_column_index": None,
    "metric_label_row_index": None,
    "source_lane": "normalized_narrative",
    "validation_state": "warned",
    "quoted_text": "Adjusted EBITDA was $218 million in 2Q22 compared to $25 million in 2Q21.",
    "table_id": None,
    "block_ids": ["norm:0001801169:0001801169-22-000075:q22022formxex992sharehol.htm#b22"],
    "evidence_kind": "normalized_passage",
    "ontology_declared": True,
    "source_url": "https://www.sec.gov/Archives/edgar/data/1801169/000180116922000075/q22022formxex992sharehol.htm",
    "passage_id": "norm:0001801169:0001801169-22-000075:q22022formxex992sharehol.htm#p7",
    "passage_kind": "narrative",
    "passage_char_count": 2232,
    "passage_table_id": None,
    "section_id": "norm:0001801169:0001801169-22-000075:q22022formxex992sharehol.htm#s2",
    "document_id": "norm:0001801169:0001801169-22-000075:q22022formxex992sharehol.htm",
    "form": "8-K",
    "document_type": "shareholder_letter",
    "accession": "0001801169-22-000075",
    "filing_date": "2022-08-04",
    "report_date": "2022-08-04",
}

#: The five `:Observation` properties that locate a value in its flattened table. Named once so
#: a test that drops one fails here rather than by quietly checking four.
CELL_INDEX_FIELDS = (
    "row_index",
    "value_column_index",
    "period_header_row_index",
    "period_header_column_index",
    "metric_label_row_index",
)


def retriever(**rows_by_statement: Any) -> tuple[BoundedGraphRetriever, RecordedReadExecutor]:
    """A retriever over a scripted executor, with the real ontology behind it."""
    executor = RecordedReadExecutor(rows_by_statement=dict(rows_by_statement))
    return BoundedGraphRetriever(executor, registry=default_registry()), executor


def rows(count: int, **fields: Any) -> tuple[dict[str, Any], ...]:
    """`count` distinct rows, so a truncation test is not comparing identical dicts."""
    return tuple({"index": index, **fields} for index in range(count))


# ---------------------------------------------------------------------------------------
# The protocol, and the bounds
# ---------------------------------------------------------------------------------------


def test_the_retriever_can_be_driven_through_the_graph_retriever_protocol() -> None:
    """Driven, not `isinstance`-checked: a `runtime_checkable` protocol proves only that some
    attributes exist, and `story/contracts.py` says so in its own docstring."""
    tool, _executor = retriever()

    def use(target: GraphRetriever) -> None:
        result = target.call("get_guidance_history", {})
        assert result.outcome is RetrievalOutcome.UNAVAILABLE
        assert len(target.trace()) == 1

    use(tool)


def test_a_bound_that_bit_is_reported_as_truncated_rather_than_silently_capping() -> None:
    cap = MAX_ROWS["search_passages"]
    tool, _executor = retriever(**{cypher.SEARCH_PASSAGES: rows(cap + 1)})

    result = tool.call("search_passages", {"terms": ["margin"]})

    assert result.outcome is RetrievalOutcome.OK
    assert len(result.rows) == cap
    assert result.truncated is True


def test_a_full_page_that_did_not_truncate_reports_truncated_false() -> None:
    cap = MAX_ROWS["search_passages"]
    tool, _executor = retriever(**{cypher.SEARCH_PASSAGES: rows(cap)})

    result = tool.call("search_passages", {"terms": ["margin"]})

    assert len(result.rows) == cap
    assert result.truncated is False


def test_every_statement_asks_for_one_row_more_than_the_declared_cap() -> None:
    """The `+ 1` is the whole of the truncation contract: without the extra row there is no
    evidence that anything was dropped, and `len(rows) == cap` cannot supply it."""
    tool, executor = retriever()
    tool.call("search_passages", {"terms": ["margin"]})
    tool.call("get_events_in_window", {"since": "2022-01-01", "until": "2022-12-31"})

    assert [call.parameters["row_limit"] for call in executor.calls] == [
        MAX_ROWS["search_passages"] + 1, MAX_ROWS["get_events_in_window"] + 1]


def test_every_statement_is_issued_with_a_positive_timeout() -> None:
    """§16 requires one on every statement, and F10 measured that the server reads `0` as *no
    timeout* — so "a timeout was passed" is not enough, it has to be positive."""
    tool, executor = retriever()
    for name, (required, _optional) in TOOL_PARAMETERS.items():
        if name in UNAVAILABLE_TOOLS:
            continue
        tool.call(name, _minimal_parameters(name, required))

    assert executor.calls, "no statement was issued, so the timeout claim is vacuous"
    assert all(call.timeout_seconds > 0 for call in executor.calls)


def test_a_non_positive_timeout_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        BoundedGraphRetriever(RecordedReadExecutor(), timeout_seconds=0)


def test_the_fulltext_candidate_pool_is_bounded_before_the_expand_and_the_sort() -> None:
    """D7. Without an inner limit the statement is linear in corpus size: `["the"]` matches
    6,873 of 8,776 passages, the `PART_OF` expand runs on all of them before the document
    filter, and `ORDER BY score DESC` drains the stream — 45,972 db hits for 26 rows, and 38,787
    for a filter that matched nothing *(PROFILE, 2026-08-03)*. The row cap cannot help, because
    the `Top` is what it bounds."""
    tool, executor = retriever()
    tool.call("search_passages", {"terms": ["the"]})

    assert "{limit: $inner_limit}" in cypher.SEARCH_PASSAGES
    assert executor.calls[0].parameters["inner_limit"] == DEFAULT_INNER_SEARCH_LIMIT


def test_the_candidate_pool_bound_is_reported_because_it_can_cost_a_row() -> None:
    """The inner limit is applied before the document filter, so a filter whose matches all
    rank below the pool is starved: `["the"]` restricted to `shareholder_letter` returns 26 rows
    unbounded and 9 at 500 *(measured live)*. `candidate_pool_size == inner_limit` is how a
    caller can tell that apart from a short corpus, and it is why the omission is visible rather
    than silent."""
    assert "candidate_pool_size AS candidate_pool_size" in cypher.SEARCH_PASSAGES


def test_no_caller_parameter_can_reach_the_candidate_pool_bound() -> None:
    tool, executor = retriever()

    result = tool.call("search_passages", {"terms": ["margin"], "inner_limit": 100_000})

    assert result_code(result) == "unknown_parameter"
    assert not executor.calls


def test_a_candidate_pool_smaller_than_the_page_is_refused_at_construction() -> None:
    """A pool below the 25-row cap cannot fill a page, and every search would look like a short
    corpus rather than like a misconfiguration."""
    with pytest.raises(ValueError, match="at least"):
        BoundedGraphRetriever(RecordedReadExecutor(), inner_search_limit=10)


def test_no_caller_parameter_can_reach_the_row_limit() -> None:
    """A model that could add `row_limit` and have it silently dropped has learned nothing."""
    tool, executor = retriever()

    result = tool.call("search_passages", {"terms": ["margin"], "row_limit": 10_000})

    assert result_code(result) == "unknown_parameter"
    assert not executor.calls


def test_two_identical_calls_issue_the_same_statement_and_return_rows_in_the_same_order() -> None:
    scripted = rows(3, passage_id="p")
    tool, executor = retriever(**{cypher.SEARCH_PASSAGES: scripted})

    first = tool.call("search_passages", {"terms": ["margin"]})
    second = tool.call("search_passages", {"terms": ["margin"]})

    assert first.rows == second.rows
    assert executor.calls[0].statement == executor.calls[1].statement
    assert executor.calls[0].parameters == executor.calls[1].parameters


# ---------------------------------------------------------------------------------------
# Lucene escaping — §9's five fixtures, and the word that is not a character
# ---------------------------------------------------------------------------------------


def test_a_passage_id_is_escaped_so_its_colons_cannot_prefix_a_lucene_field() -> None:
    escaped = escape_term("norm:0001801169:0001801169-22-000108:open-20220930.htm#p120")

    assert "\\:" in escaped
    assert ":" not in escaped.replace("\\:", "")
    assert "\\-" in escaped


def test_a_form_type_with_a_hyphen_and_a_slash_is_escaped() -> None:
    assert escape_term("8-K/A") == "8\\-K\\/A"


def test_a_quoted_row_label_becomes_a_phrase_with_its_own_quotes_escaped() -> None:
    escaped = escape_term('homes "on the market"')

    assert escaped.startswith('"') and escaped.endswith('"')
    assert escaped == '"homes \\"on the market\\""'


def test_a_date_range_cannot_become_a_lucene_range_query() -> None:
    """`[a TO b]` is range syntax and `TO` is an operator. Both are neutralised by the phrase."""
    escaped = escape_term("[2022-01-01 TO 2022-12-31]")

    assert escaped == '"[2022-01-01 TO 2022-12-31]"'
    assert not escaped.startswith("[")


def test_a_bare_not_is_quoted_so_it_cannot_exclude_evidence() -> None:
    """§9's fifth fixture, and the only one that is about a word rather than a character.

    `["margin", "NOT", "gross"]` unquoted is a boolean exclusion — a model-controlled channel
    for suppressing evidence, which is the failure §11 exists to prevent.
    """
    assert escape_term("NOT") == '"NOT"'
    assert build_query(["margin", "NOT", "gross"]) == 'margin "NOT" gross'


@pytest.mark.parametrize("word", sorted(RESERVED_WORDS))
def test_every_lucene_operator_word_is_quoted_rather_than_left_as_syntax(word: str) -> None:
    assert escape_term(word) == f'"{word}"'


def test_a_lower_case_operator_word_is_quoted_too_even_though_lucene_would_not_honour_it() -> None:
    """Wider than Lucene's own rule, on purpose.

    The default parser recognises only `NOT`, so quoting `not` protects against nothing today.
    It costs one pair of quotes and buys independence from a parser setting this package does
    not own — and the failure being guarded is a silent evidence exclusion, where over-quoting
    is the cheaper mistake. A quoted single word searches for that word.
    """
    assert escape_term("not") == '"not"'
    assert escape_term("Adjusted") == "Adjusted"


def test_the_built_query_never_joins_terms_with_an_operator() -> None:
    query = build_query(["adjusted", "EBITDA", "margin"])

    assert query == "adjusted EBITDA margin"
    assert " AND " not in query and " OR " not in query and " NOT " not in query


def test_a_term_list_that_escapes_to_nothing_is_refused_rather_than_searched() -> None:
    tool, executor = retriever()

    result = tool.call("search_passages", {"terms": ["", "   "]})

    assert result_code(result) == "no_searchable_terms"
    assert not executor.calls


def test_a_bare_string_of_terms_is_refused_because_it_would_search_one_term() -> None:
    tool, _executor = retriever()

    result = tool.call("search_passages", {"terms": "gross margin"})

    assert result_code(result) == "invalid_parameter"


# ---------------------------------------------------------------------------------------
# C3 — the citation quote is on the edge
# ---------------------------------------------------------------------------------------


def test_fact_evidence_returns_the_quote_from_the_evidenced_by_edge() -> None:
    """C3. `quoted_text` is a relationship property; a query returning only node fields loses
    every citation quote and still looks complete."""
    tool, _executor = retriever(**{cypher.FACT_EVIDENCE_FOR_OBSERVATION: (EVIDENCE_ROW,)})

    result = tool.call("get_fact_evidence", {"observation_id": EVIDENCE_ROW["observation_id"]})

    assert result.outcome is RetrievalOutcome.OK
    assert result.rows[0]["quoted_text"] == "(211)"
    assert result.rows[0]["block_ids"] == EVIDENCE_ROW["block_ids"]


def test_the_edge_table_id_and_the_passage_table_id_are_returned_under_different_names() -> None:
    """They are different properties with different occupancy — 2,690 edges against 503
    passages — and one name for both would manufacture agreement between them."""
    assert "evidence.table_id AS table_id" in cypher.FACT_EVIDENCE_FOR_OBSERVATION
    assert "passage.table_id AS passage_table_id" in cypher.FACT_EVIDENCE_FOR_OBSERVATION


# ---------------------------------------------------------------------------------------
# The table cell — the coordinates a label pair cannot supply (TABLE_CELL_CITATIONS §1.3)
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [cypher.METRIC_HISTORY, cypher.FACT_EVIDENCE_FOR_OBSERVATION],
    ids=["METRIC_HISTORY", "FACT_EVIDENCE_FOR_OBSERVATION"],
)
@pytest.mark.parametrize("field", CELL_INDEX_FIELDS)
def test_every_statement_that_reads_a_cell_returns_its_coordinates_and_not_only_its_labels(
    statement: str, field: str
) -> None:
    """Both statements already returned `row_label` and `column_label`, which locate nothing:
    a bare cell value occurs more than once in its own passage for 523 of 2,704 observations
    *(verified live 2026-08-13)*.
    Asserted as a named return on the statement text because that is the thing a future edit
    would drop, and a scripted executor would keep passing without it."""
    assert f"observation.{field} AS {field}" in statement


def test_a_table_backed_observation_is_retrieved_with_the_cell_its_value_was_printed_in() -> None:
    tool, _executor = retriever(**{cypher.FACT_EVIDENCE_FOR_OBSERVATION: (EVIDENCE_ROW,)})

    result = tool.call("get_fact_evidence", {"observation_id": EVIDENCE_ROW["observation_id"]})

    row = result.rows[0]
    assert [row[field] for field in CELL_INDEX_FIELDS] == [12, 3, 3, 2, 12]
    assert row["passage_kind"] == "table"


def test_a_table_backed_observation_record_carries_all_five_indices() -> None:
    """The contract this stage exists to widen: retrieval's two rows, merged into the record
    every detector and the packager read."""
    record = record_from_rows(HISTORY_ROW, EVIDENCE_ROW)

    assert [getattr(record, field) for field in CELL_INDEX_FIELDS] == [12, 3, 3, 2, 12]
    assert record.passage_kind == "table"
    assert record.quoted_text == "(211)"


def test_a_narrative_observation_record_carries_none_for_every_index_and_is_still_a_record() -> None:
    """14 of 2,704 observations have no table identity, and `None` is their honest answer —
    not a defect to raise on. The record still has to build, and its quote is still a quote."""
    record = record_from_rows(NARRATIVE_HISTORY_ROW, NARRATIVE_EVIDENCE_ROW)

    assert [getattr(record, field) for field in CELL_INDEX_FIELDS] == [None] * 5
    assert record.passage_kind == "narrative"
    assert record.quoted_text.startswith("Adjusted EBITDA was $218 million")


def test_the_cell_indices_survive_a_history_load_that_never_asked_for_evidence() -> None:
    """They are `:Observation` properties, so `get_metric_history` alone carries them. A
    coordinate that appeared only when a caller paid for a per-observation `get_fact_evidence`
    would be a cell identity that comes and goes; `passage_kind` is the one field that genuinely
    cannot, because it belongs to the `:Passage`."""
    record = record_from_rows(HISTORY_ROW)

    assert [getattr(record, field) for field in CELL_INDEX_FIELDS] == [12, 3, 3, 2, 12]
    assert record.passage_kind is None


def test_an_index_that_arrives_as_a_boolean_is_read_as_absent_rather_than_as_row_one() -> None:
    """`bool` is a subclass of `int` in Python, so an unguarded read would turn `True` into a
    coordinate that resolves to a real cell and names the wrong one."""
    record = record_from_rows({**HISTORY_ROW, "row_index": True, "value_column_index": False})

    assert record.row_index is None
    assert record.value_column_index is None


def test_a_missing_observation_is_not_found_rather_than_an_empty_ok() -> None:
    tool, _executor = retriever(**{cypher.FACT_EVIDENCE_FOR_OBSERVATION: ()})

    result = tool.call("get_fact_evidence", {"observation_id": "obs:nope"})

    assert result.outcome is RetrievalOutcome.NOT_FOUND
    assert "obs:nope" in result.reason


def test_get_fact_evidence_refuses_both_ids_at_once() -> None:
    tool, _executor = retriever()

    result = tool.call("get_fact_evidence", {"observation_id": "a", "event_id": "b"})

    assert result_code(result) == "ambiguous_fact_id"


def test_get_fact_evidence_refuses_neither_id() -> None:
    tool, _executor = retriever()

    assert result_code(tool.call("get_fact_evidence", {})) == "missing_parameter"


# ---------------------------------------------------------------------------------------
# C4 — the ontology is the authority for metric metadata
# ---------------------------------------------------------------------------------------


def test_a_metric_definition_is_built_from_the_ontology_and_never_from_the_node() -> None:
    """C4, tested where it would actually fail.

    The scripted node row carries a `percentage_min` the real graph does not have and could not
    have. If any code path preferred the node, the returned bound would be `999.0`; the ontology
    says `-100.0`, and that is what a percentage metric's §13.3 check has to be given.
    """
    node = {"metric_id": "adjusted_gross_margin", "graph_label": "Adjusted Gross Margin",
            "graph_ontology_definition_hash": "deadbeef", "percentage_min": 999.0}
    tool, _executor = retriever(**{cypher.METRIC_NODE: (node,)})

    result = tool.call("get_metric_definition", {"metric_id": "adjusted_gross_margin"})

    row = result.rows[0]
    assert row["metadata_source"] == "ontology"
    assert row["percentage_min"] == -100.0
    assert row["percentage_max"] == 100.0
    assert "gaap_gross_margin" in row["distinct_from"]


def test_the_metric_node_statement_reads_no_metadata_it_could_fall_back_to() -> None:
    """The strongest form of "no silent fallback": there is nothing to fall back to.

    `:Metric` carries 22 properties and none of these four *(checked live)*, so the statement
    cannot ask for them even by accident.
    """
    for absent in ("percentage_min", "percentage_max", "distinct_from", "reconciles_to"):
        assert absent not in cypher.METRIC_NODE


def test_a_metric_the_graph_never_projected_still_answers_from_the_ontology() -> None:
    tool, _executor = retriever(**{cypher.METRIC_NODE: ()})

    result = tool.call("get_metric_definition", {"metric_id": "adjusted_gross_margin"})

    assert result.outcome is RetrievalOutcome.OK
    assert result.rows[0]["present_in_graph"] is False
    assert result.rows[0]["percentage_min"] == -100.0


def test_an_ontology_hash_that_disagrees_with_the_graph_is_reported_on_the_row() -> None:
    """A definition read from a different ontology than the graph was projected under is the
    staleness §7 exists for, surfaced at the one tool that would otherwise launder it."""
    node = {"metric_id": "adjusted_gross_margin", "graph_ontology_definition_hash": "not-the-hash"}
    tool, _executor = retriever(**{cypher.METRIC_NODE: (node,)})

    result = tool.call("get_metric_definition", {"metric_id": "adjusted_gross_margin"})

    assert result.rows[0]["ontology_hash_matches_graph"] is False


def test_an_ambiguous_metric_surface_returns_every_candidate() -> None:
    """`gross margin` denotes `adjusted_gross_margin` and `gaap_gross_margin`, which moved
    13.2 -> 3.3 and 11.6 -> -12.6 across the same two quarters. Picking one would have written
    half the story before the planner saw it."""
    tool, executor = retriever()

    result = tool.call("get_metric_definition", {"metric_id": "gross margin"})

    assert result.outcome is RetrievalOutcome.AMBIGUOUS
    assert result_code(result) == "ambiguous_metric_surface"
    assert [row["metric_id"] for row in result.rows] == [
        "adjusted_gross_margin", "gaap_gross_margin"]
    assert not executor.calls, "an ambiguous surface must be refused before the graph is read"


def test_an_ambiguous_surface_is_refused_by_every_tool_that_names_a_metric() -> None:
    """§13.5 makes metric identity a REFUSE, not a `get_metric_definition` nicety."""
    tool, _executor = retriever()
    for name, parameters in (
        ("get_metric_history", {"metric_id": "gross margin"}),
        ("compare_metric_periods",
         {"metric_id": "gross margin", "period_key_a": "2022Q2", "period_key_b": "2022Q3"}),
        ("find_counter_evidence", {"metric_id": "gross margin", "period_key": "2022Q3"}),
    ):
        assert tool.call(name, parameters).outcome is RetrievalOutcome.AMBIGUOUS, name


def test_a_surface_the_ontology_does_not_know_is_not_found() -> None:
    tool, _executor = retriever()

    result = tool.call("get_metric_definition", {"metric_id": "ebitda per bushel"})

    assert result.outcome is RetrievalOutcome.NOT_FOUND


def test_list_metrics_reports_whether_the_ontology_defines_each_projected_metric() -> None:
    scripted = ({"metric_id": "adjusted_ebitda"}, {"metric_id": "invented_by_a_projection"})
    tool, _executor = retriever(**{cypher.LIST_METRICS: scripted})

    result = tool.call("list_metrics", {})

    assert [row["defined_in_ontology"] for row in result.rows] == [True, False]


# ---------------------------------------------------------------------------------------
# C2 — absent and null are the same thing, so shape comes from the fields present
# ---------------------------------------------------------------------------------------


def test_period_shape_is_derived_from_the_fields_present_and_not_from_a_property() -> None:
    """There is no `shape` property on an observation and there cannot be one: Neo4j stores no
    null, so `period_start` is either a date or the key is absent."""
    assert "shape" not in cypher.METRIC_HISTORY.split("RETURN")[0].replace("$shape", "")
    assert "observation.period_start IS NOT NULL" in cypher.METRIC_HISTORY
    assert "observation.instant_date IS NOT NULL" in cypher.METRIC_HISTORY


def test_a_shape_the_graph_cannot_express_is_refused() -> None:
    tool, executor = retriever()

    result = tool.call("get_metric_history", {"metric_id": "adjusted_ebitda", "shape": "quarter"})

    assert result_code(result) == "unknown_period_shape"
    assert not executor.calls


def test_compare_reports_how_many_observations_carried_a_sparse_property() -> None:
    """C2 where an aggregate would otherwise erase it: `collect()` drops absent values, so
    `distinct_currencies == []` cannot say whether nought or seven observations carried one."""
    assert "currency_present_count" in cypher.COMPARE_METRIC_PERIODS
    assert "period_start_present_count" in cypher.COMPARE_METRIC_PERIODS


def test_compare_refuses_two_periods_that_do_not_share_a_period_shape() -> None:
    scripted = (
        {"period_key": "2022Q3", "distinct_period_shapes": ["duration"], "distinct_units": ["USD"]},
        {"period_key": "2022-09-30", "distinct_period_shapes": ["instant"],
         "distinct_units": ["USD"]},
    )
    tool, _executor = retriever(**{cypher.COMPARE_METRIC_PERIODS: scripted})

    result = tool.call("compare_metric_periods", {
        "metric_id": "adjusted_ebitda", "period_key_a": "2022Q3", "period_key_b": "2022-09-30"})

    assert result_code(result) == "period_shape_mismatch"


def test_compare_refuses_one_metric_reported_in_two_units() -> None:
    scripted = (
        {"period_key": "2022Q2", "distinct_period_shapes": ["duration"], "distinct_units": ["USD"]},
        {"period_key": "2022Q3", "distinct_period_shapes": ["duration"],
         "distinct_units": ["percent"]},
    )
    tool, _executor = retriever(**{cypher.COMPARE_METRIC_PERIODS: scripted})

    result = tool.call("compare_metric_periods", {
        "metric_id": "adjusted_ebitda", "period_key_a": "2022Q2", "period_key_b": "2022Q3"})

    assert result_code(result) == "unit_mismatch"


def test_compare_reports_a_period_with_no_observation_as_not_found() -> None:
    scripted = ({"period_key": "2022Q2", "distinct_period_shapes": ["duration"],
                 "distinct_units": ["USD"]},)
    tool, _executor = retriever(**{cypher.COMPARE_METRIC_PERIODS: scripted})

    result = tool.call("compare_metric_periods", {
        "metric_id": "adjusted_ebitda", "period_key_a": "2022Q2", "period_key_b": "1999Q1"})

    assert result.outcome is RetrievalOutcome.NOT_FOUND
    assert "1999Q1" in result.reason


def test_compare_refuses_a_period_compared_with_itself() -> None:
    tool, executor = retriever()

    result = tool.call("compare_metric_periods", {
        "metric_id": "adjusted_ebitda", "period_key_a": "2022Q3", "period_key_b": "2022Q3"})

    assert result_code(result) == "period_keys_identical"
    assert not executor.calls


def test_compare_bounds_the_citation_handles_it_collects_per_period() -> None:
    tool, executor = retriever()
    tool.call("compare_metric_periods", {
        "metric_id": "adjusted_ebitda", "period_key_a": "2022Q2", "period_key_b": "2022Q3"})

    assert executor.calls[0].parameters["handle_limit"] == DEFAULT_HANDLE_LIMIT


# ---------------------------------------------------------------------------------------
# Passages, windows and the bounds on them
# ---------------------------------------------------------------------------------------


def test_a_missing_passage_is_not_found_and_the_reason_names_the_cited_subset() -> None:
    """8,776 cited passages against a 12,442-passage corpus: an id that exists upstream may
    legitimately not be here, and the caller needs to be told which kind of absence it is."""
    tool, _executor = retriever(**{cypher.PASSAGE_CONTEXT: ()})

    result = tool.call("get_passage_context", {"passage_id": "norm:nope#p1"})

    assert result.outcome is RetrievalOutcome.NOT_FOUND
    assert "8,776" in result.reason


@pytest.mark.parametrize("field", ["before", "after"])
def test_a_context_window_wider_than_three_is_refused_rather_than_clamped(field: str) -> None:
    """A clamp teaches a caller that its request was honoured."""
    tool, executor = retriever()

    result = tool.call("get_passage_context", {"passage_id": "p", field: 8})

    assert result_code(result) == "context_window_exceeds_bound"
    assert not executor.calls


def test_the_default_context_window_is_the_widest_the_plan_allows() -> None:
    """Read off the ids the tool asks for, since D8 made those the window.

    Seven ids centred on the anchor is `before=3 + anchor + after=3` and nothing else — the
    parameter no longer reaches the statement, so this is where the default is now visible.
    """
    tool, executor = retriever(**{cypher.PASSAGE_CONTEXT: rows(1)})
    tool.call("get_passage_context", {"passage_id": "norm:doc#p10"})

    asked = executor.calls[0].parameters["neighbour_passage_ids"]
    assert asked == [f"norm:doc#p{index}" for index in range(7, 14)]
    assert len(asked) == 2 * MAX_CONTEXT_NEIGHBOURS + 1


def test_passage_context_returns_the_gap_between_neighbours_rather_than_implying_contiguity() -> None:
    assert "offset_from_anchor" in cypher.PASSAGE_CONTEXT
    assert "AS passage_index" in cypher.PASSAGE_CONTEXT


# ---------------------------------------------------------------------------------------
# D8 — the neighbour ids are computed from the id grammar, not searched for
# ---------------------------------------------------------------------------------------


def test_the_passage_window_is_computed_from_the_id_and_never_leaves_the_document() -> None:
    """The grammar is `{document_id}#p{n}` and is total over all 8,776 passages (re-measured
    2026-08-03: every id splits in two, reconstructs exactly, and its ordinal round-trips
    through `toInteger`). So the window is arithmetic, and every id it produces carries the
    anchor's own document prefix — the window cannot cross a document because it cannot spell
    another one's id."""
    window = passage_window_ids("norm:a:b:c.htm#p120", before=2, after=1)

    assert window == ["norm:a:b:c.htm#p118", "norm:a:b:c.htm#p119",
                      "norm:a:b:c.htm#p120", "norm:a:b:c.htm#p121"]
    assert all(identifier.startswith("norm:a:b:c.htm#p") for identifier in window)


def test_the_passage_window_clamps_at_zero_rather_than_asking_for_a_negative_ordinal() -> None:
    assert passage_window_ids("norm:doc#p1", before=3, after=0) == [
        "norm:doc#p0", "norm:doc#p1"]


def test_the_passage_window_always_contains_the_anchor_even_with_no_neighbours() -> None:
    assert passage_window_ids("norm:doc#p7", before=0, after=0) == ["norm:doc#p7"]


@pytest.mark.parametrize(
    "identifier", ["norm:doc", "norm:doc#p", "norm:doc#p12#p3", "norm:doc#pxii", 120, None])
def test_an_id_the_grammar_cannot_parse_is_not_found_without_asking_the_graph(
    identifier: Any,
) -> None:
    """The grammar holds for every passage in the graph, so an id that does not parse cannot
    name one — and a query whose answer is already known is a query not worth issuing."""
    assert passage_window_ids(identifier, before=1, after=1) is None

    tool, executor = retriever()
    result = tool.call("get_passage_context", {"passage_id": identifier})

    assert result.outcome is RetrievalOutcome.NOT_FOUND
    assert not executor.calls


def test_passage_context_looks_neighbours_up_by_id_instead_of_scanning_the_document() -> None:
    """D8's fix, as a property of the statement: the non-indexable per-passage
    `toInteger(split(...))` filter that made the cost linear in document size is gone from the
    `WHERE`, and the anchor's `#p` arithmetic is all that is left of it."""
    assert "neighbour.passage_id IN $neighbour_passage_ids" in cypher.PASSAGE_CONTEXT
    where = cypher.PASSAGE_CONTEXT.split("RETURN")[0]
    assert "toInteger(split(neighbour" not in where
    assert "neighbour.document_id = anchor.document_id" in cypher.PASSAGE_CONTEXT


@pytest.mark.parametrize("value", ["2022-1-1", "not-a-date", "20220101", ""])
def test_a_malformed_date_is_refused_before_it_reaches_the_graph(value: str) -> None:
    """The graph stores dates as strings (F13), so a malformed one compares lexicographically
    against real dates and returns a quietly wrong window instead of an error."""
    tool, executor = retriever()

    result = tool.call("get_events_in_window", {"since": value, "until": "2022-12-31"})

    assert result_code(result) == "malformed_date"
    assert not executor.calls


def test_an_inverted_window_is_refused() -> None:
    tool, _executor = retriever()

    result = tool.call("get_events_in_window", {"since": "2022-12-31", "until": "2022-01-01"})

    assert result_code(result) == "inverted_window"


def test_the_event_window_names_which_date_put_a_row_in_it() -> None:
    """Only 3 of 6 events carry `occurred_on`; coalescing would report an announcement as an
    occurrence, which §5.5 and §13.8 both refuse."""
    assert "AS window_matched_on" in cypher.EVENTS_IN_WINDOW
    assert "coalesce(event.occurred_on, event.announced_on)" in cypher.EVENTS_IN_WINDOW
    assert "coalesce" not in cypher.EVENTS_IN_WINDOW.split("RETURN")[0]


# ---------------------------------------------------------------------------------------
# Counter-evidence, the blocked tools and the trace
# ---------------------------------------------------------------------------------------


def scope(**fields: Any) -> tuple[dict[str, Any], ...]:
    """One `COUNTER_EVIDENCE_SCOPE` row, defaulting to a metric that *is* observed."""
    return ({"metric_id": "adjusted_ebitda", "period_observation_count": 6,
             "metric_observation_count": 296, "recorded_issue_count": 2, **fields},)


def test_no_counter_evidence_is_an_ok_when_the_documents_that_report_the_fact_exist() -> None:
    """"This filing records no refused claim about this metric" is a finding about the corpus;
    a `NotFound` would make an honest absence look like a broken identifier. The scope read is
    what earns the `Ok`: six observations of the period, so there were documents to search."""
    tool, executor = retriever(**{cypher.COUNTER_EVIDENCE: (),
                                  cypher.COUNTER_EVIDENCE_SCOPE: scope()})

    result = tool.call("find_counter_evidence",
                       {"metric_id": "adjusted_ebitda", "period_key": "2022Q3"})

    assert result.outcome is RetrievalOutcome.OK
    assert result.rows == ()
    assert [call.statement for call in executor.calls] == [
        cypher.COUNTER_EVIDENCE, cypher.COUNTER_EVIDENCE_SCOPE]


def test_counter_evidence_with_rows_never_pays_for_the_scope_read() -> None:
    """D5 costs a second statement only where the first one's answer is ambiguous."""
    tool, executor = retriever(**{cypher.COUNTER_EVIDENCE: rows(3, severity="refusal")})

    tool.call("find_counter_evidence", {"metric_id": "adjusted_ebitda", "period_key": "2022Q3"})

    assert [call.statement for call in executor.calls] == [cypher.COUNTER_EVIDENCE]


def test_a_metric_with_no_observation_of_the_period_is_unavailable_and_not_an_empty_ok() -> None:
    """D5, the defect exactly. `revenue` carries 170 attempted issues and 0 observations
    *(verified live 2026-08-03)*, so the document scope is empty and every one of those 170 is
    excluded by construction — `Ok([])` said the filings had refused nothing, which is the
    opposite of the truth and the answer a planner asking "is there counter-evidence about
    revenue?" was given."""
    tool, _executor = retriever(
        **{cypher.COUNTER_EVIDENCE: (),
           cypher.COUNTER_EVIDENCE_SCOPE: scope(metric_id="revenue", period_observation_count=0,
                                                metric_observation_count=0,
                                                recorded_issue_count=170)})

    result = tool.call("find_counter_evidence", {"metric_id": "revenue", "period_key": "2022Q3"})

    assert result.outcome is RetrievalOutcome.UNAVAILABLE
    assert result_code(result) == "no_document_scope"
    assert "170" in result.reason, "the reason must name what was excluded, not just that it was"


def test_a_period_with_no_observation_is_unavailable_even_when_the_metric_has_others() -> None:
    """The same emptiness by a different route: the metric is measured, this period is not, and
    the scope is empty either way. One code, and a reason that separates the two counts."""
    tool, _executor = retriever(
        **{cypher.COUNTER_EVIDENCE: (),
           cypher.COUNTER_EVIDENCE_SCOPE: scope(period_observation_count=0)})

    result = tool.call("find_counter_evidence",
                       {"metric_id": "adjusted_ebitda", "period_key": "1999Q1"})

    assert result_code(result) == "no_document_scope"
    assert "296" in result.reason and "1999Q1" in result.reason


def test_a_metric_the_graph_never_projected_is_unavailable_rather_than_a_silent_empty_ok() -> None:
    tool, _executor = retriever(**{cypher.COUNTER_EVIDENCE: (),
                                   cypher.COUNTER_EVIDENCE_SCOPE: ()})

    result = tool.call("find_counter_evidence",
                       {"metric_id": "adjusted_ebitda", "period_key": "2022Q3"})

    assert result_code(result) == "no_metric_node"


def test_counter_evidence_orders_by_an_explicit_severity_rank_and_not_by_the_word() -> None:
    """D6. `{diagnostic, refusal, rejection}` sorts alphabetically with the strongest evidence
    last, so the bound dropped `rejection` rows first: `housing_inventory_homes` 2023-03-31 has
    34 refusals and 2 rejections in scope and returned 25 refusals *(verified live)*."""
    assert "ORDER BY severity_rank" in cypher.COUNTER_EVIDENCE
    assert "ORDER BY issue.severity" not in cypher.COUNTER_EVIDENCE
    ranks = [cypher.COUNTER_EVIDENCE.index(f"'{severity}' THEN") for severity in SEVERITY_ORDER]
    assert ranks == sorted(ranks), "the CASE does not rank most severe first"


def test_a_truncated_counter_evidence_result_says_what_the_bound_could_have_dropped() -> None:
    """`truncated=True` is a count and says nothing about kind. With the rank ordering it can
    say something exact and free: nothing dropped outranks anything returned."""
    cap = MAX_ROWS["find_counter_evidence"]
    scripted = tuple({"issue_id": f"iss:{index}", "severity": "refusal"}
                     for index in range(cap + 1))
    tool, _executor = retriever(**{cypher.COUNTER_EVIDENCE: scripted})

    result = tool.call("find_counter_evidence",
                       {"metric_id": "adjusted_ebitda", "period_key": "2022Q3"})

    assert result.outcome is RetrievalOutcome.OK
    assert result.truncated is True
    assert result_code(result) == "truncated_below_severity"
    assert "refusal" in result.reason


def test_an_untruncated_ok_still_says_nothing_it_does_not_have_to() -> None:
    tool, _executor = retriever(**{cypher.COUNTER_EVIDENCE: rows(2, severity="rejection")})

    result = tool.call("find_counter_evidence",
                       {"metric_id": "adjusted_ebitda", "period_key": "2022Q3"})

    assert result_code(result) == ""


def test_only_the_counter_evidence_tools_two_statements_name_an_issue() -> None:
    naming = sorted(name for name, statement in cypher.STATEMENTS.items()
                    if ":Issue" in statement)

    assert naming == ["COUNTER_EVIDENCE", "COUNTER_EVIDENCE_SCOPE"]


@pytest.mark.parametrize("tool_name", sorted(UNAVAILABLE_TOOLS))
def test_a_blocked_tool_answers_unavailable_with_a_reason_it_can_be_rendered_from(
    tool_name: str,
) -> None:
    """§9: *"`Unavailable` is an answer and is rendered as one"* — never a silence the model
    fills. So the reason carries a code a renderer can branch on and prose it can print."""
    tool, executor = retriever()

    result = tool.call(tool_name, {})

    assert result.outcome is RetrievalOutcome.UNAVAILABLE
    assert result_code(result) == UNAVAILABLE_TOOLS[tool_name][0]
    assert len(result.reason) > 60, "a reason nobody can read is a silence with extra steps"
    assert not executor.calls


def test_an_unknown_tool_is_refused_and_names_the_nine_that_exist() -> None:
    tool, _executor = retriever()

    result = tool.call("run_arbitrary_cypher", {})

    assert result_code(result) == "unknown_tool"
    assert "find_counter_evidence" in result.reason


def test_a_missing_required_parameter_is_refused() -> None:
    tool, _executor = retriever()

    assert result_code(tool.call("find_counter_evidence", {"metric_id": "revenue"})) \
        == "missing_parameter"


def test_the_trace_records_every_call_including_the_ones_that_refused() -> None:
    """§10 requires the package to carry a retrieval trace. A trace that recorded only successes
    would make a package look like it had never been told anything."""
    tool, _executor = retriever(**{cypher.SEARCH_PASSAGES: rows(2)})
    tool.call("search_passages", {"terms": ["margin"]})
    tool.call("get_guidance_history", {})
    tool.call("nope", {})

    trace = tool.trace()

    assert [entry.tool for entry in trace] == ["search_passages", "get_guidance_history", "nope"]
    assert [entry.outcome for entry in trace] == [
        RetrievalOutcome.OK, RetrievalOutcome.UNAVAILABLE, RetrievalOutcome.REFUSED]
    assert trace[0].row_count == 2
    assert trace[0].parameters == {"terms": ["margin"]}


def test_the_trace_records_the_bound_that_bit() -> None:
    tool, _executor = retriever(
        **{cypher.SEARCH_PASSAGES: rows(MAX_ROWS["search_passages"] + 1)})
    tool.call("search_passages", {"terms": ["margin"]})

    assert tool.trace()[0].truncated is True


def test_a_database_that_raises_becomes_unavailable_rather_than_a_traceback() -> None:
    """A dead or timed-out database is a state §11's planner branches on. `story/` may not
    import `neo4j`, so it cannot catch the driver's exceptions by type — the executor call is
    wrapped instead, and nothing of this package's own logic is inside the `try`."""
    executor = RecordedReadExecutor(raises=RuntimeError("Neo.ClientError.Transaction.Terminated"))
    tool = BoundedGraphRetriever(executor, registry=default_registry())

    result = tool.call("search_passages", {"terms": ["margin"]})

    assert result.outcome is RetrievalOutcome.UNAVAILABLE
    assert result_code(result) == "graph_read_failed"
    assert tool.trace()[0].outcome is RetrievalOutcome.UNAVAILABLE


# ---------------------------------------------------------------------------------------
# Citation handles — §9: a fact without one is not a return value
# ---------------------------------------------------------------------------------------


def test_every_tool_declares_the_citation_handles_its_statement_returns() -> None:
    """Checked against the queries, not against a hand-kept list: the failure this guards is a
    query that stops returning a handle, and a test that only read `CITATION_FIELDS` would go on
    passing through exactly that change."""
    by_tool = {
        "list_metrics": cypher.LIST_METRICS,
        "get_metric_history": cypher.METRIC_HISTORY,
        "get_fact_evidence": cypher.FACT_EVIDENCE_FOR_OBSERVATION,
        "get_passage_context": cypher.PASSAGE_CONTEXT,
        "search_passages": cypher.SEARCH_PASSAGES,
        "get_events_in_window": cypher.EVENTS_IN_WINDOW,
        "find_counter_evidence": cypher.COUNTER_EVIDENCE,
    }
    for tool_name, statement in by_tool.items():
        for field in CITATION_FIELDS[tool_name]:
            assert f"AS {field}" in statement, f"{tool_name} does not return {field}"


def test_every_returned_row_carries_the_citation_handles_the_tool_declares() -> None:
    handles = CITATION_FIELDS["get_fact_evidence"]
    tool, _executor = retriever(**{cypher.FACT_EVIDENCE_FOR_OBSERVATION: (EVIDENCE_ROW,)})

    result = tool.call("get_fact_evidence", {"observation_id": EVIDENCE_ROW["observation_id"]})

    for field in handles:
        assert result.rows[0][field], f"{field} is empty; §9 calls that not a return value"


def _minimal_parameters(name: str, required: frozenset[str]) -> dict[str, Any]:
    """The smallest parameter map that reaches the database for each tool."""
    supplied: dict[str, Any] = {}
    for key in required:
        if key == "terms":
            supplied[key] = ["margin"]
        elif key in {"since", "until"}:
            supplied[key] = "2022-01-01" if key == "since" else "2022-12-31"
        elif key == "metric_id":
            supplied[key] = "adjusted_ebitda"
        elif key == "period_key":
            supplied[key] = "2022Q3"
        elif key == "period_key_a":
            supplied[key] = "2022Q2"
        elif key == "period_key_b":
            supplied[key] = "2022Q3"
        elif key == "passage_id":
            supplied[key] = "norm:x#p1"
    if name == "get_fact_evidence":
        supplied["observation_id"] = "obs:x"
    return supplied


# ---------------------------------------------------------------------------------------
# Live — every tool against the loaded graph, on the 2022Q3 cluster
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_retriever():  # type: ignore[no-untyped-def]
    """A retriever over the real adapter, skipped when the container is not up.

    `verify_connectivity` first, deliberately: F12 measured that the handshake fails in 0.0s
    against a refused port while `execute_query` retries for 35 seconds, so a skip costs
    nothing and a naive probe would cost half a minute per test.
    """
    from story.context import build_story_context

    context = build_story_context()
    health = context.executor.verify_connectivity()
    if not health.ok:
        context.close()
        pytest.skip(f"neo4j unavailable: {health.status} — {health.detail}")
    try:
        yield BoundedGraphRetriever(context.executor)
    finally:
        context.close()


def _values(result: Any, field: str) -> list[Any]:
    return [row[field] for row in result.rows]


@pytest.mark.neo4j
def test_live_list_metrics_returns_the_twenty_six_projected_metrics(live_retriever) -> None:  # type: ignore[no-untyped-def]
    result = live_retriever.call("list_metrics", {})

    assert result.outcome is RetrievalOutcome.OK
    assert len(result.rows) == 26
    assert result.truncated is False
    assert all(row["defined_in_ontology"] for row in result.rows)
    assert _values(result, "metric_id") == sorted(_values(result, "metric_id"))


@pytest.mark.neo4j
def test_live_metric_definition_reads_percentage_bounds_the_node_does_not_carry(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    """C4 against the real graph: the node exists, the bounds come from the ontology, and the
    graph's own ontology hash matches the one the definition was read from."""
    result = live_retriever.call("get_metric_definition", {"metric_id": "adjusted_gross_margin"})

    row = result.rows[0]
    assert row["present_in_graph"] is True
    assert row["ontology_hash_matches_graph"] is True
    assert (row["percentage_min"], row["percentage_max"]) == (-100.0, 100.0)
    assert row["metadata_source"] == "ontology"


@pytest.mark.neo4j
def test_live_an_ambiguous_surface_returns_both_margin_metrics(live_retriever) -> None:  # type: ignore[no-untyped-def]
    result = live_retriever.call("get_metric_definition", {"metric_id": "gross margin"})

    assert result.outcome is RetrievalOutcome.AMBIGUOUS
    assert _values(result, "metric_id") == ["adjusted_gross_margin", "gaap_gross_margin"]


@pytest.mark.neo4j
def test_live_metric_history_returns_2022_adjusted_ebitda_in_chronological_order(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    result = live_retriever.call("get_metric_history", {
        "metric_id": "adjusted_ebitda", "shape": "duration",
        "since": "2022-01-01", "until": "2022-12-31"})

    assert result.outcome is RetrievalOutcome.OK
    assert result.rows
    assert all(row["period_shape"] == "duration" for row in result.rows)
    assert _values(result, "period_end") == sorted(_values(result, "period_end"))
    by_period = {row["period_key"]: row["value"] for row in result.rows}
    assert by_period["2022Q2"] == 218_000_000.0
    assert by_period["2022Q3"] == -211_000_000.0


@pytest.mark.neo4j
def test_live_metric_history_returns_the_same_rows_in_the_same_order_twice(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    parameters = {"metric_id": "adjusted_ebitda", "since": "2022-01-01", "until": "2022-12-31"}

    first = live_retriever.call("get_metric_history", dict(parameters))
    second = live_retriever.call("get_metric_history", dict(parameters))

    assert _values(first, "observation_id") == _values(second, "observation_id")


@pytest.mark.neo4j
@pytest.mark.parametrize(
    "metric_id, before, after",
    [("adjusted_ebitda", 218_000_000.0, -211_000_000.0),
     ("gaap_gross_margin", 11.6, -12.6),
     ("adjusted_gross_margin", 13.2, 3.3)],
)
def test_live_the_2022q3_cluster_is_three_sign_reversals_the_graph_agrees_on(
    live_retriever, metric_id: str, before: float, after: float,  # type: ignore[no-untyped-def]
) -> None:
    """The values are pinned *and* re-derived: the aggregate returns `distinct_values`, so a
    period the corpus disagrees with itself about would arrive as two elements and fail here
    rather than being averaged into a story."""
    result = live_retriever.call("compare_metric_periods", {
        "metric_id": metric_id, "period_key_a": "2022Q2", "period_key_b": "2022Q3"})

    assert result.outcome is RetrievalOutcome.OK
    by_period = {row["period_key"]: row for row in result.rows}
    assert by_period["2022Q2"]["distinct_values"] == [before]
    assert by_period["2022Q3"]["distinct_values"] == [after]
    assert by_period["2022Q3"]["distinct_period_shapes"] == ["duration"]
    assert by_period["2022Q3"]["observation_ids"], "no citation handle for the compared period"


@pytest.mark.neo4j
def test_live_a_percentage_metric_carries_no_currency_and_says_how_many_rows_lacked_one(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    """C2 in the one place an aggregate would erase it: `gaap_gross_margin` observations carry
    no `currency` at all, so the collection is empty *and* the count is nought — two facts, and
    the collection alone could not have supplied the second."""
    result = live_retriever.call("compare_metric_periods", {
        "metric_id": "gaap_gross_margin", "period_key_a": "2022Q2", "period_key_b": "2022Q3"})

    for row in result.rows:
        assert row["distinct_currencies"] == []
        assert row["currency_present_count"] == 0
        assert row["period_start_present_count"] == row["observation_count"]


@pytest.mark.neo4j
def test_live_fact_evidence_reads_the_quote_off_the_evidenced_by_edge(live_retriever) -> None:  # type: ignore[no-untyped-def]
    """C3, against the graph. Every 2022Q3 `adjusted_ebitda` observation is evidenced by a
    passage whose edge carries `(211)` — the printed form, on the relationship, four characters
    long, exactly as §13.7 measured."""
    history = live_retriever.call("get_metric_history", {
        "metric_id": "adjusted_ebitda", "since": "2022-07-01", "until": "2022-09-30"})
    observation_ids = [row["observation_id"] for row in history.rows
                       if row["period_key"] == "2022Q3"]
    assert observation_ids, "the 2022Q3 cluster is missing from the graph"

    result = live_retriever.call("get_fact_evidence", {"observation_id": observation_ids[0]})

    assert result.outcome is RetrievalOutcome.OK
    assert result.rows[0]["quoted_text"] == "(211)"
    assert result.rows[0]["value"] == -211_000_000.0
    assert result.rows[0]["source_url"].startswith("https://www.sec.gov/")
    assert result.rows[0]["block_ids"]


@pytest.mark.neo4j
def test_live_fact_evidence_for_an_event_returns_a_sentence_and_names_no_numeral(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    """§13.8: an event's properties are free-text strings, so `property_names` is returned and
    the values are not — a model handed `prop_headcount_reduced` would bind it as a fact."""
    events = live_retriever.call(
        "get_events_in_window", {"since": "2022-01-01", "until": "2022-12-31"})
    event_id = events.rows[0]["event_id"]

    result = live_retriever.call("get_fact_evidence", {"event_id": event_id})

    assert result.outcome is RetrievalOutcome.OK
    assert len(result.rows[0]["quoted_text"]) > 40
    assert isinstance(result.rows[0]["property_names"], list)
    assert not any(key.startswith("prop_") for key in result.rows[0])


@pytest.mark.neo4j
def test_live_passage_context_returns_the_anchor_with_its_cited_neighbours(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    anchor = "norm:0001801169:0001801169-22-000108:open-20220930.htm#p120"

    result = live_retriever.call("get_passage_context",
                                 {"passage_id": anchor, "before": 2, "after": 2})

    assert result.outcome is RetrievalOutcome.OK
    assert len(result.rows) <= MAX_ROWS["get_passage_context"]
    anchors = [row for row in result.rows if row["is_anchor"]]
    assert len(anchors) == 1 and anchors[0]["passage_id"] == anchor
    assert anchors[0]["offset_from_anchor"] == 0
    assert _values(result, "passage_index") == sorted(_values(result, "passage_index"))
    assert all(row["document_id"] == anchors[0]["document_id"] for row in result.rows)
    assert all(-2 <= row["offset_from_anchor"] <= 2 for row in result.rows)


@pytest.mark.neo4j
def test_live_the_widest_context_window_costs_the_same_in_a_long_document_as_a_short_one(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    """D8's claim, stated as behaviour rather than as a db-hit count a test cannot see: the
    window is the same seven-id lookup wherever it lands, so the 453-passage `open-20201231.htm`
    and the 183-passage `open-20220930.htm` answer identically shaped results. The db-hit
    measurement is in `cypher.PASSAGE_CONTEXT` — 968 to 112 on the long one."""
    long_document = "norm:0001801169:0001801169-21-000011:open-20201231.htm"
    short_document = "norm:0001801169:0001801169-22-000108:open-20220930.htm"

    for document in (long_document, short_document):
        result = live_retriever.call("get_passage_context",
                                     {"passage_id": f"{document}#p120"})

        assert result.outcome is RetrievalOutcome.OK, document
        assert len(result.rows) == 7, document
        assert _values(result, "offset_from_anchor") == [-3, -2, -1, 0, 1, 2, 3], document
        assert all(row["document_id"] == document for row in result.rows), document


@pytest.mark.neo4j
def test_live_a_passage_id_that_is_not_cited_by_any_fact_is_not_found(live_retriever) -> None:  # type: ignore[no-untyped-def]
    result = live_retriever.call("get_passage_context", {"passage_id": "norm:nope#p1"})

    assert result.outcome is RetrievalOutcome.NOT_FOUND


@pytest.mark.neo4j
@pytest.mark.parametrize(
    "term",
    ["norm:0001801169:0001801169-22-000108:open-20220930.htm#p120",
     "8-K/A",
     'homes "on the market"',
     "[2022-01-01 TO 2022-12-31]",
     "NOT"],
)
def test_live_every_escaping_fixture_reaches_lucene_as_data_rather_than_syntax(
    live_retriever, term: str,  # type: ignore[no-untyped-def]
) -> None:
    """§9's five fixtures, run against the real `passage_text` index.

    The assertion is that the call *succeeds*: an unescaped colon, bracket or bare `NOT` is a
    Lucene parse error or a boolean exclusion, and either one arrives here as something other
    than an `Ok`.
    """
    result = live_retriever.call("search_passages", {"terms": [term]})

    assert result.outcome is RetrievalOutcome.OK, result.reason
    assert len(result.rows) <= MAX_ROWS["search_passages"]


@pytest.mark.neo4j
def test_live_a_bare_not_searches_for_the_word_instead_of_excluding_anything(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    """The exclusion channel, closed and measured. Unquoted, `["margin", "NOT", "gross"]` would
    subtract `gross` from the result; quoted, every hit contains one of the three words."""
    result = live_retriever.call("search_passages", {"terms": ["margin", "NOT", "gross"]})

    assert result.outcome is RetrievalOutcome.OK
    assert result.rows
    assert any("gross" in row["text_excerpt"].lower() for row in result.rows), (
        "no hit contains 'gross', which is what a boolean exclusion would look like")


@pytest.mark.neo4j
def test_live_search_filters_by_document_type_and_filing_window(live_retriever) -> None:  # type: ignore[no-untyped-def]
    result = live_retriever.call("search_passages", {
        "terms": ["Adjusted EBITDA"], "document_types": ["earnings_release"],
        "since": "2022-01-01", "until": "2022-12-31"})

    assert result.outcome is RetrievalOutcome.OK
    assert result.rows
    assert all(row["document_type"] == "earnings_release" for row in result.rows)
    assert all("2022-01-01" <= row["filing_date"] <= "2022-12-31" for row in result.rows)


@pytest.mark.neo4j
def test_live_events_in_2022_are_the_two_dated_ones_and_say_which_date_matched(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    result = live_retriever.call(
        "get_events_in_window", {"since": "2022-01-01", "until": "2022-12-31"})

    assert result.outcome is RetrievalOutcome.OK
    assert {row["event_type_id"] for row in result.rows} == {
        "credit_facility_established", "workforce_reduction"}
    assert all(row["window_matched_on"] in {"occurred_on", "announced_on"} for row in result.rows)
    assert all(row["passage_id"] and row["document_id"] for row in result.rows)


@pytest.mark.neo4j
def test_live_counter_evidence_returns_refusals_and_never_an_unasked_question(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    """`contribution_margin` 2022Q3 has nine — every one an `AMBIGUOUS_ALIAS` refusal about a
    row the extractor would not resolve. None is a `:NotAttempted` issue; there are 10,852 of
    those, and surfacing one would report the run's own bounds as a finding about Opendoor."""
    result = live_retriever.call("find_counter_evidence",
                                 {"metric_id": "contribution_margin", "period_key": "2022Q3"})

    assert result.outcome is RetrievalOutcome.OK
    assert result.rows
    assert all(row["severity"] in set(SEVERITY_ORDER) for row in result.rows)
    assert all(row["passage_id"] and row["document_id"] for row in result.rows)
    ordering = [(row["severity_rank"], row["code"], row["issue_id"]) for row in result.rows]
    assert ordering == sorted(ordering), "the statement's ORDER BY did not hold"
    assert all(row["severity_rank"] == SEVERITY_ORDER.index(row["severity"])
               for row in result.rows)


@pytest.mark.neo4j
def test_live_truncation_keeps_the_rejections_the_lexicographic_order_dropped(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    """D6 against the graph. `housing_inventory_homes` 2023-03-31 has 34 refusals and 2
    rejections in scope; ordered by the word, `rejection` sorted last and the 25-row bound threw
    both away. Ordered by rank, both survive and the refusals are what the bound costs."""
    result = live_retriever.call(
        "find_counter_evidence",
        {"metric_id": "housing_inventory_homes", "period_key": "2023-03-31"})

    assert result.outcome is RetrievalOutcome.OK
    assert result.truncated is True
    severities = [row["severity"] for row in result.rows]
    assert severities.count("rejection") == 2, f"a rejection was dropped again: {severities}"
    assert severities[:2] == ["rejection", "rejection"]
    assert result_code(result) == "truncated_below_severity"
    assert "refusal" in result.reason


@pytest.mark.neo4j
def test_live_a_metric_with_no_refused_claim_returns_an_empty_ok(live_retriever) -> None:  # type: ignore[no-untyped-def]
    result = live_retriever.call("find_counter_evidence",
                                 {"metric_id": "adjusted_ebitda", "period_key": "2022Q3"})

    assert result.outcome is RetrievalOutcome.OK
    assert result.rows == ()
    assert result_code(result) == ""


@pytest.mark.neo4j
def test_live_revenue_reports_that_it_has_no_scope_instead_of_no_counter_evidence(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    """D5 against the graph, on the metric that shows why it matters. `revenue` has 170 recorded
    issues and not one observation — it has no number *because* of them — and the tool used to
    answer `Ok([])`, which `results.ok` documents as "this filing refused nothing"."""
    result = live_retriever.call("find_counter_evidence",
                                 {"metric_id": "revenue", "period_key": "2022Q3"})

    assert result.outcome is RetrievalOutcome.UNAVAILABLE
    assert result_code(result) == "no_document_scope"
    assert "170" in result.reason


@pytest.mark.neo4j
def test_live_search_reports_the_candidate_pool_it_ranked(live_retriever) -> None:  # type: ignore[no-untyped-def]
    """D7: the pool is bounded, and its size is on every row so a starved filter is legible.
    `["the"]` matches 6,873 passages and the pool caps at 500; `EBITDA` in earnings releases
    exhausts its matches and reports fewer, which is the difference the field exists to show."""
    broad = live_retriever.call("search_passages", {"terms": ["the"]})
    narrow = live_retriever.call("search_passages", {"terms": ["EBITDA"],
                                                     "document_types": ["earnings_release"]})

    assert broad.outcome is RetrievalOutcome.OK and narrow.outcome is RetrievalOutcome.OK
    assert all(row["candidate_pool_size"] == DEFAULT_INNER_SEARCH_LIMIT for row in broad.rows)
    assert all(row["candidate_pool_size"] < DEFAULT_INNER_SEARCH_LIMIT for row in narrow.rows)


@pytest.mark.neo4j
def test_live_the_trace_carries_one_entry_per_call_with_a_measured_elapsed_time(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    before = len(live_retriever.trace())
    live_retriever.call("list_metrics", {})
    trace = live_retriever.trace()

    assert len(trace) == before + 1
    assert trace[-1].tool == "list_metrics"
    assert trace[-1].row_count == 26
    assert trace[-1].elapsed_ms > 0


@pytest.mark.neo4j
def test_live_every_observation_carries_all_five_cell_indices_or_none_of_them(
    live_retriever,  # type: ignore[no-untyped-def]
) -> None:
    """The census the offline fixtures stand in for, over every observation in the graph.

    All-or-nothing is the claim that matters. A record with three of five coordinates could not
    be resolved and could not be refused either — it would look like a citable cell and index
    into the wrong one. Measured 2026-08-13: 2,690 complete, 14 empty, 0 partial.

    Paged through the loader rather than issued directly, because `adjusted_ebitda` alone has
    296 observations against `get_metric_history`'s bound of 200 and a single call would count
    a truncated page.
    """
    from story.stages.detection.canonicalization import load_observations

    load = load_observations(live_retriever, with_evidence=False)
    assert load.unreadable == ()

    complete = [r for r in load.records
                if all(getattr(r, f) is not None for f in CELL_INDEX_FIELDS)]
    empty = [r for r in load.records
             if all(getattr(r, f) is None for f in CELL_INDEX_FIELDS)]

    assert len(load.records) == len(complete) + len(empty), "an observation carries a partial cell"
    assert (len(complete), len(empty)) == (2690, 14)
    assert all(r.row_index == r.metric_label_row_index for r in complete)
