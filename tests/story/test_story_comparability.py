"""S2 — §6.9's R1–R10, each with the case it permits and the case it refuses.

Every rule is driven twice: once through a pair it must let through and once through the pair
it exists to stop. Refusals are asserted by `rule` and `reason` — never by prose — because the
detail string is written for a human reader and a test that pinned it would fail on a reworded
sentence rather than on a changed decision.

The `neo4j`-marked tests at the bottom reproduce §6.9's own quantification against the live
graph, including the one place this module's answer differs from the plan's and why.
"""

from __future__ import annotations

from typing import Any

import pytest

from story.core.periods import PeriodShape, story_period
from story.core.series import (
    COHORT_VS_PERIOD_BASIS,
    CanonicalPoint,
    CanonicalStatus,
    ClaimKind,
    ComparabilityAuthority,
    Ok,
    Refuse,
    build_series,
    comparable,
    default_authority,
)
from story.stages.detection.canonicalization import canonicalize, load_observations


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


def series_of(*points: CanonicalPoint):  # type: ignore[no-untyped-def]
    """The one series every point belongs to. All the points must share a metric."""
    return build_series(points)[points[0].metric_id]


class StubRegistry:
    """A `ConceptRegistry`'s `formula_for`, and nothing else.

    Structural typing is what makes this possible: `comparable` never asks whether it has a real
    registry, only what formula was in force on a date. R6's straddle and undeclared cases need
    a boundary that does not exist in the shipped ontology, and inventing an ontology version to
    get one would test the loader instead of the rule.
    """

    def __init__(self, boundary: str) -> None:
        self._boundary = boundary

    def formula_for(self, metric_id: str, as_of: str | None = None):  # type: ignore[no-untyped-def]
        if as_of is None:
            return _Formula(f"{metric_id}_v2")
        return _Formula(f"{metric_id}_v1" if as_of < self._boundary else f"{metric_id}_v2")


class _Formula:
    def __init__(self, concept_id: str) -> None:
        self.concept_id = concept_id


def stub_authority(boundary: str, metric_id: str = "adjusted_ebitda") -> ComparabilityAuthority:
    return ComparabilityAuthority(
        versioned_metrics=frozenset({metric_id}), registry=StubRegistry(boundary))


@pytest.fixture(scope="module")
def authority() -> ComparabilityAuthority:
    """The shipped ontology's answer to R2, R6 and R9. No database, no model."""
    return default_authority()


# ---------------------------------------------------------------------------------------
# R1 SUBJECT
# ---------------------------------------------------------------------------------------


def test_two_points_about_the_same_subject_clear_the_subject_rule(authority):
    assert comparable(
        make_point(),
        make_point(period=story_period("2022-04-01", "2022-06-30"), value=218_000_000.0),
        authority=authority,
    ) == Ok()


def test_a_point_about_another_subject_is_refused_by_the_subject_rule(authority):
    result = comparable(
        make_point(), make_point(subject_entity_id="zillow"), authority=authority)

    assert isinstance(result, Refuse)
    assert (result.rule, result.reason) == ("R1", "SUBJECT_MISMATCH")
    assert "zillow" in result.detail and "opendoor" in result.detail


# ---------------------------------------------------------------------------------------
# R2 METRIC
# ---------------------------------------------------------------------------------------


def test_a_level_claim_across_two_metrics_is_refused_without_a_declared_divergence(authority):
    result = comparable(
        make_point(),
        make_point(metric_id="adjusted_gross_profit"),
        claim=ClaimKind.LEVEL,
        authority=authority,
    )

    assert (result.rule, result.reason) == ("R2", "METRIC_MISMATCH")


def test_two_metrics_sharing_a_mutually_distinct_group_may_be_a_divergence_pair(authority):
    """The direction reads backwards on purpose: the ontology saying they must never be merged
    is what makes them comparable *as a divergence*. This is §6.3's F3."""
    result = comparable(
        make_point(metric_id="adjusted_gross_margin", unit="percent", scale="units",
                   currency=None, value=3.3),
        make_point(metric_id="gaap_gross_margin", unit="percent", scale="units",
                   currency=None, value=-12.6),
        claim=ClaimKind.DIVERGENCE,
        authority=authority,
    )

    assert isinstance(result, Ok)


