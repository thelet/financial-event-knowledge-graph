"""What every refusal code *means*, in a sentence a non-technical reader can act on.

Responsibility: one short human-readable description per code, joined at read time to the
severity, remedy, section and kind the real tables already declare. Nothing here decides
anything — it renders. The gate still refuses, the packager still discloses, the freshness gate
still blocks; this module only says, in English, what happened.

**Why this exists at all.** `story/stages/verification/codes.py:GateEntry` carries
`{code, severity, remedy, section}` and **no description**, and the 30 packaging codes carry
theirs as `#:` comments, which are not data. So a UI holding a `VerificationFinding` can render
`metric_named_in_text_contradicts_binding`, `REFUSE` and `13.5` and still not tell a reader what
went wrong. The missing column is the whole reason for this file.

**Why it lives in `demo_ui/` and not in the stages.** Adding a `description` to `GateEntry`
would move `verifier_gate_digest()` — `story/pipeline.py` digests the gate table into every
demo manifest — so a copy-edit to a sentence would mint new run identities. A display string
must not be able to do that. The catalogue is therefore additive and one-directional: it
imports the tables, it never edits them, and everything except the sentence is **read from
them** so severity, remedy, section and kind cannot drift out of agreement.

**Totality is a test, not an intention.** `tests/story/test_demo_ui_code_catalogue.py` asserts
this catalogue is total over `GATE`, over `warning_codes.SEVERITY_OF`, and over the planner's,
the writer's and the freshness gate's code sets, in both directions. A code added upstream with
no description here fails the suite; a description here for a code no stage declares fails it
too. That is what stops the catalogue rotting into a second, stale vocabulary.

**Codes collide across families, and the collision is real rather than an accident of naming.**
`citation_quote_not_in_passage` is both a §12 writer refusal (the draft could not be built) and
a §13.7 verifier refusal (the draft was built and then rejected). Six codes are shared, measured
2026-08-04: `citation_quote_not_in_passage`, `event_review_flag`, `graph_run_id_mismatch`,
`plan_names_another_package`, `unresolvable_fact_id` and `unresolvable_passage_id` — and
`graph_run_id_mismatch` is the sharpest, because the freshness gate raises it about the *loaded
database* and §13.13 raises it about a *package*. The catalogue is therefore keyed by
`(family, code)` and `explain()` takes an optional family; a bare lookup resolves in a declared
precedence order and says which family answered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from story.core.models import Severity, WarningCategory, WarningKind
from story.stages.freshness.freshness_report import RefusalCode
from story.stages.generation import planner, writer
from story.stages.packaging.warning_codes import CATEGORY_OF, KIND_OF, SEVERITY_OF
from story.stages.verification.codes import GATE

#: The five vocabularies a demo reader can meet, named for the stage that raises them. The
#: order is the precedence `explain()` uses for a bare code: the verifier's gate is what an
#: evidence panel renders most, and a §12 refusal is the rarest thing a reader sees.
FAMILY_VERIFICATION = "verification_gate"
FAMILY_PACKAGE_WARNING = "package_warning"
FAMILY_FRESHNESS = "freshness_refusal"
FAMILY_PLANNER = "planner_refusal"
FAMILY_WRITER = "writer_refusal"

FAMILY_ORDER: tuple[str, ...] = (
    FAMILY_VERIFICATION,
    FAMILY_PACKAGE_WARNING,
    FAMILY_FRESHNESS,
    FAMILY_PLANNER,
    FAMILY_WRITER,
)

#: What each family *is*, for the panel heading above the code. One sentence, same register as
#: the code descriptions themselves.
FAMILY_DESCRIPTIONS: Mapping[str, str] = {
    FAMILY_VERIFICATION: (
        "The deterministic verifier's own vocabulary (§13). It runs after generation, on the "
        "server, and it is the final authority on whether a post is published."
    ),
    FAMILY_PACKAGE_WARNING: (
        "Disclosures the evidence package carries about the evidence it holds and about how it "
        "was built (§10.1). They travel with the facts and some of them a post must say out loud."
    ),
    FAMILY_FRESHNESS: (
        "The staleness gate (§7). It runs before anything reads the graph and refuses when the "
        "loaded graph no longer matches the run it was built from."
    ),
    FAMILY_PLANNER: (
        "Reasons an editorial plan never reached the writer (§11). The model answered and the "
        "answer satisfied its schema; the plan was refused on its content."
    ),
    FAMILY_WRITER: (
        "Reasons a draft could not be constructed at all (§12) — these fire before the verifier "
        "ever sees a draft, so they are not verification findings."
    ),
}


@dataclass(frozen=True, slots=True)
class CodeExplanation:
    """One code, ready to render: what it means, and everything the real tables say about it.

    `severity`, `remedy`, `section`, `warning_kind`, `warning_category` and `blocking` are read
    from the declaring stage and never restated here, so the only thing this module owns is
    `description`. A field the family does not declare is the empty string rather than a
    plausible default: the planner and the writer declare no severity, and printing one would
    invent a gate they do not have.

    `warning_category` is §4 S5's finer split and is what lets a panel say *"a limit of this
    version"* rather than *"a warning"* about the three codes that fire on every package because
    V1 has no tool for them. Read from `CATEGORY_OF` for the same reason as the rest — a second
    copy of a classification is a classification that can disagree with itself.
    """

    code: str
    family: str
    description: str
    severity: str = ""
    remedy: str = ""
    section: str = ""
    warning_kind: str = ""
    warning_category: str = ""
    blocking: bool = False

    def as_dict(self) -> dict[str, object]:
        """A plain JSON row. No model text reaches this type, so none can leave it."""
        return {
            "code": self.code,
            "family": self.family,
            "description": self.description,
            "severity": self.severity,
            "remedy": self.remedy,
            "section": self.section,
            "warning_kind": self.warning_kind,
            "warning_category": self.warning_category,
            "blocking": self.blocking,
        }


# -- §13's gate, 85 codes ----------------------------------------------------------------------
#
# Each sentence is derived from the code's own call site and the section `GATE` cites, not from
# the name. Where a description says *why* rather than only *what*, the reason is the one the
# call site's `explanation` gives — those strings are the plan's argument, restated short.

VERIFICATION_DESCRIPTIONS: Mapping[str, str] = {
    # §13.13 run consistency and §10.3's identity block
    "candidate_id_mismatch":
        "The draft was checked against a different story candidate from the one the evidence "
        "package was built for.",
    "package_id_mismatch":
        "The draft names a different evidence package from the one it is being verified "
        "against.",
    "graph_run_id_mismatch":
        "The evidence package was built from a different graph run than the one being verified "
        "against.",
    "graph_input_digest_mismatch":
        "The extraction inputs or the ontology behind this package are no longer the ones the "
        "graph was built from, so the numbers may have moved underneath the post.",
    "package_content_digest_mismatch":
        "The evidence package no longer hashes to its own recorded digest — its contents changed "
        "after it was sealed.",
    "plan_names_another_package":
        "The editorial plan was written against a different evidence package, so it cannot judge "
        "this draft.",
    "fact_not_in_package":
        "The draft binds a number to a fact the evidence package does not contain.",
    "citation_not_in_package":
        "The draft cites a passage or source the evidence package does not contain.",
    "warned_observation_used":
        "The claim rests on an observation flagged during extraction, so the warning has to be "
        "shown beside it.",

    # §13.1 numbers
    "unbound_numeral":
        "A number appears in the text with no fact bound to it, so nothing checked it.",
    "binding_span_does_not_match_text":
        "The declared position of a number does not hold the number it claims, so every other "
        "check would be reading the wrong characters.",
    "binding_rendering_is_not_one_numeral":
        "The span bound to a fact holds no number, or more than one, so there is nothing "
        "single to compare.",
    "number_outside_tolerance":
        "The number written does not match the filed value, even allowing for the precision the "
        "draft itself printed.",
    "sign_disagreement":
        "The number carries a sign opposite to the filed value's — a gain written where the "
        "filing reports a loss, or the reverse.",
    "over_precision":
        "The number is written to more decimal places than the filing printed. A warning, not a "
        "refusal.",

    # §13.2 units and currency
    "unit_mismatch":
        "The unit written is not the unit of the fact bound to it — a percentage rendered as "
        "dollars, or a level rendered as a change.",
    "currency_symbol_on_non_monetary_unit":
        "A currency symbol is attached to a value that is not money.",
    "monetary_unit_without_currency":
        "A monetary value is stated with no currency recorded anywhere for it.",

    # §13.3 percentages
    "percent_change_ambiguous":
        "A percentage change is written so that it could mean either percentage points or a "
        "relative change, and the two readings differ by a wide, base-dependent factor.",
    "percentage_point_surface_missing":
        "A gap between two percentages is stated without the wording — 'percentage points', or "
        "an explicit relative marker — that says which reading is meant.",
    "percent_change_reported_not_calculated":
        "A change in a percentage metric is presented as something a filing reported, when no "
        "filing reported a change; it must be labelled as calculated.",
    "relative_change_across_zero":
        "A relative percentage change was taken across zero, where the arithmetic is defined and "
        "the result is meaningless.",
    "calculation_result_surface_mismatch":
        "The derived result is rendered in the wrong unit — basis points, a multiple or a plain "
        "percent where the calculation produced percentage points.",

    # §13.4 periods
    "period_unresolvable":
        "The period the sentence names cannot be resolved to a specific quarter, year or date by "
        "the closed grammar the verifier uses.",
    "period_mismatch":
        "The period the sentence names is not the period of the fact bound to it.",
    "period_shape_conflated":
        "The sentence conflates two period shapes that share an end date — a quarter written as "
        "if it were the full year, or the reverse.",
    "incomparable_periods":
        "A calculation combines periods of different shapes, so the two sides do not measure the "
        "same span of time.",
    "period_surface_absent_from_text":
        "The period the binding declares never appears in the sentence, so the prose is unbound "
        "in time.",
    "period_named_in_text_contradicts_binding":
        "The period the sentence names in words disagrees with the period its binding declares.",

    # §13.5 metric identity
    "metric_surface_unresolved":
        "The metric name used in the sentence resolves to no metric in the ontology.",
    "metric_surface_ambiguous":
        "The metric name used could mean more than one metric — 'gross margin' is both the GAAP "
        "and the adjusted measure — so the post must narrow it.",
    "metric_binding_mismatch":
        "The metric named in the sentence is not the metric of the fact bound to it.",
    "mutually_distinct_group_ambiguity":
        "The metric name spans a group of metrics the ontology declares must never be confused "
        "with one another.",
    "metric_surface_absent_from_text":
        "The metric the binding declares is never named in the sentence, so the number is "
        "reported without saying what it measures.",
    "metric_named_in_text_contradicts_binding":
        "The metric the sentence names in words is a different metric from the one its binding "
        "declares — one word, such as 'adjusted', moves the claim to another measure.",

    # §13.6 / §13.11 subject and entity identity
    "foreign_subject_named":
        "The sentence makes a claim about a company or subject the evidence does not cover.",
    "unresolved_entity_named":
        "The sentence names an entity the graph could not resolve to a known party, so who it "
        "refers to is a guess.",

    # §13.7 citation support
    "citation_span_not_in_passage":
        "The cited character range falls outside the passage text the package actually holds.",
    "citation_quote_not_in_passage":
        "The quoted text does not occur in the passage it is cited from.",
    "table_quote_does_not_reconstruct":
        "The cited table cell does not reconstruct from the passage's row and column labels, so "
        "the quote does not locate the number.",
    "citation_does_not_support_fact":
        "The cited passage is not one the bound fact was read from, so it evidences something "
        "else.",
    "citation_reused_for_unrelated_claim":
        "The same citation was carried forward to decorate a second claim the passage does not "
        "evidence.",
    "row_label_not_licensed_for_metric":
        "The table row the citation points at is not a row that reports this metric.",
    "column_label_ambiguous_in_passage":
        "The table column cited appears more than once in the passage under the same label, so "
        "it does not identify one period.",
    "column_label_ambiguity_classified":
        "The ambiguous table column was resolved by document majority; the claim proceeds and "
        "the minority reading must be shown beside it.",
    "narrative_span_missing_number":
        "The cited sentence of prose does not itself contain the number the draft states.",
    "paraphrase_distance":
        "The sentence has drifted a long way from the wording it cites. A warning, not a "
        "refusal.",
    "counter_evidence_cited_as_support":
        "A passage collected as counter-evidence is cited as if it supported the claim.",
    "evidence_kind_not_supported_in_v1":
        "The citation points at a kind of evidence this version cannot check — no lane in this "
        "run produces one.",
    "uncited_factual_sentence":
        "A sentence states something a filing said and cites nothing, so there is no span to "
        "check it against.",

    # §13.8 events
    "event_property_bound_as_fact":
        "An event's free-text property is bound as if it were a measured value; it may only be "
        "quoted verbatim.",
    "date_not_in_package":
        "The date stated is not a date the evidence package carries — several events in this "
        "corpus have no recorded date at all.",
    "event_review_flag":
        "The event used is flagged for review — its announcement and occurrence dates were "
        "treated as the same — so the claim must be annotated.",

    # §13.9 reported versus calculated
    "calculated_sentence_cites_passage":
        "A calculated value cites a filing passage, which claims the filing stated a number it "
        "did not.",
    "calculated_sentence_without_calculation":
        "A sentence is labelled as calculated but declares no calculation to recompute.",
    "reported_sentence_carries_calculation":
        "A sentence presented as reported carries a calculation, so it is a derived figure "
        "dressed as a filed one.",
    "calculation_inputs_unresolved":
        "The calculation names input observations the evidence package does not hold.",
    "calculation_inputs_incomparable":
        "The calculation's inputs may not be compared with one another under the comparability "
        "rules — different definitions, units or period shapes.",
    "calculation_does_not_recompute":
        "Recomputing the calculation from its declared inputs does not give the number written, "
        "at the draft's own printed precision.",
    "calculation_operation_not_supported":
        "The calculation uses an operation the verifier has no rule to recompute.",
    "operation_not_recomputable":
        "The operation produces no single number to recompute, so the numeral it rendered was "
        "never checked.",
    "formula_version_not_valid_for_period":
        "The metric's formula version used is not the one in force for the period computed over.",

    # §13.10 causation
    "causal_construction_forbidden":
        "The sentence asserts that one thing caused another; causal claims the model originates "
        "are never permitted.",
    "causal_attribution_frame_missing":
        "The sentence states a cause without naming, in the sentence itself, the filing that "
        "attributed it.",
    "causal_marker_not_in_cited_span":
        "The cited span does not itself contain the causal wording the sentence relies on.",
    "causal_marker_negated_in_span":
        "The cited span negates the causal link the sentence asserts — the filing says it was "
        "*not* the reason.",
    "causal_marker_ambiguous_in_span":
        "The cited span carries more than one causal statement, so it does not establish which "
        "one the sentence means.",
    "causal_frame_document_mismatch":
        "The document named as the source of the attribution is not the document cited.",

    # §13.12 conflicting facts
    "conflict_not_disclosed":
        "Filings report more than one value for this figure and the post states one without "
        "disclosing the conflict.",
    "conflict_immaterial_at_stated_precision":
        "Filings report more than one value, but they round to the same thing at the precision "
        "written, so the conflict is annotated rather than disclosed.",

    # §13.14 sentences with no numeral
    "connective_sentence_carries_a_claim":
        "A linking sentence carries a claim of its own; it may only refer to what neighbouring "
        "sentences already established.",
    "unsupported_superlative":
        "A 'first', 'only' or 'largest' claim is made without the full set of values needed to "
        "establish it.",
    "unsupported_comparative":
        "A comparison between two things is made with no declared calculation over both sides.",
    "unsupported_absence_claim":
        "The post claims something was not reported, with nothing bound that could establish an "
        "absence.",
    "unsupported_temporal_ordering":
        "The post asserts that one thing happened before another where the evidence carries no "
        "dates to order them.",
    "extremum_recomputation_failed":
        "Recomputing the 'first', 'only' or 'largest' claim over its own declared inputs "
        "contradicts it.",
    "extremum_expression_not_supported":
        "The 'first', 'only' or 'largest' claim is expressed in a form the verifier cannot "
        "recompute, so it is refused rather than assumed true.",
    "comparative_recomputation_failed":
        "Recomputing both sides of the comparison contradicts the direction the sentence "
        "states.",
    "comparative_not_supported_by_text":
        "The calculation's two sides and the words of the sentence name them in opposite order — "
        "the same number, the opposite claim.",
    "unpopulated_metric":
        "The metric is declared in the ontology and carries no observations in this run, so it "
        "can support no claim.",
    "absence_not_provable_from_bounded_package":
        "The evidence package is a bounded slice of the graph, so it can show that *it* holds no "
        "such value — never that the corpus does not.",

    # §13.15 forward-looking language
    "forward_looking_language":
        "The sentence makes a statement about the future; every observation in this run is "
        "historical and nothing could support or contradict it.",

    # §11 / §13 disclosures the plan asked for
    "required_warning_absent":
        "A caveat the plan required the post to state is missing from the draft.",
    "required_warning_has_no_declared_qualifier":
        "A required caveat has no declared wording that would count as having said it, so "
        "silence could satisfy it.",
    "required_counterpoint_absent":
        "The plan required a counterpoint and no sentence in the draft carries one.",
}


# -- §10.1's package warnings, 30 codes ---------------------------------------------------------
#
# Derived from each code's `#:` comment in `story/stages/packaging/warning_codes.py`, which is
# where the measurement behind it is recorded. The comments are documentation; these are data.

PACKAGE_WARNING_DESCRIPTIONS: Mapping[str, str] = {
    "unpreferred_source_lane":
        "A fact used here was read from a lane the pipeline prefers less — the narrative text "
        "rather than a normalised table.",
    "metric_ambiguity_declared":
        "The ontology declares a known ambiguity in how this metric is defined, and it has to be "
        "carried beside any claim about it.",
    "entity_unresolved":
        "The package names a party that could not be resolved to a known entity.",
    "event_date_absent":
        "An event used here has no recorded date it occurred on.",
    "event_review_flag":
        "An event used here is flagged for review, so it may not be presented as settled "
        "evidence.",
    "population_definition_differs":
        "Two figures being compared count different populations, so the comparison is between "
        "two denominators.",
    "formula_window_boundary_crossed":
        "The periods compared sit either side of a change in the metric's formula, so two "
        "definitions are being compared.",
    "single_source":
        "This figure is corroborated by exactly one filing.",
    "concordant_readings_collapsed":
        "Several filings report this figure identically. The package carries one row for it and "
        "names the other readings as corroborating sources on that row, rather than repeating "
        "the same number as though it were several facts.",
    "fact_conflict_disclosed":
        "Filings report more than one value for this slot, and the classification of that "
        "conflict travels with it.",
    "slot_unresolved":
        "The figure exists in the graph and emits no usable value, because its readings could "
        "not be reconciled.",
    "canonical_point_warning":
        "The canonical value carried a warning of its own from the stage that resolved it — a "
        "lane defect, a minority reading, or disagreeing units.",
    "observation_load_incomplete":
        "The observations behind this package were not proved complete, so a figure may be "
        "missing rather than absent.",
    "retrieval_truncated":
        "A graph query hit its row limit, so what it returned is a page and not the whole "
        "answer.",
    "search_pool_capped":
        "A passage search ranked a capped pool before filtering, so a filtered query may have "
        "been starved of rows.",
    "search_pool_starved":
        "The capped search pool left a query too short to be trusted, so that section was "
        "dropped rather than shipped incomplete.",
    "counter_evidence_same_document":
        "A contradicting item was matched at the level of the whole filing, not the exact "
        "passage the number came from.",
    "counter_evidence_same_passage":
        "A contradicting item was found in a passage one of the used facts is itself cited "
        "from.",
    "counter_evidence_unavailable":
        "There was nowhere to look for contradicting evidence — distinct from having looked and "
        "found none.",
    "section_truncated":
        "A section of the package hit its size cap, and rows the builder had were not included.",
    "token_budget_trimmed":
        "The package exceeded its token budget and rows were dropped to fit.",
    "package_exceeds_token_ceiling":
        "Even after trimming, the package is too large for the model's context, so a draft built "
        "on it would be written from a truncated universe.",
    "subject_identity_not_read_from_graph":
        "The company's own name and labels were not read from the graph — no bounded tool reads "
        "that node — so they come from the observations instead.",
    "evidence_chain_incomplete":
        "A fact's chain back to a filed passage does not close, so the citation could not be "
        "checked; the fact is dropped.",
    "relationships_unavailable_in_v1":
        "The relationships section is empty because no bounded tool in this version returns "
        "one — a gap in the retrieval layer, not a fact about the company.",
    "evidence_sources_absent_in_v1":
        "The structured-source section is empty because this run contains no such sources at "
        "all.",
    "comparison_refused":
        "A comparison the story candidate made is refused by the comparability rules at the "
        "stricter standard packaging applies.",
    "comparison_warned":
        "A comparison the story candidate made is permitted and must be disclosed.",
    "candidate_warning":
        "The detector that found this story recorded a caveat about it, carried through so the "
        "planner sees what the detector already knew.",
    "required_fact_does_not_fit":
        "A fact the package may never drop — one of the story's anchor readings, or one of the "
        "ontology's definitions of what its numbers mean — does not fit in the model's context, "
        "so the package refuses rather than sending numbers with their meaning trimmed away.",
}


# -- §7's freshness gate, 8 codes ---------------------------------------------------------------

FRESHNESS_DESCRIPTIONS: Mapping[str, str] = {
    "graph_manifest_unreadable":
        "The graph run's own manifest is missing or unreadable, so there is nothing to check the "
        "loaded graph against.",
    "extraction_run_directory_missing":
        "The extraction run the graph was built from is not on this machine, so its inputs "
        "cannot be hashed.",
    "package_input_digest_mismatch":
        "The extraction inputs are not the bytes the graph was projected from — the run was "
        "regenerated or written into since.",
    "graph_unreachable":
        "The database did not answer, so the loaded graph could not be checked.",
    "load_incomplete":
        "The graph load never finished, or left no completion marker.",
    "graph_run_id_mismatch":
        "The graph currently loaded is a different run from the one being asked for.",
    "count_mismatch":
        "The node and observation counts in the database are not the counts the run recorded.",
    "ontology_hash_mismatch":
        "The ontology changed after this graph was loaded, so the vocabulary underneath the data "
        "moved.",
}


# -- §11's planner refusals, 11 codes -----------------------------------------------------------

PLANNER_DESCRIPTIONS: Mapping[str, str] = {
    "unresolvable_fact_id":
        "The plan names a fact the evidence package does not hold.",
    "unresolvable_passage_id":
        "The plan names a passage the evidence package does not hold.",
    "counterpoint_missing":
        "The package carries contradicting evidence and the plan raises no counterpoint against "
        "it.",
    "counterpoint_ungrounded":
        "The plan's counterpoint names none of the contradicting items, so it is grounded in "
        "nothing.",
    "counter_evidence_unaccounted":
        "A contradicting item is neither used by the plan nor listed as unusable, so it was "
        "silently ignored.",
    "unknown_warning_code":
        "The plan requires a caveat by a name the package does not carry.",
    "unknown_unusable_id":
        "The plan lists something as unusable evidence that is not an item of this package.",
    "causal_language_not_computed":
        "The plan claims a level of causal language the code does not compute from this "
        "package's cited text.",
    "thesis_empty":
        "The plan states no thesis.",
    "no_key_points":
        "The plan carries no key point.",
    "plan_not_constructible":
        "The model's answer satisfied its schema and still could not be built into a plan.",
}


# -- §12's writer refusals, 13 codes ------------------------------------------------------------
#
# Eleven until TABLE_CELL_CITATIONS S4, which moved the citation contract from a retyped quote to
# a deterministic evidence handle and added the two failures that contract has:
# `unresolvable_evidence_handle` and `evidence_handle_out_of_bounds`. The two quote codes are
# **kept and re-described**, not deleted: a table-backed fact no longer reaches either, but a
# narrative one still does, and 14 of the corpus's 2,704 observations are narrative. A code
# deleted while still reachable leaves a panel printing a bare string nobody wrote a sentence for.

WRITER_DESCRIPTIONS: Mapping[str, str] = {
    "unresolvable_fact_id":
        "The draft binds a fact the evidence package does not hold.",
    "unresolvable_passage_id":
        "The draft cites a passage the writer was never shown.",
    "binding_rendering_not_in_text":
        "The draft declares a number at a position in its own sentence that does not contain it.",
    "binding_rendering_ambiguous_in_sentence":
        "The number the draft binds occurs more than once in its own sentence, so there is no "
        "ground for choosing which occurrence is meant.",
    "unresolvable_evidence_handle":
        "The draft cites an evidence id this package minted for no fact, so it names a cell "
        "nothing was read from.",
    "evidence_handle_out_of_bounds":
        "The cited fact's own table coordinates fall outside the passage text the package "
        "carries, so the evidence id locates no cell.",
    "citation_quote_not_in_passage":
        "The passage no longer contains the package's own quote for a fact read out of prose, "
        "or the citation's range falls outside the text the package holds.",
    "citation_quote_ambiguous_in_passage":
        "The package's own quote for a fact read out of prose occurs more than once inside its "
        "passage, so the citation resolves to no single span.",
    "more_than_one_calculation":
        "A sentence declares more than one calculation; a sentence is allowed one derivation.",
    "thesis_abandoned":
        "The draft binds different facts from the ones the plan's key points rest on — the "
        "writer changed the thesis.",
    "no_sentences":
        "The draft carries no sentence.",
    "plan_names_another_package":
        "The plan and the package handed to the writer are about different candidates.",
    "draft_not_constructible":
        "The model's answer satisfied its schema and still could not be built into a draft.",
}


DESCRIPTIONS: Mapping[str, Mapping[str, str]] = {
    FAMILY_VERIFICATION: VERIFICATION_DESCRIPTIONS,
    FAMILY_PACKAGE_WARNING: PACKAGE_WARNING_DESCRIPTIONS,
    FAMILY_FRESHNESS: FRESHNESS_DESCRIPTIONS,
    FAMILY_PLANNER: PLANNER_DESCRIPTIONS,
    FAMILY_WRITER: WRITER_DESCRIPTIONS,
}


def declared_codes(family: str) -> frozenset[str]:
    """The codes the *declaring stage* holds for a family — the set totality is measured against.

    Read from the stage every time rather than cached in a literal, because a cached copy is the
    thing that goes stale. The planner's and the writer's are read off their modules' constants
    by name, which is the only place either declares them.
    """
    if family == FAMILY_VERIFICATION:
        return frozenset(GATE)
    if family == FAMILY_PACKAGE_WARNING:
        return frozenset(SEVERITY_OF)
    if family == FAMILY_FRESHNESS:
        return frozenset(code.value for code in RefusalCode)
    if family == FAMILY_PLANNER:
        return frozenset(getattr(planner, name) for name in _PLANNER_CODE_NAMES)
    if family == FAMILY_WRITER:
        return frozenset(getattr(writer, name) for name in _WRITER_CODE_NAMES)
    raise KeyError(f"{family!r} is not one of {', '.join(FAMILY_ORDER)}")


#: The constants §11 and §12 raise their refusals with. **Names, and the values are read off the
#: module** — neither module exports a set, and a copy of the strings here would be a second
#: vocabulary that could disagree with the one the stage actually raises. A renamed constant
#: fails at import; a *new* one is caught by the AST scan in
#: `tests/story/test_demo_ui_code_catalogue.py`, which walks every `PlanViolation(...)` and
#: `DraftViolation(...)` construction and requires this catalogue to be total over what it finds.
_PLANNER_CODE_NAMES: tuple[str, ...] = (
    "UNRESOLVABLE_FACT_ID", "UNRESOLVABLE_PASSAGE_ID", "COUNTERPOINT_MISSING",
    "COUNTERPOINT_UNGROUNDED", "COUNTER_EVIDENCE_UNACCOUNTED", "UNKNOWN_WARNING_CODE",
    "UNKNOWN_UNUSABLE_ID", "CAUSAL_LANGUAGE_NOT_COMPUTED", "THESIS_EMPTY", "NO_KEY_POINTS",
    "PLAN_NOT_CONSTRUCTIBLE",
)

_WRITER_CODE_NAMES: tuple[str, ...] = (
    "UNRESOLVABLE_FACT_ID", "UNRESOLVABLE_PASSAGE_ID", "BINDING_RENDERING_NOT_IN_TEXT",
    "BINDING_RENDERING_AMBIGUOUS", "UNRESOLVABLE_EVIDENCE_HANDLE",
    "EVIDENCE_HANDLE_OUT_OF_BOUNDS", "CITATION_QUOTE_NOT_IN_PASSAGE",
    "CITATION_QUOTE_AMBIGUOUS", "MORE_THAN_ONE_CALCULATION", "THESIS_ABANDONED", "NO_SENTENCES",
    "PLAN_NAMES_ANOTHER_PACKAGE", "DRAFT_NOT_CONSTRUCTIBLE",
)


def _explanation(family: str, code: str, description: str) -> CodeExplanation:
    """One row, with everything but the sentence read from the declaring stage's own table."""
    if family == FAMILY_VERIFICATION:
        entry = GATE[code]
        return CodeExplanation(
            code=code, family=family, description=description,
            severity=entry.severity.value, remedy=entry.remedy.value, section=entry.section,
            blocking=entry.blocking)
    if family == FAMILY_PACKAGE_WARNING:
        severity: Severity = SEVERITY_OF[code]
        kind: WarningKind = KIND_OF[code]
        category: WarningCategory = CATEGORY_OF[code]
        return CodeExplanation(
            code=code, family=family, description=description, severity=severity.value,
            section="10.1", warning_kind=kind.value, warning_category=category.value,
            blocking=severity is Severity.REFUSE)
    if family == FAMILY_FRESHNESS:
        # Every freshness code is a refusal by construction — `FreshnessReport.passed` is false
        # if any check carries one — so `blocking` is true and there is no severity table to
        # read. Stated here rather than defaulted, so a reader knows it was decided.
        return CodeExplanation(code=code, family=family, description=description,
                               severity="refuse", section="7", blocking=True)
    # §11 and §12 declare no severity and no remedy: a refusal there means no plan and no draft
    # exists, which is not a gradation. Leaving both empty is the honest rendering.
    section = "11" if family == FAMILY_PLANNER else "12"
    return CodeExplanation(code=code, family=family, description=description,
                           section=section, blocking=True)


