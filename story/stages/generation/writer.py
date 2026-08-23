"""The second model call: an accepted plan in, sentence templates out. Never prose alone.

Responsibility: assemble the writer's slice of one package **by code**, build one request, read
one answer, and refuse the answer if it names a passage handle the run does not hold, writes a
brace the slot grammar cannot read, or rests on nothing the plan asked for. **No graph, no
retrieval, no tools, no verifier, and no draft compiler.** This module has a
`StoryGenerationProvider`, a `StoryEvidencePackage`, an `EditorialPlan` and a slot table it was
handed, and nothing else — the structural half of that claim is asserted by
`tests/story/test_story_writer.py::test_the_writer_module_reaches_no_graph_no_retrieval_and_no_verifier`,
which reads this file's imports rather than trusting this paragraph.

**What this stage returns changed at S4 of
`docs/2026-08-23-deterministic-draft-compiler/`.** It used to return a `Draft` with every
`FactBinding` and every `PassageCitation` already resolved. It now returns
`tuple[SentenceTemplate, ...]` — the sentences with their slots unfilled — and
`story/stages/composition/compile.py` turns those into the same `Draft` type the verifier has
always consumed, inserting every figure from a trusted row and recording the span it wrote into.
The model no longer declares a rendering, a metric surface, a period surface or a citation,
because there is no field left in its schema for any of them.

**§10.2.1 point 3 is the reason this module exists in the shape it does.** The writer's passage
set is derived from *fact bindings* by code — `writer_passages` — and never from the plan's
`required_citation_passage_ids`, which are model output. The first draft of §10 said "give the
writer the plan plus the passages the plan cites", and that would have let one model filter the
next model's universe (§0c item 11). `writer_passages(package)` takes no plan argument at all,
which is that correction stated as a signature rather than as a promise.

**Which refusals live here now, and which moved.** §12's question is *"may this answer be built
from at all"*; the compiler's is *"what does this template mean"*. The split is by subject and
not by convenience.

Here, over the model's answer:

1. the answer has at least one sentence (`no_sentences`);
2. every sentence's `kind` is a `SentenceKind` and its `rests_on` is a list of strings
   (`draft_not_constructible`) — §15.3 has an `enum` for the first and nothing for the second;
3. no sentence carries a brace the slot grammar cannot read (`malformed_slot`);
4. only an `explanatory` sentence rests on a passage (`rests_on_without_explanatory_sentence`),
   and every handle it rests on is a **passage** handle of this run's slot table
   (`rests_on_not_a_passage_handle`);
5. the templates rest on the plan: they name at least one row the plan's key points named
   (`thesis_abandoned`). A draft that shares no fact with the plan it was given is a different
   story, which is §12's *"the writer must not change the thesis"* in the only form the contract
   can express — there is no `thesis` field on a template to compare.

There, over what a template *means*: an unknown handle, an unknown field, a field the row does
not offer, a field slot with no value slot beside it, a row with no legal rendering, a bound fact
with no evidence handle. Those are R3, R4 and R6 of the architecture document, they need the slot
table's `offers` to answer, and a weaker copy of them here would be exactly the second authority
`story/stages/composition/` exists to remove.

**The two spans this module used to compute are both gone, and each was a real contract.** A
binding used to declare `rendered`, a substring of the model's own sentence, and this module
located it — refusing a substring occurring twice rather than choosing between the occurrences.
A citation used to declare an `evidence_id`, and this module resolved the cell behind it through
`story.core.table_cells`. That second contract replaced a worse one: the model used to retype a
`quote`, which was **unsatisfiable for a fifth of the corpus** — `EVIDENCED_BY.quoted_text` is a
bare cell value of median 4 characters occurring more than once in its own passage for **523 of
2,704** observations *(verified live 2026-08-13)*. Both survive below in `draft_from`, unused by
this path; on the S4 path the compiler writes the span because it did the substitution (R2) and
mints the citation from the row's own `evidence_handles` (R5), so the model is asked for neither.

**`derived_facts` and `slots` are both sequences this stage is handed rather than anything it
computes, for two different reasons.** §3 forbids a derived fact from entering
`StoryEvidencePackage.facts` — that would put a model's selection inside
`package_content_digest`, a `story_run_id` input — so there is nothing here to recompute one
from. The slot rows are `story.stages.composition.slot_table`'s, and this stage may not import
that module at all; `story/pipeline.py` is the composition root and hands the same rows to this
call and to `compile_draft`, so the prompt prints exactly what the compiler will fill from.
Both default to empty, which is the safe direction in both cases: a caller that ran no derivation
stage writes a post with no derived figure in it, and a caller that built no slot table earns
`thesis_abandoned` rather than a post built from handles nothing knows.

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

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pydantic import ValidationError

from story.contracts import StoryGenerationProvider
from story.core.evidence_slice import (
    EvidenceUnresolved,
    EvidenceUnresolvedReason,
    occurrences as _core_occurrences,
    passages_backing_facts,
    span_for_handle,
)
from story.core.models import (
    DerivedFact,
    Draft,
    DraftSentence,
    EditorialPlan,
    EvidenceScopeFact,
    FactBinding,
    GenerationResult,
    PackagedFact,
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
    SlotRowView,
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
#: §3.4 check 1, at §12's grain: the `evidence_id` the model wrote is not a handle this package
#: minted. Every fabrication lands here — a handle for a cell no fact occupies, a handle naming
#: another passage, a handle for a fact of another package — because `evidence_handle` is
#: derived from coordinates a `PackagedFact` already carries and nothing else mints one. The
#: name is §3.4's, deliberately, so §12's refusal and §13.7's read the same in a panel.
UNRESOLVABLE_EVIDENCE_HANDLE = "unresolvable_evidence_handle"
#: §3.4 check 2: the handle is the package's own and its coordinates name no cell in the
#: passage's grid — `table_cells.CellOutOfBounds`. **This is not a model failure and cannot be
#: one**, since the coordinates come off the `PackagedFact`, not off the answer. It fires when a
#: package and the passage text it carries disagree: a stored package replayed against a
#: re-extracted corpus, or an excerpted passage a fact was read outside of. Given a name rather
#: than allowed to escape as an `IndexError` because a citation that resolves to nothing is a
#: refusal, and a traceback is not one.
EVIDENCE_HANDLE_OUT_OF_BOUNDS = "evidence_handle_out_of_bounds"
CITATION_QUOTE_NOT_IN_PASSAGE = "citation_quote_not_in_passage"
CITATION_QUOTE_AMBIGUOUS = "citation_quote_ambiguous_in_passage"
#: `more_than_one_calculation` stood here and was **retired**, not renamed. It refused a
#: `calculation` array holding two objects — the array being how §15.3 spelled an optional field
#: — and DETERMINISTIC_FACT_TOOLS §5 removed the field from the writer's schema entirely. There
#: is no answer this module can now receive that would raise it. `_quote_violation`'s rule is
#: that deleting a *reachable* code is worse than keeping one that has not fired; the converse
#: is what applies here, since a catalogue offering a refusal no stage can produce is a
#: catalogue that describes a system this is not.
THESIS_ABANDONED = "thesis_abandoned"
NO_SENTENCES = "no_sentences"
PLAN_NAMES_ANOTHER_PACKAGE = "plan_names_another_package"
DRAFT_NOT_CONSTRUCTIBLE = "draft_not_constructible"

# -- the three template faults, added at S4 -----------------------------------------------------
#
# Each names something the **writer** did to its own answer, and each is caught here rather than
# in `story/stages/composition/` for one reason: the composition stage refuses a template it
# cannot compile, and its vocabulary is about the *template* — `template_not_compilable`,
# `rests_on_without_explanatory_kind`, `passage_handle_unknown`. These three say the same three
# things about the **model's answer**, before a compiler is asked, so a repair loop knows which
# stage to send the fault back to. The codes are deliberately spelled differently from the
# compiler's for that reason and not by accident; a shared string would make a §12 refusal and a
# composition refusal indistinguishable in the rejection panel.

#: `{{F3` that never closes, `{{ f3 }}`, `{{F3.Metric}}` — a brace the slot grammar cannot read.
#: Refused here rather than left in the text, because a template that reaches the compiler
#: carrying one is refused there anyway and a post is never built from it either way; what this
#: buys is the refusal naming *the model wrote a slot wrong* instead of *the compiler could not
#: read a template*.
MALFORMED_SLOT = "malformed_slot"
#: `rests_on` on a `reported`, `calculated` or `connective` sentence. The compiler resolves
#: citations by kind and only `explanatory` rests on a passage; every other kind cites the
#: evidence behind the facts its slots name, so a `rests_on` there is asking for a citation the
#: kind cannot carry.
RESTS_ON_WITHOUT_EXPLANATORY_SENTENCE = "rests_on_without_explanatory_sentence"
#: A `rests_on` entry that is not a passage handle in this run's slot table: an unknown handle,
#: or a fact row's handle (`rests_on: ["F1"]`). Both are the same mistake — `rests_on` names the
#: passage a claim paraphrases, and a fact is not a passage — so both land here.
RESTS_ON_NOT_A_PASSAGE_HANDLE = "rests_on_not_a_passage_handle"

#: `story.stages.composition.public.SlotKind.PASSAGE`'s value. A checked copy of one string, in
#: the shape this repository already uses for a constant that must not drift across a boundary
#: it cannot import over (`WARNING_QUALIFIER_PHRASES`, `slot_table.TWO_PERIOD_OPERATIONS`):
#: `tests/story/test_story_writer.py::test_the_passage_row_marker_is_the_composition_enums_own`
#: asserts it equals the enum member. `SlotKind` is a `str` enum precisely so the value is what
#: travels, so the comparison is exact rather than approximate.
PASSAGE_ROW = "passage"

#: `{{H}}` and `{{H.field}}`, and there is no third form. **A checked copy of
#: `story.stages.composition.compile.SLOT_PATTERN`**, asserted identical by
#: `tests/story/test_story_writer.py::test_the_slot_grammar_here_is_the_compilers_own`, for the
#: reason above the codes: this stage may not import that one, and a §12 refusal for a brace the
#: grammar cannot read has to know what the grammar is. The copy is narrow — one regex, one line
#: — and the alternative was leaving `MALFORMED_SLOT` unreachable and the fault to surface a
#: stage later under a code that names the compiler rather than the writer.
SLOT_PATTERN = re.compile(r"\{\{\s*([A-Z][0-9]+)(?:\.([a-z_]+))?\s*\}\}")

#: The braces no template may still carry once its well-formed slots are removed.
_UNREADABLE_BRACES = ("{{", "}}")


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

    **`generation` carries the answer that was refused** *(added 2026-08-19)*, exactly as
    `EditorialPlanRejected.generation` does and for the same measured reason. It stays `None`
    for the one refusal this module raises *before* a request is built — a plan made from
    another package — because that run genuinely spent nothing, and a field filled from
    anywhere but the call itself would have claimed otherwise.
    """

    def __init__(self, message: str, violations: Sequence[DraftViolation] = ()) -> None:
        super().__init__(message)
        self.violations = tuple(violations)
        self.generation: GenerationResult | None = None

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(violation.code for violation in self.violations)