def test_two_metrics_appearing_together_in_one_formula_may_be_a_divergence_pair(authority):
    """`contribution_profit_v1`'s components are adjusted gross profit, direct selling costs
    and holding costs; the first two share no `mutually_distinct_groups` group."""
    result = comparable(
        make_point(metric_id="adjusted_gross_profit"),
        make_point(metric_id="direct_selling_costs"),
        claim=ClaimKind.DIVERGENCE,
        authority=authority,
    )

    assert isinstance(result, Ok)


def test_two_metrics_in_different_groups_are_unrelated_even_as_a_divergence(authority):
    result = comparable(
        make_point(metric_id="homes_sold", unit="homes"),
        make_point(metric_id="gaap_gross_margin", unit="percent"),
        claim=ClaimKind.DIVERGENCE,
        authority=authority,
    )

    assert (result.rule, result.reason) == ("R2", "UNRELATED_METRICS")
    assert "home_counts" in result.detail and "margin_measures" in result.detail


# ---------------------------------------------------------------------------------------
# R3 SHAPE
# ---------------------------------------------------------------------------------------


def test_two_quarters_clear_the_shape_rule(authority):
    assert isinstance(
        comparable(
            make_point(),
            make_point(period=story_period("2022-04-01", "2022-06-30"), value=218_000_000.0),
            authority=authority,
        ),
        Ok,
    )


def test_a_quarter_compared_with_a_year_to_date_window_is_refused(authority):
    """§13.4's measured example: `adjusted_ebitda` ending `2022-09-30` is `−$211M` for Q3 and
    `+$183M` for the nine-month year-to-date, and both windows end on the same day."""
    result = comparable(
        make_point(),
        make_point(period=story_period("2022-01-01", "2022-09-30"), value=183_000_000.0),
        authority=authority,
    )

    assert (result.rule, result.reason) == ("R3", "UNCOMPARABLE_SHAPE")
    assert "quarter" in result.detail and "ytd_9m" in result.detail


def test_a_cross_year_window_is_refused_by_the_shape_rule(authority):
    result = comparable(
        make_point(),
        make_point(period=story_period("2022-07-01", "2023-03-31")),
        authority=authority,
    )

    assert (result.rule, result.reason) == ("R3", "UNCOMPARABLE_SHAPE")
    assert "2022-07-01_2023-03-31" in result.detail


def test_a_duration_compared_with_an_instant_is_refused_by_the_shape_rule(authority):
    result = comparable(
        make_point(),
        make_point(period=story_period(None, None, "2022-09-30")),
        authority=authority,
    )

    assert (result.rule, result.reason) == ("R3", "UNCOMPARABLE_SHAPE")


# ---------------------------------------------------------------------------------------
# R4 UNIT and R5 CURRENCY
# ---------------------------------------------------------------------------------------


def test_two_readings_in_different_units_are_refused(authority):
    result = comparable(
        make_point(period=story_period("2022-04-01", "2022-06-30")),
        make_point(unit="percent"),
        authority=authority,
    )

    assert (result.rule, result.reason) == ("R4", "UNIT_MISMATCH")


def test_scale_is_deliberately_not_checked_because_the_value_is_already_scale_applied(
    authority,
):
    """§6.9 R4 states it in capitals. A thousands printing and a millions printing of the same
    quarter are one reading, which is exactly what §6.1 step 2 collapses."""
    result = comparable(
        make_point(scale="thousands", value=-211_000_000.0),
        make_point(period=story_period("2022-04-01", "2022-06-30"), scale="millions",
                   value=218_000_000.0),
        authority=authority,
    )

    assert isinstance(result, Ok)


