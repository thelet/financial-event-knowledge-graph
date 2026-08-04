"""S3-A — §6.6 D2's run condition, its two gates, and everything it declines to scan.

The sign condition is driven through `reverses` against bare delta lists, because that is what
the rule is about and a series built around it would only obscure which delta decided. The gates
— D1's bar and `1.0 × σ(d)` — are driven through the scan, because both need a population and a
population is a property of a series.

**Zero is the case §6.6 D2 singles out** and it cannot be driven live: R8 refuses any step inside
presentation tolerance, and a step of exactly zero is inside every tolerance in this corpus, so a
zero delta never reaches this rule on real data. It is driven synthetically here, in both
positions it can occupy.

The `neo4j`-marked tests reproduce the live census, including the two 2022Q3 candidates §6.3
names and the one place the plan's own exclusion rule turns out to exclude nothing.
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest
from pydantic import ValidationError

from story.core.models import EvidenceRequest, StoryCandidate
from story.core.periods import PeriodShape, story_period
from story.core.series import CanonicalPoint, CanonicalStatus
from story.stages.detection.canonicalization import (
    MINORITY_READING_PRESENT,
    ObservationLoad,
    load_observations,
)
from story.stages.detection.detector_config import MIN_DELTA_POPULATION, THRESHOLD_UNMEASURED
from story.stages.detection.metric_move import SERIES_INCOMPLETE
from story.stages.detection.trend_reversal import (
    DETECTOR_ID,
    LOW_VARIANCE_PRESENTATION_NOISE,
    MIN_RUN,
    POPULATION_TOO_SMALL,
    STORY_TYPE,
    detect_trend_reversals,
    detect_trend_reversals_from_load,
    reverses,
)

GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"
MILLION = 1_000_000.0

_QUARTER_BOUNDS = {1: ("01-01", "03-31"), 2: ("04-01", "06-30"),
                   3: ("07-01", "09-30"), 4: ("10-01", "12-31")}


def quarter_period(year: int, index: int) -> Any:
    start, end = _QUARTER_BOUNDS[index]
    return story_period(f"{year}-{start}", f"{year}-{end}")


def quarterly_points(
    metric_id: str,
    values: Sequence[float | None],
    *,
    start_year: int = 2020,
    unit: str = "USD",
    scale: str | None = "millions",
    currency: str | None = "USD",
    **overrides: Any,
) -> tuple[CanonicalPoint, ...]:
    """One point per consecutive quarter from `start_year`Q1. `None` leaves a hole in the run."""
    points = []
    for offset, value in enumerate(values):
        if value is None:
            continue
        year, index = start_year + offset // 4, offset % 4 + 1
        period = quarter_period(year, index)
        points.append(
            CanonicalPoint(
                metric_id=metric_id,
                period=period,
                subject_entity_id="opendoor",
                status=CanonicalStatus.OK,
                value=value,
                unit=unit,
                scale=scale,
                currency=currency,
                representative_observation_id=f"obs:{metric_id}:{period.key}",
                supporting_observation_ids=(f"obs:{metric_id}:{period.key}",),
                n_docs=3,
                distinct_values=1,
                **overrides,
            )
        )
    return tuple(points)


def falling_then_rising(step: float, rise: float, *, length: int = 9) -> list[float]:
    """A series that falls by `step` every quarter and then rises by `rise` in the last one."""
    values = [0.0]
    for _ in range(length - 2):
        values.append(values[-1] - step)
    values.append(values[-1] + rise)
    return values


# ---------------------------------------------------------------------------------------
# The run condition — §6.6 D2's sign rule, including zero
# ---------------------------------------------------------------------------------------


def test_two_falls_answered_by_a_rise_is_a_reversal():
    assert reverses([-5.0, -3.0, 7.0]) is True


def test_two_rises_answered_by_a_fall_is_a_reversal():
    assert reverses([5.0, 3.0, -7.0]) is True


def test_a_run_that_continues_in_the_same_direction_is_not_a_reversal():
    assert reverses([-5.0, -3.0, -7.0]) is False


def test_a_prior_run_that_does_not_agree_with_itself_is_not_a_run():
    """§6.6 D2 requires **all** `min_run` prior deltas to oppose the reversing one."""
    assert reverses([5.0, -3.0, 7.0]) is False


def test_a_delta_of_exactly_zero_has_no_sign_and_cannot_reverse_anything():
    """§6.6 D2: *"a delta of exactly 0 has no sign and breaks a run rather than continuing or
    reversing it"*. Here in the reversing position."""
    assert reverses([-5.0, -3.0, 0.0]) is False


