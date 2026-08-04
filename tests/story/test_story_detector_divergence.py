"""S3-C — D4 `cross_metric_divergence`, driven through the eight properties it must have.

Offline by default. The plan's own §6.2 table is transcribed at the top and the offline tests
build canonical points from it, so an offline failure says *"the detector disagrees with the
plan"* rather than *"the detector disagrees with itself"*. The `neo4j`-marked tests at the
bottom recompute the same numbers from the live graph and record the one place this module's
answer differs from §6.6 D4's — the pre-2020 quarter R6 refuses.

Refusals are asserted by `rule` and `reason`, never by their detail sentence, for §6.9's
reason: the detail is written for a human and a test that pinned it would fail on a rewording
rather than on a changed decision.
"""

from __future__ import annotations

import random
from typing import Any, Sequence

import pytest
from pydantic import ValidationError

from story.core.models import StoryCandidate
from story.core.periods import PeriodShape, story_period
from story.core.series import (
    COHORT_VS_PERIOD_BASIS,
    CanonicalPoint,
    CanonicalStatus,
    ComparabilityAuthority,
    build_series,
    default_authority,
)
from story.stages.detection.canonicalization import canonicalize, load_observations
from story.stages.detection.cross_metric_divergence import (
    DETECTOR_ID,
    DIVERGENCE_PAIRS,
    LOW_VARIANCE_PRESENTATION_NOISE,
    MIN_POPULATION,
    POPULATION_EXCLUDES_PERIODS,
    POPULATION_TOO_SMALL,
    SERIES_ABSENT,
    SIGN_CONVENTION_MISMATCH,
    STORY_TYPE,
    UNRESOLVED_PERIOD_RULE,
    DivergencePair,
    RelationKind,
    definitional_relation,
    detect_cross_metric_divergence,
)
from story.stages.detection.detector_config import (
    DIRECTION_DECREASE,
    DIRECTION_INCREASE,
    DIRECTION_UNCHANGED,
    SIGN_CONVENTION_UNVERIFIED,
    MetricPolarity,
    ValueSign,
    value_sign_of,
)

GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"

#: §6.2's table, transcribed from `plans/llm-agent/V1_STORY_AGENT.md` — the same three margin
#: rows `tests/story/test_story_series.py` transcribes, kept here so an offline run needs no
#: database and no fixture file. 26 gapless quarters, `2019Q4 → 2026Q1`.
QUARTERS = (
    "2019Q4", "2020Q1", "2020Q2", "2020Q3", "2020Q4", "2021Q1", "2021Q2", "2021Q3", "2021Q4",
    "2022Q1", "2022Q2", "2022Q3", "2022Q4", "2023Q1", "2023Q2", "2023Q3", "2023Q4", "2024Q1",
    "2024Q2", "2024Q3", "2024Q4", "2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1",
)
PLAN_SERIES: dict[str, tuple[float, ...]] = {
    "gaap_gross_margin": (5.9, 7.3, 7.4, 10.6, 15.4, 13.0, 13.4, 8.9, 7.1, 10.4, 11.6, -12.6,
                          2.5, 5.4, 7.5, 9.8, 8.3, 9.7, 8.5, 7.6, 7.8, 8.6, 8.2, 7.2, 7.7,
                          10.0),
    "adjusted_gross_margin": (5.6, 7.1, 6.9, 9.8, 15.4, 13.0, 13.5, 10.3, 7.3, 9.9, 13.2, 3.3,
                              -3.2, -3.3, 0.4, 8.6, 7.6, 8.8, 10.2, 7.2, 6.9, 8.7, 8.6, 7.0,
                              6.1, 9.3),
    "contribution_margin": (1.5, 3.1, 2.7, 5.9, 12.6, 10.2, 10.8, 7.5, 4.0, 6.4, 10.1, -0.7,
                            -7.2, -7.7, -4.6, 4.4, 3.4, 4.8, 6.3, 3.8, 3.5, 4.7, 4.4, 2.2, 1.0,
                            4.4),
}

#: F3, as §6.6 D4 and §6.3 state it, and as this module measures it. The plan's population is
#: 26 quarters because it does not apply R6; `2019Q4` ends before every formula version's
#: `valid_from: 2020-01-01`, so `comparable` refuses it and the population is 25.
PLAN_QUARTERS = 26
PLAN_Z = 4.02
MEASURED_QUARTERS = 25
MEASURED_Z = 3.9454
MEASURED_MEAN = -0.348
MEASURED_PSTDEV = 4.1182

_QUARTER_WINDOWS = {
    "Q1": ("-01-01", "-03-31"),
    "Q2": ("-04-01", "-06-30"),
    "Q3": ("-07-01", "-09-30"),
    "Q4": ("-10-01", "-12-31"),
}


def _point(
    metric_id: str, period: Any, value: float | None, **overrides: Any
) -> CanonicalPoint:
    """One canonical point, defaulted to a percentage margin reading."""
    fields: dict[str, Any] = dict(
        metric_id=metric_id,
        period=period,
        subject_entity_id="opendoor",
        status=CanonicalStatus.OK,
        value=value,
        unit="percent",
        scale="units",
        currency=None,
        representative_observation_id=f"obs:{metric_id}:{period.key}",
        supporting_observation_ids=(f"obs:{metric_id}:{period.key}",),
        n_docs=2,
        distinct_values=1,
    )
    fields.update(overrides)
    return CanonicalPoint(**fields)


def quarter_point(metric_id: str, key: str, value: float, **overrides: Any) -> CanonicalPoint:
    year, label = key[:4], key[4:]
    start, end = _QUARTER_WINDOWS[label]
    return _point(metric_id, story_period(year + start, year + end), value, **overrides)


def year_point(metric_id: str, year: int, value: float, **overrides: Any) -> CanonicalPoint:
    return _point(
        metric_id, story_period(f"{year}-01-01", f"{year}-12-31"), value, **overrides)


def usd_point(metric_id: str, key: str, value: float, **overrides: Any) -> CanonicalPoint:
    """A USD point filed in a millions table — the scale that sets a $1M tolerance."""
    fields: dict[str, Any] = dict(unit="USD", scale="millions", currency="USD")
    fields.update(overrides)
    return quarter_point(metric_id, key, value, **fields)


def wobble(index: int) -> float:
    """A deterministic ±$20M swing, so a constructed gap history is a distribution.

    **Three fixtures below used to build a perfectly flat gap and push one quarter out of it.**
    That is exactly the single-point excursion R4b's variance floor now refuses — the σ of such
    a population is the anchor's own excursion and nothing else — so they were testing R6 and R9
    through a population the detector should never have scored. The wobble gives the gap five
    distinct levels; its σ is ≈ $14M against the $2M floor a millions-scale USD pair carries,
    with or without the anchor.
    """
    return 1.0e7 * ((index % 5) - 2)


def plan_series(*metric_ids: str, quarters: Sequence[str] = QUARTERS) -> dict[str, Any]:
    """`build_series` over §6.2's transcription, for the metrics named."""
    points = [
        quarter_point(metric_id, key, PLAN_SERIES[metric_id][QUARTERS.index(key)])
        for metric_id in metric_ids
        for key in quarters
    ]
    return build_series(points)


@pytest.fixture(scope="module")
def authority():  # type: ignore[no-untyped-def]
    """The shipped ontology's answer to R2, R6 and R9. No database, no model."""
    return default_authority()


