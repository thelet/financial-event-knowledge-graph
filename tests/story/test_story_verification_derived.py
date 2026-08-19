"""S13 — the verifier over derived facts, driven against the candidate §2 measured.

`cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:...` is the run the brief is
about: adjusted gross profit `$556M -> $110M`, a fall of `$446M`, and a draft that was refused.
**The refusal was never the arithmetic** — DETERMINISTIC_FACT_TOOLS §2 re-read the two recorded
runs and found `$446 million` covered, the recomputation clean at `446000000.0`, and
`unbound_numeral` firing on the literal `2022` because the model left `Calculation.period_surface`
empty. So the property this file drives is not *"can code compute 446"*; it is:

    a numeral bound to a derived fact is treated exactly as a numeral bound to an observed
    fact — no more trusted and no less checked

and every test here is one half of that sentence.

**Its own fixtures, and separate from `test_story_deterministic_verifier.py` on purpose.** That
file is the cross-metric-divergence candidate and 136 tests of §13.1–§13.15 over it; the two
things a derived fact needs that it cannot supply are a **two-period** movement in USD and a
metric whose two readings sit in different table columns. Retrofitting would have moved every
offset in a file whose offsets are the point. The gap sentence there is migrated to a derived
binding and stays there, which is what proves the two files agree.

The values are real: `adjusted_gross_profit` is `556000000.0` at 2022Q2 and `110000000.0` at
2022Q3, and the candidate's own `delta` signal is `-446000000.0` *(DETERMINISTIC_FACT_TOOLS §2,
read from `data/story_demo/`)*. The observation-id digests are hand-written opaque handles, as
in the sibling file: nothing here recomputes one.
"""

from __future__ import annotations

import pytest

from story.core.keys import (
    derived_fact_id,
    evidence_scope_fact_id,
    package_content_digest,
)
from story.core.models import (
    BudgetParameters,
    Calculation,
    DerivationOperation,
    DerivedFact,
    DisplaySemantics,
    Draft,
    DraftSentence,
    EditorialPlan,
    EvidenceScopeFact,
    EvidenceRole,
    FactBinding,
    KeyPoint,
    PackageBudget,
    PackagedDocument,
    PackagedFact,
    PackagedMetric,
    PackagedPassage,
    PackagedSubject,
    PassageCitation,
    SentenceKind,
    StatementClass,
    StoryCandidate,
    StoryEvidencePackage,
)
from story.core.numerals import CHANGE_VERBS
from story.stages.verification import GATE, DeterministicVerifier
import story.stages.verification.derived_facts as derived_rules
import story.stages.verification.language as language
from story.stages.verification.package_index import (
    DERIVED_FACT_PREFIX,
    EVIDENCE_SCOPE_PREFIX,
    PackageIndex,
)
# The two ends of one contract, imported from both sides deliberately: a stage may not import a
# sibling stage, and a **test** is where the agreement between the offer set and the verifier is
# asserted rather than hoped for.
from story.stages.derivation.execute import execute
from story.stages.derivation.offers import OFFERABLE_OPERATIONS, offers
from story.stages.detection import detector_config

from conftest import make_candidate

# ---------------------------------------------------------------------------------------
# The §2 candidate, as a package
# ---------------------------------------------------------------------------------------

CANDIDATE_ID = (
    "cand:metric-move:adjusted-gross-profit:opendoor:2022Q2_2022Q3:86ba9e13455d")
PACKAGE_ID = "pkg:metric-move-adjusted-gross-profit-opendoor-2022q2-2022q3:1f0c74ab9e35"
TOOL_VERSION = "1.0.0"

Q2_ID = "obs:adjusted-gross-profit:opendoor:2022Q2:normalized-table:0c4364ebbc44"
Q3_ID = "obs:adjusted-gross-profit:opendoor:2022Q3:normalized-table:4d66ef7200e9"
MARGIN_Q3_ID = "obs:adjusted-gross-margin:opendoor:2022Q3:normalized-table:7c1a04e9b2d5"

PASSAGE_ID = "psg:opendoor-10q-2022q3:agp-table"
DOCUMENT_ID = "doc:opendoor-10q-2022q3"

#: Two period columns on their own lines, so each fact carries its **own** `column_label` and
#: §13.7.1's ambiguity rule has nothing to fire on. The corpus's own shape puts them side by
#: side; that is `test_story_deterministic_verifier.py`'s grid fixture's job, and this one only
#: has to be unambiguous.
PASSAGE_TEXT = (
    "Adjusted Gross Profit (in millions)\n"
    "Three Months Ended June 30, 2022 556\n"
    "Three Months Ended September 30, 2022 110\n"
)
Q2_COLUMN = "Three Months Ended June 30, 2022"
Q3_COLUMN = "Three Months Ended September 30, 2022"


def _span(needle: str) -> tuple[int, int]:
    start = PASSAGE_TEXT.index(needle)
    return start, start + len(needle)


def make_fact(**overrides: object) -> PackagedFact:
    fields: dict[str, object] = dict(
        observation_id=Q3_ID,
        metric_id="adjusted_gross_profit",
        metric_label="Adjusted Gross Profit",
        period_key="2022Q3",
        period_start="2022-07-01",
        period_end="2022-09-30",
        shape="duration",
        value=110_000_000.0,
        unit="USD",
        currency="USD",
        scale="millions",
        row_label="Adjusted Gross Profit",
        column_label=Q3_COLUMN,
        source_lane="normalized_table",
        validation_state="ok",
        passage_id=PASSAGE_ID,
        document_id=DOCUMENT_ID,
        quoted_text="110",
    )
    fields.update(overrides)
    return PackagedFact(**fields)  # type: ignore[arg-type]


Q3_FACT = make_fact()
Q2_FACT = make_fact(
    observation_id=Q2_ID, period_key="2022Q2", period_start="2022-04-01",
    period_end="2022-06-30", value=556_000_000.0, column_label=Q2_COLUMN, quoted_text="556")

#: Read off the rows, never spelled: a handle written by hand in a test is a second authority on
#: a format §3.4 check 7 exists to say there is only one of.
Q2_HANDLE = Q2_FACT.evidence_handle
Q3_HANDLE = Q3_FACT.evidence_handle

METRICS = (
    PackagedMetric(metric_id="adjusted_gross_profit", label="Adjusted Gross Profit",
                   unit="USD", allowed_units=("USD",),
                   aliases=("adjusted gross profit",)),
    PackagedMetric(metric_id="adjusted_gross_margin", label="Adjusted Gross Margin",
                   unit="percent", allowed_units=("percent",),
                   aliases=("adjusted gross margin",)),
)


def make_package(**overrides: object) -> StoryEvidencePackage:
    fields: dict[str, object] = dict(
        package_id=PACKAGE_ID,
        candidate_id=CANDIDATE_ID,
        detector_id="detector:metric_move",
        detector_version="1.0.0",
        policy_version="canon-policy:1.0.0",
        graph_run_id="graph-v1-0483dc6b4b10",
        graph_projection_version="1.2.0",
        extraction_run_id="extract-v1-lexical-833f7bcfbce9",
        run_complete_sha256="1cc8f7b01c040531" + "0" * 48,
        ontology_id="real_estate_marketplace_v1",
        ontology_definition_hash="bb" * 32,
        ontology_semantic_version="2.0.0",
        subject=PackagedSubject(entity_id="opendoor", entity_text="Opendoor Technologies Inc.",
                                resolved=True, labels=("Entity", "PublicCompany")),
        facts=(Q2_FACT, Q3_FACT),
        metrics=METRICS,
        primary_passages=(PackagedPassage(
            passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, text=PASSAGE_TEXT,
            char_count=len(PASSAGE_TEXT), passage_kind="normalized_table",
            role=EvidenceRole.PRIMARY_SUPPORT),),
        documents=(PackagedDocument(document_id=DOCUMENT_ID, form="10-Q",
                                    document_type="10-Q", filing_date="2022-11-03"),),
        budget=PackageBudget(artifact_token_estimate=1180, prompt_token_estimate=1046,
                             section_counts={"facts": 2}, parameters=BudgetParameters()),
    )
    fields.update(overrides)
    package = StoryEvidencePackage(**fields)  # type: ignore[arg-type]
    return package.with_content_digest(package_content_digest(package.digestible_payload()))


# ---------------------------------------------------------------------------------------
# The derived fact, and the sentence that binds it
# ---------------------------------------------------------------------------------------