def test_two_readings_in_different_currencies_are_refused(authority):
    result = comparable(
        make_point(period=story_period("2022-04-01", "2022-06-30")),
        make_point(currency="EUR"),
        authority=authority,
    )

    assert (result.rule, result.reason) == ("R5", "CURRENCY_MISMATCH")


def test_two_readings_that_both_carry_no_currency_agree(authority):
    """`currency` is on 997 of 2,704 observations (C2); treating absence as disagreement would
    refuse every percentage and every count in the run."""
    result = comparable(
        make_point(unit="percent", scale="units", currency=None, value=3.3),
        make_point(period=story_period("2022-04-01", "2022-06-30"), unit="percent",
                   scale="units", currency=None, value=13.2),
        authority=authority,
    )

    assert isinstance(result, Ok)


# ---------------------------------------------------------------------------------------
# R6 FORMULA
# ---------------------------------------------------------------------------------------


def test_two_quarters_under_one_formula_version_clear_the_formula_rule(authority):
    result = comparable(
        make_point(metric_id="adjusted_gross_profit",
                   period=story_period("2022-07-01", "2022-09-30")),
        make_point(metric_id="adjusted_gross_profit",
                   period=story_period("2022-04-01", "2022-06-30"), value=1.0),
        authority=authority,
    )

    assert isinstance(result, Ok)


def test_the_quarters_either_side_of_the_adjusted_gross_profit_boundary_are_refused(authority):
    """The comparison the 2022 crisis story most wants to make, and the one R6 exists for."""
    result = comparable(
        make_point(metric_id="adjusted_gross_profit",
                   period=story_period("2022-01-01", "2022-03-31")),
        make_point(metric_id="adjusted_gross_profit",
                   period=story_period("2021-10-01", "2021-12-31"), value=1.0),
        authority=authority,
    )

    assert (result.rule, result.reason) == ("R6", "FORMULA_VERSION_MISMATCH")
    assert "adjusted_gross_profit_v1" in result.detail
    assert "adjusted_gross_profit_v2" in result.detail


def test_a_period_before_every_declared_formula_window_is_refused_as_undeclared(authority):
    """`adjusted_gross_profit`'s formulas begin at 2020-01-01 and the run holds FY2018, FY2019
    and 2019Q4 values. C4 forbids a silent fallback; clamping to v1 would assert that the
    restructuring adjustment applied in 2018, which the ontology does not say."""
    result = comparable(
        make_point(metric_id="adjusted_gross_profit",
                   period=story_period("2019-01-01", "2019-12-31")),
        make_point(metric_id="adjusted_gross_profit",
                   period=story_period("2020-01-01", "2020-12-31"), value=1.0),
        authority=authority,
    )

    assert (result.rule, result.reason) == ("R6", "FORMULA_VERSION_UNDECLARED")
    assert "FY2019" in result.detail


def test_a_duration_straddling_a_formula_boundary_is_refused():
    """Synthetic: the shipped ontology's only boundary falls on a year end, so no real window
    straddles it. A mid-quarter boundary is what a future redefinition would look like."""
    result = comparable(
        make_point(period=story_period("2022-01-01", "2022-03-31")),
        make_point(period=story_period("2022-04-01", "2022-06-30"), value=1.0),
        authority=stub_authority("2022-02-15"),
    )

    assert (result.rule, result.reason) == ("R6", "FORMULA_VERSION_STRADDLES")
    assert "2022-01-01" in result.detail and "2022-03-31" in result.detail


def test_an_instant_resolves_its_formula_version_on_its_own_date():
    """§6.9 records R6 as *undefined* for instants and asks for a decision. The decision is that
    an instant resolves on `instant_date`, because R6's own sentence is "resolution is BY
    OBSERVATION DATE" and an instant is nothing but one."""
    authority = stub_authority("2022-02-15", metric_id="housing_inventory_homes")
    before = make_point(metric_id="housing_inventory_homes", unit="homes", currency=None,
                        period=story_period(None, None, "2021-12-31"), value=17_013.0)
    after = make_point(metric_id="housing_inventory_homes", unit="homes", currency=None,
                       period=story_period(None, None, "2022-03-31"), value=12_000.0)

    assert isinstance(comparable(before, before, authority=authority), Ok)
    result = comparable(after, before, authority=authority)
    assert (result.rule, result.reason) == ("R6", "FORMULA_VERSION_MISMATCH")


