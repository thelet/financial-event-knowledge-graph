"""Claim-time validation, and the distinctions it exists to protect."""

from __future__ import annotations


from ontology.core.errors import (
    E_CALC_FIELDS_ON_REPORTED,
    E_CALC_WITHOUT_INPUTS,
    E_CONFIDENCE_RANGE,
    E_DISCOVERY_ONLY_AS_CANONICAL,
    E_EVENT_TEMPORAL,
    E_EVIDENCE_OFFSETS,
    E_MISSING_CURRENCY,
    E_MISSING_EVIDENCE,
    E_MISSING_INSTANT,
    E_MISSING_PERIOD,
    E_NO_FORMULA_FOR_DATE,
    E_PERIOD_ORDER,
    E_SOURCE_LANE_FORBIDDEN,
    E_SUBJECT_TYPE,
    E_UNIT_MISMATCH,
    E_UNKNOWN_REFERENCE,
)
from ontology.core.models import EvidenceReference, OntologyClaim

from ontology_factories import evidence, observation


def test_a_well_formed_reported_observation_validates(ontology):
    assert ontology.validate_observation(observation()).ok


def test_unknown_metric_is_rejected(ontology):
    result = ontology.validate_observation(observation(metric_id="no_such_metric"))
    assert E_UNKNOWN_REFERENCE in result.codes


def test_duration_metric_without_a_period_is_rejected(ontology):
    result = ontology.validate_observation(
        observation(period_start=None, period_end=None)
    )
    assert E_MISSING_PERIOD in result.codes


def test_instant_metric_without_an_instant_date_is_rejected(ontology):
    result = ontology.validate_observation(observation(
        metric_id="housing_inventory_homes", period_start=None, period_end=None
    ))
    assert E_MISSING_INSTANT in result.codes


def test_reversed_period_is_rejected(ontology):
    result = ontology.validate_observation(
        observation(period_start="2023-03-31", period_end="2023-01-01")
    )
    assert E_PERIOD_ORDER in result.codes


def test_wrong_unit_is_rejected(ontology):
    result = ontology.validate_observation(observation(unit="percent"))
    assert E_UNIT_MISMATCH in result.codes


def test_monetary_observation_without_a_currency_is_rejected(ontology):
    result = ontology.validate_observation(observation(
        metric_id="revenue", value=100.0, unit="USD_millions", source_lane="xbrl"
    ))
    assert E_MISSING_CURRENCY in result.codes


def test_subject_type_is_checked_but_admits_subtypes(ontology):
    assert ontology.validate_observation(observation(subject_type="public_company")).ok
    assert E_SUBJECT_TYPE in ontology.validate_observation(
        observation(subject_type="person")
    ).codes


def test_a_forbidden_source_lane_is_an_error_not_a_warning(ontology):
    """No XBRL tag for homes sold exists in any of the 97 taxonomy files."""
    result = ontology.validate_observation(observation(source_lane="xbrl"))
    assert E_SOURCE_LANE_FORBIDDEN in result.codes
    assert not result.ok


def test_an_unpreferred_but_permitted_lane_is_only_a_warning(ontology):
    """A GAAP figure read from a table is weaker evidence, not a wrong claim."""
    result = ontology.validate_observation(observation(
        metric_id="revenue", value=100.0, unit="USD_millions", currency="USD",
        source_lane="manual_annotation",
    ))
    assert result.ok
    assert "unpreferred_source_lane" in result.codes


def test_reported_observation_must_not_carry_calculation_fields(ontology):
    result = ontology.validate_observation(observation(
        assertion_type="reported", formula_version_id="adjusted_gross_margin_v1"
    ))
    assert E_CALC_FIELDS_ON_REPORTED in result.codes


def test_calculated_observation_without_inputs_is_rejected(ontology):
    result = ontology.validate_observation(observation(
        metric_id="adjusted_gross_margin", value=6.1, unit="percent",
        source_lane="calculated", assertion_type="calculated", evidence=(),
    ))
    assert E_CALC_WITHOUT_INPUTS in result.codes


def test_calculated_observation_needs_no_evidence_because_it_is_rederivable(ontology):
    result = ontology.validate_observation(observation(
        metric_id="adjusted_gross_margin", value=6.1, unit="percent",
        source_lane="calculated", assertion_type="calculated", evidence=(),
        formula_version_id="adjusted_gross_margin_v1",
        input_observation_ids=("obs-a", "obs-b"),
    ))
    assert result.ok


