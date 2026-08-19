"""§13.7's three citation rules, because the evidence is three different things.

Responsibility: deciding whether a citation *supports* the fact a sentence bound to it. Rule A
is positional reconstruction over a table cell, Rule B is span containment plus lexical
grounding over filed prose, and Rule C is a refusal until a lane emits an `:EvidenceSource`.
The identity, number, period and metric checks live in `deterministic.py`; this module assumes
they ran and asks only the citation question.

**Why Rule A is not entailment.** Measured over the run: 2,690 table observations carry a
`quoted_text` of median **4 characters**, all bare numerals, none carrying `%` or `$`. A
4-character quote `"2.2"` entails nothing at all — what it supports *exactly* is the value it
reconstructs to. So support means `reconstruct_table_quote(quoted_text, scale, unit) == value`,
which holds on **2,690/2,690** and is asserted from a test rather than trusted from this
docstring.

**Why step 4 says "uniquely, within this passage".** §13.7.1, measured: **179 of 485
`(passage_id, column_label)` pairs map to more than one `period_key`, covering 1,656 of 2,704
observations (61.3%)**, and 523 `quoted_text` strings occur more than once inside their own
passage. The concrete failure is the first row of `observations.jsonl` —
`adjusted_ebitda_margin` under `column_label: "2020"`, period `2020-01-01_2020-06-30`, in a
passage that also carries `2020-04-01_2020-06-30` under the same label. Every one of the
draft's original five steps passes while the sentence names the quarter and the value is the
half-year. **61.3% is not a reason to weaken the check; it is the measurement that says a bare
year-column citation is not evidence of a period.**

**A table-backed sentence may not paraphrase the passage** (§13.7): it may state the number,
the metric, the period and the subject and nothing else. That is why lexical grounding runs on
Rule B and on `explanatory` sentences, and not on Rule A — there is nothing in a 4-character
quote to be grounded against.

**The handle rules (TABLE_CELL_CITATIONS §3.4), and what they replace.** Every citation carries
the `PackagedFact.evidence_handle` the package minted — the field is required, see
`PassageCitation` — and `_handle_findings` asks the six questions §3.4 lists plus one it does
not, `evidence_cell_span_mismatch` (see `codes.py` notes 9 through 11). `_coverage_findings` asks
an eighth that §3.4 also does not: *"is every fact this sentence binds named by one of its
citations"*, which check 7 leaves open because any handle for any bound fact satisfies it for
every bound fact. The one that carries the load is check 7, *"the handle is the one this package
minted for this fact"*, and the hole it closes was **total on table evidence**: §13.7's older
test is `citation_does_not_support_fact`, *"the passage each bound fact was read from"*, and
every one of the corpus's **144** table-backed passages evidences more than one observation —
2,690 of 2,690, up to 70 in a single passage *(verified live 2026-08-18)*. A sentence binding one
cell and citing the cell beside it passed that test for the whole corpus.

**Rule A step 1 is not made redundant by check 3, and Rule B is no longer a check on the
model.** Both are argued where they are implemented — `_rule_a` and `_rule_b` — because both
changed meaning when the model stopped choosing spans, and a reader who finds only one of the
two arguments would conclude the wrong thing about the other.

**DETERMINISTIC_FACT_TOOLS §6 changes what *"the facts this sentence binds"* means, and nothing
else here.** A sentence may now bind a `DerivedFact`, and *"a citation supporting a derived fact
is one that supports an **input fact** of it"*. So every rule below that reasoned over
`bound_ids` reasons over `_evidence_fact_ids` instead: the observations the sentence bound
directly, plus the observations its derived facts were computed from. The derived id itself is
never in that set and **no evidence handle is ever minted for one** — `DerivedFact` has no field
to hold one, §6 forbids it, and five layers downstream assume a cited thing was read from a
filing. A reader who wants the evidence for `-$446M` follows it to `$556M` and `$110M` and cites
the two cells there, which is exactly what the coverage rule now requires of the draft.

§7's `EvidenceScopeFact` goes the other way: it was read from nothing, so **no** citation can
support it, and a `PassageCitation` on a sentence binding one is refused outright. That is the
failure §7 names — the *"no explanation was disclosed"* sentence reusing a financial-table
citation as though the table had said it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from story.core.models import (
    DraftSentence,
    EvidenceSourceCitation,
    FactBinding,
    PackagedFact,
    PackagedPassage,
    PassageCitation,
    SentenceKind,
    VerificationFinding,
)
from story.core.numerals import (
    UnreconstructableQuote,
    compare_to_fact,
    reconstruct_table_quote,
    tokenize_numerals,
)
from story.core.table_cells import CellOutOfBounds, resolve_cell, resolve_header
import story.stages.verification.language as language
from story.stages.verification.codes import finding
from story.stages.verification.metric_surfaces import MetricAliasIndex
from story.stages.verification.package_index import PackageIndex


@dataclass(frozen=True, slots=True)
class CitationUse:
    """One citation as one sentence used it, for the reuse rule.

    Keyed on the span and not on the passage alone: a table citation's span is the region the
    value was read from, and two sentences pointing at the identical region for facts that
    passage does not evidence is the laundering §13.7 names.
    """

    passage_id: str
    char_start: int
    char_end: int
    sentence_index: int
    fact_ids: tuple[str, ...]

    @property
    def span_key(self) -> tuple[str, int, int]:
        return (self.passage_id, self.char_start, self.char_end)


def citation_id(citation: PassageCitation | EvidenceSourceCitation) -> str:
    """A readable handle for a finding's `citation_ids`, never a positional index.

    §13.17 wants a rejection to be actionable without re-deriving it, and *"citation 2"* is not
    a thing anyone can look up.
    """
    if isinstance(citation, EvidenceSourceCitation):
        return citation.evidence_source_id
    return f"{citation.passage_id}@{citation.char_start}-{citation.char_end}"


def cited_span(passage: PackagedPassage, citation: PassageCitation) -> str | None:
    """The characters the citation points at, or `None` when it points outside the passage.

    `PackagedPassage.char_start` is the offset of `text[0]` into the **full** `:Passage.text`
    and explanatory and counter-evidence passages are excerpted to a ±400-character window
    (§10.2.1), so a citation's absolute offsets have to be rebased before they can be sliced. A
    citation whose span falls in the part of the passage the budget cut is *not* resolvable,
    and returning the excerpt's text anyway would let a span nobody can see license a claim.
    """
    start = citation.char_start - passage.char_start
    end = citation.char_end - passage.char_start
    if start < 0 or end > len(passage.text) or end <= start:
        return None
    return passage.text[start:end]


def _evidence_fact_ids(sentence: DraftSentence, index: PackageIndex) -> tuple[str, ...]:
    """The **observations** this sentence's citations have to answer for (§6).

    A directly bound observation is itself; a bound `DerivedFact` is its two inputs; a bound
    §7 `EvidenceScopeFact` contributes nothing, because it was read from nothing. Order follows
    the bindings and then `(from, to)`, so a finding's `fact_ids` reads in the order the
    sentence made its claims rather than in hash order.

    An id that resolves as none of the three is dropped rather than passed through:
    `deterministic._check_identity` has already refused it as `fact_not_in_package` or
    `derived_fact_not_in_run`, and carrying it here would make a citation rule report a second
    finding about a binding nothing can evidence.
    """
    found: list[str] = []
    for binding in sentence.fact_bindings:
        if index.fact(binding.fact_id) is not None:
            found.append(binding.fact_id)
            continue
        found.extend(fact.observation_id
                     for fact in index.derived_inputs(binding.fact_id))
    return tuple(dict.fromkeys(found))


def _scope_bindings(sentence: DraftSentence, index: PackageIndex) -> tuple[str, ...]:
    """The §7 evidence-scope facts this sentence binds, if any."""
    return tuple(binding.fact_id for binding in sentence.fact_bindings
                 if index.scope_fact(binding.fact_id) is not None)


def check_sentence_citations(
    sentence: DraftSentence,
    index: PackageIndex,
    aliases: MetricAliasIndex,
    earlier_uses: Mapping[tuple[str, int, int], CitationUse],
    handles: Mapping[str, PackagedFact],
) -> tuple[tuple[VerificationFinding, ...], tuple[CitationUse, ...], int]:
    """Every §13.7 finding for one sentence, the uses it made, and how many were examined.

    Returns the uses so the caller can thread the reuse map forward without this module
    holding state across sentences — a verifier that accumulated in a module global would give
    two verifications of one draft different answers depending on their order.

    `handles` is `StoryEvidencePackage.facts_by_evidence_handle()`, built once by the caller
    for the same reason `earlier_uses` is threaded rather than accumulated here: the map is
    O(facts) and this function runs once per sentence, so rebuilding it per call would make
    §3.4's checks quadratic in a draft's length for a package that never changes between
    sentences. That is the arrangement the method's own docstring asks its two consumers for.
    """
    findings: list[VerificationFinding] = []
    uses: list[CitationUse] = []
    examined = 0
    # §6: the observations this sentence's citations answer for — its own bindings, plus the
    # inputs of every derived fact it binds. Never a derived id: nothing mints a handle for one.
    bound_ids = _evidence_fact_ids(sentence, index)
    scope_ids = _scope_bindings(sentence, index)

    for citation in sentence.citations:
        examined += 1
        if isinstance(citation, EvidenceSourceCitation):
            findings.append(finding(
                "evidence_kind_not_supported_in_v1",
                sentence_index=sentence.index,
                citation_ids=(citation.evidence_source_id,),
                expected="a citation into a filed passage",
                observed=f"evidence_kind={citation.evidence_kind}",
                explanation=(
                    "§13.7.2 Rule C: support for an :EvidenceSource is coordinate "
                    "reconstruction or input recursion, and no lane emits one — all 2,714 "
                    "evidence rows in this run are normalized_passage or normalized_table. An "
                    "unimplemented rule that silently passes is worse than one that refuses."),
            ))
            continue

        handle = citation_id(citation)
        if scope_ids:
            # §7: the fact carries no `citations` field and mints no evidence handle, and the
            # absence is the design. Support is positional reconstruction or span containment
            # and neither is defined against a fact that was read from nothing — so this is
            # `citation_does_not_support_fact` in that code's own words rather than a new one.
            findings.append(finding(
                "citation_does_not_support_fact",
                sentence_index=sentence.index,
                citation_ids=(handle,),
                fact_ids=scope_ids,
                expected="no citation on a sentence binding an evidence-scope fact",
                observed=f"{citation.passage_id} cited beside {', '.join(scope_ids)}",
                explanation=(
                    "§7: an evidence-scope fact states what this package's evidence does not "
                    "contain. A filed passage cited beside it is being presented as the source "
                    "of a claim about the filings' silence, which is the exact failure the "
                    "brief names: today that sentence reuses a financial-table citation as "
                    "though the table said it, and `counter_evidence_cited_as_support` and "
                    "`citation_reused_for_unrelated_claim` are all that stand between a draft "
                    "and the claim."),
            ))
            continue
        # Before the passage is resolved, because §3.4's questions are about the handle and the
        # fact it names, not about the span the citation happens to carry: a citation naming a
        # passage the package does not hold still has an answerable question about its handle,
        # and answering it names the fact the draft should have cited instead.
        findings.extend(_handle_findings(sentence, citation, handle, index, handles, bound_ids))

        passage = index.passage(citation.passage_id)
        if passage is None:
            findings.append(finding(
                "citation_not_in_package",
                sentence_index=sentence.index,
                citation_ids=(handle,),
                expected="a passage id in the package",
                observed=citation.passage_id,
                explanation="§13.13: every id a draft names must resolve in the package.",
            ))
            continue

        span = cited_span(passage, citation)
        if span is None:
            findings.append(finding(
                "citation_span_not_in_passage",
                sentence_index=sentence.index,
                citation_ids=(handle,),
                expected=(f"a span inside [{passage.char_start}, "
                          f"{passage.char_start + len(passage.text)})"),
                observed=f"[{citation.char_start}, {citation.char_end})",
                explanation=(
                    "§13.7: the cited span must exist. This passage is "
                    + ("an excerpt" if passage.excerpted else "carried whole")
                    + ", and the span falls outside the characters the package holds."),
            ))
            continue

        uses.append(CitationUse(
            passage_id=citation.passage_id,
            char_start=citation.char_start,
            char_end=citation.char_end,
            sentence_index=sentence.index,
            fact_ids=bound_ids,
        ))

        if index.is_document_grain_counter_evidence(citation.passage_id):
            findings.append(finding(
                "counter_evidence_cited_as_support",
                sentence_index=sentence.index,
                citation_ids=(handle,),
                fact_ids=bound_ids,
                expected="a passage a packaged fact was read from",
                observed=f"{citation.passage_id} is counter-evidence at document grain",
                explanation=(
                    "§10's counter-evidence join is at document grain — the refusal sits in a "
                    "neighbouring table of the same filing, not in the cell the number came "
                    "from. Citing it as support presents an association as a contradiction's "
                    "opposite, which is the §13.14 shape."),
            ))
            continue

        findings.extend(_support_findings(
            sentence, citation, handle, passage, span, index, aliases))
        findings.extend(_reuse_findings(
            sentence, citation, handle, index, earlier_uses))

    findings.extend(_coverage_findings(sentence, index, handles, bound_ids))
    return tuple(findings), tuple(uses), examined


def _handle_findings(
    sentence: DraftSentence,
    citation: PassageCitation,
    handle: str,
    index: PackageIndex,
    handles: Mapping[str, PackagedFact],
    bound_ids: tuple[str, ...],
) -> list[VerificationFinding]:
    """§3.4 checks 1, 7 and — through `_cell_findings` — 2 through 5.

    **Runs on every citation, because `PassageCitation.evidence_handle` is required.** This
    function opened with `if declared is None: return []` until the adversarial review of S7, and
    the docstring called the gap *"the one real limit of this repair"* on the grounds that a
    handle-less citation *"falls back to §13.7's older, weaker tests"*. **For a table fact there
    is no weaker test**: Rule A step 1 compares the package's own `quoted_text` against the whole
    `passage.text` — which is true of any citation into that passage, whatever bytes it names —
    and Rule B does not run at all. A one-character citation into the demo package's table
    passage, bound to `adjusted_gross_margin`, passed §12 and verified with zero findings
    *(reproduced 2026-08-18)*. The field is now required with no default (`story/core/models.py`),
    so the branch is gone rather than narrowed.

    Check 7 is conditioned on the sentence binding a fact at all, exactly as
    `citation_does_not_support_fact` is. A sentence that binds nothing has no fact for the
    handle to be wrong about; what it cites is judged by Rule B and by the reuse rule.

    **§6: `bound_ids` here is `_evidence_fact_ids`, so a derived binding is answered for by its
    two inputs.** A handle minted for an input of a bound derivation satisfies check 7 and a
    handle for anything else does not — which is how *"a citation supporting a derived fact is
    one that supports an input fact of it"* becomes a membership test rather than an exception.
    The derived id itself is never in the set and no handle names it.

    Findings accumulate rather than short-circuit after check 7: a handle for another fact is
    still a handle this package minted, so checks 2–5 are answerable and true about it, and
    reporting both tells the reader which fact the cited cell actually belongs to.
    """
    declared = citation.evidence_handle
    fact = handles.get(declared)
    if fact is None:
        return [finding(
            "unresolvable_evidence_handle",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=bound_ids,
            expected="an evidence handle this package minted",
            observed=declared,
            explanation=(
                "§3.4 check 1: a handle is derived from coordinates a PackagedFact already "
                "carries, and nothing else mints one. An id no fact in this package answers to "
                "names no evidence — a fabricated cell, or a handle from another package."),
            suggested_fact_ids=[other.observation_id for other in index.package.facts
                                if other.evidence_handle is not None],
        )]

    findings: list[VerificationFinding] = []
    if bound_ids and fact.observation_id not in bound_ids:
        findings.append(finding(
            "evidence_handle_not_for_fact",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=bound_ids,
            expected=("a handle this package minted for "
                      + ", ".join(bound_ids) + ": "
                      + ", ".join(sorted(
                          other.evidence_handle for other in index.package.facts
                          if other.observation_id in bound_ids
                          and other.evidence_handle is not None))),
            observed=f"{declared} was minted for {fact.observation_id}",
            explanation=(
                "§3.4 check 7. §1.4 measured the cell coordinate as a perfect key — 2,690 "
                "table-backed observations, 2,690 distinct cells, 0 mapping to two periods and "
                "0 to two metrics — so a handle names exactly one fact and citing another "
                "fact's cell is always detectable. It was not detectable before: all 144 "
                "table-backed passages in the corpus evidence more than one observation, so "
                "\"the passage each bound fact was read from\" separated nothing."),
            # The fact the cited cell *does* belong to, because REBIND_TO_FACT is the remedy and
            # the other reading of this finding — the sentence meant that number — is fixed by
            # binding it rather than by re-citing. The handle to cite instead is in `expected`;
            # this field is fact ids, as `citation_does_not_support_fact` uses it.
            suggested_fact_ids=(fact.observation_id,),
        ))

    evidence_passage = index.passage(fact.passage_id or "")
    if evidence_passage is None:
        findings.append(finding(
            "unresolvable_evidence_handle",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected="a handle whose passage is one the package carries",
            observed=f"{declared} was read from {fact.passage_id!r}",
            explanation=(
                "§3.4 check 1, second half: the handle resolves to a fact and the fact's "
                "passage is outside the writer's slice (§10.2.1 point 3), so there is no grid "
                "to resolve the cell in and nothing to compare the citation against."),
        ))
        return findings

    if fact.cell is None:
        # Narrative evidence: no grid, so §3.4 checks 2-5 have nothing to ask. Rule B owns the
        # span for these 14 of 2,704 observations, and the handle carries the fact's slot
        # rather than a coordinate precisely because no coordinate separates them.
        return findings

    findings.extend(_cell_findings(sentence, citation, handle, fact, evidence_passage))
    return findings


def _cell_findings(
    sentence: DraftSentence,
    citation: PassageCitation,
    handle: str,
    fact: PackagedFact,
    passage: PackagedPassage,
) -> list[VerificationFinding]:
    """§3.4 checks 2–5 over one table cell, plus the span the citation paired with the handle.

    **None of these four can be caused by a draft**, which is why their remedy is
    REBUILD_PACKAGE: the coordinates are the `PackagedFact`'s own and the text is the
    `PackagedPassage`'s own, so a finding here says the package disagrees with itself. All three
    equalities hold on **2,690 / 2,690** table-backed evidence rows *(verified live 2026-08-18
    against `bolt://127.0.0.1:7687`, the graph run in `data/graph_runs`)*. They are checked
    anyway because a package is stored data (§14) that can be replayed against a re-extracted
    corpus, and because a measurement is not a guarantee about the next one.

    `evidence_cell_span_mismatch` is the one addition to §3.4's list, and it is checked **on
    every citation that states a handle**, against the cell's own passage.

    **It used to be guarded by `citation.passage_id == passage.passage_id`, and the reason given
    for the guard was false.** The claim was *"when it does not, `citation_does_not_support_fact`
    is already the finding"*. `_support_findings` raises that code only when **no** bound fact was
    read from the cited passage; a sentence binding a second fact that *was* read from it leaves
    the span unexamined. Reproduced 2026-08-18 on
    `pkg:metric-move-adjusted-ebitda-opendoor-2021q3-2021q4:87518cfbba68`: one sentence binding
    `obs:adjusted-ebitda:opendoor:2021Q3:normalized-table:507a6e847d8b` (read from
    `…q32021form8-kxexhibit991.htm#p28`) and `…:2021Q4:…772d120949e5` (read from
    `…q42021formxex991earningsre.htm#p29`), carrying one citation with the 2021Q3 handle, the
    2021Q4 passage id and the span `[0, 7)` over `'|  |  |'` — `check_sentence_citations`
    returned **no finding at all**, and the evidence panel highlighted seven bytes of the wrong
    filing under a verified handle.

    So the comparison is `(passage_id, char_start, char_end)` against the cell, not offsets
    against offsets: a span in another passage is not the cell's span whatever its numbers say.
    Where the older code *does* also fire the two findings stand together rather than one
    suppressing the other — `citation_does_not_support_fact` says the citation is not the fact's
    passage, this says the handle and the bytes on one row name different things, and a reader
    fixing one has not learned the other.
    """
    findings: list[VerificationFinding] = []
    cell = fact.cell
    assert cell is not None  # the caller returned on `cell is None`

    if passage.excerpted:
        # `PackagedPassage`'s own contract is *"a passage a fact is bound to is never excerpted —
        # Rule A needs the whole table"*. If one ever is, the grid coordinates are positions in
        # the full `:Passage.text` and the package holds a ±400-character window, so they would
        # resolve against the wrong string and return a perfectly well-formed wrong cell. Refused
        # under check 2's code for `cited_span`'s reason: a span nobody can see licenses nothing.
        return [finding(
            "evidence_handle_out_of_bounds",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected=f"the whole text of {passage.passage_id}",
            observed=(f"an excerpt of {len(passage.text)} characters from "
                      f"{passage.char_start}"),
            explanation=(
                "§3.4 check 2: the cell's coordinates are positions in the full :Passage.text "
                "and the package carries a window of it, so no cell can be resolved. A passage "
                "a fact was read from is never excerpted (§10.2.1 point 2) — this package "
                "breaks its own rule."),
        )]

    try:
        resolved = resolve_cell(passage.text,
                                row_index=cell.row_index,
                                column_index=cell.value_column_index)
    except CellOutOfBounds as off_grid:
        return [finding(
            "evidence_handle_out_of_bounds",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected=f"a cell at ({cell.row_index}, {cell.value_column_index})",
            observed=str(off_grid),
            explanation=(
                "§3.4 check 2. The coordinates are the package's own, so this is the package "
                "and the passage text it carries disagreeing about the shape of the table."),
        )]

    quoted = fact.quoted_text or ""
    if resolved.text != quoted:
        findings.append(finding(
            "evidence_cell_value_mismatch",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected=f"quoted_text {quoted!r} at ({cell.row_index}, {cell.value_column_index})",
            observed=repr(resolved.text),
            explanation=(
                "§3.4 check 3, which holds on 2,690 / 2,690 table-backed evidence rows. The "
                "cell the handle names does not hold the value the fact was read from, so the "
                "handle points at a different number than the one the sentence states."),
        ))

    row_label = fact.row_label or ""
    if resolved.row_label != row_label:
        findings.append(finding(
            "evidence_row_label_mismatch",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected=f"row_label {row_label!r} in column 0 of row {cell.row_index}",
            observed=repr(resolved.row_label),
            explanation=(
                "§3.4 check 4, which holds on 2,690 / 2,690. The row the coordinates land on "
                "is not the row the fact records, so the cell belongs to another metric."),
        ))

    try:
        header = resolve_header(passage.text,
                                row_index=cell.period_header_row_index,
                                column_index=cell.period_header_column_index)
    except CellOutOfBounds as off_grid:
        findings.append(finding(
            "evidence_handle_out_of_bounds",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected=(f"a header cell at ({cell.period_header_row_index}, "
                      f"{cell.period_header_column_index})"),
            observed=str(off_grid),
            explanation="§3.4 check 2, at the period header rather than at the value.",
        ))
    else:
        column_label = fact.column_label or ""
        if header != column_label:
            findings.append(finding(
                "evidence_column_label_mismatch",
                sentence_index=sentence.index,
                citation_ids=(handle,),
                fact_ids=(fact.observation_id,),
                expected=f"column_label {column_label!r} at "
                         f"({cell.period_header_row_index}, {cell.period_header_column_index})",
                observed=repr(header),
                explanation=(
                    "§3.4 check 5, which holds on 2,690 / 2,690 — but only at "
                    "period_header_column_index. Read at the value's own column it holds on "
                    "565 / 2,690, because `$` signs and blank spacer cells push the header out "
                    "of the value's column. This says the header standing over the cited cell "
                    "is not the period the fact was read under."),
            ))

    # Absolute, and carrying the passage id: `char_start`/`char_end` on a citation are offsets
    # into the full `:Passage.text` of the passage it names, so two spans are the same bytes only
    # when the passages agree too. Rebased through `passage.char_start`, which is 0 for every
    # passage a fact binds and would not be if that rule ever broke — `_cell_findings` refuses an
    # excerpted one above.
    cell_span = (passage.char_start + resolved.char_start, passage.char_start + resolved.char_end)
    if (citation.passage_id, citation.char_start, citation.char_end) != (
            passage.passage_id, *cell_span):
        findings.append(finding(
            "evidence_cell_span_mismatch",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected=f"[{cell_span[0]}, {cell_span[1]}) of {passage.passage_id} — cell "
                     f"({cell.row_index}, {cell.value_column_index})",
            observed=f"[{citation.char_start}, {citation.char_end}) of {citation.passage_id}",
            explanation=(
                "§13.7: the handle and the span on one citation row must name the same "
                "bytes. §12 derives the span from the handle, so a draft the writer built "
                "cannot differ; one where they differ has a verified handle and an evidence "
                "panel highlighting characters nothing checked."),
        ))
    return findings



def _support_findings(
    sentence: DraftSentence,
    citation: PassageCitation,
    handle: str,
    passage: PackagedPassage,
    span: str,
    index: PackageIndex,
    aliases: MetricAliasIndex,
) -> list[VerificationFinding]:
    """Rule A or Rule B for each fact this sentence bound, plus the uncited-claim refusal.

    **§6: a bound `DerivedFact` is supported through its inputs.** `_evidence_pairs` pairs each
    binding with the observation Rule A or Rule B is run against, which for a derived binding is
    one of its two inputs — so `-$446M` is evidenced by the two cells `$556M` and `$110M` were
    read from, and by nothing else. The binding carried alongside is the derived one, because it
    is the binding that holds the span, the metric surface and the period surface a rule reads.
    """
    findings: list[VerificationFinding] = []
    pairs = _evidence_pairs(sentence, index)
    supported = [(binding, fact) for binding, fact in pairs
                 if fact.passage_id == citation.passage_id]
    if pairs and not supported:
        findings.append(finding(
            "citation_does_not_support_fact",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=tuple(fact.observation_id for _binding, fact in pairs),
            expected="the passage each bound fact — or each input of a bound derivation — "
                     "was read from",
            observed=citation.passage_id,
            explanation=(
                "§13.7: support is positional reconstruction (Rule A) or span containment "
                "(Rule B), and neither is defined against a passage the fact was not read "
                "from."),
            suggested_fact_ids=[
                fact.observation_id for fact in index.package.facts
                if fact.passage_id == citation.passage_id],
        ))

    for binding, fact in supported:
        if index.is_table_fact(fact) and sentence.kind is not SentenceKind.EXPLANATORY:
            findings.extend(_rule_a(sentence, binding, fact, handle, passage, index, aliases))
        else:
            findings.extend(_rule_b(sentence, binding, fact, handle, span, aliases))
    return findings


def _evidence_pairs(
    sentence: DraftSentence, index: PackageIndex
) -> tuple[tuple[FactBinding, PackagedFact], ...]:
    """Each binding paired with the observation a §13.7 rule is run against.

    One pair for a directly bound observation; two for a bound derived fact, one per input.
    A §7 evidence-scope binding yields none — it was read from nothing, and the citation on
    such a sentence is refused before any rule here runs.
    """
    pairs: list[tuple[FactBinding, PackagedFact]] = []
    for binding in sentence.fact_bindings:
        fact = index.fact(binding.fact_id)
        if fact is not None:
            pairs.append((binding, fact))
            continue
        pairs.extend((binding, input_fact)
                     for input_fact in index.derived_inputs(binding.fact_id))
    return tuple(pairs)


def _rule_a(
    sentence: DraftSentence,
    binding: FactBinding,
    fact: PackagedFact,
    handle: str,
    passage: PackagedPassage,
    index: PackageIndex,
    aliases: MetricAliasIndex,
) -> list[VerificationFinding]:
    """§13.7 Rule A, five steps, of which this module owns 1–4.

    **Step 1 is kept, and §3.4 check 3 does not make it redundant. The argument both ways,
    because it is close.** Check 3 (`evidence_cell_value_mismatch`) says the cell at the
    handle's coordinates *is* `quoted_text`; if that holds, `quoted_text` is by construction a
    substring of `passage.text`, so step 1 cannot fail where check 3 passes. Over the corpus as
    it stands that makes step 1 dead weight: 2,704 / 2,704 evidence rows satisfy it and every
    table-backed one is now covered by a strictly stronger equality.

    It stays because **check 3's population is a subset of step 1's**. Check 3 runs only when the
    fact carries a `TableCellRef`. Step 1 runs for every fact `PackageIndex.is_table_fact`
    accepts, which is decided by `source_lane == "normalized_table"` and not by the presence of
    coordinates — so a table-lane fact with `cell is None` reaches step 1 and reaches no §3.4
    check at all.

    **The example this paragraph used to give was dead, and the correction is a measurement.** It
    named `fixtures/story_demo/evidence_package.json` as carrying that shape *"because that
    fixture predates S1 and S7 has not yet rebuilt it"*. S7 rebuilt it in `74f4f1c`; both its
    facts now carry cells and table handles. Re-measured live 2026-08-18 over the 262 packages
    the four detectors offer: `source_lane == normalized_table` implies `cell is not None` on
    **558 / 558**, and all **6** cell-less facts are `normalized_narrative`. So the gap between
    the two populations is one the types permit and the corpus does not currently hold — which is
    the standing of a check, not the standing of an example.

    Step 1 also owns a failure check 3 cannot phrase. `quote` empty — a retrieval query that
    returned node fields and dropped the `EVIDENCED_BY` edge property — is *"the fact carries no
    quoted_text"* here, and would surface from check 3 as a mismatch against `''`, which reads
    as a wrong cell rather than as missing evidence. Deleting a reachable code because a
    measurement says it has never fired is the move `_quote_violation` refused at S4, for the
    same reason.
    """
    findings: list[VerificationFinding] = []
    quote = fact.quoted_text or ""

    if not quote or quote not in passage.text:
        findings.append(finding(
            "citation_quote_not_in_passage",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected=f"quoted_text {quote!r} verbatim in {passage.passage_id}",
            observed="absent" if quote else "the fact carries no quoted_text",
            explanation=(
                "§13.7 Rule A step 1, which holds on 2,714/2,714 evidence rows in this run. "
                "quoted_text is a property of the EVIDENCED_BY edge (C3); a retrieval query "
                "returning only node fields loses it silently."),
        ))
    else:
        try:
            rebuilt = reconstruct_table_quote(quote, scale=fact.scale, unit=fact.unit)
        except UnreconstructableQuote as error:
            findings.append(finding(
                "table_quote_does_not_reconstruct",
                sentence_index=sentence.index,
                citation_ids=(handle,),
                fact_ids=(fact.observation_id,),
                expected=f"{fact.value!r}",
                observed=str(error),
                explanation="§13.7 Rule A step 2.",
            ))
        else:
            # Compared after rounding, never by equality: the reconstruction goes through
            # Decimal and the stored value is an IEEE-754 float, and `15.9` is held as
            # `15.899999999999999`.
            if round(rebuilt, 6) != round(fact.value, 6):
                findings.append(finding(
                    "table_quote_does_not_reconstruct",
                    sentence_index=sentence.index,
                    citation_ids=(handle,),
                    fact_ids=(fact.observation_id,),
                    expected=f"{fact.value!r}",
                    observed=f"{quote!r} with scale={fact.scale!r} reconstructs to {rebuilt!r}",
                    explanation=(
                        "§13.7 Rule A step 2, which holds on 2,690/2,690 table observations. A "
                        "quote that does not reconstruct is a citation to a different cell."),
                ))

    if fact.row_label and not aliases.licenses(fact.row_label, fact.metric_id):
        findings.append(finding(
            "row_label_not_licensed_for_metric",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected=f"a licensed surface for {fact.metric_id}",
            observed=f"row_label={fact.row_label!r}",
            explanation="§13.7 Rule A step 3, through §13.5's alias index.",
        ))

    findings.extend(_column_findings(sentence, binding, fact, handle, index))
    return findings


def _column_findings(
    sentence: DraftSentence,
    binding: FactBinding,
    fact: PackagedFact,
    handle: str,
    index: PackageIndex,
) -> list[VerificationFinding]:
    """§13.7.1: the column label must resolve to one `period_key` *within this passage*."""
    slot = index.column_slot(fact)
    if slot is None or not slot.ambiguous:
        return []
    if index.column_ambiguity_classified(fact):
        return [finding(
            "column_label_ambiguity_classified",
            sentence_index=sentence.index,
            char_start=binding.char_start,
            char_end=binding.char_end,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected=f"one period under column {slot.column_label!r}",
            observed=", ".join(slot.period_keys),
            explanation=(
                "§13.7.1 hatch 2: D15 classified this slot as year_only_column_ambiguity and "
                "§6.1 step 4 resolved it by document majority. The binding proceeds and the "
                "minority reading must be rendered in the evidence panel."),
        )]
    siblings = index.distinguishing_siblings(fact)
    return [finding(
        "column_label_ambiguous_in_passage",
        sentence_index=sentence.index,
        char_start=binding.char_start,
        char_end=binding.char_end,
        citation_ids=(handle,),
        fact_ids=(fact.observation_id,),
        expected=f"column {slot.column_label!r} to name one period in {slot.passage_id}",
        observed=", ".join(slot.period_keys),
        explanation=(
            "§13.7.1: 179 of 485 (passage_id, column_label) pairs in the corpus map to more "
            "than one period_key, covering 61.3% of observations. "
            + (f"Distinguishing labels in this passage: {', '.join(siblings)}."
               if siblings else
               "No label in this passage resolves uniquely, so hatch 1 does not apply.")),
    )]


def _rule_b(
    sentence: DraftSentence,
    binding: FactBinding,
    fact: PackagedFact,
    handle: str,
    span: str,
    aliases: MetricAliasIndex,
) -> list[VerificationFinding]:
    """§13.7 Rule B: span containment, the number, and lexical grounding.

    REFUSE on span, quote and number; **WARN on paraphrase distance**, which §13.7 escalates to
    §13.16 — a stage this demo path does not build, so the WARN is carried into the accepted
    artifact and acknowledged there rather than adjudicated.

    **What Rule B now means, since the model no longer chooses the span.** Its first step was
    written as a check on the writer: *"the package's quote must occur inside the span the model
    picked"*, and the span was the model's to pick. Under handles it is not. For a narrative
    fact, `writer._span_for` locates the **unique** occurrence of the package's own
    `quoted_text` and the citation's span is exactly that run of characters, so
    `quote in span` is true by construction — the same way Rule A step 1 became true by
    construction for table facts. Rule B has stopped being a statement about a model's typing.

    What it is now is a **coherence check between the citation's span and the package's own
    quote**, and it has exactly one population left: citations §12 did not build. A draft
    replayed from `data/story_runs/` against a re-extracted corpus, or one assembled in code, can
    carry a span that no longer covers the sentence the `EVIDENCED_BY` edge quoted. That is a
    defect in the evidence rather than in the prose, and it must refuse. `evidence_cell_span_
    mismatch` is the table half of the same guarantee, reached structurally instead of by
    substring, and the two together are why the span on a citation row is never taken on trust.

    Rule B also runs for a table fact cited by an `explanatory` sentence (see
    `_support_findings`), where the span is a four-character cell. That is unchanged and is
    still the reason `paraphrase_distance` is a WARN: nothing in `'3.3'` can ground a clause.
    """
    findings: list[VerificationFinding] = []
    quote = fact.quoted_text or ""
    if quote and quote not in span:
        findings.append(finding(
            "citation_quote_not_in_passage",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected=f"quoted_text {quote!r} inside the cited span",
            observed=span[:120],
            explanation=(
                "§13.7 Rule B: the cited span must contain the evidence span. Containment in "
                "the whole passage is not enough — passages run to thousands of characters."),
        ))

    if not _span_carries_value(span, fact.value):
        findings.append(finding(
            "narrative_span_missing_number",
            sentence_index=sentence.index,
            char_start=binding.char_start,
            char_end=binding.char_end,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected=f"a numeral reading {fact.value!r} in the cited span",
            observed=span[:120],
            explanation="§13.7 Rule B: the span must carry the number if the sentence does.",
        ))

    licensed = [binding.metric_surface, binding.period_surface, *binding.metric_surface.split()]
    ungrounded = language.ungrounded_words(sentence.text, span, licensed=licensed)
    if ungrounded:
        findings.append(finding(
            "paraphrase_distance",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=(fact.observation_id,),
            expected="every content word grounded in the span, an alias or the connective lexicon",
            observed=", ".join(ungrounded),
            explanation=(
                "§13.7 Rule B: WARN, escalated to §13.16 where a model-assisted verifier exists. "
                "On this path there is none, so the warning is acknowledged in the artifact."),
        ))
    return findings


def _span_carries_value(span: str, value: float) -> bool:
    """Does any numeral in the span read as this fact's value, at that numeral's precision?

    Uses §13.1's own tolerance rather than a string search: the span prints `"5.2%"` where the
    fact holds `5.2`, and `"$218 million"` where it holds `218000000.0`.
    """
    for token in tokenize_numerals(span):
        verdict = compare_to_fact(
            token.value, value,
            draft_significant_figures=token.significant_figures,
            draft_sign_explicit=token.negative,
        )
        if verdict.accepted:
            return True
    return False


def _reuse_findings(
    sentence: DraftSentence,
    citation: PassageCitation,
    handle: str,
    index: PackageIndex,
    earlier_uses: Mapping[tuple[str, int, int], CitationUse],
) -> list[VerificationFinding]:
    """§13.7: one citation may not be reused for an unrelated factual claim.

    Fires on the **second** use of an identical span when this sentence binds no fact that the
    passage evidences. Reuse alone is not the offence — a two-column table legitimately
    evidences two facts — so the predicate is reuse *and* non-support, which is why a
    legitimate second citation of the same table passes and a decorative one does not.
    """
    key = (citation.passage_id, citation.char_start, citation.char_end)
    earlier = earlier_uses.get(key)
    if earlier is None or earlier.sentence_index == sentence.index:
        return []
    # §6: a derived binding supports through its inputs here too, so a second citation of the
    # table a derivation was computed from is a legitimate second use and not a decorative one.
    supports_here = any(fact.passage_id == citation.passage_id
                        for _binding, fact in _evidence_pairs(sentence, index))
    if supports_here:
        return []
    return [finding(
        "citation_reused_for_unrelated_claim",
        sentence_index=sentence.index,
        citation_ids=(handle,),
        expected=(f"a fact read from {citation.passage_id}, as in sentence "
                  f"{earlier.sentence_index}"),
        observed=(", ".join(binding.fact_id for binding in sentence.fact_bindings)
                  or "no fact binding at all"),
        explanation=(
            "§13.7: the identical span was already cited for a different claim, and this "
            "sentence binds nothing that passage evidences. A citation carried forward to "
            "decorate a second claim is provenance the passage does not supply."),
    )]


def _coverage_findings(
    sentence: DraftSentence,
    index: PackageIndex,
    handles: Mapping[str, PackagedFact],
    bound_ids: tuple[str, ...],
) -> list[VerificationFinding]:
    """Every fact this sentence binds must be named by one of this sentence's citations.

    **The hole, and why check 7 does not close it.** Check 7 asks *"is this handle one this
    package minted for a fact this sentence binds"* — `fact.observation_id not in bound_ids` —
    so **any** handle for **any** bound fact satisfies it for **every** bound fact. A sentence
    stating two figures and carrying one evidence id therefore verified clean: reproduced
    2026-08-18 on `pkg:cross-metric-divergence-adjusted-gross-margin-gaap-gross-margin-opendoor-
    2022q3:4e4363b11373`, one sentence binding both margins with only
    `ev:…open-20220930.htm#p139:r11c2`, `passed=True` and zero findings. The GAAP figure was
    stated with nothing pointing at it, and the panel drew one mark.

    **`uncited_factual_sentence` is the right code and no new one is minted.** §13.7 already
    names this defect at sentence grain — *"a reported sentence states what a filing said, and an
    explanatory one paraphrases it; neither is checkable without the span"*
    (`deterministic.py`) — and a bound figure no citation reaches is that same sentence for that
    same figure. Its remedy, `REBIND_TO_FACT`, is the right instruction: cite the fact's handle
    or drop the binding. Minting a second code would split one claim across two names and leave
    the demo's rejection catalogue explaining the difference between them.

    **It runs only when every citation resolved to a fact this sentence binds**, which is the
    difference between *"the sentence cites fewer facts than it states"* and *"the sentence cites
    the wrong fact"*. A citation whose handle names an unbound fact is already
    `evidence_handle_not_for_fact` and one that names nothing is `unresolvable_evidence_handle`;
    both name the fact to rebind to, and a second finding saying the bound fact went uncited
    would be the same defect counted twice. A sentence with no `PassageCitation` at all is
    `uncited_factual_sentence` from `deterministic.py` or — for a Rule C row —
    `evidence_kind_not_supported_in_v1`, so this returns nothing there and the code is raised
    once however the sentence fails.

    A bound fact carrying no `evidence_handle` cannot be covered by anything: that is the
    §13.7.2 row with no filed passage, **0 of 564 facts** across the 262 live packages
    *(measured 2026-08-18)*, and refusing it is the same answer Rule C gives its citations.

    **§6 widens *"every fact this sentence binds"* to include a derived fact's two inputs, and
    that is a real strengthening rather than bookkeeping.** `-$446M` rests on `$556M` and
    `$110M`; a sentence stating the fall and citing one endpoint has evidenced half of it, and
    the reader is shown one mark under a number computed from two. Both cells or neither.

    **Driven over every real package, both ways.** For each of the **724** ordered pairs of
    handle-carrying facts in one package, a sentence binding both: citing both handles raises
    this code **0** times, citing one raises it **724 / 724** and names the uncovered fact every
    time *(measured 2026-08-18 against the 262 packages the four detectors offer)*. And the
    stand-down holds where it must: the 90 wrong-cell mis-citations §3.4 was built for still
    refuse with `evidence_handle_not_for_fact` **and nothing else**.
    """
    cited = tuple(citation for citation in sentence.citations
                  if isinstance(citation, PassageCitation))
    if not cited:
        return []
    named = {citation.evidence_handle for citation in cited}
    covered = {handles[declared].observation_id for declared in named if declared in handles}
    if any(declared not in handles for declared in named) or not covered <= set(bound_ids):
        return []

    uncovered = tuple(fact_id for fact_id in dict.fromkeys(bound_ids)
                      if fact_id not in covered and index.fact(fact_id) is not None)
    if not uncovered:
        return []
    wanted = sorted(
        fact.evidence_handle for fact in index.package.facts
        if fact.observation_id in uncovered and fact.evidence_handle is not None)
    return [finding(
        "uncited_factual_sentence",
        sentence_index=sentence.index,
        citation_ids=tuple(citation_id(citation) for citation in cited),
        fact_ids=uncovered,
        expected=("a citation for each fact this sentence binds: "
                  + (", ".join(wanted) if wanted else
                     "no handle exists for it — §13.7.2 evidence names no filed passage")),
        observed=", ".join(sorted(named)),
        explanation=(
            "§13.7: a citation is evidence for the fact its handle names and for no other. "
            "§3.4 check 7 asks only that a handle belong to *a* fact the sentence binds, so one "
            "handle satisfies it for every binding — a sentence stating two figures and "
            "evidencing one is provenance the evidence does not supply, in the same words the "
            "reuse rule refuses a citation carried forward to decorate a second claim."),
        suggested_fact_ids=uncovered,
    )]


def index_uses(uses: Sequence[CitationUse]) -> dict[tuple[str, int, int], CitationUse]:
    """First use of each span, which is the one a reuse finding names."""
    first: dict[tuple[str, int, int], CitationUse] = {}
    for use in uses:
        first.setdefault(use.span_key, use)
    return first


__all__ = [
    "CitationUse",
    "check_sentence_citations",
    "citation_id",
    "cited_span",
    "index_uses",
]
