"""S2 and S3 of EVIDENCE_ROLES_AND_SEMANTIC_FACTS — what a passage is, and what two readings are.

The two stages are one packet because they are two halves of one question, and the file is
organised the same way:

1. **`observation_equivalence`** — the arithmetic both halves stand on. Equivalent readings are
   corroboration (S3); divergent ones are counter-evidence (S2); and the two failure modes S7's
   adversarial review is aimed at are *false corroboration* (two things merged that are not the
   same fact) and *missed contradiction* (something real downgraded to a warning). Every
   non-equivalence below is a false-corroboration guard.
2. **`passage_quality`** — the filter that runs before any role is assigned, so a pipe-only
   table cannot become a counterpoint §11's planner is obliged to write about.
3. **`counter_evidence.classify_issue`** — the rule §1 exists for. Twenty issues, not one of
   which challenged the fact they were attached to.
4. **The planner's accounting, unchanged.** The risk this packet creates is a real contradiction
   quietly becoming a warning, so the last section drives a genuine counterpoint all the way to
   `counter_evidence_unaccounted` and requires it to fire.

Nothing here is a shape test. Every claim is driven through the function that makes it.
"""

from __future__ import annotations

import pytest

from story.core.models import (
    EvidenceRole,
    PackagedPassage,
    PassageQuality,
    PassageUnusableReason,
)
from story.core.observation_equivalence import (
    DIVERGENCE_REASONS,
    Divergent,
    SameReading,
    equivalent_period,
    same_reading,
)
from story.core.periods import story_period
from story.core.series import ComparabilityAuthority, ObservationRecord, default_authority
from story.stages.packaging import counter_evidence as counter
from story.stages.packaging import passage_quality

from conftest import make_package, make_plan  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------------------
# The equivalence policy
# ---------------------------------------------------------------------------------------


def homes(observation_id: str, value: float, **overrides: object) -> ObservationRecord:
    """One `housing_inventory_homes` reading of 2023-03-31 — §1.1's slot, which holds six."""
    fields: dict[str, object] = dict(
        observation_id=observation_id,
        metric_id="housing_inventory_homes",
        subject_entity_id="opendoor",
        period=story_period(None, None, "2023-03-31"),
        value=value,
        unit="homes",
        scale="units",
        currency=None,
        source_lane="normalized_table",
        validation_state="clean",
        document_id=f"doc:{observation_id}",
        passage_id=f"doc:{observation_id}#p1",
        filing_date="2023-05-04",
        quoted_text="6,261",
    )
    fields.update(overrides)
    return ObservationRecord(**fields)  # type: ignore[arg-type]


def percent(observation_id: str, value: float, **overrides: object) -> ObservationRecord:
    fields: dict[str, object] = dict(
        observation_id=observation_id,
        metric_id="gaap_gross_margin",
        subject_entity_id="opendoor",
        period=story_period("2022-07-01", "2022-09-30", None),
        value=value,
        unit="percent",
        scale="units",
        currency=None,
        source_lane="normalized_table",
        validation_state="clean",
        document_id=f"doc:{observation_id}",
        passage_id=f"doc:{observation_id}#p1",
        filing_date="2022-11-03",
        quoted_text="3.3%",
    )
    fields.update(overrides)
    return ObservationRecord(**fields)  # type: ignore[arg-type]


def test_the_same_count_printed_three_ways_in_three_filings_is_one_reading():
    """`6,261` / `6261` / `6.261 thousand` — §4 S2's own list.

    The printed form never enters the comparison: `ObservationRecord.value` is already
    scale-applied, so all three arrive as `6261.0` and the only slack is the presentation
    tolerance. A rule written over `quoted_text` would have to decide whether `6,261` and
    `6.261` are the same string, which is the string surgery this module exists to avoid.
    """
    comma = homes("obs:a", 6261.0, quoted_text="6,261")
    plain = homes("obs:b", 6261.0, quoted_text="6261", document_id="doc:b")
    scaled = homes("obs:c", 6261.0, scale="thousands", quoted_text="6.261",
                   document_id="doc:c")

    assert same_reading(comma, plain)
    assert same_reading(comma, scaled)
    assert isinstance(same_reading(plain, scaled), SameReading)