@dataclass(frozen=True, slots=True)
class SentenceTemplate:
    """One sentence as the model wrote it, before any slot is filled.

    **This is `story.stages.composition.public.SentenceTemplate`, field for field, and it is a
    copy because this stage may not import that one.**
    `tests/story/test_story_package_structure.py::test_no_stage_imports_another_stage` forbids one
    stage importing another from the module path alone, and §4.5 of
    `docs/2026-08-23-deterministic-draft-compiler/01-TARGET-ARCHITECTURE.md` puts the compiler in
    its own stage and *"template parsing rules"* in this one. So the type this function returns is
    named in a module this module cannot reach, and something had to give.

    **The three options, and why this one.** Moving the type to `story/core/models.py` would put a
    contract in the shared layer that only two stages speak, and re-key nothing but would still be
    a `core` change made from inside a stage that does not own that file. Returning plain
    dictionaries would move the parsing rules' output outside the type system exactly where the
    §12 refusals are decided. A copy asserted equal by a test is the repository's established
    answer to *"one value, two sides of a boundary that cannot be imported over"* —
    `prompts.WARNING_QUALIFIER_PHRASES` against the verifier's table,
    `slot_table.TWO_PERIOD_OPERATIONS` against the derivation stage's two sets,
    `renderings.NON_NUMERIC_DERIVED_UNITS` — and it is the one taken here.
    `tests/story/test_story_writer.py::test_the_template_this_stage_returns_is_the_compilers_own`
    compares `dataclasses.fields` on both and drives a tuple of these through `compile_draft`, so
    a field added on either side fails loudly rather than at the seam.

    `index` is positional and is not the model's — `Draft` requires `0..n-1` in order, and a model
    that numbered its own sentences would eventually skip one and make every §13 finding
    unaddressable. `templates_from` assigns it.
    """

    index: int
    #: Carries `{{H}}` and `{{H.field}}` placeholders. Everything outside them is the model's
    #: own prose and is copied through untouched.
    text: str
    kind: SentenceKind
    #: Passage handles, `explanatory` only. Required-and-possibly-empty because
    #: `story/providers/portable_schema.py` forbids optional properties.
    rests_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WrittenStory:
    """The accepted templates and the generation that produced them.

    Two values for `PlannedStory`'s reason: §14's manifest needs the token counts, the latency
    and the content digest, and a function returning only the answer would leave the runner to
    re-derive them from a store that deliberately does not record them.

    **`draft` became `templates`, and the name of the dataclass did not** (S4). What this stage
    returns is no longer a `Draft` — it is the sentences before any slot is filled, and the
    `Draft` is `story/stages/composition/compile.py`'s to build. Keeping the type name means the
    manifest code, the runner and the demo UI keep their variable, and the one field that moved
    fails at every call site that reads it rather than silently carrying something else.

    `title` rides on `templates`' side of the boundary as a plain string because the compiler
    takes it as an argument: a title carries no slot (`claims.py` gives it no binding, so every
    numeral in it but a period key of the package's own is refused), so there is nothing to
    parse and nothing to fill.
    """

    templates: tuple[SentenceTemplate, ...]
    generation: GenerationResult
    title: str = ""


