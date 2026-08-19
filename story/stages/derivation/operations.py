"""§4.1's seven operations, and the words a result may be described with.

Responsibility: turn two already-validated readings into one answer — a number or a closed
word, a unit, a currency, and the `display_semantics` a writer binds. Every clause of §4.2 has
already run by the time anything here is called (`offers.validate`), so these are total
functions over their arguments with no refusal path of their own.

**Arithmetic is not reimplemented here, and that is the defect this module exists to avoid.**
`numerals.delta_pp`, `numerals.delta_relative` and `numerals.operation_result_surfaces` already
own subtraction, relative change and what a result may be rendered in; §13.3's third gate lives
inside `delta_relative` as a raise, and this module lets it raise rather than pre-empting it
with a second copy of the test. What is genuinely new here is the *wording*: which of twelve
closed phrases describes a result, given its sign **and** the metric's stored sign convention.

**A negative delta does not mean "decreased", and the corpus is where that is measured.**
`direct_selling_costs` is stored negative on 46 of 46 canonical values and `holding_costs` on 15
of 15, because the filings print *"Direct selling costs (54,175)"* inside a subtotal — so a fall
in the stored number is a rise in the cost. `cost_of_revenue` and
`inventory_valuation_adjustment` have **no observation at all**, so their convention is
unmeasured and `DisplaySemantics.DIRECTION_UNVERIFIABLE` is what this module says about them,
beside a `metric_sign_convention_unverified` warning. Guessing the common case would put an
invented direction on a post.

**Two operations produce no number, and neither is given one.** `crossed_zero` answers a boolean
and `trend_direction` a direction word; both come back in `result_word` with `result=None`,
because a boolean rendered as `1.0` is a numeral §13.1 would then compare against the prose.

**`round_delta` is restated as one line over `core`'s own `DELTA_PRECISION`, not imported.**
`story.stages.detection.detector_config.round_delta` is that same line over that same constant,
and a stage may not import a sibling stage. The constant — not the rounding — is the thing that
must not drift, and it has exactly one home in `core/series.py`; `test_story_derivation.py`
asserts the two functions agree on the values this corpus produces.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from story.core.models import DerivationOperation, DisplaySemantics
from story.core.numerals import (
    SurfaceUnit,
    delta_pp,
    delta_relative,
    operation_result_surfaces,
)
from story.core.periods import PeriodShape, StoryPeriod
from story.core.series import DELTA_PRECISION
from story.stages.derivation.public import (
    DIRECTION_DECREASE,
    DIRECTION_INCREASE,
    DIRECTION_UNCHANGED,
    SIGN_CONVENTION_UNVERIFIED,
    DirectionOracle,
    ValidatedDerivation,
)

#: The four `Observation.unit` values this corpus holds. §4.2's unit clause is written against
#: this tuple rather than against "whatever arrived", so a unit the extraction has never
#: produced is refused at validation rather than carried into a result nobody can render.
CORPUS_UNITS: tuple[str, ...] = ("USD", "percent", "homes", "markets")

PERCENT_UNIT = "percent"

#: The unit each non-unit-preserving operation stamps on its result. `percentage_points` and
#: `multiple` are §13.3's quantities that no observation may hold and a derived fact may;
#: `boolean` and `direction` are outside `SurfaceUnit` entirely, because no numeral binds them.
UNIT_PERCENT = "percent"
UNIT_PERCENTAGE_POINTS = "percentage_points"
UNIT_MULTIPLE = "multiple"
UNIT_BOOLEAN = "boolean"
UNIT_DIRECTION = "direction"

#: What `crossed_zero` puts in `result_word`. Words rather than `"True"`/`"False"`, so a reader
#: of `derived_facts.json` meets the claim and not a Python repr.
CROSSED = "crossed"
DID_NOT_CROSS = "did_not_cross"

#: What `trend_direction` puts in `result_word` when the oracle refused to answer. A named word
#: rather than an empty string, because `DerivedFact` requires exactly one of `result` and
#: `result_word` — an empty one would make the row unconstructible, and the honest answer to
#: *"which way did it move"* on an unmeasured sign convention is *"nobody has established that"*
#: rather than nothing at all. `display_semantics` says the same in the writer's vocabulary and
#: `metric_sign_convention_unverified` says it in §10.1's.
DIRECTION_UNVERIFIED = "unverified"

#: `numerals.operation_result_surfaces`'s name for the two unit-preserving operations here.
#: `difference` is its vocabulary and `absolute_change` is §4.1's; they are the same quantity,
#: and this is the one place the two namings meet.
_RESULT_SURFACE_OPERATION: dict[DerivationOperation, str] = {
    DerivationOperation.ABSOLUTE_CHANGE: "difference",
    DerivationOperation.COMPARE_LEVELS: "compare_levels",
}


@dataclass(frozen=True, slots=True)
class OperationOutcome:
    """One operation's whole answer. Frozen, and every field stated rather than defaulted.

    `warning_codes` is a field because §10.1's disclosure channel is how an unmeasured sign
    convention travels: the quantity is real and citable and only the sentence describing it is
    unavailable, so the fact is emitted with the warning rather than dropped — which is the call
    `detector_config.quantity_direction` already records for a candidate.
    """

    result: float | None
    result_word: str
    unit: str
    currency: str | None
    display_semantics: DisplaySemantics
    warning_codes: tuple[str, ...] = ()


def round_delta(value: float) -> float:
    """Every difference this stage compares or publishes, rounded once, here.

    `13.2 − 3.3` is `9.899999999999999` in IEEE 754 and `adjusted_gross_margin` holds exactly
    those two values in adjacent quarters, so a result published raw would print a residue at
    the seventeenth digit into a post.
    """
    return round(value, DELTA_PRECISION)


def absolute_change(
    inputs: ValidatedDerivation, *, direction: DirectionOracle
) -> OperationOutcome:
    """`to − from`, in the inputs' own unit (§4.1 row 1).

    Delegates to `numerals.delta_pp`, which is `later − earlier` and is defined everywhere
    including across zero. The name reads oddly for a USD figure and the function is the general
    one — §13.3 named it for the reading it was first needed for, and a second subtraction here
    would be a second authority on the only arithmetic this operation does.

    **A percent input is refused before this runs, and that is a correction to §4.1's table.**
    The table gives this operation *"input unit"*, which for two percent readings would mint a
    `percent` result — the exact confusion §13.3 calls *"the single most likely factual error
    this package can make"*. `operation_result_surfaces("difference", "percent")` already says
    the answer is `percentage_points`, and that quantity has its own operation; admitting both
    would mint two ids for one number under two units.
    """
    delta = round_delta(delta_pp(inputs.from_fact.value, inputs.to_fact.value))
    semantics, warnings = _change_semantics(inputs, delta, direction)
    return OperationOutcome(
        result=delta,
        result_word="",
        unit=_preserved_unit(inputs),
        currency=inputs.to_fact.currency,
        display_semantics=semantics,
        warning_codes=warnings,
    )


def percentage_change(
    inputs: ValidatedDerivation, *, direction: DirectionOracle
) -> OperationOutcome:
    """`(to − from)/|from| × 100`, as a percent (§4.1 row 2).

    `numerals.delta_relative` raises `RelativeChangeAcrossZero` rather than returning a number
    for `from == 0` or a sign flip. `offers.validate` has already excluded that case, so the
    raise is unreachable from a validated input; `execute.py` catches it anyway and maps it to a
    typed refusal, because a swallowed raise here would be a computed value where §4.2 promises
    a refusal.

    **The currency does not survive.** A relative change is dimensionless and printed with a
    `%`; carrying `USD` forward would let §13.2's unit check accept a dollar sign on it.
    """
    relative = round_delta(delta_relative(inputs.from_fact.value, inputs.to_fact.value))
    semantics, warnings = _change_semantics(
        inputs, round_delta(inputs.to_fact.value - inputs.from_fact.value), direction)
    return OperationOutcome(
        result=relative,
        result_word="",
        unit=UNIT_PERCENT,
        currency=None,
        display_semantics=semantics,
        warning_codes=warnings,
    )


def percentage_point_change(
    inputs: ValidatedDerivation, *, direction: DirectionOracle
) -> OperationOutcome:
    """`to − from` between two percent levels, in percentage points (§4.1 row 3).

    The same subtraction `absolute_change` does and a different **unit**, which is the whole of
    why the two are separate operations rather than one with a flag: the unit is what decides
    which is legal, so the illegal one is a refusal at request time instead of a rendering
    caught afterwards.
    """
    points = round_delta(delta_pp(inputs.from_fact.value, inputs.to_fact.value))
    semantics, warnings = _change_semantics(inputs, points, direction)
    return OperationOutcome(
        result=points,
        result_word="",
        unit=UNIT_PERCENTAGE_POINTS,
        currency=None,
        display_semantics=semantics,
        warning_codes=warnings,
    )


def compare_levels(
    inputs: ValidatedDerivation, *, direction: DirectionOracle
) -> OperationOutcome:
    """The gap between two readings of one period, in the unit `operation_result_surfaces` names
    (§4.1 row 4).

    `to − from`, so the sign points at the subject: `adjusted_gross_margin` against
    `gaap_gross_margin` in 2022Q3 is `3.3 − (−12.6) = 15.9`, which is
    `cross_metric_divergence`'s own `gap` signal to the ninth decimal place.

    **R8's defect D is why the unit is asked for rather than copied.** A gap between two percent
    *levels* is in percentage points and in nothing else; before
    `operation_result_surfaces` existed, `"15.9 basis points"` and `"15.9x"` both passed for this
    very sentence.

    **Two metrics need one sign convention or none is claimed.** *"Higher"* is a statement about
    the quantity, not about the stored number, and two metrics stored under opposite conventions
    have no shared reading of it. Where the oracle answers differently for the two — or refuses
    for either — the semantics are `DIRECTION_UNVERIFIABLE` and the warning says why.
    """
    gap = round_delta(delta_pp(inputs.from_fact.value, inputs.to_fact.value))
    word, warnings = _shared_direction(inputs, gap, direction)
    semantics = {
        DIRECTION_INCREASE: DisplaySemantics.HIGHER_THAN,
        DIRECTION_DECREASE: DisplaySemantics.LOWER_THAN,
        DIRECTION_UNCHANGED: DisplaySemantics.EQUAL_TO,
    }.get(word or "", DisplaySemantics.DIRECTION_UNVERIFIABLE)
    return OperationOutcome(
        result=gap,
        result_word="",
        unit=_preserved_unit(inputs),
        currency=inputs.to_fact.currency if inputs.to_fact.unit != PERCENT_UNIT else None,
        display_semantics=semantics,
        warning_codes=warnings,
    )


def ratio(inputs: ValidatedDerivation, *, direction: DirectionOracle) -> OperationOutcome:
    """`to / from`, dimensionless (§4.1 row 5).

    `direction` is accepted and unused, so the seven operations share one signature and
    `execute` can dispatch through a table rather than through a branch per operation. A ratio
    has no direction to word: `2.5x` is neither a rise nor a fall, and
    `operation_result_surfaces("ratio", …)` already says the only surfaces it may take are
    `multiple` and a bare numeral — *"a unit on a ratio is a unit that survived a division that
    should have cancelled it."*

    The denominator is `from_value` and it is non-zero by §4.2; no observation in this corpus is
    zero *(verified 2026-08-04: 0 of 2,704)*, so the clause guards a value that cannot arrive
    rather than one that has.
    """
    del direction
    return OperationOutcome(
        result=round_delta(inputs.to_fact.value / inputs.from_fact.value),
        result_word="",
        unit=UNIT_MULTIPLE,
        currency=None,
        display_semantics=DisplaySemantics.TIMES,
        warning_codes=(),
    )


def crossed_zero(inputs: ValidatedDerivation, *, direction: DirectionOracle) -> OperationOutcome:
    """Did the two readings sit on opposite sides of zero (§4.1 row 6).

    **Strictly across, so a step that lands *on* zero has not crossed it**, and this is the one
    predicate in the module that delegates to nothing. `numerals.relative_change_across_zero`
    answers the *wider* question — it is `True` for `from == 0` as well, because a quotient with
    a zero denominator is undefined rather than meaningless — and answering the narrower
    question with it would report `0 → 5` as a sign reversal. `adjusted_ebitda 2021Q3 → 2021Q4`
    is `+$34.5M → +$0.4M`, which approaches zero and does not reach it, and `2022Q2 → 2022Q3` is
    `+$218M → −$211M`, which does; the whole value of the flag is telling those two apart.

    `direction` is unused, for `ratio`'s reason.
    """
    del direction
    earlier, later = inputs.from_fact.value, inputs.to_fact.value
    crossed = (earlier > 0 > later) or (earlier < 0 < later)
    return OperationOutcome(
        result=None,
        result_word=CROSSED if crossed else DID_NOT_CROSS,
        unit=UNIT_BOOLEAN,
        currency=None,
        display_semantics=(DisplaySemantics.CROSSED_ZERO if crossed
                           else DisplaySemantics.DID_NOT_CROSS_ZERO),
        warning_codes=(),
    )


def trend_direction(
    inputs: ValidatedDerivation, *, direction: DirectionOracle
) -> OperationOutcome:
    """Which way the underlying quantity moved, as a word (§4.1 row 7).

    The whole operation is the oracle call: the arithmetic sign is *not* the answer, which is
    why `detector_config.quantity_direction` takes a metric id and returns a word rather than a
    number, and why `DIRECTION_INCREASE` is `"increase"` and not `+1`.

    Where the convention is unmeasured there is **no result word to give**, and the fact is
    still minted: `result_word` carries `unchanged` only when the oracle said so. An unverifiable
    direction leaves `DIRECTION_UNVERIFIABLE` in the semantics and the raw word absent — see the
    `result_word` fallback below, which is the one place this module has to choose between an
    empty field and a wrong one, and chooses `DIRECTION_UNVERIFIED` so `DerivedFact`'s
    exactly-one-of validator still holds.
    """
    delta = round_delta(inputs.to_fact.value - inputs.from_fact.value)
    word = direction(inputs.to_fact.metric_id, delta)
    semantics = {
        DIRECTION_INCREASE: DisplaySemantics.INCREASED,
        DIRECTION_DECREASE: DisplaySemantics.DECREASED,
        DIRECTION_UNCHANGED: DisplaySemantics.UNCHANGED,
    }.get(word or "", DisplaySemantics.DIRECTION_UNVERIFIABLE)
    return OperationOutcome(
        result=None,
        result_word=word or DIRECTION_UNVERIFIED,
        unit=UNIT_DIRECTION,
        currency=None,
        display_semantics=semantics,
        warning_codes=() if word is not None else (SIGN_CONVENTION_UNVERIFIED,),
    )


#: One operation's signature. Every one of the seven takes the validated inputs and the oracle
#: and nothing else, so `execute` can dispatch through a table instead of a branch per member —
#: which is also why `ratio` and `crossed_zero` accept a `direction` they do not use.
Operation = Callable[..., OperationOutcome]

#: §4.1's table as a dispatch, so `execute` names no operation twice. A `dict` and not a chain
#: of `if`s for `series.RULE_ORDER`'s reason: the operations are enumerated in the plan and a
#: reader checking `ratio` should find one function called `ratio`.
OPERATIONS: dict[DerivationOperation, Operation] = {
    DerivationOperation.ABSOLUTE_CHANGE: absolute_change,
    DerivationOperation.PERCENTAGE_CHANGE: percentage_change,
    DerivationOperation.PERCENTAGE_POINT_CHANGE: percentage_point_change,
    DerivationOperation.COMPARE_LEVELS: compare_levels,
    DerivationOperation.RATIO: ratio,
    DerivationOperation.CROSSED_ZERO: crossed_zero,
    DerivationOperation.TREND_DIRECTION: trend_direction,
}


def _preserved_unit(inputs: ValidatedDerivation) -> str:
    """What a unit-preserving operation's result is denominated in, asked of `numerals`.

    `operation_result_surfaces` returns a *set* of admissible surfaces, and two of the four
    corpus units admit a bare numeral beside their noun (`homes`, `markets`). A derived fact
    states one unit, so the bare-numeral member is dropped and the named one kept —
    `SurfaceUnit.NONE` means *"a numeral with no suffix"*, which is a rendering permission
    and not a unit.
    """
    surfaces = operation_result_surfaces(
        _RESULT_SURFACE_OPERATION[inputs.operation], inputs.to_fact.unit)
    named = sorted(surface.value for surface in surfaces if surface is not SurfaceUnit.NONE)
    return named[0] if named else ""


def _change_semantics(
    inputs: ValidatedDerivation, delta: float, direction: DirectionOracle
) -> tuple[DisplaySemantics, tuple[str, ...]]:
    """*"increased by"* / *"decreased by"* / *"unchanged"*, from the metric's own convention."""
    word = direction(inputs.to_fact.metric_id, delta)
    semantics = {
        DIRECTION_INCREASE: DisplaySemantics.INCREASED_BY,
        DIRECTION_DECREASE: DisplaySemantics.DECREASED_BY,
        DIRECTION_UNCHANGED: DisplaySemantics.UNCHANGED,
    }.get(word or "", DisplaySemantics.DIRECTION_UNVERIFIABLE)
    return semantics, () if word is not None else (SIGN_CONVENTION_UNVERIFIED,)