def test_a_percentage_written_two_ways_is_one_reading():
    assert same_reading(percent("obs:a", 3.3, quoted_text="3.3%"),
                        percent("obs:b", 3.3, quoted_text="3.30 percent"))


def test_a_percentage_and_a_change_in_percentage_points_never_merge():
    """**§13.3 exists for exactly this confusion**, and it is the single most likely factual
    error this package can make: `15.9%` and `15.9 percentage points` are numerically equal and
    are different quantities. The refusal carries its own reason rather than a generic unit
    mismatch, because *"units differ"* reads as bookkeeping and this is not.
    """
    level = percent("obs:a", 15.9, unit="percent")
    change = percent("obs:b", 15.9, unit="percentage_points")

    answer = same_reading(level, change)

    assert not answer
    assert isinstance(answer, Divergent)
    assert answer.reason == "PERCENT_VERSUS_PERCENTAGE_POINTS"
    assert answer.rule == "E4b"
    assert "percentage points" in answer.detail


def test_a_percentage_and_the_same_number_of_basis_points_never_merge():
    assert same_reading(percent("obs:a", 15.9), percent("obs:b", 15.9, unit="basis_points")
                        ).reason == "PERCENT_VERSUS_PERCENTAGE_POINTS"  # type: ignore[union-attr]


def test_the_same_number_on_a_different_metric_is_two_facts():
    """The false-corroboration case S7's review is aimed at, and it never reaches the
    tolerance test: identity is compared before arithmetic, on purpose."""
    answer = same_reading(homes("obs:a", 6261.0),
                          homes("obs:b", 6261.0, metric_id="homes_sold"))

    assert answer.reason == "METRIC_MISMATCH"  # type: ignore[union-attr]


def test_two_subjects_are_never_two_sources_for_one_reading():
    answer = same_reading(homes("obs:a", 6261.0),
                          homes("obs:b", 6261.0, subject_entity_id="offerpad"))

    assert answer.reason == "SUBJECT_MISMATCH"  # type: ignore[union-attr]


def test_the_same_count_at_two_period_ends_is_two_readings():
    answer = same_reading(
        homes("obs:a", 6261.0),
        homes("obs:b", 6261.0, period=story_period(None, None, "2022-12-31")))

    assert answer.reason == "PERIOD_MISMATCH"  # type: ignore[union-attr]


def test_a_quarter_and_an_instant_are_not_one_window_however_equal_the_numbers():
    answer = same_reading(
        percent("obs:a", 3.3),
        percent("obs:b", 3.3, period=story_period(None, None, "2022-09-30")))

    assert answer.reason == "PERIOD_MISMATCH"  # type: ignore[union-attr]


def test_a_different_unit_is_a_different_quantity():
    answer = same_reading(homes("obs:a", 6261.0),
                          homes("obs:b", 6261.0, unit="markets"))

    assert answer.reason == "UNIT_MISMATCH"  # type: ignore[union-attr]


def test_two_currencies_are_two_quantities_and_two_absences_are_agreement():
    usd = homes("obs:a", 100.0, unit="USD", currency="USD")
    eur = homes("obs:b", 100.0, unit="USD", currency="EUR")

    assert same_reading(usd, eur).reason == "CURRENCY_MISMATCH"  # type: ignore[union-attr]
    assert same_reading(homes("obs:a", 6261.0), homes("obs:b", 6261.0, document_id="doc:b"))


def test_a_count_one_home_apart_is_one_reading_and_two_homes_apart_is_two():
    """The tolerance is `series.presentation_tolerance`'s and not a second table: a count's
    tolerance is one home, `<=` is the comparison, and `within_tolerance` rounds the difference
    to `DELTA_PRECISION` first. Two callers, one arithmetic."""
    assert same_reading(homes("obs:a", 6261.0), homes("obs:b", 6262.0))
    answer = same_reading(homes("obs:a", 6261.0), homes("obs:b", 6263.0))

    assert answer.reason == "VALUE_OUTSIDE_TOLERANCE"  # type: ignore[union-attr]
    assert "6261.0" in answer.detail and "6263.0" in answer.detail  # type: ignore[union-attr]


