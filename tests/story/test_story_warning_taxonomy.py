"""S5 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS — the warning taxonomy, the trim priority, one refusal.

Three claims, and each is only worth asserting because its opposite is a plausible bug.

1. **The five categories refine `WarningKind` and do not replace it.** `KIND_OF` is derived from
   `CATEGORY_OF`, so the planner's `claim_qualifying_warnings` filter and §13's disclosure check
   see exactly what they saw before. That is asserted code by code below rather than in prose,
   because *"nothing moved"* is the whole safety argument for a reclassification and the failure
   mode is silent: a claim qualifier misfiled as provenance goes unsaid, in silence.
2. **Reclassifying is relabelling, never hiding.** The three capability limitations are still
   constructible, still carried, still ordered by severity, still described in the catalogue.
3. **A required fact that will not fit refuses.** The plan's one new blocking behaviour. It is
   raised through the same `REFUSE` mechanism `package_exceeds_token_ceiling` already uses, and
   it names the facts, because a rejection that does not say what to fix is a search.

`tests/story/test_story_evidence_package.py` owns the §10.2 caps and the §10.3 identity; this
file owns only what S5 added, so a failure here points at one stage.
"""

from __future__ import annotations

import pytest

from story.core.graph_identity import GraphIdentity
from story.core.models import (
    BudgetParameters,
    ComparabilityFact,
    EvidenceRole,
    IdentityFact,
    PackagedFact,
    PackagedPassage,
    PackagedSubject,
    PackagedWarning,
    SemanticFact,
    Severity,
    WarningCategory,
    WarningKind,
)
from story.stages.generation.planner import claim_qualifying_warnings
from story.stages.packaging import package_assembly as assembly
from story.stages.packaging import section_bounds
from story.stages.packaging import warning_codes as codes

from conftest import (  # type: ignore[import-not-found]
    EXTRACTION_RUN_ID,
    GRAPH_RUN_ID,
    ONTOLOGY_DEFINITION_HASH,
    RUN_COMPLETE_SHA256,
    make_candidate,
    make_package,
)

IDENTITY = GraphIdentity(
    graph_run_id=GRAPH_RUN_ID,
    graph_projection_version="1.2.0",
    extraction_run_id=EXTRACTION_RUN_ID,
    extraction_run_directory=f"data/extraction_runs/{EXTRACTION_RUN_ID}",
    run_complete_sha256=RUN_COMPLETE_SHA256,
    input_content_digest="40ea214c701c",
    ontology_id="real_estate_marketplace_v1",
    ontology_version="2.0.0",
    ontology_definition_hash=ONTOLOGY_DEFINITION_HASH,
    node_count=28836,
    edge_count=35600,
)

#: Every code's kind **as it was before S5**, written out rather than computed. A test that
#: derived this from the same table it is checking would assert nothing; the point is that a
#: category edit which moves a code across the claim-qualifying line fails here loudly.
KIND_BEFORE_S5 = {
    "unpreferred_source_lane": WarningKind.CLAIM_QUALIFYING,
    "metric_ambiguity_declared": WarningKind.CLAIM_QUALIFYING,
    "entity_unresolved": WarningKind.CLAIM_QUALIFYING,
    "event_date_absent": WarningKind.CLAIM_QUALIFYING,
    "event_review_flag": WarningKind.CLAIM_QUALIFYING,
    "population_definition_differs": WarningKind.CLAIM_QUALIFYING,
    "formula_window_boundary_crossed": WarningKind.CLAIM_QUALIFYING,
    "single_source": WarningKind.CLAIM_QUALIFYING,
    "fact_conflict_disclosed": WarningKind.CLAIM_QUALIFYING,
    "slot_unresolved": WarningKind.CLAIM_QUALIFYING,
    "canonical_point_warning": WarningKind.CLAIM_QUALIFYING,
    "counter_evidence_same_document": WarningKind.CLAIM_QUALIFYING,
    "counter_evidence_same_passage": WarningKind.CLAIM_QUALIFYING,
    "comparison_refused": WarningKind.CLAIM_QUALIFYING,
    "comparison_warned": WarningKind.CLAIM_QUALIFYING,
    "candidate_warning": WarningKind.CLAIM_QUALIFYING,
    "observation_load_incomplete": WarningKind.BUILD_PROVENANCE,
    "retrieval_truncated": WarningKind.BUILD_PROVENANCE,
    "search_pool_capped": WarningKind.BUILD_PROVENANCE,
    "search_pool_starved": WarningKind.BUILD_PROVENANCE,
    "counter_evidence_unavailable": WarningKind.BUILD_PROVENANCE,
    "section_truncated": WarningKind.BUILD_PROVENANCE,
    "token_budget_trimmed": WarningKind.BUILD_PROVENANCE,
    "package_exceeds_token_ceiling": WarningKind.BUILD_PROVENANCE,
    "subject_identity_not_read_from_graph": WarningKind.BUILD_PROVENANCE,
    "evidence_chain_incomplete": WarningKind.BUILD_PROVENANCE,
    "relationships_unavailable_in_v1": WarningKind.BUILD_PROVENANCE,
    "evidence_sources_absent_in_v1": WarningKind.BUILD_PROVENANCE,
}


