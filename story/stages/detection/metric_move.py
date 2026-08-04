"""§6.6 D1 — `metric_move`, and deliberately nothing else.

Responsibility: take every pair of points that are **consecutive in a canonical series**, measure
the step between them, and turn the ones that clear their metric family's bar into a
`StoryCandidate`. It owns no comparability rule (R1–R10 are `story/core/series.py`'s), no
canonicalisation (§6.1 is `canonicalization.py`'s), no threshold or polarity table
(`detector_config.py`'s), no ranking and no prose.

**Two things this module refuses to do, and both are the point.**

*It does not read a series it cannot prove complete.* `get_metric_history` is bounded at 200 rows
and five metrics exceed it; `load_observations` pages and **refuses** a metric rather than
returning a short page, recording it in `ObservationLoad.unreadable`. A metric named there is
skipped with a `SERIES_INCOMPLETE` refusal, because every delta over an unknown series is a delta
across an unknown hole.

*It does not take a delta across a hole it can see.* Every pair goes through
`comparable(..., claim=ClaimKind.MOVEMENT, series=series)`. R10 is what stops
`pct_homes_on_market_gt_120_days` `2021-12-31 → 2022-12-31` — neighbouring **rows**, twelve months
apart, `8% → 55%` — reading as a `+47 pp` quarterly jump. A detector that re-implemented *"one
quarter apart"* would be a second, unreviewed copy of that rule.

**A correction to §6.6 D1's firing expression, and it moves the census.** As written the rule is

    fire if (unit == percent and |Δpp| ≥ pp_min) or (|Δpct| ≥ pct_min or |Δ| ≥ abs_min)

with `pp_min` and `pct_min` the *same column* of the threshold table. Taken literally the second
arm also applies to a percentage metric, so `gaap_gross_margin`'s **3.0 pp** bar doubles as a
**3.0 % relative** bar — which at a typical base of 8.2 pp is a bar of 0.25 pp, an order of
magnitude below the p75 the number was measured as. Live that is the difference between
**37 firings and 11** on `gaap_gross_margin`, 36 and 18 on `adjusted_gross_margin`, 10 and 4 on
`pct_homes_on_market_gt_120_days`, and **310 and 227** overall. The column is stated as
*"`pct_min` / `pp_min`"* — one number that means percentage points for a metric already
denominated in percent — so this module applies **the pp arm to a percent metric and the pct/abs
arms to every other**, and reports `Δpct` on the candidate without letting it fire. §13.3 calls
percent-versus-percentage-point the single most likely factual error in the whole pipeline; a
threshold that quietly means both is the same mistake one layer earlier.

**Census, measured live against `graph-v1-0483dc6b4b10` on 2026-08-04**, over the five shapes R3
admits: **227 candidates across 16 metrics** from 425 comparable steps — 93 of 230 quarter steps,
22 of 44 instant steps, and 112 of 151 fiscal-year and year-to-date steps. The last figure is
recorded rather than tuned: §6.6's thresholds are **quarter-over-quarter** percentiles, so a
twelve-month step judged against them clears the bar 74% of the time. Narrowing the scan to
quarters and instants was rejected — §6.2 lists instant series as first-class and nothing in §6.6
restricts the rule to one shape — but the shape is on every candidate as a signal, and §6.10's
ranking is where an annual restatement of a quarterly move should lose.

**Two states in which this module publishes no `delta_pct` at all, beyond §6.6 D1's floor.**
Both are about the number a reader would be handed, and neither changes which steps fire.

*Across zero, because §13.3's third gate says the quotient is not a claim.*
`adjusted_ebitda_margin 2022Q2 → 2022Q3` is `5.2% → −6.3%`; the relative change is `−221%`, which
is arithmetically defined and rhetorically meaningless, and it shipped as a signal on a real
candidate — §13.3 uses this very pair as its worked example of the number nobody may write. The
predicate is `story.core.numerals.relative_change_across_zero` — the same one `delta_relative`
raises on — rather than a second copy here: one arithmetic, one owner. The candidate carries
`RELATIVE_CHANGE_ACROSS_ZERO` so the absent number is a stated refusal rather than a gap that
reads like the floor's. **45 of 227 candidates** carried such a `delta_pct` before this
*(measured live 2026-08-04)*.

*On a percent metric, because a percentage of a percentage is the error §13.3 calls the most
likely one in the pipeline.* `contribution_margin 2022Q3 → 2022Q4` is `−0.7% → −7.2%` and made
`delta_pct = −928.57` next to a `delta_pp` of `−6.5`, two contradictory percentages under
differently-spelled keys — and it never crossed zero, so the gate above would not have caught it.
The floor is no guard here either: `0.10 × median(|v|)` is a USD-shaped rule and lands at 0.36 pp
for `adjusted_ebitda_margin` and 0.455 pp for `contribution_margin`, so it admits almost every
margin base a story would be about. `delta_pp` already says what moved, the pct arm never fires
for a percent metric anyway (below), and the suppression is therefore free of any firing effect.
**70 of 227** carried one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from story.core.keys import candidate_id
from story.core.models import EvidenceRequest, StoryCandidate
from story.core.numerals import relative_change_across_zero
from story.core.periods import PeriodShape
from story.core.series import (
    CanonicalPoint,
    CanonicalSeries,
    ClaimKind,
    ComparabilityAuthority,
    Ok,
    build_series,
    comparable,
)
from story.stages.detection.canonicalization import POLICY_VERSION, ObservationLoad, canonicalize
from story.stages.detection.detector_config import (
    COMPARABLE_SHAPES,
    SIGN_CONVENTION_UNVERIFIED,
    THRESHOLD_UNMEASURED,
    FamilyThresholds,
    polarity_of,
    quantity_direction,
    round_delta,
    thresholds_for,
    value_floor,
)

DETECTOR_ID = "detector:metric_move"
DETECTOR_VERSION = "1.0.0"
STORY_TYPE = "metric_move"

#: The unit that makes a difference a *percentage point* difference rather than a percentage.
PERCENT_UNIT = "percent"

#: The arms of §6.6 D1's firing rule, named so a candidate records which one it cleared. A
#: reader asking *"why is this a candidate"* gets `pp` or `abs` rather than a boolean.
ARM_PERCENTAGE_POINTS = "pp"
ARM_RELATIVE_PERCENT = "pct"
ARM_ABSOLUTE = "abs"

#: The step ran through zero, so §13.3's third gate refused its relative change. On the candidate
#: rather than in a log because the absent `delta_pct` would otherwise be indistinguishable from
#: the floor's absence, and the two are refusals for different reasons.
RELATIVE_CHANGE_ACROSS_ZERO = "relative_change_across_zero"

#: A series whose completeness the loader could not prove. Not a `Refuse` from `core/series.py`:
#: that type answers *"may these two points be compared"*, and this answers *"may this metric be
#: read at all"*.
SERIES_INCOMPLETE = "SERIES_INCOMPLETE"


@dataclass(frozen=True, slots=True)
class MoveMeasurement:
    """The arithmetic of one step, before any judgment about whether it is a story.

    A value type rather than a tuple of floats because `trend_reversal.py` reuses exactly this
    gate — §6.6 D2's magnitude condition is *"`|d[i]| ≥ max(D1 threshold, σ(d))`"* — and two
    detectors sharing a rule must share the code that implements it, not a description of it.

    `delta_pct` is `None` in three states and only one of them is §6.6 D1's own: the base below
    the metric's floor (*"only if `|v0| ≥ floor(metric)`, else null"*), the step across zero
    (§13.3's third gate), and a metric already denominated in percent. It is `None` rather than a
    large number because `adjusted_ebitda 2021Q4 → 2022Q1` really is `+43,900%` and that number is
    arithmetic about a rounding, not a measurement of a business — and the same is true of the
    `−221%` a sign flip yields.
    """

    metric_id: str
    unit: str
    earlier_key: str
    later_key: str
    earlier_value: float
    later_value: float
    delta: float
    #: `Δ / |v0| × 100` when that is a number a reader may be handed, else `None`. Never a firing
    #: arm for a percent metric, and on one now never reported either.
    delta_pct: float | None
    #: `Δ` itself, and only for a metric denominated in percent. `None` says the question does
    #: not apply, which is not the same as a move of zero percentage points.
    delta_pp: float | None
    floor: float | None
    thresholds: FamilyThresholds
    #: Which arms of §6.6 D1 this step cleared, in rule order. Empty means it did not fire.
    fired_on: tuple[str, ...] = ()

    @property
    def fires(self) -> bool:
        return bool(self.fired_on)

    @property
    def crosses_zero(self) -> bool:
        """Strictly across, so a step that lands **on** zero has not crossed it.

        `adjusted_ebitda 2021Q3 → 2021Q4` is `+$34.5M → +$0.4M`: it approaches zero and does not
        reach it, and `2022Q2 → 2022Q3` is `+$218M → −$211M`, which does. §6.3's F1 is the second
        and the flag exists to tell them apart.
        """
        return (self.earlier_value > 0 > self.later_value) or (
            self.earlier_value < 0 < self.later_value
        )

    @property
    def relative_change_refused(self) -> bool:
        """§13.3's third gate over this step: is `Δ/|v0|` a quotient nobody may read?

        Wider than `crosses_zero` by exactly one case — `v0 == 0`, where the quotient is not
        meaningless but undefined — and that is why this delegates to
        `story.core.numerals.relative_change_across_zero` instead of reusing the flag above.
        The two questions are *"did §6.3's F1 happen"* and *"may a percentage be printed"*, and
        a single predicate answering both would eventually be corrected for one of them.
        """
        return relative_change_across_zero(self.earlier_value, self.later_value)


@dataclass(frozen=True, slots=True)
class MetricMoveRefusal:
    """One metric this detector declined to scan, and why.

    A value and not a log line, for the reason `ObservationLoad.unreadable` is a field: a metric
    that was skipped and a metric that held no move are different findings, and a run that
    reported them the same way would let a silently short series read as a quiet quarter.
    """

    metric_id: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class MetricMoveResult:
    """What one scan produced: the candidates, and the metrics it refused to scan."""

    candidates: tuple[StoryCandidate, ...] = ()
    refusals: tuple[MetricMoveRefusal, ...] = ()


def measure_move(
    earlier: CanonicalPoint,
    later: CanonicalPoint,
    *,
    thresholds: FamilyThresholds,
    floor: float | None,
) -> MoveMeasurement:
    """§6.6 D1's arithmetic over two points, with the firing arms resolved.

    Every comparison is against an already-rounded delta (`round_delta`), because
    `adjusted_gross_margin 2022Q2 → 2022Q3` is `13.2 − 3.3 = −9.899999999999999` in IEEE 754 and
    a bar tested on the raw subtraction decides on residues at the seventeenth digit.

    **That rounding is load-bearing at a bar, not only cosmetic, and the run contains the case.**
    `core/series.py`'s `within_tolerance` records a blast radius of one for R8; this is the
    separate one for the thresholds. Measured over every comparable step in
    `graph-v1-0483dc6b4b10` *(verified 2026-08-04)*: **8 steps land exactly on their arm's bar
    after rounding**, and of those exactly one is a tie the raw subtraction would have lost —
    `adjusted_gross_margin 2024Q2 → 2024Q3` (`10.2% → 7.2%`), whose raw delta is
    `−2.999999999999999` against a 3.0 pp bar. Its candidate — id ending `:52a448345320` — exists
    only because the comparison happens after `round_delta`. All 8 ties resolve in favour of
    firing, because every arm below tests `>=`; that is deliberate — a step that moved exactly the
    measured p75 met the bar — and it is recorded here rather than left for someone to rediscover
    from a census diff.

    The caller has already run `comparable`; this function does not, and could not — R10 needs
    the series and this takes two points. Keeping the arithmetic separable is what lets a test
    drive the threshold table without building a series around it.

    **`delta_pct` is what may be *published*; the pct arm still decides on the raw quotient.** A
    step across zero always exceeds 100% relative by construction, so suppressing the arm as well
    would be a change to §6.6 D1's firing rule and not to its reporting — out of scope for a
    repair, and it would not have moved today's census in any case (all 18 candidates firing on
    `pct` alone have `crosses_zero=False`, so nothing exists solely on a refused quotient). The
    candidate records the split honestly: `fired_on` says the magnitude cleared the relative bar,
    and `RELATIVE_CHANGE_ACROSS_ZERO` says the quotient is not one a draft may quote.
    """
    if earlier.value is None or later.value is None:
        raise ValueError(
            f"{earlier.metric_id}: {earlier.period.key} or {later.period.key} emits no value; "
            "R7 refuses the pair and a delta may not be taken over it"
        )
    delta = round_delta(later.value - earlier.value)
    is_percent = earlier.unit == PERCENT_UNIT
    base = abs(earlier.value)
    # §6.6 D1's own gate, and the only one the firing arm sees.
    relative = (
        round_delta(delta / base * 100.0)
        if base > 0 and floor is not None and base >= floor
        else None
    )
    readable = (
        relative
        if not is_percent and not relative_change_across_zero(earlier.value, later.value)
        else None
    )

    arms: list[str] = []
    if is_percent:
        if abs(delta) >= thresholds.pct_min:
            arms.append(ARM_PERCENTAGE_POINTS)
    elif relative is not None and abs(relative) >= thresholds.pct_min:
        arms.append(ARM_RELATIVE_PERCENT)
    if thresholds.abs_min is not None and abs(delta) >= thresholds.abs_min:
        arms.append(ARM_ABSOLUTE)

    return MoveMeasurement(
        metric_id=earlier.metric_id,
        unit=earlier.unit,
        earlier_key=earlier.period.key,
        later_key=later.period.key,
        earlier_value=earlier.value,
        later_value=later.value,
        delta=delta,
        delta_pct=readable,
        delta_pp=delta if is_percent else None,
        floor=floor,
        thresholds=thresholds,
        fired_on=tuple(arms),
    )


def detect_metric_moves(
    points: Sequence[CanonicalPoint],
    *,
    graph_run_id: str,
    unreadable: Sequence[tuple[str, str]] = (),
    shapes: Iterable[PeriodShape] = COMPARABLE_SHAPES,
    authority: ComparabilityAuthority | None = None,
) -> MetricMoveResult:
    """Every §6.6 D1 move in the canonical points, plus the metrics that were not scanned.

    `unreadable` is `ObservationLoad.unreadable`, and it is not optional in spirit:
    `detect_metric_moves_from_load` is the call that cannot forget to pass it.
    """
    refused = {metric_id for metric_id, _ in unreadable}
    ordered_shapes = tuple(shapes)
    candidates: list[StoryCandidate] = []
    refusals = [
        MetricMoveRefusal(metric_id=metric_id, reason=SERIES_INCOMPLETE, detail=detail)
        for metric_id, detail in sorted(unreadable)
    ]

    for metric_id, series in sorted(build_series(points).items()):
        if metric_id in refused:
            continue
        thresholds = thresholds_for(metric_id)
        if thresholds is None:
            refusals.append(
                MetricMoveRefusal(
                    metric_id=metric_id,
                    reason=THRESHOLD_UNMEASURED,
                    detail=(
                        f"{metric_id} belongs to no threshold family §6.6 D1 measured a p75 of "
                        "|QoQ| for; firing would put an invented bar on a real move"
                    ),
                )
            )
            continue
        floor = value_floor([point.value for point in series.valued_points()
                             if point.value is not None])
        for shape in ordered_shapes:
            candidates.extend(
                _scan(
                    series,
                    shape,
                    thresholds=thresholds,
                    floor=floor,
                    graph_run_id=graph_run_id,
                    authority=authority,
                )
            )
    return MetricMoveResult(candidates=tuple(candidates), refusals=tuple(refusals))


def detect_metric_moves_from_load(
    load: ObservationLoad,
    *,
    graph_run_id: str,
    shapes: Iterable[PeriodShape] = COMPARABLE_SHAPES,
    authority: ComparabilityAuthority | None = None,
) -> MetricMoveResult:
    """`load_observations` → §6.1 → D1, with the loader's own refusals carried through."""
    return detect_metric_moves(
        canonicalize(load.records),
        graph_run_id=graph_run_id,
        unreadable=load.unreadable,
        shapes=shapes,
        authority=authority,
    )


