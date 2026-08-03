"""Controlled vocabularies for the ontology.

Enums live in Python because they are the *shape* of the ontology, not its content. The
concepts themselves are declared in YAML; nothing here names a metric, entity or event.
"""

from __future__ import annotations

from enum import StrEnum


class ConceptCategory(StrEnum):
    """Every concept belongs to exactly one category with its own typed model."""

    ENTITY_TYPE = "entity_type"
    ROLE_TYPE = "role_type"
    FINANCIAL_INSTRUMENT_TYPE = "financial_instrument_type"
    AGREEMENT_TYPE = "agreement_type"
    METRIC_DEFINITION = "metric_definition"
    METRIC_FORMULA = "metric_formula"
    EVENT_TYPE = "event_type"
    RELATIONSHIP_TYPE = "relationship_type"
    EVIDENCE_TYPE = "evidence_type"
    CLAIM_TYPE = "claim_type"
    STATUS_TYPE = "status_type"


class MetricCategory(StrEnum):
    """Keeps company measures and regional economic measures apart.

    The research is explicit: FIBO scopes EconomicIndicator to a statistical region, so a
    company operating KPI must never carry that mapping. `MACROECONOMIC` is the only
    category permitted to.
    """

    OPERATING = "operating"
    GAAP_FINANCIAL = "gaap_financial"
    NON_GAAP_FINANCIAL = "non_gaap_financial"
    CAPITAL_STRUCTURE = "capital_structure"
    MACROECONOMIC = "macroeconomic"


class ValueType(StrEnum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    MONETARY = "monetary"
    PERCENTAGE = "percentage"
    DURATION = "duration"
    BOOLEAN = "boolean"
    TEXT = "text"


class PeriodType(StrEnum):
    INSTANT = "instant"
    DURATION = "duration"
    UNKNOWN = "point_in_time_or_duration_unknown"


class GaapStatus(StrEnum):
    GAAP = "gaap"
    NON_GAAP = "non_gaap"
    OPERATING_KPI = "operating_kpi"
    MACRO_INDICATOR = "macro_indicator"
    NOT_APPLICABLE = "not_applicable"


class SourceLane(StrEnum):
    """Where a value came from. Recorded on every observation.

    `TRANSCRIPT` and `MARKET_DATA` added 2026-08-03 (F0 Part D4) for the two lanes the
    factual-spine plan schedules next. **Nothing emits either yet** — they exist so that the
    lane that lands first is a lane implementation rather than a contract change.

    `TRANSCRIPT`, not `earnings_call_transcript`: the plan §6.1 measured that Opendoor has
    replaced its earnings call with a streamed *Financial Open House* with shareholder Q&A, so
    the artifact is not uniformly an earnings call and a name asserting it would be wrong for
    the recent half of the corpus.
    """

    XBRL = "xbrl"
    NORMALIZED_TABLE = "normalized_table"
    NORMALIZED_NARRATIVE = "normalized_narrative"
    SEC_FILING_METADATA = "sec_filing_metadata"
    COMPANY_DASHBOARD = "company_dashboard"
    CALCULATED = "calculated"
    MANUAL_ANNOTATION = "manual_annotation"
    TRANSCRIPT = "transcript"
    MARKET_DATA = "market_data"


class AssertionType(StrEnum):
    """How a claim came to be believed. Never inferred from the value itself.

    `GUIDED` added 2026-08-03 (F0 Part D1). `guidance_issuance.inference_restrictions` demands
    *"assertion_type other than `reported`"* and the four original members could not satisfy it
    honestly: `CALCULATED` triggers the required calculation fields
    (`constraints.check_calculation_fields`) that a guided figure has no way to supply,
    `CLASSIFIED` says a category was assigned, and `INFERRED` says the graph worked the value
    out. A guided figure is none of those — the company *stated a target it has not yet met*.
    The rule was therefore unsatisfiable, and so also untestable, until this member existed.

    `GUIDED` is the one assertion type for which a period ending after the filing date is
    normal rather than a defect; `constraints.check_future_period` reads it that way.
    """

    REPORTED = "reported"
    CALCULATED = "calculated"
    CLASSIFIED = "classified"
    INFERRED = "inferred"
    GUIDED = "guided"


class MappingSystem(StrEnum):
    FIBO = "fibo"
    US_GAAP = "standard_us_gaap"
    OPENDOOR_XBRL_EXTENSION = "opendoor_extension"
    DEI = "dei_metadata"
    WIKIDATA = "wikidata"


class MappingType(StrEnum):
    """Never a boolean `mapped`. A partial fit and an exact fit are different facts."""

    EXACT = "exact"
    NARROWER = "narrower"
    BROADER = "broader"
    RELATED = "related"
    PATTERN_ONLY = "pattern_only"
    NO_MAPPING = "no_mapping"


class InferenceLevel(StrEnum):
    """How much interpretation a relationship instance carries."""

    ASSERTED = "asserted"
    DERIVED = "derived"
    INFERRED = "inferred"


class Canonicality(StrEnum):
    """Whether a disclosure channel can be cited as reproducible evidence."""

    CANONICAL = "canonical"
    SUPPLEMENTARY = "supplementary"
    DISCOVERY_ONLY = "discovery_only"


class EvidenceKind(StrEnum):
    """What an evidence reference points at. **One kind per reference, never a mixture.**

    Each member names a *discriminated* variant: the fields a reference of that kind must
    carry, and the fields it may carry, are declared per kind in `claims.yaml`
    (`EvidenceTypeDefinition.required_fields` / `optional_fields`) and enforced by
    `extraction.core.validation.validate_evidence_resolves`. A `passage_id` on a kind that
    does not name a passage is refused rather than ignored — F0 §2.2 records that fabricating
    one is the specific failure this contract exists to prevent.

    `MARKET_DATA` and `CALCULATED` added 2026-08-03 (F0 Part B). They are the two kinds the
    factual spine needs that no filed document supplies: a price series row, and a value
    re-derived from other observations rather than read anywhere.
    """

    NORMALIZED_PASSAGE = "normalized_passage"
    NORMALIZED_TABLE = "normalized_table"
    XBRL_FACT = "xbrl_fact"
    FILING_METADATA = "filing_metadata"
    EXTERNAL_PAGE = "external_page"
    MARKET_DATA = "market_data"
    CALCULATED = "calculated"


class PopulationRole(StrEnum):
    """The FIBO IND baseline/comparison pattern, adopted locally.

    Keeps Opendoor's own figure and the buybox-adjusted market figure as two observations
    of one definition instead of one observation with a footnote.
    """

    BASELINE = "baseline"
    COMPARISON = "comparison"


class ComparisonOperator(StrEnum):
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    EQ = "="


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ClaimKind(StrEnum):
    METRIC_OBSERVATION = "metric_observation"
    EVENT = "event"
    RELATIONSHIP = "relationship"


#: Units the ontology understands. Declared here so a metric cannot invent one silently.
KNOWN_UNITS = frozenset(
    {"homes", "USD", "USD_millions", "USD_thousands", "percent", "days", "markets",
     "contracts", "basis_points", "ratio", "count"}
)

#: Units that require a currency on every observation.
MONETARY_UNITS = frozenset({"USD", "USD_millions", "USD_thousands"})
