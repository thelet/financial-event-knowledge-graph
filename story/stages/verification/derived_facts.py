"""§6 and §7: what a bound derived fact has to survive before its numeral counts as checked.

Responsibility: the derived half of §13. Given a `DerivedFact` a draft bound and the package it
was computed from, re-derive it — the operation's legality, the inputs' comparability, the
orientation, the unit and the result — and decide. Given an `EvidenceScopeFact`, decide whether
the sentence binding it stated a claim about the *evidence* or smuggled in a claim about the
world. No prose scanning beyond the two closed lexicons `language.py` owns, and no arithmetic
of its own: `story/core/numerals.py` and `story/core/series.py` are the authorities, exactly as
they are for `deterministic.py`'s §13.9 recomputation.

**This is a second opinion and not a shared implementation, and the duplication is the point.**
`story/stages/derivation/` computes these facts; a stage may not import a sibling stage
(`test_no_stage_imports_another_stage`), and even if it could, a verifier that asked the
producer *"is this right?"* would be asking the defendant. So §4.1's operation table, §4.2's
unit and period clauses and §4.4's result arithmetic are all restated here, over the same
`core` primitives, and `test_story_verification_derived.py` drives both sides on the same
inputs and asserts they agree. That is the arrangement `OPERATION_INPUTS` already has with
`WRITER_OPERATIONS`, applied to a stage instead of to a prompt.

**Six of §4.1's seven operations are recomputable here and `trend_direction` is not, which is a
refusal rather than a gap left open.** The direction word is not the arithmetic sign: it is
`detector_config.quantity_direction`'s answer over the metric's stored sign convention —
`direct_selling_costs` is negative on 46 of 46 canonical values, so a fall in the stored number
is a rise in the cost — and that table lives in `story/stages/detection/`, which this stage may
not import. The derivation stage takes it as an argument from the composition root; a verifier
that took it as an argument would be a verifier a caller could configure into agreement. So a
binding to a `trend_direction` fact is `derived_operation_not_supported`, on §13.9's own words:
*"an operation the verifier cannot recompute is a hole whose name the writer would choose"*.
The fact is still minted, still printed in the evidence panel, and still unbindable.

**Nothing here mints an evidence handle, and nothing here lets one be minted.** §6 requires
that a citation stay attached to the observed facts — five layers downstream assume a cited
thing was read from a filing — so a derived fact's evidence is its *inputs'* evidence, reached
through `PackageIndex.derived_inputs`, and `citations.py` is where that rule is applied.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from story.core.models import (
    DerivationOperation,
    DerivedFact,
    DisplaySemantics,
    DraftSentence,
    EvidenceScopeFact,
    FactBinding,
    PackagedFact,
    VerificationFinding,
)
from story.core.numerals import (
    RelativeChangeAcrossZero,
    SurfaceUnit,
    delta_pp,
    delta_relative,
    operation_result_surfaces,
    tokenize_numerals,
)
from story.core.periods import story_period
from story.core.series import (
    DELTA_PRECISION,
    CanonicalPoint,
    CanonicalSeries,
    CanonicalStatus,
    ClaimKind,
    Refuse,
    comparable,
)
import story.stages.verification.language as language
from story.stages.verification.codes import finding
from story.stages.verification.package_index import PackageIndex

#: §4.1's operations that assert a **step** between two periods, restated. The set is what makes
#: the orientation rule askable at all: only a two-period operation has a chronological
#: orientation to be reversed, and `compare_levels` over one period has none.
TWO_PERIOD_OPERATIONS: frozenset[DerivationOperation] = frozenset({
    DerivationOperation.ABSOLUTE_CHANGE,
    DerivationOperation.PERCENTAGE_CHANGE,
    DerivationOperation.PERCENTAGE_POINT_CHANGE,
    DerivationOperation.CROSSED_ZERO,
    DerivationOperation.TREND_DIRECTION,
})

#: §4.1's operations that compare two readings **of one period**.
SAME_PERIOD_OPERATIONS: frozenset[DerivationOperation] = frozenset({
    DerivationOperation.COMPARE_LEVELS,
    DerivationOperation.RATIO,
})

#: The operations this module can re-derive without leaving `story/core/`. See the module
#: docstring for why `trend_direction` is absent and why its absence is a refusal.
RECOMPUTABLE: frozenset[DerivationOperation] = frozenset(
    set(DerivationOperation) - {DerivationOperation.TREND_DIRECTION})

#: `numerals`' name for the two unit-preserving operations, so the result unit is asked of
#: `operation_result_surfaces` rather than restated. `difference` is that module's vocabulary
#: and `absolute_change` is §4.1's; they are one quantity.
_UNIT_PRESERVING: Mapping[DerivationOperation, str] = {
    DerivationOperation.ABSOLUTE_CHANGE: "difference",
    DerivationOperation.COMPARE_LEVELS: "compare_levels",
}

PERCENT_UNIT = "percent"
PERCENTAGE_POINTS_UNIT = "percentage_points"
MULTIPLE_UNIT = "multiple"
BOOLEAN_UNIT = "boolean"
DIRECTION_UNIT = "direction"

#: The units whose value is a word rather than a quantity. **No numeral may render one**: a
#: boolean printed as `1.0` is a numeral §13.1 would then compare against the prose, which is
#: the state `DerivedFact`'s own `result`/`result_word` validator exists to forbid at the
#: producing end. This is the same refusal at the consuming end.
NON_NUMERIC_UNITS: frozenset[str] = frozenset({BOOLEAN_UNIT, DIRECTION_UNIT})

#: What `crossed_zero` writes into `result_word`. Restated from
#: `story/stages/derivation/operations.py`, pinned by a test that imports both — the trade
#: `derivation/public.py` makes for `DIRECTION_INCREASE`, in the other direction.
CROSSED = "crossed"
DID_NOT_CROSS = "did_not_cross"

#: §13.2's surface map for a derived unit: which numeral surfaces may render each of the six
#: quantities a derived fact can be. Five rows delegate to `operation_result_surfaces`, which is
#: R8's defect-D table and already the authority on *"a gap between two percent levels is in
#: percentage points and in nothing else"*.
#:
#: **`basis_points` is absent from every row, and that is the percent-versus-percentage-point
#: gate reaching a derived result.** The same quantity in basis points is a hundred times the
#: number; admitting the surface would require scaling the recomputation by the rendering, and
#: refusing it costs a true sentence that can be rewritten in points.
#:
#: `percent` gets the one row that does not delegate. `operation_result_surfaces` has no entry
#: for it because no `Calculation` operation ever produced a percent from percent inputs —
#: `percentage_change` is the operation that does, and it did not exist until §4.1.
DERIVED_SURFACES: Mapping[str, frozenset[SurfaceUnit]] = {
    "USD": operation_result_surfaces("difference", "USD"),
    "homes": operation_result_surfaces("difference", "homes"),
    "markets": operation_result_surfaces("difference", "markets"),
    PERCENTAGE_POINTS_UNIT: operation_result_surfaces("difference", PERCENT_UNIT),
    MULTIPLE_UNIT: operation_result_surfaces("ratio", ""),
    PERCENT_UNIT: frozenset({SurfaceUnit.PERCENT}),
}

#: Which way each `DisplaySemantics` member says the quantity went, or `None` for the members
#: that state no direction. `DIRECTION_UNVERIFIABLE` is `None` because that is exactly what it
#: means — the metric's sign convention was never measured — and the fact carries
#: `metric_sign_convention_unverified` beside it, which reaches the draft as
#: `warned_observation_used` rather than as a direction nobody established.
SEMANTIC_DIRECTION: Mapping[DisplaySemantics, bool | None] = {
    DisplaySemantics.INCREASED_BY: True,
    DisplaySemantics.HIGHER_THAN: True,
    DisplaySemantics.INCREASED: True,
    DisplaySemantics.DECREASED_BY: False,
    DisplaySemantics.LOWER_THAN: False,
    DisplaySemantics.DECREASED: False,
    DisplaySemantics.UNCHANGED: None,
    DisplaySemantics.EQUAL_TO: None,
    DisplaySemantics.TIMES: None,
    DisplaySemantics.CROSSED_ZERO: None,
    DisplaySemantics.DID_NOT_CROSS_ZERO: None,
    DisplaySemantics.DIRECTION_UNVERIFIABLE: None,
}


def round_delta(value: float) -> float:
    """Every derived quantity compared here, rounded once, at `core`'s own precision.

    `13.2 − 3.3` is `9.899999999999999` and `adjusted_gross_margin` holds exactly those two
    values in adjacent quarters, so a comparison by equality would report a residue at the
    seventeenth digit as a disagreement between two correct computations. One line over
    `DELTA_PRECISION` rather than an import of `detector_config.round_delta` or of
    `derivation.operations.round_delta`: the constant is the thing that must not drift and it
    has exactly one home.
    """
    return round(value, DELTA_PRECISION)


def expected_result_unit(
    operation: DerivationOperation, input_unit: str
) -> str | None:
    """§4.1's result-unit column, or `None` when the operation does not admit that input unit.

    `None` is the percent-versus-percentage-point gate in its structural form. A difference
    between two percent levels is a number of *percentage points* and is
    `percentage_point_change`; `absolute_change` over percent inputs is therefore not a legal
    derivation at all rather than one with a debatable unit, and the same holds for
    `percentage_change`, whose relative reading of a percentage is §13.3's ambiguity. §4.1's
    table writes *"input unit"* for `absolute_change` and that column is wrong for one of the
    corpus's four units; `offers.ADMITTED_UNITS` records the same correction on the producing
    side.
    """
    if operation is DerivationOperation.RATIO:
        return MULTIPLE_UNIT
    if operation is DerivationOperation.CROSSED_ZERO:
        return BOOLEAN_UNIT
    if operation is DerivationOperation.TREND_DIRECTION:
        return DIRECTION_UNIT
    if operation is DerivationOperation.PERCENTAGE_CHANGE:
        return None if input_unit == PERCENT_UNIT else PERCENT_UNIT
    if operation is DerivationOperation.PERCENTAGE_POINT_CHANGE:
        return PERCENTAGE_POINTS_UNIT if input_unit == PERCENT_UNIT else None
    if operation is DerivationOperation.ABSOLUTE_CHANGE and input_unit == PERCENT_UNIT:
        return None
    surfaces = operation_result_surfaces(_UNIT_PRESERVING[operation], input_unit)
    named = sorted(surface.value for surface in surfaces if surface is not SurfaceUnit.NONE)
    return named[0] if named else None


def recompute(
    operation: DerivationOperation, from_value: float, to_value: float
) -> tuple[float | None, str]:
    """§4.1's arithmetic, re-derived over `numerals`. `(result, result_word)`, one of them empty.

    `to − from` for every difference and `to / from` for the ratio, so the subject of the claim
    is `to` and the base it is stated against is `from`. **`compare_levels` is signed here and
    `deterministic._recompute`'s `compare_levels` is not**, and the two are not in conflict: that
    one recomputes the *size* a comparative sentence renders and puts the direction in the word
    `claims.py` checks, while a `DerivedFact` carries its direction in `display_semantics` and
    its sign in `result` — `cross_metric_divergence`'s own `gap` signal is signed, and the
    derivation stage asserts equality against it.

    `crossed_zero` is **strictly** across, so a step that lands on zero has not crossed it.
    `relative_change_across_zero` answers the wider question — it is `True` for `from == 0` too —
    and using it here would report `0 → 5` as a sign reversal.
    """
    if operation is DerivationOperation.RATIO:
        return round_delta(to_value / from_value), ""
    if operation is DerivationOperation.PERCENTAGE_CHANGE:
        return round_delta(delta_relative(from_value, to_value)), ""
    if operation is DerivationOperation.CROSSED_ZERO:
        crossed = (from_value > 0 > to_value) or (from_value < 0 < to_value)
        return None, (CROSSED if crossed else DID_NOT_CROSS)
    return round_delta(delta_pp(from_value, to_value)), ""


def integrity_findings(
    derived: DerivedFact, index: PackageIndex, *, sentence_index: int | None = None
) -> list[VerificationFinding]:
    """Every §6 refusal about the derived fact itself, before any prose is read.

    Ordered so that a refusal never rests on an answer an earlier refusal already invalidated:
    a fact whose inputs this package does not carry has no comparability question, and one
    whose operation this module cannot re-derive has no result to compare. Where two questions
    are independent — the unit and the arithmetic — both are asked, because a reader fixing one
    has not learned the other.
    """
    if derived.package_id != index.package.package_id:
        return [finding(
            "derived_fact_not_in_run",
            sentence_index=sentence_index,
            fact_ids=(derived.fact_id,),
            expected=f"a derived fact computed from {index.package.package_id}",
            observed=f"{derived.fact_id} names package {derived.package_id}",
            explanation=(
                "§4.4: `package_id` is inside the derived fact's own id digest precisely so a "
                "derivation cannot outlive the evidence it was computed from. A fact naming "
                "another package was computed over other rows, whatever its inputs are called."),
        )]

    inputs = index.derived_inputs(derived.fact_id)
    if len(inputs) != 2:
        missing = [fact_id for fact_id in (derived.from_fact_id, derived.to_fact_id)
                   if index.fact(fact_id) is None]
        return [finding(
            "derivation_not_offered",
            sentence_index=sentence_index,
            fact_ids=(derived.fact_id, *missing),
            expected="both input ids resolving in this package's facts[]",
            observed=f"unresolved: {', '.join(missing) or '(duplicate operand)'}",
            explanation=(
                "§4.3's offer set is every `(operation, from, to)` triple over **this package's "
                "facts**, so a derivation naming an id the package does not carry is in no offer "
                "set this package could produce. The other half of §4.3 — the `max_derivations` "
                "cap and the list the planner was actually shown — is checked in the derivation "
                "stage, where the list exists; this is the half the verifier can re-derive, and "
                "it carries the same code so one fault has one name."),
            suggested_fact_ids=[fact.observation_id for fact in index.package.facts],
        )]
    from_fact, to_fact = _ordered_inputs(derived, inputs)

    if derived.operation not in RECOMPUTABLE:
        return [finding(
            "derived_operation_not_supported",
            sentence_index=sentence_index,
            fact_ids=(derived.fact_id,),
            expected=", ".join(sorted(operation.value for operation in RECOMPUTABLE)),
            observed=derived.operation.value,
            explanation=(
                "§13.9's rule, applied to a derived fact: an operation the verifier cannot "
                "recompute is a hole whose name the writer would choose. `trend_direction`'s "
                "answer is the metric's stored sign convention and not the arithmetic sign — "
                "`direct_selling_costs` is negative on 46 of 46 canonical values, so a fall in "
                "the number is a rise in the cost — and that table is in a sibling stage this "
                "one may not import. The fact stands; binding it does not."),
        )]

    found = _comparability_findings(derived, from_fact, to_fact, index, sentence_index)
    found.extend(_shape_findings(derived, from_fact, to_fact, sentence_index))
    found.extend(_unit_findings(derived, from_fact, to_fact, sentence_index))
    found.extend(_result_findings(derived, from_fact, to_fact, sentence_index))
    return found


def _ordered_inputs(
    derived: DerivedFact, inputs: Sequence[PackagedFact]
) -> tuple[PackagedFact, PackagedFact]:
    """`(from, to)` in the derivation's own declared order, never in the package's."""
    by_id = {fact.observation_id: fact for fact in inputs}
    return by_id[derived.from_fact_id], by_id[derived.to_fact_id]


def _point(fact: PackagedFact, subject_entity_id: str) -> CanonicalPoint:
    """A `PackagedFact` back into the `CanonicalPoint` R1–R10 are written against.

    **Not a second canonicalisation.** §6.1 already ran: a fact reached the package because it
    was the canonical reading of its slot, so the status is `OK` by construction. What is built
    here is the *shape* `comparable` needs, every field of it read off the row. The provenance
    fields §6.1 step 6 fills are left empty on purpose — no comparability rule reads one, and
    `CanonicalPoint`'s own docstring forbids recomputing them from a package.
    """
    return CanonicalPoint(
        metric_id=fact.metric_id,
        period=story_period(fact.period_start, fact.period_end, fact.instant_date),
        subject_entity_id=subject_entity_id,
        status=CanonicalStatus.OK,
        value=fact.value,
        unit=fact.unit,
        scale=fact.scale,
        currency=fact.currency,
        representative_observation_id=fact.observation_id,
    )


def _claim_kind(
    derived: DerivedFact, from_fact: PackagedFact, to_fact: PackagedFact
) -> ClaimKind:
    """Which of §6.9's claim kinds this operation makes — the argument R2, R8 and R10 turn on.

    A two-period operation asserts a step and is `MOVEMENT`, which is what brings R8's
    presentation-tolerance floor and R10's adjacency to bear. A same-period operation over two
    metrics is `DIVERGENCE`, the only kind R2 admits two metrics under. Over one metric it is
    `LEVEL`, which §6.1's one-canonical-fact-per-slot guarantee makes unreachable in this corpus
    and which is written anyway because the rule is about what a claim asserts.
    """
    if derived.operation in TWO_PERIOD_OPERATIONS:
        return ClaimKind.MOVEMENT
    return (ClaimKind.DIVERGENCE if from_fact.metric_id != to_fact.metric_id
            else ClaimKind.LEVEL)


def _comparability_findings(
    derived: DerivedFact,
    from_fact: PackagedFact,
    to_fact: PackagedFact,
    index: PackageIndex,
    sentence_index: int | None,
) -> list[VerificationFinding]:
    """§6.9's R1–R10 over the two inputs, run again here rather than read off the fact.

    `DerivedFact.comparability_rule_ids` records which rules the *producer* evaluated, and a
    verifier that read that field would be accepting the producer's word for the producer's
    work. `comparable` is `story/core/series.py`'s and both sides call it, so this is one
    authority consulted twice and not two implementations that can disagree.

    `comparable(to, from)` and not the other way round: R8's own refusal sentence reads *"moved
    from `right` to `left`"*, and passing them reversed would make every refusal message
    describe the opposite of the claim being made.
    """
    subject = index.package.subject.entity_id
    to_point, from_point = _point(to_fact, subject), _point(from_fact, subject)
    series = CanonicalSeries(
        metric_id=to_fact.metric_id,
        points=tuple(sorted(
            (_point(fact, subject) for fact in index.package.facts
             if fact.metric_id == to_fact.metric_id),
            key=lambda point: (point.period.anchor_date or "", point.period.key),
        )),
    )
    answer = comparable(
        to_point, from_point, claim=_claim_kind(derived, from_fact, to_fact), series=series)
    if not isinstance(answer, Refuse):
        return []
    return [finding(
        "derived_inputs_incomparable",
        sentence_index=sentence_index,
        fact_ids=(derived.fact_id, from_fact.observation_id, to_fact.observation_id),
        expected=f"R1-R10 to permit {from_fact.observation_id} against {to_fact.observation_id}",
        observed=f"{answer.rule} refused: {answer.reason}: {answer.detail}",
        explanation=(
            "§4.2: a derivation is permitted only where §6.9's comparability rules permit the "
            "comparison. Re-derived here over `story/core/series.py` rather than read off the "
            "fact's own `comparability_rule_ids`, because a verifier that took the producer's "
            "record of its own validation would be checking nothing."),
    )]


def _shape_findings(
    derived: DerivedFact,
    from_fact: PackagedFact,
    to_fact: PackagedFact,
    sentence_index: int | None,
) -> list[VerificationFinding]:
    """§13.4 over the derivation itself: the periods it names, and the direction it runs in.

    **The chronological half is a new guarantee and not a restatement, which is worth saying
    plainly.** Nothing in §13 has ever checked that a two-input operation's first input is the
    earlier reading. The convention is a docstring — `_recompute`'s *"input order is `(base,
    subject)`"* — and for `compare_levels` and `compare_deltas` that function returns
    `abs(...)`, so a swapped pair recomputes to the identical number and the swap is invisible.
    `absolute_change` over `$556M → $110M` is `−$446M` and over `$110M → $556M` is `+$446M`;
    those are two derivations with two ids and two `display_semantics`, and only the anchor
    dates tell them apart.

    A same-period operation is checked the other way: two readings of one period, so equal
    anchors and a refusal when they differ.

    **This disagrees with `offers.offers()`'s docstring and the disagreement is deliberate.**
    That function offers *both* orientations of every pair and says
    `derived_fact_orientation_reversed` is *"a check on the draft — whether the prose runs the
    way the fact does — and not a reason to withhold the fact"*. Both halves are implemented:
    the prose half is `orientation_findings`, and this is the half the brief asks for in so many
    words — *"today nothing checks that `input_observation_ids[0]` is the chronologically
    earlier fact"*. A backwards two-period derivation is a defined computation and not a
    readable claim: `$110M -> $556M` presented as a movement asserts a step from the third
    quarter to the second, and there is no sentence that is true of. The derivation stage
    already treats it as unchecked — `_detector_agreement` records no signal reuse for a
    reversed request, because the candidate's `delta` is `later - earlier` — so refusing here is
    refusing a quantity nothing else verified either. A writer that wants the rise binds the
    forward derivation, which is offered.
    """
    found: list[VerificationFinding] = []
    declared = ((derived.from_period, from_fact.period_key),
                (derived.to_period, to_fact.period_key))
    mismatched = [(stated, actual) for stated, actual in declared if stated != actual]
    if mismatched:
        found.append(finding(
            "derived_fact_orientation_reversed",
            sentence_index=sentence_index,
            fact_ids=(derived.fact_id, from_fact.observation_id, to_fact.observation_id),
            expected=f"from_period={from_fact.period_key}, to_period={to_fact.period_key}",
            observed=f"from_period={derived.from_period}, to_period={derived.to_period}",
            explanation=(
                "§4.4: `from_period` and `to_period` are what a `FactBinding`'s "
                "`period_surface` is filled from, so a pair that does not name the inputs' own "
                "periods puts a period on the post that no observation was read over."),
        ))

    from_anchor = from_fact.period_end or from_fact.instant_date or ""
    to_anchor = to_fact.period_end or to_fact.instant_date or ""
    if derived.operation in TWO_PERIOD_OPERATIONS and not from_anchor < to_anchor:
        found.append(finding(
            "derived_fact_orientation_reversed",
            sentence_index=sentence_index,
            fact_ids=(derived.fact_id, from_fact.observation_id, to_fact.observation_id),
            expected=f"{from_fact.observation_id} to be the earlier reading",
            observed=(f"from anchors at {from_anchor or '(none)'} and to at "
                      f"{to_anchor or '(none)'}"),
            explanation=(
                "§4.1: a two-period operation is `to − from` and states a step forward in time. "
                "Reversed, it is a different derivation with a different id, a different sign "
                "and the opposite `display_semantics` — and nothing before this checked it: "
                "`_recompute` takes `abs(...)` for both comparisons, so the swap recomputes to "
                "the same number."),
        ))
    if derived.operation in SAME_PERIOD_OPERATIONS and from_anchor != to_anchor:
        found.append(finding(
            "derived_fact_orientation_reversed",
            sentence_index=sentence_index,
            fact_ids=(derived.fact_id, from_fact.observation_id, to_fact.observation_id),
            expected=f"{derived.operation.value} over two readings of one period",
            observed=f"{from_fact.period_key} against {to_fact.period_key}",
            explanation=(
                "§4.1: `compare_levels` and `ratio` compare two readings of one period. Across "
                "two periods a gap is a step and a ratio is a multiple of a metric's former "
                "self, which `absolute_change` and `percentage_change` already express with a "
                "unit and a direction word these two cannot."),
        ))
    return found


def _unit_findings(
    derived: DerivedFact,
    from_fact: PackagedFact,
    to_fact: PackagedFact,
    sentence_index: int | None,
) -> list[VerificationFinding]:
    """§4.1's result-unit column, re-derived. The percent-versus-percentage-point gate.

    Skipped when the two inputs disagree about their unit, because `derived_inputs_incomparable`
    has already refused that through R4 and a second code for one fault helps nobody.
    """
    if from_fact.unit != to_fact.unit:
        return []
    expected = expected_result_unit(derived.operation, to_fact.unit)
    if expected == derived.unit:
        return _currency_findings(derived, sentence_index)
    return [finding(
        "derived_unit_mismatch",
        sentence_index=sentence_index,
        fact_ids=(derived.fact_id,),
        expected=(f"{expected}" if expected is not None else
                  f"{derived.operation.value} not to be defined over {to_fact.unit} inputs"),
        observed=f"unit={derived.unit!r} over {to_fact.unit} inputs",
        explanation=(
            "§4.1 and §13.3: the difference between two percent levels is a number of "
            "percentage points, the relative change of one is a percent, and they are separate "
            "operations so that the wrong one is a refusal rather than a rendering caught "
            "afterwards. §13.3 calls the confusion between them the single most likely factual "
            "error this package can make; on adjusted_ebitda_margin 5.2 -> 2.2 the two readings "
            "differ by 19.2x."),
    )]


def _currency_findings(
    derived: DerivedFact, sentence_index: int | None
) -> list[VerificationFinding]:
    """§13.2, on the derived row rather than on the numeral that renders it.

    A currency surviving an operation that divided it out is the defect: a relative change and a
    ratio are dimensionless, and a `currency` carried forward would let §13.2's binding-level
    check accept a dollar sign on a percentage.
    """
    if derived.unit == "USD" or derived.currency is None:
        return []
    return [finding(
        "derived_unit_mismatch",
        sentence_index=sentence_index,
        fact_ids=(derived.fact_id,),
        expected=f"no currency on a {derived.unit} result",
        observed=f"currency={derived.currency!r}",
        explanation=(
            "§13.2: a percentage, a multiple and a direction are dimensionless. A currency that "
            "survived the division that should have cancelled it is a currency §13.2's "
            "binding-level check would then accept a `$` for."),
    )]


def _result_findings(
    derived: DerivedFact,
    from_fact: PackagedFact,
    to_fact: PackagedFact,
    sentence_index: int | None,
) -> list[VerificationFinding]:
    """The arithmetic, re-derived from the package's own values (§6's `derived_result_mismatch`).

    Compared after rounding at `DELTA_PRECISION` and never by equality, for
    `matches_at_printed_precision`'s reason: `3.3 − (−12.6)` is `15.899999999999999`.

    A `percentage_change` across zero raises rather than returning a number, and it is reported
    under §13.3's existing `relative_change_across_zero` rather than under a new code — the gate
    already carries the refusal for exactly this quantity, and `adjusted_ebitda_margin` crosses
    zero six times in 26 quarters, so this is the common case for the metric a percent story is
    most likely to be about.
    """
    try:
        result, result_word = recompute(derived.operation, from_fact.value, to_fact.value)
    except RelativeChangeAcrossZero as refused:
        return [finding(
            "relative_change_across_zero",
            sentence_index=sentence_index,
            fact_ids=(derived.fact_id, from_fact.observation_id, to_fact.observation_id),
            expected="percentage_point_change, or absolute_change",
            observed=str(refused),
            explanation=(
                "§13.3's third gate, reached through a derived fact. (−6.3 − 5.2)/|5.2| = −221% "
                "is arithmetically defined and rhetorically meaningless, and `offers.validate` "
                "refuses it before a fact can exist — so a fact carrying it is one built against "
                "other values than the ones this package holds."),
        )]

    if result is None:
        if derived.result_word == result_word and derived.result is None:
            return []
        stated = derived.result_word if derived.result is None else repr(derived.result)
    else:
        if derived.result is not None and round_delta(derived.result) == round_delta(result):
            return []
        stated = repr(derived.result) if derived.result is not None else derived.result_word
    return [finding(
        "derived_result_mismatch",
        sentence_index=sentence_index,
        fact_ids=(derived.fact_id, from_fact.observation_id, to_fact.observation_id),
        expected=repr(result) if result is not None else result_word,
        observed=f"{stated} for {derived.operation.value} over "
                 f"{from_fact.value!r} -> {to_fact.value!r}",
        explanation=(
            "§6: the verifier re-derives the result from the package's own values through "
            "`story/core/numerals.py`, the same module the derivation stage delegates to. Two "
            "code paths computing one number and differing is a defect in one of them, and the "
            "artifact does not get to be the tie-breaker for its own arithmetic."),
    )]


def orientation_findings(
    sentence: DraftSentence, binding: FactBinding, derived: DerivedFact
) -> list[VerificationFinding]:
    """§6's other orientation rule: the sentence's own **change verb** against the fact's.

    A two-period derivation states a movement, and `display_semantics` is the closed word code
    computed for it — the field that exists so the writer cannot infer a direction from the sign
    of `result`, which on a cost metric would be backwards. *"Adjusted gross profit **rose**"*
    over a fact whose semantics are *"decreased by"* is the same number and the opposite claim,
    and before this nothing read that word: §13.1 checks the numeral, §13.4 the period, §13.5
    the metric, and §13.14's direction rule only ever ran behind a declared `Calculation`, which
    §6 retires.

    **Change verbs only, and comparatives are `claims.py`'s.** A change verb has one subject —
    the metric the derivation is of — so the word alone decides the claim. A comparative has two
    sides, and *"GAAP gross margin was 15.9 points **lower** than adjusted"* and *"adjusted was
    15.9 points **higher** than GAAP"* are the same true claim in opposite words; deciding
    between them needs the alias index over the text either side of the term, which is
    `claims._comparison_sides_findings`' machinery and is where the `compare_levels` half lives.

    A word `CHANGE_DIRECTION` leaves at `None` states no direction of the stored number —
    `improved`, `widened`, `narrowed` — and the rule abstains rather than inventing a claim the
    sentence did not make. `DisplaySemantics` members with no direction abstain the same way,
    `DIRECTION_UNVERIFIABLE` among them: that fact carries
    `metric_sign_convention_unverified` and reaches the reader as `warned_observation_used`.
    """
    if derived.operation not in TWO_PERIOD_OPERATIONS:
        return []
    wanted = SEMANTIC_DIRECTION.get(derived.display_semantics)
    if wanted is None:
        return []
    stated = [
        (match, direction)
        for match, direction in (
            (match, language.change_direction(match.term))
            for match in language.change_verbs(sentence.text)
        )
        if direction is not None and direction is not wanted
    ]
    if not stated:
        return []
    match, _ = stated[0]
    return [finding(
        "derived_fact_orientation_reversed",
        sentence_index=sentence.index,
        char_start=match.start, char_end=match.end,
        fact_ids=(derived.fact_id,),
        expected=(f"a word agreeing with {derived.display_semantics.value!r} "
                  f"({derived.from_period} -> {derived.to_period})"),
        observed=f"the sentence says {match.term!r}",
        explanation=(
            "§6: `display_semantics` is a closed word code computed, and it exists so the writer "
            "cannot infer a direction from the sign of `result` — `direct_selling_costs` is "
            "stored negative on 46 of 46 canonical values, so a fall in the number is a rise in "
            "the cost. A sentence stating the opposite direction is the same number and the "
            "opposite claim."),
    )]


def scope_findings(
    sentence: DraftSentence, binding: FactBinding, scope: EvidenceScopeFact
) -> list[VerificationFinding]:
    """§7: a bounded claim about the *evidence*, and nothing stronger.

    Three refusals, and each is one half of the failure §7 names — *"today that sentence reuses
    a financial-table citation as though the table said it"*.

    1. **No numeral inside the bound span.** An evidence-scope fact has no value; a numeral
       there would be a quantity nothing could compare against, which is `unbound_numeral`'s
       whole subject. Refused under `binding_rendering_is_not_one_numeral`, the code that
       already says *"this span holds the wrong number of numerals"* — for an observation the
       right number is one and here it is zero.
    2. **No content word the fact's own `statement` does not carry.** Rule B's lexical
       grounding, with the statement in place of a cited span: the statement is the only text
       that licenses this claim, so *"the evidence in this package supplies no explanation"*
       grounds and *"there was no cause"* does not. This is what makes *"impossible to use one
       to claim something about the world"* a check rather than an intention.
    The third refusal — *"no citation on the sentence at all"* — is `citations.py`'s, because
    that is where a sentence's citations are walked and where the finding's denominator lives.
    `EvidenceScopeFact` carries no `citations` field and mints no evidence handle, so any
    `PassageCitation` beside one is a filed passage presented as the source of a claim about
    what the filings do not contain.
    """
    found: list[VerificationFinding] = []
    numerals = tokenize_numerals(binding.rendered)
    if numerals:
        found.append(finding(
            "binding_rendering_is_not_one_numeral",
            sentence_index=sentence.index,
            char_start=binding.char_start, char_end=binding.char_end,
            fact_ids=(scope.fact_id,),
            expected="no numeral in a span bound to an evidence-scope fact",
            observed=", ".join(token.text for token in numerals),
            explanation=(
                "§7: an evidence-scope fact states what the package does not contain and holds "
                "no value. A numeral bound to it is a quantity §13.1 has nothing to compare "
                "against, which is the state that rule exists to make impossible."),
        ))

    ungrounded = language.ungrounded_words(binding.rendered, scope.statement)
    if ungrounded:
        found.append(finding(
            "uncited_factual_sentence",
            sentence_index=sentence.index,
            char_start=binding.char_start, char_end=binding.char_end,
            fact_ids=(scope.fact_id,),
            expected=f"a span grounded in {scope.claim}'s own statement",
            observed=", ".join(ungrounded),
            explanation=(
                "§7: the statement is about the evidence and never about the world — *\"the "
                "evidence in this package supplies no explanation\"*, never *\"there was no "
                "cause\"*. Nothing but the statement licenses this claim, so a content word the "
                "statement does not carry is a claim the package cannot establish. Grounded "
                "through §13.7 Rule B's own lexical test, with the statement in place of a "
                "cited span."),
        ))

    return found


__all__ = [
    "CROSSED",
    "DERIVED_SURFACES",
    "DID_NOT_CROSS",
    "NON_NUMERIC_UNITS",
    "RECOMPUTABLE",
    "SAME_PERIOD_OPERATIONS",
    "SEMANTIC_DIRECTION",
    "TWO_PERIOD_OPERATIONS",
    "expected_result_unit",
    "integrity_findings",
    "orientation_findings",
    "recompute",
    "round_delta",
    "scope_findings",
]
