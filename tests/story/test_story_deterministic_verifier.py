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
from pydantic import ValidationError

from story.core.keys import package_content_digest
from story.core.table_cells import resolve_cell, resolve_header
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
    EvidenceRole,
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
    TableCellRef,
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
from story.stages.verification.period_grammar import scan as scan_periods
from story.stages.packaging.counter_evidence import MATCH_BASIS_SAME_DOCUMENT

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

#: Read off the rows, never spelled — the rule `AGM_HANDLE` states for the grid fixtures below.
#: Both facts here carry `cell=None`, so these are §3.1's narrative `…:span:…` form.
#:
#: **Every citation in this file states one, because `PassageCitation.evidence_handle` is
#: required with no default.** It was optional until the adversarial review of S7, and the
#: fixtures here took the default — which meant the drafts they build ran §13.7's older tests
#: and none of §3.4's. On a table fact there is nothing underneath: Rule A step 1 asks only that
#: the package's own `quoted_text` occur somewhere in the passage, which is true of every
#: citation into that passage whatever bytes it names.
AGM_SPAN_HANDLE = make_fact().evidence_handle
GGM_SPAN_HANDLE = GGM_FACT.evidence_handle

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
                            char_count=len(PASSAGE_TEXT), passage_kind="normalized_table",
                            role=EvidenceRole.PRIMARY_SUPPORT),
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
                                   char_end=_span("Adjusted Gross Margin 3.3")[1],
                                   evidence_handle=AGM_SPAN_HANDLE),),
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
                                   char_end=_span("Gross Margin (12.6)")[1],
                                   evidence_handle=GGM_SPAN_HANDLE),),
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
            text="Adjusted EBITDA (27,075)", char_count=24,
            role=EvidenceRole.PRIMARY_SUPPORT),),
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
                                   char_start=0, char_end=24,
                                   evidence_handle=fact.evidence_handle),),
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
                                   char_start=0, char_end=20,
                                   evidence_handle=AGM_SPAN_HANDLE),),
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
        text="Inventory 2,152", char_count=15, excerpted=True,
        role=EvidenceRole.COUNTER_EVIDENCE, match_basis=MATCH_BASIS_SAME_DOCUMENT)
    package = make_package(counter_evidence=(other,))
    sentence = agm_sentence(citations=(PassageCitation(
        passage_id=other.passage_id, document_id=DOCUMENT_ID, char_start=0, char_end=15,
        evidence_handle=AGM_SPAN_HANDLE),))
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
                                   char_start=span[0], char_end=span[1],
                                   evidence_handle=AGM_SPAN_HANDLE),),
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
        passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, char_start=0, char_end=10,
        evidence_handle=AGM_SPAN_HANDLE),))
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
# §13.4 + §13.9 — the period a derivation computes over
# ---------------------------------------------------------------------------------------

#: The sentence the demo's own writer produced, which was undeclarable before
#: `Calculation.period_surface` existed: a `calculated` sentence carries no binding, so the
#: words *"the third quarter of 2022"* were an `unbound_numeral` on `2022` by construction.
DATED_GAP_TEXT = ("The GAAP gross margin was 15.9 percentage points lower than the adjusted "
                  "gross margin in the third quarter of 2022.")


def dated_gap_sentence(**overrides: object) -> DraftSentence:
    fields: dict[str, object] = dict(
        index=0, text=DATED_GAP_TEXT, kind=SentenceKind.CALCULATED,
        calculation=Calculation(
            operation="compare_levels",
            input_observation_ids=(GGM_ID, AGM_ID),
            expression="left < right",
            result_rendered="15.9 percentage points",
            period_surface="the third quarter of 2022"),
    )
    fields.update(overrides)
    return DraftSentence(**fields)  # type: ignore[arg-type]


def test_a_calculated_sentence_may_declare_the_period_it_computed_over(verifier):
    """§13.1 says a period surface is not a fact, and §13.9 gives the sentence no binding.

    Before `Calculation.period_surface` the two rules could not both be satisfied: a derivation
    naming its own quarter was refused on the year inside the period words. Here the same
    sentence is accepted, and nothing else about it changed.
    """
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(),)), make_package(),
        make_plan(required_warnings=()))
    assert verified.all_findings == ()
    assert verified.check("periods").examined == 1


def test_a_derivation_that_names_a_period_its_inputs_were_not_read_over_is_refused(verifier):
    """The declaration is checked, not trusted — the whole reason it is the writer's to make.

    Both inputs are 2022Q3 and the sentence says the fourth quarter. §13.4 compares both
    endpoints, so this is `period_mismatch` and the draft is refused.
    """
    text = DATED_GAP_TEXT.replace("third quarter", "fourth quarter")
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(
            text=text,
            calculation=Calculation(
                operation="compare_levels", input_observation_ids=(GGM_ID, AGM_ID),
                expression="left < right", result_rendered="15.9 percentage points",
                period_surface="the fourth quarter of 2022")),)),
        make_package(), make_plan(required_warnings=()))
    mismatch = [f for f in verified.all_findings if f.code == "period_mismatch"]
    assert mismatch and mismatch[0].observed.startswith("'the fourth quarter of 2022'")
    assert verified.passed is False


def test_a_derivation_whose_period_surface_is_outside_the_grammar_is_refused(verifier):
    """A bare year is not in §13.4's grammar wherever it is declared, binding or calculation."""
    text = "The GAAP gross margin was 15.9 percentage points below the adjusted one in 2022."
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(
            text=text,
            calculation=Calculation(
                operation="difference", input_observation_ids=(GGM_ID, AGM_ID),
                expression="adjusted_gross_margin - gaap_gross_margin",
                result_rendered="15.9 percentage points", period_surface="2022")),)),
        make_package(), make_plan(required_warnings=()))
    assert "period_unresolvable" in codes_of(verified)
    assert verified.passed is False


# ---------------------------------------------------------------------------------------
# §13.14 — a comparative, which V1 could declare in no way at all
# ---------------------------------------------------------------------------------------


def test_a_comparative_backed_by_compare_levels_is_accepted(verifier):
    """§13.14 requires `compare_levels` or `compare_deltas`; the writer's enum held neither.

    −12.6 is 15.9 points below 3.3, the sentence says so, and the calculation declares the two
    sides in the order the sentence names them.
    """
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(),)), make_package(),
        make_plan(required_warnings=()))
    assert "unsupported_comparative" not in codes_of(verified)
    assert verified.passed is True


def test_a_comparative_whose_gap_does_not_recompute_is_refused(verifier):
    """The size is §13.9's, and a comparison that renders one is recomputed like any other."""
    text = DATED_GAP_TEXT.replace("15.9", "12.9")
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(
            text=text,
            calculation=Calculation(
                operation="compare_levels", input_observation_ids=(GGM_ID, AGM_ID),
                expression="left < right", result_rendered="12.9 percentage points",
                period_surface="the third quarter of 2022")),)),
        make_package(), make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings if f.code == "calculation_does_not_recompute"]
    assert failure and failure[0].expected == "15.9"
    assert verified.passed is False


def test_a_comparative_the_values_contradict_is_refused(verifier):
    """The direction is §13.14's: −12.6 is not above 3.3, whatever the expression declares."""
    text = DATED_GAP_TEXT.replace("lower", "higher")
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(
            text=text,
            calculation=Calculation(
                operation="compare_levels", input_observation_ids=(GGM_ID, AGM_ID),
                expression="left > right", result_rendered="15.9 percentage points",
                period_surface="the third quarter of 2022")),)),
        make_package(), make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "comparative_recomputation_failed"]
    assert failure and failure[0].observed == "left=-12.6, right=3.3"
    assert verified.passed is False


def test_a_comparative_whose_sentence_reverses_its_own_calculation_is_refused(verifier):
    """**The attack the recomputation alone would have accepted.**

    The declaration is `left < right` over `(gaap, adjusted)` and recomputes: −12.6 < 3.3. The
    sentence says the *adjusted* margin was the lower one, which is false by 15.9 points. Only
    a check that reads the sentence's own words against the declared sides catches it, which is
    what `comparative_not_supported_by_text` is.
    """
    text = ("The adjusted gross margin was 15.9 percentage points lower than the GAAP gross "
            "margin in the third quarter of 2022.")
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(text=text),)), make_package(),
        make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "comparative_not_supported_by_text"]
    assert failure
    assert failure[0].expected.startswith("gaap_gross_margin before 'lower'")
    assert verified.passed is False


