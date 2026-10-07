"""The first model call: an evidence package in, a schema-validated `EditorialPlan` out.

Responsibility: build one request from one package, read one answer, and refuse the answer if
it names anything the package does not hold. **No graph, no retrieval, no tools.** This module
has a `StoryGenerationProvider` and a `StoryEvidencePackage` and nothing else — no
`ReadQueryExecutor`, no `GraphRetriever`, no detector, no driver, no HTTP client. The
structural half of that claim is asserted by
`tests/story/test_story_planner.py::test_the_planner_module_reaches_no_graph_no_retrieval_and_no_driver`,
which reads this file's imports rather than trusting this paragraph.

**The planner may not choose search terms, and may not influence which evidence exists.** It
receives the package as it was built. §11's first draft rested on the stronger claim that the
model cannot influence the evidence set at all, and that claim is false for one section:
`search_passages` takes a `terms[]` and returns `ORDER BY score DESC LIMIT 25`, so adding
terms re-ranks and evicts — **adding three innocuous terms to a two-term query dropped 18 of
the 25 passages the original query returned** *(measured 2026-08-03, §11 correction AR1)*.
Top-k displacement is not addressed by escaping, so the narrower true claim is that numbers,
identities, periods and counter-evidence are model-free while explanatory passage *ranking* is
bounded and traced instead. The consequence for this module is structural: there is no
parameter, no field and no prompt line through which a plan could ask for different evidence,
and §11's second consequence — counter-evidence never comes from `search_passages` — is S5's
to keep in `story/stages/packaging/counter_evidence.py`.

**Four rules are code after the call, not instructions in the prompt.** §15.3 prohibits
`minItems` and `pattern`, so "counterpoints must be non-empty" and "this id must exist" cannot
be expressed to the grammar at all; a prompt line asking for them is a request, and this stage
needs a refusal. `plan_violations` is that refusal, it is pure, and it is the same function
D5's verifier can run against a hand-written plan:

1. every `required_fact_id` and `required_citation_passage_id` resolves in the package;
2. `counterpoints` is non-empty when `counter_evidence` is, every counterpoint is grounded in
   at least one id drawn from `counter_evidence`, and every unused counter-evidence item is
   accounted for in `unusable_evidence`;
3. `causal_language` is the value **code** computed from the package;
4. every `requested_derivations` entry is a triple the prompt actually offered
   (DETERMINISTIC_FACT_TOOLS §4.3). The fourth for the same reason as the first three and not a
   new kind of thing: §15.3 can pin the *operation* to seven words with an `enum` and can say
   nothing at all about which pairs of ids those words are legal over, because that is a fact
   about this package. So the grammar constrains the vocabulary and this function constrains the
   list — and it does so **before the writer runs**, so a plan resting on a quantity nothing
   will compute never costs a second call.

**A fourth thing happens after the call and is a narrowing rather than a rule**:
`claim_qualifying_warnings` drops the package's build-provenance codes from the model's
`required_warnings`. §10.1 publishes one `warnings[]` list serving two audiences, so the model
is answering the question it was asked when it names `token_budget_trimmed`; what it cannot do
is turn a fact about the packaging into a sentence the post must carry. The plan is not
rejected for it — only codes this package itself declares as provenance are dropped, and a code
the package does not carry at all still reaches `unknown_warning_code` below.

**One coherence hole is left open deliberately, and it was observed live.** Nothing here
refuses a plan that both *uses* a counter-evidence item and lists it in `unusable_evidence` —
which is what Qwen3.5-9B did on 2026-08-04, citing `…#p11` in its counterpoint and
simultaneously declaring it `immaterial_at_stated_precision`. §11 defines `unusable_evidence`
as "package items the plan deliberately did not use", so the two declarations contradict each
other; but §11 states no rule about it, and rejecting an otherwise sound plan for a redundant
declaration is a judgment that belongs with §13's verifier rather than here. Recorded rather
than silently enforced or silently ignored.

**A schema violation is the model's answer and is never retried** — the provider raises, and so
does this module when it re-checks the returned content. The re-check is deliberate duplication:
`ReplayingStoryGenerationProvider` replays a stored row without re-validating it, so a stage
whose guarantee depended on which provider it was handed would be a stage with no guarantee.

**What this module does not do**: choose the candidate (`selection_mode: manual_demo_candidate`,
IMPLEMENTATION_STEPS §8b), build the package (S5), write prose (S8), or decide whether the draft
is publishable (S9). It also does not decide the writer's passage set — §10.2.1 point 3 derives
that from fact bindings by code precisely so a plan cannot filter what the writer sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pydantic import ValidationError

from story.contracts import StoryGenerationProvider
from story.core import lexicon
from story.core.spine import StorySpine
from story.core.models import (
    CausalLanguage,
    Counterpoint,
    DerivationRequest,
    DerivedFact,
    EditorialPlan,
    EvidenceScopeFact,
    GenerationResult,
    KeyPoint,
    StatementClass,
    StoryEvidencePackage,
    UnusableEvidence,
    WarningKind,
)
from story.providers.portable_schema import schema_violations, validate_portable_schema
from story.providers.public import PINNED_TEMPERATURE, StoryProviderSchemaError
from story.stages.generation.prompts import (
    DERIVED_ROW,
    PASSAGE_ROW,
    PLANNER_PROMPT_VERSION,
    PLANNER_SCHEMA_NAME,
    PLANNER_SYSTEM,
    planner_prompt,
    planner_schema,
)

#: §13.10 A's list, verbatim, longest-first where one contains another so that a report names
#: the construction the filing actually used. Held here rather than imported from the verifier:
#: S9 owns the sentence-level rule and this module owns a package-level *permission*, and a
#: shared constant between two stages would be `story.stages.verification` imported into
#: `story.stages.generation`, which the package's own structural test refuses.
CAUSAL_MARKERS: tuple[str, ...] = (
    "as a result of", "attributable to", "because of", "on the back of", "the driver of",
    "was impacted by", "contributed to", "stemmed from", "resulted in", "reflecting",
    "thanks to", "owing to", "because", "explains", "reflects", "caused", "due to",
    "drove", "led to", "is why",
)

#: §13.10 condition 5. **37 passages in this corpus carry a negated causal construction** —
#: "…decided to enter into this Agreement *not as a result of* any general solicitation…" — and
#: a span like that satisfies every co-presence test while the filing asserts the opposite.
NEGATION_TOKENS: tuple[str, ...] = ("not", "no", "never", "rather than", "other than")

#: A clause boundary, for condition 5's "within the same clause". Crude and deliberately so:
#: the alternative is a parser, and this test only ever tightens — a negation this misses
#: leaves `reported_only` permitted, and §13.10 refuses the wording again at the sentence.
_CLAUSE_BOUNDARY = re.compile(r"[.;:,]")

_NEGATION = re.compile(r"\b(" + "|".join(NEGATION_TOKENS) + r")\b", re.IGNORECASE)

#: What a cited span is, at plan time. Two kinds and no third: the quote an observation was
#: evidenced by, and an excerpt, which §10.2.1 point 2 defines as a ±400-character window
#: around a matched span. A whole un-excerpted passage is **not** a cited span — that is
#: §13.10 condition 2's whole point, since passages run to thousands of characters and
#: whole-passage containment would let any marker license any claim.
SPAN_FACT_QUOTE = "fact_quote"
SPAN_EXCERPT = "excerpt"

# -- violation codes -----------------------------------------------------------------------
#
# Constants rather than free strings, for the reason `UnusableReason` is an enum: a rejection
# a caller has to parse out of prose is not dispatchable.

UNRESOLVABLE_FACT_ID = "unresolvable_fact_id"
UNRESOLVABLE_PASSAGE_ID = "unresolvable_passage_id"
COUNTERPOINT_MISSING = "counterpoint_missing"
COUNTERPOINT_UNGROUNDED = "counterpoint_ungrounded"
COUNTER_EVIDENCE_UNACCOUNTED = "counter_evidence_unaccounted"
UNKNOWN_WARNING_CODE = "unknown_warning_code"
UNKNOWN_UNUSABLE_ID = "unknown_unusable_id"
CAUSAL_LANGUAGE_NOT_COMPUTED = "causal_language_not_computed"
#: DETERMINISTIC_FACT_TOOLS §4.3 — the plan asked for a derivation that was not on the list the
#: prompt printed. **Spelled the way §6 spells the verifier's own new gate code**, so a refusal
#: here and a finding raised for the same fault downstream are one string and not two; the
#: derivation stage's `DerivationRefusalCode.NOT_OFFERED` carries the same spelling for the same
#: reason. Refused here rather than at the executor because §5 puts the check *before the writer
#: runs*: a plan resting on a quantity nothing will compute is a plan whose key point cannot be
#: written, and discovering that after a writer call has been paid for helps nobody.
DERIVATION_NOT_OFFERED = "derivation_not_offered"

#: A handle the slot table does not hold. Replaces `unresolvable_fact_id` on the current path:
#: §11 rule 1 now asks for `F1`, so an unknown *handle* is what a plan gets wrong. The id code
#: is kept beside it because a replayed 1.2.0 plan still names ids.
UNRESOLVABLE_FACT_HANDLE = "unresolvable_fact_handle"

#: The plan states a direction the spine measured the other way. §3 of
#: `docs/2026-08-26-story-pipeline-stabilization-plan/02-PLANNER-STABILIZATION.md`: this is the
#: refusal `story-v1-76da8465cd95` should have earned and did not, and it fires **before** a
#: writer is asked to carry the claim into prose.
DIRECTION_CONTRADICTS_SPINE = "direction_contradicts_spine"

THESIS_EMPTY = "thesis_empty"
NO_KEY_POINTS = "no_key_points"
PLAN_NOT_CONSTRUCTIBLE = "plan_not_constructible"


@dataclass(frozen=True, slots=True)
class CitedSpan:
    """One span the package actually cites, and where it came from."""

    source_id: str
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class CausalMarkerHit:
    """One §13.10 marker occurrence inside a cited span, with its polarity already judged."""

    source_id: str
    marker: str
    index: int
    negated: bool


@dataclass(frozen=True, slots=True)
class PlanViolation:
    """One reason a plan may not reach the writer. `code` dispatches, `detail` explains."""

    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


class EditorialPlanRejected(RuntimeError):
    """The model answered, the answer satisfied the schema, and §11 refuses it anyway.

    Deliberately **not** a `StoryProviderError`: nothing is wrong with the server or the
    transport, and a caller that caught a provider fault must not also catch this. Deliberately
    not retried either — at temperature 0 the same request returns the same plan, and §11's
    rejection is a statement about the plan, not about the attempt.

    **`generation` carries the answer that was refused** *(added 2026-08-19)*. It is `None` on
    the one refusal raised before a request is built, and set by `plan_story` on every refusal
    raised after one came back. A refusal is not a failure to generate: the tokens were spent
    and the row is in the store, and a run that recorded no result for it under-reported its
    own cost — measured on `data/story_demo/story-v1-b949ecf8bbd6`, whose `generations.jsonl`
    held the planner row while its manifest read `generation_calls: 0`, `total_tokens: 0` and
    `planner_provider_model: {}`. Attached to the refusal rather than fished out of the
    provider's store, because a store is optional and a transport fault genuinely has no result
    to report — and one that reached into a store would have invented one for it.
    """

    def __init__(self, message: str, violations: Sequence[PlanViolation] = ()) -> None:
        super().__init__(message)
        self.violations = tuple(violations)
        self.generation: GenerationResult | None = None

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(violation.code for violation in self.violations)


@dataclass(frozen=True, slots=True)
class PlannedStory:
    """The accepted plan and the generation that produced it.

    Two values because §14's manifest needs the token counts, the latency and the content
    digest, and a function returning only the plan would leave the runner to re-derive them
    from a store that deliberately does not record them.
    """

    plan: EditorialPlan
    generation: GenerationResult


# -- causal language, computed by code -------------------------------------------------------


def cited_spans(package: StoryEvidencePackage) -> tuple[CitedSpan, ...]:
    """Every span the package cites: fact quotes, and passage excerpts.

    An un-excerpted passage contributes nothing. It is in the package whole because a fact is
    bound to it (§13.7 Rule A needs the whole table), and its far end is not a span anything
    cites.
    """
    spans: list[CitedSpan] = [
        CitedSpan(fact.observation_id, SPAN_FACT_QUOTE, fact.quoted_text)
        for fact in package.facts if fact.quoted_text
    ]
    for section in (package.primary_passages, package.context_passages,
                    package.explanatory_passages, package.counter_evidence):
        spans.extend(CitedSpan(passage.passage_id, SPAN_EXCERPT, passage.text)
                     for passage in section if passage.excerpted)
    return tuple(spans)


def causal_marker_hits(package: StoryEvidencePackage) -> tuple[CausalMarkerHit, ...]:
    """Every §13.10 marker occurrence in a cited span, negated ones included and labelled.

    Returned rather than reduced to a boolean because "why is this package `forbidden`?" is a
    question an evidence panel has to answer, and a package with three negated markers and no
    plain one is a different finding from a package with none.
    """
    hits: list[CausalMarkerHit] = []
    for span in cited_spans(package):
        for marker in CAUSAL_MARKERS:
            for match in re.finditer(r"\b" + re.escape(marker) + r"\b", span.text,
                                     re.IGNORECASE):
                hits.append(CausalMarkerHit(
                    source_id=span.source_id, marker=marker, index=match.start(),
                    negated=_negated_before(span.text, match.start())))
    return tuple(hits)


def causal_language_for(package: StoryEvidencePackage) -> CausalLanguage:
    """§11's field, computed from the package **before** the call and never chosen by the model.

    `reported_only` only when a cited span carries a causal marker that its own clause does not
    negate; `forbidden` otherwise, and the schema is then pinned to `forbidden` so the grammar
    cannot emit anything else.

    `reported_only` is a *permission to consider*, not a licence: §13.10's six conditions —
    attribution frame, marker in the cited span, both terms in the span, matching document
    type, polarity, linkage — are judged per sentence at verification and none of them is
    weakened by this value.
    """
    if any(not hit.negated for hit in causal_marker_hits(package)):
        return CausalLanguage.REPORTED_ONLY
    return CausalLanguage.FORBIDDEN


def _negated_before(text: str, index: int) -> bool:
    """Does a negation token precede this marker inside the same clause? (§13.10 condition 5.)"""
    before = text[:index]
    boundaries = [match.end() for match in _CLAUSE_BOUNDARY.finditer(before)]
    clause = before[boundaries[-1]:] if boundaries else before
    return _NEGATION.search(clause) is not None


# -- the §11 rules, as code ------------------------------------------------------------------


def plan_violations(
    plan: EditorialPlan,
    package: StoryEvidencePackage,
    *,
    offered: Sequence[DerivationRequest] = (),
) -> tuple[PlanViolation, ...]:
    """Every reason this plan may not reach the writer. Empty means accepted.

    Pure and provider-free, so a hand-written plan, a replayed plan and a freshly generated one
    are all judged by one function.

    `offered` is the same tuple `planner_prompt` printed (DETERMINISTIC_FACT_TOOLS §4.3), and it
    is compared by **equality on the whole triple** rather than by membership of any part: an
    operation the offer set does not pair with these two ids, and the two ids the other way
    round, are both different derivations, and §6's `derived_fact_orientation_reversed` exists
    precisely because the reversal is a real distinction. Defaulted to empty, which refuses every
    requested derivation — the honest reading for a caller that shows the model no offers,
    since a plan cannot legitimately request from a list it was never given.
    """
    fact_ids = {fact.observation_id for fact in package.facts}
    passage_ids = _passage_ids(package)
    counter_ids = {passage.passage_id for passage in package.counter_evidence}
    # A fact read from a counter-evidence passage grounds a counterpoint just as the passage
    # does; §10's `counter_evidence[]` is typed as passages, and refusing the fact id would
    # make the more precise citation the inadmissible one.
    counter_fact_ids = {fact.observation_id for fact in package.facts
                        if fact.passage_id in counter_ids}
    known_warnings = {warning.code for warning in package.warnings}
    item_ids = fact_ids | passage_ids | {event.event_id for event in package.events}

    found: list[PlanViolation] = []

    if not plan.thesis.strip():
        found.append(PlanViolation(THESIS_EMPTY, "the plan states no thesis"))
    if not plan.key_points:
        # Beyond §11's letter, and stated as such: §11 lists `key_points[]` without requiring
        # one. A plan with none leaves the writer nothing to order, and the post it produces
        # would carry no bound fact at all.
        found.append(PlanViolation(NO_KEY_POINTS, "the plan carries no key point"))

    for index, point in enumerate(plan.key_points):
        found.extend(_id_violations(f"key_points[{index}]", point.required_fact_ids,
                                    point.required_citation_passage_ids,
                                    fact_ids, passage_ids))
    for index, counterpoint in enumerate(plan.counterpoints):
        where = f"counterpoints[{index}]"
        found.extend(_id_violations(where, counterpoint.required_fact_ids,
                                    counterpoint.required_citation_passage_ids,
                                    fact_ids, passage_ids))
        grounded = (set(counterpoint.required_citation_passage_ids) & counter_ids) or (
            set(counterpoint.required_fact_ids) & counter_fact_ids)
        if counter_ids and not grounded:
            found.append(PlanViolation(
                COUNTERPOINT_UNGROUNDED,
                f"{where} {counterpoint.claim!r} names no id drawn from counter_evidence; "
                "§11 point 1 — a counterpoint grounded in nothing is not a counterpoint"))

    if counter_ids and not plan.counterpoints:
        found.append(PlanViolation(
            COUNTERPOINT_MISSING,
            f"the package carries {len(counter_ids)} counter-evidence items and the plan "
            "carries no counterpoint"))

    accounted = {row.id for row in plan.unusable_evidence}
    used = _referenced_passage_ids(plan, package)
    for missing in sorted(counter_ids - used - accounted):
        found.append(PlanViolation(
            COUNTER_EVIDENCE_UNACCOUNTED,
            f"counter-evidence {missing} is neither used nor listed in unusable_evidence"))

    for row in plan.unusable_evidence:
        if row.id not in item_ids:
            found.append(PlanViolation(
                UNKNOWN_UNUSABLE_ID,
                f"unusable_evidence names {row.id!r}, which is not an item of this package"))

    for code in plan.required_warnings:
        if code not in known_warnings:
            found.append(PlanViolation(
                UNKNOWN_WARNING_CODE,
                f"required_warnings names {code!r}; the package carries "
                f"{sorted(known_warnings) or 'no warnings'}"))

    for index, request in enumerate(plan.requested_derivations):
        if any(request == offer for offer in offered):
            continue
        found.append(PlanViolation(
            DERIVATION_NOT_OFFERED,
            f"requested_derivations[{index}] asks for {request.operation.value} from "
            f"{request.from_fact_id!r} to {request.to_fact_id!r}; the prompt offered "
            f"{len(offered)} triples and this is not one of them (§4.3 — the planner may "
            "request only a triple from the list it was shown, and an orientation is part of "
            "the triple)"))

    computed = causal_language_for(package)
    if plan.causal_language is not computed:
        found.append(PlanViolation(
            CAUSAL_LANGUAGE_NOT_COMPUTED,
            f"causal_language is {plan.causal_language.value!r}; code computes "
            f"{computed.value!r} from this package's cited spans (§11, §13.10)"))

    return tuple(found)


#: The words that appear in **both** `lexicon.CHANGE_DIRECTION` and
#: `lexicon.COMPARATIVE_DIRECTION` — today exactly `higher` and `lower`.
#:
#: **Derived from the two vocabularies rather than written down, because the reason they are
#: excluded is precisely that they are in both.** A word in the change map states which way a
#: quantity moved; a word in the comparative map states an ordering of two named sides. A word
#: in both does whichever the sentence's word order says, and a plan binds nothing that would
#: let this function tell which. Measured while writing the check: *"Profit was higher in Q2
#: than Q3"* is a **true** statement of a decrease and was refused as a contradiction.
#:
#: A term added to either map on some later day joins this set automatically, which is the
#: property a hand-typed pair would not have.
ORDERING_WORDS: frozenset[str] = frozenset(
    set(lexicon.CHANGE_DIRECTION) & set(lexicon.COMPARATIVE_DIRECTION))


def spine_violations(
    plan: EditorialPlan, spine: StorySpine | None
) -> tuple[PlanViolation, ...]:
    """Every way this plan's prose disagrees with what code already measured. Empty means agrees.

    **The primary defence against a factually inverted plan is that the schema no longer has a
    field for the direction** — §11's 2.0.0 shape carries no `direction`, no value and no
    derivation triple, so a plan cannot *author* the factual spine. This function is the second
    half: `thesis` and `claim` are free prose and are rendered verbatim into the writer's prompt,
    so a plan that cannot author the direction can still *write a sentence stating it*, and
    `story-v1-76da8465cd95` did exactly that — *"adjusted gross profit **rose** from 2022Q2 to
    2022Q3"* over 556 -> 110.

    **This is not prose parsing in the sense worth avoiding.** It applies `story/core/lexicon.py`
    — the same closed, tested vocabulary `verification/derived_facts._direction_findings` already
    applies to every draft sentence — one stage earlier. `CHANGE_DIRECTION` is asserted total over
    `numerals.CHANGE_VERBS` by a test, and its third state matters: `improved`, `widened`,
    `turned` and `reversed` map to `None`, meaning *"states no direction of the stored number"*,
    and none of them raises here.

    **Silence is not a violation, and that asymmetry is deliberate.** §13's
    `derived_direction_not_stated_in_text` refuses a *sentence* that binds a directional derived
    fact and names no direction, because such a sentence states a figure the reader cannot place.
    A *plan* has no such duty — its job is the angle, and a thesis that never says which way the
    metric went is a thesis the writer can still carry correctly. Copying the stronger rule up
    would refuse valid plans.

    **A `None` spine, or one whose direction is unverified, disables the direction check and
    nothing else.** `quantity_direction` answers `None` where a metric's sign convention has no
    observation to measure it from, and a check that guessed there would be inventing the one
    thing the detector refused to.

    **`ORDERING_WORDS` is skipped for the same reason, and finding it is why that set is derived
    rather than typed out.**

    **Comparatives are deliberately not checked here, and the reason is a false positive found
    while writing this.** A change verb states which way the quantity moved and needs no
    knowledge of word order — *"fell"* means one thing wherever it sits. A comparative states an
    ordering of two named sides, and both sides of this spine are the **same metric in different
    periods**: *"the third quarter was higher than the second"* contradicts a decrease and *"the
    second quarter was higher than the third"* states it correctly. Telling them apart needs the
    periods resolved on each side of the comparing word, which `claims._comparison_text_findings`
    can do for a **draft sentence** only because §13 requires such a sentence to bind a
    `compare_levels` fact naming both. A plan binds nothing, so the same resolution is not
    available, and a check that flagged every *"higher"* would refuse a true plan. The draft-side
    check keeps its teeth; this one declines the question rather than answering it badly.
    """
    if spine is None or spine.direction_polarity is None:
        return ()
    found: list[PlanViolation] = []
    wanted = spine.direction_polarity
    for where, text in (("thesis", plan.thesis),
                        *((f"key_points[{index}]", point.claim)
                          for index, point in enumerate(plan.key_points))):
        for match in lexicon.change_verbs(text):
            if match.term.lower() in ORDERING_WORDS:
                continue
            stated = lexicon.change_direction(match.term)
            if stated is None or stated is wanted:
                continue
            if lexicon.negated(text, match):
                # "did not rise" over a fall is not a contradiction. Scoped to the marker's own
                # clause by the same function §13.10 negation-checks a causal marker with.
                continue
            found.append(PlanViolation(
                DIRECTION_CONTRADICTS_SPINE,
                f"{where} says {match.term!r}, which states the quantity went "
                f"{'up' if stated else 'down'}; code measured "
                f"{spine.metric_id} {spine.from_period} -> {spine.to_period} as "
                f"{spine.direction} and the plan may not contradict it"))
    return tuple(found)


def claim_qualifying_warnings(
    codes: Sequence[str], package: StoryEvidencePackage
) -> tuple[str, ...]:
    """The codes that may become `required_warnings`: everything but this package's provenance.

    §10.1's `warnings[]` mixes two families and the plan's `required_warnings` is a demand for
    *prose* — §13 refuses a draft that does not say each one. Build provenance is not a claim
    qualifier: an investor post that stated *"this package's token budget was trimmed"* would be
    reporting on its own plumbing, and the first end-to-end run failed exactly that way, with
    five of nine blocking findings demanding a sentence for `token_budget_trimmed`,
    `section_truncated`, `subject_identity_not_read_from_graph`, `evidence_sources_absent_in_v1`
    and `relationships_unavailable_in_v1` *(measured 2026-08-04)*.

    **Filtered rather than refused, and only for codes this package actually declares as
    provenance.** A code the package does not carry at all falls straight through to
    `plan_violations`' `unknown_warning_code` refusal, which is the check that stops a plan from
    inventing a caveat; dropping unknown codes here would swallow it. The model is not at fault
    for naming a code §10.1 published without a kind, so the plan is not rejected for it.

    Reads `PackagedWarning.kind` off the package rather than a table of codes: the kinds are
    declared in `story/stages/packaging/warning_codes.py` and a stage may not import another
    stage, so the package is how the decision travels here (`test_no_stage_imports_another_stage`).
    """
    provenance = {warning.code for warning in package.warnings
                  if warning.kind is WarningKind.BUILD_PROVENANCE}
    return tuple(code for code in codes if code not in provenance)


def _passage_ids(package: StoryEvidencePackage) -> set[str]:
    return {passage.passage_id
            for section in (package.primary_passages, package.context_passages,
                            package.explanatory_passages, package.counter_evidence)
            for passage in section}


def _referenced_passage_ids(
    plan: EditorialPlan, package: StoryEvidencePackage
) -> set[str]:
    """Passages the plan leans on, directly or through a fact it cites."""
    passage_of = {fact.observation_id: fact.passage_id for fact in package.facts}
    used: set[str] = set()
    for row in (*plan.key_points, *plan.counterpoints):
        used.update(row.required_citation_passage_ids)
        used.update(passage_of.get(fact_id) or "" for fact_id in row.required_fact_ids)
    used.discard("")
    return used


def _id_violations(
    where: str,
    fact_ids: Sequence[str],
    passage_ids: Sequence[str],
    known_facts: set[str],
    known_passages: set[str],
) -> list[PlanViolation]:
    found: list[PlanViolation] = []
    for fact_id in fact_ids:
        if fact_id not in known_facts:
            found.append(PlanViolation(
                UNRESOLVABLE_FACT_ID,
                f"{where} names fact {fact_id!r}, which the package does not hold"))
    for passage_id in passage_ids:
        if passage_id not in known_passages:
            found.append(PlanViolation(
                UNRESOLVABLE_PASSAGE_ID,
                f"{where} names passage {passage_id!r}, which the package does not hold"))
    return found


# -- the answer, as a plan -------------------------------------------------------------------


def _resolved_facts(
    handles: Sequence[str],
    rows: Mapping[str, Any],
    package: StoryEvidencePackage,
    inputs_of: Mapping[str, tuple[str, ...]],
    *,
    passages_ground: bool = False,
) -> tuple[tuple[str, ...], tuple[str, ...], StatementClass, tuple[str, ...]]:
    """One point's handles as `(fact ids, passage ids, statement class, unknown handles)`.

    **A `D` handle resolves to its two input observations, not to the derived id.** §11 rule 1
    restricts `required_fact_ids` to ids beginning `obs:`, and a derived fact is not one — so a
    point built on `D1` requires the two readings `D1` was computed from, which is exactly what
    the point rests on. That also keeps `writer.thesis_violations` comparable: it reads the
    handles a draft names against these ids, and a draft naming `D1` names a row whose `fact_id`
    is the derived id, so the join has to happen on one side or the other. It happens here,
    where the package is in scope.

    **The passage ids are a lookup and never the model's.** `PackagedFact.passage_id` is the
    passage a reading was cited in; a plan cannot know a different answer and could only spell
    this one wrong, which is what 1.2.0's `unresolvable_passage_id` existed to catch.

    **The statement class follows the handles.** A point naming only observations is `reported`;
    one naming any derived row is `calculated`. `explanatory` is unreachable from a handle,
    which is correct for every package in the corpus — all of them carry zero explanatory
    passages, and `story-v1-76da8465cd95` returned `explanatory` for one of them.
    """
    passage_of = {fact.observation_id: fact.passage_id for fact in package.facts}
    fact_ids: list[str] = []
    passages_named: list[str] = []
    unknown: list[str] = []
    derived_named = False
    for handle in handles:
        row = rows.get(str(handle))
        if row is None:
            unknown.append(str(handle))
            continue
        if row.kind == DERIVED_ROW:
            derived_named = True
            fact_ids.extend(inputs_of.get(row.fact_id, ()))
        elif row.kind == PASSAGE_ROW:
            # **A passage grounds a counterpoint and never a key point**, which is `passages_ground`
            # and is not a convenience. §11 requires a counterpoint to rest on something drawn
            # from `counter_evidence`, and counter-evidence is typed as *passages* — a package
            # whose counter passage backs no packaged fact cannot be answered with a fact handle
            # at all, and building the table without `P` rows made such a package unplannable:
            # every counterpoint earned `counterpoint_ungrounded` and every empty one earned
            # `counterpoint_missing`, with no third answer. A key point is the opposite case: it
            # states a figure, so a passage where a fact belongs is the model naming the wrong
            # kind of thing and is refused as one.
            if passages_ground:
                passages_named.append(row.fact_id)
            else:
                unknown.append(str(handle))
        else:
            fact_ids.append(row.fact_id)
    ordered = tuple(dict.fromkeys(fact_ids))
    passages = tuple(dict.fromkeys(
        [passage for passage in (passage_of.get(fact_id) for fact_id in ordered) if passage]
        + passages_named))
    kind = StatementClass.CALCULATED if derived_named else StatementClass.REPORTED
    return ordered, passages, kind, tuple(unknown)


def editorial_plan_from(
    content: Mapping[str, Any],
    package: StoryEvidencePackage,
    *,
    model_id: str,
    prompt_version: str = PLANNER_PROMPT_VERSION,
    offered: Sequence[DerivationRequest] = (),
    slots: Sequence[Any] = (),
    derived_facts: Sequence[DerivedFact | EvidenceScopeFact] = (),
    spine: StorySpine | None = None,
) -> EditorialPlan:
    """One schema-conformant answer as an `EditorialPlan`, or `EditorialPlanRejected`.

    **`EditorialPlan` is unchanged and the schema is not, which is what makes this affordable.**
    2.0.0 asks the model for seven leaves; the type still carries nineteen fields, and the twelve
    the model no longer writes are filled here from the package, the slot table and the spine. So
    every stored `editorial_plan.json` still loads, the demo UI's plan panel is untouched, and
    the verifier's `required_counterpoint_absent` and `required_warning_absent` keep reading the
    fields they always read.

    What code fills, and from what:

    | field | source |
    | --- | --- |
    | `candidate_id`, `package_id` | the package |
    | `prompt_version`, `model_id` | the call site |
    | `causal_language` | `causal_language_for(package)` — computed, never read from the answer |
    | `required_fact_ids` | the handles the point named, resolved through the slot table |
    | `required_citation_passage_ids` | `PackagedFact.passage_id` for those facts |
    | `statement_class` | whether any handle named a derived row |
    | `required_warnings` | `claim_qualifying_warnings` over the package's own codes, in full |
    | `requested_derivations` | empty — `story/core/spine.py` executed what the detector fired on, before this call |
    | `structure`, `prohibited_claims`, `unusable_evidence` | empty; nothing read them |

    `slots` is the same table the prompt printed the handles from, passed by the composition
    root for the reason every other cross-stage value is: a table built twice is a table that can
    disagree with the one the model was shown.
    """
    computed = causal_language_for(package)
    rows = {row.handle: row for row in slots}
    # A derived row's two inputs, read off the fact rather than off the row: `SlotRow` carries
    # `evidence_handles` (cells) and not fact ids, and §11 restricts `required_fact_ids` to
    # observations. This is the one join the slot table cannot answer.
    inputs_of = {fact.fact_id: (fact.from_fact_id, fact.to_fact_id)
                 for fact in derived_facts if isinstance(fact, DerivedFact)}
    unknown: list[str] = []
    try:
        points: list[KeyPoint] = []
        for row in content.get("key_points") or ():
            fact_ids, passages, kind, missing = _resolved_facts(
                tuple(row.get("facts") or ()), rows, package, inputs_of)
            unknown.extend(missing)
            points.append(KeyPoint(
                claim=row["claim"],
                required_fact_ids=fact_ids,
                required_citation_passage_ids=passages,
                statement_class=kind,
            ))

        counterpoints: list[Counterpoint] = []
        counter_claim = str(content.get("counterpoint") or "").strip()
        if counter_claim:
            fact_ids, passages, _kind, missing = _resolved_facts(
                tuple(content.get("counterpoint_facts") or ()), rows, package, inputs_of,
                passages_ground=True)
            unknown.extend(missing)
            counterpoints.append(Counterpoint(
                claim=counter_claim,
                required_fact_ids=fact_ids,
                required_citation_passage_ids=passages,
            ))

        plan = EditorialPlan(
            candidate_id=package.candidate_id,
            package_id=package.package_id,
            thesis=content["thesis"],
            why_it_matters=content["why_it_matters"],
            key_points=tuple(points),
            counterpoints=tuple(counterpoints),
            # Empty, and not because the field is deprecated. The spine executed the operations
            # the detector fired on before this call, so there is nothing left for a plan to
            # request for a detector-defined story — and a plan that could still ask would be
            # asking for a second copy of what already exists.
            requested_derivations=(),
            # **Deduplicated, and finding out why cost two live runs.** A package raises one
            # warning per *fact*, so two `warned` readings of one metric put
            # `unpreferred_source_lane` in this tuple twice — and §13's `required_warning_absent`
            # then wanted the qualifying phrase said twice, in a four-sentence post about two
            # figures. Measured 2026-08-26 against both providers on `market_count`. A caveat
            # stated twice is not two caveats; the model used to pick the set by hand and
            # naturally listed it once.
            required_warnings=claim_qualifying_warnings(
                tuple(dict.fromkeys(warning.code for warning in package.warnings)), package),
            causal_language=computed,
            uncertainty=content.get("uncertainty") or "",
            structure=(),
            prohibited_claims=(),
            unusable_evidence=(),
            prompt_version=prompt_version,
            model_id=model_id,
        )
    except (ValidationError, KeyError, TypeError, AttributeError, ValueError) as exc:
        # `Counterpoint` refuses an entirely ungrounded counterpoint at construction; it arrives
        # here as the same kind of answer — one the frozen types will not hold — and it is the
        # model's answer rather than a fault.
        raise EditorialPlanRejected(
            f"the model's plan cannot be constructed: {exc}",
            (PlanViolation(PLAN_NOT_CONSTRUCTIBLE, str(exc)),)) from exc

    violations = tuple(
        PlanViolation(
            UNRESOLVABLE_FACT_HANDLE,
            f"the plan names {handle!r}, which is not a handle this run's rows print; the "
            f"handles are {sorted(rows) or 'none at all'}")
        for handle in dict.fromkeys(unknown))
    violations += plan_violations(plan, package, offered=offered)
    violations += spine_violations(plan, spine)
    if violations:
        raise EditorialPlanRejected(
            "the plan is refused before the writer runs (§11): "
            + "; ".join(str(violation) for violation in violations),
            violations)
    return plan


def plan_story(
    package: StoryEvidencePackage,
    *,
    provider: StoryGenerationProvider,
    max_tokens: int,
    offered: Sequence[DerivationRequest] = (),
    slots: Sequence[Any] = (),
    derived_facts: Sequence[DerivedFact | EvidenceScopeFact] = (),
    spine: StorySpine | None = None,
    feedback: str = "",
) -> PlannedStory:
    """§11's whole stage: package in, accepted plan out.

    Two arguments, a budget and the offer set. There is still no `terms`, no retriever and no
    configuration parameter, which is the §11 correction stated as a signature rather than as a
    promise — and `offered` does not weaken it. It is a pure function of the package and the
    candidate computed *before* this call by a stage that has no model in it, so it is one more
    thing the plan may not influence rather than a channel through which it could.

    **The same tuple is printed and checked**, which is why it is one argument and not two: it
    reaches `planner_prompt` and `plan_violations` from here, so the list the model was shown
    and the list its answer is judged against cannot drift apart.

    `max_tokens` is keyword-only with no default because it is a digest input to
    `request_identity`; pass `prompts.PLANNER_MAX_TOKENS` unless something measured says
    otherwise. `temperature` is not a parameter at all — `PINNED_TEMPERATURE` is pinned here,
    at the call site, exactly as §15.1 requires.
    """
    schema = planner_schema()
    # The real provider refuses a non-portable schema when it builds the request body; the
    # replaying one never builds a body at all. Refusing here makes §15.3's guarantee a
    # property of this stage rather than of whichever provider it was handed.
    validate_portable_schema(schema)

    result = provider.generate(
        system=PLANNER_SYSTEM,
        prompt=planner_prompt(package, offered=offered, slots=slots, spine=spine,
                              derived_facts=derived_facts, feedback=feedback),
        schema=schema,
        schema_name=PLANNER_SCHEMA_NAME,
        max_tokens=max_tokens,
        temperature=PINNED_TEMPERATURE,
    )

    # Everything below judges an answer that already exists, so every refusal it raises is a
    # refusal *of a generation* rather than a failure to obtain one. The result is attached so
    # the runner can record what the call cost whichever way the stage ended; a fault raised
    # inside `provider.generate` never reaches this block and carries nothing, which is what
    # keeps a transport failure from being recorded as a call that produced an answer.
    try:
        violations = tuple(schema_violations(result.content, schema))
        if violations:
            # Never retried: the server was asked for schema-constrained output and answered,
            # and re-asking at temperature 0 returns the same thing while charging twice.
            raise StoryProviderSchemaError(
                f"the planner's answer does not satisfy schema {PLANNER_SCHEMA_NAME!r}: "
                + "; ".join(violations), violations)

        plan = editorial_plan_from(
            result.content, package,
            # The *configured* identity where the provider states one, not the `.gguf` path the
            # server reports back — the distinction `generation_store` keeps as two fields.
            model_id=getattr(provider, "model_id", "") or result.model_id,
            prompt_version=PLANNER_PROMPT_VERSION,
            offered=offered, slots=slots, derived_facts=derived_facts, spine=spine)
    except (EditorialPlanRejected, StoryProviderSchemaError) as exc:
        exc.generation = result
        raise
    return PlannedStory(plan=plan, generation=result)
