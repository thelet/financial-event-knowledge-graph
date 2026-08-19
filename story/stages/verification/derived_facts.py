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
#:
#: **A `None` here is no longer an exit from the rule (H1).** `_direction_findings` refuses every
#: directional word over a `None` member and demands none, and `_crossing_findings` takes the two
#: crossing members over to their own vocabulary — so `None` now means *"this derivation states
#: no direction, and neither may the sentence"* rather than *"nothing to check"*.
#: `DIRECTION_UNVERIFIABLE` is the sharpest case: a fact that explicitly declined to name a
#: direction was the one over which any direction word passed.
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

#: The two members that answer a **sign question** rather than a direction question, and the
#: reason `SEMANTIC_DIRECTION` leaves both at `None`: a crossing is not a rise or a fall.
#: `True` asserts the quantity crossed zero. Read by `_crossing_findings`, which is the prose
#: rule H1 added — before it, these two members reached no check at all.
CROSSING_SEMANTICS: Mapping[DisplaySemantics, bool] = {
    DisplaySemantics.CROSSED_ZERO: True,
    DisplaySemantics.DID_NOT_CROSS_ZERO: False,
}

#: Which **side of zero** each of `language.CROSSING_TERMS`' phrases asserts, as
#: `(from end, to end)`: `True` for *"this reading is positive"*, `False` for *"negative"*,
#: `None` for *"this phrase says nothing about that end"*.
#:
#: **This lexicon exists because H1's crossing rule checked one axis of a two-axis claim.**
#: `_crossing_findings` asked only *"does the prose agree that the quantity crossed"*, and most
#: of the phrases it scans assert a polarity as well. H2 §14.3 reproduced the consequence and
#: left it: over a `crossed_zero` fact whose readings are `-556,000,000 -> -110,000,000` — both
#: **negative**, so `did_not_cross` — *"Adjusted gross profit **remained positive** between the
#: second quarter of 2022 and the third quarter of 2022"* was **accepted with zero findings**.
#: The operation's own answer was right, the crossing axis agreed, and the sentence stated the
#: opposite sign. `crossed_zero` carries no numeral, so §13.1 reaches nothing in that sentence
#: and no other rule was going to.
#:
#: **Both ends, because a phrase can name both.** *"swung from a profit to a loss"* asserts a
#: positive `from` and a negative `to`; *"turned negative"* asserts only the `to`; *"remained
#: positive"* asserts both are positive. The pair is what lets one map answer all three without
#: a second rule per shape.
#:
#: **The seven neutral members are neutral about the sign and not about the crossing.** *"on the
#: same side of zero"*, *"held its sign"*, *"kept its sign"*, *"without crossing zero"*, *"did
#: not cross"*, *"did not cross zero"* and *"does not cross zero"* say the two readings share a
#: side without saying which, which is exactly `crossed_zero`'s own answer; the four `crossed`
#: members that name no side — *"crossed zero"*, *"reversed sign"*, *"changed sign"*, *"sign
#: reversal"* — are the same case in the other branch. They are the wordings that assert
#: nothing beyond what code computed, which is why `POLARITY_NEUTRAL_CROSSING_PHRASES` in the
#: tests is the `did_not_cross` half of this row.
#:
#: **`did not turn negative` asserts a positive `to` end, and that is a judgment worth stating.**
#: Read literally it is true of a quantity that was already negative — it did not *turn*. Read
#: as an investor reads it, it says the quantity is not negative now, and over `-556M -> -110M`
#: that is false. The rule takes the second reading, which costs a pedantically-true sentence
#: and admits no false one; both phrases collide with §13.14's `ABSENCE_TERMS` on `"did not"`
#: anyway, so neither is writable today for a reason that predates S13.
#:
#: Totality against `language.CROSSING_TERMS` is asserted by test, and `_crossing_findings`
#: refuses a phrase this map does not carry rather than abstaining on it —
#: `crossing_direction`'s discipline, for the reason H1 wrote it down: a lexicon that decides
#: whether a rule runs is not a rule.
CROSSING_POLARITY: Mapping[str, tuple[bool | None, bool | None]] = {
    # -- asserts a crossing, naming neither side ---------------------------------------------
    "crossed zero": (None, None), "crosses zero": (None, None),
    "cross zero": (None, None), "crossing zero": (None, None),
    "reversed sign": (None, None), "changed sign": (None, None),
    "sign reversal": (None, None),
    # -- asserts a crossing, naming the side it ended on -------------------------------------
    "crossed into negative territory": (None, False),
    "crossed into positive territory": (None, True),
    "into negative territory": (None, False),
    "into positive territory": (None, True),
    "turned negative": (None, False), "turned positive": (None, True),
    "turns negative": (None, False), "turns positive": (None, True),
    "went negative": (None, False), "went positive": (None, True),
    "swung to a loss": (None, False), "swung to a profit": (None, True),
    "flipped negative": (None, False), "flipped positive": (None, True),
    "flipped to a loss": (None, False), "flipped to a profit": (None, True),
    "fell below zero": (None, False), "rose above zero": (None, True),
    # -- asserts a crossing, naming both sides -----------------------------------------------
    "swung from a profit to a loss": (True, False),
    "swung from profit to loss": (True, False),
    "swung from a loss to a profit": (False, True),
    "swung from loss to profit": (False, True),
    "from profit to loss": (True, False),
    "from loss to profit": (False, True),
    # -- asserts no crossing, naming no side -------------------------------------------------
    "did not cross zero": (None, None), "did not cross": (None, None),
    "does not cross zero": (None, None), "without crossing zero": (None, None),
    "on the same side of zero": (None, None),
    "held its sign": (None, None), "kept its sign": (None, None),
    # -- asserts no crossing, naming the side both readings sit on ---------------------------
    "stayed positive": (True, True), "stayed negative": (False, False),
    "remained positive": (True, True), "remained negative": (False, False),
    "did not turn negative": (None, True), "did not turn positive": (None, False),
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
    sentence: DraftSentence, binding: FactBinding, derived: DerivedFact, index: PackageIndex
) -> list[VerificationFinding]:
    """§6's other orientation rule: the sentence's own **words** against the fact's word code.

    A two-period derivation states a movement, and `display_semantics` is the closed word code
    computed for it — the field that exists so the writer cannot infer a direction from the sign
    of `result`, which on a cost metric would be backwards. *"Adjusted gross profit **rose**"*
    over a fact whose semantics are *"decreased by"* is the same number and the opposite claim,
    and before this nothing read that word: §13.1 checks the numeral, §13.4 the period, §13.5
    the metric, and §13.14's direction rule only ever ran behind a declared `Calculation`, which
    §6 retires.

    **H1 turned this from an abstaining rule into a rule, and the asymmetry it removes is the
    argument.** As shipped, the check ran only if `language.change_verbs` matched one of
    `CHANGE_DIRECTION`'s thirteen words, so any synonym outside them made it fall silent: the
    review drove `grew`, `climbed`, `surged`, `gained`, `jumped` and `expanded` through it and
    every one was **accepted** over a fact reading `decreased by` / `-446000000.0`, as was
    *"Adjusted gross profit **was** $446 million"* — a change stated as a level, with no verb to
    match at all. Meanwhile the comparative half of the same question failed **closed**:
    `claims._comparison_text_findings` refuses a `compare_levels` sentence carrying no
    recognised comparative, and `comparative_direction` returning `None` is a refusal by its own
    docstring. Two halves of one guarantee cannot fail in opposite directions. So the vocabulary
    no longer decides whether the check runs — a directional derivation whose sentence states no
    word carrying the computed direction is `derived_direction_not_stated_in_text`, and
    `CHANGE_DIRECTION` was widened in the same change so that the refusal lands on prose that
    says nothing rather than on prose that says it differently.

    **Change verbs and crossing phrases; comparatives are `claims.py`'s.** A change verb has one
    subject — the metric the derivation is of — so the word alone decides the claim. A
    comparative has two sides, and *"GAAP gross margin was 15.9 points **lower** than
    adjusted"* and *"adjusted was 15.9 points **higher** than GAAP"* are the same true claim in
    opposite words; deciding between them needs the alias index over the text either side of the
    term, which is `claims._comparison_sides_findings`' machinery and is where the
    `compare_levels` half lives. A `crossed_zero` derivation answers in neither vocabulary and is
    dispatched to `_crossing_findings`.

    **`index` is taken for the crossing branch's sign question, and taking it is the point.** H3's
    polarity rule asks which side of zero each reading sits on, and it asks the *package* rather
    than the fact's own `from_value`/`to_value`: nothing validates those two fields against the
    rows they were read from — `_result_findings` compares the recomputed *result* and a
    `crossed_zero` result is one bit, so a fact declaring `(556, 110)` over rows reading
    `(-556, -110)` recomputes to `did_not_cross` either way and passes. `_result_findings`' own
    sentence governs here too: *the artifact does not get to be the tie-breaker for its own
    arithmetic*, and a sign is arithmetic.
    """
    if derived.operation not in TWO_PERIOD_OPERATIONS:
        return []
    crossing = CROSSING_SEMANTICS.get(derived.display_semantics)
    if crossing is not None:
        return _crossing_findings(sentence, derived, crossing, index)
    return _direction_findings(sentence, derived)


