"""Structured ontology errors.

Every problem is reported as a `ValidationIssue` with a machine-readable code and a path,
so a caller can act on it rather than parse prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .values import Severity


class OntologyError(RuntimeError):
    """Raised when an ontology cannot be loaded or resolved at all."""


class OntologyLoadError(OntologyError):
    def __init__(self, issues: "list[ValidationIssue]") -> None:
        self.issues = issues
        errors = [i for i in issues if i.severity == Severity.ERROR]
        head = "; ".join(f"{i.code} at {i.path}: {i.message}" for i in errors[:5])
        super().__init__(f"{len(errors)} ontology definition error(s): {head}")


class ConceptNotFoundError(OntologyError):
    pass


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    severity: Severity = Severity.ERROR
    concept_id: str | None = None

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code} at {self.path}: {self.message}"


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, code: str, path: str, message: str,
            severity: Severity = Severity.ERROR, concept_id: str | None = None) -> None:
        self.issues.append(ValidationIssue(code, path, message, severity, concept_id))

    def merge(self, other: "ValidationResult") -> "ValidationResult":
        self.issues.extend(other.issues)
        return self

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    def render(self) -> str:
        if not self.issues:
            return "OK"
        return "\n".join(str(i) for i in self.issues)


# Definition-time codes
E_DUPLICATE_ID = "duplicate_concept_id"
E_UNKNOWN_REFERENCE = "unknown_reference"
E_INVALID_INHERITANCE = "invalid_inheritance"
E_INHERITANCE_CYCLE = "inheritance_cycle"
E_INVALID_ENDPOINT = "invalid_relationship_endpoint"
E_UNKNOWN_UNIT = "unknown_unit"
E_ALIAS_COLLISION = "alias_collision_between_distinct_concepts"
E_INVALID_MAPPING = "invalid_external_mapping"
E_KPI_AS_MACRO = "company_kpi_mapped_as_macro_indicator"
E_ROLE_AS_ENTITY = "role_declared_as_entity_type"
E_FORMULA_OVERLAP = "formula_version_date_overlap"
E_FORMULA_UNKNOWN_COMPONENT = "formula_component_not_a_metric"
E_INVENTED_URI = "external_uri_present_for_no_mapping"

# Claim-time codes
E_MISSING_EVIDENCE = "missing_evidence"
E_MISSING_INSTANT = "instant_metric_requires_instant_date"
E_MISSING_PERIOD = "duration_metric_requires_period_start_and_end"
E_PERIOD_ORDER = "period_end_before_period_start"
E_UNIT_MISMATCH = "observation_unit_not_allowed_for_metric"
E_SUBJECT_TYPE = "subject_type_not_allowed_for_metric"
E_PERCENTAGE_RANGE = "percentage_outside_documented_range"
E_MISSING_CURRENCY = "monetary_value_requires_currency"
E_CALC_WITHOUT_INPUTS = "calculated_observation_requires_inputs_and_formula"
E_CALC_FIELDS_ON_REPORTED = "reported_observation_must_not_carry_calculation_fields"
E_NO_FORMULA_FOR_DATE = "no_formula_version_covers_observation_date"
E_SOURCE_LANE_FORBIDDEN = "source_lane_not_allowed_for_metric"
E_DISCOVERY_ONLY_AS_CANONICAL = "discovery_only_channel_cited_as_canonical_evidence"
E_UNKNOWN_PREDICATE = "unknown_relationship_predicate"
E_CONFIDENCE_RANGE = "confidence_outside_0_1"
E_EVENT_PARTICIPANT = "event_participant_invalid"
E_EVENT_TEMPORAL = "event_missing_required_temporal_field"
E_EVENT_PROPERTY_TYPE = "event_property_not_of_declared_type"
E_EVENT_PROPERTY_REFERENCE = "event_property_does_not_name_a_declared_concept"
E_EVENT_VALUE_RANGE = "event_value_range_incomplete_or_inverted"
E_EVENT_ASSERTION_TYPE = "assertion_type_not_allowed_for_event_type"
E_FUTURE_PERIOD = "period_ends_after_the_filing_that_carries_it"
E_ROLE_CONTEXT_REQUIRED = "role_requires_context_entity"
E_ROLE_PLAYER_TYPE = "role_player_type_not_allowed"
E_EVIDENCE_OFFSETS = "invalid_evidence_offsets"
