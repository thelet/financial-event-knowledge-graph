"""§4.2's validation, and §4.3's offer set built out of it.

Responsibility: the single answer to *"would this derivation be permitted?"*, and the
enumeration of every triple for which the answer is yes. Both live here so they cannot disagree:
§4.3 requires that a request outside the offer set be refused as `derivation_not_offered` rather
than attempted, and that promise is only worth anything while *"offered"* and *"valid"* are one
predicate rather than two lists maintained beside each other.

`offers(package, candidate)` is a pure function of the package and the candidate, with **no
model input**, which is what lets it be printed into the planner prompt and replayed byte for
byte. It reads its cap from `package.budget.parameters.max_derivations`, so the size of the list
the planner is shown is inside `package_id` and `story_run_id` (§4.3).

**§4.2's clause order is not the order the plan lists them in, and the reason is that one clause
decides another's argument.** The plan puts `series.comparable` second and the unit and period
clauses after it. But which `ClaimKind` to ask `comparable` for depends on whether the operation
takes one period or two and on whether it names one metric or two — R8's tolerance floor and
R10's adjacency apply to a step claim and not to a level one, and R2 admits two metrics only
under `DIVERGENCE`. So the shape clauses run first and `comparable` runs with the claim they
decided. Every clause the plan names still runs, and every one of them refuses.

**R1 cannot fire here, and saying so is better than implying it can.** A
`StoryEvidencePackage` carries one `subject`, and `PackagedFact` carries no subject of its own,
so both canonical points are built with the same `subject_entity_id` and R1's subject check is a
comparison of a value with itself. It is still evaluated rather than skipped —
`comparability_rule_ids` is a statement about which rules *ran*, and a list that omitted R1
because it could not fail would be a different claim. The clause §4.2 words as *"subject
agrees"* is discharged by the package's shape, not by this module.

**R10's adjacency is answered over the package's own facts, and that is weaker than it sounds
in one direction only.** The canonical series this stage can build is `package.facts` for one
metric — a subset of the corpus series, bounded by `max_facts` — so `points_between` can only
ever *under*-refuse. The half that does the work is the other one: `_r10_adjacency` also requires
the calendar gap to equal `SHAPE_STEP_MONTHS`, and that arm is independent of what the package
carries. `pct_homes_on_market_gt_120_days` between `2021-12-31` (8%) and `2022-12-31` (55%) — the
case R10 exists for, adjacent rows across a four-quarter hole and a **+47 pp** "quarterly jump"
that never happened — is refused by the gap arm whether or not the package holds the quarters
between. And for a shape whose step is its own spacing, a gap of exactly one step leaves no room
for a same-shape point to hide in. The exception is `INSTANT`, whose 3-month step is a
measurement over 24 of 25 distinct instants rather than a structural fact; an instant pair is
therefore the one place a package-local series is genuinely weaker, and it is recorded here
rather than left to be discovered.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from story.core.models import (
    DerivationOperation,
    DerivationRequest,
    PackagedFact,
    StoryCandidate,
    StoryEvidencePackage,
)
from story.core.numerals import relative_change_across_zero
from story.core.periods import story_period
from story.core.series import (
    CanonicalPoint,
    CanonicalSeries,
    CanonicalStatus,
    ClaimKind,
    ComparabilityAuthority,
    Refuse,
    comparable,
    rules_evaluated,
)
from story.stages.derivation.operations import CORPUS_UNITS, OPERATIONS, PERCENT_UNIT
from story.stages.derivation.public import (
    DerivationRefusal,
    DerivationRefusalCode,
    ValidatedDerivation,
    Validation,
)

#: The operations that assert a **step** between two periods. They take `ClaimKind.MOVEMENT`,
#: which is what brings R8's presentation-tolerance floor and R10's adjacency to bear: a move
#: smaller than the coarser filing's printed unit is rounding rather than news, and a step across
#: a hole in the series is a claim about the hole.
#:
#: A consequence worth stating because it is easy to mistake for a bug: R8 refuses a `MOVEMENT`
#: whose delta is *within* tolerance, so `absolute_change` and `trend_direction` can never mint a
#: fact whose `display_semantics` is `unchanged`. That is correct — two filings that agree to the
#: printed unit have not shown a quantity holding steady, they have shown one number twice — and
#: it means the `unchanged` member is reachable only through `compare_levels`.
TWO_PERIOD_OPERATIONS: frozenset[DerivationOperation] = frozenset({
    DerivationOperation.ABSOLUTE_CHANGE,
    DerivationOperation.PERCENTAGE_CHANGE,
    DerivationOperation.PERCENTAGE_POINT_CHANGE,
    DerivationOperation.CROSSED_ZERO,
    DerivationOperation.TREND_DIRECTION,
})

#: The operations that compare two readings **of one period**. §4.1 constrains `compare_levels`
#: to one period and leaves `ratio` unstated; it is read the same way here, and the reason is
#: what a cross-period ratio would be. `revenue` this quarter over `revenue` two years ago is a
#: multiple of its former self — a quantity `absolute_change` and `percentage_change` already
#: express, and express with a unit and a direction word this one cannot. Confined to a single
#: period, `ratio` is the operation that divides one metric by another and cancels the unit,
#: which is the only reading `operation_result_surfaces("ratio", …)` describes.
SAME_PERIOD_OPERATIONS: frozenset[DerivationOperation] = frozenset({
    DerivationOperation.COMPARE_LEVELS,
    DerivationOperation.RATIO,
})

#: Which units each operation admits, as §4.1's table. `None` means *"any of the corpus's four"*.
#:
#: **`absolute_change` excludes `percent`, which corrects §4.1's own "input unit" column.** The
#: difference between two percent levels is a number of percentage points and is
#: `percentage_point_change`; admitting it here as well would mint two ids for one quantity,
#: under two units, one of which is the confusion §13.3 calls the single most likely factual
#: error this package can make. `percentage_change` excludes it for the reason §4.1 does state:
#: a relative change of a percentage is the ambiguous reading that gate exists to refuse.
ADMITTED_UNITS: Mapping[DerivationOperation, frozenset[str] | None] = {
    DerivationOperation.ABSOLUTE_CHANGE: frozenset(set(CORPUS_UNITS) - {PERCENT_UNIT}),
    DerivationOperation.PERCENTAGE_CHANGE: frozenset(set(CORPUS_UNITS) - {PERCENT_UNIT}),
    DerivationOperation.PERCENTAGE_POINT_CHANGE: frozenset({PERCENT_UNIT}),
    DerivationOperation.COMPARE_LEVELS: None,
    DerivationOperation.RATIO: None,
    DerivationOperation.CROSSED_ZERO: None,
    DerivationOperation.TREND_DIRECTION: None,
}


def offers(
    package: StoryEvidencePackage,
    candidate: StoryCandidate,
    *,
    authority: ComparabilityAuthority | None = None,
) -> tuple[DerivationRequest, ...]:
    """Every `(operation, from_fact_id, to_fact_id)` triple this package and candidate support.

    Ordered by `(operation as §4.1 lists it, from_fact_id, to_fact_id)` and capped at
    `max_derivations`. The order is part of the contract, not a convenience: the list is printed
    into the planner prompt, `request_identity` digests that prompt, and a set iterated in hash
    order would re-key the replay store on a run that changed nothing.

    **Both orientations of a pair are offered.** `2022Q2 → 2022Q3` and `2022Q3 → 2022Q2` are
    different derivations with different results, different `display_semantics` and different
    ids; a planner that may only request one of them has had an editorial decision made for it
    by an enumeration order. §6's `derived_fact_orientation_reversed` is a check on the *draft* —
    whether the prose runs the way the fact does — and not a reason to withhold the fact.

    An offer is *the request that would be granted*, so the type is `DerivationRequest` and not a
    parallel one. §4.3's *"the planner may request only a triple from that list"* is then a
    membership test on one type rather than an agreement between two.
    """
    found: list[DerivationRequest] = []
    for operation in DerivationOperation:
        for from_fact in sorted(package.facts, key=lambda fact: fact.observation_id):
            for to_fact in sorted(package.facts, key=lambda fact: fact.observation_id):
                request = DerivationRequest(
                    operation=operation,
                    from_fact_id=from_fact.observation_id,
                    to_fact_id=to_fact.observation_id,
                )
                if validate(request, package, candidate, authority=authority):
                    found.append(request)
    return tuple(found[: max(0, package.budget.parameters.max_derivations)])


def validate(
    request: DerivationRequest,
    package: StoryEvidencePackage,
    candidate: StoryCandidate,
    *,
    authority: ComparabilityAuthority | None = None,
) -> Validation:
    """§4.2, every clause, as a value. Nothing falls through to a computed result.

    `candidate` is accepted and read by no clause today. It is in the signature because §4.3
    defines the offer set as a function of *"the package and the candidate"* and because a later
    clause — a story type that may not support an operation, say — would be a change to this
    function rather than to every call site of it. Named rather than dropped, so the contract the
    three consuming packets code against does not move when that clause arrives.
    """
    del candidate

    if request.operation not in OPERATIONS:
        # Unreachable through the enum and reachable through a replayed artifact: §14 reads every
        # stored plan back with `model_validate`, and an operation retired between two runs would
        # arrive as a string this dispatch has no entry for.
        return _refuse(request, DerivationRefusalCode.OPERATION_NOT_SUPPORTED,
                       f"{request.operation!r} is not one of the seven operations §4.1 declares")

    by_id = {fact.observation_id: fact for fact in package.facts}
    from_fact, to_fact = by_id.get(request.from_fact_id), by_id.get(request.to_fact_id)
    missing = [name for name, fact in (("from_fact_id", from_fact), ("to_fact_id", to_fact))
               if fact is None]
    if missing:
        return _refuse(
            request, DerivationRefusalCode.INPUT_NOT_IN_PACKAGE,
            f"{', '.join(missing)} names no fact in {package.package_id}; a derivation reads "
            "the package and never a free-text value or an id from another run")
    assert from_fact is not None and to_fact is not None  # narrowed by `missing`

    if from_fact.observation_id == to_fact.observation_id:
        return _refuse(
            request, DerivationRefusalCode.INPUTS_IDENTICAL,
            f"{from_fact.observation_id} was given as both operands; every operation compares "
            "two readings and this is one")

    admitted = ADMITTED_UNITS[request.operation]
    units = {from_fact.unit, to_fact.unit}
    if not units <= set(CORPUS_UNITS):
        return _refuse(
            request, DerivationRefusalCode.UNIT_NOT_ADMITTED,
            f"{sorted(units - set(CORPUS_UNITS))} is not one of the corpus's units "
            f"{list(CORPUS_UNITS)}")
    if admitted is not None and not units <= admitted:
        return _refuse(
            request, DerivationRefusalCode.UNIT_NOT_ADMITTED,
            f"{request.operation.value} is defined for {sorted(admitted)} and "
            f"{from_fact.metric_id} {from_fact.period_key} is in {from_fact.unit!r}, "
            f"{to_fact.metric_id} {to_fact.period_key} in {to_fact.unit!r}; the percent and "
            "percentage-point readings are separate operations so that the wrong one is a "
            "refusal at request time rather than a rendering caught afterwards")

    same_period = from_fact.period_key == to_fact.period_key
    if request.operation in TWO_PERIOD_OPERATIONS and same_period:
        return _refuse(
            request, DerivationRefusalCode.PERIOD_ALIGNMENT,
            f"{request.operation.value} states a step between two periods and both operands are "
            f"{from_fact.period_key}")
    if request.operation in SAME_PERIOD_OPERATIONS and not same_period:
        return _refuse(
            request, DerivationRefusalCode.PERIOD_ALIGNMENT,
            f"{request.operation.value} compares two readings of one period and these are "
            f"{from_fact.period_key} and {to_fact.period_key}")

    from_point = _point(from_fact, package.subject.entity_id)
    to_point = _point(to_fact, package.subject.entity_id)
    claim = _claim_kind(request.operation, from_fact, to_fact)
    answer = comparable(
        to_point, from_point,
        claim=claim,
        authority=authority,
        series=_series(package, to_fact.metric_id, package.subject.entity_id),
    )
    if isinstance(answer, Refuse):
        return _refuse(
            request, DerivationRefusalCode.INPUTS_INCOMPARABLE,
            f"{answer.reason}: {answer.detail}", rule_id=answer.rule)

    if request.operation is DerivationOperation.PERCENTAGE_CHANGE and relative_change_across_zero(
            from_fact.value, to_fact.value):
        return _refuse(
            request, DerivationRefusalCode.RELATIVE_CHANGE_ACROSS_ZERO,
            f"{from_fact.value} -> {to_fact.value} is a relative change nobody can read: the "
            "base is zero or the two values sit on opposite sides of it (§13.3's third gate)")
    if request.operation is DerivationOperation.RATIO and from_fact.value == 0:
        return _refuse(
            request, DerivationRefusalCode.DENOMINATOR_ZERO,
            f"{from_fact.observation_id} is the denominator and it is zero")

    return ValidatedDerivation(
        request=request,
        from_fact=from_fact,
        to_fact=to_fact,
        from_point=from_point,
        to_point=to_point,
        claim=claim,
        comparability_rule_ids=rules_evaluated(answer),
        warning_codes=tuple(sorted(warning.code for warning in answer.warnings)),
    )


def _claim_kind(
    operation: DerivationOperation, from_fact: PackagedFact, to_fact: PackagedFact
) -> ClaimKind:
    """Which of §6.9's four claim kinds this operation is making, and why each is that one.

    * A **two-period** operation asserts a step, so it is `MOVEMENT` — R8's tolerance floor and
      R10's adjacency both apply to a step claim and to nothing else. `ACCELERATION` is not used:
      it is the same gate set plus a third point, and every operation here takes exactly two.
    * A **same-period** operation over two different metrics is `DIVERGENCE`, which is the only
      kind R2 admits two metrics under, and the only one R10 does not apply to — a gap between
      two definitions of one quantity asserts no step.
    * A same-period operation over **one** metric is `LEVEL`. §6.1 guarantees one canonical fact
      per `(metric, period)` slot, so a package cannot hold two, and this branch is unreachable
      today. It is written anyway because the rule is about what a claim asserts, not about what
      this corpus happens to contain, and a `KeyError`-shaped hole would be the wrong way to
      record that.
    """
    if operation in TWO_PERIOD_OPERATIONS:
        return ClaimKind.MOVEMENT
    return (ClaimKind.DIVERGENCE if from_fact.metric_id != to_fact.metric_id
            else ClaimKind.LEVEL)


def _point(fact: PackagedFact, subject_entity_id: str) -> CanonicalPoint:
    """A `PackagedFact` back into the `CanonicalPoint` R1–R10 are written against.

    **Not a second canonicalisation.** §6.1 already ran: a fact reached the package because it
    was the canonical reading of its slot, so the status is `OK` by construction and the
    representative observation is the fact's own id. What is rebuilt here is only the *shape*
    `comparable` needs — the period, the unit, the scale R8's presentation tolerance is keyed on
    — and every field of it comes off the row rather than being recomputed.

    The provenance fields §6.1 step 6 fills are left at their defaults on purpose. `n_docs`,
    `first_filed` and `last_filed` are §6.10's ranking inputs and `CanonicalPoint`'s own
    docstring forbids recomputing them from a package; no comparability rule reads one, so the
    honest thing is to leave them empty rather than to fill them with a number this row cannot
    support.
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