def _direction_findings(
    sentence: DraftSentence, derived: DerivedFact
) -> list[VerificationFinding]:
    """The change-verb half. Two refusals, and the second is the one H1 adds.

    1. **A word carrying the opposite direction** — unchanged in force and wider in reach, since
       `CHANGE_DIRECTION` now carries the synonyms the review walked through it.
    2. **No word carrying the computed direction at all.** A derivation whose
       `display_semantics` states a direction is a claim about which way the quantity moved, and
       a sentence that binds its number without saying so has stated a level where code computed
       a change. *"Adjusted gross profit was $446 million in the third quarter of 2022"* is
       false — the 2022Q3 level is $110M — and every declared field in it is valid.

    **A `display_semantics` with no direction refuses every direction word and requires none**,
    which is the fail-closed reading of the two such members a two-period operation can actually
    produce. `UNCHANGED` says the quantity did not move, so any directional verb contradicts it
    and there is no direction to demand. `DIRECTION_UNVERIFIABLE` is the stronger case: the
    metric's sign convention was never measured — `direct_selling_costs` is stored negative on
    46 of 46 canonical values — so *nobody* established which way it went, and a sentence that
    names a direction is asserting what the derivation explicitly declined to. Both abstained
    entirely before H1, and the second is the sharper miss: the one fact that says *"the
    direction is unknown"* was the one over which any direction word passed. `EQUAL_TO` and
    `TIMES` are `SEMANTIC_DIRECTION`'s other `None`s and belong to `compare_levels` and `ratio`,
    which `orientation_findings` returns on before reaching here.

    A word `CHANGE_DIRECTION` leaves at `None` — `improved`, `widened`, `turned` — neither
    contradicts nor satisfies: it states desirability, magnitude or an unnamed sign change and
    not the direction of the stored number. Under rule 2 a sentence carrying only such words is
    refused for not stating the direction, which is where the abstention went.

    **The whole sentence is scanned for every binding, and one sentence stating two derivations
    that moved opposite ways is therefore refused.** *"X fell $446 million while Y rose $12
    million"* raises `derived_fact_orientation_reversed` on both. That is a true sentence lost,
    and it is left standing: attributing a verb to one of two bindings needs clause attribution
    this module does not have and `FactBinding` carries no field for, which is the same reason
    §13.10 conditions 3 and 6 are recorded as unimplementable in `language.py`'s docstring. The
    widened lexicon makes it likelier than it was, so it is named here rather than discovered.
    Rule 1 already had this shape before H1; rule 2 does not add to it, because a sentence with
    an agreeing word for each fact satisfies both.
    """
    wanted = SEMANTIC_DIRECTION.get(derived.display_semantics)
    stated = [
        (match, language.change_direction(match.term))
        for match in language.change_verbs(sentence.text)
    ]
    directional = [(match, direction) for match, direction in stated if direction is not None]
    contradicting = [match for match, direction in directional if direction is not wanted]
    if contradicting:
        match = contradicting[0]
        return [finding(
            "derived_fact_orientation_reversed",
            sentence_index=sentence.index,
            char_start=match.start, char_end=match.end,
            fact_ids=(derived.fact_id,),
            expected=(f"a word agreeing with {derived.display_semantics.value!r} "
                      f"({derived.from_period} -> {derived.to_period})"
                      if wanted is not None else
                      f"no direction word: this derivation says "
                      f"{derived.display_semantics.value!r}"),
            observed=f"the sentence says {match.term!r}",
            explanation=(
                "§6: `display_semantics` is a closed word code computed, and it exists so the "
                "writer cannot infer a direction from the sign of `result` — "
                "`direct_selling_costs` is stored negative on 46 of 46 canonical values, so a "
                "fall in the number is a rise in the cost. A sentence stating the opposite "
                "direction is the same number and the opposite claim, and a sentence stating "
                "any direction over a derivation that established none is a claim nothing "
                "computed."),
        )]
    if wanted is None:
        return []
    if any(direction is wanted for _match, direction in directional):
        return []
    return [finding(
        "derived_direction_not_stated_in_text",
        sentence_index=sentence.index,
        fact_ids=(derived.fact_id,),
        expected=(f"a word saying the quantity went "
                  f"{'up' if wanted else 'down'} — {derived.display_semantics.value!r} over "
                  f"{derived.from_period} -> {derived.to_period}"),
        observed=("the sentence states no change word: "
                  + (", ".join(repr(match.term) for match, _ in stated)
                     if stated else "none at all")
                  + f" — {sentence.text!r}"),
        explanation=(
            "§6: a directional derivation is a claim about which way a quantity moved, and a "
            "sentence carrying its number without saying so has stated a level where code "
            "computed a change. Measured: *\"Adjusted gross profit was $446 million in the "
            "third quarter of 2022\"* was accepted over the -$446M fall, and the 2022Q3 level "
            "is $110M. The rule refuses rather than abstains because an abstaining rule is not "
            "a rule — six synonyms outside the old lexicon each turned this check off."),
    )]


