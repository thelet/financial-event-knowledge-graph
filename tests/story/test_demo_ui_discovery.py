"""Discovery for the demo UI: the event contract, the filters, and the claims about both.

Offline by default. `FakeGraph` below answers the three §9 tools `load_observations` uses from a
hand-built observation table, so every stage from canonicalisation to ranking runs for real
against a series small enough to reason about. The one test that needs the database is marked
`neo4j` and is the one that pins the demo candidate's rank and its suppressed score term.

The synthetic corpus uses **real metric ids** — `adjusted_gross_margin` and `gaap_gross_margin`
— rather than invented ones, because `detector_config`'s threshold families, the ontology's
formula components and §6.6 D4's declared pairs are all keyed by metric id, and a made-up metric
would be refused by three tables before any detector looked at its numbers.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any, Mapping

import pytest

from story.core.models import RetrievalOutcome, RetrievalResult, RetrievalTraceEntry
from story.demo_ui import discovery
from story.demo_ui.discovery import (
    HIGHLIGHT_LIMIT,
    STAGES,
    STATUS_PASSED,
    STATUS_FAILED,
    STATUS_RUNNING,
    DiscoveryFilters,
    StaleGraphRefused,
    UnknownStoryType,
    run_discovery,
)
from story.stages.freshness import FreshnessCheck, FreshnessReport, RefusalCode
from story.stages.ranking import WEIGHTS, ordering_key

GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"

#: The candidate §8b demonstrates, and the one the UI must be able to surface without it.
DEMO_CANDIDATE_ID = (
    "cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:"
    "opendoor:2022Q3:9682f1c1c85a"
)

#: Twelve quarters of two margins that agree, then stop agreeing — the shape §6.6 D4 is about,
#: at a scale a reader can check by eye. The last two steps of `adjusted_gross_margin` are large
#: enough for D1's 3.0-point margin bar and reverse a run, so D1 and D2 fire too.
QUARTERS = (
    ("2021Q1", "2021-01-01", "2021-03-31"),
    ("2021Q2", "2021-04-01", "2021-06-30"),
    ("2021Q3", "2021-07-01", "2021-09-30"),
    ("2021Q4", "2021-10-01", "2021-12-31"),
    ("2022Q1", "2022-01-01", "2022-03-31"),
    ("2022Q2", "2022-04-01", "2022-06-30"),
    ("2022Q3", "2022-07-01", "2022-09-30"),
    ("2022Q4", "2022-10-01", "2022-12-31"),
    ("2023Q1", "2023-01-01", "2023-03-31"),
    ("2023Q2", "2023-04-01", "2023-06-30"),
    ("2023Q3", "2023-07-01", "2023-09-30"),
    ("2023Q4", "2023-10-01", "2023-12-31"),
)

ADJUSTED = (11.0, 11.4, 11.1, 11.6, 11.2, 11.5, 3.1, 4.0, 5.4, 7.6, 11.0, 11.3)
GAAP = (9.9, 10.2, 10.0, 10.4, 10.1, 10.3, 10.0, 10.2, 10.1, 10.4, 10.2, 10.3)


class FakeGraph:
    """A `story.contracts.GraphRetriever` over a table in memory. No driver, no server.

    Structural conformance and nothing inherited, for the reason `contracts.py` gives: the
    protocol exists so every stage above it is drivable with nothing running, and a fake that
    reached for the real retriever to satisfy it would quietly undo that.

    `get_metric_history` answers whole — this corpus is well under §9's 200-row bound — so the
    paging walk in `load_observations` terminates on the first page, exactly as it does live for
    21 of the 26 projected metrics.
    """

    def __init__(self, rows: Mapping[str, tuple[dict[str, Any], ...]]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, tool: str, parameters: Mapping[str, Any]) -> RetrievalResult:
        self.calls.append((tool, dict(parameters)))
        if tool == "list_metrics":
            return RetrievalResult(
                outcome=RetrievalOutcome.OK,
                rows=tuple({"metric_id": metric_id} for metric_id in sorted(self._rows)))
        if tool == "get_metric_history":
            return RetrievalResult(
                outcome=RetrievalOutcome.OK,
                rows=self._rows.get(str(parameters.get("metric_id")), ()))
        if tool == "get_fact_evidence":
            observation_id = str(parameters.get("observation_id"))
            return RetrievalResult(
                outcome=RetrievalOutcome.OK,
                rows=({"observation_id": observation_id,
                       "quoted_text": "13.2%",
                       "filing_date": "2023-02-14"},))
        return RetrievalResult(outcome=RetrievalOutcome.NOT_FOUND, reason=f"no tool {tool}")

    def trace(self) -> tuple[RetrievalTraceEntry, ...]:
        return ()


def observation(metric_id: str, index: int, value: float) -> dict[str, Any]:
    period_key, start, end = QUARTERS[index]
    return {
        "observation_id": f"obs:{metric_id}:{period_key}",
        "metric_id": metric_id,
        "subject_entity_id": "opendoor",
        "period_start": start,
        "period_end": end,
        "instant_date": None,
        "value": value,
        "unit": "percent",
        "scale": None,
        "currency": None,
        "source_lane": "normalized_table",
        "validation_state": "ok",
        "document_id": f"doc:{period_key}",
        "passage_id": f"psg:{metric_id}:{period_key}",
        "warning_codes": [],
        "ambiguity_codes": [],
    }


def fake_graph() -> FakeGraph:
    return FakeGraph({
        "adjusted_gross_margin": tuple(
            observation("adjusted_gross_margin", index, value)
            for index, value in enumerate(ADJUSTED)),
        "gaap_gross_margin": tuple(
            observation("gaap_gross_margin", index, value)
            for index, value in enumerate(GAAP)),
    })


@pytest.fixture
def result():
    return run_discovery(fake_graph(), graph_run_id=GRAPH_RUN_ID,
                         filters=DiscoveryFilters(max_suggestions=50))


# -- the fake has to be worth testing against ------------------------------------------------


def test_the_synthetic_corpus_actually_fires_more_than_one_detector(result):
    """A guard on the guards: every claim below is vacuous over an empty candidate set."""
    firing = {outcome.story_type for outcome in result.detectors if outcome.candidates}
    assert len(firing) >= 2, {o.story_type: len(o.candidates) for o in result.detectors}
    assert result.ranking.ranked
    assert result.census.slots == 24


# -- the event contract (§3) -------------------------------------------------------------------


def test_every_stage_that_ran_opens_and_closes_in_the_declared_order(result):
    """The closed set, and the order. A stage that did no work emits nothing at all."""
    seen = [(event.stage, event.status) for event in result.events]
    assert {stage for stage, _ in seen} <= set(STAGES)

    # `freshness` did not run — no report was supplied — so it must not appear.
    assert "freshness" not in {stage for stage, _ in seen}

    expected: list[tuple[str, str]] = []
    for stage in STAGES:
        if stage == "freshness":
            continue
        expected += [(stage, STATUS_RUNNING), (stage, STATUS_PASSED)]
    assert seen == expected


def test_the_stage_and_status_vocabulary_is_the_trace_contracts_own():
    """Discovery borrows `trace.py`'s closed sets rather than minting a second pair.

    `TraceEvent` validates `stage` against `STAGES_BY_PHASE` and `status` against a `Literal`, so
    a name of this module's own invention would not be a naming difference — it would be a
    `ValidationError` the first time an event was streamed. Ten of the twelve stages are
    identical and `TRACE_STAGES` records the two that are not: `metric_history`, which the
    discovery phase has no name for, and `freshness`, which `trace.py` files under the
    *generation* phase even though discovery gates before it reads.
    """
    from story.demo_ui import trace

    assert set(discovery.STATUSES) <= set(trace.Status.__args__)
    assert set(discovery.TRACE_STAGES.values()) <= set(trace.STAGES)
    assert set(discovery.TRACE_STAGES) == set(STAGES)

    discovery_stages = set(trace.STAGES_BY_PHASE["discovery"])
    identical = {stage for stage in STAGES if discovery.TRACE_STAGES[stage] == stage}
    assert identical - {"freshness"} == discovery_stages
    assert discovery.TRACE_STAGES["metric_history"] == "ranking"
    assert "freshness" in trace.STAGES_BY_PHASE["generation"]
    assert "freshness" not in discovery_stages


def test_the_sink_sees_each_event_as_it_is_emitted():
    """Streamed, not collected and flushed: the panel is useless if it arrives at the end."""
    streamed: list[tuple[str, str]] = []
    outcome = run_discovery(
        fake_graph(), graph_run_id=GRAPH_RUN_ID,
        on_event=lambda event: streamed.append((event.stage, event.status)))
    assert streamed == [(event.stage, event.status) for event in outcome.events]
    assert [event.sequence for event in outcome.events] == list(range(len(outcome.events)))


def test_every_highlighted_id_resolves_to_something_the_run_produced(result):
    """§3's rule, executable: *"highlights are never faked."*"""
    highlighted = {identifier
                   for event in result.events
                   for identifier in event.highlight_node_ids}
    assert highlighted, "no event highlighted anything; the rule below proves nothing"
    assert highlighted <= result.resolvable_ids()


