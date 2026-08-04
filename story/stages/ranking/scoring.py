"""§6.10's score, term by term, with every weight named and no model anywhere near it.

Responsibility: turn one `StoryCandidate` plus the run's `MetricHistory` into a decomposed
score. It decides no order — that is `candidate_ranking.py` — and it collapses nothing — that is
`deduplication.py`.

```
score = 0.40·clip(|z|/4)         magnitude against the metric's own delta history
      + 0.20·clip(|Δ|/p90)       magnitude in the metric's own units (§6.10 writes Δpct — see
                                 the first departure below)
      + 0.15·clip(n_docs/5)      corroboration across distinct filings
      + 0.10·novelty             1/(1+prior candidates for this metric+detector), plus
                                 is_first_occurrence
      + 0.10·recency             quarters from 2026Q1, normalised
      − 0.15·warning_count
      − 0.20·single_source       n_docs == 1
      − 0.25·repetition          overlap with recently accepted posts
```

**The weights are constants in this module and nowhere else.** §6.10 says `config/story.yaml`
owns them *"so a change is a change of record"*; no `config/story.yaml` exists at S4 and inventing
one to hold eight floats would be a configuration file with no reader. They are named module
constants with the plan section beside each, and moving them into a config file later is a change
of one import.

**Two signals are deliberately not terms, and §6.10 measured why.**

*`ambiguity_count` carries zero ranking information.* Both codes in this corpus are
metric-constant — `homes_sold_recognition_point` on all 124 `homes_sold` rows and
`pct_120_days_denominator` on all 88 `pct_>120d` rows — so a term over them would rank
`homes_sold` above or below everything else by a property of the metric, not of the story. It is
a mandatory caveat the package must carry (§10.1), not a discriminator, and
`test_story_ranking.py` asserts it never appears in `components`. It is not even reachable from
here: S3 recorded that `CanonicalPoint` carries no `ambiguity_codes` at all, so the canonical
layer this stage reads could not supply one.

*`magnitude_z` is null and not computed below eight points.* `metric_history.py` applies the
floor; this module records the suppression as a reason and leaves the key **out of** `components`
rather than writing `0.0`, because a zero contribution and an uncomputable one are different
findings and a reader must be able to tell them apart.

**Two departures from §6.10's literal text, both recorded rather than smoothed over.**

1. *The units term is `|Δ|/p90(|Δ|)`, not `Δpct/p90`.* The full argument is on
   `DeltaDistribution.own_units_ratio`; the short version is that every defect this corpus has
   produced on the relative form came from the **base** it divides by and none from the
   percentile — P10's factor of twelve, `adjusted_ebitda`'s `+43,900%` off a $0.4M base, and the
   `−221%` a sign flip yields, which R4a (`2937b58`) removed from `delta_pct` altogether. Dividing
   by the metric's own p90 normalises across metrics without a base at all. Measured: under the
   literal reading §6.3's F1 falls from 16th to 37th of 262 — out of the top decile — **because it
   crossed zero**, which is the opposite of what a magnitude term is for.
2. *The warning count is capped.* `−0.15·warning_count` is unbounded as written; four warnings
   would outweigh every positive term. Live it never binds — the distribution is **208 candidates
   with no warning, 52 with one and 2 with two** *(measured 2026-08-04)*: 52 carry
   `relative_change_across_zero` and the two carry `cohort_vs_period_basis` alongside
   `divergence_population_excludes_periods`. So the cap changes nothing on this run and exists so
   that a future candidate carrying fifteen disclosures cannot be ranked by its disclosures
   alone.

**`repetition` has no store to read and none is invented.** V1 has published no post, so there is
no history of accepted candidates anywhere in this repository. The term is a function of an
explicitly passed `accepted_history` that defaults to empty, which makes it exactly `0.0` on every
candidate today. A fabricated store — a JSON file, a table, a module global — would be a made-up
input to a real score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from story.core.models import StoryCandidate
from story.core.periods import PeriodShape, months_between
from story.stages.ranking.metric_history import MIN_DELTA_POPULATION, MetricHistory

# -- §6.10's weights ------------------------------------------------------------------------

WEIGHT_MAGNITUDE_Z = 0.40
WEIGHT_MAGNITUDE_UNITS = 0.20
WEIGHT_CORROBORATION = 0.15
WEIGHT_NOVELTY = 0.10
WEIGHT_RECENCY = 0.10
WEIGHT_WARNINGS = -0.15
WEIGHT_SINGLE_SOURCE = -0.20
WEIGHT_REPETITION = -0.25

COMPONENT_MAGNITUDE_Z = "magnitude_z"
COMPONENT_MAGNITUDE_UNITS = "magnitude_units"
COMPONENT_CORROBORATION = "corroboration"
COMPONENT_NOVELTY = "novelty"
COMPONENT_RECENCY = "recency"
COMPONENT_WARNINGS = "warning_count"
COMPONENT_SINGLE_SOURCE = "single_source"
COMPONENT_REPETITION = "repetition"

#: The one place a component name is bound to its weight. Ordered as §6.10 writes them, because
#: the order is what a breakdown is read in.
WEIGHTS: Mapping[str, float] = {
    COMPONENT_MAGNITUDE_Z: WEIGHT_MAGNITUDE_Z,
    COMPONENT_MAGNITUDE_UNITS: WEIGHT_MAGNITUDE_UNITS,
    COMPONENT_CORROBORATION: WEIGHT_CORROBORATION,
    COMPONENT_NOVELTY: WEIGHT_NOVELTY,
    COMPONENT_RECENCY: WEIGHT_RECENCY,
    COMPONENT_WARNINGS: WEIGHT_WARNINGS,
    COMPONENT_SINGLE_SOURCE: WEIGHT_SINGLE_SOURCE,
    COMPONENT_REPETITION: WEIGHT_REPETITION,
}

#: `clip(|z|/4)`. §6.10 measured the gates it saturates against: across 298 consecutive-quarter
#: deltas, 18 have `|z| > 2` and 35 have `|z| > 1.5`, so 4 is roughly twice the headline gate and
#: a candidate reaching it has saturated the term for a reason.
Z_SATURATION = 4.0

#: `clip(n_docs/5)`. §6.10's own denominator.
CORROBORATION_SATURATION = 5.0

#: §6.10's *"quarters from 2026Q1"*. The date is the anchor of that quarter, because slots are
#: ordered by `anchor_date` everywhere else in this package.
RECENCY_ANCHOR_PERIOD_KEY = "2026Q1"
RECENCY_ANCHOR_DATE = "2026-03-31"

#: *"normalised"*, over a horizon the corpus decides: the run spans 2018Q1 to 2026Q1, so 32
#: quarters is the age at which the oldest story in the graph reaches zero recency. A shorter
#: horizon would flatten two thirds of the corpus onto the same value.
RECENCY_HORIZON_QUARTERS = 32.0

#: §6.10's `novelty` is `1/(1+prior)` *"plus is_first_occurrence"*, with no coefficient stated.
#: Half, so that a first-ever occurrence lifts a much-repeated metric to the middle of the term
#: without letting it match a genuinely first firing, which already scores 1.0 on its own.
NOVELTY_FIRST_OCCURRENCE_BONUS = 0.5

#: The count at which the warning penalty saturates (see the module docstring). Live maximum: 2.
WARNING_COUNT_CAP = 4.0

#: Decimal places every component and every total is rounded to before it is compared or summed.
#: Eight weighted floats leave residues at the seventeenth digit, and a tie-break that compares
#: unrounded totals is a tie-break that never sees a tie.
SCORE_PRECISION = 9

#: Where each detector puts the step its story is about. `cross_metric_divergence` is absent
#: deliberately: its candidate is a gap between two metrics, not a step in one, and it publishes
#: its own `z` against its own population — see `_magnitude`.
MAGNITUDE_SIGNAL: Mapping[str, str] = {
    "metric_move": "delta",
    "trend_reversal": "delta",
    # The third and largest step of the accelerating window. `window_delta` is the whole
    # three-quarter move and would be scored against a one-step distribution.
    "acceleration": "delta_3",
}

DIVERGENCE_STORY_TYPE = "cross_metric_divergence"


@dataclass(frozen=True, slots=True)
class AcceptedPost:
    """One previously published post, as §6.10's repetition term reads it.

    Three fields and no text: the term is a set overlap over *"(metric_ids, period_keys,
    story_type)"*, and a post carrying its prose here would invite a similarity measure over the
    prose. There is no store behind this type in V1 — callers pass an empty sequence — and that
    is stated rather than hidden behind a default that reads a file.
    """

    metric_ids: tuple[str, ...] = ()
    period_keys: tuple[str, ...] = ()
    story_type: str = ""


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """One candidate's score, decomposed, with the terms that could not be computed named.

    `suppressed` is a mapping from a component name to the reason it is absent, so *"no magnitude
    term"* is never mistaken for *"a magnitude of zero"*. It is what the ranked row renders when a
    reader asks why a candidate scored what it did.
    """

    candidate_id: str
    total: float
    components: Mapping[str, float]
    suppressed: Mapping[str, str] = field(default_factory=dict)

    def contributions(self) -> Mapping[str, float]:
        """Each component multiplied by its weight — the numbers that literally sum to `total`."""
        return {
            name: round(value * WEIGHTS[name], SCORE_PRECISION)
            for name, value in self.components.items()
        }


def clip(value: float) -> float:
    """§6.10's `clip`: into `[0, 1]`, so no single term can carry a score on its own."""
    return min(max(value, 0.0), 1.0)