def test_a_delta_of_exactly_zero_inside_the_prior_run_breaks_it():
    """And here in the run position — a flat quarter is not a continuation of a trend."""
    assert reverses([-5.0, 0.0, 7.0]) is False
    assert reverses([0.0, -3.0, 7.0]) is False


def test_a_window_of_the_wrong_length_is_never_a_reversal():
    assert reverses([-5.0, 7.0]) is False
    assert reverses([-5.0, -3.0, -1.0, 7.0]) is False
    assert reverses([-5.0, -3.0, -1.0, 7.0], min_run=3) is True


def test_a_zero_delta_stops_a_reversal_the_series_would_otherwise_produce():
    """The rule through the scan rather than through the function, so the wiring is covered too.

    The two series differ in one quarter: the second repeats a value, making the middle delta
    exactly zero. Everything else — the magnitudes, the σ, the D1 bar — is unchanged.
    """
    moving = quarterly_points("adjusted_ebitda",
                              [v * MILLION for v in falling_then_rising(80.0, 400.0)])
    flat_middle = quarterly_points(
        "adjusted_ebitda",
        [v * MILLION for v in [0.0, -80.0, -160.0, -240.0, -320.0, -400.0, -480.0, -480.0, -80.0]])

    assert len(detect_trend_reversals(moving, graph_run_id=GRAPH_RUN_ID).candidates) == 1
    assert detect_trend_reversals(flat_middle, graph_run_id=GRAPH_RUN_ID).candidates == ()


# ---------------------------------------------------------------------------------------
# The two gates
# ---------------------------------------------------------------------------------------


def test_a_reversal_smaller_than_the_series_own_sigma_does_not_fire():
    """§6.6 D2's σ gate is what turns a noisy alternation into no candidate.

    Both series reverse at the last quarter and both clear D1's `$50M` bar. The first reverses by
    far more than its own spread; the second reverses by less.
    """
    loud = quarterly_points("adjusted_ebitda",
                            [v * MILLION for v in falling_then_rising(80.0, 600.0)])
    quiet = quarterly_points("adjusted_ebitda",
                             [v * MILLION for v in falling_then_rising(400.0, 60.0)])

    assert len(detect_trend_reversals(loud, graph_run_id=GRAPH_RUN_ID).candidates) == 1
    assert detect_trend_reversals(quiet, graph_run_id=GRAPH_RUN_ID).candidates == ()


def test_a_reversal_above_sigma_but_below_the_d1_bar_does_not_fire():
    """The other half of `max(D1 threshold, σ)`.

    A margin series that drifts by 0.2 pp a quarter and then turns by 1.1 pp has a σ of about
    0.4 — so the turn is nearly three σ — and 1.1 pp is well under §6.6's measured 3.0 pp bar.
    D2 must not promote a move D1 would not have reported.
    """
    values = [8.0, 7.8, 7.6, 7.4, 7.2, 7.0, 6.8, 6.6, 6.4, 7.5]
    points = quarterly_points("gaap_gross_margin", values, unit="percent", scale="units",
                              currency=None)

    result = detect_trend_reversals(points, graph_run_id=GRAPH_RUN_ID)

    assert result.candidates == ()
    # Raise the turn past the 3.0 pp bar and the same series does fire, so the σ gate is not
    # what suppressed it.
    louder = quarterly_points("gaap_gross_margin", values[:-1] + [10.0], unit="percent",
                              scale="units", currency=None)
    assert len(detect_trend_reversals(louder, graph_run_id=GRAPH_RUN_ID).candidates) == 1


