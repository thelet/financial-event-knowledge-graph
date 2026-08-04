"""S3 — §6.6 D3's `acceleration`, the runs it must refuse, and its census on the live run.

Offline by default. The `neo4j`-marked tests at the bottom scan the whole canonical layer and
pin the census the plan states — 8 firings across 6 metrics over quarters — together with the
two instant firings the plan's figure does not count.

Every synthetic series here is built from `ObservationRecord`s and put through the real
`canonicalize`, not from hand-made `CanonicalPoint`s. A detector that was only ever driven over
points a test wrote by hand would be a detector nobody had run against §6.1's output.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import pytest

from story.core.keys import DIGEST_CHARS
from story.core.models import (
    EvidenceRequest,
    RetrievalOutcome,
    RetrievalResult,
    RetrievalTraceEntry,
    StoryCandidate,
)
from story.core.periods import PeriodShape, story_period
from story.core.series import (
    PERCENT_TOLERANCE,
    CanonicalStatus,
    ClaimKind,
    ObservationRecord,
    Refuse,
    build_series,
    comparable,
)
from story.stages.detection.acceleration import (
    COMPARABLE_SHAPES,
    DETECTOR_ID,
    DETECTOR_VERSION,
    MAGNITUDE_RATIO,
    SERIES_INCOMPLETE,
    STORY_TYPE,
    accelerates,
    delta_sign,
    detect_acceleration,
    detect_acceleration_from_load,
)
from story.stages.detection.canonicalization import (
    FILING_DATE_UNKNOWN,
    MINORITY_READING_PRESENT,
    POLICY_VERSION,
    canonicalize,
    load_observations,
)

GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"

#: The three margins are percentages, so a delta must clear R8's 0.1 to be a movement at all.
#: Every synthetic percentage series below moves in whole points for that reason.
MARGIN = "gaap_gross_margin"
INVENTORY = "housing_inventory_homes"


def quarter_record(
    metric_id: str, year: int, quarter: int, value: float, **overrides: Any
) -> ObservationRecord:
    """One table-lane observation of a calendar quarter, canonicalisable as it stands."""
    start_month = 3 * (quarter - 1) + 1
    end_month, end_day = start_month + 2, (31, 30, 30, 31)[quarter - 1]
    fields: dict[str, Any] = dict(
        observation_id=f"obs:{metric_id}:{year}Q{quarter}",
        metric_id=metric_id,
        subject_entity_id="opendoor",
        period=story_period(
            f"{year:04d}-{start_month:02d}-01", f"{year:04d}-{end_month:02d}-{end_day:02d}", None
        ),
        value=value,
        unit="percent",
        scale="units",
        currency=None,
        source_lane="normalized_table",
        validation_state="clean",
        document_id=f"doc:{year}Q{quarter}",
        passage_id=f"doc:{year}Q{quarter}#p1",
        filing_date=f"{year:04d}-{end_month:02d}-28",
        quoted_text=f"{value}%",
    )
    fields.update(overrides)
    return ObservationRecord(**fields)


def quarterly(
    values: Sequence[float | None], *, metric_id: str = MARGIN, first: tuple[int, int] = (2020, 1)
) -> list[ObservationRecord]:
    """Consecutive quarters from `first`. A `None` is a quarter the run simply does not hold."""
    year, quarter = first
    records = []
    for value in values:
        if value is not None:
            records.append(quarter_record(metric_id, year, quarter, value))
        year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    return records


def instants(
    values: Sequence[float], *, first: tuple[int, int] = (2021, 1)
) -> list[ObservationRecord]:
    """Consecutive quarter-end instants of `housing_inventory_homes`, a real instant series."""
    year, quarter = first
    records = []
    for value in values:
        end_month, end_day = 3 * quarter, (31, 30, 30, 31)[quarter - 1]
        records.append(
            ObservationRecord(
                observation_id=f"obs:{INVENTORY}:{year}-{end_month:02d}",
                metric_id=INVENTORY,
                subject_entity_id="opendoor",
                period=story_period(None, None, f"{year:04d}-{end_month:02d}-{end_day:02d}"),
                value=value,
                unit="homes",
                scale="units",
                source_lane="normalized_table",
                validation_state="clean",
                document_id=f"doc:{year}-{end_month:02d}",
                filing_date=f"{year:04d}-{end_month:02d}-28",
                quoted_text=f"{value:,.0f} homes",
            )
        )
        year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    return records


def detect(records: Sequence[ObservationRecord], **kwargs: Any):  # type: ignore[no-untyped-def]
    return detect_acceleration(canonicalize(records), graph_run_id=GRAPH_RUN_ID, **kwargs)


# ---------------------------------------------------------------------------------------
# The rule itself — §6.6 D3, and the four shapes of run it must not fire on
# ---------------------------------------------------------------------------------------


def test_three_same_sign_deltas_of_growing_magnitude_are_one_acceleration_candidate():
    """1.0 → 2.0 → 5.0 → 11.0: deltas +1, +3, +6. Monotone, same sign, and 6 ≥ 1.5 × 1."""
    result = detect(quarterly([1.0, 2.0, 5.0, 11.0]))

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.story_type == STORY_TYPE
    assert candidate.detector_id == DETECTOR_ID
    assert candidate.metric_ids == (MARGIN,)
    assert candidate.anchor_period_keys == ("2020Q1", "2020Q2", "2020Q3", "2020Q4")
    assert candidate.signals["delta_1"] == 1.0
    assert candidate.signals["delta_2"] == 3.0
    assert candidate.signals["delta_3"] == 6.0
    assert candidate.signals["delta_sign"] == 1
    assert candidate.signals["window_delta"] == 10.0


def test_a_falling_series_accelerates_downward_and_is_the_same_candidate_shape():
    """Same sign, not same direction: three deltas of −1, −3, −6 accelerate exactly as +1, +3,
    +6 do. The candidate says so with `delta_sign`, and never with a word like *"decline"* —
    §6.6 D1 records that polarity is story-owned config and not a detector's to infer."""
    result = detect(quarterly([11.0, 10.0, 7.0, 1.0]))

    assert len(result.candidates) == 1
    assert result.candidates[0].signals["delta_sign"] == -1
    assert result.candidates[0].signals["window_delta"] == -10.0


