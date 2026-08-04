"""D4 `cross_metric_divergence` — when two definitions of the same quantity stop agreeing.

Responsibility: §6.6's D4 and nothing else. Given the canonical series of §6.1, it produces
`StoryCandidate`s for the periods where the gap between a declared pair of related metrics is
far from that pair's own history, and a typed refusal for every pair, shape and period it
declined. It owns no comparability rule — R1–R10 are `story/core/series.py`'s and are *called*,
never restated — no ranking, no threshold tuning against the answer, and **no model**.

```
gap[t] = v_a[t] − v_b[t]
z[t]   = (gap[t] − mean(gap)) / pstdev(gap)
fire if |z| ≥ 1.5
```

**The gap history is computed within a single `PeriodShape`, and that is not decoration.**
Over all shapes the `adjusted_gross_margin` ↔ `gaap_gross_margin` pair has 43 comparable
periods — 25 quarters, 6 fiscal years, 6 six-month and 6 nine-month year-to-date windows — and
2022Q3 scores `z = 4.38` against them. That number compares a quarter's wedge with a fiscal
year's, which is the same conflation §13.4 calls catastrophic when it happens to a `period_end`
match. Restricted to `PeriodShape.QUARTER` the population is 25 and 2022Q3 scores **3.945**,
which is the number this module emits *(measured live 2026-08-04)*. Each shape is scored
independently; on this run only `quarter` ever clears `MIN_POPULATION`.

**The population is anchor-relative, and that is what makes R6 bite on a *history*.** R6 is a
rule about two points; a z-score is a claim about a *distribution*, and nothing in R1–R10 asks
whether a distribution is homogeneous. Applied pointwise a cross-metric pair never trips R6 at
all — both sides sit on the same date, so each metric resolves to one formula version and the
rule is trivially satisfied. The defect that hides behind that is real: `adjusted_gross_profit`
is `_v1` through 2021Q4 and `_v2` from 2022Q1 (the restructuring adjustment is dropped), so a
`contribution_profit ↔ adjusted_gross_profit` mean taken over 2020Q1–2026Q1 averages two
different definitions of its own denominator. So a period enters the anchor's population only
when, **for each metric, its point is `comparable` with that metric's point at the anchor** —
`ClaimKind.LEVEL`, which is exactly "these two readings may be stated side by side" and which
R8 and R10 do not apply to. On the live run this splits `CP↔AGP`'s 25 quarters into a v1
population of 8 and a v2 population of 17, refusing each period of the other era by name with
`R6/FORMULA_VERSION_MISMATCH`, and leaves every other pair untouched.

**Different metrics' version ids never cause a refusal** — that was P5, corrected in S2. R6 is
evaluated per metric across both dates, so `adjusted_gross_margin_v1` versus
`gaap_gross_margin_v1` is not a mismatch and F3, the plan's own recommended spike, survives.

**A finding this module measured and the plan does not record.** §6.6 D4 states the
`AGM↔GGM` overlap as 26 quarters and §6.3's F3 as `z = +4.02` over a 26-quarter mean of −0.35
and σ 4.04. Recomputed here it is **25 quarters and z = 3.945**: `2019Q4` is refused by
`R6/FORMULA_VERSION_UNDECLARED`, because *every* versioned metric in this ontology declares
`valid_from: 2020-01-01` and a quarter ending 2019-12-31 lies in no declared window. §6.9's own
correction (P6) named the three pre-2020 slots for `adjusted_gross_profit` only; the same
window boundary applies to `adjusted_gross_margin`, `gaap_gross_margin`, `contribution_margin`,
`contribution_profit`, `contribution_profit_after_interest` and `adjusted_ebitda_margin`. The
raw 26-quarter figures reproduce the plan exactly (mean −0.3462, σ 4.0382, z 4.0231), so the
difference is the rule and not the arithmetic. Clamping `2019Q4` to v1 would assert a
definition the ontology does not declare, which C4 forbids.

**The two metrics of a pair must share a sign convention, and that is checked before any gap is
computed.** `direct_selling_costs` is stored negative — 46 of 46 values, per `detector_config`'s
own measured note — so `adjusted_gross_profit − direct_selling_costs` is `512M − (−136M)`, the
**sum** of a profit and a cost wearing a minus sign. R2 licenses that pair (both are components
of `adjusted_gross_profit`'s formula, so the relation is `co_component`), R4 and R5 pass (both
are USD), and before R4b it produced three candidates, `2022Q1` at `z = 2.574` over a "gap" of
`648000000.0`. A quantity stored under one convention minus a quantity stored under another is
not a difference and no z-score over it means anything, so the pair is refused by name with
`SIGN_CONVENTION_MISMATCH`. **`UNVERIFIED` does not match anything, including `POSITIVE`**:
`cost_of_revenue` and `inventory_valuation_adjustment` have no observation in this run and the
two costs the corpus does print are negative, so a pair joining one of them to a positive metric
is exactly the hazard above with the evidence missing. Two `UNVERIFIED` metrics *are* equal and
pass; the candidate then carries `metric_sign_convention_unverified` and states no direction.
None of the five shipped pairs is affected — all ten metrics are `POSITIVE` — which is the point:
`DivergencePair` is public and `pairs=` is a public parameter, so the refusal exists for the pair
that is one line away from being declared.

**Variance floor, and the single-point excursion it did not stop.** `pstdev(gap)` must reach
`2 × tol` for the pair's unit, or a single 0.1 pp rounding difference in an otherwise flat gap
produces `z ≈ 4.9` out of presentation noise. `tol` is `presentation_tolerance` over the
population's own points, so a USD pair filed in millions carries a $2M floor and a percentage
pair 0.2 pp. **That test alone defends against a 0.1 pp step and nothing larger.** A constructed
population of 25 quarters flat at 0.0 with one quarter at 1.05 pp has σ = 0.20576, clears the
0.2 floor by 0.0058, and the anchor scores 4.899 — which is the *maximum possible* |z| for a
member of its own 25-point population, √(n−1). A one-quarter step minting a ten-sigma candidate
is the defect the floor was written to prevent, one printed unit further out.

So the floor is applied **twice: to the population, and to the population with the anchor
removed.** The dispersion a z-score is measured against must exist independently of the
excursion being scored; a σ the anchor created by itself is not a history. The z-score itself is
still taken over the whole population, anchor included, so no published number moves. Measured
live 2026-08-04 on `graph-v1-0483dc6b4b10`, the leave-one-out σ clears the floor for **all 15**
candidates — the narrowest margin is 3.3×, the seven `AGM↔CM` candidates holding σ ≈ 0.65 pp
against a 0.2 pp floor — so the census is unchanged at 15 and every z is the number it was.
The gap's own dispersion is published as `gap_pstdev_excluding_anchor`. Excluding the
anchor from the *population* as well was measured and rejected: it moves the census to 16 (adds
`AGP↔CP 2023Q1` and `CP↔CPAI 2022Q2`, drops `AGP↔CP 2021Q4` whose v1-era population falls from 8
to 7 and under `MIN_POPULATION`) and moves F3 from 3.9454 to 6.793. It is arguably the better
statistic and it is not this repair's to make.

**A `CONFLICT` slot narrowed a population silently, and now it does not.** `_gap_series` built
from `valued_points`, so a conflicted period vanished *before* `_population` saw it: a
constructed 25-quarter pair with two right-side slots forced to `CONFLICT` reported `n = 23`,
`comparable_overlap = 23`, `periods_excluded = 0` and no warning — the population was narrowed
by two and nothing on the candidate said so, while `_population`'s own docstring claimed a
conflicted slot *"must remove a period from the population too, and none of those needs new
code"*. It removed it one layer earlier and without the disclosure. The scan now hands every
period both sides hold to `comparable`, which refuses it under **R7/NOT_CANONICAL** — R7's own
rule, not a restatement — and the count reaches the candidate as `periods_unresolved`, inside
`population_excluded_by`, and under the same `divergence_population_excludes_periods` warning
the R6 case already used. No live pair loses a period this way, so the refusal census is
unchanged.

**`housing_inventory_homes ↔ homes_sold` is not a pair.** `housing_inventory_homes` is 126/126
`instant` and `homes_sold` has zero instants, so the overlap is **zero** and R3 refuses every
pair *(verified live 2026-08-04)*. The plan's "(20)" was one series' own length. An
inventory-versus-sales relationship is real and belongs to D8's `sell_through` ratio.

**The candidate states the definitional relation, and it comes from `formulas.yaml`.** Not as
prose: `relation` is a closed enum, `shared_component_metrics` and `component_metric_id` are
metric ids, and `left_formula_expression`/`right_formula_expression` are the ontology's own
expression strings, verbatim. That `adjusted_gross_margin` and `gaap_gross_margin` share a
revenue denominator is `component_metrics: [adjusted_gross_profit, revenue]` against
`[gaap_gross_profit, revenue]`, read through the registry — never from a `:Metric` node, which
carries no such property (C4). **The candidate carries no thesis and no score** (§6.4).

**Which constants are this module's own, and which are not.** `Z_MIN`,
`VARIANCE_FLOOR_MULTIPLE`, `DIVERGENCE_SHAPES` and `DIVERGENCE_PAIRS` stay here: a list of
metric pairs and a floor stated as a multiple of a *pair's* tolerance are things only a
cross-metric detector can use, and moving them into `detector_config.py` would put D4's rule
in a module the other three detectors also read. `MIN_POPULATION` is **bound from
`detector_config.MIN_DELTA_POPULATION`** and is not a second literal: §6.10 suppresses
`magnitude_z` to null below "~8 points", `trend_reversal` applies that floor to a delta
distribution and this module applies it to a gap distribution, and a run in which the two
disagreed about the floor would suppress a z-score in one detector and compute it in the other.
It is what keeps the six-point fiscal-year and year-to-date populations from minting candidates
nobody could defend.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from story.core.keys import candidate_id
from story.core.models import Audience, EvidenceRequest, StoryCandidate
from story.core.periods import PeriodShape
from story.core.series import (
    CanonicalPoint,
    CanonicalSeries,
    ClaimKind,
    ComparabilityAuthority,
    comparable,
    default_authority,
)
from story.stages.detection.canonicalization import POLICY_VERSION
from story.stages.detection.detector_config import (
    MIN_DELTA_POPULATION,
    SIGN_CONVENTION_UNVERIFIED,
    ValueSign,
    polarity_of,
    quantity_direction,
    value_sign_of,
)

#: §6.4's closed enum member and §6.11's detector slug source.
DETECTOR_ID = "detector:cross_metric_divergence"
DETECTOR_VERSION = "1.0.0"
STORY_TYPE = "cross_metric_divergence"

#: §6.6 D4. `|z| ≥ 1.5` is §6.10's "worth a paragraph" gate, stated there over consecutive-delta
#: z-scores and reused here because it is the same question asked of a different distribution.
Z_MIN = 1.5

#: §6.6 D4's *"require σ(gap) ≥ 2 × tol, or a 0.1 pp rounding difference produces an enormous
#: z"*, as a multiple of the pair's presentation tolerance rather than as an absolute number:
#: the pairs span percentages, dollars and home counts and one constant cannot serve all three.
VARIANCE_FLOOR_MULTIPLE = 2.0

#: §6.10: *"`magnitude_z` is suppressed to `null`, not computed, when a metric has fewer than
#: ~8 points."* A mean and a population standard deviation over six numbers describe the six
#: numbers, not a history, and every fiscal-year and year-to-date population in this run has
#: exactly six or fewer members.
#:
#: Bound from `detector_config.MIN_DELTA_POPULATION` rather than restated, and kept under this
#: name because the thing counted here is a *gap* population, not a delta population — the same
#: §6.10 floor, applied to a different distribution. See the module docstring.
MIN_POPULATION = MIN_DELTA_POPULATION

#: The shapes a gap history may be built within, most populated first. `PeriodShape.OTHER` is
#: absent because R3 refuses it outright, and `INSTANT` is present because a future instant
#: pair — an inventory measure against another balance-sheet count — would be a legitimate
#: divergence; today no declared pair has one.
DIVERGENCE_SHAPES: tuple[PeriodShape, ...] = (
    PeriodShape.QUARTER,
    PeriodShape.FISCAL_YEAR,
    PeriodShape.YTD_6M,
    PeriodShape.YTD_9M,
    PeriodShape.INSTANT,
)

#: This module's own refusal reasons, distinct from R1–R10's, which arrive from `comparable`
#: carrying their own rule id. Named as constants because a caller branching on a reason should
#: not have to match a string literal this file could retype.
POPULATION_TOO_SMALL = "POPULATION_TOO_SMALL"
LOW_VARIANCE_PRESENTATION_NOISE = "LOW_VARIANCE_PRESENTATION_NOISE"
SERIES_ABSENT = "SERIES_ABSENT"
RELATION_UNDECLARED = "RELATION_UNDECLARED"
#: The two metrics are not stored under one sign convention, so `left − right` is not a
#: difference. Raised per pair, before a single gap is taken. See the module docstring.
SIGN_CONVENTION_MISMATCH = "SIGN_CONVENTION_MISMATCH"

#: The rule id this module refuses under. `D4` and not `R…`: R1–R10 are comparability's and are
#: quoted verbatim when they fire, so a reader of a refusal can tell which layer declined.
RULE_ID = "D4"

#: §10.1 carries this onto the package. Raised when the anchor's population is narrower than
#: the pair's comparable overlap — the CP↔AGP formula split is the live case — so a reader is
#: told the mean was taken over part of the history rather than discovering it from a count.
#: It covers **both** ways a population narrows: R6 refusing a period against the anchor, and
#: R7 refusing a period whose slot holds no canonical value on either side.
POPULATION_EXCLUDES_PERIODS = "divergence_population_excludes_periods"

#: The `rule/reason` token a period lost to a `CONFLICT` slot is disclosed under, in the same
#: `population_excluded_by` field R6's own token appears in. Both come from `comparable`; this
#: one is named because the count that carries it is assembled a layer above the verdicts.
UNRESOLVED_PERIOD_RULE = "R7/NOT_CANONICAL"


class RelationKind(str, Enum):
    """How `formulas.yaml` relates the two metrics of a pair. Ordered by strength.

    `COMPONENT` is the strongest statement the ontology makes — one metric appears in the
    other's `component_metrics`, so the gap is a named set of adjustments and not a residual.
    `SHARED_COMPONENTS` is F3's case: two ratios over one denominator. `CO_COMPONENT` is the
    remaining half of R2's licence — both metrics appear in some third formula's component list.
    `DISTINCT_GROUP` is the weakest and is the ontology saying the two must never be merged,
    which is precisely what makes them a divergence pair (§6.9 R2).
    """

    COMPONENT = "component"
    SHARED_COMPONENTS = "shared_components"
    CO_COMPONENT = "co_component"
    DISTINCT_GROUP = "distinct_group"


@dataclass(frozen=True, slots=True)
class DivergencePair:
    """One declared pair, in the orientation `gap = left − right`.

    Orientation is part of the declaration and not derived from the ids, because the sign of
    the gap is the whole reading: `adjusted_gross_margin − gaap_gross_margin` is *"the wedge
    the adjustments open"*, and the reverse is the same number wearing a minus sign that a
    writer would have to explain.
    """

    left: str
    right: str

    @property
    def metric_ids(self) -> tuple[str, ...]:
        """Sorted, matching `StoryCandidate.metric_ids` and §6.11's digest input."""
        return tuple(sorted((self.left, self.right)))


