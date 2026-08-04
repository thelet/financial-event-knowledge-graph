"""The tables §6.6's detectors judge a move against: thresholds, polarity, floors.

Responsibility: the *parameters* of detection, and none of its arithmetic. A detector asks this
module "how big is big for this metric", "which way is up for this metric" and "below what base
is a percentage meaningless", and does the deciding itself. Nothing here reads a graph, an
ontology or a clock; every table is a literal and every function is a pure function of its
arguments.

**Why the polarity map lives in code and not in `metrics.yaml`.** §6.6 D1's first draft named a
`polarity` key on the metric definition. There is none *(verified 2026-08-04: none of the 26
entries in `ontology/versions/real_estate_marketplace_v1/definitions/metrics.yaml` carries a
`polarity` key; the nearest declared field is `metric_category`, whose five values —
`operating`, `gaap_financial`, `non_gaap_financial`, `capital_structure`, `macroeconomic` — say
where a number comes from, not which way it is good)*. So the map is story-owned. §6.6 puts it
in `config/story.yaml`; it is here instead, because a detector that could not run without a
config file on disk could not be unit-tested from a clean checkout, and because
`tests/story/test_story_detector_metric_move.py` has to be able to assert the table's coverage
against the ontology directly. Moving it into `config/story.yaml` at S11 is a data move, not a
redesign — `METRIC_POLARITY` and `VALUE_SIGN` are two flat dictionaries.

**Polarity and sign convention are two different questions and conflating them is the bug.**
`adjusted_ebitda` is a `REVENUE`-polarity metric with 37 negative values out of 48 *(measured
live)* — a negative EBITDA is a loss, not a sign convention. `direct_selling_costs` is a
`COST`-polarity metric whose values are negative **as a convention**: 46 of 46 are below zero
*(measured live)*, because the filings print *"Direct selling costs (54,175)"* inside a
subtotal. So *"costs rose"* is a **fall** in the stored number, and the direction of a move can
only be computed from `VALUE_SIGN`, never from the sign of `Δ` alone. That is why
`quantity_direction` takes a metric id and refuses to answer without one.

**A family with no measured threshold gets no threshold.** §6.6's table states a p75 of |QoQ|
for five families. The two macroeconomic percentages (`mortgage_rate`,
`home_price_appreciation`) belong to none of them and have **zero observations in this run**, so
there is no p75 to state; `FAMILY_THRESHOLDS` has no entry for `MACRO_PERCENT` and
`thresholds_for` returns `None`. A detector must refuse rather than fire on an invented number.

**Measured p75 of |Δ| per metric, this run, over comparable same-shape steps** *(verified
2026-08-04 against `graph-v1-0483dc6b4b10`; quarters unless the metric is an instant series)*.
The plan's figures are percentages of the base and these are absolute, so the two tables are
not the same measurement — these are what `abs_min` is judged against:

    adjusted_gross_profit     $66.8M   contribution_profit    $61.1M   adjusted_ebitda   $53.8M
    contribution_profit_after_interest $50.0M   direct_selling_costs   $23.5M
    holding_costs              $6.5M (3 deltas)
    adjusted_gross_margin      3.28 pp  contribution_margin     3.43 pp
    gaap_gross_margin          2.98 pp  adjusted_ebitda_margin  4.05 pp
    pct_homes_on_market_gt_120_days 19.0 pp
    homes_sold                 1,904    homes_purchased         1,302
    housing_inventory_homes    3,649    homes_under_contract      832 (4 deltas)
    market_count                 6.0

The four margins reproduce §6.6's 3.0 pp and `pct_>120d` reproduces its 19 pp exactly.
`$50M` sits just under `adjusted_ebitda`'s own p75 and far above `holding_costs`', which is
recorded rather than corrected: one USD row is what the plan states.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from story.core.periods import PeriodShape

# `DELTA_PRECISION` — the decimal places every delta is rounded to before it is compared with a
# threshold, a sign or another delta — is **bound from `story/core/series.py`, not restated
# here**. `13.2 − 3.3` is `9.899999999999999` in IEEE 754 and `adjusted_gross_margin` holds
# exactly those two values in adjacent quarters, so a rule testing `|d| >= 3.0` or `sign(d)` on
# raw subtraction would decide on residues at the seventeenth digit. Since D9 it is also what R8
# rounds to, and R8 lives in `core/`, which may not import a stage — so `core/` owns the literal
# and this module is where the detectors read it. Two copies of a rounding precision, one inside
# the comparability rule and one inside the detectors, is exactly the pair that can drift into
# disagreeing about what counts as a movement.
from story.core.series import DELTA_PRECISION

#: The five shapes R3 admits, in the order a scan visits them. `PeriodShape.OTHER` is absent
#: because R3 refuses it outright — the three cross-year windows in this run are comparable with
#: nothing — and excluding it here as well means a step is never built out of two points
#: `comparable` would only refuse one at a time.
COMPARABLE_SHAPES: tuple[PeriodShape, ...] = (
    PeriodShape.QUARTER,
    PeriodShape.INSTANT,
    PeriodShape.FISCAL_YEAR,
    PeriodShape.YTD_6M,
    PeriodShape.YTD_9M,
)

#: §6.6 D1's `floor(metric) = 0.10 × median(|value|)`. Without it `adjusted_ebitda 2021Q4 →
#: 2022Q1` reads `$0.4M → $176M = +43,900%` *(verified: the two canonical values are `400000.0`
#: and `176000000.0`)* and the largest percentage moves in the run are all division by noise.
FLOOR_FRACTION = 0.10

#: The smallest delta population a σ is computed over, matching §6.10's *"`magnitude_z` is
#: suppressed to `null`, not computed, when a metric has fewer than ~8 points"*. A σ over three
#: deltas is a number, not a dispersion. `cross_metric_divergence.MIN_POPULATION` is this name
#: bound under the vocabulary of a gap distribution, not a second literal: the two detectors
#: are applying one §6.10 rule to two distributions, and a run where they disagreed about the
#: floor would suppress a z-score in one detector and compute it in the other.
MIN_DELTA_POPULATION = 8


class MetricPolarity(str, Enum):
    """What kind of quantity a metric measures — §6.6 D1's `revenue|cost|ratio|count`.

    Semantic, not directional: it says what the number *is*, which is what lets a renderer
    choose a word (*"revenue fell"*, *"costs rose"*, *"the margin narrowed"*). Which way the
    stored number moves when the quantity rises is `ValueSign`'s question, and the two are
    deliberately separate — see the module docstring for the `adjusted_ebitda` case that makes
    them separate.
    """

    REVENUE = "revenue"
    COST = "cost"
    RATIO = "ratio"
    COUNT = "count"


class ValueSign(str, Enum):
    """Which way the *stored* number moves when the underlying quantity rises.

    `UNVERIFIED` is a member and not an omission. `cost_of_revenue` and
    `inventory_valuation_adjustment` are cost metrics with **zero observations in this run**, and
    the only two cost metrics the corpus does print are stored negative. Assuming either
    convention for an unmeasured metric would put an invented direction on a candidate; refusing
    to answer puts a warning on it instead.
    """

    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNVERIFIED = "unverified"


class MetricFamily(str, Enum):
    """§6.6 D1's threshold families, each one a row of the plan's p75-of-|QoQ| table.

    `MACRO_PERCENT` is this module's own addition, for the two metrics §6.6's table does not
    name. It carries no thresholds, deliberately.
    """

    USD_MEASURE = "usd_measure"
    MARGIN = "margin"
    AGING_PERCENT = "aging_percent"
    HOME_COUNT = "home_count"
    MARKET_COUNT = "market_count"
    MACRO_PERCENT = "macro_percent"


@dataclass(frozen=True, slots=True)
class FamilyThresholds:
    """One row of §6.6 D1's table.

    `pct_min` does double duty exactly as the plan writes it: it is a percentage of the base for
    a metric with a base, and it is a count of percentage points for a metric already denominated
    in percent. One field, because §6.6 gives one column — *"`pct_min` / `pp_min`"* — and two
    fields would invite a family to declare a pp bound that no measurement stands behind.
    """

    #: `|Δpct| ≥ pct_min` for a non-percent metric; `|Δpp| ≥ pct_min` for a percent one.
    pct_min: float
    #: `|Δ| ≥ abs_min`, in the metric's own unit. `None` where §6.6 states no absolute arm.
    abs_min: float | None = None


#: §6.6 D1's five rows, and nothing invented for a sixth.
FAMILY_THRESHOLDS: Mapping[MetricFamily, FamilyThresholds] = {
    MetricFamily.USD_MEASURE: FamilyThresholds(pct_min=65.0, abs_min=50_000_000.0),
    MetricFamily.MARGIN: FamilyThresholds(pct_min=3.0),
    MetricFamily.AGING_PERCENT: FamilyThresholds(pct_min=19.0),
    MetricFamily.HOME_COUNT: FamilyThresholds(pct_min=40.0, abs_min=1_000.0),
    MetricFamily.MARKET_COUNT: FamilyThresholds(pct_min=6.0, abs_min=3.0),
}

#: Every one of the 26 declared metrics. `acquisition_contracts` is a `HOME_COUNT` on a measured
#: finding rather than on its declared unit: S9a recorded that *"`contracts` is not a unit — both
#: contract metrics are `homes`"*, so it is counted with the homes it counts.
METRIC_FAMILY: Mapping[str, MetricFamily] = {
    # operating counts
    "homes_sold": MetricFamily.HOME_COUNT,
    "homes_purchased": MetricFamily.HOME_COUNT,
    "homes_under_contract": MetricFamily.HOME_COUNT,
    "acquisition_contracts": MetricFamily.HOME_COUNT,
    "housing_inventory_homes": MetricFamily.HOME_COUNT,
    "market_count": MetricFamily.MARKET_COUNT,
    "pct_homes_on_market_gt_120_days": MetricFamily.AGING_PERCENT,
    # USD measures — one row, as §6.6 states it
    "revenue": MetricFamily.USD_MEASURE,
    "gaap_gross_profit": MetricFamily.USD_MEASURE,
    "cost_of_revenue": MetricFamily.USD_MEASURE,
    "inventory_balance": MetricFamily.USD_MEASURE,
    "inventory_valuation_adjustment": MetricFamily.USD_MEASURE,
    "homes_under_resale_contract": MetricFamily.USD_MEASURE,
    "adjusted_gross_profit": MetricFamily.USD_MEASURE,
    "contribution_profit": MetricFamily.USD_MEASURE,
    "contribution_profit_after_interest": MetricFamily.USD_MEASURE,
    "adjusted_ebitda": MetricFamily.USD_MEASURE,
    "holding_costs": MetricFamily.USD_MEASURE,
    "direct_selling_costs": MetricFamily.USD_MEASURE,
    "borrowing_capacity": MetricFamily.USD_MEASURE,
    # the four margins
    "gaap_gross_margin": MetricFamily.MARGIN,
    "adjusted_gross_margin": MetricFamily.MARGIN,
    "contribution_margin": MetricFamily.MARGIN,
    "adjusted_ebitda_margin": MetricFamily.MARGIN,
    # macroeconomic percentages — no measured threshold, no entry in FAMILY_THRESHOLDS
    "mortgage_rate": MetricFamily.MACRO_PERCENT,
    "home_price_appreciation": MetricFamily.MACRO_PERCENT,
}

#: §6.6 D1's story-owned polarity map, over all 26 declared metrics.
#:
#: `inventory_balance` and `homes_under_resale_contract` are `REVENUE` and not `COST`: they are
#: balance-sheet assets carried at value, and a rise in either is a rise in what the company
#: holds. `borrowing_capacity` is likewise the size of a facility, not its cost.
#: `inventory_valuation_adjustment` is a write-down and therefore a `COST`.
METRIC_POLARITY: Mapping[str, MetricPolarity] = {
    "homes_sold": MetricPolarity.COUNT,
    "homes_purchased": MetricPolarity.COUNT,
    "homes_under_contract": MetricPolarity.COUNT,
    "acquisition_contracts": MetricPolarity.COUNT,
    "housing_inventory_homes": MetricPolarity.COUNT,
    "market_count": MetricPolarity.COUNT,
    "pct_homes_on_market_gt_120_days": MetricPolarity.RATIO,
    "revenue": MetricPolarity.REVENUE,
    "gaap_gross_profit": MetricPolarity.REVENUE,
    "cost_of_revenue": MetricPolarity.COST,
    "inventory_balance": MetricPolarity.REVENUE,
    "inventory_valuation_adjustment": MetricPolarity.COST,
    "homes_under_resale_contract": MetricPolarity.REVENUE,
    "adjusted_gross_profit": MetricPolarity.REVENUE,
    "contribution_profit": MetricPolarity.REVENUE,
    "contribution_profit_after_interest": MetricPolarity.REVENUE,
    "adjusted_ebitda": MetricPolarity.REVENUE,
    "holding_costs": MetricPolarity.COST,
    "direct_selling_costs": MetricPolarity.COST,
    "borrowing_capacity": MetricPolarity.REVENUE,
    "gaap_gross_margin": MetricPolarity.RATIO,
    "adjusted_gross_margin": MetricPolarity.RATIO,
    "contribution_margin": MetricPolarity.RATIO,
    "adjusted_ebitda_margin": MetricPolarity.RATIO,
    "mortgage_rate": MetricPolarity.RATIO,
    "home_price_appreciation": MetricPolarity.RATIO,
}

#: How each metric's values are stored, over all 26.
#:
#: **The two `NEGATIVE` entries are measured, not assumed** *(verified live 2026-08-04:
#: `direct_selling_costs` 46 of 46 canonical values below zero, `holding_costs` 15 of 15)*.
#: The two `UNVERIFIED` entries are the cost metrics with no observation at all — the corpus
#: cannot say which convention they will arrive under, and the two costs it does print are
#: negative, so a default of `POSITIVE` would be a guess dressed as a reading.
#: Every other metric is `POSITIVE`: nine of the 26 have no observations either, but a count, a
#: percentage and a revenue line have no competing convention to be wrong about, while a cost
#: line has exactly one.
VALUE_SIGN: Mapping[str, ValueSign] = {
    "homes_sold": ValueSign.POSITIVE,
    "homes_purchased": ValueSign.POSITIVE,
    "homes_under_contract": ValueSign.POSITIVE,
    "acquisition_contracts": ValueSign.POSITIVE,
    "housing_inventory_homes": ValueSign.POSITIVE,
    "market_count": ValueSign.POSITIVE,
    "pct_homes_on_market_gt_120_days": ValueSign.POSITIVE,
    "revenue": ValueSign.POSITIVE,
    "gaap_gross_profit": ValueSign.POSITIVE,
    "cost_of_revenue": ValueSign.UNVERIFIED,
    "inventory_balance": ValueSign.POSITIVE,
    "inventory_valuation_adjustment": ValueSign.UNVERIFIED,
    "homes_under_resale_contract": ValueSign.POSITIVE,
    "adjusted_gross_profit": ValueSign.POSITIVE,
    "contribution_profit": ValueSign.POSITIVE,
    "contribution_profit_after_interest": ValueSign.POSITIVE,
    "adjusted_ebitda": ValueSign.POSITIVE,
    "holding_costs": ValueSign.NEGATIVE,
    "direct_selling_costs": ValueSign.NEGATIVE,
    "borrowing_capacity": ValueSign.POSITIVE,
    "gaap_gross_margin": ValueSign.POSITIVE,
    "adjusted_gross_margin": ValueSign.POSITIVE,
    "contribution_margin": ValueSign.POSITIVE,
    "adjusted_ebitda_margin": ValueSign.POSITIVE,
    "mortgage_rate": ValueSign.POSITIVE,
    "home_price_appreciation": ValueSign.POSITIVE,
}

#: What `quantity_direction` answers with. Words and not `+1`/`−1`, because the whole point of
#: the polarity map is that the arithmetic sign is *not* the answer, and a signal named
#: `direction` holding `-1` would be read as the sign by the next person to touch it.
DIRECTION_INCREASE = "increase"
DIRECTION_DECREASE = "decrease"
DIRECTION_UNCHANGED = "unchanged"

#: Warning codes this module's refusals put on a candidate. Constants because §10.1 renders
#: them and a code invented inline is a code no renderer knows about.
SIGN_CONVENTION_UNVERIFIED = "metric_sign_convention_unverified"
POLARITY_UNDECLARED = "metric_polarity_undeclared"

#: What a detector reports when it declines a metric because no threshold was ever measured for
#: its family. Paired with `thresholds_for` returning `None`.
THRESHOLD_UNMEASURED = "THRESHOLD_UNMEASURED"


def round_delta(value: float) -> float:
    """Every difference this package compares, rounded once, here."""
    return round(value, DELTA_PRECISION)


def family_of(metric_id: str) -> MetricFamily | None:
    """The threshold family, or `None` for a metric the map does not declare."""
    return METRIC_FAMILY.get(metric_id)


def thresholds_for(metric_id: str) -> FamilyThresholds | None:
    """§6.6 D1's row for a metric, or `None` when no p75 was ever measured for its family.

    Two different `None`s collapse into one deliberately — an undeclared metric and a declared
    metric in a threshold-less family are the same fact from a detector's point of view: *there
    is no measured bar here, so do not fire*. The detector's refusal names the metric, which is
    what a reader needs; distinguishing the two would be a distinction with no different action.
    """
    family = family_of(metric_id)
    if family is None:
        return None
    return FAMILY_THRESHOLDS.get(family)


def polarity_of(metric_id: str) -> MetricPolarity | None:
    return METRIC_POLARITY.get(metric_id)


def value_sign_of(metric_id: str) -> ValueSign:
    """`UNVERIFIED` for anything the map does not declare, so an unknown metric cannot get a
    direction by defaulting into the common case."""
    return VALUE_SIGN.get(metric_id, ValueSign.UNVERIFIED)


def quantity_direction(metric_id: str, delta: float) -> str | None:
    """Which way the underlying quantity moved, or `None` when the convention is unverified.

    The rule §6.6 D1 exists for: *"`direct_selling_costs` values are negative, so 'costs rose' is
    a decrease"*. A `NEGATIVE`-signed metric's direction is the **opposite** of its delta's sign,
    and no caller may reconstruct that from the number alone — which is why this takes a metric
    id and returns a word.

    `None` rather than a guess for `UNVERIFIED`, and the caller is expected to attach
    `SIGN_CONVENTION_UNVERIFIED` rather than to drop the candidate: the move is real and
    citable, and only the sentence describing it is unavailable.
    """
    sign = value_sign_of(metric_id)
    if sign is ValueSign.UNVERIFIED:
        return None
    rounded = round_delta(delta)
    if rounded == 0:
        return DIRECTION_UNCHANGED
    rising = rounded > 0 if sign is ValueSign.POSITIVE else rounded < 0
    return DIRECTION_INCREASE if rising else DIRECTION_DECREASE


def value_floor(values: Sequence[float]) -> float | None:
    """§6.6 D1's `0.10 × median(|value|)` over a metric's own history, or `None` for no history.

    A median and not a mean, because `adjusted_ebitda` swings from `+$218M` to `−$351M` inside
    four quarters and a mean over that is a number no single reading resembles. Over **every**
    canonical value of the metric — all period shapes — because the floor answers *"is this base
    small for this metric"*, and a fiscal year's magnitude is evidence about the metric's scale
    even when the step under test is a quarter.
    """
    magnitudes = sorted(abs(value) for value in values)
    if not magnitudes:
        return None
    middle = len(magnitudes) // 2
    median = (
        magnitudes[middle]
        if len(magnitudes) % 2
        else (magnitudes[middle - 1] + magnitudes[middle]) / 2
    )
    return FLOOR_FRACTION * median


__all__ = [
    "COMPARABLE_SHAPES",
    "DELTA_PRECISION",
    "DIRECTION_DECREASE",
    "DIRECTION_INCREASE",
    "DIRECTION_UNCHANGED",
    "FAMILY_THRESHOLDS",
    "FLOOR_FRACTION",
    "METRIC_FAMILY",
    "METRIC_POLARITY",
    "MIN_DELTA_POPULATION",
    "POLARITY_UNDECLARED",
    "SIGN_CONVENTION_UNVERIFIED",
    "THRESHOLD_UNMEASURED",
    "VALUE_SIGN",
    "FamilyThresholds",
    "MetricFamily",
    "MetricPolarity",
    "ValueSign",
    "family_of",
    "polarity_of",
    "quantity_direction",
    "round_delta",
    "thresholds_for",
    "value_floor",
    "value_sign_of",
]