def test_a_comparative_whose_word_points_the_other_way_is_refused(verifier):
    """`left < right` recomputes and the sentence says `higher`; the words decide."""
    text = DATED_GAP_TEXT.replace("lower", "higher")
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(text=text),)), make_package(),
        make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "comparative_not_supported_by_text"]
    assert failure and failure[0].observed == "higher"
    assert verified.passed is False


def test_a_second_comparative_in_a_licensed_sentence_is_refused(verifier):
    """One calculation supports one comparison; the second is a claim nothing declared."""
    text = ("The GAAP gross margin was 15.9 percentage points lower than the adjusted gross "
            "margin, and worse in the third quarter of 2022.")
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(text=text),)), make_package(),
        make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "comparative_not_supported_by_text"]
    assert failure and failure[0].observed == "lower, worse"
    assert verified.passed is False


def test_a_comparison_of_one_metric_against_itself_is_refused_as_unorderable(verifier):
    """Two sides of one metric are two periods, and nothing here can tell them apart.

    The metric names are the only reading of the sentence this check has, so a same-metric
    comparison could be reversed word for word and still line up with its declaration. Refused
    rather than half-checked — it was refused outright before `compare_levels` was declarable,
    so no true sentence loses ground.
    """
    q4 = make_fact(observation_id=AGM_Q4_ID, period_key="2022Q4", period_start="2022-10-01",
                   period_end="2022-12-31", value=-3.2, quoted_text="(3.2)")
    text = ("The adjusted gross margin was 6.5 percentage points lower than the adjusted gross "
            "margin.")
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(
            text=text,
            calculation=Calculation(
                operation="compare_levels",
                input_observation_ids=(q4.observation_id, AGM_ID),
                expression="left < right", result_rendered="6.5 percentage points")),)),
        make_package(facts=(make_fact(), q4)), make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "comparative_not_supported_by_text"]
    assert failure and failure[0].observed.startswith("both sides are adjusted_gross_margin")
    assert verified.passed is False


def test_a_comparison_of_two_units_is_refused_before_its_direction_is_considered(verifier):
    """§13.9's comparability applies to a comparison too: a gap needs one unit.

    `compare_levels` was exempt from the unit check while it was undeclarable. It is not now —
    the gap it renders is a number in some unit, and a percent-against-dollars gap is in none.
    """
    homes = make_fact(observation_id="obs:homes-sold:opendoor:2022Q3:normalized-table:aa11bb22",
                      metric_id="homes_sold", metric_label="Homes Sold", value=8520,
                      unit="homes", row_label="Homes Sold", quoted_text="8,520")
    metrics = (*METRICS, PackagedMetric(metric_id="homes_sold", label="Homes Sold",
                                        unit="homes", allowed_units=("homes",)))
    package = make_package(facts=(make_fact(), homes), metrics=metrics)
    text = ("Homes sold was 8,516.7 percentage points higher than the adjusted gross margin in "
            "the third quarter of 2022.")
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(
            text=text,
            calculation=Calculation(
                operation="compare_levels",
                input_observation_ids=(AGM_ID, homes.observation_id),
                expression="left < right", result_rendered="8,516.7 percentage points",
                period_surface="the third quarter of 2022")),)),
        package, make_plan(required_warnings=()))
    assert "calculation_inputs_incomparable" in codes_of(verified)
    assert verified.passed is False


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
        char_count=len(passage_text), role=EvidenceRole.PRIMARY_SUPPORT),))
    text = ("The company said gross margin declined due to home price depreciation.")
    sentence = DraftSentence(
        index=0, text=text, kind=SentenceKind.EXPLANATORY,
        citations=(PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=0, char_end=len(passage_text),
                                   evidence_handle=AGM_SPAN_HANDLE),),
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
        char_count=len(passage_text), role=EvidenceRole.PRIMARY_SUPPORT),))
    text = "The company said the agreement was entered into as a result of a solicitation."
    sentence = DraftSentence(
        index=0, text=text, kind=SentenceKind.EXPLANATORY,
        citations=(PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=0, char_end=len(passage_text),
                                   evidence_handle=AGM_SPAN_HANDLE),),
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
        text="We had $1.4 billion of unrestricted cash.", char_count=41,
        role=EvidenceRole.PRIMARY_SUPPORT)
    package = make_package(
        primary_passages=(make_package().primary_passages[0], other))
    sentence = agm_sentence(citations=(PassageCitation(
        passage_id=other.passage_id, document_id=DOCUMENT_ID, char_start=0, char_end=41,
        evidence_handle=AGM_SPAN_HANDLE),))
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


def test_a_title_may_name_the_period_the_package_is_about(verifier):
    """A period key is not a claim, and it was the only thing D5's title rule refused wrongly.

    Every fact in this package carries `period_key: 2022Q3`, and the title naming it was refused
    twice over — once on `2022` and once on `3`. The span admitted is an exact occurrence of a
    string the package itself minted, so no value, metric or comparison can hide inside it.
    """
    verified = verifier.verify(
        make_draft(title="Two gross margins in 2022Q3"), make_package(), make_plan())
    assert verified.check("title").findings == ()
    assert verified.passed is True


def test_a_title_naming_a_period_the_package_does_not_hold_is_still_unbound(verifier):
    """*"This package's own periods"* is the whole licence: 2021Q4 is not one of them."""
    verified = verifier.verify(
        make_draft(title="Two gross margins in 2021Q4"), make_package(), make_plan())
    unbound = [f for f in verified.check("title").findings if f.code == "unbound_numeral"]
    assert [f.observed for f in unbound] == ["2021", "4"]
    assert verified.passed is False


def test_a_numeral_beside_a_licensed_period_key_in_a_title_is_still_refused(verifier):
    """The exemption is the key's own characters and not the title it sits in."""
    verified = verifier.verify(
        make_draft(title="2022Q3 in 2 charts"), make_package(), make_plan())
    unbound = [f for f in verified.check("title").findings if f.code == "unbound_numeral"]
    assert [f.observed for f in unbound] == ["2"]
    assert verified.passed is False


def test_a_draft_with_no_title_reports_the_title_check_as_not_applicable(verifier):
    verified = verifier.verify(make_draft(title=""), make_package(), make_plan())
    assert verified.check("title").outcome is CheckOutcome.NOT_APPLICABLE
    assert verified.passed is True


# ---------------------------------------------------------------------------------------
# R8 — the defect class: the verifier checked declarations and never read the prose
#
# Every attack below was **measured passing** against the demo's own accepted draft before the
# repair (2026-08-04, `python -m story demo --out /tmp/r8base`, then the draft mutated and
# re-verified). The values are the demo's: `gaap_gross_margin` 2022Q3 is −12.6,
# `adjusted_gross_margin` is +3.3, and the gap is 15.9 percentage points.
#
# The cleanest statement of the class is an asymmetry. A `period_surface` was protected —
# **because a period contains numerals**, so §13.1's coverage rule forced it into the text, and
# both directions were checked: prose moved to "fourth quarter" with the declaration left at Q3
# gives `unbound_numeral`, and the declaration moved too gives `period_mismatch`. A metric name
# carries no numeral, so nothing forced it to occur at all.
# ---------------------------------------------------------------------------------------


def _reported_attack(text: str, rendered: str, **binding_overrides: object) -> DraftSentence:
    """The demo's first sentence with its prose rewritten and its binding honestly re-anchored.

    The span really does hold the rendering it claims, so `binding_span_does_not_match_text`
    cannot be what catches these — the attack is a *true* declaration over *false* prose.
    """
    fields: dict[str, object] = dict(
        fact_id=GGM_ID, rendered=rendered,
        char_start=text.index(rendered), char_end=text.index(rendered) + len(rendered),
        metric_surface="GAAP gross margin", period_surface="the third quarter of 2022",
    )
    fields.update(binding_overrides)
    return DraftSentence(
        index=0, text=text, kind=SentenceKind.REPORTED,
        fact_bindings=(FactBinding(**fields),),  # type: ignore[arg-type]
        citations=(PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=_span("Gross Margin (12.6)")[0],
                                   char_end=_span("Gross Margin (12.6)")[1],
                                   evidence_handle=GGM_SPAN_HANDLE),),
    )


def test_a_reported_sentence_naming_the_other_metric_of_the_pair_is_refused(verifier):
    """R8 defect C. One word — `adjusted` — moves the sentence to the other metric.

    Every declared field stays valid: the fact id is real, the value matches it, the metric
    surface still resolves to `gaap_gross_margin`, the period is right and the span holds what
    it says. Measured: this passed with **zero findings**, and the adjusted margin was +3.3.
    """
    text = "Adjusted gross margin was -12.6% in the third quarter of 2022."
    verified = verifier.verify(
        make_draft(sentences=(_reported_attack(text, "-12.6%"),)), make_package(),
        make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "metric_named_in_text_contradicts_binding"]
    assert failure and failure[0].observed.startswith("the sentence names adjusted_gross_margin")
    assert verified.passed is False


