"""Computing the run report's numbers from the run directory's own files.

One function, `build_report`, and a small pile of counters. It reads rows and returns data; it
formats nothing and it opens nothing that a finished run directory does not already hold —
which is what makes `report.md` regenerable from a directory alone, and byte-identically so.

The one input that is not a file is the ontology, and it supplies exactly one thing: which
metrics are in scope. Counting a metric that produced nothing requires knowing the metric
exists, and the only honest source for that is the vocabulary.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from ...core.assembly import DEFERRED_REASON, deferred_metric_ids
from ..extract.public import ANNOUNCEMENT_EQUALS_OCCURRENCE, NO_STORED_ANSWER
from ..tables.public import DEFERRED_REQUIRED_SOURCE_LANE
from .public import PASSAGE_LIST_CEILING, RunReport

STAGE_NAME = "report"

# Named here because the report says it in prose and a follow-up nobody wrote down is a
# follow-up nobody does. STAGE_13 §4.1 and §10 both ask for it by name.
TEMPORAL_FOLLOW_UP = (
    "A general announcement-versus-occurrence verifier. It needs a way to read that a "
    "sentence *reports* an announcement rather than states an occurrence, which is a reading "
    "rather than a constraint, so it is deferred rather than approximated here."
)


def _census(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def build_report(
    *,
    run_id: str,
    rows: dict[str, list[dict]],
    outcomes: Sequence,
    unselected: dict[str, int],
    ontology,
    corpus: dict[str, Any],
    verification: Sequence[dict],
    bounds: dict[str, Any],
    scoping: dict[str, Any],
) -> RunReport:
    claims = rows.get("claims.jsonl", [])
    observations = rows.get("observations.jsonl", [])
    events = rows.get("events.jsonl", [])
    relationships = rows.get("relationships.jsonl", [])
    evidence = rows.get("evidence.jsonl", [])
    issues = rows.get("issues.jsonl", [])
    rejected = rows.get("rejected_claims.jsonl", [])

    deferred = deferred_metric_ids(ontology)
    all_metrics = sorted(
        definition.concept_id
        for definition in ontology.registry.by_category("metric_definition"))
    in_scope = [metric for metric in all_metrics if metric not in deferred]
    in_scope_set = set(in_scope)

    report = RunReport(run_id=run_id)
    report.corpus = dict(corpus)
    report.ontology = {
        "ontology_id": str(ontology.metadata.ontology_id),
        "definition_hash": ontology.definition_hash,
        "metric_definitions": len(all_metrics),
        "in_scope_metrics": len(in_scope),
        "deferred_metrics": len(deferred),
    }
    report.counts = {
        "claims": len(claims),
        "observations": len(observations),
        "events": len(events),
        "relationships": len(relationships),
        "evidence_references": len(evidence),
        "issues": len(issues),
        "rejected_claims": len(rejected),
        "candidates": len(outcomes),
        "candidates_with_a_claim": sum(
            1 for outcome in outcomes if not outcome.produced_nothing),
        "candidates_with_no_claim": sum(
            1 for outcome in outcomes if outcome.produced_nothing),
    }
    report.bounds = dict(bounds)
    report.headline = {
        "run_id": run_id,
        "ontology_definition_hash": ontology.definition_hash,
        "corpus_id": corpus.get("corpus_id", ""),
        "verification_passed": all(bool(check["passed"]) for check in verification),
    }

    # -- coverage ---------------------------------------------------------------------------
    lanes_seen = sorted({str(row.get("source_lane") or "") for row in observations})
    per_metric: dict[str, dict[str, int]] = {
        metric: {"total": 0, **{lane: 0 for lane in lanes_seen}} for metric in in_scope}
    for row in observations:
        metric = str(row.get("metric_id") or "")
        bucket = per_metric.setdefault(
            metric, {"total": 0, **{lane: 0 for lane in lanes_seen}})
        bucket["total"] += 1
        bucket[str(row.get("source_lane") or "")] = (
            bucket.get(str(row.get("source_lane") or ""), 0) + 1)
    report.metric_coverage = [
        {"metric_id": metric, "in_scope": metric in in_scope_set,
         "observations": per_metric[metric]["total"],
         "by_lane": {lane: per_metric[metric].get(lane, 0) for lane in lanes_seen},
         "documents": len({row["document_id"] for row in observations
                           if row.get("metric_id") == metric})}
        for metric in sorted(per_metric)
    ]
    report.deferred_metrics = [
        {"metric_id": metric, "status": "deferred", "reason": DEFERRED_REASON,
         "required_lane": "xbrl",
         "observations": per_metric.get(metric, {}).get("total", 0)}
        for metric in sorted(deferred)
    ]
    # A deferred metric that no passage ever named would leave this at zero, which is a
    # different statement from "the deferral works" — so it is counted rather than assumed.
    report.ontology["deferred_refusals_recorded"] = sum(
        1 for row in issues if row.get("code") == DEFERRED_REQUIRED_SOURCE_LANE)

    # -- every candidate that produced no claim, grouped by reason --------------------------
    # A code's severity is not a function of the code. `AMBIGUOUS_ALIAS` is a refusal when the
    # table lane declines a row and a rejection when the narrative lane refuses a proposed
    # claim, and a table that picked whichever row it saw last would print one of the two and
    # look definitive *(found in the first full run, 2026-08-02)*.
    severities_of: dict[str, set[str]] = {}
    for row in issues:
        severities_of.setdefault(str(row.get("code")), set()).add(str(row.get("severity")))
    no_claim: dict[str, set[str]] = {}
    no_claim_lanes: dict[str, set[str]] = {}
    uncoded = 0
    for outcome in outcomes:
        if not outcome.produced_nothing:
            continue
        if not outcome.issue_codes:
            uncoded += 1
            continue
        for code in outcome.issue_codes:
            no_claim.setdefault(code, set()).add(f"{outcome.lane}\x1f{outcome.passage_id}")
            no_claim_lanes.setdefault(code, set()).add(outcome.lane)
    report.no_claim_by_code = [
        {"code": code,
         "severities": sorted(severities_of.get(code, set())),
         "candidates": len(no_claim[code]),
         "lanes": sorted(no_claim_lanes[code]),
         "occurrences": sum(1 for row in issues if row.get("code") == code)}
        for code in sorted(no_claim)
    ]
    report.no_claim_passages = {
        code: sorted(entry.split("\x1f", 1)[1] for entry in no_claim[code])
        for code in sorted(no_claim)
        if len(no_claim[code]) <= PASSAGE_LIST_CEILING
    }
    report.counts["candidates_with_no_claim_and_no_code"] = uncoded

    # -- counts by lane, document type and claim kind ---------------------------------------
    lanes = sorted({outcome.lane for outcome in outcomes}
                   | {str(row.get("lane") or "") for row in claims})
    report.by_lane = [
        {"lane": lane,
         "candidates": sum(1 for outcome in outcomes if outcome.lane == lane),
         "candidates_with_a_claim": sum(
             1 for outcome in outcomes
             if outcome.lane == lane and not outcome.produced_nothing),
         "claims": sum(1 for row in claims if row.get("lane") == lane),
         "observations": sum(1 for row in observations if row.get("lane") == lane),
         "events": sum(1 for row in events if row.get("lane") == lane),
         "relationships": sum(1 for row in relationships if row.get("lane") == lane),
         "issues": sum(1 for row in issues if row.get("lane") == lane),
         "requests_issued": sum(
             1 for outcome in outcomes if outcome.lane == lane and outcome.request_issued),
         "requests_without_a_stored_answer": sum(
             1 for outcome in outcomes if outcome.lane == lane
             and NO_STORED_ANSWER in outcome.issue_codes)}
        for lane in lanes
    ]
    document_types = sorted(
        {outcome.document_type for outcome in outcomes}
        | {str(row.get("document_type") or "") for row in claims})
    report.by_document_type = [
        {"document_type": document_type,
         "candidates": sum(1 for outcome in outcomes
                           if outcome.document_type == document_type),
         "claims": sum(1 for row in claims if row.get("document_type") == document_type),
         "observations": sum(1 for row in observations
                             if row.get("document_type") == document_type),
         "events": sum(1 for row in events if row.get("document_type") == document_type),
         "relationships": sum(1 for row in relationships
                              if row.get("document_type") == document_type),
         "issues": sum(1 for row in issues if row.get("document_type") == document_type)}
        for document_type in document_types
    ]
    report.by_claim_kind = [
        {"claim_kind": kind, "claims": count}
        for kind, count in _census(str(row.get("claim_kind")) for row in claims).items()
    ]
    report.unselected = [
        {"lane": key.split(":", 1)[0], "reason": key.split(":", 1)[1], "passages": count}
        for key, count in sorted(unselected.items())
    ]

    # -- verification, the temporal residual, and refusals ----------------------------------
    report.verification = [dict(check) for check in verification]
    report.temporal_residual = [
        {"event_id": row["event_id"],
         "event_type_id": row["event_type_id"],
         "occurred_on": row["occurred_on"],
         "announced_on": row["announced_on"],
         "occurrence_date_text": row.get("occurrence_date_text"),
         "announcement_date_text": row.get("announcement_date_text"),
         "passage_id": row["passage_id"],
         "evidence_quoted_text": row.get("evidence_quoted_text")}
        for row in events if row.get("dates_equal")
    ]
    report.counts["events_flagged_for_temporal_review"] = len(report.temporal_residual)
    report.counts["temporal_diagnostics_recorded"] = sum(
        1 for row in issues if row.get("code") == ANNOUNCEMENT_EQUALS_OCCURRENCE)
    report.rejected = [
        {"refused_by": key.split("\x1f")[0], "code": key.split("\x1f")[1],
         "rejections": count}
        for key, count in _census(
            f"{row.get('refused_by')}\x1f{row.get('code')}" for row in rejected).items()
    ]

    report.scoping = dict(scoping)
    scope_sizes = [outcome.scope_size for outcome in outcomes
                   if outcome.scope_size is not None]
    report.scoping["scoped_candidates"] = len(scope_sizes)
    report.scoping["scope_size_mean"] = (
        round(sum(scope_sizes) / len(scope_sizes), 3) if scope_sizes else 0.0)
    report.scoping["scope_size_min"] = min(scope_sizes) if scope_sizes else 0
    report.scoping["scope_size_max"] = max(scope_sizes) if scope_sizes else 0
    report.scoping["requests_issued"] = sum(
        1 for outcome in outcomes if outcome.request_issued)
    report.scoping["requests_without_a_stored_answer"] = sum(
        1 for outcome in outcomes if NO_STORED_ANSWER in outcome.issue_codes)

    report.follow_up = [TEMPORAL_FOLLOW_UP]
    return report
