"""S9 — the deterministic verifier, driven against the founder's demo candidate.

Offline, no database, no model server. Every value in the fixtures is a **real corpus value**:
`adjusted_gross_margin` 2022Q3 is `3.3`, `gaap_gross_margin` 2022Q3 is `-12.6`, the gap is
`15.899999999999999` and rounds to `15.9` percentage points, the subject is `opendoor`, and the
candidate is
`cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:opendoor:2022Q3:9682f1c1c85a`
*(verified live 2026-08-03/04 by S3 and the census tests; re-stated here rather than re-read,
because a verifier test must run on a clean checkout where `data/` is absent)*.

**The observation ids follow `extraction/core/identifiers.py`'s shape —
`obs:{metric}:{subject}:{period}:{lane}:{digest12}` — with the digest segment written by hand.**
The digests are positional hashes that cannot be recomputed without the extraction run, and
nothing in this module reads one: they are opaque handles. Recorded so nobody later mistakes
them for verified values.

**Float residues are load-bearing here and every comparison is made after rounding.**
`3.3 - (-12.6)` is `15.899999999999999`, and `3.3 - 13.2` is `-9.899999999999999`. A test that
asserted `== 15.9` would pass or fail on the arithmetic rather than on the check.

The ten malicious drafts each have their own test and each names the code that caught it.
"""

from __future__ import annotations

import pytest

from story.core.keys import package_content_digest
from story.core.models import (
    BudgetParameters,
    Calculation,
    CheckOutcome,
    Conflict,
    ConflictCluster,
    Counterpoint,
    Draft,
    DraftSentence,
    EditorialPlan,
    EventParticipant,
    FactBinding,
    KeyPoint,
    PackageBudget,
    PackagedDocument,
    PackagedEvent,
    PackagedFact,
    PackagedMetric,
    PackagedPassage,
    PackagedSubject,
    PassageCitation,
    Remedy,
    SentenceKind,
    Severity,
    StatementClass,
    StoryEvidencePackage,
)
from story.stages.verification import (
    GATE,
    DeterministicVerifier,
    DraftAccepted,
    rejection_for,
)
from story.stages.verification.codes import UndeclaredCode, finding
from story.stages.verification.metric_surfaces import MetricAliasIndex
from story.stages.verification.period_grammar import resolve as resolve_period

# ---------------------------------------------------------------------------------------
# The run this implementation is built on (IMPLEMENTATION_STEPS §0, verified 2026-08-03)
# ---------------------------------------------------------------------------------------

CANDIDATE_ID = (
    "cand:cross-metric-divergence:adjusted-gross-margin-gaap-gross-margin:"
    "opendoor:2022Q3:9682f1c1c85a"
)
PACKAGE_ID = (
    "pkg:cross-metric-divergence-adjusted-gross-margin-gaap-gross-margin-opendoor-2022q3:"
    "4f2b91c7de08"
)
GRAPH_RUN_ID = "graph-v1-0483dc6b4b10"
EXTRACTION_RUN_ID = "extract-v1-lexical-833f7bcfbce9"
RUN_COMPLETE_SHA256 = "1cc8f7b01c040531" + "0" * 48
ONTOLOGY_DEFINITION_HASH = (
    "bb94f522ba1224702289d8e0646f5fdd8fc6d31cd341f604a7f879ee87e1af34")

AGM_ID = "obs:adjusted-gross-margin:opendoor:2022Q3:normalized-table:7c1a04e9b2d5"
GGM_ID = "obs:gaap-gross-margin:opendoor:2022Q3:normalized-table:1e77b0c9a3f4"
AGM_Q4_ID = "obs:adjusted-gross-margin:opendoor:2022Q4:normalized-table:5b2d81f0c6ae"
AGM_2023Q1_ID = "obs:adjusted-gross-margin:opendoor:2023Q1:normalized-table:9ad3e2b74c10"

PASSAGE_ID = "psg:opendoor-10q-2022q3:margins-table"
DOCUMENT_ID = "doc:opendoor-10q-2022q3"

#: A margins table as the extraction lane normalises one. Both quoted texts occur verbatim,
#: which is §13.7 Rule A step 1 — measured to hold on 2,714/2,714 evidence rows.
PASSAGE_TEXT = (
    "Three Months Ended September 30, 2022\n"
    "Revenue $3,394\n"
    "Gross Margin (12.6)\n"
    "Adjusted Gross Margin 3.3\n"
)
COLUMN_LABEL = "Three Months Ended September 30, 2022"


def _span(needle: str) -> tuple[int, int]:
    start = PASSAGE_TEXT.index(needle)
    return start, start + len(needle)


def make_fact(**overrides: object) -> PackagedFact:
    fields: dict[str, object] = dict(
        observation_id=AGM_ID,
        metric_id="adjusted_gross_margin",
        metric_label="Adjusted Gross Margin",
        period_key="2022Q3",
        period_start="2022-07-01",
        period_end="2022-09-30",
        shape="duration",
        value=3.3,
        unit="percent",
        scale="units",
        row_label="Adjusted Gross Margin",
        column_label=COLUMN_LABEL,
        source_lane="normalized_table",
        validation_state="ok",
        passage_id=PASSAGE_ID,
        document_id=DOCUMENT_ID,
        quoted_text="3.3",
    )
    fields.update(overrides)
    return PackagedFact(**fields)  # type: ignore[arg-type]


GGM_FACT = make_fact(
    observation_id=GGM_ID,
    metric_id="gaap_gross_margin",
    metric_label="Gross Margin",
    value=-12.6,
    row_label="Gross Margin",
    quoted_text="(12.6)",
)

METRICS = (
    PackagedMetric(
        metric_id="adjusted_gross_margin", label="Adjusted Gross Margin", unit="percent",
        allowed_units=("percent",), aliases=("adjusted gross margin",),
        mutually_distinct_groups=("margin_measures",),
    ),
    PackagedMetric(
        metric_id="gaap_gross_margin", label="Gross Margin", unit="percent",
        allowed_units=("percent",), aliases=("gaap gross margin", "GAAP gross margin"),
        mutually_distinct_groups=("margin_measures",),
    ),
)


