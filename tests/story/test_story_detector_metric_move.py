"""S3-A — §6.6 D1's arithmetic, its thresholds, its polarity map, and what it refuses.

Every rule is driven for the decision it makes, not for the shape it makes it in. The threshold
table is exercised through `measure_move` against hand-built points, so a bar can be moved past
in both directions without a graph; the refusals are exercised through the loader, because
*"the series could not be proved complete"* is a fact about paging and cannot be faked at the
point of the delta.

The `neo4j`-marked tests at the bottom reproduce the live census and the two candidates §6.3
names, recomputed from the canonical series rather than transcribed — including
`adjusted_gross_margin`'s `−9.899999999999999`, which is compared after rounding because it is a
real IEEE 754 residue and equality against `−9.9` is false.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from story.core.models import EvidenceRequest, StoryCandidate
from story.core.periods import PeriodShape, story_period
from story.core.series import CanonicalPoint, CanonicalStatus, build_series
from story.stages.detection.canonicalization import (
    MINORITY_READING_PRESENT,
    ObservationLoad,
    canonicalize,
    load_observations,
    record_from_rows,
)
from story.stages.detection.detector_config import (
    FAMILY_THRESHOLDS,
    METRIC_FAMILY,
    METRIC_POLARITY,
    SIGN_CONVENTION_UNVERIFIED,
    THRESHOLD_UNMEASURED,
    VALUE_SIGN,
    FamilyThresholds,
    MetricFamily,
    MetricPolarity,
    ValueSign,
    quantity_direction,
    thresholds_for,
    value_floor,
)
from story.stages.detection.metric_move import (
    ARM_ABSOLUTE,
    ARM_PERCENTAGE_POINTS,
    ARM_RELATIVE_PERCENT,
    DETECTOR_ID,
    RELATIVE_CHANGE_ACROSS_ZERO,
    SERIES_INCOMPLETE,
    detect_metric_moves,
    detect_metric_moves_from_load,
    measure_move,
)

GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"

#: §6.6 D1's USD row, used wherever a test needs a bar rather than a particular metric's bar.
USD = FAMILY_THRESHOLDS[MetricFamily.USD_MEASURE]
MARGIN = FAMILY_THRESHOLDS[MetricFamily.MARGIN]


def make_point(**overrides: Any) -> CanonicalPoint:
    """A canonical `adjusted_ebitda` 2022Q3 point, with keyword overrides."""
    fields: dict[str, Any] = dict(
        metric_id="adjusted_ebitda",
        period=story_period("2022-07-01", "2022-09-30"),
        subject_entity_id="opendoor",
        status=CanonicalStatus.OK,
        value=-211_000_000.0,
        unit="USD",
        scale="millions",
        currency="USD",
        representative_observation_id="obs:one",
        supporting_observation_ids=("obs:one",),
        n_docs=4,
        distinct_values=1,
    )
    fields.update(overrides)
    return CanonicalPoint(**fields)


def quarter(index: int) -> Any:
    """The `index`-th quarter of 2022 as a `StoryPeriod`, 1-based."""
    starts = {1: ("2022-01-01", "2022-03-31"), 2: ("2022-04-01", "2022-06-30"),
              3: ("2022-07-01", "2022-09-30"), 4: ("2022-10-01", "2022-12-31")}
    return story_period(*starts[index])


def quarterly(metric_id: str, values: dict[int, float], **overrides: Any) -> tuple[CanonicalPoint, ...]:
    """One 2022 quarter per entry, sharing a unit and a subject."""
    return tuple(
        make_point(
            metric_id=metric_id,
            period=quarter(index),
            value=value,
            representative_observation_id=f"obs:{metric_id}:{index}",
            supporting_observation_ids=(f"obs:{metric_id}:{index}",),
            **overrides,
        )
        for index, value in sorted(values.items())
    )


# ---------------------------------------------------------------------------------------
# The tables — §6.6 D1's polarity map and threshold families
# ---------------------------------------------------------------------------------------


def declared_metric_ids() -> set[str]:
    """The ontology's own metric ids, so the coverage assertion cannot drift with the corpus."""
    from ontology import load_ontology

    return {metric.concept_id for metric in load_ontology().definitions.metrics}


def test_the_ontology_still_declares_no_polarity_key_which_is_why_the_map_is_story_owned():
    """The correction §6.6 D1 records, asserted rather than trusted.

    If a `polarity` key were ever added to `metrics.yaml` this test fails and the story-owned
    table becomes a second authority — exactly the state C4 forbids for metric metadata.
    """
    from ontology import load_ontology

    metrics = load_ontology().definitions.metrics

    assert len(metrics) == 26
    assert all(getattr(metric, "polarity", None) is None for metric in metrics)


def test_the_polarity_map_covers_every_one_of_the_twenty_six_declared_metrics():
    assert set(METRIC_POLARITY) == declared_metric_ids()


def test_the_value_sign_map_covers_every_one_of_the_twenty_six_declared_metrics():
    assert set(VALUE_SIGN) == declared_metric_ids()


def test_the_threshold_family_map_covers_every_one_of_the_twenty_six_declared_metrics():
    assert set(METRIC_FAMILY) == declared_metric_ids()


