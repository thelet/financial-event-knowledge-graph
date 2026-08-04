"""The second model call: an accepted plan in, a structured `Draft` out. Never prose alone.

Responsibility: assemble the writer's slice of one package **by code**, build one request, read
one answer, and refuse the answer if it names anything the slice does not hold or points at
characters its own text does not carry. **No graph, no retrieval, no tools, no verifier.** This
module has a `StoryGenerationProvider`, a `StoryEvidencePackage` and an `EditorialPlan`, and
nothing else — the structural half of that claim is asserted by
`tests/story/test_story_writer.py::test_the_writer_module_reaches_no_graph_no_retrieval_and_no_verifier`,
which reads this file's imports rather than trusting this paragraph.

**§10.2.1 point 3 is the reason this module exists in the shape it does.** The writer's passage
set is derived from *fact bindings* by code — `writer_passages` — and never from the plan's
`required_citation_passage_ids`, which are model output. The first draft of §10 said "give the
writer the plan plus the passages the plan cites", and that would have let one model filter the
next model's universe (§0c item 11). `writer_passages(package)` takes no plan argument at all,
which is that correction stated as a signature rather than as a promise.

**What the model declares and what code computes.** §12 gives every binding and every citation a
`char_start`/`char_end`. Asking a 9B model to count characters in its own sentence would fail on
essentially every call, so the schema asks for the **exact substring** — `rendered` for a
binding, `quote` for a citation — and this module locates it: once, deterministically, refusing a
substring that occurs twice rather than choosing between the occurrences. The declaration is
still the model's and the verifier still checks the binding it was handed rather than guessing
one, which is what §12's argument protects. The consequence is worth stating plainly: a draft
that leaves this module can never fail §13.1's `binding_span_does_not_match_text` or §13.7's
`citation_span_not_in_passage`, because a draft that would have is refused here first, with
`binding_rendering_not_in_text` or `citation_quote_not_in_passage`.

**Six rules are code after the call, not instructions in the prompt** — §15.3 can express none
of them, since it has no `pattern`, no `minItems` and no way to say "this string must occur in
that string":

1. every `fact_id`, in a binding or in a calculation's inputs, resolves in the package;
2. every `rendered` occurs exactly once in its own sentence's `text`;
3. every citation names a passage **in the writer's own slice**, and its `quote` occurs exactly
   once in that passage;
4. a sentence carries at most one calculation — the schema's array is how §15.3 spells
   "optional", not a licence to declare two derivations for one sentence;
5. the draft rests on the plan: it binds at least one fact the plan's key points named. A draft
   that shares no fact with the plan it was given is a different story, which is §12's *"the
   writer must not change the thesis"* in the only form the draft contract can express — there
   is no `thesis` field on a `Draft` to compare;
6. the draft has at least one sentence.

**What this module deliberately does not check.** Percentage-point surfaces, causal language,
superlatives, period grammar, metric ambiguity, calculation recomputation, required warnings and
counterpoint survival are all §13's, and `story.stages.verification` is not a surface this stage
may import (`test_no_stage_imports_another_stage`). Re-implementing a weaker copy of a §13 check
here would create a second authority that could disagree with the first; the prompt tells the
writer what §13 will refuse, and the verifier decides. `tests/story/test_story_writer.py` runs
D5's `DeterministicVerifier` over this module's output end to end, which is where that division
is shown to work rather than asserted.

**A schema violation is the model's answer and is never retried**, exactly as at the planner —
and `DraftRejected` is not a provider error, because nothing is wrong with the server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pydantic import ValidationError

from story.contracts import StoryGenerationProvider
from story.core.models import (
    Calculation,
    Draft,
    DraftSentence,
    EditorialPlan,
    FactBinding,
    GenerationResult,
    PackagedPassage,
    PassageCitation,
    SentenceKind,
    StoryEvidencePackage,
)
from story.providers.portable_schema import schema_violations, validate_portable_schema
from story.providers.public import PINNED_TEMPERATURE, StoryProviderSchemaError
from story.stages.generation.prompts import (
    PLAIN_INVESTOR_STYLE,
    WRITER_PROMPT_VERSION,
    WRITER_SCHEMA_NAME,
    StyleProfile,
    writer_prompt,
    writer_schema,
    writer_system,
)

# -- violation codes -------------------------------------------------------------------------
#
# Constants rather than free strings, for the reason the planner's are: a rejection a caller has
# to parse out of prose is not dispatchable. Named for the *writer's* failure and not for §13's
# code, because these fire before a verifier ever sees the draft and conflating the two
# vocabularies would make a repair loop ask the wrong stage to fix it.

UNRESOLVABLE_FACT_ID = "unresolvable_fact_id"
UNRESOLVABLE_PASSAGE_ID = "unresolvable_passage_id"
BINDING_RENDERING_NOT_IN_TEXT = "binding_rendering_not_in_text"
BINDING_RENDERING_AMBIGUOUS = "binding_rendering_ambiguous_in_sentence"
CITATION_QUOTE_NOT_IN_PASSAGE = "citation_quote_not_in_passage"
CITATION_QUOTE_AMBIGUOUS = "citation_quote_ambiguous_in_passage"
MORE_THAN_ONE_CALCULATION = "more_than_one_calculation"
THESIS_ABANDONED = "thesis_abandoned"
NO_SENTENCES = "no_sentences"
PLAN_NAMES_ANOTHER_PACKAGE = "plan_names_another_package"
DRAFT_NOT_CONSTRUCTIBLE = "draft_not_constructible"


@dataclass(frozen=True, slots=True)
class DraftViolation:
    """One reason a draft may not reach the verifier. `code` dispatches, `detail` explains."""

    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


class DraftRejected(RuntimeError):
    """The model answered, the answer satisfied the schema, and §12 refuses it anyway.

    Deliberately **not** a `StoryProviderError` and deliberately not retried, for the reasons
    `EditorialPlanRejected` is neither: nothing is wrong with the transport, and at temperature 0
    the same request returns the same draft.

    Also **not** a verification result. A `VerifiedDraft` records a decision about a draft that
    exists; these are the answers from which no draft can be built at all.
    """

    def __init__(self, message: str, violations: Sequence[DraftViolation] = ()) -> None:
        super().__init__(message)
        self.violations = tuple(violations)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(violation.code for violation in self.violations)


@dataclass(frozen=True, slots=True)
class WrittenStory:
    """The accepted draft and the generation that produced it.

    Two values for `PlannedStory`'s reason: §14's manifest needs the token counts, the latency
    and the content digest, and a function returning only the draft would leave the runner to
    re-derive them from a store that deliberately does not record them.
    """

    draft: Draft
    generation: GenerationResult


# -- the writer's universe, derived by code ---------------------------------------------------


def writer_passages(package: StoryEvidencePackage) -> tuple[PackagedPassage, ...]:
    """§10.2.1 point 3: the whole text of every passage a packaged fact was read from.

    Takes no plan, by design. Order is the package's own — primary, context, explanatory,
    counter-evidence — so two builds of one package render one prompt, and a passage appearing in
    two sections is yielded once.

    **A counter-evidence passage that evidences no packaged fact is not in the slice**, and that
    is the correct outcome rather than an omission. §10's counter-evidence join is at *document*
    grain, so the ordinary counter-evidence row is a neighbouring table of the same filing; §13.7
    refuses citing one as support (`counter_evidence_cited_as_support`). A counterpoint resting
    only on such a passage can therefore be honoured in exactly one way — by binding a fact read
    from it — and that is the same way §13's `required_counterpoint_absent` accepts. The two
    rules agree, and the writer is not shown text it could only misuse.
    """
    wanted = {fact.passage_id for fact in package.facts if fact.passage_id}
    found: list[PackagedPassage] = []
    seen: set[str] = set()
    for section in (package.primary_passages, package.context_passages,
                    package.explanatory_passages, package.counter_evidence):
        for passage in section:
            if passage.passage_id in wanted and passage.passage_id not in seen:
                seen.add(passage.passage_id)
                found.append(passage)
    return tuple(found)


# -- the §12 rules, as code -------------------------------------------------------------------


def draft_violations(
    draft: Draft,
    package: StoryEvidencePackage,
    plan: EditorialPlan,
    *,
    passages: Sequence[PackagedPassage] | None = None,
) -> tuple[DraftViolation, ...]:
    """Every reason this draft may not reach the verifier. Empty means accepted.

    Pure and provider-free, so a hand-written draft, a replayed one and a freshly generated one
    are judged by one function — the property that makes `plan_violations` worth having.

    `passages` defaults to the slice this module would have built, so a caller cannot widen the
    citable set by passing a longer list than the writer was shown.
    """
    slice_ids = {passage.passage_id
                 for passage in (writer_passages(package) if passages is None else passages)}
    fact_ids = {fact.observation_id for fact in package.facts}
    found: list[DraftViolation] = []

    if not draft.sentences:
        found.append(DraftViolation(NO_SENTENCES, "the draft carries no sentence"))

    for sentence in draft.sentences:
        where = f"sentences[{sentence.index}]"
        for binding in sentence.fact_bindings:
            if binding.fact_id not in fact_ids:
                found.append(DraftViolation(
                    UNRESOLVABLE_FACT_ID,
                    f"{where} binds fact {binding.fact_id!r}, which the package does not hold"))
            occurrences = _occurrences(sentence.text, binding.rendered)
            if len(occurrences) != 1:
                found.append(_rendering_violation(where, binding.rendered, occurrences))
            elif sentence.text[binding.char_start:binding.char_end] != binding.rendered:
                found.append(DraftViolation(
                    BINDING_RENDERING_NOT_IN_TEXT,
                    f"{where} declares {binding.rendered!r} at "
                    f"[{binding.char_start}, {binding.char_end}), which holds "
                    f"{sentence.text[binding.char_start:binding.char_end]!r}"))
        if sentence.calculation is not None:
            for fact_id in sentence.calculation.input_observation_ids:
                if fact_id not in fact_ids:
                    found.append(DraftViolation(
                        UNRESOLVABLE_FACT_ID,
                        f"{where} computes over {fact_id!r}, which the package does not hold"))
        found.extend(_citation_violations(where, sentence, slice_ids, package))

    bound = {binding.fact_id for sentence in draft.sentences
             for binding in sentence.fact_bindings}
    planned = {fact_id for point in plan.key_points for fact_id in point.required_fact_ids}
    if planned and not (bound & planned):
        found.append(DraftViolation(
            THESIS_ABANDONED,
            f"the plan's key points rest on {sorted(planned)} and the draft binds "
            f"{sorted(bound) or 'no fact at all'}; §12 — the writer may not change the thesis"))
    return tuple(found)


def _rendering_violation(
    where: str, rendered: str, occurrences: Sequence[int]
) -> DraftViolation:
    if not occurrences:
        return DraftViolation(
            BINDING_RENDERING_NOT_IN_TEXT,
            f"{where} declares the rendering {rendered!r}, which its own text does not contain")
    return DraftViolation(
        BINDING_RENDERING_AMBIGUOUS,
        f"{where} declares the rendering {rendered!r}, which occurs "
        f"{len(occurrences)} times in its own text; §12's binding names one span and there is "
        "no ground for choosing between them")


def _citation_violations(
    where: str,
    sentence: DraftSentence,
    slice_ids: set[str],
    package: StoryEvidencePackage,
) -> list[DraftViolation]:
    found: list[DraftViolation] = []
    texts = {passage.passage_id: passage
             for section in (package.primary_passages, package.context_passages,
                             package.explanatory_passages, package.counter_evidence)
             for passage in section}
    for citation in sentence.citations:
        if not isinstance(citation, PassageCitation):
            continue  # §13.7.2 Rule C is the verifier's refusal, not this stage's
        if citation.passage_id not in slice_ids:
            found.append(DraftViolation(
                UNRESOLVABLE_PASSAGE_ID,
                f"{where} cites {citation.passage_id!r}, which is not a passage the writer was "
                "shown; §10.2.1 point 3 — the slice is every passage a packaged fact was read "
                "from, and nothing else"))
            continue
        passage = texts.get(citation.passage_id)
        if passage is None:
            found.append(DraftViolation(
                UNRESOLVABLE_PASSAGE_ID,
                f"{where} cites {citation.passage_id!r}, which this package does not hold"))
            continue
        start = citation.char_start - passage.char_start
        end = citation.char_end - passage.char_start
        if start < 0 or end > len(passage.text) or end <= start:
            found.append(DraftViolation(
                CITATION_QUOTE_NOT_IN_PASSAGE,
                f"{where} cites [{citation.char_start}, {citation.char_end}) of "
                f"{citation.passage_id}, which the package's own text does not cover"))
    return found


def _occurrences(haystack: str, needle: str) -> tuple[int, ...]:
    """Every start index of `needle` in `haystack`. Case-sensitive, and deliberately so.

    A binding declares the characters of its own sentence; a case-insensitive match would let
    `"3.3%"` bind a span the sentence spells differently, and §13.1 then compares a numeral the
    draft never wrote.
    """
    if not needle:
        return ()
    found: list[int] = []
    start = haystack.find(needle)
    while start != -1:
        found.append(start)
        start = haystack.find(needle, start + 1)
    return tuple(found)


# -- the answer, as a draft --------------------------------------------------------------------


def draft_from(
    content: Mapping[str, Any],
    package: StoryEvidencePackage,
    plan: EditorialPlan,
    *,
    passages: Sequence[PackagedPassage] | None = None,
    model_id: str,
    style_profile_id: str = PLAIN_INVESTOR_STYLE.profile_id,
    prompt_version: str = WRITER_PROMPT_VERSION,
) -> Draft:
    """One schema-conformant answer as a `Draft`, or `DraftRejected`.

    Four fields never come from the model: `candidate_id` and `package_id` identify the package
    the draft was written from, and `prompt_version`, `model_id` and `style_profile_id` identify
    what wrote it. A model that returned a different candidate id could not re-key the artifact.

    Sentence indexes are positional and are not the model's either — `Draft` requires
    `0..n-1` in order, and a model that numbered its own sentences would eventually skip one and
    make every §13 finding unaddressable.
    """
    shown = tuple(writer_passages(package) if passages is None else passages)
    violations: list[DraftViolation] = []
    sentences: list[DraftSentence] = []
    for index, row in enumerate(content.get("sentences") or ()):
        sentence, found = _sentence_from(index, row, shown)
        violations.extend(found)
        if sentence is not None:
            sentences.append(sentence)
    if violations:
        raise DraftRejected(
            "the writer's draft is refused before verification (§12): "
            + "; ".join(str(violation) for violation in violations), violations)

    try:
        draft = Draft(
            candidate_id=package.candidate_id,
            package_id=package.package_id,
            title=content.get("title") or "",
            sentences=tuple(sentences),
            style_profile_id=style_profile_id,
            prompt_version=prompt_version,
            model_id=model_id,
        )
    except (ValidationError, KeyError, TypeError, AttributeError, ValueError) as exc:
        raise DraftRejected(
            f"the model's draft cannot be constructed: {exc}",
            (DraftViolation(DRAFT_NOT_CONSTRUCTIBLE, str(exc)),)) from exc

    found = draft_violations(draft, package, plan, passages=shown)
    if found:
        raise DraftRejected(
            "the writer's draft is refused before verification (§12): "
            + "; ".join(str(violation) for violation in found), found)
    return draft


def _sentence_from(
    index: int,
    row: Mapping[str, Any],
    passages: Sequence[PackagedPassage],
) -> tuple[DraftSentence | None, list[DraftViolation]]:
    """One schema row as a `DraftSentence`, with every span located rather than trusted."""
    where = f"sentences[{index}]"
    violations: list[DraftViolation] = []
    text = str(row.get("text") or "")

    bindings: list[FactBinding] = []
    for declared in row.get("fact_bindings") or ():
        rendered = str(declared.get("rendered") or "")
        occurrences = _occurrences(text, rendered)
        if len(occurrences) != 1:
            violations.append(_rendering_violation(where, rendered, occurrences))
            continue
        bindings.append(FactBinding(
            fact_id=str(declared.get("fact_id") or ""),
            rendered=rendered,
            char_start=occurrences[0],
            char_end=occurrences[0] + len(rendered),
            metric_surface=str(declared.get("metric_surface") or ""),
            period_surface=str(declared.get("period_surface") or ""),
        ))

    citations: list[PassageCitation] = []
    by_id = {passage.passage_id: passage for passage in passages}
    for declared in row.get("citations") or ():
        passage_id = str(declared.get("passage_id") or "")
        passage = by_id.get(passage_id)
        if passage is None:
            violations.append(DraftViolation(
                UNRESOLVABLE_PASSAGE_ID,
                f"{where} cites {passage_id!r}, which is not a passage the writer was shown"))
            continue
        quote = str(declared.get("quote") or "")
        occurrences = _occurrences(passage.text, quote)
        if len(occurrences) != 1:
            violations.append(_quote_violation(where, passage_id, quote, occurrences))
            continue
        # Rebased to the full `:Passage.text` (§10.2.1 point 2): `PackagedPassage.char_start` is
        # the offset of `text[0]`, and a citation the evidence panel can resolve is an absolute
        # one. Zero for a passage carried whole, which every passage in this slice is.
        citations.append(PassageCitation(
            passage_id=passage_id,
            document_id=passage.document_id,
            char_start=passage.char_start + occurrences[0],
            char_end=passage.char_start + occurrences[0] + len(quote),
        ))

    declared_calculations = list(row.get("calculation") or ())
    calculation: Calculation | None = None
    if len(declared_calculations) > 1:
        violations.append(DraftViolation(
            MORE_THAN_ONE_CALCULATION,
            f"{where} declares {len(declared_calculations)} calculations; §12 gives a sentence "
            "one derivation, and the array is only how §15.3 spells an optional field"))
    elif declared_calculations:
        calculation = _calculation_from(declared_calculations[0])

    if violations:
        return None, violations
    try:
        sentence = DraftSentence(
            index=index,
            text=text,
            kind=SentenceKind(str(row.get("kind") or "")),
            fact_bindings=tuple(bindings),
            calculation=calculation,
            citations=tuple(citations),
        )
    except (ValidationError, ValueError, TypeError) as exc:
        return None, [DraftViolation(DRAFT_NOT_CONSTRUCTIBLE, f"{where}: {exc}")]
    return sentence, []


def _calculation_from(declared: Mapping[str, Any]) -> Calculation:
    """One derivation, with `""` read as *no declared formula*.

    §15.3 has no null, so the schema's `formula_version_id` is a string; an empty one is an
    arithmetic derivation the ontology declares no formula for — a cross-metric gap — and §13.9's
    window check abstains on it rather than failing to find a version nobody claimed.

    `period_surface` is carried through exactly as the model wrote it and is **not** normalised
    here. §13.4 resolves it through the closed grammar and §13.1 locates it in the sentence's
    own characters; a surface tidied on the way past would be checked against words the draft
    does not contain.
    """
    return Calculation(
        operation=str(declared.get("operation") or ""),
        input_observation_ids=tuple(declared.get("input_observation_ids") or ()),
        expression=str(declared.get("expression") or ""),
        result_rendered=str(declared.get("result_rendered") or ""),
        formula_version_id=str(declared.get("formula_version_id") or "") or None,
        period_surface=str(declared.get("period_surface") or ""),
    )


def _quote_violation(
    where: str, passage_id: str, quote: str, occurrences: Sequence[int]
) -> DraftViolation:
    if not occurrences:
        return DraftViolation(
            CITATION_QUOTE_NOT_IN_PASSAGE,
            f"{where} quotes {quote!r} from {passage_id}, which does not contain it")
    return DraftViolation(
        CITATION_QUOTE_AMBIGUOUS,
        f"{where} quotes {quote!r} from {passage_id}, where it occurs {len(occurrences)} "
        "times; §13.7 measured 523 quoted_text strings occurring more than once inside their "
        "own passage, and a citation that cannot say which occurrence resolves to no span")


# -- the rendered post, and it comes from the draft ---------------------------------------------


def render_markdown(draft: Draft) -> str:
    """§12: *"a rendered Markdown post may be produced only from the structured draft"*.

    One argument and it is the draft — there is nowhere to pass the model's raw answer, which is
    what makes the rule structural rather than a habit. Every character of prose here is a
    character some `DraftSentence.text` or the `Draft.title` already carried and §13 already
    judged; this function adds punctuation, headings and the two panels, and invents no numeral.

    The evidence and derivation panels are rendered because they are the artifact's whole point:
    §13.17's `fact_ledger` and `calculation_ledger` are the verifier's copy of the same thing,
    and a post that hid its citations would be a post nobody could check.
    """
    lines: list[str] = []
    if draft.title:
        lines += ["# " + draft.title, ""]
    paragraph: list[str] = []
    for sentence in draft.sentences:
        # A connective sentence is the one place the draft declares a seam, so it opens a
        # paragraph. Any other rule would be this function inventing structure.
        if sentence.kind is SentenceKind.CONNECTIVE and paragraph:
            lines += [" ".join(paragraph), ""]
            paragraph = []
        paragraph.append(sentence.text)
    if paragraph:
        lines += [" ".join(paragraph), ""]

    citations = [(citation, sentence)
                 for sentence in draft.sentences
                 for citation in sentence.citations
                 if isinstance(citation, PassageCitation)]
    if citations:
        lines.append("## Sources")
        for citation, sentence in citations:
            lines.append(f"- sentence {sentence.index}: {citation.passage_id} "
                         f"({citation.document_id}) characters "
                         f"{citation.char_start}-{citation.char_end}")
        lines.append("")

    derivations = [(sentence.index, sentence.calculation) for sentence in draft.sentences
                   if sentence.calculation is not None]
    if derivations:
        lines.append("## Derivations")
        for index, calculation in derivations:
            inputs = ", ".join(calculation.input_observation_ids)
            lines.append(f"- sentence {index}: {calculation.expression} = "
                         f"{calculation.result_rendered} over {inputs}")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


# -- the stage ----------------------------------------------------------------------------------


def write_story(
    package: StoryEvidencePackage,
    plan: EditorialPlan,
    *,
    provider: StoryGenerationProvider,
    style: StyleProfile = PLAIN_INVESTOR_STYLE,
    length_target: int,
    max_tokens: int,
) -> WrittenStory:
    """§12's whole stage: an accepted plan and its package in, a structured draft out.

    There is no `retriever`, no `terms` and no passage argument: the slice is computed here from
    the package's own fact bindings, which is §10.2.1 point 3 as a signature.

    `max_tokens` and `length_target` are keyword-only with no default because both enter the
    request identity §14 keys on — the first directly, the second through the prompt text.
    `temperature` is not a parameter at all: `PINNED_TEMPERATURE` is pinned here, at the call
    site, exactly as §15.1 requires.
    """
    if (plan.package_id, plan.candidate_id) != (package.package_id, package.candidate_id):
        # Refused before the provider is touched. A plan built against another package cannot
        # order this one's evidence, and spending a generation to discover that would put a
        # wrong answer in the replay store under a request that looked legitimate.
        raise DraftRejected(
            "the plan was made from another package and cannot be written from this one",
            (DraftViolation(
                PLAN_NAMES_ANOTHER_PACKAGE,
                f"plan names {plan.candidate_id} / {plan.package_id}; the package is "
                f"{package.candidate_id} / {package.package_id}"),))

    passages = writer_passages(package)
    schema = writer_schema()
    # The real provider refuses a non-portable schema when it builds the request body; the
    # replaying one never builds a body at all. Refusing here makes §15.3's guarantee a property
    # of this stage rather than of whichever provider it was handed.
    validate_portable_schema(schema)

    result = provider.generate(
        system=writer_system(style),
        prompt=writer_prompt(package, plan, passages, length_target=length_target),
        schema=schema,
        schema_name=WRITER_SCHEMA_NAME,
        max_tokens=max_tokens,
        temperature=PINNED_TEMPERATURE,
    )

    violations = tuple(schema_violations(result.content, schema))
    if violations:
        # Never retried: the server was asked for schema-constrained output and answered, and
        # re-asking at temperature 0 returns the same thing while charging for it twice.
        raise StoryProviderSchemaError(
            f"the writer's answer does not satisfy schema {WRITER_SCHEMA_NAME!r}: "
            + "; ".join(violations), violations)

    draft = draft_from(
        result.content, package, plan,
        passages=passages,
        # The *configured* identity where the provider states one, not the `.gguf` path the
        # server reports back — the distinction `generation_store` keeps as two fields.
        model_id=getattr(provider, "model_id", "") or result.model_id,
        style_profile_id=style.profile_id,
        prompt_version=WRITER_PROMPT_VERSION)
    return WrittenStory(draft=draft, generation=result)


__all__ = [
    "BINDING_RENDERING_AMBIGUOUS",
    "BINDING_RENDERING_NOT_IN_TEXT",
    "CITATION_QUOTE_AMBIGUOUS",
    "CITATION_QUOTE_NOT_IN_PASSAGE",
    "DRAFT_NOT_CONSTRUCTIBLE",
    "MORE_THAN_ONE_CALCULATION",
    "NO_SENTENCES",
    "PLAN_NAMES_ANOTHER_PACKAGE",
    "THESIS_ABANDONED",
    "UNRESOLVABLE_FACT_ID",
    "UNRESOLVABLE_PASSAGE_ID",
    "DraftRejected",
    "DraftViolation",
    "WrittenStory",
    "draft_from",
    "draft_violations",
    "render_markdown",
    "write_story",
    "writer_passages",
]
