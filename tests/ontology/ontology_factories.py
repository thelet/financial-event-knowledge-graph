"""Builders for hand-made definitions and claims.

Tests that check a rule need an ontology containing exactly the thing the rule is about.
Loading the real one and hoping it happens to contain a violation is not a test of the rule
— so these build the smallest ontology that exercises it.
"""

from __future__ import annotations

from typing import Any

from ontology.contracts import OntologyDefinitions
from ontology.core.constraints import ConstraintParameters, DistinctGroup
from ontology.core.models import (
    AliasEntry,
    EntityTypeDefinition,
    EvidenceReference,
    MetricDefinition,
    MetricObservation,
    OntologyMetadata,
)

METADATA = OntologyMetadata(ontology_id="test_ontology", semantic_version="0.0.1")


def entity(concept_id: str, **kwargs: Any) -> EntityTypeDefinition:
    return EntityTypeDefinition(concept_id=concept_id, label=concept_id, **kwargs)


def metric(concept_id: str, **kwargs: Any) -> MetricDefinition:
    defaults: dict[str, Any] = {
        "label": concept_id,
        "metric_category": "operating",
        "value_type": "integer",
        "unit": "homes",
        "period_type": "duration",
        "gaap_status": "operating_kpi",
        "subject_types": ("company",),
    }
    return MetricDefinition(concept_id=concept_id, **{**defaults, **kwargs})


def definitions(**kwargs: Any) -> OntologyDefinitions:
    return OntologyDefinitions(metadata=METADATA, **kwargs)


def distinct_group(*concept_ids: str, group_id: str = "g") -> ConstraintParameters:
    return ConstraintParameters(
        mutually_distinct_groups=(
            DistinctGroup(group_id=group_id, concept_ids=concept_ids, rationale="test"),
        )
    )


def alias(surface: str, *concept_ids: str, ambiguous: bool = False) -> AliasEntry:
    return AliasEntry(alias=surface, concept_ids=concept_ids, ambiguous=ambiguous)


def evidence(**kwargs: Any) -> EvidenceReference:
    defaults = {"evidence_kind": "normalized_passage", "passage_id": "psg-1",
                "document_id": "doc-1"}
    return EvidenceReference(**{**defaults, **kwargs})


def observation(**kwargs: Any) -> MetricObservation:
    defaults: dict[str, Any] = {
        "observation_id": "obs-1",
        "metric_id": "homes_sold",
        "subject_entity_id": "opendoor",
        "subject_type": "public_company",
        "value": 100,
        "unit": "homes",
        "period_start": "2023-01-01",
        "period_end": "2023-03-31",
        "source_lane": "normalized_table",
        "evidence": (evidence(),),
    }
    return MetricObservation(**{**defaults, **kwargs})
