"""Building the seven catalogs from persisted lane outputs.

Reads an `ExtractResult` — the parsed `lane_outputs.jsonl`, never a live lane — puts every lane
payload through `core.assembly`, and flattens the results into rows. The §7 policies stay in
`assemble`; nothing here decides whether a claim is allowed, only how an allowed one is written
down.

**The lane payload is not carried into the row alongside the ontology claim, and that is
deliberate.** Everything a row needs is already on the claim: `source_lane` on the observation,
`lane` in the event's `extractor_metadata`, and the passage and document on the evidence. Zipping
lane payloads back onto assembled claims would need an index that `assemble` legitimately does
not preserve — it drops deferred metrics and refuses population-less claims — and an index that
drifts is worse than a field read twice.
"""

from __future__ import annotations

from typing import Any

from ...core.assembly import assemble, assemble_events, deferred_metric_ids
from ...core.identifiers import digest, relationship_instance_id
from ..extract.public import (
    ANNOUNCEMENT_EQUALS_OCCURRENCE,
    EVENT_LANE,
    ROUTING_LANE_OF,
    ExtractResult,
)
from .public import (
    CLAIMS,
    EVENTS,
    EVIDENCE,
    ISSUES,
    OBSERVATIONS,
    REJECTED,
    RELATIONSHIPS,
    CatalogSet,
)


def _enum(value: Any) -> Any:
    """A pydantic enum as the string it is written as, or the value unchanged."""
    return getattr(value, "value", value)


def _ordinal_id(prefix: str, parts: tuple[str, ...], seen: dict[tuple[str, ...], int]) -> str:
    """`{prefix}:{digest12}` over a group's parts plus its occurrence number in that group.

    The occurrence number is structural position, not a counter minted to dodge a collision:
    one table passage legitimately produces the same issue code on two identical rows, and the
    two are different findings about different rows. Ordering is the order of
    `lane_outputs.jsonl`, which is deterministic, so the id is a function of the run and not of
    when it was built.
    """
    seen[parts] = seen.get(parts, 0) + 1
    return f"{prefix}:{digest(*parts, str(seen[parts]))}"


def build(result: ExtractResult, *, ontology, corpus) -> CatalogSet:
    """Every catalog, from persisted lane outputs and the corpus rows evidence is built on."""
    rows = {name: [] for name in
            (CLAIMS, OBSERVATIONS, EVENTS, RELATIONSHIPS, EVIDENCE, ISSUES, REJECTED)}
    passage_rows = {row["passage_id"]: row for row in corpus}

    metric_assembly = assemble(
        result.claims, ontology=ontology, passage_rows=passage_rows)
    event_assembly = assemble_events(
        result.events, result.relationships, passage_rows=passage_rows)
    claims = [*metric_assembly.claims, *event_assembly.claims]

    for claim in claims:
        document_type = _document_type(claim, passage_rows)
        rows[CLAIMS].append(_claim_row(claim, document_type))
        if claim.metric_observation is not None:
            rows[OBSERVATIONS].append(_observation_row(claim, document_type))
        if claim.event is not None:
            rows[EVENTS].append(_event_row(claim, document_type))
        if claim.relationship is not None:
            rows[RELATIONSHIPS].append(_relationship_row(claim, document_type))
        rows[EVIDENCE].extend(_evidence_rows(claim))

    seen_issue: dict[tuple[str, ...], int] = {}
    seen_rejection: dict[tuple[str, ...], int] = {}
    for issue in result.issues:
        parts = (issue.code, issue.lane, issue.passage_id, issue.row_label or "",
                 issue.detail)
        rows[ISSUES].append({
            "issue_id": _ordinal_id("issue", parts, seen_issue),
            **{key: value for key, value in issue.as_row().items()
               if key != "raw_finding"},
        })
        if issue.rejected_claim:
            rows[REJECTED].append({
                "rejection_id": _ordinal_id("rej", parts, seen_rejection),
                "refused_by": "lane",
                **issue.as_row(),
            })

    # `assemble`'s own refusals. A §7 policy rejection is a payload a lane produced and this
    # layer would not carry, which is exactly what `rejected_claims.jsonl` is for — and it
    # reaches no lane issue, so without this it would be invisible.
    for rejection in metric_assembly.rejected:
        parts = (rejection.reason, "assemble", rejection.passage_id, rejection.metric_id,
                 rejection.detail)
        row = passage_rows.get(rejection.passage_id) or {}
        rows[REJECTED].append({
            "rejection_id": _ordinal_id("rej", parts, seen_rejection),
            "refused_by": "assemble",
            "code": rejection.reason,
            "severity": "rejection",
            "lane": "assemble",
            "passage_id": rejection.passage_id,
            "document_id": str(row.get("document_id") or ""),
            "document_type": str(row.get("document_type") or ""),
            "concept_ids": [rejection.metric_id],
            "row_label": None,
            "quoted_span": None,
            "request_sha256": None,
            "rejected_claim": True,
            "detail": rejection.detail,
            "raw_finding": None,
        })

    return CatalogSet(
        rows=rows,
        claims=claims,
        deferred_metric_ids=tuple(sorted(deferred_metric_ids(ontology))),
    )