#: §6.6 D4's pair list with the verified overlapping-quarter counts, and **without**
#: `housing_inventory_homes ↔ homes_sold`, whose overlap is zero — see the module docstring.
#: Measured on `graph-v1-0483dc6b4b10` 2026-08-04, as comparable quarters after R1–R10:
#: AGM↔GGM 25, CM↔AGM 25, CP↔AGP 25, CPAI↔CP 10, homes_purchased↔homes_sold 15. The plan's
#: 26/26/26/11/15 are the same overlaps before R6 refuses the pre-2020 `2019Q4` slot.
DIVERGENCE_PAIRS: tuple[DivergencePair, ...] = (
    DivergencePair("adjusted_gross_margin", "gaap_gross_margin"),
    DivergencePair("contribution_margin", "adjusted_gross_margin"),
    DivergencePair("contribution_profit", "adjusted_gross_profit"),
    DivergencePair("contribution_profit_after_interest", "contribution_profit"),
    DivergencePair("homes_purchased", "homes_sold"),
)


@dataclass(frozen=True, slots=True)
class DefinitionalRelation:
    """What the ontology says the two metrics are to each other (§6.6 D4).

    Every field is an ontology identifier or an ontology expression string copied verbatim.
    There is no rendered sentence here and there is none on the candidate: §6.4 puts prose in
    §11's output, derived from the package, and a detector that shipped a phrasing would have
    written the post's first clause.
    """

    kind: RelationKind
    shared_component_metrics: tuple[str, ...] = ()
    component_metric_id: str | None = None
    group_ids: tuple[str, ...] = ()
    left_formula_id: str | None = None
    right_formula_id: str | None = None
    left_expression: str | None = None
    right_expression: str | None = None


