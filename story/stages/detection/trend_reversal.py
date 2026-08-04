"""§6.6 D2 — `trend_reversal`, and deliberately nothing else.

Responsibility: find the point in a canonical series where a run of steps in one direction is
answered by a step in the other, large enough to be worth writing. It owns no comparability rule
(R1–R10 are `story/core/series.py`'s), no threshold table (`detector_config.py`'s), no
canonicalisation, no ranking and no prose. The firing arithmetic of §6.6 D1 is **imported** from
`metric_move.py` rather than restated, because D2's magnitude condition is literally *"`|d[i]| ≥
max(D1 threshold, 1.0 × σ(d))`"* and two detectors sharing a rule must share its code.

**Zero is specified, not left open.** §6.6 D2 requires it: *"a delta of exactly 0 has no sign and
breaks a run rather than continuing or reversing it."* A zero delta at `i` cannot fire, and a
zero delta anywhere in the prior run cancels the run. Live this changes nothing — R8 refuses any
step inside presentation tolerance, and a step of exactly zero is inside every tolerance in the
corpus, so a zero delta never reaches this rule — so it is driven synthetically. It is written
anyway because R8's tolerance is a property of a unit and a scale, and a metric printed with a
tolerance of zero would slip past it.

**`max(D1 threshold, σ)` is a maximum over a disjunction, so it is expressed as a conjunction.**
§6.6 D1's bar is not one number — it is *"pp, or pct, or abs"* — and `max()` of a disjunction has
no meaning. The reading implemented here is the only one that preserves both halves: the step
must **fire under D1** *and* be at least `1.0 × σ(d)`. On a percent metric, where D1 is a single
pp bar, the two readings coincide exactly.

**σ is a population standard deviation over the metric's own comparable deltas of that shape**,
and it is not computed at all below `MIN_DELTA_POPULATION` — §6.10's rule that *"`magnitude_z` is
suppressed to `null`, not computed, when a metric has fewer than ~8 points"*. A σ over three
deltas is a number, not a dispersion, and firing against one would be the small-base defect §6.6
D1 already records in its own floor.

**§6.6 D2's stated exclusion excludes nothing, and the reason is worth recording.** The plan says
*"exclude any metric whose `σ(d)` is under one presentation unit; `market_count` alternates ±0 and
produces pure noise."* Measured live 2026-08-04, `market_count`'s twenty adjacent instant deltas
are `3, 0, 6, 12, 5, 0, 1, 6, 0, 2, 0, 0, 0, −3, 0×6` — it steps up through 2021–22 and then goes
flat; it does not alternate. σ over those is **3.26** and σ over the six that survive R1–R10 is
**4.53**, both far above the one-market tolerance, and **no metric in the run at any shape has
σ(d) below its presentation unit**. The rule is implemented as written and fires on nothing; what
actually removes `market_count` is R8 — which refuses the twelve zero steps and the one-market
step as rounding — R10, which refuses the twelve-month `2018-12-31 → 2019-12-31` step — and then
`MIN_DELTA_POPULATION`, which refuses the remaining six as too few to characterise.

**Census, measured live against `graph-v1-0483dc6b4b10` on 2026-08-04: 11 candidates across 9
metrics** — 9 quarterly and 2 instant — including both of the 2022Q3 candidates §6.3 names.
Ungated (no σ, no D1 bar) the same scan gives **71 across 11 metrics** honouring R1–R10, or **76
across 12** over adjacent rows with no comparability rule at all. §6.6 D2 states **84 across 13**;
that figure does not reproduce under any zero-handling or shape grouping tried, and the closest
variant is recorded here rather than fitted to.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence

from story.core.models import StoryCandidate
from story.core.periods import PeriodShape
from story.core.series import (
    CanonicalPoint,
    CanonicalSeries,
    ClaimKind,
    ComparabilityAuthority,
    Ok,
    build_series,
    comparable,
    presentation_tolerance,
)
from story.stages.detection.canonicalization import ObservationLoad, canonicalize
from story.stages.detection.detector_config import (
    COMPARABLE_SHAPES,
    MIN_DELTA_POPULATION,
    THRESHOLD_UNMEASURED,
    FamilyThresholds,
    quantity_direction,
    round_delta,
    thresholds_for,
    value_floor,
)
from story.stages.detection.metric_move import (
    SERIES_INCOMPLETE,
    MoveMeasurement,
    candidate_from_move,
    measure_move,
)

DETECTOR_ID = "detector:trend_reversal"
DETECTOR_VERSION = "1.0.0"
STORY_TYPE = "trend_reversal"

#: §6.6 D2's `min_run`: how many prior deltas must all oppose the reversing one.
MIN_RUN = 2

#: §6.6 D2's `1.0 × σ(d)`. Named because the plan states the coefficient explicitly and a bare
#: `1.0` in the comparison would read as an accident.
SIGMA_MULTIPLE = 1.0

#: The refusals this detector can raise on its own account.
POPULATION_TOO_SMALL = "POPULATION_TOO_SMALL"
LOW_VARIANCE_PRESENTATION_NOISE = "LOW_VARIANCE_PRESENTATION_NOISE"


@dataclass(frozen=True, slots=True)
class TrendReversalRefusal:
    """One metric-and-shape this detector declined to scan, and why.

    Carried as a value for the reason `ObservationLoad.unreadable` is a field: *not scanned* and
    *scanned and quiet* are different findings, and a run that reported them the same way would
    let a suppressed series read as a stable one.
    """

    metric_id: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class TrendReversalResult:
    candidates: tuple[StoryCandidate, ...] = ()
    refusals: tuple[TrendReversalRefusal, ...] = ()


def reverses(deltas: Sequence[float], *, min_run: int = MIN_RUN) -> bool:
    """§6.6 D2's sign condition over `min_run + 1` already-rounded, already-consecutive deltas.

    The last element is the reversing step; every earlier one must oppose it. **A zero anywhere
    in the window is fatal**, at the reversing step because a zero has no sign to oppose, and in
    the prior run because a flat quarter is not a continuation of a trend — that is the plan's
    own specification and the reason this is a named function with its own test rather than a
    condition inside the scan.
    """
    if len(deltas) != min_run + 1:
        return False
    current = deltas[-1]
    if current == 0:
        return False
    return all(prior != 0 and (prior > 0) != (current > 0) for prior in deltas[:-1])


def detect_trend_reversals(
    points: Sequence[CanonicalPoint],
    *,
    graph_run_id: str,
    unreadable: Sequence[tuple[str, str]] = (),
    min_run: int = MIN_RUN,
    shapes: Iterable[PeriodShape] = COMPARABLE_SHAPES,
    authority: ComparabilityAuthority | None = None,
) -> TrendReversalResult:
    """Every §6.6 D2 reversal in the canonical points, plus what was not scanned and why."""
    refused = {metric_id for metric_id, _ in unreadable}
    ordered_shapes = tuple(shapes)
    candidates: list[StoryCandidate] = []
    refusals = [
        TrendReversalRefusal(metric_id=metric_id, reason=SERIES_INCOMPLETE, detail=detail)
        for metric_id, detail in sorted(unreadable)
    ]

    for metric_id, series in sorted(build_series(points).items()):
        if metric_id in refused:
            continue
        thresholds = thresholds_for(metric_id)
        if thresholds is None:
            refusals.append(
                TrendReversalRefusal(
                    metric_id=metric_id,
                    reason=THRESHOLD_UNMEASURED,
                    detail=(
                        f"{metric_id} belongs to no threshold family §6.6 D1 measured a p75 of "
                        "|QoQ| for, and D2's magnitude gate is `max(D1 threshold, σ)`"
                    ),
                )
            )
            continue
        floor = value_floor(
            [point.value for point in series.valued_points() if point.value is not None]
        )
        for shape in ordered_shapes:
            found, refusal = _scan(
                series,
                shape,
                thresholds=thresholds,
                floor=floor,
                min_run=min_run,
                graph_run_id=graph_run_id,
                authority=authority,
            )
            candidates.extend(found)
            if refusal is not None:
                refusals.append(refusal)
    return TrendReversalResult(candidates=tuple(candidates), refusals=tuple(refusals))


def detect_trend_reversals_from_load(
    load: ObservationLoad,
    *,
    graph_run_id: str,
    min_run: int = MIN_RUN,
    shapes: Iterable[PeriodShape] = COMPARABLE_SHAPES,
    authority: ComparabilityAuthority | None = None,
) -> TrendReversalResult:
    """`load_observations` → §6.1 → D2, with the loader's own refusals carried through."""
    return detect_trend_reversals(
        canonicalize(load.records),
        graph_run_id=graph_run_id,
        unreadable=load.unreadable,
        min_run=min_run,
        shapes=shapes,
        authority=authority,
    )