#: Every explained code, keyed by `(family, code)` because six codes appear in two families.
CATALOGUE: Mapping[tuple[str, str], CodeExplanation] = {
    (family, code): _explanation(family, code, description)
    for family in FAMILY_ORDER
    for code, description in sorted(DESCRIPTIONS[family].items())
    if code in declared_codes(family)
}


def explain(code: str, family: str | None = None) -> CodeExplanation | None:
    """One code explained, or `None` when nothing declares it.

    `None` rather than a raise, and rather than a synthesised *"unknown code"* row: a UI that
    met a code this catalogue does not carry must show the bare code and say so, not print a
    sentence nobody wrote. The build-time totality test is what keeps that branch unreachable
    for every code the pipeline can actually raise.

    Without `family`, families are tried in `FAMILY_ORDER` and the answer names the one that
    matched — `citation_quote_not_in_passage` means two different things at §12 and §13.7, and a
    caller holding a `VerificationFinding` should pass `FAMILY_VERIFICATION` to be sure.
    """
    if family is not None:
        return CATALOGUE.get((family, code))
    for candidate_family in FAMILY_ORDER:
        found = CATALOGUE.get((candidate_family, code))
        if found is not None:
            return found
    return None


def families_of(code: str) -> tuple[str, ...]:
    """Every family that declares this code, in precedence order. Two, for the six shared ones."""
    return tuple(f for f in FAMILY_ORDER if (f, code) in CATALOGUE)


