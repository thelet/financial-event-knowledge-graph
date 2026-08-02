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
    EventInstance,
    EventParticipantRef,
    EvidenceReference,
    MetricObservation,
    OntologyClaim,
    Population,
    RelationshipInstance,
)

from .identifiers import claim_id, event_id, observation_id, relationship_instance_id
from .models import LaneClaim, LaneEvent, LaneRelationship

# Metrics whose first source lane is `xbrl`, which the normalized corpus does not contain.
# V1_CLAIM_EXTRACTION §3.1: acquisition fetched 364 XBRL artifacts and normalization
# excluded every one as NON_NARRATIVE_MEDIA, correctly for a document normalizer.
DEFERRED_STATUS = "deferred"
DEFERRED_REASON = "UNAVAILABLE_REQUIRED_SOURCE_LANE"
DEFERRED_REQUIRED_LANE = "xbrl"

# §7.1's refusal, declared where the policy is enforced so that a lane wanting to state the
# same refusal earlier imports it rather than re-spelling it. `core/` may not import a stage,
# so this is the only direction that keeps one spelling.
MISSING_POPULATION_DEFINITION = "MISSING_POPULATION_DEFINITION"


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


@dataclass(frozen=True)
class PolicyRejection:
    """A lane claim refused by a §7 policy rather than by the ontology.

    Separate from `DeferredMetric` because the two say different things to the next plan: a
    deferral means the lane to read this metric does not exist yet, while a rejection means a
    lane read it and produced something this layer will not carry.
    """

    metric_id: str
    reason: str
    detail: str
    passage_id: str


@dataclass
class AssemblyResult:
    claims: list[OntologyClaim] = field(default_factory=list)
    deferred: list[DeferredMetric] = field(default_factory=list)
    rejected: list[PolicyRejection] = field(default_factory=list)


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


def declared_ambiguity_codes(ontology, metric_id: str) -> tuple[str, ...]:
    """Every ambiguity the ontology records against a metric, attached to its observations.

    §7.3 asks for one code on one metric — `homes_sold_recognition_point`, because no filing
    says whether a home is counted at title transfer, closing or contract completion. It is
    derived from the ontology rather than written down here for the same reason
    `deferred_metric_ids` is: a vocabulary that records an unresolved question and code that
    names a different one would disagree with nothing able to notice.

    Four metrics carry ambiguities today. Attaching all of them rather than the one §7.3
    names is the same policy applied consistently: each was recorded on the concept precisely
    so it would travel with the observation instead of being silently decided.
    """
    metric = ontology.registry.find(metric_id) if ontology is not None else None
    if metric is None:
        return ()
    return tuple(sorted(
        str(getattr(ambiguity, "code", ""))
        for ambiguity in (getattr(metric, "ambiguities", ()) or ())
        if getattr(ambiguity, "code", "")))


def population_policy_failure(ontology, claim: LaneClaim) -> str | None:
    """§7.1, enforced once for both lanes.

    A metric whose ontology entry declares a `population` has three filed wordings in this
    corpus — "our portfolio", "our homes", "our homes in inventory" — and no filing reconciles
    them. An observation that does not carry the wording it was read under cannot be told
    apart from one read under a different denominator, and a catalog that presents them as one
    series asserts a comparison the filings do not support.

    Derived from the declaration, not from a metric id: exactly one metric declares a
    population today, and a second would be covered without an edit here.
    """
    metric = ontology.registry.find(claim.metric_id) if ontology is not None else None
    if metric is None or getattr(metric, "population", None) is None:
        return None
    if claim.population_definition_raw:
        return None
    return (f"{claim.metric_id} declares a population and the claim carries no "
            "population_definition_raw; two denominators are not one series")