def _crossing_findings(
    sentence: DraftSentence, derived: DerivedFact, crossed: bool, index: PackageIndex
) -> list[VerificationFinding]:
    """`crossed_zero`'s prose, which nothing checked at all before H1.

    The operation is offerable, its result is recomputable — `_result_findings` re-derives the
    word from the package's own values — and **the sentence stating it was unchecked in every
    direction**: `SEMANTIC_DIRECTION[CROSSED_ZERO]` is `None` so the change-verb rule abstained,
    the fact carries no numeral so §13.1 had nothing to compare, and `language.ungrounded_words`
    was applied only to an `EvidenceScopeFact`. *"Adjusted gross profit **turned negative**
    between the second quarter of 2022 and the third quarter of 2022."* was accepted with zero
    findings over `$556M -> $110M`, which does not cross.

    Three refusals, on `_direction_findings`' shape: a phrase asserting the opposite crossing,
    a phrase asserting a **sign** the readings do not have, and no phrase at all. The last is
    what makes this a rule rather than a lexicon — an unlisted paraphrase costs a true sentence
    instead of admitting a false one, which is the direction §13.14 says the failure should
    point.

    **The sign question is H3's, and H2 §14.3 reported it as a hole with the repro.** The
    crossing axis is one of two axes most of these phrases run on. Over a fact whose readings
    are `-556,000,000 -> -110,000,000` the operation answers `did_not_cross` and *"remained
    positive"*, *"stayed positive"*, *"remained negative"* and *"on the same side of zero"* all
    agreed with that answer — the first two while asserting the opposite sign, accepted with
    zero findings. `_polarity_findings` is the second axis, read off `CROSSING_POLARITY` and
    checked against the **package's** two readings.

    **Order: crossing first, then sign, and only the first refusal is reported.** A phrase that
    gets the crossing wrong has already lost the operation's own answer, and adding a second
    finding about the sign of a claim that was never the claim would name one fault twice —
    `integrity_findings`' ordering argument, applied to prose.

    **Withdrawing `crossed_zero` from the offer set was the alternative and it was rejected.**
    That is what §12.2 did to `trend_direction`, on the ground that the verifier could not
    recompute the word at all without importing a sibling stage's sign-convention table. This
    verifier *can* recompute this word, from two values it already holds; only the prose was
    unguarded, and the repair for unguarded prose is a guard. H3 does not reopen the decision:
    the second axis is recomputable from the same two values, so the argument that kept the
    operation is the argument that closes this hole.
    """
    stated = [
        (match, language.crossing_direction(match.term))
        for match in language.crossing_claims(sentence.text)
    ]
    contradicting = [match for match, asserts in stated if asserts is not crossed]
    if contradicting:
        match = contradicting[0]
        return [finding(
            "derived_fact_orientation_reversed",
            sentence_index=sentence.index,
            char_start=match.start, char_end=match.end,
            fact_ids=(derived.fact_id,),
            expected=(f"a phrase agreeing with {derived.display_semantics.value!r} "
                      f"({derived.from_period} -> {derived.to_period}: "
                      f"{derived.from_value!r} -> {derived.to_value!r})"),
            observed=f"the sentence says {match.term!r}",
            explanation=(
                "§4.1: `crossed_zero` is strictly across — a step that lands on zero has not "
                "crossed it — and the answer is a word, not a number, so no numeral rule "
                "reaches the sentence stating it. A phrase asserting the opposite sign change "
                "is the whole claim reversed with every declared field still valid."),
        )]
    if stated:
        return _polarity_findings(sentence, derived, [match for match, _ in stated], index)
    return [finding(
        "derived_direction_not_stated_in_text",
        sentence_index=sentence.index,
        fact_ids=(derived.fact_id,),
        expected=(f"a phrase stating {derived.display_semantics.value!r} for "
                  f"{derived.from_period} -> {derived.to_period}"),
        observed=f"the sentence states no sign-crossing phrase — {sentence.text!r}",
        explanation=(
            "§4.1 and §6: a `crossed_zero` derivation answers in `result_word` and has no "
            "numeral, so the only thing a verifier can hold a sentence to is the phrase that "
            "states the crossing. A sentence binding the fact and stating none has bound a word "
            "code nothing in its own text expresses, and the reader is left with whatever the "
            "rest of the sentence implies."),
    )]


