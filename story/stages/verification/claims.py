"""§13.10, §13.14 and §13.15: what a sentence is allowed to assert.

Responsibility: the claim-safety half of the gate — causation, superlatives, comparatives,
absence, temporal ordering, forward-looking language, the connective sentence's no-claim rule,
and the title. `language.py` holds the lexicons and locates a construction; this module decides
whether the machinery §13.14 requires is behind it and recomputes the claim when it is.

Split out of `deterministic.py` because it answers a different question. Everything there is
*"does this number, unit, period, metric or citation match the package"*; everything here is
*"may this sentence say this at all"*, and the two share nothing but the package index. That is
a boundary, not a symmetry: the numeric checks reach for `story/core/numerals.py` and this
module reaches for a closed lexicon, and neither ever calls the other.

**§13.14 is the largest single addition the adversarial review forced, and it is where V1 is
most likely to be over-strict rather than under-strict.** Some legitimate connective prose will
be refused. That is the right direction for the failure to point, and §22's readability score is
where the cost shows up.

**§13.10 conditions 3 and 6 could not be implemented as written.** They require the binding to
name the cause term, the effect term and which marker occurrence the sentence relies on;
`DraftSentence`, `FactBinding` and `Calculation` carry no field for any of the three. The
conservative closure is here instead: a cited span carrying **more than one** causal marker is
refused outright, because the sentence has no way to say which one it means. 367 passages carry
two or more markers, so this refuses real sentences — and it never accepts a false one.

**§13.14's `operation: absence` cannot be satisfied from a bounded package either.** §10.2 caps
`facts[]` at twelve, so the package's own coverage establishes that *this package* holds no such
value and never that the corpus does not. Absence claims are refused — with `unpopulated_metric`
when a declared metric is empty, which is §17's attack 7.

**A correction to §13.14's own table.** Its second attack row reads *"Contribution profit held
up better than adjusted gross profit through the downturn." — "False by $2M — the 2022Q2→Q3 fall
is −$444M for CP and −$446M for AGP."* On those figures CP fell **less**, so the sentence is
*true* by $2M, not false. The mechanism the row exists to justify is unaffected: nothing outside
a `compare_deltas` recomputation looks at either number, and a `connective` sentence carrying
*"held up"* is refused here whichever way the arithmetic falls.
"""

from __future__ import annotations

from typing import Sequence

from story.core.models import (
    Calculation,
    CheckResult,
    Draft,
    DraftSentence,
    PassageCitation,
    SentenceKind,
    VerificationFinding,
)
from story.core.numerals import tokenize_numerals
# Full dotted paths, so this module does not route through the package `__init__` that imports
# it — see the same note in `deterministic.py`.
import story.stages.verification.citations as citation_rules
import story.stages.verification.language as language
from story.stages.verification.codes import finding
from story.stages.verification.package_index import PackageIndex

#: §13.14's superlative machinery. `Calculation.expression` is a free string, so the forms the
#: verifier can actually recompute are closed and anything else is refused. `unique_negative`
#: is here because it is the shape of §13.14's own worked attack — *"the only quarter in which
#: the company reported a negative adjusted gross margin"*, which is false:
#: `adjusted_gross_margin` is negative in **2022Q4 (−3.2) and 2023Q1 (−3.3)**.
EXTREMUM_EXPRESSIONS: frozenset[str] = frozenset(
    {"min", "max", "unique_negative", "unique_positive"})

#: §13.14's comparative machinery, in the same closed form.
COMPARISON_EXPRESSIONS: frozenset[str] = frozenset({"left > right", "left < right"})


def check_language(
    draft: Draft, index: PackageIndex, period_surfaces: Sequence[str]
) -> CheckResult:
    found: list[VerificationFinding] = []
    examined = 0
    for sentence in draft.sentences:
        examined += 1
        exempt = tuple(
            span for surface in period_surfaces
            for span in language.occurrences(sentence.text, surface))
        found.extend(_forward_looking_findings(sentence))
        found.extend(_causal_findings(sentence, index))
        found.extend(_connective_findings(sentence))
        found.extend(_claim_findings(sentence, index, exempt))
    return CheckResult(name="language_safety", examined=examined, findings=tuple(found))