def build_evidence(
    claim: LaneClaim | LaneEvent | LaneRelationship, passage_row: dict | None = None
) -> EvidenceReference:
    """An evidence reference pinned to a real passage.

    `table_id` in the corpus is a *block* id (`…#b8`), not a separate namespace, so it and
    `block_ids` legitimately overlap. Callers must not assume they are disjoint.

    One function for all three payload kinds rather than three, because the five fields it
    reads mean the same thing on each — and an evidence anchor whose shape depended on the
    payload kind is the one thing `verify` could not check uniformly.
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


def to_claim(
    claim: LaneClaim, passage_row: dict | None = None, *, ontology=None
) -> OntologyClaim:
    """One lane claim as an ontology claim.

    `ontology` is optional and is what turns the §7 policies on. It is a keyword rather than a
    required argument because the mapping from a `LaneClaim` to an `OntologyClaim` is
    mechanical and testable without a vocabulary, while the policies are not; `assemble` always
    passes it, and that is the path a run takes.
    """
    observation = to_observation(claim, build_evidence(claim, passage_row))
    metadata = dict(claim.extractor_metadata)
    codes = tuple(claim.ambiguity_codes) + declared_ambiguity_codes(ontology, claim.metric_id)
    if codes:
        metadata["ambiguity_codes"] = sorted(set(codes))
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


# -- events and relationships -------------------------------------------------------------------
#
# The same shape as the metric path above, one layer thinner. There is no §7 policy that
# applies to an event, nothing is deferred to a lane this corpus lacks, and the deterministic
# ids come from `identifiers.py` rather than being minted here.
#
# **Validation is not done here, and the symmetry is the point.** `assemble` does not ask the
# ontology whether its observations are valid either; `core.validation.validate` does, for all
# three claim kinds at once, through `payload_evidence` and `ontology.validate_claims`. An
# assembler that adjudicated events and not observations would be two policies wearing one name.


def to_event(event: LaneEvent, evidence: EvidenceReference) -> EventInstance:
    """A lane event as the ontology's own payload. No repair, no defaulting.

    Both dates pass through as read, including `None`. Supplying one here — the filing date is
    in the passage row, right there — would turn the one honest answer a lane can give about
    an undated event into a fabricated measurement that every downstream check would pass
    (V1_CLAIM_EXTRACTION §4.0b).

    **The participant pairs entering the id are sorted, and the payload's order is untouched.**
    An id is derived from stable identity and structural position; the order a model happened
    to list two participants in is neither, and leaving it in made `event_id` a function of the
    answer's formatting — two answers naming the same employer and the same officer in the
    other order minted two different ids for one event *(found by review 2026-08-02)*.
    """
    return EventInstance(
        event_id=event_id(
            event.event_type_id,
            event.occurred_on,
            event.passage_id,
            tuple(sorted((p.role, p.entity_id) for p in event.participants)),
        ),
        event_type_id=event.event_type_id,
        occurred_on=event.occurred_on,
        announced_on=event.announced_on,
        participants=tuple(
            EventParticipantRef(role=p.role, entity_id=p.entity_id, entity_type=p.entity_type)
            for p in event.participants
        ),
        properties=dict(event.properties),
        evidence=(evidence,),
        assertion_type=event.assertion_type,
    )


def to_relationship(
    relationship: LaneRelationship, evidence: EvidenceReference
) -> RelationshipInstance:
    """A lane edge as the ontology's own payload.

    `RelationshipInstance` has no `properties` field — `valid_from` and `valid_to` are its
    only temporal slots and there is nowhere else to put an attribute — so anything a lane
    read about the edge beyond its endpoints belongs on the event, which is where the
    ontology declares properties.
    """
    return RelationshipInstance(
        relationship_id=relationship.relationship_id,
        source_id=relationship.source_id,
        source_type=relationship.source_type,
        target_id=relationship.target_id,
        target_type=relationship.target_type,
        valid_from=relationship.valid_from,
        valid_to=relationship.valid_to,
        evidence=(evidence,),
        assertion_type=relationship.assertion_type,
    )


def to_event_claim(event: LaneEvent, passage_row: dict | None = None) -> OntologyClaim:
    instance = to_event(event, build_evidence(event, passage_row))
    return OntologyClaim(
        claim_id=claim_id("event", instance.event_id),
        claim_kind="event",
        event=instance,
        assertion_type=event.assertion_type,
        extractor_metadata=dict(event.extractor_metadata),
    )


def to_relationship_claim(
    relationship: LaneRelationship, passage_row: dict | None = None
) -> OntologyClaim:
    instance = to_relationship(relationship, build_evidence(relationship, passage_row))
    return OntologyClaim(
        claim_id=claim_id(
            "relationship",
            relationship_instance_id(
                relationship.relationship_id, relationship.source_id,
                relationship.target_id, relationship.passage_id),
        ),
        claim_kind="relationship",
        relationship=instance,
        assertion_type=relationship.assertion_type,
        extractor_metadata=dict(relationship.extractor_metadata),
    )


def assemble_events(
    events: list[LaneEvent],
    relationships: list[LaneRelationship],
    *,
    passage_rows: dict[str, dict] | None = None,
) -> AssemblyResult:
    """Lane events and edges as ontology claims, in the order given.

    Takes no `ontology` because it needs none: nothing here is derived from the vocabulary,
    unlike `assemble`, whose deferral and population policies are. Adding the parameter for
    symmetry alone would suggest a policy is applied that is not.
    """
    rows = passage_rows or {}
    result = AssemblyResult()
    for event in events:
        result.claims.append(to_event_claim(event, rows.get(event.passage_id)))
    for relationship in relationships:
        result.claims.append(
            to_relationship_claim(relationship, rows.get(relationship.passage_id)))
    return result


def assemble(
    lane_claims: list[LaneClaim],
    *,
    ontology,
    passage_rows: dict[str, dict] | None = None,
) -> AssemblyResult:
    """Turn lane output into ontology claims, deferring what has no lane.

    This is where §7's policies apply, once, for every lane. §7.1 refuses a population metric
    with no filed denominator wording and §7.3 attaches the recognition-point ambiguity, both
    derived from the ontology; §7.5 is the absence below.

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
        failure = population_policy_failure(ontology, lane_claim)
        if failure:
            result.rejected.append(PolicyRejection(
                metric_id=lane_claim.metric_id,
                reason=MISSING_POPULATION_DEFINITION,
                detail=failure,
                passage_id=lane_claim.passage_id))
            continue
        result.claims.append(
            to_claim(lane_claim, rows.get(lane_claim.passage_id), ontology=ontology))
    return result