def primary_period_key(candidate: StoryCandidate, history: MetricHistory) -> str | None:
    """The period a candidate's claim is *about*: the latest of its anchors.

    A `metric_move` anchors two quarters and a `trend_reversal` four; both are claims about the
    last one. Ordered by the slot's `anchor_date` where the run knows it, falling back to the key
    itself, which orders correctly for every key shape in this corpus and is only reached for a
    period no canonical point covers.
    """
    if not candidate.anchor_period_keys:
        return None
    def sort_key(period_key: str) -> tuple[str, str]:
        for metric_id in candidate.metric_ids:
            slot = history.slot(metric_id, period_key)
            if slot is not None and slot.anchor_date is not None:
                return (slot.anchor_date, period_key)
        return (period_key, period_key)
    return max(candidate.anchor_period_keys, key=sort_key)


def anchor_shape(candidate: StoryCandidate, history: MetricHistory) -> PeriodShape | None:
    """The shape the candidate's step was taken at, from the run rather than from the signal.

    `metric_move`, `trend_reversal` and `cross_metric_divergence` publish `period_shape`;
    `acceleration` does not, and a term that only worked for three of four detectors would put a
    twelve-month acceleration into a quarterly distribution. The slot index answers for all four.
    """
    period_key = primary_period_key(candidate, history)
    if period_key is None:
        return None
    for metric_id in candidate.metric_ids:
        slot = history.slot(metric_id, period_key)
        if slot is not None:
            return slot.shape
    signalled = candidate.signals.get("period_shape")
    return PeriodShape(signalled) if isinstance(signalled, str) else None