def test_two_filings_that_disagree_about_the_direction_say_so_with_their_own_reason():
    """Outside tolerance the sign is what separates *"they disagree about how big it is"* from
    *"they disagree about which way it went"*, and only the second can flip a story."""
    answer = same_reading(percent("obs:a", 11.6), percent("obs:b", -12.6))

    assert answer.reason == "OPPOSITE_SIGN"  # type: ignore[union-attr]
    assert counter.basis_for_divergence(answer.reason) == (  # type: ignore[union-attr]
        counter.MATCH_BASIS_OPPOSITE_DIRECTION)


def test_a_rounding_across_zero_inside_tolerance_is_still_one_reading():
    """Sign is read **after** tolerance and the order is the whole of it: two filings printing
    `+0.05` and `-0.05` at one decimal are one reading that rounded across zero, and refusing
    them would make `presentation_rounding` depend on which side of zero a value landed."""
    assert same_reading(percent("obs:a", 0.05), percent("obs:b", -0.05))


def test_two_formula_versions_of_one_metric_are_never_merged():
    """E6 is a **guard**, and the test says so rather than pretending it fires today.

    E3 requires the two periods to carry identical raw dates, and a formula version is resolved
    from the anchor date, so once E3 has passed the two versions are equal by construction —
    `adjusted_gross_profit` is `v1` before 2022 and `v2` after, and no pair of records can hold
    one period key across that boundary. What the test pins is the **outcome** the plan asks
    for: two readings under two definitions never merge. The rule that catches them today is
    E3, and E6 is what would catch them if `equivalent_period` were ever relaxed to accept two
    spellings of one window.
    """
    authority = default_authority()
    assert authority.resolve_version("adjusted_gross_profit", "2021-06-30") == \
        "adjusted_gross_profit_v1"
    assert authority.resolve_version("adjusted_gross_profit", "2022-09-30") == \
        "adjusted_gross_profit_v2"

    old = percent("obs:a", 9.9, metric_id="adjusted_gross_profit",
                  period=story_period("2021-04-01", "2021-06-30", None))
    new = percent("obs:b", 9.9, metric_id="adjusted_gross_profit",
                  period=story_period("2022-07-01", "2022-09-30", None))

    answer = same_reading(old, new, authority=authority)

    assert not answer
    assert answer.reason == "PERIOD_MISMATCH"  # type: ignore[union-attr]


def test_every_divergence_reason_has_a_match_basis_and_no_basis_is_invented():
    """`DIVERGENCE_BASIS` is total over `DIVERGENCE_REASONS` — checked at import, and checked
    here as behaviour, because a reason with no basis would produce a counter-evidence row
    whose `match_basis` nobody chose."""
    for reason in DIVERGENCE_REASONS:
        assert counter.basis_for_divergence(reason) in counter.QUALIFYING_BASES


def test_an_unversioned_metric_needs_no_authority_and_says_so_by_not_needing_one():
    """The signature is usable from a test with no YAML on disk, which is what keeps
    `core/` importable with no ontology."""
    assert same_reading(homes("obs:a", 6261.0), homes("obs:b", 6261.0, document_id="doc:b"),
                        authority=ComparabilityAuthority())


def test_a_window_nobody_can_name_is_never_equivalent_to_anything():
    """R3's `other` shape, from the equivalence side: the three cross-year windows in this run
    have no length anyone can compare."""
    odd = story_period("2022-07-01", "2023-03-31", None)

    assert not equivalent_period(odd, odd)
    assert same_reading(percent("obs:a", 3.3, period=odd),
                        percent("obs:b", 3.3, period=odd)).reason == "UNCOMPARABLE_SHAPE"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------------
# The quality filter — before any role is assigned
# ---------------------------------------------------------------------------------------


PIPE_ONLY_TABLE = "|  |  |  |  |\n| --- | --- | --- | --- |\n|  |  |  |  |"