def _has_sign(value: float, positive: bool) -> bool:
    """Strictly greater than zero is positive, strictly less is negative, and **zero is neither**.

    `recompute`'s own reading of `crossed_zero` — *"strictly across, so a step that lands on zero
    has not crossed it"* — applied to the other axis. It is the fail-closed answer: a reading of
    exactly `0` makes *"remained positive"* and *"remained negative"* both false rather than both
    arguable.
    """
    return value > 0 if positive else value < 0


def _polarity_findings(
    sentence: DraftSentence,
    derived: DerivedFact,
    matches: Sequence[language.LexicalMatch],
    index: PackageIndex,
) -> list[VerificationFinding]:
    """The second axis of a crossing claim: which **side of zero** the prose puts each reading on.

    Read against the package's two rows, never against `derived.from_value` and
    `derived.to_value` — see `orientation_findings` for why those two fields are not evidence of
    their own sign.

    **A phrase `CROSSING_POLARITY` does not carry is refused, not skipped.** The map is total
    against `language.CROSSING_TERMS` by test, so a miss is a source edit that added a scanned
    phrase without deciding what it asserts, and the honest answer to *"I cannot read this
    claim"* is the same as to *"this claim is false"*: refuse. `crossing_direction`'s `None`
    already reaches `_crossing_findings`' first refusal by the same route, and H1's whole finding
    was that the alternative — abstain on what the lexicon does not know — is not a rule.

    **Unreachable when the package does not carry an input, and deliberately silent there.**
    `integrity_findings` has already refused that fact under `derivation_not_offered`, which is
    the same fault, and §6's ordering rule is that a refusal never rests on an answer an earlier
    refusal invalidated.
    """
    from_fact = index.fact(derived.from_fact_id)
    to_fact = index.fact(derived.to_fact_id)
    if from_fact is None or to_fact is None:
        return []

    readings = ((derived.from_period, from_fact), (derived.to_period, to_fact))
    for match in matches:
        claim = CROSSING_POLARITY.get(" ".join(match.term.lower().split()))
        if claim is None:
            return [_polarity_finding(
                sentence, derived, match,
                expected="a phrase `CROSSING_POLARITY` states a side of zero for",
                observed=f"{match.term!r} is scanned by `CROSSING_TERMS` and absent from "
                         f"`CROSSING_POLARITY`, so what it asserts about the sign is unread")]
        for wants, (period, fact) in zip(claim, readings):
            if wants is None or _has_sign(fact.value, wants):
                continue
            return [_polarity_finding(
                sentence, derived, match,
                expected=f"{period} {'positive' if wants else 'negative'}, which is what "
                         f"{match.term!r} asserts",
                observed=f"{period} reads {fact.value!r} ({fact.observation_id})")]
    return []


