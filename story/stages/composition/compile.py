"""Sentence templates in, a `Draft` out — every numeral inserted by code and every span written.

Responsibility: answer *"what does this template mean"* for one set of sentences, and nothing
else. Parse the two slot forms, substitute each from a trusted row while accumulating the offset
so the span of every substitution in the **final** text is known exactly, mint one `FactBinding`
per value slot, resolve the citations the sentence's kind calls for, and either return the draft
or refuse with typed violations.

**Why the span is written and not found** (R2, and §5.3's *"the two places where care is
required"*). §13.1's four numeral checks are not tautologies today: `rendered` is evidence
because it must be a literal substring of the model's own prose. A compiler that chose a
rendering *without* putting it into the text would make all four vacuous and leave the prose
numeral unchecked entirely — `_covering_spans` builds coverage from `(rendered, char_start,
char_end)`, so a supplied span licenses whatever bytes it points at. This module inserts the
string and records the span it inserted into, so `text[char_start:char_end] == rendered` holds
because one operation produced both. The residual risk narrows from *"a 9B model retyped a
number"* to *"the compiler has a bug"*, which is what
`test_no_compiled_draft_can_violate_the_numeral_or_surface_checks` exists to hold.

**What this module must not do** (R7). No direction check against the prose, no comparison-order
check, no causal, superlative, period-grammar or metric-grounding check, and no re-derivation of
a derived result. Those are §13's, and a second verifier that can disagree with the first is
worse than none. `story/stages/composition/` may not import `story/stages/verification/`, and
`tests/story/test_story_package_structure.py::test_no_stage_imports_another_stage` holds it from
the module path alone.

**Imports.** `story.core.*` and the standard library. Nothing here reaches a provider, a prompt,
a plan's semantics or the graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping, Sequence

from pydantic import ValidationError

from story.core.evidence_slice import (
    EvidenceUnresolved,
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
    PackagedFact,
    PackagedPassage,
    PassageCitation,
    SentenceKind,
    StoryEvidencePackage,
)
from story.stages.composition.public import (
    FIELD_NOT_OFFERED_BY_ROW,
    NO_EVIDENCE_HANDLE_FOR_BOUND_FACT,
    NO_LEGAL_RENDERING,
    PASSAGE_HANDLE_UNKNOWN,
    RESTS_ON_WITHOUT_EXPLANATORY_KIND,
    SLOT_WITHOUT_BINDING,
    TEMPLATE_NOT_COMPILABLE,
    UNKNOWN_SLOT_FIELD,
    UNKNOWN_SLOT_HANDLE,
    CompiledDraft,
    CompositionRefused,
    CompositionViolation,
    SentenceTemplate,
    SlotFill,
    SlotKind,
    SlotRow,
)
from story.stages.composition.slot_table import SLOT_FIELDS, VALUE_FIELD, slot_table

#: `{{H}}` and `{{H.field}}`, and there is no third form. No nesting, no defaults, no escaping —
#: two shapes a 9B model can be shown under every row of its prompt, and everything else refused.
#: The handle shape is deliberately narrow (`[A-Z][0-9]+`) so that a brace the grammar cannot
#: read is `template_not_compilable` rather than a near-miss silently copied into the post.
SLOT_PATTERN = re.compile(r"\{\{\s*([A-Z][0-9]+)(?:\.([a-z_]+))?\s*\}\}")

#: The braces no compiled sentence may still contain. Checked against the **literal** stretches
#: of a template — the text between slots — because that is where an unclosed or misspelled slot
#: survives the substitution pass.
_UNREADABLE_BRACES = ("{{", "}}")


@dataclass(frozen=True, slots=True)
class _Filled:
    """One resolved slot, with the span it occupies in the sentence being built."""

    handle: str
    field: str
    inserted: str
    char_start: int
    char_end: int
    row: SlotRow


def compile_draft(
    templates: Sequence[SentenceTemplate],
    package: StoryEvidencePackage,
    plan: EditorialPlan,
    *,
    derived_facts: Sequence[DerivedFact | EvidenceScopeFact] = (),
    passages: Sequence[PackagedPassage] | None = None,
    title: str = "",
    model_id: str = "",
    style_profile_id: str = "",
    prompt_version: str = "",
) -> CompiledDraft:
    """The templates as a `Draft`, or `CompositionRefused` carrying every typed violation.

    **Every violation in the set is reported, not the first.** A caller repairing a refusal
    wants the whole list, which is `draft_from`'s arrangement and the reason
    `CompositionRefused.codes` exists.

    `plan` is accepted and read by no clause here, and that is deliberate rather than an
    oversight. §4.3 of the architecture document fixes this signature and the pipeline calls it
    with the plan it already holds; what the plan constrains — the thesis the draft may not
    abandon, the package it was made from — is checked where the model's answer is parsed, and
    everything else it holds is editorial. `story/stages/derivation/offers.validate` carries the
    same unused `candidate` argument for the same reason, and records it in the same way: a
    contract stated in a signature outlives a promise stated in prose.

    `model_id`, `style_profile_id` and `prompt_version` identify what wrote the sentences and
    are the caller's, since they name the *generation* stage's prompt and profile and this stage
    may not import it. `candidate_id` and `package_id` are the package's own — a draft that
    could name a different package could not be re-keyed to the run that built it.

    **`title` is prose and carries no slot**, which is a rule and not an omission. `claims.py`
    gives the title no bindings, no calculation and no citation, so *every* numeral in it is
    refused outright — *"a number nothing can bind is a number nothing checked"* — and a slot
    there would insert exactly such a numeral. A title carrying `{{` is `template_not_compilable`
    rather than silently substituted or silently passed through with the braces still in it.
    """
    shown = tuple(passages_backing_facts(package) if passages is None else passages)
    rows = slot_table(package, derived_facts, shown)
    by_handle = {row.handle: row for row in rows}
    facts_by_handle = package.facts_by_evidence_handle()
    passages_by_id = {passage.passage_id: passage for passage in shown}

    violations: list[CompositionViolation] = []
    sentences: list[DraftSentence] = []
    fills: list[SlotFill] = []
    # Every evidence handle a **completed earlier** sentence cited, which is what an
    # `explanatory` sentence's citation walks past. Not a draft-wide set of everything cited:
    # R5 forbids carrying a citation forward, and this is the one place the compiler needs to
    # know what came before.
    already_cited: set[str] = set()

    for template in templates:
        where = f"sentences[{template.index}]"
        text, filled, found = _substitute(where, template, by_handle)
        violations.extend(found)
        bindings, found = _bindings_from(where, filled)
        violations.extend(found)
        citations, found = _citations_for(
            where, template, filled, by_handle, facts_by_handle, passages_by_id, already_cited)
        violations.extend(found)
        if violations:
            # Keep scanning the remaining templates so the refusal names every fault, and stop
            # building sentences: a `Draft` requires indexes `0..n-1` in order, so a set with a
            # hole in it is not constructible and would replace the real refusal with a
            # validator's.
            continue
        try:
            sentences.append(DraftSentence(
                index=template.index,
                text=text,
                kind=template.kind,
                fact_bindings=tuple(bindings),
                citations=tuple(citations),
            ))
        except (ValidationError, ValueError, TypeError) as exc:
            violations.append(CompositionViolation(
                TEMPLATE_NOT_COMPILABLE, f"{where}: {exc}"))
            continue
        fills.extend(SlotFill(
            sentence_index=template.index, handle=one.handle, field=one.field,
            inserted=one.inserted, char_start=one.char_start, char_end=one.char_end)
            for one in filled)
        already_cited.update(citation.evidence_handle for citation in citations)

    if any(brace in title for brace in _UNREADABLE_BRACES) or SLOT_PATTERN.search(title):
        violations.append(CompositionViolation(
            TEMPLATE_NOT_COMPILABLE,
            f"the title {title!r} carries a slot; §13.15 gives a title no binding and refuses "
            "every numeral in it, so a slot there writes a number nothing can check"))

    if violations:
        raise CompositionRefused(
            "the draft compiler refuses these templates: "
            + "; ".join(str(violation) for violation in violations), violations)

    try:
        draft = Draft(
            candidate_id=package.candidate_id,
            package_id=package.package_id,
            title=title,
            sentences=tuple(sentences),
            style_profile_id=style_profile_id,
            prompt_version=prompt_version,
            model_id=model_id,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise CompositionRefused(
            f"the compiled draft cannot be constructed: {exc}",
            (CompositionViolation(TEMPLATE_NOT_COMPILABLE, str(exc)),)) from exc
    return CompiledDraft(draft=draft, slots=tuple(fills))


# -- substitution ------------------------------------------------------------------------------


def _substitute(
    where: str, template: SentenceTemplate, by_handle: Mapping[str, SlotRow]
) -> tuple[str, list[_Filled], list[CompositionViolation]]:
    """One left-to-right pass: the finished text, the span of every insertion, the refusals.

    **The offset is accumulated, never searched for.** A sentence naming one row twice —
    *"…from {{F3}} … a {{F3}} that …"* — inserts the same string twice, and the two spans are
    unambiguous because the loop knows where it wrote each one. Locating them afterwards is
    exactly what `writer._rendering_violation` has to refuse as ambiguous, and it is the failure
    mode this construction removes rather than detects. `FactBinding` and `DraftSentence` place
    no constraint on binding the same fact twice, so two bindings with two spans is the answer
    here and not a refusal *(checked against both validators, 2026-08-23)*.

    Offsets are in code points, which is what every `char_start` in this repository already
    means: `text[binding.char_start:binding.char_end]` is how §13.1 reads a span back, and
    Python slices by code point. A sentence carrying an em dash or an accented name shifts every
    later span by exactly one per character, which the accumulation gets right for free and a
    byte-oriented count would not.
    """
    out: list[str] = []
    filled: list[_Filled] = []
    violations: list[CompositionViolation] = []
    literals: list[str] = []
    # Handles a **value** slot names, whether or not it resolved. R4 is a rule about what the
    # template says, so a value slot that was itself refused must not also produce a spurious
    # `slot_without_binding` for the field slots beside it.
    value_handles: set[str] = set()
    field_slots: list[tuple[str, str]] = []
    cursor = 0
    length = 0

    for match in SLOT_PATTERN.finditer(template.text):
        literal = template.text[cursor:match.start()]
        literals.append(literal)
        out.append(literal)
        length += len(literal)
        cursor = match.end()
        handle, field = match.group(1), match.group(2) or VALUE_FIELD
        if field == VALUE_FIELD:
            value_handles.add(handle)
        else:
            field_slots.append((handle, field))
        row = by_handle.get(handle)
        if row is None:
            violations.append(CompositionViolation(
                UNKNOWN_SLOT_HANDLE,
                f"{where} names {match.group(0)}, and the slot table has no row {handle!r}; "
                "handles are assigned positionally from the package's facts, this run's derived "
                "facts and the writer's passage slice"))
            continue
        if field != VALUE_FIELD and field not in SLOT_FIELDS:
            violations.append(CompositionViolation(
                UNKNOWN_SLOT_FIELD,
                f"{where} names {match.group(0)}, and {field!r} is not a slot field for any "
                f"row; the grammar knows {', '.join(sorted(SLOT_FIELDS))}"))
            continue
        value = row.offers.get(field)
        if value is None:
            violations.append(_not_offered(where, match.group(0), field, row))
            continue
        out.append(value)
        filled.append(_Filled(handle=handle, field=field, inserted=value,
                              char_start=length, char_end=length + len(value), row=row))
        length += len(value)

    tail = template.text[cursor:]
    literals.append(tail)
    out.append(tail)
    text = "".join(out)

    residue = "".join(literals)
    if any(brace in residue for brace in _UNREADABLE_BRACES):
        violations.append(CompositionViolation(
            TEMPLATE_NOT_COMPILABLE,
            f"{where} carries braces the slot grammar cannot read: {template.text!r}. A slot is "
            "{{H}} or {{H.field}} with H matching [A-Z][0-9]+, and there is no third form, no "
            "nesting and no escape"))
    violations.extend(
        CompositionViolation(
            SLOT_WITHOUT_BINDING,
            f"{where} names {{{{{handle}.{field}}}}} and does not bind {{{{{handle}}}}} in the "
            "same sentence. §13.1 reads a period or metric surface off a *binding*, so a "
            "sentence that names a row without binding its value earns no coverage and its "
            "numerals are `unbound_numeral`")
        for handle, field in field_slots if handle not in value_handles)
    return text, filled, violations


def _not_offered(
    where: str, slot: str, field: str, row: SlotRow
) -> CompositionViolation:
    """R3's refusal, with the two causes told apart.

    A **value** slot on a fact row the emitter has no numeral for is `no_legal_rendering`, which
    is the diagnosable case: a `crossed_zero` or `trend_direction` row answers a word, and §13.2
    refuses a numeral written for one. Everything else — a field the row has no legal value for,
    and the value slot of a passage row, which has no value at all — is
    `field_not_offered_by_row`, naming what the row *does* offer so the fault is repairable
    without reading this module.
    """
    if field == VALUE_FIELD and row.kind is not SlotKind.PASSAGE:
        return CompositionViolation(
            NO_LEGAL_RENDERING,
            f"{where} names {slot}, and {row.handle} ({row.fact_id}) has no legal rendering: "
            "its value is a word rather than a quantity, and §13.2 refuses a numeral written "
            "for one")
    return CompositionViolation(
        FIELD_NOT_OFFERED_BY_ROW,
        f"{where} names {slot}, and {row.handle} ({row.fact_id}) offers "
        + (f"{', '.join(sorted(_offered_names(row)))}" if row.offers else "no slot at all"))


def _offered_names(row: SlotRow) -> list[str]:
    """The slots a row does offer, spelled as a template would write them."""
    return [f"{{{{{row.handle}}}}}" if field == VALUE_FIELD else f"{{{{{row.handle}.{field}}}}}"
            for field in row.offers]


# -- bindings ----------------------------------------------------------------------------------


def _bindings_from(
    where: str, filled: Sequence[_Filled]
) -> tuple[list[FactBinding], list[CompositionViolation]]:
    """One `FactBinding` per value slot, with the surfaces read off the row rather than typed.

    **Which surface a derived binding declares is the verifier's question and was answered by
    reading it** *(2026-08-23)*.

    * `deterministic._derived_metric_findings` accepts a surface resolving to **either**
      `derived.metric_id` or `derived.from_metric_id`, because R2 permits two metrics only under
      a `DIVERGENCE` claim. Both would pass; the row's `to_metric` is declared, because that is
      the metric the claim is *about* and a binding that declared the other one would say the
      gap is a reading of the metric it was measured *from*.
    * `deterministic._derived_period_findings` resolves the declared surface against
      `index.fact(derived.to_fact_id)`'s own window, so `to_period` is not a preference here but
      the only surface that check admits.

    A row that offers neither surface is refused rather than bound with an empty field. That is
    stricter than §13 needs in one case — a derived row whose `to` metric is ambiguous but whose
    `from` metric is not could legally declare the `from` surface — and the strictness is
    deliberate: a row whose subject metric cannot be named unambiguously is a row no sentence can
    ground in prose either (`metric_surface_absent_from_text` reads the same alias index), so
    binding it would move the refusal one stage later without making the claim writable.
    """
    bindings: list[FactBinding] = []
    violations: list[CompositionViolation] = []
    for one in filled:
        if one.field != VALUE_FIELD or one.row.kind is SlotKind.PASSAGE:
            continue
        metric = one.row.offers.get("metric") or one.row.offers.get("to_metric")
        period = one.row.offers.get("period") or one.row.offers.get("to_period")
        missing = [name for name, value in (("metric", metric), ("period", period))
                   if not value]
        if missing:
            violations.append(CompositionViolation(
                FIELD_NOT_OFFERED_BY_ROW,
                f"{where} binds {{{{{one.handle}}}}} ({one.row.fact_id}), and the row offers no "
                f"{' and no '.join(missing)} surface for the binding to declare; §13.4 and "
                "§13.5 read those two fields off every binding"))
            continue
        bindings.append(FactBinding(
            fact_id=one.row.fact_id,
            rendered=one.inserted,
            char_start=one.char_start,
            char_end=one.char_end,
            metric_surface=metric,
            period_surface=period,
        ))
    return bindings, violations


# -- citations ---------------------------------------------------------------------------------


def _citations_for(
    where: str,
    template: SentenceTemplate,
    filled: Sequence[_Filled],
    by_handle: Mapping[str, SlotRow],
    facts_by_handle: Mapping[str, PackagedFact],
    passages_by_id: Mapping[str, PackagedPassage],
    already_cited: set[str],
) -> tuple[list[PassageCitation], list[CompositionViolation]]:
    """The citations this sentence's kind calls for, minted per bound row and never carried on.

    | kind | citations |
    | --- | --- |
    | `reported`, `calculated` | every bound row's own evidence |
    | `explanatory` | that, plus one per `rests_on` passage |
    | `connective` | none |

    A row's *own evidence* is `SlotRow.evidence_handles`: an observation's single handle, and —
    for a derived row — the handles of the two observations it was computed from. A passage's is
    the first fact read from it that no earlier sentence in this draft has cited.

    **`reported` and `calculated` are one branch and not two, which departs from the slot spec's
    table.** The spec separates them — observations for the first, a derived fact's inputs for
    the second — and `SlotRow.evidence_handles` already answers *"what does this row rest on"*
    per kind of **row**, so keying on the kind of **sentence** as well would give a `reported`
    sentence that binds a derived fact no citation at all, and §13.7's `uncited_factual_sentence`
    fires on exactly that (`deterministic.py:1972`, read 2026-08-23). Withholding a citation to
    force a §13 refusal is the compiler acting as a verifier, which R7 forbids. Whether that
    sentence should have been `calculated` is §13.9's question and stays there.

    **Deduplicated on the handle within a sentence**, so a sentence binding a derived fact and
    one of its inputs does not cite that cell twice. R5 is the other half: nothing here reads a
    previous sentence's citations, so the *"same span, second claim"* shape that earned 15
    blocking findings in the corpus is not constructible.
    """
    if template.kind is SentenceKind.CONNECTIVE:
        if template.rests_on:
            return [], [_rests_on_violation(where, template)]
        return [], []
    if template.rests_on and template.kind is not SentenceKind.EXPLANATORY:
        return [], [_rests_on_violation(where, template)]

    wanted: list[tuple[str, str]] = []  # (evidence handle, what asked for it)
    for one in filled:
        if one.field != VALUE_FIELD or one.row.kind is SlotKind.PASSAGE:
            continue
        if not one.row.evidence_handles:
            return [], [CompositionViolation(
                NO_EVIDENCE_HANDLE_FOR_BOUND_FACT,
                f"{where} binds {{{{{one.handle}}}}} ({one.row.fact_id}), for which this package "
                "minted no evidence handle. R6 — an omitted citation lands as nothing where a "
                "refusal would have landed as `uncited_factual_sentence`")]
        wanted.extend((handle, f"{{{{{one.handle}}}}}") for handle in one.row.evidence_handles)

    violations: list[CompositionViolation] = []
    for passage_handle in template.rests_on:
        row = by_handle.get(passage_handle)
        if row is None or row.kind is not SlotKind.PASSAGE:
            violations.append(CompositionViolation(
                PASSAGE_HANDLE_UNKNOWN,
                f"{where} rests on {passage_handle!r}, which is not a passage handle in this "
                "run's slot table"))
            continue
        chosen = _unused_handle(row, already_cited)
        if chosen is None:
            violations.append(CompositionViolation(
                NO_EVIDENCE_HANDLE_FOR_BOUND_FACT,
                f"{where} rests on {passage_handle} ({row.fact_id}), and no fact read from that "
                "passage carries an evidence handle, so there is nothing to cite"))
            continue
        wanted.append((chosen, f"rests_on {passage_handle}"))
    if violations:
        return [], violations

    citations: list[PassageCitation] = []
    seen: set[str] = set()
    for handle, asked_by in wanted:
        if handle in seen:
            continue
        seen.add(handle)
        citation, violation = _citation_for(where, handle, asked_by,
                                            facts_by_handle, passages_by_id)
        if violation is not None:
            violations.append(violation)
        else:
            citations.append(citation)
    return citations, violations


def _rests_on_violation(
    where: str, template: SentenceTemplate
) -> CompositionViolation:
    return CompositionViolation(
        RESTS_ON_WITHOUT_EXPLANATORY_KIND,
        f"{where} is {template.kind.value} and rests on "
        f"{', '.join(template.rests_on)}; only an explanatory sentence cites a passage for what "
        "it paraphrases — every other kind cites the evidence behind the facts it binds")


def _unused_handle(row: SlotRow, already_cited: set[str]) -> str | None:
    """The first handle of a passage no earlier sentence has cited, or the first one anyway.

    **The fallback is a known disagreement with §13.7, kept deliberately, and this is the
    record** *(measured 2026-08-26)*. `citations._reuse_findings` refuses the second use of an
    identical `(passage_id, char_start, char_end)` by a sentence that binds no fact that passage
    evidences — and an `explanatory` sentence binds no fact **by construction**, so for the only
    kind of sentence that reaches this function the predicate is vacuous and the refusal is
    certain rather than judged. The proof is order-dependence: `reported` then `explanatory` over
    one passage fails, `explanatory` then `reported` passes, on the same two sentences.

    **Why it is not repaired here.** Three ways were measured and each is blocked:

    * an invented passage-scoped handle earns `unresolvable_evidence_handle`, because the
      verifier resolves handles through `package.facts_by_evidence_handle()`;
    * keeping the cell handle and widening the span to the whole passage earns
      `evidence_cell_span_mismatch`;
    * refusing here rather than emitting the citation was implemented, and reverted: it makes the
      run end at the compiler where it used to end at the verifier with a nameable finding, which
      is the *"gates ending runs before authoritative verification"* problem this work exists to
      reduce. Twenty-one recorded drafts in `test_story_composition_regression.py` take that
      shape.

    The expressive fix is to record `rests_on` on `DraftSentence` so a paraphrase-rest is visible
    to §13.7 as something other than a bound fact's cell — additive, but it re-keys
    `draft_content_sha256` and needs a verifier change, and **no package in the corpus carries an
    explanatory passage at all** (`want_explanatory_search` is off in every detector), so there is
    no live candidate to test it against. Deferred with its evidence rather than guessed at.

    Unreachable on the current path regardless: `normalize_templates` authors `rests_on` and
    authors it empty, so no template reaching the compiler names a passage.
    """
    for handle in row.evidence_handles:
        if handle not in already_cited:
            return handle
    return row.evidence_handles[0] if row.evidence_handles else None


def _citation_for(
    where: str,
    handle: str,
    asked_by: str,
    facts_by_handle: Mapping[str, PackagedFact],
    passages_by_id: Mapping[str, PackagedPassage],
) -> tuple[PassageCitation, None] | tuple[None, CompositionViolation]:
    """One handle resolved to a span the evidence panel can highlight.

    **Three failures, one code, and the causes are in the prose.** The handle names no fact of
    this package, the fact's passage is outside the writer's slice, or the coordinates resolve
    to nothing — `writer._unresolved_violation` collapses the same family for the same reason:
    the outcome is identical, a citation with no span, and `PassageCitation` requires
    `char_end > char_start`. None of the three can be caused by anything a model wrote, because
    the handle came off the row and the coordinates off the package.
    """
    fact = facts_by_handle.get(handle)
    if fact is None:
        return None, CompositionViolation(
            NO_EVIDENCE_HANDLE_FOR_BOUND_FACT,
            f"{where} cites {handle!r} for {asked_by}; this package minted a handle for every "
            "fact it holds and none of them is that one")
    passage = passages_by_id.get(fact.passage_id or "")
    if passage is None:
        return None, CompositionViolation(
            NO_EVIDENCE_HANDLE_FOR_BOUND_FACT,
            f"{where} cites {handle!r} for {asked_by}, which was read from "
            f"{fact.passage_id!r} — not a passage in this run's slice")
    located = span_for_handle(fact, passage)
    if isinstance(located, EvidenceUnresolved):
        return None, CompositionViolation(
            NO_EVIDENCE_HANDLE_FOR_BOUND_FACT,
            f"{where} cites {handle!r} for {asked_by}, {located.detail}")
    # Rebased to the full `:Passage.text` (§10.2.1 point 2), exactly as `writer._citations_from`
    # does: `PackagedPassage.char_start` is the offset of `text[0]`, and a citation the evidence
    # panel can resolve is an absolute one. Zero for a passage carried whole.
    return PassageCitation(
        passage_id=passage.passage_id,
        document_id=passage.document_id,
        char_start=passage.char_start + located.char_start,
        char_end=passage.char_start + located.char_end,
        evidence_handle=handle,
    ), None


__all__ = ["SLOT_PATTERN", "compile_draft"]