def test_reported_observation_without_evidence_is_rejected(ontology):
    result = ontology.validate_observation(observation(evidence=()))
    assert E_MISSING_EVIDENCE in result.codes


def test_evidence_without_an_anchor_does_not_count_as_evidence(ontology):
    bare = EvidenceReference(evidence_kind="normalized_passage", quoted_text="something")
    result = ontology.validate_observation(observation(evidence=(bare,)))
    assert E_MISSING_EVIDENCE in result.codes


def test_half_specified_character_offsets_are_rejected(ontology):
    result = ontology.validate_observation(
        observation(evidence=(evidence(char_start=10),))
    )
    assert E_EVIDENCE_OFFSETS in result.codes


def test_formula_resolution_uses_the_observation_date(ontology):
    """v2 does not cover a FY2021 value, even if a 2024 filing reported it."""
    result = ontology.validate_observation(observation(
        metric_id="adjusted_gross_profit", value=100.0, unit="USD_millions",
        currency="USD", period_start="2021-01-01", period_end="2021-12-31",
        source_lane="calculated", assertion_type="calculated", evidence=(),
        formula_version_id="adjusted_gross_profit_v2",
        input_observation_ids=("obs-a",),
    ))
    assert E_NO_FORMULA_FOR_DATE in result.codes


def test_confidence_outside_zero_to_one_is_rejected(ontology):
    assert E_CONFIDENCE_RANGE in ontology.validate_observation(
        observation(confidence=1.4)
    ).codes


def test_discovery_only_channel_cannot_be_cited_for_a_filed_period(ontology):
    result = ontology.validate_observation(observation(
        metric_id="acquisition_contracts", value=1180, unit="contracts",
        source_lane="company_dashboard",
        evidence=(EvidenceReference(
            evidence_kind="external_page",
            source_url="https://accountable.opendoor.com/",
            disclosure_channel_id="opendoor_accountable",
        ),),
    ))
    assert E_DISCOVERY_ONLY_AS_CANONICAL in result.codes


def test_claim_kind_must_match_the_payload_it_carries(ontology):
    claim = OntologyClaim(claim_id="c", claim_kind="event", metric_observation=observation())
    assert not ontology.validate_claim(claim).ok


def test_a_claim_may_not_carry_two_payloads(ontology):
    from ontology.core.models import EventInstance

    claim = OntologyClaim(
        claim_id="c", claim_kind="metric_observation",
        metric_observation=observation(),
        event=EventInstance(event_id="e", event_type_id="market_entry"),
    )
    assert not ontology.validate_claim(claim).ok


def test_validate_claims_accumulates_across_a_batch(ontology):
    good = OntologyClaim(claim_id="a", claim_kind="metric_observation",
                         metric_observation=observation())
    bad = OntologyClaim(claim_id="b", claim_kind="metric_observation",
                        metric_observation=observation(observation_id="obs-2", evidence=()))
    result = ontology.validate_claims([good, bad])
    assert E_MISSING_EVIDENCE in result.codes
    assert len(result.errors) == 1


def test_baseline_and_comparison_are_two_observations_of_one_definition(ontology):
    """Not one observation with a footnote."""
    shared = dict(
        metric_id="pct_homes_on_market_gt_120_days", value=25.0, unit="percent",
        period_start=None, period_end=None, instant_date="2022-12-31",
    )
    baseline = observation(observation_id="obs-b", population={"role": "baseline",
                                                              "definition_raw": "our portfolio"}, **shared)
    comparison = observation(observation_id="obs-c", value=32.0,
                             population={"role": "comparison",
                                         "definition_raw": "the broader market"},
                             **{k: v for k, v in shared.items() if k != "value"})
    assert ontology.validate_observation(baseline).ok
    assert ontology.validate_observation(comparison).ok
    assert baseline.population.role != comparison.population.role


# -- announced_on versus occurred_on -------------------------------------------------------
#
# Added 2026-08-02 with the temporal-model change (founder decision). `executive_change` used
# to require `occurred_on` unconditionally, which made a true answer unrepresentable: a press
# release that says "today announced that X has been appointed" dates the announcement and
# never the effective date, so a correct lane could only invent a date or emit nothing. The
# event type now requires `announced_on` OR `occurred_on`. These tests hold the distinction —
# the danger of two date fields is that something quietly copies one into the other, and then
# every appointment is dated to the day it was announced.