@pytest.mark.parametrize("text,reason", [
    (PIPE_ONLY_TABLE, PassageUnusableReason.EMPTY_OR_STRUCTURAL_ONLY),
    ("   \n\n\t  ", PassageUnusableReason.EMPTY_OR_STRUCTURAL_ONLY),
    ("____________________", PassageUnusableReason.EMPTY_OR_STRUCTURAL_ONLY),
    ("* * * * *", PassageUnusableReason.EMPTY_OR_STRUCTURAL_ONLY),
    ("(in millions)\n\n(unaudited)", PassageUnusableReason.NO_RELEVANT_PROPOSITION),
    ("None.", PassageUnusableReason.NO_RELEVANT_PROPOSITION),
    ("Not applicable.", PassageUnusableReason.NO_RELEVANT_PROPOSITION),
    ("End page 4\n\nAnchor", PassageUnusableReason.NO_RELEVANT_PROPOSITION),
    ("Homes in inv", PassageUnusableReason.INSUFFICIENT_CONTENT),
    ("Inventory�� fell sharply during the quarter across every market.",
     PassageUnusableReason.CORRUPTED_EXTRACTION),
])
def test_a_passage_that_states_nothing_is_unusable_and_says_which_kind_of_nothing(text, reason):
    assessment = passage_quality.assess(text)

    assert assessment.quality is PassageQuality.UNUSABLE
    assert assessment.reason is reason
    assert not assessment.usable


def test_a_filed_table_with_numbers_and_labels_is_usable():
    text = ("Financial Highlights\n\n| | Three Months Ended September 30, |\n"
            "| Inventory (at period end) | 6,261 | 12,788 |\n")

    assert passage_quality.assess(text) == passage_quality.Assessment(PassageQuality.USABLE)


def test_boilerplate_is_reported_as_boilerplate_and_not_merely_as_short():
    """`"(in millions) (unaudited)"` clears the length floor — 21 content characters, three
    word tokens — and states nothing. The order of the findings is what makes the reason the
    specific one: reporting it as `insufficient_content` would say the extractor got a fragment
    when what it got is a table caption. 31 passages in this run are exactly this string."""
    caption = passage_quality.assess("(In millions)\n\n(Unaudited)")
    fragment = passage_quality.assess("N/M")

    assert caption.reason is PassageUnusableReason.NO_RELEVANT_PROPOSITION
    assert fragment.reason is PassageUnusableReason.INSUFFICIENT_CONTENT


def test_the_content_floor_is_the_number_the_corpus_supports():
    """The bar sits at roughly a quarter of the smallest passage this corpus has ever evidenced
    an observation from (82 content characters, 12 word tokens). A bar at the observed floor
    would have refused 1,325 of 8,776 passages on one filing's formatting."""
    assert (passage_quality.MIN_CONTENT_CHARS, passage_quality.MIN_WORD_TOKENS) == (20, 3)


# ---------------------------------------------------------------------------------------
# §1's twenty issues, classified
# ---------------------------------------------------------------------------------------


def issue(code: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "issue_id": f"issue:{code.lower()}",
        "code": code,
        "severity": "refusal",
        "severity_rank": 1,
        "detail": "",
        "row_label": "Inventory (at period end)",
        "concept_ids": ["housing_inventory_homes", "inventory_balance"],
        "metric_id": "housing_inventory_homes",
        "passage_id": "doc:a#p1",
        "document_id": "doc:a",
    }
    row.update(overrides)
    return row


def bound_fact(**overrides: object):  # type: ignore[no-untyped-def]
    from story.core.models import PackagedFact

    fields: dict[str, object] = dict(
        observation_id="obs:a", metric_id="housing_inventory_homes",
        metric_label="Homes in inventory", period_key="2023-03-31", shape="instant",
        value=6261.0, unit="homes", source_lane="normalized_table",
        validation_state="clean", passage_id="doc:a#p1", document_id="doc:a")
    fields.update(overrides)
    return PackagedFact(**fields)  # type: ignore[arg-type]


def classify(code: str, *, grain: str = counter.MATCH_BASIS_SAME_PASSAGE,
             declared_unit: str | None = "homes", fact=None,  # type: ignore[no-untyped-def]
             **overrides: object) -> counter.Classification:
    return counter.classify_issue(
        issue(code, **overrides), grain=grain,
        bound_fact=bound_fact() if fact is None else fact, declared_unit=declared_unit)


def test_an_ambiguous_alias_on_the_cited_passage_is_a_fact_quality_warning():
    """§1's headline. Nineteen of the inventory candidate's twenty issues are this row —
    *"'Inventory (at period end)' resolves to 2 concepts; no claim emitted"* — and not one of
    them challenges *"inventory fell from 12,788 homes to 6,261 homes"*."""
    found = classify("AMBIGUOUS_ALIAS")

    assert found.role is EvidenceRole.WARNING_ONLY
    assert not found.qualifies
    assert found.match_basis == counter.MATCH_BASIS_SAME_PASSAGE
    assert "declined to emit" in found.why


