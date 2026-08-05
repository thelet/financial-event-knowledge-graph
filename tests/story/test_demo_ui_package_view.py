"""S6 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS — one canonical mapper, and what it refuses to guess.

Four claims, and each names the failure it is the opposite of.

1. **One evidence item carries one role on every surface.** The plan forbids per-serialiser
   patching because that is how the inconsistency arose: `GET /demo/runs/{id}/sources` wrote a
   `role` of its own from the §10 section a passage sat in, while `POST /demo/evidence-package`
   returned `PackagedPassage.role`. Two vocabularies under one field name.
2. **`role: None` is unreachable**, and the mapper raises rather than defaulting. Every default
   it could take is a claim about evidence (S1's argument for making the field required).
3. **An unavailable fact is a rendered row, not an absent one.** There is no company description
   anywhere in the corpus, and a panel that dropped the row would leave a reader unable to tell
   *"nobody asked"* from *"the corpus has nothing"*, and would leave the gap for a model to fill.
4. **`available: null` is *unknown*, never *equal to carried*.** S5 put the `None` there because
   *"4 of 4 available"* about a section that had nine is a false statement.

The cross-surface identity of the *live* D4 package is exercised in
`tests/story/test_demo_ui_api.py`; what is here runs offline against hand-built packages, so a
failure names the mapper rather than the graph.
"""

from __future__ import annotations

import pytest

from story.core.models import (
    ComparabilityFact,
    EvidenceRole,
    IdentityFact,
    PackageBudget,
    PackagedPassage,
    PackagedWarning,
    SectionLedgerEntry,
    SemanticFact,
    Severity,
    WarningCategory,
)
from story.demo_ui import package_view
from story.stages.packaging import section_bounds
from story.stages.packaging import warning_codes as codes

from conftest import make_package  # type: ignore[import-not-found]


def display(value: float) -> str:
    return f"{value:.10g}"


def passage(passage_id: str, role: EvidenceRole, **overrides) -> PackagedPassage:
    fields = dict(passage_id=passage_id, document_id="doc:1", text="A table row.",
                  char_count=12, char_end=12, role=role)
    fields.update(overrides)
    return PackagedPassage(**fields)


def ontology_package(**overrides):
    """A package carrying all three ontology sections, including the unavailable description."""
    fields = dict(
        semantic_facts=(
            SemanticFact(
                fact_id="sem:adjusted_ebitda:definition", metric_id="adjusted_ebitda",
                attribute="definition",
                statement="Adjusted EBITDA means net loss before interest, taxes and more.",
                authoritative=True, editable=False,
                source="ontology:real_estate_marketplace_v1@2.0.0"),),
        identity_facts=(
            IdentityFact(
                fact_id="ident:opendoor:legal_name", entity_id="opendoor",
                attribute="legal_name",
                statement="The subject of this post is Opendoor Technologies Inc.",
                value="Opendoor Technologies Inc.", authoritative=False, editable=False),
            IdentityFact(
                fact_id="ident:opendoor:description", entity_id="opendoor",
                attribute="description",
                statement=("No description of this company exists in the corpus or the "
                           "ontology, so none is given. Do not supply one."),
                value="", available=False, authoritative=False, editable=False),
        ),
        comparability_facts=(
            ComparabilityFact(
                fact_id="cmp:distinct", rule_id="rule:distinct_metrics",
                metric_ids=("adjusted_gross_margin", "gaap_gross_margin"),
                statement="The ontology declares these two metrics mutually distinct.",
                authoritative=True, editable=False),),
    )
    fields.update(overrides)
    return make_package(**fields)


# ---------------------------------------------------------------------------------------
# The role mapper.
# ---------------------------------------------------------------------------------------


def test_every_role_in_the_vocabulary_has_a_distinct_label_and_a_reason():
    """§4 S6 names five labels in one line and `warning_only` is the sixth S2 created. Two roles
    sharing a label would put a diagnostic and a counterpoint under one word, which is §1."""
    assert set(package_view.ROLE_LABEL) == set(EvidenceRole)
    assert set(package_view.ROLE_DESCRIPTION) == set(EvidenceRole)
    labels = list(package_view.ROLE_LABEL.values())
    assert len(set(labels)) == len(labels), f"two roles share a label: {labels}"
    assert {"primary support", "corroborating support", "context", "counter-evidence",
            "unusable"} <= set(labels)
    for role, description in package_view.ROLE_DESCRIPTION.items():
        assert len(description) > 40, role


def test_a_passage_with_no_role_is_refused_rather_than_given_one():
    """Claim 2. `PackagedPassage.role` is required, so this can only be reached by handing the
    mapper something that is not one — which is exactly when a default would be invented."""

    class Roleless:
        passage_id = "psg:1"
        role = None

    with pytest.raises(package_view.UnroledPassage):
        package_view.role_of(Roleless())
    with pytest.raises(package_view.UnroledPassage):
        package_view.role_block(Roleless())