def make_derived(**overrides: object) -> DerivedFact:
    """`$556M -> $110M` as §4 computes it. `-$446M`, in USD, *"decreased by"*."""
    fields: dict[str, object] = dict(
        operation=DerivationOperation.ABSOLUTE_CHANGE,
        package_id=PACKAGE_ID,
        from_fact_id=Q2_ID, to_fact_id=Q3_ID,
        from_period="2022Q2", to_period="2022Q3",
        from_value=556_000_000.0, to_value=110_000_000.0,
        result=-446_000_000.0,
        unit="USD", currency="USD",
        display_semantics=DisplaySemantics.DECREASED_BY,
        metric_id="adjusted_gross_profit", from_metric_id="adjusted_gross_profit",
        metric_surfaces=("Adjusted Gross Profit",),
        period_surface_hint="the third quarter of 2022",
        comparability_rule_ids=("R1", "R2", "R3", "R4", "R5", "R7", "R8", "R10"),
        tool_version=TOOL_VERSION,
        reused_detector_signal="delta",
    )
    fields.update(overrides)
    fields.setdefault("fact_id", derived_fact_id(
        operation=str(getattr(fields["operation"], "value", fields["operation"])),
        subject_entity_id="opendoor",
        metric_ids=(str(fields["from_metric_id"]), str(fields["metric_id"])),
        period_keys=(str(fields["from_period"]), str(fields["to_period"])),
        from_fact_id=str(fields["from_fact_id"]), to_fact_id=str(fields["to_fact_id"]),
        package_id=str(fields["package_id"]), tool_version=TOOL_VERSION))
    return DerivedFact(**fields)  # type: ignore[arg-type]


FALL = make_derived()

FALL_TEXT = "Adjusted gross profit fell $446 million in the third quarter of 2022."


def _citation(fact: PackagedFact, needle: str, **overrides: object) -> PassageCitation:
    start, end = _span(needle)
    fields: dict[str, object] = dict(
        passage_id=PASSAGE_ID, document_id=DOCUMENT_ID, char_start=start, char_end=end,
        evidence_handle=fact.evidence_handle)
    fields.update(overrides)
    return PassageCitation(**fields)  # type: ignore[arg-type]


Q2_CITATION = _citation(Q2_FACT, "Three Months Ended June 30, 2022 556")
Q3_CITATION = _citation(Q3_FACT, "Three Months Ended September 30, 2022 110")


def fall_sentence(
    derived: DerivedFact = FALL, text: str = FALL_TEXT, **overrides: object
) -> DraftSentence:
    """The sentence §2 could not make checkable, in the shape §6 makes it checkable in.

    Every field the model used to have to remember is code's: the number is
    `DerivedFact.result`, the period surface is `period_surface_hint`, and the citations are the
    two cells the inputs were read from.
    """
    rendered = str(overrides.pop("rendered", "$446 million"))
    fields: dict[str, object] = dict(
        index=0, text=text, kind=SentenceKind.CALCULATED,
        fact_bindings=(FactBinding(
            fact_id=derived.fact_id, rendered=rendered,
            char_start=text.index(rendered), char_end=text.index(rendered) + len(rendered),
            metric_surface="Adjusted gross profit",
            period_surface=derived.period_surface_hint),),
        citations=(Q2_CITATION, Q3_CITATION),
    )
    fields.update(overrides)
    return DraftSentence(**fields)  # type: ignore[arg-type]


def _rebound(text: str, *, rendered: str = "$446 million", **binding: object) -> DraftSentence:
    """`fall_sentence` with one binding field moved, for the rules that read that field."""
    fields: dict[str, object] = dict(
        fact_id=FALL.fact_id, rendered=rendered,
        char_start=text.index(rendered), char_end=text.index(rendered) + len(rendered),
        metric_surface="Adjusted gross profit",
        period_surface=FALL.period_surface_hint)
    fields.update(binding)
    return DraftSentence(
        index=0, text=text, kind=SentenceKind.CALCULATED,
        fact_bindings=(FactBinding(**fields),),  # type: ignore[arg-type]
        citations=(Q2_CITATION, Q3_CITATION))


def make_draft(sentences=None, **overrides: object) -> Draft:  # type: ignore[no-untyped-def]
    fields: dict[str, object] = dict(
        candidate_id=CANDIDATE_ID,
        package_id=PACKAGE_ID,
        sentences=tuple(sentences) if sentences is not None else (fall_sentence(),),
        style_profile_id="investor-plain:1",
    )
    fields.update(overrides)
    return Draft(**fields)  # type: ignore[arg-type]


def make_plan(**overrides: object) -> EditorialPlan:
    fields: dict[str, object] = dict(
        candidate_id=CANDIDATE_ID,
        package_id=PACKAGE_ID,
        thesis="Adjusted gross profit fell $446 million between 2022Q2 and 2022Q3.",
        why_it_matters="The fall is the quarter's whole story.",
        key_points=(KeyPoint(claim="Adjusted gross profit was $110 million in 2022Q3.",
                             required_fact_ids=(Q3_ID,),
                             required_citation_passage_ids=(PASSAGE_ID,),
                             statement_class=StatementClass.REPORTED),),
    )
    fields.update(overrides)
    return EditorialPlan(**fields)  # type: ignore[arg-type]


@pytest.fixture
def verifier() -> DeterministicVerifier:
    return DeterministicVerifier()


def codes(draft, package=None, plan=None, derived=(FALL,)):  # type: ignore[no-untyped-def]
    verified = DeterministicVerifier().verify(
        draft, package or make_package(), plan or make_plan(), derived)
    return {found.code for found in verified.all_findings}


# ---------------------------------------------------------------------------------------
# The property, in one test
# ---------------------------------------------------------------------------------------


def test_the_bound_derived_numeral_clears_unbound_numeral_and_the_whole_gate(verifier):
    """§13.1's mechanism needs no extension: a derived fact binds like any other.

    **This is DETERMINISTIC_FACT_TOOLS §13.1's claim, confirmed by test rather than asserted.**
    The plan says the coverage rule is *"unchanged — a derived fact binds like any other, so its
    numeral is covered by the binding span, the mechanism that already exists"*. It is: the span
    covers `$446 million` and the declared `period_surface` covers the `2022` that refused the
    original run. Both mechanisms are `_covering_spans`', untouched by this change.
    """
    verified = verifier.verify(make_draft(), make_package(), make_plan(), (FALL,))
    assert verified.all_findings == ()
    assert verified.passed is True
    # The two numerals the original run had to account for, both accounted for.
    assert verified.check("numbers").examined == 2


def test_the_derived_numeral_reaches_the_evidence_panel_and_the_derivation_panel(verifier):
    """§13.17's two ledgers. The derived row carries its inputs, so a reader can follow it."""
    verified = verifier.verify(make_draft(), make_package(), make_plan(), (FALL,))
    assert [entry.fact_id for entry in verified.fact_ledger] == [FALL.fact_id]
    entry = verified.calculation_ledger[0]
    assert entry.operation == "absolute_change"
    assert entry.input_observation_ids == (Q2_ID, Q3_ID)
    assert entry.recomputed_value == -446_000_000.0


def test_a_derived_binding_is_checked_by_every_rule_an_observed_binding_is(verifier):
    """The other half of the property: *no less checked*.

    Five rules, each driven through the derived path and each raising the code it raises for an
    observation — the span, the rendering, the tolerance, the metric surface and the period.
    """
    text = FALL_TEXT
    wrong_span = fall_sentence(
        fact_bindings=(FactBinding(
            fact_id=FALL.fact_id, rendered="$446 million", char_start=0, char_end=12,
            metric_surface="Adjusted gross profit",
            period_surface="the third quarter of 2022"),))
    assert "binding_span_does_not_match_text" in codes(make_draft(sentences=(wrong_span,)))

    two_numerals = "Adjusted gross profit fell $446 million to $110 million in 2022Q3."
    assert "binding_rendering_is_not_one_numeral" in codes(make_draft(sentences=(
        fall_sentence(text=two_numerals, rendered="$446 million to $110 million"),)))

    off = text.replace("$446 million", "$412 million")
    assert "number_outside_tolerance" in codes(make_draft(sentences=(
        fall_sentence(text=off, rendered="$412 million"),)))

    assert "metric_binding_mismatch" in codes(make_draft(sentences=(
        _rebound(text, metric_surface="Adjusted gross margin"),)))

    fourth = text.replace("third quarter", "fourth quarter")
    assert "period_mismatch" in codes(make_draft(sentences=(
        _rebound(fourth, period_surface="the fourth quarter of 2022"),)))


