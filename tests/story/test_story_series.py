"""S2 — period shapes, `canon-policy:1.0.0`, and the census it produces on the current run.

Offline by default. The `neo4j`-marked tests at the bottom rebuild the whole canonical layer
from the live graph and assert §6.1's and §6.2's numbers against it — the acceptance condition
of the step is that the series is *recomputed*, not copied, so those tests exist and the
offline ones cannot stand in for them.

The comparability rules R1–R10 are tested in `test_story_comparability.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import pytest

from story.core.models import RetrievalOutcome, RetrievalResult, RetrievalTraceEntry
from story.core.periods import (
    SHAPE_STEP_MONTHS,
    PeriodShape,
    classify_shape,
    months_between,
    period_key_of,
    story_period,
)
from story.core.series import (
    DELTA_PRECISION,
    PERCENT_TOLERANCE,
    SCALE_TOLERANCE,
    CanonicalStatus,
    ObservationRecord,
    build_series,
    presentation_tolerance,
    within_tolerance,
)
from story.stages.detection.canonicalization import (
    FILING_DATE_UNKNOWN,
    LANE_DEFECT,
    MINORITY_READING_PRESENT,
    QUARANTINE_NOT_EVALUATED,
    canonicalize,
    canonicalize_slot,
    census,
    is_flattened_table_read,
    load_observations,
)

#: Two real narrative quotes from `graph-v1-0483dc6b4b10`, copied exactly. The first is the
#: sentence §6.1 names as the reason the date mask exists; without the mask it reads
#: `31, 2023, 18` and is falsely quarantined.
REAL_DATED_NARRATIVE = (
    "As of December 31, 2023, 18% of our homes had been listed on the market for more than "
    "120 days versus 21% for the broader market as adjusted for our buybox."
)
REAL_PROSE_NARRATIVE = (
    "Adjusted EBITDA was $218 million in 2Q22 compared to $25 million in 2Q21."
)


def make_record(**overrides: Any) -> ObservationRecord:
    """One table-lane observation of `adjusted_ebitda` 2022Q3, with keyword overrides."""
    fields: dict[str, Any] = dict(
        observation_id="obs:adjusted-ebitda:opendoor:2022Q3:normalized-table:aaaaaaaaaaaa",
        metric_id="adjusted_ebitda",
        subject_entity_id="opendoor",
        period=story_period("2022-07-01", "2022-09-30", None),
        value=-211_000_000.0,
        unit="USD",
        scale="millions",
        currency="USD",
        source_lane="normalized_table",
        validation_state="clean",
        document_id="norm:0001801169:0001801169-22-000108:open-20220930.htm",
        passage_id="norm:0001801169:0001801169-22-000108:open-20220930.htm#p1",
        filing_date="2022-11-03",
        quoted_text="(211,000)",
    )
    fields.update(overrides)
    return ObservationRecord(**fields)


# ---------------------------------------------------------------------------------------
# R3's shape classifier and the period key it travels with
# ---------------------------------------------------------------------------------------


def test_a_calendar_quarter_is_a_quarter_and_carries_the_extraction_packages_period_key():
    period = story_period("2022-07-01", "2022-09-30", None)

    assert period.shape is PeriodShape.QUARTER
    assert period.key == "2022Q3"
    assert period.anchor_date == "2022-09-30"
    assert period_key_of("2022-07-01", "2022-09-30", None) == "2022Q3"


def test_the_first_quarter_is_classified_as_a_quarter_and_not_as_a_year_to_date_window():
    """§6.9 R3: `2020-01-01..2020-03-31` is both a quarter and a YTD-3M; it is a quarter."""
    assert classify_shape("2020-01-01", "2020-03-31", None) is PeriodShape.QUARTER


def test_a_window_that_ends_before_the_last_day_of_its_month_is_not_a_quarter():
    """The latent gap §6.9 records. Zero rows in the run have this shape, so it is synthetic.

    Without the `end.day` test `2022-04-01..2022-06-15` classifies as a quarter and a 76-day
    window is compared against 91-day ones.
    """
    assert classify_shape("2022-04-01", "2022-06-15", None) is PeriodShape.OTHER
    assert classify_shape("2022-04-01", "2022-06-30", None) is PeriodShape.QUARTER


def test_the_last_day_of_february_is_read_from_the_calendar_and_not_from_a_table():
    """A February quarter-end differs by a day between a leap year and an ordinary one, and a
    hard-coded 28 or 29 would misclassify one of them. No such window exists in this corpus —
    every quarter here starts in January, April, July or October — so this is synthetic too."""
    assert classify_shape("2019-12-01", "2020-02-29", None) is PeriodShape.OTHER
    assert classify_shape("2019-12-01", "2020-02-28", None) is PeriodShape.OTHER


def test_a_calendar_year_is_a_fiscal_year_and_the_two_year_to_date_windows_are_named():
    assert classify_shape("2021-01-01", "2021-12-31", None) is PeriodShape.FISCAL_YEAR
    assert classify_shape("2021-01-01", "2021-06-30", None) is PeriodShape.YTD_6M
    assert classify_shape("2021-01-01", "2021-09-30", None) is PeriodShape.YTD_9M


@pytest.mark.parametrize(
    "start,end",
    [("2022-04-01", "2022-12-31"), ("2022-07-01", "2023-03-31"), ("2022-10-01", "2023-06-30")],
)
def test_each_of_the_three_cross_year_windows_in_the_run_is_other(start, end):
    """Six observations live in these three windows *(verified live)*. R3 refuses `other`, so
    they are excluded from every comparison."""
    assert classify_shape(start, end, None) is PeriodShape.OTHER


def test_an_instant_is_classified_from_its_own_date_and_keeps_that_date_as_its_key():
    period = story_period(None, None, "2022-09-30")

    assert period.shape is PeriodShape.INSTANT
    assert period.is_instant
    assert period.key == "2022-09-30"
    assert period.anchor_date == "2022-09-30"


def test_a_date_that_does_not_parse_is_other_rather_than_an_exception():
    """F13: every date in this graph is a `String`, so a malformed one is a data state a
    detector must skip, not a traceback a run dies of."""
    assert classify_shape("2022-13-01", "2022-15-31", None) is PeriodShape.OTHER
    assert classify_shape(None, None, "not-a-date") is PeriodShape.OTHER
    assert classify_shape(None, "2022-09-30", None) is PeriodShape.OTHER


def test_months_between_counts_whole_months_across_a_year_boundary():
    assert months_between("2021-12-31", "2022-03-31") == 3
    assert months_between("2021-12-31", "2022-12-31") == 12
    assert months_between("2022-09-30", "2022-09-30") == 0
    assert months_between("2022-09-30", "nonsense") is None


def test_every_comparable_shape_declares_a_step_and_other_deliberately_does_not():
    assert set(SHAPE_STEP_MONTHS) == set(PeriodShape) - {PeriodShape.OTHER}


# ---------------------------------------------------------------------------------------
# §6.1 step 2 — presentation tolerance
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "unit,scale,expected",
    [
        ("USD", "millions", 1e6),
        ("USD", "thousands", 1e3),
        ("USD", "units", 1.0),
        ("USD", None, 1.0),
        ("percent", "units", 0.1),
        ("percent", None, 0.1),
        ("homes", "units", 1.0),
        ("markets", None, 1.0),
    ],
)
def test_presentation_tolerance_follows_the_unit_and_the_printed_scale(unit, scale, expected):
    assert presentation_tolerance(unit, scale) == expected


def test_a_difference_of_exactly_one_printed_unit_is_inside_the_tolerance_after_rounding():
    """D9. `within_tolerance` did not honour its own docstring until the difference was rounded.

    `7.4 − 7.3` is `0.10000000000000053` in IEEE 754 and `gaap_gross_margin` holds exactly those
    two values in adjacent quarters, so R8 let one printed unit through as a movement — the whole
    blast radius, measured across every canonical series in the run, being that one step. The
    tolerances are unchanged and `<=` is unchanged; only the residue is gone.
    """
    assert (7.4 - 7.3) > PERCENT_TOLERANCE
    assert round(7.4 - 7.3, DELTA_PRECISION) == PERCENT_TOLERANCE
    assert within_tolerance(7.4, 7.3, PERCENT_TOLERANCE)
    assert within_tolerance(7.3, 7.4, PERCENT_TOLERANCE)

    # Two printed units is still a movement: rounding to nine places cannot reach a tenth.
    assert not within_tolerance(7.5, 7.3, PERCENT_TOLERANCE)
    # And the same rule one scale up, where the residue does not arise at all.
    assert within_tolerance(25_000_000.0, 24_999_000.0, SCALE_TOLERANCE["millions"])
    assert not within_tolerance(27_000_000.0, 25_000_000.0, SCALE_TOLERANCE["millions"])


# ---------------------------------------------------------------------------------------
# §6.1 step 1 — the quarantine guard, which fires on nothing today
# ---------------------------------------------------------------------------------------


def test_a_flattened_table_row_of_four_percentages_is_recognised_as_one():
    assert is_flattened_table_read("18% 21% 23% 46%") is True


def test_three_numerals_separated_only_by_punctuation_are_a_flattened_row():
    assert is_flattened_table_read("(130,837) (211,000) (351,000)") is True


def test_two_numerals_are_not_enough_to_flatten_a_row():
    assert is_flattened_table_read("18% 21%") is False


def test_the_date_mask_keeps_the_real_narrative_sentence_out_of_quarantine():
    """§6.1: *"the date mask is load-bearing"*. This exact sentence is in the run twice."""
    assert is_flattened_table_read(REAL_DATED_NARRATIVE) is False


def test_a_narrative_sentence_naming_two_figures_in_prose_is_not_quarantined():
    assert is_flattened_table_read(REAL_PROSE_NARRATIVE) is False


def test_a_thousands_separated_number_is_one_numeral_and_not_three():
    assert is_flattened_table_read("1,234,567") is False


def test_only_a_narrative_observation_is_quarantined_however_flattened_its_quote_is():
    """A table-lane quote *is* a table cell; quarantining it would delete the lane."""
    flattened = "18% 21% 23% 46%"
    table = canonicalize_slot([make_record(quoted_text=flattened)])
    narrative = canonicalize_slot(
        [make_record(source_lane="normalized_narrative", quoted_text=flattened)]
    )

    assert table.quarantined_observation_ids == ()
    assert narrative.quarantined_observation_ids == (table.representative_observation_id,)
    assert LANE_DEFECT in narrative.warnings


def test_a_narrative_observation_with_no_quote_is_kept_and_the_point_says_so():
    """An absent quote is a statement about the loader, not about the observation."""
    point = canonicalize_slot(
        [make_record(source_lane="normalized_narrative", quoted_text=None)]
    )

    assert point.quarantined_observation_ids == ()
    assert QUARANTINE_NOT_EVALUATED in point.warnings
    assert point.status is CanonicalStatus.OK


def test_a_slot_whose_every_reading_was_quarantined_emits_no_value():
    point = canonicalize_slot(
        [make_record(source_lane="normalized_narrative", quoted_text="18% 21% 23% 46%")]
    )

    assert point.status is CanonicalStatus.CONFLICT
    assert point.value is None
    assert point.has_value is False


# ---------------------------------------------------------------------------------------
# §6.1 steps 2–6 — clustering, majority, representative, provenance
# ---------------------------------------------------------------------------------------


def test_two_printings_of_one_quarter_a_half_million_apart_are_one_reading():
    """The real case: `adjusted_ebitda 2021Q2` is filed as 25,579,000 and as 25,000,000."""
    point = canonicalize_slot(
        [
            make_record(observation_id="obs:a", value=25_579_000.0, scale="thousands",
                        document_id="doc-a"),
            make_record(observation_id="obs:b", value=25_000_000.0, scale="millions",
                        document_id="doc-b"),
        ]
    )

    assert point.status is CanonicalStatus.OK
    assert len(point.clusters) == 1
    assert point.distinct_values == 2
    assert point.n_docs == 2
    assert point.supporting_observation_ids == ("obs:a", "obs:b")
    assert point.minority_observation_ids == ()


def test_the_representative_is_the_more_precise_printing_and_its_value_is_the_points():
    point = canonicalize_slot(
        [
            make_record(observation_id="obs:millions", value=25_000_000.0, scale="millions",
                        filing_date="2021-08-11"),
            make_record(observation_id="obs:thousands", value=25_579_000.0, scale="thousands",
                        filing_date="2021-08-11"),
        ]
    )

    assert point.representative_observation_id == "obs:thousands"
    assert point.value == 25_579_000.0


def test_the_earliest_filing_breaks_a_tie_between_two_printings_of_equal_precision():
    point = canonicalize_slot(
        [
            make_record(observation_id="obs:late", filing_date="2023-02-15"),
            make_record(observation_id="obs:early", filing_date="2022-11-03"),
        ]
    )

    assert point.representative_observation_id == "obs:early"
    assert (point.first_filed, point.last_filed) == ("2022-11-03", "2023-02-15")


def test_an_observation_with_no_known_filing_date_loses_the_earliest_filing_tie_break():
    """Missing sorts last: a record with no filing date cannot claim to be the earliest."""
    point = canonicalize_slot(
        [
            make_record(observation_id="obs:aaa", filing_date=None),
            make_record(observation_id="obs:zzz", filing_date="2022-11-03"),
        ]
    )

    assert point.representative_observation_id == "obs:zzz"
    assert FILING_DATE_UNKNOWN in point.warnings


def test_two_readings_with_a_two_to_one_document_majority_resolve_by_majority():
    records = [
        make_record(observation_id=f"obs:major-{index}", value=100.0, unit="percent",
                    scale="units", currency=None, document_id=f"doc-{index}")
        for index in range(2)
    ] + [
        make_record(observation_id="obs:minor", value=900.0, unit="percent", scale="units",
                    currency=None, document_id="doc-9")
    ]

    point = canonicalize_slot(records)

    assert point.status is CanonicalStatus.RESOLVED_BY_MAJORITY
    assert point.value == 100.0
    assert point.supporting_observation_ids == ("obs:major-0", "obs:major-1")
    assert point.minority_observation_ids == ("obs:minor",)
    assert MINORITY_READING_PRESENT in point.warnings


def test_two_readings_one_document_each_conflict_and_the_slot_emits_no_value():
    records = [
        make_record(observation_id="obs:one", value=100.0, unit="percent", scale="units",
                    currency=None, document_id="doc-1"),
        make_record(observation_id="obs:two", value=900.0, unit="percent", scale="units",
                    currency=None, document_id="doc-2"),
    ]

    point = canonicalize_slot(records)

    assert point.status is CanonicalStatus.CONFLICT
    assert point.value is None
    assert point.has_value is False
    assert point.supporting_observation_ids == ()
    assert point.minority_observation_ids == ("obs:one", "obs:two")
    assert len(point.clusters) == 2


def test_one_document_cannot_carry_a_majority_however_many_rows_it_files():
    """The majority is over distinct documents, so six rows of one filing are one source."""
    records = [
        make_record(observation_id=f"obs:many-{index}", value=100.0, unit="percent",
                    scale="units", currency=None, document_id="doc-1")
        for index in range(6)
    ] + [
        make_record(observation_id="obs:other", value=900.0, unit="percent", scale="units",
                    currency=None, document_id="doc-2")
    ]

    point = canonicalize_slot(records)

    assert point.status is CanonicalStatus.CONFLICT


def test_a_three_to_two_document_split_is_a_disagreement_and_not_a_resolution():
    """`>= 2x the runner-up` is the bar; three against two does not clear it."""
    records = [
        make_record(observation_id=f"obs:top-{index}", value=100.0, unit="percent",
                    scale="units", currency=None, document_id=f"doc-top-{index}")
        for index in range(3)
    ] + [
        make_record(observation_id=f"obs:next-{index}", value=900.0, unit="percent",
                    scale="units", currency=None, document_id=f"doc-next-{index}")
        for index in range(2)
    ]

    assert canonicalize_slot(records).status is CanonicalStatus.CONFLICT


def test_canonicalisation_does_not_depend_on_the_order_the_records_arrive_in():
    """A canonical value that moved with page size would take every candidate id with it."""
    records = [
        make_record(observation_id="obs:c", value=25_100_000.0, scale="millions"),
        make_record(observation_id="obs:a", value=25_579_000.0, scale="thousands"),
        make_record(observation_id="obs:b", value=25_000_000.0, scale="millions"),
    ]

    forwards = canonicalize(records)
    backwards = canonicalize(list(reversed(records)))

    assert forwards == backwards
    assert forwards[0].representative_observation_id == "obs:a"


def test_every_point_carries_the_six_provenance_fields_of_step_six():
    point = canonicalize_slot(
        [
            make_record(observation_id="obs:a", document_id="doc-a", filing_date="2022-11-03"),
            make_record(observation_id="obs:b", document_id="doc-b", filing_date="2023-02-15"),
        ]
    )

    assert point.supporting_observation_ids == ("obs:a", "obs:b")
    assert point.minority_observation_ids == ()
    assert point.quarantined_observation_ids == ()
    assert point.n_docs == 2
    assert point.first_filed == "2022-11-03"
    assert point.last_filed == "2023-02-15"


def test_a_slot_with_no_observation_is_refused_at_construction():
    with pytest.raises(ValueError):
        canonicalize_slot([])


# ---------------------------------------------------------------------------------------
# The series
# ---------------------------------------------------------------------------------------


def test_a_series_orders_its_points_by_anchor_date_and_keeps_a_conflicted_slot_as_a_member():
    conflicted = [
        make_record(observation_id="obs:one", period=story_period("2022-01-01", "2022-03-31"),
                    value=100.0, unit="percent", scale="units", currency=None,
                    document_id="doc-1"),
        make_record(observation_id="obs:two", period=story_period("2022-01-01", "2022-03-31"),
                    value=900.0, unit="percent", scale="units", currency=None,
                    document_id="doc-2"),
    ]
    clean = [
        make_record(observation_id="obs:q2", period=story_period("2022-04-01", "2022-06-30"),
                    value=200.0, unit="percent", scale="units", currency=None),
        make_record(observation_id="obs:q3", period=story_period("2022-07-01", "2022-09-30"),
                    value=300.0, unit="percent", scale="units", currency=None),
    ]

    series = build_series(canonicalize(conflicted + clean))["adjusted_ebitda"]

    assert [point.period.key for point in series.points] == ["2022Q1", "2022Q2", "2022Q3"]
    assert [point.period.key for point in series.valued_points()] == ["2022Q2", "2022Q3"]
    assert series.point("2022Q1").status is CanonicalStatus.CONFLICT


def test_a_fiscal_year_does_not_sit_between_two_quarters_of_that_year():
    """`points_between` is same-shape, or every quarter step would look non-adjacent."""
    records = [
        make_record(observation_id="obs:q2", period=story_period("2022-04-01", "2022-06-30"),
                    value=1.0, unit="percent", scale="units", currency=None),
        make_record(observation_id="obs:q3", period=story_period("2022-07-01", "2022-09-30"),
                    value=2.0, unit="percent", scale="units", currency=None),
        make_record(observation_id="obs:fy", period=story_period("2022-01-01", "2022-12-31"),
                    value=3.0, unit="percent", scale="units", currency=None),
    ]
    series = build_series(canonicalize(records))["adjusted_ebitda"]

    assert series.points_between(series.point("2022Q2"), series.point("2022Q3")) == ()


# ---------------------------------------------------------------------------------------
# Loading through §9's tools, including past the 200-row bound
# ---------------------------------------------------------------------------------------


@dataclass
class ScriptedRetriever:
    """A `story.contracts.GraphRetriever` with a page script where the database would be.

    Structural conformance, no import of the retrieval stage — `story.stages.detection` may not
    import `story.stages.retrieval` (the package-structure suite enforces it), which is exactly
    why the loader is written against the protocol and is drivable by this.
    """

    pages: Sequence[RetrievalResult] = ()
    evidence: Mapping[str, RetrievalResult] = field(default_factory=dict)
    calls: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)
    _index: int = 0

    def call(self, tool: str, parameters: Mapping[str, Any]) -> RetrievalResult:
        self.calls.append((tool, dict(parameters)))
        if tool == "get_fact_evidence":
            return self.evidence.get(
                str(parameters.get("observation_id")),
                RetrievalResult(outcome=RetrievalOutcome.OK),
            )
        page = self.pages[min(self._index, len(self.pages) - 1)]
        self._index += 1
        return page

    def trace(self) -> Sequence[RetrievalTraceEntry]:
        return ()


def history_row(index: int, anchor: str) -> dict[str, Any]:
    return {
        "observation_id": f"obs:{anchor}:{index}",
        "metric_id": "gaap_gross_margin",
        "subject_entity_id": "opendoor",
        "period_start": None,
        "period_end": anchor,
        "instant_date": None,
        "value": float(index),
        "unit": "percent",
        "scale": "units",
        "currency": None,
        "source_lane": "normalized_table",
        "validation_state": "clean",
        "document_id": f"doc-{index % 3}",
        "passage_id": f"doc-{index % 3}#p{index}",
    }


def test_the_loader_pages_past_a_truncated_history_and_returns_every_row():
    """Five metrics in the run exceed `get_metric_history`'s 200-row bound; a loader that took
    the first page would build its census from 2,238 observations and report nothing wrong."""
    first = RetrievalResult(
        outcome=RetrievalOutcome.OK,
        rows=tuple(history_row(index, "2022-03-31") for index in range(3))
        + tuple(history_row(index, "2022-06-30") for index in range(3)),
        truncated=True,
    )
    second = RetrievalResult(
        outcome=RetrievalOutcome.OK,
        rows=tuple(history_row(index, "2022-06-30") for index in range(3))
        + tuple(history_row(index, "2022-09-30") for index in range(2)),
        truncated=False,
    )
    retriever = ScriptedRetriever(pages=(first, second))

    load = load_observations(
        retriever, metric_ids=["gaap_gross_margin"], with_evidence=False)

    assert len(load.records) == 8
    assert load.unreadable == ()
    assert [parameters.get("since") for _tool, parameters in retriever.calls] == [
        None, "2022-06-30"]


def test_the_loader_refuses_when_one_anchor_date_holds_more_rows_than_the_bound():
    """Keyset paging on a date cannot advance past a date that fills a whole page. The loader
    says so; it does not return a silently short series."""
    stuck = RetrievalResult(
        outcome=RetrievalOutcome.OK,
        rows=tuple(history_row(index, "2022-06-30") for index in range(3)),
        truncated=True,
    )
    retriever = ScriptedRetriever(pages=(stuck,))

    load = load_observations(
        retriever, metric_ids=["gaap_gross_margin"], with_evidence=False)

    assert load.records == ()
    assert load.unreadable[0][0] == "gaap_gross_margin"
    assert "paging stalled" in load.unreadable[0][1]


def test_a_metric_the_retriever_refuses_is_reported_rather_than_dropped_silently():
    retriever = ScriptedRetriever(
        pages=(RetrievalResult(outcome=RetrievalOutcome.REFUSED,
                               reason="unknown_period_shape: nope"),)
    )

    load = load_observations(retriever, metric_ids=["gaap_gross_margin"], with_evidence=False)

    assert load.records == ()
    assert load.unreadable == (("gaap_gross_margin", "unknown_period_shape: nope"),)


def test_the_loader_reads_the_quote_and_the_filing_date_from_the_evidence_tool():
    """C3: `quoted_text` is on the `EVIDENCED_BY` edge and `filing_date` on the `:Document`, so
    neither reaches the canonicaliser through `get_metric_history`."""
    row = history_row(0, "2022-06-30")
    retriever = ScriptedRetriever(
        pages=(RetrievalResult(outcome=RetrievalOutcome.OK, rows=(row,)),),
        evidence={
            row["observation_id"]: RetrievalResult(
                outcome=RetrievalOutcome.OK,
                rows=({"quoted_text": "11.6%", "filing_date": "2022-08-04"},),
            )
        },
    )

    load = load_observations(retriever, metric_ids=["gaap_gross_margin"])

    assert load.records[0].quoted_text == "11.6%"
    assert load.records[0].filing_date == "2022-08-04"


# ---------------------------------------------------------------------------------------
# Live — §6.1's census and §6.2's series, recomputed from the loaded graph
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_points():  # type: ignore[no-untyped-def]
    """Every observation in the run, canonicalised. Skipped when the container is not up.

    `verify_connectivity` first, matching S1's own live fixture: F12 measured that the handshake
    fails in 0.0s against a refused port while `execute_query` retries for 35 seconds.
    """
    from story.context import build_story_context
    from story.stages.retrieval.graph_tools import BoundedGraphRetriever

    context = build_story_context()
    health = context.executor.verify_connectivity()
    if not health.ok:
        context.close()
        pytest.skip(f"neo4j unavailable: {health.status} — {health.detail}")
    try:
        load = load_observations(BoundedGraphRetriever(context.executor))
        yield load, canonicalize(load.records)
    finally:
        context.close()


@pytest.mark.neo4j
def test_live_the_run_canonicalises_to_five_hundred_and_thirty_seven_slots(live_points):  # type: ignore[no-untyped-def]
    """§6.1's census, recomputed. 537 slots over 2,704 observations, 36 multi-valued, and
    tolerance collapses every one of the 36 to a single reading."""
    load, points = live_points
    measured = census(points)

    assert len(load.records) == 2704
    assert load.unreadable == ()
    assert measured.observations == 2704
    assert measured.slots == 537
    assert measured.multi_valued == 36
    assert measured.multi_cluster == 0
    assert (measured.ok, measured.resolved_by_majority, measured.conflict) == (537, 0, 0)


@pytest.mark.neo4j
def test_live_the_quarantine_guard_fires_on_nothing_in_this_run(live_points):  # type: ignore[no-untyped-def]
    """F0's `PERIOD_NOT_GROUNDED_IN_PASSAGE` already removed the three rows it caught in the
    2026-08-02 snapshot, so the rule is a guard. The synthetic tests above are what hold it."""
    _load, points = live_points

    assert census(points).quarantined == 0
    assert [point for point in points if LANE_DEFECT in point.warnings] == []


@pytest.mark.neo4j
def test_live_every_multi_valued_slot_is_a_thousands_versus_millions_printing(live_points):  # type: ignore[no-untyped-def]
    _load, points = live_points
    multi = [point for point in points if point.distinct_values > 1]

    assert len(multi) == 36
    assert all(point.status is CanonicalStatus.OK for point in multi)
    assert all(len(point.clusters) == 1 for point in multi)
    assert all(point.scale == "thousands" for point in multi)


@pytest.mark.neo4j
def test_live_five_metrics_exceed_the_history_row_bound_and_paging_recovers_them(live_points):  # type: ignore[no-untyped-def]
    """Measured on this run: the five below each return `truncated=True` on one call."""
    load, _points = live_points
    per_metric = {}
    for record in load.records:
        per_metric[record.metric_id] = per_metric.get(record.metric_id, 0) + 1

    assert sorted(metric for metric, count in per_metric.items() if count > 200) == [
        "adjusted_ebitda",
        "adjusted_ebitda_margin",
        "contribution_margin",
        "contribution_profit",
        "gaap_gross_margin",
    ]


@pytest.mark.neo4j
def test_live_the_shape_classifier_names_every_period_in_the_run(live_points):  # type: ignore[no-untyped-def]
    """74 distinct periods, and only the three cross-year windows are `other` *(measured)*."""
    load, _points = live_points
    periods = {record.period.key: record.period for record in load.records}
    by_shape: dict[str, int] = {}
    for period in periods.values():
        by_shape[period.shape.value] = by_shape.get(period.shape.value, 0) + 1

    assert len(periods) == 74
    assert by_shape == {
        "fiscal_year": 8, "quarter": 26, "ytd_6m": 6, "ytd_9m": 6, "instant": 25, "other": 3}
    assert sorted(
        key for key, period in periods.items() if period.shape is PeriodShape.OTHER
    ) == ["2022-04-01_2022-12-31", "2022-07-01_2023-03-31", "2022-10-01_2023-06-30"]
    assert sum(
        1 for record in load.records if record.period.shape is PeriodShape.OTHER
    ) == 6


#: §6.2's table, transcribed from the plan so the live test compares against the *plan* rather
#: than against itself. `adjusted_ebitda` is in millions of USD as the plan prints it; the three
#: margins are percentages. `None` is the one gap the plan records — `adjusted_ebitda 2019Q4`.
QUARTERS = (
    "2019Q4", "2020Q1", "2020Q2", "2020Q3", "2020Q4", "2021Q1", "2021Q2", "2021Q3", "2021Q4",
    "2022Q1", "2022Q2", "2022Q3", "2022Q4", "2023Q1", "2023Q2", "2023Q3", "2023Q4", "2024Q1",
    "2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1",
)
PLAN_SERIES = {
    "adjusted_ebitda": (None, -28, -22, -21, -27, -2, 26, 35, 0, 176, 218, -211, -351, -341,
                        -168, -49, -69, -50, -5, -38, -49, -30, 23, -33, -43, -31),
    "gaap_gross_margin": (5.9, 7.3, 7.4, 10.6, 15.4, 13.0, 13.4, 8.9, 7.1, 10.4, 11.6, -12.6,
                          2.5, 5.4, 7.5, 9.8, 8.3, 9.7, 8.5, 7.6, 7.8, 8.6, 8.2, 7.2, 7.7, 10.0),
    "adjusted_gross_margin": (5.6, 7.1, 6.9, 9.8, 15.4, 13.0, 13.5, 10.3, 7.3, 9.9, 13.2, 3.3,
                              -3.2, -3.3, 0.4, 8.6, 7.6, 8.8, 10.2, 7.2, 6.9, 8.7, 8.6, 7.0,
                              6.1, 9.3),
    "contribution_margin": (1.5, 3.1, 2.7, 5.9, 12.6, 10.2, 10.8, 7.5, 4.0, 6.4, 10.1, -0.7,
                            -7.2, -7.7, -4.6, 4.4, 3.4, 4.8, 6.3, 3.8, 3.5, 4.7, 4.4, 2.2, 1.0,
                            4.4),
}


@pytest.mark.neo4j
@pytest.mark.parametrize("metric_id", sorted(PLAN_SERIES))
def test_live_the_quarterly_series_of_section_six_two_reproduces_from_the_graph(
    live_points, metric_id  # type: ignore[no-untyped-def]
):
    """The acceptance condition of S2: the table is *recomputed*, not copied.

    `adjusted_ebitda` is compared at the plan's own printed precision — whole millions — because
    that is the precision the table states; the margins are compared exactly.
    """
    _load, points = live_points
    series = build_series(points)[metric_id]

    measured = []
    for quarter in QUARTERS:
        point = series.point(quarter)
        if point is None or not point.has_value:
            measured.append(None)
        elif metric_id == "adjusted_ebitda":
            measured.append(round(point.value / 1e6))
        else:
            measured.append(round(point.value, 1))

    assert tuple(measured) == PLAN_SERIES[metric_id]


@pytest.mark.neo4j
def test_live_the_three_margin_series_are_gapless_and_adjusted_ebitda_misses_one_quarter(
    live_points,  # type: ignore[no-untyped-def]
):
    _load, points = live_points
    series = build_series(points)

    quarters = {
        metric_id: {
            point.period.key
            for point in series[metric_id].valued_points(PeriodShape.QUARTER)
        }
        for metric_id in PLAN_SERIES
    }

    assert quarters["adjusted_ebitda"] & set(QUARTERS) == set(QUARTERS) - {"2019Q4"}
    for metric_id in ("gaap_gross_margin", "adjusted_gross_margin", "contribution_margin"):
        assert set(QUARTERS) <= quarters[metric_id]


@pytest.mark.neo4j
def test_live_the_spike_quarters_carry_the_values_the_plan_names(live_points):  # type: ignore[no-untyped-def]
    """§6.3's F1, F2 and F3, straight off the canonical points."""
    _load, points = live_points
    series = build_series(points)

    assert series["adjusted_ebitda"].point("2022Q2").value == 218_000_000.0
    assert series["adjusted_ebitda"].point("2022Q3").value == -211_000_000.0
    assert series["gaap_gross_margin"].point("2022Q2").value == 11.6
    assert series["gaap_gross_margin"].point("2022Q3").value == -12.6
    assert series["adjusted_gross_margin"].point("2022Q2").value == 13.2
    assert series["adjusted_gross_margin"].point("2022Q3").value == 3.3


@pytest.mark.neo4j
def test_live_the_sparse_series_cover_what_the_plan_says_they_cover(live_points):  # type: ignore[no-untyped-def]
    """*"`homes_purchased` covers 15 quarters from 2022Q3; `housing_inventory_homes` covers 23
    instants; `pct_homes_on_market_gt_120_days` covers 15."*"""
    _load, points = live_points
    series = build_series(points)

    purchased = series["homes_purchased"].valued_points(PeriodShape.QUARTER)
    assert len(purchased) == 15
    assert purchased[0].period.key == "2022Q3"
    assert len(series["housing_inventory_homes"].valued_points(PeriodShape.INSTANT)) == 23
    assert len(
        series["pct_homes_on_market_gt_120_days"].valued_points(PeriodShape.INSTANT)) == 15
