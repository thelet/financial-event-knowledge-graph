"""§6.6 D3 — `acceleration`, and deliberately nothing else.

Responsibility: find, in a canonical series, a run of **three consecutive same-sign deltas whose
absolute magnitude increases monotonically and whose last delta is at least
`MAGNITUDE_RATIO × |d[i−2]|`** — and turn each run into one `StoryCandidate`. It owns no
comparability rule (R1–R10 are `story/core/series.py`'s), no canonicalisation (§6.1 is
`canonicalization.py`'s), no ranking and no prose.

**Deceleration is not implemented, and the plan's own example for it is wrong.** §6.6 D3's first
draft justified a lineage dedup with *"the 2023Q1 decelerations of AGP, CP, CM and AGM are the
same event four times"*. The monotone-**increasing** condition cannot fire on a deceleration at
all, so this detector can never produce one of those candidates. A deceleration detector needs
its own rule (monotone decreasing `|d|`) and its own threshold, and it is out of V1 scope; a
test asserts that a decelerating series emits nothing here.

**Census, measured live against `graph-v1-0483dc6b4b10` on 2026-08-04.** Over quarterly points
the literal rule fires **8 times across 6 metrics**, reproducing §6.6 D3's figure exactly. Over
*every* comparable shape it fires **10 times across 7 metrics**: the two extra are
`housing_inventory_homes` instants (`2020-12-31 → 2021-09-30` building, `2022-06-30 →
2023-03-31` drawing down). The plan's 8 counts quarters only — §6.10's population is *"298
consecutive-quarter deltas"* — and this module scans every shape R3 admits, because an instant
series is a first-class canonical series (§6.2 lists three of them) and dropping it would be a
narrowing of the rule that nothing states. Both numbers are pinned by live tests.

**Thresholds are this module's own constants.** `detector_config.py` did not exist when this was
written (S3-A owns it); `MAGNITUDE_RATIO` and `RUN_LENGTH` are declared here and are to be
reconciled at integration. They are inside no digest — `candidate_id` covers `DETECTOR_VERSION`
instead (§6.11), so changing a threshold requires bumping the version to mint new candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from story.core.keys import candidate_id
from story.core.models import EvidenceRequest, StoryCandidate
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

DETECTOR_ID = "detector:acceleration"
DETECTOR_VERSION = "1.0.0"
STORY_TYPE = "acceleration"

#: §6.6 D3's *"three consecutive same-sign deltas"*, and therefore four points per window.
RUN_LENGTH = 3
WINDOW_POINTS = RUN_LENGTH + 1

#: §6.6 D3's `|d[i]| ≥ 1.5 × |d[i−2]|`. A magnitude gate over the run's own ends rather than a
#: metric-family threshold: D1's p75 table answers *"is this move large for this metric"*, and
#: this rule asks *"is the third step half again the first"*, which is scale-free.
MAGNITUDE_RATIO = 1.5

#: Decimal places every delta is rounded to before it is compared with anything.
#: `13.2 − 3.3` is `9.899999999999999` in IEEE 754 and `adjusted_gross_margin` really does hold
#: those two values in adjacent quarters, so a rule that tested `|d1| < |d2|` or `sign(d)` on raw
#: subtraction would decide on residues at the seventeenth digit. Nine places is far below every
#: presentation tolerance in the corpus (0.1 for a percentage, $1 for a count) and far above the
#: residue, so rounding here cannot change a decision the data supports.
DELTA_PRECISION = 9

#: The five shapes R3 admits. `PeriodShape.OTHER` is excluded because R3 refuses it — the three
#: cross-year windows in this run are not comparable with anything — and it is excluded *here*
#: as well so a window is never built out of points `comparable` would only refuse one by one.
COMPARABLE_SHAPES: tuple[PeriodShape, ...] = (
    PeriodShape.QUARTER,
    PeriodShape.INSTANT,
    PeriodShape.FISCAL_YEAR,
    PeriodShape.YTD_6M,
    PeriodShape.YTD_9M,
)

#: The refusal this detector can raise on its own account: a series whose completeness the
#: loader could not prove. Not a `Refuse` from `core/series.py` — that type answers "may these
#: two points be compared", and this answers "may this metric be read at all".
SERIES_INCOMPLETE = "SERIES_INCOMPLETE"


@dataclass(frozen=True, slots=True)
class AccelerationRefusal:
    """One metric this detector declined to scan, and why.

    A value rather than a log line, for the reason §6.1 keeps `ObservationLoad.unreadable`: a
    metric that was skipped and a metric that held no acceleration are different findings, and a
    run that reported them the same way would let a silently short series read as a quiet quarter.
    """

    metric_id: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class AccelerationResult:
    """What one scan produced: the candidates, and the metrics it refused to scan."""

    candidates: tuple[StoryCandidate, ...] = ()
    refusals: tuple[AccelerationRefusal, ...] = ()


def delta_between(later: CanonicalPoint, earlier: CanonicalPoint) -> float:
    """`later − earlier`, rounded to `DELTA_PRECISION`. Both points must carry a value."""
    if later.value is None or earlier.value is None:
        raise ValueError(
            f"{later.metric_id}: {earlier.period.key} or {later.period.key} emits no value; "
            "R7 refuses the pair and a delta may not be taken over it"
        )
    return round(later.value - earlier.value, DELTA_PRECISION)


def delta_sign(value: float) -> int | None:
    """`+1`, `−1`, or **`None` for a delta of exactly zero**.

    §6.6 D2 requires zero-delta handling to be specified rather than left to the implementation,
    and the specification is the same for D3: *a delta of exactly 0 has no sign*. `None` is what
    says so — a third sign value would invite `sign(d) == sign(previous)` to be true for a pair
    of zeros and turn a flat series into a run. A run containing one is not a run.

    In this corpus R8 also refuses a step of zero (it is inside any presentation tolerance), so
    this rule is belt and braces. It is written and tested anyway: R8's tolerance is a property
    of a unit and a scale, and a metric printed with a tolerance of zero would slip past it.
    """
    if value > 0:
        return 1
    if value < 0:
        return -1
    return None


def accelerates(deltas: Sequence[float]) -> bool:
    """§6.6 D3's arithmetic, over `RUN_LENGTH` already-rounded deltas.

    Three conditions and no fourth: same sign, strictly increasing `|d|`, and
    `|d[last]| ≥ MAGNITUDE_RATIO × |d[first]|`. The ratio is checked as a rounded *difference*
    rather than as a division, because `|d[first]|` may be arbitrarily small and a quotient over
    it is the division-by-noise §6.6 D1 records as the reason for its own small-base floor.

    Strictly increasing, matching the plan's *"monotone **increasing**"*. Measured on the current
    run the census is identical either way — no window here has two equal-magnitude deltas — so
    this choice is recorded rather than justified by data.
    """
    if len(deltas) != RUN_LENGTH:
        return False
    signs = [delta_sign(value) for value in deltas]
    if None in signs or len(set(signs)) != 1:
        return False
    magnitudes = [abs(value) for value in deltas]
    if any(left >= right for left, right in zip(magnitudes, magnitudes[1:])):
        return False
    return round(magnitudes[-1] - MAGNITUDE_RATIO * magnitudes[0], DELTA_PRECISION) >= 0


def detect_acceleration(
    points: Sequence[CanonicalPoint],
    *,
    graph_run_id: str,
    unreadable: Sequence[tuple[str, str]] = (),
    shapes: Iterable[PeriodShape] = COMPARABLE_SHAPES,
    authority: ComparabilityAuthority | None = None,
) -> AccelerationResult:
    """Every acceleration in the canonical points, plus the metrics that were not scanned.

    `unreadable` is `ObservationLoad.unreadable` and is **not** optional in spirit: five metrics
    exceed `get_metric_history`'s 200-row bound and the loader refuses rather than returning a
    short page, so a metric named there has an unknown series and every delta over it would be a
    delta across an unknown hole. Those metrics are refused, not scanned.
    `detect_acceleration_from_load` is the call that cannot forget to pass it.
    """
    refused = {metric_id for metric_id, _ in unreadable}
    ordered_shapes = tuple(shapes)
    candidates: list[StoryCandidate] = []
    for metric_id, series in sorted(build_series(points).items()):
        if metric_id in refused:
            continue
        for shape in ordered_shapes:
            candidates.extend(
                _scan(series, shape, graph_run_id=graph_run_id, authority=authority)
            )
    return AccelerationResult(
        candidates=tuple(candidates),
        refusals=tuple(
            AccelerationRefusal(metric_id=metric_id, reason=SERIES_INCOMPLETE, detail=detail)
            for metric_id, detail in sorted(unreadable)
        ),
    )


def detect_acceleration_from_load(
    load: ObservationLoad,
    *,
    graph_run_id: str,
    shapes: Iterable[PeriodShape] = COMPARABLE_SHAPES,
    authority: ComparabilityAuthority | None = None,
) -> AccelerationResult:
    """`load_observations` → §6.1 → D3, with the loader's own refusals carried through."""
    return detect_acceleration(
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
    graph_run_id: str,
    authority: ComparabilityAuthority | None,
) -> list[StoryCandidate]:
    """Every four-point window of one shape, in series order.

    The windows are built from `valued_points(shape)`, so a `CONFLICT` slot is *not* a member and
    a window can silently close over one. That is not a hole this scan has to catch itself: the
    two points either side of a dropped slot are two calendar steps apart, and R10's `SERIES_GAP`
    measures the months rather than counting list positions.
    """
    points = series.valued_points(shape)
    candidates: list[StoryCandidate] = []
    for index in range(WINDOW_POINTS, len(points) + 1):
        window = points[index - WINDOW_POINTS:index]
        warnings = _comparable_window(window, series, authority)
        if warnings is None:
            continue
        deltas = [
            delta_between(later, earlier) for earlier, later in zip(window, window[1:])
        ]
        if not accelerates(deltas):
            continue
        candidates.append(_candidate(window, deltas, warnings, graph_run_id=graph_run_id))
    return candidates


