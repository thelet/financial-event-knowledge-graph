"""§13's deterministic layer: the authority the model may tighten and never loosen.

Responsibility: given a draft, the package it was written from and the plan that asked for it,
decide. Twelve checks, each with a denominator, all of them pure functions of the three
arguments — **no database, no model server, no filesystem, no clock**. `DeterministicVerifier`
satisfies `story.contracts.DraftVerifier` and is constructible with nothing.

Ten of the twelve are here: identity and freshness, numbers, units, percentages, periods,
metric identity, subject identity, citations, reported-versus-calculated and disclosures. The
other two — `language_safety` and `title` — are `claims.py`'s, because *"does this number match
this fact"* and *"may this sentence say this at all"* are different questions with nothing in
common but the package index.

**What makes the demo claim trustworthy is that this file cannot be talked out of a refusal.**
Severity is never chosen at a call site: every finding is built through
`codes.finding`, which reads §13.17's gate, so *"one REFUSE refuses the draft"* is a property
of one table rather than of sixty call sites. `VerifiedDraft.passed` is derived from the
findings, and `CheckResult.outcome` is derived from `examined` and `findings`, so a draft with
no numeric sentences reports `numbers: NOT_APPLICABLE` and never `numbers: PASS`.

**§13.1's tolerance, in the corrected form and no other.** Magnitudes are compared —
`||V_draft| − |V_fact|| ≤ 0.5 × 10^(e−d+1)` — and **sign agreement is a separate check applied
only when the numeral itself carried a sign**. A published numeral is usually unsigned:
*"a loss of $27.1 million"* puts the sign in a word the tokeniser does not read, and the signed
form gives 54,175,000 against a ±50,000 window. Written the naive way it fails all **825**
negative-valued observations in this corpus. The arithmetic lives in `story/core/numerals.py`
(S9a, accepted) and this module reaches for it rather than restating it — `tokenize_numerals`,
`compare_token_to_fact`, `reconstruct_table_quote`, `matches_at_printed_precision`,
`percent_delta`, `relative_change_across_zero`, `find_ambiguous_percent_changes` and
`surface_supports_operation` are all called here and none of them is reimplemented.

**One place §13 could not be executed as written**, recorded rather than quietly bridged.
**§13.9 requires calculation inputs *"sharing metric and unit"*, and that clause refuses the
founder's own demo candidate**: `cross-metric-divergence` computes `adjusted_gross_margin`
minus `gaap_gross_margin`, whose whole content is that the metrics differ. Implemented as
**sharing unit and period shape**; the metric-sharing clause is not implemented and that is a
plan defect, not a relaxation made for convenience — a same-metric requirement would refuse
every cross-metric story the detector exists to find. `claims.py` records the two others,
against §13.10 and §13.14.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from story.core.keys import draft_content_sha256, package_content_digest
from story.core.models import (
    Calculation,
    CalculationLedgerEntry,
    CheckResult,
    Draft,
    DraftSentence,
    EditorialPlan,
    FactBinding,
    FactLedgerEntry,
    PackagedFact,
    PassageCitation,
    SentenceKind,
    StoryEvidencePackage,
    VerificationFinding,
    VerifiedDraft,
)
from story.core.numerals import (
    CHANGE_VERBS,
    NumeralToken,
    PercentOperation,
    RelativeChangeAcrossZero,
    SurfaceUnit,
    compare_token_to_fact,
    find_ambiguous_percent_changes,
    matches_at_printed_precision,
    percent_delta,
    relative_change_across_zero,
    surface_supports_operation,
    tokenize_numerals,
)
from story.core.periods import classify_shape
# Submodules imported by their full dotted path, the way `graph_tools` imports `cypher`: a
# `from story.stages.verification import ...` would route through this package's own
# `__init__`, which imports this module, and `test_the_story_package_has_no_import_cycle`
# reads that as the cycle it is.
import story.stages.verification.citations as citation_rules
import story.stages.verification.claims as claim_rules
import story.stages.verification.language as language
import story.stages.verification.period_grammar as period_grammar
from story.stages.verification.codes import finding
from story.stages.verification.metric_surfaces import MetricAliasIndex
from story.stages.verification.package_index import PackageIndex

#: §13.2's map, closed and total over the corpus's four units. `contracts` is deliberately
#: absent: both contract metrics carry `unit: "homes"` (S9a's finding against §13.2's
#: five-surface list). A surface not in this map makes no unit claim and the binding supplies
#: the unit — that is `SurfaceUnit.NONE`, a bare numeral.
SURFACE_UNITS: Mapping[SurfaceUnit, str] = {
    SurfaceUnit.USD: "USD",
    SurfaceUnit.PERCENT: "percent",
    SurfaceUnit.HOMES: "homes",
    SurfaceUnit.MARKETS: "markets",
}

#: Surfaces a *change* carries. §13.3's second gate: no observation in the package is a change
#: — the extraction refused all 186 it saw — so a fact binding rendering one of these is
#: binding a derived quantity to a reported row.
CHANGE_SURFACES: frozenset[SurfaceUnit] = frozenset(
    {SurfaceUnit.PERCENTAGE_POINTS, SurfaceUnit.BASIS_POINTS, SurfaceUnit.MULTIPLE})

#: What a `Calculation.operation` may be, and how many inputs it takes. An operation outside
#: this map is `calculation_operation_not_supported` rather than an unchecked pass: §13.9 says
#: recompute, and a recomputation rule that abstains on an unknown operation is a hole the
#: writer chooses the name of.
OPERATION_INPUTS: Mapping[str, tuple[int, int]] = {
    "delta_pp": (2, 2),
    "delta_bps": (2, 2),
    "delta_relative": (2, 2),
    "difference": (2, 2),
    "ratio": (2, 2),
    "sum": (2, 12),
    "extremum": (1, 40),
    "compare_levels": (2, 2),
    "compare_deltas": (4, 4),
    "absence": (0, 40),
    "temporal_order": (2, 2),
}

#: §13's *"required qualifiers present"*. A plan may require a warning to reach the post; a
#: warning code is not prose, so each code declares the phrases that count as having said it.
#: A code absent from this table is `required_warning_has_no_declared_qualifier` — a **refusal**
#: rather than a pass, so a new package warning cannot enter the pipeline and be satisfied by
#: silence.
REQUIRED_WARNING_QUALIFIERS: Mapping[str, tuple[str, ...]] = {
    "filing_date_unknown": ("filing date", "date it was filed", "as-filed date",
                            "when it was filed"),
    "counter_evidence_same_document": ("same filing", "elsewhere in the filing",
                                       "another table in the same"),
    "conflicting_values": ("conflict", "two values", "two readings", "also reported"),
    "warned_observation": ("flagged", "carries a warning", "data-quality"),
}

_CHANGE_VERB = re.compile(
    r"\b(?:" + "|".join(sorted(CHANGE_VERBS, key=len, reverse=True)) + r")\b", re.IGNORECASE)


@dataclass
class _Ledgers:
    """The evidence panel and the derivation panel, accumulated as the checks resolve ids."""

    facts: list[FactLedgerEntry] = field(default_factory=list)
    calculations: list[CalculationLedgerEntry] = field(default_factory=list)


class DeterministicVerifier:
    """§13.1–§13.15, run in one pass. Satisfies `story.contracts.DraftVerifier`.

    The three freshness arguments are the **expected** identity, supplied by whatever resolved
    it — §7's staleness gate in the pipeline, a constant in a test. They are optional and their
    absence is visible: an expectation that was never supplied is not counted in the identity
    check's `examined`, so a verification that could not check freshness cannot report that it
    did. That is the same discipline `CheckResult.outcome` applies to every other check.

    `literal_ok` is §13.1's allowlist — ordinals and a metric's own `threshold_value` such as
    *"120 days"*. Empty by default: a numeral nobody declared is `unbound_numeral`, and an
    allowlist that shipped with entries would be a set of numbers the verifier stops looking at.
    """

    def __init__(
        self,
        *,
        graph_run_id: str | None = None,
        run_complete_sha256: str | None = None,
        ontology_definition_hash: str | None = None,
        literal_ok: Sequence[str] = (),
        required_warning_qualifiers: Mapping[str, tuple[str, ...]] | None = None,
    ) -> None:
        self._graph_run_id = graph_run_id
        self._run_complete_sha256 = run_complete_sha256
        self._ontology_definition_hash = ontology_definition_hash
        self._literal_ok = tuple(literal_ok)
        self._qualifiers = dict(
            REQUIRED_WARNING_QUALIFIERS if required_warning_qualifiers is None
            else required_warning_qualifiers)

    # -- the contract -------------------------------------------------------------------

    def verify(
        self, draft: Draft, package: StoryEvidencePackage, plan: EditorialPlan
    ) -> VerifiedDraft:
        index = PackageIndex(package)
        aliases = MetricAliasIndex.from_package(package)
        ledgers = _Ledgers()
        # Both places a draft may declare a period: on a binding, and on a calculation — the
        # second because §13.9 gives a `calculated` sentence no bindings, so a derivation could
        # not otherwise say which of its own words name the period it computed over.
        period_surfaces = tuple(sorted(
            {binding.period_surface
             for sentence in draft.sentences for binding in sentence.fact_bindings
             if binding.period_surface}
            | {sentence.calculation.period_surface for sentence in draft.sentences
               if sentence.calculation is not None and sentence.calculation.period_surface}
        ))

        checks = (
            self._check_identity(draft, package, plan, index),
            self._check_numbers(draft, index, ledgers),
            self._check_units(draft, index),
            self._check_percentages(draft, index),
            self._check_periods(draft, index),
            self._check_metric_identity(draft, index, aliases),
            self._check_subject_identity(draft, index),
            self._check_citations(draft, index, aliases),
            self._check_reported_vs_calculated(draft, index, ledgers),
            claim_rules.check_language(draft, index, aliases, period_surfaces),
            claim_rules.check_title(draft, index),
            self._check_disclosures(draft, package, plan, index),
        )
        return VerifiedDraft(
            candidate_id=draft.candidate_id,
            package_identity=package.identity,
            draft_content_sha256=draft_content_sha256(draft.digestible_payload()),
            checks=checks,
            fact_ledger=tuple(ledgers.facts),
            calculation_ledger=tuple(ledgers.calculations),
        )

    # -- §13.13 identity and freshness ---------------------------------------------------

    def _check_identity(
        self,
        draft: Draft,
        package: StoryEvidencePackage,
        plan: EditorialPlan,
        index: PackageIndex,
    ) -> CheckResult:
        """Identity, freshness and *"the draft references only package facts"* (§13.13).

        The package hash is **recomputed**, not read: §10.3's digest is taken over the package
        *minus* the field that holds it (S0 finding F6), so comparing the stored field against
        itself would be an identity that always holds. A stale package hash is §17's attack 8
        — a real node, a real quote and a real document from a run that has been superseded —
        and this is the check that refuses it.
        """
        found: list[VerificationFinding] = []
        examined = 3  # the three ids a draft and a plan carry about their own package

        if draft.candidate_id != package.candidate_id:
            found.append(finding(
                "candidate_id_mismatch",
                expected=package.candidate_id, observed=draft.candidate_id,
                explanation="§13.13: the draft's verified_against block must equal the package's."))
        if draft.package_id != package.package_id:
            found.append(finding(
                "package_id_mismatch",
                expected=package.package_id, observed=draft.package_id,
                explanation="§13.13."))
        if (plan.package_id, plan.candidate_id) != (package.package_id, package.candidate_id):
            found.append(finding(
                "plan_names_another_package",
                expected=f"{package.candidate_id} / {package.package_id}",
                observed=f"{plan.candidate_id} / {plan.package_id}",
                explanation=(
                    "§13: the plan is an input to verification — a dropped required_warning is "
                    "a refusal about what the plan asked for — so a plan built against another "
                    "package cannot judge this draft."),
            ))

        recomputed = package_content_digest(package.digestible_payload())
        examined += 1
        if recomputed != package.package_content_digest:
            found.append(finding(
                "package_content_digest_mismatch",
                expected=recomputed,
                observed=package.package_content_digest or "(unstamped)",
                explanation=(
                    "§10.3: the digest is sha256 over the package's own canonical JSON with "
                    "this field excluded, so it is recomputed here rather than trusted. A "
                    "package whose rows no longer hash to their own digest is not the package "
                    "the draft was written from."),
            ))

        for label, expected, observed, code in (
            ("graph_run_id", self._graph_run_id, package.graph_run_id, "graph_run_id_mismatch"),
            ("run_complete_sha256", self._run_complete_sha256, package.run_complete_sha256,
             "graph_input_digest_mismatch"),
            ("ontology_definition_hash", self._ontology_definition_hash,
             package.ontology_definition_hash, "graph_input_digest_mismatch"),
        ):
            if expected is None:
                continue
            examined += 1
            if expected != observed:
                found.append(finding(
                    code, expected=f"{label}={expected}", observed=f"{label}={observed}",
                    explanation=(
                        "§13.13: run_complete_sha256 is the field that actually identifies the "
                        "inputs — §18 measured one extraction_run_id naming two different sets "
                        "of bytes two days apart. A package naming a superseded run is refused, "
                        "not repaired."),
                ))

        for sentence in draft.sentences:
            for binding in sentence.fact_bindings:
                examined += 1
                if binding.fact_id in index.events:
                    found.append(finding(
                        "event_property_bound_as_fact",
                        sentence_index=sentence.index,
                        char_start=binding.char_start, char_end=binding.char_end,
                        fact_ids=(binding.fact_id,),
                        expected="an observation id",
                        observed=f"{binding.fact_id} is an event",
                        explanation=(
                            "§13.8: event properties are free-text strings — "
                            "`charge_amount: \"approximately $15 million\"` is not a typed "
                            "value. The only permitted use is quoting the string verbatim."),
                    ))
                elif index.fact(binding.fact_id) is None:
                    found.append(finding(
                        "fact_not_in_package",
                        sentence_index=sentence.index,
                        char_start=binding.char_start, char_end=binding.char_end,
                        fact_ids=(binding.fact_id,),
                        expected="an observation_id in the package's facts[]",
                        observed=binding.fact_id,
                        explanation="§13.13: every fact_binding id must resolve in the package.",
                        suggested_fact_ids=[f.observation_id for f in index.package.facts],
                    ))
        return CheckResult(name="identity_and_freshness", examined=examined,
                           findings=tuple(found))

    # -- §13.1 numbers -------------------------------------------------------------------

    def _check_numbers(
        self, draft: Draft, index: PackageIndex, ledgers: _Ledgers
    ) -> CheckResult:
        """Every numeral accounted for, and every bound one inside §13.1's window."""
        found: list[VerificationFinding] = []
        examined = 0

        for sentence in draft.sentences:
            tokens = tokenize_numerals(sentence.text)
            examined += len(tokens)
            covering = self._covering_spans(sentence)
            for token in tokens:
                if any(span.contains(token.start, token.end) for span in covering):
                    continue
                found.append(finding(
                    "unbound_numeral",
                    sentence_index=sentence.index,
                    char_start=token.start, char_end=token.end,
                    expected=("a fact_binding span, a calculation result, a period surface or "
                              "the literal_ok allowlist"),
                    observed=token.text,
                    explanation=(
                        "§13.1: a numeral nobody declared is a claim nobody checked. Matching a "
                        "bare numeral back to a fact is hopeless — measured over the corpus it "
                        "is ambiguous 98.9% of the time — so the writer declares and the "
                        "verifier checks."),
                    suggested_fact_ids=[f.observation_id for f in index.package.facts],
                ))

            for binding in sentence.fact_bindings:
                found.extend(self._binding_number_findings(sentence, binding, index, ledgers))

        return CheckResult(name="numbers", examined=examined, findings=tuple(found))

    def _binding_number_findings(
        self,
        sentence: DraftSentence,
        binding: FactBinding,
        index: PackageIndex,
        ledgers: _Ledgers,
    ) -> list[VerificationFinding]:
        found: list[VerificationFinding] = []
        fact = index.fact(binding.fact_id)
        if fact is None:
            return found  # already refused as fact_not_in_package or event_property_bound_as_fact

        if sentence.text[binding.char_start:binding.char_end] != binding.rendered:
            found.append(finding(
                "binding_span_does_not_match_text",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(fact.observation_id,),
                expected=binding.rendered,
                observed=sentence.text[binding.char_start:binding.char_end],
                explanation=(
                    "§12: the binding declares which span of its own text states the fact. A "
                    "span that does not hold the rendering it claims points the whole of §13 "
                    "at the wrong characters."),
            ))
            return found

        token = self._sole_numeral(binding.rendered)
        if token is None:
            found.append(finding(
                "binding_rendering_is_not_one_numeral",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(fact.observation_id,),
                expected="exactly one numeral in the rendered span",
                observed=binding.rendered,
                explanation=(
                    "§13.1 compares the draft's numeral against the fact at the draft's own "
                    "precision, and `d` is undefined for a span holding none or two."),
            ))
            return found

        verdict = compare_token_to_fact(token, fact.value, fact_printed_form=fact.quoted_text)
        if not verdict.within_window:
            found.append(finding(
                "number_outside_tolerance",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(fact.observation_id,),
                expected=f"{fact.value!r} ({fact.metric_id} {fact.period_key})",
                observed=(f"{binding.rendered} reads {token.value!r}; "
                          f"||draft|−|fact|| = {verdict.difference!r} > {verdict.window!r}"),
                explanation=(
                    "§13.1: printed-precision half-ulp on magnitudes, "
                    "0.5 × 10^(e − d + 1) with d = "
                    f"{verdict.draft_significant_figures} as the draft wrote it."),
                suggested_fact_ids=self._suggestions(index, fact),
            ))
        if verdict.sign_explicit and not verdict.sign_agrees:
            found.append(finding(
                "sign_disagreement",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(fact.observation_id,),
                expected=("negative" if fact.value < 0 else "positive") + f" ({fact.value!r})",
                observed=binding.rendered,
                explanation=(
                    "§13.1: sign is a separate check and applies because this numeral carried "
                    "its own sign. An unsigned numeral is a claim about magnitude — "
                    "\"a loss of $27.1 million\" puts the sign in prose the tokeniser does not "
                    "read — which is why the window is computed on magnitudes."),
            ))
        if verdict.over_precise:
            found.append(finding(
                "over_precision",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(fact.observation_id,),
                expected=f"at most {verdict.fact_significant_figures} significant figures",
                observed=f"{binding.rendered} carries {verdict.draft_significant_figures}",
                explanation=(
                    "§13.1: WARN. The printed form compared against is this fact's own "
                    f"quoted_text {fact.quoted_text!r}; a fact can have two printings and the "
                    "library never guesses which applies."),
            ))

        ledgers.facts.append(FactLedgerEntry(
            fact_id=fact.observation_id, metric_id=fact.metric_id, period_key=fact.period_key,
            value=fact.value, unit=fact.unit, rendered=binding.rendered,
            sentence_index=sentence.index, passage_id=fact.passage_id,
            document_id=fact.document_id, source_url=fact.source_url,
            evidence_source_id=fact.evidence_source_id,
        ))
        return found

    def _covering_spans(
        self, sentence: DraftSentence
    ) -> tuple[language.LexicalMatch, ...]:
        """§13.1's four ways a numeral may be accounted for, as spans in this sentence.

        A calculation's `period_surface` covers the same way a binding's does, and for the same
        §13.1 reason — *"a year in `in 2022` is not a fact"*. Covering it here is not trusting
        it: `_check_periods` resolves the declared surface through §13.4's grammar and requires
        it to agree with every input observation, so a derivation naming the wrong period is
        refused by that check rather than admitted by this one.
        """
        spans = [language.LexicalMatch(term=b.rendered, start=b.char_start,
                                       end=b.char_end)
                 for b in sentence.fact_bindings]
        needles = [b.period_surface for b in sentence.fact_bindings if b.period_surface]
        needles.extend(self._literal_ok)
        if sentence.calculation is not None:
            if sentence.calculation.result_rendered:
                needles.append(sentence.calculation.result_rendered)
            if sentence.calculation.period_surface:
                needles.append(sentence.calculation.period_surface)
        for needle in needles:
            spans.extend(language.occurrences(sentence.text, needle))
        return tuple(spans)

    @staticmethod
    def _sole_numeral(rendered: str) -> NumeralToken | None:
        tokens = tokenize_numerals(rendered)
        return tokens[0] if len(tokens) == 1 else None

    @staticmethod
    def _suggestions(index: PackageIndex, fact: PackagedFact) -> list[str]:
        """§13.17's *"up to five package facts that would satisfy the sentence"*.

        Same metric first, then same period: those are the two rebinds a writer can actually
        make, and a list ordered by anything else would be a search dressed as a choice.
        """
        same_metric = [f.observation_id for f in index.package.facts
                       if f.metric_id == fact.metric_id and f.observation_id != fact.observation_id]
        same_period = [f.observation_id for f in index.package.facts
                       if f.period_key == fact.period_key and f.observation_id != fact.observation_id]
        ordered: list[str] = []
        for candidate in (*same_metric, *same_period):
            if candidate not in ordered:
                ordered.append(candidate)
        return ordered

    # -- §13.2 units ---------------------------------------------------------------------

    def _check_units(self, draft: Draft, index: PackageIndex) -> CheckResult:
        found: list[VerificationFinding] = []
        examined = 0
        for sentence in draft.sentences:
            for binding in sentence.fact_bindings:
                fact = index.fact(binding.fact_id)
                if fact is None:
                    continue
                examined += 1
                if fact.unit == "USD" and fact.currency is None:
                    found.append(finding(
                        "monetary_unit_without_currency",
                        sentence_index=sentence.index,
                        fact_ids=(fact.observation_id,),
                        expected="a currency on a USD-unit observation",
                        observed="currency is absent",
                        explanation=(
                            "§13.2: fires on zero rows today — currency is non-null on exactly "
                            "the 997 USD rows — and exists so an XBRL lane cannot introduce one "
                            "silently."),
                    ))
                token = self._sole_numeral(binding.rendered)
                if token is None:
                    continue
                if token.currency_symbol and fact.unit != "USD":
                    found.append(finding(
                        "currency_symbol_on_non_monetary_unit",
                        sentence_index=sentence.index,
                        char_start=binding.char_start, char_end=binding.char_end,
                        fact_ids=(fact.observation_id,),
                        expected=f"no currency symbol on a {fact.unit} observation",
                        observed=binding.rendered,
                        explanation="§13.2.",
                    ))
                if token.unit in CHANGE_SURFACES:
                    found.append(finding(
                        "unit_mismatch",
                        sentence_index=sentence.index,
                        char_start=binding.char_start, char_end=binding.char_end,
                        fact_ids=(fact.observation_id,),
                        expected=f"a level in {fact.unit}",
                        observed=f"{binding.rendered} renders a change ({token.unit.value})",
                        explanation=(
                            "§13.3's second gate: no observation in the package is a change — "
                            "the extraction refused all 186 it saw (174 DERIVED_CHANGE_COLUMN + "
                            "12 DERIVED_COMPARISON) — so a change surface bound to a reported "
                            "row is a derived quantity wearing a citation."),
                    ))
                    continue
                claimed = SURFACE_UNITS.get(token.unit)
                if claimed is not None and claimed != fact.unit:
                    found.append(finding(
                        "unit_mismatch",
                        sentence_index=sentence.index,
                        char_start=binding.char_start, char_end=binding.char_end,
                        fact_ids=(fact.observation_id,),
                        expected=fact.unit, observed=claimed,
                        explanation="§13.2: the corpus has four units and the map is closed.",
                    ))
        return CheckResult(name="units", examined=examined, findings=tuple(found))

    # -- §13.3 percentages ---------------------------------------------------------------

    def _check_percentages(self, draft: Draft, index: PackageIndex) -> CheckResult:
        """*"The verifier never infers which quantity a change sentence claims — the draft
        declares it and the verifier recomputes it."*"""
        found: list[VerificationFinding] = []
        examined = 0
        for sentence in draft.sentences:
            percent_facts = self._percent_facts(sentence, index)
            calculation = sentence.calculation
            operation = _percent_operation(calculation)
            if not percent_facts and operation is None:
                continue
            examined += 1

            for ambiguity in find_ambiguous_percent_changes(sentence.text):
                found.append(finding(
                    "percent_change_ambiguous",
                    sentence_index=sentence.index,
                    char_start=ambiguity.numeral.start, char_end=ambiguity.numeral.end,
                    fact_ids=tuple(fact.observation_id for fact in percent_facts),
                    expected="an explicit percentage point | pp | bps marker, or a relative marker",
                    observed=ambiguity.clause.strip(),
                    explanation=(
                        "§13.3: the two readings differ by 100/|v1| and the gap is "
                        "base-dependent — on adjusted_ebitda_margin 5.2 → 2.2 by 19.2×, and on "
                        "the same pair read forward by 45.5×. This is unresolvable, not "
                        "imprecise."),
                ))

            if operation is not None and calculation is not None:
                if not surface_supports_operation(operation, calculation.result_rendered):
                    found.append(finding(
                        "percentage_point_surface_missing",
                        sentence_index=sentence.index,
                        expected=_required_surface(operation),
                        observed=calculation.result_rendered,
                        explanation=(
                            "§13.3: each reading has its own required rendering. delta_relative "
                            "needs both halves — a %-suffixed numeral *and* an explicit relative "
                            "marker — because the % alone is exactly what makes it ambiguous."),
                    ))
                found.extend(self._across_zero_findings(sentence, calculation, operation, index))

            if sentence.kind is SentenceKind.REPORTED and percent_facts:
                found.extend(self._reported_change_findings(sentence, percent_facts))

        return CheckResult(name="percentages", examined=examined, findings=tuple(found))

    def _percent_facts(
        self, sentence: DraftSentence, index: PackageIndex
    ) -> tuple[PackagedFact, ...]:
        ids = [binding.fact_id for binding in sentence.fact_bindings]
        if sentence.calculation is not None:
            ids.extend(sentence.calculation.input_observation_ids)
        facts = [index.fact(fact_id) for fact_id in ids]
        return tuple(fact for fact in facts if fact is not None and fact.unit == "percent")

    def _across_zero_findings(
        self,
        sentence: DraftSentence,
        calculation: Calculation,
        operation: PercentOperation,
        index: PackageIndex,
    ) -> list[VerificationFinding]:
        if operation is not PercentOperation.DELTA_RELATIVE:
            return []
        values = [index.fact(i) for i in calculation.input_observation_ids]
        if len(values) != 2 or any(fact is None for fact in values):
            return []
        earlier, later = values[0].value, values[1].value  # type: ignore[union-attr]
        if not relative_change_across_zero(earlier, later):
            return []
        return [finding(
            "relative_change_across_zero",
            sentence_index=sentence.index,
            fact_ids=tuple(calculation.input_observation_ids),
            expected="delta_pp or delta_bps",
            observed=f"delta_relative over {earlier!r} → {later!r}",
            explanation=(
                "§13.3's third gate. adjusted_ebitda_margin crosses zero six times across its "
                "26 quarters, so this is the common case for the metric a percent story is most "
                "likely to be about. (−6.3 − 5.2)/|5.2| = −221% is arithmetically defined and "
                "rhetorically meaningless."),
        )]

    def _reported_change_findings(
        self, sentence: DraftSentence, percent_facts: Sequence[PackagedFact]
    ) -> list[VerificationFinding]:
        verb = _CHANGE_VERB.search(sentence.text)
        if verb is None:
            return []
        covered = [language.LexicalMatch(term=b.rendered, start=b.char_start,
                                        end=b.char_end)
                   for b in sentence.fact_bindings]
        for token in tokenize_numerals(sentence.text):
            if token.unit not in (SurfaceUnit.PERCENT, SurfaceUnit.PERCENTAGE_POINTS,
                                  SurfaceUnit.BASIS_POINTS):
                continue
            if any(span.contains(token.start, token.end) for span in covered):
                continue
            return [finding(
                "percent_change_reported_not_calculated",
                sentence_index=sentence.index,
                char_start=token.start, char_end=token.end,
                fact_ids=tuple(fact.observation_id for fact in percent_facts),
                expected="kind=calculated with a Calculation",
                observed=f"kind=reported, change verb {verb.group(0)!r}, numeral {token.text!r}",
                explanation=(
                    "§13.3's second gate: a change of a percent metric may never be a reported "
                    "sentence. No observation in the package is a change."),
            )]
        return []

    # -- §13.4 periods -------------------------------------------------------------------

    def _check_periods(self, draft: Draft, index: PackageIndex) -> CheckResult:
        found: list[VerificationFinding] = []
        examined = 0
        for sentence in draft.sentences:
            for binding in sentence.fact_bindings:
                fact = index.fact(binding.fact_id)
                if fact is None:
                    continue
                examined += 1
                resolved = period_grammar.resolve(binding.period_surface)
                if not resolved.resolved:
                    found.append(finding(
                        "period_unresolvable",
                        sentence_index=sentence.index,
                        char_start=binding.char_start, char_end=binding.char_end,
                        fact_ids=(fact.observation_id,),
                        expected="a surface in §13.4's closed grammar",
                        observed=binding.period_surface or "(empty)",
                        explanation=(
                            "§13.4: resolved through a closed grammar, never a free date "
                            "parser. \"the quarter\" is UNRESOLVABLE and a bare year is not in "
                            "the grammar — it is the shape §13.7.1 measures as ambiguous on "
                            "61.3% of observations."),
                    ))
                    continue
                if _endpoints_agree(resolved, fact):
                    continue
                conflated = _same_anchor(resolved, fact)
                found.append(finding(
                    "period_shape_conflated" if conflated else "period_mismatch",
                    sentence_index=sentence.index,
                    char_start=binding.char_start, char_end=binding.char_end,
                    fact_ids=(fact.observation_id,),
                    expected=(f"{fact.period_key} "
                              f"({fact.period_start or fact.instant_date}"
                              f"..{fact.period_end or fact.instant_date})"),
                    observed=(f"{binding.period_surface!r} resolves to {resolved.key} "
                              f"({resolved.period_start or resolved.instant_date}"
                              f"..{resolved.period_end or resolved.instant_date})"),
                    explanation=(
                        "§13.4: matching period_end alone would be catastrophic — 188 "
                        "(metric, period_end) pairs carry more than one period_key, and "
                        "adjusted_ebitda ending 2022-09-30 is +$183M for the nine-month YTD and "
                        "−$211M for Q3. Quarter-vs-YTD conflation gets its own code because "
                        "that is the finding a writer can act on."
                        if conflated else
                        "§13.4: exact equality on both endpoints and on kind."),
                    suggested_fact_ids=self._suggestions(index, fact),
                ))
            calculation_findings, calculation_examined = self._calculation_period_findings(
                sentence, index)
            examined += calculation_examined
            found.extend(calculation_findings)
        return CheckResult(name="periods", examined=examined, findings=tuple(found))

    def _calculation_period_findings(
        self, sentence: DraftSentence, index: PackageIndex
    ) -> tuple[list[VerificationFinding], int]:
        """§13.4 applied to `Calculation.period_surface`, which §13.9 leaves a sentence's only
        way to name the period it computed over.

        **Agreement with *every* input, not with one of them.** A cross-metric gap computes over
        one period and names it; a quarter-over-quarter delta computes over two and can name
        neither with a single surface, so it is refused here rather than allowed to present one
        of its two windows as the sentence's period. That refuses some true sentences and
        admits no false one, which is the direction §13.14 says the failure should point — and
        it is not a regression, because before this field existed such a sentence could not
        name a period at all.
        """
        calculation = sentence.calculation
        if calculation is None or not calculation.period_surface:
            return [], 0
        facts = [fact for fact in
                 (index.fact(fact_id) for fact_id in calculation.input_observation_ids)
                 if fact is not None]
        if not facts:
            return [], 0  # already refused as calculation_inputs_unresolved
        resolved = period_grammar.resolve(calculation.period_surface)
        if not resolved.resolved:
            return [finding(
                "period_unresolvable",
                sentence_index=sentence.index,
                fact_ids=tuple(fact.observation_id for fact in facts),
                expected="a surface in §13.4's closed grammar",
                observed=calculation.period_surface,
                explanation=(
                    "§13.4: a calculation's period surface is resolved through the same closed "
                    "grammar a binding's is. A bare year is not in the grammar, and a surface "
                    "the grammar cannot read is a period nothing checked."),
            )], 1
        disagreeing = [fact for fact in facts if not _endpoints_agree(resolved, fact)]
        if not disagreeing:
            return [], 1
        conflated = all(_same_anchor(resolved, fact) for fact in disagreeing)
        return [finding(
            "period_shape_conflated" if conflated else "period_mismatch",
            sentence_index=sentence.index,
            fact_ids=tuple(fact.observation_id for fact in disagreeing),
            expected=", ".join(sorted({fact.period_key for fact in facts})),
            observed=(f"{calculation.period_surface!r} resolves to {resolved.key} "
                      f"({resolved.period_start or resolved.instant_date}"
                      f"..{resolved.period_end or resolved.instant_date})"),
            explanation=(
                "§13.4: the period a derivation declares must be the period its inputs were "
                "read over, on both endpoints and on kind. adjusted_ebitda ending 2022-09-30 is "
                "+$183M for the nine-month YTD and −$211M for the quarter, so a surface that "
                "matched the anchor alone would name a different number."),
        )], 1

    # -- §13.5 metric identity -----------------------------------------------------------

    def _check_metric_identity(
        self, draft: Draft, index: PackageIndex, aliases: MetricAliasIndex
    ) -> CheckResult:
        found: list[VerificationFinding] = []
        examined = 0
        for sentence in draft.sentences:
            for binding in sentence.fact_bindings:
                fact = index.fact(binding.fact_id)
                if fact is None:
                    continue
                examined += 1
                resolution = aliases.resolve(binding.metric_surface)
                common = dict(
                    sentence_index=sentence.index,
                    char_start=binding.char_start, char_end=binding.char_end,
                    fact_ids=(fact.observation_id,),
                )
                if not resolution.resolved:
                    found.append(finding(
                        "metric_surface_unresolved", **common,
                        expected="a surface in the alias index",
                        observed=binding.metric_surface or "(empty)",
                        explanation="§13.5: a surface that resolves to nothing is refused."))
                elif resolution.declared_ambiguous:
                    found.append(finding(
                        "metric_surface_ambiguous", **common,
                        expected="a surface naming one metric",
                        observed=(f"{binding.metric_surface!r} resolves through "
                                  f"{resolution.matched!r} to "
                                  f"{', '.join(resolution.metric_ids)}"),
                        explanation=(
                            "§13.5: eight surfaces are declared ambiguous. gaap_gross_margin's "
                            "own label *is* \"Gross Margin\", so the surface a writer would "
                            "naturally use is the ambiguous one — a post must say \"GAAP gross "
                            "margin\"."),
                    ))
                elif resolution.shared_groups:
                    found.append(finding(
                        "mutually_distinct_group_ambiguity", **common,
                        expected="metrics from different mutually_distinct_groups",
                        observed=(f"{', '.join(resolution.metric_ids)} share "
                                  f"{', '.join(resolution.shared_groups)}"),
                        explanation="§13.5's third refusal."))
                elif fact.metric_id not in resolution.metric_ids:
                    found.append(finding(
                        "metric_binding_mismatch", **common,
                        expected=(f"the surface to resolve to {fact.metric_id}"),
                        observed=(f"{binding.metric_surface!r} resolves through "
                                  f"{resolution.matched!r} to "
                                  f"{', '.join(resolution.metric_ids)}"),
                        explanation=(
                            "§13.5 with longest match: \"gross margin\" ⊂ \"adjusted gross "
                            "margin\", so a first-match scanner assigns \"adjusted gross margin "
                            "was 13.2%\" to gaap_gross_margin — a wrong metric with a plausible "
                            "number, which is §17's attack 1. Both margins read 15.4 at 2020Q4."),
                        suggested_fact_ids=self._suggestions(index, fact),
                    ))
        return CheckResult(name="metric_identity", examined=examined, findings=tuple(found))

    # -- §13.6 / §13.11 subject ----------------------------------------------------------

    def _check_subject_identity(self, draft: Draft, index: PackageIndex) -> CheckResult:
        """*"Any named subject other than Opendoor is an automatic refusal today."*"""
        found: list[VerificationFinding] = []
        examined = 0
        unresolved = _unresolved_participants(index)
        for sentence in draft.sentences:
            examined += 1
            for match in language.foreign_subjects(sentence.text):
                found.append(finding(
                    "foreign_subject_named",
                    sentence_index=sentence.index,
                    char_start=match.start, char_end=match.end,
                    expected=index.package.subject.entity_id,
                    observed=match.term,
                    explanation=(
                        "§13.6: all 2,704 observations carry subject_entity_id \"opendoor\". "
                        "mortgage_rate and home_price_appreciation are declared and empty, so a "
                        "comparative post is not verifiable and must not be drafted."),
                ))
            cited = {c.passage_id for c in sentence.citations if isinstance(c, PassageCitation)}
            for entity_id, entity_text, passage_id in unresolved:
                if passage_id not in cited or entity_text in sentence.text:
                    continue
                found.append(finding(
                    "unresolved_entity_named",
                    sentence_index=sentence.index,
                    expected=f"the verbatim entity_text {entity_text!r}",
                    observed=sentence.text,
                    explanation=(
                        f"§13.11: {entity_id} is scoped-unresolved — any entity id containing "
                        "'#evt:' is unresolved by construction. \"Opendoor entered into the "
                        "facility\" is a refusal: the borrower is the subsidiary and the graph "
                        "refuses to guess."),
                ))
        return CheckResult(name="subject_identity", examined=examined, findings=tuple(found))

    # -- §13.7 citations -----------------------------------------------------------------

    def _check_citations(
        self, draft: Draft, index: PackageIndex, aliases: MetricAliasIndex
    ) -> CheckResult:
        found: list[VerificationFinding] = []
        examined = 0
        seen: dict[tuple[str, int, int], citation_rules.CitationUse] = {}
        for sentence in draft.sentences:
            sentence_findings, uses, count = citation_rules.check_sentence_citations(
                sentence, index, aliases, seen)
            found.extend(sentence_findings)
            examined += count
            for key, use in citation_rules.index_uses(uses).items():
                seen.setdefault(key, use)

            needs_citation = (
                sentence.kind is SentenceKind.EXPLANATORY
                or (sentence.kind is SentenceKind.REPORTED and sentence.fact_bindings)
            )
            if needs_citation and not sentence.citations:
                examined += 1
                found.append(finding(
                    "uncited_factual_sentence",
                    sentence_index=sentence.index,
                    fact_ids=tuple(b.fact_id for b in sentence.fact_bindings),
                    expected="at least one citation",
                    observed="none",
                    explanation=(
                        "§13.7: a reported sentence states what a filing said, and an "
                        "explanatory one paraphrases it. Neither is checkable without the span."),
                ))
        return CheckResult(name="citations", examined=examined, findings=tuple(found))

    # -- §13.9 reported versus calculated ------------------------------------------------

    def _check_reported_vs_calculated(
        self, draft: Draft, index: PackageIndex, ledgers: _Ledgers
    ) -> CheckResult:
        found: list[VerificationFinding] = []
        examined = 0
        for sentence in draft.sentences:
            calculation = sentence.calculation
            # A sentence is examined here when the reported/calculated distinction applies to
            # it at all: it declared one of the two kinds, or it carries a derivation. A
            # `connective` sentence with neither is not examined, and the denominator says so.
            if (sentence.kind in (SentenceKind.REPORTED, SentenceKind.CALCULATED)
                    or calculation is not None):
                examined += 1
            if sentence.kind is SentenceKind.CALCULATED:
                if calculation is None:
                    found.append(finding(
                        "calculated_sentence_without_calculation",
                        sentence_index=sentence.index,
                        expected="a Calculation", observed="none",
                        explanation="§13.9."))
                if any(isinstance(c, PassageCitation) for c in sentence.citations):
                    found.append(finding(
                        "calculated_sentence_cites_passage",
                        sentence_index=sentence.index,
                        citation_ids=tuple(citation_rules.citation_id(c)
                                           for c in sentence.citations),
                        expected="no passage citation",
                        observed=f"{len(sentence.citations)} citation(s)",
                        explanation=(
                            "§13.9: claims.yaml gives `calculated` optional_fields: [] — no "
                            "filed-passage field is permitted. A calculated value that cites a "
                            "passage is claiming the filing said something it did not. It is "
                            "enforced three times over upstream; this is the fourth."),
                    ))
            elif sentence.kind is SentenceKind.REPORTED and calculation is not None:
                found.append(finding(
                    "reported_sentence_carries_calculation",
                    sentence_index=sentence.index,
                    expected="no Calculation on a reported sentence",
                    observed=calculation.operation,
                    explanation="§13.9."))

            if calculation is not None:
                found.extend(self._calculation_findings(sentence, calculation, index, ledgers))
        return CheckResult(name="reported_vs_calculated", examined=examined,
                           findings=tuple(found))

    def _calculation_findings(
        self,
        sentence: DraftSentence,
        calculation: Calculation,
        index: PackageIndex,
        ledgers: _Ledgers,
    ) -> list[VerificationFinding]:
        found: list[VerificationFinding] = []
        bounds = OPERATION_INPUTS.get(calculation.operation)
        if bounds is None:
            return [finding(
                "calculation_operation_not_supported",
                sentence_index=sentence.index,
                expected=", ".join(sorted(OPERATION_INPUTS)),
                observed=calculation.operation,
                explanation=(
                    "§13.9 says recompute. An operation the verifier cannot recompute is a hole "
                    "whose name the writer would choose."),
            )]

        inputs = [index.fact(fact_id) for fact_id in calculation.input_observation_ids]
        missing = [fact_id for fact_id, fact in zip(calculation.input_observation_ids, inputs)
                   if fact is None]
        if missing or not (bounds[0] <= len(inputs) <= bounds[1]):
            return [finding(
                "calculation_inputs_unresolved",
                sentence_index=sentence.index,
                fact_ids=tuple(calculation.input_observation_ids),
                expected=f"{bounds[0]}–{bounds[1]} input ids, all resolving in the package",
                observed=(f"{len(inputs)} given"
                          + (f"; unresolved: {', '.join(missing)}" if missing else "")),
                explanation="§13.9 and §13.13.",
            )]
        resolved = [fact for fact in inputs if fact is not None]

        found.extend(self._comparability_findings(sentence, calculation, resolved))
        found.extend(self._formula_window_findings(sentence, calculation, index, resolved))
        found.extend(self._recompute_findings(sentence, calculation, resolved, ledgers))
        return found

    def _comparability_findings(
        self,
        sentence: DraftSentence,
        calculation: Calculation,
        inputs: Sequence[PackagedFact],
    ) -> list[VerificationFinding]:
        """§13.9's input requirement, minus the clause that refuses the demo candidate.

        §13.9 asks for *"≥2 resolving inputs sharing metric and unit"*. **Sharing metric is not
        implemented and the omission is deliberate**: `cross-metric-divergence` — the founder's
        selected candidate — computes `adjusted_gross_margin` minus `gaap_gross_margin`, so a
        same-metric requirement would refuse the story the demo exists to tell. Unit and period
        shape are required, which is what the clause was protecting.
        """
        # `extremum`, `absence` and `temporal_order` range over a set or over dates and have no
        # two-sided comparability question. The two comparisons do: their result is a gap, and a
        # gap between a percent and a dollar figure — or between a quarter and a year-to-date —
        # is the incomparability §13.9 and §13.4 exist to refuse.
        if calculation.operation in {"extremum", "absence", "temporal_order"}:
            return []
        units = {fact.unit for fact in inputs}
        if len(units) > 1:
            return [finding(
                "calculation_inputs_incomparable",
                sentence_index=sentence.index,
                fact_ids=tuple(fact.observation_id for fact in inputs),
                expected="one unit across the inputs",
                observed=", ".join(sorted(units)),
                explanation="§13.9.",
            )]
        shapes = {classify_shape(f.period_start, f.period_end, f.instant_date) for f in inputs}
        if len(shapes) > 1:
            return [finding(
                "incomparable_periods",
                sentence_index=sentence.index,
                fact_ids=tuple(fact.observation_id for fact in inputs),
                expected="one period shape across the inputs",
                observed=", ".join(sorted(shape.value for shape in shapes)),
                explanation=(
                    "§13.4: a calculation whose inputs have different shapes is "
                    "incomparable_periods. The three cross-year windows in this run classify as "
                    "`other` and R3 refuses them."),
            )]
        return []

    def _formula_window_findings(
        self,
        sentence: DraftSentence,
        calculation: Calculation,
        index: PackageIndex,
        inputs: Sequence[PackagedFact],
    ) -> list[VerificationFinding]:
        """§13.9: the `formula_version_id` must be valid **for the period computed over**.

        Checked against the package's own `formula_windows[]` rather than against the ontology,
        for the reason the whole stage is package-only: `check_formula_for_date` reads a YAML
        directory and this verifier is constructible with nothing. `formula_version_id` is null
        for an arithmetic derivation and the check abstains.
        """
        if calculation.formula_version_id is None:
            return []
        windows = {w.version_id: w for w in index.package.formula_windows}
        window = windows.get(calculation.formula_version_id)
        anchors = [f.period_end or f.instant_date or "" for f in inputs]
        if window is None:
            return [finding(
                "formula_version_not_valid_for_period",
                sentence_index=sentence.index,
                expected="a version_id in the package's formula_windows[]",
                observed=calculation.formula_version_id,
                explanation="§13.9, and §10's formula_windows[] is what carries the window."),
            ]
        outside = [
            anchor for anchor in anchors
            if (window.valid_from and anchor < window.valid_from)
            or (window.valid_to and anchor > window.valid_to)
        ]
        if not outside:
            return []
        return [finding(
            "formula_version_not_valid_for_period",
            sentence_index=sentence.index,
            fact_ids=tuple(fact.observation_id for fact in inputs),
            expected=f"anchors within [{window.valid_from}, {window.valid_to}]",
            observed=", ".join(outside),
            explanation=(
                "§13.9: validity is checked for the period computed over, not the filing date. "
                "§6.9's P6 measured five observations that end before any declared window "
                "starts, and the ontology declares only two."),
        )]

    def _recompute_findings(
        self,
        sentence: DraftSentence,
        calculation: Calculation,
        inputs: Sequence[PackagedFact],
        ledgers: _Ledgers,
    ) -> list[VerificationFinding]:
        """Recompute with exact arithmetic and compare **after rounding, never by equality**.

        §13.9's own float-residue example (`5.2 − 2.2`) is exactly 3.0 in IEEE-754 and
        demonstrates nothing; the real residues from this corpus do — `9.9 − 7.3 =
        2.6000000000000005`, `13.2 − 9.9 = 3.299999999999999`, and the spike value
        `3.3 − 13.2 = −9.899999999999999`. `15.9` itself is stored as `15.899999999999999`.

        **A comparison is recomputed here too, and it was not before.** §13.14 owns the
        comparison's *direction* and this owns its *size*: `"15.9 percentage points lower than"`
        renders a gap, the gap is a scalar over the same inputs, and a rendered numeral that
        nothing recomputes is a number nothing checked — the very thing §13.1 exists to stop.
        The size is the absolute gap because the sign of a comparison lives in its wording, not
        in its numeral: *"15.9 points lower"* and *"15.9 points higher"* are the same magnitude
        and different claims, and `claims.py` is what decides which of the two the sentence
        made. `extremum`, `absence` and `temporal_order` still have no scalar result and are
        still §13.14's alone.
        """
        operation = calculation.operation
        if operation in {"extremum", "absence", "temporal_order"}:
            return []  # §13.14 owns these; there is no scalar result to compare
        try:
            computed = _recompute(operation, [fact.value for fact in inputs])
        except RelativeChangeAcrossZero:
            return []  # already refused by §13.3's third gate
        if operation in {"compare_levels", "compare_deltas"} and not calculation.result_rendered:
            # A comparison may state its direction and no size — *"contribution profit held up
            # better"*. Nothing is claimed numerically, so there is nothing to recompute and
            # nothing for `_covering_spans` to have covered.
            return []
        token = self._sole_numeral(calculation.result_rendered)
        if token is None:
            return [finding(
                "calculation_does_not_recompute",
                sentence_index=sentence.index,
                fact_ids=tuple(fact.observation_id for fact in inputs),
                expected=f"{computed!r}",
                observed=f"result_rendered {calculation.result_rendered!r} holds no one numeral",
                explanation="§13.9: the comparison is at the draft's own printed precision.",
            )]
        if not matches_at_printed_precision(
                computed, token.value, decimals=token.decimals_written):
            return [finding(
                "calculation_does_not_recompute",
                sentence_index=sentence.index,
                fact_ids=tuple(fact.observation_id for fact in inputs),
                expected=f"{round(computed, token.decimals_written)!r}",
                observed=f"{calculation.result_rendered} reads {token.value!r}",
                explanation=(
                    f"§13.9: {calculation.expression} over "
                    f"{[fact.value for fact in inputs]} recomputes to {computed!r}."),
            )]
        ledgers.calculations.append(CalculationLedgerEntry(
            sentence_index=sentence.index, operation=operation,
            input_observation_ids=tuple(calculation.input_observation_ids),
            expression=calculation.expression, recomputed_value=computed,
            rendered=calculation.result_rendered,
            formula_version_id=calculation.formula_version_id,
        ))
        return []

    # -- §11 / §13.12 disclosures --------------------------------------------------------

    def _check_disclosures(
        self,
        draft: Draft,
        package: StoryEvidencePackage,
        plan: EditorialPlan,
        index: PackageIndex,
    ) -> CheckResult:
        found: list[VerificationFinding] = []
        examined = 0
        text = " ".join(sentence.text for sentence in draft.sentences).lower()

        for code in plan.required_warnings:
            examined += 1
            phrases = self._qualifiers.get(code)
            if phrases is None:
                found.append(finding(
                    "required_warning_has_no_declared_qualifier",
                    expected="a declared qualifier phrase for this warning code",
                    observed=code,
                    explanation=(
                        "A warning code is not prose, so each code declares the phrases that "
                        "count as having said it. An undeclared code refuses rather than "
                        "passing: silence must not satisfy a disclosure."),
                ))
            elif not any(phrase.lower() in text for phrase in phrases):
                found.append(finding(
                    "required_warning_absent",
                    expected=" | ".join(phrases), observed=code,
                    explanation=(
                        "§12: the writer must not omit a required_warning, and §10.1's caveats "
                        "are the reason the package carries them. A paraphrase of a caveat is a "
                        "new claim, so the qualifier is checked literally."),
                ))

        bound = {b.fact_id for s in draft.sentences for b in s.fact_bindings}
        cited = {c.passage_id for s in draft.sentences for c in s.citations
                 if isinstance(c, PassageCitation)}
        for counterpoint in plan.counterpoints:
            examined += 1
            if (set(counterpoint.required_fact_ids) & bound
                    or set(counterpoint.required_citation_passage_ids) & cited):
                continue
            found.append(finding(
                "required_counterpoint_absent",
                expected=("one of "
                          + ", ".join((*counterpoint.required_fact_ids,
                                       *counterpoint.required_citation_passage_ids))),
                observed="the draft binds none of them",
                explanation=(
                    "§17.14: requiring counterpoints in the *plan* was satisfiable with one "
                    "ungrounded sentence, and no §13 check required one to survive into the "
                    "draft. This is the check that closes it."),
            ))

        for sentence in draft.sentences:
            for binding in sentence.fact_bindings:
                fact = index.fact(binding.fact_id)
                if fact is None:
                    continue
                examined += 1
                if fact.warning_codes:
                    found.append(finding(
                        "warned_observation_used",
                        sentence_index=sentence.index,
                        char_start=binding.char_start, char_end=binding.char_end,
                        fact_ids=(fact.observation_id,),
                        expected="the warning surfaced in the evidence panel",
                        observed=", ".join(fact.warning_codes),
                        explanation=(
                            "§13.13: any binding to one of the 185 :Warned observations must "
                            "surface the warning beside the claim."),
                    ))
                found.extend(self._conflict_findings(sentence, binding, fact, index))
            for citation in sentence.citations:
                if not isinstance(citation, PassageCitation):
                    continue
                for event in package.events:
                    if event.passage_id != citation.passage_id or not event.review_flag:
                        continue
                    examined += 1
                    found.append(finding(
                        "event_review_flag",
                        sentence_index=sentence.index,
                        citation_ids=(citation_rules.citation_id(citation),),
                        expected="the flag annotated beside the claim",
                        observed=f"{event.event_id}: {event.review_flag}",
                        explanation=(
                            "§13.8: ANNOUNCEMENT_EQUALS_OCCURRENCE must be annotated and may "
                            "never be presented as evidence of anything."),
                    ))
        return CheckResult(name="disclosures", examined=examined, findings=tuple(found))

    def _conflict_findings(
        self,
        sentence: DraftSentence,
        binding: FactBinding,
        fact: PackagedFact,
        index: PackageIndex,
    ) -> list[VerificationFinding]:
        """§13.12's precision-relative materiality rule, derived from the package's own rows.

        Never from a constant: *"a hard-coded count would pass a draft verified against a
        different run."* On the current run all 36 conflicted slots are rounding twins that
        vanish at 2–3 significant figures; on the stale graph `market_count 2021-03-31` held 27
        and 44, which separate at one significant figure and could never be used silently.
        """
        conflict = index.conflicts.get(index.slot_of(fact))
        if conflict is None or len(conflict.clusters) < 2:
            return []
        token = self._sole_numeral(binding.rendered)
        if token is None:
            return []
        others = [cluster.value for cluster in conflict.clusters
                  if round(cluster.value, 6) != round(fact.value, 6)]
        immaterial = all(
            matches_at_printed_precision(other, fact.value, decimals=token.decimals_written)
            for other in others
        )
        if immaterial:
            return [finding(
                "conflict_immaterial_at_stated_precision",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(fact.observation_id,),
                expected=f"{fact.value!r}", observed=", ".join(repr(v) for v in others),
                explanation=(
                    "§13.12: every other value rounds to the same thing at the draft's own "
                    "precision, so the conflict is annotated rather than disclosed."),
            )]
        disclosed = all(
            any(matches_at_printed_precision(other, token_in.value,
                                             decimals=token_in.decimals_written)
                for token_in in tokenize_numerals(sentence.text))
            for other in others
        )
        if disclosed:
            return []
        return [finding(
            "conflict_not_disclosed",
            sentence_index=sentence.index,
            char_start=binding.char_start, char_end=binding.char_end,
            fact_ids=(fact.observation_id,),
            expected="both values in the sentence, with the named document",
            observed=f"{binding.rendered} alone; the slot also holds "
                     + ", ".join(repr(v) for v in others),
            explanation=(
                "§13.12: the disclosure clause is a closed template bound to the slot so it "
                "cannot be pasted decoratively — both values must appear and the chosen value "
                "must be the bound observation's."),
        )]