def test_a_series_with_too_few_deltas_to_have_a_spread_is_refused_rather_than_scanned():
    """§6.10's rule, applied to σ: *"suppressed to `null`, not computed"* below ~8 points."""
    short = quarterly_points("adjusted_ebitda",
                             [v * MILLION for v in [0.0, -100.0, -200.0, 300.0]])

    result = detect_trend_reversals(short, graph_run_id=GRAPH_RUN_ID)

    assert result.candidates == ()
    assert [(r.metric_id, r.reason) for r in result.refusals] == [
        ("adjusted_ebitda", POPULATION_TOO_SMALL)]
    assert f"below the {MIN_DELTA_POPULATION}" in result.refusals[0].detail


def test_a_series_whose_whole_spread_fits_inside_one_printed_unit_is_excluded():
    """§6.6 D2's stated exclusion, driven synthetically because nothing live satisfies it.

    A count series stepping by exactly two homes every quarter: each step clears R8's one-home
    tolerance, so the population is not empty, and σ over nine identical deltas is 0 — under one
    home. The plan names `market_count` as the case; measured, `market_count`'s σ is 3.26 raw and
    4.53 after R1–R10, both far above one market, so this rule fires on nothing in this run and
    only a hand-built series can exercise it.
    """
    points = quarterly_points("homes_sold", [1000.0 + 2 * n for n in range(10)],
                              unit="homes", scale="units", currency=None)

    result = detect_trend_reversals(points, graph_run_id=GRAPH_RUN_ID)

    assert result.candidates == ()
    assert [(r.metric_id, r.reason) for r in result.refusals] == [
        ("homes_sold", LOW_VARIANCE_PRESENTATION_NOISE)]


def test_a_metric_whose_family_has_no_measured_threshold_is_refused_because_the_gate_needs_one():
    points = quarterly_points("mortgage_rate", [3.0, 4.0, 5.0, 2.0], unit="percent",
                              scale="units", currency=None)

    result = detect_trend_reversals(points, graph_run_id=GRAPH_RUN_ID)

    assert result.candidates == ()
    assert [(r.metric_id, r.reason) for r in result.refusals] == [
        ("mortgage_rate", THRESHOLD_UNMEASURED)]


# ---------------------------------------------------------------------------------------
# Consecutiveness, completeness, and polarity
# ---------------------------------------------------------------------------------------


def test_a_run_may_not_be_built_across_a_step_the_comparability_rules_refused():
    """R10 splits a run; it does not let the scan step over the hole.

    Both series reverse in their last quarter after two falls. The second is missing the quarter
    before the run begins — leaving a six-month step — so its prior run is one delta long and
    §6.6 D2's `min_run = 2` is not satisfied.
    """
    values = [v * MILLION for v in falling_then_rising(80.0, 500.0, length=10)]
    whole = quarterly_points("adjusted_ebitda", values)
    holed = quarterly_points("adjusted_ebitda", values[:6] + [None] + values[7:])

    assert len(detect_trend_reversals(whole, graph_run_id=GRAPH_RUN_ID).candidates) == 1
    assert detect_trend_reversals(holed, graph_run_id=GRAPH_RUN_ID).candidates == ()


def test_a_series_the_loader_could_not_prove_complete_produces_a_refusal_and_no_candidate():
    """The same completeness rule D1 keeps, kept here: `unreadable` wins over any points."""
    points = quarterly_points("adjusted_ebitda",
                              [v * MILLION for v in falling_then_rising(80.0, 500.0)])

    scanned = detect_trend_reversals(points, graph_run_id=GRAPH_RUN_ID)
    refused = detect_trend_reversals(points, graph_run_id=GRAPH_RUN_ID,
                                     unreadable=(("adjusted_ebitda", "paging stalled at None"),))

    assert len(scanned.candidates) == 1
    assert refused.candidates == ()
    assert [(r.metric_id, r.reason) for r in refused.refusals] == [
        ("adjusted_ebitda", SERIES_INCOMPLETE)]