# -- the writer's universe, derived by code ---------------------------------------------------


def writer_passages(package: StoryEvidencePackage) -> tuple[PackagedPassage, ...]:
    """§10.2.1 point 3's slice, under the name this stage's rule is stated in.

    The slice itself is `evidence_slice.passages_backing_facts`, which carries the reasoning:
    it moved to `core/` because the draft compiler resolves the same passages
    (`docs/2026-08-23-deterministic-draft-compiler/01-TARGET-ARCHITECTURE.md` §4.5) and a stage
    may not import another stage.

    **The name and the signature stay here** because they are the rule rather than a convenience:
    this module's docstring, the prompt printer and `story.stages.generation.__all__` all name
    *the writer's passages*, and *one package, no plan* is §0c item 11's correction stated as a
    signature. Renaming it at the call sites would have made the alias look like debt instead of
    a boundary.
    """
    return passages_backing_facts(package)


# -- the §12 rules, as code -------------------------------------------------------------------


def draft_violations(
    draft: Draft,
    package: StoryEvidencePackage,
    plan: EditorialPlan,
    *,
    passages: Sequence[PackagedPassage] | None = None,
    derived_facts: Sequence[DerivedFact | EvidenceScopeFact] = (),
) -> tuple[DraftViolation, ...]:
    """Every reason this draft may not reach the verifier. Empty means accepted.

    Pure and provider-free, so a hand-written draft, a replayed one and a freshly generated one
    are judged by one function — the property that makes `plan_violations` worth having.

    `passages` defaults to the slice this module would have built, so a caller cannot widen the
    citable set by passing a longer list than the writer was shown.

    `derived_facts` does **not** default to anything computable, for the opposite half of the
    same reason: there is no way to derive this run's derived facts from the package, so an
    empty default cannot be a silently-widened set — it can only be a narrower one, and a
    binding to an id nothing in this run minted is exactly what `unresolvable_fact_id` is for.
    """
    slice_ids = {passage.passage_id
                 for passage in (writer_passages(package) if passages is None else passages)}
    fact_ids = _bindable_ids(package, derived_facts)
    found: list[DraftViolation] = []

    if not draft.sentences:
        found.append(DraftViolation(NO_SENTENCES, "the draft carries no sentence"))

    for sentence in draft.sentences:
        where = f"sentences[{sentence.index}]"
        for binding in sentence.fact_bindings:
            if binding.fact_id not in fact_ids:
                found.append(DraftViolation(
                    UNRESOLVABLE_FACT_ID,
                    f"{where} binds fact {binding.fact_id!r}, which is neither a fact of "
                    f"{package.package_id} nor a derived fact of this run"))
            occurrences = _occurrences(sentence.text, binding.rendered)
            if len(occurrences) != 1:
                found.append(_rendering_violation(where, binding.rendered, occurrences))
            elif sentence.text[binding.char_start:binding.char_end] != binding.rendered:
                found.append(DraftViolation(
                    BINDING_RENDERING_NOT_IN_TEXT,
                    f"{where} declares {binding.rendered!r} at "
                    f"[{binding.char_start}, {binding.char_end}), which holds "
                    f"{sentence.text[binding.char_start:binding.char_end]!r}"))
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


