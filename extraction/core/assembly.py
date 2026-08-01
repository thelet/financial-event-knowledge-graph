"""Lane output to validated ontology claims.

One place, deliberately. Both lanes produce `LaneClaim`s and both go through here, so the
policies that ONTOLOGY_V1_IMPLEMENTATION §16 deferred to extraction — carrying the 120-day
population verbatim, refusing metrics whose source lane does not exist, not inventing a
`SUPERSEDES` edge — are applied once rather than reimplemented per lane.

Evidence is built from the passage catalog's own fields, which map one-to-one onto
`EvidenceReference` *(verified)*. The ontology deliberately does not constrain evidence id
grammar — `evidence_types` declare field *names* only — so pinning the `norm:` grammar is
this layer's job. Doing it in `claims.yaml` instead would couple the vocabulary to one
corpus, which is the coupling the ontology layer exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ontology.core.models import (
    EvidenceReference,
    MetricObservation,
    OntologyClaim,
    Population,
)

from .identifiers import claim_id, observation_id
from .models import LaneClaim

# Metrics whose first source lane is `xbrl`, which the normalized corpus does not contain.
# V1_CLAIM_EXTRACTION §3.1: acquisition fetched 364 XBRL artifacts and normalization
# excluded every one as NON_NARRATIVE_MEDIA, correctly for a document normalizer.
DEFERRED_STATUS = "deferred"
DEFERRED_REASON = "UNAVAILABLE_REQUIRED_SOURCE_LANE"
DEFERRED_REQUIRED_LANE = "xbrl"


@dataclass(frozen=True)
class DeferredMetric:
    """A metric that exists, is readable, and has no lane to read it through.

    Recorded rather than dropped. A silent skip and a stated deferral look identical in a
    catalog, and only one of them tells the next plan what to build.
    """

    metric_id: str
    status: str = DEFERRED_STATUS
    reason: str = DEFERRED_REASON
    required_lane: str = DEFERRED_REQUIRED_LANE


@dataclass
class AssemblyResult:
    claims: list[OntologyClaim] = field(default_factory=list)
    deferred: list[DeferredMetric] = field(default_factory=list)


def deferred_metric_ids(ontology) -> frozenset[str]:
    """Metrics whose *first* preference is a lane this corpus lacks.

    Derived from the ontology rather than hard-coded, so adding an XBRL lane later needs no
    edit here and a vocabulary change cannot silently disagree with the code. A metric that
    names xbrl only as a fallback stays in scope.
    """
    deferred = set()
    for metric in ontology.registry.by_category("metric_definition"):
        preferences = tuple(getattr(metric, "source_lane_preferences", ()) or ())
        if preferences and str(preferences[0]) == DEFERRED_REQUIRED_LANE:
            deferred.add(metric.concept_id)
    return frozenset(deferred)


def build_evidence(claim: LaneClaim, passage_row: dict | None = None) -> EvidenceReference:
    """An evidence reference pinned to a real passage.

    `table_id` in the corpus is a *block* id (`…#b8`), not a separate namespace, so it and
    `block_ids` legitimately overlap. Callers must not assume they are disjoint.
    """
    row = passage_row or {}
    kind = "normalized_table" if claim.source_lane == "normalized_table" else "normalized_passage"
    return EvidenceReference(
        evidence_kind=kind,
        passage_id=claim.passage_id,
        document_id=claim.document_id,
        table_id=row.get("table_id"),
        block_ids=tuple(row.get("block_ids") or ()),
        source_url=row.get("source_url"),
        quoted_text=claim.raw_text or None,
    )


def to_observation(claim: LaneClaim, evidence: EvidenceReference) -> MetricObservation:
    population = (
        Population(definition_raw=claim.population_definition_raw)
        if claim.population_definition_raw
        else None
    )
    return MetricObservation(
        observation_id=observation_id(
            claim.metric_id,
            claim.subject_entity_id,
            claim.period.key,
            claim.source_lane,
            claim.passage_id,
        ),
        metric_id=claim.metric_id,
        subject_entity_id=claim.subject_entity_id,
        subject_type=claim.subject_type,
        value=claim.value,
        unit=claim.unit,
        currency=claim.currency,
        period_start=claim.period.period_start,
        period_end=claim.period.period_end,
        instant_date=claim.period.instant_date,
        population=population,
        source_lane=claim.source_lane,
        evidence=(evidence,),
        assertion_type=claim.assertion_type,
        confidence=claim.confidence,
    )


def to_claim(claim: LaneClaim, passage_row: dict | None = None) -> OntologyClaim:
    observation = to_observation(claim, build_evidence(claim, passage_row))
    metadata = dict(claim.extractor_metadata)
    if claim.ambiguity_codes:
        metadata["ambiguity_codes"] = list(claim.ambiguity_codes)
    if claim.row_label:
        metadata["row_label"] = claim.row_label
    if claim.column_label:
        metadata["column_label"] = claim.column_label
    if claim.scale is not None:
        metadata["scale"] = claim.scale.scale
        metadata["scale_location"] = claim.scale.location
    return OntologyClaim(
        claim_id=claim_id("metric_observation", observation.observation_id),
        claim_kind="metric_observation",
        metric_observation=observation,
        assertion_type=claim.assertion_type,
        confidence=claim.confidence,
        extractor_metadata=metadata,
    )


def assemble(
    lane_claims: list[LaneClaim],
    *,
    ontology,
    passage_rows: dict[str, dict] | None = None,
) -> AssemblyResult:
    """Turn lane output into ontology claims, deferring what has no lane.

    Deliberately does *not* emit `SUPERSEDES`. When a 10-K restates a prior year both
    observations are emitted, each with its own evidence, and the deterministic id keeps
    them distinct through the passage digest. Deciding which supersedes which inside a
    per-passage extractor means deciding it without having seen the other filing; it is a
    graph-layer policy over a complete set. ONTOLOGY_V1_IMPLEMENTATION §16.5.
    """
    deferred_ids = deferred_metric_ids(ontology)
    rows = passage_rows or {}
    result = AssemblyResult()
    seen_deferred: set[str] = set()

    for lane_claim in lane_claims:
        if lane_claim.metric_id in deferred_ids:
            if lane_claim.metric_id not in seen_deferred:
                seen_deferred.add(lane_claim.metric_id)
                result.deferred.append(DeferredMetric(metric_id=lane_claim.metric_id))
            continue
        result.claims.append(to_claim(lane_claim, rows.get(lane_claim.passage_id)))
    return result
