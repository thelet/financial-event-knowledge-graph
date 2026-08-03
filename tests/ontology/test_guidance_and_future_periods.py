"""The guidance contract and the future-period invariant (F0 Part D).

Three defects the factual-spine plan recorded in §5.2, tested here as three groups:

1. `guidance_issuance.inference_restrictions` demanded an assertion type no member could
   supply, so the rule was unsatisfiable and therefore untestable.
2. The guided range was an untyped `dict[str, Any]`, so `"1.0 billion"` entered silently.
3. Nothing anywhere compared a period against the filing that carried it.

Tests drive the real ontology through `validate_event` / `validate_observation` rather than
asserting on the definitions, because what matters is the verdict, not the declaration.
"""

from __future__ import annotations

from typing import Any

import pytest

from ontology.core.constraints import check_future_period
from ontology.core.errors import (
    E_EVENT_ASSERTION_TYPE,
    E_EVENT_PROPERTY_REFERENCE,
    E_EVENT_PROPERTY_TYPE,
    E_EVENT_VALUE_RANGE,
    E_FUTURE_PERIOD,
    E_MISSING_CURRENCY,
    E_UNKNOWN_UNIT,
    ValidationResult,
)
from ontology.core.models import EventInstance, EvidenceReference
from ontology.core.values import AssertionType, SourceLane

from ontology_factories import observation


def guidance_event(properties: dict[str, Any] | None = None, **kwargs: Any) -> EventInstance:
    """A guidance issuance that is correct in every respect except what a test varies."""
    defaults: dict[str, Any] = {
        "event_id": "evt-guidance-under-test",
        "event_type_id": "guidance_issuance",
        "occurred_on": "2024-02-15",
        "assertion_type": AssertionType.GUIDED,
        "participants": (
            {"role": "reporter", "entity_id": "opendoor", "entity_type": "public_company"},
            {"role": "period", "entity_id": "fy2024q1", "entity_type": "fiscal_period"},
        ),
        "properties": {
            "guided_metric": "revenue",
            "low_value": "1000",
            "high_value": "1100",
            "unit": "USD_millions",
            "currency": "USD",
        } if properties is None else properties,
        "evidence": (
            EvidenceReference(evidence_kind="filing_metadata",
                              accession="0001801169-24-000006"),
        ),
    }
    return EventInstance(**{**defaults, **kwargs})


# -- D1: the assertion type ---------------------------------------------------------------


def test_guided_is_a_distinct_assertion_type(ontology):
    """Not an alias for one of the four that already existed."""
    assert AssertionType.GUIDED == "guided"
    assert AssertionType.GUIDED not in (
        AssertionType.REPORTED, AssertionType.CALCULATED,
        AssertionType.CLASSIFIED, AssertionType.INFERRED,
    )


def test_the_restriction_that_could_not_be_satisfied_now_can_be(ontology):
    """The point of D1: `guided` passes where `reported` is refused, on the same event."""
    assert ontology.validate_event(guidance_event()).ok
    refused = ontology.validate_event(
        guidance_event(assertion_type=AssertionType.REPORTED)
    )
    assert E_EVENT_ASSERTION_TYPE in refused.codes


def test_calculated_is_not_the_workaround_for_a_guided_figure(ontology):
    """Overloading `calculated` would demand a formula and inputs guidance cannot supply."""
    guided = observation(assertion_type=AssertionType.GUIDED)
    assert guided.formula_version_id is None
    assert ontology.validate_observation(guided).ok

    as_calculated = observation(assertion_type=AssertionType.CALCULATED)
    assert not ontology.validate_observation(as_calculated).ok


def test_guided_survives_serialization_as_a_plain_string(ontology):
    dumped = guidance_event().model_dump(mode="json")
    assert dumped["assertion_type"] == "guided"
    assert EventInstance(**dumped).assertion_type == AssertionType.GUIDED


# -- D2: the typed range ------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_value",
    ["1.0 billion", "1,000", "$1000", "~1000", "1000 million", "one thousand", ""],
)
def test_a_bound_that_is_not_a_plain_number_is_refused(ontology, bad_value):
    """The scale belongs in `unit`. A string carrying its own scale is the D2 defect."""
    result = ontology.validate_event(guidance_event({
        "guided_metric": "revenue", "low_value": bad_value, "high_value": "1100",
        "unit": "USD_millions", "currency": "USD",
    }))
    assert E_EVENT_PROPERTY_TYPE in result.codes


@pytest.mark.parametrize("good_value", ["1000", "1000.5", "0", "-12.5", "1e3"])
def test_a_plain_number_passes_however_it_is_written(ontology, good_value):
    result = ontology.validate_event(guidance_event({
        "guided_metric": "revenue", "low_value": good_value, "high_value": "9999",
        "unit": "USD_millions", "currency": "USD",
    }))
    assert result.ok, result.render()