def test_a_metric_with_no_declared_formula_has_no_formula_to_disagree_about(authority):
    """`adjusted_ebitda` declares no `MetricFormulaVersion`, so R6 has nothing to rule on."""
    result = comparable(
        make_point(),
        make_point(period=story_period("2020-04-01", "2020-06-30"), value=-22_000_000.0),
        authority=authority,
    )

    assert isinstance(result, Ok)


def test_a_cross_metric_divergence_is_not_refused_for_naming_two_different_version_ids(
    authority,
):
    """Correction to R6 as written. Every metric's formula versions carry their own concept
    ids, so *"resolve_version(metric, period_end) must agree"* taken literally across two
    metrics can never be satisfied — it would refuse §6.3's F3, the plan's own recommended
    spike. R6 is evaluated per metric across both dates instead."""
    result = comparable(
        make_point(metric_id="adjusted_gross_margin", unit="percent", scale="units",
                   currency=None, value=3.3),
        make_point(metric_id="gaap_gross_margin", unit="percent", scale="units",
                   currency=None, value=-12.6),
        claim=ClaimKind.DIVERGENCE,
        authority=authority,
    )

    assert isinstance(result, Ok)


# ---------------------------------------------------------------------------------------
# R7 CANONICAL
# ---------------------------------------------------------------------------------------


def test_a_conflicted_slot_has_no_value_to_compare(authority):
    result = comparable(
        make_point(),
        make_point(period=story_period("2022-04-01", "2022-06-30"),
                   status=CanonicalStatus.CONFLICT, value=None),
        authority=authority,
    )

    assert (result.rule, result.reason) == ("R7", "NOT_CANONICAL")
    assert "conflict" in result.detail


def test_a_slot_resolved_by_majority_is_canonical_enough_to_compare(authority):
    result = comparable(
        make_point(),
        make_point(period=story_period("2022-04-01", "2022-06-30"),
                   status=CanonicalStatus.RESOLVED_BY_MAJORITY, value=218_000_000.0),
        authority=authority,
    )

    assert isinstance(result, Ok)


# ---------------------------------------------------------------------------------------
# R8 TOLERANCE
# ---------------------------------------------------------------------------------------


def test_a_movement_smaller_than_the_printed_unit_is_rounding_and_not_news(authority):
    earlier = make_point(period=story_period("2022-04-01", "2022-06-30"), value=25_000_000.0)
    later = make_point(value=25_400_000.0)

    result = comparable(later, earlier, claim=ClaimKind.MOVEMENT, authority=authority,
                        series=series_of(earlier, later))

    assert (result.rule, result.reason) == ("R8", "WITHIN_PRESENTATION_TOLERANCE")


def test_a_movement_larger_than_the_printed_unit_clears_the_tolerance_rule(authority):
    earlier = make_point(period=story_period("2022-04-01", "2022-06-30"), value=218_000_000.0)
    later = make_point(value=-211_000_000.0)

    result = comparable(later, earlier, claim=ClaimKind.MOVEMENT, authority=authority,
                        series=series_of(earlier, later))

    assert isinstance(result, Ok)


def test_the_tolerance_rule_does_not_apply_to_a_level_claim(authority):
    """A level claim asserts two readings, not a step between them, so two nearly equal
    readings are a perfectly good thing to state."""
    earlier = make_point(period=story_period("2022-04-01", "2022-06-30"), value=25_000_000.0)
    later = make_point(value=25_400_000.0)

    assert isinstance(
        comparable(later, earlier, claim=ClaimKind.LEVEL, authority=authority), Ok)