def make_package(**overrides: object) -> StoryEvidencePackage:
    """The demo package, with `package_content_digest` stamped the way §10.3 computes it."""
    fields: dict[str, object] = dict(
        package_id=PACKAGE_ID,
        candidate_id=CANDIDATE_ID,
        detector_id="detector:cross_metric_divergence",
        detector_version="1.0.0",
        policy_version="canon-policy:1.0.0",
        graph_run_id=GRAPH_RUN_ID,
        graph_projection_version="1.2.0",
        extraction_run_id=EXTRACTION_RUN_ID,
        run_complete_sha256=RUN_COMPLETE_SHA256,
        ontology_id="real_estate_marketplace_v1",
        ontology_definition_hash=ONTOLOGY_DEFINITION_HASH,
        ontology_semantic_version="2.0.0",
        subject=PackagedSubject(entity_id="opendoor", entity_text="Opendoor Technologies Inc.",
                                resolved=True, labels=("Entity", "PublicCompany")),
        facts=(make_fact(), GGM_FACT),
        metrics=METRICS,
        primary_passages=(
            PackagedPassage(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, text=PASSAGE_TEXT,
                            char_count=len(PASSAGE_TEXT), passage_kind="normalized_table"),
        ),
        documents=(PackagedDocument(document_id=DOCUMENT_ID, form="10-Q",
                                    document_type="10-Q", filing_date="2022-11-03"),),
        budget=PackageBudget(artifact_token_estimate=1180, prompt_token_estimate=1046,
                             section_counts={"facts": 2, "metrics": 2},
                             parameters=BudgetParameters()),
    )
    fields.update(overrides)
    package = StoryEvidencePackage(**fields)  # type: ignore[arg-type]
    return package.with_content_digest(package_content_digest(package.digestible_payload()))


# ---------------------------------------------------------------------------------------
# The clean draft
# ---------------------------------------------------------------------------------------

AGM_TEXT = "Adjusted gross margin was 3.3% in the third quarter of 2022."
GGM_TEXT = "GAAP gross margin was -12.6% in the third quarter of 2022."
GAP_TEXT = "The gap between the two measures was 15.9 percentage points."
WARNING_TEXT = (
    "Both figures come from the same table, whose filing date is not recorded in this run.")


def _binding(text: str, rendered: str, **overrides: object) -> FactBinding:
    start = text.index(rendered)
    fields: dict[str, object] = dict(
        fact_id=AGM_ID, rendered=rendered, char_start=start, char_end=start + len(rendered),
        metric_surface="Adjusted gross margin", period_surface="the third quarter of 2022",
    )
    fields.update(overrides)
    return FactBinding(**fields)  # type: ignore[arg-type]


def agm_sentence(**overrides: object) -> DraftSentence:
    fields: dict[str, object] = dict(
        index=0, text=AGM_TEXT, kind=SentenceKind.REPORTED,
        fact_bindings=(_binding(AGM_TEXT, "3.3%"),),
        citations=(PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=_span("Adjusted Gross Margin 3.3")[0],
                                   char_end=_span("Adjusted Gross Margin 3.3")[1]),),
    )
    fields.update(overrides)
    return DraftSentence(**fields)  # type: ignore[arg-type]


def ggm_sentence(**overrides: object) -> DraftSentence:
    fields: dict[str, object] = dict(
        index=1, text=GGM_TEXT, kind=SentenceKind.REPORTED,
        fact_bindings=(_binding(GGM_TEXT, "-12.6%", fact_id=GGM_ID,
                                metric_surface="GAAP gross margin"),),
        citations=(PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=_span("Gross Margin (12.6)")[0],
                                   char_end=_span("Gross Margin (12.6)")[1]),),
    )
    fields.update(overrides)
    return DraftSentence(**fields)  # type: ignore[arg-type]


def gap_sentence(**overrides: object) -> DraftSentence:
    fields: dict[str, object] = dict(
        index=2, text=GAP_TEXT, kind=SentenceKind.CALCULATED,
        calculation=Calculation(
            operation="delta_pp",
            input_observation_ids=(GGM_ID, AGM_ID),
            expression="adjusted_gross_margin - gaap_gross_margin",
            result_rendered="15.9 percentage points",
        ),
    )
    fields.update(overrides)
    return DraftSentence(**fields)  # type: ignore[arg-type]


def warning_sentence(**overrides: object) -> DraftSentence:
    fields: dict[str, object] = dict(
        index=3, text=WARNING_TEXT, kind=SentenceKind.CONNECTIVE)
    fields.update(overrides)
    return DraftSentence(**fields)  # type: ignore[arg-type]


def make_draft(sentences=None, **overrides: object) -> Draft:
    fields: dict[str, object] = dict(
        candidate_id=CANDIDATE_ID,
        package_id=PACKAGE_ID,
        title="Two gross margins, sixteen points apart",
        sentences=tuple(sentences) if sentences is not None else (
            agm_sentence(), ggm_sentence(), gap_sentence(), warning_sentence()),
        style_profile_id="investor-plain:1",
    )
    fields.update(overrides)
    return Draft(**fields)  # type: ignore[arg-type]


def make_plan(**overrides: object) -> EditorialPlan:
    fields: dict[str, object] = dict(
        candidate_id=CANDIDATE_ID,
        package_id=PACKAGE_ID,
        thesis="Opendoor's two gross margins diverged by 15.9 points in 2022Q3.",
        why_it_matters="The adjustment is doing all the work in the quarter.",
        key_points=(
            KeyPoint(claim="Adjusted gross margin was 3.3% in 2022Q3.",
                     required_fact_ids=(AGM_ID,),
                     required_citation_passage_ids=(PASSAGE_ID,),
                     statement_class=StatementClass.REPORTED),
        ),
        required_warnings=("filing_date_unknown",),
    )
    fields.update(overrides)
    return EditorialPlan(**fields)  # type: ignore[arg-type]


@pytest.fixture
def verifier() -> DeterministicVerifier:
    return DeterministicVerifier(
        graph_run_id=GRAPH_RUN_ID,
        run_complete_sha256=RUN_COMPLETE_SHA256,
        ontology_definition_hash=ONTOLOGY_DEFINITION_HASH,
    )


def codes_of(verified) -> set[str]:  # type: ignore[no-untyped-def]
    return {found.code for found in verified.all_findings}


# ---------------------------------------------------------------------------------------
# The stage is what its contract says it is
# ---------------------------------------------------------------------------------------


def test_the_verifier_is_constructible_with_neither_a_database_nor_a_model_provider():
    """§13's whole standing: the deterministic layer runs first and answers alone.

    Driven through the protocol rather than asserted with `isinstance`: a
    `runtime_checkable` Protocol only proves some attributes exist, and what matters is that
    `verify` returns a decision from three plain values and nothing else.
    """
    from story.contracts import DraftVerifier

    subject: DraftVerifier = DeterministicVerifier()
    verified = subject.verify(make_draft(), make_package(), make_plan())
    assert verified.candidate_id == CANDIDATE_ID