def _forward_looking_findings(
    sentence: DraftSentence
) -> list[VerificationFinding]:
    return [
        finding(
            "forward_looking_language",
            sentence_index=sentence.index,
            char_start=match.start, char_end=match.end,
            expected="a statement about a reported period",
            observed=match.term,
            explanation=(
                "§13.15: all 2,704 observations are assertion_type reported and no lane "
                "emits guidance_issuance, so nothing in the package could support a "
                "forward-looking construction or contradict one. The narrative lane's two "
                "GUIDANCE_NOT_REPORTED refusals are the citable evidence that the question "
                "was asked and declined."),
        )
        for match in language.forward_looking(sentence.text)
    ]

def _causal_findings(
    sentence: DraftSentence, index: PackageIndex
) -> list[VerificationFinding]:
    markers = language.causal_markers(sentence.text)
    if not markers:
        return []
    passage_citations = [c for c in sentence.citations if isinstance(c, PassageCitation)]
    if sentence.kind is not SentenceKind.EXPLANATORY or not passage_citations:
        return [
            finding(
                "causal_construction_forbidden",
                sentence_index=sentence.index,
                char_start=match.start, char_end=match.end,
                expected="kind=explanatory with an attribution frame and a cited span",
                observed=f"kind={sentence.kind.value}, {len(passage_citations)} citation(s)",
                explanation=(
                    "§13.10 A: LLM-originated causation is banned unconditionally. §6.7 "
                    "makes it structural — event proximity may never imply causation."),
            )
            for match in markers
        ]

    found: list[VerificationFinding] = []
    frames = language.attribution_frames(sentence.text)
    if not frames:
        found.append(finding(
            "causal_attribution_frame_missing",
            sentence_index=sentence.index,
            char_start=markers[0].start, char_end=markers[0].end,
            expected="the company said | management attributed | according to the 10-Q | …",
            observed=sentence.text,
            explanation=(
                "§13.10 B condition 1: the frame must name the source in the sentence "
                "itself."),
        ))
    for citation in passage_citations:
        found.extend(_span_causal_findings(sentence, citation, frames, index))
    return found

def _span_causal_findings(
    sentence: DraftSentence,
    citation: PassageCitation,
    frames: Sequence[language.LexicalMatch],
    index: PackageIndex,
) -> list[VerificationFinding]:
    passage = index.passage(citation.passage_id)
    if passage is None:
        return []  # already refused as citation_not_in_package
    span = citation_rules.cited_span(passage, citation)
    if span is None:
        return []  # already refused as citation_span_not_in_passage
    handle = citation_rules.citation_id(citation)
    in_span = language.causal_markers(span)
    if not in_span:
        return [finding(
            "causal_marker_not_in_cited_span",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            expected="the causal marker inside the cited span",
            observed=span[:160],
            explanation=(
                "§13.10 B condition 2: passages run to thousands of characters and "
                "whole-passage containment would let any marker license any claim. The "
                "worked case: the filing says the reduction *followed* the pandemic, so "
                "\"the company attributed the reduction to the pandemic\" is refused — the "
                "frame is correct and `attributed…to` is not in the span."),
        )]
    found: list[VerificationFinding] = []
    for match in in_span:
        if language.negated(span, match):
            found.append(finding(
                "causal_marker_negated_in_span",
                sentence_index=sentence.index,
                citation_ids=(handle,),
                expected="an unnegated causal construction",
                observed=span[max(0, match.start - 40):match.end + 40],
                explanation=(
                    "§13.10 condition 5: 37 passages carry a negated causal construction — "
                    "\"not as a result of any general solicitation\" satisfies conditions "
                    "1–4 while the filing asserts the opposite."),
            ))
    if len(in_span) > 1:
        found.append(finding(
            "causal_marker_ambiguous_in_span",
            sentence_index=sentence.index,
            citation_ids=(handle,),
            expected="one causal marker in the cited span",
            observed=", ".join(match.term for match in in_span),
            explanation=(
                "§13.10 condition 6, in the only form the S0 draft contract can express: "
                "367 passages carry two or more markers, and a span holding \"X rose due to "
                "Y\" and \"Z fell as a result of W\" satisfies co-presence for a sentence "
                "joining Z to Y. Neither DraftSentence nor FactBinding carries a "
                "marker-occurrence selector, so a multi-marker span is refused outright."),
        ))
    for frame in frames:
        required = language.frame_document_type(frame.term)
        document = index.documents.get(passage.document_id)
        if not required or document is None:
            continue
        actual = document.document_type or document.form or ""
        if actual and actual.upper() != required.upper():
            found.append(finding(
                "causal_frame_document_mismatch",
                sentence_index=sentence.index,
                citation_ids=(handle,),
                char_start=frame.start, char_end=frame.end,
                expected=required, observed=actual,
                explanation="§13.10 B condition 4: the frame's noun names the cited document.",
            ))
    return found