def _executive_change(**overrides):
    from ontology.core.models import EventInstance, EventParticipantRef

    fields = dict(
        event_id="evt:executive-change:test",
        event_type_id="executive_change",
        participants=(
            EventParticipantRef(role="employer", entity_id="opendoor",
                                entity_type="public_company"),
            EventParticipantRef(role="officer", entity_id="a_person", entity_type="person"),
        ),
        properties={"position": "Chief Executive Officer"},
        evidence=(evidence(),),
    )
    fields.update(overrides)
    return EventInstance(**fields)


def test_an_executive_change_with_only_an_announcement_date_validates(ontology):
    """The case the change exists for: the passage dates the announcement and nothing else."""
    assert ontology.validate_event(_executive_change(announced_on="2025-09-10")).ok


def test_an_executive_change_with_only_an_occurrence_date_validates(ontology):
    """Still valid the other way round — a filing that says an appointment "became effective
    on" a date supports `occurred_on` and may say nothing about when it was announced."""
    assert ontology.validate_event(_executive_change(occurred_on="2025-09-15")).ok


def test_an_executive_change_with_both_dates_validates_and_keeps_both(ontology):
    """Both stated, both preserved. Neither collapses into the other."""
    event = _executive_change(announced_on="2025-09-10", occurred_on="2025-09-15")
    assert ontology.validate_event(event).ok
    assert event.announced_on == "2025-09-10"
    assert event.occurred_on == "2025-09-15"


def test_an_executive_change_with_neither_date_is_rejected(ontology):
    """`any_of` is a requirement, not a suggestion. Dropping `occurred_on` from the required
    list must not have made an undated executive change acceptable."""
    result = ontology.validate_event(_executive_change())
    assert not result.ok
    assert E_EVENT_TEMPORAL in result.codes
    assert "at least one of" in " ".join(i.message for i in result.issues)


def test_an_event_type_that_requires_occurred_on_still_does(ontology):
    """The change is narrow. `workforce_reduction` and `credit_facility_established` are
    reported by evidence that dates the event itself, and they still require it — a blanket
    relaxation would have been the broader redesign this deliberately is not."""
    from ontology.core.models import EventInstance, EventParticipantRef

    undated = EventInstance(
        event_id="evt:workforce-reduction:test",
        event_type_id="workforce_reduction",
        participants=(EventParticipantRef(role="operator", entity_id="opendoor",
                                          entity_type="public_company"),),
        evidence=(evidence(),),
    )
    result = ontology.validate_event(undated)
    assert not result.ok
    assert E_EVENT_TEMPORAL in result.codes
    # And an announcement date does not satisfy a requirement for an occurrence date.
    assert not ontology.validate_event(
        undated.model_copy(update={"announced_on": "2021-03-04"})).ok


def test_nothing_copies_one_date_into_the_other(ontology):
    """The failure mode two date fields invite, checked on the model rather than trusted.

    Validation must not populate, default, or mirror either field. If it ever did, an
    announcement-only event would silently acquire an occurrence date equal to it — which is
    exactly the inference the founder decision forbids, arriving through the back door.
    """
    announced = _executive_change(announced_on="2025-09-10")
    assert ontology.validate_event(announced).ok
    assert announced.occurred_on is None, "validation invented an occurrence date"

    occurred = _executive_change(occurred_on="2025-09-15")
    assert ontology.validate_event(occurred).ok
    assert occurred.announced_on is None, "validation invented an announcement date"


def test_the_two_date_fields_are_distinct_on_the_model(ontology):
    """A cheap guard with a real target: if `announced_on` were ever aliased to `occurred_on`
    — a validation_alias, a property, a shared default — every test above would still pass
    while the distinction had been erased."""
    from ontology.core.models import EventInstance

    fields = EventInstance.model_fields
    assert "announced_on" in fields and "occurred_on" in fields
    event = _executive_change(announced_on="2025-09-10", occurred_on="2025-09-15")
    assert event.model_dump()["announced_on"] != event.model_dump()["occurred_on"]