def test_the_coarser_of_the_two_printings_sets_the_tolerance_floor(authority):
    """`max(tol(A), tol(B))`: a thousands reading compared against a millions one is bounded by
    the millions one, because that is the printing that lost the digits."""
    earlier = make_point(period=story_period("2022-04-01", "2022-06-30"), scale="thousands",
                         value=25_000_000.0)
    later = make_point(scale="millions", value=25_900_000.0)

    result = comparable(later, earlier, claim=ClaimKind.MOVEMENT, authority=authority,
                        series=series_of(earlier, later))

    assert (result.rule, result.reason) == ("R8", "WITHIN_PRESENTATION_TOLERANCE")


# ---------------------------------------------------------------------------------------
# R9 BASIS
# ---------------------------------------------------------------------------------------


def test_the_cohort_set_is_derived_from_the_ontology_and_names_the_two_the_plan_names(
    authority,
):
    """Derived, not listed: a metric is a cohort measure when one of its formula's adjustment
    components says so. A third would arrive here without a code change — and this assertion
    would be the thing that told us."""
    assert authority.cohort_metrics == frozenset(
        {"contribution_profit", "contribution_profit_after_interest"})


def test_a_cohort_measure_differenced_against_a_period_measure_carries_a_basis_warning(
    authority,
):
    """The pair R1–R8 as first drafted permitted silently, and the one §6.6's D3 ships."""
    result = comparable(
        make_point(metric_id="contribution_profit", value=-444_000_000.0),
        make_point(metric_id="adjusted_gross_profit", value=-446_000_000.0),
        claim=ClaimKind.DIVERGENCE,
        authority=authority,
    )

    assert isinstance(result, Ok)
    assert [warning.code for warning in result.warnings] == [COHORT_VS_PERIOD_BASIS]
    assert "contribution_profit" in result.warnings[0].detail
    assert "adjusted_gross_profit" in result.warnings[0].detail


def test_two_cohort_measures_differenced_against_each_other_need_no_basis_warning(authority):
    result = comparable(
        make_point(metric_id="contribution_profit", value=-444_000_000.0),
        make_point(metric_id="contribution_profit_after_interest", value=-460_000_000.0),
        claim=ClaimKind.DIVERGENCE,
        authority=authority,
    )

    assert result == Ok()


def test_a_movement_in_one_cohort_measure_needs_no_basis_warning(authority):
    """R9 is about differencing two *bases*, not about a metric's own series."""
    earlier = make_point(metric_id="contribution_profit",
                         period=story_period("2022-04-01", "2022-06-30"), value=200_000_000.0)
    later = make_point(metric_id="contribution_profit", value=-244_000_000.0)

    result = comparable(later, earlier, claim=ClaimKind.MOVEMENT, authority=authority,
                        series=series_of(earlier, later))

    assert result == Ok()


# ---------------------------------------------------------------------------------------
# R10 ADJACENCY
# ---------------------------------------------------------------------------------------


def test_two_consecutive_quarters_clear_the_adjacency_rule(authority):
    earlier = make_point(period=story_period("2022-04-01", "2022-06-30"), value=218_000_000.0)
    later = make_point(value=-211_000_000.0)

    result = comparable(later, earlier, claim=ClaimKind.MOVEMENT, authority=authority,
                        series=series_of(earlier, later))

    assert isinstance(result, Ok)


def test_a_step_claim_across_a_four_quarter_hole_is_refused_as_a_series_gap(authority):
    """§6.9's own example: `pct_>120d` reads a **+47 pp** "quarterly jump" between the run's
    `2021-12-31` (8%) and `2022-12-31` (55%) — adjacent rows, four quarters of nothing."""
    earlier = make_point(metric_id="pct_homes_on_market_gt_120_days", unit="percent",
                         scale="units", currency=None,
                         period=story_period(None, None, "2021-12-31"), value=8.0)
    later = make_point(metric_id="pct_homes_on_market_gt_120_days", unit="percent",
                       scale="units", currency=None,
                       period=story_period(None, None, "2022-12-31"), value=55.0)

    result = comparable(later, earlier, claim=ClaimKind.MOVEMENT, authority=authority,
                        series=series_of(earlier, later))

    assert (result.rule, result.reason) == ("R10", "SERIES_GAP")
    assert "12 months" in result.detail