# ---------------------------------------------------------------------------------------
# §13.4 — orientation, which nothing checked before
# ---------------------------------------------------------------------------------------


def test_a_derivation_whose_inputs_run_backwards_in_time_is_refused():
    """**A new guarantee, not a restatement of one, and the measurement is why.**

    Nothing in §13 has ever checked that a two-input operation's first input is the earlier
    reading. The convention is a docstring — `deterministic._recompute`'s *"input order is
    `(base, subject)`"* — and for `compare_levels` and `compare_deltas` that function returns
    `abs(...)`, so a swapped pair recomputes to the identical number and the swap is invisible.
    Here the swap is the whole claim: `$110M -> $556M` is `+$446M` and *"increased by"*.
    """
    reversed_fact = make_derived(
        from_fact_id=Q3_ID, to_fact_id=Q2_ID,
        from_period="2022Q3", to_period="2022Q2",
        from_value=110_000_000.0, to_value=556_000_000.0,
        result=446_000_000.0,
        display_semantics=DisplaySemantics.INCREASED_BY,
        period_surface_hint="the second quarter of 2022")
    text = "Adjusted gross profit rose $446 million in the second quarter of 2022."
    found = codes(
        make_draft(sentences=(fall_sentence(reversed_fact, text=text),)),
        derived=(reversed_fact,))
    assert "derived_fact_orientation_reversed" in found


def test_a_sentence_that_states_the_opposite_direction_is_refused():
    """§6: `display_semantics` is code's word, and the prose has to be the same word.

    The number is right, the period is right, the metric is right, and *"rose"* is the opposite
    of what the derivation says. Before this, nothing in §13.1–§13.5 read that word.
    """
    text = FALL_TEXT.replace("fell", "rose")
    assert "derived_fact_orientation_reversed" in codes(
        make_draft(sentences=(fall_sentence(text=text),)))


def test_a_change_verb_the_lexicon_gives_no_polarity_states_no_direction():
    """`CHANGE_DIRECTION`'s `None`s do not *contradict* the fact — and no longer excuse it.

    *"Improved"* is a statement about the quantity's desirability and not about its sign —
    `direct_selling_costs` is stored negative on 46 of 46 canonical values, so a cost that
    improves is a *rise* in the stored number — so refusing it as **reversed** would invent a
    claim the sentence did not make. That half is unchanged.

    **What changed at H1 is where the abstention goes.** The sentence still has to say which way
    the quantity moved, and a word that states desirability has not said it, so the draft is
    refused for stating no direction rather than accepted for stating none. The old assertion —
    `derived_fact_orientation_reversed not in codes` — was true then and is true now, and on its
    own it was the shape the review walked through six times.
    """
    text = FALL_TEXT.replace("fell", "improved")
    found = codes(make_draft(sentences=(fall_sentence(text=text),)))
    assert "derived_fact_orientation_reversed" not in found
    assert "derived_direction_not_stated_in_text" in found


def test_the_change_verb_lexicon_states_a_polarity_for_every_verb_numerals_knows():
    """Totality, asserted rather than trusted — `COMPARATIVE_DIRECTION`'s own discipline.

    A widened lexicon with an unstated polarity would turn a refusal into a pass at exactly the
    place this map guards.
    """
    assert set(CHANGE_VERBS) <= set(language.CHANGE_DIRECTION)


def test_a_same_period_operation_over_two_periods_is_refused():
    """§4.1: `compare_levels` and `ratio` compare two readings of **one** period."""
    across = make_derived(
        operation=DerivationOperation.COMPARE_LEVELS,
        result=-446_000_000.0, display_semantics=DisplaySemantics.LOWER_THAN)
    assert "derived_fact_orientation_reversed" in codes(
        make_draft(sentences=(fall_sentence(across),)), derived=(across,))


def test_a_derived_fact_whose_declared_periods_are_not_its_inputs_is_refused():
    """§4.4: `from_period`/`to_period` are what a binding's `period_surface` is filled from."""
    mislabelled = make_derived(from_period="2022Q1")
    assert "derived_fact_orientation_reversed" in codes(
        make_draft(sentences=(fall_sentence(mislabelled),)), derived=(mislabelled,))


# ---------------------------------------------------------------------------------------
# §6 — the result, the unit and the operation
# ---------------------------------------------------------------------------------------


def test_a_result_that_is_not_what_the_tool_computes_is_refused():
    """The verifier re-derives from the package's own values and does not read `result`.

    `$556M -> $110M` is `-446000000`, whatever the artifact says. Two code paths computing one
    number and differing is a defect in one of them.
    """
    wrong = make_derived(result=-412_000_000.0)
    text = FALL_TEXT.replace("$446 million", "$412 million")
    found = codes(make_draft(sentences=(fall_sentence(wrong, text=text,
                                                      rendered="$412 million"),)),
                  derived=(wrong,))
    assert "derived_result_mismatch" in found


def test_a_result_with_the_wrong_sign_is_refused():
    """The sign **is** the claim on a change: `+446,000,000` is the opposite quarter's story."""
    flipped = make_derived(result=446_000_000.0,
                           display_semantics=DisplaySemantics.INCREASED_BY)
    assert "derived_result_mismatch" in codes(
        make_draft(sentences=(fall_sentence(flipped),)), derived=(flipped,))


def test_a_written_minus_that_disagrees_with_the_derived_result_is_refused():
    """§13.1's sign check, on the derived path. The numeral carried its own sign, so it applies.

    Driven against the reversed derivation because **every** correct derivation over this
    package's two facts is negative — `$556M -> $110M` is a fall by every operation §4.1
    defines — so a positive result to disagree with has to be the other orientation. The draft
    is refused twice over and that is the point: the sign check reaches a derived binding
    exactly as it reaches an observed one, on top of the orientation rule.
    """
    text = "Adjusted gross profit changed by -$446 million in the second quarter of 2022."
    plus = make_derived(
        from_fact_id=Q3_ID, to_fact_id=Q2_ID,
        from_period="2022Q3", to_period="2022Q2",
        from_value=110_000_000.0, to_value=556_000_000.0,
        result=446_000_000.0,
        display_semantics=DisplaySemantics.INCREASED_BY,
        period_surface_hint="the second quarter of 2022")
    found = codes(
        make_draft(sentences=(fall_sentence(plus, text=text, rendered="-$446 million"),)),
        derived=(plus,))
    assert "sign_disagreement" in found
    assert "derived_fact_orientation_reversed" in found


def test_a_unit_the_operation_does_not_produce_is_refused():
    """§4.1's result-unit column, re-derived. `absolute_change` over USD is USD and nothing else."""
    mislabelled = make_derived(unit="percent", currency=None)
    assert "derived_unit_mismatch" in codes(
        make_draft(sentences=(fall_sentence(mislabelled),)), derived=(mislabelled,))


def test_a_currency_that_survived_a_division_is_refused():
    """§13.2: a percentage, a multiple and a direction are dimensionless."""
    dimensionless = make_derived(
        operation=DerivationOperation.PERCENTAGE_CHANGE, unit="percent", currency="USD",
        result=-80.215827338)
    assert "derived_unit_mismatch" in codes(
        make_draft(sentences=(fall_sentence(dimensionless),)), derived=(dimensionless,))


def test_percent_and_percentage_points_are_separate_operations_at_the_fact_level():
    """§13.3's *"single most likely factual error this package can make"*, made unrepresentable.

    A relative change of two USD levels is a **percent**; declaring its result in percentage
    points is the confusion, and §4.1's table is where it is refused. On
    `adjusted_ebitda_margin` 5.2 -> 2.2 the two readings differ by 19.2x.
    """
    confused = make_derived(
        operation=DerivationOperation.PERCENTAGE_CHANGE,
        unit="percentage_points", currency=None, result=-80.215827338)
    assert "derived_unit_mismatch" in codes(
        make_draft(sentences=(fall_sentence(confused),)), derived=(confused,))