def test_a_reported_sentence_naming_a_metric_the_package_does_not_carry_is_refused(verifier):
    """R8 defect C, the other half: prose about a metric that is nowhere in the package.

    Measured: *"Opendoor reported net income of −12.6 percent"* passed. `net_income` has no
    fact, no metric row and no alias here, so there was nothing for any other check to catch.
    """
    text = "Net income was -12.6% in the third quarter of 2022."
    verified = verifier.verify(
        make_draft(sentences=(_reported_attack(text, "-12.6%"),)), make_package(),
        make_plan(required_warnings=()))
    assert "metric_surface_absent_from_text" in codes_of(verified)
    assert verified.passed is False


def test_a_legitimate_alias_of_the_bound_metric_still_grounds_the_sentence(verifier):
    """The grounding rule resolves through §13.5's index, so an alias is not a refusal.

    `gaap_gross_margin` carries the alias `"GAAP gross margin"` and the label `"Gross Margin"`;
    the sentence writing it in title case must still pass, or the rule would refuse the demo's
    own prose.
    """
    text = "GAAP Gross Margin was -12.6% in the third quarter of 2022."
    verified = verifier.verify(
        make_draft(sentences=(_reported_attack(text, "-12.6%"),)), make_package(),
        make_plan(required_warnings=()))
    assert "metric_surface_absent_from_text" not in codes_of(verified)
    assert "metric_named_in_text_contradicts_binding" not in codes_of(verified)
    assert verified.passed is True


def test_a_sentence_naming_two_metrics_grounds_a_binding_to_either_of_them(verifier):
    """Longest match alone would see only the adjusted one and call the GAAP binding a lie.

    `"gross margin" ⊂ "adjusted gross margin"`, so the scan consumes the words the longest
    match used and reports both surfaces rather than the single longest.
    """
    text = ("GAAP gross margin was -12.6% and adjusted gross margin was 3.3% in the third "
            "quarter of 2022.")
    sentence = DraftSentence(
        index=0, text=text, kind=SentenceKind.REPORTED,
        fact_bindings=(
            FactBinding(fact_id=GGM_ID, rendered="-12.6%", char_start=text.index("-12.6%"),
                        char_end=text.index("-12.6%") + len("-12.6%"),
                        metric_surface="GAAP gross margin",
                        period_surface="the third quarter of 2022"),
            FactBinding(fact_id=AGM_ID, rendered="3.3%", char_start=text.index("3.3%"),
                        char_end=text.index("3.3%") + len("3.3%"),
                        metric_surface="Adjusted gross margin",
                        period_surface="the third quarter of 2022"),
        ),
        # One citation per bound fact. A single citation spanning both rows was enough before
        # §13.7 asked each binding for its own evidence, and it is `uncited_factual_sentence`
        # now: one handle names one cell, so a sentence stating two figures under one handle
        # evidences one of them. This test is about metric grounding, and a draft refused for a
        # citation defect would not be testing it.
        citations=(PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=_span("Gross Margin (12.6)")[0],
                                   char_end=_span("Gross Margin (12.6)")[1],
                                   evidence_handle=GGM_SPAN_HANDLE),
                   PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=_span("Adjusted Gross Margin 3.3")[0],
                                   char_end=_span("Adjusted Gross Margin 3.3")[1],
                                   evidence_handle=AGM_SPAN_HANDLE),),
    )
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "metric_named_in_text_contradicts_binding" not in codes_of(verified)
    assert "metric_surface_absent_from_text" not in codes_of(verified)


@pytest.mark.parametrize("verb", ["exceeded", "surpassed", "topped", "beat", "outperformed"])
def test_a_comparative_outside_the_old_lexicon_no_longer_walks_around_the_check(verifier, verb):
    """R8 defect A. Ordinary investor English walked around a closed word list.

    Each of these sentences says the GAAP margin was the larger one. It was −12.6 against +3.3.
    The declaration is left exactly as the demo wrote it — `left < right` over `(gaap,
    adjusted)`, which recomputes — so nothing but the words can refuse them, and measured, none
    of the five did.
    """
    text = (f"The GAAP gross margin {verb} the adjusted gross margin by 15.9 percentage points "
            "in the third quarter of 2022.")
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(text=text),)), make_package(),
        make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "comparative_not_supported_by_text"]
    assert failure and failure[0].observed == verb
    assert verified.passed is False


def test_every_comparative_term_declares_its_polarity():
    """A widened lexicon with an unstated polarity turns a refusal into a pass.

    `comparative_direction` returns `None` for a term the map does not carry, and
    `_comparison_text_findings` refuses on `None` — but a term in `COMPARATIVE_TERMS` and absent
    from `COMPARATIVE_DIRECTION` would still be a comparison the verifier cannot read. Totality
    is asserted rather than trusted.
    """
    from story.stages.verification.language import COMPARATIVE_DIRECTION, COMPARATIVE_TERMS

    assert set(COMPARATIVE_TERMS) <= set(COMPARATIVE_DIRECTION)


def test_a_declared_comparison_the_sentence_never_makes_is_refused(verifier):
    """R8 defect A's other half, and the one that closes the class rather than five words in it.

    The check read *"no lexicon hit ⇒ no claim"* and returned clean. It now reads *"a declared
    comparison must be a comparison the verifier can read"*. This sentence states a size and no
    direction, which is a `difference` or a `delta_pp` — not a `compare_levels`.
    """
    text = ("The gap between the two margins was 15.9 percentage points in the third quarter "
            "of 2022.")
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(text=text),)), make_package(),
        make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "comparative_not_supported_by_text"]
    assert failure and failure[0].expected.startswith("a comparative construction")
    assert verified.passed is False


@pytest.mark.parametrize("text", [
    ("The GAAP gross margin was not 15.9 percentage points lower than the adjusted gross "
     "margin in the third quarter of 2022."),
    ("The GAAP gross margin was no lower than the adjusted gross margin, a gap of 15.9 "
     "percentage points in the third quarter of 2022."),
])
def test_a_negated_comparative_no_longer_passes_on_its_unnegated_reading(verifier, text):
    """R8 defect B. §13.10 condition 5's rule, which §13.14 had never been given.

    The comparative is in the lexicon, its polarity matches the declaration, and the sentence
    asserts the opposite of both. Measured: both passed.

    The `.` inside `15.9` was itself part of the defect — `language._CLAUSE` read it as a clause
    boundary, so the `not` four words earlier fell in a different clause and `negated()` said
    `False`. That hole was live for §13.10 too, wherever a cited span carried a decimal between
    a negation and its causal marker.
    """
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(text=text),)), make_package(),
        make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "comparative_not_supported_by_text"]
    assert failure and failure[0].expected.startswith("an unnegated comparative")
    assert verified.passed is False


@pytest.mark.parametrize("rendered,phrase", [
    ("15.9 basis points", "15.9 basis points"),
    ("15.9x", "15.9x"),
    ("15.9 percent", "15.9 percent"),
])
def test_a_comparison_rendered_in_the_wrong_unit_is_refused(verifier, rendered, phrase):
    """R8 defect D. `surface_supports_operation` governed three operations and no others.

    The gap is 15.9 percentage points: **1,590** basis points, a ratio of −0.26, and *not*
    15.9 percent — which is the exact percentage-point-versus-percent confusion §13.3 exists to
    stop, on the metric whose two readings §13.3 measures 45.5× apart. Measured: all three
    passed, and the third recomputed cleanly while doing so.
    """
    text = (f"The GAAP gross margin was {phrase} lower than the adjusted gross margin in the "
            "third quarter of 2022.")
    verified = verifier.verify(
        make_draft(sentences=(dated_gap_sentence(
            text=text,
            calculation=Calculation(
                operation="compare_levels", input_observation_ids=(GGM_ID, AGM_ID),
                expression="left < right", result_rendered=rendered,
                period_surface="the third quarter of 2022")),)),
        make_package(), make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "calculation_result_surface_mismatch"]
    assert failure and failure[0].observed == rendered
    assert failure[0].expected.startswith("a result in percentage_points")
    assert verified.passed is False