def _comparable_window(
    window: Sequence[CanonicalPoint],
    series: CanonicalSeries,
    authority: ComparabilityAuthority | None,
) -> tuple[str, ...] | None:
    """The comparability warnings the window carries, or `None` if any step was refused.

    Every step is put through `comparable(..., claim=ClaimKind.ACCELERATION, series=series)` and
    not through a hand-rolled adjacency test. R10 is the rule that stops
    `pct_homes_on_market_gt_120_days` reading a *"+47 pp quarterly jump"* across a four-quarter
    hole, and a detector that re-implemented "one quarter apart" would be a second, unreviewed
    copy of it. Passing `series` is what makes R10 answer at all — omitting it is
    `SERIES_UNAVAILABLE`, refused rather than skipped.

    `None` and not an empty tuple for a refusal, because an empty tuple of warnings is the
    ordinary outcome of a permitted window and the two states must not collide.
    """
    warnings: set[str] = set()
    for earlier, later in zip(window, window[1:]):
        verdict = comparable(
            later, earlier, claim=ClaimKind.ACCELERATION, authority=authority, series=series
        )
        if not isinstance(verdict, Ok):
            return None
        warnings.update(warning.code for warning in verdict.warnings)
    return tuple(sorted(warnings))


def _candidate(
    window: Sequence[CanonicalPoint],
    deltas: Sequence[float],
    comparability_warnings: Sequence[str],
    *,
    graph_run_id: str,
) -> StoryCandidate:
    """One firing, as §6.4's structure.

    **All four period keys are anchors**, not just the quarter the run ends in: the claim is that
    three steps compounded, and a candidate anchored on one quarter would ask §10 for the
    evidence of a claim it is not making. `anchor_observation_ids` are the *supporting* ids of
    those four slots — the readings that back the canonical value — and never the minority ones,
    which §10.1 discloses separately and which back a number this run did not use.

    Signals are numbers only. There is no headline, no thesis and no score here by construction
    (§6.4): `StoryCandidate` forbids extra fields, and every value below is a float or an int.
    """
    metric_id = window[0].metric_id
    period_keys = tuple(sorted(point.period.key for point in window))
    observation_ids = tuple(
        sorted({obs for point in window for obs in point.supporting_observation_ids})
    )
    warnings = tuple(
        sorted({warning for point in window for warning in point.warnings}
               | set(comparability_warnings))
    )
    return StoryCandidate(
        candidate_id=candidate_id(
            detector_id=DETECTOR_ID,
            detector_version=DETECTOR_VERSION,
            policy_version=POLICY_VERSION,
            scope=metric_id,
            subject_entity_id=window[0].subject_entity_id,
            anchor_period_keys=period_keys,
            metric_ids=(metric_id,),
            anchor_input_ids=observation_ids,
        ),
        detector_id=DETECTOR_ID,
        detector_version=DETECTOR_VERSION,
        policy_version=POLICY_VERSION,
        graph_run_id=graph_run_id,
        # R1 has already held every step of the window to one subject, so any point's is the
        # window's.
        subject_entity_id=window[0].subject_entity_id,
        story_type=STORY_TYPE,
        metric_ids=(metric_id,),
        anchor_period_keys=period_keys,
        anchor_observation_ids=observation_ids,
        signals={
            "delta_1": deltas[0],
            "delta_2": deltas[1],
            "delta_3": deltas[2],
            # `+1`/`−1` rather than a word: direction of *value*, not polarity. §6.6 D1 records
            # that `metrics.yaml` has no polarity key and that the story layer owns that map, so
            # a detector calling a rise "growth" would be inventing the answer.
            "delta_sign": 1 if deltas[0] > 0 else -1,
            "magnitude_ratio": round(abs(deltas[-1]) / abs(deltas[0]), DELTA_PRECISION),
            "window_delta": round(sum(deltas), DELTA_PRECISION),
            "run_length": RUN_LENGTH,
        },
        warnings=warnings,
        evidence_request=EvidenceRequest(
            metric_ids=(metric_id,),
            period_keys=period_keys,
            observation_ids=observation_ids,
            # `want_explanatory_search` is left off (§6.4's default): the filings of these four
            # periods are already in the package, and a fulltext search buys rows out of §10.2's
            # budget for a question this detector has not asked.
        ),
    )


__all__ = [
    "COMPARABLE_SHAPES",
    "DELTA_PRECISION",
    "DETECTOR_ID",
    "DETECTOR_VERSION",
    "MAGNITUDE_RATIO",
    "RUN_LENGTH",
    "SERIES_INCOMPLETE",
    "STORY_TYPE",
    "WINDOW_POINTS",
    "AccelerationRefusal",
    "AccelerationResult",
    "accelerates",
    "delta_between",
    "delta_sign",
    "detect_acceleration",
    "detect_acceleration_from_load",
]
