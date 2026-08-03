"""Machine-checkable constraints.

Two families, matching when they can be checked:

- **Definition-time** constraints run once at load and can fail the load. They protect the
  ontology from itself — a duplicate id, a role smuggled in as an entity type, a formula
  with an unknown input.
- **Claim-time** constraints run per claim and never fail the load. They protect the graph
  from a bad extraction.

The checks live here; their parameters live in `constraints.yaml`. A policy change should be
a data edit, not a code edit.

Nothing in this module names a concept. Every concept id it uses arrives through
`ConstraintParameters`, loaded from YAML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from .errors import (
    E_ALIAS_COLLISION,
    E_CALC_FIELDS_ON_REPORTED,
    E_CALC_WITHOUT_INPUTS,
    E_CONFIDENCE_RANGE,
    E_DISCOVERY_ONLY_AS_CANONICAL,
    E_DUPLICATE_ID,
    E_EVENT_ASSERTION_TYPE,
    E_EVENT_PARTICIPANT,
    E_EVENT_PROPERTY_REFERENCE,
    E_EVENT_PROPERTY_TYPE,
    E_EVENT_TEMPORAL,
    E_EVENT_VALUE_RANGE,
    E_EVIDENCE_OFFSETS,
    E_FORMULA_OVERLAP,
    E_FORMULA_UNKNOWN_COMPONENT,
    E_FUTURE_PERIOD,
    E_INHERITANCE_CYCLE,
    E_INVALID_ENDPOINT,
    E_INVALID_INHERITANCE,
    E_INVALID_MAPPING,
    E_INVENTED_URI,
    E_KPI_AS_MACRO,
    E_MISSING_CURRENCY,
    E_MISSING_EVIDENCE,
    E_MISSING_INSTANT,
    E_MISSING_PERIOD,
    E_NO_FORMULA_FOR_DATE,
    E_PERCENTAGE_RANGE,
    E_PERIOD_ORDER,
    E_ROLE_AS_ENTITY,
    E_SOURCE_LANE_FORBIDDEN,
    E_SUBJECT_TYPE,
    E_UNIT_MISMATCH,
    E_UNKNOWN_PREDICATE,
    E_UNKNOWN_REFERENCE,
    E_UNKNOWN_UNIT,
    ValidationResult,
)
from .identifiers import normalize_alias
from .models import (
    AliasEntry,
    ConceptDefinition,
    EventInstance,
    EventPropertyContract,
    EventTypeDefinition,
    ExternalMapping,
    MetricDefinition,
    MetricFormulaVersion,
    MetricObservation,
    RelationshipDefinition,
    RelationshipInstance,
)
from .values import (
    KNOWN_UNITS,
    MONETARY_UNITS,
    AssertionType,
    MappingType,
    MetricCategory,
    PeriodType,
    Severity,
)


@dataclass(frozen=True)
class DistinctGroup:
    group_id: str
    concept_ids: tuple[str, ...]
    rationale: str = ""


@dataclass(frozen=True)
class ConstraintParameters:
    """Everything the checks below need that is policy rather than logic."""

    mutually_distinct_groups: tuple[DistinctGroup, ...] = ()
    economic_indicator_labels: frozenset[str] = frozenset()
    economic_indicator_uris: frozenset[str] = frozenset()
    economic_indicator_allowed_categories: frozenset[str] = frozenset()
    role_concepts_forbidden_as_entity_types: frozenset[str] = frozenset()
    discovery_only_channels: frozenset[str] = frozenset()
    percentage_default_min: float = -1000.0
    percentage_default_max: float = 1000.0
    confidence_min: float = 0.0
    confidence_max: float = 1.0
    #: Metrics whose *calculated* observations may legitimately cover a period ending after the
    #: filing that carries them — a projection re-derived from filed inputs. Empty by default:
    #: a future-period calculated fact is refused unless the ontology names the metric, because
    #: the failure it guards against (a guided figure mislabelled) is silent otherwise.
    #: `guided` needs no entry here; see `check_future_period`.
    future_period_allowed_calculated_metrics: frozenset[str] = frozenset()

    @classmethod
    def from_yaml(cls, raw: Mapping[str, Any]) -> "ConstraintParameters":
        indicator = raw.get("economic_indicator") or {}
        canonicality = raw.get("evidence_canonicality") or {}
        pct = raw.get("percentage_defaults") or {}
        conf = raw.get("confidence_range") or {}
        future = raw.get("future_period") or {}
        return cls(
            mutually_distinct_groups=tuple(
                DistinctGroup(
                    group_id=g["group_id"],
                    concept_ids=tuple(g.get("concept_ids", ())),
                    rationale=g.get("rationale", ""),
                )
                for g in raw.get("mutually_distinct_groups", ())
            ),
            economic_indicator_labels=frozenset(indicator.get("external_labels", ())),
            economic_indicator_uris=frozenset(indicator.get("external_uris", ())),
            economic_indicator_allowed_categories=frozenset(
                indicator.get("allowed_metric_categories", ())
            ),
            role_concepts_forbidden_as_entity_types=frozenset(
                raw.get("role_concepts_forbidden_as_entity_types", ())
            ),
            discovery_only_channels=frozenset(canonicality.get("discovery_only_channels", ())),
            percentage_default_min=float(pct.get("min", -1000.0)),
            percentage_default_max=float(pct.get("max", 1000.0)),
            confidence_min=float(conf.get("min", 0.0)),
            confidence_max=float(conf.get("max", 1.0)),
            future_period_allowed_calculated_metrics=frozenset(
                future.get("allowed_calculated_metrics", ())
            ),
        )


# --------------------------------------------------------------------------------------
# Definition-time
# --------------------------------------------------------------------------------------


def check_unique_ids(concepts: Sequence[ConceptDefinition], result: ValidationResult) -> None:
    seen: dict[str, str] = {}
    for concept in concepts:
        previous = seen.get(concept.concept_id)
        if previous is not None:
            result.add(
                E_DUPLICATE_ID,
                f"{concept.category}/{concept.concept_id}",
                f"concept id already declared as {previous}",
                concept_id=concept.concept_id,
            )
        seen[concept.concept_id] = str(concept.category)


def check_references(
    concepts: Sequence[ConceptDefinition],
    known_ids: frozenset[str],
    result: ValidationResult,
) -> None:
    """Every id a definition points at must exist.

    Reference fields are enumerated per model rather than discovered reflectively: a
    reflective walk would also follow `distinct_from`-style fields whose targets live in a
    different namespace, and would quietly stop checking when a model gains a field.
    """
    for concept in concepts:
        for attribute in _REFERENCE_FIELDS.get(type(concept).__name__, ()):
            value = getattr(concept, attribute, None)
            for target in _as_ids(value):
                if target not in known_ids:
                    result.add(
                        E_UNKNOWN_REFERENCE,
                        f"{concept.concept_id}.{attribute}",
                        f"references unknown concept {target!r}",
                        concept_id=concept.concept_id,
                    )


#: Which fields on which model hold concept ids that must resolve.
_REFERENCE_FIELDS: dict[str, tuple[str, ...]] = {
    "EntityTypeDefinition": ("is_a",),
    "InstrumentTypeDefinition": ("is_a", "issuer_types"),
    "AgreementTypeDefinition": ("is_a", "party_roles"),
    "RoleTypeDefinition": ("allowed_player_types", "context_types"),
    "MetricDefinition": ("subject_types", "distinct_from", "reconciles_to"),
    "MetricFormulaVersion": ("metric_id", "component_metrics"),
    "EventTypeDefinition": ("allowed_metric_relationships",),
    "StatusTypeDefinition": ("applies_to",),
}


def check_inheritance(
    concepts: Sequence[ConceptDefinition], result: ValidationResult
) -> None:
    """`is_a` must stay inside its own category and must not cycle."""
    category_of = {c.concept_id: str(c.category) for c in concepts}
    parents = {
        c.concept_id: getattr(c, "is_a", None)
        for c in concepts
        if getattr(c, "is_a", None)
    }

    for child, parent in parents.items():
        if parent in category_of and category_of[parent] != category_of[child]:
            result.add(
                E_INVALID_INHERITANCE,
                f"{child}.is_a",
                f"{child} ({category_of[child]}) cannot specialize "
                f"{parent} ({category_of[parent]})",
                concept_id=child,
            )

    for start in parents:
        seen = {start}
        node = parents.get(start)
        while node is not None:
            if node in seen:
                result.add(
                    E_INHERITANCE_CYCLE,
                    f"{start}.is_a",
                    f"inheritance cycle through {node!r}",
                    concept_id=start,
                )
                break
            seen.add(node)
            node = parents.get(node)


def check_roles_not_entity_types(
    entity_type_ids: Iterable[str],
    parameters: ConstraintParameters,
    result: ValidationResult,
) -> None:
    """A role is something a company plays, never something a company is.

    Declaring `lender` as an entity type would make the role permanent and global, which the
    corpus contradicts directly: the same counterparty appears as competitor in one period
    and partner in another.
    """
    for concept_id in entity_type_ids:
        if concept_id in parameters.role_concepts_forbidden_as_entity_types:
            result.add(
                E_ROLE_AS_ENTITY,
                f"entity_types/{concept_id}",
                f"{concept_id!r} is a contextual role and must be declared in roles.yaml",
                concept_id=concept_id,
            )


def check_units(metrics: Sequence[MetricDefinition], result: ValidationResult) -> None:
    for metric in metrics:
        for unit in (metric.unit, *metric.allowed_units):
            if unit not in KNOWN_UNITS:
                result.add(
                    E_UNKNOWN_UNIT,
                    f"{metric.concept_id}.unit",
                    f"unit {unit!r} is not in KNOWN_UNITS",
                    concept_id=metric.concept_id,
                )
        if metric.allowed_units and metric.unit not in metric.allowed_units:
            result.add(
                E_UNKNOWN_UNIT,
                f"{metric.concept_id}.allowed_units",
                f"default unit {metric.unit!r} missing from allowed_units",
                concept_id=metric.concept_id,
            )


def check_external_mappings(
    concepts: Sequence[ConceptDefinition],
    metric_categories: Mapping[str, MetricCategory],
    parameters: ConstraintParameters,
    result: ValidationResult,
) -> None:
    """Two failures the research specifically warns about.

    A `no_mapping` carrying a URI means someone recorded an absence and a target at once —
    almost always an invented URI. And an `EconomicIndicator` mapping on a company KPI is
    the mapping the research forbids by name: FIBO scopes that class to economic activity in
    a statistical region, which a homes-sold count is not.
    """
    for concept in concepts:
        for index, mapping in enumerate(concept.external_mappings):
            path = f"{concept.concept_id}.external_mappings[{index}]"
            _check_one_mapping(concept, mapping, path, metric_categories, parameters, result)


def _check_one_mapping(
    concept: ConceptDefinition,
    mapping: ExternalMapping,
    path: str,
    metric_categories: Mapping[str, MetricCategory],
    parameters: ConstraintParameters,
    result: ValidationResult,
) -> None:
    if mapping.mapping_type == MappingType.NO_MAPPING:
        if mapping.external_uri:
            result.add(
                E_INVENTED_URI,
                path,
                "mapping_type is no_mapping but an external_uri is present",
                concept_id=concept.concept_id,
            )
        return

    if not (mapping.external_uri or mapping.external_label):
        result.add(
            E_INVALID_MAPPING,
            path,
            f"mapping_type {mapping.mapping_type} requires a URI or a label",
            concept_id=concept.concept_id,
        )
        return

    if not _is_economic_indicator(mapping, parameters):
        return

    category = metric_categories.get(concept.concept_id)
    if category is None:
        result.add(
            E_KPI_AS_MACRO,
            path,
            "EconomicIndicator may only be mapped to a metric definition",
            concept_id=concept.concept_id,
        )
    elif str(category) not in parameters.economic_indicator_allowed_categories:
        result.add(
            E_KPI_AS_MACRO,
            path,
            f"{concept.concept_id!r} is a {category} metric; FIBO EconomicIndicator is "
            f"scoped to economic activity in a statistical region",
            concept_id=concept.concept_id,
        )


def _is_economic_indicator(
    mapping: ExternalMapping, parameters: ConstraintParameters
) -> bool:
    # `pattern_only` says "we reused the shape", which is exactly what the 120-day metric
    # does with the baseline/comparison split. It is not a claim of being an indicator.
    if mapping.mapping_type == MappingType.PATTERN_ONLY:
        return False
    return (
        (mapping.external_label or "") in parameters.economic_indicator_labels
        or (mapping.external_uri or "") in parameters.economic_indicator_uris
    )


def check_alias_collisions(
    aliases: Mapping[str, tuple[str, ...]],
    parameters: ConstraintParameters,
    result: ValidationResult,
) -> None:
    """No alias may silently resolve to two members of a mutually-distinct group.

    This is the check that stops "homes purchased" and "homes under contract" from being
    merged by an alias table, which the research names as the most damaging failure
    available to this ontology.
    """
    for group in parameters.mutually_distinct_groups:
        members = set(group.concept_ids)
        for alias, concept_ids in aliases.items():
            overlap = sorted(members & set(concept_ids))
            if len(overlap) > 1:
                result.add(
                    E_ALIAS_COLLISION,
                    f"aliases/{alias}",
                    f"resolves to {overlap} which are mutually distinct "
                    f"({group.group_id}): {group.rationale.strip()[:120]}",
                )


def declared_ambiguous(entries: Sequence[AliasEntry]) -> frozenset[str]:
    """Aliases the ontology itself declares ambiguous, so collisions there are intentional."""
    return frozenset(normalize_alias(e.alias) for e in entries if e.ambiguous)


def check_formula_versions(
    formulas: Sequence[MetricFormulaVersion],
    metric_ids: frozenset[str],
    result: ValidationResult,
) -> None:
    """Versions of one metric must not overlap in time, or resolution becomes arbitrary."""
    for formula in formulas:
        for component in formula.component_metrics:
            if component not in metric_ids:
                result.add(
                    E_FORMULA_UNKNOWN_COMPONENT,
                    f"{formula.concept_id}.component_metrics",
                    f"{component!r} is not a metric definition",
                    concept_id=formula.concept_id,
                )

    by_metric: dict[str, list[MetricFormulaVersion]] = {}
    for formula in formulas:
        by_metric.setdefault(formula.metric_id, []).append(formula)

    for metric_id, versions in by_metric.items():
        ordered = sorted(versions, key=lambda f: (f.valid_from, f.version))
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier.valid_to is None or earlier.valid_to >= later.valid_from:
                result.add(
                    E_FORMULA_OVERLAP,
                    f"formulas/{metric_id}",
                    f"{earlier.concept_id} (to {earlier.valid_to}) overlaps "
                    f"{later.concept_id} (from {later.valid_from})",
                    concept_id=earlier.concept_id,
                )


def check_relationship_endpoints(
    relationships: Sequence[RelationshipDefinition],
    known_ids: frozenset[str],
    result: ValidationResult,
) -> None:
    """Endpoint types must exist, and a declared inverse must exist and point back."""
    by_relationship_id = {r.relationship_id: r for r in relationships}
    for relationship in relationships:
        endpoints = (
            *(("allowed_source_types", t) for t in relationship.allowed_source_types),
            *(("allowed_target_types", t) for t in relationship.allowed_target_types),
        )
        for attribute, target in endpoints:
            if target not in known_ids:
                result.add(
                    E_INVALID_ENDPOINT,
                    f"{relationship.relationship_id}.{attribute}",
                    f"unknown endpoint type {target!r}",
                    concept_id=relationship.concept_id,
                )

        inverse_id = relationship.inverse_relationship
        if inverse_id is None:
            continue
        inverse = by_relationship_id.get(inverse_id)
        if inverse is None:
            result.add(
                E_INVALID_ENDPOINT,
                f"{relationship.relationship_id}.inverse_relationship",
                f"unknown inverse {inverse_id!r}",
                concept_id=relationship.concept_id,
            )
        elif inverse.inverse_relationship != relationship.relationship_id:
            result.add(
                E_INVALID_ENDPOINT,
                f"{relationship.relationship_id}.inverse_relationship",
                f"{inverse_id} does not declare {relationship.relationship_id} as its inverse",
                concept_id=relationship.concept_id,
                severity=Severity.WARNING,
            )


# --------------------------------------------------------------------------------------
# Claim-time
# --------------------------------------------------------------------------------------


def check_observation_periods(
    observation: MetricObservation, metric: MetricDefinition, result: ValidationResult
) -> None:
    path = f"observation/{observation.observation_id}"
    if metric.period_type == PeriodType.INSTANT:
        if not observation.instant_date:
            result.add(E_MISSING_INSTANT, path,
                       f"{metric.concept_id} is an instant metric and needs instant_date")
    elif metric.period_type == PeriodType.DURATION:
        if not (observation.period_start and observation.period_end):
            result.add(E_MISSING_PERIOD, path,
                       f"{metric.concept_id} is a duration metric and needs a period")

    if (
        observation.period_start
        and observation.period_end
        and observation.period_end < observation.period_start
    ):
        result.add(E_PERIOD_ORDER, path,
                   f"period_end {observation.period_end} precedes "
                   f"period_start {observation.period_start}")


def check_future_period(
    observation: MetricObservation,
    parameters: ConstraintParameters,
    result: ValidationResult,
    carrier_date: str | None = None,
) -> None:
    """A filed figure cannot measure a period the filing had not yet lived through.

    **Deliberately not "reject anything after today".** Wall-clock time is not a property of
    the corpus: replaying a 2021 filing in 2026 must give the same answer it gave in 2021, and
    a now-based rule would silently change verdicts as the clock moved. The comparison is
    against the *carrier* — the filing that reports the value — so it is a fact about two dates
    the run already holds.

    **Reporting lag is unbounded in the permitted direction.** A period ending well before its
    filing is the normal case, not a suspicious one: measured over the 2,704 observations of
    `extract-v1-lexical-2422c4252c07` on 2026-08-03, the lag runs from **+15 days** (Q3 2020
    8-K) to **1,045 days** (a FY2019 comparative column in a FY2022 10-K), and all 2,704 pass.
    Only the reverse direction — period end *after* the carrier — is refused.

    Three exemptions, and no others:

    - `GUIDED`, unconditionally. A guided figure is *about* a period the company has not
      reported yet; that is what the assertion type means, and refusing it would make
      `guidance_issuance.inference_restrictions` unsatisfiable again from the other side.
    - `CALCULATED`, only for a metric the ontology names in
      `constraints.yaml: future_period.allowed_calculated_metrics`. A projection re-derived
      from filed inputs is legitimate; an unlisted one is indistinguishable from a mislabelled
      guidance figure, which is the exact failure this check exists to catch.
    - No carrier date known — the check abstains rather than guesses. **This is presently the
      common case:** `reported_at` is populated by no lane and appears in no catalog *(verified
      2026-08-03)*, so on the current run the rule fires only when a caller passes
      `carrier_date`. That is a gap in what the lanes record, not a weakening of the rule.

    The carrier is the **filing date**, never the report date. Measured on the same run: one
    observation (`obs:borrowing-capacity:opendoor:2022-10-19:…`) legitimately carries an instant
    *after* its document's `report_date` — a subsequent event disclosed in a 10-Q — and a
    report-date rule would refuse it. Against `filing_date`, zero observations are
    forward-looking.
    """
    carrier = carrier_date or observation.reported_at
    if not carrier:
        return
    period_end = observation.period_end or observation.instant_date
    if not period_end or period_end <= carrier:
        return
    if observation.assertion_type == AssertionType.GUIDED:
        return
    if (
        observation.assertion_type == AssertionType.CALCULATED
        and observation.metric_id in parameters.future_period_allowed_calculated_metrics
    ):
        return
    result.add(
        E_FUTURE_PERIOD,
        f"observation/{observation.observation_id}",
        f"period ends {period_end} but the filing carrying it is dated {carrier}; "
        f"a {observation.assertion_type} observation cannot measure a period that had not "
        f"ended when it was reported. A forward-looking figure is assertion_type "
        f"{AssertionType.GUIDED} on a guidance_issuance event, not an observation.",
    )


def check_observation_unit(
    observation: MetricObservation, metric: MetricDefinition, result: ValidationResult
) -> None:
    path = f"observation/{observation.observation_id}"
    allowed = set(metric.allowed_units) | {metric.unit}
    if observation.unit not in allowed:
        result.add(E_UNIT_MISMATCH, path,
                   f"unit {observation.unit!r} not allowed for {metric.concept_id} "
                   f"(allowed: {sorted(allowed)})")
    if observation.unit in MONETARY_UNITS and not observation.currency:
        result.add(E_MISSING_CURRENCY, path,
                   f"unit {observation.unit!r} is monetary and requires a currency")


def check_observation_subject(
    observation: MetricObservation,
    metric: MetricDefinition,
    accepts: "Callable[[tuple[str, ...], str], bool]",
    result: ValidationResult,
) -> None:
    """Subtype-aware: a metric declaring `company` accepts `public_company`.

    Without this, every metric would have to enumerate the subtypes of its own subject type
    and would silently start rejecting valid observations when a subtype is added.
    """
    if metric.subject_types and not accepts(metric.subject_types, observation.subject_type):
        result.add(
            E_SUBJECT_TYPE,
            f"observation/{observation.observation_id}",
            f"subject_type {observation.subject_type!r} not allowed for "
            f"{metric.concept_id} (allowed: {list(metric.subject_types)})",
        )


def check_observation_source_lane(
    observation: MetricObservation, metric: MetricDefinition, result: ValidationResult
) -> None:
    """A forbidden lane is a stronger statement than an unpreferred one.

    `homes_sold` forbids `xbrl` because the research checked all 97 taxonomy files and found
    no such tag. An observation claiming an XBRL source for it is therefore not a low-quality
    observation — it is a wrong one.
    """
    if observation.source_lane in metric.forbidden_source_lanes:
        result.add(
            E_SOURCE_LANE_FORBIDDEN,
            f"observation/{observation.observation_id}",
            f"{metric.concept_id} is never sourced from {observation.source_lane}",
        )


def check_percentage_range(
    observation: MetricObservation,
    metric: MetricDefinition,
    parameters: ConstraintParameters,
    result: ValidationResult,
) -> None:
    if not isinstance(observation.value, (int, float)) or isinstance(observation.value, bool):
        return
    low = metric.percentage_min
    high = metric.percentage_max
    if low is None and high is None:
        low, high = parameters.percentage_default_min, parameters.percentage_default_max
    if (low is not None and observation.value < low) or (
        high is not None and observation.value > high
    ):
        result.add(
            E_PERCENTAGE_RANGE,
            f"observation/{observation.observation_id}",
            f"value {observation.value} outside documented range [{low}, {high}] "
            f"for {metric.concept_id}",
        )


def check_calculation_fields(
    observation: MetricObservation, result: ValidationResult
) -> None:
    """Reported and calculated are different acts, so they carry different fields.

    A reported figure has no inputs — it was read off a page. A calculated one without its
    formula version and inputs cannot be re-derived or audited, which defeats the point of
    recording that it was calculated.
    """
    path = f"observation/{observation.observation_id}"
    calculation_fields = (
        observation.formula_version_id,
        observation.input_observation_ids,
        observation.calculation_expression,
    )
    if observation.assertion_type == AssertionType.CALCULATED:
        if not (observation.formula_version_id and observation.input_observation_ids):
            result.add(E_CALC_WITHOUT_INPUTS, path,
                       "calculated observation requires formula_version_id and "
                       "input_observation_ids")
    elif any(calculation_fields):
        result.add(E_CALC_FIELDS_ON_REPORTED, path,
                   f"assertion_type is {observation.assertion_type} but calculation "
                   f"fields are set")


def check_formula_for_date(
    observation: MetricObservation,
    formulas: Sequence[MetricFormulaVersion],
    result: ValidationResult,
) -> None:
    """Formula resolution is by observation date, never by filing date.

    A 2024 10-K restating FY2021 must still use the FY2021 formula for the FY2021 column.
    """
    if observation.assertion_type != AssertionType.CALCULATED:
        return
    as_of = observation.period_end or observation.instant_date
    if not as_of:
        return
    covering = [
        f for f in formulas
        if f.metric_id == observation.metric_id
        and f.valid_from <= as_of
        and (f.valid_to is None or as_of <= f.valid_to)
    ]
    path = f"observation/{observation.observation_id}"
    if not covering:
        result.add(E_NO_FORMULA_FOR_DATE, path,
                   f"no formula version of {observation.metric_id} covers {as_of}")
    elif observation.formula_version_id and all(
        f.concept_id != observation.formula_version_id for f in covering
    ):
        result.add(E_NO_FORMULA_FOR_DATE, path,
                   f"formula {observation.formula_version_id!r} does not cover {as_of}; "
                   f"applicable: {[f.concept_id for f in covering]}")


def check_evidence(
    evidence: Sequence[Any],
    required: bool,
    path: str,
    parameters: ConstraintParameters,
    result: ValidationResult,
) -> None:
    """Evidence must exist, must anchor, and must be citable for the claim being made."""
    anchored = [e for e in evidence if getattr(e, "has_anchor", False)]
    if required and not anchored:
        result.add(E_MISSING_EVIDENCE, path, "no evidence reference with a usable anchor")

    for index, reference in enumerate(evidence):
        reference_path = f"{path}.evidence[{index}]"
        channel = getattr(reference, "disclosure_channel_id", None)
        if channel in parameters.discovery_only_channels:
            result.add(
                E_DISCOVERY_ONLY_AS_CANONICAL,
                reference_path,
                f"{channel!r} is a discovery-only channel: no as-of date, no archive, "
                f"self-described estimates. It cannot be the citation for a filed period.",
            )
        start = getattr(reference, "char_start", None)
        end = getattr(reference, "char_end", None)
        if start is not None and end is not None and end <= start:
            result.add(E_EVIDENCE_OFFSETS, reference_path,
                       f"char_end {end} does not follow char_start {start}")
        elif (start is None) != (end is None):
            result.add(E_EVIDENCE_OFFSETS, reference_path,
                       "char_start and char_end must be given together")


def check_confidence(
    confidence: float | None,
    path: str,
    parameters: ConstraintParameters,
    result: ValidationResult,
) -> None:
    if confidence is None:
        return
    if not parameters.confidence_min <= confidence <= parameters.confidence_max:
        result.add(E_CONFIDENCE_RANGE, path,
                   f"confidence {confidence} outside "
                   f"[{parameters.confidence_min}, {parameters.confidence_max}]")


def check_event_participants(
    event: EventInstance,
    definition: EventTypeDefinition,
    result: ValidationResult,
) -> None:
    path = f"event/{event.event_id}"
    declared = {p.role: p for p in definition.participants}
    present: dict[str, int] = {}

    for index, participant in enumerate(event.participants):
        expected = declared.get(participant.role)
        if expected is None:
            result.add(E_EVENT_PARTICIPANT, f"{path}.participants[{index}]",
                       f"{definition.concept_id} declares no participant role "
                       f"{participant.role!r}")
            continue
        present[participant.role] = present.get(participant.role, 0) + 1
        if participant.entity_type not in expected.entity_types:
            result.add(E_EVENT_PARTICIPANT, f"{path}.participants[{index}]",
                       f"role {participant.role!r} does not accept entity type "
                       f"{participant.entity_type!r} (allowed: {list(expected.entity_types)})")

    for role, expected in declared.items():
        count = present.get(role, 0)
        if expected.required and count == 0:
            result.add(E_EVENT_PARTICIPANT, path,
                       f"{definition.concept_id} requires a {role!r} participant")
        elif expected.cardinality == "one" and count > 1:
            result.add(E_EVENT_PARTICIPANT, path,
                       f"role {role!r} accepts one participant, got {count}")


def check_event_temporal(
    event: EventInstance, definition: EventTypeDefinition, result: ValidationResult
) -> None:
    """Every `required_temporal_fields` entry, and at least one `required_temporal_any_of`.

    The alternative form exists because an unconditional `occurred_on` made a true answer
    unrepresentable *(2026-08-02)*. An executive change is announced before it takes effect,
    and a press release that says "today announced that X has been appointed" dates the
    announcement and nothing else. Requiring `occurred_on` there left a correct lane two
    choices, both wrong: invent an effective date, or emit no event for a change the passage
    plainly reports.

    Nothing here infers one field from the other, and nothing may: the announcement date and
    the occurrence date are different facts about different days.
    """
    for attribute in definition.required_temporal_fields:
        if not getattr(event, attribute, None):
            result.add(E_EVENT_TEMPORAL, f"event/{event.event_id}",
                       f"{definition.concept_id} requires {attribute}")

    alternatives = definition.required_temporal_any_of
    if alternatives and not any(getattr(event, a, None) for a in alternatives):
        result.add(E_EVENT_TEMPORAL, f"event/{event.event_id}",
                   f"{definition.concept_id} requires at least one of "
                   f"{list(alternatives)}")


#: A plain number and nothing else. `"1.0 billion"`, `"1,000"`, `"$1.0"` and `"~1.0"` all fail
#: on purpose: each is a rendering that still carries its scale or its symbol in the string,
#: and the scale belongs in the unit. Accepting any of them would reintroduce exactly the hole
#: the typed range exists to close (V1 §5.2 defect 2).
_PLAIN_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def _as_number(value: Any) -> float | None:
    """The value as a number, or None if it is not one. Booleans are not numbers here."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and _PLAIN_NUMBER.match(value.strip()):
        return float(value)
    return None