def test_a_numeric_bound_may_also_arrive_already_typed(ontology):
    """`properties` is `dict[str, Any]`; a lane that has a float need not stringify it."""
    result = ontology.validate_event(guidance_event({
        "guided_metric": "revenue", "low_value": 1000, "high_value": 1100.5,
        "unit": "USD_millions", "currency": "USD",
    }))
    assert result.ok, result.render()


def test_point_guidance_sets_both_bounds_to_the_same_number(ontology):
    result = ontology.validate_event(guidance_event({
        "guided_metric": "revenue", "low_value": "1000", "high_value": "1000",
        "unit": "USD_millions", "currency": "USD",
    }))
    assert result.ok, result.render()


def test_one_bound_alone_is_neither_a_range_nor_a_point(ontology):
    for lone in ("low_value", "high_value"):
        result = ontology.validate_event(guidance_event({
            "guided_metric": "revenue", lone: "1000",
            "unit": "USD_millions", "currency": "USD",
        }))
        assert E_EVENT_VALUE_RANGE in result.codes, lone


def test_an_inverted_range_is_refused(ontology):
    result = ontology.validate_event(guidance_event({
        "guided_metric": "revenue", "low_value": "1100", "high_value": "1000",
        "unit": "USD_millions", "currency": "USD",
    }))
    assert E_EVENT_VALUE_RANGE in result.codes


def test_qualitative_guidance_carries_neither_bound_and_is_not_made_to_invent_one(ontology):
    """§5.3: coercing a directional outlook into a number fabricates a figure."""
    result = ontology.validate_event(guidance_event({"guided_metric": "revenue"}))
    assert result.ok, result.render()


def test_a_bound_without_a_unit_is_not_a_value(ontology):
    result = ontology.validate_event(guidance_event({
        "guided_metric": "revenue", "low_value": "1000", "high_value": "1100",
    }))
    assert E_EVENT_VALUE_RANGE in result.codes


def test_the_unit_is_checked_against_the_same_vocabulary_observations_face(ontology):
    result = ontology.validate_event(guidance_event({
        "guided_metric": "revenue", "low_value": "1000", "high_value": "1100",
        "unit": "billions_of_dollars",
    }))
    assert E_UNKNOWN_UNIT in result.codes


def test_a_monetary_unit_requires_a_currency(ontology):
    result = ontology.validate_event(guidance_event({
        "guided_metric": "revenue", "low_value": "1000", "high_value": "1100",
        "unit": "USD_millions",
    }))
    assert E_MISSING_CURRENCY in result.codes


def test_a_non_monetary_unit_does_not_require_a_currency(ontology):
    result = ontology.validate_event(guidance_event({
        "guided_metric": "homes_sold", "low_value": "3000", "high_value": "3200",
        "unit": "homes",
    }))
    assert result.ok, result.render()


def test_guided_metric_must_resolve_to_a_declared_metric(ontology):
    result = ontology.validate_event(guidance_event({
        "guided_metric": "adjusted_revenue_growth",
        "low_value": "10", "high_value": "12", "unit": "percent",
    }))
    assert E_EVENT_PROPERTY_REFERENCE in result.codes


def test_guidance_that_does_not_say_what_is_guided_states_nothing(ontology):
    result = ontology.validate_event(guidance_event({
        "low_value": "1000", "high_value": "1100",
        "unit": "USD_millions", "currency": "USD",
    }))
    assert E_EVENT_PROPERTY_TYPE in result.codes


def test_an_event_type_with_no_property_contract_is_unaffected(ontology):
    """The typing is opt-in per event type, so it cannot newly refuse anything else."""
    definition = ontology.registry.concept("reverse_stock_split")
    assert definition.property_contract is None
    result = ontology.validate_event(EventInstance(
        event_id="evt-rss",
        event_type_id="reverse_stock_split",
        occurred_on="2022-08-19",
        properties={"ratio": "1-for-50 approximately", "effective_date": "2022-08-19"},
        participants=({"role": "issuer", "entity_id": "opendoor",
                       "entity_type": "public_company"},),
        evidence=(EvidenceReference(evidence_kind="filing_metadata",
                                    accession="0001801169-22-000001"),),
    ))
    assert result.ok, result.render()


# -- D3: the future-period invariant ------------------------------------------------------


def test_a_reported_observation_of_a_period_after_its_filing_is_refused(ontology):
    result = ontology.validate_observation(
        observation(period_start="2027-10-01", period_end="2027-12-31"),
        carrier_date="2024-02-15",
    )
    assert E_FUTURE_PERIOD in result.codes