def test_decreasing_delta_magnitude_does_not_fire():
    """Non-negotiable 1. Deltas +6, +3, +1: same sign throughout, and nothing accelerating."""
    assert detect(quarterly([1.0, 7.0, 10.0, 11.0])).candidates == ()
    assert not accelerates([6.0, 3.0, 1.0])


def test_a_run_that_grows_by_less_than_the_ratio_does_not_fire():
    """Deltas +2, +2.5, +2.9 are monotone and same-signed; 2.9 < 1.5 × 2, so the last step is
    not half again the first and §6.6 D3's magnitude gate refuses it."""
    assert detect(quarterly([1.0, 3.0, 5.5, 8.4])).candidates == ()
    assert not accelerates([2.0, 2.5, 2.9])
    assert accelerates([2.0, 2.5, 2.0 * MAGNITUDE_RATIO])


def test_a_run_whose_signs_alternate_does_not_fire():
    """Deltas +1, −3, +6. That is a reversal, which is D2's candidate and not this one's."""
    assert detect(quarterly([1.0, 2.0, -1.0, 5.0])).candidates == ()


def test_no_deceleration_candidate_is_ever_emitted():
    """Non-negotiable 4, and the correction to §6.6 D3 that motivates it.

    The plan's first draft justified a lineage dedup with *"the 2023Q1 decelerations of AGP, CP,
    CM and AGM are the same event four times"*. A deceleration is a run of same-sign deltas of
    **shrinking** magnitude, and the monotone-increasing condition cannot fire on one — so those
    candidates describe something this detector is structurally unable to produce. Deceleration
    needs its own rule and its own threshold and is out of V1 scope.
    """
    decelerating = [
        [-47.0, -25.0, -10.0],
        [47.0, 25.0, 10.0],
        [-10.0, -10.0, -10.0],
        [3.0, 2.0, 1.0],
    ]
    for deltas in decelerating:
        assert not accelerates(deltas)

    # The 2023Q1 shape the plan names: a large move, then two smaller ones of the same sign.
    assert detect(quarterly([20.0, -27.0, -52.0, -62.0])).candidates == ()
    assert detect(quarterly([1.0, 48.0, 73.0, 83.0])).candidates == ()