# -- one row per payload --------------------------------------------------------------------


def _document_type(claim, passage_rows: dict[str, dict]) -> str:
    for reference in claim.payload_evidence:
        row = passage_rows.get(reference.passage_id or "")
        if row:
            return str(row.get("document_type") or "")
    return ""


def _lane_of(claim) -> str:
    """The *routing* lane a claim came through, not the lane name the ontology reads.

    See `ROUTING_LANE_OF`. The lane's own name stays on the observation as `source_lane`,
    where the vocabulary's `forbidden_source_lanes` is checked against it; this is the key the
    coverage table joins candidates and claims on, and the two are different strings.
    """
    observation = claim.metric_observation
    name = (str(observation.source_lane) if observation is not None
            else str(claim.extractor_metadata.get("lane") or ""))
    return ROUTING_LANE_OF.get(name, name or EVENT_LANE)


def _payload_id(claim) -> str:
    if claim.metric_observation is not None:
        return claim.metric_observation.observation_id
    if claim.event is not None:
        return claim.event.event_id
    if claim.relationship is not None:
        # `RelationshipInstance` carries the predicate, not an instance id: `assemble` mints the
        # deterministic instance id, digests it into the claim id and keeps no copy. Rebuilt
        # from the same four inputs rather than stored, so the two cannot disagree.
        passage_id, _ = _anchor(claim)
        return relationship_instance_id(
            claim.relationship.relationship_id, claim.relationship.source_id,
            claim.relationship.target_id, passage_id)
    return claim.claim_id


def _anchor(claim) -> tuple[str, str]:
    for reference in claim.payload_evidence:
        return str(reference.passage_id or ""), str(reference.document_id or "")
    return "", ""


def _claim_row(claim, document_type: str) -> dict[str, Any]:
    passage_id, document_id = _anchor(claim)
    return {
        "claim_id": claim.claim_id,
        "claim_kind": _enum(claim.claim_kind),
        "payload_id": _payload_id(claim),
        "lane": _lane_of(claim),
        "passage_id": passage_id,
        "document_id": document_id,
        "document_type": document_type,
        "assertion_type": _enum(claim.assertion_type),
        "confidence": claim.confidence,
        "extractor_metadata": claim.extractor_metadata,
    }