# ---------------------------------------------------------------------------------------
# S5a — the taxonomy is total, and it is a refinement
# ---------------------------------------------------------------------------------------


def test_every_declared_code_has_a_category_and_no_category_covers_a_code_nobody_declares():
    """The same guard `KIND_OF` and `SEVERITY_OF` already carry: a code added without a
    decision must fail the build rather than acquire one by default."""
    assert set(codes.CATEGORY_OF) == set(codes.SEVERITY_OF)
    assert set(codes.CATEGORY_OF.values()) <= set(WarningCategory)


def test_every_category_states_the_audience_it_is_for_so_the_kind_can_be_derived():
    assert set(codes.KIND_OF_CATEGORY) == set(WarningCategory)


def test_the_categories_are_a_refinement_of_the_kind_and_not_a_second_opinion():
    """The design decision, executable. Two independent tables over one code set can disagree;
    a derived one cannot. If `KIND_OF` were declared separately, this test would be the only
    thing standing between a category edit and a silently changed disclosure obligation."""
    for code, category in codes.CATEGORY_OF.items():
        assert codes.KIND_OF[code] is codes.KIND_OF_CATEGORY[category]


@pytest.mark.parametrize("code,kind", sorted((c, k.value) for c, k in KIND_BEFORE_S5.items()))
def test_no_codes_disclosure_obligation_moved_when_the_categories_arrived(code, kind):
    """The safety argument for the whole reclassification, code by code.

    §13's disclosure check reads `plan.required_warnings`, which the planner narrows with
    `claim_qualifying_warnings`, which reads `PackagedWarning.kind`. So *"the check behaves
    exactly as it does now"* is exactly *"no code's kind moved"*, and that is what this asserts.
    """
    assert codes.KIND_OF[code].value == kind


def test_only_two_codes_were_added_after_s5_and_each_is_named_with_the_stage_that_added_it():
    """S5 added `required_fact_does_not_fit`; S3a added `concordant_readings_collapsed`.

    Written as an exhaustive difference rather than two membership checks, because the failure
    this guards is a code arriving with no decision recorded anywhere — and that failure is
    silent in every other test.
    """
    added = set(codes.SEVERITY_OF) - set(KIND_BEFORE_S5)
    assert added == {codes.REQUIRED_FACT_DOES_NOT_FIT, codes.CONCORDANT_READINGS_COLLAPSED}
    # S3a's code is a statement about a fact's standing, so it qualifies a claim — the mirror of
    # `single_source`, which is the code beside it in `CATEGORY_OF`.
    assert codes.CATEGORY_OF[codes.CONCORDANT_READINGS_COLLAPSED] is (
        codes.CATEGORY_OF[codes.SINGLE_SOURCE])
    assert codes.KIND_OF[codes.CONCORDANT_READINGS_COLLAPSED] is WarningKind.CLAIM_QUALIFYING


def test_a_claim_qualifying_code_still_demands_its_qualifier_in_prose():
    """The property §13 rests on, driven through the planner's own filter rather than asserted
    about it. A qualifier that stopped being required would be a sentence the post may now omit.
    """
    package = make_package(warnings=(
        codes.packaged_warning(codes.SINGLE_SOURCE, subject_ids=("obs:adjusted-ebitda:b",)),
        codes.packaged_warning(codes.METRIC_AMBIGUITY_DECLARED, subject_ids=("adjusted_ebitda",)),
        codes.packaged_warning(codes.SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH),
        codes.packaged_warning(codes.RELATIONSHIPS_UNAVAILABLE_IN_V1),
        codes.packaged_warning(codes.EVIDENCE_SOURCES_ABSENT_IN_V1),
    ))
    named = [warning.code for warning in package.warnings]

    assert claim_qualifying_warnings(named, package) == (
        "single_source", "metric_ambiguity_declared")


