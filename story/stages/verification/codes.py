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

**R9's two, and the correction that produced them.** R8's note above says a `period_surface` is
protected *"because a period contains numerals"*. **That is false, and it was false when it was
written** — it holds only when the period phrase carries a numeral, and every counter-example it
was tested against happened to include a year. Measured against R8's own commit on the demo's
accepted draft, with the binding still declaring the true `"the third quarter of 2022"`:
*"…for the fourth quarter."*, *"…for the full year."* and *"…for the most recent quarter."* each
**passed with zero findings**. The period surface was protected only by accident, and only as
far as a numeral reached.

8. `period_surface_absent_from_text` and `period_named_in_text_contradicts_binding` (REFUSE,
   §13.4). A period the sentence *names* must be the period its declaration binds, resolved
   through §13.4's own closed grammar. A sentence naming no period at all is unchanged — see
   `DeterministicVerifier._period_grounding_findings` for why that asymmetry with the metric
   rule is deliberate.

**S5's seven, and why a repair that removed a check from §12 adds six to §13.**
TABLE_CELL_CITATIONS moved the writer's citation from a retyped quote to an evidence handle the
package minted, and §3.4 states what §13.7 then owes. Six of these are that list; the seventh is
recorded below as an addition to it.

9. `unresolvable_evidence_handle` and `evidence_handle_not_for_fact` (REFUSE, §13.7, §3.4
   checks 1 and 7). A handle names one cell and — §1.4, re-measured live 2026-08-18 — a cell
   names one fact: 2,690 table-backed observations, 2,690 distinct
   `(passage_id, row_index, value_column_index)` cells, **0** mapping to two `period_key`s and
   **0** to two `metric_id`s. So *"the handle this package minted for `F`"* is well defined, and
   citing another fact's cell is detectable. It was not detectable before, and the gap was total
   rather than marginal: §13.7's existing test is *"the passage each bound fact was read from"*,
   and **all 144** table-backed passages in the corpus evidence more than one observation — 2,690
   of 2,690 observations, up to 70 in one passage *(verified live 2026-08-18)*. On table
   evidence that test separated nothing.
10. `evidence_handle_out_of_bounds`, `evidence_cell_value_mismatch`,
   `evidence_row_label_mismatch` and `evidence_column_label_mismatch` (REFUSE, §13.7, §3.4
   checks 2–5) carry **REBUILD_PACKAGE** and not REBIND_TO_FACT, which is the one place this
   family's remedy differs from §13.7's others. The coordinates come off the `PackagedFact`, not
   off the model's answer, so a draft cannot cause any of the four and no rebinding fixes one:
   they say the package and the passage text it carries disagree about the table. All three
   equalities hold on **2,690 / 2,690** *(verified live 2026-08-18)*, so a failure is a defect
   in the evidence and the honest instruction is to rebuild it.
11. `evidence_cell_span_mismatch` (REFUSE, §13.7). **Not in §3.4's list**, and added because
   §3.4 reads as though the handle were the only thing on a citation row. `PassageCitation` also
   carries `char_start`/`char_end` — what the evidence panel highlights and what §13.7's reuse
   rule keys on — and checks 1–5 and 7 never look at them. A citation carrying `F`'s handle and
   a span over another cell would then have a verified handle and unverified bytes. §12 derives
   the span from the handle, so this cannot come from a model; it is the same class of defect as
   check 2 and is refused rather than assumed away.

**S13's seven, and the one thing they change about the table's shape.** DETERMINISTIC_FACT_TOOLS
§6 adds `derivation_not_offered`, `derived_fact_not_in_run`, `derived_fact_orientation_reversed`,
`derived_result_mismatch`, `derived_inputs_incomparable`, `derived_operation_not_supported` and
`derived_unit_mismatch`, all REFUSE. Five of the seven are spelled exactly as
`story/stages/derivation/public.py:DerivationRefusalCode` spells them, on purpose: the
derivation stage refuses a request it will not execute and this stage refuses a fact whose
result it cannot re-derive, and a fault that can arise on both sides has one name and not two.