def test_an_ambiguous_alias_rejection_is_still_a_warning_because_severity_is_not_the_test():
    """`severity_rank` orders the section (D6) and is one input. A rejection about a claim the
    run withdrew is still a statement about a claim, not about the fact the package carries."""
    assert not classify("AMBIGUOUS_ALIAS", severity="rejection", severity_rank=0).qualifies


def test_the_unit_guard_that_agrees_with_the_packaged_fact_does_not_contradict_it():
    """§1's `UNIT_CONTRADICTS_ONTOLOGY` — *"housing_inventory_homes is reported in homes, not
    'percent'"*. **Correcting the plan**: the row carries `concept_ids =
    ['housing_inventory_homes']`, so it is about *this* metric, not another one. It still does
    not qualify: the packaged fact is in `homes`, which is the unit the ontology declares, so
    the guard rejected a claim nobody made and agrees with the fact it was filed against."""
    found = classify("UNIT_CONTRADICTS_ONTOLOGY", severity="rejection", severity_rank=0,
                     concept_ids=["housing_inventory_homes"])

    assert found.role is EvidenceRole.WARNING_ONLY
    assert "the unit the ontology declares" in found.why


def test_the_unit_guard_that_names_the_packaged_facts_own_unit_does_qualify():
    """The other half, and it is the reachable one: a fact carried in a unit the ontology does
    not declare is a fact whose semantics the filing itself disputes."""
    found = classify("UNIT_CONTRADICTS_ONTOLOGY", severity="rejection", severity_rank=0,
                     fact=bound_fact(unit="percent"))

    assert found.role is EvidenceRole.COUNTER_EVIDENCE
    assert found.match_basis == counter.MATCH_BASIS_INCOMPATIBLE_SEMANTICS


@pytest.mark.parametrize("code,basis", [
    ("QUOTED_SPAN_NOT_IN_PASSAGE", counter.MATCH_BASIS_ISSUE_CHANGES_READING),
    ("VALUE_CONTRADICTS_QUOTED_TEXT", counter.MATCH_BASIS_ISSUE_CHANGES_READING),
    ("PERIOD_NOT_GROUNDED_IN_PASSAGE", counter.MATCH_BASIS_SCOPE_UNDERMINES_COMPARISON),
    ("DEFINITIONAL_NOT_OBSERVATIONAL", counter.MATCH_BASIS_TEXTUAL_LIMITATION),
    ("NOT_THE_SUBJECT_COMPANY", counter.MATCH_BASIS_INCOMPATIBLE_SEMANTICS),
])
def test_an_issue_that_impugns_the_cited_cell_qualifies_on_a_named_basis(code, basis):
    """These say the citation chain of the passage the fact was read from does not hold — the
    span is not there, the value disagrees with the quote, the period is not grounded, the
    passage defines rather than reports. Each changes how *this fact* may be read whatever
    claim the issue itself rejected."""
    found = classify(code)

    assert found.qualifies
    assert found.match_basis == basis
    assert found.role is EvidenceRole.COUNTER_EVIDENCE


def test_the_same_issue_one_table_away_is_an_association_and_not_a_contradiction():
    """*"Same-document proximity is not enough."* An `:Issue` records a claim the run refused to
    emit; a refusal in a neighbouring table is not about the cell this package cites, and the
    graph holds no observation from it."""
    for code in counter.QUALIFYING_ISSUE_CODES:
        found = classify(code, grain=counter.MATCH_BASIS_SAME_DOCUMENT)
        assert found.role is EvidenceRole.WARNING_ONLY, code
        assert found.match_basis == counter.MATCH_BASIS_SAME_DOCUMENT, code


def test_missing_period_qualifies_only_when_a_claim_was_actually_withdrawn():
    """214 refusals and 3 rejections in this run. A refusal is *"this percentage appears in a
    table row without a specific period label"* — a statement about a row nobody emitted."""
    assert not classify("MISSING_PERIOD", severity="refusal", severity_rank=1).qualifies
    assert classify("MISSING_PERIOD", severity="rejection", severity_rank=0).qualifies