def _connective_findings(sentence: DraftSentence) -> list[VerificationFinding]:
    """§13.14: *"a `connective` sentence may contain no claim"*."""
    if sentence.kind is not SentenceKind.CONNECTIVE:
        return []
    carried: list[str] = []
    if sentence.fact_bindings:
        carried.append(f"{len(sentence.fact_bindings)} fact binding(s)")
    if sentence.calculation is not None:
        carried.append(f"a calculation ({sentence.calculation.operation})")
    numerals = tokenize_numerals(sentence.text)
    if numerals:
        carried.append("numeral(s) " + ", ".join(token.text for token in numerals))
    if not carried:
        return []
    return [finding(
        "connective_sentence_carries_a_claim",
        sentence_index=sentence.index,
        expected="transition, structure and reference only",
        observed="; ".join(carried),
        explanation=(
            "§13.14: a connective sentence may only refer to what adjacent sentences "
            "already established. This is where V1 is most likely to be over-strict rather "
            "than under-strict, and that is the right direction for the failure to point."),
    )]

def _claim_findings(
    sentence: DraftSentence,
    index: PackageIndex,
    exempt: Sequence[language.LexicalMatch],
) -> list[VerificationFinding]:
    """§13.14's four constructions, each permitted only with its machinery behind it."""
    found: list[VerificationFinding] = []
    calculation = sentence.calculation
    operation = calculation.operation if calculation is not None else ""

    def outside(match: language.LexicalMatch) -> bool:
        return not any(span.contains(match.start, match.end) for span in exempt)

    for match in filter(outside, language.superlatives(sentence.text)):
        if operation != "extremum":
            found.append(finding(
                "unsupported_superlative",
                sentence_index=sentence.index,
                char_start=match.start, char_end=match.end,
                expected="kind=calculated with operation=extremum and the full comparison set",
                observed=match.term,
                explanation=(
                    "§13.14: \"the only quarter\" over a 26-quarter series means 26 input "
                    "ids and a recomputation. adjusted_gross_margin is negative in 2022Q4 "
                    "(−3.2) *and* 2023Q1 (−3.3); the same sentence about GAAP gross margin "
                    "is true, so the form is unfalsifiable by inspection."),
            ))
        break

    for match in filter(outside, language.comparatives(sentence.text)):
        if operation not in {"compare_levels", "compare_deltas"}:
            found.append(finding(
                "unsupported_comparative",
                sentence_index=sentence.index,
                char_start=match.start, char_end=match.end,
                expected="operation=compare_levels or compare_deltas with both sides' inputs",
                observed=match.term,
                explanation=(
                    "§13.14: the comparison is over deltas, which nothing else evaluates. "
                    "\"Contribution profit held up better than adjusted gross profit\" is "
                    "decided by $2M — CP falls $444M and AGP $446M over 2022Q2→Q3 — and no "
                    "check outside a recomputation looks at either number."),
            ))
        break

    for match in language.absence_claims(sentence.text):
        found.append(_absence_finding(sentence, match, index, operation))
        break

    for match in filter(outside, language.temporal_orderings(sentence.text)):
        if operation != "temporal_order":
            found.append(finding(
                "unsupported_temporal_ordering",
                sentence_index=sentence.index,
                char_start=match.start, char_end=match.end,
                expected="operation=temporal_order over two dated package items",
                observed=match.term,
                explanation=(
                    "§13.14: all three executive_change events carry occurred_on: null, so "
                    "\"the board changes took effect before the quarter closed\" asserts an "
                    "effective *ordering* where §13.8 already refuses an effective *date*. "
                    "A temporal token inside a declared period surface is exempt — that is "
                    "naming a period, not ordering two items."),
            ))
        break

    if operation == "extremum" and calculation is not None:
        found.extend(_extremum_findings(sentence, calculation, index))
    if operation in {"compare_levels", "compare_deltas"} and calculation is not None:
        found.extend(_comparison_findings(sentence, calculation, index))
    if operation == "temporal_order" and calculation is not None:
        found.extend(_temporal_findings(sentence, calculation, index))
    return found