def test_the_role_on_the_wire_is_the_packages_own_and_never_the_section_it_sits_in():
    """The defect in one assertion. A `diagnostic_passages` row that is `primary_support` must
    serialise as `primary_support` **and** say it came out of `diagnostic_passages`."""
    row = package_view.passage_payload(
        passage("psg:9", EvidenceRole.PRIMARY_SUPPORT, diagnostic_codes=("AMBIGUOUS_ALIAS",)),
        section="diagnostic_passages")

    assert row["role"] == "primary_support"
    assert row["role_label"] == "primary support"
    assert row["is_counter_evidence"] is False
    assert row["is_support"] is True
    assert row["section"] == "diagnostic_passages"
    assert row["diagnostic_codes"] == ["AMBIGUOUS_ALIAS"]


def test_a_counter_evidence_row_says_so_as_a_boolean_and_not_only_as_a_string():
    """A renderer comparing strings can misspell one; a boolean it must read cannot be missed,
    and this is the one distinction §1 says a panel must never get wrong."""
    row = package_view.passage_payload(
        passage("psg:3", EvidenceRole.COUNTER_EVIDENCE, match_basis="scope_undermines_comparison",
                diagnostic_codes=("MISSING_PERIOD",)),
        section="counter_evidence")

    assert row["is_counter_evidence"] is True
    assert row["is_support"] is False
    assert row["match_basis"] == "scope_undermines_comparison"


def test_a_section_the_package_does_not_have_is_a_refusal():
    with pytest.raises(KeyError):
        package_view.passage_payload(passage("psg:1", EvidenceRole.CONTEXT),
                                     section="counterpoints")


def test_one_passage_in_two_sections_produces_two_rows_and_each_names_the_other():
    """**Measured live on 2026-08-05**, on three candidates including
    `cand:cross-metric-divergence:adjusted-gross-profit-contribution-profit:opendoor:2021Q4`:
    `q42021formxex992sharehol.htm#p10` is `primary_support` in `primary_passages` and
    `counter_evidence` in `counter_evidence[]`, with `match_basis=scope_undermines_comparison`.

    That is §1.1's own sentence, except that S2 has since made the second half earn it. It
    matters here because the sources payload used to key a dictionary on `passage_id` across all
    five sections in order — so `counter_evidence` overwrote `primary_passages` and the panel
    showed a supporting passage as a counterpoint and *never* as support. Silently picking one
    of two true answers is the failure this test exists for.
    """
    both = passage("psg:shared", EvidenceRole.PRIMARY_SUPPORT)
    disputing = passage("psg:shared", EvidenceRole.COUNTER_EVIDENCE,
                        match_basis="scope_undermines_comparison")
    package = make_package(primary_passages=(both,), counter_evidence=(disputing,))

    rows = package_view.passage_rows(package)
    assert [(row["section"], row["role"]) for row in rows] == [
        ("primary_passages", "primary_support"),
        ("counter_evidence", "counter_evidence")]
    assert rows[0]["also_in"] == ["counter_evidence"]
    assert rows[1]["also_in"] == ["primary_passages"]
    assert rows[0]["row_id"] != rows[1]["row_id"]
    assert package_view.sections_by_passage(package)["psg:shared"] == (
        "primary_passages", "counter_evidence")


def test_the_role_counts_report_every_role_including_the_zeros():
    package = make_package(counter_evidence=(passage("psg:2", EvidenceRole.COUNTER_EVIDENCE),))
    counted = package_view.role_counts(package)

    assert set(counted) == {role.value for role in EvidenceRole}
    assert counted["counter_evidence"] == 1
    assert counted["unusable"] == 0


# ---------------------------------------------------------------------------------------
# The facts sent to the model.
# ---------------------------------------------------------------------------------------


def test_the_five_groups_are_the_plans_own_division_and_each_names_its_section():
    assert [group for group, _label, _section, _kind in package_view.FACT_GROUPS] == [
        "observed_facts", "derived_facts", "semantic_facts", "company_context",
        "comparison_rules"]
    for group, _label, section, _kind in package_view.FACT_GROUPS:
        assert group in package_view.GROUP_DESCRIPTION
        assert hasattr(make_package(), section)


def test_every_fact_row_says_kind_statement_source_authority_editability_and_slice():
    """§4 S6's own list of what a row shows, checked field by field rather than in prose."""
    facts = package_view.model_facts(ontology_package(), display=display)
    rows = [row for group in facts["groups"] for row in group["rows"]]
    assert rows

    for row in rows:
        for field in ("fact_kind", "statement", "source", "authority", "editable",
                      "warning_codes", "in_model_slice", "available"):
            assert field in row, f"{row.get('fact_id')} carries no {field}"
        assert row["statement"], row["fact_id"]
        assert row["in_model_slice"] is True