def test_a_capability_limitation_is_relabelled_and_not_removed_from_the_package():
    """*"Reclassifying a warning must not make it disappear."* Every one of the three is still
    constructible, still carried on the package, still severity-ordered, and still the same
    severity it was — only its category is new."""
    warnings = tuple(codes.packaged_warning(code) for code in (
        codes.SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH,
        codes.EVIDENCE_SOURCES_ABSENT_IN_V1,
        codes.RELATIONSHIPS_UNAVAILABLE_IN_V1,
    ))
    package = make_package(warnings=warnings)

    assert {w.code for w in package.warnings} == {
        "subject_identity_not_read_from_graph", "evidence_sources_absent_in_v1",
        "relationships_unavailable_in_v1"}
    assert codes.capability_limitations(package.warnings) == warnings
    assert [w.severity for w in warnings] == [
        Severity.ANNOTATE, Severity.ADVISORY, Severity.ADVISORY]
    assert sorted(warnings, key=codes.warning_sort_key)[0].code == (
        "subject_identity_not_read_from_graph")


def test_the_three_reclassified_codes_are_capability_limitations_and_reduce_nothing():
    """Each fires on **every** package — `:Entity` is off the query allowlist, no §9 tool
    returns a relationship, and the corpus holds zero `:EvidenceSource` nodes — so none of them
    is a fact about this evidence and none may reduce a post's verification status. The
    mechanical guarantee is that a `BUILD_PROVENANCE` code cannot become a `required_warning`
    and that none of the three is `REFUSE`."""
    for code in (codes.SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH,
                 codes.EVIDENCE_SOURCES_ABSENT_IN_V1,
                 codes.RELATIONSHIPS_UNAVAILABLE_IN_V1):
        assert codes.CATEGORY_OF[code] is WarningCategory.CAPABILITY_LIMITATION
        assert codes.KIND_OF[code] is WarningKind.BUILD_PROVENANCE
        assert codes.SEVERITY_OF[code] is not Severity.REFUSE
        assert codes.packaged_warning(code) not in codes.blocking(
            [codes.packaged_warning(code)])


def test_relationships_unavailable_does_not_reduce_the_verification_status_of_a_post():
    """The plan states it in those words. `blocking()` is what §13.17's gate acts on and
    `claim_qualifying` is what a post must say; the code is in neither, and is still in the
    package."""
    warning = codes.packaged_warning(codes.RELATIONSHIPS_UNAVAILABLE_IN_V1)
    package = make_package(warnings=(warning,))

    assert codes.blocking(package.warnings) == ()
    assert codes.claim_qualifying(package.warnings) == ()
    assert package.warnings == (warning,)


def test_the_category_is_a_lookup_and_not_a_field_on_every_row():
    """The one place the taxonomy costs tokens, and the reason it does not.

    `kind` is on the row because the *planner* reads it and a stage may not import another
    stage. Nothing has that relationship to the category: the catalogue and the panel can both
    import `CATEGORY_OF`. A twenty-row `warnings[]` carrying it measured **170 prompt tokens**
    — a third of a passage at §10.2.1's 536-token median, inside the slice the model reads — so
    the field was removed and the lookup kept.
    """
    warning = codes.packaged_warning(codes.SEARCH_POOL_CAPPED, subject_ids=("terms",))

    assert "category" not in warning.model_dump()
    assert codes.category_of(warning) is WarningCategory.RETRIEVAL_WARNING
    assert warning.kind is WarningKind.BUILD_PROVENANCE
    rebuilt = PackagedWarning.model_validate_json(warning.model_dump_json())
    assert codes.category_of(rebuilt) is WarningCategory.RETRIEVAL_WARNING


def test_a_warning_from_an_older_artifact_renders_as_what_it_used_to_mean():
    """A stored package can carry a code this table never declared — a renamed one, or one from
    a version before S5. Rendering it as nothing would leave a panel with a blank column, so the
    fallback is the kind's own category, which is exactly what the code meant before."""
    stale = PackagedWarning(code="a_code_from_before", severity=Severity.ANNOTATE)
    provenance = PackagedWarning(code="a_provenance_code_from_before",
                                 severity=Severity.ADVISORY, kind=WarningKind.BUILD_PROVENANCE)

    assert codes.category_of(stale) is WarningCategory.FACT_QUALITY_WARNING
    assert codes.category_of(provenance) is WarningCategory.RETRIEVAL_WARNING


