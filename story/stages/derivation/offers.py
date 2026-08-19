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

#: The operations that may be **offered**, which is §4.1's seven minus `trend_direction`.
#:
#: **An operation the verifier will always refuse has no business in the offer set, and this is
#: a defect a live run found rather than a rule the plan wrote** *(2026-08-19)*. `offers` printed
#: `trend_direction`, `execute_all` computed it, and
#: `story/stages/verification/derived_facts.py:RECOMPUTABLE` then refused any draft binding it as
#: `derived_operation_not_supported` — so `gpt-5.4` requested it, wrote a sentence stating it, and
#: was refused for choosing something it had been shown. The offer set and the verifier are one
#: contract with two ends, and the end that moved is this one.
#:
#: **That sentence was a claim about one operation dressed as a rule, and H2 made it the rule**
#: *(2026-08-19)*. It was written here and left false one function below: `offers` printed both
#: orientations of every two-period pair while `derived_facts._shape_findings` refused every
#: reversed one, so on §2's own candidate three of six printed triples were unbindable whatever
#: the writer did — the same fault as `trend_direction`'s, on a different axis, shipping beside
#: the paragraph that named it. `validate`'s orientation clause is the other half, and
#: `test_every_triple_the_offer_set_may_print_is_one_a_draft_can_bind` is what now holds both:
#: it drives every offered **triple** through `integrity_findings` and requires the finding list
#: to be empty. Until H2 it keyed on the operation and asserted the absence of one code, which
#: is why the offer set could disagree with the verifier under a passing test named for their
#: agreement.
#:
#: **Withdrawn rather than made checkable, and the reason is what the verifier would have to be
#: given.** The word is `detector_config.quantity_direction`'s answer over a metric's stored sign
#: convention — `direct_selling_costs` is negative on 46 of 46 canonical values, so a fall in the
#: number is a rise in the cost — and that table lives in a sibling stage neither the derivation
#: stage nor the verifier may import. This stage takes it as an argument from the composition
#: root, which is right for a *producer*; a verifier that took the same object from the same root
#: would be a verifier configurable into agreement with the thing it is checking, which is
#: `derived_facts.py`'s own *"asking the defendant"*.
#:
#: **And nothing is lost, which is what makes withdrawal the cheap answer rather than the
#: resigned one.** `trend_direction` produces no numeral at all (`result=None`, unit `direction`),
#: so no figure in any post depends on it; the same direction over the same two facts already
#: reaches the writer as `absolute_change`'s `display_semantics`, where it is a word attached to
#: a number the verifier **does** recompute and where `orientation_findings` checks the sentence
#: against it. The verifier's refusal stays exactly where it is and stays reachable — a replayed
#: artifact carrying a `trend_direction` fact is still refused — so this narrows what a planner
#: may ask for and weakens no check.
OFFERABLE_OPERATIONS: frozenset[DerivationOperation] = frozenset(
    set(DerivationOperation) - {DerivationOperation.TREND_DIRECTION})

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

    **Only the forward orientation of a two-period pair is offered, and that is a correction**
    *(2026-08-19)*. This docstring used to say both orientations were printed because
    `derived_fact_orientation_reversed` is *"a check on the draft — whether the prose runs the
    way the fact does — and not a reason to withhold the fact"*. Half of that was false:
    `derived_facts._shape_findings` raises the same code on the **fact**, unconditionally, for
    every backwards two-period derivation. Measured on §2's candidate, three of the six triples
    printed here — `absolute_change`, `percentage_change` and `crossed_zero`, each `2022Q3 →
    2022Q2` — were unbindable whatever the writer did. `validate` now refuses them with the
    verifier's own code, so the two ends state one rule. A same-period operation has no
    chronological orientation, so `compare_levels` and `ratio` are still offered both ways round
    and the writer picks which metric is the subject.

    An offer is *the request that would be granted*, so the type is `DerivationRequest` and not a
    parallel one. §4.3's *"the planner may request only a triple from that list"* is then a
    membership test on one type rather than an agreement between two.
    """
    by_operation: dict[DerivationOperation, list[DerivationRequest]] = {}
    for operation in DerivationOperation:
        for from_fact in sorted(package.facts, key=lambda fact: fact.observation_id):
            for to_fact in sorted(package.facts, key=lambda fact: fact.observation_id):
                request = DerivationRequest(
                    operation=operation,
                    from_fact_id=from_fact.observation_id,
                    to_fact_id=to_fact.observation_id,
                )
                if validate(request, package, candidate, authority=authority):
                    by_operation.setdefault(operation, []).append(request)
    return _capped(by_operation, package.budget.parameters.max_derivations)


def _capped(
    by_operation: Mapping[DerivationOperation, Sequence[DerivationRequest]], cap: int
) -> tuple[DerivationRequest, ...]:
    """`max_derivations` as a **selection** over the operations, not a slice off the front.

    **The slice was measured and it silenced whole operations** *(2026-08-19)*. The enumeration
    above is operation-major, so `found[:cap]` spends the budget on whichever operation §4.1
    lists first. On a full-budget package — twelve facts, one USD metric, twelve adjacent
    quarters, `max_derivations` 12 — the valid set held ten forward `absolute_change`, ten
    `percentage_change` and ten `crossed_zero` triples, and the printed list held **twelve
    `absolute_change` and nothing else**. A planner asking for a percentage change on such a
    package is `derivation_not_offered` and the run dies for a request that would have been
    correct. It failed closed, which is why it was a defect and not an incident.

    So the cap is spent round-robin: the first triple of each operation, then the second of
    each, until the budget runs out. Every operation with any valid triple is represented while
    the cap is at least the number of such operations, which is the property the slice could not
    hold, and no operation takes more than one more slot than another.

    **Deterministic and stably ordered, both halves asserted by test.** The round-robin decides
    *which* triples; the result is then sorted back into `(operation as §4.1 lists it,
    from_fact_id, to_fact_id)`, so the list the planner is shown reads in the documented order
    and a change to the selection cannot reorder the prompt behind it.

    **Within one operation the choice is still positional, and that is left standing.** The
    alternative considered was preferring triples over the candidate's own
    `anchor_observation_ids` — the pair the story is about. It was rejected here because it
    makes the offer list depend on a second field of the candidate for the first time (`validate`
    reads none today), and because on both live candidates the cap does not bind at all: they
    offer three and four triples against a budget of twelve. A packet that raises the cap or
    widens `max_facts` should revisit it, and this paragraph is the note to revisit.
    """
    if cap <= 0:
        return ()
    ranked = [(operation, by_operation.get(operation, ()))
              for operation in DerivationOperation]
    chosen: list[tuple[int, DerivationRequest]] = []
    for slot in range(max((len(found) for _op, found in ranked), default=0)):
        for order, (_operation, found) in enumerate(ranked):
            if slot < len(found):
                chosen.append((order, found[slot]))
    chosen = chosen[:cap]
    return tuple(request for _order, request in sorted(
        chosen, key=lambda pair: (pair[0], pair[1].from_fact_id, pair[1].to_fact_id)))


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

    if request.operation not in OFFERABLE_OPERATIONS:
        # Refused here and not merely dropped from the enumeration, so that "offered" and "valid"
        # stay one predicate: a plan replayed from an artifact recorded before this narrowing
        # names a triple this package would no longer print, and it is refused with the reason
        # rather than executed into a fact the verifier will refuse afterwards.
        return _refuse(
            request, DerivationRefusalCode.OPERATION_NOT_SUPPORTED,
            f"{request.operation.value} is not in OFFERABLE_OPERATIONS: the verifier cannot "
            "re-derive its result from the package alone, so a draft binding one is refused as "
            "`derived_operation_not_supported` and offering it would show a planner something no "
            "post can state. See that constant for why this operation is out and what carries "
            "the same claim instead")

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
    if request.operation in TWO_PERIOD_OPERATIONS and not _anchor(from_fact) < _anchor(to_fact):
        return _refuse(
            request, DerivationRefusalCode.ORIENTATION_REVERSED,
            f"{request.operation.value} is `to - from` and states a step forward in time; "
            f"{from_fact.observation_id} anchors at {_anchor(from_fact) or '(none)'} and "
            f"{to_fact.observation_id} at {_anchor(to_fact) or '(none)'}, so this request runs "
            "backwards. `derived_facts._shape_findings` refuses every fact of that shape as "
            "`derived_fact_orientation_reversed`, and a triple only this end permitted would be "
            "one the planner was shown and no draft could bind. The rise is the forward "
            "derivation, which is offered")

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


def _anchor(fact: PackagedFact) -> str:
    """The date a fact's period ends on, as `derived_facts._shape_findings` reads it.

    Spelled `period_end or instant_date` in both places rather than derived from
    `story_period`, because the verifier's rule is the one this clause has to agree with and an
    agreement stated in two different vocabularies is an agreement waiting to drift. Both ends
    are driven over a reversed `2022Q3 -> 2022Q2` pair —
    `test_every_reversed_two_period_triple_is_refused_and_every_forward_one_is_offered` here and
    `test_the_reversed_orientation_is_still_refused_by_the_verifier_that_forced_the_narrowing`
    there — so a drift shows up as two tests disagreeing rather than as a triple nothing catches.
    """
    return fact.period_end or fact.instant_date or ""


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
    "OFFERABLE_OPERATIONS",
    "SAME_PERIOD_OPERATIONS",
    "TWO_PERIOD_OPERATIONS",
    "is_offered",
    "offers",
    "validate",
]