def test_the_ontology_rows_are_authoritative_and_read_only_and_say_why():
    """*"Semantic and identity facts render read-only and authoritative. Ontology facts are not
    reachable from the prompt UI."* The reason travels with the flag, so a panel says which."""
    facts = package_view.model_facts(ontology_package(), display=display)
    groups = {group["group"]: group for group in facts["groups"]}

    semantic = groups["semantic_facts"]["rows"][0]
    assert semantic["authoritative"] is True
    assert semantic["editable"] is False
    assert "ontology" in semantic["not_editable_because"]
    assert semantic["statement_source"] == "the ontology's own words"

    rule = groups["comparison_rules"]["rows"][0]
    assert rule["authoritative"] is True
    assert rule["editable"] is False

    observed = groups["observed_facts"]["rows"][0]
    assert observed["editable"] is False, "a filed reading is not editable from a prompt panel"
    assert observed["statement_source"] == "rendered from this row's own fields"


def test_the_absent_company_description_renders_as_unavailable_and_is_never_filled_in():
    """Claim 3, and the plan states it twice. No company description exists anywhere — not on
    the ontology instance, not on any `:Entity` node, and no `:Entity` node in the graph has a
    `description` property key at all (S4, measured live). The row is carried with
    `available: false` and an empty value, and the panel must render it as unavailable rather
    than as empty or absent."""
    facts = package_view.model_facts(ontology_package(), display=display)
    context = next(group for group in facts["groups"] if group["group"] == "company_context")

    assert context["count"] == 2
    assert context["unavailable"] == 1
    row = next(row for row in context["rows"] if row["attribute"] == "description")
    assert row["available"] is False
    assert row["value"] == ""
    assert "none is given" in row["statement"]
    # The row is present, so a renderer cannot mistake it for a question nobody asked.
    assert row["fact_id"] == "ident:opendoor:description"
    assert [r["available"] for r in context["rows"]] == [True, False]


def test_the_slice_note_is_read_from_the_packaging_stages_own_exclusion_list():
    """A panel that hard-coded *"everything here reaches the model"* would keep saying it after
    `PROMPT_EXCLUDED_SECTIONS` moved."""
    facts = package_view.model_facts(ontology_package(), display=display)

    assert facts["prompt_excluded_sections"] == list(section_bounds.PROMPT_EXCLUDED_SECTIONS)
    for section in section_bounds.PROMPT_EXCLUDED_SECTIONS:
        assert package_view.in_model_slice(section) is False
    assert package_view.in_model_slice("semantic_facts") is True


# ---------------------------------------------------------------------------------------
# The ledger, and the count nobody took.
# ---------------------------------------------------------------------------------------


def test_a_section_whose_available_count_was_never_taken_renders_as_unknown():
    """Claim 4. *"4 of 4 available"* about a section that had nine is a false statement, so the
    mapper reports `available_known: false` and leaves the number `null` — it does not fill it
    with `carried`, and `complete` is `null` rather than `true`."""
    package = make_package(budget=PackageBudget(
        artifact_token_estimate=100, prompt_token_estimate=90,
        section_counts={"counter_evidence": 4},
        section_ledger=(SectionLedgerEntry(
            section="counter_evidence", available=None, carried=4, dropped=0,
            reasons=(codes.SECTION_TRUNCATED,)),)))

    row = package_view.section_summary(package, "counter_evidence")

    assert row["available"] is None
    assert row["available_known"] is False
    assert row["carried"] == 4
    assert row["complete"] is None
    assert row["reasons"] == [codes.SECTION_TRUNCATED]


def test_a_section_that_reported_what_it_had_renders_the_real_pair():
    """The other half: *"4 of 9 primary passages sent to the model"* is a reading of the ledger
    and not a sentence the panel composes out of two unrelated counts."""
    package = make_package(budget=PackageBudget(
        artifact_token_estimate=100, prompt_token_estimate=90,
        section_counts={"primary_passages": 4},
        section_ledger=(SectionLedgerEntry(
            section="primary_passages", available=9, carried=4, dropped=5,
            reasons=(codes.SECTION_TRUNCATED,)),)))

    row = package_view.section_summary(package, "primary_passages")

    assert (row["available"], row["carried"], row["dropped"]) == (9, 4, 5)
    assert row["available_known"] is True
    assert row["complete"] is False