@pytest.fixture(scope="module")
def margin_result(authority):  # type: ignore[no-untyped-def]
    return detect_cross_metric_divergence(
        plan_series("adjusted_gross_margin", "gaap_gross_margin"),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("adjusted_gross_margin", "gaap_gross_margin"),),
    )


def only(candidates: Sequence[StoryCandidate], period_key: str) -> StoryCandidate:
    matching = [c for c in candidates if c.anchor_period_keys == (period_key,)]
    assert len(matching) == 1, [c.anchor_period_keys for c in candidates]
    return matching[0]


# ---------------------------------------------------------------------------------------
# The rule, and the live candidate it must produce
# ---------------------------------------------------------------------------------------


def test_the_wedge_between_adjusted_and_gaap_gross_margin_fires_at_the_2022q3_spike(
    margin_result,  # type: ignore[no-untyped-def]
):
    """§6.3's F3: AGM 3.3% against GGM −12.6%, a gap of +15.9 pp, from §6.2's own table."""
    candidate = only(margin_result.candidates, "2022Q3")
    signals = candidate.signals

    assert candidate.detector_id == DETECTOR_ID
    assert candidate.story_type == STORY_TYPE
    assert candidate.metric_ids == ("adjusted_gross_margin", "gaap_gross_margin")
    assert (signals["left_value"], signals["right_value"]) == (3.3, -12.6)
    assert round(float(signals["gap"]), 4) == 15.9
    assert round(float(signals["z"]), 4) == MEASURED_Z
    assert signals["population_size"] == MEASURED_QUARTERS
    assert round(float(signals["gap_mean"]), 4) == MEASURED_MEAN
    assert round(float(signals["gap_pstdev"]), 4) == MEASURED_PSTDEV


def test_the_2022q3_wedge_is_the_largest_of_the_three_the_pair_produces(margin_result):  # type: ignore[no-untyped-def]
    """§6.3: *"Next largest is 2023Q1 at z = −2.07."* Over the 25-quarter population it is
    −2.0281, and 2023Q2 clears the 1.5 gate as well."""
    by_period = {
        c.anchor_period_keys[0]: round(float(c.signals["z"]), 4)
        for c in margin_result.candidates
    }

    assert by_period == {"2022Q3": MEASURED_Z, "2023Q1": -2.0281, "2023Q2": -1.6396}


def test_the_pre_2020_quarter_is_refused_by_the_formula_window_rule_and_not_by_this_module(
    margin_result,  # type: ignore[no-untyped-def]
):
    """The measured difference from §6.6 D4's "26".

    Every versioned metric in this ontology declares `valid_from: 2020-01-01`, so `2019Q4`
    lies in no declared window and `comparable` refuses it. C4 forbids clamping it to the
    earliest version, which would assert a definition the ontology does not state.
    """
    refused = [r for r in margin_result.refusals if r.period_key == "2019Q4"]

    assert [(r.rule, r.reason) for r in refused] == [("R6", "FORMULA_VERSION_UNDECLARED")]
    assert all(c.anchor_period_keys != ("2019Q4",) for c in margin_result.candidates)


def test_the_plans_twenty_six_quarter_z_of_four_point_zero_two_is_the_figure_before_r6(
    authority,  # type: ignore[no-untyped-def]
):
    """The plan's arithmetic reproduces exactly; only the population differs.

    Asserted so the difference is attributed to the rule rather than left as an unexplained
    third decimal: with `2019Q4` in, the mean is −0.3462, σ is 4.0382 and z is 4.0231, which
    rounds to §6.3's `+4.02`.
    """
    import statistics

    gaps = [
        PLAN_SERIES["adjusted_gross_margin"][i] - PLAN_SERIES["gaap_gross_margin"][i]
        for i in range(len(QUARTERS))
    ]
    assert len(gaps) == PLAN_QUARTERS
    mean, deviation = statistics.fmean(gaps), statistics.pstdev(gaps)

    assert round(mean, 4) == -0.3462
    assert round(deviation, 4) == 4.0382
    assert round((gaps[QUARTERS.index("2022Q3")] - mean) / deviation, 2) == PLAN_Z


# ---------------------------------------------------------------------------------------
# 1. Different metric version ids do not themselves cause a refusal — P5, corrected in S2
# ---------------------------------------------------------------------------------------


def test_the_two_metrics_of_a_pair_carry_different_formula_version_ids(authority):  # type: ignore[no-untyped-def]
    """The premise of the defect: equality between them is structurally impossible."""
    left = authority.registry.formula_for("adjusted_gross_margin", "2022-09-30")
    right = authority.registry.formula_for("gaap_gross_margin", "2022-09-30")

    assert (left.concept_id, right.concept_id) == (
        "adjusted_gross_margin_v1", "gaap_gross_margin_v1")
    assert left.concept_id != right.concept_id


def test_two_different_formula_version_ids_alone_refuse_no_period_of_the_pair(margin_result):  # type: ignore[no-untyped-def]
    """R6 as first written compared the two ids and would have refused every period.

    Evaluated per metric across both dates it refuses none of them, which is why F3 exists as
    a candidate at all. The only R6 refusal on this pair is the undeclared pre-2020 quarter.
    """
    reasons = {r.reason for r in margin_result.refusals}

    assert "FORMULA_VERSION_MISMATCH" not in reasons
    assert len(margin_result.candidates) == 3


# ---------------------------------------------------------------------------------------
# 2. One metric changing its own version across the window does refuse
# ---------------------------------------------------------------------------------------


def test_a_metric_that_changes_its_own_formula_version_splits_the_gap_history_in_two(
    authority,  # type: ignore[no-untyped-def]
):
    """`adjusted_gross_profit` is `_v1` to 2021-12-31 and `_v2` from 2022-01-01.

    A `contribution_profit ↔ adjusted_gross_profit` history spanning the boundary is not one
    population: the anchor's population holds only the quarters whose `adjusted_gross_profit`
    resolves to the same formula the anchor's does, and every other quarter is excluded by
    name with R6's own reason.
    """
    keys = QUARTERS[1:]  # 2020Q1 onward; 2019Q4 is undeclared for both metrics
    # One quarter in each era is pushed far from its own era's mean, so both eras fire.
    spike = {"2021Q2": -2.0e8, "2023Q2": -2.0e8}
    points = [
        usd_point("adjusted_gross_profit", key, -5e6 * index)
        for index, key in enumerate(keys)
    ] + [
        usd_point("contribution_profit", key,
                  -60e6 - 5e6 * index + wobble(index) + spike.get(key, 0.0))
        for index, key in enumerate(keys)
    ]

    result = detect_cross_metric_divergence(
        build_series(points),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("contribution_profit", "adjusted_gross_profit"),),
    )
    early = only(result.candidates, "2021Q2")
    late = only(result.candidates, "2023Q2")

    assert early.signals["population_first_period"] == "2020Q1"
    assert early.signals["population_last_period"] == "2021Q4"
    assert late.signals["population_first_period"] == "2022Q1"
    assert late.signals["population_last_period"] == "2026Q1"
    for candidate in (early, late):
        assert candidate.signals["population_excluded_by"] == "R6/FORMULA_VERSION_MISMATCH"
        assert POPULATION_EXCLUDES_PERIODS in candidate.warnings


def test_a_pair_whose_metrics_never_change_definition_excludes_no_period(margin_result):  # type: ignore[no-untyped-def]
    """The other half of the rule: it must not narrow a population it has no reason to.

    Both counts, because R4b added a second way a population narrows: a period lost to an
    unresolved slot. Neither may fire on a run with nothing to disclose.
    """
    for candidate in margin_result.candidates:
        assert candidate.signals["periods_excluded"] == 0
        assert candidate.signals["periods_unresolved"] == 0
        assert "population_excluded_by" not in candidate.signals
        assert POPULATION_EXCLUDES_PERIODS not in candidate.warnings


