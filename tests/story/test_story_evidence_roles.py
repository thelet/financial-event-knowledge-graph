"""S1 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS — the schema barrier, asserted.

Four later stages are written against the vocabulary and the fields this step adds, so what
this file holds fixed is not "the enum exists" but the three properties that make the addition
safe to build on:

1. **The vocabularies are the plan's, member for member.** S2 classifies into `EvidenceRole`
   and S4 populates `FactKind`; a member added or renamed here silently changes what those
   stages are allowed to say.
2. **Every field is inert.** S1 adds shape and changes no behaviour, so a package built by the
   real builder must report `unassessed` quality, `observed` facts, no corroboration and three
   empty ontology-fact sections. A field that arrived already populated would mean S1 had
   quietly performed a later stage's work with none of its evidence.
3. **Nothing that reaches the model widened.** `PassageUnusableReason` is a new vocabulary
   rather than four more members on `UnusableReason` precisely so the planner's schema — the
   grammar the server is constrained by — is unchanged, and that is asserted rather than
   asserted about.

The exception the plan permits is the digest, and it is exercised here too: the three new
sections are inside `package_content_digest`, inside `section_counts` and inside the prompt
slice, because a section the budget does not cover is a section two runs can differ on
silently.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from story.core.keys import package_content_digest
from story.core.models import (
    PACKAGE_VERSION,
    BudgetParameters,
    CausalLanguage,
    ComparabilityFact,
    EvidenceRole,
    FactKind,
    IdentityFact,
    PackagedFact,
    PackagedMetric,
    PackagedPassage,
    PassageCitation,
    PassageQuality,
    PassageUnusableReason,
    SemanticFact,
    UnusableReason,
)
from story.stages.generation.prompts import planner_prompt, planner_schema, writer_prompt
from story.stages.packaging import counter_evidence as counter
from story.stages.packaging import ontology_facts
from story.stages.packaging import package_assembly as assembly
from story.stages.packaging import section_bounds

from conftest import make_package  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------------------
# The two closed vocabularies §4 S1 names
# ---------------------------------------------------------------------------------------


def test_the_evidence_roles_are_the_six_the_plan_names_and_no_others():
    """`counter_evidence` and `warning_only` are the two the whole plan turns on.

    §1 measured 20 issues attached to the inventory candidate's two passages, *"not one of
    which challenges"* the fact they were attached to. The vocabulary is what lets S2 say so:
    without a `warning_only` member the only honest place to put an extraction diagnostic is the
    section that reads as a contradiction.
    """
    assert [role.value for role in EvidenceRole] == [
        "primary_support", "corroborating_support", "context", "counter_evidence",
        "warning_only", "unusable"]


def test_the_fact_kinds_are_the_five_the_plan_names_and_no_others():
    assert [kind.value for kind in FactKind] == [
        "observed", "derived", "semantic", "identity", "comparability"]


def test_passage_quality_distinguishes_unassessed_from_assessed_and_clean():
    """Two members would make *"nobody looked"* and *"looked and it is fine"* one state, and
    only the second licenses using the passage. S2 is what moves a row off `unassessed`."""
    assert [q.value for q in PassageQuality] == ["unassessed", "usable", "unusable"]


def test_the_passage_quality_reasons_are_the_four_S2_will_decide():
    assert [r.value for r in PassageUnusableReason] == [
        "empty_or_structural_only", "no_relevant_proposition", "corrupted_extraction",
        "insufficient_content"]


def test_the_planner_reason_vocabulary_did_not_widen_by_one_member():
    """**Why `PassageUnusableReason` is separate from `UnusableReason`.**

    `UnusableReason`'s members are editorial dispositions the *model* declares (§11 point 2) and
    they reach it as an `enum` in §15.3's portable schema. The four content reasons are decided
    by code before any model runs. Extending the existing enum would have handed the planner
    `corrupted_extraction` as a judgement it has no way to establish — so the test is over the
    schema the server is actually constrained by, not over the enum's own members.
    """
    schema = planner_schema(causal_language=CausalLanguage.FORBIDDEN)
    offered = schema["properties"]["unusable_evidence"]["items"]["properties"]["reason"]["enum"]

    assert offered == [member.value for member in UnusableReason]
    assert not set(offered) & {reason.value for reason in PassageUnusableReason}


# ---------------------------------------------------------------------------------------
# `PackagedPassage` — the four fields, and the defaults that refuse to flatter
# ---------------------------------------------------------------------------------------


def test_a_passage_built_without_a_role_refuses_rather_than_claiming_support():
    """The default that does not exist, because every candidate default is a claim.

    `primary_support` would have an unmigrated call site assert that an arbitrary passage backs
    a fact; `unusable` would assert it backs nothing. Neither is knowable from here, so the
    construction fails and names the field.
    """
    with pytest.raises(ValidationError) as raised:
        PackagedPassage(passage_id="psg:1", document_id="doc:1", text="x", char_count=1)

    assert "role" in str(raised.value)


def test_a_passage_reports_that_its_content_was_never_assessed():
    passage = PackagedPassage(passage_id="psg:1", document_id="doc:1", text="x", char_count=1,
                              role=EvidenceRole.CONTEXT)

    assert passage.quality_status is PassageQuality.UNASSESSED
    assert passage.unusable_reason is None
    assert passage.match_basis == ""


def test_an_unusable_reason_without_the_finding_it_explains_is_refused():
    """A reason on a passage nobody found unusable is a row a consumer can read either way."""
    with pytest.raises(ValidationError) as raised:
        PackagedPassage(passage_id="psg:1", document_id="doc:1", text="x", char_count=1,
                        role=EvidenceRole.UNUSABLE,
                        unusable_reason=PassageUnusableReason.EMPTY_OR_STRUCTURAL_ONLY)

    assert "unusable_reason" in str(raised.value)


def test_a_passage_may_carry_a_reason_once_the_finding_is_stated():
    passage = PackagedPassage(
        passage_id="psg:1", document_id="doc:1", text="| | |", char_count=5,
        role=EvidenceRole.UNUSABLE, quality_status=PassageQuality.UNUSABLE,
        unusable_reason=PassageUnusableReason.EMPTY_OR_STRUCTURAL_ONLY)

    assert passage.unusable_reason is PassageUnusableReason.EMPTY_OR_STRUCTURAL_ONLY


# ---------------------------------------------------------------------------------------
# `match_basis` as a field, and `match_basis_of` reading it
# ---------------------------------------------------------------------------------------


def counter_passage(basis: str,
                    role: EvidenceRole = EvidenceRole.COUNTER_EVIDENCE) -> PackagedPassage:
    return PackagedPassage(
        passage_id="psg:counter", document_id="doc:1", text="Gross Margin (12.6)%",
        char_count=20, role=role, match_basis=basis)


def test_match_basis_of_reads_the_field_and_not_the_disclosure_warnings():
    """The workaround is gone, and this is what proves it rather than asserting it.

    `counter_evidence.py` used to reconstruct the basis from the two `ANNOTATE` codes, so a
    package trimmed past its twenty-warning cap lost the answer for a row it was still carrying.
    The package below holds the row and **no warning at all**; a reader of the warnings would
    return nothing.
    """
    package = make_package(
        counter_evidence=(counter_passage(counter.MATCH_BASIS_SAME_DOCUMENT),),
        warnings=())

    assert counter.match_basis_of(package) == {
        "psg:counter": counter.MATCH_BASIS_SAME_DOCUMENT}


def test_a_counter_evidence_row_with_no_basis_is_a_missing_key_and_not_a_default():
    """The contract the function has always had: absent is absent, never `same_document`."""
    package = make_package(counter_evidence=(counter_passage(""),))

    assert counter.match_basis_of(package) == {}


def test_a_row_whose_passage_disagrees_with_it_about_the_basis_cannot_be_built():
    """Two readers now exist — the packaged row and the disclosure warning — and a row that
    could hold two answers is a package whose panel and whose consumer disagree."""
    from story.stages.packaging.passage_excerpts import whole

    with pytest.raises(ValueError) as raised:
        counter.CounterEvidenceRow(
            passage=counter_passage(counter.MATCH_BASIS_VALUE_OUTSIDE_TOLERANCE),
            match_basis=counter.MATCH_BASIS_OPPOSITE_DIRECTION,
            grain=counter.MATCH_BASIS_SAME_DOCUMENT, role=EvidenceRole.COUNTER_EVIDENCE,
            issue_id="issue:1", code="AMBIGUOUS_ALIAS", severity="refusal", severity_rank=1,
            metric_id="gaap_gross_margin", period_key="2022Q3", row_label="Gross Margin",
            excerpt=whole("Gross Margin (12.6)%"))

    assert "match_basis" in str(raised.value)


def test_a_row_calling_itself_counter_evidence_on_a_bare_association_cannot_be_built():
    """§4 S2's rule, made unconstructible rather than merely unproduced.

    *"Same-document proximity is not enough. A shared keyword is not enough."* A row whose only
    basis is where it was found is precisely what §1 measured twenty of, and the type refuses it.
    """
    from story.stages.packaging.passage_excerpts import whole

    with pytest.raises(ValueError) as raised:
        counter.CounterEvidenceRow(
            passage=counter_passage(counter.MATCH_BASIS_SAME_DOCUMENT),
            match_basis=counter.MATCH_BASIS_SAME_DOCUMENT,
            grain=counter.MATCH_BASIS_SAME_DOCUMENT, role=EvidenceRole.COUNTER_EVIDENCE,
            issue_id="issue:1", code="AMBIGUOUS_ALIAS", severity="refusal", severity_rank=1,
            metric_id="gaap_gross_margin", period_key="2022Q3", row_label="Gross Margin",
            excerpt=whole("Gross Margin (12.6)%"))

    assert "counter-evidence exactly when" in str(raised.value)


def test_the_disclosure_warning_still_carries_the_basis_for_the_evidence_panel():
    """The field replaced the *reader*, not the disclosure. §13.17's panel renders the warning
    beside the claim and no consumer of it changed."""
    from story.stages.packaging.passage_excerpts import whole

    row = counter.CounterEvidenceRow(
        passage=counter_passage(counter.MATCH_BASIS_ISSUE_CHANGES_READING),
        match_basis=counter.MATCH_BASIS_ISSUE_CHANGES_READING,
        grain=counter.MATCH_BASIS_SAME_DOCUMENT, role=EvidenceRole.COUNTER_EVIDENCE,
        issue_id="issue:1", code="QUOTED_SPAN_NOT_IN_PASSAGE", severity="rejection",
        severity_rank=0, metric_id="gaap_gross_margin", period_key="2022Q3",
        row_label="Gross Margin", excerpt=whole("Gross Margin (12.6)%"))

    assert "match_basis=issue_changes_reading" in row.disclosure().detail
    assert row.disclosure().code == "counter_evidence_same_document"


# ---------------------------------------------------------------------------------------
# `PackagedFact` — the kind and the three corroboration lists
# ---------------------------------------------------------------------------------------


def observed_fact(**overrides: object) -> PackagedFact:
    fields: dict[str, object] = dict(
        observation_id="obs:a", metric_id="housing_inventory_homes",
        metric_label="Homes in inventory", period_key="2023Q1", shape="instant",
        value=6261.0, unit="homes", source_lane="normalized_table",
        validation_state="clean", passage_id="psg:1", document_id="doc:1")
    fields.update(overrides)
    return PackagedFact(**fields)  # type: ignore[arg-type]


def test_a_packaged_fact_is_an_observation_unless_something_says_otherwise():
    fact = observed_fact()

    assert fact.fact_kind is FactKind.OBSERVED
    assert fact.corroborating_observation_ids == ()
    assert fact.corroborating_passage_ids == ()
    assert fact.corroborating_document_ids == ()


def test_the_five_discarded_sources_have_somewhere_to_go_without_minting_a_second_fact():
    """§1.1's measurement, as a shape: six concordant observations, one canonical fact.

    S3 keeps one fact and records the rest by id. A test that let this be five more `facts[]`
    rows would be asserting the duplicate-fact defect the plan forbids in the same sentence.
    """
    fact = observed_fact(
        corroborating_observation_ids=("obs:b", "obs:c", "obs:d", "obs:e", "obs:f"),
        corroborating_passage_ids=("psg:2", "psg:3"),
        corroborating_document_ids=("doc:2", "doc:3"))

    assert len(fact.corroborating_observation_ids) == 5
    assert fact.observation_id not in fact.corroborating_observation_ids


@pytest.mark.parametrize("field", ["corroborating_observation_ids", "corroborating_passage_ids",
                                   "corroborating_document_ids"])
def test_a_corroboration_list_out_of_order_or_repeating_is_refused(field):
    """Sorted and unique for `_require_sorted_unique`'s reason: these reach
    `package_content_digest`, and two orderings of one set would be two packages."""
    with pytest.raises(ValidationError):
        observed_fact(**{field: ("z", "a")})
    with pytest.raises(ValidationError):
        observed_fact(**{field: ("a", "a")})


# ---------------------------------------------------------------------------------------
# `PackagedMetric.description` — the field, and where it may not come from
# ---------------------------------------------------------------------------------------


def test_a_packaged_metric_carries_no_definition_until_S4_supplies_one():
    metric = PackagedMetric(metric_id="gaap_gross_margin", label="GAAP Gross Margin",
                            unit="percent")

    assert metric.description is None


def test_the_committed_packages_metric_description_is_the_ontologys_and_not_the_nodes():
    """C4, as a property of the artifact rather than of a docstring.

    The `:Metric` node has a `description` property carrying, on this run, the *same text*
    *(checked live 2026-08-05: `adjusted_gross_margin` reads `'Adjusted Gross Profit as a
    percentage of revenue.'` in both)*. Equal text is not the same source, which is why this
    test cannot be *"the description matches the node"* and is instead *"the description matches
    the ontology"*: the same node carries no `percentage_min`, no `distinct_from` and no
    `population`, so a packager that reached for it would fill four fields from a place that
    owns one.
    """
    import json
    from pathlib import Path

    from story.core.models import StoryEvidencePackage

    fixture = (Path(__file__).parent / "fixtures" / "story_demo" / "evidence_package.json")
    package = StoryEvidencePackage.model_validate(
        json.loads(fixture.read_text(encoding="utf-8")))

    from ontology import load_ontology

    registry = load_ontology(package.ontology_id).registry
    assert package.metrics != ()
    assert all(metric.description for metric in package.metrics)
    assert [metric.description for metric in package.metrics] == [
        registry.metric(metric.metric_id).description for metric in package.metrics]


# ---------------------------------------------------------------------------------------
# The three ontology-fact types
# ---------------------------------------------------------------------------------------


def test_a_semantic_fact_states_its_authority_and_its_editability_or_is_not_built():
    """Neither field is defaulted. The ontology is authoritative and subject identity is not,
    and a default here would be this module answering for the stage that knows."""
    with pytest.raises(ValidationError):
        SemanticFact(fact_id="sem:1", metric_id="gaap_gross_margin", attribute="definition",
                     statement="Gross profit as a percentage of revenue.")


def test_a_semantic_fact_is_semantic_and_cannot_be_relabelled():
    fact = SemanticFact(
        fact_id="sem:gaap_gross_margin:definition", metric_id="gaap_gross_margin",
        attribute="definition", statement="Gross profit as a percentage of revenue.",
        value="", authoritative=True, editable=False,
        source="ontology:real_estate_marketplace_v1@2.0.0")

    assert fact.fact_kind is FactKind.SEMANTIC
    with pytest.raises(ValidationError):
        SemanticFact.model_validate(fact.model_dump(mode="json") | {"fact_kind": "observed"})


def test_a_semantic_fact_may_cite_filed_text_and_ordinarily_does_not():
    """A definition the ontology asserts is not a sentence in a 10-Q. Empty citations are the
    ordinary case; inventing a span for one would be the fabrication §13.7 refuses."""
    plain = SemanticFact(fact_id="sem:1", metric_id="m", attribute="unit", statement="percent",
                         authoritative=True, editable=False)
    cited = plain.model_copy(update={"citations": (PassageCitation(
        passage_id="psg:1", document_id="doc:1", char_start=0, char_end=10),)})

    assert plain.citations == ()
    assert cited.citations[0].passage_id == "psg:1"


def test_an_identity_fact_can_say_the_corpus_has_no_source_backed_description():
    """§4 S4: *"No company description may be invented from model knowledge."*

    Omitting the row would leave the model free to supply one from its weights and leave the
    panel unable to say the corpus was asked. `available: false` says both.
    """
    absent = IdentityFact(
        fact_id="ident:opendoor:description", entity_id="opendoor", attribute="description",
        statement="No source-backed description of this entity exists in the corpus.",
        available=False, authoritative=False, editable=False)

    assert absent.available is False
    assert absent.value == ""
    assert absent.fact_kind is FactKind.IDENTITY


def test_a_comparability_fact_is_a_rule_and_sorts_the_metrics_it_ranges_over():
    fact = ComparabilityFact(
        fact_id="cmp:mutually-distinct:gross-margin", rule_id="mutually_distinct_group",
        metric_ids=("adjusted_gross_margin", "gaap_gross_margin"),
        statement="Adjusted Gross Margin and GAAP Gross Margin are mutually distinct and may "
                  "not be compared as one metric.",
        authoritative=True, editable=False)

    assert fact.fact_kind is FactKind.COMPARABILITY
    with pytest.raises(ValidationError):
        ComparabilityFact.model_validate(
            fact.model_dump(mode="json")
            | {"metric_ids": ["gaap_gross_margin", "adjusted_gross_margin"]})


# ---------------------------------------------------------------------------------------
# The three new sections: counted, digested, and inside the prompt budget
# ---------------------------------------------------------------------------------------


def semantic() -> SemanticFact:
    return SemanticFact(
        fact_id="sem:gaap_gross_margin:definition", metric_id="gaap_gross_margin",
        attribute="definition", statement="Gross profit as a percentage of revenue.",
        authoritative=True, editable=False,
        source="ontology:real_estate_marketplace_v1@2.0.0")


def test_the_new_sections_default_empty_on_every_package_built_today():
    package = make_package()

    assert package.semantic_facts == ()
    assert package.identity_facts == ()
    assert package.comparability_facts == ()


def test_section_counts_names_the_three_new_sections_even_at_zero():
    """A count that vanished at zero would make an absent section unreadable — and
    `section_counts` is itself inside `package_content_digest`, so a section it does not name is
    a section two runs can differ on silently."""
    counts = assembly.section_counts(assembly.PackageSections())

    assert counts["semantic_facts"] == 0
    assert counts["identity_facts"] == 0
    assert counts["comparability_facts"] == 0


def test_adding_a_semantic_fact_moves_the_package_content_digest():
    """The digest covers the new sections. If it did not, S4 could populate them and two
    packages carrying different ontology facts would share an id."""
    bare = make_package()
    with_fact = make_package(semantic_facts=(semantic(),))

    assert (package_content_digest(bare.digestible_payload())
            != package_content_digest(with_fact.digestible_payload()))


def test_the_new_sections_are_inside_the_slice_the_token_budget_bounds():
    """`prompt_slice` is a subtraction, not an allow-list, exactly so a section added later is
    evidence until somebody argues otherwise. This is what says the subtraction still holds."""
    payload = make_package(semantic_facts=(semantic(),)).digestible_payload()
    sliced = section_bounds.prompt_slice(payload)

    assert "semantic_facts" in sliced
    assert "identity_facts" in sliced
    assert "comparability_facts" in sliced


def test_the_package_version_moved_because_the_shape_did():
    """`PACKAGE_VERSION` exists so two shapes can never share a `package_id`.

    `1.1.0` at S1: three sections added and four row types re-shaped. `1.2.0` at S2: one more
    section (`diagnostic_passages`) and one more field (`PackagedPassage.diagnostic_codes`). Not
    bumping it would have kept the replay demo green by breaking the one guarantee the constant
    makes."""
    assert PACKAGE_VERSION == "1.2.0"


# ---------------------------------------------------------------------------------------
# What the committed artifact says now that S2 and S3 have run
# ---------------------------------------------------------------------------------------


def test_the_committed_package_has_been_assessed_corroborated_and_not_yet_given_ontology_facts():
    """Read off the artifact a live rebuild produced, and it is the S1/S2/S3 boundary.

    At S1 this test asserted the opposite of half of the below — quality `unassessed`, no
    corroboration — because S1 added shape and changed no behaviour. S2 and S3 are what change
    it: every passage has been through `passage_quality.assess`, and every fact names the
    concordant sources §6.1 collapsed. **S4 has landed too**, so the three ontology-fact
    sections are populated rather than empty and the test asserts what is in them; `fact_kind`
    is still `observed` on every row in `facts[]`, because a derived quantity is the writer's
    `Calculation` and not a packaged fact.

    The fixture is the D4 candidate, rebuilt live on 2026-08-05: the package moved from
    `…8e0cb0cf6655` to `…91fd3fe66619`, 5 facts and 3 primaries to 4 and 2, and its facts now
    name corroborating sources where they named none.
    """
    import json
    from pathlib import Path

    from story.core.models import StoryEvidencePackage

    fixture = (Path(__file__).parent / "fixtures" / "story_demo" / "evidence_package.json")
    package = StoryEvidencePackage.model_validate(
        json.loads(fixture.read_text(encoding="utf-8")))

    assert [p.role for p in package.primary_passages] == (
        [EvidenceRole.PRIMARY_SUPPORT] * len(package.primary_passages))
    for passage in (*package.primary_passages, *package.context_passages,
                    *package.explanatory_passages, *package.counter_evidence,
                    *package.diagnostic_passages):
        # A passage a fact was read from is never `unassessed` any more, and none of the 150
        # passages this corpus evidences an observation from assesses as unusable.
        assert passage.quality_status is PassageQuality.USABLE
        assert passage.unusable_reason is None
    assert any(fact.corroborating_observation_ids for fact in package.facts), (
        "§1.1 counted six concordant sources per slot; a package naming none of them is the "
        "defect S3 exists to fix")
    for fact in package.facts:
        assert fact.fact_kind is FactKind.OBSERVED
        assert fact.observation_id not in fact.corroborating_observation_ids
    assert [f.attribute for f in package.identity_facts] == list(
        ontology_facts.IDENTITY_ATTRIBUTES)
    assert {f.metric_id for f in package.semantic_facts} == {
        m.metric_id for m in package.metrics}
    assert all(f.authoritative and not f.editable for f in package.semantic_facts)
    # The one identity attribute the corpus cannot answer, on the committed artifact.
    description = next(f for f in package.identity_facts if f.attribute == "description")
    assert (description.available, description.value) == (False, "")
    assert description.statement == ontology_facts.NO_DESCRIPTION_STATEMENT


def test_the_semantic_fact_reaches_both_prompts_at_S4_and_the_row_fields_still_do_not():
    """**Inverted at S4, and the inversion is the deliverable.** At S1 this asserted that the
    definition reached neither prompt, because nothing populated it; §4 S4's whole claim is that
    it now reaches both.

    What is still asserted is the other half, unchanged: the *row* fields S1 added — a passage's
    `quality_status`, a fact's `corroborating_*` lists — remain out of both prompts. They are for
    §4 S6's evidence panel, and a planner shown `corroborating_observation_ids` would be a
    planner invited to name an id §11 rule 1 rejects.

    Asserted over the rendered prompt because that is the only thing the model sees — the same
    reason §5 requires the serialised provider request to be inspected rather than the package
    object.
    """
    package = make_package(
        semantic_facts=(semantic(),),
        counter_evidence=(counter_passage(counter.MATCH_BASIS_SAME_DOCUMENT),))
    from story.core.models import EditorialPlan

    plan = EditorialPlan(candidate_id=package.candidate_id, package_id=package.package_id,
                         thesis="t", why_it_matters="w")
    planner = planner_prompt(package)
    writer = writer_prompt(package, plan, package.primary_passages, length_target=4)

    assert "Gross profit as a percentage of revenue." in planner
    assert "Gross profit as a percentage of revenue." in writer
    # The `fact_id` stays on the package for the panel and never enters a prompt: §11 rule 1
    # admits only ids beginning `obs:` in `required_fact_ids`, so a rendered `sem:` id is an
    # invitation to a rejection.
    assert semantic().fact_id not in planner + writer
    assert "quality_status" not in planner + writer
    assert "corroborating" not in planner + writer


def test_a_package_built_by_the_assembler_carries_no_ontology_facts_and_still_digests():
    """The assembly half, with no retriever and no ontology: the sections exist, stay empty,
    and the digest reproduces over them."""
    sections = assembly.PackageSections()
    sections.subject = make_package().subject
    sections.facts = [observed_fact()]
    sections.primary_passages = [PackagedPassage(
        passage_id="psg:1", document_id="doc:1", text="Homes in inventory 6,261",
        char_count=24, role=EvidenceRole.PRIMARY_SUPPORT)]

    from conftest import make_candidate  # type: ignore[import-not-found]
    from story.core.graph_identity import GraphIdentity

    identity = GraphIdentity(
        graph_run_id="graph-v1-0483dc6b4b10",
        graph_projection_version="1.2.0",
        extraction_run_id="extract-v1-lexical-2422c4252c07",
        extraction_run_directory="data/extraction_runs/extract-v1-lexical-2422c4252c07",
        run_complete_sha256="a" * 64,
        input_content_digest="40ea214c701c",
        ontology_id="real_estate_marketplace_v1",
        ontology_version="2.0.0",
        ontology_definition_hash="bb94f522ba122470" + "0" * 48,
        node_count=28836,
        edge_count=35600,
    )
    assembler = assembly.PackageAssembler(identity=identity, budget=BudgetParameters())
    package = assembler.finalize(sections, make_candidate())

    assert package.package_version == "1.2.0"
    assert package.budget.section_counts["semantic_facts"] == 0
    assert package.package_content_digest == package_content_digest(package.digestible_payload())