def test_a_group_carries_the_ledger_row_of_the_section_it_reads():
    package = ontology_package(budget=PackageBudget(
        artifact_token_estimate=100, prompt_token_estimate=90,
        section_counts={"semantic_facts": 1},
        section_ledger=(SectionLedgerEntry(section="semantic_facts", available=1, carried=1,
                                           dropped=0, protected=True),)))
    facts = package_view.model_facts(package, display=display)
    group = next(g for g in facts["groups"] if g["group"] == "semantic_facts")

    assert group["ledger"]["protected"] is True
    assert group["ledger"]["required_dropped"] is False


# ---------------------------------------------------------------------------------------
# Warnings, as a panel has to read them.
# ---------------------------------------------------------------------------------------


def test_every_category_has_a_label_so_a_limitation_never_reads_as_a_data_defect():
    assert set(package_view.CATEGORY_LABEL) == {c.value for c in WarningCategory}


def test_a_capability_limitation_is_grouped_as_one_and_reduces_nothing():
    """§4 S5 and §4 S6 both say it: these must render as limits of V1 and must not reduce the
    post's verification status. None is `REFUSE` and none is claim-qualifying."""
    warnings = tuple(codes.packaged_warning(code) for code in (
        codes.SUBJECT_IDENTITY_NOT_READ_FROM_GRAPH,
        codes.EVIDENCE_SOURCES_ABSENT_IN_V1,
        codes.RELATIONSHIPS_UNAVAILABLE_IN_V1))
    package = make_package(warnings=warnings)

    view = package_view.warning_view(
        package, [{"description": ""} for _ in warnings])

    assert [row["code"] for row in view["capability_limitations"]] == [
        "subject_identity_not_read_from_graph", "evidence_sources_absent_in_v1",
        "relationships_unavailable_in_v1"]
    assert all(row["is_capability_limitation"] for row in view["capability_limitations"])
    assert view["claim_qualifying"] == []
    assert view["blocking"] == []
    assert "does not reduce the verification status" in view["limitation_note"]


def test_the_three_truncation_codes_carry_the_consequence_a_reader_needs_once():
    """The numbers stay in `detail`, which is rendered verbatim. What the panel adds is the
    sentence the numbers do not say — that the result is not exhaustive."""
    for code in (codes.RETRIEVAL_TRUNCATED, codes.TOKEN_BUDGET_TRIMMED,
                 codes.SECTION_TRUNCATED):
        assert package_view.CONSEQUENCE[code]
    assert "not exhaustive" in package_view.CONSEQUENCE[codes.RETRIEVAL_TRUNCATED]
    assert "No protected fact was dropped" in package_view.CONSEQUENCE[
        codes.TOKEN_BUDGET_TRIMMED]


def test_a_warning_row_keeps_every_field_it_arrived_with():
    warning = codes.packaged_warning(codes.SINGLE_SOURCE, subject_ids=("obs:a",),
                                     detail="one document reports this slot")
    row = package_view.warning_payload(warning, {"description": "…"})

    assert row["code"] == "single_source"
    assert row["severity"] == Severity.ANNOTATE.value
    assert row["kind"] == "claim_qualifying"
    assert row["category"] == WarningCategory.FACT_QUALITY_WARNING.value
    assert row["category_label"] == package_view.CATEGORY_LABEL["fact_quality_warning"]
    assert row["subject_ids"] == ["obs:a"]
    assert row["detail"] == "one document reports this slot"


def test_no_ontology_fact_is_reachable_from_the_prompt_request():
    """*"Prompt instructions stay editable; ontology facts are not reachable from the prompt UI"*
    — §4 S6, and the mechanical half of it is that a `PromptRequest` has nowhere to put one.

    Driven through the request type rather than asserted about the panel: a field the browser
    cannot send is a field no interface can grow a control for by accident.
    """
    from story.demo_ui import prompt_presets

    request = prompt_presets.PromptRequest.from_payload({
        "preset_id": prompt_presets.DEFAULT_PRESET_ID,
        "semantic_facts": [{"fact_id": "sem:x", "statement": "a definition I made up"}],
        "identity_facts": [{"attribute": "description", "value": "a company I invented"}],
        "comparability_facts": [],
        "facts": [],
    })

    assert set(request.ignored_fields) == {
        "semantic_facts", "identity_facts", "comparability_facts", "facts"}
    for banned in ("semantic", "identity", "comparability", "description"):
        assert not any(banned in field for field in
                       ("preset_id", "planner_instructions", "writer_instructions",
                        "style_guidance", "length_target")), banned


def test_a_warning_from_an_older_artifact_still_renders_a_category():
    """`category_of` falls back to the kind's own category for a code this build never
    declared, so a stored package does not leave a blank column."""
    stale = PackagedWarning(code="a_code_from_before", severity=Severity.ANNOTATE)
    row = package_view.warning_payload(stale, {})

    assert row["category"] == WarningCategory.FACT_QUALITY_WARNING.value
    assert row["category_label"]