def _polarity_finding(
    sentence: DraftSentence,
    derived: DerivedFact,
    match: language.LexicalMatch,
    *,
    expected: str,
    observed: str,
) -> VerificationFinding:
    """One code for both halves, because a sign claim the verifier cannot confirm is refused
    whether it is false or unreadable, and the panel's repair is the same sentence either way."""
    return finding(
        "derived_fact_polarity_contradicted",
        sentence_index=sentence.index,
        char_start=match.start, char_end=match.end,
        fact_ids=(derived.fact_id,),
        expected=expected,
        observed=observed,
        explanation=(
            "§4.1 and §6: `crossed_zero` answers *whether* the quantity changed sign and never "
            "*which* sign it holds, so a phrase naming a side of zero asserts something beyond "
            "the operation's answer. The fact carries no numeral, so §13.1 reaches nothing in "
            "the sentence stating it, and the crossing axis agreeing is not the sign agreeing: "
            "over readings of -556,000,000 -> -110,000,000 the computed word is "
            "`did_not_cross` and *\"remained positive\"* agrees with it while stating the "
            "opposite sign. Checked against the package's own two rows."),
    )


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
    3. **No metric surface and no period surface on the binding** (H1). `FactBinding` requires
       both fields, and for an evidence-scope fact **nothing resolves either**: `_check_periods`,
       `_check_units` and `_check_metric_identity` all reach `index.fact()` and
       `index.derived_fact()`, get `None` from both, and `continue`. The review found the free
       `period_surface` was not merely unchecked but *load-bearing* — `_covering_spans` consumed
       it from every binding unconditionally, so a model-written string licensed §13.1 coverage
       for whatever numerals it happened to contain, and
       *"…no explanation for the 92 percent collapse to 7 from 9999."* was **accepted with zero
       findings** carrying three fabricated numerals. `_covering_spans` no longer reads a surface
       no check resolves; this refuses the declaration as well, because a field two of §13's
       checks would resolve for any other fact and none resolves for this one is a declaration
       the reader has no way to test. The fact is of no metric and over no period, and the honest
       spelling of both fields is empty.
    The fourth refusal — *"no citation on the sentence at all"* — is `citations.py`'s, because
    that is where a sentence's citations are walked and where the finding's denominator lives.
    `EvidenceScopeFact` carries no `citations` field and mints no evidence handle, so any
    `PassageCitation` beside one is a filed passage presented as the source of a claim about
    what the filings do not contain.

    **No model can reach this function today, and that is a decision rather than an oversight
    (H1).** `prompts._evidence_scope_lines` prints the statement and deliberately not the
    `fact_id`, and writer system rule 17 reads *"it is a limit on what you may write and never a
    sentence to write: obey it and do not report it"*. So §7's fact is a **constraint on the
    prompt**, and the binding path below is exercised only by a hand-authored or replayed draft.
    Plan §7's claim that this makes `counter_evidence_cited_as_support` *"stop being the only
    thing standing between the draft and that claim"* is therefore not true of the tree, and the
    plan says so now.

    **Why the machinery is still worth carrying, and why the alternative was rejected.** Making
    the fact bindable means printing an id, adding a writer rule that permits the sentence, and
    reversing rule 17 — a change to what the product is willing to publish, not a repair — and
    it re-records both fixtures live and moves `WRITER_PROMPT_VERSION` for it. Against that: the
    verifier is authoritative independently of the writer (`codes.py`'s own argument for
    `operation_not_recomputable`, which is likewise unreachable from `WRITER_OPERATIONS`), a
    draft can arrive from a replayed artifact or a future prompt, and a rule written only once a
    prompt invites the failure is a rule written after the incident. What H1 refused to leave is
    the *asymmetry* the review named — unreachable machinery guarding a reachable hole. The
    hole was `period_surface` licensing §13.1 coverage from a binding no check resolves; it is
    closed at both ends, by refusal 3 here and by
    `DeterministicVerifier._period_surface_is_checked` there.
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

    declared = [f"{name}={value!r}" for name, value in
                (("metric_surface", binding.metric_surface),
                 ("period_surface", binding.period_surface)) if value]
    if declared:
        found.append(finding(
            "evidence_scope_binding_declares_a_surface",
            sentence_index=sentence.index,
            char_start=binding.char_start, char_end=binding.char_end,
            fact_ids=(scope.fact_id,),
            expected="metric_surface and period_surface both empty on an evidence-scope binding",
            observed=", ".join(declared),
            explanation=(
                "§7: the fact states what this *package's evidence* does not contain. It is of "
                "no metric and over no period, so §13.4's grammar and §13.5's alias index have "
                "nothing to resolve either surface against and both checks skip the binding. A "
                "declared surface no check resolves is a claim with no reader — and the period "
                "one was worse than idle: `_covering_spans` licensed §13.1 numeral coverage from "
                "it, so a model-written string admitted the numerals inside itself."),
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
    "CROSSING_POLARITY",
    "CROSSING_SEMANTICS",
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