12. Nothing in §13.17's tiering changes: the WARN pair and the ANNOTATE four are what they
   were, so `test_every_code_in_the_gate_is_a_refusal_except_the_five_the_plan_names` holds
   unedited. What did change is the *meaning* of two §13.9 codes, and both are argued at the
   check rather than here — `calculated_sentence_without_calculation` now asks for a
   derived-fact binding, and `reported_sentence_carries_calculation` now fires on a
   `Calculation` under any sentence kind. See `deterministic._check_reported_vs_calculated`.

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
    _refuse("period_surface_absent_from_text", Remedy.ADD_PERIOD_QUALIFIER, "13.4"),
    _refuse("period_named_in_text_contradicts_binding", Remedy.ADD_PERIOD_QUALIFIER, "13.4"),

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

    # -- §13.7 over evidence handles: TABLE_CELL_CITATIONS §3.4, notes 9-11 above -----------
    #
    # Split by who can cause them. The first two are the draft's — it named a handle nothing
    # minted, or a handle for a fact it did not bind — and rebinding fixes both. The last four
    # are the package's own coordinates disagreeing with its own passage text, which no
    # rebinding reaches.
    _refuse("unresolvable_evidence_handle", Remedy.REBIND_TO_FACT, "13.7"),
    _refuse("evidence_handle_not_for_fact", Remedy.REBIND_TO_FACT, "13.7"),
    _refuse("evidence_cell_span_mismatch", Remedy.REBIND_TO_FACT, "13.7"),
    _refuse("evidence_handle_out_of_bounds", Remedy.REBUILD_PACKAGE, "13.7"),
    _refuse("evidence_cell_value_mismatch", Remedy.REBUILD_PACKAGE, "13.7"),
    _refuse("evidence_row_label_mismatch", Remedy.REBUILD_PACKAGE, "13.7"),
    _refuse("evidence_column_label_mismatch", Remedy.REBUILD_PACKAGE, "13.7"),
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

    # -- DETERMINISTIC_FACT_TOOLS §6 and §7: derived facts ---------------------------------
    #
    # Seven, and the plan names all seven as REFUSE. They are §13.9's family under a different
    # authority: §13.9 recomputed a quantity the *writer* declared, and these recompute one
    # **code** computed and the draft merely bound. The remedies differ across the seven for
    # that reason — a draft can cause three of them by binding the wrong row, and the other
    # four say the derived-facts artifact and the package disagree, which no rebinding reaches.
    #
    # `derived_operation_not_supported` carries DROP_SENTENCE rather than
    # RESTATE_AS_CALCULATION, which is the remedy every §13.9 sibling carries. Restating as a
    # `Calculation` is no longer a repair: §6 retires the writer's `Calculation` and this
    # module now refuses one wherever it appears, so instructing a repair loop to write one
    # would be instructing it to produce a second refusal.
    #
    # `derived_unit_mismatch` carries ADD_PERCENTAGE_POINT_QUALIFIER, joining the two §13.3
    # codes, because the case it exists for is the percent-versus-percentage-point confusion —
    # §13.3's *"single most likely factual error this package can make"* — reaching the draft
    # through a derived result instead of through a `Calculation`.
    _refuse("derivation_not_offered", Remedy.REBUILD_PACKAGE, "6"),
    _refuse("derived_fact_not_in_run", Remedy.REBIND_TO_FACT, "6"),
    _refuse("derived_fact_orientation_reversed", Remedy.REBIND_TO_FACT, "6"),
    _refuse("derived_result_mismatch", Remedy.REBUILD_PACKAGE, "6"),
    _refuse("derived_inputs_incomparable", Remedy.REBIND_TO_FACT, "6"),
    _refuse("derived_operation_not_supported", Remedy.DROP_SENTENCE, "6"),
    _refuse("derived_unit_mismatch", Remedy.ADD_PERCENTAGE_POINT_QUALIFIER, "6"),

    # -- H1's two, both REFUSE, both closing a rule that abstained -------------------------
    #
    # `derived_direction_not_stated_in_text` is the fail-closed half of §6's orientation rule.
    # The seven above ask *"is the derived fact right"*; this one asks *"does the sentence say
    # what it computed"*, which nothing asked: a change verb outside `CHANGE_DIRECTION`'s
    # thirteen words made the rule abstain silently, and a change stated as a level had no verb
    # to match at all. It is a separate code from `derived_fact_orientation_reversed` because
    # the two are different faults with different repairs — one sentence says the opposite thing
    # and the other says no thing — and a shared name would make the panel's remedy wrong for
    # whichever it was not written for. DROP_SENTENCE, because the repair is in the words and
    # not in the binding.
    #
    # `evidence_scope_binding_declares_a_surface` is §7's, and it is the one code here whose
    # subject is a field nothing reads. See `derived_facts.scope_findings` refusal 3.
    _refuse("derived_direction_not_stated_in_text", Remedy.DROP_SENTENCE, "6"),
    _refuse("evidence_scope_binding_declares_a_surface", Remedy.REBIND_TO_FACT, "7"),

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