def test_a_truncated_highlight_list_says_that_it_truncated(result):
    """A cap that silently dropped ids would make a short highlight read as a small finding."""
    for event in result.events:
        assert len(event.highlight_node_ids) <= HIGHLIGHT_LIMIT
        if event.highlights_truncated:
            assert len(event.highlight_node_ids) == HIGHLIGHT_LIMIT
        else:
            assert len(event.highlight_node_ids) < HIGHLIGHT_LIMIT


def test_counts_are_read_off_the_stage_and_not_estimated(result):
    """Every number on an event is traceable to a value a stage returned."""
    by_stage = {(event.stage, event.status): event for event in result.events}

    snapshot = by_stage[("loading_graph_snapshot", STATUS_PASSED)]
    assert snapshot.accepted == len(result.load.records)
    assert snapshot.processed == result.load.calls
    assert snapshot.refused == len(result.load.unreadable)

    canonical = by_stage[("canonical_series", STATUS_PASSED)]
    assert canonical.processed == result.census.observations
    assert canonical.accepted == result.census.slots - result.census.conflict

    sweep = by_stage[("comparability", STATUS_PASSED)]
    assert sweep.processed == result.comparability.pairs
    assert sweep.accepted + sweep.refused == result.comparability.pairs

    for outcome in result.detectors:
        stage = discovery._DETECTOR_STAGES[outcome.detector_id]
        event = by_stage[(stage, STATUS_PASSED)]
        assert event.accepted == len(outcome.candidates)
        assert event.refused == len(outcome.refusals)

    ranking = by_stage[("ranking", STATUS_PASSED)]
    assert ranking.accepted == len(result.ranking.ranked)
    assert ranking.warnings == sum(1 for row in result.ranking.ranked if row.suppressed)

    suggestions = by_stage[("preparing_suggestions", STATUS_PASSED)]
    assert suggestions.accepted == len(result.suggestions)
    assert suggestions.processed == len(result.ranking.ranked)