# ---------------------------------------------------------------------------------------
# 3. Only overlapping comparable periods are used, within one shape
# ---------------------------------------------------------------------------------------


def test_a_period_only_one_side_reports_contributes_no_gap(authority):  # type: ignore[no-untyped-def]
    """`homes_purchased` starts nine quarters after `homes_sold` in the real run; the shape of
    that is one series holding a period the other does not."""
    series = plan_series("gaap_gross_margin")
    series.update(plan_series("adjusted_gross_margin", quarters=QUARTERS[:12]))

    result = detect_cross_metric_divergence(
        series,
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("adjusted_gross_margin", "gaap_gross_margin"),),
    )
    candidate = only(result.candidates, "2022Q3")

    # 2019Q4 is refused by R6, leaving 2020Q1..2022Q3.
    assert candidate.signals["population_size"] == 11
    assert candidate.signals["comparable_overlap"] == 11
    assert candidate.signals["population_last_period"] == "2022Q3"


def test_a_fiscal_year_never_enters_a_quarterly_gap_history(authority):  # type: ignore[no-untyped-def]
    """The shape restriction, stated as the number it changes.

    Six fiscal years whose gap is a flat −3.0 pp are added to the quarterly series. Pooled they
    would drag the mean and shrink σ, and 2022Q3 would score against a distribution six of whose
    members are annual. The detector scores each shape apart, so the quarterly answer is
    unmoved — and the pooled figure is computed here so the difference is a number rather than
    a claim.
    """
    import statistics

    quarters = [
        quarter_point(metric_id, key, PLAN_SERIES[metric_id][QUARTERS.index(key)])
        for metric_id in ("adjusted_gross_margin", "gaap_gross_margin")
        for key in QUARTERS
    ]
    years = [
        year_point(metric_id, year, value)
        for metric_id, value in (("adjusted_gross_margin", 6.0),
                                 ("gaap_gross_margin", 9.0))
        for year in range(2020, 2026)
    ]

    result = detect_cross_metric_divergence(
        build_series(quarters + years),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("adjusted_gross_margin", "gaap_gross_margin"),),
    )
    candidate = only(
        [c for c in result.candidates if c.signals["period_shape"] == "quarter"], "2022Q3")

    assert candidate.signals["population_size"] == MEASURED_QUARTERS
    assert round(float(candidate.signals["z"]), 4) == MEASURED_Z
    assert {c.signals["period_shape"] for c in result.candidates} == {"quarter"}

    quarterly_gaps = [
        PLAN_SERIES["adjusted_gross_margin"][i] - PLAN_SERIES["gaap_gross_margin"][i]
        for i, key in enumerate(QUARTERS) if key != "2019Q4"
    ]
    pooled = quarterly_gaps + [-3.0] * 6
    pooled_z = (15.9 - statistics.fmean(pooled)) / statistics.pstdev(pooled)
    assert round(pooled_z, 4) == 4.3606
    assert round(pooled_z, 4) != MEASURED_Z


def test_each_shape_is_scored_against_its_own_population_and_six_is_too_few(authority):  # type: ignore[no-untyped-def]
    """Six fiscal years is under §6.10's ~8-point floor, so the shape refuses rather than
    minting a candidate from a distribution of six numbers."""
    points = [
        year_point(metric_id, year, value)
        for metric_id, values in (
            ("adjusted_gross_margin", (5.0, 6.0, 20.0, 5.5, 6.5, 5.0)),
            ("gaap_gross_margin", (6.0, 6.0, 6.0, 6.0, 6.0, 6.0)),
        )
        for year, value in zip(range(2020, 2026), values)
    ]

    result = detect_cross_metric_divergence(
        build_series(points),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("adjusted_gross_margin", "gaap_gross_margin"),),
    )

    assert result.candidates == ()
    assert {(r.reason, r.period_shape) for r in result.refusals} == {
        (POPULATION_TOO_SMALL, "fiscal_year")}


# ---------------------------------------------------------------------------------------
# 4. Low-variance presentation noise does not produce an extreme z
# ---------------------------------------------------------------------------------------


def test_a_flat_gap_disturbed_by_one_rounding_step_is_refused_as_presentation_noise(
    authority,  # type: ignore[no-untyped-def]
):
    """§6.6 D4's *"require σ(gap) ≥ 2 × tol"*, and what happens without it.

    Twenty-five quarters where the two margins print the same number, and one where they differ
    by a single 0.1 pp rounding step. The z-score of that step is 4.9 — larger than F3's — and
    the whole finding is that one filing rounded up.
    """
    values = [8.0] * len(QUARTERS)
    points = [
        quarter_point("gaap_gross_margin", key, value)
        for key, value in zip(QUARTERS, values)
    ] + [
        quarter_point("adjusted_gross_margin", key,
                      value + (0.1 if key == "2023Q2" else 0.0))
        for key, value in zip(QUARTERS, values)
    ]

    result = detect_cross_metric_divergence(
        build_series(points),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("adjusted_gross_margin", "gaap_gross_margin"),),
    )

    assert result.candidates == ()
    assert {r.reason for r in result.refusals} == {
        "FORMULA_VERSION_UNDECLARED", LOW_VARIANCE_PRESENTATION_NOISE}


def test_a_single_step_in_an_otherwise_flat_gap_cannot_mint_a_ten_sigma_candidate(
    authority,  # type: ignore[no-untyped-def]
):
    """The defect the `2 × tol` floor did **not** catch, and the arm R4b added for it.

    A 0.1 pp step is refused by the floor as written. A 1.05 pp step in the same flat gap is
    not: σ over the 25-quarter population is 0.20576, which clears the 0.2 floor by 0.0058, and
    the anchor then scores 4.899 — the largest |z| a member of its own 25-point population can
    reach, √(n−1). One quarter minting a "ten-sigma" divergence out of an otherwise motionless
    gap is the same finding the floor exists to refuse, one printed unit further out.

    So the floor is applied to the population **without** the anchor as well. The dispersion
    the z-score is taken against has to exist independently of the excursion being scored; here
    it is exactly 0. The z-score itself is still computed over the whole population, which is
    why no live number moved.
    """
    values = [8.0] * len(QUARTERS)
    points = [
        quarter_point("gaap_gross_margin", key, value)
        for key, value in zip(QUARTERS, values)
    ] + [
        quarter_point("adjusted_gross_margin", key,
                      value + (1.05 if key == "2023Q2" else 0.0))
        for key, value in zip(QUARTERS, values)
    ]

    import statistics
    gaps = [1.05 if key == "2023Q2" else 0.0 for key in QUARTERS if key != "2019Q4"]
    assert round(statistics.pstdev(gaps), 5) == 0.20576  # above the 0.2 floor
    assert round(
        (1.05 - statistics.fmean(gaps)) / statistics.pstdev(gaps), 4) == 4.8990
    assert round(len(gaps) ** 0.5, 4) != 4.8990 and round((len(gaps) - 1) ** 0.5, 4) == 4.8990

    result = detect_cross_metric_divergence(
        build_series(points),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("adjusted_gross_margin", "gaap_gross_margin"),),
    )

    assert result.candidates == ()
    assert LOW_VARIANCE_PRESENTATION_NOISE in {r.reason for r in result.refusals}


