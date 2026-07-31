"""Typed ontology models.

Two families, deliberately separate:

- **Definition models** describe the ontology itself and are loaded from YAML.
- **Runtime models** describe what a future extractor emits and are validated against the
  definitions.

Nothing here names a concept. Concepts live in `versions/<id>/*.yaml`; this module only
gives them a shape. A category-specific model per concept category, rather than one blob
accepting arbitrary properties, is what lets validation say something useful.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .identifiers import validate_concept_id, validate_relationship_id
from .values import (
    AssertionType,
    ClaimKind,
    ComparisonOperator,
    ConceptCategory,
    EvidenceKind,
    GaapStatus,
    InferenceLevel,
    MappingSystem,
    MappingType,
    MetricCategory,
    PeriodType,
    PopulationRole,
    SourceLane,
    ValueType,
)

SCHEMA_VERSION = 1


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------------------
# Shared definition pieces
# --------------------------------------------------------------------------------------


class ExternalMapping(_Frozen):
    """A mapping to an outside vocabulary.

    `mapping_type` is an enum rather than a boolean `mapped`: an exact fit, a partial fit
    and a deliberate absence are three different facts, and the research found all three.
    """

    mapping_system: MappingSystem
    external_uri: str | None = None
    external_label: str | None = None
    mapping_type: MappingType
    verified: bool = False
    source_module: str | None = None
    semantic_note: str | None = None

    @field_validator("external_uri")
    @classmethod
    def _uri_shape(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("http://", "https://", "urn:")):
            raise ValueError(f"external_uri must be an absolute URI: {value!r}")
        return value


class Ambiguity(_Frozen):
    """An unresolved question recorded on the concept rather than silently decided."""

    code: str
    description: str
    impact: Literal["low", "medium", "high"] = "medium"
    evidence: tuple[str, ...] = ()


class SourceEvidenceRef(_Frozen):
    """Provenance for a *definition* — which filed passage established it."""

    passage_id: str | None = None
    document_id: str | None = None
    accession: str | None = None
    form: str | None = None
    filing_date: str | None = None
    quote: str | None = None
    source_url: str | None = None


class ConceptDefinition(_Frozen):
    """Common shape. Every category subclasses this."""

    concept_id: str
    category: ConceptCategory
    label: str
    description: str = ""
    aliases: tuple[str, ...] = ()
    external_mappings: tuple[ExternalMapping, ...] = ()
    ambiguities: tuple[Ambiguity, ...] = ()
    source_evidence: tuple[SourceEvidenceRef, ...] = ()
    notes: str | None = None

    @field_validator("concept_id")
    @classmethod
    def _id(cls, value: str) -> str:
        return validate_concept_id(value)


# --------------------------------------------------------------------------------------
# Category-specific definitions
# --------------------------------------------------------------------------------------


class EntityTypeDefinition(ConceptDefinition):
    category: Literal[ConceptCategory.ENTITY_TYPE] = ConceptCategory.ENTITY_TYPE
    is_a: str | None = None
    abstract: bool = False
    identifying_properties: tuple[str, ...] = ()
    optional_properties: tuple[str, ...] = ()


class RoleTypeDefinition(ConceptDefinition):
    """A contextual role, never a permanent entity subtype.

    Goldman Sachs is a `company` that *plays* `lender` in the context of a credit facility.
    Modelling `Lender` as a company subtype would make the role permanent and global, which
    the corpus contradicts: Zillow appears as competitor and later as partner.
    """

    category: Literal[ConceptCategory.ROLE_TYPE] = ConceptCategory.ROLE_TYPE
    allowed_player_types: tuple[str, ...]
    context_types: tuple[str, ...] = ()
    context_required: bool = True
    time_bound: bool = True
    allowed_relationships: tuple[str, ...] = ()


class InstrumentTypeDefinition(ConceptDefinition):
    category: Literal[ConceptCategory.FINANCIAL_INSTRUMENT_TYPE] = (
        ConceptCategory.FINANCIAL_INSTRUMENT_TYPE
    )
    is_a: str | None = None
    issuer_types: tuple[str, ...] = ()
    optional_properties: tuple[str, ...] = ()


class AgreementTypeDefinition(ConceptDefinition):
    category: Literal[ConceptCategory.AGREEMENT_TYPE] = ConceptCategory.AGREEMENT_TYPE
    is_a: str | None = None
    party_roles: tuple[str, ...] = ()
    optional_properties: tuple[str, ...] = ()


class PopulationDefinition(_Frozen):
    """The denominator of a ratio metric.

    `raw` keeps the filed wording verbatim. The research found three different wordings for
    the 120-day metric across document types, so a single normalized string would assert a
    resolution the corpus does not support.
    """

    normalized: str | None = None
    raw_variants: tuple[str, ...] = ()
    confidence: Literal["low", "medium", "high"] = "medium"
    comparison_population: str | None = None


class MetricDefinition(ConceptDefinition):
    category: Literal[ConceptCategory.METRIC_DEFINITION] = ConceptCategory.METRIC_DEFINITION
    metric_category: MetricCategory
    value_type: ValueType
    unit: str
    allowed_units: tuple[str, ...] = ()
    period_type: PeriodType
    gaap_status: GaapStatus
    subject_types: tuple[str, ...] = ("company",)
    source_lane_preferences: tuple[SourceLane, ...] = ()
    forbidden_source_lanes: tuple[SourceLane, ...] = ()
    population: PopulationDefinition | None = None
    comparison_operator: ComparisonOperator | None = None
    threshold_value: float | None = None
    threshold_unit: str | None = None
    measurement_start: str | None = None
    numerator_description: str | None = None
    denominator_description: str | None = None
    reconciles_to: str | None = None
    distinct_from: tuple[str, ...] = ()
    valid_from: str | None = None
    valid_to: str | None = None
    percentage_min: float | None = None
    percentage_max: float | None = None


class AdjustmentComponent(_Frozen):
    """One line of a non-GAAP reconciliation, including ones deliberately excluded."""

    label: str
    metric_id: str | None = None
    operation: Literal["add", "subtract"]
    included: bool = True
    note: str | None = None


class MetricFormulaVersion(ConceptDefinition):
    """A dated formula.

    Separate versions exist so definition drift stays visible. Adjusted Gross Profit used
    'inventory impairment' plus a restructuring adjustment early on and 'inventory
    valuation adjustment' with no restructuring line later; one timeless formula would hide
    that.
    """

    category: Literal[ConceptCategory.METRIC_FORMULA] = ConceptCategory.METRIC_FORMULA
    metric_id: str
    version: str
    valid_from: str
    valid_to: str | None = None
    expression: str
    component_metrics: tuple[str, ...] = ()
    adjustment_components: tuple[AdjustmentComponent, ...] = ()
    terminology: dict[str, str] = Field(default_factory=dict)


class EventParticipant(_Frozen):
    role: str
    entity_types: tuple[str, ...]
    required: bool = True
    cardinality: Literal["one", "many"] = "one"


class EventTypeDefinition(ConceptDefinition):
    category: Literal[ConceptCategory.EVENT_TYPE] = ConceptCategory.EVENT_TYPE
    participants: tuple[EventParticipant, ...] = ()
    required_temporal_fields: tuple[str, ...] = ("occurred_on",)
    allowed_properties: tuple[str, ...] = ()
    evidence_required: bool = True
    allowed_metric_relationships: tuple[str, ...] = ()
    allowed_relationships: tuple[str, ...] = ()
    inference_restrictions: str | None = None
    sec_item_codes: tuple[str, ...] = ()


class RelationshipDefinition(ConceptDefinition):
    category: Literal[ConceptCategory.RELATIONSHIP_TYPE] = ConceptCategory.RELATIONSHIP_TYPE
    relationship_id: str
    allowed_source_types: tuple[str, ...]
    allowed_target_types: tuple[str, ...]
    temporal: bool = False
    symmetric: bool = False
    transitive: bool = False
    inverse_relationship: str | None = None
    evidence_required: bool = True
    inference_level: InferenceLevel = InferenceLevel.ASSERTED

    @field_validator("relationship_id")
    @classmethod
    def _rid(cls, value: str) -> str:
        return validate_relationship_id(value)


class EvidenceTypeDefinition(ConceptDefinition):
    category: Literal[ConceptCategory.EVIDENCE_TYPE] = ConceptCategory.EVIDENCE_TYPE
    evidence_kind: EvidenceKind
    required_fields: tuple[str, ...] = ()


class ClaimTypeDefinition(ConceptDefinition):
    category: Literal[ConceptCategory.CLAIM_TYPE] = ConceptCategory.CLAIM_TYPE
    claim_kind: ClaimKind
    required_payload: tuple[str, ...] = ()


class StatusTypeDefinition(ConceptDefinition):
    category: Literal[ConceptCategory.STATUS_TYPE] = ConceptCategory.STATUS_TYPE
    states: tuple[str, ...]
    applies_to: tuple[str, ...] = ()


class ConceptInstance(_Frozen):
    """A named individual, e.g. a specific market or disclosure channel."""

    instance_id: str
    concept_id: str
    label: str
    properties: dict[str, Any] = Field(default_factory=dict)
    external_mappings: tuple[ExternalMapping, ...] = ()
    source_evidence: tuple[SourceEvidenceRef, ...] = ()


class AliasEntry(_Frozen):
    """One surface form and the concept(s) it may denote.

    An alias is a lookup hint, not an assertion of equivalence — which is why an alias may
    legitimately resolve to several concepts and the registry returns all of them.
    """

    alias: str
    concept_ids: tuple[str, ...]
    exact_source_label: str | None = None
    case_sensitive: bool = False
    ambiguous: bool = False
    note: str | None = None


class OntologyMetadata(_Frozen):
    """Deterministic identity. No timestamps, run ids or code commits."""

    ontology_id: str
    semantic_version: str
    schema_version: int = SCHEMA_VERSION
    label: str = ""
    description: str = ""
    created_from_research_version: str | None = None


# --------------------------------------------------------------------------------------
# Runtime models — what a future extractor emits
# --------------------------------------------------------------------------------------


class EvidenceReference(BaseModel):
    """A pointer into the normalized corpus.

    Deliberately a small boundary model of ids and primitives. The ontology never imports a
    normalization class, so either side can change without breaking the other.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_kind: EvidenceKind = EvidenceKind.NORMALIZED_PASSAGE
    passage_id: str | None = None
    document_id: str | None = None
    table_id: str | None = None
    block_ids: tuple[str, ...] = ()
    char_start: int | None = None
    char_end: int | None = None
    quoted_text: str | None = None
    source_url: str | None = None
    xbrl_concept: str | None = None
    accession: str | None = None
    disclosure_channel_id: str | None = None

    @property
    def has_anchor(self) -> bool:
        """True when the reference identifies a citable source of any supported kind."""
        return bool(
            self.passage_id or self.table_id or self.xbrl_concept
            or self.document_id or self.accession
        )