def test_the_candidate_total_is_the_sum_of_the_four_detectors(result):
    """The package-level number no single detector can see."""
    assert len(result.ranking.ranked) == sum(
        len(outcome.candidates) for outcome in result.detectors)
    assert len({row.candidate.candidate_id for row in result.ranking.ranked}) == len(
        result.ranking.ranked)


# -- the score, and the reason a term is missing -------------------------------------------------


def test_a_suggestion_carries_the_reason_each_absent_term_is_absent(result):
    """The whole point of returning `RankedCandidate` rather than `CandidateScore`.

    A suppressed component must be *absent* from `components` and *present* in `suppressed` with
    a sentence: a zero contribution and an uncomputable one are different findings.
    """
    with_reasons = [s for s in result.suggestions if s.breakdown.suppressed]
    assert with_reasons, "no candidate in this corpus suppressed a term"
    for suggestion in with_reasons:
        breakdown = suggestion.breakdown
        for name, reason in breakdown.suppressed.items():
            assert name in WEIGHTS
            assert name not in breakdown.components
            assert reason.strip()


def test_the_serialised_score_carries_components_contributions_and_suppressions(result):
    payload = result.as_dict()
    for row in payload["suggestions"]:
        score = row["score"]
        assert set(score) == {"total", "components", "contributions", "suppressed",
                              "policy_version", "is_probability"}
        assert score["is_probability"] is False
        assert set(score["components"]) & set(score["suppressed"]) == set()
        assert round(sum(score["contributions"].values()), 6) == round(score["total"], 6)


def test_the_score_is_labelled_a_heuristic_and_never_a_probability(result):
    """§7 of the plan, rendered rather than assumed: *ranking scores are not probabilities*."""
    payload = result.as_dict()
    assert "probabilit" in payload["score_disclaimer"].lower()
    assert payload["ranking_policy_version"] == result.ranking_policy_version


# -- grouping, ranking and order -----------------------------------------------------------------