def _absence_finding(
    sentence: DraftSentence,
    match: language.LexicalMatch,
    index: PackageIndex,
    operation: str,
) -> VerificationFinding:
    """§13.14's absence claim, which a bounded package can never license.

    §17's attack 7 is the shape: `revenue` has a `:Metric` node, a label, an external
    mapping and **170 `CONCERNS_METRIC` edges**, which a naive verifier reads as 170
    supporting passages. They are 170 places revenue was *refused*.
    """
    if operation != "absence":
        return finding(
            "unsupported_absence_claim",
            sentence_index=sentence.index,
            char_start=match.start, char_end=match.end,
            expected="operation=absence naming the metric and the window",
            observed=match.term,
            explanation=(
                "§13.14: an absence claim has nothing to bind and so nothing to refuse — "
                "§17.7's unpopulated_metric code is reached through §13.1 step 2, i.e. "
                "through a numeral."),
        )
    empty = [
        metric.metric_id for metric in index.package.metrics
        if not index.facts_for_metric(metric.metric_id)
    ]
    if empty:
        return finding(
            "unpopulated_metric",
            sentence_index=sentence.index,
            char_start=match.start, char_end=match.end,
            expected="a metric with observations in the package",
            observed=", ".join(empty),
            explanation=(
                "§17.7: a metric declared in the ontology and empty in the run cannot "
                "support an absence claim — its CONCERNS_METRIC edges are refusals, not "
                "evidence."),
        )
    return finding(
        "absence_not_provable_from_bounded_package",
        sentence_index=sentence.index,
        char_start=match.start, char_end=match.end,
        expected="a claim the package can establish",
        observed=match.term,
        explanation=(
            "§10.2 caps facts[] at twelve, so the package's own coverage establishes that "
            "*this package* holds no such value — never that the corpus does not. Refused "
            "rather than passed from a cap."),
    )

def _extremum_findings(
    sentence: DraftSentence, calculation: Calculation, index: PackageIndex
) -> list[VerificationFinding]:
    expression = calculation.expression.strip().lower()
    if expression not in EXTREMUM_EXPRESSIONS:
        return [finding(
            "extremum_expression_not_supported",
            sentence_index=sentence.index,
            expected=", ".join(sorted(EXTREMUM_EXPRESSIONS)),
            observed=calculation.expression,
            explanation=(
                "§13.14 requires the verifier to recompute the extremum over the exact "
                "input set, and Calculation.expression is a free string. The recomputable "
                "forms are closed here; anything else is refused rather than assumed."),
        )]
    facts = [index.fact(fact_id) for fact_id in calculation.input_observation_ids]
    values = [fact.value for fact in facts if fact is not None]
    if not values:
        return []
    if expression == "unique_negative":
        matching = [v for v in values if v < 0]
    elif expression == "unique_positive":
        matching = [v for v in values if v > 0]
    else:
        target = min(values) if expression == "min" else max(values)
        matching = [v for v in values if v == target]
    if len(matching) == 1:
        return []
    return [finding(
        "extremum_recomputation_failed",
        sentence_index=sentence.index,
        fact_ids=tuple(calculation.input_observation_ids),
        expected=f"exactly one member satisfying {expression}",
        observed=f"{len(matching)} of {len(values)}: {matching}",
        explanation=(
            "§13.14: the adjusted-gross-margin attack dies on recomputation — the metric is "
            "negative in 2022Q4 (−3.2) and 2023Q1 (−3.3)."),
    )]