def test_a_zero_delta_breaks_the_run_because_it_has_no_sign():
    """Non-negotiable 3. Deltas +1, 0, +3, +6 — the last three would accelerate on their own,
    and the flat quarter is not a step in either direction, so no window survives it."""
    assert delta_sign(0.0) is None
    assert delta_sign(-0.0) is None
    assert accelerates([1.0, 3.0, 6.0])

    assert detect(quarterly([10.0, 11.0, 11.0, 14.0, 20.0])).candidates == ()
    assert not accelerates([1.0, 0.0, 3.0])
    assert not accelerates([0.0, 3.0, 6.0])


def test_the_delta_comparison_survives_a_float_residue():
    """`13.2 − 3.3` is `9.899999999999999` and `adjusted_gross_margin` holds those two values in
    adjacent quarters. Rounded first, the pair of magnitudes below is a plateau and refuses; on
    raw subtraction it would read as strictly increasing and fire."""
    assert (13.2 - 3.3) != 9.9
    assert not accelerates([9.9, 13.2 - 3.3, 20.0])


# ---------------------------------------------------------------------------------------
# R10 — a window is consecutive because `comparable` says so, not because a list says so
# ---------------------------------------------------------------------------------------


def test_a_missing_quarter_does_not_form_a_sequence():
    """Non-negotiable 2. 2020Q3 is absent, so 2020Q2 and 2020Q4 are six months apart and R10
    refuses the step with `SERIES_GAP` — the rule that stops `pct_>120d` reading a +47 pp
    quarterly jump across a four-quarter hole."""
    records = quarterly([1.0, 2.0, None, 5.0, 11.0])
    points = canonicalize(records)

    assert detect(records).candidates == ()

    series = build_series(points)[MARGIN]
    across = series.valued_points(PeriodShape.QUARTER)
    verdict = comparable(
        across[2], across[1], claim=ClaimKind.ACCELERATION, series=series
    )
    assert isinstance(verdict, Refuse)
    assert (verdict.rule, verdict.reason) == ("R10", "SERIES_GAP")


def test_a_conflicted_slot_inside_the_window_is_a_hole_too():
    """A `CONFLICT` slot emits no value, so it is not in `valued_points` and the two points
    either side become list-adjacent. They are still two calendar steps apart, and R10 measures
    months rather than list positions — which is why the scan needs no hole-check of its own.

    The conflict is built the way §6.1 step 4 produces one: two readings, one document each, so
    neither has the document majority the rule requires.
    """
    records = quarterly([1.0, 2.0, 5.0, 11.0, 23.0])
    conflicted = quarter_record(MARGIN, 2020, 3, 99.0, observation_id="obs:rival",
                                document_id="doc:rival")
    points = canonicalize([*records, conflicted])

    assert [p.status for p in points if p.period.key == "2020Q3"] == [CanonicalStatus.CONFLICT]
    assert detect_acceleration(points, graph_run_id=GRAPH_RUN_ID).candidates == ()


def test_a_point_lying_between_two_others_is_refused_as_not_adjacent():
    """R10's other half, recorded rather than assumed.

    The scan builds its windows from consecutive entries of `valued_points`, so it can never
    hand `comparable` a pair with a canonical point between them — `SERIES_NOT_ADJACENT` is
    unreachable *from this detector*. It is asserted here anyway, because "unreachable" is a
    claim about the window construction, and the day that construction changes this is the rule
    that has to catch it.
    """
    series = build_series(canonicalize(quarterly([1.0, 2.0, 5.0, 11.0])))[MARGIN]
    points = series.valued_points(PeriodShape.QUARTER)

    verdict = comparable(points[2], points[0], claim=ClaimKind.ACCELERATION, series=series)

    assert isinstance(verdict, Refuse)
    assert (verdict.rule, verdict.reason) == ("R10", "SERIES_NOT_ADJACENT")


def test_a_step_inside_presentation_tolerance_breaks_the_run():
    """R8, reached through the same call. A percentage printed to one decimal carries ±0.1, so a
    move of 0.05 is the two filings rounding differently and a run built on one is a run built on
    a rounding. Without R8 this window fires: 0.05, 3.95, 6.0 is monotone and same-signed."""
    assert accelerates([0.05, 3.95, 6.0])
    assert detect(quarterly([1.0, 1.05, 5.0, 11.0])).candidates == ()