@dataclass(frozen=True, slots=True)
class GapPoint:
    """One period at which both metrics have a canonical value and may be subtracted."""

    period_key: str
    anchor_date: str
    left_value: float
    right_value: float
    left: CanonicalPoint
    right: CanonicalPoint
    warnings: tuple[str, ...] = ()

    @property
    def gap(self) -> float:
        """`v_a[t] − v_b[t]`. Derived rather than stored so it cannot disagree with the two
        values the candidate reports beside it."""
        return self.left_value - self.right_value

    @property
    def tolerance(self) -> float:
        return max(self.left.tolerance, self.right.tolerance)


@dataclass(frozen=True, slots=True)
class DivergenceRefusal:
    """A pair, shape and period this detector declined, with the rule that declined it.

    A value and not a log line, for the reason §6.9 gives for `Refuse`: *"a writer has to be
    told why"*, and a detector that emitted nothing and said nothing is indistinguishable from
    a detector that found nothing.
    """

    metric_ids: tuple[str, ...]
    period_shape: str
    period_key: str | None
    rule: str
    reason: str
    detail: str


@dataclass(frozen=True, slots=True)
class DivergenceResult:
    """Everything one run of this detector decided. Both halves, always."""

    candidates: tuple[StoryCandidate, ...] = ()
    refusals: tuple[DivergenceRefusal, ...] = ()