def test_the_two_metrics_the_corpus_prints_as_negative_costs_are_the_two_marked_negative():
    """`direct_selling_costs` and `holding_costs`, and nothing else, is a measured claim.

    Every other cost metric in the ontology has zero observations, so its convention is
    `UNVERIFIED` and not `NEGATIVE` — the corpus cannot say, and a default would be a guess.
    """
    negative = {m for m, sign in VALUE_SIGN.items() if sign is ValueSign.NEGATIVE}
    unverified = {m for m, sign in VALUE_SIGN.items() if sign is ValueSign.UNVERIFIED}

    assert negative == {"direct_selling_costs", "holding_costs"}
    assert unverified == {"cost_of_revenue", "inventory_valuation_adjustment"}


def test_a_family_with_no_measured_percentile_declares_no_threshold():
    """§6.6's table has five rows; `MACRO_PERCENT` is not one of them and gets no bar."""
    assert MetricFamily.MACRO_PERCENT not in FAMILY_THRESHOLDS
    assert thresholds_for("mortgage_rate") is None
    assert thresholds_for("home_price_appreciation") is None
    assert set(FAMILY_THRESHOLDS) == set(MetricFamily) - {MetricFamily.MACRO_PERCENT}


def test_the_four_margins_share_one_threshold_and_the_aging_percentage_does_not():
    """§6.6's own separation: *"`pct_>120d` is genuinely ten times noisier than the margins"*."""
    margins = {"gaap_gross_margin", "adjusted_gross_margin", "contribution_margin",
               "adjusted_ebitda_margin"}

    assert {METRIC_FAMILY[m] for m in margins} == {MetricFamily.MARGIN}
    assert MARGIN.pct_min == 3.0
    assert FAMILY_THRESHOLDS[MetricFamily.AGING_PERCENT].pct_min == 19.0


# ---------------------------------------------------------------------------------------
# Polarity — direction is never read off the sign
# ---------------------------------------------------------------------------------------


def test_a_cost_stored_as_a_negative_number_rises_when_its_value_falls():
    """The false positive §6.6 D1 names: *"`direct_selling_costs` values are negative, so
    'costs rose' is a decrease"*.

    Driven against the metric with the same sized delta in both directions, so nothing but the
    metric id can be producing the answer.
    """
    assert quantity_direction("direct_selling_costs", -12_000_000.0) == "increase"
    assert quantity_direction("direct_selling_costs", +12_000_000.0) == "decrease"
    assert quantity_direction("holding_costs", -1_000_000.0) == "increase"


def test_a_negative_valued_metric_is_not_the_same_thing_as_a_metric_with_negative_values():
    """`adjusted_ebitda` is negative in 37 of its 48 canonical slots and is `POSITIVE`-signed.

    A loss is not a sign convention, and a rule that read the convention off the data would call
    every loss-making quarter a cost line.
    """
    assert VALUE_SIGN["adjusted_ebitda"] is ValueSign.POSITIVE
    assert quantity_direction("adjusted_ebitda", -429_000_000.0) == "decrease"
    assert quantity_direction("adjusted_ebitda", +429_000_000.0) == "increase"


def test_a_metric_whose_sign_convention_was_never_measured_is_given_no_direction():
    assert quantity_direction("cost_of_revenue", -1.0) is None
    assert quantity_direction("a_metric_no_map_declares", -1.0) is None


def test_a_candidate_for_a_metric_of_unverified_sign_carries_the_warning_instead_of_a_direction():
    """The move is real and citable; only the sentence describing it is unavailable."""
    points = quarterly("cost_of_revenue", {1: 100_000_000.0, 2: 900_000_000.0}, unit="USD")

    result = detect_metric_moves(points, graph_run_id=GRAPH_RUN_ID)

    (candidate,) = result.candidates
    assert "direction" not in candidate.signals
    assert SIGN_CONVENTION_UNVERIFIED in candidate.warnings


# ---------------------------------------------------------------------------------------
# The arithmetic — §6.6 D1's four lines
# ---------------------------------------------------------------------------------------


def test_a_percentage_point_move_fires_on_the_percentage_point_arm_and_not_the_relative_one():
    """The correction this module records: for a percent metric the column is `pp_min`.

    `gaap_gross_margin 2022Q2 → 2022Q3` is `−24.2 pp` and would be `−208.6%` relative. It must
    fire, and it must fire on `pp` — a candidate recording `pct` would mean the 3.0 in the table
    had been read as a relative bar, which at a base of 11.6 is a bar of 0.35 pp.

    The `−208.6%` is not reported at all: the metric is denominated in percent *and* the step
    runs through zero, so both suppressions apply and only `delta_pp` names the move.
    """
    earlier = make_point(metric_id="gaap_gross_margin", period=quarter(2), value=11.6,
                         unit="percent", scale="units", currency=None)
    later = make_point(metric_id="gaap_gross_margin", period=quarter(3), value=-12.6,
                       unit="percent", scale="units", currency=None)

    measurement = measure_move(earlier, later, thresholds=MARGIN, floor=0.825)

    assert measurement.fired_on == (ARM_PERCENTAGE_POINTS,)
    assert round(measurement.delta_pp, 9) == -24.2
    assert measurement.delta_pct is None