def test_a_one_unit_step_survives_r8_on_a_float_residue_and_a_live_firing_rests_on_it():
    """A finding, recorded rather than worked around, because it decides a census number.

    These are `gaap_gross_margin`'s real 2020 values, and their window is one of the eight
    quarterly firings §6.6 D3 counts. Its first step is `7.3 → 7.4` — exactly one printed unit,
    which R8's `|A − B| <= tol` is written to refuse. It does not refuse it: IEEE 754 makes the
    subtraction `0.10000000000000053`, a hair above `PERCENT_TOLERANCE`. Had S2 rounded before
    comparing, this window would refuse and the live quarterly census would be **seven**
    firings, not eight. S2's rule is not this module's to change; the dependency is asserted
    here so nobody has to rediscover it from a census that moved by one.
    """
    assert (7.4 - 7.3) > PERCENT_TOLERANCE
    assert round(7.4 - 7.3, 9) == PERCENT_TOLERANCE

    result = detect(quarterly([7.3, 7.4, 10.6, 15.4]))

    assert [c.anchor_period_keys for c in result.candidates] == [
        ("2020Q1", "2020Q2", "2020Q3", "2020Q4")]


def test_the_two_shapes_of_one_metric_are_scanned_separately():
    """A quarter and a fiscal year are not neighbours (R3), so a series holding both yields
    windows within each shape and never across them."""
    fiscal = [
        quarter_record(MARGIN, 2020, 1, 100.0, observation_id="obs:fy2020",
                       period=story_period("2020-01-01", "2020-12-31", None),
                       document_id="doc:fy2020"),
    ]
    result = detect([*quarterly([1.0, 2.0, 5.0, 11.0]), *fiscal])

    assert len(result.candidates) == 1
    assert result.candidates[0].anchor_period_keys == ("2020Q1", "2020Q2", "2020Q3", "2020Q4")


def test_an_instant_series_accelerates_on_the_same_rule():
    """`housing_inventory_homes` is a series of instants, and §6.2 counts 23 of them. R10 gives
    an instant the same three-month step a quarter has, so the rule reads the same."""
    result = detect_acceleration(
        canonicalize(instants([1000.0, 1100.0, 1500.0, 2500.0])), graph_run_id=GRAPH_RUN_ID
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].anchor_period_keys == (
        "2021-03-31", "2021-06-30", "2021-09-30", "2021-12-31")
    assert result.candidates[0].metric_ids == (INVENTORY,)


def test_only_the_shapes_r3_admits_are_scanned():
    """`PeriodShape.OTHER` is not in the scanned set: the run's three cross-year windows are
    comparable with nothing, and building a window out of them would only produce refusals."""
    assert PeriodShape.OTHER not in COMPARABLE_SHAPES
    assert set(COMPARABLE_SHAPES) == set(PeriodShape) - {PeriodShape.OTHER}


# ---------------------------------------------------------------------------------------
# What the candidate carries — §6.4, §6.11, §10
# ---------------------------------------------------------------------------------------


def test_candidate_ids_are_stable_under_input_reordering():
    """Non-negotiable 6. §6.11 sorts the digest inputs so the order a detector visited its
    observations in cannot change a candidate's identity; §6.1 makes the points themselves
    order-independent. Asserted over the whole candidate, not only the id."""
    records = quarterly([1.0, 2.0, 5.0, 11.0])
    forwards = detect(records).candidates
    backwards = detect(list(reversed(records))).candidates
    shuffled = list(records)
    random.Random(17).shuffle(shuffled)

    assert forwards == backwards == detect(shuffled).candidates
    assert forwards[0].candidate_id.startswith(
        "cand:acceleration:gaap-gross-margin:opendoor:2020Q1_2020Q2_2020Q3_2020Q4:")
    assert len(forwards[0].candidate_id.rsplit(":", 1)[-1]) == DIGEST_CHARS


def test_the_candidate_carries_no_prose_no_headline_and_no_score():
    """Non-negotiable 7. §6.4: the thesis is §11's output and the score is §6.10's, and a
    candidate that carried either would let the detector write the post or rank itself.

    Asserted as the field set rather than as four `hasattr` checks — `extra="forbid"` stops a
    field being added by accident, and this stops one being added on purpose.
    """
    candidate = detect(quarterly([1.0, 2.0, 5.0, 11.0])).candidates[0]

    assert set(candidate.model_dump()) == {
        "candidate_id", "detector_id", "detector_version", "policy_version", "graph_run_id",
        "subject_entity_id", "story_type", "metric_ids", "event_ids", "anchor_period_keys",
        "anchor_observation_ids", "signals", "warnings", "evidence_request", "audience",
    }
    assert all(
        isinstance(value, (bool, int, float)) for value in candidate.signals.values()
    ), candidate.signals