def anchor_n_docs(candidate: StoryCandidate, history: MetricHistory) -> int | None:
    """Distinct filings behind the candidate's **weakest** anchor slot.

    The minimum and not the sum: §6.10 asks how corroborated the story is, and a claim about a
    step is only as filed as its least-filed end. Summing would let a heavily restated quarter
    carry a single-source one, which is exactly what the `single_source` penalty is for.
    """
    counts = [
        slot.n_docs
        for metric_id in candidate.metric_ids
        for period_key in candidate.anchor_period_keys
        if (slot := history.slot(metric_id, period_key)) is not None
    ]
    return min(counts) if counts else None


@dataclass(frozen=True, slots=True)
class MagnitudeReading:
    """The two magnitude terms and, for each, the reason it is absent when it is.

    Two reasons and not one, because the two terms fail for different causes on the same
    candidate: a 25-step distribution gives a z but a 3-step one at another shape gives neither,
    and a `cross_metric_divergence` candidate has a published z and no percentile at all.
    """

    z: float | None = None
    units: float | None = None
    z_reason: str = ""
    units_reason: str = ""


def _magnitude(candidate: StoryCandidate, history: MetricHistory) -> MagnitudeReading:
    """§6.10's first two terms, read from the candidate against the run's own history.

    `cross_metric_divergence` is the branch, and it is a branch because its z is a claim about a
    *gap* distribution the detector built and published (`z`, over `population_size` periods of
    one shape, after an anchor-relative R6 split). Recomputing it here from the canonical points
    would mean restating that population, and a second population would silently disagree with
    the one the candidate fired against. Its units term has no input: the detector publishes the
    gap, its mean and its σ, but **no percentile** of the gap, and a p90 inferred from a σ would
    be a normality assumption nobody measured. Measured consequence, live: the fifteen divergence
    candidates each forgo up to 0.20, and §6.3's F3 still lands twelfth of 262.
    """
    if candidate.story_type == DIVERGENCE_STORY_TYPE:
        z = candidate.signals.get("z")
        return MagnitudeReading(
            z=abs(float(z)) if isinstance(z, (int, float)) else None,
            z_reason="" if isinstance(z, (int, float)) else "the candidate publishes no z",
            units_reason=(
                "the pair's gap distribution publishes a z, a mean and a σ, but no p90"
            ),
        )

    signal_name = MAGNITUDE_SIGNAL.get(candidate.story_type)
    if signal_name is None:
        reason = f"no magnitude signal is declared for story_type {candidate.story_type}"
        return MagnitudeReading(z_reason=reason, units_reason=reason)
    delta = candidate.signals.get(signal_name)
    if not isinstance(delta, (int, float)):
        reason = f"the candidate publishes no {signal_name}"
        return MagnitudeReading(z_reason=reason, units_reason=reason)

    shape = anchor_shape(candidate, history)
    metric_id = candidate.metric_ids[0] if candidate.metric_ids else None
    distribution = (
        None if metric_id is None or shape is None else history.distribution(metric_id, shape)
    )
    if distribution is None:
        reason = f"{metric_id} has no delta distribution at shape {shape}"
        return MagnitudeReading(z_reason=reason, units_reason=reason)

    where = f"{metric_id} at shape {distribution.shape.value}"
    z = distribution.z_for(float(delta))
    z_reason = (
        ""
        if z is not None
        else (
            f"{where} has {distribution.population} comparable deltas, below the "
            f"{MIN_DELTA_POPULATION}-point floor §6.10 sets"
            if distribution.population < MIN_DELTA_POPULATION
            else f"{where} has σ = 0 over {distribution.population} deltas"
        )
    )

    units = distribution.own_units_ratio(float(delta))
    units_reason = (
        ""
        if units is not None
        else f"{where} has no p90 of |Δ| over {distribution.population} deltas"
    )
    return MagnitudeReading(
        z=None if z is None else abs(z), units=units, z_reason=z_reason, units_reason=units_reason
    )