def test_a_percentage_metric_below_its_percentage_point_bar_does_not_fire_however_large_the_relative_move():
    """A margin of `0.4` moving to `1.2` is `+200%` relative and `+0.8 pp`. §6.6's measured p75
    is 3.0 **pp**, so this is noise and the relative arm must not rescue it — nor may the `+200%`
    be reported, which is the second half of the same rule."""
    earlier = make_point(metric_id="gaap_gross_margin", period=quarter(2), value=0.4,
                         unit="percent", scale="units", currency=None)
    later = make_point(metric_id="gaap_gross_margin", period=quarter(3), value=1.2,
                       unit="percent", scale="units", currency=None)

    measurement = measure_move(earlier, later, thresholds=MARGIN, floor=0.1)

    assert measurement.delta_pct is None
    assert measurement.delta_pp == 0.8
    assert measurement.fired_on == ()
    assert measurement.fires is False


def test_a_dollar_move_may_fire_on_the_relative_arm_the_absolute_arm_or_both():
    small_base = make_point(period=quarter(1), value=10_000_000.0)
    big_relative = make_point(period=quarter(2), value=30_000_000.0)
    big_absolute = make_point(period=quarter(2), value=10_000_000.0 + 60_000_000.0)

    relative_only = measure_move(small_base, big_relative, thresholds=USD, floor=1_000_000.0)
    both = measure_move(small_base, big_absolute, thresholds=USD, floor=1_000_000.0)

    assert relative_only.fired_on == (ARM_RELATIVE_PERCENT,)
    assert both.fired_on == (ARM_RELATIVE_PERCENT, ARM_ABSOLUTE)


def test_a_percentage_change_is_suppressed_when_the_base_is_below_the_metric_floor():
    """§6.6 D1's small-base rule, on the case it was written for.

    `adjusted_ebitda 2021Q4 → 2022Q1` is `$0.4M → $176M`. As a percentage that is `+43,900%`
    and it would be the largest move in the corpus; the floor is `0.10 × median(|value|)`, which
    this metric's history puts at `$5.8M`, so no percentage is reported at all.
    """
    earlier = make_point(period=quarter(1), value=400_000.0)
    later = make_point(period=quarter(2), value=176_000_000.0)

    unfloored = measure_move(earlier, later, thresholds=USD, floor=0.0)
    floored = measure_move(earlier, later, thresholds=USD, floor=5_797_350.0)

    assert round(unfloored.delta_pct, 1) == 43_900.0
    assert floored.delta_pct is None
    # It is still a real $175.6M move, so the absolute arm still fires — the floor suppresses a
    # ratio, it does not suppress a candidate.
    assert floored.fired_on == (ARM_ABSOLUTE,)


def test_a_relative_change_taken_across_zero_is_refused_and_the_candidate_says_so():
    """§13.3's third gate, at the detector rather than at the verifier.

    `adjusted_ebitda 2022Q2 → 2022Q3` is `+$218M → −$211M`; `(−211 − 218)/218 = −197%` is defined
    and unreadable, and `story.core.numerals.delta_relative` raises on exactly this pair. The
    number must not be published — but the step is still a $429M move that fires, so the arms are
    untouched and the absence is stated as a warning rather than left to look like the floor's.
    """
    earlier, later = quarterly("adjusted_ebitda", {2: 218_000_000.0, 3: -211_000_000.0})

    measurement = measure_move(earlier, later, thresholds=USD, floor=5_797_350.0)
    (candidate,) = detect_metric_moves((earlier, later), graph_run_id=GRAPH_RUN_ID).candidates

    assert measurement.relative_change_refused is True
    assert measurement.delta_pct is None
    assert measurement.delta == -429_000_000.0
    assert measurement.fired_on == (ARM_RELATIVE_PERCENT, ARM_ABSOLUTE)
    assert "delta_pct" not in candidate.signals
    assert candidate.signals["crosses_zero"] is True
    assert RELATIVE_CHANGE_ACROSS_ZERO in candidate.warnings


def test_a_base_of_exactly_zero_refuses_the_relative_change_although_nothing_crossed():
    """The one case `crosses_zero` does not cover, which is why the predicate is `numerals`'.

    `0 → 60,000,000` is not a sign flip — `crosses_zero` is correctly `False` — and `Δ/|v0|` is
    not meaningless but undefined. Both end in no `delta_pct`, and the candidate says why. Only
    the absolute arm can fire from a base of zero, which is the $50M bar and not the 65% one.
    """
    earlier, later = quarterly("adjusted_ebitda", {2: 0.0, 3: 60_000_000.0})

    measurement = measure_move(earlier, later, thresholds=USD, floor=5_797_350.0)
    (candidate,) = detect_metric_moves((earlier, later), graph_run_id=GRAPH_RUN_ID).candidates

    assert measurement.crosses_zero is False
    assert measurement.relative_change_refused is True
    assert measurement.delta_pct is None
    assert measurement.fired_on == (ARM_ABSOLUTE,)
    assert RELATIVE_CHANGE_ACROSS_ZERO in candidate.warnings