def test_a_row_with_no_code_at_all_is_a_warning_rather_than_a_silent_qualification():
    assert not classify("").qualifies


def test_narrow_prefers_the_row_that_qualifies_over_the_row_that_merely_outranks_it():
    """Severity is not qualification. A passage carrying an `AMBIGUOUS_ALIAS` rejection and a
    `QUOTED_SPAN_NOT_IN_PASSAGE` rejection is represented by the second: both are rank 0, and
    only one of them says anything about the fact this package carries."""
    rows = (issue("AMBIGUOUS_ALIAS", severity="rejection", severity_rank=0, issue_id="issue:1"),
            issue("QUOTED_SPAN_NOT_IN_PASSAGE", severity="rejection", severity_rank=0,
                  issue_id="issue:2"))

    kept, dropped = counter.narrow(
        rows, metric_ids=("housing_inventory_homes",), cited_document_ids=("doc:a",),
        prefers=lambda row: counter.classify_issue(
            row, grain=counter.MATCH_BASIS_SAME_PASSAGE, bound_fact=bound_fact(),
            declared_unit="homes").qualifies)

    assert [row["issue_id"] for row in kept] == ["issue:2"]
    assert dropped == ("issue:1",)


def test_narrow_without_a_preference_still_takes_the_first_row_of_a_passage():
    """The contract the function had before S2, unchanged for every caller that does not pass
    `prefers`: one row per passage, in the tool's own severity order."""
    rows = (issue("AMBIGUOUS_ALIAS", issue_id="issue:1"),
            issue("QUOTED_SPAN_NOT_IN_PASSAGE", issue_id="issue:2"))

    kept, dropped = counter.narrow(
        rows, metric_ids=("housing_inventory_homes",), cited_document_ids=("doc:a",))

    assert [row["issue_id"] for row in kept] == ["issue:1"]
    assert dropped == ("issue:2",)


# ---------------------------------------------------------------------------------------
# The guard on this whole change: a real contradiction still reaches the planner
# ---------------------------------------------------------------------------------------


def qualifying_counter_passage(passage_id: str) -> PackagedPassage:
    """A row that S2 *does* let through: it qualified on a named basis, not on a shared filing."""
    return PackagedPassage(
        passage_id=passage_id, document_id=passage_id.rsplit("#p", 1)[0],
        text="Inventory (at period end) 6,261 — the quoted span does not occur in this passage.",
        char_count=80, role=EvidenceRole.COUNTER_EVIDENCE,
        match_basis=counter.MATCH_BASIS_ISSUE_CHANGES_READING,
        quality_status=PassageQuality.USABLE,
        diagnostic_codes=("QUOTED_SPAN_NOT_IN_PASSAGE",))


def test_a_qualifying_counterpoint_the_plan_ignores_still_fires_counter_evidence_unaccounted():
    """**The guard on this entire change.** S2's risk is a real contradiction quietly becoming a
    warning; the planner rule is correct and this packet only fixes its input, so a package that
    still carries genuine counter-evidence must still refuse a plan that walks past it."""
    from story.stages.generation.planner import COUNTER_EVIDENCE_UNACCOUNTED, plan_violations

    package = make_package(counter_evidence=(qualifying_counter_passage("doc:a#p12"),))
    plan = make_plan(package_id=package.package_id)

    codes = {violation.code for violation in plan_violations(plan, package)}

    assert COUNTER_EVIDENCE_UNACCOUNTED in codes


def test_a_warning_only_row_is_not_in_counter_evidence_and_so_demands_no_counterpoint():
    """The other side of the same rule, and the whole of §1's fix: the planner is not asked to
    write a counterpoint about an extraction diagnostic, because the diagnostic is not in the
    section that means *"this disputes the story"*."""
    from story.stages.generation.planner import plan_violations

    diagnostic = qualifying_counter_passage("doc:a#p12").model_copy(update={
        "role": EvidenceRole.WARNING_ONLY,
        "match_basis": counter.MATCH_BASIS_SAME_DOCUMENT,
        "diagnostic_codes": ("AMBIGUOUS_ALIAS",)})
    package = make_package(diagnostic_passages=(diagnostic,))
    plan = make_plan(package_id=package.package_id)

    assert plan_violations(plan, package) == ()