def check_event_assertion_type(
    event: EventInstance, definition: EventTypeDefinition, result: ValidationResult
) -> None:
    """An event type may refuse an assertion type outright.

    `guidance_issuance` refuses `reported`: a target the company has not met is not a report of
    anything. The restriction was prose in `inference_restrictions` and read by nothing until
    this check *(F0 Part D1)*.
    """
    if event.assertion_type in definition.forbidden_assertion_types:
        result.add(
            E_EVENT_ASSERTION_TYPE,
            f"event/{event.event_id}",
            f"{definition.concept_id} refuses assertion_type "
            f"{event.assertion_type!r} (forbidden: "
            f"{[str(a) for a in definition.forbidden_assertion_types]})",
        )


def check_event_properties(
    event: EventInstance,
    definition: EventTypeDefinition,
    metric_ids: frozenset[str],
    result: ValidationResult,
) -> None:
    """Types for the properties an event type declares typed. See `EventPropertyContract`.

    Event types that declare no `property_contract` are unaffected, so this is additive: the
    only thing it can newly refuse is a value an event type has explicitly typed.
    """
    contract = definition.property_contract
    if contract is None:
        return
    path = f"event/{event.event_id}"
    properties = event.properties

    for name in contract.required_properties:
        if not str(properties.get(name) or "").strip():
            result.add(E_EVENT_PROPERTY_TYPE, f"{path}.properties.{name}",
                       f"{definition.concept_id} requires {name!r}")

    for name in contract.numeric_properties:
        if name in properties and _as_number(properties[name]) is None:
            result.add(E_EVENT_PROPERTY_TYPE, f"{path}.properties.{name}",
                       f"{properties[name]!r} is not a plain number. The scale belongs in "
                       f"{contract.unit_property or 'the unit property'}, not in the digits.")

    for name in contract.metric_reference_properties:
        value = properties.get(name)
        if value is not None and str(value) not in metric_ids:
            result.add(E_EVENT_PROPERTY_REFERENCE, f"{path}.properties.{name}",
                       f"{value!r} is not a declared metric definition")

    _check_event_unit(event, contract, path, result)
    _check_event_value_range(event, contract, path, result)