def test_the_candidate_carries_an_evidence_request_naming_its_four_periods():
    """Non-negotiable 8. §10 fetches what the request names and nothing the detector chose;
    `want_explanatory_search` stays off because this detector asks no fulltext question."""
    candidate = detect(quarterly([1.0, 2.0, 5.0, 11.0])).candidates[0]

    assert candidate.evidence_request == EvidenceRequest(
        metric_ids=(MARGIN,),
        period_keys=("2020Q1", "2020Q2", "2020Q3", "2020Q4"),
        observation_ids=candidate.anchor_observation_ids,
    )
    assert candidate.evidence_request.want_counter_evidence is True
    assert candidate.evidence_request.want_explanatory_search is False
    assert len(candidate.anchor_observation_ids) == 4


def test_the_candidate_propagates_the_warnings_the_canonical_points_carry():
    """Non-negotiable 8. §6.1 puts `filing_date_unknown` on a slot whose earliest filing could
    not be established; §10.1 has to disclose it, so it cannot stop at the point."""
    records = quarterly([1.0, 2.0, 5.0, 11.0])
    records[2] = quarter_record(MARGIN, 2020, 3, 5.0, filing_date=None)

    candidate = detect(records).candidates[0]

    assert candidate.warnings == (FILING_DATE_UNKNOWN,)


def test_the_anchor_observations_are_the_supporting_readings_and_not_the_minority_one():
    """§6.11's digest inputs are the observations that back the canonical value. A minority
    reading backs a number this run did not use; §10.1 discloses it separately."""
    records = quarterly([1.0, 2.0, 5.0, 11.0])
    corroboration = quarter_record(MARGIN, 2020, 3, 5.0, observation_id="obs:second-filing",
                                   document_id="doc:second-filing")
    rival = quarter_record(MARGIN, 2020, 3, 99.0, observation_id="obs:rival",
                           document_id="doc:rival")
    points = canonicalize([*records, corroboration, rival])
    candidate = detect_acceleration(points, graph_run_id=GRAPH_RUN_ID).candidates[0]

    # Two documents against one: §6.1 step 4's majority holds the slot at 5.0 and files 99.0 as
    # the minority reading, so the accelerating run is the canonical one.
    assert [p.status for p in points if p.period.key == "2020Q3"] == [
        CanonicalStatus.RESOLVED_BY_MAJORITY]
    assert "obs:rival" not in candidate.anchor_observation_ids
    assert {"obs:gaap_gross_margin:2020Q3", "obs:second-filing"} <= set(
        candidate.anchor_observation_ids)
    assert MINORITY_READING_PRESENT in candidate.warnings


def test_the_candidate_names_the_policy_and_detector_versions_it_was_minted_under():
    """§6.11: `detector_version` and `policy_version` are inside the digest, so a threshold or
    canonicalisation change mints a new candidate rather than mutating one in place."""
    candidate = detect(quarterly([1.0, 2.0, 5.0, 11.0])).candidates[0]

    assert candidate.detector_version == DETECTOR_VERSION
    assert candidate.policy_version == POLICY_VERSION
    assert candidate.graph_run_id == GRAPH_RUN_ID
    assert isinstance(candidate, StoryCandidate)


# ---------------------------------------------------------------------------------------
# A series whose completeness the loader could not prove
# ---------------------------------------------------------------------------------------


@dataclass
class ScriptedRetriever:
    """A `story.contracts.GraphRetriever` with a per-metric page script.

    Keyed by metric so one metric can be readable while another stalls — the state this
    detector has to tell apart from "no acceleration here". No import of the retrieval stage:
    `story.stages.detection` may not import it, which is why the loader takes the protocol.
    """

    pages: Mapping[str, Sequence[RetrievalResult]] = field(default_factory=dict)
    served: dict[str, int] = field(default_factory=dict)

    def call(self, tool: str, parameters: Mapping[str, Any]) -> RetrievalResult:
        if tool == "get_fact_evidence":
            return RetrievalResult(outcome=RetrievalOutcome.OK)
        metric_id = str(parameters.get("metric_id"))
        script = self.pages.get(metric_id, ())
        index = min(self.served.get(metric_id, 0), len(script) - 1)
        self.served[metric_id] = index + 1
        return script[index]

    def trace(self) -> Sequence[RetrievalTraceEntry]:
        return ()