def test_the_real_wedge_clears_the_floor_with_its_own_anchor_taken_out(margin_result):  # type: ignore[no-untyped-def]
    """The arm above refuses nothing real, stated as the number that proves it.

    F3's gap history has σ = 4.1182 with 2022Q3 in it and 2.4915 without — the pair moves on
    its own account, twelve times the 0.2 floor, and the spike is not the only thing in the
    distribution. Measured live on all five pairs the narrowest margin is
    `homes_purchased ↔ homes_sold 2023Q1`, at 1518 against a floor of 2.0.
    """
    candidate = only(margin_result.candidates, "2022Q3")

    assert round(float(candidate.signals["gap_pstdev"]), 4) == MEASURED_PSTDEV
    assert round(float(candidate.signals["gap_pstdev_excluding_anchor"]), 4) == 2.4915
    assert float(candidate.signals["gap_pstdev_excluding_anchor"]) > float(
        candidate.signals["variance_floor"])
    assert round(float(candidate.signals["z"]), 4) == MEASURED_Z


def test_the_variance_floor_is_two_presentation_units_of_the_pairs_own_scale(margin_result):  # type: ignore[no-untyped-def]
    """0.2 pp for a percentage pair, and the real wedge clears it by a factor of twenty."""
    candidate = only(margin_result.candidates, "2022Q3")

    assert candidate.signals["unit"] == "percent"
    assert candidate.signals["variance_floor"] == 0.2
    assert float(candidate.signals["gap_pstdev"]) > 0.2


def test_a_usd_pair_filed_in_millions_carries_a_two_million_dollar_variance_floor(authority):  # type: ignore[no-untyped-def]
    """The floor follows the scale rather than a constant, because the five pairs span
    percentages, dollars and home counts."""
    points = [
        usd_point(metric_id, key, value)
        for metric_id, offset in (("contribution_profit", -4.0e5), ("adjusted_gross_profit", 0.0))
        for key, value in ((k, offset if k != "2023Q2" else offset - 1.2e6)
                           for k in QUARTERS[1:])
    ]

    result = detect_cross_metric_divergence(
        build_series(points),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("contribution_profit", "adjusted_gross_profit"),),
    )

    assert result.candidates == ()
    assert LOW_VARIANCE_PRESENTATION_NOISE in {r.reason for r in result.refusals}


# ---------------------------------------------------------------------------------------
# 4b. A pair must be stored under one sign convention before it may be differenced
# ---------------------------------------------------------------------------------------


def test_a_profit_against_a_negatively_stored_cost_is_refused_before_any_gap_is_taken(
    authority,  # type: ignore[no-untyped-def]
):
    """The pair `DivergencePair` was one line away from licensing.

    `direct_selling_costs` is stored negative — 46 of 46 values, measured in `detector_config` —
    so `adjusted_gross_profit − direct_selling_costs` is `512M − (−136M)`, the **sum** of a
    profit and a cost. R2 licenses the pair (both are components of `adjusted_gross_profit`'s
    formula, so the relation is `co_component`), R4 and R5 pass because both are USD, and the
    detector emitted three candidates from it — 2022Q1 at `z = 2.574` over a "gap" of
    `648000000.0`. Nothing downstream could have caught it: the arithmetic is correct and the
    number is meaningless.
    """
    assert value_sign_of("adjusted_gross_profit") is ValueSign.POSITIVE
    assert value_sign_of("direct_selling_costs") is ValueSign.NEGATIVE

    points = [
        usd_point("adjusted_gross_profit", key, 5.0e8 + 1.0e7 * index)
        for index, key in enumerate(QUARTERS[1:])
    ] + [
        usd_point("direct_selling_costs", key, -1.3e8 - wobble(index))
        for index, key in enumerate(QUARTERS[1:])
    ]

    result = detect_cross_metric_divergence(
        build_series(points),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("adjusted_gross_profit", "direct_selling_costs"),),
    )

    assert result.candidates == ()
    assert [(r.rule, r.reason, r.metric_ids) for r in result.refusals] == [
        ("D4", SIGN_CONVENTION_MISMATCH,
         ("adjusted_gross_profit", "direct_selling_costs"))]


def test_the_sign_refusal_needs_no_series_because_it_is_about_the_declaration(authority):  # type: ignore[no-untyped-def]
    """It fires with nothing loaded at all: a pair that may not be differenced may not be
    differenced whether or not the run holds its numbers, which is what "before any gap is
    computed" means. `SERIES_ABSENT` never gets the chance to speak."""
    result = detect_cross_metric_divergence(
        {},
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("adjusted_gross_profit", "direct_selling_costs"),),
    )

    assert [r.reason for r in result.refusals] == [SIGN_CONVENTION_MISMATCH]
    assert SERIES_ABSENT not in {r.reason for r in result.refusals}


def test_an_unverified_sign_matches_nothing_including_positive(authority):  # type: ignore[no-untyped-def]
    """`cost_of_revenue` has zero observations in this run, so its convention is unmeasured.

    `detector_config` refuses to guess it, and the two cost metrics the corpus *does* print are
    both negative — so pairing an unverified metric with a positive one is the same hazard as
    the test above with the evidence missing. Equality of `ValueSign` is the rule, and
    `UNVERIFIED` is equal only to itself.
    """
    assert value_sign_of("cost_of_revenue") is ValueSign.UNVERIFIED

    result = detect_cross_metric_divergence(
        {},
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("gaap_gross_profit", "cost_of_revenue"),),
    )

    assert [r.reason for r in result.refusals] == [SIGN_CONVENTION_MISMATCH]


def test_two_unverified_metrics_agree_and_are_scored_without_a_direction():
    """The other side of the same rule, and the only way an unverified metric reaches a
    candidate.

    Two `UNVERIFIED` metrics carry the same declaration, so the sign guard has nothing to
    refuse — and `quantity_direction` still cannot answer, so the candidate states no
    `gap_direction` and carries `metric_sign_convention_unverified` instead. It is not dropped:
    the divergence is real and citable and only the word for it is unavailable.

    **The shipped ontology relates the two unverified metrics in none of R2's four ways**
    *(verified: `definitional_relation` returns `None` for the pair, and `comparable` refuses
    every period of it with `R2/UNRELATED_METRICS`)*, which is why this drives a hand-built
    `ComparabilityAuthority` declaring them mutually distinct — the state the ontology would be
    in if either metric ever arrived. Asserted both ways so the test says which part is real:
    the branch is correct and no pair the ontology declares today can reach it.
    """
    pair = DivergencePair("cost_of_revenue", "inventory_valuation_adjustment")
    assert value_sign_of(pair.left) is value_sign_of(pair.right) is ValueSign.UNVERIFIED
    assert definitional_relation(pair, default_authority(), "2022-09-30") is None

    declared = ComparabilityAuthority(
        groups={metric: frozenset({"cost_measures"}) for metric in pair.metric_ids})
    points = [
        usd_point(pair.left, key, 4.0e8 + wobble(index))
        for index, key in enumerate(QUARTERS[1:])
    ] + [
        usd_point(pair.right, key, 1.0e7 + (3.0e8 if key == "2024Q2" else 0.0))
        for index, key in enumerate(QUARTERS[1:])
    ]

    refused = detect_cross_metric_divergence(
        build_series(points), graph_run_id=GRAPH_RUN_ID, pairs=(pair,))
    result = detect_cross_metric_divergence(
        build_series(points), graph_run_id=GRAPH_RUN_ID, authority=declared, pairs=(pair,))
    candidate = only(result.candidates, "2024Q2")

    assert {(r.rule, r.reason) for r in refused.refusals} == {("R2", "UNRELATED_METRICS")}
    assert candidate.signals["sign_convention"] == ValueSign.UNVERIFIED.value
    assert "gap_direction" not in candidate.signals
    assert SIGN_CONVENTION_UNVERIFIED in candidate.warnings