def detect_cross_metric_divergence(
    series_by_metric: Mapping[str, CanonicalSeries],
    *,
    graph_run_id: str,
    authority: ComparabilityAuthority | None = None,
    pairs: Sequence[DivergencePair] = DIVERGENCE_PAIRS,
    shapes: Sequence[PeriodShape] = DIVERGENCE_SHAPES,
    z_min: float = Z_MIN,
    min_population: int = MIN_POPULATION,
    variance_floor_multiple: float = VARIANCE_FLOOR_MULTIPLE,
) -> DivergenceResult:
    """§6.6 D4 over every declared pair and every shape, deterministically.

    `series_by_metric` is `build_series`'s output. The subject is read off the anchor points
    rather than taken as an argument: R1 has already refused any pair whose two sides disagree
    about it, so a second source for the same fact could only introduce a way for them to
    differ.

    The result is emitted pair by pair — sorted by their metric ids — then shape by shape in
    the order given, then anchor by anchor in `(anchor_date, period_key)` order, and every id
    list inside it is sorted. Two runs over the same points, in any input order, produce the
    same candidates with the same ids.
    """
    resolved = default_authority() if authority is None else authority
    candidates: list[StoryCandidate] = []
    refusals: list[DivergenceRefusal] = []

    for pair in sorted(pairs, key=lambda item: (item.metric_ids, item.left)):
        # Before the series are even looked up: this is a statement about the pair's
        # *declaration*, and a pair that may not be differenced may not be differenced whether
        # or not the run holds its numbers. See the module docstring for the
        # `adjusted_gross_profit − direct_selling_costs` sum this refuses.
        left_sign, right_sign = value_sign_of(pair.left), value_sign_of(pair.right)
        if left_sign is not right_sign:
            refusals.append(DivergenceRefusal(
                pair.metric_ids, "", None, RULE_ID, SIGN_CONVENTION_MISMATCH,
                f"{pair.left} is stored {left_sign.value} and {pair.right} is stored "
                f"{right_sign.value}, so {pair.left} − {pair.right} does not difference two "
                "quantities and a z-score over it measures nothing",
            ))
            continue
        left_series = series_by_metric.get(pair.left)
        right_series = series_by_metric.get(pair.right)
        if left_series is None or right_series is None:
            missing = [
                metric_id
                for metric_id, found in ((pair.left, left_series), (pair.right, right_series))
                if found is None
            ]
            refusals.append(DivergenceRefusal(
                pair.metric_ids, "", None, RULE_ID, SERIES_ABSENT,
                f"{', '.join(missing)} has no canonical series in this run, so the pair "
                f"{pair.left} − {pair.right} has nothing to difference",
            ))
            continue
        for shape in shapes:
            found, declined, unresolved = _gap_series(
                pair, left_series, right_series, shape, resolved)
            refusals.extend(declined)
            if not found:
                continue
            emitted, more = _candidates_for_shape(
                pair, found, shape, resolved,
                unresolved=unresolved,
                graph_run_id=graph_run_id,
                z_min=z_min,
                min_population=min_population,
                variance_floor_multiple=variance_floor_multiple,
            )
            candidates.extend(emitted)
            refusals.extend(more)

    return DivergenceResult(candidates=tuple(candidates), refusals=tuple(refusals))


