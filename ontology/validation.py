"""Validation of definitions at load time and of claims at runtime.

Orchestration only. Every individual check lives in `core.constraints`, so this module reads
as a list of what is checked and in what order, and a new rule is added in one place.
"""

from __future__ import annotations

from typing import Sequence

from .contracts import OntologyDefinitions
from .core import constraints as rules
from .core.errors import (
    E_ROLE_CONTEXT_REQUIRED,
    E_ROLE_PLAYER_TYPE,
    E_UNKNOWN_REFERENCE,
    ValidationResult,
)
from .core.models import (
    EventInstance,
    MetricObservation,
    OntologyClaim,
    RelationshipInstance,
)
from .core.values import ClaimKind, ConceptCategory, Severity
from .registry import InMemoryConceptRegistry


def validate_definitions(definitions: OntologyDefinitions) -> ValidationResult:
    """Everything that can be decided from the definitions alone.

    Runs on every load, including loads of hand-built definitions in tests, which is why it
    takes `OntologyDefinitions` rather than a directory.
    """
    result = ValidationResult()
    concepts = definitions.concepts
    known_ids = frozenset(c.concept_id for c in concepts)
    metric_ids = frozenset(m.concept_id for m in definitions.metrics)
    parameters = definitions.constraints

    rules.check_unique_ids(concepts, result)
    rules.check_references(concepts, known_ids, result)
    rules.check_inheritance(concepts, result)
    rules.check_roles_not_entity_types(
        (e.concept_id for e in definitions.entity_types), parameters, result
    )
    rules.check_units(definitions.metrics, result)
    rules.check_external_mappings(
        concepts,
        {m.concept_id: m.metric_category for m in definitions.metrics},
        parameters,
        result,
    )
    rules.check_formula_versions(definitions.formula_versions, metric_ids, result)
    rules.check_relationship_endpoints(
        definitions.relationships, known_ids | _runtime_endpoint_types(definitions), result
    )
    _check_aliases(definitions, known_ids, result)
    _check_distinct_groups(definitions, metric_ids, result)
    return result


def _runtime_endpoint_types(definitions: OntologyDefinitions) -> frozenset[str]:
    """Relationship endpoints may name a runtime kind, not only a concept.

    HAS_OBSERVATION points at a metric observation and EVIDENCED_BY at a passage. Neither is
    a declared concept — they are the categories and evidence kinds the runtime models use —
    so both vocabularies are admissible as endpoints.
    """
    return frozenset(
        {"metric_observation"}
        | {str(e.evidence_kind) for e in definitions.evidence_types}
        | {str(c.category) for c in definitions.concepts}
    )


def _check_aliases(
    definitions: OntologyDefinitions, known_ids: frozenset[str], result: ValidationResult
) -> None:
    for entry in definitions.aliases:
        for concept_id in entry.concept_ids:
            if concept_id not in known_ids:
                result.add(
                    E_UNKNOWN_REFERENCE,
                    f"aliases/{entry.alias}",
                    f"unknown concept {concept_id!r}",
                )

    # Collisions inside a mutually-distinct group are errors unless the ontology declares
    # the alias ambiguous — an acknowledged ambiguity is a finding, not a defect.
    collisions = ValidationResult()
    index = _alias_index(definitions)
    rules.check_alias_collisions(index, definitions.constraints, collisions)
    acknowledged = rules.declared_ambiguous(definitions.aliases)
    for issue in collisions.issues:
        surface = issue.path.split("/", 1)[-1]
        result.issues.append(
            issue if surface not in acknowledged
            else type(issue)(issue.code, issue.path, issue.message, Severity.INFO,
                             issue.concept_id)
        )