def _check_event_unit(
    event: EventInstance,
    contract: EventPropertyContract,
    path: str,
    result: ValidationResult,
) -> None:
    """Same two rules an observation's unit faces, from the same two vocabularies.

    Deliberately the same codes as `check_observation_unit`: an unknown unit on a guided value
    and an unknown unit on a measured one are the same defect, and a caller filtering on
    `unknown_unit` should see both.
    """
    if not contract.unit_property:
        return
    unit = event.properties.get(contract.unit_property)
    if unit is None:
        return
    if unit not in KNOWN_UNITS:
        result.add(E_UNKNOWN_UNIT, f"{path}.properties.{contract.unit_property}",
                   f"unit {unit!r} is not in KNOWN_UNITS")
    if unit in MONETARY_UNITS and contract.currency_property:
        if not str(event.properties.get(contract.currency_property) or "").strip():
            result.add(E_MISSING_CURRENCY, f"{path}.properties.{contract.unit_property}",
                       f"unit {unit!r} is monetary and requires "
                       f"{contract.currency_property!r}")


def _check_event_value_range(
    event: EventInstance,
    contract: EventPropertyContract,
    path: str,
    result: ValidationResult,
) -> None:
    """Both bounds, or neither, and a unit whenever there is a number to scale.

    Three shapes are legitimate and the third is the reason this is not simply "require both":

    - a **range** — two different bounds;
    - a **point** — both bounds set to the same value, which is how a single guided figure is
      expressed without a second representation to keep in step;
    - **qualitative** — neither bound. "we expect holding periods to lengthen" is a real
      guidance statement with no number in it, and the plan (§5.3) is explicit that it must not
      be coerced into inventing one.

    A *lone* bound is none of the three: it is either a range whose other half was lost or a
    point that failed to say so, and the two are indistinguishable afterwards.
    """
    low_name, high_name = contract.range_low_property, contract.range_high_property
    if not (low_name and high_name):
        return
    low, high = event.properties.get(low_name), event.properties.get(high_name)
    present = [name for name, value in ((low_name, low), (high_name, high))
               if value is not None]
    if not present:
        return  # qualitative guidance: permitted, and never made to invent a number
    if len(present) == 1:
        result.add(E_EVENT_VALUE_RANGE, f"{path}.properties.{present[0]}",
                   f"{present[0]!r} is set without {low_name if high is None else high_name!r}"
                   f"; a point value sets both bounds to the same number, and a range sets "
                   f"both. One bound alone states neither.")
        return

    low_number, high_number = _as_number(low), _as_number(high)
    if low_number is not None and high_number is not None and high_number < low_number:
        result.add(E_EVENT_VALUE_RANGE, path,
                   f"{high_name} {high!r} is below {low_name} {low!r}")
    if contract.unit_property and not str(
        event.properties.get(contract.unit_property) or ""
    ).strip():
        result.add(E_EVENT_VALUE_RANGE, f"{path}.properties.{contract.unit_property}",
                   f"a value range needs {contract.unit_property!r}: a bare number carries no "
                   f"scale, and 1.0 is not comparable to an actual without one")