# -- the scan ------------------------------------------------------------------------------


def _runs(
    series: CanonicalSeries,
    shape: PeriodShape,
    authority: ComparabilityAuthority | None,
) -> list[tuple[CanonicalPoint, ...]]:
    """Maximal stretches of one shape whose every adjacent pair `comparable` permits.

    A run and not the whole point list, because §6.6 D2 asks about **consecutive** deltas and a
    step R10 refused is a hole. Splitting there rather than skipping the pair is the difference
    between *"the two quarters before this one both fell"* and *"the two quarters I could measure
    both fell"* — `adjusted_gross_profit` alone has its 2021Q4→2022Q1 step refused by R6, and a
    scan that stepped over it would build a run across a change of definition.
    """
    points = series.valued_points(shape)
    runs: list[tuple[CanonicalPoint, ...]] = []
    current: list[CanonicalPoint] = []
    for earlier, later in zip(points, points[1:]):
        verdict = comparable(
            later, earlier, claim=ClaimKind.MOVEMENT, authority=authority, series=series
        )
        if isinstance(verdict, Ok):
            if not current:
                current = [earlier]
            current.append(later)
        elif current:
            runs.append(tuple(current))
            current = []
    if current:
        runs.append(tuple(current))
    return runs


def _scan(
    series: CanonicalSeries,
    shape: PeriodShape,
    *,
    thresholds: FamilyThresholds,
    floor: float | None,
    min_run: int,
    graph_run_id: str,
    authority: ComparabilityAuthority | None,
) -> tuple[list[StoryCandidate], TrendReversalRefusal | None]:
    """One metric at one shape: σ first, then every window that could reverse."""
    runs = _runs(series, shape, authority)
    population = [
        round_delta(later.value - earlier.value)  # type: ignore[operator]
        for run in runs
        for earlier, later in zip(run, run[1:])
    ]
    if not population:
        return [], None
    if len(population) < MIN_DELTA_POPULATION:
        return [], TrendReversalRefusal(
            metric_id=series.metric_id,
            reason=POPULATION_TOO_SMALL,
            detail=(
                f"{series.metric_id} has {len(population)} comparable {shape.value} deltas, "
                f"below the {MIN_DELTA_POPULATION} §6.10 requires before a dispersion is "
                "computed; σ over that many is a number, not a spread"
            ),
        )

    sigma = statistics.pstdev(population)
    tolerance = max(
        presentation_tolerance(point.unit, point.scale)
        for point in series.valued_points(shape)
    )
    if sigma < tolerance:
        return [], TrendReversalRefusal(
            metric_id=series.metric_id,
            reason=LOW_VARIANCE_PRESENTATION_NOISE,
            detail=(
                f"{series.metric_id} {shape.value} deltas have σ = {sigma} against a "
                f"presentation tolerance of {tolerance}; §6.6 D2 excludes a series whose whole "
                "spread is inside one printed unit"
            ),
        )

    candidates: list[StoryCandidate] = []
    for run in runs:
        deltas = [
            round_delta(later.value - earlier.value)  # type: ignore[operator]
            for earlier, later in zip(run, run[1:])
        ]
        for index in range(min_run, len(deltas)):
            window = deltas[index - min_run: index + 1]
            if not reverses(window, min_run=min_run):
                continue
            if abs(window[-1]) < SIGMA_MULTIPLE * sigma:
                continue
            measurement = measure_move(
                run[index], run[index + 1], thresholds=thresholds, floor=floor
            )
            if not measurement.fires:
                continue
            # The reversing step joins `run[index] → run[index + 1]`, and the prior run reaches
            # back `min_run` steps: `min_run + 2` points in all.
            anchors = run[index - min_run: index + 2]
            candidates.append(
                _candidate(
                    measurement,
                    anchors,
                    window,
                    sigma=sigma,
                    population=len(population),
                    min_run=min_run,
                    graph_run_id=graph_run_id,
                    authority=authority,
                    series=series,
                )
            )
    return candidates, None