def test_the_five_shipped_pairs_all_share_one_sign_convention(authority):  # type: ignore[no-untyped-def]
    """Why the live census is untouched by any of the above: all ten metrics are `POSITIVE`."""
    signs = {
        metric_id: value_sign_of(metric_id)
        for pair in DIVERGENCE_PAIRS
        for metric_id in pair.metric_ids
    }

    assert set(signs.values()) == {ValueSign.POSITIVE}
    assert all(value_sign_of(p.left) is value_sign_of(p.right) for p in DIVERGENCE_PAIRS)


def test_the_candidate_states_each_sides_polarity_and_which_way_the_gap_went(margin_result):  # type: ignore[no-untyped-def]
    """§6.6 D1's map, on a D4 candidate. Two polarities because a pair is two metrics, and one
    `gap_direction` because the sign guard above has established that both sides are stored the
    same way — without it the phrase would have no referent."""
    candidate = only(margin_result.candidates, "2022Q3")

    assert candidate.signals["left_polarity"] == MetricPolarity.RATIO.value
    assert candidate.signals["right_polarity"] == MetricPolarity.RATIO.value
    assert candidate.signals["sign_convention"] == ValueSign.POSITIVE.value
    # +15.9 pp against a mean of −0.348: the wedge opened.
    assert candidate.signals["gap_direction"] == DIRECTION_INCREASE
    assert only(margin_result.candidates, "2023Q1").signals[
        "gap_direction"] == DIRECTION_DECREASE
    assert DIRECTION_UNCHANGED not in set(candidate.signals.values())


# ---------------------------------------------------------------------------------------
# 5. Cohort ↔ period pairs carry the mandatory warning
# ---------------------------------------------------------------------------------------


def test_the_contribution_profit_pair_carries_the_cohort_versus_period_warning(authority):  # type: ignore[no-untyped-def]
    """R9, and `formulas.yaml`'s reason for it: *"a Contribution Profit value is NOT a slice of
    any single period's expenses."* It warns; it does not refuse."""
    keys = QUARTERS[10:]  # 2022Q1 onward — one formula era for adjusted_gross_profit
    points = [
        usd_point("adjusted_gross_profit", key, -1.0e7 * index)
        for index, key in enumerate(keys)
    ] + [
        usd_point("contribution_profit", key,
                  -6.0e7 - 1.0e7 * index + wobble(index)
                  + (-3.0e8 if key == "2024Q2" else 0.0))
        for index, key in enumerate(keys)
    ]

    result = detect_cross_metric_divergence(
        build_series(points),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("contribution_profit", "adjusted_gross_profit"),),
    )
    candidate = only(result.candidates, "2024Q2")

    assert COHORT_VS_PERIOD_BASIS in candidate.warnings
    assert candidate.signals["relation"] == RelationKind.COMPONENT.value


def test_two_cohort_measures_differenced_against_each_other_carry_no_basis_warning(authority):  # type: ignore[no-untyped-def]
    """`contribution_profit_after_interest ↔ contribution_profit` is cohort against cohort.

    R9 fires on a *mixed* basis, so a pair where both sides are cohort measures is silent — and
    a test that only ever saw the warning fire could not tell the rule from `return True`.
    """
    keys = QUARTERS[1:]
    points = [
        usd_point("contribution_profit", key, 0.0) for key in keys
    ] + [
        usd_point("contribution_profit_after_interest", key,
                  -2.0e7 + wobble(index) + (-3.0e8 if key == "2024Q2" else 0.0))
        for index, key in enumerate(keys)
    ]

    result = detect_cross_metric_divergence(
        build_series(points),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("contribution_profit_after_interest", "contribution_profit"),),
    )
    candidate = only(result.candidates, "2024Q2")

    assert COHORT_VS_PERIOD_BASIS not in candidate.warnings


# ---------------------------------------------------------------------------------------
# 6. Candidate ids and signals are deterministic and stable under input reordering
# ---------------------------------------------------------------------------------------


def test_candidate_ids_and_signals_do_not_move_when_the_points_arrive_in_another_order(
    authority,  # type: ignore[no-untyped-def]
):
    """§6.11's whole point: the order a detector visited its observations in is not identity."""
    points = [
        quarter_point(metric_id, key, PLAN_SERIES[metric_id][QUARTERS.index(key)])
        for metric_id in ("adjusted_gross_margin", "gaap_gross_margin", "contribution_margin")
        for key in QUARTERS
    ]
    first = detect_cross_metric_divergence(
        build_series(points), graph_run_id=GRAPH_RUN_ID, authority=authority)

    shuffled = list(points)
    random.Random(20260804).shuffle(shuffled)
    second = detect_cross_metric_divergence(
        build_series(shuffled), graph_run_id=GRAPH_RUN_ID, authority=authority)

    assert first.candidates == second.candidates
    assert first.refusals == second.refusals
    assert [c.candidate_id for c in first.candidates] == [
        c.candidate_id for c in second.candidates]


def test_the_candidate_id_reads_as_section_six_eleven_writes_it(margin_result):  # type: ignore[no-untyped-def]
    """`cand:{detector_slug}:{scope_slug}:{subject}:{anchor}:{digest12}` (§6.11).

    The digest is **not** pinned against the plan's published `dcc6148df2bd`: F7 records that
    those were computed against the 2026-08-02 snapshot and that the plan never states the
    `anchor_input_ids` behind them, so a test asserting one would be asserting a number nobody
    can rederive. The *shape* and the stability under reordering are what identity promises.
    """
    candidate = only(margin_result.candidates, "2022Q3")
    head, detector, scope, subject, anchor, digest = candidate.candidate_id.split(":")

    assert (head, detector, subject, anchor) == (
        "cand", "cross-metric-divergence", "opendoor", "2022Q3")
    assert scope == "adjusted-gross-margin-gaap-gross-margin"
    assert len(digest) == 12 and digest.isalnum()


def test_the_anchor_observation_ids_are_sorted_and_are_the_digest_inputs(margin_result):  # type: ignore[no-untyped-def]
    candidate = only(margin_result.candidates, "2022Q3")

    assert candidate.anchor_observation_ids == tuple(sorted(candidate.anchor_observation_ids))
    assert candidate.anchor_observation_ids == (
        "obs:adjusted_gross_margin:2022Q3", "obs:gaap_gross_margin:2022Q3")
    assert candidate.evidence_request.observation_ids == candidate.anchor_observation_ids


# ---------------------------------------------------------------------------------------
# 7. No prose on the candidate, and no score
# ---------------------------------------------------------------------------------------


def test_the_candidate_carries_no_thesis_and_no_ranking_term(margin_result):  # type: ignore[no-untyped-def]
    """§6.4: the thesis is §11's output and the score is §6.10's, and neither is a field here.

    Driven through the model rather than asserted against its field list: `extra="forbid"` is
    what makes the absence real, so the test constructs the field the plan refuses and expects
    the construction to fail.
    """
    candidate = only(margin_result.candidates, "2022Q3")

    with pytest.raises(ValidationError):
        StoryCandidate(**{**candidate.model_dump(), "thesis_hypothesis": "margins diverged"})
    forbidden = {"materiality", "novelty", "evidence_quality", "ambiguity_penalty",
                 "repetition_penalty", "score", "rank", "headline", "thesis"}
    assert forbidden.isdisjoint(candidate.signals)