def test_percent_and_percentage_points_are_separate_surfaces_at_the_numeral():
    """The same confusion one layer down: a `percent` result rendered in points.

    `basis_points` is refused for both, in either direction — the same quantity at a hundred
    times the number is a different claim and not a rendering.
    """
    relative = make_derived(
        operation=DerivationOperation.PERCENTAGE_CHANGE, unit="percent", currency=None,
        result=-80.215827338)
    text = "Adjusted gross profit fell 80.2 percentage points in the third quarter of 2022."
    assert "derived_unit_mismatch" in codes(
        make_draft(sentences=(fall_sentence(relative, text=text,
                                            rendered="80.2 percentage points"),)),
        derived=(relative,))

    bps = text.replace("80.2 percentage points", "80.2 bps")
    assert "derived_unit_mismatch" in codes(
        make_draft(sentences=(fall_sentence(relative, text=bps, rendered="80.2 bps"),)),
        derived=(relative,))


def test_an_operation_the_verifier_cannot_recompute_is_refused():
    """§13.9's rule, applied to a derived fact.

    `trend_direction`'s answer is `detector_config.quantity_direction`'s — the metric's stored
    sign convention, in a sibling stage this one may not import — so the verifier cannot
    re-derive it and says so. *"An unimplemented rule that silently passes is worse than one
    that refuses."*
    """
    word = make_derived(
        operation=DerivationOperation.TREND_DIRECTION, unit="direction", currency=None,
        result=None, result_word="decrease",
        display_semantics=DisplaySemantics.DECREASED)
    text = "Adjusted gross profit decreased in the third quarter of 2022."
    sentence = DraftSentence(
        index=0, text=text, kind=SentenceKind.CALCULATED,
        fact_bindings=(FactBinding(
            fact_id=word.fact_id, rendered="decreased",
            char_start=text.index("decreased"),
            char_end=text.index("decreased") + len("decreased"),
            metric_surface="Adjusted gross profit",
            period_surface="the third quarter of 2022"),),
        citations=(Q2_CITATION, Q3_CITATION))
    assert "derived_operation_not_supported" in codes(
        make_draft(sentences=(sentence,)), derived=(word,))


def test_a_word_valued_derivation_may_not_be_rendered_as_a_numeral():
    """§4.4: a boolean rendered as `1.0` is a number §13.1 would then compare against the prose."""
    flag = make_derived(
        operation=DerivationOperation.CROSSED_ZERO, unit="boolean", currency=None,
        result=None, result_word="did_not_cross",
        display_semantics=DisplaySemantics.DID_NOT_CROSS_ZERO)
    text = "Adjusted gross profit stayed above 0 in the third quarter of 2022."
    sentence = DraftSentence(
        index=0, text=text, kind=SentenceKind.CALCULATED,
        fact_bindings=(FactBinding(
            fact_id=flag.fact_id, rendered="0",
            char_start=text.index(" 0 ") + 1, char_end=text.index(" 0 ") + 2,
            metric_surface="Adjusted gross profit",
            period_surface="the third quarter of 2022"),),
        citations=(Q2_CITATION, Q3_CITATION))
    assert "derived_unit_mismatch" in codes(make_draft(sentences=(sentence,)), derived=(flag,))


def test_a_derivation_over_incomparable_inputs_is_refused():
    """§4.2: a derivation is permitted only where §6.9's R1–R10 permit the comparison.

    A dollar figure against a percentage is R4, and it is re-derived here through
    `story/core/series.py` rather than read off the fact's own `comparability_rule_ids` — a
    verifier that took the producer's record of its own validation would be checking nothing.
    """
    margin = make_fact(observation_id=MARGIN_Q3_ID, metric_id="adjusted_gross_margin",
                       metric_label="Adjusted Gross Margin", value=3.3, unit="percent",
                       currency=None, scale="units", row_label="Adjusted Gross Margin",
                       quoted_text="3.3")
    across_units = make_derived(from_fact_id=MARGIN_Q3_ID, from_period="2022Q3",
                                from_value=3.3, from_metric_id="adjusted_gross_margin",
                                result=110_000_000.0 - 3.3)
    found = codes(
        make_draft(sentences=(fall_sentence(across_units, rendered="$446 million"),)),
        package=make_package(facts=(Q2_FACT, Q3_FACT, margin)),
        derived=(across_units,))
    assert "derived_inputs_incomparable" in found


# ---------------------------------------------------------------------------------------
# §13.13 and §4.3 — a derived id that resolves nowhere
# ---------------------------------------------------------------------------------------


def test_a_binding_to_a_derived_fact_this_run_did_not_produce_is_refused():
    """§3: a derived fact travels as a separate artifact and resolves there or nowhere.

    `derived_facts=()` is the run that lost its derivations, and it refuses the post rather than
    passing the numerals it can still resolve.
    """
    assert "derived_fact_not_in_run" in codes(make_draft(), derived=())


def test_a_derived_fact_computed_from_another_package_is_refused():
    """§4.4: `package_id` is inside the id's digest so a derivation cannot outlive its evidence."""
    stale = make_derived(package_id="pkg:something-else:0000deadbeef")
    assert "derived_fact_not_in_run" in codes(
        make_draft(sentences=(fall_sentence(stale),)), derived=(stale,))


def test_a_derivation_over_an_input_this_package_does_not_carry_is_not_offered():
    """§4.3's offer set is every triple over **this package's facts**, so this is in none of it."""
    orphan = make_derived(from_fact_id="obs:adjusted-gross-profit:opendoor:2021Q4:"
                                       "normalized-table:aaaaaaaaaaaa")
    assert "derivation_not_offered" in codes(
        make_draft(sentences=(fall_sentence(orphan),)), derived=(orphan,))


# ---------------------------------------------------------------------------------------
# §13.7 — citations stay attached to the observed facts
# ---------------------------------------------------------------------------------------


def test_the_table_citation_stays_on_the_two_observations_the_derivation_rests_on():
    """§6: *"a citation supporting a derived fact is one that supports an input fact of it"*.

    The clean draft cites both cells and raises nothing — `_support_findings`,
    `_coverage_findings` and check 7 all resolve the derived binding through its inputs.
    """
    assert codes(make_draft()) == set()


def test_a_derivation_that_evidences_only_one_of_its_two_inputs_is_refused():
    """Both readings went into the number, so both have to be pointed at.

    §3.4 check 7 asks only that a handle belong to *a* fact the sentence binds, so one handle
    would satisfy it for every binding; a sentence stating a fall computed from two figures and
    evidencing one is provenance the evidence does not supply.
    """
    assert "uncited_factual_sentence" in codes(make_draft(sentences=(
        fall_sentence(citations=(Q3_CITATION,)),)))


def test_no_evidence_handle_is_ever_minted_for_a_derived_fact():
    """§6's flat prohibition, asserted on the type and on the package's own index.

    Five layers downstream assume a cited thing was read from a filing, so a handle for a
    computed quantity would be a citation into a filing that never printed it.
    """
    assert "evidence_handle" not in DerivedFact.model_fields
    assert FALL.fact_id not in make_package().facts_by_evidence_handle()


def test_a_citation_to_a_passage_no_input_was_read_from_is_refused():
    """§13.9, narrowed by §6: the inputs' cells, and nothing else.

    The 2022Q3 *margin* is a real fact of a real filing and no input of this derivation.
    """
    margin = make_fact(observation_id=MARGIN_Q3_ID, metric_id="adjusted_gross_margin",
                       metric_label="Adjusted Gross Margin", value=3.3, unit="percent",
                       currency=None, scale="units", row_label="Adjusted Gross Margin",
                       quoted_text="3.3")
    sentence = fall_sentence(citations=(
        Q2_CITATION, Q3_CITATION,
        _citation(margin, "Adjusted Gross Profit", evidence_handle=margin.evidence_handle)))
    found = codes(make_draft(sentences=(sentence,)),
                  package=make_package(facts=(Q2_FACT, Q3_FACT, margin)))
    assert "calculated_sentence_cites_passage" in found


def test_a_calculated_sentence_that_binds_no_derived_fact_still_refuses_every_citation():
    """The narrowed rule's floor: with an empty permitted set it is the rule it replaced."""
    sentence = DraftSentence(index=0, text="The two quarters differ.",
                             kind=SentenceKind.CALCULATED, citations=(Q3_CITATION,))
    found = codes(make_draft(sentences=(sentence,)))
    assert "calculated_sentence_cites_passage" in found
    assert "calculated_sentence_without_calculation" in found


# ---------------------------------------------------------------------------------------
# §13.9 — the retired Calculation
# ---------------------------------------------------------------------------------------