def for_family(family: str) -> tuple[CodeExplanation, ...]:
    """One family's rows, ordered by code so a rendered table is stable between runs."""
    return tuple(row for (name, _code), row in CATALOGUE.items() if name == family)


def catalogue_payload() -> dict[str, object]:
    """The whole catalogue as plain JSON, ready for an HTTP response.

    Grouped by family and ordered by code. No clock, no model text and no free-form field: a
    reader can diff two responses and see only what the tables changed.
    """
    return {
        "families": [
            {
                "family": family,
                "description": FAMILY_DESCRIPTIONS[family],
                "codes": [row.as_dict() for row in for_family(family)],
            }
            for family in FAMILY_ORDER
        ],
    }


__all__ = [
    "CATALOGUE",
    "FAMILY_DESCRIPTIONS",
    "FAMILY_FRESHNESS",
    "FAMILY_ORDER",
    "FAMILY_PACKAGE_WARNING",
    "FAMILY_PLANNER",
    "FAMILY_VERIFICATION",
    "FAMILY_WRITER",
    "CodeExplanation",
    "DESCRIPTIONS",
    "FRESHNESS_DESCRIPTIONS",
    "PACKAGE_WARNING_DESCRIPTIONS",
    "PLANNER_DESCRIPTIONS",
    "VERIFICATION_DESCRIPTIONS",
    "WRITER_DESCRIPTIONS",
    "catalogue_payload",
    "declared_codes",
    "explain",
    "families_of",
    "for_family",
]