def _gap_series(
    pair: DivergencePair,
    left_series: CanonicalSeries,
    right_series: CanonicalSeries,
    shape: PeriodShape,
    authority: ComparabilityAuthority,
) -> tuple[tuple[GapPoint, ...], tuple[DivergenceRefusal, ...], tuple[str, ...]]:
    """Every period of one shape where the two metrics may be subtracted, in series order.

    `ClaimKind.DIVERGENCE` is the only kind R2 lets name two metrics, and it is the kind that
    carries R9's cohort warning. R8 and R10 do not apply to it and that is correct: a gap is
    not a step, so there is nothing for a tolerance floor or an adjacency rule to be about.

    **Built from every point of the shape, not from `valued_points`.** A `CONFLICT` slot holds
    no value and must not enter a gap history — but filtering it out here made it vanish
    before anything counted it, and the population silently lost a period with
    `periods_excluded = 0` on the candidate. R7 is the rule that refuses a slot emitting no
    value, so the pair is handed to `comparable` and R7 declines it by name; the third return
    is those period keys, so the candidate can say how much of its history was never available.
    A period only one side reports is still skipped in silence: there is no pair to refuse.
    """
    right_points = {
        point.period.key: point
        for point in right_series.points
        if point.period.shape is shape
    }
    found: list[GapPoint] = []
    refusals: list[DivergenceRefusal] = []
    unresolved: list[str] = []
    for left in sorted(
        (point for point in left_series.points if point.period.shape is shape),
        key=lambda point: (point.period.anchor_date or "", point.period.key),
    ):
        right = right_points.get(left.period.key)
        if right is None:
            continue
        verdict = comparable(left, right, claim=ClaimKind.DIVERGENCE, authority=authority)
        if not verdict:
            refusals.append(DivergenceRefusal(
                pair.metric_ids, shape.value, left.period.key,
                verdict.rule, verdict.reason, verdict.detail))
            if verdict.rule == "R7":
                unresolved.append(left.period.key)
            continue
        assert left.value is not None and right.value is not None  # R7 passed
        found.append(GapPoint(
            period_key=left.period.key,
            anchor_date=left.period.anchor_date or "",
            left_value=float(left.value),
            right_value=float(right.value),
            left=left,
            right=right,
            warnings=tuple(sorted(warning.code for warning in verdict.warnings)),
        ))
    return tuple(found), tuple(refusals), tuple(unresolved)