def test_a_draft_that_still_declares_a_calculation_is_refused():
    """§6: the type survives for artifact back-compatibility and a draft carrying one does not.

    A writer-declared operation is the model doing the arithmetic with code checking its
    homework, which is the arrangement §1 replaces.
    """
    sentence = fall_sentence(calculation=Calculation(
        operation="difference", input_observation_ids=(Q2_ID, Q3_ID),
        expression="to - from", result_rendered="$446 million"))
    assert "reported_sentence_carries_calculation" in codes(make_draft(sentences=(sentence,)))


def test_a_calculated_sentence_with_no_derived_binding_is_refused():
    """§6 replaces `calculated_sentence_without_calculation` and keeps its name and its force."""
    sentence = DraftSentence(index=0, text="The fall was the quarter's story.",
                             kind=SentenceKind.CALCULATED)
    assert "calculated_sentence_without_calculation" in codes(make_draft(sentences=(sentence,)))


# ---------------------------------------------------------------------------------------
# §7 — evidence-scope facts
# ---------------------------------------------------------------------------------------

SCOPE_STATEMENT = (
    "The evidence in this package supplies no explanation for what is described here: it "
    "carries no explanatory passage, its causal language is forbidden, and none of its facts "
    "is marked as explaining another. State what the figures are; do not state why they moved, "
    "and do not imply a reason by juxtaposition.")

SCOPE = EvidenceScopeFact(
    fact_id=evidence_scope_fact_id(claim="no_supported_causal_explanation_in_package",
                                   package_id=PACKAGE_ID, tool_version=TOOL_VERSION),
    claim="no_supported_causal_explanation_in_package",
    statement=SCOPE_STATEMENT,
    package_id=PACKAGE_ID,
    tool_version=TOOL_VERSION,
    examined_fact_ids=tuple(sorted((Q2_ID, Q3_ID))),
)

SCOPE_TEXT = "The evidence in this package supplies no explanation for the fall."


def scope_sentence(text: str = SCOPE_TEXT, rendered: str | None = None,
                   **overrides: object) -> DraftSentence:
    """§7's binding, with **both surfaces empty**, which is the only shape H1 accepts.

    They were `"Adjusted gross profit"` and `"the third quarter of 2022"` here until H1, and
    that fixture was the review's fourth finding in miniature: an evidence-scope fact is of no
    metric and over no period, `_check_metric_identity`, `_check_units` and `_check_periods` all
    resolve neither branch for it and `continue`, and `_covering_spans` was reading the period
    string anyway. See `test_an_evidence_scope_binding_may_declare_no_metric_and_no_period`.
    """
    span = rendered or "The evidence in this package supplies no explanation"
    fields: dict[str, object] = dict(
        index=0, text=text, kind=SentenceKind.EXPLANATORY,
        fact_bindings=(FactBinding(
            fact_id=SCOPE.fact_id, rendered=span,
            char_start=text.index(span), char_end=text.index(span) + len(span),
            metric_surface="", period_surface=""),),
    )
    fields.update(overrides)
    return DraftSentence(**fields)  # type: ignore[arg-type]


def test_an_evidence_scope_fact_supports_bounded_uncertainty_wording_with_no_citation():
    """§7: a claim about the *evidence*, carrying no citation, and refused nothing.

    The sentence says what the package does not contain and nothing about the world, so there is
    no filed passage to cite and none is required — which is the whole reason the type carries
    no `citations` field.
    """
    assert codes(make_draft(sentences=(scope_sentence(),)), derived=(SCOPE,)) == set()


def test_an_evidence_scope_fact_cannot_be_used_to_claim_something_about_the_world():
    """The statement is the only text that licenses this claim, so a word it does not carry is
    a claim the package cannot establish. Rule B's lexical test, with the statement in place of
    a cited span."""
    text = "There was no underlying deterioration in the business."
    span = "no underlying deterioration in the business"
    found = codes(make_draft(sentences=(scope_sentence(text=text, rendered=span),)),
                  derived=(SCOPE,))
    assert "uncited_factual_sentence" in found


def test_a_financial_table_citation_may_not_be_attached_to_an_evidence_scope_fact():
    """**The exact failure §7 names.** Today the *"no explanation was disclosed"* sentence reuses
    a financial-table citation as though the table said it, and
    `counter_evidence_cited_as_support` and `citation_reused_for_unrelated_claim` are all that
    stand between a draft and the claim."""
    found = codes(make_draft(sentences=(scope_sentence(citations=(Q3_CITATION,)),)),
                  derived=(SCOPE,))
    assert "citation_does_not_support_fact" in found


def test_an_evidence_scope_binding_may_state_no_number():
    """It holds no value, so a numeral in its span is a quantity §13.1 cannot compare."""
    text = "The evidence in this package supplies no explanation for the $446 million fall."
    span = "no explanation for the $446 million"
    found = codes(make_draft(sentences=(scope_sentence(text=text, rendered=span),)),
                  derived=(SCOPE,))
    assert "binding_rendering_is_not_one_numeral" in found


# ---------------------------------------------------------------------------------------
# The gate, and the tables that must not drift from the stage that produces them
# ---------------------------------------------------------------------------------------


def test_the_seven_new_codes_are_in_the_gate_and_every_one_refuses():
    """§6 names all seven as REFUSE, and `finding()` raises `UndeclaredCode` for anything else."""
    for code in ("derivation_not_offered", "derived_fact_not_in_run",
                 "derived_fact_orientation_reversed", "derived_result_mismatch",
                 "derived_inputs_incomparable", "derived_operation_not_supported",
                 "derived_unit_mismatch"):
        assert GATE[code].blocking is True


def test_the_verifiers_derived_tables_agree_with_the_stage_that_produces_the_facts():
    """The second opinion has to be a second opinion **about the same thing**.

    §4.1's operation table, §4.4's arithmetic and the two restated word constants are
    implemented twice on purpose — a stage may not import a sibling stage, and a verifier that
    asked the producer *"is this right?"* would be asking the defendant. A test may import both,
    and this is where drift is caught.
    """
    # The names, not the modules: `story.stages.derivation.__init__` re-exports `offers` and
    # `execute` as **functions**, so `import story.stages.derivation.offers as offers` binds the
    # function and not the module. That collision is recorded in the package's own docstring.
    from story.stages.derivation import (
        ADMITTED_UNITS,
        CORPUS_UNITS,
        SAME_PERIOD_OPERATIONS,
        TOOL_VERSION as STAGE_TOOL_VERSION,
        TWO_PERIOD_OPERATIONS,
    )
    import story.stages.derivation.operations as operations

    assert derived_rules.CROSSED == operations.CROSSED
    assert derived_rules.DID_NOT_CROSS == operations.DID_NOT_CROSS
    assert TOOL_VERSION == STAGE_TOOL_VERSION
    assert derived_rules.TWO_PERIOD_OPERATIONS == TWO_PERIOD_OPERATIONS
    assert derived_rules.SAME_PERIOD_OPERATIONS == SAME_PERIOD_OPERATIONS

    # §4.1's unit table, both ways: what the verifier expects is what the stage would admit.
    for operation, admitted in ADMITTED_UNITS.items():
        for unit in CORPUS_UNITS:
            expected = derived_rules.expected_result_unit(operation, unit)
            allowed = admitted is None or unit in admitted
            assert (expected is not None) is allowed, (operation, unit)


def test_the_verifier_recomputes_the_same_number_the_operations_module_produces():
    """Driven through both implementations on the §2 candidate's own values."""
    import story.stages.derivation.operations as operations
    from story.stages.derivation.public import ValidatedDerivation
    from story.core.periods import story_period
    from story.core.series import CanonicalPoint, CanonicalStatus, ClaimKind

    def point(fact: PackagedFact) -> CanonicalPoint:
        return CanonicalPoint(
            metric_id=fact.metric_id,
            period=story_period(fact.period_start, fact.period_end, fact.instant_date),
            subject_entity_id="opendoor", status=CanonicalStatus.OK, value=fact.value,
            unit=fact.unit, scale=fact.scale, currency=fact.currency,
            representative_observation_id=fact.observation_id)

    from story.core.models import DerivationRequest
    validated = ValidatedDerivation(
        request=DerivationRequest(operation=DerivationOperation.ABSOLUTE_CHANGE,
                                  from_fact_id=Q2_ID, to_fact_id=Q3_ID),
        from_fact=Q2_FACT, to_fact=Q3_FACT,
        from_point=point(Q2_FACT), to_point=point(Q3_FACT),
        claim=ClaimKind.MOVEMENT, comparability_rule_ids=())
    produced = operations.absolute_change(validated, direction=lambda metric, delta: "decrease")
    checked, _word = derived_rules.recompute(
        DerivationOperation.ABSOLUTE_CHANGE, Q2_FACT.value, Q3_FACT.value)
    assert produced.result == checked == -446_000_000.0
    assert produced.unit == derived_rules.expected_result_unit(
        DerivationOperation.ABSOLUTE_CHANGE, "USD")


