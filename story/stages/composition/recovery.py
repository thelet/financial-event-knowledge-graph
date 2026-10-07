"""Plain prose in, slot templates out — by matching only strings a row already offers.

Responsibility: turn the model's sentences into `SentenceTemplate`s without changing one
character of what the model wrote. Find the literals that *are* a row's offered string, replace
each with the slot that resolves to exactly that string, derive `kind` from the slots, and hand
the compiler the result. Nothing here refuses, nothing here renders a figure, and nothing here
reads a plan, a passage or a package.

**Why this exists.** Writer contract 4.0.0 makes the slot grammar an offer rather than an
obligation — *"write `{{F1}}` where the figure goes, or write the figure exactly as the row
prints it"* — and this is the half that honours the second clause. Measured against
`data/story_demo/story-v1-1daff167348f/`'s own recorded answer, which the run refused as
`thesis_abandoned`: all three sentences recover, compile to text byte-identical to the model's
prose, and verify with zero findings at any severity. A refused run becomes an accepted post
with no prompt change, no model change and no rule relaxed
(`docs/2026-08-26-story-pipeline-stabilization-plan/03-…md` §3).

**Text-preserving by construction, which is the property everything else rests on.** The only
strings matched are the ones `SlotRow.offers` holds, and those are exactly the strings
`compile._substitute` will insert. So substituting the produced template reproduces the input
byte for byte, and `_template_from`'s standing contract — *"every character outside a slot is
the model's own prose and reaches the reader"* — survives a pass that rewrites the sentence.

The narrow rule costs a near-miss spelling, and the alternative was measured and rejected.
Matching the wider `legal_renderings` set would recover `$556.0 million`; it would also recover
`556000000.0 USD`, which `legal_renderings(F1)` really does offer *(verified 2026-08-26 against
this run's package)*, and the compiler would then publish *"Adjusted Gross Profit of
556000000.0 USD"* — prose nobody wrote, passing verification. A near miss refuses instead, as a
repairable structural failure whose feedback names the exact string to use.

**It never refuses** (R7). Where two rows could claim one literal, or one row's value occurs
twice in a sentence, the literal is left alone and an `AmbiguousLiteral` is recorded; §13 then
refuses it as `unbound_numeral` with an exact span. A second stage deciding *which fact a
numeral is* would be a second authority over §13.1's question.

**Imports.** `story.core.*`, this stage's own two modules, and the standard library. The
substring scan is `story.core.evidence_slice.occurrences`, which was written and tested for
exactly this shape of search.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from story.core.evidence_slice import occurrences
from story.core.models import SentenceKind
from story.stages.composition.compile import SLOT_PATTERN
from story.stages.composition.public import (
    VALUE_CLAIMED_BY_TWO_ROWS,
    VALUE_OCCURS_TWICE,
    AmbiguousLiteral,
    NormalizedDraft,
    RecoveredSlot,
    SentenceTemplate,
    SlotKind,
    SlotRow,
)
from story.stages.composition.slot_table import VALUE_FIELD

#: Characters that make a match part of something longer rather than a standalone literal. `$`
#: and `%` because a figure's own punctuation is what separates `13.2` from `13.2%`; `_` because
#: an identifier is one word. `.` is handled separately — see `_attached`.
_ATTACHING = frozenset("_$%")


@dataclass(frozen=True, slots=True)
class _Span:
    """One accepted substitution, before the template is assembled."""

    char_start: int
    char_end: int
    handle: str
    field: str
    #: Other `(handle, field)` pairs offering the identical string for this identical span.
    rivals: tuple[str, ...] = ()

    @property
    def slot(self) -> str:
        return f"{{{{{self.handle}}}}}" if self.field == VALUE_FIELD else (
            f"{{{{{self.handle}.{self.field}}}}}")


def normalize_templates(
    texts: Sequence[str], rows: Sequence[SlotRow]
) -> NormalizedDraft:
    """The writer's bare sentences as compilable templates, with the audit trail.

    The one entry point `story/pipeline.py` calls. Indexes are positional `0..n-1` — `Draft`
    requires them in order, and a model that numbered its own sentences would eventually skip
    one and make every §13 finding unaddressable.

    `rests_on` is `()` on every template this produces, so every recovered sentence is
    `reported`, `calculated` or `connective`. That is not a limit of this function: `03` §2
    measured `want_explanatory_search` off in every detector, so no package in the corpus holds
    an explanatory passage for a sentence to rest on. `derive_kind` takes `rests_on` anyway, so
    the route needs no change here when the search is turned on.

    **Raises nothing for ordinary content.** A sentence with unreadable braces is returned with
    its braces intact and refused by `compile_draft` as `template_not_compilable`, which is the
    one authority on the slot grammar.
    """
    templates: list[SentenceTemplate] = []
    recovered: list[RecoveredSlot] = []
    ambiguous: list[AmbiguousLiteral] = []
    hand_written: list[int] = []
    for index, text in enumerate(texts):
        one = recover_sentence(index, text, rows)
        templates.append(one.template)
        recovered.extend(one.recovered)
        ambiguous.extend(one.ambiguous)
        if one.hand_written:
            hand_written.append(index)
    return NormalizedDraft(
        templates=tuple(templates),
        recovered=tuple(recovered),
        ambiguous=tuple(ambiguous),
        hand_written=tuple(hand_written),
    )


@dataclass(frozen=True, slots=True)
class RecoveredSentence:
    """One sentence's outcome: the template, and what code did and did not claim in it.

    Returned by `recover_sentence` for a caller that works a sentence at a time; the collections
    are what `normalize_templates` concatenates. `hand_written` is the third state — neither
    recovered nor ambiguous — and it exists because *"recovery changed nothing"* has two causes
    and a reader of `composition.json` has to be able to tell them apart.
    """

    template: SentenceTemplate
    recovered: tuple[RecoveredSlot, ...] = ()
    ambiguous: tuple[AmbiguousLiteral, ...] = ()
    #: The sentence already carried `{{`, so it was passed through untouched.
    hand_written: bool = False


def recover_sentence(
    index: int, text: str, rows: Sequence[SlotRow], *, rests_on: Sequence[str] = ()
) -> RecoveredSentence:
    """One sentence: anchor on values, then fields of the anchored rows, then emit.

    **A sentence already carrying `{{` is returned untouched**, and recovery never mixes modes.
    A half-slotted sentence would let one pass insert a slot the model's own numeral then
    duplicates — *"{{F1}} of $556 million"* — and the compiler has no way to know which of the
    two figures a reader is meant to check. The model wrote a template or it wrote prose.

    **The two passes are ordered because a value is what a binding is *of*.** Only a value slot
    mints a `FactBinding` (`compile._bindings_from`), and §13.1 reads a period and a metric
    surface off a binding — so a field slot on a row this sentence does not bind is
    `slot_without_binding` at compile time. Searching for field strings from rows the sentence
    never anchored would manufacture exactly that refusal out of a coincidence of wording.

    A sentence with no anchor is not an error. It keeps its prose, becomes `connective`, and any
    numeral in it is `unbound_numeral` — which is §13's answer, arrived at with an exact span.
    """
    if "{{" in text:
        return RecoveredSentence(
            template=SentenceTemplate(index=index, text=text,
                                      kind=derive_kind(text, rows, rests_on=rests_on),
                                      rests_on=tuple(rests_on)),
            hand_written=True)

    anchors, ambiguous = _anchors(index, text, rows)
    spans = sorted(anchors + _fields(text, anchors, rows), key=lambda one: one.char_start)
    template = _emit(text, spans)
    return RecoveredSentence(
        template=SentenceTemplate(index=index, text=template,
                                  kind=derive_kind(template, rows, rests_on=rests_on),
                                  rests_on=tuple(rests_on)),
        recovered=tuple(
            RecoveredSlot(sentence_index=index, handle=one.handle, field=one.field,
                          literal=text[one.char_start:one.char_end],
                          char_start=one.char_start, char_end=one.char_end,
                          also_offered_by=one.rivals)
            for one in spans),
        ambiguous=ambiguous)


def derive_kind(
    text: str, rows: Sequence[SlotRow], *, rests_on: Sequence[str] = ()
) -> SentenceKind:
    """What a template's slots say the sentence is. Never a model's label.

        any value slot naming a derived row   → calculated
        else any value slot                   → reported
        else rests_on present                 → explanatory
        else                                  → connective

    **Deriving this is strictly stronger than letting a model write it**, measured combination by
    combination against the verifier (`03` §4). Three of the labels a model could write become
    unrepresentable — `calculated` with no derived slot, `connective` carrying a claim,
    `calculated` with no slot at all — and each of the three earns a REFUSE today.

    It also closes a live gap. **A sentence binding a derived fact and labelled `reported`
    verifies clean today**, because `reported_sentence_carries_calculation` fires on
    `sentence.calculation is not None` and the 3.0.0 compiler never builds a `Calculation` —
    `grep -rn "Calculation" story/stages/composition/` returns nothing *(verified 2026-08-26)*.
    So the check that should have caught the wrong label has nothing to fire on, and the label
    reaching the reader was the model's. Under this rule the sentence is `calculated`.

    A sentence naming **both** an observed and a derived value slot is `calculated`, and the
    shape is not refused here. Such a sentence is separately fragile — the observed row's period
    and the derived binding's `to_period` can disagree in one sentence and earn
    `period_named_in_text_contradicts_binding` — and withholding a compile to force that refusal
    is the compiler acting as a verifier, which `_citations_for`'s own docstring names as
    forbidden.

    **A `{{P1}}` does not count as a value slot.** A passage row offers no value at all, so the
    slot is `field_not_offered_by_row` whatever kind is derived; calling it `reported` would
    replace a truthful refusal with two, one of them saying the sentence claimed a figure it
    never claimed.
    """
    by_handle = {row.handle: row for row in rows}
    values = [by_handle.get(match.group(1))
              for match in SLOT_PATTERN.finditer(text) if not match.group(2)]
    named = [row for row in values if row is None or row.kind is not SlotKind.PASSAGE]
    if any(row is not None and row.kind is SlotKind.DERIVED for row in named):
        return SentenceKind.CALCULATED
    if named:
        return SentenceKind.REPORTED
    if rests_on:
        return SentenceKind.EXPLANATORY
    return SentenceKind.CONNECTIVE


# -- the anchor pass ---------------------------------------------------------------------------


def _anchors(
    index: int, text: str, rows: Sequence[SlotRow]
) -> tuple[list[_Span], tuple[AmbiguousLiteral, ...]]:
    """Every row whose value string stands alone in this sentence, exactly once, uniquely.

    Three outcomes and only the first binds:

    * one standalone occurrence, claimed by one row — an anchor.
    * one row's value occurring twice — `value_occurs_twice`. The *row* is unambiguous and the
      span is not, and a `FactBinding` declares a span. Binding the first would be choosing
      which of two numerals the reader is meant to check.
    * one span two rows could claim — `value_claimed_by_two_rows`. Two facts rendering alike is
      an ordinary shape (a metric flat across two quarters), and which one the sentence is about
      is §13.1's question, answered from the sentence's period and metric surfaces. Guessing
      here would answer it with a coin toss and hand the verifier a binding to confirm.

    Overlap and not just equality, because a row offering `$1.0 billion` and one offering
    `1.0 billion` claim different spans of the same digits, and a template cannot carry both.
    """
    found: list[_Span] = []
    for row in rows:
        if row.kind is SlotKind.PASSAGE:
            continue
        value = row.offers.get(VALUE_FIELD)
        if not value:
            continue
        found.extend(
            _Span(char_start=start, char_end=start + len(value),
                  handle=row.handle, field=VALUE_FIELD)
            for start in occurrences(text, value)
            if _standalone(text, start, start + len(value)))

    ambiguous: list[AmbiguousLiteral] = []
    repeated = {one.handle for one in found
                if sum(other.handle == one.handle for other in found) > 1}
    # One record per occurrence and not one per row, because each unbound literal is a numeral
    # §13.1 will refuse on its own span and the artifact is what a reader holds beside the
    # finding. The row is named on every one of them.
    ambiguous.extend(
        AmbiguousLiteral(sentence_index=index, code=VALUE_OCCURS_TWICE,
                         literal=text[one.char_start:one.char_end],
                         char_start=one.char_start, char_end=one.char_end,
                         handles=(one.handle,))
        for one in found if one.handle in repeated)
    found = [one for one in found if one.handle not in repeated]

    anchors: list[_Span] = []
    for group in _clusters(found):
        if len(group) == 1:
            anchors.append(group[0])
            continue
        ambiguous.append(AmbiguousLiteral(
            sentence_index=index, code=VALUE_CLAIMED_BY_TWO_ROWS,
            literal=text[min(one.char_start for one in group):
                         max(one.char_end for one in group)],
            char_start=min(one.char_start for one in group),
            char_end=max(one.char_end for one in group),
            handles=tuple(sorted({one.handle for one in group}))))
    return anchors, tuple(sorted(ambiguous, key=lambda one: (one.char_start, one.code)))


def _clusters(spans: Sequence[_Span]) -> list[list[_Span]]:
    """Spans grouped by transitive overlap — a group of one is an anchor, more is a clash.

    Transitive and not pairwise, because three rows can chain across one run of characters and a
    clash a caller reports twice is a clash a reader has to reconcile.
    """
    groups: list[list[_Span]] = []
    for span in sorted(spans, key=lambda one: (one.char_start, one.char_end, one.handle)):
        touching = [group for group in groups
                    if any(_overlaps(span, other) for other in group)]
        merged = [span]
        for group in touching:
            merged.extend(group)
            groups.remove(group)
        groups.append(merged)
    return groups


# -- the field pass ----------------------------------------------------------------------------


def _fields(
    text: str, anchors: Sequence[_Span], rows: Sequence[SlotRow]
) -> list[_Span]:
    """Metric, period and direction surfaces — for the anchored rows only, longest match first.

    Longest first because the surfaces nest: a row offering `the third quarter of 2022` and one
    offering `2022` both match inside the same run of characters, and taking the shorter would
    leave a bare year the verifier reads as an `unbound_numeral` sitting beside a slot.

    **Where two anchored rows offer the identical string for the identical span, either may be
    taken.** A field slot mints no `FactBinding` — `compile._bindings_from` iterates value slots
    only — so the compiled draft is byte-identical whichever wins, and the surfaces the bindings
    declare are read off the rows rather than off the text. The pick is `(handle, field)`-sorted
    so two builds of one run agree, and the rivals are recorded on the fill rather than dropped.

    A field string occurring twice is not an ambiguity for the same reason: both occurrences
    become the same slot, the text is unchanged, and no binding is at stake.
    """
    by_handle = {row.handle: row for row in rows}
    taken = list(anchors)
    candidates: list[_Span] = []
    for anchor in anchors:
        row = by_handle[anchor.handle]
        for field, surface in row.offers.items():
            if field == VALUE_FIELD or not surface:
                continue
            candidates.extend(
                _Span(char_start=start, char_end=start + len(surface),
                      handle=row.handle, field=field)
                for start in occurrences(text, surface)
                if _standalone(text, start, start + len(surface)))

    accepted: list[_Span] = []
    ordered = sorted(candidates, key=lambda one: (
        one.char_start - one.char_end, one.char_start, one.handle, one.field))
    for candidate in ordered:
        if any(_overlaps(candidate, other) for other in taken):
            continue
        rivals = tuple(sorted(
            f"{{{{{one.handle}.{one.field}}}}}" for one in ordered
            if one is not candidate
            and (one.char_start, one.char_end) == (candidate.char_start, candidate.char_end)))
        chosen = _Span(char_start=candidate.char_start, char_end=candidate.char_end,
                       handle=candidate.handle, field=candidate.field, rivals=rivals)
        accepted.append(chosen)
        taken.append(chosen)
    return accepted


# -- emission and the standalone rule ----------------------------------------------------------


def _emit(text: str, spans: Sequence[_Span]) -> str:
    """The template: every accepted span replaced by its slot, every other character copied."""
    out: list[str] = []
    cursor = 0
    for span in spans:
        out.append(text[cursor:span.char_start])
        out.append(span.slot)
        cursor = span.char_end
    out.append(text[cursor:])
    return "".join(out)


def _overlaps(one: _Span, other: _Span) -> bool:
    return one.char_start < other.char_end and other.char_start < one.char_end


def _standalone(text: str, start: int, end: int) -> bool:
    """Is this match the whole literal, or part of something longer?

    The guard is what keeps `$556 million` from being recovered out of `$1,556 million` and
    `13.2` out of `113.2%`. Without it a row's offered string could be claimed from the middle
    of a different number, and the compiler would then substitute a slot that renders the same
    characters back — text-preserving, and a lie about which figure the sentence states.
    """
    return not _attached(text, start - 1, -1) and not _attached(text, end, +1)


def _attached(text: str, at: int, direction: int) -> bool:
    """Does the character at `at` continue the match, reading outward in `direction`?

    **A full stop attaches only when a digit sits on its far side**, and that exception is not a
    softening — it is the difference between a decimal point and the end of a sentence. Every
    period surface this corpus offers ends in a year, so `…in the third quarter of 2022.` is the
    ordinary case: guarding `.` unconditionally would refuse to recover the period slot in the
    plan's own worked example, while allowing it unconditionally would recover `$556 million`
    out of `$556 million.5`. `$556.0 million` still refuses, which is the trade-off `03` §3
    states and keeps.
    """
    if at < 0 or at >= len(text):
        return False
    char = text[at]
    if char.isalnum() or char in _ATTACHING:
        return True
    beyond = at + direction
    return char == "." and 0 <= beyond < len(text) and text[beyond].isalnum()


__all__ = [
    "NormalizedDraft",
    "RecoveredSentence",
    "derive_kind",
    "normalize_templates",
    "recover_sentence",
]