def test_a_connective_sentence_making_a_wordless_claim_is_refused(verifier):
    """R8 defect E. `_connective_findings` read three declarations and never the prose.

    *"Opendoor's gross margin turned positive during the period"* is false — the GAAP margin was
    −12.6 — and it carries no binding, no calculation and no numeral, so the three declaration
    tests all found nothing. Measured: it passed.
    """
    sentence = DraftSentence(
        index=0, kind=SentenceKind.CONNECTIVE,
        text="Opendoor's gross margin turned positive during the period.")
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "connective_sentence_carries_a_claim"]
    assert failure and "gaap_gross_margin" in failure[0].observed
    assert verified.passed is False


def test_a_connective_sentence_that_names_no_metric_is_still_a_transition(verifier):
    """The prose scan resolves through §13.5, so it does not refuse an actual transition.

    The demo's own connective sentence names no metric — `"profitability metrics"` resolves to
    nothing — and a rule that refused it would have cost the demo its last sentence.
    """
    sentence = DraftSentence(
        index=0, kind=SentenceKind.CONNECTIVE,
        text=("This divergence highlights the impact of non-GAAP adjustments on profitability "
              "metrics for the period."))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert "connective_sentence_carries_a_claim" not in codes_of(verified)
    assert verified.passed is True


def test_a_change_verb_over_a_bound_level_is_refused_even_though_the_number_is_right(verifier):
    """R8 defect F. §13.3's second gate skipped every numeral a binding covered.

    So it could only fire where `unbound_numeral` fires anyway. −12.6% is the metric's *level*
    in the quarter, not a fall of 12.6% in it, and no observation in this package is a change.
    Measured: this passed.

    Note the unit is read off the **fact**, not off the numeral: `"12.6 percent"` spelled in
    words tokenises as `SurfaceUnit.NONE`, because §13.1's tokeniser reads the `%` sign and not
    the word, so a surface-only rule sees no percentage in this sentence at all.
    """
    text = "GAAP gross margin fell 12.6 percent in the third quarter of 2022."
    verified = verifier.verify(
        make_draft(sentences=(_reported_attack(text, "12.6 percent"),)), make_package(),
        make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "percent_change_reported_not_calculated"]
    assert failure and "fell" in failure[0].observed
    assert verified.passed is False


@pytest.mark.parametrize("operation", ["extremum", "absence", "temporal_order"])
def test_an_operation_nothing_recomputes_may_not_cover_its_own_numeral(verifier, operation):
    """R8's latent sixth: `_covering_spans` licensed a result `_recompute_findings` skipped.

    Unreachable from `WRITER_OPERATIONS` today — none of the three is in the writer's enum — but
    §13 is documented as authoritative independently of the writer, and a declaration that
    covers a numeral while nothing evaluates it is the hole §13.1 exists to close. Refused
    outright rather than given a recomputation nobody wrote.
    """
    sentence = DraftSentence(
        index=0, kind=SentenceKind.CALCULATED,
        text="The margin stood at 3.3 percentage points on that measure.",
        calculation=Calculation(
            operation=operation, input_observation_ids=(GGM_ID, AGM_ID),
            expression="min", result_rendered="3.3 percentage points"),
    )
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings if f.code == "operation_not_recomputable"]
    assert failure and failure[0].observed == operation
    assert verified.passed is False


# ---------------------------------------------------------------------------------------
# R9 — the same defect class, on the surface R8 was told not to touch
#
# R8 was told the **period** surface was already protected, on the argument that a period
# contains numerals and §13.1's coverage rule therefore forces the declared surface into the
# text. **That argument is false and R9 measured it false.** It holds only when the period
# phrase carries a numeral, and every counter-example it was tested against happened to include
# a year. Measured against `863edf6` — the demo's own accepted draft, `rendered` span honestly
# re-anchored, `period_surface` still declaring the true `"the third quarter of 2022"`:
#
#   "…for the fourth quarter."          passed, zero findings
#   "…for the full year."               passed
#   "…for the most recent quarter."     passed
#   "…last quarter."                    refused only as `unsupported_superlative` on "last"
#   "…for the fourth quarter of 2022."  refused `unbound_numeral` — on the year, not the period
#
# So the period surface was protected by accident, and only as far as a numeral reached.
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("phrase", [
    "for the fourth quarter",
    "for the full year",
    "for the most recent quarter",
    "last quarter",
])
def test_a_period_the_grammar_cannot_read_no_longer_grounds_a_true_declaration(verifier, phrase):
    """The four measured rows. Every declared field is correct; only the prose moved.

    `"last quarter"` was refused before this repair — but on `unsupported_superlative`, over the
    word `last`, which is §13.14 catching a superlative and not §13.4 catching a period. Rename
    it `"the prior quarter"` and the old verifier had nothing at all. All four are now refused
    by the rule that is actually about the period.
    """
    text = f"Opendoor reported a GAAP Gross Margin of -12.6 percent {phrase}."
    verified = verifier.verify(
        make_draft(sentences=(_reported_attack(text, "-12.6 percent"),)), make_package(),
        make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings if f.code == "period_surface_absent_from_text"]
    assert failure and failure[0].expected.startswith("the sentence to name 2022Q3")
    assert phrase.split("for ")[-1] in failure[0].observed
    assert verified.passed is False


def test_a_period_the_grammar_can_read_and_disagrees_with_is_refused_on_its_meaning(verifier):
    """The other half: prose that pins a period, and pins the wrong one.

    `unbound_numeral` already refuses this one — on the `2022`, which is an accident of the
    phrase carrying a year rather than a judgment about the period. The finding that reads the
    sentence names the period it resolved and the period the binding holds.
    """
    text = "Opendoor reported a GAAP Gross Margin of -12.6 percent for the fourth quarter of 2022."
    verified = verifier.verify(
        make_draft(sentences=(_reported_attack(text, "-12.6 percent"),)), make_package(),
        make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings
               if f.code == "period_named_in_text_contradicts_binding"]
    assert failure and "'the fourth quarter of 2022' (2022Q4)" in failure[0].observed
    assert "the third quarter of 2022" in failure[0].observed
    assert verified.passed is False


@pytest.mark.parametrize("phrase,surface", [
    ("in the third quarter of 2022", "the third quarter of 2022"),
    ("in Q3 2022", "Q3 2022"),
    ("in 3Q22", "3Q22"),
    ("in the third quarter of fiscal 2022", "the third quarter of fiscal 2022"),
    ("for the third quarter ended 2022", "the third quarter ended 2022"),
])
def test_a_legitimate_period_phrasing_still_grounds_a_true_sentence(verifier, phrase, surface):
    """The over-reach guard: whatever §13.4's grammar accepts must still pass.

    `"the third quarter of fiscal 2022"` is the case that caught a real bug in the scanner and
    is kept for it. `"fiscal 2022"` is a phrase *inside* it and the fiscal-year rule comes first
    in the grammar's own rule order, so a scan that respected rule order read this true sentence
    as naming FY2022 and contradicted its own binding. Longest match wins — §13.5's rule for the
    alias index, and this one's too.

    Not in this list, because §13.4's grammar does not carry it: *"the quarter ended September
    30, 2022"*. It is refused, and it was refused before R9 too — the declared surface resolves
    to nothing, which is `period_unresolvable`. Widening the grammar to admit it would remove an
    existing refusal, so it is left alone and recorded.
    """
    text = f"GAAP gross margin was -12.6% {phrase}."
    verified = verifier.verify(
        make_draft(sentences=(_reported_attack(text, "-12.6%", period_surface=surface),)),
        make_package(), make_plan(required_warnings=()))
    assert "period_surface_absent_from_text" not in codes_of(verified)
    assert "period_named_in_text_contradicts_binding" not in codes_of(verified)
    assert verified.passed is True


def test_a_sentence_that_names_no_period_at_all_behaves_exactly_as_it_did(verifier):
    """The deliberate asymmetry with R8's metric rule, and the reason for it.

    A metric name is what a factual sentence is *about*, so `metric_surface_absent_from_text`
    refuses a sentence that names none. A period is routinely carried by the paragraph — the
    demo's own calculated sentence declares `"the third quarter of 2022"` and states no period
    in words — so requiring every sentence to name one would refuse true prose to catch nothing:
    a sentence that asserts no period cannot assert a false one, and the declared surface is
    still checked against the fact by §13.4's existing rules.
    """
    text = "GAAP gross margin was -12.6% on the company's own reporting."
    verified = verifier.verify(
        make_draft(sentences=(_reported_attack(text, "-12.6%"),)), make_package(),
        make_plan(required_warnings=()))
    assert verified.all_findings == ()
    assert verified.passed is True