def test_every_string_signal_is_an_ontology_identifier_or_the_ontologys_own_expression(
    margin_result, authority,  # type: ignore[no-untyped-def]
):
    """The stronger half of "no prose": each string is checked against where it came from.

    A rendered sentence would fail this even if it read like a fact, because the only strings
    permitted are ids this module was given, expression strings `formulas.yaml` states, and
    words from `detector_config`'s two closed enums. `gap_direction`, `left_polarity`,
    `right_polarity` and `sign_convention` are the last of those: R4b put them here because a
    candidate that stated only the arithmetic gap left a reader of a negative-convention pair to
    infer the direction from a minus sign, which is the inference §6.6 D1's polarity map exists
    to forbid.
    """
    candidate = only(margin_result.candidates, "2022Q3")
    left = authority.registry.formula_for("adjusted_gross_margin", "2022-09-30")
    right = authority.registry.formula_for("gaap_gross_margin", "2022-09-30")
    strings = {k: v for k, v in candidate.signals.items() if isinstance(v, str)}

    assert strings == {
        "left_metric_id": "adjusted_gross_margin",
        "right_metric_id": "gaap_gross_margin",
        "population_first_period": "2020Q1",
        "population_last_period": "2026Q1",
        "period_shape": "quarter",
        "unit": "percent",
        "sign_convention": "positive",
        "gap_direction": "increase",
        "left_polarity": "ratio",
        "right_polarity": "ratio",
        "relation": "shared_components",
        "shared_component_metrics": "revenue",
        "relation_group_ids": "margin_measures",
        "left_formula_id": left.concept_id,
        "right_formula_id": right.concept_id,
        "left_formula_expression": left.expression,
        "right_formula_expression": right.expression,
    }
    assert left.expression == "adjusted_gross_profit / revenue"
    assert right.expression == "gaap_gross_profit / revenue"


def test_the_definitional_relation_is_read_from_formulas_yaml_for_every_declared_pair(
    authority,  # type: ignore[no-untyped-def]
):
    """§6.6 D4: *"the output must state the definitional relation, not just the numbers."*

    C4 in one assertion: every one of these comes from the ontology's formula versions and
    `mutually_distinct_groups`, and none of it is available on a `:Metric` node to fall back to.
    """
    measured = {
        pair.metric_ids: definitional_relation(pair, authority, "2022-09-30")
        for pair in DIVERGENCE_PAIRS
    }

    assert {ids: relation.kind for ids, relation in measured.items()} == {
        ("adjusted_gross_margin", "gaap_gross_margin"): RelationKind.SHARED_COMPONENTS,
        ("adjusted_gross_margin", "contribution_margin"): RelationKind.SHARED_COMPONENTS,
        ("adjusted_gross_profit", "contribution_profit"): RelationKind.COMPONENT,
        ("contribution_profit", "contribution_profit_after_interest"): RelationKind.COMPONENT,
        ("homes_purchased", "homes_sold"): RelationKind.DISTINCT_GROUP,
    }
    assert measured[("adjusted_gross_margin", "gaap_gross_margin")].shared_component_metrics == (
        "revenue",)
    assert measured[("adjusted_gross_profit", "contribution_profit")].component_metric_id == (
        "adjusted_gross_profit")
    assert measured[("homes_purchased", "homes_sold")].group_ids == ("home_counts",)
    assert measured[("homes_purchased", "homes_sold")].left_expression is None


# ---------------------------------------------------------------------------------------
# 8. An incomplete series refuses rather than emits
# ---------------------------------------------------------------------------------------


def test_a_truncated_series_refuses_with_a_population_too_small_rather_than_emitting(
    authority,  # type: ignore[no-untyped-def]
):
    """Five quarters carrying F3's own spike, and the answer is a refusal.

    A z-score over five numbers is arithmetic, not evidence; §6.10 suppresses `magnitude_z`
    below ~8 points and the same floor applies to a gap distribution.
    """
    truncated = QUARTERS[QUARTERS.index("2022Q1"):QUARTERS.index("2022Q1") + 5]
    result = detect_cross_metric_divergence(
        plan_series("adjusted_gross_margin", "gaap_gross_margin", quarters=truncated),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("adjusted_gross_margin", "gaap_gross_margin"),),
    )

    assert len(truncated) < MIN_POPULATION
    assert result.candidates == ()
    assert {(r.reason, r.period_key) for r in result.refusals} == {
        (POPULATION_TOO_SMALL, key) for key in truncated}


def test_a_pair_with_no_series_at_all_is_refused_by_name_rather_than_skipped(authority):  # type: ignore[no-untyped-def]
    """A hole in the run is a finding. Silence would be indistinguishable from "nothing here"."""
    result = detect_cross_metric_divergence(
        plan_series("adjusted_gross_margin"),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("adjusted_gross_margin", "gaap_gross_margin"),),
    )

    assert result.candidates == ()
    assert [(r.reason, r.metric_ids) for r in result.refusals] == [
        (SERIES_ABSENT, ("adjusted_gross_margin", "gaap_gross_margin"))]


def conflicted(*keys: str) -> Sequence[Any]:
    """§6.2's two margins with `gaap_gross_margin` unresolved at `keys`."""
    return [
        quarter_point(metric_id, key, PLAN_SERIES[metric_id][QUARTERS.index(key)])
        for metric_id in ("adjusted_gross_margin", "gaap_gross_margin")
        for key in QUARTERS
        if not (metric_id == "gaap_gross_margin" and key in keys)
    ] + [
        quarter_point("gaap_gross_margin", key, None,
                      status=CanonicalStatus.CONFLICT,
                      representative_observation_id=None, supporting_observation_ids=())
        for key in keys
    ]


def test_a_conflicted_slot_emits_no_value_and_leaves_the_period_out_of_the_history(authority):  # type: ignore[no-untyped-def]
    """R7: a slot the run could not resolve is not a zero, and it is not a population member."""
    result = detect_cross_metric_divergence(
        build_series(conflicted("2024Q2")),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("adjusted_gross_margin", "gaap_gross_margin"),),
    )
    candidate = only(result.candidates, "2022Q3")

    assert candidate.signals["population_size"] == MEASURED_QUARTERS - 1
    assert candidate.signals["comparable_overlap"] == MEASURED_QUARTERS - 1


def test_a_conflicted_slot_says_it_narrowed_the_population_and_by_how_much(authority):  # type: ignore[no-untyped-def]
    """The disclosure R4b added, because the narrowing was silent.

    `_gap_series` built from `valued_points`, so a conflicted period vanished before
    `_population` ever saw it: with two slots conflicted the candidate reported a population of
    23, a `comparable_overlap` of 23, `periods_excluded = 0` and no warning. A reader had no
    way to tell a 23-quarter pair from a 25-quarter pair that lost two — which is the state
    §10.1's disclosure exists to prevent, and which the module already handled for R6 two lines
    away.

    The count is published beside `comparable_overlap` rather than folded into it: the two
    periods were never comparable, so adding them to a field named for comparability would be
    the opposite error.
    """
    result = detect_cross_metric_divergence(
        build_series(conflicted("2024Q2", "2024Q3")),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("adjusted_gross_margin", "gaap_gross_margin"),),
    )
    candidate = only(result.candidates, "2022Q3")

    assert candidate.signals["population_size"] == MEASURED_QUARTERS - 2
    assert candidate.signals["comparable_overlap"] == MEASURED_QUARTERS - 2
    assert candidate.signals["periods_unresolved"] == 2
    assert candidate.signals["periods_excluded"] == 0
    assert candidate.signals["population_excluded_by"] == UNRESOLVED_PERIOD_RULE
    assert POPULATION_EXCLUDES_PERIODS in candidate.warnings

    # And the two periods are refused by name, under R7's own rule id rather than this
    # module's: a detector that dropped them from the population without saying so was the
    # defect, and a detector that restated R7 would be a second copy of it.
    assert {(r.rule, r.reason, r.period_key) for r in result.refusals if r.rule == "R7"} == {
        ("R7", "NOT_CANONICAL", "2024Q2"), ("R7", "NOT_CANONICAL", "2024Q3")}