# -- the scan ------------------------------------------------------------------------------


def _scan(
    series: CanonicalSeries,
    shape: PeriodShape,
    *,
    thresholds: FamilyThresholds,
    floor: float | None,
    graph_run_id: str,
    authority: ComparabilityAuthority | None,
) -> list[StoryCandidate]:
    """Every consecutive same-shape pair, judged.

    The pairs come from `valued_points(shape)`, so a `CONFLICT` slot is not a member and a pair
    can close over one. That hole is not this scan's to catch: the two points either side of a
    dropped slot are two calendar steps apart, and R10's `SERIES_GAP` measures months rather than
    counting list positions.
    """
    points = series.valued_points(shape)
    candidates: list[StoryCandidate] = []
    for earlier, later in zip(points, points[1:]):
        verdict = comparable(
            later, earlier, claim=ClaimKind.MOVEMENT, authority=authority, series=series
        )
        if not isinstance(verdict, Ok):
            continue
        measurement = measure_move(earlier, later, thresholds=thresholds, floor=floor)
        if not measurement.fires:
            continue
        candidates.append(
            candidate_from_move(
                measurement,
                (earlier, later),
                tuple(warning.code for warning in verdict.warnings),
                graph_run_id=graph_run_id,
            )
        )
    return candidates


def candidate_from_move(
    measurement: MoveMeasurement,
    window: Sequence[CanonicalPoint],
    comparability_warnings: Sequence[str],
    *,
    graph_run_id: str,
    detector_id: str = DETECTOR_ID,
    detector_version: str = DETECTOR_VERSION,
    story_type: str = STORY_TYPE,
    extra_signals: dict[str, bool | int | float | str] | None = None,
    extra_warnings: Sequence[str] = (),
) -> StoryCandidate:
    """One firing, as §6.4's structure. Shared with `trend_reversal.py`.

    Parameterised on the detector's identity rather than copied, because D1 and D2 build the same
    shape of candidate from the same points and differ only in which periods they anchor and what
    they put in `signals`. Two copies would drift, and the field that would drift first is
    `anchor_observation_ids` — the digest input §6.11 sorts.

    **`anchor_observation_ids` are the *supporting* ids** of every anchored slot, never the
    minority ones: §10.1 discloses a minority reading separately, and it backs a number this run
    did not use.

    Signals are numbers, flags and closed words. There is no headline, no thesis and no score
    here by construction — `StoryCandidate` forbids extra fields (§6.4) — and `direction` is a
    word from `detector_config` rather than the sign of the delta, because §6.6 D1's whole
    false-positive section is about a cost whose values are negative.
    """
    metric_id = measurement.metric_id
    period_keys = tuple(sorted(point.period.key for point in window))
    observation_ids = tuple(
        sorted({obs for point in window for obs in point.supporting_observation_ids})
    )
    direction = quantity_direction(metric_id, measurement.delta)
    polarity = polarity_of(metric_id)

    signals: dict[str, bool | int | float | str] = {
        "delta": measurement.delta,
        "crosses_zero": measurement.crosses_zero,
        "fired_on": ",".join(measurement.fired_on),
        "period_shape": window[0].period.shape.value,
        "threshold_pct_min": measurement.thresholds.pct_min,
    }
    if measurement.delta_pct is not None:
        signals["delta_pct"] = measurement.delta_pct
    if measurement.delta_pp is not None:
        signals["delta_pp"] = measurement.delta_pp
    if measurement.floor is not None:
        signals["value_floor"] = measurement.floor
    if measurement.thresholds.abs_min is not None:
        signals["threshold_abs_min"] = measurement.thresholds.abs_min
    if direction is not None:
        signals["direction"] = direction
    if polarity is not None:
        signals["polarity"] = polarity.value
    if extra_signals:
        signals.update(extra_signals)

    warnings = {warning for point in window for warning in point.warnings}
    warnings.update(comparability_warnings)
    warnings.update(extra_warnings)
    if measurement.relative_change_refused:
        # Said on the candidate and not only by the missing key, because §6.10, §10 and §11 all
        # read `signals` and only this layer still knows both numbers (§13.3's third gate).
        warnings.add(RELATIVE_CHANGE_ACROSS_ZERO)
    if direction is None:
        # The move is real and citable; only the sentence describing it is unavailable, so the
        # candidate is emitted carrying the gap rather than dropped (`detector_config`).
        warnings.add(SIGN_CONVENTION_UNVERIFIED)

    return StoryCandidate(
        candidate_id=candidate_id(
            detector_id=detector_id,
            detector_version=detector_version,
            policy_version=POLICY_VERSION,
            scope=metric_id,
            subject_entity_id=window[0].subject_entity_id,
            anchor_period_keys=period_keys,
            metric_ids=(metric_id,),
            anchor_input_ids=observation_ids,
        ),
        detector_id=detector_id,
        detector_version=detector_version,
        policy_version=POLICY_VERSION,
        graph_run_id=graph_run_id,
        # R1 has already held every step to one subject, so any point's is the window's.
        subject_entity_id=window[0].subject_entity_id,
        story_type=story_type,
        metric_ids=(metric_id,),
        anchor_period_keys=period_keys,
        anchor_observation_ids=observation_ids,
        signals=signals,
        warnings=tuple(sorted(warnings)),
        evidence_request=EvidenceRequest(
            metric_ids=(metric_id,),
            period_keys=period_keys,
            observation_ids=observation_ids,
            # `want_explanatory_search` is left at §6.4's default of off: the filings of these
            # periods are already in the package, and a fulltext search buys rows out of §10.2's
            # budget for a question this detector has not asked.
        ),
    )


__all__ = [
    "ARM_ABSOLUTE",
    "ARM_PERCENTAGE_POINTS",
    "ARM_RELATIVE_PERCENT",
    "DETECTOR_ID",
    "DETECTOR_VERSION",
    "PERCENT_UNIT",
    "RELATIVE_CHANGE_ACROSS_ZERO",
    "SERIES_INCOMPLETE",
    "STORY_TYPE",
    "MetricMoveRefusal",
    "MetricMoveResult",
    "MoveMeasurement",
    "candidate_from_move",
    "detect_metric_moves",
    "detect_metric_moves_from_load",
    "measure_move",
]