def test_the_grouping_the_ui_shows_is_the_grouping_the_ranker_used(result):
    """`_group` recomputes §6.6 D3's clusters so the UI has a stage to render.

    That is only honest if the two agree. Compared as sets of member tuples, because the two
    call sites build them in the same per-audience order and equality of the sequence would be
    asserting the loop rather than the result.
    """
    mine = {cluster.member_ids for cluster in result.clusters}
    ranker = {cluster.member_ids for cluster in result.ranking.clusters}
    assert mine == ranker


def test_suggestions_arrive_in_the_ranking_stages_own_order(result):
    """The tie-break is `ordering_key`'s and the UI may not have a second one."""
    keys = [ordering_key(suggestion.row) for suggestion in result.suggestions]
    assert keys == sorted(keys)
    assert [s.position for s in result.suggestions] == sorted(s.position
                                                              for s in result.suggestions)


def test_position_counts_down_the_whole_run_while_rank_is_the_clusters(result):
    """Two numbers because §6.10 gives every cluster member its representative's rank."""
    positions = [suggestion.position for suggestion in result.suggestions]
    assert positions == list(range(1, len(positions) + 1))
    assert all(1 <= suggestion.rank <= len(result.ranking.ranked)
               for suggestion in result.suggestions)


def test_two_runs_over_one_graph_produce_one_result():
    """§6.10's reproducibility requirement, over the whole composition and not one stage.

    `elapsed_ms` is dropped before comparison and nothing else is: it is the only clock in the
    module and it enters no score, no id and no order.
    """
    def payload() -> dict[str, Any]:
        body = run_discovery(fake_graph(), graph_run_id=GRAPH_RUN_ID).as_dict()
        for event in body["events"]:
            event.pop("elapsed_ms")
        return body

    assert payload() == payload()


# -- filters -------------------------------------------------------------------------------------


def test_a_story_type_filter_accepts_a_type_or_a_detector_id(result):
    for spelling in ("metric_move", "detector:metric_move"):
        narrowed = run_discovery(
            fake_graph(), graph_run_id=GRAPH_RUN_ID,
            filters=DiscoveryFilters(story_types=(spelling,), max_suggestions=50))
        assert narrowed.suggestions
        assert {s.candidate.story_type for s in narrowed.suggestions} == {"metric_move"}
        assert narrowed.matched < len(result.ranking.ranked)


def test_an_unknown_story_type_refuses_rather_than_returning_nothing():
    """An empty list and a typo are indistinguishable to a reader, and only one is a mistake."""
    with pytest.raises(UnknownStoryType):
        run_discovery(fake_graph(), graph_run_id=GRAPH_RUN_ID,
                      filters=DiscoveryFilters(story_types=("metric_moves",)))


def test_a_metric_filter_keeps_only_candidates_about_that_metric():
    narrowed = run_discovery(
        fake_graph(), graph_run_id=GRAPH_RUN_ID,
        filters=DiscoveryFilters(metric_ids=("gaap_gross_margin",), max_suggestions=50))
    assert narrowed.suggestions
    for suggestion in narrowed.suggestions:
        assert "gaap_gross_margin" in suggestion.candidate.metric_ids


def test_a_subject_filter_keeps_only_that_subject():
    kept = run_discovery(
        fake_graph(), graph_run_id=GRAPH_RUN_ID,
        filters=DiscoveryFilters(subject_entity_id="opendoor", max_suggestions=50))
    dropped = run_discovery(
        fake_graph(), graph_run_id=GRAPH_RUN_ID,
        filters=DiscoveryFilters(subject_entity_id="zillow", max_suggestions=50))
    assert kept.suggestions
    assert dropped.suggestions == ()
    assert dropped.matched == 0


def test_a_period_range_is_read_through_the_runs_own_anchor_dates(result):
    """Anchor dates and not period keys — see `DiscoveryFilters`' docstring for why."""
    late = run_discovery(
        fake_graph(), graph_run_id=GRAPH_RUN_ID,
        filters=DiscoveryFilters(period_from="2023-01-01", max_suggestions=50))
    assert late.suggestions
    assert late.matched < result.matched
    for suggestion in late.suggestions:
        anchored = [late.periods[key] for key in suggestion.candidate.anchor_period_keys
                    if key in late.periods]
        assert max(anchored) >= "2023-01-01"


def test_the_period_map_lets_a_ui_offer_keys_and_send_dates(result):
    assert result.periods["2022Q3"] == "2022-09-30"
    assert set(result.periods) == {key for key, _start, _end in QUARTERS}