def prior_candidate_counts(
    candidates: Sequence[StoryCandidate], history: MetricHistory
) -> Mapping[str, int]:
    """§6.10's *"prior candidates for this metric+detector"*, over the run's own candidate set.

    *Prior* means earlier in the series, not earlier in the list: the first time
    `adjusted_gross_margin` moves is news and the twelfth is a pattern. Ordering is by
    `(anchor_date, period_key, candidate_id)` so two candidates of one metric and detector at one
    date — a fiscal year and its fourth quarter both end 2022-12-31 — still get distinct counts
    and the count does not depend on input order.
    """
    grouped: dict[tuple[str, tuple[str, ...]], list[tuple[str, str, str]]] = {}
    for candidate in candidates:
        period_key = primary_period_key(candidate, history) or ""
        slot = next(
            (
                slot
                for metric_id in candidate.metric_ids
                if (slot := history.slot(metric_id, period_key)) is not None
            ),
            None,
        )
        anchor_date = slot.anchor_date if slot is not None and slot.anchor_date else period_key
        key = (candidate.detector_id, tuple(candidate.metric_ids))
        grouped.setdefault(key, []).append((anchor_date, period_key, candidate.candidate_id))

    counts: dict[str, int] = {}
    for rows in grouped.values():
        for position, (_date, _key, candidate_id) in enumerate(sorted(rows)):
            counts[candidate_id] = position
    return counts