def test_a_clean_draft_of_the_demo_candidate_passes_with_no_finding_at_all(verifier):
    """The founder's selected candidate, written correctly, survives every check."""
    verified = verifier.verify(make_draft(), make_package(), make_plan())
    assert verified.all_findings == ()
    assert verified.passed is True


def test_the_accepted_draft_carries_a_fact_ledger_and_a_calculation_ledger(verifier):
    """§13.17: `VerifiedDraft` carries the evidence panel and the derivation panel."""
    verified = verifier.verify(make_draft(), make_package(), make_plan())
    assert [entry.fact_id for entry in verified.fact_ledger] == [AGM_ID, GGM_ID]
    assert len(verified.calculation_ledger) == 1
    entry = verified.calculation_ledger[0]
    assert entry.operation == "delta_pp"
    # The residue is real and is why nothing here compares by equality.
    assert entry.recomputed_value == 15.899999999999999
    assert round(entry.recomputed_value, 1) == 15.9


def test_every_check_reports_its_own_denominator_so_a_clean_draft_cannot_claim_more(verifier):
    """§13.17: *"a draft with no numeric sentences must not report numbers: PASS"*."""
    verified = verifier.verify(make_draft(), make_package(), make_plan())
    numbers = verified.check("numbers")
    # Five numerals across four sentences: 3.3%, 2022, -12.6%, 2022, 15.9.
    assert numbers.examined == 5
    assert numbers.outcome is CheckOutcome.PASS


def test_a_draft_with_no_numeral_anywhere_reports_numbers_as_not_applicable_not_as_pass():
    """The denominator is the whole point: never run and clean are different answers."""
    sentence = DraftSentence(index=0, text="Margins moved in opposite directions.",
                             kind=SentenceKind.CONNECTIVE)
    verified = DeterministicVerifier().verify(
        make_draft(sentences=(sentence,)), make_package(),
        make_plan(required_warnings=()))
    assert verified.check("numbers").examined == 0
    assert verified.check("numbers").outcome is CheckOutcome.NOT_APPLICABLE
    assert verified.passed is True


def test_passed_is_derived_from_the_findings_and_is_not_a_field_anyone_can_set(verifier):
    """`graph/core/verification_report.py:217`'s discipline, carried into the story layer."""
    verified = verifier.verify(make_draft(), make_package(), make_plan())
    assert "passed" not in type(verified).model_fields
    assert verified.passed is True


# ---------------------------------------------------------------------------------------
# §13.17 — the gate
# ---------------------------------------------------------------------------------------


def test_every_code_in_the_gate_is_a_refusal_except_the_five_the_plan_names():
    """§13.17 states the exception list exhaustively, so the table must match it exactly."""
    warns = {code for code, entry in GATE.items() if entry.severity is Severity.WARN}
    annotates = {code for code, entry in GATE.items() if entry.severity is Severity.ANNOTATE}
    assert warns == {"over_precision", "paraphrase_distance"}
    assert annotates == {
        "event_review_flag", "conflict_immaterial_at_stated_precision",
        "warned_observation_used",
        # The one extension, declared in `codes.py`: §13.7.1's D15 hatch is worthless without
        # a panel row for the minority reading.
        "column_label_ambiguity_classified",
    }
    assert all(entry.blocking is (entry.severity is Severity.REFUSE) for entry in GATE.values())


def test_a_check_cannot_raise_a_code_the_gate_never_declared():
    """A code with no declared severity is a check nobody put through §13.17."""
    with pytest.raises(UndeclaredCode):
        finding("numbers_look_wrong_to_me")


def test_a_refusal_names_a_remedy_from_the_closed_enum_and_blocks(verifier):
    """§13.17: an enum is what makes a repair loop dispatchable."""
    draft = make_draft(sentences=(agm_sentence(
        text=AGM_TEXT,
        fact_bindings=(_binding(AGM_TEXT, "3.3%", metric_surface="GAAP gross margin"),),
    ),))
    verified = verifier.verify(draft, make_package(), make_plan(required_warnings=()))
    refusals = [f for f in verified.all_findings if f.blocking]
    assert refusals
    assert all(isinstance(f.remedy, Remedy) for f in refusals)
    rejection = rejection_for(verified)
    assert Remedy.NARROW_METRIC_SURFACE in rejection.remedies


def test_a_verification_nothing_blocked_cannot_be_filed_as_a_rejection(verifier):
    """§14 writes an acceptance and a rejection into different directories."""
    verified = verifier.verify(make_draft(), make_package(), make_plan())
    with pytest.raises(DraftAccepted):
        rejection_for(verified)


# ---------------------------------------------------------------------------------------
# §13.1 — numbers, in the corrected magnitude-and-sign form
# ---------------------------------------------------------------------------------------


def test_the_tolerance_test_compares_magnitudes_so_a_loss_written_unsigned_still_passes():
    """§13.1's correction, on its own worked case.

    `"$27.1 million"` against `−27,075,000` is `|Δ| = 25,000` inside a ±50,000 window. Written
    the plan's original signed way it is 54,175,000 and fails — which would fail every one of
    the **825** negative-valued observations in the corpus.
    """
    text = "Adjusted EBITDA was a loss of $27.1 million in the fourth quarter of 2020."
    fact = make_fact(
        observation_id="obs:adjusted-ebitda:opendoor:2020Q4:normalized-table:98849a208451",
        metric_id="adjusted_ebitda", metric_label="Adjusted EBITDA", period_key="2020Q4",
        period_start="2020-10-01", period_end="2020-12-31", value=-27_075_000.0, unit="USD",
        currency="USD", scale="thousands", row_label="Adjusted EBITDA",
        quoted_text="(27,075)",
    )
    package = make_package(
        facts=(fact,),
        metrics=(PackagedMetric(metric_id="adjusted_ebitda", label="Adjusted EBITDA",
                                unit="USD", aliases=("adjusted ebitda",)),),
        primary_passages=(PackagedPassage(
            passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
            text="Adjusted EBITDA (27,075)", char_count=24),),
    )
    sentence = DraftSentence(
        index=0, text=text, kind=SentenceKind.REPORTED,
        fact_bindings=(FactBinding(
            fact_id=fact.observation_id, rendered="$27.1 million",
            char_start=text.index("$27.1 million"),
            char_end=text.index("$27.1 million") + len("$27.1 million"),
            metric_surface="Adjusted EBITDA",
            period_surface="the fourth quarter of 2020"),),
        citations=(PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=0, char_end=24),),
    )
    verified = DeterministicVerifier().verify(
        make_draft(sentences=(sentence,)), package, make_plan(required_warnings=()))
    assert "number_outside_tolerance" not in codes_of(verified)
    assert "sign_disagreement" not in codes_of(verified)