class Population(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: PopulationRole = PopulationRole.BASELINE
    definition_raw: str | None = None
    definition_normalized: str | None = None
    confidence: Literal["low", "medium", "high"] = "medium"


class MetricObservation(BaseModel):
    """One measured value of one metric definition."""

    model_config = ConfigDict(extra="forbid")

    observation_id: str
    metric_id: str
    subject_entity_id: str
    subject_type: str = "company"
    value: float | int | str | bool
    unit: str
    currency: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    instant_date: str | None = None
    reported_at: str | None = None
    population: Population | None = None
    dimensions: dict[str, str] = Field(default_factory=dict)
    reporting_basis: str | None = None
    source_lane: SourceLane
    evidence: tuple[EvidenceReference, ...] = ()
    assertion_type: AssertionType = AssertionType.REPORTED
    confidence: float | None = None
    formula_version_id: str | None = None
    input_observation_ids: tuple[str, ...] = ()
    calculation_expression: str | None = None


class EventParticipantRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    entity_id: str
    entity_type: str


class EventInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_type_id: str
    occurred_on: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    participants: tuple[EventParticipantRef, ...] = ()
    properties: dict[str, Any] = Field(default_factory=dict)
    reported_metric_observation_ids: tuple[str, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()
    assertion_type: AssertionType = AssertionType.REPORTED
    confidence: float | None = None


class RelationshipInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relationship_id: str
    source_id: str
    source_type: str
    target_id: str
    target_type: str
    context_id: str | None = None
    context_type: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    evidence: tuple[EvidenceReference, ...] = ()
    assertion_type: AssertionType = AssertionType.REPORTED
    confidence: float | None = None


class OntologyClaim(BaseModel):
    """The provider-independent unit an extractor emits.

    Carries no provider response object, no prompt, no model name — only
    `extractor_metadata`, a free dict the ontology never interprets.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_kind: ClaimKind
    metric_observation: MetricObservation | None = None
    event: EventInstance | None = None
    relationship: RelationshipInstance | None = None
    assertion_type: AssertionType = AssertionType.REPORTED
    confidence: float | None = None
    evidence: tuple[EvidenceReference, ...] = ()
    extractor_metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def payload_evidence(self) -> tuple[EvidenceReference, ...]:
        """Evidence from the claim or from whichever payload it carries."""
        for payload in (self.metric_observation, self.event, self.relationship):
            if payload is not None and payload.evidence:
                return tuple(payload.evidence)
        return tuple(self.evidence)