def test_a_step_claim_over_a_quarter_the_series_holds_is_refused_as_not_adjacent(authority):
    first = make_point(period=story_period("2022-01-01", "2022-03-31"), value=176_000_000.0)
    middle = make_point(period=story_period("2022-04-01", "2022-06-30"), value=218_000_000.0)
    last = make_point(value=-211_000_000.0)

    result = comparable(last, first, claim=ClaimKind.MOVEMENT, authority=authority,
                        series=series_of(first, middle, last))

    assert (result.rule, result.reason) == ("R10", "SERIES_NOT_ADJACENT")
    assert "2022Q2" in result.detail


def test_a_step_claim_with_no_series_is_refused_rather_than_allowed_to_skip_the_rule(
    authority,
):
    """A rule that can be dropped by omitting an argument is not a rule."""
    result = comparable(
        make_point(),
        make_point(period=story_period("2022-04-01", "2022-06-30"), value=218_000_000.0),
        claim=ClaimKind.MOVEMENT,
        authority=authority,
    )

    assert (result.rule, result.reason) == ("R10", "SERIES_UNAVAILABLE")


def test_a_step_claim_handed_another_metrics_series_is_refused(authority):
    other = make_point(metric_id="contribution_profit", value=1.0)

    result = comparable(
        make_point(),
        make_point(period=story_period("2022-04-01", "2022-06-30"), value=218_000_000.0),
        claim=ClaimKind.MOVEMENT,
        authority=authority,
        series=series_of(other),
    )

    assert (result.rule, result.reason) == ("R10", "SERIES_UNAVAILABLE")


def test_the_adjacency_rule_does_not_apply_to_a_level_claim(authority):
    """§6.3's F4 — 16,873 homes to 3,558, "−78.9% in three quarters" — is a level claim with a
    stated span, and R10 would be wrong to refuse it."""
    earlier = make_point(metric_id="housing_inventory_homes", unit="homes", currency=None,
                         scale="units", period=story_period(None, None, "2022-09-30"),
                         value=16_873.0)
    later = make_point(metric_id="housing_inventory_homes", unit="homes", currency=None,
                       scale="units", period=story_period(None, None, "2023-06-30"),
                       value=3_558.0)

    assert isinstance(
        comparable(later, earlier, claim=ClaimKind.LEVEL, authority=authority), Ok)


def test_an_acceleration_claim_is_held_to_the_same_adjacency_as_a_movement(authority):
    earlier = make_point(metric_id="pct_homes_on_market_gt_120_days", unit="percent",
                         scale="units", currency=None,
                         period=story_period(None, None, "2021-12-31"), value=8.0)
    later = make_point(metric_id="pct_homes_on_market_gt_120_days", unit="percent",
                       scale="units", currency=None,
                       period=story_period(None, None, "2022-12-31"), value=55.0)

    result = comparable(later, earlier, claim=ClaimKind.ACCELERATION, authority=authority,
                        series=series_of(earlier, later))

    assert (result.rule, result.reason) == ("R10", "SERIES_GAP")


# ---------------------------------------------------------------------------------------
# The shape of the answer
# ---------------------------------------------------------------------------------------


def test_the_rules_are_applied_in_the_order_the_plan_numbers_them(authority):
    """A pair that breaks R1 and R4 reports R1: the first rule to bite is the one to fix."""
    result = comparable(
        make_point(), make_point(subject_entity_id="zillow", unit="percent"),
        authority=authority)

    assert result.rule == "R1"


