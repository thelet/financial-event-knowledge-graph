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

**The handle rules (TABLE_CELL_CITATIONS §3.4), and what they replace.** A citation now carries
the `PackagedFact.evidence_handle` the package minted, and `_handle_findings` asks the six
questions §3.4 lists plus one it does not — see `codes.py` notes 9 through 11. The one that
carries the load is check 7, *"the handle is the one this package minted for this fact"*, and
the hole it closes was **total on table evidence**: §13.7's older test is
`citation_does_not_support_fact`, *"the passage each bound fact was read from"*, and every one
of the corpus's **144** table-backed passages evidences more than one observation — 2,690 of
2,690, up to 70 in a single passage *(verified live 2026-08-18)*. A sentence binding one cell
and citing the cell beside it passed that test for the whole corpus.

**Rule A step 1 is not made redundant by check 3, and Rule B is no longer a check on the
model.** Both are argued where they are implemented — `_rule_a` and `_rule_b` — because both
changed meaning when the model stopped choosing spans, and a reader who finds only one of the
two arguments would conclude the wrong thing about the other.
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
    bound_ids = tuple(binding.fact_id for binding in sentence.fact_bindings)

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

    **Runs only when the citation states a handle, and that asymmetry is the one real limit of
    this repair.** `PassageCitation.evidence_handle` is optional because a citation nothing
    minted has no handle to state, so `None` is *"no package named this evidence"* and not a
    missing value. Every citation `writer.draft_from` builds carries one by construction — the
    §15.3 schema makes `evidence_id` the model's only citation field — so on the path a model's
    answer takes, these checks always run. A `PassageCitation` assembled in code can still omit
    the handle and fall back to §13.7's older, weaker tests, and closing *that* means making the
    field required on a type this stage does not own.

    Check 7 is conditioned on the sentence binding a fact at all, exactly as
    `citation_does_not_support_fact` is. A sentence that binds nothing has no fact for the
    handle to be wrong about; what it cites is judged by Rule B and by the reuse rule.

    Findings accumulate rather than short-circuit after check 7: a handle for another fact is
    still a handle this package minted, so checks 2–5 are answerable and true about it, and
    reporting both tells the reader which fact the cited cell actually belongs to.
    """
    declared = citation.evidence_handle
    if declared is None:
        return []

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

    `evidence_cell_span_mismatch` is the one addition to §3.4's list and is checked only when
    the citation names the fact's own passage — when it does not, `citation_does_not_support_fact`
    is already the finding, and a second one comparing offsets across two different passages
    would be noise rather than a second defect.
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

    if citation.passage_id == passage.passage_id:
        cited = (citation.char_start - passage.char_start,
                 citation.char_end - passage.char_start)
        if cited != (resolved.char_start, resolved.char_end):
            findings.append(finding(
                "evidence_cell_span_mismatch",
                sentence_index=sentence.index,
                citation_ids=(handle,),
                fact_ids=(fact.observation_id,),
                expected=f"[{resolved.char_start}, {resolved.char_end}) — cell "
                         f"({cell.row_index}, {cell.value_column_index}) of "
                         f"{passage.passage_id}",
                observed=f"[{cited[0]}, {cited[1]})",
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
    """Rule A or Rule B for each fact this sentence bound, plus the uncited-claim refusal."""
    findings: list[VerificationFinding] = []
    supported = [
        binding for binding in sentence.fact_bindings
        if (fact := index.fact(binding.fact_id)) is not None
        and fact.passage_id == citation.passage_id
    ]
    if sentence.fact_bindings and not supported:
        findings.append(finding(
            "citation_does_not_support_fact",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            fact_ids=tuple(binding.fact_id for binding in sentence.fact_bindings),
            expected="the passage each bound fact was read from",
            observed=citation.passage_id,
            explanation=(
                "§13.7: support is positional reconstruction (Rule A) or span containment "
                "(Rule B), and neither is defined against a passage the fact was not read "
                "from."),
            suggested_fact_ids=[
                fact.observation_id for fact in index.package.facts
                if fact.passage_id == citation.passage_id],
        ))

    for binding in supported:
        fact = index.fact(binding.fact_id)
        assert fact is not None  # `supported` was built from a resolved lookup
        if index.is_table_fact(fact) and sentence.kind is not SentenceKind.EXPLANATORY:
            findings.extend(_rule_a(sentence, binding, fact, handle, passage, index, aliases))
        else:
            findings.extend(_rule_b(sentence, binding, fact, handle, span, aliases))
    return findings


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

    It stays because **check 3's population is a subset of step 1's, and the difference is not
    hypothetical**. Check 3 runs only when the citation states a handle *and* the fact carries a
    `TableCellRef`. Step 1 runs for every fact `PackageIndex.is_table_fact` accepts, which is
    decided by `source_lane == "normalized_table"` and not by the presence of coordinates — so a
    table-lane fact with `cell is None` reaches step 1 and reaches no §3.4 check at all. That
    shape is not a thought experiment: it is what `fixtures/story_demo/evidence_package.json`
    carries today, because that fixture predates S1 and S7 has not yet rebuilt it.

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
    supports_here = any(
        (fact := index.fact(binding.fact_id)) is not None
        and fact.passage_id == citation.passage_id
        for binding in sentence.fact_bindings
    )
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
