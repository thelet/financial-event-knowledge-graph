"""The contracts the draft compiler is spoken to in: what a template is, what a row offers.

Responsibility: the vocabulary of one stage — the sentence a model wrote before any slot is
filled, the trusted row a slot resolves against, the provenance of each substitution, the record
of what `recovery` rewrote and what it declined to, and the closed set of reasons a template may
be refused. No parsing, no substitution, no I/O.

**Two vocabularies, and the file keeps them apart.** The nine `*_slot_*` / `*_row` constants are
*refusals*: a template carrying one produces no draft. The two under *recovery diagnostics* are
not — recovery never refuses, and a diagnostic there is a note explaining why a numeral reached
the verifier unbound.

**Why the codes live beside the types.** A refusal a caller has to parse out of prose is not
dispatchable, which is the reason `story/stages/generation/writer.py` names its own; and every
code here is named for the **template's** failure rather than for a §13 code, because these
fire before a verifier ever sees a draft and conflating the two vocabularies would make a
repair loop ask the wrong stage to fix it.

**Imports.** `story.core.models` and the standard library, and nothing else — this stage may
import `story.core.*` only, which
`tests/story/test_story_package_structure.py::test_no_stage_imports_another_stage` enforces
from the module path alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from story.core.models import Draft, SentenceKind

# -- refusal codes ----------------------------------------------------------------------------
#
# Nine, closed, and each one is a statement about the template rather than about the sentence
# it would have produced. R6 of the architecture document is the reason there is no tenth
# outcome: where the compiler cannot produce a value it refuses, because an omission lands as
# *nothing* where a refusal would have landed as `uncited_factual_sentence`.

#: `{{F9}}` where the table stops at `F2`. The handle is well formed and names no row.
UNKNOWN_SLOT_HANDLE = "unknown_slot_handle"
#: `{{F1.colour}}` — a field name outside the closed vocabulary `slot_table.SLOT_FIELDS` holds.
#: The field does not exist for *any* row, which is a different fault from a row not having it.
UNKNOWN_SLOT_FIELD = "unknown_slot_field"
#: `{{F1.direction}}`, `{{D1.period}}` on a two-period row, `{{P2}}` — the field is one the
#: vocabulary knows and this row offers no legal value for. R3: a slot a row does not offer
#: cannot be filled, and the prompt's *"do not write about this fact"* stops being advice.
#:
#: **The value slot of a passage row lands here and not on `no_legal_rendering`**, because a
#: passage has no value to render and `no_legal_rendering` is a statement about a figure.
FIELD_NOT_OFFERED_BY_ROW = "field_not_offered_by_row"
#: `{{F3.period}}` in a sentence with no `{{F3}}`. R4 — the verifier reads period and metric
#: surfaces **off bindings**, so a sentence naming a row without binding it earns no coverage
#: and its year is `unbound_numeral`. The refusal moves to compile time, where it names the
#: rule instead of pointing at a digit.
SLOT_WITHOUT_BINDING = "slot_without_binding"
#: `{{D1}}` where `D1` is a `crossed_zero` or `trend_direction` row: the value is a word and
#: §13.2 refuses a numeral written for one (`derived_unit_mismatch`). The row can be talked
#: about and cannot be counted.
NO_LEGAL_RENDERING = "no_legal_rendering"
#: A bound fact whose evidence cannot be cited: no handle was minted for it, its passage is
#: outside the writer's slice, or its handle resolves to no span. **Three causes, one code**, on
#: `writer._unresolved_violation`'s judgment — the outcome is identical and a draft has one
#: thing to say about it — with the cause named in `detail`. Never a silent omission: R6.
NO_EVIDENCE_HANDLE_FOR_BOUND_FACT = "no_evidence_handle_for_bound_fact"
#: `rests_on: ["P7"]` where the table stops at `P1`.
PASSAGE_HANDLE_UNKNOWN = "passage_handle_unknown"
#: `rests_on` on a sentence that is not `explanatory`. The compiler resolves citations by kind
#: and only that kind rests on a passage; a `reported` sentence naming one is asking for a
#: citation the kind cannot carry.
RESTS_ON_WITHOUT_EXPLANATORY_KIND = "rests_on_without_explanatory_kind"
#: A `{{` that never closes, or a handle that does not match `[A-Z][0-9]+`. There is no third
#: slot form and no escape: a brace the grammar cannot read is refused rather than left in the
#: text, and `compile_draft` asserts no compiled sentence carries one.
TEMPLATE_NOT_COMPILABLE = "template_not_compilable"


class SlotKind(str, Enum):
    """What a row is, which decides what it can be asked for and how it is cited.

    A `str` enum for `story.core.models`' reason: the value is what an artifact carries, and a
    provenance record a reader has to map back through an integer is one nobody reads.
    """

    #: A `PackagedFact` — an observation read from a filing, with an evidence handle of its own.
    OBSERVED = "observed"
    #: A `DerivedFact` — a quantity code computed. **No handle is ever minted for one**; a
    #: sentence stating it cites the two observations it was computed from.
    DERIVED = "derived"
    #: A `PackagedPassage`. Named only in `rests_on`, never in sentence text.
    PASSAGE = "passage"


@dataclass(frozen=True, slots=True)
class SentenceTemplate:
    """One sentence as the model wrote it, before any slot is filled.

    Three properties and no fourth, which is §4.2 of the architecture document: the placeholders
    already say which facts the sentence uses, and a second `facts_used` array would be a field
    that can disagree with them.

    `index` is positional and is not the model's — `Draft` requires `0..n-1` in order, and a
    model that numbered its own sentences would eventually skip one and make every §13 finding
    unaddressable. The caller that parses the model's answer assigns it.

    **Only `text` is the model's now.** Writer contract 4.0.0 narrowed the schema to
    `{title, sentences[].text}`, so `index`, `kind` and `rests_on` are all code-authored and
    `recovery.normalize_templates` is what authors them. The plan's §4 measurement is the
    argument: three of the four `kind` labels a model could write were wrong in a way the
    verifier either refused or — for a derived-bearing sentence labelled `reported` — missed
    entirely, and a field a model cannot get right is a field code should own.
    """

    index: int
    #: Carries `{{H}}` and `{{H.field}}` placeholders. Everything outside them is the model's
    #: own prose and is copied through untouched. The one field the model still writes — either
    #: with slots, or as plain prose that `recovery` turns into slots.
    text: str
    #: Derived from the slots and `rests_on` by `recovery.derive_kind`, never stated by a model.
    kind: SentenceKind
    #: Passage handles, `explanatory` only. Required-and-possibly-empty because
    #: `story/providers/portable_schema.py` forbids optional properties.
    #:
    #: **`()` throughout this phase**, and the field stays for when it is not. `03` §2 measured
    #: `EvidenceRequest.want_explanatory_search` off in every detector, so
    #: `package.explanatory_passages` is empty in every run that exists and no sentence can
    #: honestly rest on a passage. The type keeps the field so the route returns by turning the
    #: search on — and by fixing §7's citation disagreement first.
    rests_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SlotRow:
    """One bindable row and every string a template may ask it for.

    **`offers` omits a field the row has no legal value for; it never carries an empty one**,
    and that is what makes R3 a refusal rather than a check. A fact with no unambiguous metric
    surface, or a window §13.4's closed grammar has no form for, simply has no such key, and a
    template naming it is refused by code that never has to decide whether `""` meant *"none"*
    or *"the model left it blank"*.

    The value slot is keyed on the empty string, because `{{F3}}` names no field: it asks the
    row for the one thing it *is*.
    """

    handle: str
    #: The real, deterministic id — `obs:…`, `fact:derived:…`, or a `passage_id` for a passage
    #: row. Handles exist so a 9B model never retypes one of these into a sentence.
    fact_id: str
    kind: SlotKind
    offers: Mapping[str, str]
    #: The evidence a sentence resting on this row may cite, in the order the compiler emits
    #: it. One handle for an observation; for a derived row **the handles of its two inputs**,
    #: because §6 mints none for a derivation; for a passage row, the handles of the facts read
    #: from it, in package order. Empty where the package minted no handle at all — which is a
    #: refusal at compile time (`NO_EVIDENCE_HANDLE_FOR_BOUND_FACT`) and never a quiet omission.
    evidence_handles: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SlotFill:
    """Which slot became which span of which compiled sentence.

    Written as a separate artifact (`composition.json`) and deliberately **not** added to
    `DraftSentence`: `Draft.digestible_payload()` feeds `draft_content_sha256`, and a new field
    on the type would re-key every artifact already on disk for a value nothing verifies.
    """

    sentence_index: int
    handle: str
    #: `""` for a value slot, matching the key `SlotRow.offers` holds it under.
    field: str
    inserted: str
    char_start: int
    char_end: int


# -- recovery diagnostics ----------------------------------------------------------------------
#
# **Not refusal codes**, and the separation is the safety property of the whole pass. Recovery
# never refuses: where it cannot say which row a literal belongs to it leaves the literal alone
# and records one of these, and the verifier then refuses the sentence as `unbound_numeral` with
# an exact span. A second stage that could refuse a numeral would be a second authority over the
# same question, which is R7 in the one place it would be easiest to break.

#: One span is the value of two different rows — `$110 million` where two facts render alike.
#: Binding either would be a guess about which fact the sentence is about.
VALUE_CLAIMED_BY_TWO_ROWS = "value_claimed_by_two_rows"
#: One row's value string occurs twice in one sentence. The row is unambiguous and the *span* is
#: not, and a binding declares a span.
VALUE_OCCURS_TWICE = "value_occurs_twice"


@dataclass(frozen=True, slots=True)
class RecoveredSlot:
    """One literal the model wrote that code turned into a slot, and where it stood.

    **The span is into the model's original sentence, not into the template and not into the
    compiled draft.** `SlotFill` already records where the string landed in the finished text;
    the audit question this record answers is the other one — *"which characters of the model's
    own answer did code claim?"* — and a reader holding `composition.json` beside the recorded
    generation wants to highlight them there.

    `also_offered_by` is the deterministic tie, recorded rather than hidden: where two anchored
    rows offer the identical string for the identical span, the pick is `(handle, field)`-sorted
    and the rivals are named here. It is only ever populated for a **field** slot, because a
    value-slot tie is an ambiguity and refuses to bind at all — and a field slot mints no
    `FactBinding` (`compile._bindings_from` iterates value slots only), so the compiled draft is
    byte-identical whichever rival wins.
    """

    sentence_index: int
    handle: str
    #: `""` for a value slot, matching the key `SlotRow.offers` holds it under.
    field: str
    #: The model's own characters, which are also exactly what the compiler will insert.
    literal: str
    char_start: int
    char_end: int
    also_offered_by: tuple[str, ...] = ()

    def as_json(self) -> dict[str, object]:
        """The row as `story/pipeline.py` writes it into `composition.json`."""
        return {
            "sentence_index": self.sentence_index,
            "handle": self.handle,
            "field": self.field,
            "literal": self.literal,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "also_offered_by": list(self.also_offered_by),
        }


@dataclass(frozen=True, slots=True)
class AmbiguousLiteral:
    """A literal recovery declined to bind, with the rows that could have claimed it.

    A diagnostic and never a violation — see the note above `VALUE_CLAIMED_BY_TWO_ROWS`. The
    literal is left in the prose exactly as written, which is what makes the outcome a §13
    refusal naming the digits rather than a compile-time refusal naming a rule.
    """

    sentence_index: int
    #: One of the two codes above.
    code: str
    literal: str
    char_start: int
    char_end: int
    #: Every row that offered this string as its value, sorted. One handle for
    #: `value_occurs_twice`, two or more for `value_claimed_by_two_rows`.
    handles: tuple[str, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "sentence_index": self.sentence_index,
            "code": self.code,
            "literal": self.literal,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "handles": list(self.handles),
        }


@dataclass(frozen=True, slots=True)
class NormalizedDraft:
    """What the compiler is handed, and the record of how much of it code wrote.

    The seam between the generation stage and this one. `story/stages/generation/` may not
    import `story/stages/composition/`, so the writer returns bare strings and every structural
    field — the index, the slots, the kind — is authored here.

    **Four collections and not one nested tree**, for `pipeline._composition_payload`'s reason:
    a reader diffing two runs asks *which literal moved*, and a tree makes that a walk. Each one
    answers a different question, and a sentence that appears in none of the last three is a
    sentence recovery found nothing to do with — which is not an error and is the ordinary fate
    of a connective.
    """

    templates: tuple[SentenceTemplate, ...]
    #: Every literal that became a slot, in sentence then span order.
    recovered: tuple[RecoveredSlot, ...] = ()
    #: Every literal that could have been a slot and was left alone. A diagnostic, not a refusal.
    ambiguous: tuple[AmbiguousLiteral, ...] = ()
    #: Indexes of sentences that already carried `{{` and were passed through untouched.
    #: Recovery never mixes modes, and *"recovery did nothing here"* has two causes a reader of
    #: the artifact has to be able to tell apart.
    hand_written: tuple[int, ...] = ()

    def as_json(self) -> dict[str, object]:
        """The diagnostics block, for `composition.json`.

        The templates are **not** in it: `pipeline._composition_payload` already renders them
        beside the fills, and one artifact carrying the same sentence twice under two keys is a
        pair that can disagree.
        """
        return {
            "recovered": [one.as_json() for one in self.recovered],
            "ambiguous": [one.as_json() for one in self.ambiguous],
            "hand_written": list(self.hand_written),
        }


@dataclass(frozen=True, slots=True)
class CompositionViolation:
    """One reason a template set may not become a draft. `code` dispatches, `detail` explains."""

    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


class CompositionRefused(RuntimeError):
    """The templates were well formed JSON and the compiler refuses them anyway.

    **Not a verification result and not a provider error**, for `DraftRejected`'s two reasons:
    nothing is wrong with the transport, and a `VerifiedDraft` records a decision about a draft
    that exists, while this is the answer from which no draft can be built at all.

    Raised rather than returned, so a caller cannot reach a `CompiledDraft` field that was never
    filled. R6 — the compiler fails closed and loudly.
    """

    def __init__(self, message: str, violations: Sequence[CompositionViolation] = ()) -> None:
        super().__init__(message)
        self.violations = tuple(violations)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(violation.code for violation in self.violations)


@dataclass(frozen=True, slots=True)
class CompiledDraft:
    """The draft the verifier receives, and the record of how each of its numerals got there.

    `draft` is the same `Draft` the writer used to return — no field added, none removed — which
    is the property that makes this change affordable: every §13 check keeps its subject and
    every artifact already on disk still loads.
    """

    draft: Draft
    slots: tuple[SlotFill, ...] = ()


__all__ = [
    "FIELD_NOT_OFFERED_BY_ROW",
    "NO_EVIDENCE_HANDLE_FOR_BOUND_FACT",
    "NO_LEGAL_RENDERING",
    "PASSAGE_HANDLE_UNKNOWN",
    "RESTS_ON_WITHOUT_EXPLANATORY_KIND",
    "SLOT_WITHOUT_BINDING",
    "TEMPLATE_NOT_COMPILABLE",
    "UNKNOWN_SLOT_FIELD",
    "UNKNOWN_SLOT_HANDLE",
    "VALUE_CLAIMED_BY_TWO_ROWS",
    "VALUE_OCCURS_TWICE",
    "AmbiguousLiteral",
    "CompiledDraft",
    "CompositionRefused",
    "CompositionViolation",
    "NormalizedDraft",
    "RecoveredSlot",
    "SentenceTemplate",
    "SlotFill",
    "SlotKind",
    "SlotRow",
]