def _observation_row(claim, document_type: str) -> dict[str, Any]:
    observation = claim.metric_observation
    passage_id, document_id = _anchor(claim)
    population = observation.population
    return {
        "observation_id": observation.observation_id,
        "claim_id": claim.claim_id,
        "metric_id": observation.metric_id,
        "subject_entity_id": observation.subject_entity_id,
        "subject_type": _enum(observation.subject_type),
        "value": observation.value,
        "unit": _enum(observation.unit),
        "currency": observation.currency,
        "period_start": observation.period_start,
        "period_end": observation.period_end,
        "instant_date": observation.instant_date,
        "population_definition_raw": None if population is None else population.definition_raw,
        "source_lane": _enum(observation.source_lane),
        "lane": _lane_of(claim),
        "assertion_type": _enum(observation.assertion_type),
        "confidence": observation.confidence,
        "passage_id": passage_id,
        "document_id": document_id,
        "document_type": document_type,
        "ambiguity_codes": list(claim.extractor_metadata.get("ambiguity_codes") or ()),
        "scale": claim.extractor_metadata.get("scale"),
        "scale_location": claim.extractor_metadata.get("scale_location"),
        "row_label": claim.extractor_metadata.get("row_label"),
        "column_label": claim.extractor_metadata.get("column_label"),
    }


def _event_row(claim, document_type: str) -> dict[str, Any]:
    """The event payload, with both dates' chosen phrases and the raw evidence beside them.

    STAGE_13 §4.1 requires the second half explicitly: the residual it records is a model
    answering two date questions with one printed phrase, and a row carrying only the resolved
    ISO dates could not show that. `occurrence_date_text` and `announcement_date_text` are the
    lane's own record of which phrase each field was read from, and `evidence_quoted_text` is
    the sentence both came out of.
    """
    event = claim.event
    passage_id, document_id = _anchor(claim)
    metadata = claim.extractor_metadata
    quoted = next((reference.quoted_text for reference in claim.payload_evidence), None)
    return {
        "event_id": event.event_id,
        "claim_id": claim.claim_id,
        "event_type_id": event.event_type_id,
        "occurred_on": event.occurred_on,
        "announced_on": event.announced_on,
        "occurrence_date_text": metadata.get("occurrence_date_text"),
        "announcement_date_text": metadata.get("announcement_date_text"),
        "dates_equal": bool(event.occurred_on and event.occurred_on == event.announced_on),
        "review_flag": (ANNOUNCEMENT_EQUALS_OCCURRENCE
                        if event.occurred_on and event.occurred_on == event.announced_on
                        else None),
        "participants": [
            {"role": participant.role, "entity_id": participant.entity_id,
             "entity_type": _enum(participant.entity_type)}
            for participant in event.participants
        ],
        "properties": dict(event.properties),
        "assertion_type": _enum(event.assertion_type),
        "passage_id": passage_id,
        "document_id": document_id,
        "document_type": document_type,
        "evidence_quoted_text": quoted,
        "lane": _lane_of(claim),
    }


def _relationship_row(claim, document_type: str) -> dict[str, Any]:
    relationship = claim.relationship
    passage_id, document_id = _anchor(claim)
    return {
        "relationship_instance_id": _payload_id(claim),
        "claim_id": claim.claim_id,
        "relationship_id": relationship.relationship_id,
        "source_id": relationship.source_id,
        "source_type": _enum(relationship.source_type),
        "target_id": relationship.target_id,
        "target_type": _enum(relationship.target_type),
        "valid_from": relationship.valid_from,
        "valid_to": relationship.valid_to,
        "assertion_type": _enum(relationship.assertion_type),
        "passage_id": passage_id,
        "document_id": document_id,
        "document_type": document_type,
        "lane": _lane_of(claim),
    }


def _evidence_rows(claim) -> list[dict[str, Any]]:
    return [
        {
            "claim_id": claim.claim_id,
            "claim_kind": _enum(claim.claim_kind),
            "evidence_index": index,
            "evidence_kind": _enum(reference.evidence_kind),
            "passage_id": reference.passage_id,
            "document_id": reference.document_id,
            "table_id": reference.table_id,
            "block_ids": list(reference.block_ids),
            "source_url": reference.source_url,
            "quoted_text": reference.quoted_text,
        }
        for index, reference in enumerate(claim.payload_evidence)
    ]