def history_rows(records: Sequence[ObservationRecord]) -> tuple[dict[str, Any], ...]:
    """`get_metric_history`'s row shape for records this file already builds."""
    return tuple(
        {
            "observation_id": record.observation_id,
            "metric_id": record.metric_id,
            "subject_entity_id": record.subject_entity_id,
            "period_start": record.period.period_start,
            "period_end": record.period.period_end,
            "instant_date": record.period.instant_date,
            "value": record.value,
            "unit": record.unit,
            "scale": record.scale,
            "currency": record.currency,
            "source_lane": record.source_lane,
            "validation_state": record.validation_state,
            "document_id": record.document_id,
            "passage_id": record.passage_id,
        }
        for record in records
    )


def test_a_series_whose_completeness_cannot_be_proven_is_refused_and_not_scanned():
    """Non-negotiable 5, in its sharpest form: the points in hand *do* accelerate, and the
    detector still emits nothing for that metric.

    Five metrics exceed `get_metric_history`'s 200-row bound and §6.1 pages past it; when a
    single anchor date fills a page the loader refuses rather than returning a short series. A
    delta over a series of unknown extent is a delta across an unknown hole.
    """
    points = canonicalize(quarterly([1.0, 2.0, 5.0, 11.0]))
    stalled = [(MARGIN, "paging stalled at '2020-09-30': one anchor date holds more rows")]

    result = detect_acceleration(points, graph_run_id=GRAPH_RUN_ID, unreadable=stalled)

    assert result.candidates == ()
    assert result.refusals[0].metric_id == MARGIN
    assert result.refusals[0].reason == SERIES_INCOMPLETE
    assert "paging stalled" in result.refusals[0].detail


def test_the_loaders_own_refusal_reaches_the_detector_through_the_load():
    """The same state, end to end: one readable metric accelerates, one stalls, and the run
    reports one candidate and one named refusal rather than a quiet single result."""
    readable = quarterly([1.0, 2.0, 5.0, 11.0], metric_id="adjusted_ebitda_margin")
    stuck = quarterly([1.0, 2.0, 5.0, 11.0])
    retriever = ScriptedRetriever(
        pages={
            "adjusted_ebitda_margin": (
                RetrievalResult(outcome=RetrievalOutcome.OK, rows=history_rows(readable)),
            ),
            MARGIN: (
                RetrievalResult(
                    outcome=RetrievalOutcome.OK, rows=history_rows(stuck), truncated=True
                ),
            ),
        }
    )
    load = load_observations(
        retriever, metric_ids=["adjusted_ebitda_margin", MARGIN], with_evidence=False
    )

    result = detect_acceleration_from_load(load, graph_run_id=GRAPH_RUN_ID)

    assert [candidate.metric_ids for candidate in result.candidates] == [
        ("adjusted_ebitda_margin",)]
    assert [(r.metric_id, r.reason) for r in result.refusals] == [(MARGIN, SERIES_INCOMPLETE)]


# ---------------------------------------------------------------------------------------
# Live — the census of §6.6 D3, recomputed from the loaded graph
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_scan():  # type: ignore[no-untyped-def]
    """Every observation in the run, canonicalised and scanned. Skipped with no container.

    `verify_connectivity` first, matching S1's and S2's live fixtures: the handshake fails in
    0.0 s against a refused port while `execute_query` retries for 35 seconds.
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
    finally:
        context.close()
    yield load, canonicalize(load.records)


#: §6.6 D3's *"8 firings across 6 metrics"*, named. Transcribed as the windows they fire on so
#: the live test compares against a statement rather than against its own output.
PLAN_QUARTERLY_FIRINGS = (
    ("adjusted_ebitda_margin", ("2020Q1", "2020Q2", "2020Q3", "2020Q4")),
    ("adjusted_ebitda_margin", ("2022Q4", "2023Q1", "2023Q2", "2023Q3")),
    ("adjusted_gross_profit", ("2020Q3", "2020Q4", "2021Q1", "2021Q2")),
    ("contribution_profit", ("2020Q3", "2020Q4", "2021Q1", "2021Q2")),
    ("contribution_profit_after_interest", ("2020Q2", "2020Q3", "2020Q4", "2021Q1")),
    ("contribution_profit_after_interest", ("2020Q3", "2020Q4", "2021Q1", "2021Q2")),
    ("direct_selling_costs", ("2021Q1", "2021Q2", "2021Q3", "2021Q4")),
    ("gaap_gross_margin", ("2020Q1", "2020Q2", "2020Q3", "2020Q4")),
)

#: The two firings the plan's figure does not count, because §6.6 D3 was measured over quarterly
#: deltas (§6.10's population is *"298 consecutive-quarter deltas"*). They are the same rule over
#: an instant series, and `housing_inventory_homes` is one of the three §6.2 lists.
INSTANT_FIRINGS = (
    (INVENTORY, ("2020-12-31", "2021-03-31", "2021-06-30", "2021-09-30")),
    (INVENTORY, ("2022-06-30", "2022-09-30", "2022-12-31", "2023-03-31")),
)


@pytest.mark.neo4j
def test_live_the_quarterly_census_is_the_eight_firings_across_six_metrics_the_plan_states(
    live_scan,  # type: ignore[no-untyped-def]
):
    """§6.6 D3's own number, recomputed. Not copied: the windows are compared one by one."""
    _load, points = live_scan
    result = detect_acceleration(
        points, graph_run_id=GRAPH_RUN_ID, shapes=(PeriodShape.QUARTER,))

    fired = tuple(
        (candidate.metric_ids[0], candidate.anchor_period_keys)
        for candidate in result.candidates
    )
    assert fired == PLAN_QUARTERLY_FIRINGS
    assert len(fired) == 8
    assert len({metric_id for metric_id, _ in fired}) == 6


