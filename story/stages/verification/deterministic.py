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
    DerivedFact,
    Draft,
    DraftSentence,
    EditorialPlan,
    EvidenceScopeFact,
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
    operation_result_surfaces,
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
import story.stages.verification.derived_facts as derived_rules
import story.stages.verification.language as language
import story.stages.verification.period_grammar as period_grammar
from story.stages.verification.codes import finding
from story.stages.verification.metric_surfaces import MetricAliasIndex
from story.stages.verification.package_index import (
    DERIVED_FACT_PREFIX,
    EVIDENCE_SCOPE_PREFIX,
    PackageIndex,
)

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
#:
#: **DETERMINISTIC_FACT_TOOLS §6 stops this being a blanket refusal and makes it a refusal
#: *against a level*, and the distinction is the whole of what S13 changed about §13.2.** These
#: three surfaces are refused wherever they render an **observation**, exactly as before, because
#: the measurement behind that refusal has not moved: 174 `DERIVED_CHANGE_COLUMN` and 12
#: `DERIVED_COMPARISON` rows were refused at extraction and no observation in any package is a
#: change. What is new is that a `DerivedFact` *may* be `percentage_points` or `multiple` — those
#: are precisely the quantities §4.1 exists to compute — so a numeral bound to one is checked
#: against `derived_facts.DERIVED_SURFACES` instead. `basis_points` is in neither map: the same
#: quantity at a hundred times the number is not a rendering, it is a different claim.
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
#:
#: **Three of the four original keys name codes no package can carry, measured 2026-08-05.**
#: `plan_violations` refuses a `required_warning` that is not on `package.warnings`
#: (`unknown_warning_code`), and `warning_codes.SEVERITY_OF` is the closed set of codes a package
#: may carry: `filing_date_unknown` is a *canonicalisation* warning on an observation
#: (`detection/canonicalization.py`), and `conflicting_values` and `warned_observation` are
#: plan-era spellings that never became package codes. Only `counter_evidence_same_document` was
#: ever reachable through the real pipeline. The three are kept — they are constructible on a
#: hand-built package and several tests build one — and the two whose *prose* survived the rename
#: are re-keyed onto the codes that actually fire, below.
#:
#: **S6 added three entries, and each is a repair rather than new policy.**
#:
#: * `fact_conflict_disclosed` is what `conflicting_values` was renamed to. Its live detail reads
#:   *"2 distinct readings, 1 cluster(s), classified presentation_rounding"* and the phrase list
#:   already said *"two readings"*. Fires on 58 of 262 packages.
#: * `unpreferred_source_lane` is what `warned_observation` was renamed to — `warning_codes`
#:   defines it as *"a used fact whose `validation_state` is `warned`"*, its live detail reads
#:   `validation_state=warned on adjusted_ebitda 2022Q2 (lane normalized_narrative)`, and §13's
#:   own neighbouring check is still called `warned_observation_used`. Fires on 16 of 262.
#: * `single_source` is the one entry written here rather than moved, and it is written because
#:   the code's whole meaning is in the code — *"one document reports this slot"*, with nothing in
#:   `detail` a sentence would need. It fires on 47 of 262 and it now carries the entire
#:   source-count disclosure by itself, since its former mirror `concordant_readings_collapsed`
#:   became build provenance at S6.
#:
#: Nothing else was given a phrase list, and `QUALIFIER_NOT_DECLARED` below says why for each.
REQUIRED_WARNING_QUALIFIERS: Mapping[str, tuple[str, ...]] = {
    "filing_date_unknown": ("filing date", "date it was filed", "as-filed date",
                            "when it was filed"),
    "counter_evidence_same_document": ("same filing", "elsewhere in the filing",
                                       "another table in the same"),
    "conflicting_values": ("conflict", "two values", "two readings", "also reported"),
    "warned_observation": ("flagged", "carries a warning", "data-quality"),
    "fact_conflict_disclosed": ("conflict", "two values", "two readings", "also reported"),
    "unpreferred_source_lane": ("flagged", "carries a warning", "data-quality"),
    "single_source": ("one filing", "one document", "a single filing", "a single document",
                      "single source"),
}

#: The claim-qualifying codes that declare **no** phrase, with the reason each was left that way.
#:
#: A code in here still refuses: a plan that names it gets
#: `required_warning_has_no_declared_qualifier` from `_check_disclosures`, exactly as before. The
#: table changes nothing at runtime and exists so the choice is *made* rather than defaulted into.
#: `tests/story/test_story_warning_taxonomy.py` requires this and `REQUIRED_WARNING_QUALIFIERS`
#: together to cover every `CLAIM_QUALIFYING` code in `warning_codes.KIND_OF`, so the seventeenth
#: claim qualifier cannot arrive latent the way the sixteenth did (S6's Task 1 finding: sixteen
#: claim-qualifying codes, one reachable declared qualifier, and the newest of the sixteen live on
#: the demo package).
#:
#: Three reasons recur and each is stated per code below:
#:
#: * **container** — the code is a envelope and its meaning is in `detail`. One phrase list would
#:   be either vacuous or wrong: it would accept a sentence about a different contained warning.
#: * **refuses first** — the code is `Severity.REFUSE`, so §13.17's gate stops any draft built on
#:   a package carrying it. A qualifier could never be checked against an accepted post.
#: * **never observed** — zero of the 262 candidates this graph run can package carry it
#:   (measured 2026-08-05). A phrase list would be written from imagination rather than from a
#:   draft, and §4 S7 is the stage that produces drafts.
QUALIFIER_NOT_DECLARED: Mapping[str, str] = {
    "metric_ambiguity_declared": (
        "container: the ambiguity is in `detail` — `pct_120_days_denominator` is three "
        "denominator wordings, `homes_sold_recognition_point` is a recognition point — and the "
        "sentence each needs is different. §13.15's `lost_qualifier` model check is the rule "
        "aimed at this, and a phrase list here would let a post satisfy one ambiguity by "
        "writing about another. Fires on 30 of 262 packages"),
    "candidate_warning": (
        "container: `detail` carries the detector's own code — `relative_change_across_zero`, "
        "`cohort_vs_period_basis`, `divergence_population_excludes_periods` — and they demand "
        "different sentences. Fires on 54 of 262 packages"),
    "canonical_point_warning": (
        "container: `detail` carries the canonicalisation layer's own code (`lane_defect`, "
        "`minority_reading_present`, `slot_unit_disagreement`). Never observed: 0 of 262"),
    "comparison_warned": (
        "container: the disclosure is the comparability rule's own sentence and there is one "
        "per rule — the only live example is `cohort_vs_period_basis`'s 300-character "
        "explanation of why a cohort measure and a period measure do not subtract. Fires on 2 "
        "of 262 packages"),
    "counter_evidence_same_passage": (
        "the obligation is already structural and stronger: §11 requires a grounded "
        "`counterpoint` for every `counter_evidence[]` row, and `counter_evidence_unaccounted` "
        "refuses a plan that leaves one unused and undeclared. A phrase on top of that would "
        "make the post name the *grain* the row was matched at, which is build detail. Its "
        "weaker sibling `counter_evidence_same_document` keeps its phrases because that grain "
        "is what a reader has to be told. Fires on 3 of 262 packages"),
    "population_definition_differs": (
        "never observed: 0 of 262. The prose is obvious — a denominator changed — and writing "
        "it before a package has ever carried the code would be a policy nobody measured"),
    "formula_window_boundary_crossed": "never observed: 0 of 262",
    "event_date_absent": (
        "never observed: 0 of 262, because `events[]` is empty on every package this corpus "
        "builds"),
    "event_review_flag": "never observed: 0 of 262, for `event_date_absent`'s reason",
    "slot_unresolved": (
        "never observed: 0 of 262 — and a slot whose canonical status is `conflict` emits no "
        "value, so there is no packaged fact for a sentence to qualify"),
    "entity_unresolved": (
        "refuses first: `Severity.REFUSE`, and §13.11 separately refuses a draft that renders "
        "an unresolved entity as a name"),
    "comparison_refused": "refuses first: `Severity.REFUSE`",
}

_CHANGE_VERB = re.compile(
    r"\b(?:" + "|".join(sorted(CHANGE_VERBS, key=len, reverse=True)) + r")\b", re.IGNORECASE)