def test_a_refusal_is_a_value_and_never_an_exception(authority):
    """Every failure mode of §6.9 in one list, driven for its truthiness rather than its type."""
    refusals = [
        comparable(make_point(), make_point(subject_entity_id="zillow"), authority=authority),
        comparable(make_point(), make_point(metric_id="homes_sold"), authority=authority),
        comparable(make_point(),
                   make_point(period=story_period("2022-01-01", "2022-09-30")),
                   authority=authority),
    ]

    assert all(not refusal for refusal in refusals)
    assert all(isinstance(refusal, Refuse) for refusal in refusals)
    assert bool(Ok()) is True


# ---------------------------------------------------------------------------------------
# Live — §6.9's quantification, recomputed
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_series():  # type: ignore[no-untyped-def]
    from story.context import build_story_context
    from story.stages.retrieval.graph_tools import BoundedGraphRetriever

    context = build_story_context()
    health = context.executor.verify_connectivity()
    if not health.ok:
        context.close()
        pytest.skip(f"neo4j unavailable: {health.status} — {health.detail}")
    try:
        load = load_observations(BoundedGraphRetriever(context.executor))
        yield build_series(canonicalize(load.records))
    finally:
        context.close()


@pytest.mark.neo4j
def test_live_adjusted_gross_profit_is_the_only_metric_r6_bites_on(live_series, authority):  # type: ignore[no-untyped-def]
    """Seven metrics declare a formula version between them — eight versions, because
    `adjusted_gross_profit` declares two — so only one metric can have a
    pair of periods R6 separates. Measured off the graph's own periods rather than off the
    ontology alone, because a second version that no observation falls either side of would be
    a rule that never fires."""
    multi_version = {
        metric_id
        for metric_id in live_series
        if len(
            {
                authority.resolve_version(metric_id, point.period.anchor_date)
                for point in live_series[metric_id].points
            }
            - {None}
        )
        > 1
    }

    assert multi_version == {"adjusted_gross_profit"}
    assert len(authority.versioned_metrics) == 7


@pytest.mark.neo4j
def test_live_the_formula_boundary_forbids_one_hundred_and_ninety_eight_of_three_hundred_and_eighty_three_pairs(
    live_series, authority  # type: ignore[no-untyped-def]
):
    """§6.9 quantifies this as **185 of 383 (48.3%)**. The 383 reproduces exactly; the forbidden
    count does not, and the cause is recorded rather than tuned away.

    §6.9 assigns `adjusted_gross_profit`'s three pre-2020 slots (`FY2018`, `FY2019`, `2019Q4`;
    5 observations) to v1 — its census reads *"v1 `2020-01-01..2021-12-31` (17 slots, 47
    observations)"*, which is internally inconsistent, because those three slots end before
    `2020-01-01` and cannot be inside the window the same sentence names. Clamping them to v1
    reproduces 185 exactly; refusing them as `FORMULA_VERSION_UNDECLARED` gives 198. This module
    refuses, for the reason C4 gives everywhere else: the ontology declares no formula in force
    before 2020 and inventing one is a silent fallback.
    """
    import itertools

    points = live_series["adjusted_gross_profit"].points
    same_shape = forbidden = 0
    reasons: dict[str, int] = {}
    for left, right in itertools.combinations(points, 2):
        if (left.period.shape is not right.period.shape
                or left.period.shape is PeriodShape.OTHER):
            continue
        same_shape += 1
        result = comparable(left, right, claim=ClaimKind.LEVEL, authority=authority)
        if isinstance(result, Refuse):
            forbidden += 1
            reasons[result.reason] = reasons.get(result.reason, 0) + 1

    assert len(points) == 46
    assert same_shape == 383
    assert forbidden == 198
    assert reasons == {"FORMULA_VERSION_MISMATCH": 160, "FORMULA_VERSION_UNDECLARED": 38}