def test_the_loader_refusal_reaches_the_detector_through_from_load():
    """`detect_trend_reversals_from_load` is the call that cannot forget to pass `unreadable`."""
    load = ObservationLoad(records=(), unreadable=(("adjusted_ebitda", "paging stalled"),))

    result = detect_trend_reversals_from_load(load, graph_run_id=GRAPH_RUN_ID)

    assert [(r.metric_id, r.reason) for r in result.refusals] == [
        ("adjusted_ebitda", SERIES_INCOMPLETE)]


def test_a_cost_stored_as_a_negative_number_reports_costs_rising_when_its_value_falls():
    """§6.6 D1's polarity rule, at the point where a reversal has to be described.

    `direct_selling_costs` is stored negative — 46 of 46 values in the run are below zero — so a
    series whose value has been **rising** is a cost that has been **falling**, and the quarter
    that drops it by $200M is the quarter costs rose. Neither word may come from the sign of the
    delta.
    """
    values = [v * MILLION for v in
              [-500.0, -440.0, -380.0, -320.0, -260.0, -200.0, -140.0, -80.0, -280.0]]
    points = quarterly_points("direct_selling_costs", values)

    (candidate,) = detect_trend_reversals(points, graph_run_id=GRAPH_RUN_ID).candidates

    assert candidate.signals["delta"] == -200 * MILLION
    assert candidate.signals["polarity"] == "cost"
    assert candidate.signals["direction"] == "increase"
    assert candidate.signals["prior_direction"] == "decrease"


# ---------------------------------------------------------------------------------------
# What a candidate is
# ---------------------------------------------------------------------------------------


def test_every_point_of_the_run_is_an_anchor_and_so_is_every_supporting_observation():
    """The claim is *"it went one way for two quarters and then the other"*, so the evidence
    §10 fetches has to cover all four points, not the two the reversing step joins."""
    points = quarterly_points("adjusted_ebitda",
                              [v * MILLION for v in falling_then_rising(80.0, 500.0)])

    (candidate,) = detect_trend_reversals(points, graph_run_id=GRAPH_RUN_ID).candidates

    assert len(candidate.anchor_period_keys) == MIN_RUN + 2
    assert candidate.anchor_period_keys == ("2021Q2", "2021Q3", "2021Q4", "2022Q1")
    assert candidate.evidence_request == EvidenceRequest(
        metric_ids=("adjusted_ebitda",),
        period_keys=candidate.anchor_period_keys,
        observation_ids=candidate.anchor_observation_ids,
    )
    assert candidate.detector_id == DETECTOR_ID
    assert candidate.story_type == STORY_TYPE


def test_a_candidate_carries_the_run_it_measured_and_no_prose():
    points = quarterly_points("adjusted_ebitda",
                              [v * MILLION for v in falling_then_rising(80.0, 500.0)])

    (candidate,) = detect_trend_reversals(points, graph_run_id=GRAPH_RUN_ID).candidates

    assert candidate.signals["min_run"] == MIN_RUN
    assert candidate.signals["prior_delta_1"] == -80 * MILLION
    assert candidate.signals["prior_delta_2"] == -80 * MILLION
    assert candidate.signals["prior_run_delta"] == -160 * MILLION
    assert candidate.signals["delta_population"] == 8
    assert candidate.signals["sigma"] > 0
    with pytest.raises(ValidationError):
        StoryCandidate(**(candidate.model_dump() | {"thesis_hypothesis": "the trend turned"}))
    assert all(isinstance(v, (bool, int, float, str)) for v in candidate.signals.values())


