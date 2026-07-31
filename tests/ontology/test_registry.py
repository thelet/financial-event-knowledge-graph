"""Concept, alias, formula and taxonomy lookup."""

from __future__ import annotations

import pytest

from ontology.core.errors import ConceptNotFoundError


def test_concept_lookup_and_miss(registry):
    assert registry.concept("homes_sold").label == "Homes sold"
    with pytest.raises(ConceptNotFoundError):
        registry.concept("no_such_concept")


def test_metric_lookup_rejects_a_non_metric(registry):
    with pytest.raises(ConceptNotFoundError, match="not a metric"):
        registry.metric("company")


def test_canonical_label_resolves_without_being_repeated_as_an_alias(registry):
    assert [c.concept_id for c in registry.resolve_alias("Contribution Margin")] == [
        "contribution_margin"
    ]


def test_alias_resolution_is_case_and_quote_insensitive(registry):
    quoted = registry.resolve_alias('Percentage of homes "on the market" for greater than 120 days (at period end)')
    plain = registry.resolve_alias(
        "PERCENTAGE OF HOMES ON THE MARKET FOR GREATER THAN 120 DAYS (AT PERIOD END)"
    )
    assert [c.concept_id for c in quoted] == ["pct_homes_on_market_gt_120_days"]
    assert quoted == plain


def test_ambiguous_alias_returns_every_candidate_rather_than_guessing(registry):
    """Picking one is how two distinct metrics get merged."""
    resolved = [c.concept_id for c in registry.resolve_alias("gross profit")]
    assert resolved == ["adjusted_gross_profit", "gaap_gross_profit"]
    assert registry.is_ambiguous("gross profit")


def test_resolution_order_is_stable(registry):
    assert registry.resolve_alias("margin") == registry.resolve_alias("margin")


def test_unknown_alias_resolves_to_nothing(registry):
    assert registry.resolve_alias("earnings before nothing at all") == ()


def test_formula_resolution_is_by_observation_date(registry):
    """A 2024 filing restating FY2021 still needs the FY2021 formula."""
    assert registry.formula_for("adjusted_gross_profit", "2020-06-30").version == "1"
    assert registry.formula_for("adjusted_gross_profit", "2021-12-31").version == "1"
    assert registry.formula_for("adjusted_gross_profit", "2022-01-01").version == "2"
    assert registry.formula_for("adjusted_gross_profit", "2025-12-31").version == "2"


def test_adjusted_gross_profit_drift_is_visible_in_the_components(registry):
    v1, v2 = registry.formulas_for("adjusted_gross_profit")
    labels = lambda f: {c.label for c in f.adjustment_components if c.included}
    assert "Restructuring adjustment" in labels(v1)
    assert "Restructuring adjustment" not in labels(v2)
    excluded = {c.label for c in v2.adjustment_components if not c.included}
    assert "Restructuring adjustment" in excluded, "exclusion must be recorded, not omitted"


def test_formula_lookup_before_any_version_exists_returns_none(registry):
    assert registry.formula_for("adjusted_gross_profit", "2019-01-01") is None


def test_metric_without_a_formula_has_none(registry):
    assert registry.formula_for("homes_sold") is None


def test_subtype_chain(registry):
    assert registry.ancestors("public_company") == ("company",)
    assert registry.is_a("public_company", "company")
    assert not registry.is_a("company", "public_company")


def test_accepts_type_admits_subtypes(registry):
    assert registry.accepts_type(("company",), "subsidiary")
    assert not registry.accepts_type(("person",), "subsidiary")


def test_instances_are_addressable(registry):
    assert registry.instance("opendoor").label == "Opendoor Technologies Inc."
    assert registry.instance("opendoor_accountable").properties["canonicality"] == "discovery_only"