def test_the_rule_is_not_reject_anything_after_now(ontology):
    """A 2027 period filed in 2028 is a normal report, whatever today's date is.

    A wall-clock rule would refuse this now and accept it in 2029, so replaying the same run
    twice would give two answers. The comparison is against the carrier, not the clock.
    """
    result = ontology.validate_observation(
        observation(period_start="2027-10-01", period_end="2027-12-31"),
        carrier_date="2028-02-15",
    )
    assert E_FUTURE_PERIOD not in result.codes


@pytest.mark.parametrize("lag_days,filing_date", [(15, "2021-01-15"), (1045, "2023-11-11")])
def test_the_measured_extremes_of_reporting_lag_are_permitted(ontology, lag_days, filing_date):
    """+15 and 1,045 days: the minimum and maximum measured over the run on 2026-08-03."""
    result = ontology.validate_observation(
        observation(period_start="2020-10-01", period_end="2020-12-31"),
        carrier_date=filing_date,
    )
    assert E_FUTURE_PERIOD not in result.codes, lag_days


def test_a_period_ending_on_the_filing_date_is_permitted(ontology):
    """The boundary is `after`, not `on`: an 8-K can report an instant dated that same day."""
    result = ontology.validate_observation(
        observation(period_start=None, period_end=None, instant_date="2022-10-19",
                    metric_id="housing_inventory_homes", value=5326),
        carrier_date="2022-10-19",
    )
    assert E_FUTURE_PERIOD not in result.codes


def test_a_guided_assertion_on_a_future_period_is_permitted(ontology):
    result = ontology.validate_observation(
        observation(period_start="2027-10-01", period_end="2027-12-31",
                    assertion_type=AssertionType.GUIDED),
        carrier_date="2024-02-15",
    )
    assert E_FUTURE_PERIOD not in result.codes
    assert result.ok, result.render()


def test_a_calculated_future_period_fact_needs_the_ontology_to_name_the_metric(ontology):
    """Not blanket-permitted: an unlisted one is a mislabelled guidance figure's shape."""
    parameters = ontology.definitions.constraints
    future = observation(period_start="2027-10-01", period_end="2027-12-31",
                         assertion_type=AssertionType.CALCULATED)

    refused = ValidationResult()
    check_future_period(future, parameters, refused, carrier_date="2024-02-15")
    assert E_FUTURE_PERIOD in refused.codes

    permitted = ValidationResult()
    from dataclasses import replace
    check_future_period(
        future,
        replace(parameters,
                future_period_allowed_calculated_metrics=frozenset({future.metric_id})),
        permitted,
        carrier_date="2024-02-15",
    )
    assert permitted.ok


def test_an_instant_after_the_filing_is_refused_as_well_as_a_period_end(ontology):
    result = ontology.validate_observation(
        observation(period_start=None, period_end=None, instant_date="2027-12-31",
                    metric_id="housing_inventory_homes", value=5326),
        carrier_date="2024-02-15",
    )
    assert E_FUTURE_PERIOD in result.codes


def test_the_carrier_falls_back_to_reported_at(ontology):
    """No lane populates `reported_at` today, but the contract names it as the carrier."""
    result = ontology.validate_observation(
        observation(period_start="2027-10-01", period_end="2027-12-31",
                    reported_at="2024-02-15")
    )
    assert E_FUTURE_PERIOD in result.codes


def test_with_no_carrier_date_the_rule_abstains_rather_than_guesses(ontology):
    """This is why the current run is unaffected: `reported_at` is unpopulated everywhere."""
    result = ontology.validate_observation(
        observation(period_start="2027-10-01", period_end="2027-12-31")
    )
    assert E_FUTURE_PERIOD not in result.codes
    assert result.ok, result.render()


# -- D4: the two new source lanes ---------------------------------------------------------


def test_the_lanes_the_next_stages_need_are_declared(ontology):
    assert SourceLane.TRANSCRIPT == "transcript"
    assert SourceLane.MARKET_DATA == "market_data"


def test_the_new_lanes_are_accepted_on_an_observation(ontology):
    """Declared, so a lane implementation is not also a contract change."""
    for lane in (SourceLane.TRANSCRIPT, SourceLane.MARKET_DATA):
        result = ontology.validate_observation(observation(source_lane=lane))
        assert result.ok, f"{lane}: {result.render()}"


def test_no_metric_yet_declares_a_preference_for_either_new_lane(ontology):
    """Adding the members must not silently re-rank any existing metric's preferences."""
    for metric in ontology.definitions.metrics:
        assert SourceLane.TRANSCRIPT not in metric.source_lane_preferences
        assert SourceLane.MARKET_DATA not in metric.source_lane_preferences