def _candidate(
    measurement: MoveMeasurement,
    anchors: Sequence[CanonicalPoint],
    window: Sequence[float],
    *,
    sigma: float,
    population: int,
    min_run: int,
    graph_run_id: str,
    authority: ComparabilityAuthority | None,
    series: CanonicalSeries,
) -> StoryCandidate:
    """One reversal, built through D1's own candidate constructor.

    **Every point of the run is an anchor**, not only the two the reversing step joins: the claim
    is *"it had been going one way for `min_run` steps and then went the other"*, and a candidate
    anchored on one step would ask §10 for the evidence of a claim it is not making.

    `prior_direction` is computed through `quantity_direction`, like `direction`, so a
    negative-valued cost metric reports *"costs had been rising"* rather than the sign of its
    deltas. `prior_delta_1 … n` are the raw run, so a reader can check the word against the
    numbers.
    """
    comparability_warnings: set[str] = set()
    for earlier, later in zip(anchors, anchors[1:]):
        verdict = comparable(
            later, earlier, claim=ClaimKind.MOVEMENT, authority=authority, series=series
        )
        if isinstance(verdict, Ok):
            comparability_warnings.update(warning.code for warning in verdict.warnings)

    extra: dict[str, bool | int | float | str] = {
        "min_run": min_run,
        "sigma": sigma,
        "delta_population": population,
        "prior_run_delta": round_delta(sum(window[:-1])),
    }
    for offset, prior in enumerate(window[:-1], start=1):
        extra[f"prior_delta_{offset}"] = prior
    prior_direction = quantity_direction(measurement.metric_id, window[0])
    if prior_direction is not None:
        extra["prior_direction"] = prior_direction

    return candidate_from_move(
        measurement,
        anchors,
        tuple(sorted(comparability_warnings)),
        graph_run_id=graph_run_id,
        detector_id=DETECTOR_ID,
        detector_version=DETECTOR_VERSION,
        story_type=STORY_TYPE,
        extra_signals=extra,
    )


__all__ = [
    "DETECTOR_ID",
    "DETECTOR_VERSION",
    "LOW_VARIANCE_PRESENTATION_NOISE",
    "MIN_RUN",
    "POPULATION_TOO_SMALL",
    "SIGMA_MULTIPLE",
    "STORY_TYPE",
    "TrendReversalRefusal",
    "TrendReversalResult",
    "detect_trend_reversals",
    "detect_trend_reversals_from_load",
    "reverses",
]