def _series(
    package: StoryEvidencePackage, metric_id: str, subject_entity_id: str
) -> CanonicalSeries:
    """One metric's package-local series, ordered the way `build_series` orders a corpus one.

    See the module docstring for what this is and is not evidence of. Built rather than passed
    in because §4.3 defines the offer set as a function of the package alone, and a series
    threaded down from the packager would make the printed offer list depend on an argument the
    replay store does not digest.
    """
    return CanonicalSeries(
        metric_id=metric_id,
        points=tuple(sorted(
            (_point(fact, subject_entity_id)
             for fact in package.facts if fact.metric_id == metric_id),
            key=lambda point: (point.period.anchor_date or "", point.period.key),
        )),
    )


def _refuse(
    request: DerivationRequest,
    code: DerivationRefusalCode,
    detail: str,
    *,
    rule_id: str = "",
) -> DerivationRefusal:
    return DerivationRefusal(
        code=code,
        operation=str(getattr(request.operation, "value", request.operation)),
        from_fact_id=request.from_fact_id,
        to_fact_id=request.to_fact_id,
        detail=detail,
        rule_id=rule_id,
    )


def is_offered(request: DerivationRequest, offered: Sequence[DerivationRequest]) -> bool:
    """§4.3's membership test, as a named function rather than an `in` at two call sites."""
    return any(offer == request for offer in offered)


__all__ = [
    "ADMITTED_UNITS",
    "SAME_PERIOD_OPERATIONS",
    "TWO_PERIOD_OPERATIONS",
    "is_offered",
    "offers",
    "validate",
]