def _novelty(
    candidate: StoryCandidate, history: MetricHistory, prior_count: int
) -> tuple[float, bool]:
    """`1/(1+prior)`, plus §6.10's `is_first_occurrence` bonus, clipped."""
    period_key = primary_period_key(candidate, history)
    first_occurrence = any(
        history.first_negative_period.get(metric_id) == period_key
        for metric_id in candidate.metric_ids
    )
    base = 1.0 / (1.0 + prior_count)
    bonus = NOVELTY_FIRST_OCCURRENCE_BONUS if first_occurrence else 0.0
    return clip(base + bonus), first_occurrence


def _recency(candidate: StoryCandidate, history: MetricHistory) -> float | None:
    """Quarters from 2026Q1, normalised over `RECENCY_HORIZON_QUARTERS` and clipped.

    A period *after* the anchor would score above 1 and is clipped to it rather than refused:
    2026Q1 is the newest quarter in this run, and a later one arriving is a fresher graph, not an
    error.
    """
    period_key = primary_period_key(candidate, history)
    if period_key is None:
        return None
    slot = next(
        (
            slot
            for metric_id in candidate.metric_ids
            if (slot := history.slot(metric_id, period_key)) is not None
        ),
        None,
    )
    if slot is None or slot.anchor_date is None:
        return None
    months = months_between(slot.anchor_date, RECENCY_ANCHOR_DATE)
    if months is None:
        return None
    return clip(1.0 - (months / 3.0) / RECENCY_HORIZON_QUARTERS)


def repetition(candidate: StoryCandidate, accepted: Iterable[AcceptedPost]) -> float:
    """Overlap with the most similar recently accepted post, or `0.0` when there are none.

    §6.10 says *"cosine over the candidate's (metric_ids, period_keys, story_type)"*. Over binary
    membership vectors the cosine **is** a set operation — `|A∩B| / sqrt(|A|·|B|)` — so this is
    the plan's measure computed as sets, with no vector space, no embedding and no dimension that
    anything has to agree on. The alternative reading, an embedding similarity, is out of scope
    by §6.10's own *"no model"* and by the ban on overbuilding.
    """
    mine = _descriptor(candidate.metric_ids, candidate.anchor_period_keys, candidate.story_type)
    best = 0.0
    for post in accepted:
        theirs = _descriptor(post.metric_ids, post.period_keys, post.story_type)
        if not theirs:
            continue
        best = max(best, len(mine & theirs) / math.sqrt(len(mine) * len(theirs)))
    return clip(best)


def _descriptor(
    metric_ids: Sequence[str], period_keys: Sequence[str], story_type: str
) -> frozenset[tuple[str, str]]:
    """The set a repetition overlap is taken over. Tagged pairs, so a metric named like a period
    cannot collide with one."""
    return frozenset(
        [("metric", metric_id) for metric_id in metric_ids]
        + [("period", period_key) for period_key in period_keys]
        + ([("story_type", story_type)] if story_type else [])
    )