@pytest.mark.parametrize("phrase,code", [
    ("in the fourth quarter", "period_surface_absent_from_text"),
    ("for the full year", "period_surface_absent_from_text"),
    ("in the fourth quarter of 2022", "period_named_in_text_contradicts_binding"),
])
def test_a_calculations_period_surface_is_grounded_in_its_own_sentence(verifier, phrase, code):
    """R7 added `Calculation.period_surface` and it carries the identical exposure.

    §13.9 gives a calculated sentence no bindings, so this field is the *only* way a derivation
    can name the period it computed over — and it was checked against the inputs and never
    against the words beside it.
    """
    text = f"The gap between the two measures was 15.9 percentage points {phrase}."
    sentence = DraftSentence(
        index=0, text=text, kind=SentenceKind.CALCULATED,
        calculation=Calculation(
            operation="delta_pp", input_observation_ids=(GGM_ID, AGM_ID),
            expression="adjusted_gross_margin - gaap_gross_margin",
            result_rendered="15.9 percentage points",
            period_surface="the third quarter of 2022"),
    )
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    failure = [f for f in verified.all_findings if f.code == code]
    assert failure and failure[0].fact_ids == (GGM_ID, AGM_ID)
    assert verified.passed is False


def test_a_derivation_whose_sentence_names_no_period_keeps_its_declared_surface(verifier):
    """The demo's own third sentence, and the reason the no-period carve-out is not a hole.

    *"The gap between the two measures was 15.9 percentage points."* declares
    `period_surface: "the third quarter of 2022"` and names no period in words. It is true, and
    the declared surface is still required to agree with **every** input by §13.4.
    """
    sentence = gap_sentence(
        index=0,
        calculation=Calculation(
            operation="delta_pp", input_observation_ids=(GGM_ID, AGM_ID),
            expression="adjusted_gross_margin - gaap_gross_margin",
            result_rendered="15.9 percentage points",
            period_surface="the third quarter of 2022"))
    verified = verifier.verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(required_warnings=()))
    assert verified.all_findings == ()
    assert verified.passed is True


def _explanatory_attack(text: str, rendered: str, **binding_overrides: object) -> DraftSentence:
    """`_reported_attack`'s sentence, declared `explanatory` instead.

    §13.7 names the two kinds together — *"a reported sentence states what a filing said, and an
    explanatory one paraphrases it"* — and R8 grounded only the first.
    """
    fields: dict[str, object] = dict(
        fact_id=GGM_ID, rendered=rendered,
        char_start=text.index(rendered), char_end=text.index(rendered) + len(rendered),
        metric_surface="GAAP gross margin", period_surface="the third quarter of 2022",
    )
    fields.update(binding_overrides)
    return DraftSentence(
        index=0, text=text, kind=SentenceKind.EXPLANATORY,
        fact_bindings=(FactBinding(**fields),),  # type: ignore[arg-type]
        citations=(PassageCitation(passage_id=PASSAGE_ID, document_id=DOCUMENT_ID,
                                   char_start=_span("Gross Margin (12.6)")[0],
                                   char_end=_span("Gross Margin (12.6)")[1],
                                   evidence_handle=GGM_SPAN_HANDLE),),
    )


@pytest.mark.parametrize("text,code", [
    ("The filing puts adjusted gross margin at -12.6% in the third quarter of 2022.",
     "metric_named_in_text_contradicts_binding"),
    ("The filing puts net income at -12.6% in the third quarter of 2022.",
     "metric_surface_absent_from_text"),
    ("The filing puts GAAP gross margin at -12.6% for the fourth quarter.",
     "period_surface_absent_from_text"),
    ("The filing puts GAAP gross margin at -12.6% for the fourth quarter of 2022.",
     "period_named_in_text_contradicts_binding"),
])
def test_an_explanatory_sentence_is_grounded_against_its_prose_too(verifier, text, code):
    """R8's own left-undone #4. An `explanatory` sentence carries `fact_bindings` and was not
    read against its prose at all — which is if anything the easier place to move a metric or a
    period, because a paraphrase is licensed to reword the filing."""
    verified = verifier.verify(
        make_draft(sentences=(_explanatory_attack(text, "-12.6%"),)), make_package(),
        make_plan(required_warnings=()))
    assert code in codes_of(verified)
    assert verified.passed is False


def test_a_true_explanatory_paraphrase_is_not_refused_by_either_grounding_rule(verifier):
    """The over-reach guard on the kind R9 added, so the rule is a check and not a ban."""
    text = "The filing puts GAAP gross margin at -12.6% in the third quarter of 2022."
    verified = verifier.verify(
        make_draft(sentences=(_explanatory_attack(text, "-12.6%"),)), make_package(),
        make_plan(required_warnings=()))
    assert codes_of(verified) <= {"paraphrase_distance"}  # WARN, §13.7, not blocking
    assert verified.passed is True


def test_the_period_scan_reports_both_tiers_and_never_a_phrase_inside_a_phrase():
    """`scan`'s two tiers, and the longest-match rule that keeps them from overlapping.

    Tier one is §13.4's grammar applied unanchored; tier two is the deictic family §13.4 refuses
    — `"the quarter"` is its own worked example. The last case is the ordering bug: `"fiscal
    2022"` is a grammar phrase sitting inside another grammar phrase, and the fiscal-year rule
    comes first in the grammar's own rule order.
    """
    def read(text: str) -> list[tuple[str, str]]:
        return [(phrase.text, phrase.period.key if phrase.resolved else "UNRESOLVED")
                for phrase in scan_periods(text)]

    assert read("was -12.6% in the third quarter of 2022.") == [
        ("the third quarter of 2022", "2022Q3")]
    assert read("was -12.6% for the fourth quarter.") == [("the fourth quarter", "UNRESOLVED")]
    assert read("was -12.6% in Q3.") == [("q3", "UNRESOLVED")]
    assert read("was 3,394 in the nine months ended September 30, 2022.") == [
        ("the nine months ended september 30, 2022", "2022-01-01_2022-09-30")]
    assert read("was -12.6% in the third quarter of fiscal 2022.") == [
        ("the third quarter of fiscal 2022", "2022Q3")]
    assert read("The gap was 15.9 percentage points.") == []


def test_the_two_new_period_codes_are_refusals_under_section_13_4():
    """§13.17's gate is the one place a severity is chosen, and R9 adds to it rather than around
    it."""
    for code in ("period_surface_absent_from_text", "period_named_in_text_contradicts_binding"):
        assert GATE[code].severity is Severity.REFUSE
        assert GATE[code].blocking is True
        assert GATE[code].section == "13.4"
        assert GATE[code].remedy is Remedy.ADD_PERIOD_QUALIFIER


# ---------------------------------------------------------------------------------------
# §13.7 over evidence handles — TABLE_CELL_CITATIONS §3.4
#
# **Its own passage and its own facts, and the reason is a defect in the fixtures above.** Every
# fact in this file carries `source_lane="normalized_table"` with `cell=None`, and that is a
# shape the corpus does not hold: 2,690 of 2,690 table-backed observations carry all five
# indices and **0 carry some** *(verified live 2026-08-18)*. `PASSAGE_TEXT` is four bare lines
# with no `|` at all, so `split_cells` reads every line as a single column-0 cell and a row's
# label and its value would be the same string — a fixture like that cannot exercise a
# coordinate. Retrofitting it would rewrite every offset in this file for no gain, so the §3.4
# checks get a grid of their own and the fixtures above go on proving what they always proved.
#
# The grid's shape is the corpus's. **The value column and the period-header column are
# different indices** — values at 2 and 5, headers at 1 and 4 — because `$` signs and blank
# spacer cells push them apart on 2,125 of the 2,690 table-backed rows *(verified live
# 2026-08-13 by S3)*. A fixture that put them in one column would let `resolve_header` read the
# value's own column and still pass, which is the corpus's real failure mode and the reason
# `period_header_column_index` is carried at all.
#
# Two period columns, because "wrong period" is the mis-citation this repair is most about: the
# 2022Q2 cell sits three columns from the 2022Q3 one in the same row of the same passage, and
# §13.7's older `citation_does_not_support_fact` cannot tell them apart.
# ---------------------------------------------------------------------------------------

