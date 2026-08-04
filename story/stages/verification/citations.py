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
) -> tuple[tuple[VerificationFinding, ...], tuple[CitationUse, ...], int]:
    """Every §13.7 finding for one sentence, the uses it made, and how many were examined.

    Returns the uses so the caller can thread the reuse map forward without this module
    holding state across sentences — a verifier that accumulated in a module global would give
    two verifications of one draft different answers depending on their order.
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
    """§13.7 Rule A, five steps, of which this module owns 1–4."""
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