def test_the_derived_id_prefixes_this_module_branches_on_are_the_ones_keys_mints():
    """Restated constants, pinned against the module that mints the ids."""
    assert FALL.fact_id.startswith(DERIVED_FACT_PREFIX)
    assert SCOPE.fact_id.startswith(EVIDENCE_SCOPE_PREFIX)


def test_the_package_index_resolves_an_observed_or_a_derived_fact_and_keeps_them_apart():
    """§6's second map. `fact()` stays narrow so a check that was never taught fails closed."""
    index = PackageIndex(make_package(), (FALL, SCOPE))
    assert index.fact(Q3_ID) is Q3_FACT
    assert index.fact(FALL.fact_id) is None
    assert index.derived_fact(FALL.fact_id) is FALL
    assert index.scope_fact(SCOPE.fact_id) is SCOPE
    assert index.bound(FALL.fact_id) is FALL
    assert [fact.observation_id for fact in index.derived_inputs(FALL.fact_id)] == [Q2_ID, Q3_ID]
    assert index.derived_inputs(SCOPE.fact_id) == ()


# ---------------------------------------------------------------------------------------
# The repair packet after §11.4 — the offer set and the verifier, as one contract
# ---------------------------------------------------------------------------------------


def _offerable_packages() -> tuple[tuple[StoryEvidencePackage, StoryCandidate], ...]:
    """Three packages between them exercising every operation `offers` may print.

    One package cannot: `percentage_point_change` needs two **percent** readings in adjacent
    periods, `compare_levels` and `ratio` need two metrics in **one** period, and
    `absolute_change` refuses percent. So the shapes are built rather than sampled, and the test
    below asserts the union covers `OFFERABLE_OPERATIONS` — without which "every offered
    operation is bindable" would be satisfied by offering nothing.
    """
    usd = (make_package(), make_candidate(signals={}))

    margin_q2 = make_fact(
        observation_id="obs:adjusted-gross-margin:opendoor:2022Q2:normalized-table:aa11bb22cc33",
        metric_id="adjusted_gross_margin", metric_label="Adjusted Gross Margin",
        period_key="2022Q2", period_start="2022-04-01", period_end="2022-06-30",
        value=3.3, unit="percent", currency=None, scale="unit",
        row_label="Adjusted Gross Margin", column_label=Q2_COLUMN, quoted_text="3.3")
    margin_q3 = make_fact(
        observation_id=MARGIN_Q3_ID,
        metric_id="adjusted_gross_margin", metric_label="Adjusted Gross Margin",
        value=-12.6, unit="percent", currency=None, scale="unit",
        row_label="Adjusted Gross Margin", quoted_text="-12.6")
    percent = (make_package(facts=(margin_q2, margin_q3)), make_candidate(signals={}))

    gaap_q3 = make_fact(
        observation_id="obs:gaap-gross-margin:opendoor:2022Q3:normalized-table:dd44ee55ff66",
        metric_id="gaap_gross_margin", metric_label="Gross Margin",
        value=-28.5, unit="percent", currency=None, scale="unit",
        row_label="Gross Margin", quoted_text="-28.5")
    one_period = (
        make_package(
            facts=(margin_q3, gaap_q3),
            metrics=(*METRICS,
                     PackagedMetric(metric_id="gaap_gross_margin", label="Gross Margin",
                                    unit="percent", allowed_units=("percent",),
                                    aliases=("gaap gross margin",)))),
        make_candidate(signals={}, story_type="cross_metric_divergence"),
    )
    return (usd, percent, one_period)


def test_every_operation_the_offer_set_may_print_is_one_a_draft_can_bind():
    """§11.4's first defect, closed at the end that moved, and asserted generally.

    `offers` printed `trend_direction`, `execute_all` computed it, and §6 refused every draft
    binding one as `derived_operation_not_supported` — so `gpt-5.4` was refused for choosing
    something it had been offered. The rule that holds now is a statement about the *sets* and
    not about today's membership: **anything the planner may be shown must be something a draft
    can state**, whatever the two ends come to hold later.

    Driven rather than declared. The subset assertion alone would pass against a verifier that
    had stopped refusing anything, so every offer of every package below is executed and put
    through `integrity_findings`, which is the function that answers
    `derived_operation_not_supported`.
    """
    assert OFFERABLE_OPERATIONS <= derived_rules.RECOMPUTABLE, (
        sorted(operation.value for operation in
               OFFERABLE_OPERATIONS - derived_rules.RECOMPUTABLE))

    exercised: set[DerivationOperation] = set()
    for package, candidate in _offerable_packages():
        offered = offers(package, candidate)
        for request in offered:
            fact = execute(request, package, candidate,
                           direction=detector_config.quantity_direction, offered=offered)
            assert isinstance(fact, DerivedFact), (
                f"{request.operation.value} was offered and then refused: "
                f"{getattr(fact, 'code', None)} {getattr(fact, 'detail', '')}")
            found = derived_rules.integrity_findings(fact, PackageIndex(package, (fact,)))
            assert "derived_operation_not_supported" not in {item.code for item in found}, (
                f"{request.operation.value} is offered and unbindable")
            exercised.add(request.operation)

    assert exercised == OFFERABLE_OPERATIONS, (
        "the packages above no longer exercise every offerable operation, so this test proves "
        "less than it says: " + str(sorted(
            operation.value for operation in OFFERABLE_OPERATIONS - exercised)))


def test_the_withdrawn_operation_is_still_refused_by_the_verifier_that_forced_the_withdrawal():
    """Nothing was weakened to close §11.4's first defect: the offer set narrowed and the
    refusal stayed. A `trend_direction` fact reaching §13 from a replayed artifact — which is
    the only way one can arrive now — is still `derived_operation_not_supported`."""
    assert DerivationOperation.TREND_DIRECTION not in OFFERABLE_OPERATIONS
    assert DerivationOperation.TREND_DIRECTION not in derived_rules.RECOMPUTABLE

    word = make_derived(operation=DerivationOperation.TREND_DIRECTION, result=None,
                        result_word="decrease", unit="direction", currency=None,
                        display_semantics=DisplaySemantics.DECREASED)
    found = derived_rules.integrity_findings(word, PackageIndex(make_package(), (word,)))
    assert [item.code for item in found] == ["derived_operation_not_supported"]


# ---------------------------------------------------------------------------------------
# The repair packet after §11.3 — a derivation's two periods, covered and checked
# ---------------------------------------------------------------------------------------

#: The sentence both providers wrote and both were refused for, in its shortest honest form.
#: `$446 million` is the derived result and the two period phrases are the derivation's own
#: windows; the endpoint values are deliberately absent, because a sentence that stated them
#: would need two more bindings and the finding under test is about the *periods*.
TWO_PERIOD_TEXT = ("Adjusted gross profit fell $446 million from the second quarter of 2022 to "
                   "the third quarter of 2022.")


def test_a_sentence_naming_both_of_a_derivations_periods_leaves_no_numeral_undeclared(verifier):
    """§11.3's finding, and the one this packet exists for.

    A `FactBinding` declares **one** `period_surface` and §6 fixes it to `to_period`, so before
    this repair the `from` year was uncovered *by construction*: the live Qwen and `gpt-5.4`
    drafts of `cand:metric-move:adjusted-gross-profit:…` were both refused `unbound_numeral` on
    the first `2022` of a true sentence naming the two quarters it computed between. The
    derivation knows both windows; the binding could only ever name one; so `_covering_spans`
    reads them off the fact.
    """
    verified = verifier.verify(
        make_draft((fall_sentence(text=TWO_PERIOD_TEXT),)), make_package(), make_plan(), (FALL,))

    assert [found.code for found in verified.all_findings] == []
    assert verified.passed is True
    # Three numerals now — `446`, and a year in each of the two period phrases — and the check
    # examined all three rather than stopping at the covered one.
    assert verified.check("numbers").examined == 3