def _candidates_for_shape(
    pair: DivergencePair,
    gaps: Sequence[GapPoint],
    shape: PeriodShape,
    authority: ComparabilityAuthority,
    *,
    unresolved: Sequence[str],
    graph_run_id: str,
    z_min: float,
    min_population: int,
    variance_floor_multiple: float,
) -> tuple[tuple[StoryCandidate, ...], tuple[DivergenceRefusal, ...]]:
    """One anchor at a time, each scored against the population it is comparable with."""
    candidates: list[StoryCandidate] = []
    refusals: list[DivergenceRefusal] = []
    for anchor in gaps:
        population, excluded = _population(gaps, anchor, authority)
        if len(population) < min_population:
            refusals.append(DivergenceRefusal(
                pair.metric_ids, shape.value, anchor.period_key, RULE_ID, POPULATION_TOO_SMALL,
                f"{len(population)} comparable {shape.value} periods stand behind "
                f"{anchor.period_key}, and §6.10 suppresses a z-score below {min_population}; "
                f"a mean over that few numbers describes the numbers, not a history",
            ))
            continue
        values = [point.gap for point in population]
        mean = statistics.fmean(values)
        deviation = statistics.pstdev(values)
        floor = variance_floor_multiple * max(point.tolerance for point in population)
        # The same floor, against the population the anchor is not a member of. σ over the
        # whole population passes it on a single excursion in an otherwise flat gap — 25 flat
        # quarters and one 1.05 pp step give σ = 0.20576 against a 0.2 floor and score the
        # maximum |z| a member of its own population can reach — and a dispersion the anchor
        # created by itself is not a history to measure the anchor against. The z-score below
        # is still taken over the whole population, so this refuses without moving a number.
        rest = [point.gap for point in population if point.period_key != anchor.period_key]
        deviation_without_anchor = statistics.pstdev(rest) if len(rest) > 1 else 0.0
        if deviation < floor or deviation_without_anchor < floor:
            refusals.append(DivergenceRefusal(
                pair.metric_ids, shape.value, anchor.period_key, RULE_ID,
                LOW_VARIANCE_PRESENTATION_NOISE,
                f"the gap between {pair.left} and {pair.right} has a population standard "
                f"deviation of {deviation} over {len(population)} {shape.value} periods, and "
                f"{deviation_without_anchor} over the {len(rest)} of them that are not "
                f"{anchor.period_key}; the {floor} floor that {variance_floor_multiple} × "
                "presentation tolerance sets applies to both, and a z-score here would "
                "measure rounding or measure the anchor against its own excursion",
            ))
            continue
        z_score = (anchor.gap - mean) / deviation
        if abs(z_score) < z_min:
            continue
        relation = definitional_relation(pair, authority, anchor.anchor_date)
        if relation is None:
            refusals.append(DivergenceRefusal(
                pair.metric_ids, shape.value, anchor.period_key, RULE_ID, RELATION_UNDECLARED,
                f"the ontology relates {pair.left} and {pair.right} through no shared formula "
                "component and no mutually_distinct_groups group, so the candidate could state "
                "no definitional relation and §6.6 D4 requires one",
            ))
            continue
        candidates.append(_candidate(
            pair, anchor, population, excluded, relation, shape,
            unresolved=unresolved,
            graph_run_id=graph_run_id,
            z_score=z_score,
            mean=mean,
            deviation=deviation,
            deviation_without_anchor=deviation_without_anchor,
            floor=floor,
            overlap=len(gaps),
        ))
    return tuple(candidates), tuple(refusals)


def _population(
    gaps: Sequence[GapPoint], anchor: GapPoint, authority: ComparabilityAuthority
) -> tuple[tuple[GapPoint, ...], tuple[tuple[str, str], ...]]:
    """The gaps a z-score at `anchor` may be taken against, and the period keys it may not.

    **`ClaimKind.LEVEL` and not `DIVERGENCE`**, because the question here is same-metric: *may
    this period's reading of `adjusted_gross_profit` stand beside the anchor's?* LEVEL is
    §6.9's kind for exactly that — *"it asserts two readings, not a step between them"* — so it
    applies R1–R7 and leaves R8's tolerance floor and R10's adjacency out, neither of which has
    anything to say about membership of a distribution.

    R6 is what this is for. R3 is a second beneficiary and is redundant here only because the
    caller already partitions by shape; leaving the check whole rather than narrowing it to R6
    is deliberate — a unit change or a currency change must remove a period from the population
    too, and neither needs new code.

    **A slot that turns `conflict` never reaches here**, and the first draft of this docstring
    claimed it did. R7 refuses it in `_gap_series`, one layer earlier, so it is absent from
    `gaps` before this function is called: the period is removed, correctly, but not by this
    rule and not with this function's `excluded` list to show for it. `_gap_series` returns
    those keys separately and `_candidate` discloses them as `periods_unresolved`.
    """
    kept: list[GapPoint] = []
    excluded: list[tuple[str, str]] = []
    for point in gaps:
        left = comparable(point.left, anchor.left, claim=ClaimKind.LEVEL, authority=authority)
        right = comparable(point.right, anchor.right, claim=ClaimKind.LEVEL, authority=authority)
        if left and right:
            kept.append(point)
            continue
        refusal = left if not left else right
        excluded.append((f"{refusal.rule}/{refusal.reason}", point.period_key))
    return tuple(kept), tuple(excluded)