# ---------------------------------------------------------------------------------------
# Free functions the checks share
# ---------------------------------------------------------------------------------------


def _percent_operation(calculation: Calculation | None) -> PercentOperation | None:
    if calculation is None:
        return None
    try:
        return PercentOperation(calculation.operation)
    except ValueError:
        return None


def _required_surface(operation: PercentOperation) -> str:
    if operation is PercentOperation.DELTA_PP:
        return "percentage point(s) | pp"
    if operation is PercentOperation.DELTA_BPS:
        return "bps | basis points"
    return "a %-suffixed numeral AND an explicit relative marker"


def _recompute(operation: str, values: Sequence[float]) -> float:
    """The scalar operations §13.9 recomputes.

    Input order is `(base, subject)` for every two-input operation: `delta_pp(v0, v1)` is
    `v1 − v0`, which is `story.core.numerals`' own signature and the only ordering that makes
    a quarter-over-quarter delta and a cross-metric gap read the same way.

    The two comparisons return the **absolute** gap, which is what a comparative sentence
    renders: *"15.9 percentage points lower than"* states a size and puts its sign in the word
    `lower`. `claims.py` checks that word against the same values, so the direction is not lost
    by taking the magnitude here — it is checked by the rule that owns direction.
    """
    if operation in {"delta_pp", "delta_bps", "delta_relative"}:
        return percent_delta(PercentOperation(operation), values[0], values[1])
    if operation == "difference":
        return values[1] - values[0]
    if operation == "ratio":
        return values[1] / values[0]
    if operation == "compare_levels":
        return abs(values[1] - values[0])
    if operation == "compare_deltas":
        return abs((values[1] - values[0]) - (values[3] - values[2]))
    return float(sum(values))