def test_the_covered_period_is_still_the_one_the_check_reads_and_not_a_licence(verifier):
    """Coverage is not trust, stated as a test: the same sentence with the `from` window moved
    to a quarter the derivation does not span is refused, and refused by name.

    §13.1 asks *"can a reader tell which claim this numeral belongs to"* and §13.4 asks *"is the
    claim true"*. Widening the first must not answer the second, so a phrase resolving to
    neither `from_period` nor `to_period` is `period_named_in_text_contradicts_binding` —
    a refusal that did not exist before the widening, because the *any*-rule it replaced passed
    a sentence as soon as one phrase agreed.
    """
    # "the fourth quarter of 2021" and not "the first quarter of 2022": `first` is a
    # superlative and §13.8 would refuse the sentence for a second, unrelated reason.
    text = TWO_PERIOD_TEXT.replace("the second quarter of 2022",
                                   "the fourth quarter of 2021")
    codes_found = codes(make_draft((fall_sentence(text=text),)))

    assert "period_named_in_text_contradicts_binding" in codes_found
    assert GATE["period_named_in_text_contradicts_binding"].blocking is True


def test_a_sentence_naming_a_third_period_beside_the_two_is_refused(verifier):
    """The other half of the same rule: two windows are licensed, a third is a claim about a
    period nothing computed — and its numeral is no longer reported as undeclared, so this is
    the check that has to catch it."""
    text = ("Adjusted gross profit fell $446 million from the second quarter of 2022 to the "
            "third quarter of 2022, against the fourth quarter of 2021.")
    codes_found = codes(make_draft((fall_sentence(text=text),)))

    assert "period_named_in_text_contradicts_binding" in codes_found


def test_a_sentence_that_names_only_the_period_the_claim_is_not_about_is_refused(verifier):
    """`to_period` is what the binding declares and what `display_semantics` is stated against,
    so a sentence that names periods and never names that one is refused exactly as it was
    before this packet — the widening added a refusal and removed none."""
    text = "Adjusted gross profit fell $446 million from the second quarter of 2022."
    codes_found = codes(make_draft((fall_sentence(text=text),)))

    assert "period_named_in_text_contradicts_binding" in codes_found


def test_the_two_windows_are_matched_through_the_grammar_and_not_by_the_declared_string(verifier):
    """§13.4's closed grammar, on both windows: a legitimate re-phrasing is not a refusal.

    The declared surface stays `"the third quarter of 2022"` and the prose writes both windows
    the compact way. Nothing here is a substring of anything the binding declares, so a coverage
    rule built on string matching would refuse this true sentence.
    """
    text = "Adjusted gross profit fell $446 million from Q2 2022 to Q3 2022."
    verified = verifier.verify(
        make_draft((fall_sentence(text=text),)), make_package(), make_plan(), (FALL,))

    assert [found.code for found in verified.all_findings] == []


# ---------------------------------------------------------------------------------------
# H1 — the four holes an adversarial review reproduced, each with its own repro
# ---------------------------------------------------------------------------------------
#
# The verdict being repaired: *"any calculation the post uses is first turned into a
# deterministic trusted fact"* held, and *"the model only chooses how to express that fact"* did
# not. Every sentence below was **accepted with zero findings** against `ead0290`, bound to a
# derivation whose number was right.


def _crossing_fact(**overrides: object) -> DerivedFact:
    """`crossed_zero` over `$556M -> $110M`: two positive readings, so `did_not_cross`."""
    fields: dict[str, object] = dict(
        operation=DerivationOperation.CROSSED_ZERO,
        result=None, result_word=derived_rules.DID_NOT_CROSS,
        unit="boolean", currency=None,
        display_semantics=DisplaySemantics.DID_NOT_CROSS_ZERO,
        reused_detector_signal="crosses_zero")
    fields.update(overrides)
    return make_derived(**fields)


def _bound(derived: DerivedFact, text: str, span: str,
           kind: SentenceKind = SentenceKind.CALCULATED, **binding: object) -> DraftSentence:
    """One sentence binding `derived` at `span`, with both input cells cited."""
    fields: dict[str, object] = dict(
        fact_id=derived.fact_id, rendered=span,
        char_start=text.index(span), char_end=text.index(span) + len(span),
        metric_surface="Adjusted gross profit",
        period_surface=derived.period_surface_hint)
    fields.update(binding)
    return DraftSentence(
        index=0, text=text, kind=kind,
        fact_bindings=(FactBinding(**fields),),  # type: ignore[arg-type]
        citations=(Q2_CITATION, Q3_CITATION))


# -- F1: the prose may name a different metric than the derivation is of -------------------


@pytest.mark.parametrize("kind", [SentenceKind.CALCULATED, SentenceKind.REPORTED])
def test_a_derived_binding_whose_prose_names_another_metric_is_refused(kind):
    """**The hole, in the words that reproduced it.** *"Adjusted gross margin fell $446
    million"* is a **percent** metric stated in dollars, bound to a derivation of
    `adjusted_gross_profit`, with a correct number, a correct period and a correct declared
    surface — and it passed.

    `_derived_metric_findings` read the *declared* `metric_surface` and nothing else, and the
    prose rule that catches this for an observation lives in the observed branch behind
    `GROUNDED_SENTENCE_KINDS`, which a derived binding `continue`s past on **every** sentence
    kind. Parametrised over both kinds because `reported` was accepted too: this was never only
    the `calculated` exemption `GROUNDED_SENTENCE_KINDS` records as *"a separate question nobody
    has measured"*.
    """
    text = "Adjusted gross margin fell $446 million in the third quarter of 2022."
    assert "metric_named_in_text_contradicts_binding" in codes(
        make_draft(sentences=(_bound(FALL, text, "$446 million", kind),)))


@pytest.mark.parametrize("kind", [SentenceKind.CALCULATED, SentenceKind.REPORTED])
def test_a_derived_binding_whose_prose_names_no_metric_at_all_is_refused(kind):
    """The other half of §13.5's prose rule, reaching a derived binding.

    *"Revenue fell $446 million"* names a metric this package does not carry, so the alias index
    finds nothing to license and the sentence has attributed a code-computed number to a metric
    no reader can check. The observed rule has refused this shape since R8; the derived one
    skipped it entirely.
    """
    text = "Revenue fell $446 million in the third quarter of 2022."
    assert "metric_surface_absent_from_text" in codes(
        make_draft(sentences=(_bound(FALL, text, "$446 million", kind),)))


def test_the_prose_rule_admits_either_metric_of_a_two_metric_derivation():
    """Not a stricter rule than the declared surface gets: R2's two metrics both license.

    `compare_levels` is the one claim kind that carries a second metric, and a sentence stating
    the demo's gap may name either margin — which is what the committed accepted draft does. The
    same admissible set `_derived_metric_findings` uses for the declaration.
    """
    gap = make_derived(
        operation=DerivationOperation.COMPARE_LEVELS,
        from_fact_id=Q3_ID, to_fact_id=Q3_ID,
        from_period="2022Q3", to_period="2022Q3",
        from_value=110_000_000.0, to_value=110_000_000.0,
        result=0.0, display_semantics=DisplaySemantics.EQUAL_TO,
        metric_id="adjusted_gross_profit", from_metric_id="adjusted_gross_margin")
    text = "Adjusted gross margin was the other reading of the third quarter of 2022."
    sentence = _bound(gap, text, "Adjusted gross margin",
                      metric_surface="Adjusted gross profit")
    found = codes(make_draft(sentences=(sentence,)), derived=(gap,))
    assert "metric_named_in_text_contradicts_binding" not in found
    assert "metric_surface_absent_from_text" not in found


# -- F2: the prose may state the opposite direction, or no direction at all -----------------


@pytest.mark.parametrize(
    "verb", ["grew", "climbed", "surged", "gained", "jumped", "expanded", "rose"])
def test_a_change_verb_outside_the_old_lexicon_no_longer_turns_the_rule_off(verb):
    """**Six of these seven were accepted**, over a fact reading `decreased by` / `-446000000.0`.

    Only `rose` refused, because only `rose` was one of `CHANGE_DIRECTION`'s thirteen words. The
    rule read the lexicon to decide whether it ran at all, so ordinary investor English walked
    around it — the identical failure `COMPARATIVE_TERMS` records R8 measuring for comparatives,
    in the same module, one release later. Widening the lexicon is why these seven now land on
    `derived_fact_orientation_reversed` rather than on the fail-closed code below; the rule not
    depending on the lexicon is why an eighth synonym would still be refused.
    """
    text = FALL_TEXT.replace("fell", verb)
    assert "derived_fact_orientation_reversed" in codes(
        make_draft(sentences=(fall_sentence(text=text),)))