def test_a_step_that_lands_on_zero_still_reports_its_relative_change():
    """The boundary the gate must not over-reach. `v1 == 0` divides by a real base: `$40M → $0`
    is `−100%`, a number a reader can act on, and it stays."""
    earlier, later = quarterly("adjusted_ebitda", {2: 40_000_000.0, 3: 0.0})

    measurement = measure_move(earlier, later, thresholds=USD, floor=5_797_350.0)
    (candidate,) = detect_metric_moves((earlier, later), graph_run_id=GRAPH_RUN_ID).candidates

    assert measurement.relative_change_refused is False
    assert measurement.delta_pct == -100.0
    assert RELATIVE_CHANGE_ACROSS_ZERO not in candidate.warnings


def test_a_percent_metric_never_reports_a_relative_change_even_where_the_sign_never_flips():
    """The percent-of-a-percent suppression, on the case the cross-zero gate does **not** catch.

    `contribution_margin 2022Q3 → 2022Q4` is `−0.7% → −7.2%`: both negative, so nothing crossed,
    and the relative change is `−928.57%` — a second percentage contradicting the `−6.5 pp` the
    same candidate carries. §6.6 D1's floor is no guard: `0.10 × median(|v|)` is 0.455 **pp** on
    this metric's history, so a 0.7 pp base clears it easily. `delta_pp` already says what moved.
    """
    earlier = make_point(metric_id="contribution_margin", period=quarter(3), value=-0.7,
                         unit="percent", scale="units", currency=None)
    later = make_point(metric_id="contribution_margin", period=quarter(4), value=-7.2,
                       unit="percent", scale="units", currency=None)

    measurement = measure_move(earlier, later, thresholds=MARGIN, floor=0.455)

    assert measurement.relative_change_refused is False
    assert round(-6.5 / 0.7 * 100, 2) == -928.57  # what would have been published
    assert measurement.delta_pct is None
    assert measurement.delta_pp == -6.5
    assert measurement.fired_on == (ARM_PERCENTAGE_POINTS,)


def test_the_floor_is_a_tenth_of_the_median_absolute_value_over_the_metrics_whole_history():
    assert value_floor([1.0, 3.0, 5.0, 7.0]) == pytest.approx(0.4)
    assert value_floor([-100.0, 100.0]) == pytest.approx(10.0)
    assert value_floor([]) is None


def test_a_step_that_lands_on_zero_has_not_crossed_it_and_a_step_through_it_has():
    """`adjusted_ebitda 2021Q3 → 2021Q4` approaches zero; `2022Q2 → 2022Q3` goes through it."""
    approaching = measure_move(
        make_point(period=quarter(1), value=34_509_000.0),
        make_point(period=quarter(2), value=400_000.0),
        thresholds=USD, floor=5_797_350.0)
    crossing = measure_move(
        make_point(period=quarter(2), value=218_000_000.0),
        make_point(period=quarter(3), value=-211_000_000.0),
        thresholds=USD, floor=5_797_350.0)

    assert approaching.crosses_zero is False
    assert crossing.crosses_zero is True


def test_every_delta_is_rounded_before_it_is_compared_with_anything():
    """`13.2 − 3.3` is `−9.899999999999999` in IEEE 754, and `adjusted_gross_margin` really does
    hold those two values in adjacent quarters. A bar tested on the raw subtraction would decide
    a candidate on the seventeenth digit."""
    earlier = make_point(metric_id="adjusted_gross_margin", period=quarter(2), value=13.2,
                         unit="percent", scale="units", currency=None)
    later = make_point(metric_id="adjusted_gross_margin", period=quarter(3), value=3.3,
                       unit="percent", scale="units", currency=None)

    measurement = measure_move(earlier, later, thresholds=MARGIN, floor=0.8)

    assert later.value - earlier.value == -9.899999999999999
    assert measurement.delta == -9.9


def test_a_pair_where_either_point_emits_no_value_cannot_be_measured_at_all():
    """R7's state, raised rather than returned: a caller that reached here skipped `comparable`."""
    with pytest.raises(ValueError):
        measure_move(
            make_point(status=CanonicalStatus.CONFLICT, value=None, period=quarter(1)),
            make_point(period=quarter(2)),
            thresholds=USD, floor=0.0)


# ---------------------------------------------------------------------------------------
# The scan — comparability, completeness, and what a candidate is
# ---------------------------------------------------------------------------------------


def test_no_delta_is_taken_across_a_hole_the_run_can_see():
    """§6.6 D8's measured defect, as a D1 test.

    `pct_homes_on_market_gt_120_days` holds `2021-12-31 = 8%` and `2022-12-31 = 55%` with a
    four-quarter hole between them. They are **neighbouring rows**, so "nothing lies between
    them" is satisfied; only R10's calendar-step check catches it. A `+47 pp` jump would be the
    largest aging move in the corpus and it never happened.
    """
    points = tuple(
        make_point(metric_id="pct_homes_on_market_gt_120_days",
                   period=story_period(instant_date=date), value=value, unit="percent",
                   scale="units", currency=None,
                   representative_observation_id=f"obs:{date}",
                   supporting_observation_ids=(f"obs:{date}",))
        for date, value in (("2021-12-31", 8.0), ("2022-12-31", 55.0), ("2023-03-31", 59.0))
    )

    result = detect_metric_moves(points, graph_run_id=GRAPH_RUN_ID)

    assert result.candidates == ()
    # The 2022-12-31 → 2023-03-31 step *is* adjacent, and is 4 pp — below the 19 pp bar. The
    # empty result is therefore the hole being refused, not the whole series being dropped.
    assert build_series(points)["pct_homes_on_market_gt_120_days"].points[1].value == 55.0