def _alias_index(definitions: OntologyDefinitions) -> dict[str, tuple[str, ...]]:
    from .core.identifiers import normalize_alias

    index: dict[str, set[str]] = {}
    for concept in definitions.concepts:
        if concept.category == ConceptCategory.METRIC_FORMULA:
            continue  # shares its metric's label by design; see registry._build_alias_index
        for surface in (concept.label, *concept.aliases):
            index.setdefault(normalize_alias(surface), set()).add(concept.concept_id)
    for entry in definitions.aliases:
        index.setdefault(normalize_alias(entry.alias), set()).update(entry.concept_ids)
    return {k: tuple(sorted(v)) for k, v in index.items()}


def _check_distinct_groups(
    definitions: OntologyDefinitions, metric_ids: frozenset[str], result: ValidationResult
) -> None:
    for group in definitions.constraints.mutually_distinct_groups:
        for concept_id in group.concept_ids:
            if concept_id not in metric_ids:
                result.add(
                    E_UNKNOWN_REFERENCE,
                    f"constraints/mutually_distinct_groups/{group.group_id}",
                    f"{concept_id!r} is not a metric definition",
                )


class ClaimValidator:
    """Validates what a future extractor emits, against the loaded definitions."""

    def __init__(self, registry: InMemoryConceptRegistry) -> None:
        self._registry = registry
        self._parameters = registry.definitions.constraints

    # -- observations --------------------------------------------------------------------

    def validate_observation(self, observation: MetricObservation) -> ValidationResult:
        result = ValidationResult()
        path = f"observation/{observation.observation_id}"
        metric = self._registry.find(observation.metric_id)
        if metric is None or not hasattr(metric, "metric_category"):
            result.add(E_UNKNOWN_REFERENCE, path,
                       f"unknown metric {observation.metric_id!r}")
            return result

        rules.check_observation_periods(observation, metric, result)
        rules.check_observation_unit(observation, metric, result)
        rules.check_observation_subject(
            observation, metric, self._registry.accepts_type, result
        )
        rules.check_observation_source_lane(observation, metric, result)
        rules.check_calculation_fields(observation, result)
        rules.check_formula_for_date(
            observation, self._registry.formulas_for(observation.metric_id), result
        )
        rules.check_confidence(observation.confidence, path, self._parameters, result)
        if metric.value_type == "percentage":
            rules.check_percentage_range(observation, metric, self._parameters, result)

        # A calculated value is re-derivable from its inputs, so it needs no evidence of its
        # own; a reported one is only as good as the passage it came from.
        rules.check_evidence(
            observation.evidence,
            required=observation.assertion_type != "calculated",
            path=path,
            parameters=self._parameters,
            result=result,
        )
        _warn_on_unpreferred_lane(observation, metric, result)
        return result

    # -- events --------------------------------------------------------------------------

    def validate_event(self, event: EventInstance) -> ValidationResult:
        result = ValidationResult()
        path = f"event/{event.event_id}"
        definition = self._registry.find(event.event_type_id)
        if definition is None or not hasattr(definition, "participants"):
            result.add(E_UNKNOWN_REFERENCE, path,
                       f"unknown event type {event.event_type_id!r}")
            return result

        rules.check_event_temporal(event, definition, result)
        rules.check_event_participants(event, definition, result)
        rules.check_confidence(event.confidence, path, self._parameters, result)
        rules.check_evidence(
            event.evidence, definition.evidence_required, path, self._parameters, result
        )
        self._check_event_metrics(event, definition, result)
        return result

    def _check_event_metrics(self, event, definition, result: ValidationResult) -> None:
        if not definition.allowed_metric_relationships:
            return
        for observation_id in event.reported_metric_observation_ids:
            # Ids are opaque here — the event carries observation ids, not observations —
            # so this only checks that the event declares metric wiring at all.
            if not observation_id:
                result.add(E_UNKNOWN_REFERENCE, f"event/{event.event_id}",
                           "empty metric observation id")

    # -- relationships -------------------------------------------------------------------

    def validate_relationship(self, instance: RelationshipInstance) -> ValidationResult:
        result = ValidationResult()
        definition = self._registry.relationship(instance.relationship_id)
        path = (
            f"relationship/{instance.relationship_id}:"
            f"{instance.source_id}->{instance.target_id}"
        )
        rules.check_relationship_instance(instance, definition, result)
        if definition is None:
            return result

        rules.check_confidence(instance.confidence, path, self._parameters, result)
        rules.check_evidence(
            instance.evidence, definition.evidence_required, path, self._parameters, result
        )
        self._check_role_context(instance, path, result)
        return result

    def _check_role_context(
        self, instance: RelationshipInstance, path: str, result: ValidationResult
    ) -> None:
        """A role-bearing relationship must name the context the role is played in.

        LENDS_UNDER without a facility asserts that a company is a lender in general, which
        is exactly the entity/role collapse this ontology exists to prevent.
        """
        for role in self._registry.by_category("role_type"):
            if instance.relationship_id not in role.allowed_relationships:
                continue
            if role.context_required and not instance.context_id and not instance.target_id:
                result.add(E_ROLE_CONTEXT_REQUIRED, path,
                           f"role {role.concept_id!r} requires a context entity")
            if role.allowed_player_types and not self._registry.accepts_type(
                role.allowed_player_types, instance.source_type
            ):
                result.add(E_ROLE_PLAYER_TYPE, path,
                           f"{instance.source_type!r} may not play {role.concept_id!r} "
                           f"(allowed: {list(role.allowed_player_types)})")

    # -- claims --------------------------------------------------------------------------

    def validate_claim(self, claim: OntologyClaim) -> ValidationResult:
        result = ValidationResult()
        path = f"claim/{claim.claim_id}"
        payloads = {
            ClaimKind.METRIC_OBSERVATION: claim.metric_observation,
            ClaimKind.EVENT: claim.event,
            ClaimKind.RELATIONSHIP: claim.relationship,
        }
        payload = payloads.get(claim.claim_kind)
        if payload is None:
            result.add(E_UNKNOWN_REFERENCE, path,
                       f"claim_kind is {claim.claim_kind} but that payload is absent")
            return result
        for kind, other in payloads.items():
            if kind != claim.claim_kind and other is not None:
                result.add(E_UNKNOWN_REFERENCE, path,
                           f"claim_kind is {claim.claim_kind} but a {kind} payload is "
                           f"also set")

        rules.check_confidence(claim.confidence, path, self._parameters, result)
        # Evidence is NOT re-checked here. Each payload validator already decides whether
        # evidence is required for what it carries - a calculated observation needs none,
        # because it is re-derivable from its inputs. A blanket check at claim level would
        # contradict that rule from a place that knows less than the rule does.

        validators = {
            ClaimKind.METRIC_OBSERVATION: self.validate_observation,
            ClaimKind.EVENT: self.validate_event,
            ClaimKind.RELATIONSHIP: self.validate_relationship,
        }
        return result.merge(validators[claim.claim_kind](payload))

    def validate_claims(self, claims: Sequence[OntologyClaim]) -> ValidationResult:
        result = ValidationResult()
        for claim in claims:
            result.merge(self.validate_claim(claim))
        return result


def _warn_on_unpreferred_lane(observation, metric, result: ValidationResult) -> None:
    """A non-preferred lane is worth noticing but is not an error.

    A GAAP figure read from a table instead of XBRL is still correct; it is just weaker
    evidence than the tagged fact would have been. Only `forbidden_source_lanes` is an error.
    """
    if not metric.source_lane_preferences:
        return
    if observation.source_lane in metric.source_lane_preferences:
        return
    if observation.source_lane in metric.forbidden_source_lanes:
        return  # already reported as an error
    result.add(
        "unpreferred_source_lane",
        f"observation/{observation.observation_id}",
        f"{metric.concept_id} is usually sourced from "
        f"{[str(l) for l in metric.source_lane_preferences]}, "
        f"got {observation.source_lane}",
        severity=Severity.WARNING,
    )


__all__ = ["ClaimValidator", "validate_definitions"]
