"""The public contract of the ontology layer.

Callers depend on these protocols, not on `real_estate_marketplace_v1`. A second ontology
version — or a different domain entirely — satisfies the same interface, and `context.py`
decides which one is in use.

Structural typing (Protocol) rather than inheritance, matching the rest of the repository:
an implementation satisfies the contract by having the right shape, without importing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

from .core.constraints import ConstraintParameters
from .core.errors import ValidationResult
from .core.models import (
    AliasEntry,
    AgreementTypeDefinition,
    ClaimTypeDefinition,
    ConceptDefinition,
    ConceptInstance,
    EntityTypeDefinition,
    EventInstance,
    EventTypeDefinition,
    EvidenceTypeDefinition,
    InstrumentTypeDefinition,
    MetricDefinition,
    MetricFormulaVersion,
    MetricObservation,
    OntologyClaim,
    OntologyMetadata,
    RelationshipDefinition,
    RelationshipInstance,
    RoleTypeDefinition,
    StatusTypeDefinition,
)


@dataclass(frozen=True)
class OntologyDefinitions:
    """Everything a version package produces, before indexing.

    The boundary between loading and querying. A loader's job ends here; the registry's job
    starts here. Keeping them apart is what lets the registry be tested against
    hand-built definitions with no YAML on disk.
    """

    metadata: OntologyMetadata
    entity_types: tuple[EntityTypeDefinition, ...] = ()
    role_types: tuple[RoleTypeDefinition, ...] = ()
    instrument_types: tuple[InstrumentTypeDefinition, ...] = ()
    agreement_types: tuple[AgreementTypeDefinition, ...] = ()
    metrics: tuple[MetricDefinition, ...] = ()
    formula_versions: tuple[MetricFormulaVersion, ...] = ()
    event_types: tuple[EventTypeDefinition, ...] = ()
    relationships: tuple[RelationshipDefinition, ...] = ()
    evidence_types: tuple[EvidenceTypeDefinition, ...] = ()
    claim_types: tuple[ClaimTypeDefinition, ...] = ()
    status_types: tuple[StatusTypeDefinition, ...] = ()
    instances: tuple[ConceptInstance, ...] = ()
    aliases: tuple[AliasEntry, ...] = ()
    constraints: ConstraintParameters = field(default_factory=ConstraintParameters)

    @property
    def concepts(self) -> tuple[ConceptDefinition, ...]:
        """Every definition, in a stable category order."""
        return (
            *self.entity_types,
            *self.role_types,
            *self.instrument_types,
            *self.agreement_types,
            *self.metrics,
            *self.formula_versions,
            *self.event_types,
            *self.relationships,
            *self.evidence_types,
            *self.claim_types,
            *self.status_types,
        )


@runtime_checkable
class ConceptRegistry(Protocol):
    """Lookup over a resolved ontology."""

    def concept(self, concept_id: str) -> ConceptDefinition: ...

    def find(self, concept_id: str) -> ConceptDefinition | None: ...

    def metric(self, metric_id: str) -> MetricDefinition: ...

    def by_category(self, category: str) -> tuple[ConceptDefinition, ...]: ...

    def resolve_alias(self, surface_form: str) -> tuple[ConceptDefinition, ...]: ...

    def formula_for(
        self, metric_id: str, as_of: str | None = None
    ) -> MetricFormulaVersion | None: ...

    def ancestors(self, concept_id: str) -> tuple[str, ...]: ...

    def is_a(self, concept_id: str, ancestor_id: str) -> bool: ...


@runtime_checkable
class Ontology(Protocol):
    """What a caller holds. Query and validation, nothing else.

    No `run`, no `execute`: this is a library, not a pipeline. There is no state to advance.
    """

    @property
    def metadata(self) -> OntologyMetadata: ...

    @property
    def definition_hash(self) -> str: ...

    @property
    def registry(self) -> ConceptRegistry: ...

    def validate_claim(self, claim: OntologyClaim) -> ValidationResult: ...

    def validate_observation(
        self, observation: MetricObservation, carrier_date: str | None = None
    ) -> ValidationResult: ...

    def validate_event(self, event: EventInstance) -> ValidationResult: ...

    def validate_relationship(self, instance: RelationshipInstance) -> ValidationResult: ...

    def validate_claims(self, claims: Sequence[OntologyClaim]) -> ValidationResult: ...

    def snapshot(self) -> dict[str, Any]: ...


@runtime_checkable
class OntologyLoader(Protocol):
    """What a version package must provide. One method — loading is not a pipeline either."""

    def load(self) -> OntologyDefinitions: ...
