"""One test per distinction the research says must not collapse.

The rules themselves are tested in `test_definition_constraints` and `test_claim_validation`
against purpose-built ontologies. These tests assert that the SHIPPED ontology actually
declares each distinction — a rule that is never triggered because the data forgot to
declare it protects nothing.
"""

from __future__ import annotations


from ontology.core.values import AssertionType
from ontology_factories import observation


def test_the_five_home_counts_are_five_concepts(registry):
    ids = {"homes_sold", "homes_purchased", "homes_under_contract",
           "acquisition_contracts", "housing_inventory_homes"}
    assert {registry.metric(i).concept_id for i in ids} == ids


def test_the_home_counts_differ_in_period_type_not_only_in_name(registry):
    assert str(registry.metric("homes_sold").period_type) == "duration"
    assert str(registry.metric("acquisition_contracts").period_type) == "duration"
    assert str(registry.metric("homes_under_contract").period_type) == "instant"
    assert str(registry.metric("housing_inventory_homes").period_type) == "instant"


def test_distinct_from_is_declared_on_the_metrics_themselves(registry):
    assert "homes_under_contract" in registry.metric("homes_purchased").distinct_from
    assert "acquisition_contracts" in registry.metric("homes_purchased").distinct_from


def test_buy_side_and_sell_side_contracts_are_separate_concepts(registry):
    assert "homes_under_contract" in registry.metric(
        "homes_under_resale_contract"
    ).distinct_from


def test_gaap_and_non_gaap_measures_carry_different_status(registry):
    assert str(registry.metric("gaap_gross_profit").gaap_status) == "gaap"
    assert str(registry.metric("adjusted_gross_profit").gaap_status) == "non_gaap"
    assert str(registry.metric("homes_sold").gaap_status) == "operating_kpi"
    assert str(registry.metric("mortgage_rate").gaap_status) == "macro_indicator"


def test_non_gaap_reconciles_to_gaap_rather_than_equalling_it(registry):
    assert registry.metric("adjusted_gross_profit").reconciles_to == "gaap_gross_profit"
    assert registry.metric("contribution_profit").reconciles_to == "gaap_gross_profit"
    # A reconciliation is not an equivalence: they remain separate concepts.
    assert "gaap_gross_profit" in registry.metric("contribution_profit").distinct_from


def test_no_us_gaap_mapping_claims_equivalence_for_a_non_gaap_measure(registry):
    for metric_id in ("adjusted_gross_profit", "contribution_profit", "adjusted_ebitda"):
        for mapping in registry.metric(metric_id).external_mappings:
            if str(mapping.mapping_system) == "standard_us_gaap":
                assert str(mapping.mapping_type) in {"related", "no_mapping", "broader"}


def test_profit_and_margin_are_never_the_same_concept(registry):
    profit = registry.metric("contribution_profit")
    margin = registry.metric("contribution_margin")
    assert str(profit.value_type) == "monetary"
    assert str(margin.value_type) == "percentage"
    assert margin.concept_id in profit.distinct_from


def test_operating_kpis_record_that_no_xbrl_tag_exists(registry):
    """Measured, not assumed: 336 custom elements across 97 taxonomy files, no KPI tag."""
    for metric_id in ("homes_sold", "homes_purchased", "adjusted_gross_profit",
                      "contribution_profit"):
        lanes = {str(l) for l in registry.metric(metric_id).forbidden_source_lanes}
        assert "xbrl" in lanes, metric_id


def test_a_no_mapping_states_the_absence_rather_than_omitting_it(registry):
    mappings = registry.metric("homes_sold").external_mappings
    assert any(str(m.mapping_type) == "no_mapping" for m in mappings)
    assert all(m.verified for m in mappings)


def test_the_120_day_metric_keeps_its_three_filed_denominator_wordings(registry):
    metric = registry.metric("pct_homes_on_market_gt_120_days")
    assert metric.population is not None
    assert len(metric.population.raw_variants) >= 3
    assert metric.population.confidence == "low"


def test_the_120_day_ambiguity_is_recorded_rather_than_resolved(registry):
    ambiguities = registry.metric("pct_homes_on_market_gt_120_days").ambiguities
    assert any(a.impact == "high" for a in ambiguities)


def test_the_120_day_threshold_is_structured_not_buried_in_the_label(registry):
    metric = registry.metric("pct_homes_on_market_gt_120_days")
    assert str(metric.comparison_operator) == ">"
    assert metric.threshold_value == 120
    assert metric.threshold_unit == "days"
    assert metric.measurement_start == "initial_listing_date"


def test_roles_are_time_bound_and_scoped_to_a_context(registry):
    for role_id in ("lender", "borrower", "partner"):
        role = registry.concept(role_id)
        assert role.time_bound, role_id
        assert role.allowed_player_types


def test_a_role_never_appears_as_an_entity_type(definitions):
    entity_ids = {e.concept_id for e in definitions.entity_types}
    assert not entity_ids & {r.concept_id for r in definitions.role_types}


def test_competitive_and_partnership_edges_are_both_temporal(registry):
    """The same counterparty appears as competitor in one period and partner in another."""
    assert registry.relationship("COMPETES_WITH").temporal
    assert registry.relationship("PARTNERED_WITH").temporal


def test_the_live_dashboard_is_marked_discovery_only(registry):
    channel = registry.instance("opendoor_accountable")
    assert channel.properties["canonicality"] == "discovery_only"
    assert "Wayback" in channel.properties["historical_reproducibility"]


def test_edgar_remains_canonical(registry):
    assert registry.instance("sec_edgar").properties["canonicality"] == "canonical"


def test_relationships_separate_what_was_stated_from_what_was_derived(registry):
    assert str(registry.relationship("COMPETES_WITH").inference_level) == "asserted"
    assert str(registry.relationship("COMPUTED_FROM").inference_level) == "derived"
    assert str(registry.relationship("SUPERSEDES").inference_level) == "derived"


def test_a_restatement_supersedes_rather_than_overwrites(registry):
    """What was originally reported is itself a fact and is not discarded."""
    supersedes = registry.relationship("SUPERSEDES")
    assert supersedes.allowed_source_types == ("metric_observation",)
    assert supersedes.temporal


def test_guidance_is_not_an_earnings_release(registry):
    """The restriction used to be prose, and this test used to grep it.

    A substring match on `inference_restrictions` proved only that a word appeared in a
    sentence no code read. The restriction is now declared where a check can enforce it
    *(F0 Part D1)*, so the test asserts the declaration.
    """
    guidance = registry.concept("guidance_issuance")
    assert guidance.inference_restrictions
    assert AssertionType.REPORTED in guidance.forbidden_assertion_types
    assert AssertionType.GUIDED not in guidance.forbidden_assertion_types


def test_market_entry_forbids_inferring_an_event_from_a_list_that_grew(registry):
    assert "NOT evidence" in registry.concept("market_entry").inference_restrictions


def test_reported_and_calculated_stay_distinguishable_end_to_end(ontology):
    reported = observation()
    assert reported.formula_version_id is None
    assert ontology.validate_observation(reported).ok

    mislabelled = observation(formula_version_id="adjusted_gross_margin_v1")
    assert not ontology.validate_observation(mislabelled).ok
