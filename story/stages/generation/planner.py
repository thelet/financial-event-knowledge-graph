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

**Three rules are code after the call, not instructions in the prompt.** §15.3 prohibits
`minItems` and `pattern`, so "counterpoints must be non-empty" and "this id must exist" cannot
be expressed to the grammar at all; a prompt line asking for them is a request, and this stage
needs a refusal. `plan_violations` is that refusal, it is pure, and it is the same function
D5's verifier can run against a hand-written plan:

1. every `required_fact_id` and `required_citation_passage_id` resolves in the package;
2. `counterpoints` is non-empty when `counter_evidence` is, every counterpoint is grounded in
   at least one id drawn from `counter_evidence`, and every unused counter-evidence item is
   accounted for in `unusable_evidence`;
3. `causal_language` is the value **code** computed from the package.

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
from story.core.models import (
    CausalLanguage,
    Counterpoint,
    EditorialPlan,
    GenerationResult,
    KeyPoint,
    StoryEvidencePackage,
    UnusableEvidence,
    WarningKind,
)
from story.providers.portable_schema import schema_violations, validate_portable_schema
from story.providers.public import PINNED_TEMPERATURE, StoryProviderSchemaError
from story.stages.generation.prompts import (
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
    """

    def __init__(self, message: str, violations: Sequence[PlanViolation] = ()) -> None:
        super().__init__(message)
        self.violations = tuple(violations)

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
    plan: EditorialPlan, package: StoryEvidencePackage
) -> tuple[PlanViolation, ...]:
    """Every reason this plan may not reach the writer. Empty means accepted.

    Pure and provider-free, so a hand-written plan, a replayed plan and a freshly generated one
    are all judged by one function.
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

    computed = causal_language_for(package)
    if plan.causal_language is not computed:
        found.append(PlanViolation(
            CAUSAL_LANGUAGE_NOT_COMPUTED,
            f"causal_language is {plan.causal_language.value!r}; code computes "
            f"{computed.value!r} from this package's cited spans (§11, §13.10)"))

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


def editorial_plan_from(
    content: Mapping[str, Any],
    package: StoryEvidencePackage,
    *,
    model_id: str,
    prompt_version: str = PLANNER_PROMPT_VERSION,
) -> EditorialPlan:
    """One schema-conformant answer as an `EditorialPlan`, or `EditorialPlanRejected`.

    Four fields never come from the model: `candidate_id` and `package_id` identify the package
    the plan was made from, `prompt_version` and `model_id` identify what made it, and
    `causal_language` is computed here from the package. The model's own `causal_language` is
    **not read at all** — the schema pins it to one value, and reading it back would make the
    field the model's on the day the grammar is wrong.

    `required_warnings` is the model's, narrowed by `claim_qualifying_warnings`: the model may
    choose which caveats the post must state, and may not put the package's build provenance
    among them.
    """
    computed = causal_language_for(package)
    try:
        plan = EditorialPlan(
            candidate_id=package.candidate_id,
            package_id=package.package_id,
            thesis=content["thesis"],
            why_it_matters=content["why_it_matters"],
            key_points=tuple(
                KeyPoint(
                    claim=row["claim"],
                    required_fact_ids=tuple(row.get("required_fact_ids") or ()),
                    required_citation_passage_ids=tuple(
                        row.get("required_citation_passage_ids") or ()),
                    statement_class=row["statement_class"],
                ) for row in content.get("key_points") or ()),
            counterpoints=tuple(
                Counterpoint(
                    claim=row["claim"],
                    required_fact_ids=tuple(row.get("required_fact_ids") or ()),
                    required_citation_passage_ids=tuple(
                        row.get("required_citation_passage_ids") or ()),
                ) for row in content.get("counterpoints") or ()),
            required_warnings=claim_qualifying_warnings(
                tuple(content.get("required_warnings") or ()), package),
            causal_language=computed,
            uncertainty=content.get("uncertainty") or "",
            structure=tuple(content.get("structure") or ()),
            prohibited_claims=tuple(content.get("prohibited_claims") or ()),
            unusable_evidence=tuple(
                UnusableEvidence(id=row["id"], reason=row["reason"])
                for row in content.get("unusable_evidence") or ()),
            prompt_version=prompt_version,
            model_id=model_id,
        )
    except (ValidationError, KeyError, TypeError, AttributeError, ValueError) as exc:
        # `Counterpoint` refuses an entirely ungrounded counterpoint at construction and
        # `UnusableReason` refuses a free-text reason; both arrive here as the same kind of
        # answer — one the frozen types will not hold — and both are the model's answer rather
        # than a fault.
        raise EditorialPlanRejected(
            f"the model's plan cannot be constructed: {exc}",
            (PlanViolation(PLAN_NOT_CONSTRUCTIBLE, str(exc)),)) from exc

    violations = plan_violations(plan, package)
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
) -> PlannedStory:
    """§11's whole stage: package in, accepted plan out.

    Two arguments and a budget. There is no `terms`, no retriever and no configuration
    parameter, which is the §11 correction stated as a signature rather than as a promise.

    `max_tokens` is keyword-only with no default because it is a digest input to
    `request_identity`; pass `prompts.PLANNER_MAX_TOKENS` unless something measured says
    otherwise. `temperature` is not a parameter at all — `PINNED_TEMPERATURE` is pinned here,
    at the call site, exactly as §15.1 requires.
    """
    causal_language = causal_language_for(package)
    schema = planner_schema(causal_language=causal_language)
    # The real provider refuses a non-portable schema when it builds the request body; the
    # replaying one never builds a body at all. Refusing here makes §15.3's guarantee a
    # property of this stage rather than of whichever provider it was handed.
    validate_portable_schema(schema)

    result = provider.generate(
        system=PLANNER_SYSTEM,
        prompt=planner_prompt(package),
        schema=schema,
        schema_name=PLANNER_SCHEMA_NAME,
        max_tokens=max_tokens,
        temperature=PINNED_TEMPERATURE,
    )

    violations = tuple(schema_violations(result.content, schema))
    if violations:
        # Never retried: the server was asked for schema-constrained output and answered, and
        # re-asking at temperature 0 returns the same thing while charging for it twice.
        raise StoryProviderSchemaError(
            f"the planner's answer does not satisfy schema {PLANNER_SCHEMA_NAME!r}: "
            + "; ".join(violations), violations)

    plan = editorial_plan_from(
        result.content, package,
        # The *configured* identity where the provider states one, not the `.gguf` path the
        # server reports back — the distinction `generation_store` keeps as two fields.
        model_id=getattr(provider, "model_id", "") or result.model_id,
        prompt_version=PLANNER_PROMPT_VERSION)
    return PlannedStory(plan=plan, generation=result)
