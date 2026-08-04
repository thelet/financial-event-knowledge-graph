"""What §6.10's score terms need from the canonical series and a candidate does not carry.

Responsibility: turn `CanonicalPoint`s into the two distributions §6.10 scores against — a
metric's own **delta** distribution, per period shape, and its per-slot **corroboration** — and
nothing else. No weight, no ordering, no dedup rule lives here.

**Why this module exists at all.** §6.10's first three terms are `|z|` against *"the metric's own
delta history"*, a magnitude against *"the metric's own p90"*, and `n_docs`. A `StoryCandidate`
carries none of the three: `metric_move` publishes `delta` but no dispersion and no percentile, and
`n_docs` lives on `CanonicalPoint` (`story/core/series.py`, whose docstring already calls it a
*"ranking input"*). So ranking reads the series a second time. It does **not** read the graph —
the points are passed in, exactly as they are to every detector.

**The delta population is `trend_reversal.py`'s, restated.** A run is a maximal stretch of
one-shape points whose every adjacent pair `comparable` permits under `ClaimKind.MOVEMENT`, and
the population is every step inside those runs. Restated and not imported because
`tests/story/test_story_package_structure.py::test_no_stage_imports_another_stage` forbids
`story.stages.ranking` importing `story.stages.detection`, and the honest way to carry a
duplicated rule is to pin it: `test_story_ranking.py` asserts live that the σ computed here
equals the `sigma` signal every `trend_reversal` candidate publishes, for all eleven of them. A
silent divergence between the detector's dispersion and the ranker's would mean a candidate
fired against one spread and was scored against another.

**The 8-point floor is §6.10's and is applied here, not synthesised around.** Below
`MIN_DELTA_POPULATION` no z is produced — `holding_costs` has 4 deltas, `homes_under_contract` 4,
`borrowing_capacity` 0 — and the score simply has no magnitude term rather than a fabricated one.
The floor is restated from `detector_config.MIN_DELTA_POPULATION` for the same import reason, and
the two are asserted equal by test.

**Shape-matched populations are how the S3 hand-off is discharged.** S3 recorded that *"112 of
`metric_move`'s 227 candidates are twelve-month steps judged against quarter-over-quarter
percentiles"* and left it to §6.10. A twelve-month step is scored here against the twelve-month
delta distribution of its own metric, so an annual restatement is measured against annual steps
and stops looking enormous; and where a shape has fewer than eight steps, it loses the 0.40 term
outright instead of borrowing a quarterly one.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Mapping, Sequence

from story.core.periods import PeriodShape
from story.core.series import (
    DELTA_PRECISION,
    CanonicalPoint,
    CanonicalSeries,
    ClaimKind,
    ComparabilityAuthority,
    build_series,
    comparable,
)

#: §6.10's *"fewer than ~8 points"*, the floor below which `magnitude_z` is null and not
#: computed. Restated from `story.stages.detection.detector_config.MIN_DELTA_POPULATION`
#: because a stage may not import a sibling stage; `test_story_ranking.py` asserts the two
#: agree, and `cross_metric_divergence.MIN_POPULATION` is already the same number for the same
#: reason.
MIN_DELTA_POPULATION = 8

#: The smallest population a p90 is taken over. Two points make a percentile arithmetically
#: (`statistics.quantiles` requires two) but not meaningfully; four is the smallest count at
#: which the 90th percentile is not simply the maximum under inclusive interpolation. Below it
#: the units term is suppressed like the z term, rather than reported as a ratio against one
#: number.
MIN_PERCENTILE_POPULATION = 4


@dataclass(frozen=True, slots=True)
class SlotFacts:
    """One canonical slot, reduced to what ranking asks of it.

    `n_docs` is the corroboration term's whole input, and it is read from the point rather than
    recounted from a package downstream, which is what `CanonicalPoint`'s own docstring requires.
    """

    metric_id: str
    period_key: str
    shape: PeriodShape
    anchor_date: str | None
    value: float | None
    unit: str
    n_docs: int


@dataclass(frozen=True, slots=True)
class DeltaDistribution:
    """One metric at one period shape: the steps it actually took, characterised.

    `unit` is carried because it is what makes `p90_abs_delta` mean anything to a reader — a p90
    of 4.1 is four percentage points for a margin and four dollars for a profit measure.
    """

    metric_id: str
    shape: PeriodShape
    unit: str
    population: int
    mean: float
    sigma: float
    #: p90 of `|Δ|` in the metric's presentation unit, `None` below `MIN_PERCENTILE_POPULATION`.
    p90_abs_delta: float | None

    @property
    def characterises_a_spread(self) -> bool:
        """§6.10's floor, as a property. A σ over three deltas is a number, not a dispersion."""
        return self.population >= MIN_DELTA_POPULATION and self.sigma > 0.0

    def z_for(self, delta: float) -> float | None:
        """`(Δ − μ)/σ` against this metric's own steps, or `None` where §6.10 forbids one.

        Mean-centred, matching `cross_metric_divergence`'s own z — the only z this repository
        already publishes — rather than `Δ/σ`. On a series with a drift the two differ, and the
        question §6.10 asks is *"is this step unusual for this metric"*, not *"is it large".*
        """
        if not self.characterises_a_spread:
            return None
        return (delta - self.mean) / self.sigma

    def own_units_ratio(self, delta: float) -> float | None:
        """§6.10's second term: this step against the metric's own 90th-percentile step.

        **`|Δ|/p90(|Δ|)` and not §6.10's literal `Δpct/p90`, and the reason is a base, not a
        percentile.** Every defect this corpus has produced on the relative form comes from what
        `Δpct` divides *by*: P10 read a 3.0 pp bar as a 3.0 % relative one and lost a factor of
        twelve; `adjusted_ebitda 2021Q4 → 2022Q1` is `+43,900%` off a $0.4M base, which §6.6 D1
        answers with a floor; and a step across zero yields `−221%`, which R4a (`2937b58`)
        removed from `delta_pct` outright — 92 of the 221 `metric_move` quotients went with it,
        45 for crossing zero and 70 for being a percent metric already. Dividing by the metric's **own p90** already does the work the
        relative form was there for — it normalises across metrics of wildly different scale —
        and it does it without a base at all, so it is defined for a margin, for a profit measure
        and for the sign flip that is the most newsworthy step in the corpus.

        Measured consequence *(2026-08-04)*: §6.3's F1, `adjusted_ebitda` crossing zero in
        2022Q3, carries **no `delta_pct` at all** after that repair, and neither do 114 other
        candidates. Under the literal reading F1 loses the whole 0.20 term and falls from **16th
        to 37th of 262 — out of the top decile — for having crossed zero**, which is the opposite
        of what a magnitude term is for.
        """
        if self.p90_abs_delta is None or self.p90_abs_delta <= 0.0:
            return None
        return abs(delta) / self.p90_abs_delta