def test_a_point_warning_from_canonicalisation_is_carried_onto_the_candidate():
    """No point in the current run carries a warning, so §10.1's route is driven synthetically."""
    points = quarterly_points(
        "adjusted_ebitda", [v * MILLION for v in falling_then_rising(80.0, 500.0)],
        warnings=(MINORITY_READING_PRESENT,))

    (candidate,) = detect_trend_reversals(points, graph_run_id=GRAPH_RUN_ID).candidates

    assert candidate.warnings == (MINORITY_READING_PRESENT,)


def test_a_candidate_id_is_the_same_however_the_points_and_their_ids_arrived():
    """§6.11's sorting, driven through both lists the digest covers."""
    points = quarterly_points("adjusted_ebitda",
                              [v * MILLION for v in falling_then_rising(80.0, 500.0)])
    shuffled = tuple(reversed(points))

    forward = detect_trend_reversals(points, graph_run_id=GRAPH_RUN_ID).candidates[0]
    backward = detect_trend_reversals(shuffled, graph_run_id=GRAPH_RUN_ID).candidates[0]

    assert forward.candidate_id == backward.candidate_id
    assert forward.candidate_id.startswith(
        "cand:trend-reversal:adjusted-ebitda:opendoor:2021Q2_2021Q3_2021Q4_2022Q1:")


# ---------------------------------------------------------------------------------------
# Live — §6.6 D2's census, recomputed
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_load():  # type: ignore[no-untyped-def]
    from story.context import build_story_context
    from story.stages.retrieval.graph_tools import BoundedGraphRetriever

    context = build_story_context()
    health = context.executor.verify_connectivity()
    if not health.ok:
        context.close()
        pytest.skip(f"neo4j unavailable: {health.status} — {health.detail}")
    try:
        yield load_observations(BoundedGraphRetriever(context.executor))
    finally:
        context.close()


@pytest.fixture(scope="module")
def live_reversals(live_load):  # type: ignore[no-untyped-def]
    return detect_trend_reversals_from_load(live_load, graph_run_id=GRAPH_RUN_ID)


@pytest.mark.neo4j
def test_live_the_census_is_eleven_reversals_across_nine_metrics(live_reversals):  # type: ignore[no-untyped-def]
    """The gated census, so a threshold or a gate cannot move without moving a number here.

    §6.6 D2's own ungated figure — *"84 reversals across 13 metrics"* — does not reproduce under
    any zero-handling or shape grouping tried; the closest measured variant is 76 across 12 over
    adjacent quarterly rows with no comparability rule applied at all, and 71 across 11 honouring
    R1–R10. The gates are what take it to 11.
    """
    by_shape: dict[str, int] = {}
    for candidate in live_reversals.candidates:
        shape = str(candidate.signals["period_shape"])
        by_shape[shape] = by_shape.get(shape, 0) + 1

    assert len(live_reversals.candidates) == 11
    assert len({c.metric_ids[0] for c in live_reversals.candidates}) == 9
    assert by_shape == {PeriodShape.QUARTER.value: 9, PeriodShape.INSTANT.value: 2}


@pytest.mark.neo4j
def test_live_the_2022q3_collapse_is_a_reversal_in_five_metrics_at_once(live_reversals):  # type: ignore[no-untyped-def]
    """§6.3's F1 and F2 arrive here as one event seen five ways.

    Collapsing them is §6.10's lineage dedup and not this detector's business: `formulas.yaml`
    is what says these five share components, and a detector that hid four of them would be
    deciding a ranking question inside a measurement.
    """
    at_2022q3 = {
        c.metric_ids[0]
        for c in live_reversals.candidates
        if c.anchor_period_keys == ("2021Q4", "2022Q1", "2022Q2", "2022Q3")
    }

    assert at_2022q3 == {"adjusted_ebitda", "adjusted_gross_margin", "contribution_margin",
                         "contribution_profit", "gaap_gross_margin"}


