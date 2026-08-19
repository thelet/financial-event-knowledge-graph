"""The derivation stage's own vocabulary: refusals, the validated intermediate, the version.

Responsibility: everything about *deciding* a derivation, and nothing that another stage reads.
The four types §5 puts on the wire — `DerivationRequest`, `DerivedFact`, `EvidenceScopeFact` and
the operation enum — are in `story/core/models.py` instead, and the reason is structural:
`test_story_package_structure.py::test_no_stage_imports_another_stage` is symmetric, so the
planner (which emits requests), the writer (which prints derived facts) and the verifier (which
checks them) may not import this package any more than it may import theirs. `core/` is the one
surface all four share. §4 of DETERMINISTIC_FACT_TOOLS lists all of them under `public.py`; that
listing predates the import rule being applied to it, and this is the correction.

**No stage Protocol is declared here**, which departs from the repository's usual "request,
result and a stage protocol" shape. A protocol earns its place when a composition root selects
between implementations; there is one derivation implementation, it takes no provider and opens
no connection, and a `Protocol` over two module-level functions would be a type nothing could
ever be a second instance of. What *is* structural here is `DirectionOracle` — the one place
this stage depends on something it may not import — and that is a `Protocol` for exactly the
reason the others are not.

**Why `DirectionOracle` exists at all, stated plainly because it is a correction to the plan.**
§4.1 says every operation delegates to `detector_config.quantity_direction`, and
`detector_config` lives in `story/stages/detection/`. A stage may not import a sibling stage, so
the delegation cannot be an import. The three ways out were: restate `VALUE_SIGN`'s 26 rows here
(the `metric_history.py` precedent, but that restates one integer and this would restate the
table that decides whether *"costs rose"* reads as a fall — two copies of that is the defect
`detector_config`'s own docstring is about); move the table into `core/` (an edit to a module
this packet does not own); or take the answer as an argument. The argument wins: the composition
root passes `detector_config.quantity_direction` unchanged, `tests/story/test_story_derivation.py`
drives the **real** function through this seam rather than a stand-in, and no second sign
convention exists to drift from the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from story.core.models import (
    DerivationOperation,
    DerivationRequest,
    DerivedFact,
    EvidenceScopeFact,
    PackagedFact,
)
from story.core.series import CanonicalPoint, ClaimKind

#: What computed this run's derived facts, and a digest input to every `fact_id` they carry.
#: Bumped when a result would change for unchanged inputs — the same rule `detector_version`
#: follows in §6.11, and for the same reason: a redefinition must mint a new fact rather than
#: silently re-mean an existing one that a draft has already bound.
TOOL_VERSION = "1.0.0"

#: The three words `DirectionOracle` may answer with. Restated from
#: `story.stages.detection.detector_config.DIRECTION_INCREASE` and its two neighbours, because a
#: stage may not import a sibling stage; `test_story_derivation.py` asserts the six characters
#: have not drifted, which is the same trade `metric_history.MIN_DELTA_POPULATION` makes.
DIRECTION_INCREASE = "increase"
DIRECTION_DECREASE = "decrease"
DIRECTION_UNCHANGED = "unchanged"

#: What a derived fact carries when the metric's sign convention was never measured. Restated
#: from `detector_config.SIGN_CONVENTION_UNVERIFIED` and pinned by the same test — §10.1 renders
#: this code, and a code invented inline is a code no renderer knows about.
SIGN_CONVENTION_UNVERIFIED = "metric_sign_convention_unverified"

#: §7's one evidence-scope claim, and the sentence it is allowed to make.
#:
#: **The statement is about the evidence and never about the world.** *"…supplies no
#: explanation"* is checkable from the package; *"there was no cause"* is not checkable from
#: anything, and it is the sentence that today reuses a financial-table citation as though the
#: table had said it.
NO_SUPPORTED_CAUSAL_EXPLANATION = "no_supported_causal_explanation_in_package"
NO_SUPPORTED_CAUSAL_EXPLANATION_STATEMENT = (
    "The evidence in this package supplies no explanation for what is described here: it "
    "carries no explanatory passage, its causal language is forbidden, and none of its facts "
    "is marked as explaining another. State what the figures are; do not state why they moved, "
    "and do not imply a reason by juxtaposition.")


class DirectionOracle(Protocol):
    """Which way the underlying quantity moved, given the metric and the delta.

    `story.stages.detection.detector_config.quantity_direction` satisfies this exactly as
    written. `None` is a real answer and means the metric's sign convention was never measured —
    see that function's own docstring, which refuses to guess for `ValueSign.UNVERIFIED` rather
    than defaulting into the common case.
    """

    def __call__(self, metric_id: str, delta: float) -> str | None:
        ...  # pragma: no cover - structural declaration


class DerivationRefusalCode(str, Enum):
    """Why a derivation was refused, as a closed vocabulary.

    Every member is a distinct **check**, not a distinct wording: §4.2 requires that a refusal
    name which clause failed and that nothing fall through to a computed value, and a taxonomy
    where two checks shared a code could not answer the first half.

    Five members are spelled the way §6 spells the verifier's new `GATE` codes —
    `derivation_not_offered`, `derived_operation_not_supported`, `derived_inputs_incomparable`,
    `derived_unit_mismatch`, `derived_result_mismatch` — so a refusal here and the finding a
    later packet raises for the same fault are one string and not two.
    """

    #: The planner named a triple `offers()` did not put in front of it (§4.3).
    NOT_OFFERED = "derivation_not_offered"
    #: An operation outside §4.1's seven. Unreachable through the enum and reachable through a
    #: raw JSON replay, which is exactly where a closed vocabulary has to be checked twice.
    OPERATION_NOT_SUPPORTED = "derived_operation_not_supported"
    #: A fact id that resolves nowhere in this package — a free-text value, or an id from
    #: another run.
    INPUT_NOT_IN_PACKAGE = "derivation_input_not_in_package"
    #: Both ids are the same fact. Every operation compares two readings and there is one here.
    INPUTS_IDENTICAL = "derivation_inputs_identical"
    #: R1–R10 refused the pair. `rule_id` carries which one.
    INPUTS_INCOMPARABLE = "derived_inputs_incomparable"
    #: The unit does not admit this operation (§4.1's table) — the percent-versus-
    #: percentage-point confusion, and the three other unit gates beside it.
    UNIT_NOT_ADMITTED = "derived_unit_mismatch"
    #: A two-period operation given one period, or a same-period operation given two.
    PERIOD_ALIGNMENT = "derivation_period_alignment"
    #: §13.3's third gate: `(v2 − v1)/|v1|` across zero is defined and rhetorically meaningless.
    RELATIVE_CHANGE_ACROSS_ZERO = "derived_relative_change_across_zero"
    #: `ratio` with a zero denominator.
    DENOMINATOR_ZERO = "derivation_denominator_zero"
    #: The candidate's own signal holds this quantity and disagrees with the computed one.
    #: A refusal and not a preference — two code paths computing one number and differing is a
    #: defect in one of them, and picking a winner would hide it.
    DETECTOR_SIGNAL_DISAGREES = "derived_result_mismatch"


@dataclass(frozen=True, slots=True)
class DerivationRefusal:
    """One derivation that was refused, with the check that refused it and both operands named.

    A **value and not an exception**, following `series.Refuse` for its stated reason: the
    planner asking for a derivation code will not perform is an ordinary editorial state, and an
    exception makes it indistinguishable from a broken run. `detail` names both sides because
    *"incomparable"* with no operands is not a finding a writer can act on.
    """

    code: DerivationRefusalCode
    operation: str
    from_fact_id: str
    to_fact_id: str
    detail: str
    #: The §6.9 rule that refused, for `INPUTS_INCOMPARABLE`. Empty for every other code.
    rule_id: str = ""

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ValidatedDerivation:
    """A request that passed every clause of §4.2, with what the clauses produced.

    It exists so the seven operations can be *"pure functions over already-validated inputs"*
    without each of them re-deriving the comparability answer, the canonical points or the claim
    kind. Nothing constructs one except `offers.validate`, which is the only place §4.2 lives.

    `to_*` is the subject of the claim and `from_*` is the base it is stated against: `result` is
    `to − from` for every difference and `to / from` for the ratio, so a reversed request is a
    different derivation with a different id rather than the same one read backwards.

    **`comparable(left=to, right=from)`, and the order is not arbitrary.** R8's own refusal
    sentence reads *"moved from `right` to `left`"*, and `evidence_package.py` builds its
    movement pairs as `(later, earlier)` for that reason. Passing them the other way round would
    make every refusal message describe the reverse of the claim being made.
    """

    request: DerivationRequest
    from_fact: PackagedFact
    to_fact: PackagedFact
    from_point: CanonicalPoint
    to_point: CanonicalPoint
    claim: ClaimKind
    #: `series.rules_evaluated` over the `Ok` this pair produced — which rules actually ran.
    comparability_rule_ids: tuple[str, ...]
    #: R9's disclosure codes, carried onto the fact rather than dropped (§10.1's channel).
    warning_codes: tuple[str, ...] = ()

    @property
    def operation(self) -> DerivationOperation:
        return self.request.operation

    def __bool__(self) -> bool:
        return True


#: What `validate` answers with. Mirrors `series.Comparability = Ok | Refuse`: a caller branches
#: on the value, and `bool()` is defined on both halves so the branch reads the same way.
Validation = ValidatedDerivation | DerivationRefusal


@dataclass(frozen=True, slots=True)
class DerivationResult:
    """What one run of the derivation stage produced, refusals included.

    Refusals are a field and not a log line, for `MetricMoveRefusal`'s reason: a derivation the
    planner asked for and did not get, and one it never asked for, are different findings, and a
    result that reported them the same way would let a silently dropped request read as a plan
    that wanted nothing.

    `evidence_scope_facts` is separate from `facts` because §7's kind is minted **by code from
    the package alone** and is never requested — putting it in the same tuple would let a reader
    ask which planner request produced it, and the answer is that none did.
    """

    facts: tuple[DerivedFact, ...] = ()
    evidence_scope_facts: tuple[EvidenceScopeFact, ...] = ()
    refusals: tuple[DerivationRefusal, ...] = ()


__all__ = [
    "DIRECTION_DECREASE",
    "DIRECTION_INCREASE",
    "DIRECTION_UNCHANGED",
    "NO_SUPPORTED_CAUSAL_EXPLANATION",
    "NO_SUPPORTED_CAUSAL_EXPLANATION_STATEMENT",
    "SIGN_CONVENTION_UNVERIFIED",
    "TOOL_VERSION",
    "DerivationRefusal",
    "DerivationRefusalCode",
    "DerivationResult",
    "DirectionOracle",
    "Validation",
    "ValidatedDerivation",
]