def _bindable_ids(
    package: StoryEvidencePackage,
    derived_facts: Sequence[DerivedFact | EvidenceScopeFact],
) -> set[str]:
    """Every id a `FactBinding` may name: the package's observations, plus this run's derived.

    **`EvidenceScopeFact` is deliberately not in the set.** §7's row carries no result, no unit
    and no period, so there is nothing for a binding's `rendered` to be a rendering *of*; it is
    a limit on what may be written and never a figure to write. Including it would make
    `FactBinding(fact_id="fact:evidence-scope:…")` resolvable here and unresolvable at §13.1,
    which is the shape of disagreement between two stages this module exists to avoid.
    """
    return ({fact.observation_id for fact in package.facts}
            | {row.fact_id for row in derived_facts if isinstance(row, DerivedFact)})


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
    """Every §12 refusal a *finished* citation can carry, whoever built it.

    This judges a `PassageCitation` that already has a span, so it is the path a hand-written or
    a replayed draft takes; `_citations_from` is the path the model's answer takes, and the two
    do not overlap by construction — a citation `_citations_from` built is inside its passage
    because a resolver put it there.

    The handle is checked on **every** citation, because `PassageCitation.evidence_handle` is
    now required with no default. It read *"checked only when the citation carries one"* until
    the adversarial review of S7: `None` was sayable, and on a table fact it turned every §3.4
    check off with nothing weaker underneath. A citation that cannot name a handle is a citation
    nothing can be asked about, so the type no longer builds.
    """
    found: list[DraftViolation] = []
    texts = {passage.passage_id: passage
             for section in (package.primary_passages, package.context_passages,
                             package.explanatory_passages, package.counter_evidence)
             for passage in section}
    handles = package.facts_by_evidence_handle()
    for citation in sentence.citations:
        if not isinstance(citation, PassageCitation):
            continue  # §13.7.2 Rule C is the verifier's refusal, not this stage's
        if citation.evidence_handle not in handles:
            found.append(DraftViolation(
                UNRESOLVABLE_EVIDENCE_HANDLE,
                f"{where} cites evidence {citation.evidence_handle!r}, which this package minted "
                "for no fact"))
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


#: Every start index of a substring, case-sensitive — `evidence_slice.occurrences`, which owns
#: the reason the comparison is case-sensitive. Kept under this module's own name because rule 2
#: (*"every `rendered` occurs exactly once in its own sentence"*) is stated in terms of it three
#: times below, and because the compiler needs the same scan over text nobody has written yet.
_occurrences = _core_occurrences


# -- the answer, as templates -------------------------------------------------------------------


def templates_from(
    content: Mapping[str, Any],
    package: StoryEvidencePackage,
    plan: EditorialPlan,
    *,
    slots: Sequence[SlotRowView] = (),
) -> tuple[SentenceTemplate, ...]:
    """One schema-conformant answer as sentence templates, or `DraftRejected`.

    **This is `draft_from`'s parsing half under the S4 contract, and it is a different function
    rather than a branch of that one.** `draft_from` builds a `Draft`: it locates every
    `rendered` in its own sentence, resolves every `evidence_id` to a span and mints the four
    §12 types. None of that exists here — the model declares no rendering, no surface and no
    citation — so the two share nothing but the sentence index and the `kind`. `draft_from`
    stays, unused by this path, as the reader for the 50 recorded `data/story_demo/*/draft.json`
    artifacts and the pre-3.0.0 replay stores.

    **What survives from §12, in the vocabulary that still applies.**

    * `no_sentences` — an answer with an empty `sentences` array. Unchanged.
    * `draft_not_constructible` — the schema re-check at the grain a JSON schema cannot express:
      a `kind` outside `SentenceKind`, a `rests_on` that is not a list of strings. §15.3 has an
      `enum` for the first and nothing at all for the second.
    * `thesis_abandoned` — now computed over the **slot handles** the templates name, resolved to
      real ids through `slots`. The plan's key points name `obs:` ids; a template names `F2`; the
      row is what joins them. A model handed no slot table names no resolvable handle and earns
      this refusal, which is the honest answer for a caller that built no table.

    **Three refusals are new, and each is a fault a template can carry that no §13 code names**
    — `malformed_slot`, `rests_on_without_explanatory_sentence`, `rests_on_not_a_passage_handle`.
    They are caught here rather than left to the compiler because they are statements about the
    *model's answer*, and §12's job is to refuse an answer before anything is built from it. What
    is deliberately **not** caught here is every refusal about whether a slot can be *filled* — an
    unknown handle, an unknown field, a field the row does not offer, a field slot with no value
    slot beside it. Those are R3 and R4, they are the compiler's, and a weaker copy of them in
    this module would be the second authority `story/stages/composition/` exists to avoid.

    `plan_names_another_package` is not raised here either, and stays in `write_story`: it is the
    one refusal that fires *before* a request is built, and spending a generation to discover it
    would put a wrong answer in the replay store under a request that looked legitimate.
    """
    rows = {row.handle: row for row in slots}
    violations: list[DraftViolation] = []
    templates: list[SentenceTemplate] = []
    for index, row in enumerate(content.get("sentences") or ()):
        template, found = _template_from(index, row, rows)
        violations.extend(found)
        if template is not None:
            templates.append(template)

    if not templates and not violations:
        violations.append(DraftViolation(NO_SENTENCES, "the draft carries no sentence"))

    # Every sentence the model wrote, not only the ones that became templates. A sentence with
    # a violation of its own is dropped from `templates`, and reading the thesis off what
    # survived made a `rests_on` mistake in one sentence report `thesis_abandoned` as well —
    # measured 2026-08-23, on a draft whose very first slot named a fact the plan required. A
    # cascading finding names the wrong repair, which is the whole reason these codes are
    # separate constants.
    violations.extend(_thesis_violations(
        [str(row.get("text") or "") for row in (content.get("sentences") or ())], plan, rows))
    if violations:
        raise DraftRejected(
            "the writer's answer is refused before compilation (§12): "
            + "; ".join(str(violation) for violation in violations), violations)
    return tuple(templates)