GRID_PASSAGE_ID = "psg:opendoor-10q-2022q3:margins-grid"
GRID_TEXT = (
    "|  | Three Months Ended September 30, 2022 |  |  | "
    "Three Months Ended June 30, 2022 |  |\n"
    "| --- | --- | --- | --- | --- | --- |\n"
    "| Revenue | $ | 3,394 |  | $ | 2,297 |\n"
    "| Gross Margin |  | (12.6) |  |  | (5.3) |\n"
    "| Adjusted Gross Margin |  | 3.3 |  |  | 8.7 |\n"
)
Q2_COLUMN_LABEL = "Three Months Ended June 30, 2022"
AGM_Q2_ID = "obs:adjusted-gross-margin:opendoor:2022Q2:normalized-table:c40b7e19d8a2"

AGM_CELL = TableCellRef(row_index=4, value_column_index=2,
                        period_header_row_index=0, period_header_column_index=1)
GGM_CELL = TableCellRef(row_index=3, value_column_index=2,
                        period_header_row_index=0, period_header_column_index=1)
AGM_Q2_CELL = TableCellRef(row_index=4, value_column_index=5,
                           period_header_row_index=0, period_header_column_index=4)


def grid_fact(**overrides: object) -> PackagedFact:
    """A fact of the grid above. `cell` defaults to the adjusted-margin 2022Q3 cell."""
    fields: dict[str, object] = dict(passage_id=GRID_PASSAGE_ID, cell=AGM_CELL)
    fields.update(overrides)
    return make_fact(**fields)


GRID_AGM = grid_fact()
GRID_GGM = grid_fact(
    observation_id=GGM_ID, metric_id="gaap_gross_margin", metric_label="Gross Margin",
    value=-12.6, row_label="Gross Margin", quoted_text="(12.6)", cell=GGM_CELL)
GRID_AGM_Q2 = grid_fact(
    observation_id=AGM_Q2_ID, period_key="2022Q2", period_start="2022-04-01",
    period_end="2022-06-30", value=8.7, quoted_text="8.7",
    column_label=Q2_COLUMN_LABEL, cell=AGM_Q2_CELL)

#: Minted by `PackagedFact`, never spelled here. A handle written by hand in a test is a second
#: authority on the format, and §3.4 check 7 is exactly the claim that there is only one.
AGM_HANDLE = GRID_AGM.evidence_handle
GGM_HANDLE = GRID_GGM.evidence_handle
AGM_Q2_HANDLE = GRID_AGM_Q2.evidence_handle


def grid_passage(text: str = GRID_TEXT, **overrides: object) -> PackagedPassage:
    fields: dict[str, object] = dict(
        passage_id=GRID_PASSAGE_ID, document_id=DOCUMENT_ID, text=text, char_count=len(text),
        passage_kind="normalized_table", role=EvidenceRole.PRIMARY_SUPPORT)
    fields.update(overrides)
    return PackagedPassage(**fields)  # type: ignore[arg-type]


def grid_package(**overrides: object) -> StoryEvidencePackage:
    fields: dict[str, object] = dict(
        facts=(GRID_AGM, GRID_GGM, GRID_AGM_Q2), primary_passages=(grid_passage(),))
    fields.update(overrides)
    return make_package(**fields)


def grid_plan(**overrides: object) -> EditorialPlan:
    fields: dict[str, object] = dict(key_points=(
        KeyPoint(claim="Adjusted gross margin was 3.3% in 2022Q3.",
                 required_fact_ids=(AGM_ID,),
                 required_citation_passage_ids=(GRID_PASSAGE_ID,),
                 statement_class=StatementClass.REPORTED),))
    fields.update(overrides)
    return make_plan(**fields)


def grid_citation(fact: PackagedFact, *, text: str = GRID_TEXT, **overrides: object
                  ) -> PassageCitation:
    """The citation §12 would build for `fact`: the span is resolved from the coordinates.

    Written this way so a test that wants a *wrong* citation has to say which part is wrong,
    rather than assembling a plausible-looking one by hand and accidentally testing two things.
    """
    assert fact.cell is not None
    cell = resolve_cell(text, row_index=fact.cell.row_index,
                        column_index=fact.cell.value_column_index)
    fields: dict[str, object] = dict(
        passage_id=GRID_PASSAGE_ID, document_id=DOCUMENT_ID,
        char_start=cell.char_start, char_end=cell.char_end,
        evidence_handle=fact.evidence_handle)
    fields.update(overrides)
    return PassageCitation(**fields)  # type: ignore[arg-type]


def grid_draft(sentences=None, **overrides: object) -> Draft:  # type: ignore[no-untyped-def]
    return make_draft(
        sentences=tuple(sentences) if sentences is not None else (
            agm_sentence(citations=(grid_citation(GRID_AGM),)),
            ggm_sentence(citations=(grid_citation(GRID_GGM),)),
            gap_sentence(), warning_sentence()),
        **overrides)


def test_the_grid_fixture_holds_the_four_invariants_the_repair_rests_on():
    """§1.3, on this fixture, so a later edit to the grid cannot silently make every §3.4 test
    below vacuous by moving a value into its own header's column."""
    for fact in (GRID_AGM, GRID_GGM, GRID_AGM_Q2):
        assert fact.cell is not None
        cell = resolve_cell(GRID_TEXT, row_index=fact.cell.row_index,
                            column_index=fact.cell.value_column_index)
        assert cell.text == fact.quoted_text
        assert cell.row_label == fact.row_label
        assert resolve_header(GRID_TEXT, row_index=fact.cell.period_header_row_index,
                              column_index=fact.cell.period_header_column_index
                              ) == fact.column_label
        # The measurement that makes `period_header_column_index` a carried property: reading
        # the header at the value's own column returns a spacer, silently and well-formed.
        assert resolve_header(GRID_TEXT, row_index=0,
                              column_index=fact.cell.value_column_index) == ""


def test_a_clean_draft_over_a_real_grid_passes_with_no_finding_at_all(verifier):
    """§3.4 checks 1–5 and 7 are new obligations, and this is the proof they cost nothing true.

    Both citations carry the handle the package minted, resolved to the cell the coordinates
    name — which is what `writer.draft_from` builds — and the verifier finds nothing.
    """
    verified = verifier.verify(grid_draft(), grid_package(), grid_plan())
    assert verified.all_findings == ()
    assert verified.passed is True


def test_citing_another_facts_cell_in_the_same_passage_is_refused(verifier):
    """**The hole S4 left open, closed.** The sentence binds the adjusted margin and cites the
    GAAP cell — a mis-citation §12 constructs cleanly, because §12 owes only that the span is the
    cell the handle names.

    The older test cannot see it and the gap is total rather than marginal:
    `citation_does_not_support_fact` asks for *"the passage each bound fact was read from"*, and
    **all 144** table-backed passages in the corpus evidence more than one observation — 2,690 of
    2,690, up to 70 in one passage *(verified live 2026-08-18)*. On table evidence that test
    separated nothing at all. The assertion below that it stays silent is the point of the test.
    """
    verified = verifier.verify(
        grid_draft(sentences=(
            agm_sentence(citations=(grid_citation(GRID_GGM),)),
            ggm_sentence(citations=(grid_citation(GRID_GGM),)),
            gap_sentence(), warning_sentence())),
        grid_package(), grid_plan())
    assert codes_of(verified) == {"evidence_handle_not_for_fact"}
    assert verified.passed is False
    found = next(f for f in verified.all_findings
                 if f.code == "evidence_handle_not_for_fact")
    assert found.observed == f"{GGM_HANDLE} was minted for {GGM_ID}"
    # The handle to cite instead is in `expected`; `suggested_fact_ids` is fact ids, and the
    # fact it names is the one the *cited cell* belongs to — the other way to fix the sentence.
    assert AGM_HANDLE in found.expected
    assert found.suggested_fact_ids == (GGM_ID,)


def test_citing_the_same_metrics_other_period_from_the_same_row_is_refused(verifier):
    """Wrong period, same row, same passage, same metric — three cells along.

    This is the mis-citation §13.7.1 has never been able to refuse and this repair does not
    claim to fix at label grain: `column_label_ambiguous_in_passage` is about a label naming two
    periods, and §1.5 measured the header column index separating only **71** of its 179
    ambiguous pairs. Check 7 refuses this one for a different reason — the handle names a
    different fact — and leaves §13.7.1 exactly as it was.
    """
    verified = verifier.verify(
        grid_draft(sentences=(
            agm_sentence(citations=(grid_citation(GRID_AGM_Q2),)),
            ggm_sentence(citations=(grid_citation(GRID_GGM),)),
            gap_sentence(), warning_sentence())),
        grid_package(), grid_plan())
    assert codes_of(verified) == {"evidence_handle_not_for_fact"}
    assert "2022Q2" in AGM_Q2_HANDLE or AGM_Q2_HANDLE.endswith("r4c5")