def definitional_relation(
    pair: DivergencePair, authority: ComparabilityAuthority, as_of: str | None
) -> DefinitionalRelation | None:
    """What `formulas.yaml` and `constraints.yaml` say the two metrics are to each other.

    Read through `ComparabilityAuthority` and its registry — the ontology, resolved once — and
    never from a `:Metric` node, which carries no `distinct_from` and no `reconciles_to` to
    read (C4). `as_of` is the anchor's own date, so a pair whose formula was restated is
    described by the formula in force when the divergence happened rather than by the latest.

    `None` when the ontology relates them in none of the four ways, which R2 has already
    refused by the time this is reached; it is returned rather than raised so the refusal is a
    value like every other one here.
    """
    registry = authority.registry
    left_formula = None if registry is None else registry.formula_for(pair.left, as_of)
    right_formula = None if registry is None else registry.formula_for(pair.right, as_of)
    left_components = tuple(left_formula.component_metrics) if left_formula else ()
    right_components = tuple(right_formula.component_metrics) if right_formula else ()
    groups = tuple(sorted(
        authority.groups.get(pair.left, frozenset())
        & authority.groups.get(pair.right, frozenset())
    ))
    shared = tuple(sorted(set(left_components) & set(right_components)))

    common = dict(
        group_ids=groups,
        left_formula_id=None if left_formula is None else left_formula.concept_id,
        right_formula_id=None if right_formula is None else right_formula.concept_id,
        left_expression=None if left_formula is None else left_formula.expression,
        right_expression=None if right_formula is None else right_formula.expression,
    )
    if pair.right in left_components or pair.left in right_components:
        component = pair.right if pair.right in left_components else pair.left
        return DefinitionalRelation(
            RelationKind.COMPONENT, component_metric_id=component, **common)
    if shared:
        return DefinitionalRelation(
            RelationKind.SHARED_COMPONENTS, shared_component_metrics=shared, **common)
    if pair.right in authority.co_components.get(pair.left, frozenset()):
        return DefinitionalRelation(RelationKind.CO_COMPONENT, **common)
    if groups:
        return DefinitionalRelation(RelationKind.DISTINCT_GROUP, **common)
    return None