def _template_from(
    index: int, row: Mapping[str, Any], rows: Mapping[str, SlotRowView]
) -> tuple[SentenceTemplate | None, list[DraftViolation]]:
    """One schema row as a `SentenceTemplate`, with the three template faults refused.

    The text is copied through **untouched** — no stripping, no normalising, no brace repair.
    Every character outside a slot is the model's own prose and reaches the reader, and a module
    that tidied it would be a module that could change a sentence's meaning between the check and
    the post.
    """
    where = f"sentences[{index}]"
    text = str(row.get("text") or "")
    violations: list[DraftViolation] = []

    residue = SLOT_PATTERN.sub("", text)
    if any(brace in residue for brace in _UNREADABLE_BRACES):
        violations.append(DraftViolation(
            MALFORMED_SLOT,
            f"{where} carries braces the slot grammar cannot read: {text!r}. A slot is "
            "{{H}} or {{H.field}}, H matching [A-Z][0-9]+ and the field lower case, and there "
            "is no third form, no nesting and no escape"))

    try:
        kind = SentenceKind(str(row.get("kind") or ""))
    except ValueError as exc:
        # The schema's `enum` should have caught this before the answer reached here. It is still
        # checked, because `schema_violations` runs against the schema this stage built and a
        # replaying provider hands back whatever was recorded — an answer stored under an older
        # enum is exactly the shape that arrives with a `kind` no member holds.
        return None, violations + [DraftViolation(DRAFT_NOT_CONSTRUCTIBLE, f"{where}: {exc}")]

    declared = row.get("rests_on") or ()
    if isinstance(declared, str) or not isinstance(declared, Sequence):
        return None, violations + [DraftViolation(
            DRAFT_NOT_CONSTRUCTIBLE,
            f"{where} declares rests_on {declared!r}, which is not a list of passage handles")]
    rests_on = tuple(str(handle) for handle in declared)

    if rests_on and kind is not SentenceKind.EXPLANATORY:
        violations.append(DraftViolation(
            RESTS_ON_WITHOUT_EXPLANATORY_SENTENCE,
            f"{where} is {kind.value} and rests on {', '.join(rests_on)}; only an explanatory "
            "sentence rests on a passage, and every other kind is cited from the evidence "
            "behind the facts its slots name"))
    for handle in rests_on:
        found = rows.get(handle)
        if found is None or found.kind != PASSAGE_ROW:
            violations.append(DraftViolation(
                RESTS_ON_NOT_A_PASSAGE_HANDLE,
                f"{where} rests on {handle!r}, which is not a passage handle in this run's slot "
                "table; rests_on names the passage a claim paraphrases, and the PASSAGES "
                "section prints every handle it may hold"))

    if violations:
        return None, violations
    return SentenceTemplate(index=index, text=text, kind=kind, rests_on=rests_on), []


def _thesis_violations(
    texts: Sequence[str],
    plan: EditorialPlan,
    rows: Mapping[str, SlotRowView],
) -> list[DraftViolation]:
    """§12's *"the writer must not change the thesis"*, read off slot handles instead of bindings.

    The rule is unchanged and only its evidence moved: a draft that shares no fact with the plan
    it was given is a different story, and there is no `thesis` field on a template to compare.
    What the draft rests on used to be `FactBinding.fact_id`; it is now the id of the row behind
    every handle a template names.

    **Every handle a template names counts, not only its value slots.** A `{{F3.period}}` with no
    `{{F3}}` beside it is refused by the compiler under R4, so a template naming a planned fact
    only in a field slot cannot become a post either way — and treating it as *"the thesis was
    abandoned"* would name the wrong fault for a sentence that is one slot short of correct.
    Passage rows are excluded because a plan's `required_fact_ids` never holds a passage id.

    **The argument is every sentence's raw text, not the templates that survived parsing.** A
    sentence refused for something else is dropped before it reaches here, and reading the
    thesis off the survivors made one unrelated mistake report two findings — the second of
    them false, and pointing a repair at a sentence that was already right.
    """
    named = {rows[match.group(1)].fact_id
             for text in texts
             for match in SLOT_PATTERN.finditer(text)
             if match.group(1) in rows and rows[match.group(1)].kind != PASSAGE_ROW}
    planned = {fact_id for point in plan.key_points for fact_id in point.required_fact_ids}
    if planned and not (named & planned):
        return [DraftViolation(
            THESIS_ABANDONED,
            f"the plan's key points rest on {sorted(planned)} and the templates name "
            f"{sorted(named) or 'no fact at all'}; §12 — the writer may not change the thesis")]
    return []