def _endpoints_agree(resolved: period_grammar.PeriodSurface, fact: PackagedFact) -> bool:
    """§13.4: exact equality on both endpoints **and on kind**."""
    if resolved.is_instant or fact.instant_date is not None:
        return resolved.instant_date == fact.instant_date
    return (resolved.period_start, resolved.period_end) == (fact.period_start, fact.period_end)


def _same_anchor(resolved: period_grammar.PeriodSurface, fact: PackagedFact) -> bool:
    """The surface and the fact share an end date but not a window — quarter versus YTD."""
    surface_anchor = resolved.period_end or resolved.instant_date
    fact_anchor = fact.period_end or fact.instant_date
    return bool(surface_anchor) and surface_anchor == fact_anchor


def _unresolved_participants(index: PackageIndex) -> tuple[tuple[str, str, str], ...]:
    """Every unresolved event participant, with its licensed surface and its passage.

    Detection is **by id, not by heuristic** (§13.11): any entity id containing `#evt:` is
    scoped-unresolved by construction, and `resolved: false` says the same thing on the row.
    """
    found: list[tuple[str, str, str]] = []
    for event in index.package.events:
        for participant in event.participants:
            if participant.resolved and "#evt:" not in participant.entity_id:
                continue
            found.append((
                participant.entity_id,
                participant.entity_text or "",
                event.passage_id or "",
            ))
    return tuple(found)


__all__ = [
    "CHANGE_SURFACES",
    "OPERATION_INPUTS",
    "REQUIRED_WARNING_QUALIFIERS",
    "SURFACE_UNITS",
    "DeterministicVerifier",
]
