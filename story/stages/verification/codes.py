"""§13.17's gate, as a table rather than as a rule repeated at fifty call sites.

Responsibility: for every deterministic code §13.1–§13.15 can raise, what severity it carries,
what would fix it, and therefore whether it blocks. Nothing here examines a draft — the checks
live beside the thing they check and reach for `finding()` when they have decided.

**Why a table and not a severity argument at each site.** §13.17 states the gate as a closed
rule: *"Every deterministic code in §13.1–§13.15 is REFUSE except: `over_precision` and
`paraphrase_distance` (WARN); `event_review_flag`,
`conflict_immaterial_at_stated_precision` and `warned_observation_used` (ANNOTATE)."* A rule
of that shape is only true if it is expressed once. A check that passed `Severity.WARN` at its
own call site could weaken itself to make a demo accept, which is exactly what this step was
told not to allow; here, weakening a code is a one-line diff to a table with the plan section
beside it.

**Three extensions to §13.17's tiers, each named rather than smuggled.**

1. `column_label_ambiguity_classified` (ANNOTATE). §13.7.1's second escape hatch says a D15
   classification lets a binding *"proceed with the classification and the minority reading
   rendered in the evidence panel"*. Proceeding silently would lose the panel row, and §13.17
   lists only three ANNOTATE codes. This is the fourth, and it exists because the hatch is
   worthless without it.
2. `absence_not_provable_from_bounded_package` (REFUSE). §13.14 gives absence claims an
   `operation: absence` and says the verifier *"confirms the package's own coverage"*. The
   package is a **bounded** subset of the graph (§10.2 caps facts at twelve), so its coverage
   can never establish that a metric was not reported — only that this package does not carry
   it. The honest V1 answer is a refusal with its own name, not a pass derived from a cap.
3. `extremum_expression_not_supported` (REFUSE). §13.14 requires the verifier to *"recompute
   the extremum over that exact set"* and never says in what language the claim is written.
   `Calculation.expression` is a free string, so the recomputable forms are a closed set here
   and anything else is refused rather than assumed satisfied.
4. `comparative_not_supported_by_text` (REFUSE). §13.14 gives a comparative an `operation` and
   two sides and stops there; the sides are positional and the sentence names them in words, so
   recomputing the declaration alone accepts the sentence that reverses it — the same number
   and the opposite claim. This is the code for *"the calculation and the prose disagree"*, and
   it exists because the operations it guards became writable in the same change.

**R8's four, and the one asymmetry that explains all of them.** §13 checks what the writer
*declares*; in several places it never checked what the writer *wrote*. A `period_surface` is
protected because a period contains numerals and §13.1's coverage rule forces it to occur in
the text — a prose period the declaration disagrees with is `unbound_numeral` or
`period_mismatch`, measured both ways. **A metric name carries no numeral, so nothing forced
it to occur at all**, and rewriting the demo's *"GAAP Gross Margin"* to *"Adjusted Gross
Margin"* — or to *"net income"* — passed with zero findings while the binding stayed put.

5. `metric_surface_absent_from_text` and `metric_named_in_text_contradicts_binding` (REFUSE,
   §13.5). A `reported` sentence's prose must name a metric the binding's fact answers to,
   resolved through the same alias index the declared surface is.
6. `calculation_result_surface_mismatch` (REFUSE, §13.3). The unit of a derived result was
   checked for `delta_pp`, `delta_bps` and `delta_relative` and for nothing else, so a
   percentage-point gap rendered `"15.9 basis points"`, `"15.9x"` or `"15.9 percent"` passed.
7. `operation_not_recomputable` (REFUSE, §13.9). `_covering_spans` licensed the rendered
   numeral of `extremum`, `absence` and `temporal_order` while `_recompute_findings` never
   recomputed one. Unreachable from `WRITER_OPERATIONS` today — but the verifier is
   authoritative independently of the writer, so the honest answer is a refusal and not a
   recomputation nobody wrote.

**Two remedies §13.17's enum cannot express**, recorded because the enum lives in
`story/core/models.py`, which this step does not own: a dropped `required_warning` and an
absent counterpoint both want *"put the disclosure back"*, and the nearest member is
`ADD_CONFLICT_DISCLOSURE`. It is used, and it reads slightly wrong. Widening `Remedy` is the
right fix and is a plan item, not a change to make from inside a check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from story.core.models import Remedy, Severity, VerificationFinding


@dataclass(frozen=True, slots=True)
class GateEntry:
    """One deterministic code and everything §13.17 says about it.

    `blocking` is derived and not stored, for the same reason `VerifiedDraft.passed` is: a
    table that could hold `REFUSE` beside `blocking=False` is a table that will eventually be
    written that way, and `VerificationFinding`'s own validator would then reject a finding
    this module built.
    """

    code: str
    severity: Severity
    remedy: Remedy
    section: str

    @property
    def blocking(self) -> bool:
        return self.severity is Severity.REFUSE


def _refuse(code: str, remedy: Remedy, section: str) -> GateEntry:
    return GateEntry(code=code, severity=Severity.REFUSE, remedy=remedy, section=section)


def _warn(code: str, remedy: Remedy, section: str) -> GateEntry:
    return GateEntry(code=code, severity=Severity.WARN, remedy=remedy, section=section)


def _annotate(code: str, remedy: Remedy, section: str) -> GateEntry:
    return GateEntry(code=code, severity=Severity.ANNOTATE, remedy=remedy, section=section)


_ENTRIES: tuple[GateEntry, ...] = (
    # -- §13.13 run consistency, and §10.3's identity block -------------------------------
    _refuse("candidate_id_mismatch", Remedy.REBUILD_PACKAGE, "13.13"),
    _refuse("package_id_mismatch", Remedy.REBUILD_PACKAGE, "13.13"),
    _refuse("graph_run_id_mismatch", Remedy.REBUILD_PACKAGE, "13.13"),
    _refuse("graph_input_digest_mismatch", Remedy.REBUILD_PACKAGE, "13.13"),
    _refuse("package_content_digest_mismatch", Remedy.REBUILD_PACKAGE, "13.13"),
    _refuse("plan_names_another_package", Remedy.REBUILD_PACKAGE, "13.13"),
    _refuse("fact_not_in_package", Remedy.REBIND_TO_FACT, "13.13"),
    _refuse("citation_not_in_package", Remedy.REBIND_TO_FACT, "13.13"),
    _annotate("warned_observation_used", Remedy.DROP_SENTENCE, "13.13"),

    # -- §13.1 numbers ---------------------------------------------------------------------
    _refuse("unbound_numeral", Remedy.REBIND_TO_FACT, "13.1"),
    _refuse("binding_span_does_not_match_text", Remedy.REBIND_TO_FACT, "13.1"),
    _refuse("binding_rendering_is_not_one_numeral", Remedy.REBIND_TO_FACT, "13.1"),
    _refuse("number_outside_tolerance", Remedy.REBIND_TO_FACT, "13.1"),
    _refuse("sign_disagreement", Remedy.REBIND_TO_FACT, "13.1"),
    _warn("over_precision", Remedy.REBIND_TO_FACT, "13.1"),

    # -- §13.2 units and currency ----------------------------------------------------------
    _refuse("unit_mismatch", Remedy.REBIND_TO_FACT, "13.2"),
    _refuse("currency_symbol_on_non_monetary_unit", Remedy.REBIND_TO_FACT, "13.2"),
    _refuse("monetary_unit_without_currency", Remedy.REBUILD_PACKAGE, "13.2"),

    # -- §13.3 percentages -----------------------------------------------------------------
    _refuse("percent_change_ambiguous", Remedy.ADD_PERCENTAGE_POINT_QUALIFIER, "13.3"),
    _refuse("percentage_point_surface_missing", Remedy.ADD_PERCENTAGE_POINT_QUALIFIER, "13.3"),
    _refuse("percent_change_reported_not_calculated", Remedy.RESTATE_AS_CALCULATION, "13.3"),
    _refuse("relative_change_across_zero", Remedy.RESTATE_AS_CALCULATION, "13.3"),
    _refuse("calculation_result_surface_mismatch",
            Remedy.ADD_PERCENTAGE_POINT_QUALIFIER, "13.3"),

    # -- §13.4 periods ---------------------------------------------------------------------
    _refuse("period_unresolvable", Remedy.ADD_PERIOD_QUALIFIER, "13.4"),
    _refuse("period_mismatch", Remedy.ADD_PERIOD_QUALIFIER, "13.4"),
    _refuse("period_shape_conflated", Remedy.ADD_PERIOD_QUALIFIER, "13.4"),
    _refuse("incomparable_periods", Remedy.ADD_PERIOD_QUALIFIER, "13.4"),

    # -- §13.5 metric identity -------------------------------------------------------------
    _refuse("metric_surface_unresolved", Remedy.NARROW_METRIC_SURFACE, "13.5"),
    _refuse("metric_surface_ambiguous", Remedy.NARROW_METRIC_SURFACE, "13.5"),
    _refuse("metric_binding_mismatch", Remedy.NARROW_METRIC_SURFACE, "13.5"),
    _refuse("mutually_distinct_group_ambiguity", Remedy.NARROW_METRIC_SURFACE, "13.5"),
    _refuse("metric_surface_absent_from_text", Remedy.NARROW_METRIC_SURFACE, "13.5"),
    _refuse("metric_named_in_text_contradicts_binding", Remedy.NARROW_METRIC_SURFACE, "13.5"),

    # -- §13.6 / §13.11 subject and entity identity ----------------------------------------
    _refuse("foreign_subject_named", Remedy.DROP_SENTENCE, "13.6"),
    _refuse("unresolved_entity_named", Remedy.DROP_SENTENCE, "13.11"),

    # -- §13.7 citation support ------------------------------------------------------------
    _refuse("citation_span_not_in_passage", Remedy.REBIND_TO_FACT, "13.7"),
    _refuse("citation_quote_not_in_passage", Remedy.REBIND_TO_FACT, "13.7"),
    _refuse("table_quote_does_not_reconstruct", Remedy.REBIND_TO_FACT, "13.7"),
    _refuse("citation_does_not_support_fact", Remedy.REBIND_TO_FACT, "13.7"),
    _refuse("citation_reused_for_unrelated_claim", Remedy.REBIND_TO_FACT, "13.7"),
    _refuse("row_label_not_licensed_for_metric", Remedy.NARROW_METRIC_SURFACE, "13.7"),
    _refuse("column_label_ambiguous_in_passage",
            Remedy.REBIND_TO_DISTINGUISHING_COLUMN, "13.7.1"),
    _annotate("column_label_ambiguity_classified",
              Remedy.REBIND_TO_DISTINGUISHING_COLUMN, "13.7.1"),
    _refuse("narrative_span_missing_number", Remedy.REBIND_TO_FACT, "13.7"),
    _warn("paraphrase_distance", Remedy.DROP_SENTENCE, "13.7"),
    _refuse("counter_evidence_cited_as_support", Remedy.DROP_SENTENCE, "13.7"),
    _refuse("evidence_kind_not_supported_in_v1", Remedy.REBIND_TO_FACT, "13.7.2"),
    _refuse("uncited_factual_sentence", Remedy.REBIND_TO_FACT, "13.7"),

    # -- §13.8 events ----------------------------------------------------------------------
    _refuse("event_property_bound_as_fact", Remedy.REBIND_TO_FACT, "13.8"),
    _refuse("date_not_in_package", Remedy.DROP_SENTENCE, "13.8"),
    _annotate("event_review_flag", Remedy.DROP_SENTENCE, "13.8"),

    # -- §13.9 reported versus calculated --------------------------------------------------
    _refuse("calculated_sentence_cites_passage", Remedy.RESTATE_AS_CALCULATION, "13.9"),
    _refuse("calculated_sentence_without_calculation", Remedy.RESTATE_AS_CALCULATION, "13.9"),
    _refuse("reported_sentence_carries_calculation", Remedy.RESTATE_AS_CALCULATION, "13.9"),
    _refuse("calculation_inputs_unresolved", Remedy.RESTATE_AS_CALCULATION, "13.9"),
    _refuse("calculation_inputs_incomparable", Remedy.ADD_PERIOD_QUALIFIER, "13.9"),
    _refuse("calculation_does_not_recompute", Remedy.RESTATE_AS_CALCULATION, "13.9"),
    _refuse("calculation_operation_not_supported", Remedy.RESTATE_AS_CALCULATION, "13.9"),
    _refuse("operation_not_recomputable", Remedy.RESTATE_AS_CALCULATION, "13.9"),
    _refuse("formula_version_not_valid_for_period", Remedy.RESTATE_AS_CALCULATION, "13.9"),

    # -- §13.10 causation ------------------------------------------------------------------
    _refuse("causal_construction_forbidden", Remedy.REMOVE_CAUSAL_CONSTRUCTION, "13.10"),
    _refuse("causal_attribution_frame_missing", Remedy.ADD_ATTRIBUTION_FRAME, "13.10"),
    _refuse("causal_marker_not_in_cited_span", Remedy.ADD_ATTRIBUTION_FRAME, "13.10"),
    _refuse("causal_marker_negated_in_span", Remedy.REMOVE_CAUSAL_CONSTRUCTION, "13.10"),
    _refuse("causal_marker_ambiguous_in_span", Remedy.REMOVE_CAUSAL_CONSTRUCTION, "13.10"),
    _refuse("causal_frame_document_mismatch", Remedy.ADD_ATTRIBUTION_FRAME, "13.10"),

    # -- §13.12 conflicting facts ----------------------------------------------------------
    _refuse("conflict_not_disclosed", Remedy.ADD_CONFLICT_DISCLOSURE, "13.12"),
    _annotate("conflict_immaterial_at_stated_precision",
              Remedy.ADD_CONFLICT_DISCLOSURE, "13.12"),

    # -- §13.14 numeral-free sentences -----------------------------------------------------
    _refuse("connective_sentence_carries_a_claim", Remedy.DROP_SENTENCE, "13.14"),
    _refuse("unsupported_superlative", Remedy.DROP_SENTENCE, "13.14"),
    _refuse("unsupported_comparative", Remedy.DROP_SENTENCE, "13.14"),
    _refuse("unsupported_absence_claim", Remedy.DROP_SENTENCE, "13.14"),
    _refuse("unsupported_temporal_ordering", Remedy.DROP_SENTENCE, "13.14"),
    _refuse("extremum_recomputation_failed", Remedy.DROP_SENTENCE, "13.14"),
    _refuse("extremum_expression_not_supported", Remedy.DROP_SENTENCE, "13.14"),
    _refuse("comparative_recomputation_failed", Remedy.DROP_SENTENCE, "13.14"),
    _refuse("comparative_not_supported_by_text", Remedy.DROP_SENTENCE, "13.14"),
    _refuse("unpopulated_metric", Remedy.DROP_SENTENCE, "13.14"),
    _refuse("absence_not_provable_from_bounded_package", Remedy.DROP_SENTENCE, "13.14"),

    # -- §13.15 forward-looking language ---------------------------------------------------
    _refuse("forward_looking_language", Remedy.DROP_SENTENCE, "13.15"),

    # -- §11 / §13 disclosures the plan asked for ------------------------------------------
    _refuse("required_warning_absent", Remedy.ADD_CONFLICT_DISCLOSURE, "13.17"),
    _refuse("required_warning_has_no_declared_qualifier",
            Remedy.ADD_CONFLICT_DISCLOSURE, "13.17"),
    _refuse("required_counterpoint_absent", Remedy.ADD_CONFLICT_DISCLOSURE, "11"),
)

#: The gate, keyed by code. Built from a tuple so a duplicated code is a construction error
#: rather than a silent last-one-wins overwrite of a severity.
GATE: Mapping[str, GateEntry] = {}
for _entry in _ENTRIES:
    if _entry.code in GATE:  # pragma: no cover - a duplicate is a source edit, not a state
        raise ValueError(f"{_entry.code} appears twice in the gate")
    GATE[_entry.code] = _entry  # type: ignore[index]
del _entry


class UndeclaredCode(KeyError):
    """A check asked the gate about a code the gate does not carry.

    Raised rather than defaulted to REFUSE: a code with no declared severity is a check nobody
    put through §13.17, and defaulting it would make the gate's closure unverifiable.
    """


def finding(
    code: str,
    *,
    sentence_index: int | None = None,
    char_start: int | None = None,
    char_end: int | None = None,
    fact_ids: Sequence[str] = (),
    citation_ids: Sequence[str] = (),
    expected: str = "",
    observed: str = "",
    explanation: str = "",
    suggested_fact_ids: Sequence[str] = (),
) -> VerificationFinding:
    """One finding, with severity, remedy and blocking taken from the gate and not the caller.

    Every check in this stage builds findings through here. That is what makes *"no model may
    override a deterministic verdict"* and *"do not weaken a check to make a demo accept"*
    checkable properties of one table rather than promises spread over six modules.
    """
    entry = GATE.get(code)
    if entry is None:
        raise UndeclaredCode(f"{code} is not in §13.17's gate")
    return VerificationFinding(
        code=code,
        severity=entry.severity,
        sentence_index=sentence_index,
        char_start=char_start,
        char_end=char_end,
        fact_ids=tuple(fact_ids),
        citation_ids=tuple(citation_ids),
        expected=expected,
        observed=observed,
        explanation=explanation,
        remedy=entry.remedy,
        blocking=entry.blocking,
        # §13.17 caps the list at five and `VerificationFinding` enforces it; truncating here
        # keeps a check from having to remember the cap while assembling suggestions.
        suggested_fact_ids=tuple(suggested_fact_ids)[:5],
    )


__all__ = [
    "GATE",
    "GateEntry",
    "UndeclaredCode",
    "finding",
]