#: The sentence kinds whose **prose** is read against their `fact_bindings` (§13.4 and §13.5).
#:
#: `reported` and `explanatory` are the two kinds that carry bindings and state a fact in words:
#: §13.7's `uncited_factual_sentence` already names exactly this pair as *"a reported sentence
#: states what a filing said, and an explanatory one paraphrases it"*. R8 grounded `reported`
#: alone and recorded the omission; a paraphrase is if anything the easier place to move the
#: metric or the period without touching a declaration, so the pair is grounded together.
#:
#: The other two are exempt for reasons, not for symmetry. A `connective` sentence carrying any
#: binding is already `connective_sentence_carries_a_claim` — a REFUSE — so a grounding rule
#: there could only ever add a second code to a draft already refused. §13.9 gives a
#: `calculated` sentence no bindings and its period is grounded through
#: `Calculation.period_surface` instead; that **nothing refuses a binding on a calculated
#: sentence** is a separate hole, recorded rather than closed here, because closing it is a
#: §13.9 rule and not a grounding rule.
#:
#: **DETERMINISTIC_FACT_TOOLS §6 voided the `calculated` exemption's premise and the exemption
#: goes with it — for a derived binding only.** A `calculated` sentence now carries
#: `fact_bindings` to derived facts and `Calculation` is retired, so *"its period is grounded
#: through `Calculation.period_surface` instead"* is no longer true of it. Period grounding
#: therefore runs for **every** derived binding whatever the sentence's kind
#: (`_derived_period_findings`), which is a strengthening: the demo's original refusal was a
#: period the model forgot to declare, and this is the rule that reads the one it wrote instead.
#:
#: **The metric half followed at H1, and the sentence that used to stand here was the hole.** It
#: read *"the set below is unchanged, because it governs the metric rule as well and a
#: `calculated` sentence's metric grounding is a separate question nobody has measured"*. It has
#: been measured: bound to a derivation of `adjusted_gross_profit`, *"Adjusted gross **margin**
#: fell $446 million"* — a percent metric stated in dollars — and *"**Revenue** fell $446
#: million"* were both **accepted with zero findings**, and so were the same two sentences
#: declared `reported`, so this was never only the `calculated` exemption. `metric_identity` now
#: runs `_derived_metric_grounding_findings` for every derived binding whatever the kind, on the
#: same footing as the period rule. **The set below is genuinely unchanged now**: it governs
#: only the two *observed* rules, and both exemptions above still hold for them.
GROUNDED_SENTENCE_KINDS: frozenset[SentenceKind] = frozenset(
    {SentenceKind.REPORTED, SentenceKind.EXPLANATORY})


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
        self,
        draft: Draft,
        package: StoryEvidencePackage,
        plan: EditorialPlan,
        derived_facts: Sequence[DerivedFact | EvidenceScopeFact] = (),
    ) -> VerifiedDraft:
        """§13.1-§13.15 over a draft, the package it was written from, and what code derived.

        `derived_facts` is DETERMINISTIC_FACT_TOOLS §3's separate artifact — the `DerivedFact`
        rows the derivation stage computed for this run and §7's `EvidenceScopeFact`s — and it
        defaults to empty so `story.contracts.DraftVerifier`'s three-argument signature still
        describes this method. **The default is not a fallback.** A draft binding a derived id
        against an empty tuple is `derived_fact_not_in_run`, a REFUSE: a run that lost its
        derivations refuses the post rather than passing the numerals it can still resolve.
        """
        index = PackageIndex(package, derived_facts)
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
                elif index.bound(binding.fact_id) is not None:
                    continue
                elif binding.fact_id.startswith(
                        (DERIVED_FACT_PREFIX, EVIDENCE_SCOPE_PREFIX)):
                    # §6: a derived id resolves in the run's `derived_facts.json` and nowhere
                    # else. §3 forbids one entering `package.facts`, so "not in the package" is
                    # the wrong sentence for it — it names a computation this run did not make,
                    # or made against another package.
                    found.append(finding(
                        "derived_fact_not_in_run",
                        sentence_index=sentence.index,
                        char_start=binding.char_start, char_end=binding.char_end,
                        fact_ids=(binding.fact_id,),
                        expected=("a fact_id in this run's derived facts: "
                                  + (", ".join(sorted({*index.derived_facts,
                                                       *index.evidence_scope}))
                                     or "(the run carried none)")),
                        observed=binding.fact_id,
                        explanation=(
                            "§3: a derived fact may not live in `package.facts` — a package "
                            "whose contents depended on a model call would put the planner's "
                            "selection inside `package_content_digest`, which is a "
                            "`story_run_id` input — so it travels as a separate artifact and "
                            "resolves there or nowhere."),
                        suggested_fact_ids=sorted(index.derived_facts),
                    ))
                else:
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
            covering = self._covering_spans(sentence, index)
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
            # §6: a derived or evidence-scope binding is checked here too, by the same rules
            # read against a different row. Anything that resolves as neither has already been
            # refused as `fact_not_in_package`, `derived_fact_not_in_run` or
            # `event_property_bound_as_fact`.
            derived = index.derived_fact(binding.fact_id)
            if derived is not None:
                return self._derived_number_findings(sentence, binding, derived, ledgers)
            scope = index.scope_fact(binding.fact_id)
            if scope is not None:
                return self._scope_number_findings(sentence, binding, scope)
            return found

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

    def _derived_number_findings(
        self,
        sentence: DraftSentence,
        binding: FactBinding,
        derived: DerivedFact,
        ledgers: _Ledgers,
    ) -> list[VerificationFinding]:
        """§13.1 over a numeral bound to a derived fact — **the same rules, not lighter ones**.

        The span must hold what it says it holds, the rendering must be one numeral, the numeral
        must sit inside §13.1's printed-precision window around `DerivedFact.result`, and a
        written sign must agree with it. Every one of those is `compare_token_to_fact`, the same
        function `_binding_number_findings` calls against an observation, so *"a numeral bound to
        a derived fact is no more trusted and no less checked"* is one shared implementation
        rather than a claim.

        **`over_precision` cannot fire and the reason is structural, not an omission.** That
        signal compares the draft's significant figures against the fact's own `quoted_text` —
        a *printed* form, from an `EVIDENCED_BY` edge — and a derived fact has no printed form
        because nobody printed it. `compare_token_to_fact` reports
        `over_precise=False, fact_significant_figures=None` when the argument is absent, which
        its own docstring is explicit is *"not a pass"*.

        **A word-valued fact has no numeral and may not carry one.** `crossed_zero` and
        `trend_direction` answer in `result_word`; a span rendering `1.0` for *"it crossed"* is
        the exact state `DerivedFact`'s own validator forbids at the producing end, and it is
        refused here at the consuming end under `derived_unit_mismatch`.
        """
        found: list[VerificationFinding] = []
        if sentence.text[binding.char_start:binding.char_end] != binding.rendered:
            return [finding(
                "binding_span_does_not_match_text",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(derived.fact_id,),
                expected=binding.rendered,
                observed=sentence.text[binding.char_start:binding.char_end],
                explanation=(
                    "§12: the binding declares which span of its own text states the fact, and "
                    "a derived fact binds through an ordinary `FactBinding` precisely so this "
                    "rule reaches it."),
            )]

        if derived.unit in derived_rules.NON_NUMERIC_UNITS or derived.result is None:
            numerals = tokenize_numerals(binding.rendered)
            if not numerals:
                return found
            return [finding(
                "derived_unit_mismatch",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(derived.fact_id,),
                expected=f"no numeral for a {derived.unit} result "
                         f"({derived.result_word or '(no word)'})",
                observed=binding.rendered,
                explanation=(
                    "§4.4: `crossed_zero` answers a boolean and `trend_direction` a direction "
                    "word, and neither is a numeral any draft may print with a unit. A boolean "
                    "rendered as `1.0` is a number §13.1 would then compare against the prose."),
            )]

        token = self._sole_numeral(binding.rendered)
        if token is None:
            return [finding(
                "binding_rendering_is_not_one_numeral",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(derived.fact_id,),
                expected="exactly one numeral in the rendered span",
                observed=binding.rendered,
                explanation=(
                    "§13.1 compares the draft's numeral against the fact at the draft's own "
                    "precision, and `d` is undefined for a span holding none or two."),
            )]

        verdict = compare_token_to_fact(token, derived.result)
        if not verdict.within_window:
            found.append(finding(
                "number_outside_tolerance",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(derived.fact_id,),
                expected=(f"{derived.result!r} ({derived.operation.value} "
                          f"{derived.from_period}->{derived.to_period})"),
                observed=(f"{binding.rendered} reads {token.value!r}; "
                          f"||draft|-|fact|| = {verdict.difference!r} > {verdict.window!r}"),
                explanation=(
                    "§13.1: printed-precision half-ulp on magnitudes, "
                    "0.5 x 10^(e - d + 1) with d = "
                    f"{verdict.draft_significant_figures} as the draft wrote it. The value "
                    "compared against is the derivation tool's, never the draft's arithmetic."),
            ))
        if verdict.sign_explicit and not verdict.sign_agrees:
            found.append(finding(
                "sign_disagreement",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(derived.fact_id,),
                expected=("negative" if derived.result < 0 else "positive")
                         + f" ({derived.result!r})",
                observed=binding.rendered,
                explanation=(
                    "§13.1: sign is a separate check and applies because this numeral carried "
                    "its own sign. On a derived change the sign is the direction of the move — "
                    "$556M -> $110M is -$446M — so a written `+` is the opposite claim."),
            ))

        ledgers.facts.append(FactLedgerEntry(
            fact_id=derived.fact_id, metric_id=derived.metric_id,
            period_key=derived.to_period, value=derived.result, unit=derived.unit,
            rendered=binding.rendered, sentence_index=sentence.index,
        ))
        # The derivation panel row. `expression` is a free string on this type and the honest
        # content for a code-computed quantity is the call that produced it — there is no
        # writer-authored formula to record any more.
        ledgers.calculations.append(CalculationLedgerEntry(
            sentence_index=sentence.index,
            operation=derived.operation.value,
            input_observation_ids=(derived.from_fact_id, derived.to_fact_id),
            expression=(f"{derived.operation.value}({derived.from_fact_id}, "
                        f"{derived.to_fact_id})"),
            recomputed_value=derived.result,
            rendered=binding.rendered,
        ))
        return found

    def _scope_number_findings(
        self,
        sentence: DraftSentence,
        binding: FactBinding,
        scope: EvidenceScopeFact,
    ) -> list[VerificationFinding]:
        """§7's bounded-uncertainty claim: the span must say what the fact says and no more."""
        if sentence.text[binding.char_start:binding.char_end] != binding.rendered:
            return [finding(
                "binding_span_does_not_match_text",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(scope.fact_id,),
                expected=binding.rendered,
                observed=sentence.text[binding.char_start:binding.char_end],
                explanation="§12, on an evidence-scope binding.",
            )]
        return derived_rules.scope_findings(sentence, binding, scope)

    def _covering_spans(
        self, sentence: DraftSentence, index: PackageIndex
    ) -> tuple[language.LexicalMatch, ...]:
        """§13.1's ways a numeral may be accounted for, as spans in this sentence.

        A calculation's `period_surface` covers the same way a binding's does, and for the same
        §13.1 reason — *"a year in `in 2022` is not a fact"*. Covering it here is not trusting
        it: `_check_periods` resolves the declared surface through §13.4's grammar and requires
        it to agree with every input observation, so a derivation naming the wrong period is
        refused by that check rather than admitted by this one.

        **A derived binding covers *both* of its derivation's periods, and that is the repair a
        live run forced** *(2026-08-19)*. A `FactBinding` declares one `period_surface` and §6
        fixes it to `to_period`, so a sentence stating a two-period derivation — *"…fell from
        $556 million in the second quarter of 2022 to $110 million in the third quarter of
        2022"* — had its `to` year covered and its `from` year uncovered **by construction**, and
        both Qwen and `gpt-5.4` were refused `unbound_numeral` on the first `2022`. The
        derivation knows both periods; the binding could only ever name one; so the two periods
        are read off the *fact* rather than off the declaration.

        **What is covered and what is checked are different questions, and this answers only the
        first.** Covering says *"a person can see which claim this numeral belongs to"* —
        §13.1's own reason, that a bare numeral cannot be matched back to a fact. Checking says
        *"the claim is true"*, and that is `_check_periods`: the declared surface is still
        resolved through §13.4's grammar and still required to equal `to_period` on both
        endpoints and on kind, and `_derived_period_grounding_findings` still refuses a sentence
        naming a period the derivation does not span, as
        `period_named_in_text_contradicts_binding`. So the widening here cannot license a wrong
        period — it can only stop a *right* one from being reported as an undeclared numeral.

        The two periods are matched through the grammar rather than by string, exactly as a
        declared surface is: any phrase §13.4 resolves to one of the derivation's two windows
        covers, so *"Q2 2022"* and *"the second quarter of 2022"* are one answer. `scan` reports
        no character span — it runs over a lowercased, whitespace-collapsed copy — so each
        phrase is re-located in the real text by `language.occurrences`, which fails closed: a
        phrase that cannot be found again covers nothing.
        """
        spans = [language.LexicalMatch(term=b.rendered, start=b.char_start,
                                       end=b.char_end)
                 for b in sentence.fact_bindings]
        needles = [b.period_surface for b in sentence.fact_bindings
                   if b.period_surface and self._period_surface_is_checked(b, index)]
        needles.extend(self._literal_ok)
        if sentence.calculation is not None:
            if sentence.calculation.result_rendered:
                needles.append(sentence.calculation.result_rendered)
            if sentence.calculation.period_surface:
                needles.append(sentence.calculation.period_surface)
        needles.extend(self._derived_period_needles(sentence, index))
        for needle in needles:
            spans.extend(language.occurrences(sentence.text, needle))
        return tuple(spans)

    @staticmethod
    def _period_surface_is_checked(binding: FactBinding, index: PackageIndex) -> bool:
        """Whether §13.4 resolves this binding's `period_surface` — H1's gate on coverage.

        **A surface that licenses coverage and that nothing resolves is a hole, and the review
        walked through it.** `_check_periods` reaches `index.fact()` and then
        `index.derived_fact()`; for an **evidence-scope** binding both answer `None` and the
        check `continue`s, as do `_check_units` and `_check_metric_identity`. The field stayed a
        free string the model wrote — and this function's caller consumed it from *every*
        binding unconditionally, so those characters licensed §13.1 coverage for any numeral
        they contained:

            "The evidence in this package supplies no explanation for the 92 percent collapse
             to 7 from 9999."      period_surface = "92 percent collapse to 7 from 9999"

        passed with zero findings, three fabricated numerals in an accepted post. §13.1's
        docstring above is explicit that covering a declared surface *"is not trusting it:
        `_check_periods` resolves the declared surface"* — that sentence was the licence, and
        for one binding kind it was false. So coverage is now taken only from a binding whose
        surface a check does in fact resolve, and `derived_facts.scope_findings` refuses the
        declaration outright from the other end.

        A binding whose id resolves to neither is already `fact_not_in_package` or
        `derived_fact_not_in_run` and the draft is refused whatever this answers; excluding it
        here costs nothing and keeps the predicate one sentence — *"a surface some check
        resolves"* — rather than an enumeration of the kinds that happen to be safe.
        """
        return (index.fact(binding.fact_id) is not None
                or index.derived_fact(binding.fact_id) is not None)

    @staticmethod
    def _derived_period_needles(
        sentence: DraftSentence, index: PackageIndex
    ) -> list[str]:
        """The phrases this sentence uses to name a derivation's own `from` and `to` windows."""
        endpoints: list[PackagedFact] = []
        for binding in sentence.fact_bindings:
            derived = index.derived_fact(binding.fact_id)
            if derived is None:
                continue
            endpoints.extend(
                fact for fact in (index.fact(derived.from_fact_id),
                                  index.fact(derived.to_fact_id))
                if fact is not None)
        if not endpoints:
            return []
        return [phrase.text for phrase in period_grammar.scan(sentence.text)
                if phrase.resolved
                and any(_endpoints_agree(phrase.period, fact) for fact in endpoints)]

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
                    derived = index.derived_fact(binding.fact_id)
                    if derived is not None:
                        examined += 1
                        found.extend(self._derived_unit_findings(sentence, binding, derived))
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

    def _derived_unit_findings(
        self,
        sentence: DraftSentence,
        binding: FactBinding,
        derived: DerivedFact,
    ) -> list[VerificationFinding]:
        """§13.2 over a numeral bound to a derived fact.

        **This is where `CHANGE_SURFACES` stops being a blanket refusal.** Above, a numeral
        rendering `percentage_points`, `basis_points` or `multiple` against an **observation** is
        `unit_mismatch` and still is: no observation in any package is a change, the extraction
        refused all 186 it saw, and a change surface on a reported row is a derived quantity
        wearing a citation. Here the bound row *is* the derived quantity, so the same three
        surfaces are judged against what §4.1 says the operation produced — and two of them are
        legal exactly where they are the answer.

        `basis_points` is legal nowhere, in either map. §13.3's own words: the same quantity in
        basis points is a hundred times the number, so admitting the surface would require
        scaling the recomputation by the rendering.
        """
        if derived.unit in derived_rules.NON_NUMERIC_UNITS:
            return []  # `_derived_number_findings` refuses a numeral there at all
        token = self._sole_numeral(binding.rendered)
        if token is None:
            return []  # already refused as binding_rendering_is_not_one_numeral
        found: list[VerificationFinding] = []
        if token.currency_symbol and derived.unit != "USD":
            found.append(finding(
                "currency_symbol_on_non_monetary_unit",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(derived.fact_id,),
                expected=f"no currency symbol on a {derived.unit} result",
                observed=binding.rendered,
                explanation=(
                    "§13.2: a percentage, a percentage-point gap and a multiple are "
                    "dimensionless. A `$` on one is a currency that survived a division that "
                    "should have cancelled it."),
            ))
        allowed = derived_rules.DERIVED_SURFACES.get(derived.unit, frozenset())
        if token.unit not in allowed:
            found.append(finding(
                "derived_unit_mismatch",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(derived.fact_id,),
                expected=(f"a result in "
                          f"{' | '.join(sorted(surface.value for surface in allowed))} for a "
                          f"{derived.unit} derivation"),
                observed=f"{binding.rendered} renders {token.unit.value}",
                explanation=(
                    "§13.3: a gap between two percent levels is in percentage points. The same "
                    "number in percent is the confusion §13.3 calls the single most likely "
                    "factual error this package can make — 45.5x apart on the metric it "
                    "measures — in basis points it is a hundred times too small, and in `x` it "
                    "is a ratio nobody computed."),
            ))
        return found

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
                found.extend(self._reported_change_findings(sentence, percent_facts, index))

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
        self,
        sentence: DraftSentence,
        percent_facts: Sequence[PackagedFact],
        index: PackageIndex,
    ) -> list[VerificationFinding]:
        """§13.3's second gate, applied to the **bound** numerals as well as the loose ones.

        **The half that was missing, measured.** The gate skipped any numeral inside a
        fact-binding span, so it could only ever fire where `unbound_numeral` already fires.
        Rewriting the demo's first sentence to *"Opendoor's GAAP Gross Margin **fell 12.6
        percent** in the third quarter of 2022"* — the binding still on the same observation,
        the span re-anchored — **passed**. −12.6% is the metric's *level* in the quarter and
        not a change in it, and no observation in this package is a change: the extraction
        refused all 186 it saw.

        The test is the **fact's** unit and not the numeral's surface, which is what makes it
        catch that sentence: `"12.6 percent"` spelled in words tokenises as `SurfaceUnit.NONE`
        — §13.1's tokeniser reads the `%` sign and not the word — so a surface-only rule sees
        no percentage there at all. A change verb governing a numeral bound to a percent-unit
        *level* is the claim §13.3 refuses, however the writer spelled the unit.
        """
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
        for binding in sentence.fact_bindings:
            fact = index.fact(binding.fact_id)
            if fact is None or fact.unit != "percent":
                continue
            return [finding(
                "percent_change_reported_not_calculated",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(fact.observation_id,),
                expected="a level stated as a level, or kind=calculated with a Calculation",
                observed=(f"kind=reported, change verb {verb.group(0)!r} over "
                          f"{binding.rendered!r}, bound to the {fact.period_key} level "
                          f"{fact.value!r}"),
                explanation=(
                    "§13.3's second gate, on a bound numeral. The gate skipped everything a "
                    "binding covered, so it could only fire where `unbound_numeral` fires "
                    "anyway. `fell 12.6 percent` bound to a −12.6 *level* passed: the number "
                    "is the fact's and the sentence is false, because the package holds no "
                    "change at all (174 DERIVED_CHANGE_COLUMN + 12 DERIVED_COMPARISON "
                    "refused)."),
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
                    derived = index.derived_fact(binding.fact_id)
                    if derived is not None:
                        examined += 1
                        found.extend(self._derived_period_findings(
                            sentence, binding, derived, index))
                    continue
                examined += 1
                if sentence.kind in GROUNDED_SENTENCE_KINDS:
                    found.extend(self._period_grounding_findings(
                        sentence, (fact,), binding.period_surface,
                        char_start=binding.char_start, char_end=binding.char_end,
                        suggested=self._suggestions(index, fact)))
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

    def _derived_period_findings(
        self,
        sentence: DraftSentence,
        binding: FactBinding,
        derived: DerivedFact,
        index: PackageIndex,
    ) -> list[VerificationFinding]:
        """§13.4 over a derived binding: the surface must resolve to **`to_period`**.

        **`to_period` and not both, and that is §2's repair rather than a choice made here.** A
        two-period derivation computes over two windows and can name neither with one surface;
        `_calculation_period_findings` refused that outright, which is why the demo's
        `calculated` sentence had to leave `Calculation.period_surface` empty and why its
        *"in the third quarter of 2022"* became `unbound_numeral` on the literal `2022`. A
        derived fact resolves it by naming the period the claim is *about* — the later one, the
        one `display_semantics` is stated against — and code fills the surface from
        `period_surface_hint`. The model cannot forget a field it no longer writes, and the
        verifier still reads the field it was handed rather than guessing one.

        The prose grounding runs whatever the sentence's kind, unlike an observation binding.
        `GROUNDED_SENTENCE_KINDS` exempts `calculated` because such a sentence used to carry no
        bindings; it now carries these, so the exemption's premise is gone. It is
        `_derived_period_grounding_findings` rather than the generic rule, because a derivation's
        sentence may legitimately name **two** periods and the generic rule knows about one.
        """
        to_fact = index.fact(derived.to_fact_id)
        if to_fact is None:
            return []  # already refused as derivation_not_offered
        found = self._derived_period_grounding_findings(
            sentence, binding, derived, index, to_fact)
        resolved = period_grammar.resolve(binding.period_surface)
        if not resolved.resolved:
            found.append(finding(
                "period_unresolvable",
                sentence_index=sentence.index,
                char_start=binding.char_start, char_end=binding.char_end,
                fact_ids=(derived.fact_id,),
                expected="a surface in §13.4's closed grammar",
                observed=binding.period_surface or "(empty)",
                explanation=(
                    "§13.4: resolved through a closed grammar, never a free date parser. This "
                    "is the field §2 measured the original refusal on — the model left it "
                    "empty on a `Calculation` while its own text read \"in the third quarter "
                    "of 2022\" — and code now fills it from the derivation's `to_period`."),
            ))
            return found
        if _endpoints_agree(resolved, to_fact):
            return found
        conflated = _same_anchor(resolved, to_fact)
        found.append(finding(
            "period_shape_conflated" if conflated else "period_mismatch",
            sentence_index=sentence.index,
            char_start=binding.char_start, char_end=binding.char_end,
            fact_ids=(derived.fact_id, to_fact.observation_id),
            expected=(f"{derived.to_period} "
                      f"({to_fact.period_start or to_fact.instant_date}"
                      f"..{to_fact.period_end or to_fact.instant_date})"),
            observed=(f"{binding.period_surface!r} resolves to {resolved.key} "
                      f"({resolved.period_start or resolved.instant_date}"
                      f"..{resolved.period_end or resolved.instant_date})"),
            explanation=(
                "§13.4: exact equality on both endpoints and on kind, against the derivation's "
                "`to_period`. adjusted_ebitda ending 2022-09-30 is +$183M for the nine-month "
                "YTD and -$211M for the quarter, so a surface matching the anchor alone would "
                "name a different number."
                if conflated else
                "§13.4: exact equality on both endpoints and on kind, against the derivation's "
                "own `to_period` — the period the claim is about."),
            suggested_fact_ids=self._suggestions(index, to_fact),
        ))
        return found

    def _derived_period_grounding_findings(
        self,
        sentence: DraftSentence,
        binding: FactBinding,
        derived: DerivedFact,
        index: PackageIndex,
        to_fact: PackagedFact,
    ) -> list[VerificationFinding]:
        """§13.4 applied to the prose of a sentence stating a derived fact: **two** windows.

        `_period_grounding_findings` asks whether the sentence names the one period its one fact
        is about. A derivation is about two, and *"…fell from $556 million in the second quarter
        of 2022 to $110 million in the third quarter of 2022"* is a true sentence that names both
        — so this rule reads the pair.

        **Two questions, and both have to be answered `yes`.**

        1. Does the sentence name the period the *claim* is about? `to_period` is what the
           binding declares and what `display_semantics` is stated against, so a sentence that
           names periods and never names that one is `period_named_in_text_contradicts_binding`,
           exactly as it was before this function existed.
        2. Does it name any period the derivation does **not** span? That is the new half, and it
           is the price of `_covering_spans` covering the `from` window: a numeral inside a
           period phrase now stops being `unbound_numeral` when the phrase is one of the
           derivation's two, so *"which phrases are those"* has to be a refusal rather than an
           assumption. A third window in the sentence is a claim about a period nothing computed.

        The generic rule's *any* semantics survive question 1 and are exactly what question 2
        closes: under it a sentence naming Q3 and Q1 passed on Q3 and said nothing about Q1.

        An unresolvable phrase is `period_surface_absent_from_text` on the same footing as
        everywhere else — *"the quarter"* is UNRESOLVABLE in §13.4's own words — and a sentence
        naming no period at all is left alone, which is `_period_grounding_findings`' deliberate
        asymmetry and holds here for the same reason: a period is routinely carried by the
        paragraph, and an assertion nobody made cannot be false.
        """
        named = period_grammar.scan(sentence.text)
        if not named:
            return []
        from_fact = index.fact(derived.from_fact_id)
        spanned = [fact for fact in (from_fact, to_fact) if fact is not None]
        keys = ", ".join(sorted({fact.period_key for fact in spanned}))
        common = dict(
            sentence_index=sentence.index,
            char_start=binding.char_start, char_end=binding.char_end,
            fact_ids=(derived.fact_id,),
            suggested_fact_ids=tuple(self._suggestions(index, to_fact)),
        )
        resolvable = [phrase for phrase in named if phrase.resolved]
        if not resolvable:
            return [finding(
                "period_surface_absent_from_text", **common,
                expected=f"the sentence to name {keys} ({binding.period_surface!r})",
                observed=("the sentence names "
                          + ", ".join(repr(phrase.text) for phrase in named)
                          + ", which §13.4's closed grammar does not resolve"),
                explanation=(
                    "§13.4: a period phrase carrying no numeral is invisible to §13.1's coverage "
                    "rule, so nothing forced a period into the text. \"The quarter\" is "
                    "UNRESOLVABLE in §13.4's own words, and it is no more resolvable for being "
                    "written in prose."),
            )]
        outside = [phrase for phrase in resolvable
                   if not any(_endpoints_agree(phrase.period, fact) for fact in spanned)]
        names_the_claim = any(_endpoints_agree(phrase.period, to_fact)
                              for phrase in resolvable)
        if not outside and names_the_claim:
            return []
        offending = outside or resolvable
        return [finding(
            "period_named_in_text_contradicts_binding", **common,
            expected=(f"the sentence to name {derived.to_period} and, where it names a second "
                      f"period, {derived.from_period} — the two windows this derivation spans"),
            observed=("the sentence names "
                      + ", ".join(f"{phrase.text!r} ({phrase.period.key})"
                                  for phrase in offending)
                      + f"; the declaration says {binding.period_surface!r}"),
            explanation=(
                "§13.4: exact equality on both endpoints and on kind, applied to every period "
                "the sentence names rather than only to the one it declares. A derivation spans "
                "two windows and its sentence may name both; a third is a period nothing "
                "computed, and `_covering_spans` no longer reports the numeral inside it as "
                "undeclared, so it is refused here instead."),
        )]

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
        grounding = self._period_grounding_findings(
            sentence, facts, calculation.period_surface, suggested=())
        resolved = period_grammar.resolve(calculation.period_surface)
        if not resolved.resolved:
            return grounding + [finding(
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
            return grounding, 1
        conflated = all(_same_anchor(resolved, fact) for fact in disagreeing)
        return grounding + [finding(
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

    def _period_grounding_findings(
        self,
        sentence: DraftSentence,
        facts: Sequence[PackagedFact],
        declared_surface: str,
        *,
        char_start: int | None = None,
        char_end: int | None = None,
        suggested: Sequence[str] = (),
    ) -> list[VerificationFinding]:
        """§13.4 applied to the sentence's **prose**, not only to what it declared.

        **R8 was told the period surface was already protected. It is not, and R9 measured
        it.** The argument was that a period phrase contains a numeral, so §13.1's coverage rule
        forces the declared surface into the text — and that holds only for phrases that carry a
        numeral. Every counter-example the claim was tested against happened to include a year,
        which is why it survived. Measured against `863edf6` on the demo's own accepted draft,
        binding still declaring the true `"the third quarter of 2022"` and the `rendered` span
        honestly re-anchored:

        | prose | verdict |
        | --- | --- |
        | *"…for **the fourth quarter**."* | passed, zero findings |
        | *"…for **the full year**."* | passed |
        | *"…for **the most recent quarter**."* | passed |
        | *"…**last quarter**."* | refused, but only as `unsupported_superlative` on `last` |
        | *"…for **the fourth quarter of 2022**."* | refused `unbound_numeral` — on the year |

        So the protection was an accident of numeral coverage. This is R8's metric grounding,
        applied to the other surface, and it reads the sentence the same way: through §13.4's
        own grammar, never by substring, so `"Q3 2022"` grounds a binding that declared
        `"the third quarter of 2022"` and a legitimate re-phrasing is not a refusal.

        **A sentence naming no period at all is left exactly as it was**, and that is a
        deliberate asymmetry with the metric rule rather than an oversight. A metric name is
        what a factual sentence is *about*, so its absence is a sentence about nothing; a period
        is routinely carried by the paragraph — the demo's own calculated sentence declares
        `"the third quarter of 2022"` and its prose names no period at all, and it is true.
        `scan` returning nothing means the sentence asserts no period, and an assertion nobody
        made cannot be false. The declared surface is still checked against the fact by the
        rules above; what is new is that a period the sentence *does* name must be that period.

        **No denominator of its own**, unlike `_metric_grounding_findings`. `examined` counts
        the things §13.4 looked at — a binding's period, a derivation's period — and this asks a
        second question about each of *those same things* rather than about a new one. Counting
        it twice would make `periods: PASS` mean a different quantity on drafts that name their
        periods than on drafts that do not, and the denominator's job is to separate *never ran*
        from *ran clean*, which is unaffected either way.
        """
        named = period_grammar.scan(sentence.text)
        if not named:
            return []
        keys = ", ".join(sorted({fact.period_key for fact in facts}))
        common = dict(
            sentence_index=sentence.index,
            char_start=char_start, char_end=char_end,
            fact_ids=tuple(fact.observation_id for fact in facts),
            suggested_fact_ids=tuple(suggested),
        )
        resolvable = [phrase for phrase in named if phrase.resolved]
        # Agreement with **every** fact, which for a binding is its one fact and for a
        # calculation is all of its inputs — the same bar `_calculation_period_findings` sets
        # for the declared surface, because a derivation that names one of its two windows is
        # presenting a Q2→Q3 delta as a quarter.
        if any(all(_endpoints_agree(phrase.period, fact) for fact in facts)
               for phrase in resolvable):
            return []
        if resolvable:
            return [finding(
                "period_named_in_text_contradicts_binding", **common,
                expected=f"the sentence to name {keys}",
                observed=("the sentence names "
                          + ", ".join(f"{phrase.text!r} ({phrase.period.key})"
                                      for phrase in resolvable)
                          + f"; the declaration says {declared_surface!r}"),
                explanation=(
                    "§13.4: exact equality on both endpoints and on kind, applied to the period "
                    "the sentence names rather than only to the one it declares. The two are "
                    "different strings and only the declaration was ever read."),
            )]
        return [finding(
            "period_surface_absent_from_text", **common,
            expected=f"the sentence to name {keys} ({declared_surface!r})",
            observed=("the sentence names "
                      + ", ".join(repr(phrase.text) for phrase in named)
                      + ", which §13.4's closed grammar does not resolve"),
            explanation=(
                "§13.4: a period phrase carrying no numeral is invisible to §13.1's coverage "
                "rule, so nothing forced the declared surface into the text. Measured: the "
                "demo's own accepted draft passed with its prose moved to \"the fourth "
                "quarter\", \"the full year\" and \"the most recent quarter\" while the binding "
                "kept declaring the true Q3. \"The quarter\" is UNRESOLVABLE in §13.4's own "
                "words, and it is no more resolvable for being written in prose."),
        )]

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
                    derived = index.derived_fact(binding.fact_id)
                    if derived is not None:
                        examined += 1
                        found.extend(self._derived_metric_findings(
                            sentence, binding, derived, aliases))
                        # Whatever the sentence's kind, unlike an observation binding and for
                        # `_derived_period_findings`' reason: `GROUNDED_SENTENCE_KINDS` exempts
                        # `calculated` because such a sentence used to carry no bindings, and
                        # §6 is what put these on it. Counted in `examined` for the same reason
                        # the observed grounding is: a check that ran and a check that was
                        # skipped must not report the same denominator.
                        examined += 1
                        found.extend(self._derived_metric_grounding_findings(
                            sentence, binding, derived, aliases))
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
                if sentence.kind in GROUNDED_SENTENCE_KINDS:
                    grounding, grounding_examined = self._metric_grounding_findings(
                        sentence, binding, fact, index, aliases)
                    examined += grounding_examined
                    found.extend(grounding)
        return CheckResult(name="metric_identity", examined=examined, findings=tuple(found))

    def _metric_grounding_findings(
        self,
        sentence: DraftSentence,
        binding: FactBinding,
        fact: PackagedFact,
        index: PackageIndex,
        aliases: MetricAliasIndex,
    ) -> tuple[list[VerificationFinding], int]:
        """§13.5 applied to the sentence's **prose**, not only to what the binding declared.

        **The asymmetry this closes, measured on the demo's own accepted draft.** A binding
        declares a `period_surface` and a `metric_surface`, and only the first was protected:
        a period contains numerals, so §13.1's coverage rule already forces it to occur in the
        text, and moving the prose to *"the fourth quarter"* while the declaration says Q3
        raises `unbound_numeral` — or `period_mismatch` if the declaration moves too. **A
        metric name carries no numeral**, so nothing forced it to occur at all. Rewriting the
        demo's first sentence to *"Opendoor reported an **Adjusted** Gross Margin of −12.6
        percent"* with `metric_surface` left at `GAAP Gross Margin` and the binding span
        honestly re-anchored **passed with zero findings**, and the adjusted margin was +3.3.
        *"Opendoor reported **net income** of −12.6 percent"* passed too, over a metric the
        package does not carry.

        Resolved through §13.5's alias index rather than by substring, so a legitimate alias
        still passes: the demo writes *"Adjusted Gross Margin"* and the index carries
        `adjusted gross margin`. A declared-ambiguous surface in the prose — *"gross margin"* —
        licenses either margin here, because it is `metric_surface_ambiguous`'s job to refuse a
        writer for *declaring* it and this check's job to notice that the sentence talks about
        something else entirely.

        `reported` **and `explanatory`** — R8 shipped `reported` only and named the gap as its
        own left-undone item; R9 closed it. `GROUNDED_SENTENCE_KINDS` carries the reasoning and
        the two exemptions. §13.14's comparison check already reads a comparative's two sides
        out of the prose through this same index.
        """
        named = aliases.scan(sentence.text)
        if any(occurrence.licenses(fact.metric_id) for occurrence in named):
            return [], 1
        common = dict(
            sentence_index=sentence.index,
            char_start=binding.char_start, char_end=binding.char_end,
            fact_ids=(fact.observation_id,),
            suggested_fact_ids=self._suggestions(index, fact),
        )
        if not named:
            return [finding(
                "metric_surface_absent_from_text", **common,
                expected=f"the sentence to name {fact.metric_id} ({binding.metric_surface!r})",
                observed=sentence.text,
                explanation=(
                    "§13.5: a metric surface carries no numeral, so nothing else in §13 forces "
                    "it into the sentence. Measured: the demo's own accepted draft passed with "
                    "its metric renamed in the prose and the binding left untouched. A "
                    "declaration nobody can read against the words is not a declaration."),
            )], 1
        return [finding(
            "metric_named_in_text_contradicts_binding", **common,
            expected=f"the sentence to name {fact.metric_id}",
            observed=("the sentence names "
                      + ", ".join(sorted({metric_id for occurrence in named
                                          for metric_id in occurrence.metric_ids}))
                      + f"; the binding declares {binding.metric_surface!r}"),
            explanation=(
                "§13.5 with longest match: \"gross margin\" ⊂ \"adjusted gross margin\", so the "
                "one word `Adjusted` moves the sentence to the other metric while every "
                "declared field stays valid. Both margins are in this package and they read "
                "+3.3 and −12.6 in 2022Q3."),
        )], 1

    def _derived_metric_findings(
        self,
        sentence: DraftSentence,
        binding: FactBinding,
        derived: DerivedFact,
        aliases: MetricAliasIndex,
    ) -> list[VerificationFinding]:
        """§13.5 over a derived binding: the declared surface must name a metric the fact is of.

        **Two metrics are admissible and only for the operations that have two.** R2 permits a
        second metric only under a `DIVERGENCE` claim, which is `compare_levels` and `ratio`;
        everywhere else `from_metric_id` equals `metric_id` and the pair collapses to one. So a
        writer may call the demo's gap either *"adjusted gross margin"* or *"GAAP gross
        margin"* — both are what it is a comparison of — and may call it neither of them at its
        peril, which is `metric_binding_mismatch` exactly as for an observation.

        The three surface refusals above it — unresolved, declared-ambiguous, and two metrics
        sharing a `mutually_distinct_group` — are the same checks over the same alias index,
        because *"gross margin"* is ambiguous whether it names a level or a gap between two.
        """
        resolution = aliases.resolve(binding.metric_surface)
        common = dict(
            sentence_index=sentence.index,
            char_start=binding.char_start, char_end=binding.char_end,
            fact_ids=(derived.fact_id,),
        )
        if not resolution.resolved:
            return [finding(
                "metric_surface_unresolved", **common,
                expected="a surface in the alias index",
                observed=binding.metric_surface or "(empty)",
                explanation="§13.5: a surface that resolves to nothing is refused.")]
        if resolution.declared_ambiguous:
            return [finding(
                "metric_surface_ambiguous", **common,
                expected="a surface naming one metric",
                observed=(f"{binding.metric_surface!r} resolves through "
                          f"{resolution.matched!r} to {', '.join(resolution.metric_ids)}"),
                explanation=(
                    "§13.5: gaap_gross_margin's own label *is* \"Gross Margin\", so the surface "
                    "a writer would naturally reach for is the ambiguous one — and a derived "
                    "gap between the two margins is the sentence most likely to reach for it."))]
        if resolution.shared_groups:
            return [finding(
                "mutually_distinct_group_ambiguity", **common,
                expected="metrics from different mutually_distinct_groups",
                observed=(f"{', '.join(resolution.metric_ids)} share "
                          f"{', '.join(resolution.shared_groups)}"),
                explanation="§13.5's third refusal.")]
        named = set(resolution.metric_ids)
        if named & {derived.metric_id, derived.from_metric_id}:
            return []
        return [finding(
            "metric_binding_mismatch", **common,
            expected=(f"the surface to resolve to {derived.metric_id}"
                      + (f" or {derived.from_metric_id}"
                         if derived.from_metric_id != derived.metric_id else "")),
            observed=(f"{binding.metric_surface!r} resolves through {resolution.matched!r} to "
                      f"{', '.join(resolution.metric_ids)}"),
            explanation=(
                "§13.5 with longest match: \"gross margin\" is inside \"adjusted gross "
                "margin\", so one word moves the sentence to the other metric while every "
                "declared field stays valid. Both margins are in this package and they read "
                "+3.3 and -12.6 in 2022Q3."),
        )]

    def _derived_metric_grounding_findings(
        self,
        sentence: DraftSentence,
        binding: FactBinding,
        derived: DerivedFact,
        aliases: MetricAliasIndex,
    ) -> list[VerificationFinding]:
        """§13.5 applied to the **prose** of a sentence stating a derived fact (H1).

        **The hole this closes, reproduced by the review.** `_derived_metric_findings` above
        checks the *declared* `metric_surface` and nothing else. The prose rule that catches a
        renamed metric for an observation — `_metric_grounding_findings` — lives in the observed
        branch, behind `GROUNDED_SENTENCE_KINDS`, and a derived binding `continue`s past it on
        **every** sentence kind, `reported` included. So both of these were accepted with zero
        findings, bound to a derivation of `adjusted_gross_profit`:

            "Adjusted gross margin fell $446 million in the third quarter of 2022."
            "Revenue fell $446 million in the third quarter of 2022."

        The first names a **percent** metric over a USD figure; the second names a metric the
        package does not carry. The identical sentences over an *observed* binding are
        `metric_named_in_text_contradicts_binding`, which is the asymmetry:
        `GROUNDED_SENTENCE_KINDS`' own comment recorded the `calculated` exemption as *"a
        separate question nobody has measured"*, and S13 is what moved every derived figure into a `calculated` sentence.
        The unmeasured gap became the whole guard on which metric a derived number is attributed
        to.

        **Two metrics are admissible, for `_derived_metric_findings`' reason.** R2 permits a
        second metric only under a `DIVERGENCE` claim — `compare_levels` and `ratio` — so a
        sentence stating the demo's gap may name either margin, or both, and that is what the
        committed accepted draft does. Everywhere else `from_metric_id` equals `metric_id`.

        Same two codes and the same alias index as the observed rule, because it is the same
        fault: a sentence naming no metric at all is `metric_surface_absent_from_text` and one
        naming a metric this derivation is not of is
        `metric_named_in_text_contradicts_binding`. A declared-ambiguous surface in the prose —
        *"gross margin"* — licenses either margin here, exactly as it does there: refusing a
        writer for *declaring* it is `metric_surface_ambiguous`'s job.
        """
        admissible = {derived.metric_id, derived.from_metric_id}
        named = aliases.scan(sentence.text)
        if any(occurrence.licenses(metric_id)
               for occurrence in named for metric_id in admissible):
            return []
        common = dict(
            sentence_index=sentence.index,
            char_start=binding.char_start, char_end=binding.char_end,
            fact_ids=(derived.fact_id,),
        )
        expected = ("the sentence to name "
                    + " or ".join(sorted(admissible))
                    + f" ({binding.metric_surface!r})")
        if not named:
            return [finding(
                "metric_surface_absent_from_text", **common,
                expected=expected,
                observed=sentence.text,
                explanation=(
                    "§13.5: a metric surface carries no numeral, so nothing else in §13 forces "
                    "it into the sentence — and a derived figure is the case where that matters "
                    "most, because the number is the one thing in the sentence code computed "
                    "and the metric it is attributed to was never read. Measured: a $446 "
                    "million fall in adjusted gross profit, written about revenue, passed."),
            )]
        return [finding(
            "metric_named_in_text_contradicts_binding", **common,
            expected=expected,
            observed=("the sentence names "
                      + ", ".join(sorted({metric_id for occurrence in named
                                          for metric_id in occurrence.metric_ids}))
                      + f"; the binding declares {binding.metric_surface!r}"),
            explanation=(
                "§13.5 with longest match: \"gross margin\" is inside \"adjusted gross "
                "margin\", so one word moves the sentence to another metric while every "
                "declared field stays valid. Over a derivation the two metrics need not even "
                "share a unit — *\"Adjusted gross margin fell $446 million\"* states a "
                "percent metric in dollars — and the declared surface, which is the only thing "
                "the rule above reads, is still correct."),
        )]

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
        # Built once for the whole draft, not once per sentence: TABLE_CELL_CITATIONS §3.3 made
        # `facts_by_evidence_handle` a method a caller holds precisely so §12 and §13.7 each
        # build one index over one package rather than one per citation.
        handles = index.package.facts_by_evidence_handle()
        for sentence in draft.sentences:
            sentence_findings, uses, count = citation_rules.check_sentence_citations(
                sentence, index, aliases, seen, handles)
            found.extend(sentence_findings)
            examined += count
            for key, use in citation_rules.index_uses(uses).items():
                seen.setdefault(key, use)

            # §6: a `calculated` sentence used to cite nothing, because its number was the
            # model's arithmetic and no passage said it. It now binds derived facts whose
            # inputs are packaged observations, and *"cites its inputs"* is half of what
            # replaces `calculated_sentence_cites_passage` — the other half being
            # `_calculated_citation_findings`, which bounds *which* citations are permitted.
            #
            # A sentence binding only §7 evidence-scope facts is exempt and the exemption is
            # the design: that fact carries no citation field, mints no handle and is a claim
            # about what the evidence does *not* contain. Requiring a citation there is the
            # failure §7 names — the "no explanation was disclosed" sentence reaching for a
            # financial-table citation as though the table had said it.
            binds_evidence = any(index.derived_fact(binding.fact_id) is not None
                                 or index.fact(binding.fact_id) is not None
                                 for binding in sentence.fact_bindings)
            # The exemption is `scope_only` and not `not binds_evidence`, which would also
            # exempt a sentence whose bindings resolve to **nothing** — already a refusal, and
            # not a reason to stop asking for a citation as well.
            scope_only = (
                bool(sentence.fact_bindings) and not binds_evidence
                and any(index.scope_fact(binding.fact_id) is not None
                        for binding in sentence.fact_bindings))
            needs_citation = (
                (sentence.kind is SentenceKind.EXPLANATORY and not scope_only)
                or (sentence.kind is SentenceKind.REPORTED and sentence.fact_bindings)
                or (sentence.kind is SentenceKind.CALCULATED and binds_evidence)
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
        """§13.9, in the form DETERMINISTIC_FACT_TOOLS §6 leaves it. **Three expectations moved
        and each is named here rather than left to be discovered.**

        1. **`reported_sentence_carries_calculation` now fires under every sentence kind.** It
           fired only on `reported` because `calculated` was the kind a `Calculation` belonged
           to; §6 retires the writer's `Calculation` entirely — the number is code's now, and a
           writer-declared operation is the model doing arithmetic with code checking its
           homework, which is the arrangement §1 replaces. The code's name reads slightly wrong
           under a `calculated` sentence and it is kept anyway, for the reason `codes.py`
           records about `ADD_CONFLICT_DISCLOSURE`: renaming a gate entry to improve a sentence
           costs more than it buys.
        2. **`calculated_sentence_without_calculation` now asks for a derived-fact binding.**
           §6 lists it as *"replaced"*; the replacement is the same refusal about the same
           absence — a `calculated` sentence with nothing behind its number.
        3. **`calculated_sentence_cites_passage` narrows from *"no citation"* to *"no citation
           the derivation did not rest on"*.** This is the one place a check changed shape, so
           the argument in full: it existed because a calculated number was the model's own and
           any passage citation beside it claimed the filing said something it did not. Under
           §6 the number is code's, computed from two packaged observations, and §6 requires the
           table citation to *stay attached to those observations* — so the sentence must cite
           them, and refusing all citations would forbid the evidence the plan requires. What it
           still refuses is unchanged in force: a citation whose handle names anything other
           than an input of a derived fact this sentence binds, which for a sentence binding no
           derived fact is **every** citation, exactly as before.

        The §13.9 machinery below is untouched and still runs on any `Calculation` a draft
        carries: the refusal in point 1 is raised *beside* it, not instead of it, so
        `calculation_does_not_recompute`, `calculation_inputs_incomparable` and the rest stay
        reachable and stay tested against a corpus that can still produce a stored draft with
        one.
        """
        found: list[VerificationFinding] = []
        examined = 0
        seen_derived: set[str] = set()
        for sentence in draft.sentences:
            calculation = sentence.calculation
            derived_bindings = [
                (binding, derived)
                for binding in sentence.fact_bindings
                if (derived := index.derived_fact(binding.fact_id)) is not None
            ]
            # A sentence is examined here when the reported/calculated distinction applies to
            # it at all: it declared one of the two kinds, or it carries a derivation — a
            # `Calculation` or a binding to a derived fact. A `connective` sentence with none
            # of the three is not examined, and the denominator says so.
            if (sentence.kind in (SentenceKind.REPORTED, SentenceKind.CALCULATED)
                    or calculation is not None or derived_bindings):
                examined += 1
            if sentence.kind is SentenceKind.CALCULATED:
                if not derived_bindings:
                    found.append(finding(
                        "calculated_sentence_without_calculation",
                        sentence_index=sentence.index,
                        expected="at least one fact_binding to a derived fact",
                        observed=(f"{len(sentence.fact_bindings)} binding(s), none derived"
                                  if sentence.fact_bindings else "no bindings"),
                        explanation=(
                            "§6: a `calculated` sentence carries derived-fact bindings and "
                            "cites its inputs. A calculated number with nothing behind it is "
                            "the model's own arithmetic, which is the one thing S13 exists to "
                            "make unrepresentable."),
                        suggested_fact_ids=sorted(index.derived_facts),
                    ))
                found.extend(
                    self._calculated_citation_findings(sentence, index, derived_bindings))
            if calculation is not None:
                found.append(finding(
                    "reported_sentence_carries_calculation",
                    sentence_index=sentence.index,
                    expected="no Calculation on any sentence",
                    observed=f"kind={sentence.kind.value}, operation={calculation.operation}",
                    explanation=(
                        "§6: the writer no longer declares arithmetic — `calculation` left the "
                        "writer schema and `WRITER_OPERATIONS` with it. The type survives in "
                        "`story/core/models.py` so a stored draft from an earlier run still "
                        "reads back, and a draft carrying one is refused rather than silently "
                        "accepted: its number rests on the model."),
                ))
                found.extend(self._calculation_findings(sentence, calculation, index, ledgers))

            for binding, derived in derived_bindings:
                if derived.fact_id not in seen_derived:
                    seen_derived.add(derived.fact_id)
                    found.extend(derived_rules.integrity_findings(
                        derived, index, sentence_index=sentence.index))
                found.extend(derived_rules.orientation_findings(sentence, binding, derived))
        return CheckResult(name="reported_vs_calculated", examined=examined,
                           findings=tuple(found))

    def _calculated_citation_findings(
        self,
        sentence: DraftSentence,
        index: PackageIndex,
        derived_bindings: Sequence[tuple[FactBinding, DerivedFact]],
    ) -> list[VerificationFinding]:
        """§13.9's citation rule, narrowed to *"the inputs and nothing else"* (§6).

        The permitted set is the evidence handles this package minted for the **observations**
        the sentence's derived facts were computed from — never a handle for a derived fact,
        because §6 forbids minting one and `DerivedFact` has no field to hold one. A sentence
        binding no derived fact has an empty permitted set, so every passage citation on it
        refuses exactly as it did before this rule existed.
        """
        cited = [citation for citation in sentence.citations
                 if isinstance(citation, PassageCitation)]
        if not cited:
            return []
        permitted = {
            fact.evidence_handle
            for _binding, derived in derived_bindings
            for fact in index.derived_inputs(derived.fact_id)
            if fact.evidence_handle is not None
        }
        offending = [citation for citation in cited
                     if citation.evidence_handle not in permitted]
        if not offending:
            return []
        return [finding(
            "calculated_sentence_cites_passage",
            sentence_index=sentence.index,
            citation_ids=tuple(citation_rules.citation_id(c) for c in offending),
            fact_ids=tuple(derived.fact_id for _binding, derived in derived_bindings),
            expected=("a citation for an input of a bound derived fact: "
                      + (", ".join(sorted(permitted)) if permitted else
                         "this sentence binds no derived fact, so none is permitted")),
            observed=", ".join(sorted(citation.evidence_handle for citation in offending)),
            explanation=(
                "§13.9 and §6: a calculated sentence cites the observations its number was "
                "computed from and nothing else. claims.yaml gave `calculated` "
                "`optional_fields: []` when the number was the model's; now that code computes "
                "it, the filing is cited for the two readings that went in — and a citation to "
                "any other passage is still claiming the filing said something it did not."),
        )]

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
        found.extend(self._result_surface_findings(sentence, calculation, resolved))
        found.extend(self._recompute_findings(sentence, calculation, resolved, ledgers))
        return found

    def _result_surface_findings(
        self,
        sentence: DraftSentence,
        calculation: Calculation,
        inputs: Sequence[PackagedFact],
    ) -> list[VerificationFinding]:
        """§13.2 and §13.3 applied to a derived result, which had no unit rule at all.

        `percentage_point_surface_missing` governs `delta_pp`, `delta_bps` and
        `delta_relative`; every other operation rendered whatever it liked. Measured on the
        demo's own `compare_levels` sentence: `"15.9 basis points"` passed and the gap is 1,590
        bps, `"15.9x"` passed and the ratio is −0.26, and `"15.9 percent"` passed — the exact
        percentage-point-versus-percent confusion §13.3 exists to stop, on the metric whose two
        readings §13.3 measures 45.5× apart.

        Skipped when the inputs disagree about their unit, because `calculation_inputs_incompar-
        able` has already refused that and a second code for one fault helps nobody.
        """
        if not calculation.result_rendered:
            return []
        units = {fact.unit for fact in inputs}
        if len(units) != 1:
            return []
        unit = units.pop()
        if surface_supports_operation(
                calculation.operation, calculation.result_rendered, input_unit=unit):
            return []
        allowed = operation_result_surfaces(calculation.operation, unit)
        return [finding(
            "calculation_result_surface_mismatch",
            sentence_index=sentence.index,
            fact_ids=tuple(fact.observation_id for fact in inputs),
            expected=(f"a result in {' | '.join(sorted(surface.value for surface in allowed))} "
                      f"for {calculation.operation} over {unit} inputs"),
            observed=calculation.result_rendered,
            explanation=(
                "§13.3: a gap between two percent levels is in percentage points. The same "
                "number in basis points is a hundred times too small, in `x` is a ratio nobody "
                "computed, and in percent is the ambiguity §13.3's whole surface gate exists "
                "to refuse."),
        )]

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
            # **Refused rather than passed to §13.14 alone.** `_covering_spans` licenses the
            # `result_rendered` of these three, and nothing here ever recomputed one — so the
            # numeral in *"the only quarter, 1 of 26"* was covered by a declaration no check
            # evaluated. Latent today, because `WRITER_OPERATIONS` cannot emit them; §13 is
            # documented as authoritative independently of the writer, and the honest V1
            # answer is a refusal, not a recomputation nobody wrote.
            return [finding(
                "operation_not_recomputable",
                sentence_index=sentence.index,
                fact_ids=tuple(fact.observation_id for fact in inputs),
                expected="an operation with a scalar result §13.9 can recompute",
                observed=operation,
                explanation=(
                    "§13.9 says recompute. `extremum`, `absence` and `temporal_order` have no "
                    "scalar result and §13.14 checks only their machinery, while §13.1's "
                    "coverage rule was still licensing whatever numeral they rendered. An "
                    "operation nothing recomputes must not cover a numeral."),
            )]
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
                    derived = index.derived_fact(binding.fact_id)
                    if derived is not None and derived.warning_codes:
                        examined += 1
                        # §10.1's disclosure channel, carrying what §4.1 could not decide.
                        # `metric_sign_convention_unverified` is the live one: the quantity is
                        # real and citable and only the word describing its direction is
                        # unavailable, so the fact is bound with the warning beside it rather
                        # than dropped — which is `quantity_direction`'s own call, surfaced.
                        found.append(finding(
                            "warned_observation_used",
                            sentence_index=sentence.index,
                            char_start=binding.char_start, char_end=binding.char_end,
                            fact_ids=(derived.fact_id,),
                            expected="the warning surfaced in the evidence panel",
                            observed=", ".join(derived.warning_codes),
                            explanation=(
                                "§13.13, over a derived fact: R9's disclosure codes and the "
                                "unmeasured sign conventions travel on the row and must reach "
                                "the reader beside the claim they qualify."),
                        ))
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
    "GROUNDED_SENTENCE_KINDS",
    "OPERATION_INPUTS",
    "REQUIRED_WARNING_QUALIFIERS",
    "SURFACE_UNITS",
    "DeterministicVerifier",
]