def test_an_unlisted_change_word_is_refused_rather_than_abstained_on():
    """The fail-closed half, driven with a word no lexicon carries.

    *"Adjusted gross profit ballooned $446 million"* states a direction English readers can see
    and `CHANGE_DIRECTION` cannot. Widening a word list can never be the guarantee; refusing a
    sentence that states nothing the map recognises is, and it is what stops the next synonym
    from being the next hole.
    """
    text = FALL_TEXT.replace("fell", "ballooned")
    assert "derived_direction_not_stated_in_text" in codes(
        make_draft(sentences=(fall_sentence(text=text),)))


def test_a_change_stated_as_a_level_is_refused():
    """**No verb is needed to break it, which is why widening alone was not the fix.**

    *"Adjusted gross profit **was** $446 million in the third quarter of 2022."* was accepted.
    The actual 2022Q3 level is $110M; `$446 million` is the *fall*. Every declared field is
    valid and the sentence is false, and there is no word in it for a lexicon to catch.
    """
    text = "Adjusted gross profit was $446 million in the third quarter of 2022."
    assert "derived_direction_not_stated_in_text" in codes(
        make_draft(sentences=(_bound(FALL, text, "$446 million"),)))


def test_a_derivation_whose_direction_was_never_established_admits_no_direction_word():
    """`DIRECTION_UNVERIFIABLE` is the strongest of the three `None` semantics, and it abstained.

    The metric's sign convention was never measured, so *nobody* established which way the
    quantity moved — and the rule that should say so was the rule that fell silent. Any
    directional word over such a fact is now refused, and none is demanded, because there is no
    computed direction for the sentence to state.
    """
    unverifiable = make_derived(display_semantics=DisplaySemantics.DIRECTION_UNVERIFIABLE)
    found = codes(make_draft(sentences=(fall_sentence(unverifiable),)),
                  derived=(unverifiable,))
    assert "derived_fact_orientation_reversed" in found
    neutral = "Adjusted gross profit moved $446 million in the third quarter of 2022."
    assert codes(make_draft(sentences=(_bound(unverifiable, neutral, "$446 million"),)),
                 derived=(unverifiable,)) == set()


# -- F3: a word-valued derived fact had no prose check whatsoever ---------------------------


@pytest.mark.parametrize("phrase", ["turned negative", "swung from profit to loss"])
def test_a_crossed_zero_fact_refuses_a_sentence_claiming_the_opposite(phrase):
    """**Accepted with zero findings before H1, both of them.**

    `crossed_zero` over `$556M -> $110M` computes `did_not_cross`. `SEMANTIC_DIRECTION` leaves
    both crossing members at `None` so the change-verb rule abstained; the fact carries no
    numeral so §13.1 had nothing to compare; `language.ungrounded_words` reached only an
    `EvidenceScopeFact`. Three rules, three different reasons to look away from the same
    sentence.
    """
    fact = _crossing_fact()
    text = (f"Adjusted gross profit {phrase} between the second quarter of 2022 and the third "
            "quarter of 2022.")
    assert "derived_fact_orientation_reversed" in codes(
        make_draft(sentences=(_bound(fact, text, phrase),)), derived=(fact,))


def test_a_crossed_zero_sentence_that_states_no_crossing_at_all_is_refused():
    """Fail closed, the same way the change-verb half now does.

    The fact answers in a word and has no numeral, so the phrase stating the crossing is the
    only thing a verifier can hold the sentence to. A sentence binding it and stating none has
    bound a word code nothing in its own text expresses.
    """
    fact = _crossing_fact()
    text = "Adjusted gross profit is discussed for the third quarter of 2022 here."
    assert "derived_direction_not_stated_in_text" in codes(
        make_draft(sentences=(_bound(fact, text, "Adjusted gross profit"),)), derived=(fact,))


def test_a_crossed_zero_sentence_that_states_what_the_tool_computed_passes():
    """The rule refuses wrong sentences and not the operation: a true wording is accepted.

    **And a measurement H1 did not expect, recorded rather than smoothed over.** The *most*
    natural true wording — *"did not cross zero"* — is refused, and not by this rule: `"did
    not"` is a §13.14 `ABSENCE_TERMS` member and the sentence earns `unsupported_absence_claim`,
    a check that predates S13 and that H1 may not weaken. So on the `did_not_cross` branch the
    writable surfaces are the ones that state the fact positively — *"remained positive"*,
    *"stayed positive"*, *"on the same side of zero"* — and every negated spelling collides with
    §13.14. That is a real narrowing of what a `crossed_zero` derivation can say, it is the
    conservative direction, and it is a reason to ask whether the operation earns its place in
    the offer set at all rather than a reason to loosen either rule.
    """
    fact = _crossing_fact()
    text = ("Adjusted gross profit remained positive between the second quarter of 2022 and "
            "the third quarter of 2022.")
    assert codes(make_draft(sentences=(_bound(fact, text, "remained positive"),)),
                 derived=(fact,)) == set()


def test_the_crossing_lexicon_states_a_polarity_for_every_phrase_it_scans():
    """`COMPARATIVE_DIRECTION`'s discipline, asserted for the lexicon H1 adds.

    Totality is what lets `_crossing_findings` read a `None` as *"the caller passed a phrase the
    scan did not produce"* rather than as a silent abstention — which is the fault this whole
    section repairs.
    """
    for phrase in language.CROSSING_TERMS:
        assert language.crossing_direction(phrase) is not None
    text = " ".join(language.CROSSING_TERMS)
    assert {match.term.lower() for match in language.crossing_claims(text)}


# -- F4: an evidence-scope binding's period_surface licensed numeral coverage ---------------


def test_an_evidence_scope_binding_may_declare_no_metric_and_no_period():
    """§7's fact is of no metric and over no period, and two checks resolve neither.

    `_check_periods` reaches `index.fact()` and then `index.derived_fact()`, gets `None` from
    both and `continue`s — as do `_check_units` and `_check_metric_identity`. So both fields
    were free strings nothing read, and one of them was read: see the test below.
    """
    found = codes(make_draft(sentences=(scope_sentence(
        fact_bindings=(FactBinding(
            fact_id=SCOPE.fact_id, rendered=SCOPE_TEXT[:51],
            char_start=0, char_end=51,
            metric_surface="Adjusted gross profit",
            period_surface="the third quarter of 2022"),)),)), derived=(SCOPE,))
    assert "evidence_scope_binding_declares_a_surface" in found


def test_an_evidence_scope_period_surface_licenses_no_numeral():
    """**The repro, verbatim: three fabricated numerals in an accepted post.**

        "The evidence in this package supplies no explanation for the 92 percent collapse to 7
         from 9999."          period_surface = "92 percent collapse to 7 from 9999"

    `_covering_spans` consumed `binding.period_surface` from **every** binding unconditionally,
    and its own docstring's justification — *"covering it here is not trusting it:
    `_check_periods` resolves the declared surface"* — was false for this binding kind. All
    three numerals are `unbound_numeral` now, which is §13.1 working as written the moment the
    licence it never granted is taken back.
    """
    text = ("The evidence in this package supplies no explanation for the 92 percent collapse "
            "to 7 from 9999.")
    span = "The evidence in this package supplies no explanation"
    sentence = scope_sentence(text=text, fact_bindings=(FactBinding(
        fact_id=SCOPE.fact_id, rendered=span, char_start=0, char_end=len(span),
        metric_surface="", period_surface="92 percent collapse to 7 from 9999"),))
    verified = DeterministicVerifier().verify(
        make_draft(sentences=(sentence,)), make_package(), make_plan(), (SCOPE,))
    assert [found.code for found in verified.all_findings].count("unbound_numeral") == 3
    assert verified.passed is False


def test_a_derived_bindings_period_surface_still_covers_what_it_always_covered():
    """The coverage gate narrows to *"a surface some check resolves"* and to nothing less.

    §12.1's widening — both of a derivation's periods covered, both checked — is the thing most
    at risk from a rule that stops reading `period_surface`, so the two-period sentence that
    repair was made for is driven again here.
    """
    text = ("Adjusted gross profit fell $446 million from the second quarter of 2022 to the "
            "third quarter of 2022.")
    assert codes(make_draft(sentences=(_bound(FALL, text, "$446 million"),))) == set()
