"""The one composition root.

`load_ontology` is the only place that knows which version package exists and in what order
loading, validation, indexing and hashing happen. Callers ask for an id and receive an
`Ontology`; nothing else in the codebase constructs a loader or a registry.

Registration is an explicit table rather than a plugin scan: three lines of import beat a
directory walk that fails silently when a package is renamed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from .contracts import OntologyDefinitions
from .core.errors import OntologyError, OntologyLoadError, ValidationResult
from .core.models import (
    EventInstance,
    MetricObservation,
    OntologyClaim,
    OntologyMetadata,
    RelationshipInstance,
)
from .registry import InMemoryConceptRegistry
from .serialization import definition_hash, render_snapshot, snapshot
from .validation import ClaimValidator, validate_definitions
from .versions.real_estate_marketplace_v1.public import (
    ONTOLOGY_ID as REAL_ESTATE_MARKETPLACE_V1,
    load_definitions as _load_real_estate_marketplace_v1,
)

_VERSIONS: dict[str, Callable[[Path | None], OntologyDefinitions]] = {
    REAL_ESTATE_MARKETPLACE_V1: _load_real_estate_marketplace_v1,
}

DEFAULT_ONTOLOGY_ID = REAL_ESTATE_MARKETPLACE_V1


class LoadedOntology:
    """A resolved, validated, indexed ontology. Query and validate; there is nothing to run."""

    def __init__(self, definitions: OntologyDefinitions) -> None:
        self._definitions = definitions
        self._registry = InMemoryConceptRegistry(definitions)
        self._validator = ClaimValidator(self._registry)
        self._hash = definition_hash(definitions)

    @property
    def metadata(self) -> OntologyMetadata:
        return self._definitions.metadata

    @property
    def definitions(self) -> OntologyDefinitions:
        return self._definitions

    @property
    def registry(self) -> InMemoryConceptRegistry:
        return self._registry

    @property
    def definition_hash(self) -> str:
        return self._hash

    def validate_claim(self, claim: OntologyClaim) -> ValidationResult:
        return self._validator.validate_claim(claim)

    def validate_claims(self, claims: Sequence[OntologyClaim]) -> ValidationResult:
        return self._validator.validate_claims(claims)

    def validate_observation(
        self, observation: MetricObservation, carrier_date: str | None = None
    ) -> ValidationResult:
        """`carrier_date`: the filing date of the document reporting it, when the caller has
        it. See `ClaimValidator.validate_observation`."""
        return self._validator.validate_observation(observation, carrier_date)

    def validate_event(self, event: EventInstance) -> ValidationResult:
        return self._validator.validate_event(event)

    def validate_relationship(self, instance: RelationshipInstance) -> ValidationResult:
        return self._validator.validate_relationship(instance)

    def snapshot(self) -> dict[str, Any]:
        return snapshot(self._definitions)

    def render_snapshot(self) -> str:
        return render_snapshot(self._definitions)

    def __repr__(self) -> str:
        return (
            f"<LoadedOntology {self.metadata.ontology_id} "
            f"v{self.metadata.semantic_version} {self._hash[:12]}>"
        )


def available_ontologies() -> tuple[str, ...]:
    return tuple(sorted(_VERSIONS))


def load_ontology(
    ontology_id: str = DEFAULT_ONTOLOGY_ID, directory: Path | None = None
) -> LoadedOntology:
    """Load, validate and index one ontology version.

    Validation runs here rather than inside the loader so that definitions built in memory
    face exactly the same checks as definitions read from YAML. A definition-level error
    raises: a caller must never receive a half-valid ontology and discover the problem when
    a claim is validated against it.
    """
    load = _VERSIONS.get(ontology_id)
    if load is None:
        raise OntologyError(
            f"unknown ontology {ontology_id!r}; available: {list(available_ontologies())}"
        )
    return build_ontology(load(directory))


def build_ontology(definitions: OntologyDefinitions) -> LoadedOntology:
    """Validate and index an already-loaded set of definitions."""
    result = validate_definitions(definitions)
    if not result.ok:
        raise OntologyLoadError(result.issues)
    return LoadedOntology(definitions)