def test_a_minimum_score_and_a_maximum_count_both_bind(result):
    floor = max(row.score.total for row in result.ranking.ranked)
    narrowed = run_discovery(
        fake_graph(), graph_run_id=GRAPH_RUN_ID,
        filters=DiscoveryFilters(min_score=floor, max_suggestions=50))
    assert narrowed.matched >= 1
    assert all(s.breakdown.total >= floor for s in narrowed.suggestions)

    capped = run_discovery(fake_graph(), graph_run_id=GRAPH_RUN_ID,
                           filters=DiscoveryFilters(max_suggestions=2))
    assert len(capped.suggestions) == 2
    assert capped.matched == result.matched


def test_the_external_audience_filter_is_honest_about_binding_on_nothing(result):
    """Every candidate this pipeline produces today is `external` — D15–D17 are not built.

    Asserted rather than left implicit: the filter is real and it currently removes nothing, and
    a reader of the UI should be told that rather than shown a control that looks decorative.
    """
    external = run_discovery(
        fake_graph(), graph_run_id=GRAPH_RUN_ID,
        filters=DiscoveryFilters(external_only=True, max_suggestions=50))
    assert external.matched == result.matched
    assert {s.candidate.audience.value for s in external.suggestions} == {"external"}


def test_a_filter_never_changes_a_rank_or_a_score(result):
    """Filters run *after* ranking, so a reader's question cannot move a candidate's score.

    §6.10's novelty term counts prior candidates over the run's candidate set and §6.6 D3
    clusters over the same set; filtering first would make a rank depend on what was asked for.
    """
    narrowed = run_discovery(
        fake_graph(), graph_run_id=GRAPH_RUN_ID,
        filters=DiscoveryFilters(metric_ids=("gaap_gross_margin",), max_suggestions=50))
    whole = {row.candidate.candidate_id: (row.score.rank, row.score.total, row.score.dedup_group)
             for row in result.ranking.ranked}
    for suggestion in narrowed.suggestions:
        assert whole[suggestion.candidate.candidate_id] == (
            suggestion.rank, suggestion.breakdown.total, suggestion.row.score.dedup_group)


def test_the_run_says_why_it_excluded_what_it_excluded(result):
    narrowed = run_discovery(
        fake_graph(), graph_run_id=GRAPH_RUN_ID,
        filters=DiscoveryFilters(story_types=("metric_move",), max_suggestions=50))
    closing = [e for e in narrowed.events
               if e.stage == "preparing_suggestions" and e.status == STATUS_PASSED]
    assert closing[0].detail["excluded_by"].get("story_type", 0) > 0


# -- the freshness gate --------------------------------------------------------------------------


def refused_report() -> FreshnessReport:
    return FreshnessReport(
        graph_run_id=GRAPH_RUN_ID,
        checks=(
            FreshnessCheck.comparing("load_marker_status", RefusalCode.LOAD_INCOMPLETE,
                                     expected="complete", observed="complete"),
            FreshnessCheck.refusing("package_input_digest",
                                    RefusalCode.PACKAGE_INPUT_DIGEST_MISMATCH,
                                    expected="abc", observed="def"),
        ),
    )


def test_a_stale_graph_stops_the_run_after_the_event_that_says_why():
    """The refusal is emitted *before* it is raised, so a streaming UI shows the reason."""
    events: list[Any] = []
    graph = fake_graph()
    with pytest.raises(StaleGraphRefused) as raised:
        run_discovery(graph, graph_run_id=GRAPH_RUN_ID,
                      on_event=events.append, freshness=refused_report())
    assert [(e.stage, e.status) for e in events] == [
        ("freshness", STATUS_RUNNING), ("freshness", STATUS_FAILED)]
    assert events[-1].detail["codes"] == ["package_input_digest_mismatch"]
    assert raised.value.report.graph_run_id == GRAPH_RUN_ID
    # The gate is not advisory: a refused run reads nothing at all (§7, §17.8).
    assert graph.calls == []


def test_a_passing_report_opens_the_stream_and_is_carried_into_the_response():
    passing = FreshnessReport(
        graph_run_id=GRAPH_RUN_ID,
        checks=(FreshnessCheck.comparing("load_marker_status", RefusalCode.LOAD_INCOMPLETE,
                                         expected="complete", observed="complete"),))
    outcome = run_discovery(fake_graph(), graph_run_id=GRAPH_RUN_ID, freshness=passing)
    assert outcome.events[0].stage == "freshness"
    assert outcome.events[1].status == STATUS_PASSED
    assert outcome.as_dict()["freshness"]["graph_run_id"] == GRAPH_RUN_ID