# ---------------------------------------------------------------------------------------
# The pair list, and the one pair that is not on it
# ---------------------------------------------------------------------------------------


def test_the_declared_pairs_are_the_five_section_six_six_ships(authority):  # type: ignore[no-untyped-def]
    assert tuple((pair.left, pair.right) for pair in DIVERGENCE_PAIRS) == (
        ("adjusted_gross_margin", "gaap_gross_margin"),
        ("contribution_margin", "adjusted_gross_margin"),
        ("contribution_profit", "adjusted_gross_profit"),
        ("contribution_profit_after_interest", "contribution_profit"),
        ("homes_purchased", "homes_sold"),
    )


def test_housing_inventory_homes_against_homes_sold_is_not_a_declared_pair():
    """§6.6 D4's correction: `housing_inventory_homes` is 126/126 `instant` and `homes_sold`
    has zero instants, so R3 refuses every pair and the overlap is zero. The first draft's
    "(20)" was `homes_sold`'s own quarterly count."""
    named = {frozenset(pair.metric_ids) for pair in DIVERGENCE_PAIRS}

    assert frozenset({"housing_inventory_homes", "homes_sold"}) not in named
    assert all("housing_inventory_homes" not in pair.metric_ids for pair in DIVERGENCE_PAIRS)


def test_an_instant_series_and_a_duration_series_overlap_in_no_period(authority):  # type: ignore[no-untyped-def]
    """Why that pair is absent, driven rather than asserted from the plan.

    Even if it were declared, an instant point and a quarter point never share a period key, so
    the pair produces neither a candidate nor an R3 refusal — it simply has nothing to compare.
    """
    points = [
        quarter_point("homes_sold", key, 3000.0, unit="homes", scale="units")
        for key in QUARTERS
    ] + [
        CanonicalPoint(
            metric_id="housing_inventory_homes",
            period=story_period(None, None, f"{year}-12-31"),
            subject_entity_id="opendoor",
            status=CanonicalStatus.OK,
            value=10_000.0,
            unit="homes",
            scale="units",
            currency=None,
            representative_observation_id=f"obs:inventory:{year}",
            supporting_observation_ids=(f"obs:inventory:{year}",),
        )
        for year in range(2019, 2027)
    ]

    result = detect_cross_metric_divergence(
        build_series(points),
        graph_run_id=GRAPH_RUN_ID,
        authority=authority,
        pairs=(DivergencePair("housing_inventory_homes", "homes_sold"),),
    )

    assert result.candidates == ()
    assert result.refusals == ()


# ---------------------------------------------------------------------------------------
# Live — the same numbers, recomputed from the graph
# ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live_series():  # type: ignore[no-untyped-def]
    """Every observation in the run, canonicalised into series. Skipped with no container."""
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


@pytest.fixture(scope="module")
def live_result(live_series):  # type: ignore[no-untyped-def]
    return detect_cross_metric_divergence(live_series, graph_run_id=GRAPH_RUN_ID)


@pytest.mark.neo4j
def test_live_the_2022q3_wedge_reproduces_from_the_graph(live_result):  # type: ignore[no-untyped-def]
    candidate = only(
        [c for c in live_result.candidates
         if c.metric_ids == ("adjusted_gross_margin", "gaap_gross_margin")],
        "2022Q3",
    )
    signals = candidate.signals

    assert (signals["left_value"], signals["right_value"]) == (3.3, -12.6)
    assert round(float(signals["gap"]), 4) == 15.9
    assert round(float(signals["z"]), 4) == MEASURED_Z
    assert signals["population_size"] == MEASURED_QUARTERS
    assert signals["period_shape"] == "quarter"
    assert signals["shared_component_metrics"] == "revenue"


@pytest.mark.neo4j
def test_live_pooling_every_shape_would_score_the_2022q3_wedge_differently(live_series):  # type: ignore[no-untyped-def]
    """The measurement behind the shape restriction, on the real corpus.

    Over all four shapes the pair has 43 comparable periods — 25 quarters, 6 fiscal years, 6
    six-month and 6 nine-month year-to-date windows — and 2022Q3 scores 4.3784 against them.
    That number compares a quarter's wedge with a fiscal year's. The detector reports 3.9454.
    """
    import statistics

    left = {p.period.key: p for p in live_series["adjusted_gross_margin"].valued_points()}
    right = {p.period.key: p for p in live_series["gaap_gross_margin"].valued_points()}
    shared = sorted(set(left) & set(right))
    by_shape: dict[str, int] = {}
    for key in shared:
        shape = left[key].period.shape.value
        by_shape[shape] = by_shape.get(shape, 0) + 1
    # The three slots R6 refuses: they end before every formula version's `valid_from`.
    undeclared = {"2019Q4", "FY2018", "FY2019"}
    pooled = [left[key].value - right[key].value for key in shared if key not in undeclared]

    assert by_shape == {"quarter": 26, "fiscal_year": 8, "ytd_6m": 6, "ytd_9m": 6}
    assert len(pooled) == 43
    assert round(
        (15.9 - statistics.fmean(pooled)) / statistics.pstdev(pooled), 4) == 4.3784


@pytest.mark.neo4j
def test_live_the_pair_overlaps_are_the_plans_counts_less_the_pre_2020_quarter(live_series):  # type: ignore[no-untyped-def]
    """§6.6 D4 records 26 · 26 · 26 · 11 · 15 comparable quarters. Measured after R6 they are
    25 · 25 · 25 · 10 · 15: `2019Q4` precedes every formula window and is refused."""
    raw: dict[tuple[str, ...], int] = {}
    comparable_counts: dict[tuple[str, ...], int] = {}
    for pair in DIVERGENCE_PAIRS:
        left = {p.period.key
                for p in live_series[pair.left].valued_points(PeriodShape.QUARTER)}
        right = {p.period.key
                 for p in live_series[pair.right].valued_points(PeriodShape.QUARTER)}
        raw[pair.metric_ids] = len(left & right)
        result = detect_cross_metric_divergence(
            live_series, graph_run_id=GRAPH_RUN_ID, pairs=(pair,), z_min=0.0,
            min_population=1)
        comparable_counts[pair.metric_ids] = max(
            int(c.signals["comparable_overlap"])
            for c in result.candidates
            if c.signals["period_shape"] == "quarter"
        )

    assert list(raw.values()) == [26, 26, 26, 11, 15]
    assert list(comparable_counts.values()) == [25, 25, 25, 10, 15]