def test_the_truncation_disclosures_all_still_fire_at_the_severity_they_fired_at():
    """*"More disclosure, never less."* The five codes the plan names are all still declared,
    still constructible and still at their original severity — a category that quietly demoted
    `retrieval_truncated` to an advisory would weaken exactly what §4 S5 forbids weakening."""
    assert codes.SEVERITY_OF[codes.RETRIEVAL_TRUNCATED] is Severity.WARN
    assert codes.SEVERITY_OF[codes.SEARCH_POOL_CAPPED] is Severity.WARN
    assert codes.SEVERITY_OF[codes.SEARCH_POOL_STARVED] is Severity.WARN
    assert codes.SEVERITY_OF[codes.SECTION_TRUNCATED] is Severity.ANNOTATE
    assert codes.SEVERITY_OF[codes.TOKEN_BUDGET_TRIMMED] is Severity.WARN
    for code in (codes.RETRIEVAL_TRUNCATED, codes.SEARCH_POOL_CAPPED,
                 codes.SEARCH_POOL_STARVED, codes.SECTION_TRUNCATED,
                 codes.TOKEN_BUDGET_TRIMMED):
        assert codes.CATEGORY_OF[code] is WarningCategory.RETRIEVAL_WARNING


# ---------------------------------------------------------------------------------------
# S5b — what is protected, what goes, and in which order
# ---------------------------------------------------------------------------------------


TABLE_TEXT = "| Metric | 2022Q3 | 2022Q2 |\n| GAAP gross margin | (12.6) | 11.6 |"


def ontology_facts(sections: assembly.PackageSections) -> None:
    """The three ontology-fact sections S4 will fill, filled by hand so S5's protection of them
    is testable before S4 lands."""
    sections.semantic_facts.append(SemanticFact(
        fact_id="sem:gaap_gross_margin:definition", metric_id="gaap_gross_margin",
        attribute="definition",
        statement="GAAP gross margin is revenue less cost of revenue, over revenue.",
        value="", authoritative=True, editable=False,
        source="ontology:real_estate_marketplace_v1@2.0.0"))
    sections.identity_facts.append(IdentityFact(
        fact_id="ident:opendoor:legal_name", entity_id="opendoor", attribute="legal_name",
        statement="The subject is Opendoor Technologies Inc.", value="Opendoor Technologies Inc.",
        authoritative=False, editable=False))
    sections.comparability_facts.append(ComparabilityFact(
        fact_id="cmp:margin-vs-margin", rule_id="rule:distinct_metrics",
        metric_ids=("adjusted_gross_margin", "gaap_gross_margin"),
        statement="Adjusted and GAAP gross margin are distinct metrics and may not be compared.",
        authoritative=True, editable=False))


def sections_with(*, passage_text: str = TABLE_TEXT) -> assembly.PackageSections:
    """Two facts on two slots, two primaries, a diagnostic, an explanatory row — and the three
    ontology sections. The 2022Q3 slot is the one the candidate anchored on."""
    sections = assembly.PackageSections()
    sections.subject = PackagedSubject(entity_id="opendoor", entity_text="opendoor",
                                       resolved=True)
    for observation_id, period_key, value, passage_id in (
            ("obs:ggm:2022Q3:a", "2022Q3", -12.6, "doc:q3#p1"),
            ("obs:ggm:2022Q2:a", "2022Q2", 11.6, "doc:q2#p1")):
        sections.facts.append(PackagedFact(
            observation_id=observation_id, metric_id="gaap_gross_margin",
            metric_label="GAAP Gross Margin", period_key=period_key, shape="quarter",
            value=value, unit="percent", source_lane="normalized_table",
            validation_state="clean", passage_id=passage_id,
            document_id=passage_id.rsplit("#p", 1)[0], quoted_text=str(value)))
        sections.primary_passages.append(PackagedPassage(
            passage_id=passage_id, document_id=passage_id.rsplit("#p", 1)[0],
            text=passage_text, char_count=len(passage_text), char_end=len(passage_text),
            role=EvidenceRole.PRIMARY_SUPPORT))
    sections.explanatory_passages.append(PackagedPassage(
        passage_id="doc:q3#p9", document_id="doc:q3", text="Current housing environment. " * 40,
        char_count=1120, char_end=1120, role=EvidenceRole.CONTEXT))
    sections.diagnostic_passages.append(PackagedPassage(
        passage_id="doc:q3#p8", document_id="doc:q3", text="Inventory discussion. " * 40,
        char_count=840, char_end=840, role=EvidenceRole.WARNING_ONLY))
    ontology_facts(sections)
    sections.required_slots = {("gaap_gross_margin", "2022Q3")}
    return sections