# -- the security set: no model anywhere near this -----------------------------------------------


#: Field names only a model-authored artifact carries. `raw_content` is §3's named case; the
#: others are the fields the planner, the writer and the verifier fill from a generation.
MODEL_AUTHORED_FIELDS = (
    "raw_content", "content", "thesis", "why_it_matters", "key_points", "counterpoints",
    "sentences", "draft", "plan", "post", "prompt", "system", "completion", "model_id",
    "finish_reason", "uncertainty",
)


def keys_of(payload: Any) -> set[str]:
    if isinstance(payload, dict):
        return set(payload) | {k for value in payload.values() for k in keys_of(value)}
    if isinstance(payload, list):
        return {k for item in payload for k in keys_of(item)}
    return set()


def test_no_response_body_carries_a_model_authored_field(result):
    """§3: `raw_content` is never routed anywhere — and here there was no model at all."""
    assert keys_of(result.as_dict()) & set(MODEL_AUTHORED_FIELDS) == set()


def test_discovery_names_no_model_no_provider_and_no_driver():
    """The import set, read from the AST rather than trusted.

    `story.context` is on the forbidden list for a structural reason and not a stylistic one:
    the composition root constructs the Bolt adapter, and
    `test_no_driver_is_reachable_from_the_contract_the_core_or_any_stage` walks a guarded import
    exactly as it walks a plain one — so naming it here, even under `TYPE_CHECKING`, would put
    the driver in this module's closure.
    """
    path = pathlib.Path(discovery.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ("neo4j", "httpx", "story.providers", "story.stages.generation",
                 "story.stages.verification", "story.context", "story.cli", "story.pipeline")
    offences = sorted(name for name in imported
                      if any(name == f or name.startswith(f + ".") for f in forbidden))
    assert offences == []


def test_the_result_exposes_the_ranking_stages_own_rows(result):
    """§2 of the brief: the in-memory row, not the persisted `CandidateScore`.

    Driven through the interface rather than asserted with `isinstance`: the row must answer
    `suppressed`, which is exactly the field `CandidateScore` does not have.
    """
    row = result.suggestions[0].row
    assert isinstance(row.suppressed, dict) or hasattr(row.suppressed, "items")
    assert not hasattr(row.score, "suppressed")
    assert row.score.candidate_id == row.candidate.candidate_id


# -- live ----------------------------------------------------------------------------------------


@pytest.mark.neo4j
def test_the_demo_candidate_is_discovered_ranked_and_explained():
    """The §8b candidate, reached by discovery rather than by being named.

    Measured against `graph-v1-0483dc6b4b10` on 2026-08-04: 262 candidates — 227 `metric_move`,
    11 `trend_reversal`, 9 `acceleration`, 15 `cross_metric_divergence` — collapsing to 112
    stories. The demo candidate is **6th of 262** in the ordering with a total of `0.670794506`,
    and it carries `rank 1` because §6.6 D3 collapses it into the cluster whose representative
    is `cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d`.

    Its one suppressed term is the reason this whole return type exists: D4 publishes a `z`, a
    mean and a σ over the pair's gap distribution and **no p90**, so `magnitude_units` has no
    input and is absent rather than zero.
    """
    from story.context import build_story_context
    from story.demo_ui.discovery import discover_from_context

    root = pathlib.Path(__file__).resolve().parents[2]
    context = build_story_context(root)
    try:
        outcome = discover_from_context(
            context, graph_run_id=GRAPH_RUN_ID,
            filters=DiscoveryFilters(max_suggestions=20))
    finally:
        context.close()

    assert len(outcome.ranking.ranked) == 262
    assert {o.story_type: len(o.candidates) for o in outcome.detectors} == {
        "metric_move": 227, "trend_reversal": 11, "acceleration": 9,
        "cross_metric_divergence": 15}

    suggestion = outcome.suggestion(DEMO_CANDIDATE_ID)
    assert suggestion is not None, "the demo candidate is not in the default top 20"
    assert suggestion.position == 6
    assert suggestion.rank == 1
    assert suggestion.breakdown.total == pytest.approx(0.670794506)
    assert set(suggestion.breakdown.suppressed) == {"magnitude_units"}
    assert "p90" in suggestion.breakdown.suppressed["magnitude_units"]
    assert "magnitude_units" not in suggestion.breakdown.components
    assert outcome.freshness is not None and outcome.freshness.passed