def test_a_handle_from_another_package_resolves_to_nothing_and_is_refused(verifier):
    """§3.4 check 1. The handle is well formed, names a real cell of a real table, and this
    package minted it for no fact — which is every fabrication at once, because a handle is
    derived from coordinates and nothing else mints one."""
    foreign = make_fact(passage_id="psg:opendoor-10q-2021q4:margins-grid", cell=AGM_CELL)
    assert foreign.evidence_handle != AGM_HANDLE
    verified = verifier.verify(
        grid_draft(sentences=(
            agm_sentence(citations=(grid_citation(
                GRID_AGM, evidence_handle=foreign.evidence_handle),)),
            ggm_sentence(citations=(grid_citation(GRID_GGM),)),
            gap_sentence(), warning_sentence())),
        grid_package(), grid_plan())
    assert codes_of(verified) == {"unresolvable_evidence_handle"}
    found = next(f for f in verified.all_findings)
    assert found.observed == foreign.evidence_handle
    assert AGM_ID in found.suggested_fact_ids


def test_a_handle_whose_passage_the_package_does_not_carry_is_refused(verifier):
    """§3.4 check 1, second half: *"H names a passage in the writer's slice"*.

    Reachable without anyone doing anything wrong — §10.2.1 point 3 builds the slice by
    intersecting the facts' passages with the package's four passage sections, so a fact whose
    passage no section carries has a citable-looking handle and no text to resolve it in.
    """
    orphan = grid_fact(observation_id=AGM_Q2_ID, passage_id="psg:opendoor-10q-2022q2:elsewhere")
    verified = verifier.verify(
        grid_draft(sentences=(
            agm_sentence(fact_bindings=(_binding(AGM_TEXT, "3.3%", fact_id=AGM_Q2_ID),),
                         citations=(grid_citation(
                             GRID_AGM, evidence_handle=orphan.evidence_handle),)),
            ggm_sentence(citations=(grid_citation(GRID_GGM),)),
            gap_sentence(), warning_sentence())),
        grid_package(facts=(GRID_AGM, GRID_GGM, orphan)), grid_plan())
    assert "unresolvable_evidence_handle" in codes_of(verified)
    found = next(f for f in verified.all_findings
                 if f.code == "unresolvable_evidence_handle")
    assert "psg:opendoor-10q-2022q2:elsewhere" in found.observed


def test_coordinates_the_passage_no_longer_has_are_refused_as_out_of_bounds(verifier):
    """§3.4 check 2, and it cannot be a model's doing: the coordinates come off the
    `PackagedFact`. It says the package and the passage text it carries disagree about the
    table — a stored package (§14) replayed against a re-extracted corpus."""
    drifted = grid_fact(cell=TableCellRef(row_index=9, value_column_index=2,
                                          period_header_row_index=0,
                                          period_header_column_index=1))
    verified = verifier.verify(
        grid_draft(sentences=(
            agm_sentence(citations=(grid_citation(
                GRID_AGM, evidence_handle=drifted.evidence_handle),)),
            ggm_sentence(citations=(grid_citation(GRID_GGM),)),
            gap_sentence(), warning_sentence())),
        grid_package(facts=(drifted, GRID_GGM, GRID_AGM_Q2)), grid_plan())
    assert codes_of(verified) == {"evidence_handle_out_of_bounds"}
    assert "which has 6 lines" in next(iter(verified.all_findings)).observed


def test_a_cell_that_no_longer_holds_the_facts_value_is_refused(verifier):
    """§3.4 check 3, which held on 2,690 / 2,690 *(verified live 2026-08-18)*.

    The coordinates name column 0 — the row's own label — instead of the value, which is what a
    package built against a table with one fewer spacer column would carry.
    """
    shifted = grid_fact(cell=TableCellRef(row_index=4, value_column_index=0,
                                          period_header_row_index=0,
                                          period_header_column_index=1))
    verified = verifier.verify(
        grid_draft(sentences=(
            agm_sentence(citations=(grid_citation(
                shifted, evidence_handle=shifted.evidence_handle),)),
            ggm_sentence(citations=(grid_citation(GRID_GGM),)),
            gap_sentence(), warning_sentence())),
        grid_package(facts=(shifted, GRID_GGM, GRID_AGM_Q2)), grid_plan())
    assert codes_of(verified) == {"evidence_cell_value_mismatch"}
    found = next(iter(verified.all_findings))
    assert found.observed == "'Adjusted Gross Margin'"
    assert found.remedy is Remedy.REBUILD_PACKAGE


def test_a_row_relabelled_in_the_passage_is_refused(verifier):
    """§3.4 check 4, which held on 2,690 / 2,690.

    The drift is in the **passage**, not in the fact: a re-extraction that renamed the line item
    leaves the coordinates resolving to the right value under the wrong label. `row_label` on the
    fact is untouched, so §13.7 Rule A step 3's alias check is silent and this is the only
    finding — which is the whole reason check 4 is a separate obligation.
    """
    relabelled = GRID_TEXT.replace("| Adjusted Gross Margin |",
                                   "| Adjusted Gross Margin (Loss) |")
    verified = verifier.verify(
        grid_draft(sentences=(
            agm_sentence(citations=(grid_citation(GRID_AGM, text=relabelled),)),
            ggm_sentence(citations=(grid_citation(GRID_GGM, text=relabelled),)),
            gap_sentence(), warning_sentence())),
        grid_package(primary_passages=(grid_passage(relabelled),)), grid_plan())
    assert codes_of(verified) == {"evidence_row_label_mismatch"}
    assert next(iter(verified.all_findings)).observed == "'Adjusted Gross Margin (Loss)'"


def test_a_header_that_no_longer_names_the_facts_period_is_refused(verifier):
    """§3.4 check 5, which holds on 2,690 / 2,690 **at `period_header_column_index`** and on
    only 565 / 2,690 at the value's own column. The header row is redated here, so both facts'
    period headers stop matching the `column_label` they were read under."""
    redated = GRID_TEXT.replace("Three Months Ended September 30, 2022",
                                "Nine Months Ended September 30, 2022")
    verified = verifier.verify(
        grid_draft(sentences=(
            agm_sentence(citations=(grid_citation(GRID_AGM, text=redated),)),
            ggm_sentence(citations=(grid_citation(GRID_GGM, text=redated),)),
            gap_sentence(), warning_sentence())),
        grid_package(primary_passages=(grid_passage(redated),)), grid_plan())
    assert codes_of(verified) == {"evidence_column_label_mismatch"}
    assert all("Nine Months Ended September 30, 2022" in found.observed
               for found in verified.all_findings)


def test_a_span_that_is_not_the_handles_cell_is_refused(verifier):
    """**Not one of §3.4's checks**, and added because §3.4 reads as though the handle were the
    only thing on a citation row.

    `PassageCitation` also carries the offsets the evidence panel highlights and §13.7's reuse
    rule keys on. Here the handle is the adjusted margin's and the span covers the GAAP cell:
    every §3.4 check passes, and a reader would be shown `(12.6)` under a sentence saying 3.3%.
    §12 derives the span from the handle so no model can write this, which is the same standing
    check 2 has.
    """
    elsewhere = resolve_cell(GRID_TEXT, row_index=3, column_index=2)
    verified = verifier.verify(
        grid_draft(sentences=(
            agm_sentence(citations=(grid_citation(
                GRID_AGM, char_start=elsewhere.char_start, char_end=elsewhere.char_end),)),
            ggm_sentence(citations=(grid_citation(GRID_GGM),)),
            gap_sentence(), warning_sentence())),
        grid_package(), grid_plan())
    assert codes_of(verified) == {"evidence_cell_span_mismatch"}


def test_narrative_evidence_keeps_the_span_path_and_meets_no_cell_check(verifier):
    """§1.6: all 14 narrative observations quote whole sentences occurring exactly once in their
    passage, and this repair leaves that path alone. The handle for a fact with no `cell` names
    the fact's slot rather than a coordinate, so §3.4 checks 2–5 have nothing to ask and Rule B
    still owns the span.

    Driven over the fixtures at the top of this file, whose facts carry `cell=None` — the same
    draft as `test_a_clean_draft_of_the_demo_candidate_passes_with_no_finding_at_all`, whose
    citations state the handles the package minted because nothing else is constructible.
    """
    package = make_package()
    handles = package.facts_by_evidence_handle()
    assert set(handles) == {AGM_SPAN_HANDLE, GGM_SPAN_HANDLE}
    assert all(":span:" in handle for handle in handles)

    verified = verifier.verify(
        make_draft(sentences=(agm_sentence(), ggm_sentence(),
                              gap_sentence(), warning_sentence())),
        package, make_plan())
    assert verified.all_findings == ()
    assert verified.passed is True