def test_a_metric_whose_family_has_no_measured_threshold_is_refused_rather_than_judged():
    points = quarterly("mortgage_rate", {1: 3.0, 2: 9.0}, unit="percent", scale="units",
                       currency=None)

    result = detect_metric_moves(points, graph_run_id=GRAPH_RUN_ID)

    assert result.candidates == ()
    assert [(r.metric_id, r.reason) for r in result.refusals] == [
        ("mortgage_rate", THRESHOLD_UNMEASURED)]


class TruncatedHistoryRetriever:
    """A `GraphRetriever` whose `get_metric_history` always comes back truncated on one anchor.

    The failure S2 built `_paged_history` to refuse: one anchor date holding more rows than the
    bound, so no `$since` value can advance past it. Written as a stub rather than driven off
    the live graph because no metric in the current run has that shape — the largest single
    anchor date is 20 rows — and a completeness rule that could only be tested by a corpus
    defect would be a rule nobody could test.
    """

    def __init__(self) -> None:
        self.rows = tuple(
            {"observation_id": f"obs:{index}", "metric_id": "adjusted_ebitda",
             "subject_entity_id": "opendoor", "period_end": "2022-09-30",
             "period_start": "2022-07-01", "value": -211_000_000.0, "unit": "USD",
             "scale": "millions", "currency": "USD", "source_lane": "normalized_table",
             "document_id": "doc:1"}
            for index in range(3)
        )

    def call(self, tool: str, parameters: Any) -> Any:
        from story.core.models import RetrievalOutcome, RetrievalResult

        if tool == "list_metrics":
            return RetrievalResult(outcome=RetrievalOutcome.OK,
                                   rows=({"metric_id": "adjusted_ebitda"},))
        if tool == "get_metric_history":
            return RetrievalResult(outcome=RetrievalOutcome.OK, rows=self.rows, truncated=True)
        return RetrievalResult(outcome=RetrievalOutcome.OK)

    def trace(self):  # type: ignore[no-untyped-def]
        return ()


def test_a_series_the_loader_could_not_prove_complete_produces_a_refusal_and_no_candidate():
    """§6.1's paging bound, carried all the way to the detector.

    The loader returns **no rows at all** for a metric it could not page to the end of, so the
    only way a detector can know the metric exists is `ObservationLoad.unreadable`. A detector
    that ignored the field would report a metric with a truncated history exactly the same way
    it reports a metric with a quiet one.
    """
    load = load_observations(TruncatedHistoryRetriever(), with_evidence=False)

    result = detect_metric_moves_from_load(load, graph_run_id=GRAPH_RUN_ID)

    assert load.records == ()
    assert [(r.metric_id, r.reason) for r in result.refusals] == [
        ("adjusted_ebitda", SERIES_INCOMPLETE)]
    assert result.candidates == ()
    assert "paging stalled" in result.refusals[0].detail


def test_a_metric_named_unreadable_is_never_scanned_even_when_points_for_it_are_supplied():
    """Belt and braces on the same rule: the refusal wins over any points that reached the scan.

    A caller could hand in canonical points built from a partial page — from a cache, or from a
    second loader — and the completeness statement has to be the authority, not the data.
    """
    points = quarterly("adjusted_ebitda", {2: 218_000_000.0, 3: -211_000_000.0})

    scanned = detect_metric_moves(points, graph_run_id=GRAPH_RUN_ID)
    refused = detect_metric_moves(points, graph_run_id=GRAPH_RUN_ID,
                                  unreadable=(("adjusted_ebitda", "paging stalled"),))

    assert len(scanned.candidates) == 1
    assert refused.candidates == ()
    assert refused.refusals[0].reason == SERIES_INCOMPLETE


def test_a_candidate_carries_no_prose_no_thesis_and_no_score():
    """§6.4's absence, enforced by `extra="forbid"` and asserted here so it stays enforced."""
    points = quarterly("adjusted_ebitda", {2: 218_000_000.0, 3: -211_000_000.0})

    (candidate,) = detect_metric_moves(points, graph_run_id=GRAPH_RUN_ID).candidates

    assert set(StoryCandidate.model_fields) & {
        "thesis_hypothesis", "headline", "detector_headline", "score", "materiality",
        "novelty", "evidence_quality"} == set()
    with pytest.raises(ValidationError):
        StoryCandidate(**(candidate.model_dump() | {"thesis_hypothesis": "EBITDA collapsed"}))
    # Signals are numbers, flags and single closed words. A sentence in here would be the same
    # seam §6.4 removed `thesis_hypothesis` to close.
    assert all(isinstance(value, (bool, int, float, str)) for value in candidate.signals.values())
    assert all(
        not isinstance(value, str) or " " not in value for value in candidate.signals.values())