def _comparison_findings(
    sentence: DraftSentence, calculation: Calculation, index: PackageIndex
) -> list[VerificationFinding]:
    expression = calculation.expression.strip().lower()
    if expression not in COMPARISON_EXPRESSIONS:
        return [finding(
            "extremum_expression_not_supported",
            sentence_index=sentence.index,
            expected=", ".join(sorted(COMPARISON_EXPRESSIONS)),
            observed=calculation.expression,
            explanation="§13.14: the comparative's direction must be recomputable.",
        )]
    facts = [index.fact(fact_id) for fact_id in calculation.input_observation_ids]
    if any(fact is None for fact in facts):
        return []
    values = [fact.value for fact in facts if fact is not None]
    if calculation.operation == "compare_levels":
        left, right = values[0], values[1]
    else:
        left, right = values[1] - values[0], values[3] - values[2]
    holds = left > right if expression == "left > right" else left < right
    if holds:
        return []
    return [finding(
        "comparative_recomputation_failed",
        sentence_index=sentence.index,
        fact_ids=tuple(calculation.input_observation_ids),
        expected=expression,
        observed=f"left={left!r}, right={right!r}",
        explanation="§13.14: the verifier recomputes both sides and checks the direction.",
    )]

def _temporal_findings(
    sentence: DraftSentence, calculation: Calculation, index: PackageIndex
) -> list[VerificationFinding]:
    undated: list[str] = []
    for item_id in calculation.input_observation_ids:
        event = index.events.get(item_id)
        fact = index.fact(item_id)
        if event is not None and event.occurred_on is None:
            undated.append(item_id)
        elif event is None and fact is None:
            undated.append(item_id)
    if not undated:
        return []
    return [finding(
        "date_not_in_package",
        sentence_index=sentence.index,
        fact_ids=tuple(calculation.input_observation_ids),
        expected="two dated package items",
        observed=", ".join(undated),
        explanation=(
            "§13.8: three of six events carry occurred_on: null — all three "
            "executive_change — so a temporal ordering over them cannot be satisfied, "
            "which is the correct outcome."),
    )]

def check_title(draft: Draft) -> CheckResult:
    """`Draft.title` is model-written prose that **no binding covers**.

    §12 specifies `fact_bindings`, a `calculation` and `citations` per *sentence*; the
    title has none of the three, so every §13 check that runs through a binding skips it
    entirely. That is a hole a generator can walk through — *"Opendoor's worst quarter
    ever"* carries no numeral, no binding and no citation and would otherwise reach a
    published post unexamined. Since nothing can back a title claim, every construction
    §13.14 and §13.15 name is refused there unconditionally, and **any numeral in a title
    is unbound by construction**.
    """
    if not draft.title:
        return CheckResult(name="title", examined=0)
    found: list[VerificationFinding] = []
    for token in tokenize_numerals(draft.title):
        found.append(finding(
            "unbound_numeral",
            char_start=token.start, char_end=token.end,
            expected="no numeral in the title, which carries no fact_binding",
            observed=token.text,
            explanation=(
                "§12 gives bindings, calculations and citations to sentences, not to the "
                "title. A number nothing can bind is a number nothing checked."),
        ))
    scans = (
        ("forward_looking_language", language.forward_looking(draft.title)),
        ("causal_construction_forbidden", language.causal_markers(draft.title)),
        ("foreign_subject_named", language.foreign_subjects(draft.title)),
        ("unsupported_superlative", language.superlatives(draft.title)),
        ("unsupported_comparative", language.comparatives(draft.title)),
        ("unsupported_absence_claim", language.absence_claims(draft.title)),
    )
    for code, matches in scans:
        for match in matches:
            found.append(finding(
                code,
                char_start=match.start, char_end=match.end,
                expected="a title that states no claim of its own",
                observed=match.term,
                explanation=(
                    "§13.14 and §13.15 refuse these constructions wherever they appear. In "
                    "a title there is no calculation and no citation that could license "
                    "one, so the refusal is unconditional."),
            ))
            break
    return CheckResult(name="title", examined=1, findings=tuple(found))

__all__ = [
    "COMPARISON_EXPRESSIONS",
    "EXTREMUM_EXPRESSIONS",
    "check_language",
    "check_title",
]