def check_relationship_instance(
    instance: RelationshipInstance,
    definition: RelationshipDefinition | None,
    result: ValidationResult,
) -> None:
    path = f"relationship/{instance.relationship_id}:{instance.source_id}->{instance.target_id}"
    if definition is None:
        result.add(E_UNKNOWN_PREDICATE, path,
                   f"{instance.relationship_id!r} is not a declared relationship")
        return
    if instance.source_type not in definition.allowed_source_types:
        result.add(E_INVALID_ENDPOINT, f"{path}.source_type",
                   f"{instance.source_type!r} not allowed as source of "
                   f"{instance.relationship_id} "
                   f"(allowed: {list(definition.allowed_source_types)})")
    if instance.target_type not in definition.allowed_target_types:
        result.add(E_INVALID_ENDPOINT, f"{path}.target_type",
                   f"{instance.target_type!r} not allowed as target of "
                   f"{instance.relationship_id} "
                   f"(allowed: {list(definition.allowed_target_types)})")
    if (
        instance.valid_from
        and instance.valid_to
        and instance.valid_to < instance.valid_from
    ):
        result.add(E_PERIOD_ORDER, path,
                   f"valid_to {instance.valid_to} precedes valid_from {instance.valid_from}")


# --------------------------------------------------------------------------------------


def _as_ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return ()
