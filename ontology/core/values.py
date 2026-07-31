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
    """Where a value came from. Recorded on every observation."""

    XBRL = "xbrl"
    NORMALIZED_TABLE = "normalized_table"
    NORMALIZED_NARRATIVE = "normalized_narrative"
    SEC_FILING_METADATA = "sec_filing_metadata"
    COMPANY_DASHBOARD = "company_dashboard"
    CALCULATED = "calculated"
    MANUAL_ANNOTATION = "manual_annotation"


class AssertionType(StrEnum):
    """How a claim came to be believed. Never inferred from the value itself."""

    REPORTED = "reported"
    CALCULATED = "calculated"
    CLASSIFIED = "classified"
    INFERRED = "inferred"


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
    NORMALIZED_PASSAGE = "normalized_passage"
    NORMALIZED_TABLE = "normalized_table"
    XBRL_FACT = "xbrl_fact"
    FILING_METADATA = "filing_metadata"
    EXTERNAL_PAGE = "external_page"


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