def test_every_candidate_carries_an_evidence_request_naming_the_metric_and_both_periods():
    points = quarterly("adjusted_ebitda", {2: 218_000_000.0, 3: -211_000_000.0})

    (candidate,) = detect_metric_moves(points, graph_run_id=GRAPH_RUN_ID).candidates

    assert candidate.evidence_request == EvidenceRequest(
        metric_ids=("adjusted_ebitda",),
        period_keys=("2022Q2", "2022Q3"),
        observation_ids=("obs:adjusted_ebitda:2", "obs:adjusted_ebitda:3"),
    )
    assert candidate.evidence_request.want_counter_evidence is True
    assert candidate.evidence_request.want_explanatory_search is False


def test_a_point_warning_from_canonicalisation_is_carried_onto_the_candidate():
    """§10.1 has to disclose a minority reading, and the only route from §6.1 to §10 is here.

    No point in the current run carries a warning — 537 slots, 0 minority, 0 quarantined — so
    this is driven synthetically. A propagation that fired on nothing and was never tested would
    be a propagation that had silently stopped working.
    """
    earlier, later = quarterly("adjusted_ebitda", {2: 218_000_000.0, 3: -211_000_000.0})
    flagged = later.__class__(
        **{**{field: getattr(later, field) for field in later.__slots__},
           "warnings": (MINORITY_READING_PRESENT,),
           "minority_observation_ids": ("obs:minority",)})

    (candidate,) = detect_metric_moves((earlier, flagged),
                                       graph_run_id=GRAPH_RUN_ID).candidates

    # `+$218M → −$211M` also runs through zero, so the step's own refusal rides alongside the
    # point's warning; the assertion is on the whole tuple so a lost propagation still fails.
    assert candidate.warnings == (MINORITY_READING_PRESENT, RELATIVE_CHANGE_ACROSS_ZERO)
    # The minority reading backs a number this run did not use, so it is disclosed and is not
    # an anchor.
    assert "obs:minority" not in candidate.anchor_observation_ids


def test_a_candidate_id_is_the_same_however_the_observation_ids_arrived():
    """§6.11: *"anchor ids are sorted so input ordering cannot change the id"*."""
    forward = make_point(period=quarter(2), value=218_000_000.0,
                         supporting_observation_ids=("obs:a", "obs:b"))
    reversed_ids = make_point(period=quarter(2), value=218_000_000.0,
                              supporting_observation_ids=("obs:b", "obs:a"))
    later = make_point(period=quarter(3), value=-211_000_000.0,
                       supporting_observation_ids=("obs:c",))

    first = detect_metric_moves((forward, later), graph_run_id=GRAPH_RUN_ID).candidates[0]
    second = detect_metric_moves((later, reversed_ids), graph_run_id=GRAPH_RUN_ID).candidates[0]

    assert first.candidate_id == second.candidate_id
    assert first.candidate_id.startswith("cand:metric-move:adjusted-ebitda:opendoor:2022Q2_2022Q3:")
    assert first.anchor_observation_ids == ("obs:a", "obs:b", "obs:c")


def test_a_conflicted_slot_is_not_a_step_and_the_hole_it_leaves_is_refused():
    """A `CONFLICT` slot emits no value, so `valued_points` drops it — and the two points either
    side are then six months apart, which R10 measures and refuses."""
    points = (
        *quarterly("adjusted_ebitda", {1: 176_000_000.0}),
        make_point(period=quarter(2), status=CanonicalStatus.CONFLICT, value=None,
                   representative_observation_id=None, supporting_observation_ids=()),
        *quarterly("adjusted_ebitda", {3: -211_000_000.0}),
    )

    result = detect_metric_moves(points, graph_run_id=GRAPH_RUN_ID)

    assert result.candidates == ()


# ---------------------------------------------------------------------------------------
# Live — the census and the two candidates §6.3 names
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_points():  # type: ignore[no-untyped-def]
    from story.context import build_story_context
    from story.stages.retrieval.graph_tools import BoundedGraphRetriever

    context = build_story_context()
    health = context.executor.verify_connectivity()
    if not health.ok:
        context.close()
        pytest.skip(f"neo4j unavailable: {health.status} — {health.detail}")
    try:
        load = load_observations(BoundedGraphRetriever(context.executor))
        yield ObservationLoad(records=load.records, unreadable=load.unreadable,
                              calls=load.calls)
    finally:
        context.close()


@pytest.fixture(scope="module")
def live_moves(live_points):  # type: ignore[no-untyped-def]
    return detect_metric_moves_from_load(live_points, graph_run_id=GRAPH_RUN_ID)


@pytest.mark.neo4j
def test_live_every_metric_reads_to_the_end_so_nothing_is_refused_as_incomplete(live_points):  # type: ignore[no-untyped-def]
    """Five metrics exceed `get_metric_history`'s 200-row bound and the loader pages past all
    five. The claim being pinned is that **today** no series is short, so every candidate below
    stands on a complete history."""
    assert live_points.unreadable == ()
    assert len(live_points.records) == 2704


