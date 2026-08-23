"""The contracts the draft compiler is spoken to in: what a template is, what a row offers.

Responsibility: the vocabulary of one stage — the sentence a model wrote before any slot is
filled, the trusted row a slot resolves against, the provenance of each substitution, and the
closed set of reasons a template may be refused. No parsing, no substitution, no I/O.

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
    "CompiledDraft",
    "CompositionRefused",
    "CompositionViolation",
    "SentenceTemplate",
    "SlotFill",
    "SlotKind",
    "SlotRow",
]
