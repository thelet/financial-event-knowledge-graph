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


class EventPropertyContract(_Frozen):
    """Types for the properties an event type declares in `allowed_properties`.

    `allowed_properties` is a bare list of names, and `EventInstance.properties` is
    `dict[str, Any]` — so `low_value: "1.0 billion"` entered a guidance event silently while
    every observation lane enforced scale discipline *(V1 §5.2 defect 2)*. This declares what
    each named property must **be**, so the check that reads it names no concept and a second
    event type with a value range costs a YAML edit rather than a code edit.

    Optional by design: an event type that carries only free-form strings declares nothing here
    and is checked exactly as it was before *(added 2026-08-03, F0 Part D2)*.

    | Field | Meaning |
    | --- | --- |
    | `required_properties` | must be present and non-empty |
    | `numeric_properties` | must parse as a plain number — not `"1.0 billion"`, not `"1,000"` |
    | `metric_reference_properties` | must name a declared `metric_definition` |
    | `unit_property` | must hold a member of `KNOWN_UNITS` |
    | `currency_property` | must be present when the unit is monetary |
    | `range_low_property` / `range_high_property` | the two bounds of one value range |

    The range pair is named rather than inferred from `numeric_properties` because "these two
    numbers bound one value" is a stronger statement than "these two properties are numbers",
    and only the first justifies refusing a lone bound.
    """

    required_properties: tuple[str, ...] = ()
    numeric_properties: tuple[str, ...] = ()
    metric_reference_properties: tuple[str, ...] = ()
    unit_property: str | None = None
    currency_property: str | None = None
    range_low_property: str | None = None
    range_high_property: str | None = None


class EventTypeDefinition(ConceptDefinition):
    category: Literal[ConceptCategory.EVENT_TYPE] = ConceptCategory.EVENT_TYPE
    participants: tuple[EventParticipant, ...] = ()
    #: Every field here must be present. The default stays `occurred_on` because most event
    #: types are reported by evidence that dates the event itself.
    required_temporal_fields: tuple[str, ...] = ("occurred_on",)
    #: **At least one** of these must be present. For an event type whose evidence may date
    #: either the announcement or the occurrence — an executive change is announced before it
    #: takes effect — requiring `occurred_on` unconditionally forces a lane to invent a date
    #: or to emit nothing *(added 2026-08-02)*. Declaring the alternatives lets the lane
    #: record what the passage actually states. Empty means no alternative requirement.
    required_temporal_any_of: tuple[str, ...] = ()
    allowed_properties: tuple[str, ...] = ()
    #: Types for the names in `allowed_properties`. See `EventPropertyContract`.
    property_contract: EventPropertyContract | None = None
    evidence_required: bool = True
    allowed_metric_relationships: tuple[str, ...] = ()
    allowed_relationships: tuple[str, ...] = ()
    #: Assertion types this event type refuses. `inference_restrictions` below is prose that no
    #: code has ever read; where a restriction is mechanical it belongs here instead, so the
    #: rule can be tested rather than only stated *(added 2026-08-03, F0 Part D1)*.
    forbidden_assertion_types: tuple[AssertionType, ...] = ()
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
    """One evidence kind and the shape a reference of that kind must have.

    `required_fields` was declared from the start and read by nothing until F0 Part B; it is
    now the single source of what each kind must carry, so a vocabulary change cannot silently
    disagree with the validator — the same argument `deferred_metric_ids` already wins.

    `optional_fields` is the other half of a *discriminated* contract and is new
    *(2026-08-03)*. Without it "required" alone cannot say that an `xbrl_fact` may not carry a
    `passage_id`: every field would be permitted everywhere and the only enforceable rule would
    be presence. Permitted is `required_fields | optional_fields`; anything else populated on a
    reference of this kind is a mixture of two kinds and is refused.
    """

    category: Literal[ConceptCategory.EVIDENCE_TYPE] = ConceptCategory.EVIDENCE_TYPE
    evidence_kind: EvidenceKind
    required_fields: tuple[str, ...] = ()
    optional_fields: tuple[str, ...] = ()

    @property
    def permitted_fields(self) -> frozenset[str]:
        return frozenset(self.required_fields) | frozenset(self.optional_fields)


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
    """A pointer at one citable source, of exactly one `EvidenceKind`.

    Deliberately a small boundary model of ids and primitives. The ontology never imports a
    normalization class, so either side can change without breaking the other.

    **The field list is a union across kinds and the model does not police which apply.** That
    is deliberate: which fields a kind requires and permits is declared in `claims.yaml`, so a
    new kind is a vocabulary edit rather than a model edit, and the check that reads it lives
    in `extraction.core.validation`. What this class guarantees is only that a field means the
    same thing whichever kind carries it.

    Field groups, by the kind that owns them:

    | Group | Fields |
    | --- | --- |
    | filed passage | `passage_id`, `document_id`, `table_id`, `block_ids`, `char_start`, `char_end` |
    | any source | `quoted_text`, `source_url` |
    | XBRL / filing | `xbrl_concept`, `accession` |
    | external page | `disclosure_channel_id`, `fetched_at` |
    | market data | `provider`, `source_identity`, `row_identity`, `instrument_id`, `session_date`, `fetched_at` |
    | calculated | `input_observation_ids`, `calculation_expression`, `calculation_version` |

    The last two groups are new *(2026-08-03, F0 Part B)*. `calculation_version` is separate
    from `MetricObservation.formula_version_id` on purpose: the observation records which
    formula it was derived under, and the evidence records which version of the *calculator*
    produced this citation. They are usually equal and are not the same statement.
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
    #: When a non-filed source was read. A filing is immutable and dated by EDGAR; a page and
    #: a price series are not, so a citation to one that does not say when it was fetched
    #: cannot be checked against anything later.
    fetched_at: str | None = None
    provider: str | None = None
    #: The provider-stable name of the series or dataset — not the URL, which may be a query
    #: that stops resolving. `source_url` stays available beside it for a human to follow.
    source_identity: str | None = None
    #: Which row of that series. A session date alone does not identify a row when a provider
    #: revises one.
    row_identity: str | None = None
    instrument_id: str | None = None
    session_date: str | None = None
    input_observation_ids: tuple[str, ...] = ()
    calculation_expression: str | None = None
    calculation_version: str | None = None

    @property
    def has_anchor(self) -> bool:
        """True when the reference identifies a citable source of any supported kind.

        Widened 2026-08-03 for the non-filed kinds. It stays a coarse "points at something"
        test — `ontology.core.constraints.check_evidence` uses it to ask whether a claim is
        evidenced at all — while the per-kind field contract is checked in
        `extraction.core.validation`, which can read the vocabulary. Every reference that
        anchored before still anchors: the change only adds disjuncts.
        """
        return bool(
            self.passage_id or self.table_id or self.xbrl_concept
            or self.document_id or self.accession or self.source_url
            or self.source_identity or self.input_observation_ids
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
    #: When the underlying event happened or took effect. Populated only from evidence that
    #: says the change *occurred*, *became effective*, or *was effective on* that date.
    occurred_on: str | None = None
    #: When the event was publicly announced or disclosed — a press-release dateline, an
    #: "announced today". **Not interchangeable with `occurred_on`, and never inferred from
    #: it or into it** *(added 2026-08-02)*. A filing announcing an appointment dates the
    #: announcement; it does not date the appointment, and the two are routinely days or
    #: months apart. Neither field may be populated from a document's `filing_date` or
    #: `report_date` unless the passage itself gives that date that meaning.
    announced_on: str | None = None
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