@pytest.mark.neo4j
def test_live_the_adjusted_ebitda_sign_reversal_is_a_candidate_with_the_numbers_section_6_3_states(
    live_moves,  # type: ignore[no-untyped-def]
):
    """§6.3's F1, recomputed: `2022Q2 = +$218M` → `2022Q3 = −$211M`, Δ = −$429M, crossing zero."""
    (candidate,) = [
        c for c in live_moves.candidates
        if c.metric_ids == ("adjusted_ebitda",) and c.anchor_period_keys == ("2022Q2", "2022Q3")
    ]

    assert candidate.signals["delta"] == -429_000_000.0
    assert candidate.signals["crosses_zero"] is True
    # The step it crossed on is the step whose relative change §13.3 refuses.
    assert "delta_pct" not in candidate.signals
    assert RELATIVE_CHANGE_ACROSS_ZERO in candidate.warnings
    assert candidate.signals["direction"] == "decrease"
    assert candidate.signals["polarity"] == MetricPolarity.REVENUE.value
    assert candidate.signals["fired_on"] == f"{ARM_RELATIVE_PERCENT},{ARM_ABSOLUTE}"
    assert candidate.detector_id == DETECTOR_ID
    assert candidate.candidate_id.startswith(
        "cand:metric-move:adjusted-ebitda:opendoor:2022Q2_2022Q3:")


@pytest.mark.neo4j
def test_live_gaap_gross_margin_going_negative_is_a_candidate_of_minus_twenty_four_point_two_points(
    live_moves,  # type: ignore[no-untyped-def]
):
    """§6.3's F2: `11.6% → −12.6%`, **−24.2 pp** — the only negative GAAP gross margin in 26
    quarters, and it fires on the percentage-point arm."""
    (candidate,) = [
        c for c in live_moves.candidates
        if c.metric_ids == ("gaap_gross_margin",)
        and c.anchor_period_keys == ("2022Q2", "2022Q3")
    ]

    assert candidate.signals["delta_pp"] == -24.2
    assert candidate.signals["crosses_zero"] is True
    assert candidate.signals["fired_on"] == ARM_PERCENTAGE_POINTS


@pytest.mark.neo4j
def test_live_the_adjusted_gross_margin_move_is_compared_after_rounding_not_by_equality(live_points):  # type: ignore[no-untyped-def]
    """`13.2 → 3.3` subtracts to `−9.899999999999999` on the real values, and the candidate
    carries `−9.9`. Pinned against the live numbers because it is the residue §13.9 records."""
    series = build_series(canonicalize(live_points.records))["adjusted_gross_margin"]
    earlier, later = series.point("2022Q2"), series.point("2022Q3")

    assert (earlier.value, later.value) == (13.2, 3.3)
    assert later.value - earlier.value == -9.899999999999999
    measurement = measure_move(earlier, later, thresholds=MARGIN,
                               floor=value_floor([p.value for p in series.valued_points()]))
    assert measurement.delta == -9.9
    assert measurement.fired_on == (ARM_PERCENTAGE_POINTS,)


@pytest.mark.neo4j
def test_live_the_census_is_two_hundred_and_twenty_seven_moves_across_sixteen_metrics(live_moves):  # type: ignore[no-untyped-def]
    """The whole D1 census, so a threshold change has to move a number in this file.

    The shape split is recorded because it is the module's one open concession: §6.6's bars are
    quarter-over-quarter percentiles, and 112 of these 227 are twelve-month steps judged against
    them.
    """
    by_shape: dict[str, int] = {}
    for candidate in live_moves.candidates:
        shape = str(candidate.signals["period_shape"])
        by_shape[shape] = by_shape.get(shape, 0) + 1

    assert len(live_moves.candidates) == 227
    assert len({c.metric_ids[0] for c in live_moves.candidates}) == 16
    assert by_shape == {
        PeriodShape.QUARTER.value: 93,
        PeriodShape.INSTANT.value: 22,
        PeriodShape.FISCAL_YEAR.value: 42,
        PeriodShape.YTD_6M.value: 34,
        PeriodShape.YTD_9M.value: 36,
    }


@pytest.mark.neo4j
def test_live_no_candidate_carries_a_percentage_computed_over_a_base_below_its_floor(live_moves, live_points):  # type: ignore[no-untyped-def]
    """The floor, checked against every candidate rather than against the one case it was
    written for. `adjusted_ebitda 2021Q4 → 2022Q1` is the one that would otherwise read
    `+43,900%`, and it is present as a candidate with **no** `delta_pct`."""
    series = build_series(canonicalize(live_points.records))
    for candidate in live_moves.candidates:
        if "delta_pct" not in candidate.signals:
            continue
        metric = candidate.metric_ids[0]
        floor = value_floor([p.value for p in series[metric].valued_points()])
        earlier = series[metric].point(candidate.anchor_period_keys[0])
        assert abs(earlier.value) >= floor

    (small_base,) = [
        c for c in live_moves.candidates
        if c.metric_ids == ("adjusted_ebitda",) and c.anchor_period_keys == ("2021Q4", "2022Q1")
    ]
    assert "delta_pct" not in small_base.signals
    assert small_base.signals["fired_on"] == ARM_ABSOLUTE