def score_candidate(
    candidate: StoryCandidate,
    history: MetricHistory,
    *,
    prior_count: int = 0,
    accepted_history: Sequence[AcceptedPost] = (),
) -> ScoreBreakdown:
    """§6.10's score for one candidate, decomposed and rounded.

    Every present component is in `[0, 1]` except `warning_count`, which is a count capped at
    `WARNING_COUNT_CAP` and carries its own negative weight. `total` is the sum of the rounded
    weighted components, rounded again — computed in exactly the order `contributions()` reports,
    so the identity a reader checks is the identity the code used.
    """
    components: dict[str, float] = {}
    suppressed: dict[str, str] = {}

    magnitude = _magnitude(candidate, history)
    if magnitude.z is None:
        suppressed[COMPONENT_MAGNITUDE_Z] = magnitude.z_reason or "no z is computable"
    else:
        components[COMPONENT_MAGNITUDE_Z] = clip(magnitude.z / Z_SATURATION)
    if magnitude.units is None:
        suppressed[COMPONENT_MAGNITUDE_UNITS] = (
            magnitude.units_reason or "no own-units magnitude is computable"
        )
    else:
        components[COMPONENT_MAGNITUDE_UNITS] = clip(magnitude.units)

    n_docs = anchor_n_docs(candidate, history)
    if n_docs is None:
        suppressed[COMPONENT_CORROBORATION] = "no canonical slot backs the candidate's anchors"
        suppressed[COMPONENT_SINGLE_SOURCE] = "no canonical slot backs the candidate's anchors"
    else:
        components[COMPONENT_CORROBORATION] = clip(n_docs / CORROBORATION_SATURATION)
        components[COMPONENT_SINGLE_SOURCE] = 1.0 if n_docs == 1 else 0.0

    novelty, _first = _novelty(candidate, history, prior_count)
    components[COMPONENT_NOVELTY] = novelty

    recency = _recency(candidate, history)
    if recency is None:
        suppressed[COMPONENT_RECENCY] = "the anchor period has no date to measure from"
    else:
        components[COMPONENT_RECENCY] = recency

    components[COMPONENT_WARNINGS] = min(float(len(candidate.warnings)), WARNING_COUNT_CAP)
    components[COMPONENT_REPETITION] = repetition(candidate, accepted_history)

    rounded = {
        name: round(value, SCORE_PRECISION)
        for name, value in sorted(components.items(), key=lambda item: _term_order(item[0]))
    }
    total = round(
        sum(round(value * WEIGHTS[name], SCORE_PRECISION) for name, value in rounded.items()),
        SCORE_PRECISION,
    )
    return ScoreBreakdown(
        candidate_id=candidate.candidate_id,
        total=total,
        components=rounded,
        suppressed=dict(sorted(suppressed.items())),
    )


def _term_order(name: str) -> int:
    """§6.10's own order, so a breakdown reads down the plan's expression."""
    return list(WEIGHTS).index(name)


__all__ = [
    "COMPONENT_CORROBORATION",
    "COMPONENT_MAGNITUDE_UNITS",
    "COMPONENT_MAGNITUDE_Z",
    "COMPONENT_NOVELTY",
    "COMPONENT_RECENCY",
    "COMPONENT_REPETITION",
    "COMPONENT_SINGLE_SOURCE",
    "COMPONENT_WARNINGS",
    "CORROBORATION_SATURATION",
    "DIVERGENCE_STORY_TYPE",
    "MAGNITUDE_SIGNAL",
    "NOVELTY_FIRST_OCCURRENCE_BONUS",
    "RECENCY_ANCHOR_DATE",
    "RECENCY_ANCHOR_PERIOD_KEY",
    "RECENCY_HORIZON_QUARTERS",
    "SCORE_PRECISION",
    "WARNING_COUNT_CAP",
    "WEIGHTS",
    "WEIGHT_CORROBORATION",
    "WEIGHT_MAGNITUDE_UNITS",
    "WEIGHT_MAGNITUDE_Z",
    "WEIGHT_NOVELTY",
    "WEIGHT_RECENCY",
    "WEIGHT_REPETITION",
    "WEIGHT_SINGLE_SOURCE",
    "WEIGHT_WARNINGS",
    "Z_SATURATION",
    "AcceptedPost",
    "MagnitudeReading",
    "ScoreBreakdown",
    "anchor_n_docs",
    "anchor_shape",
    "clip",
    "primary_period_key",
    "prior_candidate_counts",
    "repetition",
    "score_candidate",
]