def test_sign_is_a_separate_check_and_fires_only_when_the_numeral_carried_its_own_sign(verifier):
    """The other half of §13.1's split: a *written* minus must agree with the fact."""
    text = "GAAP gross margin was 12.6% in the third quarter of 2022."
    sentence = ggm_sentence(
        index=0, text=text,
        fact_bindings=(_binding(text, "12.6%", fact_id=GGM_ID,
                                metric_surface="GAAP gross margin"),),
    )
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    # Unsigned, so the magnitude claim stands and sign is not asserted.
    assert "sign_disagreement" not in codes_of(verified)

    # The same magnitude written against a *positive* fact, with the minus in the numeral:
    # here the sign is explicit and it disagrees.
    positive = make_fact(observation_id=GGM_ID, metric_id="gaap_gross_margin",
                         metric_label="Gross Margin", value=12.6, row_label="Gross Margin",
                         quoted_text="12.6")
    signed = ggm_sentence(index=0)
    assert "sign_disagreement" in codes_of(verifier.verify(
        make_draft(sentences=(signed,)), make_package(facts=(make_fact(), positive)),
        make_plan(required_warnings=())))


def test_an_uncovered_numeral_is_a_refusal_and_not_a_warning(verifier):
    """§13.1: a numeral nobody declared is a claim nobody checked."""
    text = "Adjusted gross margin was 3.3% in the third quarter of 2022, down from 9.9%."
    sentence = agm_sentence(text=text, fact_bindings=(_binding(text, "3.3%"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    unbound = [f for f in verified.all_findings if f.code == "unbound_numeral"]
    assert len(unbound) == 1
    assert unbound[0].observed == "9.9%"
    assert unbound[0].severity is Severity.REFUSE
    assert verified.passed is False


def test_a_binding_whose_span_does_not_hold_the_rendering_it_claims_is_refused(verifier):
    """§12: a span that does not hold its rendering points the whole of §13 at the wrong
    characters. `AGM_TEXT[0:4]` is `"Adju"`, not `"3.3%"`."""
    sentence = agm_sentence(fact_bindings=(FactBinding(
        fact_id=AGM_ID, rendered="3.3%", char_start=0, char_end=4,
        metric_surface="Adjusted gross margin",
        period_surface="the third quarter of 2022"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    found = [f for f in verified.all_findings if f.code == "binding_span_does_not_match_text"]
    assert found and found[0].observed == "Adju"


def test_over_precision_warns_against_this_facts_own_printed_form_and_does_not_block(verifier):
    """§13.1's WARN, and P2: the printed form is an argument, never a guess."""
    text = "Adjusted gross margin was 3.30% in the third quarter of 2022."
    sentence = agm_sentence(text=text, fact_bindings=(_binding(text, "3.30%"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    warned = [f for f in verified.all_findings if f.code == "over_precision"]
    assert len(warned) == 1
    assert warned[0].severity is Severity.WARN
    assert warned[0].blocking is False
    assert verified.passed is True


# ---------------------------------------------------------------------------------------
# §13.2 — units
# ---------------------------------------------------------------------------------------


def test_a_currency_symbol_on_a_percent_observation_is_refused(verifier):
    text = "Adjusted gross margin was $3.3 in the third quarter of 2022."
    sentence = agm_sentence(text=text, fact_bindings=(_binding(text, "$3.3"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "currency_symbol_on_non_monetary_unit" in codes_of(verified)


def test_a_change_surface_bound_to_a_reported_row_is_refused_because_no_observation_is_a_change(
        verifier):
    """§13.3's second gate: the extraction refused all 186 changes it saw."""
    text = "Adjusted gross margin was 3.3 percentage points in the third quarter of 2022."
    sentence = agm_sentence(text=text,
                            fact_bindings=(_binding(text, "3.3 percentage points"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "unit_mismatch" in codes_of(verified)


# ---------------------------------------------------------------------------------------
# §13.3 — percentages
# ---------------------------------------------------------------------------------------


def test_a_percent_change_sentence_with_no_declared_reading_is_unresolvable_not_imprecise(
        verifier):
    """§13.3's surface gate, applied before the arithmetic is consulted (§17's attack 3)."""
    text = "Adjusted gross margin fell 3.3% in the third quarter of 2022."
    sentence = agm_sentence(text=text, fact_bindings=(_binding(text, "3.3%"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "percent_change_ambiguous" in codes_of(verified)


def test_a_relative_change_across_zero_is_refused_outright(verifier):
    """§13.3's third gate. `adjusted_ebitda_margin` crosses zero six times in 26 quarters."""
    sentence = gap_sentence(
        index=0,
        text="The margin changed by 138% relative to the prior quarter.",
        calculation=Calculation(
            operation="delta_relative", input_observation_ids=(GGM_ID, AGM_ID),
            expression="(v2 - v1)/|v1| * 100",
            result_rendered="138% relative to the prior quarter"),
    )
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "relative_change_across_zero" in codes_of(verified)


def test_a_reported_sentence_may_not_state_a_change_of_a_percent_metric(verifier):
    """§13.3's second gate: no observation in the package is a change.

    The change numeral here is uncovered, which is what makes the sentence a *reported* claim
    about a quantity the graph does not hold. Bound instead, the same surface is refused one
    step earlier as `unit_mismatch` — a change surface on a reported row.
    """
    text = "Adjusted gross margin was 3.3%, up 15.9 percentage points from the GAAP figure."
    sentence = agm_sentence(text=text, fact_bindings=(_binding(text, "3.3%"),),
                            citations=agm_sentence().citations)
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "percent_change_reported_not_calculated" in codes_of(verified)


# ---------------------------------------------------------------------------------------
# §13.4 — periods
# ---------------------------------------------------------------------------------------


def test_the_period_grammar_is_closed_and_refuses_the_quarter_and_a_bare_year():
    """§13.4: a closed grammar, never a free date parser."""
    assert resolve_period("the third quarter of 2022").key == "2022Q3"
    assert resolve_period("3Q22").key == "2022Q3"
    assert resolve_period("fiscal 2022").key == "FY2022"
    assert resolve_period("the quarter").resolved is False
    assert resolve_period("2022").resolved is False


def test_a_quarter_surface_bound_to_a_year_to_date_observation_is_a_shape_conflation(verifier):
    """§13.4: 188 `(metric, period_end)` pairs carry more than one `period_key`.

    The nine-month window and the quarter share `2022-09-30`, and the readings differ in sign
    on `adjusted_ebitda`. The conflation gets its own code because that is the finding a writer
    can act on.
    """
    ytd = make_fact(observation_id="obs:adjusted-gross-margin:opendoor:2022-01-01_2022-09-30:"
                                   "normalized-table:0d5e19b7ca23",
                    period_key="2022-01-01_2022-09-30", period_start="2022-01-01",
                    period_end="2022-09-30", value=3.3)
    package = make_package(facts=(ytd,))
    sentence = agm_sentence(fact_bindings=(_binding(AGM_TEXT, "3.3%",
                                                    fact_id=ytd.observation_id),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), package, make_plan(required_warnings=()))
    assert "period_shape_conflated" in codes_of(verified)


def test_an_unresolvable_period_surface_is_refused_with_its_own_code(verifier):
    sentence = agm_sentence(
        fact_bindings=(_binding(AGM_TEXT, "3.3%", period_surface="the quarter"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "period_unresolvable" in codes_of(verified)


# ---------------------------------------------------------------------------------------
# §13.5 — metric identity
# ---------------------------------------------------------------------------------------


def test_longest_match_wins_so_adjusted_gross_margin_never_resolves_to_the_gaap_one():
    """§13.5: `"gross margin" ⊂ "adjusted gross margin"`, and a first-match scanner is wrong."""
    index = MetricAliasIndex.from_package(make_package())
    assert index.resolve("Adjusted gross margin").metric_ids == ("adjusted_gross_margin",)
    assert index.resolve("GAAP gross margin").metric_ids == ("gaap_gross_margin",)


def test_the_bare_surface_gross_margin_is_refused_as_declared_ambiguous(verifier):
    """§13.5: `gaap_gross_margin`'s own label *is* "Gross Margin", so a post must say GAAP."""
    sentence = agm_sentence(
        fact_bindings=(_binding(AGM_TEXT, "3.3%", metric_surface="gross margin"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "metric_surface_ambiguous" in codes_of(verified)


# ---------------------------------------------------------------------------------------
# §13.6 / §13.11 — subject
# ---------------------------------------------------------------------------------------


def test_naming_any_subject_other_than_opendoor_is_an_automatic_refusal(verifier):
    """§13.6: `mortgage_rate` and `home_price_appreciation` are declared and empty."""
    text = "Adjusted gross margin was 3.3% in the third quarter of 2022, ahead of Offerpad."
    sentence = agm_sentence(text=text, fact_bindings=(_binding(text, "3.3%"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "foreign_subject_named" in codes_of(verified)


def test_an_unresolved_borrower_may_only_appear_as_its_own_entity_text(verifier):
    """§13.11 and §17's attack 10: the borrower is a subsidiary and the graph will not guess."""
    event = PackagedEvent(
        event_id="evt:credit-facility-established:2022-10-19:9f31c2a70b4d",
        event_type_id="credit_facility_established", occurred_on="2022-10-19",
        passage_id=PASSAGE_ID,
        participants=(EventParticipant(
            entity_id="opendoor_unnamed_subsidiary#evt:credit-facility-established:2022-10-19",
            role="borrower", entity_text="a subsidiary of the Company", resolved=False),),
    )
    text = "In October 2022, Opendoor entered a $525 million facility."
    sentence = DraftSentence(
        index=0, text=text, kind=SentenceKind.REPORTED,
        citations=(PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=0, char_end=20),),
    )
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(events=(event,)),
        make_plan(required_warnings=()))
    assert "unresolved_entity_named" in codes_of(verified)


# ---------------------------------------------------------------------------------------
# §13.7 — citation support
# ---------------------------------------------------------------------------------------


def test_a_table_quote_that_does_not_reconstruct_to_the_value_is_refused(verifier):
    """§13.7 Rule A step 2, which holds on 2,690/2,690 table observations."""
    # "3,394" occurs verbatim in the passage, so Rule A step 1 passes and step 2 is what
    # refuses: a quote that reconstructs to 3394.0 is a citation to a different cell.
    package = make_package(facts=(make_fact(quoted_text="3,394"), GGM_FACT))
    verified = verifier.verify(
        make_draft(sentences=(agm_sentence(),)), package, make_plan(required_warnings=()))
    assert "table_quote_does_not_reconstruct" in codes_of(verified)


def test_a_column_label_naming_two_periods_inside_one_passage_is_refused(verifier):
    """§13.7.1: 179 of 485 pairs are ambiguous, covering 61.3% of observations.

    The concrete failure is the first row of `observations.jsonl` — a `"2020"` column carrying
    both a half-year and the quarter inside it.
    """
    half_year = make_fact(
        observation_id="obs:adjusted-gross-margin:opendoor:2022-01-01_2022-06-30:"
                       "normalized-table:31c0ba5d7e92",
        period_key="2022-01-01_2022-06-30", period_start="2022-01-01",
        period_end="2022-06-30", value=9.9, quoted_text="9.9", column_label="2022")
    quarter = make_fact(column_label="2022")
    package = make_package(facts=(quarter, half_year))
    verified = verifier.verify(
        make_draft(sentences=(agm_sentence(),)), package, make_plan(required_warnings=()))
    ambiguous = [f for f in verified.all_findings
                 if f.code == "column_label_ambiguous_in_passage"]
    assert ambiguous
    assert ambiguous[0].remedy is Remedy.REBIND_TO_DISTINGUISHING_COLUMN
    assert ambiguous[0].observed == "2022-01-01_2022-06-30, 2022Q3"


def test_a_d15_classified_column_proceeds_and_is_annotated_rather_than_refused(verifier):
    """§13.7.1 hatch 2, which is the hatch that carries the load — hatch 1 rescues 1.3%."""
    half_year = make_fact(
        observation_id="obs:adjusted-gross-margin:opendoor:2022-01-01_2022-06-30:"
                       "normalized-table:31c0ba5d7e92",
        period_key="2022-01-01_2022-06-30", period_start="2022-01-01",
        period_end="2022-06-30", value=9.9, quoted_text="9.9", column_label="2022")
    quarter = make_fact(column_label="2022",
                        ambiguity_codes=("year_only_column_ambiguity",))
    verified = verifier.verify(
        make_draft(sentences=(agm_sentence(),)), make_package(facts=(quarter, half_year)),
        make_plan(required_warnings=()))
    assert "column_label_ambiguous_in_passage" not in codes_of(verified)
    annotated = [f for f in verified.all_findings
                 if f.code == "column_label_ambiguity_classified"]
    assert annotated and annotated[0].severity is Severity.ANNOTATE
    assert annotated[0].blocking is False


def test_document_grain_counter_evidence_may_never_be_cited_as_passage_level_support(verifier):
    """§10's counter-evidence join is at document grain; §13.14 is what waits for the misuse."""
    other = PackagedPassage(
        passage_id="psg:opendoor-10q-2022q3:inventory-table", document_id=DOCUMENT_ID,
        text="Inventory 2,152", char_count=15, excerpted=True)
    package = make_package(counter_evidence=(other,))
    sentence = agm_sentence(citations=(PassageCitation(
        passage_id=other.passage_id, document_id=DOCUMENT_ID, char_start=0, char_end=15),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), package, make_plan(required_warnings=()))
    assert "counter_evidence_cited_as_support" in codes_of(verified)


def test_a_citation_to_evidence_that_names_no_filed_passage_is_unavailable_not_silent(verifier):
    """§13.7.2 Rule C: zero such nodes exist, and the refusal keeps the hole visible."""
    from story.core.models import EvidenceSourceCitation

    sentence = agm_sentence(citations=(EvidenceSourceCitation(
        evidence_source_id="xbrl:us-gaap:GrossProfit:0001-22Q3", evidence_kind="xbrl_fact"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "evidence_kind_not_supported_in_v1" in codes_of(verified)


def test_a_citation_reused_for_a_claim_the_passage_does_not_evidence_is_refused(verifier):
    """§13.7: reuse *and* non-support, so a legitimate second cite of one table still passes."""
    span = _span("Adjusted Gross Margin 3.3")
    reuse = DraftSentence(
        index=1, text="Housing inventory fell over the same period.",
        kind=SentenceKind.EXPLANATORY,
        citations=(PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=span[0], char_end=span[1]),),
    )
    verified = verifier.verify(
        make_draft(sentences=(agm_sentence(), reuse)), make_package(),
        make_plan(required_warnings=()))
    assert "citation_reused_for_unrelated_claim" in codes_of(verified)


# ---------------------------------------------------------------------------------------
# §13.9 — reported versus calculated
# ---------------------------------------------------------------------------------------


def test_a_calculated_sentence_may_not_carry_a_passage_citation(verifier):
    """§13.9: `claims.yaml` gives `calculated` `optional_fields: []`."""
    sentence = gap_sentence(citations=(PassageCitation(
        passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, char_start=0, char_end=10),))
    verified = verifier.verify(
        make_draft(sentences=(agm_sentence(), ggm_sentence(), sentence)),
        make_package(), make_plan(required_warnings=()))
    assert "calculated_sentence_cites_passage" in codes_of(verified)


def test_a_derived_difference_that_does_not_reproduce_from_its_inputs_is_refused(verifier):
    """Compared after rounding to the draft's own precision, never by equality."""
    text = "The gap between the two measures was 12.9 percentage points."
    sentence = gap_sentence(
        index=0, text=text,
        calculation=Calculation(
            operation="delta_pp", input_observation_ids=(GGM_ID, AGM_ID),
            expression="adjusted_gross_margin - gaap_gross_margin",
            result_rendered="12.9 percentage points"))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings if f.code == "calculation_does_not_recompute"]
    assert failure and failure[0].expected == "15.9"


def test_a_cross_metric_gap_is_recomputable_even_though_its_inputs_name_two_metrics(verifier):
    """§13.9 asks for inputs *"sharing metric and unit"* and that clause refuses this demo.

    `cross-metric-divergence` subtracts `gaap_gross_margin` from `adjusted_gross_margin`; the
    metrics differing is the story. Unit and period shape are required instead, and the
    omission is recorded in `deterministic.py` rather than made silently.
    """
    verified = verifier.verify(make_draft(), make_package(), make_plan())
    assert "calculation_inputs_incomparable" not in codes_of(verified)
    assert verified.check("reported_vs_calculated").outcome is CheckOutcome.PASS


def test_a_calculation_over_inputs_of_different_shapes_is_incomparable(verifier):
    """§13.4: a calculation whose inputs have different shapes is `incomparable_periods`."""
    ytd = make_fact(observation_id="obs:gaap-gross-margin:opendoor:2022-01-01_2022-09-30:"
                                   "normalized-table:6ab2091fd374",
                    metric_id="gaap_gross_margin", metric_label="Gross Margin",
                    period_key="2022-01-01_2022-09-30", period_start="2022-01-01",
                    period_end="2022-09-30", value=-12.6,
                    row_label="Gross Margin", quoted_text="(12.6)")
    package = make_package(facts=(make_fact(), ytd))
    sentence = gap_sentence(index=0, calculation=Calculation(
        operation="delta_pp", input_observation_ids=(ytd.observation_id, AGM_ID),
        expression="adjusted_gross_margin - gaap_gross_margin",
        result_rendered="15.9 percentage points"))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), package, make_plan(required_warnings=()))
    assert "incomparable_periods" in codes_of(verified)


# ---------------------------------------------------------------------------------------
# §13.10 — causation
# ---------------------------------------------------------------------------------------


def test_a_span_carrying_two_causal_markers_is_refused_because_the_binding_cannot_choose(
        verifier):
    """§13.10 condition 6, in the only form the S0 draft contract can express.

    367 passages carry two or more markers. `DraftSentence` has no marker-occurrence selector,
    so co-presence would license *"the company attributed Z to Y"* over a span asserting
    *"X rose due to Y"* and *"Z fell as a result of W"*.
    """
    passage_text = ("The Company said gross margin declined due to home price depreciation. "
                    "Contribution profit fell as a result of holding costs.")
    package = make_package(primary_passages=(PackagedPassage(
        passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, text=passage_text,
        char_count=len(passage_text)),))
    text = ("The company said gross margin declined due to home price depreciation.")
    sentence = DraftSentence(
        index=0, text=text, kind=SentenceKind.EXPLANATORY,
        citations=(PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=0, char_end=len(passage_text)),),
    )
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), package, make_plan(required_warnings=()))
    assert "causal_marker_ambiguous_in_span" in codes_of(verified)


def test_a_negated_causal_construction_in_the_span_is_refused(verifier):
    """§13.10 condition 5: 37 passages carry one, and conditions 1–4 all pass on them."""
    passage_text = ("The Purchaser decided to enter into this Agreement not as a result of any "
                    "general solicitation.")
    package = make_package(primary_passages=(PackagedPassage(
        passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, text=passage_text,
        char_count=len(passage_text)),))
    text = "The company said the agreement was entered into as a result of a solicitation."
    sentence = DraftSentence(
        index=0, text=text, kind=SentenceKind.EXPLANATORY,
        citations=(PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=0, char_end=len(passage_text)),),
    )
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), package, make_plan(required_warnings=()))
    assert "causal_marker_negated_in_span" in codes_of(verified)


def test_forward_looking_language_is_an_unconditional_refusal_today(verifier):
    """§13.15: all 2,704 observations are `reported` and no lane emits `guidance_issuance`."""
    text = "Adjusted gross margin was 3.3% in the third quarter of 2022, and management expects more."
    sentence = agm_sentence(text=text, fact_bindings=(_binding(text, "3.3%"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "forward_looking_language" in codes_of(verified)


# ---------------------------------------------------------------------------------------
# §13.12 / §13.13 — disclosures a bound fact drags with it
# ---------------------------------------------------------------------------------------


def test_a_warned_observation_is_annotated_and_does_not_block(verifier):
    """§13.13: any binding to one of the 185 `:Warned` observations must surface the warning."""
    package = make_package(facts=(make_fact(warning_codes=("value_outside_expected_range",)),
                                  GGM_FACT))
    verified = verifier.verify(
        make_draft(sentences=(agm_sentence(),)), package, make_plan(required_warnings=()))
    annotated = [f for f in verified.all_findings if f.code == "warned_observation_used"]
    assert annotated and annotated[0].severity is Severity.ANNOTATE
    assert verified.passed is True


def test_a_material_conflict_the_sentence_does_not_disclose_is_refused(verifier):
    """§13.12's precision-relative rule, derived from the package and never from a constant.

    27-versus-44 separates at one significant figure — `market_count 2021-03-31` on the stale
    graph — and could never be used silently.
    """
    conflict = Conflict(
        slot="adjusted_gross_margin|2022Q3",
        clusters=(ConflictCluster(value=3.3, observation_ids=(AGM_ID,)),
                  ConflictCluster(value=6.1, observation_ids=("obs:other",))),
        classification="material", resolution_rule="document_majority")
    package = make_package(conflicts=(conflict,))
    verified = verifier.verify(
        make_draft(sentences=(agm_sentence(),)), package, make_plan(required_warnings=()))
    assert "conflict_not_disclosed" in codes_of(verified)


def test_a_conflict_that_vanishes_at_the_drafts_own_precision_is_annotated_not_refused(verifier):
    """All 36 conflicted slots on the current run are rounding twins."""
    conflict = Conflict(
        slot="adjusted_gross_margin|2022Q3",
        clusters=(ConflictCluster(value=3.3, observation_ids=(AGM_ID,)),
                  ConflictCluster(value=3.34, observation_ids=("obs:other",))),
        classification="rounding", resolution_rule="document_majority")
    verified = verifier.verify(
        make_draft(sentences=(agm_sentence(),)), make_package(conflicts=(conflict,)),
        make_plan(required_warnings=()))
    assert "conflict_immaterial_at_stated_precision" in codes_of(verified)
    assert verified.passed is True


def test_a_counterpoint_the_plan_required_and_the_draft_dropped_is_refused(verifier):
    """§17.14: nothing in the first draft required a counterpoint to survive into the post."""
    plan = make_plan(
        required_warnings=(),
        counterpoints=(Counterpoint(
            claim="GAAP gross margin was negative in the same quarter.",
            required_fact_ids=(GGM_ID,)),))
    verified = verifier.verify(
        make_draft(sentences=(agm_sentence(),)), make_package(), plan)
    assert "required_counterpoint_absent" in codes_of(verified)


def test_a_required_warning_whose_code_declares_no_qualifier_refuses_rather_than_passing(
        verifier):
    """Silence must never satisfy a disclosure."""
    verified = verifier.verify(
        make_draft(), make_package(), make_plan(required_warnings=("some_new_code",)))
    assert "required_warning_has_no_declared_qualifier" in codes_of(verified)


# ---------------------------------------------------------------------------------------
# The ten malicious drafts. Each names the check that caught it.
# ---------------------------------------------------------------------------------------


def test_malicious_right_number_wrong_metric_is_caught_by_metric_binding_mismatch(verifier):
    """§17's attack 1: both margins read 15.4 at 2020Q4, 13.0 at 2021Q1 and 8.4 at FY2024."""
    sentence = agm_sentence(
        fact_bindings=(_binding(AGM_TEXT, "3.3%", metric_surface="GAAP gross margin"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "metric_binding_mismatch" in codes_of(verified)
    assert verified.passed is False


def test_malicious_right_number_wrong_period_is_caught_by_period_mismatch(verifier):
    """§13.4: exact equality on both endpoints and on kind, never on `period_end` alone."""
    sentence = agm_sentence(fact_bindings=(_binding(
        AGM_TEXT, "3.3%", period_surface="the second quarter of 2022"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "period_mismatch" in codes_of(verified)
    assert verified.passed is False


def test_malicious_swapped_margin_values_are_caught_by_tolerance_and_by_sign(verifier):
    """The two margins swapped: 3.3 written where −12.6 belongs and the reverse."""
    swapped_agm_text = "Adjusted gross margin was -12.6% in the third quarter of 2022."
    swapped_ggm_text = "GAAP gross margin was 3.3% in the third quarter of 2022."
    sentences = (
        agm_sentence(text=swapped_agm_text,
                     fact_bindings=(_binding(swapped_agm_text, "-12.6%"),)),
        ggm_sentence(text=swapped_ggm_text,
                     fact_bindings=(_binding(swapped_ggm_text, "3.3%", fact_id=GGM_ID,
                                             metric_surface="GAAP gross margin"),)),
    )
    verified = verifier.verify(
        make_draft(sentences=sentences), make_package(), make_plan(required_warnings=()))
    assert "number_outside_tolerance" in codes_of(verified)
    assert "sign_disagreement" in codes_of(verified)
    assert verified.passed is False


def test_malicious_percentage_where_percentage_points_are_meant_is_caught_by_the_surface_gate(
        verifier):
    """§13.3: the two readings differ by `100/|v1|`, which is base-dependent."""
    text = "The gap between the two measures was 15.9%."
    sentence = gap_sentence(index=0, text=text, calculation=Calculation(
        operation="delta_pp", input_observation_ids=(GGM_ID, AGM_ID),
        expression="adjusted_gross_margin - gaap_gross_margin",
        result_rendered="15.9%"))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "percentage_point_surface_missing" in codes_of(verified)
    assert verified.passed is False


def test_malicious_unsupported_because_is_caught_by_the_causation_ban(verifier):
    """§13.10 A: LLM-originated causation is banned unconditionally."""
    text = ("Adjusted gross margin was 3.3% in the third quarter of 2022 because the "
            "adjustment excluded inventory writedowns.")
    sentence = agm_sentence(text=text, fact_bindings=(_binding(text, "3.3%"),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "causal_construction_forbidden" in codes_of(verified)
    assert verified.passed is False


def test_malicious_only_negative_margin_ever_is_caught_without_carrying_a_numeral(verifier):
    """§13.14's headline attack: it is false, and it carries no number to check.

    `adjusted_gross_margin` is negative in **2022Q4 (−3.2) and 2023Q1 (−3.3)**. The same
    sentence about *GAAP* gross margin is true (2022Q3 only), so the form is unfalsifiable by
    inspection.
    """
    sentence = DraftSentence(
        index=0, kind=SentenceKind.CONNECTIVE,
        text=("That was the only quarter in which the company reported a negative adjusted "
              "gross margin."))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "unsupported_superlative" in codes_of(verified)
    assert verified.passed is False


def test_the_same_uniqueness_claim_dressed_as_an_extremum_dies_on_recomputation(verifier):
    """§13.14: the superlative's machinery is the comparison set, and the set refuses it."""
    negatives = (
        make_fact(),
        make_fact(observation_id=AGM_Q4_ID, period_key="2022Q4", period_start="2022-10-01",
                  period_end="2022-12-31", value=-3.2, quoted_text="(3.2)"),
        make_fact(observation_id=AGM_2023Q1_ID, period_key="2023Q1", period_start="2023-01-01",
                  period_end="2023-03-31", value=-3.3, quoted_text="(3.3)"),
    )
    sentence = DraftSentence(
        index=0, kind=SentenceKind.CALCULATED,
        text="It was the only quarter with a negative adjusted gross margin.",
        calculation=Calculation(
            operation="extremum",
            input_observation_ids=(AGM_ID, AGM_Q4_ID, AGM_2023Q1_ID),
            expression="unique_negative", result_rendered="one quarter"),
    )
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(facts=negatives),
        make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings if f.code == "extremum_recomputation_failed"]
    assert failure and failure[0].observed.startswith("2 of 3")


def test_malicious_citation_to_an_unrelated_passage_is_caught_by_citation_support(verifier):
    other = PackagedPassage(
        passage_id="psg:opendoor-10q-2022q3:liquidity", document_id=DOCUMENT_ID,
        text="We had $1.4 billion of unrestricted cash.", char_count=41)
    package = make_package(
        primary_passages=(make_package().primary_passages[0], other))
    sentence = agm_sentence(citations=(PassageCitation(
        passage_id=other.passage_id, document_id=DOCUMENT_ID, char_start=0, char_end=41),))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), package, make_plan(required_warnings=()))
    assert "citation_does_not_support_fact" in codes_of(verified)
    assert verified.passed is False


def test_malicious_invented_connective_sentence_is_caught_by_the_no_claim_rule(verifier):
    """§13.14: a `connective` sentence may contain no claim — only transition and reference."""
    sentence = DraftSentence(
        index=0, kind=SentenceKind.CONNECTIVE,
        text="The two margins had been within 2 points of each other for eight quarters.")
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "connective_sentence_carries_a_claim" in codes_of(verified)
    assert "unbound_numeral" in codes_of(verified)
    assert verified.passed is False


def test_malicious_omission_of_a_required_warning_is_caught_by_the_qualifier_check(verifier):
    """§12: the writer must not omit a `required_warning`."""
    draft = make_draft(sentences=(agm_sentence(), ggm_sentence(), gap_sentence()))
    verified = verifier.verify(draft, make_package(), make_plan())
    absent = [f for f in verified.all_findings if f.code == "required_warning_absent"]
    assert absent and absent[0].observed == "filing_date_unknown"
    assert verified.passed is False


def test_malicious_stale_package_hash_is_caught_because_the_digest_is_recomputed(verifier):
    """§10.3 and S0's F6: the digest is taken over the package *minus* the field holding it.

    §17's attack 8 is the live version — a real node, a real quote and a real document from a
    superseded run, where every check in §13.1–§13.12 passes.
    """
    stale = make_package().model_copy(update={"package_content_digest": "0" * 64})
    verified = verifier.verify(make_draft(), stale, make_plan())
    mismatch = [f for f in verified.all_findings
                if f.code == "package_content_digest_mismatch"]
    assert mismatch and mismatch[0].remedy is Remedy.REBUILD_PACKAGE
    assert verified.passed is False


def test_a_package_from_a_superseded_graph_run_is_refused_not_repaired(verifier):
    """§13.13: `graph-v1-886059d862ce` survives in the plan's history, and must never verify."""
    stale = make_package(graph_run_id="graph-v1-886059d862ce")
    verified = verifier.verify(make_draft(), stale, make_plan())
    assert "graph_run_id_mismatch" in codes_of(verified)


def test_a_verifier_given_no_expected_run_does_not_report_a_freshness_check_it_never_ran():
    """The denominator again: an expectation nobody supplied is not counted as satisfied."""
    with_expectation = DeterministicVerifier(graph_run_id=GRAPH_RUN_ID).verify(
        make_draft(), make_package(), make_plan())
    without = DeterministicVerifier().verify(make_draft(), make_package(), make_plan())
    assert (with_expectation.check("identity_and_freshness").examined
            == without.check("identity_and_freshness").examined + 1)


def test_a_superlative_smuggled_into_the_title_is_refused_because_nothing_can_bind_it(verifier):
    """§12 gives bindings, calculations and citations to *sentences*; the title has none.

    Every check that reaches a claim through a binding skips the title entirely, so a headline
    is the cheapest place for an unsupported claim to survive into a published post.
    """
    draft = make_draft(title="Opendoor's worst quarter ever, in 2 charts")
    verified = verifier.verify(draft, make_package(), make_plan())
    title = verified.check("title")
    assert title.examined == 1
    assert {f.code for f in title.findings} == {"unsupported_superlative", "unbound_numeral"}
    assert verified.passed is False


def test_a_draft_with_no_title_reports_the_title_check_as_not_applicable(verifier):
    verified = verifier.verify(make_draft(title=""), make_package(), make_plan())
    assert verified.check("title").outcome is CheckOutcome.NOT_APPLICABLE
    assert verified.passed is True