@pytest.mark.neo4j
def test_live_no_candidate_publishes_a_relative_change_taken_across_zero(live_moves):  # type: ignore[no-untyped-def]
    """The whole run, against §13.3's third gate.

    Before the gate reached this layer, **45 of 227** candidates carried one — including the
    plan's own worked example, `adjusted_ebitda_margin 2022Q2 → 2022Q3`, which shipped
    `delta_pct = −221.153846154` next to the `−11.5 pp` that is the number to read. The verifier
    only ever sees a draft; §6.10, §10 and §11 all read `signals`, so the assertion is on every
    candidate rather than on the one the plan names.
    """
    published = [c for c in live_moves.candidates if "delta_pct" in c.signals]
    across = [c for c in published if c.signals.get("crosses_zero") is True]

    assert across == []
    # Not vacuous: relative change is still the common reporting case.
    assert len(published) == 129

    (textbook,) = [
        c for c in live_moves.candidates
        if c.metric_ids == ("adjusted_ebitda_margin",)
        and c.anchor_period_keys == ("2022Q2", "2022Q3")
    ]
    assert "delta_pct" not in textbook.signals
    assert textbook.signals["delta_pp"] == -11.5
    assert RELATIVE_CHANGE_ACROSS_ZERO in textbook.warnings


@pytest.mark.neo4j
def test_live_no_percent_metric_publishes_a_percentage_of_a_percentage(live_moves, live_points):  # type: ignore[no-untyped-def]
    """§13.3's *"single most likely factual error"*, on the whole run.

    **70 of 227** candidates carried a `delta_pct` on a metric already denominated in percent, and
    36 of the 262 in the S3 census read past `|200%|` because `0.10 × median(|v|)` is a USD-shaped
    floor doing nothing on a percentage. `contribution_margin 2022Q3 → 2022Q4` never crossed zero,
    so only the unit check catches it.
    """
    series = build_series(canonicalize(live_points.records))
    unit_of = {
        metric_id: next((p.unit for p in s.valued_points()), "")
        for metric_id, s in series.items()
    }

    offenders = [
        c for c in live_moves.candidates
        if "delta_pct" in c.signals and unit_of[c.metric_ids[0]] == "percent"
    ]
    assert offenders == []

    (margin,) = [
        c for c in live_moves.candidates
        if c.metric_ids == ("contribution_margin",)
        and c.anchor_period_keys == ("2022Q3", "2022Q4")
    ]
    assert margin.signals["delta_pp"] == -6.5
    assert margin.signals["crosses_zero"] is False
    assert "delta_pct" not in margin.signals


@pytest.mark.neo4j
def test_live_one_candidate_exists_only_because_the_delta_was_rounded_before_the_bar(live_moves):  # type: ignore[no-untyped-def]
    """`round_delta`'s own blast radius, which is *not* the one `within_tolerance` records.

    `core/series.py`'s `within_tolerance` measured exactly one R8 case. Separately, 8 steps in
    this run land exactly on their arm's bar after rounding, and one of them clears only because
    of it: `adjusted_gross_margin 2024Q2 → 2024Q3` is `10.2% → 7.2%`, which subtracts to
    `−2.999999999999999` against a 3.0 pp bar. All 8 resolve in favour of firing, because every
    arm tests `>=`.
    """
    ids = {c.candidate_id for c in live_moves.candidates}

    assert 7.2 - 10.2 == -2.999999999999999  # the residue itself, not a transcription of it
    assert (
        "cand:metric-move:adjusted-gross-margin:opendoor:2024Q2_2024Q3:52a448345320" in ids
    )


@pytest.mark.neo4j
def test_live_the_aging_series_never_produces_a_candidate_across_its_four_quarter_hole(live_moves):  # type: ignore[no-untyped-def]
    """R10 on the real series: no `pct_>120d` candidate anchors `2021-12-31`."""
    anchors = {
        key
        for c in live_moves.candidates
        if c.metric_ids == ("pct_homes_on_market_gt_120_days",)
        for key in c.anchor_period_keys
    }

    assert "2021-12-31" not in anchors
    # `2022-12-31 → 2023-03-31` is `55% → 59%`, four points, under the 19 pp bar — so the first
    # aging candidate in the whole series is the `59% → 24%` collapse that ends `2023-06-30`.
    assert min(anchors) == "2023-03-31"


@pytest.mark.neo4j
def test_live_every_candidate_id_is_unique_and_rebuilt_identically_from_the_same_points(live_points):  # type: ignore[no-untyped-def]
    """§6.11's determinism, over the whole run rather than over one candidate."""
    first = detect_metric_moves_from_load(live_points, graph_run_id=GRAPH_RUN_ID).candidates
    second = detect_metric_moves(
        tuple(reversed(canonicalize(live_points.records))), graph_run_id=GRAPH_RUN_ID
    ).candidates

    ids = [c.candidate_id for c in first]
    assert len(set(ids)) == len(ids)
    assert ids == [c.candidate_id for c in second]