# -- the answer, as a draft: the pre-3.0.0 parser, kept for what is already on disk --------------
#
# **Nothing on the pipeline path calls `draft_from` or `draft_violations` any more, and neither
# is dead code** (S4). They parse the writer schema as it stood at `WRITER_PROMPT_VERSION`
# 2.2.0 — a sentence with a `fact_bindings` array and a `citations` array — which is the shape of
# all 50 recorded `data/story_demo/*/draft.json` artifacts and of every generation in the
# pre-3.0.0 replay stores. A `Draft` read back from one of those still has to resolve, and the
# §12 rules that judge it are these. Their violation codes stay registered for the same reason:
# `_unresolved_violation`'s rule is that deleting a *reachable* code is worse than keeping one
# that has not fired, and every one of these is reachable from a replayed artifact.
# -- the answer, as a draft --------------------------------------------------------------------


def draft_from(
    content: Mapping[str, Any],
    package: StoryEvidencePackage,
    plan: EditorialPlan,
    *,
    passages: Sequence[PackagedPassage] | None = None,
    derived_facts: Sequence[DerivedFact | EvidenceScopeFact] = (),
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
    # Built once for the whole answer rather than per citation: `facts_by_evidence_handle` is
    # O(facts) and a five-sentence draft cites six or seven times, which is the reason §3.3 made
    # it a method a caller holds rather than a property that rebuilds.
    handles = package.facts_by_evidence_handle()
    violations: list[DraftViolation] = []
    sentences: list[DraftSentence] = []
    for index, row in enumerate(content.get("sentences") or ()):
        sentence, found = _sentence_from(index, row, shown, handles)
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

    found = draft_violations(draft, package, plan, passages=shown,
                             derived_facts=derived_facts)
    if found:
        raise DraftRejected(
            "the writer's draft is refused before verification (§12): "
            + "; ".join(str(violation) for violation in found), found)
    return draft


def _sentence_from(
    index: int,
    row: Mapping[str, Any],
    passages: Sequence[PackagedPassage],
    handles: Mapping[str, PackagedFact],
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

    citations, found = _citations_from(
        where, row.get("citations") or (), passages, handles)
    violations.extend(found)

    if violations:
        return None, violations
    try:
        sentence = DraftSentence(
            index=index,
            text=text,
            kind=SentenceKind(str(row.get("kind") or "")),
            fact_bindings=tuple(bindings),
            # No `calculation`, and there is nowhere left to read one from: the writer's schema
            # holds no such property, so a sentence this module builds carries `None` always.
            # `DraftSentence.calculation` survives on the type for the artifacts already written
            # under prompt version 1.4.0, and §13 refuses a draft that arrives carrying one.
            citations=tuple(citations),
        )
    except (ValidationError, ValueError, TypeError) as exc:
        return None, [DraftViolation(DRAFT_NOT_CONSTRUCTIBLE, f"{where}: {exc}")]
    return sentence, []


def _citations_from(
    where: str,
    declared_citations: Sequence[Mapping[str, Any]],
    passages: Sequence[PackagedPassage],
    handles: Mapping[str, PackagedFact],
) -> tuple[list[PassageCitation], list[DraftViolation]]:
    """The model's `evidence_id`s, resolved to spans the evidence panel can highlight.

    **The model supplies one token per citation and code supplies everything else.** The handle
    names a `PackagedFact`; the fact names its passage and — for the 99.5% of the corpus read out
    of a table — the grid coordinates of the cell its value sits in. `resolve_cell` turns those
    into a span, verified at 2,690 / 2,690 against the live graph. Nothing here searches the
    passage for a string the model wrote, which is the entire difference from the contract this
    replaced.

    **Table and narrative are two paths and only one of them searches.** A fact with a `cell` is
    resolved by coordinate. A fact without one was read out of prose, and its `quoted_text` is a
    whole sentence: all **14 / 14** narrative quotes occur exactly once in their passage
    *(verified live 2026-08-13)*, so the uniqueness requirement is kept for them rather than
    dropped as a formality. If it ever fails it is a defect in the package — the passage the
    package carries no longer holds the sentence the edge quoted — and a defect must refuse.
    """
    citations: list[PassageCitation] = []
    violations: list[DraftViolation] = []
    by_id = {passage.passage_id: passage for passage in passages}
    for declared in declared_citations:
        handle = str(declared.get("evidence_id") or "")
        fact = handles.get(handle)
        if fact is None:
            violations.append(DraftViolation(
                UNRESOLVABLE_EVIDENCE_HANDLE,
                f"{where} cites evidence {handle!r}; this package mints a handle for every fact "
                "it holds and none of them is that one. §3.1 — a handle is derived from the "
                "coordinates a fact already carries, so an id nothing minted names no evidence"))
            continue
        passage = by_id.get(fact.passage_id or "")
        if passage is None:
            # Reachable without the model doing anything wrong: `writer_passages` intersects the
            # facts' passages with the package's four passage sections, so a fact whose passage
            # no section carries is citable-looking and outside the slice. §10.2.1 point 3 —
            # the writer may cite only what it was shown.
            violations.append(DraftViolation(
                UNRESOLVABLE_PASSAGE_ID,
                f"{where} cites evidence {handle!r}, which was read from "
                f"{fact.passage_id!r} — not a passage the writer was shown"))
            continue
        located = _span_for(where, handle, fact, passage)
        if isinstance(located, DraftViolation):
            violations.append(located)
            continue
        start, end = located
        # Rebased to the full `:Passage.text` (§10.2.1 point 2): `PackagedPassage.char_start` is
        # the offset of `text[0]`, and a citation the evidence panel can resolve is an absolute
        # one. Zero for a passage carried whole, which every passage in this slice is.
        citations.append(PassageCitation(
            passage_id=passage.passage_id,
            document_id=passage.document_id,
            char_start=passage.char_start + start,
            char_end=passage.char_start + end,
            evidence_handle=handle,
        ))
    return citations, violations


def _span_for(
    where: str, handle: str, fact: PackagedFact, passage: PackagedPassage
) -> tuple[int, int] | DraftViolation:
    """Where in `passage.text` the evidence behind `fact` sits — by coordinate, or by search.

    Passage-relative; `_citations_from` rebases. One value out, and it is either the span or the
    reason there is none — a pair of optionals would have made "neither" and "both" constructible
    for a question that has exactly one answer.

    **The resolution is `evidence_slice.span_for_handle`'s and the refusal is this stage's.** Core
    answers *"where is it, or why is there nowhere"* in a closed enum; §12's codes and §12's
    prose are minted here, because `core/` may not hold one stage's violation vocabulary and the
    draft compiler will map the same four reasons onto a different one.
    """
    located = span_for_handle(fact, passage)
    if isinstance(located, EvidenceUnresolved):
        return _unresolved_violation(where, handle, located)
    return located.char_start, located.char_end


def _unresolved_violation(
    where: str, handle: str, unresolved: EvidenceUnresolved
) -> DraftViolation:
    """§12's refusal for a handle that names no span: core's reason, this stage's code and prose.

    **All three codes stay registered and stay reachable, and none can any longer be caused by
    anything the model wrote** — the coordinates and the quote are both the package's own.

    * `evidence_handle_out_of_bounds` — the coordinates name no cell, or name an empty one. It
      fires when a package and the passage text it carries disagree: a stored package replayed
      against a re-extracted corpus, or an excerpted passage a fact was read outside of. **Two
      of core's reasons land on this one code**, which is a judgment about what a *draft* may
      say and not a claim that the two are the same event: an off-grid coordinate is a package
      disagreeing with its own passage text about the table's shape, an empty cell is a
      well-formed coordinate naming a spacer column. The outcome is identical — the handle
      locates no span, and `PassageCitation` requires `char_end > char_start` — so a draft has
      one thing to say about both and says it once. A caller that needs them apart reads the
      reason instead of this code.
    * `citation_quote_not_in_passage` — a narrative fact whose passage no longer holds the
      sentence the edge quoted. Also raised from `_citation_violations` for a finished citation
      whose span falls outside the passage text the package holds, which is the path a
      hand-written or replayed draft takes.
    * `citation_quote_ambiguous_in_passage` — a narrative quote naming more than one place. All
      **14 / 14** narrative quotes are whole sentences occurring exactly once *(verified live
      2026-08-13)*, so this does not fire on today's corpus. It is kept rather than deleted
      because it is no longer a statement about a model's typing: it now says *"the package's own
      quote no longer identifies one place in the package's own passage"*, which is a defect in
      the evidence and must refuse. Deleting a reachable code is worse than keeping one that has
      not fired.

    What is gone is the branch that made the old contract unsatisfiable: a **table** fact never
    reaches the quote path, so no citation is ever refused for the ambiguity of a four-character
    cell value. That was 523 of 2,704 observations, and it is now zero.

    Written as four branches naming their constant rather than as a reason → code mapping,
    because `tests/story/test_demo_ui_code_catalogue.py` recovers this stage's vocabulary from
    its own call sites: a code reached through a dict subscript is a code the catalogue's rot
    guard cannot see, and the UI would then describe a refusal nothing appears to raise.
    """
    if unresolved.reason is EvidenceUnresolvedReason.CELL_OUT_OF_BOUNDS:
        return DraftViolation(
            EVIDENCE_HANDLE_OUT_OF_BOUNDS,
            f"{where} cites evidence {handle!r} and {unresolved.detail}. The coordinates are "
            "the package's own, not the model's, so this is the package and the passage text "
            "it carries disagreeing about the table")
    if unresolved.reason is EvidenceUnresolvedReason.CELL_EMPTY:
        return DraftViolation(
            EVIDENCE_HANDLE_OUT_OF_BOUNDS,
            f"{where} cites evidence {handle!r}, {unresolved.detail}")
    if unresolved.reason is EvidenceUnresolvedReason.NARRATIVE_QUOTE_ABSENT:
        return DraftViolation(
            CITATION_QUOTE_NOT_IN_PASSAGE, f"{where} cites {unresolved.detail}")
    if unresolved.reason is EvidenceUnresolvedReason.NARRATIVE_QUOTE_AMBIGUOUS:
        return DraftViolation(
            CITATION_QUOTE_AMBIGUOUS, f"{where} cites {unresolved.detail}")
    # Unreachable today — the enum has four members and each has a branch above, which
    # `test_every_reason_the_resolver_can_give_is_reached_by_a_test_here` pins. It is a
    # `return` and not a `raise` because a fifth reason added in `core/` must still refuse the
    # draft, and refusing it under a registered code beats mislabelling it as the branch that
    # happened to be last.
    return DraftViolation(DRAFT_NOT_CONSTRUCTIBLE,
                          f"{where} cites evidence {handle!r}, which names no span: "
                          f"{unresolved.detail}")


# -- the rendered post, and it comes from the draft ---------------------------------------------


def render_markdown(draft: Draft) -> str:
    """§12: *"a rendered Markdown post may be produced only from the structured draft"*.

    One argument and it is the draft — there is nowhere to pass the model's raw answer, which is
    what makes the rule structural rather than a habit. Every character of prose here is a
    character some `DraftSentence.text` or the `Draft.title` already carried and §13 already
    judged; this function adds punctuation, headings and the two panels, and invents no numeral.

    The evidence panel is rendered because it is the artifact's whole point: §13.17's
    `fact_ledger` is the verifier's copy of the same thing, and a post that hid its citations
    would be a post nobody could check.

    **The Derivations panel is kept and no draft this module builds can now fill it.** It reads
    `DraftSentence.calculation`, and the writer's schema no longer has one
    (DETERMINISTIC_FACT_TOOLS §5) — a derived quantity is an ordinary binding, so it renders
    through the sentence and the Sources panel like every other figure. What still reaches this
    branch is a `Draft` read back from an artifact written under prompt version 1.4.0, which is
    the reason `Calculation` stays on the type at all, and rendering one as it was written is
    better than a panel that silently dropped it.
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
            # The handle is printed beside the span, not instead of it. A reader resolves the
            # span; a *rejection* names the handle (§3.4's codes all carry one), and a panel that
            # printed only offsets would leave nothing in the post to match a refusal against.
            # The falsy branch is now only an empty string: `evidence_handle` is required with
            # no default, so `None` is unsayable. `""` is still constructible and refuses at
            # §3.4 check 1 rather than here, so this prints nothing instead of `[]`.
            handle = f" [{citation.evidence_handle}]" if citation.evidence_handle else ""
            lines.append(f"- sentence {sentence.index}: {citation.passage_id} "
                         f"({citation.document_id}) characters "
                         f"{citation.char_start}-{citation.char_end}{handle}")
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
    derived_facts: Sequence[DerivedFact | EvidenceScopeFact] = (),
    slots: Sequence[SlotRowView] = (),
) -> WrittenStory:
    """§12's whole stage: an accepted plan and its package in, sentence templates out.

    There is no `retriever`, no `terms` and no passage argument: the slice is computed here from
    the package's own fact bindings, which is §10.2.1 point 3 as a signature.

    `derived_facts` **is** an argument, and the asymmetry with `passages` is the point. A passage
    slice is a rule about the package and this stage owns it, so it may not be passed in. A
    derived fact is a quantity a *different stage* computed from a plan this stage was handed,
    and there is nothing in the package to recompute it from — §3 keeps it out of
    `StoryEvidencePackage.facts` so a model's selection stays outside `package_content_digest`.
    Passing it is therefore the only honest shape, and defaulting it to empty means a caller who
    ran no derivation stage writes a post with no derived figure in it rather than one with a
    figure nothing computed.

    **`slots` is an argument for a structural reason and not an editorial one** (S4). The rows
    come from `story.stages.composition.slot_table`, and this stage may not import that one —
    `test_no_stage_imports_another_stage` reads the rule off the module path. `story/pipeline.py`
    is the composition root, builds the table once, and hands the same rows to this call and to
    `compile_draft`, so the prompt prints exactly what the compiler will fill from. The default
    is empty and produces a prompt with no writable slot in it, which is the safe direction: a
    caller that forgot the table gets a refusal, not a post built from handles nothing knows.

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
        prompt=writer_prompt(package, plan, passages, slots=slots,
                             derived_facts=derived_facts, length_target=length_target),
        schema=schema,
        schema_name=WRITER_SCHEMA_NAME,
        max_tokens=max_tokens,
        temperature=PINNED_TEMPERATURE,
    )

    # `plan_story`'s block, for its reason: everything below judges an answer that already
    # exists, so a refusal raised here refuses a generation the run paid for and must record.
    # A fault raised inside `provider.generate` never reaches here and carries nothing.
    try:
        violations = tuple(schema_violations(result.content, schema))
        if violations:
            # Never retried: the server was asked for schema-constrained output and answered,
            # and re-asking at temperature 0 returns the same thing while charging twice.
            raise StoryProviderSchemaError(
                f"the writer's answer does not satisfy schema {WRITER_SCHEMA_NAME!r}: "
                + "; ".join(violations), violations)

        templates = templates_from(result.content, package, plan, slots=slots)
    except (DraftRejected, StoryProviderSchemaError) as exc:
        exc.generation = result
        raise
    # The five identity fields `draft_from` used to stamp — `candidate_id`, `package_id`,
    # `prompt_version`, `model_id`, `style_profile_id` — are `compile_draft`'s to write now,
    # because it is the function that builds the `Draft`. Two of them name *this* stage's prompt
    # and profile, which is why they are arguments there rather than values that module could
    # read: a compiler that knew the writer's prompt version would be a compiler importing the
    # writer.
    return WrittenStory(templates=templates, generation=result,
                        title=str(result.content.get("title") or ""))


__all__ = [
    "BINDING_RENDERING_AMBIGUOUS",
    "BINDING_RENDERING_NOT_IN_TEXT",
    "CITATION_QUOTE_AMBIGUOUS",
    "CITATION_QUOTE_NOT_IN_PASSAGE",
    "DRAFT_NOT_CONSTRUCTIBLE",
    "EVIDENCE_HANDLE_OUT_OF_BOUNDS",
    "MALFORMED_SLOT",
    "NO_SENTENCES",
    "PASSAGE_ROW",
    "PLAN_NAMES_ANOTHER_PACKAGE",
    "RESTS_ON_NOT_A_PASSAGE_HANDLE",
    "RESTS_ON_WITHOUT_EXPLANATORY_SENTENCE",
    "SLOT_PATTERN",
    "THESIS_ABANDONED",
    "UNRESOLVABLE_EVIDENCE_HANDLE",
    "UNRESOLVABLE_FACT_ID",
    "UNRESOLVABLE_PASSAGE_ID",
    "DraftRejected",
    "DraftViolation",
    "SentenceTemplate",
    "WrittenStory",
    "draft_from",
    "draft_violations",
    "render_markdown",
    "templates_from",
    "write_story",
    "writer_passages",
]
