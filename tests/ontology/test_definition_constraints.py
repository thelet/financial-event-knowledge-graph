"""Definition-time constraints, each exercised on an ontology built to violate it.

Loading the real ontology and hoping it happens to contain a violation would not test the
rule; every case here constructs the smallest ontology that does.
"""

from __future__ import annotations

import pytest

from ontology.context import build_ontology
from ontology.core.constraints import ConstraintParameters
from ontology.core.errors import (
    E_ALIAS_COLLISION,
    E_DUPLICATE_ID,
    E_FORMULA_OVERLAP,
    E_INHERITANCE_CYCLE,
    E_INVALID_INHERITANCE,
    E_INVENTED_URI,
    E_KPI_AS_MACRO,
    E_ROLE_AS_ENTITY,
    E_UNKNOWN_REFERENCE,
    E_UNKNOWN_UNIT,
    OntologyLoadError,
)
from ontology.core.models import ExternalMapping, MetricFormulaVersion
from ontology.validation import validate_definitions

from ontology_factories import alias, definitions, distinct_group, entity, metric


def codes(built) -> set[str]:
    return validate_definitions(built).codes


def test_duplicate_concept_id_is_rejected():
    built = definitions(entity_types=(entity("company"), entity("company")))
    assert E_DUPLICATE_ID in codes(built)


def test_reference_to_an_undeclared_concept_is_rejected():
    built = definitions(
        entity_types=(entity("company"),),
        metrics=(metric("m", subject_types=("no_such_type",)),),
    )
    assert E_UNKNOWN_REFERENCE in codes(built)


def test_inheritance_across_categories_is_rejected():
    built = definitions(
        entity_types=(entity("company"), entity("thing", is_a="m")),
        metrics=(metric("m"),),
    )
    assert E_INVALID_INHERITANCE in codes(built)


def test_inheritance_cycle_is_rejected():
    built = definitions(entity_types=(entity("a", is_a="b"), entity("b", is_a="a")))
    assert E_INHERITANCE_CYCLE in codes(built)


def test_a_role_declared_as_an_entity_type_is_rejected():
    """Lender is something a company plays in a facility, never something a company is."""
    built = definitions(
        entity_types=(entity("company"), entity("lender")),
        constraints=ConstraintParameters(
            role_concepts_forbidden_as_entity_types=frozenset({"lender"})
        ),
    )
    assert E_ROLE_AS_ENTITY in codes(built)


def test_the_real_ontology_declares_no_role_as_an_entity_type(definitions_fixture=None):
    from ontology import load_ontology

    loaded = load_ontology()
    entity_ids = {e.concept_id for e in loaded.definitions.entity_types}
    role_ids = {r.concept_id for r in loaded.definitions.role_types}
    assert not entity_ids & role_ids


def test_unknown_unit_is_rejected():
    built = definitions(
        entity_types=(entity("company"),), metrics=(metric("m", unit="furlongs"),)
    )
    assert E_UNKNOWN_UNIT in codes(built)


def test_no_mapping_carrying_a_uri_is_rejected_as_an_invented_uri():
    built = definitions(
        entity_types=(
            entity("company", external_mappings=(ExternalMapping(
                mapping_system="fibo", mapping_type="no_mapping",
                external_uri="https://example.org/invented",
            ),)),
        ),
    )
    assert E_INVENTED_URI in codes(built)


def test_economic_indicator_on_a_company_kpi_is_rejected():
    """FIBO scopes EconomicIndicator to a statistical region; a homes count is not that."""
    parameters = ConstraintParameters(
        economic_indicator_labels=frozenset({"EconomicIndicator"}),
        economic_indicator_allowed_categories=frozenset({"macroeconomic"}),
    )
    built = definitions(
        entity_types=(entity("company"),),
        metrics=(metric("kpi", external_mappings=(ExternalMapping(
            mapping_system="fibo", mapping_type="exact",
            external_label="EconomicIndicator",
        ),)),),
        constraints=parameters,
    )
    assert E_KPI_AS_MACRO in codes(built)


def test_economic_indicator_on_a_macro_metric_is_allowed():
    parameters = ConstraintParameters(
        economic_indicator_labels=frozenset({"EconomicIndicator"}),
        economic_indicator_allowed_categories=frozenset({"macroeconomic"}),
    )
    built = definitions(
        entity_types=(entity("company"),),
        metrics=(metric("rate", metric_category="macroeconomic", value_type="percentage",
                        unit="percent", gaap_status="macro_indicator",
                        external_mappings=(ExternalMapping(
                            mapping_system="fibo", mapping_type="exact",
                            external_label="EconomicIndicator",
                        ),)),),
        constraints=parameters,
    )
    assert E_KPI_AS_MACRO not in codes(built)


def test_the_real_ontology_maps_economic_indicator_only_to_macro_metrics(ontology):
    for metric_definition in ontology.definitions.metrics:
        for mapping in metric_definition.external_mappings:
            if mapping.external_label == "EconomicIndicator" and mapping.mapping_type != "pattern_only":
                assert str(metric_definition.metric_category) == "macroeconomic", (
                    f"{metric_definition.concept_id} is not a macro metric"
                )


def test_an_undeclared_alias_collision_between_distinct_metrics_is_an_error():
    built = definitions(
        entity_types=(entity("company"),),
        metrics=(metric("homes_sold"), metric("homes_purchased")),
        aliases=(alias("homes", "homes_sold", "homes_purchased"),),
        constraints=distinct_group("homes_sold", "homes_purchased"),
    )
    result = validate_definitions(built)
    assert E_ALIAS_COLLISION in result.codes
    assert not result.ok, "an unacknowledged collision must fail the load"


def test_a_collision_declared_ambiguous_is_recorded_but_does_not_fail_the_load():
    """An acknowledged ambiguity is a finding; silently merging the two would be the defect."""
    built = definitions(
        entity_types=(entity("company"),),
        metrics=(metric("homes_sold"), metric("homes_purchased")),
        aliases=(alias("homes", "homes_sold", "homes_purchased", ambiguous=True),),
        constraints=distinct_group("homes_sold", "homes_purchased"),
    )
    result = validate_definitions(built)
    assert E_ALIAS_COLLISION in result.codes
    assert result.ok


def test_overlapping_formula_versions_are_rejected():
    built = definitions(
        entity_types=(entity("company"),),
        metrics=(metric("m"),),
        formula_versions=(
            MetricFormulaVersion(concept_id="m_v1", label="m v1", metric_id="m",
                                 version="1", valid_from="2020-01-01",
                                 valid_to="2022-12-31", expression="x"),
            MetricFormulaVersion(concept_id="m_v2", label="m v2", metric_id="m",
                                 version="2", valid_from="2022-01-01", expression="y"),
        ),
    )
    assert E_FORMULA_OVERLAP in codes(built)


def test_build_ontology_raises_rather_than_returning_a_half_valid_ontology():
    built = definitions(entity_types=(entity("company"), entity("company")))
    with pytest.raises(OntologyLoadError):
        build_ontology(built)
