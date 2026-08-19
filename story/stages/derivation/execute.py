"""Request → validate → execute → a `DerivedFact`, or a typed refusal (§4, §7).

Responsibility: the one path from a planner's `DerivationRequest` to a fact code computed, and
the one place §7's evidence-scope facts are minted. Validation is `offers.validate`'s — this
module calls it and never re-implements a clause of it, which is what makes §4.3's promise
(*"the offer set is exactly what would pass"*) true by construction rather than by inspection.

**Nothing here falls through to a computed value.** Every exit is either a `DerivedFact` or a
`DerivationRefusal` naming the check that refused and both operands. `RelativeChangeAcrossZero`
is caught and mapped rather than allowed to propagate — `offers.validate` has already excluded
the case it fires on, so the catch is a second lock on the one arithmetic in this stage that can
raise, and a swallowed raise would be exactly the silent number §1 exists to eliminate.

**§7's detector-signal reuse is an assertion and never a preference.** When the candidate's own
`signals` already hold the quantity an operation computes, the two are compared and a
disagreement is `derived_result_mismatch` — a refusal. Two code paths computing one number and
differing is a defect in one of them, and picking a winner would hide it. Measured against the
§2 candidate, `cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d`
carries `delta = -446000000.0`, `delta_pct = -80.215827338`, `direction = "decrease"` and
`crosses_zero = false` — four of the seven operations — **and not the two endpoint values**,
which come from the package's facts. So the reuse covers the *result* and never the *inputs*,
except on a `cross_metric_divergence` candidate, which does publish `left_value` and
`right_value` and where both are therefore checked.

**Two arguments this stage cannot compute arrive as arguments, and both are required.**
`direction` is `detector_config.quantity_direction` (see `public.DirectionOracle` for why it is
not an import); `causal_language` and `causal_marker_fact_ids` are `planner.causal_language_for`
and `planner.causal_marker_hits`, which live in the generation stage for the same reason. None
of the three has a default: a caller who forgot `causal_marker_fact_ids` would mint §7's fact
against a package that does carry a marker, which is the one thing §7 exists to prevent.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from story.core.keys import derived_fact_id, evidence_scope_fact_id
from story.core.models import (
    CausalLanguage,
    DerivationOperation,
    DerivationRequest,
    DerivedFact,
    EvidenceScopeFact,
    PackagedFact,
    StoryCandidate,
    StoryEvidencePackage,
)
from story.core.numerals import RelativeChangeAcrossZero
from story.stages.derivation.offers import is_offered, offers, validate
from story.stages.derivation.operations import (
    CROSSED,
    OPERATIONS,
    OperationOutcome,
    period_surface_hint,
    round_delta,
)
from story.stages.derivation.public import (
    NO_SUPPORTED_CAUSAL_EXPLANATION,
    NO_SUPPORTED_CAUSAL_EXPLANATION_STATEMENT,
    TOOL_VERSION,
    DerivationRefusal,
    DerivationRefusalCode,
    DerivationResult,
    DirectionOracle,
    ValidatedDerivation,
)
from story.core.series import ComparabilityAuthority

#: Which candidate signal holds the quantity each operation computes, per detector. Keyed by
#: `StoryCandidate.story_type` because the signal set is each detector's own — `StoryCandidate`'s
#: `signals` field is free-keyed for that reason — and an operation with no entry simply has no
#: second opinion to be checked against, which is not the same as agreeing with one.
#:
#: `metric_move` publishes five of the seven; `cross_metric_divergence` publishes the gap.
#: `acceleration` and `trend_reversal` publish dispersion and run statistics rather than a
#: quantity any of these seven operations produces, so they have no row — an absence measured
#: from their own `signals` dictionaries rather than assumed.
DETECTOR_SIGNALS: Mapping[str, Mapping[DerivationOperation, str]] = {
    "metric_move": {
        DerivationOperation.ABSOLUTE_CHANGE: "delta",
        DerivationOperation.PERCENTAGE_CHANGE: "delta_pct",
        DerivationOperation.PERCENTAGE_POINT_CHANGE: "delta_pp",
        DerivationOperation.CROSSED_ZERO: "crosses_zero",
        DerivationOperation.TREND_DIRECTION: "direction",
    },
    "cross_metric_divergence": {
        DerivationOperation.COMPARE_LEVELS: "gap",
    },
}


def execute(
    request: DerivationRequest,
    package: StoryEvidencePackage,
    candidate: StoryCandidate,
    *,
    direction: DirectionOracle,
    offered: Sequence[DerivationRequest],
    authority: ComparabilityAuthority | None = None,
) -> DerivedFact | DerivationRefusal:
    """One request, executed or refused.

    **`offered` is required and is not recomputed here.** §4.3's rule is that the planner may
    request only a triple *from the list it was shown*, so the executor has to check against
    that same list; deriving a fresh one would let the printed offer set and the checked offer
    set drift apart — which is precisely the failure mode a list computed twice has.

    **§4.2 runs before §4.3's membership test, which is the reverse of the order the plan lists
    them in, and it makes the refusal strictly more informative.** A triple that is not offered
    is not offered *for a reason*, and nine times in ten the reason is a clause of §4.2 —
    `derived_inputs_incomparable` naming R6, or `derived_unit_mismatch` naming the percent
    gate. Checking membership first would collapse all of them into `derivation_not_offered`,
    which tells a planner that it asked for something it should not have and not what was wrong
    with it. Run this way round, `derivation_not_offered` means exactly one thing: *this
    derivation is valid and was not on the list you were shown* — a request beyond
    `max_derivations`, or one from a plan replayed against a different package. Nothing is
    executed before both checks pass, so §4.3's *"refused, not attempted"* is unweakened.
    """
    validated = validate(request, package, candidate, authority=authority)
    if isinstance(validated, DerivationRefusal):
        return validated

    if not is_offered(request, offered):
        return DerivationRefusal(
            code=DerivationRefusalCode.NOT_OFFERED,
            operation=str(getattr(request.operation, "value", request.operation)),
            from_fact_id=request.from_fact_id,
            to_fact_id=request.to_fact_id,
            detail=(f"{len(offered)} triples were offered for {package.package_id} and this is "
                    "not one of them; it would pass every clause of §4.2, so it was left out by "
                    "the max_derivations cap or the plan was made against another package"),
        )

    try:
        outcome: OperationOutcome = OPERATIONS[request.operation](  # type: ignore[operator]
            validated, direction=direction)
    except RelativeChangeAcrossZero as refused:
        # Unreachable from a validated input; caught so that it can never become a number.
        return DerivationRefusal(
            code=DerivationRefusalCode.RELATIVE_CHANGE_ACROSS_ZERO,
            operation=request.operation.value,
            from_fact_id=request.from_fact_id,
            to_fact_id=request.to_fact_id,
            detail=str(refused),
        )

    reused = _detector_agreement(validated, candidate, outcome)
    if isinstance(reused, DerivationRefusal):
        return reused

    return _fact(validated, package, outcome, reused_detector_signal=reused)


def execute_all(
    requests: Sequence[DerivationRequest],
    package: StoryEvidencePackage,
    candidate: StoryCandidate,
    *,
    direction: DirectionOracle,
    causal_language: CausalLanguage,
    causal_marker_fact_ids: Sequence[str],
    authority: ComparabilityAuthority | None = None,
    offered: Sequence[DerivationRequest] | None = None,
) -> DerivationResult:
    """Every request the planner made, plus §7's facts, in one result.

    Requests are executed in the order the planner listed them and the results keep that order:
    the plan is an artifact §14 stores, and a stage that reordered its inputs would make the
    stored plan and the stored derivations disagree about what was asked.

    `offered` defaults to a freshly computed offer set for the convenience of a caller that has
    not printed one — a test, or a run with no planner in it. A pipeline that put the list in
    front of the model **must** pass that list; see `execute`.
    """
    granted = offers(package, candidate, authority=authority) if offered is None else offered
    facts: list[DerivedFact] = []
    refusals: list[DerivationRefusal] = []
    for request in requests:
        answer = execute(request, package, candidate,
                         direction=direction, offered=granted, authority=authority)
        if isinstance(answer, DerivationRefusal):
            refusals.append(answer)
        else:
            facts.append(answer)
    return DerivationResult(
        facts=tuple(facts),
        evidence_scope_facts=evidence_scope_facts(
            package,
            causal_language=causal_language,
            causal_marker_fact_ids=causal_marker_fact_ids),
        refusals=tuple(refusals),
    )


def evidence_scope_facts(
    package: StoryEvidencePackage,
    *,
    causal_language: CausalLanguage,
    causal_marker_fact_ids: Sequence[str],
) -> tuple[EvidenceScopeFact, ...]:
    """§7's fact, minted **only** when the package deterministically establishes the absence.

    Three conditions, all required, and each is a property of the package rather than of a
    model's reading of it: `explanatory_passages` is empty, `causal_language` is `FORBIDDEN`, and
    no fact carries a causal marker.

    **The third condition is not redundant, and measuring that is worth a sentence.**
    `planner.cited_spans` includes every fact's `quoted_text`, so `causal_language_for` already
    scans the fact quotes — but it counts a marker only when its own clause does not negate it,
    and §7 asks whether a fact *carries* one at all. A package whose table quote reads *"was not
    driven by"* is `FORBIDDEN` and still carries a marker, and the sentence *"this package
    supplies no explanation"* would be false about it. So the negated hits are passed in too and
    this refuses on them.

    Never requested by a planner, because absence is not a calculation — which is also why this
    is not one of §4.1's seven operations and takes no `DerivationRequest`.

    Returns a tuple rather than an optional value: §7 declares one claim today and the artifact
    is a list, so a second claim is a new branch here and not a new return type everywhere.
    """
    if package.explanatory_passages:
        return ()
    if causal_language is not CausalLanguage.FORBIDDEN:
        return ()
    if causal_marker_fact_ids:
        return ()
    return (EvidenceScopeFact(
        fact_id=evidence_scope_fact_id(
            claim=NO_SUPPORTED_CAUSAL_EXPLANATION,
            package_id=package.package_id,
            tool_version=TOOL_VERSION),
        claim=NO_SUPPORTED_CAUSAL_EXPLANATION,
        statement=NO_SUPPORTED_CAUSAL_EXPLANATION_STATEMENT,
        package_id=package.package_id,
        tool_version=TOOL_VERSION,
        examined_fact_ids=tuple(sorted(fact.observation_id for fact in package.facts)),
    ),)


def _fact(
    validated: ValidatedDerivation,
    package: StoryEvidencePackage,
    outcome: OperationOutcome,
    *,
    reused_detector_signal: str,
) -> DerivedFact:
    """The row, with its id minted from the identity the computation actually had.

    `metric_surfaces` is deduplicated in `(from, to)` order rather than sorted: on a
    `DIVERGENCE` pair the order is the comparison's own — *"adjusted gross margin against GAAP
    gross margin"* reads one way — and a sorted pair would silently rename the claim.
    """
    from_fact, to_fact = validated.from_fact, validated.to_fact
    metric_ids = tuple(dict.fromkeys((from_fact.metric_id, to_fact.metric_id)))
    period_keys = tuple(dict.fromkeys((from_fact.period_key, to_fact.period_key)))
    return DerivedFact(
        fact_id=derived_fact_id(
            operation=validated.operation.value,
            subject_entity_id=package.subject.entity_id,
            metric_ids=metric_ids,
            period_keys=period_keys,
            from_fact_id=from_fact.observation_id,
            to_fact_id=to_fact.observation_id,
            package_id=package.package_id,
            tool_version=TOOL_VERSION),
        operation=validated.operation,
        package_id=package.package_id,
        from_fact_id=from_fact.observation_id,
        to_fact_id=to_fact.observation_id,
        from_period=from_fact.period_key,
        to_period=to_fact.period_key,
        from_value=from_fact.value,
        to_value=to_fact.value,
        result=outcome.result,
        result_word=outcome.result_word,
        unit=outcome.unit,
        currency=outcome.currency,
        display_semantics=outcome.display_semantics,
        metric_id=to_fact.metric_id,
        from_metric_id=from_fact.metric_id,
        metric_surfaces=tuple(dict.fromkeys(
            label for label in (from_fact.metric_label, to_fact.metric_label) if label)),
        period_surface_hint=period_surface_hint(validated.to_point.period),
        comparability_rule_ids=validated.comparability_rule_ids,
        tool_version=TOOL_VERSION,
        reused_detector_signal=reused_detector_signal,
        warning_codes=tuple(sorted(set(validated.warning_codes) | set(outcome.warning_codes))),
    )


def _detector_agreement(
    validated: ValidatedDerivation, candidate: StoryCandidate, outcome: OperationOutcome
) -> str | DerivationRefusal:
    """The signal this result was checked against, or the refusal the disagreement is.

    Returns the empty string when the candidate holds no signal for this operation *at this
    orientation and these periods*. Orientation matters and is not worked around: `delta` is
    `later − earlier`, so the reversed request computes `+446,000,000` against a signal of
    `−446,000,000`. Negating the signal would make the check pass, and it would also be this
    module doing arithmetic on a detector's answer in order to agree with it —
    `delta_pct` does not negate symmetrically anyway, since `(110−556)/556` and `(556−110)/110`
    are different numbers. So the reversed derivation is minted with **no** signal reuse
    recorded, which is honest: nothing checked it, and the field says so.
    """
    by_operation = DETECTOR_SIGNALS.get(candidate.story_type, {})
    name = by_operation.get(validated.operation)
    if name is None or name not in candidate.signals:
        return ""
    if not _signal_applies(validated, candidate):
        return ""
    expected = candidate.signals[name]
    detail = _endpoint_disagreement(validated, candidate) or _disagreement(outcome, name, expected)
    if detail is None:
        return name
    return DerivationRefusal(
        code=DerivationRefusalCode.DETECTOR_SIGNAL_DISAGREES,
        operation=validated.operation.value,
        from_fact_id=validated.from_fact.observation_id,
        to_fact_id=validated.to_fact.observation_id,
        detail=detail,
    )


def _signal_applies(validated: ValidatedDerivation, candidate: StoryCandidate) -> bool:
    """Does the candidate's signal describe **this** pair, in this direction?

    A package can carry more facts of a metric than the candidate anchored on — `max_facts` is
    12 and a two-period `metric_move` anchors two — so a signal named for the candidate's own
    step says nothing about a derivation over a different pair of quarters, and asserting
    against it would refuse a correct result.
    """
    from_fact, to_fact = validated.from_fact, validated.to_fact
    if candidate.story_type == "cross_metric_divergence":
        signals = candidate.signals
        return (from_fact.period_key == to_fact.period_key
                and from_fact.period_key in candidate.anchor_period_keys
                and from_fact.metric_id == signals.get("right_metric_id")
                and to_fact.metric_id == signals.get("left_metric_id"))
    if {from_fact.period_key, to_fact.period_key} != set(candidate.anchor_period_keys):
        return False
    if to_fact.metric_id not in candidate.metric_ids:
        return False
    # `delta` and its neighbours are `later − earlier`; only the forward orientation is checked.
    return _anchor(from_fact) < _anchor(to_fact)


def _anchor(fact: PackagedFact) -> str:
    """`coalesce(period_end, instant_date)` — the date a period is ordered by (§6.1)."""
    return fact.period_end or fact.instant_date or ""


def _disagreement(outcome: OperationOutcome, name: str, expected: Any) -> str | None:
    """`None` when the two agree; a sentence naming both values when they do not.

    Floats are compared after `round_delta`, at `DELTA_PRECISION`, and never with `==`. The
    detectors do not all round the same way — `metric_move` rounds its delta and then divides to
    get `delta_pct`, while `cross_metric_divergence` publishes `gap` raw
    (`3.3 − (−12.6) = 15.899999999999999` on the demo's own candidate) — so an exact comparison
    would report a residue at the seventeenth digit as a disagreement between two correct
    computations.

    `bool` is tested before `int` throughout, for `_cell_indices`' reason: it subclasses `int`,
    so a `crosses_zero` of `False` would otherwise compare as the number zero.
    """
    if outcome.result is not None:
        if not isinstance(expected, (int, float)) or isinstance(expected, bool):
            return (f"the candidate's {name!r} signal is {expected!r}, which is not a number to "
                    f"compare {outcome.result} against")
        if round_delta(outcome.result) != round_delta(float(expected)):
            return (f"code computed {outcome.result} and the detector's {name!r} signal is "
                    f"{expected!r}; two paths to one number disagreeing is a defect in one of "
                    "them and not a preference to resolve")
        return None
    if isinstance(expected, bool):
        computed = outcome.result_word == CROSSED
        if computed != expected:
            return (f"code computed {outcome.result_word!r} and the detector's {name!r} signal "
                    f"is {expected!r}")
        return None
    if outcome.result_word != expected:
        return (f"code computed {outcome.result_word!r} and the detector's {name!r} signal is "
                f"{expected!r}")
    return None


def _endpoint_disagreement(
    validated: ValidatedDerivation, candidate: StoryCandidate
) -> str | None:
    """The one detector that publishes its endpoint values, checked against the package's facts.

    §2 measured that `metric_move` carries `delta` and **not** the two values it was computed
    from — so for that detector there is nothing here to check, and the package is the only
    authority on the inputs. `cross_metric_divergence` does publish `left_value` and
    `right_value`, and a package whose facts disagree with them was built from a different
    canonicalisation than the candidate was, which is a defect the *result* comparison could
    miss: two wrong endpoints whose gap happens to be right still agree on the gap.
    """
    if candidate.story_type != "cross_metric_divergence":
        return None
    for signal_name, fact in (("right_value", validated.from_fact),
                              ("left_value", validated.to_fact)):
        published = candidate.signals.get(signal_name)
        if not isinstance(published, (int, float)) or isinstance(published, bool):
            continue
        if round_delta(fact.value) != round_delta(float(published)):
            return (f"the candidate's {signal_name!r} signal is {published!r} and "
                    f"{fact.observation_id} holds {fact.value}; the package and the candidate "
                    "were built from different canonical readings of one slot")
    return None


__all__ = [
    "DETECTOR_SIGNALS",
    "evidence_scope_facts",
    "execute",
    "execute_all",
]
