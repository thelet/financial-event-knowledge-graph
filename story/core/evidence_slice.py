"""The passages a package's facts were read from, and the span one fact's evidence names.

Responsibility: two pure questions about a `StoryEvidencePackage` and the passages it carries —
*"which passages back the facts?"* and *"where in this passage is the evidence behind this
fact?"* — answered with no provider, no plan, no violation vocabulary and no I/O.

**Boundaries.** `story.core.*` and the standard library only, which is what `core/` means here
(`tests/story/test_story_package_structure.py::test_core_never_imports_a_stage_a_provider_or_the_cli`
holds it). Nothing in this module refuses anything: `span_for_handle` returns a typed *answer*,
either the span or the reason there is none, and the caller decides what a missing span costs
it. That is the whole reason the resolution lives here rather than in the writer — a `core`
module that returned a `DraftViolation` would put one stage's refusal codes inside the layer
every stage imports, and the draft compiler (`docs/2026-08-23-deterministic-draft-compiler/`
§4.5) needs the same resolution under a different vocabulary.

These two functions were `writer.writer_passages` and `writer._span_for` and are unchanged in
behaviour; the writer keeps thin adapters over them, so §12's codes and §12's messages are still
the writer's own.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from story.core.models import PackagedFact, PackagedPassage, StoryEvidencePackage
from story.core.table_cells import CellOutOfBounds, resolve_cell


# -- the writer's universe, derived by code ---------------------------------------------------


def passages_backing_facts(package: StoryEvidencePackage) -> tuple[PackagedPassage, ...]:
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


# -- the handle → span resolution -------------------------------------------------------------


class EvidenceUnresolvedReason(Enum):
    """Why a fact's evidence names no span. Closed, because each member costs a caller something
    different to say and collapsing them would hand the caller a string to parse.

    The two cell members are separate here and land on one code in the writer
    (`evidence_handle_out_of_bounds`), which is the writer's judgment about what a *draft* may
    say and not a statement that the two are the same event: an off-grid coordinate is a package
    disagreeing with its own passage text about the table's shape, an empty cell is a well-formed
    coordinate naming a spacer column. A caller that wants to tell them apart now can.
    """

    #: `table_cells.CellOutOfBounds` — the coordinates name no cell in the passage's grid.
    CELL_OUT_OF_BOUNDS = "cell_out_of_bounds"
    #: A well-formed coordinate holding nothing, of which these tables have many.
    CELL_EMPTY = "cell_empty"
    #: A fact with no cell, whose passage no longer contains the package's own quote.
    NARRATIVE_QUOTE_ABSENT = "narrative_quote_absent"
    #: A fact with no cell, whose quote names more than one place in its own passage.
    NARRATIVE_QUOTE_AMBIGUOUS = "narrative_quote_ambiguous"


@dataclass(frozen=True, slots=True)
class ResolvedEvidence:
    """Where the evidence behind one fact sits, **relative to `passage.text`**.

    Passage-relative and not document-relative: `PackagedPassage.char_start` is the offset the
    caller adds to reach `:Passage.text`, and a resolver that added it here would be answering a
    question about the artifact rather than about the passage it was handed.
    """

    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class EvidenceUnresolved:
    """No span, and why. `reason` dispatches; `detail` is prose naming the coordinates or quote.

    `detail` completes a caller's own sentence about the citation — the writer prefixes *"S1
    cites evidence 'ev:…'"* — so it names the passage, the cell or the quote and never the
    caller's context, which core does not have.
    """

    reason: EvidenceUnresolvedReason
    detail: str


def span_for_handle(
    fact: PackagedFact, passage: PackagedPassage
) -> ResolvedEvidence | EvidenceUnresolved:
    """Where in `passage.text` the evidence behind `fact` sits — by coordinate, or by search.

    One value out, and it is either the span or the reason there is none — a pair of optionals
    would have made "neither" and "both" constructible for a question that has exactly one
    answer.

    **Table and narrative are two paths and only one of them searches.** A fact with a `cell` is
    resolved by coordinate, verified at 2,690 / 2,690 against the live graph. A fact without one
    was read out of prose, and its `quoted_text` is a whole sentence: all **14 / 14** narrative
    quotes occur exactly once in their passage *(verified live 2026-08-13)*, so the uniqueness
    requirement is kept for them rather than dropped as a formality. If it ever fails it is a
    defect in the package — the passage the package carries no longer holds the sentence the edge
    quoted — and the caller is told so rather than handed one of the occurrences.

    The caller is trusted to pass the passage the fact names; this function does not compare
    `fact.passage_id` to `passage.passage_id`, because *"is this fact's passage in the slice I
    was shown?"* is a question about a slice and belongs to whoever owns one.
    """
    if fact.cell is not None:
        try:
            cell = resolve_cell(passage.text,
                                row_index=fact.cell.row_index,
                                column_index=fact.cell.value_column_index)
        except CellOutOfBounds as off_grid:
            return EvidenceUnresolved(EvidenceUnresolvedReason.CELL_OUT_OF_BOUNDS, str(off_grid))
        if cell.char_end <= cell.char_start:
            # A well-formed coordinate holding nothing — a spacer column, of which these tables
            # have many. `quoted_text` is non-empty on 2,704 / 2,704 evidence edges, so a fact
            # resolving to an empty cell means its coordinates do not name the value it was read
            # from, and there is no span to cite: `PassageCitation` requires
            # `char_end > char_start`.
            return EvidenceUnresolved(
                EvidenceUnresolvedReason.CELL_EMPTY,
                f"whose cell ({fact.cell.row_index}, {fact.cell.value_column_index}) of "
                f"{passage.passage_id} is empty; there is no span to cite")
        return ResolvedEvidence(cell.char_start, cell.char_end)

    quote = fact.quoted_text or ""
    found = occurrences(passage.text, quote)
    if len(found) != 1:
        return _quote_unresolved(passage.passage_id, quote, found)
    return ResolvedEvidence(found[0], found[0] + len(quote))


def _quote_unresolved(
    passage_id: str, quote: str, found: tuple[int, ...]
) -> EvidenceUnresolved:
    """The two narrative outcomes, and neither can be caused by anything a model wrote.

    `quote` here is the *package's* own `EVIDENCED_BY.quoted_text` for a fact with no `cell` —
    the 14 narrative observations, 0.5% of the corpus — located in the passage the package
    carries beside it.

    * absent — zero occurrences: the passage the package carries no longer holds the sentence
      the edge quoted.
    * ambiguous — more than one. All **14 / 14** narrative quotes are whole sentences occurring
      exactly once *(verified live 2026-08-13)*, so this does not fire on today's corpus. It is
      kept rather than deleted because it is not a statement about a model's typing: it says
      *"the package's own quote no longer identifies one place in the package's own passage"*,
      which is a defect in the evidence and must refuse. Deleting a reachable outcome is worse
      than keeping one that has not fired.

    A **table** fact never reaches here, which is what made the old retyped-quote contract
    unsatisfiable for 523 of 2,704 observations and is now zero.
    """
    if not found:
        return EvidenceUnresolved(
            EvidenceUnresolvedReason.NARRATIVE_QUOTE_ABSENT,
            f"narrative evidence from {passage_id}, whose text no longer contains the package's "
            f"own quote {quote!r}")
    return EvidenceUnresolved(
        EvidenceUnresolvedReason.NARRATIVE_QUOTE_AMBIGUOUS,
        f"narrative evidence from {passage_id}, where the package's own quote {quote!r} occurs "
        f"{len(found)} times; a citation that cannot say which occurrence resolves to no span, "
        "and this fact carries no cell coordinate to resolve it by")


def occurrences(haystack: str, needle: str) -> tuple[int, ...]:
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