@pytest.mark.neo4j
def test_live_the_five_boundary_crossing_quarter_comparisons_are_the_ones_r6_refuses(
    live_series, authority  # type: ignore[no-untyped-def]
):
    """§6.9: *"1 adjacent-quarter comparison (2021Q4→2022Q1) and 4 year-over-year"*. Both
    reproduce as `FORMULA_VERSION_MISMATCH`; the two extra pairs this module refuses are the
    ones touching `2019Q4`, and they refuse under the undeclared code instead."""
    import itertools

    from story.core.periods import months_between

    series = live_series["adjusted_gross_profit"]
    quarters = series.valued_points(PeriodShape.QUARTER)
    adjacent, yearly, undeclared = [], [], []
    for earlier, later in itertools.combinations(quarters, 2):
        result = comparable(later, earlier, claim=ClaimKind.LEVEL, authority=authority)
        if not isinstance(result, Refuse):
            continue
        gap = months_between(earlier.period.anchor_date, later.period.anchor_date)
        pair = (earlier.period.key, later.period.key)
        if result.reason == "FORMULA_VERSION_UNDECLARED":
            undeclared.append(pair)
        elif gap == 3:
            adjacent.append(pair)
        elif gap == 12:
            yearly.append(pair)

    assert adjacent == [("2021Q4", "2022Q1")]
    assert yearly == [("2021Q1", "2022Q1"), ("2021Q2", "2022Q2"), ("2021Q3", "2022Q3"),
                      ("2021Q4", "2022Q4")]
    assert all("2019Q4" in pair for pair in undeclared)


@pytest.mark.neo4j
def test_live_the_plus_forty_seven_point_quarterly_jump_is_refused_by_the_adjacency_rule(
    live_series, authority  # type: ignore[no-untyped-def]
):
    series = live_series["pct_homes_on_market_gt_120_days"]
    earlier, later = series.point("2021-12-31"), series.point("2022-12-31")

    assert (earlier.value, later.value) == (8.0, 55.0)
    assert series.points_between(later, earlier) == ()

    result = comparable(later, earlier, claim=ClaimKind.MOVEMENT, authority=authority,
                        series=series)

    assert (result.rule, result.reason) == ("R10", "SERIES_GAP")


@pytest.mark.neo4j
def test_live_the_aging_inventory_step_the_run_does_hold_is_permitted(live_series, authority):  # type: ignore[no-untyped-def]
    """§6.3's F5: `2024-09-30 = 23%` → `2024-12-31 = 46%`, one instant step apart."""
    series = live_series["pct_homes_on_market_gt_120_days"]
    earlier, later = series.point("2024-09-30"), series.point("2024-12-31")

    assert (earlier.value, later.value) == (23.0, 46.0)
    assert isinstance(
        comparable(later, earlier, claim=ClaimKind.MOVEMENT, authority=authority,
                   series=series),
        Ok,
    )


@pytest.mark.neo4j
def test_live_the_recommended_spike_pairs_are_comparable(live_series, authority):  # type: ignore[no-untyped-def]
    """F1's movement and F3's divergence, on the real canonical points."""
    ebitda = live_series["adjusted_ebitda"]
    movement = comparable(
        ebitda.point("2022Q3"), ebitda.point("2022Q2"), claim=ClaimKind.MOVEMENT,
        authority=authority, series=ebitda)
    divergence = comparable(
        live_series["adjusted_gross_margin"].point("2022Q3"),
        live_series["gaap_gross_margin"].point("2022Q3"),
        claim=ClaimKind.DIVERGENCE, authority=authority)

    assert movement == Ok()
    assert divergence == Ok()


@pytest.mark.neo4j
def test_live_the_shipping_contribution_profit_pair_carries_the_cohort_basis_warning(
    live_series, authority  # type: ignore[no-untyped-def]
):
    result = comparable(
        live_series["contribution_profit"].point("2022Q3"),
        live_series["adjusted_gross_profit"].point("2022Q3"),
        claim=ClaimKind.DIVERGENCE,
        authority=authority,
    )

    assert isinstance(result, Ok)
    assert [warning.code for warning in result.warnings] == [COHORT_VS_PERIOD_BASIS]