@pytest.mark.neo4j
def test_live_scanning_every_comparable_shape_adds_the_two_instant_firings(live_scan):  # type: ignore[no-untyped-def]
    """The census this module ships with: 10 firings across 7 metrics.

    The difference from the plan's 8 is entirely `housing_inventory_homes`, whose points are
    instants; the fiscal-year and year-to-date series fire on nothing. Recorded rather than
    tuned away — the rule is unchanged and the population is wider than the plan's sentence.
    """
    _load, points = live_scan
    result = detect_acceleration(points, graph_run_id=GRAPH_RUN_ID)

    fired = tuple(
        (candidate.metric_ids[0], candidate.anchor_period_keys)
        for candidate in result.candidates
    )
    assert len(fired) == 10
    assert len({metric_id for metric_id, _ in fired}) == 7
    assert sorted(fired) == sorted(PLAN_QUARTERLY_FIRINGS + INSTANT_FIRINGS)
    for shape in (PeriodShape.FISCAL_YEAR, PeriodShape.YTD_6M, PeriodShape.YTD_9M):
        assert detect_acceleration(
            points, graph_run_id=GRAPH_RUN_ID, shapes=(shape,)).candidates == ()


@pytest.mark.neo4j
def test_live_not_one_candidate_in_the_run_is_a_deceleration(live_scan):  # type: ignore[no-untyped-def]
    """Non-negotiable 4 on real data. Every firing's magnitudes strictly increase and its three
    deltas share one sign, which is what makes §6.6 D3's dedup example impossible."""
    _load, points = live_scan

    for candidate in detect_acceleration(points, graph_run_id=GRAPH_RUN_ID).candidates:
        deltas = [candidate.signals[f"delta_{n}"] for n in (1, 2, 3)]
        assert accelerates(deltas), candidate.candidate_id
        assert abs(deltas[2]) > abs(deltas[0])
        assert candidate.signals["magnitude_ratio"] >= MAGNITUDE_RATIO


@pytest.mark.neo4j
def test_live_no_metric_in_this_run_is_refused_as_incomplete(live_scan):  # type: ignore[no-untyped-def]
    """The refusal channel is empty here — §6.1's paging recovers all five over-bound metrics —
    and an empty channel is worth asserting, because a refusal that silently swallowed a metric
    would look exactly like a metric with no acceleration."""
    load, points = live_scan

    assert load.unreadable == ()
    assert detect_acceleration(points, graph_run_id=GRAPH_RUN_ID).refusals == ()


@pytest.mark.neo4j
def test_live_two_scans_of_one_run_mint_the_same_candidates(live_scan):  # type: ignore[no-untyped-def]
    """Determinism against the real corpus, not against a four-point fixture."""
    _load, points = live_scan

    first = detect_acceleration(points, graph_run_id=GRAPH_RUN_ID).candidates
    second = detect_acceleration(list(reversed(points)), graph_run_id=GRAPH_RUN_ID).candidates

    assert first == second
    assert len({candidate.candidate_id for candidate in first}) == len(first)