def finalize(sections: assembly.PackageSections, **budget: object):
    return assembly.PackageAssembler(
        identity=IDENTITY, budget=BudgetParameters(**budget)).finalize(
            sections, make_candidate())


def test_the_protected_set_is_the_one_the_plan_names_and_no_step_can_reach_it():
    """A protected section that appeared in the trim plan would be protected in the docstring
    and trimmable in the code."""
    assert set(section_bounds.PROTECTED_SECTIONS) == {
        "facts", "semantic_facts", "identity_facts", "comparability_facts"}
    assert not set(section_bounds.PROTECTED_SECTIONS) & {
        step.section for step in section_bounds.TRIM_PLAN}


def test_the_trim_plan_takes_the_cheapest_row_class_first_and_the_disputing_one_last():
    """§4 S5's order, as steps rather than as sections: the corroborating rows inside
    `primary_passages` go before the supporting ones, and the demoted diagnostics go before
    either."""
    assert [step.name for step in section_bounds.TRIM_PLAN] == [
        "explanatory_passages", "context_passages", "corroborating_passages",
        "diagnostic_passages", "primary_passages", "counter_evidence"]
    corroborating = next(s for s in section_bounds.TRIM_PLAN
                         if s.name == "corroborating_passages")
    assert corroborating.roles == (EvidenceRole.CORROBORATING_SUPPORT,)
    assert corroborating.section == "primary_passages"


def test_a_budget_that_forces_trimming_takes_the_explanatory_row_and_never_the_ontology():
    """The claim in one run: with a budget of one token every trimmable row goes, and the
    metric's definition, the subject's identity and the comparison rule are all still there."""
    package = finalize(sections_with(), max_total_tokens=1)

    assert package.explanatory_passages == ()
    assert {"explanatory_passages", "diagnostic_passages"} <= set(package.budget.caps_hit)
    assert len(package.semantic_facts) == 1
    assert len(package.identity_facts) == 1
    assert len(package.comparability_facts) == 1
    assert codes.TOKEN_BUDGET_TRIMMED in {w.code for w in package.warnings}


def test_a_trim_never_strands_the_slot_the_candidate_anchored_on():
    package = finalize(sections_with(), max_total_tokens=1)

    assert ("gaap_gross_margin", "2022Q3") in {
        (fact.metric_id, fact.period_key) for fact in package.facts}


def test_a_corroborating_passage_goes_before_the_supporting_one_it_sits_beside():
    """S3's rows, ordered by S5 before S3 has written them. The corroborating row is dropped
    while both supporting passages are still in the package — which is the whole point of the
    step existing ahead of `primary_passages`."""
    sections = sections_with()
    sections.explanatory_passages.clear()
    sections.diagnostic_passages.clear()
    sections.primary_passages.append(PackagedPassage(
        passage_id="doc:q3#p2", document_id="doc:q3", text="A second reading. " * 60,
        char_count=1020, char_end=1020, role=EvidenceRole.CORROBORATING_SUPPORT))

    # 1,400 tokens is above the two supporting passages (1,261 measured) and below all three
    # (1,508), so exactly one row has to go and the test is about *which*.
    package = finalize(sections, max_total_tokens=1400)

    kept = {passage.passage_id for passage in package.primary_passages}
    assert "doc:q3#p2" not in kept
    assert kept == {"doc:q3#p1", "doc:q2#p1"}
    assert codes.TOKEN_BUDGET_TRIMMED in {w.code for w in package.warnings}