def _shared_direction(
    inputs: ValidatedDerivation, delta: float, direction: DirectionOracle
) -> tuple[str | None, tuple[str, ...]]:
    """One direction word both metrics agree on, or `None` with the reason recorded.

    Called once per metric on the **same** delta rather than once on a combined one, because the
    oracle's whole contract is that the answer depends on which metric's convention is in force.
    Two metrics under opposite conventions produce two different words for one gap, and that is
    the state this returns `None` for.
    """
    words = {direction(metric_id, delta)
             for metric_id in (inputs.from_fact.metric_id, inputs.to_fact.metric_id)}
    if len(words) == 1 and None not in words:
        return words.pop(), ()
    return None, (SIGN_CONVENTION_UNVERIFIED,)


def period_surface_hint(period: StoryPeriod) -> str:
    """The surface a `FactBinding` should carry for this period, minted by code (§2).

    **This is the repair, not a convenience.** §2 measured that the demo's refusal was never the
    arithmetic: `$446 million` recomputed cleanly and `unbound_numeral` fired on the literal
    `2022`, because the model left `Calculation.period_surface` empty while its own text read
    *"in the third quarter of 2022"*. A derived fact binds through an ordinary `FactBinding`,
    whose `period_surface` is per binding, and this is where code fills it.

    Every form here is one `story/stages/verification/period_grammar.py` resolves back to this
    period — asserted by test rather than claimed, because a hint the verifier cannot parse
    would be worse than none: it would look like a period surface and refuse as an unresolvable
    one.

    **An instant renders as its ISO date rather than as *"September 30, 2022"***, even though the
    grammar accepts both. The worded form needs a month-name table, and a second copy of one
    beside `period_grammar._MONTHS` is a table that can drift; the ISO form needs nothing and
    round-trips through the same grammar.

    `PeriodShape.OTHER` gets no hint. R3 refuses it before a derived fact can exist, so this
    returns the empty string for a period that cannot arrive rather than inventing a phrase for
    a window nobody can name.
    """
    if period.shape is PeriodShape.INSTANT and period.instant_date:
        return period.instant_date
    if period.shape is PeriodShape.QUARTER and period.period_end:
        quarter = (int(period.period_end[5:7]) - 1) // 3 + 1
        return f"the {_QUARTER_ORDINAL[quarter]} quarter of {period.period_end[:4]}"
    if period.shape is PeriodShape.FISCAL_YEAR and period.period_end:
        return f"fiscal year {period.period_end[:4]}"
    if period.shape is PeriodShape.YTD_6M and period.period_end:
        return f"the six months ended June 30, {period.period_end[:4]}"
    if period.shape is PeriodShape.YTD_9M and period.period_end:
        return f"the nine months ended September 30, {period.period_end[:4]}"
    return ""


#: The ordinal words `period_grammar._ORDINAL_QUARTER` reads. Written out rather than indexed
#: into a list for that map's own stated reason: the failure mode of an index is silent
#: (`"forth"` → 4) and the failure mode of a missing key is a refusal.
_QUARTER_ORDINAL: dict[int, str] = {1: "first", 2: "second", 3: "third", 4: "fourth"}


__all__ = [
    "CORPUS_UNITS",
    "CROSSED",
    "DID_NOT_CROSS",
    "DIRECTION_UNVERIFIED",
    "OPERATIONS",
    "PERCENT_UNIT",
    "UNIT_BOOLEAN",
    "UNIT_DIRECTION",
    "UNIT_MULTIPLE",
    "UNIT_PERCENT",
    "UNIT_PERCENTAGE_POINTS",
    "OperationOutcome",
    "absolute_change",
    "compare_levels",
    "crossed_zero",
    "percentage_change",
    "percentage_point_change",
    "period_surface_hint",
    "ratio",
    "round_delta",
    "trend_direction",
]