@pytest.mark.neo4j
def test_live_housing_inventory_homes_and_homes_sold_share_no_period(live_series):  # type: ignore[no-untyped-def]
    """The measurement behind the pair's removal: 126/126 instant against zero instants."""
    inventory = live_series["housing_inventory_homes"]
    sold = live_series["homes_sold"]

    assert {p.period.shape for p in inventory.points} == {PeriodShape.INSTANT}
    assert PeriodShape.INSTANT not in {p.period.shape for p in sold.points}
    assert {p.period.key for p in inventory.points} & {p.period.key for p in sold.points} == set()


@pytest.mark.neo4j
def test_live_the_contribution_profit_pair_splits_at_the_formula_boundary(live_result):  # type: ignore[no-untyped-def]
    """`adjusted_gross_profit` is redefined at 2022-01-01, so its history is two populations —
    8 quarters under v1 and 17 under v2 — and each candidate says which it was scored against."""
    pair_candidates = {
        c.anchor_period_keys[0]: c
        for c in live_result.candidates
        if c.metric_ids == ("adjusted_gross_profit", "contribution_profit")
    }

    assert sorted(pair_candidates) == ["2021Q4", "2022Q1"]
    assert pair_candidates["2021Q4"].signals["population_size"] == 8
    assert pair_candidates["2022Q1"].signals["population_size"] == 17
    for candidate in pair_candidates.values():
        assert candidate.signals["population_excluded_by"] == "R6/FORMULA_VERSION_MISMATCH"
        assert COHORT_VS_PERIOD_BASIS in candidate.warnings
        assert POPULATION_EXCLUDES_PERIODS in candidate.warnings


@pytest.mark.neo4j
def test_live_the_run_yields_fifteen_candidates_and_all_of_them_are_quarters(live_result):  # type: ignore[no-untyped-def]
    """The census, so a change to any threshold shows up as a number rather than as silence."""
    by_pair: dict[tuple[str, ...], int] = {}
    for candidate in live_result.candidates:
        by_pair[candidate.metric_ids] = by_pair.get(candidate.metric_ids, 0) + 1

    assert len(live_result.candidates) == 15
    assert {c.signals["period_shape"] for c in live_result.candidates} == {"quarter"}
    assert by_pair == {
        ("adjusted_gross_margin", "contribution_margin"): 7,
        ("adjusted_gross_margin", "gaap_gross_margin"): 3,
        ("adjusted_gross_profit", "contribution_profit"): 2,
        ("contribution_profit", "contribution_profit_after_interest"): 1,
        ("homes_purchased", "homes_sold"): 2,
    }


@pytest.mark.neo4j
def test_live_the_only_refusal_reasons_are_the_formula_window_and_the_population_floor(
    live_result,  # type: ignore[no-untyped-def]
):
    """Named so a new refusal reason cannot appear without a test noticing.

    The three R4b added are all absent, and each absence is a measurement:
    `SIGN_CONVENTION_MISMATCH` because all ten declared metrics are `POSITIVE`,
    `R7/NOT_CANONICAL` because no declared pair holds a conflicted slot at a period the other
    side also reports, and the strengthened `LOW_VARIANCE_PRESENTATION_NOISE` because every
    population's σ clears the floor with its anchor taken out.
    """
    reasons: dict[str, int] = {}
    for refusal in live_result.refusals:
        reasons[refusal.reason] = reasons.get(refusal.reason, 0) + 1

    assert reasons == {"FORMULA_VERSION_UNDECLARED": 12, POPULATION_TOO_SMALL: 73}


@pytest.mark.neo4j
def test_live_no_population_rests_on_its_own_anchors_excursion(live_result):  # type: ignore[no-untyped-def]
    """R4b's second variance arm, measured against every candidate the run produces.

    A σ the anchor created by itself is not a history, and the floor is applied to the
    population without the anchor for that reason. On this corpus it refuses nothing, and the
    margin is recorded as a ratio rather than as a pass so a pair that ever came close shows up
    as a number. **The narrowest is 3.3×** — the seven `adjusted_gross_margin ↔
    contribution_margin` candidates, whose 25-quarter gap history keeps σ ≈ 0.65 pp with any one
    of them removed, against the 0.2 pp floor a percentage pair carries. F3's own pair sits at
    12.5× and `homes_purchased ↔ homes_sold` at 759×.
    """
    margins = {
        (candidate.metric_ids, candidate.anchor_period_keys[0]): round(
            float(candidate.signals["gap_pstdev_excluding_anchor"])
            / float(candidate.signals["variance_floor"]), 1)
        for candidate in live_result.candidates
    }

    assert len(margins) == 15
    assert min(margins.values()) == 3.3
    assert margins[(("adjusted_gross_margin", "gaap_gross_margin"), "2022Q3")] == 12.5
    assert margins[(("homes_purchased", "homes_sold"), "2023Q1")] == 759.2


@pytest.mark.neo4j
def test_live_every_candidate_states_both_polarities_and_a_direction(live_result):  # type: ignore[no-untyped-def]
    """§6.6 D1's map on the live D4 census. All ten metrics are `POSITIVE`, so every pair has a
    shared convention, every candidate states a `gap_direction`, and none carries the unverified
    warning — which is what makes the sign guard a no-op on this run and not a silent filter."""
    for candidate in live_result.candidates:
        assert candidate.signals["sign_convention"] == ValueSign.POSITIVE.value
        assert candidate.signals["gap_direction"] in (DIRECTION_INCREASE, DIRECTION_DECREASE)
        assert candidate.signals["left_polarity"] in {p.value for p in MetricPolarity}
        assert candidate.signals["right_polarity"] in {p.value for p in MetricPolarity}
        assert SIGN_CONVENTION_UNVERIFIED not in candidate.warnings
        assert candidate.signals["periods_unresolved"] == 0


@pytest.mark.neo4j
def test_live_no_candidate_carries_a_string_that_is_not_from_the_graph_or_the_ontology(
    live_result, live_series,  # type: ignore[no-untyped-def]
):
    """Every string signal on every live candidate is a metric id, a shape, a unit, a relation
    name, a formula id, a formula expression the ontology states, or a word from one of
    `detector_config`'s closed enums.

    The last of those is R4b's addition and it is enumerated from the enums rather than typed
    out, so a detector that invented a fifth polarity or a third direction would fail here.
    """
    from ontology import load_ontology

    definitions = load_ontology().definitions
    allowed = (
        {metric.concept_id for metric in definitions.metrics}
        | {formula.concept_id for formula in definitions.formula_versions}
        | {formula.expression for formula in definitions.formula_versions}
        | {component
           for formula in definitions.formula_versions
           for component in formula.component_metrics}
        | {group.group_id for group in definitions.constraints.mutually_distinct_groups}
        | {shape.value for shape in PeriodShape}
        | {kind.value for kind in RelationKind}
        | {polarity.value for polarity in MetricPolarity}
        | {sign.value for sign in ValueSign}
        | {DIRECTION_INCREASE, DIRECTION_DECREASE, DIRECTION_UNCHANGED}
        | {"percent", "USD", "homes"}
        | {"R6/FORMULA_VERSION_MISMATCH", UNRESOLVED_PERIOD_RULE}
    )

    # The two period-key signals are the exception: a period key is the graph's own, not the
    # ontology's. Everything else — `population_excluded_by` included — is checked.
    period_signals = {"population_first_period", "population_last_period"}
    strings = {
        value
        for candidate in live_result.candidates
        for key, value in candidate.signals.items()
        if isinstance(value, str) and key not in period_signals
    }
    assert strings <= allowed, sorted(strings - allowed)