def test_a_required_fact_that_will_not_fit_is_a_refusal_and_it_names_the_fact():
    """§4 S5's one new blocking behaviour. The anchor's own passage is 40,000 characters, so no
    package carrying the 2022Q3 reading fits under the ceiling — and a package that trimmed the
    ontology instead would be a post whose numbers have no declared meaning.

    Raised through the existing `REFUSE` mechanism rather than a second one: `blocking()`
    collects it and §13.17's gate stops the draft with no new plumbing."""
    package = finalize(sections_with(passage_text="Z" * 40_000))

    refusal = next(w for w in package.warnings
                   if w.code == codes.REQUIRED_FACT_DOES_NOT_FIT)
    assert refusal in codes.blocking(package.warnings)
    assert refusal.severity is Severity.REFUSE
    assert "obs:ggm:2022Q3:a" in refusal.subject_ids
    assert "sem:gaap_gross_margin:definition" in refusal.subject_ids
    assert "sem:gaap_gross_margin:definition" in refusal.detail
    # The older, weaker statement is still made: this one says *which* facts, that one says the
    # package is too large at all.
    assert codes.PACKAGE_EXCEEDS_TOKEN_CEILING in {w.code for w in package.warnings}


def test_a_package_with_nothing_required_in_it_reports_the_ceiling_and_names_no_fact():
    """The refusal is about required content, so a package that has none must not claim one was
    stranded. Without this the code would fire on every oversized package and name nothing."""
    sections = sections_with(passage_text="Z" * 40_000)
    sections.required_slots = set()
    sections.semantic_facts.clear()
    sections.identity_facts.clear()
    sections.comparability_facts.clear()

    package = finalize(sections)

    assert codes.PACKAGE_EXCEEDS_TOKEN_CEILING in {w.code for w in package.warnings}
    assert codes.REQUIRED_FACT_DOES_NOT_FIT not in {w.code for w in package.warnings}


def test_a_refusal_is_not_raised_for_a_package_that_simply_trimmed_and_fits():
    """The refusal must be reachable only from the irreducible state. A package that trimmed
    successfully carries `token_budget_trimmed` and nothing blocking."""
    package = finalize(sections_with(), max_total_tokens=1)

    assert codes.REQUIRED_FACT_DOES_NOT_FIT not in {w.code for w in package.warnings}
    assert codes.blocking(package.warnings) == ()


# ---------------------------------------------------------------------------------------
# S5b — the ledger the panel renders
# ---------------------------------------------------------------------------------------


def test_every_section_records_available_carried_dropped_and_a_reason():
    package = finalize(sections_with(), max_total_tokens=1)
    ledger = {row.section: row for row in package.budget.section_ledger}

    assert set(ledger) == set(package.budget.section_counts)
    for name, row in ledger.items():
        assert row.carried == package.budget.section_counts[name]
        assert row.dropped >= 0
        assert row.available is None or row.available >= row.carried
    explanatory = ledger["explanatory_passages"]
    assert (explanatory.available, explanatory.carried, explanatory.dropped) == (1, 0, 1)
    assert explanatory.reasons == (codes.TOKEN_BUDGET_TRIMMED,)
    assert ledger["semantic_facts"].reasons == ()


def test_the_ledger_says_which_sections_were_untouchable_and_that_none_lost_a_required_fact():
    """*"…and that no required fact was dropped"* — measured from the surviving facts rather
    than asserted, so the panel's sentence is a reading of the package."""
    package = finalize(sections_with(), max_total_tokens=1)
    ledger = {row.section: row for row in package.budget.section_ledger}

    assert {name for name, row in ledger.items() if row.protected} == set(
        section_bounds.PROTECTED_SECTIONS)
    assert not any(row.required_dropped for row in package.budget.section_ledger)
    assert ledger["facts"].dropped == 0
    assert ledger["semantic_facts"].dropped == 0


def test_a_section_an_earlier_cap_bound_reports_an_unknown_available_and_never_a_flattering_one():
    """*"4 of 4 available"* about a section that had nine is a false statement. The selection
    stage is the only code that knows the number; until it reports one through `note_available`
    the ledger says it does not know, and names the warning that carries the real counts."""
    sections = sections_with()
    sections.caps_hit.add("counter_evidence")

    package = finalize(sections)
    row = next(r for r in package.budget.section_ledger if r.section == "counter_evidence")

    assert row.available is None
    assert row.reasons == (codes.SECTION_TRUNCATED,)


def test_a_stage_that_reports_what_it_had_makes_the_ledger_exact():
    sections = sections_with()
    sections.caps_hit.add("primary_passages")
    assembly.note_available(sections, "primary_passages", 9)

    package = finalize(sections)
    row = next(r for r in package.budget.section_ledger if r.section == "primary_passages")

    assert (row.available, row.carried) == (9, 2)
    assert row.reasons == (codes.SECTION_TRUNCATED,)