def _candidate(
    pair: DivergencePair,
    anchor: GapPoint,
    population: Sequence[GapPoint],
    excluded: Sequence[tuple[str, str]],
    relation: DefinitionalRelation,
    shape: PeriodShape,
    *,
    unresolved: Sequence[str],
    graph_run_id: str,
    z_score: float,
    mean: float,
    deviation: float,
    deviation_without_anchor: float,
    floor: float,
    overlap: int,
) -> StoryCandidate:
    """§6.4's field list, filled. No thesis, no score, no free text.

    `anchor_observation_ids` is the two anchor points' supporting observations and nothing
    else, so §6.11's digest covers what the candidate *asserts* — the two numbers — rather than
    the history it measured them against. A population that changed without the anchor changing
    would then reuse an id; `detector_version` is the field that mints a new one when the rule
    that built the population changes, which is what §6.11 puts it in the digest for.

    **`polarity` and `direction` are `detector_config`'s answers and never the sign of the
    gap**, exactly as `metric_move` builds them. The two sides carry their polarity separately —
    a pair is two metrics and one word could only describe one of them — and there is a single
    `gap_direction`, which is well defined *because* the pair was refused unless both sides are
    stored under one sign convention. `sign_convention` states which one. Two `UNVERIFIED`
    metrics agree with each other and are not refused, so `quantity_direction` returns `None`,
    no direction is stated, and `metric_sign_convention_unverified` says why.
    """
    observation_ids = tuple(sorted(
        set(anchor.left.supporting_observation_ids)
        | set(anchor.right.supporting_observation_ids)
    ))
    warnings = set(anchor.warnings) | set(anchor.left.warnings) | set(anchor.right.warnings)
    if excluded or unresolved:
        warnings.add(POPULATION_EXCLUDES_PERIODS)

    # The guard in `detect_cross_metric_divergence` has already refused the pair unless the two
    # agree, so either id answers for both and `gap_direction` is a statement about the gap
    # rather than about whichever metric happened to be on the left.
    sign_convention = value_sign_of(pair.left)
    gap_direction = quantity_direction(pair.left, anchor.gap - mean)
    if gap_direction is None:
        # The divergence is real and citable; only the word describing which way it went is
        # unavailable, so the candidate carries the gap rather than being dropped.
        warnings.add(SIGN_CONVENTION_UNVERIFIED)

    signals: dict[str, bool | int | float | str] = {
        "left_metric_id": pair.left,
        "right_metric_id": pair.right,
        "left_value": anchor.left_value,
        "right_value": anchor.right_value,
        "gap": anchor.gap,
        "z": z_score,
        "gap_mean": mean,
        "gap_pstdev": deviation,
        "gap_pstdev_excluding_anchor": deviation_without_anchor,
        "variance_floor": floor,
        "population_size": len(population),
        "population_first_period": population[0].period_key,
        "population_last_period": population[-1].period_key,
        # The periods the two metrics could actually be differenced over. Periods lost to a
        # `CONFLICT` slot are **not** in it — they never became a gap — so `periods_unresolved`
        # is published beside it rather than folded into it: a reader adding the two gets the
        # overlap the pair would have had, and a reader taking this one alone gets what was
        # measured. Before R4b the second number did not exist and the loss was invisible.
        "comparable_overlap": overlap,
        "periods_excluded": len(excluded),
        "periods_unresolved": len(unresolved),
        "period_shape": shape.value,
        "unit": anchor.left.unit,
        "sign_convention": sign_convention.value,
        "relation": relation.kind.value,
    }
    if gap_direction is not None:
        signals["gap_direction"] = gap_direction
    for side, metric_id in (("left", pair.left), ("right", pair.right)):
        polarity = polarity_of(metric_id)
        if polarity is not None:
            signals[f"{side}_polarity"] = polarity.value
    if excluded or unresolved:
        # The *rules* that narrowed the population, not just the counts. On `CP↔AGP` this reads
        # `R6/FORMULA_VERSION_MISMATCH`, which is the whole reason the mean was taken over one
        # formula era; a count alone would leave a reader to guess.
        rules = {rule for rule, _period in excluded}
        if unresolved:
            rules.add(UNRESOLVED_PERIOD_RULE)
        signals["population_excluded_by"] = ",".join(sorted(rules))
    if relation.shared_component_metrics:
        signals["shared_component_metrics"] = ",".join(relation.shared_component_metrics)
    if relation.component_metric_id is not None:
        signals["component_metric_id"] = relation.component_metric_id
    if relation.group_ids:
        signals["relation_group_ids"] = ",".join(relation.group_ids)
    for name, value in (
        ("left_formula_id", relation.left_formula_id),
        ("right_formula_id", relation.right_formula_id),
        ("left_formula_expression", relation.left_expression),
        ("right_formula_expression", relation.right_expression),
    ):
        if value is not None:
            signals[name] = value

    return StoryCandidate(
        candidate_id=candidate_id(
            detector_id=DETECTOR_ID,
            detector_version=DETECTOR_VERSION,
            policy_version=POLICY_VERSION,
            scope="-".join(pair.metric_ids),
            subject_entity_id=anchor.left.subject_entity_id,
            anchor_period_keys=(anchor.period_key,),
            metric_ids=pair.metric_ids,
            anchor_input_ids=observation_ids,
        ),
        detector_id=DETECTOR_ID,
        detector_version=DETECTOR_VERSION,
        policy_version=POLICY_VERSION,
        graph_run_id=graph_run_id,
        subject_entity_id=anchor.left.subject_entity_id,
        story_type=STORY_TYPE,
        metric_ids=pair.metric_ids,
        anchor_period_keys=(anchor.period_key,),
        anchor_observation_ids=observation_ids,
        signals=signals,
        warnings=tuple(sorted(warnings)),
        evidence_request=EvidenceRequest(
            metric_ids=pair.metric_ids,
            period_keys=(anchor.period_key,),
            observation_ids=observation_ids,
            # Off, though a wedge between two definitions is an obviously explanatory
            # question. The answer is already on the candidate — the two formula
            # expressions and the component they share, from `formulas.yaml` — so a
            # fulltext search would buy §10.2 budget for a question this detector has
            # answered structurally.
            want_explanatory_search=False,
        ),
        audience=Audience.EXTERNAL,
    )


__all__ = [
    "DETECTOR_ID",
    "DETECTOR_VERSION",
    "DIVERGENCE_PAIRS",
    "DIVERGENCE_SHAPES",
    "LOW_VARIANCE_PRESENTATION_NOISE",
    "MIN_POPULATION",
    "POPULATION_EXCLUDES_PERIODS",
    "POPULATION_TOO_SMALL",
    "RELATION_UNDECLARED",
    "RULE_ID",
    "SERIES_ABSENT",
    "SIGN_CONVENTION_MISMATCH",
    "STORY_TYPE",
    "UNRESOLVED_PERIOD_RULE",
    "VARIANCE_FLOOR_MULTIPLE",
    "Z_MIN",
    "DefinitionalRelation",
    "DivergencePair",
    "DivergenceRefusal",
    "DivergenceResult",
    "GapPoint",
    "RelationKind",
    "definitional_relation",
    "detect_cross_metric_divergence",
]