def test_a_narrative_handle_bound_to_the_other_fact_is_still_refused(verifier):
    """Check 7 does not depend on there being a cell. The narrative handle names the fact's
    `(metric_id, period_key)` slot, which §6.1 guarantees is one canonical fact, so a handle
    still names exactly one fact and citing another's is still detectable."""
    citation = agm_sentence().citations[0]
    assert isinstance(citation, PassageCitation)
    verified = verifier.verify(
        make_draft(sentences=(
            agm_sentence(citations=(citation.model_copy(
                update={"evidence_handle": GGM_FACT.evidence_handle}),)),
            ggm_sentence(), gap_sentence(), warning_sentence())),
        make_package(), make_plan())
    assert codes_of(verified) == {"evidence_handle_not_for_fact"}


def test_a_citation_that_states_no_handle_cannot_be_built_at_all():
    """**The limit this repair used to state is closed, and the old test asserted the hole.**

    It read *"a citation that states no handle falls back to the older tests"* and asserted
    `codes_of(verified) == set()` — a citation carrying the GAAP cell's bytes under no handle, on
    a sentence binding the adjusted margin, with **nothing** found. There is no older test to
    fall back to on a table fact: Rule A step 1 asks that the package's `quoted_text` occur
    somewhere in the passage, which every citation into that passage satisfies, and Rule B does
    not run. Reproduced on the committed demo package at one character.

    `PassageCitation.evidence_handle` is now required with no default, so the citation the old
    test built is not constructible — the `PackagedPassage.role` answer to a field whose every
    default was a claim about evidence.
    """
    for missing in ({"evidence_handle": None}, {}):
        with pytest.raises(ValidationError):
            PassageCitation(passage_id=GRID_PASSAGE_ID, document_id=DOCUMENT_ID,
                            char_start=0, char_end=7, **missing)  # type: ignore[arg-type]


def test_a_sentence_that_states_two_figures_and_evidences_one_is_refused(verifier):
    """**§3.4 check 7 is satisfied by any handle for any bound fact, and that was the hole.**

    Check 7 asks `fact.observation_id not in bound_ids`, so one handle answers for every binding.
    Reproduced 2026-08-18 on the committed demo package: one sentence binding both margins and
    carrying only `ev:…open-20220930.htm#p139:r11c2` verified clean, and the second number was
    stated with nothing pointing at it.

    The code is `uncited_factual_sentence` — §13.7's own name for a factual sentence with no
    span behind it, here at fact grain rather than sentence grain — and not a new one. Its
    remedy, `REBIND_TO_FACT`, is the instruction: cite the fact's handle, or drop the binding.
    """
    text = "Adjusted gross margin was 3.3% and GAAP gross margin was -12.6% in 2022Q3."
    both = DraftSentence(
        index=0, text=text, kind=SentenceKind.REPORTED,
        fact_bindings=(_binding(text, "3.3%", period_surface="2022Q3"),
                       _binding(text, "-12.6%", fact_id=GGM_ID,
                                metric_surface="GAAP gross margin", period_surface="2022Q3")),
        citations=(grid_citation(GRID_AGM),))
    verified = verifier.verify(
        grid_draft(sentences=(both, gap_sentence(index=1), warning_sentence(index=2))),
        grid_package(), grid_plan())
    assert "uncited_factual_sentence" in codes_of(verified)
    assert verified.passed is False
    found = next(f for f in verified.all_findings if f.code == "uncited_factual_sentence")
    assert found.fact_ids == (GGM_ID,)
    assert GGM_HANDLE in found.expected
    assert found.observed == AGM_HANDLE
    assert found.remedy is Remedy.REBIND_TO_FACT

    # Citing both is what the sentence owed, and the same draft then carries no citation finding
    # at all — the rule is satisfiable by saying the true thing.
    honest = both.model_copy(update={
        "citations": (grid_citation(GRID_AGM), grid_citation(GRID_GGM))})
    assert not any(found.code == "uncited_factual_sentence" for found in verifier.verify(
        grid_draft(sentences=(honest, gap_sentence(index=1), warning_sentence(index=2))),
        grid_package(), grid_plan()).all_findings)


def test_citing_the_wrong_cell_is_named_once_and_not_twice(verifier):
    """The coverage rule stands down where check 7 has already spoken.

    A sentence binding the adjusted margin and citing the GAAP cell leaves the adjusted margin
    uncovered *and* carries a handle for a fact it does not bind. Those are one defect with one
    remedy — `evidence_handle_not_for_fact` already names the fact to rebind to — so
    `uncited_factual_sentence` does not also fire, and the rejection panel does not tell a reader
    to fix one sentence twice.
    """
    verified = verifier.verify(
        grid_draft(sentences=(
            agm_sentence(citations=(grid_citation(GRID_GGM),)),
            ggm_sentence(citations=(grid_citation(GRID_GGM),)),
            gap_sentence(), warning_sentence())),
        grid_package(), grid_plan())
    assert codes_of(verified) == {"evidence_handle_not_for_fact"}


def test_the_seven_handle_codes_are_refusals_and_none_of_them_weakens_a_check(verifier):
    """§13.17's gate is the one place a severity is chosen, and §3.4 adds to it rather than
    around it. The remedies split by who can cause the finding: a draft can name a handle that
    is not the package's or not its fact's, and nothing else here is a draft's doing."""
    assert {code: GATE[code].remedy for code in (
        "unresolvable_evidence_handle", "evidence_handle_not_for_fact",
        "evidence_cell_span_mismatch")} == {
        "unresolvable_evidence_handle": Remedy.REBIND_TO_FACT,
        "evidence_handle_not_for_fact": Remedy.REBIND_TO_FACT,
        "evidence_cell_span_mismatch": Remedy.REBIND_TO_FACT}
    assert {code: GATE[code].remedy for code in (
        "evidence_handle_out_of_bounds", "evidence_cell_value_mismatch",
        "evidence_row_label_mismatch", "evidence_column_label_mismatch")} == {
        "evidence_handle_out_of_bounds": Remedy.REBUILD_PACKAGE,
        "evidence_cell_value_mismatch": Remedy.REBUILD_PACKAGE,
        "evidence_row_label_mismatch": Remedy.REBUILD_PACKAGE,
        "evidence_column_label_mismatch": Remedy.REBUILD_PACKAGE}
    for code in ("unresolvable_evidence_handle", "evidence_handle_not_for_fact",
                 "evidence_cell_span_mismatch", "evidence_handle_out_of_bounds",
                 "evidence_cell_value_mismatch", "evidence_row_label_mismatch",
                 "evidence_column_label_mismatch"):
        assert GATE[code].severity is Severity.REFUSE
        assert GATE[code].blocking is True
        assert GATE[code].section == "13.7"


def test_the_column_ambiguity_check_is_untouched_by_this_repair(verifier):
    """§1.5, and it is a bound on what this repair may claim. Carrying
    `period_header_column_index` fully separates only **71 of the 179** ambiguous
    `(passage_id, column_label)` pairs in the corpus; **108 survive**. So
    `column_label_ambiguous_in_passage` still fires on a label naming two periods, with both
    handles resolving perfectly and every §3.4 check silent.

    **The numbers were 87 and 92 here and in §1.5 until the adversarial review of S7, and both
    were the wrong measurement.** 87 counts the pairs where `period_header_column_index` takes
    more than one value — *"it distinguishes something"* — not the pairs where grouping by it
    leaves one `period_key` in every group. Re-derived live 2026-08-18 over the 2,690
    table-backed evidence rows: 179 ambiguous pairs, **71** separated, **108** not, 87 in which
    the index merely varies. The conclusion this test asserts is unchanged.
    """
    q2_same_label = grid_fact(
        observation_id=AGM_Q2_ID, period_key="2022Q2", period_start="2022-04-01",
        period_end="2022-06-30", value=8.7, quoted_text="8.7", cell=AGM_Q2_CELL)
    assert q2_same_label.column_label == COLUMN_LABEL == GRID_AGM.column_label
    verified = verifier.verify(
        grid_draft(), grid_package(facts=(GRID_AGM, GRID_GGM, q2_same_label)), grid_plan())
    assert "column_label_ambiguous_in_passage" in codes_of(verified)
    assert not any(found.code.startswith("evidence_") for found in verified.all_findings)