@dataclass(frozen=True, slots=True)
class MetricHistory:
    """The whole run's answer to *"what is normal for this metric?"*, resolved once.

    Immutable and passed by argument, never cached in a module global: §6.10's reproducibility
    requirement is that two runs over one graph produce one ranking, and a process-lifetime cache
    keyed on nothing is how the second run stops being a run.
    """

    distributions: Mapping[tuple[str, PeriodShape], DeltaDistribution]
    slots: Mapping[tuple[str, str], SlotFacts]
    #: Per metric, the earliest period whose canonical value is negative, or `None` for a metric
    #: that never printed one. §6.10's `is_first_occurrence` — *"the first negative value ever"*.
    first_negative_period: Mapping[str, str | None]

    def distribution(self, metric_id: str, shape: PeriodShape) -> DeltaDistribution | None:
        return self.distributions.get((metric_id, shape))

    def slot(self, metric_id: str, period_key: str) -> SlotFacts | None:
        return self.slots.get((metric_id, period_key))


def p90(values: Sequence[float]) -> float | None:
    """The 90th percentile, by inclusive linear interpolation, or `None` below the floor.

    `statistics.quantiles` and not a hand-rolled index, because the standard library already
    owns the interpolation rule and a second one would be a second answer. `method="inclusive"`
    is the one that treats the sample as the population, which is what these deltas are — the
    metric's steps are not a draw from a larger set of steps that were not taken.
    """
    if len(values) < MIN_PERCENTILE_POPULATION:
        return None
    return statistics.quantiles(sorted(values), n=10, method="inclusive")[-1]