@pytest.mark.neo4j
def test_live_the_adjusted_ebitda_reversal_carries_the_numbers_section_6_3_states(live_reversals):  # type: ignore[no-untyped-def]
    (candidate,) = [
        c for c in live_reversals.candidates
        if c.metric_ids == ("adjusted_ebitda",)
        and c.anchor_period_keys == ("2021Q4", "2022Q1", "2022Q2", "2022Q3")
    ]

    assert candidate.signals["delta"] == -429_000_000.0
    assert candidate.signals["crosses_zero"] is True
    assert candidate.signals["direction"] == "decrease"
    assert candidate.signals["prior_direction"] == "increase"
    assert candidate.signals["min_run"] == MIN_RUN
    assert abs(candidate.signals["delta"]) >= candidate.signals["sigma"]


@pytest.mark.neo4j
def test_live_the_gaap_gross_margin_reversal_is_the_minus_twenty_four_point_two_point_turn(live_reversals):  # type: ignore[no-untyped-def]
    (candidate,) = [
        c for c in live_reversals.candidates
        if c.metric_ids == ("gaap_gross_margin",)
        and c.anchor_period_keys == ("2021Q4", "2022Q1", "2022Q2", "2022Q3")
    ]

    assert candidate.signals["delta_pp"] == -24.2
    assert candidate.signals["prior_delta_1"] == pytest.approx(3.3)
    assert candidate.signals["prior_delta_2"] == pytest.approx(1.2)


@pytest.mark.neo4j
def test_live_market_count_yields_no_candidate_and_not_for_the_reason_the_plan_gives(live_reversals, live_load):  # type: ignore[no-untyped-def]
    """§6.6 D2 says to exclude `market_count` because *"σ(d) is under one presentation unit"*.

    Measured, that is not why. Its twenty adjacent instant deltas are `3, 0, 6, 12, 5, 0, 1, 6,
    0, 2, 0, 0, 0, −3` then six more zeros — it steps up through 2021–22 and then goes flat; it
    does not alternate — and σ over them is **3.26**, or **4.53** over the six that survive
    R1–R10, against a tolerance of one market. What removes it is R8 refusing the twelve zero
    steps and the one-market step as rounding, and R10 refusing the twelve-month
    `2018-12-31 → 2019-12-31` step: six deltas left, too few for a spread.
    """
    import statistics

    from story.core.series import build_series
    from story.stages.detection.canonicalization import canonicalize

    series = build_series(canonicalize(live_load.records))["market_count"]
    points = series.valued_points(PeriodShape.INSTANT)
    raw = [round(b.value - a.value, 9) for a, b in zip(points, points[1:])]

    assert len(raw) == 20
    assert raw.count(0.0) == 12
    assert statistics.pstdev(raw) == pytest.approx(3.2619, abs=1e-4)
    assert not [c for c in live_reversals.candidates if c.metric_ids == ("market_count",)]
    assert ("market_count", POPULATION_TOO_SMALL) in [
        (r.metric_id, r.reason) for r in live_reversals.refusals]


@pytest.mark.neo4j
def test_live_no_series_at_any_shape_is_excluded_for_low_variance(live_reversals):  # type: ignore[no-untyped-def]
    """The plan's own exclusion rule, and the measurement that it excludes nothing here."""
    assert [r for r in live_reversals.refusals
            if r.reason == LOW_VARIANCE_PRESENTATION_NOISE] == []


@pytest.mark.neo4j
def test_live_every_metric_and_shape_that_was_not_scanned_says_so(live_reversals):  # type: ignore[no-untyped-def]
    """*Not scanned* and *scanned and quiet* must be distinguishable after the run.

    Thirty-nine metric-and-shape combinations hold too few comparable deltas for a σ — every
    fiscal-year and year-to-date series in the corpus among them, since a twelve-month step
    recurs at most eight times over 2018–2026.
    """
    reasons = [r.reason for r in live_reversals.refusals]

    assert len(reasons) == 39
    assert set(reasons) == {POPULATION_TOO_SMALL}


@pytest.mark.neo4j
def test_live_every_candidate_stands_on_a_series_that_read_to_the_end(live_load):  # type: ignore[no-untyped-def]
    assert live_load.unreadable == ()