def runs_of(
    series: CanonicalSeries,
    shape: PeriodShape,
    authority: ComparabilityAuthority | None = None,
) -> tuple[tuple[CanonicalPoint, ...], ...]:
    """Maximal stretches of one shape whose every adjacent pair `comparable` permits.

    `trend_reversal._runs`, restated (see the module docstring). Splitting at a refused pair
    rather than stepping over it is what keeps a population from spanning a hole or a change of
    formula version: `adjusted_gross_profit`'s 2021Q4→2022Q1 step is refused by R6, and a
    dispersion taken across it would characterise two definitions as one.
    """
    points = series.valued_points(shape)
    runs: list[tuple[CanonicalPoint, ...]] = []
    current: list[CanonicalPoint] = []
    for earlier, later in zip(points, points[1:]):
        if comparable(
            later, earlier, claim=ClaimKind.MOVEMENT, authority=authority, series=series
        ):
            if not current:
                current = [earlier]
            current.append(later)
        elif current:
            runs.append(tuple(current))
            current = []
    if current:
        runs.append(tuple(current))
    return tuple(runs)


def build_metric_history(
    points: Sequence[CanonicalPoint],
    *,
    authority: ComparabilityAuthority | None = None,
) -> MetricHistory:
    """Every distribution and every slot fact §6.10 scores against, from one set of points.

    The shapes walked are the ones the run actually contains, minus `OTHER`, rather than a
    restated `COMPARABLE_SHAPES` — R3 has already refused `OTHER` by the time a candidate exists,
    and deriving the list from the data is one fewer constant to keep in agreement with a sibling
    stage.
    """
    series_map = build_series(points)
    shapes = sorted(
        {point.period.shape for point in points if point.period.shape is not PeriodShape.OTHER},
        key=lambda shape: shape.value,
    )

    distributions: dict[tuple[str, PeriodShape], DeltaDistribution] = {}
    slots: dict[tuple[str, str], SlotFacts] = {}
    first_negative: dict[str, str | None] = {}

    for metric_id, series in sorted(series_map.items()):
        valued = series.valued_points()
        first_negative[metric_id] = next(
            (point.period.key for point in valued if point.value is not None and point.value < 0),
            None,
        )
        for point in series.points:
            slots[(metric_id, point.period.key)] = SlotFacts(
                metric_id=metric_id,
                period_key=point.period.key,
                shape=point.period.shape,
                anchor_date=point.period.anchor_date,
                value=point.value,
                unit=point.unit,
                n_docs=point.n_docs,
            )
        for shape in shapes:
            distribution = _distribution(series, shape, authority=authority)
            if distribution is not None:
                distributions[(metric_id, shape)] = distribution

    return MetricHistory(
        distributions=distributions, slots=slots, first_negative_period=first_negative
    )


def _distribution(
    series: CanonicalSeries,
    shape: PeriodShape,
    *,
    authority: ComparabilityAuthority | None,
) -> DeltaDistribution | None:
    """One metric at one shape, or `None` when it took no comparable step at that shape."""
    deltas: list[float] = []
    unit = ""
    for run in runs_of(series, shape, authority):
        for earlier, later in zip(run, run[1:]):
            assert earlier.value is not None and later.value is not None  # `valued_points`
            deltas.append(round(later.value - earlier.value, DELTA_PRECISION))
            unit = earlier.unit
    if not deltas:
        return None
    return DeltaDistribution(
        metric_id=series.metric_id,
        shape=shape,
        unit=unit,
        population=len(deltas),
        mean=statistics.fmean(deltas),
        # Population and not sample σ, matching both dispersion detectors: these are the steps
        # the metric took, not a sample of steps it might have taken.
        sigma=statistics.pstdev(deltas),
        p90_abs_delta=p90([abs(delta) for delta in deltas]),
    )


__all__ = [
    "MIN_DELTA_POPULATION",
    "MIN_PERCENTILE_POPULATION",
    "DeltaDistribution",
    "MetricHistory",
    "SlotFacts",
    "build_metric_history",
    "p90",
    "runs_of",
]
